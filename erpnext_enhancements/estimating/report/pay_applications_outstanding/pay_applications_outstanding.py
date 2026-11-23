# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Pay Applications Outstanding: what is waiting to be billed (B9 follow-up).

Two kinds of row, longest waiting first (:func:`erpnext_enhancements.estimating.billing_reports.outstanding_rows`):

* an application still **open**, as a draft with the project manager or **To Review** with
  accounting, with the days since its period ended;
* a **locked** schedule with nothing open whose last certified application's period ended more than
  the reminder window ago ("Due"), or that has never had one ("Not started", aged from the day it was
  locked). A closed schedule is billed out and never listed, and a job billed by milestones is never
  "Due" by the calendar.

Read with ``frappe.get_list`` and only permlevel 0 fields. The column is the payment due: every role
on this report could read the amount certified (permlevel 1) too, but an open application has none
yet, and keeping to level 0 means the report never depends on who holds level 1.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate, nowdate

from erpnext_enhancements.estimating import billing_reports as br

SOV_FIELDS = ["name", "project", "customer", "status", "billing_mode", "locked_on"]
APP_FIELDS = [
	"name",
	"schedule_of_values",
	"application_no",
	"docstatus",
	"status",
	"application_type",
	"period_to",
	"current_payment_due",
	"sent_for_review_on",
]


def execute(filters=None):
	filters = frappe._dict(filters or {})
	where = {"status": ["in", ["Locked", "Closed"]]}
	if filters.get("project"):
		where["project"] = filters.get("project")
	schedules = frappe.get_list("Schedule of Values", filters=where, fields=SOV_FIELDS, limit_page_length=0)
	names = [s.name for s in schedules]
	applications = (
		frappe.get_list(
			"Pay Application",
			filters={"schedule_of_values": ["in", names], "docstatus": ["<", 2]},
			fields=APP_FIELDS,
			limit_page_length=0,
		)
		if names
		else []
	)
	days = (
		cint(filters.get("due_after_days"))
		if filters.get("due_after_days") not in (None, "")
		else br.DEFAULT_DUE_AFTER_DAYS
	)
	return columns(), br.outstanding_rows(schedules, applications, getdate(nowdate()), max(days, 0))


def columns():
	return [
		{"fieldname": "state", "label": _("State"), "fieldtype": "Data", "width": 100},
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
		{
			"fieldname": "pay_application",
			"label": _("Pay Application"),
			"fieldtype": "Link",
			"options": "Pay Application",
			"width": 140,
		},
		{"fieldname": "application_no", "label": _("No."), "fieldtype": "Int", "width": 60},
		{"fieldname": "application_type", "label": _("Type"), "fieldtype": "Data", "width": 120},
		{"fieldname": "period_to", "label": _("Period To"), "fieldtype": "Date", "width": 100},
		{"fieldname": "days_waiting", "label": _("Days Since Period End"), "fieldtype": "Int", "width": 120},
		{
			"fieldname": "current_payment_due",
			"label": _("Payment Due"),
			"fieldtype": "Currency",
			"width": 130,
		},
		{
			"fieldname": "sent_for_review_on",
			"label": _("Sent to Accounting"),
			"fieldtype": "Datetime",
			"width": 150,
		},
	]
