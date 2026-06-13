"""create_followup_task — the first AI *write* tool (gated).

Creates a ToDo follow-up, optionally linked to a record (Project, Customer,
Opportunity, Maintenance Record, …) and assigned to a user. It is the natural
write companion to the read tools: the Morning Briefing / Call Intelligence /
maintenance tools surface "you should follow up on X", and this lets the
assistant *propose* that follow-up.

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

WRITES — and therefore goes through the AI write-confirmation gate (``_gate.py``):
``create_followup_task`` is listed in the gate's ``APP_MUTATING`` set, so when
AI write gating is ON this tool returns an ``awaiting_user_confirmation``
envelope and an **AI Pending Action** instead of executing; the ToDo is created
only after a human clicks Confirm & Execute in the desk (the gate re-runs this
``execute`` as the confirming user). When gating is OFF it creates the ToDo
immediately (still audited by FAC). Risk is classified **Low** (a create).
"""

from typing import Any

import frappe
from frappe import _
from frappe.utils import nowdate
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._common import require_doc_read

_PRIORITIES = ("Low", "Medium", "High")


class CreateFollowupTask(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "create_followup_task"  # must match module filename
        self.description = (
            "Create a follow-up ToDo reminder, optionally linked to a record and "
            "assigned to a user. Use it to act on a next step surfaced by the "
            "briefing, a call, or a maintenance visit (e.g. 'call the customer "
            "back Thursday', 'order the pump for PROJ-0042'). Provide a clear "
            "'description'; optionally link it to a record with 'reference_doctype' "
            "+ 'reference_name' (e.g. Opportunity / Project / Customer / Contact / "
            "Sapphire Maintenance Record), set 'assign_to' (a User email — defaults "
            "to you), a due 'date' (YYYY-MM-DD), and a 'priority' (Low/Medium/High). "
            "This tool WRITES: when AI write gating is on it returns "
            "status='awaiting_user_confirmation' with an action_id and creates "
            "nothing until a human confirms it in ERPNext — then call "
            "check_ai_pending_action with that action_id to get the created ToDo. "
            "Never claim the task was created from the envelope alone."
        )
        self.category = "Productivity"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "ToDo"
        self.inputSchema = {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "What to follow up on (the ToDo text). Required.",
                },
                "reference_doctype": {
                    "type": "string",
                    "description": "Optional DocType to link the task to (e.g. 'Opportunity', 'Project', 'Customer', 'Contact', 'Sapphire Maintenance Record'). Requires reference_name.",
                },
                "reference_name": {
                    "type": "string",
                    "description": "Optional docname of the linked record. Requires reference_doctype.",
                },
                "assign_to": {
                    "type": "string",
                    "description": "User email to assign the task to. Defaults to the requesting user.",
                },
                "date": {
                    "type": "string",
                    "description": "Optional due date, YYYY-MM-DD.",
                },
                "priority": {
                    "type": "string",
                    "enum": list(_PRIORITIES),
                    "default": "Medium",
                    "description": "Task priority.",
                },
            },
            "required": ["description"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        args = arguments or {}

        description = (args.get("description") or "").strip()
        if not description:
            frappe.throw(_("A non-empty 'description' is required."), frappe.ValidationError)

        # Creating ToDos is gated by the confirming user's own permissions: the
        # write gate re-runs this execute() as that user, so the check binds to
        # whoever clicked Confirm & Execute (not the AI's service identity).
        if not frappe.has_permission("ToDo", "create"):
            frappe.throw(_("You do not have permission to create ToDo records."), frappe.PermissionError)

        reference_doctype = (args.get("reference_doctype") or "").strip() or None
        reference_name = (args.get("reference_name") or "").strip() or None
        if bool(reference_doctype) != bool(reference_name):
            frappe.throw(
                _("Provide both 'reference_doctype' and 'reference_name', or neither."),
                frappe.ValidationError,
            )
        if reference_doctype:
            if not frappe.db.exists("DocType", reference_doctype):
                frappe.throw(_("Unknown DocType: {0}").format(reference_doctype), frappe.ValidationError)
            if not frappe.db.exists(reference_doctype, reference_name):
                frappe.throw(
                    _("{0} {1} does not exist.").format(reference_doctype, reference_name),
                    frappe.ValidationError,
                )
            # Don't let the assistant attach a reminder to a record the user can't see.
            require_doc_read(reference_doctype, reference_name)

        assignee = (args.get("assign_to") or "").strip() or frappe.session.user
        if not frappe.db.exists("User", assignee):
            frappe.throw(_("No such User: {0}").format(assignee), frappe.ValidationError)
        if not frappe.db.get_value("User", assignee, "enabled"):
            frappe.throw(_("User {0} is disabled.").format(assignee), frappe.ValidationError)

        priority = (args.get("priority") or "Medium").title()
        if priority not in _PRIORITIES:
            frappe.throw(
                _("'priority' must be one of {0}.").format(", ".join(_PRIORITIES)),
                frappe.ValidationError,
            )

        due_date = (args.get("date") or "").strip() or None
        if due_date:
            from frappe.utils import getdate

            try:
                due_date = str(getdate(due_date))
            except Exception:
                frappe.throw(_("'date' must be a valid date (YYYY-MM-DD)."), frappe.ValidationError)

        todo = frappe.get_doc(
            {
                "doctype": "ToDo",
                "description": description,
                "status": "Open",
                "priority": priority,
                "date": due_date,
                "allocated_to": assignee,
                "assigned_by": frappe.session.user,
                "reference_type": reference_doctype,
                "reference_name": reference_name,
            }
        )
        # Permissions were checked above; insert without re-deriving them so a
        # user assigning to a colleague isn't blocked by ToDo's owner heuristics.
        todo.insert(ignore_permissions=True)

        return {
            "success": True,
            "todo": todo.name,
            "description": description,
            "allocated_to": assignee,
            "priority": priority,
            "date": due_date,
            "reference": (
                {"doctype": reference_doctype, "name": reference_name} if reference_doctype else None
            ),
            "link": f"/app/todo/{todo.name}",
            "created_on": nowdate(),
        }


__all__ = ["CreateFollowupTask"]
