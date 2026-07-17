"""Small, idempotent tweaks to core (erpnext-owned) workspaces / sidebars.

Registered in ``after_migrate`` (hooks.py), so these run after Frappe has synced
the standard workspace + Workspace Sidebar records from every app — letting us
re-assert an override even if a core app re-imported its version earlier in the
same migrate.
"""

import frappe

# (Workspace Sidebar name, [(link_type, link_to, label), ...]) sidebar items to drop.
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
