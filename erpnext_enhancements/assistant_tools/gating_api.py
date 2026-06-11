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

from erpnext_enhancements.assistant_tools._gate import insert_action_log, truncate_json


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


@frappe.whitelist()
def confirm_action(name):
    """Execute a Pending action as the confirming user and record the outcome.

    The Confirmed status is committed *before* execution (so the decision
    survives a crash); on failure the transaction is rolled back first (the
    tool may have partially written) and the Failed outcome + log row are
    persisted afterwards.
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

    arguments = json.loads(action.arguments or "{}")

    _transition(action, status="Confirmed", decided_by=frappe.session.user, decided_at=now_datetime())
    frappe.db.commit()

    frappe.flags.ai_gate_bypass = True
    frappe.flags.ai_gate_pending = action.name
    try:
        # Re-runs FAC's accessibility + permission checks for the *confirming*
        # user, FAC's own audit logging, and the actual tool.
        result = get_tool_registry().execute_tool(action.tool_name, arguments)
    except Exception as e:
        frappe.db.rollback()  # the tool may have partially written
        action = frappe.get_doc("AI Pending Action", name)  # post-rollback state (Confirmed survived)
        log_name = insert_action_log(
            user=frappe.session.user,
            tool_name=action.tool_name,
            arguments=arguments,
            success=0,
            risk=action.risk,
            summary=action.summary,
            error=str(e),
            error_type=type(e).__name__,
            pending_action=action.name,
        )
        _transition(action, status="Failed", error=str(e)[:2000], action_log=log_name)
        frappe.db.commit()  # persist the Failed outcome before throwing
        frappe.throw(_("Execution failed: {0}").format(str(e)))
    finally:
        frappe.flags.ai_gate_bypass = False
        frappe.flags.ai_gate_pending = None

    log_name = insert_action_log(
        user=frappe.session.user,
        tool_name=action.tool_name,
        arguments=arguments,
        success=1,
        risk=action.risk,
        summary=action.summary,
        result=result,
        pending_action=action.name,
    )
    target_name = action.target_name
    if not target_name and isinstance(result, dict):
        target_name = result.get("name")
    _transition(
        action,
        status="Executed",
        result=truncate_json(result),
        action_log=log_name,
        target_name=target_name,
    )
    frappe.db.commit()
    return {"status": "Executed", "action_log": log_name}


@frappe.whitelist()
def cancel_action(name):
    """Mark a Pending action Cancelled (no execution)."""
    action = frappe.get_doc("AI Pending Action", name)
    _check_identity(action)
    if action.status != "Pending":
        frappe.throw(_("This action is {0} — only Pending actions can be cancelled.").format(action.status))
    _transition(action, status="Cancelled", decided_by=frappe.session.user, decided_at=now_datetime())
    return {"status": "Cancelled"}
