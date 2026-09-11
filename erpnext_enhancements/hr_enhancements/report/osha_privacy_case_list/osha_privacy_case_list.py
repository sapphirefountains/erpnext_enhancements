# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The separate list that maps a privacy case number back to a name.

The rule requires exactly this: a case in one of six categories shows no name on
the posted 300 log, and the employer keeps a **separate, confidential list** so
the case can still be identified when it has to be.

So this report exists because the law says it must, and everything about it is
narrower than its siblings:

* **the shortest role list in the app** — `System Manager` and `HR Manager`, and
  not `HR User`, who can keep the posted log without ever needing the names;
* **no year default that shows everything** — the year is required, because a
  screen that opens on every privacy case the company has ever had is a screen
  somebody leaves open on a shared desk;
* **the category, not the detail.** It carries the case number, the name and which
  of the six applies. It deliberately does not carry the injury description: the
  whole point of a privacy case is that the *nature* of it stays off the list
  people read, and a "helpful" description column would undo the rule in the one
  report that exists to honour it.

If you are reading this because you are adding a column: that is the thing to
think about twice.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate, today

DOCTYPE = "Safety Incident"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	year = cint(filters.get("year")) or getdate(today()).year

	rows = frappe.get_all(
		DOCTYPE,
		filters={
			"is_privacy_case": 1,
			"occurred_on": ["between", [f"{year}-01-01 00:00:00", f"{year}-12-31 23:59:59"]],
		},
		fields=["name as case", "employee_name", "occurred_on", "privacy_basis", "is_recordable"],
		order_by="occurred_on asc",
	)
	for row in rows:
		row["occurred_on"] = getdate(row["occurred_on"]) if row["occurred_on"] else None
	return _columns(), rows, _message(rows, year)


def _message(rows, year):
	if not rows:
		return _("No privacy cases in {0}.").format(year)
	return _(
		"<b>Confidential.</b> {0} case(s) in {1}. These names are kept off the posted 300 log by "
		"rule; this list is how a case is identified when it has to be."
	).format(len(rows), year)


def _columns():
	return [
		{"fieldname": "case", "label": _("Case"), "fieldtype": "Link", "options": DOCTYPE, "width": 140},
		{"fieldname": "employee_name", "label": _("Employee"), "fieldtype": "Data", "width": 200},
		{"fieldname": "occurred_on", "label": _("Date"), "fieldtype": "Date", "width": 110},
		{"fieldname": "privacy_basis", "label": _("Category"), "fieldtype": "Data", "width": 320},
		{"fieldname": "is_recordable", "label": _("On the 300 log"), "fieldtype": "Check", "width": 120},
	]
