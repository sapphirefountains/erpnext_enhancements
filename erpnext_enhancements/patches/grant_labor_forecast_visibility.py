"""Permission level 2 on Project for the cost roles: who may read a budget line's labor forecast.

Project Planner Phase 4 (P4.2) adds ``Project Budget Line.labor_forecast``: the project's labor at
each person's pay rate. With one person on a job that total *is* their wage, so it is shown only
to the cost roles (``planner_tracking.COST_ROLES``: System Manager, Finance Team, Executive Team,
Estimator, HR Manager), and only the ones that exist on the site.

**Why level 2, not 1.** A child table's field answers to its parent's DocPerms, and ERPNext v16's
own Project permissions grant ``read`` at **permlevel 1 to Desk User**, which every desk user
holds (``erpnext/projects/doctype/project/project.json``). A field at level 1 on Project is
therefore readable by everybody who can open a Project; level 2 is held by nobody until this
patch grants it.

**Why ``setup_custom_perms`` runs first** (as in ``seed_employee_pay_visibility``).
``Meta.set_custom_permissions`` replaces a doctype's standard DocPerms with its Custom DocPerm
rows the moment any exist, so inserting level-2 rows into an empty set would leave Project with
no level-0 permissions at all. ``frappe.permissions.setup_custom_perms`` copies the standard rows
across first, when and only when none exist; on a site already on Custom DocPerm for Project it
does nothing. A patch and not a ``custom_docperm.json`` fixture, because that fixture captures a
doctype's rows wholesale and Project's permission set is site-owned.

Insert-only, keyed on ``(parent = "Project", role, permlevel = 2)``, read only (the field is
computed, never typed). Never raises. Safe twice.
"""

import frappe

PARENT = "Project"
PERMLEVEL = 2
#: planner_tracking.COST_ROLES, restated so a patch never imports the planner.
#: tests/test_planner_phase4.py holds the two equal.
ROLES = ("System Manager", "Finance Team", "Executive Team", "Estimator", "HR Manager")


def execute() -> None:
	try:
		created = grant_labor_forecast_visibility()
		print(f"Project permlevel-{PERMLEVEL} read (labor forecast): {created} row(s) created")
	except Exception:
		frappe.log_error(title="grant_labor_forecast_visibility", message=frappe.get_traceback())


def grant_labor_forecast_visibility() -> int:
	if not frappe.db.exists("DocType", PARENT):
		return 0

	from frappe.permissions import setup_custom_perms

	roles = [role for role in ROLES if frappe.db.exists("Role", role)]
	if not roles:
		return 0
	setup_custom_perms(PARENT)

	created = 0
	for role in roles:
		if frappe.db.exists("Custom DocPerm", {"parent": PARENT, "role": role, "permlevel": PERMLEVEL}):
			continue
		frappe.get_doc(
			{
				"doctype": "Custom DocPerm",
				"parent": PARENT,
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": role,
				"permlevel": PERMLEVEL,
				"read": 1,
				"write": 0,
			}
		).insert(ignore_permissions=True)
		created += 1

	if created:
		frappe.clear_cache(doctype=PARENT)
	return created
