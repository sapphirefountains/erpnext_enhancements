from __future__ import annotations

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.client import QuickBooksClient
from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.sync import retry_failed, run_cdc
from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.utils import get_settings


def refresh_token_if_needed():
	settings = get_settings()
	if not settings.realm_id:
		return
	if not settings.token_expires_at:
		QuickBooksClient(settings).refresh_access_token()
		return
	if get_datetime(settings.token_expires_at) <= add_to_date(now_datetime(), minutes=10, as_datetime=True):
		QuickBooksClient(settings).refresh_access_token()


def cdc_poll():
	settings = get_settings()
	if settings.last_cdc_sync:
		next_run_at = add_to_date(
			get_datetime(settings.last_cdc_sync),
			minutes=settings.cdc_poll_minutes or 15,
			as_datetime=True,
		)
		if next_run_at > now_datetime():
			return
	run_cdc()


def retry_failed_syncs():
	retry_failed()
