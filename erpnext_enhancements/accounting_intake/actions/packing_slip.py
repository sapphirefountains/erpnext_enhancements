# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Packing Slip posting handler: turn an Approved Document Intake into a draft
Purchase Receipt against the matched Purchase Order (the receiving side of a
3-way match). ERPNext-only — packing slips have no QuickBooks counterpart. A
matched PO is required; output is docstatus 0 (draft)."""

import frappe

from erpnext_enhancements.accounting_intake.actions.base import register


@register("Create Purchase Receipt")
def post_packing_slip(doc):
	po = doc.selected_match_name if doc.selected_match_doctype == "Purchase Order" else None
	if not (po and frappe.db.exists("Purchase Order", po)):
		frappe.throw("A matched Purchase Order is required to create a Purchase Receipt from a packing slip.")

	from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

	pr = make_purchase_receipt(po)
	pr.remarks = f"Created from Document Intake {doc.name}"
	pr.flags.ignore_permissions = True
	pr.insert(ignore_permissions=True)
	return "Purchase Receipt", pr.name
