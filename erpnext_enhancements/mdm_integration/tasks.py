"""Scheduler entry points for the MDM Integration (registered hourly in hooks.py).

Thin guard-clause wrappers; the real work lives in ``client`` / ``sync``:
``sync_devices`` (pull each enabled provider, throttled), ``refresh_action1_token``
(keep the Action1 OAuth token alive), and ``retry_failed_syncs``.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from erpnext_enhancements.mdm_integration.client import MDMProviderError
from erpnext_enhancements.mdm_integration.sync import auth_blocked, retry_failed, run_device_sync
from erpnext_enhancements.mdm_integration.utils import enabled_providers, get_settings

_LAST_SYNC_FIELD = {"Miradore": "miradore_last_sync", "Action1": "action1_last_sync"}


def sync_devices():
	"""Hourly: pull each enabled provider, throttled to ``sync_poll_minutes``."""
	settings = get_settings()
	poll_minutes = settings.sync_poll_minutes or 30
	for provider_key in enabled_providers(settings):
		if auth_blocked(settings, provider_key):
			continue  # paused after a non-retryable auth failure — wait for reconfig
		last = settings.get(_LAST_SYNC_FIELD[provider_key])
		if last:
			next_run = add_to_date(get_datetime(last), minutes=poll_minutes, as_datetime=True)
			if next_run > now_datetime():
				continue  # throttled — synced recently enough
		try:
			run_device_sync(provider_key)
		except MDMProviderError:
			pass  # recorded on the Sync Log + provider status_message; pauses if permanent
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"MDM scheduled sync failed: {provider_key}")


def refresh_action1_token():
	"""Hourly: refresh the Action1 OAuth token before it lapses (Live mode only)."""
	settings = get_settings()
	if (settings.provider_mode or "Mock") != "Live" or not settings.get("action1_enabled"):
		return
	if not settings.get("action1_client_id"):
		return
	if auth_blocked(settings, "Action1"):
		return  # credentials known-bad — wait for reconfiguration, don't hammer
	expires = settings.get("action1_token_expires_at")
	# Refresh when missing or within 10 min of expiry (wider than the 5-min margin
	# baked into the stored expiry, so the hourly job never lets it lapse).
	if expires and get_datetime(expires) > add_to_date(now_datetime(), minutes=10, as_datetime=True):
		return
	try:
		from erpnext_enhancements.mdm_integration.client import Action1Provider

		Action1Provider(settings)._refresh_token()
	except MDMProviderError:
		pass  # surfaced + paused (if permanent) by the device-sync path
	except Exception:
		frappe.log_error(frappe.get_traceback(), "MDM Action1 token refresh failed")


def retry_failed_syncs():
	"""Hourly: re-run MDM Sync Logs left Failed (honours Settings.retry_limit)."""
	retry_failed()
