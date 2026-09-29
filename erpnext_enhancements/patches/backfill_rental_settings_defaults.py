"""Give an already-saved Rental Settings the defaults of the fields v1.566.0 added.

Rental Settings is a Single that arrived in v1.564.0. If anyone saved it before this release (to fill
in the invoice items, as its setup note asks), it has ``tabSingles`` rows — and a ``default`` on a
field added since never reaches a Single that has rows. ``crew_digest`` and ``generate_inspections``
ship ticked; without this they would read 0 on such a site, so the crew would get no 6am digest and no
checklists would be made, with the settings page showing the boxes unticked and nothing saying why.

The same shape as ``backfill_stock_scan_settings_defaults``: only the fields in ``FIELDS``; a field is
filled only where it has **no row**, never over a stored value (an unticked box is a decision); and a
Single never saved at all is left alone, because it is built from ``new_doc()`` with every default,
and writing one row would end that for the others. The reminder switches are not listed: they ship
off, which is what a missing row already reads as.

Safe twice: the second run finds rows and writes nothing.
"""

import frappe

SETTINGS_DOCTYPE = "Rental Settings"

FIELDS = (
	"crew_digest",
	"generate_inspections",
)


def execute() -> None:
	backfill()


def backfill() -> int:
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return 0
	stored = {
		row[0]
		for row in frappe.db.sql("select field from tabSingles where doctype = %s", (SETTINGS_DOCTYPE,))
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
	return written
