# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The retirement arithmetic, executed. Bench-free, unittest.

`chat/indexing/retire_rules.py` imports nothing, which is what lets this run in the bench-free
tier. It is the prerequisite §4.F's purge is blocked on: a way to say *"everything in this room
at or below seq N is gone forever — drop its derived coverage and never re-read it"*.

**The two assertions that matter most** are the ones a review of the design produced, because
both were wrong in the obvious version:

* :class:`WhollyRetiredTest` — the delete predicate keys on `first_seq`, not `last_seq`. For a
  snapped mark the two are equivalent; for a mark set by hand they are not, and `last_seq`
  leaves a chunk holding the retired transcript verbatim that can never be cleaned up, because
  the monotonicity rule forbids lowering the mark to re-snap it.
* :class:`StraddlerTest` — `is_straddler` is a *detection* predicate as well as a
  construction-time one. The chunk sweep reads a room's messages and commits later, so a mark
  committed in between produces a straddler the snap never saw.
"""

import unittest
from typing import ClassVar

from erpnext_enhancements.chat.indexing import retire_rules as rules


class StraddlerTest(unittest.TestCase):
	def test_a_chunk_spanning_the_mark_straddles(self):
		self.assertTrue(rules.is_straddler(10, 30, 20))

	def test_a_chunk_wholly_below_does_not(self):
		self.assertFalse(rules.is_straddler(10, 20, 30))

	def test_a_chunk_wholly_above_does_not(self):
		self.assertFalse(rules.is_straddler(40, 50, 30))

	def test_a_chunk_ending_exactly_on_the_mark_does_not_straddle(self):
		"""It is wholly retired — the mark means 'at or below this seq is gone'."""
		self.assertFalse(rules.is_straddler(10, 20, 20))

	def test_a_chunk_beginning_exactly_on_the_mark_straddles(self):
		"""Its first message is retired and its last is not."""
		self.assertTrue(rules.is_straddler(20, 30, 20))

	def test_nothing_straddles_an_unretired_room(self):
		self.assertFalse(rules.is_straddler(1, 100, rules.NOT_RETIRED))


class SnapTest(unittest.TestCase):
	SPANS: ClassVar[list] = [(1, 20), (21, 40), (41, 60)]

	def test_a_mark_on_a_boundary_does_not_move(self):
		self.assertEqual(rules.snap_to_chunk_boundary(40, self.SPANS), (40, 0))

	def test_a_mark_inside_a_chunk_snaps_below_it(self):
		"""50 sits inside (41, 60), so the mark drops to 40 and ten messages are held."""
		self.assertEqual(rules.snap_to_chunk_boundary(50, self.SPANS), (40, 10))

	def test_the_hold_is_reported_rather_than_hidden(self):
		"""The lag is the entire price of this design. A caller that cannot report it presents
		a partial retirement as a complete one."""
		_effective, held = rules.snap_to_chunk_boundary(45, self.SPANS)
		self.assertEqual(held, 5)

	def test_a_room_with_no_chunks_snaps_nowhere(self):
		"""Retirement must not be gated on the semantic tier being switched on."""
		self.assertEqual(rules.snap_to_chunk_boundary(500, []), (500, 0))

	def test_overlapping_straddlers_take_the_widest_protection(self):
		"""The historical insert path could produce overlaps. The lowest first_seq wins."""
		spans = [(10, 40), (25, 50)]
		self.assertEqual(rules.snap_to_chunk_boundary(30, spans), (9, 21))

	def test_snapping_below_the_first_chunk_clamps_at_zero(self):
		self.assertEqual(rules.snap_to_chunk_boundary(5, [(1, 20)]), (rules.NOT_RETIRED, 5))

	def test_a_zero_request_is_a_no_op(self):
		self.assertEqual(rules.snap_to_chunk_boundary(0, self.SPANS), (rules.NOT_RETIRED, 0))

	def test_snapping_is_idempotent(self):
		"""Nothing rebuilds a chunk below the mark, so the spans it snapped against cannot
		move — re-snapping an effective mark must return it unchanged."""
		effective, _ = rules.snap_to_chunk_boundary(50, self.SPANS)
		self.assertEqual(rules.snap_to_chunk_boundary(effective, self.SPANS), (effective, 0))


class WhollyRetiredTest(unittest.TestCase):
	"""The delete predicate. Keys on `first_seq`, and the review found out why."""

	def test_a_chunk_below_the_mark_is_deletable(self):
		self.assertTrue(rules.wholly_retired(first_seq=10, mark=40))

	def test_a_chunk_above_the_mark_is_not(self):
		self.assertFalse(rules.wholly_retired(first_seq=41, mark=40))

	def test_a_chunk_starting_exactly_on_the_mark_is_deletable(self):
		self.assertTrue(rules.wholly_retired(first_seq=40, mark=40))

	def test_an_unsnapped_mark_still_deletes_its_straddler(self):
		"""The case the review found. A mark set by hand — the DocField is `read_only`, which
		is not a database constraint — leaves a chunk spanning it. Keyed on `last_seq` that
		chunk survives forever holding the retired transcript verbatim, matched by the lexical
		tier, and unreachable because the mark can never be lowered to re-snap it."""
		self.assertTrue(rules.wholly_retired(first_seq=30, mark=45))

	def test_nothing_is_deletable_on_an_unretired_site(self):
		self.assertFalse(rules.wholly_retired(first_seq=1, mark=rules.NOT_RETIRED))


class WatermarkFloorTest(unittest.TestCase):
	"""The fix for the re-chunking loop, which is the whole reason the purge was blocked."""

	def test_the_floor_holds_when_chunks_are_deleted(self):
		"""Deleting a room's chunks drops `max(last_seq)` to 0. Without the floor the sweep
		re-reads every live message and re-chunks it verbatim with a fresh embedding, once
		every ten minutes."""
		self.assertEqual(rules.watermark_floor(max_last_seq=0, mark=500), 500)

	def test_a_higher_chunk_watermark_wins(self):
		self.assertEqual(rules.watermark_floor(max_last_seq=900, mark=500), 900)

	def test_an_unretired_room_is_unaffected(self):
		self.assertEqual(rules.watermark_floor(max_last_seq=900, mark=rules.NOT_RETIRED), 900)

	def test_an_empty_room_floors_at_zero(self):
		self.assertEqual(rules.watermark_floor(max_last_seq=0, mark=0), 0)


class DigestTest(unittest.TestCase):
	def test_a_digest_that_covers_retired_messages_is_retired(self):
		self.assertTrue(rules.digest_is_retired(covered_from=1, mark=40))

	def test_a_digest_entirely_above_the_mark_survives(self):
		self.assertFalse(rules.digest_is_retired(covered_from=41, mark=40))

	def test_an_unretired_site_retires_no_digest(self):
		"""Without the NOT_RETIRED guard every digest beginning at 0 answers 'retired', which
		is all of them — a mechanism that ships inert would instead delete the estate."""
		self.assertFalse(rules.digest_is_retired(covered_from=0, mark=rules.NOT_RETIRED))

	def test_the_predicate_is_idempotent_after_a_rebuild(self):
		"""A digest rebuilt from surviving messages begins above the mark and stops matching."""
		self.assertFalse(rules.digest_is_retired(covered_from=41, mark=40))


class EmptiedRoomTest(unittest.TestCase):
	def test_a_fully_retired_room_is_emptied(self):
		self.assertTrue(rules.room_is_emptied(seq_high_water=40, mark=40))

	def test_a_room_with_surviving_messages_is_not(self):
		self.assertFalse(rules.room_is_emptied(seq_high_water=100, mark=40))

	def test_an_unretired_room_is_not(self):
		self.assertFalse(rules.room_is_emptied(seq_high_water=100, mark=rules.NOT_RETIRED))


class LiveRangeTest(unittest.TestCase):
	def test_the_range_starts_above_the_mark(self):
		self.assertEqual(rules.live_range(seq_high_water=100, mark=40), (41, 100))

	def test_an_unretired_room_starts_at_one(self):
		self.assertEqual(rules.live_range(seq_high_water=100, mark=0), (1, 100))

	def test_an_emptied_room_is_expressed_as_zero_and_never_inverted(self):
		"""An inverted range read as a BETWEEN would match nothing and look correct; read as a
		count it would be negative. Neither is a thing a caller should have to handle."""
		self.assertEqual(rules.live_range(seq_high_water=40, mark=40), (0, 0))


class MonotonicityTest(unittest.TestCase):
	def test_raising_the_mark_is_allowed(self):
		self.assertEqual(rules.refuse_lowering(current=10, proposed=40), "")

	def test_holding_it_is_allowed(self):
		self.assertEqual(rules.refuse_lowering(current=40, proposed=40), "")

	def test_lowering_it_is_refused_with_a_reason(self):
		"""Lowering re-admits derived coverage of messages that no longer exist, and nothing
		can rebuild that coverage because the source is gone — so the stale rows simply become
		servable again."""
		refusal = rules.refuse_lowering(current=40, proposed=10)
		self.assertTrue(refusal)
		self.assertIn("40", refusal)
		self.assertIn("10", refusal)


class ShipsInertTest(unittest.TestCase):
	"""Every predicate answers 'nothing to do' on a site where the mark has never moved.

	`Chat Room` is a NORMAL doctype, so `default: 0` reaches every existing row in the one
	ALTER — the opposite of the Single behaviour, and the case CLAUDE.md warns about in both
	directions. That makes 0 the universal starting state, and it must mean 'inert'.
	"""

	def test_every_predicate_is_a_no_op_at_zero(self):
		self.assertFalse(rules.is_straddler(1, 100, rules.NOT_RETIRED))
		self.assertFalse(rules.wholly_retired(1, rules.NOT_RETIRED))
		self.assertFalse(rules.digest_is_retired(0, rules.NOT_RETIRED))
		self.assertFalse(rules.room_is_emptied(100, rules.NOT_RETIRED))
		self.assertEqual(rules.watermark_floor(900, rules.NOT_RETIRED), 900)
		self.assertEqual(rules.snap_to_chunk_boundary(0, [(1, 20)]), (rules.NOT_RETIRED, 0))


if __name__ == "__main__":
	unittest.main()
