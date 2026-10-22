"""Reorder Board (v1.730.0, ER-2026-1323810): link the new Desk page from the Inventory
Enhancements workspace's Purchasing card, beside Find a Supplier.

The Page itself (``inventory_enhancements/page/reorder_board``) is new, so model sync imports it.
The workspace file is edited, and Workspaces are **timestamp-gated** by ``import_file``: a file is
imported only when it reads newer than the stored row. It now carries 2026-10-22 18:00, past
v1.726.0's 12:00 stamp, and this patch is the belt to those suspenders, the same shape as
``bin_manager_hub_links``: ``import_file_by_path(force=True)`` rebuilds the row past the age check.

**What it writes:** the ``Inventory Enhancements`` Workspace row and its child rows, replaced by the
file's (one more Link, "Reorder Board", in the Purchasing card). Idempotent; never raises.
"""

import os

import frappe

FOLDER, SLUG = "inventory_enhancements", "inventory_enhancements"
TITLE = "Reorder Board workspace link"


def execute():
	from frappe.modules.import_file import import_file_by_path

	path = frappe.get_app_path("erpnext_enhancements", FOLDER, "workspace", SLUG, f"{SLUG}.json")
	if not os.path.exists(path):
		frappe.log_error(title=TITLE, message=f"File missing: {path}")
		return
	try:
		import_file_by_path(path, force=True, reset_permissions=False)
	except Exception:
		frappe.log_error(title=TITLE, message=f"Could not import workspace {SLUG}\n{frappe.get_traceback()}")
		return
	try:
		frappe.clear_cache()
	except Exception:
		frappe.log_error(title=TITLE, message=f"Could not clear the cache\n{frappe.get_traceback()}")
	print("reorder_board_workspace_link: done")
