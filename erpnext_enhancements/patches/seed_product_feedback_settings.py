"""Create the ``Product Feedback Settings`` row, because a new Single does not have one.

--------------------------------------------------------------------------------------
The Frappe fact this exists for
--------------------------------------------------------------------------------------

A Single stores one row per field in ``tabSingles``, and **a brand-new Single doctype has no
rows at all** until something saves it. ``bench migrate`` creates the DocType and stops
there; ``Document.load_from_db`` for a Single loads exactly what is stored and applies no
defaults; defaults are applied by ``new_doc()``, which runs on a fresh install and never
again. So on the day this feature ships, every
``frappe.db.get_single_value("Product Feedback Settings", …)`` answers ``None``.

``CLAUDE.md`` records the version that cost us: 37 fields added to Chat Settings all read
``None`` on production and its settings page became unsaveable.

Two defences, and this patch is only the second of them:

1. **The code does not depend on this patch having run.**
   ``product_feedback_settings.get_settings()`` applies every fallback itself, and the kill
   switch is named ``paused`` rather than ``enabled`` precisely so the no-row state is the
   *running* state. A feature whose correctness needs a patch to have succeeded is a feature
   that ships broken on any site where it did not.
2. **This patch writes the row anyway**, so the two Project ids are visible and editable in
   the desk instead of being invisible constants a reader has to find in Python.

--------------------------------------------------------------------------------------
Missing, not falsy
--------------------------------------------------------------------------------------

Fields are filled **only when they have no row in ``tabSingles``**. An unchecked checkbox and
a deliberate ``0`` are both falsy and are not the same fact; a patch that restored defaults
over stored zeros would silently re-enable something somebody switched off. Same rule as
``backfill_chat_settings_defaults``.

Note this is the *Single* half of the "new field, existing rows" problem. The opposite case —
a new field on a **normal** doctype, where MariaDB writes the default into every existing row
as part of the ``ALTER`` — needs the opposite predicate, and a backfill written for one
silently matches nothing on the other. See ``CLAUDE.md``.

Safe to run twice: the second run finds rows for everything and writes nothing.
"""

import frappe

SETTINGS_DOCTYPE = "Product Feedback Settings"

#: Verified against production on 2026-08-17: PRJ-00580 is "ERPNext Enhancements",
#: PRJ-00755 is "Triton Enhancements". Both `status = Active`.
SEED = {
	"paused": 0,
	"erpnext_project": "PRJ-00580",
	"triton_project": "PRJ-00755",
	"max_proposed_tasks": 12,
	"duplicate_scan_limit": 400,
	"breakdown_timeout": 180,
}


def execute() -> None:
	seed_product_feedback_settings()


def seed_product_feedback_settings() -> int:
	"""Write each seed value that has no stored row. Returns how many it wrote."""
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
	for field, value in SEED.items():
		if field in stored:
			continue
		# A Project that does not exist on this site would make the Link field unsaveable
		# from the desk afterwards, which is a worse failure than an empty field the code
		# already falls back for.
		if field.endswith("_project") and not frappe.db.exists("Project", value):
			continue
		frappe.db.set_single_value(SETTINGS_DOCTYPE, field, value)
		written += 1

	if written:
		frappe.db.commit()
	return written
