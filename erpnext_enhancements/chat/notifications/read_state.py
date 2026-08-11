# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Reading in one place clears it everywhere — all four steps, in one function.

When somebody reads a room, four things have to happen, and **there is exactly one function
allowed to make them happen** so that no entry point can do three of them:

1. **Persist** the high-water mark — ``Chat Room Member.last_read_seq``, monotonic.
2. **Clear the bell** — mark the matching ``Notification Log`` rows read.
3. **Publish** ``chat_notification_state`` on that person's own realtime room, so every other
   tab and the floating bubble converge without a refresh.
4. **Close the operating-system notification** — a data push telling the service worker to
   call ``getNotifications({tag})`` and close them.

**Step 4 is the one everybody forgets**, and it is what makes dismissal work in both
directions. Without it, reading a message on a phone leaves the banner sitting on the desktop
until somebody swipes it away by hand — which reads as "the notifications are stuck" and is
the most-reported symptom of an otherwise working system. The reverse direction is the same
function entered from a different door: clicking or dismissing the banner posts the read
back, which runs steps 1–3.

--------------------------------------------------------------------------------------
The badge reconciles wholesale. It is never merged from increments.
--------------------------------------------------------------------------------------

``chat/api/readstate.get_unread_state`` is the authority and the client calls it on connect,
on reconnect, on becoming visible after a while, and on load. The realtime events are an
**accelerator**, never a source of truth.

The reason is not tidiness. Realtime is fire-and-forget: an event published to a client that
happens to be disconnected is simply gone. A client that merges deltas is therefore wrong by
exactly the number of events it missed — permanently, because nothing ever corrects it — and
the visible form is a bubble showing five over rooms that sum to three. That is the bug
people screenshot.

--------------------------------------------------------------------------------------
Why the counter and the badge count different things
--------------------------------------------------------------------------------------

The badge counts **messages** and the notifications are **coalesced per room**, so twenty
messages while away is a badge of twenty, one bell row and at most two banners. Those numbers
disagreeing is correct and is worth stating, because it looks like a bug in every review: a
count answers "how much have I missed", and a notification answers "does something want me".

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint

from erpnext_enhancements.chat import realtime
from erpnext_enhancements.chat.notifications import bell


def mark_room_read(
	room: str, *, user: str, up_to_seq: Any, source: str = "app"
) -> dict[str, Any]:
	"""Advance one person's read mark in one room, and clear every surface it covers.

	The single writer. ``chat/api/readstate.mark_read`` (the SPA's endpoint) and
	``chat/notifications/api.read_from_notification`` (the banner's) both come through here,
	so a read is the same event whichever surface produced it — which is the only way "reading
	on the phone clears the desktop" can be true rather than aspirational.

	``source`` is carried into the realtime payload so a client can tell its own read from one
	that arrived from another device, and skip re-rendering something it already did.

	Returns the new mark plus what was cleared, so the caller can answer the HTTP request
	without a second query.
	"""
	target = cint(up_to_seq)
	before = _mark(room, user)

	if target > before:
		from erpnext_enhancements.chat.doctype.chat_room_member.chat_room_member import (
			advance_read_mark,
		)

		advance_read_mark(room, user, target)

	after = _mark(room, user)
	changed = after > before

	# Steps 2-4 run even when the mark did not move. A repeated read is exactly what a second
	# device produces when two people's tabs both catch up, and the bell row or the banner may
	# still be sitting there from before — refusing to clear them because the integer was
	# already correct is how a notification becomes immortal.
	cleared = clear_room_notifications(user, room, last_read_seq=after, source=source)

	return {"room": room, "last_read_seq": after, "changed": changed, **cleared}


def clear_room_notifications(
	user: str, room: str, *, last_read_seq: int, source: str = "app"
) -> dict[str, Any]:
	"""Steps 2, 3 and 4. Separated so a reconciliation can run them without moving a mark."""
	email = bell.resolve_email(user)

	rows = bell.mark_rows_read(email, "Chat Room", [room])
	rows += _clear_mention_rows(user, email, room, last_read_seq)

	_publish_state(user, room, last_read_seq=last_read_seq, cleared=rows, source=source)
	_close_os_notifications(user, room)

	return {"cleared_notifications": rows}


def _clear_mention_rows(user: str, email: str, room: str, last_read_seq: int) -> list[str]:
	"""Mention rows are per message, so they clear by ``seq`` rather than by room.

	A mention notification for a message the reader has not reached yet must **survive**:
	reading the first ten messages of a room does not mean they have seen the mention at
	message twenty, and clearing it early would lose the one notification people escalate
	about.

	Raw SQL with :func:`permissions.membership_filter_sql` ANDed in, rather than
	``frappe.get_all``. The caller has already been through ``require_room``, so this is belt
	and braces — but ``frappe.get_all`` is ``get_list(ignore_permissions=True)`` and sees no
	permission layer at all, and the rule in this package is that a query against a table
	carrying conversation carries its own filter regardless of what the caller checked. Two
	implementations of one permission rule drift; a drift here is a leak.

	``user`` is the ``User`` docname the membership rows are keyed on; ``email`` is what
	``Notification Log.for_user`` holds. They are the same string on this site today and are
	one admin decision away from not being.
	"""
	if last_read_seq < 1:
		return []

	from erpnext_enhancements.chat import permissions

	scope = permissions.membership_filter_sql("`m`.`room`", user, seq_column="`m`.`seq`")
	try:
		rows = frappe.db.sql(
			f"""select `m`.`name` from `tabChat Message` `m`
				where `m`.`room` = %(room)s and `m`.`seq` <= %(seq)s and {scope}
				order by `m`.`seq` desc
				limit 500""",
			{"room": room, "seq": cint(last_read_seq)},
			as_dict=True,
		)
	except Exception:
		return []

	messages = [row["name"] for row in rows if row.get("name")]
	if not messages:
		return []
	return bell.mark_rows_read(email, "Chat Message", messages)


def _publish_state(
	user: str, room: str, *, last_read_seq: int, cleared: list[str], source: str
) -> None:
	"""Step 3. ``chat_notification_state`` on the reader's own user room.

	Counters and identifiers only — the user room is not permission-checked on join the way a
	document room is, so it carries numbers and names of rows, never content. The cleared list
	is capped because the payload has a 512-byte ceiling and a mark-all-read over a busy month
	could name hundreds of rows; a client that receives a truncated list falls back to
	refetching, which is one round trip and always correct.
	"""
	payload: dict[str, Any] = {
		"room": room,
		"last_read_seq": cint(last_read_seq),
		"source": source,
	}
	if cleared:
		payload["cleared"] = cleared[:8]
		payload["cleared_count"] = len(cleared)
	try:
		realtime.publish_user_counter(user, realtime.EVENT_NOTIFICATION_STATE, payload)
	except Exception:
		frappe.log_error(title="chat notification state publish failed", message=f"user={user}")


def _close_os_notifications(user: str, room: str) -> None:
	"""Step 4. The data push that closes the banner on every other device.

	Best-effort by construction and deliberately so: it is called from a request path, the
	push service is somebody else's infrastructure, and a person who has just read a message
	must not wait on it. A failure here leaves one stale banner, which the next read or a
	manual dismissal clears.
	"""
	try:
		from erpnext_enhancements.chat.notifications.webpush import sender, subscriptions

		payload = {"kind": "chat-clear", "tag": sender.room_tag(room), "room": room}
		for subscription in subscriptions.active_for(user):
			sender.send_one(subscription, payload)
	except Exception:
		frappe.logger("chat").debug("chat could not close OS notifications user=%s room=%s", user, room)


def _mark(room: str, user: str) -> int:
	"""The stored high-water mark, as an integer."""
	try:
		return cint(
			frappe.db.get_value("Chat Room Member", {"room": room, "user": user}, "last_read_seq")
		)
	except Exception:
		return 0
