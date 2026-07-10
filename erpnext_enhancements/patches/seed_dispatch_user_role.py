"""Seed the "Dispatch User" role (v1.153.0).

Grants create/read/write/submit on the Package Dispatch doctype alongside System
Manager, so shipping isn't limited to admins. Insert-only and idempotent; assign
it to users (or a Role Profile) post-deploy.
"""

import frappe


def execute():
	if frappe.db.exists("Role", "Dispatch User"):
		return
	role = frappe.new_doc("Role")
	role.role_name = "Dispatch User"
	role.desk_access = 1
	role.insert(ignore_permissions=True)
