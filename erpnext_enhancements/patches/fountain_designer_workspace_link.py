"""Force the Water Engineering workspace to re-sync, so the Fountain Designer shortcut and its
link on the Tools card actually appear (v1.761.0, phase F of the Fountain Designer rebuild).

A Workspace JSON is age-gated on the way in: ``bench migrate`` hands it to ``import_file``, which
compares the file's ``modified`` with the row already in the database and silently skips the file
when the row is not older. The JSON carries a bumped ``modified`` alongside this patch;
``reload_doc(force=True)`` is the half a Desk edit to the workspace cannot undo, since a hand-edit by
anyone holding Workspace Manager moves the row's stamp past the file's for good. Same shape as
``reload_stock_scan_workspaces``.

The Page itself (``fountain-designer``) is new, so model sync imports it without help.
"""

import frappe


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy. A workspace tile is
	# not worth a half-finished deploy, so a failure here is logged and nothing more.
	try:
		frappe.reload_doc("water_engineering", "workspace", "water_engineering", force=True)
		print("fountain_designer_workspace_link: Water Engineering re-synced")
	except Exception:
		frappe.log_error(title="Workspace reload failed: Water Engineering", message=frappe.get_traceback())
	try:
		frappe.clear_cache()
	except Exception:
		pass
