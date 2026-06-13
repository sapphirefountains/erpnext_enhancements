"""Seed the "Device Manager" role (v1.31.0).

A desk role for IT/ops staff who run the Device Management module — enroll,
assign and track managed devices, and act on the fleet from the Device Console
and Fleet Dashboard. The Managed Device / Device Compliance Settings doctypes and
the two pages grant this role its access in their own permission rows; this patch
only creates the role they reference. Insert-only and idempotent.
"""

import frappe


def execute():
	if frappe.db.exists("Role", "Device Manager"):
		return
	role = frappe.new_doc("Role")
	role.role_name = "Device Manager"
	role.desk_access = 1
	role.insert(ignore_permissions=True)
