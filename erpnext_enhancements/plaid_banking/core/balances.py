# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Balance fetch + durable cache for the Bank Balances widget.

``refresh_balances`` is the ONLY function that calls ``/accounts/balance/get`` --
both the scheduler and the manual "Refresh now" route through it. It walks every
Bank the native Plaid Link flow has linked, one call per bank, and writes one
normalised snapshot to the durable ``Bank Balance Snapshot`` single; the widget
reads that cache and never calls Plaid on render.

Error policy, per bank, because the keys are shared but the Items are not:

* an Item-level non-retryable code (login required, revoked token ...) marks THAT
  bank "Reconnect Required" and the loop carries on -- one dead link must not hide
  the other two banks' numbers;
* a config-level code (bad keys) pauses the whole widget (``plaid_auth_blocked``)
  and stops, since every remaining call would fail the same way;
* anything else (5xx, rate limit, network) marks that bank "Error", stays
  retryable, and the loop carries on.
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
	get_credentials,
	get_settings,
	linked_banks,
	update_settings_status,
)

MISSING_KEYS_CODE = "MISSING_API_KEYS"

NO_LINK_MESSAGE = (
	"No bank is linked to Plaid. Link one in the native Plaid Settings "
	"(ERPNext Integrations) with 'Link a new bank account'."
)


def reconnect_message(bank: str) -> str:
	return f"{bank} needs re-authentication. Open the Bank record '{bank}' and press 'Refresh Plaid Link'."


def refresh_balances(settings=None) -> dict:
	"""Pull balances for every linked bank, write the cache, stamp status.

	Returns the snapshot ``{"banks": [...], "fetched_at": ...}``. Raises
	:class:`PlaidError` only for a config-level failure (bad keys), after recording
	the pause on Settings; per-bank failures are recorded in the snapshot and do
	not raise.
	"""
	settings = settings or get_settings()
	banks = linked_banks()
	if not banks:
		snapshot = {"banks": [], "fetched_at": str(now_datetime())}
		_write_cache(snapshot)
		update_settings_status("Not Connected", message=NO_LINK_MESSAGE)
		return snapshot

	client = PlaidClient()
	# Blank native keys with a linked Bank are a config failure exactly like bad keys,
	# and must be reported as one: ``get_credentials`` throws a plain ValidationError
	# from inside the first request, which no ``except PlaidError`` below catches --
	# the status would never be stamped, the throttle anchor would never move, and the
	# hourly job would write an Error Log every tick. Check once, pause, raise PlaidError.
	try:
		get_credentials(client.native)
	except Exception as exc:
		message = error_snippet(str(exc), 300)
		update_settings_status("Error", message=message, plaid_auth_blocked=1)
		raise PlaidError(message, error_code=MISSING_KEYS_CODE) from None

	results = []
	succeeded = 0
	for row in banks:
		bank = row["bank"]
		try:
			data = client.get_balances(row["access_token"])
		except PlaidError as exc:
			code = exc.error_code
			if code in NONRETRYABLE_CONFIG_ERRORS:
				update_settings_status(
					"Error",
					message=f"Plaid configuration error ({code}). Check the client id / secret / "
					"environment on the native Plaid Settings.",
					plaid_auth_blocked=1,
				)
				raise
			if code in NONRETRYABLE_ITEM_ERRORS:
				results.append(_bank_entry(bank, "Reconnect Required", reconnect_message(bank)))
			else:
				results.append(_bank_entry(bank, "Error", error_snippet(str(exc), 300)))
			continue
		succeeded += 1
		accounts = [_normalize_account(a) for a in (data.get("accounts") or [])]
		results.append(_bank_entry(bank, "Connected", None, accounts))

	snapshot = {"banks": results, "fetched_at": str(now_datetime())}
	_write_cache(snapshot)
	_stamp_status(results, succeeded)
	return snapshot


def _bank_entry(bank: str, status: str, message: str | None, accounts: list | None = None) -> dict:
	return {"bank": bank, "status": status, "message": message, "accounts": accounts or []}


def _stamp_status(results: list[dict], succeeded: int) -> None:
	"""Settings status after a multi-bank pass.

	Connected when at least one bank answered (that is a usable widget); the
	message names the banks that did not. With no success at all the status is
	the worst thing seen. ``plaid_last_sync`` (the throttle anchor) only moves on
	a success, and the pause is lifted by one -- an Item error never pauses.
	"""
	failed = [r for r in results if r["status"] != "Connected"]
	if succeeded:
		if failed:
			names = ", ".join(f"{r['bank']} ({r['status']})" for r in failed)
			message = f"Balances refreshed for {succeeded} of {len(results)} banks. Attention: {names}."
		else:
			message = f"Balances refreshed for {succeeded} bank(s)."
		update_settings_status(
			"Connected",
			message=message,
			plaid_last_sync=now_datetime(),
			plaid_auth_blocked=0,
		)
		return
	if any(r["status"] == "Reconnect Required" for r in results):
		names = ", ".join(r["bank"] for r in results if r["status"] == "Reconnect Required")
		update_settings_status(
			"Reconnect Required",
			message=f"Every linked bank needs re-authentication: {names}. Re-link each on its Bank record.",
		)
		return
	update_settings_status(
		"Error", message=(results[0].get("message") if results else None) or "Refresh failed."
	)


def _normalize_account(account: dict) -> dict:
	"""Reduce a Plaid account object to the display fields the widget renders.

	``mask`` is the last 4 digits -- display only, not a secret.
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


def _write_cache(snapshot: dict) -> None:
	"""Upsert the single ``Bank Balance Snapshot`` row (durable cache)."""
	doc = frappe.get_single(SNAPSHOT_DOCTYPE)
	doc.snapshot_json = json.dumps(snapshot.get("banks") or [])
	doc.fetched_at = snapshot.get("fetched_at")
	doc.save(ignore_permissions=True)
	frappe.db.commit()


def read_cache() -> dict:
	"""Return the cached snapshot ``{banks, fetched_at}``.

	``snapshot_json`` written before the multi-bank shape held a bare list of
	accounts; those rows carry no ``bank`` key and are treated as empty rather
	than rendered under a made-up institution. The next refresh rewrites them.
	"""
	doc = frappe.get_single(SNAPSHOT_DOCTYPE)
	try:
		banks = json.loads(doc.snapshot_json or "[]")
	except (ValueError, TypeError):
		banks = []
	if not isinstance(banks, list) or any(not isinstance(b, dict) or "bank" not in b for b in banks):
		banks = []
	return {
		"banks": banks,
		"fetched_at": str(doc.fetched_at) if doc.fetched_at else None,
	}
