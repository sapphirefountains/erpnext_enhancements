"""Bench-free tests for the Warehouse Transactions report and its hub links (v1.797.0,
TASK-2026-03136 and TASK-2026-03137).

What is held:

* **The report record.** A Script Report in Inventory Enhancements on Stock Ledger Entry, with the
  roles ERPNext gives its own Stock Ledger report (Stock User, Accounts Manager).
* **Optional filters.** Only the dates are required. A blank Item, Warehouse or User adds no
  condition, which is the reason this is not a Query Report: Frappe 16.53 sends only filled-in
  filters, and a Query Report's ``%(name)s`` placeholder fails on the missing ones.
* **The conditions.** Canceled entries are always left out; the dates default to the last 30 days;
  a From Date after the To Date is refused; a warehouse covers the bins under it; the User filter
  matches the entry's owner.
* **Count adjustments.** ERPNext 16.52.1 stores a Stock Reconciliation entry with ``actual_qty = 0``
  and the counted balance in ``qty_after_transaction``. The change shown is that balance minus the
  previous live entry's for the same item and warehouse, in ledger order (``posting_datetime``,
  then ``creation``), as ERPNext's own Stock Ledger report does it: a previous entry before From
  Date or by someone the User filter hides still counts, a canceled one or another bin's does not,
  and a first count is its whole balance.
* **Permissions.** Every ledger read comes through ``frappe.get_list`` (permission-checked), never
  ``get_all`` or SQL.
* **The rows.** Every field read exists on Stock Ledger Entry at ERPNext v16.52.1 (production);
  item names and full names are looked up once each, and a missing one reads blank. The User
  column is plain text: Desk User holds only ``select`` on User.
* **The summary.** It counts ledger lines: a positive change adds, a negative one removes, a zero
  neither, so a transfer's two legs count once each.
* **The hubs.** The Inventory Enhancements workspace has the shortcut and the Reports-card link,
  the Operations Hub has the Inventory-card link, card counts agree, both files carry the new
  stamp, and the force-import patch is registered after model sync, imports each workspace in its
  own savepoint, rolls a failed import back, restores ``frappe.flags.in_import`` and never raises.

The ``frappe`` stub holds a small in-memory ledger and applies ``get_list``'s filters, ordering and
field list to it, so a query that asked for the wrong rows or too few fields fails here. Installed
in ``setUpModule`` and removed afterwards.

Run: python -m unittest erpnext_enhancements.tests.test_warehouse_transactions -v
"""

import ast
import datetime as dt
import itertools
import json
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
REPORT_DIR = APP / "inventory_enhancements" / "report" / "warehouse_transactions"
REPORT_JSON = REPORT_DIR / "warehouse_transactions.json"
REPORT_JS = REPORT_DIR / "warehouse_transactions.js"
INV_WS = (
	APP / "inventory_enhancements" / "workspace" / "inventory_enhancements" / "inventory_enhancements.json"
)
OPS_HUB = APP / "enhancements_core" / "workspace" / "operations_hub" / "operations_hub.json"
PATCHES_TXT = APP / "patches.txt"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

NAME = "Warehouse Transactions"
TODAY = dt.date(2026, 11, 5)
STAMP = "2026-11-05 10:00"
SR = "Stock Reconciliation"

#: Stock Ledger Entry's fields at ERPNext v16.52.1 (production), read from the tag's
#: stock_ledger_entry.json, plus the standard columns every doctype has.
SLE_FIELDS = {
	"item_code",
	"serial_no",
	"batch_no",
	"warehouse",
	"posting_date",
	"posting_time",
	"voucher_type",
	"voucher_no",
	"voucher_detail_no",
	"actual_qty",
	"incoming_rate",
	"outgoing_rate",
	"stock_uom",
	"qty_after_transaction",
	"valuation_rate",
	"stock_value",
	"stock_value_difference",
	"stock_queue",
	"project",
	"company",
	"fiscal_year",
	"is_cancelled",
	"to_rename",
	"dependant_sle_voucher_detail_no",
	"recalculate_rate",
	"serial_and_batch_bundle",
	"has_batch_no",
	"has_serial_no",
	"is_adjustment_entry",
	"auto_created_serial_and_batch_bundle",
	"posting_datetime",
	"name",
	"owner",
	"creation",
	"modified",
	"modified_by",
	"docstatus",
	"idx",
}

STUBBED = ("frappe", "frappe.utils", "frappe.modules", "frappe.modules.import_file")
OURS = (
	"erpnext_enhancements.inventory_enhancements.report.warehouse_transactions.warehouse_transactions",
	"erpnext_enhancements.patches.warehouse_transactions_hub_links",
)
_saved = {}
report = None
patch = None
frappe_stub = None

#: What the stubs record and return; reset before each test.
STATE = {}
_creation = itertools.count(1)


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)


class _Thrown(Exception):
	pass


def _reset(ledger=(), items=None, users=None, descendants=None):
	STATE.clear()
	STATE.update(
		ledger=[dict(row) for row in ledger],
		items=items or {},
		users=users or {},
		descendants=descendants or {},
		get_list=[],
		get_all=[],
		logged=[],
		imported=[],
		fail_import=set(),
		fail_rollback=False,
		savepoints=[],
		rollbacks=[],
		cleared=0,
	)
	if frappe_stub is not None:
		frappe_stub.flags.in_import = False


def _matches(row, filters):
	for field, cond in (filters or {}).items():
		value = row.get(field)
		if isinstance(cond, list):
			op, arg = cond
			if op == "in":
				ok = value in arg
			elif op == "between":
				ok = arg[0] <= value <= arg[1]
			elif op == "<=":
				ok = value <= arg
			else:
				raise AssertionError(f"stub does not know {op}")
		else:
			ok = value == cond
		if not ok:
			return False
	return True


def _ordered(rows, order_by):
	for part in reversed([p.strip() for p in order_by.split(",")]):
		field, direction = part.split()
		rows.sort(key=lambda r, f=field: r[f], reverse=direction == "desc")
	return rows


def setUpModule():
	global report, patch, frappe_stub
	for name in STUBBED + OURS:
		_saved[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	frappe._ = lambda text: text
	frappe._dict = _Dict
	frappe.flags = _Dict(in_import=False)

	def throw(message, *args, **kwargs):
		raise _Thrown(message)

	def get_list(doctype, filters=None, fields=None, order_by=None, **kwargs):
		assert doctype == "Stock Ledger Entry", doctype
		STATE["get_list"].append(
			dict(doctype=doctype, filters=filters, fields=fields, order_by=order_by, **kwargs)
		)
		rows = _ordered([r for r in STATE["ledger"] if _matches(r, filters)], order_by)
		# Only the fields asked for, so code that relies on an unrequested field reads None here.
		return [_Dict({f: r.get(f) for f in fields}) for r in rows]

	def get_all(doctype, filters=None, fields=None, **kwargs):
		STATE["get_all"].append(dict(doctype=doctype, filters=filters, fields=fields))
		source = {"Item": STATE["items"], "User": STATE["users"]}[doctype]
		field = fields[1]
		names = filters["name"][1]
		return [_Dict({"name": n, field: source[n]}) for n in names if n in source]

	def get_descendants(doctype, name):
		assert doctype == "Warehouse"
		return STATE["descendants"].get(name, [])

	def savepoint(name):
		STATE["savepoints"].append(name)

	def rollback(save_point=None):
		STATE["rollbacks"].append(save_point)
		if STATE["fail_rollback"]:
			raise RuntimeError("rollback failed")

	frappe.throw = throw
	frappe.get_list = get_list
	frappe.get_all = get_all
	frappe.db = types.SimpleNamespace(get_descendants=get_descendants, savepoint=savepoint, rollback=rollback)
	frappe.get_app_path = lambda app, *parts: str(APP.joinpath(*parts))
	frappe.log_error = lambda title=None, message=None, **kw: STATE["logged"].append((title, message))
	frappe.get_traceback = lambda: "traceback"

	def clear_cache():
		STATE["cleared"] += 1

	frappe.clear_cache = clear_cache

	utils = types.ModuleType("frappe.utils")

	def getdate(value=None):
		if value is None:
			return TODAY
		if isinstance(value, dt.datetime):
			return value.date()
		if isinstance(value, dt.date):
			return value
		return dt.date.fromisoformat(str(value)[:10])

	utils.getdate = getdate
	utils.nowdate = lambda: TODAY.isoformat()
	utils.add_days = lambda date, days: getdate(date) + dt.timedelta(days=days)
	utils.flt = lambda value, precision=None: float(value or 0)
	frappe.utils = utils

	modules = types.ModuleType("frappe.modules")
	import_file = types.ModuleType("frappe.modules.import_file")

	def import_file_by_path(path, force=False, reset_permissions=False, **kwargs):
		# import_doc sets in_import first and clears it only after a successful insert.
		frappe.flags.in_import = True
		if Path(path).stem in STATE["fail_import"]:
			raise RuntimeError("insert failed after the old row was deleted")
		STATE["imported"].append((Path(path), force, reset_permissions))
		frappe.flags.in_import = False

	import_file.import_file_by_path = import_file_by_path
	modules.import_file = import_file
	frappe.modules = modules

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.modules"] = modules
	sys.modules["frappe.modules.import_file"] = import_file
	frappe_stub = frappe

	from erpnext_enhancements.inventory_enhancements.report.warehouse_transactions import (
		warehouse_transactions as report_module,
	)
	from erpnext_enhancements.patches import warehouse_transactions_hub_links as patch_module

	report = report_module
	patch = patch_module


def tearDownModule():
	for name in STUBBED + OURS:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


def sle(
	item="ITEM-A",
	qty=1.0,
	after=None,
	owner="a@example.com",
	warehouse="Bin A1 - X",
	day=TODAY,
	time="09:00",
	voucher_type="Stock Entry",
	cancelled=0,
	name=None,
):
	"""One Stock Ledger Entry as the table holds it. A reconciliation stores ``actual_qty = 0``,
	as ERPNext 16.52.1 writes it, so pass ``qty=0`` and the counted balance as ``after``."""
	seq = next(_creation)
	hours, minutes = (int(x) for x in time.split(":"))
	return {
		"name": name or f"SLE-{seq:04d}",
		"posting_date": day,
		"posting_time": dt.timedelta(hours=hours, minutes=minutes),
		"posting_datetime": dt.datetime.combine(day, dt.time(hours, minutes)),
		"creation": seq,
		"item_code": item,
		"warehouse": warehouse,
		"actual_qty": qty,
		"qty_after_transaction": qty if after is None else after,
		"voucher_type": voucher_type,
		"voucher_no": f"{voucher_type[:3].upper()}-{seq:04d}",
		"owner": owner,
		"is_cancelled": cancelled,
	}


def count(item="ITEM-A", after=0.0, **kw):
	return sle(item=item, qty=0, after=after, voucher_type=SR, **kw)


def rows_by_name(result):
	return {r["voucher_no"]: r for r in result[1]}


class TestTheReportRecord(unittest.TestCase):
	def setUp(self):
		self.doc = json.loads(REPORT_JSON.read_text(encoding="utf-8"))

	def test_is_a_standard_script_report_in_inventory(self):
		self.assertEqual(self.doc["name"], NAME)
		self.assertEqual(self.doc["report_name"], NAME)
		self.assertEqual(self.doc["report_type"], "Script Report")
		self.assertEqual(self.doc["module"], "Inventory Enhancements")
		self.assertEqual(self.doc["is_standard"], "Yes")
		self.assertEqual(self.doc["ref_doctype"], "Stock Ledger Entry")
		self.assertEqual(self.doc["disabled"], 0)

	def test_roles_match_erpnexts_stock_ledger(self):
		# Stock Ledger at ERPNext v16.52.1: Stock User, Accounts Manager. Those two are also the
		# roles with read + report on Stock Ledger Entry, which the run checks.
		self.assertEqual({r["role"] for r in self.doc["roles"]}, {"Stock User", "Accounts Manager"})


class TestConditions(unittest.TestCase):
	def setUp(self):
		_reset()

	def conditions(self, **filters):
		return report.build_conditions(_Dict(filters))

	def test_defaults_to_the_last_30_days_and_skips_canceled(self):
		self.assertEqual(
			self.conditions(),
			{"is_cancelled": 0, "posting_date": ["between", [TODAY - dt.timedelta(days=30), TODAY]]},
		)
		self.assertEqual(report.DEFAULT_DAYS, 30)

	def test_blank_filters_add_nothing(self):
		# The whole point of a Script Report here: absent and blank both mean "any".
		base = self.conditions(from_date="2026-10-01", to_date="2026-10-31")
		blank = self.conditions(
			from_date="2026-10-01", to_date="2026-10-31", item_code="", warehouse=None, user=""
		)
		self.assertEqual(base, blank)
		self.assertEqual(set(base), {"is_cancelled", "posting_date"})

	def test_explicit_dates(self):
		got = self.conditions(from_date="2026-10-01", to_date="2026-10-31")
		self.assertEqual(got["posting_date"], ["between", [dt.date(2026, 10, 1), dt.date(2026, 10, 31)]])

	def test_only_a_to_date_counts_back_from_it(self):
		got = self.conditions(to_date="2026-10-31")
		self.assertEqual(got["posting_date"], ["between", [dt.date(2026, 10, 1), dt.date(2026, 10, 31)]])

	def test_one_day_is_allowed(self):
		got = self.conditions(from_date="2026-10-31", to_date="2026-10-31")
		self.assertEqual(got["posting_date"], ["between", [dt.date(2026, 10, 31), dt.date(2026, 10, 31)]])

	def test_from_after_to_is_refused(self):
		with self.assertRaises(_Thrown):
			self.conditions(from_date="2026-11-02", to_date="2026-11-01")

	def test_item_and_user(self):
		got = self.conditions(item_code="ITEM-A", user="a@example.com")
		self.assertEqual(got["item_code"], "ITEM-A")
		# The person is the entry's owner: whoever submitted the voucher.
		self.assertEqual(got["owner"], "a@example.com")
		self.assertNotIn("user", got)

	def test_a_warehouse_covers_the_bins_under_it(self):
		_reset(descendants={"Stores - X": ["Bin A1 - X", "Bin A2 - X"]})
		got = self.conditions(warehouse="Stores - X")
		self.assertEqual(got["warehouse"], ["in", ["Stores - X", "Bin A1 - X", "Bin A2 - X"]])

	def test_a_bin_is_just_itself(self):
		got = self.conditions(warehouse="Bin A1 - X")
		self.assertEqual(got["warehouse"], ["in", ["Bin A1 - X"]])


class TestExecute(unittest.TestCase):
	def test_reads_through_get_list_with_fields_the_doctype_has(self):
		_reset()
		report.execute({"from_date": "2026-10-01", "to_date": "2026-10-31"})
		# No counts among the rows, so no second ledger read.
		self.assertEqual(len(STATE["get_list"]), 1)
		call = STATE["get_list"][0]
		self.assertEqual(call["doctype"], "Stock Ledger Entry")
		self.assertEqual(call["filters"]["is_cancelled"], 0)
		self.assertLessEqual(set(call["fields"]), SLE_FIELDS)
		# Frappe 16 refuses SQL functions written as field strings.
		for field in call["fields"]:
			self.assertRegex(field, r"^[a-z_]+$")
		self.assertEqual(call["order_by"], "posting_datetime desc, creation desc")
		self.assertNotIn("limit", call)
		# No rows, so no lookups.
		self.assertEqual(STATE["get_all"], [])

	def test_never_reads_the_ledger_without_permissions(self):
		# Walk the parsed code, not the text: the docstring names frappe.db.sql on purpose.
		tree = ast.parse((REPORT_DIR / "warehouse_transactions.py").read_text(encoding="utf-8"))
		get_lists = 0
		for node in ast.walk(tree):
			if not isinstance(node, ast.Call):
				continue
			called = ast.unparse(node.func)
			with self.subTest(call=called):
				self.assertNotIn(called, {"frappe.db.sql", "frappe.db.multisql", "frappe.qb.from_"})
				self.assertNotIn("ignore_permissions", [k.arg for k in node.keywords])
				# get_all skips permissions, so it may only fetch display names: directly with a
				# literal doctype, or through _lookup, whose callers name Item and User.
				first = node.args[0] if node.args else None
				if called == "frappe.get_all" and isinstance(first, ast.Constant):
					self.assertIn(first.value, {"Item", "User"})
				if called == "_lookup":
					self.assertIn(ast.literal_eval(first), {"Item", "User"})
				if called == "frappe.get_list":
					get_lists += 1
					self.assertEqual(ast.literal_eval(first), "Stock Ledger Entry")
		# The report's rows and the balances before a count.
		self.assertEqual(get_lists, 2)

	def test_shapes_rows_with_names(self):
		_reset(
			ledger=[
				sle("ITEM-A", 5, owner="a@example.com", time="09:00"),
				sle("ITEM-B", -2, after=8, owner="b@example.com", time="09:10"),
				sle("ITEM-A", -1, after=4, owner="a@example.com", time="09:20"),
				sle("GONE", 3, owner="gone@example.com", time="09:30"),
			],
			items={"ITEM-A": "Pump, 1 HP", "ITEM-B": "Nozzle, jet"},
			users={"a@example.com": "Alex Able", "b@example.com": "Bo Baker"},
		)
		columns, rows, message, chart, summary = report.execute({})
		self.assertIsNone(message)
		self.assertIsNone(chart)
		# Newest first.
		self.assertEqual([r["item_name"] for r in rows], ["", "Pump, 1 HP", "Nozzle, jet", "Pump, 1 HP"])
		self.assertEqual([r["full_name"] for r in rows], ["", "Alex Able", "Bo Baker", "Alex Able"])
		self.assertEqual(
			[r["user"] for r in rows], ["gone@example.com", "a@example.com", "b@example.com", "a@example.com"]
		)
		self.assertEqual([r["qty_change"] for r in rows], [3, -1, -2, 5])
		self.assertEqual([r["balance_after"] for r in rows], [3, 4, 8, 5])
		# One lookup per doctype, each name once.
		self.assertEqual([c["doctype"] for c in STATE["get_all"]], ["Item", "User"])
		self.assertEqual(STATE["get_all"][0]["filters"]["name"][1], ["GONE", "ITEM-A", "ITEM-B"])
		self.assertEqual(
			STATE["get_all"][1]["filters"]["name"][1], ["a@example.com", "b@example.com", "gone@example.com"]
		)
		# Every row carries every column.
		fieldnames = [c["fieldname"] for c in columns]
		for row in rows:
			self.assertEqual(set(row), set(fieldnames))

	def test_leaves_out_canceled_and_out_of_window_rows(self):
		_reset(
			ledger=[
				sle("A", 5, time="09:00"),
				sle("A", 9, cancelled=1, time="09:05"),
				sle("A", 7, day=TODAY - dt.timedelta(days=45)),
			]
		)
		rows = report.execute({})[1]
		self.assertEqual([r["qty_change"] for r in rows], [5])

	def test_summary_counts_ledger_lines(self):
		_reset(
			ledger=[
				sle("A", 5, owner="a@example.com"),
				sle("B", -2, owner="b@example.com"),
				sle("C", 0, after=4, owner="a@example.com", voucher_type="Stock Entry"),
				sle("D", None, after=0, owner="c@example.com"),
				sle("E", 0.5, owner="a@example.com"),
				# A transfer: one line out of one bin, one into another. Both count.
				sle("F", -3, after=0, owner="a@example.com", warehouse="Bin A1 - X"),
				sle("F", 3, owner="a@example.com", warehouse="Bin A2 - X"),
			]
		)
		summary = report.execute({})[4]
		values = {card["label"]: card["value"] for card in summary}
		self.assertEqual(
			values,
			{"Ledger lines": 7, "Lines adding stock": 3, "Lines removing stock": 2, "People": 3},
		)


class TestCountAdjustments(unittest.TestCase):
	"""ERPNext 16.52.1 stores a count's entry with actual_qty 0; the change is derived."""

	def test_change_is_the_count_minus_the_previous_balance(self):
		_reset(ledger=[sle("A", 5, after=5, time="09:00"), count("A", after=3, time="10:00")])
		result = report.execute({})
		counted = next(r for r in result[1] if r["voucher_type"] == SR)
		self.assertEqual(counted["qty_change"], -2)
		self.assertEqual(counted["balance_after"], 3)
		cards = {c["label"]: c["value"] for c in result[4]}
		self.assertEqual(cards["Lines removing stock"], 1)
		self.assertEqual(cards["Lines adding stock"], 1)

	def test_a_count_that_finds_more_is_an_addition(self):
		_reset(ledger=[sle("A", 5, after=5, time="09:00"), count("A", after=9, time="10:00")])
		rows = report.execute({})[1]
		self.assertEqual(rows[0]["qty_change"], 4)

	def test_previous_balance_from_before_the_window(self):
		_reset(
			ledger=[
				sle("A", 10, after=10, day=dt.date(2026, 9, 1), voucher_type="Purchase Receipt"),
				count("A", after=12),
			]
		)
		rows = report.execute({})[1]
		# The receipt is outside the 30 days and not a row, but it is the balance before.
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["qty_change"], 2)

	def test_previous_entry_by_someone_the_user_filter_hides(self):
		_reset(
			ledger=[
				sle("A", 10, after=10, owner="a@example.com", time="08:00"),
				sle("A", 4, after=14, owner="b@example.com", time="09:00"),
				count("A", after=12, owner="a@example.com", time="10:00"),
			]
		)
		rows = report.execute({"user": "a@example.com"})[1]
		self.assertEqual([r["user"] for r in rows], ["a@example.com", "a@example.com"])
		# Against b's 14, not a's own 10.
		self.assertEqual(rows[0]["qty_change"], -2)

	def test_a_first_count_is_the_whole_balance(self):
		_reset(ledger=[count("A", after=6)])
		self.assertEqual(report.execute({})[1][0]["qty_change"], 6)

	def test_a_canceled_entry_is_not_a_previous_balance(self):
		_reset(
			ledger=[
				sle("A", 5, after=5, time="08:00"),
				sle("A", 95, after=100, cancelled=1, time="09:00"),
				count("A", after=4, time="10:00"),
			]
		)
		counted = next(r for r in report.execute({})[1] if r["voucher_type"] == SR)
		self.assertEqual(counted["qty_change"], -1)

	def test_other_bins_and_items_do_not_mix(self):
		_reset(
			ledger=[
				sle("A", 5, after=5, warehouse="Bin A1 - X", time="08:00"),
				sle("A", 50, after=50, warehouse="Bin A2 - X", time="08:30"),
				sle("B", 70, after=70, warehouse="Bin A1 - X", time="08:45"),
				count("A", after=7, warehouse="Bin A1 - X", time="10:00"),
			]
		)
		counted = next(r for r in report.execute({})[1] if r["voucher_type"] == SR)
		self.assertEqual(counted["qty_change"], 2)

	def test_ties_on_the_same_stamp_follow_creation(self):
		_reset(
			ledger=[
				sle("A", 5, after=5, time="10:00"),
				sle("A", 3, after=8, time="10:00"),
				count("A", after=6, time="10:00"),
			]
		)
		counted = next(r for r in report.execute({})[1] if r["voucher_type"] == SR)
		self.assertEqual(counted["qty_change"], -2)

	def test_two_counts_in_a_row(self):
		_reset(
			ledger=[
				sle("A", 5, after=5, time="08:00"),
				count("A", after=3, time="09:00", name="SLE-C1"),
				count("A", after=8, time="10:00", name="SLE-C2"),
			]
		)
		by_balance = {r["balance_after"]: r["qty_change"] for r in report.execute({})[1]}
		self.assertEqual(by_balance, {5: 5, 3: -2, 8: 5})

	def test_a_reconciliation_that_stores_a_change_keeps_it(self):
		_reset(
			ledger=[sle("A", 5, after=5, time="08:00"), sle("A", -5, after=0, voucher_type=SR, time="09:00")]
		)
		rows = report.execute({})[1]
		self.assertEqual(rows[0]["qty_change"], -5)
		# Nothing to derive, so no second ledger read.
		self.assertEqual(len(STATE["get_list"]), 1)

	def test_the_balance_read_is_permission_checked_and_in_ledger_order(self):
		_reset(ledger=[sle("A", 5, after=5, time="08:00"), count("A", after=3, time="10:00")])
		report.execute({"item_code": "A"})
		self.assertEqual(len(STATE["get_list"]), 2)
		call = STATE["get_list"][1]
		self.assertEqual(call["doctype"], "Stock Ledger Entry")
		self.assertEqual(call["filters"]["is_cancelled"], 0)
		self.assertEqual(call["filters"]["item_code"], ["in", ["A"]])
		self.assertEqual(call["filters"]["warehouse"], ["in", ["Bin A1 - X"]])
		self.assertEqual(call["filters"]["posting_date"], ["<=", TODAY])
		# No lower date bound: a balance set before From Date must be found.
		self.assertNotIn("owner", call["filters"])
		self.assertEqual(call["order_by"], "posting_datetime asc, creation asc")
		self.assertLessEqual(set(call["fields"]), SLE_FIELDS)

	def test_production_shape(self):
		"""A count sweep as production writes it: per bin, a receipt long before, then counts."""
		ledger = []
		for i in range(5):
			ledger.append(
				sle(
					f"P{i}",
					10,
					after=10,
					warehouse=f"Bin {i}",
					day=dt.date(2026, 9, 1),
					voucher_type="Purchase Receipt",
				)
			)
			ledger.append(count(f"P{i}", after=10 + (i - 2), warehouse=f"Bin {i}"))
		_reset(ledger=ledger)
		result = report.execute({})
		self.assertEqual(sorted(r["qty_change"] for r in result[1]), [-2, -1, 0, 1, 2])
		cards = {c["label"]: c["value"] for c in result[4]}
		self.assertEqual((cards["Lines adding stock"], cards["Lines removing stock"]), (2, 2))


class TestColumnsAndFilters(unittest.TestCase):
	def test_has_every_column_the_task_named(self):
		columns = {c["fieldname"]: c for c in report.get_columns()}
		# The task's "Actual Qty" is the Qty Change column: the SLE's actual_qty, or the change a
		# count made, which the ledger stores as 0.
		for name in (
			"item_code",
			"qty_change",
			"warehouse",
			"posting_date",
			"posting_time",
			"voucher_type",
			"voucher_no",
			"user",
		):
			with self.subTest(column=name):
				self.assertIn(name, columns)
		self.assertIn("balance_after", columns)
		self.assertEqual(columns["item_code"]["options"], "Item")
		self.assertEqual(columns["warehouse"]["options"], "Warehouse")
		self.assertEqual(columns["voucher_no"]["fieldtype"], "Dynamic Link")
		self.assertEqual(columns["voucher_no"]["options"], "voucher_type")
		self.assertEqual(len(columns), len(report.get_columns()), "fieldnames are unique")

	def test_the_user_column_is_not_a_link(self):
		# Desk User holds only `select` on User, so a link opens onto a permission error.
		column = {c["fieldname"]: c for c in report.get_columns()}["user"]
		self.assertEqual(column["fieldtype"], "Data")
		self.assertNotIn("options", column)

	def test_js_filters(self):
		text = REPORT_JS.read_text(encoding="utf-8")
		self.assertIn(f'frappe.query_reports["{NAME}"]', text)
		fieldnames = re.findall(r'fieldname:\s*"(\w+)"', text)
		self.assertEqual(fieldnames, ["from_date", "to_date", "item_code", "warehouse", "user"])
		# Only the two dates are required; one reqd per date block.
		self.assertEqual(text.count("reqd: 1"), 2)
		self.assertIn(f"add_days(frappe.datetime.get_today(), -{report.DEFAULT_DAYS})", text)
		for doctype in ("Item", "Warehouse", "User"):
			self.assertIn(f'options: "{doctype}"', text)


class TestWhereToFindIt(unittest.TestCase):
	def _ws(self, path):
		return json.loads(path.read_text(encoding="utf-8"))

	def _card(self, ws, card):
		links = ws["links"]
		start = next(
			i for i, link in enumerate(links) if link["type"] == "Card Break" and link["label"] == card
		)
		rest = links[start + 1 :]
		end = next((i for i, link in enumerate(rest) if link["type"] == "Card Break"), len(rest))
		members = rest[:end]
		self.assertEqual(links[start]["link_count"], len(members))
		return members

	def _assert_report_link(self, members):
		rows = [m for m in members if m.get("link_to") == NAME]
		self.assertEqual(len(rows), 1)
		row = rows[0]
		self.assertEqual(row["label"], NAME)
		self.assertEqual(row["link_type"], "Report")
		# is_query_report picks the /query-report route; without it Frappe opens the Report Builder.
		self.assertEqual(row["is_query_report"], 1)

	def test_the_inventory_workspace_has_the_shortcut(self):
		ws = self._ws(INV_WS)
		self.assertIn(
			{"label": NAME, "link_to": NAME, "type": "Report"},
			[{k: s.get(k) for k in ("label", "link_to", "type")} for s in ws["shortcuts"]],
		)
		blocks = json.loads(ws["content"])
		self.assertIn(NAME, [b["data"].get("shortcut_name") for b in blocks if b["type"] == "shortcut"])
		self.assertEqual(len({b["id"] for b in blocks}), len(blocks), "block ids are unique")
		self._assert_report_link(self._card(ws, "Reports"))
		self.assertGreaterEqual(ws["modified"], STAMP)

	def test_the_operations_hub_links_it(self):
		hub = self._ws(OPS_HUB)
		self._assert_report_link(self._card(hub, "Inventory"))
		self.assertGreaterEqual(hub["modified"], STAMP)

	def test_the_patch_is_registered_after_model_sync(self):
		lines = [line.strip() for line in PATCHES_TXT.read_text(encoding="utf-8").splitlines()]
		dotted = "erpnext_enhancements.patches.warehouse_transactions_hub_links"
		self.assertEqual(lines.count(dotted), 1)
		self.assertGreater(lines.index(dotted), lines.index("[post_model_sync]"))

	def test_the_patch_force_imports_both_workspaces(self):
		_reset()
		patch.execute()
		self.assertEqual([p for p, _, _ in STATE["imported"]], [INV_WS, OPS_HUB])
		self.assertTrue(all(force for _, force, _ in STATE["imported"]))
		self.assertFalse(any(reset for _, _, reset in STATE["imported"]))
		self.assertEqual(
			STATE["savepoints"], ["wh_txn_ws_inventory_enhancements", "wh_txn_ws_operations_hub"]
		)
		self.assertEqual(STATE["rollbacks"], [])
		self.assertEqual(STATE["logged"], [])
		self.assertEqual(STATE["cleared"], 1)
		self.assertFalse(frappe_stub.flags.in_import)

	def test_a_failed_import_rolls_back_to_its_savepoint(self):
		# import_doc deletes the stored row before insert(); without the rollback, migrate would
		# commit the delete and the workspace would be gone.
		_reset()
		STATE["fail_import"].add("inventory_enhancements")
		patch.execute()
		self.assertEqual(STATE["rollbacks"], ["wh_txn_ws_inventory_enhancements"])
		self.assertEqual([p for p, _, _ in STATE["imported"]], [OPS_HUB])
		self.assertEqual(len(STATE["logged"]), 1)
		self.assertEqual(STATE["cleared"], 1)
		# import_doc left in_import set when the insert raised; the patch puts it back.
		self.assertFalse(frappe_stub.flags.in_import)

	def test_the_patch_never_raises_even_when_the_rollback_fails(self):
		_reset()
		STATE["fail_import"].update({"inventory_enhancements", "operations_hub"})
		STATE["fail_rollback"] = True
		patch.execute()
		self.assertEqual(len(STATE["rollbacks"]), 2)
		self.assertEqual(len(STATE["logged"]), 4)
		self.assertFalse(frappe_stub.flags.in_import)

	def test_ci_runs_this_suite(self):
		self.assertIn(
			"python -m unittest erpnext_enhancements.tests.test_warehouse_transactions -v",
			CI.read_text(encoding="utf-8"),
		)


if __name__ == "__main__":
	unittest.main()
