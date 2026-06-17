# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Vendor Bill posting handler: turn an Approved Document Intake into a draft
Purchase Invoice. When a Purchase Order is matched and carries stock items, a
draft Purchase Receipt is created first (3-way match); otherwise the bill is
invoiced directly against the PO, or built standalone when there is no PO. Also
serves company-card receipts whose proposed action is "Create Purchase Invoice".
All output is docstatus 0 (draft)."""

import frappe
from frappe.utils import flt, today

from erpnext_enhancements.accounting_intake.actions.base import get_company, register


@register("Create Purchase Invoice")
def post_vendor_bill(doc):
	company = get_company()
	po = doc.selected_match_name if doc.selected_match_doctype == "Purchase Order" else None

	if po and frappe.db.exists("Purchase Order", po):
		if _po_has_stock_items(po):
			_make_purchase_receipt(po)
		pi_name = _make_pi_from_po(po, doc)
	else:
		pi_name = _standalone_pi(doc, company)
	return "Purchase Invoice", pi_name


def _po_has_stock_items(po):
	for row in frappe.get_all("Purchase Order Item", filters={"parent": po}, fields=["item_code"]):
		if row.item_code and frappe.db.get_value("Item", row.item_code, "is_stock_item"):
			return True
	return False


def _make_purchase_receipt(po):
	from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

	pr = make_purchase_receipt(po)
	pr.flags.ignore_permissions = True
	pr.insert(ignore_permissions=True)
	return pr.name


def _make_pi_from_po(po, doc):
	from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice

	pi = make_purchase_invoice(po)
	if doc.document_number:
		pi.bill_no = doc.document_number
	if doc.document_date:
		pi.bill_date = doc.document_date
		pi.set_posting_time = 1
		pi.posting_date = doc.document_date
	pi.remarks = f"Created from Document Intake {doc.name}"
	pi.flags.ignore_permissions = True
	pi.insert(ignore_permissions=True)
	return pi.name


def _standalone_pi(doc, company):
	pi = frappe.new_doc("Purchase Invoice")
	pi.company = company
	pi.supplier = doc.party
	pi.set_posting_time = 1
	pi.posting_date = doc.document_date or today()
	if doc.document_number:
		pi.bill_no = doc.document_number
	if doc.document_date:
		pi.bill_date = doc.document_date
	pi.credit_to = frappe.db.get_value("Company", company, "default_payable_account") or _any_payable_account(company)
	expense_account = frappe.db.get_value("Company", company, "default_expense_account") or _any_expense_account(company)

	for line in doc.line_items:
		row = {
			"qty": flt(line.qty) or 1,
			"rate": flt(line.rate),
			"description": line.description or line.proposed_item_name or "Item",
			"expense_account": expense_account,
		}
		if line.matched_item:
			row["item_code"] = line.matched_item
		else:
			row["item_name"] = (line.proposed_item_name or line.description or "Item")[:140]
			row["uom"] = "Nos"
		pi.append("items", row)

	if not pi.get("items"):
		pi.append(
			"items",
			{
				"item_name": (doc.party_name_text or "Document Intake")[:140],
				"description": doc.party_name_text or "Document Intake",
				"qty": 1,
				"rate": flt(doc.grand_total),
				"expense_account": expense_account,
				"uom": "Nos",
			},
		)

	pi.remarks = f"Created from Document Intake {doc.name}"
	pi.flags.ignore_permissions = True
	pi.insert(ignore_permissions=True)
	return pi.name


def _any_expense_account(company):
	return frappe.db.get_value("Account", {"company": company, "root_type": "Expense", "is_group": 0}, "name")


def _any_payable_account(company):
	return frappe.db.get_value("Account", {"company": company, "account_type": "Payable", "is_group": 0}, "name")
