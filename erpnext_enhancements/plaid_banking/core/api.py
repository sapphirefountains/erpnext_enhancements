# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted endpoints for the Bank Balances widget.

Two trust tiers, enforced at the top of every method (whitelisted methods are
callable directly, so the RPC gate is the only access boundary):

* **read** -- the Bank Balances widget feed: ``{System Manager, Accounts Manager,
  Accounts User}``. Returns balances + masks + freshness per bank; never tokens
  or keys.
* **connect** -- anything that spends a Plaid call or writes a Bank Account's
  Plaid link (refresh / test / map / absorb): ``{System Manager, Accounts
  Manager}`` only.

There is no link / exchange / disconnect endpoint any more: linking a bank is the
native ERPNext flow (Plaid Settings -> "Link a new bank account"; Bank ->
"Refresh Plaid Link"), and the token it stores on the Bank is what these read.
"""

import frappe

from erpnext_enhancements.plaid_banking.core import link_accounts
from erpnext_enhancements.plaid_banking.core.balances import (
	NO_LINK_MESSAGE,
	read_cache,
	refresh_balances,
)
from erpnext_enhancements.plaid_banking.core.client import PlaidClient, PlaidError
from erpnext_enhancements.plaid_banking.core.utils import (
	error_snippet,
	get_credentials,
	get_settings,
	is_enabled,
	linked_banks,
	update_settings_status,
)

FINANCE_READ_ROLES = ("System Manager", "Accounts Manager", "Accounts User")
FINANCE_CONNECT_ROLES = ("System Manager", "Accounts Manager")


def _require_finance_operator(connect=False):
	frappe.only_for(FINANCE_CONNECT_ROLES if connect else FINANCE_READ_ROLES)


@frappe.whitelist()
def get_bank_balances():
	"""Read-only widget feed (role-gated). Returns the cached snapshot grouped by
	bank + freshness + status. Never returns a token / secret / client id."""
	_require_finance_operator()
	settings = get_settings()
	if not is_enabled(settings):
		return {"enabled": False}
	snapshot = read_cache()
	banks = snapshot.get("banks", [])
	return {
		"enabled": True,
		"status": settings.plaid_status or "Not Connected",
		"status_message": settings.plaid_status_message,
		"paused": bool(settings.plaid_auth_blocked),
		"reconnect_required": any(b.get("status") == "Reconnect Required" for b in banks),
		"banks": banks,
		"last_sync": str(settings.plaid_last_sync)
		if settings.plaid_last_sync
		else snapshot.get("fetched_at"),
	}


@frappe.whitelist()
def refresh_now():
	"""Manual balance refresh (widget + Settings button). Spends one Plaid call per
	linked bank. Deliberately NOT refused while paused: a human pressing the button
	is the retry the pause was waiting for, and a success lifts it."""
	_require_finance_operator(connect=True)
	settings = get_settings()
	if not linked_banks():
		update_settings_status("Not Connected", message=NO_LINK_MESSAGE)
		return {"ok": False, "message": NO_LINK_MESSAGE}
	try:
		snapshot = refresh_balances(settings)
	except PlaidError as exc:
		return {"ok": False, "message": error_snippet(str(exc), 300)}
	return {"ok": True, "banks": snapshot["banks"], "last_sync": snapshot["fetched_at"]}


@frappe.whitelist()
def test_connection():
	"""Validate the setup without spending a balance call.

	Keys present + no linked bank: reports that and points at the native Link --
	there is no cheap key-only probe left now that we create no Link tokens, and a
	missing key throws before any request. With links: ``/item/get`` per bank; all
	OK lifts the auth pause, any failure is reported per bank and does not pause.
	"""
	_require_finance_operator(connect=True)
	try:
		get_credentials()
	except Exception as exc:
		message = error_snippet(str(exc), 300)
		update_settings_status("Error", message=message)
		return {"ok": False, "message": message}

	banks = linked_banks()
	if not banks:
		message = "API keys present. No bank is linked yet: use 'Link a new bank account' on the native Plaid Settings."
		update_settings_status("Not Connected", message=message)
		return {"ok": True, "message": message, "banks": []}

	client = PlaidClient()
	report = []
	for row in banks:
		try:
			client.item_get(row["access_token"])
			report.append({"bank": row["bank"], "ok": True, "message": None})
		except PlaidError as exc:
			report.append({"bank": row["bank"], "ok": False, "message": error_snippet(str(exc), 300)})

	failed = [r for r in report if not r["ok"]]
	if failed:
		names = ", ".join(r["bank"] for r in failed)
		message = f"Connection failed for {names}."
		update_settings_status("Error", message=message)
		return {"ok": False, "message": message, "banks": report}
	message = f"Test Connection OK for {len(report)} bank(s)."
	update_settings_status("Connected", message=message, plaid_auth_blocked=0)
	return {"ok": True, "message": message, "banks": report}


# ---- mapping helpers (see core/link_accounts.py) ---------------------------


@frappe.whitelist()
def mapping_overview():
	"""Per linked bank: its Plaid accounts and our company Bank Accounts (for the dialog)."""
	_require_finance_operator(connect=True)
	return link_accounts.mapping_overview()


@frappe.whitelist()
def list_plaid_accounts(bank):
	"""The Plaid accounts behind one linked Bank (ids, names, masks; no balances)."""
	_require_finance_operator(connect=True)
	return link_accounts.list_plaid_accounts(bank)


@frappe.whitelist()
def map_plaid_account(bank_account, account_id, mask=None, start_date=None, bank=None):
	"""Stamp a Plaid account id onto an existing company Bank Account. ``bank`` is the
	Bank that holds the token when native Link named it differently from the master's."""
	_require_finance_operator(connect=True)
	return link_accounts.map_plaid_account(
		bank_account, account_id, mask=mask, start_date=start_date, bank=bank or None
	)


@frappe.whitelist()
def absorb_native_duplicate(duplicate, into):
	"""Move a native-created duplicate's Plaid link onto our master and delete the duplicate."""
	_require_finance_operator(connect=True)
	return link_accounts.absorb_native_duplicate(duplicate, into)


@frappe.whitelist()
def prune_link_created_gl_accounts(bank):
	"""Delete the unused GL Accounts a native "Refresh Plaid Link" left behind for one Bank."""
	_require_finance_operator(connect=True)
	return link_accounts.prune_link_created_gl_accounts(bank)
