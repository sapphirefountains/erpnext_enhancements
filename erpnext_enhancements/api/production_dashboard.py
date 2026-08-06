# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted feeds for the Production Dashboard widgets.

Build-floor worklists: what is in flight and ageing, which milestones have
slipped, what material has not landed, and where hours have run past budget.

Gating is the shared department contract in ``api/dashboard_widgets.py``:
Production roles per ``api/kpi.py``, plus a per-widget toggle defaulting OFF.

**Two Project fields need care and are the reason the hours widget computes in
Python rather than SQL:**

* ``custom_total_time_elapsed`` is a **Duration** — seconds, not hours.
* ``custom_time_budget_in_hours`` is a **Data** field — free text that usually
  holds a number of hours but is not guaranteed to parse.

So actual is divided by 3600 before it is compared to budget, and a row whose
budget does not parse to a positive number is skipped rather than treated as a
zero budget (which would rank it as an infinite overrun and push real overruns
off the list).

Like ``_production_metrics`` in the KPI engine, "active" accepts both ``Open``
and ``Active``: the stock Project status is ``Open`` but this site books live
projects as ``Active``.
"""

import frappe
from frappe.utils import flt, getdate, nowdate

from erpnext_enhancements.api.dashboard_widgets import fetch_all, widget_feed

ROW_LIMIT = 20

ACTIVE_STATUSES = ("Open", "Active")
BUILD_PROJECT_TYPE = "Build"

SECONDS_PER_HOUR = 3600.0

# Only flag an overrun once it is past the budget; anything under is on plan.
OVERRUN_PCT = 100.0


def _days_between(earlier, later=None):
	if not earlier:
		return None
	later = later or getdate(nowdate())
	return (getdate(later) - getdate(earlier)).days


def _active_filter():
	"""Project filters for live work, narrowed to Builds where the type field exists."""
	filters = {"status": ("in", ACTIVE_STATUSES)}
	if frappe.db.has_column("Project", "project_type"):
		filters["project_type"] = BUILD_PROJECT_TYPE
	return filters


@frappe.whitelist()
@widget_feed("Production", "wip_aging")
def get_wip_aging():
	"""Active builds, longest-running first, with progress and overdue state."""
	today = getdate(nowdate())
	fields = ["name", "project_name", "customer", "status", "percent_complete", "expected_end_date", "creation"]
	if frappe.db.has_column("Project", "custom_project_dollar_amount"):
		fields.append("custom_project_dollar_amount")

	rows = fetch_all(
		"Project",
		filters=_active_filter(),
		fields=fields,
		order_by="creation asc",
		limit=ROW_LIMIT,
	)

	projects = []
	for r in rows:
		end = r.get("expected_end_date")
		overdue_days = _days_between(end) if end and getdate(end) < today else None
		projects.append(
			{
				"name": r.name,
				"title": r.project_name or r.name,
				"customer": r.customer or "",
				"percent": flt(r.percent_complete),
				"age_days": _days_between(r.creation),
				"end_date": str(getdate(end)) if end else None,
				"overdue_days": overdue_days,
				"amount": flt(r.get("custom_project_dollar_amount")),
			}
		)
	return {"projects": projects}


@frappe.whitelist()
@widget_feed("Production", "milestone_slippage")
def get_milestone_slippage():
	"""Pending project process steps past their due date, most overdue first.

	Joined to Project so the row can name the job rather than just the step, and
	restricted to live projects — an overdue step on a cancelled job is noise.
	"""
	today = getdate(nowdate())
	statuses = ", ".join(["%(st" + str(i) + ")s" for i in range(len(ACTIVE_STATUSES))])
	params = {"st" + str(i): s for i, s in enumerate(ACTIVE_STATUSES)}
	params.update({"today": today, "limit": ROW_LIMIT})

	rows = frappe.db.sql(
		f"""
		select s.name, s.step_title, s.step_number, s.responsible_role, s.due_by,
		       s.parent as project, p.project_name, p.customer
		from `tabProject Process Step` s
		join `tabProject` p on p.name = s.parent and s.parenttype = 'Project'
		where s.status = 'Pending'
		  and s.due_by is not null
		  and s.due_by < %(today)s
		  and p.status in ({statuses})
		order by s.due_by asc
		limit %(limit)s
		""",
		params,
		as_dict=True,
	)

	steps = [
		{
			"project": r.project,
			"project_title": r.project_name or r.project,
			"customer": r.customer or "",
			"title": r.step_title or f"Step {r.step_number}",
			"role": r.responsible_role or "",
			"due_by": str(getdate(r.due_by)) if r.due_by else None,
			"overdue_days": _days_between(r.due_by),
		}
		for r in rows
	]
	return {"steps": steps}


@frappe.whitelist()
@widget_feed("Production", "material_readiness")
def get_material_readiness():
	"""Submitted material requests on live projects that are not fully received.

	Grouped per request rather than per line: the build cares whether the request
	has landed, and a 40-line request would otherwise bury every other job. The
	earliest required-by date across the request's outstanding lines drives the
	late flag.
	"""
	today = getdate(nowdate())
	statuses = ", ".join(["%(st" + str(i) + ")s" for i in range(len(ACTIVE_STATUSES))])
	params = {"st" + str(i): s for i, s in enumerate(ACTIVE_STATUSES)}
	params.update({"limit": ROW_LIMIT})

	rows = frappe.db.sql(
		f"""
		select mr.name, mr.transaction_date, mr.material_request_type, mr.status,
		       mri.project, p.project_name,
		       min(mri.schedule_date) as needed_by,
		       count(*) as line_count,
		       sum(case when coalesce(mri.received_qty, 0) < mri.qty then 1 else 0 end) as open_lines
		from `tabMaterial Request Item` mri
		join `tabMaterial Request` mr on mr.name = mri.parent
		join `tabProject` p on p.name = mri.project
		where mr.docstatus = 1
		  and mr.status not in ('Stopped', 'Cancelled')
		  and p.status in ({statuses})
		  and coalesce(mri.received_qty, 0) < mri.qty
		group by mr.name, mr.transaction_date, mr.material_request_type, mr.status,
		         mri.project, p.project_name
		order by needed_by asc
		limit %(limit)s
		""",
		params,
		as_dict=True,
	)

	requests = [
		{
			"name": r.name,
			"project": r.project,
			"project_title": r.project_name or r.project,
			"type": r.material_request_type or "",
			"status": r.status or "",
			"needed_by": str(getdate(r.needed_by)) if r.needed_by else None,
			"late_days": (
				(today - getdate(r.needed_by)).days
				if r.needed_by and getdate(r.needed_by) < today
				else None
			),
			"open_lines": int(r.open_lines or 0),
			"line_count": int(r.line_count or 0),
		}
		for r in rows
	]
	return {"requests": requests}


@frappe.whitelist()
@widget_feed("Production", "hours_variance")
def get_hours_variance():
	"""Active builds ranked by how far actual hours have run past budget.

	See the module docstring for the two unit traps this navigates: elapsed time
	is stored in seconds and the budget is stored as free text.
	"""
	if not (
		frappe.db.has_column("Project", "custom_time_budget_in_hours")
		and frappe.db.has_column("Project", "custom_total_time_elapsed")
	):
		return {"projects": [], "unavailable": "The project time-budget fields are not installed."}

	rows = fetch_all(
		"Project",
		filters=_active_filter(),
		fields=[
			"name",
			"project_name",
			"customer",
			"percent_complete",
			"custom_time_budget_in_hours",
			"custom_total_time_elapsed",
		],
		order_by="modified desc",
		limit=200,
	)

	projects = []
	for r in rows:
		budget = flt(r.custom_time_budget_in_hours)
		if budget <= 0:
			# No usable budget: nothing to be over or under. Skipping beats
			# ranking it as an infinite overrun.
			continue
		actual = flt(r.custom_total_time_elapsed) / SECONDS_PER_HOUR
		used_pct = actual / budget * 100.0
		projects.append(
			{
				"name": r.name,
				"title": r.project_name or r.name,
				"customer": r.customer or "",
				"budget_hours": round(budget, 1),
				"actual_hours": round(actual, 1),
				"used_pct": round(used_pct, 1),
				"over": used_pct > OVERRUN_PCT,
				"percent_complete": flt(r.percent_complete),
			}
		)

	projects.sort(key=lambda p: p["used_pct"], reverse=True)
	return {"projects": projects[:ROW_LIMIT], "overrun_pct": OVERRUN_PCT}
