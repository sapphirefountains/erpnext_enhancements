"""crew_day_route — one person's route for one day on the Project Planner (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

The person can be named three ways: the Planner Resource name, the resource's name as shown on the
planner, or the user email. The name is resolved here, to a Planner Resource, and the route itself
comes from api/project_planner.get_route, which carries the planner's role gate. The browser Maps
key that get_route returns is removed before the result leaves this module: an MCP client is not a
browser, and the key is billable.
"""

import datetime
from typing import Any

from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for

# The browser key is a billable Google Maps key. It is removed from every route this tool returns.
STRIPPED_KEYS = ("maps_key",)
MAX_OPTIONS = 60


def _resolve_resource(value):
    """(planner resource name, None) for one clear match, else (None, a sentence saying why not).

    Matches the name, the resource name or the user, all as the caller typed them (the database
    compares case-insensitively). When more than one row matches, a single active one wins; anything
    else lists the choices, so a name shared by two people is never guessed.
    """
    import frappe

    value = str(value or "").strip()
    if not value:
        return None, "Give the Planner Resource, the person's full name or their email."

    matches = frappe.get_list(
        "Planner Resource",
        or_filters=[["name", "=", value], ["resource_name", "=", value], ["user", "=", value]],
        fields=["name", "resource_name", "user", "is_active"],
        limit=10,
    )
    if len(matches) == 1:
        return matches[0].name, None
    if len(matches) > 1:
        active = [m for m in matches if m.is_active]
        if len(active) == 1:
            return active[0].name, None
        options = ", ".join(f"{m.resource_name} ({m.name})" for m in matches)
        return None, f"More than one planner resource matches {value}: {options}. Use the resource name."

    people = frappe.get_list(
        "Planner Resource",
        filters={"is_active": 1},
        fields=["name", "resource_name"],
        order_by="resource_name asc",
        limit=MAX_OPTIONS,
    )
    options = ", ".join(f"{p.resource_name} ({p.name})" for p in people) or "none"
    return None, f"No active planner resource matches {value}. Active resources: {options}."


class CrewDayRoute(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "crew_day_route"  # must match module filename
        self.description = (
            "Show one person's route for one day on the Project Planner: the shop, each stop in driving "
            "order with its arrival and departure times, drive minutes and kilometers, which stops have "
            "no location, and whether the day is a long drive. Give the Planner Resource name, the "
            "person's full name or their email, and a date (YYYY-MM-DD). Read-only; it changes no "
            "booking. Requires the planner's roles (Projects or Maintenance)."
        )
        self.category = "Projects"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Planner Resource"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "resource": {
                    "type": "string",
                    "description": "The Planner Resource name (RES-00001), the person's full name, or their email.",
                },
                "date": {
                    "type": "string",
                    "description": "The day, YYYY-MM-DD.",
                },
            },
            "required": ["resource", "date"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from erpnext_enhancements.api.project_planner import get_route

        arguments = arguments or {}
        date = str(arguments.get("date") or "").strip()
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            return {"success": False, "error": "Give the day as YYYY-MM-DD, for example 2026-06-14."}

        resource, problem = _resolve_resource(arguments.get("resource"))
        if problem:
            return {"success": False, "error": problem}

        route = get_route(resource, date)
        return {"success": True, **{k: v for k, v in route.items() if k not in STRIPPED_KEYS}}


__all__ = ["CrewDayRoute"]
