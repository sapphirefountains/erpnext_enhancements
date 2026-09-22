# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Authorization-code OAuth for the three ad platforms: connect, exchange, refresh.

The shape is the QuickBooks one (``quickbooks_online/core``): a one-time CSRF ``state``
minted by ``start_oauth``, tokens stored in encrypted Password fields, a dead credential
marked *Auth Failed* instead of erroring every night. Two differences, both deliberate:

* **``state`` is bound to the user who started the flow.** The callback is not a guest
  endpoint (the returning browser carries the ERPNext session), and it refuses a state that
  another user minted. A leaked ``state`` is therefore worthless to anybody but the person
  who clicked Connect.
* **Credentials live on ``Marketing Connections``, a System-Manager-only Single**, not on
  Marketing Settings, which Sales Manager can read.

Token lifetimes differ and so does the refresh:

| Platform | Stored | Refresh |
|---|---|---|
| Google Ads | refresh token (no expiry) | a fresh access token every run |
| Meta | long-lived user token (~60 days) | none without the user: reconnect before expiry; the form warns |
| LinkedIn | access token (60 days) + refresh token (365 days) | refreshed when inside 7 days of expiry |

Every token call raises ``from None`` with a redacted message: the request body holds the
client secret and the authorization code.
"""

import datetime
import json
import secrets
from urllib.parse import urlencode

from erpnext_enhancements.marketing.core import constants as C
from erpnext_enhancements.marketing.core.client import MarketingAPIError
from erpnext_enhancements.marketing.core.utils import (
	field,
	get_credentials,
	get_secret,
	redact_text,
	set_secret,
)

CALLBACK_METHOD = "erpnext_enhancements.marketing.api.oauth_callback"

#: LinkedIn access tokens are refreshed this close to expiry.
LINKEDIN_REFRESH_WITHIN_DAYS = 7


def redirect_uri():
	"""The callback URL each platform app must list, exactly."""
	from frappe.utils import get_url

	return f"{get_url()}/api/method/{CALLBACK_METHOD}"


def _state_key(state):
	return f"marketing_oauth_state:{state}"


def spec_for(name):
	"""The OAuth spec for an ad platform or a publishing connection."""
	from erpnext_enhancements.marketing.publish import constants as P

	return C.OAUTH.get(name) or P.PUBLISH_OAUTH[name]


def known_connections():
	"""Every name a Connect flow may be started for: the ad platforms, then publishing."""
	from erpnext_enhancements.marketing.publish import constants as P

	return C.PLATFORMS + P.PUBLISH_CONNECTIONS


def authorization_url(platform, client_id, state):
	spec = spec_for(platform)
	params = {
		"client_id": client_id,
		"redirect_uri": redirect_uri(),
		"response_type": "code",
		"scope": spec["scope_separator"].join(spec["scopes"]),
		"state": state,
		**spec["extra_authorize_params"],
	}
	return f"{spec['authorize_url']}?{urlencode(params)}"


def mint_state(platform, user):
	import frappe

	state = secrets.token_urlsafe(32)
	frappe.cache().set_value(
		_state_key(state),
		json.dumps({"platform": platform, "user": user}),
		expires_in_sec=C.OAUTH_STATE_TTL_SECONDS,
	)
	return state


def consume_state(state, user):
	"""The platform a valid ``state`` was minted for, or None. Single use."""
	import frappe

	if not state:
		return None
	raw = frappe.cache().get_value(_state_key(state))
	frappe.cache().delete_value(_state_key(state))
	if not raw:
		return None
	try:
		data = json.loads(raw)
	except (TypeError, ValueError):
		return None
	if data.get("user") != user or data.get("platform") not in known_connections():
		return None
	return data["platform"]


# ---------------------------------------------------------------- token calls


def _token_request(platform, method, params=None, data=None, http=None):
	"""One call to a platform's token endpoint. The only non-data URL this module hits.

	``platform`` may also be a publishing connection (``publish/oauth.py``): same token
	endpoints, same redaction, a different stored token.
	"""
	if http is None:
		import requests

		http = requests
	url = spec_for(platform)["token_url"]
	try:
		response = http.request(method, url, params=params, data=data, timeout=30)
	except Exception as exc:
		raise MarketingAPIError(platform, f"token endpoint unreachable: {type(exc).__name__}") from None
	try:
		body = response.json()
	except ValueError:
		body = {}
	if response.status_code >= 400 or "error" in body:
		detail = body.get("error_description") or body.get("error") or response.text
		if isinstance(detail, dict):
			detail = detail.get("message") or str(detail)
		raise MarketingAPIError(
			platform,
			f"token exchange failed: {redact_text(detail, 300)}",
			status=token_failure_status(response.status_code),
		) from None
	return body


def token_failure_status(status):
	"""How a failed token call is reported. Pure.

	Only a rejection is an auth failure: ``invalid_grant`` (400), a bad client (401), a
	refused scope. A token server that is down (5xx) or throttling (408/429) says nothing
	about the credential, and reporting it as 401 -- as this did until v1.508.0 -- marked a
	good Google Ads connection *Auth Failed* for one bad minute at Google, and would have
	cleared a good publishing refresh token.
	"""
	if status in C.RETRYABLE_STATUSES:
		return status
	return 401


def _expiry(seconds):
	from frappe.utils import now_datetime

	if not seconds:
		return None
	return now_datetime() + datetime.timedelta(seconds=int(seconds))


def exchange_code(platform, code, creds, http=None):
	"""Turn an authorization code into stored tokens. Stages them on ``creds``; the caller saves."""
	client_id = creds.get(field(platform, "client_id"))
	client_secret = get_secret(creds, field(platform, "client_secret"))
	base = {
		"client_id": client_id,
		"client_secret": client_secret,
		"redirect_uri": redirect_uri(),
		"code": code,
	}

	if platform == C.PLATFORM_GOOGLE:
		body = _token_request(platform, "POST", data={**base, "grant_type": "authorization_code"}, http=http)
		if not body.get("refresh_token"):
			raise MarketingAPIError(
				platform, "Google returned no refresh token; reconnect and approve offline access", status=401
			)
		set_secret(creds, field(platform, "refresh_token"), body["refresh_token"])

	elif platform == C.PLATFORM_META:
		short = _token_request(platform, "GET", params=base, http=http)
		body = _token_request(
			platform,
			"GET",
			params={
				"grant_type": "fb_exchange_token",
				"client_id": client_id,
				"client_secret": client_secret,
				"fb_exchange_token": short.get("access_token"),
			},
			http=http,
		)
		set_secret(creds, field(platform, "access_token"), body.get("access_token"))
		creds.set(field(platform, "access_token_expires_on"), _expiry(body.get("expires_in")))

	else:
		body = _token_request(platform, "POST", data={**base, "grant_type": "authorization_code"}, http=http)
		_store_linkedin(creds, body)


def _store_linkedin(creds, body):
	platform = C.PLATFORM_LINKEDIN
	set_secret(creds, field(platform, "access_token"), body.get("access_token"))
	creds.set(field(platform, "access_token_expires_on"), _expiry(body.get("expires_in")))
	if body.get("refresh_token"):
		set_secret(creds, field(platform, "refresh_token"), body["refresh_token"])
		creds.set(field(platform, "refresh_token_expires_on"), _expiry(body.get("refresh_token_expires_in")))


def access_token(platform, creds=None, http=None):
	"""A usable access token for ``platform`` right now, refreshing where the platform allows.

	Raises ``MarketingAPIError`` with ``status=401`` when a human has to reconnect.
	"""
	from frappe.utils import get_datetime, now_datetime

	creds = creds or get_credentials()

	if platform == C.PLATFORM_GOOGLE:
		refresh = get_secret(creds, field(platform, "refresh_token"))
		if not refresh:
			raise MarketingAPIError(platform, "not connected", status=401)
		body = _token_request(
			platform,
			"POST",
			data={
				"grant_type": "refresh_token",
				"refresh_token": refresh,
				"client_id": creds.get(field(platform, "client_id")),
				"client_secret": get_secret(creds, field(platform, "client_secret")),
			},
			http=http,
		)
		return body["access_token"]

	token = get_secret(creds, field(platform, "access_token"))
	expires = creds.get(field(platform, "access_token_expires_on"))
	now = now_datetime()

	if (
		platform == C.PLATFORM_LINKEDIN
		and expires
		and get_datetime(expires) - now < datetime.timedelta(days=LINKEDIN_REFRESH_WITHIN_DAYS)
	):
		refresh = get_secret(creds, field(platform, "refresh_token"))
		if refresh:
			body = _token_request(
				platform,
				"POST",
				data={
					"grant_type": "refresh_token",
					"refresh_token": refresh,
					"client_id": creds.get(field(platform, "client_id")),
					"client_secret": get_secret(creds, field(platform, "client_secret")),
				},
				http=http,
			)
			_store_linkedin(creds, body)
			creds.save(ignore_permissions=True)
			return body["access_token"]

	if not token:
		raise MarketingAPIError(platform, "not connected", status=401)
	if expires and get_datetime(expires) <= now:
		raise MarketingAPIError(platform, "access token expired; reconnect", status=401)
	return token


def revoke(platform, creds, http=None):
	"""Best-effort revoke at the platform (Google only offers one). Never raises."""
	url = spec_for(platform)["revoke_url"]
	if not url:
		return
	token = get_secret(creds, field(platform, "refresh_token"))
	if not token:
		return
	try:
		if http is None:
			import requests

			http = requests
		http.request("POST", url, data={"token": token}, timeout=15)
	except Exception:
		pass
