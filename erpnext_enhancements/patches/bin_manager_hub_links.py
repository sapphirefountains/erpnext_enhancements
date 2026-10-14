"""Bin Manager (v1.693.0, ER-2026-1320545): link the new Desk page from the Operations Hub (a
shortcut beside Stock Scan, and a link in its Inventory card) and from the Inventory Enhancements
workspace's Counts card.

The Page itself (``inventory_enhancements/page/bin_manager``) is new, so model sync imports it.
The two workspace files are edited, and Workspaces are **timestamp-gated** by ``import_file``: a
file is imported only when it reads newer than the stored row. Both now carry
2026-10-14 14:00, past the rows S4a wrote (12:00 that day), and this patch is the belt to those
suspenders, the same shape as ``custom_pages_s4a_hub_links``: ``import_file_by_path(force=True)``
rebuilds each row past the age check. Idempotent; never raises (each step logs its own failure).
"""

import os

import frappe

#: (folder under the app, workspace slug) for every workspace file this release edits.
WORKSPACES = (
	("enhancements_core", "operations_hub"),
	("inventory_enhancements", "inventory_enhancements"),
)

TITLE = "Bin Manager hub links"


def execute():
	from frappe.modules.import_file import import_file_by_path

	done, failed = [], []
	for folder, slug in WORKSPACES:
		path = frappe.get_app_path("erpnext_enhancements", folder, "workspace", slug, f"{slug}.json")
		if not os.path.exists(path):
			frappe.log_error(title=TITLE, message=f"File missing: {path}")
			failed.append(slug)
			continue
		try:
			import_file_by_path(path, force=True, reset_permissions=False)
			done.append(slug)
		except Exception:
			frappe.log_error(
				title=TITLE, message=f"Could not import workspace {slug}\n{frappe.get_traceback()}"
			)
			failed.append(slug)
	try:
		frappe.clear_cache()
	except Exception:
		frappe.log_error(title=TITLE, message=f"Could not clear the cache\n{frappe.get_traceback()}")
	print(f"bin_manager_hub_links: {len(done)} done, {len(failed)} failed: {failed}")
