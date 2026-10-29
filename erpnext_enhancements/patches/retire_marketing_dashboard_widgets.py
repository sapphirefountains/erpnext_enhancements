"""Marketing Performance takes over from the GA4 Dashboard and the Marketing widgets (v1.756.0).

Nik, 2026-10-29: one Marketing Performance page in place of the old ones. The code side is the
page, its endpoint and the GA4 Dashboard's redirect. This patch does what a code change cannot
do on its own, each step guarded so one failure stops neither the rest nor the migrate:

1. **The three workspaces re-sync.** The Marketing Dashboard loses its four widget blocks and
   gains a Marketing Performance shortcut; the Marketing Hub and the Integration Hub point their
   GA4 Dashboard shortcuts and links at the new page. Each JSON's ``modified`` is bumped, which is
   enough where the stored row is older, and ``force=True`` covers a row somebody edited more
   recently (several people hold Workspace Manager).
2. **The four block records go.** The block seeder upserts and never deletes, so "Marketing
   Funnel Cascade", "Marketing Channel Spend", "Marketing Unsourced Leads" and "Marketing Source
   Health" would otherwise sit in the block picker for ever, calling endpoints that no longer
   exist. They are deleted only after step 1 has removed the Marketing Dashboard's rows pointing
   at them, and never with ``force``: a copy in somebody's private workspace still links to one,
   ``delete_doc`` refuses, and that refusal is logged and the block left alone.

Prints what it did, cannot raise, safe twice.
"""

import frappe

WORKSPACES = (
	("kpi_dashboards", "marketing_dashboard"),
	("enhancements_core", "marketing_hub"),
	("integration_hub", "integration_hub"),
)
RETIRED_BLOCKS = (
	"Marketing Funnel Cascade",
	"Marketing Channel Spend",
	"Marketing Unsourced Leads",
	"Marketing Source Health",
)


def execute():
	outcome = {"workspaces": [], "deleted": [], "kept": [], "failed": []}
	for module, name in WORKSPACES:
		try:
			frappe.reload_doc(module, "workspace", name, force=True)
			frappe.db.commit()
			outcome["workspaces"].append(name)
		except Exception:
			frappe.db.rollback()
			outcome["failed"].append(name)
			frappe.log_error(
				f"retire_marketing_dashboard_widgets: could not re-sync the {name} workspace\n{frappe.get_traceback()}",
				"Marketing Performance",
			)
	for block in RETIRED_BLOCKS:
		if not frappe.db.exists("Custom HTML Block", block):
			continue
		try:
			frappe.delete_doc("Custom HTML Block", block, ignore_permissions=True)
			frappe.db.commit()
			outcome["deleted"].append(block)
		except Exception:
			frappe.db.rollback()
			outcome["kept"].append(block)
			frappe.log_error(
				f"Left the retired block {block!r} in place: something still links to it\n{frappe.get_traceback()}",
				"Marketing Performance",
			)
	frappe.clear_cache()
	print(f"retire_marketing_dashboard_widgets: {outcome}")
