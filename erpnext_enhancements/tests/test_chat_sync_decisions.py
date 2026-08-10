# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for ``chat/sync/decisions.py`` — **the decision matrix IS the spec**.

Plain pytest functions, not ``TestCase`` classes, so this file needs its **own**
``python -m pytest`` step in ``ci.yml``. ``python -m unittest`` silently collects zero
function-style tests and reports success; this repo has already shipped two suites that ran
nowhere for weeks because of exactly that, so the style is not a preference.

No ``frappe`` stub is installed, deliberately.
:func:`test_decisions_imports_with_frappe_and_requests_unavailable` blocks both imports and
re-imports the module from scratch, because every other assertion here rests on the module
staying pure. The moment a module-scope ``import frappe`` appears in ``decisions.py``, the only
CI tier with automatic regression protection stops being able to reach the echo rules at all.

Why the matrix rather than prose
--------------------------------
:data:`DECISION_MATRIX` enumerates every reachable combination of
*(event type × room known × stored × tombstoned × client-id present × local hit × in flight ×
fetched × budget)* with its expected verdict and the reason. **That table is the
specification** — a prose description of the rules is not, because prose cannot be checked
against the code and drifts the first time somebody adds a rung.
:func:`test_classify_inbound_is_total_and_never_suppresses_a_stranger` then runs the full cross
product, including the combinations no caller should ever produce, and asserts the invariants
that hold across all of them. The table says what; the cross product says *nothing else*.

The direction the bugs run
--------------------------
A duplicated message is visible and gets reported. **A suppressed message is not.** A
coworker's message that never reaches ERPNext leaves no row, no log line and no complaint, and
is discovered — if ever — weeks later by somebody reconstructing a conversation. So the
suppression direction gets named tests of its own:
:func:`test_a_foreign_message_is_never_suppressed_by_an_unrelated_in_flight_claim` and
:func:`test_two_identical_short_messages_seconds_apart_are_ambiguous_not_bound`.

``VERIFY:`` the Pub/Sub payloads below are **inline and hand-built to the shape recorded in
PHASE2_VERIFIED.md §7**, not captured from the live API. ``chat/testing/fixtures.py`` — the
byte-shaped fixture module — is written by a sibling task and did not exist when this suite was
written. When it lands, re-point the parsing tests at it: a parser tested only against the
author's own idea of the payload is a parser tested against itself.
"""

from __future__ import annotations

import base64
import importlib
import itertools
import json
import sys
from dataclasses import dataclass
from typing import Any

import pytest

from erpnext_enhancements.chat.gchat.ids import is_client_message_id
from erpnext_enhancements.chat.sync.decisions import (
	ECHO_REASON_AMBIGUOUS,
	ECHO_REASON_INSUFFICIENT_EVIDENCE,
	ECHO_REASON_MATCHED,
	ECHO_REASON_NO_CANDIDATES,
	ECHO_REASON_NO_MATCH,
	ECHO_REASON_WINDOW_CLOSED,
	ECHO_WINDOW_SECONDS_DEFAULT,
	EVENT_MESSAGE_BATCH_CREATED,
	EVENT_MESSAGE_BATCH_DELETED,
	EVENT_MESSAGE_BATCH_UPDATED,
	EVENT_MESSAGE_CREATED,
	EVENT_MESSAGE_DELETED,
	EVENT_MESSAGE_UPDATED,
	EVENT_SUBSCRIPTION_EXPIRATION_REMINDER,
	EVENT_SUBSCRIPTION_EXPIRED,
	EVENT_SUBSCRIPTION_SUSPENDED,
	EchoCandidate,
	InboundFacts,
	InboundVerdict,
	MalformedEvent,
	classify_inbound,
	fanout_events,
	idempotency_key,
	is_lifecycle_event_type,
	is_message_event_type,
	parse_pubsub_envelope,
	select_echo_candidate,
	singular_event_type,
)

# ---------------------------------------------------------------------------
# Fixed values, so a failure names the same thing every time
# ---------------------------------------------------------------------------

ROOM = "CHAT-ROOM-00001"
SPACE = "spaces/AAAAmoUb1234"
RESOURCE = f"{SPACE}/messages/nqRyKXo3d0k.nqRyKXo3d0k"
OTHER_RESOURCE = f"{SPACE}/messages/Xn3PpQr7t1a.Xn3PpQr7t1a"
THIRD_RESOURCE = f"{SPACE}/messages/Zz9WwEe5r2b.Zz9WwEe5r2b"

STORED = "CHAT-MSG-STORED"
LOCAL = "CHAT-MSG-LOCAL"

#: Shaped exactly as ``ids.client_message_id`` produces: ``client-`` plus 32 hex characters.
OURS = "client-" + ("9f2c" * 8)
#: ``client-`` prefixed but not one of ours — the id another Chat app could legally set. It is
#: still "candidate echo" shaped, which is the whole point of I3's two halves.
FOREIGN_CLIENT_SHAPED = "client-" + ("abcd" * 8)
#: Not ``client-`` prefixed at all: a plain third-party id.
FOREIGN = "some-other-apps-id"

#: A membership event — real, subscribed to elsewhere, and not this classifier's business.
EVENT_MEMBERSHIP_CREATED = "google.workspace.chat.membership.v1.created"

CE_ID = f"{SPACE}/spaceEvents/AAAAmoUb1234.eventid"
CE_SUBJECT = f"//chat.googleapis.com/{SPACE}"
CE_SOURCE = "//workspaceevents.googleapis.com/subscriptions/f9b1c0b2-1c1a-4a1a-9d1e-000000000001"
SUBSCRIPTION = "subscriptions/f9b1c0b2-1c1a-4a1a-9d1e-000000000001"
PUBLISH_TIME = "2026-08-09T18:04:11.123Z"


def _facts(**overrides: Any) -> InboundFacts:
	"""A baseline :class:`InboundFacts` with the named fields replaced.

	The production dataclass deliberately has **no defaults** — a caller that forgets the
	``gchat_message_name`` probe must get a ``TypeError``, not a plausible wrong answer — so the
	convenience lives here, in the tests, where forgetting a field is not a production bug.
	"""
	base: dict[str, Any] = {
		"event_type": EVENT_MESSAGE_CREATED,
		"space_name": SPACE,
		"resource_name": RESOURCE,
		"room": ROOM,
		"stored_message": None,
		"stored_is_deleted": False,
		"client_message_id": None,
		"resource_fetched": False,
		"local_by_client_id": None,
		"in_flight_claims": 0,
		"attempt": 0,
		"defer_budget": 2,
	}
	base.update(overrides)
	return InboundFacts(**base)


# ---------------------------------------------------------------------------
# Purity — the precondition everything else rests on
# ---------------------------------------------------------------------------


def test_decisions_imports_with_frappe_and_requests_unavailable() -> None:
	"""``chat.sync.decisions`` must import with both blocked.

	CI installs neither on this job. ``sys.modules[name] = None`` is the documented way to make
	``import name`` raise ``ImportError``, so this reproduces the CI environment rather than
	approximating it. ``gchat.ids`` is pulled in as well because ``decisions`` imports it at
	module scope — that edge is exactly as load-bearing as the module itself.

	If this fails, the fix is **not** a stub: it is to move the offending import inside the
	function that needs it, the way ``client.py`` already does.
	"""
	modules = (
		"erpnext_enhancements.chat.gchat.ids",
		"erpnext_enhancements.chat.sync.decisions",
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
			if module is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = module


# ---------------------------------------------------------------------------
# The decision matrix. This table is the specification.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
	"""One line of the specification: the facts, the verdict, and why it is that verdict."""

	case: str
	overrides: dict[str, Any]
	expected: InboundVerdict
	why: str


DECISION_MATRIX: tuple[Row, ...] = (
	# -- routing: what this classifier does not own ---------------------------------------
	Row(
		"lifecycle-expiration-reminder-is-not-a-message-event",
		{"event_type": EVENT_SUBSCRIPTION_EXPIRATION_REMINDER},
		InboundVerdict.IGNORE,
		"Lifecycle events arrive on the SAME topic as resource events. They belong to the "
		"subscription renewer; parsing one for a message resource would find nothing.",
	),
	Row(
		"membership-created-is-not-a-message-event",
		{"event_type": EVENT_MEMBERSHIP_CREATED},
		InboundVerdict.IGNORE,
		"Membership reconciliation is its own pipeline (§4.H). IGNORE here means 'not mine'.",
	),
	Row(
		"an-unrecognised-event-type-is-never-guessed-at",
		{"event_type": "google.workspace.chat.something.v9.invented"},
		InboundVerdict.IGNORE,
		"Google shipped eleven Chat API changes in the seven months to 2026-08-07. A new "
		"event type must do nothing, not be coerced into the nearest handler.",
	),
	Row(
		"a-space-we-do-not-mirror",
		{"room": None},
		InboundVerdict.IGNORE,
		"The unique(gchat_space_name) probe missed. Checked before any rung that could write, "
		"so an un-mirrored space cannot create rows by any path.",
	),
	Row(
		"a-blank-resource-name-addresses-nothing",
		{"resource_name": ""},
		InboundVerdict.IGNORE,
		"parse_pubsub_envelope refuses to emit one, so this guards a caller that built facts "
		"by hand. With includeResource=false the name is the event's only content.",
	),
	# -- batched types normalise rather than vanish ----------------------------------------
	Row(
		"batch-created-normalises-to-created",
		{"event_type": EVENT_MESSAGE_BATCH_CREATED, "stored_message": STORED},
		InboundVerdict.DUPLICATE,
		"One delivery can carry N resources. If the caller's fan-out and this classifier "
		"disagreed, 'unknown type => IGNORE' would silently eat a burst of real traffic.",
	),
	Row(
		"batch-updated-normalises-to-updated",
		{
			"event_type": EVENT_MESSAGE_BATCH_UPDATED,
			"stored_message": STORED,
			"resource_fetched": True,
		},
		InboundVerdict.NEW,
		"Same reason, and it also proves the batch type does not fall into the created-only "
		"DUPLICATE rung.",
	),
	Row(
		"batch-deleted-normalises-to-deleted",
		{"event_type": EVENT_MESSAGE_BATCH_DELETED, "stored_message": STORED},
		InboundVerdict.NEW,
		"NEW on a deleted means 'apply the tombstone' — the verdict is provenance, the "
		"operation is the event type.",
	),
	# -- created ---------------------------------------------------------------------------
	Row(
		"created-unknown-must-be-fetched-first",
		{},
		InboundVerdict.RESOLVE_VIA_GET,
		"THE NORMAL PATH. includeResource=false buys the 7-day TTL and costs one "
		"messages.get per event. Deleting this rung does not save a call; it removes the only "
		"source of the clientAssignedMessageId the echo check needs.",
	),
	Row(
		"created-unknown-fetches-even-with-a-write-in-flight",
		{"in_flight_claims": 1},
		InboundVerdict.RESOLVE_VIA_GET,
		"The get is strictly more informative than a defer, so it comes first. Deferring "
		"before fetching would spend the budget without learning anything.",
	),
	Row(
		"created-already-stored-is-a-redelivery",
		{"stored_message": STORED},
		InboundVerdict.DUPLICATE,
		"Pub/Sub is at-least-once by contract. Structural dedupe on unique(gchat_message_name) "
		"is success, not an error — ack it.",
	),
	Row(
		"created-for-a-tombstoned-name-never-resurrects",
		{"stored_message": STORED, "stored_is_deleted": True},
		InboundVerdict.TOMBSTONED,
		"Terminal. Google's tombstone is metadata-only, so the ERPNext row holds the last copy "
		"of the body; re-creating would produce a second, empty message.",
	),
	Row(
		"created-tombstone-outranks-even-our-own-echo",
		{
			"stored_message": STORED,
			"stored_is_deleted": True,
			"client_message_id": OURS,
			"local_by_client_id": LOCAL,
			"resource_fetched": True,
		},
		InboundVerdict.TOMBSTONED,
		"The tombstone rung sits above the echo rung on purpose: whatever the provenance, a "
		"buried row stays buried.",
	),
	Row(
		"created-echo-with-the-client-id-already-in-hand",
		{"client_message_id": OURS, "local_by_client_id": LOCAL},
		InboundVerdict.ECHO,
		"A caller holding the id (the interaction transport, or a future includeResource=true "
		"deployment) is not sent to buy one. Bind the name; do not insert, notify or relay.",
	),
	Row(
		"created-echo-after-the-get",
		{"client_message_id": OURS, "local_by_client_id": LOCAL, "resource_fetched": True},
		InboundVerdict.ECHO,
		"The real deployment's path: RESOLVE_VIA_GET first, then this on the second pass.",
	),
	Row(
		"created-client-id-we-cannot-find-alarms-rather-than-guessing",
		{"client_message_id": OURS, "resource_fetched": True},
		InboundVerdict.ECHO_ORPHAN,
		"An id that looks like ours with no row behind it is a broken invariant. Guessing a "
		"binding here is how a real message gets eaten.",
	),
	Row(
		"created-orphan-does-not-wait-for-a-fetch-it-does-not-need",
		{"client_message_id": OURS},
		InboundVerdict.ECHO_ORPHAN,
		"Same rung, before the fetch: the probe already answered.",
	),
	Row(
		"created-a-client-shaped-id-that-is-not-ours-is-still-only-a-candidate",
		{"client_message_id": FOREIGN_CLIENT_SHAPED, "resource_fetched": True},
		InboundVerdict.ECHO_ORPHAN,
		"'Looks like ours' and 'is ours' are different claims and only the index knows. Any "
		"Chat client may set a client- id, so the prefix alone can never suppress — it alarms.",
	),
	Row(
		"created-a-third-party-id-is-not-an-echo-candidate-at-all",
		{"client_message_id": FOREIGN, "resource_fetched": True},
		InboundVerdict.NEW,
		"No client- prefix, so I3 does not apply and the message is a stranger's.",
	),
	Row(
		"created-fetched-with-no-client-id-is-a-genuine-foreign-message",
		{"resource_fetched": True},
		InboundVerdict.NEW,
		"A human typing in the native Chat client. The common case, and it must be cheap.",
	),
	Row(
		"created-foreign-with-a-write-in-flight-is-deferred-not-suppressed",
		{"resource_fetched": True, "in_flight_claims": 1, "attempt": 0, "defer_budget": 2},
		InboundVerdict.DEFER,
		"We cannot yet tell an unbound echo from a stranger, so we wait — bounded, and it "
		"expires into NEW. A delay, never a suppression.",
	),
	Row(
		"created-foreign-becomes-new-once-the-defer-budget-is-spent",
		{"resource_fetched": True, "in_flight_claims": 1, "attempt": 2, "defer_budget": 2},
		InboundVerdict.NEW,
		"THE FALSE-POSITIVE DIRECTION. A coworker's message must arrive late rather than "
		"never; eating it would be invisible and unrecoverable.",
	),
	Row(
		"created-foreign-with-deferral-disabled",
		{"resource_fetched": True, "in_flight_claims": 1, "defer_budget": 0},
		InboundVerdict.NEW,
		"defer_budget=0 turns the rung off entirely, which is what a site that would rather "
		"have duplicates than latency sets.",
	),
	Row(
		"created-foreign-with-nothing-in-flight",
		{"resource_fetched": True, "in_flight_claims": 0, "defer_budget": 2},
		InboundVerdict.NEW,
		"No open claim, no ambiguity, no reason to wait.",
	),
	# -- updated ---------------------------------------------------------------------------
	Row(
		"updated-unknown-name-must-be-fetched",
		{"event_type": EVENT_MESSAGE_UPDATED},
		InboundVerdict.RESOLVE_VIA_GET,
		"Rule 1: an updated for an unknown gchat_message_name is applied as a CREATE from the "
		"fetched resource — so the fetch is mandatory, not optional.",
	),
	Row(
		"updated-known-name-still-needs-the-body-and-the-timestamp",
		{"event_type": EVENT_MESSAGE_UPDATED, "stored_message": STORED},
		InboundVerdict.RESOLVE_VIA_GET,
		"The event carries neither the new text nor lastUpdateTime, and Rule 3's conflict "
		"resolution needs the timestamp. A stored row does NOT make an edit a duplicate.",
	),
	Row(
		"updated-known-name-applies-the-edit",
		{"event_type": EVENT_MESSAGE_UPDATED, "stored_message": STORED, "resource_fetched": True},
		InboundVerdict.NEW,
		"The single most dangerous mis-rung in this module: DUPLICATE here would drop EVERY "
		"inbound edit, silently, forever.",
	),
	Row(
		"updated-our-own-patch-coming-back-body-unchanged",
		{
			"event_type": EVENT_MESSAGE_UPDATED,
			"stored_message": STORED,
			"resource_fetched": True,
			"client_message_id": OURS,
			"local_by_client_id": LOCAL,
			"body_matches_stored": True,
		},
		InboundVerdict.ECHO,
		"Relaying an ERPNext edit calls messages.patch, and Google emits an `updated` for it. "
		"The body is what identifies it: identical text on the same row means there is nothing "
		"to apply, so consuming it avoids a revision row with equal before/after, a wasted "
		"digest rebuild, and a `modified` bump that busts every cached digest for the room.",
	),
	Row(
		"updated-coworker-edited-our-message-in-the-native-client",
		{
			"event_type": EVENT_MESSAGE_UPDATED,
			"stored_message": STORED,
			"resource_fetched": True,
			"client_message_id": OURS,
			"local_by_client_id": LOCAL,
			"body_matches_stored": False,
		},
		InboundVerdict.NEW,
		"THE regression this row exists for. A clientAssignedMessageId is written once and "
		"lives forever, so it says ERPNext AUTHORED the message -- never that ERPNext caused "
		"THIS event. Returning ECHO on the strength of it would drop every inbound edit of "
		"every message we ever sent, in silence, and Rule 3 could never fire on the rows it "
		"was written for.",
	),
	Row(
		"updated-our-message-body-not-compared-is-not-an-echo",
		{
			"event_type": EVENT_MESSAGE_UPDATED,
			"stored_message": STORED,
			"resource_fetched": True,
			"client_message_id": OURS,
			"local_by_client_id": LOCAL,
			"body_matches_stored": None,
		},
		InboundVerdict.NEW,
		"`None` means the caller did not compare, and unknown must never read as 'matches'. "
		"Applying an edit twice is idempotent; suppressing one loses it forever.",
	),
	Row(
		"updated-for-a-tombstoned-name-never-resurrects",
		{"event_type": EVENT_MESSAGE_UPDATED, "stored_message": STORED, "stored_is_deleted": True},
		InboundVerdict.TOMBSTONED,
		"A late edit of a message the user already deleted must not bring it back.",
	),
	Row(
		"updated-unknown-name-is-applied-as-a-create",
		{"event_type": EVENT_MESSAGE_UPDATED, "resource_fetched": True},
		InboundVerdict.NEW,
		"Rule 1 again, now at the terminal rung: out-of-order delivery must not lose content.",
	),
	Row(
		"updated-on-a-bound-row-ignores-an-unrelated-in-flight-claim",
		{
			"event_type": EVENT_MESSAGE_UPDATED,
			"stored_message": STORED,
			"resource_fetched": True,
			"in_flight_claims": 1,
		},
		InboundVerdict.NEW,
		"Once gchat_message_name is stored the row is bound, so any open claim for the room "
		"belongs to some other message. Deferring here would be latency for nothing.",
	),
	Row(
		"updated-unbound-with-a-write-in-flight-defers",
		{"event_type": EVENT_MESSAGE_UPDATED, "resource_fetched": True, "in_flight_claims": 1},
		InboundVerdict.DEFER,
		"Unbound and ambiguous: the create this edit belongs to may be the write in flight.",
	),
	# -- deleted ---------------------------------------------------------------------------
	Row(
		"deleted-known-name-applies-the-tombstone",
		{"event_type": EVENT_MESSAGE_DELETED, "stored_message": STORED},
		InboundVerdict.NEW,
		"The stored row is the TARGET of the delete, not evidence of a replay.",
	),
	Row(
		"deleted-already-tombstoned-is-idempotent",
		{"event_type": EVENT_MESSAGE_DELETED, "stored_message": STORED, "stored_is_deleted": True},
		InboundVerdict.TOMBSTONED,
		"Terminal and re-entrant: a redelivered delete changes nothing and errors nothing.",
	),
	Row(
		"deleted-unknown-name-is-recorded-ignored",
		{"event_type": EVENT_MESSAGE_DELETED},
		InboundVerdict.IGNORE,
		"ADR §G.8 Rule 1, verbatim. There is nothing to delete, and a delete never creates.",
	),
	Row(
		"deleted-unknown-name-defers-while-a-write-is-in-flight",
		{"event_type": EVENT_MESSAGE_DELETED, "in_flight_claims": 1, "attempt": 0},
		InboundVerdict.DEFER,
		"The create it belongs to may be seconds behind. Bounded, then Ignored.",
	),
	Row(
		"deleted-unknown-name-ignores-once-the-budget-is-spent",
		{
			"event_type": EVENT_MESSAGE_DELETED,
			"in_flight_claims": 1,
			"attempt": 2,
			"defer_budget": 2,
		},
		InboundVerdict.IGNORE,
		"The bound has to actually bind, or DEFER is an infinite loop wearing a budget.",
	),
	Row(
		"deleted-a-message-we-can-name-by-client-id-is-applied",
		{
			"event_type": EVENT_MESSAGE_DELETED,
			"client_message_id": OURS,
			"local_by_client_id": LOCAL,
		},
		InboundVerdict.NEW,
		"Naming the row by client id is naming it. A delete can arrive before the create echo "
		"bound gchat_message_name, and IGNORE would discard a real deletion of a row we are "
		"holding and can identify. Our OWN relayed delete never reaches here -- is_deleted is "
		"set before the relay, so rung 4 answers TOMBSTONED first.",
	),
	Row(
		"deleted-orphaned-client-id-alarms",
		{"event_type": EVENT_MESSAGE_DELETED, "client_message_id": OURS},
		InboundVerdict.ECHO_ORPHAN,
		"Same alarm as every other orphan. Never a silent bind.",
	),
)


@pytest.mark.parametrize("row", DECISION_MATRIX, ids=[row.case for row in DECISION_MATRIX])
def test_decision_matrix(row: Row) -> None:
	"""Each row of the specification, asserted."""
	verdict = classify_inbound(_facts(**row.overrides))
	assert verdict is row.expected, f"{row.case}: expected {row.expected}, got {verdict}.\n{row.why}"


def test_the_matrix_covers_every_verdict() -> None:
	"""A table with an unreachable verdict is a table that has stopped describing the code."""
	covered = {row.expected for row in DECISION_MATRIX}
	missing = set(InboundVerdict) - covered
	assert not missing, f"DECISION_MATRIX never produces {sorted(v.value for v in missing)}."


def test_the_matrix_has_no_duplicate_case_names() -> None:
	"""Duplicate ids make a failure report ambiguous, which is when it matters most."""
	names = [row.case for row in DECISION_MATRIX]
	assert len(names) == len(set(names)), "DECISION_MATRIX case names must be unique."


# ---------------------------------------------------------------------------
# Totality and the invariants that hold across the whole cross product
# ---------------------------------------------------------------------------

_ALL_EVENT_TYPES = (EVENT_MESSAGE_CREATED, EVENT_MESSAGE_UPDATED, EVENT_MESSAGE_DELETED)
_ALL_CLIENT_IDS = (None, OURS, FOREIGN)


def test_classify_inbound_is_total_and_never_suppresses_a_stranger() -> None:
	"""Every combination — including the ones no caller should build — obeys the invariants.

	The matrix above says what each reachable case decides. This says that **nothing else can
	happen**: no exception, no drift between two calls with the same facts, and no path to
	``ECHO`` that did not go through the ``unique(room, client_message_id)`` probe.

	That last one is the load-bearing invariant of the whole phase. ``ECHO`` is the only verdict
	that discards a message, so if it can be reached without an index hit then some sequence of
	unrelated facts — a busy room, a redelivery, a slow relay — silently deletes a coworker's
	message. It is asserted over the full cross product rather than a sample because the
	dangerous combinations are exactly the ones nobody thinks to write down.
	"""
	combinations = itertools.product(
		_ALL_EVENT_TYPES,
		(ROOM, None),  # room
		(None, STORED),  # stored_message
		(False, True),  # stored_is_deleted
		_ALL_CLIENT_IDS,
		(False, True),  # resource_fetched
		(None, LOCAL),  # local_by_client_id
		(0, 1),  # in_flight_claims
		(0, 2),  # attempt
		(0, 2),  # defer_budget
	)

	seen: set[InboundVerdict] = set()
	checked = 0
	for (
		event_type,
		room,
		stored_message,
		stored_is_deleted,
		client_message_id,
		resource_fetched,
		local_by_client_id,
		in_flight_claims,
		attempt,
		defer_budget,
	) in combinations:
		facts = _facts(
			event_type=event_type,
			room=room,
			stored_message=stored_message,
			stored_is_deleted=stored_is_deleted,
			client_message_id=client_message_id,
			resource_fetched=resource_fetched,
			local_by_client_id=local_by_client_id,
			in_flight_claims=in_flight_claims,
			attempt=attempt,
			defer_budget=defer_budget,
		)
		verdict = classify_inbound(facts)
		context = f"facts={facts} verdict={verdict}"
		checked += 1
		seen.add(verdict)

		assert isinstance(verdict, InboundVerdict), context
		assert classify_inbound(facts) is verdict, f"not deterministic: {context}"

		looks_like_ours = is_client_message_id(facts.client_message_id)

		if verdict is InboundVerdict.ECHO:
			assert looks_like_ours, f"ECHO without a client- id: {context}"
			assert facts.local_by_client_id, f"ECHO without an index hit: {context}"
		if verdict is InboundVerdict.ECHO_ORPHAN:
			assert looks_like_ours, f"ECHO_ORPHAN without a client- id: {context}"
			assert not facts.local_by_client_id, f"ECHO_ORPHAN despite an index hit: {context}"
		if verdict is InboundVerdict.TOMBSTONED:
			assert facts.stored_message and facts.stored_is_deleted, context
		if verdict is InboundVerdict.DUPLICATE:
			assert facts.stored_message and not facts.stored_is_deleted, context
			assert (
				singular_event_type(facts.event_type) == EVENT_MESSAGE_CREATED
			), f"DUPLICATE on an edit or a delete would drop it: {context}"
		if verdict is InboundVerdict.DEFER:
			assert facts.stored_message is None, f"deferred a bound row: {context}"
			assert facts.in_flight_claims > 0, f"deferred with nothing in flight: {context}"
			assert facts.attempt < facts.defer_budget, f"deferred past the budget: {context}"
		if verdict is InboundVerdict.RESOLVE_VIA_GET:
			assert not facts.resource_fetched, f"fetching twice: {context}"
			assert (
				singular_event_type(facts.event_type) != EVENT_MESSAGE_DELETED
			), f"a deleted resource cannot be fetched: {context}"
		if verdict is InboundVerdict.NEW:
			assert facts.room, f"ingesting into an un-mirrored space: {context}"
			# NEW means "act on this event", NOT "insert a row" -- so a message we authored
			# reaching NEW is correct for a mutation and catastrophic for a create.
			#
			# The invariant used to be a flat `not looks_like_ours`, which reads as caution
			# and is actually the bug: it forced every `updated` naming one of our own
			# messages to a suppressing verdict, which would have dropped every inbound edit
			# of every message ERPNext ever sent. Narrowed to the claim that is actually true.
			if looks_like_ours:
				assert singular_event_type(facts.event_type) != EVENT_MESSAGE_CREATED, (
					f"a CREATE carrying our own client id is an echo, never an insert: {context}"
				)
			# Either we fetched the resource, or it is a delete of a row we can already name.
			# A deleted resource cannot be fetched -- messages.get answers NOT_FOUND -- so the
			# delete path is the one exemption, and "can name" is `gchat_message_name` bound
			# OR the client-id probe hit. Both identify exactly one local row.
			assert facts.resource_fetched or (
				singular_event_type(facts.event_type) == EVENT_MESSAGE_DELETED
				and (facts.stored_message or facts.local_by_client_id)
			), f"ingesting without the resource: {context}"
		if not facts.room:
			assert verdict is InboundVerdict.IGNORE, f"acted on an un-mirrored space: {context}"

	assert checked == 3 * 2 * 2 * 2 * 3 * 2 * 2 * 2 * 2 * 2
	assert seen == set(
		InboundVerdict
	), f"the cross product never produced {sorted(v.value for v in set(InboundVerdict) - seen)}."


# ---------------------------------------------------------------------------
# The four cases the phase brief calls out, by name
# ---------------------------------------------------------------------------


def test_echo_with_the_client_id_present_writes_no_row_and_relays_nothing() -> None:
	"""Case 1. The id is in hand, the index hit, so this is definitionally our own message.

	The assertion that matters is what ``ECHO`` is *not*: it is not ``NEW`` (no insert, no
	``notify_new_message``) and it is not ``RESOLVE_VIA_GET`` (no second read spent on a
	question already answered).
	"""
	verdict = classify_inbound(_facts(client_message_id=OURS, local_by_client_id=LOCAL))
	assert verdict is InboundVerdict.ECHO
	assert verdict is not InboundVerdict.NEW
	assert verdict is not InboundVerdict.RESOLVE_VIA_GET


def test_echo_with_the_client_id_absent_resolves_via_get_then_echoes() -> None:
	"""Case 2, and the deployment's actual path.

	``includeResource: false`` means the event carries a resource name and nothing else, so the
	first pass cannot answer and must fetch. The second pass, holding the fetched
	``clientAssignedMessageId``, suppresses.

	``VERIFY:`` whether ``messages.get`` actually populates ``clientAssignedMessageId`` is
	**unproven** (PHASE2_VERIFIED.md §8.1) — the proto declares it OPTIONAL, not OUTPUT_ONLY,
	and no Google document shows it populated in a response. If it returns empty, this ladder
	collapses to :func:`select_echo_candidate` and the second pass below becomes the
	no-client-id case instead. One live round trip settles it.
	"""
	first = _facts()
	assert classify_inbound(first) is InboundVerdict.RESOLVE_VIA_GET

	second = _facts(resource_fetched=True, client_message_id=OURS, local_by_client_id=LOCAL)
	assert classify_inbound(second) is InboundVerdict.ECHO


def test_a_foreign_message_is_never_suppressed_by_an_unrelated_in_flight_claim() -> None:
	"""Case 3, **the false-positive direction, and the worst bug this module could have.**

	A coworker types in the native Chat client at the moment the relay happens to be writing
	something else to that space. The room has an in-flight claim; the inbound message has no
	client id. Nothing about those two facts connects them.

	A suppression bug here eats real messages. It is worse than a duplicate and far harder to
	notice: a duplicate is reported by the first person who sees it, while a message that never
	arrives leaves no row, no log line and no complaint. So the assertion is not merely "the
	verdict is NEW" but "the verdict is never ECHO at any point on the way there" — deferral is
	allowed to cost latency and is not allowed to cost the message.
	"""
	verdicts = [
		classify_inbound(_facts(resource_fetched=True, in_flight_claims=1, attempt=attempt, defer_budget=2))
		for attempt in range(6)
	]

	assert InboundVerdict.ECHO not in verdicts, "an unrelated in-flight claim suppressed a stranger"
	assert InboundVerdict.DUPLICATE not in verdicts
	assert verdicts[:2] == [InboundVerdict.DEFER, InboundVerdict.DEFER], "the delay is bounded"
	assert set(verdicts[2:]) == {InboundVerdict.NEW}, "and it terminates in NEW, every time"


@pytest.mark.parametrize("event_type", [EVENT_MESSAGE_CREATED, EVENT_MESSAGE_UPDATED])
def test_a_tombstoned_name_is_never_resurrected(event_type: str) -> None:
	"""Case 4. A later create or update for a buried name stays buried.

	Two reasons it is terminal rather than merely idempotent. Google's tombstone is rich in
	metadata and **empty of content** (ADR §F.6.5), so the ERPNext row is the only copy of what
	was said — a resurrection would produce a second, textless message and lose nothing loudly.
	And the delete may have been a deliberate act by the author; re-creating it silently
	overrides a person's decision.
	"""
	facts = _facts(event_type=event_type, stored_message=STORED, stored_is_deleted=True)
	assert classify_inbound(facts) is InboundVerdict.TOMBSTONED

	# Even with our own id and an index hit — the tombstone rung sits above the echo rung.
	with_echo = _facts(
		event_type=event_type,
		stored_message=STORED,
		stored_is_deleted=True,
		resource_fetched=True,
		client_message_id=OURS,
		local_by_client_id=LOCAL,
	)
	assert classify_inbound(with_echo) is InboundVerdict.TOMBSTONED


# ---------------------------------------------------------------------------
# select_echo_candidate — the bounded fallback, and the anti-test it satisfies
# ---------------------------------------------------------------------------


def _candidate(
	name: str,
	*,
	sender: str = "alice@example.com",
	text_hash: str = "a" * 64,
	created_epoch: float = 1_000_000.0,
	sync_state: str = "Pending",
) -> EchoCandidate:
	return EchoCandidate(
		message=name,
		sender=sender,
		text_hash=text_hash,
		created_epoch=created_epoch,
		sync_state=sync_state,
	)


def test_two_identical_short_messages_seconds_apart_are_ambiguous_not_bound() -> None:
	"""**This test is the resolution of the ADR/brief conflict, and it belongs to both.**

	The Phase 2 brief forbids heuristic echo correlation outright and writes this exact
	scenario as its anti-test: two identical short messages seconds apart, where any
	correlation heuristic binds one of them and swaps two people's attribution with nothing to
	notice. ADR §G.3.2 nonetheless permits a bounded heuristic, because the residue it covers —
	an older build, a manual API call, an import backfill — is otherwise duplicated forever.

	Neither side has to give way. **Refusing ambiguity satisfies both**: two "ok" messages
	inside the window produce two matches, so the function binds nothing, the caller treats the
	message as ``NEW``, and the alarm fires. The brief's objection is answered
	deterministically rather than by argument, and the cost is a visible duplicate instead of
	an invisible mis-attribution.

	If a later change makes this return a name, it has re-introduced precisely the defect both
	documents were arguing about.
	"""
	ok_hash = "e0" * 32
	candidates = [
		_candidate("CHAT-MSG-0001", text_hash=ok_hash, created_epoch=1_000_000.0),
		_candidate("CHAT-MSG-0002", text_hash=ok_hash, created_epoch=1_000_003.0),
	]

	name, reason = select_echo_candidate(
		candidates,
		sender="alice@example.com",
		text_hash=ok_hash,
		inbound_epoch=1_000_004.0,
		window_seconds=ECHO_WINDOW_SECONDS_DEFAULT,
	)

	assert name is None, "two identical messages must never bind one of them"
	assert reason == ECHO_REASON_AMBIGUOUS


def test_exactly_one_candidate_inside_the_window_binds() -> None:
	"""The residue the ADR kept the fallback for. One match, and it is unambiguous."""
	name, reason = select_echo_candidate(
		[_candidate("CHAT-MSG-0001")],
		sender="ALICE@example.com",
		text_hash=("A" * 64),
		inbound_epoch=1_000_030.0,
		window_seconds=ECHO_WINDOW_SECONDS_DEFAULT,
	)
	assert (name, reason) == ("CHAT-MSG-0001", ECHO_REASON_MATCHED)


def test_the_same_row_offered_twice_is_one_candidate_not_an_ambiguity() -> None:
	"""A join that returned a row twice must not be mistaken for two competing messages."""
	same = _candidate("CHAT-MSG-0001")
	name, reason = select_echo_candidate(
		[same, same],
		sender="alice@example.com",
		text_hash="a" * 64,
		inbound_epoch=1_000_000.0,
		window_seconds=ECHO_WINDOW_SECONDS_DEFAULT,
	)
	assert (name, reason) == ("CHAT-MSG-0001", ECHO_REASON_MATCHED)


def test_a_candidate_outside_the_window_never_binds() -> None:
	"""Time-bounded, symmetrically: clock skew runs both ways and the window is an abs()."""
	for offset in (-ECHO_WINDOW_SECONDS_DEFAULT - 1.0, ECHO_WINDOW_SECONDS_DEFAULT + 1.0):
		name, reason = select_echo_candidate(
			[_candidate("CHAT-MSG-0001", created_epoch=1_000_000.0)],
			sender="alice@example.com",
			text_hash="a" * 64,
			inbound_epoch=1_000_000.0 + offset,
			window_seconds=ECHO_WINDOW_SECONDS_DEFAULT,
		)
		assert (name, reason) == (None, ECHO_REASON_NO_MATCH)


def test_a_near_miss_body_never_binds_because_only_an_exact_hash_counts() -> None:
	"""Exact normalised hash, never a similarity.

	A substring or similarity test is the shape that already produced a live defect in this
	stack, where a source filter made *"Pond A"* match inside *"Pond Alpha"*. One character of
	difference must be a total miss.
	"""
	name, reason = select_echo_candidate(
		[_candidate("CHAT-MSG-0001", text_hash="a" * 63 + "b")],
		sender="alice@example.com",
		text_hash="a" * 64,
		inbound_epoch=1_000_000.0,
		window_seconds=ECHO_WINDOW_SECONDS_DEFAULT,
	)
	assert (name, reason) == (None, ECHO_REASON_NO_MATCH)


def test_a_different_sender_never_binds() -> None:
	name, reason = select_echo_candidate(
		[_candidate("CHAT-MSG-0001", sender="bob@example.com")],
		sender="alice@example.com",
		text_hash="a" * 64,
		inbound_epoch=1_000_000.0,
		window_seconds=ECHO_WINDOW_SECONDS_DEFAULT,
	)
	assert (name, reason) == (None, ECHO_REASON_NO_MATCH)


@pytest.mark.parametrize("sync_state", ["Relayed", "Inbound", "Not Mirrored", ""])
def test_only_an_unrelayed_row_can_be_an_unbound_echo(sync_state: str) -> None:
	"""A ``Relayed`` row already has its resource name, so structural dedupe answered first."""
	name, reason = select_echo_candidate(
		[_candidate("CHAT-MSG-0001", sync_state=sync_state)],
		sender="alice@example.com",
		text_hash="a" * 64,
		inbound_epoch=1_000_000.0,
		window_seconds=ECHO_WINDOW_SECONDS_DEFAULT,
	)
	assert (name, reason) == (None, ECHO_REASON_NO_MATCH)


@pytest.mark.parametrize(("sender", "text_hash"), [("", "a" * 64), ("alice@example.com", ""), ("", "")])
def test_an_empty_sender_or_hash_refuses_before_looking_at_a_single_candidate(
	sender: str, text_hash: str
) -> None:
	"""Matching on an empty hash would match everything — the suppression bug in one line."""
	name, reason = select_echo_candidate(
		[_candidate("CHAT-MSG-0001", sender=sender or "alice@example.com", text_hash=text_hash or "a" * 64)],
		sender=sender,
		text_hash=text_hash,
		inbound_epoch=1_000_000.0,
		window_seconds=ECHO_WINDOW_SECONDS_DEFAULT,
	)
	assert (name, reason) == (None, ECHO_REASON_INSUFFICIENT_EVIDENCE)


def test_a_non_positive_window_is_not_a_bound() -> None:
	"""A window that cannot exclude anything is a heuristic without its bound."""
	name, reason = select_echo_candidate(
		[_candidate("CHAT-MSG-0001")],
		sender="alice@example.com",
		text_hash="a" * 64,
		inbound_epoch=1_000_000.0,
		window_seconds=0.0,
	)
	assert (name, reason) == (None, ECHO_REASON_WINDOW_CLOSED)


def test_no_candidates_says_so_rather_than_no_match() -> None:
	"""The two are different incidents: nothing to compare against, versus nothing matched."""
	name, reason = select_echo_candidate(
		[],
		sender="alice@example.com",
		text_hash="a" * 64,
		inbound_epoch=1_000_000.0,
		window_seconds=ECHO_WINDOW_SECONDS_DEFAULT,
	)
	assert (name, reason) == (None, ECHO_REASON_NO_CANDIDATES)


# ---------------------------------------------------------------------------
# idempotency_key
# ---------------------------------------------------------------------------


def test_created_and_deleted_keys_ignore_the_timestamp() -> None:
	"""Those operations are idempotent against a resource, so a redelivery must collapse."""
	for event_type in (EVENT_MESSAGE_CREATED, EVENT_MESSAGE_DELETED):
		assert idempotency_key(event_type, RESOURCE, None) == idempotency_key(
			event_type, RESOURCE, "2026-08-09T18:04:11.123456Z"
		)


def test_an_updated_key_changes_with_last_update_time() -> None:
	"""Two edits of one message are two operations.

	Collapsing them is how a stale redelivery overwrites a newer edit — the exact hazard ADR
	§G.8 Rule 3 exists to prevent.
	"""
	first = idempotency_key(EVENT_MESSAGE_UPDATED, RESOURCE, "2026-08-09T18:04:11.123456Z")
	second = idempotency_key(EVENT_MESSAGE_UPDATED, RESOURCE, "2026-08-09T18:07:02.000000Z")
	assert first != second


def test_the_three_event_kinds_never_collide_on_one_resource() -> None:
	keys = {
		idempotency_key(EVENT_MESSAGE_CREATED, RESOURCE, None),
		idempotency_key(EVENT_MESSAGE_UPDATED, RESOURCE, None),
		idempotency_key(EVENT_MESSAGE_DELETED, RESOURCE, None),
	}
	assert len(keys) == 3


def test_two_resources_never_collide() -> None:
	assert idempotency_key(EVENT_MESSAGE_CREATED, RESOURCE, None) != idempotency_key(
		EVENT_MESSAGE_CREATED, OTHER_RESOURCE, None
	)


def test_a_batched_type_keys_the_same_as_its_singular() -> None:
	"""Fan-out must not change the key, or a batched redelivery re-applies everything."""
	assert idempotency_key(EVENT_MESSAGE_BATCH_CREATED, RESOURCE, None) == idempotency_key(
		EVENT_MESSAGE_CREATED, RESOURCE, None
	)


def test_the_key_is_short_legible_and_column_safe() -> None:
	"""It lands in a Data column and in a Redis key; nothing may make its length Google's call."""
	key = idempotency_key(EVENT_MESSAGE_CREATED, RESOURCE, None)
	assert key.startswith("created:")
	assert len(key) <= 64
	assert RESOURCE not in key, "the resource name is hashed, not embedded"


# ---------------------------------------------------------------------------
# parse_pubsub_envelope — two nested layers
# ---------------------------------------------------------------------------


def _b64(payload: Any) -> str:
	return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def _envelope(
	event_type: str,
	payload: Any,
	*,
	ce_id: str = CE_ID,
	ce_subject: str = CE_SUBJECT,
	ce_source: str = CE_SOURCE,
	data: Any = None,
) -> dict[str, Any]:
	"""The outer Pub/Sub envelope, shaped per PHASE2_VERIFIED.md §7.

	Note the two layers both calling their payload "message": the outer one is the Pub/Sub
	message, the inner one is the Chat Message resource. Conflating them is the classic bug and
	the reason these tests assert on both.
	"""
	message: dict[str, Any] = {
		"attributes": {
			"ce-specversion": "1.0",
			"ce-type": event_type,
			"ce-source": ce_source,
			"ce-id": ce_id,
			"ce-subject": ce_subject,
			"ce-datacontenttype": "application/json",
		},
		"messageId": "12977544156041",
		"orderingKey": "",
		"publishTime": PUBLISH_TIME,
	}
	message["data"] = _b64(payload) if data is None else data
	return {"message": message, "subscription": "projects/erpnext-465317/subscriptions/chat-events-sub"}


@pytest.mark.parametrize("event_type", [EVENT_MESSAGE_CREATED, EVENT_MESSAGE_UPDATED, EVENT_MESSAGE_DELETED])
def test_a_singular_message_event_parses_to_its_resource_name(event_type: str) -> None:
	"""With ``includeResource: false`` the name is the entire content, so it is the whole test."""
	parsed = parse_pubsub_envelope(_envelope(event_type, {"message": {"name": RESOURCE}}))

	assert parsed.event_type == event_type
	assert parsed.resource_name == RESOURCE
	assert parsed.resource_names == (RESOURCE,)
	assert parsed.space_name == SPACE
	assert parsed.event_id == CE_ID
	assert parsed.subject == CE_SUBJECT
	assert parsed.publish_time == PUBLISH_TIME
	assert parsed.is_lifecycle is False
	assert parsed.subscription_name == ""
	assert parsed.raw == {"message": {"name": RESOURCE}}
	assert parsed.is_batch is False


def test_a_batched_delivery_keeps_every_name_and_fans_out() -> None:
	"""One row is one delivery, not one resource — and every contained name must survive it."""
	payload = {
		"messages": [
			{"message": {"name": RESOURCE}},
			{"message": {"name": OTHER_RESOURCE}},
			{"message": {"name": THIRD_RESOURCE}},
		]
	}
	parsed = parse_pubsub_envelope(_envelope(EVENT_MESSAGE_BATCH_CREATED, payload))

	assert parsed.is_batch is True
	assert parsed.resource_names == (RESOURCE, OTHER_RESOURCE, THIRD_RESOURCE)

	fanned = fanout_events(parsed)
	assert [event.resource_name for event in fanned] == [RESOURCE, OTHER_RESOURCE, THIRD_RESOURCE]
	assert {event.event_type for event in fanned} == {EVENT_MESSAGE_CREATED}
	assert all(event.space_name == SPACE for event in fanned)
	assert all(not event.is_batch for event in fanned)


def test_fanning_out_a_singular_event_returns_it_unchanged() -> None:
	"""So the worker has one code path rather than a branch it will forget to write."""
	parsed = parse_pubsub_envelope(_envelope(EVENT_MESSAGE_CREATED, {"message": {"name": RESOURCE}}))
	assert fanout_events(parsed) == (parsed,)


@pytest.mark.parametrize(
	"event_type",
	[EVENT_SUBSCRIPTION_EXPIRATION_REMINDER, EVENT_SUBSCRIPTION_SUSPENDED, EVENT_SUBSCRIPTION_EXPIRED],
)
def test_the_three_lifecycle_types_are_recognised_and_routed_apart(event_type: str) -> None:
	"""Lifecycle events arrive on the SAME topic as resource events (PHASE2_VERIFIED.md §7).

	They carry no space and no message, so a parser that treated them as resource events would
	raise ``MalformedEvent`` on every expiry reminder — and the reminders are the only warning
	before a subscription lapses and inbound sync stops with nothing in any log.

	Their payload uses **snake_case** inside the subscription object, unlike the Chat resources.
	"""
	payload = {
		"subscription": {
			"name": SUBSCRIPTION,
			"target_resource": "//chat.googleapis.com/spaces/-",
			"event_types": [EVENT_MESSAGE_CREATED],
			"expire_time": "2026-08-16T18:04:11.123456Z",
			"state": "ACTIVE",
		}
	}
	parsed = parse_pubsub_envelope(_envelope(event_type, payload, ce_subject=CE_SOURCE))

	assert parsed.is_lifecycle is True
	assert parsed.subscription_name == SUBSCRIPTION
	assert parsed.resource_name == SUBSCRIPTION
	assert parsed.space_name == ""
	assert is_lifecycle_event_type(parsed.event_type)
	assert not is_message_event_type(parsed.event_type)


def test_a_lifecycle_event_falls_back_to_ce_source_for_the_subscription() -> None:
	"""``VERIFY:`` the lifecycle payload's container key is unproven, so the parser must not
	depend on its spelling. ``ce-source`` is the documented
	``//<authority>/subscriptions/{id}`` and is the backstop."""
	parsed = parse_pubsub_envelope(
		_envelope(EVENT_SUBSCRIPTION_EXPIRED, {"unexpected_wrapper": {"state": "EXPIRED"}})
	)
	assert parsed.subscription_name == SUBSCRIPTION


def test_the_space_comes_from_the_resource_name_before_any_attribute() -> None:
	"""Precedence matters: the resource name is documented, ``ce-subject`` is not."""
	parsed = parse_pubsub_envelope(
		_envelope(
			EVENT_MESSAGE_CREATED,
			{"message": {"name": RESOURCE}},
			ce_id="",
			ce_subject="//chat.googleapis.com/spaces/WRONGSPACE",
		)
	)
	assert parsed.space_name == SPACE


def test_the_space_falls_back_to_ce_id_then_ce_subject() -> None:
	"""A resource type whose name is not space-prefixed must still route somewhere sane."""
	from_ce_id = parse_pubsub_envelope(
		_envelope(
			"google.workspace.chat.reaction.v1.created",
			{"reaction": {"name": "reactions/abc123"}},
			ce_subject="",
		)
	)
	assert from_ce_id.space_name == SPACE

	from_subject = parse_pubsub_envelope(
		_envelope(
			"google.workspace.chat.reaction.v1.created",
			{"reaction": {"name": "reactions/abc123"}},
			ce_id="",
		)
	)
	assert from_subject.space_name == SPACE


def test_a_membership_event_parses_even_though_the_classifier_ignores_it() -> None:
	"""Parsing and classification are different jobs.

	The parser must not be the thing that decides what we care about, or adding a subscription
	target later means editing a base64 decoder.
	"""
	parsed = parse_pubsub_envelope(
		_envelope(
			EVENT_MEMBERSHIP_CREATED,
			{"membership": {"name": f"{SPACE}/members/106712345678901234567"}},
		)
	)
	assert parsed.space_name == SPACE
	assert parsed.resource_name == f"{SPACE}/members/106712345678901234567"
	assert not is_message_event_type(parsed.event_type)


def test_data_may_arrive_already_decoded_as_a_mapping_or_as_bytes() -> None:
	"""Three shapes, and the distinction is not arbitrary.

	A **str** is base64 — the REST push/pull envelope. **bytes** are the decoded payload, which
	is what a Pub/Sub client library hands over. A **Mapping** is the in-process fake harness.
	"""
	payload = {"message": {"name": RESOURCE}}

	as_mapping = parse_pubsub_envelope(_envelope(EVENT_MESSAGE_CREATED, payload, data=payload))
	as_bytes = parse_pubsub_envelope(
		_envelope(EVENT_MESSAGE_CREATED, payload, data=json.dumps(payload).encode("utf-8"))
	)
	as_base64 = parse_pubsub_envelope(_envelope(EVENT_MESSAGE_CREATED, payload))

	assert as_mapping.resource_name == as_bytes.resource_name == as_base64.resource_name == RESOURCE


def test_unpadded_base64_still_decodes() -> None:
	"""Padding gets stripped by intermediaries often enough to be worth tolerating."""
	payload = {"message": {"name": RESOURCE}}
	parsed = parse_pubsub_envelope(_envelope(EVENT_MESSAGE_CREATED, payload, data=_b64(payload).rstrip("=")))
	assert parsed.resource_name == RESOURCE


@pytest.mark.parametrize(
	("case", "envelope"),
	[
		("not-a-mapping", "just a string"),
		("no-outer-message", {"subscription": "projects/x/subscriptions/y"}),
		("outer-message-is-not-an-object", {"message": "nope"}),
		("no-attributes", {"message": {"data": _b64({"message": {"name": RESOURCE}})}}),
		(
			"no-ce-type",
			{
				"message": {
					"attributes": {"ce-id": CE_ID},
					"data": _b64({"message": {"name": RESOURCE}}),
				}
			},
		),
		(
			"no-data",
			{"message": {"attributes": {"ce-type": EVENT_MESSAGE_CREATED, "ce-id": CE_ID}}},
		),
	],
	ids=lambda value: value if isinstance(value, str) else "",
)
def test_a_structurally_broken_envelope_raises_rather_than_returning_a_partial(
	case: str, envelope: Any
) -> None:
	"""A partial is what the next stage would act on, and it routes somewhere wrong.

	``Chat Inbound Event`` keeps the payload untouched, so a refusal is recoverable: fix the
	parser and reprocess the row. A half-parsed event that landed in the wrong space is not.
	"""
	with pytest.raises(MalformedEvent):
		parse_pubsub_envelope(envelope)


def test_corrupt_base64_raises_instead_of_silently_losing_bytes() -> None:
	"""``validate=True``: the permissive decoder discards illegal characters one at a time and
	yields a shorter, plausible-looking payload."""
	with pytest.raises(MalformedEvent):
		parse_pubsub_envelope(_envelope(EVENT_MESSAGE_CREATED, {}, data="!!!! not base64 !!!!"))


def test_data_that_decodes_to_something_other_than_an_object_raises() -> None:
	for value in ("[1, 2, 3]", '"a string"', "null"):
		with pytest.raises(MalformedEvent):
			parse_pubsub_envelope(
				_envelope(
					EVENT_MESSAGE_CREATED,
					{},
					data=base64.b64encode(value.encode("utf-8")).decode("ascii"),
				)
			)


def test_a_message_event_with_no_resource_name_raises() -> None:
	"""With ``includeResource: false`` a payload without a name has no content at all."""
	with pytest.raises(MalformedEvent):
		parse_pubsub_envelope(_envelope(EVENT_MESSAGE_CREATED, {"message": {"text": "hi"}}))


def test_a_message_event_that_names_no_space_raises() -> None:
	"""Without a space there is no room to route to, and a guess would cross-post."""
	with pytest.raises(MalformedEvent):
		parse_pubsub_envelope(
			_envelope(
				EVENT_MESSAGE_CREATED,
				{"message": {"name": "widgets/12345"}},
				ce_id="",
				ce_subject="",
			)
		)


def test_a_lifecycle_event_that_names_no_subscription_raises() -> None:
	"""There would be nothing to renew or reactivate, and a lapsed subscription is silent."""
	with pytest.raises(MalformedEvent):
		parse_pubsub_envelope(
			_envelope(
				EVENT_SUBSCRIPTION_EXPIRED,
				{"subscription": {"state": "EXPIRED"}},
				ce_source="",
				ce_subject="",
			)
		)


def test_cloudevent_attribute_names_are_read_case_insensitively() -> None:
	"""Same class of assumption as an HTTP header's casing, and the same cost when it is wrong:
	an event that parses as malformed for a reason nobody can see in the payload."""
	envelope = _envelope(EVENT_MESSAGE_CREATED, {"message": {"name": RESOURCE}})
	attributes = envelope["message"]["attributes"]
	envelope["message"]["attributes"] = {key.upper(): value for key, value in attributes.items()}

	parsed = parse_pubsub_envelope(envelope)
	assert parsed.event_type == EVENT_MESSAGE_CREATED
	assert parsed.resource_name == RESOURCE


# ---------------------------------------------------------------------------
# Event-type helpers
# ---------------------------------------------------------------------------


def test_batch_types_normalise_and_everything_else_is_left_alone() -> None:
	assert singular_event_type(EVENT_MESSAGE_BATCH_CREATED) == EVENT_MESSAGE_CREATED
	assert singular_event_type(EVENT_MESSAGE_BATCH_UPDATED) == EVENT_MESSAGE_UPDATED
	assert singular_event_type(EVENT_MESSAGE_BATCH_DELETED) == EVENT_MESSAGE_DELETED
	assert singular_event_type(EVENT_MEMBERSHIP_CREATED) == EVENT_MEMBERSHIP_CREATED
	assert singular_event_type(None) == ""


def test_an_unlisted_subscription_event_is_still_treated_as_lifecycle() -> None:
	"""Prefix as well as exact match: a fourth lifecycle type Google adds must go to the
	subscription handler, not into the message pipeline where it has no resource to parse."""
	assert is_lifecycle_event_type("google.workspace.events.subscription.v1.somethingNew")
	assert not is_lifecycle_event_type(EVENT_MESSAGE_CREATED)
	assert not is_lifecycle_event_type(None)
