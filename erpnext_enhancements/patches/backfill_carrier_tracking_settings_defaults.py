"""Give Carrier Tracking Settings a stored row for every field that declares a default (v1.819.0).

Carrier Tracking Settings is a new Single, so on the migrate that installs it ``tabSingles`` has no
rows for it at all. Nothing is wrong with that today: ``frappe.get_cached_doc`` builds a never-saved
Single from its JSON defaults, every default here is "off" or "Sandbox", and ``carrier_tracking.config``
reads a missing value as off and a missing environment as the sandbox.

This patch exists for the two traps CLAUDE.md describes, both of which a Single meets later:

* **A default on a field added later never reaches the existing row.** This runs on
  ``after_migrate`` as well, so the next field that declares a default gets its row on the
  migrate that adds it, without anyone remembering to write a patch.
* **The first write of one field to a never-saved Single makes every other field load blank.**
  Nothing in the app writes to this Single today, but once every declared default has a row, a
  future write cannot blank the switches.

Missing, not falsy: a field is written **only when it has no row**, never over a stored value. An
unticked switch and one nobody has saved are both ``0``; only the second may be filled. Safe to run
any number of times; the second run writes nothing. Same shape as
``backfill_marketing_settings_defaults``.
"""

import frappe
from frappe.model import no_value_fields

SETTINGS_DOCTYPE = "Carrier Tracking Settings"


def execute() -> None:
	backfill_carrier_tracking_settings_defaults()


def backfill_carrier_tracking_settings_defaults() -> int:
	"""Write each declared default that has no stored row. Returns how many it wrote. Never raises
	into a migrate: it is also an ``after_migrate`` hook."""
	try:
		if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
			return 0

		stored = {
			row[0]
			for row in frappe.db.sql(
				"select field from tabSingles where doctype = %s",
				(SETTINGS_DOCTYPE,),
			)
		}

		written = 0
		for field in frappe.get_meta(SETTINGS_DOCTYPE).fields:
			if field.fieldtype in no_value_fields or field.fieldtype == "Password":
				continue
			if field.default in (None, ""):
				continue
			if field.fieldname in stored:
				continue
			frappe.db.set_single_value(SETTINGS_DOCTYPE, field.fieldname, field.default)
			written += 1

		if written:
			frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)
		return written
	except Exception:
		frappe.log_error(title="Carrier Tracking Settings defaults backfill failed")
		return 0
