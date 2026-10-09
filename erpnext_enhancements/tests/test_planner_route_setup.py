# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Project Planner's route setup (v1.578.0): settings, the drive-time cache, its backfill, the
hooks, and the two read-only AI tools.

What each section pins, and what would fail silently otherwise:

* **The Routes fields are declared with the defaults the routing code assumes.** A default that
  drifts from the JSON leaves a site with one value in the form and another in the engine.
* **Validate never refuses a field that is None or blank.** An existing Single row has no
  ``tabSingles`` row for these, so they read None on every site that predates the release. A
  validate that rejects None makes the settings page unsaveable (the Chat Settings failure in
  CLAUDE.md).
* **The backfill fills only the fields with no row, never over a stored value (a stored 0 is a
  decision), leaves a never-saved Single alone, reads ``tabSingles`` raw, and cannot raise.**
* **Planner Drive Time is a cache of Google answers**: its pair key is unique and read-only, and
  its class name is the one Frappe derives.
* **hooks.py has the daily job and both tools exactly once**; a tool missing from the list is
  never loaded and a duplicate is loaded twice.
* **The two tools are read-only, classified as such in _gate.py, and do what their schemas say.**
  Each calls the planner API lazily, and crew_day_route never returns the browser Maps key.

Bench-free: installs its own ``frappe`` and ``frappe_assistant_core`` stubs in ``setUpModule``,
loads the modules from their files, and restores ``sys.modules`` in ``tearDownModule``.

Run: python -m unittest erpnext_enhancements.tests.test_planner_route_setup -v
"""

import ast
import datetime
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
DOCTYPES = APP / "project_enhancements" / "doctype"
SETTINGS_DIR = DOCTYPES / "project_planner_settings"
SETTINGS_JSON = SETTINGS_DIR / "project_planner_settings.json"
SETTINGS_PY = SETTINGS_DIR / "project_planner_settings.py"
DRIVE_DIR = DOCTYPES / "planner_drive_time"
DRIVE_JSON = DRIVE_DIR / "planner_drive_time.json"
DRIVE_PY = DRIVE_DIR / "planner_drive_time.py"
RESOURCE_JSON = DOCTYPES / "planner_resource" / "planner_resource.json"
PATCH = APP / "patches" / "backfill_project_planner_route_settings.py"
PATCHES_TXT = APP / "patches.txt"
PATCHES_README = APP / "patches" / "README.md"
HOOKS = APP / "hooks.py"

ROUTE_FIELDS = ("pad_drive_time", "use_google_routes", "day_start_time", "long_drive_minutes")
CACHE_FIELDS = ("start_latitude", "start_longitude", "start_geocoded_from")
PATCH_NAME = "erpnext_enhancements.patches.backfill_project_planner_route_settings"
DAILY_JOB = "erpnext_enhancements.project_enhancements.routing.backfill_coordinates"
TOOLS = ("crew_schedule_suggestions", "crew_day_route")

# Every module this suite may replace. Snapshotted in setUpModule, restored in tearDownModule.
_PREFIXES = (
	"frappe",
	"frappe_assistant_core",
	"erpnext_enhancements.assistant_tools",
	"erpnext_enhancements.api.project_planner",
)
_saved_modules = {}

frappe = None
_gate = None
settings_mod = None
patch_mod = None
crew_tools = {}
calls = []
_db_writes = []


class _Throw(Exception):
	pass


class _Document:
	"""Just enough of frappe's Document for the settings controller."""

	def __init__(self, **values):
		self.__dict__.update(values)
		self.doctype = "Project Planner Settings"

	def get(self, key, default=None):
		value = self.__dict__.get(key)
		return default if value is None else value


def _is_ours(name):
	return any(name == prefix or name.startswith(prefix + ".") for prefix in _PREFIXES)


def _load(name, path):
	spec = importlib.util.spec_from_file_location(name, path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def _settings_meta_fields():
	"""The Settings JSON's fields as frappe's meta would expose them: the defaults come from the
	JSON itself, so the backfill is checked against what the doctype declares."""
	data = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
	return {f["fieldname"]: SimpleNamespace(**f) for f in data["fields"] if "fieldname" in f}


def setUpModule():
	global frappe, _gate, settings_mod, patch_mod
	for name in list(sys.modules):
		if _is_ours(name):
			_saved_modules[name] = sys.modules.pop(name)

	frappe = types.ModuleType("frappe")
	frappe._ = lambda text, *a, **k: text
	frappe.throw = lambda message, exc=None, title=None: (_ for _ in ()).throw((exc or _Throw)(message))
	frappe.logged = []
	frappe.log_error = lambda title=None, message=None, **k: frappe.logged.append((title, message))
	frappe.clear_document_cache = lambda *a, **k: None
	frappe.get_meta = lambda doctype: SimpleNamespace(
		get_field=lambda fieldname: _settings_meta_fields().get(fieldname),
		get_label=lambda fieldname: fieldname,
	)
	frappe.db = SimpleNamespace(exists=lambda *a, **k: True, sql=lambda *a, **k: [])
	frappe.get_list = lambda doctype, **kw: []

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Document
	model.document = document

	base_tool = types.ModuleType("frappe_assistant_core.core.base_tool")

	class BaseTool:
		"""The attributes erpnext_enhancements sets on frappe_assistant_core's BaseTool."""

		def __init__(self):
			self.name = ""
			self.description = ""
			self.inputSchema = {}
			self.requires_permission = None
			self.category = "Custom"
			self.source_app = "frappe_assistant_core"

		def execute(self, arguments):
			raise NotImplementedError

	base_tool.BaseTool = BaseTool
	fac = types.ModuleType("frappe_assistant_core")
	fac_core = types.ModuleType("frappe_assistant_core.core")
	fac.core, fac_core.base_tool = fac_core, base_tool

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.model": model,
			"frappe.model.document": document,
			"frappe_assistant_core": fac,
			"frappe_assistant_core.core": fac_core,
			"frappe_assistant_core.core.base_tool": base_tool,
		}
	)

	settings_mod = _load("_plan_route_settings", SETTINGS_PY)
	patch_mod = _load("_plan_route_backfill", PATCH)

	from erpnext_enhancements.assistant_tools import _gate as gate

	_gate = gate
	for name in TOOLS:
		module = __import__(f"erpnext_enhancements.assistant_tools.{name}", fromlist=["x"])
		crew_tools[name] = module

	api = types.ModuleType("erpnext_enhancements.api.project_planner")
	api.suggest_dates = _fake_suggest_dates
	api.get_route = _fake_get_route
	sys.modules["erpnext_enhancements.api.project_planner"] = api


def tearDownModule():
	for name in list(sys.modules):
		if _is_ours(name):
			sys.modules.pop(name)
	sys.modules.update(_saved_modules)
	_saved_modules.clear()


def _fake_suggest_dates(task, start=None, days=10, resource=None):
	calls.append(("suggest_dates", task, start, days))
	return {"task": task, "subject": "Install", "site": None, "hours_needed": 8, "suggestions": [], "note": None}


def _fake_get_route(resource, date):
	calls.append(("get_route", resource, date))
	return {
		"resource": resource,
		"date": date,
		"stops": [],
		"drive_minutes": 0,
		"maps_key": "AIza-browser-key-do-not-leak",
		"map_ids": {"map_id_light": "L", "map_id_dark": "D"},
	}


def _read(path):
	return json.loads(path.read_text(encoding="utf-8"))


def _fields_by_name(doc):
	return {f["fieldname"]: f for f in doc["fields"] if "fieldname" in f}


class TestSettingsRoutesFields(unittest.TestCase):
	def setUp(self):
		self.doc = _read(SETTINGS_JSON)
		self.fields = _fields_by_name(self.doc)

	def test_the_routes_fields_have_the_agreed_types_and_defaults(self):
		expected = {
			"pad_drive_time": ("Check", "1"),
			"use_google_routes": ("Check", "1"),
			"day_start_time": ("Time", "08:00:00"),
			"long_drive_minutes": ("Int", "90"),
		}
		for name, (fieldtype, default) in expected.items():
			with self.subTest(field=name):
				self.assertEqual(self.fields[name]["fieldtype"], fieldtype)
				self.assertEqual(self.fields[name]["default"], default)

	def test_the_routes_fields_carry_the_agreed_labels_and_descriptions(self):
		self.assertEqual(self.fields["pad_drive_time"]["label"], "Count drive time against people's hours")
		self.assertEqual(self.fields["use_google_routes"]["label"], "Drive times from Google Routes")
		self.assertEqual(
			self.fields["use_google_routes"]["description"],
			"Off: a straight-line estimate is used everywhere (no Google calls).",
		)
		self.assertEqual(self.fields["day_start_time"]["label"], "Day starts at")
		self.assertEqual(
			self.fields["day_start_time"]["description"],
			"Leaving the shop; arrival times on a route count from here.",
		)
		self.assertEqual(
			self.fields["long_drive_minutes"]["label"], "Flag a day with more driving than (minutes)"
		)

	def test_the_shop_cache_is_read_only_and_never_typed(self):
		for name in CACHE_FIELDS:
			with self.subTest(field=name):
				self.assertEqual(self.fields[name].get("read_only"), 1)
		self.assertEqual(self.fields["start_latitude"]["fieldtype"], "Float")
		self.assertEqual(self.fields["start_latitude"]["precision"], "6")
		self.assertEqual(self.fields["start_longitude"]["fieldtype"], "Float")
		self.assertEqual(self.fields["start_longitude"]["precision"], "6")
		self.assertEqual(self.fields["start_geocoded_from"]["fieldtype"], "Data")
		self.assertEqual(
			self.fields["start_geocoded_from"]["description"],
			"The shop address these coordinates were found from.",
		)

	def test_the_routes_section_comes_after_the_rental_section_with_a_column_break(self):
		order = self.doc["field_order"]
		rental_end = order.index("rental_cleaning_hours")
		self.assertEqual(order[rental_end + 1], "section_routes")
		self.assertEqual(self.fields["section_routes"]["fieldtype"], "Section Break")
		self.assertEqual(self.fields["section_routes"]["label"], "Routes")
		self.assertEqual(self.fields["column_break_routes"]["fieldtype"], "Column Break")
		brk = order.index("column_break_routes")
		self.assertLess(order.index("long_drive_minutes"), brk)
		self.assertLess(brk, order.index("start_latitude"))

	def test_every_field_in_the_order_is_declared_and_every_declared_field_is_ordered(self):
		declared = [f["fieldname"] for f in self.doc["fields"] if "fieldname" in f]
		self.assertEqual(sorted(declared), sorted(self.doc["field_order"]))

	def test_the_existing_fields_are_still_there_in_their_original_order(self):
		original = [
			"default_day_hours",
			"maintenance_visit_hours",
			"section_rental",
			"rental_delivery_hours",
			"rental_setup_hours",
			"column_break_rental",
			"rental_takedown_hours",
			"rental_cleaning_hours",
		]
		order = self.doc["field_order"]
		self.assertEqual([name for name in order if name in original], original)
		self.assertEqual(self.fields["default_day_hours"]["default"], "8")
		self.assertEqual(self.fields["rental_setup_hours"]["default"], "3")

	def test_the_json_is_stamped_with_the_release_date(self):
		self.assertEqual(self.doc["modified"], "2026-10-09 12:00:00.000000")
		self.assertEqual(self.doc["name"], "Project Planner Settings")
		self.assertTrue(self.doc["issingle"])


class TestSettingsValidate(unittest.TestCase):
	def _settings(self, **new_values):
		values = {
			"default_day_hours": 8,
			"maintenance_visit_hours": 2,
			"rental_delivery_hours": 2,
			"rental_setup_hours": 3,
			"rental_takedown_hours": 2,
			"rental_cleaning_hours": 2,
			"meta": SimpleNamespace(get_label=lambda field: field),
		}
		values.update(new_values)
		return settings_mod.ProjectPlannerSettings(**values)

	def test_a_row_that_predates_the_routes_fields_saves(self):
		# The Single row has none of the route fields: frappe hands the validate None for each.
		doc = self._settings()
		for name in ROUTE_FIELDS + CACHE_FIELDS:
			self.assertIsNone(doc.get(name))
		doc.validate()

	def test_blank_and_none_route_values_are_never_refused(self):
		doc = self._settings(
			pad_drive_time=None,
			use_google_routes=None,
			day_start_time=None,
			long_drive_minutes=None,
			start_latitude=None,
			start_longitude=None,
			start_geocoded_from=None,
		)
		doc.validate()
		doc = self._settings(long_drive_minutes="", day_start_time="")
		doc.validate()

	def test_a_deliberate_zero_drive_limit_is_allowed(self):
		self._settings(long_drive_minutes=0, pad_drive_time=0, use_google_routes=0).validate()

	def test_a_negative_drive_limit_is_refused(self):
		with self.assertRaises(_Throw):
			self._settings(long_drive_minutes=-5).validate()

	def test_the_existing_hour_checks_still_refuse_what_they_refused(self):
		with self.assertRaises(_Throw):
			self._settings(default_day_hours=0).validate()
		with self.assertRaises(_Throw):
			self._settings(rental_setup_hours=25).validate()


class TestBackfillPatch(unittest.TestCase):
	def _run(self, stored=(), exists=True, sql_error=None):
		_db_writes.clear()
		frappe.logged.clear()

		def sql(query, params=None):
			if sql_error:
				raise sql_error
			return [(name,) for name in stored]

		frappe.db = SimpleNamespace(
			exists=lambda doctype, name=None: exists,
			sql=sql,
			set_single_value=lambda doctype, field, value: _db_writes.append((doctype, field, value)),
		)
		return patch_mod.backfill_project_planner_route_settings()

	def test_fills_only_the_route_fields_with_no_row(self):
		# pad_drive_time is stored (a deliberate choice, even if it is 0) and must be kept.
		written = self._run(stored=("default_day_hours", "pad_drive_time"))
		self.assertEqual(written, 3)
		self.assertEqual(
			sorted(_db_writes),
			sorted(
				[
					("Project Planner Settings", "use_google_routes", "1"),
					("Project Planner Settings", "day_start_time", "08:00:00"),
					("Project Planner Settings", "long_drive_minutes", "90"),
				]
			),
		)

	def test_never_writes_over_a_stored_value_even_a_falsy_one(self):
		stored = ROUTE_FIELDS
		self.assertEqual(self._run(stored=stored), 0)
		self.assertEqual(_db_writes, [])

	def test_a_stored_zero_for_a_drive_limit_is_left_alone(self):
		self._run(stored=("long_drive_minutes",))
		self.assertNotIn("long_drive_minutes", [field for _doc, field, _v in _db_writes])

	def test_a_never_saved_single_is_left_alone(self):
		self.assertEqual(self._run(stored=()), 0)
		self.assertEqual(_db_writes, [])

	def test_a_site_without_the_doctype_writes_nothing(self):
		self.assertEqual(self._run(stored=("default_day_hours",), exists=False), 0)
		self.assertEqual(_db_writes, [])

	def test_a_second_run_writes_nothing(self):
		# Nothing of the four is stored, so the first run writes all four.
		self.assertEqual(self._run(stored=("default_day_hours",)), 4)
		written_on_second_run = self._run(stored=ROUTE_FIELDS + ("default_day_hours",))
		self.assertEqual(written_on_second_run, 0)

	def test_execute_never_raises_and_logs_without_the_exception_text(self):
		frappe.logged.clear()
		frappe.db = SimpleNamespace(
			exists=lambda *a, **k: True,
			sql=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SECRET-ROW-CONTENT")),
			set_single_value=lambda *a, **k: None,
		)
		patch_mod.execute()
		self.assertEqual(len(frappe.logged), 1)
		title, message = frappe.logged[0]
		self.assertEqual(title, "Project Planner: route settings backfill")
		self.assertNotIn("SECRET-ROW-CONTENT", message)

	def test_the_patch_never_reads_tabsingles_through_an_ordering_helper(self):
		tree = ast.parse(PATCH.read_text(encoding="utf-8"))
		offenders = []
		for node in ast.walk(tree):
			if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
				continue
			if node.func.attr in {"get_value", "get_values", "get_all", "get_list", "exists"}:
				first = node.args[0] if node.args else None
				if isinstance(first, ast.Constant) and first.value == "Singles":
					offenders.append(node.lineno)
		self.assertEqual(offenders, [])
		self.assertIn("from tabSingles", PATCH.read_text(encoding="utf-8").replace("\n", " "))

	def test_the_patch_is_registered_after_model_sync_with_its_version_note(self):
		lines = PATCHES_TXT.read_text(encoding="utf-8").splitlines()
		self.assertIn(PATCH_NAME, lines)
		index = lines.index(PATCH_NAME)
		self.assertIn("[post_model_sync]", lines[: index])
		self.assertNotIn("[pre_model_sync]", lines[lines.index("[post_model_sync]") :])
		self.assertIn("# v1.578.0", " ".join(lines[index - 3 : index]))

	def test_the_patch_has_a_row_in_the_patches_index(self):
		rows = [
			line for line in PATCHES_README.read_text(encoding="utf-8").splitlines()
			if line.startswith("| `backfill_project_planner_route_settings` |")
		]
		self.assertEqual(len(rows), 1)
		self.assertIn("| post |", rows[0])


class TestMaterializeDefaults(unittest.TestCase):
	"""v1.578.1. Writing ONE row into a never-saved Single ends load_from_db's new_doc() defaults
	for every other field, and a blank Check loads as 0 (_fix_numeric_types). The routing
	module's cache of the shop's coordinates did exactly that, switching drive padding and Google
	Routes off; check_routes caught it on production before a planner load committed it."""

	def _run(self, stored=(), sql_error=None):
		_db_writes.clear()
		frappe.logged.clear()
		fields = list(_settings_meta_fields().values())

		def sql(query, params=None):
			if sql_error:
				raise sql_error
			return [(name,) for name in stored]

		frappe.db = SimpleNamespace(
			exists=lambda doctype, name=None: True,
			sql=sql,
			set_single_value=lambda doctype, values, **kw: _db_writes.append((doctype, dict(values), kw)),
		)
		frappe.get_meta = lambda doctype: SimpleNamespace(fields=fields, get_field=None, get_label=lambda f: f)
		return settings_mod.materialize_defaults()

	def test_a_never_saved_single_gets_every_default(self):
		# Ten declared defaults, plus Phase 3B's two "Telling people" switches (both "0").
		self.assertEqual(self._run(stored=()), 12)
		values = _db_writes[0][1]
		self.assertEqual(values["use_google_routes"], "1")
		self.assertEqual(values["pad_drive_time"], "1")
		self.assertEqual(values["default_day_hours"], "8")
		self.assertNotIn("start_latitude", values)  # a cache, with no default to write
		self.assertEqual(_db_writes[0][2], {"update_modified": False})

	def test_the_trap_state_is_healed_and_stored_values_are_kept(self):
		# The state routing produced: only the shop cache rows, plus a deliberate unticked box.
		written = self._run(stored=("start_latitude", "start_longitude", "start_geocoded_from", "pad_drive_time"))
		values = _db_writes[0][1]
		self.assertEqual(values["use_google_routes"], "1")
		self.assertNotIn("pad_drive_time", values)
		self.assertEqual(written, 11)

	def test_a_whole_single_writes_nothing(self):
		every = tuple(_settings_meta_fields())
		self.assertEqual(self._run(stored=every), 0)
		self.assertEqual(_db_writes, [])

	def test_it_never_raises(self):
		self.assertEqual(self._run(sql_error=RuntimeError("boom")), 0)
		self.assertEqual(len(frappe.logged), 1)

	def test_missing_defaults_is_fill_only(self):
		got = settings_mod.missing_defaults(
			[("a", "1"), ("b", "0"), ("c", None), ("d", ""), (None, "1")], {"b"}
		)
		self.assertEqual(got, {"a": "1"})

	def test_routing_makes_the_single_whole_before_it_caches_the_shop(self):
		source = (APP / "project_enhancements/routing.py").read_text(encoding="utf-8")
		body = source[source.index("def start_point(") :]
		body = body[: body.index("\ndef ", 1)]
		self.assertIn("materialize_defaults()", body)
		self.assertLess(body.index("materialize_defaults()"), body.index("frappe.db.set_single_value("))

	def test_the_patch_runs_it_after_model_sync(self):
		lines = PATCHES_TXT.read_text(encoding="utf-8").splitlines()
		name = "erpnext_enhancements.patches.materialize_project_planner_settings"
		self.assertIn(name, lines)
		index = lines.index(name)
		self.assertIn("# v1.578.1", " ".join(lines[index - 3 : index]))
		patch_source = (APP / "patches/materialize_project_planner_settings.py").read_text(encoding="utf-8")
		self.assertIn("materialize_defaults()", patch_source)
		rows = [l for l in PATCHES_README.read_text(encoding="utf-8").splitlines() if l.startswith("| `materialize_project_planner_settings` |")]
		self.assertEqual(len(rows), 1)


class TestPlannerDriveTimeDoctype(unittest.TestCase):
	def setUp(self):
		self.doc = _read(DRIVE_JSON)
		self.fields = _fields_by_name(self.doc)

	def test_it_is_named_and_filed_in_project_enhancements(self):
		self.assertEqual(self.doc["name"], "Planner Drive Time")
		self.assertEqual(self.doc["module"], "Project Enhancements")
		self.assertEqual(self.doc["doctype"], "DocType")
		self.assertEqual(DRIVE_JSON.parent.name, "planner_drive_time")

	def test_it_is_keyed_on_a_unique_read_only_pair_key(self):
		self.assertEqual(self.doc["autoname"], "field:pair_key")
		pair = self.fields["pair_key"]
		self.assertEqual(pair["fieldtype"], "Data")
		self.assertEqual(pair.get("reqd"), 1)
		self.assertEqual(pair.get("unique"), 1)
		self.assertEqual(pair.get("read_only"), 1)

	def test_every_field_is_read_only_and_none_is_typed_by_hand(self):
		for name, field in self.fields.items():
			with self.subTest(field=name):
				self.assertEqual(field.get("read_only"), 1)

	def test_the_coordinates_and_measures_have_the_agreed_types(self):
		for name in ("origin_lat", "origin_lng", "dest_lat", "dest_lng"):
			self.assertEqual(self.fields[name]["fieldtype"], "Float")
			self.assertEqual(self.fields[name]["precision"], "6")
		self.assertEqual(self.fields["minutes"]["fieldtype"], "Float")
		self.assertEqual(self.fields["minutes"]["precision"], "1")
		self.assertEqual(self.fields["km"]["fieldtype"], "Float")
		self.assertEqual(self.fields["km"]["precision"], "2")
		self.assertEqual(self.fields["fetched_on"]["fieldtype"], "Datetime")

	def test_only_google_answers_are_stored(self):
		source = self.fields["source"]
		self.assertEqual(source["fieldtype"], "Select")
		self.assertEqual(source["options"], "Google")
		self.assertEqual(source["default"], "Google")

	def test_it_has_no_new_button_and_is_not_tracked(self):
		self.assertEqual(self.doc["in_create"], 1)
		self.assertEqual(self.doc["track_changes"], 0)

	def test_its_permissions_are_the_agreed_ones(self):
		grants = {p["role"]: p for p in self.doc["permissions"]}
		self.assertEqual(set(grants), {"System Manager", "Projects Manager"})
		sm = grants["System Manager"]
		self.assertEqual((sm.get("read"), sm.get("delete"), sm.get("report")), (1, 1, 1))
		self.assertFalse(sm.get("write") or sm.get("create"))
		pm = grants["Projects Manager"]
		self.assertEqual((pm.get("read"), pm.get("report")), (1, 1))
		self.assertFalse(pm.get("write") or pm.get("create") or pm.get("delete"))

	def test_its_keys_are_the_same_format_as_planner_resource(self):
		ours = set(self.doc)
		theirs = set(_read(RESOURCE_JSON))
		self.assertTrue(ours <= theirs | {"in_create"}, f"keys not in planner_resource: {ours - theirs}")

	def test_the_controller_class_is_the_one_frappe_derives(self):
		# Frappe resolves the controller as doctype.replace(" ", "").replace("-", ""), with no
		# title-casing (CLAUDE.md), so the class must be spelled exactly that way.
		derived = self.doc["name"].replace(" ", "").replace("-", "")
		tree = ast.parse(DRIVE_PY.read_text(encoding="utf-8"))
		classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
		self.assertEqual(classes, [derived])

	def test_the_controller_is_a_plain_document_with_a_docstring(self):
		tree = ast.parse(DRIVE_PY.read_text(encoding="utf-8"))
		self.assertTrue(ast.get_docstring(tree))
		cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
		self.assertEqual([base.id for base in cls.bases], ["Document"])

	def test_the_package_init_exists(self):
		self.assertTrue((DRIVE_DIR / "__init__.py").exists())


class TestHooks(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.tree = ast.parse(HOOKS.read_text(encoding="utf-8"))

	def _top_level_value(self, name):
		for node in self.tree.body:
			if isinstance(node, ast.Assign) and any(
				isinstance(t, ast.Name) and t.id == name for t in node.targets
			):
				return node.value
		return None

	def test_the_daily_job_is_registered_exactly_once(self):
		scheduler = self._top_level_value("scheduler_events")
		self.assertIsInstance(scheduler, ast.Dict)
		daily = None
		for key, value in zip(scheduler.keys, scheduler.values):
			if isinstance(key, ast.Constant) and key.value == "daily":
				daily = ast.literal_eval(value)
		self.assertIsNotNone(daily, "scheduler_events has no daily list")
		self.assertEqual(daily.count(DAILY_JOB), 1)

	def test_both_tools_are_registered_exactly_once(self):
		tools = ast.literal_eval(self._top_level_value("assistant_tools"))
		for name in TOOLS:
			with self.subTest(tool=name):
				paths = [t for t in tools if t.split(".")[-2] == name]
				self.assertEqual(len(paths), 1, paths)
				self.assertTrue(paths[0].startswith("erpnext_enhancements.assistant_tools."))

	def test_the_new_routing_job_is_a_dotted_path_under_the_app(self):
		self.assertTrue(DAILY_JOB.startswith("erpnext_enhancements.project_enhancements.routing."))


class TestToolClassification(unittest.TestCase):
	def test_both_tools_are_read_only_in_the_gate(self):
		for name in TOOLS:
			with self.subTest(tool=name):
				self.assertIn(name, _gate.EXPLICIT_READONLY)
				self.assertNotIn(name, _gate.APP_MUTATING)
				self.assertNotIn(name, _gate.EXPLICIT_MUTATING)
				self.assertNotIn(name, _gate.HIGH_RISK)

	def test_both_advertise_the_read_only_annotation(self):
		for name, module in crew_tools.items():
			with self.subTest(tool=name):
				tool = module.__dict__[
					"CrewScheduleSuggestions" if name == "crew_schedule_suggestions" else "CrewDayRoute"
				]()
				self.assertIs(tool.annotations["readOnlyHint"], True)
				self.assertIs(tool.annotations["x-ee-mutation"], False)
				self.assertEqual(tool.annotations, _gate.annotations_for(name))


class TestToolContracts(unittest.TestCase):
	def _tool(self, name):
		cls = "CrewScheduleSuggestions" if name == "crew_schedule_suggestions" else "CrewDayRoute"
		return getattr(crew_tools[name], cls)()

	def test_name_matches_the_module_file(self):
		for name in TOOLS:
			self.assertEqual(self._tool(name).name, name)

	def test_metadata_is_set_the_way_the_schema_contract_needs(self):
		for name in TOOLS:
			tool = self._tool(name)
			self.assertEqual(tool.source_app, "erpnext_enhancements")
			self.assertEqual(tool.requires_permission, "Planner Resource")
			self.assertGreater(len(tool.description), 50)

	def test_the_suggestions_schema_takes_a_task_and_bounded_days(self):
		schema = self._tool("crew_schedule_suggestions").inputSchema
		self.assertEqual(schema["required"], ["task"])
		self.assertEqual(set(schema["properties"]), {"task", "days", "from_date"})
		self.assertEqual(schema["properties"]["days"]["type"], "integer")
		self.assertEqual((schema["properties"]["days"]["minimum"], schema["properties"]["days"]["maximum"]), (1, 30))
		self.assertEqual(schema["properties"]["days"]["default"], 10)
		self.assertEqual(schema["properties"]["from_date"]["type"], "string")

	def test_the_route_schema_takes_a_resource_and_a_date(self):
		schema = self._tool("crew_day_route").inputSchema
		self.assertEqual(set(schema["required"]), {"resource", "date"})
		self.assertEqual(set(schema["properties"]), {"resource", "date"})
		for prop in schema["properties"].values():
			self.assertEqual(prop["type"], "string")
			self.assertTrue(prop["description"])

	def test_the_suggestions_description_says_it_books_nothing(self):
		self.assertIn("Read-only; it books nothing.", self._tool("crew_schedule_suggestions").description)

	def test_each_module_defines_one_tool_and_its_execute(self):
		for name, module in crew_tools.items():
			with self.subTest(tool=name):
				classes = [
					obj for obj in vars(module).values()
					if isinstance(obj, type) and obj.__module__ == module.__name__
				]
				self.assertEqual(len(classes), 1)
				self.assertIn("execute", classes[0].__dict__)


class TestCrewScheduleSuggestionsExecute(unittest.TestCase):
	def setUp(self):
		calls.clear()
		self.tool = crew_tools["crew_schedule_suggestions"].CrewScheduleSuggestions()

	def test_a_task_is_required(self):
		for arguments in ({}, {"task": "  "}, {"task": None}):
			with self.subTest(arguments=arguments):
				result = self.tool.execute(arguments)
				self.assertFalse(result["success"])
				self.assertEqual(calls, [])

	def test_days_outside_one_to_thirty_are_refused_without_a_call(self):
		for days in (0, 31, -3, "lots"):
			with self.subTest(days=days):
				result = self.tool.execute({"task": "TASK-1", "days": days})
				self.assertFalse(result["success"])
		self.assertEqual(calls, [])

	def test_a_bad_from_date_is_refused_without_a_call(self):
		result = self.tool.execute({"task": "TASK-1", "from_date": "2026-13-40"})
		self.assertFalse(result["success"])
		self.assertEqual(calls, [])

	def test_the_defaults_are_ten_days_from_today(self):
		result = self.tool.execute({"task": "TASK-1"})
		self.assertEqual(calls, [("suggest_dates", "TASK-1", None, 10)])
		self.assertIs(result["success"], True)
		self.assertEqual(result["task"], "TASK-1")

	def test_the_arguments_reach_the_planner_api(self):
		result = self.tool.execute({"task": "TASK-7", "days": "5", "from_date": "2026-06-14"})
		self.assertEqual(calls, [("suggest_dates", "TASK-7", "2026-06-14", 5)])
		self.assertIs(result["success"], True)
		self.assertIn("suggestions", result)


class TestCrewDayRouteExecute(unittest.TestCase):
	def setUp(self):
		calls.clear()
		self.tool = crew_tools["crew_day_route"].CrewDayRoute()
		self.rows = [
			{"name": "RES-00001", "resource_name": "Jesse Doe", "user": "jesse@example.com", "is_active": 1},
			{"name": "RES-00002", "resource_name": "Jesse Doe", "user": "jesse.old@example.com", "is_active": 0},
			{"name": "RES-00003", "resource_name": "Korben Lee", "user": "korben@example.com", "is_active": 1},
		]
		frappe.get_list = self._get_list

	def _get_list(self, doctype, filters=None, or_filters=None, fields=None, **kwargs):
		if or_filters:
			matched = [
				row for row in self.rows
				if any(row.get(field) == value for field, _op, value in or_filters)
			]
			return [SimpleNamespace(**row) for row in matched]
		active = [row for row in self.rows if row["is_active"]]
		return [SimpleNamespace(**row) for row in active]

	def test_a_bad_date_is_refused_without_a_lookup_or_a_call(self):
		for date in ("", "2026-02-30", "June 14"):
			with self.subTest(date=date):
				result = self.tool.execute({"resource": "Korben Lee", "date": date})
				self.assertFalse(result["success"])
		self.assertEqual(calls, [])

	def test_a_missing_resource_is_refused(self):
		result = self.tool.execute({"resource": "", "date": "2026-06-14"})
		self.assertFalse(result["success"])
		self.assertEqual(calls, [])

	def test_a_full_name_resolves_to_the_resource(self):
		result = self.tool.execute({"resource": "Korben Lee", "date": "2026-06-14"})
		self.assertEqual(calls, [("get_route", "RES-00003", "2026-06-14")])
		self.assertIs(result["success"], True)

	def test_an_email_resolves_to_the_resource(self):
		self.tool.execute({"resource": "korben@example.com", "date": "2026-06-14"})
		self.assertEqual(calls, [("get_route", "RES-00003", "2026-06-14")])

	def test_the_resource_name_resolves_directly(self):
		self.tool.execute({"resource": "RES-00003", "date": "2026-06-14"})
		self.assertEqual(calls, [("get_route", "RES-00003", "2026-06-14")])

	def test_a_name_shared_by_two_people_resolves_to_the_one_active_resource(self):
		self.tool.execute({"resource": "Jesse Doe", "date": "2026-06-14"})
		self.assertEqual(calls, [("get_route", "RES-00001", "2026-06-14")])

	def test_a_name_shared_by_two_active_resources_is_refused_with_the_choices(self):
		self.rows[1]["is_active"] = 1
		result = self.tool.execute({"resource": "Jesse Doe", "date": "2026-06-14"})
		self.assertFalse(result["success"])
		self.assertIn("RES-00001", result["error"])
		self.assertIn("RES-00002", result["error"])
		self.assertEqual(calls, [])

	def test_an_unknown_name_lists_the_active_resources(self):
		result = self.tool.execute({"resource": "Nobody Here", "date": "2026-06-14"})
		self.assertFalse(result["success"])
		self.assertIn("Korben Lee (RES-00003)", result["error"])
		self.assertIn("Jesse Doe (RES-00001)", result["error"])
		self.assertNotIn("RES-00002", result["error"])
		self.assertEqual(calls, [])

	def test_the_browser_maps_key_is_never_returned(self):
		result = self.tool.execute({"resource": "Korben Lee", "date": "2026-06-14"})
		self.assertNotIn("maps_key", result)
		self.assertNotIn("AIza-browser-key-do-not-leak", json.dumps(result))
		self.assertEqual(result["map_ids"], {"map_id_light": "L", "map_id_dark": "D"})
		self.assertEqual(result["resource"], "RES-00003")
		self.assertEqual(result["date"], "2026-06-14")


if __name__ == "__main__":
	unittest.main()
