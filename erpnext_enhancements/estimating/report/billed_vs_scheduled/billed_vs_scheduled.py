# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Billed vs Scheduled: billed to date against scheduled value (B9 follow-up).

With a schedule of values picked: its lines by top phase, change orders last (or one row per line).
Without: one row per schedule, its contract sum to date against what has been billed. Billed to date
is what the latest certified pay application wrote on each line (completed and stored). The
arithmetic is in :mod:`erpnext_enhancements.estimating.billing_reports`; documents are read with
permission checks and only permlevel 0 fields (a schedule of values has no other kind).
"""

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements.estimating import billing_reports as br

SOV_FIELDS = ["name", "title", "project", "status", "contract_sum_to_date", "billed_to_date"]
LINE_FIELDS = (
	"idx",
	"item_no",
	"description",
	"phase_number",
	"scheduled_value",
	"billed_to_date",
	"by_others",
	"change_order",
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if filters.get("schedule_of_values"):
		sov = frappe.get_doc("Schedule of Values", filters.get("schedule_of_values"))
		sov.check_permission("read")
		lines = [{f: row.get(f) for f in LINE_FIELDS} for row in sov.lines]
		rows = br.billed_rows(lines, filters.get("group_by") or "Phase")
		# Our own total row: frappe's would average the percent column (billing_reports docstring).
		return line_columns(), [*rows, br.billed_total(rows)] if rows else rows
	# A draft schedule is not a contract yet; it shows only when asked (an unticked box is never sent).
	where = {} if cint(filters.get("include_drafts")) else {"status": ["in", ["Locked", "Closed"]]}
	if filters.get("project"):
		where["project"] = filters.get("project")
	schedules = frappe.get_list("Schedule of Values", filters=where, fields=SOV_FIELDS, limit_page_length=0)
	rows = br.schedule_summary_rows(schedules)
	return summary_columns(), [*rows, br.billed_total(rows)] if rows else rows


def _money_columns():
	return [
		{
			"fieldname": "scheduled_value",
			"label": _("Scheduled Value"),
			"fieldtype": "Currency",
			"width": 140,
		},
		{"fieldname": "billed_to_date", "label": _("Billed to Date"), "fieldtype": "Currency", "width": 140},
		{"fieldname": "percent_billed", "label": _("% Billed"), "fieldtype": "Percent", "width": 90},
		{"fieldname": "balance", "label": _("Balance to Bill"), "fieldtype": "Currency", "width": 140},
	]


def line_columns():
	return [
		{"fieldname": "label", "label": _("Work"), "fieldtype": "Data", "width": 280},
		{"fieldname": "group", "label": _("Phase"), "fieldtype": "Data", "width": 170},
		*_money_columns(),
		{"fieldname": "by_others", "label": _("By Others"), "fieldtype": "Check", "width": 80},
	]


def summary_columns():
	return [
		{
			"fieldname": "schedule_of_values",
			"label": _("Schedule of Values"),
			"fieldtype": "Link",
			"options": "Schedule of Values",
			"width": 150,
		},
		{"fieldname": "label", "label": _("Title"), "fieldtype": "Data", "width": 220},
		{
			"fieldname": "project",
			"label": _("Project"),
			"fieldtype": "Link",
			"options": "Project",
			"width": 130,
		},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 80},
		*_money_columns(),
	]
