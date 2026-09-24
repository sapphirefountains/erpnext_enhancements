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

Re-verified against FAC 3.0.0 (written against 2.4.3): ``_safe_execute`` is unchanged, and
the new FAC Chat does not add a third execution path. Its cloud runtime is registered as a
per-user OAuth client of this site's own ``handle_mcp`` endpoint (``chat/api/auth.py``), so a
tool call from the Desk widget or ``/copilot`` arrives exactly like one from Claude or Triton
and passes through this wrapper. FAC Chat's own per-tool Ask/Allow/Block approvals sit in
FAC's cloud, in front of this gate, not instead of it.

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
import hmac
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
    # FAC 3.0.0's `faco` plugin (disabled on prod until someone ticks it in FAC's plugin
    # settings). FAC's own detector already calls both `write`, so the category branch would
    # gate them too -- listed here because this set exists precisely so a write never depends
    # on a configuration row being present and right.
    #
    # send_email queues mail from the site's own Email Account to ANY address the model
    # supplies, and declares `requires_permission = None`, so every Assistant User holds it.
    # That is the shape of an exfiltration channel: one injected instruction in a document the
    # model reads, and the data it just read leaves the building under our domain's name.
    "send_email",
    # generate_document renders markdown to a PDF and saves it as a private File -- a create,
    # which FAC itself reclassified from read_only to write in 3.0.0.
    "generate_document",
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
    # v1.335.0 -- the Item naming advisor. Read-only by construction: it composes
    # frappe.get_list reads and a pure rules module, and there is no Item doc_event
    # for it to trip. Listed here rather than left to FAC's category detector for the
    # reason the training tools record above -- unclassified, is_mutating() falls to
    # the fail-closed default and the tool answers with a confirmation card.
    "item_naming_check",
    # v1.339.0 -- the party-naming advisor for Project/Opportunity/Address. Read-only by
    # construction: frappe.get_list reads plus a pure rules module, and no validate hook
    # on any of the three for it to trip.
    "party_naming_check",
    # AI-authored trainings: draft a whole Course Spec from a brief. Read-only in the
    # sense the gate cares about -- it creates no Training records (its companion
    # author_training_course, below in APP_MUTATING, is the write). It does make one
    # Vertex call (logged to AI Model Usage) and caches the proposal; a confirmation
    # card in front of "draft me a proposal" would be friction with nothing to undo.
    "draft_course_spec",
}

# This app's own *write* tools (assistant_tools/<name>.py). They must gate even
# though FAC's category detector has never seen them — listed here so the gate
# never depends on the fail-closed fallback to confirm them.
APP_MUTATING = {
    "create_followup_task",
    "workforce_clock_in",
    "workforce_clock_out",
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
    # AI-authored trainings: build a Training Course + unpublished draft from a
    # Course Spec. A create that yields a Draft (never published) whose quiz
    # questions are all flagged unreviewed, so publication is still gated on a human.
    "author_training_course",
    # ... and the step after it. Publishing is a one-way door: _materialize_lessons
    # freezes the lesson titles into toc_json and a submitted version cannot be
    # edited, a Required course with auto_assign fans assignments out to everyone
    # with notifications, and change_type="Material Change (require retake)"
    # invalidates every existing completion. Not HIGH_RISK -- nothing is destroyed
    # and nothing arbitrary executes -- so it lands on the fail-safe Medium band by
    # being in neither risk set, which is the honest classification.
    "publish_training_course",
    # Open an editable draft of an EXISTING course. The gentlest write in this
    # group -- no learner sees anything change, the live version stays live, and an
    # unwanted draft is simply deleted -- but it is still a write on a live training
    # record, and the failure worth a confirmation card is the boring one: opening a
    # draft on the wrong course and editing it for an hour. Not HIGH_RISK, so it
    # lands on the Medium band alongside publish, which is if anything generous.
    "create_training_draft_version",
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
    # An email cannot be recalled once the queue flushes, and see EXPLICIT_MUTATING for why
    # this one in particular is an exfiltration channel rather than a notification.
    "send_email",
}
LOW_RISK = {
    "create_document",
    "create_dashboard",
    "create_dashboard_chart",
    "create_followup_task",
    "save_water_design",
    "author_training_course",
    # a private File the requester owns; nothing existing changes and deleting it undoes it
    "generate_document",
}

# Only plain-document create/update may use the settings exempt-doctype
# allowlist; privileged/irreversible tools never skip confirmation.
EXEMPTABLE_TOOLS = {"create_document", "update_document"}

# tool name -> callable(arguments) -> bool  (True = this CALL needs confirmation)
#
# Why the split exists: self-service is authority the person already has; 
# on-behalf is authority over someone else's record.
# Note: ai_write_gating_enabled ships default 0 and is currently OFF in production,
# so the gate is the human-in-the-loop layer, never the authorization.
# The role check that actually stops an unauthorised on-behalf clock-out lives inside
# close_interval_for_employee, and must not be assumed to live here.
# A decider must be pure and total — it runs inside the gate, and anything it raises must fail closed.
#
# A decider is never registered for a HIGH_RISK tool. Step 3b does not consult HIGH_RISK, so a
# decider returning False would run it unconfirmed; test_ai_gate_per_call pins the two sets
# disjoint. In particular run_python_code stays gated: as deployed (FAC 3.0.0) it hands the caller
# the whole `frappe` module on a read-write connection, so it is arbitrary code, not a read — and
# one unconfirmed call could create or close any Task and undo ADR 0016 §6 (verified 2026-09-23).

#: Task statuses an AI may set without a confirmation (ADR 0016 §6). An allowlist rather than a
#: Completed/Canceled denylist, so a status this site adds later, ERPNext core's "Cancelled"
#: spelling, "Invoiced" or a typo all wait for a human. Closing a Task is the thing to confirm.
_TASK_STATUSES_THAT_RUN = frozenset({"Open", "Working", "Pending Review", "Overdue"})

#: Keys in update_document's `data` that change which record is written or how it is saved
#: rather than a field of it. FAC 3.0.0 setattr()s every key it is given, so
#: {"name": <another Task>, "modified": <that Task's modified>} with no status would make the
#: save land on the OTHER Task and write this one's fields — its status included — over it
#: (frappe v16 check_if_latest compares against the modified the caller supplied). Each of these
#: waits for a human, as does `is_template`, from which ERPNext derives status "Template".
_TASK_KEYS_THAT_WAIT = frozenset(
    {
        "name",
        "doctype",
        "docstatus",
        "modified",
        "modified_by",
        "creation",
        "owner",
        "idx",
        "parent",
        "parenttype",
        "parentfield",
        "lft",
        "rgt",
        "old_parent",
        "amended_from",
        "is_template",
    }
)


def _update_document_needs_human(args):
    """update_document executes for a Task unless it closes it; anything else is ADR 0006's default.

    Reads the raw arguments FAC 3.0.0 passes, {doctype, name, data}, before FAC has validated
    them, so every shape it does not recognise answers "needs a human". True for other doctypes
    hands the call on to step 4, which is where an exempt doctype such as Comment still executes.
    """
    if not isinstance(args, dict) or args.get("doctype") != "Task":
        return True
    data = args.get("data")
    if not isinstance(data, dict):
        return True
    for key in data:
        if not isinstance(key, str) or key in _TASK_KEYS_THAT_WAIT or key.startswith("_"):
            return True
    if "status" not in data:
        return False
    status = data.get("status")
    return not (isinstance(status, str) and status in _TASK_STATUSES_THAT_RUN)


PER_CALL_GATED = {
    "workforce_clock_out": lambda args: bool((args or {}).get("employee")),
    "workforce_clock_in": lambda args: False,
    "update_document": _update_document_needs_human,
}

#: Doctypes the settings allowlist may never exempt, whatever a row says. The exemption step
#: applies to create_document and update_document alike, so exempting Task would ungate Task
#: creation along with its updates — the one write ADR 0016 §6 exists to confirm.
NEVER_EXEMPT = frozenset({"Task"})

# ------------------------------------------------- private-context denylist
#
# Some doctypes are unreachable through the generic FAC tools, and the refusal lives HERE
# rather than in the permission stack because two of those tools sit underneath the
# permission stack entirely.
#
# `run_database_query` states its own security model as "Restricted to SELECT statements
# only. Requires System Manager role for security." -- a role check and a read-only-SQL
# check, and nothing else. Raw SQL never consults DocPerm, `permission_query_conditions` or
# `has_permission`, so a doctype's own hooks close `get_document` and `list_documents` and do
# nothing at all to this one. `run_python_code` is the same shape and runs unconfirmed while
# `ai_write_gating_enabled` is off, which is how it ships.
#
# WHAT IS ON THE LIST AND WHY, because the list got shorter for a reason that is easy to
# misread. It held the 23 Chat DocTypes from v1.271.0 until ADR 0011 retired the chat module.
# It was very nearly removed entirely in that release, on the argument that a denylist naming
# doctypes that no longer exist protects nothing -- and that argument was WRONG, in a way
# worth recording:
#
#   `_normalise_for_denylist` reduces text to one contiguous needle, so the needle for
#   `Chat Attachment` was `chatattachment` -- a SUBSTRING of `tabtritonchatattachment`. The
#   chat list had therefore been gating `Triton Chat Attachment` all along, incidentally, and
#   that doctype SURVIVES. It holds every employee's private Triton uploads; its own
#   `ai_governance/permissions.py` says in bold that those hooks do not protect raw SQL; and
#   its `has_permission` deliberately returns False for a row a System Manager does not own,
#   "because a role that could read them would be a role that could read what everybody asked
#   the assistant about". Deleting the list wholesale would have silently un-gated it.
#
# So the entry below is the same protection, now DELIBERATE rather than a side effect of a
# substring match on an unrelated feature's name. Add to it whenever a doctype's content is
# private to one person and its hooks are the only thing between a System Manager and
# everybody else's rows.
DENYLIST_DOCTYPES = frozenset({"Triton Chat Attachment"})

# Arguments whose *text* is searched for a denylisted table name, per tool. A SQL string and a
# Python program are both free text: there is no `doctype` argument to compare, so the table
# name has to be matched inside the payload.
DENYLIST_TEXT_ARGUMENTS = {
    "run_database_query": ("query", "sql"),
    "run_python_code": ("code",),
}

_SQL_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_SQL_COMMENT_LINE = re.compile(r"(--|#)[^\n]*")
_NON_WORD = re.compile(r"[^a-z0-9_]+")


# ------------------------------------------------------------- pure helpers
# (unit-tested bench-free in tests/test_ai_gate_unit.py)


def _normalise_for_denylist(text):
    """Case-fold, strip comments, and drop everything that is not a word character, so a
    table name is one contiguous needle.

    ``select name from `tabTriton Chat Attachment` ``,
    ``SELECT/*x*/ NAME FROM   tabTRITON chat attachment`` and a `` `` `` -quoted,
    newline-split, comment-riddled spelling of the same thing all reduce to a string
    containing ``tritonchatattachment``. Backticks, quoting, whitespace, casing, SQL comments
    and ``information_schema`` filters all stop mattering, because none of them survive the
    reduction.

    **Underscores DO survive**, deliberately — the character class keeps ``_`` — so
    ``tab_Triton__Chat__Attachment`` does *not* match. That is correct rather than a hole:
    it is not the name of any table, so a query spelling it that way fails in MariaDB rather
    than returning rows. (The pre-v1.426.0 version of this docstring claimed the opposite,
    using that exact string as a worked example. It was wrong, and it was wrong in the
    reassuring direction, which is the kind of comment worth deleting rather than fixing
    quietly.)

    The rule this serves is coarse and absolute on purpose: **do not attempt to allow "safe"
    queries.** String-matching SQL loses to every one of the tricks above if it tries to
    parse; it wins only if it refuses on contact. Over-refusal costs an analyst one rephrase.
    Under-refusal costs the invariant, silently, with a correct answer delivered to the wrong
    reader.
    """
    if not isinstance(text, str):
        return ""
    lowered = text.lower()
    lowered = _SQL_COMMENT_BLOCK.sub(" ", lowered)
    lowered = _SQL_COMMENT_LINE.sub(" ", lowered)
    return _NON_WORD.sub("", lowered)


def _denylist_needles():
    """``[("tritonchatattachment", "Triton Chat Attachment")]`` -- the normalised needle for
    every denylisted DocType, sorted so a refusal names the most specific table it matched
    rather than a prefix of it."""
    pairs = ((_normalise_for_denylist(dt), dt) for dt in DENYLIST_DOCTYPES)
    return sorted((needle, dt) for needle, dt in pairs if needle)


def denylist_hit(tool_name, arguments):
    """The denylisted DocType this call would reach, or ``None``.

    Two shapes, because the tools come in two shapes. ``get_document`` / ``list_documents`` /
    ``search_documents`` and friends take a literal ``doctype`` argument, so that half is a
    string comparison rather than a parse. ``run_database_query`` and ``run_python_code`` take
    free text, so that half is the contact match described on
    :func:`_normalise_for_denylist`.

    The ``doctype`` check is applied to **every** tool rather than to a named list: a tool
    added to FAC tomorrow that takes a ``doctype`` is covered the day it appears, which is the
    opposite of how an allowlist of tool names would age.
    """
    args = arguments or {}
    if not isinstance(args, dict):
        return None

    target = args.get("doctype")
    if isinstance(target, str) and target.strip() in DENYLIST_DOCTYPES:
        return target.strip()

    needles = _denylist_needles()
    for key in DENYLIST_TEXT_ARGUMENTS.get(tool_name, ()):
        haystack = _normalise_for_denylist(args.get(key))
        if not haystack:
            continue
        for needle, doctype in reversed(needles):
            if needle in haystack:
                return doctype
    return None


def _denylist_refusal_message(doctype):
    return (
        f"Refused: {doctype} holds one person's private assistant context and is not "
        "readable through the generic Frappe tools, by any role, with AI gating on or off. "
        "Raw SQL consults no permission hook, so this refusal is the only thing standing "
        "between a System Manager and everybody else's rows. Ask the person, or read it as "
        "yourself in the Triton widget."
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

    FAC reads ``getattr(tool, "annotations", None)`` into its ``tools/list``
    response, so an MCP client such as Triton can read a tool's mutation/risk from
    here instead of guessing from the verb — which is how Triton was
    mis-classifying the oddly-named device tools (``remote_wipe_device`` /
    ``run_device_script`` / …) as read-only and skipping its confirmation step.
    Mutating tools advertise ``readOnlyHint: False`` + ``destructiveHint`` + an
    ``x-ee-risk`` band; explicit read tools advertise ``readOnlyHint: True``;
    unknown tools get ``{}`` (the client decides).

    **Not verbatim since FAC 2.5.0.** FAC merges hints derived from the tool's
    *FAC category* over these (``{**tool_annotations, **category_hints}``), and it
    seeds every external tool as ``read_write`` → ``readOnlyHint: False``. So every
    read tool here was advertised as a write until
    ``ai_governance/fac_tool_categories.py`` began aligning the categories with
    these annotations. The ``x-ee-*`` keys were never overridden, which is why
    Triton (it reads ``x-ee-mutation`` first) did not notice.

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
    if tool_name == "send_email":
        # The recipients ARE the decision, so the card names them rather than counting them:
        # "Send an email to 1 recipient" reads the same whether that is a colleague or a
        # stranger's address an injected instruction supplied.
        recipients = args.get("recipients") or []
        if isinstance(recipients, str):
            recipients = [recipients]
        shown = ", ".join(str(r) for r in list(recipients)[:3])
        if len(recipients) > 3:
            shown += f" (+{len(recipients) - 3} more)"
        subject = (args.get("subject") or "").strip()
        snippet = (subject[:60] + "…") if len(subject) > 60 else subject
        return f"Send an email to {shown or 'no recipients'}: “{snippet}”".strip()
    if tool_name == "generate_document":
        label = (args.get("title") or args.get("filename") or "").strip()
        return f"Generate a PDF document {label}".strip()
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
    if tool_name == "author_training_course":
        # The spec/token is opaque here; the card says what will happen, not its title.
        return "Author a training course from an AI draft (creates an unpublished Draft)"
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


def args_fingerprint(user, tool_name, arguments, key=None):
    """Dedup key for a proposal: same user, tool and arguments -> same card.

    Computed over the RAW arguments, so two proposals that differ only in a sealed credential
    value never collapse into one card (confirming it would run the first one's value). With
    ``key`` (the site's encryption key, which ``_propose`` passes) it is an HMAC: ``args_hash``
    is readable by anyone who can read the row -- AI Auditor included -- and the rest of the
    hashed text sits in plain sight in ``arguments``, so a bare hash over a short password is
    an offline guessing target. Without ``key`` it is the original plain SHA-1.
    """
    canonical = json.dumps(arguments or {}, sort_keys=True, default=str)
    message = f"{user}|{tool_name}|{canonical}".encode()
    if key:
        return hmac.new(str(key).encode(), message, hashlib.sha256).hexdigest()
    return hashlib.sha1(message).hexdigest()


# ------------------------------------------------ redaction and sealed values
#
# A proposal stores its arguments twice. ``arguments`` is the copy people read: the Desk card,
# check_ai_pending_action, the AI Action Log. Credential-like keys in it are replaced by
# REDACTED. ``sealed_arguments`` is a Password field holding just the values that were
# replaced, encrypted in __Auth, and it exists only while the action is Pending.
#
# Before v1.524.1 there was only the first copy, and confirm_action executed it, so a confirmed
# action wrote the literal "***REDACTED***" wherever a key looked like a credential. The
# predicate is a name heuristic, and most of what it catches is not a secret: FAC's own list
# includes the substring "auth", so `author` matches, and so does the `draft_token` argument of
# author_training_course. That tool could never succeed through a confirmation when it was
# given a draft_token, which is its preferred input.
#
# Only the replaced values are sealed, not the whole payload, for two reasons. The secret
# material is then exactly what was hidden, and nothing else. It is also small, whereas a whole
# create_document payload can exceed what a Password field's TEXT column holds once Fernet and
# base64 have inflated it.

REDACTED = "***REDACTED***"

#: A sealed payload is Fernet-encrypted and base64'd into __Auth.password, a TEXT column
#: (65,535 bytes), and "*" * len(payload) goes into the doc column. Both grow roughly 4/3
#: plus a constant. This leaves generous headroom under that.
SEALED_MAX_BYTES = 32_000

#: A sealed string shorter than this is not masked out of a stored result or error. Masking
#: replaces every occurrence, so a false positive like `author: "Jo"` would shred ordinary
#: text if it were masked.
MASK_MIN_LENGTH = 6


class SealError(Exception):
    """The sealed values cannot be put back exactly where they came from."""


def _sensitive_key_predicate():
    """FAC's heuristic when importable, else the historical fallback."""
    try:
        from frappe_assistant_core.core.base_tool import _is_sensitive_key

        return _is_sensitive_key
    except Exception:

        def _is_sensitive_key(key):
            return isinstance(key, str) and any(
                fragment in key.lower()
                for fragment in ("password", "secret", "api_key", "token", "credential")
            )

        return _is_sensitive_key


def redact_arguments(arguments):
    """Return ``(sanitized, sealed)``.

    ``sanitized`` is ``arguments`` with every credential-like key's value replaced by
    REDACTED. ``sealed`` lists ``[path, value]`` for each replacement, where ``path`` holds
    the dict keys (str) and list indices (int) from the root down to that key.
    """
    is_sensitive = _sensitive_key_predicate()
    sealed = []

    def scrub(value, path):
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                if is_sensitive(k):
                    out[k] = REDACTED
                    sealed.append([[*path, k], v])
                else:
                    out[k] = scrub(v, [*path, k])
            return out
        if isinstance(value, (list, tuple)):
            return [scrub(v, [*path, i]) for i, v in enumerate(value)]
        return value

    return scrub(arguments or {}, []), sealed


def sanitize_arguments(arguments):
    """Redact credential-like keys (FAC's heuristic when importable)."""
    return redact_arguments(arguments)[0]


def seal_payload(sealed):
    """The Password-field value for ``sealed``, or None when nothing was redacted.

    Raises SealError when the payload is too large to store (see SEALED_MAX_BYTES).
    """
    if not sealed:
        return None
    payload = json.dumps(sealed, default=str)
    if len(payload.encode("utf-8")) > SEALED_MAX_BYTES:
        raise SealError("the credential-like values are too large to hold for confirmation")
    return payload


def unseal_arguments(arguments, sealed):
    """Put each sealed value back into ``arguments`` (parsed from the stored copy), in place.

    Refuses rather than guessing. Every path must walk dicts by str key and lists by int
    index, and must end at a dict key whose value is still exactly REDACTED. Anything else
    means the stored copy and the seal disagree, and executing either one would write
    something nobody confirmed. Any unexpected error is raised as SealError too. That keeps
    it on confirm_action's refusal path and off a 500, whose error snapshot would print this
    frame's locals.
    """
    try:
        return _unseal(arguments, sealed)
    except SealError:
        raise
    except Exception:
        raise SealError("the hidden values do not fit the stored arguments") from None


def _unseal(arguments, sealed):
    for entry in sealed or []:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise SealError("malformed sealed entry")
        path, value = entry
        if not isinstance(path, (list, tuple)) or not path:
            raise SealError("malformed sealed path")
        container = arguments
        for step in path[:-1]:
            if isinstance(container, dict) and isinstance(step, str) and step in container:
                container = container[step]
            elif (
                isinstance(container, list)
                and isinstance(step, int)
                and not isinstance(step, bool)
                and 0 <= step < len(container)
            ):
                container = container[step]
            else:
                raise SealError("sealed path does not match the stored arguments")
        last = path[-1]
        if not isinstance(container, dict) or not isinstance(last, str) or container.get(last) != REDACTED:
            raise SealError("sealed path does not end at a redacted value")
        container[last] = value
    return arguments


def has_unrestored_redaction(arguments):
    """True if any credential-like key still holds REDACTED, i.e. the sanitizer's placeholder
    rather than a value anyone proposed. A literal "***REDACTED***" under an ordinary key is
    left alone: the sanitizer could not have put it there."""
    is_sensitive = _sensitive_key_predicate()

    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if is_sensitive(k) and v == REDACTED:
                    return True
                if walk(v):
                    return True
        elif isinstance(value, (list, tuple)):
            for v in value:
                if walk(v):
                    return True
        return False

    return walk(arguments)


def mask_secrets(value, sealed):
    """``value`` with each sealed string (MASK_MIN_LENGTH or longer) replaced by REDACTED.

    Used on what the confirmed execution returns or raises. Before the seal existed, a
    confirmed action ran with the placeholder, so its result and error could only ever echo
    the placeholder. Now it runs with the real value, and a Frappe validation message or a
    tool result that quotes it would carry it into AI Pending Action and AI Action Log, which
    are rows the redaction exists to keep it out of.
    """
    secrets = []

    def collect(v):
        if isinstance(v, str):
            if len(v) >= MASK_MIN_LENGTH:
                secrets.append(v)
        elif isinstance(v, dict):
            for item in v.values():
                collect(item)
        elif isinstance(v, (list, tuple)):
            for item in v:
                collect(item)

    for entry in sealed or []:
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            collect(entry[1])
    if not secrets:
        return value
    # Longest first, so a secret that contains another is masked whole.
    secrets.sort(key=len, reverse=True)

    def mask(v):
        if isinstance(v, str):
            for secret in secrets:
                if secret in v:
                    v = v.replace(secret, REDACTED)
            return v
        if isinstance(v, dict):
            return {k: mask(item) for k, item in v.items()}
        if isinstance(v, (list, tuple)):
            return [mask(item) for item in v]
        return v

    return mask(value)


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
        rows = {row.document_type for row in settings.get("ai_exempt_doctypes") or []}
        return rows - NEVER_EXEMPT
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

# FAC 3.0.0 reads an `X-AR-Session-Id` header into `frappe.local.ar_session_id` when the call
# comes from FAC Chat's cloud runtime ("AR"), which reaches this site through the very same
# `handle_mcp` endpoint as every other client -- so its tool calls land in this gate too. That
# header names the CONVERSATION. FAC's own `assistant_session_id` is no substitute: absent an
# `Mcp-Session-Id` header it is a fresh UUID per request, so on a stateless endpoint it
# identifies nothing a human could look up afterwards.
FAC_CHAT_CLIENT_ID = "fac-chat"


def _session_id():
    local = getattr(frappe, "local", None)
    return getattr(local, "ar_session_id", None) or getattr(local, "assistant_session_id", None)


def _client_id():
    local = getattr(frappe, "local", None)
    client = getattr(local, "assistant_client_id", None)
    if not client and getattr(local, "ar_session_id", None):
        return FAC_CHAT_CLIENT_ID
    return client


def _fingerprint_key():
    """The site's encryption key, for args_fingerprint's HMAC. None means the plain hash is
    used. get_encryption_key() creates the key on first use, so on a real site that only
    happens when site_config.json cannot be written."""
    try:
        from frappe.utils.password import get_encryption_key

        return get_encryption_key()
    except Exception:
        return None


def _propose(tool, arguments):
    user = frappe.session.user
    name = getattr(tool, "name", "")
    sanitized, sealed = redact_arguments(arguments)
    try:
        sealed_payload = seal_payload(sealed)
    except SealError as e:
        # Queueing it without the values would guarantee a card that cannot run as proposed.
        return _error_response(f"This action was not queued for confirmation: {e}.")
    fingerprint = args_fingerprint(user, name, arguments, key=_fingerprint_key())

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
            "client_id": _client_id(),
            "session_id": _session_id(),
            "arguments": json.dumps(sanitized, default=str, indent=1),
            # Password field: Frappe encrypts it into __Auth on insert and keeps only
            # asterisks in the column. The controller deletes it once the action is decided.
            "sealed_arguments": sealed_payload,
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
    # 0) Private-context doctypes are refused outright, and this branch is first for a
    #    reason. Everything below it can be switched off -- the confirm-flow bypass by a flag,
    #    the rest by `ai_write_gating_enabled`, which ships dormant. A refusal reachable only
    #    while a settings checkbox is ticked is not an invariant, and this one is: no role, no
    #    flag and no confirmation makes one person's private assistant context readable
    #    through a generic tool.
    denied = denylist_hit(getattr(tool, "name", ""), arguments)
    if denied:
        message = _denylist_refusal_message(denied)
        # Evidence, not silence. An attempt to read private context through a generic tool is
        # exactly the event an operator wants to find later, and AI Action Log is already
        # append-only and already purged on a schedule.
        insert_action_log(
            user=getattr(getattr(frappe, "session", None), "user", None),
            tool_name=getattr(tool, "name", ""),
            arguments=arguments,
            success=False,
            risk="High",
            summary=f"Refused a generic-tool read of private assistant context ({denied}).",
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

    # 3b) Per-call gating (ADR 0014). Only the DECIDER runs inside the guard.
    #
    # Getting this nesting wrong is a double-execution bug rather than a missed
    # confirmation: if `original(...)` or the logging below sat inside the same
    # try/except, a failure AFTER the write had already happened would be
    # swallowed and fall through to the Pending Action path — offering a human a
    # card for an action that had already run, which executes it a second time on
    # confirm. So the guard covers the decision and nothing else, and the
    # execution path below can only be reached by a decider that returned cleanly.
    name = getattr(tool, "name", "")
    decider = PER_CALL_GATED.get(name)
    if decider is not None:
        try:
            needs_confirmation = bool(decider(arguments))
        except Exception:
            # Fail closed: an undecidable call is one a human should see.
            needs_confirmation = True
        if not needs_confirmation:
            response = original(tool, arguments)
            # Evidence, not silence: this is the only record that a write ran
            # without a human seeing it. insert_action_log never raises to its
            # caller by contract, which is what keeps this off the path above.
            as_dict = response if isinstance(response, dict) else {}
            insert_action_log(
                user=frappe.session.user,
                tool_name=name,
                arguments=arguments,
                success=bool(as_dict.get("success")) if as_dict else True,
                result=as_dict.get("result", response if not as_dict else None),
                error=as_dict.get("error"),
                error_type=as_dict.get("error_type"),
                auto_approved=1,
                execution_time=as_dict.get("execution_time"),
            )
            return response

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
    _wrap_log_execution(BaseTool)


def _wrap_log_execution(BaseTool):
    """Keep sealed values out of FAC's own Assistant Audit Log row for a confirmed call.

    A confirmed call runs FAC's real ``_safe_execute`` with the restored values, and
    ``log_execution`` writes the Assistant Audit Log row. That row is committed together with
    the confirmed write. FAC's argument sanitizer only looks at top-level keys, so the nested
    ``data.<field>`` of every create/update would be stored in plaintext. Its output sanitizer
    redacts by key name, never by value, so an echoed value would be stored too.

    While ``gating_api.confirm_action`` holds ``frappe.flags.ai_gate_sealed``, this wrapper gives
    FAC the recursively redacted arguments and masks the sealed strings from everything else it
    logs: result, error, traceback. Class-level and keyed on a request-local flag, so a
    concurrent call in another thread is untouched. On a failed call FAC's row, and the Error
    Log it writes, go with the rollback in confirm_action.
    """
    original_log = getattr(BaseTool, "log_execution", None)
    if original_log is None or getattr(original_log, GATE_MARKER, False):
        return

    @functools.wraps(original_log)
    def masked_log_execution(self, arguments, result, *args, **kwargs):
        # Named to match Frappe's traceback blocklist ("secret"), like confirm_action's locals.
        sealed_secrets = getattr(getattr(frappe, "flags", None), "ai_gate_sealed", None)
        if sealed_secrets:
            arguments = mask_secrets(sanitize_arguments(arguments), sealed_secrets)
            result = mask_secrets(result, sealed_secrets)
            args = tuple(mask_secrets(list(args), sealed_secrets))
            kwargs = mask_secrets(kwargs, sealed_secrets)
        return original_log(self, arguments, result, *args, **kwargs)

    setattr(masked_log_execution, GATE_MARKER, True)
    BaseTool.log_execution = masked_log_execution
