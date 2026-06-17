# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Advisory matching for the Accounting Document Intake pipeline.

Reuses the pure fuzzy scorer in ``google_drive/drive_match.py`` to suggest the
party (Supplier/Customer) and the source document (Purchase Order / Sales
Invoice) for an extracted document. Suggestions are advisory only — the reviewer
always decides, and a no-match never blocks posting."""

import frappe
from frappe.utils import flt

from erpnext_enhancements.google_drive import drive_match

_PARTY_NAME_FIELD = {"Supplier": "supplier_name", "Customer": "customer_name"}


def match_party(party_name_text, party_type, limit=2000):
	"""Return ``(record_name, confidence, candidates)`` for the best party match.

	``candidates`` is a list of ``{record, label, score, tier}`` (best first).
	``record_name`` is filled only when the top score is at least Medium tier."""
	if not party_name_text or party_type not in _PARTY_NAME_FIELD:
		return None, 0.0, []
	name_field = _PARTY_NAME_FIELD[party_type]
	rows = frappe.get_all(party_type, fields=["name", name_field], limit=limit)
	candidates = [{"name": (r.get(name_field) or r["name"]), "record": r["name"]} for r in rows]
	ranked = drive_match.best_matches([party_name_text], candidates, limit=5)
	out = []
	for row in ranked:
		score = row["score"]
		out.append(
			{
				"record": row["folder"]["record"],
				"label": row["folder"]["name"],
				"score": score,
				"tier": drive_match.tier_for_score(score),
			}
		)
	if out and out[0]["score"] >= drive_match.TIER_MEDIUM:
		return out[0]["record"], out[0]["score"], out
	return None, (out[0]["score"] if out else 0.0), out


def match_documents(doc):
	"""Build advisory match-candidate rows for the document's source record,
	keyed off ``document_type``. Returns a list of plain dicts ready to append to
	the ``proposed_matches`` child table."""
	dt = doc.document_type
	if dt in ("Vendor Bill", "Packing Slip"):
		return _match_purchase_orders(doc)
	if dt == "Customer Remittance":
		return _match_sales_invoices(doc)
	return []


def _amount_proximity_score(a, b):
	a, b = flt(a), flt(b)
	if a <= 0 or b <= 0:
		return 55.0
	diff = abs(a - b) / max(a, b)
	return round(max(0.0, 100.0 * (1 - diff)), 1)


def _match_row(dt, name, label, score, rule, selected=False):
	return {
		"match_doctype": dt,
		"match_name": name,
		"label": (label or name)[:140],
		"score": round(flt(score), 1),
		"tier": drive_match.tier_for_score(score),
		"match_rule": rule,
		"selected": 1 if selected else 0,
	}


def _match_purchase_orders(doc):
	po_number = (doc.po_number_text or "").strip()
	if po_number and frappe.db.exists("Purchase Order", po_number):
		return [_match_row("Purchase Order", po_number, po_number, 100.0, "po_number", selected=True)]

	filters = {"docstatus": 1, "status": ["not in", ["Closed", "Completed", "Delivered"]]}
	supplier = doc.party if doc.proposed_party_type == "Supplier" else None
	if supplier:
		filters["supplier"] = supplier
	pos = frappe.get_all(
		"Purchase Order",
		filters=filters,
		fields=["name", "supplier", "grand_total"],
		order_by="transaction_date desc",
		limit=20,
	)
	target = flt(doc.grand_total)
	rows = [
		_match_row(
			"Purchase Order",
			po["name"],
			f"{po['name']} · {po.get('supplier') or ''}",
			_amount_proximity_score(target, po.get("grand_total")) if target else 60.0,
			"supplier+amount",
		)
		for po in pos
	]
	rows.sort(key=lambda r: r["score"], reverse=True)
	return rows[:5]


def _match_sales_invoices(doc):
	rows = []
	num = (doc.document_number or "").strip()
	if num and frappe.db.exists("Sales Invoice", num):
		rows.append(_match_row("Sales Invoice", num, num, 100.0, "invoice_no", selected=True))

	filters = {"docstatus": 1, "outstanding_amount": [">", 0]}
	customer = doc.party if doc.proposed_party_type == "Customer" else None
	if customer:
		filters["customer"] = customer
	sis = frappe.get_all(
		"Sales Invoice",
		filters=filters,
		fields=["name", "customer", "outstanding_amount"],
		order_by="posting_date desc",
		limit=20,
	)
	target = flt(doc.grand_total)
	for si in sis:
		rows.append(
			_match_row(
				"Sales Invoice",
				si["name"],
				f"{si['name']} · outstanding {flt(si.get('outstanding_amount'))}",
				_amount_proximity_score(target, si.get("outstanding_amount")) if target else 60.0,
				"customer+amount",
			)
		)
	seen, uniq = set(), []
	for r in sorted(rows, key=lambda r: (r["match_rule"] != "invoice_no", -r["score"])):
		if r["match_name"] in seen:
			continue
		seen.add(r["match_name"])
		uniq.append(r)
	return uniq[:5]
