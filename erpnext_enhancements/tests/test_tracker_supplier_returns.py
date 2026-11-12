# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Procurement Tracker's "returned to the supplier", run against the REAL Supplier Return
module (v1.807.0) instead of a stand-in.

``tests/test_project_stock.py`` holds the tracker's adapter to the *shape* the Supplier Return
session sent while its code was still on a branch. That is a test of an agreement. This one is a
test of the thing: ``inventory_enhancements/supplier_returns.returned_lines`` itself, on
``tests/test_supplier_returns``' own stub (real SQL on SQLite, real Supplier Return documents made
through that module's own functions), read through ``project_stock._returns`` and
``project_stock_rules.return_summary`` exactly as ``places_for`` reads it.

What it would catch that the stand-in cannot: a key the adapter reads being renamed or dropped, a
status that stops (or starts) meaning "the goods have left", a quantity changing unit, and the
project filter no longer matching a return's lines.

Reuses that suite's stub, so it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_tracker_supplier_returns -v
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.tests import test_supplier_returns as base

PROJECT = "PRJ-TEST"
PUMP = "TST-PUMP-1"  # a stock item, receipt line PRI-1 (10 received)
FITTING = "TST-FITTING-1"  # a non-stock item, receipt line PRI-2

_OWN = ("erpnext_enhancements.inventory_enhancements.project_stock",)

stock = None
rules = None


def setUpModule():
	global stock, rules
	for name in _OWN:
		sys.modules.pop(name, None)
	base.setUpModule()
	from erpnext_enhancements.inventory_enhancements import project_stock as stock_module
	from erpnext_enhancements.inventory_enhancements import project_stock_rules as rules_module

	stock, rules = stock_module, rules_module


def tearDownModule():
	for name in _OWN:
		sys.modules.pop(name, None)
	base.tearDownModule()


class TestTheRealReturnedLines(unittest.TestCase):
	def setUp(self):
		base.reset()

	def test_the_keys_the_tracker_reads_are_the_ones_the_module_returns(self):
		"""``return_summary`` reads six keys of each line. All six are in the module's own list."""
		read = {"supplier_return", "status", "item_code", "stock_qty", "qty", "gone", "open"}
		self.assertLessEqual(read, set(base.rules.RETURNED_LINE_KEYS))
		base.new_return({"PRI-1": 2})
		line = base.core.returned_lines(project=PROJECT)[0]
		self.assertEqual(tuple(line), base.rules.RETURNED_LINE_KEYS)
		self.assertEqual((line["item_code"], line["project"]), (PUMP, PROJECT))

	def test_a_return_being_arranged_is_listed_and_has_not_left(self):
		name = base.new_return({"PRI-1": 3})
		summary = stock._returns(PROJECT)
		self.assertEqual(
			summary,
			{PUMP: {"gone": 0, "returns": [{"supplier_return": name, "status": "Draft", "qty": 3, "gone": False, "open": True}]}},
		)
		# Nothing has left, so "returned to the supplier" is not a place yet.
		places = rules.item_places([], {}, returned=summary[PUMP]["gone"])
		self.assertEqual(places, [])

	def test_once_sent_back_the_goods_are_a_place_and_the_return_is_named(self):
		sent = base.sent_back({"PRI-1": 2})
		asked = base.new_return({"PRI-1": 3, "PRI-2": 1})
		summary = stock._returns(PROJECT)
		self.assertEqual(summary[PUMP]["gone"], 2)
		self.assertEqual(
			[(r["supplier_return"], r["status"], r["qty"], r["gone"]) for r in summary[PUMP]["returns"]],
			[(sent, "Sent Back", 2, True), (asked, "Draft", 3, False)],
		)
		# A non-stock item's return is on record too, though its place is not tracked.
		self.assertEqual(
			[(r["supplier_return"], r["qty"], r["gone"]) for r in summary[FITTING]["returns"]], [(asked, 1, False)]
		)
		places = rules.item_places([], {}, returned=summary[PUMP]["gone"])
		self.assertEqual([(p["kind"], p["qty"], p["text"]) for p in places], [("returned", 2, "2 returned to the supplier")])

	def test_the_ledgers_own_return_rows_and_the_supplier_return_are_never_added(self):
		"""The purchase return behind a Sent Back return posts the ledger rows the tracker also
		reads. Two of ten went back: two, not four."""
		base.sent_back({"PRI-1": 2})
		gone = stock._returns(PROJECT)[PUMP]["gone"]
		ledger = [
			{"warehouse": "Shelf", "kind": rules.LEDGER_MOVE, "qty": 10},
			{"warehouse": "Shelf", "kind": rules.LEDGER_SUPPLIER_RETURN, "qty": -2},
		]
		places = rules.item_places(ledger, {"Shelf": 8}, returned=gone)
		self.assertEqual({p["kind"]: p["qty"] for p in places}, {"shop_bin": 8, "returned": 2})

	def test_every_status_from_sent_back_on_means_gone_and_canceled_or_denied_are_left_out(self):
		"""Read from the module's own vocabulary, so a status added there is judged here."""
		module = base.rules
		for status in sorted(module.GONE):
			line = module.returned_line({"supplier_return": "SRET-X", "status": status, "item_code": PUMP, "qty": 2, "conversion_factor": 12})
			summary = rules.return_summary([line])
			self.assertEqual(summary[PUMP]["gone"], 24, f"{status}: the stock unit, not the purchase unit")
			self.assertTrue(summary[PUMP]["returns"][0]["gone"], status)
		for status in sorted(module.NOT_COUNTED):
			self.assertIsNone(module.returned_line({"supplier_return": "SRET-X", "status": status, "item_code": PUMP, "qty": 2}))
		self.assertIn("Sent Back", module.GONE)
		self.assertFalse({"Draft", "Requested", "Approved by Supplier"} & set(module.GONE))

	def test_another_job_and_no_job_have_no_returns(self):
		base.sent_back({"PRI-1": 2})
		self.assertEqual(stock._returns("PRJ-OTHER"), {})
		self.assertEqual(stock._returns(""), {})


if __name__ == "__main__":
	unittest.main()
