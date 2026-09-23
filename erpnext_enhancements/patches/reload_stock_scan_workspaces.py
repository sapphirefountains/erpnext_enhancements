"""Force the Inventory Enhancements and Production Hub workspaces to re-sync, so the Stock
Scan shortcuts and the Stock Scan Log link actually appear (v1.521.0).

Inventory Enhancements gains **Stock Scan** (``/stock-scan``) and **Warehouse QR Labels**
(``/warehouse-labels``) as URL shortcuts and a **Stock Scan Log** link on its Counts card;
Production Hub — the technicians' home — gains the Stock Scan shortcut and the log on its
"Stock and tools" card.

A Workspace JSON is age-gated on the way in: ``bench migrate`` hands it to ``import_file``,
which compares the file's ``modified`` against the row already in the database and silently
skips the file when the row is not older. Both JSONs carry a bumped ``modified`` alongside
this patch; ``reload_doc(force=True)`` is the half a Desk edit to either workspace cannot
undo, since a hand-edit by anyone holding Workspace Manager moves the row's stamp past the
file's for good.

The Inventory Enhancements stamp had not moved since 2026-06-15 — including in v1.337.0,
which added the Reports card (Item Naming Audit) to the same file. So on a site that already
had this workspace that card has most likely never arrived either; it comes in with this one.

``reload_doc``'s first argument is the **directory's** module, not the workspace's: Production
Hub declares no module on purpose (``tests/test_workspaces.py::MODULELESS_HUBS``) but its file
lives under ``enhancements_core/``, and Enhancements Core is a real module, so the path resolves.
Same shape as ``reload_workspaces_for_qbo_matching``, which reloads Finance Hub this way.
"""

import frappe

WORKSPACES = (
	("inventory_enhancements", "inventory_enhancements"),
	("enhancements_core", "production_hub"),
)


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy. A
	# workspace tile is not worth a half-finished deploy, so every failure here is quiet.
	for module, name in WORKSPACES:
		try:
			frappe.reload_doc(module, "workspace", name, force=True)
			print(f"reload_stock_scan_workspaces: {name} re-synced")
		except Exception:
			frappe.log_error(
				title=f"Workspace reload failed: {name}",
				message=frappe.get_traceback(),
			)
	try:
		frappe.clear_cache()
	except Exception:
		pass
