"""Seed the "Fleet Manager" role (v1.138.0).

Owns the Fleet Maintenance module: full access to Fleet Vehicle / Vehicle
Maintenance Log (granted in those doctypes' permissions) and the audience for
fleet reminders. Insert-only and idempotent; assign it to users post-deploy.
"""

import frappe


def execute():
	if frappe.db.exists("Role", "Fleet Manager"):
		return
	role = frappe.new_doc("Role")
	role.role_name = "Fleet Manager"
	role.desk_access = 1
	role.insert(ignore_permissions=True)
