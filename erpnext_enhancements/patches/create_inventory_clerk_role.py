"""Seed the "Inventory Clerk" role (v1.29.0).

A desk role for warehouse staff who run physical counts on the Inventory
Scanner Audit page. The page and the Inventory Enhancements doctypes (Storage
Location, Inventory Count Session) grant this role its read/create access in
their own permission rows; this patch only creates the role they reference.
Insert-only and idempotent.
"""

import frappe


def execute():
	if frappe.db.exists("Role", "Inventory Clerk"):
		return
	role = frappe.new_doc("Role")
	role.role_name = "Inventory Clerk"
	role.desk_access = 1
	role.insert(ignore_permissions=True)
