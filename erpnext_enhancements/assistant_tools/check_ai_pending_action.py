"""check_ai_pending_action — retrieve gated-action status/results (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

This is the model's half of the write-confirmation round-trip: a gated
mutation returns an envelope with an ``action_id``; after the human confirms
in the desk, the model calls this tool to fetch the REAL result (or learns the
action was cancelled / expired / failed). Deliberately read-only — there is no
MCP confirm tool (see ``_gate.py`` for why).
"""

import json
from typing import Any

import frappe
from frappe import _
from frappe_assistant_core.core.base_tool import BaseTool


class CheckAiPendingAction(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "check_ai_pending_action"  # must match module filename
        self.description = (
            "Check the status of a gated AI action (an 'AI Pending Action'). When a "
            "write tool returns status awaiting_user_confirmation with an action_id, "
            "the action has NOT executed — the user must confirm it in ERPNext. Call "
            "this tool with that action_id after the user says they confirmed (or to "
            "poll politely) to retrieve the real outcome: Pending (still waiting), "
            "Executed (result included), Failed (error included), Cancelled or "
            "Expired. Without action_id it lists your own still-Pending actions. "
            "Never invent a result for an unexecuted action."
        )
        self.category = "AI Governance"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "AI Pending Action"
        self.inputSchema = {
            "type": "object",
            "properties": {
                "action_id": {
                    "type": "string",
                    "description": "The AI Pending Action id from the confirmation envelope (e.g. AI-PA-2026-00001). Omit to list your own pending actions.",
                },
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        user = frappe.session.user
        action_id = (arguments or {}).get("action_id")

        if not action_id:
            pending = frappe.get_all(
                "AI Pending Action",
                filters={"requested_by": user, "status": "Pending"},
                fields=["name", "tool_name", "summary", "risk", "expires_at", "creation"],
                order_by="creation desc",
                limit_page_length=20,
            )
            return {
                "success": True,
                "pending_actions": [
                    {**row, "expires_at": str(row.expires_at), "creation": str(row.creation)}
                    for row in pending
                ],
            }

        if not frappe.db.exists("AI Pending Action", action_id):
            return {"success": False, "error": f"AI Pending Action {action_id} not found"}

        action = frappe.get_doc("AI Pending Action", action_id)
        if action.requested_by != user and "System Manager" not in frappe.get_roles():
            frappe.throw(
                _("This pending action belongs to another user."), frappe.PermissionError
            )

        payload = {
            "success": True,
            "action_id": action.name,
            "status": action.status,
            "tool_name": action.tool_name,
            "summary": action.summary,
            "risk": action.risk,
            "expires_at": str(action.expires_at or ""),
            "decided_by": action.decided_by,
            "decided_at": str(action.decided_at or ""),
        }
        if action.status == "Executed":
            try:
                payload["result"] = json.loads(action.result or "null")
            except Exception:
                payload["result"] = action.result
        elif action.status == "Failed":
            payload["error"] = action.error
        elif action.status == "Pending":
            payload["note"] = (
                "Still awaiting human confirmation in ERPNext. Do not fabricate a result."
            )
        return payload
