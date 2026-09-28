"""Force the revamped Travel hub and its sidebar to re-sync from the repo.

The v1.554.0 hub revamp rewrote ``travel_management/workspace/travel_management/travel_management.json``
(the per-viewer "My Travel" block first, then "I want to…" shortcuts, "How a work trip works",
and the records cards) and added ``workspace_sidebar/travel.json``, a curated sidebar that
replaces the one production generated for itself: that one had two URL items with no URL, a
"New Travel Trip" item, and no Plan a Trip.

Both records are **timestamp-gated** on import: ``bench migrate`` skips a Workspace or a
Workspace Sidebar JSON whose ``modified`` is not newer than the row already stored, silently
(``frappe-workspace-modified-gate``). Both files carry a new stamp, which is enough on its own on a
site whose rows are older; this patch is the half a stamp that loses to a newer row cannot undo.
The patch that did the same job for Plan a Trip, ``reload_travel_workspace_for_plan_a_trip``, has
already run on production and will never run again, so this one has a name of its own.

The sidebar is imported with ``import_file_by_path``, NOT ``reload_doc``: ``reload_doc`` takes a
*module* and looks for ``<module>/workspace_sidebar/travel/travel.json``, but app-level sidebars
are flat files under ``<app>/workspace_sidebar/`` (``resync_hr_and_training_workspaces`` explains
how that once failed in silence). ``import_file_by_path`` is what ``frappe/model/sync.py`` itself
calls for them.

Deliberately overwrites the desk copy of each: these are app-owned records and the repo is the
source of truth. Someone's own arrangement of a workspace lives in a *private* Workspace, which this
does not touch. The "My Travel" block itself is created by ``sync_custom_html_blocks`` in
``after_migrate``; a workspace may name a block that does not exist yet, because the importer sets
``ignore_links``.

It also reloads the **Travel Trip Cost Summary** report, whose roles no longer include Employee:
crew see every part of a trip but its money, and this report is nothing but money (Nik,
2026-09-28). Its JSON carries a new stamp for the same age gate.
"""

import frappe


def execute() -> None:
	# A patch that raises aborts `bench migrate`, which on this repo IS the deploy. A desk page's
	# layout is not worth a half-finished deploy, so every failure here is logged and swallowed.
	try:
		frappe.reload_doc("travel_management", "workspace", "travel_management", force=True)
	except Exception:
		frappe.log_error(
			title="Workspace reload failed: Travel",
			message=frappe.get_traceback(),
		)
	try:
		from frappe.modules.import_file import import_file_by_path

		import_file_by_path(
			frappe.get_app_path("erpnext_enhancements", "workspace_sidebar", "travel.json"),
			force=True,
		)
	except Exception:
		frappe.log_error(
			title="Workspace sidebar reload failed: Travel",
			message=frappe.get_traceback(),
		)
	# Crew no longer open Travel Trip Cost Summary (Nik, 2026-09-28): the report's JSON dropped the
	# Employee role and got a new stamp, and a Report is timestamp-gated on import like the rest.
	# reload_doc(force=True) replaces the stored row, roles table included.
	try:
		frappe.reload_doc("travel_management", "report", "travel_trip_cost_summary", force=True)
	except Exception:
		frappe.log_error(
			title="Report reload failed: Travel Trip Cost Summary",
			message=frappe.get_traceback(),
		)
	try:
		frappe.clear_cache()
	except Exception:
		pass
