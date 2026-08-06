"""training_compliance_status — who owes what training, and what is overdue (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

Answers the question a manager actually asks — *"is my team current?"* — without
them opening a report. Deliberately returns the **exceptions first** (overdue,
then due soon) rather than a full roster, because a tool that answers
"everybody is fine" with two hundred rows gets ignored.

One thing this tool will not do, and it matters: it never reports a **watch
coverage** figure on its own. Coverage measures time elapsed with a video playing
and visible, not attention — somebody can start a lesson and walk away. Reported
beside a pass it is context; reported alone it reads as proof of engagement,
which it is not. Scores and completions are what this returns.
"""

from typing import Any

import frappe

# Only date_diff/nowdate: the assistant-tool contract test stubs frappe.utils
# narrowly, so importing anything more exotic here fails at import time and takes
# the whole tool registry down with it.
from frappe.utils import date_diff, nowdate
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._common import clamp_limit
from erpnext_enhancements.assistant_tools._gate import annotations_for

# Statuses that still represent work outstanding, mirroring
# training/doctype/training_assignment/training_assignment.py.
OPEN_STATUSES = ("Not Started", "In Progress", "Awaiting Sign-off", "Overdue")


class TrainingComplianceStatus(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "training_compliance_status"  # must match module filename
        self.description = (
            "Training assignments across the team: who is overdue, who is due soon, and "
            "per-course completion counts. Filter by employee, course or department. "
            "Returns overdue first, then due within due_days, then a per-course summary. "
            "Use this to answer 'is my team current on their training' or 'who still "
            "owes the safety course'. Use training_learner_record for one person's full "
            "history including scores. Note: this reports assignments and completions, "
            "not video watch coverage — coverage measures time spent, not attention, and "
            "is misleading on its own."
        )
        self.category = "Training"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Training Assignment"
        self.annotations = annotations_for(self.name)
        self.default_config = {"max_rows": 500}
        self.inputSchema = {
            "type": "object",
            "properties": {
                "employee": {
                    "type": "string",
                    "description": "Limit to one Employee (docname, e.g. 'HR-EMP-00001')",
                },
                "course": {
                    "type": "string",
                    "description": "Limit to one Training Course (docname, e.g. 'TRN-CRS-00001')",
                },
                "department": {
                    "type": "string",
                    "description": "Limit to employees in one Department (docname)",
                },
                "due_days": {
                    "type": "integer",
                    "default": 14,
                    "description": "Horizon in days for the 'due soon' list",
                },
                "include_completed": {
                    "type": "boolean",
                    "default": False,
                    "description": "Also return completed assignments (off by default: the "
                    "useful answer is normally the exceptions)",
                },
                "limit": {
                    "type": "integer",
                    "default": 200,
                    "description": "Maximum assignment rows to return",
                },
            },
            "required": [],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not frappe.db.exists("DocType", "Training Assignment"):
            return {"error": "The Training module is not installed on this site."}

        limit = clamp_limit(arguments.get("limit"), 200, self.default_config["max_rows"])
        try:
            due_days = int(arguments.get("due_days") or 14)
        except (TypeError, ValueError):
            due_days = 14

        filters = {}
        if arguments.get("course"):
            filters["course"] = arguments["course"]
        if arguments.get("employee"):
            filters["employee"] = arguments["employee"]
        elif arguments.get("department"):
            # Resolved to employees rather than joined, so the department filter
            # behaves the same whether or not an assignment carries one.
            employees = frappe.get_all(
                "Employee",
                filters={"department": arguments["department"], "status": "Active"},
                pluck="name",
            )
            if not employees:
                return {"assignments": [], "overdue": [], "due_soon": [], "by_course": []}
            filters["employee"] = ["in", employees]

        if not arguments.get("include_completed"):
            filters["status"] = ["in", OPEN_STATUSES]

        rows = frappe.get_all(
            "Training Assignment",
            filters=filters,
            fields=[
                "name", "course", "course_title", "user", "employee",
                "status", "due_date", "assigned_on", "assignment_source",
            ],
            order_by="due_date asc",
            limit_page_length=limit,
        )

        today = nowdate()
        names = {
            e.name: e.employee_name
            for e in frappe.get_all(
                "Employee",
                filters={"name": ["in", [r.employee for r in rows if r.employee] or [""]]},
                fields=["name", "employee_name"],
            )
        }

        overdue, due_soon = [], []
        for row in rows:
            row["employee_name"] = names.get(row.employee) or row.user
            if row.due_date:
                row["days_until_due"] = date_diff(row.due_date, today)
                if row["days_until_due"] < 0:
                    overdue.append(row)
                elif row["days_until_due"] <= due_days:
                    due_soon.append(row)

        return {
            "as_of": today,
            "overdue": overdue,
            "due_soon": due_soon,
            "assignments": rows,
            "by_course": self._by_course(filters.get("course")),
            "note": (
                "Completion here means the course was passed. It does not imply anyone "
                "watched attentively — see the tool description."
            ),
        }

    # ------------------------------------------------------------------ helpers

    def _by_course(self, course=None):
        """Per-course counts: assigned vs completed vs overdue."""
        filters = {"course": course} if course else {}
        summary = {}
        for row in frappe.get_all(
            "Training Assignment",
            filters=filters,
            fields=["course", "course_title", "status"],
            limit_page_length=0,
        ):
            entry = summary.setdefault(
                row.course,
                {"course": row.course, "course_title": row.course_title,
                 "assigned": 0, "completed": 0, "overdue": 0, "outstanding": 0},
            )
            entry["assigned"] += 1
            if row.status == "Completed":
                entry["completed"] += 1
            elif row.status == "Overdue":
                entry["overdue"] += 1
                entry["outstanding"] += 1
            elif row.status in OPEN_STATUSES:
                entry["outstanding"] += 1
        return sorted(summary.values(), key=lambda e: -e["overdue"])
