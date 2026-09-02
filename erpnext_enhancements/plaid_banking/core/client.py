# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Plaid REST client built on ``requests`` -- no third-party SDK.

ERPNext's own connector (``erpnext_integrations/doctype/plaid_settings/
plaid_connector.py``) uses ``plaid-python``, and because it is an erpnext
dependency the package is present on any host that runs erpnext. This module
still does not import it, deliberately:

* the widget needs exactly three POSTs (``/accounts/balance/get``,
  ``/accounts/get``, ``/item/get``), each a JSON body with the keys in it -- an SDK
  adds nothing to that but a model layer to keep in step;
* the SDK's presence is erpnext's business: its pinned major has changed before
  (``plaid-python~=7.2.1`` today) and an erpnext upgrade that moves it must not be
  able to break a dashboard tile;
* the sibling Stripe and QuickBooks modules are hand-rolled on ``requests`` for the
  same host-can't-pip-install reason (ADR 0004), and one client shape across the
  three is worth more than a saved hundred lines.

Plaid authenticates by placing ``client_id`` + ``secret`` in the JSON body of every
POST (not a header); all endpoints are POST ``application/json``. Keys and the
environment are read from the NATIVE ``Plaid Settings``; access tokens are passed
in per call because they are per Bank.

**Logging discipline:** ``secret`` and ``access_token`` live only inside the
request body -- never in a raised message, never passed to ``frappe.log_error``.
The only text logged on failure is ``error_snippet(error_message)``.
"""

from __future__ import annotations

import frappe
import requests

from erpnext_enhancements.plaid_banking.core.constants import (
	ACCOUNTS_BALANCE_GET,
	ACCOUNTS_GET,
	ENVIRONMENT_BASE_URLS,
	ITEM_GET,
	TIMEOUT,
)
from erpnext_enhancements.plaid_banking.core.utils import (
	error_snippet,
	get_credentials,
	get_environment,
	get_native_settings,
)


class PlaidError(frappe.ValidationError):
	"""Raised on a Plaid REST/transport failure. Carries ``error_code`` + ``status_code``."""

	def __init__(self, message, *, error_code=None, status_code=None):
		super().__init__(message)
		self.error_code = error_code
		self.status_code = status_code


class PlaidClient:
	"""Thin wrapper over the Plaid REST endpoints the widget uses.

	Takes the NATIVE Plaid Settings document (loaded once per client so a
	multi-bank refresh reads the keys once).
	"""

	def __init__(self, native_settings=None):
		self.native = native_settings or get_native_settings()

	def get_base_url(self) -> str:
		return ENVIRONMENT_BASE_URLS[get_environment(self.native)]

	def _auth_body(self) -> dict:
		client_id, secret = get_credentials(self.native)  # throws if missing
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
			raise PlaidError(f"Plaid request failed: {error_snippet(str(exc), 200)}") from None
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

	def get_balances(self, access_token: str) -> dict:
		"""POST /accounts/balance/get -> live balances for one Bank's Item."""
		return self._request(ACCOUNTS_BALANCE_GET, {"access_token": access_token})

	def get_accounts(self, access_token: str) -> dict:
		"""POST /accounts/get -> the Item's accounts (ids, names, masks; no live balances)."""
		return self._request(ACCOUNTS_GET, {"access_token": access_token})

	def item_get(self, access_token: str) -> dict:
		"""POST /item/get -> Item metadata (used by Test Connection)."""
		return self._request(ITEM_GET, {"access_token": access_token})
