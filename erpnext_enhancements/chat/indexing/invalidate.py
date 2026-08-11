# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What stops Triton quoting a message somebody deleted.

Phase 2 wired :func:`chat.seams.mark_room_context_stale` at every edit and every delete, with
the ``seq`` span that changed, and made it count and log. This is the half it was waiting for.

--------------------------------------------------------------------------------------
Why a delete is not just a cache miss
--------------------------------------------------------------------------------------

A rolling summary can add information but **it cannot unsay it**. If a digest generated before
a delete is still served, the deleted sentence is still in a model's context window, and the
person who deleted it has no way to know.

And ERPNext is the only place that text exists. Google's tombstone is content-free —
``showDeleted`` returns the delete time and metadata, never the body — so "it is still in
Google" is not a fallback, it is nowhere. The chunk table makes this worse rather than better,
because a chunk is a *verbatim copy* of the messages and a digest is a *summary* that crosses
time and topic boundaries the original never did.

--------------------------------------------------------------------------------------
Overlap, not containment
--------------------------------------------------------------------------------------

A row is invalidated when its span **overlaps** the changed span — ``first_seq <= to`` and
``last_seq >= from`` — not when it contains it. Containment misses the case that actually
happens: a multi-message delete spanning two chunks contains neither.

Widening is the safe direction throughout. Too-wide invalidation costs one recomputation; a
missed one serves a person text they deleted.

--------------------------------------------------------------------------------------
Marked, never deleted
--------------------------------------------------------------------------------------

Rows are flagged ``is_stale`` and left in place. Deleting them would be tidier and is wrong
twice over: a stale row still tells the indexer that span is covered, so deleting it silently
re-opens a gap the watermark thinks is closed; and retrieval already skips stale rows, so the
row's continued existence costs a boolean in a ``WHERE`` clause and buys a rebuild that knows
what it is replacing.

**Never raises.** It is called from the delete path, and raising would abort the delete —
leaving the message live *and* the digest stale, which is the exact pair of outcomes the seam
exists to prevent.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, now_datetime

CHUNK_DOCTYPE = "Chat Context Chunk"
ROOM_DIGEST_DOCTYPE = "Chat Room Digest"
THREAD_DIGEST_DOCTYPE = "Chat Thread Digest"


def invalidate_span(room: str, from_seq: int, to_seq: int) -> dict[str, int]:
	"""Mark every chunk and digest overlapping ``[from_seq, to_seq]`` stale.

	Returns per-table counts so the seam can log what it actually invalidated — an
	invalidation that reports nothing is indistinguishable from one that matched nothing, and
	those need different responses.
	"""
	room_name = str(room or "").strip()
	if not room_name:
		return {"chunks": 0, "room_digests": 0, "thread_digests": 0}

	low = max(cint(from_seq), 0)
	high = max(cint(to_seq), 0)
	if low > high:
		low, high = high, low

	stamp = now_datetime()
	return {
		"chunks": _mark_chunks(room_name, low, high, stamp),
		"room_digests": _mark_room_digests(room_name, low, high, stamp),
		"thread_digests": _mark_thread_digests(room_name, low, high, stamp),
	}


def _mark_chunks(room: str, low: int, high: int, stamp) -> int:
	"""Chunks whose ``[first_seq, last_seq]`` overlaps the changed span.

	``stale_from_seq``/``stale_to_seq`` record what triggered it, so an operator can tell a
	targeted invalidation from a room-wide one — which is the difference between "somebody
	edited a message" and "something is wrong with the indexer".
	"""
	try:
		frappe.db.sql(
			f"""
			update `tab{CHUNK_DOCTYPE}`
			set `is_stale` = 1,
				`stale_since` = %(stamp)s,
				`stale_from_seq` = %(low)s,
				`stale_to_seq` = %(high)s
			where `room` = %(room)s
				and `first_seq` <= %(high)s
				and `last_seq` >= %(low)s
				and `is_stale` = 0
			""",
			{"room": room, "low": low, "high": high, "stamp": stamp},
		)
		return _rows_affected()
	except Exception:
		_note(f"could not invalidate chunks in room {room} for seq {low}..{high}")
		return 0


def _mark_room_digests(room: str, low: int, high: int, stamp) -> int:
	try:
		frappe.db.sql(
			f"""
			update `tab{ROOM_DIGEST_DOCTYPE}`
			set `is_stale` = 1, `stale_since` = %(stamp)s
			where `room` = %(room)s
				and `covered_from` <= %(high)s
				and `covered_to` >= %(low)s
				and `is_stale` = 0
			""",
			{"room": room, "low": low, "high": high, "stamp": stamp},
		)
		return _rows_affected()
	except Exception:
		_note(f"could not invalidate the room digest for {room}")
		return 0


def _mark_thread_digests(room: str, low: int, high: int, stamp) -> int:
	try:
		frappe.db.sql(
			f"""
			update `tab{THREAD_DIGEST_DOCTYPE}`
			set `is_stale` = 1, `stale_since` = %(stamp)s
			where `room` = %(room)s
				and `covered_from` <= %(high)s
				and `covered_to` >= %(low)s
				and `is_stale` = 0
			""",
			{"room": room, "low": low, "high": high, "stamp": stamp},
		)
		return _rows_affected()
	except Exception:
		_note(f"could not invalidate thread digests in room {room}")
		return 0


def _rows_affected() -> int:
	"""How many rows the statement above changed.

	``select row_count()`` is MariaDB's own answer and names no table, which is the point:
	the three writers here need to report what they actually invalidated, and an
	invalidation that reports nothing is indistinguishable from one that matched nothing —
	those need different responses from whoever is reading the log.

	One helper rather than the same statement inlined three times, so the raw-SQL guard's
	unresolved-target exemption is one entry rather than three copies of one argument.
	"""
	try:
		return cint(frappe.db.sql("select row_count()")[0][0])
	except Exception:
		return 0


def _note(message: str) -> None:
	try:
		frappe.log_error(message, "Chat Indexing")
	except Exception:
		pass
