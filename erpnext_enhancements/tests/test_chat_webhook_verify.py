"""Bench-free JWT tests for the inbound Google Chat webhook — the six negative cases.

**Shape P: plain pytest functions.** It gets its own ``python -m pytest`` step in
``ci.yml``. ``python -m unittest`` cannot collect a file of plain functions — it collects
nothing and reports success — and this repo has already lost a suite that way for weeks.
Appending this file to a unittest module list would run **zero** of the assertions below on
the most exposed function in the app.

``chat/gchat/webhook.handle`` is ``allow_guest=True``: a world-reachable POST that Phase 2
wires to the message store. The JWT is the only thing between it and an open relay, so this
suite is the only pre-production defence that exists on it (ADR §G.6, Appendix B §3.6, §9-E).

The six negative cases the ADR names, each with a test whose name says which one it is:

  1. no ``Authorization`` header at all;
  2. a malformed token;
  3. an expired token;
  4. **a valid Google-signed token whose ``email``/``iss`` claim is not**
     ``chat@system.gserviceaccount.com``;
  5. **a valid token minted for the wrong audience**;
  6. a valid token carrying a malformed body → 401, **with no database write**.

Cases 4 and 5 are the two that matter most, and the reason they are named rather than folded
into "bad token": they are precisely the cases a *generic* ID-token verification passes.
``google.oauth2.id_token.verify_oauth2_token()`` checks the signature, ``exp`` and ``aud`` —
it does **not** check who signed the thing. Without the explicit ``email`` check every GCP
project on earth can mint a token our endpoint accepts. Case 5 is its mirror: a token that
is genuinely Google's and genuinely Chat's, minted for somebody else's endpoint.

**Ordering is asserted, not assumed.** Every test drives ``handle()`` and then inspects
``STATE.trace`` — an ordered log written by the stub as the handler touches things. The
contract the ADR states is *verify before parsing the body and before any DB access*, and
the trace is what proves it: on all six failure paths the trace ends at ``verify`` and the
body was never read; ``db`` never appears at all, on any path, success included. A separate
"was the DB touched" boolean would let a body read slip in front of verification unnoticed.

Two deliberate non-assertions, stated so nobody adds them later thinking they were missed:

  * Frappe populates ``form_dict`` from the request before any app code runs, so "before body
    parsing" can only ever mean *before ours*. No app-level test can assert otherwise.
  * ``get_expected_audience()`` reads the ``Chat Settings`` Single from the **document cache**
    before verification, and that is allowed: it is not a query against attacker-controlled
    data and the request cannot influence it. The trace shows it as ``settings`` so the
    exception is visible rather than hidden.

No network, no crypto, no bench. The crypto boundary is one injected callable
(``webhook._default_oidc_verifier``, and ``cert_fetcher`` for project-number mode), which is
exactly the seam ``webhook.py`` was written to expose. Nothing here contains real employee
content, and every fake credential reads ``FAKE-DO-NOT-USE`` so
``scripts/check_no_committed_secrets.py`` can tell it apart from a leak.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
import types
from collections.abc import Mapping
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Bench guard, then the frappe stub
# ---------------------------------------------------------------------------


def _real_frappe_is_installed() -> bool:
	"""Is there an actual ``frappe`` package importable here, as opposed to a stub?

	``find_spec`` answers from the import system, so a bare ``types.ModuleType`` another
	suite left in ``sys.modules`` has no ``__spec__`` and reads as absent — which is the
	answer we want.
	"""
	try:
		spec = importlib.util.find_spec("frappe")
	except (ImportError, ValueError):
		return False
	return bool(spec and spec.origin)


if _real_frappe_is_installed():  # pragma: no cover - only true on a bench
	# Skip rather than stub. The stub below replaces ``frappe.db`` with a tripwire that
	# raises on any attribute access; installing that over a real frappe would take the
	# rest of a `bench run-tests` run with it, including
	# tests/test_chat_permissions_bench.py, which is the suite this one cannot replace.
	pytest.skip(
		"bench-free suite: a real frappe is installed, and stubbing over it would break "
		"the rest of the bench run",
		allow_module_level=True,
	)


class _DatabaseTripwire:
	"""Stands in for ``frappe.db``. **Any** attribute access is recorded and then refused.

	The claim under test is "no database access on a rejected request". A mock that merely
	returns ``None`` would let a stray read pass silently and keep the suite green; this
	records the attribute name into the shared trace *and* raises, so a DB touch on a
	rejection path fails loudly and names the accessor it came from.
	"""

	def __init__(self, state: _State) -> None:
		object.__setattr__(self, "_state", state)

	def __getattr__(self, name: str) -> Any:
		self._state.trace.append(f"db:{name}")

		def _refuse(*args: Any, **kwargs: Any) -> Any:
			raise AssertionError(
				f"the Chat webhook reached frappe.db.{name}() — verification must complete "
				"before any database access (ADR §G.6)"
			)

		return _refuse


class _FakeRequest:
	"""The werkzeug request surface ``_peek_event_type`` uses: ``get_data()`` and nothing else."""

	def __init__(self, state: _State) -> None:
		self._state = state

	def get_data(self) -> bytes:
		self._state.trace.append("body")
		return self._state.body


class _FakeLogger:
	def __init__(self, state: _State) -> None:
		self._state = state

	def warning(self, message: str) -> None:
		self._state.warnings.append(message)

	def info(self, message: str) -> None:
		self._state.infos.append(message)


class _FakeCache:
	"""In-memory ``frappe.cache()``. Only the misconfiguration throttle reaches it."""

	def __init__(self) -> None:
		self.store: dict[str, Any] = {}

	def get_value(self, key: str, **kwargs: Any) -> Any:
		return self.store.get(key)

	def set_value(self, key: str, value: Any, **kwargs: Any) -> None:
		self.store[key] = value

	def delete_value(self, key: str, **kwargs: Any) -> None:
		self.store.pop(key, None)


class _State:
	"""Everything the stub records, in one object so a fixture can reset it wholesale."""

	def __init__(self) -> None:
		self.trace: list[str] = []
		self.authorization: str | None = None
		self.audience: str = ENDPOINT_AUDIENCE
		self.body: bytes = b'{"type":"MESSAGE"}'
		self.warnings: list[str] = []
		self.infos: list[str] = []
		self.error_logs: list[dict[str, Any]] = []
		self.cache = _FakeCache()


#: The configured JWT audience. Byte-for-byte comparison is the whole point of the
#: audience check, so the trailing-slash-free form is written once and reused.
ENDPOINT_AUDIENCE = "https://erp.example.invalid/api/method/erpnext_enhancements.chat.gchat.webhook.handle"

#: A different, equally well-formed endpoint URL — the audience an attacker's own Chat
#: deployment would have. Case 5 turns on this being a *valid* URL that is simply not ours.
OTHER_AUDIENCE = "https://someone-else.example.invalid/api/method/chat.handle"

#: The identity Google Chat signs with. Restated rather than imported so a test cannot pass
#: because the constant under test was edited.
CHAT_ISSUER = "chat@system.gserviceaccount.com"

#: A real Google service-account email that is not Chat's. This is case 4: any GCP project
#: can mint an ID token, and this is what its ``email`` claim looks like.
ATTACKER_ISSUER = "attacker@fake-do-not-use-project.iam.gserviceaccount.com"

#: Signature material never reaches the verifier here — it is injected — so the string only
#: has to be non-empty and obviously not a credential.
FAKE_SIGNATURE = "FAKE-DO-NOT-USE-signature"

STATE = _State()


def install_frappe_stub() -> types.ModuleType:
	"""Install the minimal fake ``frappe`` that ``chat.gchat.webhook`` imports.

	The house pattern (``test_fountain_move``, ``test_quickbooks_online``): build the module,
	attach only what the module under test actually uses, and put it in ``sys.modules``
	before the import. ``frappe.rate_limiter.rate_limit`` is separate because that is where
	the decorator really lives — assuming ``frappe.rate_limit`` is an import-time crash, and
	this repo has made that mistake before.
	"""
	frappe = types.ModuleType("frappe")

	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.get_request_header = lambda name, default=None: (
		STATE.authorization if str(name).lower() == "authorization" else default
	)

	def _get_cached_doc(doctype: str, *args: Any, **kwargs: Any) -> Any:
		STATE.trace.append("settings")
		return types.SimpleNamespace(interaction_endpoint_url=STATE.audience)

	frappe.get_cached_doc = _get_cached_doc
	frappe.logger = lambda *a, **k: _FakeLogger(STATE)
	frappe.cache = lambda: STATE.cache
	frappe.log_error = lambda **kwargs: STATE.error_logs.append(dict(kwargs))
	frappe.db = _DatabaseTripwire(STATE)
	frappe.local = types.SimpleNamespace(response=types.SimpleNamespace(http_status_code=None))
	frappe.request = _FakeRequest(STATE)
	frappe.session = types.SimpleNamespace(user="Guest")

	sys.modules["frappe"] = frappe

	limiter = types.ModuleType("frappe.rate_limiter")
	limiter.rate_limit = lambda *a, **k: (lambda fn: fn)
	sys.modules["frappe.rate_limiter"] = limiter
	frappe.rate_limiter = limiter

	return frappe


FRAPPE = install_frappe_stub()

# Imported here, after the stub: at module scope `webhook` does `import frappe`, and on a
# bench-free runner that import is the stub or it is an ImportError.
from erpnext_enhancements.chat.gchat import webhook  # noqa: I001


# ---------------------------------------------------------------------------
# Fixtures and builders
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_state() -> Any:
	"""Reset the recorder between tests. Autouse: a leaked trace is a false green."""
	global STATE
	STATE = _State()
	FRAPPE.db = _DatabaseTripwire(STATE)
	FRAPPE.request = _FakeRequest(STATE)
	FRAPPE.local.response.http_status_code = None
	return STATE


def _b64url(payload: Mapping[str, Any]) -> str:
	raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
	return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def jwt(header: Mapping[str, Any] | None = None, claims: Mapping[str, Any] | None = None) -> str:
	"""A structurally valid, cryptographically meaningless JWT.

	Endpoint-URL mode never decodes these segments — the injected verifier supplies the
	claims — but ``_looks_like_a_jwt`` requires three non-empty segments, and project-number
	mode really does parse the JOSE header to pick a ``kid``.
	"""
	return ".".join(
		[
			_b64url(header or {"alg": "RS256", "kid": "FAKE-DO-NOT-USE-kid"}),
			_b64url(claims or {"iss": CHAT_ISSUER}),
			FAKE_SIGNATURE,
		]
	)


def chat_claims(**overrides: Any) -> dict[str, Any]:
	"""The claim set a genuine Google Chat ID token carries, before any override.

	``exp`` is far in the future and every test that cares passes an explicit ``now``, so
	nothing here decays into a failure in 2027.
	"""
	claims: dict[str, Any] = {
		"aud": ENDPOINT_AUDIENCE,
		"email": CHAT_ISSUER,
		"email_verified": True,
		"iss": "https://accounts.google.com",
		"exp": 2_000_000_000,
		"iat": 1_999_999_400,
	}
	claims.update(overrides)
	return claims


def verifier_returning(claims: Mapping[str, Any]) -> Any:
	"""A crypto boundary that accepts everything and reports ``claims``.

	This is the *dangerous* verifier — the generic one that checks a signature and stops.
	Cases 4 and 5 use it deliberately: they assert that the checks **around** the crypto
	catch what the crypto does not.
	"""

	def _verify(token: str, audience: str) -> Mapping[str, Any]:
		STATE.trace.append("verify")
		return dict(claims)

	return _verify


def verifier_raising(exc: Exception) -> Any:
	"""A crypto boundary that rejects, the way google-auth actually does."""

	def _verify(token: str, audience: str) -> Mapping[str, Any]:
		STATE.trace.append("verify")
		raise exc

	return _verify


def call_handle(monkeypatch: Any, verifier: Any) -> dict[str, Any]:
	"""Drive the real ``handle()`` with the crypto boundary swapped out.

	``_verify_oidc_id_token`` resolves ``_default_oidc_verifier`` from module globals at call
	time, so patching the attribute is enough — no argument threading, and the handler runs
	exactly the code path production runs.
	"""
	monkeypatch.setattr(webhook, "_default_oidc_verifier", verifier)
	return webhook.handle()


def assert_rejected_before_the_body(response: Mapping[str, Any]) -> None:
	"""401, empty body, and the ordered proof that nothing downstream was reached.

	``trace == ["settings", "verify"]`` is the assertion doing the work: the audience read
	happened, the token was checked, and then the handler stopped. No ``body``, no ``db``.
	"""
	assert response == {}
	assert FRAPPE.local.response.http_status_code == 401
	assert STATE.trace == ["settings", "verify"], STATE.trace
	assert STATE.error_logs == []


def assert_no_database_access() -> None:
	"""No trace entry may name the database, on any path this suite exercises."""
	assert not [step for step in STATE.trace if step.startswith("db:")], STATE.trace
	assert STATE.error_logs == []


# ---------------------------------------------------------------------------
# extract_bearer_token — step 1 of the order, on its own
# ---------------------------------------------------------------------------


def test_bearer_extraction_accepts_only_a_real_bearer_credential() -> None:
	"""The scheme is case-insensitive per RFC 7235; everything else is not a bearer token."""
	token = jwt()
	assert webhook.extract_bearer_token(f"Bearer {token}") == token
	assert webhook.extract_bearer_token(f"bearer {token}") == token
	assert webhook.extract_bearer_token(f"BEARER {token}") == token

	assert webhook.extract_bearer_token(None) == ""
	assert webhook.extract_bearer_token("") == ""
	assert webhook.extract_bearer_token("   ") == ""
	assert webhook.extract_bearer_token(token) == ""  # no scheme
	assert webhook.extract_bearer_token(f"Basic {token}") == ""
	assert webhook.extract_bearer_token("Bearer ") == ""
	# A space inside the credential is not a bearer token whatever else it is — and it is
	# how a header-splitting attempt arrives.
	assert webhook.extract_bearer_token("Bearer part-one part-two") == ""


# ---------------------------------------------------------------------------
# CASE 1 — no Authorization header
# ---------------------------------------------------------------------------


def test_case_1_no_authorization_header_is_401_and_never_reaches_the_body(monkeypatch: Any) -> None:
	"""The unauthenticated ``curl`` from the ADR's checkpoint, as a test.

	Nothing is verified because there is nothing to verify, so the trace is empty: not even
	the settings read happens, because the missing header short-circuits above it.
	"""
	STATE.authorization = None

	response = call_handle(monkeypatch, verifier_returning(chat_claims()))

	assert response == {}
	assert FRAPPE.local.response.http_status_code == 401
	assert STATE.trace == []
	assert_no_database_access()
	assert any(webhook.REJECT_MISSING_BEARER in line for line in STATE.warnings)


def test_case_1_the_rejection_is_logged_to_the_file_log_not_the_error_log(monkeypatch: Any) -> None:
	"""An Error Log insert per rejected request is a DB write an anonymous caller controls.

	Rule 7 of the module docstring: never 200-and-ignore, but never grow a table either.
	The signal lands in the file logger.
	"""
	STATE.authorization = "Basic FAKE-DO-NOT-USE"

	call_handle(monkeypatch, verifier_returning(chat_claims()))

	assert STATE.warnings, "a rejection must still be recorded somewhere"
	assert STATE.error_logs == []


# ---------------------------------------------------------------------------
# CASE 2 — malformed token
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
	("label", "credential"),
	[
		("not a jwt at all", "FAKE-DO-NOT-USE"),
		("two segments", "eyJhbGciOiJSUzI1NiJ9.e30"),
		("four segments", "a.b.c.d"),
		("empty middle segment", "eyJhbGciOiJSUzI1NiJ9..sig"),
		("empty trailing segment", "eyJhbGciOiJSUzI1NiJ9.e30."),
		("only dots", ".."),
	],
)
def test_case_2_a_malformed_token_is_401_before_any_crypto(
	monkeypatch: Any, label: str, credential: str
) -> None:
	"""Structural rejection happens before the verifier is ever called.

	The injected verifier appends ``verify`` to the trace, so its **absence** from the trace
	is the proof that a malformed credential never reaches the crypto — which is what keeps
	a garbage header cheap on a world-reachable endpoint.
	"""
	STATE.authorization = f"Bearer {credential}"

	response = call_handle(monkeypatch, verifier_returning(chat_claims()))

	assert response == {}
	assert FRAPPE.local.response.http_status_code == 401
	assert STATE.trace == ["settings"], STATE.trace
	assert "verify" not in STATE.trace
	assert_no_database_access()


def test_case_2_the_rejected_token_never_appears_in_a_log_line(monkeypatch: Any) -> None:
	"""A rejected credential is attacker-controlled; this repo has been bitten by log leaks.

	Two dot-separated segments, so it is refused structurally — which is the path where the
	temptation to log "the token we could not parse" is strongest.
	"""
	secret_looking = "eyJhbGciOiJSUzI1NiJ9.FAKE-DO-NOT-USE-payload-that-must-not-be-logged"
	STATE.authorization = f"Bearer {secret_looking}"

	call_handle(monkeypatch, verifier_returning(chat_claims()))

	assert STATE.warnings
	for line in STATE.warnings:
		assert secret_looking not in line
		assert "FAKE-DO-NOT-USE-payload" not in line


# ---------------------------------------------------------------------------
# CASE 3 — expired token
# ---------------------------------------------------------------------------


def test_case_3_an_expired_token_rejected_by_google_auth_is_401(monkeypatch: Any) -> None:
	"""The realistic shape: google-auth itself raises on ``exp``."""
	STATE.authorization = f"Bearer {jwt()}"

	response = call_handle(monkeypatch, verifier_raising(ValueError("Token expired, iat=…")))

	assert_rejected_before_the_body(response)
	assert any(webhook.REJECT_SIGNATURE_OR_EXPIRY in line for line in STATE.warnings)


def test_case_3_expiry_is_re_checked_even_when_the_verifier_lets_it_through() -> None:
	"""Defence in depth: a lenient or misconfigured verifier does not get to grant entry.

	``_check_shared_claims`` re-derives expiry from the claims, so a verifier constructed
	with generous clock skew — or swapped for a library that treats ``exp`` as advisory —
	still fails here. ``leeway`` is zero by default, deliberately.
	"""
	result = webhook.verify_chat_bearer_token(
		jwt(),
		ENDPOINT_AUDIENCE,
		oidc_verifier=verifier_returning(chat_claims(exp=1_000)),
		now=2_000,
	)

	assert not result.ok
	assert result.reason == webhook.REJECT_EXPIRED


def test_case_3_a_token_with_no_usable_exp_counts_as_expired() -> None:
	"""Fail closed. An absent or unparseable ``exp`` is not "never expires"."""
	for bad_exp in (None, "", "soon", {}, []):
		result = webhook.verify_chat_bearer_token(
			jwt(),
			ENDPOINT_AUDIENCE,
			oidc_verifier=verifier_returning(chat_claims(exp=bad_exp)),
			now=1_000,
		)
		assert not result.ok, bad_exp
		assert result.reason == webhook.REJECT_EXPIRED


def test_case_3_exp_is_a_boundary_not_a_range() -> None:
	"""One second either side of ``exp``, with ``leeway`` at its default zero."""
	accepted = webhook.verify_chat_bearer_token(
		jwt(),
		ENDPOINT_AUDIENCE,
		oidc_verifier=verifier_returning(chat_claims(exp=1_000)),
		now=1_000,
	)
	assert accepted.ok

	rejected = webhook.verify_chat_bearer_token(
		jwt(),
		ENDPOINT_AUDIENCE,
		oidc_verifier=verifier_returning(chat_claims(exp=1_000)),
		now=1_001,
	)
	assert not rejected.ok
	assert rejected.reason == webhook.REJECT_EXPIRED


# ---------------------------------------------------------------------------
# CASE 4 — a VALID Google token signed by somebody who is not Google Chat
#
# The single most important test in this file. Everything about this token is real: Google
# signed it, it has not expired, and its `aud` is our endpoint URL byte for byte. A generic
# ID-token verification returns it as valid, because that is exactly what
# `verify_oauth2_token` is specified to do. Only the explicit `email` check refuses it.
# Delete `webhook.py`'s "THE line" and every assertion below inverts.
# ---------------------------------------------------------------------------


def test_case_4_a_valid_google_token_with_the_wrong_issuer_email_is_401(monkeypatch: Any) -> None:
	"""Any GCP project can mint this. Without the ``email`` check it is an open relay."""
	STATE.authorization = f"Bearer {jwt()}"
	forged = chat_claims(email=ATTACKER_ISSUER)

	response = call_handle(monkeypatch, verifier_returning(forged))

	assert_rejected_before_the_body(response)
	assert any(webhook.REJECT_ISSUER_IDENTITY in line for line in STATE.warnings)


@pytest.mark.parametrize(
	"email",
	[
		ATTACKER_ISSUER,
		# The near-misses, each of which a substring or prefix check would wave through.
		"chat@system.gserviceaccount.com.fake-do-not-use.example",
		"not-chat@system.gserviceaccount.com",
		"chat@system.gserviceaccount.co",
		"CHAT@SYSTEM.GSERVICEACCOUNT.COM",
		" chat@system.gserviceaccount.com",
		"chat@system.gserviceaccount.com ",
		"",
		None,
	],
)
def test_case_4_the_issuer_check_is_exact_equality(email: Any) -> None:
	"""Equality, not ``in`` and not ``startswith``. Each variant here defeats a looser check.

	The case-differing variant is included on purpose: email addresses are often compared
	case-insensitively, and doing that here would widen the boundary for no benefit —
	Google emits exactly one spelling.
	"""
	result = webhook.verify_chat_bearer_token(
		jwt(),
		ENDPOINT_AUDIENCE,
		oidc_verifier=verifier_returning(chat_claims(email=email)),
		now=1_000,
	)

	assert not result.ok, email
	assert result.reason == webhook.REJECT_ISSUER_IDENTITY


def test_case_4_email_must_be_asserted_as_verified_not_merely_present() -> None:
	"""Google's own instruction, and the claim has been seen as both a bool and a string."""
	for value in (False, "false", "False", None, "", 0):
		result = webhook.verify_chat_bearer_token(
			jwt(),
			ENDPOINT_AUDIENCE,
			oidc_verifier=verifier_returning(chat_claims(email_verified=value)),
			now=1_000,
		)
		assert not result.ok, value
		assert result.reason == webhook.REJECT_EMAIL_NOT_VERIFIED

	for value in (True, "true", "True"):
		result = webhook.verify_chat_bearer_token(
			jwt(),
			ENDPOINT_AUDIENCE,
			oidc_verifier=verifier_returning(chat_claims(email_verified=value)),
			now=1_000,
		)
		assert result.ok, value


def test_case_4_project_number_mode_pins_alg_and_kid_before_any_crypto() -> None:
	"""The gates in *front* of the signature check, in the self-signed-JWT audience mode.

	Renamed from ``..._pins_the_same_identity_on_the_iss_claim``, which is what the old name
	promised and not what the body did: everything here stops before ``google_jwt.decode``,
	so the ``iss`` check downstream of it was never reached. Deleting that check left this
	suite entirely green — the identity assertions now live in the three tests below, which
	stub the decode so the code after it is reachable at all.
	"""
	certs = {"FAKE-DO-NOT-USE-kid": "FAKE-DO-NOT-USE-certificate"}

	# alg pinning — the `alg: none` and HMAC-confusion families die here, before crypto.
	for alg in ("none", "HS256", "hs256", "", None):
		result = webhook.verify_chat_bearer_token(
			jwt(header={"alg": alg, "kid": "FAKE-DO-NOT-USE-kid"}),
			"123456789012",
			cert_fetcher=lambda: certs,
			audience_mode=webhook.AUDIENCE_MODE_PROJECT_NUMBER,
			now=1_000,
		)
		assert not result.ok, alg
		assert result.reason == webhook.REJECT_UNSUPPORTED_ALG

	# A key that is not one of Google's, i.e. the signature the forger actually controls.
	unknown = webhook.verify_chat_bearer_token(
		jwt(header={"alg": "RS256", "kid": "FAKE-DO-NOT-USE-attacker-kid"}),
		"123456789012",
		cert_fetcher=lambda: certs,
		audience_mode=webhook.AUDIENCE_MODE_PROJECT_NUMBER,
		now=1_000,
	)
	assert not unknown.ok
	assert unknown.reason == webhook.REJECT_UNKNOWN_KID

	# A cert fetch that fails is an operator problem, not an attack — and must not be
	# mistaken for a bad signature, or the log sends somebody hunting the wrong thing.
	def _explode() -> Mapping[str, str]:
		raise OSError("googleapis.com unreachable")

	unavailable = webhook.verify_chat_bearer_token(
		jwt(),
		"123456789012",
		cert_fetcher=_explode,
		audience_mode=webhook.AUDIENCE_MODE_PROJECT_NUMBER,
		now=1_000,
	)
	assert not unavailable.ok
	assert unavailable.reason == webhook.REJECT_VERIFIER_UNAVAILABLE


#: The other audience mode's configured value: a Cloud project number. All-digits is how
#: ``get_expected_audience`` tells the two modes apart without carrying a second field.
PROJECT_NUMBER_AUDIENCE = "123456789012"


def install_google_jwt_stub(monkeypatch: Any, claims: Mapping[str, Any]) -> list[dict[str, Any]]:
	"""Replace ``google.auth.jwt`` with a decoder that returns ``claims`` unconditionally.

	Project-number mode reaches its identity checks only *after* ``google_jwt.decode``
	returns, and that decode is real crypto against a real Google certificate. Without this
	stub the whole block behind it is unreachable on a bench-free runner, and the suite
	cannot see it at all: deleting the ``iss`` check from ``webhook.py`` was measured on
	2026-08-08 to leave all 49 tests green. That is the "passes because the pattern is
	wrong" failure, on the one function in the app that is ``allow_guest=True``.

	The two environments fail *differently* and the stub has to defeat both. Locally
	``google-auth`` is installed, so the import succeeds and ``decode`` raises on the fake
	certificate — a ``REJECT_SIGNATURE_OR_EXPIRY``. In CI the package is absent entirely
	(the workflow installs only ``httpx pytest jinja2``), so the *import* raises and the
	result is ``REJECT_VERIFIER_UNAVAILABLE``. Overriding the three ``sys.modules`` entries
	covers both, and ``monkeypatch`` owns the restore so the real package is back in place
	before the next test — this suite shares a process with nothing, but the stub would
	otherwise outlive the test that installed it.

	Returns the list the fake decoder records its kwargs into, so a caller can assert that
	``verify=True`` and the configured audience were really forwarded to the crypto rather
	than quietly dropped by a refactor.
	"""
	calls: list[dict[str, Any]] = []

	def _decode(token: str, **kwargs: Any) -> Mapping[str, Any]:
		calls.append(dict(kwargs))
		return claims

	jwt_module = types.ModuleType("google.auth.jwt")
	jwt_module.decode = _decode
	auth_module = types.ModuleType("google.auth")
	auth_module.jwt = jwt_module
	google_module = types.ModuleType("google")
	google_module.auth = auth_module

	monkeypatch.setitem(sys.modules, "google", google_module)
	monkeypatch.setitem(sys.modules, "google.auth", auth_module)
	monkeypatch.setitem(sys.modules, "google.auth.jwt", jwt_module)
	return calls


def project_number_claims(**overrides: Any) -> dict[str, Any]:
	"""The claim set a genuine self-signed Chat JWT carries. Identity lives in ``iss`` here."""
	claims: dict[str, Any] = {
		"aud": PROJECT_NUMBER_AUDIENCE,
		"iss": CHAT_ISSUER,
		"exp": 2_000_000_000,
		"iat": 1_999_999_400,
	}
	claims.update(overrides)
	return claims


def verify_project_number(
	monkeypatch: Any, claims: Mapping[str, Any]
) -> tuple[Any, list[dict[str, Any]]]:
	"""Drive the real ``_verify_project_number_jwt`` all the way past the signature check."""
	calls = install_google_jwt_stub(monkeypatch, claims)
	result = webhook.verify_chat_bearer_token(
		jwt(claims=claims),
		PROJECT_NUMBER_AUDIENCE,
		cert_fetcher=lambda: {"FAKE-DO-NOT-USE-kid": "FAKE-DO-NOT-USE-certificate"},
		audience_mode=webhook.AUDIENCE_MODE_PROJECT_NUMBER,
		now=1_000,
	)
	return result, calls


def test_case_4_project_number_mode_rejects_a_wrong_iss_after_a_good_signature(
	monkeypatch: Any,
) -> None:
	"""Case 4 in the second audience mode: the signature verifies, the signer is not Chat.

	The exact token a forger produces. ``google_jwt.decode`` is satisfied — they signed it
	properly, for our audience — and ``iss`` is the only claim that says it was not Google
	Chat. No ``email`` claim, so the fallback check on the next line cannot rescue this.
	"""
	result, _ = verify_project_number(monkeypatch, project_number_claims(iss=ATTACKER_ISSUER))

	assert not result.ok
	assert result.reason == webhook.REJECT_ISSUER_IDENTITY


def test_case_4_project_number_mode_rejects_a_wrong_email_beside_a_right_iss(
	monkeypatch: Any,
) -> None:
	"""Both identity claims are checked, not just the first one that happens to be present."""
	result, _ = verify_project_number(
		monkeypatch, project_number_claims(email=ATTACKER_ISSUER)
	)

	assert not result.ok
	assert result.reason == webhook.REJECT_ISSUER_IDENTITY


def test_case_4_project_number_mode_accepts_the_real_chat_identity(monkeypatch: Any) -> None:
	"""The positive control, and the reason the two rejections above mean anything.

	Without it, a ``_verify_project_number_jwt`` that had started refusing *everything*
	would satisfy both of them. It also pins the two arguments that must reach the crypto:
	``verify=True`` (dropping it turns the decode into a base64 parse) and our own audience
	rather than one lifted from the token.
	"""
	result, calls = verify_project_number(monkeypatch, project_number_claims(email=CHAT_ISSUER))

	assert result.ok
	assert calls, "google_jwt.decode was never reached — the stub did not take"
	assert calls[0]["verify"] is True
	assert calls[0]["audience"] == PROJECT_NUMBER_AUDIENCE


# ---------------------------------------------------------------------------
# CASE 5 — a valid token minted for the WRONG audience
#
# The mirror of case 4: genuinely Google's, genuinely Chat's, and minted for somebody else's
# endpoint. A replay of a token captured from another Chat deployment is exactly this shape.
# ---------------------------------------------------------------------------


def test_case_5_a_token_for_another_audience_is_401(monkeypatch: Any) -> None:
	"""google-auth rejects it, and so does the handler — 401 before the body."""
	STATE.authorization = f"Bearer {jwt()}"

	response = call_handle(
		monkeypatch, verifier_raising(ValueError(f"Token has wrong audience {OTHER_AUDIENCE}"))
	)

	assert_rejected_before_the_body(response)


def test_case_5_the_audience_is_re_checked_even_if_the_verifier_did_not(monkeypatch: Any) -> None:
	"""The duplicated check earns its keep here.

	``verify_oauth2_token`` checks ``aud`` — but only against the audience it was handed. If
	that argument is ever wired wrongly, or the verifier is swapped, this is the check still
	standing. It is also the one whose failure is a *configuration* error, which is why it
	names both values in the log rather than reading as a signing mystery.
	"""
	STATE.authorization = f"Bearer {jwt()}"
	wrong = chat_claims(aud=OTHER_AUDIENCE)

	response = call_handle(monkeypatch, verifier_returning(wrong))

	assert_rejected_before_the_body(response)
	assert any(webhook.REJECT_AUDIENCE_MISMATCH in line for line in STATE.warnings)


def test_case_5_the_audience_comparison_is_byte_for_byte_including_a_trailing_slash() -> None:
	"""A trailing slash on one side and not the other is a mismatch. Deliberately.

	This is what the Chat API console does to people: the registered URL and the deployed
	URL differ by one character and every request 401s. Normalising it here would hide a
	real misconfiguration behind an endpoint that "works", which is worse.
	"""
	for near_miss in (
		ENDPOINT_AUDIENCE + "/",
		ENDPOINT_AUDIENCE.replace("https://", "http://"),
		ENDPOINT_AUDIENCE.upper(),
		ENDPOINT_AUDIENCE + "?x=1",
		OTHER_AUDIENCE,
	):
		result = webhook.verify_chat_bearer_token(
			jwt(),
			ENDPOINT_AUDIENCE,
			oidc_verifier=verifier_returning(chat_claims(aud=near_miss)),
			now=1_000,
		)
		assert not result.ok, near_miss
		assert result.reason == webhook.REJECT_AUDIENCE_MISMATCH


def test_case_5_an_unconfigured_audience_refuses_rather_than_accepting_anything() -> None:
	"""Empty configuration must never mean "any audience will do"."""
	result = webhook.verify_chat_bearer_token(jwt(), "", oidc_verifier=verifier_returning(chat_claims()))

	assert not result.ok
	assert result.reason == webhook.REJECT_AUDIENCE_NOT_CONFIGURED


def test_case_5_an_unconfigured_audience_is_the_one_condition_that_alerts(monkeypatch: Any) -> None:
	"""The single exception to "no Error Log row on a rejection", and it is throttled.

	Universal 401s caused by a blank settings field read exactly like a signing problem, so
	somebody has to be told — once an hour, through Redis, so an anonymous caller cannot
	turn "tell somebody" into a flood.
	"""
	STATE.authorization = f"Bearer {jwt()}"
	STATE.audience = ""

	first = call_handle(monkeypatch, verifier_returning(chat_claims()))
	assert first == {}
	assert FRAPPE.local.response.http_status_code == 401
	assert len(STATE.error_logs) == 1
	assert "Chat Inbound Auth" in STATE.error_logs[0]["title"]

	for _ in range(5):
		call_handle(monkeypatch, verifier_returning(chat_claims()))
	assert len(STATE.error_logs) == 1, "the misconfiguration alert must be throttled"


# ---------------------------------------------------------------------------
# CASE 6 — a valid token, a malformed body: 401 WITH NO DATABASE WRITE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
	("label", "body"),
	[
		("not json", b"FAKE-DO-NOT-USE not json"),
		("json but not an object", b'"a string"'),
		("json array", b"[1, 2, 3]"),
		("json null", b"null"),
		("empty body", b""),
		("invalid utf-8", b"\xff\xfe\x00garbage"),
		("truncated json", b'{"type": "MESS'),
	],
)
def test_case_6_a_verified_caller_with_a_malformed_body_is_401_with_no_db_write(
	monkeypatch: Any, label: str, body: bytes
) -> None:
	"""Verification passed; the body did not. Phase 1 treats that as unauthenticated.

	The trace is the point: ``settings → verify → body`` and then nothing. The body was read
	**after** verification, and the handler reached no table on the way out — which is what
	makes a malformed-body flood cost nothing but a log line.
	"""
	STATE.authorization = f"Bearer {jwt()}"
	STATE.body = body

	response = call_handle(monkeypatch, verifier_returning(chat_claims()))

	assert response == {}
	assert FRAPPE.local.response.http_status_code == 401
	assert STATE.trace == ["settings", "verify", "body"], STATE.trace
	assert_no_database_access()
	assert any(webhook.REJECT_MALFORMED_BODY in line for line in STATE.warnings)


def test_case_6_the_malformed_body_is_never_echoed_into_a_log_line(monkeypatch: Any) -> None:
	"""A body from a verified caller is still attacker-controlled if the token leaks."""
	marker = "FAKE-DO-NOT-USE-body-marker"
	STATE.authorization = f"Bearer {jwt()}"
	STATE.body = f'"{marker}"'.encode()

	call_handle(monkeypatch, verifier_returning(chat_claims()))

	assert STATE.warnings
	for line in STATE.warnings:
		assert marker not in line


# ---------------------------------------------------------------------------
# The positive case
# ---------------------------------------------------------------------------


def test_the_positive_case_is_200_with_an_empty_body_and_no_database_write(monkeypatch: Any) -> None:
	"""Phase 1's whole handler: verify, log the event type, return 200 and touch nothing.

	If this ever starts writing a row, Phase 2 has landed early and the ADR's phase boundary
	(ADR §G.4) has been crossed without the dedupe and echo-suppression that go with it.
	"""
	STATE.authorization = f"Bearer {jwt()}"
	STATE.body = b'{"type":"MESSAGE","eventTime":"2026-08-08T00:00:00Z"}'

	response = call_handle(monkeypatch, verifier_returning(chat_claims()))

	assert response == {}
	assert FRAPPE.local.response.http_status_code == 200
	assert STATE.trace == ["settings", "verify", "body"], STATE.trace
	assert_no_database_access()
	assert any("event_type=MESSAGE" in line for line in STATE.infos)
	assert STATE.warnings == []


def test_the_positive_case_verifies_before_it_reads_the_body(monkeypatch: Any) -> None:
	"""Stated as its own test because it is the ordering rule, not an incidental detail."""
	STATE.authorization = f"Bearer {jwt()}"
	STATE.body = b'{"type":"MESSAGE"}'

	call_handle(monkeypatch, verifier_returning(chat_claims()))

	assert STATE.trace.index("verify") < STATE.trace.index("body")


def test_a_verified_request_with_an_unspecified_event_type_still_returns_200(
	monkeypatch: Any,
) -> None:
	"""A JSON object with no ``type`` is well-formed enough; only a non-object is refused."""
	STATE.authorization = f"Bearer {jwt()}"
	STATE.body = b"{}"

	response = call_handle(monkeypatch, verifier_returning(chat_claims()))

	assert response == {}
	assert FRAPPE.local.response.http_status_code == 200
	assert any("event_type=unspecified" in line for line in STATE.infos)


def test_the_verifier_is_handed_the_configured_audience_not_one_from_the_request(
	monkeypatch: Any,
) -> None:
	"""The audience must come from settings. A request-supplied audience is self-certification."""
	seen: list[tuple[str, str]] = []

	def _verify(token: str, audience: str) -> Mapping[str, Any]:
		STATE.trace.append("verify")
		seen.append((token, audience))
		return chat_claims()

	STATE.authorization = f"Bearer {jwt()}"
	STATE.body = b'{"type":"MESSAGE","audience":"' + OTHER_AUDIENCE.encode("ascii") + b'"}'

	call_handle(monkeypatch, _verify)

	assert seen and seen[0][1] == ENDPOINT_AUDIENCE


# ---------------------------------------------------------------------------
# Surrounding behaviour the six cases depend on
# ---------------------------------------------------------------------------


def test_the_verifier_is_pure_with_respect_to_frappe() -> None:
	"""``verify_chat_bearer_token`` must read no settings and touch no table.

	That purity is what lets every case above run bench-free and offline, and it is also the
	property that keeps the security boundary reviewable: the whole decision is visible in
	its arguments.
	"""
	result = webhook.verify_chat_bearer_token(
		jwt(),
		ENDPOINT_AUDIENCE,
		oidc_verifier=verifier_returning(chat_claims()),
		now=1_000,
	)

	assert result.ok
	assert STATE.trace == ["verify"], STATE.trace


def test_a_missing_google_auth_library_is_distinguished_from_a_bad_signature() -> None:
	"""An operator problem must not be logged as an attack, or nobody finds the real cause."""
	result = webhook.verify_chat_bearer_token(
		jwt(),
		ENDPOINT_AUDIENCE,
		oidc_verifier=verifier_raising(ImportError("No module named 'google'")),
	)

	assert not result.ok
	assert result.reason == webhook.REJECT_VERIFIER_UNAVAILABLE


def test_a_verifier_returning_a_non_mapping_is_refused() -> None:
	"""Fail closed on a claim set that is not a claim set, rather than duck-typing into it."""
	for junk in ("claims", 42, None, ["aud"]):
		result = webhook.verify_chat_bearer_token(
			jwt(),
			ENDPOINT_AUDIENCE,
			oidc_verifier=verifier_returning_raw(junk),
			now=1_000,
		)
		assert not result.ok, junk
		assert result.reason == webhook.REJECT_MALFORMED_TOKEN


def verifier_returning_raw(value: Any) -> Any:
	"""A verifier that returns something that is not a claim mapping at all."""

	def _verify(token: str, audience: str) -> Any:
		STATE.trace.append("verify")
		return value

	return _verify


def test_verification_result_is_falsy_when_it_fails_and_truthy_when_it_passes() -> None:
	"""``if not result:`` is how the handler reads it; ``__bool__`` has to mean what it says."""
	assert not webhook.verify_chat_bearer_token("", ENDPOINT_AUDIENCE)
	assert webhook.verify_chat_bearer_token(
		jwt(),
		ENDPOINT_AUDIENCE,
		oidc_verifier=verifier_returning(chat_claims()),
		now=1_000,
	)


def test_cert_cache_ttl_is_clamped_in_both_directions() -> None:
	"""A hostile ``Cache-Control`` must not pin a stale key document, nor collapse the cache.

	Pure, so it belongs here rather than behind a network call: the failure it prevents is a
	cert fetch on the hot path of every inbound event, which turns a ``googleapis.com`` blip
	into a 401 for every legitimate request.
	"""
	assert webhook.cache_ttl_from_headers({}) == webhook.CERT_CACHE_DEFAULT_TTL
	assert webhook.cache_ttl_from_headers({"Cache-Control": "no-store"}) == webhook.CERT_CACHE_DEFAULT_TTL
	assert webhook.cache_ttl_from_headers({"cache-control": "max-age=1"}) == webhook.CERT_CACHE_MIN_TTL
	assert (
		webhook.cache_ttl_from_headers({"Cache-Control": "public, max-age=99999999"})
		== webhook.CERT_CACHE_MAX_TTL
	)
	assert webhook.cache_ttl_from_headers({"Cache-Control": "public, max-age=7200"}) == 7200


def test_the_chat_issuer_constant_is_not_configurable() -> None:
	"""It is hard-coded on purpose: a settings field here is a security boundary somebody
	can widen from the Desk, and the whole point of the check is that it cannot be."""
	assert webhook.CHAT_ISSUER == CHAT_ISSUER
