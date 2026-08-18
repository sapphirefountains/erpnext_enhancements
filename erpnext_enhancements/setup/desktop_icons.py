"""Stamp our generated tile artwork onto the Desk home grid, every migrate.

Registered on ``after_migrate`` and ``after_install`` (hooks.py). Idempotent, and a
no-op in steady state: one ``get_value`` per tile and no writes once the site agrees
with ``desktop_icon_map.TILES``.

Why a hook and not a shipped ``desktop_icon/*.json``
---------------------------------------------------
``<app>/desktop_icon/`` IS an app-level sync folder in v16 (``frappe/model/sync.py``
``app_level_folders``), so shipping the records declaratively is possible -- but
``import_file_by_path`` is gated on the file's ``modified`` beating the row's, and
these rows already exist on every site, auto-created at some arbitrary past
timestamp. That makes "did my change land?" a timestamp race. Worse, a re-import
rewrites the whole row, including ``hidden`` and any ordering a user has dragged.

Stamping one field is both deterministic and surgical, and it self-heals: if
``create_desktop_icons_from_workspace()`` ever recreates a row from scratch, the
next migrate puts the artwork back.

Never raises. This is cosmetics -- a failure here must not abort a deploy, the way
``setup.workspace_tweaks`` must not.
"""

import frappe

from erpnext_enhancements.setup.desktop_icon_map import TILES, logo_url


def sync_desktop_icons():
	"""``after_migrate`` / ``after_install`` entry point. Contractually cannot raise."""
	try:
		_sync()
	except Exception:
		# Deliberately swallowed: a broken desk tile is not worth a failed migrate.
		frappe.log_error(title="Desktop icon sync failed")


def _sync():
	changed = False

	for label, (slug, _glyph, _colour) in TILES.items():
		if not frappe.db.exists("Desktop Icon", label):
			if not _create_tile(label):
				continue
			changed = True

		url = logo_url(slug)
		if frappe.db.get_value("Desktop Icon", label, "logo_url") == url:
			continue

		frappe.db.set_value("Desktop Icon", label, "logo_url", url)
		changed = True

	if changed:
		# `Desktop Icon` is read_only:1 and we wrote past the ORM, so its `on_update`
		# -- which is what normally invalidates these two -- never ran. Both keys are
		# required: `desktop_icons` holds the per-user icon list, `bootinfo` embeds it.
		frappe.cache.delete_key("desktop_icons")
		frappe.cache.delete_key("bootinfo")


def _create_tile(label):
	"""Create the missing tile for a workspace we ship. Return True if it now exists.

	``create_desktop_icons()`` only runs at install/upgrade, so a workspace added to
	this app afterwards never gets a tile and is simply absent from the desk -- which
	is what happened to Training and Shipping.

	A Desktop Icon alone is not enough: ``get_desktop_icons()`` gates every ``Link``
	tile on ``bootinfo.workspace_sidebar_item[label.lower()]`` having items, so a tile
	with no matching Workspace Sidebar never renders. Frappe's own
	``add_workspace_to_desktop`` creates both, and is careful in exactly the way we
	need -- it reuses an existing sidebar rather than replacing it, and only appends
	the workspace link if it is not already there.
	"""
	if not frappe.db.exists("Workspace", label):
		return False

	from frappe.desk.doctype.desktop_icon.desktop_icon import add_workspace_to_desktop

	add_workspace_to_desktop(label)
	return bool(frappe.db.exists("Desktop Icon", label))
