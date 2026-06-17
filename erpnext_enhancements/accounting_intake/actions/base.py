# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Per-type posting handlers for Approved Document Intake records.

Each handler creates a **draft** (docstatus 0) ERPNext record — never submitted;
the accountant has approved the proposal, but the resulting Purchase Invoice /
Expense Claim / etc. is still reviewed and submitted through the normal ERPNext
flow. ``post_document`` is the enqueued dispatcher: it routes by the document's
``proposed_action``, records the created record, then files the source document."""

import frappe

from erpnext_enhancements.accounting_intake.audit import log_intake

# proposed_action -> handler(doc) -> (created_doctype, created_docname)
HANDLERS = {}


def register(action):
	def deco(fn):
		HANDLERS[action] = fn
		return fn

	return deco


def get_company():
	"""Resolve the company to post against: Accounting Intake Settings default,
	then the global default company, then any company."""
	settings = frappe.get_cached_doc("Accounting Intake Settings")
	return (
		settings.get("default_company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
		or frappe.db.get_value("Company", {}, "name")
	)


def post_document(docname):
	"""Background job: create the draft ERPNext record for an Approved Document
	Intake, then file the source document. Idempotent — no-op if already posted."""
	# Import handlers so they register (also makes the enqueued worker register).
	from erpnext_enhancements.accounting_intake.actions import (  # noqa: F401
		customer_remittance,
		packing_slip,
		receipt_expense,
		vendor_bill,
	)

	doc = frappe.get_doc("Document Intake", docname)
	if doc.status != "Approved" or doc.created_docname:
		return

	handler = HANDLERS.get(doc.proposed_action)
	if not handler:
		log_intake("Post", "Skipped", accounting_document=docname, detail=f"No handler for {doc.proposed_action}")
		return

	try:
		doc.db_set("status", "Posting", update_modified=False)
		target_dt, target_name = handler(doc)
		doc.reload()
		doc.created_doctype = target_dt
		doc.created_docname = target_name
		doc.status = "Posted"
		doc.save(ignore_permissions=True)
		log_intake("Post", "Success", accounting_document=docname, reference_doctype=target_dt, reference_name=target_name)

		from erpnext_enhancements.accounting_intake import filing

		filing.file_document(docname)
	except Exception:
		frappe.db.rollback()
		if frappe.db.exists("Document Intake", docname):
			failed = frappe.get_doc("Document Intake", docname)
			failed.db_set("status", "Failed", update_modified=False)
			failed.db_set("error", frappe.get_traceback()[:1000], update_modified=False)
		log_intake(
			"Post", "Failed", accounting_document=docname, error=frappe.get_traceback(),
			payload={
				"method": "erpnext_enhancements.accounting_intake.actions.base.post_document",
				"kwargs": {"docname": docname},
			},
		)
		frappe.log_error(frappe.get_traceback(), "Accounting Intake Posting")
