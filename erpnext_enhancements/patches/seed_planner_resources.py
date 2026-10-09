"""Seed the Project Planner's people from the Employee list (v1.577.0).

The planner books hours against **Planner Resources**, not Employees (a subcontractor crew has no
Employee at all), so on day one the page would be an empty grid. This creates a resource for each
active employee the planner is meant to book, so Nik opens it and sees the field crew, the PMs and
the designers already there:

* designation contains "Technician" -> group **Field**, home team **Maintenance**
* designation is "Project Manager"  -> group **PM**, home team **Projects**
* department starts with "Design"   -> group **Design**, home team **Projects**

Checked in that order, so a technician in a Design department is still Field. The match is
case-insensitive. Anyone else (the office, sales, accounting) gets no resource and never appears
on the planner; add one by hand if that changes.

**No work-pattern rows.** A resource with none works Monday to Friday at the Project Planner
Settings' full-day hours, which is right for most people. Austin's Monday-to-Wednesday week is Nik's
to enter in the Desk, deliberately: guessing a person's schedule here would be a fact nobody gave us.

**Insert-only and keyed on employee.** An employee who already has a resource (active or not) is
skipped, so a person Nik retired or re-grouped by hand is never recreated or reverted. Safe twice.

``select_resource_group`` is a pure function of two strings so the rule is testable without a
bench. The patch **cannot raise**: one that does aborts ``bench migrate``, which on this repo is
the deploy. It returns quietly when the Planner Resource table does not exist yet, and a failure on
one employee is logged without stopping the rest.
"""

import frappe

#: (designation fragment, group, home team), checked in order.
GROUP_BY_DESIGNATION = (("technician", "Field", "Maintenance"),)
PROJECT_MANAGER = "project manager"


def select_resource_group(designation, department):
	"""Return ``(group, home_team)`` for an employee, or None when the planner should not book them."""
	designation = (designation or "").strip().lower()
	department = (department or "").strip().lower()
	for fragment, group, home_team in GROUP_BY_DESIGNATION:
		if fragment in designation:
			return group, home_team
	if designation == PROJECT_MANAGER:
		return "PM", "Projects"
	if department.startswith("design"):
		return "Design", "Projects"
	return None


def execute():
	try:
		if not frappe.db.table_exists("Planner Resource"):
			return

		have = {
			row.get("employee")
			for row in frappe.get_all("Planner Resource", fields=["employee"])
			if row.get("employee")
		}
		employees = frappe.get_all(
			"Employee",
			filters={"status": "Active"},
			fields=["name", "employee_name", "designation", "department", "user_id"],
		)
		created = 0
		for emp in employees:
			if emp.name in have:
				continue
			choice = select_resource_group(emp.get("designation"), emp.get("department"))
			if not choice:
				continue
			group, home_team = choice
			try:
				doc = frappe.new_doc("Planner Resource")
				doc.resource_name = emp.employee_name or emp.name
				doc.resource_type = "Employee"
				doc.employee = emp.name
				doc.user = emp.get("user_id")
				doc.resource_group = group
				doc.home_team = home_team
				doc.is_active = 1
				doc.insert(ignore_permissions=True)
				created += 1
			except Exception:
				frappe.log_error(
					title="Planner resource seed",
					message=f"seed_planner_resources failed for {emp.name}\n{frappe.get_traceback()}",
				)
		if created:
			frappe.db.commit()
	except Exception:
		try:
			frappe.db.rollback()
			frappe.log_error(
				title="Planner resource seed",
				message=f"seed_planner_resources failed\n{frappe.get_traceback()}",
			)
		except Exception:
			# The database went away mid-migrate; a raise here would only hide the real failure.
			pass
