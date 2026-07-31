"""Point the Opportunity Kanban's name card field at primary_contact (v1.199.0).

The board named "Opportunity" showed `contact_person`, not the Opportunity's Primary
Contact. That was hard to spot because **both** Link-to-Contact fields on Opportunity
were labelled "Full Name" — so the field picker offered the same label twice and the
board was configured with whichever one came first. v1.199.0 relabels `contact_person`
to "Contact Person" (Property Setter fixture) so the two stop being interchangeable.

Card fields before → after::

    ["custom_contact_mobile_number", "contact_person",  "customer_name", "opportunity_amount"]
    ["custom_contact_mobile_number", "primary_contact", "customer_name", "opportunity_amount"]

``opportunity_amount`` **must stay in the list.** `opportunity_kanban_totals.js` sums it
per column, and it reaches the browser via the board's own field list — it is not in
`crm_enhancements/opportunity_list.js`'s `add_fields`. Dropping it would silently empty
the column totals rather than error.

This is a patch rather than a fixture because **Kanban Board is not in the `fixtures`
list in hooks.py** — the board exists only as a live database record, so a UI edit would
not survive a fresh site build and would never reach a second site at all.

Surgical and idempotent: it swaps that one entry in place, leaves the other three and
the board's `field_name` / `filters` / `private` alone, and does nothing if the board is
absent, already correct, or has been re-arranged by hand into something this patch does
not recognise. A board somebody has since customised is not this patch's to overwrite —
it logs and backs off, following ``update_production_procurement_step``.

Depends on :mod:`backfill_opportunity_primary_contact` having run first: without it the
swap blanks ~142 cards that show a name today. `patches.txt` orders them accordingly.
"""

import json

import frappe

BOARD = "Opportunity"
OLD_FIELD = "contact_person"
NEW_FIELD = "primary_contact"


def execute():
	if not frappe.db.exists("DocType", "Kanban Board"):
		return
	if not frappe.db.exists("Kanban Board", BOARD):
		return

	raw = frappe.db.get_value("Kanban Board", BOARD, "fields")
	try:
		fields = json.loads(raw) if raw else []
	except (TypeError, ValueError):
		frappe.logger().info(
			f"set_opportunity_kanban_primary_contact: board {BOARD} has unparseable fields, leaving it alone"
		)
		return

	if not isinstance(fields, list):
		return

	if NEW_FIELD in fields:
		return  # already done

	if OLD_FIELD not in fields:
		frappe.logger().info(
			f"set_opportunity_kanban_primary_contact: board {BOARD} no longer shows {OLD_FIELD}; "
			"leaving the card fields as configured"
		)
		return

	# Swap in place so the card keeps its column order.
	updated = [NEW_FIELD if f == OLD_FIELD else f for f in fields]
	frappe.db.set_value("Kanban Board", BOARD, "fields", json.dumps(updated))
	frappe.logger().info(
		f"set_opportunity_kanban_primary_contact: {BOARD} card fields -> {updated}"
	)
