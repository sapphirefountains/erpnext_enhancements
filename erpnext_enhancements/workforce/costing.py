# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Labour costing: pay rates → the cost of a Job Interval → ERPNext's Activity Cost.

WI-016 chose **costing-rate-only**: keep pay out of ERPNext, give each employee an
Activity Cost row with a costing rate somebody types in, and let Timesheets cost from
that. Nik reversed that on 2026-09-17. Pay now lives on the Employee as effective-dated
``Employee Pay Rate`` rows, held at **permission level 1** (HR Manager, Accounts Manager,
System Manager), and everything downstream is *derived* from it:

* ``Job Interval`` gets a permlevel-1 pay block — rate in force on the start date, burden,
  burdened rate, and ``labor_cost`` (stamped here, recomputed by the interval's own
  ``validate`` whenever ``end_time`` is set, so an approved correction keeps it honest);
* the Timesheet Detail line the interval syncs to gets ``costing_rate = burdened_rate``
  (``api.time_kiosk.sync_interval_to_timesheet``), because ERPNext's ``update_cost`` would
  otherwise read Activity Cost / Activity Type and, for a line with no activity type, cost
  nothing at all;
* ``Activity Cost`` rows are **upserted from the pay rate** per (employee, Activity Type),
  so anything in ERPNext that still costs from Activity Cost — a Timesheet typed by hand —
  agrees with the kiosk.

Worked hours are never stored (the module README's rule: elapsed is derived), and this
module never touches ``billing_rate`` on an Activity Cost that already exists — that is a
number somebody may have set on purpose.

Everything that writes is wrapped: a costing table must never block an Employee save,
so every exception in the sync paths is logged with ``frappe.log_error`` and swallowed.
The arithmetic is in the pure helpers at the top so ``tests/test_workforce_costing.py``
can pin it without a bench.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate, nowdate

from erpnext_enhancements.workforce.doctype.time_kiosk_settings.time_kiosk_settings import (
	get_settings,
)

#: Annual hours behind a salaried employee's hourly equivalent (40 h × 52 weeks).
ANNUAL_HOURS = 2080.0

PAY_RATE_TABLE = "Employee Pay Rate"
PAY_RATE_FIELDS = ("effective_from", "pay_type", "hourly_rate", "annual_salary", "hourly_equivalent", "burden_pct")

#: The permlevel-1 block on Job Interval, in the order they are stamped.
COST_FIELDS = ("pay_type", "pay_rate", "burden_pct", "burdened_rate", "labor_cost")


# ------------------------------------------------------------------ pure helpers


def hourly_equivalent(annual_salary):
	"""``annual_salary / 2080``, the hourly figure a salaried person is costed at."""
	return round(flt(annual_salary) / ANNUAL_HOURS, 4) if flt(annual_salary) > 0 else 0.0


def resolve_rate(rows, on_date, default_burden_pct=0.0):
	"""Pick the row in force on ``on_date`` and compute the burdened rate.

	``rows`` are dicts (or row objects) with the ``PAY_RATE_FIELDS`` keys. The rate in
	force is the row with the latest ``effective_from`` on or before ``on_date``; a
	future-dated row is ignored. Returns ``{"pay_type", "pay_rate", "burden_pct",
	"burdened_rate"}`` or ``None`` when nothing is in force yet.
	"""
	on_date = getdate(on_date)
	chosen = None
	for row in rows or []:
		effective = _get(row, "effective_from")
		if not effective:
			continue
		effective = getdate(effective)
		if effective > on_date:
			continue
		if chosen is None or effective > getdate(_get(chosen, "effective_from")):
			chosen = row
	if chosen is None:
		return None

	pay_type = _get(chosen, "pay_type") or "Hourly"
	if pay_type == "Salaried":
		rate = flt(_get(chosen, "hourly_equivalent")) or hourly_equivalent(_get(chosen, "annual_salary"))
	else:
		rate = flt(_get(chosen, "hourly_rate"))

	burden = _get(chosen, "burden_pct")
	burden = flt(default_burden_pct) if burden in (None, "") else flt(burden)

	return {
		"pay_type": pay_type,
		"pay_rate": round(rate, 4),
		"burden_pct": burden,
		"burdened_rate": burdened_rate(rate, burden),
	}


def burdened_rate(rate, burden_pct):
	return round(flt(rate) * (1.0 + flt(burden_pct) / 100.0), 4)


def worked_hours(start_time, end_time, paused_seconds=0):
	"""``(end − start − paused) / 3600``, clamped at zero."""
	if not start_time or not end_time:
		return 0.0
	seconds = (get_datetime(end_time) - get_datetime(start_time)).total_seconds() - flt(paused_seconds)
	return max(seconds, 0.0) / 3600.0


def labor_cost(start_time, end_time, paused_seconds, rate):
	"""Worked hours × the burdened rate, to the cent."""
	return round(worked_hours(start_time, end_time, paused_seconds) * flt(rate), 2)


def _get(row, key):
	if isinstance(row, dict):
		return row.get(key)
	return getattr(row, key, None)


# ------------------------------------------------------------------ rate lookup


def _pay_rate_rows(employee):
	"""Every pay-rate row on the employee, oldest first. Empty on a bench where the
	child table has not been created yet."""
	try:
		if not frappe.db.table_exists(PAY_RATE_TABLE):
			return []
		return frappe.get_all(
			PAY_RATE_TABLE,
			filters={"parent": employee, "parenttype": "Employee"},
			fields=list(PAY_RATE_FIELDS),
			order_by="effective_from asc",
		)
	except Exception:
		return []


def rate_for(employee, on_date=None):
	"""The pay rate in force for ``employee`` on ``on_date`` (default today), or None.

	Reads ``tabEmployee Pay Rate`` directly rather than loading the Employee, because
	this is called from the clock-in path and from a scheduler sweep over every employee.
	"""
	if not employee:
		return None
	return resolve_rate(
		_pay_rate_rows(employee),
		on_date or nowdate(),
		default_burden_pct=flt(get_settings().get("default_burden_pct")),
	)


# ------------------------------------------------------------------ stamping


def stamp_position(interval):
	"""Copy the employee's Position and tier onto the interval — once.

	Read through ``db.get_value`` guarded on the two custom columns, because this
	fires from ``log_time`` and the columns are fixtures that may not exist on a
	fresh bench. Never overwrites: a promotion must not rewrite an old interval.
	"""
	if not getattr(interval, "employee", None) or getattr(interval, "position", None):
		return
	fields = []
	for column in ("custom_position", "custom_position_tier"):
		try:
			if frappe.db.has_column("Employee", column):
				fields.append(column)
		except Exception:
			pass
	if not fields:
		return
	row = frappe.db.get_value("Employee", interval.employee, fields, as_dict=True)
	if not row:
		return
	if row.get("custom_position"):
		interval.position = row.get("custom_position")
	if row.get("custom_position_tier") is not None:
		interval.position_tier = cint(row.get("custom_position_tier"))


def stamp_cost(interval):
	"""Write the permlevel-1 pay block onto ``interval`` from the rate in force on its
	start date. ``labor_cost`` only when ``end_time`` is set; everything blank when no
	rate exists, so a missing rate reads as missing rather than as zero cost.

	Called from ``JobInterval.validate`` (every save with an end time) and from
	``log_time`` at Start (rate only). Never raises on a missing table or rate.
	"""
	if not _interval_has_cost_fields():
		return
	try:
		rate = rate_for(interval.employee, getdate(interval.start_time) if interval.start_time else None)
	except Exception:
		frappe.log_error(title="Time Kiosk costing: rate lookup failed")
		rate = None

	if not rate:
		for field in COST_FIELDS:
			setattr(interval, field, None)
		return

	interval.pay_type = rate["pay_type"]
	interval.pay_rate = rate["pay_rate"]
	interval.burden_pct = rate["burden_pct"]
	interval.burdened_rate = rate["burdened_rate"]
	if interval.end_time:
		interval.labor_cost = labor_cost(
			interval.start_time, interval.end_time, interval.total_paused_seconds, rate["burdened_rate"]
		)
	else:
		interval.labor_cost = None


def _interval_has_cost_fields():
	try:
		return frappe.db.has_column("Job Interval", "burdened_rate")
	except Exception:
		return False


# ------------------------------------------------------------------ Employee validate


def validate_employee_pay_rates(doc, method=None):
	"""Employee ``validate`` doc_event: every row has the amount its pay type needs, no
	two rows share an effective date, rows are sorted, ``hourly_equivalent`` is filled.

	``getattr(doc, "custom_pay_rates", None)`` and not ``doc.custom_pay_rates``: this
	hook fires during ERPNext's own test bootstrap before the custom field exists.
	"""
	rows = getattr(doc, "custom_pay_rates", None)
	if not rows:
		return

	seen = {}
	for row in rows:
		if not row.effective_from:
			frappe.throw(_("Pay Rates row {0}: Effective From is required.").format(row.idx))
		key = str(getdate(row.effective_from))
		if key in seen:
			frappe.throw(
				_("Pay Rates rows {0} and {1} share the same Effective From ({2}); only one rate can be in force on a day.").format(
					seen[key], row.idx, key
				)
			)
		seen[key] = row.idx

		pay_type = row.pay_type or "Hourly"
		if pay_type == "Hourly":
			if flt(row.hourly_rate) <= 0:
				frappe.throw(_("Pay Rates row {0}: an Hourly rate needs an Hourly Rate above zero.").format(row.idx))
			row.hourly_equivalent = None
		elif pay_type == "Salaried":
			if flt(row.annual_salary) <= 0:
				frappe.throw(_("Pay Rates row {0}: a Salaried rate needs an Annual Salary above zero.").format(row.idx))
			row.hourly_equivalent = hourly_equivalent(row.annual_salary)
		else:
			frappe.throw(_("Pay Rates row {0}: unknown pay type {1}.").format(row.idx, pay_type))

		if row.burden_pct not in (None, "") and flt(row.burden_pct) < 0:
			frappe.throw(_("Pay Rates row {0}: Burden % cannot be negative.").format(row.idx))

	rows.sort(key=lambda r: getdate(r.effective_from))
	for index, row in enumerate(rows, start=1):
		row.idx = index


def _rate_signature(doc):
	rows = getattr(doc, "custom_pay_rates", None) or []
	return tuple(
		(str(getdate(r.effective_from)) if r.effective_from else "", r.pay_type, flt(r.hourly_rate),
		 flt(r.annual_salary), flt(r.burden_pct) if r.burden_pct not in (None, "") else None)
		for r in rows
	)


def on_employee_update(doc, method=None):
	"""Employee ``on_update`` doc_event: re-derive Activity Cost when the pay rates
	changed. Compared against ``get_doc_before_save()`` so an ordinary Employee save —
	and Employee is saved often — costs nothing."""
	if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
		return
	if not hasattr(doc, "custom_pay_rates"):
		return
	before = doc.get_doc_before_save()
	if before is not None and _rate_signature(before) == _rate_signature(doc):
		return
	sync_activity_costs(employee=doc.name)


def on_activity_type_insert(doc, method=None):
	"""Activity Type ``after_insert`` doc_event: a new activity gets a cost row for
	every employee with a rate, so a hand-typed Timesheet against it costs correctly."""
	if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
		return
	sync_activity_costs(activity_type=doc.name)


# ------------------------------------------------------------------ Activity Cost


def sync_activity_costs(employee=None, activity_type=None):
	"""Upsert ``Activity Cost`` per (employee, Activity Type) with ``costing_rate`` =
	the burdened rate in force today. New rows get ``billing_rate 0``; an existing
	row's ``billing_rate`` is never touched. Employees with no rate are skipped, and
	an existing row for them is left alone (it may be a hand-typed rate).

	Returns the number of rows written. Every exception is logged and swallowed —
	this runs inside an Employee save and a costing table must never block one.
	"""
	written = 0
	try:
		if not (frappe.db.table_exists("Activity Cost") and frappe.db.table_exists(PAY_RATE_TABLE)):
			return 0

		employees = (
			[employee]
			if employee
			else frappe.get_all("Employee", filters={"status": "Active"}, pluck="name", limit=1000)
		)
		activity_types = (
			[activity_type]
			if activity_type
			else frappe.get_all("Activity Type", pluck="name", order_by="name asc", limit=500)
		)
		if not employees or not activity_types:
			return 0

		default_burden = flt(get_settings().get("default_burden_pct"))
		today = nowdate()

		for emp in employees:
			rate = resolve_rate(_pay_rate_rows(emp), today, default_burden)
			if not rate:
				continue
			for activity in activity_types:
				try:
					written += _upsert_activity_cost(emp, activity, rate["burdened_rate"])
				except Exception:
					frappe.log_error(title=f"Time Kiosk costing: Activity Cost {emp} / {activity}")
	except Exception:
		frappe.log_error(title="Time Kiosk costing: sync_activity_costs")
	return written


def _upsert_activity_cost(employee, activity_type, costing_rate):
	existing = frappe.db.get_value(
		"Activity Cost",
		{"employee": employee, "activity_type": activity_type},
		["name", "costing_rate"],
		as_dict=True,
	)
	if existing:
		if abs(flt(existing.costing_rate) - flt(costing_rate)) < 0.00005:
			return 0
		frappe.db.set_value("Activity Cost", existing.name, "costing_rate", costing_rate)
		return 1

	doc = frappe.get_doc({
		"doctype": "Activity Cost",
		"employee": employee,
		"activity_type": activity_type,
		"costing_rate": costing_rate,
		"billing_rate": 0,
	})
	doc.insert(ignore_permissions=True)
	return 1


def sync_all_activity_costs():
	"""Daily scheduler job: pick up rate rows whose effective date has arrived. The
	on_update trigger only fires when an Employee is saved, so a rate dated next month
	would otherwise never reach Activity Cost until somebody touched the record."""
	sync_activity_costs()
