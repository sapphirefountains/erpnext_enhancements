# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Labor Cost Analysis — hours and burdened labour cost from the time clock, grouped the
way finance asks for it.

The costing question WI-016 wanted answered — *what did the labour on this job cost?* —
finally has data behind it in v1.480.0: every completed ``Job Interval`` carries the pay
rate in force on the day it started, the burden, and ``labor_cost`` (see
``workforce/costing.py`` and ADR 0013). This report is the reading surface. It sums those
stamps over a period and groups them by **Project**, **Employee**, **Position**,
**Activity Type** or **Project and Employee**.

Three things it does on purpose:

* **It reads the stamped cost, never a live rate.** ``labor_cost`` was fixed when the
  interval closed, from the rate in force that day. Re-pricing history from today's rate
  would move every past number on the day of a pay review, which is exactly the property
  an analysis of the past must not have. The one derived money column, ``straight_pay``,
  is hours × the *stamped* ``pay_rate`` for the same reason.
* **Unrated intervals are counted, not hidden.** An interval closed before the employee had
  a pay-rate row carries no cost. Summing it in as zero understates the job silently; the
  ``unrated`` column says how many rows are doing that, and the JS paints it orange.
* **Budget appears only when grouped by Project.** The ``Labor`` budget line is a fact about
  a project, and repeating it against every employee row would sum to nonsense on the
  Totals row. The line is found by *what it declares* — a ``Project Budget Category``
  whose ``actual_source`` is Timesheets — rather than by the name "Labor", which is a
  label somebody can rename.

Hours are net of pauses and an interval is credited whole to the day it starts, the same
rule ``payroll_export.worked_hours`` applies, so this report and the payroll workbook agree
on any period. Default window is the last 30 days.

Roles are the three that read Job Interval at permlevel 1, ADR 0013's pay audience (System
Manager, HR Manager, Accounts Manager). Projects Manager reads Job Interval but not its pay
block, and this report's SQL would bypass that: a raw query applies no permlevel, so the
report's roles are the only gate on the rates it prints (v1.538.0). Every one of the three
also reads Job Interval at permlevel 0, because a Script Report whose readers cannot read its
``ref_doctype`` errors for exactly its intended audience
(``tests/test_workforce_report_labor_cost.py`` and ``tests/test_pay_rate_permissions.py`` pin
the set).
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, nowdate

#: The grouping the JS filter offers, in order. The test asserts the JS options equal this.
GROUP_BY = ("Project", "Employee", "Position", "Activity Type", "Project and Employee")

#: Column spec as plain literals so a bench-free test can read it with ``ast``:
#: (fieldname, label, fieldtype, options, width, groupings the column appears in — () = all).
COLUMNS = (
	("project", "Project", "Link", "Project", 160, ("Project", "Project and Employee")),
	("project_name", "Project Name", "Data", "", 200, ("Project", "Project and Employee")),
	("employee", "Employee", "Link", "Employee", 140, ("Employee", "Project and Employee")),
	("employee_name", "Employee Name", "Data", "", 170, ("Employee", "Project and Employee")),
	("position", "Position", "Link", "Position", 170, ("Position",)),
	("position_tier", "Tier", "Int", "", 60, ("Position",)),
	("time_category", "Activity Type", "Link", "Activity Type", 160, ("Activity Type",)),
	("intervals", "Intervals", "Int", "", 90, ()),
	("hours", "Hours", "Float", "", 90, ()),
	("avg_rate", "Avg Burdened Rate", "Currency", "", 140, ()),
	("labor_cost", "Labor Cost", "Currency", "", 130, ()),
	("straight_pay", "Straight-time Pay", "Currency", "", 140, ()),
	("unrated", "Unrated", "Int", "", 80, ()),
	("budget", "Labor Budget", "Currency", "", 130, ("Project",)),
	("variance", "Budget Variance", "Currency", "", 140, ("Project",)),
)

#: Per grouping: the Job Interval / joined columns selected and grouped on.
_GROUP_KEYS = {
	"Project": ("ji.project", "p.project_name"),
	"Employee": ("ji.employee", "e.employee_name"),
	"Position": ("ji.position", "ji.position_tier"),
	"Activity Type": ("ji.time_category",),
	"Project and Employee": ("ji.project", "p.project_name", "ji.employee", "e.employee_name"),
}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	group_by = filters.get("group_by") or GROUP_BY[0]
	if group_by not in GROUP_BY:
		frappe.throw(_("Unknown grouping: {0}").format(group_by))

	if not _cost_fields_exist():
		# The v1.480.0 schema has not synced on this bench. An empty report beats a 500.
		return get_columns(group_by), [], _("Labor cost fields are not installed on this site yet."), None

	data = get_data(filters, group_by)
	return get_columns(group_by), data, None, get_chart(data, group_by)


def _cost_fields_exist():
	try:
		return frappe.db.has_column("Job Interval", "labor_cost")
	except Exception:
		return False


def get_columns(group_by):
	columns = []
	for fieldname, label, fieldtype, options, width, groupings in COLUMNS:
		if groupings and group_by not in groupings:
			continue
		column = {"label": _(label), "fieldname": fieldname, "fieldtype": fieldtype, "width": width}
		if options:
			column["options"] = options
		columns.append(column)
	return columns


def get_data(filters, group_by):
	from_date = filters.get("from_date") or add_days(nowdate(), -30)
	to_date = filters.get("to_date") or nowdate()

	conditions = [
		"ji.status = 'Completed'",
		"ji.end_time is not null",
		"date(ji.start_time) between %(from_date)s and %(to_date)s",
	]
	values = {"from_date": from_date, "to_date": to_date}
	for fieldname in ("project", "employee", "position", "time_category"):
		if filters.get(fieldname):
			conditions.append(f"ji.{fieldname} = %({fieldname})s")
			values[fieldname] = filters.get(fieldname)

	keys = _GROUP_KEYS[group_by]
	select_keys = ", ".join(f"{key} as {key.split('.')[1]}" for key in keys)
	worked = "greatest(timestampdiff(second, ji.start_time, ji.end_time) - coalesce(ji.total_paused_seconds, 0), 0)"

	rows = frappe.db.sql(
		f"""
		select
			{select_keys},
			count(*) as intervals,
			sum({worked}) / 3600.0 as hours,
			sum(coalesce(ji.labor_cost, 0)) as labor_cost,
			sum({worked} / 3600.0 * coalesce(ji.pay_rate, 0)) as straight_pay,
			sum(case when coalesce(ji.pay_rate, 0) = 0 then 1 else 0 end) as unrated
		from `tabJob Interval` ji
		left join `tabProject` p on p.name = ji.project
		left join `tabEmployee` e on e.name = ji.employee
		where {" AND ".join(conditions)}
		group by {", ".join(keys)}
		order by labor_cost desc, hours desc
		""",
		values,
		as_dict=True,
	)

	budgets = _labor_budgets([r.project for r in rows if r.get("project")]) if group_by == "Project" else {}

	for row in rows:
		row["hours"] = round(flt(row.hours), 2)
		row["labor_cost"] = round(flt(row.labor_cost), 2)
		row["straight_pay"] = round(flt(row.straight_pay), 2)
		row["avg_rate"] = round(row["labor_cost"] / row["hours"], 2) if row["hours"] else 0.0
		if group_by == "Project":
			budget = budgets.get(row.get("project"))
			row["budget"] = round(flt(budget), 2) if budget is not None else None
			row["variance"] = round(flt(budget) - row["labor_cost"], 2) if budget is not None else None
	return rows


def _labor_budgets(projects):
	"""``{project: budgeted_amount}`` for the budget lines whose category records its
	actuals from Timesheets — the budget model's own definition of labour. Projects with no
	such line are absent, and the report shows a blank rather than a zero, because a zero
	reads as "budgeted nothing" and a blank as "no labour budget was set"."""
	if not projects:
		return {}
	try:
		if not (frappe.db.table_exists("Project Budget Line") and frappe.db.table_exists("Project Budget Category")):
			return {}
	except Exception:
		return {}
	rows = frappe.db.sql(
		"""
		select bl.parent as project, sum(coalesce(bl.budgeted_amount, 0)) as budget
		from `tabProject Budget Line` bl
		join `tabProject Budget Category` c on c.name = bl.category
		where bl.parenttype = 'Project'
		  and bl.parent in %(projects)s
		  and c.actual_source = 'Timesheets'
		group by bl.parent
		""",
		{"projects": tuple(set(projects))},
		as_dict=True,
	)
	return {r.project: flt(r.budget) for r in rows}


def get_chart(data, group_by):
	if not data:
		return None
	label_field = {
		"Project": "project_name",
		"Employee": "employee_name",
		"Position": "position",
		"Activity Type": "time_category",
		"Project and Employee": "employee_name",
	}[group_by]
	top = data[:15]
	return {
		"data": {
			"labels": [str(r.get(label_field) or r.get("project") or r.get("employee") or "") for r in top],
			"datasets": [{"name": _("Labor Cost"), "values": [r.get("labor_cost") or 0 for r in top]}],
		},
		"type": "bar",
		"colors": ["#2490ef"],
	}
