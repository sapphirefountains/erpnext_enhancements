"""Give the **existing** Water Engineering Settings row the house-rule defaults (v1.759.0).

The Fountain Designer rebuild (phase C) adds the Sapphire house rules (foam-jet submergence,
freeboard multiplier, lighting watts per foot, wind setpoints, liner allowance, spray loss,
labor) and the utility rates as fields on Water Engineering Settings, a Single.

**A ``default`` on a new field of a Single never reaches the row that already exists.**
``bench migrate`` adds no ``tabSingles`` row for a newly declared field and ``load_from_db``
applies no defaults, so on every site that saved these settings before (the engineer of
record and contractor block), each new rule would read ``0`` — a freeboard multiplier of 0,
a wind stop at 0 mph. The engine itself falls back to ``engine/rules.py`` when it is handed
nothing, but the Settings page would show zeros and the first save would store them.

Missing, not falsy: this fills a field **only when it has no row in ``tabSingles``**, never
over a stored value, so a rule a designer deliberately set to 0 stays 0. It writes every
declared default that is missing, not only the new ones, because writing one field of a
never-saved Single makes ``load_from_db`` read ``tabSingles`` and blank every field without a
row (``contractor_name`` would lose its default). Same shape as
``backfill_marketing_settings_defaults``. Safe to run twice: the second run writes nothing.
"""

import frappe
from frappe.model import no_value_fields

SETTINGS_DOCTYPE = "Water Engineering Settings"


def execute() -> None:
	backfill_water_house_rules()


def backfill_water_house_rules() -> int:
	"""Write each declared default that has no stored row. Returns how many it wrote."""
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
		if field.fieldtype in no_value_fields:
			continue
		if field.default in (None, ""):
			continue
		if field.fieldname in stored:
			continue
		# set_single_value writes tabSingles directly and runs no controller validate, so a
		# validate added later can never make this patch raise (a raising patch aborts the deploy).
		frappe.db.set_single_value(SETTINGS_DOCTYPE, field.fieldname, field.default)
		written += 1

	if written:
		frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)

	return written
