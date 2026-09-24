"""Whitelisted confirm / cancel endpoints for AI Pending Actions.

Called from the AI Pending Action form buttons and list view by dotted path
(``frappe.call`` resolves them at request time — no Python import from app
code, keeping the FAC-optional tripwire green). Confirmation is desk-only by
design: there is no MCP-exposed confirm tool (see ``_gate.py``), and nothing in
this module is registered as one or listed in FAC's tool configuration. That
includes the batch endpoints below.

**Every endpoint here is POST-only and refuses a token-authenticated request**
(v1.528.0). POST, because Frappe skips its CSRF check for GET, and ``_confirm_one``
commits for itself, so a GET link to ``confirm_action`` (one an assistant reply
can render as a same-origin anchor) used to run a gated action on a single
click, with no dialog. No token, because a decision has to come from a person
at the desk: ``_require_desk_session`` explains why.

Batch decisions (v1.528.0). ``my_pending_actions`` lists the session user's own
queue. ``confirm_actions`` and ``cancel_actions`` decide up to ``MAX_BATCH`` of
them in one request, oldest first, one at a time through the same
``_confirm_one`` / ``_cancel_one`` that the form's buttons use. Each decision
commits on its own, so one failure neither stops nor undoes the others.

**A batch is not atomic, and a request killed mid-execution can strand one
action.** ``_confirm_one`` commits Confirmed before it runs the tool. If the
worker is killed while the tool runs (gunicorn's timeout), that action's write
rolls back but its Confirmed status stays, and nothing re-drives or fails a
Confirmed action. So a request stops starting new actions once it has run for
``BATCH_TIME_BUDGET_SECONDS`` and returns the rest as Skipped, and the list view
sends one action per request, oldest first. A batch request is then exactly the
request the form's own Confirm button makes, with the same timeout exposure, and
the budget only matters to a caller that sends several at once.

**The batch ownership rule is stricter than ``_check_identity``.** A batch only
covers actions whose ``requested_by`` is the session user, System Managers
included. ``_check_identity`` lets a System Manager decide anybody's action, and
that override exists for the exception: a card that has to be decided while the
person it was proposed for is away, read and decided on its own. A confirmed
action runs as the person who confirms it, so a System Manager confirming
someone else's queue would run every proposal in it with System Manager rights,
which the requester's own assistant could never have used. One card at a time
that is a deliberate decision. As a batch it would be one click over proposals
nobody read, and some of them could have been planted by a prompt injection in
the other person's session. So the batch endpoints skip other people's actions,
and the form still lets a System Manager decide them one by one.

FAC imports happen inside function bodies so this module imports cleanly on
FAC-less sites (and under the bench-free test stubs); calling confirm there
fails with a clear message instead of an ImportError traceback.
"""

import json
from time import monotonic

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from erpnext_enhancements.assistant_tools._gate import (
    NEVER_EXEMPT,
    SealError,
    _changes_docstatus,
    has_unrestored_redaction,
    insert_action_log,
    mask_secrets,
    truncate_json,
    unseal_arguments,
)

#: The most actions one batch request may decide. Each one can run a tool, and they all run
#: inside a single web request, so ``BATCH_TIME_BUDGET_SECONDS`` also bounds it. The list view
#: sends one per request anyway.
MAX_BATCH = 50

#: After this many seconds a batch request starts no new action and returns the rest as Skipped.
#: gunicorn kills a worker that runs past its timeout (bench's ``http_timeout``, usually 120s) in
#: the middle of whatever it is doing, and an action killed mid-tool stays Confirmed forever with
#: its write rolled back. 45s leaves room for one slow tool that starts just before the budget
#: runs out.
BATCH_TIME_BUDGET_SECONDS = 45

#: The most rows ``my_pending_actions`` returns.
MY_PENDING_LIMIT = 100

#: Summaries in batch results are cut to this many characters.
SUMMARY_MAX = 200


def _require_desk_session():
    """Refuse a request that carries an ``Authorization`` header. Every endpoint calls it first.

    Confirmation is desk-only by design: the gate exists so that a person, signed in as
    themselves, reads a proposal and decides it. Frappe v16 authenticates a request from that
    header in three ways (``frappe.auth.validate_auth``): an OAuth bearer token, an API key and
    secret as ``token key:secret``, or the same pair as ``Basic``. And ``validate_oauth`` checks
    a bearer token against *its own* scopes, so any valid token opens any ``/api/method`` call.
    An OAuth client holding the user's token, such as an MCP client or FAC Chat's cloud side,
    would otherwise be able to list its own proposals and confirm them, which is exactly the
    decision the gate takes away from the model. The desk never sends the header: it
    authenticates with the session cookie and the CSRF token.

    Outside a web request (a job, the console, ``bench run-tests``) there is no header, and this
    does nothing. An ``auth_hooks`` authenticator that reads some other header would not be seen
    here; this app registers none. ``marketing.publish.approval.browser_request`` is the same
    idea for approving a social post.
    """
    if getattr(getattr(frappe, "local", None), "request", None) is None:
        return
    if frappe.get_request_header("Authorization"):
        frappe.throw(
            _(
                "AI actions are decided in the desk, by a person signed in as themselves. This "
                "request was authenticated with a token or API key, so it cannot list, reveal or "
                "decide them."
            ),
            frappe.PermissionError,
        )


def _check_identity(action):
    user = frappe.session.user
    if user != action.requested_by and "System Manager" not in frappe.get_roles():
        frappe.throw(
            _("Only {0} (who the AI was acting for) or a System Manager may decide this action.").format(
                action.requested_by
            ),
            frappe.PermissionError,
        )


def _transition(action, **values):
    frappe.flags.ai_action_transition = True
    try:
        for key, value in values.items():
            action.set(key, value)
        action.save(ignore_permissions=True)
    finally:
        frappe.flags.ai_action_transition = False


def _arguments_to_execute(action):
    """The arguments exactly as the assistant proposed them, plus the sealed entries.

    ``action.arguments`` is the redacted copy people read. The values it hides come back from
    the sealed Password field. Raises SealError when they cannot be restored exactly, and in
    that case nothing may run: the only alternative is writing "***REDACTED***" into a real
    record, which is what this used to do.

    Local names that hold a restored value contain "secret" on purpose. On a 5xx, Frappe logs
    an Error Log snapshot using get_traceback(with_context=True), which prints every frame's
    locals except those whose names match its blocklist (password, secret, token, key...).
    That rule covers top-level names only, so a restored value nested inside ``arguments``
    would otherwise be printed.
    """
    try:
        secret_arguments = json.loads(action.arguments or "{}")
        sealed_secrets = []
        if action.get("sealed_arguments"):
            try:
                secret_payload = action.get_password("sealed_arguments", raise_exception=False)
            except Exception:
                secret_payload = None
            if not secret_payload:
                raise SealError("its hidden values could not be decrypted")
            try:
                sealed_secrets = json.loads(secret_payload)
            except ValueError:
                raise SealError("its hidden values are unreadable") from None
            unseal_arguments(secret_arguments, sealed_secrets)
        if has_unrestored_redaction(secret_arguments):
            # A pre-v1.524.1 proposal (no seal) or a seal that doesn't cover every placeholder.
            raise SealError("some of its values were hidden and not kept")
    except SealError:
        raise
    except Exception:
        raise SealError("its stored arguments are unreadable") from None
    return secret_arguments, sealed_secrets


def _confirm_one(name):
    """Execute a Pending action as the confirming user and record the outcome.

    The Confirmed status is committed *before* execution (so the decision
    survives a crash); on failure the transaction is rolled back first (the
    tool may have partially written) and the Failed outcome + log row are
    persisted afterwards.

    It executes the arguments as proposed, not the redacted copy on the card. Credential-like
    values come back from the sealed field, which the Confirmed transition then deletes. What
    the execution returns or raises is masked before it is stored, here and, through
    ``frappe.flags.ai_gate_sealed``, in FAC's own Assistant Audit Log row
    (``_gate._wrap_log_execution``). The ``secret_*`` local names are explained in
    ``_arguments_to_execute``.

    Shared by ``confirm_action`` (one card, from its form) and ``confirm_actions`` (a batch).
    Every outcome worth keeping is committed here before anything is raised, which is what
    lets the batch roll back after a failure without losing it.
    """
    action = frappe.get_doc("AI Pending Action", name)
    _check_identity(action)

    if action.status != "Pending":
        frappe.throw(_("This action is {0} — only Pending actions can be confirmed.").format(action.status))
    if action.expires_at and get_datetime(action.expires_at) < now_datetime():
        _transition(action, status="Expired")
        frappe.db.commit()
        frappe.throw(_("This action expired before it was confirmed. Ask the assistant to propose it again."))

    try:
        from frappe_assistant_core.core.tool_registry import get_tool_registry
    except ImportError:
        frappe.throw(_("Frappe Assistant Core is not installed on this site."))

    try:
        secret_arguments, sealed_secrets = _arguments_to_execute(action)
    except SealError as e:
        message = _(
            "Not executed: {0}. Running it would write the placeholder ***REDACTED*** instead of "
            "what the assistant proposed. Ask the assistant to propose it again."
        ).format(e)
        # Failed, not left Pending. A re-proposal with the same arguments would otherwise dedupe
        # onto this same unrunnable card until it expired.
        try:
            card = json.loads(action.arguments or "{}")
        except Exception:
            card = {}
        log_name = insert_action_log(
            user=frappe.session.user,
            tool_name=action.tool_name,
            arguments=card,
            success=0,
            risk=action.risk,
            summary=action.summary,
            error=message,
            error_type="SealError",
            pending_action=action.name,
        )
        _transition(
            action,
            status="Failed",
            decided_by=frappe.session.user,
            decided_at=now_datetime(),
            error=message,
            action_log=log_name,
        )
        frappe.db.commit()
        frappe.throw(message)

    _transition(action, status="Confirmed", decided_by=frappe.session.user, decided_at=now_datetime())
    frappe.db.commit()

    frappe.flags.ai_gate_bypass = True
    frappe.flags.ai_gate_pending = action.name
    frappe.flags.ai_gate_sealed = sealed_secrets or None
    try:
        # Re-runs FAC's accessibility + permission checks for the *confirming*
        # user, FAC's own audit logging, and the actual tool.
        secret_result = get_tool_registry().execute_tool(action.tool_name, secret_arguments)
    except Exception as secret_error:
        frappe.db.rollback()  # the tool may have partially written; FAC's audit + Error Log rows go too
        action = frappe.get_doc("AI Pending Action", name)  # post-rollback state (Confirmed survived)
        error = mask_secrets(str(secret_error), sealed_secrets)
        log_name = insert_action_log(
            user=frappe.session.user,
            tool_name=action.tool_name,
            arguments=secret_arguments,
            success=0,
            risk=action.risk,
            summary=action.summary,
            error=error,
            error_type=type(secret_error).__name__,
            pending_action=action.name,
        )
        _transition(action, status="Failed", error=error[:2000], action_log=log_name)
        frappe.db.commit()  # persist the Failed outcome before throwing
        frappe.throw(_("Execution failed: {0}").format(error))
    finally:
        frappe.flags.ai_gate_bypass = False
        frappe.flags.ai_gate_pending = None
        frappe.flags.ai_gate_sealed = None

    stored_result = mask_secrets(secret_result, sealed_secrets)
    log_name = insert_action_log(
        user=frappe.session.user,
        tool_name=action.tool_name,
        arguments=secret_arguments,
        success=1,
        risk=action.risk,
        summary=action.summary,
        result=stored_result,
        pending_action=action.name,
    )
    target_name = action.target_name
    if not target_name and isinstance(secret_result, dict):
        target_name = secret_result.get("name")
    _transition(
        action,
        status="Executed",
        result=truncate_json(stored_result),
        action_log=log_name,
        target_name=target_name,
    )
    frappe.db.commit()
    return {"status": "Executed", "action_log": log_name}


@frappe.whitelist(methods=["POST"])
def confirm_action(name):
    """Execute one Pending action from its form. The work is ``_confirm_one``, which the batch
    endpoint shares.

    POST-only since v1.528.0. Frappe skips its CSRF check for GET, and ``_confirm_one`` commits
    for itself, so a GET link here ran the action on one click with no dialog.
    """
    _require_desk_session()
    return _confirm_one(name)


def _display_path(path):
    """``["data", "rows", 0, "password"]`` -> ``data.rows[0].password``."""
    text = ""
    for step in path:
        if isinstance(step, int) and not isinstance(step, bool):
            text += f"[{step}]"
        else:
            text += f".{step}" if text else str(step)
    return text


@frappe.whitelist(methods=["POST"])
def reveal_sealed(name):
    """The values the card shows as ***REDACTED***, for the two people who may decide it.

    Confirming runs these values, so the person confirming must be able to read them first.
    Most of what the name heuristic hides is ordinary data, like an `author` or a
    `credential_number`, and a prompt-injected value there would otherwise go through unseen.
    Only while the action is Pending and unexpired, and only for the requester or a System
    Manager. Both could already obtain them: the requester's own assistant proposed them, and
    a System Manager can call frappe.client.get_password. Each reveal leaves a comment on the
    action.
    """
    _require_desk_session()
    action = frappe.get_doc("AI Pending Action", name)
    _check_identity(action)
    if action.status != "Pending":
        frappe.throw(_("This action is {0} — its hidden values are gone.").format(action.status))
    if action.expires_at and get_datetime(action.expires_at) < now_datetime():
        frappe.throw(_("This action has expired."))
    if not action.get("sealed_arguments"):
        return []
    secret_payload = action.get_password("sealed_arguments", raise_exception=False)
    if not secret_payload:
        frappe.throw(_("The hidden values could not be decrypted."))
    try:
        sealed_secrets = json.loads(secret_payload)
    except ValueError:
        frappe.throw(_("The hidden values are unreadable."))
    action.add_comment("Info", _("Hidden values viewed by {0}").format(frappe.session.user))
    return [
        {"path": _display_path(entry[0]), "value": entry[1]}
        for entry in sealed_secrets
        if isinstance(entry, (list, tuple)) and len(entry) == 2 and isinstance(entry[0], (list, tuple))
    ]


def _cancel_one(name):
    """Mark a Pending action Cancelled (no execution). It does not commit; the request does,
    and the batch commits after each one."""
    action = frappe.get_doc("AI Pending Action", name)
    _check_identity(action)
    if action.status != "Pending":
        frappe.throw(_("This action is {0} — only Pending actions can be cancelled.").format(action.status))
    _transition(action, status="Cancelled", decided_by=frappe.session.user, decided_at=now_datetime())
    return {"status": "Cancelled"}


@frappe.whitelist(methods=["POST"])
def cancel_action(name):
    """Mark a Pending action Cancelled (no execution). POST-only, like ``confirm_action``."""
    _require_desk_session()
    return _cancel_one(name)


# ------------------------------------------------------------------ batches


def _seconds_between(start, end):
    """Whole seconds from ``start`` to ``end``, or None when either can't be read."""
    try:
        return int((get_datetime(end) - get_datetime(start)).total_seconds())
    except Exception:
        return None


def _review_reasons(row, has_hidden):
    """Why the batch dialog should not tick this action to begin with, as short phrases.

    Risk comes from the tool name alone, so an ``update_document`` that cancels an invoice and
    one that edits its remarks are both Medium, with the same summary. These are the questions
    the gate itself treats as never exempt (``_changes_docstatus``, ``NEVER_EXEMPT``), asked of
    the redacted ``arguments`` the form shows. ``sealed_arguments`` is never read, and the
    arguments are not returned. Arguments that cannot be parsed need a look too.
    """
    reasons = []
    if row.get("risk") == "High":
        reasons.append(_("high risk"))
    if has_hidden:
        reasons.append(_("has hidden values"))
    try:
        arguments = json.loads(row.get("arguments") or "{}")
    except Exception:
        arguments = None
    if not isinstance(arguments, dict):
        reasons.append(_("its arguments could not be read"))
    elif _changes_docstatus(arguments):
        reasons.append(_("submits or cancels a document"))
    if row.get("target_doctype") in NEVER_EXEMPT:
        reasons.append(_("changes the AI gate's own records or settings"))
    return reasons


@frappe.whitelist(methods=["POST"])
def my_pending_actions():
    """The session user's own Pending, unexpired actions, oldest first, for the batch dialog.

    Read-only. Only rows whose ``requested_by`` is the session user, System Managers included
    (see the module docstring). ``has_hidden`` says whether the action has sealed values, and is
    answered by a second query that filters on the column rather than reading it, so neither
    the ciphertext nor the asterisks the column holds ever enter this function. Nothing is
    decrypted. ``batch_default`` is whether the dialog ticks the row to begin with, and
    ``review_reason`` says why not when it doesn't (``_review_reasons``): High risk, hidden
    values, a submit or cancel, or a write to the gate's own records.

    ``age_seconds`` and ``expires_in_seconds`` are computed here in site-local time on both
    sides, so the browser never has to compare a site-local stamp with its own clock.
    """
    _require_desk_session()
    user = frappe.session.user
    now = now_datetime()
    rows = frappe.get_all(
        "AI Pending Action",
        filters={"requested_by": user, "status": "Pending"},
        # A `>` filter on a datetime compiles to a bare comparison, which a NULL never passes,
        # so "no expiry" has to be asked for explicitly. filters AND (either of these).
        or_filters=[["expires_at", "is", "not set"], ["expires_at", ">", now]],
        # `arguments` is the redacted copy, read only by _review_reasons and never returned.
        fields=[
            "name",
            "tool_name",
            "summary",
            "risk",
            "creation",
            "expires_at",
            "target_doctype",
            "arguments",
        ],
        order_by="creation asc",
        limit=MY_PENDING_LIMIT,
    )
    names = [row.get("name") for row in rows]
    hidden = set()
    if names:
        hidden = set(
            frappe.get_all(
                "AI Pending Action",
                filters={"name": ["in", names], "sealed_arguments": ["is", "set"]},
                pluck="name",
            )
        )
    out = []
    for row in rows:
        has_hidden = row.get("name") in hidden
        expires_at = row.get("expires_at")
        reasons = _review_reasons(row, has_hidden)
        out.append(
            {
                "name": row.get("name"),
                "tool_name": row.get("tool_name"),
                "summary": row.get("summary"),
                "risk": row.get("risk"),
                "creation": row.get("creation"),
                "expires_at": expires_at,
                "target_doctype": row.get("target_doctype"),
                "has_hidden": has_hidden,
                "batch_default": not reasons,
                "review_reason": ", ".join(reasons),
                "age_seconds": _seconds_between(row.get("creation"), now),
                "expires_in_seconds": _seconds_between(now, expires_at) if expires_at else None,
            }
        )
    return out


def _batch_names(names):
    """``names`` as a list of unique, non-empty strings, in the order given.

    Accepts a JSON list (what ``frappe.call`` sends) or a list. Anything else, or more than
    MAX_BATCH unique names, is refused before any action is read.

    Refused early, because a request body can hold millions of names and any signed-in user can
    send one: a list longer than four times the cap is refused before the loop looks at a single
    entry (four times, so a client may still repeat itself), and the loop stops at the first name
    past the cap. The seen-set keeps it linear; ``not in`` on the growing list was quadratic.
    """
    shape_error = _("Send the actions as a list of AI Pending Action names.")
    if isinstance(names, str):
        try:
            names = json.loads(names)
        except ValueError:
            frappe.throw(shape_error)
    if not isinstance(names, (list, tuple)):
        frappe.throw(shape_error)
    if len(names) > MAX_BATCH * 4:
        frappe.throw(
            _(
                "At most {0} actions can be decided at once, and {1} names were sent. "
                "Decide them in smaller groups."
            ).format(MAX_BATCH, len(names))
        )
    unique = []
    seen = set()
    for entry in names:
        if not isinstance(entry, str):
            frappe.throw(shape_error)
        entry = entry.strip()
        if not entry or entry in seen:
            continue
        seen.add(entry)
        unique.append(entry)
        if len(unique) > MAX_BATCH:
            frappe.throw(
                _(
                    "At most {0} actions can be decided at once, and more than that were sent. "
                    "Decide them in smaller groups."
                ).format(MAX_BATCH)
            )
    return unique


def _batch_plan(names):
    """``[(name, row)]``: the actions found, oldest first, then the names not found with a None row.

    Oldest first so that a proposal which depends on an earlier one runs after it.
    """
    rows = []
    if names:
        rows = frappe.get_all(
            "AI Pending Action",
            filters={"name": ["in", names]},
            fields=["name", "tool_name", "summary", "status", "requested_by", "expires_at", "creation"],
        )
    by_name = {row.get("name"): row for row in rows}
    found = sorted(
        (by_name[name] for name in names if name in by_name),
        key=lambda row: (str(row.get("creation") or ""), row.get("name")),
    )
    return [(row.get("name"), row) for row in found] + [(name, None) for name in names if name not in by_name]


def _skip_reason(row, user, now):
    """Why this action is not decided in a batch, or None if it may be."""
    if row is None or row.get("requested_by") != user:
        # One message for both, so the batch is not a way to find out whose actions exist.
        return _(
            "Skipped: not an action the AI proposed for you, or it no longer exists. A batch only "
            "covers your own actions; a System Manager can still decide someone else's from its form."
        )
    if row.get("status") != "Pending":
        return _("Skipped: it is already {0}.").format(row.get("status"))
    expires_at = row.get("expires_at")
    if expires_at and get_datetime(expires_at) < now:
        return _("Skipped: it expired at {0}. Ask the assistant to propose it again.").format(expires_at)
    return None


def _failure_message(error):
    text = str(error).strip() or type(error).__name__
    return text[:1000]


def _log_unexpected_failure(name, error, message):
    """Leave an Error Log for a failure that is not an ordinary refusal.

    Ordinary refusals (a frappe.throw, which includes a failed execution) are already recorded
    on the action. Anything else would, from the form, have been a 500 with an Error Log, and a
    batch that swallows it must not lose that trace. The message is passed explicitly, so
    Frappe logs this text rather than a traceback with frame locals, which could hold the
    restored values ``_confirm_one`` names ``secret_*``.
    """
    validation_error = getattr(frappe, "ValidationError", None)
    if isinstance(validation_error, type) and isinstance(error, validation_error):
        return
    try:
        frappe.log_error(
            title=f"AI Pending Action batch decision failed: {name}",
            message=f"{type(error).__name__}: {message}",
            reference_doctype="AI Pending Action",
            reference_name=name,
        )
        frappe.db.commit()  # the next action's rollback must not take this with it
    except Exception:
        pass


def _run_batch(names, decide, done_status):
    """Decide each action in ``names`` with ``decide(name)``, skipping the ones a batch may not.

    Messages are muted around each decision. In v16 ``msgprint`` still raises a throw's
    exception while ``frappe.flags.mute_messages`` is set, it just queues no modal, so a
    twenty-action batch reports its failures in the results table rather than in twenty
    dialogs. After a failure the transaction is rolled back: whatever the decision had worth
    keeping, it committed before raising, and anything uncommitted is a partial write.

    Once ``BATCH_TIME_BUDGET_SECONDS`` have passed, no further action is started. The ones left
    are returned as Skipped, still Pending, for the caller to send again (see the module
    docstring for what a killed worker would do instead). An action skipped for its own reason
    keeps that reason.
    """
    user = frappe.session.user
    names = _batch_names(names)
    now = now_datetime()
    started = monotonic()
    results = []
    counts = {done_status.lower(): 0, "failed": 0, "skipped": 0}
    budget_reached = False
    for name, row in _batch_plan(names):
        owned = row is not None and row.get("requested_by") == user
        item = {
            "name": name,
            # Another person's action gets neither: the batch must not read out what it holds.
            "tool_name": (row.get("tool_name") or "") if owned else "",
            "summary": ((row.get("summary") or "")[:SUMMARY_MAX]) if owned else "",
            "status": "Skipped",
            "message": "",
        }
        reason = _skip_reason(row, user, now)
        if not reason and monotonic() - started >= BATCH_TIME_BUDGET_SECONDS:
            budget_reached = True
            reason = _(
                "Skipped: not started, because this request reached its {0}-second time budget. "
                "It is still Pending; run the batch again."
            ).format(BATCH_TIME_BUDGET_SECONDS)
        if reason:
            item["message"] = reason
            counts["skipped"] += 1
            results.append(item)
            continue

        muted = getattr(frappe.flags, "mute_messages", False)
        frappe.flags.mute_messages = True
        try:
            outcome = decide(name)
        except Exception as error:
            frappe.db.rollback()
            item["status"] = "Failed"
            item["message"] = _failure_message(error)
            counts["failed"] += 1
            _log_unexpected_failure(name, error, item["message"])
        else:
            item["status"] = done_status
            log_name = outcome.get("action_log") if isinstance(outcome, dict) else None
            item["message"] = _("Action Log {0}").format(log_name) if log_name else ""
            counts[done_status.lower()] += 1
        finally:
            frappe.flags.mute_messages = muted
        results.append(item)
    # The list view stops on this rather than sending newer actions ahead of the older ones it
    # just had skipped here.
    return {"results": results, **counts, "budget_reached": budget_reached}


def _cancel_and_commit(name):
    outcome = _cancel_one(name)
    frappe.db.commit()  # so a later failure's rollback cannot un-cancel it
    return outcome


@frappe.whitelist(methods=["POST"])
def confirm_actions(names):
    """Confirm and execute up to MAX_BATCH of the session user's own Pending actions.

    ``names`` is a JSON list or a list. Oldest first. Each action goes through ``_confirm_one``,
    exactly as its form's button would send it. Actions that are not the user's own (System
    Managers included), not Pending or expired are skipped with the reason, and one failure
    never stops the rest. So do the ones left when the time budget runs out. Returns
    ``{"results": [...], "executed", "failed", "skipped"}``.
    """
    _require_desk_session()
    return _run_batch(names, _confirm_one, "Executed")


@frappe.whitelist(methods=["POST"])
def cancel_actions(names):
    """Cancel up to MAX_BATCH of the session user's own Pending actions. Nothing runs.

    The same list handling, ownership, skip and time-budget rules as ``confirm_actions``.
    Returns ``{"results": [...], "cancelled", "failed", "skipped"}``.
    """
    _require_desk_session()
    return _run_batch(names, _cancel_and_commit, "Cancelled")
