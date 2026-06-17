"""Scheduler entry points for the QuickBooks Online integration.

These three functions are wired to the hourly scheduler via ``hooks.py``:
``refresh_token_if_needed`` (keep OAuth alive), ``cdc_poll`` (pull QBO changes)
and ``retry_failed_syncs`` (re-run failed sync logs). They are intentionally
thin -- guard clauses and cursor checks here, real work in ``client.py`` and
``sync.py``.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from erpnext_enhancements.quickbooks_online.core.client import QuickBooksAPIError, QuickBooksClient
from erpnext_enhancements.quickbooks_online.core.sync import retry_failed, run_cdc
from erpnext_enhancements.quickbooks_online.core.utils import clear_oauth_tokens, get_settings


def refresh_token_if_needed():
	"""Hourly scheduler hook: refresh the OAuth access token before it expires.

	No-op when the integration is not connected (no realm id) or when a recorded
	expiry is still more than 10 minutes away. Otherwise (no expiry recorded, or
	the token lapses within the next 10 minutes -- a wider window than the 5-min
	margin baked into ``token_expires_at`` so the hourly job never lets a token
	lapse between runs) it refreshes via ``client.refresh_access_token``.

	If the refresh fails with ``invalid_grant`` -- the refresh token was revoked
	or expired (e.g. the user disconnected the app from Intuit's My Apps page) --
	the grant is gone on Intuit's side, so the now-dead tokens are cleared and the
	connection is marked Not Connected rather than failing this job every hour.
	Any other error propagates.
	"""
	settings = get_settings()
	if not settings.realm_id:
		return
	if settings.token_expires_at and get_datetime(settings.token_expires_at) > add_to_date(
		now_datetime(), minutes=10, as_datetime=True
	):
		return
	try:
		QuickBooksClient(settings).refresh_access_token()
	except QuickBooksAPIError as exc:
		if "invalid_grant" in str(exc):
			clear_oauth_tokens(
				get_settings(),
				message="QuickBooks disconnected: the refresh token was revoked or expired. Reconnect to resume sync.",
			)
		else:
			raise


def cdc_poll():
	"""Hourly scheduler hook: poll QBO Change Data Capture, throttled by cursor.

	Skips the poll if the configured ``cdc_poll_minutes`` interval (default 15)
	has not elapsed since the last successful ``last_cdc_sync``, so the effective
	cadence is set in Settings rather than by how often the scheduler fires.
	Delegates the actual fetch/upsert to ``sync.run_cdc``.
	"""
	settings = get_settings()
	if settings.last_cdc_sync:
		# Throttle: only run once per cdc_poll_minutes window since last sync.
		next_run_at = add_to_date(
			get_datetime(settings.last_cdc_sync),
			minutes=settings.cdc_poll_minutes or 15,
			as_datetime=True,
		)
		if next_run_at > now_datetime():
			return
	run_cdc()


def retry_failed_syncs():
	"""Hourly scheduler hook: re-run sync logs left in the Failed state.

	Delegates to ``sync.retry_failed`` (which respects Settings.retry_limit).
	"""
	retry_failed()
