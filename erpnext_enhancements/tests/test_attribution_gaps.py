"""Bench-free tests for the Attribution Gaps report's classification.

The report is the instrument every stage of the attribution enablement checklist
reads (docs/attribution-runbook.md). Until v1.502.1 it filed any record carrying the
``Unknown (pre-Aug 2026)`` bucket as *Historical*, whatever its creation date -- and
the source gate tells a salesperson to pick exactly that bucket when the source is
unknown. So every bypass of the gate read as old debt, and the live-gap count stayed
at zero while the gate was being routed around. These tests pin the fix.

Installs a minimal frappe stub in ``setUpModule`` and removes it afterwards.

Run: python -m unittest erpnext_enhancements.tests.test_attribution_gaps -v
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

STUBBED = ("frappe", "frappe.utils")
OURS = (
	"erpnext_enhancements.crm_enhancements.attribution",
	"erpnext_enhancements.crm_enhancements.report.attribution_gaps.attribution_gaps",
	"erpnext_enhancements.patches.backfill_unknown_lead_source",
)
_saved = {}
report = None
attribution = None
CAPTURED = {}


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)


def setUpModule():
	global report, attribution
	for name in STUBBED + OURS:
		_saved[name] = sys.modules.pop(name, None)
	frappe = types.ModuleType("frappe")
	frappe._ = lambda text: text
	frappe._dict = _Dict

	def sql(query, values=None, as_dict=False):
		CAPTURED.setdefault("queries", []).append((query, values))
		return []

	frappe.db = types.SimpleNamespace(sql=sql, has_column=lambda doctype, column: True)
	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda value: int(value or 0)
	utils.now_datetime = lambda: None
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	from erpnext_enhancements.crm_enhancements import attribution as attr_module
	from erpnext_enhancements.crm_enhancements.report.attribution_gaps import attribution_gaps as module

	report = module
	attribution = attr_module


def tearDownModule():
	for name in STUBBED + OURS:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


BUCKET = "Unknown (pre-Aug 2026)"


class GapKindTests(unittest.TestCase):
	def test_blank_is_no_source(self):
		self.assertEqual(report.gap_kind(None, "2026-09-01"), report.GAP_NO_SOURCE)
		self.assertEqual(report.gap_kind("", "2020-01-01"), report.GAP_NO_SOURCE)

	def test_bucket_before_capture_is_history(self):
		self.assertEqual(report.gap_kind(BUCKET, "2026-07-31"), report.GAP_HISTORICAL)

	def test_bucket_on_a_new_record_is_a_live_gap(self):
		# The two Opportunities that were already hiding on prod: 2026-08-06, 2026-09-09.
		self.assertEqual(report.gap_kind(BUCKET, "2026-08-01"), report.GAP_UNKNOWN_NEW)
		self.assertEqual(report.gap_kind(BUCKET, "2026-09-09"), report.GAP_UNKNOWN_NEW)

	def test_date_objects_work_too(self):
		import datetime

		self.assertEqual(report.gap_kind(BUCKET, datetime.date(2026, 8, 6)), report.GAP_UNKNOWN_NEW)
		self.assertEqual(report.gap_kind(BUCKET, datetime.date(2025, 3, 1)), report.GAP_HISTORICAL)


class ReportShapeTests(unittest.TestCase):
	def rows(self):
		return [
			report._shape(
				{"name": "O-1", "source": BUCKET, "created": "2026-09-09", "owner_user": "a"}, "Opportunity"
			),
			report._shape(
				{"name": "O-2", "source": BUCKET, "created": "2025-01-01", "owner_user": "a"}, "Opportunity"
			),
			report._shape(
				{"name": "O-3", "source": None, "created": "2026-09-01", "owner_user": "b"}, "Opportunity"
			),
		]

	def test_new_unknown_ranks_with_the_live_gaps(self):
		ranks = {r["record"]: r["_gap_rank"] for r in self.rows()}
		self.assertEqual(ranks, {"O-1": 0, "O-2": 1, "O-3": 0})

	def test_chart_counts_every_live_gap(self):
		rows = self.rows()
		for row in rows:
			row.pop("_gap_rank")
		chart = report.get_chart(rows)
		counts = dict(zip(chart["data"]["labels"], chart["data"]["datasets"][0]["values"], strict=True))
		self.assertEqual(counts, {"a": 1, "b": 1}, "the historical row stays out, the new unknown goes in")

	def test_live_filter_keeps_a_new_unknown(self):
		condition = report._source_condition(_Dict(only_live_gaps=1), "o")
		self.assertIn("%(capture_start)s", condition)
		self.assertIn("%(bucket)s", condition)
		self.assertEqual(report._base_values(_Dict())["capture_start"], attribution.CAPTURE_START)

	def test_capture_start_matches_the_backfill_cutoff(self):
		from erpnext_enhancements.patches import backfill_unknown_lead_source as patch

		self.assertEqual(attribution.CAPTURE_START, patch.CUTOFF)


class GateMessageTests(unittest.TestCase):
	def test_the_escape_hatch_says_where_it_shows(self):
		source = (REPO_ROOT / "erpnext_enhancements" / "crm_enhancements" / "attribution.py").read_text(
			encoding="utf-8"
		)
		self.assertIn("Attribution Gaps report for", source)


if __name__ == "__main__":
	unittest.main()
