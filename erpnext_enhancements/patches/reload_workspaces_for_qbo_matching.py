"""Force the QuickBooks Online and Finance Hub workspaces to re-sync, so the new
Record Matching shortcut actually appears.

A Workspace JSON is age-gated on the way in: ``bench migrate`` hands it to ``import_file``,
which compares the file's ``modified`` against the row already in the database and silently
skips the file when the row is not older. Both JSONs carry a bumped ``modified`` alongside
this patch; ``reload_doc(force=True)`` is the other half, and the half that a later edit
which forgets the bump cannot undo. Same shape as ``reload_training_workspace_for_glossary``
(v1.469.0), for the same reason.
"""

import frappe

WORKSPACES = (
	("quickbooks_online", "quickbooks_online"),
	("accounting_intake", "finance_hub"),
)


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy. A
	# workspace card is not worth a half-finished deploy, so every failure here is quiet.
	for module, name in WORKSPACES:
		try:
			frappe.reload_doc(module, "workspace", name, force=True)
			print(f"reload_workspaces_for_qbo_matching: {name} re-synced")
		except Exception:
			frappe.log_error(
				title=f"Workspace reload failed: {name}",
				message=frappe.get_traceback(),
			)
	try:
		frappe.clear_cache()
	except Exception:
		pass
