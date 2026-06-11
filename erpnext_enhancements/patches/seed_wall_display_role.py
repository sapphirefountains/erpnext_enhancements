"""Seed the "Wall Display" role (v1.13.0).

A low-privilege login role for the dedicated wall/TV users (one per Pi or TV
browser) that sign in to the ``/wall`` display. ``desk_access = 0`` keeps
those accounts out of the desk entirely — the wall page is a website route
and its data endpoint gates on this role (``task_dashboard.STAFF_ROLES``)
before fetching permission-free, exactly like the Task Dashboard block.

Insert-only and idempotent.
"""

import frappe


def execute():
	if frappe.db.exists("Role", "Wall Display"):
		return
	role = frappe.new_doc("Role")
	role.role_name = "Wall Display"
	role.desk_access = 0
	role.insert(ignore_permissions=True)
