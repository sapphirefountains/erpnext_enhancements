"""Give the **existing** Triton Assistant Settings row the ``Classic`` its new *Widget* field declares.

v1.802.0 adds ``widget_mode`` (Classic / Framed for pilot users / Framed for everyone) with
``default: "Classic"``. **A ``default`` on a new field of a Single never reaches the row that
already exists**: ``bench migrate`` adds no ``tabSingles`` row for a newly declared field, and
``load_from_db`` applies no defaults (they fire in ``new_doc()``, on a fresh install and never
again). So on every live site the field reads ``None`` while the JSON says ``Classic``.

Nothing breaks without this patch, on purpose. ``api.triton_frame.frame_config`` treats a missing
or unknown mode as Classic, so the widget people use today keeps running whether or not this has
run, and the controller has no ``validate`` that could refuse the blank. What the patch fixes is
what a person sees: without it the settings form opens with an **empty** *Widget* box, which reads
as "not set up" on the one page where Nikolas turns the pilot on. With it the box says Classic.

The other two new fields are deliberately absent from ``DEFAULTS``. *Framed Site Address* has no
default (blank means "use the Gateway URL"), and *Pilot Users* is a table.

Fills the field **only when it has no row in ``tabSingles``**, never over a stored value. A row
that says ``Framed for pilot users`` is somebody's decision; a missing row is a fact about the
schema migration.

**And it writes nothing at all to a Single that was never saved.** That is the inverse of the trap
above, and it bites harder. A Single with no rows loads from ``new_doc()`` and shows every JSON
default, ``Classic`` included, so there is nothing to fill. Writing the one row would make
``load_from_db`` read ``tabSingles`` from then on, and every *other* field without a row would
load blank: the master switch off, attachments off, a 0 MB upload cap, a 0 second timeout.
Production's row has been saved many times; a staging site or a freshly restored copy may not
have been. ``tabSingles`` is read with ``frappe.db.sql`` because it is not a doctype table
(three columns, no ``creation``), so ``frappe.db.get_value`` on it cannot work. Safe to run twice:
the second pass finds the row and writes nothing.
"""

import frappe
from frappe.model import no_value_fields

SETTINGS_DOCTYPE = "Triton Assistant Settings"

# Restated here so the patch is readable without the JSON, and so a later edit to the JSON's
# default is a visible decision rather than a silent one.
DEFAULTS = {
	"widget_mode": "Classic",
}


def execute() -> None:
	backfill_triton_widget_mode()


def backfill_triton_widget_mode() -> int:
	"""Write each default that has no stored row. Returns how many it wrote."""
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
		# Never saved: every default already applies. One row here would blank all the others.
		return 0
	declared = {
		df.fieldname for df in frappe.get_meta(SETTINGS_DOCTYPE).fields if df.fieldtype not in no_value_fields
	}

	written = 0
	for fieldname, default in DEFAULTS.items():
		if fieldname not in declared or fieldname in stored:
			continue
		# set_single_value writes tabSingles directly and does not run the controller.
		frappe.db.set_single_value(SETTINGS_DOCTYPE, fieldname, default)
		written += 1

	if written:
		frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)

	return written
