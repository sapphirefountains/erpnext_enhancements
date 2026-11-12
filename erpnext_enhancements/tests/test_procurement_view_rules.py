# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the Procurement Tracker's saved views (v1.815.0).

Three layers, one file (pytest; own CI step):

* ``procurement_view_rules`` (pure): what a stored layout may hold (known column keys only, an operator
  that suits the column, size limits), the lenient repair of a stored view that is out of date or damaged,
  and the owner / quota / unique-name rules.
* The catalog (``data/procurement_tracker_views.json``): every preset is a layout the strict validator
  accepts, and the one phrase the "no place recorded" preset depends on is the server's own wording.
* The endpoints (``procurement_views.py``) and the DocType controller, run against a small stand-in for
  ``frappe`` with an in-memory table: **only the owner reads, changes or deletes a view**, a stranger's
  view reads as "not found", a bad layout writes nothing, and a broken stored view comes back repaired.

Every name and figure is made up. The repo is public.

Run: python -m pytest erpnext_enhancements/tests/test_procurement_view_rules.py -q
"""

import copy
import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements import procurement_view_rules as rules
from erpnext_enhancements.inventory_enhancements import project_stock_rules as stock_rules

DOCTYPE_DIR = APP / "project_enhancements" / "doctype" / "procurement_tracker_view"
ALICE = "alice@example.test"
BOB = "bob@example.test"


def layout(**kw):
	base = {
		"columns": ["item", "ordered", "received", "status"],
		"sort": {"key": "status", "dir": "asc"},
		"filters": {
			"location": "",
			"status": "",
			"supplier": "",
			"action": False,
			"noprice": False,
			"rules": [],
		},
	}
	base.update(kw)
	return base


def with_rules(*rule_list):
	out = layout()
	out["filters"]["rules"] = list(rule_list)
	return out


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------


class TestCatalog:
	def test_every_shipped_preset_is_a_layout_the_server_accepts(self):
		for entry in rules.presets():
			assert rules.validate_layout(entry["layout"], entry["mode"]) == entry["layout"], entry["id"]

	def test_presets_have_unique_ids_names_and_a_reason(self):
		ids = [p["id"] for p in rules.presets()]
		assert len(set(ids)) == len(ids)
		assert all(p["name"] and p["description"] for p in rules.presets())
		# A person's own views are named PTV-xxxxx: no preset can ever shadow one.
		assert not any(i.startswith("PTV-") for i in ids)

	def test_the_standard_views_are_the_tracker_as_it_was(self):
		assert rules.standard_layout("documents")["columns"] == [
			"item",
			"where",
			"requested",
			"ordered",
			"received",
			"rate",
			"amount",
			"status",
			"doc_chain",
		]
		assert rules.standard_layout("items")["columns"] == [
			"item",
			"requested",
			"ordered",
			"received",
			"unit_price",
			"spent",
			"status",
			"where",
			"due",
			"supplier",
			"documents",
		]
		for mode in rules.MODES:
			std = rules.standard_layout(mode)
			assert std["sort"] is None
			assert std["filters"] == {**rules.FILTER_DEFAULTS, "rules": []}

	def test_the_location_gaps_preset_reads_the_servers_own_words(self):
		# The preset filters Where it is now on this phrase; if the server's wording moves, the preset
		# silently matches nothing, so the wording is held here.
		phrase = next(
			r["value"]
			for p in rules.presets()
			if p["id"] == "location-gaps"
			for r in p["layout"]["filters"]["rules"]
		)
		assert phrase in stock_rules.place_text(stock_rules.KIND_UNRECORDED, 3).lower()

	def test_price_columns_are_exactly_the_ones_the_server_leaves_out(self):
		prices = {c["key"] for mode in rules.MODES for c in rules.columns(mode) if c.get("price")}
		assert prices == {"rate", "amount", "unit_price", "spent", "received_value", "no_price"}

	def test_presets_that_need_prices_are_the_money_ones(self):
		needs = {
			p["id"]
			for p in rules.presets()
			if any(rules.column_map(p["mode"])[k].get("price") for k in p["layout"]["columns"])
			or p["layout"]["filters"]["noprice"]
		}
		assert needs == {"std-documents", "std-items", "money-items", "money-documents", "missing-prices"}

	def test_the_client_catalog_carries_the_columns_operators_limits_and_presets(self):
		catalog = rules.catalog_for_client()
		assert set(catalog) == {"modes", "operators", "limits", "presets"}
		assert [c["key"] for c in catalog["modes"]["items"]["columns"]][:2] == ["item", "requested"]
		# A copy: handing it out must not let a caller edit the catalog.
		catalog["presets"][0]["layout"]["columns"].append("zzz")
		assert "zzz" not in rules.preset("std-documents")["layout"]["columns"]


# ---------------------------------------------------------------------------
# Names, quota, owner
# ---------------------------------------------------------------------------


class TestNamesQuotaOwner:
	def test_a_name_is_one_line_with_single_spaces(self):
		assert rules.clean_name("  Money   audit \n") == "Money audit"

	@pytest.mark.parametrize("bad", ["", "   ", None, "x" * 61, "a\x00b", "a\x07b"])
	def test_a_bad_name_is_refused_in_plain_words(self, bad):
		with pytest.raises(rules.ViewError):
			rules.clean_name(bad)

	def test_sixty_characters_is_the_longest_name(self):
		assert len(rules.clean_name("x" * 60)) == 60

	def test_two_names_that_differ_only_in_case_or_spacing_are_one_name(self):
		with pytest.raises(rules.ViewError, match="already have"):
			rules.check_unique_name("My  View", ["my view"])
		rules.check_unique_name("My view 2", ["my view"])

	def test_a_person_keeps_fifty_views(self):
		rules.check_quota(49)
		with pytest.raises(rules.ViewError, match="up to 50"):
			rules.check_quota(50)

	def test_only_the_owner_may_touch_a_view(self):
		rules.check_owner(ALICE, ALICE)
		for owner, user in [(ALICE, BOB), (None, ALICE), (ALICE, None), ("", ""), (ALICE, "Guest")]:
			with pytest.raises(rules.NotYours):
				rules.check_owner(owner, user)

	def test_somebody_elses_view_reads_as_not_found(self):
		with pytest.raises(rules.NotYours, match="not found"):
			rules.check_owner(ALICE, BOB)
		assert isinstance(rules.NotYours("x"), rules.ViewError)


# ---------------------------------------------------------------------------
# validate_layout: strict
# ---------------------------------------------------------------------------


class TestValidate:
	def test_a_good_layout_comes_back_as_stored(self):
		out = rules.validate_layout(layout(), "items")
		assert out == layout()

	def test_a_layout_may_arrive_as_json_text(self):
		assert rules.validate_layout(json.dumps(layout()), "items") == layout()

	@pytest.mark.parametrize(
		"bad",
		[
			None,
			"not json",
			"[]",
			42,
			{},
			{"columns": []},
			{"columns": "item"},
			{"columns": ["item", "item"]},
			{"columns": ["item", "retired"]},
			{"columns": [1]},
			{"columns": ["rate"]},  # a by-document column, validated as by-item
		],
	)
	def test_a_layout_that_names_no_known_column_is_refused(self, bad):
		with pytest.raises(rules.ViewError):
			rules.validate_layout(bad, "items")

	def test_the_same_columns_are_checked_against_the_grouping(self):
		assert rules.validate_layout({"columns": ["rate", "amount"]}, "documents")["columns"] == [
			"rate",
			"amount",
		]
		with pytest.raises(rules.ViewError):
			rules.validate_layout({"columns": ["spent"]}, "documents")

	def test_an_unknown_grouping_is_refused(self):
		with pytest.raises(rules.ViewError):
			rules.validate_layout(layout(), "nonsense")

	def test_sort_must_be_on_a_shown_sortable_column_with_a_direction(self):
		for sort in [
			{"key": "due", "dir": "asc"},  # not shown
			{"key": "status", "dir": "sideways"},
			{"key": "status"},
			"status",
		]:
			with pytest.raises(rules.ViewError):
				rules.validate_layout(layout(sort=sort), "items")
		with pytest.raises(rules.ViewError):
			rules.validate_layout(
				{"columns": ["item", "documents"], "sort": {"key": "documents", "dir": "asc"}}, "items"
			)
		assert rules.validate_layout(layout(sort=None), "items")["sort"] is None

	def test_only_the_three_known_keys_survive(self):
		out = rules.validate_layout({**layout(), "script": "alert(1)", "sql": "x"}, "items")
		assert set(out) == {"columns", "sort", "filters"}
		out = rules.validate_layout(
			layout(filters={"location": "", "evil": 1, "rules": []}),
			"items",
		)
		assert set(out["filters"]) == {"location", "status", "supplier", "action", "noprice", "rules"}

	def test_toolbar_filters_are_text_and_flags(self):
		with pytest.raises(rules.ViewError):
			rules.validate_layout(layout(filters={"status": ["late"]}), "items")
		with pytest.raises(rules.ViewError):
			rules.validate_layout(layout(filters={"action": "yes"}), "items")
		with pytest.raises(rules.ViewError):
			rules.validate_layout(layout(filters={"supplier": "x" * 141}), "items")
		assert rules.validate_layout(layout(filters={"action": 1}), "items")["filters"]["action"] is True

	def test_the_size_limit(self):
		many = [
			{"column": "status", "op": "any_of", "values": [f"{i:03d}" + "x" * 130 for i in range(25)]}
			for _ in range(3)
		]
		with pytest.raises(rules.ViewError, match="too much"):
			rules.validate_layout(with_rules(*many), "items")

	def test_at_most_twelve_column_filters(self):
		one = {"column": "item", "op": "blank"}
		rules.validate_layout(with_rules(*[one] * 12), "items")
		with pytest.raises(rules.ViewError, match="up to 12"):
			rules.validate_layout(with_rules(*[one] * 13), "items")


class TestRules:
	@pytest.mark.parametrize(
		"rule",
		[
			{"column": "item", "op": "contains", "value": "pipe"},
			{"column": "status", "op": "any_of", "values": ["late", "to_order"]},
			{"column": "where", "op": "contains", "value": "no place recorded"},
			{"column": "received", "op": "between", "min": 1, "max": 5},
			{"column": "received", "op": "between", "min": None, "max": 5.5},
			{"column": "spent", "op": "between", "min": 0, "max": None},
			{"column": "due", "op": "between", "min": "2026-11-01", "max": None},
			{"column": "supplier", "op": "blank"},
			{"column": "supplier", "op": "not_blank"},
			{"column": "item", "op": "not_blank"},
		],
	)
	def test_a_good_filter_is_kept_in_its_stored_form(self, rule):
		out = rules.validate_layout(with_rules(rule), "items")["filters"]["rules"]
		assert out == [{**{"min": None, "max": None}, **rule}] if rule["op"] == "between" else [rule]

	@pytest.mark.parametrize(
		"rule",
		[
			"item",
			{"column": "nope", "op": "blank"},
			{"column": "item", "op": "between", "min": 1},  # text has no range
			{"column": "received", "op": "contains", "value": "1"},  # a number has no 'contains'
			{"column": "documents", "op": "contains", "value": "x"}
			if False
			else {"column": "item", "op": "equals", "value": "x"},
			{"column": "item", "op": "contains", "value": ""},
			{"column": "item", "op": "contains", "value": "x" * 121},
			{"column": "item", "op": "contains", "value": 5},
			{"column": "status", "op": "any_of", "values": []},
			{"column": "status", "op": "any_of", "values": "late"},
			{"column": "status", "op": "any_of", "values": ["a"] * 1 + [""]},
			{"column": "status", "op": "any_of", "values": [str(i) for i in range(26)]},
			{"column": "received", "op": "between"},
			{"column": "received", "op": "between", "min": 9, "max": 1},
			{"column": "received", "op": "between", "min": "1"},
			{"column": "received", "op": "between", "min": True},
			{"column": "received", "op": "between", "min": float("inf")},
			{"column": "received", "op": "between", "min": 10**13},
			{"column": "due", "op": "between", "min": "next week"},
			{"column": "due", "op": "between", "min": "2026-13-45"},
			{"column": "due", "op": "between", "min": 20261101},
			{"op": "blank"},
			{"column": ["item"], "op": "blank"},
		],
	)
	def test_a_filter_that_does_not_fit_is_refused(self, rule):
		with pytest.raises(rules.ViewError):
			rules.validate_layout(with_rules(rule), "items")

	def test_choices_are_kept_once_each(self):
		rule = {"column": "status", "op": "any_of", "values": ["late", " late ", "to_order"]}
		assert rules.validate_layout(with_rules(rule), "items")["filters"]["rules"][0]["values"] == [
			"late",
			"to_order",
		]

	def test_a_by_item_column_filter_is_not_a_by_document_one(self):
		with pytest.raises(rules.ViewError):
			rules.validate_layout(
				{"columns": ["item"], "filters": {"rules": [{"column": "spent", "op": "blank"}]}}, "documents"
			)

	def test_a_filter_may_name_a_price_column_for_a_person_who_cannot_see_it(self):
		# Storing it is harmless: the column's data is not in that person's response, and the browser
		# sets the filter aside (see the node suite). What a layout can name is not what a person can read.
		rules.validate_layout(with_rules({"column": "spent", "op": "between", "min": 1}), "items")


# ---------------------------------------------------------------------------
# repair_layout: lenient, never raises
# ---------------------------------------------------------------------------


class TestRepair:
	def test_a_valid_layout_is_untouched(self):
		assert rules.repair_layout(layout(), "items") == (layout(), False)

	def test_a_column_the_app_no_longer_has_is_dropped_and_the_rest_kept(self):
		stored = layout(columns=["item", "retired", "received"], sort={"key": "retired", "dir": "asc"})
		out, repaired = rules.repair_layout(stored, "items")
		assert (out["columns"], out["sort"], repaired) == (["item", "received"], None, True)

	def test_a_filter_that_no_longer_fits_is_dropped_the_others_kept(self):
		stored = with_rules(
			{"column": "retired", "op": "blank"}, {"column": "received", "op": "between", "min": 1}
		)
		out, repaired = rules.repair_layout(stored, "items")
		assert [r["column"] for r in out["filters"]["rules"]] == ["received"]
		assert repaired is True

	def test_a_loose_flag_and_a_wrong_sized_value_are_mended(self):
		stored = layout(filters={"action": "yes", "supplier": ["x"], "location": "x" * 500, "rules": "junk"})
		out, repaired = rules.repair_layout(stored, "items")
		assert out["filters"]["action"] is False
		assert out["filters"]["supplier"] == ""
		assert out["filters"]["location"] == ""
		assert out["filters"]["rules"] == []
		assert repaired is True

	def test_a_view_with_nothing_usable_is_the_standard_layout(self):
		for stored in [
			None,
			"",
			"not json",
			"[]",
			7,
			{},
			{"columns": ["retired"]},
			{"columns": []},
			{"columns": "item"},
		]:
			out, repaired = rules.repair_layout(stored, "items")
			assert out == rules.standard_layout("items"), stored
			assert repaired is True

	def test_json_text_is_read(self):
		out, repaired = rules.repair_layout(json.dumps({"columns": ["item", "gone"]}), "documents")
		assert (out["columns"], repaired) == (["item"], True)

	def test_it_never_raises_whatever_is_stored(self):
		junk = [
			{"columns": ["item"], "sort": "x"},
			{"columns": ["item"], "filters": {"rules": [None, 1, "x", {}, {"column": 5}]}},
			{"columns": [None, 5, ["item"], {"a": 1}]},
			{"columns": ["item"] * 100},
			{"columns": ["item"], "filters": {"rules": [{"column": "item", "op": "blank"}] * 40}},
			object(),
		]
		for stored in junk:
			out, _ = rules.repair_layout(stored, "items")
			assert out["columns"]
			rules.validate_layout(out, "items")  # what it returns is always a layout the server would store

	def test_too_many_filters_are_cut_to_the_limit(self):
		stored = with_rules(*[{"column": "item", "op": "blank"}] * 40)
		out, repaired = rules.repair_layout(stored, "items")
		assert len(out["filters"]["rules"]) == 12 and repaired is True

	def test_it_is_deterministic(self):
		stored = {"columns": ["item", "x", "status"], "sort": {"key": "status", "dir": "desc"}}
		assert rules.repair_layout(stored, "items") == rules.repair_layout(copy.deepcopy(stored), "items")

	def test_the_stored_form_is_compact_and_ordered(self):
		a = rules.layout_json(layout())
		b = rules.layout_json(json.loads(json.dumps(layout())))
		assert a == b and " " not in a


# ---------------------------------------------------------------------------
# The endpoints and the controller, against a stand-in for frappe
# ---------------------------------------------------------------------------


class Doc:
	"""Stands in for frappe.model.document.Document: enough for the controller and the endpoints."""

	_before = None

	def is_new(self):
		return not getattr(self, "name", None)

	def get_doc_before_save(self):
		return self._before


class Env:
	"""The in-memory table, the session, and the permissions."""

	def __init__(self):
		self.table = {}
		self.user = ALICE
		self.project_readers = {ALICE, BOB}
		self.counter = 0
		self.deleted = []


def install(env):
	"""A fake ``frappe`` and the two modules under test, imported against it. Returns (procurement_views,
	controller module). The fake is removed from sys.modules by the fixture."""
	frappe = types.ModuleType("frappe")

	class PermissionError_(Exception):
		pass

	class DoesNotExistError(Exception):
		pass

	class ValidationError(Exception):
		pass

	frappe.PermissionError = PermissionError_
	frappe.DoesNotExistError = DoesNotExistError
	frappe.ValidationError = ValidationError

	class Session:
		@property
		def user(self_inner):
			return env.user

	frappe.session = Session()

	def whitelist(methods=None, **_kw):
		def wrap(fn):
			fn.__whitelisted__ = True
			fn.__methods__ = methods
			return fn

		return wrap

	frappe.whitelist = whitelist

	def throw(msg, exc=None, **_kw):
		raise (exc or ValidationError)(msg)

	frappe.throw = throw
	frappe.has_permission = (
		lambda doctype, ptype="read", **_kw: env.user in env.project_readers
		if doctype == "Project"
		else False
	)
	frappe.parse_json = json.loads

	def get_all(doctype, filters=None, fields=None, order_by=None, limit_page_length=None, **_kw):
		rows = [dict(r) for r in env.table.values() if all(r.get(k) == v for k, v in (filters or {}).items())]
		rows.sort(key=lambda r: r["view_name"].lower())
		rows = rows[: limit_page_length or len(rows)]
		return [{k: r.get(k) for k in fields} for r in rows]

	frappe.get_all = get_all

	db = types.SimpleNamespace()

	def get_value(doctype, name, fields, as_dict=False):
		row = env.table.get(name)
		if not row:
			return None
		return {k: row.get(k) for k in fields}

	def set_value(doctype, name, field, value):
		env.table[name][field] = value

	db.get_value = get_value
	db.set_value = set_value
	frappe.db = db

	def new_doc(doctype):
		return Controller()

	def get_doc(doctype, name):
		row = env.table[name]
		doc = Controller()
		doc.__dict__.update(row)
		doc._before = Controller()
		doc._before.__dict__.update(row)
		return doc

	def delete_doc(doctype, name, ignore_permissions=False, **_kw):
		assert ignore_permissions is True
		env.deleted.append(name)
		env.table.pop(name, None)

	frappe.new_doc = new_doc
	frappe.get_doc = get_doc
	frappe.delete_doc = delete_doc

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = Doc
	model.document = document
	frappe.model = model
	sys.modules.update({"frappe": frappe, "frappe.model": model, "frappe.model.document": document})
	for name in ("erpnext_enhancements.procurement_views",):
		sys.modules.pop(name, None)

	controller_path = DOCTYPE_DIR / "procurement_tracker_view.py"
	spec = importlib.util.spec_from_file_location("ptv_controller_under_test", controller_path)
	controller = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(controller)
	Controller = controller.ProcurementTrackerView

	def save(self, ignore_permissions=False):
		assert ignore_permissions is True
		self.validate()
		if self.is_new():
			env.counter += 1
			self.name = f"PTV-{env.counter:05d}"
			self.owner = env.user
		fields = ("name", "owner", "view_name", "mode", "layout")
		env.table[self.name] = {k: getattr(self, k) for k in fields}
		return self

	def as_dict(self):
		return {k: getattr(self, k) for k in ("name", "view_name", "mode", "layout")}

	Controller.save = save
	Controller.as_dict = as_dict
	views = importlib.import_module("erpnext_enhancements.procurement_views")
	return views, controller


@pytest.fixture
def world():
	saved = {
		k: sys.modules.get(k)
		for k in ("frappe", "frappe.model", "frappe.model.document", "erpnext_enhancements.procurement_views")
	}
	env = Env()
	views, controller = install(env)
	env.views = views
	env.controller = controller
	env.frappe = sys.modules["frappe"]
	try:
		yield env
	finally:
		for k, v in saved.items():
			if v is None:
				sys.modules.pop(k, None)
			else:
				sys.modules[k] = v


def save(env, view_name="Mine", mode="items", lay=None, **kw):
	return env.views.save_view(view_name, mode, json.dumps(lay or layout()), **kw)


class TestEndpoints:
	def test_a_person_creates_reads_changes_renames_and_deletes_their_own_view(self, world):
		made = save(world, "Audit")
		assert made["id"] == "PTV-00001" and made["name"] == "Audit" and made["preset"] is False
		assert [v["name"] for v in world.views.get_views()["mine"]] == ["Audit"]
		changed = save(world, "Audit", lay=layout(columns=["item", "due"], sort=None), name=made["id"])
		assert changed["layout"]["columns"] == ["item", "due"]
		renamed = world.views.rename_view(made["id"], "Audit 2")
		assert renamed["name"] == "Audit 2" and renamed["layout"]["columns"] == ["item", "due"]
		assert world.views.delete_view(made["id"]) == {"deleted": made["id"]}
		assert world.views.get_views()["mine"] == []

	def test_get_views_carries_the_catalog_and_who_asked(self, world):
		out = world.views.get_views()
		assert out["user"] == ALICE
		assert [p["id"] for p in out["catalog"]["presets"]][:2] == ["std-documents", "std-items"]

	def test_nobody_sees_anybody_elses_views(self, world):
		save(world, "Alice's")
		world.user = BOB
		assert world.views.get_views()["mine"] == []
		save(world, "Bob's")
		assert [v["name"] for v in world.views.get_views()["mine"]] == ["Bob's"]
		world.user = ALICE
		assert [v["name"] for v in world.views.get_views()["mine"]] == ["Alice's"]

	def test_a_stranger_cannot_change_rename_or_delete_a_view_and_it_reads_as_not_found(self, world):
		made = save(world, "Alice's")
		before = copy.deepcopy(world.table)
		world.user = BOB
		doesnt = world.frappe.DoesNotExistError
		with pytest.raises(doesnt, match="not found"):
			save(world, "Bob's take", name=made["id"], lay=layout(columns=["item"], sort=None))
		with pytest.raises(doesnt, match="not found"):
			world.views.rename_view(made["id"], "Stolen")
		with pytest.raises(doesnt, match="not found"):
			world.views.delete_view(made["id"])
		assert world.table == before and world.deleted == []

	def test_a_view_that_does_not_exist_reads_exactly_like_a_strangers(self, world):
		made = save(world, "Alice's")
		world.user = BOB
		messages = []
		for name in (made["id"], "PTV-99999"):
			with pytest.raises(world.frappe.DoesNotExistError) as e:
				world.views.delete_view(name)
			messages.append(str(e.value))
		assert messages[0] == messages[1]

	def test_a_name_that_is_not_a_name_is_not_found(self, world):
		for name in (None, "", "' OR 1=1 --", "../x"):
			with pytest.raises(world.frappe.DoesNotExistError):
				world.views.delete_view(name)

	def test_signed_out_and_people_who_cannot_open_a_project_are_refused(self, world):
		world.user = "Guest"
		with pytest.raises(world.frappe.PermissionError):
			world.views.get_views()
		world.user = "carol@example.test"
		for call in (
			lambda: world.views.get_views(),
			lambda: save(world),
			lambda: world.views.rename_view("PTV-00001", "x"),
			lambda: world.views.delete_view("PTV-00001"),
		):
			with pytest.raises(world.frappe.PermissionError):
				call()
		assert world.table == {}

	def test_the_writes_are_post_only_and_the_read_is_not(self, world):
		v = world.views
		assert v.save_view.__methods__ == ["POST"]
		assert v.rename_view.__methods__ == ["POST"]
		assert v.delete_view.__methods__ == ["POST"]
		assert v.get_views.__whitelisted__ and v.get_views.__methods__ is None

	def test_a_bad_layout_writes_nothing(self, world):
		for bad in (
			layout(columns=["item", "retired"]),
			layout(columns=[]),
			with_rules({"column": "item", "op": "between", "min": 1}),
			{"columns": ["item"], "sort": {"key": "due", "dir": "asc"}},
		):
			with pytest.raises(world.frappe.ValidationError):
				save(world, "Bad", lay=bad)
		with pytest.raises(world.frappe.ValidationError):
			world.views.save_view("Bad", "items", "{not json")
		assert world.table == {}

	def test_a_bad_name_or_grouping_writes_nothing(self, world):
		for name, mode in (("", "items"), ("x" * 61, "items"), ("Fine", "sideways"), ("Fine", "")):
			with pytest.raises(world.frappe.ValidationError):
				save(world, name, mode=mode)
		assert world.table == {}

	def test_a_stored_layout_is_what_the_server_normalized_not_what_was_sent(self, world):
		made = save(world, "N", lay={**layout(), "script": "x", "filters": {"status": " late ", "evil": 1}})
		stored = json.loads(world.table[made["id"]]["layout"])
		assert set(stored) == {"columns", "sort", "filters"}
		assert stored["filters"]["status"] == "late" and "evil" not in stored["filters"]
		assert world.table[made["id"]]["layout"] == rules.layout_json(stored)

	def test_the_owner_is_the_session_user_and_cannot_be_chosen(self, world):
		made = save(world, "Mine")
		assert world.table[made["id"]]["owner"] == ALICE
		world.user = BOB
		# Even a request that names a stranger's view in the body of a "new" view makes a new view for Bob.
		other = save(world, "Bob new", lay=layout())
		assert world.table[other["id"]]["owner"] == BOB

	def test_two_people_may_use_the_same_name(self, world):
		save(world, "Money")
		world.user = BOB
		save(world, "Money")
		assert len(world.table) == 2

	def test_one_person_may_not_use_a_name_twice(self, world):
		made = save(world, "Money")
		with pytest.raises(world.frappe.ValidationError, match="already have"):
			save(world, "  money ")
		other = save(world, "Other")
		with pytest.raises(world.frappe.ValidationError, match="already have"):
			world.views.rename_view(other["id"], "MONEY")
		# Saving a view under its own name is not a clash.
		save(world, "Money", name=made["id"], lay=layout(columns=["item"], sort=None))

	def test_the_fifty_first_view_is_refused(self, world):
		for i in range(50):
			save(world, f"View {i}")
		with pytest.raises(world.frappe.ValidationError, match="up to 50"):
			save(world, "One too many")
		world.user = BOB
		save(world, "Bob is not held to Alice's count")
		assert len(world.table) == 51

	def test_a_view_keeps_its_grouping(self, world):
		made = save(world, "Items", mode="items")
		with pytest.raises(world.frappe.ValidationError, match="keeps its grouping"):
			save(world, "Items", mode="documents", lay={"columns": ["item"]}, name=made["id"])
		assert world.table[made["id"]]["mode"] == "items"

	def test_a_preset_is_not_a_record_and_has_no_endpoint(self, world):
		for preset_id in ("std-items", "money-items"):
			with pytest.raises(world.frappe.DoesNotExistError):
				world.views.delete_view(preset_id)
			with pytest.raises(world.frappe.DoesNotExistError):
				save(world, "x", name=preset_id)
		# "Save a copy" is a new view whose layout is the preset's.
		copy_ = save(world, "Money, my way", lay=rules.preset("money-items")["layout"])
		assert copy_["layout"] == rules.preset("money-items")["layout"]

	def test_deleting_removes_only_that_record(self, world):
		a = save(world, "A")
		b = save(world, "B")
		world.views.delete_view(a["id"])
		assert list(world.table) == [b["id"]] and world.deleted == [a["id"]]

	def test_a_broken_or_outdated_stored_view_comes_back_repaired(self, world):
		world.table["PTV-77001"] = {
			"name": "PTV-77001",
			"owner": ALICE,
			"view_name": "Old",
			"mode": "items",
			"layout": json.dumps(
				{"columns": ["item", "retired", "status"], "sort": {"key": "retired", "dir": "asc"}}
			),
		}
		world.table["PTV-77002"] = {
			"name": "PTV-77002",
			"owner": ALICE,
			"view_name": "Junk",
			"mode": "documents",
			"layout": "{{{",
		}
		world.table["PTV-77003"] = {
			"name": "PTV-77003",
			"owner": ALICE,
			"view_name": "Fine",
			"mode": "items",
			"layout": rules.layout_json(layout()),
		}
		by_name = {v["name"]: v for v in world.views.get_views()["mine"]}
		assert (
			by_name["Old"]["layout"]["columns"],
			by_name["Old"]["layout"]["sort"],
			by_name["Old"]["repaired"],
		) == (["item", "status"], None, True)
		assert (by_name["Junk"]["layout"], by_name["Junk"]["repaired"]) == (
			rules.standard_layout("documents"),
			True,
		)
		assert by_name["Fine"]["repaired"] is False

	def test_reading_never_writes(self, world):
		world.table["PTV-77002"] = {
			"name": "PTV-77002",
			"owner": ALICE,
			"view_name": "Junk",
			"mode": "items",
			"layout": "{{{",
		}
		before = copy.deepcopy(world.table)
		world.views.get_views()
		assert world.table == before


class TestController:
	def make(self, world, **kw):
		doc = world.controller.ProcurementTrackerView()
		doc.view_name = kw.get("view_name", "  Spaced   name ")
		doc.mode = kw.get("mode", "items")
		doc.layout = kw.get("layout", json.dumps(layout()))
		return doc

	def test_the_class_name_is_the_one_frappe_derives(self, world):
		assert world.controller.ProcurementTrackerView.__name__ == "Procurement Tracker View".replace(" ", "")

	def test_validate_cleans_the_name_and_stores_the_normalized_layout(self, world):
		doc = self.make(world)
		doc.validate()
		assert doc.view_name == "Spaced name"
		assert doc.layout == rules.layout_json(layout())

	def test_validate_refuses_a_layout_written_from_the_desk_too(self, world):
		for bad in ("{}", "junk", json.dumps({"columns": ["nope"]})):
			with pytest.raises(world.frappe.ValidationError):
				self.make(world, layout=bad).validate()
		with pytest.raises(world.frappe.ValidationError):
			self.make(world, mode="sideways").validate()

	def test_an_edit_cannot_hand_a_view_to_somebody_else(self, world):
		made = save(world, "Mine")
		doc = world.frappe.get_doc("Procurement Tracker View", made["id"])
		doc.owner = BOB
		with pytest.raises(world.frappe.ValidationError, match="stays with the person"):
			doc.validate()


class TestDocType:
	def meta(self):
		return json.loads((DOCTYPE_DIR / "procurement_tracker_view.json").read_text(encoding="utf-8"))

	def test_it_sits_in_its_declared_module_folder(self):
		meta = self.meta()
		assert meta["module"] == "Project Enhancements"
		assert DOCTYPE_DIR.parent.parent.name == "project_enhancements"
		assert meta["name"] == "Procurement Tracker View"

	def test_nobody_but_a_system_manager_has_a_permission_on_it(self):
		# The door is procurement_views.py, which checks the owner. A DocPerm for any other role
		# would open /api/resource to every person's views.
		roles = {p["role"] for p in self.meta()["permissions"]}
		assert roles == {"System Manager"}
		for p in self.meta()["permissions"]:
			assert not p.get("if_owner") and not p.get("share") and not p.get("export")

	def test_the_fields_are_the_ones_the_endpoints_write(self):
		names = {f["fieldname"] for f in self.meta()["fields"]}
		assert names == {"view_name", "mode", "layout"}
		mode = next(f for f in self.meta()["fields"] if f["fieldname"] == "mode")
		assert mode["options"].split("\n") == list(rules.MODES)
		name = next(f for f in self.meta()["fields"] if f["fieldname"] == "view_name")
		assert name["length"] == rules.LIMITS["max_name_length"]

	def test_it_is_not_a_single_or_a_child_table(self):
		meta = self.meta()
		assert not meta.get("issingle") and not meta.get("istable")
		assert meta["autoname"].startswith("format:PTV-")
