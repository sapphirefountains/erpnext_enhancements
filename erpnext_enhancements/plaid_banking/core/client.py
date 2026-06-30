# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Plaid REST client built on ``requests`` — no third-party SDK.

The host is a managed server where PyPI packages can't be installed, so (like the
Stripe and QuickBooks Online modules) everything Plaid-facing is hand-rolled on
top of ``requests`` (a Frappe dependency). Plaid authenticates by placing
``client_id`` + ``secret`` in the JSON body of every POST (not a header); all
endpoints are POST ``application/json``.

Unlike QuickBooks there is no OAuth refresh loop — the Plaid access_token is
long-lived — so a non-retryable error is surfaced via :class:`PlaidError`
(carrying ``error_code``) and the caller *pauses* the integration rather than
retrying, mirroring the MDM auth-block pattern that ended the QBO 401 storm.

**Logging discipline:** ``secret`` and ``access_token`` live only inside the
request body — never in a raised message, never passed to ``frappe.log_error``.
The only text logged on failure is ``error_snippet(error_message)``.
"""

from __future__ import annotations

import frappe
import requests

from erpnext_enhancements.plaid_banking.core.constants import (
	ACCOUNTS_BALANCE_GET,
	CLIENT_NAME,
	COUNTRY_CODES,
	ENVIRONMENT_BASE_URLS,
	ITEM_GET,
	ITEM_REMOVE,
	LANGUAGE,
	LINK_TOKEN_CREATE,
	PLAID_PRODUCTS,
	PUBLIC_TOKEN_EXCHANGE,
	TIMEOUT,
)
from erpnext_enhancements.plaid_banking.core.utils import (
	error_snippet,
	get_credentials,
	get_settings,
)


class PlaidError(frappe.ValidationError):
	"""Raised on a Plaid REST/transport failure. Carries ``error_code`` + ``status_code``."""

	def __init__(self, message, *, error_code=None, status_code=None):
		super().__init__(message)
		self.error_code = error_code
		self.status_code = status_code


class PlaidClient:
	"""Thin wrapper over the Plaid REST endpoints used by this integration."""

	def __init__(self, settings=None):
		self.settings = settings or get_settings()

	def get_base_url(self) -> str:
		env = self.settings.plaid_environment or "Sandbox"
		return ENVIRONMENT_BASE_URLS.get(env, ENVIRONMENT_BASE_URLS["Sandbox"])

	def _auth_body(self) -> dict:
		client_id, secret = get_credentials(self.settings)  # throws if missing
		return {"client_id": client_id, "secret": secret}

	def _request(self, path: str, body: dict) -> dict:
		"""POST ``{auth + body}`` as JSON; return parsed JSON or raise PlaidError.

		Mirrors ``stripe_payments/core/client._request``: wraps
		``requests.RequestException`` and raises a typed error on status >= 400.
		Parses Plaid's ``{error_code, error_message}`` envelope and attaches
		``error_code`` so callers can branch on the non-retryable sets. Never puts
		the request body (secret / access_token) in the raised message or any log.
		"""
		payload = {**self._auth_body(), **body}
		try:
			response = requests.post(
				f"{self.get_base_url()}{path}",
				json=payload,
				headers={"Content-Type": "application/json"},
				timeout=TIMEOUT,
			)
		except requests.RequestException as exc:
			raise PlaidError(f"Plaid request failed: {error_snippet(str(exc), 200)}")
		if response.status_code >= 400:
			data = {}
			try:
				data = response.json() if response.text else {}
			except ValueError:
				data = {}
			raise PlaidError(
				f"Plaid API error ({response.status_code}/{data.get('error_code')}): "
				f"{error_snippet(data.get('error_message') or response.text)}",
				error_code=data.get("error_code"),
				status_code=response.status_code,
			)
		return response.json()

	# ---- endpoint wrappers -------------------------------------------------

	def create_link_token(self, *, user_client_id: str, access_token: str | None = None) -> dict:
		"""POST /link/token/create. With ``access_token`` set → Link update mode
		(reconnect an existing Item; products are omitted in update mode)."""
		body = {
			"client_name": CLIENT_NAME,
			"language": LANGUAGE,
			"country_codes": COUNTRY_CODES,
			"user": {"client_user_id": user_client_id},
			"products": PLAID_PRODUCTS,
			# "redirect_uri": <registered OAuth redirect — required only if KeyBank
			# uses Plaid's OAuth flow; register it in the Plaid dashboard first>.
		}
		if access_token:
			body["access_token"] = access_token
			body.pop("products", None)
		return self._request(LINK_TOKEN_CREATE, body)

	def exchange_public_token(self, public_token: str) -> dict:
		"""POST /item/public_token/exchange → ``{access_token, item_id}``."""
		return self._request(PUBLIC_TOKEN_EXCHANGE, {"public_token": public_token})

	def get_balances(self, access_token: str) -> dict:
		"""POST /accounts/balance/get → live account balances."""
		return self._request(ACCOUNTS_BALANCE_GET, {"access_token": access_token})

	def item_get(self, access_token: str) -> dict:
		"""POST /item/get → Item metadata (used by Test Connection)."""
		return self._request(ITEM_GET, {"access_token": access_token})

	def item_remove(self, access_token: str) -> dict:
		"""POST /item/remove → invalidate the access_token at Plaid (disconnect)."""
		return self._request(ITEM_REMOVE, {"access_token": access_token})
