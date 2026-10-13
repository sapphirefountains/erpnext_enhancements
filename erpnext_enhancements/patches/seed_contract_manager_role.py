"""Seed the "Contract Manager" role and its one-role profile (v1.615.0).

Nik, 2026-10-13: "make a Contract Manager role for sales, Lisa, James and me" (and Clegg). Issuing a
contract, taking or sending for its signature, cancelling or amending it, and editing a Contract
Template's wording are the Contract Manager's (with System Manager); building and saving drafts stays
open to Projects Manager, Sales Manager and Sales User. See the Project Contract, Contract Template and
Contract Signature Request permissions and ``esign.api.SEND_ROLES``.

Runs **pre_model_sync** so the role exists before those doctypes sync permission rows naming it.
Insert-only and idempotent, like ``seed_po_approver_role``. The one-role "Contract Manager" Role
Profile is how a user who already has role profiles is given the role: on this site a profiled
user's roles are rebuilt from their profiles on every save, so a role granted directly would not
stay. Nobody is granted anything here; who holds the role is decided per site, in the Desk.
"""

import frappe

ROLE = "Contract Manager"


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
