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

A saved home-screen layout never sees a new tile
------------------------------------------------
v16 draws the home grid from the person's own ``Desktop Layout`` row whenever they have
one, not from the site's icons: ``desk/page/desktop/desktop.py:16-20`` renders that row
into the page, and ``desktop.js:220-229`` ``sync_layout`` uses it *instead of*
``frappe.boot.desktop_icons``, falling back only when it is empty. The row is a frozen copy
of the icon list, taken when the person last pressed Save in Edit Layout
(``desktop_layout.py:28-42``), and nothing in v16 adds a later icon to it. So a tile created
after that save never reaches them, and Edit Layout cannot add it back either: its "Removed
Icons" pane is built from the same saved list (``desktop.js:1290-1306``). Only Reset Layout
would, at the cost of their arrangement. That is why, on prod, Training, Time Kiosk and
Quality Control are missing from every layout saved before they existed.
:func:`_sync_saved_layouts` appends the ``ADD_TO_SAVED_LAYOUTS`` tiles to those layouts
(WI-080 PR 4 review).
"""

import json

import frappe

from erpnext_enhancements.setup.desktop_icon_map import TILES, logo_url

#: Tiles appended to every saved home-screen layout that does not hold them yet (see the module
#: docstring). **Only a tile every Desk User may open belongs here.** A saved layout is drawn
#: verbatim: none of ``get_desktop_icons``' role and sidebar checks (``desktop_icon.py:176-205``)
#: runs on it, so a role-gated tile added here would show to people its page refuses.
#:
#: Knowledge Base qualifies: its workspace has no roles, so its tile has none (``_sync_roles``),
#: and every System User reads Knowledge Article, which is the workspace's module gate
#: (``tests/test_knowledge_base_entry_points.py`` pins both). Time Kiosk would qualify too, but it is
#: outside WI-080, so adding it is left to its own change. A role-gated tile (a team hub) never does.
ADD_TO_SAVED_LAYOUTS = ("Knowledge Base",)

#: What a saved layout holds for each icon: the columns ``get_desktop_icons`` reads
#: (``desktop_icon.py:130-147``), because the layout is a copy of that list.
LAYOUT_FIELDS = (
	"label",
	"bg_color",
	"link",
	"link_type",
	"app",
	"icon_type",
	"parent_icon",
	"icon",
	"link_to",
	"idx",
	"standard",
	"logo_url",
	"hidden",
	"name",
	"restrict_removal",
	"icon_image",
)


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

	if _sync_roles():
		changed = True

	if _sync_saved_layouts():
		changed = True

	if changed:
		# `Desktop Icon` is read_only:1 and we wrote past the ORM, so its `on_update`
		# -- which is what normally invalidates these two -- never ran. Both keys are
		# required: `desktop_icons` holds the per-user icon list, `bootinfo` embeds it.
		frappe.cache.delete_key("desktop_icons")
		frappe.cache.delete_key("bootinfo")


def _sync_roles():
	"""Derive every tile's `roles` from its workspace's. Return True if anything moved.

	**A tile is visible exactly when its page is openable**, which is the only rule
	that does not rot. `Desktop Icon.roles` and `Workspace.roles` are two separate
	gates in v16 -- `get_desktop_icons` intersects the first with the user's roles
	(desktop_icon.py:182,:200), `Workspace.is_permitted` reads the second
	(desk/desktop.py:59-74) -- so keeping them as two hand-maintained lists would
	guarantee they drift, and the drift is silent in both directions: a tile that
	opens a refusal, or a page nobody can find.

	Deriving also means the decision is recorded once, in the workspace JSON that
	ships in this repo, rather than twice.

	**`Desktop Icon.roles` is show/hide, never a permission boundary.** Typing the
	route still works; `Workspace.roles` is what actually refuses the page, and
	DocPerms are what refuse the data. Nothing here is access control.

	Note `roles: []` on a workspace means "no restriction beyond the module gate",
	not "nobody" -- so an empty desired set correctly clears the tile's roles and
	leaves it visible to everyone, which is what 26 of the 34 shipped workspaces want.

	This function must NEVER call `get_desktop_icons()`. Without `bootinfo` that
	returns [] and then caches the empty list per user (desktop_icon.py:184-212),
	which would blank the home grid for everybody until the cache expired.
	"""
	changed = False

	for label in TILES:
		if not frappe.db.exists("Workspace", label) or not frappe.db.exists("Desktop Icon", label):
			continue

		desired = set(
			frappe.get_all(
				"Has Role",
				filters={"parenttype": "Workspace", "parent": label},
				pluck="role",
			)
			or []
		)
		current = set(
			frappe.get_all(
				"Has Role",
				filters={"parenttype": "Desktop Icon", "parent": label},
				pluck="role",
			)
			or []
		)
		if desired == current:
			continue

		# Through the ORM, because this is a child table. The logo_url stamp above
		# writes past it deliberately; a child table cannot be set that way.
		doc = frappe.get_doc("Desktop Icon", label)
		doc.set("roles", [{"role": role} for role in sorted(desired)])
		doc.save(ignore_permissions=True)
		changed = True

	return changed


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


def _sync_saved_layouts():
	"""Append each ``ADD_TO_SAVED_LAYOUTS`` tile to every saved layout that lacks it. Return True if
	any layout changed.

	Runs after the tiles are made and stamped, so the copy carries the current artwork. A no-op in
	steady state: once every layout holds the tile, nothing is written.
	"""
	if not frappe.db.table_exists("Desktop Layout"):
		return False

	icons = {}
	for label in ADD_TO_SAVED_LAYOUTS:
		row = frappe.db.get_value("Desktop Icon", label, list(LAYOUT_FIELDS), as_dict=True)
		if row:
			icons[label] = dict(row)
	if not icons:
		return False

	changed = False
	for row in frappe.get_all("Desktop Layout", fields=["name", "layout"], order_by="name"):
		layout = layout_with(row.get("layout"), icons)
		if layout is None:
			continue
		# Past the ORM and without moving `modified`: this is the person's own row, and the only
		# change is one tile placed after all of theirs. Desktop Layout has no controller logic.
		frappe.db.set_value("Desktop Layout", row.get("name"), "layout", layout, update_modified=False)
		changed = True

	return changed


def layout_with(layout, icons):
	"""Return a saved layout's JSON with every icon in ``icons`` it lacks appended after the person's
	own tiles, or None when there is nothing to change. Pure.

	``icons`` maps a label to that Desktop Icon's row (``LAYOUT_FIELDS``). An icon the layout already
	holds is left alone, hidden or not: hiding one keeps its entry with ``hidden: 1``
	(``desktop.js:1024-1035``), and v16 has no other way to take one out, so an absent label is a
	tile made after the save, never one the person removed. An empty layout is left alone, because
	v16 then draws the site's own list, which has the tile already (``desktop.js:225-229``); so is
	one this cannot read, which is not this code's to rewrite.
	"""
	if not layout or not icons:
		return None
	try:
		entries = json.loads(layout)
	except (TypeError, ValueError):
		return None
	if not isinstance(entries, list) or not entries or not all(isinstance(e, dict) for e in entries):
		return None

	present = {entry.get("label") for entry in entries}
	missing = [label for label in icons if label not in present]
	if not missing:
		return None

	# After the last of theirs: the grid sorts by idx, then label (desktop.js:686-691).
	after = max(_idx(entry) for entry in entries) + 1
	for offset, label in enumerate(missing):
		entries.append({**icons[label], "label": label, "idx": after + offset})
	return json.dumps(entries)


def _idx(entry):
	try:
		return int(entry.get("idx") or 0)
	except (TypeError, ValueError):
		return 0
