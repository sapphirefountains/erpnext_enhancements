"""Bench-free tests for element codes, the append-only rule, the tally and who may promote
(WI-079 slice 5, ADR 0016 §2).

The acceptance criterion this pins first: **importing a revision that renumbers an existing
part is refused.** A part number never changes meaning, so a note pinned to ``L3-S04-E05`` in
one revision still points at the same part in the next.

Stdlib only. Run: python -m unittest erpnext_enhancements.tests.test_design_review_codes -v
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.design_review import authority, codes, tally

EXISTING = {"learner:S04": [[1, "Top bar"], [2, "Video block"], [3, "Callout"]]}


class TestCodes(unittest.TestCase):
	def test_round_trip(self):
		self.assertEqual(codes.format_code("L3", "S04", 5), "L3-S04-E05")
		self.assertEqual(codes.parse_code("L3-S04-E05"), ("L3", "S04", 5))
		self.assertEqual(codes.parse_code("ENTRY-S01-E03"), ("ENTRY", "S01", 3))
		for bad in ("", "L3-S04", "l3-S04-E05", "L3-S04-E", "L3-S04-E05; drop", "L3-S04-E05-E06"):
			self.assertIsNone(codes.parse_code(bad), bad)

	def test_append_is_accepted(self):
		incoming = {
			"learner:S04": [[1, "Top bar"], [2, "Video block"], [3, "Callout"], [4, "Quiz card"]],
			"learner:S05": [[1, "Question"]],
		}
		added = codes.check_append_only(EXISTING, incoming)
		self.assertEqual(added, ["learner:S04:E04 Quiz card", "learner:S05:E01 Question"])

	def test_omitting_a_part_keeps_it(self):
		self.assertEqual(codes.check_append_only(EXISTING, {"learner:S04": [[1, "Top bar"]]}), [])

	def test_renumbering_is_refused(self):
		with self.assertRaises(codes.RenumberedPart) as ctx:
			codes.check_append_only(
				EXISTING, {"learner:S04": [[1, "Top bar"], [2, "Callout"], [3, "Video block"]]}
			)
		message = str(ctx.exception)
		self.assertIn("E02 is 'Video block'", message)
		self.assertIn("Nothing was imported", message)

	def test_moving_a_name_to_a_new_number_is_refused(self):
		with self.assertRaises(codes.RenumberedPart):
			codes.check_append_only(EXISTING, {"learner:S04": [[9, "Video block"]]})

	def test_duplicates_in_a_revision_are_refused(self):
		with self.assertRaises(codes.RenumberedPart):
			codes.check_append_only({}, {"learner:S04": [[1, "Top bar"], [1, "Other"]]})
		with self.assertRaises(codes.RenumberedPart):
			codes.check_append_only({}, {"learner:S04": [[1, "Top bar"], [2, "Top bar"]]})


class TestTally(unittest.TestCase):
	OPTIONS = ["L1", "L2", "L3", "L4", "L5"]

	def test_borda_matches_the_artifact_ballot(self):
		result = tally.borda([["L3", "L1", "L2", "L4", "L5"], ["L1", "L3", "L5", "L2", "L4"]], self.OPTIONS)
		self.assertEqual(result["points"], {"L1": 9, "L2": 5, "L3": 9, "L4": 3, "L5": 4})
		self.assertEqual(result["first"], {"L1": 1, "L2": 0, "L3": 1, "L4": 0, "L5": 0})
		self.assertEqual(result["voters"], 2)

	def test_ranking_must_name_every_option_once(self):
		self.assertEqual(
			tally.clean_ranking(["L5", "L4", "L3", "L2", "L1"], self.OPTIONS), ["L5", "L4", "L3", "L2", "L1"]
		)
		for bad in (["L1", "L2"], ["L1", "L2", "L3", "L4", "L9"], ["L1", "L1", "L3", "L4", "L5"]):
			with self.assertRaises(ValueError):
				tally.clean_ranking(bad, self.OPTIONS)

	def test_verdict_counts(self):
		rows = [
			{"option_code": "L3", "screen_code": "S04", "verdict": "Yes"},
			{"option_code": "L3", "screen_code": "S04", "verdict": "No"},
			{"option_code": "L3", "screen_code": "S04", "verdict": "Yes"},
		]
		self.assertEqual(tally.verdict_counts(rows), {"L3:S04": {"Yes": 2, "Maybe": 0, "No": 1}})


class TestAuthority(unittest.TestCase):
	def test_service_accounts_cannot_promote(self):
		for user in (
			"triton@sapphirefountains.com",
			"mdm@sapphirefountains.com",
			"Administrator",
			"Guest",
			"",
			None,
		):
			self.assertFalse(authority.is_human_system_manager(user, ["System Manager"]), user)

	def test_a_person_needs_the_role(self):
		self.assertTrue(
			authority.is_human_system_manager("nik@sapphirefountains.com", ["System Manager", "Desk User"])
		)
		self.assertFalse(authority.is_human_system_manager("nik@sapphirefountains.com", ["Desk User"]))


if __name__ == "__main__":
	unittest.main()
