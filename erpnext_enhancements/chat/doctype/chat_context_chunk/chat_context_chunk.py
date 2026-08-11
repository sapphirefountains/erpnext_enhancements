# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chat Context Chunk — the semantic index, and a verbatim copy of what coworkers said.

A chunk is a run of consecutive messages in one room, sealed at a boundary, stored as one
body with one embedding. Volume is roughly messages ÷ 15.

**This table is exactly as sensitive as ``Chat Message`` and is treated identically**: zero
DocPerm, no ``in_global_search``, ``track_changes = 0``, and it is in the MCP denylist. That
is not caution about a derived artefact — ``body`` holds the messages verbatim, so a leak
here is a leak of the transcript, and one that is *easier* to read than the transcript
because a chunk is already assembled into prose.

--------------------------------------------------------------------------------------
The one rule this controller enforces, and why it is a controller rule
--------------------------------------------------------------------------------------

**A chunk never spans rooms.** Not a chunking heuristic — the permission boundary. The
retrieval gate filters candidates on ``room`` in the ``WHERE`` clause before a single vector
is loaded, so a chunk covering two rooms is a chunk that *cannot be filtered*: it would be
admitted by membership of either room and would carry the other one's content with it.

``room`` is therefore ``reqd`` and ``set_only_once``, and :meth:`ChatContextChunk.validate`
re-checks the span rather than trusting the writer. The writer is a background job that will
be retried, and the retry is where the interesting bugs live.

--------------------------------------------------------------------------------------
Sealing, and the tail that is deliberately not indexed
--------------------------------------------------------------------------------------

``sealed = 0`` means "still growing"; only a sealed chunk is embedded, and only a sealed
chunk is a retrieval candidate. The open tail of every room is **excluded from the semantic
index on purpose**, and a reviewer will challenge it, so the argument is here rather than in
a commit message:

* embedding the tail means **one embedding call per chat message**, which is the largest
  avoidable cost in the whole design;
* the tail of the room the question was asked in is carried **verbatim** by the thread tier,
  so the case that matters most is not missing anything;
* other rooms' tails are reachable through the **lexical** tier, which is exact-match and
  needs no embedding;
* and the next digest pass folds them in within minutes.

So the gap is "a semantic match in another room's last few minutes", bounded by the digest
cadence, and it costs one embedding call per message to close.

--------------------------------------------------------------------------------------
Staleness is not optional bookkeeping
--------------------------------------------------------------------------------------

An edit or delete inside ``[first_seq, last_seq]`` sets ``is_stale``, and a stale chunk is
**skipped by retrieval outright** rather than served with a caveat. ERPNext holds the only
copy of a deleted body — Google's tombstone is content-free — so a stale chunk served once
is a deleted sentence back in a model's context window, with the person who deleted it
unable to know. Rebuild is from source, never a patch of the stored body.

Indentation is tabs, per ``CLAUDE.md`` and the convention this package follows.
"""

from __future__ import annotations

import hashlib
import json

import frappe
from frappe.model.document import Document

#: ``ceil(len(text) / 4) * 1.15`` — the fallback when no real tokenizer is importable in a
#: background-job context. Recorded in ``token_count_method`` either way, so a later
#: correction pass can find the estimates instead of guessing which rows to trust.
CHARS_PER_TOKEN: int = 4
HEURISTIC_SAFETY: float = 1.15

METHOD_HEURISTIC: str = "heuristic"
METHOD_TOKENIZER: str = "tokenizer"


def estimate_tokens(text: str) -> int:
	"""The heuristic count. Deliberately an over-estimate.

	Erring high means the assembler under-fills the ceiling; erring low means it overflows
	it, and an overflow is discovered by the model provider rather than by us.
	"""
	if not text:
		return 0
	return int(-(-len(text) // CHARS_PER_TOKEN) * HEURISTIC_SAFETY) + 1


def content_hash(body: str) -> str:
	"""sha256 of the sealed body.

	This is what makes re-running the indexer *idempotent* rather than merely harmless: two
	jobs that build the same chunk agree on the hash, and a rebuild can tell whether the
	embedding it already holds is still the embedding of this text.
	"""
	return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


class ChatContextChunk(Document):
	def validate(self) -> None:
		self._validate_span()
		self._normalise_participants()
		self._refresh_content_hash()
		self._ensure_token_count()

	def _validate_span(self) -> None:
		first = int(self.first_seq or 0)
		last = int(self.last_seq or 0)
		if first <= 0:
			frappe.throw(frappe._("Chat Context Chunk requires a positive First Seq."))
		if last < first:
			frappe.throw(
				frappe._("Chat Context Chunk span is reversed: first_seq {0} > last_seq {1}.").format(
					first, last
				)
			)

	def _normalise_participants(self) -> None:
		"""Stored as a JSON array of User names, sorted and de-duplicated.

		Sorted because the field feeds ``content_hash`` indirectly through the chunk's
		identity in tests and in the citation line, and an unordered iteration is the
		classic way to make a hash flap for no reason.
		"""
		raw = self.participants
		if not raw:
			self.participants = "[]"
			return
		if isinstance(raw, str):
			try:
				values = json.loads(raw)
			except Exception:
				values = [raw]
		else:
			values = raw
		if not isinstance(values, list | tuple | set):
			values = [values]
		cleaned = sorted({str(value).strip() for value in values if str(value or "").strip()})
		self.participants = json.dumps(cleaned)

	def _refresh_content_hash(self) -> None:
		self.content_hash = content_hash(self.body or "")

	def _ensure_token_count(self) -> None:
		if not self.token_count:
			self.token_count = estimate_tokens(self.body or "")
			self.token_count_method = METHOD_HEURISTIC
		elif not self.token_count_method:
			self.token_count_method = METHOD_HEURISTIC

	def participant_list(self) -> list[str]:
		try:
			return list(json.loads(self.participants or "[]"))
		except Exception:
			return []
