"""Bench-free tests for the four inventory reports and their hub links (v1.805.0).

Location Contents, Where Is This Item, Project Materials and Job Site Contents. What is held:

* **The report records.** Four Script Reports in Inventory Enhancements, each on a reference
  doctype its roles can report on, with no required filter (a Query Report cannot have an optional
  one on Frappe 16.53 and later, which is why none of them is one). Each script's filters are the
  ones its Python reads, and its Select options are the rules module's own words.
* **The status rules** (``inventory_report_rules.location_status``): as expected, assigned here
  with none on hand, here and not assigned, here but assigned to another bin (named, and never the
  Project Shelf, a job site, the Boneyard or a group), on order, empty and undecided, and "held
  here" at a place that is never a bin.
* **Location Contents calls the Bin Manager's listing rule** (``api.stock_scan._items_at``) once
  per location that could list anything, with no row cap, and carries no copy of it.
* **The shelf and site split** (``place_kind``, ``shelf_shares``): a job site wins, then the
  Project Shelf, the Boneyard, a vehicle; a job's shelf share is its ledger net, and the no-job
  share makes the rows add up to what is on the shelf, going negative when a count left less.
* **The project roll-up** (``project_item_rows``): ordered from order lines, received from
  receipt lines (a store run has no order), returns, still to come against closed-short, used
  less brought back, and "not located" for what the stock ledger cannot place, with a line that
  is not a stock item reported as ordered and received and nothing more.
* **How long stock has been at a job site** (``stay``), counts included.
* **The Project Shelf before it exists.** The setting is read with ``.get`` on the settings
  document and never raises; with no shelf every report still runs and says so. The by-project
  answer comes through one adapter, which asks the Project Shelf change's own module when that
  is installed and reads the same ledger rows itself until then, with the same arithmetic.
* **The queries.** Every stock ledger read skips canceled entries, every order and receipt read
  takes submitted documents only, values travel as bound parameters, and the only names
  interpolated into SQL are fixed clauses.
* **Print and export.** A row holds plain values under declared fieldnames (no markup), so the
  grid print and the CSV and Excel export, which read the raw cell, get what the screen shows.
* **The hubs.** Both workspace files link the reports with card counts that agree, and the
  force-import patch is registered after model sync, imports each workspace in its own savepoint,
  rolls a failed import back, restores ``frappe.flags.in_import`` and never raises.

The ``frappe`` stub is installed in ``setUpModule`` and removed afterwards. ``frappe.db.sql`` is
answered from a table of (text the query must contain, rows), and every call is recorded.

Run: python -m unittest erpnext_enhancements.tests.test_inventory_reports -v
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
INV = APP / "inventory_enhancements"
REPORTS = INV / "report"
INV_WS = INV / "workspace" / "inventory_enhancements" / "inventory_enhancements.json"
OPS_HUB = APP / "enhancements_core" / "workspace" / "operations_hub" / "operations_hub.json"
PATCHES_TXT = APP / "patches.txt"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

TODAY = dt.date(2026, 11, 12)
STAMP = "2026-11-12 10:00"

STOCK_ROLES = {"Stock User", "Stock Manager", "Inventory Clerk", "Item Manager", "System Manager"}
PROJECT_ROLES = STOCK_ROLES | {"Projects User", "Projects Manager", "Purchase User", "Purchase Manager"}

#: name -> (folder, reference doctype, roles)
REPORT_RECORDS = {
	"Location Contents": ("location_contents", "Warehouse", STOCK_ROLES),
	"Where Is This Item": ("where_is_this_item", "Warehouse", STOCK_ROLES),
	"Project Materials": ("project_materials", "Purchase Order", PROJECT_ROLES),
	"Job Site Contents": ("job_site_contents", "Warehouse", PROJECT_ROLES),
}

#: Roles that hold report permission on each reference doctype on production (tabCustom DocPerm,
#: read 2026-11-12). A report's roles outside this set are admitted by the report and then need
#: one of these as well; every Inventory Clerk and Projects user on production holds Stock User.
CAN_REPORT = {
	"Warehouse": {"Stock User", "Stock Manager", "Item Manager", "Purchase User"},
	"Purchase Order": {"Stock User", "Purchase User", "Purchase Manager"},
}

STUBBED = ("frappe", "frappe.utils", "frappe.modules", "frappe.modules.import_file")
PKG = "erpnext_enhancements.inventory_enhancements"
OURS = (
	f"{PKG}.inventory_report_rules",
	f"{PKG}.inventory_report_data",
	f"{PKG}.job_sites",
	f"{PKG}.report.location_contents.location_contents",
	f"{PKG}.report.where_is_this_item.where_is_this_item",
	f"{PKG}.report.project_materials.project_materials",
	f"{PKG}.report.job_site_contents.job_site_contents",
	"erpnext_enhancements.patches.inventory_reports_hub_links",
	"erpnext_enhancements.api.stock_scan",
)
_saved = {}
frappe_stub = None
rules = data = job_sites = None
location_contents = where_item = project_materials = job_site_contents = patch = None

#: What the stubs record and return; reset before each test.
STATE = {}


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class _Thrown(Exception):
	pass


def _reset():
	STATE.clear()
	STATE.update(
		sql=[],
		sql_calls=[],
		get_all={},
		get_all_calls=[],
		values={},
		settings={},
		settings_raises=False,
		columns=set(),
		descendants={},
		items_at={},
		items_at_calls=[],
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


def _answer_sql(contains, rows):
	"""Answer every later ``frappe.db.sql`` whose text contains ``contains`` with ``rows``."""
	STATE["sql"].append((contains, rows))


def setUpModule():
	global frappe_stub, rules, data, job_sites, location_contents, where_item
	global project_materials, job_site_contents, patch
	for name in STUBBED + OURS:
		_saved[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	frappe._ = lambda text: text
	frappe._dict = _Dict
	frappe.flags = _Dict(in_import=False)

	def throw(message, *args, **kwargs):
		raise _Thrown(message)

	def sql(query, params=None, as_dict=False, **kwargs):
		STATE["sql_calls"].append((query, params, as_dict))
		for contains, rows in STATE["sql"]:
			if contains in query:
				found = rows(query, params) if callable(rows) else rows
				return [_Dict(r) if isinstance(r, dict) else r for r in found]
		return []

	def get_all(doctype, filters=None, fields=None, **kwargs):
		STATE["get_all_calls"].append(dict(doctype=doctype, filters=filters, fields=fields))
		rows = STATE["get_all"].get(doctype, [])
		if isinstance(filters, dict):
			for field in ("name", "parent", "item_code"):
				cond = filters.get(field)
				if isinstance(cond, list) and cond[0] == "in":
					rows = [r for r in rows if r.get(field) in cond[1]]
			if "disable" in filters:
				rows = [r for r in rows if int(r.get("disable") or 0) == filters["disable"]]
		return [_Dict(r) for r in rows]

	def get_value(doctype, name, fields=None, as_dict=False, **kwargs):
		row = STATE["values"].get((doctype, name))
		if row is None:
			return None
		return _Dict(row) if as_dict else row.get(fields if isinstance(fields, str) else fields[0])

	def has_column(doctype, column):
		return (doctype, column) in STATE["columns"]

	def get_cached_doc(doctype, *args):
		if STATE["settings_raises"]:
			raise RuntimeError("no such doctype")
		return _Dict(STATE["settings"])

	def savepoint(name):
		STATE["savepoints"].append(name)

	def rollback(save_point=None):
		STATE["rollbacks"].append(save_point)
		if STATE["fail_rollback"]:
			raise RuntimeError("rollback failed")

	frappe.throw = throw
	frappe.get_all = get_all
	frappe.get_cached_doc = get_cached_doc
	frappe.db = types.SimpleNamespace(
		sql=sql,
		get_value=get_value,
		has_column=has_column,
		get_descendants=lambda doctype, name: STATE["descendants"].get((doctype, name), []),
		savepoint=savepoint,
		rollback=rollback,
	)
	frappe.get_app_path = lambda app, *parts: str(APP.joinpath(*parts))
	frappe.log_error = lambda title=None, message=None, **kw: STATE["logged"].append((title, message))
	frappe.get_traceback = lambda: "traceback"

	def clear_cache():
		STATE["cleared"] += 1

	frappe.clear_cache = clear_cache

	utils = types.ModuleType("frappe.utils")
	utils.nowdate = lambda: TODAY.isoformat()
	utils.escape_html = lambda text: (
		str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
	)
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

	# The Bin Manager's listing rule, standing in for the real module (the whole Stock Scan API).
	stock_scan = types.ModuleType("erpnext_enhancements.api.stock_scan")

	def _items_at(row, limit=150):
		STATE["items_at_calls"].append((row.name, row.company, limit))
		return [dict(item) for item in STATE["items_at"].get(row.name, [])], False

	stock_scan._items_at = _items_at

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.modules"] = modules
	sys.modules["frappe.modules.import_file"] = import_file
	sys.modules["erpnext_enhancements.api.stock_scan"] = stock_scan
	frappe_stub = frappe

	from erpnext_enhancements.inventory_enhancements import inventory_report_data as data_module
	from erpnext_enhancements.inventory_enhancements import inventory_report_rules as rules_module
	from erpnext_enhancements.inventory_enhancements import job_sites as job_sites_module
	from erpnext_enhancements.inventory_enhancements.report.job_site_contents import (
		job_site_contents as job_site_module,
	)
	from erpnext_enhancements.inventory_enhancements.report.location_contents import (
		location_contents as location_module,
	)
	from erpnext_enhancements.inventory_enhancements.report.project_materials import (
		project_materials as project_module,
	)
	from erpnext_enhancements.inventory_enhancements.report.where_is_this_item import (
		where_is_this_item as where_module,
	)
	from erpnext_enhancements.patches import inventory_reports_hub_links as patch_module

	rules, data, job_sites = rules_module, data_module, job_sites_module
	location_contents, where_item = location_module, where_module
	project_materials, job_site_contents, patch = project_module, job_site_module, patch_module


def tearDownModule():
	for name in STUBBED + OURS:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


# ---------------------------------------------------------------------------
# A small shop: two shelf bins under a shelf, the Project Shelf, the Boneyard, a van, two job sites.

WAREHOUSES = [
	{"name": "All - SF", "warehouse_name": "All", "parent_warehouse": None, "is_group": 1},
	{"name": "Inventory - SF", "warehouse_name": "Inventory", "parent_warehouse": "All - SF", "is_group": 1},
	{
		"name": "Shelf A - SF",
		"warehouse_name": "Shelf A",
		"parent_warehouse": "Inventory - SF",
		"is_group": 1,
	},
	{"name": "Bin A-1 - SF", "warehouse_name": "Bin A-1", "parent_warehouse": "Shelf A - SF"},
	{"name": "Bin A-2 - SF", "warehouse_name": "Bin A-2", "parent_warehouse": "Shelf A - SF"},
	{"name": "Bin A-10 - SF", "warehouse_name": "Bin A-10", "parent_warehouse": "Shelf A - SF"},
	{"name": "Boneyard - SF", "warehouse_name": "Boneyard", "parent_warehouse": "Inventory - SF"},
	{"name": "Project Shelf - SF", "warehouse_name": "Project Shelf", "parent_warehouse": "All - SF"},
	{"name": "Van - SF", "warehouse_name": "Van", "parent_warehouse": "All - SF"},
	{
		"name": "Old Bin - SF",
		"warehouse_name": "Old Bin",
		"parent_warehouse": "Inventory - SF",
		"disabled": 1,
	},
	{
		"name": "Transit - SF",
		"warehouse_name": "Transit",
		"parent_warehouse": "All - SF",
		"warehouse_type": "Transit",
	},
	{"name": "Job Sites - SF", "warehouse_name": "Job Sites", "parent_warehouse": "All - SF", "is_group": 1},
	{"name": "Site J1 - SF", "warehouse_name": "Site J1", "parent_warehouse": "Job Sites - SF"},
	{"name": "Site J2 - SF", "warehouse_name": "Site J2", "parent_warehouse": "Job Sites - SF"},
	{"name": "Site Old - SF", "warehouse_name": "Site Old", "parent_warehouse": "Job Sites - SF"},
]
for _row in WAREHOUSES:
	_row.setdefault("is_group", 0)
	_row.setdefault("disabled", 0)
	_row.setdefault("warehouse_type", None)
	_row.setdefault("company", "SF")

SITE_PROJECTS = {"Site J1 - SF": "JOB-1", "Site J2 - SF": "JOB-2"}
SITES = {"Site J1 - SF", "Site J2 - SF", "Site Old - SF"}

ITEMS = {
	"A": {
		"name": "A",
		"item_name": "Alpha",
		"item_group": "Fittings",
		"stock_uom": "Unit",
		"is_stock_item": 1,
	},
	"B": {
		"name": "B",
		"item_name": "Bravo",
		"item_group": "Fittings",
		"stock_uom": "Unit",
		"is_stock_item": 1,
	},
	"C": {
		"name": "C",
		"item_name": "Charlie",
		"item_group": "Pumps",
		"stock_uom": "Unit",
		"is_stock_item": 1,
	},
	"D": {"name": "D", "item_name": "Delta", "item_group": "Pumps", "stock_uom": "FT", "is_stock_item": 1},
	"N": {
		"name": "N",
		"item_name": "November",
		"item_group": "Pumps",
		"stock_uom": "Unit",
		"is_stock_item": 0,
	},
}

PROJECTS = {
	"JOB-1": {"name": "JOB-1", "project_name": "First Job", "customer": "Cust 1", "status": "Active"},
	"JOB-2": {"name": "JOB-2", "project_name": "Second Job", "customer": "Cust 2", "status": "Completed"},
}
for _row in PROJECTS.values():
	_row["project_type"] = "Build"


def _where(shelf="Project Shelf - SF", sites_ready=True):
	"""``inventory_report_data.places()``'s answer for the small shop."""
	return _Dict(
		warehouses=[_Dict(w) for w in WAREHOUSES],
		names={w["name"]: w["warehouse_name"] for w in WAREHOUSES},
		parents={w["name"]: w["parent_warehouse"] for w in WAREHOUSES},
		shelf=shelf,
		boneyard="Boneyard - SF",
		sites=set(SITES) if sites_ready else set(),
		site_projects=dict(SITE_PROJECTS) if sites_ready else {},
		vehicles={"Van - SF"},
		sites_ready=sites_ready,
	)


def _item(code, on_hand=0, on_order=0, assigned=False, is_default=False, undecided=False):
	"""One of ``_items_at``'s rows."""
	return {
		"item_code": code,
		"item_name": ITEMS[code]["item_name"],
		"image": None,
		"stock_uom": ITEMS[code]["stock_uom"],
		"on_hand": float(on_hand),
		"on_order": float(on_order),
		"assigned": assigned,
		"is_default": is_default,
		"undecided": undecided,
	}


class _Patched:
	"""Replace attributes on modules for one test, and put them back."""

	def __init__(self):
		self._undo = []

	def set(self, module, name, value):
		self._undo.append((module, name, getattr(module, name)))
		setattr(module, name, value)

	def restore(self):
		for module, name, value in reversed(self._undo):
			setattr(module, name, value)
		self._undo.clear()


class _Case(unittest.TestCase):
	def setUp(self):
		_reset()
		self.patched = _Patched()
		self.addCleanup(self.patched.restore)
		STATE["get_all"]["Item"] = list(ITEMS.values())
		STATE["get_all"]["Project"] = list(PROJECTS.values())
		self.patched.set(data, "_shelf_module", lambda: None)
		self.patched.set(data, "_returns_module", lambda: None)

	def places(self, **kwargs):
		where = _where(**kwargs)
		self.patched.set(data, "places", lambda: where)
		return where


def _export(columns, rows):
	"""What Frappe's CSV and Excel export writes: the labels, then each row's raw cell by
	fieldname (``frappe.desk.query_report.build_xlsx_data`` reads ``row.get(fieldname)``)."""
	header = [c["label"] for c in columns]
	return [header] + [[row.get(c["fieldname"]) for c in columns] for row in rows]


PLAIN = (str, int, float, dt.date, type(None))
FIELDTYPES = {"Data", "Link", "Dynamic Link", "Float", "Int", "Currency", "Date", "Check"}


def _strip_py(source):
	"""A module's code with its docstrings and comments gone, so a rule's own explanation does
	not trip the check that enforces it."""
	tree = ast.parse(source)
	for node in ast.walk(tree):
		if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node):
			node.body = node.body[1:] or [ast.Pass()]
	return ast.unparse(tree)


def _sql_strings(path):
	"""Every string in a module that is SQL: ``(text, the names an f-string interpolates)``."""
	out = []
	tree = ast.parse(path.read_text(encoding="utf-8"))
	# The constant pieces of an f-string are read as part of it, not again on their own.
	pieces = {
		id(value) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) for value in node.values
	}
	for node in ast.walk(tree):
		if id(node) in pieces:
			continue
		if isinstance(node, ast.JoinedStr):
			text = "".join(v.value for v in node.values if isinstance(v, ast.Constant))
			names = {ast.unparse(v.value) for v in node.values if isinstance(v, ast.FormattedValue)}
		elif isinstance(node, ast.Constant) and isinstance(node.value, str):
			text, names = node.value, set()
		else:
			continue
		if re.search(r"\bselect\b[\s\S]*\bfrom\s+`tab", text) or text.lstrip().startswith("select "):
			out.append((text, names))
	return out


# ---------------------------------------------------------------------------


class TestReportRecords(unittest.TestCase):
	def _record(self, name):
		folder = REPORT_RECORDS[name][0]
		return json.loads((REPORTS / folder / f"{folder}.json").read_text(encoding="utf-8"))

	def test_each_is_a_script_report_in_the_inventory_module(self):
		for name, (folder, ref, _roles) in REPORT_RECORDS.items():
			with self.subTest(name):
				record = self._record(name)
				self.assertEqual(record["name"], name)
				self.assertEqual(record["report_name"], name)
				self.assertEqual(record["report_type"], "Script Report")
				self.assertEqual(record["module"], "Inventory Enhancements")
				self.assertEqual(record["is_standard"], "Yes")
				self.assertEqual(record["ref_doctype"], ref)
				self.assertFalse(record["disabled"])
				self.assertFalse(record["prepared_report"])
				self.assertEqual(folder, name.lower().replace(" ", "_"))
				self.assertTrue((REPORTS / folder / "__init__.py").exists())

	def test_the_roles_are_the_stock_ones_plus_projects_and_purchase_for_the_job_reports(self):
		for name, (_folder, _ref, roles) in REPORT_RECORDS.items():
			with self.subTest(name):
				self.assertEqual({r["role"] for r in self._record(name)["roles"]}, roles)
		# The people who use Stock Scan and the Bin Manager can open every one.
		scan_roles = {"System Manager", "Stock Manager", "Stock User", "Inventory Clerk"}
		for roles in (STOCK_ROLES, PROJECT_ROLES):
			self.assertLessEqual(scan_roles, roles)

	def test_a_role_that_can_report_on_the_reference_doctype_is_among_each_reports_roles(self):
		# frappe.desk.query_report.get_report_doc refuses a run without report permission on the
		# reference doctype, whatever the report's own roles say.
		for name, (_folder, ref, roles) in REPORT_RECORDS.items():
			with self.subTest(name):
				self.assertTrue(CAN_REPORT[ref] & roles)
				self.assertIn("Stock User", CAN_REPORT[ref])

	def test_no_filter_is_required_and_each_script_registers_its_report(self):
		for name, (folder, _ref, _roles) in REPORT_RECORDS.items():
			with self.subTest(name):
				js = (REPORTS / folder / f"{folder}.js").read_text(encoding="utf-8")
				self.assertIn(f'frappe.query_reports["{name}"]', js)
				self.assertNotRegex(js, r"\breqd\s*:")
				self.assertIn("report_face", js)

	def test_each_scripts_filters_are_the_ones_the_python_reads(self):
		for name, (folder, _ref, _roles) in REPORT_RECORDS.items():
			with self.subTest(name):
				js = (REPORTS / folder / f"{folder}.js").read_text(encoding="utf-8")
				py = (REPORTS / folder / f"{folder}.py").read_text(encoding="utf-8")
				declared = set(re.findall(r'fieldname:\s*"(\w+)"', js))
				read = set(re.findall(r'filters\.get\("(\w+)"\)', py))
				self.assertEqual(declared, read)

	def test_the_select_options_are_the_rules_own_words(self):
		js = (REPORTS / "location_contents" / "location_contents.js").read_text(encoding="utf-8")
		for status in rules.STATUSES:
			self.assertIn(f'"{status}"', js)
		listed = re.search(r'fieldname: "status"[\s\S]*?options: \[([\s\S]*?)\]', js).group(1)
		self.assertEqual(re.findall(r'"([^"]*)"', listed), ["", *rules.STATUSES])
		js = (REPORTS / "project_materials" / "project_materials.js").read_text(encoding="utf-8")
		listed = re.search(r'fieldname: "show"[\s\S]*?options: \[([\s\S]*?)\]', js).group(1)
		self.assertEqual(tuple(re.findall(r'"([^"]*)"', listed)), rules.SHOW_OPTIONS)

	def test_the_document_links_are_built_with_get_form_link_and_escaped(self):
		js = (REPORTS / "project_materials" / "project_materials.js").read_text(encoding="utf-8")
		self.assertIn("frappe.utils.get_form_link(doctype, name)", js)
		self.assertIn("frappe.utils.escape_html(name)", js)
		self.assertNotIn("/app/", js)
		self.assertNotIn("/desk/", js)


class TestLocationStatus(unittest.TestCase):
	def status(self, holding=False, elsewhere=(), **item):
		return rules.location_status(_item("A", **item), holding=holding, elsewhere=elsewhere)

	def test_on_hand_and_assigned_is_as_expected(self):
		self.assertEqual(self.status(on_hand=3, assigned=True), rules.STATUS_OK)
		self.assertEqual(self.status(on_hand=3, is_default=True), rules.STATUS_OK)

	def test_assigned_with_none_on_hand(self):
		self.assertEqual(self.status(assigned=True), rules.STATUS_EMPTY)
		self.assertEqual(self.status(is_default=True), rules.STATUS_EMPTY)

	def test_on_hand_and_not_assigned_anywhere(self):
		self.assertEqual(self.status(on_hand=1, undecided=True), rules.STATUS_UNASSIGNED)

	def test_on_hand_and_assigned_to_another_bin(self):
		self.assertEqual(
			self.status(on_hand=1, undecided=True, elsewhere=["Bin A-2 - SF"]), rules.STATUS_ELSEWHERE
		)
		# Assigned here as well: this bin is one of its bins, so nothing is wrong.
		self.assertEqual(self.status(on_hand=1, assigned=True, elsewhere=["Bin A-2 - SF"]), rules.STATUS_OK)

	def test_an_order_on_its_way_answers_before_empty(self):
		self.assertEqual(self.status(on_order=5), rules.STATUS_ON_ORDER)
		self.assertEqual(self.status(on_order=5, assigned=True), rules.STATUS_ON_ORDER)
		# Stock on hand is judged first, whatever is on order.
		self.assertEqual(self.status(on_hand=1, on_order=5), rules.STATUS_UNASSIGNED)

	def test_gone_and_never_decided(self):
		self.assertEqual(self.status(undecided=True), rules.STATUS_UNDECIDED)

	def test_a_place_that_is_never_a_bin_holds_stock_without_a_problem(self):
		self.assertEqual(self.status(holding=True, on_hand=2), rules.STATUS_HELD)
		self.assertEqual(self.status(holding=True, on_hand=2, elsewhere=["Bin A-1 - SF"]), rules.STATUS_HELD)
		self.assertEqual(self.status(holding=True, on_order=2), rules.STATUS_ON_ORDER)
		self.assertEqual(self.status(holding=True, is_default=True), rules.STATUS_NOTHING)
		self.assertEqual(self.status(holding=True, undecided=True), rules.STATUS_NOTHING)

	def test_the_problems_are_the_four_somebody_acts_on(self):
		self.assertEqual(
			rules.PROBLEMS,
			{rules.STATUS_EMPTY, rules.STATUS_UNASSIGNED, rules.STATUS_ELSEWHERE, rules.STATUS_UNDECIDED},
		)
		for status in (rules.STATUS_OK, rules.STATUS_ON_ORDER, rules.STATUS_HELD, rules.STATUS_NOTHING):
			self.assertFalse(rules.is_problem(status))
		self.assertEqual(
			set(rules.STATUSES),
			rules.PROBLEMS
			| {rules.STATUS_OK, rules.STATUS_ON_ORDER, rules.STATUS_HELD, rules.STATUS_NOTHING},
		)

	def test_assigned_here_says_how(self):
		self.assertEqual(rules.assigned_here(_item("A", assigned=True)), rules.ASSIGNED_RULE)
		self.assertEqual(rules.assigned_here(_item("A", is_default=True)), rules.ASSIGNED_DEFAULT)
		self.assertEqual(rules.assigned_here(_item("A", assigned=True, is_default=True)), rules.ASSIGNED_BOTH)
		self.assertEqual(rules.assigned_here(_item("A")), rules.ASSIGNED_NO)

	def test_other_bins_are_the_rules_and_only_then_the_default(self):
		by_rule = {"A": {"Bin A-1 - SF", "Bin A-2 - SF"}}
		by_default = {"A": {"Bin A-10 - SF"}, "B": {"Bin A-10 - SF"}}
		self.assertEqual(rules.other_bins("A", "Bin A-1 - SF", by_rule, by_default), ["Bin A-2 - SF"])
		self.assertEqual(rules.other_bins("B", "Bin A-1 - SF", by_rule, by_default), ["Bin A-10 - SF"])
		self.assertEqual(rules.other_bins("B", "Bin A-10 - SF", by_rule, by_default), [])
		self.assertEqual(rules.other_bins("C", "Bin A-1 - SF", by_rule, by_default), [])

	def test_a_place_that_is_never_a_bin_is_never_the_other_bin(self):
		# Production has Putaway Rules and Item Defaults naming the warehouse that becomes the
		# Project Shelf. Neither makes it somebody's bin.
		not_bins = {"Project Shelf - SF", "Site J1 - SF", "Boneyard - SF", "Shelf A - SF"}
		by_rule = {"A": {"Project Shelf - SF"}, "B": {"Project Shelf - SF", "Bin A-2 - SF"}}
		by_default = {"A": {"Site J1 - SF", "Boneyard - SF", "Shelf A - SF"}, "C": {"Bin A-2 - SF"}}
		self.assertEqual(rules.other_bins("A", "Bin A-1 - SF", by_rule, by_default, not_bins), [])
		self.assertEqual(
			rules.other_bins("B", "Bin A-1 - SF", by_rule, by_default, not_bins), ["Bin A-2 - SF"]
		)
		self.assertEqual(
			rules.other_bins("C", "Bin A-1 - SF", by_rule, by_default, not_bins), ["Bin A-2 - SF"]
		)


class TestPlaces(unittest.TestCase):
	KW = dict(shelf="Project Shelf - SF", sites=SITES, boneyard="Boneyard - SF", vehicles={"Van - SF"})

	def test_each_kind(self):
		self.assertEqual(rules.place_kind("Bin A-1 - SF", **self.KW), rules.KIND_SHOP)
		self.assertEqual(rules.place_kind("Project Shelf - SF", **self.KW), rules.KIND_SHELF)
		self.assertEqual(rules.place_kind("Site J1 - SF", **self.KW), rules.KIND_SITE)
		self.assertEqual(rules.place_kind("Site Old - SF", **self.KW), rules.KIND_SITE)
		self.assertEqual(rules.place_kind("Boneyard - SF", **self.KW), rules.KIND_BONEYARD)
		self.assertEqual(rules.place_kind("Van - SF", **self.KW), rules.KIND_VEHICLE)

	def test_a_job_site_wins_over_everything_else(self):
		kw = dict(self.KW, sites=SITES | {"Project Shelf - SF", "Boneyard - SF", "Van - SF"})
		for name in ("Project Shelf - SF", "Boneyard - SF", "Van - SF"):
			self.assertEqual(rules.place_kind(name, **kw), rules.KIND_SITE)

	def test_with_no_shelf_set_nothing_is_the_project_shelf(self):
		kw = dict(self.KW, shelf=None)
		self.assertEqual(rules.place_kind("Project Shelf - SF", **kw), rules.KIND_SHOP)

	def test_the_places_that_are_never_a_bin(self):
		self.assertEqual(
			{k for k in rules.KINDS if rules.is_holding(k)},
			{rules.KIND_SHELF, rules.KIND_SITE, rules.KIND_BONEYARD},
		)
		self.assertEqual(rules.kind_rank(rules.KIND_SHOP), 0)

	def test_leaves_under_a_group_walks_the_parents(self):
		self.assertEqual(
			rules.leaves_under(WAREHOUSES, "Shelf A - SF"), ["Bin A-1 - SF", "Bin A-2 - SF", "Bin A-10 - SF"]
		)
		# A disabled bin and a group are not locations; the Boneyard under Inventory is.
		self.assertEqual(
			rules.leaves_under(WAREHOUSES, "Inventory - SF"),
			["Bin A-1 - SF", "Bin A-2 - SF", "Bin A-10 - SF", "Boneyard - SF"],
		)

	def test_a_leaf_is_its_own_answer_and_an_unknown_root_is_none(self):
		self.assertEqual(rules.leaves_under(WAREHOUSES, "Bin A-2 - SF"), ["Bin A-2 - SF"])
		self.assertEqual(rules.leaves_under(WAREHOUSES, "Nowhere - SF"), [])
		self.assertEqual(rules.leaves_under(WAREHOUSES, "Old Bin - SF"), [])

	def test_no_root_is_every_location_and_never_a_transit_one(self):
		every = rules.leaves_under(WAREHOUSES)
		self.assertNotIn("Transit - SF", every)
		self.assertNotIn("Old Bin - SF", every)
		self.assertNotIn("Shelf A - SF", every)
		self.assertEqual(len(every), 9)

	def test_a_parent_chain_that_loops_is_under_nothing(self):
		looped = [
			{"name": "X", "parent_warehouse": "Y"},
			{"name": "Y", "parent_warehouse": "X"},
			{"name": "Root", "parent_warehouse": None, "is_group": 1},
		]
		self.assertEqual(rules.leaves_under(looped, "Root"), [])


class TestShelfSplit(unittest.TestCase):
	def test_the_jobs_shares_add_up_to_the_shelf(self):
		# Production's own shape on 2026-11-12: four jobs' receipts of one item, 71 on the shelf.
		shares = rules.shelf_shares(71, {"J1": 10, "J2": 18, "J3": 31, "J4": 12})
		self.assertEqual(shares, [("J1", 10.0), ("J2", 18.0), ("J3", 31.0), ("J4", 12.0)])

	def test_what_no_job_accounts_for_is_under_no_job_and_comes_last(self):
		self.assertEqual(rules.shelf_shares(10, {"J2": 3, "J1": 2}), [("J1", 2.0), ("J2", 3.0), ("", 5.0)])
		self.assertEqual(rules.shelf_shares(4, {}), [("", 4.0)])
		self.assertEqual(rules.shelf_shares(4, None), [("", 4.0)])

	def test_a_count_below_the_claims_leaves_the_jobs_alone_and_shows_the_gap(self):
		# The jobs' figures are the ledger's, the same ones the Project Shelf's own screens show.
		# The no-job share carries the difference, so the rows still add up to what is there.
		shares = rules.shelf_shares(6, {"J1": 4, "J2": 8})
		self.assertEqual(shares, [("J1", 4.0), ("J2", 8.0), ("", -6.0)])
		self.assertAlmostEqual(sum(q for _p, q in shares), 6)

	def test_a_job_that_had_more_leave_than_arrive_reads_negative(self):
		shares = rules.shelf_shares(5, {"J1": -2, "J2": 0, "J3": 5})
		self.assertEqual(shares, [("J1", -2.0), ("J3", 5.0), ("", 2.0)])
		self.assertAlmostEqual(sum(q for _p, q in shares), 5)

	def test_an_empty_shelf_still_shows_what_the_ledger_claims(self):
		self.assertEqual(rules.shelf_shares(0, {"J1": 5}), [("J1", 5.0), ("", -5.0)])
		self.assertEqual(rules.shelf_shares(0, {}), [])
		self.assertEqual(rules.shelf_shares(0, None), [])

	def test_the_gaps_are_counted_by_item(self):
		rows = [
			{"item_code": "A", "project": "J1", "qty": 4},
			{"item_code": "A", "project": "", "qty": 2},
			{"item_code": "B", "project": "", "qty": -3},
			{"item_code": "C", "project": "", "qty": -1},
			{"item_code": "D", "project": "J1", "qty": -9},
		]
		self.assertEqual(rules.shelf_gaps(rows), (1, 2))
		self.assertEqual(rules.shelf_gaps([]), (0, 0))


def _order(item, po, qty, received=0, status="To Receive and Bill", cf=1, **extra):
	line = {
		"item_code": item,
		"item_name": ITEMS[item]["item_name"],
		"purchase_order": po,
		"po_status": status,
		"transaction_date": dt.date(2026, 9, 1),
		"qty": qty,
		"stock_qty": qty * cf,
		"received_qty": received,
		"conversion_factor": cf,
		"stock_uom": ITEMS[item]["stock_uom"],
		"amount": 10.0 * qty,
	}
	line.update(extra)
	return line


def _receipt(item, pr, qty, is_return=0, cf=1, day=dt.date(2026, 9, 20), **extra):
	line = {
		"item_code": item,
		"item_name": ITEMS[item]["item_name"],
		"purchase_receipt": pr,
		"is_return": is_return,
		"posting_date": day,
		"qty": qty,
		"stock_qty": qty * cf,
		"conversion_factor": cf,
		"stock_uom": ITEMS[item]["stock_uom"],
	}
	line.update(extra)
	return line


class TestProjectRollup(unittest.TestCase):
	def rows(self, orders=(), receipts=(), **kwargs):
		found = rules.project_item_rows(list(orders), list(receipts), items=ITEMS, **kwargs)
		return {row["item_code"]: row for row in found}

	def test_ordered_received_and_placed(self):
		rows = self.rows(
			[_order("A", "PO-1", 10, received=10, status="To Bill")],
			[_receipt("A", "PR-1", 10)],
			shelf={"A": 4},
			site={"A": (3, 30.0)},
			used={"A": 3},
		)
		row = rows["A"]
		self.assertEqual(
			(row["ordered"], row["received"], row["on_shelf"], row["at_site"], row["used"]), (10, 10, 4, 3, 3)
		)
		self.assertEqual(row["not_located"], 0)
		self.assertEqual(row["still_to_come"], 0)
		self.assertEqual(row["standing"], rules.STANDING_RECEIVED)
		self.assertEqual(row["where_note"], "")
		self.assertEqual(row["purchase_orders"], "PO-1")
		self.assertEqual(row["receipts"], "PR-1")
		self.assertEqual(row["is_stock_item"], 1)

	def test_one_row_per_item_with_every_document_behind_it(self):
		rows = self.rows(
			[
				_order("A", "PO-2", 2, received=2),
				_order("A", "PO-1", 3, received=3),
				_order("A", "PO-1", 1, 1),
			],
			[_receipt("A", "PR-2", 2), _receipt("A", "PR-1", 4, day=dt.date(2026, 10, 2))],
		)
		self.assertEqual(list(rows), ["A"])
		self.assertEqual(rows["A"]["ordered"], 6)
		self.assertEqual(rows["A"]["received"], 6)
		self.assertEqual(rows["A"]["purchase_orders"], "PO-1, PO-2")
		self.assertEqual(rows["A"]["receipts"], "PR-1, PR-2")
		self.assertEqual(rows["A"]["first_ordered"], dt.date(2026, 9, 1))
		self.assertEqual(rows["A"]["last_received"], dt.date(2026, 10, 2))

	def test_a_line_that_is_not_a_stock_item_is_ordered_and_received_and_nothing_more(self):
		rows = self.rows([_order("N", "PO-1", 5, received=5, status="To Bill")], [_receipt("N", "PR-1", 5)])
		row = rows["N"]
		self.assertEqual(row["is_stock_item"], 0)
		self.assertEqual((row["ordered"], row["received"]), (5, 5))
		self.assertEqual((row["on_shelf"], row["at_site"], row["used"]), (0, 0, 0))
		self.assertEqual(row["not_located"], 5)
		self.assertEqual(row["where_note"], rules.WHERE_NOT_STOCK)

	def test_a_stock_item_received_into_a_shop_bin_names_the_bin(self):
		rows = self.rows(
			[_order("A", "PO-1", 2, received=2, status="To Bill")],
			[_receipt("A", "PR-1", 2)],
			received_into={"A": ["Bin A-1"]},
		)
		self.assertEqual(rows["A"]["not_located"], 2)
		self.assertEqual(rows["A"]["received_into"], "Bin A-1")
		self.assertEqual(rows["A"]["where_note"], rules.WHERE_SHOP_BIN)

	def test_a_stock_item_received_before_it_was_one_was_never_recorded(self):
		rows = self.rows([_order("A", "PO-1", 2, received=2)], [_receipt("A", "PR-1", 2)])
		self.assertEqual(rows["A"]["received_into"], "")
		self.assertEqual(rows["A"]["where_note"], rules.WHERE_NOT_LOCATED)

	def test_still_to_come_is_what_open_lines_are_short(self):
		rows = self.rows([_order("A", "PO-1", 10, received=4)], [_receipt("A", "PR-1", 4)])
		self.assertEqual(rows["A"]["still_to_come"], 6)
		self.assertEqual(rows["A"]["closed_short"], 0)
		self.assertEqual(rows["A"]["standing"], rules.STANDING_WAITING)

	def test_an_order_on_hold_is_still_to_come(self):
		rows = self.rows([_order("A", "PO-1", 2, status="On Hold")])
		self.assertEqual(rows["A"]["still_to_come"], 2)

	def test_a_closed_or_delivered_order_is_not_bringing_anything(self):
		rows = self.rows(
			[
				_order("A", "PO-1", 10, received=4, status="Closed"),
				_order("B", "PO-2", 3, status="Delivered"),
			],
			[_receipt("A", "PR-1", 4)],
		)
		self.assertEqual(rows["A"]["still_to_come"], 0)
		self.assertEqual(rows["A"]["closed_short"], 6)
		self.assertEqual(rows["A"]["standing"], rules.STANDING_RECEIVED)
		self.assertEqual(rows["B"]["closed_short"], 3)
		self.assertEqual(rows["B"]["standing"], rules.STANDING_CLOSED_SHORT)

	def test_a_line_received_over_its_quantity_is_owed_nothing(self):
		rows = self.rows([_order("A", "PO-1", 5, received=7)], [_receipt("A", "PR-1", 7)])
		self.assertEqual(rows["A"]["still_to_come"], 0)
		self.assertEqual(rows["A"]["received"], 7)

	def test_quantities_are_in_the_stock_unit(self):
		# Ordered by the box of 12.
		rows = self.rows([_order("A", "PO-1", 2, received=1, cf=12)], [_receipt("A", "PR-1", 1, cf=12)])
		self.assertEqual(rows["A"]["ordered"], 24)
		self.assertEqual(rows["A"]["received"], 12)
		self.assertEqual(rows["A"]["still_to_come"], 12)

	def test_a_line_with_no_stored_stock_qty_uses_its_factor(self):
		rows = self.rows([_order("A", "PO-1", 2, cf=5, stock_qty=None)])
		self.assertEqual(rows["A"]["ordered"], 10)
		rows = self.rows([_order("A", "PO-1", 2, stock_qty=None, conversion_factor=None)])
		self.assertEqual(rows["A"]["ordered"], 2)

	def test_a_store_run_is_received_with_no_order(self):
		rows = self.rows([], [_receipt("B", "PR-9", 3)])
		self.assertEqual((rows["B"]["ordered"], rows["B"]["received"]), (0, 3))
		self.assertEqual(rows["B"]["standing"], rules.STANDING_NO_ORDER)
		self.assertEqual(rows["B"]["purchase_orders"], "")

	def test_a_return_receipt_is_returned_and_not_received(self):
		rows = self.rows(
			[_order("A", "PO-1", 10, received=10, status="To Bill")],
			[_receipt("A", "PR-1", 10), _receipt("A", "PR-2", -4, is_return=1, day=dt.date(2026, 11, 1))],
		)
		row = rows["A"]
		self.assertEqual((row["received"], row["returned"]), (10, 4))
		self.assertEqual(row["not_located"], 6)
		self.assertEqual(row["receipts"], "PR-1, PR-2")
		# The day it was last received is not moved by the return.
		self.assertEqual(row["last_received"], dt.date(2026, 9, 20))

	def _sr(self, item, qty, gone=True, receipt_line=None, name="SR-1"):
		"""One of ``inventory_report_data.supplier_return_lines``' rows."""
		return {
			"item_code": item,
			"receipt_line": receipt_line,
			"qty": qty,
			"gone": gone,
			"supplier_return": name,
		}

	def test_a_record_only_supplier_return_counts_as_returned(self):
		# No return receipt exists for it: the Supplier Return is the only record the goods left.
		rows = self.rows(
			[], [_receipt("A", "PR-1", 10)], supplier_returns=[self._sr("A", 2, receipt_line="L1")]
		)
		self.assertEqual(rows["A"]["returned"], 2)
		self.assertEqual(rows["A"]["return_pending"], 0)
		self.assertEqual(rows["A"]["not_located"], 8)
		self.assertEqual(rows["A"]["supplier_returns"], "SR-1")

	def test_a_supplier_return_and_its_own_return_receipt_are_one_return(self):
		# A Supplier Return sends stock back through ERPNext's return receipt, and both name the
		# receipt line the goods came in on. Counted twice, four returned would read as eight.
		rows = self.rows(
			[],
			[_receipt("A", "PR-1", 10), _receipt("A", "PR-2", -4, is_return=1, returns_line="L1")],
			supplier_returns=[self._sr("A", 4, receipt_line="L1")],
		)
		self.assertEqual(rows["A"]["returned"], 4)
		self.assertEqual(rows["A"]["not_located"], 6)
		self.assertEqual(rows["A"]["receipts"], "PR-1, PR-2")

	def test_per_receipt_line_the_larger_of_the_two_is_what_went_back(self):
		rows = self.rows(
			[],
			[
				_receipt("A", "PR-1", 20),
				# Two went back by hand in the Desk and three more on a Supplier Return's receipt.
				_receipt("A", "PR-2", -2, is_return=1, returns_line="L1"),
				_receipt("A", "PR-3", -3, is_return=1, returns_line="L1"),
				# Another line, returned by hand only.
				_receipt("A", "PR-4", -1, is_return=1, returns_line="L2"),
			],
			supplier_returns=[
				self._sr("A", 3, receipt_line="L1"),
				# A third line, on a Supplier Return whose return receipt is not submitted yet.
				self._sr("A", 6, receipt_line="L3", name="SR-2"),
			],
		)
		self.assertEqual(rows["A"]["returned"], 5 + 1 + 6)
		self.assertEqual(rows["A"]["supplier_returns"], "SR-1, SR-2")

	def test_a_return_that_names_no_receipt_line_stands_alone(self):
		got = rules.returned_by_item(
			[_receipt("A", "PR-2", -2, is_return=1), _receipt("A", "PR-3", -1, is_return=1)],
			[self._sr("A", 4), self._sr("B", 1)],
		)
		self.assertEqual(got, {"A": 7, "B": 1})
		self.assertEqual(rules.returned_by_item([_receipt("A", "PR-1", 5)], []), {})
		self.assertEqual(rules.returned_by_item(None, None), {})

	def test_a_return_asked_for_and_not_sent_back_is_still_here(self):
		rows = self.rows(
			[],
			[_receipt("A", "PR-1", 10)],
			supplier_returns=[self._sr("A", 3, gone=False, receipt_line="L1")],
			shelf={"A": 10},
		)
		row = rows["A"]
		self.assertEqual((row["returned"], row["return_pending"]), (0, 3))
		self.assertEqual((row["on_shelf"], row["not_located"]), (10, 0))
		self.assertEqual(row["supplier_returns"], "SR-1")

	def test_parts_taken_from_shop_stock_list_with_no_order(self):
		rows = self.rows(used={"C": 4})
		self.assertEqual((rows["C"]["ordered"], rows["C"]["received"], rows["C"]["used"]), (0, 0, 4))
		self.assertEqual(rows["C"]["standing"], rules.STANDING_FROM_STOCK)
		self.assertEqual(rows["C"]["not_located"], 0)

	def test_not_located_is_never_less_than_nothing(self):
		rows = self.rows([], [_receipt("A", "PR-1", 2)], shelf={"A": 5}, used={"A": 3})
		self.assertEqual(rows["A"]["not_located"], 0)

	def test_rows_come_back_by_item_code_with_the_items_own_name(self):
		rows = rules.project_item_rows(
			[_order("B", "PO-1", 1, item_name="typed on the line"), _order("A", "PO-1", 1)], [], items=ITEMS
		)
		self.assertEqual([r["item_code"] for r in rows], ["A", "B"])
		self.assertEqual(rows[1]["item_name"], "Bravo")
		self.assertEqual(rows[1]["item_group"], "Fittings")

	def test_the_show_filter(self):
		rows = self.rows(
			[_order("A", "PO-1", 5), _order("B", "PO-1", 2, received=2), _order("N", "PO-1", 1, received=1)],
			[_receipt("B", "PR-1", 2), _receipt("N", "PR-1", 1)],
			shelf={"B": 2},
		)

		def kept(show):
			return sorted(code for code, row in rows.items() if rules.show_row(row, show))

		self.assertEqual(kept(rules.SHOW_ALL), ["A", "B", "N"])
		self.assertEqual(kept(rules.SHOW_WAITING), ["A"])
		self.assertEqual(kept(rules.SHOW_HELD), ["B"])
		self.assertEqual(kept(rules.SHOW_NOT_LOCATED), ["N"])
		self.assertEqual(
			rules.project_item_summary(list(rows.values())),
			{"items": 3, "waiting": 1, "held": 1, "not_located": 1, "not_stock": 1},
		)


class TestUsedOnJob(unittest.TestCase):
	HOLDING = {"Project Shelf - SF", "Site J1 - SF"}

	def used(self, *rows):
		keys = ("item_code", "voucher_type", "purpose", "warehouse", "qty")
		return rules.used_by_item([dict(zip(keys, row, strict=True)) for row in rows], holding=self.HOLDING)

	def test_an_issue_a_delivery_and_an_invoice_are_use(self):
		self.assertEqual(
			self.used(
				("A", "Stock Entry", "Material Issue", "Bin A-1 - SF", -4),
				("A", "Stock Entry", "Material Issue", "Site J1 - SF", -1),
				("B", "Delivery Note", None, "Bin A-1 - SF", -2),
				("C", "Sales Invoice", None, "Bin A-1 - SF", -3),
			),
			{"A": 5, "B": 2, "C": 3},
		)

	def test_parts_brought_back_lower_it_and_never_below_nothing(self):
		self.assertEqual(
			self.used(
				("A", "Stock Entry", "Material Issue", "Bin A-1 - SF", -5),
				("A", "Stock Entry", "Material Receipt", "Bin A-1 - SF", 2),
			),
			{"A": 3},
		)
		# Production, 2026-11-11: two taken for a job and two brought back.
		self.assertEqual(
			self.used(
				("B", "Stock Entry", "Material Issue", "Bin A-1 - SF", -2),
				("B", "Stock Entry", "Material Receipt", "Bin A-1 - SF", 2),
			),
			{},
		)
		self.assertEqual(self.used(("C", "Stock Entry", "Material Receipt", "Bin A-1 - SF", 2)), {})

	def test_a_receipt_onto_the_shelf_or_a_site_is_not_parts_coming_back(self):
		self.assertEqual(
			self.used(
				("A", "Stock Entry", "Material Issue", "Bin A-1 - SF", -5),
				("A", "Stock Entry", "Material Receipt", "Project Shelf - SF", 2),
				("A", "Stock Entry", "Material Receipt", "Site J1 - SF", 1),
			),
			{"A": 5},
		)

	def test_a_transfer_a_purchase_and_a_count_are_not_use(self):
		self.assertEqual(
			self.used(
				("A", "Stock Entry", "Material Transfer", "Bin A-1 - SF", -5),
				("A", "Stock Entry", "Material Transfer", "Site J1 - SF", 5),
				("A", "Purchase Receipt", None, "Project Shelf - SF", 9),
				("A", "Stock Reconciliation", None, "Bin A-1 - SF", 0),
			),
			{},
		)

	def test_received_into_reads_the_ledger_and_leaves_the_shelf_and_sites_out(self):
		keys = ("item_code", "voucher_type", "purpose", "warehouse", "qty")
		rows = [
			("A", "Purchase Receipt", None, "Bin A-2 - SF", 2),
			("A", "Purchase Receipt", None, "Bin A-1 - SF", 1),
			("A", "Purchase Receipt", None, "Project Shelf - SF", 9),
			("B", "Purchase Receipt", None, "Site J1 - SF", 9),
			("C", "Stock Entry", "Material Receipt", "Bin A-1 - SF", 4),
			("D", "Purchase Receipt", None, "Bin A-1 - SF", -1),
		]
		self.assertEqual(
			rules.received_into([dict(zip(keys, row, strict=True)) for row in rows], holding=self.HOLDING),
			{"A": ["Bin A-1 - SF", "Bin A-2 - SF"]},
		)


class TestHeldByProject(unittest.TestCase):
	def test_one_row_per_job_with_shelf_and_site_apart(self):
		rows = rules.held_by_project(
			[
				{"project": "JOB-1", "item_code": "A", "qty": 4, "value": 40.0},
				{"project": "JOB-1", "item_code": "B", "qty": 1, "value": 5.0},
				{"project": "", "item_code": "C", "qty": 9, "value": 90.0},
			],
			[
				{"project": "JOB-1", "item_code": "A", "qty": 2, "value": 20.0},
				{"project": "JOB-2", "item_code": "D", "qty": 30, "value": 60.0},
				{"project": "", "item_code": "D", "qty": 5, "value": 10.0},
				{"project": "JOB-2", "item_code": "A", "qty": 0, "value": 0.0},
			],
			PROJECTS,
		)
		self.assertEqual([r["project"] for r in rows], ["JOB-1", "JOB-2"])
		first, second = rows
		self.assertEqual((first["shelf_qty"], first["shelf_value"]), (5, 45.0))
		self.assertEqual((first["site_qty"], first["site_value"]), (2, 20.0))
		self.assertEqual(first["total_value"], 65.0)
		self.assertEqual(first["items"], 2)
		self.assertEqual(first["customer"], "Cust 1")
		self.assertEqual((second["shelf_qty"], second["site_qty"], second["items"]), (0, 30, 1))
		self.assertEqual(second["project_status"], "Completed")


class TestStay(unittest.TestCase):
	def rows(self, *pairs):
		return [{"posting_date": day, "qty_after_transaction": after} for day, after in pairs]

	def test_arrived_is_the_day_the_balance_last_rose_from_nothing(self):
		arrived, last_in = rules.stay(self.rows(("2026-09-01", 5), ("2026-09-10", 2), ("2026-10-01", 8)))
		self.assertEqual(arrived, dt.date(2026, 9, 1))
		self.assertEqual(last_in, dt.date(2026, 10, 1))

	def test_used_up_and_sent_again_starts_a_new_stay(self):
		arrived, last_in = rules.stay(
			self.rows(("2026-08-01", 5), ("2026-08-20", 0), ("2026-10-05", 3), ("2026-10-09", 1))
		)
		self.assertEqual(arrived, dt.date(2026, 10, 5))
		self.assertEqual(last_in, dt.date(2026, 10, 5))

	def test_a_count_is_read_from_its_balance(self):
		# ERPNext 16 stores a count with actual_qty 0; the balance after is what moves.
		arrived, last_in = rules.stay(self.rows(("2026-09-01", 4), ("2026-09-15", 6)))
		self.assertEqual((arrived, last_in), (dt.date(2026, 9, 1), dt.date(2026, 9, 15)))
		arrived, last_in = rules.stay(self.rows(("2026-09-01", 4), ("2026-09-15", 0)))
		self.assertIsNone(arrived)
		self.assertEqual(last_in, dt.date(2026, 9, 1))

	def test_no_ledger_is_no_answer(self):
		self.assertEqual(rules.stay([]), (None, None))
		self.assertEqual(rules.stay(None), (None, None))

	def test_days_since(self):
		self.assertEqual(rules.days_since("2026-11-02", "2026-11-12"), 10)
		self.assertEqual(rules.days_since(dt.date(2026, 11, 12), TODAY), 0)
		self.assertEqual(rules.days_since("2026-12-01", TODAY), 0)
		self.assertIsNone(rules.days_since(None, TODAY))


class TestSharedReads(_Case):
	def test_the_project_shelf_is_none_before_the_field_exists(self):
		# No such field on the settings document: .get answers None and nothing raises.
		STATE["settings"] = {"boneyard_warehouse": "Boneyard - SF"}
		self.assertIsNone(data.project_shelf())

	def test_the_project_shelf_is_none_when_the_settings_cannot_be_read(self):
		STATE["settings_raises"] = True
		self.assertIsNone(data.project_shelf())
		self.assertIsNone(data.boneyard())

	def test_the_project_shelf_must_be_one_enabled_location(self):
		STATE["settings"] = {"project_shelf_warehouse": " Project Shelf - SF "}
		self.assertIsNone(data.project_shelf())  # no such warehouse
		STATE["values"][("Warehouse", "Project Shelf - SF")] = {"name": "Project Shelf - SF", "is_group": 1}
		self.assertIsNone(data.project_shelf())
		STATE["values"][("Warehouse", "Project Shelf - SF")] = {"name": "Project Shelf - SF", "disabled": 1}
		self.assertIsNone(data.project_shelf())
		STATE["values"][("Warehouse", "Project Shelf - SF")] = {"name": "Project Shelf - SF"}
		self.assertEqual(data.project_shelf(), "Project Shelf - SF")

	def test_the_setting_is_never_read_with_get_single_value_or_from_tabsingles(self):
		source = _strip_py((INV / "inventory_report_data.py").read_text(encoding="utf-8"))
		self.assertNotIn("get_single_value", source)
		self.assertNotIn("Singles", source)
		self.assertIn("get_cached_doc(SETTINGS).get(field)", source)

	def test_vehicles_are_empty_before_the_employee_field_exists(self):
		self.assertEqual(data.vehicle_warehouses(WAREHOUSES), set())
		self.assertEqual([c for c in STATE["get_all_calls"] if c["doctype"] == "Employee"], [])

	def test_a_vehicle_is_the_employees_warehouse_and_everything_under_it(self):
		STATE["columns"].add(("Employee", "custom_default_vehicle_warehouse"))
		STATE["get_all"]["Employee"] = [
			{"custom_default_vehicle_warehouse": "Van - SF"},
			{"custom_default_vehicle_warehouse": "Shelf A - SF"},
		]
		self.assertEqual(
			data.vehicle_warehouses(WAREHOUSES),
			{"Van - SF", "Shelf A - SF", "Bin A-1 - SF", "Bin A-2 - SF", "Bin A-10 - SF"},
		)

	def test_places_reads_the_job_sites_from_the_job_sites_module(self):
		STATE["get_all"]["Warehouse"] = WAREHOUSES
		self.patched.set(job_sites, "site_map", lambda: dict(SITE_PROJECTS))
		self.patched.set(job_sites, "site_warehouses", lambda: set(SITES))
		self.patched.set(job_sites, "ready", lambda: True)
		where = data.places()
		self.assertEqual(where.sites, SITES)
		self.assertEqual(where.site_projects, SITE_PROJECTS)
		self.assertIsNone(where.shelf)
		self.assertTrue(where.sites_ready)
		self.assertEqual(where.names["Bin A-1 - SF"], "Bin A-1")
		self.assertEqual(where.parents["Bin A-1 - SF"], "Shelf A - SF")
		self.assertEqual(data.kind_of("Site J1 - SF", where), rules.KIND_SITE)

	def test_chunks_are_sorted_unique_and_capped(self):
		self.assertEqual(data.chunks(["b", "a", "b", None, ""], size=1), [["a"], ["b"]])
		self.assertEqual(data.chunks([]), [])

	def _shelf_sql(self, bins, claims):
		_answer_sql("from `tabBin`", bins)
		_answer_sql("from `tabStock Ledger Entry`", claims)

	def test_before_the_project_shelf_module_is_installed_the_adapter_reads_the_ledger_itself(self):
		self._shelf_sql(
			[
				{"item_code": "A", "actual_qty": 10, "valuation_rate": 2.0},
				{"item_code": "B", "actual_qty": 6, "valuation_rate": 1.5},
			],
			[
				{"item_code": "A", "project": "JOB-1", "qty": 4},
				{"item_code": "B", "project": "JOB-1", "qty": 4},
				{"item_code": "B", "project": "JOB-2", "qty": 8},
			],
		)
		rows = data.shelf_stock_by_project("Project Shelf - SF")
		self.assertEqual(
			[(r["item_code"], r["project"], r["qty"], r["value"]) for r in rows],
			[
				("A", "JOB-1", 4.0, 8.0),
				("A", "", 6.0, 12.0),
				("B", "JOB-1", 4.0, 6.0),
				("B", "JOB-2", 8.0, 12.0),
				("B", "", -6.0, -9.0),
			],
		)
		for query, params, _as_dict in STATE["sql_calls"]:
			self.assertEqual(params, {"shelf": "Project Shelf - SF"})
			self.assertNotIn("in %(items)s", query)
		ledger = next(q for q, _p, _d in STATE["sql_calls"] if "tabStock Ledger Entry" in q)
		# A row with no Project is not a claim; what it leaves is the gap to tabBin.
		self.assertIn("coalesce(project, '') <> ''", ledger)
		self.assertIn("is_cancelled = 0", ledger)

	def test_the_adapter_narrows_by_item_and_by_job(self):
		self._shelf_sql(
			[{"item_code": "A", "actual_qty": 10, "valuation_rate": 1}],
			[{"item_code": "A", "project": "JOB-1", "qty": 4}],
		)
		rows = data.shelf_stock_by_project("Project Shelf - SF", item_codes=["A", "A"], projects=["JOB-1"])
		self.assertEqual([(r["project"], r["qty"]) for r in rows], [("JOB-1", 4.0)])
		for query, params, _as_dict in STATE["sql_calls"]:
			self.assertIn("and item_code in %(items)s", query)
			self.assertEqual(params["items"], ("A",))

	def test_a_claim_with_nothing_left_on_the_shelf_still_shows(self):
		self._shelf_sql([], [{"item_code": "A", "project": "JOB-1", "qty": 3}])
		rows = data.shelf_stock_by_project("Project Shelf - SF")
		self.assertEqual([(r["project"], r["qty"]) for r in rows], [("JOB-1", 3.0), ("", -3.0)])

	def test_no_shelf_or_no_items_asks_nothing(self):
		self.assertEqual(data.shelf_stock_by_project(None), [])
		self.assertEqual(data.shelf_stock_by_project("Project Shelf - SF", item_codes=[]), [])
		self.assertEqual(STATE["sql_calls"], [])

	def _module(self, answer=None, warehouse="Project Shelf - SF", raises=False):
		"""A stand-in for the Project Shelf change's module, in the shape its note settles."""
		calls = []

		def shelf_stock(project=None, item_code=None):
			calls.append((project, item_code))
			if raises:
				raise RuntimeError("boom")
			return answer

		def shelf_warehouse():
			if raises:
				raise RuntimeError("boom")
			return warehouse

		module = types.SimpleNamespace(shelf_stock=shelf_stock, warehouse=shelf_warehouse, calls=calls)
		self.patched.set(data, "_shelf_module", lambda: module)
		return module

	def test_with_the_project_shelf_module_installed_the_adapter_asks_it(self):
		module = self._module(
			{
				"warehouse": "Project Shelf - SF",
				"rows": [
					{"project": "JOB-1", "item_code": "A", "qty": 4.0, "qty_in": 10.0, "qty_out": 6.0},
					{"project": "JOB-2", "item_code": "A", "qty": -1.0},
					{"project": "JOB-1", "item_code": "B", "qty": 2.0},
					{"project": "", "item_code": "A", "qty": 99.0},
				],
				"untagged": [],
				"exact": False,
			}
		)
		_answer_sql(
			"from `tabBin`",
			[
				{"item_code": "A", "actual_qty": 5, "valuation_rate": 2.0},
				{"item_code": "B", "actual_qty": 2, "valuation_rate": 1.0},
			],
		)
		rows = data.shelf_stock_by_project("Project Shelf - SF")
		self.assertEqual(
			[(r["item_code"], r["project"], r["qty"]) for r in rows],
			[("A", "JOB-1", 4.0), ("A", "JOB-2", -1.0), ("A", "", 2.0), ("B", "JOB-1", 2.0)],
		)
		self.assertEqual(module.calls, [(None, None)])
		# The ledger is not read twice: only tabBin is asked here.
		self.assertFalse([q for q, _p, _d in STATE["sql_calls"] if "tabStock Ledger Entry" in q])

	def test_one_item_is_asked_for_by_name_and_several_are_filtered_here(self):
		answer = {
			"warehouse": "Project Shelf - SF",
			"rows": [
				{"project": "JOB-1", "item_code": "A", "qty": 1.0},
				{"project": "JOB-1", "item_code": "C", "qty": 1.0},
			],
		}
		module = self._module(answer)
		data.shelf_stock_by_project("Project Shelf - SF", item_codes=["A"])
		rows = data.shelf_stock_by_project("Project Shelf - SF", item_codes=["A", "B"])
		self.assertEqual(module.calls, [(None, "A"), (None, None)])
		self.assertEqual({r["item_code"] for r in rows}, {"A"})

	def test_an_answer_that_cannot_be_used_falls_back_to_the_own_reading(self):
		for kwargs in (
			{"answer": {"warehouse": "Another - SF", "rows": [{"project": "X", "item_code": "A", "qty": 9}]}},
			{"answer": None},
			{"answer": ["not", "a", "dict"]},
			{"raises": True},
		):
			with self.subTest(**{k: str(v)[:20] for k, v in kwargs.items()}):
				_reset()
				self._module(**kwargs)
				self._shelf_sql(
					[{"item_code": "A", "actual_qty": 4, "valuation_rate": 1}],
					[{"item_code": "A", "project": "JOB-1", "qty": 4}],
				)
				rows = data.shelf_stock_by_project("Project Shelf - SF")
				self.assertEqual([(r["project"], r["qty"]) for r in rows], [("JOB-1", 4.0)])

	def test_which_warehouse_is_the_shelf_is_the_modules_answer_when_installed(self):
		STATE["settings"] = {"project_shelf_warehouse": "Stale - SF"}
		self._module(warehouse="Project Shelf - SF")
		self.assertEqual(data.project_shelf(), "Project Shelf - SF")
		self._module(warehouse=None)
		self.assertIsNone(data.project_shelf())
		# A module that raises is not trusted: back to this module's own reading of the setting.
		STATE["settings"] = {"project_shelf_warehouse": "Project Shelf - SF"}
		STATE["values"][("Warehouse", "Project Shelf - SF")] = {"name": "Project Shelf - SF"}
		self._module(raises=True)
		self.assertEqual(data.project_shelf(), "Project Shelf - SF")

	def _import_fails(self, module_name, error):
		"""Make ``from erpnext_enhancements.inventory_enhancements import <module_name>`` raise
		``error`` for one test, as a module that is on disk and broken would."""
		import builtins

		real = builtins.__import__

		def failing(name, globals=None, locals=None, fromlist=(), level=0):
			if name == "erpnext_enhancements.inventory_enhancements" and module_name in (fromlist or ()):
				raise error
			return real(name, globals, locals, fromlist, level)

		self.patched.set(builtins, "__import__", failing)

	def test_a_project_shelf_module_that_fails_to_import_does_not_take_a_report_down(self):
		self.patched.restore()
		for error in (
			RuntimeError("boom"),
			SyntaxError("bad file"),
			AttributeError("frappe.x"),
			ImportError("gone"),
		):
			with self.subTest(type(error).__name__):
				self._import_fails("project_shelf", error)
				self.assertIsNone(data._shelf_module())
				# The shelf is still found from the setting, and its shares from the ledger.
				STATE["settings"] = {"project_shelf_warehouse": "Project Shelf - SF"}
				STATE["values"][("Warehouse", "Project Shelf - SF")] = {"name": "Project Shelf - SF"}
				self.assertEqual(data.project_shelf(), "Project Shelf - SF")
				self._shelf_sql(
					[{"item_code": "A", "actual_qty": 4, "valuation_rate": 1}],
					[{"item_code": "A", "project": "JOB-1", "qty": 4}],
				)
				rows = data.shelf_stock_by_project("Project Shelf - SF")
				self.assertEqual([(r["project"], r["qty"]) for r in rows], [("JOB-1", 4.0)])
				self.patched.restore()
				_reset()

	def test_a_supplier_returns_module_that_fails_to_import_does_not_take_a_report_down(self):
		self.patched.restore()
		for error in (
			RuntimeError("boom"),
			SyntaxError("bad file"),
			AttributeError("frappe.x"),
			ImportError("gone"),
		):
			with self.subTest(type(error).__name__):
				self._import_fails("supplier_returns", error)
				self.assertIsNone(data._returns_module())
				self.assertEqual(data.supplier_return_lines("JOB-1"), [])
				self.patched.restore()

	def test_the_import_guards_catch_any_failure_not_only_a_missing_module(self):
		tree = ast.parse((INV / "inventory_report_data.py").read_text(encoding="utf-8"))
		for name in ("_shelf_module", "_returns_module"):
			fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
			handlers = [h for n in ast.walk(fn) if isinstance(n, ast.Try) for h in n.handlers]
			self.assertEqual([ast.unparse(h.type) for h in handlers], ["Exception"], name)

	def test_the_module_is_found_by_import_and_its_absence_is_not_an_error(self):
		self.patched.restore()
		name = "erpnext_enhancements.inventory_enhancements.project_shelf"
		had = name in sys.modules
		saved = sys.modules.get(name)
		try:
			sys.modules[name] = None  # what a failed import looks like to the import system
			self.assertIsNone(data._shelf_module())
			fake = types.ModuleType(name)
			sys.modules[name] = fake
			self.assertIs(data._shelf_module(), fake)
		finally:
			if had:
				sys.modules[name] = saved
			else:
				sys.modules.pop(name, None)

	def test_nothing_but_the_adapter_reads_whose_stock_is_on_the_shelf(self):
		for path in sorted(REPORTS.glob("*/*.py")):
			source = _strip_py(path.read_text(encoding="utf-8"))
			for word in (
				"_returns_module",
				"returned_lines(",
				"_shelf_claims",
				"_own_shelf_claims",
				"_shelf_module",
				"shelf_stock(",
				"project_shelf_warehouse",
			):
				self.assertNotIn(word, source, f"{path.name}: {word}")

	def test_before_the_supplier_return_module_is_installed_there_are_no_returns(self):
		self.assertEqual(data.supplier_return_lines("JOB-1"), [])

	def _returns(self, lines=None, raises=False):
		"""A stand-in for the Supplier Return change's module, in the shape it settles."""
		calls = []

		def returned_lines(purchase_order_items=None, project=None):
			calls.append((purchase_order_items, project))
			if raises:
				raise RuntimeError("boom")
			return lines

		module = types.SimpleNamespace(returned_lines=returned_lines, calls=calls)
		self.patched.set(data, "_returns_module", lambda: module)
		return module

	def test_the_seam_asks_the_supplier_return_module_for_the_projects_lines(self):
		module = self._returns(
			[
				{
					"supplier_return": "SR-1",
					"status": "Sent Back",
					"purchase_receipt": "PR-1",
					"purchase_receipt_item": "L1",
					"item_code": "A",
					"project": "JOB-1",
					"qty": 1,
					"uom": "Box",
					"stock_qty": 12,
					"gone": True,
					"open": True,
				},
				# A record-only line with no stock quantity of its own, asked for and not sent yet.
				{"supplier_return": "SR-2", "item_code": "N", "qty": -2, "stock_qty": None, "gone": False},
			]
		)
		self.assertEqual(
			data.supplier_return_lines("JOB-1"),
			[
				{
					"item_code": "A",
					"receipt_line": "L1",
					"qty": 12.0,
					"gone": True,
					"supplier_return": "SR-1",
				},
				{
					"item_code": "N",
					"receipt_line": None,
					"qty": 2.0,
					"gone": False,
					"supplier_return": "SR-2",
				},
			],
		)
		self.assertEqual(module.calls, [(None, "JOB-1")])

	def test_an_answer_the_seam_cannot_use_is_no_returns_and_never_an_error(self):
		self._returns(raises=True)
		self.assertEqual(data.supplier_return_lines("JOB-1"), [])
		self._returns(None)
		self.assertEqual(data.supplier_return_lines("JOB-1"), [])
		self._returns(["junk", {"supplier_return": "SR-1"}, {"item_code": "A", "stock_qty": 1, "gone": 1}])
		self.assertEqual([line["item_code"] for line in data.supplier_return_lines("JOB-1")], ["A"])

	def test_the_supplier_return_module_is_found_by_import_and_its_absence_is_not_an_error(self):
		self.patched.restore()
		name = "erpnext_enhancements.inventory_enhancements.supplier_returns"
		had = name in sys.modules
		saved = sys.modules.get(name)
		try:
			sys.modules[name] = None  # what a failed import looks like to the import system
			self.assertIsNone(data._returns_module())
			self.assertEqual(data.supplier_return_lines("JOB-1"), [])
			fake = types.ModuleType(name)
			sys.modules[name] = fake
			self.assertIs(data._returns_module(), fake)
		finally:
			if had:
				sys.modules[name] = saved
			else:
				sys.modules.pop(name, None)

	def test_site_bins_carry_each_sites_job(self):
		_answer_sql(
			"from `tabBin`",
			lambda query, params: [
				{"item_code": "A", "warehouse": w, "actual_qty": 2, "ordered_qty": 1, "stock_value": 20.0}
				for w in params["sites"]
			],
		)
		rows = data.site_bins(_where())
		self.assertEqual(
			sorted((r["warehouse"], r["project"]) for r in rows),
			[("Site J1 - SF", "JOB-1"), ("Site J2 - SF", "JOB-2"), ("Site Old - SF", "")],
		)
		self.assertEqual((rows[0]["qty"], rows[0]["on_order"], rows[0]["value"]), (2.0, 1.0, 20.0))
		rows = data.site_bins(_where(), projects=["JOB-2"], item_codes=["A"])
		self.assertEqual([r["warehouse"] for r in rows], ["Site J2 - SF"])
		self.assertEqual(STATE["sql_calls"][-1][1], {"sites": ("Site J2 - SF",), "items": ("A",)})
		calls = len(STATE["sql_calls"])
		self.assertEqual(data.site_bins(_where(), projects=["JOB-9"]), [])
		self.assertEqual(data.site_bins(_where(sites_ready=False)), [])
		self.assertEqual(len(STATE["sql_calls"]), calls)


class TestLocationContents(_Case):
	def setUp(self):
		super().setUp()
		STATE["items_at"] = {
			"Bin A-1 - SF": [
				_item("A", on_hand=5, assigned=True),
				_item("B", assigned=True, is_default=True),
				_item("C", on_hand=1, undecided=True),
			],
			"Bin A-2 - SF": [_item("D", on_hand=2, undecided=True), _item("A", on_order=4)],
			"Bin A-10 - SF": [_item("B", undecided=True)],
			"Project Shelf - SF": [_item("A", on_hand=9), _item("C", is_default=True)],
			"Site J1 - SF": [_item("A", on_hand=1)],
			"Boneyard - SF": [_item("D", on_hand=3)],
		}
		self.asked = set(STATE["items_at"]) | {"Van - SF"}
		self.patched.set(location_contents, "locations_with_anything", lambda item_code=None: set(self.asked))
		STATE["get_all"]["Putaway Rule"] = [
			{"item_code": "A", "warehouse": "Bin A-1 - SF", "disable": 0},
			{"item_code": "B", "warehouse": "Bin A-1 - SF", "disable": 0},
			{"item_code": "C", "warehouse": "Bin A-2 - SF", "disable": 0},
			{"item_code": "C", "warehouse": "Project Shelf - SF", "disable": 0},
			{"item_code": "D", "warehouse": "Bin A-1 - SF", "disable": 1},
		]
		STATE["get_all"]["Item Default"] = [
			{"parent": "B", "default_warehouse": "Bin A-1 - SF"},
			{"parent": "D", "default_warehouse": "Project Shelf - SF"},
		]
		self.places()

	def run_report(self, **filters):
		return location_contents.execute(filters)

	def by_place(self, rows):
		return {(r["warehouse"], r["item_code"]): r for r in rows}

	def test_it_calls_the_bin_managers_listing_rule_once_per_location_with_no_cap(self):
		self.run_report(location="Shelf A - SF")
		self.assertEqual(
			STATE["items_at_calls"],
			[("Bin A-1 - SF", "SF", None), ("Bin A-2 - SF", "SF", None), ("Bin A-10 - SF", "SF", None)],
		)

	def test_the_report_carries_no_copy_of_the_listing_rule(self):
		source = _strip_py(
			(REPORTS / "location_contents" / "location_contents.py").read_text(encoding="utf-8")
		)
		self.assertIn("stock_scan._items_at(", source)
		self.assertIn("limit=None", source)
		# The rule's own tables: the report asks only which locations are worth asking.
		self.assertNotIn("actual_qty", source)
		self.assertNotIn("ordered_qty", source)
		self.assertNotIn("tabItem`", source)

	def test_a_location_that_could_list_nothing_is_not_asked(self):
		self.asked = {"Bin A-2 - SF"}
		self.run_report(location="Shelf A - SF")
		self.assertEqual([call[0] for call in STATE["items_at_calls"]], ["Bin A-2 - SF"])

	def test_the_statuses_of_a_shelf(self):
		_columns, rows, _message, _chart, _summary = self.run_report(location="Shelf A - SF")
		got = {key: row["status"] for key, row in self.by_place(rows).items()}
		self.assertEqual(
			got,
			{
				("Bin A-1 - SF", "A"): rules.STATUS_OK,
				("Bin A-1 - SF", "B"): rules.STATUS_EMPTY,
				("Bin A-1 - SF", "C"): rules.STATUS_ELSEWHERE,
				("Bin A-2 - SF", "D"): rules.STATUS_UNASSIGNED,
				("Bin A-2 - SF", "A"): rules.STATUS_ON_ORDER,
				("Bin A-10 - SF", "B"): rules.STATUS_UNDECIDED,
			},
		)

	def test_the_other_bin_is_named_and_is_never_the_project_shelf(self):
		rows = self.by_place(self.run_report(location="Shelf A - SF")[1])
		# C has rules at Bin A-2 and at the Project Shelf: only the bin is named.
		self.assertEqual(rows[("Bin A-1 - SF", "C")]["other_bins"], "Bin A-2")
		# D's only assignment anywhere is an Item Default at the Project Shelf, and a disabled rule.
		self.assertEqual(rows[("Bin A-2 - SF", "D")]["other_bins"], "")
		self.assertEqual(rows[("Bin A-1 - SF", "B")]["assigned_here"], rules.ASSIGNED_BOTH)
		self.assertEqual(rows[("Bin A-1 - SF", "A")]["assigned_here"], rules.ASSIGNED_RULE)
		self.assertEqual(rows[("Bin A-1 - SF", "C")]["assigned_here"], rules.ASSIGNED_NO)

	def test_the_shelf_a_job_site_and_the_boneyard_hold_stock_without_a_problem(self):
		rows = self.by_place(self.run_report()[1])
		self.assertEqual(rows[("Project Shelf - SF", "A")]["status"], rules.STATUS_HELD)
		self.assertEqual(rows[("Project Shelf - SF", "A")]["kind"], rules.KIND_SHELF)
		self.assertEqual(rows[("Project Shelf - SF", "C")]["status"], rules.STATUS_NOTHING)
		self.assertEqual(rows[("Site J1 - SF", "A")]["status"], rules.STATUS_HELD)
		self.assertEqual(rows[("Site J1 - SF", "A")]["kind"], rules.KIND_SITE)
		self.assertEqual(rows[("Boneyard - SF", "D")]["status"], rules.STATUS_HELD)
		self.assertFalse(any(r["is_problem"] for r in rows.values() if r["kind"] != rules.KIND_SHOP))

	def test_before_a_project_shelf_is_set_that_warehouse_is_judged_as_a_bin(self):
		self.places(shelf=None)
		_columns, rows, message, _chart, _summary = self.run_report()
		rows = self.by_place(rows)
		# A's bin is Bin A-1, so stock of it here reads as out of place; C's Item Default here
		# reads as an empty bin. With the shelf set, the two were "Held here" and "Nothing here".
		self.assertEqual(rows[("Project Shelf - SF", "A")]["status"], rules.STATUS_ELSEWHERE)
		self.assertEqual(rows[("Project Shelf - SF", "A")]["kind"], rules.KIND_SHOP)
		self.assertEqual(rows[("Project Shelf - SF", "C")]["status"], rules.STATUS_EMPTY)
		self.assertEqual(message, data.NO_SHELF_NOTE + "<br>" + data.ALL_ROWS_NOTE)
		self.places()
		self.assertEqual(self.run_report()[2], data.ALL_ROWS_NOTE)

	def test_only_problems_and_the_status_filter_narrow_the_table_not_the_cards(self):
		_columns, every, _m, _c, cards = self.run_report(location="Shelf A - SF")
		_columns, problems, _m, _c, problem_cards = self.run_report(location="Shelf A - SF", only_problems=1)
		self.assertEqual(len(every), 6)
		self.assertEqual({r["status"] for r in problems}, rules.PROBLEMS)
		self.assertEqual(len(problems), 4)
		self.assertEqual(cards, problem_cards)
		_columns, picked, _m, _c, _s = self.run_report(location="Shelf A - SF", status=rules.STATUS_ON_ORDER)
		self.assertEqual([(r["warehouse"], r["item_code"]) for r in picked], [("Bin A-2 - SF", "A")])
		values = {card["label"]: card["value"] for card in cards}
		self.assertEqual(values["Locations"], 3)
		self.assertEqual(values["Items listed"], 6)
		self.assertEqual(values["As expected"], 1)
		self.assertEqual(values["Assigned, none on hand"], 1)
		self.assertEqual(values["Here, not assigned"], 1)
		self.assertEqual(values["Here, assigned elsewhere"], 1)

	def test_the_item_filter_asks_only_that_items_locations_and_keeps_only_it(self):
		seen = []

		def with_anything(item_code=None):
			seen.append(item_code)
			return {"Bin A-1 - SF", "Bin A-2 - SF"}

		self.patched.set(location_contents, "locations_with_anything", with_anything)
		rows = self.run_report(item_code=" A ")[1]
		self.assertEqual(seen, ["A"])
		self.assertEqual(
			[(r["warehouse"], r["item_code"]) for r in rows], [("Bin A-1 - SF", "A"), ("Bin A-2 - SF", "A")]
		)

	def test_the_item_group_filter_covers_the_groups_under_it(self):
		STATE["descendants"][("Item Group", "All Parts")] = ["Pumps"]
		rows = self.run_report(location="Shelf A - SF", item_group="All Parts")[1]
		self.assertEqual({r["item_code"] for r in rows}, {"C", "D"})
		rows = self.run_report(location="Shelf A - SF", item_group="Fittings")[1]
		self.assertEqual({r["item_code"] for r in rows}, {"A", "B"})

	def test_rows_are_in_shelf_order_with_the_ones_to_act_on_first(self):
		rows = self.run_report(location="Shelf A - SF")[1]
		self.assertEqual(
			[(r["warehouse"], r["item_code"]) for r in rows],
			[
				("Bin A-1 - SF", "C"),
				("Bin A-1 - SF", "B"),
				("Bin A-1 - SF", "A"),
				("Bin A-2 - SF", "D"),
				("Bin A-2 - SF", "A"),
				("Bin A-10 - SF", "B"),
			],
		)
		self.assertEqual({r["under"] for r in rows}, {"Shelf A"})

	def test_a_location_that_does_not_exist_is_refused(self):
		with self.assertRaises(_Thrown):
			self.run_report(location="Nowhere - SF")

	def test_nothing_listed_is_an_empty_report_and_no_lookups(self):
		STATE["items_at"] = {}
		_columns, rows, _message, _chart, cards = self.run_report()
		self.assertEqual(rows, [])
		self.assertEqual({card["value"] for card in cards}, {0})
		self.assertEqual(STATE["get_all_calls"], [])

	def test_the_locations_worth_asking_come_from_three_small_queries(self):
		self.patched.restore()
		_answer_sql("from `tabBin`", [("Bin A-1 - SF",), (None,)])
		_answer_sql("from `tabPutaway Rule`", [("Bin A-2 - SF",)])
		_answer_sql("from `tabItem Default`", [("Bin A-1 - SF",), ("Project Shelf - SF",)])
		self.assertEqual(
			location_contents.locations_with_anything(),
			{"Bin A-1 - SF", "Bin A-2 - SF", "Project Shelf - SF"},
		)
		queries = [query for query, _params, _as_dict in STATE["sql_calls"]]
		self.assertEqual(len(queries), 3)
		self.assertTrue(all("select distinct" in q for q in queries))
		self.assertTrue(all("%(item)s" not in q for q in queries))
		self.assertIn("disable = 0", queries[1])
		STATE["sql_calls"].clear()
		location_contents.locations_with_anything("A")
		for query, params, _as_dict in STATE["sql_calls"]:
			self.assertIn("%(item)s", query)
			self.assertEqual(params, {"item": "A"})


class TestWhereIsThisItem(_Case):
	def setUp(self):
		super().setUp()
		self.bins = [
			{
				"item_code": "A",
				"warehouse": "Bin A-1 - SF",
				"actual_qty": 5,
				"ordered_qty": 0,
				"stock_value": 50,
			},
			{
				"item_code": "A",
				"warehouse": "Project Shelf - SF",
				"actual_qty": 9,
				"ordered_qty": 3,
				"stock_value": 90,
			},
			{
				"item_code": "A",
				"warehouse": "Site J1 - SF",
				"actual_qty": 1,
				"ordered_qty": 0,
				"stock_value": 10,
			},
			{
				"item_code": "A",
				"warehouse": "Boneyard - SF",
				"actual_qty": 2,
				"ordered_qty": 0,
				"stock_value": 0,
			},
			{"item_code": "A", "warehouse": "Van - SF", "actual_qty": 4, "ordered_qty": 0, "stock_value": 40},
		]
		self.asked_bins = []

		def bin_rows(item_codes):
			self.asked_bins.append(item_codes)
			return [dict(b) for b in self.bins if item_codes is None or b["item_code"] in item_codes]

		self.patched.set(where_item, "bin_rows", bin_rows)
		self.moves = (
			{
				("A", "Bin A-1 - SF"): {
					"posting_date": dt.date(2026, 11, 1),
					"voucher_type": "Stock Entry",
					"voucher_no": "SE-1",
				}
			},
			{
				("A", "JOB-1"): {
					"posting_date": dt.date(2026, 10, 2),
					"voucher_type": "Purchase Receipt",
					"voucher_no": "PR-1",
				},
				("A", ""): {
					"posting_date": dt.date(2026, 9, 2),
					"voucher_type": "Stock Reconciliation",
					"voucher_no": "SR-1",
				},
			},
		)
		self.patched.set(where_item, "last_movements", lambda item_codes, shelf=None: self.moves)
		self.shares = [
			{"item_code": "A", "project": "JOB-1", "qty": 6.0, "value": 60.0},
			{"item_code": "A", "project": "", "qty": 3.0, "value": 30.0},
		]
		self.patched.set(
			data, "shelf_stock_by_project", lambda shelf, item_codes=None, projects=None: list(self.shares)
		)
		STATE["get_all"]["Putaway Rule"] = [
			{"item_code": "A", "warehouse": "Bin A-1 - SF", "disable": 0},
			{"item_code": "A", "warehouse": "Bin A-2 - SF", "disable": 0},
		]
		self.places()

	def run_report(self, **filters):
		return where_item.execute(filters)

	def test_one_row_per_place_split_by_kind(self):
		rows = self.run_report(item_code="A")[1]
		self.assertEqual(
			[(r["kind"], r["warehouse"], r["project"], r["on_hand"], r["on_order"]) for r in rows],
			[
				(rules.KIND_SHOP, "Bin A-1 - SF", "", 5.0, 0.0),
				(rules.KIND_SHELF, "Project Shelf - SF", "JOB-1", 6.0, 0.0),
				(rules.KIND_SHELF, "Project Shelf - SF", "", 3.0, 3.0),
				(rules.KIND_SITE, "Site J1 - SF", "JOB-1", 1.0, 0.0),
				(rules.KIND_VEHICLE, "Van - SF", "", 4.0, 0.0),
				(rules.KIND_BONEYARD, "Boneyard - SF", "", 2.0, 0.0),
			],
		)
		self.assertEqual(self.asked_bins, [["A"]])

	def test_the_shelf_is_split_by_job_and_its_shares_add_up(self):
		rows = [r for r in self.run_report(item_code="A")[1] if r["kind"] == rules.KIND_SHELF]
		self.assertEqual(sum(r["on_hand"] for r in rows), 9.0)
		self.assertEqual([r["project_name"] for r in rows], ["First Job", where_item.NO_JOB])
		self.assertEqual([r["value"] for r in rows], [60.0, 30.0])
		# Each shelf row's last movement is the newest row carrying that row's job.
		self.assertEqual([r["last_voucher"] for r in rows], ["PR-1", "SR-1"])

	def test_an_order_to_the_shelf_gets_a_no_job_row_of_its_own_when_every_unit_is_claimed(self):
		self.shares = [{"item_code": "A", "project": "JOB-1", "qty": 9.0, "value": 90.0}]
		rows = [r for r in self.run_report(item_code="A")[1] if r["kind"] == rules.KIND_SHELF]
		self.assertEqual(
			[(r["project"], r["on_hand"], r["on_order"]) for r in rows], [("JOB-1", 9.0, 0.0), ("", 0.0, 3.0)]
		)

	def test_a_shelf_counted_lower_than_the_jobs_claim_shows_the_gap_and_says_what_it_means(self):
		self.shares = [
			{"item_code": "A", "project": "JOB-1", "qty": 12.0, "value": 120.0},
			{"item_code": "A", "project": "", "qty": -3.0, "value": -30.0},
		]
		_columns, rows, message, _chart, _summary = self.run_report(item_code="A")
		shelf = [r for r in rows if r["kind"] == rules.KIND_SHELF]
		self.assertEqual([(r["project"], r["on_hand"]) for r in shelf], [("JOB-1", 12.0), ("", -3.0)])
		self.assertEqual(sum(r["on_hand"] for r in shelf), 9.0)
		self.assertIn(where_item.SHORT_SHELF_NOTE, message)
		self.shares = [{"item_code": "A", "project": "JOB-1", "qty": 9.0, "value": 90.0}]
		self.assertEqual(self.run_report(item_code="A")[2], data.ALL_ROWS_NOTE)

	def test_a_claim_on_an_item_with_nothing_left_on_the_shelf_still_gets_its_rows(self):
		self.shares = [
			{"item_code": "C", "project": "JOB-2", "qty": 2.0, "value": 0.0},
			{"item_code": "C", "project": "", "qty": -2.0, "value": 0.0},
		]
		rows = [r for r in self.run_report()[1] if r["item_code"] == "C"]
		self.assertEqual(
			[(r["kind"], r["project"], r["on_hand"]) for r in rows],
			[(rules.KIND_SHELF, "JOB-2", 2.0), (rules.KIND_SHELF, "", -2.0)],
		)
		self.assertEqual(rows[0]["project_name"], "Second Job")

	def test_a_job_site_row_carries_its_job_and_a_shop_row_its_last_movement(self):
		rows = {r["warehouse"]: r for r in self.run_report(item_code="A")[1] if r["kind"] != rules.KIND_SHELF}
		self.assertEqual(rows["Site J1 - SF"]["project_name"], "First Job")
		self.assertEqual(rows["Bin A-1 - SF"]["last_moved"], dt.date(2026, 11, 1))
		self.assertEqual(rows["Bin A-1 - SF"]["last_voucher_type"], "Stock Entry")
		self.assertIsNone(rows["Van - SF"]["last_moved"])
		self.assertEqual(rows["Bin A-1 - SF"]["under"], "Shelf A")

	def test_its_bin_and_the_items_bins_by_name(self):
		rows = {r["warehouse"]: r for r in self.run_report(item_code="A")[1]}
		self.assertEqual(rows["Bin A-1 - SF"]["assigned_here"], rules.ASSIGNED_RULE)
		self.assertEqual(rows["Van - SF"]["assigned_here"], rules.ASSIGNED_NO)
		self.assertEqual(rows["Van - SF"]["assigned_bins"], "Bin A-1, Bin A-2")

	def test_an_assigned_place_with_none_on_hand_lists_when_the_box_is_ticked(self):
		rows = self.run_report(item_code="A", include_empty=1)[1]
		empty = [r for r in rows if r["warehouse"] == "Bin A-2 - SF"]
		self.assertEqual(
			[(r["on_hand"], r["on_order"], r["assigned_here"]) for r in empty],
			[(0.0, 0.0, rules.ASSIGNED_RULE)],
		)
		self.assertNotIn("Bin A-2 - SF", {r["warehouse"] for r in self.run_report(item_code="A")[1]})

	def test_before_a_project_shelf_is_set_no_row_is_of_that_kind(self):
		self.places(shelf=None)
		asked = []
		self.patched.set(data, "shelf_stock_by_project", lambda *args, **kwargs: asked.append(args) or [])
		_columns, rows, message, _chart, _summary = self.run_report(item_code="A")
		self.assertEqual(asked, [])
		self.assertNotIn(rules.KIND_SHELF, {r["kind"] for r in rows})
		shelf_row = next(r for r in rows if r["warehouse"] == "Project Shelf - SF")
		self.assertEqual(
			(shelf_row["kind"], shelf_row["on_hand"], shelf_row["on_order"]), (rules.KIND_SHOP, 9.0, 3.0)
		)
		self.assertIn(data.NO_SHELF_NOTE, message)

	def test_with_nothing_chosen_it_is_every_item_with_stock(self):
		self.bins.append(
			{
				"item_code": "B",
				"warehouse": "Bin A-2 - SF",
				"actual_qty": 7,
				"ordered_qty": 0,
				"stock_value": 7,
			}
		)
		_columns, rows, _message, _chart, cards = self.run_report()
		self.assertEqual(self.asked_bins, [None])
		self.assertEqual({r["item_code"] for r in rows}, {"A", "B"})
		# Sorted by the item's name, the shop first within an item.
		self.assertEqual(rows[0]["item_name"], "Alpha")
		self.assertEqual(rows[-1]["item_name"], "Bravo")
		values = {card["label"]: card["value"] for card in cards}
		self.assertEqual(values["Items"], 2)
		# Several items: the cards count places, not a sum across different units.
		self.assertEqual(values["In shop bins (places)"], 2)
		self.assertEqual(values["On the Project Shelf (places)"], 2)

	def test_for_one_item_the_cards_are_quantities(self):
		cards = self.run_report(item_code="A")[4]
		values = {card["label"]: card["value"] for card in cards}
		self.assertEqual(values["In shop bins"], 5.0)
		self.assertEqual(values["On the Project Shelf"], 9.0)
		self.assertEqual(values["On job sites"], 1.0)
		self.assertEqual(values["In vehicles"], 4.0)
		self.assertEqual(values["In the Boneyard"], 2.0)

	def test_the_unfiltered_list_is_cut_at_the_cap_and_says_so(self):
		self.patched.set(where_item, "MAX_ROWS", 2)
		_columns, rows, message, _chart, _summary = self.run_report()
		self.assertEqual({r["warehouse"] for r in rows}, {"Bin A-1 - SF", "Project Shelf - SF"})
		self.assertIn("Showing the first 2 places", message)

	def test_a_search_that_matches_nothing_is_empty_and_reads_no_stock(self):
		self.patched.set(where_item, "find_items", lambda filters: ([], False))
		self.assertEqual(self.run_report(search="zzz")[1], [])
		self.assertEqual(self.asked_bins, [])

	def test_a_search_cut_at_the_cap_says_so(self):
		self.patched.set(where_item, "find_items", lambda filters: (["A"], True))
		self.assertIn("More than 500 items match", self.run_report(search="a")[2])

	def test_an_item_wins_over_a_search_and_nothing_chosen_is_no_filter(self):
		self.assertEqual(where_item.find_items(_Dict(item_code=" A ", search="pump")), (["A"], False))
		self.assertEqual(where_item.find_items(_Dict()), (None, False))
		self.assertEqual(where_item.find_items(_Dict(search="x")), (None, False))  # one letter is not a word
		self.assertEqual(STATE["sql_calls"], [])

	def test_a_search_matches_every_word_in_the_code_or_the_name(self):
		_answer_sql("from `tabItem`", [("A",), ("B",)])
		STATE["descendants"][("Item Group", "Pumps")] = ["Small Pumps"]
		codes, cut = where_item.find_items(_Dict(search="ball 50%_valve", item_group="Pumps"))
		self.assertEqual((codes, cut), (["A", "B"], False))
		query, params, _as_dict = STATE["sql_calls"][-1]
		self.assertEqual(query.count("(name like %(w"), 2)
		self.assertIn("item_group in %(groups)s", query)
		self.assertIn("limit %(limit)s", query)
		self.assertEqual(params["w0"], "%ball%")
		# A percent sign or underscore somebody typed is matched as written.
		self.assertEqual(params["w1"], "%50\\%\\_valve%")
		self.assertEqual(params["groups"], ("Pumps", "Small Pumps"))
		self.assertEqual(params["limit"], where_item.MAX_ITEMS + 1)
		self.assertNotIn("ball", query)

	def test_one_more_than_the_cap_means_the_list_was_cut(self):
		self.patched.set(where_item, "MAX_ITEMS", 2)
		_answer_sql("from `tabItem`", [("A",), ("B",), ("C",)])
		self.assertEqual(where_item.find_items(_Dict(search="pump")), (["A", "B"], True))

	def test_the_last_movement_is_the_newest_row_and_creation_breaks_a_tie(self):
		self.patched.restore()
		_answer_sql(
			"where is_cancelled = 0 and warehouse = %(shelf)s",
			[
				{
					"item_code": "A",
					"project": "JOB-1",
					"posting_date": "2026-10-02",
					"voucher_no": "PR-1",
					"creation": "1",
				}
			],
		)
		_answer_sql(
			"group by item_code, warehouse",
			[
				{
					"item_code": "A",
					"warehouse": "Bin A-1 - SF",
					"voucher_no": "SE-1",
					"creation": "2026-11-01 10:00:00",
				},
				{
					"item_code": "A",
					"warehouse": "Bin A-1 - SF",
					"voucher_no": "SE-2",
					"creation": "2026-11-01 10:00:05",
				},
			],
		)
		by_place, by_job = where_item.last_movements(["A"], "Project Shelf - SF")
		self.assertEqual(by_place[("A", "Bin A-1 - SF")]["voucher_no"], "SE-2")
		self.assertEqual(by_job[("A", "JOB-1")]["voucher_no"], "PR-1")
		self.assertEqual(len(STATE["sql_calls"]), 2)
		self.assertEqual(STATE["sql_calls"][1][1], {"items": ("A",), "shelf": "Project Shelf - SF"})
		STATE["sql_calls"].clear()
		where_item.last_movements(None, None)
		self.assertEqual(len(STATE["sql_calls"]), 1)
		self.assertNotIn("%(items)s", STATE["sql_calls"][0][0])


class TestProjectMaterials(_Case):
	def setUp(self):
		super().setUp()
		self.orders = [
			_order("A", "PO-1", 10, received=10, status="To Bill"),
			_order("B", "PO-1", 5, received=2),
			_order("N", "PO-2", 3, received=3, status="To Bill"),
		]
		self.receipts = [_receipt("A", "PR-1", 10), _receipt("B", "PR-1", 2), _receipt("N", "PR-2", 3)]
		self.ledger = [
			{
				"item_code": "A",
				"voucher_type": "Stock Entry",
				"purpose": "Material Issue",
				"warehouse": "Site J1 - SF",
				"qty": -2,
			},
			{
				"item_code": "B",
				"voucher_type": "Purchase Receipt",
				"purpose": None,
				"warehouse": "Bin A-1 - SF",
				"qty": 2,
			},
			{
				"item_code": "A",
				"voucher_type": "Purchase Receipt",
				"purpose": None,
				"warehouse": "Project Shelf - SF",
				"qty": 10,
			},
		]
		self.asked = []
		self.patched.set(
			project_materials, "order_lines", lambda project: self.asked.append(project) or self.orders
		)
		self.patched.set(project_materials, "receipt_lines", lambda project: self.receipts)
		self.patched.set(project_materials, "job_ledger", lambda project: self.ledger)
		self.shelf_rows = [
			{"item_code": "A", "project": "JOB-1", "qty": 5.0, "value": 50.0},
			{"item_code": "C", "project": "JOB-2", "qty": 2.0, "value": 8.0},
			{"item_code": "D", "project": "", "qty": 7.0, "value": 7.0},
		]

		def shelf(shelf, item_codes=None, projects=None):
			return [r for r in self.shelf_rows if projects is None or r["project"] in projects]

		self.patched.set(data, "shelf_stock_by_project", shelf)
		self.site_rows = [
			{
				"project": "JOB-1",
				"warehouse": "Site J1 - SF",
				"item_code": "A",
				"qty": 3.0,
				"on_order": 0.0,
				"value": 30.0,
			},
			{
				"project": "JOB-2",
				"warehouse": "Site J2 - SF",
				"item_code": "D",
				"qty": 40.0,
				"on_order": 0.0,
				"value": 20.0,
			},
			{
				"project": "",
				"warehouse": "Site Old - SF",
				"item_code": "D",
				"qty": 1.0,
				"on_order": 0.0,
				"value": 1.0,
			},
		]

		def sites(where, projects=None, item_codes=None):
			return [r for r in self.site_rows if projects is None or r["project"] in projects]

		self.patched.set(data, "site_bins", sites)
		self.places()

	def run_report(self, **filters):
		return project_materials.execute(filters)

	def test_one_projects_items(self):
		columns, rows, _message, _chart, _summary = self.run_report(project="JOB-1")
		self.assertEqual(self.asked, ["JOB-1"])
		self.assertEqual(columns[0]["fieldname"], "item_code")
		by_item = {r["item_code"]: r for r in rows}
		self.assertEqual(list(by_item), ["A", "B", "N"])
		a = by_item["A"]
		self.assertEqual(
			(a["ordered"], a["received"], a["on_shelf"], a["at_site"], a["used"]), (10, 10, 5, 3, 2)
		)
		self.assertEqual(a["not_located"], 0)
		b = by_item["B"]
		self.assertEqual((b["still_to_come"], b["not_located"], b["received_into"]), (3, 2, "Bin A-1"))
		self.assertEqual(b["where_note"], rules.WHERE_SHOP_BIN)
		n = by_item["N"]
		self.assertEqual(
			(n["is_stock_item"], n["not_located"], n["where_note"]), (0, 3, rules.WHERE_NOT_STOCK)
		)
		self.assertEqual(a["purchase_orders"], "PO-1")
		self.assertEqual(n["receipts"], "PR-2")

	def test_the_shelf_and_the_sites_do_not_count_as_parts_brought_back(self):
		self.ledger.append(
			{
				"item_code": "A",
				"voucher_type": "Stock Entry",
				"purpose": "Material Receipt",
				"warehouse": "Project Shelf - SF",
				"qty": 1,
			}
		)
		rows = {r["item_code"]: r for r in self.run_report(project="JOB-1")[1]}
		self.assertEqual(rows["A"]["used"], 2)

	def test_the_show_and_item_filters_narrow_the_table_not_the_cards(self):
		_c, every, _m, _ch, cards = self.run_report(project="JOB-1")
		_c, waiting, _m, _ch, waiting_cards = self.run_report(project="JOB-1", show=rules.SHOW_WAITING)
		self.assertEqual([r["item_code"] for r in waiting], ["B"])
		self.assertEqual(cards, waiting_cards)
		self.assertEqual([r["item_code"] for r in self.run_report(project="JOB-1", item_code="N")[1]], ["N"])
		values = {card["label"]: card["value"] for card in cards}
		self.assertEqual(
			values,
			{
				"Items": 3,
				"Still to come": 1,
				"On the shelf or at the site": 1,
				"Received, not located": 2,
				"Not stock items": 1,
			},
		)
		self.assertEqual(len(every), 3)

	def test_supplier_returns_reach_the_rows_and_are_not_counted_twice(self):
		asked = []
		self.receipts.append(_receipt("A", "PR-9", -2, is_return=1, returns_line="L1"))

		def lines(project):
			asked.append(project)
			return [
				{"item_code": "A", "receipt_line": "L1", "qty": 2.0, "gone": True, "supplier_return": "SR-1"},
				{
					"item_code": "B",
					"receipt_line": "L2",
					"qty": 1.0,
					"gone": False,
					"supplier_return": "SR-2",
				},
				{"item_code": "C", "receipt_line": "L3", "qty": 5.0, "gone": True, "supplier_return": "SR-3"},
			]

		self.patched.set(data, "supplier_return_lines", lines)
		rows = {r["item_code"]: r for r in self.run_report(project="JOB-1")[1]}
		self.assertEqual(asked, ["JOB-1"])
		# A: the Supplier Return and its own return receipt are one return of two.
		self.assertEqual((rows["A"]["returned"], rows["A"]["supplier_returns"]), (2, "SR-1"))
		self.assertEqual((rows["B"]["returned"], rows["B"]["return_pending"]), (0, 1))
		# C is on the job's list only because of its return, and still gets its name.
		self.assertEqual((rows["C"]["returned"], rows["C"]["item_name"]), (5, "Charlie"))

	def test_the_shelf_is_asked_for_the_job_and_for_the_no_job_shares(self):
		asked = []

		def shelf(shelf, item_codes=None, projects=None):
			asked.append(projects)
			return [
				{"item_code": "A", "project": "JOB-1", "qty": 5.0, "value": 50.0},
				{"item_code": "A", "project": "", "qty": -2.0, "value": -20.0},
				{"item_code": "D", "project": "", "qty": -9.0, "value": -9.0},
			]

		self.patched.set(data, "shelf_stock_by_project", shelf)
		_c, rows, message, _ch, _s = self.run_report(project="JOB-1")
		self.assertEqual(asked, [["JOB-1", ""]])
		by_item = {r["item_code"]: r for r in rows}
		# The job's own figure is the ledger's; the no-job share is not the job's stock.
		self.assertEqual(by_item["A"]["on_shelf"], 5)
		self.assertNotIn("D", by_item)
		# One of this job's shelf items is short on the shelf; D is not this job's.
		self.assertIn(project_materials.SHORT_SHELF_NOTE.format(1), message)

	def test_a_finished_job_reads_the_same(self):
		# Nothing in the one-project view reads the job's status.
		rows = self.run_report(project="JOB-2")[1]
		self.assertEqual([r["item_code"] for r in rows], ["A", "B", "C", "D", "N"])
		by_item = {r["item_code"]: r for r in rows}
		self.assertEqual(by_item["C"]["on_shelf"], 2)
		self.assertEqual(by_item["D"]["at_site"], 40)

	def test_the_line_over_the_table_names_the_job_and_its_site_and_is_escaped(self):
		STATE["get_all"]["Project"] = [dict(PROJECTS["JOB-1"], project_name="<b>First</b> Job")]
		message = self.run_report(project="JOB-1")[2]
		self.assertIn("JOB-1 | &lt;b&gt;First&lt;/b&gt; Job | Active | Cust 1", message)
		self.assertIn("Job site: Site J1", message)
		self.assertNotIn("<b>", message)
		self.assertNotIn(data.NO_SHELF_NOTE, message)

	def test_a_job_with_no_site_and_a_site_with_no_shelf_say_so(self):
		self.places(shelf=None, sites_ready=False)
		_c, rows, message, _ch, _s = self.run_report(project="JOB-1")
		self.assertIn("This job has no job site warehouse", message)
		self.assertIn(data.NO_SHELF_NOTE, message)
		by_item = {r["item_code"]: r for r in rows}
		self.assertEqual(by_item["A"]["on_shelf"], 0)
		# With no shelf, the receipt into that warehouse reads as stock in a shop location.
		self.assertEqual(by_item["A"]["received_into"], "Project Shelf")

	def test_a_project_that_does_not_exist_is_refused(self):
		with self.assertRaises(_Thrown):
			self.run_report(project="JOB-404")

	def test_with_no_project_it_is_what_each_job_holds(self):
		columns, rows, message, _chart, cards = self.run_report(include_finished=1)
		self.assertEqual(self.asked, [])
		self.assertEqual(columns[0]["fieldname"], "project")
		self.assertEqual([r["project"] for r in rows], ["JOB-1", "JOB-2"])
		first = rows[0]
		self.assertEqual(
			(first["shelf_qty"], first["shelf_value"], first["site_qty"], first["site_value"]), (5, 50, 3, 30)
		)
		self.assertEqual(first["total_value"], 80)
		self.assertEqual((rows[1]["shelf_value"], rows[1]["site_value"]), (8, 20))
		self.assertIn("1 item(s) on the Project Shelf are under no job", message)
		self.assertNotIn("counted lower", message)
		self.shelf_rows.append({"item_code": "B", "project": "", "qty": -4.0, "value": -4.0})
		message = self.run_report(include_finished=1)[2]
		self.assertIn(project_materials.SHORT_SHELF_NOTE.format(1), message)
		self.shelf_rows.pop()
		values = {card["label"]: card["value"] for card in cards}
		self.assertEqual(values["Jobs holding stock"], 2)
		self.assertEqual(values["On the Project Shelf"], 58)
		self.assertEqual(values["At job sites"], 50)
		self.assertEqual(values["Held in all"], 108)

	def test_the_customer_filter_and_finished_jobs(self):
		self.assertEqual(
			[r["project"] for r in self.run_report(customer="Cust 2", include_finished=1)[1]], ["JOB-2"]
		)
		# Unticked, a Completed job drops out; ticked is what the script starts with.
		self.assertEqual([r["project"] for r in self.run_report()[1]], ["JOB-1"])
		self.assertEqual([r["project"] for r in self.run_report(include_finished=1)[1]], ["JOB-1", "JOB-2"])

	def test_with_no_shelf_and_no_sites_the_all_jobs_view_is_empty_and_says_why(self):
		self.places(shelf=None, sites_ready=False)
		self.patched.set(data, "site_bins", lambda where, projects=None, item_codes=None: [])
		_c, rows, message, _ch, _s = self.run_report(include_finished=1)
		self.assertEqual(rows, [])
		self.assertIn(data.NO_SHELF_NOTE, message)
		self.assertIn("Job sites are not set up", message)


class TestJobSiteContents(_Case):
	def setUp(self):
		super().setUp()
		self.site_rows = [
			{
				"project": "JOB-1",
				"warehouse": "Site J1 - SF",
				"item_code": "A",
				"qty": 3.0,
				"on_order": 0.0,
				"value": 30.0,
			},
			{
				"project": "JOB-1",
				"warehouse": "Site J1 - SF",
				"item_code": "C",
				"qty": 0.0,
				"on_order": 2.0,
				"value": 0.0,
			},
			{
				"project": "JOB-2",
				"warehouse": "Site J2 - SF",
				"item_code": "D",
				"qty": 40.0,
				"on_order": 0.0,
				"value": 20.0,
			},
			{
				"project": "",
				"warehouse": "Site Old - SF",
				"item_code": "D",
				"qty": 1.0,
				"on_order": 0.0,
				"value": 1.0,
			},
		]
		self.asked = []

		def sites(where, projects=None, item_codes=None):
			self.asked.append((projects, item_codes))
			return [
				dict(r)
				for r in self.site_rows
				if (projects is None or r["project"] in projects)
				and (item_codes is None or r["item_code"] in item_codes)
			]

		self.patched.set(data, "site_bins", sites)
		self.ledger_asked = []

		def ledger(warehouses):
			self.ledger_asked.append(set(warehouses))
			return {
				("A", "Site J1 - SF"): [
					{"posting_date": "2026-08-01", "qty_after_transaction": 5},
					{"posting_date": "2026-08-20", "qty_after_transaction": 0},
					{"posting_date": "2026-11-02", "qty_after_transaction": 2},
					{"posting_date": "2026-11-10", "qty_after_transaction": 3},
				],
				("D", "Site J2 - SF"): [{"posting_date": "2026-07-05", "qty_after_transaction": 40}],
			}

		self.patched.set(job_site_contents, "site_ledger", ledger)
		self.places()

	def run_report(self, **filters):
		return job_site_contents.execute(filters)

	def test_everything_out_on_every_job(self):
		_columns, rows, message, _chart, cards = self.run_report()
		self.assertEqual(message, data.ALL_ROWS_NOTE)
		self.assertEqual(self.asked, [(None, None)])
		self.assertEqual(
			[(r["project"], r["warehouse"], r["item_code"]) for r in rows],
			[
				("JOB-1", "Site J1 - SF", "A"),
				("JOB-1", "Site J1 - SF", "C"),
				("JOB-2", "Site J2 - SF", "D"),
				("", "Site Old - SF", "D"),
			],
		)
		first = rows[0]
		self.assertEqual(
			(first["project_name"], first["customer"], first["project_status"]),
			("First Job", "Cust 1", "Active"),
		)
		self.assertEqual((first["on_hand"], first["value"], first["stock_uom"]), (3.0, 30.0, "Unit"))
		values = {card["label"]: card["value"] for card in cards}
		self.assertEqual(values["Job sites with stock"], 3)
		self.assertEqual(values["Item lines"], 4)
		self.assertEqual(values["Value on job sites"], 51.0)
		self.assertEqual(values["Longest there (days)"], 130)

	def test_when_it_arrived_and_how_long_it_has_been_there(self):
		rows = {(r["warehouse"], r["item_code"]): r for r in self.run_report()[1]}
		a = rows[("Site J1 - SF", "A")]
		# Used up in August and sent again in November: the stay starts in November.
		self.assertEqual(
			(a["arrived"], a["last_arrival"], a["days_there"]),
			(dt.date(2026, 11, 2), dt.date(2026, 11, 10), 10),
		)
		d = rows[("Site J2 - SF", "D")]
		self.assertEqual((d["arrived"], d["days_there"]), (dt.date(2026, 7, 5), 130))
		# On order only: nothing has arrived, and its ledger is not read.
		c = rows[("Site J1 - SF", "C")]
		self.assertEqual((c["on_hand"], c["on_order"], c["arrived"], c["days_there"]), (0.0, 2.0, None, None))
		self.assertEqual(self.ledger_asked, [{"Site J1 - SF", "Site J2 - SF", "Site Old - SF"}])
		# Stock with no ledger behind it has no arrival, and is still listed.
		self.assertIsNone(rows[("Site Old - SF", "D")]["arrived"])

	def test_the_project_filter(self):
		rows = self.run_report(project="JOB-2")[1]
		self.assertEqual(self.asked, [({"JOB-2"}, None)])
		self.assertEqual([r["warehouse"] for r in rows], ["Site J2 - SF"])
		# A finished job's site still lists.
		self.assertEqual(rows[0]["project_status"], "Completed")

	def test_the_customer_filter_finds_that_customers_jobs(self):
		rows = self.run_report(customer="Cust 1")[1]
		self.assertEqual(self.asked, [({"JOB-1"}, None)])
		self.assertEqual({r["project"] for r in rows}, {"JOB-1"})
		self.assertEqual(self.run_report(customer="Cust 1", project="JOB-2")[1], [])
		self.assertEqual(self.run_report(customer="Nobody")[1], [])

	def test_the_item_and_item_group_filters(self):
		self.run_report(item_code="A")
		self.assertEqual(self.asked, [(None, ["A"])])
		rows = self.run_report(item_group="Pumps")[1]
		self.assertEqual({r["item_code"] for r in rows}, {"C", "D"})

	def test_before_job_sites_exist_the_report_is_empty_and_says_so(self):
		self.places(sites_ready=False)
		_columns, rows, message, _chart, cards = self.run_report()
		self.assertEqual(rows, [])
		self.assertEqual(message, "Job sites are not set up on this site yet.<br>" + data.ALL_ROWS_NOTE)
		self.assertEqual(self.asked, [])
		self.assertEqual({card["value"] for card in cards}, {0})

	def test_the_ledger_is_read_in_ledger_order_for_the_given_sites(self):
		self.patched.restore()
		_answer_sql(
			"from `tabStock Ledger Entry`",
			[
				{
					"item_code": "A",
					"warehouse": "Site J1 - SF",
					"posting_date": "2026-11-02",
					"qty_after_transaction": 2,
				},
				{
					"item_code": "A",
					"warehouse": "Site J1 - SF",
					"posting_date": "2026-11-10",
					"qty_after_transaction": 3,
				},
			],
		)
		found = job_site_contents.site_ledger({"Site J1 - SF"})
		self.assertEqual(len(found[("A", "Site J1 - SF")]), 2)
		query, params, _as_dict = STATE["sql_calls"][-1]
		self.assertIn("order by posting_datetime asc, creation asc", query)
		self.assertEqual(params, {"sites": ("Site J1 - SF",)})
		self.assertEqual(job_site_contents.site_ledger(set()), {})


class TestQueries(unittest.TestCase):
	FILES = [INV / "inventory_report_data.py", *sorted(REPORTS.glob("*/*.py"))]
	OURS = [f for f in FILES if f.parent.name in {r[0] for r in REPORT_RECORDS.values()} or f.parent == INV]

	#: The only names an f-string may put into SQL: fixed clauses built in the same function.
	CLAUSES = {"item_clause", "fields", "item_bin", "item_rule", "item_default"}

	def sql(self):
		return [(path, text, names) for path in self.OURS for text, names in _sql_strings(path)]

	def test_there_is_sql_to_check(self):
		self.assertGreaterEqual(len(self.sql()), 12)

	def test_every_stock_ledger_read_skips_canceled_entries(self):
		for path, text, _names in self.sql():
			if "`tabStock Ledger Entry`" in text:
				with self.subTest(path.name, text=text[:60]):
					self.assertEqual(
						text.count("is_cancelled = 0"), text.count("`tabStock Ledger Entry`"), text
					)

	def test_orders_and_receipts_are_submitted_ones_only(self):
		source = (REPORTS / "project_materials" / "project_materials.py").read_text(encoding="utf-8")
		self.assertIn("where po.docstatus = 1", source)
		self.assertIn("where pr.docstatus = 1", source)
		# What lets a Supplier Return and its own return receipt be counted as one return.
		self.assertIn("pri.purchase_receipt_item as returns_line", source)

	def test_the_order_lines_project_is_the_lines_own_else_its_orders(self):
		source = (REPORTS / "project_materials" / "project_materials.py").read_text(encoding="utf-8")
		self.assertIn("poi.project = %(project)s", source)
		self.assertIn("coalesce(poi.project, '') = '' and po.project = %(project)s", source)
		self.assertRegex(
			source,
			r"nullif\(pri\.project, ''\), nullif\(pr\.project, ''\), nullif\(poi\.project, ''\), po\.project",
		)

	def test_values_travel_as_bound_parameters(self):
		for path, text, names in self.sql():
			with self.subTest(path.name, text=text[:60]):
				self.assertLessEqual(names, self.CLAUSES)
				self.assertNotRegex(text, r"%s")
				self.assertNotIn("{", text.replace("%(", ""))
				self.assertNotRegex(text, r"(?i)^\s*with\s")

	def test_no_sql_function_is_passed_to_get_all_as_a_string(self):
		# Frappe 16 refuses it: "SQL functions are not allowed as strings in SELECT".
		for path in self.OURS:
			tree = ast.parse(path.read_text(encoding="utf-8"))
			for node in ast.walk(tree):
				if not (
					isinstance(node, ast.Call) and getattr(node.func, "attr", "") in {"get_all", "get_list"}
				):
					continue
				for keyword in node.keywords:
					if keyword.arg != "fields":
						continue
					for value in ast.walk(keyword.value):
						if isinstance(value, ast.Constant) and isinstance(value.value, str):
							self.assertNotIn("(", value.value, f"{path.name}: {value.value}")

	def test_no_report_reads_a_framework_field_by_attribute(self):
		# Custom fields may not exist yet on a fresh site: reads go through .get.
		for path in self.OURS:
			source = _strip_py(path.read_text(encoding="utf-8"))
			self.assertNotRegex(source, r"\.custom_\w+", path.name)


class TestPermissionsNote(_Case):
	"""Every report says, over its own table, that User Permissions do not narrow it. Until this
	was on the page only the docs said so."""

	def setUp(self):
		super().setUp()
		self.patched.set(location_contents, "locations_with_anything", lambda item_code=None: set())
		self.patched.set(where_item, "bin_rows", lambda codes: [])
		self.patched.set(where_item, "last_movements", lambda codes, shelf=None: ({}, {}))
		self.patched.set(data, "shelf_stock_by_project", lambda *a, **k: [])
		self.patched.set(data, "site_bins", lambda *a, **k: [])
		self.patched.set(project_materials, "order_lines", lambda project: [])
		self.patched.set(project_materials, "receipt_lines", lambda project: [])
		self.patched.set(project_materials, "job_ledger", lambda project: [])
		self.patched.set(job_site_contents, "site_ledger", lambda warehouses: {})

	def messages(self):
		return {
			"Location Contents": location_contents.execute({})[2],
			"Where Is This Item": where_item.execute({})[2],
			"Project Materials, one job": project_materials.execute({"project": "JOB-1"})[2],
			"Project Materials, every job": project_materials.execute({})[2],
			"Job Site Contents": job_site_contents.execute({})[2],
		}

	def test_the_note_is_the_last_line_over_every_report(self):
		for kwargs in ({}, {"shelf": None}, {"sites_ready": False}, {"shelf": None, "sites_ready": False}):
			self.places(**kwargs)
			for name, message in self.messages().items():
				with self.subTest(name, **kwargs):
					self.assertTrue(message.endswith(data.ALL_ROWS_NOTE), message)
					self.assertEqual(message.count(data.ALL_ROWS_NOTE), 1)

	def test_the_note_says_it_in_plain_words(self):
		self.assertEqual(
			data.ALL_ROWS_NOTE,
			"This report shows everything its filters match. User Permissions do not narrow it.",
		)
		self.assertEqual(data.message(), data.ALL_ROWS_NOTE)
		self.assertEqual(data.message("One", None, "", "Two"), "One<br>Two<br>" + data.ALL_ROWS_NOTE)

	def test_no_report_builds_its_message_without_it(self):
		for folder, _ref, _roles in REPORT_RECORDS.values():
			source = _strip_py((REPORTS / folder / f"{folder}.py").read_text(encoding="utf-8"))
			with self.subTest(folder):
				self.assertIn("data.message(", source)
				self.assertNotIn("<br>", source)


class TestPrintAndExport(_Case):
	"""The grid print and the CSV and Excel export read each row's raw cell by fieldname. So a
	row must hold plain values under declared fieldnames, and no markup: the links and colors are
	added on screen by each report's formatter."""

	def check(self, columns, rows, extra=()):
		fieldnames = [c["fieldname"] for c in columns]
		self.assertEqual(len(fieldnames), len(set(fieldnames)))
		for column in columns:
			self.assertTrue(column["label"])
			self.assertIn(column["fieldtype"], FIELDTYPES)
			self.assertIsInstance(column["width"], int)
			if column["fieldtype"] == "Link":
				self.assertTrue(column["options"])
			if column["fieldtype"] == "Dynamic Link":
				self.assertIn(column["options"], fieldnames)
		self.assertTrue(rows)
		for row in rows:
			self.assertLessEqual(set(row) - set(extra), set(fieldnames))
			self.assertLessEqual(set(fieldnames), set(row))
			for key, value in row.items():
				self.assertIsInstance(value, PLAIN, key)
				if isinstance(value, str):
					self.assertNotIn("<", value, key)
		sheet = _export(columns, rows)
		self.assertEqual(len(sheet), len(rows) + 1)
		self.assertTrue(all(len(line) == len(columns) for line in sheet))
		return sheet

	def test_location_contents(self):
		STATE["items_at"] = {"Bin A-1 - SF": [_item("A", on_hand=5, assigned=True), _item("C", on_hand=1)]}
		self.patched.set(
			location_contents, "locations_with_anything", lambda item_code=None: {"Bin A-1 - SF"}
		)
		STATE["get_all"]["Putaway Rule"] = [{"item_code": "C", "warehouse": "Bin A-2 - SF", "disable": 0}]
		self.places()
		columns, rows, *_rest = location_contents.execute({})
		sheet = self.check(columns, rows, extra={"is_problem"})
		self.assertEqual(
			sheet[0],
			[
				"Location", "Under", "Kind", "Item", "Item Name", "Item Group", "On Hand", "On Order",
				"UOM", "Assigned Here", "Status", "Assigned To",
			],
		)  # fmt: skip
		self.assertEqual(
			sheet[1],
			[
				"Bin A-1 - SF", "Shelf A", "Shop bin", "C", "Charlie", "Pumps", 1.0, 0.0, "Unit", "No",
				"Here, assigned to another bin", "Bin A-2",
			],
		)  # fmt: skip

	def test_where_is_this_item(self):
		self.patched.set(
			where_item,
			"bin_rows",
			lambda codes: [
				{
					"item_code": "A",
					"warehouse": "Site J1 - SF",
					"actual_qty": 1,
					"ordered_qty": 0,
					"stock_value": 10,
				}
			],
		)
		self.patched.set(where_item, "last_movements", lambda codes, shelf=None: ({}, {}))
		self.patched.set(data, "shelf_stock_by_project", lambda *a, **k: [])
		self.places()
		columns, rows, *_rest = where_item.execute({"item_code": "A"})
		sheet = self.check(columns, rows)
		self.assertEqual(
			sheet[1][:8], ["A", "Alpha", "Job site", "Site J1 - SF", "Job Sites", "JOB-1", "First Job", 1.0]
		)

	def test_project_materials_both_views(self):
		self.patched.set(
			project_materials, "order_lines", lambda project: [_order("A", "PO-1", 2, received=2)]
		)
		self.patched.set(
			project_materials,
			"receipt_lines",
			lambda project: [_receipt("A", "PR-1", 2), _receipt("A", "PR-2", 1)],
		)
		self.patched.set(project_materials, "job_ledger", lambda project: [])
		self.patched.set(
			data,
			"shelf_stock_by_project",
			lambda *a, **k: [{"item_code": "A", "project": "JOB-1", "qty": 3.0, "value": 30.0}],
		)
		self.patched.set(data, "site_bins", lambda *a, **k: [])
		self.places()
		columns, rows, *_rest = project_materials.execute({"project": "JOB-1"})
		sheet = self.check(columns, rows)
		row = dict(zip(sheet[0], sheet[1], strict=True))
		# The document names are plain text in the cell: the script turns them into links.
		self.assertEqual(row["Purchase Orders"], "PO-1")
		self.assertEqual(row["Receipts"], "PR-1, PR-2")
		self.assertEqual((row["Ordered"], row["Received"], row["On Project Shelf"]), (2, 3, 3.0))
		columns, rows, *_rest = project_materials.execute({"include_finished": 1})
		sheet = self.check(columns, rows)
		self.assertEqual(sheet[1][:2], ["JOB-1", "First Job"])

	def test_job_site_contents(self):
		self.patched.set(
			data,
			"site_bins",
			lambda *a, **k: [
				{
					"project": "JOB-1",
					"warehouse": "Site J1 - SF",
					"item_code": "A",
					"qty": 3.0,
					"on_order": 0.0,
					"value": 30.0,
				}
			],
		)
		self.patched.set(
			job_site_contents,
			"site_ledger",
			lambda warehouses: {
				("A", "Site J1 - SF"): [{"posting_date": "2026-11-02", "qty_after_transaction": 3}]
			},
		)
		self.places()
		columns, rows, *_rest = job_site_contents.execute({})
		sheet = self.check(columns, rows)
		row = dict(zip(sheet[0], sheet[1], strict=True))
		self.assertEqual(
			(row["Project"], row["Job Site"], row["Item"], row["On Hand"]),
			("JOB-1", "Site J1 - SF", "A", 3.0),
		)
		self.assertEqual((row["Arrived"], row["Days There"]), (dt.date(2026, 11, 2), 10))

	def test_no_report_has_a_print_template_of_its_own(self):
		# Frappe's own grid print is used. A report .html is compiled whole, comments and all,
		# and a double brace in its prose becomes running code.
		for folder, _ref, _roles in REPORT_RECORDS.values():
			self.assertEqual(list((REPORTS / folder).glob("*.html")), [])


class TestHubs(unittest.TestCase):
	def setUp(self):
		_reset()

	def _card(self, workspace, label):
		links = workspace["links"]
		start = next(
			i for i, l in enumerate(links) if l.get("type") == "Card Break" and l.get("label") == label
		)
		members = []
		for link in links[start + 1 :]:
			if link.get("type") == "Card Break":
				break
			members.append(link)
		self.assertEqual(links[start]["link_count"], len(members))
		return members

	def test_the_inventory_hub_links_all_four_in_its_reports_card(self):
		ws = json.loads(INV_WS.read_text(encoding="utf-8"))
		members = {l["label"]: l for l in self._card(ws, "Reports")}
		for name in REPORT_RECORDS:
			with self.subTest(name):
				link = members[name]
				self.assertEqual(
					(link["link_type"], link["link_to"], link["is_query_report"]), ("Report", name, 1)
				)
		self.assertIn("Warehouse Transactions", members)
		self.assertGreaterEqual(ws["modified"], STAMP)

	def test_the_operations_hub_links_them_in_two_cards(self):
		hub = json.loads(OPS_HUB.read_text(encoding="utf-8"))
		inventory = {l["label"]: l for l in self._card(hub, "Inventory")}
		for name in ("Location Contents", "Where Is This Item", "Job Site Contents"):
			self.assertEqual((inventory[name]["link_type"], inventory[name]["link_to"]), ("Report", name))
		reports = {l["label"]: l for l in self._card(hub, "Reports")}
		self.assertEqual(reports["Project Materials"]["link_to"], "Project Materials")
		self.assertIn("Pending Items by Project", reports)
		self.assertGreaterEqual(hub["modified"], STAMP)

	def test_every_card_count_in_both_files_agrees_with_its_links(self):
		for path in (INV_WS, OPS_HUB):
			workspace = json.loads(path.read_text(encoding="utf-8"))
			for link in workspace["links"]:
				if link.get("type") == "Card Break":
					self._card(workspace, link["label"])

	def test_the_patch_is_registered_after_model_sync(self):
		lines = [line.strip() for line in PATCHES_TXT.read_text(encoding="utf-8").splitlines()]
		dotted = "erpnext_enhancements.patches.inventory_reports_hub_links"
		self.assertEqual(lines.count(dotted), 1)
		self.assertGreater(lines.index(dotted), lines.index("[post_model_sync]"))

	def test_the_patch_force_imports_both_workspaces(self):
		patch.execute()
		self.assertEqual([p for p, _, _ in STATE["imported"]], [INV_WS, OPS_HUB])
		self.assertTrue(all(force for _, force, _ in STATE["imported"]))
		self.assertFalse(any(reset for _, _, reset in STATE["imported"]))
		self.assertEqual(
			STATE["savepoints"], ["inv_reports_ws_inventory_enhancements", "inv_reports_ws_operations_hub"]
		)
		self.assertEqual(STATE["rollbacks"], [])
		self.assertEqual(STATE["logged"], [])
		self.assertEqual(STATE["cleared"], 1)
		self.assertFalse(frappe_stub.flags.in_import)

	def test_a_failed_import_rolls_back_to_its_savepoint(self):
		# import_doc deletes the stored row before insert(); without the rollback, migrate would
		# commit the delete and the workspace would be gone.
		STATE["fail_import"].add("inventory_enhancements")
		patch.execute()
		self.assertEqual(STATE["rollbacks"], ["inv_reports_ws_inventory_enhancements"])
		self.assertEqual([p for p, _, _ in STATE["imported"]], [OPS_HUB])
		self.assertEqual(len(STATE["logged"]), 1)
		self.assertEqual(STATE["cleared"], 1)
		self.assertFalse(frappe_stub.flags.in_import)

	def test_the_patch_never_raises_even_when_the_rollback_fails(self):
		STATE["fail_import"].update({"inventory_enhancements", "operations_hub"})
		STATE["fail_rollback"] = True
		patch.execute()
		self.assertEqual(len(STATE["rollbacks"]), 2)
		self.assertEqual(len(STATE["logged"]), 4)
		self.assertFalse(frappe_stub.flags.in_import)

	def test_the_patch_writes_no_business_data(self):
		source = _strip_py((APP / "patches" / "inventory_reports_hub_links.py").read_text(encoding="utf-8"))
		for word in ("set_value", "db.sql", "delete_doc", "insert(", ".save("):
			self.assertNotIn(word, source)

	def test_ci_runs_this_suite(self):
		self.assertIn(
			"python -m unittest erpnext_enhancements.tests.test_inventory_reports -v",
			CI.read_text(encoding="utf-8"),
		)


if __name__ == "__main__":
	unittest.main()
