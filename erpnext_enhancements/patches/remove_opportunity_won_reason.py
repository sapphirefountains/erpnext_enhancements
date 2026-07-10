"""One-time migration patch (post_model_sync; listed in patches.txt).

Removes the ``custom_won_reason`` field from Opportunity. As of v1.149.0 winning
an Opportunity no longer captures or requires a reason — the required-on-Closed
Won validation was dropped and the field was removed from
``setup.custom_fields.create_opportunity_winloss_fields``. This deletes the
leftover Custom Field on existing sites via ``delete_doc`` so its ``on_trash``
also drops the DB column. Idempotent — matches nothing once cleaned.
"""
import frappe


def execute():
	name = frappe.db.get_value(
		"Custom Field", {"dt": "Opportunity", "fieldname": "custom_won_reason"}, "name"
	)
	if not name:
		return
	frappe.delete_doc("Custom Field", name, ignore_missing=True, force=True)
	frappe.clear_cache(doctype="Opportunity")
