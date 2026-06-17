# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Map a Triton Document AI extraction onto a Document Intake review record:
header fields, line items (with Item resolution), advisory party/document
matches, and the resulting review status. New Items that can't be resolved are
proposed on the line for the inventory clerk (Stock Manager) to review."""

import re

import frappe
from frappe.utils import flt, getdate

from erpnext_enhancements.accounting_intake import matching

_PARTY_TYPE_BY_DOC = {
	"Vendor Bill": "Supplier",
	"Receipt / Expense": "Supplier",
	"Packing Slip": "Supplier",
	"Customer Remittance": "Customer",
}
_ACTION_BY_DOC = {
	"Vendor Bill": "Create Purchase Invoice",
	"Customer Remittance": "Create Payment Entry",
	"Receipt / Expense": "Create Expense Claim",
	"Packing Slip": "Create Purchase Receipt",
}

# Extracted entity keys we look for, in priority order, per target field.
_PARTY_KEYS = ["supplier_name", "customer_name", "vendor_name", "merchant_name", "receiver_name", "remitter_name"]
_NUMBER_KEYS = ["invoice_id", "invoice_number", "document_number", "receipt_id", "payment_reference", "reference_number"]
_DATE_KEYS = ["invoice_date", "receipt_date", "document_date", "payment_date", "date", "purchase_time"]
_PO_KEYS = ["purchase_order", "po_number"]
_TOTAL_KEYS = ["total_amount", "amount", "payment_amount", "grand_total"]


def _first(entities, keys):
	for k in keys:
		v = entities.get(k)
		if v:
			return v
	return None


def _to_amount(val):
	if val is None:
		return None
	s = re.sub(r"[^0-9.\-]", "", str(val))
	try:
		return flt(s) if s not in ("", "-", ".") else None
	except Exception:
		return None


def _to_date(val):
	if not val:
		return None
	try:
		return getdate(val)
	except Exception:
		return None


def _to_confidence(val):
	c = flt(val)
	return c * 100 if c <= 1 else c


def apply_extraction(doc, result):
	"""Populate ``doc`` (a Document Intake) from a Triton extraction ``result``
	and set its review status. Does not save."""
	entities = result.get("entities") or {}
	doc.extracted_json = frappe.as_json(result)
	doc.raw_text = (result.get("text") or "")[:100000]
	if result.get("confidence") is not None:
		doc.extraction_confidence = _to_confidence(result.get("confidence"))
	doc.processor_used = result.get("processor")
	doc.field_confidence = frappe.as_json(result.get("entity_confidences") or {})

	# Header
	doc.party_name_text = _first(entities, _PARTY_KEYS)
	doc.document_number = _first(entities, _NUMBER_KEYS)
	doc.document_date = _to_date(_first(entities, _DATE_KEYS))
	doc.due_date = _to_date(entities.get("due_date"))
	doc.po_number_text = _first(entities, _PO_KEYS)
	cur = entities.get("currency")
	if cur and frappe.db.exists("Currency", cur):
		doc.currency = cur
	doc.grand_total = _to_amount(_first(entities, _TOTAL_KEYS))
	doc.net_total = _to_amount(entities.get("net_amount"))
	doc.tax_total = _to_amount(entities.get("total_tax_amount") or entities.get("tax_amount"))

	doc.proposed_party_type = _PARTY_TYPE_BY_DOC.get(doc.document_type)
	if not doc.proposed_action:
		doc.proposed_action = _ACTION_BY_DOC.get(doc.document_type)

	# Line items + Item resolution
	doc.set("line_items", [])
	needs_item_review = False
	for li in result.get("line_items") or []:
		row = doc.append("line_items", {})
		row.description = (li.get("description") or "")[:255]
		row.qty = flt(li.get("quantity")) or 1
		row.uom = li.get("unit")
		row.rate = _to_amount(li.get("unit_price"))
		row.amount = _to_amount(li.get("amount"))
		if li.get("confidence") is not None:
			row.confidence = _to_confidence(li.get("confidence"))
		matched = _resolve_item(li)
		if matched:
			row.matched_item = matched
		elif row.description:
			row.new_item_proposed = 1
			row.proposed_item_name = row.description[:140]
			row.proposed_item_group = _default_item_group()
			row.proposed_uom = _valid_uom(row.uom)
			row.is_stock_item = 0
			row.item_review_status = "Pending"
			needs_item_review = True

	# Advisory party match
	if doc.party_name_text and doc.proposed_party_type:
		record, score, _candidates = matching.match_party(doc.party_name_text, doc.proposed_party_type)
		doc.party_match_confidence = score
		if record and not doc.party:
			doc.party = record

	# Advisory document match (best with a party resolved above)
	doc.set("proposed_matches", [])
	doc.selected_match_doctype = None
	doc.selected_match_name = None
	for m in matching.match_documents(doc):
		doc.append("proposed_matches", m)
		if m.get("selected"):
			doc.selected_match_doctype = m["match_doctype"]
			doc.selected_match_name = m["match_name"]

	doc.status = "Needs Item Review" if needs_item_review else "Needs Review"


def _resolve_item(li):
	"""Return an existing Item name for a line, or None. Exact match on product
	code or item name only — fuzzy Item matching is a future enhancement."""
	code = (li.get("product_code") or "").strip()
	if code:
		if frappe.db.exists("Item", code):
			return code
		by_code = frappe.db.get_value("Item", {"item_code": code}, "name")
		if by_code:
			return by_code
	desc = (li.get("description") or "").strip()
	if desc:
		by_name = frappe.db.get_value("Item", {"item_name": desc}, "name")
		if by_name:
			return by_name
	return None


def _default_item_group():
	return frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"


def _valid_uom(uom):
	if uom and frappe.db.exists("UOM", uom):
		return uom
	return frappe.db.get_value("UOM", {"name": "Nos"}, "name") or None
