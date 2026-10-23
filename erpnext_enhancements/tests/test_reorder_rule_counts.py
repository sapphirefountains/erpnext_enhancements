# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests that a reorder rule counts as a rule by existing (v1.731.1).

v1.728.0 let "Reorder at" be 0 (running out of all stock is a normal place to reorder). The KPI
dashboards' stock counts still asked for a level *above* 0, so an item whose rule is "reorder at 0,
buy 12" was not a stocked item: not in ``Stocked Items Out of Stock`` (the item that is out, which is
the point of such a rule), not in the denominator of ``Stocked Items Counted (90d)``. This runs the
**real** ``kpi_dashboards.snapshots._operations_metrics`` against SQLite tables holding the cases,
so the counts are what the queries return and not what their text says.

A rule is ERPNext's own trigger test, ``reorder_level or reorder_qty``: a level above 0, or a
quantity to buy. Pinned both ways: the level-0 rule joins every count, a rule that only has a level
(older rows) stays in, and a row with neither is not a rule.

The Stock Scan side (``stock_scan._has_reorder_rule``) is in ``test_bin_manager``, on that suite's stub.

Run: python -m unittest erpnext_enhancements.tests.test_reorder_rule_counts -v
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.tests import sqlite_sql_helper as sqlite_sql  # noqa: E402

snapshots = None
FRAPPE = None

DDL = (
	"create table `tabItem Reorder` (parent text, parenttype text, warehouse text, "
	"warehouse_reorder_level real, warehouse_reorder_qty real)",
	"create table `tabBin` (item_code text, warehouse text, actual_qty real, stock_value real, valuation_rate real)",
	"create table `tabStock Ledger Entry` (item_code text, voucher_type text, is_cancelled int, posting_date text)",
)


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda s: s
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.get_all = lambda *a, **k: []
	frappe.db = types.SimpleNamespace(
		sql=None,
		exists=lambda doctype, name=None: True,
		has_column=lambda doctype, column: True,
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.flt = lambda v, precision=None: float(v or 0)
	utils.add_days = lambda d, n: "2026-07-24" if n == -90 else "2026-09-22"
	utils.add_months = lambda d, n: d
	utils.getdate = lambda v=None: v
	utils.nowdate = lambda: "2026-10-22"
	utils.now_datetime = lambda: None
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	attribution = types.ModuleType("erpnext_enhancements.crm_enhancements.attribution")
	attribution.UNKNOWN_LEAD_SOURCE = "Unknown"
	sys.modules["erpnext_enhancements.crm_enhancements.attribution"] = attribution
	return frappe


def setUpModule():
	global snapshots, FRAPPE
	for module in list(sys.modules):
		if module.startswith("erpnext_enhancements") and module != "erpnext_enhancements.tests.sqlite_sql_helper":
			sys.modules.pop(module, None)
	FRAPPE = _install_frappe_stub()
	from erpnext_enhancements.kpi_dashboards import snapshots as mod

	snapshots = mod
	# The store-run and naming helpers read tables this fixture does not have; they are not under test.
	snapshots._store_run_suppliers = lambda: []
	snapshots._party_naming_pct = lambda doctype: None


def _metrics(rules, bins, counts):
	"""``_operations_metrics`` over these rows: {kpi_key: value}."""
	conn = sqlite_sql.connect(*DDL)
	conn.executemany("insert into `tabItem Reorder` values (?, 'Item', 'Bin A', ?, ?)", rules)
	conn.executemany("insert into `tabBin` values (?, 'Bin A', ?, 0, 1)", bins)
	conn.executemany("insert into `tabStock Ledger Entry` values (?, 'Stock Reconciliation', 0, ?)", counts)
	FRAPPE.db.sql = sqlite_sql.sql_for(conn, only=("tabItem Reorder", "tabBin", "tabStock Ledger Entry"))
	try:
		return {row["kpi_key"]: row["value"] for row in snapshots._operations_metrics()["values"]}
	finally:
		conn.close()


# item, level, qty
RULES = [
	("AT-ZERO-OUT", 0, 12),  # reorder when it runs out; none left
	("BELOW", 4, 10),  # 2 on hand, level 4
	("LEVEL-ONLY", 4, 0),  # an older rule: a level and no quantity
	("NO-RULE", 0, 0),  # a row with neither is not a rule
	("TWO-ROWS", 3, 6),  # one item, two warehouses: counted once
	("TWO-ROWS", 3, 6),
	("AT-ZERO-STOCKED", 0, 5),  # reorder at 0; 7 on hand
]
BINS = [
	("AT-ZERO-OUT", 0),
	("BELOW", 2),
	("LEVEL-ONLY", 10),
	("NO-RULE", 0),
	("TWO-ROWS", 3),
	("AT-ZERO-STOCKED", 7),
]
# counted inside the 90 days (since 2026-07-24), and one that is too old
COUNTS = [("AT-ZERO-STOCKED", "2026-10-01"), ("BELOW", "2026-09-01"), ("LEVEL-ONLY", "2026-01-01")]


class TestARuleCountsByExisting(unittest.TestCase):
	def setUp(self):
		self.m = _metrics(RULES, BINS, COUNTS)

	def test_a_rule_that_reorders_at_zero_is_out_of_stock_when_it_is(self):
		# AT-ZERO-OUT only; NO-RULE has no stock either and is not a stocked item.
		self.assertEqual(self.m["stocked_items_out"], 1)

	def test_below_reorder_is_at_or_under_the_level(self):
		# on hand <= level, ERPNext's own trigger: BELOW (2 <= 4), AT-ZERO-OUT (0 <= 0) and TWO-ROWS (3 <= 3, once
		# although it has two rows). LEVEL-ONLY (10 > 4) and AT-ZERO-STOCKED (7 > 0) are not; NO-RULE has no rule.
		self.assertEqual(self.m["items_below_reorder"], 3)

	def test_an_item_exactly_at_its_level_is_below(self):
		"""Before v1.731.1 the count asked for ``on hand < level``. With no negative stock on prod a rule at 0
		could never be below, and an item sitting exactly at its level (the board lists it) was missed."""
		for name, level, on_hand, expected in (
			("level 0, on hand 0", 0, 0, 1),
			("level 5, on hand 5", 5, 5, 1),
			("level 5, on hand 6", 5, 6, 0),
			("level 0, on hand 1", 0, 1, 0),
		):
			with self.subTest(case=name):
				m = _metrics([("X", level, 12)], [("X", on_hand)], [])
				self.assertEqual(m["items_below_reorder"], expected)

	def test_the_count_coverage_share_includes_the_level_zero_rules(self):
		# stocked: AT-ZERO-OUT, BELOW, LEVEL-ONLY, TWO-ROWS, AT-ZERO-STOCKED = 5; counted since
		# 2026-07-24: AT-ZERO-STOCKED and BELOW = 2 (LEVEL-ONLY was counted too long ago).
		self.assertAlmostEqual(self.m["stocked_items_counted_90"], 40.0)

	def test_an_older_rule_with_only_a_level_is_still_a_stocked_item(self):
		only = _metrics([("LEVEL-ONLY", 4, 0)], [("LEVEL-ONLY", 0)], [])
		self.assertEqual((only["stocked_items_out"], only["items_below_reorder"]), (1, 1))

	def test_a_row_with_neither_number_is_no_rule(self):
		none = _metrics([("NO-RULE", 0, 0)], [("NO-RULE", 0)], [])
		self.assertNotIn("stocked_items_counted_90", none)  # nothing stocked: no share to publish
		self.assertEqual((none["stocked_items_out"], none["items_below_reorder"]), (0, 0))

	def test_the_four_counts_share_one_definition(self):
		"""Not four copies that can drift: every query that filters on a rule uses the constant."""
		import inspect

		source = inspect.getsource(snapshots._operations_metrics)
		self.assertEqual(source.count("+ STOCKED_RULE_SQL"), 4)
		self.assertNotIn("warehouse_reorder_level,0) > 0", source)


if __name__ == "__main__":
	unittest.main()
