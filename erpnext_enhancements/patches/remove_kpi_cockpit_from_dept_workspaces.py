"""Strip the KPI Cockpit block from the department workspaces.

Per-department KPIs moved to dedicated, role-gated desk pages
(``kpi_dashboards/page/<dept>_kpi``) so they can be shared individually. The
seeder no longer places the KPI Cockpit on the seven department workspaces (see
``setup/custom_html_blocks.py``); this patch removes any existing placement from
their ``content`` so sites that already had it are cleaned up. The cockpit stays
on Home and on the KPI Dashboards workspace.

Idempotent: a workspace without the block (or absent entirely) is skipped, and
all other blocks (e.g. the six Finance widgets on the Finance Dashboard
workspace) are preserved.
"""

import json

import frappe

from erpnext_enhancements.setup.custom_html_blocks import KPI_COCKPIT, KPI_DEPARTMENT_DASHBOARDS


def execute():
	changed = False
	for workspace in KPI_DEPARTMENT_DASHBOARDS:
		if not frappe.db.exists("Workspace", workspace):
			continue
		content = frappe.db.get_value("Workspace", workspace, "content")
		try:
			blocks = json.loads(content or "[]")
		except (ValueError, TypeError):
			continue
		kept = [
			b
			for b in blocks
			if not (
				isinstance(b, dict)
				and b.get("type") == "custom_block"
				and (b.get("data") or {}).get("custom_block_name") == KPI_COCKPIT
			)
		]
		if len(kept) != len(blocks):
			frappe.db.set_value("Workspace", workspace, "content", json.dumps(kept))
			changed = True
	if changed:
		frappe.clear_cache()
