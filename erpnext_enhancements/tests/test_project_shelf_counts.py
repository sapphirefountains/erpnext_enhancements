# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for a count that names its jobs on the Project Shelf (v1.843.0).

The owner's yes of 2026-11-24 ("Yes, all recommended"): a count at the Project Shelf is a Stock
Reconciliation, which has no project, so a **Project Shelf Tag** beside it says whose units the
change was. The tag posts nothing; the count's difference stays on the stock adjustment account
with no project. Production that day had seven counts that left 218 units claimed by jobs and
gone, and a September transfer that left 13 more.

Layers, cheapest first:

* ``project_shelf_rules`` run directly: who a count's change belongs to (``count_split``: the
  default and a person's own split), a tag sharing out a movement's own change in
  ``shelf_summary`` (never more, only its sign, only while its document is live), the cleanup's
  replay (``settle_untagged``) on production's own ledger, and a past count's context.
* ``inventory_report_rules``: the over-claim the cap hides, said in the notes, and the Counted Off
  Shelf column.
* ``project_shelf``, ``bin_assignments`` and the cleanup patch on ``tests/test_project_shelf``'
  ``frappe`` stub (so this suite has its own CI step). Its Stock Reconciliation submit is made to
  do what ERPNext's does before the hook runs (current quantity, a count's ledger row, the new
  on-hand) and then call ``project_shelf.tag_reconciliation``, as ``hooks.py`` has Frappe do.
* The wiring, read from source: the hook, the patch, the DocType, the CI step, the bundle level.

What it cannot see: ERPNext's own reconciliation (its GL entries are untouched by design, and no
code here writes one), Frappe dispatching ``doc_events``, and the Desk drawing the dialog
(``scripts/test_bin_manager.mjs`` runs the page's pure split helpers).

Run: python -m unittest erpnext_enhancements.tests.test_project_shelf_counts -v
"""

import contextlib
import io
import json
import re
import unittest
from pathlib import Path

from erpnext_enhancements.inventory_enhancements import inventory_report_rules as report_rules
from erpnext_enhancements.inventory_enhancements import project_shelf_rules as rules
from erpnext_enhancements.tests import test_bin_assignments as base
from erpnext_enhancements.tests import test_project_shelf as shelf_base

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
HOOKS = APP / "hooks.py"
PATCHES_TXT = APP / "patches.txt"
DOCTYPE_DIR = APP / "inventory_enhancements" / "doctype" / "project_shelf_tag"
CI = REPO / ".github" / "workflows" / "ci.yml"
BUNDLE = APP / "public" / "js" / "bin_manager.bundle.js"
PAGE = APP / "inventory_enhancements" / "page" / "bin_manager" / "bin_manager.js"
COUNT_DESK = APP / "api" / "count_desk.py"

SHELF = shelf_base.SHELF
ITEM = shelf_base.ITEM
JOB_A = shelf_base.JOB_A
JOB_B = shelf_base.JOB_B
BIN_A = base.BIN_A
STATE = base.STATE
TAG = "Project Shelf Tag"

shelf = None
bins = None
FRAPPE = None
_ORIGINAL = {}


def setUpModule():
	global shelf, bins, FRAPPE
	shelf_base.setUpModule()
	shelf, bins, FRAPPE = shelf_base.shelf, shelf_base.bins, shelf_base.FRAPPE
	_ORIGINAL["submit"] = base.Doc.submit
	_ORIGINAL["msgprint"] = getattr(FRAPPE, "msgprint", None)

	def submit(self):
		"""What ERPNext's submit does before ``doc_events`` run, then the hook ``hooks.py`` names."""
		_ORIGINAL["submit"](self)
		if self.doctype != "Stock Reconciliation":
			return
		for row in self.items or ():
			before = base._bin_qty(row["item_code"], row["warehouse"])
			row["current_qty"] = before
			if row["warehouse"] == SHELF:
				STATE["ledger"].append(
					shelf_base._row(
						row["item_code"],
						0,
						None,
						voucher_type="Stock Reconciliation",
						voucher_no=self.name,
						after=float(row["qty"]),
						day="2026-11-24",
					)
				)
			base._bin(row["item_code"], row["warehouse"], float(row["qty"]))
		if not STATE.get("no_hook"):
			shelf.tag_reconciliation(self)

	base.Doc.submit = submit
	FRAPPE.msgprint = lambda message=None, **kwargs: STATE.setdefault("msgprints", []).append(
		(message, kwargs)
	)


def tearDownModule():
	base.Doc.submit = _ORIGINAL["submit"]
	if _ORIGINAL["msgprint"] is None:
		del FRAPPE.msgprint
	else:
		FRAPPE.msgprint = _ORIGINAL["msgprint"]
	shelf_base.tearDownModule()


def _ledger(*rows):
	"""Ledger rows in order: (item, qty, project, voucher_type, voucher_no, after, day)."""
	out = []
	for item, qty, project, vtype, vno, after, day in rows:
		out.append(
			{
				"item_code": item,
				"project": project,
				"actual_qty": qty,
				"qty_after_transaction": after,
				"voucher_type": vtype,
				"voucher_no": vno,
				"posting_date": day,
			}
		)
	return out


def _tag(vno, item, project, qty, vtype="Stock Reconciliation"):
	return {"voucher_type": vtype, "voucher_no": vno, "item_code": item, "project": project, "qty": qty}


def _held_on(item, *triples):
	"""Receipts onto the shelf on given days, ``(job, qty, day)``, and the matching on-hand. The
	day matters: a count down comes out of the oldest delivery first."""
	running = 0.0
	for job, qty, day in triples:
		running += qty
		STATE["ledger"].append(shelf_base._row(item, qty, job, after=running, day=day))
	base._bin(item, SHELF, running)


def _two_jobs():
	"""JOB_A's 5 delivered first, JOB_B's 3 later: a count down takes from JOB_A first."""
	_held_on(ITEM, (JOB_A, 5, "2026-11-01"), (JOB_B, 3, "2026-11-20"))


# --------------------------------------------------------------------------- who a count's change is


class TestCountSplit(unittest.TestCase):
	def test_a_count_down_with_one_job_holding_takes_it_all_from_that_job(self):
		split = rules.count_split(200, 0, [("PRJ-1", 200, "2026-11-23")])
		self.assertEqual(split["change"], -200)
		self.assertEqual(split["tags"], [("PRJ-1", -200)])
		self.assertFalse(split["choice"])
		self.assertEqual(split["rows"][0]["holds"], 200)

	def test_a_count_down_comes_from_the_oldest_delivery_first(self):
		claims = [("OLD", 5, "2026-11-01"), ("NEW", 3, "2026-11-20")]
		split = rules.count_split(8, 5, claims)
		self.assertEqual(split["tags"], [("OLD", -3)])
		self.assertTrue(split["choice"], "two jobs held it: a person should look")
		split = rules.count_split(8, 2, claims)
		self.assertEqual(sorted(split["tags"]), [("NEW", -1), ("OLD", -5)])

	def test_stock_no_job_claims_is_counted_off_first(self):
		split = rules.count_split(10, 7, [("A", 5, "2026-11-01")])
		self.assertEqual(split["unclaimed"], 5)
		self.assertEqual(split["tags"], [])
		self.assertEqual(split["no_job"], 3)
		self.assertEqual(rules.count_split(10, 3, [("A", 5, "2026-11-01")])["tags"], [("A", -2)])

	def test_an_over_claimed_job_loses_from_what_it_actually_holds(self):
		# A claims 5 and holds 3 (2 left without a job); B's newer delivery keeps its stock.
		split = rules.count_split(6, 4, [("A", 5, "2026-11-01"), ("B", 3, "2026-11-20")])
		self.assertEqual(split["tags"], [("A", -2)])
		row = next(r for r in split["rows"] if r["project"] == "A")
		self.assertEqual((row["holds"], row["short"]), (3, 2))

	def test_a_count_up_refills_a_short_claim_without_a_tag_then_goes_to_the_one_job(self):
		split = rules.count_split(3, 7, [("A", 5, "2026-11-01")])
		# 2 refill the claim A already had; the other 2 are found units for the only job holding it.
		self.assertEqual(split["tags"], [("A", 2)])
		self.assertEqual(split["rows"][0]["qty"], 4)
		self.assertFalse(split["choice"])

	def test_a_count_up_with_two_jobs_holding_starts_under_no_job(self):
		split = rules.count_split(8, 10, [("A", 5, "2026-11-01"), ("B", 3, "2026-11-20")])
		self.assertEqual(split["tags"], [])
		self.assertEqual(split["no_job"], 2)
		self.assertTrue(split["choice"])

	def test_a_count_up_with_no_job_holding_is_no_jobs(self):
		split = rules.count_split(0, 4, [])
		self.assertEqual((split["tags"], split["no_job"], split["choice"]), ([], 4, False))

	def test_a_count_that_changes_nothing_names_nobody(self):
		split = rules.count_split(5, 5, [("A", 5, "2026-11-01")])
		self.assertEqual((split["change"], split["tags"]), (0.0, []))

	def test_a_persons_split_down_is_written_as_they_said(self):
		claims = [("OLD", 5, "2026-11-01"), ("NEW", 3, "2026-11-20")]
		split = rules.count_split(8, 5, claims, {"NEW": 3})
		self.assertIsNone(split["problem"])
		self.assertEqual(split["tags"], [("NEW", -3)])

	def test_a_persons_split_that_does_not_fit_is_a_sentence_and_writes_nothing(self):
		claims = [("OLD", 5, "2026-11-01"), ("NEW", 3, "2026-11-20")]
		cases = [
			({"NEW": 4}, "add up to 4"),
			({"NEW": 2, "OLD": 2}, "add up to 4"),
			({"OLD": -1, "NEW": 4}, "less than nothing"),
			({"OTHER": 3}, "OTHER has none"),
			({"": 3}, "belongs to no job"),
		]
		for chosen, words in cases:
			with self.subTest(chosen=chosen):
				split = rules.count_split(8, 5, claims, chosen)
				self.assertIn(words, split["problem"])
				self.assertEqual(split["tags"], [])
		split = rules.count_split(8, 0, claims, {"NEW": 4, "OLD": 4})
		self.assertIn("NEW has only 3", split["problem"])

	def test_a_persons_split_up_may_name_any_job_and_tags_only_beyond_its_shortfall(self):
		claims = [("A", 5, "2026-11-01")]
		split = rules.count_split(3, 7, claims, {"A": 1, "C": 3})
		self.assertIsNone(split["problem"])
		# A was 2 short: its 1 refills the claim, no tag. C, not on record, gets its 3.
		self.assertEqual(split["tags"], [("C", 3)])

	def test_the_page_split_is_read_from_json_or_a_dict(self):
		self.assertIsNone(rules.chosen_split(None))
		self.assertIsNone(rules.chosen_split(""))
		self.assertEqual(rules.chosen_split('{"A": "2", "": 1}'), {"A": 2.0, "": 1.0})
		self.assertEqual(rules.chosen_split({"null": 1, " A ": 2}), {"": 1.0, "A": 2.0})
		self.assertEqual(rules.chosen_split("not json"), {})
		self.assertEqual(rules.chosen_split("[1]"), {})


# --------------------------------------------------------------------------- tags in the summary


class TestTagsInTheSummary(unittest.TestCase):
	def ledger(self):
		return _ledger(
			("THN6", 200, "PRJ-1", "Purchase Receipt", "PR-1", 200, "2026-11-23"),
			("THN6", 0, None, "Stock Reconciliation", "SR-1", 0, "2026-11-24"),
		)

	def test_without_a_tag_the_count_leaves_the_claim_and_says_so(self):
		out = rules.shelf_summary(self.ledger(), {"THN6": 0})
		self.assertEqual(out["rows"][0]["tagged"], 200)
		self.assertEqual(out["rows"][0]["qty"], 0)
		self.assertEqual(out["untagged_movements"][0]["qty"], -200)
		self.assertFalse(out["exact"])

	def test_a_tag_takes_the_count_off_the_job_and_the_shelf_is_exact(self):
		out = rules.shelf_summary(self.ledger(), {"THN6": 0}, tags=[_tag("SR-1", "THN6", "PRJ-1", -200)])
		self.assertEqual(out["rows"], [])
		self.assertEqual(out["untagged_movements"], [])
		self.assertTrue(out["exact"])

	def test_a_tag_never_shares_out_more_than_the_change_or_the_other_sign(self):
		out = rules.shelf_summary(self.ledger(), {"THN6": 0}, tags=[_tag("SR-1", "THN6", "PRJ-1", -500)])
		self.assertTrue(out["exact"])
		out = rules.shelf_summary(self.ledger(), {"THN6": 0}, tags=[_tag("SR-1", "THN6", "PRJ-1", 50)])
		self.assertEqual(out["rows"][0]["tagged"], 200, "a found-units tag cannot name a loss")

	def test_a_partial_tag_leaves_the_rest_untagged_and_counted_is_its_share(self):
		ledger = _ledger(
			("A", 6, "PRJ-1", "Purchase Receipt", "PR-1", 6, "2026-11-01"),
			("A", 4, "PRJ-2", "Purchase Receipt", "PR-2", 10, "2026-11-02"),
			("A", 0, None, "Stock Reconciliation", "SR-1", 5, "2026-11-24"),
		)
		out = rules.shelf_summary(ledger, {"A": 5}, tags=[_tag("SR-1", "A", "PRJ-1", -3)])
		rows = {r["project"]: r for r in out["rows"]}
		self.assertEqual((rows["PRJ-1"]["tagged"], rows["PRJ-1"]["counted"]), (3, -3))
		self.assertEqual(out["untagged_movements"][0]["qty"], -2)

	def test_a_tag_whose_document_has_no_live_row_counts_for_nothing(self):
		# An undone count: ERPNext marks its ledger rows canceled, the shelf's read leaves them out.
		ledger = self.ledger()[:1]
		out = rules.shelf_summary(ledger, {"THN6": 200}, tags=[_tag("SR-1", "THN6", "PRJ-1", -200)])
		self.assertEqual(out["rows"][0]["tagged"], 200)
		self.assertTrue(out["exact"])

	def test_one_documents_tags_are_used_once_across_its_rows(self):
		ledger = _ledger(
			("A", 10, "PRJ-1", "Purchase Receipt", "PR-1", 10, "2026-11-01"),
			("A", -3, None, "Stock Entry", "SE-1", 7, "2026-11-02"),
			("A", -3, None, "Stock Entry", "SE-1", 4, "2026-11-02"),
		)
		out = rules.shelf_summary(
			ledger, {"A": 4}, tags=[_tag("SE-1", "A", "PRJ-1", -3, vtype="Stock Entry")]
		)
		self.assertEqual(out["rows"][0]["tagged"], 7)
		self.assertEqual(len(out["untagged_movements"]), 1)

	def test_a_row_with_its_own_job_is_never_retagged(self):
		ledger = _ledger(
			("A", 5, "PRJ-1", "Purchase Receipt", "PR-1", 5, "2026-11-01"),
			("A", -2, "PRJ-1", "Stock Entry", "SE-1", 3, "2026-11-02"),
		)
		out = rules.shelf_summary(
			ledger, {"A": 3}, tags=[_tag("SE-1", "A", "PRJ-2", -2, vtype="Stock Entry")]
		)
		self.assertEqual([(r["project"], r["tagged"]) for r in out["rows"]], [("PRJ-1", 3)])

	def test_claims_of_reads_the_balances_and_latest_arrival(self):
		out = rules.shelf_summary(self.ledger()[:1], {"THN6": 200})
		self.assertEqual(rules.claims_of(out["rows"], "THN6"), [("PRJ-1", 200, "2026-11-23")])
		self.assertEqual(rules.claims_of(out["rows"], "OTHER"), [])


# --------------------------------------------------------------------------- the cleanup's rule


#: Production's shelf ledger for the 9 items that drifted, 2026-11-24 (the other items' rows only
#: add exact claims and are left out). One transfer of 2026-09-23, seven counts that day.
PRODUCTION = _ledger(
	("LM75-23B24", 6, "PRJ-00759", "Purchase Receipt", "MAT-PRE-2026-00024", 6, "2026-09-09"),
	("AF12Z-30-10-21", 7, "PRJ-00759", "Purchase Receipt", "MAT-PRE-2026-00032", 7, "2026-09-15"),
	("AF12Z-30-10-21", -7, None, "Stock Entry", "MAT-STE-2026-00002", 0, "2026-09-23"),
	("LM75-23B24", -6, None, "Stock Entry", "MAT-STE-2026-00002", 0, "2026-09-23"),
	("ECX2071-24R", 6, "PRJ-00759", "Purchase Receipt", "MAT-PRE-2026-00105", 6, "2026-09-28"),
	("436-020", 1, "PRJ-00604", "Purchase Receipt", "MAT-PRE-2026-00124", 1, "2026-10-07"),
	("ZEV-018568-8pk", 1, "PRJ-00746", "Purchase Receipt", "MAT-PRE-2026-00185", 1, "2026-11-12"),
	("lmus-revh-05", 8, "PRJ-00701", "Purchase Receipt", "MAT-PRE-2026-00194", 8, "2026-11-16"),
	("GCX3131", 1, "PRJ-00701", "Purchase Receipt", "MAT-PRE-2026-00199", 1, "2026-11-17"),
	("SCE-30EL2410LP", 1, "PRJ-00701", "Purchase Receipt", "MAT-PRE-2026-00201", 1, "2026-11-17"),
	("THN6", 200, "PRJ-00701", "Purchase Receipt", "MAT-PRE-2026-00218", 200, "2026-11-23"),
	("436-020", 0, None, "Stock Reconciliation", "MAT-RECO-2026-00235", 0, "2026-11-24"),
	("ECX2071-24R", 0, None, "Stock Reconciliation", "MAT-RECO-2026-00236", 0, "2026-11-24"),
	("lmus-revh-05", 0, None, "Stock Reconciliation", "MAT-RECO-2026-00237", 0, "2026-11-24"),
	("ZEV-018568-8pk", 0, None, "Stock Reconciliation", "MAT-RECO-2026-00238", 0, "2026-11-24"),
	("THN6", 0, None, "Stock Reconciliation", "MAT-RECO-2026-00239", 0, "2026-11-24"),
	("GCX3131", 0, None, "Stock Reconciliation", "MAT-RECO-2026-00240", 0, "2026-11-24"),
	("SCE-30EL2410LP", 0, None, "Stock Reconciliation", "MAT-RECO-2026-00241", 0, "2026-11-24"),
)


class TestSettleUntagged(unittest.TestCase):
	def test_production_is_settled_by_the_nine_tags_the_owner_was_shown(self):
		plan = rules.settle_untagged(PRODUCTION)
		self.assertEqual(
			[(t["voucher_no"], t["item_code"], t["project"], t["qty"]) for t in plan],
			[
				("MAT-STE-2026-00002", "AF12Z-30-10-21", "PRJ-00759", -7),
				("MAT-STE-2026-00002", "LM75-23B24", "PRJ-00759", -6),
				("MAT-RECO-2026-00235", "436-020", "PRJ-00604", -1),
				("MAT-RECO-2026-00236", "ECX2071-24R", "PRJ-00759", -6),
				("MAT-RECO-2026-00237", "lmus-revh-05", "PRJ-00701", -8),
				("MAT-RECO-2026-00238", "ZEV-018568-8pk", "PRJ-00746", -1),
				("MAT-RECO-2026-00239", "THN6", "PRJ-00701", -200),
				("MAT-RECO-2026-00240", "GCX3131", "PRJ-00701", -1),
				("MAT-RECO-2026-00241", "SCE-30EL2410LP", "PRJ-00701", -1),
			],
		)
		self.assertEqual(-sum(t["qty"] for t in plan), 231)
		self.assertFalse(any(t["choice"] for t in plan), "one job held each item: nothing was guessed")
		before = rules.shelf_summary(PRODUCTION, {})
		self.assertFalse(before["exact"])
		after = rules.shelf_summary(PRODUCTION, {}, tags=plan)
		self.assertTrue(after["exact"])
		self.assertEqual(after["untagged_movements"], [])

	def test_it_is_safe_twice(self):
		plan = rules.settle_untagged(PRODUCTION)
		self.assertEqual(rules.settle_untagged(PRODUCTION, plan), [])

	def test_a_movement_onto_the_shelf_with_no_job_is_left_alone(self):
		ledger = _ledger(("A", 4, None, "Stock Entry", "SE-1", 4, "2026-11-01"))
		self.assertEqual(rules.settle_untagged(ledger), [])

	def test_a_movement_already_tagged_is_skipped_even_in_part(self):
		ledger = _ledger(
			("A", 5, "PRJ-1", "Purchase Receipt", "PR-1", 5, "2026-11-01"),
			("A", 0, None, "Stock Reconciliation", "SR-1", 0, "2026-11-02"),
		)
		self.assertEqual(rules.settle_untagged(ledger, [_tag("SR-1", "A", "PRJ-1", -2)]), [])

	def test_two_jobs_get_the_default_and_say_it_chose(self):
		ledger = _ledger(
			("A", 5, "OLD", "Purchase Receipt", "PR-1", 5, "2026-11-01"),
			("A", 3, "NEW", "Purchase Receipt", "PR-2", 8, "2026-11-20"),
			("A", 0, None, "Stock Reconciliation", "SR-1", 6, "2026-11-24"),
		)
		plan = rules.settle_untagged(ledger)
		self.assertEqual([(t["project"], t["qty"], t["choice"]) for t in plan], [("OLD", -2, True)])

	def test_each_proposal_counts_for_the_next(self):
		ledger = _ledger(
			("A", 4, "OLD", "Purchase Receipt", "PR-1", 4, "2026-11-01"),
			("A", 4, "NEW", "Purchase Receipt", "PR-2", 8, "2026-11-20"),
			("A", -3, None, "Stock Entry", "SE-1", 5, "2026-11-21"),
			("A", -3, None, "Stock Entry", "SE-2", 2, "2026-11-22"),
		)
		plan = rules.settle_untagged(ledger)
		self.assertEqual(
			sorted((t["voucher_no"], t["project"], t["qty"]) for t in plan),
			[("SE-1", "OLD", -3), ("SE-2", "NEW", -2), ("SE-2", "OLD", -1)],
		)


class TestCountContext(unittest.TestCase):
	def test_what_the_shelf_held_just_before_a_count_without_its_own_tags(self):
		ledger = _ledger(
			("A", 5, "OLD", "Purchase Receipt", "PR-1", 5, "2026-11-01"),
			("A", 3, "NEW", "Purchase Receipt", "PR-2", 8, "2026-11-20"),
			("A", 0, None, "Stock Reconciliation", "SR-1", 6, "2026-11-24"),
			("A", 4, "NEW", "Purchase Receipt", "PR-3", 10, "2026-11-25"),
		)
		tags = [_tag("SR-1", "A", "OLD", -2)]
		before, change, claims = rules.count_context(ledger, tags, "A", "Stock Reconciliation", "SR-1")
		self.assertEqual((before, change), (8, -2))
		self.assertEqual(sorted(claims), [("NEW", 3, "2026-11-20"), ("OLD", 5, "2026-11-01")])
		self.assertIsNone(rules.count_context(ledger, tags, "A", "Stock Reconciliation", "SR-9"))


# --------------------------------------------------------------------------- the reports' notes


class TestReportRules(unittest.TestCase):
	def test_the_over_claim_is_counted_from_each_jobs_over(self):
		rows = [
			{"item_code": "A", "project": "J1", "qty": 0.0, "over": 200.0},
			{"item_code": "B", "project": "J1", "qty": 4.0, "over": 1.0},
			{"item_code": "B", "project": "", "qty": 2.0},
		]
		self.assertEqual(report_rules.shelf_over(rows), (2, 201.0))
		self.assertEqual(report_rules.shelf_gaps(rows), (1, 2))

	def test_the_note_says_the_over_claim_first_then_a_count(self):
		self.assertEqual(
			report_rules.where_note(True, 200, over=200),
			report_rules.WHERE_OVER_CLAIMED.format("200"),
		)
		self.assertEqual(report_rules.where_note(True, 0, counted_off=200), report_rules.WHERE_COUNTED_OFF)
		self.assertEqual(report_rules.where_note(True, 0), "")
		self.assertEqual(report_rules.where_note(False, 3), report_rules.WHERE_NOT_STOCK)

	def test_counted_off_is_its_own_column_and_not_located_no_longer_absorbs_it(self):
		receipts = [
			{"item_code": "THN6", "purchase_receipt": "PR-1", "stock_qty": 200, "posting_date": "2026-11-23"}
		]
		items = {"THN6": {"item_name": "Wire", "stock_uom": "Unit", "is_stock_item": 1}}
		row = report_rules.project_item_rows([], receipts, items=items, counted_off={"THN6": 200})[0]
		self.assertEqual((row["counted_off"], row["not_located"]), (200, 0))
		self.assertEqual(row["where_note"], report_rules.WHERE_COUNTED_OFF)
		row = report_rules.project_item_rows([], receipts, items=items, over_claimed={"THN6": 200})[0]
		self.assertEqual(row["not_located"], 200)
		self.assertIn("200 more on the Project Shelf", row["where_note"])


# --------------------------------------------------------------------------- the module, the page's endpoints


class _Base(unittest.TestCase):
	def setUp(self):
		shelf_base._reset()
		STATE.pop("msgprints", None)
		STATE.pop("no_hook", None)

	def assertRefused(self, fn, *words, **kwargs):
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			fn(**kwargs)
		for word in words:
			self.assertIn(word, str(caught.exception))


class TestTheBinManagersCount(_Base):
	def test_a_count_naming_the_job_writes_its_tag_and_the_shelf_reads_exact(self):
		shelf_base._held(ITEM, (JOB_A, 6))
		bins.set_count(ITEM, SHELF, 0, base._ref(), jobs=json.dumps({JOB_A: 6}))
		tags = list(base._table(TAG).values())
		self.assertEqual(
			[(t["project"], t["qty"], t["source"]) for t in tags], [(JOB_A, -6.0, rules.TAG_COUNT)]
		)
		self.assertEqual(tags[0]["warehouse"], SHELF)
		log = next(iter(base._table("Stock Scan Log").values()))
		self.assertEqual(log["project"], JOB_A)
		self.assertEqual(tags[0]["stock_scan_log"], log["name"])
		out = shelf.shelf_stock()
		self.assertTrue(out["exact"])
		self.assertEqual(out["rows"], [])

	def test_a_split_between_two_jobs_is_written_as_named(self):
		_two_jobs()
		bins.set_count(ITEM, SHELF, 4, base._ref(), jobs={JOB_B: 3, JOB_A: 1})
		self.assertEqual(
			sorted((t["project"], t["qty"]) for t in base._table(TAG).values()),
			[(JOB_A, -1.0), (JOB_B, -3.0)],
		)
		log = next(iter(base._table("Stock Scan Log").values()))
		self.assertIsNone(log.get("project"), "two jobs: the log names neither")
		rows = {r["project"]: r["qty"] for r in shelf.shelf_stock()["rows"]}
		self.assertEqual(rows, {JOB_A: 4.0})

	def test_a_split_that_does_not_add_up_refuses_before_anything_is_written(self):
		shelf_base._held(ITEM, (JOB_A, 6))
		self.assertRefused(
			bins.set_count,
			"add up to 2",
			item_code=ITEM,
			warehouse=SHELF,
			qty=0,
			client_ref=base._ref(),
			jobs={JOB_A: 2},
		)
		self.assertEqual(base._table("Stock Reconciliation"), {})
		self.assertEqual(base._table("Stock Scan Log"), {})
		self.assertEqual(base._table(TAG), {})

	def test_a_job_that_does_not_exist_is_a_sentence(self):
		shelf_base._held(ITEM, (JOB_A, 2))
		self.assertRefused(
			bins.set_count,
			"no job PRJ-404",
			item_code=ITEM,
			warehouse=SHELF,
			qty=5,
			client_ref=base._ref(),
			jobs={"PRJ-404": 3},
		)
		self.assertEqual(base._table("Stock Reconciliation"), {})

	def test_without_a_split_the_default_names_the_job(self):
		shelf_base._held(ITEM, (JOB_A, 6))
		bins.set_count(ITEM, SHELF, 2, base._ref())
		self.assertEqual(
			[(t["project"], t["qty"], t["source"]) for t in base._table(TAG).values()],
			[(JOB_A, -4.0, rules.TAG_DEFAULT)],
		)
		self.assertNotIn("msgprints", STATE, "one job held it: nothing to tell anyone")

	def test_an_ordinary_bin_counts_as_it_always_did(self):
		base._bin(ITEM, BIN_A, 4)
		bins.set_count(ITEM, BIN_A, 1, base._ref(), jobs={JOB_A: 3})
		self.assertEqual(base._table(TAG), {})
		sr = next(iter(base._table("Stock Reconciliation").values()))
		self.assertEqual(sr["items"][0]["qty"], 1.0)

	def test_the_count_itself_still_carries_no_project(self):
		shelf_base._held(ITEM, (JOB_A, 6))
		bins.set_count(ITEM, SHELF, 0, base._ref(), jobs={JOB_A: 6})
		sr = next(iter(base._table("Stock Reconciliation").values()))
		self.assertNotIn("project", sr)
		self.assertNotIn("project", sr["items"][0])

	def test_count_plan_is_the_default_for_the_dialog_and_nothing_away_from_the_shelf(self):
		_two_jobs()
		plan = bins.count_plan(ITEM, SHELF, "4")
		self.assertTrue(plan["shelf"])
		self.assertEqual(plan["change"], -4)
		self.assertTrue(plan["choice"])
		self.assertEqual({r["project"]: r["qty"] for r in plan["rows"]}, {JOB_A: 4.0, JOB_B: 0.0})
		base._bin(ITEM, BIN_A, 4)
		self.assertEqual(bins.count_plan(ITEM, BIN_A, "1"), {"shelf": False})
		self.assertEqual(bins.count_plan(ITEM, SHELF, "lots"), {"shelf": False})

	def test_the_boot_says_a_shelf_count_names_its_jobs(self):
		self.assertTrue(bins.manager_boot()["shelf_count_jobs"])
		shelf_base._reset(shelf_on=False)
		self.assertFalse(bins.manager_boot()["shelf_count_jobs"])


class TestChangeWhose(_Base):
	def test_a_counts_split_can_be_said_again(self):
		_two_jobs()
		bins.set_count(ITEM, SHELF, 5, base._ref())
		self.assertEqual([(t["project"], t["qty"]) for t in base._table(TAG).values()], [(JOB_A, -3.0)])
		answer = bins.count_whose(ITEM)["count"]
		self.assertEqual((answer["before"], answer["change"]), (8.0, -3.0))
		self.assertEqual({r["project"]: r["qty"] for r in answer["rows"]}, {JOB_A: 3.0, JOB_B: 0.0})
		out = bins.change_whose(ITEM, answer["voucher_no"], json.dumps({JOB_B: 3}))
		self.assertIn("Saved", out["message"])
		self.assertEqual(
			[(t["project"], t["qty"], t["source"]) for t in base._table(TAG).values()],
			[(JOB_B, -3.0, rules.TAG_CHANGED)],
		)
		self.assertEqual({r["project"]: r["qty"] for r in shelf.shelf_stock()["rows"]}, {JOB_A: 5.0})

	def test_a_split_that_does_not_fit_the_count_is_refused_and_keeps_the_old_one(self):
		_two_jobs()
		bins.set_count(ITEM, SHELF, 2, base._ref())
		voucher = bins.count_whose(ITEM)["count"]["voucher_no"]
		self.assertRefused(
			bins.change_whose, "has only 3", item_code=ITEM, voucher_no=voucher, jobs={JOB_B: 4, JOB_A: 2}
		)
		self.assertRefused(
			bins.change_whose, "add up to 2", item_code=ITEM, voucher_no=voucher, jobs={JOB_B: 2}
		)
		self.assertEqual(
			[(t["project"], t["qty"]) for t in base._table(TAG).values()], [(JOB_A, -5.0), (JOB_B, -1.0)]
		)

	def test_an_item_never_counted_on_the_shelf_has_no_count(self):
		shelf_base._held(ITEM, (JOB_A, 5))
		self.assertIsNone(bins.count_whose(ITEM)["count"])
		self.assertRefused(
			bins.change_whose, "not a live count", item_code=ITEM, voucher_no="SR-404", jobs={}
		)


class TestTheSubmitHook(_Base):
	def _reconcile(self, qty, quiet=False, item=ITEM):
		sr = FRAPPE.new_doc("Stock Reconciliation")
		sr.company = base.COMPANY
		sr.append("items", {"item_code": item, "warehouse": SHELF, "qty": qty})
		if quiet:
			sr.flags.shelf_quiet = True
		sr.insert()
		sr.submit()
		return sr

	def test_a_count_from_anywhere_gets_the_default_and_says_when_it_chose(self):
		_two_jobs()
		sr = self._reconcile(5)
		self.assertEqual([(t["project"], t["qty"]) for t in base._table(TAG).values()], [(JOB_A, -3.0)])
		self.assertEqual(
			sr.flags.shelf_tagged, [{"item_code": ITEM, "tags": [(JOB_A, -3.0)], "choice": True}]
		)
		message, kwargs = STATE["msgprints"][0]
		self.assertIn("Change whose", message)
		self.assertTrue(kwargs.get("alert"))

	def test_the_count_desk_says_it_itself(self):
		_two_jobs()
		self._reconcile(5, quiet=True)
		self.assertNotIn("msgprints", STATE)

	def test_a_reconciliation_away_from_the_shelf_writes_nothing(self):
		sr = FRAPPE.new_doc("Stock Reconciliation")
		sr.append("items", {"item_code": ITEM, "warehouse": BIN_A, "qty": 2})
		sr.insert()
		sr.submit()
		self.assertEqual(base._table(TAG), {})

	def test_a_default_that_fails_is_logged_and_the_count_still_posts(self):
		shelf_base._held(ITEM, (JOB_A, 5))
		real = shelf.count_plan
		shelf.count_plan = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
		try:
			self._reconcile(1)
		finally:
			shelf.count_plan = real
		self.assertEqual(base._table(TAG), {})
		self.assertTrue(any("could not name the jobs" in (title or "") for title, _m in STATE["errors"]))
		self.assertEqual(len(base._table("Stock Reconciliation")), 1)

	def test_a_named_split_that_no_longer_fits_refuses_the_submit(self):
		shelf_base._held(ITEM, (JOB_A, 5))
		sr = FRAPPE.new_doc("Stock Reconciliation")
		sr.append("items", {"item_code": ITEM, "warehouse": SHELF, "qty": 1})
		sr.flags.shelf_jobs = {ITEM: {JOB_A: 9}}
		sr.insert()
		with self.assertRaises(FRAPPE.ValidationError):
			sr.submit()
		self.assertEqual(base._table(TAG), {})

	def test_tags_that_cannot_be_read_leave_the_shelf_as_it_was(self):
		shelf_base._held(ITEM, (JOB_A, 5))
		real = FRAPPE.get_all

		def get_all(doctype, *args, **kwargs):
			if doctype == TAG:
				raise RuntimeError("no such table")
			return real(doctype, *args, **kwargs)

		FRAPPE.get_all = get_all
		try:
			out = shelf.shelf_stock()
		finally:
			FRAPPE.get_all = real
		self.assertEqual([(r["project"], r["qty"]) for r in out["rows"]], [(JOB_A, 5.0)])


class TestTheCleanupPatch(_Base):
	def _patch(self):
		from erpnext_enhancements.patches import settle_project_shelf_untagged as patch

		out = io.StringIO()
		with contextlib.redirect_stdout(out):
			patch.execute()
		return out.getvalue()

	def test_it_names_the_job_of_every_movement_off_the_shelf_and_is_safe_twice(self):
		shelf_base._held(ITEM, (JOB_A, 6))
		# A count posted before this release: no hook, so no tag.
		STATE["no_hook"] = True
		bins.set_count(ITEM, SHELF, 0, base._ref())
		STATE.pop("no_hook")
		self.assertFalse(shelf.shelf_stock()["exact"])
		printed = self._patch()
		self.assertEqual(
			[(t["project"], t["qty"], t["source"]) for t in base._table(TAG).values()],
			[(JOB_A, -6.0, rules.TAG_CLEANUP)],
		)
		self.assertIn(f"{JOB_A} -6", printed)
		self.assertIn("No GL entry", printed)
		# Every tag is printed with its name and document, and checked against the owner's approval: this
		# test world's row is not one of the 9, and none of the 9 is here.
		name = next(iter(base._table(TAG)))
		self.assertIn(f"wrote {name}: Stock Reconciliation", printed)
		self.assertIn("1 tag(s) written, 6 unit(s) named", printed)
		self.assertIn("NOT IN THE 2026-11-24 APPROVAL: SR-", printed)
		self.assertEqual(printed.count("approved but not written"), 9)
		self.assertTrue(shelf.shelf_stock()["exact"])
		again = self._patch()
		self.assertIn("already names its job", again)
		self.assertNotIn("approved but not written", again)
		self.assertEqual(len(base._table(TAG)), 1)

	def test_on_production_it_would_write_exactly_the_approved_nine(self):
		from erpnext_enhancements.patches import settle_project_shelf_untagged as patch

		plan = rules.settle_untagged(PRODUCTION)
		written = [(t["voucher_no"], t["item_code"], t["project"], t["qty"]) for t in plan]
		self.assertEqual(patch.compare(written), ([], []))
		self.assertEqual(len(patch.APPROVED), 9)
		self.assertEqual(-sum(row[3] for row in patch.APPROVED), 231)
		extra, missing = patch.compare([*written, ("MAT-RECO-2026-00242", "X", "PRJ-1", -1.0)])
		self.assertEqual((extra, missing), ([("MAT-RECO-2026-00242", "X", "PRJ-1", -1.0)], []))
		extra, missing = patch.compare(written[1:])
		self.assertEqual((extra, len(missing)), ([], 1))
		out = io.StringIO()
		with contextlib.redirect_stdout(out):
			patch._report(written)
		self.assertIn("matches the 9 rows approved on 2026-11-24", out.getvalue())
		self.assertIn("9 tag(s) written, 231 unit(s) named", out.getvalue())

	def test_it_never_raises(self):
		real = shelf.settle_plan
		shelf.settle_plan = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
		try:
			printed = self._patch()
		finally:
			shelf.settle_plan = real
		self.assertIn("could not finish", printed)
		self.assertTrue(any("cleanup" in (title or "") for title, _m in STATE["errors"]))

	def test_with_no_shelf_it_does_nothing(self):
		shelf_base._reset(shelf_on=False)
		self.assertIn("no Project Shelf", self._patch())


# --------------------------------------------------------------------------- the wiring


class TestTheWiring(unittest.TestCase):
	def test_the_hook_is_registered_on_reconciliation_submit(self):
		source = HOOKS.read_text(encoding="utf-8")
		block = re.search(r'"Stock Reconciliation": \{(.*?)\}', source, re.S)
		self.assertIsNotNone(block)
		self.assertIn(
			'"on_submit": "erpnext_enhancements.inventory_enhancements.project_shelf.tag_reconciliation"',
			block.group(1),
		)

	def test_the_patch_runs_after_model_sync(self):
		text = PATCHES_TXT.read_text(encoding="utf-8")
		post = text.split("[post_model_sync]", 1)[1]
		self.assertIn("erpnext_enhancements.patches.settle_project_shelf_untagged", post)

	def test_the_doctype_sits_in_its_module_and_its_controller_is_found(self):
		meta = json.loads((DOCTYPE_DIR / "project_shelf_tag.json").read_text(encoding="utf-8"))
		self.assertEqual((meta["name"], meta["module"]), (TAG, "Inventory Enhancements"))
		self.assertEqual(meta["in_create"], 1)
		fields = {f["fieldname"]: f for f in meta["fields"]}
		for name in ("voucher_type", "voucher_no", "item_code", "warehouse", "project", "qty", "source"):
			self.assertEqual(fields[name].get("reqd"), 1, name)
		self.assertEqual(fields["source"]["options"].split("\n"), list(rules.TAG_SOURCES))
		self.assertNotIn("Stock Manager", [p["role"] for p in meta["permissions"] if p.get("write")])
		controller = (DOCTYPE_DIR / "project_shelf_tag.py").read_text(encoding="utf-8")
		self.assertIn("class ProjectShelfTag(Document)", controller)

	def test_nothing_puts_a_project_on_a_stock_reconciliation(self):
		for path in (APP / "fixtures").glob("*.json"):
			for record in json.loads(path.read_text(encoding="utf-8")):
				if isinstance(record, dict) and record.get("dt") in (
					"Stock Reconciliation",
					"Stock Reconciliation Item",
				):
					self.assertNotEqual(record.get("fieldname"), "project", path.name)

	def test_the_count_desk_reports_the_split_itself(self):
		source = COUNT_DESK.read_text(encoding="utf-8")
		self.assertIn("sr.flags.shelf_quiet = True", source)
		self.assertIn("_shelf_note(shelf_tagged)", source)

	def test_the_page_draws_the_split_only_from_a_bundle_that_has_it(self):
		version = int(re.search(r"const VERSION = (\d+);", BUNDLE.read_text(encoding="utf-8")).group(1))
		level = int(re.search(r"const BM_SPLIT_LEVEL = (\d+);", PAGE.read_text(encoding="utf-8")).group(1))
		self.assertEqual(version, level)
		self.assertIn("this.split_ready() && this.bm.count_names_jobs", PAGE.read_text(encoding="utf-8"))

	def test_this_suite_has_its_own_ci_step(self):
		self.assertIn(
			"python -m unittest erpnext_enhancements.tests.test_project_shelf_counts",
			CI.read_text(encoding="utf-8"),
		)


if __name__ == "__main__":
	unittest.main()
