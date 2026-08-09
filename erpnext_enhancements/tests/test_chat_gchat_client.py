# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the Google Chat transport's **pure tier** (ADR 0009 §G).

Plain pytest functions, not ``TestCase`` classes — same shape as
``test_quickbooks_online.py`` and ``test_gantt_api.py``, and it needs its **own**
``python -m pytest`` step in ``ci.yml``. ``python -m unittest`` silently collects
zero function-style tests and reports success; this repo has already shipped a
suite that ran nowhere for weeks because of exactly that, so the style is not a
preference.

**This suite installs no ``frappe`` stub, deliberately.** The four modules it
covers — ``backoff``, ``ids``, ``dryrun``, ``client`` — import neither ``frappe``
nor ``requests`` at module scope, and
:func:`test_the_pure_tier_imports_with_frappe_and_requests_unavailable` asserts
that property by blocking both imports and re-importing from scratch. It is the
load-bearing precondition for everything else here: the moment a module-scope
``import frappe`` appears in ``client.py``, the only CI tier with automatic
regression protection stops being able to reach the transport at all.

What each block is defending, in one line each:

* **Backoff** — a jittered sleep that can go negative is a ``ValueError`` out of
  ``time.sleep`` in a background worker at 3am, and a jitter that does not grow
  is a retry storm wearing a backoff's clothes.
* **Classification** — retrying a ``403`` converts a wrong identity or a missing
  DWD scope from a fast, legible failure into a slow, confusing one. That case
  has its own named test.
* **Id derivation** — ``clientAssignedMessageId`` is invariant I3's whole
  mechanism, and Google's three constraints on it (``client-`` prefix, ≤ 63
  characters, ``[a-z0-9-]`` only) are asserted against deliberately hostile
  ERPNext names rather than against the happy path.
* **Dry-run** — *"the transport is never entered"* is a Phase 1 acceptance
  criterion, so it is asserted the only way that means anything: by patching the
  single method that performs I/O and counting the calls.
"""

from __future__ import annotations

import importlib
import itertools
import random
import re
import sys
from typing import Any

import pytest

from erpnext_enhancements.chat.gchat import backoff as backoff_module
from erpnext_enhancements.chat.gchat import client as client_module
from erpnext_enhancements.chat.gchat import dryrun as dryrun_module
from erpnext_enhancements.chat.gchat import ids as ids_module
from erpnext_enhancements.chat.gchat.backoff import (
	RetryDecision,
	classify_error,
	compute_backoff,
	parse_retry_after,
	should_retry,
)
from erpnext_enhancements.chat.gchat.client import (
	AuthIdentity,
	GoogleChatClient,
	SpaceType,
	ThreadReply,
	build_create_message_call,
	build_setup_space_call,
	message_ids_for,
	validate_client_message_id,
)
from erpnext_enhancements.chat.gchat.ids import (
	CLIENT_MESSAGE_ID_MAX_LENGTH,
	CLIENT_MESSAGE_ID_PREFIX,
	assert_legal_client_message_id,
	client_message_id,
	is_client_message_id,
	request_id,
	scope_fingerprint,
)

#: Google's own charset rule, restated here rather than imported, so a change to
#: the module's private regex has to be made twice — once in the code and once in
#: front of a reviewer.
LEGAL_ID_CHARS = re.compile(r"\A[a-z0-9-]+\Z")

SITE = "chat-test.localhost"
OTHER_SITE = "staging.localhost"
SPACE = "spaces/AAAAmoUb1234"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class MaxDrawRandom:
	"""A ``random.Random`` stand-in that always draws the top of the range.

	Full jitter means the *value* is random and the *ceiling* is not, so the only
	property worth asserting about growth is the ceiling's. Forcing the draw to
	the endpoint turns ``compute_backoff`` into the deterministic function
	``min(cap, base * 2 ** attempt)`` and makes "grows with the attempt number" a
	real assertion instead of a coin flip that passes on most seeds.

	It also records every call, which is how the ``Retry-After`` test proves the
	jitter was not merely *overridden* but never *consulted*.
	"""

	def __init__(self) -> None:
		self.calls: list[tuple[float, float]] = []

	def uniform(self, low: float, high: float) -> float:
		self.calls.append((low, high))
		return high


class MinDrawRandom:
	"""The other endpoint. Guards the "never negative" claim at its worst case."""

	def __init__(self) -> None:
		self.calls: list[tuple[float, float]] = []

	def uniform(self, low: float, high: float) -> float:
		self.calls.append((low, high))
		return low


class RecordingTransport:
	"""Counts entries into :meth:`GoogleChatClient._request` and answers 200.

	Patched over the *class*, not an instance, so a dry-run call that constructs
	its own client internally could not slip past. Being a plain object rather
	than a function, it is not a descriptor and therefore receives **no** ``self``
	— which is why ``__call__`` takes only the keyword arguments ``_request`` is
	given.
	"""

	def __init__(self, status: int = 200, payload: dict[str, Any] | None = None) -> None:
		self.status = status
		self.payload = payload if payload is not None else {"name": f"{SPACE}/messages/real"}
		self.calls: list[dict[str, Any]] = []

	def __call__(self, **kwargs: Any) -> tuple[int, dict[str, Any], dict[str, str]]:
		self.calls.append(kwargs)
		return self.status, dict(self.payload), {}


def _exception_named(name: str, base: type[BaseException] = Exception) -> BaseException:
	"""An exception whose *class name* is ``name``.

	``classify_error`` matches on class names across the MRO rather than importing
	``requests``, precisely so the pure tier stays importable without it. Testing
	it therefore means minting the names it looks for, which is what a real
	``requests`` exception would present anyway.
	"""
	return type(name, (base,), {})("synthetic")


# ---------------------------------------------------------------------------
# The precondition: the pure tier must import with neither dependency present
# ---------------------------------------------------------------------------


def test_the_pure_tier_imports_with_frappe_and_requests_unavailable() -> None:
	"""``backoff``/``ids``/``dryrun``/``client`` must import with both blocked.

	CI installs neither ``frappe`` nor ``requests`` on this job, so every other
	assertion in this file rests on this one. ``sys.modules[name] = None`` is the
	documented way to make ``import name`` raise ``ImportError`` — it reproduces
	the CI environment exactly rather than approximating it.

	If this fails, the fix is *not* to add a stub: it is to move the offending
	import inside the function that needs it, the way ``client.py`` already does
	for ``requests`` in ``_request`` and for ``frappe`` in ``message_ids_for``.
	"""
	modules = (
		"erpnext_enhancements.chat.gchat.backoff",
		"erpnext_enhancements.chat.gchat.ids",
		"erpnext_enhancements.chat.gchat.dryrun",
		"erpnext_enhancements.chat.gchat.client",
	)
	saved = {name: sys.modules.get(name) for name in (*modules, "frappe", "requests")}
	try:
		for name in modules:
			sys.modules.pop(name, None)
		sys.modules["frappe"] = None  # type: ignore[assignment]
		sys.modules["requests"] = None  # type: ignore[assignment]
		for name in modules:
			assert importlib.import_module(name) is not None
	finally:
		for name, module in saved.items():
			if module is None and name in ("frappe", "requests"):
				sys.modules.pop(name, None)
			elif module is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = module


# ---------------------------------------------------------------------------
# compute_backoff — bounds, growth, and Retry-After precedence
# ---------------------------------------------------------------------------


def test_backoff_is_never_negative_and_never_exceeds_the_cap() -> None:
	"""Seeded, so a failure is reproducible rather than "it went red once".

	A negative sleep is a ``ValueError`` from ``time.sleep`` inside a relay
	worker; a sleep above the cap parks a worker holding a queue slot. Both are
	only ever observed in production, which is why they are pinned here.
	"""
	rng = random.Random(20260808)
	cap = 32.0
	for attempt in range(0, 13):
		for _ in range(200):
			delay = compute_backoff(attempt, 0.5, cap, rng=rng)
			assert delay >= 0.0
			assert delay <= cap


def test_backoff_ceiling_grows_with_the_attempt_number() -> None:
	"""With the draw forced to the endpoint, the delay is ``min(cap, base·2^n)``."""
	rng = MaxDrawRandom()
	delays = [compute_backoff(attempt, 0.5, 32.0, rng=rng) for attempt in range(0, 9)]

	assert delays == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 32.0, 32.0]
	assert all(later >= earlier for earlier, later in itertools.pairwise(delays))
	assert delays[5] > delays[0]


def test_backoff_saturates_at_the_cap_rather_than_overflowing() -> None:
	"""``2 ** attempt`` with an unbounded attempt is an ``OverflowError`` waiting
	for a bad caller; the exponent is clamped well past saturation."""
	rng = MaxDrawRandom()
	assert compute_backoff(10_000, 0.5, 32.0, rng=rng) == 32.0


def test_backoff_lower_endpoint_is_zero_not_negative() -> None:
	rng = MinDrawRandom()
	assert compute_backoff(4, 0.5, 32.0, rng=rng) == 0.0


def test_retry_after_wins_over_the_computed_value_and_the_rng_is_never_drawn() -> None:
	"""A ``Retry-After`` from Google is information; a jittered guess is not.

	The second half of the assertion is the one with teeth: it is not enough that
	the hint is returned, the jitter must not have been *consulted*. A
	``compute_backoff`` that drew and then discarded would still advance a shared
	RNG and would still be wrong about which value it believes.
	"""
	rng = MaxDrawRandom()
	assert compute_backoff(0, 0.5, 32.0, retry_after=7.5, rng=rng) == 7.5
	assert compute_backoff(6, 0.5, 32.0, retry_after=7.5, rng=rng) == 7.5
	assert rng.calls == []


def test_retry_after_is_clamped_to_the_cap() -> None:
	"""Honoured, but bounded — an over-long server hint must not park a worker.

	Stated explicitly because it is a *deliberate* departure from "obey the
	header": the retry budget, not one long sleep, is what absorbs an outage.
	"""
	rng = MaxDrawRandom()
	assert compute_backoff(0, 0.5, 32.0, retry_after=9_999.0, rng=rng) == 32.0


def test_a_negative_retry_after_is_a_malformed_header_not_an_instruction() -> None:
	rng = MaxDrawRandom()
	assert compute_backoff(3, 0.5, 32.0, retry_after=-5.0, rng=rng) == 4.0


def test_parse_retry_after_returns_none_for_junk_rather_than_zero() -> None:
	"""``0`` would read as "retry immediately" and defeat the backoff entirely."""
	assert parse_retry_after("120") == 120.0
	assert parse_retry_after(30) == 30.0
	assert parse_retry_after(None) is None
	assert parse_retry_after("") is None
	assert parse_retry_after("soon-ish") is None
	assert parse_retry_after("-3") is None


def test_parse_retry_after_handles_the_http_date_form_with_an_injected_clock() -> None:
	"""RFC 9110 allows a date, Google has been seen to send one, and the clock is
	injectable so this assertion is not a function of when it runs."""
	# Thu, 01 Jan 1970 00:01:00 GMT is 60 seconds after the epoch.
	assert parse_retry_after("Thu, 01 Jan 1970 00:01:00 GMT", now=0.0) == 60.0
	# A date already in the past floors at zero rather than going negative.
	assert parse_retry_after("Thu, 01 Jan 1970 00:00:10 GMT", now=100.0) == 0.0


# ---------------------------------------------------------------------------
# classify_error — the table, and the one row with its own name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_these_statuses_retry(status: int) -> None:
	assert classify_error(status) is RetryDecision.RETRY


@pytest.mark.parametrize(
	"status",
	[400, 401, 402, 403, 404, 405, 409, 412, 413, 415, 422, 451, 499, 501, 505],
)
def test_every_other_status_fails_fast(status: int) -> None:
	"""Including every 4xx that is not 429, and the 5xx outside the audited set.

	The audited set is a closed frozenset rather than "any 5xx" on purpose: an
	unlisted 5xx is rare enough that surfacing it beats absorbing it.
	"""
	assert classify_error(status) is RetryDecision.FAIL


def test_a_403_is_never_retried_because_retrying_it_hides_a_wrong_identity() -> None:
	"""Named, because this is the row that costs an afternoon when it is wrong.

	``403 PERMISSION_DENIED`` from the Chat API means the impersonated subject is
	wrong, a DWD scope is missing, or the domain-wide-delegation binding was never
	granted. Every one of those is a configuration fault. Retrying it five times
	turns a fast, legible failure into a slow, confusing one and buries the
	diagnosis under four identical log lines.
	"""
	assert classify_error(403) is RetryDecision.FAIL
	assert should_retry(classify_error(403), attempt=0, max_attempts=5) is False


@pytest.mark.parametrize(
	"exception_name",
	["ConnectTimeout", "ReadTimeout", "Timeout", "ConnectionError", "ConnectionResetError"],
)
def test_connection_and_read_timeouts_retry(exception_name: str) -> None:
	assert classify_error(None, _exception_named(exception_name)) is RetryDecision.RETRY


@pytest.mark.parametrize("exception_name", ["SSLError", "SSLCertVerificationError", "TooManyRedirects"])
def test_tls_and_redirect_failures_do_not_retry(exception_name: str) -> None:
	"""``requests.exceptions.SSLError`` subclasses its ``ConnectionError``, so a
	naive MRO scan would retry a certificate failure. The deny-list is checked
	first, and this is what holds that ordering in place."""
	assert classify_error(None, _exception_named(exception_name, ConnectionError)) is RetryDecision.FAIL


def test_no_status_and_no_exception_fails_rather_than_retrying_on_no_evidence() -> None:
	assert classify_error(None, None) is RetryDecision.FAIL


def test_should_retry_spends_the_whole_budget_and_no_more() -> None:
	"""``attempt`` counts attempts already made, so the last permitted one is
	``max_attempts - 1`` and nothing follows it."""
	assert should_retry(RetryDecision.RETRY, attempt=0, max_attempts=3) is True
	assert should_retry(RetryDecision.RETRY, attempt=1, max_attempts=3) is True
	assert should_retry(RetryDecision.RETRY, attempt=2, max_attempts=3) is False
	assert should_retry(RetryDecision.FAIL, attempt=0, max_attempts=3) is False


# ---------------------------------------------------------------------------
# client_message_id — Google's three constraints, against hostile inputs
# ---------------------------------------------------------------------------

#: A name no naming rule would produce, which is the point: the derivation must
#: be legal for inputs nobody anticipated, not merely for the ones they did.
PATHOLOGICAL_NAME = "CHAT-MSG-" + ("Ω_ünïcødé/Ẅørd " * 40) + ("X" * 500)

HOSTILE_NAMES = [
	"CHAT-MSG-00001",
	"chat_msg_with_underscores",
	"Ünïcødé-Ναμε-日本語",
	"name with spaces and / slashes",
	"UPPER.dotted.Name",
	PATHOLOGICAL_NAME,
	"x",
]


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_client_message_id_satisfies_googles_three_constraints(name: str) -> None:
	"""Begins with ``client-``; at most 63 characters; ``[a-z0-9-]`` only.

	Asserted against the *output* rather than reasoned about from the hash
	alphabet, because the value of this test is that it still holds after someone
	changes the derivation.
	"""
	value = client_message_id(name, site=SITE)

	assert value.startswith(CLIENT_MESSAGE_ID_PREFIX)
	assert len(value) <= CLIENT_MESSAGE_ID_MAX_LENGTH
	assert LEGAL_ID_CHARS.match(value)
	assert value == value.lower()


def test_client_message_id_length_is_fixed_and_independent_of_the_input() -> None:
	"""``len("client-") + 32 == 39``. The 63-character cap is unreachable by
	construction, not by luck — a truncating derivation would still pass the cap
	assertion above while silently colliding."""
	lengths = {len(client_message_id(name, site=SITE)) for name in HOSTILE_NAMES}
	assert lengths == {39}


def test_client_message_id_is_deterministic_and_site_scoped() -> None:
	"""Same inputs, same id, forever — and a different site mints a different id.

	The site is in the seed so a staging bench relaying into the same Workspace
	cannot collide with production's ids (ADR 0009 §G.2.2).
	"""
	first = client_message_id("CHAT-MSG-00042", site=SITE)
	second = client_message_id("CHAT-MSG-00042", site=SITE)
	assert first == second
	assert client_message_id("CHAT-MSG-00043", site=SITE) != first
	assert client_message_id("CHAT-MSG-00042", site=OTHER_SITE) != first


def test_distinct_names_do_not_collide_across_a_realistic_batch() -> None:
	ids_seen = {client_message_id(f"CHAT-MSG-{n:06d}", site=SITE) for n in range(5_000)}
	assert len(ids_seen) == 5_000


def test_client_message_id_refuses_an_empty_name_or_an_empty_site() -> None:
	"""An empty name would map every message to one id, which the
	``unique(room, client_message_id)`` index turns into a hard insert failure on
	the *second* message — loud, but only after the relay has misbehaved."""
	with pytest.raises(ValueError, match="erpnext_message_name is required"):
		client_message_id("", site=SITE)
	with pytest.raises(ValueError, match="site is required"):
		client_message_id("CHAT-MSG-00001", site="")


@pytest.mark.parametrize(
	("bad", "reason"),
	[
		("no-prefix-here", "must begin with"),
		(CLIENT_MESSAGE_ID_PREFIX + "A" * 10, "only lowercase"),
		(CLIENT_MESSAGE_ID_PREFIX + "under_score", "only lowercase"),
		(CLIENT_MESSAGE_ID_PREFIX + "a" * 80, "characters"),
	],
)
def test_the_legality_assertion_rejects_what_google_would_reject(bad: str, reason: str) -> None:
	with pytest.raises(ValueError, match=reason):
		assert_legal_client_message_id(bad)


def test_the_transport_re_validates_ids_it_did_not_derive() -> None:
	"""``validate_client_message_id`` is the boundary for manual replays, import
	backfills and any future caller that formats its own id — and it adds the
	transport's context so a relay job's error says which call rejected it."""
	good = client_message_id("CHAT-MSG-00042", site=SITE)
	assert validate_client_message_id(good) == good
	with pytest.raises(ValueError, match="messageId rejected"):
		validate_client_message_id("Client-Uppercase")


def test_is_client_message_id_is_the_cheap_half_of_echo_suppression() -> None:
	"""``True`` means *"go probe the unique index"*, not *"this is certainly ours"*.

	Conflating the two would let anyone suppress ERPNext's ingestion of their own
	messages by guessing the prefix, which is a denial of service rather than a
	defect in the derivation (invariant I3).
	"""
	assert is_client_message_id(client_message_id("CHAT-MSG-1", site=SITE)) is True
	assert is_client_message_id("client-anything-at-all") is True
	assert is_client_message_id("nGKp8sQAAAE") is False
	assert is_client_message_id("") is False
	assert is_client_message_id(None) is False


# ---------------------------------------------------------------------------
# request_id — deterministic, and different per operation
# ---------------------------------------------------------------------------


def test_request_id_is_deterministic() -> None:
	"""Determinism is the whole feature: specifying an existing ``requestId``
	returns the resource created with it, so a retried provisioning job replays
	rather than creating a second space (ADR 0009 §G.2.3)."""
	first = request_id("CHAT-ROOM-0001", "Space Create", site=SITE)
	assert first == request_id("CHAT-ROOM-0001", "Space Create", site=SITE)


def test_request_id_differs_per_operation() -> None:
	"""Two different operations against one room are two different requests. A
	shared id would make ``spaces.setup`` and a later membership write dedupe
	against each other, which is a silently skipped call."""
	room = "CHAT-ROOM-0001"
	minted = {
		request_id(room, operation, site=SITE)
		for operation in ("Space Create", "Space Setup", "Membership Create", "Message Create")
	}
	assert len(minted) == 4


def test_request_id_is_site_scoped_and_reference_scoped() -> None:
	base = request_id("CHAT-ROOM-0001", "Space Setup", site=SITE)
	assert request_id("CHAT-ROOM-0002", "Space Setup", site=SITE) != base
	assert request_id("CHAT-ROOM-0001", "Space Setup", site=OTHER_SITE) != base


def test_request_id_is_uuid_shaped() -> None:
	value = request_id("CHAT-ROOM-0001", "Space Setup", site=SITE)
	assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value)


def test_request_id_refuses_missing_arguments() -> None:
	with pytest.raises(ValueError, match="erpnext_name is required"):
		request_id("", "Space Setup", site=SITE)
	with pytest.raises(ValueError, match="operation is required"):
		request_id("CHAT-ROOM-0001", "", site=SITE)
	with pytest.raises(ValueError, match="site is required"):
		request_id("CHAT-ROOM-0001", "Space Setup", site="")


def test_message_ids_for_returns_the_validated_pair() -> None:
	"""The one helper a caller should reach for, so the coupling between the two
	idempotency keys sits on two lines instead of scattered through the relay."""
	message_id, derived_request_id = message_ids_for("CHAT-MSG-00042", site=SITE)
	assert message_id == client_message_id("CHAT-MSG-00042", site=SITE)
	assert derived_request_id == request_id("CHAT-MSG-00042", "Message Create", site=SITE)
	assert validate_client_message_id(message_id) == message_id


def test_scope_fingerprint_is_order_insensitive() -> None:
	"""The token cache is keyed on ``(subject, sorted(scopes))`` (ADR §E.4.2), so
	a caller that reorders its scope tuple must hit the same entry rather than
	mint a second token for the same grant."""
	a = ("https://www.googleapis.com/auth/chat.messages", "https://www.googleapis.com/auth/chat.spaces")
	assert scope_fingerprint(a) == scope_fingerprint(tuple(reversed(a)))
	assert scope_fingerprint(a) != scope_fingerprint((*a, "https://www.googleapis.com/auth/chat.bot"))


# ---------------------------------------------------------------------------
# Dry-run — deterministic, visibly fake, and the transport is never entered
# ---------------------------------------------------------------------------


def _dry_client(**kwargs: Any) -> GoogleChatClient:
	"""A dry-run client with every runtime dependency injected.

	``settings`` is a plain object so no ``Chat Settings`` read happens, and
	``token_provider`` raises: dry-run must short-circuit *above* the point a
	credential would be needed, and a token provider that could be reached is a
	dry-run that is one refactor from doing I/O.
	"""

	def _no_token(_identity: AuthIdentity, _subject: str | None) -> str:
		raise AssertionError("dry-run must never mint a credential")

	defaults: dict[str, Any] = {
		"subject": "relay.user@example.com",
		"dry_run": True,
		"settings": object(),
		"token_provider": _no_token,
		"correlation_id": "fixedcorrid",
	}
	defaults.update(kwargs)
	return GoogleChatClient(**defaults)


def test_dry_run_never_enters_the_transport(monkeypatch: pytest.MonkeyPatch) -> None:
	"""**Phase 1 acceptance criterion.** Zero calls into ``_request``, for every method.

	Patched on the class, and counted rather than merely refused, so the failure
	message says *how many* times the network was reached. ``_request`` is the
	single choke point that performs I/O — the guarantee being made is "no network
	at all", and a guarantee implemented *inside* the transport would be one
	refactor away from untrue, which is why the dry-run branch sits above it.
	"""
	transport = RecordingTransport()
	monkeypatch.setattr(GoogleChatClient, "_request", transport)

	client = _dry_client()
	message_id, derived_request_id = message_ids_for("CHAT-MSG-00042", site=SITE)

	client.setup_space(
		space_type=SpaceType.SPACE,
		request_id=request_id("CHAT-ROOM-0001", "Space Setup", site=SITE),
		member_emails=["coworker@example.com"],
		display_name="Fountain Crew",
	)
	client.find_direct_message("coworker@example.com")
	client.create_message(SPACE, message_id, derived_request_id, "hello")
	client.patch_message(f"{SPACE}/messages/{message_id}", text="edited")
	client.delete_message(f"{SPACE}/messages/{message_id}")
	client.list_messages(SPACE)
	client.get_message(f"{SPACE}/messages/{message_id}")

	assert transport.calls == [], f"dry-run entered the transport {len(transport.calls)} time(s)"


def test_the_zero_call_assertion_would_actually_catch_a_regression(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""The control for the test above.

	A "zero calls" assertion is worthless unless the same harness records a call
	when one happens. With ``dry_run=False`` the identical client must enter the
	transport exactly once, which proves the counter is wired to the thing being
	counted.
	"""
	transport = RecordingTransport()
	monkeypatch.setattr(GoogleChatClient, "_request", transport)

	client = _dry_client(dry_run=False, token_provider=lambda _i, _s: "FAKE-DO-NOT-USE-token")
	message_id, derived_request_id = message_ids_for("CHAT-MSG-00042", site=SITE)
	client.create_message(SPACE, message_id, derived_request_id, "hello")

	assert len(transport.calls) == 1


def test_dry_run_never_mints_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
	"""``_dry_client``'s token provider raises; reaching it is the failure."""
	monkeypatch.setattr(GoogleChatClient, "_request", RecordingTransport())
	client = _dry_client()
	message_id, derived_request_id = message_ids_for("CHAT-MSG-00042", site=SITE)
	assert client.create_message(SPACE, message_id, derived_request_id, "hi")["dryRun"] is True


def test_dry_run_names_are_deterministic_for_the_same_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Two separate clients, same inputs, byte-identical resource names.

	Phase 2's echo suppression probes ``unique(room, client_message_id)`` and
	``unique(gchat_message_name)``; neither can be exercised end to end against a
	mode that mints a fresh random name per call. A dry-run replay must collide
	exactly where a real replay would.
	"""
	monkeypatch.setattr(GoogleChatClient, "_request", RecordingTransport())
	message_id, derived_request_id = message_ids_for("CHAT-MSG-00042", site=SITE)

	first = _dry_client().create_message(SPACE, message_id, derived_request_id, "hello")
	second = _dry_client().create_message(SPACE, message_id, derived_request_id, "hello")

	assert first["name"] == second["name"]
	assert first["thread"]["name"] == second["thread"]["name"]
	assert first["clientAssignedMessageId"] == message_id


def test_dry_run_space_provisioning_replays_to_the_same_space(monkeypatch: pytest.MonkeyPatch) -> None:
	"""The dry-run proof of the exactly-once property the real ``requestId`` buys:
	two provisioning attempts for one room return the *same* space."""
	monkeypatch.setattr(GoogleChatClient, "_request", RecordingTransport())
	provisioning_id = request_id("CHAT-ROOM-0001", "Space Setup", site=SITE)

	def _setup() -> dict[str, Any]:
		return _dry_client().setup_space(
			space_type=SpaceType.SPACE,
			request_id=provisioning_id,
			member_emails=["coworker@example.com"],
			display_name="Fountain Crew",
		)

	assert _setup()["name"] == _setup()["name"]


def test_dry_run_names_are_visibly_fake(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Every synthetic name carries ``DRYRUN-`` and every payload a ``dryRun`` key.

	Two independent detectors on purpose. A ``Chat Message`` row reading
	``spaces/DRYRUN-…/messages/client-…`` cannot be mistaken for real state by a
	human scanning a list view, and a consumer that never looks at the resource
	name still cannot mistake the payload for real.
	"""
	monkeypatch.setattr(GoogleChatClient, "_request", RecordingTransport())
	message_id, derived_request_id = message_ids_for("CHAT-MSG-00042", site=SITE)
	message = _dry_client().create_message(SPACE, message_id, derived_request_id, "hello")

	assert dryrun_module.DRYRUN_MARKER in message["name"]
	assert dryrun_module.is_dryrun_name(message["name"]) is True
	assert message["dryRun"] is True
	assert message["sender"]["name"] == dryrun_module.DRYRUN_SENDER_NAME
	assert message["createTime"] == dryrun_module.DRYRUN_CREATE_TIME


def test_a_dry_run_write_into_a_real_space_still_yields_a_fake_name(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""The hole this closes is specific and would not be found by inspection.

	Once a room has been provisioned for real its ``gchat_space_name`` is a
	genuine ``spaces/AAAA…``. A dry-run message into that room must **not** hand
	back ``spaces/AAAA…/messages/client-…`` — a name that looks entirely real and
	would be written straight into ``Chat Message.gchat_message_name``, at which
	point Phase 2's reconciliation hunts for a message Google has never heard of.
	"""
	monkeypatch.setattr(GoogleChatClient, "_request", RecordingTransport())
	message_id, derived_request_id = message_ids_for("CHAT-MSG-00042", site=SITE)
	real_space = "spaces/AAAArealspace99"

	message = _dry_client().create_message(real_space, message_id, derived_request_id, "hello")

	assert real_space not in message["name"]
	assert dryrun_module.is_dryrun_name(message["name"]) is True
	# Idempotent: the same real space always folds to the same twin, so create /
	# patch / get agree with each other in dry-run.
	assert dryrun_module.dryrun_space_for(real_space) == dryrun_module.dryrun_space_for(
		dryrun_module.dryrun_space_for(real_space)
	)


def test_dry_run_message_list_is_empty_because_empty_is_the_only_honest_answer(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""A reconciliation sweep run against dry-run must find nothing and conclude
	nothing, rather than find fiction and act on it."""
	monkeypatch.setattr(GoogleChatClient, "_request", RecordingTransport())
	listing = _dry_client().list_messages(SPACE)
	assert listing["messages"] == []
	assert listing["dryRun"] is True


def test_describe_request_logs_body_keys_and_never_body_values() -> None:
	"""Dry-run is where a developer is most tempted to log the whole payload, and
	the payload is employee chat content. Decision #12 audits non-participant
	reads; a log file holding every message routes around that audit entirely."""
	rendered = dryrun_module.describe_request(
		http_method="POST",
		path="/v1/spaces/AAAA/messages",
		query={"messageId": "client-abc"},
		body_keys=("text", "thread"),
	)
	assert "text" in rendered
	assert "body_keys=[text,thread]" in rendered
	assert "messageId=client-abc" in rendered


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------


def test_create_message_requires_message_id_and_request_id_positionally() -> None:
	"""**A ``TypeError``, not a code review comment.**

	The two idempotency keys are positional and required. As optional kwargs with
	defaults they would be omitted the first time somebody writes a quick retry
	path — at which point a replayed relay job posts a second copy of the message
	and inbound echo suppression, which keys on the ``client-`` id, has nothing to
	match. Asserting the ``TypeError`` is asserting that Phase 2 *cannot* skip
	them.
	"""
	client = _dry_client()
	with pytest.raises(TypeError):
		client.create_message(SPACE, "hello")  # type: ignore[call-arg]
	with pytest.raises(TypeError):
		client.create_message(SPACE)  # type: ignore[call-arg]
	with pytest.raises(TypeError):
		build_create_message_call(SPACE, text="hello")  # type: ignore[call-arg]


def test_create_message_enforces_the_client_prefix_at_the_boundary() -> None:
	"""Invariant I3 is only sound if nothing else can put a ``client-`` id into a
	space — and only useful if everything ERPNext sends carries one."""
	valid_request_id = request_id("CHAT-MSG-00042", "Message Create", site=SITE)
	with pytest.raises(ValueError, match="messageId rejected"):
		build_create_message_call(SPACE, "not-prefixed", valid_request_id, "hello")
	with pytest.raises(ValueError, match="messageId rejected"):
		build_create_message_call(SPACE, "client-UPPER", valid_request_id, "hello")


def test_create_message_requires_a_non_empty_request_id() -> None:
	message_id = client_message_id("CHAT-MSG-00042", site=SITE)
	with pytest.raises(ValueError, match="requestId is required"):
		build_create_message_call(SPACE, message_id, "", "hello")


def test_create_message_puts_both_idempotency_keys_in_the_query_string() -> None:
	message_id, derived_request_id = message_ids_for("CHAT-MSG-00042", site=SITE)
	call = build_create_message_call(SPACE, message_id, derived_request_id, "hello")

	assert call.http_method == "POST"
	assert call.path == f"/v1/{SPACE}/messages"
	assert call.query["messageId"] == message_id
	assert call.query["requestId"] == derived_request_id
	assert call.body == {"text": "hello"}
	# No thread was requested, so no reply option may be present: sending one
	# without the other is the silent-new-thread failure ThreadReply exists for.
	assert "messageReplyOption" not in call.query


def test_a_thread_id_cannot_be_passed_without_its_reply_option() -> None:
	"""``MESSAGE_REPLY_OPTION_UNSPECIFIED`` *"ignores any thread ID"* and starts a
	new thread — with **no error**. The mirror loses its thread structure while
	every call returns 200. Binding the two into one value removes the failure by
	construction, and ``create_message`` has no separate thread argument at all.
	"""
	import inspect

	parameters = inspect.signature(GoogleChatClient.create_message).parameters
	assert "thread_name" not in parameters
	assert "message_reply_option" not in parameters

	with pytest.raises(ValueError, match="ignores the thread id"):
		ThreadReply(f"{SPACE}/threads/T1", client_module.MessageReplyOption.UNSPECIFIED)
	with pytest.raises(ValueError, match="requires a thread name"):
		ThreadReply("")

	message_id, derived_request_id = message_ids_for("CHAT-MSG-00042", site=SITE)
	call = build_create_message_call(
		SPACE,
		message_id,
		derived_request_id,
		"hello",
		thread=ThreadReply(f"{SPACE}/threads/T1"),
	)
	assert call.body["thread"] == {"name": f"{SPACE}/threads/T1"}
	assert call.query["messageReplyOption"] == "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"


def test_notification_options_are_refused_under_the_human_identity() -> None:
	"""**CQ-1, resolved 2026-08-08 in favour of human attribution.**

	``createMessageNotificationOptions`` requires *app* authentication, which
	stamps the message as sent by the Chat app and destroys the attribution the
	whole relay exists for. Google Chat's own notification therefore fires for
	native-client users, and that is expected and documented rather than a defect.
	This assertion is what stops the trade-off being silently re-made in Phase 4.
	"""
	message_id, derived_request_id = message_ids_for("CHAT-MSG-00042", site=SITE)
	with pytest.raises(ValueError, match="human attribution"):
		build_create_message_call(
			SPACE,
			message_id,
			derived_request_id,
			"hello",
			identity=AuthIdentity.USER,
			notification_options={"notificationType": "NOTIFICATION_TYPE_SILENT"},
		)

	# Permitted for Triton's app-identity replies, which is the only place it belongs.
	app_call = build_create_message_call(
		SPACE,
		message_id,
		derived_request_id,
		"hello",
		identity=AuthIdentity.APP,
		notification_options={"notificationType": "NOTIFICATION_TYPE_SILENT"},
	)
	assert (
		app_call.query["createMessageNotificationOptions.notificationType"] == "NOTIFICATION_TYPE_SILENT"
	)


@pytest.mark.parametrize(
	("space_type", "display_name", "members", "reason"),
	[
		(SpaceType.SPACE, "", ["a@example.com"], "requires displayName"),
		(SpaceType.GROUP_CHAT, "Named", ["a@example.com", "b@example.com"], "must not set displayName"),
		(SpaceType.GROUP_CHAT, "", ["a@example.com"], "at least 2 memberships"),
		(SpaceType.DIRECT_MESSAGE, "Named", ["a@example.com"], "must not set displayName"),
		(SpaceType.DIRECT_MESSAGE, "", ["a@example.com", "b@example.com"], "exactly 1 membership"),
	],
)
def test_the_three_space_types_are_three_mutually_exclusive_contracts(
	space_type: SpaceType, display_name: str, members: list[str], reason: str
) -> None:
	"""Each violation is a 400 from Google whose text names the wrong thing.

	Refusing locally turns "Invalid argument" into a sentence that says which of
	the three contracts was broken.
	"""
	with pytest.raises(ValueError, match=reason):
		build_setup_space_call(
			space_type=space_type,
			request_id="req-1",
			member_emails=members,
			display_name=display_name or None,
		)


def test_the_three_space_types_each_build_a_legal_body() -> None:
	named = build_setup_space_call(
		space_type=SpaceType.SPACE,
		request_id="req-1",
		member_emails=["a@example.com"],
		display_name="Fountain Crew",
	)
	assert named.body["space"] == {"spaceType": "SPACE", "displayName": "Fountain Crew"}
	assert named.requires_identity is AuthIdentity.USER

	group = build_setup_space_call(
		space_type=SpaceType.GROUP_CHAT,
		request_id="req-2",
		member_emails=["a@example.com", "b@example.com"],
	)
	assert group.body["space"] == {"spaceType": "GROUP_CHAT"}
	assert len(group.body["memberships"]) == 2

	dm = build_setup_space_call(
		space_type=SpaceType.DIRECT_MESSAGE,
		request_id="req-3",
		member_emails=["a@example.com"],
	)
	# Explicitly false: true would mean a human-to-app DM, a different resource.
	assert dm.body["space"] == {"spaceType": "DIRECT_MESSAGE", "singleUserBotDm": False}


def test_the_caller_is_excluded_from_setup_memberships_by_name() -> None:
	"""Including the caller is a 400 that reads as a membership problem rather
	than an off-by-one; naming the address locally is worth the check."""
	with pytest.raises(ValueError, match="calling \\(impersonated\\) user"):
		build_setup_space_call(
			space_type=SpaceType.SPACE,
			request_id="req-1",
			member_emails=["relay.user@example.com", "b@example.com"],
			display_name="Fountain Crew",
			caller_email="Relay.User@example.com",
		)


def test_a_log_record_carries_a_fingerprint_and_never_the_body() -> None:
	"""Chat content is employee-private. What a log line may say about a message
	is its length in characters, its length in **bytes** (Google's 32,000 limit is
	byte-denominated, so an emoji-heavy message is a third of what a character
	count suggests) and a truncated hash — enough to correlate two lines about one
	message, not enough to reconstruct it."""
	message_id, derived_request_id = message_ids_for("CHAT-MSG-00042", site=SITE)
	secret = "the quarterly numbers are 🙃 bad"
	call = build_create_message_call(SPACE, message_id, derived_request_id, secret)

	record = client_module.build_log_record(
		call=call,
		correlation_id="abc123",
		attempt=0,
		status=200,
		latency_ms=12.34,
		dry_run=False,
		identity=AuthIdentity.USER,
	)
	rendered = repr(record)

	assert secret not in rendered
	assert "quarterly" not in rendered
	assert record["text_bytes"] > record["text_length"]
	assert len(record["text_hash"]) == 12
	assert "body" not in record
	assert "query" not in record


def test_bearer_tokens_are_scrubbed_from_anything_bound_for_a_log_or_a_raise() -> None:
	"""``Chat Relay Job.last_error`` is readable by anyone with the DocType, and an
	error body echoed by an intermediate proxy has been known to include the
	request's own ``Authorization`` header."""
	scrubbed = client_module.scrub_secrets(
		"upstream said: Authorization: Bearer ya29.FAKE-DO-NOT-USE-token-material"
	)
	assert "ya29" not in scrubbed
	assert "Bearer [redacted]" in scrubbed


def test_the_retry_policy_module_holds_the_audited_status_set() -> None:
	"""Pinned as a set so widening it is a visible diff rather than a stray
	``or status >= 500``."""
	assert backoff_module.RETRYABLE_STATUS_CODES == frozenset({429, 500, 502, 503, 504})


def test_ids_module_ships_no_inverse_and_that_is_deliberate() -> None:
	"""**Recorded divergence from Appendix B §3, which asked for "derivation + inverse".**

	ADR 0009 §G.2.2 *rejects* the reversible ``"client-" + name.lower()``
	derivation: it is legal only if Frappe's hash alphabet is lowercase
	alphanumeric, which nobody verified, and it breaks silently the day a naming
	rule changes. Nothing needs an inverse — echo suppression asks *"do I have a
	row with this id?"*, which is an index probe. The ADR outranks the appendix,
	so there is no inverse to test; this assertion exists so that adding one is a
	deliberate act with a failing test attached, not a quiet convenience.
	"""
	assert not hasattr(ids_module, "erpnext_name_from_client_message_id")
	assert not hasattr(ids_module, "decode_client_message_id")
