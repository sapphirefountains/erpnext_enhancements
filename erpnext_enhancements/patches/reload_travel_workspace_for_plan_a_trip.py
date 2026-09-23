"""Force the Travel workspace to re-sync, so its "New Travel Trip" tile becomes "Plan a Trip".

The Plan a Trip release swapped the workspace's "New Travel Trip" shortcut (a blank form) for
"Plan a Trip" (the step-by-step page) and stamped
``travel_management/workspace/travel_management/travel_management.json`` with a new
``modified``. A Workspace JSON is age-gated on import: ``bench migrate`` skips the file when the
row already in the database is not older than the stamp, silently (the trap
``frappe-workspace-modified-gate`` describes, and the one ``reload_location_timeline_page`` was
written for). The stamp should be enough on its own; this patch is the half a stamp that loses
to a newer prod row cannot undo.

The Plan a Trip Page itself is new, so it has no older row to lose to and imports normally.
"""

import frappe


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy. A workspace
	# tile is not worth a half-finished deploy, so every failure here is quiet.
	try:
		frappe.reload_doc("travel_management", "workspace", "travel_management", force=True)
		print("reload_travel_workspace_for_plan_a_trip: Travel workspace re-synced")
	except Exception:
		frappe.log_error(
			title="Workspace reload failed: Travel",
			message=frappe.get_traceback(),
		)
	try:
		frappe.clear_cache()
	except Exception:
		pass
