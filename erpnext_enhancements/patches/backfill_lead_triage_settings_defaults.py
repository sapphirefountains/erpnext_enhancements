"""Give the existing ERPNext Enhancements Settings row the lead-triage defaults (v1.502.0).

A ``default`` on a new field of a Single never reaches the row that already exists: a
Single stores one ``tabSingles`` row per field, ``bench migrate`` adds none for a new
field, and defaults only fire in ``new_doc()``. So without this, the settings page shows
a blank triage role, a blank 60 and a blank 240 on the one site that matters, while the
code quietly uses its own fallbacks (``lead_triage.sla_config``) -- two answers to "what
is the SLA", and the visible one wrong.

Same shape as ``backfill_marketing_settings_defaults``, with one deliberate difference:
it touches **only the fields this release added** (``FIELDS``), not every field with a
default. This Single holds switches for a dozen unrelated features, and "no row" for one
of them may be the state a feature has run in for months -- filling
``require_industry_on_commercial`` (default 1) that way would switch a validation on in a
deploy about something else.

Fills a field only when it has **no row**, never over a stored value: an unticked box and
a deliberate 0 are not the same fact. Safe to run twice.
"""

import frappe

SETTINGS_DOCTYPE = "ERPNext Enhancements Settings"

FIELDS = (
	"lead_triage_role",
	"lead_sla_enabled",
	"lead_sla_response_minutes",
	"lead_sla_escalation_minutes",
	"lead_sla_business_start",
	"lead_sla_business_end",
)


def execute() -> None:
	backfill()


def backfill() -> int:
	"""Write each listed field's declared default where no row exists. Returns rows written."""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return 0

	stored = {
		row[0]
		for row in frappe.db.sql(
			"select field from tabSingles where doctype = %s",
			(SETTINGS_DOCTYPE,),
		)
	}
	meta = frappe.get_meta(SETTINGS_DOCTYPE)

	written = 0
	for fieldname in FIELDS:
		field = meta.get_field(fieldname)
		if not field or field.default in (None, "") or fieldname in stored:
			continue
		# Direct write, not the document API: it must not run validate, which is
		# unrelated to these fields and would make this patch hostage to it.
		frappe.db.set_single_value(SETTINGS_DOCTYPE, fieldname, field.default)
		written += 1

	if written:
		frappe.clear_document_cache(SETTINGS_DOCTYPE, SETTINGS_DOCTYPE)
	return written
