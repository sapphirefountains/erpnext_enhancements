# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Project Planner Phase 4, tracking: what was actually worked, and what the labor will cost.

* **Actuals (P4.1)** come from the kiosk's **Job Intervals** (workforce): per task, per person,
  hours = (end, or now for one still Open, or the pause for one Paused) − start − paused time.
  Read in bulk for the tasks on screen. A task is **running over** (``over_plan``) when it is not
  finished and its actual hours exceed its planned hours by more than 10 %: planned is the task's
  ``expected_time``, or the engine's allocated total when it has no estimate.
* **The labor forecast (P4.2)** for a project is the hours booked from today on (the shared engine,
  ``google=False``: no paid drive-time requests) plus the hours already clocked, each at the
  person's pay rate on that day. Today is counted once: what is booked today counts only beyond
  what has already been clocked today.
* **Rates** are the latest ``Employee Pay Rate`` row in force on the day (Employee
  ``custom_pay_rates``, permlevel 1): ``hourly_rate`` for Hourly, ``hourly_equivalent`` (else
  salary ÷ 2080) for Salaried, × (1 + ``burden_pct`` ÷ 100) when a burden is set. With no burden
  the **base rate** is used and the answer says so (``burdened: False``): the burdened rates of
  WI-016/017 are still with the payroll firm, and Time Kiosk Settings' default burden is
  deliberately not applied here. A subcontractor, or anybody without an Employee or a rate, has no
  rate: their hours are listed and costed at nothing ("no rate").

**Who sees money.** A per-person cost gives away a wage, and so does a project total with one
person's labor in it. So cost figures go only to :data:`COST_ROLES` (intersected with the roles
that exist on the site); everybody else gets hours and nothing else: no rate, no cost, no total,
and the pay rates are not even read for them. The stored ``Project Budget Line.labor_forecast`` is
at **permlevel 2**, granted to the same roles by the ``grant_labor_forecast_visibility`` patch: on
Project, permlevel 1 is read by ``Desk User`` (ERPNext v16's standard Project permissions), which
is every desk user, so level 1 would hide nothing.

**Customer jobs only.** Every reader here goes through the engine's rule (Nik, 2026-10-09:
``crew_availability.PLANNER_PROJECT_TYPES``), so actuals are reported for customer-job tasks only.
The labor forecast of a project counts the clocked hours on it whatever its type: it is a cost
forecast for the project's own budget, not a planner surface.

The pure helpers take plain values so ``tests/test_planner_phase4.py`` runs them without a bench.
"""

import datetime
from collections import defaultdict

import frappe
from frappe import _

from erpnext_enhancements.project_enhancements import crew_availability as engine

#: The roles that may see labor cost: a per-person figure reveals a wage. Intersected with the
#: roles that exist on the site (:func:`cost_roles`).
COST_ROLES = frozenset({"System Manager", "Finance Team", "Executive Team", "Estimator", "HR Manager"})

#: Running over: actual hours beyond planned × this, on a task that is not finished.
OVER_PLAN_FACTOR = 1.10

#: Annual hours behind a salaried person's hourly equivalent (40 h × 52 weeks), as in
#: ``workforce.costing``.
ANNUAL_HOURS = 2080.0

#: How far ahead booked hours are forecast: to the project's last open task, at most this far.
FORECAST_MAX_DAYS = 180

#: The engine booking kinds that are labor on a project (driving has no project).
LABOR_KINDS = ("task", "rental", "visit", "travel")

INTERVAL_DOCTYPE = "Job Interval"
PAY_RATE_DOCTYPE = "Employee Pay Rate"
INTERVAL_FIELDS = (
	"name",
	"employee",
	"project",
	"task",
	"start_time",
	"end_time",
	"status",
	"total_paused_seconds",
	"last_pause_time",
)
PAY_RATE_FIELDS = (
	"parent",
	"effective_from",
	"pay_type",
	"hourly_rate",
	"annual_salary",
	"hourly_equivalent",
	"burden_pct",
)
RATE_LABEL_BURDENED = "burdened rate"
RATE_LABEL_BASE = "base rate"

#: The key prefix for a person who clocked time but is not a Planner Resource.
EMPLOYEE_KEY = "employee:"


# ---------------------------------------------------------------------- pure helpers


def _float(value):
	return engine._float(value)


def interval_hours(row, now):
	"""Worked hours of one Job Interval row: (end − start − paused) / 3600, never negative.

	An interval with no end is measured to ``now`` while Open, and to its ``last_pause_time``
	while Paused (the running pause is not yet in ``total_paused_seconds``). A Completed interval
	with no end time cannot be measured and counts nothing.
	"""
	start = engine._as_datetime(row.get("start_time"))
	if not start:
		return 0.0
	end = engine._as_datetime(row.get("end_time"))
	if not end:
		if row.get("status") == "Paused" and row.get("last_pause_time"):
			end = engine._as_datetime(row.get("last_pause_time"))
		elif row.get("status") in ("Open", "Paused"):
			end = engine._as_datetime(now)
	if not end:
		return 0.0
	seconds = (end - start).total_seconds() - _float(row.get("total_paused_seconds"))
	return round(max(seconds, 0.0) / 3600.0, 4)


def person_key(employee, employee_to_resource):
	"""The Planner Resource clocking as ``employee``, else ``"employee:<employee>"``."""
	resource = (employee_to_resource or {}).get(employee)
	return resource or (f"{EMPLOYEE_KEY}{employee}" if employee else None)


def actuals_by_task(intervals, now, employee_to_resource):
	"""``{task: {person key: hours}}`` from Job Interval rows (see :func:`person_key`)."""
	out = defaultdict(lambda: defaultdict(float))
	for row in intervals or []:
		key = person_key(row.get("employee"), employee_to_resource)
		if not row.get("task") or not key:
			continue
		out[row["task"]][key] += interval_hours(row, now)
	return {task: {key: round(hours, 2) for key, hours in people.items()} for task, people in out.items()}


def planned_total(task, booked):
	"""A task's planned hours: its estimate, else what the engine allocated its crew."""
	expected = _float((task or {}).get("expected_time"))
	if expected > 0:
		return round(expected, 2)
	return round(sum(_float(h) for h in (booked or {}).values()), 2)


def is_over_plan(planned, actual, status):
	"""Running over: not finished, something planned, and actual > planned × 1.10."""
	if status in engine.FINISHED_STATUSES:
		return False
	planned, actual = _float(planned), _float(actual)
	return planned > 0 and actual - planned * OVER_PLAN_FACTOR > engine.TOLERANCE


def rate_on(rows, day):
	"""``{"rate", "burdened"}`` from the pay-rate row in force on ``day``, or None.

	The row with the latest ``effective_from`` on or before ``day`` (a future row is ignored).
	Hourly: ``hourly_rate``; Salaried: ``hourly_equivalent``, else ``annual_salary`` ÷ 2080. A
	burden is applied only when it is set (> 0: a blank Percent reads back as 0); otherwise the
	base rate comes back with ``burdened: False``. A row with no usable amount is no rate.
	"""
	day = engine._as_date(day)
	chosen = None
	for row in rows or []:
		effective = engine._as_date(row.get("effective_from"))
		if not effective or (day and effective > day):
			continue
		if chosen is None or effective > engine._as_date(chosen.get("effective_from")):
			chosen = row
	if chosen is None:
		return None
	if (chosen.get("pay_type") or "Hourly") == "Salaried":
		rate = _float(chosen.get("hourly_equivalent")) or (
			_float(chosen.get("annual_salary")) / ANNUAL_HOURS
			if _float(chosen.get("annual_salary")) > 0
			else 0.0
		)
	else:
		rate = _float(chosen.get("hourly_rate"))
	if rate <= 0:
		return None
	burden = _float(chosen.get("burden_pct"))
	if burden > 0:
		return {"rate": round(rate * (1 + burden / 100.0), 4), "burdened": True}
	return {"rate": round(rate, 4), "burdened": False}


def remaining_today(booked_today, actual_today):
	"""What today's booking adds beyond what has already been clocked today (never negative)."""
	return round(max(_float(booked_today) - _float(actual_today), 0.0), 2)


def forecast_person(booked_days, actual_days, today, rate_of=None):
	"""One person's forecast: ``{booked, actual, booked_cost, actual_cost, rate, burdened, no_rate}``.

	``booked_days``/``actual_days`` are ``{date: hours}``. Booked counts the days after ``today``,
	plus :func:`remaining_today` for today; actual counts every clocked day. ``rate_of(day)`` gives
	:func:`rate_on`'s answer (None: no rate); without ``rate_of`` no cost is worked out at all. The
	``rate`` reported is today's, else the latest one used.
	"""
	today = engine._as_date(today)
	booked = {}
	for day, hours in (booked_days or {}).items():
		day = engine._as_date(day)
		if day > today:
			booked[day] = _float(hours)
		elif day == today:
			booked[day] = remaining_today(
				hours, (actual_days or {}).get(day) or (actual_days or {}).get(str(day))
			)
	actual = {engine._as_date(day): _float(hours) for day, hours in (actual_days or {}).items()}
	out = {
		"booked": round(sum(booked.values()), 2),
		"actual": round(sum(actual.values()), 2),
	}
	if rate_of is None:
		return out
	booked_cost = actual_cost = 0.0
	used, burdened = [], []
	for days, cost_key in ((booked, "booked"), (actual, "actual")):
		for day, hours in days.items():
			if hours <= 0:
				continue
			rate = rate_of(day)
			if not rate:
				continue
			used.append((day, rate))
			burdened.append(bool(rate.get("burdened")))
			if cost_key == "booked":
				booked_cost += hours * rate["rate"]
			else:
				actual_cost += hours * rate["rate"]
	current = rate_of(today) or (max(used, key=lambda pair: pair[0])[1] if used else None)
	out.update(
		booked_cost=round(booked_cost, 2),
		actual_cost=round(actual_cost, 2),
		rate=round(current["rate"], 2) if current else None,
		burdened=(all(burdened) if burdened else (bool(current["burdened"]) if current else None)),
		no_rate=current is None,
	)
	return out


def forecast_answer(project, people, labels, can_see_cost, through=None):
	"""The :func:`get_labor_forecast` answer from :func:`forecast_person` results.

	``people`` is ``{person key: forecast_person(...)}``. Hours always; for ``can_see_cost`` also
	the costs, each person's rate and cost, and ``rate_label`` ("burdened rate" or "base rate").
	Without it there is no money anywhere in the answer, not even a total: one person's labor in a
	total is their wage. ``burdened`` is None for a caller who cannot see cost (nothing was read).
	"""
	rows = []
	for key, person in people.items():
		if person["booked"] <= 0 and person["actual"] <= 0:
			continue
		row = {
			"label": (labels or {}).get(key) or key,
			"booked": person["booked"],
			"actual": person["actual"],
		}
		if can_see_cost:
			priced = person.get("rate") is not None
			row.update(
				rate=person.get("rate"),
				cost=round(person.get("booked_cost", 0) + person.get("actual_cost", 0), 2)
				if priced
				else None,
				burdened=person.get("burdened"),
				no_rate=not priced,
			)
		rows.append(row)
	rows.sort(key=lambda r: (str(r["label"]).lower(), str(r["label"])))
	answer = {
		"project": project,
		"through": str(through) if through else None,
		"booked_hours": round(sum(r["booked"] for r in rows), 2),
		"actual_hours": round(sum(r["actual"] for r in rows), 2),
		"by_person": rows,
		"can_see_cost": bool(can_see_cost),
		"burdened": None,
	}
	if can_see_cost:
		booked_cost = round(sum(p.get("booked_cost", 0) for p in people.values()), 2)
		actual_cost = round(sum(p.get("actual_cost", 0) for p in people.values()), 2)
		flags = [p.get("burdened") for p in people.values() if p.get("burdened") is not None]
		burdened = all(flags) if flags else None
		answer.update(
			booked_cost=booked_cost,
			actual_cost=actual_cost,
			forecast_cost=round(booked_cost + actual_cost, 2),
			burdened=burdened,
			rate_label=RATE_LABEL_BURDENED if burdened else RATE_LABEL_BASE,
			no_rate=[r["label"] for r in rows if r.get("no_rate")],
		)
	return answer


# ---------------------------------------------------------------------- who sees money


def cost_roles():
	""":data:`COST_ROLES` that exist on this site."""
	out = set()
	for role in sorted(COST_ROLES):
		try:
			if frappe.db.exists("Role", role):
				out.add(role)
		except Exception:
			continue
	return out


def can_see_cost(user=None):
	"""True for Administrator and for a holder of one of :func:`cost_roles`."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(cost_roles() & set(frappe.get_roles(user)))


# ---------------------------------------------------------------------- readers


def _installed(doctype):
	try:
		return bool(frappe.db.exists("DocType", doctype))
	except Exception:
		return False


def read_intervals(tasks=None, project=None, since=None):
	"""Job Interval rows for ``tasks`` (names), or for ``project``, or started on/after ``since``.

	``since`` is a ``>=`` filter, which never matches a blank start; anything finer (the end of a
	range) is decided by the caller in Python.
	"""
	if not _installed(INTERVAL_DOCTYPE):
		return []
	filters = {}
	if tasks is not None:
		names = [n for n in dict.fromkeys(tasks) if n]
		if not names:
			return []
		filters["task"] = ["in", names]
	if project:
		filters["project"] = project
	if since:
		filters["start_time"] = [">=", f"{engine._as_date(since)} 00:00:00"]
	if not filters:
		return []
	return frappe.get_all(
		INTERVAL_DOCTYPE,
		filters=filters,
		fields=list(INTERVAL_FIELDS),
		order_by="start_time asc",
		limit_page_length=0,
	)


def resource_index():
	"""``(employee_to_resource, labels, kinds)`` for every Planner Resource, active or not."""
	employee_to_resource, labels, kinds = {}, {}, {}
	for row in frappe.get_all(
		"Planner Resource",
		fields=["name", "resource_name", "employee", "resource_type", "is_active"],
		limit_page_length=0,
	):
		labels[row.get("name")] = row.get("resource_name") or row.get("name")
		kinds[row.get("name")] = {"employee": row.get("employee"), "type": row.get("resource_type")}
		if row.get("employee") and (
			row.get("employee") not in employee_to_resource or engine._float(row.get("is_active"))
		):
			employee_to_resource[row.get("employee")] = row.get("name")
	return employee_to_resource, labels, kinds


def employee_labels(employees):
	"""``{"employee:EMP": employee name}`` for people who clocked time but are not resources."""
	employees = sorted({e for e in employees or () if e})
	if not employees:
		return {}
	return {
		f"{EMPLOYEE_KEY}{row.get('name')}": row.get("employee_name") or row.get("name")
		for row in frappe.get_all(
			"Employee",
			filters={"name": ["in", employees]},
			fields=["name", "employee_name"],
			limit_page_length=0,
		)
	}


def pay_rate_rows(employees):
	"""``{employee: [pay-rate rows]}``. Only ever called for a caller who may see cost."""
	employees = sorted({e for e in employees or () if e})
	if not employees or not _installed(PAY_RATE_DOCTYPE):
		return {}
	out = defaultdict(list)
	for row in frappe.get_all(
		PAY_RATE_DOCTYPE,
		filters={"parenttype": "Employee", "parent": ["in", employees]},
		fields=list(PAY_RATE_FIELDS),
		order_by="effective_from asc",
		limit_page_length=0,
	):
		out[row.get("parent")].append(row)
	return dict(out)


# ---------------------------------------------------------------------- actuals (P4.1)


def task_actuals(task_names, now=None):
	"""``(actuals, labels)``: ``{task: {person key: hours}}`` and a label for every person key."""
	names = [n for n in dict.fromkeys(task_names or []) if n]
	if not names:
		return {}, {}
	intervals = read_intervals(tasks=names)
	if not intervals:
		return {}, {}
	employee_to_resource, labels, _kinds = resource_index()
	now = now or frappe.utils.now_datetime()
	actuals = actuals_by_task(intervals, now, employee_to_resource)
	strangers = {row.get("employee") for row in intervals if row.get("employee") not in employee_to_resource}
	labels = dict(labels)
	labels.update(employee_labels(strangers))
	return actuals, labels


def actuals_summary(task, booked, actual, labels, crew=None):
	"""The :func:`get_actuals` entry for one task.

	``booked`` is the engine's ``{resource: hours}`` over the whole span, ``actual`` is
	``{person key: hours}``, ``crew`` the task's resolved crew (explicit hours win over the
	allocation as a person's planned hours). Everybody on the crew is listed, and so is anybody
	who clocked time on it without being on it.
	"""
	planned = planned_total(task, booked)
	total = round(sum(_float(h) for h in (actual or {}).values()), 2)
	people, order = {}, []
	for member in crew or []:
		key = member.get("resource")
		if not key or key in people:
			continue
		hours = member.get("hours")
		people[key] = {
			"resource": key,
			"label": member.get("label") or (labels or {}).get(key) or key,
			"planned": round(_float(hours) if _float(hours) > 0 else _float((booked or {}).get(key)), 2),
			"actual": 0.0,
		}
		order.append(key)
	for key, hours in (booked or {}).items():
		if key not in people:
			people[key] = {
				"resource": key,
				"label": (labels or {}).get(key) or key,
				"planned": round(_float(hours), 2),
				"actual": 0.0,
			}
			order.append(key)
	for key, hours in (actual or {}).items():
		if key not in people:
			people[key] = {
				"resource": None if key.startswith(EMPLOYEE_KEY) else key,
				"label": (labels or {}).get(key) or key,
				"planned": 0.0,
				"actual": 0.0,
			}
			order.append(key)
		people[key]["actual"] = round(_float(hours), 2)
	return {
		"planned": planned,
		"actual": total,
		"by_person": [people[key] for key in order],
		"over_plan": is_over_plan(planned, total, (task or {}).get("status")),
	}


# ---------------------------------------------------------------------- labor forecast (P4.2)


def _horizon(project, today):
	"""The last day of the project's open dated tasks (at least today), at most 180 days out."""
	last = today
	for row in frappe.get_all(
		"Task",
		filters={"project": project},
		fields=["name", "status", "exp_start_date", "exp_end_date", "is_group", "is_template"],
		limit_page_length=0,
	):
		if row.get("status") in engine.FINISHED_STATUSES or row.get("is_group") or row.get("is_template"):
			continue
		span = engine.task_span(row)
		if span and span[1] > last:
			last = span[1]
	return min(last, today + datetime.timedelta(days=FORECAST_MAX_DAYS))


def booked_by_person(days, project):
	"""``{resource: {date: hours}}``: firm engine bookings on ``project`` (pencils are not labor
	anyone has committed to, and driving belongs to no project)."""
	out = defaultdict(lambda: defaultdict(float))
	for resource, per_day in (days or {}).items():
		for day, cell in (per_day or {}).items():
			for booking in cell.get("bookings") or []:
				if booking.get("tentative") or booking.get("kind") not in LABOR_KINDS:
					continue
				if booking.get("project") != project:
					continue
				out[resource][engine._as_date(day)] += _float(booking.get("hours"))
	return {resource: dict(per_day) for resource, per_day in out.items()}


def labor_forecast(project, with_cost=False, now=None):
	"""The labor forecast for ``project`` (see the module docstring and :func:`forecast_answer`).

	``with_cost`` is decided by the caller (:func:`can_see_cost`, or True for the budget rollup,
	which writes into a permlevel-2 field); without it no pay rate is read. The engine runs with
	``google=False``: a forecast never asks Google for a drive time.
	"""
	from frappe.utils import getdate, nowdate

	today = getdate(nowdate())
	now = now or frappe.utils.now_datetime()
	through = _horizon(project, today)
	data = engine._compute(today, through, google=False)
	booked = booked_by_person(data.get("days"), project)
	employee_to_resource, labels, kinds = resource_index()
	for person in data.get("resources") or []:
		labels.setdefault(person["name"], person.get("label") or person["name"])
		kinds.setdefault(person["name"], {"employee": person.get("employee"), "type": person.get("type")})

	actual = defaultdict(lambda: defaultdict(float))
	strangers = set()
	for row in read_intervals(project=project):
		key = person_key(row.get("employee"), employee_to_resource)
		start = engine._as_date(row.get("start_time"))
		if not key or not start:
			continue
		if key.startswith(EMPLOYEE_KEY):
			strangers.add(row.get("employee"))
		actual[key][start] += interval_hours(row, now)
	labels.update(employee_labels(strangers))

	def employee_of(key):
		if key.startswith(EMPLOYEE_KEY):
			return key[len(EMPLOYEE_KEY) :]
		info = kinds.get(key) or {}
		# A subcontractor is not on our payroll, whatever their record links to.
		return None if info.get("type") == "Subcontractor" else info.get("employee")

	keys = list(dict.fromkeys(list(booked) + list(actual)))
	rates = pay_rate_rows({employee_of(k) for k in keys}) if with_cost else {}
	people = {}
	for key in keys:
		rows = rates.get(employee_of(key)) or []
		rate_of = (lambda day, rows=rows: rate_on(rows, day)) if with_cost else None
		people[key] = forecast_person(booked.get(key) or {}, dict(actual.get(key) or {}), today, rate_of)
	return forecast_answer(project, people, labels, with_cost, through)


# ---------------------------------------------------------------------- Task validate (P4.4)


def validate_equipment(doc, method=None):
	"""Tidy ``Task.custom_equipment`` before it is stored. Task ``validate`` doc_event.

	One row per vehicle/asset (the first wins); the link that does not match the row's type is
	cleared; a row naming nothing is an error; ``label`` is filled from the Asset's
	``asset_name`` or the vehicle's name. Reads the table defensively: doc_events fire during
	ERPNext's own test bootstrap, before the field exists.
	"""
	rows = list(getattr(doc, "custom_equipment", None) or [])
	if not rows:
		return
	seen = set()
	for row in rows:
		kind = row.get("equipment_type") or ("Vehicle" if row.get("vehicle") else "Asset")
		row.equipment_type = kind
		if kind == "Vehicle":
			row.asset = None
		else:
			row.vehicle = None
		ref = engine.equipment_ref(row)
		if not ref:
			frappe.throw(
				_("Equipment row {0}: pick the {1}.").format(
					row.get("idx"), _("vehicle") if kind == "Vehicle" else _("asset")
				)
			)
		if ref in seen:
			doc.remove(row)
			continue
		seen.add(ref)
	refs = [engine.equipment_ref(row) for row in (doc.get("custom_equipment") or [])]
	labels, _statuses = engine.read_equipment_details([r for r in refs if r])
	for row, ref in zip(doc.get("custom_equipment") or [], refs, strict=False):
		if ref:
			row.label = labels.get(ref) or ref[1]
