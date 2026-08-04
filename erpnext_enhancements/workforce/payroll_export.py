# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""WP-8: per-employee, per-period hours in the payroll provider's own format.

The provider is **Shaw & Nielsen** (firm code ``SHAWA2530``, client code ``5813``)
and they consume a specific semi-monthly workbook. This module reproduces that
workbook from Job Interval data instead of it being typed by hand.

The layout below is transcribed from a real submitted file
(``5813 7-16-2026 to 7-31-2026``), not inferred from a description. Everything
about it — the six-line header block, the three-row stacked column header, the
trailing Totals row, the sheet name — is reproduced because a payroll clerk
matches this against what they already have, and a file that "contains the same
information" in a different shape costs more time than it saves.

## What this can and cannot compute — read before trusting the output

**It computes hours worked.** Completed Job Intervals, per employee, per period,
net of paused time, optionally broken down by project (the extra column the
business asked for on top of the provider's format).

**It does not compute the overtime split, and it must not.** The brief assumed
"rates are already configured in ERPNext" — they are not, and there is nowhere
for them to be. ``hrms`` is **not installed on this site**: there is no Salary
Structure doctype, no salary slips, no payroll module of any kind, and 0
Timesheets. So the ``Qualified OT`` and ``Overtime`` columns are emitted **blank**
for the provider to fill exactly as they do today.

That is a deliberate refusal, not an omission. ``Qualified OT`` is the federal
qualified-overtime figure; getting it wrong is a tax-reporting error, and
reimplementing FLSA premium arithmetic from scratch to save a payroll bureau a
calculation they already perform correctly is the worst trade available here.

Same reasoning for Bonus, Commission, Reimbursement and Services: nothing in
ERPNext currently holds them, so they are emitted blank rather than guessed.
Healthcare Stipend IS emitted, because it is a flat recurring amount that now has
a home on the Employee record.

## Salaried employees

Report a flat ``SALARIED_PERIOD_HOURS`` (86.67 = 2080 / 24) rather than clocked
time, matching the submitted file. A salaried person's Job Intervals are still
worth having — they feed the project-level column and job costing — but they are
not what payroll is paid from.

## The mapping problem

The provider keys on their own employee number (22, 7, 17, …), which is **not**
the ERPNext employee id. It lives in core ``Employee.employee_number``, which was
null for all 15 active employees; ``patches.seed_payroll_employee_numbers``
transcribes them from the submitted file. An employee with no number still
appears in the output, with a blank ``Emp Num`` and flagged in the report, because
silently dropping somebody from a payroll file is the single worst failure mode
this module has.

## Before anyone cuts over

The acceptance criterion is that this reconciles **exactly** against a manually
produced spreadsheet for a complete pay period. As of 2026-08-04 that cannot be
done: ``tabJob Interval`` has **0 rows** in production and ``tabTime Kiosk Log``
has 16. There is nothing to reconcile until the kiosk is actually in use for a
full period. Do not schedule a cutover against this until that has happened.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_last_day, getdate, nowdate

#: Provider identifiers, transcribed from the submitted workbook.
FIRM_NAME = "Shaw & Nielsen"
FIRM_CODE = "SHAWA2530"
CLIENT_CODE = "5813"
EMPLOYER_NAME = "Sapphire Fountains LLC"
PAY_SCHEDULE = "Twice a Month (Semi-Monthly)"

#: Flat hours reported for a salaried employee each semi-monthly period.
#: 2080 annual hours / 24 periods. Matches the submitted file exactly.
SALARIED_PERIOD_HOURS = 86.67

#: The three-row stacked column header, verbatim. Column order is the contract —
#: the provider's import maps by position, so nothing here may be reordered and
#: no column may be dropped, blank or not.
HEADER_ROWS = (
	("", "", "S/H", "Reg", "Qualified", "OT", "PTO", "Hol", "Bonus", "Comm", "Reimb", "AddlH", "HlthS", "Svcs"),
	("Emp", "Employee", "Salaried", "Regular", "OT", "Overtime", "PTO", "Holiday", "Bonus",
	 "Commission", "Reimbursement", "Hourly Pay", "Healthcare Stipend", "Services"),
	("Num", "Name", "/ Hourly", "Hours", "Hours", "Hours", "Hours", "Hours", "Amount",
	 "Amount", "Amount", "Hours", "Amount", "Amount"),
)

#: Columns summed on the Totals row (0-indexed), matching the submitted file:
#: everything numeric from Regular Hours rightwards.
TOTAL_COLUMNS = tuple(range(3, 14))


# ------------------------------------------------------------------ periods


def current_period(today=None):
	"""The semi-monthly period containing ``today`` as ``(from_date, to_date)``.

	1st-15th and 16th-end-of-month. ``get_last_day`` rather than a 30/31 lookup so
	February and leap years are not a special case.
	"""
	today = getdate(today or nowdate())
	if today.day <= 15:
		return today.replace(day=1), today.replace(day=15)
	return today.replace(day=16), getdate(get_last_day(today))


def previous_period(today=None):
	"""The period before the current one — the one actually being submitted."""
	start, _end = current_period(today)
	last_day_of_previous = add_days(start, -1)
	return current_period(last_day_of_previous)


def period_label(from_date, to_date):
	"""``7/16/2026 to 7/31/2026`` — the provider's own date format."""
	def fmt(value):
		value = getdate(value)
		return f"{value.month}/{value.day}/{value.year}"

	return f"{fmt(from_date)} to {fmt(to_date)}"


def sheet_name(from_date, to_date):
	"""``5813 7-16-2026 to 7-31-2026``, matching the submitted workbook's tab."""
	def fmt(value):
		value = getdate(value)
		return f"{value.month}-{value.day}-{value.year}"

	return f"{CLIENT_CODE} {fmt(from_date)} to {fmt(to_date)}"


# -------------------------------------------------------------------- hours


def worked_hours(from_date, to_date, employee=None):
	"""Net worked hours per (employee, project) from Completed Job Intervals.

	Net of ``total_paused_seconds`` — a lunch break is not worked time, and the
	kiosk records it explicitly rather than leaving it inside the interval.

	Bounded by ``start_time`` within the period. An interval that spans midnight
	into the next period is credited whole to the period it started in, which is
	how the manual sheet has always treated an overnight callout. Splitting it
	would be defensible but would silently disagree with history, and this module
	has to reconcile against history before anything else.
	"""
	conditions = ["ji.status = 'Completed'", "ji.start_time >= %(from_date)s",
	              "DATE(ji.start_time) <= %(to_date)s", "ji.end_time IS NOT NULL"]
	values = {"from_date": from_date, "to_date": to_date}
	if employee:
		conditions.append("ji.employee = %(employee)s")
		values["employee"] = employee

	return frappe.db.sql(
		f"""
		SELECT
			ji.employee,
			ji.project,
			SUM(GREATEST(
				TIMESTAMPDIFF(SECOND, ji.start_time, ji.end_time)
					- COALESCE(ji.total_paused_seconds, 0),
				0
			)) / 3600.0 AS hours
		FROM `tabJob Interval` ji
		WHERE {" AND ".join(conditions)}
		GROUP BY ji.employee, ji.project
		""",
		values,
		as_dict=True,
	)


def build_rows(from_date, to_date, include_projects=False):
	"""One dict per employee (plus per-project rows when asked), in the provider's
	name order.

	Every active employee appears, worked hours or not — a zero row is
	information ("this person did not clock in"), while a missing row is a
	payroll error waiting to happen.
	"""
	if frappe.db.table_exists("Job Interval"):
		buckets = worked_hours(from_date, to_date)
	else:
		buckets = []

	by_employee = {}
	for row in buckets:
		by_employee.setdefault(row.employee, []).append(row)

	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["name", "employee_name", "employee_number",
		        "last_name", "first_name", "middle_name"] + _optional_employee_fields(),
		limit=500,
	)

	rows = []
	for employee in sorted(employees, key=_provider_sort_key):
		salaried = (employee.get("custom_payroll_classification") or "Hourly") == "Salaried"
		projects = by_employee.get(employee.name, [])
		clocked = sum(flt(p.hours) for p in projects)

		rows.append({
			"employee": employee.name,
			"emp_num": employee.get("employee_number") or "",
			"employee_label": _provider_name(employee),
			"classification": "S" if salaried else "H",
			# Salaried people are paid the flat figure regardless of what the
			# clock says. Their clocked hours are still reported below, per
			# project, because job costing wants them.
			"regular_hours": SALARIED_PERIOD_HOURS if salaried else round(clocked, 2),
			"clocked_hours": round(clocked, 2),
			"healthcare_stipend": flt(employee.get("custom_healthcare_stipend") or 0),
			"unmapped": not employee.get("employee_number"),
			"project": "",
			"is_employee_row": True,
		})

		if include_projects:
			for project in sorted(projects, key=lambda p: flt(p.hours), reverse=True):
				rows.append({
					"employee": employee.name,
					"emp_num": "",
					"employee_label": "",
					"classification": "",
					"regular_hours": round(flt(project.hours), 2),
					"clocked_hours": round(flt(project.hours), 2),
					"healthcare_stipend": 0,
					"unmapped": False,
					"project": project.project or _("(no project)"),
					"is_employee_row": False,
				})

	return rows


def _optional_employee_fields():
	"""Custom fields that may not exist yet on a bench that has not migrated."""
	optional = []
	for fieldname in ("custom_payroll_classification", "custom_healthcare_stipend"):
		try:
			if frappe.db.has_column("Employee", fieldname):
				optional.append(fieldname)
		except Exception:
			pass
	return optional


def _provider_name(employee):
	"""``Symanski  Lisa J.`` — last name, TWO spaces, first name, middle initial.

	The double space is not a typo in the source file; it is how the provider's
	own export writes it, and a clerk eyeballing two files side by side notices
	single-spaced names as a difference. Reproduced deliberately.
	"""
	last = (employee.get("last_name") or "").strip()
	first = (employee.get("first_name") or "").strip()
	middle = (employee.get("middle_name") or "").strip()

	if not last and not first:
		return employee.get("employee_name") or employee.name

	initial = f" {middle[0]}." if middle else ""
	return f"{last}  {first}{initial}".strip()


def _provider_sort_key(employee):
	"""Alphabetical by last name, as the submitted file is ordered."""
	return ((employee.get("last_name") or employee.get("employee_name") or "").lower(),
	        (employee.get("first_name") or "").lower())


# ------------------------------------------------------------------ workbook


def workbook_rows(from_date, to_date, pay_date=None, include_projects=False):
	"""The complete sheet as a list of rows, header block included.

	Kept separate from the xlsx writer so the layout is testable without a
	spreadsheet library — which matters, because the layout IS the deliverable.
	"""
	rows = [
		["Firm:", "", FIRM_NAME, "", "", "Firm Code:", "", FIRM_CODE],
		["Employer:", "", EMPLOYER_NAME, "", "", "Client Code:", "", CLIENT_CODE],
		["Pay Period:", "", period_label(from_date, to_date)],
		["Pay Schedule:", "", PAY_SCHEDULE],
		[],
		["Pay Date:", "", str(getdate(pay_date)) if pay_date else ""],
		[],
	]
	rows.extend([list(header) for header in HEADER_ROWS])

	data = build_rows(from_date, to_date, include_projects=include_projects)
	totals = [0.0] * 14

	for row in data:
		line = [""] * 14
		line[0] = row["emp_num"]
		line[1] = row["employee_label"] if row["is_employee_row"] else f"    {row['project']}"
		line[2] = row["classification"]
		line[3] = row["regular_hours"] or ""
		# Columns 4-11 (Qualified OT, Overtime, PTO, Holiday, Bonus, Commission,
		# Reimbursement, Additional Hourly) are left blank ON PURPOSE — see the
		# module docstring. There is no payroll module on this site to derive them
		# from, and guessing a qualified-overtime figure is a tax-reporting error.
		if row["is_employee_row"] and row["healthcare_stipend"]:
			line[12] = row["healthcare_stipend"]

		if row["is_employee_row"]:
			for index in TOTAL_COLUMNS:
				totals[index] += flt(line[index] or 0)

		rows.append(line)

	total_line = ["", "Totals", ""] + [round(totals[i], 2) for i in TOTAL_COLUMNS]
	rows.append(total_line)
	return rows


@frappe.whitelist()
def download_payroll_workbook(from_date=None, to_date=None, pay_date=None, include_projects=0):
	"""Whitelisted: stream the period's workbook as .xlsx.

	Role-gated rather than permission-gated on a doctype: the file contains every
	employee's hours, which is not something a general Employee reader should be
	able to pull.
	"""
	if not _may_export():
		frappe.throw(_("You are not permitted to export payroll hours."), frappe.PermissionError)

	if not (from_date and to_date):
		from_date, to_date = previous_period()

	from frappe.utils.xlsxutils import make_xlsx

	rows = workbook_rows(from_date, to_date, pay_date=pay_date,
	                     include_projects=cint(include_projects))
	name = sheet_name(from_date, to_date)

	xlsx = make_xlsx(rows, name)
	frappe.response["filename"] = f"{name}.xlsx"
	frappe.response["filecontent"] = xlsx.getvalue()
	frappe.response["type"] = "binary"


def _may_export():
	return bool({"System Manager", "HR Manager", "Accounts Manager"}.intersection(frappe.get_roles()))
