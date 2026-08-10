# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The only realtime publish in the chat package — **a security module, not a wrapper**.

Nothing here is convenience. Every line exists because ``frappe.publish_realtime`` fails
*open*: when it cannot work out where to send something it sends it **everywhere**, and it
does that silently, at runtime, in exactly the code path — a background relay worker — that
nobody is watching. Three separate mechanisms inside one function can steer a chat message
away from the room it was written for, and all three were read out of Frappe's own
``frappe/realtime.py`` rather than inferred:

**Trap 1 — the final fallback is a site-wide broadcast.** The bottom of the targeting chain
is ``room = get_site_room()``, which returns the literal ``"all"``. Every logged-in System
User is already sitting in that room. So a publish that merely *forgets* an argument does
not fail, log, or drop; it hands the payload to every connected session on the site.

**Trap 2 — ``task_id`` outranks ``user=``, and it is seeded implicitly.** Before the chain
runs, ``publish_realtime`` does ``if not task_id and hasattr(frappe.local, "task_id"):
task_id = frappe.local.task_id``. Inside a background job that attribute is set, so a call
that carefully passes ``user=`` or ``doctype=``/``docname=`` and no ``room=`` is retargeted
to ``task_progress:<task_id>`` — a room any client may join, with **no permission check at
all**. The chat relay runs in background jobs. This is the live one, not the theoretical one.

**Trap 3 — two event names overwrite an explicitly passed ``room=``.** ``list_update`` and
``docinfo_update`` are special-cased *above* the ``if not room:`` guard and assign ``room``
unconditionally, so the one call you targeted most carefully is the one that escapes. These
two names are **refused with a ``ValueError``** rather than documented as a hazard: a
comment does not survive the afternoon somebody reuses a name that "already works" in the
desk.

--------------------------------------------------------------------------------------
Why the room is computed here and passed explicitly
--------------------------------------------------------------------------------------

Passing ``room=`` is the only thing that short-circuits the whole chain: with a truthy
``room`` the ``if not room:`` block — task_id, user, doc, site-wide, in that order — never
executes. Passing ``doctype=``/``docname=`` alone does **not** do that; trap 2 outranks it.

So both are passed. ``room`` is the real targeting; ``doctype``/``docname`` state the intent
for a reader and stand as a fallback that lands in the same place if the explicit room ever
came back empty. The room string itself comes from ``frappe.realtime.get_doc_room`` rather
than an f-string in this file: the socket server builds the same name on the Node side, and
a hand-rolled copy is a silent no-delivery bug the day either side changes shape.

``after_commit`` is honoured either way — Frappe queues on the *resolved* room, after the
chain, so an explicit room does not bypass it. (The one exception is the ``task_id`` branch,
which force-clears ``after_commit``; another reason never to reach it.)

--------------------------------------------------------------------------------------
Document rooms are permission-checked. User rooms are not. That is the whole split.
--------------------------------------------------------------------------------------

Invariant I8: joining ``doc:Chat Room/<room>`` makes the socket server call back into
``/api/method/frappe.realtime.has_permission`` → the full Python permission stack →
``chat_room_has_permission`` in :mod:`erpnext_enhancements.chat.permissions`, under the
joining user's own session, *before* the join is granted. That callback is the only reason
realtime content is safe at all, and it is why this helper takes a **``Chat Room`` name**
and never a raw room string: a raw string is a way to name a room the permission stack was
never asked about.

User rooms (``user:<email>``) get **no** such check — they are joined server-side from the
already-authenticated identity, so they cannot be joined by the wrong person, but nothing
inspects what is put in them. That is fine for a user's own unread count and never fine for
content, which is why :func:`publish_user_counter` is a separate function with a separate
docstring and actively refuses content-shaped payloads.

--------------------------------------------------------------------------------------
Payloads carry identifiers, not content
--------------------------------------------------------------------------------------

Realtime payloads go through Redis pub/sub and fan out to every socket in the room. The
consumer re-reads the database anyway — which is also why every publish is
``after_commit=True``: a client that reacts to the event before the transaction lands reads
the row that is not there yet, and that is a "message vanished" bug that only reproduces
under load. So the payload is a set of identifiers and the client fetches. Both functions
cap the serialised size for that reason, and neither ever logs a payload.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

import frappe

# Imported rather than re-derived. These two build the room names the Node socket server
# builds independently on its own side; if the shape ever changes, importing means we change
# with it and hand-rolling means we silently publish into a room nobody is in.
from frappe.realtime import get_doc_room, get_user_room

#: The DocType whose *document room* carries chat content. One constant, because the room
#: name, the permission hook and the socket join must all agree on the same spelling.
ROOM_DOCTYPE: Final[str] = "Chat Room"

#: The three message events. Declared here so no caller spells one.
EVENT_MESSAGE_CREATED: Final[str] = "chat_message_created"
EVENT_MESSAGE_EDITED: Final[str] = "chat_message_edited"
EVENT_MESSAGE_DELETED: Final[str] = "chat_message_deleted"

#: Phase 3's additions, and which room each one belongs in. The split is invariant I8 and it
#: is what makes socket security free: a **document room** is permission-checked when a
#: client joins it (the socket server calls back into ``chat_room_has_permission``), a
#: **user room** is not joinable by anybody else but is not scoped to a conversation either.
#: So content and content-adjacent state go to the doc room, and counters go to the user
#: room — never the other way round.
#:
#: Document room (``publish_room_event``):
EVENT_TYPING: Final[str] = "chat_typing"
EVENT_TYPING_STOPPED: Final[str] = "chat_typing_stopped"
EVENT_PRESENCE: Final[str] = "chat_presence"
EVENT_READ_RECEIPT: Final[str] = "chat_read_receipt"
EVENT_ROOM_UPDATED: Final[str] = "chat_room_updated"
#: User room (``publish_user_counter``):
EVENT_UNREAD_UPDATED: Final[str] = "chat_unread_updated"
EVENT_MENTION: Final[str] = "chat_mention"

#: Every event name this package may publish, as one set. The SPA generates its handler map
#: from the same list (``public/js/chat/events.js``) so a name added on one side and not the
#: other is visible rather than silent — a client listening for an event nobody publishes is
#: indistinguishable, from the client's side, from a quiet room.
#:
#: The prompt's §4.5 table spells these ``chat:message_created``. The repo shipped
#: ``chat_message_created`` in Phase 1 and Phase 2 publishes it, so the underscore form is
#: what exists and the colon form would be a silent break of the sync engine's own events.
#: ``_EVENT_PREFIXES`` accepts both; ``chat/README.md`` records the divergence.
ALL_EVENTS: Final[frozenset[str]] = frozenset(
	{
		EVENT_MESSAGE_CREATED,
		EVENT_MESSAGE_EDITED,
		EVENT_MESSAGE_DELETED,
		EVENT_TYPING,
		EVENT_TYPING_STOPPED,
		EVENT_PRESENCE,
		EVENT_READ_RECEIPT,
		EVENT_ROOM_UPDATED,
		EVENT_UNREAD_UPDATED,
		EVENT_MENTION,
	}
)

#: Trap 3. Both names are special-cased inside ``publish_realtime`` and **overwrite an
#: explicitly passed** ``room=`` before the ``if not room:`` guard runs. Refused, not
#: documented — see the module docstring. ``tests/test_chat_guardrails.py`` carries the
#: same pair as a source-level lint; this is the runtime half of the same rule.
FORBIDDEN_EVENTS: Final[frozenset[str]] = frozenset({"list_update", "docinfo_update"})

#: Every chat event name must start with one of these. This is cheaper and more durable
#: than a denylist of Frappe's own event names (``msgprint``, ``task_progress``,
#: ``global``, ``progress_update``, …): a prefix rule cannot go stale when the framework
#: adds a fourth special case, and a denylist can.
_EVENT_PREFIXES: Final[tuple[str, ...]] = ("chat_", "chat:")

#: Serialised-payload ceilings, in bytes. Not a Google limit — a fan-out limit. Every byte
#: here is copied to every socket in the room through Redis pub/sub, and the consumer is
#: going to re-read the row regardless, so a large payload buys nothing and costs the
#: whole room. The counter ceiling is far tighter because a counter is a number.
MAX_ROOM_PAYLOAD_BYTES: Final[int] = 4096
MAX_COUNTER_PAYLOAD_BYTES: Final[int] = 512

#: Payload keys that must never appear on a **user-room** publish. User rooms are not
#: permission-checked on join, so anything routed there is trusted to the addressing logic
#: alone; content does not belong in that trust model. Identifier-shaped keys (``message``,
#: ``room``, ``seq``) are deliberately absent — those are exactly what a counter payload is
#: allowed to carry so the client knows what to re-fetch.
_CONTENT_KEYS: Final[frozenset[str]] = frozenset(
	{
		"attachment",
		"attachments",
		"body",
		"content",
		"excerpt",
		"file",
		"html",
		"markdown",
		"preview",
		"snippet",
		"text",
		"text_plain",
	}
)


def _validate_event(event: str) -> str:
	"""Reject the two reserved names and anything not obviously ours.

	The reserved-name check runs first and has its own message, because the failure it
	prevents is invisible: the publish succeeds, into the wrong room, with no error.
	"""
	name = (event or "").strip()
	if not name:
		raise ValueError("a chat realtime event needs a name; an empty one becomes Frappe's 'global'")
	if name in FORBIDDEN_EVENTS:
		raise ValueError(
			f"{name!r} is a reserved Frappe realtime event name and may never be used by the chat "
			"package. Frappe special-cases it and UNCONDITIONALLY overwrites an explicitly passed "
			"room= before the `if not room:` guard runs, so the targeting this module exists to "
			f"guarantee is discarded. Name the event chat_<something> instead. (Reserved: "
			f"{sorted(FORBIDDEN_EVENTS)}.)"
		)
	if not name.startswith(_EVENT_PREFIXES):
		raise ValueError(
			f"chat realtime event {name!r} must start with one of {list(_EVENT_PREFIXES)}. The "
			"prefix keeps chat off Frappe's own event names (msgprint, task_progress, global, "
			"progress_update), each of which has bespoke behaviour inside publish_realtime that "
			"a chat publish would inherit by accident."
		)
	return name


def _validate_payload(payload: Mapping[str, Any] | None, *, limit: int, where: str) -> dict[str, Any]:
	"""Copy the payload, refuse bytes, and cap the serialised size.

	The copy is not hygiene: the caller's dict is frequently a document field bag that keeps
	mutating after the publish is queued, and ``after_commit=True`` means Frappe serialises
	it *later*. Publishing a live reference is how a payload changes between the call and
	the wire.
	"""
	if payload is None:
		payload = {}
	if not isinstance(payload, Mapping):
		raise TypeError(f"{where}: payload must be a mapping, got {type(payload).__name__}")

	copied: dict[str, Any] = dict(payload)

	for key, value in copied.items():
		if isinstance(value, bytes | bytearray | memoryview):
			raise ValueError(
				f"{where}: payload key {key!r} carries raw bytes. Realtime payloads fan out to "
				"every socket in the room through Redis; file content is fetched over HTTP from "
				"/private/files, which delegates to the attached document's permissions."
			)

	try:
		blob = json.dumps(copied, default=str, separators=(",", ":"))
	except (TypeError, ValueError) as exc:
		raise ValueError(f"{where}: payload is not JSON-serialisable") from exc

	size = len(blob.encode("utf-8"))
	if size > limit:
		# The payload itself is never included in the message — it may contain a body.
		raise ValueError(
			f"{where}: payload serialises to {size} bytes, over the {limit}-byte ceiling. "
			"Realtime payloads carry identifiers and let the client fetch; the consumer re-reads "
			"the row anyway, so a large payload costs every socket in the room and buys nothing."
		)
	return copied


def publish_room_event(room: str, event: str, payload: Mapping[str, Any] | None = None) -> None:
	"""Publish chat content to one room's **document room**. The only content publish there is.

	``room`` is a ``Chat Room`` **name**, never a socket room string. The distinction is the
	security property: a document room is permission-checked when a client joins it (the
	socket server calls back into ``chat_room_has_permission``; invariant I8), and a raw room
	string is a way to name a room that check was never asked about. There is deliberately no
	parameter through which a caller can supply one — passing a room-shaped string here just
	produces the document room of a ``Chat Room`` that does not exist, which delivers to
	nobody. Failing closed is the correct direction for this mistake.

	What is passed to Frappe, and why each part:

	* ``room=get_doc_room(...)`` — the *only* thing that short-circuits the targeting chain.
	  With a truthy room, the ``task_id`` → ``user`` → ``doc`` → site-wide fallback never
	  runs, so traps 1 and 2 are unreachable rather than merely unlikely.
	* ``doctype``/``docname`` — intent for the reader, and a fallback that resolves to the
	  same room if the explicit one were ever empty.
	* ``after_commit=True`` — always. The consumer immediately re-reads the database, so an
	  event that beats its own transaction points at a row that does not exist yet.
	* **no** ``task_id`` — never, under any circumstance. Passing one would opt into the
	  branch that both retargets to an unauthenticated room *and* force-clears
	  ``after_commit``.

	Raises ``ValueError`` on an empty room, a reserved or unprefixed event name, or an
	oversized payload. It raises rather than returning quietly because a realtime publish
	that silently does nothing is indistinguishable from one that worked.
	"""
	name = (room or "").strip()
	if not name:
		raise ValueError(
			"publish_room_event needs a Chat Room name. Without one the doc room cannot be "
			"built and Frappe's fallback chain ends at the site-wide room."
		)

	event_name = _validate_event(event)
	message = _validate_payload(payload, limit=MAX_ROOM_PAYLOAD_BYTES, where="publish_room_event")

	doc_room = get_doc_room(ROOM_DOCTYPE, name)
	if not doc_room:
		# Cannot happen with the current helper, and that is the point: if a future Frappe
		# returns an empty string here, an empty room= falls straight through to "all".
		raise ValueError(
			f"frappe.realtime.get_doc_room({ROOM_DOCTYPE!r}, {name!r}) returned nothing. Refusing "
			"to publish: an empty room= is what Frappe's site-wide broadcast fallback looks like."
		)

	frappe.publish_realtime(
		event=event_name,
		message=message,
		room=doc_room,
		doctype=ROOM_DOCTYPE,
		docname=name,
		after_commit=True,
	)


def publish_user_counter(user: str, event: str, payload: Mapping[str, Any] | None = None) -> None:
	"""Publish a **counter** to one user's own room. Counters only — never content.

	A user room is not permission-checked in the way a document room is. It cannot be joined
	by the wrong person (the socket server joins it server-side from the authenticated
	session; there is no ``user_subscribe`` verb a client can call), but nothing inspects
	what is placed in it, and the room is not scoped to a conversation. So it is exactly
	right for "you have 3 unread" and exactly wrong for what those 3 say — a user's unread
	badge spans rooms, which is precisely why it cannot be a document-room publish.

	Content-shaped keys are refused rather than trusted to review, and the size ceiling is
	tight, because the whole legitimate payload is a handful of numbers and identifiers.

	Same targeting discipline as :func:`publish_room_event`: an explicit ``room=`` computed
	by ``get_user_room``, never a bare ``user=`` on its own — inside a background job a bare
	``user=`` loses to ``frappe.local.task_id`` and lands in an unauthenticated
	task-progress room. ``user=`` is passed alongside for the reader; Frappe ignores it once
	``room`` is set.
	"""
	who = (user or "").strip()
	if not who:
		raise ValueError(
			"publish_user_counter needs a user. Without one the user room cannot be built and "
			"Frappe's fallback chain ends at the site-wide room."
		)

	event_name = _validate_event(event)
	message = _validate_payload(payload, limit=MAX_COUNTER_PAYLOAD_BYTES, where="publish_user_counter")

	offenders = sorted(set(message) & _CONTENT_KEYS)
	if offenders:
		raise ValueError(
			f"publish_user_counter refuses payload key(s) {offenders}: a user room is not "
			"permission-checked on join the way a document room is, so it carries counters and "
			"identifiers only. Publish the content to the room's document room with "
			"publish_room_event and let the client fetch."
		)

	user_room = get_user_room(who)
	if not user_room:
		raise ValueError(
			f"frappe.realtime.get_user_room({who!r}) returned nothing. Refusing to publish: an "
			"empty room= is what Frappe's site-wide broadcast fallback looks like."
		)

	frappe.publish_realtime(
		event=event_name,
		message=message,
		room=user_room,
		user=who,
		after_commit=True,
	)
