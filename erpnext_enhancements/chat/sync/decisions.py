# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Inbound classification and Pub/Sub event parsing — **the heart of §4.D**.

**Pure module. No I/O, no ``frappe``, no ``requests``, no clock, no randomness.**
Every function is a total function of its arguments. That is not tidiness: this repo has no
Frappe integration-test job (``CLAUDE.md``), so the bench-free tier is the *only* tier with
automatic regression protection in CI, and a bench-free test can only reach code that imports
neither ``frappe`` nor a network library. The rules that decide whether a coworker's message
gets duplicated — or silently eaten — therefore live here, where they can be held still.

This module also **names no Google host**, deliberately. Stripping the CloudEvents authority
off a ``ce-subject`` is done with :data:`_CE_AUTHORITY_RE`, which matches ``//<anything>/``
rather than a hostname. ``tests/test_chat_guardrails.py`` asserts that only ``gchat/client.py``
and ``gchat/auth.py`` name a Google host in a live string, and a parser that hardcoded one
would be a second place a reviewer has to check when Google moves something.

The fact that shapes everything: ``includeResource: false``
-----------------------------------------------------------
ADR §G.4.2 chose ``payloadOptions.includeResource: false`` because it is the **only**
configuration with a 7-day subscription TTL ceiling (4 hours with resource data, 24 hours with
resource data *and* domain-wide delegation — the 24-hour figure raises the *include-resource*
ceiling, not the 7-day one, so DWD buys no TTL here). ``PHASE2_VERIFIED.md §1.4``.

**The consequence is that an inbound event carries only a resource name.** No body, no sender,
no ``clientAssignedMessageId``. The Phase 2 brief's *"Layer 1 — read the client id off the
payload"* **does not exist in this deployment**. The real ladder is:

1. structural dedupe on ``gchat_message_name``;
2. ``spaces.messages.get`` the resource name;
3. echo check on the **fetched** ``clientAssignedMessageId``;
4. ``client-`` id with no local row ⇒ alarm, do not guess;
5. otherwise new.

**So :attr:`InboundVerdict.RESOLVE_VIA_GET` is the NORMAL path, not a fallback.** Almost every
genuine inbound message passes through it exactly once. It is not an inefficiency to be
optimised away — deleting it does not save a call, it removes the only source of the
information the echo check needs, and the failure mode is that every message this system sends
comes back and is stored a second time. The ADR budgeted the read against the
3,000-reads-per-minute project quota on purpose.

Which direction to be wrong in
------------------------------
Two failure directions are not symmetric and the code is biased on purpose.

* **A duplicate** is visible, ugly, and reported by the first person who sees it.
* **A suppressed genuine message** is invisible. A coworker's message never appears in ERPNext,
  nobody knows it was sent, and there is no row anywhere to notice. It is discovered weeks
  later, if at all, by someone reconstructing a conversation.

So this module never suppresses on a guess. :func:`classify_inbound` returns
:attr:`InboundVerdict.ECHO` only when the ``unique(room, client_message_id)`` probe **hit** —
never because a space merely has an unrelated write in flight — and
:func:`select_echo_candidate` returns ``(None, "ambiguous")`` the moment two rows could match.

Verdicts, and what the caller does with each
--------------------------------------------
The verdict answers *"may I apply this event's operation?"*; the **operation comes from the
event type**, not from the verdict. That distinction is load-bearing and easy to miss:

==================  ====================================================================
``IGNORE``          a space we do not mirror, a resource event this classifier does not
                    own (membership, lifecycle), or a ``deleted`` for a message we never
                    stored (ADR §G.8 Rule 1 — *recorded Ignored*).
``DUPLICATE``       a ``created`` whose ``gchat_message_name`` is already stored. Success:
                    a Pub/Sub redelivery or a reconciliation replay. Ack it.
``ECHO``            our own write coming back. Bind ``gchat_message_name`` onto the
                    existing row; **do not insert, do not notify, do not relay.**
``ECHO_ORPHAN``     a ``client-`` id we appear to have minted with no row behind it.
                    **Alarm and stop.** Guessing here is how a real message gets eaten.
``RESOLVE_VIA_GET`` the normal path — fetch the resource, then classify again.
``DEFER``           an outbound write for this room is in flight and we cannot yet tell an
                    echo from a stranger. Bounded; expires into the terminal verdict.
``TOMBSTONED``      terminal. The row is soft-deleted and is never resurrected.
``NEW``             **apply this event's operation.** ``created`` ⇒ insert; ``updated`` ⇒
                    apply the edit, or insert it as a create if the name is unknown
                    (Rule 1); ``deleted`` ⇒ tombstone the stored row.
==================  ====================================================================

``NEW`` on an ``updated`` or a ``deleted`` reads oddly until you hold the distinction above:
the classifier is a *provenance* gate, and "not ours, not a replay, not tombstoned" is the same
answer whichever operation the event carries.

Identifier rules are **not** restated here
------------------------------------------
:func:`~erpnext_enhancements.chat.gchat.ids.is_client_message_id` is the cheap half of
invariant I3 — *does this look like an id we minted?* — and the ``unique(room,
client_message_id)`` probe the caller supplies as
:attr:`InboundFacts.local_by_client_id` is the authoritative half. **"Looks like ours" and "is
ours" are not the same claim**, and only the index knows: any Chat client may set a ``client-``
prefixed id, so treating the prefix alone as proof would let a third party suppress ingestion
of their own messages by guessing it. That is a denial of service, not a defect in the
derivation. This module uses both halves and re-states neither; the rules live in ``ids.py``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

from erpnext_enhancements.chat.gchat.ids import is_client_message_id

# --------------------------------------------------------------------------------------
# Event type vocabulary
# --------------------------------------------------------------------------------------

#: The three singular Chat message event types. These are the only types this classifier
#: owns; membership, space and reaction events are routed elsewhere by the caller.
EVENT_MESSAGE_CREATED: Final[str] = "google.workspace.chat.message.v1.created"
EVENT_MESSAGE_UPDATED: Final[str] = "google.workspace.chat.message.v1.updated"
EVENT_MESSAGE_DELETED: Final[str] = "google.workspace.chat.message.v1.deleted"

#: The batched variants. Workspace Events coalesces bursts, so one Pub/Sub delivery can carry
#: N resource names — ``Chat Inbound Event``'s own docstring records that one row is one
#: *delivery*, not one contained resource, and that the worker fans it out.
EVENT_MESSAGE_BATCH_CREATED: Final[str] = "google.workspace.chat.message.v1.batchCreated"
EVENT_MESSAGE_BATCH_UPDATED: Final[str] = "google.workspace.chat.message.v1.batchUpdated"
EVENT_MESSAGE_BATCH_DELETED: Final[str] = "google.workspace.chat.message.v1.batchDeleted"

MESSAGE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
	{EVENT_MESSAGE_CREATED, EVENT_MESSAGE_UPDATED, EVENT_MESSAGE_DELETED}
)

#: Batch type -> the singular type each contained resource should be classified under.
#:
#: :func:`classify_inbound` normalises through this rather than returning ``IGNORE`` for a
#: batch type. The caller is *supposed* to fan out first (see :func:`fanout_events`), but the
#: cost of the two disagreeing must not be silently dropped messages — which is exactly what
#: "unknown type ⇒ IGNORE" would do to a burst of real coworker traffic.
BATCH_TO_SINGULAR_EVENT_TYPE: Final[Mapping[str, str]] = {
	EVENT_MESSAGE_BATCH_CREATED: EVENT_MESSAGE_CREATED,
	EVENT_MESSAGE_BATCH_UPDATED: EVENT_MESSAGE_UPDATED,
	EVENT_MESSAGE_BATCH_DELETED: EVENT_MESSAGE_DELETED,
}

#: Subscription lifecycle events arrive on the **same topic** as the resource events
#: (``PHASE2_VERIFIED.md §7``). ``expirationReminder`` fires at T−12h and T−1h.
EVENT_SUBSCRIPTION_EXPIRATION_REMINDER: Final[str] = (
	"google.workspace.events.subscription.v1.expirationReminder"
)
EVENT_SUBSCRIPTION_SUSPENDED: Final[str] = "google.workspace.events.subscription.v1.suspended"
EVENT_SUBSCRIPTION_EXPIRED: Final[str] = "google.workspace.events.subscription.v1.expired"

LIFECYCLE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
	{
		EVENT_SUBSCRIPTION_EXPIRATION_REMINDER,
		EVENT_SUBSCRIPTION_SUSPENDED,
		EVENT_SUBSCRIPTION_EXPIRED,
	}
)

#: Matched as a prefix as well as against the exact set above, so a fourth lifecycle type
#: Google adds is routed to the subscription handler rather than into the message pipeline,
#: where it would be parsed for a resource name it does not have.
LIFECYCLE_EVENT_PREFIX: Final[str] = "google.workspace.events.subscription.v1."


def singular_event_type(event_type: str | None) -> str:
	"""Collapse a batched Chat message event type onto its singular equivalent.

	Identity for everything else, including types this module does not own — the caller's
	routing, not a rewrite here, is what keeps membership events out of the message pipeline.
	"""
	value = str(event_type or "").strip()
	return BATCH_TO_SINGULAR_EVENT_TYPE.get(value, value)


def is_message_event_type(event_type: str | None) -> bool:
	"""Is this one of the three Chat *message* resource events (batched or not)?"""
	return singular_event_type(event_type) in MESSAGE_EVENT_TYPES


def is_lifecycle_event_type(event_type: str | None) -> bool:
	"""Is this a Workspace Events **subscription lifecycle** event rather than a resource one?

	Prefix as well as exact match: see :data:`LIFECYCLE_EVENT_PREFIX`.
	"""
	value = str(event_type or "").strip()
	return value in LIFECYCLE_EVENT_TYPES or value.startswith(LIFECYCLE_EVENT_PREFIX)


# --------------------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------------------


class InboundVerdict(str, Enum):
	"""The eight answers. See the module docstring for what the caller does with each."""

	IGNORE = "IGNORE"
	DUPLICATE = "DUPLICATE"
	ECHO = "ECHO"
	ECHO_ORPHAN = "ECHO_ORPHAN"
	RESOLVE_VIA_GET = "RESOLVE_VIA_GET"
	DEFER = "DEFER"
	TOMBSTONED = "TOMBSTONED"
	NEW = "NEW"


@dataclass(frozen=True)
class InboundFacts:
	"""Everything the decision needs, gathered by the caller. **No I/O in this module.**

	Every field is a probe result the caller already had to run, or a counter it already
	holds. Nothing here is optional and nothing has a default, deliberately: a default would
	let a caller forget to run the ``gchat_message_name`` probe and get ``NEW`` for a message
	it has already stored, or forget ``room`` and get ``IGNORE`` for every event in a mirrored
	space. Both are silent. Making the constructor demand all of them turns the omission into
	a ``TypeError`` at the call site.
	"""

	#: The CloudEvents ``ce-type``. Batched variants are normalised, so either form is fine.
	event_type: str
	#: ``spaces/{space}`` — carried for logging and for the room probe the caller already ran.
	space_name: str
	#: ``spaces/{space}/messages/{id}`` — the only content an ``includeResource: false``
	#: event actually carries.
	resource_name: str
	#: ``unique(gchat_space_name)`` probe result. ``None`` ⇒ a space we do not mirror.
	room: str | None
	#: ``unique(gchat_message_name)`` probe result — the ``Chat Message.name``, or ``None``.
	stored_message: str | None
	#: ``Chat Message.is_deleted`` on :attr:`stored_message`. Meaningless when that is ``None``.
	stored_is_deleted: bool
	#: ``clientAssignedMessageId`` from a ``messages.get``, or ``None`` if not fetched yet.
	#: With ``includeResource: false`` this is **always** ``None`` on the first pass.
	client_message_id: str | None
	#: Has the caller already performed the ``messages.get``? Drives ``RESOLVE_VIA_GET``.
	resource_fetched: bool
	#: ``unique(room, client_message_id)`` probe result. The authoritative half of I3.
	local_by_client_id: str | None
	#: ``Chat Relay Job`` rows ``In Progress`` with an unexpired lease for this room.
	in_flight_claims: int
	#: How many times this raw event has been processed. ``0`` on the first pass.
	attempt: int
	#: How many attempts may be deferred before the terminal verdict is taken anyway.
	#: ``0`` disables deferral entirely.
	defer_budget: int
	#: Does the **fetched** body equal the body already stored on :attr:`local_by_client_id`
	#: (or :attr:`stored_message`)? ``None`` when unfetched or when there is nothing to
	#: compare against.
	#:
	#: This exists for exactly one case and it is a case the client id cannot answer. Relaying
	#: an ERPNext **edit** calls ``messages.patch``, and Google emits a ``message.v1.updated``
	#: for it — so an ``updated`` naming a message we authored has two possible causes, our own
	#: patch echoing back and a coworker editing it in the native client, and
	#: ``clientAssignedMessageId`` is identical in both because it is written once and never
	#: changes. The body is what separates them, and comparing one specific message's stored
	#: text against that same message's fetched text is an exact equality on a single row — not
	#: a heuristic, and structurally incapable of the "two identical short messages" collapse
	#: that :func:`select_echo_candidate` has to defend against.
	#:
	#: Default ``None`` because it is the one field the caller legitimately may not have: on
	#: the pre-fetch pass there is nothing fetched to compare. Every other field is mandatory.
	body_matches_stored: bool | None = None


def classify_inbound(facts: InboundFacts) -> InboundVerdict:
	"""Total, side-effect-free classification of one inbound Chat message event.

	Every field combination maps to exactly one verdict and
	``tests/test_chat_sync_decisions.py`` enumerates the matrix as a table — **that table is
	the specification.** This is the only place the echo rules live.

	The ladder, in order, and why each rung sits where it does:

	1. **Not a message event** ⇒ ``IGNORE``. Membership, space, reaction and lifecycle events
	   are somebody else's pipeline. Batched message types are normalised, not dropped.
	2. **Unknown room** ⇒ ``IGNORE``. A space we do not mirror. Checked before anything that
	   could write, so an un-mirrored space cannot create rows by any path.
	3. **Empty resource name** ⇒ ``IGNORE``. There is nothing to address;
	   :func:`parse_pubsub_envelope` refuses to produce one, so this is a caller bug guard.
	4. **Stored and soft-deleted** ⇒ ``TOMBSTONED``, whatever the event says. A tombstone is
	   terminal: a late ``created`` or ``updated`` for a name we already buried must not
	   resurrect it. Google's own tombstone is metadata-only, so ERPNext's row is the last
	   copy of the body (ADR §F.6.5) and re-creating it would produce a second, empty one.
	5. **Stored, and the event is ``created``** ⇒ ``DUPLICATE``. A Pub/Sub redelivery or a
	   reconciliation replay. Success — ack it. Note this rung is ``created``-only: a stored
	   row is the *target* of an ``updated`` or a ``deleted``, not evidence of a replay, and
	   calling those ``DUPLICATE`` would drop every inbound edit and every inbound delete.
	6. **Carries an id shaped like ours.** No local row for it ⇒ ``ECHO_ORPHAN``, whatever the
	   event type: an id we minted that we cannot find is anomalous in every direction and the
	   caller alarms rather than guesses. A local row **and** the event is ``created`` ⇒
	   ``ECHO``. A local row and the event is ``updated`` or ``deleted`` ⇒ **fall through**.

	   **That last clause is the whole rung and it is easy to get wrong.** A
	   ``clientAssignedMessageId`` is written once and lives on the Google message forever, so
	   it says *ERPNext authored this message* — it does **not** say *ERPNext caused this
	   event*. Only a ``created`` event can be our own write coming back; an ``updated`` or a
	   ``deleted`` naming a message we originated is a coworker mutating it in the native
	   client, which is precisely the traffic decision #2 exists to carry. Returning ``ECHO``
	   there would mean "bind, do not insert, do not notify, do not relay", so **every inbound
	   edit of every message ERPNext ever sent would be dropped in silence** — no row change,
	   no revision, no log line — and ADR §G.8 Rule 3, which exists to arbitrate exactly that
	   collision, could never fire on the rows it was written for.

	   This rung sits above the fetch rung on purpose: a caller that *already* has the id (the
	   interaction transport, or a future ``includeResource: true`` deployment) must not be
	   sent to buy one it is holding.
	7. **``deleted``** ⇒ ``NEW`` when we can name the row — either ``gchat_message_name`` is
	   bound *or* the client-id probe hit — so the tombstone is applied. Otherwise ``IGNORE``
	   per ADR §G.8 Rule 1, with a bounded ``DEFER`` first if a write for this room is in
	   flight, because the create it belongs to may be seconds behind. A ``deleted`` event
	   never reaches the fetch rung: the message is gone, and ``messages.get`` would answer
	   ``NOT_FOUND``. Our own relayed delete echoing back lands here too and is a no-op —
	   ``is_deleted`` was set before the relay, so rung 4 catches it as ``TOMBSTONED`` first.
	8. **Not fetched** ⇒ ``RESOLVE_VIA_GET``. **This is the normal path** — see the module
	   docstring before deciding it looks expensive.
	9. **In flight, budget left** ⇒ ``DEFER``. Only when the resource name is *not* yet stored:
	   once it is, any in-flight claim for that room is a different message's.
	10. Otherwise ⇒ ``NEW``.

	**``NEW`` means "act on this event", not "insert a row".** The caller already has to switch
	on the event type to know what acting means, and the three meanings are: ``created`` ⇒
	insert; ``updated`` ⇒ apply ADR §G.8 Rule 3 to the stored row, or upsert from the fetched
	resource if there is none (Rule 1's ``allowMissing`` equivalent); ``deleted`` ⇒ tombstone
	the stored row. The verdict deliberately does not encode which — that is the event type's
	job, and duplicating it here would give two places to disagree.

	**Rung 9 is a delay, never a suppression.** A genuine foreign message that arrives while an
	unrelated outbound write is open is deferred at most :attr:`InboundFacts.defer_budget`
	times and then classified ``NEW``. It is never ``ECHO``, because ``ECHO`` requires the
	index probe to have hit. Suppressing a coworker's message on the strength of "something
	else was being written to that room" is the worst bug this module could have: invisible,
	unreported, and unrecoverable.
	"""
	event_type = singular_event_type(facts.event_type)

	if not is_message_event_type(event_type):
		return InboundVerdict.IGNORE
	if not str(facts.room or "").strip():
		return InboundVerdict.IGNORE
	if not str(facts.resource_name or "").strip():
		return InboundVerdict.IGNORE

	if facts.stored_message:
		if facts.stored_is_deleted:
			return InboundVerdict.TOMBSTONED
		if event_type == EVENT_MESSAGE_CREATED:
			return InboundVerdict.DUPLICATE

	if is_client_message_id(facts.client_message_id):
		if not facts.local_by_client_id:
			return InboundVerdict.ECHO_ORPHAN
		if event_type == EVENT_MESSAGE_CREATED:
			return InboundVerdict.ECHO
		if event_type == EVENT_MESSAGE_UPDATED and facts.body_matches_stored is True:
			# Our own `messages.patch` coming back. `is True` and not truthiness: `None`
			# means "not compared", and treating unknown as "matches" would suppress a real
			# edit — the one direction this module may never guess in.
			return InboundVerdict.ECHO
		# Deliberately falls through otherwise. See rung 6 in the docstring: our own client
		# id is permanent, so it identifies the AUTHOR of the message, never the author of
		# this event.

	if event_type == EVENT_MESSAGE_DELETED:
		# `local_by_client_id` counts as knowing the message. A delete can legitimately
		# arrive before the create echo bound `gchat_message_name`, and IGNORE there would
		# discard a real deletion of a row we are holding and can name.
		if facts.stored_message or facts.local_by_client_id:
			return InboundVerdict.NEW
		return _defer_or(facts, InboundVerdict.IGNORE)

	if not facts.resource_fetched:
		return InboundVerdict.RESOLVE_VIA_GET

	return _defer_or(facts, InboundVerdict.NEW)


def _defer_or(facts: InboundFacts, terminal: InboundVerdict) -> InboundVerdict:
	"""``DEFER`` while a write for this room is in flight and the budget allows; else ``terminal``.

	Three conditions, and dropping any one of them turns a delay into a suppression:

	* **the resource name is not stored.** Once it is, the row is bound and an in-flight claim
	  for the room belongs to some other message.
	* **something is genuinely in flight.** ``in_flight_claims`` is the count of
	  ``Chat Relay Job`` rows ``In Progress`` with an unexpired lease — a real, inspectable
	  fact, not a heuristic.
	* **the budget is not spent.** ``attempt`` counts prior processings of this raw event, so
	  ``attempt < defer_budget`` is a hard ceiling and ``defer_budget = 0`` disables deferral.

	The terminal verdict is what the caller eventually acts on, and it is never ``ECHO``.
	"""
	if facts.stored_message:
		return terminal
	if (facts.in_flight_claims or 0) > 0 and (facts.attempt or 0) < (facts.defer_budget or 0):
		return InboundVerdict.DEFER
	return terminal


# --------------------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------------------

#: Hex characters of SHA-256 kept in an idempotency key. 32 hex characters is 128 bits, which
#: is the same budget ``ids.client_message_id`` spends, and it keeps the whole key inside a
#: ``Data`` column and inside a Redis key without a length rule anybody has to remember.
_IDEMPOTENCY_DIGEST_CHARS: Final[int] = 32


def idempotency_key(event_type: str, resource_name: str, last_update_time: str | None) -> str:
	"""The key that makes reprocessing one delivery a no-op, per §4.C's table.

	``created`` and ``deleted`` key on the **resource name alone**: those operations are
	idempotent against a given resource, so a redelivery of either must collapse onto the
	first. ``updated`` additionally keys on ``lastUpdateTime``, because two edits of one
	message are two different operations and collapsing them would make a stale redelivery
	overwrite a newer edit — the exact hazard ADR §G.8 Rule 3 exists to prevent.

	The resource name is **hashed rather than embedded**. No published rule bounds the length
	of a Chat resource name, and this value is stored in a column and used as a cache key; a
	key whose length depends on Google's id format is a truncation waiting to happen. The
	kind prefix survives in clear so a key is still legible in a log line.

	**An ``updated`` with no ``lastUpdateTime`` collapses onto one key per resource, and that
	is deliberate.** Rule 3 discards an inbound edit whose timestamp is missing (ERPNext wins
	ties and wins absences), so the second such edit was never going to be applied anyway;
	letting it dedupe keeps the two rules agreeing instead of quietly disagreeing.
	"""
	kind = singular_event_type(event_type)
	short_kind = kind.rsplit(".", 1)[-1] if kind else "unknown"
	name = str(resource_name or "").strip()

	if kind == EVENT_MESSAGE_UPDATED:
		seed = f"{kind}|{name}|{str(last_update_time or '').strip()}"
	else:
		seed = f"{kind}|{name}"

	digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:_IDEMPOTENCY_DIGEST_CHARS]
	return f"{short_kind}:{digest}"


# --------------------------------------------------------------------------------------
# Event parsing — two nested layers (PHASE2_VERIFIED.md §7)
# --------------------------------------------------------------------------------------


class MalformedEvent(ValueError):
	"""A delivery this parser refuses to interpret.

	Raised rather than returning a partial ``ParsedEvent``, because a partial is exactly what
	a downstream stage would act on. ``Chat Inbound Event`` keeps the payload untouched, so a
	refusal is recoverable: fix the parser, reprocess the row. A half-parsed event that routed
	to the wrong space is not.
	"""


@dataclass(frozen=True)
class ParsedEvent:
	"""One CloudEvent, lifted out of its Pub/Sub envelope."""

	event_type: str
	space_name: str
	resource_name: str
	#: ``ce-subject``, verbatim. Carried rather than interpreted — see the ``VERIFY:`` on
	#: :func:`parse_pubsub_envelope` about its exact shape for Chat resource events.
	subject: str
	#: ``ce-id``, of the form ``spaces/{space}/spaceEvents/{id}``.
	event_id: str
	publish_time: str
	#: A subscription lifecycle event rather than a Chat resource event.
	is_lifecycle: bool
	#: Populated for lifecycle events; empty otherwise.
	subscription_name: str
	#: The decoded inner payload, untouched. Everything else on this object is derived from
	#: it, so a parser bug is diagnosable from the row without re-fetching anything.
	raw: Mapping[str, Any]
	#: **Every** resource name the delivery carried. Length 1 for a singular event; length N
	#: for a batched one, where :attr:`resource_name` is only the first. Callers that can meet
	#: a batch delivery must use :func:`fanout_events`, never :attr:`resource_name`.
	resource_names: tuple[str, ...] = ()

	@property
	def is_batch(self) -> bool:
		return len(self.resource_names) > 1


#: The Pub/Sub envelope's own key. Note the collision with the *inner* Chat payload's
#: ``message`` key — the two nested layers both call their payload "message", which is the
#: single most confusing thing about this shape and the reason the outer read is a named
#: constant rather than a literal buried in the function.
_PUBSUB_MESSAGE_KEY: Final[str] = "message"

#: CloudEvents attributes, carried as Pub/Sub message attributes.
_CE_ID: Final[str] = "ce-id"
_CE_SOURCE: Final[str] = "ce-source"
_CE_TYPE: Final[str] = "ce-type"
_CE_SUBJECT: Final[str] = "ce-subject"

#: ``//<authority>/<resource path>`` — the CloudEvents form for a Google resource reference.
#: Matched structurally rather than by hostname: this module names no Google host (see the
#: module docstring), and the structural form is also correct for every other service.
_CE_AUTHORITY_RE: Final[re.Pattern[str]] = re.compile(r"\A//[^/]+/")

#: ``spaces/{space}`` at the head of a resource name, an event id, or a stripped subject.
_SPACE_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"\A(spaces/[A-Za-z0-9_-]+)")

#: ``subscriptions/{id}`` at the head of a stripped ``ce-source`` or lifecycle payload name.
_SUBSCRIPTION_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"\A(subscriptions/[A-Za-z0-9_.-]+)")


def parse_pubsub_envelope(envelope: Mapping[str, Any]) -> ParsedEvent:
	"""Outer Pub/Sub message ➜ :class:`ParsedEvent`. Raises, never returns a partial.

	**Two nested layers**, and conflating them is the classic bug (``PHASE2_VERIFIED.md §7``):

	* the **outer** Pub/Sub envelope has one key that matters, ``message``, holding
	  ``attributes`` / ``data`` / ``messageId`` / ``orderingKey`` / ``publishTime``. The
	  CloudEvents metadata is in ``attributes``;
	* the **inner** payload is base64 in ``data`` and, with ``includeResource: false``, carries
	  the changed resource's **name only**.

	Both layers use the word "message" for their payload. The outer one is a Pub/Sub message;
	the inner one is a Chat Message resource. They are unrelated.

	``data`` is accepted in three forms and the distinction is not arbitrary: a **str** is
	base64, which is what the REST push/pull envelope carries; **bytes** are the already
	decoded payload, which is what a Pub/Sub client library hands over; and a **Mapping** is
	an already decoded payload, which is what the in-process fake harness produces. Base64 is
	decoded with validation on, so a truncated or corrupted attribute raises here rather than
	silently yielding fewer bytes and a confusing JSON error.

	``VERIFY:`` the exact ``ce-subject`` value for a Chat *resource* event was never read off a
	captured production payload — the documented CloudEvents form is
	``//<service authority>/<resource>``, and this parser strips any authority and then looks
	for a ``spaces/`` prefix. It is a **fallback** here: the space is derived from the resource
	name first and from ``ce-id`` second, both of which are documented shapes. Settle it by
	diffing the first real delivery's ``Chat Inbound Event.payload`` against the fixtures.

	``VERIFY:`` the lifecycle payload's container key. ``PHASE2_VERIFIED.md §7`` records only
	that lifecycle payloads use **snake_case** inside the subscription object, unlike the Chat
	resources. This parser reads a ``name`` off whatever single object the payload contains and
	falls back to ``ce-source`` / ``ce-subject``, so it does not depend on the key's spelling —
	but the ``subscription_name`` it returns is unproven until one real reminder is captured.
	"""
	if not isinstance(envelope, Mapping):
		raise MalformedEvent(f"Pub/Sub envelope must be a mapping; got {type(envelope).__name__}.")

	pubsub_message = envelope.get(_PUBSUB_MESSAGE_KEY)
	if not isinstance(pubsub_message, Mapping):
		raise MalformedEvent(
			"Pub/Sub envelope has no 'message' object. The outer layer is the Pub/Sub message; "
			"the inner Chat resource is inside its base64 'data'."
		)

	attributes = pubsub_message.get("attributes")
	if not isinstance(attributes, Mapping):
		raise MalformedEvent("Pub/Sub message carries no 'attributes'; the CloudEvents metadata lives there.")

	event_type = _attribute(attributes, _CE_TYPE)
	if not event_type:
		raise MalformedEvent(
			"Pub/Sub message attributes carry no 'ce-type'; nothing can be routed without it."
		)

	payload = _decode_data(pubsub_message.get("data"))
	event_id = _attribute(attributes, _CE_ID)
	subject = _attribute(attributes, _CE_SUBJECT)
	source = _attribute(attributes, _CE_SOURCE)
	publish_time = str(pubsub_message.get("publishTime") or "")

	if is_lifecycle_event_type(event_type):
		subscription_name = _subscription_name(payload, source, subject)
		if not subscription_name:
			raise MalformedEvent(
				f"{event_type} names no subscription in its payload, its ce-source or its ce-subject; "
				"there is nothing to renew or reactivate."
			)
		return ParsedEvent(
			event_type=event_type,
			space_name="",
			resource_name=subscription_name,
			subject=subject,
			event_id=event_id,
			publish_time=publish_time,
			is_lifecycle=True,
			subscription_name=subscription_name,
			raw=payload,
			resource_names=(subscription_name,),
		)

	resource_names = _resource_names(payload)
	if not resource_names:
		raise MalformedEvent(
			f"{event_type} carries no resource name in its payload. With includeResource=false the "
			"name is the ONLY content an event has, so a payload without one cannot be interpreted."
		)

	space_name = _space_name(resource_names[0], event_id, subject)
	if not space_name:
		raise MalformedEvent(
			f"{event_type} yields no 'spaces/{{space}}' from its resource name, ce-id or ce-subject; "
			"without a space there is no room to route it to."
		)

	return ParsedEvent(
		event_type=event_type,
		space_name=space_name,
		resource_name=resource_names[0],
		subject=subject,
		event_id=event_id,
		publish_time=publish_time,
		is_lifecycle=False,
		subscription_name="",
		raw=payload,
		resource_names=resource_names,
	)


def fanout_events(parsed: ParsedEvent) -> tuple[ParsedEvent, ...]:
	"""Split a batched delivery into one singular :class:`ParsedEvent` per resource name.

	One ``Chat Inbound Event`` row is one **delivery**, not one resource — that DocType's own
	docstring says so, and it is what keeps acking a delivery a single decision. This is the
	other half of that: the worker fans out here, so every downstream stage sees exactly one
	resource and :func:`classify_inbound` never has to reason about a list.

	A singular event returns a one-tuple containing itself, so the caller has one code path.
	"""
	names = parsed.resource_names or ((parsed.resource_name,) if parsed.resource_name else ())
	if len(names) <= 1:
		return (parsed,)

	singular = singular_event_type(parsed.event_type)
	return tuple(
		ParsedEvent(
			event_type=singular,
			space_name=_space_name(name, parsed.event_id, parsed.subject) or parsed.space_name,
			resource_name=name,
			subject=parsed.subject,
			event_id=parsed.event_id,
			publish_time=parsed.publish_time,
			is_lifecycle=parsed.is_lifecycle,
			subscription_name=parsed.subscription_name,
			raw=parsed.raw,
			resource_names=(name,),
		)
		for name in names
	)


def _attribute(attributes: Mapping[str, Any], name: str) -> str:
	"""Case-insensitive attribute read.

	Pub/Sub attribute keys are case-sensitive and Google sends them lowercase, but this is the
	same class of assumption as an HTTP header's casing — ``client.header_value`` exists for
	exactly that reason — and the cost of being wrong is an event that parses as malformed for
	a reason nobody can see in the payload.
	"""
	wanted = name.lower()
	for key, value in attributes.items():
		if str(key).lower() == wanted:
			return str(value or "").strip()
	return ""


def _decode_data(raw: Any) -> dict[str, Any]:
	"""Decode the inner CloudEvent payload. See :func:`parse_pubsub_envelope` for the forms."""
	if raw is None or raw == "":
		raise MalformedEvent("Pub/Sub message carries no 'data'; the changed resource's name lives there.")

	if isinstance(raw, Mapping):
		return dict(raw)

	if isinstance(raw, bytes | bytearray):
		decoded = bytes(raw)
	else:
		text = "".join(str(raw).split())
		# Pub/Sub emits padded standard base64; re-padding costs nothing and tolerates a
		# transport that stripped it. `validate=True` so corruption raises instead of being
		# silently discarded character by character.
		text += "=" * (-len(text) % 4)
		try:
			decoded = base64.b64decode(text, validate=True)
		except (binascii.Error, ValueError) as exc:
			raise MalformedEvent(f"Pub/Sub 'data' is not valid base64: {exc}") from None

	try:
		payload = json.loads(decoded.decode("utf-8"))
	except (UnicodeDecodeError, ValueError) as exc:
		raise MalformedEvent(f"Pub/Sub 'data' did not decode to JSON: {exc}") from None

	if not isinstance(payload, Mapping):
		raise MalformedEvent(f"Pub/Sub 'data' decoded to {type(payload).__name__}, not a JSON object.")
	return dict(payload)


def _name_of(candidate: Any) -> str:
	"""The resource name carried by a payload fragment, in either of the two shapes.

	``{"name": "..."}`` is the resource itself; ``{"message": {"name": "..."}}`` is the
	event-data wrapper around it. Both appear, and which one a batch element uses is not
	documented — ``VERIFY:`` against a captured batched delivery.
	"""
	if isinstance(candidate, str):
		return candidate.strip()
	if not isinstance(candidate, Mapping):
		return ""

	direct = candidate.get("name")
	if isinstance(direct, str) and direct.strip():
		return direct.strip()

	for value in candidate.values():
		if isinstance(value, Mapping):
			nested = value.get("name")
			if isinstance(nested, str) and nested.strip():
				return nested.strip()
	return ""


def _resource_names(payload: Mapping[str, Any]) -> tuple[str, ...]:
	"""Every resource name in a decoded payload, batched or singular.

	Structural rather than keyed on ``messages`` / ``memberships`` / ``reactions``: the batch
	container's key varies by resource type and the singular wrapper's key varies with it, and
	a parser that enumerated them would need editing every time a new subscription target is
	added. A list of things with names is a batch; a thing with a name is not.
	"""
	batched: list[str] = []
	for value in payload.values():
		if isinstance(value, list | tuple):
			for item in value:
				name = _name_of(item)
				if name:
					batched.append(name)
	if batched:
		return tuple(batched)

	for value in payload.values():
		name = _name_of(value)
		if name:
			return (name,)

	direct = _name_of(payload)
	return (direct,) if direct else ()


def _space_name(*candidates: str) -> str:
	"""First ``spaces/{space}`` found among the candidates, authority stripped.

	Order is the caller's, and it matters: the resource name is authoritative and documented,
	``ce-id`` (``spaces/{space}/spaceEvents/{id}``) is documented, and ``ce-subject`` is the
	unverified fallback.
	"""
	for candidate in candidates:
		text = _CE_AUTHORITY_RE.sub("", str(candidate or "").strip())
		match = _SPACE_PREFIX_RE.match(text)
		if match:
			return match.group(1)
	return ""


def _subscription_name(payload: Mapping[str, Any], source: str, subject: str) -> str:
	"""``subscriptions/{id}`` from the lifecycle payload, then ``ce-source``, then ``ce-subject``."""
	for candidate in (_name_of(payload), source, subject):
		text = _CE_AUTHORITY_RE.sub("", str(candidate or "").strip())
		match = _SUBSCRIPTION_PREFIX_RE.match(text)
		if match:
			return match.group(1)
	return ""


# --------------------------------------------------------------------------------------
# The bounded echo fallback (ADR §G.3.2) — ships DISABLED
# --------------------------------------------------------------------------------------

#: ADR §G.3.2's window. A candidate outside it cannot be bound at all, which is what stops the
#: heuristic reaching back into history for something that merely reads the same.
ECHO_WINDOW_SECONDS_DEFAULT: Final[float] = 120.0

#: Only a message we believe we have **not** successfully relayed can be an unbound echo. A
#: ``Relayed`` row already has its ``gchat_message_name``, so structural dedupe would have
#: answered first; ``Inbound`` and ``Not Mirrored`` never had an outbound write to echo.
ECHO_FALLBACK_SYNC_STATES: Final[frozenset[str]] = frozenset({"Pending", "Failed"})

#: Reason codes. Only ``matched`` binds; every other value means the caller treats the message
#: as ``NEW``. They are constants so the alarm, the counter and the tests cannot drift apart.
ECHO_REASON_MATCHED: Final[str] = "matched"
ECHO_REASON_AMBIGUOUS: Final[str] = "ambiguous"
ECHO_REASON_NO_MATCH: Final[str] = "no-match"
ECHO_REASON_NO_CANDIDATES: Final[str] = "no-candidates"
ECHO_REASON_INSUFFICIENT_EVIDENCE: Final[str] = "insufficient-evidence"
ECHO_REASON_WINDOW_CLOSED: Final[str] = "window-closed"


@dataclass(frozen=True)
class EchoCandidate:
	"""One ERPNext row that *could* be the unbound original of an inbound message."""

	#: ``Chat Message.name``.
	message: str
	#: The sending user. Compared case-insensitively because it is an email address.
	sender: str
	#: SHA-256 of the **normalised** body. The caller normalises; this function compares.
	text_hash: str
	#: Epoch seconds. Site-local vs UTC confusion has already shipped one bug in this app
	#: (``now_datetime()`` is site-local), so the boundary is an epoch float, not a string.
	created_epoch: float
	#: ``Chat Message.sync_state``.
	sync_state: str


def select_echo_candidate(
	candidates: Sequence[EchoCandidate],
	*,
	sender: str,
	text_hash: str,
	inbound_epoch: float,
	window_seconds: float,
) -> tuple[str | None, str]:
	"""Return ``(message_name_or_None, reason)`` for the no-client-id residue.

	**This function is the resolution of a real conflict between two authorities**, and it is
	worth stating in full because a later reader will otherwise "fix" one side of it.

	* The **Phase 2 brief forbids heuristic correlation outright** and writes an explicit
	  anti-test for it, citing two identical short messages sent seconds apart. It is right:
	  binding the wrong one swaps two people's attribution, and nothing ever reports it.
	* **ADR §G.3.2 permits a bounded one** for the residue where no client id exists *at all* —
	  a message posted by an older build, a manual API call during setup, an import-mode
	  backfill. Nothing else can recover those, and they are otherwise duplicated forever.

	**Both are satisfied by refusing ambiguity.** More than one candidate matching returns
	``(None, "ambiguous")``, which the caller treats as ``NEW`` and alarms on. That kills the
	brief's exact objection *deterministically* rather than by argument: two identical "ok"
	messages seconds apart produce two matches, so nothing is bound and nothing is lost —
	worst case a duplicate, which is visible, instead of a mis-attribution, which is not.

	The predicate is deliberately narrow and every clause is load-bearing:

	* **exact** normalised body hash — never a similarity. A substring test is the shape that
	  already produced a live defect in this stack, where a source filter made *"Pond A"* match
	  inside *"Pond Alpha"*;
	* **same sender**, case-folded;
	* **inside the window**, symmetrically, because clock skew runs both ways;
	* ``sync_state`` in :data:`ECHO_FALLBACK_SYNC_STATES`.

	An empty ``sender`` or ``text_hash`` returns ``insufficient-evidence`` without inspecting a
	single candidate: matching on an empty hash would match everything, which is precisely the
	suppression bug the whole module is arranged to avoid. A non-positive window returns
	``window-closed`` for the same reason — a window that cannot exclude anything is not a
	bound.

	**Never called unless ``Chat Settings.echo_fallback_enabled`` is on, and it is off by
	default.** Every firing is counted (``heuristic_binds``) and alarmed: a nonzero rate is an
	incident, not a steady state.
	"""
	wanted_sender = str(sender or "").strip().casefold()
	wanted_hash = str(text_hash or "").strip().casefold()
	if not wanted_sender or not wanted_hash:
		return (None, ECHO_REASON_INSUFFICIENT_EVIDENCE)

	try:
		window = float(window_seconds)
		inbound_at = float(inbound_epoch)
	except (TypeError, ValueError):
		return (None, ECHO_REASON_INSUFFICIENT_EVIDENCE)
	if window <= 0:
		return (None, ECHO_REASON_WINDOW_CLOSED)

	if not candidates:
		return (None, ECHO_REASON_NO_CANDIDATES)

	# A dict, not a set: insertion order makes the single-match return deterministic, and two
	# rows for the same Chat Message name are one candidate rather than an ambiguity.
	matched: dict[str, None] = {}
	for candidate in candidates:
		name = str(getattr(candidate, "message", "") or "").strip()
		if not name:
			continue
		if str(getattr(candidate, "sender", "") or "").strip().casefold() != wanted_sender:
			continue
		if str(getattr(candidate, "text_hash", "") or "").strip().casefold() != wanted_hash:
			continue
		if str(getattr(candidate, "sync_state", "") or "").strip() not in ECHO_FALLBACK_SYNC_STATES:
			continue
		try:
			created_at = float(getattr(candidate, "created_epoch", None))
		except (TypeError, ValueError):
			# A row with an unreadable timestamp cannot be shown to be inside the window, so it
			# is not a candidate. Failing towards NEW is the safe direction.
			continue
		if abs(created_at - inbound_at) > window:
			continue
		matched[name] = None

	if not matched:
		return (None, ECHO_REASON_NO_MATCH)
	if len(matched) > 1:
		return (None, ECHO_REASON_AMBIGUOUS)
	return (next(iter(matched)), ECHO_REASON_MATCHED)
