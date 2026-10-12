# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Compared with last visit: each water reading next to its value at the site's previous visit.

A reviewer judging "pH 8.6" needs to know whether it was 7.4 last week or 8.7 every week
(TASK-2026-02476, A3 of the 2026-10-12 Maintenance tools plan). This is the one place that
answer is worked out. Three surfaces read it, so they cannot disagree:

* the visit review email (``review_digest.build_context``): the Value cell reads
  "7.4 (was 7.9)", which keeps the table at the email system's four columns;
* the Visit Review page, which shows that same context;
* the record's desk-form dashboard (``get_dashboard_context(record=...)``).

**What counts as the previous value.** The same reading (matched on its label, ignoring case
and surrounding spaces) on the same water feature, at the most recent *approved* visit
(docstatus 1) to the same site dated on or before this one. A visit that left the reading
blank or zero did not measure it, as everywhere else on this form (``evaluate_reading_ranges``
says why), so the search goes back to the latest visit that did, at most ``LOOKBACK`` approved
visits. A visit not yet approved is never compared against: it may still be sent back.

**Which water feature.** A row's ``serial_no`` when the row is tagged (a Per Site Visit record
tags every row), else the record's own ``serial_no``, else none (a site-level reading). The
same rule on both sides, so a contract that changed shape between Per Feature and Per Site
Visit still lines up.

**A visit's date.** ``visit_date`` (the day the work was done), else the Scheduled Visit
Date, else the day the record was created. Blank ``visit_date`` means "the day it was
submitted", which no field records, so the scheduled date is the nearest thing kept.

With no approved visit to the site before this one, every surface says "First recorded
visit". Until the first approvals on 2026-10-12 that was true everywhere.
"""

import datetime

import frappe
from frappe.utils import cint, flt, getdate, nowdate

DOCTYPE = "Sapphire Maintenance Record"
#: How many earlier approved visits to the site are searched for a reading. A weekly site's
#: ten visits are about two and a half months; a reading not taken in that time is not a
#: useful comparison.
LOOKBACK = 10
#: Records read before the date filter, which cannot be expressed in ``get_all``: newest
#: first by creation, so a backfilled visit created late is still among them.
CANDIDATES = 50


def visit_day(record):
	"""The day a visit's work was done, by the rule in the module docstring."""
	value = record.get("visit_date") or record.get("scheduled_visit_date")
	if value:
		return getdate(value)
	created = record.get("creation")
	return getdate(created) if created else getdate(nowdate())


def feature_of(record, row):
	"""The water feature a row belongs to: its own tag, else the record's, else ''."""
	return row.get("serial_no") or record.get("serial_no") or ""


def reading_key(record, row):
	"""``(feature, reading)`` for a chemistry row: how a reading is matched across visits."""
	return (feature_of(record, row), (row.get("reading") or "").strip().lower())


def measured(row):
	return bool(flt(row.get("reading_value")))


def earlier_visits(doc):
	"""The site's approved visits before this one, newest first, at most ``LOOKBACK``.

	``[{name, serial_no, visit_date, scheduled_visit_date, creation, day}]``.
	"""
	if not doc.get("project"):
		return []
	rows = frappe.get_all(
		DOCTYPE,
		filters={"project": doc.project, "docstatus": 1, "name": ["!=", doc.get("name") or ""]},
		fields=["name", "serial_no", "visit_date", "scheduled_visit_date", "creation"],
		order_by="creation desc",
		limit=CANDIDATES,
	)
	day = visit_day(doc)
	earlier = []
	for row in rows:
		row = frappe._dict(row)
		row.day = visit_day(row)
		if row.day <= day:
			earlier.append(row)
	earlier.sort(key=lambda r: (r.day, str(r.get("creation") or "")), reverse=True)
	return earlier[:LOOKBACK]


def previous_readings(doc, visits=None):
	"""``{reading_key: {value, uom, day, record}}``: each reading's latest earlier measurement.

	Only readings this visit has are looked up. See the module docstring for the rules.
	``visits`` is ``earlier_visits(doc)`` when the caller already has it.
	"""
	wanted = {reading_key(doc, row) for row in doc.get("chemistry_readings") or []}
	if not wanted:
		return {}
	visits = earlier_visits(doc) if visits is None else visits
	if not visits:
		return {}
	by_name = {visit.name: visit for visit in visits}
	rows = frappe.get_all(
		"Sapphire Chemistry Reading",
		filters={"parenttype": DOCTYPE, "parent": ["in", list(by_name)]},
		fields=["parent", "reading", "reading_value", "uom", "serial_no"],
	)
	rank = {visit.name: index for index, visit in enumerate(visits)}
	found = {}
	for row in sorted(rows, key=lambda r: rank.get(r.get("parent"), len(rank))):
		visit = by_name.get(row.get("parent"))
		if not visit or not measured(row):
			continue
		key = reading_key(visit, row)
		if key in wanted and key not in found:
			found[key] = {
				"value": flt(row.get("reading_value")),
				"uom": row.get("uom") or "",
				"day": visit.day,
				"record": visit.name,
			}
	return found


def compare(doc):
	"""Everything the three surfaces show, worked out once.

	Returns ``{"previous": previous_readings(doc), "first_visit": bool}``. ``first_visit`` is
	True when the site has no approved visit before this one at all; with earlier visits
	that simply never took these readings it is False and ``previous`` is empty.
	"""
	visits = earlier_visits(doc)
	if not visits:
		return {"previous": {}, "first_visit": True}
	return {"previous": previous_readings(doc, visits), "first_visit": False}


def reading_comparisons(doc, result=None):
	"""One line per chemistry row on this visit, in form order, for the desk-form dashboard.

	``[{reading, serial_no, value, uom, out_of_range, previous, previous_day, previous_record,
	change}]``; the ``previous*`` keys and ``change`` are None where nothing earlier measured
	the reading, and ``value``/``change`` are None for a reading not taken on this visit.
	"""
	result = result or compare(doc)
	lines = []
	for row in doc.get("chemistry_readings") or []:
		before = result["previous"].get(reading_key(doc, row))
		value = flt(row.get("reading_value")) if measured(row) else None
		lines.append(
			{
				"reading": row.get("reading") or "",
				"serial_no": feature_of(doc, row) or None,
				"value": value,
				"uom": row.get("uom") or "",
				"out_of_range": cint(row.get("out_of_range")),
				"previous": before["value"] if before else None,
				"previous_day": str(before["day"]) if before else None,
				"previous_record": before["record"] if before else None,
				"change": round(value - before["value"], 4) if before and value is not None else None,
			}
		)
	return lines


def compared_days(result):
	"""The distinct days the previous values came from, newest first."""
	days = {entry["day"] for entry in result["previous"].values() if isinstance(entry["day"], datetime.date)}
	return sorted(days, reverse=True)
