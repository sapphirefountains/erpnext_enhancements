# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted review actions for the Document Intake queue.

Two reviewer roles, two gates: the inventory clerk (Stock Manager) approves any
proposed new Items (``approve_items``), then the accountant (Accounts Manager)
approves the document (``approve_document``) — which moves it to ``Approved``.
The per-type posting handler that turns an Approved document into a draft
ERPNext record lands in a later PR; nothing is submitted here."""

import frappe
from frappe import _

from erpnext_enhancements.accounting_intake.audit import log_intake

_ITEM_ROLES = {"Stock Manager", "System Manager"}
_APPROVE_ROLES = {"Accounts Manager", "System Manager"}
_PARTY_ACTIONS = {"Create Purchase Invoice", "Create Purchase Receipt", "Create Payment Entry"}


def _require(roles):
	if not (set(frappe.get_roles()) & roles):
		frappe.throw(_("You are not permitted to perform this action."), frappe.PermissionError)


@frappe.whitelist()
def approve_items(docname):
	"""Inventory clerk: create Items for rows marked Approved, then advance the
	document to Needs Review once no proposed Item is still Pending."""
	_require(_ITEM_ROLES)
	doc = frappe.get_doc("Document Intake", docname)
	created = 0
	for row in doc.line_items:
		if row.new_item_proposed and row.item_review_status == "Approved" and not row.matched_item:
			row.matched_item = _create_item(row)
			row.new_item_proposed = 0
			created += 1
	pending = [r for r in doc.line_items if r.new_item_proposed and (r.item_review_status or "Pending") == "Pending"]
	doc.item_reviewed_by = frappe.session.user
	if not pending:
		doc.status = "Needs Review"
	doc.save(ignore_permissions=True)
	log_intake("Item Review", "Success", accounting_document=docname, detail=f"{created} item(s) created")
	return {"created": created, "status": doc.status, "pending": len(pending)}


def _create_item(row):
	name = (row.proposed_item_name or row.description or "Item")[:140]
	if frappe.db.exists("Item", name):
		return name
	existing = frappe.db.get_value("Item", {"item_name": name}, "name")
	if existing:
		return existing
	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": name,
			"item_name": name,
			"item_group": row.proposed_item_group or _default_group(),
			"stock_uom": row.proposed_uom or "Nos",
			"is_stock_item": 1 if row.is_stock_item else 0,
		}
	)
	item.flags.ignore_mandatory = True
	item.insert(ignore_permissions=True)
	return item.name


def _default_group():
	return frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"


def _validate_for_approval(doc):
	issues = []
	if (doc.proposed_action or "") in ("", "Ignore"):
		issues.append(_("Choose a proposed action before approving."))
	pending = [r for r in doc.line_items if r.new_item_proposed and (r.item_review_status or "Pending") == "Pending"]
	if pending:
		issues.append(_("{0} proposed Item(s) still need inventory-clerk review.").format(len(pending)))
	if (doc.proposed_action or "") in _PARTY_ACTIONS and not doc.party:
		issues.append(_("Set the Party before approving."))
	return issues


@frappe.whitelist()
def approve_document(docname):
	"""Accountant: approve the proposed action and move to Approved. The draft
	ERPNext record is created by the per-type posting handler (later PR)."""
	_require(_APPROVE_ROLES)
	doc = frappe.get_doc("Document Intake", docname)
	issues = _validate_for_approval(doc)
	if issues:
		frappe.throw("<br>".join(issues))
	doc.reviewed_by = frappe.session.user
	doc.reviewed_on = frappe.utils.now_datetime()
	doc.status = "Approved"
	doc.save(ignore_permissions=True)
	log_intake("Approve", "Success", accounting_document=docname, detail=doc.proposed_action)
	return {"status": doc.status}


@frappe.whitelist()
def reject_document(docname, reason=None):
	_require(_APPROVE_ROLES)
	doc = frappe.get_doc("Document Intake", docname)
	doc.status = "Rejected"
	if reason:
		doc.review_notes = reason
	doc.reviewed_by = frappe.session.user
	doc.reviewed_on = frappe.utils.now_datetime()
	doc.save(ignore_permissions=True)
	log_intake("Approve", "Skipped", accounting_document=docname, detail="Rejected")
	return {"status": doc.status}


@frappe.whitelist()
def reprocess(docname):
	"""Re-run extraction for a stuck/failed/changed document."""
	_require(_APPROVE_ROLES | _ITEM_ROLES)
	doc = frappe.get_doc("Document Intake", docname)
	doc.db_set("status", "Received", update_modified=False)
	frappe.enqueue(
		"erpnext_enhancements.accounting_intake.intake.run_extraction",
		queue="long",
		enqueue_after_commit=True,
		docname=docname,
	)
	return {"status": "Received"}
