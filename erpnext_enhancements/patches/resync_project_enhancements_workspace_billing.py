"""Re-sync the Project Enhancements workspace so its new Progress Billing card arrives (B9 follow-up).

The card links the Schedule of Values, the Pay Application and the three progress-billing reports
(Retainage Held, Pay Applications Outstanding, Billed vs Scheduled). Workspaces are imported only
when the file is newer than the stored row (``import_file`` compares ``modified``), so the JSON's
``modified`` is bumped, and ``reload_doc(force=True)`` covers a row somebody edited in the Desk since,
which would otherwise stay newer than the file forever.

A post-model-sync patch, so the reports it links exist by the time it runs. It never raises: a
failure is one Error Log row and the migrate, which is the deploy, goes on. Safe twice.
"""

import frappe


def execute():
	try:
		frappe.reload_doc("project_enhancements", "workspace", "project_enhancements", force=True)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(
			title="Project Enhancements workspace resync",
			message=frappe.get_traceback(),
		)
