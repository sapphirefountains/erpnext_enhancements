# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Google sign-in and the Calendar v3 REST calls calendar sync makes, with ``requests``.

**Identity** (Nik, 2026-10-12, "for now"): one Google account, Nik's, signed in through OAuth with
the OAuth client already in **Google Settings** (Frappe's). A service account cannot do it: Google
Workspace caps sharing outside the domain at "See all event details", so it could never be given
edit rights on the three calendars. The refresh token lives in Calendar Sync Settings' Password
field, so switching to another account later is one more click of *Authorize with Google*.

**The redirect URI is Frappe's shared one**, ``/api/method/frappe.integrations.google_oauth.callback``
(v16.31+): the state token is stored server-side by ``create_google_oauth_state`` and names our
callback (``api/calendar_sync.oauth_callback``), so no route of ours has to be registered with
Google. It must still be listed under the OAuth client's *Authorized redirect URIs* in Google Cloud
Console; it already is if Google Contacts, Mail or Indexing was ever connected on this site.

Things this module is careful about:

* **Scopes are ``calendar.events`` and ``calendar.readonly`` and nothing else.** ``events`` writes
  the events; ``readonly`` lists the account's calendars for *Pick calendars* and reads the
  account's own address. ``include_granted_scopes`` is deliberately **not** sent: with it, a token
  would also carry every scope the account ever granted this client (Gmail, Drive).
* **No token, secret or authorization code is ever logged or put in an exception message.** Every
  failure becomes a :class:`GoogleCalendarError` carrying an HTTP status and Google's short reason,
  raised ``from None`` so the traceback frame with the request (and its headers) is not chained
  onto it: ``frappe.log_error`` prints a chained exception's locals.
* **Backoff.** 429, 5xx and 403 ``rateLimitExceeded``/``userRateLimitExceeded`` are retried up to
  :data:`RETRIES` times with exponential waits (and ``Retry-After`` when Google sends one). A 401 drops
  the cached access token and refreshes once.
* **The access token is cached in redis** for its lifetime minus two minutes. The deploy's FLUSHDB
  just costs one refresh.
"""

import json
import time
from urllib.parse import quote, urlencode

import frappe

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
API_ROOT = "https://www.googleapis.com/calendar/v3"
SCOPES = (
	"https://www.googleapis.com/auth/calendar.events",
	"https://www.googleapis.com/auth/calendar.readonly",
)
#: Frappe v16's shared Google callback (``frappe.integrations.google_oauth.CALLBACK_METHOD``).
CALLBACK_METHOD = "/api/method/frappe.integrations.google_oauth.callback"
#: Where Frappe's callback hands the code: ours, named in the server-side state.
OUR_CALLBACK = "erpnext_enhancements.api.calendar_sync.oauth_callback"
STATE_DOMAIN = "erpnext_calendar_sync"
SETTINGS_ROUTE = "/app/calendar-sync-settings"

SETTINGS_DOCTYPE = "Calendar Sync Settings"
TOKEN_CACHE_KEY = "ee_calendar_sync_access_token"
TIMEOUT_S = 20
RETRIES = 4
RATE_REASONS = ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded")


class GoogleCalendarError(Exception):
	"""A Google answer that was not a success. Carries no request, URL or token.

	``status`` is the HTTP status (0 when no answer arrived), ``reason`` Google's short reason
	(``"rateLimitExceeded"``, ``"invalid_grant"``) or an exception class name.
	"""

	def __init__(self, status, reason="", message=""):
		self.status = int(status or 0)
		self.reason = str(reason or "")[:80]
		self.message = str(message or "")[:200]
		super().__init__(self.describe())

	def describe(self):
		text = f"Google answered {self.status}" if self.status else "Google did not answer"
		if self.reason:
			text += f" ({self.reason})"
		if self.message:
			text += f": {self.message}"
		return text


class GoogleAuthError(GoogleCalendarError):
	"""The refresh token was refused (revoked, expired, wrong client): nothing works until someone
	clicks *Authorize with Google* again."""


# ---------------------------------------------------------------------- framework seams


def _sleep(seconds):
	time.sleep(seconds)


def _http():
	import requests

	return requests


def oauth_client():
	"""``(client_id, client_secret)`` from Google Settings, or a clear ``frappe.throw``."""
	settings = frappe.get_cached_doc("Google Settings")
	client_id = (settings.get("client_id") or "").strip()
	secret = settings.get_password(fieldname="client_secret", raise_exception=False) if client_id else None
	if not settings.get("enable") or not client_id or not secret:
		frappe.throw(
			frappe._("Turn on Google Settings and fill in its Client ID and Client Secret first."),
			title=frappe._("Google Settings"),
		)
	return client_id, secret


def google_settings_ready():
	"""True when Google Settings is on and has a client id and secret. Never raises."""
	try:
		settings = frappe.get_cached_doc("Google Settings")
		return bool(
			settings.get("enable")
			and settings.get("client_id")
			and settings.get_password(fieldname="client_secret", raise_exception=False)
		)
	except Exception:
		return False


def redirect_uri(request_address=None):
	"""The redirect URI Google sees: this site's address plus Frappe's shared callback.

	Built from the request's own address while there is one (Frappe's callback builds it the same
	way when it exchanges the code, and the two must match exactly), else ``get_url()``.
	"""
	if request_address is None:
		try:
			from frappe.utils import get_request_site_address

			request_address = get_request_site_address(True)
		except Exception:
			request_address = None
	if not request_address:
		from frappe.utils import get_url

		request_address = get_url()
	return str(request_address).rstrip("/") + CALLBACK_METHOD


def authorization_url(user):
	"""The Google consent URL for ``user`` (who must come back signed in as themselves)."""
	try:
		from frappe.integrations.google_oauth import create_google_oauth_state
	except ImportError:
		frappe.throw(frappe._("Authorize with Google needs Frappe 16.31 or later."))
	client_id, _secret = oauth_client()
	state = create_google_oauth_state(
		{
			"redirect": SETTINGS_ROUTE,
			"success_query_param": "calendar_sync=connected",
			"failure_query_param": "calendar_sync=refused",
			"domain": STATE_DOMAIN,
			"callback_method": OUR_CALLBACK,
			"user": user,
		}
	)
	params = {
		"access_type": "offline",
		"response_type": "code",
		"prompt": "consent",
		"client_id": client_id,
		"scope": " ".join(SCOPES),
		"redirect_uri": redirect_uri(),
		"state": state,
	}
	return AUTH_URL + "?" + urlencode(params, quote_via=quote)


def _post_token(data):
	"""POST to the token endpoint. Returns the JSON; raises :class:`GoogleAuthError` on a refusal."""
	try:
		response = _http().post(TOKEN_URL, data=data, timeout=TIMEOUT_S)
		status = int(response.status_code)
		payload = response.json() if response.content else {}
	except Exception as exc:
		raise GoogleCalendarError(0, type(exc).__name__) from None
	if status >= 400 or "error" in (payload or {}):
		# Google's token errors are {"error": "invalid_grant", "error_description": "..."}: no secret.
		raise GoogleAuthError(
			status, (payload or {}).get("error"), (payload or {}).get("error_description")
		) from None
	return payload or {}


def exchange_code(code):
	"""``{"refresh_token", "access_token", "expires_in", "scope"}`` for an authorization code."""
	client_id, secret = oauth_client()
	return _post_token(
		{
			"code": code,
			"client_id": client_id,
			"client_secret": secret,
			"grant_type": "authorization_code",
			"redirect_uri": redirect_uri(),
		}
	)


def stored_refresh_token():
	"""The refresh token from Settings, or None. Never logged."""
	try:
		doc = frappe.get_cached_doc(SETTINGS_DOCTYPE)
		return doc.get_password(fieldname="refresh_token", raise_exception=False) or None
	except Exception:
		return None


def forget_access_token():
	try:
		frappe.cache.delete_value(TOKEN_CACHE_KEY)
	except Exception:
		pass


def access_token(force=False):
	"""A live access token: the cached one, else a refresh. Raises :class:`GoogleAuthError`."""
	if not force:
		try:
			cached = frappe.cache.get_value(TOKEN_CACHE_KEY)
		except Exception:
			cached = None
		if cached:
			return cached
	refresh = stored_refresh_token()
	if not refresh:
		raise GoogleAuthError(0, "not_connected", "Authorize with Google first") from None
	client_id, secret = oauth_client()
	payload = _post_token(
		{
			"client_id": client_id,
			"client_secret": secret,
			"refresh_token": refresh,
			"grant_type": "refresh_token",
		}
	)
	token = payload.get("access_token")
	if not token:
		raise GoogleAuthError(0, "no_access_token") from None
	try:
		ttl = max(60, int(payload.get("expires_in") or 3600) - 120)
		frappe.cache.set_value(TOKEN_CACHE_KEY, token, expires_in_sec=ttl)
	except Exception:
		pass
	return token


def revoke(token):
	"""Best effort: tell Google to drop a refresh token. Never raises."""
	if not token:
		return False
	try:
		response = _http().post(REVOKE_URL, data={"token": token}, timeout=TIMEOUT_S)
		return int(response.status_code) < 400
	except Exception:
		return False


# ---------------------------------------------------------------------- the client


def _error_from(response):
	try:
		payload = response.json()
	except Exception:
		payload = {}
	error = (payload or {}).get("error") or {}
	if not isinstance(error, dict):
		return "", str(error)
	reasons = [e.get("reason") for e in error.get("errors") or [] if isinstance(e, dict)]
	return (reasons[0] if reasons else error.get("status") or ""), error.get("message") or ""


def _retry_after(response, attempt):
	try:
		value = float(response.headers.get("Retry-After") or 0)
	except Exception:
		value = 0
	return min(60.0, value or float(2**attempt))


class Client:
	"""Calendar v3 over REST. One instance per job; it holds an access token, never logs it."""

	def __init__(self):
		self._token = None

	def _headers(self, force=False):
		if force or not self._token:
			self._token = access_token(force=force)
		return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

	def request(self, method, path, params=None, body=None):
		"""One call with backoff. Returns the JSON (``{}`` for an empty answer)."""
		url = API_ROOT + path
		refreshed = False
		attempt = 0
		while True:
			try:
				response = _http().request(
					method,
					url,
					params=params,
					data=json.dumps(body) if body is not None else None,
					headers={
						**self._headers(),
						**({"Content-Type": "application/json"} if body is not None else {}),
					},
					timeout=TIMEOUT_S,
				)
				status = int(response.status_code)
			except GoogleCalendarError:
				raise
			except Exception as exc:
				if attempt < RETRIES:
					attempt += 1
					_sleep(float(2**attempt))
					continue
				raise GoogleCalendarError(0, type(exc).__name__) from None
			if status < 300:
				if status == 204 or not response.content:
					return {}
				try:
					return response.json()
				except Exception:
					return {}
			reason, message = _error_from(response)
			if status == 401 and not refreshed:
				refreshed = True
				forget_access_token()
				self._token = None
				self._headers(force=True)
				continue
			retryable = status == 429 or status >= 500 or (status == 403 and reason in RATE_REASONS)
			if retryable and attempt < RETRIES:
				_sleep(_retry_after(response, attempt + 1))
				attempt += 1
				continue
			raise GoogleCalendarError(status, reason, message) from None

	# -- events
	def insert_event(self, calendar_id, body):
		return self.request(
			"POST",
			f"/calendars/{quote(calendar_id, safe='')}/events",
			params={"sendUpdates": "none"},
			body=body,
		)

	def update_event(self, calendar_id, event_id, body):
		return self.request(
			"PUT",
			f"/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}",
			params={"sendUpdates": "none"},
			body=body,
		)

	def delete_event(self, calendar_id, event_id):
		"""Delete; an event already gone (404/410) counts as deleted."""
		try:
			self.request(
				"DELETE",
				f"/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}",
				params={"sendUpdates": "none"},
			)
		except GoogleCalendarError as exc:
			if exc.status not in (404, 410):
				raise
		return True

	def list_events(self, calendar_id, sync_token=None, page_token=None):
		"""One page of ``events.list`` with deleted events, incremental when ``sync_token`` is given.

		A 410 means the token expired: the caller drops it and lists in full.
		"""
		params = {"showDeleted": "true", "maxResults": 250}
		if sync_token:
			params["syncToken"] = sync_token
		if page_token:
			params["pageToken"] = page_token
		return self.request("GET", f"/calendars/{quote(calendar_id, safe='')}/events", params=params)

	def list_instances(
		self, calendar_id, sync_token=None, page_token=None, time_min=None, time_max=None, show_deleted=True
	):
		"""One page of ``events.list`` with ``singleEvents=true`` (recurring events as instances), for
		the C4 time-off read. Incremental from ``sync_token``; or a window (``time_min`` /
		``time_max``, which Google does not allow with a token). A 410 means the token expired."""
		params = {
			"singleEvents": "true",
			"maxResults": 250,
			"showDeleted": "true" if show_deleted else "false",
		}
		if sync_token:
			params["syncToken"] = sync_token
		else:
			if time_min:
				params["timeMin"] = time_min
			if time_max:
				params["timeMax"] = time_max
		if page_token:
			params["pageToken"] = page_token
		return self.request("GET", f"/calendars/{quote(calendar_id, safe='')}/events", params=params)

	# -- calendars
	def calendar_list(self):
		out, page = [], None
		for _ in range(20):
			params = {"maxResults": 250, "minAccessRole": "reader"}
			if page:
				params["pageToken"] = page
			answer = self.request("GET", "/users/me/calendarList", params=params)
			out.extend(answer.get("items") or [])
			page = answer.get("nextPageToken")
			if not page:
				break
		return out

	def primary_email(self):
		"""The signed-in account's address: the id of its primary calendar."""
		answer = self.request("GET", "/users/me/calendarList/primary")
		return answer.get("id") or ""
