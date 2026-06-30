"""Create + role-gate the per-department KPI workspaces.

Each department gets its own desk Workspace (Finance Dashboard, Sales Dashboard,
…) carrying the KPI Cockpit, restricted to that department's roles (+ System
Manager) so it can be shared with just that team — while staying user-editable
(an admin can drag more Custom HTML Blocks onto any of them; the block seeder
only *appends* the cockpit, it never overwrites added content).

One-time + idempotent: an existing workspace is role-gated in place (its content
is left alone — the seeder keeps the cockpit on it); a missing one is created
with the cockpit. Workspaces are created non-standard (``is_standard = 0``) so
they behave like the site-created department dashboards and survive migrations.
"""

import json

import frappe

from erpnext_enhancements.api.kpi import DEPARTMENT_ROLES

# department -> workspace name. Mirrors setup/custom_html_blocks.KPI_DEPARTMENT_DASHBOARDS.
DEPT_WORKSPACE = {
	"Finance": "Finance Dashboard",
	"Sales": "Sales Dashboard",
	"Operations": "Operations Dashboard",
	"Design": "Design Dashboard",
	"Production": "Production Dashboard",
	"Marketing": "Marketing Dashboard",
	"Product": "Product Dashboard",
	"Executive": "Executive Dashboard",
}

_COCKPIT_BLOCK = {
	"id": "ee_chb_kpi_cockpit",
	"type": "custom_block",
	"data": {"custom_block_name": "KPI Cockpit", "col": 12},
}


def execute():
	for index, (dept, ws_name) in enumerate(DEPT_WORKSPACE.items()):
		roles = sorted(set(DEPARTMENT_ROLES.get(dept, set())) | {"System Manager"})
		if frappe.db.exists("Workspace", ws_name):
			ws = frappe.get_doc("Workspace", ws_name)
		else:
			ws = frappe.new_doc("Workspace")
			ws.label = ws_name
			ws.title = ws_name
			ws.public = 1
			ws.is_standard = 0
			ws.icon = "dashboard"
			ws.sequence_id = 50 + index
			ws.content = json.dumps([_COCKPIT_BLOCK])
		# Role-gate: only this department (+ System Manager) sees the workspace.
		ws.set("roles", [{"role": role} for role in roles])
		ws.flags.ignore_permissions = True
		ws.save()
	frappe.clear_cache()
