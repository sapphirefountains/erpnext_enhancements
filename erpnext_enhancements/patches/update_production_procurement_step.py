"""Show the optional quoting step in the live "Production — Job Production" map (WI-066).

The seeded process map drew procurement as ``Material Request → PO → Receipt``,
with no place for a supplier quote — which is what the purchasing SOP said too,
until WI-066 documented the optional Request for Quotation path. Fixing the
literal in ``seed_process_maps_finance_production`` only helps fresh sites: that
seeder is insert-only (it skips any Process Document whose title already exists),
and this document has existed on prod since it was first seeded. Hence a targeted
one-shot rewrite.

Deliberately surgical: it swaps one known substring and does nothing at all if the
live copy has drifted from what we expect. A process map is a document a human may
have edited, and silently overwriting someone's edits to fix one arrow is a worse
outcome than leaving the arrow stale — the divergence is logged instead.
"""

import frappe

TITLE = "Production — Job Production"
OLD = '    C --> D["Procurement: Material Request → PO → Receipt"]'
NEW = '    C --> D["Procurement: Material Request → RFQ (optional) → PO → Receipt"]'


def execute():
	if not frappe.db.exists("DocType", "Process Document"):
		return
	if not frappe.db.exists("Process Document", TITLE):
		return

	code = frappe.db.get_value("Process Document", TITLE, "mermaid_code") or ""
	if NEW in code:
		return
	if OLD not in code:
		frappe.log_error(
			title="WI-066: process map not updated",
			message=(
				f"{TITLE!r} no longer contains the expected procurement line, so it was "
				f"left untouched rather than overwritten. Update it by hand if you want "
				f"the optional RFQ step shown.\n\nExpected to find:\n{OLD}"
			),
		)
		return

	frappe.db.set_value("Process Document", TITLE, "mermaid_code", code.replace(OLD, NEW))
