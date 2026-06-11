"""Seed the "AI Auditor" role (v1.14.0).

Read/report/export access to the AI Governance doctypes (AI Pending Action,
AI Action Log, AI Model Usage) is granted in those doctypes' own permission
rows; this patch only creates the role they reference. Insert-only and
idempotent.
"""

import frappe


def execute():
	if frappe.db.exists("Role", "AI Auditor"):
		return
	role = frappe.new_doc("Role")
	role.role_name = "AI Auditor"
	role.desk_access = 1
	role.insert(ignore_permissions=True)
