# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Receipt / Expense posting handler: turn an Approved Document Intake into a
draft Expense Claim (employee reimbursement). Company-card receipts instead use
the "Create Purchase Invoice" action, which is handled by ``vendor_bill``. Output
is docstatus 0 (draft); the employee/approver submit it through the normal flow."""

import frappe
from frappe.utils import flt, today

from erpnext_enhancements.accounting_intake.actions.base import get_company, register


@register("Create Expense Claim")
def post_expense_claim(doc):
	company = get_company()
	employee = _employee_for(doc)
	if not employee:
		frappe.throw(
			"No Employee is linked to the reviewer — map one, or use 'Create Purchase Invoice' for this receipt."
		)

	expense_type = _default_expense_claim_type()
	default_account = None
	if expense_type:
		default_account = frappe.db.get_value(
			"Expense Claim Account", {"parent": expense_type, "company": company}, "default_account"
		)

	ec = frappe.new_doc("Expense Claim")
	ec.employee = employee
	ec.company = company
	ec.posting_date = doc.document_date or today()
	ec.expense_approver = frappe.db.get_value("Employee", employee, "expense_approver")

	for line in doc.line_items:
		amount = flt(line.amount) or (flt(line.rate) * (flt(line.qty) or 1))
		ec.append(
			"expenses",
			{
				"expense_date": doc.document_date or today(),
				"expense_type": expense_type,
				"description": line.description or "Expense",
				"amount": amount,
				"sanctioned_amount": amount,
				"default_account": default_account,
			},
		)

	if not ec.get("expenses"):
		amount = flt(doc.grand_total)
		ec.append(
			"expenses",
			{
				"expense_date": doc.document_date or today(),
				"expense_type": expense_type,
				"description": doc.party_name_text or "Expense",
				"amount": amount,
				"sanctioned_amount": amount,
				"default_account": default_account,
			},
		)

	ec.flags.ignore_permissions = True
	ec.flags.ignore_mandatory = True
	ec.insert(ignore_permissions=True)
	return "Expense Claim", ec.name


def _employee_for(doc):
	user = doc.reviewed_by or frappe.session.user
	return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name") or frappe.db.get_value(
		"Employee", {"status": "Active"}, "name"
	)


def _default_expense_claim_type():
	return frappe.db.get_value("Expense Claim Type", {}, "name")
