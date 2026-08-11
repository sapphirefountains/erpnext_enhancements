"""AI write-confirmation gate for Frappe Assistant Core (FAC).

Wraps ``BaseTool._safe_execute`` — the single choke point both FAC execution
paths converge on (the legacy JSON-RPC handler via ``ToolRegistry.execute_tool``
and the StreamableHTTP endpoint via ``mcp/tool_adapter``) — so that, when
``ai_write_gating_enabled`` is on, AI-proposed mutations are *not* executed:
an **AI Pending Action** is recorded instead and the model receives an
anti-fabrication envelope telling it the action has NOT run and how the human
confirms it in the desk (``gating_api.confirm_action``). Read tools pass
through untouched; gating off means byte-identical behaviour.

Why patch here and not ``execute_tool``: ``api/fac_endpoint`` calls
``_import_tools()`` (which imports this package) on every MCP request *before*
dispatch, so a class-level wrap applied from ``assistant_tools/__init__`` is
in place before any tool executes in a fresh worker — and ``tool_adapter``
bypasses ``execute_tool`` entirely.

Deliberately desk-only confirmation: there is NO MCP-exposed confirm tool — a
model-callable confirm would collapse the human-in-the-loop guarantee to a
prompt-injection-resistant-as-tissue-paper convention. The model retrieves the
real result afterwards via the read-only ``check_ai_pending_action`` tool.

Underscore-prefixed module: FAC's loader only imports the dotted paths listed
in the ``assistant_tools`` hook, and the schema tests' stub environment must
be able to import this package — ``apply_gate()`` no-ops when the (stub)
BaseTool has no ``_safe_execute``.
"""

import functools
import hashlib
import json
import re

import frappe

GATE_MARKER = "_ee_ai_gate"
RESULT_MAX_BYTES = 50_000

# FAC built-ins that mutate. Belt-and-braces explicit set — category lookup
# (FAC Tool Configuration / detector) is the primary classifier, but these
# must gate even if configuration rows are missing.
EXPLICIT_MUTATING = {
    "create_document",
    "update_document",
    "delete_document",
    "submit_document",
    "run_workflow",
    "run_python_code",
    "create_dashboard",
    "create_dashboard_chart",
}

# Privileged-but-read-only: FAC enforces read-only SQL for run_database_query
# (utils/read_only_db.py), so confirmation would be pure friction.
EXPLICIT_READONLY = {
    "run_database_query",
    # this app's read-only tools (the write tools live in APP_MUTATING below)
    "maintenance_day_board",
    "maintenance_contract_status",
    "maintenance_visit_history",
    "maintenance_site_briefing",
    "project_status_overview",
    "project_procurement_status",
    "workforce_time_status",
    "check_ai_pending_action",
    "stripe_payment_status",
    "quickbooks_sync_status",
    "document_intake_queue",
    "closed_won_handoff_status",
    # water_engineering: stateless calculator + design/panel readers (no DB writes)
    "water_calc",
    "water_design_status",
    "control_panel_status",
    # training: both read-only. Omitting them here was not merely a missing
    # annotation -- is_mutating() would fall past every branch to the fail-closed
    # default, so with ai_write_gating_enabled on, two reads returned a
    # confirmation card instead of an answer. test_every_registered_tool_is_
    # classified now makes that unrepeatable.
    "training_compliance_status",
    "training_learner_record",
    # A1 (v1.259.0): four more reads. Each wraps an existing function and adds no
    # business logic of its own; the redactions they apply are in their modules.
    # kpi_dashboard_status deliberately does NOT expose refresh_kpi_dashboard,
    # which rebuilds a snapshot and commits — that one is a write and belongs
    # nowhere near this set.
    "contract_signing_status",
    "kpi_dashboard_status",
    "project_pickup_route",
    "training_course_catalog",
}

# This app's own *write* tools (assistant_tools/<name>.py). They must gate even
# though FAC's category detector has never seen them — listed here so the gate
# never depends on the fail-closed fallback to confirm them.
APP_MUTATING = {
    "create_followup_task",
    # mdm_integration remote device actions — all gated (lock/wipe/locate the
    # mobile fleet via Miradore; reboot/run-script/deploy patches via Action1).
    "remote_lock_device",
    "remote_wipe_device",
    "locate_device",
    "reboot_device",
    "run_device_script",
    "deploy_device_patch",
    # water_engineering: create/update a Water Feature Design (plain doc write)
    "save_water_design",
}

HIGH_RISK = {
    "delete_document",
    "submit_document",
    "run_workflow",
    "run_python_code",
    # irreversible / arbitrary-remote-code device actions: a wipe is irreversible,
    # a remote lock can lock a user out of their device, and run_device_script is
    # arbitrary remote code (as dangerous as run_python_code).
    "remote_wipe_device",
    "remote_lock_device",
    "run_device_script",
}
LOW_RISK = {
    "create_document",
    "create_dashboard",
    "create_dashboard_chart",
    "create_followup_task",
    "save_water_design",
}

# Only plain-document create/update may use the settings exempt-doctype
# allowlist; privileged/irreversible tools never skip confirmation.
EXEMPTABLE_TOOLS = {"create_document", "update_document"}

# ------------------------------------------------------------ chat denylist
#
# Employee chat content is unreachable through the generic FAC tools, and the
# refusal lives HERE rather than in the permission stack because one of those
# tools sits underneath the permission stack entirely.
#
# `run_database_query` states its own security model as "Restricted to SELECT
# statements only. Requires System Manager role for security." — a role check
# and a read-only-SQL check, and nothing else. Raw SQL never consults DocPerm,
# `permission_query_conditions` or `has_permission`, so the chat DocTypes'
# zero-DocPerm posture closes `get_document` and `list_documents` and does
# nothing at all to this one. A System Manager is otherwise one
# `select text, sender from `tabChat Message`` away from every private message
# on the site, delivered into a model's context window.
#
# Nor is "no DocPerm" the same as "unreachable": frappe/permissions.py
# short-circuits `if user == "Administrator"` and allows everything, so the
# desk, /api/resource and `get_document` all still read a zero-DocPerm DocType
# as Administrator.
#
# The seam is `_gated_execute`, which already sees every FAC tool call
# including FAC's own built-ins. The branch is the FIRST thing that function
# does — above the confirm-flow bypass and above the settings check — because
# `ai_write_gating_enabled` ships dormant, and a denylist that can be switched
# off from a settings form is not an invariant. Chat retrieval has exactly one
# supported door and it is named in the refusal message.
#
# Set equality against the filesystem is asserted by
# tests/test_chat_mcp_denylist.py, so a chat DocType added later fails the
# build by default instead of quietly escaping the denylist. That is the same
# failure mode `test_every_registered_tool_is_classified` exists to prevent.
CHAT_DENYLIST_DOCTYPES = frozenset(
    {
        "Chat Allowed User",
        "Chat Attachment",
        "Chat Event Subscription",
        "Chat Inbound Event",
        "Chat Mention",
        "Chat Message",
        "Chat Message Revision",
        "Chat Provisioning Run",
        "Chat Push Subscription",
        "Chat Relay Job",
        "Chat Retrieval Audit",
        "Chat Retrieval Audit Room",
        "Chat Room",
        "Chat Room Member",
        "Chat Settings",
    }
)

# The one supported door, named in every refusal so the model redirects itself
# instead of trying a different spelling of the same query.
CHAT_RETRIEVAL_ENDPOINT = "erpnext_enhancements.chat.retrieval.gate.retrieve"

# Arguments whose *text* is searched for a chat table name, per tool. A SQL
# string and a Python program are both free text: there is no `doctype`
# argument to compare, so the table name has to be matched inside the payload.
CHAT_TEXT_ARGUMENTS = {
    "run_database_query": ("query", "sql"),
    "run_python_code": ("code",),
}

_SQL_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_SQL_COMMENT_LINE = re.compile(r"(--|#)[^\n]*")
_NON_WORD = re.compile(r"[^a-z0-9_]+")


# ------------------------------------------------------------- pure helpers
# (unit-tested bench-free in tests/test_ai_gate_unit.py)


def _normalise_for_denylist(text):
    """Case-fold, strip comments, and drop everything that is not a word
    character, so a table name is one contiguous needle.

    ``select text from `tabChat Message` `` and
    ``SELECT/*x*/ text FROM tab_Chat__Message`` both reduce to a string
    containing ``tabchatmessage``. Backticks, quoting, whitespace, casing, SQL
    comments and `information_schema` filters all stop mattering, because none
    of them survive the reduction.

    The rule this serves is coarse and absolute on purpose: **do not attempt to
    allow "safe" queries.** String-matching SQL loses to every one of the
    tricks above if it tries to parse; it wins only if it refuses on contact.
    Over-refusal costs an analyst one rephrase. Under-refusal costs the
    invariant, silently, with a correct answer delivered to the wrong reader.
    """
    if not isinstance(text, str):
        return ""
    lowered = text.lower()
    lowered = _SQL_COMMENT_BLOCK.sub(" ", lowered)
    lowered = _SQL_COMMENT_LINE.sub(" ", lowered)
    return _NON_WORD.sub("", lowered)


def _denylist_needles():
    """``{"chatmessage": "Chat Message", …}`` — the normalised needle for every
    denylisted DocType, longest first so a refusal names the most specific
    table it matched rather than its prefix (``Chat Room Member`` before
    ``Chat Room``)."""
    pairs = ((_normalise_for_denylist(dt), dt) for dt in CHAT_DENYLIST_DOCTYPES)
    return sorted((needle, dt) for needle, dt in pairs if needle)


def chat_denylist_hit(tool_name, arguments):
    """The denylisted DocType this call would reach, or ``None``.

    Two shapes, because the tools come in two shapes. ``get_document`` /
    ``list_documents`` / ``search_documents`` and friends take a literal
    ``doctype`` argument, so that half is a string comparison rather than a
    parse. ``run_database_query`` and ``run_python_code`` take free text, so
    that half is the contact match described on
    :func:`_normalise_for_denylist`.

    The ``doctype`` check is applied to **every** tool rather than to a named
    list: a tool added to FAC tomorrow that takes a ``doctype`` is covered the
    day it appears, which is the opposite of how an allowlist of tool names
    would age.
    """
    args = arguments or {}
    if not isinstance(args, dict):
        return None

    target = args.get("doctype")
    if isinstance(target, str) and target.strip() in CHAT_DENYLIST_DOCTYPES:
        return target.strip()

    needles = _denylist_needles()
    for key in CHAT_TEXT_ARGUMENTS.get(tool_name, ()):
        haystack = _normalise_for_denylist(args.get(key))
        if not haystack:
            continue
        for needle, doctype in reversed(needles):
            if needle in haystack:
                return doctype
    return None


def _chat_refusal_message(doctype):
    return (
        f"Refused: {doctype} is employee-private chat data and is not readable "
        "through the generic Frappe tools, by any role, with AI gating on or "
        f"off. Chat context has exactly one door — {CHAT_RETRIEVAL_ENDPOINT} — "
        "which runs as the asking person, derives the rooms they are actually "
        "in rather than accepting a room list, and records the read. Ask "
        "through that, or ask the person."
    )


def classify_risk(tool_name, category=None):
    """High = irreversible / arbitrary-code; Medium = update or unknown
    mutating (fail-safe default, same as Triton); Low = plain creates."""
    if tool_name in HIGH_RISK or category == "privileged":
        return "High"
    if tool_name in LOW_RISK:
        return "Low"
    return "Medium"


def annotations_for(tool_name):
    """MCP ToolAnnotations (+ ``x-ee-*`` risk band) for a tool, derived from the
    classification sets above so this gate stays the single source of truth.

    FAC forwards a tool's ``annotations`` verbatim in its ``tools/list`` response
    (it reads ``getattr(tool, "annotations", None)``), so an MCP client such as
    Triton can read a tool's mutation/risk from here instead of guessing from the
    verb — which is how Triton was mis-classifying the oddly-named device tools
    (``remote_wipe_device`` / ``run_device_script`` / …) as read-only and skipping
    its confirmation step. Mutating tools advertise ``readOnlyHint: False`` +
    ``destructiveHint`` + an ``x-ee-risk`` band; explicit read tools advertise
    ``readOnlyHint: True``; unknown tools get ``{}`` (the client decides).

    Pure / bench-free (no frappe calls), so it is safe to call from a tool's
    ``__init__`` and from the schema tests' stub environment."""
    if tool_name in EXPLICIT_MUTATING or tool_name in APP_MUTATING:
        return {
            "readOnlyHint": False,
            "destructiveHint": tool_name in HIGH_RISK,
            "idempotentHint": False,
            "x-ee-mutation": True,
            "x-ee-risk": classify_risk(tool_name).lower(),
        }
    if tool_name in EXPLICIT_READONLY:
        return {"readOnlyHint": True, "x-ee-mutation": False}
    return {}


def summarize_tool_call(tool_name, arguments):
    """One human line for the desk card (ported from Triton's templates)."""
    args = arguments or {}
    doctype = args.get("doctype") or ""
    name = args.get("name") or ""
    if tool_name == "create_document":
        return f"Create {doctype}".strip()
    if tool_name == "update_document":
        return f"Update {doctype} {name}".strip()
    if tool_name == "delete_document":
        return f"Delete {doctype} {name}".strip()
    if tool_name == "submit_document":
        return f"Submit {doctype} {name}".strip()
    if tool_name == "run_workflow":
        action = args.get("action") or args.get("workflow_action") or "transition"
        return f"Workflow '{action}' on {doctype} {name}".strip()
    if tool_name == "run_python_code":
        return "Run arbitrary Python code on the server"
    if tool_name == "create_dashboard":
        return "Create a dashboard"
    if tool_name == "create_dashboard_chart":
        return "Create a dashboard chart"
    if tool_name == "create_followup_task":
        text = (args.get("description") or "").strip()
        snippet = (text[:60] + "…") if len(text) > 60 else text
        on = ""
        if args.get("reference_doctype") and args.get("reference_name"):
            on = f" on {args['reference_doctype']} {args['reference_name']}"
        return (f"Create follow-up task “{snippet}”{on}").strip()
    if tool_name == "remote_lock_device":
        return f"Remote LOCK device {args.get('device') or ''}".strip()
    if tool_name == "remote_wipe_device":
        return f"Remote WIPE ({args.get('mode') or 'selective'}) device {args.get('device') or ''}".strip()
    if tool_name == "locate_device":
        return f"Locate device {args.get('device') or ''}".strip()
    if tool_name == "reboot_device":
        return f"Reboot device {args.get('device') or ''}".strip()
    if tool_name == "run_device_script":
        return f"Run a script on device {args.get('device') or ''}".strip()
    if tool_name == "deploy_device_patch":
        return f"Deploy patch {args.get('patch') or ''} to device {args.get('device') or ''}".strip()
    if tool_name == "save_water_design":
        return f"Save Water Feature Design {args.get('design') or '(new)'}".strip()
    return tool_name.replace("_", " ").capitalize()


def build_envelope(action_name, summary, risk, expires_at):
    """The anti-fabrication payload the model receives instead of a result.

    The explicit "NOT executed / no output exists / do not fabricate" language
    is load-bearing: without it models invent results and chain dependent
    actions (proven in Triton's tool loop)."""
    return {
        "status": "awaiting_user_confirmation",
        "executed": False,
        "output": None,
        "action_id": action_name,
        "summary": summary,
        "risk": (risk or "").lower(),
        "expires_at": str(expires_at or ""),
        "message": (
            "This action requires human confirmation and has NOT been executed. "
            "No output exists. Do NOT fabricate or describe its result, and do "
            "NOT take any step that depends on it. Ask the user to open "
            f"'AI Pending Action' {action_name} in ERPNext (a desk notification "
            "was sent) and click 'Confirm & Execute'. After they confirm, call "
            "the check_ai_pending_action tool with this action_id to retrieve "
            "the real result."
        ),
    }


def args_fingerprint(user, tool_name, arguments):
    canonical = json.dumps(arguments or {}, sort_keys=True, default=str)
    return hashlib.sha1(f"{user}|{tool_name}|{canonical}".encode()).hexdigest()


def sanitize_arguments(arguments):
    """Redact credential-like keys (FAC's heuristic when importable)."""
    try:
        from frappe_assistant_core.core.base_tool import _is_sensitive_key
    except Exception:

        def _is_sensitive_key(key):
            return isinstance(key, str) and any(
                fragment in key.lower()
                for fragment in ("password", "secret", "api_key", "token", "credential")
            )

    def scrub(value):
        if isinstance(value, dict):
            return {
                k: "***REDACTED***" if _is_sensitive_key(k) else scrub(v)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    return scrub(arguments or {})


def truncate_json(value):
    try:
        text = json.dumps(value, default=str, indent=1)
    except Exception:
        text = str(value)
    if len(text) > RESULT_MAX_BYTES:
        text = text[:RESULT_MAX_BYTES] + "\n... [truncated]"
    return text


# ------------------------------------------------------------ classification


def _gating_enabled():
    from erpnext_enhancements.feature_flags import ai_write_gating_enabled

    return ai_write_gating_enabled()


def _tool_category(tool):
    name = getattr(tool, "name", "")
    try:
        from frappe_assistant_core.core.tool_registry import get_tool_registry

        config = get_tool_registry()._get_tool_configurations().get(name) or {}
        if config.get("tool_category"):
            return config["tool_category"]
    except Exception:
        pass
    try:
        from frappe_assistant_core.utils.tool_category_detector import detect_tool_category

        return detect_tool_category(tool)
    except Exception:
        return None


def is_mutating(tool):
    name = getattr(tool, "name", "")
    if name in EXPLICIT_MUTATING or name in APP_MUTATING:
        return True
    if name in EXPLICIT_READONLY:
        return False
    category = _tool_category(tool)
    if category in ("write", "privileged"):
        return True
    if category == "read_only":
        return False
    # Unknown category on an unknown tool: fail closed — a wrongly gated read
    # is friction, a wrongly executed write is damage.
    return True


def _exempt_doctypes():
    try:
        settings = frappe.get_cached_doc("ERPNext Enhancements Settings")
        return {row.document_type for row in settings.get("ai_exempt_doctypes") or []}
    except Exception:
        return set()


# ------------------------------------------------------------------ logging


def insert_action_log(
    *,
    user,
    tool_name,
    arguments,
    success,
    risk=None,
    summary=None,
    result=None,
    error=None,
    error_type=None,
    pending_action=None,
    auto_approved=0,
    execution_time=None,
):
    """Append one AI Action Log row (ignore_permissions; never raises to the
    caller — a logging failure must not break the execution it records)."""
    try:
        args = sanitize_arguments(arguments)
        log = frappe.get_doc(
            {
                "doctype": "AI Action Log",
                "user": user,
                "tool_name": tool_name,
                "integration": "frappe_assistant_core",
                "pending_action": pending_action,
                "auto_approved": 1 if auto_approved else 0,
                "risk": risk or classify_risk(tool_name),
                "summary": summary or summarize_tool_call(tool_name, args),
                "arguments": json.dumps(args, default=str, indent=1),
                "result": truncate_json(result) if result is not None else None,
                "success": 1 if success else 0,
                "error": str(error)[:2000] if error else None,
                "error_type": error_type,
                "target_doctype": (arguments or {}).get("doctype"),
                "target_name": (arguments or {}).get("name"),
                "timestamp": frappe.utils.now_datetime(),
                "execution_time": execution_time,
            }
        )
        log.insert(ignore_permissions=True)
        if pending_action:
            frappe.db.set_value(
                "AI Pending Action", pending_action, "action_log", log.name,
                update_modified=False,
            )
        return log.name
    except Exception:
        try:
            frappe.log_error(
                f"AI Action Log insert failed for {tool_name}\n{frappe.get_traceback()}",
                "AI Governance",
            )
        except Exception:
            pass
        return None


# ----------------------------------------------------------------- proposal


def _propose(tool, arguments):
    user = frappe.session.user
    name = getattr(tool, "name", "")
    fingerprint = args_fingerprint(user, name, arguments)

    # Models retry: an identical pending proposal gets its envelope back
    # instead of a duplicate card.
    existing = frappe.db.get_value(
        "AI Pending Action",
        {
            "args_hash": fingerprint,
            "status": "Pending",
            "expires_at": (">", frappe.utils.now_datetime()),
        },
        ["name", "summary", "risk", "expires_at"],
        as_dict=True,
    )
    if existing:
        return _success_response(
            build_envelope(existing.name, existing.summary, existing.risk, existing.expires_at)
        )

    category = _tool_category(tool)
    risk = classify_risk(name, category)
    summary = summarize_tool_call(name, arguments)
    ttl_hours = frappe.utils.cint(
        frappe.db.get_single_value("ERPNext Enhancements Settings", "ai_pending_action_ttl_hours")
    ) or 1
    expires_at = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=ttl_hours)

    action = frappe.get_doc(
        {
            "doctype": "AI Pending Action",
            "tool_name": name,
            "integration": "frappe_assistant_core",
            "summary": summary,
            "risk": risk,
            "status": "Pending",
            "requested_by": user,
            "client_id": getattr(frappe.local, "assistant_client_id", None),
            "session_id": getattr(frappe.local, "assistant_session_id", None),
            "arguments": json.dumps(sanitize_arguments(arguments), default=str, indent=1),
            "args_hash": fingerprint,
            "target_doctype": (arguments or {}).get("doctype"),
            "target_name": (arguments or {}).get("name"),
            "expires_at": expires_at,
        }
    )
    action.insert(ignore_permissions=True)
    # The proposal must survive whatever happens later in this MCP request.
    frappe.db.commit()

    _notify_requester(action)
    return _success_response(build_envelope(action.name, summary, risk, expires_at))


def _notify_requester(action):
    try:
        frappe.get_doc(
            {
                "doctype": "Notification Log",
                "subject": f"AI action awaiting your confirmation: {action.summary}",
                "document_type": "AI Pending Action",
                "document_name": action.name,
                "for_user": action.requested_by,
                "type": "Alert",
            }
        ).insert(ignore_permissions=True)
        frappe.publish_realtime(
            "ai_pending_action", {"name": action.name, "summary": action.summary},
            user=action.requested_by,
        )
    except Exception:
        pass


def _success_response(envelope):
    # _safe_execute's response shape; result is a JSON string so the JSON-RPC
    # handler's str() coercion emits clean JSON rather than a Python repr.
    return {
        "success": True,
        "result": json.dumps(envelope, indent=2),
        "execution_time": 0.0,
    }


def _error_response(message):
    return {
        "success": False,
        "error": message,
        "error_type": "AIGateError",
        "execution_time": 0.0,
    }


# --------------------------------------------------------------- the wrapper


def _gated_execute(tool, original, arguments):
    # 0) Chat data is refused outright, and this branch is first for a reason.
    #    Everything below it can be switched off — the confirm-flow bypass by a
    #    flag, the rest by `ai_write_gating_enabled`, which ships dormant. A
    #    refusal reachable only while a settings checkbox is ticked is not an
    #    invariant, and this one is: no role, no flag and no confirmation makes
    #    a coworker's private message readable through a generic tool.
    denied = chat_denylist_hit(getattr(tool, "name", ""), arguments)
    if denied:
        message = _chat_refusal_message(denied)
        # Evidence, not silence. An attempt to read chat through a generic tool
        # is exactly the event an operator wants to find later, and AI Action
        # Log is already append-only and already purged on a schedule.
        insert_action_log(
            user=getattr(getattr(frappe, "session", None), "user", None),
            tool_name=getattr(tool, "name", ""),
            arguments=arguments,
            success=False,
            risk="High",
            summary=f"Refused a generic-tool read of chat data ({denied}).",
            error=message,
            error_type="AIGateError",
        )
        return _error_response(message)

    # 1) Confirm-flow re-execution: run for real (gating_api logs the outcome).
    if getattr(frappe.flags, "ai_gate_bypass", False):
        return original(tool, arguments)

    # 2) Gating off → byte-identical behaviour.
    try:
        enabled = _gating_enabled()
    except Exception:
        # Settings unreadable: fail closed for mutations, open for reads.
        if is_mutating(tool):
            return _error_response(
                "AI write gating could not evaluate its settings; the mutation was blocked."
            )
        return original(tool, arguments)
    if not enabled:
        return original(tool, arguments)

    # 3) Reads pass through untouched.
    if not is_mutating(tool):
        return original(tool, arguments)

    try:
        # 4) Allowlisted plain create/update: execute, but log with provenance.
        name = getattr(tool, "name", "")
        if name in EXEMPTABLE_TOOLS and (arguments or {}).get("doctype") in _exempt_doctypes():
            response = original(tool, arguments)
            insert_action_log(
                user=frappe.session.user,
                tool_name=name,
                arguments=arguments,
                success=bool(response.get("success")),
                result=response.get("result"),
                error=response.get("error"),
                error_type=response.get("error_type"),
                auto_approved=1,
                execution_time=response.get("execution_time"),
            )
            return response

        # 5/6) Propose + envelope.
        return _propose(tool, arguments)
    except Exception:
        # Fail closed: any gate failure on a mutating tool blocks execution.
        try:
            frappe.log_error(
                f"AI gate failure for {getattr(tool, 'name', '?')}\n{frappe.get_traceback()}",
                "AI Governance",
            )
        except Exception:
            pass
        return _error_response(
            "The AI write gate hit an internal error; the mutation was blocked. "
            "An administrator can find details in the Error Log."
        )


def apply_gate():
    """Wrap ``BaseTool._safe_execute`` once per process. Idempotent and
    self-guarding (model: ``monkeypatches.apply``): inert when FAC is absent
    or stubbed (no ``_safe_execute`` attribute — the bench-free test stubs),
    loud in the Error Log when a real FAC build renamed the seam."""
    try:
        from frappe_assistant_core.core.base_tool import BaseTool
    except Exception:
        return  # FAC not installed — hook strings are inert, so is the gate.

    original = getattr(BaseTool, "_safe_execute", None)
    if original is None:
        # Either the schema-test stub (fine) or a FAC upgrade renamed the
        # seam (NOT fine — writes would silently un-gate). Log loudly when a
        # real frappe is around to log to.
        try:
            frappe.log_error(
                "BaseTool._safe_execute is missing — the AI write gate could not "
                "attach. If frappe_assistant_core was upgraded, re-point the gate "
                "(erpnext_enhancements/assistant_tools/_gate.py).",
                "AI Governance",
            )
        except Exception:
            pass
        return

    if getattr(original, GATE_MARKER, False):
        return  # already applied in this process

    @functools.wraps(original)
    def gated_safe_execute(self, arguments):
        return _gated_execute(self, original, arguments)

    setattr(gated_safe_execute, GATE_MARKER, True)
    BaseTool._safe_execute = gated_safe_execute
