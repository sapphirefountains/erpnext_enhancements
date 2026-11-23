"""Sync orchestration for the QuickBooks Online integration.

This is the engine that drives data from QBO into ERPNext. It coordinates the
``client`` (fetch), ``mapping`` (transform/upsert) and the logging/audit
doctypes, exposing the high-level operations the UI and scheduler call:

  * ``import_all``     -- full one-way import of every selected entity. Started by a
    person from the dashboard, and by nothing else (v1.800.0).
  * ``preview_resync`` -- dry run that records what a resync *would* do.
  * ``run_resync``     -- replay a preview: refresh mapping cursors, flag differences.
  * ``sync_entity``    -- fetch + upsert one entity (webhook/manual single sync).
  * ``run_cdc``        -- poll Change Data Capture for incremental updates/deletes.
  * ``retry_failed``   -- re-run a failed CDC poll (scheduler), honouring retry_limit.
    Never an Import All or a Run Resync.

**The sync links, it never overwrites (v1.800.0).** None of these operations saves over
an ERPNext record a mapping already links -- not the hourly CDC, not a webhook, not Import
All and not Run Resync. A linked record whose QuickBooks side has moved on is flagged
``QuickBooks Changed`` on its mapping for a person to review on the Record Matching page
(see ``mapping._review_linked_record`` and ``core/matching.py``).

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
from erpnext_enhancements.quickbooks_online.core.utils import (
	get_settings,
	is_rq_job_timeout,
	json_dumps,
)

# A QuickBooks Sync Log left Running/Queued longer than this is treated as orphaned
# (its worker stopped, restarted or OOM'd) so it neither blocks a new run forever nor
# lingers as a phantom "Running". Keyed per sync type because a full import legitimately
# runs for hours while a CDC poll is a matter of seconds/minutes.
STALE_RUN_SECONDS = {
	"CDC": 60 * 60,  # 1h -- a CDC poll never legitimately runs this long
	"Import All": 11 * 60 * 60,  # > the 10h background-job timeout (api.QBO_JOB_TIMEOUT)
	"Run Resync": 11 * 60 * 60,
	"Preview Resync": 11 * 60 * 60,
	"Opening Balances": 60 * 60,
	"Entity Sync": 30 * 60,
}
DEFAULT_STALE_RUN_SECONDS = 2 * 60 * 60

# How many records a batch op (import / preview / CDC / resync) processes between
# commits. Each document insert takes a FOR UPDATE lock on the `{#####}` naming-series
# row -- a tabSeries entry keyed by the empty string and shared by EVERY
# `format:...{#####}` doctype site-wide -- and holds it until the transaction commits.
# Committing only per entity would hold that global lock for the minutes a large entity
# (e.g. ~2,000 Purchases) takes to fetch + upsert, briefly blocking unrelated record
# creation across the whole site. Committing every N records bounds the hold to a few
# seconds while keeping commit overhead negligible.
QBO_COMMIT_EVERY = 100

# Failed runs of these types are restarted by a person from the dashboard and by nothing
# else -- see ``retry_failed``. A full import or resync belongs on the long queue with its
# 10-hour budget (``api.QBO_JOB_TIMEOUT``); the scheduler's retry runs on the default
# queue, where RQ ends a job after 300 seconds.
RESTART_BY_HAND_TYPES = frozenset({"Import All", "Run Resync", "Preview Resync"})


def import_all(entity_types=None):
	"""Full one-way import of all (or selected) QBO entities into ERPNext.

	For each entity (master types first), pages through every QBO record, stores
	the raw payload and upserts it via ``safe_upsert``. Wrapped in a single
	"Import All" sync log; on success stamps ``last_full_import`` and sets
	Settings status to Connected/Failed based on whether any record failed.

	Triggered from the Settings form / dashboard "Import All" action, and from nowhere
	else: until v1.800.0 the scheduler's ``retry_failed`` restarted it after every failed
	run, which is how a full import ran 2 to 6 times a day on the 300-second default queue.
	Side effects: many QBO GETs, DB writes, a commit per batch. It creates and links
	records; it never saves over a record a mapping already links (``upsert_entity``).
	Returns the sync log name. Re-raises after logging on hard failure.
	"""
	settings = get_settings()
	ensure_connected(settings)
	# Never run two full imports at once: they page the same entities and would race
	# on the same mapping rows. Skip if one is already (genuinely) in progress.
	if run_in_progress("Import All"):
		return None
	log = start_log("Import All")
	try:
		settings.status = "Syncing"
		settings.save(ignore_permissions=True)
		frappe.db.commit()
		processed = 0
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
				processed += 1
				# Commit every QBO_COMMIT_EVERY records so the shared naming-series lock
				# (and the rows written so far) is released in seconds rather than held
				# for the minutes a large entity takes -- otherwise one big batch briefly
				# blocks record creation elsewhere on the site. Counters stay live and a
				# late failure keeps committed progress (the upsert is idempotent).
				if processed % QBO_COMMIT_EVERY == 0:
					log.save(ignore_permissions=True)
					frappe.db.commit()
			# Flush this entity's tail (the < QBO_COMMIT_EVERY records since the last
			# commit) before moving on, so progress is durable at every entity boundary.
			log.save(ignore_permissions=True)
			frappe.db.commit()
		finish_log(log)
		# Write the end-of-run status with set_value, not a save of the Settings doc we
		# loaded at the start: a long import spans the hourly token refresh, which modifies
		# Settings, so a stale-doc save would TimestampMismatch and fail the whole run here.
		_record_settings_status(
			"Failed" if log.status == "Failed" else "Connected",
			_status_message(log, "Full import completed."),
			{"last_full_import": now_datetime()} if log.status == "Completed" else None,
		)
		return log.name
	except Exception as exc:
		fail_log(log, exc)
		raise


def preview_resync(entity_types=None, log_name=None):
	"""Dry-run resync: fetch QBO data and compute changes without writing.

	Mirrors ``import_all`` but calls ``safe_upsert(..., preview=True)`` so no
	ERPNext records are created/updated; the per-record planned actions are
	collected and stored on the log's ``preview_payload``. The returned
	``preview_id`` is then passed to ``run_resync`` to run it for real. A linked
	record that differs is reported as ``would_flag`` (v1.800.0), compared whether or
	not QuickBooks moved, exactly as the run will. Raw payloads ARE stored (so
	``run_resync`` can replay them).
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
		processed = 0
		for entity_type in ordered_entities(entity_types):
			for payload in query_entity_payloads(entity_type, settings=settings):
				store_raw_payload(
					"Resync",
					entity_type,
					_clean_payload(payload),
					sync_log=log.name,
					realm_id=settings.realm_id,
				)
				# recheck, as run_resync will: the preview must count what the run would flag.
				result = safe_upsert(entity_type, payload, settings, preview=True, recheck=True)
				_track_result(log, result)
				preview.append({"entity_type": entity_type, "qbo_id": payload.get("Id"), **result})
				processed += 1
				# Commit every QBO_COMMIT_EVERY records so this long dry run releases the
				# shared naming-series lock (it still writes raw payloads, which run_resync
				# later reads back) in seconds rather than holding it for the whole pass.
				if processed % QBO_COMMIT_EVERY == 0:
					log.save(ignore_permissions=True)
					frappe.db.commit()
			# Flush this entity's tail before moving on.
			log.save(ignore_permissions=True)
			frappe.db.commit()
		log.preview_payload = json_dumps(preview)
		finish_log(log)
		frappe.db.commit()
		return {"preview_id": log.name, "summary": summarize_log(log), "changes": preview}
	except Exception as exc:
		fail_log(log, exc)
		raise


def run_resync(preview_id):
	"""Apply a previously generated preview: create, link, and flag what differs.

	Replays the raw payloads captured under the given preview log (in creation
	order) through ``safe_upsert``. Re-fetches nothing from QBO -- it works purely
	from the stored payloads. Raises if ``preview_id`` is not a valid log.
	Returns ``{sync_log, summary}``.

	Until v1.800.0 this passed ``overwrite=True`` and QuickBooks' values were saved over
	every linked record. ``upsert_entity`` no longer saves over a linked record, whatever
	it is passed. A resync creates what is missing, refreshes mapping cursors and flags
	the linked records QuickBooks changed (``QuickBooks Changed``). It passes ``recheck=True``,
	so a linked record is compared even when QuickBooks has not moved since its last
	sync: a resync is how a person asks for the full comparison, for example after a
	mapper fix changes what the existing drafts should hold. The one way QuickBooks
	values then reach a linked record is a person's *Use QuickBooks values* or *Refresh
	draft* on the Record Matching page.
	"""
	if not preview_id or not frappe.db.exists("QuickBooks Sync Log", preview_id):
		frappe.throw("A valid Preview Resync log is required before running a resync.")
	settings = get_settings()
	ensure_connected(settings)
	log = start_log("Run Resync")
	try:
		processed = 0
		for raw in frappe.get_all(
			"QuickBooks Raw Payload",
			filters={"sync_log": preview_id},
			fields=["qbo_entity_type", "payload"],
			order_by="creation asc",
		):
			payload = json.loads(raw.payload)
			result = safe_upsert(raw.qbo_entity_type, payload, settings, recheck=True)
			_track_result(log, result)
			processed += 1
			# Commit in batches so a large replay holds the shared naming-series lock for
			# a bounded window at a time, keeps counters current, and survives a late
			# failure with progress intact.
			if processed % QBO_COMMIT_EVERY == 0:
				log.save(ignore_permissions=True)
				frappe.db.commit()
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
		# Savepoint the upsert: fail_log below commits (Settings status must survive the
		# re-raise), so a partial insert left by a failed upsert would otherwise be committed
		# as a mapping-less document and later re-imported as a duplicate. Roll it back first,
		# keeping the archived raw payload and the log.
		frappe.db.savepoint("qbo_entity_upsert")
		try:
			result = upsert_entity(entity_type, payload, settings)
		except Exception:
			frappe.db.rollback(save_point="qbo_entity_upsert")
			raise
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
	the mapping deleted (status == "Deleted") or upserts it -- which creates and links
	new records and flags, never saves, a record a mapping already links. The cursor is only
	advanced to "now" if the run completed without failures, so a failed batch is
	retried against the same window next time. Returns the sync log name.
	"""
	settings = get_settings()
	if not settings.sync_enabled or not settings.realm_id:
		return None
	ensure_connected(settings)
	# Serialize CDC: overlapping polls reprocess the same change window and race on
	# the same mapping rows (TimestampMismatchError). Skip if one is already running.
	if run_in_progress("CDC"):
		return None
	log = start_log("CDC")
	# Cursor: changes since the last successful CDC; first run looks back 24h.
	# Clamp it into QBO's 30-day CDC window so a stale cursor (paused integration)
	# degrades to a recent window instead of a hard 400.
	changed_since = settings.last_cdc_sync or add_to_date(now_datetime(), days=-1, as_datetime=True)
	changed_since = _clamp_cdc_cursor(get_datetime(changed_since), get_datetime(now_datetime()))
	# Capture the next window's floor BEFORE the CDC request, not at run end. A wide
	# catch-up poll (first run after a pause) can take minutes; a record changed in QBO
	# during those minutes is in neither this window nor a next one cursored at end-of-run,
	# so it silently never syncs. Back off two minutes for ERPNext↔QBO clock skew; the small
	# re-fetch overlap next run is harmless (upserts are idempotent by mapping name).
	next_cursor = add_to_date(now_datetime(), minutes=-2, as_datetime=True)
	try:
		response = QuickBooksClient(settings).cdc(CDC_ENTITIES, changed_since)
		processed = 0
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
						processed += 1
						# Bound the shared naming-series lock hold on a wide catch-up
						# window (a first poll after a long pause can be large).
						if processed % QBO_COMMIT_EVERY == 0:
							log.save(ignore_permissions=True)
							frappe.db.commit()
					# Commit each entity batch so a poll never holds its locks across
					# the whole CDC window and progress is durable mid-run.
					log.save(ignore_permissions=True)
					frappe.db.commit()
		finish_log(log)
		# Only advance the cursor on a clean run so failures get reprocessed. Written via
		# set_value (not a stale-doc save) for the same reason as import_all's finish.
		_record_settings_status(
			"Failed" if log.status == "Failed" else "Connected",
			_status_message(log, "CDC sync completed."),
			{"last_cdc_sync": next_cursor} if log.status == "Completed" else None,
		)
		return log.name
	except Exception as exc:
		fail_log(log, exc)
		raise


def retry_failed(log_name=None):
	"""Re-run a failed CDC poll, capped by ``retry_limit``. Never an Import All.

	Called by the ``tasks.retry_failed_syncs`` scheduler hook (all failed logs)
	or with a specific ``log_name`` from the dashboard "Retry Failed" action.
	Each eligible failed **CDC** log has its ``retry_count`` incremented (skipped once
	it hits Settings.retry_limit, default 3) to record the attempt, and ``run_cdc`` is
	then re-run **at most once per call**, no matter how many CDC logs are failed.

	**A failed Import All or Run Resync is never re-run from here (v1.800.0).** Until
	then this function ended by calling the full import whenever one of those logs was
	Failed, so the hourly scheduler ran a full Import All inline on the 300-second
	default queue after every failed one -- and a full import cannot finish in 300
	seconds, so each run failed and earned the next: 2 to 6 full imports a day, each
	re-saving every linked record it reached. Those logs are now left exactly as they
	are (no ``retry_count`` bump, since nothing is attempted) and their names come back
	in ``restart_by_hand`` so the dashboard can say what to do: start the run again from
	the dashboard, where it goes to the long queue with a 10-hour budget.
	``RESTART_BY_HAND_TYPES`` names the types; ``tests/test_quickbooks_link_only.py``
	holds this function to it, and checks this module's source for any other caller.

	Running the global operation once per *log* (the behaviour before v1.36) amplified
	catastrophically: every failed run creates another failed log, so N failed logs
	spawned N re-runs that each spawned more, all racing on the same mapping rows
	(``TimestampMismatchError``) and never converging -- the observed runaway of
	hundreds of failed CDC runs. Orphaned Running/Queued logs are reaped first so a
	crashed run can't keep the failed list (and the storm) alive.

	Returns ``{"cdc": <True when a CDC poll was re-run>, "restart_by_hand": [log names]}``.
	"""
	reap_stale_runs()
	limit = get_settings().retry_limit or 3
	filters = {"status": "Failed"}
	if log_name:
		filters["name"] = log_name
	retry_cdc = False
	restart_by_hand = []
	for log in frappe.get_all(
		"QuickBooks Sync Log", filters=filters, fields=["name", "sync_type", "retry_count"]
	):
		if log.sync_type in RESTART_BY_HAND_TYPES:
			restart_by_hand.append(log.name)
			continue
		if log.sync_type != "CDC":
			continue
		# Stop retrying a log once it has hit the configured attempt ceiling.
		if (log.retry_count or 0) >= limit:
			continue
		doc = frappe.get_doc("QuickBooks Sync Log", log.name)
		doc.retry_count = (doc.retry_count or 0) + 1
		doc.save(ignore_permissions=True)
		retry_cdc = True
	# One CDC poll for the whole batch of failed CDC logs.
	if retry_cdc:
		run_cdc()
	return {"cdc": retry_cdc, "restart_by_hand": restart_by_hand}


def run_in_progress(sync_type):
	"""True if a non-stale Running/Queued sync log of ``sync_type`` exists.

	The concurrency guard for ``run_cdc``/``import_all``. Logs older than the
	per-type staleness window (``STALE_RUN_SECONDS``) are an orphaned run whose
	worker died and are ignored, so a crashed run can never permanently block new
	ones.
	"""
	now = get_datetime(now_datetime())
	threshold = STALE_RUN_SECONDS.get(sync_type, DEFAULT_STALE_RUN_SECONDS)
	for log in frappe.get_all(
		"QuickBooks Sync Log",
		filters={"sync_type": sync_type, "status": ["in", ["Running", "Queued"]]},
		fields=["started_at", "modified"],
	):
		started = get_datetime(log.started_at or log.modified)
		if started and (now - started).total_seconds() <= threshold:
			return True
	return False


def reap_stale_runs():
	"""Fail orphaned Running/Queued sync logs and return how many were reaped.

	A run whose worker died (deploy, restart, OOM) leaves its log stuck Running
	forever -- inflating the failed/running counts and, for an Import All, blocking
	every future import via the ``run_in_progress`` guard. This marks any Running or
	Queued log past its per-type staleness window as Failed so it stops blocking and
	the normal retry path can pick it up.
	"""
	now = get_datetime(now_datetime())
	reaped = 0
	for log in frappe.get_all(
		"QuickBooks Sync Log",
		filters={"status": ["in", ["Running", "Queued"]]},
		fields=["name", "sync_type", "started_at", "modified"],
	):
		started = get_datetime(log.started_at or log.modified)
		threshold = STALE_RUN_SECONDS.get(log.sync_type, DEFAULT_STALE_RUN_SECONDS)
		if not started or (now - started).total_seconds() <= threshold:
			continue
		doc = frappe.get_doc("QuickBooks Sync Log", log.name)
		doc.status = "Failed"
		doc.finished_at = now_datetime()
		if not (doc.error_message or "").strip():
			doc.error_message = (
				"Run did not finish (worker stopped or restarted); marked Failed by stale-run cleanup."
			)
		doc.save(ignore_permissions=True)
		reaped += 1
	return reaped


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

	Customers are yielded top-level-first (sorted by QBO ``Level``) so that a
	sub-customer / job -- which the mapper turns into a Project under its parent
	Customer (see ``mapping._map_qbo_job_to_project`` / ``_top_level_customer``) --
	finds its parent already imported and mapped when it resolves.
	"""
	if entity_type == "Customer":
		# QBO ``Level`` is the nesting depth (0 = top-level customer, >0 = job under a
		# parent). Stable-sort by level so parents precede their children; QBO's own
		# order is preserved within a level.
		payloads = list(query_all(entity_type, settings=settings))
		payloads.sort(key=lambda payload: payload.get("Level") or 0)
		yield from payloads
		return

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
	run. ``kwargs`` forward flags like ``preview`` / ``recheck``.

	A ``TimestampMismatchError`` (another worker touched the same mapping/record
	between our read and write) is retried once: ``upsert_entity`` re-reads both the
	mapping and the target doc on entry, so a single retry clears the transient race
	rather than parking a perfectly good record as Failed.

	**RQ's job timeout is never one of the swallowed exceptions (v1.800.0).**
	``JobTimeoutException`` subclasses ``Exception``, so the ``except Exception`` below
	used to catch it like any bad record: the run counted one "failed" record and
	carried on. RQ's alarm is one-shot, so from then on the run had no time limit at
	all, until the worker's own watchdog killed the process without a word -- leaving
	a Sync Log stuck in Running until ``reap_stale_runs`` failed it 11 hours later, and
	blocking every Import All in between. Production still holds one such log from
	2026-10-06. The timeout is re-raised after the savepoint rollback, so the run
	wrapper's ``fail_log`` ends the run as Failed, with the traceback, at once.
	"""
	# Savepoint per record: upsert_entity's create path is doc.insert() THEN save_mapping().
	# If save_mapping raises, the inserted doc is left in the open transaction and the next
	# batch commit persists it WITHOUT a Sync Mapping row — after which the retry path
	# re-creates it (transactions are never fuzzy-matched), a silent duplicate. Rolling back
	# to the savepoint on failure undoes only this record's partial writes, leaving the rest
	# of the committed batch intact.
	frappe.db.savepoint("qbo_upsert")
	try:
		return upsert_entity(entity_type, payload, settings, **kwargs)
	except frappe.exceptions.TimestampMismatchError:
		frappe.db.rollback(save_point="qbo_upsert")
		frappe.db.savepoint("qbo_upsert")
		try:
			return upsert_entity(entity_type, payload, settings, **kwargs)
		except Exception as exc:
			frappe.db.rollback(save_point="qbo_upsert")
			if is_rq_job_timeout(exc):
				raise
			return _failed_result(entity_type, payload)
	except Exception as exc:
		frappe.db.rollback(save_point="qbo_upsert")
		if is_rq_job_timeout(exc):
			raise
		return _failed_result(entity_type, payload)


def _failed_result(entity_type, payload):
	"""Log the traceback and build the ``{"action": "failed", ...}`` result dict."""
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
	"""Open, insert and commit a new ``QuickBooks Sync Log`` in the Running state.

	The commit is load-bearing, not cosmetic. Inserting the log advances the
	``QBO-SYNC-{YYYY}-`` naming series, which takes a row lock on its ``tabSeries``
	entry. A batch op (import/resync/CDC) otherwise holds that lock for its ENTIRE
	single-transaction run -- minutes to hours -- so every other run's
	``start_log`` blocks on the series and dies with "Lock wait timeout exceeded;
	try restarting transaction" before it can create a log, which surfaces as
	"Import All does nothing" (the job fails at start_log, never logging). Committing
	here releases the series lock immediately and makes the Running log visible at
	once -- to the dashboard for progress, and to ``run_in_progress`` /
	api.import_all's "already_running" guard so duplicate runs are refused instead
	of piling up.
	"""
	log = frappe.new_doc("QuickBooks Sync Log")
	log.sync_type = sync_type
	log.entity_type = entity_type
	log.status = "Running"
	log.started_at = now_datetime()
	log.insert(ignore_permissions=True)
	frappe.db.commit()
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
		# Commit the Running transition at once: the row was pre-created Queued, so
		# this both frees its lock and lets the dashboard see the run start.
		frappe.db.commit()
	return log


def _record_settings_status(status, message, extra=None):
	"""Write QBO Settings status fields directly, bypassing the optimistic-lock check.

	A long import/CDC run loads the Settings Single once at the start and would save it
	at the end -- but the hourly token refresh modifies that doc mid-run, so saving the
	now-stale doc raises ``TimestampMismatchError`` and fails an otherwise-successful run
	at the finish line (the imported data, committed per batch, survives; only the run is
	wrongly marked Failed and the cursor / last-import stamp is lost). ``db.set_value`` is
	an atomic per-field write -- no whole-doc timestamp check and no race -- and it leaves
	the token fields the refresh just wrote untouched. Commits so the status survives the
	caller's re-raise.
	"""
	updates = {"status": status, "status_message": (message or "")[:1000]}
	if extra:
		updates.update(extra)
	frappe.db.set_value("QuickBooks Online Settings", "QuickBooks Online Settings", updates)
	frappe.db.commit()


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
	# set_value, not a doc save: the error handler must not itself raise a second
	# TimestampMismatchError (masking the original) when Settings changed mid-run.
	_record_settings_status("Failed", str(exc))


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

	``flagged`` (and a preview's ``would_flag``) is a linked record whose QuickBooks side
	moved on and now differs from ERPNext (v1.800.0). It counts under ``conflict_count``,
	the counter the pre-v1.800.0 ``conflict`` action used: both mean "QuickBooks and
	ERPNext disagree and a person has to look", and the dashboard already shows it.

	``ignored`` is counted so records a human closed out stay visible as a category
	in every run's summary rather than just being absent from it.
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
	elif action == "ignored":
		log.ignored_count = (log.ignored_count or 0) + 1
	elif action in ("flagged", "would_flag", "conflict"):
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
