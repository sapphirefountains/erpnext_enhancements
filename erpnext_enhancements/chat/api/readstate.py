# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Read receipts, unread counts, and the badge fan-out.

**Read state is a high-water mark, not a row per message.** ``Chat Room Member.last_read_seq``
is one integer per (user, room), and "user X has read message Y" is *derived* —
``Y.seq <= member.last_read_seq``. Per-message receipt rows would be roughly an order of
magnitude more writes than messages themselves (every member × every message), which is the
dominant scaling risk in the whole feature. Phase 1 permits materialising them for DMs and
small rooms if the UI genuinely needs per-message avatars; it does not, because the
derivation gives the same answer from data already loaded with the roster.

**Monotonic, and enforced by the statement.** ``advance_read_mark`` is a single conditional
``UPDATE`` with ``where coalesce(last_read_seq, 0) < :seq``. A read-modify-write between two
tabs lets the lower number win and silently un-reads messages the user has already seen; the
comparison belongs in the statement, where the database serialises it for free. An
out-of-order flush from a slow tab is therefore a no-op rather than a regression, and the
server never has to trust the client's ordering.

**The badge reconciles wholesale.** :func:`get_unread_state` is the authoritative count and
the SPA calls it on connect and on every reconnect. The realtime counter published by
:func:`announce_unread` is an accelerator carrying one room's new totals — never a delta the
client merges. A merged count drifts after any period offline and never self-corrects.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint

from erpnext_enhancements.chat import permissions, realtime
from erpnext_enhancements.chat.api._common import require_room, require_session


@frappe.whitelist()
def mark_read(room: str, up_to_seq: Any) -> dict[str, Any]:
	"""Advance the caller's read mark in one room. Never moves it backwards.

	The client batches to at most one call per 2 s and flushes immediately on room switch,
	window blur, tab hide and ``pagehide``; this endpoint is deliberately cheap enough that
	the batching is an optimisation rather than a requirement.

	Returns the mark that is now stored — which may be **higher** than the one requested,
	when another tab of the same user got there first. The client keeps its own monotonic
	value and takes the max, so a slow tab's stale flush cannot pull the UI backwards either.
	"""
	user, name = require_room(room)
	target = cint(up_to_seq)
	if target < 1:
		return {"room": name, "last_read_seq": _current_mark(name, user), "changed": False}

	# Delegated to Phase 4's single writer rather than advancing the mark here. A read has to
	# do four things — persist the mark, clear the bell rows, tell this person's other tabs,
	# and close the operating-system banner on their other devices — and the whole point of one
	# writer is that no entry point can do three of them. This endpoint and the one a tapped
	# notification calls are two doors into the same room.
	from erpnext_enhancements.chat.notifications import read_state

	result = read_state.mark_room_read(name, user=user, up_to_seq=target, source="app")
	after = cint(result.get("last_read_seq"))

	if result.get("changed"):
		# The room's members see the receipt; the reader's own other tabs and their floating
		# bubble see the counter. Two rooms, two payload shapes, exactly as invariant I8 wants:
		# content-adjacent state to the permission-checked doc room, counters to the user room.
		_publish_receipt(name, user, after)
		_publish_user_counter(user, name)

	return {
		"room": name,
		"last_read_seq": after,
		"changed": bool(result.get("changed")),
		"cleared_notifications": result.get("cleared_notifications") or [],
	}


@frappe.whitelist()
def mark_all_read() -> dict[str, Any]:
	"""Advance every room's mark to its tail. The "clear the badge" affordance.

	One statement per room rather than one bulk ``UPDATE … JOIN``, because
	``advance_read_mark`` is the only place the monotonic rule is written and a second bulk
	implementation of it is a second place for it to be wrong.
	"""
	user = require_session()
	rooms = _unread_rows(user)

	from erpnext_enhancements.chat.doctype.chat_room_member.chat_room_member import advance_read_mark

	for row in rooms:
		if cint(row["unread"]) > 0:
			advance_read_mark(row["room"], user, cint(row["seq_high_water"]))

	state = get_unread_state()
	_publish_user_counter(user, None, state=state)
	return state


@frappe.whitelist()
def get_unread_state() -> dict[str, Any]:
	"""The authoritative wholesale unread count. Called on connect and every reconnect.

	Wholesale rather than incremental on purpose: realtime is fire-and-forget and not
	durable, so a client that merges deltas is a client whose badge is wrong by exactly the
	number of events that arrived while it was disconnected — permanently, because nothing
	ever corrects it.
	"""
	user = require_session()
	rows = _unread_rows(user)
	mentions = _mention_counts(user)

	rooms = [
		{
			"room": row["room"],
			"unread": cint(row["unread"]),
			"mentions": cint(mentions.get(row["room"], 0)),
		}
		for row in rows
		if cint(row["unread"]) > 0 or mentions.get(row["room"])
	]

	return {
		"rooms": rooms,
		"total_unread": sum(room["unread"] for room in rooms),
		"total_mentions": sum(room["mentions"] for room in rooms),
	}


@frappe.whitelist()
def get_read_marks(room: str) -> list[dict[str, Any]]:
	"""Every member's read high-water mark in one room — what per-message receipts render from.

	Refreshed on ``chat_read_receipt`` and re-fetched wholesale on reconnect, same as the
	counters. Small by construction: one row per member, not per message.
	"""
	_user, name = require_room(room)
	scope = permissions.membership_filter_sql("`m`.`room`")
	rows = frappe.db.sql(
		f"""select `m`.`user`, `m`.`last_read_seq`, `m`.`last_read_at`
			from `tabChat Room Member` `m`
			where `m`.`room` = %(room)s and `m`.`is_active` = 1 and {scope}""",
		{"room": name},
		as_dict=True,
	)
	return [
		{
			"user": row["user"],
			"last_read_seq": cint(row.get("last_read_seq")),
			"last_read_at": row.get("last_read_at") and str(row["last_read_at"]) or None,
		}
		for row in rows
	]


# --------------------------------------------------------------------------- fan-out


def announce_unread(doc: Any, method: str | None = None) -> None:
	"""``Chat Message.after_insert``, registered by Phase 3 in ``hooks.py``.

	Publishes each *other* member's new totals to their own user room, which is what drives
	the room-list indicator and the floating bubble's badge on a Desk page where no room's
	document room has been joined.

	Deliberately a **separate hook** rather than a line inside ``outbox.finalise_new_message``.
	Phase 2's soak asserts "exactly once per genuinely new message, zero for echoes" against
	that function; editing it to add a counter fan-out would put a Phase 3 concern inside the
	proof's subject. Additive wiring keeps the proof measuring what it was written to measure.

	**Never raises.** It runs inside the inserting transaction's ``after_insert``, where an
	exception destroys the message it exists to announce. A missing badge costs one refresh.
	"""
	try:
		if cint(getattr(doc, "is_deleted", 0)):
			# A row that is born deleted announces nothing — same rule, and the same reason, as
			# ``finalise_new_message``'s suppression of ``chat_message_created``.
			return
		room = (getattr(doc, "room", None) or "").strip()
		if not room:
			return
		if getattr(frappe.flags, "in_install", False) or getattr(frappe.flags, "in_migrate", False):
			return

		sender = (getattr(doc, "sender", None) or "").strip()
		mentioned = {
			(row.get("user") or "").strip()
			for row in (getattr(doc, "mentions", None) or [])
			if (getattr(row, "user", None) if not isinstance(row, dict) else row.get("user"))
		}

		for member in _room_members(room):
			if member == sender:
				# The author's own read mark was already advanced by the insert, so their
				# unread for this room is zero and publishing it would be a no-op event.
				continue
			_publish_user_counter(member, room, mention=member in mentioned)
	except Exception:
		frappe.log_error(
			title="chat unread fan-out failed",
			message=f"message={getattr(doc, 'name', '?')} room={getattr(doc, 'room', '?')}",
		)


def _publish_receipt(room: str, user: str, seq: int) -> None:
	"""``chat_read_receipt`` to the room's document room. A high-water mark, not per message."""
	try:
		realtime.publish_room_event(
			room,
			realtime.EVENT_READ_RECEIPT,
			{"room": room, "user": user, "last_read_seq": cint(seq)},
		)
	except Exception:
		frappe.log_error(title="chat read receipt publish failed", message=f"room={room} user={user}")


def _publish_user_counter(
	user: str, room: str | None, *, mention: bool = False, state: dict[str, Any] | None = None
) -> None:
	"""``chat_unread_updated`` to one user's own room. Counters only, and small.

	``publish_user_counter`` caps the payload at 512 bytes and refuses content-shaped keys,
	so this cannot carry the room list — and must not. The wholesale list is
	:func:`get_unread_state`, fetched over HTTP; this event tells the client *which* room
	changed and what its totals now are, so a badge update costs no round trip in the common
	case and a reconnect costs exactly one.
	"""
	payload: dict[str, Any] = {"mention": 1 if mention else 0}
	if room:
		payload["room"] = room
		payload["unread"] = _unread_for(room, user)
	else:
		# No room means the change was not local to one — `mark_all_read` is the only caller
		# that does this, and it can move every counter at once. Both clients key their badge
		# update on `payload.room`, so a roomless event used to reach them, resolve to no room,
		# and no-op: every other tab kept a stale badge until a full page reload. The payload
		# cannot carry the new list (512-byte cap, and it must not carry content), so it says
		# so instead and the client refetches. One extra round trip, once, per mark-all-read.
		payload["reconcile"] = 1
	if state is not None:
		payload["total_unread"] = cint(state.get("total_unread"))
		payload["total_mentions"] = cint(state.get("total_mentions"))

	try:
		realtime.publish_user_counter(user, realtime.EVENT_UNREAD_UPDATED, payload)
	except Exception:
		frappe.log_error(title="chat unread publish failed", message=f"user={user} room={room}")


# --------------------------------------------------------------------------- queries


def _current_mark(room: str, user: str) -> int:
	"""One integer. ``get_value`` rather than a raw query — no SQL, so no filter to apply."""
	return cint(
		frappe.db.get_value("Chat Room Member", {"room": room, "user": user}, "last_read_seq")
	)


def _unread_for(room: str, user: str) -> int:
	"""``seq_high_water - last_read_seq`` for one (room, user). Two ``get_value`` reads."""
	high = cint(frappe.db.get_value("Chat Room", room, "seq_high_water"))
	read = _current_mark(room, user)
	return max(0, high - read)


def _unread_rows(user: str) -> list[dict[str, Any]]:
	"""Per-room unread arithmetic for one user, in one statement.

	``seq`` is dense per room by construction — allocated one at a time under the room's row
	lock and never reused — so ``seq_high_water - last_read_seq`` **is** the count of unread
	messages and no ``COUNT(*)`` is needed. Deleted rows keep their ``seq``, so a room whose
	tail was deleted can read one high; that is a one-message overcount on a tombstoned row
	and it clears the moment the user opens the room. A join to count non-deleted rows would
	turn the cheapest query in the app into the most expensive one to save that.
	"""
	scope = permissions.membership_filter_sql("`r`.`name`", user)
	return frappe.db.sql(
		f"""select
				`r`.`name` as `room`,
				`r`.`seq_high_water`,
				greatest(0, coalesce(`r`.`seq_high_water`, 0) - coalesce(`m`.`last_read_seq`, 0))
					as `unread`
			from `tabChat Room` `r`
			join `tabChat Room Member` `m`
				on `m`.`room` = `r`.`name` and `m`.`user` = %(user)s and `m`.`is_active` = 1
			where coalesce(`r`.`is_archived`, 0) = 0 and {scope}""",
		{"user": user},
		as_dict=True,
	)


def _mention_counts(user: str) -> dict[str, int]:
	"""Unread mentions per room. A mention indicator is distinct from a plain unread (§4.4).

	Bounded by the read mark on both sides, so this scans only what the user has not seen —
	which is the property that keeps it cheap on a busy room and free on a quiet one.
	"""
	scope = permissions.membership_filter_sql("`msg`.`room`", user, seq_column="`msg`.`seq`")
	rows = frappe.db.sql(
		f"""select `msg`.`room`, count(distinct `msg`.`name`) as `mentions`
			from `tabChat Mention` `mention`
			join `tabChat Message` `msg` on `msg`.`name` = `mention`.`parent`
			join `tabChat Room Member` `me`
				on `me`.`room` = `msg`.`room` and `me`.`user` = %(user)s and `me`.`is_active` = 1
			where `mention`.`user` = %(user)s
				and `mention`.`parenttype` = 'Chat Message'
				and coalesce(`msg`.`is_deleted`, 0) = 0
				and `msg`.`seq` > coalesce(`me`.`last_read_seq`, 0)
				and {scope}
			group by `msg`.`room`""",
		{"user": user},
		as_dict=True,
	)
	return {row["room"]: cint(row["mentions"]) for row in rows}


def _room_members(room: str) -> list[str]:
	"""Active members of a room, for the counter fan-out.

	Called from ``after_insert``, where there is no session user to scope against — the
	writer may be the inbound sync worker relaying a coworker's message from Google. The
	membership filter is still ANDed in and still resolves correctly: for a background writer
	``frappe.session.user`` is ``Administrator``, which the filter answers with ``1 = 1``,
	and for a user-initiated send it is the sender, who is a member of the room they just
	posted in. Either way the row set is "the members of this room", which is what a fan-out
	needs and the only thing it gets.
	"""
	scope = permissions.membership_filter_sql("`m`.`room`")
	rows = frappe.db.sql(
		f"""select `m`.`user` from `tabChat Room Member` `m`
			where `m`.`room` = %(room)s and `m`.`is_active` = 1 and {scope}""",
		{"room": room},
		as_dict=True,
	)
	return [row["user"] for row in rows if row.get("user")]
