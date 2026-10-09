"""crew_conflicts — what is double-booked or otherwise wrong on the planners (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

It calls api/planner_conflicts.get_conflicts (Project Planner Phase 6D), the Conflict center's own
list: people double-booked or over their hours, work on a day off or on a personal block, vehicle
and equipment clashes, and missing qualifications, from the planners' shared availability engine
with Google switched off. It carries the planner's role gate, so this module adds no permission of
its own. It changes nothing, and it never returns a personal block's note (a block reads
"Unavailable"), even for a caller allowed to see it: a note is personal and has no business in a
model's context. The fixes the Conflict center offers are for a person on the page, not returned.
"""

import datetime
from typing import Any

from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for

MAX_DAYS = 60
DEFAULT_DAYS = 7
KINDS = ("overbooked", "overlap", "day_off", "blocked", "equipment", "qualification")


def _day(value, name):
	"""(date or None, error or None) for an optional YYYY-MM-DD argument."""
	text = str(value or "").strip()
	if not text:
		return None, None
	try:
		return datetime.date.fromisoformat(text), None
	except ValueError:
		return None, f"{name} must look like 2026-06-14."


def summary(conflict):
	"""One conflict as the tool returns it: what, when, who, the records, and whether it was kept.

	Never the block note, never the fixes."""
	kept = conflict.get("acknowledged")
	return {
		"date": conflict.get("date"),
		"kind": conflict.get("kind"),
		"who": conflict.get("resource_label"),
		"what": conflict.get("message"),
		"records": [
			{"doctype": item.get("doctype"), "name": item.get("name"), "title": item.get("title")}
			for item in conflict.get("items") or []
		],
		"kept": {
			"reason": kept.get("reason"),
			"by": kept.get("by_name") or kept.get("by"),
			"on": kept.get("on"),
		}
		if kept
		else None,
	}


class CrewConflicts(BaseTool):
	def __init__(self):
		super().__init__()
		self.name = "crew_conflicts"  # must match module filename
		self.description = (
			"Answer \"What's double-booked next week?\" from the Project Planner's Conflict center: for a "
			"range of days, everything wrong on the planners, sorted by date: people double-booked or "
			"over their hours, work on a day off or while someone is unavailable, two jobs on one "
			"vehicle or asset, and crews missing a required qualification. Each entry says who, what, "
			"the records involved, and whether someone kept it on purpose (with their reason). Conflicts "
			"kept on purpose are left out unless include_kept is true. Drive times behind 'over by' "
			"figures are estimates. Read-only; it changes nothing. For who could take the work use "
			"crew_who_is_free. Requires the planner's roles (Projects or Maintenance)."
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
					"description": "The first day, YYYY-MM-DD. Omit for today.",
				},
				"end": {
					"type": "string",
					"description": f"The last day, YYYY-MM-DD. Omit for a week. At most {MAX_DAYS} days.",
				},
				"kind": {
					"type": "string",
					"enum": list(KINDS),
					"description": "Only this kind of conflict. Omit for every kind.",
				},
				"include_kept": {
					"type": "boolean",
					"description": "Also list conflicts someone kept on purpose with a reason. Default false.",
				},
			},
			"required": [],
		}

	def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
		from frappe.utils import getdate, nowdate

		from erpnext_enhancements.api.planner_conflicts import get_conflicts

		arguments = arguments or {}
		start, problem = _day(arguments.get("start"), "start")
		if problem:
			return {"success": False, "error": problem}
		start = start or getdate(nowdate())
		end, problem = _day(arguments.get("end"), "end")
		if problem:
			return {"success": False, "error": problem}
		end = end or start + datetime.timedelta(days=DEFAULT_DAYS - 1)
		if end < start:
			return {"success": False, "error": "end is before start."}
		if (end - start).days + 1 > MAX_DAYS:
			return {"success": False, "error": f"Ask about {MAX_DAYS} days or fewer."}
		kind = str(arguments.get("kind") or "").strip() or None
		if kind and kind not in KINDS:
			return {"success": False, "error": f"kind must be one of {', '.join(KINDS)}."}
		include_kept = arguments.get("include_kept") in (True, 1, "1", "true", "True")

		rows = [
			c for c in get_conflicts(start.isoformat(), end.isoformat()) if not kind or c.get("kind") == kind
		]
		kept = [c for c in rows if c.get("acknowledged")]
		shown = rows if include_kept else [c for c in rows if not c.get("acknowledged")]
		return {
			"success": True,
			"start": start.isoformat(),
			"end": end.isoformat(),
			"count": len(shown),
			"kept_hidden": 0 if include_kept else len(kept),
			"conflicts": [summary(c) for c in shown],
		}


__all__ = ["CrewConflicts"]
