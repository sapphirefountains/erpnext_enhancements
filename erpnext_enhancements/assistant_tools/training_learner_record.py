"""training_learner_record — one person's training history (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

The question behind this is usually operational rather than administrative:
*"can I send this technician to that job?"* So the answer leads with what they
currently hold and what has lapsed, and the full history sits underneath.

Two things it is deliberately careful about:

* **A completion is pinned to the course version that was passed.** Answering
  "are they trained on this" with a bare yes hides the case where the course has
  since materially changed and their pass was superseded. That distinction is
  surfaced, not flattened.
* **Watch coverage is never returned on its own.** It measures time elapsed with
  a video playing and visible, not attention. Beside a score it is context;
  alone it reads as proof of engagement, which it is not.
"""

from typing import Any

import frappe
from frappe.utils import date_diff, nowdate
from frappe_assistant_core.core.base_tool import BaseTool

OPEN_STATUSES = ("Not Started", "In Progress", "Awaiting Sign-off", "Overdue")


class TrainingLearnerRecord(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "training_learner_record"  # must match module filename
        self.description = (
            "One person's training record: what they have completed and when, which "
            "certifications are current versus expired or superseded, what is still "
            "assigned, and their quiz scores. Accepts an employee docname, a user email, "
            "or a person's name. Use this before dispatching somebody to a job that needs "
            "a certification, or when asked 'is X trained on Y'. Use "
            "training_compliance_status for the whole team rather than one person. A "
            "completion is tied to the exact course version passed, so a course that has "
            "materially changed since shows as superseded rather than current."
        )
        self.category = "Training"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Training Assignment"
        self.default_config = {}
        self.inputSchema = {
            "type": "object",
            "properties": {
                "employee": {
                    "type": "string",
                    "description": "Employee docname, user email, or the person's name",
                },
                "course": {
                    "type": "string",
                    "description": "Optionally narrow to one Training Course (docname)",
                },
            },
            "required": ["employee"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not frappe.db.exists("DocType", "Training Assignment"):
            return {"error": "The Training module is not installed on this site."}

        who = (arguments.get("employee") or "").strip()
        if not who:
            return {"error": "Give an employee docname, a user email, or a name."}

        employee, user = self._resolve(who)
        if not user:
            return {"error": f"Could not identify a learner from {who!r}."}

        course = arguments.get("course")
        return {
            "learner": {
                "employee": employee,
                "user": user,
                "employee_name": frappe.db.get_value("Employee", employee, "employee_name")
                if employee
                else frappe.db.get_value("User", user, "full_name"),
            },
            "current_certifications": self._completions(user, course, current_only=True),
            "lapsed_or_superseded": self._completions(user, course, current_only=False),
            "outstanding": self._outstanding(user, course),
            "note": (
                "Scores and completions only. Watch coverage is not reported here: it "
                "measures time with a video playing, not attention."
            ),
        }

    # ------------------------------------------------------------------ helpers

    def _resolve(self, who):
        """Employee + user for a docname, an email, or a person's name.

        Tolerant on purpose — an operator asking about "Lian" should not have to
        know an Employee docname to get an answer.
        """
        if frappe.db.exists("Employee", who):
            return who, frappe.db.get_value("Employee", who, "user_id")

        row = frappe.db.get_value("Employee", {"user_id": who}, ["name", "user_id"], as_dict=True)
        if row:
            return row.name, row.user_id

        matches = frappe.get_all(
            "Employee",
            filters={"employee_name": ["like", f"%{who}%"], "status": "Active"},
            fields=["name", "user_id"],
            limit=2,
        )
        # Refuse an ambiguous name rather than guessing — answering about the wrong
        # technician is worse than answering nothing.
        if len(matches) == 1:
            return matches[0].name, matches[0].user_id

        if frappe.db.exists("User", who):
            return frappe.db.get_value("Employee", {"user_id": who}, "name"), who
        return None, None

    def _completions(self, user, course, current_only):
        if not frappe.db.exists("DocType", "Training Completion"):
            return []
        filters = {"user": user, "docstatus": 1}
        if course:
            filters["course"] = course
        rows = frappe.get_all(
            "Training Completion",
            filters=filters,
            fields=[
                "name", "course", "course_title_snapshot", "course_version",
                "version_number", "score_percent", "completed_on", "expires_on", "status",
            ],
            order_by="completed_on desc",
            limit_page_length=0,
        )
        today = nowdate()
        out = []
        for row in rows:
            is_current = row.status == "Valid"
            if row.expires_on:
                row["days_until_expiry"] = date_diff(row.expires_on, today)
                if row["days_until_expiry"] < 0:
                    is_current = False
            if is_current == bool(current_only):
                out.append(row)
        return out

    def _outstanding(self, user, course):
        filters = {"user": user, "status": ["in", OPEN_STATUSES]}
        if course:
            filters["course"] = course
        rows = frappe.get_all(
            "Training Assignment",
            filters=filters,
            fields=["name", "course", "course_title", "status", "due_date", "assignment_source"],
            order_by="due_date asc",
            limit_page_length=0,
        )
        today = nowdate()
        for row in rows:
            if row.due_date:
                row["days_until_due"] = date_diff(row.due_date, today)
        return rows
