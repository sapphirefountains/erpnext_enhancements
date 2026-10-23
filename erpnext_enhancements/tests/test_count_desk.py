# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the Count Desk (v1.737.0): counts and audits on a laptop.

Three halves:

* **The rules.** ``inventory_enhancements/count_desk_rules.py`` has no Frappe in it, so every
  judgement runs here as plain Python: which bins a choice covers, the sheet's rows and summary, a
  row's save, and what posting does (the variance applied to what is on hand now; nothing posted
  for a match, a superseded count, reserved stock or an item with no cost). The summary and the row
  key are held equal to the page's JS by ``tests/data/count_desk_vectors.json``, and its paste cases
  are held to the rule (value n lands on the nth row on screen) here and run through the JS there.
* **The endpoints.** ``api/count_desk.py`` runs against a small ``frappe`` stub installed in
  ``setUpModule``, a stand-in for ``api.stock_scan`` (whose listing, accounts and rates it reuses)
  and one for ERPNext's stock ledger (``get_previous_sle``), with the **real** Inventory Count
  Session controller, so the per-item variance fix runs too. What it pins: the role gate and whose
  count is whose; the session row is locked before it is loaded; a changed count snapshots the
  system (or, under a Counted At, the ledger then) and a reason alone does not; one bad row does not
  stop the rest; a post submits **as the user** in reconciliations of at most 100 rows (the stub
  queues a longer submit, as v16 does), each with a comment, and a draft is inserted past
  permissions and dated now; a count where everything matched finalizes with no reconciliation; a
  retried post posts nothing.
* **The page.** ``scripts/test_count_desk.mjs`` under node, and static checks on the page's files:
  roles, the namespaced CSS, the bundle, the endpoints it names, the doctype fields, the hub links.

Run: python -m unittest erpnext_enhancements.tests.test_count_desk -v
"""

import ast
import copy
import datetime
import json
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
	sys.path.insert(0, str(REPO))

from erpnext_enhancements.inventory_enhancements import count_desk_rules as rules

APP = REPO / "erpnext_enhancements"
API = APP / "api" / "count_desk.py"
SCANNER = APP / "api" / "inventory_scanner.py"
STOCK_SCAN = APP / "api" / "stock_scan.py"
PAGE_DIR = APP / "inventory_enhancements" / "page" / "count_desk"
PAGE_JSON = PAGE_DIR / "count_desk.json"
PAGE_JS = PAGE_DIR / "count_desk.js"
PAGE_CSS = PAGE_DIR / "count_desk.css"
BUNDLE = APP / "public" / "js" / "count_desk.bundle.js"
LOGIC = APP / "public" / "js" / "count_desk" / "logic.js"
CONTROLLER = APP / "public" / "js" / "count_desk" / "controller.js"
HARNESS = REPO / "scripts" / "test_count_desk.mjs"
VECTORS = json.loads((APP / "tests" / "data" / "count_desk_vectors.json").read_text(encoding="utf-8"))
DOCTYPES = APP / "inventory_enhancements" / "doctype"
SESSION_JSON = DOCTYPES / "inventory_count_session" / "inventory_count_session.json"
LINE_JSON = DOCTYPES / "inventory_count_line" / "inventory_count_line.json"
SESSION_JS = DOCTYPES / "inventory_count_session" / "inventory_count_session.js"
ICONS = APP / "tests" / "data" / "desk_icon_names.txt"
OPS_HUB = APP / "enhancements_core" / "workspace" / "operations_hub" / "operations_hub.json"
INV_WS = APP / "inventory_enhancements" / "workspace" / "inventory_enhancements" / "inventory_enhancements.json"
PATCH = APP / "patches" / "count_desk_hub_links.py"
PATCHES_TXT = APP / "patches.txt"
CI = REPO / ".github" / "workflows" / "ci.yml"

USER = "parker@example.com"
OTHER = "sam@example.com"
COMPANY = "Example Co"
W1 = "Bin A1-1-1 - SF"
W2 = "Bin A1-1-2 - SF"
W10 = "Bin A1-1-10 - SF"
SHELF = "Shelf A1-1 - SF"
ROW = "Row A - SF"
ROOT = "All Warehouses - SF"
TRUCK = "Truck - SF"
OFF = "Bin Off - SF"


def _warehouses():
	return [
		dict(name=ROOT, warehouse_name="All Warehouses", is_group=1, lft=1, rgt=20, parent_warehouse=None),
		dict(name=ROW, warehouse_name="Row A", is_group=1, lft=2, rgt=15, parent_warehouse=ROOT),
		dict(name=SHELF, warehouse_name="Shelf A1-1", is_group=1, lft=3, rgt=14, parent_warehouse=ROW),
		dict(name=W10, warehouse_name="Bin A1-1-10", is_group=0, lft=4, rgt=5, parent_warehouse=SHELF),
		dict(name=W2, warehouse_name="Bin A1-1-2", is_group=0, lft=6, rgt=7, parent_warehouse=SHELF),
		dict(name=W1, warehouse_name="Bin A1-1-1", is_group=0, lft=8, rgt=9, parent_warehouse=SHELF),
		dict(name=OFF, warehouse_name="Bin Off", is_group=0, disabled=1, lft=10, rgt=11, parent_warehouse=SHELF),
		dict(name=TRUCK, warehouse_name="Truck", is_group=0, warehouse_type="Transit", lft=16, rgt=17, parent_warehouse=ROOT),
	]


# =========================================================================== the rules


class TestScope(unittest.TestCase):
	def test_a_group_counts_every_countable_bin_under_it_in_shelf_order(self):
		bins, problems = rules.expand_scope([SHELF], _warehouses())
		self.assertEqual(bins, [W1, W2, W10])
		self.assertEqual(problems, [])

	def test_leaves_repeats_and_problems(self):
		bins, problems = rules.expand_scope([W2, W2, TRUCK, OFF, "Nope", ROW], _warehouses())
		self.assertEqual(bins, [W1, W2, W10])
		self.assertEqual(
			problems,
			[
				"Truck is a transit warehouse, not a place stock is kept.",
				"Bin Off is disabled.",
				"There is no location called Nope.",
			],
		)

	def test_an_empty_group_is_said(self):
		rows = _warehouses() + [dict(name="Row E", warehouse_name="Row E", is_group=1, lft=18, rgt=19)]
		self.assertEqual(rules.expand_scope(["Row E"], rows), ([], ["Row E has no locations to count."]))

	def test_check_scope(self):
		self.assertEqual(rules.check_scope([], []), "Choose at least one location to count.")
		self.assertEqual(rules.check_scope([], ["X."]), "X.")
		self.assertIsNone(rules.check_scope([W1], ["X."]))
		self.assertIn("one count can cover", rules.check_scope(["b"] * (rules.MAX_SCOPE + 1), []))

	def test_scope_is_a_json_list(self):
		self.assertEqual(rules.parse_scope('["a", "b", "a", " "]'), ["a", "b"])
		self.assertEqual(rules.parse_scope("not json"), [])
		self.assertEqual(rules.parse_scope(None), [])
		self.assertEqual(json.loads(rules.dump_scope(["a", "a", "b"])), ["a", "b"])

	def test_titles(self):
		self.assertEqual(rules.default_title([]), "Count")
		self.assertEqual(rules.default_title(["Shelf B1-2"]), "Shelf B1-2")
		self.assertEqual(rules.default_title(["A", "B"]), "A and B")
		self.assertEqual(rules.default_title(["A", "B", "C", "D"]), "A + 3 more")
		self.assertEqual(rules.check_title("  two   spaces "), "two spaces")
		self.assertEqual(len(rules.check_title("x" * 500)), rules.TITLE_MAX)


class TestKeysAndSummary(unittest.TestCase):
	def test_the_key_agrees_with_the_page(self):
		for v in VECTORS["keys"]:
			with self.subTest(v["item_code"]):
				self.assertEqual(rules.line_key(v["item_code"], v["warehouse"], v["storage_location"]), v["key"])
				self.assertEqual(
					rules.split_key(v["key"]),
					{"warehouse": v["warehouse"], "storage_location": v["storage_location"], "item_code": v["item_code"]},
				)
		self.assertIsNone(rules.split_key("nope"))

	def test_the_summary_agrees_with_the_page(self):
		for v in VECTORS["summaries"]:
			with self.subTest(v["name"]):
				self.assertEqual(rules.summary(v["rows"]), v["summary"])


def _line(item, wh, counted, system, when="2026-10-20 10:00:00", **extra):
	return dict(
		item_code=item, warehouse=wh, counted_qty=counted, system_qty=system, scan_time=when, **extra
	)


class TestGroupsAndTheSheet(unittest.TestCase):
	def test_a_variance_is_the_items_over_every_storage_location(self):
		lines = [
			_line("A", W1, 5, 10, "2026-10-20 10:00:00", storage_location="L1"),
			_line("A", W1, 4, 11, "2026-10-20 11:00:00", storage_location="L2", reason_code="Other"),
			_line("B", W1, 2, 2),
		]
		g = rules.groups(lines)
		self.assertEqual(g[("A", W1)]["counted"], 9)
		self.assertEqual(g[("A", W1)]["snapshot"], 11, "the latest line's on-hand")
		self.assertEqual(g[("A", W1)]["variance"], -2)
		self.assertEqual(g[("A", W1)]["reason_code"], "Other")
		self.assertEqual(g[("B", W1)]["variance"], 0.0)

	def test_the_sheet_lists_counted_lines_and_uncovered_expected_items(self):
		expected = {
			W1: [
				dict(item_code="A", item_name="AY", stock_uom="Unit", assigned=True, on_order=0),
				dict(item_code="C", item_name="CEE", stock_uom="Unit", on_order=3),
			],
			W2: [],
		}
		lines = [_line("A", W1, 7, 10, idx=1, storage_location="L1"), _line("Z", W1, 1, 0, idx=2)]
		balances = {("A", W1): dict(actual_qty=8, valuation_rate=2.5), ("C", W1): dict(actual_qty=3, reserved=1)}
		items = {
			"A": dict(item_name="AY", stock_uom="Unit", is_stock_item=1, whole_number=True),
			"C": dict(item_name="CEE", stock_uom="Unit", is_stock_item=1),
			"Z": dict(item_name="ZED", stock_uom="FT", is_stock_item=1, has_serial_no=1),
		}
		rows = rules.sheet_rows(expected, lines, balances, items, {W1: "Bin A1-1-1"})
		by = {r["item_code"]: r for r in rows}
		self.assertEqual(sorted(by), ["A", "C", "Z"], "A is covered by its L1 line; C is uncounted")
		a = by["A"]
		self.assertEqual((a["counted"], a["counted_qty"], a["snapshot_qty"], a["system_qty"]), (True, 7, 10, 8))
		self.assertEqual((a["variance"], a["moved"], a["value_variance"]), (-3, -2, -7.5))
		self.assertTrue(a["expected"] and a["assigned"] and a["whole_number"])
		c = by["C"]
		self.assertEqual((c["counted"], c["counted_qty"], c["variance"], c["reserved"], c["on_order"]), (False, None, None, 1, 3))
		self.assertEqual(c["key"], rules.line_key("C", W1))
		self.assertFalse(by["Z"]["expected"])
		self.assertIn("Serialized", by["Z"]["refusal"])
		self.assertEqual([r["item_code"] for r in rows], ["C", "Z", "A"], "storage location sorts after the bin itself")

	def test_refusals(self):
		self.assertEqual(rules.item_refusal(None), "This item no longer exists.")
		self.assertIn("disabled", rules.item_refusal(dict(disabled=1, is_stock_item=1)))
		self.assertIn("not kept in stock", rules.item_refusal(dict(is_stock_item=0)))
		self.assertIn("Batch", rules.item_refusal(dict(is_stock_item=1, has_batch_no=1)))
		self.assertIn("template", rules.item_refusal(dict(is_stock_item=1, has_variants=1)))
		self.assertIsNone(rules.item_refusal(dict(is_stock_item=1)))


class TestAnEntry(unittest.TestCase):
	KEY = rules.line_key("A", W1)

	def test_a_count(self):
		self.assertEqual(
			rules.check_entry({"key": self.KEY, "counted_qty": "4"}, [W1], None),
			({"warehouse": W1, "storage_location": None, "item_code": "A", "counted_qty": 4.0}, None),
		)

	def test_clear_reason_and_flag(self):
		values, problem = rules.check_entry(
			{"key": self.KEY, "counted_qty": None, "reason_code": "Other", "reason": " lost ", "recount": True}, [W1], None
		)
		self.assertIsNone(problem)
		self.assertEqual((values["clear"], values["reason_code"], values["reason"], values["recount"]), (True, "Other", "lost", 1))

	def test_refusals(self):
		self.assertEqual(rules.check_entry("x", [], None)[1], "That row could not be read.")
		self.assertEqual(rules.check_entry({"key": "bad"}, [], None)[1], "That row could not be read.")
		self.assertIn("not part of this count", rules.check_entry({"key": self.KEY, "counted_qty": 1}, [W2], None)[1])
		self.assertEqual(rules.check_entry({"key": self.KEY, "counted_qty": 1}, [W1], "Nope.")[1], "Nope.")
		self.assertEqual(rules.check_entry({"key": self.KEY, "counted_qty": -1}, [W1], None)[1], "A count cannot be less than zero.")
		self.assertIn("whole numbers", rules.check_entry({"key": self.KEY, "counted_qty": 1.5}, [W1], None, True, "Unit")[1])
		self.assertIn("not one of the reasons", rules.check_entry({"key": self.KEY, "reason_code": "Gremlins"}, [W1], None)[1])

	def test_a_phone_count_counts_anywhere_and_a_refused_item_can_still_be_cleared(self):
		self.assertIsNone(rules.check_entry({"key": self.KEY, "counted_qty": 1}, [], None)[1])
		self.assertIsNone(rules.check_entry({"key": self.KEY, "counted_qty": None}, [W1], "Nope.")[1])


class TestThePostPlan(unittest.TestCase):
	def _plan(self, lines, balances, **kw):
		return rules.post_plan(
			rules.groups(lines),
			balances,
			kw.get("refusals", {}),
			kw.get("superseded", set()),
			kw.get("rate_for", lambda i, w: 0),
			require_reason=kw.get("require_reason", False),
		)

	def test_the_variance_is_applied_to_what_is_on_hand_now(self):
		plan = self._plan([_line("A", W1, 7, 10)], {("A", W1): dict(actual_qty=8)})
		(row,) = plan["adjust"]
		self.assertEqual((row["qty"], row["current"], row["variance"], row["valuation_rate"]), (5, 8, -3, None))

	def test_a_match_posts_nothing(self):
		plan = self._plan([_line("A", W1, 10, 10)], {("A", W1): dict(actual_qty=4)})
		self.assertEqual((plan["adjust"], len(plan["matched"])), ([], 1))

	def test_below_zero_posts_zero_and_already_zero_posts_nothing(self):
		plan = self._plan([_line("A", W1, 2, 10), _line("B", W1, 2, 10)], {("A", W1): dict(actual_qty=5), ("B", W1): dict(actual_qty=0)})
		(a,) = plan["adjust"]
		self.assertEqual((a["item_code"], a["qty"], a["clamped"]), ("A", 0, True))
		(b,) = plan["skipped"]
		self.assertIn("Nothing is on hand", b["reason"])

	def test_from_empty_it_needs_a_cost(self):
		lines = [_line("A", W1, 3, 0), _line("B", W1, 3, 0)]
		plan = self._plan(lines, {}, rate_for=lambda item, wh: 4.5 if item == "A" else 0)
		self.assertEqual([(r["item_code"], r["qty"], r["valuation_rate"]) for r in plan["adjust"]], [("A", 3, 4.5)])
		self.assertIn("no cost on record", plan["skipped"][0]["reason"])

	def test_superseded_reserved_refused_and_reasons(self):
		lines = [_line("A", W1, 1, 2), _line("B", W1, 1, 2), _line("C", W1, 1, 2), _line("D", W1, 1, 2, reason="x")]
		plan = self._plan(
			lines,
			{("B", W1): dict(actual_qty=2, reserved=1), ("D", W1): dict(actual_qty=2)},
			superseded={("A", W1)},
			refusals={"C": "Nope."},
			require_reason=True,
		)
		reasons = {r["item_code"]: r["reason"] for r in plan["skipped"]}
		self.assertIn("posted after yours", reasons["A"])
		self.assertIn("reserved", reasons["B"])
		self.assertEqual(reasons["C"], "Nope.")
		self.assertEqual([r["item_code"] for r in plan["adjust"]], ["D"])
		plan = self._plan([_line("E", W1, 1, 2)], {("E", W1): dict(actual_qty=2)}, require_reason=True)
		self.assertEqual([r["item_code"] for r in plan["needs_reason"]], ["E"])

	def test_the_reconciliation_comment_and_the_message(self):
		adjust = [dict(item_code=f"I{i}", warehouse=W1, reason_code="Other", reason="x" * 40) for i in range(80)]
		adjust += [dict(item_code="J", warehouse=W1, reason_code="", reason="by hand"), dict(item_code="K", warehouse=W1)]
		text = rules.reconciliation_comment("INV-1", "Shelf <2>", "Parker", adjust, link="https://x/desk/inventory-count-session/INV-1", part=2, parts=3)
		self.assertTrue(
			text.startswith(
				'<p>From count <a href="https://x/desk/inventory-count-session/INV-1">INV-1</a> (Shelf &lt;2&gt;), counted by Parker '
				"on the Count Desk. 82 item(s) adjusted here, part 2 of 3.</p>"
			)
		)
		self.assertIn("<p>Reasons: Other: 80 · No reason given: 1 · Note only: 1</p>", text)
		self.assertEqual(text.count("<li>"), rules.COMMENT_ITEMS + 1, "50 items by name, then how many more")
		self.assertIn("<li>and 31 more on INV-1</li>", text)
		self.assertNotIn("part", rules.reconciliation_comment("INV-1", "", "", adjust[:1]))
		plan = {"adjust": [{}], "matched": [{}, {}], "skipped": [{}]}
		self.assertEqual(
			rules.result_message(plan, ["SR-1"], []),
			"Posted 1 adjustment(s) on SR-1. 2 item(s) matched the system. 1 item(s) were not posted; see the list.",
		)
		self.assertIn("for a Stock Manager", rules.result_message(plan, [], ["SR-2"]))
		self.assertEqual(rules.result_message({"adjust": [], "matched": [], "skipped": []}, [], []), "Nothing needed adjusting.")


class TestPostingInParts(unittest.TestCase):
	def test_batches_of_at_most_100(self):
		self.assertEqual(rules.RECONCILIATION_ROWS, 100, "ERPNext v16 queues the submit of more than 100")
		self.assertEqual([len(b) for b in rules.batches(range(365))], [100, 100, 100, 65])
		self.assertEqual([len(b) for b in rules.batches(range(100))], [100])
		self.assertEqual(rules.batches([]), [])
		self.assertEqual(rules.batches([1, 2, 3], 2), [[1, 2], [3]])

	def test_every_reconciliation_a_count_made(self):
		self.assertEqual(rules.reconciliation_names("SR-1\nSR-2\n\nSR-3", "SR-1"), ["SR-1", "SR-2", "SR-3"])
		self.assertEqual(rules.reconciliation_names(None, "SR-9"), ["SR-9"], "a phone count, or one posted before the list")
		self.assertEqual(rules.reconciliation_names("", None), [])
		self.assertEqual(rules.dump_names(["SR-1", "SR-2", "SR-1"]), "SR-1\nSR-2")
		self.assertEqual(rules.combined_status(["Submitted", "Submitted"]), "Submitted")
		self.assertEqual(rules.combined_status(["Submitted", "Cancelled"]), "Mixed")
		self.assertIsNone(rules.combined_status([None]))


class TestCountedAtRules(unittest.TestCase):
	CREATED = datetime.datetime(2026, 10, 20, 13, 0)
	NOW = datetime.datetime(2026, 10, 20, 14, 0)

	def test_blank_is_a_live_count(self):
		self.assertEqual(rules.check_counted_at(None, self.CREATED, self.NOW), (None, None))
		self.assertEqual(rules.check_counted_at("  ", self.CREATED, self.NOW), (None, None))

	def test_a_time_before_the_count_was_opened_within_a_week(self):
		when, problem = rules.check_counted_at("2026-10-20 09:00:00", self.CREATED, self.NOW)
		self.assertEqual((when, problem), (datetime.datetime(2026, 10, 20, 9, 0), None))
		self.assertEqual(rules.check_counted_at("2026-10-14 08:30", "2026-10-20 13:00:00.123456", self.NOW)[0], datetime.datetime(2026, 10, 14, 8, 30))
		_, problem = rules.check_counted_at("2026-10-13 12:59", self.CREATED, self.NOW)
		self.assertIn("at most 7 days before the count was started (2026-10-20 13:00)", problem)

	def test_not_in_the_future_but_a_fast_clock_is_now(self):
		_, problem = rules.check_counted_at("2026-10-20 14:10", self.CREATED, self.NOW)
		self.assertIn("in the future", problem)
		self.assertEqual(rules.check_counted_at("2026-10-20 14:03", self.CREATED, self.NOW)[0], self.NOW)

	def test_unreadable(self):
		self.assertIn("is not a date and time", rules.check_counted_at("yesterday-ish", self.CREATED, self.NOW)[1])

	def test_the_sheet_counts_an_uncounted_row_against_the_ledger_then(self):
		expected = {W1: [dict(item_code="A", item_name="AY"), dict(item_code="C", item_name="CEE")]}
		lines = [_line("A", W1, 7, 10)]
		balances = {("A", W1): dict(actual_qty=8), ("C", W1): dict(actual_qty=3)}
		rows = {r["item_code"]: r for r in rules.sheet_rows(expected, lines, balances, {}, {}, as_of={("C", W1): 5})}
		self.assertEqual((rows["C"]["as_of_qty"], rows["C"]["system_qty"]), (5, 3))
		self.assertEqual(rows["A"]["as_of_qty"], 10, "a counted row: its snapshot")
		live = rules.sheet_rows(expected, lines, balances, {}, {})
		self.assertEqual([r["as_of_qty"] for r in live], [None, None])


class TestThePaste(unittest.TestCase):
	"""The page lays a pasted column down the rows on screen (logic.fill_plan, run on these vectors by
	scripts/test_count_desk.mjs). This side holds the vectors to the rule itself: value n lands on the
	nth row from the start, whatever that row is, so nothing lands a row high."""

	def test_each_value_lands_on_its_own_row(self):
		for v in VECTORS["pastes"]:
			with self.subTest(v["name"]):
				items = [r["item_code"] for r in v["rows"]]
				refused = {r["item_code"] for r in v["rows"] if r.get("refusal")}
				start = items.index(v["start"])
				entries = {item: qty for item, qty in v["entries"]}
				said = list(entries) + v["problems"] + v["refused"]
				self.assertEqual(len(said), len(set(said)), "a row takes one value")
				landed = []
				for n, text in enumerate(v["values"]):
					if start + n >= len(items) or not text.strip():
						continue
					item = items[start + n]
					landed.append(item)
					if item in refused:
						self.assertIn(item, v["refused"])
					elif item in entries:
						self.assertEqual(entries[item], float(text) if re.fullmatch(r"\d+(\.\d+)?", text) else entries[item])
					else:
						self.assertIn(item, v["problems"])
				self.assertEqual(sorted(landed), sorted(said), "nothing lands anywhere else")
				self.assertTrue(refused.isdisjoint(entries), "a refused row keeps nothing")


class TestCoverage(unittest.TestCase):
	def test_coverage_counts_bins_holding_stock(self):
		bins = [
			dict(on_hand_items=2, last_counted="2026-10-01"),
			dict(on_hand_items=1, last_counted="2026-01-01"),
			dict(on_hand_items=1, last_counted=None),
			dict(on_hand_items=0, last_counted=None),
		]
		self.assertEqual(rules.coverage(bins, "2026-10-20"), {"bins": 3, "counted": 1, "pct": 33.3, "never": 1})
		self.assertEqual(rules.coverage([], "2026-10-20")["pct"], None)

	def test_dates(self):
		self.assertEqual(rules.days_since("2026-10-18 09:00:00", "2026-10-20"), 2)
		self.assertIsNone(rules.days_since(None, "2026-10-20"))
		self.assertEqual(rules.latest(None, "2026-10-01", datetime.datetime(2026, 10, 5, 8)), "2026-10-05")
		self.assertIsNone(rules.latest(None, ""))

	def test_accuracy(self):
		self.assertEqual(rules.accuracy([_line("A", W1, 1, 1), _line("B", W1, 1, 2)]), (1, 2))


class TestRulesMatchTheCode(unittest.TestCase):
	def _constant(self, path, name):
		for node in ast.parse(path.read_text(encoding="utf-8")).body:
			if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
				return ast.literal_eval(node.value.args[0] if isinstance(node.value, ast.Call) else node.value)
		raise AssertionError(name)

	def test_the_roles_are_the_phone_pages(self):
		self.assertEqual(set(self._constant(SCANNER, "ALLOWED_ROLES")), set(rules.COUNT_ROLES))
		self.assertEqual(set(self._constant(SCANNER, "SUPERVISOR_ROLES")), set(rules.SUPERVISOR_ROLES))

	def test_the_reason_codes_are_the_selects_options(self):
		fields = {f["fieldname"]: f for f in json.loads(LINE_JSON.read_text(encoding="utf-8"))["fields"]}
		self.assertEqual(fields["reason_code"]["options"].split("\n"), [""] + list(rules.REASON_CODES))

	def test_the_rules_import_no_frappe(self):
		tree = ast.parse((APP / "inventory_enhancements" / "count_desk_rules.py").read_text(encoding="utf-8"))
		names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
		names += [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
		self.assertFalse([n for n in names if n and n.split(".")[0] == "frappe"])


# =========================================================================== the endpoints

STATE = {}
FRAPPE = None
CD = None
_SAVED_MODULES = {}
_OWN = (
	"frappe",
	"frappe.utils",
	"frappe.model",
	"frappe.model.document",
	"erpnext_enhancements.api.stock_scan",
	"erpnext_enhancements.api.count_desk",
	"erpnext_enhancements.inventory_enhancements.doctype.inventory_scanner_settings.inventory_scanner_settings",
	"erpnext_enhancements.inventory_enhancements.doctype.inventory_count_session.inventory_count_session",
	"erpnext",
	"erpnext.stock",
	"erpnext.stock.stock_ledger",
)


class _Dict(dict):
	__getattr__ = dict.get

	def __setattr__(self, key, value):
		self[key] = value


class ValidationError(Exception):
	pass


class PermissionError_(ValidationError):  # noqa: N801
	pass


class DoesNotExistError(ValidationError):
	pass


class Row:
	"""A child row: attribute and dict access, as frappe's BaseDocument gives them."""

	def __init__(self, data):
		self.__dict__.update(data)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)

	def set(self, key, value):
		self.__dict__[key] = value

	def as_dict(self):
		return _Dict(self.__dict__)

	def __getattr__(self, name):
		return None


class Document:
	"""Just enough of frappe's Document for the session and a Stock Reconciliation."""

	def __init__(self, data=None):
		data = dict(data or {})
		self.__dict__["_children"] = {}
		for key, value in data.items():
			if isinstance(value, list):
				for row in value:
					self.append(key, row)
			else:
				self.__dict__[key] = value

	def __getattr__(self, name):
		if name in self.__dict__["_children"]:
			return self.__dict__["_children"][name]
		if name in ("lines", "items"):
			# A table field of a new document is an empty list in frappe, never None.
			return self.__dict__["_children"].setdefault(name, [])
		return None

	def __setattr__(self, name, value):
		if name == "lines" or name == "items":
			self.__dict__["_children"][name] = list(value)
		else:
			self.__dict__[name] = value

	def get(self, key, default=None):
		if key in self.__dict__["_children"]:
			return self.__dict__["_children"][key]
		return self.__dict__.get(key, default)

	def set(self, key, value):
		setattr(self, key, value)

	def append(self, field, data):
		rows = self.__dict__["_children"].setdefault(field, [])
		row = Row(dict(data, idx=len(rows) + 1))
		rows.append(row)
		return row

	def _check(self, ptype, ignore_permissions):
		if not ignore_permissions and not FRAPPE.has_permission(self.doctype, ptype):
			raise PermissionError_(f"no {ptype} on {self.doctype}")

	def insert(self, ignore_permissions=False):
		self._check("create", ignore_permissions)
		STATE["inserted"].append((self.doctype, ignore_permissions))
		if self.doctype == "Stock Reconciliation":
			STATE["sr_seq"] += 1
			self.name = f"MAT-RECO-{STATE['sr_seq']}"
			self.docstatus = 0
			STATE["srs"][self.name] = self
		else:
			STATE["seq"] += 1
			self.name = f"INV-CNT-{STATE['seq']}"
			self.creation = "2026-10-20 13:00:00"
			self.modified = "2026-10-20 12:00:00"
			if hasattr(self, "validate"):
				self.validate()
			STATE["sessions"][self.name] = self
		return self

	def submit(self):
		self._check("submit", False)
		# ERPNext v16 StockReconciliation.submit: more than 100 rows is queue_action("submit"), and
		# the document comes back still a draft. STATE["queue_submits"] queues any length.
		if self.doctype == "Stock Reconciliation" and (len(self.items) > 100 or STATE.get("queue_submits")):
			STATE["queued"].append(self.name)
			return
		self.docstatus = 1
		STATE["submitted"].append(self.name)

	def add_comment(self, comment_type="Comment", text=None, **kw):
		STATE["comments"].append((self.doctype, self.name, comment_type, text))

	def save(self, ignore_permissions=False):
		self._check("write", ignore_permissions)
		if hasattr(self, "validate"):
			self.validate()
		STATE["saves"].append(self.name)
		STATE["sessions"][self.name] = self
		return self

	def db_set(self, field, value=None):
		values = field if isinstance(field, dict) else {field: value}
		for key, val in values.items():
			self.__dict__[key] = val
		STATE["db_sets"].append(values)


def _frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError_
	frappe.DoesNotExistError = DoesNotExistError
	frappe._dict = _Dict
	frappe._ = lambda text: text
	frappe.session = types.SimpleNamespace(user=USER)

	def throw(message, exc=ValidationError):
		raise exc(message)

	frappe.throw = throw
	frappe.get_roles = lambda user=None: list(STATE["roles"])
	frappe.has_permission = lambda doctype, ptype="read", *a, **k: (doctype, ptype) in STATE["perms"]
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)

	def get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, limit=None, **kw):
		STATE["get_all"].append((doctype, copy.deepcopy(filters), fields))
		rows = [_Dict(r) for r in STATE["tables"].get(doctype, [])]
		if doctype == "Stock Reconciliation":
			rows = [
				_Dict(name=sr.name, docstatus=sr.docstatus, difference_amount=sr.difference_amount or 0)
				for sr in STATE["srs"].values()
			]
		if doctype == "Inventory Count Session":
			rows = [
				_Dict(scope=d.scope, **{k: v for k, v in d.__dict__.items() if not k.startswith("_") and k != "scope"})
				for d in STATE["sessions"].values()
			]
		if doctype == "Inventory Count Line":
			rows = [
				_Dict(parent=d.name, **line.as_dict())
				for d in STATE["sessions"].values()
				for line in (d.lines or [])
			]
		for key, cond in (filters or {}).items():
			rows = [r for r in rows if _matches(r.get(key), cond)]
		if pluck:
			return [r.get(pluck) for r in rows]
		return rows

	frappe.get_all = get_all

	def get_doc(doctype, name=None):
		if isinstance(doctype, dict):
			from erpnext_enhancements.inventory_enhancements.doctype.inventory_count_session.inventory_count_session import (
				InventoryCountSession,
			)

			data = dict(doctype)
			return InventoryCountSession(data)
		STATE["get_doc"].append(name)
		return STATE["sessions"][name]

	frappe.get_doc = get_doc
	frappe.new_doc = lambda doctype: Document({"doctype": doctype})
	frappe.get_cached_value = lambda doctype, name, field: (
		next((r for r in STATE["tables"].get(doctype, []) if r["name"] == name), {}).get(field)
	)

	db = types.SimpleNamespace()

	def exists(doctype, name=None):
		if doctype == "Inventory Count Session":
			return name in STATE["sessions"]
		return any(r["name"] == name for r in STATE["tables"].get(doctype, []))

	def get_value(doctype, name, field=None, as_dict=False, **kw):
		if doctype == "Stock Reconciliation":
			sr = STATE["srs"].get(name)
			return _Dict(name=sr.name, docstatus=sr.docstatus, difference_amount=0) if sr else None
		row = next((r for r in STATE["tables"].get(doctype, []) if r["name"] == name), None)
		if not row:
			return None
		if isinstance(field, list):
			return _Dict({f: row.get(f) for f in field})
		return row.get(field)

	def sql(query, values=None, as_dict=False, **kw):
		text = " ".join(query.split())
		STATE["sql"].append(text)
		if "for update" in text:
			return []
		if "from `tabBin` group by warehouse" in text:
			return [_Dict(r) for r in STATE["bin_stats"]]
		if "voucher_type = 'Stock Reconciliation'" in text and "group by warehouse" in text and "max(posting_date)" in text:
			return [_Dict(r) for r in STATE["ledger_last"]]
		if "max(l.scan_time)" in text:
			return []
		if "timestamp(posting_date, posting_time)" in text:
			return [_Dict(r) for r in STATE["ledger_pairs"]]
		if "sum(difference_amount)" in text and "name in %(names)s" in text:
			names = set(values["names"])
			return [(sum(sr.difference_amount or 0 for sr in STATE["srs"].values() if sr.name in names and sr.docstatus == 1),)]
		if "count(*) as n" in text:
			return []
		raise AssertionError(f"unexpected SQL: {text[:120]}")

	db.exists = exists
	db.get_value = get_value
	db.sql = sql
	db.has_column = lambda doctype, column: True
	frappe.db = db

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(float(v)) if v not in (None, "") else 0
	utils.flt = lambda v, *a: float(v) if v not in (None, "") else 0.0
	utils.cstr = lambda v: "" if v is None else str(v)
	utils.now_datetime = lambda: datetime.datetime(2026, 10, 20, 14, 0, 0)
	utils.nowdate = lambda: "2026-10-20"
	utils.nowtime = lambda: "14:00:00"
	utils.add_days = lambda d, n: (datetime.date.fromisoformat(str(d)) + datetime.timedelta(days=n)).isoformat()
	utils.get_datetime = lambda v: v if isinstance(v, datetime.datetime) else datetime.datetime.fromisoformat(str(v)[:19])
	utils.get_url_to_form = lambda doctype, name: f"https://erp.example.com/desk/{doctype.lower().replace(' ', '-')}/{name}"
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = Document
	model.document = document
	frappe.model = model
	return frappe, utils, model, document


def _matches(value, cond):
	if isinstance(cond, list):
		op, arg = cond[0], cond[1]
		if op == "in":
			return value in arg
		if op == "is":
			return bool(value) if arg == "set" else not value
		if op == ">=":
			return value is not None and str(value) >= str(arg)
		raise AssertionError(op)
	return value == cond


def _scan_stub():
	scan = types.ModuleType("erpnext_enhancements.api.stock_scan")

	def items_at(row, limit=150):
		STATE["listing_limits"].append(limit)
		rows = STATE["listing"].get(row.name, [])
		return (rows if limit is None else rows[:limit]), False

	scan._items_at = items_at
	scan._warehouse = lambda name: next((_Dict(r) for r in STATE["tables"]["Warehouse"] if r["name"] == name), None)
	scan._location_problem = lambda row, name="": None if row and not row.get("is_group") else f"No {name}."
	scan._full_name = lambda user: {"parker@example.com": "Parker Bailey", "sam@example.com": "Sam"}.get(user, user)
	scan._difference_account = lambda company, item_code, configured=None: "5119 - Stock Adjustment - SF"
	scan._cost_center = lambda configured, company: "Main - SF" if configured else None
	scan._receipt_rate = lambda item_code, warehouse, company: STATE["rates"].get(item_code, 0)
	return scan


def _ledger_stub():
	"""``erpnext.stock.stock_ledger.get_previous_sle`` over ``STATE["sle"]``: the last entry of the
	pair at or before ``posting_date posting_time``, as ERPNext's ``<=`` and ``desc`` query reads it."""
	erpnext = types.ModuleType("erpnext")
	stock = types.ModuleType("erpnext.stock")
	ledger = types.ModuleType("erpnext.stock.stock_ledger")

	def get_previous_sle(args, for_update=False, extra_cond=None, for_report=False):
		STATE["sle_reads"].append(dict(args))
		at = datetime.datetime.fromisoformat(f"{args['posting_date']} {args['posting_time']}")
		rows = [
			r
			for r in STATE["sle"]
			if r["item_code"] == args["item_code"] and r["warehouse"] == args["warehouse"] and r["at"] <= at
		]
		return _Dict(rows[-1]) if rows else {}

	ledger.get_previous_sle = get_previous_sle
	stock.stock_ledger = ledger
	erpnext.stock = stock
	return {"erpnext": erpnext, "erpnext.stock": stock, "erpnext.stock.stock_ledger": ledger}


def setUpModule():
	global FRAPPE, CD
	for name in _OWN:
		_SAVED_MODULES[name] = sys.modules.pop(name, None)
	FRAPPE, utils, model, document = _frappe_stub()
	sys.modules.update({"frappe": FRAPPE, "frappe.utils": utils, "frappe.model": model, "frappe.model.document": document})
	sys.modules.update(_ledger_stub())
	sys.modules["erpnext_enhancements.api.stock_scan"] = _scan_stub()
	settings = types.ModuleType(_OWN[6])
	settings.get_settings = lambda: dict(STATE["settings"])
	sys.modules[_OWN[6]] = settings
	import erpnext_enhancements.api as api_pkg
	import erpnext_enhancements.api.count_desk as count_desk

	api_pkg.stock_scan = sys.modules["erpnext_enhancements.api.stock_scan"]
	CD = count_desk


def tearDownModule():
	for name in _OWN:
		sys.modules.pop(name, None)
		if _SAVED_MODULES.get(name) is not None:
			sys.modules[name] = _SAVED_MODULES[name]
	import erpnext_enhancements.api as api_pkg

	if hasattr(api_pkg, "stock_scan") and api_pkg.stock_scan is not _SAVED_MODULES.get("erpnext_enhancements.api.stock_scan"):
		del api_pkg.stock_scan


ITEMS = [
	dict(name="A", item_name="AY", stock_uom="Unit", is_stock_item=1, valuation_rate=2),
	dict(name="B", item_name="BEE", stock_uom="Unit", is_stock_item=1, valuation_rate=3),
	dict(name="S", item_name="SERIAL", stock_uom="Unit", is_stock_item=1, has_serial_no=1),
	dict(name="F", item_name="FOOT", stock_uom="FT", is_stock_item=1),
]


def _reset(roles=("Inventory Clerk",), perms=()):
	STATE.clear()
	STATE.update(
		roles=list(roles),
		perms=set(perms),
		tables={
			"Warehouse": [dict(w, company=COMPANY) for w in _warehouses()],
			"Item": [dict(i) for i in ITEMS],
			"UOM": [dict(name="Unit", must_be_whole_number=1), dict(name="FT", must_be_whole_number=0)],
			"Bin": [
				dict(item_code="A", warehouse=W1, actual_qty=10, reserved_stock=0, valuation_rate=2),
				dict(item_code="B", warehouse=W1, actual_qty=4, reserved_stock=0, valuation_rate=3),
			],
			"User": [dict(name=USER, full_name="Parker Bailey"), dict(name=OTHER, full_name="Sam")],
			"Stock Reconciliation": [],
		},
		listing={W1: [dict(item_code="A", item_name="AY", stock_uom="Unit"), dict(item_code="B", item_name="BEE", stock_uom="Unit")]},
		listing_limits=[],
		bin_stats=[dict(warehouse=W1, on_hand_items=2, stock_value=32)],
		ledger_last=[dict(warehouse=W1, last=datetime.date(2026, 7, 1))],
		ledger_pairs=[],
		rates={},
		settings={"require_variance_reason": 0, "scan_cost_center": None},
		sessions={},
		seq=0,
		sr_seq=0,
		srs={},
		inserted=[],
		submitted=[],
		queued=[],
		comments=[],
		sle=[],
		sle_reads=[],
		saves=[],
		db_sets=[],
		sql=[],
		get_all=[],
		get_doc=[],
	)


def _start(warehouses=(SHELF,), **kw):
	return CD.start_count(json.dumps(list(warehouses)), **kw)


def _save(name, *entries):
	return CD.save_counts(name, json.dumps(list(entries)))


def _key(item, wh=W1, loc=None):
	return rules.line_key(item, wh, loc)


class TestAccess(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_outside_the_counting_roles_everything_is_refused(self):
		STATE["roles"] = ["Stock User"]
		for call in (CD.boot, lambda: _start(), lambda: CD.get_sheet("x"), lambda: _save("x"), lambda: CD.post_count("x")):
			with self.subTest(call=call):
				with self.assertRaises(PermissionError_):
					call()

	def test_a_count_is_its_starters_unless_you_supervise(self):
		name = _start()["name"]
		FRAPPE.session.user = OTHER
		try:
			with self.assertRaises(PermissionError_):
				CD.get_sheet(name)
			STATE["roles"] = ["Stock Manager"]
			self.assertEqual(CD.get_sheet(name)["count"]["name"], name)
		finally:
			FRAPPE.session.user = USER

	def test_every_endpoint_says_its_method(self):
		tree = ast.parse(API.read_text(encoding="utf-8"))
		methods = {}
		for node in tree.body:
			if isinstance(node, ast.FunctionDef):
				for dec in node.decorator_list:
					if isinstance(dec, ast.Call) and ast.unparse(dec.func) == "frappe.whitelist":
						kw = {k.arg: ast.literal_eval(k.value) for k in dec.keywords}
						methods[node.name] = kw.get("methods")
		self.assertEqual(
			methods,
			{
				"boot": ["GET"],
				"get_sheet": ["GET"],
				"start_count": ["POST"],
				"update_count": ["POST"],
				"save_counts": ["POST"],
				"post_count": ["POST"],
			},
		)

	def test_the_role_check_comes_first_in_every_endpoint(self):
		tree = ast.parse(API.read_text(encoding="utf-8"))
		for node in tree.body:
			if isinstance(node, ast.FunctionDef) and node.decorator_list:
				body = [n for n in node.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
				with self.subTest(endpoint=node.name):
					self.assertEqual(ast.unparse(body[0]), "_check_access()")

	def test_no_sql_function_as_a_string_field(self):
		"""Frappe 16 refuses ``count(name)`` as a get_all field (CLAUDE.md)."""
		for node in ast.walk(ast.parse(API.read_text(encoding="utf-8"))):
			if isinstance(node, ast.Call) and ast.unparse(node.func) in ("frappe.get_all", "frappe.get_list"):
				for kw in node.keywords:
					if kw.arg == "fields":
						for el in ast.walk(kw.value):
							if isinstance(el, ast.Constant) and isinstance(el.value, str):
								self.assertNotIn("(", el.value, ast.unparse(node)[:80])


class TestBoot(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_the_tree_and_what_the_caller_may_do(self):
		out = CD.boot()
		self.assertEqual([b["warehouse"] for b in out["bins"]], [W1, W2, W10], "countable bins, shelf order")
		self.assertEqual({g["name"] for g in out["groups"]}, {ROOT, ROW, SHELF})
		b1 = out["bins"][0]
		self.assertEqual((b1["on_hand_items"], b1["stock_value"], b1["last_counted"], b1["days_since"]), (2, 32.0, "2026-07-01", 111))
		self.assertEqual(b1["trail"], "Row A › Shelf A1-1", "the tree's root is dropped from the trail")
		self.assertFalse(out["can_post"])
		self.assertEqual(out["stats"]["coverage"], {"bins": 1, "counted": 0, "pct": 0.0, "never": 0})
		self.assertEqual(out["reasons"], list(rules.REASON_CODES))
		STATE["perms"] = {("Stock Reconciliation", "create"), ("Stock Reconciliation", "submit")}
		self.assertTrue(CD.boot()["can_post"])

	def test_open_counts_are_your_own_unless_you_supervise(self):
		_start()
		FRAPPE.session.user = OTHER
		try:
			self.assertEqual(CD.boot()["open_counts"], [])
			STATE["roles"] = ["Stock Manager"]
			(count,) = CD.boot()["open_counts"]
			self.assertEqual((count["counted_by_name"], count["mine"], count["scope"]), ("Parker Bailey", False, [W1, W2, W10]))
		finally:
			FRAPPE.session.user = USER


class TestStart(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_shelf_becomes_its_bins_titled_after_it(self):
		out = _start(blind=1)
		doc = STATE["sessions"][out["name"]]
		self.assertEqual(json.loads(doc.scope), [W1, W2, W10])
		self.assertEqual((doc.count_title, doc.blind_count, doc.counted_by, doc.status, doc.company), ("Shelf A1-1", 1, USER, "Open", COMPANY))
		self.assertEqual(STATE["inserted"], [("Inventory Count Session", True)])
		self.assertEqual(out["overlaps"], [])

	def test_nothing_countable_is_refused_and_overlaps_are_said(self):
		with self.assertRaises(ValidationError):
			_start([TRUCK])
		first = _start([W1])["name"]
		out = _start([W1, W2], title="Mine")
		self.assertEqual(STATE["sessions"][out["name"]].count_title, "Mine")
		self.assertEqual(out["overlaps"], [{"name": first, "title": "Bin A1-1-1", "counted_by_name": "Parker Bailey", "bins": 1}])


class TestSheet(unittest.TestCase):
	def setUp(self):
		_reset()
		self.name = _start([W1, W2])["name"]

	def test_every_item_the_bins_hold_uncapped(self):
		out = CD.get_sheet(self.name)
		self.assertEqual([(r["item_code"], r["counted"], r["system_qty"]) for r in out["rows"]], [("A", False, 10), ("B", False, 4)])
		self.assertEqual(STATE["listing_limits"], [None, None], "the phone's 150-item cap is not the sheet's")
		self.assertEqual(out["count"]["scope"][0], {"warehouse": W1, "warehouse_name": "Bin A1-1-1", "trail": "Row A › Shelf A1-1"})
		self.assertTrue(out["count"]["editable"])

	def test_a_count_snapshots_the_system_and_a_reason_alone_does_not(self):
		out = _save(self.name, {"key": _key("A"), "counted_qty": 7})
		self.assertEqual(out["results"], [{"key": _key("A"), "ok": True}])
		(group,) = out["groups"]
		(row,) = group["rows"]
		self.assertEqual((row["counted_qty"], row["snapshot_qty"], row["variance"], row["counted_by"]), (7, 10, -3, USER))
		line = STATE["sessions"][self.name].lines[0]
		self.assertEqual((line.variance, str(line.scan_time)), (-3, "2026-10-20 14:00:00"), "the controller's per-item variance")
		STATE["tables"]["Bin"][0]["actual_qty"] = 6
		line.scan_time = "earlier"
		_save(self.name, {"key": _key("A"), "reason_code": "Other", "recount": 1})
		self.assertEqual((line.system_qty, line.scan_time, line.reason_code, line.recount), (10, "earlier", "Other", 1))
		_save(self.name, {"key": _key("A"), "counted_qty": 7})
		self.assertEqual(line.system_qty, 10, "the same count is not a new one")
		_save(self.name, {"key": _key("A"), "counted_qty": 5})
		self.assertEqual(line.system_qty, 6, "a changed count is counted against what the system shows now")

	def test_the_session_row_is_locked_before_it_is_loaded(self):
		STATE["sql"].clear()
		STATE["get_doc"].clear()
		_save(self.name, {"key": _key("A"), "counted_qty": 7})
		self.assertTrue(STATE["sql"][0].endswith("for update"))
		self.assertEqual(STATE["get_doc"], [self.name])

	def test_one_bad_row_does_not_stop_the_rest(self):
		out = _save(
			self.name,
			{"key": _key("A"), "counted_qty": 7},
			{"key": _key("B"), "counted_qty": 1.5},
			{"key": _key("S"), "counted_qty": 1},
			{"key": _key("A", W10), "counted_qty": 1},
			{"key": _key("B"), "reason_code": "Other"},
		)
		said = [(r["ok"], r.get("message", "")) for r in out["results"]]
		self.assertEqual(said[0], (True, ""))
		self.assertIn("whole numbers", said[1][1])
		self.assertIn("Serialized", said[2][1])
		self.assertIn("not part of this count", said[3][1])
		self.assertEqual(said[4], (False, "Count it first, then add a reason or a flag."))
		self.assertEqual(len(STATE["sessions"][self.name].lines), 1)

	def test_clearing_a_count_removes_its_line(self):
		_save(self.name, {"key": _key("A"), "counted_qty": 7})
		out = _save(self.name, {"key": _key("A"), "counted_qty": None})
		self.assertEqual(out["results"][0]["cleared"], True)
		self.assertEqual(out["groups"], [{"item_code": "A", "warehouse": W1, "rows": []}])
		self.assertEqual(STATE["sessions"][self.name].lines, [])

	def test_a_finished_count_takes_no_saves(self):
		STATE["sessions"][self.name].status = "Finalized"
		with self.assertRaises(ValidationError):
			_save(self.name, {"key": _key("A"), "counted_qty": 7})

	def test_too_many_rows(self):
		with self.assertRaises(ValidationError):
			_save(self.name, *[{"key": _key("A"), "counted_qty": 1}] * (CD.SAVE_LIMIT + 1))

	def test_update_renames_blinds_and_adds_locations(self):
		CD.update_count(self.name, title="Renamed", blind=1)
		doc = STATE["sessions"][self.name]
		self.assertEqual((doc.count_title, doc.blind_count), ("Renamed", 1))
		out = CD.update_count(self.name, add_warehouses=json.dumps([W10]))
		self.assertEqual(json.loads(doc.scope), [W1, W2, W10])
		self.assertIn("rows", out)
		with self.assertRaises(ValidationError):
			CD.update_count(self.name, add_warehouses=json.dumps([TRUCK]))
		doc.scope = None
		with self.assertRaises(ValidationError):
			CD.update_count(self.name, add_warehouses=json.dumps([W10]))


POST = {("Stock Reconciliation", "create"), ("Stock Reconciliation", "submit")}


class TestPost(unittest.TestCase):
	def setUp(self):
		_reset(perms=POST)
		self.name = _start([W1, W2])["name"]

	def test_a_post_submits_as_the_user_the_variance_on_what_is_on_hand_now(self):
		_save(self.name, {"key": _key("A"), "counted_qty": 7}, {"key": _key("B"), "counted_qty": 4})
		STATE["tables"]["Bin"][0]["actual_qty"] = 8  # two taken after the count
		out = CD.post_count(self.name)
		(sr_name,) = out["posted"]
		sr = STATE["srs"][sr_name]
		self.assertEqual(STATE["inserted"][-1], ("Stock Reconciliation", False), "no ignore_permissions")
		self.assertEqual(STATE["submitted"], [sr_name])
		self.assertEqual([(r.item_code, r.warehouse, r.qty) for r in sr.items], [("A", W1, 5.0)], "B matched: not a row")
		self.assertIsNone(sr.items[0].valuation_rate, "a recount keeps the unit cost")
		self.assertEqual((sr.purpose, sr.company, sr.expense_account), ("Stock Reconciliation", COMPANY, "5119 - Stock Adjustment - SF"))
		self.assertFalse(sr.set_posting_time)
		doc = STATE["sessions"][self.name]
		self.assertEqual((doc.status, doc.stock_reconciliation, doc.stock_reconciliations), ("Finalized", sr_name, sr_name))
		self.assertEqual(out["matched"], 1)
		self.assertIn("Posted 1 adjustment(s)", out["message"])
		self.assertEqual(out["count"]["stock_reconciliations"], [sr_name])
		self.assertEqual(out["count"]["sr_status"], "Submitted")

	def test_the_count_goes_on_the_reconciliation_as_a_comment_not_a_remark(self):
		"""Stock Reconciliation has no ``remarks`` on v16 (checked on prod): a remark was dropped. The
		stub takes any attribute, so this asserts the comment and the attribute's absence."""
		STATE["settings"]["require_variance_reason"] = 1
		_save(self.name, {"key": _key("A"), "counted_qty": 7, "reason_code": "Used and not recorded", "reason": "<b>truck</b>"})
		(sr_name,) = CD.post_count(self.name)["posted"]
		self.assertNotIn("remarks", STATE["srs"][sr_name].__dict__)
		((doctype, name, kind, text),) = STATE["comments"]
		self.assertEqual((doctype, name, kind), ("Stock Reconciliation", sr_name, "Comment"))
		link = f"https://erp.example.com/desk/inventory-count-session/{self.name}"
		self.assertIn(f'From count <a href="{link}">{self.name}</a> (Bin A1-1-1 and Bin A1-1-2), counted by Parker Bailey', text)
		self.assertIn("Reasons: Used and not recorded: 1", text)
		self.assertIn("A at Bin A1-1-1 - SF: Used and not recorded - &lt;b&gt;truck&lt;/b&gt;", text)
		self.assertNotIn("<b>truck", text, "a typed note is escaped")

	def test_a_long_post_is_reconciliations_of_100_each_submitted_now(self):
		"""v16 queues the submit of more than 100 rows and returns a draft (the stub models it): a
		count of 250 items posts 100, 100 and 50, each submitted in the request, all on the session."""
		codes = [f"P{i:03d}" for i in range(250)]
		STATE["tables"]["Item"] += [dict(name=c, item_name=c, stock_uom="Unit", is_stock_item=1) for c in codes]
		STATE["tables"]["Bin"] += [dict(item_code=c, warehouse=W1, actual_qty=5, reserved_stock=0, valuation_rate=1) for c in codes]
		STATE["listing"][W1] += [dict(item_code=c, item_name=c, stock_uom="Unit") for c in codes]
		_save(self.name, *[{"key": _key(c), "counted_qty": 4} for c in codes])
		out = CD.post_count(self.name)
		srs = [STATE["srs"][n] for n in out["posted"]]
		self.assertEqual([len(sr.items) for sr in srs], [100, 100, 50])
		self.assertEqual(([sr.docstatus for sr in srs], STATE["queued"]), ([1, 1, 1], []))
		self.assertEqual(sorted(r.item_code for sr in srs for r in sr.items), codes, "every item once")
		doc = STATE["sessions"][self.name]
		self.assertEqual(doc.stock_reconciliations.split("\n"), out["posted"])
		self.assertEqual(doc.stock_reconciliation, out["posted"][0])
		parts = [t for (_, _, _, t) in STATE["comments"]]
		self.assertEqual([("part 1 of 3" in t, "part 2 of 3" in t, "part 3 of 3" in t) for t in parts], [(True, False, False), (False, True, False), (False, False, True)])
		self.assertIn(", ".join(out["posted"]), out["message"])
		again = CD.post_count(self.name)
		self.assertTrue(again["repeated"])
		self.assertIn(f"Its reconciliations are {', '.join(out['posted'])}.", again["message"])
		self.assertEqual(len(STATE["srs"]), 3, "a retry posts nothing")

	def test_a_submit_that_does_not_finish_refuses_the_post(self):
		"""Were ERPNext to queue one anyway, the post says so and finalizes nothing (the request's
		rollback takes the reconciliations with it), so a retry posts afresh instead of 'repeated'."""
		STATE["queue_submits"] = True
		_save(self.name, {"key": _key("A"), "counted_qty": 7})
		with self.assertRaises(ValidationError) as caught:
			CD.post_count(self.name)
		self.assertIn("was not submitted when it was posted, so nothing was posted", str(caught.exception))
		self.assertEqual(STATE["sessions"][self.name].status, "Open")
		self.assertEqual(STATE["db_sets"], [])

	def test_a_long_draft_is_split_the_same_way(self):
		STATE["perms"] = set()
		codes = [f"P{i:03d}" for i in range(101)]
		STATE["tables"]["Item"] += [dict(name=c, item_name=c, stock_uom="Unit", is_stock_item=1) for c in codes]
		STATE["tables"]["Bin"] += [dict(item_code=c, warehouse=W1, actual_qty=5, reserved_stock=0, valuation_rate=1) for c in codes]
		STATE["listing"][W1] += [dict(item_code=c, item_name=c, stock_uom="Unit") for c in codes]
		_save(self.name, *[{"key": _key(c), "counted_qty": 4} for c in codes])
		out = CD.post_count(self.name, mode="draft")
		self.assertEqual([len(STATE["srs"][n].items) for n in out["drafts"]], [100, 1])
		self.assertTrue(all(STATE["srs"][n].set_posting_time == 1 for n in out["drafts"]))
		self.assertEqual(len(STATE["comments"]), 2)
		self.assertEqual(CD.get_sheet(self.name)["count"]["sr_status"], "Draft")

	def test_everything_matched_finalizes_with_no_reconciliation(self):
		_save(self.name, {"key": _key("A"), "counted_qty": 10})
		out = CD.post_count(self.name)
		self.assertEqual((out["posted"], out["drafts"], STATE["srs"]), ([], [], {}))
		self.assertEqual(STATE["sessions"][self.name].status, "Finalized")
		self.assertIn("Nothing needed adjusting.", out["message"])

	def test_a_retry_posts_nothing_twice(self):
		_save(self.name, {"key": _key("A"), "counted_qty": 7})
		CD.post_count(self.name)
		again = CD.post_count(self.name)
		self.assertTrue(again["repeated"])
		self.assertEqual(len(STATE["srs"]), 1)

	def test_without_submit_it_drafts_dated_now(self):
		STATE["perms"] = set()
		_save(self.name, {"key": _key("A"), "counted_qty": 7})
		with self.assertRaises(PermissionError_):
			CD.post_count(self.name, mode="post")
		out = CD.post_count(self.name, mode="draft")
		(name,) = out["drafts"]
		sr = STATE["srs"][name]
		self.assertEqual(STATE["inserted"][-1], ("Stock Reconciliation", True))
		self.assertEqual(STATE["submitted"], [])
		self.assertEqual((sr.set_posting_time, sr.posting_date, sr.posting_time), (1, "2026-10-20", "14:00:00"))
		self.assertIn("for a Stock Manager", out["message"])

	def test_a_required_reason_refuses_the_whole_post(self):
		STATE["settings"]["require_variance_reason"] = 1
		_save(self.name, {"key": _key("A"), "counted_qty": 7})
		with self.assertRaises(ValidationError) as caught:
			CD.post_count(self.name)
		self.assertIn("A variance reason is required before posting: A.", str(caught.exception))
		self.assertEqual(STATE["srs"], {})
		_save(self.name, {"key": _key("A"), "reason_code": "Damaged or scrapped"})
		CD.post_count(self.name)
		(sr,) = STATE["srs"].values()
		((_, name, _, text),) = STATE["comments"]
		self.assertEqual(name, sr.name)
		self.assertIn("A at Bin A1-1-1 - SF: Damaged or scrapped", text)

	def test_superseded_and_reserved_are_left_out_and_noted(self):
		_save(self.name, {"key": _key("A"), "counted_qty": 7}, {"key": _key("B"), "counted_qty": 1})
		STATE["ledger_pairs"] = [dict(item_code="A", warehouse=W1, last=datetime.datetime(2026, 10, 20, 15, 0))]
		STATE["tables"]["Bin"][1]["reserved_stock"] = 2
		out = CD.post_count(self.name)
		self.assertEqual(out["posted"], [])
		reasons = {r["item_code"]: r["reason"] for r in out["skipped"]}
		self.assertIn("posted after yours", reasons["A"])
		self.assertIn("reserved", reasons["B"])
		self.assertIn("Not posted:", STATE["sessions"][self.name].notes)

	def test_from_empty_it_uses_the_receipt_rate(self):
		STATE["listing"][W2] = [dict(item_code="F", item_name="FOOT", stock_uom="FT")]
		STATE["rates"]["F"] = 1.25
		_save(self.name, {"key": _key("F", W2), "counted_qty": 2.5})
		out = CD.post_count(self.name)
		sr = STATE["srs"][out["posted"][0]]
		self.assertEqual([(r.item_code, r.qty, r.valuation_rate) for r in sr.items], [("F", 2.5, 1.25)])

	def test_nothing_counted(self):
		with self.assertRaises(ValidationError):
			CD.post_count(self.name)
		with self.assertRaises(ValidationError):
			CD.post_count(self.name, mode="sideways")


class TestCountedAt(unittest.TestCase):
	"""A paper count: 10 on the shelf, counted at 9:00; 3 taken at 10:00; typed in at 14:00 (the
	stub's now). Counted against the system as typed it posts 10 and undoes the take; counted against
	the ledger at 9:00 it posts nothing, and the take stands."""

	NINE = "2026-10-20 09:00:00"

	def setUp(self):
		_reset(perms=POST)
		STATE["tables"]["Bin"][0]["actual_qty"] = 7
		STATE["sle"] = [
			dict(item_code="B", warehouse=W1, at=datetime.datetime(2026, 10, 20, 7, 0), qty_after_transaction=4),
			dict(item_code="A", warehouse=W1, at=datetime.datetime(2026, 10, 20, 8, 0), qty_after_transaction=10),
			dict(item_code="A", warehouse=W1, at=datetime.datetime(2026, 10, 20, 10, 0), qty_after_transaction=7),
		]
		self.name = _start([W1, W2])["name"]

	def test_counted_live_the_take_is_undone(self):
		_save(self.name, {"key": _key("A"), "counted_qty": 10})
		(sr_name,) = CD.post_count(self.name)["posted"]
		self.assertEqual([(r.item_code, r.qty) for r in STATE["srs"][sr_name].items], [("A", 10)])

	def test_counted_at_nine_the_take_stands(self):
		CD.update_count(self.name, counted_at=self.NINE)
		_save(self.name, {"key": _key("A"), "counted_qty": 10})
		line = STATE["sessions"][self.name].lines[0]
		self.assertEqual((line.system_qty, line.scan_time), (10, datetime.datetime(2026, 10, 20, 9, 0)))
		self.assertEqual(STATE["sle_reads"][-1], {"item_code": "A", "warehouse": W1, "posting_date": "2026-10-20", "posting_time": "09:00:00"})
		out = CD.post_count(self.name)
		self.assertEqual((out["posted"], out["matched"], STATE["srs"]), ([], 1, {}))

	def test_a_short_count_at_nine_is_taken_off_what_is_there_now(self):
		CD.update_count(self.name, counted_at=self.NINE)
		_save(self.name, {"key": _key("A"), "counted_qty": 8})
		(sr_name,) = CD.post_count(self.name)["posted"]
		self.assertEqual([(r.item_code, r.qty) for r in STATE["srs"][sr_name].items], [("A", 5)], "7 now, 2 short")

	def test_setting_or_clearing_it_resnapshots_every_counted_line(self):
		_save(self.name, {"key": _key("A"), "counted_qty": 8})
		line = STATE["sessions"][self.name].lines[0]
		self.assertEqual(line.system_qty, 7)
		out = CD.update_count(self.name, counted_at=self.NINE)
		self.assertEqual((line.system_qty, str(line.scan_time)), (10, self.NINE))
		self.assertEqual(out["count"]["counted_at"], self.NINE)
		rows = {r["item_code"]: r for r in out["rows"]}
		self.assertEqual((rows["A"]["snapshot_qty"], rows["A"]["variance"], rows["A"]["as_of_qty"]), (10, -2, 10))
		self.assertEqual((rows["B"]["as_of_qty"], rows["B"]["system_qty"]), (4, 4), "uncounted: the ledger at 9:00")
		CD.update_count(self.name, counted_at="")
		self.assertEqual((line.system_qty, str(line.scan_time)), (7, "2026-10-20 14:00:00"), "live again: now")
		self.assertIsNone(STATE["sessions"][self.name].counted_at)
		self.assertIsNone(CD.get_sheet(self.name)["rows"][0]["as_of_qty"])

	def test_the_same_time_again_rereads_nothing(self):
		CD.update_count(self.name, counted_at=self.NINE)
		_save(self.name, {"key": _key("A"), "counted_qty": 8})
		reads = len(STATE["sle_reads"])
		out = CD.update_count(self.name, counted_at=self.NINE, title="Paper")
		self.assertEqual((len(STATE["sle_reads"]), "rows" in out), (reads, False))

	def test_a_time_in_the_future_or_long_before_the_count_is_refused(self):
		for bad, said in (
			("2026-10-20 14:30:00", "in the future"),
			("2026-10-13 12:00:00", "at most 7 days before"),
			("soon", "not a date and time"),
		):
			with self.subTest(bad=bad):
				with self.assertRaises(ValidationError) as caught:
					CD.update_count(self.name, counted_at=bad)
				self.assertIn(said, str(caught.exception))
		self.assertIsNone(STATE["sessions"][self.name].counted_at)
		with self.assertRaises(ValidationError):
			_start([W1], counted_at="2026-10-21 09:00:00")
		STATE["sessions"][self.name].scope = None  # a phone count: snapshotted as each item is scanned
		with self.assertRaises(ValidationError) as caught:
			CD.update_count(self.name, counted_at=self.NINE)
		self.assertIn("counted as each item is scanned", str(caught.exception))
		CD.update_count(self.name, counted_at="")

	def test_a_count_started_with_it_and_a_reconciliation_after_it(self):
		name = _start([W1], counted_at=self.NINE)["name"]
		self.assertEqual(STATE["sessions"][name].counted_at, datetime.datetime(2026, 10, 20, 9, 0))
		sheet = CD.get_sheet(name)
		self.assertEqual(sheet["count"]["counted_at"], self.NINE)
		self.assertEqual({r["item_code"]: r["as_of_qty"] for r in sheet["rows"]}, {"A": 10, "B": 4})
		_save(name, {"key": _key("A"), "counted_qty": 8})
		# Somebody posted a count of A at 9:30: after this one, so this one is not posted over it.
		STATE["ledger_pairs"] = [dict(item_code="A", warehouse=W1, last=datetime.datetime(2026, 10, 20, 9, 30))]
		out = CD.post_count(name)
		self.assertEqual(out["posted"], [])
		self.assertIn("posted after yours", out["skipped"][0]["reason"])


class TestTheSessionController(unittest.TestCase):
	def test_validate_stamps_the_items_variance_on_every_line(self):
		from erpnext_enhancements.inventory_enhancements.doctype.inventory_count_session.inventory_count_session import (
			InventoryCountSession,
		)

		doc = InventoryCountSession({"doctype": "Inventory Count Session"})
		doc.append("lines", _line("A", W1, 5, 10, "2026-10-20 10:00:00", storage_location="L1"))
		doc.append("lines", _line("A", W1, 4, 10, "2026-10-20 10:05:00", storage_location="L2"))
		doc.append("lines", _line("B", W1, 3, 2))
		doc.validate()
		self.assertEqual([line.variance for line in doc.lines], [-1, -1, 1])


# =========================================================================== the page


def _code(path):
	"""Comments stripped: an absence assertion must not be satisfied by its own explanation."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestThePageUnderNode(unittest.TestCase):
	def test_the_page_harness_passes(self):
		result = subprocess.run([shutil.which("node"), str(HARNESS)], capture_output=True, text=True, timeout=300, cwd=REPO)
		output = (result.stdout + result.stderr)[-6000:]
		self.assertEqual(result.returncode, 0, output)
		self.assertIn("checks passed", result.stdout, output)
		self.assertNotIn("FAIL", result.stdout, output)

	def test_the_page_scripts_parse(self):
		for path in (PAGE_JS, SESSION_JS):
			with self.subTest(path=path.name):
				result = subprocess.run([shutil.which("node"), "--check", str(path)], capture_output=True, text=True, timeout=60)
				self.assertEqual(result.returncode, 0, result.stderr)


class TestThePageFiles(unittest.TestCase):
	def test_the_page_is_for_the_people_who_count(self):
		page = json.loads(PAGE_JSON.read_text(encoding="utf-8"))
		self.assertEqual((page["name"], page["page_name"], page["title"]), ("count-desk", "count-desk", "Count Desk"))
		self.assertEqual((page["module"], page["standard"]), ("Inventory Enhancements", "Yes"))
		self.assertEqual({r["role"] for r in page["roles"]}, set(rules.COUNT_ROLES))
		icons = {line for line in ICONS.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")}
		self.assertIn(page["icon"], icons)

	def test_every_css_selector_is_namespaced(self):
		css = re.sub(r"/\*[\s\S]*?\*/", "", PAGE_CSS.read_text(encoding="utf-8"))
		selectors = []
		for block in re.findall(r"([^{}]+)\{[^{}]*\}", re.sub(r"@media[^{]+\{", "", css)):
			selectors += [s.strip() for s in block.split(",") if s.strip()]
		self.assertGreater(len(selectors), 50)
		for selector in selectors:
			with self.subTest(selector=selector):
				self.assertRegex(selector, r"^(\[data-theme=\"dark\"\]\s+)?\.cd-")

	def test_the_page_loads_bundles_never_a_raw_assets_path(self):
		code = _code(PAGE_JS)
		self.assertIn('"count_desk.bundle.js"', code)
		self.assertNotIn("/assets/", code)
		self.assertNotRegex(code, r"[\"'`]/app/")
		self.assertIn("window.sf_count_desk", BUNDLE.read_text(encoding="utf-8"))

	def test_every_request_is_quiet_and_no_settings_single_is_read(self):
		code = _code(PAGE_JS) + _code(CONTROLLER) + _code(LOGIC)
		self.assertNotIn("frappe.call(", code)
		self.assertNotRegex(code, r"get_single_value|Inventory Scanner Settings|frappe\.db\.")
		self.assertNotIn("location.search", code)
		self.assertNotIn("eval(", code, "a count cell's sum is parsed, never evaluated")

	def test_every_endpoint_the_page_names_exists_with_its_method(self):
		code = _code(LOGIC)
		sources = {
			"API": API.read_text(encoding="utf-8"),
			"INV": SCANNER.read_text(encoding="utf-8"),
			"SCAN": STOCK_SCAN.read_text(encoding="utf-8"),
		}
		found = re.findall(r'(API|INV|SCAN) \+ "(\w+)"', code)
		self.assertGreaterEqual(len(found), 9)
		for prefix, name in found:
			with self.subTest(endpoint=name):
				self.assertRegex(sources[prefix], rf"@frappe\.whitelist\([^)]*\)\ndef {name}\(")
		controller = _code(CONTROLLER)
		for read in ("boot", "sheet"):
			with self.subTest(read=read):
				self.assertRegex(controller, rf'READS\.{read}, [^;]*method: "GET"')


class TestTheDoctypes(unittest.TestCase):
	def _fields(self, path):
		doc = json.loads(path.read_text(encoding="utf-8"))
		return doc, {f["fieldname"]: f for f in doc["fields"]}

	def test_the_session_carries_a_title_scope_and_blind_flag(self):
		doc, fields = self._fields(SESSION_JSON)
		for name, fieldtype in (
			("count_title", "Data"),
			("scope", "Small Text"),
			("blind_count", "Check"),
			("counted_at", "Datetime"),
			("stock_reconciliations", "Small Text"),
		):
			with self.subTest(field=name):
				self.assertEqual(fields[name]["fieldtype"], fieldtype)
				self.assertIn(name, doc["field_order"])
		self.assertEqual(fields["blind_count"]["default"], "0")
		self.assertEqual(set(doc["field_order"]), set(fields))
		# A nullable column with no default: migrate adds it, and every existing count reads blank,
		# which is a live count and an empty list, the right answers for both.
		self.assertNotIn("default", fields["counted_at"])
		self.assertNotIn("reqd", fields["counted_at"])
		self.assertEqual(fields["stock_reconciliations"].get("read_only"), 1)
		self.assertEqual(doc["title_field"], "count_title")
		self.assertGreaterEqual(doc["modified"], "2026-10-23")

	def test_the_line_carries_a_reason_code_recount_and_who_counted(self):
		doc, fields = self._fields(LINE_JSON)
		for name, fieldtype in (("reason_code", "Select"), ("recount", "Check"), ("counted_by", "Link")):
			with self.subTest(field=name):
				self.assertEqual(fields[name]["fieldtype"], fieldtype)
				self.assertIn(name, doc["field_order"])
		self.assertEqual(fields["recount"]["default"], "0")
		self.assertEqual(set(doc["field_order"]), set(fields))
		self.assertGreaterEqual(doc["modified"], "2026-10-20")

	def test_the_endpoints_read_only_fields_the_doctypes_have(self):
		_, session = self._fields(SESSION_JSON)
		_, line = self._fields(LINE_JSON)
		known = set(session) | set(line) | {"name", "modified", "parent", "parenttype", "idx"}
		text = API.read_text(encoding="utf-8")
		for block in re.findall(r"SESSION,\s*filters=[^)]*?fields=\[([^\]]*)\]", text, re.S):
			for field in re.findall(r'"(\w+)"', block):
				with self.subTest(field=field):
					self.assertIn(field, known)

	def test_the_phone_page_keeps_to_its_own_count(self):
		text = SCANNER.read_text(encoding="utf-8")
		self.assertIn('frappe.db.has_column("Inventory Count Session", "scope")', text)
		self.assertIn('filters["scope"] = ["is", "not set"]', text)
		self.assertIn('"counted_by": frappe.session.user', text)

	def test_the_listing_can_be_uncapped(self):
		text = STOCK_SCAN.read_text(encoding="utf-8")
		self.assertIn("def _items_at(row, limit=LOCATION_ITEM_LIMIT):", text)
		self.assertIn("if limit is None:\n\t\treturn rows, False", text)

	def test_the_form_opens_the_count_desk(self):
		code = _code(SESSION_JS)
		self.assertIn('frappe.set_route("count-desk", frm.doc.name)', code)


class TestWhereToFindIt(unittest.TestCase):
	def _ws(self, path):
		return json.loads(path.read_text(encoding="utf-8"))

	def _in_card(self, ws, card):
		links = ws["links"]
		start = next(i for i, link in enumerate(links) if link["type"] == "Card Break" and link["label"] == card)
		rest = links[start + 1 :]
		end = next((i for i, link in enumerate(rest) if link["type"] == "Card Break"), len(rest))
		members = rest[:end]
		self.assertIn(("Count Desk", "count-desk", "Page"), [(m["label"], m.get("link_to"), m.get("link_type")) for m in members])
		self.assertEqual(links[start]["link_count"], len(members))

	def test_the_operations_hub_links_it(self):
		hub = self._ws(OPS_HUB)
		self.assertIn(
			{"label": "Count Desk", "type": "URL", "url": "/desk/count-desk"},
			[{k: s.get(k) for k in ("label", "type", "url")} for s in hub["shortcuts"]],
		)
		self.assertIn("Count Desk", [b["data"].get("shortcut_name") for b in json.loads(hub["content"])])
		self._in_card(hub, "Inventory")
		self.assertGreaterEqual(hub["modified"], "2026-10-20 15:00")

	def test_the_inventory_workspace_links_it(self):
		ws = self._ws(INV_WS)
		self._in_card(ws, "Counts")
		self.assertGreaterEqual(ws["modified"], "2026-10-20 15:00")

	def test_the_patch_lands_both_files_and_is_registered(self):
		text = PATCH.read_text(encoding="utf-8")
		self.assertIn('"operations_hub"', text)
		self.assertIn('"inventory_enhancements"', text)
		self.assertIn("force=True", text)
		lines = [line.strip() for line in PATCHES_TXT.read_text(encoding="utf-8").splitlines()]
		self.assertIn("erpnext_enhancements.patches.count_desk_hub_links", lines)
		self.assertGreater(
			lines.index("erpnext_enhancements.patches.count_desk_hub_links"), lines.index("[post_model_sync]")
		)

	def test_ci_runs_this_suite(self):
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_count_desk -v", CI.read_text(encoding="utf-8"))


if __name__ == "__main__":
	unittest.main()
