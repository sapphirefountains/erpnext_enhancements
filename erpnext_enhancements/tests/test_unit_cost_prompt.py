# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the unit-cost prompt (v1.727.0, TASK-2026-02914).

ERPNext refuses to post stock for an item that has no cost (``Valuation Rate Missing`` on a Material
Receipt; ``Valuation Rate required`` on a Stock Reconciliation row), and the scanner pages used to
meet that with a sentence ("ask a Stock Manager to set its Valuation Rate"). They now answer with a
payload, ``{needs_valuation_rate: True, item_code, item_name, uom, suggestion, source, max_rate}``,
the page asks for a unit cost, and the same save is sent again with ``rate``.

This runs the **real** ``api/stock_scan.py`` and ``bin_assignments.py`` on
``tests/test_bin_assignments.py``'s ``frappe`` stub, reused rather than copied (its writes check
permission unless ``ignore_permissions`` is passed). What it pins:

* **Only where ERPNext would refuse.** An item with a cost on record is never asked for one, and a
  ``rate`` sent for it is ignored: a screen cannot restate what an item with a history costs.
* **Permission first.** A person who could not post the document is not asked for a price.
* **Nothing is posted by the question**, no voucher and no log row, so the same reference is free
  for the answer; the answer posts once, and a resend returns the first save.
* **What the answer may be.** Positive, at most ``MAX_UNIT_COST`` (100,000); zero, negative, ``nan``,
  ``inf`` and text are sentences, not another prompt. It is the cost per stock unit, for this one
  posting: the Item and the price lists are not written, "Allow Zero Valuation Rate" is never set.
* **The record of it**: the Stock Entry's remarks and the log row's ``rate`` say a cost was
  entered, and an add without a purchase order stays flagged for a stock manager's review.
* **The suggestion** (``_suggested_unit_cost``): the latest submitted purchase line, else the default
  buying price list, with the source named; never ``standard_rate`` (a selling price).
* **The Inventory Scanner Audit** is untouched: it posts nothing (a draft reconciliation for a stock
  manager), so it never meets the refusal.

Run: python -m unittest erpnext_enhancements.tests.test_unit_cost_prompt -v
"""

import ast
import math
import unittest
from pathlib import Path

from erpnext_enhancements.inventory_enhancements import stock_scan_rules as rules
from erpnext_enhancements.tests import test_bin_assignments as base

APP = Path(__file__).resolve().parents[1]
STOCK_SCAN = APP / "api" / "stock_scan.py"
BINS = APP / "inventory_enhancements" / "bin_assignments.py"
SCANNER_AUDIT = APP / "api" / "inventory_scanner.py"

BIN_A, STATE = base.BIN_A, base.STATE


def setUpModule():
	base.setUpModule()


def tearDownModule():
	base.tearDownModule()


def _suggestion(rate=None, source=None):
	"""Make the suggestion lookup answer ``(rate, source)``; returns the calls it saw."""
	seen = []

	def lookup(item_code, company):
		seen.append((item_code, company))
		return rate, source

	base.scan._suggested_unit_cost = lookup
	return seen


class _Base(base._Base):
	def setUp(self):
		super().setUp()
		STATE["rate"] = 0  # no cost on record
		self._original = base.scan._suggested_unit_cost
		_suggestion(None, None)

	def tearDown(self):
		base.scan._suggested_unit_cost = self._original

	def add(self, ref=1, qty=2, **kwargs):
		return base.scan.add("PDT-0001", BIN_A, qty, base._ref(ref), without_po=1, **kwargs)

	def count(self, ref=1, qty=3, **kwargs):
		return base.bins.set_count("PDT-0001", BIN_A, qty, base._ref(ref), **kwargs)

	def nothing_posted(self):
		self.assertEqual(base._vouchers("Stock Entry"), [])
		self.assertEqual(base._vouchers("Stock Reconciliation"), [])
		self.assertEqual(base._table("Stock Scan Log"), {})


class TestAddWithoutAPurchaseOrder(_Base):
	def test_no_cost_on_record_answers_with_a_payload_and_posts_nothing(self):
		_suggestion(8.25, "the last purchase of it: Purchase Order PO-1, Oct 3")
		out = self.add()
		self.assertEqual(
			out,
			{
				"needs_valuation_rate": True,
				"item_code": "PDT-0001",
				"item_name": out["item_name"],
				"uom": "Unit",
				"suggestion": 8.25,
				"source": "the last purchase of it: Purchase Order PO-1, Oct 3",
				"max_rate": 100000,
			},
		)
		self.nothing_posted()

	def test_without_a_suggestion_the_payload_says_so(self):
		out = self.add()
		self.assertEqual((out["suggestion"], out["source"]), (None, None))

	def test_the_answer_posts_one_receipt_at_that_cost(self):
		out = self.add(rate="7.5")
		(se,) = base._vouchers("Stock Entry")
		self.assertEqual(se["stock_entry_type"], "Material Receipt")
		(row,) = se["items"]
		self.assertEqual((row["basic_rate"], row["qty"], row["t_warehouse"]), (7.5, 2.0, BIN_A))
		self.assertNotIn("allow_zero_valuation_rate", row, "never ticked, silently or otherwise")
		(log,) = base._table("Stock Scan Log").values()
		self.assertEqual((log["action"], log["rate"], log["needs_review"]), ("Add Without PO", 7.5, 1))
		self.assertIn("entered a unit cost of 7.5 per Unit", se["remarks"])
		self.assertTrue(out)

	def test_the_question_leaves_the_reference_free_and_a_resend_posts_once(self):
		self.add(ref=1)  # the question
		self.add(ref=1, rate=9)  # the answer, same reference
		again = self.add(ref=1, rate=9)  # a retry after a lost reply
		self.assertEqual(len(base._vouchers("Stock Entry")), 1)
		self.assertTrue(again["repeated"])

	def test_a_cost_on_record_is_never_asked_for_and_a_typed_one_is_ignored(self):
		STATE["rate"] = 12.5
		self.add(ref=1)
		self.add(ref=2, rate=99)
		for se in base._vouchers("Stock Entry"):
			self.assertEqual(se["items"][0]["basic_rate"], 12.5)
		logs = list(base._table("Stock Scan Log").values())
		self.assertEqual(len(logs), 2)
		self.assertTrue(all(not log.get("rate") for log in logs), "no cost was entered")
		self.assertNotIn("entered a unit cost", base._vouchers("Stock Entry")[1]["remarks"])

	def test_the_cost_must_be_positive_and_at_most_the_cap(self):
		for ref, bad in enumerate(("0", -1, "abc", "nan", "inf", 100001, "1e9"), start=1):
			with self.subTest(rate=bad):
				self.assertRefused(
					base.scan.add,
					"unit cost",
					item_code="PDT-0001",
					warehouse=BIN_A,
					qty=2,
					client_ref=base._ref(ref),
					without_po=1,
					rate=bad,
				)
		self.nothing_posted()
		self.add(ref=20, rate=rules.MAX_UNIT_COST)
		self.assertEqual(base._vouchers("Stock Entry")[0]["items"][0]["basic_rate"], 100000.0)

	def test_a_blank_cost_is_a_question_not_an_error(self):
		self.assertTrue(self.add(rate="  ")["needs_valuation_rate"])
		self.assertTrue(self.add(rate="")["needs_valuation_rate"])

	def test_permission_comes_before_the_question(self):
		STATE["perm"][("Stock Entry", "submit")] = False
		with self.assertRaises(base.FRAPPE.PermissionError):
			self.add()
		self.nothing_posted()
		STATE["perm"][("Stock Entry", "submit")] = True
		STATE["perm"][("Stock Entry", "create")] = False
		with self.assertRaises(base.FRAPPE.PermissionError):
			self.add(rate=5)

	def test_the_item_and_the_price_lists_are_not_written(self):
		import copy

		before = copy.deepcopy(base._table("Item"))
		self.add(rate=5)
		self.count(ref=2, rate=5)
		self.assertEqual(base._table("Item"), before, "the Item master is untouched")
		self.assertEqual(base._table("Item Price"), {})
		written = {doctype for _kind, doctype, _ignore in STATE["writes"]}
		self.assertFalse(written & {"Item", "Item Price", "Price List"}, written)
		self.assertNotIn(
			("insert", "Stock Entry", True), STATE["writes"], "posted as the user, never ignoring permissions"
		)


class TestACountUpFromAnEmptyBin(_Base):
	def test_no_cost_on_record_answers_with_a_payload_and_posts_nothing(self):
		out = self.count()
		self.assertTrue(out["needs_valuation_rate"])
		self.assertEqual((out["item_code"], out["uom"]), ("PDT-0001", "Unit"))
		self.nothing_posted()

	def test_the_answer_posts_one_reconciliation_at_that_cost(self):
		self.count(rate="4.5")
		(sr,) = base._vouchers("Stock Reconciliation")
		self.assertEqual((sr["items"][0]["qty"], sr["items"][0]["valuation_rate"]), (3.0, 4.5))
		(log,) = base._table("Stock Scan Log").values()
		self.assertEqual((log["action"], log["rate"]), ("Count", 4.5))
		self.assertNotIn("allow_zero_valuation_rate", sr["items"][0])

	def test_a_count_down_or_with_stock_here_never_asks(self):
		base._bin("PDT-0001", BIN_A, actual_qty=4)
		out = self.count(qty=2)
		self.assertNotIn("needs_valuation_rate", out)
		self.assertNotIn("valuation_rate", base._vouchers("Stock Reconciliation")[0]["items"][0])
		# a count to zero needs no cost either
		base._reset_bin = None
		base._bin("PDT-0001", BIN_A, actual_qty=2)
		out = self.count(ref=2, qty=0)
		self.assertNotIn("needs_valuation_rate", out)

	def test_a_cost_on_record_is_used_and_a_typed_one_ignored(self):
		STATE["rate"] = 12.5
		self.count(rate=99)
		self.assertEqual(base._vouchers("Stock Reconciliation")[0]["items"][0]["valuation_rate"], 12.5)

	def test_bad_costs_are_sentences(self):
		for ref, bad in enumerate((0, -3, "x", math.inf, 100001), start=1):
			with self.subTest(rate=bad):
				self.assertRefused(
					base.bins.set_count,
					"unit cost",
					item_code="PDT-0001",
					warehouse=BIN_A,
					qty=3,
					client_ref=base._ref(ref),
					rate=bad,
				)
		self.nothing_posted()

	def test_permission_comes_before_the_question(self):
		STATE["perm"][("Stock Reconciliation", "submit")] = False
		with self.assertRaises(base.FRAPPE.PermissionError):
			self.count()
		self.nothing_posted()


class TestTheRule(unittest.TestCase):
	def test_unit_cost_checks(self):
		self.assertEqual(rules.check_unit_cost(None), (None, None))
		self.assertEqual(rules.check_unit_cost("  "), (None, None))
		self.assertEqual(rules.check_unit_cost("12.5"), (12.5, None))
		self.assertEqual(rules.check_unit_cost(100000), (100000.0, None))
		self.assertEqual(rules.check_unit_cost("0.000001"), (0.000001, None))
		for bad in (0, "0", -1, "abc", "nan", "inf", float("inf"), 100000.01, "1e9"):
			with self.subTest(value=bad):
				rate, problem = rules.check_unit_cost(bad, "Unit")
				self.assertIsNone(rate)
				self.assertIn("unit cost", problem)
				self.assertIn("per Unit", problem)

	def test_the_cap_is_what_nik_said(self):
		self.assertEqual(rules.MAX_UNIT_COST, 100000)


class TestTheSuggestion(unittest.TestCase):
	"""``_suggested_unit_cost`` against canned query answers: which source wins, and what it says."""

	def setUp(self):
		base._reset()
		self.queries = []
		self.answers = {"purchases": [], "price": []}
		self.settings = {"Buying Settings": {"buying_price_list": "Standard Buying"}}
		original = base.FRAPPE.db.sql

		def sql(query, values=None, as_dict=False, **kwargs):
			text = " ".join(query.split())
			self.queries.append(text)
			if "from `tabPurchase Receipt Item`" in text:
				return self.answers["purchases"]
			if "from `tabItem Price`" in text:
				return self.answers["price"]
			return original(query, values, as_dict=as_dict, **kwargs)

		self._sql = original
		base.FRAPPE.db.sql = sql
		self._single = getattr(base.FRAPPE.db, "get_single_value", None)
		base.FRAPPE.db.get_single_value = lambda doctype, field: self.settings.get(doctype, {}).get(field)
		self._cached = getattr(base.FRAPPE, "get_cached_value", None)
		base.FRAPPE.get_cached_value = lambda doctype, name, field: "USD"
		self._fmt = getattr(base.FRAPPE.utils, "formatdate", None)
		base.FRAPPE.utils.formatdate = lambda d: str(d)
		self._original_get = base.FRAPPE.db.get_value

	def tearDown(self):
		base.FRAPPE.db.sql = self._sql
		if self._single:
			base.FRAPPE.db.get_single_value = self._single
		if self._cached:
			base.FRAPPE.get_cached_value = self._cached
		if self._fmt:
			base.FRAPPE.utils.formatdate = self._fmt

	def suggest(self):
		return base.scan.__dict__["_suggested_unit_cost"]("PDT-0001", base.COMPANY)

	def test_the_latest_purchase_line_wins_and_names_itself(self):
		self.answers["purchases"] = [
			base._dict(rate=6.0, doctype="Purchase Order", name="PO-0007", on_date="2026-10-03")
		]
		self.answers["price"] = [(2.0,)]
		rate, source = self.suggest()
		self.assertEqual(rate, 6.0)
		self.assertIn("PO-0007", source)
		self.assertIn("last purchase", source)

	def test_no_purchase_falls_back_to_the_buying_price_list(self):
		self.answers["price"] = [(2.5,)]
		rate, source = self.suggest()
		self.assertEqual(rate, 2.5)
		self.assertIn("Standard Buying", source)

	def test_nothing_on_file_is_no_suggestion(self):
		self.assertEqual(self.suggest(), (None, None))
		self.settings["Buying Settings"] = {}
		self.assertEqual(self.suggest(), (None, None))

	def test_the_queries_are_submitted_purchase_lines_in_stock_units_and_never_a_selling_price(self):
		self.answers["purchases"] = []
		self.suggest()
		text = " ".join(self.queries)
		self.assertIn("docstatus = 1", text)
		self.assertIn("base_net_rate / nullif(i.conversion_factor, 0)", text)
		self.assertIn("buying = 1", text)
		self.assertNotIn("standard_rate", text)
		self.assertNotIn("selling", text)


class TestTheClientsHoldTheSameCap(unittest.TestCase):
	def test_the_page_and_the_bin_manager_use_the_servers_cap(self):
		logic = (APP / "public" / "js" / "stock_scan" / "logic.js").read_text(encoding="utf-8")
		self.assertIn(f"export const MAX_UNIT_COST = {rules.MAX_UNIT_COST};", logic)
		# the Bin Manager re-exports the page's pieces, so the two screens word and cap it alike
		bm = (APP / "public" / "js" / "bin_manager" / "logic.js").read_text(encoding="utf-8")
		self.assertIn('from "../stock_scan/logic.js"', bm)
		self.assertNotIn("MAX_UNIT_COST =", bm)

	def test_both_pages_ask_for_the_cost_and_neither_changes_the_item(self):
		app = (APP / "public" / "js" / "stock_scan" / "app.js").read_text(encoding="utf-8")
		self.assertIn("isCostPrompt(result)", app)
		self.assertIn("askUnitCost(", app)
		bm = (APP / "inventory_enhancements" / "page" / "bin_manager" / "bin_manager.js").read_text(
			encoding="utf-8"
		)
		self.assertIn("cost_dialog(", bm)
		self.assertNotIn(
			"reqd: 1",
			bm[bm.index("cost_dialog(kind, row, given, prompt) {") : bm.index("restore_zeros(d, picked) {")],
		)

	def test_the_inventory_scanner_audit_page_is_untouched(self):
		page = (
			APP / "inventory_enhancements" / "page" / "inventory_scanner_audit" / "inventory_scanner_audit.js"
		).read_text(encoding="utf-8")
		self.assertNotIn("needs_valuation_rate", page)


class TestTheSource(unittest.TestCase):
	def test_no_ignore_permissions_in_the_prompt_path(self):
		for path, names in ((STOCK_SCAN, ("_receipt_cost", "_suggested_unit_cost")), (BINS, ("set_count",))):
			tree = ast.parse(path.read_text(encoding="utf-8"))
			for node in ast.walk(tree):
				if isinstance(node, ast.FunctionDef) and node.name in names:
					text = ast.unparse(node)
					self.assertNotIn("ignore_permissions", text, node.name)
					self.assertNotIn("allow_zero_valuation_rate", text, node.name)

	def test_the_item_and_price_lists_are_never_written_by_the_helpers(self):
		tree = ast.parse(STOCK_SCAN.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if isinstance(node, ast.FunctionDef) and node.name in ("_receipt_cost", "_suggested_unit_cost"):
				text = ast.unparse(node)
				for banned in ("set_value", "db_set", ".save(", ".insert(", "frappe.get_doc", "new_doc"):
					self.assertNotIn(banned, text, f"{node.name}: {banned}")

	def test_the_inventory_scanner_audit_posts_nothing_so_it_never_meets_the_refusal(self):
		text = SCANNER_AUDIT.read_text(encoding="utf-8")
		self.assertNotIn(".submit()", text, "it builds a DRAFT reconciliation for a stock manager")
		self.assertNotIn("_receipt_cost", text)


if __name__ == "__main__":
	unittest.main()
