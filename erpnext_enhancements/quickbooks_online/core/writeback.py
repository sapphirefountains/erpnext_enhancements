# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""ERPNext → QuickBooks Online write-back for the Accounting Document Intake
feature — the *only* ERPNext→QBO write path.

When the accountant submits an intake-created Purchase Invoice or Payment Entry,
a "Push to QuickBooks" button calls :func:`push_to_qbo`, which creates the
matching Bill / Payment in QBO and then **immediately seeds the QuickBooks Sync
Mapping ledger**. That seed is the loop-guard: the next hourly CDC poll sees the
new QBO transaction, finds the mapping in ``upsert_entity``'s "already mapped →
update" branch, and links it to our existing record instead of importing a
duplicate.

Gated by ``Accounting Intake Settings.qbo_writeback_enabled`` (default off). Never
auto-creates QBO master records: if a Supplier/Customer/Account isn't already
linked to a QBO Vendor/Customer/Account it fails with a clear message so a human
can resolve the link (or let it import from QBO) first.

Scope v1: Purchase Invoice → Bill, Payment Entry (Receive) → Payment. The
scanned document is attached to the QBO transaction by a follow-up change."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, getdate

from erpnext_enhancements.quickbooks_online.core import mapping
from erpnext_enhancements.quickbooks_online.core.client import QuickBooksClient
from erpnext_enhancements.quickbooks_online.core.utils import get_settings

# ERPNext doctype -> (QBO entity type, QBO API path segment)
_SUPPORTED = {
	"Purchase Invoice": ("Bill", "bill"),
	"Payment Entry": ("Payment", "payment"),
}
_PUSH_ROLES = {"Accounts Manager", "System Manager"}


def _writeback_enabled() -> bool:
	return bool(frappe.db.get_single_value("Accounting Intake Settings", "qbo_writeback_enabled"))


def _qbo_id(erpnext_doctype: str, erpnext_name: str | None) -> str | None:
	"""Reverse Sync Mapping lookup — the QBO id linked to an ERPNext record."""
	if not erpnext_name:
		return None
	return frappe.db.get_value(
		"QuickBooks Sync Mapping",
		{"erpnext_doctype": erpnext_doctype, "erpnext_name": erpnext_name, "deleted": 0},
		"qbo_id",
	)


def _from_intake(doctype: str, name: str) -> bool:
	return bool(frappe.db.exists("Document Intake", {"created_doctype": doctype, "created_docname": name}))


def _already_linked(doctype: str, name: str) -> bool:
	if frappe.db.get_value(doctype, name, "custom_qbo_id"):
		return True
	return bool(
		frappe.db.exists("QuickBooks Sync Mapping", {"erpnext_doctype": doctype, "erpnext_name": name, "deleted": 0})
	)


@frappe.whitelist()
def push_to_qbo(doctype: str, name: str):
	"""Create the QBO Bill/Payment for a submitted, intake-created ERPNext doc and
	seed the Sync Mapping so CDC links rather than duplicates. Synchronous, so the
	button surfaces fail-clear errors directly."""
	if not (set(frappe.get_roles()) & _PUSH_ROLES):
		frappe.throw(_("You are not permitted to push to QuickBooks."), frappe.PermissionError)
	if doctype not in _SUPPORTED:
		frappe.throw(_("QuickBooks write-back is not supported for {0}.").format(doctype))
	if not _writeback_enabled():
		frappe.throw(_("QuickBooks write-back is disabled in Accounting Intake Settings."))
	if not _from_intake(doctype, name):
		frappe.throw(_("Only documents created by Accounting Document Intake can be pushed to QuickBooks."))
	if _already_linked(doctype, name):
		frappe.throw(_("This document is already linked to QuickBooks."))

	doc = frappe.get_doc(doctype, name)
	if doc.docstatus != 1:
		frappe.throw(_("Submit the document before pushing it to QuickBooks."))

	settings = get_settings()
	if not settings.realm_id:
		frappe.throw(_("QuickBooks Online is not connected."))

	entity_type, path = _SUPPORTED[doctype]
	payload = _build_bill(doc) if doctype == "Purchase Invoice" else _build_payment(doc)

	client = QuickBooksClient(settings)
	response = client.request("POST", f"/v3/company/{settings.realm_id}/{path}", json=payload)
	created = (response or {}).get(entity_type) or {}
	qbo_id = str(created.get("Id") or "")
	if not qbo_id:
		frappe.throw(_("QuickBooks did not return an id for the created {0}.").format(entity_type))

	# Loop-guard: seed the ledger before the next CDC poll sees the new txn, so it
	# links to this record instead of importing a duplicate. owned_fields mirror
	# what the importer would compute, so the eventual re-import is a clean no-op.
	_dt, values = mapping.map_qbo_to_erpnext(entity_type, created, settings)
	mapping.save_mapping(entity_type, qbo_id, created, doctype, name, values or {}, match_status="Created")

	frappe.db.set_value(doctype, name, "custom_qbo_id", qbo_id, update_modified=False)
	di = frappe.db.get_value("Document Intake", {"created_doctype": doctype, "created_docname": name}, "name")
	if di:
		frappe.db.set_value(
			"Document Intake", di,
			{"qbo_pushed": 1, "qbo_mapping": f"{entity_type}:{qbo_id}"},
			update_modified=False,
		)

	return {"qbo_entity": entity_type, "qbo_id": qbo_id}


def _build_bill(pi) -> dict:
	"""QBO Bill payload from a Purchase Invoice (inverse of mapping._map_purchase_invoice)."""
	vendor_id = _qbo_id("Supplier", pi.supplier)
	if not vendor_id:
		frappe.throw(
			_("Supplier '{0}' isn't linked to a QuickBooks Vendor — link it (or let it import from QBO) before pushing.").format(pi.supplier)
		)

	lines = []
	for item in pi.items:
		account = item.expense_account or pi.credit_to
		account_id = _qbo_id("Account", account)
		if not account_id:
			frappe.throw(_("Account '{0}' isn't linked to a QuickBooks Account.").format(account))
		lines.append(
			{
				"DetailType": "AccountBasedExpenseLineDetail",
				"Amount": flt(item.amount),
				"Description": (item.description or item.item_name or "")[:1000] or None,
				"AccountBasedExpenseLineDetail": {"AccountRef": {"value": account_id}},
			}
		)

	payload = {
		"VendorRef": {"value": vendor_id},
		"Line": lines,
		"TxnDate": str(getdate(pi.bill_date or pi.posting_date)),
		"PrivateNote": f"ERPNext {pi.name} (Document Intake)",
	}
	if pi.bill_no:
		payload["DocNumber"] = str(pi.bill_no)[:21]  # QBO DocNumber max length
	if pi.due_date:
		payload["DueDate"] = str(getdate(pi.due_date))
	return payload


def _build_payment(pe) -> dict:
	"""QBO Payment payload from a Payment Entry (inverse of mapping._map_payment_entry)."""
	if pe.payment_type != "Receive" or pe.party_type != "Customer":
		frappe.throw(_("Only customer (Receive) payments are pushed to QuickBooks."))
	customer_id = _qbo_id("Customer", pe.party)
	if not customer_id:
		frappe.throw(_("Customer '{0}' isn't linked to a QuickBooks Customer.").format(pe.party))

	payload = {
		"CustomerRef": {"value": customer_id},
		"TotalAmt": flt(pe.received_amount or pe.paid_amount),
		"TxnDate": str(getdate(pe.posting_date)),
		"PrivateNote": f"ERPNext {pe.name} (Document Intake)",
	}
	# Apply against the linked QBO Invoice(s) for any allocated, QBO-mapped Sales Invoices.
	lines = []
	for ref in pe.references or []:
		if ref.reference_doctype != "Sales Invoice":
			continue
		invoice_id = _qbo_id("Sales Invoice", ref.reference_name)
		if invoice_id:
			lines.append(
				{"Amount": flt(ref.allocated_amount), "LinkedTxn": [{"TxnId": invoice_id, "TxnType": "Invoice"}]}
			)
	if lines:
		payload["Line"] = lines
	return payload
