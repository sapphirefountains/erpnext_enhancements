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

Phase 2 adds three blocks and one module. ``events_client`` targets a **second
Google host** and is covered here rather than in a suite of its own, because it
deliberately shares this one's transport: the same ``_request``, the same retry
loop, the same dry-run branch. Splitting the tests would let the two drift
exactly where the design says they cannot.

* **Attachments** — the auth asymmetry is the whole design and it is asserted in
  both directions: ``media.upload`` refuses the app identity because ``chat.bot``
  is absent from its scope list, while ``media.download`` accepts either. A
  builder that silently allowed an app-auth upload would fail as a 403 four steps
  later, reading like a DWD misconfiguration rather than an impossibility.
* **No threading** — ``spaceThreadingState`` is output-only and the API cannot
  create a threaded space, so the assertion is structural: **no** builder in the
  module may grow a thread argument.
* **Subscriptions** — ``ttl`` is input-only and every published ceiling is an
  "up to", so the tests pin the two properties that follow: the field is *omitted*
  by default, and a response without ``expireTime`` raises rather than defaults.
"""

from __future__ import annotations

import hashlib
import importlib
import itertools
import pathlib
import random
import re
import sys
from typing import Any

import pytest

from erpnext_enhancements.chat.gchat import backoff as backoff_module
from erpnext_enhancements.chat.gchat import client as client_module
from erpnext_enhancements.chat.gchat import dryrun as dryrun_module
from erpnext_enhancements.chat.gchat import events_client as events_module
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
	GoogleChatAPIError,
	GoogleChatClient,
	GoogleChatError,
	SpaceType,
	ThreadReply,
	attachment_bytes,
	attachment_content_type,
	build_create_membership_call,
	build_create_message_call,
	build_delete_membership_call,
	build_download_media_call,
	build_get_space_call,
	build_list_members_call,
	build_setup_space_call,
	build_upload_attachment_call,
	chat_target_resource,
	message_ids_for,
	validate_client_message_id,
)
from erpnext_enhancements.chat.gchat.events_client import (
	MESSAGE_EVENT_TYPES,
	SubscriptionExpiryUnknown,
	WorkspaceEventsClient,
	build_create_subscription_call,
	build_delete_subscription_call,
	build_get_subscription_call,
	build_list_subscriptions_call,
	build_patch_subscription_call,
	build_reactivate_subscription_call,
	parse_rfc3339_epoch,
	parse_subscription,
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
MEMBERSHIP = f"{SPACE}/members/105250506097979753968"
PUBSUB_TOPIC = "projects/erpnext-465317/topics/chat-events"
SUBSCRIPTION = "subscriptions/0dc4c8ba-d4c9-4b2b-b0a1-2b1d1f2a3b4c"


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


class FakeResponse:
	"""A ``requests``-shaped response. ``content`` is optional **on purpose**.

	``PHASE2_INTERFACES.md §8`` contracts the fake Chat harness's response double to expose
	``.status_code``, ``.text`` and ``.headers`` — three attributes, no fourth. So the
	binary read path must work against a double that has no ``.content``, and the way to
	assert that is to have a double that genuinely lacks it.
	"""

	def __init__(
		self,
		*,
		status_code: int = 200,
		text: str = "{}",
		content: bytes | None = None,
		headers: dict[str, str] | None = None,
	) -> None:
		self.status_code = status_code
		self.text = text
		self.headers = headers or {}
		if content is not None:
			self.content = content


class FakeSession:
	"""A ``transport=`` double: the seam the fake Chat harness plugs into.

	Injected as ``transport=`` rather than patched over ``_request``, so these tests
	exercise the **real** ``_request`` — the kwargs it chooses, the bytes/JSON fork, the
	header it sets — which is the whole reason the fake harness is a transport double and
	not a client subclass.
	"""

	def __init__(self, *responses: FakeResponse) -> None:
		self.queue = list(responses) or [FakeResponse()]
		self.calls: list[dict[str, Any]] = []

	def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
		self.calls.append({"method": method, "url": url, **kwargs})
		return self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]


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
	"""``backoff``/``ids``/``dryrun``/``client``/``events_client`` import with both blocked.

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
		"erpnext_enhancements.chat.gchat.events_client",
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

	**Phase 2 extends the list rather than adding a second test**, and that is the
	point of the design it is testing. Attachments send raw bytes and subscriptions
	go to a *different host*; both were built to route through this one method, so
	both are covered by this one patch. A second transport would need a second
	assertion, and the day someone forgot to write it the guarantee would be half
	true with nothing to say so.
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

	# Phase 2, same client: spaces, memberships, attachments.
	client.get_space(SPACE)
	client.list_members(SPACE)
	client.create_membership(SPACE, "coworker@example.com")
	client.delete_membership(MEMBERSHIP)
	client.upload_attachment(SPACE, "plan.pdf", content=b"%PDF-1.4 fake", content_type="application/pdf")
	client.download_media("CO4ZjpGVUOWEDgcy")

	# Phase 2, second host, same transport. WorkspaceEventsClient composes the client
	# above rather than owning a socket, so these must be counted by the same patch.
	events = WorkspaceEventsClient(chat_client=client)
	events.create_subscription(target_resource=chat_target_resource(), pubsub_topic=PUBSUB_TOPIC)
	events.validate_subscription(target_resource=chat_target_resource(), pubsub_topic=PUBSUB_TOPIC)
	events.patch_subscription(SUBSCRIPTION)
	events.reactivate_subscription(SUBSCRIPTION)
	events.get_subscription(SUBSCRIPTION)
	events.list_subscriptions(filter_expression=f'event_types:"{MESSAGE_EVENT_TYPES[0]}"')
	events.delete_subscription(SUBSCRIPTION)

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


# ---------------------------------------------------------------------------
# Phase 2: threading is settled, and settled means "no new argument"
# ---------------------------------------------------------------------------


def test_no_builder_in_the_module_takes_a_thread_argument_except_create_message() -> None:
	"""**SETTLED 2026-08-09: the API cannot create a threaded space, so nothing threads.**

	Three independent confirmations closed this: the discovery document marks
	``Space.spaceThreadingState`` ``"readOnly": true``; ``spaces.setup`` states
	*"Spaces with threaded replies aren't supported."*; and ``spaces.patch``'s
	``updateMask`` enumerates every updatable path and does not include it. There
	is no create-time decision to get wrong because there is no way to ask.

	So the assertion is structural rather than about one function. ``ThreadReply``
	stays — it is the right shape for the day the capability exists, and deleting
	it would delete the explanation — but ``build_create_message_call`` is the only
	builder permitted to name it. This test is what makes adding a thread argument
	to the new membership, attachment or space builders a failing test rather than
	a review comment nobody leaves.
	"""
	import inspect

	forbidden = {"thread", "thread_name", "thread_key", "threadkey", "message_reply_option"}
	#: The two that legitimately name a thread, and the reason each is not a write.
	#: ``build_create_message_call`` takes a ``ThreadReply`` and would be the only way to
	#: thread if threading existed; ``build_message_filter`` names ``thread.name``, which is
	#: one of the two fields ``messages.list`` can filter on — reading a thread somebody
	#: else created is unaffected by our inability to create one.
	permitted = {"build_create_message_call": {"thread"}, "build_message_filter": {"thread_name"}}

	offenders: list[str] = []
	for name, function in vars(client_module).items():
		if not name.startswith("build_") or not callable(function):
			continue
		named = set(inspect.signature(function).parameters) & forbidden
		if named - permitted.get(name, set()):
			offenders.append(f"{name}({sorted(named)})")

	assert not offenders, (
		f"these builders take a thread argument: {offenders}. spaceThreadingState is output-only "
		"and spaces.setup refuses threaded spaces, so a thread argument on a WRITE can only be "
		"ignored by Google or silently start a new top-level thread. Read ThreadReply's docstring."
	)

	# And the one that may: still a ThreadReply, still no loose thread id beside it.
	parameters = inspect.signature(client_module.build_create_message_call).parameters
	assert parameters["thread"].annotation == "ThreadReply | None"


def test_the_threading_verify_block_records_the_answer_rather_than_the_question() -> None:
	"""A resolved ``VERIFY:`` left open is worse than none: the next reader re-researches it.

	``ThreadReply``'s docstring is the only place a developer meets this decision, so it
	must say what was settled and what follows — not still be asking. What legitimately
	remains open is *which* ``spaceThreadingState`` an API-created space lands in, and the
	answer to that is "read it back", which is a different kind of statement.
	"""
	doc = ThreadReply.__doc__ or ""
	assert "SETTLED" in doc
	assert "aren't supported" in doc
	assert "read" in doc.lower() and "gchat_threading_state" in doc


# ---------------------------------------------------------------------------
# Phase 2: attachments, and the auth asymmetry that is the whole design
# ---------------------------------------------------------------------------


def test_media_upload_is_user_auth_only_because_chat_bot_is_not_in_its_scopes() -> None:
	"""**Not a policy of ours — a property of the API.**

	``media.upload``'s scope list is exactly ``chat.import``, ``chat.messages`` and
	``chat.messages.create``. ``chat.bot`` — the app-authentication scope — is *absent*, so
	no app-auth token can create an attachment at all. Pinning the identity on the builder
	turns that from a 403 arriving four steps later (which reads like a DWD
	misconfiguration) into a refusal at the point of construction.
	"""
	call = build_upload_attachment_call(SPACE, "plan.pdf", content=b"%PDF-1.4 fake")
	assert call.requires_identity is AuthIdentity.USER

	app_client = GoogleChatClient(
		identity=AuthIdentity.APP,
		dry_run=False,
		settings=object(),
		token_provider=lambda _i, _s: "FAKE-DO-NOT-USE-token",
		transport=FakeSession(),
	)
	with pytest.raises(GoogleChatError, match="requires the USER identity"):
		app_client.upload_attachment(SPACE, "plan.pdf", content=b"%PDF-1.4 fake")


def test_media_download_accepts_either_identity_and_that_asymmetry_is_the_point() -> None:
	"""``media.download``'s scopes *include* ``chat.bot``, unlike ``media.upload``'s.

	The asymmetry is real and it runs in the direction this design needs: ERPNext ingests
	far more attachments than it sends, and the sender of an inbound one is frequently
	somebody we hold no DWD mandate for. Leaving ``requires_identity`` open is what lets a
	sweep download without impersonating anyone.
	"""
	call = build_download_media_call("CO4ZjpGVUOWEDgcy")
	assert call.requires_identity is None
	assert call.expects_binary is True
	assert call.path == "/v1/media/CO4ZjpGVUOWEDgcy"
	assert call.query == {"alt": "media"}


def test_the_upload_is_multipart_with_the_filename_in_its_own_json_part() -> None:
	"""``uploadType=media`` would be simpler and would lose the filename.

	The media-only form carries bytes and nothing else, so the attachment arrives with
	nothing for Chat to title it. The metadata part is what makes ``multipart`` mandatory
	rather than a preference.
	"""
	content = b"%PDF-1.4 quarterly numbers"
	call = build_upload_attachment_call(SPACE, "plan.pdf", content=content, content_type="application/pdf")

	assert call.path == f"/upload/v1/{SPACE}/attachments:upload"
	assert call.query == {"uploadType": "multipart"}
	assert call.content_type.startswith("multipart/related; boundary=")
	assert call.body is None, "an upload must not also carry a JSON body — the transport forks on this"

	boundary = call.content_type.split("boundary=", 1)[1]
	raw = call.raw_body or b""
	assert raw.startswith(f"--{boundary}".encode())
	assert raw.endswith(f"--{boundary}--\r\n".encode())
	assert b'{"filename": "plan.pdf"}' in raw
	assert b"Content-Type: application/pdf" in raw
	assert content in raw


def test_the_upload_boundary_is_deterministic_and_cannot_occur_inside_the_payload() -> None:
	"""Two properties, and both have to hold at once.

	Deterministic, because the builder is a pure function and the pure tier is the only one
	CI protects — a random boundary would make the built payload un-assertable. And absent
	from the content, because a boundary that appears in the body terminates the part early:
	Google receives a truncated file and answers 200, which is the worst combination
	available.
	"""
	content = b"%PDF-1.4 quarterly numbers"
	first = build_upload_attachment_call(SPACE, "plan.pdf", content=content)
	second = build_upload_attachment_call(SPACE, "plan.pdf", content=content)
	assert first.raw_body == second.raw_body

	# Different bytes, different boundary: two uploads of the same filename must not share
	# one, or a fixture built from either would silently validate the other.
	assert (
		build_upload_attachment_call(SPACE, "plan.pdf", content=b"other").content_type != first.content_type
	)

	boundary = first.content_type.split("boundary=", 1)[1]
	assert boundary.encode("ascii") not in content

	# The adversarial case: a file whose bytes contain the boundary the builder would have
	# picked. The loop must move off it rather than produce a truncated upload.
	digest = hashlib.sha256(b"|".join((b"evil.bin", b"placeholder"))).hexdigest()
	hostile = f"chatupload{digest[:24]}".encode()
	call = build_upload_attachment_call(SPACE, "evil.bin", content=hostile)
	chosen = call.content_type.split("boundary=", 1)[1]
	assert chosen.encode("ascii") not in hostile


@pytest.mark.parametrize(
	("filename", "reason"),
	[
		("", "requires a filename"),
		("/private/files/plan.pdf", "no path separators"),
		("..\\..\\secrets.env", "no path separators"),
		("plan\r\n.pdf", "no path separators"),
	],
)
def test_an_upload_filename_may_not_carry_a_path_or_a_control_character(filename: str, reason: str) -> None:
	"""ERPNext ``File`` rows hold a ``file_name`` that is *sometimes* a private path.

	Uploading ``/private/files/….pdf`` publishes this server's directory layout into a Chat
	space as the attachment's visible title, and a CR or LF would be written straight into a
	multipart part header. Rejected rather than stripped: a caller passing either has a bug
	worth surfacing.
	"""
	with pytest.raises(ValueError, match=reason):
		build_upload_attachment_call(SPACE, filename, content=b"x")


def test_a_zero_byte_upload_is_refused_rather_than_sent() -> None:
	"""Google accepts it. Chat then shows an attachment nobody can open, which is
	indistinguishable from a delivery failure at the only moment anyone looks."""
	with pytest.raises(ValueError, match="zero bytes"):
		build_upload_attachment_call(SPACE, "plan.pdf", content=b"")


def test_upload_content_is_a_required_keyword_and_not_an_optional_one() -> None:
	"""**A ``TypeError``, for the same reason ``messageId`` is positional.**

	A multipart body cannot be built without the bytes, and a ``content`` that defaulted to
	empty is how a caller who forgot to read the file uploads nothing and gets a 200.
	"""
	with pytest.raises(TypeError):
		build_upload_attachment_call(SPACE, "plan.pdf")  # type: ignore[call-arg]


@pytest.mark.parametrize(
	"resource",
	["", "/leading-slash", "../../etc/passwd", "has space", "query?alt=media", "frag#ment"],
)
def test_a_download_resource_name_cannot_smuggle_a_different_url(resource: str) -> None:
	"""``resourceName`` is interpolated into a URL path unescaped, because Google's own
	template is ``v1/media/{resourceName=**}`` and the ``**`` means slashes are legitimate.
	So the validator's job is what must *not* get through, not what the charset is."""
	with pytest.raises(ValueError):
		build_download_media_call(resource)


def test_download_returns_bytes_through_the_one_transport_and_never_a_download_uri() -> None:
	"""The real ``_request`` runs here — injected transport, not a patched method.

	``downloadUri`` and ``thumbnailUri`` are human, browser-session URLs and do not accept a
	bearer token; a download built on either works when a developer pastes it into a
	logged-in browser and fails in every background job. The only correct call is
	``GET /v1/media/{resourceName}?alt=media`` with the ``Authorization`` header, which is
	what this asserts by reading the request the transport actually made.
	"""
	png = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
	session = FakeSession(FakeResponse(content=png, headers={"Content-Type": "image/png"}))
	client = GoogleChatClient(
		subject="relay.user@example.com",
		dry_run=False,
		settings=object(),
		token_provider=lambda _i, _s: "FAKE-DO-NOT-USE-token",
		transport=session,
	)

	payload = client.download_media("CO4ZjpGVUOWEDgcy")

	assert attachment_bytes(payload) == png
	assert attachment_content_type(payload) == "image/png"
	sent = session.calls[0]
	assert sent["url"] == "https://chat.googleapis.com/v1/media/CO4ZjpGVUOWEDgcy"
	assert sent["params"] == {"alt": "media"}
	assert sent["headers"]["Authorization"].startswith("Bearer ")
	assert "data" not in sent, "a GET must not carry a raw body"


def test_download_works_against_a_response_double_with_no_content_attribute() -> None:
	"""``PHASE2_INTERFACES.md §8`` contracts the fake harness's response to expose
	``.status_code``, ``.text`` and ``.headers`` — three attributes, no fourth.

	A transport helper that hard-depended on ``.content`` would make the entire download
	path untestable against the fake, silently, and the fake is where the §4.D race and the
	fault injection live. So the fallback is asserted against a double that genuinely lacks
	the attribute rather than one that fakes its absence.
	"""
	session = FakeSession(FakeResponse(text="plain bytes", headers={"Content-Type": "text/plain"}))
	client = GoogleChatClient(
		subject="relay.user@example.com",
		dry_run=False,
		settings=object(),
		token_provider=lambda _i, _s: "FAKE-DO-NOT-USE-token",
		transport=session,
	)
	assert attachment_bytes(client.download_media("CO4ZjpGVUOWEDgcy")) == b"plain bytes"


def test_a_failed_download_still_parses_the_json_error_envelope() -> None:
	"""The byte path is entered on 2xx **only**.

	Chat is served by ESF and answers errors with the modern AIP-193 envelope. Routing a 403
	through the byte path would turn a legible ``PERMISSION_DENIED`` into an unparsed blob
	in ``Chat Relay Job.last_error`` — and 403 is the status this integration produces most
	often while a DWD grant is still being got right.
	"""
	body = (
		'{"error": {"code": 403, "message": "The caller does not have permission", '
		'"status": "PERMISSION_DENIED"}}'
	)
	session = FakeSession(FakeResponse(status_code=403, text=body))
	client = GoogleChatClient(
		subject="relay.user@example.com",
		dry_run=False,
		settings=object(),
		token_provider=lambda _i, _s: "FAKE-DO-NOT-USE-token",
		transport=session,
	)
	with pytest.raises(GoogleChatAPIError, match="PERMISSION_DENIED"):
		client.download_media("CO4ZjpGVUOWEDgcy")


def test_the_upload_sends_raw_bytes_and_the_multipart_content_type_not_json() -> None:
	"""The transport forks on ``raw_body``, and ``data=`` is passed **only** on that fork.

	The fake Chat harness implements ``request(method, url, params, json, headers,
	timeout)``. A ``data=`` that was always present would break every JSON call against it,
	so the two are mutually exclusive by construction and this is what holds that in place.
	"""
	session = FakeSession(FakeResponse(text='{"attachmentDataRef": {"attachmentUploadToken": "t"}}'))
	client = GoogleChatClient(
		subject="relay.user@example.com",
		dry_run=False,
		settings=object(),
		token_provider=lambda _i, _s: "FAKE-DO-NOT-USE-token",
		transport=session,
	)
	client.upload_attachment(SPACE, "plan.pdf", content=b"%PDF-1.4 fake", content_type="application/pdf")

	sent = session.calls[0]
	assert sent["data"].startswith(b"--chatupload")
	assert "json" not in sent
	assert sent["headers"]["Content-Type"].startswith("multipart/related; boundary=")


def test_a_log_record_carries_the_attachment_size_and_never_the_attachment() -> None:
	"""An attachment's bytes are exactly as private as a message body, and there is **no**
	setting that turns them on.

	``Chat Settings.log_message_bodies`` reaches ``call.body``; ``raw_body`` is a different
	field and no logging path in the module touches it. That separation is the reason the
	two are separate fields at all, so the length — which is operationally useful — is what
	the record carries.
	"""
	secret = b"%PDF-1.4 the quarterly numbers are bad"
	call = build_upload_attachment_call(SPACE, "quarterly.pdf", content=secret)
	record = client_module.build_log_record(
		call=call,
		correlation_id="abc123",
		attempt=0,
		status=200,
		latency_ms=12.3,
		dry_run=False,
		identity=AuthIdentity.USER,
	)
	rendered = repr(record)

	assert record["raw_bytes"] == len(call.raw_body or b"")
	assert record["raw_bytes"] > 0
	assert "quarterly numbers" not in rendered
	assert "%PDF" not in rendered
	assert "raw_body" not in record


# ---------------------------------------------------------------------------
# Phase 2: which calls actually spend the space's one write per second
# ---------------------------------------------------------------------------


def test_membership_and_space_creation_do_not_spend_the_per_space_write_budget() -> None:
	"""**PHASE2_VERIFIED.md §2, corrections 1 and 2 — and getting it wrong costs 300×.**

	``members.create`` / ``members.delete`` appear in **no** per-space bucket; they consume
	the project-wide 300-per-minute membership bucket alone. ``spaces.setup`` has no
	per-space bucket either, because the space does not exist yet. Charging either to the
	1-write-per-second space bucket would throttle a bulk org provisioning sweep about 300
	times harder than the API requires — a difference between minutes and most of a day.

	The flag is a field on the call rather than an inference from the HTTP verb precisely
	because all four of these are POSTs and DELETEs that look identical to a verb check.
	"""
	message_id, derived_request_id = message_ids_for("CHAT-MSG-00042", site=SITE)
	spends = {
		"create_message": build_create_message_call(SPACE, message_id, derived_request_id, "hi"),
		"patch_message": client_module.build_patch_message_call(
			f"{SPACE}/messages/{message_id}", text="edited"
		),
		"delete_message": client_module.build_delete_message_call(f"{SPACE}/messages/{message_id}"),
		"upload_attachment": build_upload_attachment_call(SPACE, "plan.pdf", content=b"x"),
	}
	free = {
		"setup_space": build_setup_space_call(
			space_type=SpaceType.SPACE, request_id="req-1", member_emails=["a@example.com"], display_name="X"
		),
		"create_membership": build_create_membership_call(SPACE, "a@example.com"),
		"delete_membership": build_delete_membership_call(MEMBERSHIP),
		"list_members": build_list_members_call(SPACE),
		"get_space": build_get_space_call(SPACE),
	}

	for name, call in spends.items():
		assert call.consumes_space_write_budget is True, f"{name} spends a space write and must say so"
	for name, call in free.items():
		assert call.consumes_space_write_budget is False, (
			f"{name} does not consume the per-space bucket (PHASE2_VERIFIED.md §2). Charging it to "
			"the 1-write-per-second bucket throttles provisioning ~300x harder than necessary."
		)


def test_an_attachment_message_costs_two_of_the_spaces_seconds_not_one() -> None:
	"""``media.upload`` shares the bucket with ``messages.create``, so the relay's cost
	model for one message with one attachment is two seconds of that space's budget. Stated
	as an assertion because it is the number the relay worker's pacing is derived from."""
	message_id, derived_request_id = message_ids_for("CHAT-MSG-00042", site=SITE)
	pair = (
		build_upload_attachment_call(SPACE, "plan.pdf", content=b"x"),
		build_create_message_call(SPACE, message_id, derived_request_id, "see attached"),
	)
	assert sum(1 for call in pair if call.consumes_space_write_budget) == 2


# ---------------------------------------------------------------------------
# Phase 2: spaces and memberships
# ---------------------------------------------------------------------------


def test_membership_writes_are_pinned_to_the_user_identity() -> None:
	"""The app-authenticated form needs ``chat.app.memberships``, a Marketplace
	admin-install scope. PHASE2_CONTRACT.md §2 rules that whole family out — it is not a
	fallback we can reach for — so leaving the identity open would only defer a 403."""
	assert build_create_membership_call(SPACE, "a@example.com").requires_identity is AuthIdentity.USER
	assert build_delete_membership_call(MEMBERSHIP).requires_identity is AuthIdentity.USER
	# Reads are app-capable: a reconciliation sweep should not have to impersonate anyone
	# to ask who is in a space.
	assert build_list_members_call(SPACE).requires_identity is None
	assert build_get_space_call(SPACE).requires_identity is None


def test_a_membership_needs_a_stated_role_and_unspecified_is_not_offered() -> None:
	call = build_create_membership_call(SPACE, "a@example.com", role="ROLE_MANAGER")
	assert call.body == {"member": {"name": "users/a@example.com", "type": "HUMAN"}, "role": "ROLE_MANAGER"}
	with pytest.raises(ValueError, match="role must be one of"):
		build_create_membership_call(SPACE, "a@example.com", role="MEMBERSHIP_ROLE_UNSPECIFIED")


def test_a_membership_name_is_not_an_email_and_is_validated_as_a_resource_name() -> None:
	"""``users/{email}`` is the *request* form for creating a membership; the resource that
	comes back is named differently, and deleting the wrong one is not undone by re-adding —
	a re-added member rejoins with no history."""
	with pytest.raises(ValueError, match="membership resource name"):
		build_delete_membership_call("users/a@example.com")
	with pytest.raises(ValueError, match="membership resource name"):
		build_delete_membership_call(f"{SPACE}/members/one/two")


def test_a_member_list_filter_naming_no_supported_field_is_refused_locally() -> None:
	"""``spaces.members.list`` filters on ``member.type`` and ``role`` and nothing else.

	The failure this catches is a filter copied from ``messages.list``: Google answers 400,
	the sweep logs a failure, and membership quietly stops reconciling. The check is
	coarse on purpose — it asks whether a supported field is named at all, and is not, and
	must not become, a parser.
	"""
	assert "filter" in build_list_members_call(SPACE, filter_expression='role = "ROLE_MANAGER"').query
	with pytest.raises(ValueError, match="filters on"):
		build_list_members_call(SPACE, filter_expression='createTime > "2026-01-01T00:00:00Z"')


def test_the_member_page_size_is_clamped_rather_than_sent() -> None:
	"""A rejected page is a sweep that silently does not run — same reasoning as
	``messages.list``, different limits (100 default, 1000 max, not 25/1000)."""
	assert build_list_members_call(SPACE).query["pageSize"] == "100"
	assert build_list_members_call(SPACE, page_size=99_999).query["pageSize"] == "1000"
	assert build_list_members_call(SPACE, page_size=-5).query["pageSize"] == "1"


def test_a_dry_run_membership_and_attachment_are_visibly_fake() -> None:
	"""Same two independent detectors as every other synthetic resource: a ``DRYRUN-``
	marker in the name and a ``dryRun`` key in the payload. An attachment upload token is
	the one most likely to be stored and later replayed against Google."""
	membership = build_create_membership_call(SPACE, "a@example.com").dry_run_payload or {}
	assert dryrun_module.is_dryrun_name(membership["name"]) is True
	assert membership["dryRun"] is True

	upload = build_upload_attachment_call(SPACE, "plan.pdf", content=b"x").dry_run_payload or {}
	assert dryrun_module.is_dryrun_name(upload["attachmentDataRef"]["attachmentUploadToken"]) is True
	assert upload["dryRun"] is True


def test_a_builder_supplied_dry_run_payload_wins_over_the_kind_dispatch() -> None:
	"""The seam that lets a resource family this module holds no template for stay synthetic
	without teaching ``dry_run_response`` a shape that does not belong to it."""
	call = build_download_media_call("CO4ZjpGVUOWEDgcy")
	assert client_module.dry_run_response(call)["dryRun"] is True
	assert attachment_bytes(client_module.dry_run_response(call)) == b""


# ---------------------------------------------------------------------------
# Phase 2: the Workspace Events subscription client — a second host, one socket
# ---------------------------------------------------------------------------


def test_the_events_client_targets_a_different_host_through_the_same_call_type() -> None:
	"""One socket, two hosts. ``GoogleCall.host`` is what makes that possible without a
	second ``_request``, and a second ``_request`` would double the retry loop, the logging,
	the token handling and the dry-run branch while keeping none of them in step."""
	call = build_create_subscription_call(
		target_resource=chat_target_resource(), event_types=MESSAGE_EVENT_TYPES, pubsub_topic=PUBSUB_TOPIC
	)
	assert call.url.startswith("https://workspaceevents.googleapis.com/")
	assert call.host == events_module.WORKSPACE_EVENTS_HOST
	# The default is unchanged, so no Chat call was affected by adding the field.
	assert build_get_space_call(SPACE).url.startswith("https://chat.googleapis.com/")


def test_the_chat_host_is_composed_by_the_transport_module_not_typed_in_the_events_one() -> None:
	"""The guardrail permits one module per Google host, and a subscription's
	``targetResource`` names the *Chat* host from inside the *events* builder's caller.

	``chat_target_resource()`` is how those two facts coexist: the literal stays in
	``client.py`` and the events module only shape-checks what it is handed.
	"""
	assert chat_target_resource() == "//chat.googleapis.com/spaces/-"
	assert chat_target_resource(SPACE) == f"//chat.googleapis.com/{SPACE}"

	source = pathlib.Path(events_module.__file__).read_text(encoding="utf-8")
	live = source.split('"""', 2)[2] if source.count('"""') >= 2 else source
	assert "chat.googleapis.com" not in live, (
		"events_client.py names the Chat host outside its module docstring. One host per module is "
		"what makes the containment argument checkable; compose the target with "
		"client.chat_target_resource() instead."
	)


def test_ttl_is_omitted_by_default_because_omitted_means_the_maximum() -> None:
	"""**``ttl`` is input-only, and unspecified means "use the maximum possible duration".**

	So the correct request is the one that does not mention it. Asserting the *absence* of
	a key rather than the presence of a value is the only way to pin that: a builder that
	sent ``"ttl": "604800s"`` would look right, would be a request rather than a promise,
	and would quietly cap every subscription at whatever number somebody typed.
	"""
	call = build_create_subscription_call(
		target_resource=chat_target_resource(), event_types=MESSAGE_EVENT_TYPES, pubsub_topic=PUBSUB_TOPIC
	)
	assert "ttl" not in (call.body or {})
	assert (call.body or {})["payloadOptions"] == {"includeResource": False}
	assert (call.body or {})["notificationEndpoint"] == {"pubsubTopic": PUBSUB_TOPIC}
	assert call.query["validateOnly"] == "false"

	# The renewal patch too: same field, same reasoning, and an empty body is correct.
	patch = build_patch_subscription_call(SUBSCRIPTION, ttl=None)
	assert patch.query["updateMask"] == "ttl"
	assert patch.body == {}

	explicit = build_create_subscription_call(
		target_resource=chat_target_resource(),
		event_types=MESSAGE_EVENT_TYPES,
		pubsub_topic=PUBSUB_TOPIC,
		ttl="3600s",
	)
	assert (explicit.body or {})["ttl"] == "3600s"
	with pytest.raises(ValueError, match="protobuf duration"):
		build_create_subscription_call(
			target_resource=chat_target_resource(),
			event_types=MESSAGE_EVENT_TYPES,
			pubsub_topic=PUBSUB_TOPIC,
			ttl="3600",
		)


def test_include_resource_defaults_false_because_it_is_the_only_seven_day_ceiling() -> None:
	"""ADR §G.4.2's binding reason, restated as an assertion.

	``includeResource: false`` is the **only** configuration with a 7-day TTL ceiling — 4
	hours with resource data, 24 hours with resource data *and* domain-wide delegation. The
	24-hour figure raises the ``includeResource: true`` branch and **not** the 7-day one, so
	DWD buys no TTL benefit here at all. The cost of ``false`` is that an event carries a
	resource name and nothing else, which is why every inbound event costs one
	``messages.get``.
	"""
	default = build_create_subscription_call(
		target_resource=chat_target_resource(), event_types=MESSAGE_EVENT_TYPES, pubsub_topic=PUBSUB_TOPIC
	)
	assert (default.body or {})["payloadOptions"]["includeResource"] is False


def test_a_subscription_cannot_be_held_without_the_expiry_google_actually_granted() -> None:
	"""**Never schedule a renewal from a constant.**

	Every published TTL figure is a ceiling — "up to 7 days" — and nothing guarantees the
	server granted one. So the type refuses to exist without an ``expireTime``: there is no
	way to obtain a ``Subscription`` and then discover you have to invent its expiry, which
	is the shape of the bug this closes (a renewal scheduled from
	``Chat Settings.subscription_ttl_seconds``, which is a *request*).
	"""
	with pytest.raises(SubscriptionExpiryUnknown, match="no expireTime"):
		parse_subscription({"name": SUBSCRIPTION, "state": "ACTIVE"})
	with pytest.raises(SubscriptionExpiryUnknown, match="no name"):
		parse_subscription({"expireTime": "2026-08-16T10:00:00Z"})
	with pytest.raises(SubscriptionExpiryUnknown, match="unparseable"):
		parse_subscription({"name": SUBSCRIPTION, "expireTime": "next tuesday"})

	parsed = parse_subscription(
		{"name": SUBSCRIPTION, "expireTime": "2026-08-16T10:00:00Z", "state": "ACTIVE"}
	)
	assert parsed.expire_time == "2026-08-16T10:00:00Z"
	assert parsed.expire_epoch == parse_rfc3339_epoch("2026-08-16T10:00:00Z")


def test_the_operation_envelope_is_unwrapped_and_a_pending_one_is_an_error() -> None:
	"""``create``/``patch``/``reactivate``/``delete`` are declared as returning an
	``Operation``, not the resource.

	In practice a Chat subscription operation comes back ``done: true``, but "in practice"
	is not a contract. An operation that is *not* done must raise rather than yield ``{}``:
	nothing here polls operations, so a caller handed an empty mapping would store a
	subscription with no name and never find it again.
	"""
	envelope = {
		"name": "operations/abc",
		"done": True,
		"response": {"name": SUBSCRIPTION, "expireTime": "2026-08-16T10:00:00Z", "state": "ACTIVE"},
	}
	assert parse_subscription(envelope).name == SUBSCRIPTION

	with pytest.raises(GoogleChatAPIError, match="not done"):
		parse_subscription({"name": "operations/abc", "done": False})
	with pytest.raises(GoogleChatAPIError, match="PERMISSION_DENIED"):
		parse_subscription({"name": "operations/abc", "done": True, "error": {"status": "PERMISSION_DENIED"}})


@pytest.mark.parametrize(
	("stamp", "expected"),
	[
		("2026-08-16T10:00:00Z", 1786874400.0),
		# Nine fractional digits, truncated to six — not rounded, or a renewal could be
		# scheduled a microsecond after the expiry it was derived from.
		("2026-08-16T10:00:00.123456789Z", 1786874400.123456),
		("2026-08-16T11:00:00+01:00", 1786874400.0),
		("2026-08-16T11:00:00+0100", 1786874400.0),
	],
)
def test_expire_time_parsing_survives_nanoseconds_and_offsets(stamp: str, expected: float) -> None:
	"""``datetime.fromisoformat`` accepts 0, 3 or 6 fractional digits and Google emits 9.

	A raise here happens inside the renewal scheduler, which is the one place a crash means
	every subscription silently lapses seven days later. Truncation rather than rounding,
	so a renewal is never scheduled *after* the expiry it was derived from.
	"""
	assert parse_rfc3339_epoch(stamp) == pytest.approx(expected, abs=1e-6)


def test_a_dry_run_subscription_is_born_expired_and_says_so() -> None:
	"""The trap this flag exists for, made explicit.

	``dryrun.py``'s rule is that a plausible-looking timestamp is the field most likely to
	make a fake read as real, so a synthetic ``expireTime`` is the Unix epoch. The
	consequence is that a dry-run subscription is *already expired*, and a renewal scheduler
	that did not check would renew it forever at one Workspace Events call per pass.
	"""
	call = build_create_subscription_call(
		target_resource=chat_target_resource(), event_types=MESSAGE_EVENT_TYPES, pubsub_topic=PUBSUB_TOPIC
	)
	subscription = parse_subscription(call.dry_run_payload)
	assert subscription.is_dry_run is True
	assert subscription.expire_epoch == 0.0
	assert dryrun_module.is_dryrun_name(subscription.name) is True


def test_lifecycle_event_types_are_delivered_not_subscribed_to() -> None:
	"""Naming one in ``eventTypes`` is a 400 whose message does not say why.

	They arrive on the same topic regardless — ``expirationReminder`` at T−12h and T−1h,
	then ``suspended`` and ``expired`` — and their payload uses ``snake_case`` inside the
	subscription object, unlike every Chat resource. Both facts shape the consumer, not the
	subscription.
	"""
	with pytest.raises(ValueError, match="lifecycle event"):
		build_create_subscription_call(
			target_resource=chat_target_resource(),
			event_types=[events_module.EVENT_TYPE_EXPIRATION_REMINDER],
			pubsub_topic=PUBSUB_TOPIC,
		)


@pytest.mark.parametrize(
	("kwargs", "reason"),
	[
		({"target_resource": "spaces/-"}, "targetResource must be"),
		({"target_resource": "//chat.example.com/spaces/-"}, "targetResource must be"),
		({"pubsub_topic": "chat-events"}, "pubsubTopic must be"),
		({"pubsub_topic": PUBSUB_TOPIC.replace("topics", "subscriptions")}, "pubsubTopic must be"),
		({"event_types": []}, "at least one event type"),
		({"event_types": ["message.created"]}, "namespaced"),
	],
)
def test_the_subscription_builder_refuses_what_google_would_reject(
	kwargs: dict[str, Any], reason: str
) -> None:
	"""Each of these is a 400 whose text names the wrong thing, and two of them are worse
	than a 400: a topic that is really a *subscription* name, and the interaction topic
	pasted where the events topic belongs, both accept the create and then deliver into the
	wrong pipeline."""
	call_kwargs: dict[str, Any] = {
		"target_resource": chat_target_resource(),
		"event_types": MESSAGE_EVENT_TYPES,
		"pubsub_topic": PUBSUB_TOPIC,
	}
	call_kwargs.update(kwargs)
	with pytest.raises(ValueError, match=reason):
		build_create_subscription_call(**call_kwargs)


def test_duplicate_event_types_are_dropped_and_the_callers_order_is_kept() -> None:
	"""Order preserved so a diff of two subscriptions reads the way the caller wrote them;
	duplicates dropped because a repeated event type is a paste, not an intent."""
	call = build_create_subscription_call(
		target_resource=chat_target_resource(),
		event_types=[MESSAGE_EVENT_TYPES[1], MESSAGE_EVENT_TYPES[0], MESSAGE_EVENT_TYPES[1]],
		pubsub_topic=PUBSUB_TOPIC,
	)
	assert (call.body or {})["eventTypes"] == [MESSAGE_EVENT_TYPES[1], MESSAGE_EVENT_TYPES[0]]


def test_listing_subscriptions_without_a_filter_is_refused_locally() -> None:
	"""The API marks ``filter`` Required and wants at least one event type. Enforcing it
	here turns a bare 400 into a sentence, in a code path (orphan detection across the
	roster) whose failure mode is "reconciliation quietly did nothing"."""
	with pytest.raises(ValueError, match="requires a filter"):
		build_list_subscriptions_call()
	assert build_list_subscriptions_call(filter_expression='event_types:"x"').query["filter"] == (
		'event_types:"x"'
	)


def test_every_subscription_call_is_user_authenticated() -> None:
	"""Shape B targets ``spaces/-``, which is user-auth only. Shape A needs Marketplace
	admin-install approval for ``chat.app.*`` and shape C needs Developer Preview plus an
	Enterprise SKU, so neither is a fallback — pinning the identity turns an impossibility
	into a local refusal instead of a 403 that reads like a scope problem."""
	calls = (
		build_create_subscription_call(
			target_resource=chat_target_resource(),
			event_types=MESSAGE_EVENT_TYPES,
			pubsub_topic=PUBSUB_TOPIC,
		),
		build_patch_subscription_call(SUBSCRIPTION, ttl=None),
		build_reactivate_subscription_call(SUBSCRIPTION),
		build_get_subscription_call(SUBSCRIPTION),
		build_list_subscriptions_call(filter_expression='event_types:"x"'),
		build_delete_subscription_call(SUBSCRIPTION),
	)
	for call in calls:
		assert call.requires_identity is AuthIdentity.USER, call.client_method


def test_validate_only_is_a_separate_method_so_create_can_always_return_a_subscription() -> None:
	"""The ``validateOnly`` preflight is what settles whether a DWD-impersonated user may
	create a ``spaces/-`` subscription at all — the docs say the target supports user
	authentication and DWD *is* user authentication, but no page states the combination and
	the whole inbound design rests on it.

	It returns the raw response, because a validated non-creation has no expiry to read and
	forcing one would defeat the type's entire purpose.
	"""
	transport = RecordingTransport()
	client = _dry_client()
	events = WorkspaceEventsClient(chat_client=client)

	validated = events.validate_subscription(
		target_resource=chat_target_resource(), pubsub_topic=PUBSUB_TOPIC
	)
	assert isinstance(validated, dict)
	assert validated["dryRun"] is True

	created = events.create_subscription(target_resource=chat_target_resource(), pubsub_topic=PUBSUB_TOPIC)
	assert isinstance(created, events_module.Subscription)
	assert transport.calls == []


def test_a_subscription_name_cannot_smuggle_a_second_path_segment() -> None:
	for bad in ("", "0dc4c8ba", "subscriptions/a/b", "subscriptions/../spaces"):
		with pytest.raises(ValueError, match="subscription resource name"):
			build_get_subscription_call(bad)


def test_deleting_an_already_deleted_subscription_is_only_a_success_when_asked_for() -> None:
	"""``allowMissing`` off by default: a retried teardown wants it on, and a reconciliation
	sweep deleting something it did not expect to be missing should still say so."""
	assert "allowMissing" not in build_delete_subscription_call(SUBSCRIPTION).query
	assert build_delete_subscription_call(SUBSCRIPTION, allow_missing=True).query["allowMissing"] == "true"


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
