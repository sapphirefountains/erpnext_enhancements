"""Seed the "Product Engineer" role (v1.142.0).

Owns the Product Configurator module: full access to Configurable Product /
Product Configuration (granted in those doctypes' permissions). Insert-only
and idempotent; assign it to users post-deploy. Generating ERPNext records
additionally needs the usual stock/manufacturing permissions (Item, BOM,
Item Price) — grant those through standard roles.
"""

import frappe


def execute():
	if frappe.db.exists("Role", "Product Engineer"):
		return
	role = frappe.new_doc("Role")
	role.role_name = "Product Engineer"
	role.desk_access = 1
	role.insert(ignore_permissions=True)
