"""One-time cleanup: delete the orphaned "Learning" desk tile.

A ``Desktop Icon`` labelled **Learning** points at ``link_to = "Learning"``, a
Workspace that no longer exists -- the Training module's workspace is named
**Training**. The row is a leftover from before that rename.

It is already invisible, and for a reason worth knowing: ``get_desktop_icons()``
resolves a ``Link`` tile through ``bootinfo.workspace_sidebar_item[label.lower()]``
and drops any tile whose sidebar is missing or empty. There is no ``Workspace
Sidebar`` named "Learning" either, so the tile is filtered out of every user's grid
before it is ever drawn. Nothing is broken today; this is removing a row that can
only ever confuse whoever reads ``tabDesktop Icon`` next.

Deliberately a patch rather than a line in ``setup.desktop_icons``: deletion is a
one-time correction of a stale name, and an ``after_migrate`` hook that deleted it
on every run would fight anyone who later creates a legitimate Learning tile.

Guarded on the workspace still being absent, so if "Learning" is ever a real
workspace again this becomes a no-op instead of deleting a live tile. Safe twice.
"""

import frappe

LABEL = "Learning"


def execute():
	if not frappe.db.exists("Desktop Icon", LABEL):
		return

	if frappe.db.exists("Workspace", LABEL):
		# Somebody rebuilt it for real -- leave it alone.
		return

	# force=True because `parent_icon` is a Link back onto Desktop Icon and a dead row
	# is not worth failing a migrate over a link check.
	frappe.delete_doc("Desktop Icon", LABEL, ignore_missing=True, force=True)

	# on_trash clears the cache for the *current* user only (`clear_desktop_icons_cache`
	# takes a user), and the stale entry sits in every user's cached icon list.
	frappe.cache.delete_key("desktop_icons")
	frappe.cache.delete_key("bootinfo")
