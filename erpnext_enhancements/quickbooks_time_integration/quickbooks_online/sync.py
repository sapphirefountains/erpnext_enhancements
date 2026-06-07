from __future__ import annotations

import json

import frappe
from frappe.utils import add_to_date, now_datetime

from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.client import QuickBooksClient
from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.constants import (
	ACCOUNTING_ENTITIES,
	CDC_ENTITIES,
	MASTER_ENTITIES,
	TRANSACTION_ENTITIES,
)
from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.mapping import (
	mark_deleted,
	upsert_entity,
)
from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.utils import get_settings, json_dumps


def import_all(entity_types=None):
	settings = get_settings()
	ensure_connected(settings)
	log = start_log("Import All")
	try:
		settings.status = "Syncing"
		settings.save(ignore_permissions=True)
		for entity_type in ordered_entities(entity_types):
			for payload in query_entity_payloads(entity_type, settings=settings):
				store_raw_payload(
					"Import",
					entity_type,
					_clean_payload(payload),
					sync_log=log.name,
					realm_id=settings.realm_id,
				)
				_track_result(log, safe_upsert(entity_type, payload, settings))
		finish_log(log)
		if log.status == "Completed":
			settings.last_full_import = now_datetime()
		settings.status = "Failed" if log.status == "Failed" else "Connected"
		settings.status_message = _status_message(log, "Full import completed.")
		settings.save(ignore_permissions=True)
		frappe.db.commit()
		return log.name
	except Exception as exc:
		fail_log(log, exc)
		raise


def preview_resync(entity_types=None):
	settings = get_settings()
	ensure_connected(settings)
	log = start_log("Preview Resync")
	preview = []
	try:
		for entity_type in ordered_entities(entity_types):
			for payload in query_entity_payloads(entity_type, settings=settings):
				store_raw_payload(
					"Resync",
					entity_type,
					_clean_payload(payload),
					sync_log=log.name,
					realm_id=settings.realm_id,
				)
				result = safe_upsert(entity_type, payload, settings, preview=True)
				_track_result(log, result)
				preview.append({"entity_type": entity_type, "qbo_id": payload.get("Id"), **result})
		log.preview_payload = json_dumps(preview)
		finish_log(log)
		frappe.db.commit()
		return {"preview_id": log.name, "summary": summarize_log(log), "changes": preview}
	except Exception as exc:
		fail_log(log, exc)
		raise


def run_resync(preview_id):
	if not preview_id or not frappe.db.exists("QuickBooks Sync Log", preview_id):
		frappe.throw("A valid Preview Resync log is required before running overwrite resync.")
	settings = get_settings()
	ensure_connected(settings)
	log = start_log("Run Resync")
	try:
		for raw in frappe.get_all(
			"QuickBooks Raw Payload",
			filters={"sync_log": preview_id},
			fields=["qbo_entity_type", "payload"],
			order_by="creation asc",
		):
			payload = json.loads(raw.payload)
			result = safe_upsert(raw.qbo_entity_type, payload, settings, overwrite=True)
			_track_result(log, result)
		finish_log(log)
		frappe.db.commit()
		return {"sync_log": log.name, "summary": summarize_log(log)}
	except Exception as exc:
		fail_log(log, exc)
		raise


def sync_entity(entity_type, qbo_id, source="Manual"):
	settings = get_settings()
	ensure_connected(settings)
	log = start_log("Entity Sync", entity_type=entity_type)
	try:
		response = QuickBooksClient(settings).get_entity(entity_type, qbo_id)
		payload = response.get(entity_type) or response.get(entity_type.lower()) or response
		store_raw_payload(source, entity_type, payload, sync_log=log.name, realm_id=settings.realm_id)
		result = upsert_entity(entity_type, payload, settings)
		_track_result(log, result)
		finish_log(log)
		frappe.db.commit()
		return {"sync_log": log.name, "result": result}
	except Exception as exc:
		fail_log(log, exc)
		raise


def run_cdc():
	settings = get_settings()
	if not settings.sync_enabled or not settings.realm_id:
		return None
	ensure_connected(settings)
	log = start_log("CDC")
	changed_since = settings.last_cdc_sync or add_to_date(now_datetime(), days=-1, as_datetime=True)
	try:
		response = QuickBooksClient(settings).cdc(CDC_ENTITIES, changed_since)
		for cdc_response in response.get("CDCResponse", []):
			for query_response in cdc_response.get("QueryResponse", []):
				for entity_type, payloads in query_response.items():
					if not isinstance(payloads, list):
						continue
					for payload in payloads:
						store_raw_payload(
							"CDC", entity_type, payload, sync_log=log.name, realm_id=settings.realm_id
						)
						if payload.get("status") == "Deleted":
							result = mark_deleted(entity_type, payload.get("Id"))
						else:
							result = safe_upsert(entity_type, payload, settings)
						_track_result(log, result)
		finish_log(log)
		if log.status == "Completed":
			settings.last_cdc_sync = now_datetime()
		settings.status = "Failed" if log.status == "Failed" else "Connected"
		settings.status_message = _status_message(log, "CDC sync completed.")
		settings.save(ignore_permissions=True)
		frappe.db.commit()
		return log.name
	except Exception as exc:
		fail_log(log, exc)
		raise


def retry_failed(log_name=None):
	filters = {"status": "Failed"}
	if log_name:
		filters["name"] = log_name
	for log in frappe.get_all(
		"QuickBooks Sync Log", filters=filters, fields=["name", "sync_type", "retry_count"]
	):
		if (log.retry_count or 0) >= (get_settings().retry_limit or 3):
			continue
		doc = frappe.get_doc("QuickBooks Sync Log", log.name)
		doc.retry_count = (doc.retry_count or 0) + 1
		doc.save(ignore_permissions=True)
		if doc.sync_type == "CDC":
			run_cdc()
		elif doc.sync_type in {"Import All", "Run Resync"}:
			import_all()
	return True


def query_all(entity_type, settings=None):
	settings = settings or get_settings()
	client = QuickBooksClient(settings)
	start_position = 1
	max_results = 100
	while True:
		response = client.query(
			f"select * from {entity_type} startposition {start_position} maxresults {max_results}"
		)
		query_response = response.get("QueryResponse") or {}
		records = query_response.get(entity_type) or []
		yield from records
		if len(records) < max_results:
			break
		start_position += max_results


def query_entity_payloads(entity_type, settings=None):
	if entity_type != "Account":
		yield from query_all(entity_type, settings=settings)
		return

	payloads = list(query_all(entity_type, settings=settings))
	parent_ids = {
		str(parent_id)
		for parent_id in ((payload.get("ParentRef") or {}).get("value") for payload in payloads)
		if parent_id
	}
	for payload in payloads:
		enriched = dict(payload)
		enriched["_qbo_has_children"] = str(payload.get("Id")) in parent_ids
		yield enriched


def _clean_payload(payload):
	if not isinstance(payload, dict):
		return payload
	return {key: value for key, value in payload.items() if not key.startswith("_qbo_")}


def safe_upsert(entity_type, payload, settings, **kwargs):
	try:
		return upsert_entity(entity_type, payload, settings, **kwargs)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"QuickBooks Online {entity_type} import failed for {payload.get('Id') if isinstance(payload, dict) else ''}",
		)
		return {
			"action": "failed",
			"entity_type": entity_type,
			"reason": frappe.get_traceback(),
			"qbo_id": payload.get("Id") if isinstance(payload, dict) else None,
		}


def ordered_entities(entity_types=None):
	selected = list(entity_types or ACCOUNTING_ENTITIES)
	ordered = [entity for entity in MASTER_ENTITIES + TRANSACTION_ENTITIES if entity in selected]
	return ordered + [entity for entity in selected if entity not in ordered]


def store_raw_payload(source, entity_type, payload, *, sync_log=None, realm_id=None, operation=None):
	doc = frappe.new_doc("QuickBooks Raw Payload")
	doc.source = source
	doc.qbo_entity_type = entity_type
	doc.qbo_id = payload.get("Id") if isinstance(payload, dict) else None
	doc.operation = operation or (payload.get("operation") if isinstance(payload, dict) else None)
	doc.realm_id = realm_id
	doc.sync_log = sync_log
	doc.received_at = now_datetime()
	doc.payload = json_dumps(payload)
	doc.insert(ignore_permissions=True)
	return doc


def start_log(sync_type, entity_type=None):
	log = frappe.new_doc("QuickBooks Sync Log")
	log.sync_type = sync_type
	log.entity_type = entity_type
	log.status = "Running"
	log.started_at = now_datetime()
	log.insert(ignore_permissions=True)
	return log


def fail_log(log, exc):
	log.status = "Failed"
	log.error_message = frappe.get_traceback()
	log.finished_at = now_datetime()
	log.failed_count = (log.failed_count or 0) + 1
	log.save(ignore_permissions=True)
	settings = get_settings()
	settings.status = "Failed"
	settings.status_message = str(exc)[:1000]
	settings.save(ignore_permissions=True)
	frappe.db.commit()


def finish_log(log):
	log.status = "Failed" if (log.failed_count or 0) else "Completed"
	log.finished_at = now_datetime()
	log.save(ignore_permissions=True)


def _status_message(log, completed_message):
	if log.status == "Failed":
		return f"Sync finished with {log.failed_count or 0} failed record(s). See QuickBooks Sync Log {log.name}."
	return completed_message


def summarize_log(log):
	return {
		"created": log.created_count or 0,
		"updated": log.updated_count or 0,
		"linked": log.linked_count or 0,
		"deleted": log.deleted_count or 0,
		"conflicts": log.conflict_count or 0,
		"manual_review": log.manual_review_count or 0,
		"failed": log.failed_count or 0,
	}


def ensure_connected(settings):
	if not settings.realm_id:
		frappe.throw("Connect QuickBooks Online before syncing.")


def _track_result(log, result):
	action = result.get("action")
	if action == "created" or action == "create":
		log.created_count = (log.created_count or 0) + 1
	elif action == "updated" or action == "update":
		log.updated_count = (log.updated_count or 0) + 1
	elif action == "linked" or action == "link":
		log.linked_count = (log.linked_count or 0) + 1
	elif action == "deleted" or action == "delete":
		log.deleted_count = (log.deleted_count or 0) + 1
	elif action == "manual_review":
		log.manual_review_count = (log.manual_review_count or 0) + 1
	elif action == "conflict":
		log.conflict_count = (log.conflict_count or 0) + 1
	elif action == "failed":
		log.failed_count = (log.failed_count or 0) + 1
		_append_failure_message(log, result)


def _append_failure_message(log, result):
	failure_number = log.failed_count or 1
	if failure_number > 20:
		if failure_number == 21:
			log.error_message = (
				(log.error_message or "").rstrip()
				+ "\nAdditional failures omitted. See Error Log for full tracebacks."
			).strip()
		return

	reason = _last_non_empty_line(result.get("reason")) or "Unknown error"
	entity = result.get("entity_type") or log.entity_type or "Unknown entity"
	qbo_id = result.get("qbo_id") or "unknown QBO ID"
	message = f"{failure_number}. {entity} {qbo_id}: {reason}"
	log.error_message = ((log.error_message or "").rstrip() + "\n" + message).strip()


def _last_non_empty_line(text):
	if not text:
		return None
	lines = [line.strip() for line in str(text).splitlines() if line.strip()]
	return lines[-1] if lines else None
