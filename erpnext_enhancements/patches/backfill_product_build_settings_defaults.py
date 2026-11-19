"""Give Product Build Settings a stored row for every field that declares a default (v1.829.0).

v1.829.0 adds **Email the Customer When It Ships** (``send_shipped_email``, default 1) to a Single
that v1.828.0 created. A default on a field added later never reaches a Single someone has already
saved (CLAUDE.md): ``tabSingles`` has no row for it, the settings page then shows the box unticked,
and the next save writes 0, switching the Shipped email off without anyone choosing to. The code
reads a missing row as on (``settings.get_settings``), but the page is what a person saves.

This writes each declared default **only where it has no row**, never over a stored value: an
unticked box and one nobody has saved are both 0, and only the second may be filled. It runs on
``after_migrate`` too, so the next field that declares a default gets its row on the migrate that
adds it. On a Single nobody has saved it writes every default, which is exactly what the page
would show and what ``frappe.get_cached_doc`` already reads; the three warehouses and the test
template's blanks are not touched (no default, or none to write).

Same shape as ``backfill_carrier_tracking_settings_defaults``. Never raises; safe twice.
"""

import frappe
from frappe.model import no_value_fields

SETTINGS_DOCTYPE = "Product Build Settings"


def execute() -> None:
	backfill_product_build_settings_defaults()


def backfill_product_build_settings_defaults() -> int:
	"""Write each declared default that has no stored row. Returns how many it wrote."""
	try:
		if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
			return 0
		stored = {
			row[0]
			for row in frappe.db.sql("select field from tabSingles where doctype = %s", (SETTINGS_DOCTYPE,))
		}
		written = 0
		for field in frappe.get_meta(SETTINGS_DOCTYPE).fields:
			if field.fieldtype in no_value_fields or field.fieldtype == "Password":
				continue
			if field.default in (None, "") or field.fieldname in stored:
				continue
			frappe.db.set_single_value(SETTINGS_DOCTYPE, field.fieldname, field.default)
			written += 1
		if written:
			frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)
		return written
	except Exception:
		frappe.log_error(title="Product Build Settings defaults backfill failed")
		return 0
