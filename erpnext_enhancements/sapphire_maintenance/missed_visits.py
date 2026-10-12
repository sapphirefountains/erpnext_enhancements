# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Missed and stale maintenance visits: the visits that should have happened and have not.

TASK-2026-02475 (A2 of the 2026-10-12 Maintenance tools plan). On 2026-10-12, 18 of 23
visit drafts were 0% filled in, and a forgotten form was found by accident. Backdating
(v1.592.0) lets a technician fill in an earlier day; this says which visits need it.

The rows feed the **Missed Maintenance Visits** report and the "Missed and not sent"
section of the Visit Review page, through :func:`missed_visits`, so the two always agree.
**Nothing is sent automatically** (Nik, 2026-10-12): this lists, it never nudges.

Three kinds of row:

* **Not drafted** (``KIND_NOT_DRAFTED``): a visit the contract says was due, with no open
  draft standing for it. Regular visits mirror the nightly scheduler's own rule
  (``tasks.generate_predictive_maintenance_records``): an Active contract whose fountain's
  ``next_visit_date`` has passed, with no open *regular* draft (``visit_label`` not set) for
  that fountain, or for the contract on a Per Site Visit contract, which gets one row. Skipped,
  as the scheduler skips them: a customer on service hold, a contract not started yet. A date
  inside a paused contract's winter window (``maintenance_scheduling.defer_for_winter``) is
  skipped too: that fountain is drained, and the Winterization visit stands in for the month.
  Seasonal visits (Spring Startup, Winterization, custom rows) are drafted on the 1st of
  their month and stamped with the year, so one whose month has gone by this year with no
  stamp and no record of that visit was never drafted, unless the contract started after
  that month began.
* **Not started** (``KIND_NOT_STARTED``): an open draft dated before today with nothing
  filled in (``completion_percent`` 0).
* **Started, not sent** (``KIND_NOT_SENT``): an open draft dated before today with some
  answers, still not sent for review.

A draft's date is its ``visit_date`` (set by a backfill or Log a visit), else its
Scheduled Visit Date. A draft with neither is unscheduled, which the Maintenance Planner's
tray shows, not late. A visit waiting in Pending Review is on the Visit Review page's queue
and not here.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

DOCTYPE = "Sapphire Maintenance Record"
CONTRACT = "Sapphire Maintenance Contract"
REVIEW_STATE = "Pending Review"

KIND_NOT_DRAFTED = "Not drafted"
KIND_NOT_STARTED = "Not started"
KIND_NOT_SENT = "Started, not sent"
KINDS = (KIND_NOT_DRAFTED, KIND_NOT_STARTED, KIND_NOT_SENT)

MONTHS = [
	"January",
	"February",
	"March",
	"April",
	"May",
	"June",
	"July",
	"August",
	"September",
	"October",
	"November",
	"December",
]


def missed_visits(today=None, technician=None, project=None, kind=None):
	"""Every missed or stale visit, most overdue first. See the module docstring.

	Each row: ``{kind, project, site, serial_no, fountain, visit_label, due_date, days_late,
	technician, technician_name, record, contract, completion}``. ``record`` is None for a
	visit never drafted; ``contract`` is None for a draft that belongs to no contract.
	"""
	today = getdate(today or nowdate())
	rows = late_drafts(today) + undrafted_visits(today)
	if technician:
		rows = [row for row in rows if row["technician"] == technician]
	if project:
		rows = [row for row in rows if row["project"] == project]
	if kind:
		rows = [row for row in rows if row["kind"] == kind]
	_name_things(rows)
	rows.sort(key=lambda row: (-row["days_late"], row["site"] or "", row["fountain"] or ""))
	return rows


def late_drafts(today):
	"""Open drafts dated before ``today``: Not started, or Started, not sent."""
	rows = []
	for draft in frappe.get_all(
		DOCTYPE,
		filters={"docstatus": 0},
		fields=[
			"name",
			"project",
			"serial_no",
			"visit_label",
			"technician",
			"visit_date",
			"scheduled_visit_date",
			"completion_percent",
			"maintenance_contract",
			"workflow_state",
		],
	):
		if (draft.get("workflow_state") or "") == REVIEW_STATE:
			continue
		day = draft.get("visit_date") or draft.get("scheduled_visit_date")
		if not day or getdate(day) >= today:
			continue
		completion = flt(draft.get("completion_percent"))
		rows.append(
			_row(
				KIND_NOT_STARTED if completion <= 0 else KIND_NOT_SENT,
				today,
				getdate(day),
				project=draft.get("project"),
				serial_no=draft.get("serial_no"),
				visit_label=draft.get("visit_label"),
				technician=draft.get("technician"),
				record=draft.get("name"),
				contract=draft.get("maintenance_contract"),
				completion=completion,
			)
		)
	return rows


def undrafted_visits(today):
	"""Visits an Active contract says were due, with nothing drafted for them."""
	from erpnext_enhancements.api.maintenance_scheduling import defer_for_winter
	from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract import (
		iter_seasonal_visits,
	)

	open_regular = frappe.get_all(
		DOCTYPE,
		filters={"docstatus": 0, "visit_label": ["is", "not set"]},
		fields=["project", "serial_no", "maintenance_contract"],
	)
	open_by_contract = {
		row.get("maintenance_contract") for row in open_regular if row.get("maintenance_contract")
	}
	open_by_feature = {(row.get("project"), row.get("serial_no")) for row in open_regular}
	seasonal_done = _seasonal_records(today.year)
	defaults = _default_technicians()

	rows = []
	for name in frappe.get_all(CONTRACT, filters={"status": "Active"}, pluck="name"):
		contract = frappe.get_doc(CONTRACT, name)
		if contract.get("start_date") and getdate(contract.start_date) > today:
			continue
		if contract.get("customer") and frappe.db.get_value(
			"Customer", contract.customer, "custom_service_hold"
		):
			continue
		technician = defaults.get(contract.get("project"))

		due = [
			row
			for row in contract.get("covered_features") or []
			if row.get("next_visit_date")
			and getdate(row.next_visit_date) < today
			# Inside a paused contract's winter window: drained, not missed.
			and defer_for_winter(contract, getdate(row.next_visit_date)) == getdate(row.next_visit_date)
		]
		if contract.get("visit_shape") == "Per Site Visit":
			if due and contract.name not in open_by_contract:
				rows.append(
					_row(
						KIND_NOT_DRAFTED,
						today,
						min(getdate(row.next_visit_date) for row in due),
						project=contract.get("project"),
						technician=technician,
						contract=contract.name,
					)
				)
		else:
			for row in due:
				if (contract.get("project"), row.get("serial_no")) in open_by_feature:
					continue
				rows.append(
					_row(
						KIND_NOT_DRAFTED,
						today,
						getdate(row.next_visit_date),
						project=contract.get("project"),
						serial_no=row.get("serial_no"),
						technician=technician,
						contract=contract.name,
					)
				)

		for visit in iter_seasonal_visits(contract):
			due_on = _seasonal_due(visit, contract, today)
			if not due_on or (contract.name, visit["label"]) in seasonal_done:
				continue
			rows.append(
				_row(
					KIND_NOT_DRAFTED,
					today,
					due_on,
					project=contract.get("project"),
					visit_label=visit["label"],
					technician=technician,
					contract=contract.name,
				)
			)
	return rows


def _seasonal_due(visit, contract, today):
	"""The 1st of a seasonal visit's month when that month has gone by this year undrafted.

	None when it is drafted (stamped for this year), still to come, this month (tonight's
	scheduler run drafts it), or the contract started after that month was over. The
	scheduler drafts on any day of the month, so a contract started part way through it
	still had its chance.
	"""
	if cint(visit.get("last_generated_year")) >= today.year:
		return None
	month = visit.get("target_month")
	if month not in MONTHS:
		return None
	due_on = datetime.date(today.year, MONTHS.index(month) + 1, 1)
	if due_on.month >= today.month:
		return None
	month_over = datetime.date(today.year, due_on.month + 1, 1)  # due_on.month < 12 here
	started = contract.get("start_date") or contract.get("creation")
	if started and getdate(started) >= month_over:
		return None
	return due_on


def _seasonal_records(year):
	"""``{(contract, visit_label)}`` for every labelled visit recorded this year, any state but
	canceled: a seasonal visit made by hand counts even though the scheduler never stamped it."""
	return {
		(row.get("maintenance_contract"), row.get("visit_label"))
		for row in frappe.get_all(
			DOCTYPE,
			filters={
				"visit_label": ["is", "set"],
				"docstatus": ["!=", 2],
				"creation": [">=", f"{year}-01-01"],
			},
			fields=["maintenance_contract", "visit_label"],
		)
		if row.get("maintenance_contract")
	}


def _default_technicians():
	"""``{project: default technician}`` from every Maintenance Profile, in one read."""
	return {
		row.get("project"): row.get("default_technician")
		for row in frappe.get_all("Sapphire Maintenance Profile", fields=["project", "default_technician"])
		if row.get("project")
	}


def _row(kind, today, due, **fields):
	row = {
		"kind": kind,
		"project": None,
		"site": None,
		"serial_no": None,
		"fountain": None,
		"visit_label": None,
		"due_date": due,
		"days_late": (today - due).days,
		"technician": None,
		"technician_name": None,
		"record": None,
		"contract": None,
		"completion": 0.0,
	}
	row.update(fields)
	return row


def _name_things(rows):
	"""Fill in the site, fountain and technician names, one read per kind of thing."""
	projects = sorted({row["project"] for row in rows if row["project"]})
	serials = sorted({row["serial_no"] for row in rows if row["serial_no"]})
	users = sorted({row["technician"] for row in rows if row["technician"]})
	sites = _names("Project", projects, "project_name")
	fountains = _names("Serial No", serials, "item_name")
	people = _names("User", users, "full_name")
	for row in rows:
		row["site"] = sites.get(row["project"]) or row["project"]
		if row["serial_no"]:
			row["fountain"] = fountains.get(row["serial_no"]) or row["serial_no"]
		elif row["kind"] == KIND_NOT_DRAFTED and not row["visit_label"]:
			row["fountain"] = _("Whole site")
		if row["technician"]:
			row["technician_name"] = people.get(row["technician"]) or row["technician"]


def _names(doctype, names, field):
	if not names:
		return {}
	return {
		row.get("name"): row.get(field)
		for row in frappe.get_all(doctype, filters={"name": ["in", names]}, fields=["name", field])
		if row.get(field)
	}
