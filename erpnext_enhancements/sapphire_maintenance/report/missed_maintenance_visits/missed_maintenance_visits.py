# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Missed Maintenance Visits (v1.595.0, TASK-2026-02475): visits that should have happened.

Three kinds of row, worked out in ``sapphire_maintenance/missed_visits.py``, which the Visit
Review page's "Missed and not sent" section reads too, so the two always agree: **Not drafted**
(a contract visit that fell due with nothing drafted for it), **Not started** (a draft dated
before today with nothing filled in) and **Started, not sent** (a draft dated before today
with answers, never sent for review). The module docstring has the exact rules, including
the winter pause and seasonal visits.

Nothing here writes or sends: Nik decided on 2026-10-12 that missed visits are listed, never
chased automatically. The *Open* column opens a draft in the Visit Wizard, where the technician
can set the day the work was done (v1.592.0), or the contract for a visit never drafted.

Roles: Maintenance Reviewer, Maintenance Supervisor, System Manager (the report JSON), the
Visit Review page's.
"""

import frappe
from frappe import _

from erpnext_enhancements.sapphire_maintenance import missed_visits


def execute(filters=None):
	filters = filters or {}
	rows = missed_visits.missed_visits(
		technician=filters.get("technician") or None,
		project=filters.get("project") or None,
		kind=filters.get("kind") or None,
	)
	for row in rows:
		row["open"] = row["record"] or row["contract"] or ""
		row["open_kind"] = "visit" if row["record"] else "contract"
	message = None
	if not rows:
		message = _("No missed or unsent visits.")
	return get_columns(), rows, message


def get_columns():
	return [
		{"fieldname": "kind", "label": _("Kind"), "fieldtype": "Data", "width": 140},
		{"fieldname": "site", "label": _("Site"), "fieldtype": "Data", "width": 200},
		{"fieldname": "fountain", "label": _("Water Feature"), "fieldtype": "Data", "width": 170},
		{"fieldname": "visit_label", "label": _("Visit Type"), "fieldtype": "Data", "width": 130},
		{"fieldname": "due_date", "label": _("Due"), "fieldtype": "Date", "width": 100},
		{"fieldname": "days_late", "label": _("Days Late"), "fieldtype": "Int", "width": 90},
		{"fieldname": "technician_name", "label": _("Technician"), "fieldtype": "Data", "width": 150},
		{"fieldname": "completion", "label": _("Filled In"), "fieldtype": "Percent", "width": 90},
		{
			"fieldname": "record",
			"label": _("Visit"),
			"fieldtype": "Link",
			"options": "Sapphire Maintenance Record",
			"width": 120,
		},
		{"fieldname": "open", "label": _("Open"), "fieldtype": "Data", "width": 160},
		{
			"fieldname": "project",
			"label": _("Project"),
			"fieldtype": "Link",
			"options": "Project",
			"width": 110,
		},
	]
