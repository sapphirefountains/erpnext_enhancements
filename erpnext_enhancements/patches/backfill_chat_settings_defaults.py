"""Give the **existing** Chat Settings row the defaults its new fields were declared with.

--------------------------------------------------------------------------------------
The Frappe fact this exists for, because it is not obvious and it cost a production bug
--------------------------------------------------------------------------------------

**A ``default`` on a new field of a Single doctype never reaches the row that already
exists.** A Single stores one row per field in ``tabSingles``; ``bench migrate`` adds no row
for a newly declared field, and ``Document.load_from_db`` for a Single loads exactly what is
stored and applies no defaults. Defaults are applied by ``new_doc()`` — which runs on a
*fresh install* and never again.

So v1.271.0 added 37 fields to Chat Settings, every one of them carrying a sensible
``default`` in the JSON, and on the production row all of them read ``None``. For the
fifteen numeric dials that meant **0**, and ``chat_settings_rules.validate_retrieval``
refuses a zero on every one of them — deliberately, because a zero silently disables the
behaviour it sizes.

The result was worse than a wrong number: **Chat Settings could not be saved at all.** Open
it, change anything, press Save, and fifteen validation errors come back about fields you
never touched and cannot see a problem with. The feature was dormant, so nothing was broken
at runtime; the *settings page* was broken, which is the one page you have to use to switch
the feature on.

--------------------------------------------------------------------------------------
Missing, not falsy
--------------------------------------------------------------------------------------

This fills a field **only when it has no row in ``tabSingles``**, never when it has a row
holding a falsy value. The distinction is the whole safety argument: an unchecked checkbox
and a deliberate ``0`` are both falsy and are not the same fact, and a patch that "helpfully"
restored defaults over stored zeros would silently re-enable things somebody switched off.
Whether that could actually happen here is beside the point — the version that cannot do it
is the version that does not need the argument re-checked every time the field list grows.

Safe to run twice: the second run finds rows for everything and writes nothing.
"""

import frappe
from frappe.model import no_value_fields

SETTINGS_DOCTYPE = "Chat Settings"


def execute() -> None:
	backfill_chat_settings_defaults()


def backfill_chat_settings_defaults() -> int:
	"""Write each declared default that has no stored row. Returns how many it wrote.

	Guarded on the DocType existing because this is registered on ``after_migrate``, which
	also runs on sites that do not have the chat module's tables yet.
	"""
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
		# set_single_value writes tabSingles directly and does NOT run the controller's
		# validate — which is the point: validate is exactly what is currently failing, so a
		# patch that went through the document API could not repair the document.
		frappe.db.set_single_value(SETTINGS_DOCTYPE, field.fieldname, field.default)
		written += 1

	if written:
		frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)

	return written
