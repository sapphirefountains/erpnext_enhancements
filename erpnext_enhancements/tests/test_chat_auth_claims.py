# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the hand-rolled DWD assertion claim set (ADR 0009 §E.4).

Plain pytest functions, and this suite needs its **own** ``python -m pytest``
step in ``ci.yml`` — ``python -m unittest`` collects zero function-style tests
and reports success, which is how a suite in this repo once ran nowhere for
weeks.

**Why this is the highest-value test file in Phase 1.** ``chat/gchat/auth.py``
hand-rolls a security primitive. It does so for a documented reason —
``google.auth.impersonated_credentials`` has no ``subject`` parameter and no
``with_subject`` method, so the library gives impersonation but not delegation,
and the ``sub`` claim has nowhere supported to live — but hand-rolled is
hand-rolled. The claim set is the part of that primitive with an executable
contract, and this file is its only automated defence. What it *cannot* do is
prove Google accepts the assertion; that is smoke-test step 2, and it has not
been run (no live Google calls were made from this session).

Unlike the pure-tier client suite, this one **must** install a ``frappe`` stub:
``auth.py`` imports ``frappe`` and ``requests`` at module scope because
``ChatAuthError`` subclasses ``frappe.ValidationError``. It also blocks
``google.auth`` so the ADC step takes the metadata-server path deterministically
on every machine, rather than depending on what happens to be installed. That
stubbing is why this suite needs a CI step to itself: the suites that install
stubs cross-talk when they share a process.

**Every credential in this file is fake and says so** — ``FAKE-DO-NOT-USE``
appears inside each one, so a grep for real key material cannot mistake a fixture
for a leak, and a fixture cannot be mistaken for something worth trying.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import sys
import types
from typing import Any

import pytest

# Every fixture credential carries the marker. The committed-secret scan
# allowlists exactly this shape.
FAKE_DELEGATION_SA = "chat-relay-FAKE-DO-NOT-USE@fake-project.iam.gserviceaccount.com"
FAKE_APP_SA = "chat-app-FAKE-DO-NOT-USE@fake-project.iam.gserviceaccount.com"
FAKE_ADC_TOKEN = "ya29.FAKE-DO-NOT-USE-adc-token"
FAKE_SIGNED_JWT = "eyJhbGciOiJSUzI1NiJ9.FAKE-DO-NOT-USE-assertion.FAKE-DO-NOT-USE-signature"
FAKE_ACCESS_TOKEN = "ya29.FAKE-DO-NOT-USE-delegated-access-token"

SUBJECT = "relay.user@example.com"
FIXED_NOW = 1_775_000_000

#: Everything that must never appear in a log line, an exception message, or any
#: other surface this module can reach. Kept as one list so a new secret-shaped
#: value gets covered by every leak assertion at once.
SECRET_MATERIAL = (FAKE_ADC_TOKEN, FAKE_SIGNED_JWT, FAKE_ACCESS_TOKEN)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class Recorder:
	"""A logger that records everything, so "nothing was logged" is provable.

	The module under test exposes **no** logging surface — it never calls
	``frappe.logger`` or ``frappe.log_error``, on purpose, because every frame on
	this path holds token material. Asserting an absence is hard, so this hands
	the module a working logger and asserts it stays empty. If somebody adds a
	well-meaning ``logger.info(f"minted {token}")``, this catches it; a source
	scan alone would not catch it if the call were indirect.
	"""

	def __init__(self) -> None:
		self.lines: list[str] = []

	def _record(self, *args: Any, **kwargs: Any) -> None:
		self.lines.append(" ".join(str(arg) for arg in args) + " " + repr(kwargs))

	info = warning = error = debug = exception = critical = _record


class FakeResponse:
	"""Minimal ``requests.Response`` stand-in: a status, a JSON body, a text body."""

	def __init__(self, status_code: int, payload: Any = None, text: str | None = None) -> None:
		self.status_code = status_code
		self._payload = payload
		self.text = text if text is not None else json.dumps(payload or {})

	def json(self) -> Any:
		if self._payload is None:
			raise ValueError("no json")
		return self._payload


class FakeHttp:
	"""A programmable ``requests`` stand-in that logs what was actually sent.

	Routing is by URL substring rather than by exact match so a test reads as
	"the signJwt call" instead of as a URL template, and the request log is what
	lets the wiring assertion check that the bytes handed to ``signJwt`` are
	exactly :func:`serialise_claims`' output.
	"""

	class RequestException(Exception):
		pass

	def __init__(self) -> None:
		self.sent: list[dict[str, Any]] = []
		self.metadata_response = FakeResponse(200, {"access_token": FAKE_ADC_TOKEN, "expires_in": 3600})
		self.sign_jwt_response = FakeResponse(200, {"signedJwt": FAKE_SIGNED_JWT})
		self.token_response = FakeResponse(
			200, {"access_token": FAKE_ACCESS_TOKEN, "expires_in": 3600, "token_type": "Bearer"}
		)

	def get(self, url: str, **kwargs: Any) -> FakeResponse:
		self.sent.append({"method": "GET", "url": url, **kwargs})
		return self.metadata_response

	def post(self, url: str, **kwargs: Any) -> FakeResponse:
		self.sent.append({"method": "POST", "url": url, **kwargs})
		if ":signJwt" in url:
			return self.sign_jwt_response
		return self.token_response

	def sent_to(self, fragment: str) -> list[dict[str, Any]]:
		return [call for call in self.sent if fragment in call["url"]]


class FakeCache:
	"""``frappe.cache()`` — a dict with the three methods ``auth`` wraps."""

	def __init__(self) -> None:
		self.store: dict[str, Any] = {}
		self.ttls: dict[str, int] = {}

	def get_value(self, key: str) -> Any:
		return self.store.get(key)

	def set_value(self, key: str, value: Any, expires_in_sec: int | None = None) -> None:
		self.store[key] = value
		if expires_in_sec is not None:
			self.ttls[key] = expires_in_sec

	def delete_value(self, key: str) -> None:
		self.store.pop(key, None)
		self.ttls.pop(key, None)


class FakeChatSettings:
	"""``Chat Settings`` — **identifiers only**, and that is the whole design.

	There is no ``Password`` field here because there is nothing to store: the
	keyless flow signs through IAM Credentials with key material nobody in this
	codebase ever sees. If a future change wants a credential field, the design
	has gone wrong and
	:func:`test_the_module_never_reads_a_password_field` should be the thing that
	says so.
	"""

	def __init__(self, **overrides: Any) -> None:
		self.enabled = 1
		self.delegation_service_account = FAKE_DELEGATION_SA
		self.chat_app_service_account = FAKE_APP_SA
		self.http_timeout_seconds = 30
		for key, value in overrides.items():
			setattr(self, key, value)


class ExplodingClock:
	"""Stands in for the ``time`` module and refuses to be read.

	The claim set must be a total function of its arguments: a builder that reads
	the clock cannot be asserted against a fixed expectation, and this claim set's
	only automated defence *is* that assertion.
	"""

	def time(self) -> float:
		raise AssertionError("build_assertion_claims must not read the clock")


class Env:
	"""Everything a test needs, bundled so the fixture returns one object."""

	def __init__(self, auth: Any, frappe: Any, http: FakeHttp, cache: FakeCache, logger: Recorder) -> None:
		self.auth = auth
		self.frappe = frappe
		self.http = http
		self.cache = cache
		self.logger = logger
		self.settings = frappe.__chat_settings__


# ---------------------------------------------------------------------------
# Stub installation
# ---------------------------------------------------------------------------

_STUBBED = ("frappe", "requests", "google.auth", "google.auth.transport.requests")


def install_stubs(http: FakeHttp, cache: FakeCache, logger: Recorder, settings: FakeChatSettings) -> Any:
	"""Install the fake ``frappe`` / ``requests`` and block ``google.auth``.

	``google.auth`` is blocked with ``sys.modules[name] = None`` (the documented
	way to make an import raise ``ImportError``) so ``_adc_access_token`` takes the
	metadata-server branch on every machine. Whether ``google.auth`` imports on
	the production bench is an open ``VERIFY:`` in the module; a test whose result
	depends on the answer would be a test that means something different on CI
	than on a laptop.
	"""
	frappe = types.ModuleType("frappe")

	class ValidationError(Exception):
		pass

	frappe.ValidationError = ValidationError
	frappe.__chat_settings__ = settings
	frappe.get_cached_doc = lambda _doctype: settings
	frappe.cache = lambda: cache
	frappe.logger = lambda *args, **kwargs: logger
	frappe.log_error = logger._record
	frappe._ = lambda message=None, *args, **kwargs: message

	sys.modules["frappe"] = frappe
	sys.modules["requests"] = http  # type: ignore[assignment]
	sys.modules["google.auth"] = None  # type: ignore[assignment]
	sys.modules["google.auth.transport.requests"] = None  # type: ignore[assignment]
	return frappe


def import_auth() -> Any:
	"""(Re)import the module under test against the current stubs."""
	sys.modules.pop("erpnext_enhancements.chat.gchat.auth", None)
	return importlib.import_module("erpnext_enhancements.chat.gchat.auth")


@pytest.fixture()
def env() -> Any:
	saved = {name: sys.modules.get(name) for name in _STUBBED}
	saved_present = {name: name in sys.modules for name in _STUBBED}

	http, cache, logger, settings = FakeHttp(), FakeCache(), Recorder(), FakeChatSettings()
	frappe = install_stubs(http, cache, logger, settings)
	try:
		yield Env(import_auth(), frappe, http, cache, logger)
	finally:
		sys.modules.pop("erpnext_enhancements.chat.gchat.auth", None)
		for name in _STUBBED:
			if saved_present[name]:
				sys.modules[name] = saved[name]
			else:
				sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# build_assertion_claims — the six claims
# ---------------------------------------------------------------------------


def test_the_claim_set_is_exactly_the_six_documented_claims(env: Env) -> None:
	"""``iss``, ``sub``, ``scope``, ``aud``, ``iat``, ``exp`` — and nothing else.

	Asserted as an exact key set rather than as six ``in`` checks: an *extra*
	claim is as much a change to a signed security token as a missing one, and it
	would otherwise ship unnoticed.
	"""
	claims = env.auth.build_assertion_claims(
		FAKE_DELEGATION_SA, SUBJECT, ["scope-a", "scope-b"], FIXED_NOW
	)
	assert set(claims) == {"iss", "sub", "scope", "aud", "iat", "exp"}


def test_iss_is_the_delegation_service_account_not_the_vm_and_not_the_user(env: Env) -> None:
	"""Three service accounts are in play — the VM's, the delegation SA and the
	Chat app's — and putting the wrong one in ``iss`` produces a 403 from IAM
	Credentials that reads as a missing IAM binding."""
	claims = env.auth.build_assertion_claims(FAKE_DELEGATION_SA, SUBJECT, ["scope-a"], FIXED_NOW)
	assert claims["iss"] == FAKE_DELEGATION_SA
	assert claims["iss"] != SUBJECT


def test_sub_is_the_impersonated_user(env: Env) -> None:
	"""``sub`` is what makes the relayed message render as the real person. It is
	the single claim that distinguishes human attribution (CQ-1, resolved in its
	favour) from an ``App``-badged bot post."""
	claims = env.auth.build_assertion_claims(FAKE_DELEGATION_SA, SUBJECT, ["scope-a"], FIXED_NOW)
	assert claims["sub"] == SUBJECT


def test_sub_is_omitted_entirely_for_the_app_identity_not_sent_empty(env: Env) -> None:
	"""An empty-string ``sub`` is **not** the same thing as an absent one.

	Omitting it turns the delegated assertion into the plain two-legged
	service-account grant the Chat app uses; sending ``"sub": ""`` is rejected by
	Google. This is the difference between Triton's leg working and a 400 nobody
	can read.
	"""
	claims = env.auth.build_assertion_claims(FAKE_APP_SA, "", ["scope-a"], FIXED_NOW)
	assert "sub" not in claims


def test_scope_is_space_separated_in_the_callers_order(env: Env) -> None:
	"""Order is the caller's, because the DWD authorisation in the Admin console
	is a byte-comparison against a pasted list and reordering here would make a
	mismatch look like a wrong scope string."""
	claims = env.auth.build_assertion_claims(
		FAKE_DELEGATION_SA, SUBJECT, ["scope-b", "scope-a", "scope-c"], FIXED_NOW
	)
	assert claims["scope"] == "scope-b scope-a scope-c"


def test_the_relay_scope_tuple_is_pinned(env: Env) -> None:
	"""Changing this tuple is a **Workspace admin change**, not a code change: the
	DWD scope list is frozen in one super-admin session and takes up to 24 hours to
	propagate. Edited apart, every call fails ``401 unauthorized_client``, which
	looks identical to a wrong client id. Pinning it makes the code half of that
	edit a visible diff.
	"""
	assert env.auth.RELAY_SCOPES == (
		"https://www.googleapis.com/auth/chat.messages",
		"https://www.googleapis.com/auth/chat.spaces",
		"https://www.googleapis.com/auth/chat.memberships",
	)
	assert env.auth.APP_SCOPES == ("https://www.googleapis.com/auth/chat.bot",)


def test_aud_is_always_the_google_token_endpoint(env: Env) -> None:
	claims = env.auth.build_assertion_claims(FAKE_DELEGATION_SA, SUBJECT, ["scope-a"], FIXED_NOW)
	assert claims["aud"] == "https://oauth2.googleapis.com/token"
	assert claims["aud"] == env.auth.OAUTH_TOKEN_URL


def test_iat_is_the_injected_clock_and_exp_is_at_most_ten_minutes_later(env: Env) -> None:
	"""``exp - iat <= 600``. Ten minutes is the conventional maximum; the ADR
	records that Google's actual ceiling is undocumented, so this pins the value
	we chose rather than a value we verified."""
	claims = env.auth.build_assertion_claims(FAKE_DELEGATION_SA, SUBJECT, ["scope-a"], FIXED_NOW)
	assert claims["iat"] == FIXED_NOW
	assert claims["exp"] - claims["iat"] == 600
	assert claims["exp"] - claims["iat"] <= env.auth.MAX_ASSERTION_LIFETIME_SECONDS


@pytest.mark.parametrize("lifetime", [1, 60, 599, 600])
def test_lifetimes_up_to_the_maximum_are_accepted(env: Env, lifetime: int) -> None:
	claims = env.auth.build_assertion_claims(
		FAKE_DELEGATION_SA, SUBJECT, ["scope-a"], FIXED_NOW, lifetime_seconds=lifetime
	)
	assert claims["exp"] - claims["iat"] == lifetime


@pytest.mark.parametrize("lifetime", [601, 3600, 86_400])
def test_an_over_long_lifetime_is_rejected_not_silently_clamped(env: Env, lifetime: int) -> None:
	"""**The implementation raises; it does not clamp — and that is the contract.**

	Clamping would hide a caller's mistaken belief about how long its credential
	lives, and the belief is the bug. Raising surfaces it here, in a unit-testable
	place, instead of as an opaque 400 from Google that reads like a configuration
	problem. This test asserts the *rejection*, so a later "helpful" clamp fails.
	"""
	with pytest.raises(env.auth.ChatAuthError, match="exceeds the 600s maximum"):
		env.auth.build_assertion_claims(
			FAKE_DELEGATION_SA, SUBJECT, ["scope-a"], FIXED_NOW, lifetime_seconds=lifetime
		)


@pytest.mark.parametrize("lifetime", [0, -1, -600])
def test_a_non_positive_lifetime_is_rejected(env: Env, lifetime: int) -> None:
	"""``exp <= iat`` is an assertion that is expired the moment it is signed."""
	with pytest.raises(env.auth.ChatAuthError, match="must be positive"):
		env.auth.build_assertion_claims(
			FAKE_DELEGATION_SA, SUBJECT, ["scope-a"], FIXED_NOW, lifetime_seconds=lifetime
		)


def test_an_assertion_with_no_issuer_or_no_scopes_is_refused(env: Env) -> None:
	"""An assertion with no scopes would be granted nothing, and a missing issuer
	means ``Chat Settings`` was never configured — both are worth naming locally
	rather than sending and reading Google's answer."""
	with pytest.raises(env.auth.ChatAuthError, match="issuer"):
		env.auth.build_assertion_claims("", SUBJECT, ["scope-a"], FIXED_NOW)
	with pytest.raises(env.auth.ChatAuthError, match="no scopes"):
		env.auth.build_assertion_claims(FAKE_DELEGATION_SA, SUBJECT, [], FIXED_NOW)


def test_chat_auth_error_is_a_validation_error_so_one_catch_covers_the_module(env: Env) -> None:
	"""Mirrors ``StripeError``: a ``ValidationError`` subclass, so it surfaces in
	the desk instead of as a 500 and a caller catches one thing."""
	assert issubclass(env.auth.ChatAuthError, env.frappe.ValidationError)


# ---------------------------------------------------------------------------
# The clock is injected, not read
# ---------------------------------------------------------------------------


def test_the_clock_is_injected_not_read(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
	"""Proven by taking the clock away.

	``monkeypatch`` replaces the ``time`` *attribute on the auth module*, not the
	global ``time`` module, so nothing else in the process is affected. If
	``build_assertion_claims`` ever reaches for the clock, this raises instead of
	quietly producing a value that happens to be right today.
	"""
	monkeypatch.setattr(env.auth, "time", ExplodingClock())
	claims = env.auth.build_assertion_claims(FAKE_DELEGATION_SA, SUBJECT, ["scope-a"], FIXED_NOW)
	assert claims["iat"] == FIXED_NOW


def test_now_epoch_is_a_required_argument(env: Env) -> None:
	"""There is no default, because a default would be a clock read wearing a
	signature."""
	with pytest.raises(TypeError):
		env.auth.build_assertion_claims(FAKE_DELEGATION_SA, SUBJECT, ["scope-a"])  # type: ignore[call-arg]


def test_the_same_inputs_always_produce_the_same_claim_set(env: Env) -> None:
	first = env.auth.build_assertion_claims(FAKE_DELEGATION_SA, SUBJECT, ["a", "b"], FIXED_NOW)
	second = env.auth.build_assertion_claims(FAKE_DELEGATION_SA, SUBJECT, ["a", "b"], FIXED_NOW)
	assert first == second


def test_minting_reads_real_utc_and_not_frappes_site_local_clock(env: Env) -> None:
	"""``_mint`` must call ``time.time()``, never ``frappe.utils.now_datetime()``.

	The latter returns a **naive** datetime in the *site's* timezone; calling
	``.timestamp()`` on it reinterprets those wall-clock digits in the *server's*
	timezone and silently shifts ``iat``/``exp`` by the offset between the two.
	This app has already shipped one always-fails bug from exactly that confusion,
	and Google compares these claims against real UTC.
	"""
	code = _code_only(env.auth)
	assert "now_datetime" not in code
	assert "time.time()" in code


# ---------------------------------------------------------------------------
# Serialisation determinism
# ---------------------------------------------------------------------------


def test_serialisation_is_byte_identical_for_the_same_claims(env: Env) -> None:
	"""Sorted keys and no incidental whitespace.

	Google does not care about key order — the determinism buys a byte-exact test
	and nothing else. But a test that cannot compare bytes is a test that compares
	nothing, and bytes are what get signed.
	"""
	claims = env.auth.build_assertion_claims(FAKE_DELEGATION_SA, SUBJECT, ["a", "b"], FIXED_NOW)
	reordered = {key: claims[key] for key in reversed(list(claims))}

	first = env.auth.serialise_claims(claims)
	second = env.auth.serialise_claims(reordered)

	assert first == second
	assert first.encode("utf-8") == second.encode("utf-8")


def test_serialised_claims_are_sorted_and_compact(env: Env) -> None:
	claims = env.auth.build_assertion_claims(FAKE_DELEGATION_SA, SUBJECT, ["a", "b"], FIXED_NOW)
	payload = env.auth.serialise_claims(claims)

	assert payload.startswith('{"aud":')
	assert ", " not in payload
	assert '": ' not in payload
	assert list(json.loads(payload)) == sorted(claims)


def test_serialisation_is_a_json_object_and_nothing_more(env: Env) -> None:
	"""``signJwt`` wants *"a serialized JSON object that contains a JWT Claims
	Set"* — so no base64url and no header. IAM Credentials builds both, chooses
	``alg``/``kid``, and signs with key material this codebase never sees. That is
	the whole of our contribution to the signing step."""
	payload = env.auth.serialise_claims(
		env.auth.build_assertion_claims(FAKE_DELEGATION_SA, SUBJECT, ["a"], FIXED_NOW)
	)
	assert json.loads(payload)["iss"] == FAKE_DELEGATION_SA
	assert "." not in payload.split('"')[0]
	assert not payload.startswith("eyJ")


def test_the_bytes_handed_to_sign_jwt_are_exactly_the_serialised_claims(env: Env) -> None:
	"""The wiring assertion. Everything above tests the builder; this tests that
	the builder's output is what actually leaves the process."""
	env.auth.get_delegated_token(SUBJECT)

	posted = env.http.sent_to(":signJwt")
	assert len(posted) == 1
	sent_payload = posted[0]["json"]["payload"]

	claims = json.loads(sent_payload)
	assert claims["iss"] == FAKE_DELEGATION_SA
	assert claims["sub"] == SUBJECT
	assert claims["aud"] == env.auth.OAUTH_TOKEN_URL
	assert claims["exp"] - claims["iat"] == 600
	assert sent_payload == env.auth.serialise_claims(claims)
	assert posted[0]["url"].endswith(f"/projects/-/serviceAccounts/{FAKE_DELEGATION_SA}:signJwt")


def test_the_app_identity_signs_an_assertion_with_no_sub_claim(env: Env) -> None:
	"""Phase 1 only *mints* this token; nothing calls it yet. It exists now so the
	two identities stay one code path rather than two divergent ones."""
	env.auth.get_app_token()

	claims = json.loads(env.http.sent_to(":signJwt")[0]["json"]["payload"])
	assert claims["iss"] == FAKE_APP_SA
	assert "sub" not in claims
	assert claims["scope"] == "https://www.googleapis.com/auth/chat.bot"


# ---------------------------------------------------------------------------
# No token, assertion or key ever reaches a log line
# ---------------------------------------------------------------------------


def _module_source(module: Any) -> str:
	with open(module.__file__, encoding="utf-8") as handle:
		return handle.read()


def _code_only(module: Any) -> str:
	"""``module``'s source with docstrings and comments removed.

	The naive version of these scans was wrong in a way worth recording, because
	it is the way every source-scan test is first written: ``"now_datetime" not in
	source`` failed on the **comment explaining why ``now_datetime`` must not be
	used**, and ``"chat.googleapis.com" not in source`` failed on the comment
	saying that host deliberately does not appear here. A scan that punishes a
	module for documenting its own rule teaches people to delete the
	documentation.

	Docstring spans come from the AST, which knows exactly which string literals
	are docstrings; comments are stripped by regex, which will also cut a ``#``
	inside a string literal. That is accepted — these scans look for call sites,
	not for prose, and a false *negative* on a ``#``-bearing string is cheaper
	than the false positives above.
	"""
	source = _module_source(module)
	drop: set[int] = set()
	for node in ast.walk(ast.parse(source)):
		if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
			continue
		body = getattr(node, "body", None)
		first = body[0] if body else None
		if (
			isinstance(first, ast.Expr)
			and isinstance(first.value, ast.Constant)
			and isinstance(first.value.value, str)
		):
			drop.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))

	return "\n".join(
		re.sub(r"#.*$", "", line)
		for number, line in enumerate(source.splitlines(), start=1)
		if number not in drop
	)


def test_the_module_exposes_no_logging_surface_at_all(env: Env) -> None:
	"""The strongest form of "no token is ever logged": **nothing** is logged.

	Every frame on this path holds token material, so the module does not log,
	full stop. Asserted against the source because an indirect call is still a
	call, and because the rule is easier to keep than "log carefully".

	``print`` is checked too. A stray ``print`` in a Frappe background worker goes
	to the worker's stdout, which is captured — it is a log line by another name.
	"""
	code = _code_only(env.auth)
	for forbidden in ("frappe.logger", "log_error", "logging.", "traceback"):
		assert forbidden not in code, f"auth.py must not reference {forbidden!r}"
	# Word-boundaried, because `scope_fingerprint(` contains the substring `print(`.
	assert re.search(r"\bprint\s*\(", code) is None, "auth.py must not print"


def test_a_successful_mint_writes_nothing_to_the_logger_it_was_handed(env: Env) -> None:
	"""The module was given a working logger; it must still have used none of it."""
	token = env.auth.get_delegated_token(SUBJECT)

	assert token == FAKE_ACCESS_TOKEN
	assert env.logger.lines == []


@pytest.mark.parametrize(
	("failure", "match"),
	[
		("sign_jwt_403", "serviceAccountTokenCreator"),
		("sign_jwt_404", "no such service account"),
		("token_unauthorized_client", "Domain Wide Delegation"),
		("token_invalid_grant", "clock has drifted"),
	],
)
def test_a_failed_mint_logs_nothing_and_leaks_nothing(env: Env, failure: str, match: str) -> None:
	"""Failure is where credentials leak, because failure is where people log.

	Each case asserts three things at once: the diagnosis is present (a bare
	``403`` here means the IAM binding, overwhelmingly often, and saying so is
	worth more than the code costs), nothing was logged, and **no secret appears
	in the exception message** — which matters because that message lands in
	``Chat Relay Job.last_error``, a field readable by anyone with the DocType.
	"""
	if failure == "sign_jwt_403":
		env.http.sign_jwt_response = FakeResponse(
			403, {"error": {"message": "Permission denied", "signedJwt": FAKE_SIGNED_JWT}}
		)
	elif failure == "sign_jwt_404":
		env.http.sign_jwt_response = FakeResponse(404, {"error": {"message": "Not found"}})
	elif failure == "token_unauthorized_client":
		env.http.token_response = FakeResponse(
			401, {"error": "unauthorized_client", "assertion": FAKE_SIGNED_JWT}
		)
	else:
		env.http.token_response = FakeResponse(
			400, {"error": "invalid_grant", "access_token": FAKE_ACCESS_TOKEN}
		)

	with pytest.raises(env.auth.ChatAuthError) as raised:
		env.auth.get_delegated_token(SUBJECT)

	message = str(raised.value)
	assert match in message
	assert env.logger.lines == []
	for secret in SECRET_MATERIAL:
		assert secret not in message


def test_the_error_describers_never_echo_the_body_verbatim(env: Env) -> None:
	"""Pure, and the reason they are pure: the request that produced these bodies
	carried a signed assertion, so the body is not safe to repeat."""
	token_message = env.auth.describe_token_error(
		400, {"error": "invalid_scope", "assertion": FAKE_SIGNED_JWT, "access_token": FAKE_ACCESS_TOKEN}
	)
	sign_message = env.auth.describe_sign_jwt_error(
		403, {"error": {"message": "Permission denied", "signedJwt": FAKE_SIGNED_JWT}}
	)

	for message in (token_message, sign_message):
		for secret in SECRET_MATERIAL:
			assert secret not in message

	assert "invalid_scope" in token_message
	assert "malformed or not authorised" in token_message


def test_the_dwd_propagation_window_is_named_in_the_error(env: Env) -> None:
	"""Without this sentence, the first symptom of a 24-hour Admin-console
	propagation delay is an engineer re-reading their claim builder."""
	message = env.auth.describe_token_error(401, {"error": "unauthorized_client"})
	assert "24 hours" in message
	assert "numeric OAuth client id" in message


def test_the_module_never_reads_a_password_field(env: Env) -> None:
	"""``Chat Settings`` holds identifiers only, and this is the code half of that.

	Under the keyless design there is nothing secret to store: no service-account
	JSON, no private key, no client secret. A ``get_password`` call here would mean
	somebody had added a credential field, which is the point at which the design
	has gone wrong — so it fails here rather than in review.
	"""
	code = _code_only(env.auth)
	for forbidden in ("get_password", "private_key", "BEGIN PRIVATE KEY", "client_secret"):
		assert forbidden not in code, f"auth.py must not reference {forbidden!r}"


def test_no_secret_material_survives_into_the_cache_key(env: Env) -> None:
	"""The cache key is written into Redis and read by operators; it must be an
	identifier, not a credential."""
	env.auth.get_delegated_token(SUBJECT)
	for key in env.cache.store:
		for secret in SECRET_MATERIAL:
			assert secret not in key


# ---------------------------------------------------------------------------
# The cache and the master switch
# ---------------------------------------------------------------------------


def test_the_token_cache_is_per_impersonated_user(env: Env) -> None:
	"""A single shared entry would hand one person's delegated credential to
	everybody (ADR §E.4.2 constraint 1). At the measured roster that is ~20 live
	tokens, which is the price of the property."""
	first = env.auth._token_cache_key("dwd", SUBJECT, env.auth.RELAY_SCOPES)
	second = env.auth._token_cache_key("dwd", "someone.else@example.com", env.auth.RELAY_SCOPES)
	assert first != second
	assert SUBJECT in first


def test_the_cache_key_is_insensitive_to_scope_order(env: Env) -> None:
	"""A caller that reorders its scope tuple must hit the same entry rather than
	mint a second token for the same grant."""
	forward = env.auth._token_cache_key("dwd", SUBJECT, ["scope-a", "scope-b"])
	reversed_order = env.auth._token_cache_key("dwd", SUBJECT, ["scope-b", "scope-a"])
	assert forward == reversed_order


def test_a_cached_token_is_reused_rather_than_reminted(env: Env) -> None:
	"""Minting costs two network round trips, so a cache miss on every relayed
	message would be four extra calls per message."""
	assert env.auth.get_delegated_token(SUBJECT) == FAKE_ACCESS_TOKEN
	first_call_count = len(env.http.sent)
	assert env.auth.get_delegated_token(SUBJECT) == FAKE_ACCESS_TOKEN
	assert len(env.http.sent) == first_call_count


def test_force_refresh_bypasses_the_cache(env: Env) -> None:
	env.auth.get_delegated_token(SUBJECT)
	before = len(env.http.sent)
	env.auth.get_delegated_token(SUBJECT, force_refresh=True)
	assert len(env.http.sent) > before


def test_the_cached_ttl_expires_before_the_token_does(env: Env) -> None:
	"""Lifetime minus a safety margin, floored — so an in-flight request can never
	be holding a token that expires mid-call, and a pathologically short
	``expires_in`` cannot produce a cache that never holds anything."""
	assert env.auth._cache_ttl(3600) == 3600 - env.auth.TOKEN_CACHE_SAFETY_MARGIN_SECONDS
	assert env.auth._cache_ttl(10) == env.auth.MIN_TOKEN_CACHE_SECONDS
	assert env.auth._cache_ttl(0) == env.auth.MIN_TOKEN_CACHE_SECONDS


def test_invalidating_the_cache_drops_exactly_one_token(env: Env) -> None:
	env.auth.get_delegated_token(SUBJECT)
	assert env.cache.store
	env.auth.invalidate_token_cache(SUBJECT)
	assert env.cache.store == {}


def test_the_master_switch_is_enforced_at_the_bottom_of_the_stack(env: Env) -> None:
	"""When ``enabled`` is off, **no Google call is made from any path**.

	Minting a token is a Google call, so the switch is checked here as well as at
	the top of each job — a defence that survives somebody adding a new caller and
	forgetting the check. The assertion that matters is the second one: zero HTTP.
	"""
	env.settings.enabled = 0

	with pytest.raises(env.auth.ChatAuthError, match="Chat is disabled"):
		env.auth.get_delegated_token(SUBJECT)
	with pytest.raises(env.auth.ChatAuthError, match="Chat is disabled"):
		env.auth.get_app_token()

	assert env.http.sent == []
	assert env.cache.store == {}


def test_a_delegated_token_requires_the_email_to_impersonate(env: Env) -> None:
	"""Guessing an identity is the one mistake on this path with a privacy
	consequence, so the module refuses rather than falling back to the app
	identity — which would silently swap human attribution for an ``App`` badge."""
	with pytest.raises(env.auth.ChatAuthError, match="requires the email"):
		env.auth.get_delegated_token("")
	assert env.http.sent == []


def test_an_unconfigured_service_account_is_named_locally(env: Env) -> None:
	env.settings.delegation_service_account = ""
	with pytest.raises(env.auth.ChatAuthError, match="no delegation service account"):
		env.auth.get_delegated_token(SUBJECT)

	env.settings.chat_app_service_account = ""
	with pytest.raises(env.auth.ChatAuthError, match="no Chat app service account"):
		env.auth.get_app_token()


def test_settings_reads_are_defensive_against_a_missing_field(env: Env) -> None:
	"""``getattr(…, None) or default``, because this module is imported during
	``bench migrate`` when ``Chat Settings`` may exist with an older field set. An
	``AttributeError`` at that moment fails the migration, not the feature."""

	class BareSettings:
		pass

	env.frappe.get_cached_doc = lambda _doctype: BareSettings()
	config = env.auth.get_auth_config()

	assert config["enabled"] is False
	assert config["delegation_service_account"] == ""
	assert config["http_timeout_seconds"] == env.auth.DEFAULT_HTTP_TIMEOUT_SECONDS


def test_the_adc_step_falls_back_to_the_metadata_server(env: Env) -> None:
	"""``google.auth`` is blocked in this suite, so the metadata branch is the one
	exercised — which is also the branch the production topology (one GCE VM per
	environment) actually uses. The ADC token is deliberately **not** cached: it is
	a ``cloud-platform`` credential for the VM itself, strictly more powerful than
	the Chat tokens this module hands out, and the round trip is link-local."""
	env.auth.get_delegated_token(SUBJECT)

	metadata_calls = env.http.sent_to("metadata.google.internal")
	assert len(metadata_calls) == 1
	assert metadata_calls[0]["headers"] == {"Metadata-Flavor": "Google"}
	assert FAKE_ADC_TOKEN not in "".join(env.cache.store)
	assert FAKE_ADC_TOKEN not in "".join(str(value) for value in env.cache.store.values())


def test_an_unreachable_metadata_server_is_diagnosed_not_swallowed(env: Env) -> None:
	def _boom(*_args: Any, **_kwargs: Any) -> Any:
		raise env.http.RequestException("no route to host")

	env.http.get = _boom  # type: ignore[method-assign]
	with pytest.raises(env.auth.ChatAuthError, match="metadata server"):
		env.auth.get_delegated_token(SUBJECT)
	assert env.logger.lines == []


def test_the_signed_assertion_is_exchanged_with_the_jwt_bearer_grant(env: Env) -> None:
	env.auth.get_delegated_token(SUBJECT)

	exchange = env.http.sent_to("oauth2.googleapis.com/token")
	assert len(exchange) == 1
	assert exchange[0]["data"]["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
	assert exchange[0]["data"]["assertion"] == FAKE_SIGNED_JWT


def test_chat_googleapis_com_is_not_mentioned_in_the_auth_module(env: Env) -> None:
	"""The transport host lives in ``client.py`` and nowhere else — one module
	means one place a credential can appear in a stack frame. The full guardrail
	is ``test_chat_guardrails.py``; this is the local half, so a change here fails
	next to the change."""
	assert "chat.googleapis.com" not in _code_only(env.auth)
