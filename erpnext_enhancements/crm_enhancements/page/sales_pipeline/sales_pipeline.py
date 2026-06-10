"""Server-side logic for the Sales Pipeline page (``/app/sales-pipeline``).

A TV-friendly, realtime funnel view of open Opportunities — the "sales
pipeline on a screen" from the Jun 9 Projects/Invoice-processing meeting.
Columns are the live ``Opportunity.status`` options (read from meta, so a
stage rename on the site reshapes the board without a code change), plus two
special columns:

* **Won — awaiting project**: Closed Won opportunities whose
  ``custom_created_project`` is still empty — the PRO-0204 Step 1 → Step 2
  gap, surfaced on the wall. These age on a much tighter clock than pipeline
  stages (``WON_AMBER_DAYS`` / ``WON_RED_DAYS``).
* **On Hold**: parked deals, rendered muted and never marked stale.

Staleness ("it lights up if it's been sitting too long") is driven by
``custom_stage_changed_on``, stamped by :func:`stamp_stage_change`
(Opportunity ``before_save``); opportunities from before that field shipped
fall back to ``modified`` (and a one-shot patch backfills them, see
``patches/backfill_stage_changed_on``). Thresholds live in **ERPNext
Enhancements Settings → Sales Pipeline Dashboard** (amber/red days).

Access mirrors the Project Dashboard model: this is a shared wall display,
so the gate is page-level, not per-record. If a "Custom Role" record exists
for the ``sales-pipeline`` page, its roles win; otherwise any user holding
one of ``DEFAULT_ROLES`` may view. Data is then fetched permission-free
(``frappe.get_all``) so per-user User Permissions can't silently empty the
board (see the Project Dashboard docstring for the history there).
"""

import frappe
from frappe.utils import cint, date_diff, flt, get_datetime, getdate, now_datetime, nowdate

from erpnext_enhancements.feature_flags import throw_if_process_automation_disabled

# Statuses that never appear on the board.
TERMINAL_STATUSES = {"Lost", "Closed Lost", "Closed", "Converted"}
WON_STATUS = "Closed Won"
PARKED_STATUSES = {"On Hold"}

# Cards rendered per column (totals always cover the full set; the column
# footer shows "+N more"). A TV column taller than this is unreadable anyway.
MAX_CARDS_PER_STAGE = 30

# A won opportunity should have a project within a day or two (the daily
# unconverted nag in status_alerts enforces the same expectation), so the
# won column ages on its own clock instead of the stage thresholds.
WON_AMBER_DAYS = 1
WON_RED_DAYS = 3

DEFAULT_STALE_AMBER_DAYS = 7
DEFAULT_STALE_RED_DAYS = 14

# Hand-off rail: how many in-progress projects show under the board.
HANDOFF_RAIL_LIMIT = 14

# Fallback page-access roles when no Custom Role is configured for the page.
# Deliberately broad — the meeting's intent is office-wide visibility on a
# wall TV. Tighten without a deploy by creating a Custom Role record for the
# "sales-pipeline" page.
DEFAULT_ROLES = {
	"System Manager",
	"Sales Master Manager",
	"Sales Manager",
	"Sales User",
	"Projects Manager",
	"Projects User",
	"Employee",
}


@frappe.whitelist()
def check_permission():
	"""Page-level access check (Custom Role for the page, else DEFAULT_ROLES)."""
	try:
		custom_role = frappe.db.get_value("Custom Role", {"page": "sales-pipeline"}, "name")
		if custom_role:
			permitted = {
				r.role
				for r in frappe.get_all(
					"Has Role", filters={"parent": custom_role, "parenttype": "Custom Role"}, fields=["role"]
				)
			}
		else:
			permitted = DEFAULT_ROLES
		return bool(permitted.intersection(set(frappe.get_roles())))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Sales Pipeline permission check failed")
		return False


def _stale_level(days_in_stage, amber_days, red_days):
	"""0 = fresh, 1 = amber, 2 = red."""
	if red_days > 0 and days_in_stage >= red_days:
		return 2
	if amber_days > 0 and days_in_stage >= amber_days:
		return 1
	return 0


def _thresholds():
	settings = frappe.get_single("ERPNext Enhancements Settings")
	amber = settings.get("pipeline_stale_amber_days")
	red = settings.get("pipeline_stale_red_days")
	amber = DEFAULT_STALE_AMBER_DAYS if amber in (None, "") else cint(amber)
	red = DEFAULT_STALE_RED_DAYS if red in (None, "") else cint(red)
	return amber, red


def _days_since(value):
	"""Whole days from a date/datetime-ish value to today (never negative)."""
	if not value:
		return 0
	try:
		return max(date_diff(nowdate(), getdate(value)), 0)
	except Exception:
		return 0


def _stage_columns():
	"""Status options from live meta, split into open / parked, order preserved."""
	options = (frappe.get_meta("Opportunity").get_field("status").options or "").split("\n")
	options = [opt.strip() for opt in options if opt.strip()]
	open_stages = [
		opt for opt in options if opt not in TERMINAL_STATUSES and opt != WON_STATUS and opt not in PARKED_STATUSES
	]
	parked = [opt for opt in options if opt in PARKED_STATUSES]
	return open_stages, parked


@frappe.whitelist()
def get_pipeline_data():
	"""Everything the board needs in one call."""
	throw_if_process_automation_disabled()
	if not check_permission():
		frappe.throw(frappe._("You do not have permission to view the Sales Pipeline."), frappe.PermissionError)

	open_stages, parked_stages = _stage_columns()
	amber_days, red_days = _thresholds()

	opportunities = frappe.get_all(
		"Opportunity",
		filters={"status": ("in", open_stages + parked_stages + [WON_STATUS])},
		fields=[
			"name",
			"status",
			"customer_name",
			"party_name",
			"opportunity_amount",
			"opportunity_owner",
			"custom_opportunity_summary",
			"custom_stage_changed_on",
			"custom_date_closed_won",
			"custom_created_project",
			"modified",
		],
	)

	owner_ids = {opp.opportunity_owner for opp in opportunities if opp.opportunity_owner}
	owner_names = {}
	if owner_ids:
		owner_names = dict(
			frappe.get_all(
				"User",
				filters={"name": ("in", list(owner_ids))},
				fields=["name", "full_name"],
				as_list=True,
			)
		)

	def make_card(opp, kind):
		if kind == "won":
			basis = opp.custom_date_closed_won or opp.custom_stage_changed_on or opp.modified
			days = _days_since(basis)
			stale = _stale_level(days, WON_AMBER_DAYS, WON_RED_DAYS)
		else:
			days = _days_since(opp.custom_stage_changed_on or opp.modified)
			stale = 0 if kind == "parked" else _stale_level(days, amber_days, red_days)
		return {
			"name": opp.name,
			"customer": opp.customer_name or opp.party_name or opp.name,
			"summary": opp.custom_opportunity_summary or "",
			"amount": flt(opp.opportunity_amount),
			"owner": owner_names.get(opp.opportunity_owner) or opp.opportunity_owner or "",
			"days_in_stage": days,
			"stale": stale,
		}

	def make_column(label, kind, opps):
		cards = sorted((make_card(opp, kind) for opp in opps), key=lambda c: c["days_in_stage"], reverse=True)
		return {
			"label": label,
			"kind": kind,
			"count": len(cards),
			"total": sum(card["amount"] for card in cards),
			"overflow": max(len(cards) - MAX_CARDS_PER_STAGE, 0),
			"opportunities": cards[:MAX_CARDS_PER_STAGE],
		}

	by_status = {}
	won_waiting = []
	for opp in opportunities:
		if opp.status == WON_STATUS:
			if not opp.custom_created_project:
				won_waiting.append(opp)
		else:
			by_status.setdefault(opp.status, []).append(opp)

	columns = [make_column(stage, "open", by_status.get(stage, [])) for stage in open_stages]
	columns.append(make_column(frappe._("Won — awaiting project"), "won", won_waiting))
	columns.extend(make_column(stage, "parked", by_status.get(stage, [])) for stage in parked_stages)

	return {
		"stages": columns,
		"handoff": _handoff_strip(),
		"currency": frappe.defaults.get_global_default("currency") or "USD",
		"thresholds": {"amber": amber_days, "red": red_days},
		"generated_at": str(now_datetime()),
	}


def _handoff_strip():
	"""Active projects with an unfinished PRO-0204 hand-off, overdue first.

	The post-won extension of the funnel: once an opportunity converts, its
	project appears here as "Step N/total — current step" until the process
	completes. Best-effort by design — any failure logs and returns an empty
	rail rather than taking the whole board down.
	"""
	empty = {"projects": [], "overflow": 0}
	try:
		rows = frappe.get_all(
			"Project Process Step",
			filters={"parenttype": "Project"},
			fields=["parent", "step_number", "step_title", "status", "due_by"],
			order_by="parent asc, step_number asc",
		)
		if not rows:
			return empty

		active = {
			p.name: p.project_name or p.name
			for p in frappe.get_all(
				"Project",
				filters={"name": ("in", list({row.parent for row in rows})), "status": "Active"},
				fields=["name", "project_name"],
			)
		}

		by_parent = {}
		for row in rows:
			by_parent.setdefault(row.parent, []).append(row)

		now = now_datetime()
		items = []
		for parent, steps in by_parent.items():
			if parent not in active:
				continue
			current = next((s for s in steps if s.status == "Pending"), None)
			if not current:
				continue
			items.append(
				{
					"project": parent,
					"label": active[parent],
					"step_number": current.step_number,
					"step_title": current.step_title,
					"total": len(steps),
					"overdue": bool(current.due_by and get_datetime(current.due_by) < now),
				}
			)

		items.sort(key=lambda item: (not item["overdue"], item["step_number"]))
		return {
			"projects": items[:HANDOFF_RAIL_LIMIT],
			"overflow": max(len(items) - HANDOFF_RAIL_LIMIT, 0),
		}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Sales Pipeline hand-off rail failed")
		return empty


def stamp_stage_change(doc, method=None):
	"""Opportunity ``before_save`` — timestamp every entry into a new stage.

	Stamps on insert (no before-doc) and whenever ``status`` changes; an
	ordinary edit inside the same stage keeps the clock running. This feeds
	the days-in-stage badge and the amber/red staleness lights.
	"""
	before = doc.get_doc_before_save()
	if before is None or before.status != doc.status:
		doc.custom_stage_changed_on = now_datetime()


def publish_pipeline_update(doc, method=None):
	"""Opportunity ``on_update`` — nudge open boards to refresh (realtime)."""
	frappe.publish_realtime("sales_pipeline_updated", {"opportunity": doc.name})
