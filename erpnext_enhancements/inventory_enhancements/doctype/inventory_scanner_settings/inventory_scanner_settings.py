"""Controller for the Inventory Scanner Settings Single doctype.

App-wide behaviour for the Inventory Scanner Audit page: the fallback
``default_warehouse`` used when an item is scanned before a location sets the
active warehouse, whether a reason is required for non-zero variances, whether
negative counts and unmatched item barcodes are allowed, and whether the
in-page camera scanner button is shown.

Exposes ``get_settings()``, a defensive reader (used by the scanner API and the
page bootstrap) that falls back to ``DEFAULTS`` for any unset field, so it is
safe even before the Single has ever been saved.

The **Stock Scan Page** section (v1.521.0) configures ``/stock-scan``
(``api/stock_scan.py``): the accounts a take and an add-without-PO post to, the cost
center, whether a take needs a job, and the undo window. Those fields were added to a
Single that already existed, so ``patches/backfill_stock_scan_settings_defaults`` writes
their declared defaults where no ``tabSingles`` row exists — and ``DEFAULTS`` below falls
back the same way, so the page is right even before that patch has run.

``boneyard_warehouse`` (v1.689.0) is where Stock Scan's *Send to boneyard*
(``inventory_enhancements.bin_assignments.send_to_boneyard``) moves stock a bin no longer
keeps. It declares no default, since the warehouse's name is per site:
``patches/add_boneyard_warehouse`` creates ``Boneyard`` under ``Inventory - SF`` and writes it
here once, only where ``tabSingles`` has no row for the field. Blank switches the action off.

The **Item Naming** section (v1.532.0) holds ``naming_digest_recipients``, who gets
``inventory_enhancements.item_naming_digest``'s Monday email. It has no default on purpose:
blank means nobody. ``patches/seed_naming_digest_recipient`` writes the Purchasing Agent's
address once, only where ``tabSingles`` has no row for the field, so a list somebody has
edited or deliberately emptied is never overwritten.

The **Reorder Board** section (v1.730.0) holds ``reorder_alert_role``, whose enabled users get
``inventory_enhancements.reorder_board.send_daily_digest`` (Nik, 2026-10-22: Purchase Managers).
It was added to a Single that already existed, so its ``Purchase Manager`` default reaches the
stored row only through ``patches/backfill_reorder_alert_role``; ``DEFAULTS`` and
``reorder_board.alert_role`` fall back to it as well, so blank always means Purchase Manager.
"""

import frappe
from frappe.model.document import Document

# Defaults used when the Single has never been saved or a field is blank. Kept
# in sync with the field defaults in inventory_scanner_settings.json.
DEFAULTS = {
	"default_warehouse": None,
	"require_variance_reason": 1,
	"block_negative_counts": 1,
	"allow_unknown_item": 0,
	"enable_camera_scan": 1,
	# Stock Scan page. A blank account or cost center means "use the fallback chain in
	# api.stock_scan", so None is the default, not a missing value.
	"take_expense_account": None,
	"add_offset_account": None,
	"scan_cost_center": None,
	"require_project_for_take": 0,
	# A stored 0 is a deliberate "undo off" and is kept; only a missing row falls back.
	"undo_window_minutes": 30,
	# The off switch for the page's browser Back/Forward (v1.534.0), in case iPhones re-prompt for
	# the camera. It declares no default on purpose: unticked is the answer for every site, and a
	# site that never saved the field reads None, which api.stock_scan's cint() takes as off. So
	# there is nothing for the backfill patch to write.
	"stock_scan_disable_browser_back": None,
	# Where "Send to boneyard" moves stock (v1.689.0, bin_assignments.send_to_boneyard). No
	# declared default: a Link's value differs per site. patches/add_boneyard_warehouse creates
	# the warehouse and fills this once, only where tabSingles has no row for it. Blank = off.
	"boneyard_warehouse": None,
	# Item naming digest. None: no recipients, no email.
	"naming_digest_recipients": None,
	# Reorder Board digest recipients (v1.730.0): this role's enabled users. Blank = the default.
	"reorder_alert_role": "Purchase Manager",
}


class InventoryScannerSettings(Document):
	pass


def get_settings():
	"""Return Inventory Scanner Settings as a dict, falling back to DEFAULTS for
	any unset/blank field. Safe to call before the Single has ever been saved."""
	doc = frappe.get_cached_doc("Inventory Scanner Settings")
	resolved = {}
	for key, default in DEFAULTS.items():
		value = doc.get(key)
		# Checks come back as 0/1; 0 is legitimate, so only fall back on None/"".
		resolved[key] = default if value in (None, "") else value
	return resolved
