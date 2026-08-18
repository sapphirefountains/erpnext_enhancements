"""The `Order Stage` field on Purchase Order (v1.289.0).

Five stages the buyer sets by hand (v1.328.0 added two more; see
`update_po_order_stage_options`) — see `po_order_stage` for why this cannot be three
more options on ERPNext's own `status` field, which is recomputed on every save.

Created with `create_custom_fields` (so `is_system_generated = 1` and the fixture export
skips it), matching `add_po_approval_stamp_fields` and the other procurement fields on
this doctype.

The field spec itself lives in `po_order_stage.field_definition()` — shared with the
patch that widened it, so the two cannot drift onto different option lists. The
rationale for each property is documented there.

`default` is `Created`, and on a normal doctype that reaches **every existing row** — one
`ALTER`, and MariaDB writes the default in as part of it. All 157 existing orders will read
`Created` the moment this runs, which is wrong for the 124 that are submitted. That is what
`backfill_po_order_stage` is for, and why it cannot key on emptiness.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_enhancements.po_order_stage import field_definition


def execute():
	create_custom_fields({"Purchase Order": [field_definition()]}, update=True)
	frappe.db.commit()
