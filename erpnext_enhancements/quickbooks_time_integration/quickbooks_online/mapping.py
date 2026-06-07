from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.constants import ENTITY_DOCTYPE_MAP
from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.utils import (
	json_dumps,
	json_loads,
	parse_qbo_datetime,
)


def get_erpnext_doctype(entity_type: str) -> str | None:
	return ENTITY_DOCTYPE_MAP.get(entity_type)


def map_qbo_to_erpnext(entity_type: str, payload: dict, settings) -> tuple[str | None, dict]:
	mappers = {
		"Account": _map_account,
		"Customer": _map_customer,
		"Vendor": _map_supplier,
		"Item": _map_item,
		"Invoice": _map_sales_invoice,
		"Bill": _map_purchase_invoice,
		"Payment": _map_payment_entry,
		"JournalEntry": _map_journal_entry,
		"Estimate": _map_quotation,
		"PurchaseOrder": _map_purchase_order,
		"Deposit": _map_payment_entry,
		"TaxCode": _map_tax_code,
	}
	mapper = mappers.get(entity_type)
	if not mapper:
		return None, {}
	return mapper(payload, settings)


def upsert_entity(entity_type: str, payload: dict, settings, *, overwrite=False, preview=False):
	erpnext_doctype, values = map_qbo_to_erpnext(entity_type, payload, settings)
	if not erpnext_doctype:
		return {"action": "skipped", "reason": "No native ERPNext mapping"}

	qbo_id = str(payload.get("Id"))
	if not qbo_id:
		return {"action": "skipped", "reason": "QBO payload has no Id"}

	preflight_issues = validate_mapped_values(entity_type, erpnext_doctype, values, include_doc_required=False)
	if preflight_issues:
		if not preview:
			save_manual_review_mapping(entity_type, qbo_id, payload, erpnext_doctype, preflight_issues)
		return {
			"action": "manual_review",
			"doctype": erpnext_doctype,
			"qbo_id": qbo_id,
			"reason": "; ".join(preflight_issues),
			"issues": preflight_issues,
		}

	mapping = get_mapping(entity_type, qbo_id)
	if mapping and mapping.erpnext_name and frappe.db.exists(erpnext_doctype, mapping.erpnext_name):
		doc = frappe.get_doc(erpnext_doctype, mapping.erpnext_name)
		conflicts = detect_conflicts(doc, values, mapping)
		if conflicts and not overwrite:
			if not preview:
				mapping.conflict_status = "Conflict"
				mapping.save(ignore_permissions=True)
			return {"action": "conflict", "doctype": erpnext_doctype, "name": doc.name, "fields": conflicts}
		if preview:
			return {"action": "update", "doctype": erpnext_doctype, "name": doc.name, "fields": list(values)}
		apply_values(doc, values)
		doc.save(ignore_permissions=True)
		save_mapping(entity_type, qbo_id, payload, erpnext_doctype, doc.name, values, conflict_status="Clean")
		return {"action": "updated", "doctype": erpnext_doctype, "name": doc.name}

	existing_match = find_existing_match(entity_type, payload, settings)
	if existing_match:
		if existing_match["status"] == "ambiguous":
			if not preview:
				save_pending_mapping(entity_type, qbo_id, payload, erpnext_doctype, existing_match)
			return {
				"action": "manual_review",
				"doctype": erpnext_doctype,
				"candidates": existing_match["candidates"],
				"reason": existing_match["reason"],
			}
		if preview:
			return {
				"action": "link",
				"doctype": erpnext_doctype,
				"name": existing_match["name"],
				"match_rule": existing_match["rule"],
			}
		doc = frappe.get_doc(erpnext_doctype, existing_match["name"])
		applied_values = apply_blank_values(doc, values)
		doc.save(ignore_permissions=True)
		save_mapping(
			entity_type,
			qbo_id,
			payload,
			erpnext_doctype,
			doc.name,
			applied_values or _matching_owned_values(doc, values),
			conflict_status="Clean",
			match_status="Auto Matched",
			match_rule=existing_match["rule"],
			match_confidence=existing_match["confidence"],
		)
		return {
			"action": "linked",
			"doctype": erpnext_doctype,
			"name": doc.name,
			"match_rule": existing_match["rule"],
			"filled_fields": list(applied_values),
		}

	if preview:
		create_issues = validate_mapped_values(entity_type, erpnext_doctype, values)
		if create_issues:
			return {
				"action": "manual_review",
				"doctype": erpnext_doctype,
				"qbo_id": qbo_id,
				"reason": "; ".join(create_issues),
				"issues": create_issues,
			}
		return {"action": "create", "doctype": erpnext_doctype, "fields": list(values)}

	create_issues = validate_mapped_values(entity_type, erpnext_doctype, values)
	if create_issues:
		save_manual_review_mapping(entity_type, qbo_id, payload, erpnext_doctype, create_issues)
		return {
			"action": "manual_review",
			"doctype": erpnext_doctype,
			"qbo_id": qbo_id,
			"reason": "; ".join(create_issues),
			"issues": create_issues,
		}

	doc = frappe.new_doc(erpnext_doctype)
	apply_values(doc, values)
	doc.insert(ignore_permissions=True)
	save_mapping(
		entity_type,
		qbo_id,
		payload,
		erpnext_doctype,
		doc.name,
		values,
		conflict_status="Clean",
		match_status="Created",
	)
	return {"action": "created", "doctype": erpnext_doctype, "name": doc.name}


def link_existing_record(
	entity_type: str, qbo_id: str, erpnext_doctype: str, erpnext_name: str, *, apply_qbo_data=False
):
	settings = frappe.get_single("QuickBooks Online Settings")
	if not frappe.db.exists(erpnext_doctype, erpnext_name):
		frappe.throw(f"{erpnext_doctype} {erpnext_name} does not exist.")

	payload_doc = _latest_raw_payload(entity_type, qbo_id)
	payload = json_loads(payload_doc.payload, default={}) if payload_doc else {}
	if not payload:
		frappe.throw("No QuickBooks raw payload is available for this entity. Sync or preview it first.")

	expected_doctype, values = map_qbo_to_erpnext(entity_type, payload, settings)
	if expected_doctype and expected_doctype != erpnext_doctype:
		frappe.throw(f"{entity_type} should be linked to {expected_doctype}, not {erpnext_doctype}.")

	owned_values = {}
	if apply_qbo_data:
		doc = frappe.get_doc(erpnext_doctype, erpnext_name)
		owned_values = apply_blank_values(doc, values)
		doc.save(ignore_permissions=True)

	mapping = save_mapping(
		entity_type,
		qbo_id,
		payload,
		erpnext_doctype,
		erpnext_name,
		owned_values or _matching_owned_values(frappe.get_doc(erpnext_doctype, erpnext_name), values),
		conflict_status="Clean",
		match_status="Manual Matched",
		match_rule="manual",
		match_confidence=100,
	)
	frappe.db.commit()
	return mapping.name


def preview_existing_matches(entity_types=None, limit=100):
	settings = frappe.get_single("QuickBooks Online Settings")
	results = []
	for raw in frappe.get_all(
		"QuickBooks Raw Payload",
		filters={
			"qbo_entity_type": ["in", entity_types or ["Account", "Customer", "Vendor", "Item", "TaxCode"]]
		},
		fields=["qbo_entity_type", "qbo_id", "payload"],
		order_by="creation desc",
		limit_page_length=limit,
	):
		if not raw.qbo_id or get_mapping(raw.qbo_entity_type, raw.qbo_id):
			continue
		payload = json_loads(raw.payload, default={}) or {}
		erpnext_doctype, values = map_qbo_to_erpnext(raw.qbo_entity_type, payload, settings)
		match = find_existing_match(raw.qbo_entity_type, payload, settings)
		results.append(
			{
				"entity_type": raw.qbo_entity_type,
				"qbo_id": raw.qbo_id,
				"qbo_name": _display_name(payload),
				"erpnext_doctype": erpnext_doctype,
				"match": match,
				"mapped_fields": values,
			}
		)
	return results


def mark_deleted(entity_type: str, qbo_id: str, *, preview=False):
	mapping = get_mapping(entity_type, qbo_id)
	if preview:
		return {"action": "delete", "mapping": mapping.name if mapping else None}
	if mapping:
		mapping.deleted = 1
		mapping.conflict_status = "Clean"
		mapping.save(ignore_permissions=True)
	return {"action": "deleted"}


def get_mapping(entity_type: str, qbo_id: str):
	name = frappe.db.get_value(
		"QuickBooks Sync Mapping",
		{"qbo_entity_type": entity_type, "qbo_id": str(qbo_id)},
		"name",
	)
	return frappe.get_doc("QuickBooks Sync Mapping", name) if name else None


def save_mapping(
	entity_type: str,
	qbo_id: str,
	payload: dict,
	erpnext_doctype: str,
	erpnext_name: str,
	values: dict,
	**extra,
):
	mapping = get_mapping(entity_type, qbo_id) or frappe.new_doc("QuickBooks Sync Mapping")
	mapping.qbo_entity_type = entity_type
	mapping.qbo_id = str(qbo_id)
	mapping.erpnext_doctype = erpnext_doctype
	mapping.erpnext_name = erpnext_name
	mapping.sync_token = payload.get("SyncToken")
	mapping.last_qbo_updated_at = parse_qbo_datetime((payload.get("MetaData") or {}).get("LastUpdatedTime"))
	mapping.last_synced_at = now_datetime()
	mapping.deleted = 0
	mapping.owned_fields = json_dumps(values)
	for fieldname, value in extra.items():
		setattr(mapping, fieldname, value)
	if mapping.is_new():
		mapping.insert(ignore_permissions=True)
	else:
		mapping.save(ignore_permissions=True)
	return mapping


def save_pending_mapping(entity_type: str, qbo_id: str, payload: dict, erpnext_doctype: str, match: dict):
	mapping = get_mapping(entity_type, qbo_id) or frappe.new_doc("QuickBooks Sync Mapping")
	mapping.qbo_entity_type = entity_type
	mapping.qbo_id = str(qbo_id)
	mapping.erpnext_doctype = erpnext_doctype
	mapping.sync_token = payload.get("SyncToken")
	mapping.last_qbo_updated_at = parse_qbo_datetime((payload.get("MetaData") or {}).get("LastUpdatedTime"))
	mapping.last_synced_at = now_datetime()
	mapping.deleted = 0
	mapping.conflict_status = "Pending Review"
	mapping.match_status = "Pending Review"
	mapping.match_rule = match.get("reason")
	mapping.match_confidence = 50
	mapping.owned_fields = json_dumps({"candidates": match.get("candidates", [])})
	if mapping.is_new():
		mapping.insert(ignore_permissions=True)
	else:
		mapping.save(ignore_permissions=True)
	return mapping


def save_manual_review_mapping(entity_type: str, qbo_id: str, payload: dict, erpnext_doctype: str, issues: list[str]):
	mapping = get_mapping(entity_type, qbo_id) or frappe.new_doc("QuickBooks Sync Mapping")
	mapping.qbo_entity_type = entity_type
	mapping.qbo_id = str(qbo_id)
	mapping.erpnext_doctype = erpnext_doctype
	mapping.sync_token = payload.get("SyncToken")
	mapping.last_qbo_updated_at = parse_qbo_datetime((payload.get("MetaData") or {}).get("LastUpdatedTime"))
	mapping.last_synced_at = now_datetime()
	mapping.deleted = 0
	mapping.conflict_status = "Pending Review"
	mapping.match_status = "Pending Review"
	mapping.match_rule = "preflight"
	mapping.match_confidence = 0
	mapping.owned_fields = json_dumps({"issues": issues})
	if mapping.is_new():
		mapping.insert(ignore_permissions=True)
	else:
		mapping.save(ignore_permissions=True)
	return mapping


def validate_mapped_values(
	entity_type: str, erpnext_doctype: str, values: dict, *, include_doc_required: bool = True
) -> list[str]:
	issues = []
	for fieldname in sorted(_required_mapped_fields(entity_type, erpnext_doctype, values, include_doc_required)):
		if _is_empty_required_value(values.get(fieldname)):
			issues.append(f"Missing required field: {fieldname}")
	for account in _blocked_stock_accounts(entity_type, values):
		issues.append(f"Stock account requires a stock transaction: {account}")
	return issues


def _required_mapped_fields(
	entity_type: str, erpnext_doctype: str, values: dict, include_doc_required: bool = True
) -> set[str]:
	fields = {
		"Invoice": {"company", "customer", "items"},
		"Bill": {"company", "supplier", "items"},
		"Estimate": {"company", "party_name", "items"},
		"PurchaseOrder": {"company", "supplier", "items"},
		"JournalEntry": {"company", "accounts"},
	}.get(entity_type, set())
	if not include_doc_required:
		return fields
	try:
		meta = frappe.get_meta(erpnext_doctype)
	except Exception:
		return fields
	for df in getattr(meta, "fields", []) or []:
		if not getattr(df, "reqd", 0) or not getattr(df, "fieldname", None):
			continue
		if _can_validate_required_field(df, values):
			fields.add(df.fieldname)
	return fields


def _can_validate_required_field(df, values: dict) -> bool:
	if df.fieldname in values:
		return True
	if getattr(df, "default", None):
		return False
	return getattr(df, "fieldtype", None) in {
		"Attach",
		"Check",
		"Code",
		"Currency",
		"Data",
		"Date",
		"Datetime",
		"Float",
		"Int",
		"Link",
		"Long Text",
		"Percent",
		"Select",
		"Small Text",
		"Table",
		"Text",
		"Time",
	}


def _is_empty_required_value(value):
	return value in (None, "") or (isinstance(value, list) and not value)


def _blocked_stock_accounts(entity_type: str, values: dict) -> list[str]:
	if entity_type != "JournalEntry":
		return []
	stock_accounts = []
	for row in values.get("accounts") or []:
		account = row.get("account")
		if account and frappe.db.get_value("Account", account, "account_type") == "Stock":
			stock_accounts.append(account)
	return stock_accounts


def detect_conflicts(doc, incoming_values: dict, mapping) -> list[str]:
	owned = json_loads(mapping.owned_fields, default={}) or {}
	conflicts = []
	for fieldname, previous_value in owned.items():
		if fieldname not in incoming_values:
			continue
		current_value = doc.get(fieldname)
		if _normalize(current_value) != _normalize(previous_value) and _normalize(
			current_value
		) != _normalize(incoming_values[fieldname]):
			conflicts.append(fieldname)
	return conflicts


def apply_values(doc, values: dict):
	for fieldname, value in values.items():
		if value is not None:
			doc.set(fieldname, value)


def apply_blank_values(doc, values: dict) -> dict:
	applied = {}
	for fieldname, value in values.items():
		if value is None:
			continue
		if isinstance(value, list):
			continue
		if doc.get(fieldname) in (None, ""):
			doc.set(fieldname, value)
			applied[fieldname] = value
	return applied


def find_existing_match(entity_type: str, payload: dict, settings):
	matchers = {
		"Account": _match_account,
		"Customer": _match_customer,
		"Vendor": _match_supplier,
		"Item": _match_item,
		"TaxCode": _match_tax_code,
	}
	matcher = matchers.get(entity_type)
	return matcher(payload, settings) if matcher else None


def _normalize(value):
	return "" if value is None else str(value)


def _display_name(payload):
	return (
		payload.get("DisplayName")
		or payload.get("FullyQualifiedName")
		or payload.get("Name")
		or payload.get("Id")
	)


def _latest_raw_payload(entity_type, qbo_id):
	name = frappe.db.get_value(
		"QuickBooks Raw Payload",
		{"qbo_entity_type": entity_type, "qbo_id": str(qbo_id)},
		"name",
		order_by="creation desc",
	)
	return frappe.get_doc("QuickBooks Raw Payload", name) if name else None


def _matching_owned_values(doc, incoming_values: dict):
	return {
		fieldname: value
		for fieldname, value in incoming_values.items()
		if not isinstance(value, list) and _normalize(doc.get(fieldname)) == _normalize(value)
	}


def _map_account(payload, settings):
	parent_account = _qbo_parent_account(payload, settings)
	return "Account", {
		"account_name": payload.get("Name"),
		"company": settings.company,
		"parent_account": parent_account,
		"is_group": 1
		if payload.get("_qbo_has_children") or (payload.get("SubAccount") is False and not parent_account)
		else 0,
		"root_type": _account_root_type(payload.get("AccountType")),
		"account_type": _account_type(payload.get("AccountType")),
	}


def _map_customer(payload, settings):
	return "Customer", {
		"customer_name": _display_name(payload),
		"customer_type": "Company" if payload.get("CompanyName") else "Individual",
		"customer_group": _default_group("Customer Group", "All Customer Groups"),
		"territory": _default_group("Territory", "All Territories"),
	}


def _map_supplier(payload, settings):
	return "Supplier", {
		"supplier_name": _display_name(payload),
		"supplier_type": "Company" if payload.get("CompanyName") else "Individual",
		"supplier_group": _default_group("Supplier Group", "All Supplier Groups"),
	}


def _map_item(payload, settings):
	return "Item", {
		"item_code": payload.get("Sku") or payload.get("Name") or payload.get("Id"),
		"item_name": payload.get("Name"),
		"description": payload.get("Description"),
		"item_group": _default_or_none("Item Group", "All Item Groups"),
		"stock_uom": _default_or_none("UOM", "Nos"),
		"is_stock_item": 0,
	}


def _map_sales_invoice(payload, settings):
	return "Sales Invoice", {
		"company": settings.company,
		"customer": _linked_name("Customer", "Customer", payload.get("CustomerRef", {}).get("value")),
		"posting_date": payload.get("TxnDate"),
		"set_posting_time": 1,
		"items": _sales_items(payload),
		"remarks": f"Imported from QuickBooks Online Invoice {payload.get('DocNumber') or payload.get('Id')}",
	}


def _map_purchase_invoice(payload, settings):
	return "Purchase Invoice", {
		"company": settings.company,
		"supplier": _linked_name("Vendor", "Supplier", payload.get("VendorRef", {}).get("value")),
		"posting_date": payload.get("TxnDate"),
		"set_posting_time": 1,
		"items": _purchase_items(payload),
		"remarks": f"Imported from QuickBooks Online Bill {payload.get('DocNumber') or payload.get('Id')}",
	}


def _map_payment_entry(payload, settings):
	party_type, party = _payment_party(payload)
	if not party_type or not party:
		return None, {}
	return "Payment Entry", {
		"company": settings.company,
		"posting_date": payload.get("TxnDate"),
		"payment_type": "Receive",
		"party_type": party_type,
		"party": party,
		"remarks": f"Imported from QuickBooks Online payment/deposit {payload.get('Id')}",
	}


def _map_journal_entry(payload, settings):
	return "Journal Entry", {
		"company": settings.company,
		"posting_date": payload.get("TxnDate"),
		"accounts": _journal_accounts(payload),
		"remark": f"Imported from QuickBooks Online Journal Entry {payload.get('DocNumber') or payload.get('Id')}",
	}


def _map_quotation(payload, settings):
	return "Quotation", {
		"company": settings.company,
		"quotation_to": "Customer",
		"party_name": _linked_name("Customer", "Customer", payload.get("CustomerRef", {}).get("value")),
		"transaction_date": payload.get("TxnDate"),
		"items": _sales_items(payload),
	}


def _map_purchase_order(payload, settings):
	return "Purchase Order", {
		"company": settings.company,
		"supplier": _linked_name("Vendor", "Supplier", payload.get("VendorRef", {}).get("value")),
		"transaction_date": payload.get("TxnDate"),
		"items": _purchase_items(payload),
	}


def _map_tax_code(payload, settings):
	return "Account", {
		"account_name": payload.get("Name") or f"QBO TaxCode {payload.get('Id')}",
		"company": settings.company,
		"parent_account": _root_account_for_type("Liability", settings),
		"is_group": 0,
		"root_type": "Liability",
		"account_type": "Tax",
	}


def _linked_name(qbo_entity_type: str, erpnext_doctype: str, qbo_id: str | None):
	if not qbo_id:
		return None
	return frappe.db.get_value(
		"QuickBooks Sync Mapping",
		{"qbo_entity_type": qbo_entity_type, "qbo_id": str(qbo_id), "erpnext_doctype": erpnext_doctype},
		"erpnext_name",
	)


def _default_or_none(doctype: str, name: str):
	return name if frappe.db.exists(doctype, name) else None


def _default_group(doctype: str, fallback_name: str):
	name = frappe.db.get_value(doctype, {"is_group": 0}, "name")
	if name:
		return name
	return None


def _qbo_parent_account(payload, settings):
	parent_ref = payload.get("ParentRef") or {}
	parent_qbo_id = parent_ref.get("value")
	if parent_qbo_id:
		parent = _linked_name("Account", "Account", parent_qbo_id)
		if parent:
			return parent
	return _root_account_for_type(_account_root_type(payload.get("AccountType")), settings)


def _root_account_for_type(root_type, settings):
	if not root_type:
		return None
	accounts = frappe.get_all(
		"Account",
		filters={"company": settings.company, "is_group": 1, "root_type": root_type},
		fields=["name"],
		limit_page_length=1,
	)
	return accounts[0].name if accounts else None


def _payment_party(payload):
	customer = _linked_name("Customer", "Customer", (payload.get("CustomerRef") or {}).get("value"))
	if customer:
		return "Customer", customer
	vendor = _linked_name("Vendor", "Supplier", (payload.get("VendorRef") or {}).get("value"))
	if vendor:
		return "Supplier", vendor
	return None, None


def _account_root_type(qbo_account_type):
	root_type_map = {
		"Bank": "Asset",
		"Accounts Receivable": "Asset",
		"Fixed Asset": "Asset",
		"Other Current Asset": "Asset",
		"Other Asset": "Asset",
		"Accounts Payable": "Liability",
		"Credit Card": "Liability",
		"Other Current Liability": "Liability",
		"Long Term Liability": "Liability",
		"Equity": "Equity",
		"Income": "Income",
		"Other Income": "Income",
		"Expense": "Expense",
		"Other Expense": "Expense",
		"Cost of Goods Sold": "Expense",
	}
	return root_type_map.get(qbo_account_type)


def _account_type(qbo_account_type):
	account_type_map = {
		"Bank": "Bank",
		"Accounts Receivable": "Receivable",
		"Accounts Payable": "Payable",
		"Credit Card": "Payable",
		"Fixed Asset": "Fixed Asset",
		"Expense": "Expense Account",
		"Other Expense": "Expense Account",
		"Income": "Income Account",
		"Other Income": "Income Account",
	}
	return account_type_map.get(qbo_account_type)


def _match_account(payload, settings):
	return _single_or_ambiguous(
		"Account",
		{"account_name": payload.get("Name"), "company": settings.company},
		"account_name + company",
		payload,
		confidence=95,
	)


def _match_tax_code(payload, settings):
	return _single_or_ambiguous(
		"Account",
		{
			"account_name": payload.get("Name") or f"QBO TaxCode {payload.get('Id')}",
			"company": settings.company,
		},
		"tax account name + company",
		payload,
		confidence=90,
	)


def _match_customer(payload, settings):
	name = _display_name(payload)
	email = (payload.get("PrimaryEmailAddr") or {}).get("Address")
	for filters, rule, confidence in [
		({"customer_name": name}, "customer_name", 95),
		({"customer_name": payload.get("CompanyName")}, "company_name", 90),
	]:
		match = _single_or_ambiguous("Customer", filters, rule, payload, confidence=confidence)
		if match:
			return match
	if email and _has_field("Customer", "email_id"):
		return _single_or_ambiguous("Customer", {"email_id": email}, "email_id", payload, confidence=85)
	return None


def _match_supplier(payload, settings):
	name = _display_name(payload)
	email = (payload.get("PrimaryEmailAddr") or {}).get("Address")
	for filters, rule, confidence in [
		({"supplier_name": name}, "supplier_name", 95),
		({"supplier_name": payload.get("CompanyName")}, "company_name", 90),
	]:
		match = _single_or_ambiguous("Supplier", filters, rule, payload, confidence=confidence)
		if match:
			return match
	if email and _has_field("Supplier", "email_id"):
		return _single_or_ambiguous("Supplier", {"email_id": email}, "email_id", payload, confidence=85)
	return None


def _match_item(payload, settings):
	sku = payload.get("Sku")
	name = payload.get("Name")
	for filters, rule, confidence in [
		({"item_code": sku}, "item_code/SKU", 98),
		({"item_name": name}, "item_name", 90),
	]:
		match = _single_or_ambiguous("Item", filters, rule, payload, confidence=confidence)
		if match:
			return match
	return None


def _single_or_ambiguous(doctype, filters, rule, payload, confidence):
	filters = {key: value for key, value in filters.items() if value not in (None, "")}
	if not filters:
		return None
	candidates = frappe.get_all(doctype, filters=filters, fields=["name"], limit_page_length=5)
	if len(candidates) == 1:
		return {
			"status": "matched",
			"name": candidates[0].name,
			"rule": rule,
			"confidence": confidence,
		}
	if len(candidates) > 1:
		return {
			"status": "ambiguous",
			"reason": rule,
			"candidates": [{"doctype": doctype, "name": candidate.name} for candidate in candidates],
			"qbo_name": _display_name(payload),
		}
	return None


def _has_field(doctype, fieldname):
	try:
		return frappe.get_meta(doctype).has_field(fieldname)
	except Exception:
		return False


def _sales_items(payload):
	items = []
	for line in payload.get("Line", []) or []:
		detail = line.get("SalesItemLineDetail") or {}
		item_ref = detail.get("ItemRef") or {}
		item_code = _linked_name("Item", "Item", item_ref.get("value"))
		if not item_code:
			continue
		items.append(
			{
				"item_code": item_code,
				"description": line.get("Description") or item_ref.get("name"),
				"qty": detail.get("Qty") or 1,
				"rate": detail.get("UnitPrice") or line.get("Amount") or 0,
				"amount": line.get("Amount") or 0,
			}
		)
	return items


def _purchase_items(payload):
	items = []
	for line in payload.get("Line", []) or []:
		detail = line.get("ItemBasedExpenseLineDetail") or {}
		item_ref = detail.get("ItemRef") or {}
		item_code = _linked_name("Item", "Item", item_ref.get("value"))
		if not item_code:
			continue
		items.append(
			{
				"item_code": item_code,
				"description": line.get("Description") or item_ref.get("name"),
				"qty": detail.get("Qty") or 1,
				"rate": detail.get("UnitPrice") or line.get("Amount") or 0,
				"amount": line.get("Amount") or 0,
			}
		)
	return items


def _journal_accounts(payload):
	accounts = []
	for line in payload.get("Line", []) or []:
		detail = line.get("JournalEntryLineDetail") or {}
		account_ref = detail.get("AccountRef") or {}
		account = _linked_name("Account", "Account", account_ref.get("value"))
		if not account:
			continue
		amount = line.get("Amount") or 0
		posting_type = detail.get("PostingType")
		accounts.append(
			{
				"account": account,
				"debit_in_account_currency": amount if posting_type == "Debit" else 0,
				"credit_in_account_currency": amount if posting_type == "Credit" else 0,
			}
		)
	return accounts
