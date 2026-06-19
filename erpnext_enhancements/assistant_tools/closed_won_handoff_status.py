"""closed_won_handoff_status — Closed-Won → Project hand-off queue (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

The hand-off engine (PRO-0204) turns a Closed-Won Opportunity into a Project and
tracks the first three hand-off steps as Project Process Step rows. This tool
reads the *gap*: Opportunities marked 'Closed Won' that have not yet had a
project created (``custom_created_project`` empty), oldest first, with how many
days each has been waiting — plus, for one opportunity, its current hand-off
step state. Read-only; the queue goes through ``frappe.get_list`` so Opportunity
permissions are enforced. To advance a step or create the project, use the desk
(the "Create project now?" prompt) — those are deliberately not exposed here.
"""

from typing import Any

import frappe
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._common import clamp_limit, require_doc_read

_WON_STATUS = "Closed Won"

_QUEUE_FIELDS = ["name", "customer_name", "party_name", "custom_date_closed_won", "opportunity_amount"]


class ClosedWonHandoffStatus(BaseTool):
	def __init__(self):
		super().__init__()
		self.name = "closed_won_handoff_status"  # must match module filename
		self.description = (
			"The Closed-Won → Project hand-off backlog. Returns Opportunities whose "
			"status is 'Closed Won' but which have not yet had a project created "
			"(the hand-off gap), oldest first, each with the days it has been waiting "
			"and its won date, plus the total backlog count. Use it to answer 'which "
			"won deals still need a project', 'what's the oldest un-handed-off "
			"opportunity', or 'how big is the hand-off backlog'. Pass 'opportunity' "
			"to get one deal's current hand-off step state (the first three "
			"opportunity→project steps: their status, who's responsible, and the SLA "
			"due dates). Read-only — creating the project / advancing a step is done "
			"in the desk."
		)
		self.category = "Sales"
		self.source_app = "erpnext_enhancements"
		self.requires_permission = "Opportunity"
		self.inputSchema = {
			"type": "object",
			"properties": {
				"opportunity": {
					"type": "string",
					"description": "An Opportunity name to return its hand-off step state instead of the backlog queue.",
				},
				"limit": {
					"type": "integer",
					"description": "How many backlog opportunities to return (default 20, max 100).",
				},
			},
			"required": [],
		}

	def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
		args = arguments or {}
		opportunity = args.get("opportunity")
		if opportunity:
			return self._detail(opportunity)
		return self._queue(args)

	def _queue(self, args: dict[str, Any]) -> dict[str, Any]:
		limit = clamp_limit(args.get("limit"), 20, 100)
		filters = {"status": _WON_STATUS, "custom_created_project": ["is", "not set"]}

		total = frappe.get_list("Opportunity", filters=filters, fields=["count(name) as count"])
		total_waiting = total[0]["count"] if total else 0

		rows = frappe.get_list(
			"Opportunity",
			filters=filters,
			fields=_QUEUE_FIELDS,
			order_by="custom_date_closed_won asc",
			limit_page_length=limit,
		)
		today = frappe.utils.nowdate()
		for row in rows:
			won = row.get("custom_date_closed_won")
			row["custom_date_closed_won"] = str(won or "")
			row["days_waiting"] = frappe.utils.date_diff(today, won) if won else None

		return {
			"success": True,
			"total_waiting": total_waiting,
			"opportunities": rows,
		}

	def _detail(self, opportunity: str) -> dict[str, Any]:
		if not frappe.db.exists("Opportunity", opportunity):
			return {"success": False, "error": f"Opportunity {opportunity} not found"}
		require_doc_read("Opportunity", opportunity)

		from erpnext_enhancements.crm_enhancements.project_prompt import opportunity_handoff_steps

		opp = (
			frappe.db.get_value(
				"Opportunity",
				opportunity,
				[
					"name",
					"status",
					"customer_name",
					"party_name",
					"custom_created_project",
					"custom_date_closed_won",
				],
				as_dict=True,
			)
			or {}
		)
		opp["custom_date_closed_won"] = str(opp.get("custom_date_closed_won") or "")
		handoff = opportunity_handoff_steps(opportunity) or {}
		return {
			"success": True,
			"opportunity": opp,
			"project": handoff.get("project"),
			"steps": handoff.get("steps") or [],
		}


__all__ = ["ClosedWonHandoffStatus"]
