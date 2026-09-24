"""Point the Monday Item naming digest at the Purchasing Agent, once (v1.532.0).

Nik, 2026-09-24 (TASK-2026-02238): the weekly list of new Items that fail the naming rules
goes to the Purchasing Agent, Parker Bailey. ``Inventory Scanner Settings.naming_digest_recipients``
is a new field on a Single that already exists, and it deliberately has no ``default``: a
default would name a person in the DocType JSON and reach every fresh install. So the address
is written here, as data.

Same shape as ``backfill_marketing_settings_defaults`` and
``backfill_stock_scan_settings_defaults``, and for the same reason: a field is filled **only
when ``tabSingles`` has no row for it**, never over a stored value. Once somebody has edited
the list, or emptied it to stop the email, that is a decision; a later run of this patch must
not undo it. The read is a plain ``select field from tabSingles``, never ``db.get_value("Singles")``,
which cannot succeed (``tabSingles`` has no ``creation`` column to order by; CLAUDE.md).

A Single that has **never been saved** is left alone. It has no rows at all, and Frappe then
builds it from ``new_doc()`` with every declared default; writing one row here would end that,
and every Check that defaults to 1 (the count page's camera button among them) would start
reading 0. Production's was saved on 2026-06-13, so this writes there. A fresh site gets no
recipient and therefore no email until somebody sets one.

Safe to run twice: the second run finds the row and writes nothing. Cannot raise.
"""

import frappe

SETTINGS_DOCTYPE = "Inventory Scanner Settings"
FIELD = "naming_digest_recipients"
RECIPIENT = "parker.bailey@sapphirefountains.com"


def execute() -> None:
	try:
		seed()
	except Exception:
		# A patch that raises aborts `bench migrate`, which is the deploy. Losing a recipient
		# seed costs one Monday email; losing the migrate costs the release.
		frappe.log_error(frappe.get_traceback(), "seed_naming_digest_recipient failed")


def seed() -> bool:
	"""Write the recipient where the field has no row. Returns True when it wrote."""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return False
	if not frappe.get_meta(SETTINGS_DOCTYPE).get_field(FIELD):
		return False

	stored = {
		row[0]
		for row in frappe.db.sql(
			"select field from tabSingles where doctype = %s",
			(SETTINGS_DOCTYPE,),
		)
	}
	if not stored or FIELD in stored:
		return False

	# Direct write, not the document API: a patch must not be hostage to validate.
	frappe.db.set_single_value(SETTINGS_DOCTYPE, FIELD, RECIPIENT)
	frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)
	return True
