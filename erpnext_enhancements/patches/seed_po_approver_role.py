"""Seed the "PO Approver" role (v1.169.0).

The Purchase Order dollar-threshold escalation (WI-013) uses a native
Authorization Rule whose approving role is "PO Approver" — held only by the CEO
— so a PO above the threshold can only be submitted by that role. This patch
creates the role so the Authorization Rule fixture (WI-013) and the Role Profile
fixtures (WI-010) never reference a missing role on fresh sites, and so it
already exists on prod (COUNT=1) before WI-013 deploys. Insert-only and
idempotent; assign it to the CEO post-deploy (WI-011).
"""

import frappe


def execute():
	if frappe.db.exists("Role", "PO Approver"):
		return
	role = frappe.new_doc("Role")
	role.role_name = "PO Approver"
	role.desk_access = 1
	role.insert(ignore_permissions=True)
