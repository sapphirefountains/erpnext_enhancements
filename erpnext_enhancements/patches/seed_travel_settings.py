"""Seed the Travel Coordinator role and Travel Settings defaults (v1.15.0).

Creates the **Travel Coordinator** role (desk access; sees and manages every
Travel Trip via the permission hooks) and fills Travel Settings with one
per-diem rate row per travel type. All rates are deliberately 0 — finance
sets real numbers in **Travel Settings**; hardcoding an IRS rate here would
just go stale. Expense Claim Types are NOT auto-created (they need company
expense accounts; claim generation throws a clear configuration error until
they are picked in Travel Settings).

Insert-only and existence-guarded: re-runs and fresh installs are safe, and
values an admin has already changed are never overwritten.
"""

import frappe

TRAVEL_TYPES = ("Domestic", "International", "Local Site Visit")


def execute():
	_seed_role()
	_seed_settings()


def _seed_role():
	if not frappe.db.exists("Role", "Travel Coordinator"):
		role = frappe.new_doc("Role")
		role.role_name = "Travel Coordinator"
		role.desk_access = 1
		role.insert(ignore_permissions=True)


def _seed_settings():
	settings = frappe.get_single("Travel Settings")
	changed = False

	existing_types = {row.travel_type for row in settings.per_diem_rates}
	for travel_type in TRAVEL_TYPES:
		if travel_type in existing_types:
			continue
		settings.append(
			"per_diem_rates",
			{"travel_type": travel_type, "daily_rate": 0, "first_last_day_percent": 100},
		)
		changed = True

	if changed:
		settings.flags.ignore_permissions = True
		settings.save()
