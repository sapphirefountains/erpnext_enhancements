"""Put **Stock Scan** on the Desk: a home-grid tile and a Desk Shortcuts entry (v1.523.0).

Stock Scan (``/stock-scan``, v1.521.0) is how nearly everyone who touches inventory will
work it — technicians taking parts, receivers putting stock away — so it gets the same
two doors the Time Kiosk has, rather than living two clicks deep in the Inventory
Enhancements workspace:

1. **A ``Desktop Icon`` on the ``/desk`` home grid**, exactly the kiosk's shape
   (``patches/seed_time_kiosk_desktop_icon``): ``icon_type = "Link"``,
   ``link_type = "External"``, ``link = "/stock-scan"``, ``standard = 1``, artwork from
   ``setup/desktop_icon_map.TILES``. ``setup/desktop_icons._create_tile`` rightly refuses a
   label with no Workspace behind it, which is why a patch inserts it. **It needs its
   same-named ``Workspace Sidebar`` with a visible item** —
   ``get_desktop_icons`` drops a Link tile whose sidebar is missing or empty, whatever its
   ``link_type`` — and that ships declaratively as ``workspace_sidebar/stock_scan.json``.

   Unlike the kiosk tile it carries **roles**: the page's own gate,
   ``stock_scan_rules.SCAN_ROLES`` (Stock User, Stock Manager, Inventory Clerk, System
   Manager). ``Desktop Icon.roles`` is show/hide only — the page refuses anyone else
   itself — but a tile that opens onto "you do not have permission" is worse than no
   tile. ``setup/desktop_icons._sync_roles`` skips a label with no Workspace, so these
   roles are not overwritten on the next migrate. Stock User is held by 17 of the 18
   enabled staff, so in practice nearly everyone sees it.

   It takes the idx of ERPNext's own **Stock** tile, falling back to the Time Kiosk's,
   so it lands in the grid beside the module it belongs to (tiles sort by idx, ties in
   database order). A user can drag it anywhere; this is only the first placement.

2. **An ``Enhancement Desk Shortcut``** (the Home workspace's *Desk Shortcuts* block),
   the same pattern ``patches/seed_desk_shortcuts`` used for the others: a site-relative
   ``URL`` shortcut (the block navigates to it in the same tab), sequence 15 — right
   after the Time Kiosk and before the count page's *Inventory Scanner* — with the same
   roles.

Insert-only and keyed on the label: an admin who later moves, recolours, re-roles or
hides either one keeps their edit. Roles that do not exist on the site are skipped.
Never raises: a desk tile is cosmetics and must never abort the migrate that is the
deploy.
"""

import frappe

from erpnext_enhancements.inventory_enhancements.stock_scan_rules import SCAN_ROLES

LABEL = "Stock Scan"
LINK = "/stock-scan"
#: The tile lands beside this one (ERPNext's Stock workspace tile)...
NEAR = "Stock"
#: ...or, on a site without it, beside the other page-launcher tile.
FALLBACK_NEAR = "Time Kiosk"

SHORTCUT_ICON = "\U0001f4e6"  # 📦
SHORTCUT_COLOR = "Blue"
SHORTCUT_SEQUENCE = 15


def execute() -> None:
	for step in (seed_stock_scan_desktop_icon, seed_stock_scan_desk_shortcut):
		try:
			created = step()
			print(f"{step.__name__}: {'created' if created else 'already present'}")
		except Exception:
			frappe.log_error(title=step.__name__)

	# The tile list is cached per user and both lists ride in bootinfo; a new row is
	# invisible to everyone already signed in until these are dropped.
	frappe.cache.delete_key("desktop_icons")
	frappe.cache.delete_key("bootinfo")


def _existing_roles():
	return sorted(role for role in SCAN_ROLES if frappe.db.exists("Role", role))


def seed_stock_scan_desktop_icon() -> bool:
	if not frappe.db.exists("DocType", "Desktop Icon"):
		return False
	if frappe.db.exists("Desktop Icon", LABEL):
		return False

	from erpnext_enhancements.setup.desktop_icon_map import TILES, logo_url

	slug = TILES[LABEL][0]
	idx = frappe.db.get_value("Desktop Icon", NEAR, "idx")
	if idx is None:
		idx = frappe.db.get_value("Desktop Icon", FALLBACK_NEAR, "idx")

	icon = frappe.new_doc("Desktop Icon")
	icon.label = LABEL
	icon.standard = 1
	icon.icon_type = "Link"
	icon.link_type = "External"
	icon.link = LINK
	icon.logo_url = logo_url(slug)
	icon.hidden = 0
	if idx is not None:
		icon.idx = int(idx)
	for role in _existing_roles():
		icon.append("roles", {"role": role})
	icon.insert(ignore_permissions=True)
	return True


def seed_stock_scan_desk_shortcut() -> bool:
	if not frappe.db.exists("DocType", "Enhancement Desk Shortcut"):
		return False
	if frappe.db.exists("Enhancement Desk Shortcut", LABEL):
		return False

	doc = frappe.new_doc("Enhancement Desk Shortcut")
	doc.shortcut_label = LABEL
	doc.enabled = 1
	doc.sequence = SHORTCUT_SEQUENCE
	doc.link_type = "URL"
	doc.url = LINK
	doc.icon = SHORTCUT_ICON
	doc.color = SHORTCUT_COLOR
	doc.visible_to_all = 0
	for role in _existing_roles():
		doc.append("roles", {"role": role})
	doc.insert(ignore_permissions=True)
	return True
