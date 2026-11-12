"""Seed the "Warehouse Map Editor" role and its one-role profile (v1.815.0).

Nik, 2026-11-12, asked who may create and edit the warehouse map: "People you name". So drawing
and changing a Warehouse Map is this role's (with System Manager), and everyone who already works
the inventory (the Stock Scan roles) may look. See the Warehouse Map DocType's permissions and
``api/warehouse_map.py``.

Runs **pre_model_sync** so the role exists before the Warehouse Map DocType syncs the permission
row naming it. Insert-only and idempotent, the same shape as ``seed_contract_manager_role``. The
one-role "Warehouse Map Editor" Role Profile is how a user who already has role profiles is given
the role: on this site a profiled user's roles are rebuilt from their profiles on every save, so a
role granted directly would not stay. Nobody is granted anything here; who holds the role is
decided per site, in the Desk.
"""

import frappe

ROLE = "Warehouse Map Editor"


def execute():
	if not frappe.db.exists("Role", ROLE):
		role = frappe.new_doc("Role")
		role.role_name = ROLE
		role.desk_access = 1
		role.insert(ignore_permissions=True)
	if frappe.db.exists("DocType", "Role Profile") and not frappe.db.exists("Role Profile", ROLE):
		profile = frappe.new_doc("Role Profile")
		profile.role_profile = ROLE
		profile.append("roles", {"role": ROLE})
		profile.insert(ignore_permissions=True)
