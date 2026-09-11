# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Receipt / Expense posting: an out-of-pocket receipt becomes a draft bill.

**Two handlers, and on this site only the second one can run.**

`Create Expense Claim` was the original and the only proposal extraction ever
made for a `Receipt / Expense`. It calls `frappe.new_doc("Expense Claim")`, and
`Expense Claim` is an **hrms** doctype — hrms is not installed here and cannot
simply be installed (it collides with this app's `HR` module label and six
`Training *` doctype names). So the call raised a bare `DoesNotExistError`, the
dispatcher's broad `except` caught it, and the reviewer got a generic *Failed*
with a truncated traceback — plus a burned retry against `retry_limit`, every
time. It had never actually fired: `tabDocument Intake` was empty on prod when
this was found.

`Create Reimbursement Bill` is what this company actually does. QuickBooks models
employee reimbursement as a **vendor bill**, and prod already carries seven
Suppliers for it. So the receipt becomes a draft Purchase Invoice against that
Supplier, through the same builder `vendor_bill` uses, and lands in the same
place as every other payable.

**The Supplier is resolved from an explicit link, never from its name**, and that
is the decision worth reading twice. The seven on prod are:

    Jesse Griffin Reimbursement            Danny Rosser Reimbursement
    Employee Clegg Mabey Reimbursement     Nathan Cox Reimbursement
    Lisa Symanski Reimbursement            Lian Silva Reimbursement
    Logan Penrod Employee Reimbursement

Three different naming shapes; `Danny Rosser` against an Employee called **Daniel
Rosser**; `Lian Silva` against an Employee called **Lian Jentz Da Silva**; and
nine of the sixteen staff have none at all. A `LIKE` across 1,180 Suppliers would
miss two of the seven outright and — far worse — could match a real vendor, which
posts somebody's lunch receipt as a bill against a company you actually owe money
to. Nobody would notice until it was paid.

So `Employee.custom_reimbursement_supplier` holds the answer, a patch seeds only
the unambiguous ones, and the rest are a human's decision. When it is not set,
this refuses with a message naming the two ways forward — the shape
`travel_management/api.py::_require_hrms` already established for exactly this
situation.

All output is docstatus 0 (draft); the accountant submits through the normal flow.
"""

import frappe
from frappe import _
from frappe.utils import flt, today

from erpnext_enhancements.accounting_intake.actions.base import get_company, register

EXPENSE_CLAIM = "Create Expense Claim"
REIMBURSEMENT_BILL = "Create Reimbursement Bill"


def expense_claims_available():
	"""Whether the hrms Expense Claim path can run on this site at all.

	Read by ``extraction`` so the unavailable action is never *proposed*, which is
	the difference between telling somebody up front and failing after they press
	Approve. Mirrors ``travel_management.expense_claims_available``.
	"""
	return bool(frappe.db.exists("DocType", "Expense Claim"))


def _require_expense_claims():
	"""Refuse with the real cause rather than a raw ``DoesNotExistError``.

	Same shape and the same reason as ``travel_management/api.py::_require_hrms``:
	without it the dispatcher reports a generic *Failed* with a truncated
	traceback, and burns a retry attempt against ``retry_limit`` on every pass.
	"""
	if not expense_claims_available():
		frappe.throw(
			_(
				"Expense Claims need the HR module (Frappe HR / “hrms”), which is not installed "
				"on this site. Use <b>{0}</b> instead — it bills the receipt to the employee's "
				"reimbursement Supplier, which is how QuickBooks already records it here."
			).format(REIMBURSEMENT_BILL),
			title=_("HR module not installed"),
		)


# --------------------------------------------------------------- reimbursement


@register(REIMBURSEMENT_BILL)
def post_reimbursement_bill(doc):
	"""An employee's out-of-pocket receipt, as a draft Purchase Invoice.

	The **merchant** is on the intake as ``party``/``party_name_text``; the
	**Supplier the bill is raised against** is the employee's reimbursement
	vendor, which is a different thing. Getting those two confused is how a
	receipt from a hardware store becomes a bill payable to that store rather than
	to the person who paid for it out of their own pocket, so the supplier is
	swapped deliberately and the merchant is preserved in the remarks.
	"""
	company = get_company()
	employee = payer(doc)
	if not employee:
		frappe.throw(
			_(
				"Set <b>Paid By</b> on this document before approving it — there is no way to "
				"tell who paid for this out of their own pocket, and guessing bills the wrong "
				"person. Use <b>Create Purchase Invoice</b> instead if it went on a company card."
			),
			title=_("Nobody to reimburse"),
		)

	supplier = reimbursement_supplier(employee)
	if not supplier:
		name = frappe.db.get_value("Employee", employee, "employee_name") or employee
		frappe.throw(
			_(
				"{0} has no reimbursement Supplier on their Employee record, so there is nothing "
				"to bill this to. Set <b>Reimbursement Supplier</b> on their Employee record, or "
				"use <b>Create Purchase Invoice</b> if this was paid on a company card."
			).format(name),
			title=_("No reimbursement Supplier"),
		)

	from erpnext_enhancements.accounting_intake.actions import vendor_bill

	# The merchant, kept where a human will read it. `_standalone_pi` puts the
	# intake reference in `remarks`, so this goes in first and is appended to.
	merchant = doc.party_name_text or doc.party or ""
	pi_name = vendor_bill.build_standalone_pi(doc, company, supplier=supplier)
	if merchant:
		frappe.db.set_value(
			"Purchase Invoice",
			pi_name,
			"remarks",
			_("Reimbursement to {0} for a receipt from {1}. Created from Document Intake {2}.").format(
				frappe.db.get_value("Employee", employee, "employee_name") or employee,
				merchant,
				doc.name,
			),
			update_modified=False,
		)
	return "Purchase Invoice", pi_name


def reimbursement_supplier(employee):
	"""The Supplier this person's receipts are billed to, or None.

	An explicit link and nothing else. See the module docstring for why matching on
	``supplier_name`` is not merely unreliable here but unsafe.

	Guarded with ``has_column`` because it is a **fixture** Custom Field, and
	``sync_fixtures()`` runs after the post-model-sync patches — so there is a real
	window on the migrate that introduces it where the DocType exists and the column
	does not. Note ``has_column`` takes a DOCTYPE and **raises** on an unknown table
	rather than returning False, so the ``try`` is doing real work.
	"""
	if not employee:
		return None
	try:
		if not frappe.db.has_column("Employee", "custom_reimbursement_supplier"):
			return None
	except Exception:
		return None
	supplier = frappe.db.get_value("Employee", employee, "custom_reimbursement_supplier")
	if not supplier:
		return None
	# A link can outlive its target, and posting against a deleted or disabled
	# Supplier fails deep inside the Purchase Invoice controller with a message
	# about nothing in particular.
	row = frappe.db.get_value("Supplier", supplier, ["name", "disabled"], as_dict=True)
	if not row or row.disabled:
		return None
	return row.name


# ------------------------------------------------------------- expense claim


@register(EXPENSE_CLAIM)
def post_expense_claim(doc):
	"""The hrms path. Guarded first, because without hrms it cannot run at all."""
	_require_expense_claims()

	company = get_company()
	employee = payer(doc)
	if not employee:
		frappe.throw(
			_(
				"Set <b>Paid By</b> on this document before approving it — a claim filed against "
				"the wrong employee is a payment to the wrong employee."
			),
			title=_("Nobody to reimburse"),
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


# --------------------------------------------------------------------- shared


def payer(doc):
	"""The Employee who paid, read from the document and **inferred from nothing**.

	This used to be ``doc.reviewed_by or frappe.session.user`` resolved to an
	Employee, and that is wrong in a way worth spelling out, because it looked
	entirely reasonable and the adversarial review of this change is what caught
	it.

	``reviewed_by`` is stamped by ``review.approve_document``, which is gated on
	``Accounts Manager``/``System Manager``. **The approver is by role design not
	the claimant.** So a technician's receipt, approved by the accountant, produced
	a draft Purchase Invoice payable to *the accountant's* reimbursement Supplier,
	with remarks confidently naming them as the person owed the money — on every
	receipt, not as an edge case. The technician who actually paid would never have
	been reimbursed, and nothing about the invoice looks unusual.

	There is no signal on the record that means "who paid" except the one now asked
	for. ``doc.owner`` is not it: Upload and Mobile are role-gated to accounting
	staff so a technician cannot use them, and Email and Drive rows are inserted by
	the mail hook and a scheduler job, so their owner is Administrator. Hence an
	explicit field, required at the approval gate the same way ``party`` already is
	for the three party actions.

	``extraction`` *suggests* a value on the Email channel, where the sender is
	recorded. A suggestion the reviewer can see and change is a different thing
	from a silent inference, and it is the only automation that is safe here.
	"""
	employee = doc.get("paid_by_employee")
	if not employee:
		return None
	# A link can outlive its target, and an inactive Employee is somebody who has
	# left -- reimbursing them through this route is at best a surprise.
	row = frappe.db.get_value("Employee", employee, ["name", "status"], as_dict=True)
	if not row or row.status != "Active":
		return None
	return row.name


def _default_expense_claim_type():
	return frappe.db.get_value("Expense Claim Type", {}, "name")
