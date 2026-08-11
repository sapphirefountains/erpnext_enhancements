"""Inbound Google Chat webhook: the JWT verifier, and a Phase 1 handler that does nothing.

`handle()` is `allow_guest=True` because Google is not an authenticated Frappe user.
That makes this the most exposed function in the app — a world-reachable POST endpoint
that Phase 2 will wire to the message store. **The JWT is the only thing between it and
an open relay.** Treat every edit to this module as a change to a security boundary.

The verifier is written first and the handler second, deliberately: the ordering of the
file mirrors the ordering the request must obey.

Verification, in the order the request meets it (ADR §G.6, Phase 1 prompt §4.6):

  1. `Authorization: Bearer <token>` — missing or malformed is a 401 before anything else.
  2. The token is verified **before** we parse the body and before we touch a table.
  3. Google Chat's bearer token is *always* issued by `chat@system.gserviceaccount.com`,
     in both audience modes.
  4. Endpoint-URL mode (the recommended one, and ours): verify as a Google-signed OIDC ID
     token whose `aud` is the configured endpoint URL, then check `email == CHAT_ISSUER`
     and `email_verified`. **The email check is the single most important line in this
     module.** `google.oauth2.id_token.verify_oauth2_token()` checks the signature, `exp`
     and `aud` — it does *not* check the `email` claim. Without the explicit check, any
     GCP project on earth can mint a valid ID token for our audience and inject messages
     into a company's conversation record (ADR §G.6.2).
  5. Project-number mode: verify the self-signed JWT against Google's x509 certs for
     `chat@system.gserviceaccount.com`, selecting the cert by the JWT `kid`, then check
     `iss`, `aud == <project number>` and `exp`.
  6. The audience must match the configured value **byte for byte**, trailing slash
     included. A mismatch is logged with both values so it reads as the configuration
     error it is, rather than as a signing mystery. It is never swallowed.
  7. Any failure is a `401`. Never 200-and-ignore: a 200 destroys the only signal that
     distinguishes an attack from a misconfiguration.
  8. Google's public certs are cached honouring `Cache-Control: max-age`, so a live cert
     download does not sit on the hot path of every inbound event.

**Never substitute an IP allowlist for this check, and do not let a future security review
"improve" it into one.** Google publishes no Chat-specific egress range; the only published
ranges (`goog.json`, `cloud.json`) are org-wide and documented as allocated dynamically and
changing often. Allowlisting them would admit every Google customer's Cloud resources —
weaker than no control at all in the threat model that matters, because the attack is a
forged token from *any* GCP project, not a packet from an unexpected address. Pinning the
issuer identity authenticates *who* rather than *where from*. This was checked at design
time and re-checked at implementation time; it remains true (ADR §G.6.1, risk R04-V15).

Two things this module deliberately does not do:

  * **It never logs the token and never logs the request body on a rejection.** A rejected
    body is attacker-controlled, and this repo has already been bitten by frame locals
    reaching the Error Log.
  * **It writes no Error Log row on a rejection.** Rejections go to the file logger. An
    Error Log insert per rejected request is a DB write an anonymous caller can trigger at
    will — a rejection path that floods the database is its own outage. The one exception
    is an unconfigured audience, which is an operator error rather than an attack and is
    throttled to one row an hour.

Phase 1 scope: verify, log the event type, return `200` with an empty body. Parsing,
dispatch, dedupe and the message write are Phase 2 (ADR §G.4). Do not add them here.

Checkpoint proof — the unauthenticated `401`. **Not executed in this session: this
environment has no bench and the Google Cloud runbook has not been confirmed executed.
A human must run these against the deployed endpoint and record the output.**

    # no Authorization header at all
    curl -s -o /dev/null -w '%{http_code}\n' -X POST \
      https://<erp-host>/api/method/erpnext_enhancements.chat.gchat.webhook.handle \
      -H 'Content-Type: application/json' -d '{}'
    # expect: 401

    # a syntactically plausible but unsigned bearer
    curl -s -o /dev/null -w '%{http_code}\n' -X POST \
      https://<erp-host>/api/method/erpnext_enhancements.chat.gchat.webhook.handle \
      -H 'Content-Type: application/json' \
      -H 'Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.e30.bm90LWEtc2lnbmF0dXJl' \
      -d '{}'
    # expect: 401

The escape hatch, recorded so it is not rediscovered: if this endpoint ever terminates on
Cloud Run, granting `roles/run.invoker` to `chat@system.gserviceaccount.com` offloads the
entire check to the platform and this module becomes deletable (ADR §G.6.3).
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
from collections.abc import Mapping as AbcMapping
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

import frappe
from frappe.rate_limiter import rate_limit

# The one identity Google Chat signs with, in BOTH audience modes. Hard-coded on purpose:
# making it configurable would turn the security boundary into a settings field somebody
# can widen from the Desk.
CHAT_ISSUER = "chat@system.gserviceaccount.com"

# Google's x509 certs for that identity. Used only by the project-number path; the
# endpoint-URL path lets google-auth choose its own key document.
# `VERIFY:` ADR §G.6 flags that the JWK form of this URL was seen only in secondary
# sources. It blocks nothing under `verify_oauth2_token`; it matters only here.
CHAT_ISSUER_CERTS_URL = (
	"https://www.googleapis.com/service_accounts/v1/metadata/x509/"
	"chat@system.gserviceaccount.com"
)

AUDIENCE_MODE_ENDPOINT_URL = "endpoint_url"
AUDIENCE_MODE_PROJECT_NUMBER = "project_number"

# Rejection reasons. Stable slugs, not prose: they are what the tests assert on and what a
# human greps the file log for. None of them ever reaches the HTTP response body.
REJECT_MISSING_BEARER = "missing_bearer"
REJECT_MALFORMED_TOKEN = "malformed_token"
REJECT_AUDIENCE_NOT_CONFIGURED = "audience_not_configured"
REJECT_SIGNATURE_OR_EXPIRY = "signature_expiry_or_audience"
REJECT_AUDIENCE_MISMATCH = "audience_mismatch"
REJECT_EXPIRED = "expired"
REJECT_ISSUER_IDENTITY = "issuer_identity"
REJECT_EMAIL_NOT_VERIFIED = "email_not_verified"
REJECT_UNSUPPORTED_ALG = "unsupported_alg"
REJECT_UNKNOWN_KID = "unknown_kid"
REJECT_VERIFIER_UNAVAILABLE = "verifier_unavailable"
REJECT_MALFORMED_BODY = "malformed_body"

# Cert caching. `expires_in_sec` is clamped so a hostile or absent `Cache-Control` cannot
# pin a stale key document forever, nor collapse the cache into a per-request fetch.
CERT_CACHE_PREFIX = "chat:gchat:certs:"
CERT_CACHE_MIN_TTL = 300
CERT_CACHE_MAX_TTL = 86400
CERT_CACHE_DEFAULT_TTL = 3600

_MAX_AGE_RE = re.compile(r"max-age\s*=\s*(\d+)", re.IGNORECASE)

# Truncation applied to anything attacker-controlled before it reaches a log line.
_LOG_CLIP = 200

#: `() -> {kid: x509 PEM certificate}`. Injected by the tests so the six negative cases
#: run with no network access.
CertFetcher = Callable[[], Mapping[str, str]]

#: `(token, audience) -> claims`, raising on any verification failure. This is the crypto
#: boundary the tests mock; the identity checks around it are what the tests exercise.
OidcVerifier = Callable[[str, str], Mapping[str, Any]]


# ---------------------------------------------------------------------------
# The verifier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerificationResult:
	"""The outcome of one verification.

	A result object rather than a raised exception, because the caller has to be able to
	distinguish *which* check failed in order to log it, and because a raise out of a
	guest endpoint publishes frame locals — here, the token — to the Error Log.

	`detail` is for the file log only. It never reaches the HTTP response.
	"""

	ok: bool
	reason: str = ""
	claims: Mapping[str, Any] = field(default_factory=dict)
	detail: str = ""

	def __bool__(self) -> bool:
		return self.ok


def _accept(claims: Mapping[str, Any]) -> VerificationResult:
	return VerificationResult(ok=True, claims=dict(claims))


def _reject(reason: str, detail: str = "") -> VerificationResult:
	return VerificationResult(ok=False, reason=reason, detail=detail)


def _clip(value: Any) -> str:
	"""Render an attacker-controlled value short enough to be safe in a log line."""
	text = str(value)
	return text if len(text) <= _LOG_CLIP else text[:_LOG_CLIP] + "…"


def extract_bearer_token(header: Optional[str]) -> str:
	"""`Authorization: Bearer <token>` -> the token, or `""` when absent or malformed.

	Returns a string rather than raising so that step 1 of the verification order is a
	plain truthiness check at the top of the handler. The scheme comparison is
	case-insensitive (RFC 7235 makes it so); the token itself is not touched.
	"""
	value = (header or "").strip()
	if not value:
		return ""
	scheme, _, token = value.partition(" ")
	if scheme.lower() != "bearer":
		return ""
	token = token.strip()
	# A space inside the credential means this is not a bearer token, whatever it is.
	if not token or " " in token:
		return ""
	return token


def _looks_like_a_jwt(token: str) -> bool:
	"""Three non-empty dot-separated segments. Cheap structural gate before any crypto."""
	parts = token.split(".")
	return len(parts) == 3 and all(parts)


def _b64url_decode(segment: str) -> bytes:
	padding = "=" * (-len(segment) % 4)
	return base64.urlsafe_b64decode(segment + padding)


def _decode_jwt_header(token: str) -> Optional[dict[str, Any]]:
	"""The unverified JOSE header, or None if it is not decodable JSON.

	Unverified by definition — it is read only to pick the `kid` and to reject an
	unexpected `alg` *before* any signature work. Nothing in it is trusted.
	"""
	try:
		raw = _b64url_decode(token.split(".", 1)[0])
		header = json.loads(raw.decode("utf-8"))
	except (ValueError, binascii.Error, UnicodeDecodeError, IndexError):
		return None
	return header if isinstance(header, dict) else None


def cache_ttl_from_headers(headers: Mapping[str, str]) -> int:
	"""Seconds to cache a Google key document, from its `Cache-Control: max-age`.

	Pure, so the clamping is testable without a network. Clamped both ways: a missing or
	unparseable header must not mean "fetch every request", and a very long `max-age`
	must not outlive a key rotation we would otherwise pick up.
	"""
	control = ""
	for key, value in (headers or {}).items():
		if str(key).lower() == "cache-control":
			control = str(value)
			break
	match = _MAX_AGE_RE.search(control)
	if not match:
		return CERT_CACHE_DEFAULT_TTL
	try:
		max_age = int(match.group(1))
	except ValueError:
		return CERT_CACHE_DEFAULT_TTL
	return max(CERT_CACHE_MIN_TTL, min(CERT_CACHE_MAX_TTL, max_age))


class _CachedHttpResponse:
	"""The minimal google-auth transport response surface: `status`, `data`, `headers`."""

	__slots__ = ("status", "data", "headers")

	def __init__(self, status: int, data: bytes, headers: Mapping[str, str]) -> None:
		self.status = status
		self.data = data
		self.headers = dict(headers or {})


class CachingGoogleAuthRequest:
	"""A `google.auth.transport.Request` that serves repeat GETs from `frappe.cache()`.

	`id_token.verify_oauth2_token()` re-downloads Google's signing certificates on *every*
	call. On this endpoint that is one outbound TLS round trip per inbound Chat event,
	sitting in front of the message write — and it means a transient `googleapis.com`
	blip 401s every legitimate request. Google serves those documents with a long
	`Cache-Control: max-age`; honouring it turns the round trip into a Redis read and
	keeps verification working for as long as the cached document is valid.

	Only cacheable GETs are intercepted; anything else is passed straight through, so
	swapping this in for the stock transport cannot change what google-auth does.
	"""

	def __init__(self, inner: Any = None) -> None:
		self._inner = inner

	def _transport(self) -> Any:
		if self._inner is None:
			from google.auth.transport import requests as google_requests

			self._inner = google_requests.Request()
		return self._inner

	def __call__(
		self,
		url: str,
		method: str = "GET",
		body: Any = None,
		headers: Optional[Mapping[str, str]] = None,
		timeout: Any = None,
		**kwargs: Any,
	) -> Any:
		passthrough = str(method).upper() != "GET" or body
		if passthrough:
			return self._transport()(
				url, method=method, body=body, headers=headers, timeout=timeout, **kwargs
			)
		cache = frappe.cache()
		key = CERT_CACHE_PREFIX + str(url)
		cached = cache.get_value(key)
		if cached:
			return _CachedHttpResponse(200, cached.get("data") or b"", cached.get("headers") or {})
		response = self._transport()(url, method=method, headers=headers, timeout=timeout, **kwargs)
		if getattr(response, "status", 0) == 200:
			payload = {
				"data": response.data,
				"headers": dict(getattr(response, "headers", {}) or {}),
			}
			cache.set_value(key, payload, expires_in_sec=cache_ttl_from_headers(payload["headers"]))
		return response


def fetch_chat_issuer_certs(force_refresh: bool = False) -> Mapping[str, str]:
	"""`{kid: x509 PEM}` for `chat@system.gserviceaccount.com`, cached per `Cache-Control`.

	The default `cert_fetcher` for project-number mode. Kept out of the hot path by the
	same cache the endpoint-URL path uses, for the same reason.
	"""
	cache = frappe.cache()
	key = CERT_CACHE_PREFIX + CHAT_ISSUER_CERTS_URL
	if not force_refresh:
		cached = cache.get_value(key)
		if cached:
			return cached
	import requests

	response = requests.get(CHAT_ISSUER_CERTS_URL, timeout=10)
	response.raise_for_status()
	certs = response.json()
	cache.set_value(key, certs, expires_in_sec=cache_ttl_from_headers(response.headers))
	return certs


def _default_oidc_verifier(token: str, audience: str) -> Mapping[str, Any]:
	"""Signature + `exp` + `aud`, via google-auth. Identity is checked by the caller.

	Imported lazily. `google.oauth2` and `google.auth.transport.requests` are both
	importable on the prod bench (measured, ADR §G.6.3) but are not guaranteed in the
	bench-free CI image, and this module must import there for the negative tests to run.
	"""
	from google.oauth2 import id_token

	return id_token.verify_oauth2_token(token, CachingGoogleAuthRequest(), audience)


def _is_expired(claims: Mapping[str, Any], now: Optional[float], leeway: int) -> bool:
	"""A claim set with no usable `exp` counts as expired — fail closed, never open."""
	try:
		exp = int(claims.get("exp"))
	except (TypeError, ValueError):
		return True
	current = time.time() if now is None else now
	return current > exp + leeway


def _check_shared_claims(
	claims: Mapping[str, Any],
	audience: str,
	now: Optional[float],
	leeway: int,
) -> Optional[VerificationResult]:
	"""`aud` byte-for-byte and `exp`. Returns a rejection, or None when both pass.

	`aud` is re-checked here even though both verification paths already check it. The
	duplication is deliberate: it is the one check whose failure is a configuration error
	rather than an attack, and checking it ourselves is what lets the log line name both
	values. A byte-for-byte comparison means a trailing slash on one side and not the
	other is a mismatch — which is exactly what the Cloud console entry does to people.
	"""
	if not isinstance(claims, AbcMapping):
		return _reject(REJECT_MALFORMED_TOKEN, "verifier returned a non-mapping claim set")
	aud = claims.get("aud")
	if aud != audience:
		return _reject(
			REJECT_AUDIENCE_MISMATCH,
			f"token aud {_clip(aud)!r} != configured audience {audience!r} "
			"(must match the Chat API console entry byte for byte, trailing slash included)",
		)
	if _is_expired(claims, now, leeway):
		return _reject(REJECT_EXPIRED, "exp is absent, unparseable or in the past")
	return None


def verify_chat_bearer_token(
	token: str,
	audience: str,
	cert_fetcher: Optional[CertFetcher] = None,
	*,
	audience_mode: str = AUDIENCE_MODE_ENDPOINT_URL,
	oidc_verifier: Optional[OidcVerifier] = None,
	now: Optional[float] = None,
	leeway: int = 0,
) -> VerificationResult:
	"""Decide whether a bearer token really came from Google Chat. The whole boundary.

	Pure with respect to Frappe: it reads no settings, touches no table and — with
	`cert_fetcher` / `oidc_verifier` supplied — performs no I/O, which is what lets
	`tests/test_chat_webhook_verify.py` drive every negative case bench-free and offline.

	Args:
		token: the raw credential from the `Authorization` header.
		audience: the expected `aud`, compared byte for byte. Never normalised.
		cert_fetcher: `() -> {kid: x509 PEM}`; project-number mode only.
		audience_mode: `endpoint_url` (OIDC ID token) or `project_number` (self-signed JWT).
		oidc_verifier: `(token, audience) -> claims`; endpoint-URL mode only.
		now: unix seconds, for deterministic expiry tests.
		leeway: seconds of clock skew tolerated on `exp`. Zero by default.

	Returns:
		A `VerificationResult`. Truthy only when *every* check passed.
	"""
	if not audience:
		return _reject(REJECT_AUDIENCE_NOT_CONFIGURED, "Chat Settings has no interaction endpoint URL")
	if not token or not isinstance(token, str):
		return _reject(REJECT_MISSING_BEARER)
	if not _looks_like_a_jwt(token):
		return _reject(REJECT_MALFORMED_TOKEN, "not three non-empty dot-separated segments")

	if audience_mode == AUDIENCE_MODE_PROJECT_NUMBER:
		return _verify_project_number_jwt(token, audience, cert_fetcher, now, leeway)
	return _verify_oidc_id_token(token, audience, oidc_verifier, now, leeway)


def _verify_oidc_id_token(
	token: str,
	audience: str,
	oidc_verifier: Optional[OidcVerifier],
	now: Optional[float],
	leeway: int,
) -> VerificationResult:
	"""Endpoint-URL mode: Google-signed OIDC ID token whose `aud` is our endpoint URL."""
	verifier = oidc_verifier or _default_oidc_verifier
	try:
		claims = verifier(token, audience)
	except ImportError as exc:
		# google-auth absent is an operator problem, not an attack. Distinguish it, or
		# every request 401s and the log says "bad signature".
		return _reject(REJECT_VERIFIER_UNAVAILABLE, _clip(exc))
	except Exception as exc:  # noqa: BLE001 - google-auth raises a wide family here
		return _reject(REJECT_SIGNATURE_OR_EXPIRY, f"{type(exc).__name__}: {_clip(exc)}")

	rejection = _check_shared_claims(claims, audience, now, leeway)
	if rejection is not None:
		return rejection

	# ---- THE line. -------------------------------------------------------------------
	# verify_oauth2_token() above checked the signature, exp and aud. It did NOT check
	# who signed it. Delete this and any Google service account in the world — i.e.
	# anybody with a GCP project — can mint a token our endpoint accepts, and the endpoint
	# writes to the company's message store. ADR §G.6.2.
	if claims.get("email") != CHAT_ISSUER:
		return _reject(REJECT_ISSUER_IDENTITY, f"email claim {_clip(claims.get('email'))!r}")
	# ----------------------------------------------------------------------------------

	# Google's own instruction is to require the claim be asserted as verified, not merely
	# present. Accept the string form too: the claim is JSON and has been seen both ways.
	if claims.get("email_verified") not in (True, "true", "True"):
		return _reject(REJECT_EMAIL_NOT_VERIFIED, _clip(claims.get("email_verified")))

	return _accept(claims)


def _verify_project_number_jwt(
	token: str,
	audience: str,
	cert_fetcher: Optional[CertFetcher],
	now: Optional[float],
	leeway: int,
) -> VerificationResult:
	"""Project-number mode: self-signed JWT, verified against Google's x509 certs.

	The alternative Chat configuration, where the Authentication Audience is the Cloud
	project number instead of the endpoint URL. Same issuer identity, different token
	type and a hand-rolled key selection — which is why the `kid` and `alg` are pinned
	before any signature work happens.
	"""
	header = _decode_jwt_header(token)
	if header is None:
		return _reject(REJECT_MALFORMED_TOKEN, "undecodable JOSE header")
	if str(header.get("alg") or "").upper() != "RS256":
		# Pinning the algorithm is what stops the `alg: none` and HMAC-confusion families.
		return _reject(REJECT_UNSUPPORTED_ALG, _clip(header.get("alg")))
	kid = header.get("kid")
	if not kid or not isinstance(kid, str):
		return _reject(REJECT_MALFORMED_TOKEN, "no kid in the JOSE header")

	fetch = cert_fetcher or fetch_chat_issuer_certs
	try:
		certs = fetch() or {}
	except Exception as exc:  # noqa: BLE001 - any transport failure is the same outcome
		return _reject(REJECT_VERIFIER_UNAVAILABLE, f"{type(exc).__name__}: {_clip(exc)}")
	certificate = certs.get(kid)
	if not certificate:
		# Either a rotation we have cached past, or a token signed by a key that is not
		# Google's. Both are a 401; a cache-refresh retry belongs in Phase 2's ops work.
		return _reject(REJECT_UNKNOWN_KID, _clip(kid))

	try:
		from google.auth import jwt as google_jwt
	except ImportError as exc:
		return _reject(REJECT_VERIFIER_UNAVAILABLE, _clip(exc))
	try:
		claims = google_jwt.decode(token, certs={kid: certificate}, verify=True, audience=audience)
	except Exception as exc:  # noqa: BLE001 - google-auth raises a wide family here
		return _reject(REJECT_SIGNATURE_OR_EXPIRY, f"{type(exc).__name__}: {_clip(exc)}")

	rejection = _check_shared_claims(claims, audience, now, leeway)
	if rejection is not None:
		return rejection

	# The same identity check as endpoint-URL mode, on the claim this token type carries
	# it in. Google guarantees the identity in both modes; only the claim name differs.
	if claims.get("iss") != CHAT_ISSUER:
		return _reject(REJECT_ISSUER_IDENTITY, f"iss claim {_clip(claims.get('iss'))!r}")
	if "email" in claims and claims.get("email") != CHAT_ISSUER:
		return _reject(REJECT_ISSUER_IDENTITY, f"email claim {_clip(claims.get('email'))!r}")

	return _accept(claims)


def get_expected_audience() -> tuple[str, str]:
	"""`(audience, audience_mode)` from `Chat Settings`. The only pre-verification read.

	Reading a Single from the document cache is not a query against attacker-controlled
	data and cannot be influenced by the request — the rule the ADR states is that the
	*body* is not parsed and nothing is *written* before the token is verified, and both
	hold.

	The field is `interaction_endpoint_url` per ADR §G.6.3 and the shipped `Chat Settings`
	JSON. The Phase 1 prompt calls the same field `webhook_audience_url`; the ADR wins, and
	the fallback below exists only so a site that was configured against the prompt's name
	does not silently 401 every request. See the divergence note in the handover.

	Whitespace is stripped; **a trailing slash is not**. Byte-for-byte means byte-for-byte.

	An all-digits value is taken to be a Cloud project number and selects the self-signed
	JWT path, which is how the two audience modes are distinguished without a second field.
	"""
	settings = frappe.get_cached_doc("Chat Settings")
	audience = (getattr(settings, "interaction_endpoint_url", None) or "").strip()
	if not audience:
		audience = (getattr(settings, "webhook_audience_url", None) or "").strip()
	mode = AUDIENCE_MODE_PROJECT_NUMBER if audience.isdigit() else AUDIENCE_MODE_ENDPOINT_URL
	return audience, mode


def verify_chat_request() -> VerificationResult:
	"""Verify the *current* request. The ADR's `verify_chat_request`, returning not raising.

	ADR §G.6.3 sketches this as raising `frappe.AuthenticationError`. It returns a result
	instead: a raise out of a guest endpoint publishes frame locals — here, the bearer
	token — to the Error Log, which is a failure mode this repo has already paid for. The
	handler turns a falsy result into the same `401` the ADR requires.
	"""
	token = extract_bearer_token(frappe.get_request_header("Authorization"))
	if not token:
		return _reject(REJECT_MISSING_BEARER)
	try:
		audience, mode = get_expected_audience()
	except Exception as exc:  # noqa: BLE001 - an unreadable Single must still be a 401
		return _reject(REJECT_AUDIENCE_NOT_CONFIGURED, f"{type(exc).__name__}: {_clip(exc)}")
	if not audience:
		return _reject(REJECT_AUDIENCE_NOT_CONFIGURED, "Chat Settings.interaction_endpoint_url is empty")
	return verify_chat_bearer_token(token, audience, audience_mode=mode)


# ---------------------------------------------------------------------------
# The handler — and in Phase 1 it deliberately does nothing
# ---------------------------------------------------------------------------


def _log_rejection(reason: str, detail: str = "") -> None:
	"""File-log a rejection. Never the token, never the body, never an Error Log row.

	The file logger rather than `frappe.log_error` because an Error Log insert is a DB
	write an anonymous caller can trigger at will on a world-reachable endpoint. The
	rejection is still recorded — losing the signal is the thing rule 7 exists to prevent —
	just not in a table an attacker can grow.

	A logging subsystem that itself fails must not convert a 401 into a 500, hence the
	bare guard.
	"""
	message = f"Chat webhook rejected: {reason}"
	if detail:
		message = f"{message} — {detail}"
	try:
		frappe.logger("chat.gchat.webhook", allow_site=True).warning(message)
	except Exception:  # noqa: BLE001 - logging must never change the response
		pass


def _log_misconfiguration(detail: str) -> None:
	"""One Error Log row an hour for the one condition that is an operator error.

	An unconfigured or unreadable audience 401s every legitimate Chat request, and the
	symptom — universal 401s — reads exactly like a signing problem. Somebody has to be
	told. Throttled through Redis so that an anonymous caller cannot turn "tell somebody"
	into an Error Log flood.
	"""
	try:
		cache = frappe.cache()
		key = "chat:gchat:webhook_misconfig_logged"
		if cache.get_value(key):
			return
		cache.set_value(key, 1, expires_in_sec=3600)
		frappe.log_error(
			title="Chat Inbound Auth",
			message=(
				"The Google Chat webhook is rejecting every request because its JWT audience "
				"is not configured. Set Chat Settings > Interaction Endpoint URL to the exact "
				f"URL registered in the Chat API console.\n\nDetail: {detail}"
			),
		)
	except Exception:  # noqa: BLE001 - never let the alerting path change the response
		pass


def _unauthorized(reason: str, detail: str = "") -> dict[str, Any]:
	"""401, empty body, no detail to the caller. Google's documented expectation."""
	_log_rejection(reason, detail)
	if reason == REJECT_AUDIENCE_NOT_CONFIGURED:
		_log_misconfiguration(detail)
	frappe.local.response.http_status_code = 401
	return {}


def _verified_payload() -> Optional[dict[str, Any]]:
	"""The whole request body as a dict, **strictly after verification**.

	Phase 5's dispatch needs the message and the space, not just the event type. Kept as a
	separate function from :func:`_peek_event_type` so the Phase 1 reasoning above stays
	readable: the peek is deliberately minimal and this is deliberately not, and merging them
	would make the minimal one look like an accident.

	Same caveat as the peek: Frappe populates ``form_dict`` from the request before any app
	code runs, so "after verification" means after *ours*. Nothing here can move the
	framework's own pre-parse.
	"""
	request = getattr(frappe, "request", None)
	raw = request.get_data() if request is not None else b""
	if not raw:
		return None
	try:
		payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
	except (UnicodeDecodeError, ValueError):
		return None
	return payload if isinstance(payload, dict) else None


def _peek_event_type() -> Optional[str]:
	"""The event `type`, for the Phase 1 log line. None when the body is not a JSON object.

	The only read of the request body in Phase 1, and it happens strictly after
	verification. It extracts one short string and dispatches on nothing — parsing,
	dedupe and dispatch are Phase 2 (ADR §G.4).

	Caveat worth stating once: Frappe populates `form_dict` from the request before any
	app code runs, so "before body parsing" means before *ours*. Nothing in this module
	can move the framework's own pre-parse, and no app-level check can either.
	"""
	request = getattr(frappe, "request", None)
	raw = request.get_data() if request is not None else b""
	if not raw:
		return None
	try:
		payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
	except (UnicodeDecodeError, ValueError):
		return None
	if not isinstance(payload, dict):
		return None
	event_type = payload.get("type") or payload.get("eventType") or ""
	if not isinstance(event_type, str):
		return None
	return event_type[:64]


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=600, seconds=60, methods=["POST"])
def handle() -> dict[str, Any]:
	"""The inbound Google Chat endpoint. Phase 1: verify, log the event type, return 200.

	Path — keep it stable, because the JWT audience is this URL byte for byte and changing
	it invalidates every token Google mints for us:

	    /api/method/erpnext_enhancements.chat.gchat.webhook.handle

	`allow_guest=True` is not optional: Google is not a Frappe user. The `@rate_limit`
	above it is a cost control, not the security boundary — its dimension is the client IP,
	which is spoofable, and Google's Chat egress addresses are neither stable nor
	published. The JWT is the boundary.

	Returns an empty object. Phase 1 has nothing to say back to Chat; a synchronous card or
	text response is Phase 2's, and the primary inbound path is Pub/Sub in any case.
	"""
	result = verify_chat_request()
	if not result.ok:
		return _unauthorized(result.reason, result.detail)

	event_type = _peek_event_type()
	if event_type is None:
		# A verified caller sending a body that is not a JSON object is not a well-formed
		# Chat request. Phase 1 treats it as unauthenticated rather than as a client error
		# (Appendix B §3.6) — and, crucially, reaches no table on the way out.
		return _unauthorized(REJECT_MALFORMED_BODY)

	frappe.logger("chat.gchat.webhook", allow_site=True).info(
		f"Chat webhook accepted: event_type={event_type or 'unspecified'}"
	)

	# Phase 5's dispatch. It acknowledges and enqueues and NEVER answers inline: Google's
	# interaction deadline is a hard 30 seconds, and a handler that does retrieval plus a
	# model turn inside it works in development and times out under load — where the symptom
	# is not an error but a mention that is silently never answered.
	#
	# Wrapped, because this endpoint's contract is "verify, then 200". A dispatch failure must
	# not turn a verified request into a 500 that Google will retry against a system already
	# having a bad day.
	#
	# Imported inside the function so this module stays importable — and its verifier stays
	# testable — without pulling in the retrieval stack.
	try:
		payload = _verified_payload()
		if payload is not None:
			from erpnext_enhancements.chat.invoke import dispatch

			dispatch.dispatch_chat_interaction(payload)
	except Exception:
		try:
			frappe.log_error(
				"A verified Chat interaction event was accepted but could not be dispatched.",
				"Chat Triton",
			)
		except Exception:
			pass

	frappe.local.response.http_status_code = 200
	return {}
