# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Payroll Hours Export — the on-screen view of the Shaw & Nielsen workbook.

Same columns, same order, same period logic as ``workforce/payroll_export.py``,
which is where all the arithmetic and every decision about the format lives. This
is the desk surface: it exists so a human can eyeball the numbers, spot the
employee whose hours look wrong, and fix it BEFORE the file goes to the provider.

The "Download Workbook" button on this report is the actual deliverable — it
produces the .xlsx with the provider's six-line header block, which a Query
Report's own Excel export cannot.

Two columns exist only for review and are not in the provider's format:

* **Clocked** — what the time clock actually recorded. For an hourly employee it
  equals Regular; for a salaried one it will not, and that difference is the
  point (86.67 is reported, the clock says whatever it says).
* **Project** — the breakdown the business asked for on top of the provider's
  format. Indented sub-rows under each employee when switched on.

Blank OT columns are not a bug. See the module docstring in ``payroll_export.py``:
``hrms`` is not installed on this site, there is no Salary Structure to derive a
premium from, and inventing a qualified-overtime figure is a tax-reporting error.
"""

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements.workforce import payroll_export


def execute(filters=None):
	filters = frappe._dict(filters or {})

	from_date = filters.get("from_date")
	to_date = filters.get("to_date")
	if not (from_date and to_date):
		from_date, to_date = payroll_export.previous_period()

	rows = payroll_export.build_rows(from_date, to_date,
	                                 include_projects=cint(filters.get("include_projects")))
	data = [_shape(row) for row in rows]

	return get_columns(), data, _message(rows, from_date, to_date), None


def get_columns():
	return [
		{"label": _("Emp Num"), "fieldname": "emp_num", "fieldtype": "Data", "width": 90},
		{"label": _("Employee"), "fieldname": "employee_label", "fieldtype": "Data", "width": 220},
		{"label": _("S/H"), "fieldname": "classification", "fieldtype": "Data", "width": 60},
		{"label": _("Regular Hours"), "fieldname": "regular_hours", "fieldtype": "Float", "precision": 2, "width": 120},
		{"label": _("Qualified OT"), "fieldname": "qualified_ot", "fieldtype": "Data", "width": 110},
		{"label": _("Overtime"), "fieldname": "overtime", "fieldtype": "Data", "width": 100},
		{"label": _("PTO"), "fieldname": "pto", "fieldtype": "Data", "width": 80},
		{"label": _("Holiday"), "fieldname": "holiday", "fieldtype": "Data", "width": 80},
		{"label": _("Healthcare Stipend"), "fieldname": "healthcare_stipend", "fieldtype": "Currency", "width": 150},
		# --- review only, not part of the provider's file ---
		{"label": _("Clocked"), "fieldname": "clocked_hours", "fieldtype": "Float", "precision": 2, "width": 100},
		{"label": _("Project"), "fieldname": "project", "fieldtype": "Link", "options": "Project", "width": 200},
		{"label": _("Employee Record"), "fieldname": "employee", "fieldtype": "Link", "options": "Employee", "width": 150},
	]


def _shape(row):
	return {
		"emp_num": row["emp_num"],
		"employee_label": row["employee_label"] if row["is_employee_row"] else "",
		"classification": row["classification"],
		"regular_hours": row["regular_hours"] or 0,
		# Emitted blank, exactly as they go to the provider.
		"qualified_ot": "",
		"overtime": "",
		"pto": "",
		"holiday": "",
		"healthcare_stipend": row["healthcare_stipend"] or 0,
		"clocked_hours": row["clocked_hours"] or 0,
		"project": row["project"] or "",
		"employee": row["employee"] if row["is_employee_row"] else "",
	}


def _message(rows, from_date, to_date):
	"""Warn about the two things that make an export untrustworthy."""
	notes = [
		_("Period: {0}. Overtime, PTO, Holiday, Bonus, Commission and Reimbursement are submitted blank — "
		  "this site has no payroll module (hrms is not installed), so they stay with the provider.").format(
			payroll_export.period_label(from_date, to_date)
		)
	]

	unmapped = [r["employee"] for r in rows if r.get("is_employee_row") and r.get("unmapped")]
	if unmapped:
		notes.append(
			_("<b>{0} employee(s) have no payroll Emp Num</b> and will export with a blank first column: {1}. "
			  "Set Employee → employee_number before submitting.").format(len(unmapped), ", ".join(unmapped))
		)

	if not frappe.db.count("Job Interval"):
		notes.append(
			_("<b>No Job Intervals exist on this site yet.</b> Every hour figure below is zero because the "
			  "time kiosk has not been used, not because nobody worked. Do not reconcile against a manual "
			  "sheet until a full pay period has been clocked.")
		)

	return "<br><br>".join(notes)
