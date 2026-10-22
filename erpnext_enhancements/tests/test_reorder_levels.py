# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for reorder levels on the two audit screens (v1.720.0).

Three parts:

* **The endpoints** (``api/reorder_levels.py``) against a ``frappe`` stub installed in
  ``setUpModule``: an in-memory Item with an ``Item Reorder`` child table, whose ``save`` checks
  write permission and refuses what ERPNext's ``Item.validate`` refuses (a level with no quantity, a
  second row for one warehouse). What it pins: the role gate; the rule an item shows (its Purchase
  rule on the screen's warehouse, else its own rule wherever it is, labelled, else none); that a
  write checks Item permission **before anything else**, refuses a stale page by ``modified`` and
  writes nothing, creates the row for an item with none (as a Purchase rule, on a bin that can hold
  stock), edits the rule on the warehouse it sits on, saves **nothing** for numbers that are
  already those, and lets ERPNext's own refusal through.
* **The source**, read as text: no SQL, no ``ignore_permissions`` and no direct child-table write in
  the endpoint module, because the write must go through ``Item.save()`` so ERPNext validates; the
  editor's files never touch history; both pages and the bundle are wired as the module says.
* **The editor** (``public/js/reorder_levels/``) under node: ``scripts/test_reorder_levels.mjs``.

Run: python -m unittest erpnext_enhancements.tests.test_reorder_levels -v
"""

import ast
import copy
import datetime
import json
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

from erpnext_enhancements.inventory_enhancements import stock_scan_rules as rules

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
API_FILE = APP / "api" / "reorder_levels.py"
JS_DIR = APP / "public" / "js" / "reorder_levels"
BUNDLE_JS = APP / "public" / "js" / "reorder_levels.bundle.js"
BUNDLE_CSS = APP / "public" / "css" / "reorder_levels.bundle.css"
BIN_PAGE = APP / "inventory_enhancements" / "page" / "bin_manager" / "bin_manager.js"
ISA_PAGE = APP / "inventory_enhancements" / "page" / "inventory_scanner_audit" / "inventory_scanner_audit.js"
HARNESS = REPO_ROOT / "scripts" / "test_reorder_levels.mjs"

BIN = "Bin A1-1-1 - SF"
BIN_B = "Bin A1-1-2 - SF"
ROOM = "Inventory Room - SF"

api = None
FRAPPE = None
STATE = None


class Row(dict):
	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value

	def set(self, key, value):
		self[key] = value

	def as_dict(self):
		return dict(self)


class Doc:
	def __init__(self, doctype, data):
		object.__setattr__(self, "doctype", doctype)
		object.__setattr__(self, "_d", copy.deepcopy(data))
		self._d["reorder_levels"] = [Row(r) for r in self._d.get("reorder_levels") or []]

	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		return self._d.get(key)

	def __setattr__(self, key, value):
		self._d[key] = value

	def get(self, key, default=None):
		return self._d.get(key, default)

	def append(self, table, data):
		row = Row(data)
		self._d.setdefault(table, []).append(row)
		return row

	def remove(self, row):
		for table, rows in self._d.items():
			if isinstance(rows, list) and any(r is row for r in rows):
				self._d[table] = [r for r in rows if r is not row]
				return
		raise ValueError("not a child row of this document")

	def check_permission(self, ptype="read"):
		if not FRAPPE.has_permission(self.doctype, ptype, doc=self):
			raise FRAPPE.PermissionError(f"No permission to {ptype} {self.doctype}")

	def save(self):
		self.check_permission("write")
		if STATE["save_error"]:
			raise STATE["save_error"]
		seen = set()
		for index, row in enumerate(self._d["reorder_levels"], start=1):
			# Item.validate_warehouse_for_reorder
			if row.get("warehouse") in seen:
				raise FRAPPE.ValidationError(
					f"Row {index}: An Reorder entry already exists for this warehouse"
				)
			seen.add(row.get("warehouse"))
			if row.get("warehouse_reorder_level") and not row.get("warehouse_reorder_qty"):
				raise FRAPPE.ValidationError(f"Row #{index}: Please set reorder quantity")
		STATE["clock"] += 1
		self._d["modified"] = datetime.datetime(2026, 10, 22, 9, 0, STATE["clock"], 123456)
		STATE["saves"].append(self._d["name"])
		STATE["items"][self._d["name"]] = copy.deepcopy(self._d)


def _matches(record, filters):
	for key, rule in (filters or {}).items():
		value = record.get(key)
		if isinstance(rule, list) and rule[0] == "in":
			if value not in rule[1]:
				return False
		elif value != rule:
			return False
	return True


SHELF_A = "Shelf A - SF"
INVENTORY = "Inventory - SF"
ALL = "All Warehouses - SF"
# The group each seeded rule checks stock in, as on production: bins under the Rows -> Inventory,
# the Inventory Room -> All Warehouses.
GROUP_OF = {BIN: INVENTORY, BIN_B: INVENTORY, ROOM: ALL}


def _item_record(code, rules=None, modified=None, uom="Unit"):
	return {
		"name": code,
		"stock_uom": uom,
		"modified": modified or datetime.datetime(2026, 10, 22, 8, 0, 0, 123456),
		"reorder_levels": [
			{
				"parent": code,
				"parenttype": "Item",
				"parentfield": "reorder_levels",
				"warehouse": r[0],
				"warehouse_group": r[4] if len(r) > 4 else GROUP_OF.get(r[0], r[0]),
				"warehouse_reorder_level": r[1],
				"warehouse_reorder_qty": r[2],
				"material_request_type": r[3] if len(r) > 3 else "Purchase",
			}
			for r in (rules or [])
		],
	}


def _reset(perm=None, roles=None):
	STATE.clear()
	STATE.update(
		{
			"perm": dict(perm or {}),
			"roles": list(roles if roles is not None else ["Stock User"]),
			"clock": 10,
			"saves": [],
			"save_error": None,
			"items": {
				"PDT-A": _item_record("PDT-A", [(BIN, 4.0, 12.0)]),
				"PDT-ROOM": _item_record("PDT-ROOM", [(ROOM, 6.0, 24.0)]),
				"PDT-BARE": _item_record("PDT-BARE"),
				"PDT-MIXED": _item_record(
					"PDT-MIXED", [(ROOM, 1.0, 2.0, "Material Transfer"), (BIN_B, 3.0, 9.0, "Purchase")]
				),
				"PDT-XFER": _item_record("PDT-XFER", [(ROOM, 2.0, 5.0, "Material Transfer")]),
				"PDT-LOCALX": _item_record(
					"PDT-LOCALX", [(ROOM, 6.0, 24.0, "Purchase"), (BIN, 2.0, 5.0, "Material Transfer")]
				),
				# one bin rule that checks the bin itself, the shape the default group must not copy
				"PDT-OWN": _item_record("PDT-OWN", [(BIN_B, 1.0, 1.0, "Purchase", BIN_B)]),
				"PDT-FEET": _item_record("PDT-FEET", uom="Foot"),
				"PDT-WHOLE": _item_record("PDT-WHOLE", uom="Each"),
			},
			"uoms": {
				"Unit": {"name": "Unit", "must_be_whole_number": 0},
				"Foot": {"name": "Foot", "must_be_whole_number": 0},
				"Each": {"name": "Each", "must_be_whole_number": 1},
			},
			"warehouses": {
				BIN: {
					"name": BIN,
					"warehouse_name": "Bin A1-1-1",
					"is_group": 0,
					"disabled": 0,
					"warehouse_type": "",
				},
				BIN_B: {
					"name": BIN_B,
					"warehouse_name": "Bin A1-1-2",
					"is_group": 0,
					"disabled": 0,
					"warehouse_type": "",
				},
				ROOM: {
					"name": ROOM,
					"warehouse_name": "Inventory Room",
					"is_group": 0,
					"disabled": 0,
					"warehouse_type": "",
				},
				"Row A - SF": {
					"name": "Row A - SF",
					"warehouse_name": "Row A",
					"is_group": 1,
					"disabled": 0,
					"warehouse_type": "",
				},
				"Old - SF": {
					"name": "Old - SF",
					"warehouse_name": "Old",
					"is_group": 0,
					"disabled": 1,
					"warehouse_type": "",
				},
				"Truck - SF": {
					"name": "Truck - SF",
					"warehouse_name": "Truck",
					"is_group": 0,
					"disabled": 0,
					"warehouse_type": "Transit",
				},
			},
		}
	)
	_tree()


def _tree():
	"""Production's shape: All Warehouses > Inventory > Row > Shelf > Bin, the Inventory Room straight
	under Inventory, a second Row with no rules yet, and two warehouses outside it."""

	def wh(name, parent=None, group=0, **kw):
		return {
			"name": name,
			"warehouse_name": name.replace(" - SF", ""),
			"parent_warehouse": parent,
			"is_group": group,
			"disabled": 0,
			"warehouse_type": "",
			**kw,
		}

	w = STATE["warehouses"]
	for name, parent, group in (
		(ALL, None, 1),
		(INVENTORY, ALL, 1),
		("Row 1 - SF", INVENTORY, 1),
		(SHELF_A, "Row 1 - SF", 1),
		("Row 2 - SF", INVENTORY, 1),
		("Shelf C - SF", "Row 2 - SF", 1),
		("Bin C - SF", "Shelf C - SF", 0),
		("Annex - SF", ALL, 0),
		("Loose - SF", None, 0),
	):
		w[name] = wh(name, parent, group)
	w[BIN]["parent_warehouse"] = SHELF_A
	w[BIN_B]["parent_warehouse"] = SHELF_A
	w[ROOM]["parent_warehouse"] = INVENTORY


def _install_frappe_stub():
	global FRAPPE, STATE
	frappe = types.ModuleType("frappe")

	class ValidationError(Exception):
		pass

	class PermissionError(Exception):
		pass

	class DoesNotExistError(ValidationError):
		pass

	class TimestampMismatchError(ValidationError):
		pass

	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError
	frappe.DoesNotExistError = DoesNotExistError
	frappe.TimestampMismatchError = TimestampMismatchError
	frappe._ = lambda s, *a, **k: s
	frappe.bold = lambda s: f"<b>{s}</b>"

	def throw(msg="", exc=ValidationError, title=None):
		raise exc(msg)

	frappe.throw = throw

	def whitelist(*dargs, **dkwargs):
		def wrap(fn):
			fn._whitelist = dkwargs
			return fn

		return wrap(dargs[0]) if dargs and callable(dargs[0]) else wrap

	frappe.whitelist = whitelist
	frappe.session = types.SimpleNamespace(user="parker@example.com")
	STATE = {}
	frappe.get_roles = lambda user=None: list(STATE["roles"])

	def has_permission(doctype=None, ptype="read", doc=None, user=None, throw=False, **kwargs):
		STATE.setdefault("checks", []).append((doctype, ptype))
		return STATE["perm"].get((doctype, ptype), True)

	frappe.has_permission = has_permission

	def get_doc(doctype, name=None):
		if name not in STATE["items"]:
			raise DoesNotExistError(f"{doctype} {name}")
		return Doc(doctype, STATE["items"][name])

	frappe.get_doc = get_doc

	def get_all(doctype, filters=None, fields=None, order_by=None, **kwargs):
		if doctype == "Item":
			rows = [r for r in STATE["items"].values() if _matches(r, filters)]
		elif doctype == "Item Reorder":
			rows = []
			for item in STATE["items"].values():
				rows.extend(item["reorder_levels"])
			rows = [r for r in rows if _matches(r, filters)]
		elif doctype == "Warehouse":
			rows = [r for r in STATE["warehouses"].values() if _matches(r, filters)]
		elif doctype == "UOM":
			rows = [r for r in STATE["uoms"].values() if _matches(r, filters)]
		else:
			raise AssertionError(f"unexpected get_all({doctype})")
		return [frappe._dict({f: r.get(f) for f in fields}) for r in rows]

	frappe.get_all = get_all

	def exists(doctype, name=None):
		return name in STATE["items"] if doctype == "Item" else False

	def get_value(doctype, name, fieldname="name", as_dict=False, **kwargs):
		record = STATE["warehouses"].get(name) if doctype == "Warehouse" else None
		if record is None:
			return None
		if isinstance(fieldname, list):
			return (
				frappe._dict({f: record.get(f) for f in fieldname})
				if as_dict
				else [record.get(f) for f in fieldname]
			)
		return record.get(fieldname)

	frappe.db = types.SimpleNamespace(exists=exists, get_value=get_value)

	class _dict(dict):
		def __getattr__(self, key):
			if key.startswith("__"):
				raise AttributeError(key)
			return self.get(key)

	frappe._dict = _dict

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda value: int(float(value or 0)) if str(value or "").strip() else 0

	def flt(value, precision=None):
		try:
			return float(value or 0)
		except (TypeError, ValueError):
			return 0.0

	utils.flt = flt
	utils.cstr = lambda value: "" if value is None else str(value)
	utils.getdate = lambda value=None: str(value or "2026-10-22")[:10]
	utils.nowdate = lambda: "2026-10-22"
	utils.strip_html_tags = lambda text: re.sub(r"<[^>]+>", "", text or "")

	def get_datetime(value):
		if isinstance(value, datetime.datetime):
			return value
		text = str(value)
		for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
			try:
				return datetime.datetime.strptime(text, fmt)
			except ValueError:
				continue
		raise ValueError(text)

	utils.get_datetime = get_datetime
	frappe.utils = utils
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	FRAPPE = frappe


def setUpModule():
	global api
	_install_frappe_stub()
	for mod in ("erpnext_enhancements.api.item_flow", "erpnext_enhancements.api.reorder_levels"):
		sys.modules.pop(mod, None)
	from erpnext_enhancements.api import reorder_levels

	api = reorder_levels


def tearDownModule():
	for mod in (
		"frappe",
		"frappe.utils",
		"erpnext_enhancements.api.item_flow",
		"erpnext_enhancements.api.reorder_levels",
	):
		sys.modules.pop(mod, None)


def stamp(code):
	return str(STATE["items"][code]["modified"])


class TestReading(unittest.TestCase):
	def setUp(self):
		_reset()

	def read(self, codes, warehouse=BIN):
		return api.get_reorder_levels(warehouse, json.dumps(codes))

	def test_is_a_post_only_endpoint_behind_the_scan_roles(self):
		self.assertEqual(api.get_reorder_levels._whitelist, {"methods": ["POST"]})
		self.assertEqual(api.set_reorder_level._whitelist, {"methods": ["POST"]})
		_reset(roles=["Sales User"])
		with self.assertRaises(FRAPPE.PermissionError):
			self.read(["PDT-A"])
		_reset(roles=["Inventory Clerk"])
		self.assertIn("PDT-A", self.read(["PDT-A"])["levels"])

	def test_an_item_shows_its_rule_on_the_screens_warehouse(self):
		out = self.read(["PDT-A"])
		e = out["levels"]["PDT-A"]
		self.assertEqual((e["has_rule"], e["level"], e["qty"], e["row_warehouse"]), (True, 4.0, 12.0, BIN))
		self.assertFalse(e["elsewhere"])
		self.assertEqual(e["warehouse_label"], "Bin A1-1-1")
		self.assertEqual(e["modified"], "2026-10-22 08:00:00.123456")

	def test_an_item_whose_rule_is_on_the_room_shows_it_labelled(self):
		e = self.read(["PDT-ROOM"])["levels"]["PDT-ROOM"]
		self.assertEqual((e["has_rule"], e["level"], e["qty"]), (True, 6.0, 24.0))
		self.assertEqual(
			(e["row_warehouse"], e["warehouse_label"], e["elsewhere"]), (ROOM, "Inventory Room", True)
		)

	def test_an_item_with_no_rule_offers_the_screens_warehouse(self):
		e = self.read(["PDT-BARE"])["levels"]["PDT-BARE"]
		self.assertEqual((e["has_rule"], e["level"], e["qty"], e["row_warehouse"]), (False, None, None, BIN))
		self.assertFalse(e["elsewhere"])

	def test_no_warehouse_means_no_place_for_a_new_rule_but_an_own_rule_still_shows(self):
		out = api.get_reorder_levels("", json.dumps(["PDT-BARE", "PDT-ROOM"]))["levels"]
		self.assertIsNone(out["PDT-BARE"]["row_warehouse"])
		self.assertEqual(out["PDT-ROOM"]["row_warehouse"], ROOM)

	def test_which_rule_an_item_shows(self):
		# the screen's own warehouse first, whatever the rule's type
		local = self.read(["PDT-LOCALX"])["levels"]["PDT-LOCALX"]
		self.assertEqual(
			(local["row_warehouse"], local["material_request_type"], local["elsewhere"]),
			(BIN, "Material Transfer", False),
		)
		# no rule here: a Purchase rule elsewhere before a Transfer rule elsewhere
		mixed = self.read(["PDT-MIXED"])["levels"]["PDT-MIXED"]
		self.assertEqual(
			(mixed["level"], mixed["qty"], mixed["row_warehouse"], mixed["elsewhere"]),
			(3.0, 9.0, BIN_B, True),
		)
		# only a transfer rule anywhere: that is shown, labelled
		x = self.read(["PDT-XFER"])["levels"]["PDT-XFER"]
		self.assertEqual((x["material_request_type"], x["elsewhere"]), ("Material Transfer", True))

	def test_unknown_items_are_left_out_and_the_list_is_checked(self):
		self.assertEqual(self.read(["PDT-NONE"])["levels"], {})
		self.assertEqual(api.get_reorder_levels(BIN, "[]")["levels"], {})
		self.assertEqual(api.get_reorder_levels(BIN, None)["levels"], {})
		for bad in ("not json", json.dumps({"a": 1})):
			with self.assertRaises(FRAPPE.ValidationError):
				api.get_reorder_levels(BIN, bad)
		with self.assertRaises(FRAPPE.ValidationError):
			api.get_reorder_levels(BIN, json.dumps([f"X{n}" for n in range(api.MAX_ITEMS + 1)]))

	def test_can_edit_follows_write_on_item(self):
		self.assertTrue(self.read(["PDT-A"])["can_edit"])
		_reset(perm={("Item", "write"): False})
		self.assertFalse(self.read(["PDT-A"])["can_edit"])

	def test_a_reader_with_the_codes_as_a_list_also_works(self):
		self.assertIn("PDT-A", api.get_reorder_levels(BIN, ["PDT-A"])["levels"])


class TestWriting(unittest.TestCase):
	def setUp(self):
		_reset()

	def save(self, code="PDT-A", warehouse=BIN, level=None, qty=None, modified="", view=None):
		modified = stamp(code) if modified == "" else modified
		return api.set_reorder_level(code, warehouse, modified, level=level, qty=qty, view_warehouse=view)

	def test_editing_changes_the_rule_through_the_item_and_answers_with_the_new_entry(self):
		out = self.save(level="6", qty="20")
		self.assertTrue(out["saved"])
		self.assertEqual(STATE["saves"], ["PDT-A"])
		row = STATE["items"]["PDT-A"]["reorder_levels"][0]
		self.assertEqual((row["warehouse_reorder_level"], row["warehouse_reorder_qty"]), (6.0, 20.0))
		self.assertEqual(len(STATE["items"]["PDT-A"]["reorder_levels"]), 1, "the row is edited, not added")
		e = out["entry"]
		self.assertEqual((e["level"], e["qty"]), (6.0, 20.0))
		self.assertEqual(
			e["modified"], "2026-10-22 09:00:11.123456", "the new stamp, to send with the next save"
		)

	def test_one_value_is_enough_the_other_is_kept(self):
		self.save(level="7")
		row = STATE["items"]["PDT-A"]["reorder_levels"][0]
		self.assertEqual((row["warehouse_reorder_level"], row["warehouse_reorder_qty"]), (7.0, 12.0))
		self.save(qty="30", modified=stamp("PDT-A"))
		row = STATE["items"]["PDT-A"]["reorder_levels"][0]
		self.assertEqual((row["warehouse_reorder_level"], row["warehouse_reorder_qty"]), (7.0, 30.0))

	def test_numbers_that_are_already_those_save_nothing(self):
		before = stamp("PDT-A")
		out = self.save(level="4", qty="12")
		self.assertFalse(out["saved"])
		self.assertEqual(STATE["saves"], [])
		self.assertEqual(
			stamp("PDT-A"), before, "the Item's modified keeps still, so other open copies stay current"
		)
		self.assertEqual(out["entry"]["modified"], before)

	def test_permission_is_checked_before_anything_else(self):
		_reset(perm={("Item", "write"): False})
		with self.assertRaises(FRAPPE.PermissionError):
			self.save(level="9", qty="9")
		self.assertEqual(STATE["saves"], [])
		# the role gate too
		_reset(roles=["Sales User"])
		with self.assertRaises(FRAPPE.PermissionError):
			self.save(level="9", qty="9")
		# and a refusal of the Item comes before a complaint about the numbers
		_reset(perm={("Item", "write"): False})
		with self.assertRaises(FRAPPE.PermissionError):
			self.save(level="nope", qty="x")

	def test_a_stale_page_writes_nothing(self):
		old = stamp("PDT-A")
		self.save(level="5", qty="12")  # someone else saves first
		with self.assertRaises(FRAPPE.TimestampMismatchError) as caught:
			self.save(level="8", qty="12", modified=old)
		self.assertIn("changed by someone else", str(caught.exception))
		row = STATE["items"]["PDT-A"]["reorder_levels"][0]
		self.assertEqual(row["warehouse_reorder_level"], 5.0, "their number is still there")
		self.assertEqual(len(STATE["saves"]), 1)

	def test_the_stamp_is_required_and_compared_as_a_moment(self):
		with self.assertRaises(FRAPPE.TimestampMismatchError):
			self.save(level="8", modified=None)
		with self.assertRaises(FRAPPE.TimestampMismatchError):
			self.save(level="8", modified="not a date")
		# the same moment written without microseconds is another moment, and one written as the
		# datetime the database returns is the same one
		self.save(level="8", modified=STATE["items"]["PDT-A"]["modified"])
		self.assertEqual(len(STATE["saves"]), 1)

	def test_an_item_with_no_rule_gets_a_purchase_rule_on_the_bin(self):
		out = self.save("PDT-BARE", BIN, level="3", qty="10")
		rows = STATE["items"]["PDT-BARE"]["reorder_levels"]
		self.assertEqual(len(rows), 1)
		self.assertEqual(
			(rows[0]["warehouse"], rows[0]["warehouse_group"], rows[0]["material_request_type"]),
			(BIN, INVENTORY, "Purchase"),
		)
		self.assertEqual((rows[0]["warehouse_reorder_level"], rows[0]["warehouse_reorder_qty"]), (3.0, 10.0))
		self.assertTrue(out["entry"]["has_rule"])

	def test_a_new_rule_needs_both_numbers_neither_ever_zero(self):
		for kwargs in ({"level": "3"}, {"qty": "10"}):
			with self.assertRaises(FRAPPE.ValidationError) as caught:
				self.save("PDT-BARE", BIN, **kwargs)
			self.assertIn("Enter both Reorder at and Buy", str(caught.exception))
		self.assertEqual(STATE["items"]["PDT-BARE"]["reorder_levels"], [], "nothing was made")
		self.assertEqual(STATE["saves"], [])

	def test_a_rule_cannot_be_made_where_stock_is_not_kept(self):
		for warehouse, words in (
			("", "Choose the bin"),
			("Nowhere - SF", "Choose the bin"),
			("Row A - SF", "cannot hold stock"),
			("Old - SF", "cannot hold stock"),
			("Truck - SF", "cannot hold stock"),
		):
			with self.subTest(warehouse=warehouse):
				with self.assertRaises(FRAPPE.ValidationError) as caught:
					self.save("PDT-BARE", warehouse, level="1", qty="1")
				self.assertIn(words, str(caught.exception))
		self.assertEqual(STATE["saves"], [])

	def test_a_rule_on_another_warehouse_is_edited_where_it_is_and_read_against_the_screen(self):
		out = self.save("PDT-ROOM", ROOM, level="9", qty="30", view=BIN)
		rows = STATE["items"]["PDT-ROOM"]["reorder_levels"]
		self.assertEqual(
			[(r["warehouse"], r["warehouse_reorder_level"]) for r in rows], [(ROOM, 9.0)], "no second row"
		)
		self.assertTrue(out["entry"]["elsewhere"])
		self.assertEqual(out["entry"]["warehouse_label"], "Inventory Room")

	def test_only_the_named_warehouses_rule_is_edited(self):
		self.save("PDT-MIXED", BIN_B, level="5", qty="9", view=BIN)
		rows = STATE["items"]["PDT-MIXED"]["reorder_levels"]
		self.assertEqual(
			[(r["warehouse"], r["warehouse_reorder_level"]) for r in rows], [(ROOM, 1.0), (BIN_B, 5.0)]
		)
		# naming a warehouse the item has no rule on makes one there, beside the others
		self.save("PDT-MIXED", BIN, level="2", qty="4", modified=stamp("PDT-MIXED"))
		rows = STATE["items"]["PDT-MIXED"]["reorder_levels"]
		self.assertEqual([r["warehouse"] for r in rows], [ROOM, BIN_B, BIN])

	def test_the_numbers_are_checked_before_any_write(self):
		for level, qty in (("x", "1"), ("1", "-2"), ("nan", "1"), ("inf", "1"), ("-1", "1"), ("1", "1e999")):
			with self.subTest(level=level, qty=qty):
				with self.assertRaises(FRAPPE.ValidationError):
					self.save(level=level, qty=qty)
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			self.save()
		self.assertIn("level or a quantity", str(caught.exception))
		self.assertEqual(STATE["saves"], [])

	def test_a_level_or_quantity_of_zero_is_never_saved(self):
		for kwargs, words in (
			({"level": "5", "qty": "0"}, "Buy must be more than 0"),
			({"level": "0", "qty": "5"}, "Reorder at must be more than 0"),
			({"level": "0", "qty": "0"}, "Reorder at must be more than 0"),
			({"level": "0"}, "Reorder at must be more than 0"),
		):
			with self.subTest(kwargs=kwargs):
				with self.assertRaises(FRAPPE.ValidationError) as caught:
					self.save(**kwargs)
				self.assertIn(words, str(caught.exception))
		with self.assertRaises(FRAPPE.ValidationError):
			self.save("PDT-BARE", BIN, level="0", qty="5")
		self.assertEqual(STATE["saves"], [])
		row = STATE["items"]["PDT-A"]["reorder_levels"][0]
		self.assertEqual((row["warehouse_reorder_level"], row["warehouse_reorder_qty"]), (4.0, 12.0))

	def test_erpnexts_own_refusal_gets_through(self):
		STATE["save_error"] = FRAPPE.ValidationError("Item PDT-A is blocked by a validation of its own")
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			self.save(level="9", qty="9")
		self.assertIn("blocked by a validation", str(caught.exception))
		self.assertEqual(STATE["saves"], [])

	def test_an_unknown_item_is_a_sentence_not_a_traceback(self):
		with self.assertRaises(FRAPPE.DoesNotExistError):
			api.set_reorder_level("PDT-NONE", BIN, "2026-10-22 08:00:00.123456", level="1", qty="1")

	def test_a_resend_after_a_lost_reply_changes_nothing(self):
		first = self.save(level="6", qty="20")
		again = self.save(level="6", qty="20", modified=first["entry"]["modified"])
		self.assertTrue(first["saved"])
		self.assertFalse(again["saved"])
		self.assertEqual(len(STATE["saves"]), 1)


class TestTheCeilingAndTheUnit(unittest.TestCase):
	def setUp(self):
		_reset()

	def save(self, code="PDT-A", **kw):
		return api.set_reorder_level(code, BIN, stamp(code), **kw)

	def test_the_ceiling_is_the_stock_scan_quantity_ceiling_and_the_clients(self):
		self.assertEqual(api.MAX_AMOUNT, rules.MAX_QTY)
		logic = (JS_DIR / "logic.js").read_text(encoding="utf-8")
		self.assertIn(
			f"export const MAX_AMOUNT = {api.MAX_AMOUNT};", logic, "the editor and the server must agree"
		)

	def test_a_number_above_the_ceiling_is_refused_whichever_box_it_was_typed_in(self):
		for kwargs in (
			{"level": "12345678905", "qty": "12"},
			{"level": "4", "qty": "100001"},
			{"level": "100001"},
			{"qty": "1e9"},
		):
			with self.subTest(kwargs=kwargs):
				with self.assertRaises(FRAPPE.ValidationError) as caught:
					self.save(**kwargs)
				self.assertTrue(
					"more than" in str(caught.exception) or "number" in str(caught.exception),
					str(caught.exception),
				)
		self.assertEqual(STATE["saves"], [])
		self.save(level="100000", qty="100000")
		self.assertEqual(len(STATE["saves"]), 1, "the ceiling itself is allowed")

	def test_a_unit_counted_in_whole_numbers_takes_whole_numbers(self):
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			api.set_reorder_level("PDT-WHOLE", BIN, stamp("PDT-WHOLE"), level="4.5", qty="10")
		self.assertIn("whole numbers", str(caught.exception))
		with self.assertRaises(FRAPPE.ValidationError):
			api.set_reorder_level("PDT-WHOLE", BIN, stamp("PDT-WHOLE"), level="4", qty="2.5")
		self.assertEqual(STATE["saves"], [])
		api.set_reorder_level("PDT-WHOLE", BIN, stamp("PDT-WHOLE"), level="4", qty="10")
		self.assertEqual(STATE["saves"], ["PDT-WHOLE"])
		# where fractions are fine
		api.set_reorder_level("PDT-FEET", BIN, stamp("PDT-FEET"), level="4.5", qty="10.25")
		self.assertEqual(STATE["saves"], ["PDT-WHOLE", "PDT-FEET"])

	def test_the_read_says_which_units_are_whole_numbers(self):
		out = api.get_reorder_levels(BIN, json.dumps(["PDT-WHOLE", "PDT-FEET"]))["levels"]
		self.assertEqual((out["PDT-WHOLE"]["stock_uom"], out["PDT-WHOLE"]["whole_number"]), ("Each", True))
		self.assertEqual((out["PDT-FEET"]["stock_uom"], out["PDT-FEET"]["whole_number"]), ("Foot", False))
		self.assertTrue(
			api.set_reorder_level("PDT-WHOLE", BIN, stamp("PDT-WHOLE"), level="4", qty="10")["entry"][
				"whole_number"
			]
		)


class TestTheGroupANewRuleChecksStockIn(unittest.TestCase):
	"""Production: 32 of 33 bin rules check Inventory - SF, all 83 Inventory Room rules check All
	Warehouses - SF, one bin rule checks itself. A new rule follows its neighbours."""

	def setUp(self):
		_reset()

	def group(self, warehouse):
		return api._default_group(warehouse)

	def test_a_bin_under_a_row_checks_inventory(self):
		self.assertEqual(self.group(BIN), INVENTORY)
		self.assertEqual(self.group(BIN_B), INVENTORY, "its own neighbour checks itself, and is not copied")

	def test_the_inventory_room_checks_all_warehouses(self):
		self.assertEqual(self.group(ROOM), ALL)

	def test_a_bin_on_a_shelf_with_no_rules_climbs_to_the_rows_neighbours(self):
		self.assertEqual(self.group("Bin C - SF"), INVENTORY)

	def _only(self, *rules_):
		"""Every seeded rule removed, then ``rules_`` (code, warehouse, group) made."""
		for item in STATE["items"].values():
			item["reorder_levels"] = []
		for index, (warehouse, group) in enumerate(rules_):
			code = f"PDT-X{index}"
			STATE["items"][code] = _item_record(code, [(warehouse, 1.0, 1.0, "Purchase", group)])

	def test_a_rule_that_checks_its_own_bin_is_never_the_example(self):
		self._only((BIN_B, BIN_B))
		self.assertEqual(self.group(BIN), SHELF_A, "no usable neighbour: the parent group")
		self.assertEqual(self.group(ROOM), INVENTORY)

	def test_with_no_neighbours_the_parent_group_else_itself(self):
		self.assertEqual(self.group("Annex - SF"), ALL)
		self.assertEqual(self.group("Loose - SF"), "Loose - SF")

	def test_the_most_common_group_wins_and_a_tie_goes_by_name(self):
		self._only((BIN, "Row 1 - SF"), (BIN, "Row 1 - SF"), (BIN_B, INVENTORY))
		self.assertEqual(self.group("Bin C - SF"), "Row 1 - SF", "2 votes beat 1")
		self._only((BIN, "Row 1 - SF"), (BIN_B, INVENTORY))
		self.assertEqual(self.group("Bin C - SF"), INVENTORY, "a tie goes by name")

	def test_a_new_rule_made_by_the_endpoint_gets_it_and_existing_rules_are_untouched(self):
		before = copy.deepcopy(STATE["items"]["PDT-ROOM"]["reorder_levels"])
		api.set_reorder_level("PDT-BARE", ROOM, stamp("PDT-BARE"), level="2", qty="9")
		self.assertEqual(STATE["items"]["PDT-BARE"]["reorder_levels"][0]["warehouse_group"], ALL)
		api.set_reorder_level("PDT-A", BIN, stamp("PDT-A"), level="6", qty="12")
		self.assertEqual(STATE["items"]["PDT-A"]["reorder_levels"][0]["warehouse_group"], INVENTORY)
		self.assertEqual(STATE["items"]["PDT-ROOM"]["reorder_levels"], before)
		# editing the rule that checks its own bin does not quietly change what it checks
		api.set_reorder_level("PDT-OWN", BIN_B, stamp("PDT-OWN"), level="2", qty="3")
		self.assertEqual(STATE["items"]["PDT-OWN"]["reorder_levels"][0]["warehouse_group"], BIN_B)


class TestRemoving(unittest.TestCase):
	def setUp(self):
		_reset()

	def remove(self, code="PDT-A", warehouse=BIN, modified="", view=None):
		return api.remove_reorder_rule(code, warehouse, stamp(code) if modified == "" else modified, view)

	def test_the_post_only_endpoint_removes_the_row_through_the_item(self):
		self.assertEqual(api.remove_reorder_rule._whitelist, {"methods": ["POST"]})
		out = self.remove()
		self.assertTrue(out["saved"])
		self.assertEqual(STATE["saves"], ["PDT-A"])
		self.assertEqual(STATE["items"]["PDT-A"]["reorder_levels"], [])
		self.assertFalse(out["entry"]["has_rule"])
		self.assertEqual(out["entry"]["row_warehouse"], BIN, "a new rule can be made again on the bin")

	def test_only_the_named_rule_goes(self):
		out = self.remove("PDT-MIXED", BIN_B, view=BIN)
		self.assertEqual([r["warehouse"] for r in STATE["items"]["PDT-MIXED"]["reorder_levels"]], [ROOM])
		self.assertEqual(out["entry"]["row_warehouse"], ROOM, "the item next rule is what shows")
		self.assertTrue(out["entry"]["elsewhere"])

	def test_permission_first_then_the_stale_page_then_a_missing_rule(self):
		_reset(perm={("Item", "write"): False})
		with self.assertRaises(FRAPPE.PermissionError):
			self.remove()
		_reset(roles=["Sales User"])
		with self.assertRaises(FRAPPE.PermissionError):
			self.remove()
		_reset()
		old = stamp("PDT-A")
		api.set_reorder_level("PDT-A", BIN, old, level="5", qty="12")
		with self.assertRaises(FRAPPE.TimestampMismatchError):
			self.remove(modified=old)
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			self.remove("PDT-BARE", BIN)
		self.assertIn("no reorder rule", str(caught.exception))
		with self.assertRaises(FRAPPE.ValidationError):
			self.remove("PDT-ROOM", BIN)
		self.assertEqual(len(STATE["saves"]), 1)
		self.assertEqual(len(STATE["items"]["PDT-A"]["reorder_levels"]), 1)

	def test_erpnexts_refusal_gets_through_and_nothing_is_kept(self):
		STATE["save_error"] = FRAPPE.ValidationError("blocked")
		with self.assertRaises(FRAPPE.ValidationError):
			self.remove()
		self.assertEqual(STATE["saves"], [])


class TestTheSource(unittest.TestCase):
	def test_the_endpoint_writes_through_the_item_only(self):
		text = API_FILE.read_text(encoding="utf-8")
		tree = ast.parse(text)
		code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
		code = re.sub(r'"""[\s\S]*?"""', "", code)
		for banned in (
			"ignore_permissions",
			"db.sql",
			"db.set_value",
			"db.delete",
			"db_set",
			"db_update",
			"frappe.delete_doc",
			"sql(",
		):
			self.assertNotIn(banned, code, f"the write must be Item.save() as the user, not {banned}")
		calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
		saves = [c for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr == "save"]
		self.assertEqual(len(saves), 2, "set and remove each write once: the Item's save")

	def test_the_item_permission_is_checked_first(self):
		tree = ast.parse(API_FILE.read_text(encoding="utf-8"))
		fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "set_reorder_level")
		body = [
			n
			for n in fn.body
			if not (isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant))
		]
		first = ast.unparse(body[0])
		second = ast.unparse(body[1])
		self.assertEqual(first, "_check_access()")
		self.assertIn("item_flow._item(item_code, 'write')", second)

	def test_no_aggregate_function_strings_in_get_all(self):
		text = API_FILE.read_text(encoding="utf-8")
		self.assertIsNone(re.search(r"fields=\[[^\]]*\b(count|sum|max|min|avg)\s*\(", text, re.I))

	def test_the_editor_never_touches_history_or_the_url(self):
		for path in [*JS_DIR.glob("*.js"), BUNDLE_JS]:
			text = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", path.read_text(encoding="utf-8"))
			for banned in (
				"pushState",
				"replaceState",
				"set_route",
				"location.",
				"history.",
				"popstate",
				"hashchange",
				"href",
			):
				self.assertNotIn(
					banned, text, f"{path.name}: a save must never be a history entry ({banned})"
				)

	def test_the_editor_files_use_no_jquery_and_no_inline_style(self):
		for path in JS_DIR.glob("*.js"):
			text = path.read_text(encoding="utf-8")
			self.assertNotIn("$(", re.sub(r"`[^`]*\$\{[^`]*`", "", text), path.name)
			self.assertNotIn("style=", text, path.name)

	def test_the_css_is_namespaced_and_phone_sized(self):
		css = re.sub(r"/\*[\s\S]*?\*/", "", BUNDLE_CSS.read_text(encoding="utf-8"))
		for selector in re.findall(r"([^{}]+)\{", re.sub(r"@media[^{]+\{", "", css)):
			for part in selector.split(","):
				self.assertTrue(part.strip().startswith(".ro"), f"not namespaced: {part.strip()}")
		self.assertIn("font-size: 16px", css, "iOS zooms the page into a box under 16px")
		self.assertIn("min-height: 44px", css)

	def test_the_bundle_installs_the_global_the_pages_test(self):
		text = BUNDLE_JS.read_text(encoding="utf-8")
		self.assertIn("window.sf_reorder", text)
		for name in ("ReorderEditor", "cell_html", "bind", "paint", "read_boxes"):
			self.assertIn(name, text)

	def test_both_pages_ask_for_the_bundle_and_work_without_it(self):
		bins = BIN_PAGE.read_text(encoding="utf-8")
		self.assertIn('"reorder_levels.bundle.js", "reorder_levels.bundle.css"', bins)
		self.assertIn("this.reorder ?", bins, "the column exists only with the editor")
		self.assertRegex(
			bins, r"catch \(e\) \{\s+resolve\(\);", "a throwing require must not wedge the ready chain"
		)
		self.assertIn("sync_reorder(loc)", bins)
		self.assertIn(
			"this.ready.then(() => this.load_reorder_bundle());", bins, "the bins do not wait for the column"
		)
		self.assertIn("redraw_after_blur", bins)
		self.assertIn("confirm: (text, yes, no) => frappe.confirm(text, yes, no)", bins)
		# the page's own files are still the first require, exactly as test_bin_manager pins
		self.assertIn(
			'const BM_ASSETS = ["sf_kit.bundle.js", "sf_kit.bundle.css", "bin_manager.bundle.js"];', bins
		)
		isa = ISA_PAGE.read_text(encoding="utf-8")
		self.assertIn("'reorder_levels.bundle.js', 'reorder_levels.bundle.css'", isa)
		self.assertIn("reorderCell(p)", isa)
		self.assertIn("isa-reorder", isa)
		self.assertIn("this.reorder.load(", isa)
		self.assertIn("after_enter", isa, "Enter must return focus to the counted-quantity box")
		self.assertIn("confirm: (text, yes, no) => frappe.confirm(text, yes, no)", isa)


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheEditorUnderNode(unittest.TestCase):
	def test_the_harness_passes(self):
		run = subprocess.run(
			[shutil.which("node"), str(HARNESS)], capture_output=True, text=True, timeout=300, cwd=REPO_ROOT
		)
		self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
		self.assertIn("checks passed", run.stdout)

	def test_the_files_parse(self):
		for path in [*JS_DIR.glob("*.js"), BIN_PAGE, ISA_PAGE]:
			run = subprocess.run(
				[shutil.which("node"), "--check", str(path)], capture_output=True, text=True, timeout=60
			)
			# a .js file with import/export is checked as the module it is
			if run.returncode != 0 and "import" in path.read_text(encoding="utf-8")[:2000]:
				run = subprocess.run(
					[shutil.which("node"), "--input-type=module", "--check"],
					input=path.read_text(encoding="utf-8"),
					capture_output=True,
					text=True,
					timeout=60,
				)
			self.assertEqual(run.returncode, 0, f"{path.name}: {run.stderr}")


if __name__ == "__main__":
	unittest.main()
