# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted feeds for the HR Dashboard widgets.

Training compliance, who is capturing time, recent joiners and leavers, and
upcoming work anniversaries.

Gating is the shared department contract in ``api/dashboard_widgets.py``: HR
roles per ``api/kpi.py`` — ``HR Manager`` and the instance-defined ``HR Team``,
deliberately **not** ``HR User``, which every employee on this site holds — plus
a per-widget toggle defaulting OFF.

The ``hrms`` app is not installed here (no Attendance, Leave or Salary tables;
payroll lives in QuickBooks), so everything reads ``Employee`` plus this app's
own Training and Job Interval doctypes. ``Job Interval`` and ``Timesheet`` are
guarded: the time widget reports "no time captured yet" rather than implying
nobody works here, which is what a bare zero would say before the kiosk rollout.
"""

import frappe
from frappe.utils import add_days, getdate, nowdate

from erpnext_enhancements.api.dashboard_widgets import fetch_all, widget_feed

ROW_LIMIT = 20

# Assignment statuses that still expect work from the learner.
OPEN_TRAINING_STATUSES = ("Not Started", "In Progress", "Awaiting Sign-off", "Overdue")

# Window for "has this person logged any time recently".
TIME_WINDOW_DAYS = 7

# How far back the joiner/leaver list looks, and how far forward anniversaries do.
MOVEMENT_DAYS = 90
ANNIVERSARY_DAYS = 45


def _doctype_exists(doctype):
	return bool(frappe.db.exists("DocType", doctype))


def _days_until(value):
	if not value:
		return None
	return (getdate(value) - getdate(nowdate())).days


@frappe.whitelist()
@widget_feed("HR", "training_compliance")
def get_training_compliance():
	"""Open training assignments that are overdue or due soon, most overdue first.

	Waived and Cancelled assignments are excluded — a waiver is a decision that
	has already been made, and leaving it on a compliance list guarantees the list
	stops being read.
	"""
	today = getdate(nowdate())
	rows = fetch_all(
		"Training Assignment",
		filters={
			"status": ("in", OPEN_TRAINING_STATUSES),
			"due_date": ("<=", add_days(today, 14)),
		},
		fields=["name", "course_title", "course", "status", "due_date", "employee", "user", "learner_type"],
		order_by="due_date asc",
		limit=ROW_LIMIT,
	)

	employee_ids = {r.employee for r in rows if r.employee}
	names = {}
	if employee_ids:
		names = dict(
			fetch_all(
				"Employee",
				filters={"name": ("in", list(employee_ids))},
				fields=["name", "employee_name"],
				as_list=True,
			)
		)

	assignments = []
	overdue = 0
	for r in rows:
		days = _days_until(r.due_date)
		if days is not None and days < 0:
			overdue += 1
		assignments.append(
			{
				"name": r.name,
				"course": r.course_title or r.course or "",
				"learner": names.get(r.employee) or r.user or r.employee or "",
				"status": r.status or "",
				"due_date": str(getdate(r.due_date)) if r.due_date else None,
				"days_until": days,
				"overdue": days is not None and days < 0,
			}
		)

	return {"assignments": assignments, "overdue_count": overdue}


@frappe.whitelist()
@widget_feed("HR", "timesheet_completeness")
def get_timesheet_completeness():
	"""Active employees split by whether they logged any time in the window.

	Reads Job Interval (the time kiosk) and Timesheet together — either counts as
	captured time. Where neither doctype exists, or neither holds a single row in
	the window, the widget says so rather than listing every employee as
	delinquent: before the kiosk rollout that would be a false accusation against
	the whole company.
	"""
	since = add_days(getdate(nowdate()), -TIME_WINDOW_DAYS)
	employees = fetch_all(
		"Employee",
		filters={"status": "Active"},
		fields=["name", "employee_name", "department", "designation"],
		order_by="employee_name asc",
		limit=500,
	)

	logged = set()
	sources = 0

	if _doctype_exists("Job Interval"):
		rows = fetch_all(
			"Job Interval",
			filters={"start_time": (">=", since)},
			fields=["employee"],
			limit=5000,
		)
		sources += len(rows)
		logged.update(r.employee for r in rows if r.employee)

	if _doctype_exists("Timesheet"):
		rows = fetch_all(
			"Timesheet",
			filters={"docstatus": 1, "start_date": (">=", since)},
			fields=["employee"],
			limit=5000,
		)
		sources += len(rows)
		logged.update(r.employee for r in rows if r.employee)

	if not sources:
		return {
			"unavailable": f"No time has been captured in the last {TIME_WINDOW_DAYS} days by any source.",
			"window_days": TIME_WINDOW_DAYS,
		}

	missing = [
		{
			"name": e.name,
			"title": e.employee_name or e.name,
			"department": e.department or "",
			"designation": e.designation or "",
		}
		for e in employees
		if e.name not in logged
	]

	return {
		"missing": missing[:ROW_LIMIT],
		"missing_count": len(missing),
		"logged_count": len([e for e in employees if e.name in logged]),
		"active_count": len(employees),
		"window_days": TIME_WINDOW_DAYS,
	}


@frappe.whitelist()
@widget_feed("HR", "headcount_movement")
def get_headcount_movement():
	"""Active headcount plus who joined and who left inside the window.

	Windows are half-open ``(a, b]`` to match the turnover KPI's convention in
	``kpi_dashboards/snapshots.py``, so a widget row and the KPI above it can
	never disagree about whether a given exit counts.
	"""
	today = getdate(nowdate())
	since = add_days(today, -MOVEMENT_DAYS)

	active = frappe.db.count("Employee", {"status": "Active"})

	joiners = fetch_all(
		"Employee",
		filters={"date_of_joining": ("between", [add_days(since, 1), today])},
		fields=["name", "employee_name", "designation", "department", "date_of_joining", "status"],
		order_by="date_of_joining desc",
		limit=ROW_LIMIT,
	)
	leavers = fetch_all(
		"Employee",
		filters={"status": "Left", "relieving_date": ("between", [add_days(since, 1), today])},
		fields=["name", "employee_name", "designation", "date_of_joining", "relieving_date"],
		order_by="relieving_date desc",
		limit=ROW_LIMIT,
	)

	def tenure_years(start, end):
		if not start or not end:
			return None
		return round((getdate(end) - getdate(start)).days / 365.25, 1)

	return {
		"active": active,
		"window_days": MOVEMENT_DAYS,
		"joined": [
			{
				"name": e.name,
				"title": e.employee_name or e.name,
				"role": e.designation or e.department or "",
				"date": str(getdate(e.date_of_joining)) if e.date_of_joining else None,
			}
			for e in joiners
		],
		"left": [
			{
				"name": e.name,
				"title": e.employee_name or e.name,
				"role": e.designation or "",
				"date": str(getdate(e.relieving_date)) if e.relieving_date else None,
				"tenure_years": tenure_years(e.date_of_joining, e.relieving_date),
			}
			for e in leavers
		],
		"net": len(joiners) - len(leavers),
	}


@frappe.whitelist()
@widget_feed("HR", "people_calendar")
def get_people_calendar():
	"""Upcoming work anniversaries for active employees.

	Anniversaries only — the Employee master on this site does not carry reliable
	dates of birth, and a birthday list that silently omits half the company is
	worse than no birthday list. The year-boundary case is handled by comparing
	this year's and next year's anniversary and taking whichever falls inside the
	window, so late-December joiners still appear in early January.
	"""
	today = getdate(nowdate())
	horizon = add_days(today, ANNIVERSARY_DAYS)

	employees = fetch_all(
		"Employee",
		filters={"status": "Active", "date_of_joining": ("is", "set")},
		fields=["name", "employee_name", "designation", "department", "date_of_joining"],
		limit=500,
	)

	upcoming = []
	for e in employees:
		joined = getdate(e.date_of_joining)
		for year_offset in (0, 1):
			try:
				anniversary = joined.replace(year=today.year + year_offset)
			except ValueError:
				# 29 February on a non-leap year — observe it on the 28th.
				anniversary = joined.replace(year=today.year + year_offset, day=28)
			if today <= anniversary <= horizon:
				upcoming.append(
					{
						"name": e.name,
						"title": e.employee_name or e.name,
						"role": e.designation or e.department or "",
						"date": str(anniversary),
						"days_until": (anniversary - today).days,
						"years": anniversary.year - joined.year,
					}
				)
				break

	upcoming.sort(key=lambda r: r["days_until"])
	return {"upcoming": upcoming[:ROW_LIMIT], "horizon_days": ANNIVERSARY_DAYS}
