# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Give our desk areas icon names frappe v16.50 can draw (v1.575.1).

v16.50's Dock rail and module sidebars draw an icon with ``frappe.utils.icon(name)``: an
``<svg><use href="#icon-<name>">`` into the sprites frappe and ERPNext load. A name no sprite has
draws nothing and raises nothing, so the entry shows as a blank slot. On 2026-10-07 five slots on
our rail were blank, because the one-time conversion to the new desk copied each workspace's
``icon`` into the Dock Item row and the "<area> (Custom)" Sidebar it made for that workspace, and
six of our names are in no v16.50 sprite: ``assistant``, ``mobile``, ``money``, ``service`` and
``water`` never were, and ``book-marked`` was dropped by 16.50's lucide upgrade (it is now
``book-bookmark``). The old desk drew our home tiles from ``logo_url`` artwork and never looked
these names up, which is why nothing showed it until the rail did.

The workspace JSONs now carry each area's /desk tile glyph (``setup/desktop_icon_map.TILES``), so
the rail and the tile show the same picture. This patch puts the same name on the copies already in
the database: ``Workspace.icon``, ``Sidebar.header_icon`` and ``Dock Item.icon``. A row changes only
when it belongs to one of these areas AND still holds one of the broken names, so an icon someone
picked by hand is never overwritten. Left alone on purpose:

* ``Desktop Icon.icon``, which nothing on the v16.50 desk reads (the tile is its ``logo_url``);
* the workspace JSON ``modified`` stamps, which stay as they were. Bumping one makes migrate
  re-import that workspace, and on 16.50 a re-import wipes the site's edits to a standard one.
  Knowledge Base is the exception: its suite requires a bump with every content change, and
  prod's row still carried the file's own stamp (2026-09-29 12:00), so nobody had edited it.
  Model sync re-imports it before this patch runs, which then finds nothing to do there;
* frappe's own Build sidebar, whose DocType entry names ``file-code-2`` (now ``file-code``).
  That row is frappe's, and its next re-import would undo an edit here.

``post_model_sync``. Each table is its own step; the patch cannot raise, because a raising patch
aborts ``bench migrate``, which on this repo is the deploy. Safe to run twice: the second run finds
no broken names.
"""

import frappe

# Area (the workspace, and the sidebar the conversion made for it) -> the icon it should show.
ICONS = {
	"AI Governance": "bot",
	"Design Hub": "palette",
	"Devices": "smartphone",
	"Finance Hub": "wallet",
	"Knowledge Base": "book-bookmark",
	"QuickBooks Online": "book-open-check",
	"Sapphire Maintenance": "wrench",
	"Support Hub": "life-buoy",
	"Water Engineering": "droplets",
}

# Names our JSONs shipped that no v16.50 sprite draws. Only these are ever replaced.
BROKEN = ("assistant", "book-marked", "mobile", "money", "service", "water")

# The suffix the conversion gave the sidebar it cloned from an app-less v16 sidebar.
CONVERTED = " (Custom)"

# (doctype, icon field, the field that names the area)
TARGETS = (
	("Workspace", "icon", "name"),
	("Sidebar", "header_icon", "name"),
	("Dock Item", "icon", "link_to"),
)


def execute():
	changed = 0
	for doctype, field, key in TARGETS:
		try:
			count = _heal(doctype, field, key)
		except Exception:
			frappe.db.rollback()
			print(f"heal_desk_icon_names: {doctype} failed; skipped")
			frappe.log_error(title=f"heal_desk_icon_names: {doctype}")
		else:
			frappe.db.commit()
			changed += count
	# Migrate clears the cache before its patches, not after, and the rail is drawn from boot data.
	if changed:
		try:
			frappe.clear_cache()
		except Exception:
			frappe.log_error(title="heal_desk_icon_names: clear_cache")


def area(name):
	"""The ICONS key a workspace, sidebar or dock target belongs to, or None."""
	name = name or ""
	if name.endswith(CONVERTED):
		name = name[: -len(CONVERTED)]
	return name if name in ICONS else None


def _heal(doctype, field, key):
	"""Fix one table's broken names; return how many rows changed."""
	# A site that has not reached the v16.50 desk has no Sidebar or Dock Item table.
	if not frappe.db.table_exists(doctype):
		return 0
	rows = frappe.get_all(
		doctype,
		filters={field: ("in", BROKEN)},
		fields=sorted({"name", key, field}),
	)
	changed = 0
	for row in rows:
		target = area(row.get(key))
		if not target:
			print(
				f"heal_desk_icon_names: {doctype} {row.get('name')} names {row.get(field)!r}; not ours, left"
			)
			continue
		frappe.db.set_value(doctype, row.get("name"), field, ICONS[target], update_modified=False)
		print(f"heal_desk_icon_names: {doctype} {row.get(key)}: {row.get(field)} -> {ICONS[target]}")
		changed += 1
	return changed
