# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests: the Inventory Room swap (v1.823.0).

The owner, 2026-11-17: "Those bays should exist in the Inventory Room, not the Inventory
warehouse." ``patches/swap_inventory_room_names.py`` renames the leaf ``Inventory Room - SF`` to
``Receiving - SF``, then the group ``Inventory - SF`` to ``Inventory Room - SF``. Pinned here,
through the real patch against an in-memory database:

* the two renames run in that order, with Frappe's ``rename_doc`` (``force``, no alert, no queued
  index) and each sets the ``warehouse_name`` ERPNext 16.54.0 has no hook to set;
* everything that pointed at the room points at Receiving, everything that named the group names
  the Inventory Room, the tree does not change and no stock moves; above all, **nothing that meant
  the room ends up on the group**;
* people's saved list filters follow each rename, the room's before the group takes its name, and a
  second run leaves a filter on the renamed group alone;
* the group is never renamed unless the room was and its filters moved, nothing is merged, a failed
  step is rolled back to its savepoint and logged, one Error Log row lists what did not finish, and
  the patch never raises (a raising patch aborts ``bench migrate``, which is the deploy);
* a site without these warehouses answers "not needed here" with no Error Log row, and the second
  run answers "already";
* the output counts and never names a person.

What it cannot see: Frappe's real ``rename_doc`` walking every Link field (the stub follows the
links production had on 2026-11-17, listed in :data:`LINKS`), the Single it saves through the ORM,
and the after_rename hook. The rehearsal on a copy of production checks those.

Stubs a minimal ``frappe`` in ``setUpModule``, so it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_swap_inventory_room_names -v
"""

import ast
import contextlib
import copy
import io
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
PATCH_FILE = APP / "patches" / "swap_inventory_room_names.py"
PATCH_MODULE = "erpnext_enhancements.patches.swap_inventory_room_names"
STUBBED = (
	"frappe",
	"frappe.model",
	"frappe.model.rename_doc",
	"frappe.utils",
	"frappe.utils.global_search",
	PATCH_MODULE,
)

ROOT = "All Warehouses - SF"
GROUP = "Inventory - SF"
ROOM = "Inventory Room - SF"
RECEIVING = "Receiving - SF"
ROW = "Row 1 - SF"
BAY = "Bay A1 - SF"
BIN = "Bin A1-1-1 - SF"
BONEYARD = "Boneyard - SF"
SHELF = "Project Shelf - SF"
COMPANY = "Sapphire Fountains"
SETTINGS = "Inventory Scanner Settings"
ITEM, ITEM2, ITEM3 = "PDT-0001", "PDT-0002", "PDT-0003"

#: The Link fields to Warehouse the stub's rename follows: what named either warehouse on
#: production on 2026-11-17. Frappe's own rename follows every Link field on the site.
LINKS = (
	("Warehouse", "parent_warehouse"),
	("Warehouse", "old_parent"),
	("Bin", "warehouse"),
	("Stock Ledger Entry", "warehouse"),
	("Item Default", "default_warehouse"),
	("Putaway Rule", "warehouse"),
	("Item Reorder", "warehouse"),
	("Item Reorder", "warehouse_group"),
	("Purchase Order Item", "warehouse"),
	("Warehouse Floor Map Shape", "warehouse"),
)

patch = None
FRAPPE = None
STATE = {}
_saved = {}


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


def _table(doctype):
	return STATE["db"].setdefault(doctype, {})


def _warehouse(name, label, parent, is_group=0):
	_table("Warehouse")[name] = _Dict(
		name=name,
		warehouse_name=label,
		parent_warehouse=parent,
		old_parent=parent or "",
		is_group=is_group,
		disabled=0,
		company=COMPANY,
	)


def _row(doctype, name, **fields):
	_table(doctype)[name] = _Dict(name=name, **fields)


def _reset():
	"""Production before the patch, in miniature."""
	STATE.clear()
	STATE.update(
		{
			"db": {},
			"singles": {
				(SETTINGS, "overhead_receiving_warehouse"): ROOM,
				(SETTINGS, "project_shelf_warehouse"): SHELF,
			},
			"user_settings": [],
			"renames": [],
			"indexed": [],
			"errors": [],
			"set_values": [],
			"cache_dropped": [],
			"cache_cleared": [],
			"savepoints": [],
			"fail_rename": set(),
		}
	)
	_warehouse(ROOT, "All Warehouses", None, is_group=1)
	_warehouse(GROUP, "Inventory", ROOT, is_group=1)
	_warehouse(ROW, "Row 1", GROUP, is_group=1)
	_warehouse(BAY, "Bay A1", ROW, is_group=1)
	_warehouse(BIN, "Bin A1-1-1", BAY)
	_warehouse(ROOM, "Inventory Room", GROUP)
	_warehouse(BONEYARD, "Boneyard", GROUP)
	_warehouse(SHELF, "Project Shelf", ROOT)
	_row("Bin", "BIN-1", item_code=ITEM, warehouse=ROOM, actual_qty=5.0)
	_row("Bin", "BIN-2", item_code=ITEM2, warehouse=ROOM, actual_qty=0.0)
	_row("Bin", "BIN-3", item_code=ITEM, warehouse=BIN, actual_qty=3.0)
	_row("Stock Ledger Entry", "SLE-1", item_code=ITEM, warehouse=ROOM, actual_qty=5.0)
	_row("Stock Ledger Entry", "SLE-2", item_code=ITEM, warehouse=BIN, actual_qty=3.0)
	_row("Item Default", "ID-1", parent=ITEM, default_warehouse=ROOM)
	_row("Item Default", "ID-2", parent=ITEM3, default_warehouse=BIN)
	_row("Putaway Rule", "PR-1", item_code=ITEM, warehouse=ROOM, disable=0)
	_row("Item Reorder", "IR-1", parent=ITEM, warehouse=ROOM, warehouse_group=ROOT)
	_row("Item Reorder", "IR-2", parent=ITEM3, warehouse=BIN, warehouse_group=GROUP)
	_row("Purchase Order Item", "POI-1", item_code=ITEM, warehouse=ROOM)
	_row("Warehouse Floor Map Shape", "SHAPE-1", warehouse=ROOM, bin_order=None)
	_row("Warehouse Floor Map Shape", "SHAPE-2", warehouse=BAY, bin_order=None)


def _snapshot():
	return copy.deepcopy({key: STATE[key] for key in ("db", "singles", "user_settings")})


def _matches(row, filters):
	return all(row.get(key) == value for key, value in (filters or {}).items())


def _install_frappe():
	frappe = types.ModuleType("frappe")

	def get_value(doctype, name, fields=None, as_dict=False):
		if not isinstance(name, str):
			raise AssertionError(f"the stub reads a {doctype} by name only, not {name!r}")
		row = _table(doctype).get(name)
		if not row:
			return None
		if as_dict:
			return _Dict({field: row.get(field) for field in fields})
		if isinstance(fields, (list, tuple)):
			return tuple(row.get(field) for field in fields)
		return row.get(fields)

	def exists(doctype, name=None):
		return name in _table(doctype)

	def set_value(doctype, name, field, value=None, update_modified=True):
		STATE["set_values"].append((doctype, name, {field: value}, update_modified))
		_table(doctype)[name][field] = value

	def count(doctype, filters=None):
		return sum(1 for row in _table(doctype).values() if _matches(row, filters))

	def sql(query, values=None, as_dict=False, **kwargs):
		text = " ".join(query.split())
		if "`__UserSettings`" in text:
			if STATE.get("filters_fail"):
				raise RuntimeError("the saved filters could not be read")
			saved = STATE["user_settings"]
			if text.startswith("select"):
				needle = values[0].strip("%")
				return [(row["user"], row["doctype"]) for row in saved if needle in row["data"]]
			old, new, like = values
			for row in saved:
				if like.strip("%") in row["data"]:
					row["data"] = row["data"].replace(old, new)
			return []
		if text.startswith("select count(*), coalesce(sum(actual_qty), 0) from `tabBin`"):
			rows = [
				b for b in _table("Bin").values() if b["warehouse"] == values["w"] and b["actual_qty"] > 0
			]
			return [(len(rows), sum(b["actual_qty"] for b in rows))]
		if text.startswith("select (select count(*) from `tabItem Default`"):
			w = values["w"]
			return [
				(
					count("Item Default", {"default_warehouse": w}),
					count("Putaway Rule", {"warehouse": w, "disable": 0}),
					count("Item Reorder", {"warehouse": w}),
				)
			]
		if text.startswith("select count(*) from `tabWarehouse Floor Map Shape` where warehouse"):
			return [(count("Warehouse Floor Map Shape", {"warehouse": values["w"]}),)]
		if text.startswith("select count(*) from `tabItem Reorder` where warehouse_group"):
			return [(count("Item Reorder", {"warehouse_group": values["g"]}),)]
		raise AssertionError(f"the stub does not know this query: {text}")

	def get_single_value(doctype, field):
		return STATE["singles"].get((doctype, field))

	def savepoint(name):
		STATE["savepoints"].append((name, _snapshot()))

	def rollback(save_point=None):
		for index in range(len(STATE["savepoints"]) - 1, -1, -1):
			name, snapshot = STATE["savepoints"][index]
			if name == save_point:
				STATE.update(copy.deepcopy(snapshot))
				return
		raise AssertionError(f"no savepoint {save_point}")

	frappe.db = types.SimpleNamespace(
		get_value=get_value,
		exists=exists,
		set_value=set_value,
		count=count,
		sql=sql,
		get_single_value=get_single_value,
		savepoint=savepoint,
		rollback=rollback,
	)
	frappe.cache = types.SimpleNamespace(hdel=lambda name, key: STATE["cache_dropped"].append((name, key)))
	frappe.log_error = lambda title=None, message=None, **kwargs: STATE["errors"].append((title, message))
	frappe.get_traceback = lambda *args, **kwargs: "Traceback (most recent call last): ..."
	frappe.clear_document_cache = lambda doctype, name=None: STATE["cache_cleared"].append((doctype, name))

	model = types.ModuleType("frappe.model")
	rename_module = types.ModuleType("frappe.model.rename_doc")

	def rename_doc(
		doctype=None,
		old=None,
		new=None,
		force=False,
		merge=False,
		show_alert=True,
		rebuild_search=True,
		**kwargs,
	):
		"""Frappe's rename as far as this patch meets it: refuse a taken name (``validate_rename``
		without ``merge``), move the row, follow every Link and every Single that held it."""
		STATE["renames"].append(
			{
				"doctype": doctype,
				"old": old,
				"new": new,
				"force": force,
				"merge": merge,
				"show_alert": show_alert,
				"rebuild_search": rebuild_search,
			}
		)
		table = _table(doctype)
		if old not in table:
			raise RuntimeError(f"Can't rename {old} to {new} because {old} doesn't exist.")
		if new in table and not merge:
			raise RuntimeError(f"Another {doctype} with name {new} exists, select another name")
		row = table.pop(old)
		row["name"] = new
		table[new] = row
		for linked, field in LINKS:
			for other in _table(linked).values():
				if other.get(field) == old:
					other[field] = new
		for key, value in list(STATE["singles"].items()):
			if value == old:
				STATE["singles"][key] = new
		if old in STATE["fail_rename"]:
			raise RuntimeError("the rename could not finish")
		return new

	rename_module.rename_doc = rename_doc
	utils = types.ModuleType("frappe.utils")
	search = types.ModuleType("frappe.utils.global_search")

	def rebuild_for_doctype(doctype):
		if STATE.get("search_fails"):
			raise RuntimeError("the index could not be rebuilt")
		STATE["indexed"].append(doctype)

	search.rebuild_for_doctype = rebuild_for_doctype
	frappe.model, frappe.utils = model, utils
	model.rename_doc, utils.global_search = rename_module, search
	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.model": model,
			"frappe.model.rename_doc": rename_module,
			"frappe.utils": utils,
			"frappe.utils.global_search": search,
		}
	)
	return frappe


def setUpModule():
	global patch, FRAPPE
	for name in STUBBED:
		if name in sys.modules:
			_saved[name] = sys.modules.pop(name)
	FRAPPE = _install_frappe()
	import importlib

	patch = importlib.import_module(PATCH_MODULE)


def tearDownModule():
	for name in STUBBED:
		sys.modules.pop(name, None)
	sys.modules.update(_saved)


def _settings_row(user, doctype, filters):
	return {"user": user, "doctype": doctype, "data": json.dumps({"List": {"filters": filters}})}


class _Base(unittest.TestCase):
	def setUp(self):
		_reset()

	def _run(self):
		"""Run the patch. ``(what it printed, the Error Log rows it left with its report title)``."""
		printed = io.StringIO()
		with contextlib.redirect_stdout(printed):
			patch.execute()
		reports = [message for title, message in STATE["errors"] if title == patch.REPORT_TITLE]
		return printed.getvalue(), reports

	def _line(self, text, number):
		return next(line for line in text.splitlines() if line.startswith(f"{number}. "))

	def wh(self, name):
		return _table("Warehouse").get(name)


# --------------------------------------------------------------------------- the swap


class TestTheSwap(_Base):
	def test_it_renames_the_room_then_the_group_with_frappes_rename(self):
		printed, reports = self._run()
		self.assertEqual(reports, [], "a clean run writes no Error Log row")
		self.assertEqual(STATE["errors"], [])
		self.assertEqual(
			STATE["renames"],
			[
				{
					"doctype": "Warehouse",
					"old": ROOM,
					"new": RECEIVING,
					"force": True,
					"merge": False,
					"show_alert": False,
					"rebuild_search": False,
				},
				{
					"doctype": "Warehouse",
					"old": GROUP,
					"new": ROOM,
					"force": True,
					"merge": False,
					"show_alert": False,
					"rebuild_search": False,
				},
			],
		)
		self.assertIn("1. The room: Inventory Room becomes Receiving: done.", printed)
		self.assertIn("3. The group: Inventory becomes Inventory Room: done.", printed)
		self.assertIn("5. The search index: done.", printed)
		self.assertEqual(STATE["indexed"], ["Warehouse"], "indexed once, here, after both renames")

	def test_each_rename_sets_the_label_people_read(self):
		self._run()
		self.assertEqual(self.wh(RECEIVING).warehouse_name, "Receiving")
		self.assertEqual(self.wh(ROOM).warehouse_name, "Inventory Room")
		self.assertIsNone(self.wh(GROUP))
		labels = [v for v in STATE["set_values"] if v[0] == "Warehouse"]
		self.assertEqual(
			labels,
			[
				("Warehouse", RECEIVING, {"warehouse_name": "Receiving"}, False),
				("Warehouse", ROOM, {"warehouse_name": "Inventory Room"}, False),
			],
			"only the label, and a migration is not somebody's edit",
		)
		self.assertIn(("Warehouse", RECEIVING), STATE["cache_cleared"])
		self.assertIn(("Warehouse", ROOM), STATE["cache_cleared"])

	def test_the_tree_does_not_change_only_the_names(self):
		self._run()
		group, receiving = self.wh(ROOM), self.wh(RECEIVING)
		self.assertEqual((group.is_group, group.parent_warehouse, group.disabled), (1, ROOT, 0))
		self.assertEqual((receiving.is_group, receiving.parent_warehouse, receiving.disabled), (0, ROOM, 0))
		for child in (ROW, BONEYARD, RECEIVING):
			self.assertEqual(self.wh(child).parent_warehouse, ROOM, child)
			self.assertEqual(self.wh(child).old_parent, ROOM, f"{child}: old_parent is a Link too")
		self.assertEqual(self.wh(BAY).parent_warehouse, ROW)
		self.assertEqual(self.wh(BIN).parent_warehouse, BAY)
		self.assertEqual(self.wh(SHELF).parent_warehouse, ROOT)
		self.assertEqual(len(_table("Warehouse")), 8, "nothing created, nothing deleted")

	def test_everything_that_pointed_at_the_room_points_at_receiving(self):
		self._run()
		self.assertEqual(_table("Bin")["BIN-1"].warehouse, RECEIVING)
		self.assertEqual(_table("Bin")["BIN-2"].warehouse, RECEIVING)
		self.assertEqual(_table("Stock Ledger Entry")["SLE-1"].warehouse, RECEIVING)
		self.assertEqual(_table("Item Default")["ID-1"].default_warehouse, RECEIVING)
		self.assertEqual(_table("Putaway Rule")["PR-1"].warehouse, RECEIVING)
		self.assertEqual(_table("Item Reorder")["IR-1"].warehouse, RECEIVING)
		self.assertEqual(_table("Purchase Order Item")["POI-1"].warehouse, RECEIVING)
		self.assertEqual(_table("Warehouse Floor Map Shape")["SHAPE-1"].warehouse, RECEIVING)
		self.assertEqual(STATE["singles"][(SETTINGS, "overhead_receiving_warehouse")], RECEIVING)
		# No stock moved, and the rule stayed enabled.
		self.assertEqual([b.actual_qty for b in _table("Bin").values()], [5.0, 0.0, 3.0])
		self.assertEqual(_table("Putaway Rule")["PR-1"].disable, 0)

	def test_what_named_the_group_names_the_inventory_room(self):
		self._run()
		self.assertEqual(_table("Item Reorder")["IR-2"].warehouse_group, ROOM)
		# The bin's own rows are untouched.
		self.assertEqual(_table("Item Reorder")["IR-2"].warehouse, BIN)
		self.assertEqual(_table("Bin")["BIN-3"].warehouse, BIN)
		self.assertEqual(_table("Item Default")["ID-2"].default_warehouse, BIN)
		self.assertEqual(_table("Warehouse Floor Map Shape")["SHAPE-2"].warehouse, BAY)
		self.assertEqual(STATE["singles"][(SETTINGS, "project_shelf_warehouse")], SHELF)

	def test_nothing_that_meant_the_room_ends_up_on_the_group(self):
		"""The one way this patch could do harm: a row that named the room naming the whole shop."""
		self._run()
		on_group = [
			(doctype, row.name, field)
			for doctype, field in LINKS
			if doctype != "Warehouse"
			for row in _table(doctype).values()
			if row.get(field) == ROOM and not (doctype == "Item Reorder" and field == "warehouse_group")
		]
		self.assertEqual(on_group, [])
		self.assertNotIn(ROOM, STATE["singles"].values())

	def test_twice_is_once(self):
		self._run()
		before = _snapshot()
		writes = len(STATE["set_values"])
		printed, reports = self._run()
		self.assertEqual(reports, [])
		self.assertEqual(STATE["errors"], [])
		self.assertEqual(len(STATE["renames"]), 2, "the second run renames nothing")
		self.assertEqual(len(STATE["set_values"]), writes, "and writes nothing")
		self.assertEqual(_snapshot(), before)
		for number in (1, 2, 3, 4):
			self.assertIn(": already.", self._line(printed, number))
		self.assertIn("5. The search index: done.", printed, "a rebuild, so done again")

	def test_a_run_that_stopped_after_the_room_finishes_the_swap(self):
		"""A rehearsal, or a run whose group step failed: the room is Receiving, the group is not
		renamed yet."""
		STATE["fail_rename"] = {GROUP}
		self._run()
		self.assertIn(RECEIVING, _table("Warehouse"))
		self.assertIn(GROUP, _table("Warehouse"), "rolled back to the step's savepoint")
		STATE["fail_rename"] = set()
		STATE["errors"].clear()
		printed, reports = self._run()
		self.assertEqual(reports, [])
		self.assertIn("1. The room: Inventory Room becomes Receiving: already.", printed)
		self.assertIn("3. The group: Inventory becomes Inventory Room: done.", printed)
		self.assertEqual(self.wh(ROOM).is_group, 1)
		self.assertEqual(_table("Item Reorder")["IR-2"].warehouse_group, ROOM)


# --------------------------------------------------------------------------- saved list filters


class TestSavedFilters(_Base):
	def _people(self):
		STATE["user_settings"] = [
			_settings_row("pat@example.com", "Bin", [["Bin", "warehouse", "=", ROOM]]),
			_settings_row(
				"pat@example.com", "Stock Ledger Entry", [["Stock Ledger Entry", "warehouse", "=", GROUP]]
			),
			_settings_row(
				"sam@example.com",
				"Stock Ledger Entry",
				[["Stock Ledger Entry", "warehouse", "in", [ROOM, GROUP, BIN]]],
			),
			_settings_row("sam@example.com", "Item", [["Item", "item_group", "=", "Parts"]]),
			_settings_row(
				"lee@example.com", "Warehouse", [["Warehouse", "name", "like", "%Old Inventory - SF%"]]
			),
		]

	def _saved(self):
		return {
			(row["user"], row["doctype"]): json.loads(row["data"])["List"]["filters"]
			for row in STATE["user_settings"]
		}

	def test_each_rename_carries_the_filters_that_named_it(self):
		self._people()
		printed, reports = self._run()
		self.assertEqual(reports, [])
		saved = self._saved()
		self.assertEqual(saved[("pat@example.com", "Bin")], [["Bin", "warehouse", "=", RECEIVING]])
		self.assertEqual(
			saved[("pat@example.com", "Stock Ledger Entry")], [["Stock Ledger Entry", "warehouse", "=", ROOM]]
		)
		# The room's filter went to Receiving before the group took the room's name.
		self.assertEqual(
			saved[("sam@example.com", "Stock Ledger Entry")],
			[["Stock Ledger Entry", "warehouse", "in", [RECEIVING, ROOM, BIN]]],
		)
		# A row that never named either, and a longer text that only contains one, are untouched.
		self.assertEqual(saved[("sam@example.com", "Item")], [["Item", "item_group", "=", "Parts"]])
		self.assertEqual(
			saved[("lee@example.com", "Warehouse")], [["Warehouse", "name", "like", "%Old Inventory - SF%"]]
		)
		self.assertEqual(
			sorted(set(STATE["cache_dropped"])),
			[
				("_user_settings", "Bin::pat@example.com"),
				("_user_settings", "Stock Ledger Entry::pat@example.com"),
				("_user_settings", "Stock Ledger Entry::sam@example.com"),
			],
		)
		self.assertIn(
			"2. People's saved list filters that named the room: done. 2 saved list settings of 2 people now name Receiving - SF",
			printed,
		)
		self.assertIn(
			"4. People's saved list filters that named the group: done. 2 saved list settings of 2 people now name Inventory Room - SF",
			printed,
		)

	def test_a_second_run_leaves_a_filter_on_the_renamed_group_alone(self):
		"""After the swap, a filter naming Inventory Room - SF means the group, and stays."""
		self._run()
		STATE["user_settings"] = [
			_settings_row(
				"pat@example.com", "Bin", [["Bin", "warehouse", "descendants of (inclusive)", ROOM]]
			)
		]
		before = copy.deepcopy(STATE["user_settings"])
		printed, reports = self._run()
		self.assertEqual(reports, [])
		self.assertEqual(STATE["user_settings"], before)
		self.assertIn("2. People's saved list filters that named the room: already.", printed)

	def test_nothing_the_run_prints_or_logs_names_a_person(self):
		self._people()
		STATE["search_fails"] = True  # one failed step, so the Error Log row is written too
		printed, reports = self._run()
		self.assertEqual(len(reports), 1)
		for text in (printed, *(f"{title} {message}" for title, message in STATE["errors"])):
			self.assertNotIn("@", text)
			self.assertNotIn("example.com", text)

	def test_the_count_is_singular_when_it_is_one(self):
		STATE["user_settings"] = [_settings_row("pat@example.com", "Bin", [["Bin", "warehouse", "=", ROOM]])]
		printed, _reports = self._run()
		self.assertIn("1 saved list setting of 1 person now name Receiving - SF: lists Bin", printed)
		self.assertIn(
			"4. People's saved list filters that named the group: already. no saved list filter names Inventory - SF",
			printed,
		)

	def test_the_group_waits_for_the_rooms_filters(self):
		"""Renaming the group while a filter still names the room would point it at the whole shop."""
		self._people()
		STATE["filters_fail"] = True
		printed, reports = self._run()
		self.assertIn(RECEIVING, _table("Warehouse"), "the room's rename is kept")
		self.assertIn(GROUP, _table("Warehouse"), "the group is not renamed")
		self.assertEqual(len(STATE["renames"]), 1)
		self.assertEqual(len(reports), 1)
		self.assertIn("2. People's saved list filters that named the room: failed. RuntimeError", reports[0])
		self.assertIn(
			"3. The group: Inventory becomes Inventory Room: skipped. the saved list filters that named the room",
			reports[0],
		)
		self.assertIn(patch.RERUN, reports[0])
		# Running it again finishes it, and the room's filter still lands on Receiving.
		STATE["filters_fail"] = False
		STATE["errors"].clear()
		printed, reports = self._run()
		self.assertEqual(reports, [])
		saved = self._saved()
		self.assertEqual(saved[("pat@example.com", "Bin")], [["Bin", "warehouse", "=", RECEIVING]])
		self.assertEqual(
			saved[("pat@example.com", "Stock Ledger Entry")], [["Stock Ledger Entry", "warehouse", "=", ROOM]]
		)
		self.assertEqual(self.wh(ROOM).is_group, 1)

	def test_a_rewrite_never_runs_while_the_old_name_is_a_warehouse(self):
		STATE["user_settings"] = [_settings_row("pat@example.com", "Bin", [["Bin", "warehouse", "=", ROOM]])]
		status, detail = patch.rewrite_saved_filters(ROOM, BIN)
		self.assertEqual(status, patch.ALREADY)
		self.assertIn("a filter naming it means that one", detail)
		self.assertIn(ROOM, STATE["user_settings"][0]["data"])
		self.assertEqual(patch.rewrite_saved_filters(GROUP, "Nowhere - SF")[0], patch.SKIPPED)


# --------------------------------------------------------------------------- never merge, never raise


class TestRefusalsAndFailures(_Base):
	def test_the_group_is_never_renamed_when_the_room_was_not(self):
		STATE["fail_rename"] = {ROOM}
		printed, reports = self._run()
		self.assertEqual([r["old"] for r in STATE["renames"]], [ROOM], "no attempt on the group")
		self.assertEqual(self.wh(ROOM).is_group, 0, "rolled back: the room is still the room")
		self.assertNotIn(RECEIVING, _table("Warehouse"))
		self.assertEqual(_table("Bin")["BIN-1"].warehouse, ROOM, "its links rolled back with it")
		self.assertIn(GROUP, _table("Warehouse"))
		self.assertEqual(len(reports), 1)
		self.assertIn(
			"1. The room: Inventory Room becomes Receiving: failed. RuntimeError: the rename could not finish",
			reports[0],
		)
		self.assertIn(
			"3. The group: Inventory becomes Inventory Room: skipped. the room was not renamed", reports[0]
		)
		self.assertIn("By hand:", reports[0])
		self.assertTrue(
			any(title == "Inventory Room patch: renaming the room failed" for title, _m in STATE["errors"])
		)

	def test_nothing_is_merged_when_receiving_already_exists(self):
		_warehouse(RECEIVING, "Receiving", GROUP)
		printed, reports = self._run()
		self.assertEqual(STATE["renames"], [])
		self.assertIn(
			f"both {ROOM} and {RECEIVING} exist as places that hold stock; nothing is merged", reports[0]
		)
		self.assertIn("3. The group: Inventory becomes Inventory Room: skipped.", reports[0])
		self.assertEqual(self.wh(ROOM).warehouse_name, "Inventory Room")

	def test_a_group_called_receiving_is_left_alone(self):
		_warehouse(RECEIVING, "Receiving", ROOT, is_group=1)
		_printed, reports = self._run()
		self.assertEqual(STATE["renames"], [])
		self.assertIn(f"{RECEIVING} exists and is a group", reports[0])

	def test_a_room_that_is_already_a_group_is_left_alone(self):
		self.wh(ROOM).is_group = 1
		_printed, reports = self._run()
		self.assertEqual(STATE["renames"], [])
		self.assertIn("is already a group and there is no Receiving - SF", reports[0])

	def test_a_site_with_the_room_but_not_the_group_renames_nothing(self):
		del _table("Warehouse")[GROUP]
		_printed, reports = self._run()
		self.assertEqual(STATE["renames"], [])
		self.assertIn(f"there is no group {GROUP} to take the room's name afterwards", reports[0])

	def test_the_group_step_refuses_to_take_a_name_still_held(self):
		"""Reached only by hand: the rules hold on their own, whatever step 1 said."""
		self.assertEqual(
			patch.rename_group(patch.DONE, patch.DONE)[0], patch.SKIPPED
		)  # the room is still a leaf
		self.assertIn("still a place that holds stock", patch.rename_group(patch.DONE, patch.DONE)[1])
		self.assertEqual(STATE["renames"], [])
		for room_step in (patch.SKIPPED, patch.FAILED, patch.ABSENT):
			self.assertEqual(patch.rename_group(room_step, patch.DONE)[0], patch.SKIPPED)

	def test_a_failed_index_undoes_neither_rename(self):
		STATE["search_fails"] = True
		_printed, reports = self._run()
		self.assertIn(RECEIVING, _table("Warehouse"))
		self.assertEqual(self.wh(ROOM).is_group, 1)
		self.assertIn("5. The search index: failed", reports[0])

	def test_it_cannot_raise_even_when_everything_does(self):
		original = FRAPPE.db.get_value
		FRAPPE.db.get_value = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the database is gone"))
		try:
			self._run()
		finally:
			FRAPPE.db.get_value = original
		self.assertTrue(any(title == patch.REPORT_TITLE for title, _m in STATE["errors"]))

	def test_a_site_without_these_warehouses_is_left_alone_and_nobody_is_alerted(self):
		del _table("Warehouse")[ROOM]
		printed, reports = self._run()
		self.assertEqual(reports, [])
		self.assertEqual(STATE["errors"], [], "no Error Log row: there is nothing for anyone to finish")
		self.assertEqual(STATE["renames"], [])
		self.assertEqual(STATE["indexed"], [])
		self.assertEqual(printed.count("not needed here"), len(patch.STEPS))
		self.assertNotIn("By hand:", printed)
		self.assertIn(GROUP, _table("Warehouse"))

	def test_an_empty_site_is_left_alone_too(self):
		STATE["db"]["Warehouse"] = {}
		printed, reports = self._run()
		self.assertEqual((reports, STATE["errors"], STATE["renames"]), ([], [], []))
		self.assertEqual(printed.count("not needed here"), len(patch.STEPS))


# --------------------------------------------------------------------------- what it says


class TestTheOutput(_Base):
	def test_it_says_what_is_left_for_a_person(self):
		printed, _reports = self._run()
		self.assertIn("Where things stand:", printed)
		self.assertIn(f"- In {RECEIVING} now: 1 items, 5 units. No stock was moved.", printed)
		self.assertIn(
			f"- Naming {RECEIVING}: 1 Item Defaults, 1 enabled bin assignments (Putaway Rules), 1 reorder rules.",
			printed,
		)
		self.assertIn(f"({SETTINGS}): {RECEIVING}.", printed)
		self.assertIn(
			f"- Warehouse Map boxes linked to {RECEIVING}: 1. A box drawn as the whole room should be re-linked",
			printed,
		)
		self.assertIn("The QR label printed for Inventory Room now opens the group", printed)
		self.assertIn("Print a new label for Receiving from Warehouse Labels.", printed)
		self.assertIn(
			f"- {ROOM} holds 3 warehouses directly; 1 reorder rules count stock across it.", printed
		)

	def test_no_item_is_named_in_what_it_prints(self):
		"""The build log is not the place for the shop's item list: counts only."""
		STATE["search_fails"] = True
		printed, reports = self._run()
		for text in (printed, *reports):
			for code in (ITEM, ITEM2, ITEM3):
				self.assertNotIn(code, text)

	def test_a_count_that_cannot_be_read_is_left_out(self):
		original = FRAPPE.db.sql
		FRAPPE.db.sql = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no"))
		try:
			lines = patch.counts(RECEIVING, ROOM)
		finally:
			FRAPPE.db.sql = original
		self.assertTrue(all(not line.startswith(f"- In {RECEIVING}") for line in lines))
		self.assertTrue(any("QR label" in line for line in lines))
		self.assertEqual(patch.counts(None, None), [])

	def test_every_step_that_can_stop_has_a_way_to_finish_it(self):
		self.assertEqual(
			[key for key, _label in patch.STEPS], ["room", "room_filters", "group", "group_filters", "search"]
		)
		self.assertEqual(set(patch.RECOVERY), {key for key, _label in patch.STEPS})
		text = patch.report({"room": (patch.DONE, "x")})
		self.assertIn("2. People's saved list filters that named the room: skipped. not reached", text)


# --------------------------------------------------------------------------- the file and its wiring


class TestWiring(unittest.TestCase):
	def test_it_never_merges_deletes_or_reads_singles_through_get_value(self):
		source = PATCH_FILE.read_text(encoding="utf-8")
		tree = ast.parse(source)
		calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
		for call in calls:
			for keyword in call.keywords:
				if keyword.arg == "merge":
					self.fail("rename_doc is never asked to merge")
		code = "\n".join(ast.unparse(node) for node in tree.body if not isinstance(node, ast.Expr))
		self.assertNotIn("delete_doc", code)
		self.assertNotIn("delete from", code.lower())
		for quote in ('"', "'"):
			self.assertNotIn(f"get_value({quote}Singles", code)
		self.assertNotIn("frappe.enqueue", code, "the deploy clears the job queue")
		self.assertIn("force=True", code)
		self.assertIn("rebuild_search=False", code)

	def test_it_is_registered_once_after_the_project_shelf_patch(self):
		lines = [line.strip() for line in (APP / "patches.txt").read_text(encoding="utf-8").splitlines()]
		mine = PATCH_MODULE
		self.assertEqual(lines.count(mine), 1)
		self.assertLess(lines.index("[post_model_sync]"), lines.index(mine))
		self.assertLess(
			lines.index("erpnext_enhancements.patches.rename_stores_to_project_shelf"), lines.index(mine)
		)

	def test_it_has_a_row_in_the_patches_readme(self):
		readme = (APP / "patches" / "README.md").read_text(encoding="utf-8")
		self.assertIn("| `swap_inventory_room_names` | post |", readme)

	def test_ci_runs_this_suite(self):
		ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_swap_inventory_room_names", ci)

	def test_stock_scan_says_a_label_that_opens_a_group_is_out_of_date(self):
		"""The old Inventory Room label opens the group after the swap."""
		tree = ast.parse((APP / "api" / "stock_scan.py").read_text(encoding="utf-8"))
		problem = next(
			n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_location_problem"
		)
		text = ast.unparse(problem)
		self.assertIn("is a group of locations", text)
		self.assertIn("the label is out of date: print a new one from Warehouse Labels", text)


if __name__ == "__main__":
	unittest.main()
