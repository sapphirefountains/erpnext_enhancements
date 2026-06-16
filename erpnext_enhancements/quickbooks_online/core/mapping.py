"""Entity mapping, matching and idempotent upsert between QBO and ERPNext.

The transform-and-persist heart of the integration, called by ``sync.py`` for
every fetched/received QBO record. Responsibilities:

  * Translate a QBO payload into an ERPNext DocType + field values
    (``map_qbo_to_erpnext`` and the ``_map_*`` functions).
  * Decide what to do with it idempotently via the ``QuickBooks Sync Mapping``
    ledger (``upsert_entity``): update an already-linked record, auto-link an
    existing ERPNext record by fuzzy match (``find_existing_match`` / ``_match_*``),
    create a new one, or defer to manual review on missing-required-field or
    ambiguous-match conditions.
  * Track ownership of QBO-sourced fields so subsequent syncs can detect
    user-made conflicts (``detect_conflicts``, ``owned_fields``) and respect them
    unless an overwrite resync is requested.

The mapping ledger keyed on (qbo_entity_type, qbo_id) is what makes repeated
imports/webhooks/CDC safe to re-run.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from erpnext_enhancements.quickbooks_online.core.constants import ENTITY_DOCTYPE_MAP
from erpnext_enhancements.quickbooks_online.core.utils import (
	json_dumps,
	json_loads,
	parse_qbo_datetime,
)


def get_erpnext_doctype(entity_type: str) -> str | None:
	"""Return the ERPNext DocType a QBO entity maps to, or None if unmapped."""
	return ENTITY_DOCTYPE_MAP.get(entity_type)


def map_qbo_to_erpnext(entity_type: str, payload: dict, settings) -> tuple[str | None, dict]:
	"""Dispatch a QBO payload to its per-entity mapper.

	Returns ``(erpnext_doctype, values)`` -- the target DocType and a dict of
	ERPNext field values -- or ``(None, {})`` when the entity type has no mapper.
	Pure transform; performs no writes (though some mappers read existing
	mappings/defaults from the DB to resolve references).
	"""
	mappers = {
		"Account": _map_account,
		"Customer": _map_customer,
		"Vendor": _map_supplier,
		"Item": _map_item,
		"Invoice": _map_sales_invoice,
		"SalesReceipt": _map_sales_receipt,
		"Bill": _map_purchase_invoice,
		"Payment": _map_payment_entry,
		"JournalEntry": _map_journal_entry,
		"Estimate": _map_quotation,
		"PurchaseOrder": _map_purchase_order,
		"Purchase": _map_purchase,
		"Transfer": _map_transfer,
		"BillPayment": _map_bill_payment,
		"CreditCardPayment": _map_credit_card_payment,
		"VendorCredit": _map_vendor_credit,
		"Deposit": _map_deposit,
		"TaxCode": _map_tax_code,
	}
	mapper = mappers.get(entity_type)
	if not mapper:
		return None, {}
	return mapper(payload, settings)


def upsert_entity(entity_type: str, payload: dict, settings, *, overwrite=False, preview=False):
	"""Idempotently create/update/link an ERPNext record from a QBO payload.

	The core decision tree, driven by the (entity_type, qbo_id) mapping ledger:

	  1. No mapper / no Id          -> ``skipped``.
	  2. Preflight validation fails -> ``manual_review`` (records a pending mapping).
	  3. Already mapped & exists    -> update it, unless user edits collide with
	     QBO-owned values and ``overwrite`` is False -> ``conflict``.
	  4. Unmapped but a fuzzy match exists -> auto-link (fill only blank fields);
	     multiple candidates -> ``manual_review``.
	  5. Otherwise                  -> create a new record (after required-field check).

	``preview=True`` computes the would-be action without writing (verbs become
	create/update/link/delete). ``overwrite=True`` lets QBO win conflicts.
	Side effects (non-preview): ERPNext doc insert/save and Sync Mapping writes.
	Returns an action dict consumed by ``sync._track_result``.
	"""
	erpnext_doctype, values = map_qbo_to_erpnext(entity_type, payload, settings)
	if not erpnext_doctype:
		return {"action": "skipped", "reason": "No native ERPNext mapping"}

	qbo_id = str(payload.get("Id"))
	if not qbo_id:
		return {"action": "skipped", "reason": "QBO payload has no Id"}

	# Preflight: only the fields we actually mapped (don't yet enforce all
	# DocType-required fields -- a later create-time check does that).
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

	# A child Account can't be written under a ledger parent, so promote the
	# parent to a group first (no-op for other doctypes / group parents).
	if not preview:
		_ensure_group_parent(erpnext_doctype, values)

	# Already-linked path: update the previously synced ERPNext record in place.
	mapping = get_mapping(entity_type, qbo_id)
	if mapping and mapping.erpnext_name and frappe.db.exists(erpnext_doctype, mapping.erpnext_name):
		doc = frappe.get_doc(erpnext_doctype, mapping.erpnext_name)
		conflicts = detect_conflicts(doc, values, mapping)
		# A conflict = a user changed a QBO-owned field; respect it unless overwriting.
		if conflicts and not overwrite:
			if not preview:
				mapping.conflict_status = "Conflict"
				mapping.save(ignore_permissions=True)
			return {"action": "conflict", "doctype": erpnext_doctype, "name": doc.name, "fields": conflicts}
		if preview:
			return {"action": "update", "doctype": erpnext_doctype, "name": doc.name, "fields": list(values)}
		apply_values(doc, values)
		if _clear_account_type_for_group_conversion(erpnext_doctype, doc):
			values.pop("account_type", None)
		doc.save(ignore_permissions=True)
		save_mapping(entity_type, qbo_id, payload, erpnext_doctype, doc.name, values, conflict_status="Clean")
		return {"action": "updated", "doctype": erpnext_doctype, "name": doc.name}

	# Not yet linked: try to attach to a pre-existing ERPNext record by fuzzy match.
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
		# A QBO group account auto-linked to a pre-existing ledger must become a
		# group or its children can never nest under it (apply_blank_values
		# treats the existing 0 as a value and leaves it).
		if erpnext_doctype == "Account" and values.get("is_group") and not doc.get("is_group"):
			doc.is_group = 1
			applied_values["is_group"] = 1
		if _clear_account_type_for_group_conversion(erpnext_doctype, doc):
			applied_values.pop("account_type", None)
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
	"""Manually link a QBO entity to a chosen ERPNext record (dashboard action).

	Backs the "Link Existing Records" dialog. Reads the latest stored raw payload
	for the entity (sync/preview must have run first), validates the chosen
	DocType matches the entity's expected mapping, and writes a "Manual Matched"
	Sync Mapping. With ``apply_qbo_data`` it also fills the record's blank fields
	from QBO data. Side effects: optional doc save, mapping write, commit. Returns
	the mapping name. Raises if the record or a raw payload is missing.
	"""
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
	"""Suggest ERPNext records to link for as-yet-unmapped QBO master entities.

	Scans recent ``QuickBooks Raw Payload`` rows (master entities by default),
	skips any already mapped, and for each runs ``find_existing_match`` to propose
	a candidate. Read-only; returns a list the dashboard renders in the "Link
	Existing Records" dialog.
	"""
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
	"""Flag a mapping as deleted when QBO reports the entity removed (via CDC).

	Soft delete: sets the mapping's ``deleted`` flag rather than touching the
	linked ERPNext document. Called from ``sync.run_cdc`` for "Deleted" payloads.
	"""
	mapping = get_mapping(entity_type, qbo_id)
	if preview:
		return {"action": "delete", "mapping": mapping.name if mapping else None}
	if mapping:
		mapping.deleted = 1
		mapping.conflict_status = "Clean"
		mapping.save(ignore_permissions=True)
	return {"action": "deleted"}


def get_mapping(entity_type: str, qbo_id: str):
	"""Fetch the ``QuickBooks Sync Mapping`` for (entity_type, qbo_id), or None."""
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
	"""Create or update the ledger row linking a QBO entity to an ERPNext record.

	Records the QBO ``SyncToken`` and ``LastUpdatedTime`` (concurrency/cursor
	metadata), the sync timestamp, and ``owned_fields`` -- the JSON snapshot of
	QBO-sourced values used later by ``detect_conflicts``. ``**extra`` sets
	match_status/match_rule/conflict_status etc. Upserts by (entity_type, qbo_id).
	"""
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
	"""Persist a "Pending Review" mapping for an ambiguous fuzzy match.

	Stores the competing candidate records (in ``owned_fields``) so a human can
	resolve the link from the dashboard rather than the sync guessing wrong.
	"""
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
	"""Persist a "Pending Review" mapping for a record that failed validation.

	Used when required fields are missing (or a stock-account rule is violated);
	the failing ``issues`` are stored so the record can be triaged instead of
	silently dropped.
	"""
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
	"""Return a list of blocking issues, or empty if the mapped values are insertable.

	Checks required fields (entity-specific plus, when ``include_doc_required``,
	the DocType's own reqd fields) and the QBO->ERPNext quirk that journal lines
	posting to Stock accounts can't be booked via a plain Journal Entry. A
	non-empty list routes the record to manual review.
	"""
	issues = []
	for fieldname in sorted(_required_mapped_fields(entity_type, erpnext_doctype, values, include_doc_required)):
		if _is_empty_required_value(values.get(fieldname)):
			issues.append(f"Missing required field: {fieldname}")
	for account in _blocked_stock_accounts(entity_type, values):
		issues.append(f"Stock account requires a stock transaction: {account}")
	imbalance = _journal_imbalance(entity_type, values)
	if imbalance:
		issues.append(imbalance)
	return issues


def _required_mapped_fields(
	entity_type: str, erpnext_doctype: str, values: dict, include_doc_required: bool = True
) -> set[str]:
	"""Compute the set of field names that must be non-empty for this entity.

	Combines a hardcoded per-entity baseline with the DocType's own required
	fields (when ``include_doc_required``), but only those we can meaningfully
	check pre-insert (see ``_can_validate_required_field``).
	"""
	fields = {
		"Invoice": {"company", "customer", "items"},
		"SalesReceipt": {"company", "customer", "items"},
		"Bill": {"company", "supplier", "items"},
		"Estimate": {"company", "party_name", "items"},
		"PurchaseOrder": {"company", "supplier", "items"},
		"JournalEntry": {"company", "accounts"},
		# Cash-movement types mapped to Journal Entries: an empty accounts table
		# means none of their account references resolved -> manual review rather
		# than an opaque insert failure.
		"Purchase": {"company", "accounts"},
		"Transfer": {"company", "accounts"},
		"BillPayment": {"company", "accounts"},
		"CreditCardPayment": {"company", "accounts"},
		"VendorCredit": {"company", "accounts"},
		"Deposit": {"company", "accounts"},
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
	"""Whether a DocType required field is one we can pre-validate.

	True if we mapped it. Otherwise we skip the fields ERPNext fills for us on
	insert, so a record is not parked for manual review over values its
	controller computes anyway:

	  * ``naming_series`` -- assigned by autoname.
	  * ``read_only``     -- computed totals (grand_total, base_*_amount, ...).
	  * ``fetch_from``    -- pulled from a linked doc (e.g. account currency).
	  * has a ``default`` -- ERPNext applies the field default.

	Everything else is checked, but only for data-bearing field types (layout/
	virtual types are ignored).
	"""
	if df.fieldname in values:
		return True
	if df.fieldname == "naming_series":
		return False
	if getattr(df, "read_only", 0):
		return False
	if getattr(df, "fetch_from", None):
		return False
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
	"""True for None, empty string, or empty list (e.g. an items/accounts table)."""
	return value in (None, "") or (isinstance(value, list) and not value)


def _blocked_stock_accounts(entity_type: str, values: dict) -> list[str]:
	"""List Stock accounts referenced by a Journal Entry's lines.

	ERPNext forbids posting to a Stock-type account through a Journal Entry
	(stock value must come from a stock transaction), so any such account is a
	validation blocker.
	"""
	if entity_type != "JournalEntry":
		return []
	stock_accounts = []
	for row in values.get("accounts") or []:
		account = row.get("account")
		if account and frappe.db.get_value("Account", account, "account_type") == "Stock":
			stock_accounts.append(account)
	return stock_accounts


def _journal_imbalance(entity_type: str, values: dict) -> str | None:
	"""Return an issue string if a Journal Entry's debits and credits don't match.

	Applies to every entity mapped onto a Journal Entry (native JournalEntry plus
	the cash-movement types: Purchase/Transfer/BillPayment/Deposit/...). A lopsided
	total almost always means a line referenced a QBO account that isn't mapped
	into ERPNext yet (e.g. an inactive account that wasn't imported), so it is
	routed to manual review with a clear reason instead of failing on insert with
	ERPNext's opaque "Total Debit must equal Total Credit" error.
	"""
	if get_erpnext_doctype(entity_type) != "Journal Entry":
		return None
	rows = values.get("accounts") or []
	if not rows:
		return None
	debit = sum(_to_amount(row.get("debit_in_account_currency")) for row in rows)
	credit = sum(_to_amount(row.get("credit_in_account_currency")) for row in rows)
	if round(debit - credit, 2) == 0:
		return None
	return (
		f"Journal Entry is unbalanced (debit {debit:.2f} vs credit {credit:.2f}); "
		"some lines may reference QuickBooks accounts not yet imported into ERPNext."
	)


def detect_conflicts(doc, incoming_values: dict, mapping) -> list[str]:
	"""Return field names a user changed away from their last QBO-synced value.

	Compares the current ERPNext value against the last value QBO owned
	(``mapping.owned_fields``). A field conflicts only if it differs from BOTH
	the previously-synced value (proving a local edit) AND the incoming QBO value
	(proving the edit isn't just QBO catching up) -- so a true three-way divergence.
	"""
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
	"""Set every non-None mapped value on the doc (full overwrite of mapped fields)."""
	for fieldname, value in values.items():
		if value is not None:
			doc.set(fieldname, value)


def apply_blank_values(doc, values: dict) -> dict:
	"""Fill only currently-empty scalar fields on the doc; return what was set.

	Non-destructive merge used when auto/manual-linking an existing record: child
	tables (lists) and already-populated fields are left untouched so existing
	ERPNext data wins.
	"""
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
	"""Find a pre-existing ERPNext record to link a master entity to.

	Dispatches to a per-entity matcher (only master types: Account/Customer/
	Vendor/Item/TaxCode). Returns a match dict (matched/ambiguous) or None.
	Transactions are never auto-matched -- they are always created fresh.
	"""
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
	"""Coerce a value to a string for tolerant equality comparison (None -> "")."""
	return "" if value is None else str(value)


def _display_name(payload):
	"""Best human-readable label for a QBO record.

	``FullyQualifiedName`` is preferred over ``DisplayName`` so QBO sub-customers
	and sub-vendors (jobs like ``Landmark Aquatics:100023 - Daybreak Splash Pad``)
	keep their parent context and stay unique -- a bare ``DisplayName`` is only
	the leaf and can collide across different parents. For top-level records the
	two are identical, so this is a no-op there.
	"""
	return (
		payload.get("FullyQualifiedName")
		or payload.get("DisplayName")
		or payload.get("Name")
		or payload.get("Id")
	)


def _latest_raw_payload(entity_type, qbo_id):
	"""Return the most recent stored raw payload doc for an entity, or None."""
	name = frappe.db.get_value(
		"QuickBooks Raw Payload",
		{"qbo_entity_type": entity_type, "qbo_id": str(qbo_id)},
		"name",
		order_by="creation desc",
	)
	return frappe.get_doc("QuickBooks Raw Payload", name) if name else None


def _matching_owned_values(doc, incoming_values: dict):
	"""Subset of mapped values that already equal the doc's current values.

	When linking without filling blanks, this is recorded as the mapping's
	``owned_fields`` so future conflict detection only guards fields QBO and
	ERPNext already agree on.
	"""
	return {
		fieldname: value
		for fieldname, value in incoming_values.items()
		if not isinstance(value, list) and _normalize(doc.get(fieldname)) == _normalize(value)
	}


# ---------------------------------------------------------------------------
# Per-entity mappers: QBO payload -> (ERPNext DocType, field-value dict).
# Each returns the tuple consumed by map_qbo_to_erpnext. References to other
# QBO entities (CustomerRef, ItemRef, AccountRef, ...) are resolved to ERPNext
# names via _linked_name, i.e. they require the referenced entity to be mapped
# already (hence the master-before-transaction import order).
# ---------------------------------------------------------------------------


def _map_account(payload, settings):
	"""Map a QBO Account to an ERPNext Account (root/type derived from AccountType).

	Group accounts get no ``account_type``: they never receive postings, and a
	set Account Type blocks ERPNext's ledger->group conversion when a linked
	leaf later turns out to have children.
	"""
	parent_account = _qbo_parent_account(payload, settings)
	is_group = (
		1
		if payload.get("_qbo_has_children") or (payload.get("SubAccount") is False and not parent_account)
		else 0
	)
	return "Account", {
		"account_name": payload.get("Name"),
		"company": settings.company,
		"parent_account": parent_account,
		"is_group": is_group,
		"root_type": _account_root_type(payload.get("AccountType")),
		"account_type": None if is_group else _account_type(payload.get("AccountType")),
	}


def _map_customer(payload, settings):
	"""Map a QBO Customer to an ERPNext Customer (company vs individual by CompanyName).

	``customer_type`` is resolved against the field's actual Select options --
	sites customize them via Property Setter (e.g. Commercial/Residential/
	Partnership), and an option not in the list fails validation on insert.
	"""
	return "Customer", {
		"customer_name": _display_name(payload),
		"customer_type": _select_option(
			"Customer",
			"customer_type",
			("Company", "Commercial") if payload.get("CompanyName") else ("Individual", "Residential"),
		),
		"customer_group": _default_group("Customer Group", "All Customer Groups"),
		"territory": _default_group("Territory", "All Territories"),
	}


def _map_supplier(payload, settings):
	"""Map a QBO Vendor to an ERPNext Supplier."""
	return "Supplier", {
		"supplier_name": _display_name(payload),
		"supplier_type": _select_option(
			"Supplier",
			"supplier_type",
			("Company", "Commercial") if payload.get("CompanyName") else ("Individual", "Residential"),
		),
		"supplier_group": _default_group("Supplier Group", "All Supplier Groups"),
	}


def _map_item(payload, settings):
	"""Map a QBO Item to an ERPNext Item (non-stock; item_code from SKU/Name/Id)."""
	return "Item", {
		"item_code": payload.get("Sku") or payload.get("Name") or payload.get("Id"),
		"item_name": payload.get("Name"),
		"description": payload.get("Description"),
		"item_group": _default_or_none("Item Group", "All Item Groups"),
		"stock_uom": _default_or_none("UOM", "Nos"),
		"is_stock_item": 0,
	}


def _map_sales_invoice(payload, settings):
	"""Map a QBO Invoice to an ERPNext Sales Invoice (customer + line items).

	Sets the currency/exchange and receivable account ERPNext otherwise can't
	infer from the minimal payload, so the invoice is insertable rather than
	parked for missing required fields. ``base_*``/``grand_total`` are left for
	ERPNext to compute.
	"""
	currency, rate = _txn_currency(payload, settings)
	return "Sales Invoice", {
		"company": settings.company,
		"customer": _linked_name("Customer", "Customer", (payload.get("CustomerRef") or {}).get("value")),
		"posting_date": payload.get("TxnDate"),
		"set_posting_time": 1,
		"currency": currency,
		"conversion_rate": rate,
		"debit_to": _company_value(settings, "default_receivable_account"),
		"selling_price_list": _default_price_list(selling=True),
		"price_list_currency": currency,
		"plc_conversion_rate": 1,
		"items": _sales_items(payload),
		"remarks": f"Imported from QuickBooks Online Invoice {payload.get('DocNumber') or payload.get('Id')}",
	}


def _map_sales_receipt(payload, settings):
	"""Map a QBO SalesReceipt to an ERPNext Sales Invoice.

	A Sales Receipt is an invoice paid at the point of sale; it is imported as a
	Sales Invoice (so revenue and the item income accounts post correctly). The
	cash side is not linked here -- see the migration notes on reconciling the
	deposit account.
	"""
	doctype, values = _map_sales_invoice(payload, settings)
	values["remarks"] = (
		f"Imported from QuickBooks Online Sales Receipt {payload.get('DocNumber') or payload.get('Id')}"
	)
	return doctype, values


def _map_purchase_invoice(payload, settings):
	"""Map a QBO Bill to an ERPNext Purchase Invoice (supplier + line items).

	Sets the payable account (``credit_to``) and currency/exchange ERPNext can't
	infer from the payload so the bill is insertable.
	"""
	currency, rate = _txn_currency(payload, settings)
	return "Purchase Invoice", {
		"company": settings.company,
		"supplier": _linked_name("Vendor", "Supplier", (payload.get("VendorRef") or {}).get("value")),
		"posting_date": payload.get("TxnDate"),
		"set_posting_time": 1,
		"currency": currency,
		"conversion_rate": rate,
		"credit_to": _company_value(settings, "default_payable_account"),
		"items": _purchase_items(payload),
		"remarks": f"Imported from QuickBooks Online Bill {payload.get('DocNumber') or payload.get('Id')}",
	}


def _map_payment_entry(payload, settings):
	"""Map a QBO Payment to an ERPNext Payment Entry.

	Returns ``(None, {})`` if the referenced party (Customer or Vendor) is not yet
	mapped, since a Payment Entry requires a party. Populates the bank/party
	accounts and amounts ERPNext requires: a customer Payment is a "Receive"
	(debit the company's default bank/cash, credit the receivable); a vendor
	Payment is a "Pay" (the reverse). Single-currency exchange rates are 1.
	"""
	party_type, party = _payment_party(payload)
	if not party_type or not party:
		return None, {}
	currency, rate = _txn_currency(payload, settings)
	amount = _to_amount(payload.get("TotalAmt"))
	bank = _company_value(settings, "default_bank_account") or _company_value(settings, "default_cash_account")
	if party_type == "Customer":
		payment_type, paid_from, paid_to = "Receive", _company_value(settings, "default_receivable_account"), bank
	else:
		payment_type, paid_from, paid_to = "Pay", bank, _company_value(settings, "default_payable_account")
	return "Payment Entry", {
		"company": settings.company,
		"posting_date": payload.get("TxnDate"),
		"payment_type": payment_type,
		"party_type": party_type,
		"party": party,
		"paid_from": paid_from,
		"paid_to": paid_to,
		"paid_amount": amount,
		"received_amount": amount,
		"source_exchange_rate": rate,
		"target_exchange_rate": rate,
		"remarks": f"Imported from QuickBooks Online payment {payload.get('Id')}",
	}


def _map_journal_entry(payload, settings):
	"""Map a QBO JournalEntry to an ERPNext Journal Entry (debit/credit lines)."""
	return _journal_entry_doc(
		settings,
		payload.get("TxnDate"),
		_journal_accounts(payload),
		f"Imported from QuickBooks Online Journal Entry {payload.get('DocNumber') or payload.get('Id')}",
	)


def _map_purchase(payload, settings):
	"""Map a QBO Purchase (Expense / Check / Credit Card charge) to a Journal Entry.

	A normal purchase credits the funding account (bank or credit card,
	``AccountRef``) and debits each expense line's account; a ``Credit`` (refund /
	credit-card credit) reverses both sides. Item-based lines are skipped -- their
	GL account lives on the Item, not the line -- which the balance guard catches.
	"""
	is_credit = bool(payload.get("Credit"))
	total = _to_amount(payload.get("TotalAmt"))
	accounts = []
	source = _ledger_line(
		_resolve_account(settings, (payload.get("AccountRef") or {}).get("value")),
		debit=total if is_credit else 0,
		credit=0 if is_credit else total,
	)
	if source:
		accounts.append(source)
	for line in payload.get("Line") or []:
		detail = line.get("AccountBasedExpenseLineDetail") or {}
		amount = _to_amount(line.get("Amount"))
		row = _ledger_line(
			_resolve_account(settings, (detail.get("AccountRef") or {}).get("value")),
			debit=0 if is_credit else amount,
			credit=amount if is_credit else 0,
		)
		if row:
			accounts.append(row)
	label = payload.get("PaymentType") or "Purchase"
	return _journal_entry_doc(
		settings,
		payload.get("TxnDate"),
		accounts,
		f"Imported from QuickBooks Online {label} {payload.get('DocNumber') or payload.get('Id')}",
	)


def _map_transfer(payload, settings):
	"""Map a QBO Transfer to a Journal Entry (debit the destination, credit the source)."""
	amount = _to_amount(payload.get("Amount"))
	accounts = [
		_ledger_line(_resolve_account(settings, (payload.get("ToAccountRef") or {}).get("value")), debit=amount),
		_ledger_line(_resolve_account(settings, (payload.get("FromAccountRef") or {}).get("value")), credit=amount),
	]
	return _journal_entry_doc(
		settings,
		payload.get("TxnDate"),
		[row for row in accounts if row],
		f"Imported from QuickBooks Online Transfer {payload.get('Id')}",
	)


def _map_bill_payment(payload, settings):
	"""Map a QBO BillPayment to a Journal Entry (debit A/P, credit the funding account).

	The funds come from a bank account (Check) or a credit card (CreditCard); the
	A/P account falls back to the company default when the payload omits it.
	"""
	total = _to_amount(payload.get("TotalAmt"))
	funding_ref = (
		(payload.get("CheckPayment") or {}).get("BankAccountRef")
		or (payload.get("CreditCardPayment") or {}).get("CCAccountRef")
		or {}
	).get("value")
	accounts = [
		_ledger_line(
			_resolve_account(settings, (payload.get("APAccountRef") or {}).get("value"), "default_payable_account"),
			debit=total,
		),
		_ledger_line(_resolve_account(settings, funding_ref), credit=total),
	]
	return _journal_entry_doc(
		settings,
		payload.get("TxnDate"),
		[row for row in accounts if row],
		f"Imported from QuickBooks Online Bill Payment {payload.get('DocNumber') or payload.get('Id')}",
	)


def _map_credit_card_payment(payload, settings):
	"""Map a QBO CreditCardPayment to a Journal Entry (debit the card, credit the bank)."""
	amount = _to_amount(payload.get("Amount") or payload.get("TotalAmt"))
	accounts = [
		_ledger_line(
			_resolve_account(settings, (payload.get("CreditCardAccountRef") or {}).get("value")), debit=amount
		),
		_ledger_line(_resolve_account(settings, (payload.get("BankAccountRef") or {}).get("value")), credit=amount),
	]
	return _journal_entry_doc(
		settings,
		payload.get("TxnDate"),
		[row for row in accounts if row],
		f"Imported from QuickBooks Online Credit Card Payment {payload.get('Id')}",
	)


def _map_vendor_credit(payload, settings):
	"""Map a QBO VendorCredit to a Journal Entry (debit A/P, credit each expense line)."""
	total = _to_amount(payload.get("TotalAmt"))
	accounts = []
	ap = _ledger_line(
		_resolve_account(settings, (payload.get("APAccountRef") or {}).get("value"), "default_payable_account"),
		debit=total,
	)
	if ap:
		accounts.append(ap)
	for line in payload.get("Line") or []:
		detail = line.get("AccountBasedExpenseLineDetail") or {}
		row = _ledger_line(
			_resolve_account(settings, (detail.get("AccountRef") or {}).get("value")),
			credit=_to_amount(line.get("Amount")),
		)
		if row:
			accounts.append(row)
	return _journal_entry_doc(
		settings,
		payload.get("TxnDate"),
		accounts,
		f"Imported from QuickBooks Online Vendor Credit {payload.get('DocNumber') or payload.get('Id')}",
	)


def _map_deposit(payload, settings):
	"""Map a QBO Deposit to a Journal Entry (debit the deposited-to account, credit sources).

	A bank deposit moves money into ``DepositToAccountRef`` from each line's source
	account (commonly Undeposited Funds), preserving QBO's clearing-account flow.
	"""
	total = _to_amount(payload.get("TotalAmt"))
	accounts = []
	deposit_to = _ledger_line(
		_resolve_account(settings, (payload.get("DepositToAccountRef") or {}).get("value")), debit=total
	)
	if deposit_to:
		accounts.append(deposit_to)
	for line in payload.get("Line") or []:
		detail = line.get("DepositLineDetail") or {}
		row = _ledger_line(
			_resolve_account(settings, (detail.get("AccountRef") or {}).get("value")),
			credit=_to_amount(line.get("Amount")),
		)
		if row:
			accounts.append(row)
	return _journal_entry_doc(
		settings,
		payload.get("TxnDate"),
		accounts,
		f"Imported from QuickBooks Online Deposit {payload.get('Id')}",
	)


def _map_quotation(payload, settings):
	"""Map a QBO Estimate to an ERPNext Quotation (to a Customer)."""
	currency, rate = _txn_currency(payload, settings)
	return "Quotation", {
		"company": settings.company,
		"quotation_to": "Customer",
		"party_name": _linked_name("Customer", "Customer", (payload.get("CustomerRef") or {}).get("value")),
		"transaction_date": payload.get("TxnDate"),
		"currency": currency,
		"conversion_rate": rate,
		"selling_price_list": _default_price_list(selling=True),
		"price_list_currency": currency,
		"plc_conversion_rate": 1,
		"items": _sales_items(payload),
	}


def _map_purchase_order(payload, settings):
	"""Map a QBO PurchaseOrder to an ERPNext Purchase Order (supplier + items)."""
	currency, rate = _txn_currency(payload, settings)
	return "Purchase Order", {
		"company": settings.company,
		"supplier": _linked_name("Vendor", "Supplier", (payload.get("VendorRef") or {}).get("value")),
		"transaction_date": payload.get("TxnDate"),
		"currency": currency,
		"conversion_rate": rate,
		"items": _purchase_items(payload),
	}


def _map_tax_code(payload, settings):
	"""Map a QBO TaxCode to an ERPNext Tax-type Account under Liability."""
	return "Account", {
		"account_name": payload.get("Name") or f"QBO TaxCode {payload.get('Id')}",
		"company": settings.company,
		"parent_account": _root_account_for_type("Liability", settings),
		"is_group": 0,
		"root_type": "Liability",
		"account_type": "Tax",
	}


def _linked_name(qbo_entity_type: str, erpnext_doctype: str, qbo_id: str | None):
	"""Resolve a QBO reference id to the ERPNext record name it was mapped to.

	The bridge that lets transaction mappers point at already-imported masters
	(e.g. an Invoice's CustomerRef -> the ERPNext Customer). Returns None if the
	referenced entity has not been mapped yet.
	"""
	if not qbo_id:
		return None
	return frappe.db.get_value(
		"QuickBooks Sync Mapping",
		{"qbo_entity_type": qbo_entity_type, "qbo_id": str(qbo_id), "erpnext_doctype": erpnext_doctype},
		"erpnext_name",
	)


def _default_or_none(doctype: str, name: str):
	"""Return ``name`` if that record exists in ``doctype``, else None."""
	return name if frappe.db.exists(doctype, name) else None


def _default_group(doctype: str, fallback_name: str):
	"""Return any non-group record of ``doctype`` (a safe default leaf group)."""
	name = frappe.db.get_value(doctype, {"is_group": 0}, "name")
	if name:
		return name
	return None


def _select_option(doctype: str, fieldname: str, preferred):
	"""Return the first ``preferred`` value that is a valid option of a Select field.

	Property Setters can replace a field's options (e.g. Customer.customer_type
	-> Commercial/Residential/Partnership), making the stock value invalid. Falls
	back to the field's first option, else the first preference.
	"""
	try:
		field = frappe.get_meta(doctype).get_field(fieldname)
		options = [option.strip() for option in (field.options or "").split("\n") if option.strip()]
	except Exception:
		options = []
	for value in preferred:
		if value in options:
			return value
	return options[0] if options else preferred[0]


def _to_amount(value):
	"""Coerce a QBO numeric (str/None/number) to a float, defaulting to 0."""
	try:
		return float(value)
	except (TypeError, ValueError):
		return 0.0


def _company_value(settings, fieldname: str):
	"""Read a field off the configured ERPNext Company (e.g. a default account)."""
	if not settings.company:
		return None
	return frappe.db.get_value("Company", settings.company, fieldname)


def _company_currency(settings):
	"""Return the company's default currency (the home currency for postings)."""
	return _company_value(settings, "default_currency")


def _default_price_list(selling: bool = True):
	"""Return an enabled selling/buying Price List name, or None if none exists."""
	field = "selling" if selling else "buying"
	return frappe.db.get_value("Price List", {field: 1, "enabled": 1}, "name")


def _txn_currency(payload, settings):
	"""Resolve a transaction's (currency, conversion_rate) from its QBO CurrencyRef.

	Falls back to the company currency and a rate of 1 -- the common case for a
	single-currency company, where every QBO transaction is in the home currency.
	"""
	currency = (payload.get("CurrencyRef") or {}).get("value") or _company_currency(settings)
	rate = _to_amount(payload.get("ExchangeRate")) or 1
	return currency, rate


def _resolve_account(settings, qbo_id, company_default_field: str | None = None):
	"""Resolve a QBO account reference to an ERPNext Account name.

	Prefers the account the QBO id was mapped to; when the payload omits the ref
	(QBO often leaves A/P implicit) an optional Company default field is used as a
	fallback. Returns None when neither resolves -- the caller's balance guard
	then routes the transaction to manual review.
	"""
	account = _linked_name("Account", "Account", qbo_id)
	if account:
		return account
	if company_default_field:
		return _company_value(settings, company_default_field)
	return None


def _ledger_line(account, debit=0, credit=0):
	"""Build one Journal Entry account row, or None if the account didn't resolve."""
	if not account:
		return None
	return {
		"account": account,
		"debit_in_account_currency": _to_amount(debit),
		"credit_in_account_currency": _to_amount(credit),
	}


def _journal_entry_doc(settings, posting_date, accounts, remark):
	"""Assemble the ``(\"Journal Entry\", values)`` tuple shared by the JE mappers."""
	return "Journal Entry", {
		"company": settings.company,
		"posting_date": posting_date,
		"accounts": accounts,
		"remark": remark,
	}


def _qbo_parent_account(payload, settings):
	"""Resolve an Account's parent: the mapped QBO ParentRef, else the root for its type."""
	parent_ref = payload.get("ParentRef") or {}
	parent_qbo_id = parent_ref.get("value")
	if parent_qbo_id:
		parent = _linked_name("Account", "Account", parent_qbo_id)
		if parent:
			return parent
	return _root_account_for_type(_account_root_type(payload.get("AccountType")), settings)


def _ensure_group_parent(erpnext_doctype: str, values: dict):
	"""Promote a ledger parent Account to a group so a child can be written under it.

	QBO parent accounts auto-linked to pre-existing chart-of-accounts leaves stay
	ledgers, and ERPNext then rejects every child with "Parent account ... can
	not be a ledger". The conversion goes through the Account controller, which
	still blocks parents that already have GL entries (those sync attempts keep
	failing and need manual chart restructuring).
	"""
	if erpnext_doctype != "Account":
		return
	parent_name = values.get("parent_account")
	if not parent_name:
		return
	is_group = frappe.db.get_value("Account", parent_name, "is_group")
	if is_group is None or int(is_group):
		return
	parent = frappe.get_doc("Account", parent_name)
	parent.is_group = 1
	# ERPNext refuses the conversion while an Account Type is set; groups never
	# receive postings, so the type is informational and safe to drop.
	parent.account_type = None
	parent.save(ignore_permissions=True)


def _clear_account_type_for_group_conversion(erpnext_doctype: str, doc) -> bool:
	"""Drop Account Type when an existing ledger Account is being made a group.

	ERPNext blocks the ledger->group conversion while an Account Type is set
	("Cannot covert to Group because Account Type is selected.", account.py's
	``validate_group_or_ledger``). Returns True when it cleared the field so the
	caller can drop ``account_type`` from the QBO-owned values it records.
	"""
	if erpnext_doctype != "Account" or not doc.get("is_group") or not doc.get("account_type"):
		return False
	was_group = frappe.db.get_value("Account", doc.name, "is_group")
	if was_group is None or int(was_group):
		return False
	doc.account_type = None
	return True


def _root_account_for_type(root_type, settings):
	"""Return the company's group account for a root type (Asset/Liability/...)."""
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
	"""Resolve a payment's party: a mapped Customer, else a mapped Vendor, else (None, None)."""
	customer = _linked_name("Customer", "Customer", (payload.get("CustomerRef") or {}).get("value"))
	if customer:
		return "Customer", customer
	vendor = _linked_name("Vendor", "Supplier", (payload.get("VendorRef") or {}).get("value"))
	if vendor:
		return "Supplier", vendor
	return None, None


def _account_root_type(qbo_account_type):
	"""Translate a QBO AccountType to an ERPNext root_type (Asset/Liability/...)."""
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
	"""Translate a QBO AccountType to an ERPNext account_type (Bank/Receivable/...)."""
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
		"Cost of Goods Sold": "Cost of Goods Sold",
	}
	return account_type_map.get(qbo_account_type)


# ---------------------------------------------------------------------------
# Per-entity matchers: locate a pre-existing ERPNext record to link a QBO
# master entity to, returning a {status: matched|ambiguous, ...} dict (with a
# confidence score) or None. Used by find_existing_match during upsert and by
# the dashboard "Link Existing Records" preview.
# ---------------------------------------------------------------------------


def _match_account(payload, settings):
	"""Match a QBO Account by name + company."""
	return _single_or_ambiguous(
		"Account",
		{"account_name": payload.get("Name"), "company": settings.company},
		"account_name + company",
		payload,
		confidence=95,
	)


def _match_tax_code(payload, settings):
	"""Match a QBO TaxCode to an Account by tax-account name + company."""
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
	"""Match a QBO Customer by display name, then company name, then email (descending confidence)."""
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
	"""Match a QBO Vendor by supplier name, then company name, then email."""
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
	"""Match a QBO Item by SKU (item_code), then item name."""
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
	"""Resolve a candidate query to matched (exactly one), ambiguous (>1), or None.

	Drops empty filter values; returns a "matched" dict only on a single hit so
	the sync never auto-links when the lookup is non-unique (those become
	"ambiguous" -> manual review).
	"""
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
	"""Safely report whether a DocType has a given field (False on any error)."""
	try:
		return frappe.get_meta(doctype).has_field(fieldname)
	except Exception:
		return False


def _sales_items(payload):
	"""Build ERPNext sales line items from a QBO transaction's SalesItemLineDetail lines.

	Skips lines whose ItemRef isn't mapped to an ERPNext Item yet.
	"""
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
	"""Build ERPNext purchase line items from QBO ItemBasedExpenseLineDetail lines.

	Skips lines whose ItemRef isn't mapped to an ERPNext Item yet.
	"""
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
	"""Build ERPNext journal lines from QBO JournalEntryLineDetail lines.

	Maps each line's AccountRef to an ERPNext Account (skipping unmapped ones) and
	splits the amount into debit/credit columns based on QBO's PostingType.
	"""
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
