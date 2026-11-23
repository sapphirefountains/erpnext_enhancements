# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Retainage Held: what each job holds back and has released, by schedule of values (B9 follow-up).

One row per schedule, from the figures its latest certified pay application wrote onto it
(``retainage_held``, ``retainage_released``, ``billed_to_date``). The arithmetic is
:func:`erpnext_enhancements.estimating.billing_reports.retainage_rows`; schedules and applications
are read with ``frappe.get_list``, so a person sees only the jobs they may read, and only permlevel 0
fields, which every role on this report reads.
"""

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements.estimating import billing_reports as br

SOV_FIELDS = [
	"name",
	"project",
	"customer",
	"status",
	"contract_sum_to_date",
	"billed_to_date",
	"retainage_held",
	"retainage_released",
]
APP_FIELDS = ["name", "schedule_of_values", "application_no", "period_to", "application_type"]


def execute(filters=None):
	filters = frappe._dict(filters or {})
	where = {}
	for key in ("customer", "project", "status"):
		if filters.get(key):
			where[key] = filters.get(key)
	schedules = frappe.get_list("Schedule of Values", filters=where, fields=SOV_FIELDS, limit_page_length=0)
	names = [s.name for s in schedules]
	certified = (
		frappe.get_list(
			"Pay Application",
			filters={"schedule_of_values": ["in", names], "docstatus": 1},
			fields=APP_FIELDS,
			limit_page_length=0,
		)
		if names
		else []
	)
	# A Check left unticked is never sent (query_report.js get_filter_values drops every falsy value), so
	# the box is "include jobs holding nothing", off by default: absent means holding only.
	holding_only = not cint(filters.get("include_empty"))
	rows = br.retainage_rows(schedules, certified, holding_only=holding_only)
	# Our own total row: frappe's would average the percent column (billing_reports module docstring).
	return columns(), [*rows, br.retainage_total(rows)] if rows else rows


def columns():
	return [
		{
			"fieldname": "schedule_of_values",
			"label": _("Schedule of Values"),
			"fieldtype": "Link",
			"options": "Schedule of Values",
			"width": 150,
		},
		{
			"fieldname": "project",
			"label": _("Project"),
			"fieldtype": "Link",
			"options": "Project",
			"width": 130,
		},
		{
			"fieldname": "customer",
			"label": _("Account"),
			"fieldtype": "Link",
			"options": "Customer",
			"width": 170,
		},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 80},
		{
			"fieldname": "contract_sum_to_date",
			"label": _("Contract Sum to Date"),
			"fieldtype": "Currency",
			"width": 140,
		},
		{"fieldname": "billed_to_date", "label": _("Billed to Date"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "retainage_held", "label": _("Retainage Held"), "fieldtype": "Currency", "width": 130},
		{
			"fieldname": "retainage_released",
			"label": _("Released to Date"),
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"fieldname": "held_percent_of_billed",
			"label": _("Held % of Billed"),
			"fieldtype": "Percent",
			"width": 110,
		},
		{
			"fieldname": "latest_application",
			"label": _("Latest Certified"),
			"fieldtype": "Link",
			"options": "Pay Application",
			"width": 140,
		},
		{"fieldname": "latest_period_to", "label": _("Period To"), "fieldtype": "Date", "width": 100},
		{"fieldname": "latest_type", "label": _("Type"), "fieldtype": "Data", "width": 120},
	]
