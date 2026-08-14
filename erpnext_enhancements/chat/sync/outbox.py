# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The write path — invariant I1. Everything a ``Chat Message`` insert must do, and nothing
it is allowed to do.

``Chat Message``'s controller is deliberately thin: it delegates here so that the three
writers (the ERPNext composer, the inbound ingest, the reconciliation backfill) share one
implementation rather than three that agree on a good day. The rule this module exists to
enforce is one sentence long:

    **No document event on any chat DocType may make an HTTP call to Google.**

A Chat API call inside a hook runs inside the inserting transaction, on a web worker, so a
Google timeout becomes a *failed message insert*. ERPNext is the system of record and Chat
is the mirror; a mirror that can refuse a write is not a mirror. The insert therefore writes
a **``Chat Relay Job`` row in the same transaction** and stops. That row is the only thing
the write path knows about Google, and ``tests/test_chat_outbox.py`` enforces the rule with
an AST check over this module and the three controllers rather than a comment.

--------------------------------------------------------------------------------------
``seq`` allocation: which half is the guarantee and which half is the optimisation
--------------------------------------------------------------------------------------

Two mechanisms, and confusing them is how this gets rewritten badly later:

* **``unique(room, seq)`` is the GUARANTEE.** It is a database constraint. Nothing — not a
  second worker, not a replayed job, not a backfill script somebody runs by hand — can put
  two messages at the same position in a room while it exists. Correctness rests on it
  alone.
* **``UPDATE tabChat Room SET seq_high_water = seq_high_water + 1`` is the OPTIMISATION.**
  The ``UPDATE`` takes an exclusive row lock on that one ``Chat Room`` row, held until the
  inserting transaction commits, so a concurrent insert into the *same* room blocks there
  and then reads the already-incremented value. That is what makes the read-back safe, and
  it means the guarantee is essentially never *tested* — collisions become rare enough that
  the retry path is a safety net rather than a hot path.

So a ``(room, seq)`` collision is a **retry, not a crash**: :func:`insert_message` rolls
back to a savepoint, clears the derived identity and allocates again. It is not an error
worth an Error Log row, because the only outcomes are "we got the next number" and "we got
the one after that".

**Never ``SELECT max(seq)``.** A bare ``MAX(seq)`` read takes no lock at all and two
concurrent inserts read the same maximum; ``MAX(seq) … FOR UPDATE`` does take one, but on a
range of ``tabChat Message`` rather than on a single row, which means gap locks across the
busiest table in the feature and a deadlock surface that grows with the size of the room.
The counter column is one row, one lock, constant cost.

    Note for the reader who checks the schema: ``Chat Room.seq_high_water``'s field
    description used to say *"ADVISORY MIRROR ONLY … Never read it to allocate"*, written
    for the ``MAX(seq) FOR UPDATE`` design this module replaced. It now says what the code
    does. Nothing else reads the column, so the two could only ever have disagreed in
    prose — which is exactly how a later reader talks themselves back into ``MAX(seq)``.

--------------------------------------------------------------------------------------
``job_seq`` rides the same lock, on purpose
--------------------------------------------------------------------------------------

``Chat Relay Job`` has ``unique(room, job_seq)`` and no counter column of its own, and
Rule 1 (CREATE-BEFORE-EDIT, ADR §G.8) depends on that number being a strict per-room FIFO
position. :func:`allocate_job_seq` therefore takes the **``Chat Room`` row lock first** and
only then reads ``MAX(job_seq)`` for that room. Any other transaction allocating a
``job_seq`` for the same room is blocked on the same row, so the read cannot be stale.

This is why every job creator must go through :func:`create_relay_job` rather than
inserting a ``Chat Relay Job`` directly: the lock is not on the table it reads, so it is
invisible to anyone who has not read this paragraph.

--------------------------------------------------------------------------------------
``enqueue_after_commit=True``, and why the enqueue is not the delivery guarantee
--------------------------------------------------------------------------------------

Without ``enqueue_after_commit`` the worker can ``get_doc`` the job before the inserting
transaction commits and find nothing — an intermittent "message not found" that reproduces
only under load, which is the worst kind. The realtime publish is ``after_commit=True`` for
exactly the same reason: a client that reacts to the event before the row lands re-reads a
row that is not there yet.

And the enqueue is **only** a latency optimisation. The production deploy runs
``redis-cli -p 11000 FLUSHDB`` and restarts the whole process group, Frappe v16 wires no RQ
retries at all, so a queued job does not survive a release. The ``Chat Relay Job`` row does,
and the sweeper over those rows is the sole delivery guarantee. ``deduplicate=True``
*requires* ``job_id`` (it throws without one) and skips a new enqueue while an existing job
is ``QUEUED`` **or ``STARTED``** — which is safe here precisely because a dropped enqueue
costs one sweep interval and nothing else.

--------------------------------------------------------------------------------------
Relay refusal
--------------------------------------------------------------------------------------

:func:`create_relay_job` refuses, unconditionally, to create an outbound job for a row whose
``sync_origin`` is ``"Google Chat"``. It is the cheapest guard in the system: relaying an
inbound message back to Chat is the characteristic failure of a bidirectional mirror, it
compounds (the echo of the echo is a third message), and at one write per second per space
the room fills with duplicates faster than anyone can disable the feature.

--------------------------------------------------------------------------------------
Edit and delete (§4.F): ``on_update``, and deliberately **not** ``on_trash``
--------------------------------------------------------------------------------------

:func:`propagate_message_change` is the ``on_update`` body and it is mostly a set of
refusals, because the cheap implementation of this is wrong in three separate ways:

* **Frappe runs ``on_update`` on the insert too.** ``Document.insert`` calls
  ``run_post_save_methods()`` immediately after ``after_insert``, with ``flags.in_insert``
  still set, and that runs ``on_update``. An edit relay that did not notice would send every
  message twice.
* **``has_value_changed`` cannot tell an edit from an insert.** Frappe's implementation is
  ``previous.get(f) != self.get(f) if previous else True`` — with no before-image it returns
  ``True`` for *every* field. It is the right tool once :func:`_before_image` has already
  established that there is a before-image, and a trap before that.
* **Most saves change no text.** The relay's own completion writes ``sync_state``; a job per
  save would have the relay queue its own successor, forever, out of a budget of one write
  per second per space.

The delete is driven off ``is_deleted`` flipping 0 → 1 and **never** off ``on_trash``, which
:func:`refuse_hard_delete` refuses outright. ADR §F.6.5: Google's tombstone is content-free —
``showDeleted=true`` returns the delete time and the metadata and never the body — so ERPNext
is the last copy of what was said and a hard delete destroys it. It also could not work if we
wanted it to: a relay job addresses its message by ``reference_name`` and the worker reads
``client_message_id`` off that row, both of which a hard delete has already removed by the
time any worker runs.

Neither path is gated on whether the create was ever relayed. ``messages.patch`` is sent with
``allowMissing=true`` against the ``client-`` alias, so an edit to a message Google never
received upserts it rather than 404ing — the documented safety net under Rule 1, and the
reason this module does not have to reason about ``sync_state`` at all.

--------------------------------------------------------------------------------------
Oversized messages (CQ-11)
--------------------------------------------------------------------------------------

``Chat Settings.oversized_message_policy`` decides, and the arithmetic is
:mod:`erpnext_enhancements.chat.sync.budget` — 32,000 **bytes** over the whole encoded
resource, not ``len(text)``.

* ``Truncate With Link`` (default) relays a cut body plus a deep link back to the ERPNext
  row and sets ``Chat Message.truncated_for_relay``. Nothing is lost: the full body stays
  on the row, which is the source of truth.
* ``Reject`` refuses the message at compose time with a validation error.

Both are gated on *"would this message actually be relayed"*. Rejecting a message in a room
that is not mirrored at all would let Google's transport limit dictate what an employee may
say inside ERPNext with no mirror on the other end to justify it, which inverts decision #1.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

import html
import random
import re
import string
from typing import Any, Final

import frappe
from frappe.utils import cint, escape_html, now_datetime

from erpnext_enhancements.chat import links, realtime, seams
from erpnext_enhancements.chat.gchat import ids
from erpnext_enhancements.chat.sync import budget, states

# --- names, so no call site spells one -----------------------------------------

MESSAGE_DOCTYPE: Final[str] = "Chat Message"
ROOM_DOCTYPE: Final[str] = "Chat Room"
MEMBER_DOCTYPE: Final[str] = "Chat Room Member"
RELAY_JOB_DOCTYPE: Final[str] = "Chat Relay Job"

ORIGIN_ERPNEXT: Final[str] = "ERPNext"
ORIGIN_GOOGLE_CHAT: Final[str] = "Google Chat"
ORIGIN_TRITON: Final[str] = "Triton"

#: ``Chat Relay Job.operation``. Spelled here rather than imported from
#: ``chat_relay_job.py`` for the same reason :data:`RELAY_WORKER_PATH` is a string: this
#: module is loaded by a document event and must not pull the relay's import graph in behind
#: it. The DocType's ``Select`` options are the source of truth for the vocabulary, and
#: ``tests/test_chat_sync_states.py`` pins the pure twin in ``states.py`` against that JSON.
OPERATION_MESSAGE_CREATE: Final[str] = "Message Create"
OPERATION_MESSAGE_UPDATE: Final[str] = "Message Update"
OPERATION_MESSAGE_DELETE: Final[str] = "Message Delete"

#: The relay worker's dotted path. A string rather than an import: importing
#: :mod:`erpnext_enhancements.chat.sync.outbound` here would pull the Google transport into
#: the module that document events load, which is exactly what the AST guard forbids.
#: ``outbound.run_relay_job(job: str)`` is the agreed entry point — and ``job``, not
#: ``job_name``, because ``frappe.enqueue`` takes a ``job_name`` of its own and would swallow
#: it. This comment was right and the worker had drifted; v1.277.13 read it the other way
#: round and changed the caller. The test now checks both sides rather than either comment.
RELAY_WORKER_PATH: Final[str] = "erpnext_enhancements.chat.sync.outbound.run_relay_job"

#: ``long``, not ``default``: a relay attempt is an HTTP call to Google with a 30-second
#: timeout plus jittered in-call backoff, and parking that on the queue the rest of the site
#: uses for ordinary saves is how a Google slowdown becomes a site-wide one.
RELAY_QUEUE: Final[str] = "long"
RELAY_JOB_TIMEOUT: Final[int] = 300

#: The non-text part of a ``messages.create`` payload, in bytes, as a deliberate
#: over-estimate: field names, JSON quoting, the space name, ``clientAssignedMessageId``,
#: ``requestId`` and the thread reference. :mod:`budget` refuses to guess this and is right
#: to — a guess that is too small is a 400 from Google, and a guess that is too large only
#: truncates a message that would just have fitted. Over-estimating is the safe direction.
ENVELOPE_ESTIMATE_BYTES: Final[int] = 1024

#: ``Chat Room.last_message_preview`` is documented as plain text truncated to 200
#: characters and escaped by us.
PREVIEW_MAX_CHARS: Final[int] = 200

#: How many times :func:`insert_message` re-allocates ``seq`` after a ``(room, seq)``
#: collision before giving up. Five, because under the row lock a collision means somebody
#: is writing ``seq`` outside this module — a backfill, a restored dump — and the fifth
#: failure is information, not bad luck.
SEQ_COLLISION_ATTEMPTS: Final[int] = 5

#: MariaDB names the offending index in the duplicate-entry message, which is the only way
#: to tell "somebody already took this seq, allocate another" from "this Google message is
#: already stored, which is success and the caller's business".
_SEQ_CONSTRAINT: Final[str] = "unique_room_seq"
_CLIENT_ID_CONSTRAINT: Final[str] = "unique_room_client_message_id"
_JOB_SEQ_CONSTRAINT: Final[str] = "unique_room_job_seq"

#: Room columns the write path needs. Read in one round trip in ``before_insert`` and
#: carried on ``doc.flags`` so ``after_insert`` does not read them again.
_ROOM_GATE_FIELDS: Final[tuple[str, ...]] = (
	"name",
	"provisioning_mode",
	"external_users_allowed",
	"mirror_whitelisted",
	"gchat_space_name",
)

#: Tags that mean "a line ended here" when a body arrives with markup in it.
_BLOCK_BREAK = re.compile(r"(?i)</\s*(p|div|li|tr|h[1-6]|blockquote|pre)\s*>|<\s*br\s*/?\s*>")
_TAG = re.compile(r"<[^>]*>")
_TRAILING_SPACE = re.compile(r"[ \t]+(\n|$)")
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


class RelayRefused(Exception):
	"""An outbound relay job was requested for something that must never be relayed.

	Deliberately **not** a :class:`frappe.ValidationError`: this is a programming error on
	our side, not bad input from a user, and it must not render as a form message that
	somebody clicks past. Deliberately not a bare ``assert`` either — ``python -O`` strips
	those, and this guard is worth more than the microsecond it costs.
	"""


# --- pure-ish helpers ----------------------------------------------------------


def extract_text_plain(text: str | None) -> str:
	"""Plain-text extraction for ``Chat Message.text_plain``.

	``text_plain`` is what the 32,000-byte relay budget is measured against, what Phase 5's
	retrieval chunks are assembled from, and what ``Chat Room.last_message_preview`` is cut
	from — so **no read path ever re-parses markup**, and getting it wrong here is a bug
	that surfaces three phases later as a summary quoting an HTML tag.

	``Chat Message.text`` is documented as *"the body AS AUTHORED, stored verbatim and never
	trusted"*, so this function assumes nothing about its format. Block-level tags become
	newlines (otherwise two paragraphs merge into one run-on word), remaining tags are
	dropped, entities are unescaped, and runs of blank lines collapse. It is a *normaliser*,
	not a sanitiser: sanitisation happens on the way out, in the client, using DOM APIs —
	this value is never rendered as HTML by anything.

	Pure: no ``frappe``, no I/O, no clock. That is what lets the bench-free tier cover it.
	"""
	body = text or ""
	if not isinstance(body, str):
		body = str(body)
	if not body:
		return ""

	body = _BLOCK_BREAK.sub("\n", body)
	body = _TAG.sub("", body)
	# The standard library's `html.unescape`, not a frappe helper: this runs on the insert
	# path of the busiest table in the feature and the entity table has not changed since
	# HTML5 was frozen.
	body = html.unescape(body)
	body = body.replace("\r\n", "\n").replace("\r", "\n")
	body = _TRAILING_SPACE.sub(r"\1", body)
	body = _EXCESS_BLANK_LINES.sub("\n\n", body)
	return body.strip()


def build_preview(text_plain: str | None, *, limit: int = PREVIEW_MAX_CHARS) -> str:
	"""``Chat Room.last_message_preview`` — plain text, truncated, **HTML-escaped by us**.

	The escaping is the field's stated contract and it is not decoration. Frappe's core
	``Notification Log`` renders its subject as markup, and the room list is the second
	place this string lands; a preview that arrives already escaped cannot turn a message
	body into a render surface no matter which consumer picks it up. Consumers that render
	as text must unescape — that direction is a cosmetic bug, the other direction is an
	injection.

	Newlines collapse to spaces: a preview is one line in a room list, and a multi-line
	preview breaks the layout of every consumer rather than one.
	"""
	flat = " ".join((text_plain or "").split())
	if len(flat) > limit:
		# The ellipsis is inside the budget, so the stored value never exceeds `limit`
		# characters. Room lists are laid out on that number.
		flat = flat[: max(limit - 1, 0)].rstrip() + "…"
	return escape_html(flat)


def truncation_suffix(room: str, message: str) -> str:
	"""The marker appended to a body that did not fit — a plain URL, never a markdown link.

	Google Chat's text formatting supports a ``<url|label>`` link form under some
	authentication modes and not others, and a formatting token Chat does not honour renders
	as literal punctuation in front of every truncated message. A bare URL auto-links
	everywhere and degrades to a readable URL where it does not.
	"""
	return f"\n… (truncated — full message: {links.build_message_deep_link(room, message)})"


def relay_text(message: Any) -> tuple[str, bool]:
	"""``(body, was_truncated)`` — the bytes the relay should send for one message.

	Called by the write path (to record ``truncated_for_relay``) **and** by the relay worker
	(to build the request). One function, so the flag on the row and the bytes on the wire
	cannot disagree.

	The body is deliberately **not** frozen into ``Chat Relay Job.payload``. Two reasons, and
	the second is the load-bearing one: the outbox row would become a second copy of every
	message body, in a table with a 30-day retention rule and a different permission story
	from ``Chat Message``; and an edit landing between compose and relay should send the
	corrected text, not the superseded text. The payload carries identifiers.
	"""
	settings = _settings()
	limit = cint(_setting(settings, "message_byte_limit")) or budget.MESSAGE_BYTE_LIMIT_DEFAULT
	body = getattr(message, "text_plain", None) or extract_text_plain(getattr(message, "text", None))
	body = _formatted_for_chat(body, message)
	room = getattr(message, "room", None) or ""
	name = getattr(message, "name", None) or ""
	suffix = truncation_suffix(room, name) if room and name else ""
	return budget.fit_to_byte_budget(body, limit - ENVELOPE_ESTIMATE_BYTES, suffix=suffix)


def _formatted_for_chat(body: str, message: Any) -> str:
	"""Translate Triton's markdown into Google Chat's own formatting. **Triton only.**

	Google Chat's bold is a *single* asterisk, so ``**Incompressibility:**`` reached the Chat
	client bold with a stray ``*`` glued to it — reported as "sometimes it bolds and sometimes
	it leaves a bold character", which is one deterministic mistranslation rather than two
	behaviours.

	**A human's message is left exactly as typed**, and that asymmetry is the decision rather
	than an oversight. Triton emits CommonMark by nature; a coworker typing ``2 * 3`` typed
	arithmetic, and a relay that rewrites what somebody wrote is a relay that lies about what
	they said. It also keeps the three surfaces consistent: the SPA renders markdown for
	Triton's replies and leaves human messages literal, for the same reason.

	Converting **before** the byte budget is applied, not after, so the truncation point is
	measured on the bytes that actually go on the wire. Doing it the other way can cut a
	message inside a delimiter the conversion is about to rewrite.

	**Keyed on ``sync_origin``, not ``sender_kind``, and the first version got this wrong.**
	It gated on ``sender_kind == "Triton"`` — a value **nothing in the codebase ever wrote**, so
	the conversion never ran once and every Triton answer reached Google Chat as raw CommonMark.
	It shipped green, because the converter's own tests were thorough and the call-site test
	only asserted that a gate existed rather than that it could ever match.

	``sync_origin`` is the right field here specifically: it is what
	:func:`_auth_identity_for` already reads two hundred lines below to make the *same* fork —
	Triton's replies post under the app identity, everyone else's under the human's. One fact,
	one source, inside one module. (``sender_kind`` is now also set, and is what the reader-side
	surfaces key on; see ``chat/invoke/handler.py``.)
	"""
	if str(getattr(message, "sync_origin", "") or "") != ORIGIN_TRITON:
		return body
	from erpnext_enhancements.chat.gchat.markdown import to_google_chat

	return to_google_chat(body)


# --- settings and flags --------------------------------------------------------


def _settings() -> Any:
	"""``Chat Settings``, or an empty bag if it is not installed yet.

	``doc_events`` fire during ERPNext's own test bootstrap, before this app's Singles have
	necessarily been seeded, and a missing settings row must not turn a fresh-DB install
	into a crash. Every read of the result goes through :func:`_setting`, which applies the
	house ``getattr(obj, "field", None) or ""`` rule.
	"""
	try:
		return frappe.get_cached_doc("Chat Settings")
	except Exception:
		return frappe._dict()


def _setting(settings: Any, field: str) -> Any:
	return getattr(settings, field, None)


def _in_bootstrap() -> bool:
	"""Is this an install, a migrate or a patch, rather than somebody sending a message?

	The side effects in :func:`finalise_new_message` — an outbox row, a realtime publish, a
	notification seam — are all wrong during a bootstrap, and one of them (the outbox row)
	would queue a Google write for a fixture. ``seq`` allocation is **not** guarded by this:
	a message row without a ``seq`` is broken whoever wrote it.
	"""
	flags = getattr(frappe, "flags", None)
	if flags is None:
		return False
	return bool(
		getattr(flags, "in_install", False)
		or getattr(flags, "in_migrate", False)
		or getattr(flags, "in_patch", False)
		or getattr(flags, "in_setup_wizard", False)
	)


def _is_import() -> bool:
	"""A backfill or an import, where a message is historical and nobody is to be notified.

	``frappe.flags.in_import`` covers the framework's own importer;
	``Chat Settings.import_mode_enabled`` covers ours. Both suppress the notification seam
	and nothing else — the rows are still real messages, they still get a ``seq``, and they
	still relay if the room is mirrored.
	"""
	if getattr(getattr(frappe, "flags", None), "in_import", False):
		return True
	return bool(cint(_setting(_settings(), "import_mode_enabled")))


# --- the room gate -------------------------------------------------------------


def room_snapshot(room: str) -> Any:
	"""The room columns the write path needs, in one round trip.

	``frappe.db.get_value`` and not ``get_cached_value``: ``external_users_allowed`` is
	re-read from Google on every reconciliation pass precisely because a space can acquire
	an external member long after it was provisioned, and serving a stale ``0`` from cache
	would mirror company conversation outside the domain for as long as the cache lived.
	"""
	row = frappe.db.get_value(ROOM_DOCTYPE, room, list(_ROOM_GATE_FIELDS), as_dict=True)
	if not row:
		frappe.throw(
			frappe._("Chat Room {0} does not exist; a message cannot be filed against it.").format(room),
			title=frappe._("Chat Message"),
		)
	return row


def relay_disposition(doc: Any, room_row: Any, settings: Any) -> tuple[bool, str]:
	"""``(should_relay, reason)`` for one new message. The single gate, evaluated once.

	Order is cheapest-and-most-absolute first, and every ``False`` carries a reason string
	that is stored nowhere but read in the debug log and in the tests — a gate whose refusals
	are indistinguishable from each other is a gate nobody can debug at 2am.

	The distinction that matters most: **``pause_outbound`` is not here.** It is the
	incident kill switch, and the whole point of a kill switch is that flipping it back
	drains the backlog in order. So a paused site still writes the outbox row and the worker
	declines to send; a site with ``relay_outbound_enabled`` off writes no row at all,
	because that flag is a rollout control and messages composed before the rollout are not
	owed a mirror.
	"""
	origin = getattr(doc, "sync_origin", None) or ORIGIN_ERPNEXT
	if origin == ORIGIN_GOOGLE_CHAT:
		return False, "inbound: this row came from Chat and relaying it back is the echo loop"

	if not cint(_setting(settings, "enabled")):
		return False, "Chat Settings.enabled is off (master rollout switch)"
	if not cint(_setting(settings, "google_sync_enabled")):
		return False, "Chat Settings.google_sync_enabled is off (ERPNext-only chat)"
	if not cint(_setting(settings, "relay_outbound_enabled")):
		return False, "Chat Settings.relay_outbound_enabled is off"

	if (getattr(doc, "message_type", None) or "") == "System":
		# A System message is ERPNext narrating its own state ("Bob was added to the room").
		# Chat emits its own membership notices for the same events, so relaying ours puts
		# the notice in the space twice, in two voices.
		return False, "System messages are ERPNext-side narration; Chat emits its own"

	if (getattr(room_row, "provisioning_mode", None) or "") == "Not Mirrored":
		return False, "Chat Room.provisioning_mode is Not Mirrored"

	if cint(getattr(room_row, "external_users_allowed", None)) and not cint(
		getattr(room_row, "mirror_whitelisted", None)
	):
		# §4.H data-egress policy, and the field descriptions say this in as many words:
		# the outbound relay skips a room with external members unless a human has ticked
		# Mirror Whitelisted. The decision to send company conversation outside the domain
		# belongs to a person.
		return False, "room admits external members and is not Mirror Whitelisted"

	return True, ""


# --- seq and job_seq -----------------------------------------------------------


def lock_room(room: str) -> None:
	"""Take the ``Chat Room`` row lock, held until this transaction commits.

	``SELECT … FOR UPDATE`` on the primary key: one row, one lock, no gap locks. Taking it
	again inside the same transaction is free — InnoDB already holds it — which is what lets
	:func:`allocate_seq` and :func:`allocate_job_seq` each ask for it without either needing
	to know whether the other ran first.
	"""
	frappe.db.sql(
		f"select name from `tab{ROOM_DOCTYPE}` where name = %(room)s for update",
		{"room": room},
	)


def allocate_seq(room: str) -> int:
	"""The next ``seq`` for ``room``, allocated inside the inserting transaction.

	One statement takes the lock and does the arithmetic; the read-back is safe because the
	lock is still held. See the module docstring for which half of this is the guarantee
	(``unique(room, seq)``) and which is the optimisation (this counter).

	``coalesce(seq_high_water, 0)`` rather than a bare ``+ 1``: Frappe declares ``Int`` as
	``NOT NULL DEFAULT 0`` today, and ``NULL + 1`` is ``NULL``, which would write a null
	``seq`` on a room whose column was ever added by hand.
	"""
	frappe.db.sql(
		f"update `tab{ROOM_DOCTYPE}` set seq_high_water = coalesce(seq_high_water, 0) + 1 "
		"where name = %(room)s",
		{"room": room},
	)
	rows = frappe.db.sql(
		f"select seq_high_water from `tab{ROOM_DOCTYPE}` where name = %(room)s",
		{"room": room},
		as_dict=True,
	)
	if not rows:
		frappe.throw(
			frappe._("Chat Room {0} does not exist; no sequence could be allocated.").format(room),
			title=frappe._("Chat Message"),
		)
	allocated = cint(rows[0].get("seq_high_water"))
	if allocated < 1:
		frappe.throw(
			frappe._(
				"Chat Room {0} allocated a non-positive sequence ({1}). seq is the transcript's "
				"only ordering and must be positive and monotonic."
			).format(room, allocated),
			title=frappe._("Chat Message"),
		)
	return allocated


def repair_seq_high_water(room: str) -> int:
	"""Catch the counter up to the room's real tail. **The cold path, and only the cold path.**

	This is the one place ``MAX(seq)`` over ``tabChat Message`` is legitimate, and the
	distinction is worth being precise about because the module docstring spends a paragraph
	forbidding it: ``MAX(seq)`` is never the **allocator**, it is the **repair**. It runs only
	after ``unique(room, seq)`` has already proved that the counter is behind reality — a
	restored dump, a hand-run backfill, a row written by something that bypassed this module —
	and no amount of re-incrementing a counter that is behind can escape the collision.

	It also has to run **outside** the failed insert's savepoint, which is the non-obvious
	half. ``ROLLBACK TO SAVEPOINT`` undoes the ``UPDATE`` that
	:func:`allocate_seq` issued, so a retry that simply allocates again receives the *same*
	number and collides identically until the attempt budget runs out. That is a real bug
	rather than a hypothetical: it was written, and ``test_a_seq_collision_is_retried_rather
	_than_raised`` found it on the first run.

	The room lock is retaken rather than assumed. Whether ``ROLLBACK TO SAVEPOINT`` releases
	row locks taken after the savepoint is version- and engine-specific, and re-taking a lock
	this transaction already holds costs nothing.
	"""
	lock_room(room)
	rows = frappe.db.sql(
		f"select coalesce(max(seq), 0) as high from `tab{MESSAGE_DOCTYPE}` where room = %(room)s",
		{"room": room},
		as_dict=True,
	)
	high = cint(rows[0].get("high") if rows else 0)
	frappe.db.sql(
		f"update `tab{ROOM_DOCTYPE}` set seq_high_water = %(high)s "
		"where name = %(room)s and coalesce(seq_high_water, 0) < %(high)s",
		{"high": high, "room": room},
	)
	return high


def allocate_job_seq(room: str) -> int:
	"""The next ``Chat Relay Job.job_seq`` for ``room`` — the per-room FIFO position.

	Takes the **``Chat Room``** row lock first and then reads ``MAX(job_seq)`` off
	``tabChat Relay Job``. The lock is not on the table being read, which is unusual enough
	to be worth stating twice: it works because *every* allocator takes the same room lock,
	so two transactions cannot be between the read and the insert at the same time for one
	room. Rule 1 (CREATE-BEFORE-EDIT) is only as strong as this number, and
	``unique(room, job_seq)`` catches the case where somebody bypasses
	:func:`create_relay_job`.
	"""
	lock_room(room)
	rows = frappe.db.sql(
		f"select coalesce(max(job_seq), 0) as high from `tab{RELAY_JOB_DOCTYPE}` where room = %(room)s",
		{"room": room},
		as_dict=True,
	)
	return cint(rows[0].get("high") if rows else 0) + 1


# --- duplicate-tolerant insert -------------------------------------------------


def _savepoint_name() -> str:
	"""Frappe's own scheme, copied: ten random lowercase letters, no user input."""
	return "chat" + "".join(random.sample(string.ascii_lowercase, 10))


def _insert_catching_duplicates(doc: Any, **insert_kwargs: Any) -> Exception | None:
	"""Insert ``doc``; return the duplicate exception if one fired, otherwise ``None``.

	**Catch both exceptions, and the obvious guess is the wrong one.**
	``frappe.DuplicateEntryError`` is raised *only* for a primary-key collision — a duplicate
	``name``. Every other unique index, which is every index this module cares about, raises
	``frappe.UniqueValidationError``; the two share no base beyond ``Exception``
	(``DuplicateEntryError`` derives from Frappe's ``NameError``, ``UniqueValidationError``
	from ``ValidationError``). Catching only the first would fail open on exactly the
	collision the design rests on.

	**Why this hand-rolls the savepoint** instead of using
	``frappe.database.database.savepoint(catch=…)``: that context manager *swallows* the
	exception, and the callers here have to tell a ``(room, seq)`` collision — retry — from a
	``gchat_message_name`` collision — success, and the caller's business. The mechanism is
	identical; only the exception's visibility differs.

	``show_unique_validation_message`` calls ``frappe.msgprint`` *before* raising, so an
	expected duplicate would leak a user-visible "must be unique" toast. It is cleared here.
	"""
	point = _savepoint_name()
	frappe.db.savepoint(point)
	try:
		doc.insert(**insert_kwargs)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError) as exc:
		frappe.db.rollback(save_point=point)
		_clear_duplicate_toast()
		return exc
	frappe.db.release_savepoint(point)
	return None


def _clear_duplicate_toast() -> None:
	"""Drop the "must be unique" message Frappe queued on its way to raising."""
	try:
		frappe.clear_last_message()
	except Exception:
		log = getattr(getattr(frappe, "local", None), "message_log", None)
		if isinstance(log, list) and log:
			log.pop()


def _names_constraint(exc: Exception, constraint: str) -> bool:
	"""Does this duplicate-entry error name ``constraint``?

	MariaDB's text is ``Duplicate entry 'x' for key 'unique_room_seq'``. Matching on the
	constraint name is the only way to distinguish the indexes, and the names are pinned in
	``patches/add_chat_indexes.py``. A miss reads as "not the seq index", which routes the
	exception to the caller rather than silently retrying — the safe direction.
	"""
	return constraint in str(exc)


def insert_message(doc: Any, *, max_attempts: int = SEQ_COLLISION_ATTEMPTS) -> Any:
	"""Insert a ``Chat Message``, treating a ``(room, seq)`` collision as a retry.

	**This is the entry point every writer should use** — the composer, the inbound ingest
	and the reconciliation backfill alike. A bare ``doc.insert()`` still works and is still
	correct; it just turns the one-in-a-million collision into a user-visible error instead
	of a second allocation.

	A collision on ``gchat_message_name`` or on ``(room, client_message_id)`` is **not**
	retried here. Both mean "this message is already stored", which is success for the
	inbound writer and a caller-level fact rather than an allocation problem, so the
	exception is re-raised for the caller to interpret. Retrying it would insert a duplicate
	under a fresh ``seq``, which is the exact outcome the unique index exists to prevent.
	"""
	last: Exception | None = None
	for _attempt in range(max(int(max_attempts), 1)):
		failure = _insert_catching_duplicates(doc, ignore_permissions=True)
		if failure is None:
			return doc
		if not _names_constraint(failure, _SEQ_CONSTRAINT):
			raise failure
		last = failure
		# Clear the derived identity so the next pass allocates a fresh seq *and* a fresh
		# client_message_id: `set_new_name` nulls `name` on every attempt (autoname is
		# `hash`), so an id derived from the previous name would no longer match its row.
		doc.seq = 0
		doc.client_message_id = ""
		# And catch the counter up, OUTSIDE the rolled-back savepoint. Without this the retry
		# is not a retry: the rollback undid the increment, so allocate_seq hands back the
		# number that just collided, five times in a row.
		repair_seq_high_water(getattr(doc, "room", None) or "")

	raise last if last else RuntimeError("insert_message exhausted its attempts without an error")


# --- the document-event bodies -------------------------------------------------


def prepare_new_message(doc: Any) -> None:
	"""``Chat Message.before_insert``. Everything that does not need ``doc.name``.

	**``doc.name`` is not available here**, and that is a framework fact rather than an
	oversight: ``Document.insert`` calls ``run_method("before_insert")`` and only *then*
	``set_new_name``, which for ``autoname: hash`` begins by assigning ``doc.name = None``
	(``frappe/model/naming.py``, v16). So ``client_message_id`` — which is derived from the
	name — cannot be minted here, and is minted in :func:`derive_client_message_id` from
	``validate`` instead. Both this module's earlier docstring and ``gchat/ids.py`` say
	"computed in ``before_insert``"; they were written before anybody read the ordering.

	The oversized **Reject** check runs before the sequence is allocated, so a refused
	message does not burn a ``seq`` and does not hold the room lock for the length of a
	form round trip.
	"""
	room = (getattr(doc, "room", None) or "").strip()
	if not room:
		frappe.throw(
			frappe._("A chat message must belong to a room."),
			title=frappe._("Chat Message"),
		)

	doc.sync_origin = (getattr(doc, "sync_origin", None) or ORIGIN_ERPNEXT).strip() or ORIGIN_ERPNEXT
	doc.text_plain = extract_text_plain(getattr(doc, "text", None))
	_set_thread_root(doc)

	settings = _settings()
	room_row = room_snapshot(room)
	should_relay, reason = relay_disposition(doc, room_row, settings)

	# Carried to after_insert on flags rather than re-read: the gate must be evaluated once,
	# or a settings change landing mid-insert could write a `Pending` sync_state with no
	# outbox row behind it — a message that renders as "sending" forever.
	doc.flags.chat_should_relay = should_relay
	doc.flags.chat_relay_reason = reason
	doc.flags.chat_room_snapshot = room_row

	if should_relay:
		_enforce_oversized_policy(doc, settings)

	doc.sync_state = _initial_sync_state(doc, should_relay)
	if not (getattr(doc, "seq", None) or 0):
		doc.seq = allocate_seq(room)


def derive_client_message_id(doc: Any) -> None:
	"""``Chat Message.validate``'s naming-dependent half. Idempotent by design.

	Runs from ``validate`` because that is the first hook after ``set_new_name``, and
	``client_message_id`` is ``reqd`` so it must exist before ``_validate()`` runs the
	mandatory check a few statements later.

	Guarded on "already set" for two reasons: ``validate`` also runs on every subsequent
	save, and an edit must never re-mint the idempotency key Google has already seen; and
	:func:`insert_message` deliberately clears it before a retry so a fresh name gets a
	matching id.
	"""
	if (getattr(doc, "client_message_id", None) or "").strip():
		return
	name = (getattr(doc, "name", None) or "").strip()
	if not name:
		frappe.throw(
			frappe._(
				"Chat Message has no name at validate time, so no clientAssignedMessageId can be "
				"derived. Something has changed in Frappe's insert ordering — see "
				"erpnext_enhancements.chat.sync.outbox.prepare_new_message."
			),
			title=frappe._("Chat Message"),
		)
	site = getattr(getattr(frappe, "local", None), "site", None) or ""
	doc.client_message_id = ids.client_message_id(name, site=site)


def finalise_new_message(doc: Any) -> None:
	"""``Chat Message.after_insert``. Denormalise, mark read, enqueue, publish, notify.

	Order is deliberate. The durable work — the room denormalisation, the author's read mark
	and the outbox row — happens first and inside the transaction, so a failure rolls the
	message back with it rather than leaving a message nobody will relay. The two
	observational steps come last and **cannot** fail the insert: a realtime publish that
	raises would destroy the message it exists to announce, which is strictly worse than a
	missing event.

	**A row that is born deleted announces nothing.** The inbound ingest lands a message
	already tombstoned when Google's own resource is already a tombstone by the time we fetch
	it — a ``deleted`` event that overtook its ``created``, or a reconciliation replay weeks
	later; see :func:`erpnext_enhancements.chat.sync.inbound._apply_created`. The row is still
	stored, still gets a ``seq`` and still denormalises onto the room, because ERPNext is the
	record that this message existed. But notifying somebody about a message that is already
	deleted is worse than not notifying at all, and a ``chat_message_created`` event would push
	the SPA to render a row every read path filters out. Both are suppressed, and the
	suppression lives here rather than in the ingest because ``after_insert`` is the single
	place either one fires — which is also what makes "exactly once per genuinely new message"
	structural rather than a rule three writers have to remember.
	"""
	if _in_bootstrap():
		return

	_denormalise_room(doc)
	_advance_author_read_mark(doc)

	if doc.flags.get("chat_should_relay"):
		_record_truncation(doc)
		create_relay_job(
			room=doc.room,
			operation=OPERATION_MESSAGE_CREATE,
			reference_doctype=MESSAGE_DOCTYPE,
			reference_name=doc.name,
			origin=getattr(doc, "sync_origin", None) or ORIGIN_ERPNEXT,
			impersonate_user=getattr(doc, "sender", None) or "",
			request_id=ids.request_id(
				doc.name,
				OPERATION_MESSAGE_CREATE,
				site=getattr(getattr(frappe, "local", None), "site", None) or "",
			),
			payload={
				"kind": "message.create",
				"message": doc.name,
				"seq": cint(getattr(doc, "seq", 0)),
				"client_message_id": getattr(doc, "client_message_id", None) or "",
				"thread_root": getattr(doc, "thread_root", None) or "",
			},
		)

	if cint(getattr(doc, "is_deleted", 0)):
		return

	_publish_created(doc)

	if not _is_import():
		seams.notify_new_message(doc)


def refresh_text_plain(doc: Any) -> None:
	"""``Chat Message.validate``'s other half: keep ``text_plain`` in step with an edited body.

	``prepare_new_message`` computes it once at ``before_insert`` and, until this existed,
	never again — so an edited message kept the *original* extraction forever. That is not a
	cosmetic staleness: :func:`relay_text` reads ``text_plain`` in preference to ``text``, and
	so does the relay worker's ``_message_context``, which means an outbound ``Message Update``
	would have patched Chat with the wording the author had just replaced. Phase 5's retrieval
	chunks and the room preview read the same column.

	Only on an actual edit, and only when ``text`` really moved. ``extract_text_plain`` runs
	two regexes over the whole body, this runs inside ``validate`` on the busiest table in the
	feature, and the insert path has already done the work.
	"""
	before = _before_image(doc)
	if before is None:
		return
	if (before.get("text") or "") == (getattr(doc, "text", None) or ""):
		return
	doc.text_plain = extract_text_plain(getattr(doc, "text", None))


def propagate_message_change(doc: Any) -> None:
	"""``Chat Message.on_update``. Relay an edit or a soft delete — and usually neither.

	The §4.F outbound half. ``outbound`` has registered ``Message Update`` and
	``Message Delete`` handlers since wave 1, but nothing outside inbound's Rule-3 revert could
	reach them, so an ERPNext edit reached Chat *never* and an ERPNext delete left the message
	on screen in the space indefinitely.

	Read the module docstring for the three ways the obvious implementation is wrong. In
	order, this body:

	1. refuses during a bootstrap, like every other side effect here;
	2. refuses without a before-image — which is how it tells an edit from the insert, because
	   ``Document.insert`` runs ``on_update`` as well and ``has_value_changed`` answers ``True``
	   for every field when there is nothing to compare against;
	3. refuses when neither ``text`` nor ``is_deleted`` moved, so the relay's own
	   ``sync_state`` write cannot queue its own successor;
	4. marks the room's Phase 5 context stale **and writes the ``Chat Message Revision``**
	   before consulting the relay gate, because both are ERPNext-side facts: a room nobody
	   mirrors still has a digest that must not quote text the author deleted, and it still
	   has an audit trail that has to record what the text used to be;
	5. and only then applies the same :func:`relay_disposition` the create path uses — which
	   is where the ``sync_origin == "Google Chat"`` refusal lives. An inbound row generating
	   an outbound job is the echo loop with an extra step: a coworker edits in Chat, we relay
	   their own edit back at them, and Chat's event for *that* arrives as the next edit.

	A delete supersedes an edit made in the same save. Google's tombstone carries no content,
	so patching the text of a message we are about to delete spends a write from a
	one-per-second budget to change nothing observable.

	Only 0 → 1 relays. There is no un-delete in Chat — the deletion is permanent and there is
	no call to reverse it — so an ERPNext restore has no counterpart to send, and queuing a
	fresh create would collide with a ``messageId`` that is unique within the space forever.
	"""
	if _in_bootstrap():
		return

	before = _before_image(doc)
	if before is None:
		return

	room = (getattr(doc, "room", None) or "").strip()
	if not room:
		return

	became_deleted = bool(cint(getattr(doc, "is_deleted", 0))) and not cint(before.get("is_deleted"))
	text_changed = (before.get("text") or "") != (getattr(doc, "text", None) or "")
	if not became_deleted and not text_changed:
		return

	# Before the gate, deliberately. See point 4 above.
	seq = cint(getattr(doc, "seq", 0))
	seams.mark_room_context_stale(room, seq, seq)
	_record_change_revision(doc, before, became_deleted=became_deleted)

	should_relay, _reason = relay_disposition(doc, room_snapshot(room), _settings())
	if not should_relay:
		return

	if became_deleted:
		_enqueue_message_delete(doc)
		return

	_record_edit_truncation(doc)
	_enqueue_message_update(doc)


def refuse_hard_delete(doc: Any) -> None:
	"""``Chat Message.on_trash``. **Refuses**, and relays nothing. Both halves are the design.

	*Why refuse.* The delete in this system is a soft delete that keeps ``text`` on the row
	(ADR §F.6.5). Google's tombstone is rich in metadata and empty of content —
	``showDeleted=true`` returns the name, the delete time and ``deletionMetadata``, never the
	body — so after a relay ERPNext is the only party that still has what was said. A hard
	delete therefore destroys evidence rather than hiding it, and it does so on the table this
	company would be asked to produce. Every read path already filters ``is_deleted = 0``, so
	the soft delete is indistinguishable from a hard one to everybody except the Phase 6
	oversight endpoint, which pays for the difference with an audit row.

	*Why it relays nothing.* Even if a hard delete were permitted, ``on_trash`` could not be
	the propagation point: a ``Chat Relay Job`` addresses its message by ``reference_name`` and
	the worker reads ``client_message_id`` off that row, and both are gone by the time any
	worker runs. The job would ``Skip`` on a message that was in fact deleted, leaving the
	Chat copy live with nothing left in ERPNext to reconcile it against.

	Two escapes, and both are narrow. A bootstrap (install, migrate, patch) is not somebody
	deleting a colleague's message, and a controller that refused there would brick a patch
	rolling back its own fixtures. ``doc.flags.chat_allow_hard_delete`` is the explicit,
	greppable opt-in for the Phase 6 retention/erasure path, which has to write its audit row
	first.
	"""
	if _in_bootstrap():
		return

	flags = getattr(doc, "flags", None)
	if flags is not None and flags.get("chat_allow_hard_delete"):
		return

	frappe.throw(
		frappe._(
			"A chat message cannot be deleted outright. Set is_deleted instead: the delete is a "
			"soft delete that keeps the text, because Google Chat's own tombstone contains no "
			"message content — so if this row goes, nobody has what was said. Every read path "
			"already hides a message with is_deleted set."
		),
		title=frappe._("Chat Message"),
	)


# --- on_update internals -------------------------------------------------------


def _before_image(doc: Any) -> Any:
	"""The row as it was before this save, or ``None`` when there is no such thing.

	``None`` has three causes and every one of them means "do not relay an edit". The save is
	the **insert** — ``Document.insert`` calls ``run_post_save_methods()`` right after
	``after_insert``, which runs ``on_update`` with ``flags.in_insert`` still set and no
	before-image. Or the document was written by something that never loaded one. Or
	``load_doc_before_save`` could not read the row, in which case the row is gone and an edit
	relay against it would fail anyway.

	This is the guard that makes ``has_value_changed`` safe to use afterwards. Frappe's
	implementation is ``previous.get(f) != self.get(f) if previous else True``, so on an insert
	it answers ``True`` for *every* field — gating the outbound edit on it alone would relay a
	second, identical message for every message anyone ever sends.
	"""
	getter = getattr(doc, "get_doc_before_save", None)
	if not callable(getter):
		return None
	try:
		return getter()
	except Exception:
		# A before-image we cannot load is indistinguishable from one that does not exist, and
		# both answers route to "no outbound edit" — so this never needs to be an Error Log row.
		return None


def _record_change_revision(doc: Any, before: Any, *, became_deleted: bool) -> None:
	"""One ``Chat Message Revision`` for an ERPNext-side edit or soft delete — §4.F's other half.

	``inbound.record_revision`` was the only writer of this table in the package, so
	*"a revision is written on every mutation"* held for **Chat-side** mutations only: an edit
	or a delete performed in ERPNext left no audit row at all. That is the half of the trail a
	user is most likely to ask about, because it is the half they performed themselves, and on
	the delete branch it is the only place the deleted body is preserved as a *before* image —
	Google's tombstone carries no content, so after a relay this row and the message row are
	between them the last copies of what was said.

	**Three things to get right, and the first is the reason this is a function and not a line.**

	*``text_before`` comes from the before-image, never from the row.* ``on_update`` runs
	**after** the row is written, so ``frappe.db.get_value(..., "text")`` here would return the
	*new* body and the revision would record a transition from the text to itself — an audit
	row that looks complete and says nothing. ``before`` is the pre-save image the caller has
	already loaded, which is the only copy of the superseded body still in memory.

	*It is written before the relay gate.* **The audit trail is not conditional on the mirror.**
	An unmirrored room, a paused rollout switch and a Google-origin row are all reasons not to
	send a Chat write; none of them is a reason not to record what an employee changed.

	*A delete supersedes an edit made in the same save*, matching the relay's own precedence,
	and ``text_before`` is still the pre-save body: what the delete buried is what was there
	before this save, whether or not the same save also rewrote it.

	Deliberately **not** wrapped in a try/except. Unlike the realtime publish next door — where
	a failure must not destroy the message it exists to announce — this row is the record, not
	an announcement, and it is written inside the same transaction as the mutation. Either both
	land or neither does, which is the property an e-discovery answer rests on.

	``record_revision`` is reached by a function-local import for the same reason
	``inbound._outbox_attr`` reaches back the other way: this module is loaded by a document
	event, and pulling the inbound package's import graph in behind every ``Chat Message`` save
	buys nothing. Reusing it also keeps one numbering scheme for the table — it allocates
	``revision_no`` as ``max + 1`` inside this transaction, so an ERPNext edit of a Chat-origin
	message continues that message's existing trail instead of starting a second one, and two
	*concurrent* writers that compute the same number are separated by
	``unique(message, revision_no)`` with the loser treating the refusal as success.
	"""
	from erpnext_enhancements.chat.doctype.chat_message_revision.chat_message_revision import (
		CHANGE_DELETE,
		CHANGE_EDIT,
	)
	from erpnext_enhancements.chat.sync.inbound import record_revision, site_to_utc

	before_text = str((before or {}).get("text") or "")
	after_text = str(getattr(doc, "text", None) or "")

	# `frappe.session.user` and nothing cleverer: the audit row records who saved this document,
	# which for a background job is honestly that job's user rather than the message's author.
	# Guessing the author here would attribute a moderator's delete to the person moderated.
	actor = str(getattr(getattr(frappe, "session", None), "user", None) or "")
	actor = "" if actor in ("Guest",) else actor

	if became_deleted:
		stamped = getattr(doc, "deleted_at", None) or now_datetime()
		record_revision(
			doc.name,
			room=doc.room,
			change_type=CHANGE_DELETE,
			# The body is kept on the live row as well (ADR §F.6.5) — this is the *audit* copy,
			# and it is the one that survives a later hard delete run by the Phase 6 retention
			# path. `text_after` is empty because a delete has no resulting body.
			text_before=before_text,
			text_after="",
			actor=actor or None,
			actor_email=actor,
			origin=ORIGIN_ERPNEXT,
			origin_timestamp=site_to_utc(stamped),
			note="erpnext delete; the body is retained here and on the row",
		)
		return

	stamped = getattr(doc, "edited_at", None) or now_datetime()
	record_revision(
		doc.name,
		room=doc.room,
		change_type=CHANGE_EDIT,
		text_before=before_text,
		text_after=after_text,
		actor=actor or None,
		actor_email=actor,
		origin=ORIGIN_ERPNEXT,
		origin_timestamp=site_to_utc(stamped),
		note="erpnext edit",
	)


def _record_edit_truncation(doc: Any) -> None:
	"""``truncated_for_relay`` for an edited body — which, unlike a new one, can move back to 0.

	:func:`_record_truncation` on the create path only ever *sets* the flag: a new row starts
	at 0, so a create that fits has nothing to record, and writing the 0 anyway would add a
	second write to every insert on the hottest table in the feature. An edit is the other
	case. A body that crossed 32,000 bytes can be shortened again, and a flag that never
	clears tells a reader the Chat copy is a summary long after it stopped being one.

	One call to :func:`relay_text`, so the flag on the row and the bytes the worker will send
	are decided by the same arithmetic and cannot drift apart.
	"""
	_body, truncated = relay_text(doc)
	flag = 1 if truncated else 0
	if cint(getattr(doc, "truncated_for_relay", 0)) == flag:
		return
	doc.truncated_for_relay = flag
	frappe.db.set_value(MESSAGE_DOCTYPE, doc.name, "truncated_for_relay", flag, update_modified=False)


def _enqueue_message_update(doc: Any) -> str:
	"""One ``Message Update`` outbox row for an edited body.

	**No ``request_id``, and that is not an omission.** ``spaces.messages.patch`` accepts no
	``requestId`` parameter at all, and a deterministic value derived from the message name —
	which is all we have — would be *identical* across two successive edits of the same
	message, so the queue would show what looks like a replay of the first edit. Idempotency
	on this path comes from the ``client-`` alias instead: the patch is idempotent by
	construction, and ``allowMissing=true`` makes it an upsert if it somehow overtakes its
	create.

	``impersonate_user`` is the message's **author**, not whoever pressed save. App auth may
	only patch what the app created, so editing a human's message is a DWD impersonation of
	that human; pinning it on the row is what stops a retry drifting to a different principal
	and 403ing in a way that reads like a scope problem.
	"""
	return create_relay_job(
		room=doc.room,
		operation=OPERATION_MESSAGE_UPDATE,
		reference_doctype=MESSAGE_DOCTYPE,
		reference_name=doc.name,
		origin=getattr(doc, "sync_origin", None) or ORIGIN_ERPNEXT,
		impersonate_user=getattr(doc, "sender", None) or "",
		payload={"kind": "message.update", "message": doc.name, "reason": "erpnext-edit"},
	)


def _enqueue_message_delete(doc: Any) -> str:
	"""One ``Message Delete`` outbox row for a soft-deleted message.

	The author again, for the same reason as the edit and one more: the ``deletionType``
	Google records is derived from the principal, so impersonating the author yields
	``CREATOR_VIA_APP`` and attribution stays clean without anybody having to guess. A manager
	deleting somebody else's message is Phase 6 moderation, it needs
	``SPACE_OWNER_VIA_APP``, and whether an impersonated manager may delete another member's
	message is on the unproven list — so this path does not attempt it.

	No ``request_id``: ``spaces.messages.delete`` takes none, and a 404 from it is already
	success (the message is not there, which is what the job wanted).
	"""
	return create_relay_job(
		room=doc.room,
		operation=OPERATION_MESSAGE_DELETE,
		reference_doctype=MESSAGE_DOCTYPE,
		reference_name=doc.name,
		origin=getattr(doc, "sync_origin", None) or ORIGIN_ERPNEXT,
		impersonate_user=getattr(doc, "sender", None) or "",
		payload={"kind": "message.delete", "message": doc.name},
	)


# --- after_insert internals ----------------------------------------------------


def _denormalise_room(doc: Any) -> None:
	"""Write ``last_message*`` onto the room, with ``update_modified=False``.

	A plain overwrite, with no "is this newer?" guard, and the room row lock is why that is
	correct: ``seq`` is allocated under it, so the transaction that commits second is
	necessarily the one holding the higher ``seq``. A late arrival — an inbound message whose
	Google ``createTime`` predates the room tail — still receives the next ``seq`` and still
	*is* the tail by the only ordering this system has.

	``update_modified=False`` keeps ``Chat Room.modified`` meaningful: it should move when
	somebody renames or archives the room, not on every message.
	"""
	frappe.db.set_value(
		ROOM_DOCTYPE,
		doc.room,
		{
			"last_message": doc.name,
			"last_message_at": getattr(doc, "creation", None) or now_datetime(),
			"last_message_sender": getattr(doc, "sender", None) or None,
			"last_message_preview": build_preview(getattr(doc, "text_plain", None)),
		},
		update_modified=False,
	)


def _advance_author_read_mark(doc: Any) -> None:
	"""A person's own message is already read by them.

	Without this, sending a message marks your own room unread — and ``sender_kind``'s field
	description already anticipates the same problem for Triton replies. Delegated to
	``Chat Room Member`` so the monotonic ``last_read_seq`` rule lives with the column.
	"""
	sender = getattr(doc, "sender", None) or ""
	if not sender:
		return
	from erpnext_enhancements.chat.doctype.chat_room_member.chat_room_member import advance_read_mark

	advance_read_mark(doc.room, sender, cint(getattr(doc, "seq", 0)))


def _record_truncation(doc: Any) -> None:
	"""Set ``truncated_for_relay`` when the body will not fit Google's byte ceiling.

	Written after the insert with ``update_modified=False`` rather than in ``before_insert``,
	because the deep link in the truncation suffix needs ``doc.name`` and the suffix is
	reserved *inside* the budget — so the flag and the bytes are decided by the same call to
	:func:`relay_text` and cannot drift apart.
	"""
	_body, truncated = relay_text(doc)
	if not truncated:
		return
	doc.truncated_for_relay = 1
	frappe.db.set_value(MESSAGE_DOCTYPE, doc.name, "truncated_for_relay", 1, update_modified=False)


def _publish_created(doc: Any) -> None:
	"""Announce the new message to the room's document room. Identifiers only, never the body.

	Wrapped, and this is the one place in the chat package where a realtime failure is
	swallowed. :mod:`chat.realtime` raises on a bad publish deliberately — "a publish that
	silently does nothing is indistinguishable from one that worked" — but here the caller
	is ``after_insert``, so the exception would roll back the message. A missing event costs
	one refresh; a lost message costs the thing the feature is for.
	"""
	try:
		realtime.publish_room_event(
			doc.room,
			realtime.EVENT_MESSAGE_CREATED,
			{
				"message": doc.name,
				"room": doc.room,
				"seq": cint(getattr(doc, "seq", 0)),
				"sender": getattr(doc, "sender", None) or "",
				"sender_kind": getattr(doc, "sender_kind", None) or "",
				"message_type": getattr(doc, "message_type", None) or "",
				"thread_root": getattr(doc, "thread_root", None) or "",
				"has_attachments": cint(getattr(doc, "has_attachments", 0)),
			},
		)
	except Exception as exc:
		# Type name and identifiers only. No payload, no body, no traceback: a bare re-raise
		# or a `frappe.get_traceback()` out of a background job publishes frame locals into
		# the Error Log, and this app has leaked private key material exactly that way once.
		frappe.log_error(
			title="chat outbox: realtime publish failed",
			message=f"{type(exc).__name__} message={doc.name} room={doc.room}",
		)


# --- before_insert internals ---------------------------------------------------


def _set_thread_root(doc: Any) -> None:
	"""Denormalise the thread root so the thread panel is one indexed range scan.

	Threading is exactly one level deep (the reply edge mirrors the existing Comments app),
	which is why ``thread_root`` never needs recomputation: a reply to a reply attaches to
	the same root. A root message stores ``NULL`` rather than its own name — it has no name
	yet at ``before_insert``, and ``(thread_root, seq)`` is a non-unique index where NULLs
	cost nothing.
	"""
	parent = (getattr(doc, "parent_message", None) or "").strip()
	if not parent:
		doc.thread_root = None
		return
	row = frappe.db.get_value(MESSAGE_DOCTYPE, parent, ["name", "thread_root"], as_dict=True)
	if not row:
		frappe.throw(
			frappe._("Chat Message {0} does not exist, so this reply has no thread to join.").format(parent),
			title=frappe._("Chat Message"),
		)
	doc.thread_root = (row.get("thread_root") or row.get("name")) or None


def _initial_sync_state(doc: Any, should_relay: bool) -> str:
	"""The ``sync_state`` a brand-new row starts in.

	Three answers and no fourth. ``Inbound`` for a row that came from Chat — permanently,
	whatever any relay job later does to it. ``Pending`` for a row with an outbox entry
	behind it. ``Not Mirrored`` for everything else, which is the honest answer for a room
	nobody is mirroring and the one that keeps the health report's ``Pending`` count
	meaningful.
	"""
	if (getattr(doc, "sync_origin", None) or "") == ORIGIN_GOOGLE_CHAT:
		return states.MessageSyncState.INBOUND.value
	return (
		states.MessageSyncState.PENDING.value if should_relay else states.MessageSyncState.NOT_MIRRORED.value
	)


def _enforce_oversized_policy(doc: Any, settings: Any) -> None:
	"""CQ-11's ``Reject`` branch. Only reachable for a message that would actually relay.

	``Truncate With Link`` — the default — needs nothing here: the cut happens at relay time
	via :func:`relay_text`, and the whole body stays on the ERPNext row, which is the source
	of truth.

	The byte count is over the encoded resource and not ``len(text)``: UTF-8 gives four bytes
	for an emoji above the BMP and three for CJK, so a message that "looks" half the limit
	can exceed it.
	"""
	if (_setting(settings, "oversized_message_policy") or "") != "Reject":
		return

	limit = cint(_setting(settings, "message_byte_limit")) or budget.MESSAGE_BYTE_LIMIT_DEFAULT
	size = budget.message_payload_bytes(getattr(doc, "text_plain", None), extra_bytes=ENVELOPE_ESTIMATE_BYTES)
	if size <= limit:
		return

	frappe.throw(
		frappe._(
			"This message is {0} bytes and Google Chat accepts at most {1} for a mirrored "
			"message. Chat Settings is set to Reject oversized messages, so it has not been "
			"saved. Shorten it, or ask an administrator to switch the Oversized Message Policy "
			"to Truncate With Link — which relays a shortened copy plus a link back to the full "
			"text and keeps everything you wrote here."
		).format(size, limit),
		title=frappe._("Message too long to mirror"),
	)


# --- the outbox row ------------------------------------------------------------


def auth_identity_for_origin(origin: str) -> str:
	"""Which Google identity a job of this origin is written as. **The whole decision.**

	--------------------------------------------------------------------------------------
	Two identities, and only one of them costs a Workspace seat
	--------------------------------------------------------------------------------------

	``USER`` is domain-wide delegation of a named human. It is what the coworker mirror needs
	and it is not negotiable there: a mirrored message *is* that person's, an App badge on it
	would be a lie about who spoke, and Google only lets app auth patch or delete messages the
	app itself created — so editing a coworker's message means being that coworker. CQ-1.

	``APP`` is the registered Chat app, and it is right for exactly one thing: **Triton's own
	replies**. ``gchat/client.py`` has said so since Phase 1 — ``AuthIdentity.APP`` is
	documented there as "Triton's replies, and nothing else" — and ``auth.get_app_token`` was
	written for it. The relay simply never grew the second half, and hardcoded ``USER``.

	The practical difference is a licence. A ``USER`` write needs a real, licensed Workspace
	account that is a **joined member** of the space; the bot has neither and buying one for a
	reply nobody attributes to a person is paying for the wrong thing. A Chat app is *added*
	to a space, not licensed. What it costs instead is the App badge — which for a bot reply
	is not a cost at all, it is the truth.

	Derived once at write time and frozen on the row, for the same reason ``impersonate_user``
	is: a retry that re-derived it could drift to a different identity, and the resulting 403
	reads like a scope problem and sends the next person to the wrong file.
	"""
	return (
		states.AUTH_IDENTITY_APP if (origin or "") == ORIGIN_TRITON else states.AUTH_IDENTITY_USER
	)


def create_relay_job(
	*,
	room: str,
	operation: str,
	reference_doctype: str = "",
	reference_name: str = "",
	payload: dict[str, Any] | None = None,
	impersonate_user: str = "",
	request_id: str = "",
	available_at: Any = None,
	origin: str = ORIGIN_ERPNEXT,
	enqueue: bool = True,
) -> str:
	"""Write one outbox row **in the caller's transaction** and enqueue it after commit.

	Every outbound Google operation goes through here — message create, edit, delete, space
	create, membership add/remove, attachment upload — because ``job_seq`` allocation depends
	on a lock taken on a *different* table (see :func:`allocate_job_seq`) and a direct insert
	would not take it.

	``origin`` is the ``Chat Message.sync_origin`` of the referenced row where there is one.
	Passing ``"Google Chat"`` raises :class:`RelayRefused`, unconditionally and before any
	work: relaying an inbound message back to Chat is the echo loop, it compounds, and at one
	write per second per space the room fills with duplicates faster than the feature can be
	turned off.

	A ``unique(room, job_seq)`` collision re-allocates and retries, for the same reason
	``(room, seq)`` does: the number is an ordering position, not an identity, so losing a
	race for one means taking the next.
	"""
	identity = auth_identity_for_origin(origin)

	if (origin or "") == ORIGIN_GOOGLE_CHAT:
		raise RelayRefused(
			f"refusing to create a {operation!r} relay job for {reference_doctype} "
			f"{reference_name!r}: its sync_origin is {ORIGIN_GOOGLE_CHAT!r}. A row that came "
			"from Chat must never be relayed back to Chat — that is the echo loop, and it "
			"compounds."
		)

	body = frappe.as_json(payload or {})
	last: Exception | None = None

	for _attempt in range(SEQ_COLLISION_ATTEMPTS):
		job = frappe.get_doc(
			{
				"doctype": RELAY_JOB_DOCTYPE,
				"room": room,
				"job_seq": allocate_job_seq(room),
				"operation": operation,
				"status": states.RelayState.PENDING.value,
				"available_at": available_at or now_datetime(),
				"attempts": 0,
				"reference_doctype": reference_doctype or None,
				"reference_name": reference_name or None,
				"request_id": request_id or None,
				"impersonate_user": impersonate_user or None,
				"auth_identity": identity,
				"payload": body,
			}
		)
		failure = _insert_catching_duplicates(job, ignore_permissions=True)
		if failure is None:
			if enqueue:
				enqueue_relay_job(job.name)
			return job.name
		if not _names_constraint(failure, _JOB_SEQ_CONSTRAINT):
			raise failure
		last = failure

	raise last if last else RuntimeError("create_relay_job exhausted its attempts without an error")


def enqueue_relay_job(job: str) -> None:
	"""Hand one outbox row to a background worker, **after the transaction commits**.

	``enqueue_after_commit=True`` is not optional. Frappe registers the enqueue on
	``frappe.db.after_commit``; without it the worker can ``get_doc`` the row before the
	insert lands and see nothing, which is an intermittent "job not found" that only
	reproduces under load.

	``deduplicate=True`` **requires** ``job_id`` and throws without one. It also skips a new
	enqueue while an existing job for the same id is ``QUEUED`` *or* ``STARTED``, so a
	sweeper re-enqueue during an in-flight attempt is silently dropped — which is correct
	here and only because the sweeper, not the queue, is the delivery guarantee. Worst case
	is one sweep interval of delay.

	Never raises. The row is committed and the sweeper will find it; a Redis blip must not
	roll back the message that produced it.
	"""
	try:
		frappe.enqueue(
			RELAY_WORKER_PATH,
			queue=RELAY_QUEUE,
			timeout=RELAY_JOB_TIMEOUT,
			enqueue_after_commit=True,
			job_id=relay_job_id(job),
			deduplicate=True,
			# `job`, and it MUST NOT be `job_name`: that is one of frappe.enqueue's own
			# parameters, so a kwarg by that name is eaten by the enqueue machinery and the
			# worker is handed nothing. v1.277.13 renamed this to `job_name` to match the
			# worker and traded "unexpected keyword argument" for "missing 1 required
			# positional argument" — the worker was the side that had drifted.
			job=job,
		)
	except Exception as exc:
		frappe.log_error(
			title="chat outbox: relay enqueue failed",
			message=f"{type(exc).__name__} job={job} (row is committed; the sweeper will pick it up)",
		)


def relay_job_id(job: str) -> str:
	"""The RQ ``job_id`` for one outbox row. One place, so the sweeper dedupes against it."""
	return f"chat-relay::{job}"
