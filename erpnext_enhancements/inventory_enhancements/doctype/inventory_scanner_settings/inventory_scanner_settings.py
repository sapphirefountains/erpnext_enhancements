"""Controller for the Inventory Scanner Settings Single doctype.

App-wide behaviour for the Inventory Scanner Audit page: the fallback
``default_warehouse`` used when an item is scanned before a location sets the
active warehouse, whether a reason is required for non-zero variances, whether
negative counts and unmatched item barcodes are allowed, and whether the
in-page camera scanner button is shown.

Exposes ``get_settings()``, a defensive reader (used by the scanner API and the
page bootstrap) that falls back to ``DEFAULTS`` for any unset field, so it is
safe even before the Single has ever been saved.
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
