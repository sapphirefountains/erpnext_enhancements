"""Custom fields recording who submitted a Purchase Order and when (v1.199.2).

The supplier-facing Purchase Order print format shows an approver name and date. There
was nowhere truthful to read those from: ERPNext's Purchase Order has no approver field,
and `modified_by` is whoever touched the document *last*, which after any post-submit
edit is not the approver at all. Printing that would have been confidently wrong — the
worst kind of wrong on a document that goes to a vendor.

So the two submit gates that already establish the fact now record it.
``po_approval.stamp_approval`` runs last in the ``before_submit`` chain, after
``enforce_requester_separation`` and ``enforce_threshold`` have both passed, and writes
``frappe.session.user`` and the timestamp onto the document being submitted.

Created with ``create_custom_fields`` (so ``is_system_generated = 1`` and the fixture
export skips them by design, matching the other procurement fields on this doctype).
Read-only and hidden from the form: they are a record, not an input.

Purchase Orders submitted before this shipped have no stamp. The print format prints an
em dash rather than inventing one.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Purchase Order": [
				{
					"fieldname": "custom_approval_stamp_section",
					"label": "Approval",
					"fieldtype": "Section Break",
					"insert_after": "status",
					"collapsible": 1,
				},
				{
					"fieldname": "custom_approved_by",
					"label": "Approved By",
					"fieldtype": "Link",
					"options": "User",
					"insert_after": "custom_approval_stamp_section",
					"read_only": 1,
					"no_copy": 1,
					"description": (
						"Set automatically when the order is submitted, after the "
						"separation-of-duties and approval-threshold gates pass."
					),
				},
				{
					"fieldname": "custom_approved_on",
					"label": "Approved On",
					"fieldtype": "Datetime",
					"insert_after": "custom_approved_by",
					"read_only": 1,
					"no_copy": 1,
				},
			]
		},
		update=True,
	)
