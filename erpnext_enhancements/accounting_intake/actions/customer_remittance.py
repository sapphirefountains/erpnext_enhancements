# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Customer Remittance posting handler: turn an Approved Document Intake into a
draft Payment Entry (Receive) for the customer, allocated against the reviewer-
selected Sales Invoice when there is one (otherwise an on-account payment the
accountant allocates). Output is docstatus 0 (draft). Field recipe mirrors
``quickbooks_online/core/mapping.py::_map_payment_entry``."""

import frappe
from frappe.utils import flt, today

from erpnext_enhancements.accounting_intake.actions.base import get_company, register


@register("Create Payment Entry")
def post_remittance(doc):
	if not (doc.proposed_party_type == "Customer" and doc.party):
		frappe.throw("Set the Customer before posting a customer remittance.")
	company = get_company()
	customer = doc.party

	bank = (
		frappe.db.get_value("Company", company, "default_bank_account")
		or frappe.db.get_value("Company", company, "default_cash_account")
		or _any_account(company, "Bank")
		or _any_account(company, "Cash")
	)
	receivable = frappe.db.get_value("Company", company, "default_receivable_account") or _any_account(company, "Receivable")

	amount = flt(doc.grand_total)
	pe = frappe.new_doc("Payment Entry")
	pe.payment_type = "Receive"
	pe.company = company
	pe.posting_date = doc.document_date or today()
	pe.party_type = "Customer"
	pe.party = customer
	pe.paid_from = receivable
	pe.paid_to = bank
	pe.paid_amount = amount
	pe.received_amount = amount
	pe.reference_no = doc.document_number or doc.name
	pe.reference_date = doc.document_date or today()

	si = _selected_sales_invoice(doc)
	if si:
		outstanding = flt(frappe.db.get_value("Sales Invoice", si, "outstanding_amount"))
		pe.append(
			"references",
			{
				"reference_doctype": "Sales Invoice",
				"reference_name": si,
				"total_amount": flt(frappe.db.get_value("Sales Invoice", si, "grand_total")),
				"outstanding_amount": outstanding,
				"allocated_amount": min(outstanding, amount) if amount else outstanding,
			},
		)

	pe.flags.ignore_permissions = True
	pe.flags.ignore_mandatory = True
	pe.insert(ignore_permissions=True)
	return "Payment Entry", pe.name


def _selected_sales_invoice(doc):
	if (
		doc.selected_match_doctype == "Sales Invoice"
		and doc.selected_match_name
		and frappe.db.exists("Sales Invoice", doc.selected_match_name)
	):
		return doc.selected_match_name
	return None


def _any_account(company, account_type):
	return frappe.db.get_value("Account", {"company": company, "account_type": account_type, "is_group": 0}, "name")
