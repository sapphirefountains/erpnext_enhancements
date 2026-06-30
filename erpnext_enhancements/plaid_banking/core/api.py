# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted endpoints for the Plaid bank-balance integration.

Two trust tiers, enforced at the top of every method (whitelisted methods are
callable directly, so the RPC gate is the only access boundary):

* **read** — the Bank Balances widget feed: ``{System Manager, Accounts Manager,
  Accounts User}``. Returns balances + masks + freshness; never tokens/secrets.
* **connect** — anything that spends a Plaid call or writes the high-value access
  token (link/exchange/disconnect/refresh/test): ``{System Manager, Accounts
  Manager}`` only.
"""

import frappe

from erpnext_enhancements.plaid_banking.core import connect as connect_flow
from erpnext_enhancements.plaid_banking.core.balances import read_cache, refresh_balances
from erpnext_enhancements.plaid_banking.core.client import PlaidClient, PlaidError
from erpnext_enhancements.plaid_banking.core.utils import (
	clear_access_token,
	error_snippet,
	get_secret,
	get_settings,
	is_enabled,
	update_settings_status,
)

FINANCE_READ_ROLES = ("System Manager", "Accounts Manager", "Accounts User")
FINANCE_CONNECT_ROLES = ("System Manager", "Accounts Manager")


def _require_finance_operator(connect=False):
	frappe.only_for(FINANCE_CONNECT_ROLES if connect else FINANCE_READ_ROLES)


@frappe.whitelist()
def get_bank_balances():
	"""Read-only widget feed (role-gated). Returns the cached snapshot + freshness
	+ connection status. Never returns access_token / secret / client_id."""
	_require_finance_operator()
	settings = get_settings()
	if not is_enabled(settings):
		return {"enabled": False}
	snapshot = read_cache()
	return {
		"enabled": True,
		"status": settings.plaid_status or "Not Connected",
		"status_message": settings.plaid_status_message,
		"reconnect_required": settings.plaid_status == "Reconnect Required"
		or bool(settings.plaid_auth_blocked),
		"institution_name": settings.plaid_institution_name or snapshot.get("institution_name"),
		"accounts": snapshot.get("accounts", []),
		"last_sync": str(settings.plaid_last_sync) if settings.plaid_last_sync else snapshot.get("fetched_at"),
	}


@frappe.whitelist()
def create_link_token():
	"""Start the connect flow — returns ``{link_token, reconnect}`` for Plaid Link."""
	_require_finance_operator(connect=True)
	return connect_flow.create_link_token()


@frappe.whitelist()
def exchange_public_token(public_token):
	"""Finish the connect flow — store the encrypted access token + item id."""
	_require_finance_operator(connect=True)
	return connect_flow.exchange_public_token(public_token)


@frappe.whitelist()
def refresh_now():
	"""Manual balance refresh (widget + Settings button). Spends a Plaid call."""
	_require_finance_operator(connect=True)
	settings = get_settings()
	if settings.plaid_auth_blocked:
		return {"ok": False, "reconnect_required": True, "message": "Reconnect the bank first."}
	try:
		snapshot = refresh_balances(settings)
	except PlaidError as exc:
		return {"ok": False, "message": error_snippet(str(exc), 300)}
	return {"ok": True, "accounts": snapshot["accounts"], "last_sync": snapshot["fetched_at"]}


@frappe.whitelist()
def disconnect():
	"""Revoke the Item at Plaid (best-effort) and clear the stored token."""
	_require_finance_operator(connect=True)
	settings = get_settings()
	access_token = get_secret(settings, "plaid_access_token")
	if access_token:
		try:
			PlaidClient(settings).item_remove(access_token)
		except PlaidError:
			pass  # best-effort revoke; clear locally regardless
	clear_access_token(settings, "Disconnected from Plaid.")
	return {"disconnected": True}


@frappe.whitelist()
def test_connection():
	"""Validate the connection: item_get when linked, else a link-token round-trip
	(validates the API keys). Lifts the auth pause on success."""
	_require_finance_operator(connect=True)
	settings = get_settings()
	access_token = get_secret(settings, "plaid_access_token")
	try:
		if access_token:
			PlaidClient(settings).item_get(access_token)
			update_settings_status("Connected", message="Test Connection OK.", plaid_auth_blocked=0)
		else:
			connect_flow.create_link_token()  # exercises client_id/secret
			update_settings_status("Not Connected", message="API keys OK — connect a bank.")
	except PlaidError as exc:
		update_settings_status("Error", message=error_snippet(str(exc), 300))
		return {"ok": False, "message": error_snippet(str(exc), 300)}
	return {"ok": True, "message": "Connection OK."}
