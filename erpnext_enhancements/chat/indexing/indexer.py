# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The pass that fills the semantic index. Scheduler-driven, resumable, idempotent.

Two jobs, deliberately separate:

* :func:`sweep_chunks` reads messages past each room's chunk watermark, seals what the pure
  boundary logic says to seal, and writes the rows. **No network I/O at all.**
* :func:`sweep_embeddings` picks up sealed rows with no vector and embeds them.

Splitting them is not tidiness. Chunking is cheap, local and always correct; embedding is a
paid external call that can fail, rate-limit or hang. Fused, an embedding outage stops the
index advancing and the room's history silently stops being searchable *at all* rather than
only semantically. Split, chunk rows keep landing, the **lexical** tier keeps finding them,
and the vectors backfill when the provider returns.

--------------------------------------------------------------------------------------
The watermark is derived, not stored
--------------------------------------------------------------------------------------

"Where did indexing get to in this room" is ``max(last_seq)`` over that room's sealed chunks.
There is no cursor column, and that is the same reasoning the digest pass uses for its dirty
predicate: a cursor is a second source of truth for a fact the rows already state, and the
copy that drifts is the one nobody notices. Derived is self-correcting — delete a chunk and
the next pass rebuilds it.

--------------------------------------------------------------------------------------
Why an unsealed tail is never written
--------------------------------------------------------------------------------------

Only **sealed** chunks are inserted. The open tail is left on the floor and re-read next pass.

The alternative — writing the tail unsealed and updating it as messages arrive — makes every
message a row update, gives the row a ``content_hash`` that changes under a reader, and
reintroduces exactly the per-message cost the tail rule exists to avoid. Re-reading a handful
of tail messages every five minutes is cheaper than that and cannot go stale.

--------------------------------------------------------------------------------------
Retries are the normal case, not the exception
--------------------------------------------------------------------------------------

A deploy ``FLUSHDB``s the queue Redis, Frappe v16 wires no RQ retries, and a worker can be
SIGKILLed mid-pass. So both jobs are written to be re-run at any point:

* a chunk insert that collides on ``unique(room, first_seq)`` is **success** — another worker
  built the same chunk from the same messages, which is what determinism buys;
* the watermark re-derives, so a half-finished pass simply resumes;
* nothing is deleted, so a crash cannot lose ground.

Both jobs no-op while ``Chat Settings.enabled`` is 0, like every other scheduler entry in this
package.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint, get_datetime, now_datetime

from erpnext_enhancements.chat.doctype.chat_context_chunk.chat_context_chunk import (
	METHOD_HEURISTIC,
	estimate_tokens,
)
from erpnext_enhancements.chat.indexing import chunker

CHUNK_DOCTYPE = "Chat Context Chunk"
MESSAGE_DOCTYPE = "Chat Message"
ROOM_DOCTYPE = "Chat Room"

#: Messages read per room per pass. A bound rather than a preference: the first pass over a
#: room with two years of history must not be one statement that loads it all into a worker.
MESSAGES_PER_PASS: int = 2_000

#: Rooms per pass, and chunks embedded per pass. Both bounded for the same reason the relay's
#: sweeper is: a job whose duration scales with the backlog is a job that gets killed by the
#: next deploy exactly when the backlog is largest.
ROOMS_PER_PASS: int = 25
EMBED_BATCH: int = 20


def sweep_chunks() -> dict[str, int]:
	"""Build and seal chunks for rooms whose history has moved past the watermark.

	Returns a small dict of counts, so ``bench execute`` on this reads as a report rather
	than as silence.
	"""
	if not _enabled():
		return {"rooms": 0, "chunks": 0, "skipped": 0}

	rooms = _rooms_needing_chunks(limit=ROOMS_PER_PASS)
	built = 0
	skipped = 0
	for room in rooms:
		try:
			built += _index_room(room["name"], cint(room.get("watermark")))
		except Exception:
			skipped += 1
			frappe.db.rollback()
			_note(f"chunking failed for room {room['name']}")
	return {"rooms": len(rooms), "chunks": built, "skipped": skipped}


def sweep_embeddings() -> dict[str, int]:
	"""Embed sealed chunks that have no vector yet.

	Separate from :func:`sweep_chunks` so an embedding outage cannot stop the index
	advancing — see the module docstring. A chunk that keeps failing stops being retried at
	``max_embed_failures`` and stays lexically searchable.
	"""
	if not _enabled() or not _semantic_enabled():
		return {"attempted": 0, "embedded": 0, "failed": 0}

	rows = _chunks_awaiting_embedding(limit=EMBED_BATCH)
	if not rows:
		return {"attempted": 0, "embedded": 0, "failed": 0}

	from erpnext_enhancements.chat.indexing import embed

	try:
		vectors = embed.embed_documents([row["body"] or "" for row in rows])
	except Exception as exc:
		# One failure fails the batch, not the row: the provider is down or the request was
		# malformed, and blaming an individual chunk would burn its retry budget for
		# something that was never about it.
		for row in rows:
			_record_embed_failure(row["name"], exc.__class__.__name__)
		frappe.db.commit()
		return {"attempted": len(rows), "embedded": 0, "failed": len(rows)}

	from erpnext_enhancements.chat.retrieval import vectors as vector_store

	embedded = 0
	for row, vector in zip(rows, vectors, strict=False):
		try:
			frappe.db.set_value(
				CHUNK_DOCTYPE,
				row["name"],
				{
					"embedding": vector_store.encode_vector(vector),
					"embedding_dim": len(vector),
					"embedding_model": _setting("embedding_model") or "",
					"embedding_version": cint(_setting("embedding_version")) or 1,
					"embedded_at": now_datetime(),
					"embed_failures": 0,
					"last_error": None,
				},
				update_modified=False,
			)
			embedded += 1
		except Exception as exc:
			_record_embed_failure(row["name"], exc.__class__.__name__)

	# Committed explicitly: InnoDB FULLTEXT sees COMMITTED rows only, and the next pass's
	# watermark read has to see what this one wrote.
	frappe.db.commit()
	return {"attempted": len(rows), "embedded": embedded, "failed": len(rows) - embedded}


# ------------------------------------------------------------------------------ one room


def _index_room(room: str, watermark: int) -> int:
	"""Chunk one room's messages after ``watermark``. Returns the number of chunks sealed."""
	rows = _messages_after(room, watermark, limit=MESSAGES_PER_PASS)
	if not rows:
		return 0

	messages, stamps = _to_chunker_messages(rows)
	tail_idle = _tail_idle_minutes(rows[-1])
	chunks = chunker.chunk_messages(
		messages,
		seal_tokens=cint(_setting("chunk_seal_tokens")) or chunker.DEFAULT_SEAL_TOKENS,
		seal_messages=cint(_setting("chunk_seal_messages")) or chunker.DEFAULT_SEAL_MESSAGES,
		gap_minutes=cint(_setting("chunk_seal_gap_minutes")) or chunker.DEFAULT_GAP_MINUTES,
		idle_tail_minutes=cint(_setting("chunk_idle_tail_minutes")) or chunker.DEFAULT_IDLE_TAIL_MINUTES,
		tail_idle_minutes=tail_idle,
	)

	written = 0
	for chunk in chunks:
		if not chunk.sealed or not chunk.messages:
			continue  # the open tail is left on the floor and re-read next pass
		if _write_chunk(room, chunk, stamps):
			written += 1

	# Commit per room rather than per pass: a room that fails must not roll back the rooms
	# already indexed, and FULLTEXT will not see any of it until it commits anyway.
	frappe.db.commit()
	return written


def _write_chunk(room: str, chunk: chunker.Chunk, stamps: dict[str, Any]) -> bool:
	"""Insert one sealed chunk. A ``unique(room, first_seq)`` collision is **success**.

	Another worker built the same chunk from the same messages — which is exactly what the
	pure, deterministic boundary logic is for. Logging it as an error would turn normal
	operation into a wall of noise and train everyone to ignore the log.
	"""
	from frappe.database.database import savepoint

	body = chunker.render_body(
		chunk, timestamps={m.name: str(stamps.get(m.name) or "") for m in chunk.messages}
	)
	first, last = chunk.messages[0], chunk.messages[-1]

	doc = frappe.new_doc(CHUNK_DOCTYPE)
	doc.room = room
	doc.thread_root = chunk.thread_root
	doc.sealed = 1
	doc.first_seq = first.seq
	doc.last_seq = last.seq
	doc.message_count = len(chunk.messages)
	doc.first_message = first.name
	doc.last_message = last.name
	doc.first_message_at = stamps.get(first.name)
	doc.last_message_at = stamps.get(last.name)
	doc.body = body
	doc.participants = frappe.as_json(chunk.participants())
	doc.token_count = estimate_tokens(body)
	doc.token_count_method = METHOD_HEURISTIC

	try:
		with savepoint(catch=(frappe.DuplicateEntryError, frappe.UniqueValidationError)):
			doc.insert(ignore_permissions=True)
	except Exception:
		_note(f"could not write a chunk for room {room} at seq {first.seq}")
		return False
	# `savepoint` swallows the duplicate, so an existing row leaves `doc.name` unset. That is
	# the signal for "already indexed" rather than an error.
	return bool(doc.get("name"))


def _to_chunker_messages(rows: list[dict[str, Any]]) -> tuple[list[chunker.Message], dict[str, Any]]:
	"""Rows → the pure chunker's input, with the clock read done **here**.

	``minutes_since_previous`` is computed in this module rather than inside the chunker,
	for the same reason the ranker takes an age rather than a timestamp: a clock read inside
	a pure function makes two identical runs differ, and determinism is the property the
	whole content-hash identity rests on.
	"""
	messages: list[chunker.Message] = []
	stamps: dict[str, Any] = {}
	previous: Any = None
	for row in rows:
		stamp = row.get("origin_timestamp") or row.get("creation")
		stamps[row["name"]] = stamp
		gap = 0.0
		if previous is not None and stamp is not None:
			try:
				gap = max((get_datetime(stamp) - get_datetime(previous)).total_seconds() / 60.0, 0.0)
			except Exception:
				gap = 0.0
		previous = stamp or previous
		text = str(row.get("text_plain") or row.get("text") or "")
		messages.append(
			chunker.Message(
				name=row["name"],
				seq=cint(row.get("seq")),
				sender=str(row.get("sender") or row.get("sender_email") or ""),
				text=text,
				thread_root=row.get("thread_root") or None,
				minutes_since_previous=gap,
				tokens=estimate_tokens(text),
			)
		)
	return messages, stamps


def _tail_idle_minutes(last_row: dict[str, Any]) -> float | None:
	"""How long the newest message has been the newest. ``None`` when unknowable.

	``now_datetime()`` and never SQL ``NOW()``: the database runs UTC while Frappe writes
	site-local, so mixing them reports a message from a minute ago as six hours old — and in
	*this* function that error seals every tail immediately, which is precisely the
	per-message embedding cost the tail rule exists to prevent.
	"""
	stamp = last_row.get("origin_timestamp") or last_row.get("creation")
	if not stamp:
		return None
	try:
		return max((now_datetime() - get_datetime(stamp)).total_seconds() / 60.0, 0.0)
	except Exception:
		return None


# ------------------------------------------------------------------------------- queries


def _rooms_needing_chunks(*, limit: int) -> list[dict[str, Any]]:
	"""Rooms whose ``seq_high_water`` is ahead of their newest sealed chunk.

	A LEFT JOIN rather than a stored cursor, and a room with **no** chunks at all is included
	by the ``coalesce`` — which is what makes the first pass over an existing room work
	without a backfill script.

	Oldest-lag-first, so a room that has been waiting cannot be starved by a busier one.
	"""
	return (
		frappe.db.sql(
			f"""
		select r.`name` as `name`, coalesce(max(c.`last_seq`), 0) as `watermark`
		from `tab{ROOM_DOCTYPE}` r
		left join `tab{CHUNK_DOCTYPE}` c
			on c.`room` = r.`name` and c.`sealed` = 1
		where r.`is_archived` = 0
		group by r.`name`, r.`seq_high_water`
		having r.`seq_high_water` > coalesce(max(c.`last_seq`), 0)
		order by (r.`seq_high_water` - coalesce(max(c.`last_seq`), 0)) desc
		limit %(limit)s
		""",
			{"limit": max(cint(limit), 0)},
			as_dict=True,
		)
		or []
	)


def _messages_after(room: str, watermark: int, *, limit: int) -> list[dict[str, Any]]:
	"""One room's live messages after the watermark, in ``seq`` order.

	Deleted rows are excluded, and that is the invalidation contract rather than a filter:
	a chunk is a verbatim copy, so indexing a deleted message would put its text somewhere
	the delete does not reach.
	"""
	return (
		frappe.db.sql(
			f"""
		select `name`, `seq`, `sender`, `sender_email`, `text`, `text_plain`,
			`thread_root`, `creation`, `origin_timestamp`
		from `tab{MESSAGE_DOCTYPE}`
		where `room` = %(room)s
			and `seq` > %(watermark)s
			and `is_deleted` = 0
		order by `seq` asc
		limit %(limit)s
		""",
			{"room": room, "watermark": max(cint(watermark), 0), "limit": max(cint(limit), 0)},
			as_dict=True,
		)
		or []
	)


def _chunks_awaiting_embedding(*, limit: int) -> list[dict[str, Any]]:
	"""Sealed, non-stale chunks with no vector and retry budget left."""
	return (
		frappe.db.sql(
			f"""
		select `name`, `body`
		from `tab{CHUNK_DOCTYPE}`
		where `sealed` = 1
			and `is_stale` = 0
			and (`embedding` is null or `embedding` = '')
			and coalesce(`embed_failures`, 0) < %(max_failures)s
		order by `last_message_at` desc
		limit %(limit)s
		""",
			{
				"max_failures": cint(_setting("max_embed_failures")) or 3,
				"limit": max(cint(limit), 0),
			},
			as_dict=True,
		)
		or []
	)


def _record_embed_failure(chunk: str, reason: str) -> None:
	"""Count the failure on the row. Never carries the body or a token."""
	try:
		frappe.db.sql(
			f"""
			update `tab{CHUNK_DOCTYPE}`
			set `embed_failures` = coalesce(`embed_failures`, 0) + 1,
				`last_error` = %(reason)s
			where `name` = %(name)s
			""",
			{"name": chunk, "reason": reason[:500]},
		)
	except Exception:
		pass


# ------------------------------------------------------------------------------- helpers


def _enabled() -> bool:
	try:
		return bool(cint(frappe.db.get_single_value("Chat Settings", "enabled")))
	except Exception:
		return False


def _semantic_enabled() -> bool:
	try:
		return bool(cint(frappe.db.get_single_value("Chat Settings", "semantic_tier_enabled")))
	except Exception:
		return False


def _setting(fieldname: str) -> Any:
	try:
		return frappe.db.get_single_value("Chat Settings", fieldname)
	except Exception:
		return None


def _note(message: str) -> None:
	try:
		frappe.log_error(message, "Chat Indexing")
	except Exception:
		pass
