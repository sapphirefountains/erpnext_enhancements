# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Crew Utilization: how much of each person's time is booked, and how much they clocked.

Project Planner Phase 4 (P4.3). One row per person per week (weeks begin on the site's first
weekday, System Settings ``first_day_of_the_week``, Sunday on production) or per month:

* **Capacity**: the hours the shared availability engine gives them (work pattern, less holidays
  and time off), the same figure both planners use.
* **Booked**: firm hours, split by what booked them: project tasks, maintenance visits, rental
  crew work, travel and driving (the drive padding the planner adds). Pencilled work is not
  booked; it is not a commitment.
* **Actual**: hours clocked at the kiosk (Job Intervals, net of pauses) on customer jobs, the
  same set the planner shows (Nik, 2026-10-09: Design, Build, Service, Events/Rent, Delivery).
  Clocked time on anything else (internal projects, shop time with no project) is **Other
  clocked**, its own column, never dropped and never mixed in.
* **Utilization %** is booked ÷ capacity and **Actual %** is actual ÷ capacity, None (blank) when
  the person had no capacity that period: a percentage of nothing is not 0 %.

The engine runs once for the whole range with **Google switched off** (``google=False``): drive
times come from the cache or the straight-line estimate, so a month of utilization never fans out
into billable requests. No money is in this report, only hours.

The aggregation (:func:`periods_for`, :func:`aggregate`, :func:`chart`) is pure, so
``tests/test_planner_phase4.py`` runs it without a bench.
"""

import datetime
from collections import defaultdict

import frappe
from frappe import _

from erpnext_enhancements.api import project_planner as api
from erpnext_enhancements.project_enhancements import crew_availability as engine
from erpnext_enhancements.project_enhancements import planner_tracking

#: Longest range one run covers: half a year of every person's days.
MAX_DAYS = 186

#: Booking kind → the booked column it lands in.
KIND_COLUMNS = {
	"task": "project",
	"visit": "maintenance",
	"rental": "rental",
	"travel": "travel",
	"drive": "drive",
}
SPLIT = ("project", "maintenance", "rental", "travel", "drive")
SPLIT_LABELS = {
	"project": "Project",
	"maintenance": "Maintenance",
	"rental": "Rental",
	"travel": "Travel",
	"drive": "Drive",
}
GRANULARITIES = ("Week", "Month")


def _as_date(value):
	return engine._as_date(value)


def month_bounds(day):
	"""``(first, last)`` day of the month holding ``day``."""
	day = _as_date(day)
	first = day.replace(day=1)
	following = (first + datetime.timedelta(days=32)).replace(day=1)
	return first, following - datetime.timedelta(days=1)


def periods_for(start, end, granularity, first_weekday):
	"""``[{"key", "label", "start", "end"}]`` covering ``start``..``end``, each clipped to it.

	Weeks begin on ``first_weekday`` (a weekday number, Monday 0); months on the 1st.
	"""
	start, end = _as_date(start), _as_date(end)
	out = []
	if granularity == "Month":
		cursor = start
		while cursor <= end:
			first, last = month_bounds(cursor)
			out.append(
				{
					"key": str(first),
					"label": f"{first:%b} {first.year}",
					"start": max(first, start),
					"end": min(last, end),
				}
			)
			cursor = last + datetime.timedelta(days=1)
		return out
	cursor = api.week_start(start, first_weekday)
	while cursor <= end:
		last = cursor + datetime.timedelta(days=6)
		out.append(
			{
				"key": str(cursor),
				"label": _("Week of {0}").format(f"{cursor:%b} {cursor.day}"),
				"start": max(cursor, start),
				"end": min(last, end),
			}
		)
		cursor = last + datetime.timedelta(days=1)
	return out


def _percent(part, whole):
	whole = engine._float(whole)
	return round(engine._float(part) / whole * 100, 1) if whole > 0 else None


def aggregate(resources, days, actual, other, periods):
	"""One row per person per period.

	``resources`` are the engine's resource dicts and ``days`` its ``{resource: {date: cell}}``;
	``actual`` and ``other`` are ``{resource: {date: hours}}`` (customer-job and other clocked
	time). Firm bookings only: a tentative booking is in nobody's booked hours.
	"""
	rows = []
	for person in resources or []:
		name = person["name"]
		per_day = (days or {}).get(name) or {}
		for period in periods:
			row = {
				"resource": name,
				"label": person.get("label") or name,
				"group": person.get("group"),
				"period": period["label"],
				"period_start": str(period["start"]),
				"capacity": 0.0,
				"booked": 0.0,
				**{column: 0.0 for column in SPLIT},
				"actual": 0.0,
				"other_clocked": 0.0,
			}
			for day in engine.daterange(period["start"], period["end"]):
				cell = per_day.get(str(day)) or {}
				row["capacity"] += engine._float(cell.get("capacity"))
				row["booked"] += engine._float(cell.get("booked"))
				for booking in cell.get("bookings") or []:
					column = KIND_COLUMNS.get(booking.get("kind"))
					if column and not booking.get("tentative"):
						row[column] += engine._float(booking.get("hours"))
				row["actual"] += engine._float(((actual or {}).get(name) or {}).get(day))
				row["other_clocked"] += engine._float(((other or {}).get(name) or {}).get(day))
			for key in ("capacity", "booked", *SPLIT, "actual", "other_clocked"):
				row[key] = round(row[key], 2)
			row["utilization"] = _percent(row["booked"], row["capacity"])
			row["actual_pct"] = _percent(row["actual"], row["capacity"])
			rows.append(row)
	return rows


def chart(rows):
	"""A stacked bar per person: booked hours over the whole range, split by kind."""
	labels, totals = [], {}
	for row in rows:
		if row["resource"] not in totals:
			labels.append(row["label"])
			totals[row["resource"]] = defaultdict(float)
		for column in SPLIT:
			totals[row["resource"]][column] += row[column]
	if not labels:
		return None
	return {
		"data": {
			"labels": labels,
			"datasets": [
				{"name": _(SPLIT_LABELS[column]), "values": [round(t[column], 2) for t in totals.values()]}
				for column in SPLIT
			],
		},
		"type": "bar",
		"barOptions": {"stacked": 1},
		"fieldtype": "Float",
	}


def columns():
	hours = lambda fieldname, label, width=100: {  # noqa: E731
		"fieldname": fieldname,
		"label": _(label),
		"fieldtype": "Float",
		"precision": 2,
		"width": width,
	}
	return [
		{"fieldname": "label", "label": _("Person"), "fieldtype": "Data", "width": 170},
		{
			"fieldname": "resource",
			"label": _("Planner Resource"),
			"fieldtype": "Link",
			"options": "Planner Resource",
			"hidden": 1,
		},
		{"fieldname": "group", "label": _("Group"), "fieldtype": "Data", "width": 90},
		{"fieldname": "period", "label": _("Period"), "fieldtype": "Data", "width": 120},
		{"fieldname": "period_start", "label": _("From"), "fieldtype": "Date", "hidden": 1},
		hours("capacity", "Capacity"),
		hours("booked", "Booked"),
		hours("project", "Project"),
		hours("maintenance", "Maintenance", 110),
		hours("rental", "Rental"),
		hours("travel", "Travel"),
		hours("drive", "Drive"),
		hours("actual", "Actual (kiosk)", 110),
		hours("other_clocked", "Other clocked", 110),
		{"fieldname": "utilization", "label": _("Utilization %"), "fieldtype": "Percent", "width": 110},
		{"fieldname": "actual_pct", "label": _("Actual %"), "fieldtype": "Percent", "width": 90},
	]


def _range(filters):
	from frappe.utils import getdate, nowdate

	today = getdate(nowdate())
	first, last = month_bounds(today)
	start = getdate(filters.get("from_date")) if filters.get("from_date") else first
	end = getdate(filters.get("to_date")) if filters.get("to_date") else last
	if end < start:
		frappe.throw(_("The end date is before the start date."))
	if (end - start).days + 1 > MAX_DAYS:
		frappe.throw(_("Pick a range of {0} days or less.").format(MAX_DAYS))
	return start, end


def _resources(filters):
	"""None for every active person, else the names the group / person filters leave."""
	if filters.get("resource"):
		return [filters.get("resource")]
	if filters.get("group"):
		return frappe.get_all(
			"Planner Resource",
			filters={"is_active": 1, "resource_group": filters.get("group")},
			pluck="name",
			limit_page_length=0,
		)
	return None


def _rental_tasks(names):
	"""The Task names among ``names`` that are rental crew tasks (they always count as jobs)."""
	names = sorted({n for n in names or () if n})
	if not names:
		return set()
	try:
		if not frappe.db.has_column("Task", "custom_rental_booking"):
			return set()
	except Exception:
		return set()
	return {
		row.get("name")
		for row in frappe.get_all(
			"Task",
			filters={"name": ["in", names]},
			fields=["name", "custom_rental_booking"],
			limit_page_length=0,
		)
		if row.get("custom_rental_booking")
	}


def clocked(intervals, start, end, employee_to_resource, wanted, jobs, rental_tasks, now):
	"""``(actual, other)``: ``{resource: {date: hours}}`` clocked on customer jobs, and the rest.

	An interval counts on its start date, inside ``start``..``end``, for a person in ``wanted``.
	It is a job when its project is in ``jobs`` or its task in ``rental_tasks``.
	"""
	actual = defaultdict(lambda: defaultdict(float))
	other = defaultdict(lambda: defaultdict(float))
	for row in intervals or []:
		day = _as_date(row.get("start_time"))
		resource = (employee_to_resource or {}).get(row.get("employee"))
		if not day or not (start <= day <= end) or resource not in wanted:
			continue
		hours = planner_tracking.interval_hours(row, now)
		is_job = row.get("project") in jobs or row.get("task") in rental_tasks
		(actual if is_job else other)[resource][day] += hours
	return actual, other


def execute(filters=None):
	api._require_planner()
	filters = frappe._dict(filters or {})
	start, end = _range(filters)
	granularity = filters.get("granularity") if filters.get("granularity") in GRANULARITIES else "Week"
	data = engine._compute(start, end, _resources(filters), google=False)
	people = data.get("resources") or []

	intervals = [
		row
		for row in planner_tracking.read_intervals(since=start)
		if _as_date(row.get("start_time")) and _as_date(row.get("start_time")) <= end
	]
	employee_to_resource = {p.get("employee"): p.get("name") for p in people if p.get("employee")}
	jobs = engine.planner_projects({row.get("project") for row in intervals if row.get("project")})
	rentals = _rental_tasks({row.get("task") for row in intervals if row.get("task")})
	actual, other = clocked(
		intervals,
		start,
		end,
		employee_to_resource,
		{p.get("name") for p in people},
		jobs,
		rentals,
		frappe.utils.now_datetime(),
	)
	periods = periods_for(start, end, granularity, api._site_first_weekday())
	rows = aggregate(people, data.get("days"), actual, other, periods)
	return columns(), rows, None, chart(rows)
