# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The sweep that turns a missed subscription renewal from data loss into lag.

	bench --site <site> execute erpnext_enhancements.chat.sync.reconcile.reconcile_due_rooms

**Workspace Events has no replay.** When a subscription lapses, is suspended, or the Pub/Sub
consumer is down, the events delivered in that window are gone — not queued, not
dead-lettered, gone. Nothing in Google will ever tell us what we missed. The only way to find
out what was said is to ask the space directly, and the only read that can ask a bounded
question is ``spaces.messages.list`` with a ``createTime`` filter.

That is this module, and it is the difference between "inbound was down for six hours" being
a latency incident and being a permanent hole in the record of what a company said to itself.

The filter has exactly two fields
----------------------------------
``createTime`` and ``thread.name``. Nothing else. A third field is a 400, and a filter that
silently matched nothing would compare the wrong window and "repair" messages that were never
missing — so the expression is built by
:func:`~erpnext_enhancements.chat.gchat.client.build_message_filter` and never by hand. Read
that function's docstring before changing anything here.

One inbound path, never two
----------------------------
Everything the sweep finds is ingested through **the same idempotent inbound path** a live
event takes. It writes a ``Chat Inbound Event`` row — the same durable delivery record the
Pub/Sub consumer writes, carrying a **genuine two-layer Pub/Sub envelope**, base64 ``data``
and all — and hands the row's name to ``inbound.process_inbound_event``. There is no second
ingest, no shortcut into ``Chat Message.insert``, and therefore no second place for echo
suppression, ordering and late-arrival handling to be got subtly differently.

That the envelope is real is not cosmetic: the inbound processor parses ``payload`` with
``decisions.parse_pubsub_envelope`` and settles the row ``Failed`` on anything else. A sweep
with its own payload shape would not be reusing the inbound path, it would be feeding it one
``MalformedEvent`` per recovered message. Provenance and the already-fetched resource ride in
a ``reconcile`` key **outside** ``message``, where the parser cannot see them — see
:func:`_recovered_envelope`.

The consequence is that the sweep is **durable without the processor**. If ``sync/inbound.py``
is unavailable, the rows are still written and still ``Received``; the inbound sweeper drains
them on its own schedule. The sweep never silently drops what it found.

Exactly once, twice over
-------------------------
Two independent guards, because "the sweep recovered all twenty messages exactly once" has to
survive overlapping windows, a redelivered live event for the same message, and two sweeps
racing:

1. before writing anything, a probe on ``Chat Message.gchat_message_name`` — a unique column —
   skips a message ERPNext already has;
2. ``Chat Inbound Event.pubsub_message_id`` is unique, and the sweep writes a **deterministic**
   synthetic id derived from the resource name, so re-listing the same message in an
   overlapping window collides instead of enqueuing a second copy.

Both are needed. (1) is the cheap common case and keeps junk rows out of the inbound table;
(2) is the one that holds when two sweeps run concurrently, because (1) is a check-then-act
and this one is not. The insert runs inside ``frappe.database.database.savepoint`` catching
**both** ``DuplicateEntryError`` *and* ``UniqueValidationError`` — a non-primary-key unique
index raises the second, the ADR says only the first, and the ADR is wrong
(``PHASE2_VERIFIED.md`` §1.1).

Windows, watermarks and the overlap
------------------------------------
The window starts at ``max(Chat Room.last_reconcile_at, Chat Room.last_event_at)`` minus
:data:`RECONCILE_OVERLAP_SECONDS`. The overlap is deliberate: ``createTime >`` is exclusive,
Google's clock is not ours, and a duplicate costs nothing while a miss costs a message.

``last_reconcile_at`` is written with the timestamp taken **before** the listing started, not
after. A message created during the sweep would otherwise fall in the hole between the last
page and the watermark, and it would fall there silently.

Ordering
---------
Every page is collected first, then sorted by ``(createTime, name)`` ascending, and only then
ingested. Relying on ``orderBy`` alone would make the ordering guarantee a property of
Google's paging rather than of this code — and ``seq`` is allocated in ingest order, so the
order messages are handed over in *is* the order they will forever appear in.

Limits and honesty about them
------------------------------
* **Deletes that happened inside the gap are not recovered.** The filter is on ``createTime``,
  so a message deleted during the outage is simply absent from the listing and is
  indistinguishable from one created outside the window. Detecting those needs a full-space
  listing, which is a different (and much more expensive) operation than this one.
* A sweep truncated by :data:`RECONCILE_MAX_MESSAGES_PER_ROOM` or by quota **does not advance
  the watermark**, so the next pass redoes the window. That converges, and it is why the
  duplicate guards above have to be exactly right.

Quota
------
15 reads/second/space and a per-project read bucket, both respected. Membership and space
writes are *not* charged to the per-space bucket; reads are, at their own rate, which is why
this uses a separate limiter key from the 1-write/second one.

Nothing here logs a message body. Indentation is tabs.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Final

import frappe

from erpnext_enhancements.chat import seams
from erpnext_enhancements.chat.gchat import client as chat_client
from erpnext_enhancements.chat.sync import ratelimit, subscriptions
from erpnext_enhancements.chat.sync.subscriptions import (
	ALERT_SEVERITY_ALARM,
	ALERT_SEVERITY_WARN,
	AlertSink,
	SyncAlert,
)

ROOM_DOCTYPE: Final[str] = "Chat Room"
ROOM_MEMBER_DOCTYPE: Final[str] = "Chat Room Member"
MESSAGE_DOCTYPE: Final[str] = "Chat Message"
INBOUND_EVENT_DOCTYPE: Final[str] = "Chat Inbound Event"

#: The event type the sweep replays a recovered message as. A message found by the sweep is,
#: as far as ERPNext is concerned, a message that was created and whose creation event never
#: arrived — so it takes the ``created`` path, and the ordering and late-arrival rules apply
#: to it exactly as they would have if the event had been delivered.
RECOVERED_EVENT_TYPE: Final[str] = "google.workspace.chat.message.v1.created"

#: How far back before the watermark to start. ``createTime >`` is exclusive and clocks
#: differ; a re-listed message is free (both dedupe guards catch it) and a missed one is not.
RECONCILE_OVERLAP_SECONDS: Final[int] = 120

#: With no watermark at all — a room never swept and with no events yet — this is the window.
#: Overridden by ``Chat Settings.reconcile_window_minutes``.
DEFAULT_RECONCILE_WINDOW_MINUTES: Final[int] = 60

#: Never look further back than this, however stale the watermark is. A room whose
#: ``last_reconcile_at`` is a year old would otherwise page through a year of history at 15
#: reads/second, and the operator would find out from the quota dashboard.
MAX_RECONCILE_LOOKBACK_SECONDS: Final[int] = 30 * 86_400

#: Messages one room may recover in one pass. A truncated sweep leaves the watermark alone
#: and converges over the following passes; an unbounded one turns a backfill into an outage.
RECONCILE_MAX_MESSAGES_PER_ROOM: Final[int] = 500

#: ``messages.list`` page size. The API caps at 1000 and defaults to 25; 100 keeps the number
#: of round trips low without making a single page's failure expensive.
RECONCILE_PAGE_SIZE: Final[int] = 100

#: Rooms one scheduled pass will sweep.
RECONCILE_ROOM_BATCH: Final[int] = 25

#: 15 reads per second per space ⇒ one read costs 1000/15 ms of that space's budget. The
#: write bucket is 1/second and lives under a different key: a sweep must not be able to
#: starve the relay of its write second (``PHASE2_VERIFIED.md`` §2).
SPACE_READ_COST_MS: Final[int] = 1000 // 15

#: Redis key prefix for the read bucket, distinct from ``SpaceRateLimiter``'s write default.
SPACE_READ_KEY_PREFIX: Final[str] = "chat:space_read"

#: Google's per-project message **read** bucket, per 60 seconds. ``Chat Settings`` carries a
#: write limit but no read limit, so this is the fallback; the reads bucket is independent of
#: the writes bucket (``PHASE2_VERIFIED.md`` §2, correction 3) and must not be charged to it.
DEFAULT_PROJECT_MESSAGE_READS_PER_MINUTE: Final[int] = 3000

#: If the sweep recovers a message that Chat had this much earlier than the room's last
#: delivered event, then Chat had traffic and the event stream did not. That is the stated
#: window in §4.J, and it is the condition that catches a subscription which is ACTIVE,
#: unexpired and quietly delivering nothing.
EVENT_SILENCE_ALERT_SECONDS: Final[int] = 3600

#: Candidate names for the inbound processor, tried in order. Mirrors
#: ``GoogleChatClient._access_token``'s handling of ``chat.gchat.auth``: this module must not
#: guess at another wave's signature, and a missing processor has to degrade to "the rows are
#: written and the inbound sweeper will drain them" rather than to an ImportError inside a
#: scheduled job. The processor is called with one positional argument, the
#: ``Chat Inbound Event`` docname.
INBOUND_PROCESSOR_FUNCTIONS: Final[tuple[str, ...]] = (
	"process_inbound_event",
	"process_event",
	"ingest_inbound_event",
)


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def _log_debug(message: str, *args: Any) -> None:
	try:
		frappe.logger("chat").debug(message, *args)
	except Exception:
		pass


def _as_int(value: Any, default: int = 0) -> int:
	try:
		return int(value)
	except (TypeError, ValueError):
		return default


def _as_datetime(value: Any) -> datetime | None:
	if not value:
		return None
	if isinstance(value, datetime):
		return value
	try:
		return frappe.utils.get_datetime(value)
	except Exception:
		return None


def synthetic_delivery_id(resource_name: str) -> str:
	"""The deterministic ``pubsub_message_id`` the sweep writes for a recovered message.

	Deterministic is the entire point: two sweeps over overlapping windows must collide on the
	unique index rather than enqueue the same message twice. Prefixed ``reconcile:`` so a row
	that came from a sweep is distinguishable at a glance from one Pub/Sub delivered, which
	matters when somebody is working out whether the live stream is healthy.
	"""
	digest = hashlib.sha256(str(resource_name or "").encode("utf-8")).hexdigest()[:32]
	return f"reconcile:{digest}"


def _inbound_processor() -> Callable[[str], Any] | None:
	"""Resolve ``sync/inbound.py``'s entry point, or ``None``.

	Name-candidate resolution rather than a hard import, for the same reason
	``GoogleChatClient._access_token`` does it against ``chat.gchat.auth``: this module owns
	the *sweep*, another owns the *ingest*, and the sweep must remain shippable and durable if
	the two land out of step. ``None`` is not an error — the ``Chat Inbound Event`` rows have
	already been written and the inbound sweeper drains them.
	"""
	try:
		from erpnext_enhancements.chat.sync import inbound
	except Exception:
		return None
	for candidate in INBOUND_PROCESSOR_FUNCTIONS:
		function = getattr(inbound, candidate, None)
		if callable(function):
			return function
	return None


def _settings() -> Any:
	try:
		return frappe.get_cached_doc("Chat Settings")
	except Exception:
		return None


def _setting_int(settings: Any, field: str, default: int) -> int:
	raw = getattr(settings, field, None) if settings is not None else None
	if raw in (None, ""):
		return int(default)
	try:
		return int(raw)
	except (TypeError, ValueError):
		return int(default)


def _gate() -> tuple[bool, str]:
	"""Same gate as the subscription manager, plus the inbound kill switch.

	``pause_inbound`` **does** stop the sweep, unlike the renewal job: the sweep's entire
	output is inbound ingest, and an operator who paused inbound has said they do not want
	messages applied right now. Nothing is lost by waiting — the watermark does not move, so
	the same window is swept when they un-pause.
	"""
	settings = _settings()
	if settings is None:
		return False, "Chat Settings is unreadable"
	if not _setting_int(settings, "enabled", 0):
		return False, "Chat Settings.enabled is 0"
	if _setting_int(settings, "dry_run_mode", 1):
		return False, "Chat Settings.dry_run_mode is 1"
	if _setting_int(settings, "pause_inbound", 0):
		return False, "Chat Settings.pause_inbound is 1"
	return True, ""


# --------------------------------------------------------------------------------------
# Quota
# --------------------------------------------------------------------------------------


def _charge_read(space: str, settings: Any) -> bool:
	"""Claim one ``messages.list`` read against both buckets. ``False`` means "come back later".

	Fails **open** on a Redis error, and that is a deliberate trade rather than an oversight:
	Google enforces its own limits and answers 429 (which the transport's backoff already
	handles), whereas a cache outage that silently stopped every reconciliation sweep would
	turn a Redis blip into a permanent hole in the message record. The bucket is an
	optimisation; backoff is the correctness mechanism (``PHASE2_VERIFIED.md`` §2).
	"""
	try:
		limiter = ratelimit.SpaceRateLimiter(key_prefix=SPACE_READ_KEY_PREFIX)
		decision = limiter.acquire(space, cost_ms=SPACE_READ_COST_MS, block=True)
		if not decision.allowed:
			return False
	except Exception:
		_log_debug("chat reconcile: per-space read bucket unavailable; proceeding")

	try:
		limit = _setting_int(
			settings, "project_message_reads_per_minute", DEFAULT_PROJECT_MESSAGE_READS_PER_MINUTE
		)
		return bool(ratelimit.ProjectQuota().charge(ratelimit.BUCKET_MESSAGE_READS, limit=limit))
	except Exception:
		_log_debug("chat reconcile: project read bucket unavailable; proceeding")
		return True


# --------------------------------------------------------------------------------------
# Room selection and impersonation
# --------------------------------------------------------------------------------------

#: Identifier and state columns only. No ``title``, no ``last_message_preview`` — the sweep
#: has no business reading room content, and ``chat/permissions.py``'s rule is about content.
_ROOM_FIELDS: Final[tuple[str, ...]] = (
	"name",
	"gchat_space_name",
	"provisioning_state",
	"is_archived",
	"last_event_at",
	"last_reconcile_at",
	"last_message_sender",
)


def _room(name: str) -> dict[str, Any] | None:
	row = frappe.db.get_value(ROOM_DOCTYPE, name, list(_ROOM_FIELDS), as_dict=True)
	return dict(row) if row else None


def _mirrored_rooms(*, limit: int, user: str = "") -> list[dict[str, Any]]:
	"""Rooms that are actually mirrored into a Google space, oldest watermark first.

	``frappe.get_all`` with an explicit identifier-only column list, in a background job with
	no session user. The standing rule — ``get_all`` is ``get_list(ignore_permissions=True)``
	— is about reading chat **content** past the membership model; nothing selected here is
	content.
	"""
	filters: dict[str, Any] = {
		"provisioning_state": "Ready",
		"is_archived": 0,
		"gchat_space_name": ["is", "set"],
	}
	if user:
		rooms = frappe.get_all(
			ROOM_MEMBER_DOCTYPE,
			filters={"user": user, "is_active": 1},
			pluck="room",
			limit=max(1, int(limit)),
		)
		if not rooms:
			return []
		filters["name"] = ["in", list(rooms)]

	rows = frappe.get_all(
		ROOM_DOCTYPE,
		filters=filters,
		fields=list(_ROOM_FIELDS),
		order_by="last_reconcile_at asc",
		limit=max(1, int(limit)),
	)
	return [dict(row) for row in rows]


def _subscribed_users() -> list[str]:
	"""Coworkers with an ACTIVE subscription, i.e. principals whose grant we know is live.

	Preferred as the impersonation subject for ``messages.list``: they hold a working token
	today, so a read on their behalf will not 401 in the middle of a recovery sweep.
	"""
	rows = frappe.get_all(
		subscriptions.SUBSCRIPTION_DOCTYPE,
		filters={"state": "ACTIVE"},
		pluck="target_user",
		limit=200,
	)
	return [user for user in rows if user]


def _subject_for_room(room: str, *, preferred: Sequence[str] = ()) -> str:
	"""Which coworker to impersonate when listing this room's space.

	``spaces.messages.list`` under user authentication reads what that user can see, so the
	subject has to be a member of the space. Preference order:

	1. a member with an ACTIVE Chat event subscription — their grant is known good;
	2. any active member;
	3. nobody, in which case the room is skipped with a stated reason rather than read as the
	   app identity, which would either 403 or (worse) succeed and read a *different* set of
	   messages from the one a member sees.
	"""
	members = frappe.get_all(
		ROOM_MEMBER_DOCTYPE,
		filters={"room": room, "is_active": 1},
		pluck="user",
		limit=200,
	)
	members = [user for user in members if user]
	if not members:
		return ""
	preferred_set = {user for user in preferred if user}
	for user in members:
		if user in preferred_set:
			return user
	return members[0]


# --------------------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------------------


def reconcile_window_start(
	room: dict[str, Any],
	*,
	now_local: datetime,
	since: datetime | None,
	default_window_minutes: int,
) -> datetime:
	"""Where this room's sweep should start looking. Pure given its arguments.

	``since`` wins when the caller has one — the subscription-expiry path passes the dead
	subscription's ``expire_time``, which is the only honest lower bound for that gap.
	Otherwise the later of the two watermarks, minus the overlap, clamped to
	:data:`MAX_RECONCILE_LOOKBACK_SECONDS`.

	The **later** of ``last_reconcile_at`` and ``last_event_at``, not the earlier: a live event
	that arrived after the last sweep proves the stream was working up to that point, so
	starting before it would re-read a window nothing could have been missed in.
	"""
	floor = now_local - timedelta(seconds=MAX_RECONCILE_LOOKBACK_SECONDS)

	candidate = since
	if candidate is None:
		watermarks = [
			value
			for value in (
				_as_datetime(room.get("last_reconcile_at")),
				_as_datetime(room.get("last_event_at")),
			)
			if value is not None
		]
		if watermarks:
			candidate = max(watermarks)
		else:
			candidate = now_local - timedelta(minutes=max(1, int(default_window_minutes)))

	start = candidate - timedelta(seconds=RECONCILE_OVERLAP_SECONDS)
	return max(start, floor)


# --------------------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------------------


def reconcile_room(
	room: str,
	*,
	since: datetime | None = None,
	reason: str = "scheduled",
	client: Any | None = None,
	client_factory: Callable[[str], Any] | None = None,
	ingest: Callable[[str], Any] | None = None,
	alert: AlertSink | None = None,
	preferred_subjects: Sequence[str] = (),
	max_messages: int = RECONCILE_MAX_MESSAGES_PER_ROOM,
	skip_gate: bool = False,
) -> dict[str, Any]:
	"""Sweep one room for messages Google has and ERPNext does not. Never raises.

	Returns a JSON-encodable summary: ``{"status", "room", "space", "since", "listed",
	"recovered", "already_present", "duplicate", "ingested", "truncated", "reason"}``.

	``client`` / ``client_factory`` / ``ingest`` / ``alert`` are injection points so the whole
	sweep is exercisable against the fake Chat API with no bench and no network. They are the
	only reason chaos test 10 — a subscription that expires with a twenty-message backlog —
	can be an assertion rather than a hope.
	"""
	alert = alert or subscriptions.raise_operator_alert
	summary: dict[str, Any] = {
		"status": "skipped",
		"room": room,
		"space": "",
		"since": "",
		"listed": 0,
		"recovered": 0,
		"already_present": 0,
		"duplicate": 0,
		"ingested": 0,
		"truncated": False,
		"reason": "",
	}

	if not skip_gate:
		ok, gate_reason = _gate()
		if not ok:
			summary["reason"] = gate_reason
			return summary

	row = _room(room)
	if row is None:
		summary["reason"] = "no such Chat Room"
		return summary

	space = str(row.get("gchat_space_name") or "").strip()
	if not space:
		summary["reason"] = "room has no gchat_space_name; there is nothing to reconcile against"
		return summary
	summary["space"] = space

	settings = _settings()
	now_epoch, now_local = subscriptions.now_pair()
	# Taken BEFORE the listing. Writing the post-sweep clock would leave anything created
	# during the sweep in a hole between the last page and the watermark.
	started_local = now_local
	window_start = reconcile_window_start(
		row,
		now_local=now_local,
		since=since,
		default_window_minutes=_setting_int(
			settings, "reconcile_window_minutes", DEFAULT_RECONCILE_WINDOW_MINUTES
		),
	)
	start_epoch = subscriptions.epoch_from_local_datetime(
		window_start, now_epoch=now_epoch, now_local=now_local
	)
	stamp = subscriptions.rfc3339_utc(start_epoch)
	summary["since"] = stamp

	subject = _subject_for_room(room, preferred=preferred_subjects or _subscribed_users())
	if client is None and not subject:
		summary["reason"] = "no active member to impersonate; messages.list is user-authenticated"
		return summary

	try:
		transport = client or (client_factory or _client_for)(subject)
		messages, truncated = _list_window(
			transport, space=space, stamp=stamp, settings=settings, max_messages=max_messages
		)
	except Exception as exc:
		summary["status"] = "failed"
		summary["reason"] = subscriptions.error_text(exc, "messages.list")
		alert(
			SyncAlert(
				key=f"reconcile-list-failed:{room}",
				severity=ALERT_SEVERITY_WARN,
				subject=f"The Chat reconciliation sweep could not read space {space}",
				message=(
					"Until this succeeds, any message sent while inbound was down stays missing from "
					f"ERPNext with nothing to show for it. Detail: {summary['reason']}"
				),
				subscription=room,
			)
		)
		return summary

	summary["listed"] = len(messages)
	summary["truncated"] = truncated

	# Sorted here rather than trusted from the API: `seq` is allocated in ingest order, so the
	# order these are handed over in is the order they will forever be displayed in.
	messages.sort(
		key=lambda resource: (str(resource.get("createTime") or ""), str(resource.get("name") or ""))
	)

	processor = ingest if ingest is not None else _inbound_processor()
	newest_created: str = ""
	for resource in messages:
		outcome, event_name = _enqueue_recovered(
			resource, room=room, space=space, reason=reason, started_local=started_local
		)
		if outcome == "already_present":
			summary["already_present"] += 1
			continue
		if outcome == "duplicate":
			summary["duplicate"] += 1
			continue
		if outcome != "queued":
			continue

		summary["recovered"] += 1
		newest_created = max(newest_created, str(resource.get("createTime") or ""))
		if processor is None or not event_name:
			continue
		try:
			processor(event_name)
			summary["ingested"] += 1
		except Exception as exc:
			# The row stays `Received`, so the inbound sweeper retries it. This is exactly the
			# durability the Chat Inbound Event row exists for, and it is why the sweep does
			# not insert Chat Message rows itself.
			_log_debug("chat reconcile: inbound processor failed for %s (%s)", event_name, type(exc).__name__)

	if summary["recovered"]:
		# `reconcile_recovered`, and **never** `events_ingested`. Every row counted here is then
		# handed to `inbound.process_inbound_event`, and `_apply_created` bumps `events_ingested`
		# when — and only when — the insert actually happened. Bumping it here as well made the
		# counter read `2N` after recovering `N` messages, which is worse than having no counter
		# during the incident the counter exists for: the operator's first question is "did the
		# sweep find more than the stream missed?" and a doubled number answers it wrongly.
		#
		# The sweep still gets its own number, because "how much did reconciliation have to
		# repair" is a genuinely different question from "how many messages entered ERPNext" —
		# it is the one that says whether the live event stream is healthy. Note the two are
		# expected to *disagree*: a row the sweep recovers can still be classified ECHO or
		# DUPLICATE downstream and insert nothing, so `reconcile_recovered > events_ingested` is
		# normal and not a discrepancy to chase.
		seams.bump(seams.COUNTER_RECONCILE_RECOVERED, summary["recovered"])

	if not truncated:
		# Only a complete sweep may move the watermark. A truncated one leaves it alone and the
		# next pass redoes the window; both dedupe guards make that free.
		try:
			frappe.db.set_value(
				ROOM_DOCTYPE, room, {"last_reconcile_at": started_local}, update_modified=False
			)
		except Exception:
			_log_debug("chat reconcile: could not write last_reconcile_at for %s", room)

	_alert_on_event_silence(row, newest_created=newest_created, now_local=now_local, alert=alert)

	summary["status"] = "ok"
	return summary


def _client_for(subject: str) -> chat_client.GoogleChatClient:
	"""A Chat transport impersonating one member of the room being swept."""
	return chat_client.GoogleChatClient(subject=subject, dry_run=False)


def _list_window(
	transport: Any,
	*,
	space: str,
	stamp: str,
	settings: Any,
	max_messages: int,
) -> tuple[list[dict[str, Any]], bool]:
	"""Page ``messages.list`` over the window. Returns ``(resources, truncated)``.

	``show_deleted`` is **False**. A tombstone carries no text at all, so it could not be
	ingested as content anyway, and a delete that happened inside the gap is undetectable from
	a ``createTime``-filtered listing regardless — it is simply absent, exactly like a message
	created outside the window. That limitation is stated in the module docstring rather than
	papered over with a second, full-space listing.
	"""
	limit = max(1, int(max_messages or RECONCILE_MAX_MESSAGES_PER_ROOM))
	filter_expression = chat_client.build_message_filter(create_time_after=stamp)

	collected: list[dict[str, Any]] = []
	page_token: str | None = None
	truncated = False

	while True:
		if not _charge_read(space, settings):
			truncated = True
			break

		payload = transport.list_messages(
			space,
			page_size=RECONCILE_PAGE_SIZE,
			page_token=page_token,
			filter_expression=filter_expression,
			# Ascending, so the common case pages in the order we want. The local sort in
			# `reconcile_room` is the actual guarantee; this just keeps the pages tidy.
			# VERIFY: that the live API accepts the lowercase " asc" suffix on orderBy. AIP-132
			# specifies `field asc|desc` and Google's own sample uses `createTime DESC`; if a
			# 400 ever comes back, drop the argument — the local sort makes it optional.
			order_by="createTime asc",
			show_deleted=False,
		)
		for resource in payload.get("messages") or []:
			if isinstance(resource, Mapping):
				collected.append(dict(resource))
			if len(collected) >= limit:
				truncated = True
				break
		if truncated:
			break
		page_token = str(payload.get("nextPageToken") or "") or None
		if not page_token:
			break

	return collected, truncated


def _recovered_envelope(
	*,
	resource_name: str,
	space: str,
	delivery_id: str,
	started_local: datetime,
) -> dict[str, Any]:
	"""A recovered message, wrapped in the **real** two-layer Pub/Sub envelope.

	This is what makes "one inbound path" true rather than aspirational.
	``inbound.process_inbound_event`` reads ``Chat Inbound Event.payload`` through
	``decisions.parse_pubsub_envelope`` and settles the row ``Failed`` on anything it cannot
	parse — so a sweep that invented its own payload shape would not be using the inbound path
	at all, it would be poisoning it, one ``MalformedEvent`` per recovered message.

	Three details are deliberate:

	* ``data`` is **base64**, exactly as a live REST delivery carries it, so the decode path
	  the sweep exercises is the decode path production runs. A dict would also be accepted and
	  would quietly skip it.
	* the inner payload is ``{"message": {"name": …}}`` — the name and nothing else, which is
	  what ``includeResource: false`` actually delivers. Putting the fetched resource in here
	  would make the sweep's rows the only ones in the table carrying a body, and the first
	  person to read one would draw the wrong conclusion about what an event contains.
	* there is **no ``ce-source``**. A real one names the subscription that delivered the
	  event, and no subscription delivered this: inventing one would be a lie about provenance
	  written into the durable record. ``parse_pubsub_envelope`` reads ``ce-source`` only for
	  lifecycle events. (It would also have to name a Google host, which may appear in exactly
	  two modules and this is not one of them.)
	"""
	inner = json.dumps({"message": {"name": resource_name}}, separators=(",", ":")).encode("utf-8")
	now_epoch, now_local = subscriptions.now_pair()
	published = subscriptions.epoch_from_local_datetime(
		started_local, now_epoch=now_epoch, now_local=now_local
	)
	return {
		"message": {
			"attributes": {
				"ce-id": f"{space}/spaceEvents/{delivery_id}",
				"ce-type": RECOVERED_EVENT_TYPE,
				"ce-subject": space,
				"ce-specversion": "1.0",
				"ce-datacontenttype": "application/json",
			},
			"data": base64.b64encode(inner).decode("ascii"),
			"messageId": delivery_id,
			"orderingKey": space,
			"publishTime": subscriptions.rfc3339_utc(published),
		}
	}


def _enqueue_recovered(
	resource: Mapping[str, Any],
	*,
	room: str,
	space: str,
	reason: str,
	started_local: datetime,
) -> tuple[str, str]:
	"""Write one ``Chat Inbound Event`` for a recovered message. ``(outcome, event_name)``.

	Outcomes: ``already_present`` (ERPNext already has it), ``duplicate`` (a delivery row for
	it already exists), ``queued`` (written), ``skipped`` (unusable resource).

	The payload envelope is the contract with ``sync/inbound.py``::

		{"source": "reconcile", "reason": "...", "room": "...", "space": "spaces/...",
		 "late_arrival": true, "swept_at": "<site-local ISO>", "message": {<Message>}}

	The ``reconcile`` sibling carries provenance and the **already-fetched resource**. It is
	deliberately outside ``message``, where ``parse_pubsub_envelope`` cannot see it, so a row
	written by the sweep parses exactly like one Pub/Sub delivered and no branch is needed on
	the inbound side. Today ``inbound.process_inbound_event`` ignores it and pays its own
	``spaces.messages.get``; that costs one extra read per recovered message and is the price
	of one code path, which is the right trade. **The optimisation is left on the table on
	purpose and is described in ``reconcile["message"]``**: an inbound that chose to read it
	could skip the fetch, and ``classify_inbound`` already takes ``resource_fetched=True``.

	``late_arrival`` is provenance, not an instruction. Everything the sweep finds is by
	definition a message whose event did not arrive when it should have, but §4.G's
	``late_arrival`` flag on ``Chat Message`` is computed by the inbound path from the room
	tail — the sweep does not get to override it, because a sweep that ran while the stream
	was healthy legitimately re-lists messages that were never late.
	"""
	name = str(resource.get("name") or "").strip()
	if not name:
		return "skipped", ""

	# Cheap, indexed, and keeps junk out of the inbound table. Not sufficient on its own — it
	# is a check-then-act — which is why the unique `pubsub_message_id` below is the real
	# guarantee. Reads one opaque Google identifier and no content.
	try:
		if frappe.db.get_value(MESSAGE_DOCTYPE, {"gchat_message_name": name}, "name"):
			return "already_present", ""
	except Exception:
		_log_debug("chat reconcile: could not probe Chat Message for %s", name)

	delivery_id = synthetic_delivery_id(name)
	payload = _recovered_envelope(
		resource_name=name,
		space=space,
		delivery_id=delivery_id,
		started_local=started_local,
	)
	payload["reconcile"] = {
		"source": "reconcile",
		"reason": reason,
		"room": room,
		"space": space,
		"late_arrival": True,
		"swept_at": started_local.isoformat(),
		"message": dict(resource),
	}

	from frappe.database.database import savepoint

	created: list[str] = []
	try:
		# BOTH exception types, inside a savepoint. `gchat_message_name` and
		# `pubsub_message_id` are non-primary-key unique indexes, and those raise
		# `UniqueValidationError`, not `DuplicateEntryError` — the ADR says only the first and
		# the ADR is wrong (PHASE2_VERIFIED.md §1.1). The savepoint is mandatory, not hygiene:
		# a failed statement can poison the surrounding transaction.
		with savepoint(catch=(frappe.DuplicateEntryError, frappe.UniqueValidationError)):
			doc = frappe.get_doc(
				{
					"doctype": INBOUND_EVENT_DOCTYPE,
					"transport": "Workspace Event",
					"event_type": RECOVERED_EVENT_TYPE,
					"received_at": started_local,
					"status": "Received",
					"attempts": 0,
					"gchat_space_name": space,
					"gchat_resource_name": name,
					"pubsub_message_id": delivery_id,
					"payload": json.dumps(payload, default=str),
				}
			).insert(ignore_permissions=True)
			created.append(doc.name)
	except Exception as exc:
		_log_debug("chat reconcile: could not enqueue %s (%s)", name, type(exc).__name__)
		return "skipped", ""

	if not created:
		# The savepoint swallowed a unique violation: this message was already recovered, by an
		# earlier overlapping sweep or by a live event. Success, not an error.
		return "duplicate", ""
	return "queued", created[0]


def _alert_on_event_silence(
	room: Mapping[str, Any], *, newest_created: str, now_local: datetime, alert: AlertSink
) -> None:
	"""Chat had traffic and the event stream did not deliver it. That is the §4.J alarm.

	Deliberately *not* "the sweep found something" — an overlapping window legitimately
	re-lists messages that arrived perfectly well. The condition is a recovered message whose
	``createTime`` is more than :data:`EVENT_SILENCE_ALERT_SECONDS` newer than the last event
	this room actually received, which means the stream was quiet while the space was not.
	"""
	if not newest_created:
		return

	last_event = _as_datetime(room.get("last_event_at"))
	try:
		newest_epoch = _rfc3339_epoch(newest_created)
	except ValueError:
		return

	# The same offset-free conversion the subscription clock uses, so this comparison cannot
	# be wrong by the site's UTC offset — which is the direction that would silence the alarm.
	now_epoch, clock_local = subscriptions.now_pair()
	newest_local = subscriptions.local_datetime_from_epoch(
		newest_epoch, now_epoch=now_epoch, now_local=clock_local
	)

	if last_event is not None:
		if (newest_local - last_event).total_seconds() <= EVENT_SILENCE_ALERT_SECONDS:
			return
	elif (now_local - newest_local).total_seconds() <= EVENT_SILENCE_ALERT_SECONDS:
		return

	alert(
		SyncAlert(
			key=f"reconcile-events-missing:{room.get('name')}",
			severity=ALERT_SEVERITY_ALARM,
			subject="A Chat room had traffic that never arrived as an event",
			message=(
				f"The reconciliation sweep recovered a message created at {newest_created} in room "
				f"{room.get('name')}, but the last Workspace Event this room received was "
				f"{last_event or 'never'}. The subscription covering this space is not delivering — "
				"check Chat Event Subscription for its members, and note that a subscription can sit "
				"ACTIVE with a healthy expiry and deliver nothing at all."
			),
			subscription=str(room.get("name") or ""),
		)
	)


def _rfc3339_epoch(value: str) -> float:
	"""Google's RFC-3339, tolerating nanoseconds. Delegated, not re-implemented.

	``events_client.parse_rfc3339_epoch`` already handles the nine-fractional-digit case that
	``datetime.fromisoformat`` rejects, and a second parser would be a second thing to get
	wrong about the timestamp everything here is compared against.
	"""
	from erpnext_enhancements.chat.gchat.events_client import parse_rfc3339_epoch

	return parse_rfc3339_epoch(value)


# --------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------


def reconcile_due_rooms(
	limit: int = 0,
	*,
	client_factory: Callable[[str], Any] | None = None,
	ingest: Callable[[str], Any] | None = None,
	alert: AlertSink | None = None,
) -> dict[str, Any]:
	"""Sweep every mirrored room whose watermark is stale. **The scheduled entry point.**

		bench --site <site> execute \\
			erpnext_enhancements.chat.sync.reconcile.reconcile_due_rooms

	Never raises, and isolates each room: one space that has been deleted at Google, or one
	room with no member left to impersonate, must not stop the other rooms being swept.
	"""
	alert = alert or subscriptions.raise_operator_alert
	summary: dict[str, Any] = {
		"status": "ok",
		"reason": "",
		"rooms": 0,
		"swept": 0,
		"recovered": 0,
		"already_present": 0,
		"duplicate": 0,
		"ingested": 0,
		"skipped": 0,
		"failed": 0,
	}

	ok, reason = _gate()
	if not ok:
		summary["status"] = "skipped"
		summary["reason"] = reason
		return summary

	try:
		rooms = _mirrored_rooms(limit=RECONCILE_ROOM_BATCH if not limit else int(limit))
		preferred = _subscribed_users()
	except Exception as exc:
		summary["status"] = "failed"
		summary["reason"] = subscriptions.error_text(exc, "select-rooms")
		return summary

	summary["rooms"] = len(rooms)
	for room in rooms:
		result = reconcile_room(
			str(room.get("name") or ""),
			reason="scheduled",
			client_factory=client_factory,
			ingest=ingest,
			alert=alert,
			preferred_subjects=preferred,
			skip_gate=True,
		)
		status = result.get("status")
		if status == "ok":
			summary["swept"] += 1
			for key in ("recovered", "already_present", "duplicate", "ingested"):
				summary[key] += _as_int(result.get(key), 0)
		elif status == "failed":
			summary["failed"] += 1
		else:
			summary["skipped"] += 1

	return summary


def reconcile_rooms_for_user(
	user: str,
	*,
	since: datetime | None = None,
	reason: str = "subscription-expired",
	client_factory: Callable[[str], Any] | None = None,
	ingest: Callable[[str], Any] | None = None,
	alert: AlertSink | None = None,
	limit: int = 200,
) -> dict[str, Any]:
	"""Sweep every mirrored room this coworker is in — the gap a lapsed subscription left.

	**Deliberately over-broad.** A ``spaces/-`` subscription covers every space its principal
	is in, so the rooms genuinely at risk when one lapses are those no *other* live
	subscription covers. Computing that exactly means intersecting memberships against the
	live subscription set, and getting it wrong in the tight direction means silently not
	recovering messages. Sweeping every room the person is in is idempotent, costs reads that
	the per-space bucket already paces, and cannot under-recover.
	"""
	alert = alert or subscriptions.raise_operator_alert
	summary: dict[str, Any] = {
		"status": "ok",
		"reason": "",
		"user": user,
		"rooms": 0,
		"swept": 0,
		"recovered": 0,
		"ingested": 0,
		"skipped": 0,
		"failed": 0,
	}

	ok, gate_reason = _gate()
	if not ok:
		summary["status"] = "skipped"
		summary["reason"] = gate_reason
		return summary

	try:
		rooms = _mirrored_rooms(limit=limit, user=user)
		preferred = _subscribed_users()
	except Exception as exc:
		summary["status"] = "failed"
		summary["reason"] = subscriptions.error_text(exc, "select-rooms")
		return summary

	summary["rooms"] = len(rooms)
	for room in rooms:
		result = reconcile_room(
			str(room.get("name") or ""),
			since=since,
			reason=reason,
			client_factory=client_factory,
			ingest=ingest,
			alert=alert,
			preferred_subjects=preferred,
			skip_gate=True,
		)
		status = result.get("status")
		if status == "ok":
			summary["swept"] += 1
			summary["recovered"] += _as_int(result.get("recovered"), 0)
			summary["ingested"] += _as_int(result.get("ingested"), 0)
		elif status == "failed":
			summary["failed"] += 1
		else:
			summary["skipped"] += 1

	return summary
