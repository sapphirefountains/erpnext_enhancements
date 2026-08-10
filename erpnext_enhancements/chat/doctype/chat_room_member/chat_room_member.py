# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chat Room Member — one person's membership of one room.

**A standalone DocType, not a child table of Chat Room, and that is the pivotal
modelling choice in the whole schema.** A child table is loaded in full with its
parent and cannot be indexed on ``user`` — and ``(user, is_active)`` is the
leading index of the single most-run query in the system: *"every room this user
is currently in"*, which the client asks on boot, the unread badge asks
continuously, and the retrieval gate asks to derive ``allowed_rooms``.

**Leaving is soft.** ``is_active = 0`` plus ``left_at`` and ``left_seq``, never a
row delete. Three things fall out of that, all of them wanted: the audit trail
can answer *"was this person in the room when that was said"*; the
``unique(room, user)`` constraint survives a leave-and-rejoin; and the Google
membership resource name survives with it, so a re-add reconciles instead of
blindly re-creating.

**Read state is a high-water mark, not a receipt table.** ``last_read_seq`` is
one integer per ``(user, room)``. The rejected alternative — a row per
``(user, message)`` — is the dominant scaling risk in the design by an order of
magnitude: a 20-person room turns 2,000 message writes a day into ~20,000
receipt writes a day, against a site whose current busiest table takes ~728
writes a day in total.

``(room, user)`` uniqueness is enforced by a composite index from a patch, not by
a ``validate()`` doing a ``SELECT``, because a check-then-insert is a TOCTOU bug
at exactly the moment it matters: two workers materialising the same
per-document room at once.

THE READ MARK IS MONOTONIC, AND IT IS RAW SQL FOR A REASON
-----------------------------------------------------------
:func:`advance_read_mark` is the one piece of Phase 2 behaviour this DocType
carries, and it is here rather than in the sync package because the rule belongs
with the column: ``last_read_seq`` may only ever move **forward**.

The write path calls it for the author of every message. Without that, sending a
message marks your own room unread — the same problem ``sender_kind``'s field
description already anticipates for Triton's replies, arriving from the other
direction.

The membership diff against Google and the derived-membership reconciliation
still belong to later phases.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import cint, now_datetime


class ChatRoomMember(Document):
	pass


def advance_read_mark(room: str, user: str, seq: int) -> None:
	"""Move one member's ``last_read_seq`` forward to ``seq``. Never backwards, never a save.

	**One conditional ``UPDATE``, not a read-modify-write.** Two tabs of the same SPA, or a
	message insert racing the user's own scroll, both produce two concurrent advances; a
	``get_value`` / compare / ``set_value`` sequence between them lets the lower number win
	and silently marks messages unread that the user has already seen. ``where last_read_seq
	< %(seq)s`` pushes the comparison into the statement, where the database serialises it
	for free.

	**No ``Document.save``, and ``update_modified=False`` in spirit.** A read mark moving is
	not a change to the membership: a save would fire this DocType's own hooks, take a
	document lock, write a ``Version`` row on any doctype that tracks changes, and churn
	``modified`` on a row that several other queries filter on. The read mark is the highest
	frequency write in the entire feature — it moves for every member of every room on every
	message — and it has to cost one statement.

	Silently does nothing when the membership row is absent. That is the right answer rather
	than an error: a message can legitimately be filed by somebody who is no longer an active
	member (an inbound message from a person who has left the space), and refusing the read
	mark must never be able to fail the message insert it hangs off.
	"""
	target = cint(seq)
	if not room or not user or target < 1:
		return

	frappe.db.sql(
		"""update `tabChat Room Member`
			set last_read_seq = %(seq)s, last_read_at = %(now)s
			where room = %(room)s and user = %(user)s and coalesce(last_read_seq, 0) < %(seq)s""",
		{"seq": target, "now": now_datetime(), "room": room, "user": user},
	)
