# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Ask FedEx or UPS where a package is. ``requests`` and the standard library only.

The host is a managed server where nothing can be pip-installed, so, like ``stripe_payments``
and the QuickBooks client, this talks to the carriers' REST APIs directly. No Frappe import:
the caller hands in a ``Config``, a token cache and (in tests) a fake HTTP object, so every path
below is tested without a bench (``tests/test_carrier_tracking_client.py``).

**Nothing here has been called against a real carrier.** Nobody has credentials yet. Every URL,
header and field was read from the carriers' current documentation on 2026-11-13 (the sources are
listed beside each constant), and the tests use made-up payloads shaped like the documented ones.
When the first real call happens, the likely corrections are all constants in this file or a key
in ``normalize.py``; the Error Log and the row's Last Problem field carry the carrier's own error
code (masked) to say which.

``track()`` **never raises.** It returns one dict (``_result``) whose ``outcome`` says what
happened: ``ok``, ``not_found``, ``invalid`` (about the package), ``auth_failed``,
``rate_limited``, ``unavailable``, ``bad_response``, ``not_configured``, ``unsupported``.
``poll_rules`` decides what each one does to a stored row. Anything other than ``ok`` leaves the
status fields empty, so a caller cannot mistake "could not check" for an answer.

Sources (all read 2026-11-13):

FedEx
  * Hosts, sandbox and production: https://developer.fedex.com/api/en-us/guides/best-practices.html
  * OAuth, ``POST /oauth/token``, form fields ``grant_type``/``client_id``/``client_secret``,
    response ``access_token``/``expires_in`` (3600, "The standard token expiration time is one
    hour"): https://developer.fedex.com/api/en-us/catalog/authorization/v1/docs.html (spec file
    https://developer.fedex.com/wirc/json/api_groups/APIAuthorization/APIAuthorization-Resource.json)
  * Track, ``POST /track/v1/trackingnumbers``, body, headers, response, up to 30 numbers a call:
    https://developer.fedex.com/api/en-us/catalog/track/v1/docs.html (spec file
    https://developer.fedex.com/wirc/json/api_groups/Track/TrackingNumbers-Resource.json; error
    codes in .../Track/Track-Common-ErrorMapping.json). FedEx now calls this API "Basic Integrated
    Visibility"; the path is still ``/track/v1``.
  * Limits: 1,400 transactions per 10 seconds per project, 429 with "Retry after 10 seconds";
    100,000 Track requests a day per project; the token endpoint answers **403 for ten minutes**
    to more than 3 hits a second (or 1 a second for 2 minutes) from one IP:
    https://developer.fedex.com/api/en-us/guides/ratelimits.html

UPS
  * OAuth client credentials, ``POST /security/v1/oauth/token``, HTTP Basic with the client id and
    secret, form ``grant_type=client_credentials``; every response field is a *string*,
    ``expires_in`` included: https://github.com/UPS-API/api-documentation/blob/main/OAuthClientCredentials.yaml
    and https://developer.ups.com/oauth-developer-guide ("the rate limit for the token endpoint is
    far lower" than the APIs': reuse the token until shortly before it expires).
  * Track, ``GET /api/track/v1/details/{inquiryNumber}`` with required headers ``transId`` and
    ``transactionSrc``; ``trackResponse.shipment[].package[]``; errors as
    ``{"response": {"errors": [{"code", "message"}]}}``, 404 "Tracking number information not
    found": https://github.com/UPS-API/api-documentation/blob/main/Tracking.yaml and
    https://developer.ups.com/api/reference/trackingapi/errors
  * Hosts: ``wwwcie.ups.com`` (CIE, UPS's test environment, which answers every number with the
    same sample) and ``onlinetools.ups.com``. No numeric rate limit is published; a 429 is
    "Number of requests exceeded Quota for the time interval":
    https://developer.ups.com/get-started

**Secrets.** The client secret goes only into the token request (FedEx: form body; UPS: the Basic
header). The access token goes only into the Track request's header and the cache. Neither is put
in a local that outlives the call, neither is ever put into a message: every error text passes
through ``carriers.mask`` with the id, secret and token as known secrets before it leaves here.

**The token cache** is Redis (``config.cache_adapter``), keyed by carrier, environment and a hash
of the credentials, with a lifetime five minutes shorter than the carrier's, so a token is never
used in its last minutes. A refused login is remembered for ten minutes and a 429 for as long as
the carrier asks (``Retry-After``, at most an hour), so a misconfigured carrier or a busy one is
not asked again on every package.
"""

import base64
import hashlib
import uuid
from collections import namedtuple
from datetime import datetime

from erpnext_enhancements.carrier_tracking import carriers as C
from erpnext_enhancements.carrier_tracking import normalize as N

SANDBOX = "Sandbox"
PRODUCTION = "Production"

HOSTS = {
	C.FEDEX: {SANDBOX: "https://apis-sandbox.fedex.com", PRODUCTION: "https://apis.fedex.com"},
	C.UPS: {SANDBOX: "https://wwwcie.ups.com", PRODUCTION: "https://onlinetools.ups.com"},
}
TOKEN_PATHS = {C.FEDEX: "/oauth/token", C.UPS: "/security/v1/oauth/token"}
FEDEX_TRACK_PATH = "/track/v1/trackingnumbers"
UPS_TRACK_PATH = "/api/track/v1/details/{number}"

#: UPS asks every call to name the application that made it (``transactionSrc``).
UPS_TRANSACTION_SRC = "erpnext-enhancements"

#: Connect and read timeouts, seconds. A carrier slower than this is "unavailable" this time.
TIMEOUT = (5, 15)

#: A token is used until this many seconds before the carrier says it expires.
TOKEN_SAFETY_SECONDS = 300
#: Used when a token response says nothing about its lifetime (both carriers document it).
TOKEN_FALLBACK_SECONDS = 1800
#: How long a refused login is remembered before the carrier is asked again.
AUTH_REFUSED_SECONDS = 600
#: FedEx's token endpoint answers 403 for ten minutes after too many requests from one address.
FEDEX_TOKEN_PENALTY_SECONDS = 600
#: How long any other token failure (down, a redirect, a 404, a 200 with no token, HTML) is
#: remembered, so the next package does not ask the token endpoint again straight away.
TOKEN_FAILURE_SECONDS = 120
#: Back-off after a 429 when the carrier does not say how long (FedEx says 10 seconds).
DEFAULT_RETRY_AFTER = 60
MAX_RETRY_AFTER = 3600

CACHE_PREFIX = "carrier_tracking"

# What ``track`` can answer. ``poll_rules`` decides what each one does to a stored row.
OK = "ok"
NOT_FOUND = "not_found"
INVALID = "invalid"
AUTH_FAILED = "auth_failed"
RATE_LIMITED = "rate_limited"
UNAVAILABLE = "unavailable"
BAD_RESPONSE = "bad_response"
NOT_CONFIGURED = "not_configured"
UNSUPPORTED = "unsupported"
#: The carrier refused the request with an error that is not one of its documented package codes.
#: Never read as "invalid number" or "not found", which stop a package for good. Every REJECTED
#: result carries a ``scope``:
#:
#: * ``"carrier"``: clearly not about one number: an account or login code, a code naming our
#:   request as a whole, a wrong path, an HTTP status other than 400 or 404, a body that is not JSON.
#:   The poller skips the carrier for the rest of the run and counts nothing against the package.
#: * ``"number"``: ambiguous, a 400 or 404 with a code we cannot place. It may be this number (a
#:   mistyped one, on a carrier picked by hand) or our request (then every number gets it). The
#:   poller tells the two apart by comparing packages in the same run (``tracking``), and
#:   ``poll_rules`` stops a package that only ever gets this answer after a few days.
REJECTED = "rejected"
SCOPE_CARRIER = "carrier"
SCOPE_NUMBER = "number"

#: FedEx error codes about the account or the request as a whole (prefixes, then exact codes).
#: From Track-Common-ErrorMapping.json and the Track spec's error examples.
FEDEX_CARRIER_CODE_PREFIXES = (
	"CUSTOMER.",
	"USER.",
	"ACCOUNT.",
	"NOT.AUTHORIZED",
	"UNAUTHORIZED",
	"FORBIDDEN",
	"SERVICE.",
	"INTERNAL.",
	"SYSTEM.",
)
FEDEX_CARRIER_CODES = frozenset({"NOT.FOUND.ERROR"})
#: UPS error codes about the login or the request as a whole: missing transId / transactionSrc
#: (VSS002, VSS004), invalid or expired token (UJ0001, 250002, 251004) and the gateway's client,
#: merchant and quota codes (10401, 10403, 10429, 10004). From developer.ups.com's Tracking errors
#: and Get Started troubleshooting pages.
UPS_CARRIER_CODES = frozenset(
	{"VSS002", "VSS004", "UJ0001", "250002", "251004", "10401", "10403", "10429", "10004"}
)

#: Our words for each outcome, stored in a row's Last Problem field and shown on the banner. The
#: carrier's own error code and message are appended where it sent one.
OUTCOME_WORDS = {
	NOT_FOUND: "The carrier has no record of this number yet.",
	INVALID: "The carrier says this is not a valid tracking number.",
	AUTH_FAILED: "The carrier refused our API credentials. Check Carrier Tracking Settings.",
	RATE_LIMITED: "The carrier asked us to slow down. It will be checked again later.",
	UNAVAILABLE: "Could not reach the carrier. It will be checked again later.",
	BAD_RESPONSE: "The carrier's answer could not be read. It will be checked again later.",
	NOT_CONFIGURED: "This carrier is not set up in Carrier Tracking Settings.",
	REJECTED: "The carrier turned the request down. It will be checked again later.",
	UNSUPPORTED: C.NOT_TRACKABLE_REASON,
}

Config = namedtuple("Config", ["carrier", "environment", "client_id", "client_secret"])


# ---------------------------------------------------------------------------- results


def _result(config, number, now, outcome, error="", retry_after=None, **fields):
	result = {
		"carrier": getattr(config, "carrier", None) or "",
		"tracking_number": number,
		"outcome": outcome,
		"status": None,
		"description": "",
		"estimated_delivery": None,
		"delivered_at": None,
		"last_location": "",
		"checked_at": now.replace(microsecond=0).isoformat(sep=" "),
		"error": error,
		"retry_after": retry_after,
		"scope": None,
		"destination": "",
	}
	result.update(fields)
	return result


def rejected_scope(carrier, status, codes):
	"""``SCOPE_CARRIER`` or ``SCOPE_NUMBER`` for a REJECTED answer (see ``REJECTED``)."""
	codes = [str(code or "").strip().upper() for code in codes]
	if status not in (400, 404):
		return SCOPE_CARRIER
	if carrier == C.FEDEX and any(
		code in FEDEX_CARRIER_CODES or code.startswith(FEDEX_CARRIER_CODE_PREFIXES) for code in codes
	):
		return SCOPE_CARRIER
	if carrier == C.UPS and any(code in UPS_CARRIER_CODES for code in codes):
		return SCOPE_CARRIER
	if status == 404 and not codes:
		return SCOPE_CARRIER  # a 404 outside the carrier's error envelope is a wrong path
	return SCOPE_NUMBER


def _rejected(config, number, now, status, body, says, token):
	codes = [code for code, _ in _carrier_errors(body)]
	return _result(
		config,
		number,
		now,
		REJECTED,
		_message(REJECTED, config, says or f"HTTP {status}", token),
		scope=rejected_scope(config.carrier, status, codes),
	)


def _secrets(config, token=None):
	return (getattr(config, "client_id", None), getattr(config, "client_secret", None), token)


def _message(outcome, config, carrier_says=None, token=None):
	"""Our words for ``outcome``, plus the carrier's own error code and message, masked."""
	words = OUTCOME_WORDS.get(outcome, "")
	if carrier_says:
		words = f"{words} ({getattr(config, 'carrier', '')} said: {carrier_says})"
	return C.mask(words, _secrets(config, token))


# ------------------------------------------------------------------------------ cache


def _key(config, kind):
	"""Redis key for this carrier, environment and credential pair. The hash names the pair
	without storing it, so new credentials never reuse an old token."""
	digest = hashlib.sha256(f"{config.client_id}\0{config.client_secret}".encode()).hexdigest()[:16]
	return f"{CACHE_PREFIX}:{kind}:{config.carrier}:{config.environment}:{digest}"


def backoff_key(config):
	"""Not per credential: a busy carrier is busy for any key."""
	return f"{CACHE_PREFIX}:backoff:{config.carrier}:{config.environment}"


def _cache_get(cache, key):
	try:
		return cache.get(key)
	except Exception:
		return None


def _cache_set(cache, key, value, seconds):
	try:
		cache.set(key, value, max(1, int(seconds)))
	except Exception:
		pass


def _cache_delete(cache, key):
	try:
		cache.delete(key)
	except Exception:
		pass


# ------------------------------------------------------------------------------- HTTP


def _http():
	import requests

	return requests


def _json(response):
	"""The response's JSON object, or ``None`` when the body is not one."""
	try:
		body = response.json()
	except Exception:
		return None
	return body if isinstance(body, dict) else None


def _retry_after(response):
	try:
		value = int(str((response.headers or {}).get("Retry-After") or "").strip())
	except Exception:
		return DEFAULT_RETRY_AFTER
	return max(1, min(MAX_RETRY_AFTER, value))


def _carrier_errors(body):
	"""``[(code, message), ...]`` from either carrier's error envelope."""
	if not isinstance(body, dict):
		return []
	errors = body.get("errors")
	if errors is None:
		errors = (
			(body.get("response") or {}).get("errors") if isinstance(body.get("response"), dict) else None
		)
	out = []
	for item in errors if isinstance(errors, list) else []:
		if isinstance(item, dict):
			out.append((str(item.get("code") or ""), str(item.get("message") or "")))
	return out


def _says(body):
	errors = _carrier_errors(body)
	return "; ".join(f"{code} {message}".strip() for code, message in errors[:3]) or None


# ------------------------------------------------------------------------------ token


def _token(config, cache, http, now, force=False):
	"""``(token, None)`` or ``(None, failed_result_fields)``. Cached; see the module docstring."""
	token_key = _key(config, "token")
	refused_key = _key(config, "refused")
	if not force:
		cached = _cache_get(cache, token_key)
		if cached:
			return cached, None
	refused = _cache_get(cache, refused_key)
	if refused:
		return None, {"outcome": refused.get("outcome", "auth_failed"), "error": refused.get("error", "")}

	url = HOSTS[config.carrier][config.environment] + TOKEN_PATHS[config.carrier]
	headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
	if config.carrier == C.UPS:
		basic = base64.b64encode(f"{config.client_id}:{config.client_secret}".encode()).decode()
		headers["Authorization"] = "Basic " + basic
		data = {"grant_type": "client_credentials"}
	else:
		data = {
			"grant_type": "client_credentials",
			"client_id": config.client_id,
			"client_secret": config.client_secret,
		}
	try:
		response = http.request("POST", url, headers=headers, data=data, timeout=TIMEOUT)
	except Exception as exc:
		failed = {"outcome": UNAVAILABLE, "error": _message(UNAVAILABLE, config, type(exc).__name__)}
		_cache_set(cache, refused_key, failed, TOKEN_FAILURE_SECONDS)
		return None, failed
	finally:
		headers = data = None

	status = int(getattr(response, "status_code", 0) or 0)
	body = _json(response)
	if status == 200 and body and body.get("access_token"):
		token = str(body["access_token"])
		try:
			lifetime = int(str(body.get("expires_in")).strip())
		except Exception:
			lifetime = TOKEN_FALLBACK_SECONDS
		_cache_set(cache, token_key, token, max(60, lifetime - TOKEN_SAFETY_SECONDS))
		return token, None

	if status == 429 or (status == 403 and config.carrier == C.FEDEX):
		# FedEx's token endpoint punishes too many requests with a ten-minute 403.
		wait = _retry_after(response) if status == 429 else FEDEX_TOKEN_PENALTY_SECONDS
		failed = {
			"outcome": "rate_limited",
			"error": _message("rate_limited", config, _says(body)),
			"retry_after": wait,
		}
		_cache_set(cache, refused_key, failed, wait)
		return None, failed
	if status in (400, 401, 403):
		failed = {"outcome": "auth_failed", "error": _message("auth_failed", config, _says(body))}
		_cache_set(cache, refused_key, failed, AUTH_REFUSED_SECONDS)
		return None, failed
	# Anything else (a 5xx, a redirect, a 404, a 200 without a token, a page that is not JSON) is
	# the token endpoint not working, which is about the carrier and never about a package.
	failed = {"outcome": UNAVAILABLE, "error": _message(UNAVAILABLE, config, _says(body) or f"HTTP {status}")}
	_cache_set(cache, refused_key, failed, TOKEN_FAILURE_SECONDS)
	return None, failed


def forget_token(config, cache):
	_cache_delete(cache, _key(config, "token"))
	_cache_delete(cache, _key(config, "refused"))


# ------------------------------------------------------------------------------ track


def is_configured(config):
	return bool(
		config
		and config.carrier in C.TRACKABLE
		and config.environment in (SANDBOX, PRODUCTION)
		and config.client_id
		and config.client_secret
	)


def track(config, number, cache, http=None, now=None):
	"""What the carrier says about ``number``. Never raises; see the module docstring."""
	now = now or datetime.now()
	number = C.clean_number(number)
	try:
		if not config or config.carrier not in C.TRACKABLE:
			return _result(config, number, now, "unsupported", _message("unsupported", config))
		if not is_configured(config):
			return _result(config, number, now, "not_configured", _message("not_configured", config))
		if not C.is_plausible(number, config.carrier):
			return _result(config, number, now, "invalid", _message("invalid", config))

		waiting = _cache_get(cache, backoff_key(config))
		if waiting:
			return _result(
				config, number, now, "rate_limited", _message("rate_limited", config), retry_after=waiting
			)

		http = http or _http()
		token, failed = _token(config, cache, http, now)
		if failed:
			return _result(config, number, now, **failed)

		response = _call(config, number, token, http)
		if response is not None and int(getattr(response, "status_code", 0) or 0) == 401:
			# The token expired early or was revoked: get a new one once, then try once more.
			_cache_delete(cache, _key(config, "token"))
			token, failed = _token(config, cache, http, now, force=True)
			if failed:
				return _result(config, number, now, **failed)
			response = _call(config, number, token, http)
		return _interpret(config, number, response, token, cache, now)
	except Exception as exc:
		return _result(
			config, number, now, "bad_response", _message("bad_response", config, type(exc).__name__)
		)


def _call(config, number, token, http):
	"""The Track request. Returns the response, or ``None`` when no answer arrived."""
	base = HOSTS[config.carrier][config.environment]
	try:
		if config.carrier == C.FEDEX:
			return http.request(
				"POST",
				base + FEDEX_TRACK_PATH,
				headers={
					"Authorization": "Bearer " + token,
					"Content-Type": "application/json",
					"X-locale": "en_US",
				},
				json={
					"includeDetailedScans": False,
					"trackingInfo": [{"trackingNumberInfo": {"trackingNumber": number}}],
				},
				timeout=TIMEOUT,
			)
		return http.request(
			"GET",
			base + UPS_TRACK_PATH.format(number=number),
			headers={
				"Authorization": "Bearer " + token,
				"transId": uuid.uuid4().hex,
				"transactionSrc": UPS_TRANSACTION_SRC,
				"Accept": "application/json",
			},
			params={
				"locale": "en_US",
				"returnSignature": "false",
				"returnMilestones": "false",
				"returnPOD": "false",
			},
			timeout=TIMEOUT,
		)
	except Exception:
		return None


def _interpret(config, number, response, token, cache, now):
	if response is None:
		return _result(config, number, now, "unavailable", _message("unavailable", config, None, token))
	status = int(getattr(response, "status_code", 0) or 0)
	body = _json(response)
	says = _says(body)

	if status == 429:
		wait = _retry_after(response)
		_cache_set(cache, backoff_key(config), wait, wait)
		return _result(
			config,
			number,
			now,
			"rate_limited",
			_message("rate_limited", config, says, token),
			retry_after=wait,
		)
	if status >= 500:
		return _result(
			config,
			number,
			now,
			"unavailable",
			_message("unavailable", config, says or f"HTTP {status}", token),
		)
	if status in (401, 403):
		_cache_delete(cache, _key(config, "token"))
		return _result(config, number, now, "auth_failed", _message("auth_failed", config, says, token))
	if body is None:
		# A body that is not JSON is never about one package: a 200 with an HTML page is the carrier
		# (or a proxy in front of it) not working, and any other status is a wrong path or an error
		# page. Neither may count against a package (review round 2).
		if status == 200:
			return _result(
				config,
				number,
				now,
				UNAVAILABLE,
				_message(UNAVAILABLE, config, "a page that is not JSON", token),
			)
		return _result(
			config,
			number,
			now,
			REJECTED,
			_message(REJECTED, config, f"HTTP {status}", token),
			scope=SCOPE_CARRIER,
		)

	if config.carrier == C.FEDEX:
		return _interpret_fedex(config, number, status, body, says, token, now)
	return _interpret_ups(config, number, status, body, says, token, now)


def _from_codes(codes):
	for code in codes:
		outcome = N.fedex_error_outcome(code)
		if outcome:
			return outcome
	return None


def _alert_codes(output):
	"""``output.alerts`` as FedEx documents it: an array of ``{code, alertType, message}``.

	A bare string is read too, since FedEx's own schema description shows its example that way.
	"""
	alerts = output.get("alerts")
	if isinstance(alerts, str):
		alerts = [{"code": alerts.split(" ")[0], "message": alerts}]
	out = []
	for alert in alerts if isinstance(alerts, list) else []:
		if isinstance(alert, dict):
			out.append((str(alert.get("code") or ""), str(alert.get("message") or "")))
	return out


def _interpret_fedex(config, number, status, body, says, token, now):
	if status != 200:
		# Only FedEx's documented package-level codes say anything about the package. Every other
		# error (CUSTOMER.USAGE.LOCKED, USER.RELOGIN.REQUIRED, NOT.FOUND.ERROR for a wrong path, a
		# locale or size code) is the carrier turning the request down.
		outcome = _from_codes(code for code, _ in _carrier_errors(body))
		if not outcome:
			return _rejected(config, number, now, status, body, says, token)
		return _result(
			config, number, now, outcome, _message(outcome, config, says or f"HTTP {status}", token)
		)
	output = body.get("output") if isinstance(body.get("output"), dict) else {}
	complete = output.get("completeTrackResults")
	entry = complete[0] if isinstance(complete, list) and complete and isinstance(complete[0], dict) else {}
	results = entry.get("trackResults")
	track_result = (
		results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else None
	)
	if track_result is None:
		alerts = _alert_codes(output)
		outcome = _from_codes(code for code, _ in alerts) or BAD_RESPONSE
		text = "; ".join(f"{code} {message}".strip() for code, message in alerts[:3]) or None
		return _result(config, number, now, outcome, _message(outcome, config, text, token))
	error = track_result.get("error")
	if isinstance(error, dict) and (error.get("code") or error.get("message")):
		# An error inside this package's own result is about the package; an unknown one is "could
		# not check" for it, which counts towards MAX_ROW_FAILURES but never stops it at once.
		outcome = N.fedex_error_outcome(error.get("code")) or BAD_RESPONSE
		text = f"{error.get('code') or ''} {error.get('message') or ''}".strip()
		return _result(config, number, now, outcome, _message(outcome, config, text, token))
	fields = N.fedex_package(track_result)
	fields["description"] = C.mask(fields["description"], _secrets(config, token), 240)
	return _result(config, number, now, "ok", **fields)


def _interpret_ups(config, number, status, body, says, token, now):
	# Tracking.yaml documents 404 as "Tracking number information not found", in its error
	# envelope. A 404 without that envelope is a wrong path, and a 400 is documented only as
	# "Invalid request": both are the request, not the package, so neither stops anything.
	if status == 404 and _carrier_errors(body):
		return _result(config, number, now, NOT_FOUND, _message(NOT_FOUND, config, says, token))
	if status != 200:
		return _rejected(config, number, now, status, body, says, token)
	track_response = body.get("trackResponse") if isinstance(body.get("trackResponse"), dict) else {}
	shipments = track_response.get("shipment")
	shipment = (
		shipments[0] if isinstance(shipments, list) and shipments and isinstance(shipments[0], dict) else {}
	)
	packages = shipment.get("package")
	package = None
	if isinstance(packages, list):
		matching = [
			p for p in packages if isinstance(p, dict) and C.clean_number(p.get("trackingNumber")) == number
		]
		package = (matching or [p for p in packages if isinstance(p, dict)] or [None])[0]
	if package is None:
		warnings = shipment.get("warnings")
		text = (
			"; ".join(
				f"{w.get('code') or ''} {w.get('message') or ''}".strip()
				for w in warnings
				if isinstance(w, dict)
			)
			if isinstance(warnings, list)
			else ""
		)
		# UPS documents no warning codes, so a shipment with warnings and no package is "could not
		# check", not "not found".
		return _result(config, number, now, BAD_RESPONSE, _message(BAD_RESPONSE, config, text or None, token))
	fields = N.ups_package(package)
	fields["description"] = C.mask(fields["description"], _secrets(config, token), 240)
	return _result(config, number, now, "ok", **fields)
