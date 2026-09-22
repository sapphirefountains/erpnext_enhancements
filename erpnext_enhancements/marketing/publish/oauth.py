# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Connect, use and keep alive the three publishing connections (TASK-2026-01480).

Same machinery as the ad connectors (``core/oauth.py``): a one-time ``state`` bound to the
person who clicked Connect, the same logged-in callback URL (so each platform app registers
one redirect URI), encrypted Password fields on Marketing Connections, and ``raise ... from
None`` everywhere a secret is in scope. What differs is what is kept, because a publisher
needs less than the login hands over:

| Connection | Networks | Kept | Lifetime | Kept alive by |
|---|---|---|---|---|
| Meta Publishing | Facebook, Instagram | the **Page** token only | no expiry; dies if the person loses their Page role, changes password, or removes the app | a daily check that it still works |
| LinkedIn Publishing | LinkedIn | access + refresh token | 60 days / 365 days, the refresh token not rolling | refreshed inside 7 days of expiry; warned 30 days before either runs out for good |
| YouTube Publishing | YouTube | refresh token | until revoked, or 6 months unused (7 days for an External Google app still in Testing) | a daily refresh, which also counts as use |

**Meta keeps only the Page token.** The login ends with a long-lived *user* token that can
act on every Page the person manages; a publisher needs one Page. The user token is used
here, in memory, to check what was granted and fetch the Page token, and never stored.

**What Meta granted is checked, not assumed.** Meta's dialog lets a person untick
permissions, and a Meta token also carries whatever that person granted this app before --
the ads connection uses the same app. So before anything is stored, ``GET /me/permissions``
is read, and the connection is **refused** if it holds a spend-capable permission
(``core/constants.SPEND_CAPABLE_SCOPES``). A missing Instagram permission, or a Page with no
linked Instagram account, connects Facebook alone and says so.

**A dead credential is cleared, not retried.** ``invalid_grant`` on a refresh, or a Meta token
Meta no longer honors, clears the stored tokens and marks the connection *Auth Failed* with
a message saying to reconnect. Nothing retries a dead token every night, and the form keys
its Reconnect button on that status. (The task said *Not Connected*; *Auth Failed* is what
the ad connectors already use for "a person has to reconnect", and it keeps the difference
between "never connected" and "was connected, broke" visible.)
"""

import datetime
from urllib.parse import urlencode

from erpnext_enhancements.marketing.core import constants as C
from erpnext_enhancements.marketing.core import oauth as core_oauth
from erpnext_enhancements.marketing.core.client import MarketingAPIError
from erpnext_enhancements.marketing.core.utils import field, get_secret, set_secret
from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish.client import PublishTransport

STATUS_CONNECTED = "Connected"
STATUS_AUTH_FAILED = "Auth Failed"
STATUS_NOT_CONNECTED = "Not Connected"

#: Token fields a dead or disconnected credential loses.
TOKEN_FIELDS = ("access_token", "refresh_token", "access_token_expires_on", "refresh_token_expires_on")


def _require(connection):
	if connection not in P.PUBLISH_CONNECTIONS:
		raise ValueError(f"not a publishing connection: {connection}")
	return connection


def _has(creds, fieldname):
	meta = getattr(creds, "meta", None)
	return meta is None or meta.has_field(fieldname)


def _now():
	from frappe.utils import now_datetime

	return now_datetime()


def _expiry(seconds, now=None):
	if not seconds:
		return None
	return (now or _now()) + datetime.timedelta(seconds=int(seconds))


def _as_datetime(value):
	if not value:
		return None
	if isinstance(value, datetime.datetime):
		return value
	from frappe.utils import get_datetime

	return get_datetime(value)


def linkedin_headers():
	return {"LinkedIn-Version": C.LINKEDIN_API_VERSION, "X-Restli-Protocol-Version": "2.0.0"}


# ---------------------------------------------------------------- connect


def authorization_url(connection, creds, state):
	"""The consent URL. Meta may use a Facebook Login for Business configuration instead of scopes."""
	_require(connection)
	client_id = creds.get(field(connection, "client_id"))
	config_id = creds.get(field(connection, "login_config_id")) if connection == P.CONNECTION_META else None
	if not config_id:
		return core_oauth.authorization_url(connection, client_id, state)
	# A Login for Business configuration names its permissions in Meta's console, where this
	# code cannot see them. That is why exchange_code() checks what was granted either way.
	spec = P.PUBLISH_OAUTH[connection]
	params = {
		"client_id": client_id,
		"redirect_uri": core_oauth.redirect_uri(),
		"response_type": "code",
		"config_id": config_id,
		"override_default_response_type": "true",
		"state": state,
	}
	return f"{spec['authorize_url']}?{urlencode(params)}"


def _client(creds, connection):
	return creds.get(field(connection, "client_id")), get_secret(creds, field(connection, "client_secret"))


def exchange_code(connection, code, creds, http=None):
	"""Turn an authorization code into stored tokens and identity. Returns a one-line summary.

	Everything is checked before anything is staged on ``creds``, so a connection that fails
	half way leaves no half-stored token behind. The caller saves.
	"""
	_require(connection)
	client_id, client_secret = _client(creds, connection)
	base = {
		"client_id": client_id,
		"client_secret": client_secret,
		"redirect_uri": core_oauth.redirect_uri(),
		"code": code,
	}
	if connection == P.CONNECTION_META:
		return _connect_meta(creds, base, http)
	if connection == P.CONNECTION_LINKEDIN:
		return _connect_linkedin(creds, base, http)
	return _connect_youtube(creds, base, http)


def _stage(creds, connection, values, secrets=None):
	for name, value in values.items():
		fieldname = field(connection, name)
		if _has(creds, fieldname):
			creds.set(fieldname, value)
	for name, value in (secrets or {}).items():
		set_secret(creds, field(connection, name), value)


# -- Meta


def granted_permissions(transport):
	"""The permissions the token's person has granted this app, by status ``granted``."""
	body = transport.request("GET", f"{C.META_GRAPH_BASE}/me/permissions")
	return {row.get("permission") for row in body.get("data") or [] if row.get("status") == "granted"}


def check_meta_permissions(granted):
	"""``(instagram_ok, missing_for_instagram)``; raises when Facebook itself cannot work. Pure."""
	connection = P.CONNECTION_META
	spend = sorted(set(granted) & C.SPEND_CAPABLE_SCOPES)
	if spend:
		raise MarketingAPIError(
			connection,
			f"refused: this Meta login grants {', '.join(spend)}, which can touch ad spend. Remove "
			"it from the app (and from the login configuration, if one is set), then connect again.",
			status=401,
		)
	missing = sorted(P.META_REQUIRED[P.NETWORK_FACEBOOK] - set(granted))
	if missing:
		raise MarketingAPIError(
			connection, f"Facebook publishing needs {', '.join(missing)}, which was not granted", status=401
		)
	missing_ig = sorted(P.META_REQUIRED[P.NETWORK_INSTAGRAM] - set(granted))
	return not missing_ig, missing_ig


def choose(connection, candidates, preset, describe, what, setting):
	"""The one candidate to connect: the preset id if given, else the only one. Pure.

	``candidates`` is ``[(id, row)]``. Anything ambiguous raises with the choices listed, so
	the fix is a field to fill in, not a guess the code made.
	"""
	if preset:
		for candidate_id, row in candidates:
			if str(candidate_id) == str(preset).strip():
				return candidate_id, row
		shown = describe(candidates) or "none"
		raise MarketingAPIError(
			connection,
			f"{what} {preset} is not one this login can manage (it can manage: {shown})",
			status=401,
		)
	if len(candidates) == 1:
		return candidates[0]
	if not candidates:
		raise MarketingAPIError(connection, f"this login manages no {what}", status=401)
	raise MarketingAPIError(
		connection,
		f"this login manages {len(candidates)} of them; set {setting} to one of: {describe(candidates)}",
		status=401,
	)


def _connect_meta(creds, base, http):
	connection = P.CONNECTION_META
	short = core_oauth._token_request(connection, "GET", params=base, http=http)
	long_lived = core_oauth._token_request(
		connection,
		"GET",
		params={
			"grant_type": "fb_exchange_token",
			"client_id": base["client_id"],
			"client_secret": base["client_secret"],
			"fb_exchange_token": short.get("access_token"),
		},
		http=http,
	)
	user = PublishTransport(connection, token=long_lived.get("access_token"), http=http)
	instagram_ok, missing_ig = check_meta_permissions(granted_permissions(user))

	body = user.request(
		"GET",
		f"{C.META_GRAPH_BASE}/me/accounts",
		params={"fields": "id,name,access_token,tasks,instagram_business_account{id,username}", "limit": 100},
	)
	pages = [(row.get("id"), row) for row in body.get("data") or []]
	page_id, page = choose(
		connection,
		pages,
		creds.get(field(connection, "page_id")),
		lambda rows: ", ".join(f"{r.get('name')} ({i})" for i, r in rows),
		"Facebook Page",
		"Facebook Page ID",
	)
	if P.META_POSTING_TASK not in (page.get("tasks") or []):
		raise MarketingAPIError(
			connection,
			f"this login cannot post to {page.get('name')} (no {P.META_POSTING_TASK} role)",
			status=401,
		)
	if not page.get("access_token"):
		raise MarketingAPIError(connection, "Meta returned no Page token", status=401)

	instagram = page.get("instagram_business_account") or {}
	if instagram_ok and instagram.get("id"):
		ig_id, ig_name, ig_note = (
			instagram["id"],
			instagram.get("username") or "",
			f"Instagram: @{instagram.get('username')}",
		)
	elif not instagram.get("id"):
		ig_id, ig_name, ig_note = "", "", "Instagram: no professional account is linked to this Page"
	else:
		ig_id, ig_name, ig_note = "", "", f"Instagram: not granted ({', '.join(missing_ig)})"

	_stage(
		creds,
		connection,
		{
			"page_id": page_id,
			"page_name": page.get("name") or "",
			"instagram_user_id": ig_id,
			"instagram_username": ig_name,
		},
		# Only the Page token. The long-lived user token goes out of scope here.
		secrets={"access_token": page["access_token"]},
	)
	return f"Page: {page.get('name')} ({page_id}) · {ig_note}"


# -- LinkedIn


def _linkedin_tokens(body, now=None):
	values = {"access_token_expires_on": _expiry(body.get("expires_in"), now)}
	secrets = {"access_token": body.get("access_token")}
	if body.get("refresh_token"):
		secrets["refresh_token"] = body["refresh_token"]
		values["refresh_token_expires_on"] = _expiry(body.get("refresh_token_expires_in"), now)
	return values, secrets


def _connect_linkedin(creds, base, http):
	connection = P.CONNECTION_LINKEDIN
	body = core_oauth._token_request(
		connection, "POST", data={**base, "grant_type": "authorization_code"}, http=http
	)
	transport = PublishTransport(
		connection, token=body.get("access_token"), headers=linkedin_headers(), http=http
	)
	org_id, name = _resolve_organization(transport, creds.get(field(connection, "organization_id")))
	values, secrets = _linkedin_tokens(body)
	_stage(creds, connection, {**values, "organization_id": org_id, "organization_name": name}, secrets)
	note = (
		""
		if body.get("refresh_token")
		else " · LinkedIn issued no refresh token: reconnect before the access token expires"
	)
	return f"Company Page: {name} ({org_id}){note}"


def _resolve_organization(transport, preset):
	body = transport.request(
		"GET",
		f"{C.LINKEDIN_REST_BASE}/organizationAcls",
		params={"q": "roleAssignee", "role": P.LINKEDIN_ORG_ROLE, "state": "APPROVED"},
	)
	orgs = []
	for row in body.get("elements") or []:
		urn = row.get("organization") or ""
		if urn.startswith("urn:li:organization:"):
			orgs.append((urn.rsplit(":", 1)[1], row))
	org_id, _row = choose(
		P.CONNECTION_LINKEDIN,
		orgs,
		preset,
		lambda rows: ", ".join(i for i, _ in rows),
		f"Company Page where you are {P.LINKEDIN_ORG_ROLE}",
		"Company Page (Organization) ID",
	)
	org = transport.request("GET", f"{C.LINKEDIN_REST_BASE}/organizations/{org_id}")
	return org_id, org.get("localizedName") or org.get("vanityName") or org_id


# -- YouTube


def _connect_youtube(creds, base, http):
	connection = P.CONNECTION_YOUTUBE
	body = core_oauth._token_request(
		connection, "POST", data={**base, "grant_type": "authorization_code"}, http=http
	)
	if not body.get("refresh_token"):
		raise MarketingAPIError(
			connection,
			"Google returned no refresh token; connect again and approve offline access",
			status=401,
		)
	transport = PublishTransport(connection, token=body.get("access_token"), http=http)
	channel_id, title = _resolve_channel(transport)
	_stage(
		creds,
		connection,
		{"channel_id": channel_id, "channel_title": title},
		secrets={"refresh_token": body["refresh_token"]},
	)
	return f"Channel: {title} ({channel_id})"


def _resolve_channel(transport):
	body = transport.request(
		"GET", f"{P.YOUTUBE_API_BASE}/channels", params={"part": "snippet", "mine": "true"}
	)
	items = body.get("items") or []
	if not items:
		raise MarketingAPIError(
			P.CONNECTION_YOUTUBE,
			"this Google account has no YouTube channel. Connect again and pick the channel's account "
			"(a Brand Account is chosen on Google's account picker)",
			status=401,
		)
	channel = items[0]
	return channel.get("id"), (channel.get("snippet") or {}).get("title") or channel.get("id")


# ---------------------------------------------------------------- use


def access_token(connection, creds, force_refresh=False, http=None):
	"""A usable bearer token right now. ``force_refresh`` after a 401.

	Raises ``MarketingAPIError`` with status 401 when a person has to reconnect.
	"""
	_require(connection)
	if connection == P.CONNECTION_META:
		token = get_secret(creds, field(connection, "access_token"))
		if not token:
			raise MarketingAPIError(connection, "not connected", status=401)
		if force_refresh:
			# Page tokens are not refreshable: a 401 on one means Meta has revoked it.
			raise MarketingAPIError(
				connection, "Meta no longer accepts the Page token; reconnect", status=401
			)
		return token

	if connection == P.CONNECTION_YOUTUBE:
		refresh = get_secret(creds, field(connection, "refresh_token"))
		if not refresh:
			raise MarketingAPIError(connection, "not connected", status=401)
		client_id, client_secret = _client(creds, connection)
		body = core_oauth._token_request(
			connection,
			"POST",
			data={
				"grant_type": "refresh_token",
				"refresh_token": refresh,
				"client_id": client_id,
				"client_secret": client_secret,
			},
			http=http,
		)
		if body.get("refresh_token"):  # rotation: Google rarely does, but honor it when it does
			set_secret(creds, field(connection, "refresh_token"), body["refresh_token"])
			creds.save(ignore_permissions=True)
		return body["access_token"]

	return _linkedin_access_token(creds, force_refresh, http)


def _linkedin_access_token(creds, force_refresh, http):
	connection = P.CONNECTION_LINKEDIN
	now = _now()
	token = get_secret(creds, field(connection, "access_token"))
	expires = _as_datetime(creds.get(field(connection, "access_token_expires_on")))
	due = force_refresh or (
		expires is not None and expires - now < datetime.timedelta(days=P.LINKEDIN_REFRESH_WITHIN_DAYS)
	)
	refresh = get_secret(creds, field(connection, "refresh_token"))
	if due and refresh:
		client_id, client_secret = _client(creds, connection)
		body = core_oauth._token_request(
			connection,
			"POST",
			data={
				"grant_type": "refresh_token",
				"refresh_token": refresh,
				"client_id": client_id,
				"client_secret": client_secret,
			},
			http=http,
		)
		# LinkedIn's refresh is not rolling: a response without a new refresh token leaves the
		# original one and its expiry in place (_linkedin_tokens only stages what came back).
		values, secrets = _linkedin_tokens(body, now)
		_stage(creds, connection, values, secrets)
		creds.save(ignore_permissions=True)
		return body["access_token"]
	if not token:
		raise MarketingAPIError(connection, "not connected", status=401)
	if force_refresh or (expires is not None and expires <= now):
		raise MarketingAPIError(
			connection, "access token expired and cannot be refreshed; reconnect", status=401
		)
	return token


def transport_for(connection, creds, http=None, settings=None):
	"""An authorized ``PublishTransport`` that refreshes itself once on a 401."""
	settings = settings or {}
	token = access_token(connection, creds, http=http)
	return PublishTransport(
		connection,
		token=token,
		refresh=lambda: access_token(connection, creds, force_refresh=True, http=http),
		headers=linkedin_headers() if connection == P.CONNECTION_LINKEDIN else None,
		max_retries=settings.get("max_retries") or 3,
		timeout=settings.get("request_timeout_seconds") or 30,
		http=http,
	)


def describe(connection, creds, http=None):
	"""Re-read what the stored token reaches. The Test button, and the daily Meta check."""
	transport = transport_for(connection, creds, http=http)
	if connection == P.CONNECTION_META:
		page_id = creds.get(field(connection, "page_id"))
		page = transport.request(
			"GET",
			f"{C.META_GRAPH_BASE}/{page_id}",
			params={"fields": "id,name,instagram_business_account{id,username}"},
		)
		lines = [f"Page: {page.get('name')} ({page.get('id')})"]
		instagram = page.get("instagram_business_account") or {}
		if creds.get(field(connection, "instagram_user_id")) and instagram.get("id"):
			lines.append(f"Instagram: @{instagram.get('username')}")
		else:
			lines.append("Instagram: not available on this connection")
		return lines
	if connection == P.CONNECTION_LINKEDIN:
		org_id = creds.get(field(connection, "organization_id"))
		org = transport.request("GET", f"{C.LINKEDIN_REST_BASE}/organizations/{org_id}")
		return [f"Company Page: {org.get('localizedName') or org_id} ({org_id})"]
	channel_id, title = _resolve_channel(transport)
	return [f"Channel: {title} ({channel_id})"]


# ---------------------------------------------------------------- upkeep


def mark_dead(creds, connection, message, now=None):
	"""Forget a credential the platform no longer honors. The caller saves."""
	for name in TOKEN_FIELDS:
		fieldname = field(connection, name)
		if _has(creds, fieldname):
			creds.set(fieldname, None)
	creds.set(field(connection, "connection_status"), STATUS_AUTH_FAILED)
	creds.set(field(connection, "status_message"), f"Reconnect needed: {message}"[:500])
	creds.set(field(connection, "last_error_at"), now or _now())


def expiry_warning(connection, creds, now=None):
	"""A warning when a token nobody can refresh runs out within ``EXPIRY_WARNING_DAYS``. Pure."""
	if connection != P.CONNECTION_LINKEDIN:
		return None
	now = now or _now()
	horizon = now + datetime.timedelta(days=P.EXPIRY_WARNING_DAYS)
	has_refresh = bool(get_secret(creds, field(connection, "refresh_token")))
	name, label = (
		("refresh_token_expires_on", "refresh token")
		if has_refresh
		else ("access_token_expires_on", "access token (LinkedIn issued no refresh token)")
	)
	when = _as_datetime(creds.get(field(connection, name)))
	if when is not None and when <= horizon:
		return f"LinkedIn {label} runs out on {when:%Y-%m-%d}; reconnect before then"
	return None


def maintain(connection, creds, http=None, now=None):
	"""One connection's daily upkeep. Returns ``"ok"``, ``"warned"``, ``"dead"`` or ``"error"``.

	Per connection, because the lifetimes differ: Meta's Page token is checked by reading
	the Page; LinkedIn's access token is refreshed inside seven days of expiry; YouTube's
	refresh token is exercised, which also stops Google expiring it for disuse.
	"""
	now = now or _now()
	try:
		if connection == P.CONNECTION_META:
			describe(connection, creds, http=http)
		else:
			access_token(connection, creds, http=http)
	except MarketingAPIError as exc:
		if exc.is_auth_failure:
			mark_dead(creds, connection, str(exc), now)
			return "dead"
		creds.set(field(connection, "status_message"), str(exc)[:500])
		creds.set(field(connection, "last_error_at"), now)
		return "error"
	warning = expiry_warning(connection, creds, now)
	if warning:
		creds.set(field(connection, "status_message"), warning)
		return "warned"
	return "ok"
