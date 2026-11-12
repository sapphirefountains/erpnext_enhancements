# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the Bin Manager's views beyond one bin (v1.806.1):
``inventory_enhancements/bin_views.py``.

The module runs for real on a small in-memory ``frappe`` stub of its own (so this suite has its
own CI step), with stand-ins for the two modules it leans on: ``api.stock_scan`` (the role gate
and two row reads; ``tests/test_bin_manager`` runs the real one) and ``job_sites`` (which sites
there are; ``tests/test_job_sites`` runs the real one).

What is fenced:

* every endpoint asks the role gate before it reads anything, and none of them writes;
* each bin gets its kind, a job site gets its job, and "unassigned stock" is never counted at a
  place nothing is ever assigned to (a job site, the boneyard, the Project Shelf);
* a customer is shown only to a caller who can read Projects, a value only to one who can read
  the Stock Ledger;
* the boot's extras never fail the page: a part that cannot be read is simply absent;
* By project: the shelf is this job's share (through ``project_stock.shelf_stock``, the one
  door), the site is everything on hand there, and the sentence that explains why nothing can be
  sent is the one the move itself would give;
* Problems: the four lists, each cut at the limit and saying so.

Run: python -m unittest erpnext_enhancements.tests.test_bin_views -v
"""

import ast
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

SOURCE = APP / "inventory_enhancements" / "bin_views.py"

SHELF = "Project Shelf - SF"
BONEYARD = "Boneyard - SF"
BIN_A = "Bin A1-1-1 - SF"
BIN_B = "Bin A1-1-2 - SF"
VAN = "Van 2 - SF"
SITE = "Job Site PRJ-90001 Water Wall - SF"
OLD_SITE = "Job Site PRJ-80000 Old Lobby - SF"
LOOSE_SITE = "Job Site - SF"  # the hand-made one: under the group, no job of its own
BUILD = "PRJ-90001"
DONE = "PRJ-80000"

_MODULES = (
	"frappe",
	"frappe.utils",
	"erpnext_enhancements.api",
	"erpnext_enhancements.api.stock_scan",
	"erpnext_enhancements.inventory_enhancements.job_sites",
	"erpnext_enhancements.inventory_enhancements.project_stock",
	"erpnext_enhancements.inventory_enhancements.bin_views",
)

STATE = {}
views = None
_ORIGINAL = {}


class _Row(dict):
	__getattr__ = dict.get


class _Throw(Exception):
	pass


class _Denied(Exception):
	pass


def _reset():
	STATE.clear()
	STATE.update(
		{
			"warehouses": {
				"All Warehouses - SF": ("All Warehouses", None, 1),
				"Inventory - SF": ("Inventory", "All Warehouses - SF", 1),
				"Vehicles - SF": ("Vehicles", "All Warehouses - SF", 1),
				"Job Sites - SF": ("Job Sites", "All Warehouses - SF", 1),
				BIN_A: ("Bin A1-1-1", "Inventory - SF", 0),
				BIN_B: ("Bin A1-1-2", "Inventory - SF", 0),
				BONEYARD: ("Boneyard", "Inventory - SF", 0),
				SHELF: ("Project Shelf", "All Warehouses - SF", 0),
				VAN: ("Van 2", "Vehicles - SF", 0),
				SITE: ("Job Site PRJ-90001 Water Wall", "Job Sites - SF", 0),
				OLD_SITE: ("Job Site PRJ-80000 Old Lobby", "Job Sites - SF", 0),
				LOOSE_SITE: ("Job Site", "Job Sites - SF", 0),
			},
			"disabled": set(),
			"items": {
				"ELBOW": ("FITTING, ELBOW", "Unit", 1, 0),
				"PUMP": ("PUMP", "Each", 1, 0),
				"VALVE": ("VALVE", "Unit", 1, 0),
				"LABOR": ("Install labor", "Hour", 0, 0),
			},
			"bins": [],  # (item_code, warehouse, qty, value)
			"rules": [],  # (item_code, warehouse, disable)
			"ledger": [],  # (project, item_code, warehouse, qty)
			"projects": {
				BUILD: ("Water Wall", "Active", "Build", "Acme Hotels"),
				DONE: ("Old Lobby", "Completed", "Build", "Bolt Bank"),
			},
			"site_map": {SITE: BUILD, OLD_SITE: DONE},
			"sites_ready": True,
			"sites_group": True,
			"settings": {"project_shelf_warehouse": SHELF, "boneyard_warehouse": BONEYARD},
			"perms": {("Project", "read"), ("Stock Ledger Entry", "read")},
			"access": True,
			"calls": [],
			"writes": [],
		}
	)


def _stock_item(code):
	item = STATE["items"].get(code)
	return bool(item and item[2] and not item[3])


def _on_hand(code, warehouse):
	return sum(q for i, w, q, _v in STATE["bins"] if i == code and w == warehouse)


def _sql(query, values=None, as_dict=False):
	text = " ".join(query.split())
	STATE["calls"].append(("sql", text[:60]))
	values = values or {}
	if "as missing_items" in text:  # _counts
		out = []
		for name, (_label, _parent, is_group) in STATE["warehouses"].items():
			if is_group or name in STATE["disabled"]:
				continue
			assigned = {i for i, w, d in STATE["rules"] if w == name and not d and _stock_item(i)}
			missing = sum(1 for i in assigned if _on_hand(i, name) <= 0)
			loose = sum(
				1 for i, w, q, _v in STATE["bins"] if w == name and q > 0 and _stock_item(i) and i not in assigned
			)
			value = sum(v for _i, w, q, v in STATE["bins"] if w == name and q > 0)
			out.append(_Row(name=name, missing_items=missing, unassigned_items=loose, stock_value=value))
		return out
	if text.startswith("select b.item_code, i.item_name, i.stock_uom, b.actual_qty, b.stock_value"):
		return [
			_Row(item_code=i, item_name=STATE["items"][i][0], stock_uom=STATE["items"][i][1], actual_qty=q, stock_value=v)
			for i, w, q, v in STATE["bins"]
			if w == values["warehouse"] and q > 0
		]
	if text.startswith("select sle.project, sle.item_code, sum(sle.actual_qty)"):
		sums = {}
		for project, item, warehouse, qty in STATE["ledger"]:
			if warehouse == values["shelf"] and project:
				sums[(project, item)] = sums.get((project, item), 0) + qty
		return [_Row(project=p, item_code=i, qty=q) for (p, i), q in sums.items() if q > 0]
	if text.startswith("select warehouse, count(*) as item_count from `tabBin`"):
		return [
			_Row(warehouse=w, item_count=sum(1 for _i, w2, q, _v in STATE["bins"] if w2 == w and q > 0))
			for w in values["warehouses"]
		]
	if text.startswith("select distinct pr.warehouse, pr.item_code"):
		return [
			_Row(warehouse=w, item_code=i, item_name=STATE["items"][i][0], stock_uom=STATE["items"][i][1])
			for i, w, d in STATE["rules"]
			if not d and _stock_item(i) and _on_hand(i, w) <= 0
		]
	if text.startswith("select b.warehouse, b.item_code, i.item_name, i.stock_uom, b.actual_qty"):
		assigned = {(i, w) for i, w, d in STATE["rules"] if not d}
		return [
			_Row(warehouse=w, item_code=i, item_name=STATE["items"][i][0], stock_uom=STATE["items"][i][1], actual_qty=q)
			for i, w, q, _v in STATE["bins"]
			if q > 0 and _stock_item(i) and (i, w) not in assigned
		]
	raise AssertionError(f"unexpected query: {text[:120]}")


def _get_all(doctype, filters=None, fields=None, order_by=None, **kwargs):
	STATE["calls"].append(("get_all", doctype))
	filters = filters or {}
	if doctype == "Warehouse":
		names = filters.get("name", [None, None])[1] if "name" in filters else None
		rows = []
		for name, (label, parent, is_group) in STATE["warehouses"].items():
			if names is not None and name not in names:
				continue
			if "is_group" in filters and bool(is_group) != bool(filters["is_group"]):
				continue
			if "disabled" in filters and (name in STATE["disabled"]) != bool(filters["disabled"]):
				continue
			rows.append(
				_Row(
					name=name,
					warehouse_name=label,
					parent_warehouse=parent,
					is_group=is_group,
					disabled=int(name in STATE["disabled"]),
					warehouse_type="",
					creation=list(STATE["warehouses"]).index(name),
				)
			)
		return rows
	if doctype == "Project":
		wanted = set(filters["name"][1])
		rows = []
		for name, (label, status, kind, customer) in STATE["projects"].items():
			if name in wanted:
				row = _Row(name=name, project_name=label, status=status, project_type=kind)
				if "customer" in fields:
					row["customer"] = customer
				rows.append(row)
		return rows
	if doctype == "Bin":
		rows = STATE["bins"]
		if "warehouse" in filters:
			wanted = filters["warehouse"]
			wanted = set(wanted[1]) if isinstance(wanted, list) else {wanted}
			rows = [r for r in rows if r[1] in wanted]
		if "item_code" in filters:
			rows = [r for r in rows if r[0] == filters["item_code"]]
		return [_Row(item_code=i, warehouse=w, actual_qty=q) for i, w, q, _v in rows if q > 0]
	if doctype == "Putaway Rule":
		return [_Row(warehouse=w) for i, w, d in STATE["rules"] if i == filters["item_code"] and not d]
	if doctype == "Item":
		wanted = set(filters["name"][1])
		return [
			_Row(name=code, item_name=v[0], stock_uom=v[1], is_stock_item=v[2])
			for code, v in STATE["items"].items()
			if code in wanted
		]
	if doctype == "Employee":
		return [_Row(custom_default_vehicle_warehouse="Vehicles - SF")]
	raise AssertionError(f"unexpected read of {doctype}")


def _install():
	frappe = types.ModuleType("frappe")

	def throw(message, exc=None):
		raise (exc or _Throw)(message)

	def get_value(doctype, name, fields, as_dict=False):
		assert doctype == "Project", doctype
		row = STATE["projects"].get(name)
		if not row:
			return None
		return _Row(name=name, project_name=row[0], status=row[1], project_type=row[2], customer=row[3])

	class _Settings:
		def get(self, fieldname):
			return STATE["settings"].get(fieldname)

	frappe.db = types.SimpleNamespace(sql=_sql, get_value=get_value, has_column=lambda doctype, column: True)
	frappe.get_all = _get_all
	frappe.get_cached_doc = lambda doctype: _Settings()
	frappe.has_permission = lambda doctype, ptype="read": (doctype, ptype) in STATE["perms"]
	frappe.throw = throw
	frappe._ = lambda text: text
	frappe.whitelist = lambda *a, **kw: (lambda fn: fn)
	frappe.PermissionError = _Denied
	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.cstr = lambda v: "" if v is None else str(v)
	utils.flt = lambda v, precision=None: float(v or 0)
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	# api.stock_scan: the gate and two reads.
	api = types.ModuleType("erpnext_enhancements.api")
	api.__path__ = []
	scan = types.ModuleType("erpnext_enhancements.api.stock_scan")

	def _check_access():
		STATE["calls"].append(("gate",))
		if not STATE["access"]:
			raise _Denied("You are not permitted to use Stock Scan.")

	def _item(code):
		code = str(code or "").strip()
		item = STATE["items"].get(code)
		return _Row(name=code, item_name=item[0], stock_uom=item[1], is_stock_item=item[2]) if item else None

	def _warehouse(name):
		row = STATE["warehouses"].get(name)
		if not row:
			return None
		return _Row(name=name, warehouse_name=row[0], is_group=row[2], disabled=int(name in STATE["disabled"]))

	scan._check_access, scan._item, scan._warehouse = _check_access, _item, _warehouse
	api.stock_scan = scan
	sys.modules["erpnext_enhancements.api"] = api
	sys.modules["erpnext_enhancements.api.stock_scan"] = scan

	sites = types.ModuleType("erpnext_enhancements.inventory_enhancements.job_sites")

	def site_map():
		if not STATE["sites_ready"]:
			raise RuntimeError("the Warehouse field is not there yet")
		return dict(STATE["site_map"])

	def site_warehouses():
		names = set(site_map())
		names.update(n for n, (_l, parent, _g) in STATE["warehouses"].items() if parent == "Job Sites - SF")
		return names

	sites.site_map = site_map
	sites.site_warehouses = site_warehouses
	sites.ready = lambda: STATE["sites_ready"]
	sites.enabled = lambda: bool(STATE["sites_ready"] and STATE["sites_group"])
	sites.group = lambda: _Row(name="Job Sites - SF") if STATE["sites_group"] else None
	sites.allowed_types = lambda: ("Build",)
	sys.modules["erpnext_enhancements.inventory_enhancements.job_sites"] = sites
	import erpnext_enhancements.inventory_enhancements as package

	_ORIGINAL["job_sites_attr"] = getattr(package, "job_sites", None)
	package.job_sites = sites


def setUpModule():
	global views
	for name in _MODULES:
		_ORIGINAL[name] = sys.modules.pop(name, None)
	_reset()
	_install()
	import importlib

	views = importlib.import_module("erpnext_enhancements.inventory_enhancements.bin_views")


def tearDownModule():
	import erpnext_enhancements
	import erpnext_enhancements.inventory_enhancements as package

	for attr in ("job_sites", "project_stock", "bin_views"):
		if hasattr(package, attr):
			delattr(package, attr)
	if _ORIGINAL.get("job_sites_attr") is not None:
		package.job_sites = _ORIGINAL["job_sites_attr"]
	if hasattr(erpnext_enhancements, "api") and _ORIGINAL.get("erpnext_enhancements.api") is None:
		delattr(erpnext_enhancements, "api")
	for name in _MODULES:
		sys.modules.pop(name, None)
		if _ORIGINAL.get(name) is not None:
			sys.modules[name] = _ORIGINAL[name]


def _boot_bins():
	"""What ``manager_boot`` hands ``decorate``: one dict per usable leaf."""
	return [
		{
			"warehouse": name,
			"warehouse_name": label,
			"on_hand_items": 0,
			"assigned_items": 0,
			"is_boneyard": name == BONEYARD,
		}
		for name, (label, _parent, is_group) in STATE["warehouses"].items()
		if not is_group
	]


class _Base(unittest.TestCase):
	def setUp(self):
		_reset()


class TestDecorate(_Base):
	def test_each_bin_gets_its_kind(self):
		bins = _boot_bins()
		extras = views.decorate(bins)
		kinds = {b["warehouse"]: b["kind"] for b in bins}
		self.assertEqual(
			kinds,
			{
				BIN_A: "shop_bin",
				BIN_B: "shop_bin",
				BONEYARD: "boneyard",
				SHELF: "project_shelf",
				VAN: "vehicle",  # under the group an Employee's vehicle field names
				SITE: "job_site",
				OLD_SITE: "job_site",
				LOOSE_SITE: "job_site",  # under the Job Sites group, with no job of its own
			},
		)
		self.assertEqual(extras["shelf_warehouse"], SHELF)
		self.assertEqual(extras["shelf_name"], "Project Shelf")
		self.assertTrue(extras["job_sites"])

	def test_a_job_site_carries_its_job(self):
		by = {b["warehouse"]: b for b in (lambda bins: (views.decorate(bins), bins)[1])(_boot_bins())}
		self.assertEqual(
			(by[SITE]["project"], by[SITE]["project_name"], by[SITE]["customer"], by[SITE]["project_closed"]),
			(BUILD, "Water Wall", "Acme Hotels", False),
		)
		self.assertTrue(by[OLD_SITE]["project_closed"])
		self.assertNotIn("project", by[LOOSE_SITE])
		self.assertNotIn("project", by[BIN_A])

	def test_a_customer_is_only_for_those_who_can_read_projects(self):
		STATE["perms"].discard(("Project", "read"))
		bins = _boot_bins()
		views.decorate(bins)
		site = next(b for b in bins if b["warehouse"] == SITE)
		self.assertEqual(site["project_name"], "Water Wall")
		self.assertIsNone(site["customer"])

	def test_the_counts_behind_the_quick_filters(self):
		STATE["rules"] = [("ELBOW", BIN_A, 0), ("PUMP", BIN_A, 0), ("VALVE", BIN_A, 1)]
		STATE["bins"] = [
			("ELBOW", BIN_A, 4, 10.0),  # assigned and here
			("VALVE", BIN_A, 2, 6.0),  # its rule is disabled: stock with no assignment
			("PUMP", SITE, 1, 250.0),
			("ELBOW", SHELF, 9, 22.5),
			("PUMP", BONEYARD, 1, 0.01),
		]
		bins = _boot_bins()
		extras = views.decorate(bins)
		by = {b["warehouse"]: b for b in bins}
		self.assertEqual((by[BIN_A]["missing_items"], by[BIN_A]["unassigned_items"]), (1, 1))
		self.assertEqual(by[BIN_A]["stock_value"], 16.0)
		# Nothing is ever assigned to a job site, the boneyard or the shared shelf: stock there is
		# not "unassigned".
		for place in (SITE, SHELF, BONEYARD):
			self.assertEqual(by[place]["unassigned_items"], 0, place)
		self.assertEqual(by[SITE]["stock_value"], 250.0)
		self.assertTrue(extras["can_see_value"])

	def test_a_value_is_only_for_those_who_can_read_the_stock_ledger(self):
		STATE["perms"].discard(("Stock Ledger Entry", "read"))
		STATE["bins"] = [("PUMP", SITE, 1, 250.0)]
		bins = _boot_bins()
		extras = views.decorate(bins)
		self.assertFalse(extras["can_see_value"])
		self.assertTrue(all("stock_value" not in b for b in bins))

	def test_a_site_with_nothing_set_up_still_gets_plain_bins(self):
		"""No Project Shelf field, no job-site field: every place is a shop bin (or the boneyard),
		and nothing raises."""
		STATE["settings"] = {"boneyard_warehouse": BONEYARD}
		STATE["sites_ready"] = False
		bins = _boot_bins()
		extras = views.decorate(bins)
		by = {b["warehouse"]: b["kind"] for b in bins}
		self.assertEqual(by[SHELF], "shop_bin")
		self.assertEqual(by[SITE], "shop_bin")
		self.assertEqual(by[BONEYARD], "boneyard")
		self.assertEqual(by[VAN], "vehicle")
		self.assertEqual((extras["shelf_warehouse"], extras["job_sites"]), (None, False))

	def test_a_shelf_setting_that_names_no_usable_bin_is_no_shelf(self):
		STATE["settings"]["project_shelf_warehouse"] = "Gone - SF"
		extras = views.decorate(_boot_bins())
		self.assertEqual((extras["shelf_warehouse"], extras["shelf_name"]), (None, None))


class TestItemLocations(_Base):
	def test_where_an_item_is_and_where_it_is_assigned(self):
		STATE["bins"] = [("ELBOW", BIN_A, 4, 1), ("ELBOW", SITE, 2, 1), ("PUMP", BIN_B, 1, 1)]
		STATE["rules"] = [("ELBOW", BIN_A, 0), ("ELBOW", BIN_B, 0), ("ELBOW", VAN, 1)]
		out = views.item_locations(" ELBOW ")
		self.assertEqual((out["item_code"], out["item_name"], out["is_stock_item"]), ("ELBOW", "FITTING, ELBOW", True))
		self.assertEqual(
			out["locations"],
			{
				BIN_A: {"on_hand": 4.0, "assigned": True},
				SITE: {"on_hand": 2.0, "assigned": False},
				BIN_B: {"on_hand": 0.0, "assigned": True},
			},
		)

	def test_an_unknown_item_is_refused_and_a_non_stock_one_says_so(self):
		with self.assertRaises(_Throw):
			views.item_locations("GHOST")
		out = views.item_locations("LABOR")
		self.assertEqual((out["is_stock_item"], out["locations"]), (False, {}))


class TestProjectView(_Base):
	def setUp(self):
		super().setUp()
		STATE["ledger"] = [(BUILD, "ELBOW", SHELF, 10), (BUILD, "ELBOW", SHELF, -4), ("PRJ-OTHER", "ELBOW", SHELF, 5)]
		STATE["bins"] = [("ELBOW", SHELF, 11, 5.5), ("PUMP", SITE, 2, 500.0), ("ELBOW", SITE, 4, 2.0)]
		# project_stock.shelf_stock reads the ledger through its own query; answer it here.
		stock = sys.modules["erpnext_enhancements.inventory_enhancements.project_stock"]
		self._ledger_rows = stock.ledger_rows
		stock.ledger_rows = lambda project: [
			{"item_code": i, "warehouse": w, "kind": "move", "qty": q} for p, i, w, q in STATE["ledger"] if p == project
		]

	def tearDown(self):
		sys.modules["erpnext_enhancements.inventory_enhancements.project_stock"].ledger_rows = self._ledger_rows

	def test_the_shelf_is_the_jobs_share_and_the_site_is_everything_there(self):
		out = views.project_view(BUILD)
		self.assertEqual(out["project"]["project_name"], "Water Wall")
		self.assertEqual(out["project"]["customer"], "Acme Hotels")
		self.assertFalse(out["project"]["closed"])
		self.assertEqual(out["shelf"]["warehouse"], SHELF)
		# 6 of the shelf's 11 elbows are this job's.
		self.assertEqual(
			out["shelf"]["items"],
			[{"item_code": "ELBOW", "item_name": "FITTING, ELBOW", "stock_uom": "Unit", "qty": 6}],
		)
		self.assertEqual(out["site"]["warehouse"], SITE)
		self.assertEqual(
			[(r["item_code"], r["qty"], r["value"]) for r in out["site"]["items"]],
			[("ELBOW", 4.0, 2.0), ("PUMP", 2.0, 500.0)],
		)
		self.assertIsNone(out["send_refusal"])

	def test_the_shelf_numbers_come_through_the_one_door(self):
		stock = sys.modules["erpnext_enhancements.inventory_enhancements.project_stock"]
		original = stock.shelf_stock
		stock.shelf_stock = lambda project: [{"item_code": "FROM-THE-ADAPTER", "qty": 1}]
		try:
			self.assertEqual(views.project_view(BUILD)["shelf"]["items"][0]["item_code"], "FROM-THE-ADAPTER")
		finally:
			stock.shelf_stock = original

	def test_a_job_with_no_site_yet_can_still_be_sent_to_when_it_is_a_build(self):
		STATE["site_map"] = {}
		out = views.project_view(BUILD)
		self.assertIsNone(out["site"])
		self.assertIsNone(out["send_refusal"])

	def test_why_nothing_can_be_sent_is_said_in_a_sentence(self):
		finished = views.project_view(DONE)
		self.assertTrue(finished["project"]["closed"])
		self.assertIn("Old Lobby is Completed", finished["send_refusal"])
		STATE["projects"]["PRJ-70000"] = ("Fountain service", "Active", "Service", "Acme Hotels")
		self.assertIn("Job sites are kept for Build jobs", views.project_view("PRJ-70000")["send_refusal"])
		STATE["site_map"] = {}
		STATE["sites_group"] = False
		self.assertIn("not set up yet", views.project_view(BUILD)["send_refusal"])
		STATE["sites_ready"] = False
		self.assertIn("being set up", views.project_view(BUILD)["send_refusal"])

	def test_no_shelf_set_is_no_shelf_and_values_and_customers_are_gated(self):
		STATE["settings"] = {}
		STATE["perms"].clear()
		out = views.project_view(BUILD)
		self.assertIsNone(out["shelf"])
		self.assertIsNone(out["project"]["customer"])
		self.assertTrue(all("value" not in r for r in out["site"]["items"]))

	def test_an_unknown_job_is_refused(self):
		for name in ("PRJ-NOPE", "", None):
			with self.assertRaises(_Throw):
				views.project_view(name)

	def test_the_index_lists_jobs_with_material_somewhere(self):
		out = views.project_index()
		rows = {r["project"]: r for r in out}
		self.assertEqual(
			(rows[BUILD]["shelf_items"], rows[BUILD]["site_items"], rows[BUILD]["site"]), (1, 2, SITE)
		)
		self.assertEqual((rows[DONE]["shelf_items"], rows[DONE]["site_items"], rows[DONE]["closed"]), (0, 0, True))
		# A job with shelf stock and no site, known only by its id.
		self.assertEqual((rows["PRJ-OTHER"]["shelf_items"], rows["PRJ-OTHER"]["site"]), (1, None))
		# Jobs still in progress first.
		self.assertEqual(out[-1]["project"], DONE)


class TestProblems(_Base):
	def test_the_four_lists(self):
		STATE["rules"] = [("ELBOW", BIN_A, 0), ("PUMP", BIN_A, 0), ("LABOR", BIN_B, 0)]
		STATE["bins"] = [
			("ELBOW", BIN_A, 4, 1),
			("VALVE", BIN_B, 3, 1),  # no assignment at a shop bin
			("VALVE", VAN, 1, 1),  # nor on the van
			("PUMP", SITE, 2, 1),  # a live job's site: not a problem
			("PUMP", OLD_SITE, 1, 1),  # a finished job's site
			("PUMP", BONEYARD, 5, 1),  # the boneyard: never "unassigned"
			("ELBOW", SHELF, 9, 1),
			("VALVE", LOOSE_SITE, 2, 1),  # a site with no job: not a shop bin either
		]
		STATE["ledger"] = [(DONE, "ELBOW", SHELF, 3), (BUILD, "ELBOW", SHELF, 6)]
		out = views.problems()
		self.assertEqual(
			[(r["warehouse"], r["item_code"]) for r in out["assigned_empty"]["rows"]], [(BIN_A, "PUMP")]
		)
		self.assertEqual(
			[(r["warehouse"], r["item_code"], r["qty"]) for r in out["unassigned_stock"]["rows"]],
			[(BIN_B, "VALVE", 3.0), (VAN, "VALVE", 1.0)],
		)
		self.assertEqual(
			[(r["project"], r["item_code"], r["qty"], r["status"]) for r in out["finished_shelf"]["rows"]],
			[(DONE, "ELBOW", 3.0, "Completed")],
		)
		self.assertEqual(
			[(r["project"], r["warehouse"], r["item_code"], r["qty"]) for r in out["finished_site"]["rows"]],
			[(DONE, OLD_SITE, "PUMP", 1.0)],
		)
		self.assertEqual(out["assigned_empty"]["total"], 1)
		self.assertFalse(out["assigned_empty"]["truncated"])

	def test_a_disabled_or_missing_bin_is_not_listed(self):
		STATE["rules"] = [("ELBOW", BIN_A, 0), ("ELBOW", "Gone - SF", 0)]
		STATE["disabled"].add(BIN_A)
		self.assertEqual(views.problems()["assigned_empty"]["rows"], [])

	def test_a_long_list_is_cut_and_says_so(self):
		for n in range(views.PROBLEM_LIMIT + 5):
			code = f"ITEM-{n:04d}"
			STATE["items"][code] = (code, "Unit", 1, 0)
			STATE["rules"].append((code, BIN_A, 0))
		out = views.problems()["assigned_empty"]
		self.assertEqual((len(out["rows"]), out["total"], out["truncated"]), (views.PROBLEM_LIMIT, views.PROBLEM_LIMIT + 5, True))
		self.assertEqual(out["rows"][0]["item_code"], "ITEM-0000")

	def test_nothing_set_up_means_two_empty_lists_not_an_error(self):
		STATE["settings"] = {}
		STATE["sites_ready"] = False
		out = views.problems()
		self.assertEqual((out["finished_shelf"]["rows"], out["finished_site"]["rows"]), ([], []))


class TestTheGate(_Base):
	def test_every_endpoint_asks_the_role_gate_first(self):
		STATE["access"] = False
		for call in (
			lambda: views.item_locations("ELBOW"),
			lambda: views.project_view(BUILD),
			views.project_index,
			views.problems,
		):
			STATE["calls"].clear()
			with self.assertRaises(_Denied):
				call()
			self.assertEqual(STATE["calls"], [("gate",)], "the gate comes before any read")

	def test_the_module_only_reads(self):
		"""No insert, save, set_value, delete or commit anywhere in it, and every endpoint is a GET."""
		tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
		banned = {"insert", "save", "submit", "cancel", "set_value", "delete_doc", "commit", "db_set", "new_doc", "get_doc"}
		used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
		self.assertEqual(used & banned, set())
		endpoints = [
			n
			for n in tree.body
			if isinstance(n, ast.FunctionDef) and any("whitelist" in ast.unparse(d) for d in n.decorator_list)
		]
		self.assertEqual(
			sorted(n.name for n in endpoints), ["item_locations", "problems", "project_index", "project_view"]
		)
		for node in endpoints:
			self.assertIn("methods=['GET']", ast.unparse(node.decorator_list[0]), node.name)
			self.assertIn("scan._check_access()", ast.unparse(node.body[1]), node.name)

	def test_no_sql_function_is_written_as_a_get_all_field(self):
		"""Frappe 16 refuses ``count(...)`` as a string field; every aggregate here is ``db.sql``."""
		tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("get_all"):
				for kw in node.keywords:
					if kw.arg == "fields":
						self.assertNotRegex(ast.unparse(kw.value), r"(count|sum|max|min)\(", ast.unparse(node)[:80])


if __name__ == "__main__":
	unittest.main()
