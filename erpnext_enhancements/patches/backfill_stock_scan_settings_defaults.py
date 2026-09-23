"""Give the existing Inventory Scanner Settings row the Stock Scan page's defaults (v1.521.0).

A ``default`` on a new field of a Single never reaches the row that already exists: a
Single stores one ``tabSingles`` row per field, ``bench migrate`` adds none for a new
field, and defaults only fire in ``new_doc()``. So without this the settings page would
show a blank Undo Window while ``get_settings()`` quietly used 30 — two answers to "how
long can I undo", and the visible one wrong.

Same shape as ``backfill_lead_triage_settings_defaults``: it touches **only the fields
this release added** (``FIELDS``), and fills a field only when it has **no row**, never
over a stored value. An unticked box and a deliberate 0 are not the same fact, and a
Stock Manager who sets the window to 0 has switched undo off on purpose.

Safe to run twice: the second run finds rows for everything and writes nothing.
"""

import frappe

SETTINGS_DOCTYPE = "Inventory Scanner Settings"

FIELDS = (
	"require_project_for_take",
	"undo_window_minutes",
)


def execute() -> None:
	backfill()


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
	# A Single that has never been saved has no rows at all, and then Frappe's load_from_db
	# builds it from new_doc() -- every declared default applies, these two included. Writing
	# even one row here would end that: the next load finds rows, stops using defaults, and
	# every Check with default 1 that has no row (enable_camera_scan, block_negative_counts,
	# require_variance_reason) reads 0 -- hiding the count page's camera button. So a
	# never-saved Single is left alone. (Production's was saved on 2026-06-13.)
	if not stored:
		return 0
	meta = frappe.get_meta(SETTINGS_DOCTYPE)

	written = 0
	for fieldname in FIELDS:
		field = meta.get_field(fieldname)
		if not field or field.default in (None, "") or fieldname in stored:
			continue
		# Direct write, not the document API: a patch must not be hostage to validate.
		frappe.db.set_single_value(SETTINGS_DOCTYPE, fieldname, field.default)
		written += 1

	if written:
		frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)
	return written
