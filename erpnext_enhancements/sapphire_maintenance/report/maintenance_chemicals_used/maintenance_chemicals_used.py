# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Maintenance Chemicals Used (v1.731.0, TASK-2026-02480): chemicals and parts used, by site and month.

One row per site x month x item x unit, with the total quantity, the number of visits it was used
on and the last visit date. The rules (what counts as a visit that happened, why quantities are
never converted or added across units, how tablet sizes stay apart) are in
``sapphire_maintenance/chemical_usage.py``; this file only declares the columns.

No price or cost column: billing is undecided, and a dose is not a stock unit.

Roles: Maintenance Reviewer, Maintenance Supervisor, System Manager (as the Missed Maintenance
Visits report) plus Finance Team, which the 2026-10-12 plan named for the finance phase. Frappe
also demands the *report* permission on the report's ``ref_doctype`` (``desk/query_report.py``,
``get_report_doc``), so Finance Team holds a read + report DocPerm on Sapphire Maintenance Record;
that DocPerm exists so the report can run, not as a privacy guarantee: the record's
``has_permission`` hook and ``1=0`` query condition hide visits only from a Finance-only user,
and every current Finance Team holder also holds Maintenance User.
"""

from frappe import _

from erpnext_enhancements.sapphire_maintenance import chemical_usage


def execute(filters=None):
	rows, message = chemical_usage.get_usage(filters)
	if not rows and not message:
		message = _("No chemicals or parts were recorded on counted visits in this period.")
	return get_columns(), rows, message


def get_columns():
	return [
		{"fieldname": "month", "label": _("Month"), "fieldtype": "Data", "width": 80},
		{
			"fieldname": "project",
			"label": _("Site"),
			"fieldtype": "Link",
			"options": "Project",
			"width": 160,
		},
		{
			"fieldname": "item",
			"label": _("Item"),
			"fieldtype": "Link",
			"options": "Item",
			"width": 160,
		},
		{"fieldname": "size", "label": _("Size"), "fieldtype": "Data", "width": 60},
		{"fieldname": "uom", "label": _("Unit"), "fieldtype": "Data", "width": 90},
		{"fieldname": "qty", "label": _("Quantity"), "fieldtype": "Float", "width": 90},
		{"fieldname": "containers", "label": _("Containers"), "fieldtype": "Float", "width": 80},
		{"fieldname": "visits", "label": _("Visits"), "fieldtype": "Int", "width": 70},
		{"fieldname": "last_visit", "label": _("Last Visit"), "fieldtype": "Date", "width": 100},
	]
