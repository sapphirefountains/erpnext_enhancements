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

from erpnext_enhancements.mdm_integration.client import get_provider
from erpnext_enhancements.mdm_integration.mapping import upsert_device
from erpnext_enhancements.mdm_integration.utils import get_settings, json_dumps

# Per-provider Settings field for the "last successful sync" cursor.
_LAST_SYNC_FIELD = {"Miradore": "miradore_last_sync", "Action1": "action1_last_sync"}
_STATUS_FIELD = {"Miradore": "miradore_status", "Action1": "action1_status"}


def run_device_sync(provider_key):
	"""Pull one provider's devices and reconcile them into the registry.

	Returns the sync log name. Re-raises on hard failure after logging.
	"""
	settings = get_settings()
	provider = get_provider(provider_key, settings)
	log = start_log(provider_key)
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
		stamp = now_datetime() if log.status == "Completed" else None
		update_provider_status(
			settings,
			provider_key,
			"Failed" if log.status == "Failed" else "Connected",
			last_sync=stamp,
		)
		frappe.db.commit()
		return log.name
	except Exception as exc:
		fail_log(log, exc, provider_key)
		raise


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
	"""Re-run MDM Sync Logs left Failed, capped by Settings.retry_limit."""
	settings = get_settings()
	filters = {"status": "Failed"}
	if log_name:
		filters["name"] = log_name
	for log in frappe.get_all("MDM Sync Log", filters=filters, fields=["name", "provider", "retry_count"]):
		if (log.retry_count or 0) >= (settings.retry_limit or 3):
			continue
		frappe.db.set_value("MDM Sync Log", log.name, "retry_count", (log.retry_count or 0) + 1)
		try:
			run_device_sync(log.provider)
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
	update_provider_status(get_settings(), provider_key, "Failed", message=str(exc)[:1000])
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


def update_provider_status(settings, provider_key, status, *, last_sync=None, message=None):
	"""Persist a provider's status (+ optional last_sync) and roll up the overall
	Settings status."""
	settings.set(_STATUS_FIELD[provider_key], status)
	if last_sync is not None:
		settings.set(_LAST_SYNC_FIELD[provider_key], last_sync)
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
