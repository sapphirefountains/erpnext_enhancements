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
* **Permissions.** Rows come through ``frappe.get_list`` (permission-checked), never ``get_all``.
* **The rows.** Every field read exists on Stock Ledger Entry at ERPNext v16.52.1 (production);
  item names and full names are looked up once each, and a missing one reads blank.
* **The summary.** Additions are positive quantities, removals negative, a zero is neither.
* **The hubs.** The Inventory Enhancements workspace has the shortcut and the Reports-card link,
  the Operations Hub has the Inventory-card link, card counts agree, both files carry the new
  stamp, and the force-import patch is registered after model sync and never raises.

Installs a minimal frappe stub in ``setUpModule`` and removes it afterwards.

Run: python -m unittest erpnext_enhancements.tests.test_warehouse_transactions -v
"""

import ast
import datetime as dt
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

#: What the stubs record and return; reset before each test.
STATE = {}


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)


class _Thrown(Exception):
	pass


def _reset(entries=(), items=None, users=None, descendants=None):
	STATE.clear()
	STATE.update(
		entries=[_Dict(e) for e in entries],
		items=items or {},
		users=users or {},
		descendants=descendants or {},
		get_list=[],
		get_all=[],
		logged=[],
		imported=[],
		fail_import=set(),
		cleared=0,
	)


def setUpModule():
	global report, patch
	for name in STUBBED + OURS:
		_saved[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	frappe._ = lambda text: text
	frappe._dict = _Dict

	def throw(message, *args, **kwargs):
		raise _Thrown(message)

	def get_list(doctype, filters=None, fields=None, order_by=None, **kwargs):
		STATE["get_list"].append(
			dict(doctype=doctype, filters=filters, fields=fields, order_by=order_by, **kwargs)
		)
		return list(STATE["entries"])

	def get_all(doctype, filters=None, fields=None, **kwargs):
		STATE["get_all"].append(dict(doctype=doctype, filters=filters, fields=fields))
		source = {"Item": STATE["items"], "User": STATE["users"]}[doctype]
		field = fields[1]
		names = filters["name"][1]
		return [_Dict({"name": n, field: source[n]}) for n in names if n in source]

	def get_descendants(doctype, name):
		assert doctype == "Warehouse"
		return STATE["descendants"].get(name, [])

	frappe.throw = throw
	frappe.get_list = get_list
	frappe.get_all = get_all
	frappe.db = types.SimpleNamespace(get_descendants=get_descendants)
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
		if isinstance(value, dt.date):
			return value
		return dt.date.fromisoformat(str(value)[:10])

	utils.getdate = getdate
	utils.nowdate = lambda: TODAY.isoformat()
	utils.add_days = lambda date, days: getdate(date) + dt.timedelta(days=days)
	frappe.utils = utils

	modules = types.ModuleType("frappe.modules")
	import_file = types.ModuleType("frappe.modules.import_file")

	def import_file_by_path(path, force=False, reset_permissions=False, **kwargs):
		if Path(path).stem in STATE["fail_import"]:
			raise RuntimeError("import failed")
		STATE["imported"].append((Path(path), force, reset_permissions))

	import_file.import_file_by_path = import_file_by_path
	modules.import_file = import_file
	frappe.modules = modules

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.modules"] = modules
	sys.modules["frappe.modules.import_file"] = import_file

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


def entry(item="ITEM-A", qty=1.0, owner="a@example.com", warehouse="Bin A1 - X", **extra):
	row = {
		"name": f"SLE-{item}-{qty}",
		"posting_date": TODAY,
		"posting_time": dt.timedelta(hours=9),
		"item_code": item,
		"warehouse": warehouse,
		"actual_qty": qty,
		"voucher_type": "Stock Entry",
		"voucher_no": "STE-0001",
		"owner": owner,
	}
	row.update(extra)
	return row


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
		# Stock Ledger at ERPNext v16.52.1 and on production: Stock User, Accounts Manager. Those two
		# are also the roles with read + report on Stock Ledger Entry, which the run checks.
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

	def test_shapes_rows_with_names(self):
		_reset(
			entries=[
				entry("ITEM-A", 5, "a@example.com"),
				entry("ITEM-B", -2, "b@example.com"),
				entry("ITEM-A", -1, "a@example.com"),
				entry("GONE", 3, "gone@example.com"),
			],
			items={"ITEM-A": "Pump, 1 HP", "ITEM-B": "Nozzle, jet"},
			users={"a@example.com": "Alex Able", "b@example.com": "Bo Baker"},
		)
		columns, rows, message, chart, summary = report.execute({})
		self.assertIsNone(message)
		self.assertIsNone(chart)
		self.assertEqual([r["item_name"] for r in rows], ["Pump, 1 HP", "Nozzle, jet", "Pump, 1 HP", ""])
		self.assertEqual([r["full_name"] for r in rows], ["Alex Able", "Bo Baker", "Alex Able", ""])
		self.assertEqual(
			[r["user"] for r in rows], ["a@example.com", "b@example.com", "a@example.com", "gone@example.com"]
		)
		self.assertEqual([r["actual_qty"] for r in rows], [5, -2, -1, 3])
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

	def test_summary_counts_additions_removals_and_people(self):
		_reset(
			entries=[
				entry("A", 5, "a@example.com"),
				entry("B", -2, "b@example.com"),
				entry("C", 0, "a@example.com"),
				entry("D", None, "c@example.com"),
				entry("E", 0.5, "a@example.com"),
			]
		)
		summary = report.execute({})[4]
		values = {card["label"]: card["value"] for card in summary}
		self.assertEqual(values, {"Movements": 5, "Additions": 2, "Removals": 1, "People": 3})


class TestColumnsAndFilters(unittest.TestCase):
	def test_has_every_column_the_task_named(self):
		columns = {c["fieldname"]: c for c in report.get_columns()}
		for name in (
			"item_code",
			"actual_qty",
			"warehouse",
			"posting_date",
			"posting_time",
			"voucher_type",
			"voucher_no",
			"user",
		):
			with self.subTest(column=name):
				self.assertIn(name, columns)
		self.assertEqual(columns["item_code"]["options"], "Item")
		self.assertEqual(columns["warehouse"]["options"], "Warehouse")
		self.assertEqual(columns["user"]["options"], "User")
		self.assertEqual(columns["voucher_no"]["fieldtype"], "Dynamic Link")
		self.assertEqual(columns["voucher_no"]["options"], "voucher_type")
		self.assertEqual(len(columns), len(report.get_columns()), "fieldnames are unique")

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
		self.assertEqual(STATE["logged"], [])
		self.assertEqual(STATE["cleared"], 1)

	def test_the_patch_never_raises(self):
		_reset()
		STATE["fail_import"].add("inventory_enhancements")
		patch.execute()
		self.assertEqual([p for p, _, _ in STATE["imported"]], [OPS_HUB])
		self.assertEqual(len(STATE["logged"]), 1)
		self.assertEqual(STATE["cleared"], 1)

	def test_ci_runs_this_suite(self):
		self.assertIn(
			"python -m unittest erpnext_enhancements.tests.test_warehouse_transactions -v",
			CI.read_text(encoding="utf-8"),
		)


if __name__ == "__main__":
	unittest.main()
