# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Retiring derived coverage of destroyed messages. **No Frappe, no imports.**

The prerequisite Phase 6 §4.F is blocked on. `chat/governance/purge_rules.py` marks
`Chat Context Chunk`, `Chat Room Digest` and `Chat Thread Digest` **BLOCKED** because the
derived layer has a staleness story and no *retirement* story: chunks and digests are rebuilt
*from live messages*, and nothing removes derived content on the grounds that its source was
destroyed. This is that story.

--------------------------------------------------------------------------------------
Why this sits at the package root rather than in ``chat/indexing/``
--------------------------------------------------------------------------------------

It shipped in ``chat/indexing/`` in v1.295.0 and that was wrong. Four consumers span three
packages — the indexer and the summariser in ``chat/indexing/``, the retrieval gate, and the
retention planner in ``chat/governance/`` — and ``chat/indexing`` is the **writer package**,
whose exemption in ``test_chat_gate_source_scan.py`` rests on four stated properties. The
fourth is *"no endpoint importing it"*, and the gate importing the writer package to obtain a
WHERE fragment would have broken it for the sake of a module that reads nothing.

So it lives beside ``permissions.py``, which is the closest precedent: shared pure logic that
several packages need and none owns.

--------------------------------------------------------------------------------------
The mark, and why it is a floor
--------------------------------------------------------------------------------------

One monotonic integer per room — `Chat Room.retired_below_seq` — meaning *"every message in
this room at or below this seq is gone forever."* It is the mirror of `seq_high_water`, and a
room's live range is `(retired_below_seq, seq_high_water]`.

`0` means nothing is retired, and because `Chat Room` is a **normal** DocType the `default`
reaches every existing row in the one `ALTER` — the opposite of the Single behaviour, and the
case `CLAUDE.md` warns about in both directions. So this ships inert on every site, with no
backfill patch, and a patch keyed on emptiness would match zero rows.

--------------------------------------------------------------------------------------
The boundary is solved by arithmetic, not by re-chunking
--------------------------------------------------------------------------------------

A chunk that straddles the mark covers surviving messages too, so deleting it wholesale
destroys *their* retrieval coverage — permanently, because nothing rebuilds a mid-range hole:
the indexer's watermark is `max(last_seq)` over sealed chunks, so a gap below a surviving
chunk is never revisited.

**So the mark snaps down to a sealed-chunk boundary when it is set.** After snapping no
surviving chunk straddles it, chunks below it are wholly retired and deletable, and no future
chunk can straddle it either because the indexer reads only above the floor.

The price is a bounded **hold**, not lost coverage: messages between the snapped mark and the
requested one are kept until the retention window advances past their covering chunk's end.
That lag is one chunk's own time span — 1200 tokens, or 20 messages, or a 45-minute gap — so
minutes to hours, against a retention window measured in days. :func:`snap_to_chunk_boundary`
returns it so a caller can report the lag rather than hide it.

**The alternative was a mid-range rebuild path, and it is worse on every axis.** It would
re-run the chunker over a truncated stream, where the inter-message gap is recomputed over
surviving rows — so a truncated range trips the gap rule where it did not before, moves
`first_seq`, and collides with `unique(room, first_seq)`. It would need a forced seal, changing
what `sealed` means. And it would pay a fresh embedding per retirement. Snapping costs one
integer of lag and no new code path.

--------------------------------------------------------------------------------------
Two corrections from review, both load-bearing
--------------------------------------------------------------------------------------

**Predicates key on `first_seq`, not `last_seq`.** Deleting on `last_seq <= mark` is correct
only for a mark that was snapped, and the field is `read_only` on the DocField — which is not
a database constraint, and a System Manager at a bench prompt is exactly who sets these. An
unsnapped mark can never be snapped afterwards, because the monotonicity invariant forbids
lowering it. `first_seq > mark` is *equivalent* when the mark was snapped and *correct* when
it was not, so it costs nothing and closes the case.

**A chunk born after the snap can still straddle it.** The chunk sweep reads a room's messages
and commits later; a mark committed between those two points produces a straddler the snap
never saw. :func:`is_straddler` is therefore also a *detection* predicate, not only a
construction-time one — the sweep re-checks rather than assuming.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

#: A room with nothing retired. Distinct from "retired through seq 0", which cannot occur:
#: `seq` starts at 1, so a mark of 0 is unambiguous.
NOT_RETIRED: Final[int] = 0


def is_straddler(first_seq: int, last_seq: int, mark: int) -> bool:
	"""Does this chunk span the mark — covering both retired and surviving messages?

	Also a *detection* predicate and not only a construction-time one: the chunk sweep reads a
	room's messages at one moment and commits later, so a mark committed in between produces a
	straddler the snap never saw. Anything consuming the mark re-checks rather than assuming
	the snap held.
	"""
	if mark <= NOT_RETIRED:
		return False
	return int(first_seq) <= int(mark) < int(last_seq)


def snap_to_chunk_boundary(requested: int, spans: Sequence[tuple[int, int]]) -> tuple[int, int]:
	"""``(effective_mark, held_back)`` — the mark lowered to clear every straddling chunk.

	``spans`` is ``(first_seq, last_seq)`` per sealed chunk in the room, in any order.

	Returns the *held back* count alongside, because the lag is the whole price of this design
	and a caller that cannot report it will present a partial retirement as a complete one.

	Multiple straddlers are possible — the historical insert path could overlap — and the
	widest protection wins: the lowest `first_seq` among them. A room with no chunks at all
	(the semantic tier switched off, or a brand-new room) has no straddler, so the mark is not
	gated on indexing being enabled.
	"""
	target = max(int(requested), NOT_RETIRED)
	if target <= NOT_RETIRED:
		return NOT_RETIRED, 0

	straddling = [int(first) for first, last in spans if is_straddler(first, last, target)]
	if not straddling:
		return target, 0

	effective = max(min(straddling) - 1, NOT_RETIRED)
	return effective, target - effective


def wholly_retired(first_seq: int, mark: int) -> bool:
	"""May this chunk be deleted?

	**`first_seq`, deliberately, and not `last_seq`.** For a mark that was snapped the two are
	equivalent — no surviving chunk straddles it — so this costs nothing there. For a mark set
	by hand, which the `read_only` DocField does not prevent, `last_seq <= mark` leaves the
	straddler in place while the purge is authorised to destroy the messages inside it: the
	chunk then holds the retired transcript verbatim, is matched by the lexical tier, and can
	never be cleaned up because the mark can never be lowered to re-snap it.
	"""
	if mark <= NOT_RETIRED:
		return False
	return int(first_seq) > NOT_RETIRED and int(first_seq) <= int(mark)


def watermark_floor(max_last_seq: int, mark: int) -> int:
	"""Where the chunk indexer resumes from. **The fix for the re-chunking loop.**

	Without the floor, deleting a room's chunks retreats the watermark — it is
	``max(last_seq)`` over sealed chunks with no staleness filter — and the ten-minute sweep
	re-reads every live message above it and re-chunks them *verbatim*, with a fresh
	embedding. A purge that tidied up its chunks would manufacture new copies of the text it
	was destroying, once every ten minutes, for as many nights as its batch cap took.
	"""
	return max(int(max_last_seq or 0), int(mark or 0), NOT_RETIRED)


def digest_is_retired(covered_from: int, mark: int) -> bool:
	"""Does this digest summarise anything that no longer exists?

	Any intersection at all, because a digest is a summary crossing time and topic boundaries
	and there is no partial un-saying of one. Delete rather than rebuild — and unlike a chunk
	that is safe, because the digest's own watermark lives on the row being deleted, so
	deleting it retreats nothing and manufactures no new copy. The room is simply re-selected
	once, and either a correct digest is written from what survives or none exists.

	Guarded on :data:`NOT_RETIRED` first: without that, an unretired site answers *"retired"*
	for every digest that begins at 0, which is all of them.
	"""
	if mark <= NOT_RETIRED:
		return False
	return int(covered_from) <= int(mark)


def room_is_emptied(seq_high_water: int, mark: int) -> bool:
	"""Has retirement consumed the whole room?

	The specific fix for the five-minutes-forever spin: `_messages_for_digest` filters
	`is_deleted = 0` and `_rebuild_room_digest` returns *before writing* when that comes back
	empty, so an emptied room keeps its stale summary while the dirty sweep re-selects it
	every five minutes doing nothing, incrementing no failure counter and therefore never
	poisoning out. A caller that knows the room is emptied deletes the row instead.
	"""
	if mark <= NOT_RETIRED:
		return False
	return int(mark) >= int(seq_high_water or 0)


def live_range(seq_high_water: int, mark: int) -> tuple[int, int]:
	"""``(first surviving seq, last)``. Empty is expressed as ``(0, 0)``, never as inverted."""
	low = max(int(mark or 0), NOT_RETIRED) + 1
	high = int(seq_high_water or 0)
	if high < low:
		return NOT_RETIRED, NOT_RETIRED
	return low, high


def refuse_lowering(current: int, proposed: int) -> str:
	"""``""`` if the move is legal, else why not. Monotonic upward, and never past the top.

	Lowering would be worse than useless: it would re-admit derived coverage of messages that
	are already destroyed, and there is nothing left to rebuild that coverage *from*, so the
	stale rows would simply become servable again.
	"""
	now = int(current or 0)
	want = int(proposed or 0)
	if want < now:
		return (
			f"retired_below_seq only moves up ({now} -> {want}). Lowering it re-admits derived "
			"coverage of messages that no longer exist, and nothing can rebuild that coverage "
			"correctly because the source is gone."
		)
	return ""
