"""The `Order Stage` field on Purchase Order (v1.289.0).

Five stages the buyer sets by hand — see `po_order_stage` for why this cannot be three
more options on ERPNext's own `status` field, which is recomputed on every save.

Created with `create_custom_fields` (so `is_system_generated = 1` and the fixture export
skips it), matching `add_po_approval_stamp_fields` and the other procurement fields on
this doctype.

Three of the properties are load-bearing rather than decorative:

* **`allow_on_submit`.** Every transition the buyer cares about — ordered, confirmed,
  collected — happens after the order is submitted. Without this the field would be
  editable only in the one state where it is always `Created`, i.e. useless.
* **`insert_after: tracking_section`** puts it at the top of the Order Status section,
  above the `status` ERPNext computes. Deliberately not `insert_after: status`, which is
  already the anchor for `custom_approval_stamp_section`; two custom fields sharing an
  anchor resolve in an order nobody controls, and losing that race would drop this field
  inside the collapsible Approval section.
* **`in_standard_filter`** is the actual feature for a list of a hundred-odd orders:
  "what am I waiting on" is a filter, not a scroll.

`default` is `Created`, and on a normal doctype that reaches **every existing row** — one
`ALTER`, and MariaDB writes the default in as part of it. All 157 existing orders will read
`Created` the moment this runs, which is wrong for the 124 that are submitted. That is what
`backfill_po_order_stage` is for, and why it cannot key on emptiness.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_enhancements.po_order_stage import DEFAULT_STAGE, FIELD, STAGES


def execute():
	create_custom_fields(
		{
			"Purchase Order": [
				{
					"fieldname": FIELD,
					"label": "Order Stage",
					"fieldtype": "Select",
					# No leading blank option: the default fills every row, so the
					# field always holds one of the five and reports can rely on it.
					"options": "\n".join(STAGES),
					"default": DEFAULT_STAGE,
					"insert_after": "tracking_section",
					"allow_on_submit": 1,
					"no_copy": 1,
					"in_standard_filter": 1,
					"in_list_view": 1,
					"description": (
						"Where this order is with the supplier. Set by hand — submitting "
						"an order here is approval, not the act of placing it. Only "
						"'Received' arrives on its own, when a Purchase Receipt completes "
						"the order."
					),
				}
			]
		},
		update=True,
	)
	frappe.db.commit()
