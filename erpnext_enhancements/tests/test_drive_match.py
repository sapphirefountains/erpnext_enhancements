"""Bench-free unit tests for the Drive Link Manager fuzzy matcher
(``crm_enhancements/drive_match.py``).

The scoring/normalization/ranking is pure Python (no frappe, no network), so it
runs as plain unittest in CI — guarding the behaviour the dashboard relies on:
id-prefix stripping, order-independent matching, sane tiering, and best-of
ranking across alias forms.

Run: python -m unittest erpnext_enhancements.tests.test_drive_match
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.crm_enhancements import drive_match as dm  # noqa: E402


class TestNormalize(unittest.TestCase):
	def test_strips_id_prefix(self):
		self.assertEqual(dm.normalize("PRJ-00694 Smith Residence"), "smith residence")
		self.assertEqual(dm.normalize("CRM-OPP-2026-00112 — Pool Reno"), "pool reno")

	def test_collapses_punctuation_and_case(self):
		self.assertEqual(dm.normalize("  Smith & Sons, LLC.  "), "smith sons llc")

	def test_empty(self):
		self.assertEqual(dm.normalize(None), "")
		self.assertEqual(dm.normalize(""), "")


class TestSimilarity(unittest.TestCase):
	def test_identical_after_normalize_is_100(self):
		self.assertEqual(dm.similarity("PRJ-00694 Smith Residence", "Smith Residence"), 100.0)

	def test_order_independent(self):
		# Token-set overlap keeps reordered words scoring high.
		self.assertGreaterEqual(dm.similarity("Smith Pool Reno", "Reno Pool Smith"), 70.0)

	def test_unrelated_is_low(self):
		self.assertLess(dm.similarity("Smith Residence", "Acme Industrial Park"), 50.0)

	def test_empty_inputs_score_zero(self):
		self.assertEqual(dm.similarity("", "anything"), 0.0)
		self.assertEqual(dm.similarity("anything", None), 0.0)

	def test_containment_helps(self):
		# Folder appends a suffix to the record name — should still rank well.
		self.assertGreaterEqual(dm.similarity("Smith Residence", "Smith Residence Pool"), 70.0)


class TestTier(unittest.TestCase):
	def test_boundaries(self):
		self.assertEqual(dm.tier_for_score(100.0), "High")
		self.assertEqual(dm.tier_for_score(dm.TIER_HIGH), "High")
		self.assertEqual(dm.tier_for_score(dm.TIER_HIGH - 0.1), "Medium")
		self.assertEqual(dm.tier_for_score(dm.TIER_MEDIUM), "Medium")
		self.assertEqual(dm.tier_for_score(dm.TIER_LOW), "Low")
		self.assertEqual(dm.tier_for_score(dm.TIER_LOW - 0.1), "None")
		self.assertEqual(dm.tier_for_score(0.0), "None")


class TestBestMatches(unittest.TestCase):
	def setUp(self):
		self.folders = [
			{"id": "f1", "name": "Smith Residence", "path": "Smith/Smith Residence"},
			{"id": "f2", "name": "Jones Pool", "path": "Jones/Jones Pool"},
			{"id": "f3", "name": "Acme Industrial Park", "path": "Acme/Acme Industrial Park"},
		]

	def test_best_first_and_carries_folder_through(self):
		ranked = dm.best_matches(["PRJ-00694 Smith Residence", "Smith Residence", "PRJ-00694"], self.folders)
		self.assertEqual(ranked[0]["folder"]["id"], "f1")
		self.assertEqual(ranked[0]["score"], 100.0)
		# Whole folder dict is preserved for the caller (id/path kept).
		self.assertEqual(ranked[0]["folder"]["path"], "Smith/Smith Residence")

	def test_limit_respected(self):
		self.assertEqual(len(dm.best_matches(["Smith"], self.folders, limit=2)), 2)

	def test_alias_best_score_wins(self):
		# Only the bare-id alias is useless; the name alias should still match f1.
		ranked = dm.best_matches(["PRJ-00694", "Smith Residence"], self.folders)
		self.assertEqual(ranked[0]["folder"]["id"], "f1")

	def test_no_folders(self):
		self.assertEqual(dm.best_matches(["Smith"], []), [])

	def test_ignores_falsy_aliases(self):
		ranked = dm.best_matches([None, "", "Jones Pool"], self.folders)
		self.assertEqual(ranked[0]["folder"]["id"], "f2")


if __name__ == "__main__":
	unittest.main()
