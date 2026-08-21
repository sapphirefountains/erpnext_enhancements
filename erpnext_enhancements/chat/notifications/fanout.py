# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One message, every member, one decision each — Phase 2's seam, finally implemented.

Phase 2 wired :func:`chat.seams.notify_new_message` at every path that creates a genuinely
new message, and proved with a counter that it fires **exactly once per new message and zero
times for echoes, replays, reconciliations and outbound relays**. That proof is what makes
this module safe to write: a bidirectional mirror's characteristic failure is a message
coming back from Google and being re-ingested, and the production symptom is a notification
storm rather than anything visible in a log. The counter is the thing that would have said
so before twenty phones buzzed twice.

**So this module is called from that seam and nowhere else**, and Phase 2's assertions are
left exactly as they are rather than being edited to accommodate it.

--------------------------------------------------------------------------------------
It runs in a background job, and what that costs
--------------------------------------------------------------------------------------

Deciding for every member means reading presence for every member, and then writing bell
rows and posting Web Push requests to an external service. None of that belongs inside the
``after_insert`` of the busiest table in the feature: a slow push endpoint would become a
slow *send*, and an exception would roll back the message it was supposed to announce.

The cost is stated rather than hidden: **the production deploy FLUSHDBs the queue Redis, so
a job enqueued in the seconds before a release is destroyed.** The message survives, because
it is committed. What is lost is one message's bell row and push. It is bounded and it
self-heals in two directions — the next message in that room writes the row, and the unread
badge is derived from the database on every reconnect rather than from these events — so the
lasting damage is one missed ping around a deploy. Closing it properly means a sweeper, and
that belongs with the outbox sweeper rather than bolted on here.

The **counter** deliberately does not ride this job. ``readstate.announce_unread`` is
already a separate ``after_insert`` hook publishing each member's new totals on their own
realtime room, and it is synchronous, cheap and durable in the only sense that matters (the
client reconciles wholesale on reconnect). Moving it here would put the room list's
responsiveness behind a queue for no benefit.

--------------------------------------------------------------------------------------
Every decision is logged with its reason, and that is the support tool
--------------------------------------------------------------------------------------

Nobody reports the notification they did not get. So every recipient's outcome is
debug-logged with the reason code that produced it — identifiers only, never a body — and
:mod:`~erpnext_enhancements.chat.notifications.debug` can replay the same decision for a
named person on demand. Without that, "why didn't Jane get this" is unanswerable, and the
answer people reach for instead is "notifications are broken", which is unfalsifiable.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint

from erpnext_enhancements.chat.notifications import bell, policy
from erpnext_enhancements.chat.notifications import presence as presence_store
from erpnext_enhancements.chat.notifications import settings as notification_settings

#: The queue the fan-out runs on. ``short`` because the unit of work is one message and the
#: expensive part is a handful of HTTPS POSTs with their own timeouts — putting it on
#: ``long`` would let a slow push service hold a worker that background jobs with real
#: deadlines are waiting for.
_QUEUE = "short"


def enqueue_fanout(message: str) -> None:
	"""Schedule the fan-out for one message. Called from the seam, never directly.

	``enqueue_after_commit=True`` as a **literal**, which the guardrail suite asserts by
	source inspection: a job that starts before its transaction lands reads a row that does
	not exist yet, and the symptom is a notification about a message nobody can open.
	"""
	name = (message or "").strip()
	if not name:
		return
	try:
		frappe.enqueue(
			"erpnext_enhancements.chat.notifications.fanout.run_fanout",
			queue=_QUEUE,
			enqueue_after_commit=True,
			message=name,
		)
	except Exception:
		# A queue that will not accept a job must not fail the message insert. The counter and
		# the room indicator still work; the bell and the push are what is lost.
		frappe.log_error(title="chat notification fan-out could not be queued", message=f"message={name}")


def run_fanout(message: str) -> dict[str, Any]:
	"""Decide and act for every member of the message's room. The job body.

	Returns a summary — counts and reason codes, never content — so that a manual
	``bench execute`` during an incident answers "what did it decide and for whom" without
	anybody having to read the code.
	"""
	summary: dict[str, Any] = {"message": message, "recipients": 0, "bell": 0, "push": 0, "reasons": {}}

	row = _message_row(message)
	if not row:
		return summary
	if cint(row.get("is_deleted")):
		# Born deleted, or deleted before the job ran. Announcing it would notify somebody
		# about a tombstone, and the same rule already governs the realtime publish.
		return summary

	room = (row.get("room") or "").strip()
	sender = (row.get("sender") or "").strip()
	if not room:
		return summary

	tuning = notification_settings.load()
	now = presence_store.now_epoch()
	room_label = _room_label(room)
	sender_label = _sender_label(row)
	mentioned = _mentioned_users(message)

	for member in _members(room):
		user = (member.get("user") or "").strip()
		if not user:
			continue

		recipient = policy.Recipient(
			is_author=bool(sender) and user == sender,
			is_mentioned=user in mentioned,
			is_muted=_is_muted(member),
			notifications_enabled=_notifications_enabled(user),
		)
		clients, store_available = presence_store.clients_for(
			user, now=now, ttl_seconds=tuning.presence_ttl_seconds
		)
		decision = policy.decide_for(
			recipient=recipient,
			clients=clients,
			room=room,
			now=now,
			policy=tuning,
			store_available=store_available,
		)

		summary["recipients"] += 1
		summary["reasons"][decision.reason.value] = summary["reasons"].get(decision.reason.value, 0) + 1
		_log(message, room, user, decision)

		if decision.auto_read:
			_auto_read(room, user, cint(row.get("seq")), skip=recipient.is_author)
			continue

		if decision.bell:
			wrote = (
				bell.notify_mention(
					user,
					room=room,
					room_label=room_label,
					message=message,
					sender_label=sender_label,
					sender_user=sender,
				)
				if decision.reason is policy.Reason.MENTION
				else bell.notify_room(
					user,
					room=room,
					room_label=room_label,
					sender_label=sender_label,
					sender_user=sender,
					message=message,
				)
			)
			if wrote:
				summary["bell"] += 1

		if decision.push and _send_push(user, room=room, room_label=room_label, message=message, sender_label=sender_label):
			summary["push"] += 1

	return summary


# --------------------------------------------------------------------------- inputs


def _message_row(message: str) -> dict[str, Any] | None:
	"""The message, read with ``get_value`` rather than ``get_doc``.

	``Chat Message`` ships zero DocPerm, so ``get_doc`` would run the permission stack and be
	refused before any hook is consulted — including for this job, which has no session user
	at all. The same reasoning, and the same workaround, as ``api/_common.require_message``.
	"""
	try:
		return frappe.db.get_value(
			"Chat Message",
			(message or "").strip(),
			["name", "room", "seq", "sender", "sender_email", "sender_kind", "is_deleted"],
			as_dict=True,
		)
	except Exception:
		return None


def _members(room: str) -> list[dict[str, Any]]:
	"""Active members of the room, with the two fields the mute rule needs.

	A **system-context roster read**, not a user-scoped one. The fan-out runs as a background
	job with no asking user (it may be the inbound-sync worker relaying a coworker's Google
	Chat message), and its job is to notify *everyone* in the room. The query is already bounded
	to one named room's active members — the complete, correct recipient set.

	It deliberately does NOT AND in ``membership_filter_sql``. That fragment answers "which
	rooms is the SESSION user in", and since v1.284.0/v1.301.0 it returns ``1 = 1`` only when
	``allow_oversight=True`` — for the job's ``Administrator`` session (a member of nothing) it
	resolves to an EXISTS that matches no room, so ANDing it silently yielded zero recipients
	and no ERPNext user was ever notified of a message that arrived from Google Chat. Registered
	in ``test_chat_rawsql_guard.SYSTEM_CONTEXT_READS``.
	"""
	try:
		return frappe.db.sql(
			"""select `m`.`user`, `m`.`notification_mode`, `m`.`muted_until`
				from `tabChat Room Member` `m`
				where `m`.`room` = %(room)s and `m`.`is_active` = 1""",
			{"room": room},
			as_dict=True,
		)
	except Exception:
		return []


def _mentioned_users(message: str) -> set[str]:
	"""Everybody this message names directly.

	A ``User`` mention only — a ``Triton`` mention names no ``User`` and must not make every
	member look mentioned, which is what reading the child rows without the type filter would
	do in a room where somebody asked Triton a question.
	"""
	try:
		rows = frappe.get_all(
			"Chat Mention",
			filters={"parent": message, "parenttype": "Chat Message", "mention_type": "User"},
			pluck="user",
		)
	except Exception:
		return set()
	return {(user or "").strip() for user in rows if (user or "").strip()}


def _is_muted(member: dict[str, Any]) -> bool:
	"""Whether this member has muted the room, from the two fields that can say so.

	``notification_mode`` is the durable choice; ``muted_until`` is the "mute for an hour"
	affordance and expires on its own. An expired ``muted_until`` is not a mute, and reading
	it as one would leave somebody silently muted long after they meant to be.
	"""
	if (member.get("notification_mode") or "All") in {"None", "Muted"}:
		return True
	until = member.get("muted_until")
	if not until:
		return False
	try:
		from frappe.utils import get_datetime, now_datetime

		return get_datetime(until) > now_datetime()
	except Exception:
		return False


def _notifications_enabled(user: str) -> bool:
	"""Frappe's own per-user kill switch, consulted so the reason code can be honest.

	The framework drops the row for these people regardless of what this module decides, so
	not checking would not send them anything — it would just mean the debug output claimed a
	bell row was written when none was. Defaults to True: an unreadable settings row must not
	silence somebody.
	"""
	try:
		from frappe.desk.doctype.notification_settings.notification_settings import (
			is_notifications_enabled,
		)

		return bool(is_notifications_enabled(bell.resolve_email(user)))
	except Exception:
		return True


# --------------------------------------------------------------------------- outputs


def _auto_read(room: str, user: str, seq: int, *, skip: bool) -> None:
	"""Advance the read mark for somebody demonstrably looking at the message.

	``skip`` is the author, whose mark the insert already advanced — calling again would be a
	no-op, but the no-op costs a statement per message per room and says something misleading
	in the log.

	Delegated to ``advance_read_mark`` rather than reimplemented: it is the only place the
	monotonic rule is written, and a second implementation is a second place for it to be
	wrong.
	"""
	if skip or seq < 1:
		return
	try:
		from erpnext_enhancements.chat.doctype.chat_room_member.chat_room_member import (
			advance_read_mark,
		)

		advance_read_mark(room, user, seq)
	except Exception:
		frappe.log_error(title="chat auto-read failed", message=f"room={room} user={user}")


def _send_push(
	user: str, *, room: str, room_label: str, message: str, sender_label: str
) -> bool:
	"""Hand off to Web Push. Isolated so a push outage cannot cost anybody their bell row.

	Imported inside the function on purpose: the push package reaches for ``cryptography``
	and ``requests``, and a module-scope import would drag both into every process that ever
	touches a chat message — including the ones with neither installed.
	"""
	try:
		from erpnext_enhancements.chat.notifications.webpush import sender

		return bool(
			sender.push_to_user(
				user,
				room=room,
				room_label=room_label,
				message=message,
				sender_label=sender_label,
			)
		)
	except Exception:
		frappe.log_error(
			title="chat web push failed", message=f"user={user} room={room} message={message}"
		)
		return False


# --------------------------------------------------------------------------- labels


def _room_label(room: str) -> str:
	"""What to call the room in a notification subject.

	A direct message has no title — it is named after whoever you are not — and resolving that
	per recipient would mean a query per member. The generic label is deliberate: a subject
	reading "New messages in a direct message" tells the reader as much as they need to decide
	whether to open it, and the room itself names the person the moment they do.
	"""
	try:
		row = frappe.db.get_value("Chat Room", room, ["title", "room_type"], as_dict=True) or {}
	except Exception:
		return ""
	title = (row.get("title") or "").strip()
	if title:
		return title
	return frappe._("a direct message") if row.get("room_type") == "Direct Message" else ""


def _sender_label(row: dict[str, Any]) -> str:
	"""The sender's display name, degrading to their address and then to a generic noun.

	``sender`` may be NULL: an external Chat participant is stored with ``sender_email`` and
	no ``User`` link, because ERPNext is the record of what was said rather than of who has an
	account here.
	"""
	sender = (row.get("sender") or "").strip()
	if sender:
		try:
			full = frappe.db.get_value("User", sender, "full_name")
		except Exception:
			full = None
		return (full or sender).strip()
	email = (row.get("sender_email") or "").strip()
	if email:
		return email
	return frappe._("Someone")


def _log(message: str, room: str, user: str, decision: policy.Decision) -> None:
	"""Identifiers and a reason code. **Never a body**, per the module-wide logging rule.

	Lazy ``%``-args rather than an f-string: the format only runs when debug logging is on,
	and this is one line per member per message on the busiest path in the feature.
	"""
	try:
		log = frappe.logger("chat")
	except Exception:
		return
	log.debug(
		"chat notify message=%s room=%s user=%s reason=%s bell=%s push=%s auto_read=%s",
		message,
		room,
		user,
		decision.reason.value,
		decision.bell,
		decision.push,
		decision.auto_read,
	)
