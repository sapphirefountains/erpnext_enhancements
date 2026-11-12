"""Seed the "Warehouse Map Editor" role and its one-role profile (v1.815.0).

Nik, 2026-11-12, asked who may create and edit the warehouse map: "People you name". So drawing
and changing a Warehouse Floor Map (a map on the Warehouse Map page) is this role's (with System
Manager), and everyone who already works the inventory (the Stock Scan roles) may look. See the
Warehouse Floor Map DocType's permissions and ``api/warehouse_map.py``.

Runs **pre_model_sync** so the role exists before the Warehouse Floor Map DocType syncs the
permission row naming it. Insert-only and idempotent, the same shape as ``seed_contract_manager_role``. The
one-role "Warehouse Map Editor" Role Profile is how a user who already has role profiles is given
the role: on this site a profiled user's roles are rebuilt from their profiles on every save, so a
role granted directly would not stay. Nobody is granted anything here; who holds the role is
decided per site, in the Desk.

**It cannot raise**: a patch that raises aborts ``bench migrate``, which is the deploy. Each of the
two steps is on its own, committed when it works and rolled back when it does not, and a failure
is one Error Log row, "Warehouse Map Editor role seed", that says what to do by hand: create the
Role "Warehouse Map Editor" with Desk Access ticked (and, for profiled users, a Role Profile of
the same name holding it). Until the role exists only a System Manager can edit a map; everyone
can still look.
"""

import frappe

ROLE = "Warehouse Map Editor"


def _role():
	if frappe.db.exists("Role", ROLE):
		return "already there"
	role = frappe.new_doc("Role")
	role.role_name = ROLE
	role.desk_access = 1
	role.insert(ignore_permissions=True)
	return "created"


def _profile():
	if not frappe.db.exists("DocType", "Role Profile"):
		return "skipped: this site has no Role Profile"
	if frappe.db.exists("Role Profile", ROLE):
		return "already there"
	profile = frappe.new_doc("Role Profile")
	profile.role_profile = ROLE
	profile.append("roles", {"role": ROLE})
	profile.insert(ignore_permissions=True)
	return "created"


def execute():
	for label, step in (("role", _role), ("role profile", _profile)):
		try:
			print(f"seed_warehouse_map_editor_role: {label}: {step()}")
			frappe.db.commit()
		except Exception:
			try:
				frappe.db.rollback()
				frappe.log_error(
					f"seed_warehouse_map_editor_role: {label} failed\n{frappe.get_traceback()}",
					"Warehouse Map Editor role seed",
				)
				frappe.db.commit()
			except Exception:
				# The database went away mid-migrate. A map then has no editors but System
				# Managers until the role exists, which is quieter than a broken deploy.
				pass
			print(f"seed_warehouse_map_editor_role: {label}: failed, see the Error Log")
