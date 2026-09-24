"""Whitelisted confirm / cancel endpoints for AI Pending Actions.

Called from the AI Pending Action form buttons by dotted path
(``frappe.call`` resolves them at request time — no Python import from app
code, keeping the FAC-optional tripwire green). Confirmation is desk-only by
design: there is no MCP-exposed confirm tool (see ``_gate.py``).

FAC imports happen inside function bodies so this module imports cleanly on
FAC-less sites (and under the bench-free test stubs); calling confirm there
fails with a clear message instead of an ImportError traceback.
"""

import json

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from erpnext_enhancements.assistant_tools._gate import (
    SealError,
    has_unrestored_redaction,
    insert_action_log,
    mask_secrets,
    truncate_json,
    unseal_arguments,
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


@frappe.whitelist()
def confirm_action(name):
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


@frappe.whitelist()
def cancel_action(name):
    """Mark a Pending action Cancelled (no execution)."""
    action = frappe.get_doc("AI Pending Action", name)
    _check_identity(action)
    if action.status != "Pending":
        frappe.throw(_("This action is {0} — only Pending actions can be cancelled.").format(action.status))
    _transition(action, status="Cancelled", decided_by=frappe.session.user, decided_at=now_datetime())
    return {"status": "Cancelled"}
