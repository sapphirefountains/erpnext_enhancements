# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Missed Calls (R6, v1.757.0): incoming calls nobody answered and nobody called back in time.

An incoming Call Log whose status says nobody picked up (``insights.MISSED_STATUSES``) and that no
outgoing call to the same number followed within the callback window (default Marketing Settings'
*Missed Calls: Callback Window*, 4 hours). First-time callers -- a number never seen before -- are
flagged and listed first. One still inside its window reads *waiting*.

Whether a call was called back is decided on **every** call, so a callback the viewer cannot see
still counts; then **Call Log's own permissions apply and are never widened**: only the calls
``frappe.get_list`` lets the viewer open are shown (``calls.call_rows(respect_permissions=True)``), so a
person sees only the calls they may already open, numbers included.

Its ``ref_doctype`` is GA4 Daily Metric, not Call Log: Frappe checks report permission on the
``ref_doctype`` before ``execute`` runs, and giving the marketing roles report rights on Call Log would open
every call in Report View. The marketing roles read and report on their own daily figures; the checks inside
``execute`` decide the rest.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate, now_datetime, nowdate

from erpnext_enhancements.marketing.performance import insights, rules
from erpnext_enhancements.marketing.performance.calls import call_rows


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not frappe.has_permission("Call Log", "read"):
		frappe.throw(_("You cannot read Call Logs."), frappe.PermissionError)
	from erpnext_enhancements.marketing.core.utils import get_settings

	start, end = rules.parse_range(filters.get("from_date"), filters.get("to_date"), getdate(nowdate()))
	hours = cint(filters.get("callback_hours")) or cint(get_settings().get("missed_call_callback_hours")) or 4
	visible = {c["name"] for c in call_rows(start, end, respect_permissions=True)}
	rows = [
		row
		for row in insights.missed_calls(call_rows(start, end), now_datetime(), hours)
		if row["name"] in visible
	]
	for row in rows:
		row["first_time"] = 1 if row.get("first_time") else 0
	columns = [
		{"label": _("When"), "fieldname": "at", "fieldtype": "Datetime", "width": 160},
		{"label": _("First Time"), "fieldname": "first_time", "fieldtype": "Check", "width": 90},
		{"label": _("State"), "fieldname": "state", "fieldtype": "Data", "width": 80},
		{"label": _("Caller"), "fieldname": "caller", "fieldtype": "Data", "width": 200},
		{"label": _("Number"), "fieldname": "number", "fieldtype": "Data", "width": 140},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
		{
			"label": _("Call Log"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Call Log",
			"width": 200,
		},
	]
	return columns, rows
