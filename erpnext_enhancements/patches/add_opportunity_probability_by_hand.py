"""The `Probability Set by Hand` check on Opportunity (form kit P5.5).

Created with `create_custom_fields` from `opportunity_probability.field_definition()`, like the
procurement fields made the same way (`add_po_order_stage_field`), because the backfill patch
that follows writes it on this same migrate and patches run before fixtures sync. The
Opportunity `field_order` Property Setter places it after `probability`.

`default` is `0`, so the `ALTER` writes 0 into every existing Opportunity: no deal is marked as
set by hand until `backfill_opportunity_probability` marks the ones that are.

Cannot raise: a patch that raises aborts `bench migrate`, which on this repo is the deploy.
"""

import frappe


def execute():
	try:
		from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

		from erpnext_enhancements.crm_enhancements.opportunity_probability import field_definition

		create_custom_fields({"Opportunity": [field_definition()]}, update=True)
		frappe.db.commit()
	except Exception:
		try:
			frappe.log_error(
				title="Opportunity probability field patch failed", message=frappe.get_traceback()
			)
		except Exception:
			pass
