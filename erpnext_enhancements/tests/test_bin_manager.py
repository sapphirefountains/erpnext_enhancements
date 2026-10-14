# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the Bin Manager Desk page (v1.693.0, ER-2026-1320545).

Two halves:

* **The server.** ``bin_assignments.create_and_assign`` (a new Item made through the store run's
  quick-item path and put on the bin, in one request) and ``bin_assignments.manager_boot`` (the
  page's bins and what the caller may do), run against the **real** ``api/stock_scan.py`` and
  ``bin_assignments.py`` on ``tests/test_bin_assignments.py``'s ``frappe`` stub, reused rather
  than copied: its Putaway Rule mimics ERPNext's ``validate`` and its writes check permission
  unless ``ignore_permissions`` is passed. What it pins: the Item is inserted as the user and
  every refusal comes before it; a resend posts nothing twice; the log row is an *Assign* with
  ``created_item`` and ``needs_review``; Undo takes the item off the bin, clears the Item Default
  the save set (only while it still names the bin) and leaves the Item; the boot lists leaf bins
  only, marks the boneyard, and says what the caller may do from their permissions, not a role
  list.
* **The page.** ``scripts/test_bin_manager.mjs`` runs the page's pure module and its controller
  (``public/js/bin_manager/``) under node against a model of the v16 router: picking a bin pushes
  one entry, Back returns to the picker and Forward to the bin, dialogs push none, a deep link's
  ``frappe.route_options`` is read (and the address cleaned), the make-home box's default, which
  actions show for which permissions, and that every action calls its endpoint with a
  ``client_ref``. The static checks below pin the page's files: the roles, the namespaced CSS,
  the bundle, and the hub links.

Run: python -m unittest erpnext_enhancements.tests.test_bin_manager -v
"""

import ast
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from erpnext_enhancements.inventory_enhancements import stock_scan_rules as rules
from erpnext_enhancements.tests import test_bin_assignments as base

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
PAGE_DIR = APP / "inventory_enhancements" / "page" / "bin_manager"
PAGE_JSON = PAGE_DIR / "bin_manager.json"
PAGE_JS = PAGE_DIR / "bin_manager.js"
PAGE_CSS = PAGE_DIR / "bin_manager.css"
BUNDLE = APP / "public" / "js" / "bin_manager.bundle.js"
LOGIC = APP / "public" / "js" / "bin_manager" / "logic.js"
CONTROLLER = APP / "public" / "js" / "bin_manager" / "controller.js"
HARNESS = REPO / "scripts" / "test_bin_manager.mjs"
OPS_HUB = APP / "enhancements_core" / "workspace" / "operations_hub" / "operations_hub.json"
INV_WS = (
	APP / "inventory_enhancements" / "workspace" / "inventory_enhancements" / "inventory_enhancements.json"
)
PATCH = APP / "patches" / "bin_manager_hub_links.py"
PATCHES_TXT = APP / "patches.txt"
MODULE = APP / "inventory_enhancements" / "bin_assignments.py"
API = APP / "api" / "stock_scan.py"

BIN_A, BIN_B, BONEYARD, STORES = base.BIN_A, base.BIN_B, base.BONEYARD, base.STORES
STATE = base.STATE

_ORIGINAL_INSERT = None
_ORIGINAL_SQL = None


def setUpModule():
	global _ORIGINAL_INSERT, _ORIGINAL_SQL
	base.setUpModule()
	_ORIGINAL_INSERT = base.Doc.insert

	def insert(self, ignore_permissions=False):
		# ERPNext names an Item by its code and gives each Item Default row a name of its own.
		if self.doctype == "Item":
			self.name = self.item_code
		_ORIGINAL_INSERT(self, ignore_permissions=ignore_permissions)
		if self.doctype == "Item":
			for idx, row in enumerate(self._data.get("item_defaults") or [], start=1):
				name = f"ID-{self.name}-{idx}"
				base._table("Item Default")[name] = {
					"name": name,
					"parent": self.name,
					"parenttype": "Item",
					"parentfield": "item_defaults",
					"idx": idx,
					**dict(row),
				}
		return self

	base.Doc.insert = insert

	_ORIGINAL_SQL = base.FRAPPE.db.sql

	def sql(query, values=None, as_dict=False, **kwargs):
		text = " ".join(query.split())
		if "coalesce(b.items, 0) as on_hand_items" in text:
			STATE["sql"].append(text)
			return _bin_rows_answer()
		return _ORIGINAL_SQL(query, values, as_dict=as_dict, **kwargs)

	base.FRAPPE.db.sql = sql


def tearDownModule():
	base.Doc.insert = _ORIGINAL_INSERT
	base.FRAPPE.db.sql = _ORIGINAL_SQL
	base.tearDownModule()


def _bin_rows_answer():
	"""What ``_bin_rows``'s SQL returns, worked out from the stub's tables by its own where clause."""
	out = []
	for row in base._table("Warehouse").values():
		if row.get("is_group") or row.get("disabled") or (row.get("warehouse_type") or "") == "Transit":
			continue
		on_hand = sum(
			1
			for b in base._table("Bin").values()
			if b["warehouse"] == row["name"] and float(b.get("actual_qty") or 0) > 0
		)
		assigned = len(
			{
				r["item_code"]
				for r in base._table("Putaway Rule").values()
				if r["warehouse"] == row["name"] and not r.get("disable")
			}
		)
		out.append(
			base._dict(
				name=row["name"],
				warehouse_name=row["warehouse_name"],
				company=row["company"],
				lft=row.get("lft"),
				rgt=row.get("rgt"),
				on_hand_items=on_hand,
				assigned_items=assigned,
			)
		)
	return out


def _function(path, name):
	return next(
		n
		for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
		if isinstance(n, ast.FunctionDef) and n.name == name
	)


def _seed_quick_item_lists():
	for group, is_group in (("Fittings", 0), ("Pumps", 0), ("All Item Groups", 1)):
		base._table("Item Group")[group] = {"name": group, "is_group": is_group}
	for uom in rules.QUICK_ITEM_UOMS:
		base._table("UOM")[uom] = {"name": uom, "enabled": 1}


def _spec(**values):
	spec = {
		"item_code": "PDT-0420",
		"item_name": "FITTING, ELBOW, 2 IN",
		"item_group": "Fittings",
		"stock_uom": "Unit",
	}
	spec.update(values)
	return spec


class _Base(base._Base):
	def setUp(self):
		super().setUp()
		_seed_quick_item_lists()

	def create(self, ref=1, warehouse=BIN_A, **spec):
		return base.bins.create_and_assign(warehouse, base._ref(ref), **_spec(**spec))


class TestCreateAndAssign(_Base):
	def test_creates_the_item_as_the_user_and_puts_it_on_the_bin(self):
		out = self.create()
		item = base._table("Item")["PDT-0420"]
		self.assertEqual(
			(item["item_name"], item["is_stock_item"], item["stock_uom"]), ("FITTING, ELBOW, 2 IN", 1, "Unit")
		)
		self.assertIn(("insert", "Item", False), STATE["writes"], "as the user, never past the framework")
		(rule,) = base._rules_for("PDT-0420", BIN_A)
		self.assertEqual(rule["disable"], 0)
		log = base._log(out["log"])
		self.assertEqual(
			(log["action"], log["warehouse"], log["item_code"], log["created_item"], log["needs_review"]),
			("Assign", BIN_A, "PDT-0420", 1, 1),
		)
		changes = json.loads(log["rule_changes"])
		self.assertEqual(changes[0]["kind"], "rule")
		self.assertEqual(changes[0]["created"], 1)
		self.assertEqual(
			changes[1:],
			[{"kind": "item_default_set", "row": "ID-PDT-0420-1", "item": "PDT-0420", "warehouse": BIN_A}],
		)
		(default,) = base._table("Item Default").values()
		self.assertEqual((default["company"], default["default_warehouse"]), (base.COMPANY, BIN_A))

	def test_a_resend_posts_nothing_twice(self):
		first = self.create()
		again = self.create()
		self.assertEqual(again, {"log": first["log"], "repeated": True, "undone": False})
		self.assertEqual(len(base._table("Item")), 2, "the seeded item and the one created")
		self.assertEqual(len(base._table("Stock Scan Log")), 1)

	def test_refusals_come_before_anything_is_written(self):
		base._item("PDT-0999")
		cases = (
			({"warehouse": "Inventory - SF"}, "group of locations", None),
			({"warehouse": "Goods In Transit - SF"}, "transit", None),
			({"warehouse": BONEYARD}, "is the boneyard", None),
			({"warehouse": "Nowhere - SF"}, "no location", None),
			({"item_code": "PDT-0999"}, "already in ERPNext", None),
			({"item_code": ""}, "part or model number", None),
			({"item_name": ""}, "name for the item", None),
			({"item_group": "All Item Groups"}, "Choose a group", None),
			({"stock_uom": "Box"}, "units offered", None),
		)
		for kwargs, words, exc in cases:
			with self.subTest(**kwargs):
				base._reset()
				_seed_quick_item_lists()
				base._item("PDT-0999")
				warehouse = kwargs.pop("warehouse", BIN_A)
				with self.assertRaises(exc or base.FRAPPE.ValidationError) as caught:
					self.create(warehouse=warehouse, **kwargs)
				self.assertIn(words, str(caught.exception))
				self.assertEqual(STATE["writes"], [])
				self.assertEqual(base._table("Stock Scan Log"), {})

	def test_a_concurrent_retry_is_still_being_recorded_not_item_exists(self):
		"""A retry sent while the first attempt is mid-request: its Item insert loses on the primary
		key with Frappe's "Item X already exists". It must read as the 409 _begin_log gives a
		duplicate log row, so the page keeps the same reference and the next tap finds the first
		save, never as a refusal that would make the page mint a new one."""
		original = base.scan._quick_item

		def lost_the_race(spec, location):
			raise base.FRAPPE.DuplicateEntryError(f"Item {spec['item_code']} already exists")

		base.scan._quick_item = lost_the_race
		try:
			with self.assertRaises(base.FRAPPE.DuplicateEntryError) as caught:
				self.create()
		finally:
			base.scan._quick_item = original
		self.assertEqual(
			str(caught.exception), "This save is still being recorded. Wait a moment, then try again."
		)
		self.assertEqual(base._table("Stock Scan Log"), {})
		self.assertEqual(base._table("Putaway Rule"), {})

	def test_only_the_item_insert_is_caught(self):
		"""The catch wraps _quick_item and nothing else: a duplicate anywhere else is not this race."""
		fn = next(
			n
			for n in ast.walk(ast.parse(MODULE.read_text(encoding="utf-8")))
			if isinstance(n, ast.FunctionDef) and n.name == "create_and_assign"
		)
		tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
		self.assertEqual(len(tries), 1)
		self.assertEqual(
			[ast.unparse(s) for s in tries[0].body], ["created = scan._quick_item(spec, location)"]
		)
		self.assertEqual([ast.unparse(h.type) for h in tries[0].handlers], ["frappe.DuplicateEntryError"])
		self.assertNotIn(
			"DuplicateEntryError", ast.unparse(_function(API, "store_run")), "store_run unchanged"
		)

	def test_without_item_create_it_is_refused_before_anything_is_written(self):
		STATE["perm"][("Item", "create")] = False
		with self.assertRaises(base.FRAPPE.PermissionError) as caught:
			self.create()
		self.assertIn("Item Manager", str(caught.exception))
		self.assertEqual((STATE["writes"], base._table("Stock Scan Log")), ([], {}))

	def test_outside_the_gate_it_is_refused_before_anything_is_written(self):
		for setup in (base._without_item_read, lambda: STATE.update(roles=["Item Manager", "Employee"])):
			with self.subTest(setup=setup):
				base._reset()
				_seed_quick_item_lists()
				setup()
				with self.assertRaises(base.FRAPPE.PermissionError):
					self.create()
				self.assertEqual(STATE["writes"], [])
				self.assertNotIn("PDT-0420", base._table("Item"))

	def test_undo_takes_it_off_the_bin_and_the_item_stays(self):
		out = self.create()
		base.scan.undo(out["log"])
		(rule,) = base._rules_for("PDT-0420", BIN_A)
		self.assertEqual(rule["disable"], 1)
		self.assertIsNone(base._table("Item Default")["ID-PDT-0420-1"]["default_warehouse"])
		self.assertIn("PDT-0420", base._table("Item"), "the Item stays")
		self.assertEqual(base._log(out["log"])["status"], "Undone")
		self.assertIn(("Item", "PDT-0420"), STATE["cache_cleared"])

	def test_undo_leaves_a_default_somebody_changed_since(self):
		out = self.create()
		base._table("Item Default")["ID-PDT-0420-1"]["default_warehouse"] = STORES
		base.scan.undo(out["log"])
		self.assertEqual(base._table("Item Default")["ID-PDT-0420-1"]["default_warehouse"], STORES)
		self.assertEqual(base._rules_for("PDT-0420", BIN_A)[0]["disable"], 1)

	def test_a_stock_user_with_item_create_creates_and_undoes(self):
		base._as_stock_user()
		out = self.create()
		self.assertEqual(base._rules_for("PDT-0420", BIN_A)[0]["disable"], 0)
		self.assertIn(("insert", "Putaway Rule", True), STATE["writes"])
		base.scan.undo(out["log"])
		self.assertEqual(base._rules_for("PDT-0420", BIN_A)[0]["disable"], 1)

	def test_the_messages_say_the_item_was_created_and_that_it_stays(self):
		out = self.create()
		doc = base.FRAPPE.get_doc("Stock Scan Log", out["log"])
		self.assertEqual(
			base.scan._bin_message(doc, "0 Unit", "FITTING, ELBOW, 2 IN"),
			"FITTING, ELBOW, 2 IN is now on Bin A1-1-1. New item PDT-0420 created.",
		)
		self.assertEqual(
			base.scan._bin_message(doc, "0 Unit", "FITTING, ELBOW, 2 IN", undone=True),
			"Undone: FITTING, ELBOW, 2 IN is no longer on Bin A1-1-1. The new item PDT-0420 stays in ERPNext.",
		)

	def test_a_plain_assign_says_nothing_about_a_new_item(self):
		out = base.bins.assign("PDT-0001", BIN_A, base._ref(7))
		doc = base.FRAPPE.get_doc("Stock Scan Log", out["log"])
		self.assertNotIn("New item", base.scan._bin_message(doc, "0 Unit", "PART"))
		self.assertNotIn("stays", base.scan._bin_message(doc, "0 Unit", "PART", undone=True))


class TestManagerBoot(_Base):
	def setUp(self):
		super().setUp()
		base._rule("PDT-0001", BIN_A)
		base._bin("PDT-0001", BIN_A, actual_qty=4)
		base._item("PDT-0002")
		base._bin("PDT-0002", BIN_A, actual_qty=0)
		base._bin("PDT-0002", BIN_B, actual_qty=2)
		base._warehouse("All Warehouses - SF", "All Warehouses", is_group=1, lft=0, rgt=100)

	def test_lists_every_usable_bin_with_its_counts_and_the_boneyard_marked(self):
		out = base.bins.manager_boot()
		names = [b["warehouse"] for b in out["bins"]]
		self.assertNotIn("Inventory - SF", names, "a group is not a bin")
		self.assertNotIn("Goods In Transit - SF", names, "nor is transit")
		by = {b["warehouse"]: b for b in out["bins"]}
		self.assertEqual((by[BIN_A]["on_hand_items"], by[BIN_A]["assigned_items"]), (1, 1))
		self.assertEqual((by[BIN_B]["on_hand_items"], by[BIN_B]["assigned_items"]), (1, 0))
		self.assertTrue(by[BONEYARD]["is_boneyard"])
		self.assertFalse(by[BIN_A]["is_boneyard"])
		self.assertEqual(by[BIN_A]["trail"], "Inventory", "the root is dropped from the trail")
		self.assertEqual(out["boneyard_warehouse"], BONEYARD)
		self.assertEqual(out["boneyard_name"], "Boneyard")
		self.assertEqual(names[:2], [BIN_A, BIN_B], "shelf-number order")

	def test_what_the_caller_may_do_comes_from_their_permissions(self):
		out = base.bins.manager_boot()
		self.assertEqual(
			(out["can_manage"], out["can_count"], out["can_move"], out["can_create_items"]),
			(True, True, True, True),
		)
		base._as_stock_user()
		STATE["perm"][("Item", "create")] = False
		out = base.bins.manager_boot()
		self.assertEqual(
			(out["can_manage"], out["can_count"], out["can_move"], out["can_create_items"]),
			(True, False, True, False),
		)
		base._without_item_read()
		self.assertFalse(base.bins.manager_boot()["can_manage"])

	def test_the_quick_item_lists_are_the_store_runs(self):
		out = base.bins.manager_boot()
		self.assertEqual(out["uoms"], list(rules.QUICK_ITEM_UOMS))
		self.assertEqual(out["undo_window_minutes"], 30)
		self.assertTrue(out["refuse_from"])

	def test_an_unset_or_disabled_boneyard_hides_it(self):
		STATE["settings"]["boneyard_warehouse"] = None
		out = base.bins.manager_boot()
		self.assertIsNone(out["boneyard_warehouse"])
		self.assertFalse(any(b["is_boneyard"] for b in out["bins"]))
		STATE["settings"]["boneyard_warehouse"] = BONEYARD
		base._table("Warehouse")[BONEYARD]["disabled"] = 1
		self.assertIsNone(base.bins.manager_boot()["boneyard_warehouse"])

	def test_outside_the_scan_roles_it_is_refused(self):
		STATE["roles"] = ["Employee"]
		with self.assertRaises(base.FRAPPE.PermissionError):
			base.bins.manager_boot()


# --------------------------------------------------------------------------- the page


def _code(path):
	"""Comments stripped: an absence assertion must not be satisfied by its own explanation."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestThePageUnderNode(unittest.TestCase):
	def test_the_page_harness_passes(self):
		result = subprocess.run(
			[shutil.which("node"), str(HARNESS)], capture_output=True, text=True, timeout=300, cwd=REPO
		)
		output = (result.stdout + result.stderr)[-6000:]
		self.assertEqual(result.returncode, 0, output)
		self.assertIn("checks passed", result.stdout, output)
		self.assertNotIn("FAIL", result.stdout, output)

	def test_the_page_script_parses(self):
		result = subprocess.run(
			[shutil.which("node"), "--check", str(PAGE_JS)], capture_output=True, text=True, timeout=60
		)
		self.assertEqual(result.returncode, 0, result.stderr)


class TestThePageFiles(unittest.TestCase):
	def test_the_page_is_for_the_people_who_use_stock_scan(self):
		page = json.loads(PAGE_JSON.read_text(encoding="utf-8"))
		self.assertEqual((page["name"], page["page_name"]), ("bin-manager", "bin-manager"))
		self.assertEqual(page["module"], "Inventory Enhancements")
		self.assertEqual(page["standard"], "Yes")
		self.assertEqual({r["role"] for r in page["roles"]}, set(rules.SCAN_ROLES))

	def test_no_workspace_has_the_pages_slug(self):
		for path in APP.glob("*/workspace/*/*.json"):
			name = json.loads(path.read_text(encoding="utf-8")).get("name") or ""
			with self.subTest(workspace=name):
				self.assertNotEqual(name.lower().replace(" ", "-"), "bin-manager")

	def test_every_css_selector_is_namespaced(self):
		css = re.sub(r"/\*[\s\S]*?\*/", "", PAGE_CSS.read_text(encoding="utf-8"))
		selectors = []
		for block in re.findall(r"([^{}]+)\{[^{}]*\}", re.sub(r"@media[^{]+\{", "", css)):
			selectors += [s.strip() for s in block.split(",") if s.strip()]
		self.assertTrue(selectors)
		for selector in selectors:
			with self.subTest(selector=selector):
				self.assertTrue(
					re.match(r"^(\[data-theme=\"dark\"\]\s+)?\.bm-", selector),
					f"{selector!r} is not namespaced",
				)

	def test_the_page_loads_bundles_never_a_raw_assets_path(self):
		code = _code(PAGE_JS)
		self.assertIn('"bin_manager.bundle.js"', code)
		self.assertNotIn("/assets/", code)
		self.assertIn("window.sf_bin_manager", BUNDLE.read_text(encoding="utf-8"))

	def test_the_page_never_reads_a_settings_single_or_calls_frappe_dialogs(self):
		code = _code(PAGE_JS) + _code(CONTROLLER) + _code(LOGIC)
		self.assertNotRegex(code, r"get_single_value|Inventory Scanner Settings|frappe\.db\.get_value")
		self.assertNotIn("frappe.call(", code, "every request goes through the quiet sf_kit call")
		self.assertNotIn("location.search", code, "route_options, never only the query string")

	def test_every_endpoint_the_page_names_exists_and_is_whitelisted(self):
		code = _code(LOGIC)
		sources = {
			"erpnext_enhancements.inventory_enhancements.bin_assignments.": (
				APP / "inventory_enhancements" / "bin_assignments.py"
			).read_text(encoding="utf-8"),
			"erpnext_enhancements.api.stock_scan.": (APP / "api" / "stock_scan.py").read_text(
				encoding="utf-8"
			),
		}
		found = re.findall(r'(BA|SCAN) \+ "(\w+)"', code)
		self.assertGreaterEqual(len(found), 11)
		for prefix, name in found:
			module = (
				"erpnext_enhancements.inventory_enhancements.bin_assignments."
				if prefix == "BA"
				else "erpnext_enhancements.api.stock_scan."
			)
			with self.subTest(endpoint=name):
				self.assertRegex(sources[module], rf"@frappe\.whitelist\([^)]*\)\ndef {name}\(")


class TestWhereToFindIt(unittest.TestCase):
	def _links(self, path):
		return json.loads(path.read_text(encoding="utf-8"))

	def test_the_operations_hub_links_it(self):
		hub = self._links(OPS_HUB)
		self.assertIn(
			{"label": "Bin Manager", "type": "URL", "url": "/desk/bin-manager"},
			[{k: s.get(k) for k in ("label", "type", "url")} for s in hub["shortcuts"]],
		)
		content = json.loads(hub["content"])
		self.assertIn("Bin Manager", [b["data"].get("shortcut_name") for b in content])
		self._in_card(hub, "Inventory")
		self.assertGreaterEqual(hub["modified"], "2026-10-14 13:00")

	def test_the_inventory_workspace_links_it(self):
		ws = self._links(INV_WS)
		self._in_card(ws, "Counts")
		self.assertGreaterEqual(ws["modified"], "2026-10-14 13:00")

	def _in_card(self, ws, card):
		links = ws["links"]
		start = next(i for i, l in enumerate(links) if l["type"] == "Card Break" and l["label"] == card)
		rest = links[start + 1 :]
		end = next((i for i, l in enumerate(rest) if l["type"] == "Card Break"), len(rest))
		members = rest[:end]
		self.assertIn(
			("Bin Manager", "bin-manager", "Page"),
			[(l["label"], l.get("link_to"), l.get("link_type")) for l in members],
		)
		self.assertEqual(links[start]["link_count"], len(members))

	def test_the_patch_lands_both_files_and_is_registered(self):
		text = PATCH.read_text(encoding="utf-8")
		self.assertIn('"operations_hub"', text)
		self.assertIn('"inventory_enhancements"', text)
		self.assertIn("force=True", text)
		lines = [line.strip() for line in PATCHES_TXT.read_text(encoding="utf-8").splitlines()]
		self.assertIn("erpnext_enhancements.patches.bin_manager_hub_links", lines)
		self.assertGreater(
			lines.index("erpnext_enhancements.patches.bin_manager_hub_links"),
			lines.index("[post_model_sync]"),
		)


if __name__ == "__main__":
	unittest.main()
