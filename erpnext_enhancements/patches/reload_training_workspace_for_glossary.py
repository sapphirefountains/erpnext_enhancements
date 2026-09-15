"""Force the Training workspace to re-sync, so the glossary link actually appears.

**A Workspace JSON is age-gated on the way in.** ``bench migrate`` hands each fixture to
``import_file``, which compares the file's ``modified`` against the row already in the database and
**silently skips** the file when the row is not older. So a workspace edited in the repo reaches a
fresh install and no existing site, and the only symptom is a card that never changes — no error,
no log line, nothing to notice. This module has been caught by it before: the Training desk
workspace sat on its three-card install default for releases while the JSON said otherwise.

Bumping ``modified`` in the JSON is half the fix and the half that can be undone by accident, since
a later edit that forgets to bump it lands in exactly the same silence. ``reload_doc(force=True)``
is the other half: it imports the file whatever the timestamps say.

Why it matters here: until now ``Training Glossary Term`` was linked from **no workspace at all**.
The only route to the 717 definitions was typing the doctype name into the awesomebar, which is not
a route anybody finds. A glossary nobody can reach to correct is a glossary that stays wrong.
"""

import frappe


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy. A workspace
	# card is not worth a half-finished deploy, so every failure here is quiet.
	try:
		frappe.reload_doc("training", "workspace", "training", force=True)
		frappe.clear_cache()
		print("reload_training_workspace_for_glossary: Training workspace re-synced")
	except Exception:
		frappe.log_error(
			title="Training workspace reload failed",
			message=frappe.get_traceback(),
		)
