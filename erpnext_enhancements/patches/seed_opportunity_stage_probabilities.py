"""Seed Sales Activity Settings' stage probability table with Nik's numbers (form kit P5.5).

Qualification 10, Needs Analysis 25, Proposal/Price Quote 50, Negotiation/Review 75
(`opportunity_probability.DEFAULTS`, decided 2026-10-13).

**Only into an empty table.** Rows anyone has already entered are a decision, and are left.

**Child rows are inserted on their own, the Single is never saved.** Sales Activity Settings is a
Single: saving it re-writes every field row in `tabSingles`, and writing *part* of a never-saved
Single makes its other fields load blank (CLAUDE.md). A child table's rows live in their own
table, keyed by `parenttype`/`parent`, so inserting them touches nothing else; the cached
document is cleared afterwards so the next read sees them.

Cannot raise; safe twice (a second run finds rows and does nothing).
"""

import frappe

from erpnext_enhancements.crm_enhancements.opportunity_probability import (
	DEFAULTS,
	ROW_DOCTYPE,
	SETTINGS,
	TABLE,
)


def execute():
	try:
		seed()
	except Exception:
		try:
			frappe.log_error(
				title="Opportunity stage probability seed failed", message=frappe.get_traceback()
			)
		except Exception:
			pass


def seed():
	"""Insert the default rows when the table has none; returns how many were inserted."""
	if not frappe.db.table_exists(ROW_DOCTYPE):
		return 0
	if frappe.db.count(ROW_DOCTYPE, {"parenttype": SETTINGS, "parentfield": TABLE}):
		return 0
	for idx, (stage, probability) in enumerate(DEFAULTS.items(), 1):
		frappe.get_doc(
			{
				"doctype": ROW_DOCTYPE,
				"parenttype": SETTINGS,
				"parent": SETTINGS,
				"parentfield": TABLE,
				"idx": idx,
				"stage": stage,
				"probability": probability,
			}
		).db_insert()
	frappe.db.commit()
	frappe.clear_document_cache(SETTINGS, SETTINGS)
	return len(DEFAULTS)
