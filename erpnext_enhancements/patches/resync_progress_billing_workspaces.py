"""Re-sync the two workspaces that carry the Progress Billing card (estimating B9 follow-up).

The card links the Schedule of Values, the Pay Application and the three progress-billing reports
(Retainage Held, Pay Applications Outstanding, Billed vs Scheduled). It sits on the **Project
Enhancements** workspace and, at Nik's request (2026-11-23), on the **Finance Hub**. Workspaces are
imported only when the file is newer than the stored row (``import_file`` compares ``modified``), so
both JSONs' ``modified`` is bumped, and ``reload_doc(force=True)`` covers a row somebody edited in the
Desk since, which would otherwise stay newer than the file forever.

A post-model-sync patch, so the reports it links exist by the time it runs. Each workspace is
reloaded on its own and never raises: a failure is one Error Log row and the migrate, which is the
deploy, goes on. Safe twice.
"""

import frappe

WORKSPACES = (
	("project_enhancements", "project_enhancements"),
	("accounting_intake", "finance_hub"),
)


def execute():
	for module, name in WORKSPACES:
		try:
			frappe.reload_doc(module, "workspace", name, force=True)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title=f"Progress Billing workspace resync: {name}",
				message=frappe.get_traceback(),
			)
