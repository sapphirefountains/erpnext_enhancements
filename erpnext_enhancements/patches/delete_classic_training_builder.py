# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""R3: remove the classic Training Builder page and the flag that gated it.

The end of a staged retirement, approved by Nik on 2026-09-12:

* **R1** (v1.416.0) unlinked it — no button anywhere, URL still reachable.
* **v1.417.0** ported video registration, the last capability that existed only there.
  Until that landed, ticking the R2 flag would have removed the only way to register a
  video, which is why the switch shipped before the port but was never thrown.
* **R2** (v1.418.0) added `classic_builder_retired`, defaulting off.
* **R3** — this — deletes the page. **One-way.**

--------------------------------------------------------------------------------------
Two records to remove, and the second is the one that is easy to forget
--------------------------------------------------------------------------------------

Deleting the folder from the app stops the page being *synced*; it does not delete the
`Page` row already on the site. That is the same two-step rule `fixtures/README.md` states
for Custom Fields, and it applies here: **the repo is the source of truth, and removal is
two steps.**

Frappe v16's `migrate.py` does call `remove_orphan_entities()` with `Page` in its list
after `post_schema_updates`, so the row would very likely go on its own. This deletes it
explicitly anyway, for a reason that is not belt-and-braces: `remove_orphan_entities`
removes a page only when **no installed app** ships it, and a page left behind on a site
where that sweep did not fire is a desk route that 404s on click rather than one that is
absent. Cheap to be certain.

The `classic_builder_retired` field goes too. Its JSON declaration is removed in the same
release, and — again the two-step rule — that only stops it being *managed*; the
`tabSingles` row survives until something deletes it. A stale row for a field no form shows
is harmless until somebody greps for it and finds a setting that appears to exist.

--------------------------------------------------------------------------------------
What is deliberately left alone
--------------------------------------------------------------------------------------

`register_video_asset`, `retry_video_copy`, `_builder_video_assets` and `_probe_drive_video`
stay in `api/training_author.py`. They were never the classic builder's code — the canvas
calls all four as of v1.417.0, and `get_builder_bootstrap` / `save_draft_version` were
always shared. Deleting a page is not deleting an API.

No learner-facing behaviour changes. Nothing in `api/training.py`, `training/grading.py` or
the player is touched: this removes an authoring surface that nothing has linked to since
v1.416.0.

--------------------------------------------------------------------------------------
Mechanics
--------------------------------------------------------------------------------------

`post_model_sync`, so the page is already gone from the app when this runs. Guarded and
wrapped: a patch that raises aborts `bench migrate`, which on this repo is the deploy, and
a failure to tidy a dead row is not worth a failed deploy. Safe to run twice — the second
run finds nothing to delete.
"""

import frappe

PAGE = "training-builder"
SETTINGS = "Training Settings"
FIELD = "classic_builder_retired"


def execute():
	_delete_page()
	_delete_flag_row()


def _delete_page():
	try:
		if not frappe.db.exists("Page", PAGE):
			return
		# ignore_missing because a concurrent `remove_orphan_entities` may have taken it
		# first; delete_permanently because a Page has no meaningful trash state.
		frappe.delete_doc("Page", PAGE, force=True, ignore_missing=True, delete_permanently=True)
		frappe.clear_cache()
		print("delete_classic_training_builder: removed Page %s" % PAGE)
	except Exception:
		frappe.log_error(
			title="delete_classic_training_builder",
			message="Could not delete Page %s" % PAGE,
		)


def _delete_flag_row():
	"""Drop the `tabSingles` row for the retired flag.

	Removing the field from the DocType JSON stops it being managed; the stored row
	outlives that. Deleted by hand rather than through the doc API because the field no
	longer exists on the meta, so `set_single_value` has nothing to write to.
	"""
	try:
		if not frappe.db.exists("DocType", SETTINGS):
			return
		frappe.db.sql(
			"delete from tabSingles where doctype = %s and field = %s", (SETTINGS, FIELD)
		)
		frappe.clear_document_cache(SETTINGS, SETTINGS)
	except Exception:
		frappe.log_error(
			title="delete_classic_training_builder",
			message="Could not delete the %s.%s row" % (SETTINGS, FIELD),
		)
