# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Inbound processing — one raw ``Chat Inbound Event`` row turned into a decision and applied.

**Safe to run twice on the same row.** That is not a nicety. Pub/Sub is at-least-once by
contract, the production deploy ``FLUSHDB``s the queue Redis so an enqueued job simply
disappears mid-release, and Frappe v16 wires **no** RQ retries at all — so the only way a
delivery is ever guaranteed to be processed is that something re-drives it later
(:func:`sweep_stuck_inbound_events`) and processing it a second time is a no-op. Every
mutation below is therefore either structurally idempotent (a unique index absorbs the
second attempt) or explicitly compare-and-set.

Where the rules live, and where they deliberately do not
--------------------------------------------------------
**Every echo rule lives in :mod:`erpnext_enhancements.chat.sync.decisions`.** This module
gathers facts, calls :func:`~erpnext_enhancements.chat.sync.decisions.classify_inbound`, and
acts on the verdict. It re-implements none of the ladder. That split is the whole reason the
ladder is testable: ``decisions`` imports neither ``frappe`` nor ``requests``, so the rules
that decide whether a coworker's message gets duplicated — or silently eaten — are covered by
the bench-free CI tier, which is the only tier in this repo with automatic regression
protection (``CLAUDE.md``). A rule copied down here would be a rule with no test.

``RESOLVE_VIA_GET`` is the NORMAL path
--------------------------------------
The subscription runs ``payloadOptions.includeResource: false`` because it is the only
configuration with a **7-day** TTL ceiling, confirmed live against this tenant on 2026-08-09
(``PHASE2_VERIFIED.md`` §1.4: the same call with ``includeResource: true`` is refused with
*"The subscription expiration time exceeds the maximum allowed"*). The price is that an event
carries a resource name and nothing else — no body, no sender, no
``clientAssignedMessageId``. So almost every genuine inbound message costs one
``spaces.messages.get``, budgeted against the 3,000-reads-per-minute project bucket. Deleting
that read does not save a call; it removes the only source of the information the echo check
needs, and the failure mode is that every message ERPNext sends comes back and is stored a
second time.

The four ordering rules, and which of them this module owns
------------------------------------------------------------
ADR §G.8, by name. Rules 1 and 3 are implemented here; 2 is the unique index doing its job;
4 belongs to the outbound sweeper.

* **Rule 1 — CREATE-BEFORE-EDIT.** An ``updated`` naming a message we have never stored is
  applied as a **create** from the fetched resource (:func:`_apply_updated` falls through to
  :func:`_apply_created`). A ``deleted`` for a message we cannot name is recorded ``Ignored``,
  and **that is safe only because the create converges on its own**: when it is finally
  processed, its ``messages.get`` returns Google's tombstone and :func:`_apply_created` lands
  the row already deleted. Read that function before touching either half — an ``Ignored``
  delete with no such convergence behind it is the silent divergence this phase exists to
  prevent, and it is what the 200-message soak actually caught.
  Both come out of ``classify_inbound``'s verdict plus this module's event-type switch.
* **Rule 3 — LAST-WRITER-WINS BY ``lastUpdateTime``, ERPNEXT BREAKS TIES.** See
  :func:`_apply_updated`, which carries the full argument including the half people leave out.
* **Ordering is ``seq``, never a timestamp.** A late arrival **appends** — it is given the next
  ``seq`` like anything else and flagged :attr:`late_arrival` — because ``seq`` is immutable
  once assigned and Phase 5's digest watermarks and cache keys derive from it. Renumbering to
  put a late message "in the right place" would invalidate every cached digest in the room and
  break every stored watermark. Store both timestamps, display the origin's, sort by ``seq``.

Tombstones keep the body. This is a deliberate divergence
----------------------------------------------------------
The Phase 2 brief §4.F says move a deleted body to the audit table and clear the live row.
**The ADR wins and this module keeps the body on the row** (ADR §F.6.5 / §G.7.4,
``PHASE2_CONTRACT.md`` §9 item 2): Google's tombstone is rich in metadata and *empty of
content* — ``showDeleted=true`` returns the delete time and ``deletionMetadata``, never the
text — so if ERPNext does not keep the body, **nobody has it**. Read paths filter
``is_deleted = 0``; only the Phase 6 oversight endpoint sees through, and it pays with an
audit row.

A sender with no ERPNext ``User`` is stored, never dropped
-----------------------------------------------------------
ERPNext is the record of what was said. A Chat member who maps to no ``User`` — an external
participant, an unprovisioned coworker — is stored with :attr:`sender` ``None`` and
:attr:`sender_email` populated. ``Chat Message.validate`` enforces "one of the two, never
neither", which is why :func:`_resolve_sender` never returns a pair of empties.

Seams, and who owns which call
-------------------------------
:func:`~erpnext_enhancements.chat.seams.notify_new_message` must fire **exactly once per
genuinely new message and zero times** for echoes, duplicates, replays, reconciliations and
outbound relays. That single count is the cheapest proof the sync engine is not duplicating.

**This module does not call it, and that is how the property is kept.**
``Chat Message.after_insert`` — :func:`erpnext_enhancements.chat.sync.outbox.finalise_new_message`
— notifies and publishes ``chat_message_created`` once per inserted row, so inbound satisfies
the rule by inserting exactly one row per genuinely new message and returning early for
everything else. Two call sites, each individually defensible, is how a mirror ends up
notifying twice; the symptom in production is a notification storm and nothing wrong anywhere
in a log.

The same module owns the other half of the loop guard: ``outbox.relay_disposition`` refuses
``sync_origin == "Google Chat"`` before any other check, so an ingested message writes **no**
``Chat Relay Job`` and is never posted back to Google. Inbound therefore sets
:data:`SYNC_ORIGIN_INBOUND` before inserting and never touches the relay table itself.

What inbound *does* own: :func:`~erpnext_enhancements.chat.seams.mark_room_context_stale` on
**every** edit and **every** delete with the ``seq`` span that changed, and the
``chat_message_edited`` / ``chat_message_deleted`` publishes — ``after_insert`` cannot see
either, and without the staleness signal Phase 5 keeps serving a digest built from text the
user has already deleted.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final

import frappe

from erpnext_enhancements.chat import seams
from erpnext_enhancements.chat.doctype.chat_inbound_event.chat_inbound_event import (
	STATUS_FAILED,
	STATUS_IGNORED,
	STATUS_PROCESSED,
	STATUS_RECEIVED,
)
from erpnext_enhancements.chat.doctype.chat_message_revision.chat_message_revision import (
	CHANGE_CREATE,
	CHANGE_DELETE,
	CHANGE_EDIT,
	ORIGIN_GOOGLE_CHAT,
)
from erpnext_enhancements.chat.sync.decisions import (
	EVENT_MESSAGE_CREATED,
	EVENT_MESSAGE_DELETED,
	EVENT_MESSAGE_UPDATED,
	InboundFacts,
	InboundVerdict,
	MalformedEvent,
	ParsedEvent,
	fanout_events,
	is_client_message_id,
	parse_pubsub_envelope,
	singular_event_type,
)

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

#: ``Chat Message.sync_origin`` / ``sync_state`` for a row this module creates. Both are read
#: by the outbound path as "do not relay this", so they are set together and never apart.
SYNC_ORIGIN_INBOUND: Final[str] = "Google Chat"
SYNC_STATE_INBOUND: Final[str] = "Inbound"

#: ``Chat Relay Job.operation`` for the Rule 3 revert. A literal rather than an import: at the
#: time of writing ``outbox`` names only ``OPERATION_MESSAGE_CREATE`` as a constant, and the
#: value here has to match the DocType's Select option exactly. Move it to ``outbox`` the moment
#: that module grows the constant — two spellings of one enum value is a job that never runs.
OPERATION_MESSAGE_UPDATE: Final[str] = "Message Update"

#: ``Chat Message.client_message_id`` is ``reqd`` and carries ``unique(room,
#: client_message_id)``, so an inbound message needs one — but it must **not** look like an id
#: ERPNext minted. ``ids.is_client_message_id`` tests the ``client-`` prefix, and a foreign
#: message wearing that prefix would enter the echo ladder claiming provenance it does not
#: have. ``inbound-`` is outside that prefix by construction, and deriving it from the Google
#: resource name gives the create path a **second** structural dedupe key for free: two workers
#: racing on one redelivered event collide on ``unique(room, client_message_id)`` even in the
#: window before ``gchat_message_name`` is written.
INBOUND_CLIENT_ID_PREFIX: Final[str] = "inbound-"

#: Hex characters kept from the resource-name digest. 32 hex = 128 bits, the same budget
#: ``ids.client_message_id`` spends, and ``inbound-`` + 32 = 40 characters, inside the
#: ``Data(64)`` column with room to spare.
_INBOUND_CLIENT_ID_CHARS: Final[int] = 32

#: RFC 2606 reserves ``.invalid`` as a TLD guaranteed never to resolve. A Chat ``User``
#: resource carries ``name``/``displayName``/``type`` and **no email address** — Google does
#: not expose one on this surface at all — so when the ``users/{id}`` → ``User`` mapping fails
#: we still have to store *something*, because ``Chat Message.validate`` refuses a row with
#: neither ``sender`` nor ``sender_email`` and dropping the message is the one outcome decision
#: #1 forbids. A guaranteed-unresolvable address that embeds the Google user id is honest about
#: being a placeholder and is mechanically repairable by a later backfill; a fabricated
#: ``localpart@ourdomain`` would be a plausible-looking wrong answer that nobody would ever
#: notice was wrong.
UNRESOLVED_SENDER_DOMAIN: Final[str] = "unresolved.invalid"

#: How long a ``DEFER`` waits before the raw row is re-enqueued, and the ceiling on that.
#: Deliberately short: a defer exists because an outbound write for the room is in flight, and
#: those complete in about a second.
DEFER_DELAY_SECONDS: Final[int] = 5
DEFER_DELAY_CAP_SECONDS: Final[int] = 60

#: Fallback for ``Chat Settings.echo_defer_budget`` when the field cannot be read.
DEFAULT_DEFER_BUDGET: Final[int] = 3

#: How many recent inbound messages the skew alarm takes its **median** over. §4.G asks for a
#: median across a window rather than a per-message threshold precisely so that one delayed
#: redelivery — which is normal and self-correcting — cannot page anybody.
SKEW_SAMPLE_SIZE: Final[int] = 50

#: A ``Received`` row older than this with nobody working on it is assumed lost to a deploy's
#: ``FLUSHDB`` and is re-enqueued by the sweeper.
STUCK_EVENT_MINUTES: Final[int] = 10

#: ``deletionMetadata.deletionType`` → ``Chat Message.deletion_source``.
#:
#: The two ``_VIA_APP`` values are **ours**: ``CREATOR_VIA_APP`` is what a DWD-impersonated
#: delete on behalf of the author produces and ``SPACE_OWNER_VIA_APP`` an impersonated manager,
#: so both mean "ERPNext asked for this" even when we arrive at the tombstone by another route.
#: Attribution is clean without guessing, which is why the ADR chose to read this field rather
#: than infer intent from timing.
DELETION_TYPE_TO_SOURCE: Final[Mapping[str, str]] = {
	"CREATOR": "Google Chat",
	"SPACE_OWNER": "Google Chat",
	"SPACE_MEMBER": "Google Chat",
	"ADMIN": "Admin",
	"APP_MESSAGE_EXPIRY": "Retention",
	"CREATOR_VIA_APP": "ERPNext",
	"SPACE_OWNER_VIA_APP": "ERPNext",
}

#: Fallback when ``deletionMetadata`` is absent or carries a value Google added after this was
#: written. "Google Chat" rather than "" because we know at minimum that Chat told us.
DEFAULT_DELETION_SOURCE: Final[str] = "Google Chat"

#: What goes in ``Chat Message.text`` for a message we only ever saw as a **tombstone** — see
#: :func:`_apply_created`. The body is not empty, it is *unrecoverable*, and those are different
#: facts: an empty string reads as "somebody sent a blank message", which is the exact confusion
#: :func:`resource_text` refuses to introduce one layer down.
#:
#: A self-describing notice rather than ``""`` for the same reason
#: :data:`UNRESOLVED_SENDER_DOMAIN` is a guaranteed-unresolvable address rather than a
#: fabricated one: the only reader this row will ever have is the Phase 6 oversight path, and a
#: blank body tells them nothing while looking exactly like a bug in the soft delete. Bracketed
#: and in the first person plural so it can never be mistaken for something a coworker typed,
#: and greppable so a later backfill can find every one of them if Google ever exposes a way to
#: recover a deleted body (today there is none — the tombstone is metadata and nothing else).
TOMBSTONE_BODY_NOTICE: Final[str] = (
	"[This message was already deleted in Google Chat when ERPNext first saw it, so its text "
	"was never delivered to us. Google's tombstone carries metadata only; the body is "
	"unrecoverable.]"
)

#: Google emits RFC-3339 with a ``Z`` and, occasionally, more than six fractional digits.
#: ``datetime.fromisoformat`` accepts three or six and nothing else, so the extras are trimmed
#: rather than allowed to raise inside the one comparison Rule 3 depends on.
_RFC3339_FRACTION_RE: Final[re.Pattern[str]] = re.compile(r"(\.\d{1,9})")

#: ``spaces/{space}/members/{member}`` — the member segment **is** the Google user id, which is
#: what makes ``Chat Room Member.gchat_membership_name`` a usable ``users/{id}`` → ``User`` map.
_MEMBERSHIP_TAIL_RE: Final[re.Pattern[str]] = re.compile(r"/members/([A-Za-z0-9_.-]+)\Z")


# --------------------------------------------------------------------------------------
# Settings, logging, alarms
# --------------------------------------------------------------------------------------


def _settings() -> Any:
	"""``Chat Settings``, or ``None``.

	Never raises. This runs in a background worker during ``bench migrate`` and during
	ERPNext's own test bootstrap, when the single doc may not exist yet; a missing settings
	row must degrade every tunable to its default, not crash the ingest pipeline.
	"""
	try:
		return frappe.get_cached_doc("Chat Settings")
	except Exception:
		return None


def _setting_int(fieldname: str, default: int) -> int:
	"""One integer tunable, defensively. ``getattr(obj, field, None) or default`` per house rule."""
	raw = getattr(_settings(), fieldname, None)
	if raw in (None, ""):
		return int(default)
	try:
		return int(raw)
	except (TypeError, ValueError):
		return int(default)


def _setting_bool(fieldname: str, default: bool = False) -> bool:
	raw = getattr(_settings(), fieldname, None)
	if raw in (None, ""):
		return bool(default)
	return bool(raw)


def _logger() -> Any:
	try:
		return frappe.logger("chat")
	except Exception:  # pragma: no cover - only on a half-booted frappe
		return None


def _log(level: str, summary: str, **fields: Any) -> None:
	"""Structured-ish log line. **Never** a message body, never a token.

	The first parameter is ``summary`` and not ``message`` on purpose: half the call sites in
	this module want to log a ``message=<Chat Message name>`` field, and naming the positional
	``message`` makes every one of them a ``TypeError`` — on the error path, where it is least
	likely to be exercised before production.

	Field values are coerced with ``str`` and the caller is responsible for passing only
	identifiers, lengths and hashes — the same discipline ``client.build_log_record`` applies.
	"""
	log = _logger()
	if log is None:
		return
	try:
		rendered = " ".join(f"{key}={value}" for key, value in fields.items())
		getattr(log, level, log.info)(f"chat inbound {summary} {rendered}".rstrip())
	except Exception:
		return


def _alarm(kind: str, summary: str, **fields: Any) -> None:
	"""An operator-visible alarm: a counter, a warning line, and an Error Log row.

	Alarms exist for the cases where guessing would be worse than stopping —
	``ECHO_ORPHAN`` above all. Never raises: an alarm that can abort the transaction it is
	reporting on turns a recoverable anomaly into lost data.
	"""
	_log("warning", f"ALARM {kind}: {summary}", **fields)
	try:
		rendered = ", ".join(f"{key}={value}" for key, value in fields.items())
		frappe.log_error(
			title=f"Chat inbound: {kind}",
			message=f"{summary}\n\n{rendered}",
		)
	except Exception:
		return


def clear_expected_duplicate_message() -> None:
	"""Drop the *"must be unique"* msgprint Frappe emits **before** raising.

	``show_unique_validation_message`` calls ``frappe.msgprint`` and only then raises, so an
	expected duplicate — which is the single most common outcome on this path, because Pub/Sub
	redelivers — would otherwise surface a user-visible validation banner for an event the
	design treats as a success.
	"""
	try:
		clear = getattr(frappe, "clear_messages", None)
		if callable(clear):
			clear()
			return
		message_log = getattr(frappe.local, "message_log", None)
		if isinstance(message_log, list):
			message_log.clear()
	except Exception:
		return


def duplicate_errors() -> tuple[type[BaseException], ...]:
	"""The two exception classes a unique collision can raise, and why both are needed.

	``frappe.DuplicateEntryError`` is raised **only** for a primary-key collision — a duplicate
	``name``. Every *other* unique index, which is exactly what ``gchat_message_name``,
	``unique(room, client_message_id)`` and ``unique(message, revision_no)`` are, raises
	``frappe.UniqueValidationError``. They share no base beyond ``Exception``
	(``DuplicateEntryError(NameError)`` vs ``UniqueValidationError(ValidationError)``), so
	catching the first alone — which ADR §G.3.1 and §G.8 Rule 2 both instruct — would fail to
	catch the collision the whole design rests on. ``PHASE2_VERIFIED.md`` §1.1.

	Resolved through ``getattr`` rather than named directly so that a Frappe version missing
	one of them degrades to catching the other instead of failing at import.
	"""
	found = [
		getattr(frappe, "DuplicateEntryError", None),
		getattr(frappe, "UniqueValidationError", None),
	]
	classes = tuple(cls for cls in found if isinstance(cls, type) and issubclass(cls, BaseException))
	return classes or (Exception,)


def savepoint_catching_duplicates() -> Any:
	"""``frappe.database.database.savepoint`` armed with both duplicate classes.

	The savepoint is **mandatory, not hygiene**: a failed statement can poison the surrounding
	transaction, so a caught duplicate without one leaves every subsequent write in this job
	doomed — and in the puller's batch loop that would mean the rest of the batch silently
	failing to commit. Frappe's own docstring models exactly this pattern.

	Public because :mod:`erpnext_enhancements.chat.sync.pubsub` needs the identical guard for
	``unique(pubsub_message_id)``. One implementation, so the two dedupe paths cannot drift.
	"""
	from frappe.database.database import savepoint

	return savepoint(catch=duplicate_errors())


# --------------------------------------------------------------------------------------
# Time — every comparison is explicit, because a naive one is wrong by the site offset
# --------------------------------------------------------------------------------------


def parse_google_timestamp(value: Any) -> datetime | None:
	"""RFC-3339 (UTC, ``Z``-suffixed) → an **aware UTC** ``datetime``, or ``None``.

	Aware on purpose. Google returns UTC and Frappe writes site-local naive datetimes, and the
	one comparison that decides Rule 3 sits exactly on that seam: comparing them naively is
	wrong by the site's offset **in the direction that always makes Google win**, which
	silently inverts decision #1 (ERPNext is the source of truth). Returning an aware value
	makes a naive comparison a ``TypeError`` at the line that is wrong rather than a wrong
	answer in production.
	"""
	text = str(value or "").strip()
	if not text:
		return None
	# Trim sub-microsecond precision: Google occasionally emits nanoseconds and
	# `fromisoformat` accepts three or six fractional digits only.
	text = _RFC3339_FRACTION_RE.sub(lambda m: m.group(1)[:7], text)
	if text.endswith(("Z", "z")):
		text = text[:-1] + "+00:00"
	try:
		parsed = datetime.fromisoformat(text)
	except ValueError:
		return None
	if parsed.tzinfo is None:
		# A timestamp with no offset from a UTC-only API is UTC. Stated rather than assumed.
		return parsed.replace(tzinfo=UTC)
	return parsed.astimezone(UTC)


def _site_timezone() -> Any:
	"""The site's ``ZoneInfo``, or ``UTC`` if it cannot be resolved."""
	try:
		from zoneinfo import ZoneInfo

		from frappe.utils import get_system_timezone

		return ZoneInfo(str(get_system_timezone() or "UTC"))
	except Exception:
		return UTC


def utc_to_site(value: datetime | None) -> datetime | None:
	"""Aware UTC → the **naive site-local** datetime Frappe stores in a ``Datetime`` column."""
	if value is None:
		return None
	aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
	return aware.astimezone(_site_timezone()).replace(tzinfo=None)


def site_to_utc(value: Any) -> datetime | None:
	"""A Frappe ``Datetime`` (naive, site-local) → an **aware UTC** datetime.

	The other half of :func:`parse_google_timestamp`, and the reason Rule 3's comparison is
	sound. Accepts a string or a ``datetime`` because ``frappe.db.get_value`` returns either
	depending on the driver and the column.
	"""
	if value in (None, ""):
		return None
	if isinstance(value, datetime):
		local = value
	else:
		try:
			from frappe.utils import get_datetime

			local = get_datetime(value)
		except Exception:
			return None
	if local is None:
		return None
	if local.tzinfo is not None:
		return local.astimezone(UTC)
	return local.replace(tzinfo=_site_timezone()).astimezone(UTC)


def _now_utc() -> datetime:
	"""Site clock, as aware UTC.

	``frappe.utils.now_datetime()`` is **site-local** — this app has already shipped one bug
	from treating it as UTC (the Turnstile always-fail) — so it is converted rather than used
	directly.
	"""
	try:
		from frappe.utils import now_datetime

		converted = site_to_utc(now_datetime())
		if converted is not None:
			return converted
	except Exception:
		pass
	return datetime.now(UTC)


# --------------------------------------------------------------------------------------
# Probes — the facts `classify_inbound` needs
# --------------------------------------------------------------------------------------


def _room_for_space(space_name: str) -> str | None:
	"""``unique(gchat_space_name)`` probe. ``None`` ⇒ a space we do not mirror.

	Checked before anything that could write, so an un-mirrored space cannot create rows by
	any path — chaos case 12.
	"""
	name = str(space_name or "").strip()
	if not name:
		return None
	return frappe.db.get_value("Chat Room", {"gchat_space_name": name}, "name") or None


def _stored_by_resource_name(resource_name: str) -> tuple[str | None, bool]:
	"""``unique(gchat_message_name)`` probe → ``(Chat Message.name, is_deleted)``."""
	name = str(resource_name or "").strip()
	if not name:
		return (None, False)
	row = frappe.db.get_value(
		"Chat Message", {"gchat_message_name": name}, ["name", "is_deleted"], as_dict=True
	)
	if not row:
		return (None, False)
	return (row.get("name"), bool(row.get("is_deleted")))


def _local_by_client_id(room: str, client_message_id: str | None) -> str | None:
	"""``unique(room, client_message_id)`` probe — the authoritative half of invariant I3.

	*Looks like ours* (the ``client-`` prefix) and *is ours* (this index) are different
	claims: any Chat client may set a ``client-`` prefixed id, so treating the prefix as proof
	would let a third party suppress ingestion of their own messages by guessing it.
	"""
	value = str(client_message_id or "").strip()
	if not room or not value:
		return None
	return frappe.db.get_value("Chat Message", {"room": room, "client_message_id": value}, "name") or None


def _in_flight_claims(room: str) -> int:
	"""``Chat Relay Job`` rows ``In Progress`` with an **unexpired** lease for this room.

	The lease is what makes this a fact rather than a guess: a crashed worker leaves a row
	``In Progress`` forever, and counting those would defer every inbound event in the room
	until the defer budget ran out. ``lease_expires_at`` is the in-flight claim *and* the
	crashed-worker detector, one field.
	"""
	if not room:
		return 0
	try:
		from frappe.utils import now_datetime

		return int(
			frappe.db.count(
				"Chat Relay Job",
				{"room": room, "status": "In Progress", "lease_expires_at": (">", now_datetime())},
			)
		)
	except Exception:
		# A missing table or column during bootstrap must not defer real traffic.
		return 0


# --------------------------------------------------------------------------------------
# The Google read
# --------------------------------------------------------------------------------------


def _reader_subject(room: str) -> str:
	"""Which human the ``messages.get`` impersonates, chosen **deterministically**.

	Shape B gives every coworker their own ``spaces/-`` subscription under user auth, and the
	Chat app itself is frequently not a member of the space at all, so the read must be made as
	somebody who is. Ordering by ``user`` rather than by recency means a retry of the same event
	uses the same principal: a read that succeeds on one attempt and 403s on the next, because
	the "most recent member" changed between them, is a fault that reproduces for nobody.
	"""
	rows = frappe.get_all(
		"Chat Room Member",
		filters={"room": room, "is_active": 1},
		fields=["user"],
		order_by="user asc",
		limit=1,
	)
	subject = (rows[0].get("user") if rows else "") or ""
	if subject:
		return str(subject)

	settings = _settings()
	for row in getattr(settings, "allowed_users", None) or []:
		candidate = getattr(row, "user", None) or ""
		if candidate:
			return str(candidate)

	raise frappe.ValidationError(
		f"Chat Room {room} has no active member and Chat Settings lists no allowed user, so there is "
		"no identity to read the message as. spaces.messages.get under shape B is user-authenticated; "
		"the Chat app is not necessarily a member of the space."
	)


def fetch_message_resource(room: str, resource_name: str, *, client: Any = None) -> dict[str, Any]:
	"""``spaces.messages.get`` for one resource name. ``client`` is the test injection point.

	Exceptions are re-raised ``from None``. A bare re-raise out of a background job publishes
	the failing frames' locals into the Error Log, and the transport's frame holds the
	``Authorization: Bearer`` header — this app has leaked private key material exactly that
	way once.
	"""
	if client is None:
		from erpnext_enhancements.chat.gchat.client import GoogleChatClient

		client = GoogleChatClient(subject=_reader_subject(room))
	try:
		return dict(client.get_message(resource_name) or {})
	except Exception as exc:
		raise frappe.ValidationError(
			f"spaces.messages.get failed for a mirrored message: {type(exc).__name__}: {exc}"
		) from None


# --------------------------------------------------------------------------------------
# Reading a Message resource
# --------------------------------------------------------------------------------------


def resource_text(resource: Mapping[str, Any]) -> str:
	"""The body, or ``""`` for a tombstone.

	A tombstone carries ``deleteTime`` and ``deletionMetadata`` and **no ``text`` key at all** —
	not an empty string, which a caller could mistake for a message somebody sent blank.
	"""
	return str(resource.get("text") or "")


def resource_tombstone(resource: Mapping[str, Any]) -> tuple[datetime, str, str] | None:
	"""``(deleted_at_utc, deletion_source, deletionType)`` if this resource is a tombstone.

	``None`` means Google handed us a live message. **This is the pending-tombstone probe**, and
	the thing it probes is Google's own copy of the message rather than any row of ours — see
	:func:`_apply_created` for why that turned out to be the durable place to keep it.

	Both keys are tested because either alone is a guess: ``deleteTime`` is the timestamp and
	``deletionMetadata`` is the attribution, and a tombstone that arrived with only one of them
	is still a tombstone. Neither is ever present on a live message.
	"""
	delete_time = resource.get("deleteTime")
	metadata = resource.get("deletionMetadata")
	if not delete_time and not metadata:
		return None
	deletion_type = str((metadata or {}).get("deletionType") or "")
	return (
		parse_google_timestamp(delete_time) or _now_utc(),
		DELETION_TYPE_TO_SOURCE.get(deletion_type, DEFAULT_DELETION_SOURCE),
		deletion_type,
	)


def resource_client_id(resource: Mapping[str, Any]) -> str | None:
	"""``clientAssignedMessageId``, or ``None``.

	``VERIFY:`` whether the real API populates this on a read is **unproven**
	(``PHASE2_VERIFIED.md`` §8.1). The proto marks it ``OPTIONAL`` rather than ``OUTPUT_ONLY``
	and the discovery document does not mark it ``readOnly``, so the design is not inverted —
	but no Google document or sample shows it populated in a response. Echo suppression's
	primary path depends on the answer, and one live round trip settles it: create a message
	with a ``client-`` id, ``get`` it, print the field. Until then the bounded fallback
	(``Chat Settings.echo_fallback_enabled``, off by default) is the only other rung.
	"""
	value = str(resource.get("clientAssignedMessageId") or "").strip()
	return value or None


def _resource_user_id(resource: Mapping[str, Any]) -> str:
	"""``users/{id}`` → ``{id}``, from ``Message.sender``."""
	sender = resource.get("sender")
	name = str((sender or {}).get("name") or "") if isinstance(sender, Mapping) else ""
	return name.rsplit("/", 1)[-1].strip() if name else ""


def _resolve_sender(room: str, resource: Mapping[str, Any]) -> tuple[str | None, str]:
	"""``(User or None, sender_email)`` for a Chat sender. **Never both empty.**

	The Chat ``User`` resource carries ``name`` (``users/{id}``), ``displayName``, ``type`` and
	``domainId`` — and **no email address**. Google does not expose one on this surface, so the
	mapping has to come from somewhere we already wrote down:
	``Chat Room Member.gchat_membership_name`` is ``spaces/{space}/members/{member}`` and the
	member segment *is* the Google user id, so the membership rows the provisioning path
	already maintains are the map.

	When that lookup misses — an external participant, a coworker nobody has provisioned here,
	a room whose memberships have not been reconciled yet — the message is **still stored**,
	with ``sender = None`` and a placeholder address under
	:data:`UNRESOLVED_SENDER_DOMAIN`. ERPNext is the record of what was said; a mirror that
	silently drops the messages in which the conversation left the company acquires holes in
	exactly the rows somebody will one day need. Chaos case 13.
	"""
	user_id = _resource_user_id(resource)

	if room and user_id:
		# `like` on a suffix cannot use the index, but the candidate set is one room's
		# membership — tens of rows — and the alternative is a second denormalised column
		# nothing else needs.
		rows = frappe.get_all(
			"Chat Room Member",
			filters={"room": room, "gchat_membership_name": ("like", f"%/members/{user_id}")},
			fields=["user", "gchat_membership_name"],
			order_by="user asc",
			limit=5,
		)
		for row in rows:
			tail = _MEMBERSHIP_TAIL_RE.search(str(row.get("gchat_membership_name") or ""))
			if tail and tail.group(1) == user_id and row.get("user"):
				return (str(row["user"]), str(row["user"]))

	placeholder = f"chat-user-{user_id or 'unknown'}@{UNRESOLVED_SENDER_DOMAIN}"
	return (None, placeholder)


def _inbound_client_id(resource_name: str) -> str:
	"""A deterministic, non-``client-`` id for an inbound row. See :data:`INBOUND_CLIENT_ID_PREFIX`."""
	digest = hashlib.sha256(str(resource_name or "").encode("utf-8")).hexdigest()
	return INBOUND_CLIENT_ID_PREFIX + digest[:_INBOUND_CLIENT_ID_CHARS]


# --------------------------------------------------------------------------------------
# seq allocation
# --------------------------------------------------------------------------------------


def _outbox_attr(name: str) -> Any:
	"""Late-bound handle on ``sync.outbox``, or ``None`` if it is not present yet.

	The write path (seq allocation, relay-job creation) is another module's; reaching for it
	lazily means this one imports cleanly whether or not that module has landed, and a missing
	function degrades to a documented local fallback rather than an ``ImportError`` inside a
	background worker.
	"""
	try:
		from erpnext_enhancements.chat.sync import outbox
	except Exception:
		return None
	candidate = getattr(outbox, name, None)
	return candidate if callable(candidate) else None


def _attachments_attr(name: str) -> Any:
	"""Late-bound handle on ``sync.attachments``, or ``None``. Same contract as :func:`_outbox_attr`.

	The degradation this buys is the point: if the attachment ingest is missing or broken, the
	message is still stored. Losing the file is bad; losing the message because of the file is
	worse, and ERPNext is the record of what was said.
	"""
	try:
		from erpnext_enhancements.chat.sync import attachments
	except Exception:
		return None
	candidate = getattr(attachments, name, None)
	return candidate if callable(candidate) else None


def insert_message(doc: Any) -> bool:
	"""Insert one ``Chat Message`` through the write path. ``False`` ⇒ it already existed.

	Delegates to :func:`erpnext_enhancements.chat.sync.outbox.insert_message`, which is the
	documented entry point for **every** writer — the composer, this ingest and the
	reconciliation backfill alike — and which owns three things inbound must not duplicate:

	* ``seq`` allocation under the ``Chat Room`` row lock, with the ``unique(room, seq)``
	  collision retried and the counter repaired outside the rolled-back savepoint. A **late
	  arrival still appends**: it takes the next ``seq`` like anything else, because ``seq`` is
	  immutable once assigned and Phase 5's digest watermarks and cache keys derive from it.
	  Renumbering to make the transcript read chronologically would invalidate every stored
	  watermark in the room. Display the origin's timestamp; sort by ``seq``.
	* the relay gate. ``outbox.relay_disposition`` refuses ``sync_origin == "Google Chat"``
	  outright — *"inbound: this row came from Chat and relaying it back is the echo loop"* —
	  so an ingested message writes **no** outbox row. That is why this module sets
	  ``sync_origin`` before inserting and never touches ``Chat Relay Job``.
	* ``notify_new_message`` and the ``chat_message_created`` realtime publish, both fired from
	  ``after_insert`` for every inserted row. **Inbound therefore does not call either**, and
	  the "exactly once per genuinely new message" property becomes structural: one insert, one
	  notify. Echoes, duplicates, replays and tombstones never reach this function at all.

	The fallback exists only so this module imports and behaves sanely before that one lands;
	it is not a second write path and it is not maintained as one.
	"""
	writer = _outbox_attr("insert_message")
	if writer is not None:
		try:
			writer(doc)
			return True
		except duplicate_errors():
			# `outbox._insert_catching_duplicates` has already rolled back to its own savepoint
			# and cleared the toast, then re-raised because the collision is the caller's to
			# interpret. For inbound it means "already stored", which is Rule 2's SUCCESS.
			clear_expected_duplicate_message()
			return False

	inserted = False
	with savepoint_catching_duplicates():
		doc.insert(ignore_permissions=True)
		inserted = True
	if not inserted:
		clear_expected_duplicate_message()
	return inserted


# --------------------------------------------------------------------------------------
# Revisions
# --------------------------------------------------------------------------------------


def record_revision(
	message: str,
	*,
	room: str,
	change_type: str,
	text_before: str = "",
	text_after: str = "",
	actor: str | None = None,
	actor_email: str = "",
	origin: str = ORIGIN_GOOGLE_CHAT,
	origin_timestamp: datetime | None = None,
	note: str = "",
) -> str | None:
	"""Write one ``Chat Message Revision``. Returns its name, or ``None`` if it already existed.

	``revision_no`` is allocated as ``max + 1`` **inside the caller's transaction**, and
	``unique(message, revision_no)`` is what makes "the same change recorded twice" a failed
	insert rather than two audit rows both claiming to be revision 4. Every writer of this
	table is retried — the relay sweeper re-drives a crashed job, Pub/Sub redelivers at least
	once — so the collision is an expected outcome and is treated as success, exactly like the
	``gchat_message_name`` one.

	``origin_timestamp`` is the originating side's own clock, converted from RFC-3339 UTC to
	site-local, and is stored *alongside* ``creation`` rather than instead of it: ``creation``
	is when we learned, this is when it happened, and the gap between them is the only evidence
	of a delayed redelivery.
	"""
	from erpnext_enhancements.chat.doctype.chat_message_revision.chat_message_revision import (
		ChatMessageRevision,
	)

	next_no = ChatMessageRevision.next_revision_no(message)

	doc = frappe.new_doc("Chat Message Revision")
	doc.message = message
	doc.room = room
	doc.revision_no = next_no
	doc.change_type = change_type
	doc.origin = origin
	doc.origin_timestamp = utc_to_site(origin_timestamp)
	doc.actor = actor
	doc.actor_email = actor_email
	doc.text_before = text_before
	doc.text_after = text_after
	doc.note = note

	inserted = False
	with savepoint_catching_duplicates():
		doc.insert(ignore_permissions=True)
		inserted = True
	if not inserted:
		clear_expected_duplicate_message()
		return None
	return str(doc.name)


# --------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------


def enqueue_inbound_event(inbound_event: str) -> None:
	"""Queue :func:`process_inbound_event` for one raw row.

	**The keyword is ``inbound_event`` and never ``event``.** ``frappe.enqueue``'s own
	signature is ``(method, queue, timeout, event, is_async, job_name, now,
	enqueue_after_commit, …, **kwargs)``, so a kwarg named ``event`` is **consumed by enqueue
	itself** and never reaches the job — which calls ``process_inbound_event()`` with no
	arguments and dies with a ``TypeError`` in a worker nobody is watching. ``queue``,
	``timeout``, ``now``, ``job_name`` and ``is_async`` are reserved the same way. This app has
	been bitten by the same class of bug before, from the other direction: Frappe pops a POSTed
	key named ``sid`` and answers "session expired".

	``enqueue_after_commit=True`` is a literal, and ``tests/test_chat_guardrails.py`` lints for
	it: a job dispatched inside the enqueueing transaction runs against a row that does not
	exist yet, which is the "event vanished" bug that only reproduces under load.

	**The enqueue is an optimisation, never the delivery guarantee.** A production deploy runs
	``redis-cli -p 11000 FLUSHDB`` and restarts the whole honcho group, so queued jobs do not
	survive a release, and Frappe v16 wires no RQ retries at all.
	:func:`sweep_stuck_inbound_events` re-drives anything the queue lost, and the row is what
	makes that possible.

	**There is no delay parameter, deliberately.** ``frappe.enqueue`` exposes no route to RQ's
	scheduler, and RQ's own scheduling lives in the Redis a deploy flushes — so a
	``delay_seconds`` argument here could only ever be dropped on the floor, which is exactly
	what it was doing: a ``DEFER`` re-entered the queue instantly and three defers spent the
	whole ``Chat Settings.echo_defer_budget`` inside a millisecond, waiting out nothing. The
	delay is durable now and lives on the row as ``Chat Inbound Event.available_at``; see
	:func:`_defer`.
	"""
	name = str(inbound_event or "").strip()
	if not name:
		return
	try:
		frappe.enqueue(
			"erpnext_enhancements.chat.sync.inbound.process_inbound_event",
			queue="short",
			inbound_event=name,
			enqueue_after_commit=True,
		)
	except Exception as exc:
		_log("warning", "enqueue failed; the sweeper will re-drive", event=name, error=type(exc).__name__)


def process_inbound_event(inbound_event: str, client: Any = None) -> str:
	"""Process one ``Chat Inbound Event`` row. **Idempotent.** Returns a short status string.

	Takes the docname positionally, which is the contract ``sync.reconcile`` codes against
	(``processor(event_name)``). The parameter is **not** called ``event`` — see
	:func:`enqueue_inbound_event` for why that name cannot survive a trip through
	``frappe.enqueue``.

	``client`` is the ``spaces.messages.get`` injection point and is never passed in
	production; it exists so the bench-free suite can drive the whole pipeline against
	``testing.fake_chat``.

	The row's ``status`` is the idempotence gate: a second run of a settled row returns
	immediately. That matters because there are three independent things that can re-drive a
	row — an RQ retry that never happens, the sweeper, and an operator — and because the
	interesting failure is not "processed twice" but "processed twice *differently*".
	"""
	name = str(inbound_event or "").strip()
	if not name:
		return "no-event"

	try:
		row = frappe.get_doc("Chat Inbound Event", name)
	except Exception:
		_log("warning", "inbound row disappeared before processing", event=name)
		return "missing"

	status = str(getattr(row, "status", None) or STATUS_RECEIVED)
	if status in (STATUS_PROCESSED, STATUS_IGNORED, STATUS_FAILED):
		return "already-settled"

	if not _setting_bool("relay_inbound_enabled", False) or _setting_bool("pause_inbound", False):
		# Deliberately leaves the row `Received`. The kill switch pauses ingestion; it does not
		# discard it. Turning inbound back on and letting the sweeper drain is the recovery.
		_log("info", "inbound paused; leaving row for the sweeper", event=name)
		return "paused"

	try:
		payload = frappe.parse_json(getattr(row, "payload", None) or "{}")
	except Exception:
		payload = {}

	try:
		parsed = parse_pubsub_envelope(payload if isinstance(payload, Mapping) else {})
	except MalformedEvent as exc:
		# The payload is kept verbatim, so a parser fix plus a re-drive recovers this row. That
		# is the entire reason `Chat Inbound Event.payload` is untouched and why `Failed` rows
		# are exempt from retention.
		_settle(row, STATUS_FAILED, last_error=f"MalformedEvent: {exc}")
		return "malformed"

	if parsed.is_lifecycle:
		return _route_lifecycle(row, parsed)

	verdicts: list[str] = []
	first_message: str | None = None
	deferred = False

	try:
		for singular in fanout_events(parsed):
			verdict, message_name = _apply_event(row, singular, client=client)
			verdicts.append(verdict.value)
			if message_name and first_message is None:
				first_message = message_name
			if verdict is InboundVerdict.DEFER:
				deferred = True
	except Exception as exc:
		# **Nothing is allowed to escape this frame**, and not only because a background job
		# that raises is retried by nothing in v16. Frappe's job error handler writes
		# ``get_traceback(with_context=True)`` into the Error Log, and *with context* means the
		# failing frames' local variables — which here hold the fetched Message resource, i.e.
		# a coworker's message body, and one frame further down the ``Authorization: Bearer``
		# header. This app has published private key material exactly that way once.
		return _fail_or_retry(row, exc)

	if deferred:
		_defer(row)
		return "DEFER"

	if first_message:
		try:
			frappe.db.set_value(
				"Chat Inbound Event", row.name, "resulting_message", first_message, update_modified=False
			)
		except Exception:
			pass

	settled = _settle_from_verdicts(row, verdicts)
	return ",".join(verdicts) if verdicts else settled


def _fail_or_retry(row: Any, exc: BaseException) -> str:
	"""A processing failure: leave it for the sweeper, or give up loudly. Never re-raise.

	The distinction is ``attempts`` against ``Chat Settings.relay_max_attempts``. Almost every
	failure here is transient — Google 5xx'd, a token mint timed out, the row's room was being
	reconciled — and the right answer is to leave the row ``Received`` so
	:func:`sweep_stuck_inbound_events` re-drives it. A permanent one (a room with no member to
	read as, a resource Google 404s) would otherwise be retried every ten minutes forever,
	turning one anomaly into a permanent load and burying its own Error Log entry, so the
	attempt count is a ceiling.

	The stored reason is the **exception class and its message**, truncated. Not a traceback:
	see the note at the call site.
	"""
	attempts = int(getattr(row, "attempts", None) or 0) + 1
	ceiling = _setting_int("relay_max_attempts", 5)
	reason = f"{type(exc).__name__}: {exc}"

	try:
		frappe.db.set_value(
			"Chat Inbound Event",
			row.name,
			# ``available_at`` is **cleared**, not pushed forward. A failure retry is not a defer,
			# and leaving a past defer timestamp behind would keep this row in the sweeper's
			# "due now" population and re-drive it once a minute until the attempt ceiling. Empty
			# returns it to the lost-job population, which waits :data:`STUCK_EVENT_MINUTES`
			# between attempts exactly as it did before ``available_at`` existed.
			{"attempts": attempts, "available_at": None},
			update_modified=False,
		)
	except Exception:
		pass

	if attempts >= max(ceiling, 1):
		_alarm(
			"INBOUND_GIVING_UP",
			"an inbound delivery failed on every attempt and is now Failed. The payload is kept "
			"verbatim, so this is reprocessable once the cause is fixed — Failed rows are exempt "
			"from retention for exactly that reason.",
			event=row.name,
			attempts=attempts,
			reason=reason,
		)
		return _settle(row, STATUS_FAILED, last_error=reason)

	_log(
		"warning", "inbound processing failed; leaving it for the sweeper", event=row.name, attempts=attempts
	)
	try:
		frappe.db.set_value("Chat Inbound Event", row.name, "last_error", reason[:500], update_modified=False)
	except Exception:
		pass
	return "retry"


def sweep_stuck_inbound_events(limit: int | None = None) -> dict[str, int]:
	"""Re-drive ``Received`` rows nobody finished. **The delivery guarantee, not the queue.**

	Three things lose an enqueued job and none of them leave a trace in any log: the production
	deploy ``FLUSHDB``s the queue Redis and restarts honcho mid-flight; Frappe v16 passes no
	``retry=`` to RQ anywhere, so a worker exception is simply the end of that job; and a
	``DEFER`` deliberately puts a row back without a durable timer behind it. The row is the
	only durable record, so this sweep is what actually delivers.

	``Failed`` rows are **not** re-driven. They carry evidence of something that needs a human
	— a malformed payload, an ``ECHO_ORPHAN`` — and a loop that retried them forever would turn
	one anomaly into a permanent load and bury the Error Log it wrote.

	**It is also the defer timer**, which is why it runs every minute rather than every ten
	(``hooks.py`` ``scheduler_events["cron"]["* * * * *"]``). :func:`_defer` cannot schedule a
	delayed job — ``frappe.enqueue`` reaches no scheduler and RQ's lives in the flushed Redis —
	so it writes ``available_at`` and stops, and this sweep's interval becomes the real
	granularity of a defer. At ten minutes a three-defer budget would be a thirty-minute worst
	case for one message; at one minute it is three, against a wait that exists to outlast an
	outbound write of about a second.
	"""
	from frappe.utils import add_to_date, now_datetime

	now = now_datetime()
	cutoff = add_to_date(now, minutes=-STUCK_EVENT_MINUTES)
	batch = int(limit or _setting_int("sweeper_batch_size", 200))

	# Two selections, not one, because the two populations share nothing but a status. A
	# **deferred** row asked to be picked up at a stated moment and must not serve
	# STUCK_EVENT_MINUTES on top of its own delay; a row the queue **lost** asked for nothing,
	# and re-driving it early would race a worker that may still be holding it.
	due = frappe.get_all(
		"Chat Inbound Event",
		filters={"status": STATUS_RECEIVED, "available_at": ("<=", now)},
		pluck="name",
		order_by="available_at asc",
		limit=batch,
	)
	# `frappe.get_all(limit=0)` means *no limit*, not *no rows* — so a full batch skips the
	# second query outright rather than passing it a zero.
	remaining = batch - len(due)
	lost = (
		frappe.get_all(
			"Chat Inbound Event",
			filters={
				"status": STATUS_RECEIVED,
				"available_at": ("is", "not set"),
				"received_at": ("<", cutoff),
			},
			pluck="name",
			order_by="received_at asc",
			limit=remaining,
		)
		if remaining > 0
		else []
	)

	names = list(due) + list(lost)
	for name in names:
		enqueue_inbound_event(name)

	if names:
		_log("info", "re-drove stuck inbound rows", count=len(names), due=len(due), lost=len(lost))
	return {"requeued": len(names), "deferred_due": len(due), "lost": len(lost)}


# --------------------------------------------------------------------------------------
# Per-event application
# --------------------------------------------------------------------------------------


def _apply_event(row: Any, parsed: ParsedEvent, *, client: Any) -> tuple[InboundVerdict, str | None]:
	"""Classify one singular event and act on the verdict. Returns ``(verdict, message or None)``."""
	event_type = singular_event_type(parsed.event_type)
	room = _room_for_space(parsed.space_name)
	stored_message, stored_is_deleted = _stored_by_resource_name(parsed.resource_name)
	# ``defer_count``, not ``attempts``. ``InboundFacts.attempt`` feeds exactly one comparison —
	# against ``echo_defer_budget`` — so it must count **defers** and nothing else. Passing
	# ``attempts`` meant a single transient Google 5xx silently shrank this event's defer budget,
	# and a couple of defers ate the failure-retry budget from the other side.
	attempt = int(getattr(row, "defer_count", None) or 0)
	defer_budget = _setting_int("echo_defer_budget", DEFAULT_DEFER_BUDGET)

	facts = InboundFacts(
		event_type=event_type,
		space_name=parsed.space_name,
		resource_name=parsed.resource_name,
		room=room,
		stored_message=stored_message,
		stored_is_deleted=stored_is_deleted,
		client_message_id=None,
		resource_fetched=False,
		local_by_client_id=None,
		in_flight_claims=_in_flight_claims(room or ""),
		attempt=attempt,
		defer_budget=defer_budget,
	)
	verdict = _classify(facts)
	resource: dict[str, Any] = {}

	if verdict is InboundVerdict.RESOLVE_VIA_GET:
		# The normal path, not a fallback — see the module docstring before deciding it looks
		# expensive.
		resource = fetch_message_resource(room or "", parsed.resource_name, client=client)
		client_id = resource_client_id(resource)
		local = _local_by_client_id(room or "", client_id)
		facts = _with(
			facts,
			client_message_id=client_id,
			resource_fetched=True,
			local_by_client_id=local,
			body_matches_stored=_body_matches(local or stored_message, resource_text(resource)),
		)
		verdict = _classify(facts)

	return (verdict, _act(row, parsed, facts, verdict, resource, client=client))


def _classify(facts: InboundFacts) -> InboundVerdict:
	"""``classify_inbound``, and nothing else. Kept as a one-liner so it is greppable.

	Every echo rule lives in ``decisions``. If a rule appears to be missing, it belongs there.
	"""
	from erpnext_enhancements.chat.sync.decisions import classify_inbound

	return classify_inbound(facts)


def _with(facts: InboundFacts, **changes: Any) -> InboundFacts:
	"""``dataclasses.replace`` for the frozen facts, spelled out so the import stays local."""
	import dataclasses

	return dataclasses.replace(facts, **changes)


def _body_matches(message: str | None, fetched_text: str) -> bool | None:
	"""Does the fetched body equal the body already stored on ``message``?

	``None`` when there is nothing to compare against — and ``None`` is meaningful:
	``classify_inbound`` tests ``is True``, so "not compared" never reads as "matches". This
	exists for exactly one case: an ERPNext edit relayed with ``messages.patch`` produces a
	``message.v1.updated`` naming a message we authored, and so does a coworker editing that
	same message in the native client. ``clientAssignedMessageId`` is identical in both — it is
	written once and never changes — so the body is what separates them. Comparing one row's
	stored text against that same row's fetched text is an exact equality on a single row, not
	a heuristic.
	"""
	if not message:
		return None
	stored = frappe.db.get_value("Chat Message", message, "text")
	if stored is None:
		return None
	return str(stored or "") == str(fetched_text or "")


def _act(
	row: Any,
	parsed: ParsedEvent,
	facts: InboundFacts,
	verdict: InboundVerdict,
	resource: Mapping[str, Any],
	*,
	client: Any,
) -> str | None:
	"""Dispatch on the verdict. Returns the ``Chat Message`` this event touched, if any."""
	event_type = singular_event_type(parsed.event_type)

	if verdict is InboundVerdict.IGNORE:
		if not facts.room:
			# Chaos case 12. Not an error: a Workspace Events subscription targets `spaces/-`,
			# so every space the subscribing coworker is in produces events, including ones we
			# deliberately do not mirror.
			_log("debug", "event for an unmirrored space", space=parsed.space_name, type=event_type)
		elif event_type == EVENT_MESSAGE_DELETED:
			# ADR §G.8 Rule 1: a delete for a message we cannot name conjures no row to bury.
			# **This used to be the end of the story and that was the silent-divergence bug** —
			# the create landed a moment later and the message stayed alive here and dead in
			# Chat forever. It is now merely redundant: whenever that create is processed, the
			# `messages.get` behind it returns Google's tombstone and `_apply_created` lands the
			# row already deleted. Logged rather than dropped because "the delete arrived first"
			# is the only warning anybody gets if that convergence ever stops working.
			_log(
				"info",
				"delete for a message we have not ingested; its create will land as a tombstone",
				room=facts.room,
				resource=parsed.resource_name,
			)
		return None

	if verdict is InboundVerdict.DUPLICATE:
		seams.bump(seams.COUNTER_DUPLICATES_REJECTED)
		_log("debug", "duplicate delivery", resource=parsed.resource_name)
		return facts.stored_message

	if verdict is InboundVerdict.TOMBSTONED:
		# Terminal. A late `created` or `updated` for a name we already buried must not
		# resurrect it: Google's tombstone is content-free, so ERPNext's row is the last copy
		# of the body and re-creating it would produce a second, empty one.
		_log("debug", "event for a tombstoned message", resource=parsed.resource_name)
		return facts.stored_message

	if verdict is InboundVerdict.ECHO:
		return _apply_echo(facts, parsed)

	if verdict is InboundVerdict.ECHO_ORPHAN:
		seams.bump(seams.COUNTER_ECHO_ORPHANS)
		_alarm(
			"ECHO_ORPHAN",
			"an inbound message carries a client- id that looks like one ERPNext minted, but no "
			"Chat Message in this room holds it. Binding it to a guess is how a real message gets "
			"eaten, so nothing was written. Reconcile the room (§4.J) or check whether another "
			"build is posting into this space.",
			room=facts.room,
			resource=parsed.resource_name,
			client_message_id=facts.client_message_id,
		)
		return None

	if verdict is InboundVerdict.DEFER:
		return None

	# NEW — apply this event's operation. The verdict is a provenance gate; the operation
	# comes from the event type.
	if event_type == EVENT_MESSAGE_CREATED:
		return _apply_created(row, facts, resource)
	if event_type == EVENT_MESSAGE_UPDATED:
		return _apply_updated(row, facts, resource)
	if event_type == EVENT_MESSAGE_DELETED:
		return _apply_deleted(facts, parsed, client=client)
	return None


# --------------------------------------------------------------------------------------
# ECHO — bind, do not insert, do not notify, do not relay
# --------------------------------------------------------------------------------------


def _apply_echo(facts: InboundFacts, parsed: ParsedEvent) -> str | None:
	"""Bind ``gchat_message_name`` onto the row we already have. Compare-and-set.

	No insert, no ``notify_new_message``, no relay job. This is our own message coming back;
	nobody is newly informed of anything, and notifying on it is how a mirror turns one message
	into a notification loop.

	The write is ``update_modified=False``. The D6 cache watermark is ``(max(seq), count(*),
	max(modified))``, so bumping ``modified`` to record a binding — which changes no content —
	would invalidate every cached digest for the room on every echo.

	**On the read-then-write here.** The general rule in this module is *never*
	``SELECT``-then-``INSERT``, because that is a TOCTOU at exactly the moment it matters. This
	is a different shape and the difference is load-bearing: the two writers who can race here
	(the relay writing back the name ``messages.create`` returned, and this path writing the
	name the event carried) are writing the **same value**, because Google issued one resource
	name for one message. A lost update is therefore a no-op. What the read is for is the case
	that is *not* convergent — a row already bound to a **different** resource name — where the
	correct action is to alarm rather than to overwrite, and no conditional ``UPDATE`` can make
	that decision for us. The insert-side guarantee is still structural: ``unique(gchat_message_
	name)`` refuses a second row for one Google message no matter who wins this line.
	"""
	message = facts.local_by_client_id
	if not message:
		return None

	current = str(frappe.db.get_value("Chat Message", message, "gchat_message_name") or "")
	if current == parsed.resource_name:
		seams.bump(seams.COUNTER_ECHOES_SUPPRESSED)
		return message

	if current:
		_alarm(
			"ECHO_REBIND_REFUSED",
			"an echo would have re-pointed a Chat Message at a different Google resource name. "
			"Refused: the first binding wins (ADR §G.8 Rule 2) and a second one means two Google "
			"messages carry one client id, which Google's own uniqueness rule forbids.",
			message=message,
			bound_to=current,
			inbound=parsed.resource_name,
		)
		return message

	bound = False
	with savepoint_catching_duplicates():
		frappe.db.set_value(
			"Chat Message", message, "gchat_message_name", parsed.resource_name, update_modified=False
		)
		bound = True
	if not bound:
		# Another row already holds this resource name — Rule 2, first writer wins. Success.
		clear_expected_duplicate_message()

	seams.bump(seams.COUNTER_ECHOES_SUPPRESSED)
	_log("debug", "echo suppressed", message=message, resource=parsed.resource_name)
	return message


# --------------------------------------------------------------------------------------
# NEW / created
# --------------------------------------------------------------------------------------


def _apply_created(row: Any, facts: InboundFacts, resource: Mapping[str, Any]) -> str | None:
	"""Insert one genuinely foreign message — **already tombstoned if Google's copy is.**

	The ordinary case is the whole of the first paragraph of this function: a live resource
	becomes a live row, and ``after_insert`` notifies exactly once.

	DELETED BEFORE CREATED — the convergence, and why the pending tombstone lives on Google
	--------------------------------------------------------------------------------------
	Pub/Sub gives no ordering guarantee without an ordering key, so a ``deleted`` event can and
	does arrive before the ``created`` it refers to. ADR §G.8 Rule 1 says a delete for a message
	we never ingested is ``Ignored``, which is right — a delete must never conjure a row to bury
	— but *"not ingested"* and *"not ingested **yet**"* are different claims, and until this
	function could tell them apart the consequence was the worst outcome this phase exists to
	prevent: the create landed a moment later, the row was **alive in ERPNext and dead in Chat,
	permanently, with nothing in any log**, and no counter moved.

	**The fix is (b) from the two options on the table — a pending tombstone keyed on the
	resource name, consumed here — and the store for it is Google's own message resource.**
	Every inbound event already costs one ``spaces.messages.get`` (``includeResource: false``
	buys the 7-day subscription TTL and nothing else can), and a message that has been deleted
	comes back from that ``get`` as a tombstone carrying ``deleteTime`` and
	``deletionMetadata``. So the pending tombstone is already in our hands at exactly the moment
	we need it, keyed by the only identity that matters, with no second row to write, no schema
	to add, no query to run and nothing to expire.

	*Why not (a), settling the unresolvable delete ``Deferred`` so the sweeper retries it.* It
	converges only if the create arrives inside the defer budget — three minutes at the current
	sweep interval. A ``deleted`` recovered by the §4.J reconciliation sweep can precede its
	``created`` by a great deal more than that, and the budget cannot be raised to cover it
	without holding every genuinely-unresolvable delete open for the same length of time. It
	also leaves the correctness of a delete depending on *when* two events happen to be
	processed, which is the property the soak proved we cannot rely on.

	*Why not a pending-tombstone row of our own, in ``Chat Inbound Event`` or elsewhere.* It
	would be a second copy of a fact Google already holds, it would need a lookup on every
	single inbound create (``gchat_resource_name`` carries no index), and it would be wrong
	whenever the delete event was never delivered at all — the reconciliation-replay case, where
	the resource is a tombstone and no delete event of ours exists. The resource answers all
	three.

	**The row lands already tombstoned, never alive-then-deleted.** A tombstone is terminal
	(``classify_inbound`` rung 4), so a row that is alive for even one committed instant is a
	row some reader can see, some digest can quote and some notification can announce. Landing
	it deleted also means ``after_insert`` fires no ``notify_new_message`` and no
	``chat_message_created`` — see :func:`erpnext_enhancements.chat.sync.outbox.finalise_new_message`,
	which is where that suppression has to live because that is where both calls are made.

	**The body is unrecoverable and is recorded as such.** Google's tombstone is metadata and
	nothing else, so a message deleted before we ingested it has no text anywhere, on either
	side, ever. :data:`TOMBSTONE_BODY_NOTICE` goes on the row instead of ``""`` because a blank
	body is indistinguishable from a blank message and from a soft delete that wrongly cleared
	the row — and distinguishing those three is the whole job of the one reader this row will
	ever have.
	"""
	room = facts.room or ""
	if not room:
		return None

	tombstone = resource_tombstone(resource)
	text = TOMBSTONE_BODY_NOTICE if tombstone else resource_text(resource)
	created_utc = parse_google_timestamp(resource.get("createTime"))
	updated_utc = parse_google_timestamp(resource.get("lastUpdateTime"))
	sender, sender_email = _resolve_sender(room, resource)

	late, skew_ms = _ordering_facts(row, room, created_utc)

	doc = frappe.new_doc("Chat Message")
	doc.room = room
	doc.sender = sender
	doc.sender_email = sender_email
	doc.sender_kind = "Human"
	doc.message_type = "Text"
	doc.text = text
	doc.text_plain = str(resource.get("argumentText") or text)
	doc.gchat_message_name = facts.resource_name
	doc.gchat_thread_name = str((resource.get("thread") or {}).get("name") or "")
	doc.gchat_create_time = utc_to_site(created_utc)
	doc.gchat_last_update_time = utc_to_site(updated_utc)
	doc.client_message_id = _inbound_client_id(facts.resource_name)
	doc.sync_state = SYNC_STATE_INBOUND
	doc.sync_origin = SYNC_ORIGIN_INBOUND
	doc.late_arrival = 1 if late else 0
	doc.clock_skew_ms = skew_ms

	if tombstone is not None:
		deleted_utc, deletion_source, _deletion_type = tombstone
		# Set on the document, not written afterwards: a second statement would leave the row
		# alive between the insert and the update, and `after_insert` — which runs between them
		# — is exactly what must not see a live row here.
		doc.is_deleted = 1
		doc.deleted_at = utc_to_site(deleted_utc)
		# The Chat `sender` on a tombstone is still the message's AUTHOR; Google does not name
		# who deleted it. `deletion_source` carries the only attribution there is, which is the
		# same compromise `_apply_deleted` makes on the ordinary path.
		doc.deleted_by = sender
		doc.deletion_source = deletion_source

	if not insert_message(doc):
		# `unique(gchat_message_name)` or `unique(room, client_message_id)` refused it, which
		# means the row already exists — precisely what this writer wanted. Rule 2: SUCCESS,
		# not an error. Never logged as one, never retried, never fails the job.
		seams.bump(seams.COUNTER_DUPLICATES_REJECTED)
		existing, _deleted = _stored_by_resource_name(facts.resource_name)
		return existing

	seams.bump(seams.COUNTER_EVENTS_INGESTED)
	if late:
		seams.bump(seams.COUNTER_LATE_ARRIVALS)

	record_revision(
		doc.name,
		room=room,
		change_type=CHANGE_CREATE,
		text_before="",
		text_after=text,
		actor=sender,
		actor_email=sender_email,
		origin_timestamp=created_utc,
		# One revision, not a Create followed by a Delete. ERPNext performed no delete — the row
		# was born in this state — and an audit trail that invents a transition it never made is
		# worse than one that is merely terse.
		note=(
			"inbound create; already deleted in Chat when we ingested it "
			f"(deletionType={tombstone[2] or 'unknown'} -> {tombstone[1]}), body unrecoverable"
			if tombstone
			else "inbound create"
		),
	)

	if tombstone is not None:
		_log(
			"info",
			"a deleted message was ingested as a tombstone; ERPNext and Chat now agree",
			room=room,
			resource=facts.resource_name,
			message=doc.name,
		)

	# Attachments, once the row exists and its revision is written. Late-bound for the same
	# reason `_outbox_attr` is: the ingest belongs to another module, and a missing one must
	# degrade to "the message is stored, the file is not" rather than to an ImportError in a
	# worker. Idempotent on `unique(gchat_attachment_name)`, so a redelivery adds no second row.
	#
	# `_apply_updated` deliberately needs no second call: it delegates an `updated` for an
	# unknown resource name straight to this function (Rule 1's allowMissing equivalent), so
	# one call site covers both doors a message can arrive through.
	ingest_attachments = _attachments_attr("ingest_message_attachments")
	if ingest_attachments is not None:
		ingest_attachments(doc.name, resource)

	_touch_room(room, created_utc)
	_check_skew_alert(room)

	# **No `notify_new_message` and no realtime publish here, deliberately.** Both fire from
	# `Chat Message.after_insert` (`outbox.finalise_new_message`) for every inserted row, which
	# is what makes "exactly once per genuinely new message" structural rather than a rule
	# somebody has to keep. Calling them again here would notify twice for every inbound
	# message — the mirror's characteristic failure, and one whose only symptom in production
	# is a notification storm with nothing wrong in any log.
	return str(doc.name)


def _ordering_facts(row: Any, room: str, created_utc: datetime | None) -> tuple[bool, int]:
	"""``(late_arrival, clock_skew_ms)`` — §4.G instrumentation.

	**Late** means the message was *created* in Chat before the newest message already in the
	room, i.e. it arrived out of order. It is recorded and the message still appends; see
	:func:`allocate_seq` for why nothing is renumbered.

	**Skew** is ``received_at − createTime`` in milliseconds. That is delivery lag *and* clock
	skew together, and the name is a slight overclaim — the two cannot be separated from this
	side. It is the right measurement anyway, because the alarm is on the **median over a
	window** (:func:`_check_skew_alert`): a single delayed redelivery is normal and
	self-correcting, while a sustained median means the two clocks genuinely disagree and every
	Rule 3 decision in the system is being made on bad data.
	"""
	if created_utc is None:
		return (False, 0)

	tail = site_to_utc(frappe.db.get_value("Chat Room", room, "last_message_at"))
	late = bool(tail and created_utc < tail)

	received = site_to_utc(getattr(row, "received_at", None)) or _now_utc()
	skew_ms = int((received - created_utc).total_seconds() * 1000)
	return (late, skew_ms)


def _check_skew_alert(room: str) -> None:
	"""Alarm when the **median** ``clock_skew_ms`` over a window exceeds the configured ceiling.

	Reads one integer column off recent rows — no message content — so this is an operational
	query, not a chat read, and it needs no membership filter.
	"""
	threshold = _setting_int("clock_skew_alert_ms", 300_000)
	if threshold <= 0:
		return

	rows = frappe.get_all(
		"Chat Message",
		filters={"room": room, "sync_origin": SYNC_ORIGIN_INBOUND},
		fields=["clock_skew_ms"],
		order_by="seq desc",
		limit=SKEW_SAMPLE_SIZE,
	)
	samples = [int(r.get("clock_skew_ms") or 0) for r in rows]
	if len(samples) < 3:
		# Too few to have a median worth acting on; two samples would alarm on one bad delivery.
		return

	median = statistics.median(samples)
	if abs(median) <= threshold:
		return

	seams.bump(seams.COUNTER_SKEW_ALERTS)
	_alarm(
		"CLOCK_SKEW",
		"the median gap between Google's createTime and our receipt exceeds the configured "
		"ceiling. Every ADR §G.8 Rule 3 decision compares those two clocks, so a sustained skew "
		"silently decides every edit conflict in one direction.",
		room=room,
		median_ms=int(median),
		threshold_ms=threshold,
		samples=len(samples),
	)


def _touch_room(room: str, created_utc: datetime | None) -> None:
	"""Advance ``Chat Room.last_event_at`` — the §4.J reconciliation watermark.

	Deliberately does **not** write ``last_message``/``last_message_at``/``last_message_preview``:
	those are the write path's denormalisation, owned by ``Chat Message``'s own document events,
	and two writers on one denormalised field is how it goes stale in a way nobody can trace.
	"""
	try:
		frappe.db.set_value(
			"Chat Room",
			room,
			"last_event_at",
			utc_to_site(created_utc) or None,
			update_modified=False,
		)
	except Exception:
		return


# --------------------------------------------------------------------------------------
# NEW / updated — ADR §G.8 Rule 3
# --------------------------------------------------------------------------------------


def _apply_updated(row: Any, facts: InboundFacts, resource: Mapping[str, Any]) -> str | None:
	"""Apply, or refuse, one inbound edit. **LAST-WRITER-WINS BY ``lastUpdateTime``, ERPNEXT BREAKS TIES.**

	Rule 1 first: an ``updated`` naming a message we have never stored is applied as a
	**create** from the fetched resource. Pub/Sub does not guarantee ordering without an
	ordering key, so an edit genuinely can be delivered before the create it edits (chaos case
	2), and dropping it would lose the message entirely rather than merely lose the edit.

	Then Rule 3, on the two timestamps, **converted explicitly**. Google returns RFC-3339 UTC;
	``edited_at`` is Frappe-written site-local. A naive comparison is wrong by the site offset
	*in the direction that always makes Google win*, which silently inverts decision #1 — so
	both sides are normalised to aware UTC and a naive value would raise rather than mislead.

	* Google's ``lastUpdateTime`` **strictly newer** than ERPNext's watermark ⇒ the inbound edit
	  is applied.
	* Equal, or **either missing** ⇒ **ERPNext wins**, and this is the half people leave out:
	  discarding the inbound edit is only half a decision. The other half is to **re-queue an
	  outbound ``Message Update``**, so that Chat converges back onto ERPNext's text. Without
	  it, the two sides stay permanently divergent with nothing anywhere to notice — the message
	  reads one way in ERPNext and another way in Chat, forever, and no error was ever logged.

	``VERIFY:`` **the tie-break baseline.** ``PHASE2_CONTRACT.md`` §4 Rule 3 says "either
	missing ⇒ ERPNext wins", and :func:`_erpnext_edit_watermark` implements exactly that by
	returning ``edited_at`` and nothing else. The consequence is worth putting in front of the
	human at the checkpoint rather than burying: ``edited_at`` is ``NULL`` until ERPNext itself
	edits a message, so under the literal rule the **first** Chat-side edit of any message is
	always discarded and reverted.

	**SETTLED 2026-08-09, and the resolution is that Rule 3 never applied to that case.** The
	rule's own statement opens *"When both sides have edited the same message"*, so a
	Chat-origin message ERPNext has never touched is outside its scope: there is no competing
	ERPNext version to protect and therefore no tie to break. Declining to decide read as
	caution and behaved as data loss — the edit was discarded *and* an outbound revert was
	queued, overwriting the coworker's own edit in Chat with our stale copy.

	So the ladder is three rungs, not one: the §4.C monotonic guard (a redelivery of an edit
	already stored is a no-op), then "is this even a conflict?", then Rule 3 proper. ERPNext
	still wins every genuine tie and every missing timestamp.
	"""
	room = facts.room or ""
	message = facts.stored_message or facts.local_by_client_id
	if not message:
		# Rule 1 — CREATE-BEFORE-EDIT. Not an error; not a defer either, because the fetched
		# resource is a complete Message and there is nothing left to wait for.
		_log("info", "updated before created; applying as a create", resource=facts.resource_name)
		return _apply_created(row, facts, resource)

	inbound_at = parse_google_timestamp(resource.get("lastUpdateTime"))
	erp_at = _erpnext_edit_watermark(message)

	# Rung 1 — the monotonic guard (§4.C), before any conflict reasoning. A redelivery of an
	# edit already stored is a no-op, not a conflict, and Pub/Sub guarantees we will see them.
	if _inbound_edit_is_stale(message, inbound_at):
		_log("info", "stale inbound edit ignored", resource=facts.resource_name)
		seams.bump(seams.COUNTER_DUPLICATES_REJECTED)
		return message

	# Rung 2 — is this even a conflict? ADR §G.8 Rule 3 opens "When BOTH sides have edited the
	# same message", and `erp_at is None` means ERPNext never has. There is nothing to
	# arbitrate, so Rule 3 does not apply and the edit is simply applied.
	#
	# This is not a softening of "ERPNext wins ties" — it is that clause's precondition. The
	# earlier reading, `inbound_at and erp_at and inbound_at > erp_at`, made `erp_at` NULL
	# unwinnable: `edited_at` is written ONLY when an inbound edit is applied, which required
	# `erp_at` to already be non-NULL. So for every Chat-origin message the answer was False
	# forever, and every edit a coworker made to their own message in the native client was
	# discarded AND reverted — overwriting their edit in Chat with our stale copy, which is
	# the loudest possible way to lose data while looking like policy.
	if erp_at is None:
		inbound_wins = True
	else:
		# Rung 3 — Rule 3 proper. Later origin timestamp wins; equal or missing, ERPNext wins,
		# because decision #1 makes it the source of truth and a tie-break favouring the
		# mirror would invert that.
		inbound_wins = bool(inbound_at and inbound_at > erp_at)

	stored_text = str(frappe.db.get_value("Chat Message", message, "text") or "")
	new_text = resource_text(resource)

	if not inbound_wins:
		return _refuse_inbound_edit(
			message,
			room=room,
			stored_text=stored_text,
			inbound_text=new_text,
			inbound_at=inbound_at,
			erp_at=erp_at,
		)

	if stored_text == new_text:
		# The edit is already applied — a redelivery. Recording a revision here would add an
		# audit row describing a change that did not happen.
		frappe.db.set_value(
			"Chat Message",
			message,
			"gchat_last_update_time",
			utc_to_site(inbound_at),
			update_modified=False,
		)
		return message

	seq = int(frappe.db.get_value("Chat Message", message, "seq") or 0)
	sender, sender_email = _resolve_sender(room, resource)

	frappe.db.set_value(
		"Chat Message",
		message,
		{
			"text": new_text,
			"text_plain": str(resource.get("argumentText") or new_text),
			"is_edited": 1,
			"edited_at": utc_to_site(inbound_at),
			"gchat_last_update_time": utc_to_site(inbound_at),
		},
	)

	record_revision(
		message,
		room=room,
		change_type=CHANGE_EDIT,
		text_before=stored_text,
		text_after=new_text,
		actor=sender,
		actor_email=sender_email,
		origin_timestamp=inbound_at,
		note="inbound edit applied (Rule 3: Google newer)",
	)

	# Every edit, with the span that changed. Without this, Phase 5 keeps serving a digest
	# built from the superseded text.
	seams.mark_room_context_stale(room, seq, seq)
	_publish(room, "edited", message, seq)
	# No notify: nobody is newly informed of a message they already have.
	return message


def _erpnext_edit_watermark(message: str) -> datetime | None:
	"""ERPNext's side of the Rule 3 comparison, as aware UTC — or ``None`` if it never edited.

	``edited_at`` and nothing else, and **``None`` is a meaningful answer rather than a
	missing one**: it says *ERPNext has never edited this message*, which takes the event out
	of Rule 3's scope altogether. See :func:`_apply_updated` for what the caller does with it.

	Isolated in its own function so the baseline is one line in one place rather than an edit
	to the conflict logic.
	"""
	return site_to_utc(frappe.db.get_value("Chat Message", message, "edited_at"))


def _inbound_edit_is_stale(message: str, inbound_at: datetime | None) -> bool:
	"""Has an edit at or before this ``lastUpdateTime`` already been applied?

	The monotonic guard from §4.C's idempotency table — *"an update whose ``lastUpdateTime``
	is ≤ the stored value is ignored"*. It runs **before** Rule 3 and is a different question:
	Rule 3 arbitrates between two live edits, this one discards a redelivery of an edit we
	have already stored. Pub/Sub is at-least-once, so this fires in normal operation.

	Unknown ``inbound_at`` is **not** stale. A missing timestamp is Rule 3's problem (ERPNext
	wins absences), and answering "stale" here would silently drop it one rung earlier.
	"""
	if inbound_at is None:
		return False
	stored = site_to_utc(frappe.db.get_value("Chat Message", message, "gchat_last_update_time"))
	return bool(stored and inbound_at <= stored)


def _refuse_inbound_edit(
	message: str,
	*,
	room: str,
	stored_text: str,
	inbound_text: str,
	inbound_at: datetime | None,
	erp_at: datetime | None,
) -> str:
	"""ERPNext won the tie. Record **why**, then push ERPNext's text back to Chat.

	The revision row with ``text_before == text_after`` looks redundant and is not: it is the
	only record that a conflict occurred and which way it was resolved, and
	``Chat Message Revision.note``'s own field description names "why an inbound edit was
	discarded" as one of the things it is for. Without it, the discarded edit leaves no trace
	anywhere — the row is unchanged, so even ``Version`` history (which this DocType does not
	keep) would show nothing.

	The revert is **capped**. ``Chat Settings.max_reverts_per_hour`` against
	``Chat Room.reverts_this_hour`` exists so two systems cannot fight forever: if ERPNext
	re-pushes its text and a coworker edits it back on every round, the loop is bounded and
	alarmed rather than run at one write per second per space until somebody notices.
	"""
	record_revision(
		message,
		room=room,
		change_type=CHANGE_EDIT,
		text_before=stored_text,
		text_after=stored_text,
		origin_timestamp=inbound_at,
		note=(
			"inbound edit DISCARDED (Rule 3: ERPNext wins on equal-or-missing). "
			f"google_last_update={inbound_at.isoformat() if inbound_at else 'missing'} "
			f"erpnext_edited_at={erp_at.isoformat() if erp_at else 'missing'} "
			f"discarded_bytes={len(inbound_text.encode('utf-8'))}"
		),
	)

	if not _charge_revert(room):
		_alarm(
			"REVERT_CAP",
			"an inbound edit was discarded but the outbound revert was suppressed: this room has "
			"hit Chat Settings.max_reverts_per_hour. ERPNext and Chat now disagree on this "
			"message's text until somebody intervenes — which is the point of the cap, because "
			"the alternative is the two systems overwriting each other indefinitely.",
			room=room,
			message=message,
		)
		return message

	if _requeue_outbound_update(message):
		seams.bump(seams.COUNTER_REVERTS_APPLIED)
	return message


def _charge_revert(room: str) -> bool:
	"""Consume one revert from the room's hourly budget. ``False`` ⇒ over cap.

	A rolling one-hour window kept on the room itself (``reverts_this_hour``,
	``revert_window_start``) rather than in Redis, because a deploy ``FLUSHDB``s the cache and a
	fight-suppression counter that resets on every release does not suppress anything.
	"""
	cap = _setting_int("max_reverts_per_hour", 5)
	if cap <= 0:
		return True

	from frappe.utils import add_to_date, get_datetime, now_datetime

	now = now_datetime()
	values = (
		frappe.db.get_value("Chat Room", room, ["reverts_this_hour", "revert_window_start"], as_dict=True)
		or {}
	)
	count = int(values.get("reverts_this_hour") or 0)

	# `get_datetime` rather than trusting the column's type: `frappe.db.get_value` hands back a
	# `datetime` or a string depending on the driver and the column, and comparing a string to
	# a datetime is a TypeError on the one path that exists to stop two systems fighting.
	started: Any = values.get("revert_window_start") or None
	if started is not None:
		try:
			started = get_datetime(started)
		except Exception:
			started = None

	if started is None or started < add_to_date(now, hours=-1):
		started, count = now, 0

	if count >= cap:
		return False

	frappe.db.set_value(
		"Chat Room",
		room,
		{"reverts_this_hour": count + 1, "revert_window_start": started},
		update_modified=False,
	)
	return True


def _requeue_outbound_update(message: str) -> bool:
	"""Queue an outbound ``Message Update`` so Chat converges back onto ERPNext's text.

	**Interface owned by another module.** ``sync.outbox`` is the only thing allowed to allocate
	``Chat Relay Job.job_seq`` — ``unique(room, job_seq)`` is what makes ADR §G.8 Rule 1 a
	property of the schema rather than a hope — so this reaches for its enqueue rather than
	writing a relay row by hand. Required signature, reported at the checkpoint::

	    outbox.enqueue_message_update(message: str, *, reason: str = "") -> str | None

	If it is absent the divergence is **alarmed rather than swallowed**: the whole reason Rule
	3's else-branch exists is that a discarded inbound edit with no re-queue leaves the two
	sides permanently different with nothing to notice, and silently failing to re-queue would
	reproduce exactly that.
	"""
	create_job = _outbox_attr("create_relay_job")
	if create_job is None:
		_alarm(
			"REVERT_NOT_QUEUED",
			"an inbound edit was discarded under Rule 3 but no outbound re-queue is available "
			"(sync.outbox.create_relay_job is missing), so Chat still shows the edited text and "
			"ERPNext shows the original. Nothing else will notice this.",
			message=message,
		)
		return False

	row = frappe.db.get_value("Chat Message", message, ["room", "sender", "sync_origin"], as_dict=True) or {}
	try:
		create_job(
			room=row.get("room") or "",
			operation=OPERATION_MESSAGE_UPDATE,
			reference_doctype="Chat Message",
			reference_name=message,
			# The **row's own** origin, not this event's. ``outbox.create_relay_job`` refuses
			# ``origin="Google Chat"`` outright as the echo loop, and it is right to for a
			# create. For a Rule 3 revert of a row that itself came from Chat it is a genuine
			# conflict of two correct rules, and it fails closed: the revert is refused, the
			# alarm below fires, and the two sides stay visibly divergent rather than being
			# silently reconciled by a job the echo guard exists to prevent. Raised at the
			# checkpoint — the fix belongs in outbox, gating on "was this job caused by an
			# inbound event" rather than on the referenced row's origin.
			origin=row.get("sync_origin") or "ERPNext",
			# The identity that created a message is the only identity allowed to patch it:
			# under app auth you may patch only what the app created, so an edit to a human's
			# message is a DWD impersonation of that human. Pinned on the row so a retry cannot
			# drift to a different principal and 403 in a way that reads like a scope problem.
			impersonate_user=row.get("sender") or "",
			payload={"kind": "message.update", "message": message, "reason": "rule-3-revert"},
		)
		return True
	except Exception as exc:
		_alarm(
			"REVERT_NOT_QUEUED",
			f"the outbound re-queue for a discarded inbound edit failed: {type(exc).__name__}",
			message=message,
		)
		return False


# --------------------------------------------------------------------------------------
# NEW / deleted — the tombstone
# --------------------------------------------------------------------------------------


def _apply_deleted(facts: InboundFacts, parsed: ParsedEvent, *, client: Any) -> str | None:
	"""Soft-delete the stored row. **The body stays on the row.**

	ADR §F.6.5 / §G.7.4, and a deliberate divergence from the Phase 2 brief §4.F, which says to
	move the body to the audit table and clear the live row. Google's tombstone is rich in
	metadata and **empty of content** — ``showDeleted=true`` returns the delete time and
	``deletionMetadata``, never the text — so if ERPNext does not keep the body, **nobody has
	it**. Every read path filters ``is_deleted = 0``; only the Phase 6 oversight endpoint sees
	through, and it pays with an audit row.

	The metadata comes from a best-effort ``messages.get``: the event carries a resource name
	and nothing else, and ``deletionMetadata.deletionType`` is the difference between "a
	coworker deleted their own message", "an admin did", "retention did" and "this is our own
	relayed delete coming back". ``VERIFY:`` whether the real API returns a tombstone or a 404
	for ``get`` on a deleted message is inferred from ``list(showDeleted=true)`` and stated
	nowhere; the fetch is therefore allowed to fail, and the delete is still applied with
	``deleted_at = now`` and the default source.

	**This function is not where a delete that overtook its create is handled.** Such an event
	never reaches here at all: ``classify_inbound`` answers ``IGNORE`` for a ``deleted`` naming
	a message we cannot name, and the convergence happens later, in :func:`_apply_created`, off
	the tombstone Google returns for the resource. The ``if not message`` guard below is
	therefore a caller-contract assertion rather than a path anything takes.
	"""
	message = facts.stored_message or facts.local_by_client_id
	room = facts.room or ""
	if not message:
		return None

	if frappe.db.get_value("Chat Message", message, "is_deleted"):
		# Already buried, most likely our own relayed delete echoing back. Idempotent.
		return message

	resource: Mapping[str, Any] = {}
	try:
		resource = fetch_message_resource(room, parsed.resource_name, client=client)
	except Exception:
		_log("info", "tombstone metadata unavailable; applying the delete anyway", message=message)

	deletion_type = str((resource.get("deletionMetadata") or {}).get("deletionType") or "")
	source = DELETION_TYPE_TO_SOURCE.get(deletion_type, DEFAULT_DELETION_SOURCE)
	deleted_utc = parse_google_timestamp(resource.get("deleteTime")) or _now_utc()
	actor, actor_email = _resolve_sender(room, resource) if resource else (None, "")

	stored_text = str(frappe.db.get_value("Chat Message", message, "text") or "")
	seq = int(frappe.db.get_value("Chat Message", message, "seq") or 0)

	frappe.db.set_value(
		"Chat Message",
		message,
		{
			"is_deleted": 1,
			"deleted_at": utc_to_site(deleted_utc),
			"deleted_by": actor,
			"deletion_source": source,
			# `text` is deliberately NOT cleared. See the docstring.
		},
	)

	record_revision(
		message,
		room=room,
		change_type=CHANGE_DELETE,
		text_before=stored_text,
		text_after="",
		actor=actor,
		actor_email=actor_email,
		origin_timestamp=deleted_utc,
		note=f"inbound delete; deletionType={deletion_type or 'unknown'} -> {source}",
	)

	seams.mark_room_context_stale(room, seq, seq)
	_publish(room, "deleted", message, seq)
	return message


# --------------------------------------------------------------------------------------
# Realtime, row settlement, deferral
# --------------------------------------------------------------------------------------


def _publish(room: str, kind: str, message: str, seq: int) -> None:
	"""Fan an **edit or a delete** out to the room's document room, through the one helper.

	``created`` is deliberately absent: ``outbox.finalise_new_message`` publishes it from
	``after_insert`` for every inserted row, and a second publish here would be a duplicate the
	SPA has to de-duplicate at the other end.

	Identifiers only — the client re-reads the row, and every byte here is copied to every
	socket in the room through Redis pub/sub. Never raises: a realtime failure must not undo a
	change that is already committed.
	"""
	from erpnext_enhancements.chat import realtime

	events = {
		"edited": realtime.EVENT_MESSAGE_EDITED,
		"deleted": realtime.EVENT_MESSAGE_DELETED,
	}
	try:
		realtime.publish_room_event(
			room, events[kind], {"message": message, "room": room, "seq": seq, "origin": SYNC_ORIGIN_INBOUND}
		)
	except Exception as exc:
		_log("warning", "realtime publish failed", room=room, error=type(exc).__name__)


def _settle(row: Any, status: str, *, last_error: str = "") -> str:
	"""Write the terminal status onto the raw row."""
	values: dict[str, Any] = {"status": status}
	if last_error:
		# Truncated: `last_error` is a Small Text and a Google traceback is not a useful column.
		values["last_error"] = last_error[:500]
	try:
		frappe.db.set_value("Chat Inbound Event", row.name, values, update_modified=False)
	except Exception:
		pass
	return status


def _settle_from_verdicts(row: Any, verdicts: list[str]) -> str:
	"""One delivery can fan out to N resources; one row carries one status.

	``Ignored`` only when **every** contained resource was ignored — a batch with one real
	message in it is a processed delivery, and marking it ``Ignored`` would exempt it from the
	one thing an operator greps for.
	"""
	if not verdicts:
		return _settle(row, STATUS_IGNORED)
	if InboundVerdict.ECHO_ORPHAN.value in verdicts:
		return _settle(row, STATUS_FAILED, last_error="ECHO_ORPHAN: see the Error Log alarm")
	if all(v == InboundVerdict.IGNORE.value for v in verdicts):
		return _settle(row, STATUS_IGNORED)
	return _settle(row, STATUS_PROCESSED)


def _defer(row: Any) -> None:
	"""Spend one unit of the defer budget and set the row's ``available_at``. Does **not** enqueue.

	**``defer_count``, not ``attempts``.** The two bound unrelated things and sharing one column
	made them eat each other: ``classify_inbound`` compares the defer spend against
	``Chat Settings.echo_defer_budget`` while :func:`_fail_or_retry` compares ``attempts``
	against ``relay_max_attempts``, so on one counter three defers left only two real retries
	before the row was marked ``Failed`` and exempted from the sweeper — and, in the direction
	that loses data, one transient ``messages.get`` timeout permanently shrank that event's
	defer budget. Two ceilings, two columns.

	**The delay is stored, not scheduled.** ``frappe.enqueue`` exposes no route to RQ's
	scheduler, and RQ's scheduling lives in the queue Redis the production deploy ``FLUSHDB``s,
	so the only durable timer available is a column. The row is deliberately **not** re-enqueued
	here: an immediate re-enqueue is the bug this replaces, and it spent the whole budget in
	milliseconds while waiting out nothing at all.

	**The consequence, stated because it is a real cost:** the sweeper's cron interval is now
	the defer granularity. At a ten-minute sweep a three-defer budget is a thirty-minute worst
	case for one message — absurd for a wait whose entire purpose is to outlast an outbound
	write that completes in about a second — so :func:`sweep_stuck_inbound_events` is scheduled
	**every minute** (``hooks.py`` ``scheduler_events["cron"]["* * * * *"]``), making the worst
	case three minutes. The one-minute cron costs nothing extra on the lost-job half of that
	sweep: it still only looks at rows older than :data:`STUCK_EVENT_MINUTES`, so it selects the
	same rows it would have at ten-minute granularity, merely sooner.

	A defer is a delay, never a suppression. Once the budget is spent the event is classified on
	its merits and **never** as an echo: suppressing a coworker's message because something
	unrelated was being written to that room is the worst bug this pipeline could have.
	"""
	from frappe.utils import add_to_date, now_datetime

	deferred = int(getattr(row, "defer_count", None) or 0) + 1
	delay = min(DEFER_DELAY_SECONDS * deferred, DEFER_DELAY_CAP_SECONDS)
	try:
		frappe.db.set_value(
			"Chat Inbound Event",
			row.name,
			{"defer_count": deferred, "available_at": add_to_date(now_datetime(), seconds=delay)},
			update_modified=False,
		)
	except Exception:
		pass
	_log("debug", "deferred", event=row.name, defer_count=deferred, delay=delay)


def _route_lifecycle(row: Any, parsed: ParsedEvent) -> str:
	"""Subscription lifecycle events share the topic and belong to another module.

	``expirationReminder`` (T−12h and T−1h), ``suspended`` and ``expired`` arrive on the **same**
	Pub/Sub topic as the resource events, so this pipeline meets them and must hand them on
	rather than try to parse a resource name they do not have. If the subscription module is
	not present the row is marked ``Ignored`` and alarmed — an unrenewed subscription is
	**permanently deleted** by Google and cannot be reactivated, so this is not a silent skip.
	"""
	handler = None
	try:
		from erpnext_enhancements.chat.sync import subscriptions

		handler = getattr(subscriptions, "handle_lifecycle_event", None)
	except Exception:
		handler = None

	if callable(handler):
		try:
			handler(parsed)
			return _settle(row, STATUS_PROCESSED)
		except Exception as exc:
			return _settle(row, STATUS_FAILED, last_error=f"lifecycle handler failed: {type(exc).__name__}")

	_alarm(
		"LIFECYCLE_UNHANDLED",
		"a Workspace Events subscription lifecycle event arrived and no handler is registered. "
		"An expired subscription is permanently deleted by Google and cannot be reactivated, so "
		"inbound sync for that coworker stops silently.",
		subscription=parsed.subscription_name,
		type=parsed.event_type,
	)
	return _settle(row, STATUS_IGNORED)
