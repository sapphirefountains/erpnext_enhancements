"""Seed the "PO Creator" role (v1.191.0).

WI-066 moves Purchase Order create/write/submit off `Purchase User` onto a
dedicated role. `Purchase User` sits in four Role Profiles (Production Team,
Design Team, Finance Team, Sales Team), so sixteen enabled users could commit
company money; five people actually buy. A role that nobody inherits by accident
is the whole point — do not add it to any Role Profile except "PO Creators".

The role must exist before fixture sync runs, and the ordering is not the one
hooks.py used to claim: fixture files import in **alphabetical filename order**
(frappe/utils/fixtures.py sorts the directory listing), so custom_docperm.json
lands before role.json. A `PO Creator` Custom DocPerm row imported against a
missing Role is exactly the kind of dangling record this app has been bitten by
before. post_model_sync patches run before fixture sync, which closes it.

Insert-only and idempotent. Assign it to the five creators post-deploy — Desk UI
only, see docs/migration/wi066-po-creator-and-sod.md.
"""

import frappe


def execute():
	if frappe.db.exists("Role", "PO Creator"):
		return
	role = frappe.new_doc("Role")
	role.role_name = "PO Creator"
	role.desk_access = 1
	role.insert(ignore_permissions=True)
