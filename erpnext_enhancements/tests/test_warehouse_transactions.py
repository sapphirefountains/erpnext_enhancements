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

* **v1.831.0.** ``stock_movement_rules`` (each kind, what counts as use, the room trail, who keeps
  stock, the totals by person and by item, a move counted once from either side); the Project
  filter's two halves (rows carrying the job OR rows at its site, the other filters still narrowing
  both, a job with no site, no site field yet); a room covering its shelves with the Room / Rack
  column; the Kind column and filter from a permission-checked Stock Entry read; and both views,
  with everyone who keeps stock listed at zero and the service account left out.

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

# Pure (no Frappe): the kinds and totals the report's new views are built from.
from erpnext_enhancements.inventory_enhancements import stock_movement_rules as rules

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


def _reset(
	ledger=(),
	items=None,
	users=None,
	descendants=None,
	warehouses=None,
	stock_entries=None,
	has_role=None,
	user_rows=None,
	uoms=None,
	site_column=True,
):
	STATE.clear()
	STATE.update(
		ledger=[dict(row) for row in ledger],
		items=items or {},
		users=users or {},
		descendants=descendants or {},
		# Warehouse rows: {name, warehouse_name, parent_warehouse, custom_job_site_project}.
		warehouses=[dict(row) for row in (warehouses or [])],
		# {Stock Entry name: purpose}: the Stock Entries the reader may see.
		stock_entries=stock_entries or {},
		# [(user, role)] rows of Has Role.
		has_role=list(has_role or []),
		# {user: {full_name, enabled, user_type}} for the By person view's list of people.
		user_rows=user_rows or {},
		uoms=uoms or {},
		# True, False, or "raise" (frappe.db.has_column raises on a missing table).
		site_column=site_column,
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


def _cond(value, op, arg):
	if op == "in":
		return value in arg
	if op == "between":
		return arg[0] <= value <= arg[1]
	if op == "<=":
		return value <= arg
	if op == "=":
		return value == arg
	raise AssertionError(f"stub does not know {op}")


def _matches(row, filters):
	for field, cond in (filters or {}).items():
		value = row.get(field)
		if isinstance(cond, list):
			op, arg = cond
			ok = _cond(value, op, arg)
		else:
			ok = value == cond
		if not ok:
			return False
	return True


def _matches_any(row, or_filters):
	"""frappe.get_list's or_filters: [[field, op, value], ...], any one holding."""
	if not or_filters:
		return True
	return any(_cond(row.get(field), op, arg) for field, op, arg in or_filters)


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
		STATE["get_list"].append(
			dict(doctype=doctype, filters=filters, fields=fields, order_by=order_by, **kwargs)
		)
		if doctype == "Stock Entry":
			# Only the Stock Entries the reader may see: a missing one is a permission miss.
			assert fields == ["name", "purpose"], fields
			names = filters["name"][1]
			known = STATE["stock_entries"]
			return [_Dict(name=n, purpose=known[n]) for n in names if n in known]
		assert doctype == "Stock Ledger Entry", doctype
		rows = [
			r for r in STATE["ledger"] if _matches(r, filters) and _matches_any(r, kwargs.get("or_filters"))
		]
		rows = _ordered(rows, order_by)
		# Only the fields asked for, so code that relies on an unrequested field reads None here.
		return [_Dict({f: r.get(f) for f in fields}) for r in rows]

	def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
		STATE["get_all"].append(dict(doctype=doctype, filters=filters, fields=fields, pluck=pluck))
		if doctype == "Warehouse":
			rows = [r for r in STATE["warehouses"] if _matches(r, filters)]
			if pluck:
				return [r.get(pluck) for r in rows]
			return [_Dict({f: r.get(f) for f in fields}) for r in rows]
		if doctype == "Has Role":
			assert filters["parenttype"] == "User" and pluck == "parent", (filters, pluck)
			roles = filters["role"][1]
			return [user for user, role in STATE["has_role"] if role in roles]
		names = filters["name"][1]
		if doctype == "User" and len(fields) > 2:
			rows = STATE["user_rows"]
			return [_Dict(name=n, **{f: rows[n].get(f) for f in fields[1:]}) for n in names if n in rows]
		field = fields[1]
		source = {
			("Item", "item_name"): STATE["items"],
			("Item", "stock_uom"): STATE["uoms"],
			("User", "full_name"): STATE["users"],
		}[(doctype, field)]
		return [_Dict({"name": n, field: source[n]}) for n in names if n in source]

	def has_column(doctype, column):
		assert (doctype, column) == ("Warehouse", "custom_job_site_project"), (doctype, column)
		if STATE["site_column"] == "raise":
			raise RuntimeError("Table 'tabWarehouse' doesn't exist")
		return bool(STATE["site_column"])

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
	frappe.db = types.SimpleNamespace(
		get_descendants=get_descendants, savepoint=savepoint, rollback=rollback, has_column=has_column
	)
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
	project=None,
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
		"project": project,
	}


def count(item="ITEM-A", after=0.0, **kw):
	return sle(item=item, qty=0, after=after, voucher_type=SR, **kw)


def _ledger_reads():
	"""The Stock Ledger Entry reads, without the Stock Entry purpose read beside them."""
	return [c for c in STATE["get_list"] if c["doctype"] == "Stock Ledger Entry"]


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
				# get_all skips permissions, so it may only fetch display names and structure: item
				# names and units, people's names and who holds a stock role, and the warehouse tree
				# (names, parents, which job a site is). Directly with a literal doctype, or through
				# _lookup, whose callers name Item and User. Never a ledger or a voucher.
				first = node.args[0] if node.args else None
				if called == "frappe.get_all" and isinstance(first, ast.Constant):
					self.assertIn(first.value, {"Item", "User", "Warehouse", "Has Role"})
				if called == "_lookup":
					self.assertIn(ast.literal_eval(first), {"Item", "User"})
				if called == "frappe.get_list":
					get_lists += 1
					self.assertIn(ast.literal_eval(first), {"Stock Ledger Entry", "Stock Entry"})
		# The report's rows, the balances before a count, and the Stock Entries' purposes.
		self.assertEqual(get_lists, 3)

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
		# One lookup per doctype, each name once; then the warehouse tree for the Room / Rack column.
		self.assertEqual([c["doctype"] for c in STATE["get_all"]], ["Item", "User", "Warehouse"])
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
		self.assertEqual(len(_ledger_reads()), 1)

	def test_the_balance_read_is_permission_checked_and_in_ledger_order(self):
		_reset(ledger=[sle("A", 5, after=5, time="08:00"), count("A", after=3, time="10:00")])
		report.execute({"item_code": "A"})
		self.assertEqual(len(_ledger_reads()), 2)
		call = _ledger_reads()[1]
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
		self.assertEqual(
			fieldnames, ["from_date", "to_date", "view", "item_code", "warehouse", "project", "user", "kind"]
		)
		# Only the two dates are required; one reqd per date block.
		self.assertEqual(text.count("reqd: 1"), 2)
		self.assertIn(f"add_days(frappe.datetime.get_today(), -{report.DEFAULT_DAYS})", text)
		for doctype in ("Item", "Warehouse", "User", "Project"):
			self.assertIn(f'options: "{doctype}"', text)
		# The two Select filters offer exactly what the Python accepts, in its order.
		view_block = text[text.index('fieldname: "view"') : text.index('fieldname: "item_code"')]
		views = re.findall(r'"([^"]+)"', view_block.split("options:")[1].split("]")[0])
		self.assertEqual(views, list(rules.VIEWS))
		self.assertIn('default: "Movements"', view_block)
		kind_block = text[text.index('fieldname: "kind"') :]
		kinds = re.findall(r'"([^"]*)"', kind_block.split("options:")[1].split("]")[0])
		self.assertEqual(kinds, ["", *rules.KINDS])


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


# --- v1.831.0: a job, a room or rack, kinds, and totals by person and by item ---------------------

SITE = "Job Site PRJ-90001 Water Wall - X"
SHELF = "Project Shelf - X"

#: A small tree: a room with a rack of two shelves, the shop's group, the job sites' group.
TREE = [
	{"name": "All Warehouses - X", "warehouse_name": "All Warehouses", "parent_warehouse": None},
	{"name": "Atrium - X", "warehouse_name": "Atrium", "parent_warehouse": "All Warehouses - X"},
	{"name": "North Rack 1 - X", "warehouse_name": "North Rack 1", "parent_warehouse": "Atrium - X"},
	{"name": "Shelf NR1-1 - X", "warehouse_name": "Shelf NR1-1", "parent_warehouse": "North Rack 1 - X"},
	{"name": "Shelf NR1-2 - X", "warehouse_name": "Shelf NR1-2", "parent_warehouse": "North Rack 1 - X"},
	{"name": "Bin A1 - X", "warehouse_name": "Bin A1", "parent_warehouse": "All Warehouses - X"},
	{"name": SHELF, "warehouse_name": "Project Shelf", "parent_warehouse": "All Warehouses - X"},
	{"name": "Job Sites - X", "warehouse_name": "Job Sites", "parent_warehouse": "All Warehouses - X"},
	{
		"name": SITE,
		"warehouse_name": "Job Site PRJ-90001 Water Wall",
		"parent_warehouse": "Job Sites - X",
		"custom_job_site_project": "PRJ-90001",
	},
]


class TestMovementKinds(unittest.TestCase):
	"""stock_movement_rules.movement_kind: what each ledger line was."""

	def test_the_table(self):
		cases = [
			(("Stock Reconciliation", None, -3), "Count"),
			(("Stock Reconciliation", None, 4), "Count"),
			(("Stock Entry", "Material Issue", -1), "Take"),
			(("Stock Entry", "Material Receipt", 2), "Add"),
			(("Stock Entry", "Material Transfer", -2), "Move"),
			(("Stock Entry", "Material Transfer", 2), "Move"),
			(("Stock Entry", "Material Transfer for Manufacture", 2), "Move"),
			(("Stock Entry", "Repack", 2), "Other"),
			(("Stock Entry", "Receive from Customer", 2), "Other"),
			# A Stock Entry the reader may not see: no purpose, so Other rather than a guess.
			(("Stock Entry", None, -1), "Other"),
			(("Purchase Receipt", None, 5), "Receive"),
			(("Purchase Invoice", None, 5), "Receive"),
			(("Purchase Receipt", None, -5), "Return to supplier"),
			(("Delivery Note", None, -1), "Delivered"),
			(("Sales Invoice", None, -1), "Delivered"),
			(("Delivery Note", None, 1), "Other"),
			(("Landed Cost Voucher", None, 0), "Other"),
			((None, None, None), "Other"),
		]
		for args, kind in cases:
			with self.subTest(args=args):
				self.assertEqual(rules.movement_kind(*args), kind)
				self.assertIn(kind, rules.KINDS)

	def test_use_is_takes_and_deliveries_only(self):
		# A count correction says the shelf was wrong, not that anything was used.
		self.assertEqual(rules.USE_KINDS, {"Take", "Delivered"})
		self.assertNotIn("Count", rules.USE_KINDS)
		self.assertNotIn("Move", rules.USE_KINDS)

	def test_clean_view_and_kind(self):
		self.assertEqual(rules.clean_view("By person"), "By person")
		self.assertEqual(rules.clean_view("By item"), "By item")
		for odd in (None, "", "by person", "Everything"):
			self.assertEqual(rules.clean_view(odd), "Movements")
		self.assertEqual(rules.clean_kind("Take"), "Take")
		self.assertIsNone(rules.clean_kind(""))
		self.assertIsNone(rules.clean_kind("take"))

	def test_group_trail(self):
		parents = {r["name"]: r["parent_warehouse"] for r in TREE}
		labels = {r["name"]: r["warehouse_name"] for r in TREE}
		self.assertEqual(rules.group_trail("Shelf NR1-1 - X", parents, labels), "Atrium › North Rack 1")
		self.assertEqual(rules.group_trail("North Rack 1 - X", parents, labels), "Atrium")
		# Directly under the root: nothing worth naming.
		self.assertEqual(rules.group_trail("Bin A1 - X", parents, labels), "")
		self.assertEqual(rules.group_trail("All Warehouses - X", parents, labels), "")
		# Unknown, and a loop in the tree, end the walk.
		self.assertEqual(rules.group_trail("Nowhere", parents, labels), "")
		self.assertEqual(rules.group_trail("A", {"A": "B", "B": "A"}), "B")
		# Without labels, the docname.
		self.assertEqual(rules.group_trail("Shelf NR1-1 - X", parents), "Atrium - X › North Rack 1 - X")

	def test_stock_people(self):
		users = [
			{"name": "a@example.com", "full_name": "Alex Able", "enabled": 1, "user_type": "System User"},
			{"name": "b@example.com", "full_name": "Bo Baker", "enabled": 0, "user_type": "System User"},
			{"name": "c@example.com", "full_name": "Cy Cole", "enabled": 1, "user_type": "Website User"},
			{"name": "Administrator", "full_name": "Administrator", "enabled": 1, "user_type": "System User"},
			{"name": "bot@example.com", "full_name": "Bot", "enabled": 1, "user_type": "System User"},
			{"name": "d@example.com", "full_name": "", "enabled": 1, "user_type": "System User"},
			{"name": "e@example.com", "full_name": "Ed Eel", "enabled": 1, "user_type": "System User"},
		]
		holders = [
			"a@example.com",
			"b@example.com",
			"c@example.com",
			"Administrator",
			"bot@example.com",
			"d@example.com",
		]
		got = rules.stock_people(users, holders, {"administrator", "bot@example.com"})
		# Disabled, a website user, Frappe's own and a service account are left out; e holds no role.
		self.assertEqual(got, {"a@example.com": "Alex Able", "d@example.com": "d@example.com"})


def _people_rows():
	"""Movement rows as the report shapes them, for the totals."""
	day = TODAY
	return [
		# Alex: two takes on one Stock Entry (two items), a move of one item (both legs), a count.
		{
			"user": "a@example.com",
			"full_name": "Alex Able",
			"voucher_no": "SE-1",
			"item_code": "A",
			"qty_change": -2,
			"kind": "Take",
			"posting_date": day,
		},
		{
			"user": "a@example.com",
			"full_name": "Alex Able",
			"voucher_no": "SE-1",
			"item_code": "B",
			"qty_change": -1,
			"kind": "Take",
			"posting_date": day,
		},
		{
			"user": "a@example.com",
			"full_name": "Alex Able",
			"voucher_no": "SE-2",
			"item_code": "A",
			"qty_change": -3,
			"kind": "Move",
			"posting_date": day - dt.timedelta(days=2),
		},
		{
			"user": "a@example.com",
			"full_name": "Alex Able",
			"voucher_no": "SE-2",
			"item_code": "A",
			"qty_change": 3,
			"kind": "Move",
			"posting_date": day - dt.timedelta(days=2),
		},
		{
			"user": "a@example.com",
			"full_name": "Alex Able",
			"voucher_no": "SR-1",
			"item_code": "B",
			"qty_change": -4,
			"kind": "Count",
			"posting_date": day - dt.timedelta(days=5),
		},
		# Bo: one receipt.
		{
			"user": "b@example.com",
			"full_name": "Bo Baker",
			"voucher_no": "PR-1",
			"item_code": "A",
			"qty_change": 10,
			"kind": "Receive",
			"posting_date": day - dt.timedelta(days=1),
		},
	]


class TestTotalsByPerson(unittest.TestCase):
	def test_counts_per_person(self):
		people = rules.by_person(_people_rows(), {"c@example.com": "Cy Cole", "a@example.com": "Alex Able"})
		self.assertEqual([p["user"] for p in people], ["a@example.com", "b@example.com", "c@example.com"])
		alex, bo, cy = people
		self.assertEqual(
			{
				k: alex[k]
				for k in ("saves", "lines", "takes", "receipts", "moves", "counts", "others", "items")
			},
			{
				"saves": 3,
				"lines": 5,
				"takes": 2,
				"receipts": 0,
				"moves": 1,
				"counts": 1,
				"others": 0,
				"items": 2,
			},
		)
		self.assertEqual(alex["days_active"], 3)
		self.assertEqual(alex["last_movement"], TODAY)
		self.assertEqual((bo["saves"], bo["receipts"], bo["full_name"]), (1, 1, "Bo Baker"))
		# Someone who keeps stock and made nothing is a row of zeros, not a missing row.
		self.assertEqual(
			{k: cy[k] for k in ("full_name", "saves", "lines", "takes", "last_movement")},
			{"full_name": "Cy Cole", "saves": 0, "lines": 0, "takes": 0, "last_movement": None},
		)

	def test_a_move_seen_from_one_side_is_still_one_move(self):
		rows = [r for r in _people_rows() if not (r["kind"] == "Move" and r["qty_change"] > 0)]
		alex = rules.by_person(rows)[0]
		self.assertEqual(alex["moves"], 1)

	def test_people_with_none_sort_last_by_name(self):
		people = rules.by_person([], {"z@example.com": "Zed", "y@example.com": "Amy", "x@example.com": ""})
		self.assertEqual([p["full_name"] for p in people], ["Amy", "x@example.com", "Zed"])


class TestTotalsByItem(unittest.TestCase):
	def test_quantities_per_item(self):
		items = rules.by_item(_people_rows(), {"A": "Nos", "B": "Ft"})
		self.assertEqual([i["item_code"] for i in items], ["A", "B"])
		a, b = items
		self.assertEqual(
			{
				k: a[k]
				for k in (
					"stock_uom",
					"used",
					"uses",
					"received",
					"moved",
					"counted",
					"net_change",
					"people",
					"lines",
				)
			},
			{
				"stock_uom": "Nos",
				"used": 2.0,
				"uses": 1,
				"received": 10.0,
				"moved": 3.0,
				"counted": 0.0,
				"net_change": 8.0,
				"people": 2,
				"lines": 4,
			},
		)
		# A count correction is never use: B was used once (1) and counted down by 4.
		self.assertEqual((b["used"], b["uses"], b["counted"], b["net_change"]), (1.0, 1, -4.0, -5.0))

	def test_a_move_with_only_its_source_leg_shown(self):
		rows = [r for r in _people_rows() if not (r["kind"] == "Move" and r["qty_change"] > 0)]
		a = next(i for i in rules.by_item(rows) if i["item_code"] == "A")
		self.assertEqual(a["moved"], 3.0)
		self.assertEqual(a["net_change"], 5.0)

	def test_returns(self):
		rows = [
			{
				"item_code": "A",
				"voucher_no": "PR-2",
				"qty_change": -2,
				"kind": "Return to supplier",
				"user": "a",
			}
		]
		a = rules.by_item(rows)[0]
		self.assertEqual((a["returned"], a["used"], a["net_change"]), (2.0, 0.0, -2.0))


class TestKindsInTheReport(unittest.TestCase):
	def ledger(self):
		return [
			sle("A", -1, after=4, voucher_type="Stock Entry", time="08:00", name="SLE-T"),
			sle("A", 2, after=6, voucher_type="Stock Entry", time="08:30", name="SLE-H"),
			sle("A", 5, after=11, voucher_type="Purchase Receipt", time="09:00"),
			count("A", after=10, time="10:00"),
		]

	def test_each_row_says_what_it_was(self):
		ledger = self.ledger()
		vouchers = {r["name"]: r["voucher_no"] for r in ledger}
		# The second Stock Entry is one the reader may not see.
		_reset(ledger=ledger, stock_entries={vouchers["SLE-T"]: "Material Issue"})
		rows = report.execute({})[1]
		self.assertEqual([r["kind"] for r in rows], ["Count", "Receive", "Other", "Take"])
		purpose_reads = [c for c in STATE["get_list"] if c["doctype"] == "Stock Entry"]
		self.assertEqual(len(purpose_reads), 1)
		self.assertEqual(
			sorted(purpose_reads[0]["filters"]["name"][1]), sorted([vouchers["SLE-T"], vouchers["SLE-H"]])
		)

	def test_the_kind_filter_keeps_one(self):
		ledger = self.ledger()
		_reset(ledger=ledger, stock_entries={ledger[0]["voucher_no"]: "Material Issue"})
		rows = report.execute({"kind": "Take"})[1]
		self.assertEqual([r["qty_change"] for r in rows], [-1])
		# The count still works out its change from the whole ledger.
		counted = report.execute({"kind": "Count"})[1]
		self.assertEqual([r["qty_change"] for r in counted], [-1])
		# An unknown kind is no filter.
		self.assertEqual(len(report.execute({"kind": "Juggling"})[1]), 4)

	def test_no_stock_entries_no_purpose_read(self):
		_reset(ledger=[sle("A", 5, voucher_type="Purchase Receipt")])
		report.execute({})
		self.assertEqual([c["doctype"] for c in STATE["get_list"]], ["Stock Ledger Entry"])


class TestRoomOrRack(unittest.TestCase):
	def test_a_room_includes_every_shelf_under_it_and_says_where(self):
		_reset(
			ledger=[
				sle("A", -1, warehouse="Shelf NR1-1 - X", time="08:00"),
				sle("B", -2, warehouse="Shelf NR1-2 - X", time="09:00"),
				sle("C", -3, warehouse="Bin A1 - X", time="10:00"),
			],
			descendants={"Atrium - X": ["North Rack 1 - X", "Shelf NR1-1 - X", "Shelf NR1-2 - X"]},
			warehouses=TREE,
		)
		rows = report.execute({"warehouse": "Atrium - X"})[1]
		self.assertEqual([r["item_code"] for r in rows], ["B", "A"])
		self.assertEqual({r["where"] for r in rows}, {"Atrium › North Rack 1"})
		everything = report.execute({})[1]
		self.assertEqual({r["item_code"]: r["where"] for r in everything}["C"], "")

	def test_the_location_filter_is_labeled_for_rooms_and_racks(self):
		text = REPORT_JS.read_text(encoding="utf-8")
		block = text[text.index('fieldname: "warehouse"') : text.index('fieldname: "project"')]
		self.assertIn("room, rack or bin", block)


class TestProjectFilter(unittest.TestCase):
	def ledger(self):
		return [
			# Tagged with the job: a take at a shop bin, a receipt onto the Project Shelf.
			sle("A", -1, warehouse="Bin A1 - X", project="PRJ-90001", time="08:00"),
			sle("B", 4, warehouse=SHELF, project="PRJ-90001", voucher_type="Purchase Receipt", time="08:10"),
			# At the job's site with no tag: a count there is still the job's movement.
			count("C", after=2, warehouse=SITE, time="08:20"),
			# Another job, and untagged shop stock: never the job's.
			sle("D", -1, warehouse="Bin A1 - X", project="PRJ-80000", time="08:30"),
			sle("E", -1, warehouse="Bin A1 - X", time="08:40"),
			count("F", after=1, warehouse=SHELF, time="08:50"),
		]

	def test_rows_carrying_the_job_and_everything_at_its_site(self):
		_reset(ledger=self.ledger(), warehouses=TREE)
		rows = report.execute({"project": "PRJ-90001"})[1]
		self.assertEqual(sorted(r["item_code"] for r in rows), ["A", "B", "C"])
		call = STATE["get_list"][0]
		self.assertNotIn("project", call["filters"])
		self.assertEqual(call["or_filters"], [["project", "=", "PRJ-90001"], ["warehouse", "in", [SITE]]])
		self.assertEqual({r["project"] for r in rows if r["item_code"] != "C"}, {"PRJ-90001"})

	def test_the_other_filters_still_narrow_both_halves(self):
		_reset(ledger=self.ledger(), warehouses=TREE)
		rows = report.execute({"project": "PRJ-90001", "warehouse": SHELF})[1]
		self.assertEqual([r["item_code"] for r in rows], ["B"])
		rows = report.execute({"project": "PRJ-90001", "item_code": "C"})[1]
		self.assertEqual([r["item_code"] for r in rows], ["C"])

	def test_a_job_without_a_site_is_a_plain_condition(self):
		_reset(ledger=self.ledger(), warehouses=TREE)
		rows = report.execute({"project": "PRJ-80000"})[1]
		self.assertEqual([r["item_code"] for r in rows], ["D"])
		call = STATE["get_list"][0]
		self.assertEqual(call["filters"]["project"], "PRJ-80000")
		self.assertNotIn("or_filters", call)

	def test_before_the_site_field_exists(self):
		for state in (False, "raise"):
			with self.subTest(site_column=state):
				_reset(ledger=self.ledger(), warehouses=TREE, site_column=state)
				rows = report.execute({"project": "PRJ-90001"})[1]
				self.assertEqual(sorted(r["item_code"] for r in rows), ["A", "B"])
				self.assertFalse(any(c["doctype"] == "Warehouse" and c["pluck"] for c in STATE["get_all"]))

	def test_no_project_reads_no_sites(self):
		_reset(ledger=self.ledger(), warehouses=TREE)
		report.execute({})
		self.assertFalse(any(c.get("pluck") for c in STATE["get_all"]))
		self.assertNotIn("or_filters", STATE["get_list"][0])


STAFF = {
	"a@example.com": {"full_name": "Alex Able", "enabled": 1, "user_type": "System User"},
	"b@example.com": {"full_name": "Bo Baker", "enabled": 1, "user_type": "System User"},
	"c@example.com": {"full_name": "Cy Cole", "enabled": 1, "user_type": "System User"},
	"gone@example.com": {"full_name": "Gone Away", "enabled": 0, "user_type": "System User"},
	"triton@sapphirefountains.com": {"full_name": "Service", "enabled": 1, "user_type": "System User"},
	"Administrator": {"full_name": "Administrator", "enabled": 1, "user_type": "System User"},
}
ROLES = [
	("a@example.com", "Stock User"),
	("b@example.com", "Stock Manager"),
	("c@example.com", "Inventory Clerk"),
	("c@example.com", "Stock User"),
	("gone@example.com", "Stock User"),
	("triton@sapphirefountains.com", "Stock Manager"),
	("Administrator", "Stock Manager"),
	("d@example.com", "Sales User"),
]


class TestByPersonView(unittest.TestCase):
	def setUp(self):
		_reset(
			ledger=[
				sle("A", -1, owner="a@example.com", time="08:00", voucher_type="Stock Entry"),
				sle("B", 2, owner="a@example.com", time="09:00", voucher_type="Purchase Receipt"),
				sle("A", -1, owner="outsider@example.com", time="10:00", voucher_type="Stock Entry"),
			],
			users={
				"a@example.com": "Alex Able",
				"outsider@example.com": "Out Sider",
				"b@example.com": "Bo Baker",
			},
			has_role=ROLES,
			user_rows=STAFF,
		)
		STATE["stock_entries"] = {
			r["voucher_no"]: "Material Issue" for r in STATE["ledger"] if r["voucher_type"] == "Stock Entry"
		}

	def test_everyone_who_keeps_stock_is_listed_with_zeros(self):
		columns, people, message, chart, summary = report.execute({"view": "By person"})
		names = [p["full_name"] for p in people]
		# Movers first (most saves); then the people who keep stock and made none, by name. The
		# disabled account, the service account and Administrator are not listed.
		self.assertEqual(names, ["Alex Able", "Out Sider", "Bo Baker", "Cy Cole"])
		alex = people[0]
		self.assertEqual((alex["saves"], alex["takes"], alex["receipts"]), (2, 1, 1))
		self.assertEqual(people[2]["saves"], 0)
		self.assertEqual({c["fieldname"] for c in columns}, set(people[0]))
		cards = {c["label"]: c["value"] for c in summary}
		self.assertEqual(cards["People with movements"], 2)
		self.assertEqual(cards["People with none"], 2)
		self.assertEqual(cards["Saves"], 3)
		self.assertEqual(cards["Ledger lines"], 3)
		user_column = next(c for c in columns if c["fieldname"] == "user")
		self.assertEqual(user_column["fieldtype"], "Data")

	def test_a_user_filter_lists_that_person_even_with_none(self):
		people = report.execute({"view": "By person", "user": "b@example.com"})[1]
		self.assertEqual([(p["full_name"], p["saves"]) for p in people], [("Bo Baker", 0)])
		self.assertFalse(any(c["doctype"] == "Has Role" for c in STATE["get_all"]))

	def test_other_filters_narrow_the_counts_but_keep_the_people(self):
		people = report.execute({"view": "By person", "kind": "Receive"})[1]
		self.assertEqual([(p["full_name"], p["saves"]) for p in people][:1], [("Alex Able", 1)])
		self.assertEqual(len(people), 3)

	def test_the_roles_asked_for(self):
		report.execute({"view": "By person"})
		call = next(c for c in STATE["get_all"] if c["doctype"] == "Has Role")
		self.assertEqual(set(call["filters"]["role"][1]), {"Stock User", "Stock Manager", "Inventory Clerk"})


class TestByItemView(unittest.TestCase):
	def test_one_row_per_item_in_its_unit(self):
		ledger = [
			sle("A", -2, after=8, voucher_type="Stock Entry", time="08:00"),
			sle("A", -1, after=7, voucher_type="Stock Entry", time="09:00"),
			sle("B", 6, voucher_type="Purchase Receipt", time="09:30"),
			count("B", after=5, time="10:00"),
		]
		_reset(
			ledger=ledger,
			items={"A": "Pump", "B": "Nozzle"},
			uoms={"A": "Nos", "B": "Ft"},
			stock_entries={
				r["voucher_no"]: "Material Issue" for r in ledger if r["voucher_type"] == "Stock Entry"
			},
		)
		columns, items, message, chart, summary = report.execute({"view": "By item"})
		self.assertEqual([i["item_code"] for i in items], ["A", "B"])
		a, b = items
		self.assertEqual((a["item_name"], a["stock_uom"], a["used"], a["uses"]), ("Pump", "Nos", 3.0, 2))
		self.assertEqual((b["received"], b["counted"], b["used"]), (6.0, -1.0, 0.0))
		self.assertEqual({c["fieldname"] for c in columns}, set(a))
		cards = {c["label"]: c["value"] for c in summary}
		self.assertEqual((cards["Items moved"], cards["Items used"], cards["Ledger lines"]), (2, 1, 4))
		self.assertEqual(next(c for c in columns if c["fieldname"] == "item_code")["options"], "Item")

	def test_the_movements_view_is_the_default(self):
		_reset(ledger=[sle("A", -1)])
		for view in (None, "", "Movements", "nonsense"):
			with self.subTest(view=view):
				columns = report.execute({"view": view})[0]
				self.assertEqual(columns, report.get_columns())


if __name__ == "__main__":
	unittest.main()
