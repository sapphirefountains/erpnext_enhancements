"""Give the **existing** Triton Assistant Settings row the defaults its new attachment fields carry.

A ``default`` on a new field of a Single never reaches the row that already exists.
``load_from_db`` calls ``get_singles_dict``, which is a bare query over ``tabSingles`` --
stored rows only, no meta, no defaults -- and it falls back to ``new_doc`` (the one path that
*does* apply defaults) only ``if not single_doc``, i.e. only when the Single has no stored
rows at all. This row has plenty; the widget has been live since v1.x. So each new field gets
no row and no default, and ``_fix_numeric_types`` converts an ``Int`` only
``elif self.get(df.fieldname) is not None`` -- leaving it at ``None``, which the first
``cint()`` in the read path turns into ``0``.

Unlike Chat Settings (v1.277.3) this cannot brick the settings page: the controller is
``pass`` with no ``validate``, so nothing rejects the zeros. The damage is quieter and harder
to find. A 0 MB cap would reject every upload with no error, on production only, while the
JSON says 25 -- the same *passes-when-broken* shape as the PAD SPACE checks, where nobody
goes looking because nothing complains.

**This is the second of two defences, not the only one.** ``triton_attachments`` reads both
Int dials as "0 means unset" (``configured if configured > 0 else DEFAULT``), mirroring
``fountain_move/intake.py``'s photo cap and ``get_settings``'s own
``int(behavior.request_timeout or 120)``. The patch exists so the numbers are *visible and
editable in the Desk form* rather than being constants a reader has to find in Python -- and
because ``enable_attachments`` genuinely needs it: that one is a ``Check`` read as
``bool(...)``, so until this runs the feature is switched off on every live site. It ships
off and comes on inside the same migrate, which is the right order.

The two picker credentials are deliberately absent from ``DEFAULTS``: they carry no
``default``, and blank is the correct value for an unconfigured API key.

Fills a field **only when it has no row in ``tabSingles``**, never over a stored falsy value:
a deliberately unticked box and a missing row are not the same fact. Safe to run twice -- the
second pass finds rows for everything and writes nothing.
"""

import frappe
from frappe.model import no_value_fields

SETTINGS_DOCTYPE = "Triton Assistant Settings"

# Restated here so the patch is readable without the JSON, and so a later edit to the JSON's
# defaults is a visible decision rather than a silent one.
DEFAULTS = {
	"enable_attachments": "1",
	"triton_max_upload_mb": "25",
	"triton_attachment_retention_days": "30",
}


def execute() -> None:
	backfill_triton_assistant_settings_defaults()


def backfill_triton_assistant_settings_defaults() -> int:
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
