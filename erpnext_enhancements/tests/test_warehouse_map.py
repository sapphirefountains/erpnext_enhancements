# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the Warehouse Map (v1.815.0).

Three layers, in this order:

* ``inventory_enhancements/warehouse_map_rules.py`` runs for real: it has no Frappe in it.
* ``api/warehouse_map.py`` and the ``Warehouse Floor Map`` controller run for real on a small in-memory
  ``frappe`` stub of this suite's own (so it has its own CI step), with stand-ins for the modules
  they lean on: ``api.stock_scan`` (the role gate and the settings), ``bin_assignments`` and
  ``bin_views`` (the bins' counts and kinds; ``tests/test_bin_manager`` and ``tests/test_bin_views``
  run the real ones) and ``job_sites``. The stub's ``Document.save`` asks the write permission and
  runs the real ``validate``, so an endpoint's save is held to the DocType's rules exactly as it
  is on a bench.
* The files around them, read as text: the DocType and page JSON, the stylesheet, ``hooks.py``,
  ``patches.txt``, and the page script's own promises.

What is fenced:

* a box is stored as the named fields only, finite and inside the drawing, and one that cannot
  mean anything is refused in a sentence that says which box;
* a warehouse is on one box and one map; the floor plan picture is a private file or nothing;
* a bay's front view is the tree's shelves and bins in shelf-number order, an empty shelf kept, a
  disabled or transit bin left out;
* every read asks the Stock Scan gate and read on the map before it reads anything; a map being
  drawn (disabled) reads as "no such map" to everyone but its editors;
* a save needs the DocType's own write and create (never ``ignore_permissions``), refuses a
  record changed since the editor opened, and writes nothing the payload merely happens to carry;
* nothing here writes a Warehouse, a Putaway Rule or stock.

Run: python -m unittest erpnext_enhancements.tests.test_warehouse_map -v
"""

import ast
import importlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.inventory_enhancements import stock_scan_rules  # noqa: E402
from erpnext_enhancements.inventory_enhancements import warehouse_map_rules as rules  # noqa: E402

API_SOURCE = APP / "api" / "warehouse_map.py"
CONTROLLER_SOURCE = APP / "inventory_enhancements" / "doctype" / "warehouse_floor_map" / "warehouse_floor_map.py"
PAGE_DIR = APP / "inventory_enhancements" / "page" / "warehouse_map"
NODE_HARNESS = REPO_ROOT / "scripts" / "test_warehouse_map.mjs"

MAP = "Warehouse Floor Map"
SHAPE = "Warehouse Floor Map Shape"

ROOT = "All Warehouses - ZZ"
INV = "Stock - ZZ"
ROW2 = "Lane 7 - ZZ"
BAY = "Bay K1 - ZZ"
BAY2 = "Bay K2 - ZZ"
SHELF1 = "Shelf K1-1 - ZZ"
SHELF2 = "Shelf K1-2 - ZZ"
BIN1 = "Bin K1-2-1 - ZZ"
BIN2 = "Bin K1-2-2 - ZZ"
BIN9 = "Bin K1-2-9 - ZZ"
BIN10 = "Bin K1-2-10 - ZZ"
ROOM = "Parts Room - ZZ"
BONEYARD = "Boneyard - ZZ"
UP = "Mezzanine - ZZ"
NOOK = "Nook Wall - ZZ"
NOOK_ROW = "Nook Row 1 - ZZ"
NOOK_LEFT = "Nook 1 Left - ZZ"
NOOK_CENTER = "Nook 1 Center - ZZ"
NOOK_RIGHT = "Nook 1 Right - ZZ"
NOOK_TOP = "Nook Top - ZZ"
HUES = "Hue Rack - ZZ"
HUE_RED = "Red Row - ZZ"
HUE_AMBER = "Amber Row - ZZ"
HUE_TEAL = "Teal Row - ZZ"
RED1 = "Red-1 - ZZ"
RED2 = "Red-2 - ZZ"
RED10 = "Red-10 - ZZ"
AMBER1 = "Amber-1 - ZZ"
TEAL1 = "Teal-1 - ZZ"
VANS = "Delivery Van - ZZ"
VAN_BIN = "Van Shelf - ZZ"
SITES = "Job Sites - ZZ"
SITE = "Job Site PRJ-90001 Water Wall - ZZ"
TRANSIT = "Goods In Transit - ZZ"


def _tree_rows():
	"""Made-up names in the shapes a real tree has: a group of lanes over bays over shelves over
	numbered bins; two rooms; a wall of nooks whose bins are named Left, Center and Right; a rack
	whose rows are named for colors; a van; a job site; a transit warehouse. Deliberately out of
	order the way a real tree is: bin 10 is created before bin 9, and Center before Left."""
	rows = [
		(ROOT, "All Warehouses", None, 1),
		(INV, "Stock", ROOT, 1),
		(ROW2, "Lane 7", INV, 1),
		(BAY, "Bay K1", ROW2, 1),
		(BAY2, "Bay K2", ROW2, 1),
		(SHELF2, "Shelf K1-2", BAY, 1),
		(SHELF1, "Shelf K1-1", BAY, 1),
		(BIN10, "Bin K1-2-10", SHELF2, 0),
		(BIN2, "Bin K1-2-2", SHELF2, 0),
		(BIN9, "Bin K1-2-9", SHELF2, 0),
		(BIN1, "Bin K1-2-1", SHELF2, 0),
		(ROOM, "Parts Room", INV, 0),
		(BONEYARD, "Boneyard", INV, 0),
		(UP, "Mezzanine", ROOT, 1),
		(NOOK, "Nook Wall", UP, 1),
		(NOOK_ROW, "Nook Row 1", NOOK, 1),
		(NOOK_CENTER, "Nook 1 Center", NOOK_ROW, 0),
		(NOOK_LEFT, "Nook 1 Left", NOOK_ROW, 0),
		(NOOK_RIGHT, "Nook 1 Right", NOOK_ROW, 0),
		(NOOK_TOP, "Nook Top", NOOK, 0),
		(HUES, "Hue Rack", UP, 1),
		(HUE_RED, "Red Row", HUES, 1),
		(HUE_AMBER, "Amber Row", HUES, 1),
		(HUE_TEAL, "Teal Row", HUES, 1),
		(RED10, "Red-10", HUE_RED, 0),
		(RED2, "Red-2", HUE_RED, 0),
		(RED1, "Red-1", HUE_RED, 0),
		(AMBER1, "Amber-1", HUE_AMBER, 0),
		(TEAL1, "Teal-1", HUE_TEAL, 0),
		(VANS, "Delivery Van", ROOT, 1),
		(VAN_BIN, "Van Shelf", VANS, 0),
		(SITES, "Job Sites", ROOT, 1),
		(SITE, "Job Site PRJ-90001 Water Wall", SITES, 0),
		(TRANSIT, "Goods In Transit", ROOT, 0),
	]
	return rows


def _tree(disabled=()):
	return {
		name: {
			"name": name,
			"warehouse_name": label,
			"parent_warehouse": parent,
			"is_group": group,
			"disabled": int(name in disabled),
			"warehouse_type": "Transit" if name == TRANSIT else "",
		}
		for name, label, parent, group in _tree_rows()
	}


def _box(**over):
	box = {"shape_type": "Rack", "warehouse": BAY, "label": "", "x": 10, "y": 20, "w": 40, "h": 100}
	box.update(over)
	return box


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


class TestShapes(unittest.TestCase):
	def test_a_box_is_stored_as_the_named_fields_only(self):
		shape = rules.clean_shape(_box(owner="x", docstatus=1, idx=7, name="abc", levels=[1]), 1200, 800)
		self.assertEqual(
			sorted(shape),
			sorted(
				["shape_type", "warehouse", "label", "x", "y", "w", "h", "rotation", "shelves_from", "bins_from", "bin_order"]
			),
		)
		self.assertEqual((shape["shape_type"], shape["warehouse"], shape["label"]), ("Rack", BAY, None))
		self.assertIsNone(shape["bin_order"])
		self.assertEqual((shape["shelves_from"], shape["bins_from"]), ("Top", "Left"))

	def test_a_box_is_held_inside_the_drawing(self):
		shape = rules.clean_shape(_box(x=1190, y=-50, w=400, h=9000), 1200, 800)
		self.assertEqual((shape["w"], shape["h"]), (400.0, 800.0))
		self.assertEqual((shape["x"], shape["y"]), (800.0, 0.0))
		tiny = rules.clean_shape(_box(w=0.5, h=-3), 1200, 800)
		self.assertEqual((tiny["w"], tiny["h"]), (rules.MIN_SIZE, rules.MIN_SIZE))

	def test_a_turn_is_kept_between_0_and_360(self):
		self.assertEqual(rules.clean_shape(_box(rotation=450), 1200, 800)["rotation"], 90.0)
		self.assertEqual(rules.clean_shape(_box(rotation=-90), 1200, 800)["rotation"], 270.0)
		self.assertEqual(rules.clean_shape(_box(rotation="x"), 1200, 800)["rotation"], 0.0)
		self.assertEqual(rules.clean_shape(_box(rotation=359.99), 1200, 800)["rotation"], 0.0)

	def test_numbers_that_are_not_numbers_are_refused(self):
		for bad in (None, "", "wide", float("nan"), float("inf")):
			with self.assertRaises(rules.MapError) as caught:
				rules.clean_shape(_box(w=bad), 1200, 800, index=2)
			self.assertIn("Box 3", str(caught.exception))

	def test_a_box_that_cannot_mean_anything_is_refused(self):
		cases = [
			(_box(shape_type="Circle"), "a rack, a room or a label"),
			(_box(warehouse=""), "a rack needs"),
			(_box(shape_type="Label", label="  "), "a label needs its words"),
			(_box(shape_type="Room", warehouse="", label=""), "a room needs a name or a warehouse"),
		]
		for box, words in cases:
			with self.assertRaises(rules.MapError) as caught:
				rules.clean_shape(box, 1200, 800)
			self.assertIn(words, str(caught.exception))

	def test_a_room_may_be_a_name_alone_and_a_label_never_holds_a_warehouse(self):
		room = rules.clean_shape(_box(shape_type="Room", warehouse="", label="Office"), 1200, 800)
		self.assertEqual((room["warehouse"], room["label"]), (None, "Office"))
		label = rules.clean_shape(_box(shape_type="Label", label="Dock door"), 1200, 800)
		self.assertIsNone(label["warehouse"])

	def test_facing_is_one_of_two_words(self):
		shape = rules.clean_shape(_box(shelves_from="Bottom", bins_from="Right"), 1200, 800)
		self.assertEqual((shape["shelves_from"], shape["bins_from"]), ("Bottom", "Right"))
		shape = rules.clean_shape(_box(shelves_from="Sideways", bins_from=None), 1200, 800)
		self.assertEqual((shape["shelves_from"], shape["bins_from"]), ("Top", "Left"))

	def test_a_label_is_cut_to_its_length(self):
		shape = rules.clean_shape(_box(label="x" * 200), 1200, 800)
		self.assertEqual(len(shape["label"]), rules.MAX_LABEL)

	def test_one_warehouse_is_on_one_box(self):
		with self.assertRaises(rules.MapError) as caught:
			rules.clean_shapes([_box(), _box(shape_type="Label", label="x"), _box(x=300)], 1200, 800)
		self.assertIn("on this map twice (boxes 1 and 3)", str(caught.exception))

	def test_a_box_tied_to_a_warehouse_that_is_gone_is_refused(self):
		with self.assertRaises(rules.MapError) as caught:
			rules.clean_shapes([_box(warehouse="Bay Z9 - ZZ")], 1200, 800, known={BAY})
		self.assertIn("is not a warehouse any more", str(caught.exception))
		# Without the list of warehouses the check is not made (the pure rules never read).
		self.assertEqual(len(rules.clean_shapes([_box(warehouse="Bay Z9 - ZZ")], 1200, 800)), 1)

	def test_too_many_boxes(self):
		boxes = [_box(shape_type="Label", label=str(n)) for n in range(rules.MAX_SHAPES + 1)]
		with self.assertRaises(rules.MapError):
			rules.clean_shapes(boxes, 1200, 800)

	def test_a_warehouse_is_on_one_map(self):
		shapes = rules.clean_shapes([_box()], 1200, 800)
		self.assertIn("already on the map Upstairs", rules.elsewhere_problem(shapes, {BAY: "Upstairs"}))
		self.assertIsNone(rules.elsewhere_problem(shapes, {BAY2: "Upstairs"}))
		self.assertIsNone(rules.elsewhere_problem(shapes, {}))


class TestTheMapItself(unittest.TestCase):
	def test_a_name_can_be_part_of_an_address(self):
		self.assertIsNone(rules.map_name_problem("Shop Floor"))
		self.assertIsNotNone(rules.map_name_problem("  "))
		self.assertIsNotNone(rules.map_name_problem("x" * (rules.MAX_NAME + 1)))
		for bad in ("A/B", "A?B", "A#B", "A%B", "A&B", "A\\B"):
			self.assertIsNotNone(rules.map_name_problem(bad), bad)

	def test_a_name_the_desk_itself_can_carry(self):
		"""On every route change the Desk builds a jQuery selector from the first two parts of
		the address (``container.js`` ``has_sidebar``, v16.56.0). A double quote in a map's name
		makes it throw before the page is told it is on show, and the page sits on "Loading"."""
		for bad in ('12" Rack Room', "A<B", "A>B", "Line\nBreak", "Tab\tbed", "Bell\x07", ".", ".."):
			self.assertIsNotNone(rules.map_name_problem(bad), repr(bad))
		for fine in ("Bob's Loft", "Room [2]", "Shop Floor (north)", "v1.2", "..."):
			self.assertIsNone(rules.map_name_problem(fine), fine)

	def test_the_drawing_has_a_sane_size(self):
		self.assertEqual(rules.canvas_size(None, 0), (rules.DEFAULT_WIDTH, rules.DEFAULT_HEIGHT))
		self.assertEqual(rules.canvas_size(3, 10**9), (rules.MIN_CANVAS, rules.MAX_CANVAS))
		self.assertEqual(rules.canvas_size("4032", 3024.4), (4032.0, 3024.4))

	def test_the_floor_plan_is_a_private_file_or_nothing(self):
		self.assertIsNone(rules.background_problem(""))
		self.assertIsNone(rules.background_problem(None))
		self.assertIsNone(rules.background_problem("/private/files/plan.png"))
		for bad in (
			"/files/plan.png",
			"https://example.com/plan.png",
			"//example.com/plan.png",
			"data:image/png;base64,AAAA",
			"/private/files/../../files/plan.png",
			"private/files/plan.png",
		):
			self.assertIn("private file", rules.background_problem(bad), bad)


class TestTheTree(unittest.TestCase):
	def test_a_bays_front_view_is_its_shelves_and_bins_in_shelf_order(self):
		levels = rules.levels(BAY, _tree())
		self.assertEqual([level["warehouse"] for level in levels], [SHELF1, SHELF2])
		self.assertEqual([level["label"] for level in levels], ["Shelf K1-1", "Shelf K1-2"])
		# An empty shelf is kept: it is there in the building.
		self.assertEqual(levels[0]["bins"], [])
		# 9 before 10, whatever order the tree made them in.
		self.assertEqual(levels[1]["bins"], [BIN1, BIN2, BIN9, BIN10])

	def test_bins_directly_under_the_box_are_a_first_row_with_no_name(self):
		levels = rules.levels(NOOK, _tree())
		self.assertEqual(levels[0], {"warehouse": NOOK, "label": "", "bins": [NOOK_TOP]})
		self.assertEqual(levels[1]["warehouse"], NOOK_ROW)

	def test_a_bin_has_no_front_view(self):
		self.assertEqual(rules.levels(ROOM, _tree()), [])
		self.assertEqual(rules.levels("Nowhere - ZZ", _tree()), [])

	def test_a_disabled_or_transit_bin_is_never_drawn(self):
		tree = _tree(disabled={BIN2, SHELF1})
		levels = rules.levels(BAY, tree)
		self.assertEqual([level["warehouse"] for level in levels], [SHELF2])
		self.assertEqual(levels[0]["bins"], [BIN1, BIN9, BIN10])
		kids = rules.children_index(tree)
		self.assertEqual(rules.leaves_under(TRANSIT, tree, kids), [])
		self.assertEqual(rules.leaves_under(ROOM, tree, kids), [ROOM])

	def test_the_boxes_worth_placing(self):
		units = rules.units(_tree(), skip={VANS, VAN_BIN, SITES, SITE})
		self.assertEqual(
			[(u["warehouse"], u["suggested_type"], u["bins"]) for u in units],
			[
				# In the tree's order, each level by name: Mezzanine's two racks, then Stock's.
				(HUES, "Rack", 5),
				(NOOK, "Rack", 4),
				(BONEYARD, "Room", 1),
				# A bay is one box: at most two steps above its bins. Lane 7 is not (three).
				(BAY, "Rack", 4),
				# A group with no bins yet is a place, drawn as a room.
				(BAY2, "Room", 0),
				(ROOM, "Room", 1),
			],
		)

	def test_units_leave_out_what_is_not_in_the_building_only_when_told(self):
		names = [u["warehouse"] for u in rules.units(_tree())]
		self.assertIn(VANS, names)
		self.assertIn(SITES, names)
		# Transit is never a place, told or not, and neither is a disabled warehouse.
		self.assertNotIn(TRANSIT, names)
		self.assertNotIn(BAY, [u["warehouse"] for u in rules.units(_tree(disabled={BAY}))])

	def test_a_root_is_never_one_box(self):
		flat = {
			"Root": {"name": "Root", "warehouse_name": "Root", "parent_warehouse": None, "is_group": 1},
			"A": {"name": "A", "warehouse_name": "A", "parent_warehouse": "Root", "is_group": 0},
			"B": {"name": "B", "warehouse_name": "B", "parent_warehouse": "Root", "is_group": 0},
		}
		self.assertEqual([u["warehouse"] for u in rules.units(flat)], ["A", "B"])

	def test_a_bin_is_shown_by_the_nearest_box_above_it(self):
		tree = _tree()
		placed = {BAY: "Shop Floor", ROOM: "Shop Floor", NOOK: "Upstairs"}
		self.assertEqual(rules.locate(BIN9, placed, tree), {"map": "Shop Floor", "shape": BAY})
		self.assertEqual(rules.locate(BAY, placed, tree), {"map": "Shop Floor", "shape": BAY})
		self.assertEqual(rules.locate(NOOK_LEFT, placed, tree), {"map": "Upstairs", "shape": NOOK})
		# A shelf placed on its own wins over its bay: it is nearer.
		placed[SHELF2] = "Shop Floor"
		self.assertEqual(rules.locate(BIN9, placed, tree), {"map": "Shop Floor", "shape": SHELF2})

	def test_a_group_nobody_placed_answers_with_the_map_that_shows_most_of_it(self):
		tree = _tree()
		placed = {BAY: "Shop Floor", ROOM: "Shop Floor", NOOK: "Upstairs"}
		self.assertEqual(rules.locate(ROW2, placed, tree), {"map": "Shop Floor", "shape": None})
		self.assertEqual(rules.locate(ROOT, placed, tree), {"map": "Shop Floor", "shape": None})
		self.assertIsNone(rules.locate(BONEYARD, placed, tree))
		self.assertIsNone(rules.locate("Nowhere - ZZ", placed, tree))
		self.assertIsNone(rules.locate("", placed, tree))

	def test_how_a_unit_is_already_shown(self):
		tree = _tree()
		kids = rules.children_index(tree)
		self.assertEqual(rules.shown_by(BAY, {BAY: "M"}, tree, kids), {"map": "M", "via": [BAY]})
		self.assertEqual(rules.shown_by(BAY, {ROW2: "M"}, tree, kids), {"map": "M", "via": [ROW2]})
		self.assertEqual(
			rules.shown_by(BAY, {SHELF1: "M", SHELF2: "M"}, tree, kids), {"map": "M", "via": sorted([SHELF1, SHELF2])}
		)
		self.assertIsNone(rules.shown_by(BAY, {ROOM: "M"}, tree, kids))

	def test_a_trail_drops_the_root(self):
		tree = _tree()
		self.assertEqual(rules.trail(BIN9, tree), "Stock › Lane 7 › Bay K1 › Shelf K1-2")
		self.assertEqual(rules.trail(INV, tree), "")

	def test_a_loop_in_the_tree_ends(self):
		tree = _tree()
		tree[ROOT]["parent_warehouse"] = BIN9
		self.assertTrue(rules.ancestors(BIN9, tree))
		self.assertIsInstance(rules.units(tree), list)


class TestTheOrderOnTheWall(unittest.TestCase):
	"""F1 of the review of PR #1453. Sorted as words, Left / Center / Right come out Center,
	Left, Right, so a row of nooks was drawn in an order nobody standing in front of it sees, and
	turning the row around (bin 1 at the right) only gave Right, Left, Center."""

	def test_position_words_sort_by_the_place_they_name(self):
		names = ["Nook 1 Center", "Nook 1 Right", "Nook 1 Left"]
		self.assertEqual(sorted(names, key=rules.position_key), ["Nook 1 Left", "Nook 1 Center", "Nook 1 Right"])
		# The plain natural order, which the labels page still uses, is the wrong one here.
		self.assertEqual(sorted(names, key=stock_scan_rules.natural_key)[0], "Nook 1 Center")
		shelves = ["Bottom Shelf", "Top Shelf", "Middle Shelf"]
		self.assertEqual(sorted(shelves, key=rules.position_key), ["Top Shelf", "Middle Shelf", "Bottom Shelf"])
		self.assertEqual(sorted(["Rear", "Front"], key=rules.position_key), ["Front", "Rear"])
		self.assertEqual(
			sorted(["Pod Lower Right", "Pod Upper Left", "Pod Upper Right", "Pod Lower Left"], key=rules.position_key),
			["Pod Upper Left", "Pod Upper Right", "Pod Lower Left", "Pod Lower Right"],
		)

	def test_numbers_still_count(self):
		names = ["Bin K1-2-10", "Bin K1-2-9", "Bin K1-2-1"]
		self.assertEqual(sorted(names, key=rules.position_key), ["Bin K1-2-1", "Bin K1-2-9", "Bin K1-2-10"])
		self.assertEqual(sorted(["Red-10", "Red-2"], key=rules.position_key), ["Red-2", "Red-10"])
		# A word that only contains a position word is a word.
		self.assertEqual(sorted(["Leftovers", "Apex"], key=rules.position_key), ["Apex", "Leftovers"])
		self.assertEqual(rules.position_key(None), [])

	def test_a_row_of_nooks_reads_left_center_right(self):
		"""The tree makes them Center, Left, Right (as production's does)."""
		levels = rules.levels(NOOK, _tree())
		self.assertEqual(levels[1]["bins"], [NOOK_LEFT, NOOK_CENTER, NOOK_RIGHT])
		self.assertEqual(rules.levels(NOOK_ROW, _tree())[0]["bins"], [NOOK_LEFT, NOOK_CENTER, NOOK_RIGHT])

	def test_no_rule_knows_the_order_of_colors_so_a_box_carries_its_own(self):
		tree = _tree()
		auto = rules.levels(HUES, tree)
		self.assertEqual([level["warehouse"] for level in auto], [HUE_AMBER, HUE_RED, HUE_TEAL])
		self.assertEqual(auto[1]["bins"], [RED1, RED2, RED10])
		order = rules.clean_order("\n".join([HUE_TEAL, HUE_RED, RED10, RED1]))
		own = rules.levels(HUES, tree, order=order)
		self.assertEqual([level["warehouse"] for level in own], [HUE_TEAL, HUE_RED, HUE_AMBER])
		# Listed bins first, in the box's order; a bin it does not list follows in the rule's.
		self.assertEqual(own[1]["bins"], [RED10, RED1, RED2])
		self.assertEqual(own[2]["bins"], [AMBER1])

	def test_a_box_order_is_laid_over_the_rule(self):
		self.assertEqual(rules.ordered(["a", "b", "c", "d"], ["c", "gone", "a"]), ["c", "a", "b", "d"])
		self.assertEqual(rules.ordered(["a", "b"], []), ["a", "b"])
		self.assertEqual(rules.ordered(["a", "b"], None), ["a", "b"])
		self.assertEqual(rules.ordered([], ["a"]), [])

	def test_a_box_order_is_cleaned(self):
		self.assertEqual(rules.clean_order(" b \n\na\nb\n"), ["b", "a"])
		self.assertEqual(rules.clean_order(["b", None, "a", "b", 7]), ["b", "a", "7"])
		self.assertEqual(rules.clean_order(None), [])
		self.assertEqual(rules.clean_order("b\na\nc", known={"a", "c"}), ["a", "c"])
		self.assertEqual(len(rules.clean_order([f"w{n}" for n in range(rules.MAX_ORDER + 50)])), rules.MAX_ORDER)
		shape = rules.clean_shape(_box(warehouse=HUES, bin_order=f"{HUE_TEAL}\n\n{HUE_RED}\n{HUE_TEAL}"), 1200, 800)
		self.assertEqual(shape["bin_order"], f"{HUE_TEAL}\n{HUE_RED}")
		# Only a box that stands for a warehouse has anything inside it to put in order.
		self.assertIsNone(rules.clean_shape(_box(shape_type="Label", label="x", bin_order="a\nb"), 1200, 800)["bin_order"])
		# A name that is no longer a warehouse leaves the order when the map is saved.
		cleaned = rules.clean_shapes([_box(warehouse=HUES, bin_order=f"{HUE_TEAL}\nGone - ZZ")], 1200, 800, known={HUES, HUE_TEAL})
		self.assertEqual(cleaned[0]["bin_order"], HUE_TEAL)

	def test_why_a_warehouse_is_not_drawn(self):
		tree = _tree(disabled={BAY})
		self.assertEqual(rules.missing_reason(None), "deleted")
		self.assertEqual(rules.missing_reason(tree[BAY]), "disabled")
		self.assertEqual(rules.missing_reason(tree[TRANSIT]), "transit")
		self.assertEqual(rules.missing_reason(tree[ROOM]), "")
		self.assertFalse(rules.usable(None))
		self.assertTrue(rules.usable(tree[ROOM]))


class TestColors(unittest.TestCase):
	def test_a_bins_color(self):
		self.assertEqual(rules.bin_status({}), "empty")
		self.assertEqual(rules.bin_status(None), "empty")
		self.assertEqual(rules.bin_status({"on_hand_items": 3}), "stock")
		self.assertEqual(rules.bin_status({"on_hand_items": 3, "unassigned_items": 1}), "problem")
		self.assertEqual(rules.bin_status({"missing_items": 2}), "problem")

	def test_a_problem_anywhere_inside_a_box_shows_from_the_floor(self):
		rows = [{"on_hand_items": 2}, {}, {"on_hand_items": 1, "missing_items": 1}]
		self.assertEqual(
			rules.summarize(rows), {"bins": 3, "stock_bins": 2, "problem_bins": 1, "items": 3, "status": "problem"}
		)
		self.assertEqual(rules.summarize([{"on_hand_items": 2}, {}])["status"], "stock")
		self.assertEqual(rules.summarize([{}, {}])["status"], "empty")
		self.assertEqual(rules.summarize([])["status"], "empty")


# ---------------------------------------------------------------------------
# The endpoints and the controller, on a stub
# ---------------------------------------------------------------------------

_MODULES = (
	"frappe",
	"frappe.utils",
	"frappe.model",
	"frappe.model.document",
	"erpnext_enhancements.api",
	"erpnext_enhancements.api.stock_scan",
	"erpnext_enhancements.api.warehouse_map",
	"erpnext_enhancements.inventory_enhancements.job_sites",
	"erpnext_enhancements.inventory_enhancements.bin_assignments",
	"erpnext_enhancements.inventory_enhancements.bin_views",
	"erpnext_enhancements.inventory_enhancements.doctype.warehouse_floor_map.warehouse_floor_map",
	"erpnext_enhancements.inventory_enhancements.warehouse_map_files",
)
_PACKAGE_ATTRS = ("job_sites", "bin_assignments", "bin_views")

STATE = {}
_ORIGINAL = {}
api = None
files = None
WarehouseMap = None


class _Row(dict):
	__getattr__ = dict.get

	def __setattr__(self, key, value):
		self[key] = value


class _Throw(Exception):
	pass


class _Denied(Exception):
	pass


class _Stale(Exception):
	pass


VIEWER = {(MAP, "read")}
EDITOR = {(MAP, "read"), (MAP, "write"), (MAP, "create"), (MAP, "delete")}


def _reset():
	STATE.clear()
	STATE.update(
		{
			"tree": _tree(),
			"maps": {},
			"bins": {
				BIN1: {"on_hand_items": 2, "assigned_items": 2},
				BIN2: {"on_hand_items": 0, "assigned_items": 1, "missing_items": 1},
				BIN9: {"on_hand_items": 1, "assigned_items": 0, "unassigned_items": 1},
				ROOM: {"on_hand_items": 55, "assigned_items": 50},
				VAN_BIN: {"on_hand_items": 4, "kind": "vehicle"},
				SITE: {"on_hand_items": 0, "kind": "job_site", "project_name": "Water Wall"},
			},
			"locations": {"ELBOW": {BIN9: {"on_hand": 4.0, "assigned": False}, BIN1: {"on_hand": 0.0, "assigned": True}}},
			# (file_url, is_private, attached_to_doctype, attached_to_name)
			"files": [],
			"perms": set(VIEWER),
			"access": True,
			"calls": [],
			"writes": [],
			"stamp": 0,
			"decorate_fails": False,
		}
	)


def _stamp():
	STATE["stamp"] += 1
	return f"2026-11-12 10:00:{STATE['stamp']:02d}.000000"


def _seed(name, shapes=(), **fields):
	"""A stored map, written past the rules (as a record made before a rule existed would be)."""
	rows = []
	for n, shape in enumerate(shapes):
		row = _Row(rules.clean_shape(shape, 1200, 800))
		row["name"] = f"{name}-row{n}"
		rows.append(row)
	STATE["maps"][name] = {
		"name": name,
		"map_name": name,
		"sort_order": 0,
		"disabled": 0,
		"background": "",
		"width": 1200.0,
		"height": 800.0,
		"modified": _stamp(),
		"shapes": rows,
		**fields,
	}
	return STATE["maps"][name]


def _install():
	frappe = types.ModuleType("frappe")

	def throw(message, exc=None):
		raise (exc or _Throw)(message)

	def has_permission(doctype, ptype="read", doc=None):
		return (doctype, ptype) in STATE["perms"]

	def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
		STATE["calls"].append(("get_all", doctype))
		filters = filters or {}
		if doctype == "Warehouse":
			rows = [_Row(row) for row in STATE["tree"].values()]
			return [row["name"] for row in rows] if pluck == "name" else rows
		if doctype == MAP:
			return [_Row({key: stored.get(key) for key in fields}) for stored in STATE["maps"].values()]
		if doctype == "File":
			want = (filters["file_url"], filters["is_private"], filters["attached_to_doctype"], filters["attached_to_name"])
			return [_Row(name=f"file{n}") for n, row in enumerate(STATE["files"]) if tuple(row) == want]
		if doctype == SHAPE and "bin_order" in filters:
			needle = filters["bin_order"][1].strip("%")
			return [
				_Row(name=row["name"], bin_order=row.get("bin_order"))
				for stored in STATE["maps"].values()
				for row in stored["shapes"]
				if needle in (row.get("bin_order") or "")
			]
		if doctype == SHAPE:
			assert filters.get("parenttype") == MAP, filters
			op, value = filters["parent"]
			out = []
			for name, stored in STATE["maps"].items():
				if (op == "in" and name not in value) or (op == "!=" and name == value):
					continue
				out.extend(_Row(parent=name, warehouse=row.get("warehouse")) for row in stored["shapes"])
			return out
		raise AssertionError(f"unexpected get_all: {doctype}")

	def exists(doctype, name):
		STATE["calls"].append(("exists", doctype))
		assert doctype == MAP, doctype
		if isinstance(name, dict):
			return any(all(m.get(k) == v for k, v in name.items()) for m in STATE["maps"].values())
		return name in STATE["maps"]

	def set_value(doctype, name, field, value, update_modified=True):
		assert doctype == SHAPE and field == "bin_order" and update_modified is False, (doctype, field)
		STATE["writes"].append(("set_value", name))
		for stored in STATE["maps"].values():
			for row in stored["shapes"]:
				if row["name"] == name:
					row[field] = value

	class Document:
		def __init__(self, data=None):
			self.__dict__.update(data or {})
			self.__dict__.setdefault("doctype", MAP)

		def is_new(self):
			return bool(self.__dict__.get("_new"))

		def __getattr__(self, key):
			return None

		def get(self, key, default=None):
			value = self.__dict__.get(key)
			return default if value is None else value

		def set(self, key, value):
			self.__dict__[key] = value

		def append(self, key, value):
			row = _Row(value)
			rows = self.__dict__.setdefault(key, [])
			row["name"] = f"new-row{len(rows)}"
			rows.append(row)
			return row

		def save(self):
			creating = not self.__dict__.get("name")
			STATE["writes"].append(("save", self.__dict__.get("name") or self.map_name))
			for ptype in ("create", "write") if creating else ("write",):
				if not has_permission(MAP, ptype):
					raise _Denied(f"No {ptype} permission for {MAP}")
			# As Frappe does (document.py insert / _save, v16.56.0): Links are checked BEFORE
			# validate runs, and a Link value that is not text trips an assert.
			for n, row in enumerate(self.__dict__.get("shapes") or [], 1):
				warehouse = row.get("warehouse")
				if warehouse is None:
					continue
				assert isinstance(warehouse, (str, int)), "a Link that is not text"
				if warehouse not in STATE["tree"]:
					raise _Throw(f"Could not find Row #{n}: Warehouse: {warehouse}")
			self.__dict__["_new"] = creating
			try:
				self.validate()
			finally:
				self.__dict__.pop("_new", None)
			if creating:
				self.name = self.map_name
			self.modified = _stamp()
			STATE["maps"][self.name] = {
				key: self.__dict__.get(key)
				for key in ("name", "map_name", "sort_order", "disabled", "background", "width", "height", "modified")
			}
			STATE["maps"][self.name]["shapes"] = [_Row(row) for row in self.__dict__.get("shapes") or []]
			return self

	def get_doc(doctype, name):
		assert doctype == MAP, doctype
		stored = STATE["maps"][name]
		data = dict(stored)
		data["shapes"] = [_Row(row) for row in stored["shapes"]]
		return WarehouseMap(data)

	def new_doc(doctype):
		assert doctype == MAP, doctype
		return WarehouseMap({"shapes": []})

	def rename_doc(doctype, old, new):
		STATE["writes"].append(("rename", old, new))
		if not has_permission(MAP, "write"):
			raise _Denied("No write permission")
		# As Frappe does (rename_doc.py, v16.56.0): before_rename runs, validate does not, and the
		# attached Files follow the record.
		get_doc(MAP, old).before_rename(old, new, False)
		stored = STATE["maps"].pop(old)
		stored.update({"name": new, "map_name": new})
		STATE["maps"][new] = stored
		STATE["files"] = [
			(url, private, doctype, new if (doctype, name) == (MAP, old) else name)
			for url, private, doctype, name in STATE["files"]
		]
		return new

	def delete_doc(doctype, name):
		STATE["writes"].append(("delete", name))
		if not has_permission(MAP, "delete"):
			raise _Denied("No delete permission")
		del STATE["maps"][name]

	frappe.throw = throw
	frappe.has_permission = has_permission
	frappe.get_all = get_all
	frappe.get_doc = get_doc
	frappe.new_doc = new_doc
	frappe.rename_doc = rename_doc
	frappe.delete_doc = delete_doc
	frappe.db = types.SimpleNamespace(exists=exists, set_value=set_value)
	frappe._ = lambda text: text
	frappe.whitelist = lambda *a, **kw: (lambda fn: fn)
	frappe.log_error = lambda *a, **kw: STATE["calls"].append(("log_error", kw.get("title")))
	frappe.PermissionError = _Denied
	frappe.TimestampMismatchError = _Stale
	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.cstr = lambda v: "" if v is None else str(v)
	utils.flt = lambda v, precision=None: float(v or 0)
	frappe.utils = utils
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = Document
	model.document = document
	frappe.model = model
	sys.modules.update(
		{"frappe": frappe, "frappe.utils": utils, "frappe.model": model, "frappe.model.document": document}
	)

	package_api = types.ModuleType("erpnext_enhancements.api")
	package_api.__path__ = []
	scan = types.ModuleType("erpnext_enhancements.api.stock_scan")

	def _check_access():
		STATE["calls"].append(("gate",))
		if not STATE["access"]:
			raise _Denied("You are not permitted to use Stock Scan.")

	scan._check_access = _check_access
	scan.get_settings = lambda: {"boneyard_warehouse": BONEYARD}
	package_api.stock_scan = scan
	sys.modules["erpnext_enhancements.api"] = package_api
	sys.modules["erpnext_enhancements.api.stock_scan"] = scan

	import erpnext_enhancements.inventory_enhancements as package

	assignments = types.ModuleType("erpnext_enhancements.inventory_enhancements.bin_assignments")

	def _bin_rows():
		return [
			_Row(
				name=name,
				warehouse_name=row["warehouse_name"],
				on_hand_items=(STATE["bins"].get(name) or {}).get("on_hand_items", 0),
				assigned_items=(STATE["bins"].get(name) or {}).get("assigned_items", 0),
			)
			for name, row in STATE["tree"].items()
			if not row["is_group"] and not row["disabled"] and row["warehouse_type"] != "Transit"
		]

	assignments._bin_rows = _bin_rows

	bin_views = types.ModuleType("erpnext_enhancements.inventory_enhancements.bin_views")

	def decorate(bins):
		if STATE["decorate_fails"]:
			raise RuntimeError("the kinds could not be read")
		for row in bins:
			extra = STATE["bins"].get(row["warehouse"]) or {}
			row["kind"] = extra.get("kind") or ("boneyard" if row.get("is_boneyard") else "shop_bin")
			row["missing_items"] = extra.get("missing_items", 0)
			row["unassigned_items"] = extra.get("unassigned_items", 0)
			if extra.get("project_name"):
				row["project_name"] = extra["project_name"]
		return {}

	def item_locations(item_code):
		_check_access()
		if item_code not in STATE["locations"]:
			raise _Throw(f"There is no item {item_code}.")
		return {
			"item_code": item_code,
			"item_name": "FITTING, ELBOW",
			"stock_uom": "Unit",
			"is_stock_item": True,
			"locations": STATE["locations"][item_code],
		}

	bin_views.decorate = decorate
	bin_views.item_locations = item_locations
	bin_views._vehicle_leaves = lambda tree: {VANS, VAN_BIN}

	sites = types.ModuleType("erpnext_enhancements.inventory_enhancements.job_sites")
	sites.site_warehouses = lambda: {SITE}
	sites.group = lambda: _Row(name=SITES)

	for attr, module in (("bin_assignments", assignments), ("bin_views", bin_views), ("job_sites", sites)):
		sys.modules[f"erpnext_enhancements.inventory_enhancements.{attr}"] = module
		_ORIGINAL[f"attr:{attr}"] = getattr(package, attr, None)
		setattr(package, attr, module)


def _load(name, path):
	spec = importlib.util.spec_from_file_location(name, path)
	module = importlib.util.module_from_spec(spec)
	sys.modules[name] = module
	spec.loader.exec_module(module)
	return module


def setUpModule():
	global api, files, WarehouseMap
	for name in _MODULES:
		_ORIGINAL[name] = sys.modules.pop(name, None)
	_reset()
	_install()
	controller = _load(
		"erpnext_enhancements.inventory_enhancements.doctype.warehouse_floor_map.warehouse_floor_map", CONTROLLER_SOURCE
	)
	WarehouseMap = controller.WarehouseFloorMap
	api = _load("erpnext_enhancements.api.warehouse_map", API_SOURCE)
	files = _load(
		"erpnext_enhancements.inventory_enhancements.warehouse_map_files",
		APP / "inventory_enhancements" / "warehouse_map_files.py",
	)


def tearDownModule():
	import erpnext_enhancements
	import erpnext_enhancements.inventory_enhancements as package

	for attr in _PACKAGE_ATTRS:
		if hasattr(package, attr):
			delattr(package, attr)
		if _ORIGINAL.get(f"attr:{attr}") is not None:
			setattr(package, attr, _ORIGINAL[f"attr:{attr}"])
	if hasattr(erpnext_enhancements, "api") and _ORIGINAL.get("erpnext_enhancements.api") is None:
		delattr(erpnext_enhancements, "api")
	for name in _MODULES:
		sys.modules.pop(name, None)
		if _ORIGINAL.get(name) is not None:
			sys.modules[name] = _ORIGINAL[name]


class _Base(unittest.TestCase):
	def setUp(self):
		_reset()

	def as_editor(self):
		STATE["perms"] = set(EDITOR)

	def shop_floor(self):
		return _seed(
			"Shop Floor",
			[
				_box(warehouse=BAY, label="K1"),
				_box(shape_type="Room", warehouse=ROOM, x=300, w=150, h=120),
				_box(shape_type="Label", label="Dock door", x=600),
			],
		)


class TestWhoMayLook(_Base):
	READS = (
		("boot", ()),
		("get_map", ("Shop Floor",)),
		("locate", (BIN9,)),
		("find_item", ("ELBOW",)),
		("editor_data", ()),
		("box_levels", (BAY,)),
	)

	def test_every_read_asks_the_gate_before_it_reads_anything(self):
		self.shop_floor()
		self.as_editor()
		for name, args in self.READS:
			STATE["calls"].clear()
			getattr(api, name)(*args)
			self.assertEqual(STATE["calls"][0], ("gate",), name)
			self.assertEqual(STATE["writes"], [], name)

	def test_nobody_outside_the_stock_roles_reads_anything(self):
		self.shop_floor()
		self.as_editor()
		STATE["access"] = False
		for name, args in self.READS:
			STATE["calls"].clear()
			with self.assertRaises(_Denied, msg=name):
				getattr(api, name)(*args)
			self.assertEqual(STATE["calls"], [("gate",)], name)

	def test_a_stock_role_without_read_on_the_map_is_refused(self):
		self.shop_floor()
		STATE["perms"] = set()
		for name, args in self.READS:
			with self.assertRaises(_Denied, msg=name):
				getattr(api, name)(*args)

	def test_the_editors_list_is_the_editors(self):
		self.shop_floor()
		with self.assertRaises(_Denied):
			api.editor_data()
		with self.assertRaises(_Denied):
			api.box_levels(BAY)

	def test_a_map_being_drawn_is_not_announced(self):
		self.shop_floor()
		_seed("Upstairs", [_box(warehouse=NOOK)], disabled=1)
		self.assertEqual([m["name"] for m in api.boot()["maps"]], ["Shop Floor"])
		with self.assertRaises(_Throw) as hidden:
			api.get_map("Upstairs")
		with self.assertRaises(_Throw) as absent:
			api.get_map("Basement")
		self.assertEqual(str(hidden.exception).replace("Upstairs", "X"), str(absent.exception).replace("Basement", "X"))
		# Its boxes do not answer "where is this" either.
		self.assertIsNone(api.locate(NOOK_LEFT)["map"])
		self.as_editor()
		self.assertEqual([m["name"] for m in api.boot()["maps"]], ["Shop Floor", "Upstairs"])
		self.assertEqual(api.get_map("Upstairs")["map"]["disabled"], True)
		self.assertEqual(api.locate(NOOK_LEFT)["map"], "Upstairs")


class TestReads(_Base):
	def test_boot_lists_the_maps_in_order_and_says_who_may_edit(self):
		_seed("Upstairs", sort_order=2)
		_seed("Shop Floor", sort_order=1)
		_seed("Annex 10", sort_order=5)
		_seed("Annex 9", sort_order=5)
		boot = api.boot()
		self.assertEqual([m["title"] for m in boot["maps"]], ["Shop Floor", "Upstairs", "Annex 9", "Annex 10"])
		self.assertFalse(boot["can_edit"])
		self.as_editor()
		self.assertTrue(api.boot()["can_edit"])
		# Write without create is not an editor: a new map could not be saved.
		STATE["perms"] = {(MAP, "read"), (MAP, "write")}
		self.assertFalse(api.boot()["can_edit"])

	def test_a_map_as_the_page_draws_it(self):
		self.shop_floor()
		data = api.get_map("Shop Floor")
		self.assertEqual((data["map"]["width"], data["map"]["height"]), (1200.0, 800.0))
		self.assertTrue(data["map"]["modified"])
		bay, room, label = data["shapes"]
		self.assertEqual((bay["warehouse_name"], bay["label"], bay["is_group"], bay["missing"]), ("Bay K1", "K1", True, False))
		self.assertEqual(bay["trail"], "Stock › Lane 7")
		self.assertEqual([level["label"] for level in bay["levels"]], ["Shelf K1-1", "Shelf K1-2"])
		self.assertEqual(bay["levels"][1]["bins"], [BIN1, BIN2, BIN9, BIN10])
		self.assertEqual(
			bay["summary"], {"bins": 4, "stock_bins": 2, "problem_bins": 2, "items": 3, "status": "problem"}
		)
		self.assertEqual((room["is_group"], room["levels"], room["summary"]["status"]), (False, [], "stock"))
		self.assertEqual((label["warehouse"], label["summary"], label["levels"]), (None, None, []))
		# Every bin under a box, with the Bin Manager's counts and a color.
		self.assertEqual(sorted(data["bins"]), sorted([BIN1, BIN2, BIN9, BIN10, ROOM]))
		self.assertEqual(data["bins"][BIN2]["status"], "problem")
		self.assertEqual(data["bins"][BIN10]["status"], "empty")
		self.assertEqual(data["bins"][BIN1]["warehouse_name"], "Bin K1-2-1")

	def test_vehicles_and_job_sites_are_listed_beside_the_map(self):
		self.shop_floor()
		off = api.get_map("Shop Floor")["off_map"]
		self.assertEqual([(row["kind"], row["warehouse"]) for row in off], [("job_site", SITE), ("vehicle", VAN_BIN)])
		self.assertEqual(off[0]["project_name"], "Water Wall")
		self.assertEqual(off[1]["on_hand_items"], 4)

	def test_a_box_whose_warehouse_is_gone_is_drawn_as_missing(self):
		self.shop_floor()
		del STATE["tree"][ROOM]
		STATE["tree"][BAY]["disabled"] = 1
		bay, room, _label = api.get_map("Shop Floor")["shapes"]
		self.assertEqual((room["missing"], room["warehouse_name"], room["summary"]), (True, ROOM, None))
		self.assertEqual((bay["missing"], bay["levels"], bay["summary"]), (True, [], None))

	def test_the_map_still_draws_when_the_kinds_cannot_be_read(self):
		self.shop_floor()
		STATE["decorate_fails"] = True
		data = api.get_map("Shop Floor")
		self.assertEqual(data["bins"][BIN1]["status"], "stock")
		self.assertEqual(data["off_map"], [])
		self.assertIn(("log_error", "Warehouse Map: the bins' kinds and problem counts could not be read"), STATE["calls"])

	def test_where_a_warehouse_is_shown(self):
		self.shop_floor()
		self.assertEqual(
			api.locate(BIN9),
			{"warehouse": BIN9, "warehouse_name": "Bin K1-2-9", "is_group": False, "map": "Shop Floor", "shape": BAY},
		)
		self.assertEqual(api.locate(ROW2)["shape"], None)
		self.assertEqual(api.locate(ROW2)["map"], "Shop Floor")
		self.assertIsNone(api.locate(BONEYARD)["map"])
		with self.assertRaises(_Throw):
			api.locate("Nowhere - ZZ")

	def test_where_an_item_is(self):
		self.shop_floor()
		STATE["locations"]["ELBOW"][VAN_BIN] = {"on_hand": 9.0, "assigned": False}
		found = api.find_item("ELBOW")
		self.assertEqual((found["item_code"], found["stock_uom"], found["is_stock_item"]), ("ELBOW", "Unit", True))
		# On a map first (most on hand first), then the places no map shows.
		self.assertEqual([p["warehouse"] for p in found["places"]], [BIN9, BIN1, VAN_BIN])
		self.assertEqual(found["places"][0]["shape"], BAY)
		self.assertEqual(found["places"][0]["trail"], "Stock › Lane 7 › Bay K1 › Shelf K1-2")
		self.assertEqual((found["places"][2]["map"], found["places"][2]["on_hand"]), (None, 9.0))
		with self.assertRaises(_Throw):
			api.find_item("NOPE")

	def test_a_boxes_own_order_reaches_the_page(self):
		_seed("Upstairs", [_box(warehouse=HUES, bin_order=f"{HUE_TEAL}\n{HUE_RED}\n{RED10}"), _box(warehouse=NOOK, x=300)])
		hues, nook = api.get_map("Upstairs")["shapes"]
		self.assertEqual(hues["bin_order"], [HUE_TEAL, HUE_RED, RED10])
		self.assertEqual([level["warehouse"] for level in hues["levels"]], [HUE_TEAL, HUE_RED, HUE_AMBER])
		self.assertEqual(hues["levels"][1]["bins"], [RED10, RED1, RED2])
		# With no order of its own a box follows the rule: Left, Center, Right.
		self.assertEqual(nook["bin_order"], [])
		self.assertEqual(nook["levels"][1]["bins"], [NOOK_LEFT, NOOK_CENTER, NOOK_RIGHT])

	def test_the_automatic_order_for_a_box_just_tied(self):
		self.as_editor()
		answer = api.box_levels(HUES)
		self.assertTrue(answer["is_group"])
		self.assertEqual([level["warehouse"] for level in answer["levels"]], [HUE_AMBER, HUE_RED, HUE_TEAL])
		self.assertEqual(answer["names"][HUE_RED], "Red Row")
		self.assertEqual(answer["names"][RED10], "Red-10")
		self.assertEqual(api.box_levels(ROOM), {"is_group": False, "levels": [], "names": {}})
		with self.assertRaises(_Throw):
			api.box_levels("Nowhere - ZZ")

	def test_a_disabled_warehouse_keeps_its_box_and_says_why(self):
		"""F5: the page, the save and the hooks.py comment now agree. Deleted: the box has to go.
		Disabled or Transit: the box stays, is not drawn as a place, and comes back by itself."""
		self.shop_floor()
		STATE["tree"][BAY]["disabled"] = 1
		del STATE["tree"][ROOM]
		bay, room, _label = api.get_map("Shop Floor")["shapes"]
		self.assertEqual((bay["missing"], bay["missing_reason"]), (True, "disabled"))
		self.assertEqual((room["missing"], room["missing_reason"]), (True, "deleted"))
		self.assertEqual(_label["missing_reason"], "")

	def test_what_the_editor_offers(self):
		self.shop_floor()
		_seed("Upstairs", [_box(warehouse=NOOK_ROW)])
		self.as_editor()
		data = api.editor_data()
		units = {u["warehouse"]: u for u in data["units"]}
		# Vehicles and job sites are not in the building.
		self.assertEqual(sorted(units), sorted([BAY, BAY2, BONEYARD, ROOM, NOOK, HUES]))
		self.assertEqual((units[BAY]["on_map"], units[BAY]["via"]), ("Shop Floor", [BAY]))
		self.assertEqual((units[BONEYARD]["on_map"], units[BONEYARD]["via"]), (None, []))
		# A part of the alcove is placed upstairs, so the alcove is not offered whole again.
		self.assertEqual((units[NOOK]["on_map"], units[NOOK]["via"]), ("Upstairs", [NOOK_ROW]))
		self.assertEqual(units[BAY]["trail"], "Stock › Lane 7")
		names = [w["name"] for w in data["warehouses"]]
		self.assertIn(SHELF2, names)
		self.assertNotIn(TRANSIT, names)


class TestSaving(_Base):
	def payload(self, **over):
		data = {"map_name": "Shop Floor", "shapes": [_box()]}
		data.update(over)
		return json.dumps(data)

	def test_a_viewer_cannot_save_and_nothing_is_written(self):
		self.shop_floor()
		before = json.dumps(STATE["maps"], sort_keys=True, default=str)
		for call in (
			lambda: api.save_map(self.payload()),
			lambda: api.save_map(self.payload(), name="Shop Floor"),
			lambda: api.delete_map("Shop Floor"),
		):
			with self.assertRaises(_Denied):
				call()
		self.assertEqual(STATE["writes"], [])
		self.assertEqual(json.dumps(STATE["maps"], sort_keys=True, default=str), before)

	def test_a_new_map(self):
		self.as_editor()
		saved = api.save_map(self.payload(width=4032, height=3024, sort_order="3"))
		self.assertEqual(saved["name"], "Shop Floor")
		stored = STATE["maps"]["Shop Floor"]
		self.assertEqual((stored["width"], stored["height"], stored["sort_order"]), (4032.0, 3024.0, "3"))
		self.assertEqual(stored["shapes"][0]["warehouse"], BAY)
		self.assertEqual(saved["modified"], stored["modified"])
		with self.assertRaises(_Throw) as caught:
			api.save_map(self.payload())
		self.assertIn("already a map called Shop Floor", str(caught.exception))

	def test_only_the_named_fields_are_written(self):
		self.as_editor()
		box = _box(owner="someone", docstatus=2, parent="Other", levels=[{"bins": ["x"]}], summary={"status": "stock"})
		api.save_map(self.payload(shapes=[box], owner="someone", docstatus=1, modified="1999-01-01", name="Hijack"))
		self.assertEqual(list(STATE["maps"]), ["Shop Floor"])
		stored = STATE["maps"]["Shop Floor"]
		self.assertNotIn("owner", stored)
		self.assertNotIn("docstatus", stored)
		self.assertEqual(
			sorted(k for k in stored["shapes"][0] if k != "name"),
			sorted(
				["shape_type", "warehouse", "label", "x", "y", "w", "h", "rotation", "shelves_from", "bins_from", "bin_order"]
			),
		)

	def test_the_whole_list_of_boxes_replaces_what_was_stored(self):
		stored = self.shop_floor()
		self.as_editor()
		api.save_map(self.payload(shapes=[_box(warehouse=BAY2)]), name="Shop Floor", modified=stored["modified"])
		self.assertEqual([row["warehouse"] for row in STATE["maps"]["Shop Floor"]["shapes"]], [BAY2])
		# A payload with no boxes key leaves the boxes alone (a rename, say).
		now = STATE["maps"]["Shop Floor"]["modified"]
		api.save_map(json.dumps({"map_name": "Shop Floor", "sort_order": 4}), name="Shop Floor", modified=now)
		self.assertEqual([row["warehouse"] for row in STATE["maps"]["Shop Floor"]["shapes"]], [BAY2])
		self.assertEqual(STATE["maps"]["Shop Floor"]["sort_order"], 4)

	def test_a_map_changed_since_the_editor_opened_is_not_overwritten(self):
		stored = self.shop_floor()
		self.as_editor()
		opened = stored["modified"]
		api.save_map(self.payload(), name="Shop Floor", modified=opened)
		with self.assertRaises(_Stale) as caught:
			api.save_map(self.payload(shapes=[]), name="Shop Floor", modified=opened)
		self.assertIn("changed by someone else", str(caught.exception))
		self.assertEqual(len(STATE["maps"]["Shop Floor"]["shapes"]), 1)

	def test_the_doctypes_rules_hold_a_save(self):
		self.shop_floor()
		_seed("Upstairs", [_box(warehouse=NOOK)])
		self.as_editor()
		refused = [
			(self.payload(shapes=[_box(), _box(x=200)]), "on this map twice"),
			(self.payload(shapes=[_box(warehouse="Bay Z9 - ZZ")]), "not a warehouse any more"),
			(self.payload(shapes=[_box(warehouse=NOOK)]), "already on the map Upstairs"),
			(self.payload(shapes=[_box(warehouse="")]), "a rack needs"),
			(self.payload(background="/files/plan.png"), "private file"),
			(self.payload(background="https://example.com/a.png"), "private file"),
		]
		before = STATE["maps"]["Shop Floor"]["modified"]
		for payload, words in refused:
			with self.assertRaises(_Throw) as caught:
				api.save_map(payload, name="Shop Floor", modified=before)
			self.assertIn(words, str(caught.exception))
		self.assertEqual(STATE["maps"]["Shop Floor"]["modified"], before)

	def test_the_stale_save_check_cannot_be_skipped(self):
		"""F6: ``modified`` used to be optional, so a caller that left it out overwrote anything."""
		self.shop_floor()
		self.as_editor()
		for stamp in (None, "", "  "):
			with self.assertRaises(_Throw) as caught:
				api.save_map(self.payload(shapes=[]), name="Shop Floor", modified=stamp)
			self.assertIn("could not be read", str(caught.exception))
		self.assertEqual(len(STATE["maps"]["Shop Floor"]["shapes"]), 3)
		self.assertEqual(STATE["writes"], [])

	def test_a_deleted_warehouse_is_refused_in_the_apps_own_words(self):
		"""Frappe checks a row's Links before ``validate`` runs (this suite's stub does the same),
		so without the endpoint cleaning first the editor would read "Could not find Row #1:
		Warehouse: ..." and a value that is not text would be a crash."""
		stored = self.shop_floor()
		self.as_editor()
		with self.assertRaises(_Throw) as caught:
			api.save_map(self.payload(shapes=[_box(warehouse="Gone - ZZ")]), name="Shop Floor", modified=stored["modified"])
		self.assertIn("Box 1: Gone - ZZ is not a warehouse any more", str(caught.exception))
		self.assertNotIn("Could not find", str(caught.exception))
		for junk in (["Bay"], {"a": 1}, 3.5):
			with self.assertRaises(_Throw) as caught:
				api.save_map(self.payload(shapes=[_box(warehouse=junk)]), name="Shop Floor", modified=stored["modified"])
			self.assertIn("is not a warehouse any more", str(caught.exception))
		self.assertEqual(STATE["writes"], [])

	def test_a_disabled_warehouses_box_still_saves(self):
		stored = self.shop_floor()
		self.as_editor()
		STATE["tree"][BAY]["disabled"] = 1
		api.save_map(self.payload(shapes=[_box(warehouse=BAY), _box(warehouse=TRANSIT, x=300)]), name="Shop Floor", modified=stored["modified"])
		self.assertEqual([row["warehouse"] for row in STATE["maps"]["Shop Floor"]["shapes"]], [BAY, TRANSIT])

	def test_a_boxes_own_order_is_stored_clean(self):
		stored = _seed("Upstairs")
		self.as_editor()
		box = _box(warehouse=HUES, bin_order=f"{HUE_TEAL}\n{HUE_TEAL}\n\nGone - ZZ\n{RED10}")
		api.save_map(json.dumps({"map_name": "Upstairs", "shapes": [box]}), name="Upstairs", modified=stored["modified"])
		self.assertEqual(STATE["maps"]["Upstairs"]["shapes"][0]["bin_order"], f"{HUE_TEAL}\n{RED10}")

	def test_the_picture_is_this_maps_own_private_file(self):
		"""F3: the address alone proves nothing about the file behind it."""
		stored = self.shop_floor()
		_seed("Upstairs")
		self.as_editor()
		STATE["files"] = [
			("/private/files/mine.png", 1, MAP, "Shop Floor"),
			("/private/files/theirs.png", 1, MAP, "Upstairs"),
			("/private/files/payslip.png", 1, "Salary Slip", "SAL-0001"),
			("/private/files/turned-public.png", 0, MAP, "Shop Floor"),
		]
		for url in ("/private/files/theirs.png", "/private/files/payslip.png", "/private/files/nothing.png", "/private/files/turned-public.png"):
			with self.assertRaises(_Throw) as caught:
				api.save_map(self.payload(background=url), name="Shop Floor", modified=stored["modified"])
			self.assertIn("has to be a private file uploaded to this map", str(caught.exception), url)
		api.save_map(self.payload(background="/private/files/mine.png"), name="Shop Floor", modified=stored["modified"])
		self.assertEqual(STATE["maps"]["Shop Floor"]["background"], "/private/files/mine.png")
		# A map that is not saved yet has no File of its own to point at.
		with self.assertRaises(_Throw):
			api.save_map(json.dumps({"map_name": "Annex", "background": "/private/files/mine.png"}))
		self.assertNotIn("Annex", STATE["maps"])
		# A rename takes the map's Files with it, so the picture still belongs to it afterwards.
		now = STATE["maps"]["Shop Floor"]["modified"]
		saved = api.save_map(self.payload(map_name="Ground Floor", background="/private/files/mine.png"), name="Shop Floor", modified=now)
		self.assertEqual(STATE["maps"][saved["name"]]["background"], "/private/files/mine.png")

	def test_a_rename_in_the_desk_form_is_held_to_the_name_rule(self):
		"""A rename runs no ``validate``; ``before_rename`` is the only place the rule can stand."""
		self.shop_floor()
		doc = WarehouseMap(dict(STATE["maps"]["Shop Floor"]))
		for bad in ("A/B", 'Bay "A"', "..", ""):
			with self.assertRaises(_Throw):
				doc.before_rename("Shop Floor", bad, False)
		with self.assertRaises(_Throw) as caught:
			doc.before_rename("Shop Floor", "Upstairs", True)
		self.assertIn("cannot be merged", str(caught.exception))
		self.assertIsNone(doc.before_rename("Shop Floor", "Ground Floor", False))

	def test_a_bad_name_or_a_payload_that_is_not_a_map(self):
		self.as_editor()
		for payload in ("not json", "[1, 2]", json.dumps({"map_name": "A", "shapes": "all"})):
			with self.assertRaises(_Throw) as caught:
				api.save_map(payload)
			self.assertIn("could not be read", str(caught.exception))
		for name in ("", "A/B"):
			with self.assertRaises(_Throw):
				api.save_map(json.dumps({"map_name": name}))
		with self.assertRaises(_Throw):
			api.save_map(self.payload(), name="Basement")
		self.assertEqual(STATE["maps"], {})

	def test_a_box_that_is_not_a_dict_is_refused_not_crashed_on(self):
		self.as_editor()
		with self.assertRaises(_Throw):
			api.save_map(self.payload(shapes=["Bay K1", None]))

	def test_a_new_name_renames_the_map(self):
		stored = self.shop_floor()
		_seed("Upstairs")
		self.as_editor()
		saved = api.save_map(self.payload(map_name="Ground Floor"), name="Shop Floor", modified=stored["modified"])
		self.assertEqual(saved["name"], "Ground Floor")
		self.assertEqual(sorted(STATE["maps"]), ["Ground Floor", "Upstairs"])
		self.assertEqual(STATE["maps"]["Ground Floor"]["map_name"], "Ground Floor")
		now = STATE["maps"]["Ground Floor"]["modified"]
		with self.assertRaises(_Throw) as caught:
			api.save_map(self.payload(map_name="Upstairs"), name="Ground Floor", modified=now)
		self.assertIn("already a map called Upstairs", str(caught.exception))
		with self.assertRaises(_Throw):
			api.save_map(self.payload(map_name="A?B"), name="Ground Floor", modified=now)
		# A change of capitals only finds its own row in a database that ignores case; it is a rename.
		saved = api.save_map(self.payload(map_name="GROUND floor"), name="Ground Floor", modified=now)
		self.assertEqual(saved["name"], "GROUND floor")

	def test_deleting_a_map(self):
		self.shop_floor()
		self.as_editor()
		self.assertEqual(api.delete_map("Shop Floor"), {"deleted": "Shop Floor"})
		self.assertEqual(STATE["maps"], {})
		with self.assertRaises(_Throw):
			api.delete_map("Shop Floor")

	def test_the_desk_form_is_held_to_the_same_rules(self):
		"""The controller, saved directly: the way in that never touches the endpoint."""
		self.as_editor()
		doc = WarehouseMap({"map_name": "  Shop Floor  ", "width": 0, "height": None, "shapes": []})
		doc.append("shapes", _box(x=5000, rotation=450, junk="x"))
		doc.save()
		stored = STATE["maps"]["Shop Floor"]
		self.assertEqual((stored["map_name"], stored["width"], stored["height"]), ("Shop Floor", 1200.0, 800.0))
		self.assertEqual((stored["shapes"][0]["x"], stored["shapes"][0]["rotation"]), (1160.0, 90.0))
		bad = WarehouseMap({"map_name": "Annex", "background": "/files/public.png", "shapes": []})
		with self.assertRaises(_Throw):
			bad.save()
		self.assertNotIn("Annex", STATE["maps"])


# ---------------------------------------------------------------------------
# The files around them
# ---------------------------------------------------------------------------


def _calls_with_keyword(source, keyword):
	return [
		node.lineno
		for node in ast.walk(ast.parse(source))
		if isinstance(node, ast.Call) and any(kw.arg == keyword for kw in node.keywords)
	]


class TestTheFiles(unittest.TestCase):
	def test_reads_are_get_and_the_two_writes_are_post(self):
		tree = ast.parse(API_SOURCE.read_text(encoding="utf-8"))
		methods = {}
		for node in tree.body:
			if not isinstance(node, ast.FunctionDef):
				continue
			for decorator in node.decorator_list:
				if isinstance(decorator, ast.Call) and getattr(decorator.func, "attr", "") == "whitelist":
					methods[node.name] = [ast.literal_eval(kw.value) for kw in decorator.keywords if kw.arg == "methods"]
		self.assertEqual(
			methods,
			{
				"boot": [["GET"]],
				"get_map": [["GET"]],
				"locate": [["GET"]],
				"find_item": [["GET"]],
				"editor_data": [["GET"]],
				"box_levels": [["GET"]],
				"save_map": [["POST"]],
				"delete_map": [["POST"]],
			},
		)

	def test_nothing_is_written_past_permissions(self):
		for path in (API_SOURCE, CONTROLLER_SOURCE):
			source = path.read_text(encoding="utf-8")
			self.assertEqual(_calls_with_keyword(source, "ignore_permissions"), [], path.name)
			self.assertEqual(_calls_with_keyword(source, "ignore_links"), [], path.name)

	def test_the_map_writes_no_warehouse_rule_or_stock(self):
		"""Every write in the endpoint file is on the map's own record."""
		tree = ast.parse(API_SOURCE.read_text(encoding="utf-8"))
		writers = {"new_doc", "get_doc", "delete_doc", "rename_doc", "set_value", "sql", "insert"}
		for node in ast.walk(tree):
			if not isinstance(node, ast.Call) or getattr(node.func, "attr", "") not in writers:
				continue
			self.assertTrue(node.args, f"line {node.lineno}")
			first = node.args[0]
			self.assertIsInstance(first, ast.Name, f"line {node.lineno}: a write on something other than MAP")
			self.assertEqual(first.id, "MAP", f"line {node.lineno}")

	def test_the_doctype_gives_looking_to_the_stock_roles_and_changing_to_the_editors(self):
		doctype = json.loads((CONTROLLER_SOURCE.parent / "warehouse_floor_map.json").read_text(encoding="utf-8"))
		self.assertEqual(doctype["module"], "Inventory Enhancements")
		perms = {row["role"]: row for row in doctype["permissions"]}
		self.assertEqual(set(perms), stock_scan_rules.SCAN_ROLES | {rules.EDITOR_ROLE})
		for role in ("System Manager", rules.EDITOR_ROLE):
			self.assertEqual([perms[role].get(p) for p in ("read", "write", "create", "delete")], [1, 1, 1, 1], role)
		for role in stock_scan_rules.SCAN_ROLES - {"System Manager"}:
			self.assertEqual(perms[role].get("read"), 1, role)
			for ptype in ("write", "create", "delete", "submit", "cancel", "import"):
				self.assertFalse(perms[role].get(ptype), f"{role} {ptype}")
		fields = {f["fieldname"]: f for f in doctype["fields"]}
		self.assertEqual(fields["background"]["fieldtype"], "Attach Image")
		self.assertEqual(fields["shapes"]["options"], SHAPE)
		self.assertEqual((doctype["autoname"], fields["map_name"].get("unique")), ("field:map_name", 1))

	def test_a_box_is_a_child_row_that_links_a_warehouse(self):
		path = APP / "inventory_enhancements" / "doctype" / "warehouse_floor_map_shape" / "warehouse_floor_map_shape.json"
		doctype = json.loads(path.read_text(encoding="utf-8"))
		self.assertEqual((doctype["istable"], doctype["module"]), (1, "Inventory Enhancements"))
		fields = {f["fieldname"]: f for f in doctype["fields"]}
		# A Link, not a name kept as text: Frappe carries a warehouse's rename into it.
		self.assertEqual((fields["warehouse"]["fieldtype"], fields["warehouse"]["options"]), ("Link", "Warehouse"))
		self.assertEqual(fields["shape_type"]["options"].split("\n"), list(rules.SHAPE_TYPES))
		self.assertEqual(fields["shelves_from"]["options"].split("\n"), [rules.TOP, rules.BOTTOM])
		self.assertEqual(fields["bins_from"]["options"].split("\n"), [rules.LEFT, rules.RIGHT])
		stored = set(rules.clean_shape(_box(), 1200, 800))
		self.assertEqual(stored, {f["fieldname"] for f in doctype["fields"] if "Break" not in f["fieldtype"]})
		self.assertEqual(stored, set(api._SHAPE_FIELDS))

	def test_the_fields_the_review_asked_for(self):
		parent = json.loads((CONTROLLER_SOURCE.parent / "warehouse_floor_map.json").read_text(encoding="utf-8"))
		fields = {f["fieldname"]: f for f in parent["fields"]}
		# The Desk form cannot upload a picture at all: its Attach dialog has the Private switch.
		self.assertEqual(fields["background"].get("read_only"), 1)
		# The claim about a hidden map is the one the permissions keep.
		self.assertIn("This list still shows it", fields["disabled"]["description"])
		path = APP / "inventory_enhancements" / "doctype" / "warehouse_floor_map_shape" / "warehouse_floor_map_shape.json"
		child = {f["fieldname"]: f for f in json.loads(path.read_text(encoding="utf-8"))["fields"]}
		self.assertEqual(child["bin_order"]["fieldtype"], "Small Text")
		# A User Permission on Warehouse must not make a whole map, and its picture, unreadable.
		self.assertEqual(child["warehouse"].get("ignore_user_permissions"), 1)

	def test_the_hooks_that_guard_the_picture_and_follow_a_rename(self):
		tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
		events = {}
		for node in tree.body:
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "doc_events":
				for key, value in zip(node.value.keys, node.value.values):
					if isinstance(key, ast.Constant) and key.value in ("File", "Warehouse"):
						events[key.value] = ast.literal_eval(value)
		module = "erpnext_enhancements.inventory_enhancements.warehouse_map_files."
		self.assertEqual(events["File"]["validate"], module + "refuse_public_file")
		self.assertEqual(events["Warehouse"], {"after_rename": module + "follow_warehouse_rename"})
		for name in ("refuse_public_file", "follow_warehouse_rename", "has_visible_map"):
			self.assertTrue(callable(getattr(files, name)), name)

	def test_every_door_waits_for_a_map(self):
		"""F9: Stock Scan hid its link until a map existed and the Desk's doors did not."""
		boot = (APP / "boot.py").read_text(encoding="utf-8")
		self.assertIn("bootinfo.ee_warehouse_map = _warehouse_map_flag()", boot)
		body = boot[boot.index("def _warehouse_map_flag()") :]
		body = body[: body.index("\ndef ", 10)]
		self.assertIn("except Exception:", body)
		self.assertIn("return 0", body)
		for path in (
			"public/js/warehouse_map_links.js",
			"public/js/warehouse_stock_scan.js",
			"inventory_enhancements/page/bin_manager/bin_manager.js",
			"inventory_enhancements/report/where_is_this_item/where_is_this_item.js",
			"inventory_enhancements/report/location_contents/location_contents.js",
		):
			self.assertIn("frappe.boot.ee_warehouse_map", (APP / path).read_text(encoding="utf-8"), path)

	def test_a_map_never_stops_a_warehouse_being_deleted(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		names = []
		for node in ast.parse(hooks).body:
			target = None
			if isinstance(node, ast.Assign) and len(node.targets) == 1:
				target = node.targets[0]
			elif isinstance(node, ast.AugAssign):
				target = node.target
			if isinstance(target, ast.Name) and target.id == "ignore_links_on_delete":
				names.extend(ast.literal_eval(node.value))
		# Both: Frappe skips the child DocType that holds the Link, and the parent it reports.
		self.assertIn(MAP, names)
		self.assertIn(SHAPE, names)
		self.assertIn("Planner Draft Change", names)

	def test_the_role_exists_before_the_doctype_names_it(self):
		lines = (APP / "patches.txt").read_text(encoding="utf-8").splitlines()
		patch = "erpnext_enhancements.patches.seed_warehouse_map_editor_role"
		self.assertEqual(lines.count(patch), 1)
		self.assertLess(lines.index(patch), lines.index("[post_model_sync]"))
		source = (APP / "patches" / "seed_warehouse_map_editor_role.py").read_text(encoding="utf-8")
		self.assertIn(f'ROLE = "{rules.EDITOR_ROLE}"', source)
		# It creates the role and grants it to nobody.
		self.assertNotIn("Has Role", source)
		self.assertNotIn("add_roles", source)

	def test_no_page_shares_its_address_with_a_doctype(self):
		"""Frappe's router makes a route out of every DocType a user can read, by slugging its name,
		and asks "is this a DocType?" before "is this a page?" (``router.js`` ``segment_kind``,
		read at v16.56.0). So a Page named ``warehouse-map`` beside a DocType named ``Warehouse
		Map`` is never reached by anyone who can read the DocType: its address opens the list
		view. This page's DocType was named exactly that until the day it was built. Every Page
		in the app against every DocType in the app."""

		def slug(name):  # router.js slug()
			return name.lower().replace(" ", "-")

		def named(kind, pattern):
			out = []
			for path in APP.glob(pattern):
				try:
					data = json.loads(path.read_text(encoding="utf-8"))
				except ValueError:
					continue
				if isinstance(data, dict) and data.get("doctype") == kind and data.get("name"):
					out.append(data["name"])
			return out

		doctypes = {slug(name): name for name in named("DocType", "**/doctype/*/*.json")}
		pages = named("Page", "**/page/*/*.json")
		self.assertIn(slug(MAP), doctypes)
		self.assertIn("warehouse-map", pages)
		clashes = {page: doctypes[page] for page in pages if page in doctypes}
		self.assertEqual(clashes, {}, "a Page whose address is a DocType's list view is never reached")

	def test_the_page_opens_to_the_stock_scan_roles(self):
		page = json.loads((PAGE_DIR / "warehouse_map.json").read_text(encoding="utf-8"))
		self.assertEqual({row["role"] for row in page["roles"]}, stock_scan_rules.SCAN_ROLES)
		self.assertEqual((page["name"], page["module"]), ("warehouse-map", "Inventory Enhancements"))

	def test_every_selector_is_the_pages_own(self):
		css = re.sub(r"/\*.*?\*/", "", (PAGE_DIR / "warehouse_map.css").read_text(encoding="utf-8"), flags=re.S)
		for block in re.findall(r"([^{}]+)\{", css):
			selector = block.strip()
			if not selector or selector.startswith("@") or re.fullmatch(r"\d+%|from|to", selector):
				continue
			for part in selector.split(","):
				part = part.strip()
				self.assertTrue(
					part.startswith(".wm-") or part.startswith('[data-theme="dark"] .wm-'),
					f"a selector outside .wm-: {part!r}",
				)

	def test_the_page_script_keeps_its_promises(self):
		source = (PAGE_DIR / "warehouse_map.js").read_text(encoding="utf-8")
		code = re.sub(r"^\s*//.*$", "", source, flags=re.M)
		# The address's query is read from frappe.route_options, never from the location.
		self.assertNotIn("location.search", code)
		self.assertIn("frappe.route_options = null", code)
		# An array and an options object as two arguments is not a form the router unwraps.
		self.assertNotRegex(code, r"frappe\.set_route\(\s*route\s*,")
		self.assertIn("frappe.set_route(...route, options)", code)
		# The bundle, never a raw /assets path.
		self.assertIn('"warehouse_map.bundle.js"', code)
		self.assertNotIn("/assets/erpnext_enhancements/js/warehouse_map", code)
		self.assertTrue((APP / "public" / "js" / "warehouse_map.bundle.js").exists())
		# The picture is sent by the page itself, always private, attached to the map's own record,
		# and never through Frappe's upload dialog: that dialog offers a Private switch and a web
		# link (a public floor plan), and has the server shrink any picture over 200 KB to at most
		# 1024 by 768 (FileUploader.vue and utils/image.py, v16.56.0).
		self.assertNotIn("frappe.ui.FileUploader(", code)
		self.assertIn('form.append("is_private", "1")', code)
		self.assertIn('form.append("doctype", "Warehouse Floor Map")', code)
		self.assertIn('"/api/method/upload_file"', code)
		self.assertNotRegex(code, r"""append\(\s*["']optimize["']""")
		# Every screen is a route, and the Desk scrolls to the top on every route unless told not to.
		self.assertIn("wrapper.disable_scroll_to_top = true", code)

	def test_the_doors_into_the_map(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn('"public/js/warehouse_map_links.js"', hooks)
		doors = {
			"public/js/warehouse_map_links.js": 'frappe.set_route("warehouse-map", { item: frm.doc.name })',
			"public/js/warehouse_stock_scan.js": 'frappe.set_route("warehouse-map", { w: frm.doc.name })',
			"inventory_enhancements/page/bin_manager/bin_manager.js": 'data-act="open-map"',
			"inventory_enhancements/report/where_is_this_item/where_is_this_item.js": 'frappe.set_route("warehouse-map", { item })',
			"inventory_enhancements/report/location_contents/location_contents.js": 'frappe.set_route("warehouse-map", options)',
			"public/js/stock_scan/app.js": "this.mapLink(",
		}
		for path, needle in doors.items():
			self.assertIn(needle, (APP / path).read_text(encoding="utf-8"), path)
		# Stock Scan offers the link only when there is a map, and its boot can never fail on it.
		scan_source = (APP / "api" / "stock_scan.py").read_text(encoding="utf-8")
		self.assertIn('"warehouse_map": 1 if _has_warehouse_map() else 0', scan_source)
		body = scan_source[scan_source.index("def _has_warehouse_map()") :]
		body = body[: body.index("\ndef ", 10)]
		self.assertIn("except Exception:", body)
		self.assertIn("return False", body)


class TestThePictureStaysPrivate(_Base):
	"""F2: ``warehouse_map_files.refuse_public_file`` (``File`` ``validate``). The map's own check
	only stopped a public address being stored on the map; the public File itself stayed on the
	site, readable without signing in."""

	def file(self, **over):
		row = {"attached_to_doctype": MAP, "attached_to_name": "Shop Floor", "is_private": 1, "is_folder": 0}
		row.update(over)
		return types.SimpleNamespace(**row)

	def test_a_public_file_on_a_map_is_refused(self):
		for doc in (self.file(is_private=0), self.file(is_private=None), self.file(is_private="0")):
			with self.assertRaises(_Throw) as caught:
				files.refuse_public_file(doc)
			self.assertIn("kept private", str(caught.exception))
		# A web link is not a private file either.
		with self.assertRaises(_Throw):
			files.refuse_public_file(self.file(is_private=0, file_url="https://example.com/plan.png"))

	def test_everything_else_is_left_alone(self):
		self.assertIsNone(files.refuse_public_file(self.file()))
		self.assertIsNone(files.refuse_public_file(self.file(is_private=0, attached_to_doctype="Item")))
		self.assertIsNone(files.refuse_public_file(self.file(is_private=0, attached_to_doctype=None)))
		self.assertIsNone(files.refuse_public_file(self.file(is_private=0, is_folder=1)))
		# A File with none of these attributes at all (a stub, an odd caller) is not this hook's business.
		self.assertIsNone(files.refuse_public_file(types.SimpleNamespace()))
		self.assertEqual(STATE["calls"], [])

	def test_it_reads_nothing_and_writes_nothing(self):
		source = (APP / "inventory_enhancements" / "warehouse_map_files.py").read_text(encoding="utf-8")
		body = source[source.index("def refuse_public_file") : source.index("def follow_warehouse_rename")]
		for call in ("frappe.db", "frappe.get_all", "frappe.get_doc", "delete_doc"):
			self.assertNotIn(call, body, call)


class TestARenameReachesABoxesOrder(_Base):
	def test_the_renamed_warehouse_is_rewritten_where_it_stood(self):
		_seed("Upstairs", [_box(warehouse=HUES, bin_order=f"{HUE_TEAL}\n{HUE_RED}\n{RED10}"), _box(warehouse=NOOK, x=300)])
		files.follow_warehouse_rename(None, "after_rename", HUE_RED, "Crimson Row - ZZ", False)
		rows = STATE["maps"]["Upstairs"]["shapes"]
		self.assertEqual(rows[0]["bin_order"], f"{HUE_TEAL}\nCrimson Row - ZZ\n{RED10}")
		self.assertIsNone(rows[1]["bin_order"])
		self.assertEqual(STATE["writes"], [("set_value", rows[0]["name"])])

	def test_only_a_whole_line_is_a_name(self):
		_seed("Upstairs", [_box(warehouse=HUES, bin_order=f"{RED10}\n{RED1}")])
		# "Red-1 - ZZ" is inside "Red-10 - ZZ" as text; only the line that IS the name changes.
		files.follow_warehouse_rename(None, "after_rename", "Red-1", "Rouge-1", False)
		self.assertEqual(STATE["maps"]["Upstairs"]["shapes"][0]["bin_order"], f"{RED10}\n{RED1}")
		self.assertEqual(STATE["writes"], [])

	def test_a_merge_lists_the_place_once(self):
		_seed("Upstairs", [_box(warehouse=HUES, bin_order=f"{RED1}\n{RED2}\n{RED10}")])
		files.follow_warehouse_rename(None, "after_rename", RED2, RED1, True)
		self.assertEqual(STATE["maps"]["Upstairs"]["shapes"][0]["bin_order"], f"{RED1}\n{RED10}")

	def test_it_cannot_be_why_a_rename_failed(self):
		_seed("Upstairs", [_box(warehouse=HUES, bin_order=HUE_RED)])
		held = sys.modules["frappe"].get_all
		sys.modules["frappe"].get_all = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no table yet"))
		try:
			self.assertIsNone(files.follow_warehouse_rename(None, "after_rename", HUE_RED, "Crimson Row - ZZ"))
		finally:
			sys.modules["frappe"].get_all = held
		self.assertIn(("log_error", "Warehouse Map: a renamed warehouse was not carried into a box's order"), STATE["calls"])
		for old, new in ((None, "x"), ("x", None), ("x", "x"), ("", "")):
			self.assertIsNone(files.follow_warehouse_rename(None, "after_rename", old, new))

	def test_the_boot_flag_never_raises(self):
		self.assertFalse(files.has_visible_map())
		_seed("Draft", disabled=1)
		self.assertFalse(files.has_visible_map())
		_seed("Shop Floor")
		self.assertTrue(files.has_visible_map())
		held = sys.modules["frappe"].db.exists
		sys.modules["frappe"].db.exists = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no table yet"))
		try:
			self.assertFalse(files.has_visible_map())
		finally:
			sys.modules["frappe"].db.exists = held


class TestTheRolePatch(unittest.TestCase):
	"""``patches/seed_warehouse_map_editor_role`` on a frappe of its own: it runs pre-model-sync,
	inside the deploy's migrate, so the one thing it must never do is raise."""

	def run_patch(self, existing=(), fail_on=()):
		log = {"inserted": [], "commits": 0, "rollbacks": 0, "errors": []}
		existing = set(existing)

		class Doc:
			def __init__(self, doctype):
				self.doctype = doctype
				self.roles = []

			def append(self, key, row):
				self.roles.append(row)

			def insert(self, ignore_permissions=False):
				assert ignore_permissions, "a migrate has no session user to hold a permission"
				if self.doctype in fail_on:
					raise RuntimeError(f"{self.doctype} could not be inserted")
				name = getattr(self, "role_name", None) or getattr(self, "role_profile", None)
				existing.add((self.doctype, name))
				log["inserted"].append((self.doctype, name, getattr(self, "desk_access", None), list(self.roles)))

		def bump(key):
			log[key] += 1

		fake = types.ModuleType("frappe")
		fake.db = types.SimpleNamespace(
			exists=lambda doctype, name: (doctype, name) in existing,
			commit=lambda: bump("commits"),
			rollback=lambda: bump("rollbacks"),
		)
		fake.new_doc = Doc
		fake.get_traceback = lambda: "traceback"
		fake.log_error = lambda message, title: log["errors"].append(title)
		held = sys.modules.get("frappe")
		sys.modules["frappe"] = fake
		try:
			spec = importlib.util.spec_from_file_location(
				"seed_warehouse_map_editor_role_under_test", APP / "patches" / "seed_warehouse_map_editor_role.py"
			)
			module = importlib.util.module_from_spec(spec)
			spec.loader.exec_module(module)
			module.execute()
		finally:
			sys.modules["frappe"] = held
		return log

	def test_it_creates_the_role_and_a_one_role_profile(self):
		role = rules.EDITOR_ROLE
		log = self.run_patch(existing={("DocType", "Role Profile")})
		self.assertEqual(log["inserted"], [("Role", role, 1, []), ("Role Profile", role, None, [{"role": role}])])
		self.assertEqual((log["commits"], log["rollbacks"], log["errors"]), (2, 0, []))

	def test_a_second_run_inserts_nothing(self):
		role = rules.EDITOR_ROLE
		log = self.run_patch(existing={("DocType", "Role Profile"), ("Role", role), ("Role Profile", role)})
		self.assertEqual((log["inserted"], log["errors"]), ([], []))

	def test_a_site_without_role_profiles_gets_the_role_alone(self):
		log = self.run_patch()
		self.assertEqual([row[0] for row in log["inserted"]], ["Role"])

	def test_it_cannot_raise(self):
		for fail_on in ({"Role"}, {"Role Profile"}, {"Role", "Role Profile"}):
			log = self.run_patch(existing={("DocType", "Role Profile")}, fail_on=fail_on)
			self.assertEqual(log["rollbacks"], len(fail_on))
			self.assertEqual(log["errors"], ["Warehouse Map Editor role seed"] * len(fail_on))
		# The role still goes in when only the profile fails.
		log = self.run_patch(existing={("DocType", "Role Profile")}, fail_on={"Role Profile"})
		self.assertEqual([row[0] for row in log["inserted"]], ["Role"])


class TestThePageUnderNode(unittest.TestCase):
	def test_the_node_harness_passes(self):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		result = subprocess.run([node, str(NODE_HARNESS)], capture_output=True, text=True, timeout=120)
		self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-2000:])


if __name__ == "__main__":
	unittest.main()
