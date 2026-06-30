# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Scheduled job for the Plaid integration.

Registered in ``hooks.py`` ``scheduler_events["hourly"]``. Fires hourly but
self-throttles to ``refresh_poll_minutes`` (default 240 = ~4h; balances change
slowly) and skips entirely while ``plaid_auth_blocked`` is set — so a dead
connection or bad keys can never produce a retry-storm (the QBO ``cdc_poll``
throttle + the MDM auth-block guard, combined).
"""

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from erpnext_enhancements.plaid_banking.core.balances import refresh_balances
from erpnext_enhancements.plaid_banking.core.client import PlaidError
from erpnext_enhancements.plaid_banking.core.utils import get_settings, is_enabled


def scheduled_balance_refresh():
	"""Refresh cached balances if enabled, connected, not paused, and not throttled."""
	settings = get_settings()
	if not is_enabled(settings) or not settings.plaid_item_id:
		return
	if settings.plaid_auth_blocked:
		return  # paused after a non-retryable failure — wait for reconnect (no storm)

	last = settings.plaid_last_sync
	if last:
		throttle = settings.refresh_poll_minutes or 240
		next_run = add_to_date(get_datetime(last), minutes=throttle, as_datetime=True)
		if next_run > now_datetime():
			return  # throttled

	try:
		refresh_balances(settings)
	except PlaidError:
		# Already recorded on Settings status / auth_blocked by refresh_balances;
		# don't spam the Error Log.
		pass
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Plaid scheduled balance refresh failed")
