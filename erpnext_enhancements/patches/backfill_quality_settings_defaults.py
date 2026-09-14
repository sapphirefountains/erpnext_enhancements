"""Give the **existing** Quality Settings row the defaults its fields were declared with.

**A ``default`` on a new field of a Single never reaches the row that already exists.** A Single
stores one row per field in ``tabSingles``; ``bench migrate`` adds no row for a newly declared
field and ``load_from_db`` applies no defaults — they fire in ``new_doc()``, i.e. on a fresh
install and never again. So every dial reads ``None``/``0`` on any site that existed before the
field did, however confidently the JSON says otherwise.

Chat Settings shipped 37 fields this way in v1.271.0 and its settings page became **unsaveable**
in v1.277.3: ``validate`` refused the fifteen zeros, so opening the page and pressing Save
returned fifteen errors about fields nobody had touched.

The shape is the part worth remembering — saving a Single deletes and re-inserts every field
row, so a page people actually use self-heals on the next save. The ones that bite are the
settings for **dormant** features, where the first save is the one you need and the one that
fails. The Quality module ships dormant behind ``quality_enabled = 0``, which puts it squarely
in that trap, so this patch ships in the same release as the DocType rather than after it.

``QualitySettings.validate`` additionally repairs a missing dial in place, so the page cannot
brick even if this has not run. Two defences, because the failure mode is "the switch you need
in order to switch it on".

--------------------------------------------------------------------------------------
Missing, not falsy
--------------------------------------------------------------------------------------

This fills a field **only when it has no row in ``tabSingles``**, never when it has a row
holding a falsy value. That distinction is the whole safety argument: an unticked box and a
deliberate ``0`` are both falsy and are not the same fact. Restoring a default over a stored
zero would silently switch a feature back on after somebody turned it off — and
``quality_enabled``, ``notifications_enabled`` and ``advisory_inspector_qualification`` are
exactly that kind of switch.

Note the table is read with ``frappe.db.sql`` and not ``frappe.db.get_value``. ``tabSingles`` has
exactly three columns — ``doctype``, ``field``, ``value`` — and ``get_value`` defaults to
``order_by="creation"``, so it compiles to a query ending ``ORDER BY creation`` and raises
``Unknown column 'creation'`` on every site, every time. **A patch that raises aborts
``bench migrate``, which on this repo is the deploy**, and v1.395.0 is the release that proved
it: production ended up schema-synced with 3 of 8 patches applied, no fixtures, no Property
Setters and no ``after_migrate`` hooks, while ``__version__`` reported the new release, so every
cheap check said it had installed. ``tests/test_singles_table_access.py`` fails the build on it.

Safe to run twice: the second run finds rows for everything and writes nothing.
"""

import frappe
from frappe.model import no_value_fields

SETTINGS_DOCTYPE = "Quality Settings"


def execute() -> None:
	backfill_quality_settings_defaults()


def backfill_quality_settings_defaults() -> int:
	"""Write each declared default that has no stored row. Returns how many it wrote.

	Guarded on the DocType existing because this is also registered on ``after_migrate``, which
	runs on sites that do not have the Quality tables yet.
	"""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return 0

	stored = {
		row[0]
		for row in frappe.db.sql(
			"select field from tabSingles where doctype = %s", (SETTINGS_DOCTYPE,)
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
		# set_single_value writes tabSingles directly and does NOT run the controller's
		# validate — which is the point: validate is exactly what fails in the situation this
		# repairs, so a patch going through the document API could not fix the document.
		frappe.db.set_single_value(SETTINGS_DOCTYPE, field.fieldname, field.default)
		written += 1

	if written:
		frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)

	frappe.logger().info(f"backfill_quality_settings_defaults: wrote {written} field rows")
	return written
