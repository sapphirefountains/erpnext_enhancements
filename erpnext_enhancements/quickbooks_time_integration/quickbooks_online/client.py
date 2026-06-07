from __future__ import annotations

import base64
from urllib.parse import urlencode

import frappe
import requests
from frappe.utils import add_to_date, now_datetime

from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.constants import (
	AUTHORIZATION_URL,
	ENVIRONMENT_BASE_URLS,
	MINOR_VERSION,
	OAUTH_SCOPE,
	TOKEN_URL,
)
from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.utils import (
	get_secret,
	get_settings,
	set_secret,
)


class QuickBooksAPIError(Exception):
	pass


class QuickBooksClient:
	def __init__(self, settings=None):
		self.settings = settings or get_settings()

	def get_base_url(self):
		return ENVIRONMENT_BASE_URLS.get(self.settings.environment or "Sandbox", ENVIRONMENT_BASE_URLS["Sandbox"])

	def build_authorization_url(self, state: str, environment: str | None = None):
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
		payload = {
			"grant_type": "authorization_code",
			"code": code,
			"redirect_uri": self.settings.redirect_uri,
		}
		data = self._token_request(payload)
		self._store_tokens(data, realm_id=realm_id)
		return data

	def refresh_access_token(self):
		refresh_token = get_secret(self.settings, "refresh_token")
		if not refresh_token:
			frappe.throw("QuickBooks Online refresh token is missing. Reconnect the integration.")

		data = self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
		self._store_tokens(data)
		return data

	def _token_request(self, payload):
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
		set_secret(self.settings, "access_token", data.get("access_token"))
		if data.get("refresh_token"):
			set_secret(self.settings, "refresh_token", data.get("refresh_token"))
		if realm_id:
			self.settings.realm_id = realm_id
		expires_in = int(data.get("expires_in") or 3600)
		self.settings.token_expires_at = add_to_date(now_datetime(), seconds=expires_in - 300, as_datetime=True)
		self.settings.status = "Connected"
		self.settings.status_message = "Connected to QuickBooks Online."
		self.settings.save(ignore_permissions=True)
		frappe.db.commit()

	def request(self, method: str, path: str, **kwargs):
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
		if response.status_code == 401:
			self.refresh_access_token()
			return self.request(method, path, **kwargs, params=params)
		if response.status_code >= 400:
			raise QuickBooksAPIError(f"QuickBooks API request failed: {response.status_code} {response.text}")
		return response.json() if response.text else {}

	def query(self, query: str):
		return self.request(
			"GET",
			f"/v3/company/{self.settings.realm_id}/query",
			params={"query": query},
			content_type="text/plain",
		)

	def get_entity(self, entity_type: str, qbo_id: str):
		return self.request("GET", f"/v3/company/{self.settings.realm_id}/{entity_type.lower()}/{qbo_id}")

	def cdc(self, entities: list[str], changed_since):
		return self.request(
			"GET",
			f"/v3/company/{self.settings.realm_id}/cdc",
			params={"entities": ",".join(entities), "changedSince": changed_since},
			content_type="text/plain",
		)

