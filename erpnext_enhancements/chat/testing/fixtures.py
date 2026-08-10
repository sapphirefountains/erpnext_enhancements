# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Byte-shaped Google Workspace Events payloads — **the only thing the inbound parser is
tested against**.

``sync/decisions.parse_pubsub_envelope`` is the narrowest, highest-consequence function in
Phase 2: every inbound message, edit and delete arrives through it, and it is the one place
where a wrong guess about Google's wire format is invisible until production. So the rule
is absolute — **the parser is never tested against a hand-written approximation inside a
test file.** It is tested against this module, and this module is the single place a shape
may be corrected when the first live event is captured.

PROVENANCE — read this before trusting a byte
----------------------------------------------
**Every payload in this module is CONSTRUCTED, not captured.** Not one of them came off the
wire from Google. They are assembled from primary documentation as read on 2026-08-09
(``PHASE2_VERIFIED.md`` §7) and each field is marked below with how much evidence stands
behind it. That distinction is the whole reason this docstring exists: a constructed
payload that reads like a captured one is worse than no fixture at all, because it converts
"we do not know" into "we tested it".

*Documented* — stated in Google's own reference:

* the two nested layers: a Pub/Sub envelope whose single top-level key is ``message``,
  containing exactly ``attributes``, ``data``, ``messageId``, ``orderingKey``,
  ``publishTime``;
* the CloudEvents metadata living in ``attributes`` as ``ce-*`` keys;
* ``ce-id`` having the form ``spaces/{space}/spaceEvents/{id}``;
* ``ce-source`` being ``//workspaceevents.googleapis.com/subscriptions/{id}``;
* the three subscription-lifecycle ``ce-type`` strings, and that their payload uses
  **snake_case** inside the subscription object where the Chat resources use camelCase;
* that with ``payloadOptions.includeResource: false`` — the configuration ADR §G.4.2 chose,
  for the 7-day TTL ceiling — the ``data`` payload carries **the changed resource's name and
  nothing else**.

*Inferred* — consistent with the documentation, stated nowhere:

* that the inner JSON is **compact** (no spaces after separators). A machine publisher has
  no reason to pretty-print, and Chat's *error* bodies are pretty-printed only because ESF
  does that on the error path. If the first captured event is pretty-printed, only
  :func:`encode_data` changes.
* ``ce-subject`` carrying the space resource name.
* ``orderingKey`` carrying the space resource name. Plausible — per-space ordering is the
  only ordering key that would be useful — but unstated.
* the exact ``{"message": {"name": …}}`` wrapper under ``includeResource: false``. The
  documented shape is ``MessageCreatedEventData = {"message": <Message>}`` with the resource
  included; "name only" is documented as the *behaviour*, and a ``Message`` carrying only
  its ``name`` is the only way to express that in the declared type.

**Falsification plan**, and it is cheap: the first real inbound event logs its raw envelope
verbatim (headers and all) and gets diffed against :func:`message_created_event`. Until that
diff has been run once, treat every assertion built on this module as a test of *our own
consistency*, not of Google's.

A note on the two hostnames below
----------------------------------
``tests/test_chat_guardrails.py`` confines the Chat API host to ``gchat/client.py`` and
``gchat/auth.py`` — one place to reason about identity, quota and retry — and it enforces
that by scanning every live string under ``chat/``. A fixture is not a call path, but the
scanner cannot tell the difference, so :data:`SUBSCRIPTION_TARGET_RESOURCE` ships as an
RFC-2606 ``.invalid`` stand-in and every builder takes ``target_resource=`` to override it.
The real value is ``//`` followed by the Chat API host followed by ``/spaces/-``; a bench
test that wants it verbatim passes
``target_resource=f"//{client.CHAT_API_HOST.split('//')[1]}/spaces/-"``.

The Workspace Events host took the third route, which is better than either of the two that
paragraph anticipated: it is **imported** from ``gchat/events_client.py``, the one module the
guardrail permits to name it. So ``ce-source`` keeps the real host — no approximation, which
is the thing this module exists to prevent — and the containment argument keeps zero holes.
No ``HOST_LITERAL_EXCEPTIONS`` entry was needed, and an exception would have been the wrong
answer anyway: that list is a hole in the argument, and this did not require one.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Final

from erpnext_enhancements.chat.gchat.events_client import WORKSPACE_EVENTS_HOST

# --------------------------------------------------------------------------------------
# Constants — the strings the parser matches on
# --------------------------------------------------------------------------------------

#: The Workspace Events API host as it appears inside ``ce-source``, **derived rather than
#: written**, because ``tests/test_chat_guardrails.py`` now confines that host to
#: ``gchat/events_client.py`` the same way it confines the Chat API host to ``client.py``.
#:
#: The alternative was the ``.invalid`` placeholder used below for the Chat host, and it was
#: rejected here: a fixture's whole value is fidelity to a captured payload, and the day
#: someone teaches :func:`~erpnext_enhancements.chat.sync.decisions.parse_pubsub_envelope`
#: to read ``ce-source`` — to reject an event minted for a *different* subscription, say —
#: a placeholder host would let that bug through green tests. Deriving keeps the real string
#: in the fixture with no second copy to drift.
#:
#: The import is safe in the bench-free tier: ``events_client`` imports only the standard
#: library and the two pure ``gchat`` helpers at module scope, exactly as ``client.py`` does.
EVENTS_HOST: Final[str] = WORKSPACE_EVENTS_HOST.split("://", 1)[-1]

#: A stand-in for the Chat API host, which this module may not name in a live string. The
#: ``.invalid`` TLD is reserved by RFC 2606 precisely so a placeholder can never resolve —
#: which is what makes it safe to leave in a fixture, and obvious when it leaks.
CHAT_HOST_PLACEHOLDER: Final[str] = "chat-api-host.invalid"

#: Documented as the subscription target for shape B (one ``spaces/-`` subscription per
#: coworker, user auth only). The host half is the placeholder above — override with
#: ``target_resource=`` when the real string matters.
SUBSCRIPTION_TARGET_RESOURCE: Final[str] = f"//{CHAT_HOST_PLACEHOLDER}/spaces/-"

#: Documented ``ce-type`` strings for Chat resource events.
EVENT_MESSAGE_CREATED: Final[str] = "google.workspace.chat.message.v1.created"
EVENT_MESSAGE_UPDATED: Final[str] = "google.workspace.chat.message.v1.updated"
EVENT_MESSAGE_DELETED: Final[str] = "google.workspace.chat.message.v1.deleted"
EVENT_MEMBERSHIP_CREATED: Final[str] = "google.workspace.chat.membership.v1.created"
EVENT_MEMBERSHIP_UPDATED: Final[str] = "google.workspace.chat.membership.v1.updated"
EVENT_MEMBERSHIP_DELETED: Final[str] = "google.workspace.chat.membership.v1.deleted"
EVENT_SPACE_UPDATED: Final[str] = "google.workspace.chat.space.v1.updated"

#: Documented ``ce-type`` strings for subscription lifecycle events. They arrive on the
#: **same topic** as the resource events, which is why the parser must branch on the type
#: rather than on the subscription it came from. ``expirationReminder`` fires twice — at
#: T−12h and at T−1h — so the renewal path must be idempotent, not merely correct.
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

CHAT_EVENT_TYPES: Final[frozenset[str]] = frozenset(
	{
		EVENT_MESSAGE_CREATED,
		EVENT_MESSAGE_UPDATED,
		EVENT_MESSAGE_DELETED,
		EVENT_MEMBERSHIP_CREATED,
		EVENT_MEMBERSHIP_UPDATED,
		EVENT_MEMBERSHIP_DELETED,
		EVENT_SPACE_UPDATED,
	}
)

#: Documented. CloudEvents 1.0 over Pub/Sub, JSON payload.
CE_SPECVERSION: Final[str] = "1.0"
CE_DATACONTENTTYPE: Final[str] = "application/json"

#: Defaults that make a fixture readable at a glance. None of them is a real identifier.
DEFAULT_SUBSCRIPTION_ID: Final[str] = "fake-events-subscription"
DEFAULT_SPACE: Final[str] = "spaces/AAAAfakeSpace"
DEFAULT_MESSAGE: Final[str] = f"{DEFAULT_SPACE}/messages/fakeMsg01.fakeMsg01"
DEFAULT_MEMBERSHIP: Final[str] = f"{DEFAULT_SPACE}/members/100000000000000000001"
DEFAULT_PUBSUB_MESSAGE_ID: Final[str] = "9000000000000001"
DEFAULT_PUBLISH_TIME: Final[str] = "2026-08-09T12:00:00.000000Z"
DEFAULT_PUBSUB_TOPIC: Final[str] = "projects/erpnext-465317/topics/chat-events"
DEFAULT_AUTHORITY: Final[str] = "relay@example.com"

_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------------------


def rfc3339_from_epoch_ms(epoch_ms: int) -> str:
	"""``1754740800000`` → ``"2026-08-09T12:00:00.000000Z"``.

	Lives here rather than in :mod:`.fake_chat` so the fake's emitted timestamps and these
	fixtures are formatted by one function. Two formatters is how a test starts passing
	against a shape production will never see.

	Reads no clock. The whole harness is deterministic, which means every timestamp is
	derived from an injected clock's integer milliseconds and never from ``now()``.
	"""
	moment = _EPOCH + timedelta(milliseconds=int(epoch_ms))
	return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{moment.microsecond:06d}Z"


def encode_data(payload: Mapping[str, Any]) -> str:
	"""Inner payload → the base64 string that sits in ``message.data``.

	Compact separators: **inferred**, not documented (see the module docstring). Standard
	base64 with padding — Pub/Sub's REST representation of ``bytes``, which is
	base64-with-padding rather than the URL-safe variant.
	"""
	raw = json.dumps(dict(payload), separators=(",", ":"), sort_keys=False).encode("utf-8")
	return base64.b64encode(raw).decode("ascii")


def decode_data(encoded: str) -> dict[str, Any]:
	"""``message.data`` → the inner payload. The inverse of :func:`encode_data`.

	Raises :class:`ValueError` on anything that is not base64 of a JSON object, because the
	parser's malformed-input path is a real path — ``MALFORMED_ENVELOPES`` below feeds it —
	and a helper that silently returned ``{}`` would hide exactly the case being tested.
	"""
	try:
		raw = base64.b64decode(str(encoded), validate=True)
	except (binascii.Error, TypeError, ValueError) as exc:
		raise ValueError(f"message.data is not valid base64: {exc}") from None
	try:
		parsed = json.loads(raw.decode("utf-8"))
	except (UnicodeDecodeError, ValueError) as exc:
		raise ValueError(f"message.data does not decode to JSON: {exc}") from None
	if not isinstance(parsed, dict):
		raise ValueError(f"message.data decoded to {type(parsed).__name__}, expected an object")
	return parsed


def space_event_id(space: str, event_uid: str) -> str:
	"""``spaces/{space}/spaceEvents/{id}`` — the documented shape of ``ce-id``.

	It is also the natural inbound idempotency key: Pub/Sub's own ``messageId`` changes on a
	redelivery, this does not.
	"""
	return f"{space}/spaceEvents/{event_uid}"


def pubsub_envelope(
	*,
	event_type: str,
	data: Mapping[str, Any],
	subject: str,
	event_id: str,
	publish_time: str = DEFAULT_PUBLISH_TIME,
	pubsub_message_id: str = DEFAULT_PUBSUB_MESSAGE_ID,
	ordering_key: str | None = None,
	subscription_id: str = DEFAULT_SUBSCRIPTION_ID,
	extra_attributes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
	"""Wrap **any** inner payload in the two-layer Pub/Sub envelope.

	This is the helper the fake emits through and the helper every fixture below is built
	from, so there is exactly one construction of the envelope in the repo. When the first
	captured event contradicts it, one function changes and every fixture and every emitted
	event moves with it.

	``ordering_key`` defaults to ``subject`` — the space — which is the inferred behaviour
	and the only one that would be useful: per-space ordering is what makes Rule 1
	(CREATE-BEFORE-EDIT) survivable on the inbound side. Nothing may *depend* on it; the
	relay orders by ``Chat Message.seq``, never by arrival.
	"""
	attributes: dict[str, str] = {
		"ce-id": event_id,
		"ce-source": f"//{EVENTS_HOST}/subscriptions/{subscription_id}",
		"ce-type": event_type,
		"ce-subject": subject,
		"ce-specversion": CE_SPECVERSION,
		"ce-datacontenttype": CE_DATACONTENTTYPE,
	}
	attributes.update({str(k): str(v) for k, v in (extra_attributes or {}).items()})
	return {
		"message": {
			"attributes": attributes,
			"data": encode_data(data),
			"messageId": str(pubsub_message_id),
			"orderingKey": ordering_key if ordering_key is not None else subject,
			"publishTime": str(publish_time),
		}
	}


# --------------------------------------------------------------------------------------
# Chat resource events — includeResource: false, so a NAME and nothing else
# --------------------------------------------------------------------------------------


def message_created_event(
	*,
	space: str = DEFAULT_SPACE,
	message: str = DEFAULT_MESSAGE,
	event_uid: str = "evtCreated01",
	publish_time: str = DEFAULT_PUBLISH_TIME,
	pubsub_message_id: str = DEFAULT_PUBSUB_MESSAGE_ID,
	subscription_id: str = DEFAULT_SUBSCRIPTION_ID,
) -> dict[str, Any]:
	"""A new message in a mirrored space.

	**There is no body here, and that is the design, not an omission.**
	``includeResource: false`` is what buys the 7-day subscription TTL (4 hours with the
	resource, 24 hours with the resource *and* DWD — the 24h figure raises the
	include-resource ceiling, not the 7-day one). The price is one
	``spaces.messages.get`` per inbound event, budgeted against the 3,000-reads-per-minute
	project quota, and the collapse of the prompt's "client id in the payload" layer:
	there is no ``clientAssignedMessageId`` to read here, so echo suppression cannot begin
	until the resource has been fetched.
	"""
	return pubsub_envelope(
		event_type=EVENT_MESSAGE_CREATED,
		data={"message": {"name": message}},
		subject=space,
		event_id=space_event_id(space, event_uid),
		publish_time=publish_time,
		pubsub_message_id=pubsub_message_id,
		subscription_id=subscription_id,
	)


def message_updated_event(
	*,
	space: str = DEFAULT_SPACE,
	message: str = DEFAULT_MESSAGE,
	event_uid: str = "evtUpdated01",
	publish_time: str = DEFAULT_PUBLISH_TIME,
	pubsub_message_id: str = "9000000000000002",
	subscription_id: str = DEFAULT_SUBSCRIPTION_ID,
) -> dict[str, Any]:
	"""An edit. Byte-identical to a create except for ``ce-type``.

	Which is exactly why ``decisions.idempotency_key`` keys an ``updated`` event on the
	resource name **plus** ``lastUpdateTime`` — and why that field can only be learned from
	the ``messages.get``, never from the event. Two edits to one message produce two events
	that differ in nothing an inbound worker can see before it fetches.
	"""
	return pubsub_envelope(
		event_type=EVENT_MESSAGE_UPDATED,
		data={"message": {"name": message}},
		subject=space,
		event_id=space_event_id(space, event_uid),
		publish_time=publish_time,
		pubsub_message_id=pubsub_message_id,
		subscription_id=subscription_id,
	)


def message_deleted_event(
	*,
	space: str = DEFAULT_SPACE,
	message: str = DEFAULT_MESSAGE,
	event_uid: str = "evtDeleted01",
	publish_time: str = DEFAULT_PUBLISH_TIME,
	pubsub_message_id: str = "9000000000000003",
	subscription_id: str = DEFAULT_SUBSCRIPTION_ID,
) -> dict[str, Any]:
	"""A delete.

	**SETTLED live on 2026-08-09** (``PHASE2_VERIFIED.md`` §8.3): a ``subscriptions.create``
	with ``validateOnly=true`` for ``eventTypes: [...message.v1.deleted]``, under the scope
	set the relay actually holds, returned **HTTP 200** from the production VM. **Inbound
	delete propagation is authorised** and this fixture describes traffic we will really
	receive — it is not speculative.

	What remains open is only the *minimal* scope. The Workspace Events scope table lists
	``created`` and ``updated`` under MESSAGES and **omits ``deleted`` and ``batchDeleted``
	entirely** — a reproducible documentation gap, not a research miss — and the narrower
	``chat.messages.readonly`` could not be tested because it is deliberately absent from the
	delegation grant, which holds the broader ``chat.messages``. Nothing in the design needs
	the minimal scope, so the gap is recorded rather than chased.

	The reconciliation sweep stays, but as defence in depth against a missed *delivery*, not
	as a fallback for an unauthorised *event type*.
	"""
	return pubsub_envelope(
		event_type=EVENT_MESSAGE_DELETED,
		data={"message": {"name": message}},
		subject=space,
		event_id=space_event_id(space, event_uid),
		publish_time=publish_time,
		pubsub_message_id=pubsub_message_id,
		subscription_id=subscription_id,
	)


def membership_created_event(
	*,
	space: str = DEFAULT_SPACE,
	membership: str = DEFAULT_MEMBERSHIP,
	event_uid: str = "evtMember01",
	publish_time: str = DEFAULT_PUBLISH_TIME,
	pubsub_message_id: str = "9000000000000004",
	subscription_id: str = DEFAULT_SUBSCRIPTION_ID,
) -> dict[str, Any]:
	"""Somebody joined a mirrored space — the trigger for §4.H membership reconciliation.

	The membership **name** is all that arrives, and a membership name is
	``spaces/{space}/members/{id}`` where the id is Google's opaque user id, **not** an
	email address. So resolving this to an ERPNext ``User`` costs a
	``spaces.members.get`` (or a ``list``) as well, and a member with no ERPNext user is the
	case ``Chat Message.sender_email`` exists for.
	"""
	return pubsub_envelope(
		event_type=EVENT_MEMBERSHIP_CREATED,
		data={"membership": {"name": membership}},
		subject=space,
		event_id=space_event_id(space, event_uid),
		publish_time=publish_time,
		pubsub_message_id=pubsub_message_id,
		subscription_id=subscription_id,
	)


def membership_deleted_event(
	*,
	space: str = DEFAULT_SPACE,
	membership: str = DEFAULT_MEMBERSHIP,
	event_uid: str = "evtMember02",
	publish_time: str = DEFAULT_PUBLISH_TIME,
	pubsub_message_id: str = "9000000000000005",
	subscription_id: str = DEFAULT_SUBSCRIPTION_ID,
) -> dict[str, Any]:
	"""Somebody left, or was removed.

	Whether ERPNext follows depends on ``Chat Room.membership_authority``: under
	``ERPNext`` this is a divergence to be reverted (bounded by the revert cap, so two
	systems cannot fight forever), under ``Bidirectional`` it is an instruction.
	"""
	return pubsub_envelope(
		event_type=EVENT_MEMBERSHIP_DELETED,
		data={"membership": {"name": membership}},
		subject=space,
		event_id=space_event_id(space, event_uid),
		publish_time=publish_time,
		pubsub_message_id=pubsub_message_id,
		subscription_id=subscription_id,
	)


def space_updated_event(
	*,
	space: str = DEFAULT_SPACE,
	event_uid: str = "evtSpace01",
	publish_time: str = DEFAULT_PUBLISH_TIME,
	pubsub_message_id: str = "9000000000000006",
	subscription_id: str = DEFAULT_SUBSCRIPTION_ID,
) -> dict[str, Any]:
	"""A space was renamed or otherwise patched. Name only, same as everything else."""
	return pubsub_envelope(
		event_type=EVENT_SPACE_UPDATED,
		data={"space": {"name": space}},
		subject=space,
		event_id=space_event_id(space, event_uid),
		publish_time=publish_time,
		pubsub_message_id=pubsub_message_id,
		subscription_id=subscription_id,
	)


# --------------------------------------------------------------------------------------
# Subscription lifecycle — same topic, different type, snake_case payload
# --------------------------------------------------------------------------------------


def _subscription_resource(
	*,
	subscription_id: str,
	state: str,
	expire_time: str,
	target_resource: str,
	pubsub_topic: str,
	authority: str,
	event_types: Sequence[str],
) -> dict[str, Any]:
	"""The Subscription resource as it appears inside a lifecycle payload.

	**snake_case**, deliberately: ``PHASE2_VERIFIED.md`` §7 records that lifecycle payloads
	use snake_case inside the subscription object while every Chat resource event uses
	camelCase. A parser that normalises one convention will silently read ``None`` from the
	other, and the field it will read ``None`` from is ``expire_time`` — the one the renewal
	scheduler derives its period from.
	"""
	return {
		"name": f"subscriptions/{subscription_id}",
		"uid": subscription_id,
		"target_resource": target_resource,
		"event_types": list(event_types),
		"payload_options": {"include_resource": False},
		"notification_endpoint": {"pubsub_topic": pubsub_topic},
		"state": state,
		"expire_time": expire_time,
		"authority": authority,
		"reconciling": False,
	}


def subscription_expiration_reminder_event(
	*,
	subscription_id: str = DEFAULT_SUBSCRIPTION_ID,
	expire_time: str = "2026-08-16T12:00:00.000000Z",
	target_resource: str = SUBSCRIPTION_TARGET_RESOURCE,
	pubsub_topic: str = DEFAULT_PUBSUB_TOPIC,
	authority: str = DEFAULT_AUTHORITY,
	event_uid: str = "evtLifecycle01",
	publish_time: str = DEFAULT_PUBLISH_TIME,
	pubsub_message_id: str = "9000000000000007",
) -> dict[str, Any]:
	"""T−12h and T−1h before expiry. **Fires twice**, so renewal must be idempotent.

	This is the event the whole inbound pipeline's continuity rests on: a subscription that
	expires unnoticed produces no error anywhere — inbound simply goes quiet, and the first
	symptom is a coworker asking why their Chat reply never appeared in ERPNext. Renewal
	reads ``expire_time`` off the response and reschedules from **that**, never from
	``Chat Settings.subscription_ttl_seconds``, which is a request and not a promise.

	``ce-subject`` on a lifecycle event is the **subscription**, not a space — inferred, and
	the reason the parser's ``subject`` field cannot be assumed to be a space name.
	"""
	subscription = _subscription_resource(
		subscription_id=subscription_id,
		state="ACTIVE",
		expire_time=expire_time,
		target_resource=target_resource,
		pubsub_topic=pubsub_topic,
		authority=authority,
		event_types=[EVENT_MESSAGE_CREATED, EVENT_MESSAGE_UPDATED, EVENT_MESSAGE_DELETED],
	)
	return pubsub_envelope(
		event_type=EVENT_SUBSCRIPTION_EXPIRATION_REMINDER,
		data={"subscription": subscription},
		subject=f"subscriptions/{subscription_id}",
		event_id=f"subscriptions/{subscription_id}/events/{event_uid}",
		publish_time=publish_time,
		pubsub_message_id=pubsub_message_id,
		ordering_key="",
		subscription_id=subscription_id,
	)


def subscription_suspended_event(
	*,
	subscription_id: str = DEFAULT_SUBSCRIPTION_ID,
	expire_time: str = "2026-08-16T12:00:00.000000Z",
	error_type: str = "USER_SCOPE_REVOKED",
	target_resource: str = SUBSCRIPTION_TARGET_RESOURCE,
	pubsub_topic: str = DEFAULT_PUBSUB_TOPIC,
	authority: str = DEFAULT_AUTHORITY,
	event_uid: str = "evtLifecycle02",
	publish_time: str = DEFAULT_PUBLISH_TIME,
	pubsub_message_id: str = "9000000000000008",
) -> dict[str, Any]:
	"""Suspended — recoverable by ``subscriptions.reactivate``, but only until it expires.

	A suspended subscription still counts against the per-user cap and still expires on the
	original schedule, so "suspended" is a countdown, not a pause. ``suspension_reason`` is
	**inferred** as a snake_case sibling of the other fields; the enum member spelled here is
	a plausible one and is not quoted from any document.
	"""
	subscription = _subscription_resource(
		subscription_id=subscription_id,
		state="SUSPENDED",
		expire_time=expire_time,
		target_resource=target_resource,
		pubsub_topic=pubsub_topic,
		authority=authority,
		event_types=[EVENT_MESSAGE_CREATED, EVENT_MESSAGE_UPDATED, EVENT_MESSAGE_DELETED],
	)
	subscription["suspension_reason"] = error_type
	return pubsub_envelope(
		event_type=EVENT_SUBSCRIPTION_SUSPENDED,
		data={"subscription": subscription},
		subject=f"subscriptions/{subscription_id}",
		event_id=f"subscriptions/{subscription_id}/events/{event_uid}",
		publish_time=publish_time,
		pubsub_message_id=pubsub_message_id,
		ordering_key="",
		subscription_id=subscription_id,
	)


def subscription_expired_event(
	*,
	subscription_id: str = DEFAULT_SUBSCRIPTION_ID,
	target_resource: str = SUBSCRIPTION_TARGET_RESOURCE,
	pubsub_topic: str = DEFAULT_PUBSUB_TOPIC,
	authority: str = DEFAULT_AUTHORITY,
	event_uid: str = "evtLifecycle03",
	publish_time: str = DEFAULT_PUBLISH_TIME,
	pubsub_message_id: str = "9000000000000009",
) -> dict[str, Any]:
	"""Expired. Terminal — there is nothing to reactivate, only a new subscription to create.

	Every event that occurred while it was down is **gone**; Workspace Events has no replay.
	Recovery is the §4.J reconciliation sweep over ``spaces.messages.list``, bounded by
	``Chat Room.last_reconcile_at``, and that is the only reason the sweep exists.
	"""
	subscription = _subscription_resource(
		subscription_id=subscription_id,
		state="EXPIRED",
		expire_time=DEFAULT_PUBLISH_TIME,
		target_resource=target_resource,
		pubsub_topic=pubsub_topic,
		authority=authority,
		event_types=[EVENT_MESSAGE_CREATED, EVENT_MESSAGE_UPDATED, EVENT_MESSAGE_DELETED],
	)
	return pubsub_envelope(
		event_type=EVENT_SUBSCRIPTION_EXPIRED,
		data={"subscription": subscription},
		subject=f"subscriptions/{subscription_id}",
		event_id=f"subscriptions/{subscription_id}/events/{event_uid}",
		publish_time=publish_time,
		pubsub_message_id=pubsub_message_id,
		ordering_key="",
		subscription_id=subscription_id,
	)


# --------------------------------------------------------------------------------------
# Malformed input — a real path, not an edge case
# --------------------------------------------------------------------------------------


def malformed_event() -> dict[str, Any]:
	"""The canonical bad envelope: an inner payload that is not base64 at all.

	It exists because the consequence of getting this wrong is disproportionate. The Pub/Sub
	consumer acks or nacks per message; an unhandled exception on one poison payload nacks
	it forever and, on a subscription shared with anything else, stalls the whole stream.
	``parse_pubsub_envelope`` must raise ``MalformedEvent`` — a *named* failure the consumer
	can dead-letter — and never return a partially-populated ``ParsedEvent`` that the rest
	of the pipeline would then act on.
	"""
	envelope = message_created_event()
	envelope["message"]["data"] = "this is not base64 %%%%"
	return envelope


def malformed_events() -> list[tuple[str, dict[str, Any]]]:
	"""``(why_it_is_broken, envelope)`` for every malformed shape worth a test.

	A list rather than one fixture because the failures are genuinely different: a missing
	outer key is a routing mistake, a missing ``ce-type`` is an unparseable event, and
	base64 of valid JSON that is not an object is the one a naive ``json.loads`` accepts and
	then indexes into.
	"""
	cases: list[tuple[str, dict[str, Any]]] = []

	empty: dict[str, Any] = {}
	cases.append(("no outer 'message' key at all", empty))

	no_data = message_created_event()
	del no_data["message"]["data"]
	cases.append(("outer envelope present, 'data' missing", no_data))

	cases.append(("'data' is not base64", malformed_event()))

	not_json = message_created_event()
	not_json["message"]["data"] = base64.b64encode(b"<html>502 Bad Gateway</html>").decode("ascii")
	cases.append(("'data' base64-decodes to something that is not JSON", not_json))

	not_object = message_created_event()
	not_object["message"]["data"] = base64.b64encode(b'["a","list"]').decode("ascii")
	cases.append(("'data' decodes to a JSON array, not an object", not_object))

	no_type = message_created_event()
	del no_type["message"]["attributes"]["ce-type"]
	cases.append(("no ce-type, so the event cannot be routed", no_type))

	unknown_type = message_created_event()
	unknown_type["message"]["attributes"]["ce-type"] = "google.workspace.chat.reaction.v1.created"
	cases.append(("a ce-type this build has never seen (reactions are not subscribed)", unknown_type))

	no_name = message_created_event()
	no_name["message"]["data"] = encode_data({"message": {}})
	cases.append(("inner payload present but carries no resource name", no_name))

	return cases


# --------------------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------------------

#: Every well-formed fixture, by ``ce-type``. A parser test loops this rather than naming
#: fixtures one by one, so a new event type added here is a new event type tested — the
#: "we added a case and forgot the test" failure cannot happen silently.
ALL_EVENT_FACTORIES: Final[Mapping[str, Callable[[], dict[str, Any]]]] = {
	EVENT_MESSAGE_CREATED: message_created_event,
	EVENT_MESSAGE_UPDATED: message_updated_event,
	EVENT_MESSAGE_DELETED: message_deleted_event,
	EVENT_MEMBERSHIP_CREATED: membership_created_event,
	EVENT_MEMBERSHIP_DELETED: membership_deleted_event,
	EVENT_SPACE_UPDATED: space_updated_event,
	EVENT_SUBSCRIPTION_EXPIRATION_REMINDER: subscription_expiration_reminder_event,
	EVENT_SUBSCRIPTION_SUSPENDED: subscription_suspended_event,
	EVENT_SUBSCRIPTION_EXPIRED: subscription_expired_event,
}
