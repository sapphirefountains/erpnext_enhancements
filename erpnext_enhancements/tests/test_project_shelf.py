# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the Project Shelf (v1.810.0).

The owner's decision of 2026-11-12: everything ordered for a project is received onto one
shared Project Shelf by default, the person receiving can still put it somewhere else, stock
on the shelf does not count as stock the shop has, and every unit on it belongs to a job.

Layers, cheapest first:

* ``inventory_enhancements/project_shelf_rules.py`` has no Frappe in it and is run directly:
  where a purchase line goes, whose job a move carries, the three-way split of an item's
  on-hand, and the arithmetic that reads the shelf's ledger by job and reports what the tags
  do not account for.
* ``inventory_enhancements/project_shelf.py``, ``api/stock_scan.py``, ``bin_assignments.py``
  and ``procurement_project.py`` run for real on ``tests/test_job_sites``' in-memory ``frappe``
  stub (itself ``tests/test_bin_assignments``', so this suite has its own CI step): the shelf
  is read from one setting; a take there needs a job; a move onto or off it carries one; it
  is never an item's bin; what the page totals and lists leaves it out and names it apart;
  and a purchase line defaults to it, or to the item's own bin on an Overhead project.
* ``patches/rename_stores_to_project_shelf.py``: the rename, the index, the two settings, each
  step on its own, never raising, safe twice.

What it cannot see: Frappe's own ``rename_doc`` rewriting every Link (the stub renames the
row and the links this suite reads), ERPNext writing a receipt line's project onto its ledger
row, and the stock a submitted voucher really moves. Those need a bench; the hand-back says
what the rehearsal on the production mirror should check.

Run: python -m unittest erpnext_enhancements.tests.test_project_shelf -v
"""

import ast
import json
import sys
import types
import unittest
from pathlib import Path

from erpnext_enhancements.inventory_enhancements import job_site_rules as site_rules
from erpnext_enhancements.inventory_enhancements import project_shelf_rules as rules
from erpnext_enhancements.tests import test_bin_assignments as base
from erpnext_enhancements.tests import test_job_sites as sites_base

APP = Path(__file__).resolve().parents[1]
SETTINGS_JSON = (
	APP / "inventory_enhancements" / "doctype" / "inventory_scanner_settings" / "inventory_scanner_settings.json"
)
SETTINGS_PY = SETTINGS_JSON.with_suffix(".py")
PROCUREMENT = APP / "procurement_project.py"
RECEIVE = APP / "api" / "procurement.py"
REORDER = APP / "inventory_enhancements" / "reorder_board.py"
PATCHES_TXT = APP / "patches.txt"
HOOKS = APP / "hooks.py"
CI = APP.parent / ".github" / "workflows" / "ci.yml"

BIN_A, BIN_B, STORES, BONEYARD = base.BIN_A, base.BIN_B, base.STORES, base.BONEYARD
STATE = base.STATE
COMPANY = base.COMPANY
SHELF = "Project Shelf - SF"
ROOM = "Inventory Room - SF"
ITEM = "PDT-0001"
BUILD = sites_base.BUILD
SITE = sites_base.SITE
JOB_A = "PRJ-90001"  # the Build job the shared reset makes ("Water Wall")
JOB_B = "PRJ-90002"
DONE_JOB = "PRJ-90003"
OVERHEAD = "PRJ-90700"
SETTINGS = "Inventory Scanner Settings"

_OWN_MODULES = (
	"erpnext_enhancements.inventory_enhancements.project_shelf",
	"erpnext_enhancements.procurement_project",
	"erpnext_enhancements.patches.rename_stores_to_project_shelf",
	"frappe.model",
	"frappe.model.rename_doc",
	"frappe.utils.global_search",
)

#: The tables whose Warehouse links the stub's rename follows. Frappe's follows every Link.
_LINKS = (
	("Item Default", "default_warehouse"),
	("Bin", "warehouse"),
	("Putaway Rule", "warehouse"),
	("Company", "default_warehouse"),
)

shelf = None
scan = None
bins = None
sites = None
procurement = None
patch = None
FRAPPE = None
_ORIGINAL = {}


def setUpModule():
	global shelf, scan, bins, sites, procurement, patch, FRAPPE
	for name in _OWN_MODULES:
		sys.modules.pop(name, None)
	sites_base.setUpModule()
	FRAPPE = base.FRAPPE
	scan, bins, sites = base.scan, base.bins, sites_base.sites

	_ORIGINAL["sql"] = FRAPPE.db.sql

	def sql(query, values=None, as_dict=False, **kwargs):
		text = " ".join(query.split())
		if "from `tabStock Ledger Entry`" in text:
			if STATE.get("ledger_fails"):
				raise RuntimeError("the ledger could not be read")
			rows = [
				r
				for r in STATE.get("ledger", [])
				if r.get("warehouse", SHELF) == values["warehouse"]
				and (not values.get("item_code") or r["item_code"] == values["item_code"])
			]
			return [base._dict(r) for r in rows]
		if text.startswith("select pr.warehouse from `tabPutaway Rule` pr"):
			rows = [
				r
				for r in base._table("Putaway Rule").values()
				if r["item_code"] == values["item"] and not r.get("disable")
			]
			rows.sort(
				key=lambda r: (r.get("priority") or 1, -base._bin_qty(r["item_code"], r["warehouse"]), r["warehouse"])
			)
			return [(r["warehouse"],) for r in rows]
		if text.startswith("select count(*)") or "from `tabPurchase Order Item` poi" in text:
			return [STATE.get("count_row", (0, 0, 0))]
		if "`__UserSettings`" in text:
			if STATE.get("filters_fail"):
				raise RuntimeError("the saved filters could not be read")
			saved = STATE.setdefault("user_settings", [])
			if text.startswith("select"):
				needle = values[0].strip("%")
				return [(row["user"], row["doctype"]) for row in saved if needle in row["data"]]
			old, new, like = values
			for row in saved:
				if like.strip("%") in row["data"]:
					row["data"] = row["data"].replace(old, new)
			return []
		return _ORIGINAL["sql"](query, values, as_dict=as_dict, **kwargs)

	FRAPPE.db.sql = sql

	base.Doc.check_permission = lambda self, ptype=None: STATE.setdefault("checked", []).append(
		(self.doctype, ptype)
	)
	FRAPPE.cache = types.SimpleNamespace(
		hdel=lambda name, key: STATE.setdefault("cache_dropped", []).append((name, key))
	)

	# Frappe's rename and its search index, as the patch imports them.
	FRAPPE.get_meta = lambda doctype: types.SimpleNamespace(
		fields=[types.SimpleNamespace(**f) for f in STATE.get("meta_fields", [])]
	)
	sys.modules["frappe.model"] = types.ModuleType("frappe.model")
	rename_module = types.ModuleType("frappe.model.rename_doc")

	def rename_doc(doctype=None, old=None, new=None, force=False, show_alert=True, rebuild_search=True, **kwargs):
		STATE.setdefault("renames", []).append(
			{"doctype": doctype, "old": old, "new": new, "force": force, "show_alert": show_alert, "rebuild_search": rebuild_search}
		)
		if STATE.get("rename_fails"):
			raise RuntimeError("the rename could not finish")
		table = base._table(doctype)
		row = table.pop(old)
		row["name"] = new
		table[new] = row
		for linked, field in _LINKS:
			for other in base._table(linked).values():
				if other.get(field) == old:
					other[field] = new
		return new

	rename_module.rename_doc = rename_doc
	sys.modules["frappe.model.rename_doc"] = rename_module
	search_module = types.ModuleType("frappe.utils.global_search")

	def rebuild_for_doctype(doctype):
		if STATE.get("search_fails"):
			raise RuntimeError("the index could not be rebuilt")
		STATE.setdefault("indexed", []).append(doctype)

	search_module.rebuild_for_doctype = rebuild_for_doctype
	sys.modules["frappe.utils.global_search"] = search_module

	from erpnext_enhancements import procurement_project as procurement_module
	from erpnext_enhancements.inventory_enhancements import project_shelf as shelf_module
	from erpnext_enhancements.patches import rename_stores_to_project_shelf as patch_module

	shelf, procurement, patch = shelf_module, procurement_module, patch_module
	# stock_scan and bin_assignments imported project_shelf under this stub already; one module.
	assert scan.project_shelf is shelf and bins.project_shelf is shelf
	shelf._settings = lambda: dict(STATE["settings"])
	procurement._scanner_settings = lambda: dict(STATE["settings"])


def tearDownModule():
	FRAPPE.db.sql = _ORIGINAL["sql"]
	del base.Doc.check_permission
	del FRAPPE.cache
	for name in _OWN_MODULES:
		sys.modules.pop(name, None)
	sites_base.tearDownModule()


def _project(name, project_name, project_type="Build", status="Active"):
	sites_base._project(name, project_name, project_type=project_type, status=status)


def _reset(shelf_on=True, fallback=True, **settings):
	"""The shared world, with ``Stores`` already renamed: a Project Shelf beside the shop's
	group, an Inventory Room inside it, two jobs, a finished job and an overhead bucket."""
	sites_base._reset(
		project_shelf_warehouse=SHELF if shelf_on else None,
		overhead_receiving_warehouse=ROOM if fallback else None,
		**settings,
	)
	del base._table("Warehouse")[STORES]
	base._warehouse(SHELF, "Project Shelf", parent_warehouse="All Warehouses - SF")
	base._warehouse(ROOM, "Inventory Room")
	_project(JOB_B, "Lobby Fountain")
	_project(DONE_JOB, "Old Courtyard", status="Completed")
	_project(OVERHEAD, "Overhead - Shop & Warehouse", project_type="Overhead")
	STATE["ledger"] = []


def _row(item, qty, project=None, voucher_type="Purchase Receipt", voucher_no="MAT-PRE-0001", after=None, day="2026-11-01"):
	return {
		"item_code": item,
		"project": project,
		"actual_qty": qty,
		"qty_after_transaction": after,
		"voucher_type": voucher_type,
		"voucher_no": voucher_no,
		"posting_date": day,
	}


def _held(item, *pairs):
	"""Put receipts on the shelf's ledger so ``pairs`` of (job, qty) hold ``item`` there, and
	the matching on-hand row."""
	running = 0.0
	for job, qty in pairs:
		running += qty
		STATE["ledger"].append(_row(item, qty, job, after=running))
	base._bin(item, SHELF, running)


class _Base(unittest.TestCase):
	def setUp(self):
		_reset()

	def assertRefused(self, fn, *words, **kwargs):
		with self.assertRaises(FRAPPE.ValidationError) as caught:
			fn(**kwargs)
		for word in words:
			self.assertIn(word, str(caught.exception))
		return str(caught.exception)


# --------------------------------------------------------------------------- the rules


class TestWhereALineGoes(unittest.TestCase):
	def test_a_job_line_goes_to_the_shelf(self):
		self.assertEqual(rules.line_warehouse(True, False, SHELF, BIN_A, ROOM), (SHELF, rules.REASON_SHELF))

	def test_an_overhead_line_goes_to_the_items_own_bin(self):
		self.assertEqual(rules.line_warehouse(True, True, SHELF, BIN_A, ROOM), (BIN_A, rules.REASON_OWN_BIN))

	def test_an_overhead_line_with_no_bin_goes_to_the_fallback(self):
		self.assertEqual(rules.line_warehouse(True, True, SHELF, None, ROOM), (ROOM, rules.REASON_FALLBACK))

	def test_an_overhead_line_with_no_bin_and_no_fallback_is_not_decided(self):
		self.assertEqual(rules.line_warehouse(True, True, SHELF, None, None), (None, rules.REASON_NONE))

	def test_no_project_or_no_shelf_decides_nothing(self):
		self.assertEqual(rules.line_warehouse(False, False, SHELF, BIN_A, ROOM), (None, rules.REASON_NONE))
		self.assertEqual(rules.line_warehouse(True, False, None, BIN_A, ROOM), (None, rules.REASON_NONE))
		self.assertEqual(rules.line_warehouse(True, True, "", BIN_A, ROOM), (None, rules.REASON_NONE))

	def test_own_bin_prefers_the_assigned_bin_then_the_default_then_a_reorder_rule(self):
		self.assertEqual(rules.own_bin([BIN_A, BIN_B], ROOM, [BIN_B]), BIN_A)
		self.assertEqual(rules.own_bin([], ROOM, [BIN_B]), ROOM)
		self.assertEqual(rules.own_bin([], None, [BIN_B]), BIN_B)
		self.assertIsNone(rules.own_bin([], None, []))

	def test_own_bin_never_answers_the_shelf_a_site_or_the_boneyard(self):
		"""774 Item Defaults named the shelf on the day it was renamed. None of them is a bin."""
		not_bins = {SHELF, SITE, BONEYARD}
		self.assertIsNone(rules.own_bin([SHELF], SHELF, [SITE], not_bins))
		self.assertEqual(rules.own_bin([SHELF, BIN_B], SHELF, [], not_bins), BIN_B)
		self.assertEqual(rules.own_bin([BONEYARD], ROOM, [], not_bins), ROOM)

	def test_only_a_location_nobody_chose_may_be_defaulted(self):
		self.assertTrue(rules.may_default("", (SHELF, ROOM)))
		self.assertTrue(rules.may_default(None, ()))
		self.assertTrue(rules.may_default(SHELF, (SHELF, None)))
		self.assertFalse(rules.may_default(BIN_A, (SHELF, ROOM)))
		self.assertFalse(rules.may_default(BIN_A, ()))

	def test_an_overhead_line_on_the_shelf_is_the_one_place_nobody_chooses(self):
		self.assertTrue(rules.overhead_on_shelf(SHELF, True, True, SHELF))
		self.assertFalse(rules.overhead_on_shelf(SHELF, True, False, SHELF), "a job's line belongs there")
		self.assertFalse(rules.overhead_on_shelf(BIN_A, True, True, SHELF))
		self.assertFalse(rules.overhead_on_shelf(SHELF, False, True, SHELF))
		self.assertFalse(rules.overhead_on_shelf(SHELF, True, True, None), "no shelf set up")
		self.assertFalse(rules.overhead_on_shelf("", True, True, SHELF))

	def test_the_form_replaces_only_what_erpnext_filled_when_a_line_gets_its_item(self):
		automatic = (SHELF, BIN_A, None)
		self.assertTrue(rules.form_may_default("", True, automatic))
		self.assertTrue(rules.form_may_default(SHELF, True, automatic), "the Company's default")
		self.assertTrue(rules.form_may_default(BIN_A, True, automatic), "the item's default bin")
		self.assertFalse(rules.form_may_default(BIN_B, True, automatic), "typed by a person before the project")
		self.assertFalse(rules.form_may_default(BIN_B, True, ()))

	def test_when_only_the_project_changes_a_hand_pick_stays(self):
		automatic = (SHELF, BIN_A)
		# The rule's own value from this session, or its value for the project the line had.
		self.assertTrue(rules.form_may_default(SHELF, False, automatic, previous=None, applied=SHELF))
		self.assertTrue(rules.form_may_default(BIN_B, False, automatic, previous=BIN_B, applied=None))
		# A hand pick stays, even when it equals the item's default bin or the shelf.
		self.assertFalse(rules.form_may_default(BIN_A, False, automatic, previous=SHELF, applied=SHELF))
		self.assertFalse(rules.form_may_default(SHELF, False, automatic, previous=BIN_B, applied=BIN_B))
		self.assertFalse(rules.form_may_default(BIN_B, False, automatic))
		self.assertTrue(rules.form_may_default("", False, automatic))

	def test_a_receipt_goes_where_the_order_line_says(self):
		self.assertEqual(rules.receiving_warehouse(BIN_A, True, False, SHELF), (BIN_A, rules.REASON_NONE, False))
		self.assertEqual(rules.receiving_warehouse(SHELF, True, False, SHELF), (SHELF, rules.REASON_SHELF, False))
		# An Overhead line a person pointed at a bin stays on that bin.
		self.assertEqual(rules.receiving_warehouse(BIN_B, True, True, SHELF, BIN_A), (BIN_B, rules.REASON_NONE, False))

	def test_an_overhead_line_that_names_the_shelf_is_offered_the_items_bin(self):
		"""Every order line open on the day of the rename named Stores, Overhead ones included."""
		self.assertEqual(
			rules.receiving_warehouse(SHELF, True, True, SHELF, BIN_A, ROOM), (BIN_A, rules.REASON_OWN_BIN, True)
		)
		self.assertEqual(
			rules.receiving_warehouse(SHELF, True, True, SHELF, None, ROOM), (ROOM, rules.REASON_FALLBACK, True)
		)
		# Nowhere better to offer: it stays where the order said.
		self.assertEqual(
			rules.receiving_warehouse(SHELF, True, True, SHELF, None, None), (SHELF, rules.REASON_SHELF, False)
		)

	def test_a_line_with_no_location_gets_the_rule(self):
		self.assertEqual(rules.receiving_warehouse("", True, False, SHELF), (SHELF, rules.REASON_SHELF, False))
		self.assertEqual(rules.receiving_warehouse(None, True, True, SHELF, BIN_A), (BIN_A, rules.REASON_OWN_BIN, False))
		self.assertEqual(rules.receiving_warehouse("", False, False, SHELF), (None, rules.REASON_NONE, False))

	def test_the_overhead_type_is_the_one_purchasing_uses(self):
		tree = ast.parse(PROCUREMENT.read_text(encoding="utf-8"))
		value = next(
			node.value.value
			for node in tree.body
			if isinstance(node, ast.Assign)
			and any(getattr(t, "id", None) == "OVERHEAD_PROJECT_TYPE" for t in node.targets)
		)
		self.assertEqual(rules.OVERHEAD_PROJECT_TYPE, value)


class TestWhoseStockAMoveIs(unittest.TestCase):
	def test_an_add_at_the_shelf_never_credits_the_job(self):
		"""Away from the shelf a job on an add means the parts came back from it. At the shelf
		the job is who they are set aside for, and crediting it would cancel its later take."""
		self.assertEqual(rules.add_offset_setting(True, False), "take_expense_account")
		self.assertEqual(rules.add_offset_setting(False, False), "add_offset_account")
		self.assertEqual(rules.add_offset_setting(True, True), "add_offset_account")
		self.assertEqual(rules.add_offset_setting(False, True), "add_offset_account")

	def test_a_take_at_the_shelf_always_needs_a_job(self):
		self.assertTrue(rules.take_needs_job(True, 0))
		self.assertTrue(rules.take_needs_job(False, 1))
		self.assertFalse(rules.take_needs_job(False, 0))

	def test_between_two_ordinary_places_it_is_the_job_site_rule(self):
		self.assertEqual(rules.move_project(False, False, None, None), (None, None))
		self.assertEqual(rules.move_project(False, False, None, JOB_A), (JOB_A, None))
		self.assertEqual(rules.move_project(False, False, JOB_B, None), (JOB_B, None))
		# A job named by the page is ignored where the shelf is not involved.
		self.assertEqual(rules.move_project(False, False, None, None, given=JOB_A), (None, None))

	def test_shelf_to_a_job_site_carries_the_sites_job(self):
		self.assertEqual(rules.move_project(True, False, None, JOB_A, given=JOB_B), (JOB_A, None))

	def test_shelf_to_a_bin_carries_the_job_named(self):
		self.assertEqual(rules.move_project(True, False, None, None, given=JOB_B, holders=[(JOB_A, 3)]), (JOB_B, None))

	def test_shelf_to_a_bin_with_no_job_named_uses_the_only_holder(self):
		self.assertEqual(rules.move_project(True, False, None, None, holders=[(JOB_A, 3)]), (JOB_A, None))
		self.assertEqual(rules.move_project(True, False, None, None, holders=[(JOB_A, 3), (JOB_B, 0)]), (JOB_A, None))

	def test_shelf_to_a_bin_never_guesses_between_two_jobs_or_none(self):
		for holders in ([(JOB_A, 3), (JOB_B, 2)], [], [(JOB_A, 0)], [(JOB_A, -2)]):
			project, problem = rules.move_project(True, False, None, None, holders=holders)
			self.assertIsNone(project)
			self.assertIn("which job these were bought for", problem)

	def test_a_bin_to_the_shelf_needs_the_job_it_is_for(self):
		self.assertEqual(rules.move_project(False, True, None, None, given=JOB_B), (JOB_B, None))
		project, problem = rules.move_project(False, True, None, None)
		self.assertIsNone(project)
		self.assertIn("being set aside for", problem)

	def test_a_job_site_back_to_the_shelf_is_still_that_jobs(self):
		self.assertEqual(rules.move_project(False, True, JOB_A, None, given=JOB_B), (JOB_A, None))

	def test_sole_holder(self):
		self.assertEqual(rules.sole_holder([(JOB_A, 1)]), JOB_A)
		self.assertIsNone(rules.sole_holder([(JOB_A, 1), (JOB_B, 1)]))
		self.assertIsNone(rules.sole_holder([]))
		self.assertIsNone(rules.sole_holder([("", 5)]))


class TestWhatIsShopStock(unittest.TestCase):
	def test_an_items_on_hand_splits_three_ways(self):
		bins_ = [(BIN_A, 4), (BIN_B, 1), (SHELF, 6), (SITE, 3)]
		self.assertEqual(rules.split_totals(bins_, {SITE}, SHELF), (5.0, 3.0, 6.0))

	def test_with_no_shelf_it_is_the_job_site_split(self):
		bins_ = [(BIN_A, 4), (SHELF, 6), (SITE, 3)]
		self.assertEqual(rules.split_totals(bins_, {SITE}, None), (10.0, 3.0, 0.0))
		self.assertEqual(rules.split_totals(bins_, {SITE}, None)[:2], site_rules.shop_and_site_totals(bins_, {SITE}))

	def test_blank_and_bad_quantities_are_zero(self):
		self.assertEqual(rules.split_totals([(BIN_A, None), (SHELF, "x"), (SITE, "2")], {SITE}, SHELF), (0.0, 2.0, 0.0))
		self.assertEqual(rules.split_totals(None, None, None), (0.0, 0.0, 0.0))

	def test_not_shop_is_every_site_and_the_shelf(self):
		self.assertEqual(rules.not_shop({SITE}, SHELF), {SITE, SHELF})
		self.assertEqual(rules.not_shop({SITE}, None), {SITE})
		self.assertEqual(rules.not_shop((), ""), set())

	def test_places_split_three_ways_in_order(self):
		a, b = {"warehouse": BIN_A}, {"warehouse": BIN_B, "job_site": False}
		on_shelf = {"warehouse": SHELF, "project_shelf": True}
		on_site = {"warehouse": SITE, "job_site": True}
		self.assertEqual(rules.split_places([on_site, a, on_shelf, b]), ([a, b], [on_shelf], [on_site]))
		self.assertEqual(rules.split_places(None), ([], [], []))

	def test_the_reorder_boards_roll_up_leaves_the_shelf_out_like_a_site(self):
		"""29 of production's rules roll up at the company's root, which the shelf sits under.
		The same arithmetic that takes job sites out takes the shelf out; the rules are not
		rewritten."""
		root, shop = "All Warehouses - SF", "Inventory - SF"
		projected = {ITEM: {BIN_A: 2.0, shop: 2.0, SHELF: 10.0, SITE: 4.0, root: 16.0}}
		ancestors = {BIN_A: (shop, root), SHELF: (root,), SITE: ("Job Sites - SF", root)}
		out = site_rules.without_sites(projected, rules.not_shop({SITE}, SHELF), ancestors)
		self.assertEqual(out[ITEM][root], 2.0)
		self.assertEqual(out[ITEM][shop], 2.0)
		# The shelf's own figure stays, and the argument is not changed.
		self.assertEqual(out[ITEM][SHELF], 10.0)
		self.assertEqual(projected[ITEM][root], 16.0)

	def test_the_old_label_is_worked_out_from_the_new_name(self):
		self.assertEqual(rules.former_docname("Project Shelf - SF"), "Stores - SF")
		self.assertEqual(rules.former_docname("Project Shelf"), "Stores")
		self.assertIsNone(rules.former_docname("Mezzanine - SF"))
		self.assertIsNone(rules.former_docname(None))


class TestShelfSummary(unittest.TestCase):
	"""The arithmetic behind ``shelf_stock``. The shapes are production's on 2026-11-12, with
	made-up names: receipts tagged by job, one untagged transfer out, one count."""

	def test_receipts_are_added_up_by_job_and_item(self):
		ledger = [
			_row("A", 12, JOB_A, after=12, day="2026-09-15"),
			_row("A", 18, JOB_B, after=30, day="2026-09-30"),
			_row("B", 6, JOB_A, after=6, day="2026-09-08"),
		]
		out = rules.shelf_summary(ledger, {"A": 30, "B": 6})
		self.assertEqual(
			[(r["project"], r["item_code"], r["qty"], r["qty_in"], r["qty_out"]) for r in out["rows"]],
			[(JOB_A, "A", 12.0, 12.0, 0.0), (JOB_A, "B", 6.0, 6.0, 0.0), (JOB_B, "A", 18.0, 18.0, 0.0)],
		)
		# Nothing is short, so what each job has is what its tags say.
		self.assertEqual([(r["tagged"], r["over_tagged"]) for r in out["rows"]], [(12.0, 0.0), (6.0, 0.0), (18.0, 0.0)])
		self.assertEqual(out["untagged"], [])
		self.assertEqual(out["untagged_movements"], [])
		self.assertTrue(out["exact"])

	def test_a_take_for_the_job_comes_off_that_jobs_count(self):
		ledger = [
			_row("A", 10, JOB_A, after=10, day="2026-10-16"),
			_row("A", -4, JOB_A, "Stock Entry", "MAT-STE-0009", after=6, day="2026-11-02"),
		]
		out = rules.shelf_summary(ledger, {"A": 6})
		self.assertEqual(out["rows"][0]["qty"], 6.0)
		self.assertEqual((out["rows"][0]["qty_in"], out["rows"][0]["qty_out"]), (10.0, 4.0))
		self.assertEqual(out["rows"][0]["last_movement"], "2026-11-02")
		self.assertTrue(out["exact"])

	def test_a_job_that_is_back_to_zero_has_no_row(self):
		ledger = [_row("A", 5, JOB_A, after=5), _row("A", -5, JOB_A, "Stock Entry", "MAT-STE-0001", after=0)]
		out = rules.shelf_summary(ledger, {"A": 0})
		self.assertEqual(out["rows"], [])
		self.assertTrue(out["exact"])

	def test_an_untagged_move_is_reported_not_hidden(self):
		"""Production's own case: received for a job, then transferred out with no project."""
		ledger = [
			_row("A", 7, JOB_A, after=7, day="2026-09-15"),
			_row("A", -7, None, "Stock Entry", "MAT-STE-0002", after=0, day="2026-09-23"),
		]
		out = rules.shelf_summary(ledger, {"A": 0})
		# The tag is still there; the job's figure is what is physically there: nothing.
		self.assertEqual((out["rows"][0]["qty"], out["rows"][0]["tagged"], out["rows"][0]["over_tagged"]), (0.0, 7.0, 7.0))
		self.assertEqual(
			out["untagged"], [{"item_code": "A", "on_hand": 0.0, "tagged": 7.0, "qty": -7.0, "over_tagged": 7.0}]
		)
		self.assertEqual(rules.holders(out["rows"], "A"), [], "a job whose parts have left holds nothing")
		self.assertEqual(
			out["untagged_movements"],
			[
				{
					"posting_date": "2026-09-23",
					"voucher_type": "Stock Entry",
					"voucher_no": "MAT-STE-0002",
					"item_code": "A",
					"qty": -7.0,
					"kind": rules.KIND_MOVE,
				}
			],
		)
		self.assertFalse(out["exact"])

	def test_a_count_is_worked_out_from_the_balance_before_it_and_never_tagged(self):
		"""ERPNext writes a count with actual_qty 0 and the counted balance after it."""
		ledger = [
			_row("A", 10, JOB_A, after=10, day="2026-10-01"),
			_row("A", 0, JOB_A, rules.RECONCILIATION, "MAT-RECO-0001", after=8, day="2026-10-20"),
		]
		out = rules.shelf_summary(ledger, {"A": 8})
		self.assertEqual(out["rows"][0]["tagged"], 10.0, "a count carries no job, whatever its row says")
		self.assertEqual(out["rows"][0]["qty"], 8.0, "and the job cannot have more than was counted")
		self.assertEqual(
			out["untagged"], [{"item_code": "A", "on_hand": 8.0, "tagged": 10.0, "qty": -2.0, "over_tagged": 2.0}]
		)
		self.assertEqual((out["untagged_movements"][0]["qty"], out["untagged_movements"][0]["kind"]), (-2.0, rules.KIND_COUNT))
		self.assertFalse(out["exact"])

	def test_a_first_count_with_nothing_before_it_is_all_untagged(self):
		out = rules.shelf_summary([_row("A", 0, None, rules.RECONCILIATION, "MAT-RECO-0002", after=5)], {"A": 5})
		self.assertEqual(out["rows"], [])
		self.assertEqual(out["untagged"][0]["qty"], 5.0)
		self.assertEqual(out["untagged_movements"][0]["qty"], 5.0)

	def test_a_count_that_changes_nothing_is_not_a_movement(self):
		ledger = [_row("A", 4, JOB_A, after=4), _row("A", 0, None, rules.RECONCILIATION, "MAT-RECO-0003", after=4)]
		out = rules.shelf_summary(ledger, {"A": 4})
		self.assertEqual(out["untagged_movements"], [])
		self.assertTrue(out["exact"])

	def test_parts_bought_for_one_job_and_used_on_another_show_as_a_negative(self):
		ledger = [
			_row("A", 4, JOB_A, after=4),
			_row("A", -4, JOB_B, "Stock Entry", "MAT-STE-0003", after=0),
		]
		out = rules.shelf_summary(ledger, {"A": 0})
		self.assertEqual([(r["project"], r["tagged"]) for r in out["rows"]], [(JOB_A, 4.0), (JOB_B, -4.0)])
		# Nothing is on the shelf, so neither job has any: never a negative, never a phantom 4.
		self.assertEqual([(r["qty"], r["over_tagged"]) for r in out["rows"]], [(0.0, 4.0), (0.0, 0.0)])
		self.assertEqual(out["untagged"], [], "the tags add up to what is there")
		self.assertFalse(out["exact"], "but a negative balance is never exact")

	def test_stock_on_hand_that_no_row_explains_is_untagged(self):
		out = rules.shelf_summary([], {"A": 3, "B": 0})
		self.assertEqual(
			out["untagged"], [{"item_code": "A", "on_hand": 3.0, "tagged": 0.0, "qty": 3.0, "over_tagged": 0.0}]
		)
		self.assertFalse(out["exact"])

	def test_a_jobs_figure_is_never_more_than_is_on_the_shelf(self):
		"""Two jobs' tags claim 9 and 6 are there: the job that received last keeps its parts."""
		ledger = [
			_row("A", 5, JOB_A, after=5, day="2026-09-01"),
			_row("A", 4, JOB_B, after=9, day="2026-10-01"),
			_row("A", 0, None, rules.RECONCILIATION, "MAT-RECO-0009", after=6, day="2026-10-20"),
		]
		out = rules.shelf_summary(ledger, {"A": 6})
		by_job = {r["project"]: r for r in out["rows"]}
		self.assertEqual((by_job[JOB_B]["qty"], by_job[JOB_B]["over_tagged"]), (4.0, 0.0))
		self.assertEqual((by_job[JOB_A]["qty"], by_job[JOB_A]["tagged"], by_job[JOB_A]["over_tagged"]), (2.0, 5.0, 3.0))
		self.assertEqual(sum(r["qty"] for r in out["rows"]), 6.0)
		self.assertEqual(out["untagged"][0]["over_tagged"], 3.0)
		self.assertEqual(rules.projects_summary(out["rows"])[0]["qty"], 2.0)

	def test_allocate(self):
		self.assertEqual(rules.allocate(10, [(JOB_A, 3, "2026-09-01"), (JOB_B, 4, "2026-10-01")]), {JOB_A: 3.0, JOB_B: 4.0})
		self.assertEqual(rules.allocate(5, [(JOB_A, 3, "2026-09-01"), (JOB_B, 4, "2026-10-01")]), {JOB_A: 1.0, JOB_B: 4.0})
		self.assertEqual(rules.allocate(0, [(JOB_A, 13, "2026-09-15")]), {JOB_A: 0.0})
		# A negative balance holds nothing and takes nothing; a negative on-hand is none.
		self.assertEqual(rules.allocate(1, [(JOB_A, 3, "2026-09-01"), (JOB_B, -2, "2026-10-01")]), {JOB_A: 1.0})
		self.assertEqual(rules.allocate(-4, [(JOB_A, 3, "")]), {JOB_A: 0.0})
		# The same date is settled by the job's id, the same way every time.
		self.assertEqual(rules.allocate(2, [(JOB_B, 2, "2026-10-01"), (JOB_A, 2, "2026-10-01")]), {JOB_A: 2.0, JOB_B: 0.0})
		self.assertEqual(rules.allocate(5, None), {})

	def test_the_project_filter_keeps_that_jobs_rows_and_its_items_gaps(self):
		ledger = [
			_row("A", 4, JOB_A, after=4),
			_row("A", 2, JOB_B, after=6),
			_row("B", 5, JOB_B, after=5),
			_row("C", -1, None, "Stock Entry", "MAT-STE-0004", after=0),
			_row("A", -1, None, "Stock Entry", "MAT-STE-0005", after=5),
		]
		out = rules.shelf_summary(ledger, {"A": 5, "B": 5, "C": 0}, project=JOB_A)
		self.assertEqual([(r["project"], r["item_code"]) for r in out["rows"]], [(JOB_A, "A")])
		self.assertEqual([u["item_code"] for u in out["untagged"]], ["A"])
		self.assertEqual([m["voucher_no"] for m in out["untagged_movements"]], ["MAT-STE-0005"])
		self.assertFalse(out["exact"])

	def test_the_item_filter(self):
		ledger = [_row("A", 4, JOB_A, after=4), _row("B", 5, JOB_B, after=5)]
		out = rules.shelf_summary(ledger, {"A": 4, "B": 9}, item_code="A")
		self.assertEqual([r["item_code"] for r in out["rows"]], ["A"])
		self.assertEqual(out["untagged"], [])
		self.assertTrue(out["exact"])

	def test_movements_are_newest_first_and_capped(self):
		ledger = [
			_row("A", -1, None, "Stock Entry", f"MAT-STE-{n:04d}", after=100 - n, day=f"2026-10-{n:02d}")
			for n in range(1, 11)
		]
		out = rules.shelf_summary(ledger, {"A": 90}, limit=3)
		self.assertEqual([m["voucher_no"] for m in out["untagged_movements"]], ["MAT-STE-0010", "MAT-STE-0009", "MAT-STE-0008"])

	def test_projects_summary_counts_only_what_a_job_still_holds(self):
		rows = [
			{"project": JOB_A, "item_code": "A", "qty": 4.0},
			{"project": JOB_A, "item_code": "B", "qty": 6.0},
			{"project": JOB_B, "item_code": "A", "qty": -2.0},
		]
		self.assertEqual(
			rules.projects_summary(rows),
			[{"project": JOB_A, "items": 2, "qty": 10.0}, {"project": JOB_B, "items": 0, "qty": 0.0}],
		)

	def test_an_items_tags_are_exact_only_when_they_account_for_every_unit(self):
		clean = rules.shelf_summary([_row("A", 4, JOB_A, after=4)], {"A": 4})
		self.assertTrue(rules.item_exact(clean, "A"))
		short = rules.shelf_summary([_row("A", 4, JOB_A, after=4)], {"A": 6})
		self.assertFalse(rules.item_exact(short, "A"), "two are there with no job on record")
		self.assertTrue(rules.item_exact(short, "B"), "another item is not affected")
		crossed = rules.shelf_summary(
			[_row("A", 4, JOB_A, after=4), _row("A", -1, JOB_B, "Stock Entry", "MAT-STE-0007", after=3)], {"A": 3}
		)
		self.assertFalse(rules.item_exact(crossed, "A"), "a negative balance is a guess about whose are left")

	def test_holders_are_the_jobs_with_some_left_most_first(self):
		rows = [
			{"project": JOB_A, "item_code": "A", "qty": 2.0},
			{"project": JOB_B, "item_code": "A", "qty": 5.0},
			{"project": DONE_JOB, "item_code": "A", "qty": -1.0},
			{"project": JOB_A, "item_code": "B", "qty": 9.0},
		]
		self.assertEqual(rules.holders(rows, "A"), [(JOB_B, 5.0), (JOB_A, 2.0)])
		self.assertEqual(rules.holders(rows, "Z"), [])


# --------------------------------------------------------------------------- the module


class TestTheModule(_Base):
	def test_the_shelf_is_the_warehouse_the_setting_names(self):
		self.assertEqual(shelf.warehouse(), SHELF)
		self.assertEqual(shelf.label(), "Project Shelf")
		self.assertTrue(shelf.is_shelf(SHELF))
		self.assertFalse(shelf.is_shelf(BIN_A))
		self.assertFalse(shelf.is_shelf(""))

	def test_a_blank_setting_is_no_shelf(self):
		_reset(shelf_on=False)
		self.assertIsNone(shelf.warehouse())
		self.assertFalse(shelf.is_shelf(SHELF))
		self.assertEqual(shelf.shelf_stock()["warehouse"], None)
		self.assertEqual(shelf.not_shop_warehouses(), sites.site_warehouses())

	def test_a_group_a_disabled_or_a_missing_warehouse_is_no_shelf(self):
		base._table("Warehouse")[SHELF]["is_group"] = 1
		self.assertIsNone(shelf.warehouse())
		base._table("Warehouse")[SHELF].update(is_group=0, disabled=1)
		self.assertIsNone(shelf.warehouse())
		del base._table("Warehouse")[SHELF]
		self.assertIsNone(shelf.warehouse())

	def test_it_never_raises_when_the_settings_cannot_be_read(self):
		original = shelf._settings
		shelf._settings = lambda: (_ for _ in ()).throw(RuntimeError("no settings"))
		try:
			self.assertIsNone(shelf.warehouse())
			self.assertFalse(shelf.is_shelf(SHELF))
			self.assertEqual(shelf.shelf_stock()["rows"], [])
		finally:
			shelf._settings = original

	def test_not_shop_is_every_job_site_and_the_shelf(self):
		base._warehouse(SITE, "Job Site PRJ-90001 Water Wall", **{sites_base.FIELD: BUILD})
		self.assertEqual(shelf.not_shop_warehouses(), sites.site_warehouses() | {SHELF})
		self.assertIn(SITE, shelf.not_shop_warehouses())

	def test_the_fallback_is_the_other_setting(self):
		self.assertEqual(shelf.fallback_warehouse(), ROOM)
		_reset(fallback=False)
		self.assertIsNone(shelf.fallback_warehouse())

	def test_the_old_label_opens_the_shelf(self):
		self.assertEqual(shelf.former_name(), STORES)
		row = scan._warehouse(STORES)
		self.assertEqual(row.name, SHELF)
		self.assertEqual(scan._resolve(STORES)["location"]["warehouse"], SHELF)

	def test_a_real_stores_warehouse_is_never_shadowed(self):
		base._warehouse(STORES, "Stores")
		self.assertIsNone(shelf.former_name())
		self.assertEqual(scan._warehouse(STORES).name, STORES)

	def test_shelf_stock_names_the_jobs_and_items(self):
		base._item("PDT-0002", item_name="VALVE, BALL")
		_held(ITEM, (JOB_A, 3), (JOB_B, 2))
		_held("PDT-0002", (JOB_A, 4))
		out = shelf.shelf_stock()
		self.assertEqual(out["warehouse"], SHELF)
		self.assertEqual(
			[(r["project"], r["project_name"], r["item_code"], r["qty"]) for r in out["rows"]],
			[
				(JOB_A, "Water Wall", ITEM, 3.0),
				(JOB_A, "Water Wall", "PDT-0002", 4.0),
				(JOB_B, "Lobby Fountain", ITEM, 2.0),
			],
		)
		self.assertEqual(out["rows"][1]["item_name"], "VALVE, BALL")
		self.assertEqual(out["rows"][0]["stock_uom"], "Unit")
		self.assertEqual(
			out["projects"],
			[
				{"project": JOB_A, "items": 2, "qty": 7.0, "project_name": "Water Wall", "status": "Active", "project_type": "Build"},
				{"project": JOB_B, "items": 1, "qty": 2.0, "project_name": "Lobby Fountain", "status": "Active", "project_type": "Build"},
			],
		)
		self.assertTrue(out["exact"])

	def test_shelf_stock_for_one_job_and_for_one_item(self):
		base._item("PDT-0002")
		_held(ITEM, (JOB_A, 3), (JOB_B, 2))
		_held("PDT-0002", (JOB_B, 4))
		one = shelf.shelf_stock(project=JOB_B)
		self.assertEqual([(r["item_code"], r["qty"]) for r in one["rows"]], [(ITEM, 2.0), ("PDT-0002", 4.0)])
		self.assertEqual([p["project"] for p in one["projects"]], [JOB_B])
		item = shelf.shelf_stock(item_code=ITEM)
		self.assertEqual([(r["project"], r["qty"]) for r in item["rows"]], [(JOB_A, 3.0), (JOB_B, 2.0)])

	def test_shelf_stock_reports_what_the_tags_do_not_explain(self):
		_held(ITEM, (JOB_A, 3))
		base._table("Bin")[f"{ITEM}@{SHELF}"]["actual_qty"] = 5.0
		out = shelf.shelf_stock()
		self.assertEqual(out["untagged"][0]["qty"], 2.0)
		self.assertEqual(out["untagged"][0]["item_name"], f"PART, {ITEM}")
		self.assertFalse(out["exact"])

	def test_a_job_whose_tagged_stock_has_left_is_not_a_holder(self):
		"""Production on the day of the rename: 13 units received for a job, moved out by a
		transfer with no project. The tag stays on the ledger; the shelf holds none."""
		STATE["ledger"] = [
			_row(ITEM, 7, DONE_JOB, after=7, day="2026-09-15"),
			_row(ITEM, -7, None, "Stock Entry", "MAT-STE-0002", after=0, day="2026-09-23"),
		]
		base._bin(ITEM, SHELF, 0)
		out = shelf.shelf_stock()
		self.assertEqual((out["rows"][0]["qty"], out["rows"][0]["tagged"], out["rows"][0]["over_tagged"]), (0.0, 7.0, 7.0))
		self.assertEqual(out["projects"][0]["qty"], 0.0)
		self.assertEqual(shelf.holders(ITEM), [])
		self.assertFalse(out["exact"])

	def test_shelf_stock_never_raises_and_never_calls_a_failure_exact(self):
		STATE["ledger_fails"] = True
		out = shelf.shelf_stock()
		self.assertEqual((out["warehouse"], out["rows"], out["exact"]), (SHELF, [], False))
		self.assertTrue(any("Project Shelf" in (title or "") for title, _message in STATE["errors"]))

	def test_holders_are_the_jobs_holding_one_item(self):
		_held(ITEM, (JOB_A, 3), (JOB_B, 5))
		self.assertEqual(
			shelf.holders(ITEM),
			[
				{"project": JOB_B, "project_name": "Lobby Fountain", "qty": 5.0},
				{"project": JOB_A, "project_name": "Water Wall", "qty": 3.0},
			],
		)
		self.assertEqual(shelf.holders(""), [])
		self.assertEqual(shelf.holding(ITEM)["exact"], True)
		base._table("Bin")[f"{ITEM}@{SHELF}"]["actual_qty"] = 9.0
		held = shelf.holding(ITEM)
		self.assertEqual((len(held["jobs"]), held["exact"]), (2, False), "one more is there than the tags say")
		self.assertEqual(shelf.holding(""), {"jobs": [], "exact": True})


# --------------------------------------------------------------------------- availability


class TestAvailability(_Base):
	def test_search_totals_leave_the_shelf_and_job_sites_out(self):
		base._warehouse(SITE, "Job Site PRJ-90001 Water Wall", **{sites_base.FIELD: BUILD})
		base._bin(ITEM, BIN_A, 4)
		base._bin(ITEM, SHELF, 6)
		base._bin(ITEM, SITE, 3)
		STATE["search_rows"] = [
			{"item_code": ITEM, "item_name": "PART", "image": None, "stock_uom": "Unit", "is_stock_item": 1}
		]
		row = scan.search_items("part")[0]
		self.assertEqual((row["on_hand_total"], row["on_project_shelf"], row["on_job_sites"]), (4.0, 6.0, 3.0))

	def test_with_no_shelf_set_up_its_stock_is_the_shops_as_before(self):
		_reset(shelf_on=False)
		base._bin(ITEM, BIN_A, 4)
		base._bin(ITEM, SHELF, 6)
		STATE["search_rows"] = [
			{"item_code": ITEM, "item_name": "PART", "image": None, "stock_uom": "Unit", "is_stock_item": 1}
		]
		row = scan.search_items("part")[0]
		self.assertEqual((row["on_hand_total"], row["on_project_shelf"]), (10.0, 0.0))

	def test_elsewhere_marks_the_shelf_with_its_jobs_between_the_shop_and_the_sites(self):
		base._warehouse(SITE, "Job Site PRJ-90001 Water Wall", **{sites_base.FIELD: BUILD})
		base._bin(ITEM, BIN_B, 1)
		base._bin(ITEM, SITE, 9)
		_held(ITEM, (JOB_A, 3), (JOB_B, 2))
		places = scan._elsewhere(ITEM, COMPANY, exclude=BIN_A)
		self.assertEqual([p["warehouse"] for p in places], [BIN_B, SHELF, SITE])
		self.assertEqual([p["project_shelf"] for p in places], [False, True, False])
		self.assertEqual([j["project"] for j in places[1]["jobs"]], [JOB_A, JOB_B])
		self.assertEqual(places[0]["jobs"], [])

	def test_the_shelf_lists_only_what_is_there_or_on_its_way(self):
		"""Its leftover Item Defaults and Putaway Rules are not assignments."""
		_held(ITEM, (JOB_A, 3))
		STATE["items_at"] = [
			{"name": ITEM, "item_name": "PART", "image": None, "stock_uom": "Unit", "on_hand": 3, "on_order": 0, "assigned": 1, "is_default": 1, "undecided": 0},
			{"name": "PDT-0002", "item_name": "DEFAULTED", "image": None, "stock_uom": "Unit", "on_hand": 0, "on_order": 0, "assigned": 1, "is_default": 1, "undecided": 0},
			{"name": "PDT-0003", "item_name": "ON ORDER", "image": None, "stock_uom": "Unit", "on_hand": 0, "on_order": 5, "assigned": 0, "is_default": 1, "undecided": 0},
		]
		rows, truncated = scan._items_at(scan._warehouse(SHELF))
		self.assertEqual([r["item_code"] for r in rows], [ITEM, "PDT-0003"])
		self.assertFalse(truncated)
		self.assertEqual([(r["assigned"], r["is_default"], r["undecided"]) for r in rows], [(False, False, False)] * 2)
		self.assertEqual(rows[0]["jobs"], [{"project": JOB_A, "project_name": "Water Wall", "qty": 3.0}])
		self.assertEqual(rows[1]["jobs"], [])

	def test_an_ordinary_bin_lists_as_before(self):
		STATE["items_at"] = [
			{"name": "PDT-0002", "item_name": "DEFAULTED", "image": None, "stock_uom": "Unit", "on_hand": 0, "on_order": 0, "assigned": 1, "is_default": 1, "undecided": 0},
		]
		rows, _truncated = scan._items_at(scan._warehouse(BIN_A))
		self.assertEqual([(r["item_code"], r["assigned"]) for r in rows], [("PDT-0002", True)])
		self.assertNotIn("jobs", rows[0])

	def test_the_location_and_the_item_say_they_are_the_shelf(self):
		_held(ITEM, (JOB_A, 3))
		base._default(ITEM, SHELF)
		base._rule(ITEM, SHELF)
		STATE["items_at"] = []
		self.assertTrue(scan._location_payload(scan._warehouse(SHELF))["project_shelf"])
		self.assertFalse(scan._location_payload(scan._warehouse(BIN_A))["project_shelf"])
		original = scan._open_order_lines
		scan._open_order_lines = lambda item_code, company: []
		try:
			item = scan._item_payload(ITEM, scan._warehouse(SHELF))
		finally:
			scan._open_order_lines = original
		self.assertTrue(item["project_shelf"])
		self.assertEqual(item["shelf_jobs"], [{"project": JOB_A, "project_name": "Water Wall", "qty": 3.0}])
		self.assertEqual((item["assigned"], item["is_default"], item["undecided"]), (False, False, False))

	def test_a_location_search_marks_the_shelf(self):
		rows = {r["warehouse"]: r for r in scan.search_locations("shelf")}
		self.assertTrue(rows[SHELF]["project_shelf"])
		rows = {r["warehouse"]: r for r in scan.search_locations("bin")}
		self.assertFalse(rows[BIN_A]["project_shelf"])

	def test_the_reorder_board_reads_the_shared_set(self):
		tree = ast.parse(REORDER.read_text(encoding="utf-8"))
		build = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "build_board")
		source = ast.unparse(build)
		self.assertIn("sites = project_shelf.not_shop_warehouses()", source)
		self.assertIn("site_rules.without_sites(projected_map, sites, ancestors)", source)
		self.assertIn("read_tracking(rule_rows, ancestors, today, sites)", source)


# --------------------------------------------------------------------------- moves and takes


class TestTakesAndAdds(_Base):
	def setUp(self):
		super().setUp()
		base._bin(ITEM, SHELF, 5)
		base._bin(ITEM, BIN_A, 5)

	def test_a_take_at_the_shelf_needs_a_job_whatever_the_setting_says(self):
		self.assertEqual(STATE["settings"].get("require_project_for_take"), None)
		self.assertRefused(
			scan.take, "Pick the job", "Project Shelf", item_code=ITEM, warehouse=SHELF, qty=1, client_ref=base._ref(1)
		)
		self.assertEqual(base._vouchers("Stock Entry"), [])

	def test_a_take_at_the_shelf_with_a_job_is_charged_to_it(self):
		out = scan.take(ITEM, SHELF, 2, base._ref(2), project=JOB_A)
		entry = base._vouchers("Stock Entry")[0]
		self.assertEqual((entry["stock_entry_type"], entry["project"]), ("Material Issue", JOB_A))
		self.assertEqual(base._log(out["log"])["project"], JOB_A)

	def test_a_take_at_an_ordinary_bin_still_needs_no_job(self):
		scan.take(ITEM, BIN_A, 1, base._ref(3))
		self.assertIsNone(base._vouchers("Stock Entry")[0].get("project"))

	def test_stock_added_to_the_shelf_without_an_order_needs_a_job(self):
		self.assertRefused(
			scan.add, "Pick the job", item_code=ITEM, warehouse=SHELF, qty=1, client_ref=base._ref(4), without_po=1
		)
		self.assertEqual(base._vouchers("Stock Entry"), [])
		scan.add(ITEM, SHELF, 1, base._ref(5), without_po=1, project=JOB_B)
		entry = base._vouchers("Stock Entry")[0]
		self.assertEqual((entry["stock_entry_type"], entry["project"]), ("Material Receipt", JOB_B))

	def _offsets(self, fn):
		"""Run ``fn`` and return the settings account each entry's other side was resolved from."""
		STATE["settings"].update(take_expense_account="5200 - Parts Taken - SF", add_offset_account="5300 - Added Without PO - SF")
		original = scan._difference_account
		scan._difference_account = lambda company, item_code, configured=None: configured
		try:
			fn()
		finally:
			scan._difference_account = original
		return [row["expense_account"] for entry in base._vouchers("Stock Entry") for row in entry["items"]]

	def test_an_add_at_the_shelf_offsets_like_found_stock_and_keeps_the_job(self):
		"""Credited to the job's Parts Taken account, the job's later take would net to nothing
		and the job would carry no cost for the parts."""
		accounts = self._offsets(lambda: scan.add(ITEM, SHELF, 1, base._ref(20), without_po=1, project=JOB_B))
		self.assertEqual(accounts, ["5300 - Added Without PO - SF"])
		self.assertEqual(base._vouchers("Stock Entry")[0]["project"], JOB_B)

	def test_a_return_from_a_job_at_an_ordinary_bin_still_credits_the_job(self):
		accounts = self._offsets(lambda: scan.add(ITEM, BIN_A, 1, base._ref(21), without_po=1, project=JOB_B))
		self.assertEqual(accounts, ["5200 - Parts Taken - SF"])

	def test_found_stock_at_an_ordinary_bin_still_offsets_to_added_without_po(self):
		accounts = self._offsets(lambda: scan.add(ITEM, BIN_A, 1, base._ref(22), without_po=1))
		self.assertEqual(accounts, ["5300 - Added Without PO - SF"])

	def test_a_take_at_the_shelf_still_charges_the_parts_taken_account(self):
		accounts = self._offsets(lambda: scan.take(ITEM, SHELF, 1, base._ref(23), project=JOB_B))
		self.assertEqual(accounts, ["5200 - Parts Taken - SF"])

	def test_a_new_items_bin_is_never_the_shelf_or_a_job_site(self):
		spec = {"item_code": "NEW-0001", "item_name": "VALVE, NEW", "item_group": "Parts", "stock_uom": "Unit"}
		item = scan._quick_item(spec, scan._warehouse(SHELF))
		self.assertEqual(item.item_defaults, [{"company": COMPANY, "default_warehouse": None}])
		spec["item_code"] = "NEW-0002"
		item = scan._quick_item(spec, scan._warehouse(BIN_A))
		self.assertEqual(item.item_defaults, [{"company": COMPANY, "default_warehouse": BIN_A}])

	def test_stock_added_to_an_ordinary_bin_still_needs_no_job(self):
		scan.add(ITEM, BIN_A, 1, base._ref(6), without_po=1)
		self.assertIsNone(base._vouchers("Stock Entry")[0].get("project"))

	def test_a_store_run_is_refused_at_the_shelf_before_anything_is_written(self):
		tree = ast.parse((APP / "api" / "stock_scan.py").read_text(encoding="utf-8"))
		run = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "store_run")
		source = ast.unparse(run)
		refusal = source.index("project_shelf.is_shelf(location.name)")
		self.assertLess(refusal, source.index("_begin_log("), "refused before the log row")
		self.assertLess(refusal, source.index("_quick_item(spec, location)"), "and before a quick Item is created")


class TestMoves(_Base):
	def setUp(self):
		super().setUp()
		STATE["columns"] = {("Warehouse", sites_base.FIELD)}
		base._warehouse(SITE, "Job Site PRJ-90001 Water Wall", **{sites_base.FIELD: BUILD})
		base._bin(ITEM, BIN_A, 5)

	def _entry(self):
		return base._vouchers("Stock Entry")[-1]

	def test_shelf_to_a_bin_carries_the_only_job_that_holds_it(self):
		_held(ITEM, (JOB_A, 5))
		out = bins.move_to(ITEM, SHELF, BIN_B, 2, base._ref(1))
		self.assertEqual((self._entry()["stock_entry_type"], self._entry()["project"]), ("Material Transfer", JOB_A))
		self.assertEqual(base._log(out["log"])["project"], JOB_A)

	def test_shelf_to_a_bin_never_guesses_between_two_jobs(self):
		_held(ITEM, (JOB_A, 3), (JOB_B, 2))
		self.assertRefused(
			bins.move_to, "which job these were bought for", item_code=ITEM, from_warehouse=SHELF, to_warehouse=BIN_B, qty=2, client_ref=base._ref(2)
		)
		self.assertEqual(base._vouchers("Stock Entry"), [])
		self.assertEqual(base._table("Stock Scan Log"), {}, "refused before the log row")
		bins.move_to(ITEM, SHELF, BIN_B, 2, base._ref(3), project=JOB_B)
		self.assertEqual(self._entry()["project"], JOB_B)

	def test_shelf_to_a_bin_with_nothing_tagged_asks(self):
		base._bin(ITEM, SHELF, 4)
		self.assertRefused(
			bins.move_to, "which job these were bought for", item_code=ITEM, from_warehouse=SHELF, to_warehouse=BIN_B, qty=1, client_ref=base._ref(4)
		)

	def test_a_job_whose_parts_have_left_is_never_picked_as_the_only_holder(self):
		"""Ten are tagged to a finished job and gone; four came in later with no job. The move
		must ask, not hang the four on the job whose ten left."""
		STATE["ledger"] = [
			_row(ITEM, 10, DONE_JOB, after=10, day="2026-09-01"),
			_row(ITEM, -10, None, "Stock Entry", "MAT-STE-0002", after=0, day="2026-09-23"),
			_row(ITEM, 0, None, rules.RECONCILIATION, "MAT-RECO-0001", after=4, day="2026-10-20"),
		]
		base._bin(ITEM, SHELF, 4)
		self.assertRefused(
			bins.move_to, "which job these were bought for", item_code=ITEM, from_warehouse=SHELF, to_warehouse=BIN_B, qty=1, client_ref=base._ref(30)
		)
		self.assertEqual(base._vouchers("Stock Entry"), [])

	def test_a_finished_jobs_leftovers_can_be_released_to_the_shop(self):
		_held(ITEM, (DONE_JOB, 4))
		bins.move_to(ITEM, SHELF, BIN_B, 4, base._ref(5), project=DONE_JOB)
		self.assertEqual(self._entry()["project"], DONE_JOB)

	def test_a_job_that_does_not_exist_is_a_sentence(self):
		_held(ITEM, (JOB_A, 5))
		self.assertRefused(
			bins.move_to, "There is no job", item_code=ITEM, from_warehouse=SHELF, to_warehouse=BIN_B, qty=1, client_ref=base._ref(6), project="PRJ-NOPE"
		)

	def test_shelf_to_a_job_site_carries_the_sites_job_whatever_was_named(self):
		_held(ITEM, (JOB_A, 3), (JOB_B, 2))
		bins.move_to(ITEM, SHELF, SITE, 2, base._ref(7), project=JOB_B)
		self.assertEqual(self._entry()["project"], BUILD)

	def test_a_bin_to_the_shelf_needs_the_job_it_is_for(self):
		self.assertRefused(
			bins.move_to, "being set aside for", item_code=ITEM, from_warehouse=BIN_A, to_warehouse=SHELF, qty=1, client_ref=base._ref(8)
		)
		self.assertEqual(base._vouchers("Stock Entry"), [])
		bins.move_to(ITEM, BIN_A, SHELF, 1, base._ref(9), project=JOB_B)
		self.assertEqual(self._entry()["project"], JOB_B)

	def test_nothing_is_set_aside_on_the_shelf_for_a_finished_job(self):
		self.assertRefused(
			bins.move_to, "Old Courtyard", "Completed", item_code=ITEM, from_warehouse=BIN_A, to_warehouse=SHELF, qty=1, client_ref=base._ref(10), project=DONE_JOB
		)

	def test_a_job_site_back_to_the_shelf_is_still_that_jobs(self):
		base._bin(ITEM, SITE, 3)
		bins.move_to(ITEM, SITE, SHELF, 1, base._ref(11), project=JOB_B)
		self.assertEqual(self._entry()["project"], BUILD)

	def test_move_here_takes_the_job_too(self):
		_held(ITEM, (JOB_A, 3), (JOB_B, 2))
		self.assertRefused(
			scan.move_here, "which job these were bought for", item_code=ITEM, warehouse=BIN_B, from_warehouse=SHELF, qty=1, client_ref=base._ref(12)
		)
		out = scan.move_here(ITEM, BIN_B, SHELF, 1, base._ref(13), project=JOB_A)
		self.assertEqual(self._entry()["project"], JOB_A)
		self.assertEqual(base._log(out["log"])["project"], JOB_A)

	def test_a_move_between_two_bins_carries_no_job_as_before(self):
		bins.move_to(ITEM, BIN_A, BIN_B, 1, base._ref(14), project=JOB_A)
		self.assertIsNone(self._entry().get("project"))

	def test_the_boneyard_trip_from_the_shelf_carries_the_job(self):
		_held(ITEM, (JOB_A, 2))
		bins.send_to_boneyard(ITEM, SHELF, 2, base._ref(15))
		self.assertEqual(self._entry()["project"], JOB_A)

	def test_the_shelf_is_never_an_items_bin(self):
		self.assertRefused(bins.assign, "Project Shelf", "nothing is kept there", item_code=ITEM, warehouse=SHELF, client_ref=base._ref(16))
		self.assertRefused(
			bins.move_to, "Project Shelf", "nothing is kept there", item_code=ITEM, from_warehouse=BIN_A, to_warehouse=SHELF, qty=1, client_ref=base._ref(17), make_home=1, project=JOB_A
		)
		self.assertEqual(base._vouchers("Stock Entry"), [])
		self.assertEqual(base._rules_for(ITEM, SHELF), [])

	def test_a_move_off_the_shelf_with_make_home_takes_its_leftover_claim_off(self):
		"""How the leftover rule and default at the shelf go away: as the stock leaves."""
		_held(ITEM, (JOB_A, 5))
		base._rule(ITEM, SHELF)
		base._default(ITEM, SHELF)
		bins.move_to(ITEM, SHELF, BIN_B, 5, base._ref(18), make_home=1)
		self.assertTrue(all(r["disable"] for r in base._rules_for(ITEM, SHELF)))
		self.assertIsNone(base._table("Item Default")[f"ID-{ITEM}-1"]["default_warehouse"])
		self.assertTrue(any(not r["disable"] for r in base._rules_for(ITEM, BIN_B)))


# --------------------------------------------------------------------------- purchase lines


def _order(lines, project=None, docstatus=0, company=COMPANY):
	doc = base.Doc(
		"Purchase Order",
		{"name": "PUR-ORD-0001", "project": project, "docstatus": docstatus, "company": company},
	)
	for n, line in enumerate(lines, start=1):
		doc.append("items", {"name": f"row{n}", "item_code": ITEM, **line})
	return doc


class TestPurchaseLines(_Base):
	def test_a_job_is_not_overhead_and_a_bucket_is(self):
		self.assertFalse(procurement.is_overhead(JOB_A))
		self.assertTrue(procurement.is_overhead(OVERHEAD))
		self.assertFalse(procurement.is_overhead(""))
		self.assertFalse(procurement.is_overhead("PRJ-NOPE"))

	def test_a_job_line_defaults_to_the_shelf(self):
		self.assertEqual(procurement.default_line_warehouse(ITEM, JOB_A, COMPANY), {"warehouse": SHELF, "reason": "shelf"})

	def test_an_overhead_line_defaults_to_the_bin_the_item_is_assigned_to(self):
		base._rule(ITEM, BIN_B)
		base._default(ITEM, SHELF)
		self.assertEqual(
			procurement.default_line_warehouse(ITEM, OVERHEAD, COMPANY), {"warehouse": BIN_B, "reason": "own_bin"}
		)

	def test_with_two_bins_it_is_the_one_holding_the_most(self):
		base._rule(ITEM, BIN_A)
		base._rule(ITEM, BIN_B)
		base._bin(ITEM, BIN_B, 9)
		self.assertEqual(procurement.item_own_bin(ITEM, COMPANY), BIN_B)

	def test_an_overhead_line_falls_to_the_item_default_then_the_fallback(self):
		base._default(ITEM, BIN_A)
		self.assertEqual(procurement.default_line_warehouse(ITEM, OVERHEAD, COMPANY)["warehouse"], BIN_A)
		base._table("Item Default").clear()
		self.assertEqual(
			procurement.default_line_warehouse(ITEM, OVERHEAD, COMPANY), {"warehouse": ROOM, "reason": "fallback"}
		)

	def test_a_default_or_rule_that_names_the_shelf_is_not_the_items_bin(self):
		base._rule(ITEM, SHELF)
		base._default(ITEM, SHELF)
		self.assertIsNone(procurement.item_own_bin(ITEM, COMPANY))
		self.assertEqual(procurement.default_line_warehouse(ITEM, OVERHEAD, COMPANY)["warehouse"], ROOM)

	def test_a_disabled_bin_or_the_boneyard_is_not_the_items_bin(self):
		base._rule(ITEM, BIN_A)
		base._table("Warehouse")[BIN_A]["disabled"] = 1
		base._default(ITEM, BONEYARD)
		self.assertIsNone(procurement.item_own_bin(ITEM, COMPANY))

	def test_with_no_shelf_nothing_is_decided(self):
		_reset(shelf_on=False)
		base._rule(ITEM, BIN_B)
		self.assertEqual(procurement.default_line_warehouse(ITEM, JOB_A, COMPANY), {"warehouse": None, "reason": "none"})
		self.assertEqual(procurement.default_line_warehouse(ITEM, OVERHEAD, COMPANY), {"warehouse": None, "reason": "none"})

	def test_no_project_decides_nothing(self):
		self.assertEqual(procurement.default_line_warehouse(ITEM, "", COMPANY), {"warehouse": None, "reason": "none"})

	def test_it_never_raises(self):
		original = procurement.is_overhead
		procurement.is_overhead = lambda project: (_ for _ in ()).throw(RuntimeError("boom"))
		try:
			self.assertEqual(procurement.default_line_warehouse(ITEM, JOB_A), {"warehouse": None, "reason": "none"})
		finally:
			procurement.is_overhead = original
		self.assertTrue(any("Purchase line location" in (title or "") for title, _message in STATE["errors"]))

	# --- the save hook -----------------------------------------------------------------------

	def test_a_draft_line_with_no_location_gets_its_default(self):
		base._rule(ITEM, BIN_B)
		doc = _order([{"project": JOB_A, "warehouse": None}, {"project": OVERHEAD, "warehouse": ""}])
		procurement.default_line_warehouses(doc)
		self.assertEqual([row.warehouse for row in doc.items], [SHELF, BIN_B])

	def test_a_location_a_person_picked_is_never_changed_on_save(self):
		base._rule(ITEM, BIN_B)
		doc = _order(
			[
				{"project": JOB_A, "warehouse": BIN_A},  # a job's line sent to a bin on purpose
				{"project": JOB_A, "warehouse": SHELF},
				{"project": OVERHEAD, "warehouse": BIN_A},  # an Overhead line on a bin that is not its own
			]
		)
		procurement.default_line_warehouses(doc)
		self.assertEqual([row.warehouse for row in doc.items], [BIN_A, SHELF, BIN_A])

	def test_an_overhead_line_is_never_saved_on_the_shelf(self):
		"""ERPNext fills the shelf into every new line (the Company's default warehouse). The
		form corrects it when its script runs in time; the save corrects it always."""
		base._rule(ITEM, BIN_B)
		doc = _order([{"project": OVERHEAD, "warehouse": SHELF}])
		procurement.default_line_warehouses(doc)
		self.assertEqual(doc.items[0].warehouse, BIN_B)
		# And again on the next save, if something put it back.
		doc.items[0].warehouse = SHELF
		procurement.default_line_warehouses(doc)
		self.assertEqual(doc.items[0].warehouse, BIN_B)

	def test_the_paths_the_form_never_sees_are_corrected_on_save(self):
		base._rule(ITEM, BIN_B)
		cases = {
			"a duplicated order": {"project": OVERHEAD, "warehouse": SHELF},
			"mapped from a Supplier Quotation": {"project": OVERHEAD, "warehouse": SHELF, "supplier_quotation_item": "SQI-1"},
			"mapped from a Material Request": {"project": OVERHEAD, "warehouse": SHELF, "material_request_item": "MRI-1"},
			"a REST save that sends a warehouse": {"project": OVERHEAD, "warehouse": SHELF, "rate": 4},
		}
		for label, line in cases.items():
			with self.subTest(label):
				doc = _order([dict(line)])
				procurement.cascade_project_to_items(doc)
				procurement.default_line_warehouses(doc)
				self.assertEqual(doc.items[0].warehouse, BIN_B)

	def test_an_overhead_line_with_no_bin_goes_to_the_fallback_on_save(self):
		doc = _order([{"project": OVERHEAD, "warehouse": SHELF}])
		procurement.default_line_warehouses(doc)
		self.assertEqual(doc.items[0].warehouse, ROOM)

	def test_an_overhead_line_with_nowhere_better_stays_where_it_is(self):
		_reset(fallback=False)
		doc = _order([{"project": OVERHEAD, "warehouse": SHELF}])
		procurement.default_line_warehouses(doc)
		self.assertEqual(doc.items[0].warehouse, SHELF)
		self.assertEqual(STATE["errors"], [])

	def test_the_save_hook_never_raises(self):
		original = procurement.default_line_warehouse
		procurement.default_line_warehouse = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
		try:
			doc = _order([{"project": OVERHEAD, "warehouse": SHELF}, {"project": JOB_A, "warehouse": None}])
			procurement.default_line_warehouses(doc)
		finally:
			procurement.default_line_warehouse = original
		self.assertEqual([row.warehouse for row in doc.items], [SHELF, None])

	def test_a_submitted_order_is_left_alone(self):
		doc = _order([{"project": JOB_A, "warehouse": None}], docstatus=1)
		procurement.default_line_warehouses(doc)
		self.assertIsNone(doc.items[0].warehouse)

	def test_the_cascade_gives_a_line_it_fills_its_default(self):
		base._rule(ITEM, BIN_B)
		base._default(ITEM, SHELF)
		doc = _order([{"project": "", "warehouse": SHELF}, {"project": "", "warehouse": None}], project=OVERHEAD)
		procurement.cascade_project_to_items(doc)
		self.assertEqual([row.project for row in doc.items], [OVERHEAD, OVERHEAD])
		self.assertEqual([row.warehouse for row in doc.items], [BIN_B, BIN_B])

	def test_the_cascade_leaves_a_place_a_person_picked(self):
		doc = _order([{"project": "", "warehouse": BIN_A}], project=JOB_A)
		procurement.cascade_project_to_items(doc)
		self.assertEqual((doc.items[0].project, doc.items[0].warehouse), (JOB_A, BIN_A))

	def test_the_cascade_leaves_a_line_that_came_from_a_request(self):
		base._default(ITEM, BIN_A)
		doc = _order([{"project": "", "warehouse": BIN_A, "material_request_item": "MRI-1"}], project=JOB_A)
		procurement.cascade_project_to_items(doc)
		self.assertEqual(doc.items[0].warehouse, BIN_A, "the request asked for that bin on purpose")

	def test_but_not_an_overhead_request_line_on_the_shelf(self):
		"""A request line's warehouse is usually the Item Default, which is the shelf."""
		base._rule(ITEM, BIN_B)
		doc = _order([{"project": "", "warehouse": SHELF, "material_request_item": "MRI-1"}], project=OVERHEAD)
		procurement.cascade_project_to_items(doc)
		self.assertEqual(doc.items[0].warehouse, BIN_B)

	def test_the_cascade_never_touches_a_line_that_has_its_project(self):
		base._rule(ITEM, BIN_B)
		doc = _order([{"project": OVERHEAD, "warehouse": SHELF}], project=JOB_A)
		procurement.cascade_project_to_items(doc)
		self.assertEqual((doc.items[0].project, doc.items[0].warehouse), (OVERHEAD, SHELF))

	def test_the_hook_runs_after_the_cascade_and_before_the_date_cascade(self):
		hooks = HOOKS.read_text(encoding="utf-8")
		cascade = hooks.index('"erpnext_enhancements.procurement_project.cascade_project_to_items"')
		default = hooks.index('"erpnext_enhancements.procurement_project.default_line_warehouses"')
		self.assertLess(cascade, default)

	# --- the form and the dialog -------------------------------------------------------------

	def test_the_form_gets_each_lines_default_with_its_item_and_project_echoed(self):
		base._rule(ITEM, BIN_B)
		out = procurement.line_warehouses(
			json.dumps(
				[
					{"key": "r1", "item_code": ITEM, "project": JOB_A},
					{"key": "r2", "item_code": ITEM, "project": OVERHEAD},
					{"key": "r3", "item_code": ITEM, "project": ""},
					{"key": "", "item_code": ITEM, "project": JOB_A},
					"junk",
				]
			),
			COMPANY,
		)
		self.assertEqual(sorted(out), ["r1", "r2"])
		self.assertEqual(
			out["r1"],
			{
				"item_code": ITEM,
				"project": JOB_A,
				"warehouse": SHELF,
				"warehouse_name": "Project Shelf",
				"reason": "shelf",
				"keep": False,
			},
		)
		self.assertEqual((out["r2"]["warehouse"], out["r2"]["reason"]), (BIN_B, "own_bin"))

	def _asked(self, **line):
		row = {"key": "r1", "item_code": ITEM, **line}
		return procurement.line_warehouses([row], COMPANY)["r1"]

	def test_the_form_is_told_to_keep_a_warehouse_a_person_picked(self):
		"""The project changes on a line whose warehouse was picked by hand: it stays."""
		base._rule(ITEM, BIN_B)
		base._default(ITEM, BIN_A)
		# The rule set the shelf for the job; the person then picked a bin; now the project changes.
		picked = self._asked(project=OVERHEAD, warehouse=BIN_A, was_item_code=ITEM, was_project=JOB_A, applied=SHELF)
		self.assertTrue(picked["keep"], "even though it equals the item's default bin")
		self.assertEqual(picked["warehouse"], BIN_B, "the default is still answered, for the record")
		# The rule's own value is replaced: what it set this session, or what it gives for the old project.
		self.assertFalse(self._asked(project=OVERHEAD, warehouse=SHELF, was_item_code=ITEM, was_project=JOB_A, applied=SHELF)["keep"])
		self.assertFalse(self._asked(project=JOB_A, warehouse=BIN_B, was_item_code=ITEM, was_project=OVERHEAD)["keep"])
		# A line already there when the form opened, on a bin that is not the rule's: a hand pick.
		self.assertTrue(self._asked(project=JOB_B, warehouse=BIN_A, was_item_code=ITEM, was_project=JOB_A)["keep"])

	def test_when_a_line_gets_its_item_only_erpnexts_own_fill_is_replaced(self):
		base._default(ITEM, BIN_A)
		base._table("Company")[COMPANY] = {"name": COMPANY, "default_warehouse": SHELF}
		self.assertFalse(self._asked(project=JOB_A, warehouse=BIN_A)["keep"], "the item's default bin")
		self.assertFalse(self._asked(project=OVERHEAD, warehouse=SHELF)["keep"], "the Company's default")
		self.assertFalse(self._asked(project=JOB_A, warehouse="")["keep"])
		self.assertTrue(self._asked(project=JOB_A, warehouse=BIN_B)["keep"], "typed before the project was set")
		# A changed item is a fresh fill by ERPNext too.
		self.assertFalse(self._asked(project=JOB_A, warehouse=BIN_A, was_item_code="OTHER", was_project=JOB_A)["keep"])

	def test_an_overhead_line_on_the_shelf_is_never_kept(self):
		base._rule(ITEM, BIN_B)
		answer = self._asked(project=OVERHEAD, warehouse=SHELF, was_item_code=ITEM, was_project=OVERHEAD, applied=BIN_B)
		self.assertFalse(answer["keep"], "the save would move it anyway")

	def test_the_form_script_sends_what_the_server_needs_and_obeys_keep(self):
		script = (APP / "public" / "js" / "purchase_order_project.js").read_text(encoding="utf-8")
		for sent in ("warehouse: row.warehouse", "was_item_code: was.item_code", "was_project: was.project", "applied: applied[row.name]"):
			self.assertIn(sent, script)
		self.assertIn("if (answer.keep || !answer.warehouse) return;", script)
		# The warehouse field's own event is not used to tell a hand pick: ERPNext triggers it
		# itself after its item lookup (transaction.js at v16.54.0).
		self.assertNotIn("warehouse: function", script)
		self.assertNotIn("warehouse(frm", script)

	# --- rows added after submit, and receipts made in the Desk --------------------------------

	def _new_line(self, **values):
		base._table("Purchase Order")["PUR-ORD-0009"] = {
			"name": "PUR-ORD-0009",
			"project": values.pop("header", OVERHEAD),
			"company": COMPANY,
			"docstatus": 1,
		}
		return base.Doc(
			"Purchase Order Item",
			{"parenttype": "Purchase Order", "parent": "PUR-ORD-0009", "item_code": ITEM, **values},
		)

	def test_a_row_added_with_update_items_gets_the_headers_project_and_its_place(self):
		"""ERPNext inserts that row itself, with no project and the Company's default warehouse."""
		base._rule(ITEM, BIN_B)
		row = self._new_line(warehouse=SHELF)
		procurement.default_new_order_line(row)
		self.assertEqual((row.project, row.warehouse), (OVERHEAD, BIN_B))

	def test_a_job_row_added_with_update_items_keeps_the_place_the_dialog_gave_it(self):
		row = self._new_line(header=JOB_A, warehouse=BIN_A)
		procurement.default_new_order_line(row)
		self.assertEqual((row.project, row.warehouse), (JOB_A, BIN_A))
		row = self._new_line(header=JOB_A, warehouse=None)
		procurement.default_new_order_line(row)
		self.assertEqual(row.warehouse, SHELF)

	def test_a_row_of_another_document_and_a_failure_are_left_alone(self):
		row = base.Doc("Purchase Order Item", {"parenttype": "Supplier Quotation", "parent": "SQ-1", "item_code": ITEM, "warehouse": SHELF})
		procurement.default_new_order_line(row)
		self.assertEqual((row.project, row.warehouse), (None, SHELF))
		procurement.default_new_order_line(None)

	def _receipt(self, **header):
		receipt = base.Doc("Purchase Receipt", {"company": COMPANY, "__islocal": 1, **header})
		receipt.append("items", {"item_code": ITEM, "project": OVERHEAD, "warehouse": SHELF, "purchase_order_item": "row1"})
		receipt.append("items", {"item_code": ITEM, "project": JOB_A, "warehouse": SHELF, "purchase_order_item": "row2"})
		return receipt

	def test_a_receipt_made_in_the_desk_moves_its_overhead_line_off_the_shelf_on_first_save(self):
		base._rule(ITEM, BIN_B)
		receipt = self._receipt()
		procurement.default_receipt_warehouses(receipt)
		self.assertEqual([row.warehouse for row in receipt.items], [BIN_B, SHELF])

	def test_a_saved_receipt_a_return_and_a_receipt_a_person_placed_are_left_alone(self):
		base._rule(ITEM, BIN_B)
		for label, receipt in (
			("already saved", self._receipt(__islocal=0)),
			("a return", self._receipt(is_return=1)),
			("submitted", self._receipt(docstatus=1)),
		):
			with self.subTest(label):
				procurement.default_receipt_warehouses(receipt)
				self.assertEqual([row.warehouse for row in receipt.items], [SHELF, SHELF])
		placed = self._receipt()
		placed.flags.receiving_places_chosen = True
		procurement.default_receipt_warehouses(placed)
		self.assertEqual([row.warehouse for row in placed.items], [SHELF, SHELF], "a scan at the shelf still wins")
		procurement.default_receipt_warehouses(None)

	def test_the_new_hooks_are_wired(self):
		hooks = HOOKS.read_text(encoding="utf-8")
		self.assertIn('"before_insert": "erpnext_enhancements.procurement_project.default_new_order_line"', hooks)
		self.assertIn('"before_validate": "erpnext_enhancements.procurement_project.default_receipt_warehouses"', hooks)
		receive = ast.unparse(
			next(n for n in ast.walk(ast.parse(RECEIVE.read_text(encoding="utf-8"))) if isinstance(n, ast.FunctionDef) and n.name == "receive_items")
		)
		self.assertIn("receipt.flags.receiving_places_chosen = bool(target or places)", receive)
		self.assertLess(receive.index("receiving_places_chosen"), receive.index("receipt.insert()"))

	def test_the_form_needs_read_on_purchase_orders(self):
		STATE["perm"] = {("Purchase Order", "read"): False}
		with self.assertRaises(FRAPPE.PermissionError):
			procurement.line_warehouses([{"key": "r1", "item_code": ITEM, "project": JOB_A}])

	def test_receiving_defaults_keep_the_orders_place_and_fix_the_overhead_line_on_the_shelf(self):
		base._rule(ITEM, BIN_B)
		doc = _order(
			[
				{"project": JOB_A, "warehouse": SHELF},
				{"project": OVERHEAD, "warehouse": SHELF},
				{"project": OVERHEAD, "warehouse": BIN_A},
				{"project": JOB_B, "warehouse": None},
			],
			docstatus=1,
		)
		base._table("Purchase Order")[doc.name] = dict(doc._data)
		out = procurement.receiving_defaults(doc.name)
		self.assertIn(("Purchase Order", "read"), STATE["checked"])
		self.assertEqual((out["shelf"], out["shelf_name"]), (SHELF, "Project Shelf"))
		lines = out["lines"]
		self.assertEqual([lines[f"row{n}"]["warehouse"] for n in (1, 2, 3, 4)], [SHELF, BIN_B, BIN_A, SHELF])
		self.assertEqual([lines[f"row{n}"]["changed"] for n in (1, 2, 3, 4)], [False, True, False, False])
		self.assertEqual(lines["row2"]["ordered_warehouse"], SHELF)
		self.assertEqual(lines["row2"]["warehouse_name"], "Bin A1-1-2")
		self.assertTrue(lines["row2"]["overhead"])

	def test_a_receipt_draft_from_the_intake_gets_the_same_places(self):
		base._rule(ITEM, BIN_B)
		receipt = base.Doc("Purchase Receipt", {"company": COMPANY, "set_warehouse": SHELF})
		receipt.append("items", {"item_code": ITEM, "project": OVERHEAD, "warehouse": SHELF})
		receipt.append("items", {"item_code": ITEM, "project": JOB_A, "warehouse": SHELF})
		self.assertEqual(procurement.apply_receiving_defaults(receipt), 1)
		self.assertEqual([row.warehouse for row in receipt.items], [BIN_B, SHELF])
		self.assertIsNone(receipt.set_warehouse, "the header no longer names one place for lines in two")

	def test_a_receipt_draft_is_untouched_with_no_shelf_and_never_raises(self):
		_reset(shelf_on=False)
		receipt = base.Doc("Purchase Receipt", {"company": COMPANY})
		receipt.append("items", {"item_code": ITEM, "project": OVERHEAD, "warehouse": SHELF})
		self.assertEqual(procurement.apply_receiving_defaults(receipt), 0)
		self.assertEqual(procurement.apply_receiving_defaults(None), 0)

	def test_the_intake_drafts_call_it(self):
		for name in ("packing_slip.py", "vendor_bill.py"):
			source = (APP / "accounting_intake" / "actions" / name).read_text(encoding="utf-8")
			self.assertIn("apply_receiving_defaults(pr)", source, name)
			self.assertLess(source.index("apply_receiving_defaults(pr)"), source.index("pr.insert("), name)

	def test_receive_items_reads_a_place_per_line_and_the_scan_still_wins(self):
		tree = ast.parse(RECEIVE.read_text(encoding="utf-8"))
		functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
		namespace = {"frappe": types.SimpleNamespace(parse_json=json.loads)}
		exec(compile(ast.Module(body=[functions["_typed_warehouses"]], type_ignores=[]), "typed", "exec"), namespace)
		typed = namespace["_typed_warehouses"]
		self.assertEqual(
			typed(
				json.dumps(
					[
						{"purchase_order_item": "a", "qty": 1, "warehouse": " Bin A "},
						{"purchase_order_item": "b", "qty": 1},
						{"purchase_order_item": "", "warehouse": "Bin C"},
						"junk",
					]
				)
			),
			{"a": "Bin A"},
		)
		self.assertEqual(typed(None), {})
		source = ast.unparse(functions["receive_items"])
		# With one place for the whole receipt (a scan at a bin), the per-line places are not read.
		self.assertIn("places = {} if target else", source)
		self.assertIn("_receiving_warehouse(place, po.company)", source)
		self.assertLess(source.index("_receiving_warehouse(place, po.company)"), source.index("make_purchase_receipt("))


# --------------------------------------------------------------------------- the patch


class TestThePatch(_Base):
	def setUp(self):
		"""Production before the patch: a Stores warehouse, no shelf, a saved settings Single."""
		_reset(shelf_on=False, fallback=False)
		del base._table("Warehouse")[SHELF]
		base._warehouse(STORES, "Stores", parent_warehouse="All Warehouses - SF")
		base._table("Company")[COMPANY] = {"name": COMPANY, "abbr": "SF", "default_warehouse": STORES}
		base._default(ITEM, STORES)
		base._rule(ITEM, STORES)
		base._bin(ITEM, STORES, 7)
		STATE["singles"][(SETTINGS, "default_warehouse")] = "All Warehouses - SF"
		STATE["meta_fields"] = [
			{"fieldname": "undo_window_minutes", "default": "30"},
			{"fieldname": "require_variance_reason", "default": "1"},
			{"fieldname": "project_shelf_warehouse", "default": None},
		]

	def _run(self):
		patch.execute()
		return [(title, message) for title, message in STATE["errors"] if title == patch.REPORT_TITLE]

	def test_it_renames_stores_with_frappes_rename_and_sets_the_label(self):
		reports = self._run()
		self.assertEqual(reports, [], "a clean run writes no Error Log row")
		self.assertEqual(
			STATE["renames"],
			[{"doctype": "Warehouse", "old": STORES, "new": SHELF, "force": True, "show_alert": False, "rebuild_search": False}],
		)
		warehouses = base._table("Warehouse")
		self.assertNotIn(STORES, warehouses)
		self.assertEqual(warehouses[SHELF]["warehouse_name"], "Project Shelf")
		# Where it sits is not changed, and it stays enabled.
		self.assertEqual((warehouses[SHELF]["parent_warehouse"], warehouses[SHELF]["disabled"]), ("All Warehouses - SF", 0))

	def test_everything_that_pointed_at_stores_points_at_the_shelf(self):
		self._run()
		self.assertEqual(base._table("Item Default")[f"ID-{ITEM}-1"]["default_warehouse"], SHELF)
		self.assertEqual([r["warehouse"] for r in base._table("Putaway Rule").values()], [SHELF])
		on_hand_at = {r["warehouse"] for r in base._table("Bin").values()}
		self.assertIn(SHELF, on_hand_at)
		self.assertNotIn(STORES, on_hand_at)
		self.assertEqual(base._table("Company")[COMPANY]["default_warehouse"], SHELF)
		# No stock moved and nothing was cleared.
		self.assertEqual(base._bin_qty(ITEM, SHELF), 7.0)
		self.assertEqual([r["disable"] for r in base._table("Putaway Rule").values()], [0])

	def test_it_fills_both_settings_and_indexes_the_name(self):
		self._run()
		self.assertEqual(STATE["singles"][(SETTINGS, "project_shelf_warehouse")], SHELF)
		self.assertEqual(STATE["singles"][(SETTINGS, "overhead_receiving_warehouse")], ROOM)
		self.assertEqual(STATE["indexed"], ["Warehouse"])
		self.assertIn((SETTINGS, SETTINGS), STATE["cache_cleared"])
		# A saved Single: no declared default is written alongside.
		self.assertNotIn((SETTINGS, "undo_window_minutes"), STATE["singles"])

	def test_twice_is_once(self):
		self._run()
		before = json.dumps(STATE["db"], sort_keys=True, default=str)
		singles = dict(STATE["singles"])
		reports = self._run()
		self.assertEqual(reports, [])
		self.assertEqual(len(STATE["renames"]), 1)
		self.assertEqual(json.dumps(STATE["db"], sort_keys=True, default=str), before)
		self.assertEqual(STATE["singles"], singles)

	def test_a_stored_setting_is_never_written_over_an_emptied_one_included(self):
		STATE["singles"][(SETTINGS, "project_shelf_warehouse")] = None
		STATE["singles"][(SETTINGS, "overhead_receiving_warehouse")] = BIN_A
		self._run()
		self.assertIsNone(STATE["singles"][(SETTINGS, "project_shelf_warehouse")])
		self.assertEqual(STATE["singles"][(SETTINGS, "overhead_receiving_warehouse")], BIN_A)

	def test_a_never_saved_single_gets_its_declared_defaults_first(self):
		STATE["singles"].clear()
		self._run()
		self.assertEqual(STATE["singles"][(SETTINGS, "undo_window_minutes")], "30")
		self.assertEqual(STATE["singles"][(SETTINGS, "require_variance_reason")], "1")
		self.assertEqual(STATE["singles"][(SETTINGS, "project_shelf_warehouse")], SHELF)

	def test_materialize_defaults_writes_nothing_once_a_row_is_stored(self):
		self.assertEqual(patch.materialize_defaults({"default_warehouse"}), 0)
		self.assertNotIn((SETTINGS, "undo_window_minutes"), STATE["singles"])
		self.assertEqual(patch.materialize_defaults(set()), 2)

	def test_with_no_fallback_room_that_one_setting_is_skipped_and_said(self):
		del base._table("Warehouse")[ROOM]
		reports = self._run()
		self.assertEqual(len(reports), 1)
		self.assertIn("5. The Overhead receiving setting: skipped", reports[0][1])
		self.assertIn("By hand:", reports[0][1])
		self.assertNotIn((SETTINGS, "overhead_receiving_warehouse"), STATE["singles"])
		self.assertEqual(STATE["singles"][(SETTINGS, "project_shelf_warehouse")], SHELF)

	def test_a_site_without_the_shop_group_is_left_alone_and_nobody_is_alerted(self):
		del base._table("Warehouse")["Inventory - SF"]
		reports = self._run()
		self.assertEqual(reports, [], "no Error Log row: there is nothing for anyone to finish")
		self.assertEqual(STATE["errors"], [])
		self.assertNotIn("renames", STATE)
		self.assertIn(STORES, base._table("Warehouse"))
		self.assertNotIn((SETTINGS, "project_shelf_warehouse"), STATE["singles"])

	def test_a_site_with_no_stores_at_all_is_left_alone_and_nobody_is_alerted(self):
		del base._table("Warehouse")[STORES]
		reports = self._run()
		self.assertEqual(reports, [])
		self.assertEqual(STATE["errors"], [])
		self.assertNotIn("indexed", STATE)
		self.assertNotIn((SETTINGS, "project_shelf_warehouse"), STATE["singles"])

	def test_the_quiet_run_still_says_what_it_found(self):
		del base._table("Warehouse")["Inventory - SF"]
		outcome = patch.rename_warehouse()
		self.assertEqual(outcome[0], patch.ABSENT)
		text = patch.report({key: (patch.ABSENT, "nothing was renamed on this site") for key, _label in patch.STEPS})
		self.assertEqual(text.count("not needed here"), len(patch.STEPS))
		self.assertNotIn("By hand:", text)

	def test_saved_list_filters_that_named_stores_are_pointed_at_the_shelf(self):
		"""Frappe's rename looks for them with LIKE and no wildcards, so it rewrites none."""
		STATE["user_settings"] = [
			{"user": "pat@example.com", "doctype": "Bin", "data": '{"List":{"filters":[["Bin","warehouse","=","Stores - SF"]]}}'},
			{"user": "pat@example.com", "doctype": "Item", "data": '{"List":{"filters":[["Item","item_group","=","Parts"]]}}'},
			{"user": "sam@example.com", "doctype": "Stock Ledger Entry", "data": '{"Report":{"filters":[["Stock Ledger Entry","warehouse","in",["Stores - SF","Bin A1-1-1 - SF"]]]}}'},
			{"user": "sam@example.com", "doctype": "Warehouse", "data": '{"List":{"filters":[["Warehouse","name","like","%Old Stores - SF%"]]}}'},
		]
		reports = self._run()
		self.assertEqual(reports, [])
		saved = {(row["user"], row["doctype"]): row["data"] for row in STATE["user_settings"]}
		self.assertIn('"Project Shelf - SF"', saved[("pat@example.com", "Bin")])
		self.assertNotIn("Stores - SF", saved[("pat@example.com", "Bin")])
		self.assertIn('["Project Shelf - SF","Bin A1-1-1 - SF"]', saved[("sam@example.com", "Stock Ledger Entry")])
		# A row that never named it, and a longer text that only contains it, are untouched.
		self.assertIn("Parts", saved[("pat@example.com", "Item")])
		self.assertIn("%Old Stores - SF%", saved[("sam@example.com", "Warehouse")])
		self.assertEqual(
			sorted(STATE["cache_dropped"]),
			[("_user_settings", "Bin::pat@example.com"), ("_user_settings", "Stock Ledger Entry::sam@example.com")],
		)

	def test_the_report_counts_people_and_never_names_them(self):
		"""The migrate output lands in the build log, and a user id is a sign-in address."""
		STATE["user_settings"] = [
			{"user": "pat@example.com", "doctype": "Bin", "data": '{"filters":[["Bin","warehouse","=","Stores - SF"]]}'},
			{"user": "pat@example.com", "doctype": "Stock Ledger Entry", "data": '{"filters":[["x","warehouse","=","Stores - SF"]]}'},
			{"user": "sam@example.com", "doctype": "Bin", "data": '{"filters":[["Bin","warehouse","=","Stores - SF"]]}'},
		]
		base._warehouse(SHELF, "Project Shelf")
		status, detail = patch.rewrite_saved_filters(SHELF)
		self.assertEqual(status, patch.DONE)
		self.assertEqual(
			detail, "3 saved list settings of 2 people now name Project Shelf - SF: lists Bin, Stock Ledger Entry"
		)
		self.assertNotIn("@", detail)
		STATE["user_settings"] = STATE["user_settings"][:1]
		STATE["user_settings"][0]["data"] = '{"filters":[["Bin","warehouse","=","Stores - SF"]]}'
		self.assertIn("1 saved list setting of 1 person now name", patch.rewrite_saved_filters(SHELF)[1])

	def test_nothing_the_whole_run_prints_or_logs_names_a_person(self):
		import contextlib
		import io

		STATE["user_settings"] = [
			{"user": "pat@example.com", "doctype": "Bin", "data": '{"filters":[["Bin","warehouse","=","Stores - SF"]]}'},
			{"user": "sam@example.com", "doctype": "Bin", "data": '{"filters":[["Bin","warehouse","=","Stores - SF"]]}'},
		]
		# One skipped step, so the run also writes its Error Log row.
		del base._table("Warehouse")[ROOM]
		printed = io.StringIO()
		with contextlib.redirect_stdout(printed):
			reports = self._run()
		self.assertIn("3. People's saved list filters: done. 2 saved list settings of 2 people", printed.getvalue())
		self.assertEqual(len(reports), 1)
		for text in (printed.getvalue(), *(f"{title} {message}" for title, message in STATE["errors"])):
			self.assertNotIn("@", text)
			self.assertNotIn("example.com", text)

	def test_rewriting_saved_filters_twice_is_once(self):
		STATE["user_settings"] = [
			{"user": "pat@example.com", "doctype": "Bin", "data": '{"filters":[["Bin","warehouse","=","Stores - SF"]]}'},
		]
		base._warehouse(SHELF, "Project Shelf")
		self.assertEqual(patch.rewrite_saved_filters(SHELF)[0], patch.DONE)
		before = [dict(row) for row in STATE["user_settings"]]
		self.assertEqual(patch.rewrite_saved_filters(SHELF)[0], patch.ALREADY)
		self.assertEqual(STATE["user_settings"], before)

	def test_a_failure_rewriting_saved_filters_undoes_nothing_else(self):
		STATE["filters_fail"] = True
		reports = self._run()
		self.assertIn(SHELF, base._table("Warehouse"))
		self.assertEqual(STATE["singles"][(SETTINGS, "project_shelf_warehouse")], SHELF)
		self.assertIn("3. People's saved list filters: failed", reports[0][1])

	def test_nothing_is_merged_when_both_exist(self):
		base._warehouse(SHELF, "Project Shelf")
		reports = self._run()
		self.assertNotIn("renames", STATE)
		self.assertIn("both Stores - SF and Project Shelf - SF exist", reports[0][1])
		self.assertIn(STORES, base._table("Warehouse"))

	def test_a_group_called_stores_is_left_alone(self):
		base._table("Warehouse")[STORES]["is_group"] = 1
		reports = self._run()
		self.assertNotIn("renames", STATE)
		self.assertIn("is a group of warehouses", reports[0][1])

	def test_a_failed_rename_is_rolled_back_logged_and_stops_nothing(self):
		STATE["rename_fails"] = True
		reports = self._run()
		self.assertIn(STORES, base._table("Warehouse"), "rolled back to the step's savepoint")
		self.assertNotIn(SHELF, base._table("Warehouse"))
		self.assertIn("1. The warehouse: failed. RuntimeError: the rename could not finish", reports[0][1])
		self.assertTrue(any(title == "Project Shelf patch: renaming the warehouse failed" for title, _m in STATE["errors"]))
		# The later steps ran and said why they had nothing to do.
		self.assertIn("4. The Project Shelf setting: skipped", reports[0][1])
		self.assertIn(patch.RERUN, reports[0][1])

	def test_a_failed_index_does_not_undo_the_rename(self):
		STATE["search_fails"] = True
		reports = self._run()
		self.assertIn(SHELF, base._table("Warehouse"))
		self.assertEqual(STATE["singles"][(SETTINGS, "project_shelf_warehouse")], SHELF)
		self.assertIn("2. The search index: failed", reports[0][1])

	def test_it_cannot_raise_even_when_everything_does(self):
		original = FRAPPE.db.get_value
		FRAPPE.db.get_value = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the database is gone"))
		try:
			patch.execute()
		finally:
			FRAPPE.db.get_value = original

	def test_the_report_counts_what_points_at_the_shelf(self):
		STATE["count_row"] = (3, 2, 1)
		lines = patch.counts(SHELF)
		self.assertEqual(len(lines), 5)
		self.assertTrue(all(line.startswith("- ") for line in lines))
		self.assertIn("No stock was moved", lines[0])
		self.assertIn("None was changed", lines[1])
		self.assertEqual(patch.counts(None), [])
		text = patch.report({"rename": (patch.DONE, "x")}, lines)
		self.assertIn("What points at the Project Shelf now:", text)
		self.assertIn("2. The search index: skipped. not reached", text)

	def test_it_reads_singles_with_sql_never_through_get_value(self):
		source = (APP / "patches" / "rename_stores_to_project_shelf.py").read_text(encoding="utf-8")
		self.assertIn('"select field from tabSingles where doctype = %s"', source)
		self.assertNotIn('get_value("Singles"', source)
		self.assertNotIn("delete_doc", source)

	def test_it_is_registered_after_the_job_sites_patch(self):
		lines = [line.strip() for line in PATCHES_TXT.read_text(encoding="utf-8").splitlines()]
		mine = "erpnext_enhancements.patches.rename_stores_to_project_shelf"
		self.assertEqual(lines.count(mine), 1)
		self.assertLess(lines.index("erpnext_enhancements.patches.add_job_sites_warehouse"), lines.index(mine))


# --------------------------------------------------------------------------- the wiring


class TestWiring(unittest.TestCase):
	def test_the_settings_fields(self):
		doc = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
		fields = {f["fieldname"]: f for f in doc["fields"]}
		for name in ("project_shelf_warehouse", "overhead_receiving_warehouse"):
			self.assertIn(name, doc["field_order"])
			self.assertEqual((fields[name]["fieldtype"], fields[name]["options"]), ("Link", "Warehouse"))
			# No declared default: a default on a new field of a Single never reaches the stored
			# row, and a warehouse's name is per site. The patch fills it.
			self.assertNotIn("default", fields[name])
			self.assertNotIn("hidden", fields[name])
		source = SETTINGS_PY.read_text(encoding="utf-8")
		self.assertIn('"project_shelf_warehouse": None,', source)
		self.assertIn('"overhead_receiving_warehouse": None,', source)

	def test_the_modules_names_match_the_settings(self):
		source = (APP / "inventory_enhancements" / "project_shelf.py").read_text(encoding="utf-8")
		self.assertIn('SETTING = "project_shelf_warehouse"', source)
		self.assertIn('FALLBACK_SETTING = "overhead_receiving_warehouse"', source)

	def test_the_three_functions_other_modules_call_keep_their_signatures(self):
		"""The reports and the Procurement Tracker call these. Changing one breaks a sibling."""
		tree = ast.parse((APP / "inventory_enhancements" / "project_shelf.py").read_text(encoding="utf-8"))
		functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
		self.assertEqual([a.arg for a in functions["warehouse"].args.args], [])
		self.assertEqual([a.arg for a in functions["is_shelf"].args.args], ["name"])
		self.assertEqual([a.arg for a in functions["not_shop_warehouses"].args.args], [])
		stock = functions["shelf_stock"].args
		self.assertEqual([a.arg for a in stock.args], ["project", "item_code"])
		self.assertEqual([ast.unparse(d) for d in stock.defaults], ["None", "None"])
		returned = {
			key.value
			for node in ast.walk(functions["shelf_stock"])
			if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
			for key in node.value.keys
		}
		self.assertEqual(returned, {"warehouse", "rows", "projects", "untagged", "untagged_movements", "exact"})

	def test_this_suite_has_its_own_ci_step(self):
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_project_shelf -v", CI.read_text(encoding="utf-8"))


if __name__ == "__main__":
	unittest.main()
