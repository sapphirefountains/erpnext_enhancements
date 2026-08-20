# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Google **Workspace Events** API — subscriptions, and only subscriptions.

A different host from ``chat/gchat/client.py`` (``workspaceevents.googleapis.com`` rather
than ``chat.googleapis.com``) and therefore a different module, because
``tests/test_chat_guardrails.py`` confines each Google host to exactly one place. That is
the same containment rule the Chat transport lives under: one module means one place to
reason about identity and quota, and a bounded set a reviewer has to read to know
everything this app sends to Google.

One socket, still
-----------------
This module builds calls; it does **not** open a socket. Every builder returns a
:class:`~erpnext_enhancements.chat.gchat.client.GoogleCall` with ``host=`` set, and
:class:`WorkspaceEventsClient` hands it to
:meth:`~erpnext_enhancements.chat.gchat.client.GoogleChatClient.execute`. So the retry
loop, the backoff table, the ``Authorization`` header, the structured log line, the
``raise … from None`` discipline and the dry-run short-circuit are the *same code* the
Chat calls run through — one place to patch to prove dry-run performs zero network I/O,
and no second implementation to drift out of step. Errors surface as the existing
``GoogleChatError`` family for the same reason: a relay job catching one exception type
catches everything that came from Google.

Why a subscription exists at all, in one paragraph
--------------------------------------------------
Google Chat has no outbound webhook for message events. The only supported inbound path
is a Workspace Events subscription publishing to Pub/Sub, and the shape this design uses
is **one ``spaces/-`` subscription per coworker, user-authenticated** — shape B of
PHASE2_CONTRACT.md §2. Shape A needs Marketplace admin-install approval for the
``chat.app.*`` scope family; shape C needs Developer Preview plus an Enterprise SKU.
Neither is a fallback we can reach for, so every method here pins
``requires_identity=USER``.

TTL: ask for nothing, read what you were given
-----------------------------------------------
``ttl`` is **input only**, and unspecified (or ``0s``) means *"use the maximum possible
duration"*. So the builders **omit it by default** and the published figures are read as
what they are — ceilings, prefixed "up to", with nothing guaranteeing the server grants
one:

===================================================  ==============
``includeResource: false``                           up to 7 days
``includeResource: true``                            up to 4 hours
``includeResource: true`` + domain-wide delegation   up to 24 hours
===================================================  ==============

**The 24-hour figure extends the ``includeResource: true`` branch. It does not raise the
7-day one**, so under this design's ``includeResource: false`` domain-wide delegation buys
no TTL benefit at all; the DWD case rests on attribution alone. That reading is easy to
get backwards and expensive to get backwards, because getting it backwards makes the
4-hour configuration look free.

The consequence for code: **nothing may schedule a renewal from a constant.** Every method
that returns a subscription returns a :class:`Subscription`, and a :class:`Subscription`
cannot be constructed without an ``expireTime`` — :func:`parse_subscription` raises
:class:`SubscriptionExpiryUnknown` rather than hand back a resource whose expiry the
caller would then have to invent. ``Chat Settings.subscription_ttl_seconds`` is a
*request*, and ``Chat Event Subscription.expire_time`` is the fact.

Quota
-----
600 subscription calls per minute project-wide, 100 per minute per user. With ~20
coworkers that is not a constraint on steady-state renewal and *is* a constraint on a
first-run bulk provisioning sweep, which must therefore be throttled and resumable like
every other bulk sweep in Phase 2.

Nothing here logs a body, a token, or a query string.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from erpnext_enhancements.chat.gchat import dryrun
from erpnext_enhancements.chat.gchat.client import (
	AuthIdentity,
	GoogleCall,
	GoogleChatAPIError,
	GoogleChatClient,
	GoogleChatError,
	TokenProvider,
)

# The one occurrence of this host in the app. tests/test_chat_guardrails.py asserts it, the
# same way it asserts the Chat host against client.py.
WORKSPACE_EVENTS_HOST: Final[str] = "https://workspaceevents.googleapis.com"
WORKSPACE_EVENTS_VERSION: Final[str] = "v1"

# Chat resource events. Requested explicitly; a subscription receives exactly what it asked
# for and nothing else.
EVENT_TYPE_MESSAGE_CREATED: Final[str] = "google.workspace.chat.message.v1.created"
EVENT_TYPE_MESSAGE_UPDATED: Final[str] = "google.workspace.chat.message.v1.updated"
EVENT_TYPE_MESSAGE_DELETED: Final[str] = "google.workspace.chat.message.v1.deleted"
EVENT_TYPE_MEMBERSHIP_CREATED: Final[str] = "google.workspace.chat.membership.v1.created"
EVENT_TYPE_MEMBERSHIP_DELETED: Final[str] = "google.workspace.chat.membership.v1.deleted"
EVENT_TYPE_SPACE_UPDATED: Final[str] = "google.workspace.chat.space.v1.updated"

#: What Phase 2's inbound pipeline subscribes to. Membership and space events are declared
#: above but deliberately **not** in this tuple: §4.H reconciles membership on a sweep, and
#: adding an event type to a live subscription is a ``patch``, not a redeploy.
#:
#: ``VERIFY:`` which OAuth scope authorises ``message.v1.deleted``. Google's scope table
#: lists ``created`` and ``updated`` under MESSAGES and omits ``deleted`` and
#: ``batchDeleted`` entirely — a reproducible documentation gap, not a research miss. If the
#: create call 403s, drop to the first two and record that inbound delete propagation is
#: blocked (PHASE2_VERIFIED.md §8.3).
MESSAGE_EVENT_TYPES: Final[tuple[str, ...]] = (
	EVENT_TYPE_MESSAGE_CREATED,
	EVENT_TYPE_MESSAGE_UPDATED,
	EVENT_TYPE_MESSAGE_DELETED,
)

#: Subscription lifecycle events. **Never requested** — Google delivers them to the same
#: topic whether you ask or not, and naming one in ``eventTypes`` is a 400. They arrive with
#: ``snake_case`` field names inside the subscription object, unlike every Chat resource,
#: which is a real fixture-shaping detail rather than a curiosity (PHASE2_VERIFIED.md §7).
#: ``expirationReminder`` fires twice: at T−12h and at T−1h.
LIFECYCLE_EVENT_TYPE_PREFIX: Final[str] = "google.workspace.events.subscription.v1."
EVENT_TYPE_EXPIRATION_REMINDER: Final[str] = f"{LIFECYCLE_EVENT_TYPE_PREFIX}expirationReminder"
EVENT_TYPE_SUBSCRIPTION_SUSPENDED: Final[str] = f"{LIFECYCLE_EVENT_TYPE_PREFIX}suspended"
EVENT_TYPE_SUBSCRIPTION_EXPIRED: Final[str] = f"{LIFECYCLE_EVENT_TYPE_PREFIX}expired"
LIFECYCLE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
	{EVENT_TYPE_EXPIRATION_REMINDER, EVENT_TYPE_SUBSCRIPTION_SUSPENDED, EVENT_TYPE_SUBSCRIPTION_EXPIRED}
)

#: Every subscribable event type is under this namespace. Checked as a prefix rather than
#: against a closed list: Google adds event types faster than this file gets read, and a
#: closed list would refuse a legitimate one while a typo'd ``google.workspace.chat.mesage``
#: passes either check equally. The prefix catches the failure that happens (a Pub/Sub topic
#: name or a resource name pasted into ``eventTypes``).
EVENT_TYPE_NAMESPACE: Final[str] = "google.workspace."

SUBSCRIPTION_STATE_ACTIVE: Final[str] = "ACTIVE"
SUBSCRIPTION_STATE_SUSPENDED: Final[str] = "SUSPENDED"
SUBSCRIPTION_STATE_DELETED: Final[str] = "DELETED"

#: subscriptions.list default. The roster is ~20, so a page size that fits it in one call
#: keeps the reconciliation read to a single request.
DEFAULT_SUBSCRIPTION_PAGE_SIZE: Final[int] = 50
MAX_SUBSCRIPTION_PAGE_SIZE: Final[int] = 100

# `subscriptions/{subscription}` — the only resource name shape this API produces.
_SUBSCRIPTION_NAME_RE: Final[re.Pattern[str]] = re.compile(r"subscriptions/[A-Za-z0-9_-]+")

# `//<service>.googleapis.com/spaces/<id-or-dash>`. Shape-checked without naming the Chat
# host, which lives in client.py and may not appear here — compose the value with
# `client.chat_target_resource()` and pass it in.
_TARGET_RESOURCE_RE: Final[re.Pattern[str]] = re.compile(
	r"//[a-z0-9-]+\.googleapis\.com/spaces/(-|[A-Za-z0-9_-]+)"
)

# `projects/{project}/topics/{topic}`.
_PUBSUB_TOPIC_RE: Final[re.Pattern[str]] = re.compile(r"projects/[a-z0-9-]+/topics/[A-Za-z0-9._~%+-]+")

# A protobuf Duration in its JSON form: seconds, optional fraction, mandatory trailing `s`.
_TTL_RE: Final[re.Pattern[str]] = re.compile(r"\d+(\.\d+)?s")

#: Google's own spelling of "the maximum lifetime" for a ``ttl`` that has to be *present*.
#: Omitting the field says the same thing on ``create``, where ``ttl`` is simply an unset
#: input — but not on ``patch``, where naming ``ttl`` in ``updateMask`` is a promise that the
#: body carries it. See :func:`build_patch_subscription_call`.
MAX_TTL: Final[str] = "0s"

# RFC-3339 with an explicit offset. Parsed by hand rather than handed straight to
# `datetime.fromisoformat` because Google emits nanosecond precision and `fromisoformat`
# accepts 0, 3 or 6 fractional digits — a 9-digit `expireTime` raises, and it raises inside
# the renewal scheduler, which is the one place a crash means every subscription silently
# lapses.
_RFC3339_RE: Final[re.Pattern[str]] = re.compile(
	r"(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|z|[+-]\d{2}:?\d{2})"
)


class SubscriptionExpiryUnknown(GoogleChatError):
	"""Google answered without an ``expireTime``, so the renewal clock cannot be set.

	Raised rather than defaulted. Every published TTL figure is a ceiling — "up to 7 days" —
	and nothing guarantees the server granted one, so a default would be a guess about the
	single number that decides whether inbound sync keeps working. A subscription whose
	expiry is unknown is a subscription that will lapse unnoticed; failing here converts a
	silent outage in seven days' time into a loud failure now.
	"""


def _qbool(value: bool) -> str:
	"""Lowercase JSON booleans in the query string, as Google's REST transcoding wants.

	Two lines duplicated from ``client.py`` rather than imported: this module is allowed to
	depend on that one for *shared machinery* (the call type, the transport, the exception
	family), and reaching across a module boundary for a private formatting helper is how
	a private helper becomes a public contract nobody agreed to.
	"""
	return "true" if value else "false"


# --------------------------------------------------------------------------------------
# Pure helpers: validation
# --------------------------------------------------------------------------------------


def validate_subscription_name(name: str) -> str:
	"""``subscriptions/<id>``, and nothing that could smuggle a second path segment."""
	candidate = str(name or "").strip()
	if not _SUBSCRIPTION_NAME_RE.fullmatch(candidate):
		raise ValueError(
			f"Expected a subscription resource name of the form 'subscriptions/<id>'; got {candidate!r}."
		)
	return candidate


def validate_target_resource(target_resource: str) -> str:
	"""``//<service>.googleapis.com/spaces/<id>`` — what the subscription watches.

	Shape-checked here and *composed* elsewhere: the Chat host may appear in exactly one
	module and it is not this one, so the caller builds the value with
	``client.chat_target_resource()``. The regex names the ``googleapis.com`` suffix only,
	which is why this file passes the guardrail while still refusing a target that is a
	Pub/Sub topic, a bare space name, or a URL somebody typed.

	``spaces/-`` — every space the authenticated user is in — is **user-authentication
	only**, and there is no documented cap on subscriptions per user.
	"""
	candidate = str(target_resource or "").strip()
	if not _TARGET_RESOURCE_RE.fullmatch(candidate):
		raise ValueError(
			"targetResource must be a fully-qualified resource of the form "
			f"'//<service>.googleapis.com/spaces/<id>' (or '/spaces/-' for every space); got "
			f"{candidate!r}. Compose it with chat.gchat.client.chat_target_resource()."
		)
	return candidate


def validate_pubsub_topic(pubsub_topic: str) -> str:
	"""``projects/<project>/topics/<topic>``.

	**Two topics exist and they are not interchangeable** (PHASE2_CONTRACT.md §2): the
	Workspace Events firehose is bursty, Phase 5's interaction events are latency-sensitive
	and low-volume, and they are separate so a poison message on one cannot stall the other.
	This validator cannot tell them apart — ``Chat Settings`` save-time validation is what
	refuses the two fields holding the same value, and it exists because production briefly
	did hold the same value in both.
	"""
	candidate = str(pubsub_topic or "").strip()
	if not _PUBSUB_TOPIC_RE.fullmatch(candidate):
		raise ValueError(
			f"pubsubTopic must be 'projects/<project>/topics/<topic>'; got {candidate!r}. "
			"The publisher principal Google pushes as is chat-api-push@system.gserviceaccount.com; "
			"a topic that has not granted it publisher rights accepts the subscription and then "
			"delivers nothing."
		)
	return candidate


def validate_event_types(event_types: Sequence[str]) -> tuple[str, ...]:
	"""Normalise and check the requested event types, preserving the caller's order.

	Order is preserved rather than sorted so a diff of two subscriptions reads the way the
	caller wrote them. Duplicates are dropped silently — a repeated event type is a copy
	-paste, not an intent.

	A **lifecycle** event type is rejected outright. They are delivered to the topic whether
	subscribed or not, and naming one is a 400 whose message does not say why.
	"""
	seen: set[str] = set()
	kept: list[str] = []
	for raw in event_types or ():
		candidate = str(raw or "").strip()
		if not candidate:
			continue
		if candidate in LIFECYCLE_EVENT_TYPES or candidate.startswith(LIFECYCLE_EVENT_TYPE_PREFIX):
			raise ValueError(
				f"{candidate!r} is a subscription lifecycle event. Google delivers those to the topic "
				"automatically and rejects a subscription that asks for one; handle them on the "
				"consumer side instead."
			)
		if not candidate.startswith(EVENT_TYPE_NAMESPACE):
			raise ValueError(
				f"Event types are namespaced {EVENT_TYPE_NAMESPACE}…; got {candidate!r}. "
				"A resource name or a topic name pasted here is accepted by nothing and reads as a "
				"generic 400."
			)
		if candidate in seen:
			continue
		seen.add(candidate)
		kept.append(candidate)

	if not kept:
		raise ValueError("A subscription must name at least one event type; got none.")
	return tuple(kept)


def validate_ttl(ttl: str | None) -> str | None:
	"""A protobuf ``Duration`` (``"3600s"``), or ``None`` to request the maximum.

	``None`` is the intended value and the default everywhere in this module. ``ttl`` is
	input-only and unspecified means *"use the maximum possible duration"*, so asking for a
	shorter one buys nothing and costs a renewal.

	``"0s"`` — :data:`MAX_TTL` — is permitted because Google documents it as also meaning
	"the maximum". Which spelling is correct depends on the call: ``create`` omits the field,
	``patch`` **must** send it, because ``updateMask`` naming ``ttl`` is a promise that the
	body carries one. Returning ``None`` here means "caller's choice of spelling", and the
	two builders choose differently on purpose.
	"""
	if ttl is None:
		return None
	candidate = str(ttl).strip()
	if not _TTL_RE.fullmatch(candidate):
		raise ValueError(
			f"ttl must be a protobuf duration such as '3600s'; got {candidate!r}. Pass None — the "
			"default — to request the maximum, which is what this design wants in every case."
		)
	return candidate


def parse_rfc3339_epoch(value: str) -> float:
	"""RFC-3339 timestamp to a POSIX epoch float, tolerating nanosecond precision.

	Google emits UTC with a ``Z`` and up to nine fractional digits; ``datetime.fromisoformat``
	accepts zero, three or six. Truncating to microseconds before parsing is the whole trick,
	and truncation is right rather than rounding — a renewal must not be scheduled *after*
	the expiry it was derived from.

	Frappe's ``now_datetime()`` is **site-local**, not UTC. This app has already shipped one
	bug from that confusion (the Turnstile always-fail), so this returns an epoch — an
	absolute instant with no timezone to lose — rather than a naive datetime.
	"""
	text = str(value or "").strip()
	match = _RFC3339_RE.fullmatch(text)
	if not match:
		raise ValueError(f"Expected an RFC-3339 timestamp with an explicit offset; got {text!r}.")

	fraction = (match.group(7) or "")[:6].ljust(6, "0")
	offset = match.group(8)
	if offset in ("Z", "z"):
		offset = "+00:00"
	elif ":" not in offset:
		offset = f"{offset[:3]}:{offset[3:]}"

	normalised = (
		f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
		f"T{match.group(4)}:{match.group(5)}:{match.group(6)}.{fraction}{offset}"
	)
	return datetime.fromisoformat(normalised).timestamp()


# --------------------------------------------------------------------------------------
# Pure helpers: response parsing
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Subscription:
	"""One Workspace Events subscription, with its expiry **already read**.

	The type exists to make one mistake unrepresentable: scheduling a renewal from a
	constant. There is no way to hold a ``Subscription`` without also holding the
	``expireTime`` Google actually granted, because :func:`parse_subscription` refuses to
	build one otherwise.

	``expire_epoch`` is an absolute instant. Compare it against ``time.time()``, never
	against ``frappe.utils.now_datetime()``, which is site-local.
	"""

	name: str
	expire_time: str
	expire_epoch: float
	state: str
	uid: str = ""
	etag: str = ""
	target_resource: str = ""
	event_types: tuple[str, ...] = ()
	payload: Mapping[str, Any] = field(default_factory=dict)

	@property
	def is_dry_run(self) -> bool:
		"""``True`` for a synthetic subscription minted by dry-run mode.

		**The renewal scheduler must check this before acting.** A dry-run subscription's
		``expireTime`` is the Unix epoch — deliberately, because ``dryrun.py``'s rule is that
		a plausible-looking timestamp is the field most likely to make a fake read as real —
		which means it is *already expired* and a scheduler that does not check would renew
		it forever, at one Workspace Events call per pass.
		"""
		return bool(self.payload.get("dryRun")) or dryrun.is_dryrun_name(self.name)


def unwrap_operation(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
	"""Unwrap the long-running-operation envelope ``subscriptions.create`` answers with.

	``create``, ``patch``, ``reactivate`` and ``delete`` are declared as returning an
	``Operation``, not the resource; ``get`` and ``list`` return resources directly. In
	practice a Chat subscription operation comes back ``done: true`` with the subscription
	in ``response``, but "in practice" is not a contract, so both shapes are handled and an
	operation that is *not* done is an error rather than an empty result — a caller handed
	``{}`` would store a subscription with no name and never find it again.
	"""
	body = dict(payload or {})
	if "done" not in body and "response" not in body:
		return body

	error = body.get("error")
	if isinstance(error, Mapping):
		raise GoogleChatAPIError(
			f"Workspace Events operation failed: {error.get('status') or error.get('code') or ''} "
			f"{error.get('message') or ''}",
			google_status=str(error.get("status") or ""),
			client_method="unwrap_operation",
		) from None

	if not body.get("done"):
		raise GoogleChatAPIError(
			"Workspace Events returned an operation that is not done. Nothing here polls "
			f"operations — {body.get('name') or '<unnamed>'} would have to be polled before its "
			"subscription exists, and no code path is written for that.",
			client_method="unwrap_operation",
		) from None

	response = body.get("response")
	return dict(response) if isinstance(response, Mapping) else {}


def parse_subscription(payload: Mapping[str, Any] | None, *, client_method: str = "") -> Subscription:
	"""A response — bare resource or operation envelope — to a :class:`Subscription`.

	Raises :class:`SubscriptionExpiryUnknown` when ``expireTime`` is missing or unparseable.
	That is the whole point of routing every read through here: see the module docstring's
	TTL section, and PHASE2_VERIFIED.md §1.4.
	"""
	resource = unwrap_operation(payload)
	name = str(resource.get("name") or "")
	expire_time = str(resource.get("expireTime") or "")

	if not name:
		raise SubscriptionExpiryUnknown(
			"Workspace Events returned a subscription with no name; there is nothing to store or "
			"renew. Response keys: " + ", ".join(sorted(resource)) + ".",
			client_method=client_method,
		)
	if not expire_time:
		raise SubscriptionExpiryUnknown(
			f"{name} came back with no expireTime, so the renewal clock cannot be set. Every "
			"published TTL figure is a ceiling ('up to 7 days') and nothing guarantees the server "
			"granted one, so defaulting here would be guessing the single number that decides "
			"whether inbound sync keeps working.",
			client_method=client_method,
		)

	try:
		expire_epoch = parse_rfc3339_epoch(expire_time)
	except ValueError as exc:
		raise SubscriptionExpiryUnknown(
			f"{name} came back with an unparseable expireTime: {exc}",
			client_method=client_method,
		) from None

	event_types = resource.get("eventTypes")
	return Subscription(
		name=name,
		expire_time=expire_time,
		expire_epoch=expire_epoch,
		state=str(resource.get("state") or ""),
		uid=str(resource.get("uid") or ""),
		etag=str(resource.get("etag") or ""),
		target_resource=str(resource.get("targetResource") or ""),
		event_types=tuple(str(t) for t in event_types) if isinstance(event_types, Sequence) else (),
		payload=resource,
	)


def _synthetic_subscription(
	*,
	seed: Sequence[str],
	target_resource: str,
	event_types: Sequence[str],
	pubsub_topic: str,
	include_resource: bool,
) -> dict[str, Any]:
	"""A visibly fake ``Subscription`` resource for dry-run, shaped like the real one.

	``expireTime`` is the Unix epoch, matching ``dryrun.DRYRUN_CREATE_TIME`` and matching
	that module's rule: the timestamp is the field most likely to make a fake row read as
	real, so it is the one that must be obviously wrong. The consequence — a dry-run
	subscription is born expired — is handled by :attr:`Subscription.is_dry_run`, which the
	renewal scheduler checks first.
	"""
	digest = dryrun.dryrun_hash(*seed)
	return {
		"name": f"subscriptions/{dryrun.DRYRUN_MARKER}{digest}",
		"uid": f"{dryrun.DRYRUN_MARKER}{digest}",
		"targetResource": target_resource,
		"eventTypes": list(event_types),
		"notificationEndpoint": {"pubsubTopic": pubsub_topic},
		"payloadOptions": {"includeResource": include_resource},
		"state": SUBSCRIPTION_STATE_ACTIVE,
		"expireTime": dryrun.DRYRUN_CREATE_TIME,
		"etag": f"{dryrun.DRYRUN_MARKER}{digest[:8]}",
		"dryRun": True,
	}


def _synthetic_operation(response: Mapping[str, Any], *seed: str) -> dict[str, Any]:
	"""Wrap a dry-run resource in the operation envelope the real API returns.

	Shaped like the real thing on purpose: the unwrap path in :func:`unwrap_operation` is
	then exercised by every dry-run call rather than only by the tests that remember to
	construct an envelope by hand.
	"""
	return {
		"name": f"operations/{dryrun.DRYRUN_MARKER}{dryrun.dryrun_hash(*seed)}",
		"done": True,
		"response": dict(response),
		"dryRun": True,
	}


# --------------------------------------------------------------------------------------
# Pure helpers: call construction
# --------------------------------------------------------------------------------------


def build_create_subscription_call(
	*,
	target_resource: str,
	event_types: Sequence[str],
	pubsub_topic: str,
	include_resource: bool = False,
	ttl: str | None = None,
	validate_only: bool = False,
) -> GoogleCall:
	"""``POST /v1/subscriptions`` — subscribe to Chat events for one principal.

	``include_resource=False`` is the shipped configuration and the default here, and the
	binding reason is the TTL table in this module's docstring: it is the **only**
	configuration with a 7-day ceiling. Setting it ``True`` drops the ceiling to 4 hours
	(24 with DWD) and buys a message body ERPNext then has to reconcile against its own
	copy anyway. The cost of ``False`` is one ``spaces.messages.get`` per event, which was
	budgeted against the 3,000-reads-per-minute bucket.

	``ttl=None`` **omits the field**, which is how you ask for the maximum. Read
	``expireTime`` off the response — :func:`parse_subscription` will not let you skip it.

	``validate_only=True`` performs every check and creates nothing. That is how
	``scripts/c2_events_subscription_check.py`` settles the open question of whether a
	DWD-impersonated user may create a ``spaces/-`` subscription at all — the docs say the
	target supports user authentication and DWD *is* user authentication, but no page states
	the combination and the entire inbound design rests on it (PHASE2_VERIFIED.md §8.2).
	"""
	target = validate_target_resource(target_resource)
	types = validate_event_types(event_types)
	topic = validate_pubsub_topic(pubsub_topic)
	duration = validate_ttl(ttl)

	body: dict[str, Any] = {
		"targetResource": target,
		"eventTypes": list(types),
		"notificationEndpoint": {"pubsubTopic": topic},
		"payloadOptions": {"includeResource": bool(include_resource)},
	}
	if duration is not None:
		body["ttl"] = duration

	resource = _synthetic_subscription(
		seed=(target, topic, *types),
		target_resource=target,
		event_types=types,
		pubsub_topic=topic,
		include_resource=bool(include_resource),
	)
	return GoogleCall(
		client_method="create_subscription",
		google_method="workspaceevents.subscriptions.create",
		http_method="POST",
		path=f"/{WORKSPACE_EVENTS_VERSION}/subscriptions",
		query={"validateOnly": _qbool(validate_only)},
		body=body,
		host=WORKSPACE_EVENTS_HOST,
		requires_identity=AuthIdentity.USER,
		dry_run_payload=_synthetic_operation(resource, "create", target, topic),
	)


def build_patch_subscription_call(
	name: str,
	*,
	ttl: str | None,
	update_mask: str = "ttl",
	validate_only: bool = False,
) -> GoogleCall:
	"""``PATCH /v1/{subscription.name=subscriptions/*}`` — the renewal.

	Renewal is a patch of ``ttl``, and ``ttl=None`` again means "the maximum". A renewal
	that asked for the same short window every time would be a scheduled job whose only
	achievement is more scheduled jobs.

	``ttl`` is **keyword-only and has no default** so that "renew for how long?" is a
	decision the caller makes visibly, even when the answer is ``None``.

	``VERIFY:`` which field paths ``updateMask`` accepts beyond ``ttl``. ``eventTypes`` is
	plausible and unread; ``expireTime`` is the other half of the ``expiration`` union and
	may or may not be settable. Nothing in Phase 2 needs either — a change of event types is
	a delete and a create, which is also the only way to be sure what the new subscription
	is watching.
	"""
	subscription = validate_subscription_name(name)
	duration = validate_ttl(ttl)
	mask = str(update_mask or "").strip()
	if not mask:
		raise ValueError("updateMask must name at least one field; a patch with an empty mask is a no-op.")

	# **A patch that names ttl in its mask must carry ttl.** Omitting it is a flat 400
	# INVALID_ARGUMENT — "ttl field must be set" — and this was not a validation nicety: it
	# meant no renewal this app ever issued succeeded. Every subscription ran to its 7-day
	# expiry and died, which is what dragged the recreate path (and the uid collision that
	# lived in it) into play at all. `last_renewed` on both live rows equalled their create
	# date, 16 consecutive failures apiece, and nobody noticed for as long as recreate
	# happened to paper over it (v1.340.2).
	#
	# The reasoning that used to sit here — "the mask says set ttl, the absent value says to
	# the maximum" — is true of *create*, where ttl is an unset input field, and false of
	# *patch*, where the mask is a promise about the body. Only inject when the mask
	# actually names ttl, so a future mask over some other field is unaffected.
	body: dict[str, Any] = {}
	if duration is not None:
		body["ttl"] = duration
	elif "ttl" in [field.strip() for field in mask.split(",")]:
		body["ttl"] = MAX_TTL

	resource = {
		"name": f"subscriptions/{dryrun.DRYRUN_MARKER}{dryrun.dryrun_hash(subscription)}",
		"state": SUBSCRIPTION_STATE_ACTIVE,
		"expireTime": dryrun.DRYRUN_CREATE_TIME,
		"dryRun": True,
	}
	return GoogleCall(
		client_method="patch_subscription",
		google_method="workspaceevents.subscriptions.patch",
		http_method="PATCH",
		path=f"/{WORKSPACE_EVENTS_VERSION}/{subscription}",
		query={"updateMask": mask, "validateOnly": _qbool(validate_only)},
		body=body,
		host=WORKSPACE_EVENTS_HOST,
		requires_identity=AuthIdentity.USER,
		dry_run_payload=_synthetic_operation(resource, "patch", subscription),
	)


def build_reactivate_subscription_call(name: str) -> GoogleCall:
	"""``POST /v1/{name=subscriptions/*}:reactivate`` — bring a suspended one back.

	A subscription is suspended, not deleted, when Google cannot deliver — the topic lost its
	publisher binding, the user lost the scope, the target space went away. Reactivation is
	only possible while it is still within its expiry; past that it is gone and the answer is
	a fresh create.

	The reactivated subscription's ``expireTime`` is **not** extended by reactivating, so the
	result still has to be read: a reactivate followed by no renewal buys hours, not days.
	"""
	subscription = validate_subscription_name(name)
	resource = {
		"name": f"subscriptions/{dryrun.DRYRUN_MARKER}{dryrun.dryrun_hash(subscription)}",
		"state": SUBSCRIPTION_STATE_ACTIVE,
		"expireTime": dryrun.DRYRUN_CREATE_TIME,
		"dryRun": True,
	}
	return GoogleCall(
		client_method="reactivate_subscription",
		google_method="workspaceevents.subscriptions.reactivate",
		http_method="POST",
		path=f"/{WORKSPACE_EVENTS_VERSION}/{subscription}:reactivate",
		body={},
		host=WORKSPACE_EVENTS_HOST,
		requires_identity=AuthIdentity.USER,
		dry_run_payload=_synthetic_operation(resource, "reactivate", subscription),
	)


def build_get_subscription_call(name: str) -> GoogleCall:
	"""``GET /v1/{name=subscriptions/*}`` — the truth about state and expiry.

	The reconciliation sweep's read. ``Chat Event Subscription`` rows are ERPNext's belief;
	this is what Google thinks, and the two disagreeing is exactly the condition the sweep
	exists to find. Returns the resource directly — no operation envelope.
	"""
	subscription = validate_subscription_name(name)
	return GoogleCall(
		client_method="get_subscription",
		google_method="workspaceevents.subscriptions.get",
		http_method="GET",
		path=f"/{WORKSPACE_EVENTS_VERSION}/{subscription}",
		host=WORKSPACE_EVENTS_HOST,
		requires_identity=AuthIdentity.USER,
		dry_run_payload={
			"name": f"subscriptions/{dryrun.DRYRUN_MARKER}{dryrun.dryrun_hash(subscription)}",
			"state": SUBSCRIPTION_STATE_ACTIVE,
			"expireTime": dryrun.DRYRUN_CREATE_TIME,
			"dryRun": True,
		},
	)


def build_list_subscriptions_call(
	*,
	page_size: int = DEFAULT_SUBSCRIPTION_PAGE_SIZE,
	page_token: str | None = None,
	filter_expression: str | None = None,
) -> GoogleCall:
	"""``GET /v1/subscriptions`` — every subscription this principal owns.

	``filter_expression`` is **required by the API**, not by us: the reference marks
	``filter`` Required and demands at least one event type, with ``event_types`` and
	``target_resource`` the only filterable fields (``OR`` between types, ``AND`` between a
	type and a target). Enforcing it locally turns a bare 400 into a sentence.

	``VERIFY:`` that requirement was not re-read this session. If a real call succeeds
	without a filter, relax this to a warning rather than deleting it — a list that silently
	returns another product's subscriptions is worse than a 400.

	Listing is per **principal**, which is why orphan detection across a 20-coworker roster
	is 20 calls and not one. The 100-per-minute per-user quota is not the constraint; the
	600-per-minute project one is, and only during a first-run sweep.
	"""
	expression = str(filter_expression or "").strip()
	if not expression:
		raise ValueError(
			"subscriptions.list requires a filter naming at least one event type — e.g. "
			f'event_types:"{EVENT_TYPE_MESSAGE_CREATED}". Only event_types and target_resource '
			"are filterable."
		)

	size = max(1, min(int(page_size or DEFAULT_SUBSCRIPTION_PAGE_SIZE), MAX_SUBSCRIPTION_PAGE_SIZE))
	query: dict[str, str] = {"pageSize": str(size), "filter": expression}
	if page_token:
		query["pageToken"] = str(page_token)

	return GoogleCall(
		client_method="list_subscriptions",
		google_method="workspaceevents.subscriptions.list",
		http_method="GET",
		path=f"/{WORKSPACE_EVENTS_VERSION}/subscriptions",
		query=query,
		host=WORKSPACE_EVENTS_HOST,
		requires_identity=AuthIdentity.USER,
		# Empty, for the same reason messages.list is empty in dry-run: a reconciliation
		# sweep that finds fiction acts on it, and the action here is "create a subscription
		# that already exists" or "delete one that does not".
		dry_run_payload={"subscriptions": [], "nextPageToken": "", "dryRun": True},
	)


def build_delete_subscription_call(
	name: str, *, validate_only: bool = False, allow_missing: bool = False
) -> GoogleCall:
	"""``DELETE /v1/{name=subscriptions/*}`` — stop receiving events for one principal.

	``allow_missing=True`` makes deleting an already-deleted subscription a success rather
	than a 404, which is what a retried teardown wants; it is off by default so that a
	reconciliation sweep deleting something it did not expect to be missing still says so.

	Deleting is not reversible and not the same as suspending: a new subscription gets a new
	name, and the gap between the delete and the create is inbound events that nobody
	receives and nothing replays. ``Chat Room.last_event_at`` plus the reconciliation sweep
	is what recovers that window.
	"""
	subscription = validate_subscription_name(name)
	query: dict[str, str] = {"validateOnly": _qbool(validate_only)}
	if allow_missing:
		query["allowMissing"] = _qbool(True)

	return GoogleCall(
		client_method="delete_subscription",
		google_method="workspaceevents.subscriptions.delete",
		http_method="DELETE",
		path=f"/{WORKSPACE_EVENTS_VERSION}/{subscription}",
		query=query,
		host=WORKSPACE_EVENTS_HOST,
		requires_identity=AuthIdentity.USER,
		dry_run_payload=_synthetic_operation({}, "delete", subscription),
	)


# --------------------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------------------


class WorkspaceEventsClient:
	"""Subscription lifecycle for one impersonated coworker.

	    events = WorkspaceEventsClient(subject="alice@example.com")
	    subscription = events.create_subscription(
	        target_resource=chat_target_resource(),
	        event_types=MESSAGE_EVENT_TYPES,
	        pubsub_topic=settings.pubsub_events_topic,
	    )
	    room.expire_time = subscription.expire_time   # never a constant

	Composition over inheritance, and over duplication: this holds a
	:class:`~erpnext_enhancements.chat.gchat.client.GoogleChatClient` and calls its
	``execute``. There is therefore exactly one retry loop, one dry-run branch, one place a
	bearer token exists and one method a test patches to prove no network I/O happened — for
	both Google hosts. A second transport would double all four and keep none of them in
	step.

	**Every method is user-authenticated.** Shape B subscriptions target ``spaces/-``, which
	is user-auth only; a client constructed as the app identity is refused by
	``execute``'s identity check before anything is sent, rather than by a 403 from Google
	that reads like a scope problem.
	"""

	def __init__(
		self,
		*,
		subject: str | None = None,
		identity: AuthIdentity = AuthIdentity.USER,
		dry_run: bool | None = None,
		settings: Any | None = None,
		token_provider: TokenProvider | None = None,
		transport: Any | None = None,
		correlation_id: str | None = None,
		chat_client: GoogleChatClient | None = None,
	) -> None:
		"""``subject`` is the impersonated coworker whose spaces are being watched.

		The injection points mirror ``GoogleChatClient``'s exactly, so the bench-free suite
		exercises this class the same way it exercises that one. ``chat_client`` is the
		fourth: hand in an already-built transport when one call is being made as part of a
		sequence that already has one, so the correlation id is shared across both hosts.
		"""
		self._client = chat_client or GoogleChatClient(
			subject=subject,
			identity=identity,
			dry_run=dry_run,
			settings=settings,
			token_provider=token_provider,
			transport=transport,
			correlation_id=correlation_id,
		)

	@property
	def identity(self) -> AuthIdentity:
		return self._client.identity

	@property
	def subject(self) -> str | None:
		return self._client.subject

	@property
	def dry_run(self) -> bool:
		return self._client.dry_run

	def create_subscription(
		self,
		*,
		target_resource: str,
		event_types: Sequence[str] = MESSAGE_EVENT_TYPES,
		pubsub_topic: str,
		include_resource: bool = False,
		ttl: str | None = None,
	) -> Subscription:
		"""Create a subscription and return it **with its granted expiry read**.

		Returns a :class:`Subscription`, not a dict, and that is the enforcement mechanism
		for "never schedule a renewal from a constant": the type cannot exist without an
		``expireTime``. Store :attr:`Subscription.expire_time` on
		``Chat Event Subscription.expire_time`` and derive the renewal from it —
		``Chat Settings.subscription_ttl_seconds`` is a request, not a promise.

		Use :meth:`validate_subscription` for the ``validateOnly`` preflight; it is a
		separate method precisely so this one can always return a real subscription.
		"""
		call = build_create_subscription_call(
			target_resource=target_resource,
			event_types=event_types,
			pubsub_topic=pubsub_topic,
			include_resource=include_resource,
			ttl=ttl,
			validate_only=False,
		)
		return parse_subscription(self._client.execute(call), client_method=call.client_method)

	def validate_subscription(
		self,
		*,
		target_resource: str,
		event_types: Sequence[str] = MESSAGE_EVENT_TYPES,
		pubsub_topic: str,
		include_resource: bool = False,
		ttl: str | None = None,
	) -> dict[str, Any]:
		"""``validateOnly=true`` — run every server-side check and create nothing.

		Returns the raw response rather than a :class:`Subscription`, because a validated
		non-creation has no expiry to read and forcing one would defeat the type's whole
		purpose.

		This is the preflight that settles the open question: **can a DWD-impersonated user
		create a ``spaces/-`` subscription?** No page states the combination, and the whole
		inbound design rests on the answer (PHASE2_VERIFIED.md §8.2).

		``VERIFY:`` what a ``validateOnly`` success actually returns — an operation with an
		empty ``response``, or one carrying a phantom subscription. Nothing here depends on
		which; the caller checks for the absence of an exception.
		"""
		call = build_create_subscription_call(
			target_resource=target_resource,
			event_types=event_types,
			pubsub_topic=pubsub_topic,
			include_resource=include_resource,
			ttl=ttl,
			validate_only=True,
		)
		return self._client.execute(call)

	def patch_subscription(
		self, name: str, *, ttl: str | None = None, update_mask: str = "ttl"
	) -> Subscription:
		"""Renew a subscription. ``ttl=None`` asks for the maximum; read what you get back."""
		call = build_patch_subscription_call(name, ttl=ttl, update_mask=update_mask)
		return parse_subscription(self._client.execute(call), client_method=call.client_method)

	def reactivate_subscription(self, name: str) -> Subscription:
		"""Bring a suspended subscription back, and read the expiry it still carries."""
		call = build_reactivate_subscription_call(name)
		return parse_subscription(self._client.execute(call), client_method=call.client_method)

	def get_subscription(self, name: str) -> Subscription:
		"""Read one subscription — state and expiry as Google currently holds them."""
		call = build_get_subscription_call(name)
		return parse_subscription(self._client.execute(call), client_method=call.client_method)

	def list_subscriptions(
		self,
		*,
		filter_expression: str,
		page_size: int = DEFAULT_SUBSCRIPTION_PAGE_SIZE,
		page_token: str | None = None,
	) -> dict[str, Any]:
		"""One page of this principal's subscriptions. Raw, because a page is not a resource.

		``filter_expression`` is positional-by-necessity rather than optional: the API
		requires it. Feed the entries through :func:`parse_subscription` individually if you
		need their expiries.
		"""
		return self._client.execute(
			build_list_subscriptions_call(
				page_size=page_size, page_token=page_token, filter_expression=filter_expression
			)
		)

	def delete_subscription(
		self, name: str, *, validate_only: bool = False, allow_missing: bool = False
	) -> dict[str, Any]:
		"""Delete a subscription. Raw response — there is no resource left to parse."""
		return self._client.execute(
			build_delete_subscription_call(name, validate_only=validate_only, allow_missing=allow_missing)
		)
