"""Two more Order Stage options on Purchase Order (v1.328.0).

ER-2026-256846 asked for six states and the field shipped with five, missing two of them:
*"I have an answer but we're waiting for fulfillment"* (`Awaiting Fulfillment`) and *"the
order has only been partially fulfilled"* (`Partially Fulfilled`). This widens the Select
to the seven in `po_order_stage.STAGES`.

**Purely additive, so there is deliberately no data migration here, and that is the whole
point of the choice.** Adding an option leaves every stored value still in the list;
*renaming* one does the opposite — a row holding a value no longer in `options` refuses to
save, and would need every affected row rewritten in this same transaction. That asymmetry
is why `Received` kept its label rather than becoming the "Fully Received" the request
literally asked for. The two directions look equally cheap from the outside and are not.

Nor is a re-backfill needed for the orders the new `Partially Fulfilled` would suit better.
`backfill_stage_for` now returns it for a part-received order, where it used to fall
through to `Awaiting Confirmation` — but production had **zero** part-received orders when
this shipped (158 orders, checked), so the old rule and the new one disagree on no stored
row. If that ever stops being true, the fix is a backfill keyed on `per_received`, not on
emptiness: `custom_order_stage` has a default and is never empty (v1.280.3).

Reads its spec from `po_order_stage.field_definition()`, the same function
`add_po_order_stage_field` uses, so the two cannot drift onto different option lists.
`create_custom_fields(update=True)` rewrites the existing Custom Field in place. Safe
twice.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_enhancements.po_order_stage import field_definition


def execute():
	create_custom_fields({"Purchase Order": [field_definition()]}, update=True)
	frappe.db.commit()
