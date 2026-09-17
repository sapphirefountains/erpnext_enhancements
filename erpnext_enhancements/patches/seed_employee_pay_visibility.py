"""Permission level 1 on Employee for HR Manager, System Manager and Accounts Manager — insert-only.

v1.480.0 puts pay on the Employee: the ``custom_pay_rates`` table (fixture Custom Field) is
declared at **permlevel 1**, so only a role holding a permlevel-1 ``read`` on Employee can see
it. On prod, HR Manager and System Manager already hold that row — as **Custom DocPerm** rows
made on the site, not in any fixture (verified live 2026-09-17). Accounts Manager holds
nothing, and job costing is their job. So this seeds the permlevel-1 read for all three,
keyed on ``(parent = "Employee", role, permlevel = 1)``, creating only what is missing: the
two existing site rows are found and left alone, not duplicated; on a fresh install all three
are created.

**Why ``setup_custom_perms`` runs first.** ``Meta.get_permissions`` is
``custom_perms or self.permissions`` — the moment ANY Custom DocPerm row exists for a doctype,
the Custom DocPerm rows are the *entire* effective permission set and the standard DocPerms
are ignored. Prod is already on Custom DocPerm for Employee, so adding a row there is safe;
a fresh install is not, and inserting three permlevel-1 rows into an empty set would leave
Employee with no permlevel-0 permissions at all. ``frappe.permissions.setup_custom_perms``
copies the standard rows across first when — and only when — none exist. That is exactly
what the Role Permission Manager does before it adds a rule.

A patch and not a ``custom_docperm.json`` fixture entry: the fixture's ``parent in`` filter
captures a doctype's rows **wholesale** (Material Request and Purchase Order, WI-012), and
Employee's permission set is site-owned with rows this repo does not version. Insert-only
so a human's later edit survives.

Never raises. Safe twice.
"""

import frappe

PARENT = "Employee"
PERMLEVEL = 1
ROLES = ("HR Manager", "System Manager", "Accounts Manager")


def execute() -> None:
	try:
		created = seed_employee_pay_visibility()
		print(f"Employee permlevel-1 read: {created} row(s) created")
	except Exception:
		frappe.log_error(title="seed_employee_pay_visibility")


def seed_employee_pay_visibility() -> int:
	if not frappe.db.exists("DocType", PARENT):
		return 0

	from frappe.permissions import setup_custom_perms

	setup_custom_perms(PARENT)

	created = 0
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			continue
		if frappe.db.exists("Custom DocPerm", {"parent": PARENT, "role": role, "permlevel": PERMLEVEL}):
			continue
		frappe.get_doc({
			"doctype": "Custom DocPerm",
			"parent": PARENT,
			"parenttype": "DocType",
			"parentfield": "permissions",
			"role": role,
			"permlevel": PERMLEVEL,
			"read": 1,
			"write": 1 if role in ("HR Manager", "System Manager") else 0,
		}).insert(ignore_permissions=True)
		created += 1

	if created:
		frappe.clear_cache(doctype=PARENT)
	return created
