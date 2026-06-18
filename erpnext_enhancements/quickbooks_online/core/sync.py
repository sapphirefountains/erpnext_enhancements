"""Sync orchestration for the QuickBooks Online integration.

This is the engine that drives data from QBO into ERPNext. It coordinates the
``client`` (fetch), ``mapping`` (transform/upsert) and the logging/audit
doctypes, exposing the high-level operations the UI and scheduler call:

  * ``import_all``     -- full one-way import of every selected entity (UI/Retry).
  * ``preview_resync`` -- dry run that records what an overwrite resync *would* do.
  * ``run_resync``     -- replay a preview, overwriting QBO-owned fields.
  * ``sync_entity``    -- fetch + upsert one entity (webhook/manual single sync).
  * ``run_cdc``        -- poll Change Data Capture for incremental updates/deletes.
  * ``retry_failed``   -- re-run logs left Failed (scheduler), honouring retry_limit.

Every operation opens a ``QuickBooks Sync Log`` (run lifecycle + per-action
counters), archives each fetched record as a ``QuickBooks Raw Payload``, and
funnels writes through ``mapping.upsert_entity`` (wrapped by ``safe_upsert`` so
one bad record cannot abort an entire batch). Master entities are imported
before transactions so reference links resolve.
"""

from __future__ import annotations

import json
from datetime import timedelta

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from erpnext_enhancements.quickbooks_online.core.client import QuickBooksClient
from erpnext_enhancements.quickbooks_online.core.constants import (
	ACCOUNTING_ENTITIES,
	CDC_ENTITIES,
	CDC_MAX_LOOKBACK_DAYS,
	MASTER_ENTITIES,
	TRANSACTION_ENTITIES,
)
from erpnext_enhancements.quickbooks_online.core.mapping import (
	mark_deleted,
	upsert_entity,
)
from erpnext_enhancements.quickbooks_online.core.utils import get_settings, json_dumps


def import_all(entity_types=None):
	"""Full one-way import of all (or selected) QBO entities into ERPNext.

	For each entity (master types first), pages through every QBO record, stores
	the raw payload and upserts it via ``safe_upsert``. Wrapped in a single
	"Import All" sync log; on success stamps ``last_full_import`` and sets
	Settings status to Connected/Failed based on whether any record failed.

	Triggered from the Settings form / dashboard "Import All" action and from the
	scheduler ``retry_failed`` path. Side effects: many QBO GETs, DB writes, two
	commits. Returns the sync log name. Re-raises after logging on hard failure.
	"""
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


def preview_resync(entity_types=None, log_name=None):
	"""Dry-run resync: fetch QBO data and compute changes without writing.

	Mirrors ``import_all`` but calls ``safe_upsert(..., preview=True)`` so no
	ERPNext records are created/updated; the per-record planned actions are
	collected and stored on the log's ``preview_payload``. The returned
	``preview_id`` is then passed to ``run_resync`` to actually apply the
	overwrite. Raw payloads ARE stored (so ``run_resync`` can replay them).
	Returns ``{preview_id, summary, changes}``.

	When ``log_name`` is supplied (the API pre-creates a Queued log so the
	dashboard has a stable id to poll while this runs as a background job) that
	log is reused; otherwise a fresh one is opened.
	"""
	settings = get_settings()
	ensure_connected(settings)
	log = _resume_or_start_log(log_name, "Preview Resync")
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
	"""Apply a previously generated preview, overwriting QBO-owned fields.

	Replays the raw payloads captured under the given preview log (in creation
	order) through ``safe_upsert(..., overwrite=True)`` so conflicts are resolved
	in QBO's favour rather than flagged. Re-fetches nothing from QBO -- it works
	purely from the stored payloads. Raises if ``preview_id`` is not a valid log.
	Returns ``{sync_log, summary}``.
	"""
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
	"""Fetch a single QBO entity by id and upsert it into ERPNext.

	The fine-grained sync used by webhooks (enqueued with ``source="Webhook"``)
	and the dashboard's per-entity "Sync" button. GETs the entity, unwraps the
	type-keyed response envelope, stores the raw payload and runs ``upsert_entity``
	directly (not ``safe_upsert``) so failures fail the log and re-raise.
	Returns ``{sync_log, result}``.
	"""
	settings = get_settings()
	ensure_connected(settings)
	log = start_log("Entity Sync", entity_type=entity_type)
	try:
		response = QuickBooksClient(settings).get_entity(entity_type, qbo_id)
		# QBO wraps single-entity GETs under a type-named key (e.g. {"Invoice": {...}}).
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
	"""Poll QBO Change Data Capture and apply incremental changes/deletions.

	Driven by the ``tasks.cdc_poll`` scheduler hook (and ``retry_failed``). No-op
	unless sync is enabled and connected. Uses ``last_cdc_sync`` as the
	"changedSince" cursor (defaulting to 24h ago on first run), walks the nested
	CDCResponse->QueryResponse structure, archives each payload, and either marks
	the mapping deleted (status == "Deleted") or upserts it. The cursor is only
	advanced to "now" if the run completed without failures, so a failed batch is
	retried against the same window next time. Returns the sync log name.
	"""
	settings = get_settings()
	if not settings.sync_enabled or not settings.realm_id:
		return None
	ensure_connected(settings)
	log = start_log("CDC")
	# Cursor: changes since the last successful CDC; first run looks back 24h.
	# Clamp it into QBO's 30-day CDC window so a stale cursor (paused integration)
	# degrades to a recent window instead of a hard 400.
	changed_since = settings.last_cdc_sync or add_to_date(now_datetime(), days=-1, as_datetime=True)
	changed_since = _clamp_cdc_cursor(get_datetime(changed_since), get_datetime(now_datetime()))
	try:
		response = QuickBooksClient(settings).cdc(CDC_ENTITIES, changed_since)
		for cdc_response in response.get("CDCResponse", []):
			for query_response in cdc_response.get("QueryResponse", []):
				for entity_type, payloads in query_response.items():
					# QueryResponse mixes entity-name keys (lists) with metadata; skip non-lists.
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
		# Only advance the cursor on a clean run so failures get reprocessed.
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
	"""Re-run sync logs left in the Failed state, capped by ``retry_limit``.

	Called by the ``tasks.retry_failed_syncs`` scheduler hook (all failed logs)
	or with a specific ``log_name`` from the dashboard "Retry Failed" action.
	Each eligible log has its ``retry_count`` incremented (skipped once it hits
	Settings.retry_limit, default 3), then the originating operation is re-run:
	CDC logs re-run ``run_cdc``; Import All / Run Resync logs re-run ``import_all``.
	Always returns True.
	"""
	filters = {"status": "Failed"}
	if log_name:
		filters["name"] = log_name
	for log in frappe.get_all(
		"QuickBooks Sync Log", filters=filters, fields=["name", "sync_type", "retry_count"]
	):
		# Stop retrying a log once it has hit the configured attempt ceiling.
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


def _clamp_cdc_cursor(changed_since, now):
	"""Clamp a CDC ``changedSince`` cursor into QBO's 30-day lookback window.

	QBO rejects a cursor older than ~30 days; if the stored cursor predates that
	(the integration was paused longer than the window), return the earliest
	timestamp QBO still accepts -- a 1-day safety margin inside the limit to
	tolerate clock skew with Intuit's servers. Otherwise the cursor is unchanged.
	"""
	earliest = now - timedelta(days=CDC_MAX_LOOKBACK_DAYS - 1)
	if changed_since is None or changed_since < earliest:
		return earliest
	return changed_since


def query_all(entity_type, settings=None):
	"""Yield every QBO record of a type, paging through the query endpoint.

	Generator that walks QBO's ``startposition``/``maxresults`` pagination (100
	per page) until a short page signals the end. Each page is a separate HTTP
	call via ``client.query``.

	Master entities add ``where Active in (true, false)`` because QBO's query
	endpoint returns only active records by default; without it, transactions
	referencing deactivated accounts/items/parties (common in historical data)
	would have unresolvable references and import incomplete or unbalanced.
	"""
	settings = settings or get_settings()
	client = QuickBooksClient(settings)
	start_position = 1
	max_results = 100
	# QBO syntax: the WHERE clause precedes startposition/maxresults paging.
	condition = " where Active in (true, false)" if entity_type in MASTER_ENTITIES else ""
	while True:
		response = client.query(
			f"select * from {entity_type}{condition} startposition {start_position} maxresults {max_results}"
		)
		query_response = response.get("QueryResponse") or {}
		records = query_response.get(entity_type) or []
		yield from records
		# A page smaller than the page size means we've reached the last page.
		if len(records) < max_results:
			break
		start_position += max_results


def query_entity_payloads(entity_type, settings=None):
	"""Yield payloads for an entity, enriching hierarchical types with a children flag.

	For most entities this is a passthrough to ``query_all``. For the hierarchical
	types ("Account" and "Class", both of which use ``ParentRef``) it first
	materializes the full list to detect which records are parents of others,
	tagging each with a transient ``_qbo_has_children`` flag so the mapper can
	correctly mark parents as a group (``is_group``) Account / Cost Center. The
	flag is stripped before persistence by ``_clean_payload``.
	"""
	if entity_type not in ("Account", "Class"):
		yield from query_all(entity_type, settings=settings)
		return

	payloads = list(query_all(entity_type, settings=settings))
	# Collect every id referenced as a parent so children imply a group.
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
	"""Strip transient ``_qbo_*`` helper keys before a payload is stored."""
	if not isinstance(payload, dict):
		return payload
	return {key: value for key, value in payload.items() if not key.startswith("_qbo_")}


def safe_upsert(entity_type, payload, settings, **kwargs):
	"""Run ``mapping.upsert_entity``, converting exceptions into a failed result.

	Used by batch operations (import/resync/CDC) so one malformed record logs an
	Error and returns ``{"action": "failed", ...}`` instead of aborting the whole
	run. ``kwargs`` forward flags like ``preview`` / ``overwrite``.
	"""
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
	"""Order a selection so master data is imported before transactions.

	Ensures dependencies (Accounts/Customers/Items, etc.) exist and are mapped
	before the transactions that reference them; any selected entity not in the
	known master/transaction lists is appended at the end.
	"""
	selected = list(entity_types or ACCOUNTING_ENTITIES)
	ordered = [entity for entity in MASTER_ENTITIES + TRANSACTION_ENTITIES if entity in selected]
	return ordered + [entity for entity in selected if entity not in ordered]


def store_raw_payload(source, entity_type, payload, *, sync_log=None, realm_id=None, operation=None):
	"""Persist a fetched/received QBO payload as a ``QuickBooks Raw Payload``.

	The integration's audit trail and the data source ``run_resync`` /
	``link_existing_record`` replay from. ``source`` is the origin
	(Import/Resync/Webhook/CDC/Manual); the QBO id/operation are extracted from
	the payload when it is a dict. Inserts (ignore_permissions) and returns the doc.
	"""
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
	"""Open and insert a new ``QuickBooks Sync Log`` in the Running state."""
	log = frappe.new_doc("QuickBooks Sync Log")
	log.sync_type = sync_type
	log.entity_type = entity_type
	log.status = "Running"
	log.started_at = now_datetime()
	log.insert(ignore_permissions=True)
	return log


def create_pending_log(sync_type):
	"""Insert a Queued ``QuickBooks Sync Log`` for a background job to populate.

	Lets the web request return a stable log name immediately (for the UI to
	poll) while the heavy work runs on a worker and fills this same row in via
	``_resume_or_start_log``. Returns the new log's name.
	"""
	log = frappe.new_doc("QuickBooks Sync Log")
	log.sync_type = sync_type
	log.status = "Queued"
	log.started_at = now_datetime()
	log.insert(ignore_permissions=True)
	return log.name


def _resume_or_start_log(log_name, sync_type):
	"""Return a Running log: reuse a pre-created (Queued) one, else open a fresh one."""
	if not log_name:
		return start_log(sync_type)
	log = frappe.get_doc("QuickBooks Sync Log", log_name)
	if log.status != "Running":
		log.status = "Running"
		if not log.started_at:
			log.started_at = now_datetime()
		log.save(ignore_permissions=True)
	return log


def fail_log(log, exc):
	"""Mark a run as hard-failed: record the traceback on the log and Settings.

	Called from each operation's except-block for unexpected errors (as opposed
	to per-record failures tracked via counters). Saves the log with the full
	traceback, sets Settings status to Failed with the exception text, and commits
	so the failure survives the re-raise that follows.
	"""
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
	"""Close a run: status is Failed if any per-record failures, else Completed."""
	log.status = "Failed" if (log.failed_count or 0) else "Completed"
	log.finished_at = now_datetime()
	log.save(ignore_permissions=True)


def _status_message(log, completed_message):
	"""Pick the Settings status message: failure summary or the success text."""
	if log.status == "Failed":
		return f"Sync finished with {log.failed_count or 0} failed record(s). See QuickBooks Sync Log {log.name}."
	return completed_message


def summarize_log(log):
	"""Return the log's per-action counters as a plain dict (for UI responses)."""
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
	"""Guard: raise unless a realm id (connected company) is present on Settings."""
	if not settings.realm_id:
		frappe.throw("Connect QuickBooks Online before syncing.")


def _track_result(log, result):
	"""Increment the matching per-action counter on the log from an upsert result.

	Maps both preview verbs (create/update/link/delete) and applied verbs
	(created/updated/...) to the same counter so previews and real runs summarize
	identically. Failures additionally append a human-readable line via
	``_append_failure_message``.
	"""
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
	"""Append a numbered one-line failure note to the log's error_message.

	Keeps the log readable by capping at 20 inline failures and adding a single
	"additional failures omitted" note at the 21st; full tracebacks remain in the
	Frappe Error Log (written by ``safe_upsert``).
	"""
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
	"""Return the last non-blank line of ``text`` (the salient bit of a traceback)."""
	if not text:
		return None
	lines = [line.strip() for line in str(text).splitlines() if line.strip()]
	return lines[-1] if lines else None
