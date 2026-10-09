"""Small, idempotent tweaks to core (erpnext-owned) workspaces / sidebars.

Registered in ``after_migrate`` (hooks.py), so these run after Frappe has synced
the standard workspace + Workspace Sidebar records from every app — letting us
re-assert an override even if a core app re-imported its version earlier in the
same migrate.

frappe 16.50 draws a module's sidebar from a ``Sidebar`` document (the areas on the
Dock rail) and no longer reads ``Workspace Sidebar``. The same items are hidden in
both, so this works on either side of that upgrade.
"""

import frappe

# (sidebar name, [(link_type, link_to, label), ...]) sidebar items to hide. The name is
# both the Workspace Sidebar's (before frappe 16.50) and the Sidebar's (16.50 on).
_HIDDEN_SIDEBAR_ITEMS = {
	# Hide the "Project" DocType link in the default Projects module sidebar
	# (user request, "for now"). The Workspace Sidebar Item child has no `hidden`
	# field, so the row is removed. Kept surgical — the Dashboard link (also
	# link_to=Project but link_type=Dashboard) and every other item stay.
	"Projects": [("DocType", "Project", "Project")],
}


def hide_core_sidebar_items():
	"""``after_migrate`` entry point: remove specific links from core sidebars.

	Idempotent: only saves when a targeted item is actually present, so on steady
	state it is a no-op. Saving bumps the sidebar's ``modified`` past the shipping
	app's file stamp, so Frappe's ``modified``-gated sync will not re-import (and
	re-add) the item on later migrates; if a core upgrade ever does bump its file
	and re-add the link, this hook (running after that sync) drops it again.
	"""
	for sidebar_name, targets in _HIDDEN_SIDEBAR_ITEMS.items():
		if not frappe.db.exists("Workspace Sidebar", sidebar_name):
			continue

		doc = frappe.get_doc("Workspace Sidebar", sidebar_name)
		drop = set(targets)
		kept = [
			it for it in doc.items
			if (it.link_type, it.link_to, it.label) not in drop
		]
		if len(kept) == len(doc.items):
			continue  # nothing to remove — no-op

		doc.items = kept
		doc.flags.ignore_permissions = True
		doc.save()

	try:
		_hide_sidebar_items()
	except Exception:
		# Cosmetic: a sidebar link is not worth a failed deploy.
		frappe.log_error(title="Core sidebar item not hidden", message=frappe.get_traceback())


def _hide_sidebar_items():
	"""Set ``hidden`` on the same items in frappe 16.50's ``Sidebar`` documents.

	The loop above stopped working on the 2026-10-06 upgrade: the desk now builds the
	Projects sidebar from ERPNext's ``Sidebar`` "Projects", whose "Project" link came back.

	The row is flagged rather than removed, and ERPNext's own row is the one flagged,
	because of how 16.50 lays a site's changes over an app's sidebar
	(``frappe/desk/layers.py``). A site change goes in a ``Custom Sidebar`` layer, and a
	layer that names any of the app's rows is read as an arrangement: the rows it names
	come first. A one-row layer hiding this link would therefore put the next workspace
	anyone creates in the Projects module (``add_site_sidebar_item`` appends it to that
	layer) above Home, and the module opens on its first item. The app's row may carry
	``hidden`` itself ("so the base can hide", ``sidebar.get_sidebar_items``), which
	changes no order and leaves the layers free. A Workspace Manager can still show the
	link again from the sidebar editor, because a site layer's ``hidden: 0`` wins.

	Written with ``db.set_value``, not by saving the document: outside developer mode
	``Sidebar.validate_app_content`` refuses a save except during a migrate, and in
	developer mode a save exports the JSON into ERPNext's own folder. An ERPNext release
	that ships a newer file re-imports the row unflagged; this runs after that import in
	the same migrate and flags it again.
	"""
	if not frappe.db.exists("DocType", "Sidebar") or not frappe.db.has_column("Sidebar Item", "hidden"):
		return

	changed = False
	for sidebar_name, targets in _HIDDEN_SIDEBAR_ITEMS.items():
		for link_type, link_to, label in targets:
			rows = frappe.get_all(
				"Sidebar Item",
				filters={
					"parenttype": "Sidebar",
					"parent": sidebar_name,
					"link_type": link_type,
					"link_to": link_to,
					"label": label,
					"hidden": 0,
				},
				pluck="name",
			)
			for name in rows:
				frappe.db.set_value("Sidebar Item", name, "hidden", 1, update_modified=False)
				changed = True

	if changed:
		# Every user's boot carries the resolved sidebars; this is the key frappe's own
		# sidebar customizations clear when they change one.
		frappe.cache.delete_key("bootinfo")


# (sidebar name, [item, ...]) links this app adds to a core (app-owned) sidebar. Each item is
# placed right after its ``after`` row, matched on (link_type, link_to). Nik, 2026-10-09: "I'd
# also like the Project Planner to exist in the Projects module". Crew Utilization is the
# planner's own report, so it goes beside the module's other reports.
_ADDED_SIDEBAR_ITEMS = {
	"Projects": [
		{
			"label": "Project Planner",
			"link_type": "Page",
			"link_to": "project-planner",
			"icon": "calendar-range",
			"child": 0,
			"after": ("DocType", "Task"),
		},
		{
			"label": "Crew Utilization",
			"link_type": "Report",
			"link_to": "Crew Utilization",
			"icon": None,
			"child": 1,
			"after": ("Report", "Project wise Stock Tracking"),
		},
	],
}


def add_core_sidebar_items():
	"""``after_migrate`` entry point: add this app's links to frappe 16.50's core ``Sidebar`` documents.

	Written into ERPNext's own rows, for the reason ``_hide_sidebar_items`` gives: a ``Custom
	Sidebar`` layer that names nothing in the base is read as a set of appends and lands at the
	very end (after Setup's Settings), and one that names any base row is an arrangement that
	reorders the module. The base is read from the database in ``idx`` order
	(``sidebar.get_sidebar_items``), so a row inserted at the right ``idx`` sits exactly where it
	belongs, and the desk still drops it for anyone who cannot open the page or report.

	Rows go in with ``db_insert`` and the ones below shift with ``db.set_value``, not by saving
	the document: ``Sidebar.validate_app_content`` refuses a save outside a migrate, and in
	developer mode a save would export the JSON into ERPNext's own folder. An ERPNext release that
	ships a newer sidebar file re-imports it without our rows; this runs after that import in the
	same migrate and adds them again.

	Idempotent: an item already present under the same (link_type, link_to), whatever its label or
	``hidden``, is left exactly as it is, so a Workspace Manager who hid it keeps it hidden. An
	item whose anchor row is missing is skipped rather than guessed at. Never raises.
	"""
	try:
		_add_sidebar_items()
	except Exception:
		# Cosmetic: a sidebar link is not worth a failed deploy.
		frappe.log_error(title="Core sidebar item not added", message=frappe.get_traceback())


def _add_sidebar_items():
	if not frappe.db.exists("DocType", "Sidebar"):
		return

	changed = False
	for sidebar_name, additions in _ADDED_SIDEBAR_ITEMS.items():
		if not frappe.db.exists("Sidebar", sidebar_name):
			continue
		for item in additions:
			rows = frappe.get_all(
				"Sidebar Item",
				filters={"parenttype": "Sidebar", "parent": sidebar_name},
				fields=["name", "idx", "link_type", "link_to"],
				order_by="idx asc",
			)
			if any((r.get("link_type"), r.get("link_to")) == (item["link_type"], item["link_to"]) for r in rows):
				continue
			anchor = next((r for r in rows if (r.get("link_type"), r.get("link_to")) == item["after"]), None)
			if anchor is None:
				continue
			idx = int(anchor.get("idx") or 0) + 1
			for row in rows:
				if int(row.get("idx") or 0) >= idx:
					frappe.db.set_value(
						"Sidebar Item", row["name"], "idx", int(row["idx"]) + 1, update_modified=False
					)
			frappe.get_doc(
				{
					"doctype": "Sidebar Item",
					"parent": sidebar_name,
					"parenttype": "Sidebar",
					"parentfield": "items",
					"idx": idx,
					"type": "Link",
					"label": item["label"],
					"link_type": item["link_type"],
					"link_to": item["link_to"],
					"icon": item["icon"],
					"child": item["child"],
					"indent": 0,
					"collapsible": 1,
					"keep_closed": 0,
					"show_arrow": 0,
					"open_in_new_tab": 0,
					"is_default_module": 0,
				}
			).db_insert()
			changed = True

	if changed:
		frappe.cache.delete_key("bootinfo")
