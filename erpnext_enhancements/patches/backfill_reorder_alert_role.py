"""Give the existing Inventory Scanner Settings row the Reorder Board's alert role (v1.730.0).

``reorder_alert_role`` (default ``Purchase Manager``) names who gets the Reorder Board's morning
digest. A ``default`` on a new field of a Single never reaches the row that already exists: a
Single stores one ``tabSingles`` row per field, ``bench migrate`` adds none for a new field, and
defaults only fire in ``new_doc()``. Without this the settings page would show a blank role while
the digest quietly used Purchase Manager -- two answers to "who gets it", the visible one wrong.
(``reorder_board.alert_role`` and ``DEFAULTS`` fall back to Purchase Manager too, so the digest is
right before this runs.)

**What it writes:** one ``tabSingles`` row, ``Inventory Scanner Settings.reorder_alert_role`` =
``Purchase Manager``, only where there is **no row** for that field, never over a stored value.
A never-saved Single is left alone: one written row would stop it loading its declared defaults,
and its default-1 Checks would read 0 (production's was saved on 2026-06-13 and has 16 rows).
``tabSingles`` is read with a plain ``select field``, never ``db.get_value("Singles", ...)``.

Same shape as ``backfill_stock_scan_settings_defaults``. Never raises; safe twice.
"""

import frappe

SETTINGS_DOCTYPE = "Inventory Scanner Settings"
FIELDS = ("reorder_alert_role",)


def execute() -> None:
	try:
		backfill()
	except Exception:
		frappe.log_error(title="backfill_reorder_alert_role failed", message=frappe.get_traceback())


def backfill() -> int:
	"""Write each listed field's declared default where no row exists. Returns rows written."""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return 0
	stored = {
		row[0]
		for row in frappe.db.sql(
			"select field from tabSingles where doctype = %s",
			(SETTINGS_DOCTYPE,),
		)
	}
	if not stored:
		return 0
	meta = frappe.get_meta(SETTINGS_DOCTYPE)
	written = 0
	for fieldname in FIELDS:
		field = meta.get_field(fieldname)
		if not field or field.default in (None, "") or fieldname in stored:
			continue
		frappe.db.set_single_value(SETTINGS_DOCTYPE, fieldname, field.default)
		written += 1
	if written:
		frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)
	print(f"backfill_reorder_alert_role: {written} written")
	return written
