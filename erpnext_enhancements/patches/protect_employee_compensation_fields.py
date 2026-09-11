# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Give HR the permission level the compensation fields were just moved to.

The Property Setters in `fixtures/property_setter.json` raise ten Employee fields
— cost to company, the healthcare stipend, bank details, passport number, health
details, blood group — from permlevel 0 to permlevel 1. **On its own that hides
them from everybody, including HR**, because no role on this site holds any
permission at level 1. This patch adds the two that should.

**A patch and not a fixture, deliberately.** `Custom DocPerm` is the one table
where the repo's usual fixture habit is dangerous: `setup_custom_perms` copies the
standard permission set wholesale the first time a doctype is customised, and the
six rows already on prod (Employee, Employee Self Service, HR Manager, HR User,
Project User, Projects User) are **not** in this repo's fixture file. Managing two
new rows through a file that does not know about the other six is how the other
six get lost. `frappe.permissions.add_permission` is the framework's own API for
this, it calls `setup_custom_perms` itself, and it is a no-op when the rule
already exists.

**What was actually verified before writing any of this** (prod, 2026-09-11), and
it matters because it refutes half of the finding that prompted it:

* every one of Employee's 119 fields sat at permlevel 0 — no field-level
  protection of any kind;
* `Custom DocPerm` grants the `Employee` role read **and write** at level 0;
* **but 19 `User Permission` rows scope each person to their own Employee record**,
  so a colleague cannot open anybody else's. The claim that they could is wrong.

What is true is narrower and still worth fixing: the entire protection rested on
those nineteen rows being correct and complete — one deleted row away from being
gone — and an employee could edit their **own** cost-to-company, because
self-service write sat at level 0 with nothing above it.

No field touched here holds any data today, so this closes nothing that is
currently leaking. That is the right time to do it: before somebody types a bank
account into a field with no protection above it.

`date_of_birth` is deliberately left at level 0. It is the one field in this group
with real data (16 of 16), its sensitivity is far below a bank account's, and
raising it would hide it from the person themselves.
"""

import frappe

DOCTYPE = "Employee"

#: Roles that may read and write the Employee fields at permlevel 1.
#: Deliberately NOT `HR User`: the two people who hold `HR Manager` are the two
#: who would ever need a bank account number, and everybody on this site holds
#: `HR User` anyway — it is one of the twenty-one roles all sixteen staff carry,
#: so granting it here would be the same as granting it to everyone.
ROLES = ("HR Manager", "System Manager")

PERMLEVEL = 1


def execute():
	grant_employee_field_permissions()


def grant_employee_field_permissions():
	"""Idempotent, and registered as an `after_migrate` hook as well as a patch.

	The hook matters for the usual ordering reason: the Property Setters that
	create permlevel 1 are FIXTURES, and `sync_fixtures()` runs in
	`post_schema_updates()` — after the post-model-sync patches. On the migrate
	that introduces them the fields are still at level 0 when this patch runs, so
	the grant would be made against a level nothing uses; harmless, but the hook is
	what makes it land in the same deploy rather than the next one.

	Never raises. A permission grant that fails is a thing to fix by hand, not a
	reason to abort a migrate half way through.
	"""
	try:
		from frappe.permissions import add_permission

		granted = []
		for role in ROLES:
			if not frappe.db.exists("Role", role):
				continue
			if frappe.db.exists(
				"Custom DocPerm",
				{"parent": DOCTYPE, "role": role, "permlevel": PERMLEVEL, "if_owner": 0},
			):
				continue
			add_permission(DOCTYPE, role, PERMLEVEL)
			granted.append(role)

		if granted:
			print(
				f"[erpnext_enhancements] granted level-{PERMLEVEL} Employee access to "
				f"{', '.join(granted)}"
			)
		return len(granted)
	except Exception:
		frappe.log_error(
			f"Could not grant level-{PERMLEVEL} Employee permissions\n{frappe.get_traceback()}",
			"Employee permissions",
		)
		return 0
