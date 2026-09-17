"""Put a **Time Kiosk** tile on the Desk home grid that opens ``/kiosk`` directly.

Every other tile this app ships fronts a workspace, and ``setup/desktop_icons.py`` creates
those through frappe's own ``add_workspace_to_desktop`` — which rightly refuses a label with
no Workspace behind it. The kiosk is not a workspace; it is a standalone PWA at ``/kiosk``.
So this patch inserts the ``Desktop Icon`` itself, once, insert-only:

* ``icon_type = "Link"``, ``link_type = "External"``, ``link = "/kiosk"`` —
  ``frappe.utils.get_route_for_icon`` (v16 ``utils.js``) routes an External tile straight to
  its ``link`` and never consults a sidebar.
* ``standard = 1`` and **no roles**: ``get_desktop_icons`` shows a standard tile to every user
  and only intersects roles when the tile lists some. The time clock is the one thing every
  technician touches, so everybody sees it.
* ``logo_url`` from ``setup/desktop_icon_map.TILES`` — the after-migrate stamper keeps it
  honest from then on (it stamps any tile that *exists*; it only declines to *create* one).
* ``idx`` right after the Workforce tile, so it lands beside its module rather than at the
  end of the grid. A user can still drag it; this is only the first placement.

**The sidebar half is not optional.** ``get_desktop_icons`` (v16 ``desktop_icon.py``)
permits a ``Link`` tile only when ``bootinfo.workspace_sidebar_item[label.lower()]`` has
items — it does not check ``link_type`` — so an External tile with no same-named
``Workspace Sidebar`` is silently dropped from the grid, which is exactly how the stale
``Learning`` tile stayed invisible until v1.326.0. The sidebar ships declaratively as
``workspace_sidebar/time_kiosk.json`` (a new record, so the import age gate cannot skip it)
with a single URL item to ``/kiosk``. This patch does not create it and does not depend on
it; the two are checked together by ``tests/test_time_kiosk_desktop_icon.py``.

Never raises. Safe twice: the row is keyed on its label.
"""

import frappe

LABEL = "Time Kiosk"
LINK = "/kiosk"
AFTER = "Workforce"


def execute() -> None:
	try:
		created = seed_time_kiosk_desktop_icon()
		print(f"seed_time_kiosk_desktop_icon: {'created' if created else 'already present'}")
	except Exception:
		# A desk tile is cosmetics; it must never abort the migrate that is the deploy.
		frappe.log_error(title="seed_time_kiosk_desktop_icon")


def seed_time_kiosk_desktop_icon() -> bool:
	if not frappe.db.exists("DocType", "Desktop Icon"):
		return False
	if frappe.db.exists("Desktop Icon", LABEL):
		return False

	from erpnext_enhancements.setup.desktop_icon_map import TILES, logo_url

	slug = TILES[LABEL][0]
	after_idx = frappe.db.get_value("Desktop Icon", AFTER, "idx")

	icon = frappe.new_doc("Desktop Icon")
	icon.label = LABEL
	icon.standard = 1
	icon.icon_type = "Link"
	icon.link_type = "External"
	icon.link = LINK
	icon.logo_url = logo_url(slug)
	icon.hidden = 0
	if after_idx is not None:
		icon.idx = int(after_idx) + 1
	icon.insert(ignore_permissions=True)

	# The tile list is cached per user and embedded in bootinfo; a new standard row is
	# invisible to everyone already signed in until both are dropped.
	frappe.cache.delete_key("desktop_icons")
	frappe.cache.delete_key("bootinfo")
	return True
