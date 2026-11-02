# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Retire the Water Engineering Wizard page (v1.771.0, phase H of the Fountain Designer rebuild).

The Fountain Designer (``/desk/fountain-designer``) replaced the wizard, and its quick calculator is
now the Calculator drawer on the designer's Review step. This release removes the page folder; this
patch removes what the folder leaves on a site.

1. **Re-import the two workspaces that linked it**, Water Engineering (a shortcut and a Tools-card
   link) and Design Hub (a "Jump to" shortcut, now the Fountain Designer). A Workspace JSON is
   timestamp-gated on import (``frappe-workspace-modified-gate``); both files carry a new stamp, and
   ``import_file_by_path(force=True)`` is the half a newer Desk edit cannot undo. Not ``reload_doc``,
   because Design Hub declares no module (``custom_pages_s4a_hub_links`` says why).
2. **Delete the Page row.** Removing the folder stops it syncing; ``remove_orphan_entities`` would
   very likely delete it too, but only where no installed app ships it, and a Page row left behind
   is a Desk route that errors on click (``delete_classic_training_builder``).
3. **Point every other row that still names it at the Fountain Designer**: Workspace Links and
   Shortcuts in workspaces the repo does not ship (a private or hand-made workspace), and Workspace
   Sidebar items. Production on 2026-11-01 had one such row, the "Water Engineering Wizard" item of
   the site-made Water Engineering sidebar. A Link and a sidebar item take the new label too; a
   Shortcut keeps its label, because the workspace's content block finds a shortcut by its label.
4. **Delete any Desktop Icon that opens it** (none on production on 2026-11-01).

Route History is left alone: it is a record of where people went, not a way in. The address itself
keeps working: ``public/js/global_enhancements/retired_routes.js`` sends
``/app/water-engineering-wizard`` (and ``/desk/...``) to the Fountain Designer through frappe's own
``frappe.re_route``, which keeps Back working.

``post_model_sync``. **It never raises** (a raising patch aborts ``bench migrate``, which is the
deploy): every step is its own try, logged on failure. Safe twice.
"""

import os

import frappe

PAGE = "water-engineering-wizard"
TARGET = "fountain-designer"
TARGET_LABEL = "Fountain Designer"
TITLE = "retire_water_engineering_wizard"

#: (folder under the app, workspace slug) of the shipped workspaces that linked the page.
WORKSPACES = (
	("water_engineering", "water_engineering"),
	("enhancements_core", "design_hub"),
)


def execute():
	done, failed = [], []
	for folder, slug in WORKSPACES:
		_step(lambda f=folder, s=slug: _import_workspace(f, s), f"workspace {slug}", done, failed)
	_step(_delete_page, f"Page {PAGE}", done, failed)
	_step(_repoint_rows, "repoint the rows that still name it", done, failed)
	_step(_delete_desktop_icons, "Desktop Icons", done, failed)
	try:
		frappe.clear_cache()
	except Exception:
		pass
	print(f"{TITLE}: {len(done)} done, {len(failed)} failed: {failed}")


def _step(fn, label, done, failed):
	try:
		fn()
		done.append(label)
	except Exception:
		frappe.log_error(title=TITLE, message=f"Could not {label}\n{frappe.get_traceback()}")
		failed.append(label)


def _import_workspace(folder, slug):
	from frappe.modules.import_file import import_file_by_path

	path = frappe.get_app_path("erpnext_enhancements", folder, "workspace", slug, f"{slug}.json")
	if not os.path.exists(path):
		raise FileNotFoundError(path)
	import_file_by_path(path, force=True, reset_permissions=False)


def _delete_page():
	if frappe.db.exists("Page", PAGE):
		frappe.delete_doc("Page", PAGE, force=True, ignore_missing=True, delete_permanently=True)
		print(f"{TITLE}: removed Page {PAGE}")


def _repoint_rows():
	"""Every Link, Shortcut and sidebar item still naming the page, after the re-import above."""
	moved = 0
	for doctype, relabel in (
		("Workspace Link", True),
		("Workspace Shortcut", False),
		("Workspace Sidebar Item", True),
	):
		if not frappe.db.table_exists(doctype):
			continue
		for row in frappe.get_all(doctype, filters={"link_to": PAGE}, fields=["name", "label"]):
			values = {"link_to": TARGET}
			if relabel and (row.get("label") or "").strip().lower() in ("", "water engineering wizard"):
				values["label"] = TARGET_LABEL
			frappe.db.set_value(doctype, row["name"], values, update_modified=False)
			moved += 1
	print(f"{TITLE}: pointed {moved} row(s) at {TARGET}")


def _delete_desktop_icons():
	if not frappe.db.table_exists("Desktop Icon") or not frappe.db.has_column("Desktop Icon", "link_to"):
		return
	for name in frappe.get_all("Desktop Icon", filters={"link_to": PAGE}, pluck="name"):
		frappe.delete_doc("Desktop Icon", name, force=True, ignore_missing=True)
		print(f"{TITLE}: removed Desktop Icon {name}")
