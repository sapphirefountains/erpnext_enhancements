"""crew_schedule_suggestions — the best days, and people, to book a project Task by drive time (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

It calls api/project_planner.suggest_dates, which does the work and carries the planner's role
gate, so this module adds no permission of its own. It books nothing: a suggestion becomes a
booking only when a person presses Book on the Project Planner.
"""

import datetime
from typing import Any

from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for

DEFAULT_DAYS = 10
MAX_DAYS = 30


class CrewScheduleSuggestions(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "crew_schedule_suggestions"  # must match module filename
        self.description = (
            "Suggest the best dates (and who) for a project Task by drive time: favors days when a crew "
            "is already near the site, flags days with long driving, and respects each person's free "
            "hours, work pattern, holidays and time off. Read-only; it books nothing."
        )
        self.category = "Projects"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Planner Resource"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The Task name (for example TASK-2026-00123) to find a date for.",
                },
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_DAYS,
                    "default": DEFAULT_DAYS,
                    "description": f"How many days from the start to consider, 1 to {MAX_DAYS}. Default {DEFAULT_DAYS}.",
                },
                "from_date": {
                    "type": "string",
                    "description": "First day to consider, YYYY-MM-DD. Omit to start today; never earlier than today.",
                },
            },
            "required": ["task"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from erpnext_enhancements.api.project_planner import suggest_dates

        arguments = arguments or {}
        task = str(arguments.get("task") or "").strip()
        if not task:
            return {"success": False, "error": "Give the Task name to find dates for."}

        raw_days = arguments.get("days")
        try:
            days = DEFAULT_DAYS if raw_days is None else int(raw_days)
        except (TypeError, ValueError):
            return {"success": False, "error": f"days must be a whole number from 1 to {MAX_DAYS}."}
        if not 1 <= days <= MAX_DAYS:
            return {"success": False, "error": f"days must be from 1 to {MAX_DAYS}."}

        from_date = str(arguments.get("from_date") or "").strip() or None
        if from_date:
            try:
                datetime.date.fromisoformat(from_date)
            except ValueError:
                return {"success": False, "error": "from_date must look like 2026-06-14."}

        result = suggest_dates(task, start=from_date, days=days)
        return {"success": True, **result}


__all__ = ["CrewScheduleSuggestions"]
