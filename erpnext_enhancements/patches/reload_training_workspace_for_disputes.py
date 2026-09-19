"""Re-sync the Training workspace again, for the Answer disputes queue.

A second patch rather than an edit to ``reload_training_workspace_for_canvas``,
and the reason is the whole point of ``tabPatch Log``: that one **already ran**
on production at 2026-09-19 10:22:59, so it is recorded and will never run again.
Editing it would land a file nothing executes, and the queue would be invisible
on every existing site while the repo said otherwise — which is exactly the
silent-skip failure the first patch existed to prevent, reintroduced by trying to
reuse it.

The rule worth keeping: **one workspace edit, one patch.** They are cheap, they
are idempotent, and a patch that has already been recorded is not a place to add
anything.

As before, ``force=True`` is the half that does the work (it skips the
``modified`` comparison outright); the bumped stamp in the JSON is for the next
ordinary migrate.
"""

import frappe


def execute() -> None:
	# Quiet on failure: a patch that raises aborts `bench migrate`, which on this
	# repo IS the deploy, and a queue tile is not worth a half-applied install.
	try:
		frappe.reload_doc("training", "workspace", "training", force=True)
		frappe.clear_cache()
		print("reload_training_workspace_for_disputes: Training workspace re-synced")
	except Exception:
		frappe.log_error(
			title="Training workspace reload failed",
			message=frappe.get_traceback(),
		)
