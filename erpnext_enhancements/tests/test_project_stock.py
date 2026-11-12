# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the Procurement Tracker's "where it is now" lookup and its wiring
(v1.806.0).

Three layers:

* ``inventory_enhancements/project_stock.py`` runs for real on a small in-memory ``frappe`` stub
  of its own (so this suite has its own CI step): the lookup is a fixed handful of queries
  however many lines the project has, a site with no Project Shelf setting simply has no shelf,
  a failing settings read or job-site read never raises, and the Project Shelf adapter answers
  from the Project Shelf's own module when it is installed (``project_shelf.shelf_stock``, cut to
  what the shelf holds) and by the same rule from the ledger when it is not. The job's Supplier
  Returns are read the same way: a module that is not there yet means no returns on record.
* ``project_enhancements/__init__.py`` cannot be imported without a bench (it pulls in the email
  and print modules), so its new functions are lifted out of the source and run against
  stand-ins: the overview is gated on Project read before anything is read, and a where-now
  lookup that fails leaves the feed standing and says so.
* The source itself: the feed's two ``SELECT``s still name the same columns in the same order
  (a ``UNION`` that drifts fails only on a bench, and this repo has none in CI), the tracker
  script dials an endpoint that exists and loads its logic bundle, and the assistant tool offers
  the by-item view.

The rules themselves (``project_stock_rules``, ``procurement_overview``) are in
``test_procurement_overview.py``.

Run: python -m unittest erpnext_enhancements.tests.test_project_stock -v
"""

import ast
import re
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

FEED = APP / "project_enhancements" / "__init__.py"
SCRIPT = APP / "public" / "js" / "project_enhancements.js"
BUNDLE = APP / "public" / "js" / "procurement_tracker.bundle.js"
LOGIC = APP / "public" / "js" / "procurement_tracker" / "logic.js"
TOOL = APP / "assistant_tools" / "project_procurement_status.py"

PROJECT = "PRJ-90001"
SHELF = "Project Shelf - SF"
SITE = "Job Site PRJ-90001 Water Wall - SF"
OTHER_SITE = "Job Site PRJ-90002 Lobby - SF"
BIN = "Bin A1-2-3 - SF"

_MODULES = (
	"frappe",
	"erpnext_enhancements.inventory_enhancements.project_stock",
	"erpnext_enhancements.inventory_enhancements.job_sites",
	"erpnext_enhancements.inventory_enhancements.project_shelf",
	"erpnext_enhancements.inventory_enhancements.supplier_returns",
)

STATE = {}
stock = None
_ORIGINAL = {}


class _Row(dict):
	__getattr__ = dict.get


def _reset():
	STATE.clear()
	STATE.update(
		{
			"ledger": [],  # what the one SQL query returns
			"bins": [],  # (item_code, warehouse, qty)
			"items": {},  # code -> (item_name, stock_uom, is_stock_item)
			"settings": {"project_shelf_warehouse": SHELF, "boneyard_warehouse": "Boneyard - SF"},
			"settings_error": False,
			"site_map": {SITE: PROJECT, OTHER_SITE: "PRJ-90002"},
			"sites_error": False,
			"sql": [],
			"get_all": [],
		}
	)


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")

	def sql(query, values=None, as_dict=False):
		STATE["sql"].append((query, values))
		if "group by sle.project, sle.item_code" in " ".join(query.split()):
			# The shelf fallback: each job's balance at the shelf, by item. This suite's ledger rows
			# are one job's unless a row says otherwise.
			sums = {}
			for r in STATE["ledger"]:
				project = r.get("project") or PROJECT
				if r["warehouse"] == values["shelf"] and values.get("project") in (None, project):
					sums[(project, r["item_code"])] = sums.get((project, r["item_code"]), 0) + r["qty"]
			return [_Row(project=p, item_code=i, qty=q) for (p, i), q in sums.items() if q > 0]
		return [_Row(r) for r in STATE["ledger"]]

	def get_all(doctype, filters=None, fields=None, **kwargs):
		STATE["get_all"].append(doctype)
		if doctype == "Bin":
			wanted = set(filters["warehouse"][1])
			return [
				_Row(item_code=i, warehouse=w, actual_qty=q)
				for i, w, q in STATE["bins"]
				if w in wanted and q > 0
			]
		if doctype == "Warehouse":
			return [_Row(name=n, warehouse_name=n.replace(" - SF", "")) for n in filters["name"][1]]
		if doctype == "Item":
			return [
				_Row(name=code, item_name=v[0], stock_uom=v[1], is_stock_item=v[2])
				for code, v in STATE["items"].items()
				if code in set(filters["name"][1])
			]
		if doctype == "Employee":
			return [_Row(custom_default_vehicle_warehouse="Van 2 - SF")]
		raise AssertionError(f"unexpected read of {doctype}")

	class _Settings:
		def get(self, fieldname):
			return STATE["settings"].get(fieldname)

	def get_cached_doc(doctype):
		assert doctype == "Inventory Scanner Settings", doctype
		if STATE["settings_error"]:
			raise RuntimeError("no such Single yet")
		return _Settings()

	frappe.db = types.SimpleNamespace(sql=sql, has_column=lambda doctype, column: True)
	frappe.get_all = get_all
	frappe.get_cached_doc = get_cached_doc
	frappe._ = lambda text: text
	sys.modules["frappe"] = frappe

	# job_sites reads the Warehouse field and a setting of its own; its two answers are all this
	# module needs from it, and test_job_sites runs the real thing.
	sites = types.ModuleType("erpnext_enhancements.inventory_enhancements.job_sites")

	def site_map():
		if STATE["sites_error"]:
			raise RuntimeError("the field is not there yet")
		return dict(STATE["site_map"])

	sites.site_map = site_map
	sites.site_warehouses = lambda: set(STATE["site_map"])
	sys.modules["erpnext_enhancements.inventory_enhancements.job_sites"] = sites
	import erpnext_enhancements.inventory_enhancements as package

	_ORIGINAL["job_sites_attr"] = getattr(package, "job_sites", None)
	package.job_sites = sites


def setUpModule():
	global stock
	for name in _MODULES:
		_ORIGINAL[name] = sys.modules.pop(name, None)
	_reset()
	_install_frappe_stub()
	import importlib

	stock = importlib.import_module("erpnext_enhancements.inventory_enhancements.project_stock")


def _install_module(name, **functions):
	"""Stand in for a sibling release's module: importable from the package, with these functions."""
	import erpnext_enhancements.inventory_enhancements as package

	module = types.ModuleType(f"erpnext_enhancements.inventory_enhancements.{name}")
	for key, fn in functions.items():
		setattr(module, key, fn)
	sys.modules[module.__name__] = module
	setattr(package, name, module)
	return module


def _remove_module(name):
	import erpnext_enhancements.inventory_enhancements as package

	sys.modules.pop(f"erpnext_enhancements.inventory_enhancements.{name}", None)
	if hasattr(package, name):
		delattr(package, name)


def tearDownModule():
	import erpnext_enhancements.inventory_enhancements as package

	_remove_module("project_shelf")
	_remove_module("supplier_returns")
	if _ORIGINAL.get("job_sites_attr") is None:
		if hasattr(package, "job_sites"):
			delattr(package, "job_sites")
	else:
		package.job_sites = _ORIGINAL["job_sites_attr"]
	for name in _MODULES:
		sys.modules.pop(name, None)
		if _ORIGINAL.get(name) is not None:
			sys.modules[name] = _ORIGINAL[name]


def _ledger(item_code, warehouse, qty, voucher_type="Purchase Receipt", purpose="", is_return=0):
	return {
		"item_code": item_code,
		"warehouse": warehouse,
		"voucher_type": voucher_type,
		"purpose": purpose,
		"is_return": is_return,
		"qty": qty,
	}


class TestPlacesFor(unittest.TestCase):
	def setUp(self):
		_reset()
		STATE["items"] = {
			"ELBOW": ("FITTING, ELBOW", "Unit", 1),
			"PUMP": ("PUMP", "Unit", 1),
			"LABOR": ("Install labor", "Hour", 0),
		}

	def test_a_job_split_across_shelf_site_and_a_take(self):
		STATE["ledger"] = [
			_ledger("ELBOW", SHELF, 10),
			_ledger("ELBOW", SHELF, -4, "Stock Entry", "Material Transfer"),
			_ledger("ELBOW", SITE, 4, "Stock Entry", "Material Transfer"),
			_ledger("ELBOW", SITE, -1, "Stock Entry", "Material Issue"),
		]
		STATE["bins"] = [("ELBOW", SHELF, 6), ("ELBOW", SITE, 3)]
		out = stock.places_for(PROJECT, ["ELBOW", "LABOR"])
		self.assertEqual(out["shelf"], SHELF)
		self.assertEqual(out["sites"], [SITE])
		elbow = out["items"]["ELBOW"]
		self.assertTrue(elbow["is_stock_item"])
		self.assertEqual(
			[(p["kind"], p["qty"]) for p in elbow["places"]],
			[("project_shelf", 6), ("job_site", 3), ("taken", 1)],
		)
		self.assertEqual(elbow["places"][1]["label"], "Job site")
		# A non-stock item is known, and has no places: the caller says "not tracked".
		self.assertEqual(out["items"]["LABOR"], {
			"is_stock_item": False, "item_name": "Install labor", "stock_uom": "Hour", "places": [],
			"supplier_returns": [],
		})

	def test_the_lookup_is_set_based_whatever_the_number_of_lines(self):
		"""One ledger query, one Bin read, one Warehouse read, one Item read: for 3 items or 300."""
		codes = [f"ITEM-{n}" for n in range(300)]
		STATE["items"] = {code: (code, "Unit", 1) for code in codes}
		STATE["ledger"] = [_ledger(code, SHELF, 1) for code in codes]
		STATE["bins"] = [(code, SHELF, 1) for code in codes]
		out = stock.places_for(PROJECT, codes)
		self.assertEqual(len(out["items"]), 300)
		self.assertEqual(len(STATE["sql"]), 1)
		self.assertEqual(sorted(STATE["get_all"]), ["Bin", "Item", "Warehouse"])
		query, values = STATE["sql"][0]
		self.assertEqual(values, {"project": PROJECT})
		self.assertIn("sle.is_cancelled = 0", query)
		# Bound, never formatted in.
		self.assertIn("%(project)s", query)
		self.assertNotIn(PROJECT, query)

	def test_a_part_taken_for_the_job_that_was_never_bought_for_it_is_included(self):
		STATE["ledger"] = [_ledger("PUMP", BIN, -1, "Stock Entry", "Material Issue")]
		out = stock.places_for(PROJECT, ["ELBOW"])
		self.assertEqual([(p["kind"], p["qty"]) for p in out["items"]["PUMP"]["places"]], [("taken", 1)])
		self.assertEqual(out["items"]["ELBOW"]["places"], [])

	def test_stock_sent_to_the_site_from_the_shop_lists_without_a_ledger_row_of_its_own(self):
		STATE["bins"] = [("PUMP", SITE, 2)]
		out = stock.places_for(PROJECT, [])
		self.assertEqual([(p["kind"], p["qty"]) for p in out["items"]["PUMP"]["places"]], [("job_site", 2)])

	def test_another_jobs_site_is_not_this_jobs(self):
		STATE["ledger"] = [_ledger("PUMP", OTHER_SITE, 2, "Stock Entry", "Material Transfer")]
		STATE["bins"] = [("PUMP", OTHER_SITE, 2)]
		place = stock.places_for(PROJECT, ["PUMP"])["items"]["PUMP"]["places"][0]
		self.assertEqual(place["kind"], "other_site")

	def test_a_site_without_the_shelf_setting_has_no_shelf(self):
		"""The field arrives with another release. Until then the old Stores is a named place."""
		STATE["settings"] = {}
		STATE["ledger"] = [_ledger("ELBOW", "Stores - SF", 5)]
		STATE["bins"] = [("ELBOW", "Stores - SF", 5)]
		out = stock.places_for(PROJECT, ["ELBOW"])
		self.assertIsNone(out["shelf"])
		place = out["items"]["ELBOW"]["places"][0]
		self.assertEqual((place["kind"], place["label"], place["text"]), ("shop_bin", "Stores", "5 in Stores"))

	def test_settings_and_job_sites_that_cannot_be_read_never_raise(self):
		STATE["settings_error"] = True
		STATE["sites_error"] = True
		STATE["ledger"] = [_ledger("ELBOW", SHELF, 5)]
		STATE["bins"] = [("ELBOW", SHELF, 5)]
		self.assertIsNone(stock.shelf_warehouse())
		out = stock.places_for(PROJECT, ["ELBOW"])
		self.assertEqual(out["sites"], [])
		self.assertEqual(out["items"]["ELBOW"]["places"][0]["kind"], "shop_bin")

	def test_no_project_reads_nothing(self):
		self.assertEqual(stock.places_for("", ["ELBOW"]), {"items": {}, "shelf": None, "sites": []})
		self.assertEqual(STATE["sql"], [])

	def test_an_item_erpnext_does_not_know_is_absent(self):
		out = stock.places_for(PROJECT, ["GHOST"])
		self.assertNotIn("GHOST", out["items"])


class TestShelfStock(unittest.TestCase):
	def setUp(self):
		_reset()
		STATE["items"] = {"ELBOW": ("FITTING, ELBOW", "Unit", 1), "PUMP": ("PUMP", "Each", 1)}

	def test_what_is_on_the_shelf_for_the_job(self):
		STATE["ledger"] = [
			_ledger("PUMP", SHELF, 2),
			_ledger("ELBOW", SHELF, 10),
			_ledger("ELBOW", SHELF, -4, "Stock Entry", "Material Transfer"),
			_ledger("ELBOW", SITE, 4, "Stock Entry", "Material Transfer"),
		]
		# The shelf holds 20 elbows: other jobs' too. This job's share is what its ledger says.
		STATE["bins"] = [("ELBOW", SHELF, 20), ("PUMP", SHELF, 1)]
		self.assertEqual(
			stock.shelf_stock(PROJECT),
			[
				{"item_code": "ELBOW", "item_name": "FITTING, ELBOW", "stock_uom": "Unit", "qty": 6, "recorded": 6},
				# Two were received for the job and the shelf holds one: one can be handed over.
				{"item_code": "PUMP", "item_name": "PUMP", "stock_uom": "Each", "qty": 1, "recorded": 2},
			],
		)
		self.assertEqual(stock.shelf_by_project(), {PROJECT: {"ELBOW": 6, "PUMP": 1}})

	def test_no_shelf_or_no_project_is_an_empty_shelf(self):
		STATE["ledger"] = [_ledger("PUMP", SHELF, 2)]
		STATE["bins"] = [("PUMP", SHELF, 2)]
		self.assertEqual(stock.shelf_stock(""), [])
		STATE["settings"] = {}
		self.assertEqual(stock.shelf_stock(PROJECT), [])

	def test_vehicles_come_from_the_employee_field(self):
		self.assertEqual(stock.vehicles(), {"Van 2 - SF"})


class TestTheProjectShelfModule(unittest.TestCase):
	"""The adapter, once ``inventory_enhancements/project_shelf.py`` is installed (settled
	2026-11-12: ``warehouse()`` and ``shelf_stock(project=None, item_code=None)``)."""

	def setUp(self):
		_reset()
		self.asked = []

		def shelf_stock(project=None, item_code=None):
			self.asked.append(project)
			rows = [
				{"project": PROJECT, "item_code": "ELBOW", "item_name": "FITTING, ELBOW", "stock_uom": "Unit", "qty": 6.0, "qty_in": 10.0, "qty_out": 4.0},
				{"project": PROJECT, "item_code": "PUMP", "item_name": "PUMP", "stock_uom": "Each", "qty": 2.0},
				{"project": PROJECT, "item_code": "VALVE", "item_name": "VALVE", "stock_uom": "Unit", "qty": -1.0},
				{"project": "PRJ-90002", "item_code": "ELBOW", "item_name": "FITTING, ELBOW", "stock_uom": "Unit", "qty": 3.0},
			]
			if project:
				rows = [r for r in rows if r["project"] == project]
			return {
				"warehouse": "Shelf From The Module - SF",
				"rows": rows,
				# More pumps are tagged than the shelf holds; the shelf has none of one item at all.
				"untagged": [{"item_code": "PUMP", "on_hand": 1.0, "tagged": 2.0, "qty": -1.0}],
				"exact": False,
			}

		_install_module("project_shelf", warehouse=lambda: "Shelf From The Module - SF", shelf_stock=shelf_stock)

	def tearDown(self):
		_remove_module("project_shelf")

	def test_the_shelf_is_the_modules_answer(self):
		STATE["settings"] = {}  # the adapter does not read the setting itself once the module is here
		self.assertEqual(stock.shelf_warehouse(), "Shelf From The Module - SF")

	def test_a_jobs_share_comes_from_the_module_cut_to_what_the_shelf_holds(self):
		self.assertEqual(
			stock.shelf_stock(PROJECT),
			[
				{"item_code": "ELBOW", "item_name": "FITTING, ELBOW", "stock_uom": "Unit", "qty": 6.0, "recorded": 6.0},
				{"item_code": "PUMP", "item_name": "PUMP", "stock_uom": "Each", "qty": 1.0, "recorded": 2.0},
			],
		)
		self.assertEqual(self.asked, [PROJECT], "asked for this job, once")
		self.assertEqual(STATE["sql"], [], "and the ledger is not read a second way")

	def test_every_jobs_share(self):
		self.assertEqual(
			stock.shelf_by_project(), {PROJECT: {"ELBOW": 6.0, "PUMP": 1.0}, "PRJ-90002": {"ELBOW": 3.0}}
		)
		self.assertEqual(self.asked, [None])

	def test_a_module_that_fails_is_an_empty_shelf_not_an_error(self):
		def broken(project=None, item_code=None):
			raise RuntimeError("the shelf could not be read")

		_install_module("project_shelf", warehouse=broken, shelf_stock=broken)
		self.assertIsNone(stock.shelf_warehouse())
		self.assertEqual(stock.shelf_stock(PROJECT), [])
		self.assertEqual(stock.shelf_by_project(), {})


class TestSupplierReturns(unittest.TestCase):
	"""The tracker's "returned to the supplier", once ``supplier_returns.returned_lines`` is
	installed (settled 2026-11-12)."""

	def setUp(self):
		_reset()
		STATE["items"] = {"ELBOW": ("FITTING, ELBOW", "Unit", 1), "LABOR": ("Install labor", "Hour", 0)}
		self.asked = []
		self.lines = [
			{"supplier_return": "SRET-0001", "status": "Sent Back", "item_code": "ELBOW", "qty": 1, "stock_qty": 4, "gone": True, "open": True},
			{"supplier_return": "SRET-0002", "status": "Requested", "item_code": "ELBOW", "qty": 2, "stock_qty": 2, "gone": False, "open": True},
			{"supplier_return": "SRET-0003", "status": "Credit Received", "item_code": "LABOR", "qty": 1, "stock_qty": 1, "gone": True, "open": False},
		]

		def returned_lines(purchase_order_items=None, project=None):
			self.asked.append((purchase_order_items, project))
			return list(self.lines)

		_install_module("supplier_returns", returned_lines=returned_lines)

	def tearDown(self):
		_remove_module("supplier_returns")

	def test_what_went_back_is_a_place_and_each_return_is_named(self):
		STATE["ledger"] = [_ledger("ELBOW", SHELF, 10)]
		STATE["bins"] = [("ELBOW", SHELF, 6)]
		out = stock.places_for(PROJECT, ["ELBOW"])
		self.assertEqual(self.asked, [(None, PROJECT)])
		elbow = out["items"]["ELBOW"]
		# Only the return whose goods have left is a place; the one being arranged is still here.
		self.assertEqual([(p["kind"], p["qty"]) for p in elbow["places"]], [("project_shelf", 6), ("returned", 4)])
		self.assertEqual(
			elbow["supplier_returns"],
			[
				{"supplier_return": "SRET-0001", "status": "Sent Back", "qty": 4, "gone": True, "open": True},
				{"supplier_return": "SRET-0002", "status": "Requested", "qty": 2, "gone": False, "open": True},
			],
		)

	def test_the_ledgers_return_and_the_supplier_returns_are_the_same_goods_never_added(self):
		STATE["ledger"] = [_ledger("ELBOW", SHELF, 10), _ledger("ELBOW", SHELF, -4, "Purchase Receipt", "", 1)]
		STATE["bins"] = [("ELBOW", SHELF, 6)]
		places = stock.places_for(PROJECT, ["ELBOW"])["items"]["ELBOW"]["places"]
		self.assertEqual([(p["kind"], p["qty"]) for p in places], [("project_shelf", 6), ("returned", 4)])
		# A return made in the Desk with no Supplier Return record is still in the ledger.
		self.lines = []
		places = stock.places_for(PROJECT, ["ELBOW"])["items"]["ELBOW"]["places"]
		self.assertEqual([(p["kind"], p["qty"]) for p in places], [("project_shelf", 6), ("returned", 4)])

	def test_a_non_stock_items_return_is_on_record_though_its_place_is_not(self):
		out = stock.places_for(PROJECT, [])
		labor = out["items"]["LABOR"]
		self.assertEqual(labor["places"], [])
		self.assertEqual(labor["supplier_returns"][0]["supplier_return"], "SRET-0003")

	def test_no_module_or_a_failing_one_means_no_returns_on_record(self):
		def broken(purchase_order_items=None, project=None):
			raise RuntimeError("the returns could not be read")

		_install_module("supplier_returns", returned_lines=broken)
		STATE["ledger"] = [_ledger("ELBOW", SHELF, 5)]
		STATE["bins"] = [("ELBOW", SHELF, 5)]
		self.assertEqual(stock.places_for(PROJECT, ["ELBOW"])["items"]["ELBOW"]["supplier_returns"], [])
		_remove_module("supplier_returns")
		self.assertEqual(stock.places_for(PROJECT, ["ELBOW"])["items"]["ELBOW"]["supplier_returns"], [])

	def test_both_modules_are_imported_where_they_are_used_never_at_the_top(self):
		"""Either release can merge first: an import at module scope would take the tracker down
		on a site that has one and not the other."""
		source = (APP / "inventory_enhancements" / "project_stock.py").read_text(encoding="utf-8")
		tree = ast.parse(source)
		top = [ast.unparse(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
		self.assertFalse(any("project_shelf" in line or "supplier_returns" in line for line in top), top)
		for name in ("project_shelf", "supplier_returns"):
			self.assertRegex(source, rf"try:\n\t\tfrom erpnext_enhancements\.inventory_enhancements import {name}\n\texcept ImportError:")


# ---------------------------------------------------------------------------
# The feed's new functions, lifted out of a module that needs a bench to import
# ---------------------------------------------------------------------------


def _lift(*names):
	"""The named top-level functions of the feed module, compiled into a namespace of stand-ins."""
	tree = ast.parse(FEED.read_text(encoding="utf-8"))
	wanted = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
	assert {n.name for n in wanted} == set(names), names
	for node in wanted:
		node.decorator_list = []
	module = ast.Module(body=wanted, type_ignores=[])
	namespace = {}
	exec(compile(ast.fix_missing_locations(module), str(FEED), "exec"), namespace)
	return namespace


class TestTheFeedsNewFunctions(unittest.TestCase):
	def setUp(self):
		_reset()
		self.calls = []
		self.logged = []
		self.ns = _lift("_attach_overview", "get_procurement_overview")
		calls, logged = self.calls, self.logged

		class _Overview:
			@staticmethod
			def attach(rows, found, today):
				calls.append(("attach", found, today))
				return [{"item_code": "A"}]

			@staticmethod
			def filter_options(rows, items):
				return {"suppliers": []}

		self.ns.update(
			{
				"procurement_overview": _Overview,
				"_today": lambda: "2026-11-12",
				"frappe": types.SimpleNamespace(
					log_error=lambda *a, **kw: logged.append(a),
					get_traceback=lambda: "traceback",
				),
				"require_project_read": lambda project: calls.append(("gate", project)),
				"_documents_and_overview": lambda project: (
					calls.append(("read", project)) or ([{"doctype": "Purchase Order"}], [{}], [{"item_code": "A"}], True)
				),
			}
		)

	def test_the_overview_is_gated_on_project_read_before_anything_is_read(self):
		out = self.ns["get_procurement_overview"](PROJECT)
		self.assertEqual(self.calls, [("gate", PROJECT), ("read", PROJECT)])
		self.assertEqual(sorted(out), ["filters", "groups", "items", "located", "today"])
		self.assertTrue(out["located"])

	def test_no_project_reads_nothing(self):
		out = self.ns["get_procurement_overview"]("")
		self.assertEqual(self.calls, [])
		self.assertEqual((out["groups"], out["items"]), ([], []))

	def test_a_failed_lookup_leaves_the_feed_standing_and_says_so(self):
		STATE["ledger"] = None  # the stub's sql() raises on this
		items, located = self.ns["_attach_overview"](PROJECT, [{"item_code": "A"}])
		self.assertFalse(located)
		self.assertEqual(items, [{"item_code": "A"}])
		# The states are still attached, with no where-now to go on.
		self.assertEqual(self.calls, [("attach", None, "2026-11-12")])
		self.assertEqual(len(self.logged), 1)

	def test_a_working_lookup_hands_the_items_to_attach(self):
		STATE["items"] = {"A": ("A", "Unit", 1)}
		STATE["ledger"] = [_ledger("A", SHELF, 2)]
		STATE["bins"] = [("A", SHELF, 2)]
		_items, located = self.ns["_attach_overview"](PROJECT, [{"item_code": "A"}, {"item_code": None}])
		self.assertTrue(located)
		found = self.calls[0][1]
		self.assertEqual(found["A"]["places"][0]["text"], "2 on the Project Shelf")
		self.assertEqual(self.logged, [])


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------


def _select_lists(sql):
	"""The column names of each SELECT of the feed's UNION, in order."""
	sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
	inner = sql[sql.index("FROM (") + len("FROM (") : sql.rindex(") as combined_results")]
	out = []
	for part in inner.split("UNION ALL"):
		body = part[part.index("SELECT") + len("SELECT") : re.search(r"\n\s*FROM\s*\n", part).start()]
		columns, depth, current = [], 0, ""
		for char in body:
			if char == "(":
				depth += 1
			elif char == ")":
				depth -= 1
			if char == "," and depth == 0:
				columns.append(current)
				current = ""
			else:
				current += char
		columns.append(current)
		names = []
		for column in columns:
			column = " ".join(column.split())
			alias = re.search(r"\bas (\w+)$", column, flags=re.IGNORECASE)
			names.append(alias.group(1) if alias else column.rsplit(".", 1)[-1])
		out.append(names)
	return out


class TestTheSource(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.feed = FEED.read_text(encoding="utf-8")
		cls.tree = ast.parse(cls.feed)
		cls.script = SCRIPT.read_text(encoding="utf-8")

	def _function(self, name):
		return next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == name)

	def test_both_selects_of_the_union_name_the_same_columns_in_the_same_order(self):
		"""A UNION pairs columns by position. A column added to one SELECT and not the other
		fails only when MariaDB runs it, and nothing in CI does."""
		chain = self._function("_chain_rows")
		sql = next(
			n.value
			for n in ast.walk(chain)
			if isinstance(n, ast.Constant) and isinstance(n.value, str) and "UNION ALL" in n.value
		)
		first, second = _select_lists(sql)
		self.assertEqual(first, second)
		for column in ("supplier", "expected_date", "po_conversion_factor", "warehouse", "po_item_name"):
			self.assertIn(column, first)

	def test_the_public_feeds_keep_their_names_and_their_gate(self):
		for name in ("get_procurement_status", "get_procurement_documents", "get_procurement_overview"):
			fn = self._function(name)
			self.assertTrue(
				any("whitelist" in ast.unparse(d) for d in fn.decorator_list), f"{name} is not whitelisted"
			)
			self.assertIn("require_project_read(project_name)", ast.unparse(fn), name)

	def test_the_keys_the_assistant_tool_sums_are_still_emitted(self):
		"""Adding keys is safe; renaming these breaks the tool in silence."""
		for key in ("requested_qty", "ordered_qty", "received_qty", "order_status", "receive_status"):
			self.assertIn(f'"{key}"', (APP / "procurement_quantities.py").read_text(encoding="utf-8"))
		chain = ast.unparse(self._function("_chain_rows"))  # unparse writes single quotes
		for key in ("warehouse", "po_line_qty", "row_id", "mr_status"):
			self.assertIn(f"'{key}': ", chain)

	def test_the_tracker_dials_the_overview_and_loads_its_logic(self):
		self.assertIn('"erpnext_enhancements.project_enhancements.get_procurement_overview"', self.script)
		self.assertIn('frappe.require("procurement_tracker.bundle.js"', self.script)
		self.assertTrue(BUNDLE.is_file() and LOGIC.is_file())
		self.assertIn("window.sf_procurement_tracker", BUNDLE.read_text(encoding="utf-8"))

	def test_the_tracker_never_renders_data_as_markup(self):
		"""Every v-html in the template goes through hl(), which escapes each piece."""
		for match in re.finditer(r'v-html="([^"]*)"', self.script):
			self.assertRegex(match.group(1), r"^hl\(", match.group(0))

	def test_the_tracker_writes_the_address_without_a_history_entry(self):
		code = re.sub(r"//[^\n]*", "", self.script)
		self.assertIn("window.history.replaceState(", code)
		self.assertNotIn("pushState(", code)

	def test_the_assistant_tool_offers_the_by_item_view(self):
		tool = TOOL.read_text(encoding="utf-8")
		self.assertIn('"enum": ["stage_summary", "documents", "items"]', tool)
		self.assertIn("get_procurement_overview(project)", tool)


if __name__ == "__main__":
	unittest.main()
