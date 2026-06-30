# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Plaid Link connect-flow logic (non-whitelisted; gating lives in ``api``).

Two steps: create a Link token (frontend opens Plaid Link with it), then exchange
the resulting public token for a long-lived access token and persist it. A stable,
non-PII ``client_user_id`` identifies this deployment's linker.
"""

from __future__ import annotations

import frappe

from erpnext_enhancements.plaid_banking.core.client import PlaidClient
from erpnext_enhancements.plaid_banking.core.utils import (
	get_secret,
	get_settings,
	is_enabled,
	set_secret,
)

# Stable, non-PII identifier for this deployment's linker (Plaid requires a
# user.client_user_id; we link a single shared institution, not per-end-user).
LINK_USER_ID = "sapphire-erp"


def create_link_token() -> dict:
	"""Return ``{link_token, reconnect}``. Reconnect (update) mode when an access
	token already exists."""
	settings = get_settings()
	if not is_enabled(settings):
		frappe.throw("Enable Plaid in Plaid Settings first.")
	access_token = get_secret(settings, "plaid_access_token")
	result = PlaidClient(settings).create_link_token(
		user_client_id=LINK_USER_ID,
		access_token=access_token or None,
	)
	return {"link_token": result["link_token"], "reconnect": bool(access_token)}


def exchange_public_token(public_token: str) -> dict:
	"""Exchange the public token, store the encrypted access token + item id."""
	settings = get_settings()
	result = PlaidClient(settings).exchange_public_token(public_token)
	set_secret(settings, "plaid_access_token", result["access_token"])
	settings.plaid_item_id = result.get("item_id") or ""
	settings.plaid_status = "Connected"
	settings.plaid_status_message = "Bank connected via Plaid Link."
	settings.plaid_auth_blocked = 0
	settings.save(ignore_permissions=True)
	frappe.db.commit()
	# Warm the cache (and surface any immediate error) off the request path.
	frappe.enqueue(
		"erpnext_enhancements.plaid_banking.core.balances.refresh_balances",
		queue="short",
		enqueue_after_commit=True,
	)
	return {"connected": True}
