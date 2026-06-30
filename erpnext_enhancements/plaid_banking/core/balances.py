# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Balance fetch + durable cache for the Plaid integration.

``refresh_balances`` is the ONLY function that calls ``/accounts/balance/get`` —
both the scheduler and the manual "Refresh now" route through it. Results are
normalized and written to the durable ``Bank Balance Snapshot`` single doctype;
the dashboard widget reads that cache and never calls Plaid on render. A
non-retryable Plaid error pauses the integration (``plaid_auth_blocked``) instead
of retrying, mirroring the MDM auth-block pattern.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import now_datetime

from erpnext_enhancements.plaid_banking.core.client import PlaidClient, PlaidError
from erpnext_enhancements.plaid_banking.core.constants import (
	NONRETRYABLE_CONFIG_ERRORS,
	NONRETRYABLE_ITEM_ERRORS,
	SNAPSHOT_DOCTYPE,
)
from erpnext_enhancements.plaid_banking.core.utils import (
	error_snippet,
	get_secret,
	get_settings,
	update_settings_status,
)


def refresh_balances(settings=None) -> dict:
	"""Pull balances, normalize, write the durable cache, stamp status.

	Raises if there is no connection. On a Plaid error, records the condition on
	Settings (status + auth_blocked when non-retryable) and re-raises.
	"""
	settings = settings or get_settings()
	access_token = get_secret(settings, "plaid_access_token")
	if not access_token:
		frappe.throw("No Plaid connection. Connect a bank first.")

	try:
		data = PlaidClient(settings).get_balances(access_token)
	except PlaidError as exc:
		_handle_plaid_error(settings, exc)
		raise

	accounts = [_normalize_account(a) for a in (data.get("accounts") or [])]
	snapshot = {
		"accounts": accounts,
		"institution_name": settings.plaid_institution_name or "",
		"fetched_at": str(now_datetime()),
	}
	_write_cache(snapshot)
	update_settings_status(
		"Connected",
		message="Balances refreshed.",
		plaid_last_sync=now_datetime(),
		plaid_auth_blocked=0,
	)
	return snapshot


def _normalize_account(account: dict) -> dict:
	"""Reduce a Plaid account object to the display fields the widget renders.

	``mask`` is the last 4 digits — display only, not a secret.
	"""
	balances = account.get("balances") or {}
	return {
		"account_id": account.get("account_id"),
		"name": account.get("official_name") or account.get("name"),
		"mask": account.get("mask"),
		"subtype": account.get("subtype"),
		"type": account.get("type"),
		"available": balances.get("available"),
		"current": balances.get("current"),
		"currency": balances.get("iso_currency_code") or "USD",
	}


def _handle_plaid_error(settings, exc: PlaidError) -> None:
	"""Translate a Plaid error into Settings status + the pause flag.

	Non-retryable Item errors → "Reconnect Required" + pause. Bad keys → "Error" +
	pause. Anything else (transient 5xx / rate limit) → "Error" but stay retryable.
	Never logs the access token; only ``error_snippet`` of the message.
	"""
	code = exc.error_code
	if code in NONRETRYABLE_ITEM_ERRORS:
		update_settings_status(
			"Reconnect Required",
			message="Bank connection needs re-authentication — open Plaid Settings and Reconnect Bank.",
			plaid_auth_blocked=1,
		)
	elif code in NONRETRYABLE_CONFIG_ERRORS:
		update_settings_status(
			"Error",
			message=f"Plaid configuration error ({code}). Check the client id / secret / environment.",
			plaid_auth_blocked=1,
		)
	else:
		update_settings_status(
			"Error",
			message=error_snippet(str(exc), 300),
		)


def _write_cache(snapshot: dict) -> None:
	"""Upsert the single ``Bank Balance Snapshot`` row (durable cache)."""
	doc = frappe.get_single(SNAPSHOT_DOCTYPE)
	doc.snapshot_json = json.dumps(snapshot.get("accounts") or [])
	doc.institution_name = snapshot.get("institution_name") or ""
	doc.fetched_at = snapshot.get("fetched_at")
	doc.save(ignore_permissions=True)
	frappe.db.commit()


def read_cache() -> dict:
	"""Return the cached snapshot ``{accounts, institution_name, fetched_at}``."""
	doc = frappe.get_single(SNAPSHOT_DOCTYPE)
	try:
		accounts = json.loads(doc.snapshot_json or "[]")
	except (ValueError, TypeError):
		accounts = []
	return {
		"accounts": accounts,
		"institution_name": doc.institution_name or "",
		"fetched_at": str(doc.fetched_at) if doc.fetched_at else None,
	}
