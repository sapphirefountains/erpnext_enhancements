"""crew_who_is_free — who has free hours on the Project Planner, day by day (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

It calls api/project_planner.who_is_free, which reads the planners' shared availability engine
(work patterns, holidays, time off, project tasks, maintenance visits, rentals, travel and driving)
and carries the planner's role gate, so this module adds no permission of its own. It books
nothing. Driving is priced without calling Google, so asking costs no Routes calls.
"""

import datetime
from typing import Any

from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for

MAX_DAYS = 31
DEFAULT_HOURS = 1
GROUPS = ("Field", "PM", "Design", "Subcontractor")


def _day(value, name):
    """(date or None, error or None) for an optional YYYY-MM-DD argument."""
    text = str(value or "").strip()
    if not text:
        return None, None
    try:
        return datetime.date.fromisoformat(text), None
    except ValueError:
        return None, f"{name} must look like 2026-06-14."


class CrewWhoIsFree(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "crew_who_is_free"  # must match module filename
        self.description = (
            "Answer \"Who is free on <day> for <n> hours?\" from the Project Planner: for each day from "
            "start to end, the people with at least that many free hours (most free first) and, for "
            "everyone else, why not (a day off, a holiday, travelling, or only so many hours left). It "
            "counts work patterns, holidays, time off, project tasks, maintenance visits, rentals, "
            "travel and driving, exactly as both planners do. For \"What is <person> doing next "
            "week?\" use crew_day_route for each day instead: it lists that person's stops in order. "
            "Read-only; it books nothing. Requires the planner's roles (Projects or Maintenance)."
        )
        self.category = "Projects"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Planner Resource"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "start": {
                    "type": "string",
                    "description": "The first day, YYYY-MM-DD.",
                },
                "end": {
                    "type": "string",
                    "description": f"The last day, YYYY-MM-DD. Omit for one day. At most {MAX_DAYS} days.",
                },
                "hours": {
                    "type": "number",
                    # minimum, not exclusiveMinimum: Gemini validates every tool schema on every
                    # request and supports only a subset of JSON Schema.
                    "minimum": 0.25,
                    "maximum": 24,
                    "default": DEFAULT_HOURS,
                    "description": "How many free hours someone needs that day to count as free. Default 1.",
                },
                "group": {
                    "type": "string",
                    "enum": list(GROUPS),
                    "description": "Only people in this Planner Resource group. Omit for everyone.",
                },
            },
            "required": ["start"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from erpnext_enhancements.api.project_planner import who_is_free

        arguments = arguments or {}
        start, problem = _day(arguments.get("start"), "start")
        if problem:
            return {"success": False, "error": problem}
        if not start:
            return {"success": False, "error": "Give the first day as YYYY-MM-DD, for example 2026-06-14."}
        end, problem = _day(arguments.get("end"), "end")
        if problem:
            return {"success": False, "error": problem}
        end = end or start
        if end < start:
            return {"success": False, "error": "end is before start."}
        if (end - start).days + 1 > MAX_DAYS:
            return {"success": False, "error": f"Ask about {MAX_DAYS} days or fewer."}

        raw_hours = arguments.get("hours")
        try:
            hours = DEFAULT_HOURS if raw_hours in (None, "") else float(raw_hours)
        except (TypeError, ValueError):
            return {"success": False, "error": "hours must be a number of hours, for example 4."}
        if not 0 < hours <= 24:
            return {"success": False, "error": "hours must be more than 0 and at most 24."}

        group = str(arguments.get("group") or "").strip() or None
        if group and group not in GROUPS:
            return {"success": False, "error": f"group must be one of {', '.join(GROUPS)}."}

        result = who_is_free(start.isoformat(), end=end.isoformat(), hours=hours, group=group)
        return {"success": True, **result}


__all__ = ["CrewWhoIsFree"]
