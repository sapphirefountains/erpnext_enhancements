"""Order Stage moves to the top of the Purchase Order form (v1.335.0).

ER-2026-276347: *"it's also way too hidden to be of use to me"*. The field shipped at
`insert_after: tracking_section` — the top of the Order Status section, which is where it
belongs by subject and the wrong place for it by use. That section lives inside the More
Info tab behind a collapsed heading, so reading the stage meant knowing it was there and
setting it meant four clicks per order, on the one field a buyer changes most.

It now anchors to `supplier_name`, directly under the supplier at the top of the first tab.

**`bench migrate` alone does not move it, and that is the trap this patch exists for.** The
spec lives in `po_order_stage.field_definition()`, and nothing re-reads that function
without a patch entry — the field is `is_system_generated = 1`, so it is not in
`fixtures/custom_field.json` either, and no fixture sync touches it. A change to
`field_definition()` with no patch beside it edits a file and ships nothing, which looks
exactly like a change that worked.

`create_custom_fields(update=True)` rewrites the existing Custom Field in place from that
one spec, so this cannot drift onto a different option list than the create and widen
patches. Safe twice.

One thing it deliberately leaves alone: `Custom Field.idx` still reads 118, because frappe
only recomputes idx on insert. Nothing depends on it — `Meta.sort_fields` positions custom
fields from `insert_after` and never looks at idx — and writing a corrected number would be
inventing a fact rather than recording one.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_enhancements.po_order_stage import field_definition


def execute():
	create_custom_fields({"Purchase Order": [field_definition()]}, update=True)
	frappe.clear_cache(doctype="Purchase Order")
	frappe.db.commit()
