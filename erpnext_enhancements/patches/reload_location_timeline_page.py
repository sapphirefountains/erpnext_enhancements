"""Force the Location Timeline desk page to re-sync, so Projects Manager actually gets it.

v1.480.0 added Projects Manager to ``workforce/page/location_timeline/location_timeline.json``
and stamped the file ``modified: 2026-09-17 09:00:00``. The deploy installed everything else
and skipped this one file: a Page JSON is age-gated on the way in exactly like a Workspace —
``bench migrate`` hands it to ``import_file``, which compares the file's ``modified`` against
the row already in the database and silently skips the file when the row is not older. The
prod row read ``2026-09-17 14:12:58``, five hours *newer* than the stamp (verified live
2026-09-17 after the deploy: the page still carried System Manager and HR Manager only, while
the API gate and the report had Projects Manager). Nothing errored; the page just kept its old
roles, which is the failure shape ``frappe-workspace-modified-gate`` describes.

The JSON now carries a later stamp, and this patch is the other half — the half a future edit
that forgets the bump cannot undo. Same shape as ``reload_workspaces_for_qbo_matching``.
"""

import frappe


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy. A page's
	# role list is not worth a half-finished deploy, so every failure here is quiet.
	try:
		frappe.reload_doc("workforce", "page", "location_timeline", force=True)
		print("reload_location_timeline_page: location-timeline re-synced")
	except Exception:
		frappe.log_error(
			title="Page reload failed: location-timeline",
			message=frappe.get_traceback(),
		)
	try:
		frappe.clear_cache()
	except Exception:
		pass
