# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for job-site warehouses (v1.801.0).

The owner's decision of 2026-11-11: material sent to a Build job goes to that job's own site
warehouse, one per project, and stock on a job site must not read as stock the shop has.

Three layers, cheapest first:

* ``inventory_enhancements/job_site_rules.py`` has no Frappe in it and is run directly: the
  site's name, who may have one, whose job a take is, and the arithmetic that takes job sites
  out of a total and out of ERPNext's rolled-up projected quantities.
* ``inventory_enhancements/job_sites.py``, ``api/stock_scan.py`` and ``bin_assignments.py``
  run for real on ``tests/test_bin_assignments``' in-memory ``frappe`` stub (reused, so this
  suite has its own CI step): a site is created once, under the group, past Warehouse
  permissions and nothing else; a move to or from a site carries the job on the stock entry
  and on the log row; a take at a site is charged to the site's job whatever the page sent; a
  site is never an item's bin; and what the page totals and lists leaves job sites out.
* ``patches/add_job_sites_warehouse.py``: the field, the tree, the group, the setting and the
  old hand-made warehouse, each step on its own, never raising, safe twice.

What it cannot see: ERPNext's own Warehouse ``validate`` and the nested-set numbering of a
real insert, which need a bench.

Run: python -m unittest erpnext_enhancements.tests.test_job_sites -v
"""

import ast
import json
import sys
import types
import unittest
from pathlib import Path

from erpnext_enhancements.inventory_enhancements import job_site_rules as rules
from erpnext_enhancements.inventory_enhancements import stock_scan_rules as scan_rules
from erpnext_enhancements.tests import test_bin_assignments as base

APP = Path(__file__).resolve().parents[1]
SETTINGS_JSON = (
	APP / "inventory_enhancements" / "doctype" / "inventory_scanner_settings" / "inventory_scanner_settings.json"
)
SETTINGS_PY = SETTINGS_JSON.with_suffix(".py")
REORDER = APP / "inventory_enhancements" / "reorder_board.py"
PATCHES_TXT = APP / "patches.txt"
HOOKS = APP / "hooks.py"

BIN_A, BIN_B, STORES = base.BIN_A, base.BIN_B, base.STORES
STATE = base.STATE
COMPANY = base.COMPANY
FIELD = rules.SITE_FIELD
GROUP = "Job Sites - SF"
BUILD = "PRJ-90001"
SERVICE = "PRJ-90100"
SITE = "Job Site PRJ-90001 Water Wall - SF"

_OWN_MODULES = (
	"erpnext_enhancements.inventory_enhancements.job_sites",
	"erpnext_enhancements.patches.add_job_sites_warehouse",
	"frappe.custom",
	"frappe.custom.doctype",
	"frappe.custom.doctype.custom_field",
	"frappe.custom.doctype.custom_field.custom_field",
	"frappe.utils.nestedset",
)

sites = None
scan = None
bins = None
patch = None
FRAPPE = None
_ORIGINAL = {}


def setUpModule():
	global sites, scan, bins, patch, FRAPPE
	for name in _OWN_MODULES:
		sys.modules.pop(name, None)
	base.setUpModule()
	FRAPPE = base.FRAPPE
	scan, bins = base.scan, base.bins

	# What ERPNext and Frappe have that the shared stub does not, and this feature reads.
	FRAPPE.db.has_column = lambda doctype, column: (doctype, column) in STATE.get("columns", ())
	FRAPPE.utils.escape_html = lambda text: text

	_ORIGINAL["get_all"] = FRAPPE.get_all

	def _passes(row, field, op, want):
		value = row.get(field)
		if op == "is":
			return bool(value) if want == "set" else not value
		if op == "in":
			return value in want
		if op == "not in":
			return value not in want
		if op == "=":
			# An unset Check reads as 0 in the database; a row this stub stored without it is None.
			return value == want or (not value and not want)
		if op == "like":
			return want.strip("%").lower() in str(value or "").lower()
		raise AssertionError(f"this stub does not know the filter operator {op!r}")

	def get_all(doctype, filters=None, **kwargs):
		# The shared stub matches dict filters only. This feature sends two lists: ``[[field,
		# "is", "set"]]`` for the sites, and the job picker's status and project-type tests.
		if isinstance(filters, list):
			rest = {k: v for k, v in kwargs.items() if k not in ("fields", "or_filters", "limit")}
			rows = _ORIGINAL["get_all"](doctype, None, **rest)
			rows = [r for r in rows if all(_passes(r, *f) for f in filters)]
			fields = kwargs.get("fields")
			return [base._dict({f: r.get(f) for f in fields}) for r in rows] if fields else rows
		return _ORIGINAL["get_all"](doctype, filters, **kwargs)

	FRAPPE.get_all = get_all

	_ORIGINAL["get_value"] = FRAPPE.db.get_value

	def get_value(doctype, filters, fieldname="name", **kwargs):
		# The stub has no transactions, so it cannot show isolation. It can show that a lock
		# was asked for, on which table and in which order.
		if kwargs.get("for_update"):
			STATE.setdefault("locks", []).append(doctype)
		return _ORIGINAL["get_value"](doctype, filters, fieldname, **kwargs)

	FRAPPE.db.get_value = get_value

	_ORIGINAL["sql"] = FRAPPE.db.sql

	def sql(query, values=None, as_dict=False, **kwargs):
		text = " ".join(query.split())
		if "as exact_code" in text:  # search_items
			return [base._dict(r) for r in STATE.get("search_rows", [])]
		if "as undecided" in text and STATE.get("items_at") is not None:  # _items_at
			return [base._dict(r) for r in STATE["items_at"]]
		if text.startswith("select count(*), count(distinct lft)"):
			return [STATE.get("tree_counts", (0, 0))]
		return _ORIGINAL["sql"](query, values, as_dict=as_dict, **kwargs)

	FRAPPE.db.sql = sql

	def add_comment(self, comment_type, text=None):
		STATE.setdefault("comments", []).append((self.doctype, self.name, comment_type, text))

	base.Doc.add_comment = add_comment

	# create_custom_fields and rebuild_tree, as the patch imports them.
	for dotted in ("frappe.custom", "frappe.custom.doctype", "frappe.custom.doctype.custom_field"):
		sys.modules[dotted] = types.ModuleType(dotted)
	custom_field = types.ModuleType("frappe.custom.doctype.custom_field.custom_field")

	def create_custom_fields(fields, update=False):
		if STATE.get("field_fails"):
			raise RuntimeError("the column could not be added")
		STATE.setdefault("custom_fields", []).append((fields, update))
		STATE["columns"] = set(STATE.get("columns", ())) | {
			(doctype, row["fieldname"]) for doctype, rows in fields.items() for row in rows
		}

	custom_field.create_custom_fields = create_custom_fields
	sys.modules["frappe.custom.doctype.custom_field.custom_field"] = custom_field
	nestedset = types.ModuleType("frappe.utils.nestedset")
	nestedset.rebuild_tree = lambda doctype: STATE.setdefault("rebuilt", []).append(doctype)
	sys.modules["frappe.utils.nestedset"] = nestedset

	from erpnext_enhancements.inventory_enhancements import job_sites as sites_module
	from erpnext_enhancements.patches import add_job_sites_warehouse as patch_module

	sites, patch = sites_module, patch_module
	# stock_scan and bin_assignments imported job_sites under this stub already; one module.
	assert scan.job_sites is sites and bins.job_sites is sites
	sites._settings = lambda: dict(STATE["settings"])


def tearDownModule():
	FRAPPE.get_all = _ORIGINAL["get_all"]
	FRAPPE.db.get_value = _ORIGINAL["get_value"]
	FRAPPE.db.sql = _ORIGINAL["sql"]
	del base.Doc.add_comment
	for name in _OWN_MODULES:
		sys.modules.pop(name, None)
	base.tearDownModule()


def _project(name, project_name, project_type="Build", status="Active", company=COMPANY):
	base._table("Project")[name] = {
		"name": name,
		"project_name": project_name,
		"project_type": project_type,
		"status": status,
		"company": company,
	}


def _reset(ready=True, group=True, **settings):
	base._reset(settings={"job_sites_warehouse": GROUP if group else None, **settings})
	STATE["columns"] = {("Warehouse", FIELD)} if ready else set()
	base._warehouse(GROUP, "Job Sites", is_group=1, parent_warehouse="All Warehouses - SF", lft=30, rgt=40)
	_project(BUILD, "Water Wall")
	_project(SERVICE, "Spring Start-Up", project_type="Service")
	base._bin("PDT-0001", BIN_A, actual_qty=5)


def _open_site(project=BUILD):
	site, _created = sites.ensure_site(project)
	return site.name


class _Base(unittest.TestCase):
	def setUp(self):
		_reset()

	def assertRefused(self, fn, *words, exc=None, **kwargs):
		with self.assertRaises(exc or FRAPPE.ValidationError) as caught:
			fn(**kwargs)
		for word in words:
			self.assertIn(word, str(caught.exception))
		return str(caught.exception)


# --------------------------------------------------------------------------- the rules


class TestRules(unittest.TestCase):
	def test_blank_project_types_mean_build(self):
		for blank in (None, "", "  \n ,"):
			with self.subTest(setting=blank):
				self.assertEqual(rules.project_types(blank), ("Build",))

	def test_project_types_one_per_line_or_comma(self):
		self.assertEqual(rules.project_types("Build\n Events ,Design\nBuild"), ("Build", "Events", "Design"))

	def test_a_site_is_named_for_its_job(self):
		self.assertEqual(rules.site_name("PRJ-90001", "Water Wall"), "Job Site PRJ-90001 Water Wall")

	def test_a_site_name_never_carries_stray_spaces(self):
		"""Production has project names ending in two spaces. A trailing space in a docname is
		invisible in every list and breaks every lookup typed by hand."""
		self.assertEqual(rules.site_name(" PRJ-90002 ", "Lobby  Fountain 1  "), "Job Site PRJ-90002 Lobby Fountain 1")

	def test_a_site_name_is_safe_as_a_docname_and_in_a_url(self):
		"""Frappe refuses ``<`` and ``>`` in a name; a slash or a quote breaks routes and labels.
		The ordinary punctuation of a job's name stays."""
		self.assertEqual(
			rules.site_name("PRJ-90003", 'Pool & Runnel Falls - North <3/4"> (Phase 2)'),
			"Job Site PRJ-90003 Pool & Runnel Falls - North 3 4 (Phase 2)",
		)

	def test_a_long_name_is_cut_and_the_id_is_whole(self):
		name = rules.site_name("PRJ-90003", "Reflection Pool " * 20)
		self.assertLessEqual(len(name), rules.MAX_SITE_NAME)
		self.assertTrue(name.startswith("Job Site PRJ-90003 Reflection Pool"))
		self.assertEqual(name, name.rstrip())

	def test_a_job_with_no_name_of_its_own_gets_the_id_once(self):
		for label in (None, "", "PRJ-00001"):
			with self.subTest(project_name=label):
				self.assertEqual(rules.site_name("PRJ-00001", label), "Job Site PRJ-00001")

	def test_a_finished_job_is_refused(self):
		for status in sorted(scan_rules.CLOSED_JOB_STATUSES):
			with self.subTest(status=status):
				said = rules.site_refusal(
					"PRJ-1", "Water Wall", status, "Build", ("Build",), scan_rules.CLOSED_JOB_STATUSES
				)
				self.assertIn("Water Wall", said)
				self.assertIn(status, said)

	def test_a_job_that_keeps_no_site_is_told_what_to_do_instead(self):
		said = rules.site_refusal(
			"PRJ-2", "Spring Start-Up", "Active", "Service", ("Build",), scan_rules.CLOSED_JOB_STATUSES
		)
		self.assertIn("Build jobs", said)
		self.assertIn("Spring Start-Up", said)
		self.assertIn("pick the job at the top of the page and take them", said)

	def test_a_job_with_no_type_keeps_no_site(self):
		self.assertIsNotNone(
			rules.site_refusal("PRJ-3", "X", "Active", None, ("Build",), scan_rules.CLOSED_JOB_STATUSES)
		)

	def test_an_open_build_job_may_have_one(self):
		self.assertIsNone(
			rules.site_refusal("PRJ-1", "Water Wall", "Active", "Build", ("Build",), scan_rules.CLOSED_JOB_STATUSES)
		)

	def test_a_site_that_exists_is_not_held_to_the_type_but_is_to_the_status(self):
		closed = scan_rules.CLOSED_JOB_STATUSES
		self.assertIsNone(rules.site_refusal("PRJ-2", "X", "Active", "Service", ("Build",), closed, has_site=True))
		self.assertIsNotNone(
			rules.site_refusal("PRJ-2", "X", "Completed", "Service", ("Build",), closed, has_site=True)
		)

	def test_a_take_at_a_site_is_the_sites_job(self):
		self.assertEqual(rules.site_take_project("PRJ-90001", "PRJ-90100"), "PRJ-90001")
		self.assertEqual(rules.site_take_project("PRJ-90001", None), "PRJ-90001")
		self.assertEqual(rules.site_take_project(None, "PRJ-90100"), "PRJ-90100")
		self.assertIsNone(rules.site_take_project(None, ""))

	def test_a_transfer_carries_the_job_it_goes_to_or_comes_back_from(self):
		self.assertEqual(rules.transfer_project(None, "PRJ-1"), "PRJ-1")
		self.assertEqual(rules.transfer_project("PRJ-1", None), "PRJ-1")
		self.assertEqual(rules.transfer_project("PRJ-1", "PRJ-2"), "PRJ-2")
		self.assertIsNone(rules.transfer_project(None, None))

	def test_the_shops_total_leaves_job_sites_out(self):
		bins_ = [("Bin A", 2), ("Site 1", 4), ("Stores", 1.5), ("Site 2", None)]
		self.assertEqual(rules.shop_and_site_totals(bins_, {"Site 1", "Site 2"}), (3.5, 4.0))
		self.assertEqual(rules.shop_and_site_totals(bins_, set()), (7.5, 0.0))
		self.assertEqual(rules.shop_and_site_totals([], {"Site 1"}), (0.0, 0.0))

	def test_site_stock_comes_out_of_every_group_above_it(self):
		projected = {
			"FIT-1": {"Bin A": 1.0, "Inventory": 1.0, "Site 1": 4.0, "Job Sites": 4.0, "All": 5.0},
			"FIT-2": {"Bin A": 3.0, "Inventory": 3.0, "All": 3.0},
		}
		ancestors = {
			"Bin A": frozenset({"Inventory", "All"}),
			"Site 1": frozenset({"Job Sites", "All"}),
		}
		out = rules.without_sites(projected, {"Site 1"}, ancestors)
		self.assertEqual(out["FIT-1"], {"Bin A": 1.0, "Inventory": 1.0, "Site 1": 4.0, "Job Sites": 0.0, "All": 1.0})
		self.assertEqual(out["FIT-2"], projected["FIT-2"])
		self.assertEqual(projected["FIT-1"]["All"], 5.0, "the caller's map is not changed")

	def test_an_order_on_its_way_to_a_site_comes_out_too(self):
		"""Projected quantity counts what is on order. A purchase order delivering straight to a
		job is not a restock of the shelf either."""
		out = rules.without_sites(
			{"PUMP": {"Site 1": 2.0, "All": 2.0}}, {"Site 1"}, {"Site 1": frozenset({"All"})}
		)
		self.assertEqual(out["PUMP"]["All"], 0.0)

	def test_with_no_job_sites_the_map_is_handed_back_as_it_was(self):
		projected = {"FIT-1": {"All": 5.0}}
		self.assertIs(rules.without_sites(projected, set(), {}), projected)

	def test_only_a_finished_job_stops_stock_going_to_its_site(self):
		closed = scan_rules.CLOSED_JOB_STATUSES
		for status in sorted(closed):
			with self.subTest(status=status):
				said = rules.send_refusal("PRJ-1", "Plaza Fountain", status, closed)
				self.assertIn("Plaza Fountain", said)
				self.assertIn(status, said)
				self.assertIn("moved back to a shelf", said)
		for status in ("Active", "Client Hold", "Parked", None):
			with self.subTest(status=status):
				self.assertIsNone(rules.send_refusal("PRJ-1", "Plaza Fountain", status, closed))

	def test_everything_under_a_group_at_any_depth(self):
		parents = {
			"All": None,
			"Inventory": "All",
			"Bin A": "Inventory",
			"Job Sites": "All",
			"Site 1": "Job Sites",
			"Out Of State": "Job Sites",
			"Site 2": "Out Of State",
			"Yard": "Site 2",
		}
		self.assertEqual(rules.descendants(parents, "Job Sites"), {"Site 1", "Out Of State", "Site 2", "Yard"})
		self.assertEqual(rules.descendants(parents, "Inventory"), {"Bin A"})
		self.assertEqual(rules.descendants(parents, "Bin A"), set())

	def test_descendants_of_nothing_and_a_tree_that_loops(self):
		self.assertEqual(rules.descendants({"A": "B"}, None), set())
		self.assertEqual(rules.descendants(None, "B"), set())
		looped = {"A": "B", "B": "A", "C": "Root"}
		self.assertEqual(rules.descendants(looped, "Root"), {"C"})

	def test_places_split_shop_first(self):
		a, b, c = {"warehouse": "A"}, {"warehouse": "S", "job_site": True}, {"warehouse": "B", "job_site": False}
		self.assertEqual(rules.split_places([b, a, c]), ([a, c], [b]))


# --------------------------------------------------------------------------- a job's site


class TestEnsureSite(_Base):
	def test_the_first_send_opens_the_site_under_the_group(self):
		site, created = sites.ensure_site(BUILD)
		self.assertTrue(created)
		row = base._table("Warehouse")[site.name]
		self.assertEqual(site.name, SITE)
		self.assertEqual(
			(row["warehouse_name"], row["parent_warehouse"], row["company"], row["is_group"], row[FIELD]),
			("Job Site PRJ-90001 Water Wall", GROUP, COMPANY, 0, BUILD),
		)

	def test_it_is_written_past_warehouse_permissions_and_says_so_on_the_project(self):
		"""The people who load a truck hold Stock User; ERPNext gives Warehouse create to Stock
		Manager and Item Manager. The site is the one write made past permissions, and it is
		noted where the job's people will read it."""
		STATE["perm"][("Warehouse", "create")] = False
		sites.ensure_site(BUILD)
		self.assertEqual([w for w in STATE["writes"] if w[1] == "Warehouse"], [("insert", "Warehouse", True)])
		((doctype, name, kind, text),) = STATE["comments"]
		self.assertEqual((doctype, name, kind), ("Project", BUILD, "Info"))
		self.assertIn(SITE, text)
		self.assertIn(base.USER, text)

	def test_the_second_send_finds_it(self):
		first, _ = sites.ensure_site(BUILD)
		second, created = sites.ensure_site(BUILD)
		self.assertFalse(created)
		self.assertEqual(second.name, first.name)
		self.assertEqual(len([w for w in STATE["writes"] if w[:2] == ("insert", "Warehouse")]), 1)
		self.assertEqual(len(STATE["comments"]), 1)

	def test_refusals_are_sentences_and_write_nothing(self):
		_project("PRJ-DONE", "Old Fountain", status="Completed")
		_project("PRJ-OTHER", "Elsewhere", company="Other Co")
		cases = (
			("", ("There is no job",)),
			("PRJ-NOPE", ("There is no job", "PRJ-NOPE")),
			("PRJ-DONE", ("Old Fountain", "Completed")),
			(SERVICE, ("Build jobs", "Spring Start-Up")),
			("PRJ-OTHER", ("Other Co", COMPANY)),
		)
		for project, words in cases:
			with self.subTest(project=project):
				self.assertRefused(sites.ensure_site, *words, project=project)
		self.assertEqual([w for w in STATE["writes"] if w[1] == "Warehouse"], [])

	def test_without_the_group_it_says_who_can_fix_it(self):
		_reset(group=False)
		self.assertRefused(sites.ensure_site, "not set up", "Inventory Scanner Settings", project=BUILD)
		self.assertFalse(sites.enabled())

	def test_a_setting_that_names_a_leaf_or_a_disabled_group_is_no_group(self):
		for change in ({"is_group": 0}, {"disabled": 1}, {"company": None}):
			with self.subTest(change=change):
				_reset()
				base._table("Warehouse")[GROUP].update(change)
				self.assertIsNone(sites.group())
				self.assertFalse(sites.enabled())

	def test_before_the_field_exists_nothing_is_a_job_site(self):
		_reset(ready=False)
		self.assertFalse(sites.ready())
		self.assertFalse(sites.enabled())
		self.assertEqual(sites.site_warehouses(), set())
		self.assertIsNone(sites.site_project(BIN_A))
		self.assertIsNone(sites.site_info(BIN_A))
		self.assertRefused(sites.ensure_site, "being set up", project=BUILD)

	def test_ready_never_raises(self):
		"""``has_column`` raises on a missing table rather than answering False."""

		def boom(doctype, column):
			raise RuntimeError("tabWarehouse is not there")

		original = FRAPPE.db.has_column
		FRAPPE.db.has_column = boom
		try:
			self.assertFalse(sites.ready())
		finally:
			FRAPPE.db.has_column = original

	def test_a_site_somebody_closed_is_not_quietly_reopened(self):
		name = _open_site()
		base._table("Warehouse")[name]["disabled"] = 1
		self.assertRefused(sites.ensure_site, "was closed", "Stock Manager", project=BUILD)

	def test_a_site_made_by_hand_for_another_kind_of_job_is_used(self):
		base._warehouse("Hand Made - SF", "Hand Made", parent_warehouse=GROUP, **{FIELD: SERVICE})
		site, created = sites.ensure_site(SERVICE)
		self.assertEqual((site.name, created), ("Hand Made - SF", False))

	def test_the_jobs_row_is_locked_before_the_site_is_looked_for(self):
		"""Two phones sending to one job at once. Without the lock both find no site and both
		insert, and under MariaDB's REPEATABLE READ the loser's re-read is still its own
		snapshot, so it cannot see the winner's row. The Project lock makes the second wait; the
		locking read of the Warehouse then sees what the first committed."""
		STATE["locks"] = []
		sites.ensure_site(BUILD)
		self.assertEqual(STATE["locks"][:2], ["Project", "Warehouse"])
		STATE["locks"] = []
		sites.ensure_site(BUILD)
		self.assertEqual(STATE["locks"], ["Project", "Warehouse"], "the second send locks and reads the same way")

	def test_the_duplicate_fallback_reads_with_a_lock_too(self):
		original = base.Doc.insert

		def racing_insert(self, ignore_permissions=False):
			if self.doctype == "Warehouse":
				base._warehouse(SITE, "Job Site PRJ-90001 Water Wall", parent_warehouse=GROUP, **{FIELD: BUILD})
				raise FRAPPE.DuplicateEntryError(SITE)
			return original(self, ignore_permissions=ignore_permissions)

		base.Doc.insert = racing_insert
		STATE["locks"] = []
		try:
			sites.ensure_site(BUILD)
		finally:
			base.Doc.insert = original
		self.assertEqual(STATE["locks"], ["Project", "Warehouse", "Warehouse"])

	def test_two_phones_at_once_end_with_one_site(self):
		original = base.Doc.insert

		def racing_insert(self, ignore_permissions=False):
			if self.doctype == "Warehouse":
				# The other phone's insert lands first; ours then collides on the name.
				base._warehouse(SITE, "Job Site PRJ-90001 Water Wall", parent_warehouse=GROUP, **{FIELD: BUILD})
				raise FRAPPE.DuplicateEntryError(SITE)
			return original(self, ignore_permissions=ignore_permissions)

		base.Doc.insert = racing_insert
		try:
			site, created = sites.ensure_site(BUILD)
		finally:
			base.Doc.insert = original
		self.assertEqual((site.name, created), (SITE, False))

	def test_site_info_names_the_job(self):
		name = _open_site()
		self.assertEqual(sites.site_info(name), {"project": BUILD, "project_name": "Water Wall"})
		self.assertEqual(sites.site_project(name), BUILD)
		self.assertIsNone(sites.site_project(BIN_A))

	def test_everything_under_the_group_is_on_a_job_site(self):
		"""Production's hand-made ``Job Site - SF`` has no job of its own. Under the group, its
		stock is still not on a shelf."""
		name = _open_site()
		base._warehouse("Job Site - SF", "Job Site", parent_warehouse=GROUP)
		self.assertEqual(sites.site_warehouses(), {name, "Job Site - SF"})
		self.assertIsNone(sites.site_project("Job Site - SF"))
		self.assertEqual(sites.site_map(), {name: BUILD})

	def test_a_sub_group_somebody_adds_later_is_still_on_a_job_site(self):
		name = _open_site()
		base._warehouse("Out Of State - SF", "Out Of State", is_group=1, parent_warehouse=GROUP)
		base._warehouse("Laydown Yard - SF", "Laydown Yard", parent_warehouse="Out Of State - SF")
		self.assertEqual(sites.site_warehouses(), {name, "Out Of State - SF", "Laydown Yard - SF"})
		self.assertNotIn(BIN_A, sites.site_warehouses())

	def test_a_site_moved_out_of_the_group_is_still_a_site(self):
		name = _open_site()
		base._table("Warehouse")[name]["parent_warehouse"] = "Inventory - SF"
		self.assertEqual(sites.site_warehouses(), {name})


class TestTheEndpoint(_Base):
	def test_it_answers_with_the_place_to_move_to(self):
		out = scan.job_site(BUILD)
		self.assertEqual(
			(out["warehouse"], out["warehouse_name"], out["job_site"], out["project"], out["project_name"], out["created"]),
			(SITE, "Job Site PRJ-90001 Water Wall", True, BUILD, "Water Wall", True),
		)
		self.assertFalse(scan.job_site(BUILD)["created"])

	def test_somebody_who_cannot_post_the_transfer_does_not_open_a_site(self):
		STATE["perm"][("Stock Entry", "submit")] = False
		self.assertRefused(scan.job_site, "Stock Entry", exc=FRAPPE.PermissionError, project=BUILD)
		self.assertNotIn(SITE, base._table("Warehouse"))

	def test_the_role_gate_comes_first(self):
		STATE["roles"] = ["Sales User"]
		self.assertRefused(scan.job_site, "not permitted", exc=FRAPPE.PermissionError, project=BUILD)
		self.assertNotIn(SITE, base._table("Warehouse"))

	def test_the_job_picker_lists_only_jobs_that_keep_a_site(self):
		names = [row["project"] for row in scan.search_projects(job_sites_only=1)]
		self.assertEqual(names, [BUILD])
		self.assertEqual(sorted(row["project"] for row in scan.search_projects()), sorted([BUILD, SERVICE]))

	def test_the_setting_widens_the_list(self):
		_reset(job_site_project_types="Build\nService")
		self.assertEqual(sorted(row["project"] for row in scan.search_projects(job_sites_only=1)), sorted([BUILD, SERVICE]))
		self.assertTrue(sites.ensure_site(SERVICE)[1])

	def test_the_boot_says_whether_the_door_is_open(self):
		self.assertEqual(scan.boot_payload()["settings"]["job_sites"], 1)
		_reset(group=False)
		self.assertEqual(scan.boot_payload()["settings"]["job_sites"], 0)
		_reset(ready=False)
		self.assertEqual(scan.boot_payload()["settings"]["job_sites"], 0)


# --------------------------------------------------------------------------- stock moving


class TestMoves(_Base):
	def test_sending_to_a_job_is_a_transfer_that_carries_the_job(self):
		site = _open_site()
		out = bins.move_to("PDT-0001", BIN_A, site, 3, base._ref())
		(se,) = base._vouchers("Stock Entry")
		self.assertEqual((se["stock_entry_type"], se["docstatus"], se["project"]), ("Material Transfer", 1, BUILD))
		(row,) = se["items"]
		self.assertEqual((row["s_warehouse"], row["t_warehouse"], row["qty"]), (BIN_A, site, 3.0))
		self.assertIn(f"for {BUILD}", se["remarks"])
		log = base._log(out["log"])
		self.assertEqual((log["action"], log["warehouse"], log["from_warehouse"], log["project"]), ("Move To", site, BIN_A, BUILD))

	def test_bringing_it_back_carries_the_job_it_came_from(self):
		site = _open_site()
		base._bin("PDT-0001", site, actual_qty=4)
		out = bins.move_to("PDT-0001", site, BIN_B, 1, base._ref())
		(se,) = base._vouchers("Stock Entry")
		self.assertEqual(se["project"], BUILD)
		self.assertEqual(base._log(out["log"])["project"], BUILD)

	def test_move_here_from_a_site_carries_it_too(self):
		site = _open_site()
		base._bin("PDT-0001", site, actual_qty=4)
		out = scan.move_here("PDT-0001", BIN_B, site, 2, base._ref())
		(se,) = base._vouchers("Stock Entry")
		self.assertEqual(se["project"], BUILD)
		self.assertEqual(base._log(out["log"])["project"], BUILD)

	def test_a_move_between_two_shelves_names_no_job(self):
		_open_site()
		out = bins.move_to("PDT-0001", BIN_A, BIN_B, 2, base._ref())
		(se,) = base._vouchers("Stock Entry")
		self.assertIsNone(se.get("project"))
		self.assertIsNone(base._log(out["log"]).get("project"))
		self.assertNotIn(" for ", se["remarks"])

	def test_a_site_is_never_an_items_home(self):
		site = _open_site()
		self.assertRefused(
			bins.move_to, "job site", item_code="PDT-0001", from_warehouse=BIN_A, to_warehouse=site, qty=5,
			client_ref=base._ref(), make_home=1,
		)
		self.assertEqual(base._vouchers("Stock Entry"), [])
		self.assertEqual(base._rules_for("PDT-0001", site), [])

	def test_nothing_is_assigned_to_a_site(self):
		site = _open_site()
		self.assertRefused(bins.assign, "job site", item_code="PDT-0001", warehouse=site, client_ref=base._ref())
		self.assertEqual(base._rules_for("PDT-0001", site), [])

	def test_nothing_more_is_sent_to_the_site_of_a_finished_job(self):
		"""``job_site`` refuses a finished job, but a site that exists is also reached by name,
		by its label and from "where it is already". Stock sent there could not be used (a take
		at the site is refused), only moved back."""
		site = _open_site()
		base._bin("PDT-0001", BIN_B, actual_qty=3)
		base._table("Project")[BUILD]["status"] = "Completed"
		self.assertRefused(
			bins.move_to, "Water Wall is Completed", "moved back to a shelf",
			item_code="PDT-0001", from_warehouse=BIN_A, to_warehouse=site, qty=1, client_ref=base._ref(1),
		)
		self.assertRefused(
			scan.move_here, "Water Wall is Completed",
			item_code="PDT-0001", warehouse=site, from_warehouse=BIN_B, qty=1, client_ref=base._ref(2),
		)
		self.assertEqual(base._vouchers("Stock Entry"), [])
		self.assertEqual(base._table("Stock Scan Log"), {})

	def test_leftovers_still_come_back_from_the_site_of_a_finished_job(self):
		site = _open_site()
		base._bin("PDT-0001", site, actual_qty=4)
		base._table("Project")[BUILD]["status"] = "Completed"
		bins.move_to("PDT-0001", site, BIN_B, 1, base._ref(1))
		scan.move_here("PDT-0001", BIN_A, site, 1, base._ref(2))
		first, second = base._vouchers("Stock Entry")
		self.assertEqual((first["project"], second["project"]), (BUILD, BUILD))
		self.assertEqual((first["docstatus"], second["docstatus"]), (1, 1))

	def test_a_job_on_hold_still_receives(self):
		site = _open_site()
		base._table("Project")[BUILD]["status"] = "Client Hold"
		bins.move_to("PDT-0001", BIN_A, site, 1, base._ref())
		self.assertEqual(len(base._vouchers("Stock Entry")), 1)

	def test_a_hand_made_site_for_another_kind_of_job_still_receives(self):
		"""A site that exists was decided on, by a Stock Manager in the Desk if not by the
		project-type rule. Only a finished job closes it to new stock."""
		base._warehouse("Hand Made - SF", "Hand Made", parent_warehouse=GROUP, **{FIELD: SERVICE})
		bins.move_to("PDT-0001", BIN_A, "Hand Made - SF", 1, base._ref())
		(se,) = base._vouchers("Stock Entry")
		self.assertEqual(se["project"], SERVICE)

	def test_the_hand_made_job_site_warehouse_is_never_an_items_bin_either(self):
		"""It has no job of its own, so the Project field says nothing about it. It sits under
		the group, and an item given it as a home would be reordered against a shelf that is not
		in the building."""
		base._warehouse("Job Site - SF", "Job Site", parent_warehouse=GROUP)
		self.assertRefused(bins.assign, "job site", item_code="PDT-0001", warehouse="Job Site - SF", client_ref=base._ref(1))
		self.assertRefused(
			bins.move_to, "job site", item_code="PDT-0001", from_warehouse=BIN_A, to_warehouse="Job Site - SF",
			qty=5, client_ref=base._ref(2), make_home=1,
		)
		self.assertEqual(base._rules_for("PDT-0001", "Job Site - SF"), [])
		self.assertEqual(base._vouchers("Stock Entry"), [])
		# A plain move there is still a move.
		bins.move_to("PDT-0001", BIN_A, "Job Site - SF", 1, base._ref(3))
		self.assertEqual(len(base._vouchers("Stock Entry")), 1)

	def test_a_take_at_a_site_is_charged_to_the_sites_job_whatever_the_page_sent(self):
		site = _open_site()
		base._bin("PDT-0001", site, actual_qty=4)
		for n, sent in enumerate((None, SERVICE, BUILD), start=1):
			with self.subTest(sent=sent):
				out = scan.take("PDT-0001", site, 1, base._ref(n), project=sent)
				se = base._vouchers("Stock Entry")[-1]
				self.assertEqual((se["stock_entry_type"], se["project"]), ("Material Issue", BUILD))
				self.assertEqual(base._log(out["log"])["project"], BUILD)

	def test_a_take_at_a_shelf_is_the_picked_job_as_before(self):
		_open_site()
		scan.take("PDT-0001", BIN_A, 1, base._ref(1), project=SERVICE)
		scan.take("PDT-0001", BIN_A, 1, base._ref(2))
		first, second = base._vouchers("Stock Entry")
		self.assertEqual(first["project"], SERVICE)
		self.assertIsNone(second.get("project"))

	def test_a_take_at_the_site_of_a_finished_job_is_refused(self):
		site = _open_site()
		base._bin("PDT-0001", site, actual_qty=4)
		base._table("Project")[BUILD]["status"] = "Completed"
		self.assertRefused(scan.take, "Completed", item_code="PDT-0001", warehouse=site, qty=1, client_ref=base._ref())


# --------------------------------------------------------------------------- what reads as available


class TestAvailability(_Base):
	def setUp(self):
		super().setUp()
		self.site = _open_site()
		base._bin("PDT-0001", self.site, actual_qty=9)
		base._bin("PDT-0001", STORES, actual_qty=2)

	def test_a_site_is_marked_and_listed_after_every_shelf(self):
		places = scan._elsewhere("PDT-0001", COMPANY, exclude=BIN_B)
		self.assertEqual([(p["warehouse"], p["job_site"]) for p in places], [(BIN_A, False), (STORES, False), (self.site, True)])

	def test_a_site_found_by_name_is_still_marked_a_site(self):
		"""Move to…'s *Find a location* reaches a site that already exists. The page must know
		not to offer it as the item's bin, which the server would refuse."""
		found = {row["warehouse"]: row["job_site"] for row in scan.search_locations("job site")}
		self.assertEqual(found, {self.site: True})
		shelves = {row["warehouse"]: row["job_site"] for row in scan.search_locations("Bin A1")}
		self.assertTrue(shelves)
		self.assertFalse(any(shelves.values()))

	def test_the_search_total_is_the_shops(self):
		STATE["search_rows"] = [
			{"item_code": "PDT-0001", "item_name": "PART", "image": None, "stock_uom": "Unit", "is_stock_item": 1},
			{"item_code": "PDT-0002", "item_name": "OTHER", "image": None, "stock_uom": "Unit", "is_stock_item": 1},
		]
		first, second = scan.search_items("part")
		self.assertEqual((first["on_hand_total"], first["on_job_sites"]), (7.0, 9.0))
		self.assertEqual((second["on_hand_total"], second["on_job_sites"]), (0.0, 0.0))

	def test_the_search_at_a_site_still_says_what_is_there(self):
		STATE["search_rows"] = [
			{"item_code": "PDT-0001", "item_name": "PART", "image": None, "stock_uom": "Unit", "is_stock_item": 1}
		]
		(row,) = scan.search_items("part", warehouse=self.site)
		self.assertEqual(row["on_hand_here"], 9.0)

	def test_before_the_field_exists_the_total_is_everything_as_it_was(self):
		STATE["columns"] = set()
		STATE["search_rows"] = [
			{"item_code": "PDT-0001", "item_name": "PART", "image": None, "stock_uom": "Unit", "is_stock_item": 1}
		]
		(row,) = scan.search_items("part")
		self.assertEqual((row["on_hand_total"], row["on_job_sites"]), (16.0, 0.0))
		self.assertFalse(any(p["job_site"] for p in scan._elsewhere("PDT-0001", COMPANY)))

	def test_a_site_lists_what_is_there_now_and_nothing_that_was(self):
		"""Nothing is assigned to a site or taken off one, so the bin list's "nobody has decided
		yet" clause would keep every item ever sent to the job on its list for good."""
		rows = [
			{"name": "PDT-0001", "item_name": "PART", "stock_uom": "Unit", "on_hand": 9, "on_order": 0, "assigned": 0, "is_default": 0, "undecided": 1},
			{"name": "PDT-0002", "item_name": "GONE", "stock_uom": "Unit", "on_hand": 0, "on_order": 0, "assigned": 0, "is_default": 0, "undecided": 1},
			{"name": "PDT-0003", "item_name": "COMING", "stock_uom": "Unit", "on_hand": 0, "on_order": 2, "assigned": 0, "is_default": 0, "undecided": 1},
		]
		STATE["items_at"] = rows
		at_site, _ = scan._items_at(scan._warehouse(self.site))
		self.assertEqual([r["item_code"] for r in at_site], ["PDT-0001", "PDT-0003"])
		at_bin, _ = scan._items_at(scan._warehouse(BIN_A))
		self.assertEqual(sorted(r["item_code"] for r in at_bin), ["PDT-0001", "PDT-0002", "PDT-0003"])

	def test_the_location_and_the_item_say_whose_site_it_is(self):
		STATE["items_at"] = []
		location = scan._location_payload(scan._warehouse(self.site))
		self.assertEqual(location["job_site"], {"project": BUILD, "project_name": "Water Wall"})
		self.assertIsNone(scan._location_payload(scan._warehouse(BIN_A))["job_site"])

	def test_the_reorder_board_reads_sites_out(self):
		"""On the source: the board's own suite runs on another stub. The arithmetic is
		``TestRules``'; this pins that the board calls it, on every warehouse that is a site."""
		tree = ast.parse(REORDER.read_text(encoding="utf-8"))
		build = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "build_board")
		source = ast.unparse(build)
		self.assertIn("sites = job_sites.site_warehouses()", source)
		self.assertIn("projected_map = site_rules.without_sites(projected_map, sites, ancestors)", source)
		self.assertIn("read_tracking(rule_rows, ancestors, today, sites)", source)
		tracking = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "read_tracking")
		self.assertRegex(ast.unparse(tracking), r"if warehouse in sites:\s+return False")


# --------------------------------------------------------------------------- the patch


class TestThePatch(_Base):
	def setUp(self):
		_reset(group=False)
		del base._table("Warehouse")[GROUP]
		STATE["columns"] = set()
		STATE["singles"][("Inventory Scanner Settings", "default_warehouse")] = "All Warehouses - SF"

	def test_it_makes_the_field_the_group_and_the_setting(self):
		patch.execute()
		((fields, update),) = STATE["custom_fields"]
		(field,) = fields["Warehouse"]
		self.assertTrue(update)
		self.assertEqual((field["fieldname"], field["fieldtype"], field["options"]), (FIELD, "Link", "Project"))
		self.assertEqual(field["ignore_user_permissions"], 1)
		group = base._table("Warehouse")[GROUP]
		self.assertEqual(
			(group["warehouse_name"], group["is_group"], group["parent_warehouse"], group["company"]),
			("Job Sites", 1, "All Warehouses - SF", COMPANY),
		)
		self.assertEqual(STATE["singles"][("Inventory Scanner Settings", "job_sites_warehouse")], GROUP)
		self.assertEqual(STATE["errors"], [], "a run that finished every step is not an alert")

	def _report(self):
		"""The one summary row a run with an unfinished step leaves, as its message."""
		rows = [message for title, message in STATE["errors"] if title == patch.REPORT_TITLE]
		self.assertEqual(len(rows), 1, STATE["errors"])
		return rows[0]

	def test_each_step_answers_done_then_already(self):
		self.assertEqual(patch.ensure_field()[0], patch.DONE)
		self.assertEqual(patch.ensure_field()[0], patch.ALREADY)
		status, _detail, group = patch.ensure_group()
		self.assertEqual((status, group), (patch.DONE, GROUP))
		self.assertEqual(patch.ensure_group()[::2], (patch.ALREADY, GROUP))
		self.assertEqual(patch.fill_setting(group)[0], patch.DONE)
		self.assertEqual(patch.fill_setting(group)[0], patch.ALREADY)
		self.assertEqual(patch.adopt_old_warehouse(group)[0], patch.ALREADY)
		self.assertEqual(patch.repair_tree()[0], patch.ALREADY)

	def test_the_report_names_every_step_and_what_to_do_for_the_unfinished(self):
		text = patch.report(
			{
				"field": (patch.DONE, "Warehouse.custom_job_site_project"),
				"tree": (patch.ALREADY, "no two warehouses share a position"),
				"group": (patch.SKIPPED, "there is no group warehouse Inventory - SF"),
				"setting": (patch.FAILED, "RuntimeError: boom"),
			}
		)
		lines = text.splitlines()
		self.assertEqual(lines[0], "1. The Warehouse field: done. Warehouse.custom_job_site_project")
		self.assertIn("3. The Job Sites group: skipped. there is no group warehouse Inventory - SF", lines)
		self.assertIn("4. The setting: failed. RuntimeError: boom", lines)
		self.assertIn('5. The old "Job Site" warehouse: skipped. not reached', lines)
		self.assertEqual(len([line for line in lines if line.strip().startswith("By hand:")]), 3)
		self.assertIn("Inventory Scanner Settings", text)
		self.assertIn('create a Warehouse named "Job Sites"', text)

	def test_the_group_sits_beside_the_shops_stock_not_inside_it(self):
		patch.execute()
		self.assertNotEqual(base._table("Warehouse")[GROUP]["parent_warehouse"], patch.SHOP_GROUP)
		self.assertEqual(
			base._table("Warehouse")[GROUP]["parent_warehouse"],
			base._table("Warehouse")[patch.SHOP_GROUP]["parent_warehouse"],
		)

	def test_safe_twice(self):
		patch.execute()
		writes = len(STATE["writes"])
		patch.execute()
		self.assertEqual(len(STATE["writes"]), writes)
		self.assertEqual(STATE["errors"], [], "five times already is not an alert either")
		self.assertEqual(len([w for w in base._table("Warehouse").values() if w["warehouse_name"] == "Job Sites"]), 1)

	def test_a_stored_setting_is_never_written_over_an_emptied_one_included(self):
		for stored in ("Somewhere Else - SF", ""):
			with self.subTest(stored=stored):
				self.setUp()
				STATE["singles"][("Inventory Scanner Settings", "job_sites_warehouse")] = stored
				patch.execute()
				self.assertEqual(STATE["singles"][("Inventory Scanner Settings", "job_sites_warehouse")], stored)

	def test_a_never_saved_single_is_left_alone(self):
		STATE["singles"].clear()
		patch.execute()
		self.assertNotIn(("Inventory Scanner Settings", "job_sites_warehouse"), STATE["singles"])
		self.assertIn(GROUP, base._table("Warehouse"))
		report = self._report()
		self.assertIn("4. The setting: skipped.", report)
		self.assertIn("has never been saved", report)
		self.assertIn("Open Inventory Scanner Settings, choose that group in Job Sites Warehouse and save", report)

	def test_without_the_shop_group_nothing_is_made(self):
		del base._table("Warehouse")[patch.SHOP_GROUP]
		patch.execute()
		self.assertNotIn(GROUP, base._table("Warehouse"))
		self.assertNotIn(("Inventory Scanner Settings", "job_sites_warehouse"), STATE["singles"])
		# Recorded as run, with three steps not done: one row says so, and how to finish each.
		self.assertEqual([title for title, _ in STATE["errors"]], [patch.REPORT_TITLE])
		report = self._report()
		self.assertIn("1. The Warehouse field: done.", report)
		self.assertIn("3. The Job Sites group: skipped. there is no group warehouse Inventory - SF", report)
		self.assertIn("4. The setting: skipped.", report)
		self.assertIn('5. The old "Job Site" warehouse: skipped.', report)
		self.assertIn('create a Warehouse named "Job Sites"', report)
		self.assertIn(patch.RERUN, report)

	def test_a_leaf_already_called_job_sites_is_not_taken_over(self):
		base._warehouse(GROUP, "Job Sites", is_group=0)
		patch.execute()
		self.assertEqual(base._table("Warehouse")[GROUP]["is_group"], 0)
		self.assertNotIn(("Inventory Scanner Settings", "job_sites_warehouse"), STATE["singles"])
		self.assertIn("exists and is not a group, so it was left alone", self._report())

	def test_the_tree_is_renumbered_only_when_two_warehouses_share_a_number(self):
		STATE["tree_counts"] = (307, 307)
		patch.execute()
		self.assertNotIn("rebuilt", STATE)
		self.setUp()
		STATE["tree_counts"] = (307, 306)
		patch.execute()
		self.assertEqual(STATE["rebuilt"], ["Warehouse"])

	def test_the_old_hand_made_warehouse_comes_into_the_tree_and_is_otherwise_untouched(self):
		base._warehouse("Job Site - SF", "Job Site", parent_warehouse=None)
		base._warehouse("Job Site - OC", "Job Site", parent_warehouse=None, company="Other Co")
		patch.execute()
		old = base._table("Warehouse")["Job Site - SF"]
		self.assertEqual((old["parent_warehouse"], old["disabled"], old["is_group"]), (GROUP, 0, 0))
		self.assertFalse(old.get(FIELD), "it is no one job's site")
		self.assertIsNone(base._table("Warehouse")["Job Site - OC"]["parent_warehouse"])

	def test_a_job_site_warehouse_that_has_a_parent_is_somebodys_and_stays(self):
		base._warehouse("Job Site - SF", "Job Site", parent_warehouse=STORES)
		patch.execute()
		self.assertEqual(base._table("Warehouse")["Job Site - SF"]["parent_warehouse"], STORES)

	def test_a_step_that_fails_is_logged_and_the_rest_still_run(self):
		STATE["field_fails"] = True
		patch.execute()  # must not raise: a patch that raises aborts the deploy
		self.assertEqual(
			[title for title, _ in STATE["errors"]],
			["Job sites patch: creating the Warehouse field failed", patch.REPORT_TITLE],
		)
		self.assertIn(GROUP, base._table("Warehouse"))
		self.assertEqual(STATE["singles"][("Inventory Scanner Settings", "job_sites_warehouse")], GROUP)
		report = self._report()
		self.assertIn("1. The Warehouse field: failed. RuntimeError: the column could not be added", report)
		self.assertIn("3. The Job Sites group: done.", report)
		self.assertIn("4. The setting: done.", report)

	def test_the_report_itself_cannot_stop_the_deploy(self):
		STATE["field_fails"] = True
		original = FRAPPE.log_error

		def boom(*args, **kwargs):
			raise RuntimeError("the Error Log is not writable")

		FRAPPE.log_error = boom
		try:
			patch.execute()
		finally:
			FRAPPE.log_error = original
		self.assertIn(GROUP, base._table("Warehouse"))

	def test_a_fresh_install_gets_the_field_and_cannot_fail(self):
		patch.install_field()
		self.assertIn(("Warehouse", FIELD), STATE["columns"])
		STATE["field_fails"] = True
		patch.install_field()

	def test_it_is_registered_for_migrate_and_for_install(self):
		lines = [line.strip() for line in PATCHES_TXT.read_text(encoding="utf-8").splitlines()]
		self.assertEqual(lines.count("erpnext_enhancements.patches.add_job_sites_warehouse"), 1)
		self.assertIn('"erpnext_enhancements.patches.add_job_sites_warehouse.install_field"', HOOKS.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- the settings


class TestTheSettings(unittest.TestCase):
	def test_both_fields_exist_and_declare_no_default(self):
		"""A default on a new field of a Single never reaches the row that already exists. These
		two have none to drift from: the group is written by the patch, blank types mean Build."""
		doc = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
		by_name = {f["fieldname"]: f for f in doc["fields"]}
		for name in ("job_sites_warehouse", "job_site_project_types"):
			with self.subTest(field=name):
				self.assertIn(name, doc["field_order"])
				self.assertNotIn("default", by_name[name])
				self.assertTrue(by_name[name].get("description"))
		self.assertEqual(
			(by_name["job_sites_warehouse"]["fieldtype"], by_name["job_sites_warehouse"]["options"]),
			("Link", "Warehouse"),
		)

	def test_the_reader_knows_both(self):
		tree = ast.parse(SETTINGS_PY.read_text(encoding="utf-8"))
		defaults = next(
			node.value
			for node in tree.body
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "DEFAULTS"
		)
		values = ast.literal_eval(defaults)
		self.assertIn("job_sites_warehouse", values)
		self.assertIn("job_site_project_types", values)
		self.assertIsNone(values["job_sites_warehouse"])
		self.assertIsNone(values["job_site_project_types"])


if __name__ == "__main__":
	unittest.main()
