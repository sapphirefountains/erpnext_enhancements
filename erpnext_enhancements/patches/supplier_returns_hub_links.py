"""Supplier Returns (v1.807.0): link the new DocType and its report from the purchasing hub, the
Purchasing card of the Inventory Enhancements workspace, with a shortcut that counts the returns
waiting on money.

The DocType and the Report are new, so model sync imports them. The workspace file is edited, and
Workspaces are **timestamp-gated** by ``import_file``: a file is imported only when it reads newer
than the stored row. The file's ``modified`` is bumped, and this patch is the belt to those
suspenders, the same shape as ``warehouse_transactions_hub_links``:
``import_file_by_path(force=True)`` rebuilds the row past the age check.

**The import runs in a savepoint.** frappe's ``import_doc`` deletes the stored row before it calls
``insert()``. If the insert raised and the patch only logged it, migrate would commit the delete
and the workspace would be gone. So a failure rolls back to the savepoint, which puts the old row
back. ``import_doc`` also sets ``frappe.flags.in_import`` and clears it only after a successful
insert, so the flag is restored in a ``finally``. Idempotent; never raises; prints what it did.
"""

import os

import frappe

FOLDER = "inventory_enhancements"
SLUG = "inventory_enhancements"
TITLE = "Supplier Returns hub links"


def execute():
	outcome = "imported"
	try:
		from frappe.modules.import_file import import_file_by_path

		path = frappe.get_app_path("erpnext_enhancements", FOLDER, "workspace", SLUG, f"{SLUG}.json")
		if not os.path.exists(path):
			frappe.log_error(title=TITLE, message=f"File missing: {path}")
			print(f"supplier_returns_hub_links: file missing, nothing done ({path})")
			return
		savepoint = "supplier_returns_ws"
		in_import = frappe.flags.in_import
		try:
			frappe.db.savepoint(savepoint)
			import_file_by_path(path, force=True, reset_permissions=False)
		except Exception:
			outcome = "failed, the old workspace is kept"
			try:
				frappe.db.rollback(save_point=savepoint)
			except Exception:
				frappe.log_error(
					title=TITLE, message=f"Could not roll back workspace {SLUG}\n{frappe.get_traceback()}"
				)
			frappe.log_error(
				title=TITLE, message=f"Could not import workspace {SLUG}\n{frappe.get_traceback()}"
			)
		finally:
			frappe.flags.in_import = in_import
		try:
			frappe.clear_cache()
		except Exception:
			frappe.log_error(title=TITLE, message=f"Could not clear the cache\n{frappe.get_traceback()}")
	except Exception:
		outcome = "failed before the import"
		try:
			frappe.log_error(title=TITLE, message=frappe.get_traceback())
		except Exception:
			pass
	print(f"supplier_returns_hub_links: workspace {SLUG} {outcome}")
