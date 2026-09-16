"""Give each Position a Department, so training can be grouped by the part of the business it is for.

``Position`` has carried a ``department`` field all along and **19 of the 20 positions on production
had it empty** — only *Operations Manager* was set. Nothing depended on it, so nothing filled it in.

The learner rail now groups the catalogue by department, and that grouping is derived: a course's
assignment rule names a Department directly, or names a Position and the department comes from here.
Without this the ten technician courses — all assigned to Position *Technician* — would file under
nothing, and a "Production" heading would be empty on the one screen it exists to serve.

**This is a judgement about the org chart, not a derivation.** Nothing in the data says a Technician
is in Production; somebody has to. The mapping below was written for review and is trivially
changed: edit the table, re-run, and the rail regroups. Positions absent from it are left alone.

Two things it will not do
-------------------------

**It never overwrites a department somebody has set.** *Operations Manager* already read
*Operations* and still does. Only an empty field is filled, so a correction made in the Desk
survives the next migrate — the same rule ``training/setup.py`` keeps for categories and badges.

**It never invents a Department.** A mapping whose target does not exist on this site is skipped and
named. Creating org structure from a patch is how a tidy-up ends up inventing a department nobody
agreed to.
"""

import frappe

DOCTYPE = "Position"

#: ``Position -> Department``, as they exist on production 2026-09-15.
#:
#: The four technician grades go to **Production** together: the Technician position is the job
#: family above Junior / Senior / Master, and a learner in any of them is doing the same work for
#: the same part of the business. That is also what makes the ten technician modules appear under
#: one heading rather than four.
POSITION_DEPARTMENT = {
	"Technician": "Production",
	"Junior Technician": "Production",
	"Senior Technician": "Production",
	"Master Technician": "Production",
	"Designer": "Design - SF",
	"Junior Designer": "Design - SF",
	"Electrical Designer": "Design - SF",
	"Engineer": "Design - SF",
	"Chief Executive Officer": "Executive - SF",
	"HR Manager": "HR - SF",
	"Marketing Specialist": "Marketing",
	"Operations Manager": "Operations",
	"Project Manager": "Operations",
	"Purchasing Agent/Inventory Clerk": "Operations",
	"Sales Representative": "Sales - SF",
	"AP/AR, Purchasing Manager": "Finance - SF",
	"Finance & Accounting Manager": "Finance - SF",
	"Software Engineer": "Product Management - SF",
	"Internal Systems Manager": "Product Management - SF",
}

#: Left deliberately unset. `All Positions` is the root of the tree rather than a job anybody holds,
#: and giving it a department would file every course that targets it under one arbitrary heading.
UNASSIGNED = ("All Positions",)


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy.
	if not frappe.db.exists("DocType", DOCTYPE):
		return
	if not frappe.db.has_column(DOCTYPE, "department"):
		return

	filled = []
	kept = []
	missing = []

	for position, department in POSITION_DEPARTMENT.items():
		if not frappe.db.exists(DOCTYPE, position):
			continue
		if not frappe.db.exists("Department", department):
			# Never create org structure from a patch.
			missing.append(f"{position}: no Department called {department}")
			continue
		current = frappe.db.get_value(DOCTYPE, position, "department")
		if current:
			# Somebody has already answered this. A correction made in the Desk outlives the
			# next migrate, which is the whole reason this is fill-only.
			kept.append(f"{position} is already {current}")
			continue
		frappe.db.set_value(DOCTYPE, position, "department", department)
		filled.append(f"{position} -> {department}")

	if filled:
		frappe.db.commit()

	print(
		f"set_position_departments: {len(filled)} filled, {len(kept)} already set, "
		f"{len(missing)} skipped"
	)
	for line in filled:
		print(f"  set: {line}")
	for line in kept:
		print(f"  kept: {line}")
	for line in missing:
		print(f"  skipped: {line}")
