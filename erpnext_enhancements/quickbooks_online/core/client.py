"""HTTP/OAuth2 client for the QuickBooks Online REST API.

This is the transport layer of the integration. It owns the full OAuth2
lifecycle (build authorization URL -> exchange code -> refresh access token)
and the authenticated request helpers (generic ``request``, plus ``query``,
``get_entity`` and ``cdc``) that ``sync.py`` and ``api.py`` build on.

Token storage is delegated to ``utils.set_secret``/``get_secret`` (encrypted
Password fields on the singleton Settings doc); ``_store_tokens`` also persists
the computed expiry and connection status. The client transparently refreshes
on a 401 and retries the request once.
"""

from __future__ import annotations

import base64
from urllib.parse import urlencode

import frappe
import requests
from frappe.utils import add_to_date, now_datetime

from erpnext_enhancements.quickbooks_online.core.constants import (
	AUTHORIZATION_URL,
	ENVIRONMENT_BASE_URLS,
	MINOR_VERSION,
	OAUTH_SCOPE,
	REVOKE_URL,
	TOKEN_URL,
)
from erpnext_enhancements.quickbooks_online.core.utils import (
	format_qbo_datetime,
	get_secret,
	get_settings,
	set_secret,
)


class QuickBooksAPIError(Exception):
	"""Raised when a QBO token or data request returns an HTTP error (>=400)."""

	pass


class QuickBooksClient:
	"""Thin wrapper around the QBO OAuth2 + REST endpoints.

	Bound to a single ``QuickBooks Online Settings`` doc (loaded lazily if not
	passed) which supplies credentials, the realm id and the Sandbox/Production
	environment. Construct per-operation; it is cheap and holds no connection.
	"""

	def __init__(self, settings=None):
		self.settings = settings or get_settings()

	def get_base_url(self):
		"""Return the API host for the configured environment (defaults Sandbox)."""
		return ENVIRONMENT_BASE_URLS.get(self.settings.environment or "Sandbox", ENVIRONMENT_BASE_URLS["Sandbox"])

	def build_authorization_url(self, state: str, environment: str | None = None):
		"""Build the Intuit consent URL the user is redirected to for OAuth2.

		``state`` is the CSRF token minted by ``api.start_oauth`` (validated on
		callback). Optionally overrides the environment in memory. Raises if the
		client id or redirect URI have not been configured on Settings.
		"""
		if environment:
			self.settings.environment = environment
		if not self.settings.client_id or not self.settings.redirect_uri:
			frappe.throw("Client ID and Redirect URI are required before connecting to QuickBooks Online.")

		return (
			AUTHORIZATION_URL
			+ "?"
			+ urlencode(
				{
					"client_id": self.settings.client_id,
					"scope": OAUTH_SCOPE,
					"redirect_uri": self.settings.redirect_uri,
					"response_type": "code",
					"state": state,
				}
			)
		)

	def exchange_code(self, code: str, realm_id: str):
		"""Exchange an OAuth2 authorization code for tokens and persist them.

		Called by ``api.oauth_callback`` after the user consents. ``realm_id`` is
		the QBO company id returned alongside the code; it is stored on Settings.
		Side effects: HTTP POST to the token endpoint, then writes access/refresh
		tokens, expiry and "Connected" status (see ``_store_tokens``).
		"""
		payload = {
			"grant_type": "authorization_code",
			"code": code,
			"redirect_uri": self.settings.redirect_uri,
		}
		data = self._token_request(payload)
		self._store_tokens(data, realm_id=realm_id)
		return data

	def refresh_access_token(self):
		"""Use the stored refresh token to obtain a new access token.

		Invoked proactively by ``tasks.refresh_token_if_needed`` (hourly
		scheduler) and reactively by ``request`` on a 401. Side effects: HTTP POST
		to the token endpoint and persistence via ``_store_tokens`` (QBO may also
		rotate the refresh token, which is then saved). Raises if no refresh token
		is stored -- the integration must be reconnected.
		"""
		refresh_token = get_secret(self.settings, "refresh_token")
		if not refresh_token:
			frappe.throw("QuickBooks Online refresh token is missing. Reconnect the integration.")

		data = self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
		self._store_tokens(data)
		return data

	def revoke_tokens(self):
		"""Revoke the OAuth2 grant at Intuit (best-effort). Return True on success.

		POSTs the refresh token -- falling back to the access token -- to Intuit's
		revocation endpoint with the same ``client_secret_basic`` auth the token
		endpoint uses; revoking the refresh token tears down the whole grant.
		Deliberately swallows every failure and returns False (nothing stored,
		missing client credentials, a non-200, or a network exception) so an
		explicit Disconnect can still clear local state and end up disconnected.
		Does not itself touch Settings -- the caller pairs this with
		``utils.clear_oauth_tokens``.
		"""
		token = get_secret(self.settings, "refresh_token") or get_secret(self.settings, "access_token")
		client_secret = get_secret(self.settings, "client_secret")
		if not token or not self.settings.client_id or not client_secret:
			return False
		basic = base64.b64encode(f"{self.settings.client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
		try:
			response = requests.post(
				REVOKE_URL,
				headers={
					"Accept": "application/json",
					"Authorization": f"Basic {basic}",
					"Content-Type": "application/json",
				},
				json={"token": token},
				timeout=30,
			)
		except Exception:
			return False
		return response.status_code == 200

	def _token_request(self, payload):
		"""POST to the Intuit token endpoint with HTTP Basic client auth.

		Shared by ``exchange_code`` and ``refresh_access_token``. The client
		id/secret are sent base64-encoded in the Authorization header (OAuth2
		client_secret_basic). Raises ``QuickBooksAPIError`` on any >=400 response.
		"""
		client_secret = get_secret(self.settings, "client_secret")
		if not self.settings.client_id or not client_secret:
			frappe.throw("QuickBooks Online Client ID and Client Secret are required.")

		basic = base64.b64encode(f"{self.settings.client_id}:{client_secret}".encode("utf-8")).decode("utf-8")
		response = requests.post(
			TOKEN_URL,
			headers={
				"Accept": "application/json",
				"Authorization": f"Basic {basic}",
				"Content-Type": "application/x-www-form-urlencoded",
			},
			data=payload,
			timeout=60,
		)
		if response.status_code >= 400:
			raise QuickBooksAPIError(f"QuickBooks token request failed: {response.status_code} {response.text}")
		return response.json()

	def _store_tokens(self, data, realm_id=None):
		"""Persist tokens, computed expiry and connection status to Settings.

		Side effects: encrypts access/refresh tokens (refresh only updated when
		present so a refresh response without one keeps the old token), stores the
		realm id when given, sets status "Connected", saves and commits.

		The stored ``token_expires_at`` is deliberately backdated by 300s (5 min)
		relative to QBO's ``expires_in`` so callers refresh slightly early and
		avoid using a token that expires mid-request.
		"""
		set_secret(self.settings, "access_token", data.get("access_token"))
		if data.get("refresh_token"):
			set_secret(self.settings, "refresh_token", data.get("refresh_token"))
		if realm_id:
			self.settings.realm_id = realm_id
		expires_in = int(data.get("expires_in") or 3600)
		# Subtract a 5-minute safety margin from the real expiry.
		self.settings.token_expires_at = add_to_date(now_datetime(), seconds=expires_in - 300, as_datetime=True)
		self.settings.status = "Connected"
		self.settings.status_message = "Connected to QuickBooks Online."
		self.settings.save(ignore_permissions=True)
		frappe.db.commit()

	def request(self, method: str, path: str, **kwargs):
		"""Make an authenticated QBO API call, refreshing the token on 401.

		Prepends the environment base URL to ``path`` and injects the bearer
		token plus the pinned ``minorversion`` query param. On a 401 it refreshes
		the access token once and retries the same request; any other >=400
		response raises ``QuickBooksAPIError``. Returns the parsed JSON body (or
		``{}`` for an empty body). Raises if no access token is stored.
		"""
		access_token = get_secret(self.settings, "access_token")
		if not access_token:
			frappe.throw("QuickBooks Online access token is missing. Connect the integration first.")

		url = f"{self.get_base_url()}{path}"
		params = kwargs.pop("params", {}) or {}
		params.setdefault("minorversion", MINOR_VERSION)
		response = requests.request(
			method,
			url,
			headers={
				"Accept": "application/json",
				"Authorization": f"Bearer {access_token}",
				"Content-Type": kwargs.pop("content_type", "application/json"),
			},
			params=params,
			timeout=kwargs.pop("timeout", 60),
			**kwargs,
		)
		# 401 => access token expired/revoked: refresh once and retry the same call.
		if response.status_code == 401:
			self.refresh_access_token()
			return self.request(method, path, **kwargs, params=params)
		if response.status_code >= 400:
			raise QuickBooksAPIError(f"QuickBooks API request failed: {response.status_code} {response.text}")
		return response.json() if response.text else {}

	def upload_attachable(self, *, file_bytes, file_name, mime_type, entity_type, qbo_id):
		"""Upload a file to QBO and attach it to ``entity_type``/``qbo_id`` (a Bill
		or Payment) via the Attachable batch-upload endpoint. Sent as
		multipart/form-data (a JSON metadata part + the binary), so — unlike
		``request`` — the Content-Type header is left for ``requests`` to set with
		the multipart boundary. Refreshes the token once on 401, like ``request``."""
		import json as _json

		access_token = get_secret(self.settings, "access_token")
		if not access_token:
			frappe.throw("QuickBooks Online access token is missing. Connect the integration first.")
		metadata = {
			"AttachableRef": [{"EntityRef": {"type": entity_type, "value": str(qbo_id)}}],
			"FileName": file_name,
			"ContentType": mime_type,
		}
		response = requests.post(
			f"{self.get_base_url()}/v3/company/{self.settings.realm_id}/upload",
			headers={"Accept": "application/json", "Authorization": f"Bearer {access_token}"},
			files={
				"file_metadata_0": ("metadata.json", _json.dumps(metadata), "application/json"),
				"file_content_0": (file_name, file_bytes, mime_type or "application/octet-stream"),
			},
			params={"minorversion": MINOR_VERSION},
			timeout=120,
		)
		if response.status_code == 401:
			self.refresh_access_token()
			return self.upload_attachable(
				file_bytes=file_bytes, file_name=file_name, mime_type=mime_type, entity_type=entity_type, qbo_id=qbo_id
			)
		if response.status_code >= 400:
			raise QuickBooksAPIError(f"QuickBooks upload failed: {response.status_code} {response.text}")
		return response.json() if response.text else {}

	def query(self, query: str):
		"""Run a QBO SQL-like query (the ``/query`` endpoint, text/plain body).

		Used by ``sync.query_all`` for paginated full imports. The query string
		uses QBO's ``startposition``/``maxresults`` paging syntax.
		"""
		return self.request(
			"GET",
			f"/v3/company/{self.settings.realm_id}/query",
			params={"query": query},
			content_type="text/plain",
		)

	def get_entity(self, entity_type: str, qbo_id: str):
		"""Fetch a single QBO entity by id (used for webhook/manual entity syncs)."""
		return self.request("GET", f"/v3/company/{self.settings.realm_id}/{entity_type.lower()}/{qbo_id}")

	def report(self, report_name: str, params: dict | None = None):
		"""Fetch a QBO Reports-API report (TrialBalance, GeneralLedger, ...) by name.

		Reports live under ``/v3/company/{realm}/reports/{ReportName}`` and return a
		nested ``Columns``/``Rows`` structure (not the entity envelope ``query`` and
		``get_entity`` return). ``params`` carries the report controls QBO expects --
		``date_macro``/``start_date``/``end_date``, ``accounting_method``,
		``account``, etc. -- and is merged with the pinned ``minorversion`` by
		``request``. Used by ``core.reconcile`` (balance comparison) and
		``core.opening_balances`` (point-in-time balances for the opening JE).

		The Reports API returns computed ledger balances even for companies whose
		transaction/statement exports come back empty, which is why reconciliation
		and opening balances source from here rather than from ``query``.
		"""
		return self.request(
			"GET",
			f"/v3/company/{self.settings.realm_id}/reports/{report_name}",
			params=dict(params or {}),
		)

	def cdc(self, entities: list[str], changed_since):
		"""Call the Change Data Capture endpoint for entities changed since a cursor.

		``changed_since`` is the cursor (Settings.last_cdc_sync), a datetime or
		timestamp string. It is normalized to the ISO-8601 UTC form QBO requires
		(see ``format_qbo_datetime``) -- passing a raw datetime makes QBO reject
		the request as an invalid ``changedSince``. QBO returns every record of
		the given entity types modified or deleted since then. Invoked by
		``sync.run_cdc`` on the hourly scheduler poll.
		"""
		return self.request(
			"GET",
			f"/v3/company/{self.settings.realm_id}/cdc",
			params={"entities": ",".join(entities), "changedSince": format_qbo_datetime(changed_since)},
			content_type="text/plain",
		)

