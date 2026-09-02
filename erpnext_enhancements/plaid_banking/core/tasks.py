# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Scheduled job for the Bank Balances widget.

Registered in ``hooks.py`` ``scheduler_events["hourly"]``. Fires hourly but
self-throttles to ``refresh_poll_minutes`` (default 240 = ~4h; balances change
slowly) and skips entirely while ``plaid_auth_blocked`` is set -- so bad keys
can never produce a retry-storm (the QBO ``cdc_poll`` throttle + the MDM
auth-block guard, combined). A single bank needing re-authentication does not
pause the job: its one refused call per pass is the cost of keeping the other
banks' numbers fresh.
"""

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from erpnext_enhancements.plaid_banking.core.balances import refresh_balances
from erpnext_enhancements.plaid_banking.core.client import PlaidError
from erpnext_enhancements.plaid_banking.core.utils import get_settings, is_enabled, linked_banks


def scheduled_balance_refresh():
	"""Refresh cached balances if enabled, not paused, not throttled, and something is linked."""
	settings = get_settings()
	if not is_enabled(settings):
		return
	if settings.plaid_auth_blocked:
		return  # paused after a config failure -- wait for a human (no storm)

	last = settings.plaid_last_sync
	if last:
		throttle = settings.refresh_poll_minutes or 240
		next_run = add_to_date(get_datetime(last), minutes=throttle, as_datetime=True)
		if next_run > now_datetime():
			return  # throttled

	if not linked_banks():
		return  # nothing linked natively yet; the widget says so from the cache

	try:
		refresh_balances(settings)
	except PlaidError:
		# Already recorded on Settings status / auth_blocked by refresh_balances;
		# don't spam the Error Log.
		pass
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Plaid scheduled balance refresh failed")
