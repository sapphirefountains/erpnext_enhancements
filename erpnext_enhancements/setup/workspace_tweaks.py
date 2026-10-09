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
