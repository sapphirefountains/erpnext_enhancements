"""Inventory reports (v1.805.0): link the four new reports from the two hubs.

Location Contents, Where Is This Item, Project Materials and Job Site Contents join the Reports
card of the Inventory Enhancements workspace. On the Operations Hub, the first two and Job Site
Contents join its Inventory card and Project Materials joins its Reports card, beside Pending
Items by Project.

The Reports themselves (``inventory_enhancements/report/...``) are new, so model sync imports
them. The two workspace files are edited, and Workspaces are **timestamp-gated** by
``import_file``: a file is imported only when it reads newer than the stored row. Both now carry
2026-11-12 10:00, past the rows on production (2026-11-05 10:00 on both), and this patch is the
belt to those suspenders, the same shape as ``warehouse_transactions_hub_links``:
``import_file_by_path(force=True)`` rebuilds each row past the age check. That also covers
another change the same day stamping the same files with the same minute.

**Each import runs in its own savepoint.** frappe 16.53's ``import_doc`` deletes the stored row
(``delete_old_doc``) before it calls ``insert()``. If the insert raised and the patch only logged
it, migrate would commit the delete and the workspace would be gone. So a failure rolls back to the
savepoint, which puts the old row back. ``import_doc`` also sets ``frappe.flags.in_import`` and
clears it only after a successful insert, so the flag is restored in a ``finally``. Idempotent;
never raises (each step logs its own failure).
"""

import os

import frappe

#: (folder under the app, workspace slug) for every workspace file this release edits.
WORKSPACES = (
	("inventory_enhancements", "inventory_enhancements"),
	("enhancements_core", "operations_hub"),
)

TITLE = "Inventory reports hub links"


def execute():
	from frappe.modules.import_file import import_file_by_path

	done, failed = [], []
	for folder, slug in WORKSPACES:
		path = frappe.get_app_path("erpnext_enhancements", folder, "workspace", slug, f"{slug}.json")
		if not os.path.exists(path):
			frappe.log_error(title=TITLE, message=f"File missing: {path}")
			failed.append(slug)
			continue
		savepoint = f"inv_reports_ws_{slug}"
		in_import = frappe.flags.in_import
		try:
			frappe.db.savepoint(savepoint)
			import_file_by_path(path, force=True, reset_permissions=False)
			done.append(slug)
		except Exception:
			try:
				frappe.db.rollback(save_point=savepoint)
			except Exception:
				frappe.log_error(
					title=TITLE, message=f"Could not roll back workspace {slug}\n{frappe.get_traceback()}"
				)
			frappe.log_error(
				title=TITLE, message=f"Could not import workspace {slug}\n{frappe.get_traceback()}"
			)
			failed.append(slug)
		finally:
			frappe.flags.in_import = in_import
	try:
		frappe.clear_cache()
	except Exception:
		frappe.log_error(title=TITLE, message=f"Could not clear the cache\n{frappe.get_traceback()}")
	print(f"inventory_reports_hub_links: {len(done)} done, {len(failed)} failed: {failed}")
