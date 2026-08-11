# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Keyless Google authentication for the chat relay — **no private key, ever**.

Two identities, because ADR 0009 §E.2 chose a hybrid and the two halves are not
interchangeable:

* **The human relay** uses service-account **domain-wide delegation** (user
  authentication). It is the only mode in which a relayed message renders as the
  real person, and the only mode that can upload an attachment. Locked decision:
  human attribution wins, so ``NOTIFICATION_TYPE_SILENT`` — which *requires app
  authentication* — is **not** used on the relay, and Google Chat's own
  notification therefore fires for native-client users. That is expected and
  documented, not a defect.
* **Triton's replies** use the registered Chat app on ``chat.bot`` (app
  authentication), which is bot-badged and can send ``cardsV2``. Phase 1 only
  *mints* that token; nothing calls it yet.

**Why this module is ~150 lines instead of three.** Google's quickstart tells
you to download a JSON service-account key. On a GCP VM you do not need one and
must not create one: ADR 0009 §E.4.1 quotes Google's own guidance —
*"avoid service account keys and use the signJwt API instead"* — and the entire
trust relationship reduces to one IAM binding
(``roles/iam.serviceAccountTokenCreator``, VM service account → delegation
service account) whose revocation is a single, audited
``remove-iam-policy-binding``. The cost of that is that the DWD assertion is
hand-rolled, because ``google.auth.impersonated_credentials`` has **no**
``subject`` parameter and **no** ``with_subject`` method (verified twice against
the library reference; ADR 0009 §E.4.1 item 6). The library gives impersonation
but not delegation, so the ``sub`` claim has nowhere supported to live.

The four steps, each mapped to a function below:

1. Application Default Credentials on the VM — the metadata server, i.e. the
   VM-attached service account (:func:`_adc_access_token`).
2. Build the assertion claim set (:func:`build_assertion_claims`) — **pure**,
   and the only part with automated regression cover.
3. ``projects.serviceAccounts.signJwt`` on ``projects/-/serviceAccounts/<sa>``
   with the serialised claim set as ``payload`` (:func:`_sign_jwt`). IAM
   Credentials builds the JWT header, chooses ``alg``/``kid`` and signs with key
   material we never see — **we write no cryptography on the signing path**.
4. ``POST https://oauth2.googleapis.com/token`` with
   ``grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`` and the signed JWT
   as ``assertion`` (:func:`_exchange_assertion`).

**No SDK, by doctrine and by force.** ``signJwt`` is a plain ``requests`` POST
rather than ``google.cloud.iam_credentials_v1.IAMCredentialsClient.sign_jwt``:
``google-cloud-iam`` is a **separate distribution** from ``google-auth``, the
production import probe never established that it is installed, and the deploy
pipeline has **no pip install step at all**. That is ADR 0004 exactly — the same
reason ``stripe_payments`` and the QuickBooks client hand-roll their HTTP. Do
not "fix" this by adding an SDK.

  VERIFY: whether ``google.auth.impersonated_credentials`` imports on the prod
  bench. Settle by adding it to the ``frappe.get_module`` probe used for the
  earlier dependency survey. **Blocks nothing** — it would only shorten
  :func:`_adc_access_token`, and the metadata fallback below covers its absence.

  VERIFY: whether ``google.cloud.iam_credentials_v1`` imports on the prod bench.
  Same probe. **Blocks nothing, and must change nothing**: even if it is
  present, ADR 0009 §E.4.1 says do not write ``IAMCredentialsClient.sign_jwt``
  in any phase.

  VERIFY (Google-side, unexecuted): the OAuth scope the VM-attached service
  account must present to call ``signJwt``. The reference page's
  Authorization-scopes block was truncated in both fetches, so ``cloud-platform``
  is *assumed*, not asserted. It matters because VM access scopes are a VM
  property whose change requires a stop/start. Settle from the VM with:
  ``curl -s -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/scopes``

  VERIFY (Google-side, unexecuted): the per-project quota on
  ``iamcredentials.googleapis.com`` ``signJwt``, read from the Quotas page for
  the project owning the VM. It decides only whether a cold cache after a deploy
  can mint ~20 tokens in a burst without 429ing.

**Nothing in this module logs, returns or stores a token, a signed assertion or
a claim payload.** Every raise on a path that has token material in its frame
uses ``raise … from None``: a bare re-raise out of a background job publishes
frame locals into the Error Log, which is how a secret gets written to the
database by accident.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

import frappe
import requests

from erpnext_enhancements.chat.gchat.ids import scope_fingerprint

# --- Endpoints -------------------------------------------------------------
# Chat's own host (chat.googleapis.com) deliberately does NOT appear here: a
# guardrail test asserts it appears in client.py and nowhere else.

#: *"Always ``https://oauth2.googleapis.com/token``"* — the required ``aud`` of
#: the assertion, and the endpoint it is exchanged at (ADR 0009 §E.4.1 item 5).
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

#: ``POST https://iamcredentials.googleapis.com/v1/{name=projects/*/serviceAccounts/*}:signJwt``.
#: The literal hyphen wildcard in ``projects/-/`` is Google's own format string,
#: not a shortcut of ours.
IAM_CREDENTIALS_BASE_URL = "https://iamcredentials.googleapis.com/v1"

#: Link-local metadata server. Reachable only from inside the VM.
METADATA_TOKEN_URL = (
	"http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)

JWT_BEARER_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"

# --- Tuning ----------------------------------------------------------------

#: Google's documented ceiling on an assertion's lifetime is not published; 600
#: seconds is the conventional maximum and is what ADR 0009 §E.4 specifies. An
#: over-long ``exp`` fails as a **400 that reads like a configuration problem**,
#: which is exactly the kind of failure that costs an afternoon — so the builder
#: refuses to construct one rather than letting Google refuse it.
MAX_ASSERTION_LIFETIME_SECONDS = 600

#: Cache the access token for its lifetime minus this margin, so an in-flight
#: request cannot be holding a token that expires mid-call.
TOKEN_CACHE_SAFETY_MARGIN_SECONDS = 120

#: Floor on the cached TTL, so a pathologically short ``expires_in`` cannot
#: produce a negative TTL and a cache that never holds anything.
MIN_TOKEN_CACHE_SECONDS = 60

DEFAULT_HTTP_TIMEOUT_SECONDS = 30

# --- Scopes ----------------------------------------------------------------
# The DWD scope list is frozen in ONE super-admin session (Security → Access and
# data control → API controls → Manage Domain Wide Delegation), and changes take
# up to 24 hours to propagate. Changing this tuple is therefore a Workspace
# admin change, not a code change; the two must be edited together or every call
# fails with `401 unauthorized_client`, which looks identical to a wrong client
# id.

SCOPE_CHAT_MESSAGES = "https://www.googleapis.com/auth/chat.messages"
SCOPE_CHAT_MESSAGES_CREATE = "https://www.googleapis.com/auth/chat.messages.create"
SCOPE_CHAT_MESSAGES_READONLY = "https://www.googleapis.com/auth/chat.messages.readonly"
SCOPE_CHAT_SPACES = "https://www.googleapis.com/auth/chat.spaces"
SCOPE_CHAT_MEMBERSHIPS = "https://www.googleapis.com/auth/chat.memberships"
SCOPE_CHAT_IMPORT = "https://www.googleapis.com/auth/chat.import"
SCOPE_CHAT_DELETE = "https://www.googleapis.com/auth/chat.delete"
SCOPE_CHAT_BOT = "https://www.googleapis.com/auth/chat.bot"

#: The relay's delegated scopes.
#:
#: VERIFY (Google-side, unexecuted): this tuple must equal, byte for byte, the
#: scope list authorised against the delegation service account's **numeric
#: OAuth client id** in the Admin console, confirmed via *View details* — the
#: console silently accepts a paste, and that round-trip is the only proof of
#: what was stored. ADR 0009 §E.5.2 step 7. Note also §7298 of the ADR:
#: ``chat.delete`` gates deleting a **space**, not a message; message deletion is
#: covered by ``chat.messages``. Do not add a scope here speculatively — Google's
#: own DWD guidance is *"do not give access to non-essential OAuth scopes"*.
RELAY_SCOPES: tuple[str, ...] = (
	SCOPE_CHAT_MESSAGES,
	SCOPE_CHAT_SPACES,
	SCOPE_CHAT_MEMBERSHIPS,
)

#: The Chat app's own scope. Requires **no** administrator approval, which is the
#: whole reason Triton's leg is cheap (ADR 0009 §E.1). Any ``chat.app.*`` scope
#: would require a super-admin action and a Marketplace admin-install; none is
#: used.
APP_SCOPES: tuple[str, ...] = (SCOPE_CHAT_BOT,)


class ChatAuthError(frappe.ValidationError):
	"""A Google credential could not be obtained.

	Mirrors ``StripeError``: a ``ValidationError`` subclass so callers can catch
	one thing, and so the message surfaces in the desk instead of as a 500. The
	*message* is always a diagnosis — never a body dump, because the bodies on
	this path carry assertions and tokens.
	"""


# ---------------------------------------------------------------------------
# Pure functions — the tested tier
# ---------------------------------------------------------------------------


def build_assertion_claims(
	issuer: str,
	subject: str,
	scopes: Sequence[str],
	now_epoch: int,
	lifetime_seconds: int = MAX_ASSERTION_LIFETIME_SECONDS,
	audience: str = OAUTH_TOKEN_URL,
) -> dict[str, Any]:
	"""Build the JWT claim set that IAM Credentials will sign. **Pure.**

	Takes ``now_epoch`` as an argument and never reads the clock, because a
	function that reads the clock cannot be asserted against a fixed expectation
	— and this claim set is a hand-rolled security primitive whose only
	automated defence is that assertion.

	The six claims are ADR 0009 §E.4.1 item 5, each confirmed against Google's
	service-account OAuth reference:

	* ``iss`` — the **delegation service account's email**. Not the VM service
	  account, and not the user.
	* ``sub`` — the email of the user being impersonated. **Omitted entirely**
	  when ``subject`` is empty, which is what turns this from a delegated
	  (3LO-equivalent) assertion into the plain two-legged service-account
	  assertion the Chat app uses. An empty-string ``sub`` is not the same thing
	  as an absent one and Google rejects it.
	* ``scope`` — space-separated, in the caller's order.
	* ``aud`` — always the token endpoint.
	* ``iat`` / ``exp``.

	``lifetime_seconds`` is capped at :data:`MAX_ASSERTION_LIFETIME_SECONDS` by
	raising rather than by clamping. Clamping would hide a caller's mistaken
	belief about how long its credential lives; raising surfaces it here, in a
	unit-testable place, instead of as an opaque 400 from Google.
	"""
	if not issuer:
		raise ChatAuthError("Assertion issuer (the delegation service account email) is required")
	if not scopes:
		raise ChatAuthError("An assertion with no scopes would be granted nothing")
	if lifetime_seconds <= 0:
		raise ChatAuthError("Assertion lifetime must be positive")
	if lifetime_seconds > MAX_ASSERTION_LIFETIME_SECONDS:
		raise ChatAuthError(
			f"Assertion lifetime {lifetime_seconds}s exceeds the {MAX_ASSERTION_LIFETIME_SECONDS}s "
			"maximum; Google rejects an over-long exp with a 400 that reads like a config error"
		)

	claims: dict[str, Any] = {
		"iss": issuer,
		"scope": " ".join(scopes),
		"aud": audience,
		"iat": int(now_epoch),
		"exp": int(now_epoch) + int(lifetime_seconds),
	}
	if subject:
		claims["sub"] = subject
	return claims


def serialise_claims(claims: dict[str, Any]) -> str:
	"""Serialise a claim set for the ``payload`` field of ``signJwt``. **Pure.**

	*"Must be a serialized JSON object that contains a JWT Claims Set."* — so
	this is the whole of our contribution to the signing step; there is no
	base64url encoding and no header, because IAM Credentials builds both.

	Sorted keys and no incidental whitespace, so the same claim set always
	serialises to the same bytes. Determinism here buys a byte-exact test and
	nothing else — Google does not care about key order — but a test that cannot
	compare bytes is a test that compares nothing.
	"""
	return json.dumps(claims, sort_keys=True, separators=(",", ":"))


def describe_token_error(status: int, body: dict[str, Any] | None) -> str:
	"""Turn an OAuth token-endpoint failure into the diagnosis it actually is. **Pure.**

	The two documented failure signals on this path both present as generic
	4xx, and both have a specific, non-obvious cause. Naming them here is worth
	more than the code costs: without this, the first symptom of a 24-hour DWD
	propagation delay is an engineer re-reading their claim builder.

	Never includes the response body verbatim — the request that produced it
	carried a signed assertion.
	"""
	error = (body or {}).get("error") or ""
	hint = ""
	if error == "unauthorized_client":
		hint = (
			" — the delegation service account's numeric OAuth client id, or one of the scopes, is not "
			"authorised in Admin console → Security → Access and data control → API controls → Manage "
			"Domain Wide Delegation. Changes there take up to 24 hours; allow the full window before "
			"concluding the scope strings are wrong."
		)
	elif error == "invalid_grant":
		hint = (
			" — the impersonated user (sub) is not a valid Workspace account in the delegated domain, "
			"or the VM clock has drifted far enough that iat/exp are outside Google's tolerance."
		)
	elif error == "invalid_scope":
		hint = " — a scope string is malformed or not authorised for delegation."
	return f"Google token exchange failed ({status}: {error or 'no error code'}){hint}"


def describe_sign_jwt_error(status: int, body: dict[str, Any] | None) -> str:
	"""Diagnose an IAM Credentials ``signJwt`` failure. **Pure.**

	``403`` here means one thing overwhelmingly often, and it is not the thing
	the message says.
	"""
	message = ((body or {}).get("error") or {}).get("message") or ""
	hint = ""
	if status == 403:
		hint = (
			" — the VM-attached service account is missing roles/iam.serviceAccountTokenCreator ON the "
			"delegation service account (the binding is on the target SA's own allow policy, not on the "
			"project), or the VM's access scopes do not include cloud-platform."
		)
	elif status == 404:
		hint = " — no such service account; check delegation_service_account on Chat Settings."
	elif status == 401:
		hint = " — the VM has no usable Application Default Credentials."
	return f"IAM Credentials signJwt failed ({status}{': ' + message if message else ''}){hint}"


# ---------------------------------------------------------------------------
# Configuration — identifiers only, never a secret
# ---------------------------------------------------------------------------


def get_auth_config() -> dict[str, Any]:
	"""Read the identifiers this module needs from ``Chat Settings``.

	``Chat Settings`` holds **identifiers only** — service-account *emails*,
	project ids, topic names, the audience URL. There is no ``Password`` field
	on it and there must never be one: under the keyless design there is nothing
	to store. If a future change wants to add one, the design has gone wrong.

	Every read is ``getattr(…, None) or default``. That is the house convention
	and it is load-bearing here for a specific reason: this module is imported
	during ``bench migrate``, when ``Chat Settings`` may exist with an older
	field set, and an ``AttributeError`` at that moment fails the migration
	rather than the feature.
	"""
	settings = frappe.get_cached_doc("Chat Settings")
	return {
		"enabled": bool(getattr(settings, "enabled", 0)),
		"delegation_service_account": getattr(settings, "delegation_service_account", None) or "",
		"chat_app_service_account": getattr(settings, "chat_app_service_account", None) or "",
		"http_timeout_seconds": int(
			getattr(settings, "http_timeout_seconds", None) or DEFAULT_HTTP_TIMEOUT_SECONDS
		),
	}


def _require_enabled(config: dict[str, Any]) -> None:
	"""Refuse to mint a token while the master switch is off.

	``enabled`` is a rollout control, and the rule attached to it is absolute:
	when it is off, **no Google call is made from any path**. Minting a token is
	a Google call, so the switch is enforced at the bottom of the stack as well
	as at the top of each job — a defence that survives someone adding a new
	caller and forgetting the check.
	"""
	if not config["enabled"]:
		raise ChatAuthError("Chat is disabled in Chat Settings; no Google credential will be minted.")


# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------
# VERIFY: the exact frappe.cache() method names on Frappe v16. Not read from
# framework source in this session — but `get_value` / `set_value(expires_in_sec=)`
# / `delete_value` are used by ~10 modules in this app against the running v16
# site (api/finance_calendar.py, quickbooks_online/core/api.py, and
# triton_chat.py:126-154, which caches a bearer token in exactly this shape), so
# the risk is bounded. They are wrapped here anyway so that a v16 rename is a
# three-line fix in one place rather than a grep.
#
# Cache instance choice, from ADR 0009 §E.4.2: prefer the *cache* Redis, because
# the deploy FLUSHDBs both instances and a flushed token cache is a latency
# event, not a correctness one. `frappe.cache()` is that instance.


def _cache_get(key: str) -> str | None:
	value = frappe.cache().get_value(key)
	return value if isinstance(value, str) else None


def _cache_set(key: str, value: str, ttl_seconds: int) -> None:
	frappe.cache().set_value(key, value, expires_in_sec=ttl_seconds)


def _cache_delete(key: str) -> None:
	frappe.cache().delete_value(key)


def _token_cache_key(kind: str, subject: str, scopes: Sequence[str]) -> str:
	"""Key on ``(subject, sorted(scopes))`` — ADR 0009 §E.4.2 constraint 1.

	Per-impersonated-user, not global: at the measured roster that is ~20 live
	access tokens, and a single shared entry would hand one person's delegated
	credential to everybody. ``frappe.cache()`` is already namespaced per site,
	so the site is not repeated in the key.
	"""
	return f"chat_gchat_token::{kind}::{subject or '-'}::{scope_fingerprint(scopes)}"


def _cache_ttl(expires_in: int) -> int:
	"""Token lifetime minus the safety margin, floored."""
	return max(int(expires_in) - TOKEN_CACHE_SAFETY_MARGIN_SECONDS, MIN_TOKEN_CACHE_SECONDS)


def invalidate_token_cache(subject: str = "", scopes: Sequence[str] = RELAY_SCOPES) -> None:
	"""Drop one cached access token — for a 401-retry, and for the smoke test.

	Only ever drops a *token*. No permission or identity decision is cached
	anywhere in this module, so there is nothing else here that could go stale
	in a way that grants access.
	"""
	_cache_delete(_token_cache_key("dwd" if subject else "app", subject, scopes))


# ---------------------------------------------------------------------------
# The four steps
# ---------------------------------------------------------------------------


def get_vm_access_token(timeout: int = 30) -> str:
	"""A token for the **VM-attached** service account, for Google APIs that are not Chat.

	Phase 5's embedding calls need one: Vertex AI is a different API on a different host with
	a different scope, and it is *not* a Chat call, so neither the delegated path (which
	impersonates a coworker) nor the app path (``chat.bot``) is the right credential. The VM's
	own identity is.

	A thin public wrapper over the same Application Default Credentials step the delegated
	flow already uses for step 1, exposed rather than reached into — a caller importing a
	private helper is a caller that breaks silently when the private helper is refactored.

	The scope comes from the instance's own configuration rather than from this call, which is
	the keyless design's whole point: there is no key, so there is nothing here to scope.
	Confirm the instance carries ``cloud-platform`` before expecting Vertex AI to answer.

	Never log the return value.
	"""
	return _adc_access_token(timeout)


def _adc_access_token(timeout: int) -> str:
	"""Step 1 — a token for the **VM-attached** service account.

	Two paths, in this order:

	1. ``google.auth.default()``, if it imports. It is the documented route, it
	   caches in-process, and it also works on a developer machine running
	   ``gcloud auth application-default login``. The import is guarded because
	   nothing established that ``google.auth`` is importable on the production
	   bench — only ``google.oauth2.service_account`` and
	   ``googleapiclient.discovery`` were probed.
	2. A raw GET against the metadata server. Zero dependencies, link-local, and
	   exactly what the production topology (a single GCE VM per environment)
	   provides.

	The result is deliberately **not** cached in Redis. It is a
	``cloud-platform``-scoped credential for the VM itself — a strictly more
	powerful thing than the Chat tokens this module hands out — and the
	metadata-server round trip is link-local and cheap, so caching would trade
	real blast radius for an invisible latency win.
	"""
	try:
		# Imported inside the function, not at module scope: presence on the
		# production bench is unproven, and an ImportError at import time would
		# break `bench migrate` rather than one code path.
		import google.auth
		import google.auth.transport.requests

		credentials, _project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
		credentials.refresh(google.auth.transport.requests.Request())
		if credentials.token:
			return str(credentials.token)
	except ImportError:
		pass
	except Exception:
		# Fall through to the metadata server rather than failing here: on the
		# VM the metadata path is the one that actually has to work, and a
		# google-auth misconfiguration must not take it down with it. The
		# exception is swallowed rather than logged because its frame may hold
		# credential material.
		pass

	try:
		response = requests.get(
			METADATA_TOKEN_URL,
			headers={"Metadata-Flavor": "Google"},
			timeout=timeout,
		)
	except requests.RequestException as exc:
		raise ChatAuthError(
			f"Could not reach the GCP metadata server for Application Default Credentials: {exc}"
		) from None

	if response.status_code != 200:
		raise ChatAuthError(
			f"Metadata server returned {response.status_code} for the default service-account token; "
			"the VM may have no attached service account."
		) from None

	token = (response.json() or {}).get("access_token")
	if not token:
		raise ChatAuthError("Metadata server returned no access_token") from None
	return str(token)


def _sign_jwt(service_account_email: str, payload: str, adc_token: str, timeout: int) -> str:
	"""Step 3 — have IAM Credentials sign the claim set.

	``projects/-/serviceAccounts/<email>`` uses Google's own hyphen wildcard for
	the project, so this module never needs to know which project owns the
	service account. The response contains ``signedJwt``; it is returned and
	never logged.
	"""
	url = f"{IAM_CREDENTIALS_BASE_URL}/projects/-/serviceAccounts/{service_account_email}:signJwt"
	try:
		response = requests.post(
			url,
			headers={"Authorization": f"Bearer {adc_token}"},
			json={"payload": payload},
			timeout=timeout,
		)
	except requests.RequestException as exc:
		raise ChatAuthError(f"IAM Credentials signJwt request failed: {exc}") from None

	body = _safe_json(response)
	if response.status_code != 200:
		raise ChatAuthError(describe_sign_jwt_error(response.status_code, body)) from None

	signed = (body or {}).get("signedJwt")
	if not signed:
		raise ChatAuthError("IAM Credentials signJwt returned no signedJwt") from None
	return str(signed)


def _exchange_assertion(assertion: str, timeout: int) -> tuple[str, int]:
	"""Step 4 — trade the signed assertion for an access token.

	Returns ``(access_token, expires_in)``. Form-encoded, per the JWT-bearer
	grant.
	"""
	try:
		response = requests.post(
			OAUTH_TOKEN_URL,
			data={"grant_type": JWT_BEARER_GRANT_TYPE, "assertion": assertion},
			timeout=timeout,
		)
	except requests.RequestException as exc:
		raise ChatAuthError(f"Google token exchange request failed: {exc}") from None

	body = _safe_json(response)
	if response.status_code != 200:
		raise ChatAuthError(describe_token_error(response.status_code, body)) from None

	token = (body or {}).get("access_token")
	if not token:
		raise ChatAuthError("Google token exchange returned no access_token") from None
	return str(token), int((body or {}).get("expires_in") or 3600)


def _safe_json(response: requests.Response) -> dict[str, Any] | None:
	"""Parse a JSON body, or return ``None``. Never raises, never logs the body."""
	try:
		parsed = response.json()
	except ValueError:
		return None
	return parsed if isinstance(parsed, dict) else None


def _mint(
	*,
	kind: str,
	issuer: str,
	subject: str,
	scopes: Sequence[str],
	timeout: int,
	force_refresh: bool,
) -> str:
	"""Run steps 1–4 behind the per-subject cache. Shared by both identities."""
	cache_key = _token_cache_key(kind, subject, scopes)
	if not force_refresh:
		cached = _cache_get(cache_key)
		if cached:
			return cached

	# `time.time()` and NOT `frappe.utils.now_datetime()`. The latter returns a
	# naive datetime in the *site's* timezone; calling `.timestamp()` on it
	# reinterprets those wall-clock digits in the *server's* timezone, which
	# silently shifts iat/exp by the offset between the two. That exact confusion
	# has already produced one always-fails bug in this app. Google compares
	# these claims against real UTC, so we read real UTC.
	now_epoch = int(time.time())
	claims = build_assertion_claims(issuer, subject, scopes, now_epoch)
	adc_token = _adc_access_token(timeout)
	signed = _sign_jwt(issuer, serialise_claims(claims), adc_token, timeout)
	token, expires_in = _exchange_assertion(signed, timeout)

	_cache_set(cache_key, token, _cache_ttl(expires_in))
	return token


def get_delegated_token(
	subject: str,
	scopes: Sequence[str] = RELAY_SCOPES,
	force_refresh: bool = False,
) -> str:
	"""Mint (or reuse) an access token that acts **as the human** ``subject``.

	``subject`` is the Workspace email of the person whose message is being
	relayed — not the ERPNext ``User.name``, which is usually but not always the
	same string. Callers resolve that before calling; this module deliberately
	does not, because guessing an identity is the one mistake here with a
	privacy consequence.

	This is the credential behind human attribution. A message posted with it
	renders as the real person (with a *"via <app name>"* attribution), which is
	the entire reason DWD was chosen over app auth.

	Never log the return value.
	"""
	if not subject:
		raise ChatAuthError("A delegated token requires the email of the user to impersonate")

	config = get_auth_config()
	_require_enabled(config)
	issuer = config["delegation_service_account"]
	if not issuer:
		raise ChatAuthError("Chat Settings has no delegation service account configured.")

	return _mint(
		kind="dwd",
		issuer=issuer,
		subject=subject,
		scopes=scopes,
		timeout=config["http_timeout_seconds"],
		force_refresh=force_refresh,
	)


def get_app_token(
	scopes: Sequence[str] = APP_SCOPES,
	force_refresh: bool = False,
) -> str:
	"""Mint (or reuse) an access token for the **Chat app identity** (``chat.bot``).

	**Phase 1 only mints this; nothing calls it yet.** It exists now because the
	ADR's hybrid uses it for Triton's replies (bot-badged, ``cardsV2``-capable),
	and because writing it alongside the delegated path is how the two stay one
	code path instead of two divergent ones.

	Mechanically it is the same assertion flow with **no** ``sub`` claim — the
	plain two-legged service-account grant. That keeps the keyless property:
	still no key file, still signed by IAM Credentials.

	  VERIFY (Google-side, unexecuted): what credential material a
	  Cloud-console-configured Chat app actually holds for ``chat.bot`` calls,
	  and whether this self-signed-assertion route is the supported one for it,
	  or whether IAM Credentials ``generateAccessToken`` on the same service
	  account is preferred. ADR 0009 §E.1 carries this as the one unresolved
	  cell in the auth trade-off table. Blocks Triton's leg (Phase 5), not
	  Phase 1 — but settle it before anything relies on the token.

	Never log the return value.
	"""
	config = get_auth_config()
	_require_enabled(config)
	issuer = config["chat_app_service_account"]
	if not issuer:
		raise ChatAuthError("Chat Settings has no Chat app service account configured.")

	return _mint(
		kind="app",
		issuer=issuer,
		subject="",
		scopes=scopes,
		timeout=config["http_timeout_seconds"],
		force_refresh=force_refresh,
	)
