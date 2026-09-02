"""Give the **existing** Plaid Banking Settings row the defaults its fields were declared with.

Same trap as ``backfill_marketing_settings_defaults``: a ``default`` on a field of a
Single reaches a row only through ``new_doc()``, i.e. on a fresh install. This Single
is not new -- it is the renamed "Plaid Settings" (see ``rename_plaid_settings_doctype``)
and the rows it inherited from that name were the NATIVE ones, which the rename patch
moved back out. So on the day this ships the row holds none of our fields at all, and
the widget it switches on is a dormant feature: the first save of its settings page is
the one that has to work.

Fills a field **only when it has no row in ``tabSingles``**, never over a stored falsy
value -- an unchecked "Show the widget" and a deliberate 0 are not the same fact.
Safe to run twice: the second run finds rows for everything and writes nothing.
"""

import frappe
from frappe.model import no_value_fields

SETTINGS_DOCTYPE = "Plaid Banking Settings"

# Declared defaults, restated here so the patch is readable without the JSON and so
# a later edit to the JSON's defaults is a visible decision rather than a silent one.
DEFAULTS = {
	"plaid_enabled": "0",
	"refresh_poll_minutes": "240",
	"plaid_status": "Not Connected",
	"plaid_auth_blocked": "0",
}


def execute() -> None:
	backfill_plaid_banking_settings_defaults()


def backfill_plaid_banking_settings_defaults() -> int:
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
		# set_single_value writes tabSingles directly and does NOT run the controller's
		# validate -- the point, since validate is what fails on the page this repairs.
		frappe.db.set_single_value(SETTINGS_DOCTYPE, fieldname, default)
		written += 1

	if written:
		frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)

	return written
