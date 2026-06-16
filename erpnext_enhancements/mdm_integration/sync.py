"""Sync orchestration for the MDM Integration.

Pulls each enabled provider's device inventory, archives every record as an
``MDM Raw Payload``, reconciles it into the Managed Device registry via
``mapping.upsert_device`` (wrapped by ``safe_upsert`` so one bad record can't
abort the batch), and flags registry devices the provider stopped returning as
**Unmanaged** (never deleted). Run lifecycle + counters live on an ``MDM Sync
Log``. Mirrors ``quickbooks_online/sync.py``; the per-provider cursor
(``*_last_sync``) only advances on a clean run.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from erpnext_enhancements.mdm_integration.client import MDMProviderError, get_provider
from erpnext_enhancements.mdm_integration.mapping import upsert_device
from erpnext_enhancements.mdm_integration.utils import get_settings, json_dumps

# Per-provider Settings field for the "last successful sync" cursor.
_LAST_SYNC_FIELD = {"Miradore": "miradore_last_sync", "Action1": "action1_last_sync"}
_STATUS_FIELD = {"Miradore": "miradore_status", "Action1": "action1_status"}
# Set when a provider hits a non-retryable (auth/permission) failure: the
# scheduler then *pauses* that provider's automatic sync + retries instead of
# hammering known-bad credentials every cycle. Cleared on the next successful
# sync, on a passing Test Connection, or when MDM Settings is re-saved.
_AUTH_BLOCK_FIELD = {"Miradore": "miradore_auth_blocked", "Action1": "action1_auth_blocked"}


def auth_blocked(settings, provider_key):
	"""True when ``provider_key`` is paused after a non-retryable failure."""
	field = _AUTH_BLOCK_FIELD.get(provider_key)
	return bool(field and settings.get(field))


def clear_provider_auth_block(settings, provider_key):
	"""Un-pause a provider (e.g. after a passing Test Connection). Saves + commits
	only when the block was actually set, so it stays a no-op otherwise."""
	field = _AUTH_BLOCK_FIELD.get(provider_key)
	if field and settings.get(field):
		settings.set(field, 0)
		settings.save(ignore_permissions=True)
		frappe.db.commit()


def run_device_sync(provider_key, log=None):
	"""Pull one provider's devices and reconcile them into the registry.

	Pass ``log`` to re-run an existing (failed) MDM Sync Log in place — a retry
	*reuses* its own row rather than spawning a new one, so a persistently failing
	provider can never fan out into an unbounded pile of logs. Returns the sync
	log name. Re-raises on hard failure after logging.
	"""
	settings = get_settings()
	provider = get_provider(provider_key, settings)
	log = _open_log(provider_key, log)
	seen = set()
	try:
		update_provider_status(settings, provider_key, "Syncing")
		for pd in provider.list_devices():
			store_raw_payload(provider_key, "Sync", pd, sync_log=log.name)
			if pd.provider_id:
				seen.add(pd.provider_id)
			_track(log, safe_upsert(pd))
		log.unmanaged_count = flag_unmanaged(provider_key, seen)
		finish_log(log)
		if log.status == "Completed":
			# A clean run clears any auth pause and advances the cursor.
			update_provider_status(
				settings, provider_key, "Connected", last_sync=now_datetime(), auth_blocked=False
			)
		else:
			# Per-device mapping failures (list_devices succeeded) — Failed, but a
			# transient/data problem, not an auth block; leave it retryable.
			update_provider_status(settings, provider_key, "Failed")
		frappe.db.commit()
		return log.name
	except Exception as exc:
		fail_log(log, exc, provider_key)
		raise


def _open_log(provider_key, log):
	"""Start a fresh MDM Sync Log, or re-open an existing one for a retry — reset
	its run state (status/timestamps/counters/error) while preserving its
	``retry_count`` — so a retry never creates a new row."""
	if log is None:
		return start_log(provider_key)
	log.status = "Running"
	log.started_at = now_datetime()
	log.finished_at = None
	log.error_message = None
	for field in ("created_count", "updated_count", "discovered_count", "unmanaged_count", "failed_count"):
		log.set(field, 0)
	log.save(ignore_permissions=True)
	return log


def flag_unmanaged(provider_key, seen_ids):
	"""Mark this provider's registry devices that the feed no longer returns as
	Unmanaged (so the dashboard surfaces them). Never deletes."""
	count = 0
	for device in frappe.get_all(
		"Managed Device",
		filters={"mdm_provider": provider_key, "mdm_link_state": ("in", ["Managed", "Discovered"])},
		fields=["name", "mdm_provider_device_id"],
	):
		if device.mdm_provider_device_id and device.mdm_provider_device_id not in seen_ids:
			frappe.db.set_value("Managed Device", device.name, "mdm_link_state", "Unmanaged", update_modified=False)
			count += 1
	return count


def safe_upsert(pd):
	"""Run ``mapping.upsert_device``, converting an exception into a failed result
	so one malformed record doesn't abort the whole sync."""
	try:
		return upsert_device(pd)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"MDM upsert failed for {pd.provider} {pd.provider_id}")
		return {"action": "failed", "provider_id": pd.provider_id, "reason": frappe.get_traceback()}


def retry_failed(log_name=None):
	"""Re-run MDM Sync Logs left Failed, capped by Settings.retry_limit.

	Each eligible log is re-run *in place* (no new rows), and a provider whose
	credentials are known-bad (``auth_blocked``, e.g. a standing 401) is skipped —
	a permanent auth failure is paused for reconfiguration, never retried every
	cycle. A specific ``log_name`` (the dashboard "Retry" action) overrides the
	pause, since that is a deliberate operator request after a fix.
	"""
	settings = get_settings()
	limit = settings.retry_limit or 3
	explicit = bool(log_name)
	filters = {"status": "Failed"}
	if log_name:
		filters["name"] = log_name
	# Providers that fail non-retryably *during this run* — skip their remaining
	# logs immediately (our local `settings` copy won't see the freshly-persisted
	# block) so one bad-credential provider can't burn a 401 per stale log.
	blocked = set()
	for log in frappe.get_all("MDM Sync Log", filters=filters, fields=["name", "provider", "retry_count"]):
		if (log.retry_count or 0) >= limit or log.provider in blocked:
			continue
		if not explicit and auth_blocked(settings, log.provider):
			continue
		doc = frappe.get_doc("MDM Sync Log", log.name)
		doc.retry_count = (doc.retry_count or 0) + 1
		doc.save(ignore_permissions=True)
		try:
			run_device_sync(log.provider, log=doc)
		except MDMProviderError as exc:
			# Already recorded on the log + provider status_message; don't also
			# spam the Error Log. Stop hammering this provider once it's permanent.
			if not exc.retryable:
				blocked.add(log.provider)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"MDM retry failed for {log.provider}")
	return True


# ---------------------------------------------------------------- log/payload


def store_raw_payload(provider_key, source, pd, *, sync_log=None):
	"""Archive a fetched ProviderDevice's raw record as an MDM Raw Payload."""
	doc = frappe.new_doc("MDM Raw Payload")
	doc.provider = provider_key
	doc.source = source
	doc.provider_device_id = pd.provider_id
	doc.sync_log = sync_log
	doc.received_at = now_datetime()
	doc.payload = json_dumps(pd.raw or {})
	doc.insert(ignore_permissions=True)
	return doc


def start_log(provider_key):
	log = frappe.new_doc("MDM Sync Log")
	log.provider = provider_key
	log.status = "Running"
	log.started_at = now_datetime()
	log.insert(ignore_permissions=True)
	return log


def finish_log(log):
	log.status = "Failed" if (log.failed_count or 0) else "Completed"
	log.finished_at = now_datetime()
	log.save(ignore_permissions=True)


def fail_log(log, exc, provider_key):
	log.status = "Failed"
	log.error_message = frappe.get_traceback()
	log.finished_at = now_datetime()
	log.failed_count = (log.failed_count or 0) + 1
	log.save(ignore_permissions=True)
	# A non-retryable error (bad key, revoked access) pauses the provider until it
	# is reconfigured; a transient one leaves it eligible for the next retry.
	non_retryable = not getattr(exc, "retryable", True)
	update_provider_status(
		get_settings(), provider_key, "Failed", message=str(exc)[:1000], auth_blocked=non_retryable
	)
	frappe.db.commit()


def _track(log, result):
	action = result.get("action")
	if action == "created":
		log.created_count = (log.created_count or 0) + 1
		if result.get("discovered"):
			log.discovered_count = (log.discovered_count or 0) + 1
	elif action == "updated":
		log.updated_count = (log.updated_count or 0) + 1
	elif action == "failed":
		log.failed_count = (log.failed_count or 0) + 1
		_append_failure(log, result)


def _append_failure(log, result):
	number = log.failed_count or 1
	if number > 20:
		return
	reason = _last_line(result.get("reason")) or "Unknown error"
	message = f"{number}. {result.get('provider_id') or '?'}: {reason}"
	log.error_message = ((log.error_message or "").rstrip() + "\n" + message).strip()


def _last_line(text):
	if not text:
		return None
	lines = [line.strip() for line in str(text).splitlines() if line.strip()]
	return lines[-1] if lines else None


def update_provider_status(
	settings, provider_key, status, *, last_sync=None, message=None, auth_blocked=None
):
	"""Persist a provider's status (+ optional last_sync / auth pause) and roll up
	the overall Settings status. ``auth_blocked`` is left untouched when None."""
	settings.set(_STATUS_FIELD[provider_key], status)
	if last_sync is not None:
		settings.set(_LAST_SYNC_FIELD[provider_key], last_sync)
	if auth_blocked is not None:
		settings.set(_AUTH_BLOCK_FIELD[provider_key], 1 if auth_blocked else 0)
	# Overall status = worst of the two provider statuses.
	statuses = {settings.get("miradore_status"), settings.get("action1_status")}
	if "Failed" in statuses:
		settings.status = "Failed"
	elif "Syncing" in statuses:
		settings.status = "Syncing"
	elif "Connected" in statuses:
		settings.status = "Connected"
	if message is not None:
		settings.status_message = message
	settings.save(ignore_permissions=True)
	frappe.db.commit()
