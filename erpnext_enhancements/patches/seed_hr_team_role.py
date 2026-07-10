"""Seed the "HR Team" role (v1.150.0).

Gates the HR KPI dashboard (api/kpi.py DEPARTMENT_ROLES) and the HR Stat Entry
doctype alongside HR Manager. The role already exists on the production site
(instance-created); this patch makes fresh sites match so the HR Dashboard
workspace's role rows and the doctype permissions never reference a missing
role. Insert-only and idempotent; assign it to users post-deploy.
"""

import frappe


def execute():
	if frappe.db.exists("Role", "HR Team"):
		return
	role = frappe.new_doc("Role")
	role.role_name = "HR Team"
	role.desk_access = 1
	role.insert(ignore_permissions=True)
