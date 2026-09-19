"""Force the Training workspace to re-sync, so the Course canvas shortcut appears.

Same mechanism, same file and the same reason as
``reload_training_workspace_for_glossary``: a Workspace JSON is **age-gated** on the way
in. ``bench migrate`` hands the file to ``import_file``, which compares its ``modified``
against the row already in the database and *silently skips* it when the row is not older.
So an edited workspace reaches a fresh install and no existing site, with no error and no
log line — the card simply never changes.

Why this one: the Training workspace had shortcuts to the learner portal, Insights,
sessions and three course lists, and **none to the authoring surface**. Together with the
desk rail's "Course canvas" link landing on a dead end that said to edit the URL by hand,
the canvas was a page you had to already know about. The dead end is now a home screen, so
the shortcut has somewhere worth pointing.

**This patch alone is sufficient**, and it is worth being exact about that rather than
repeating the usual "both halves are needed": ``force=True`` skips the timestamp comparison
entirely, so the reload lands whatever the row's ``modified`` says. The bumped stamp in the
JSON is not a second requirement — it is what keeps the *next* ordinary ``bench migrate``
from being a no-op once this one-shot patch has been recorded in ``tabPatch Log`` and will
never run again. Ship both; only one of them is doing the work today.
"""

import frappe


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy --
	# and a half-applied migrate is how prod ended up schema-synced with no fixtures in
	# v1.395.0. A workspace shortcut is not worth that, so every failure here is quiet.
	try:
		frappe.reload_doc("training", "workspace", "training", force=True)
		frappe.clear_cache()
		print("reload_training_workspace_for_canvas: Training workspace re-synced")
	except Exception:
		frappe.log_error(
			title="Training workspace reload failed",
			message=frappe.get_traceback(),
		)
