# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Planner Phase 6C (TASK-2026-02469): big picture, tablet mode and print, on both planners.

What this pins, because each part fails quietly:

* **Saved views live on the server, per user.** ``api/planner_views_prefs.py`` writes the caller's
  own ``__UserSettings`` row straight to the table (Frappe's own ``user_settings.save`` writes only
  to Redis, which every deploy flushes) and drops the cached copy; it keeps unknown top-level keys of
  the row, drops unknown view keys, caps the JSON and the number of views, never remembers Phase 4's
  *Running over* chip as "last used", and answers to each planner's own role gate. The pages never
  touch browser storage for a view.
* **The route wins and Back stays whole.** Only a bare planner URL takes the saved view, and it
  REPLACES that history entry (``route_flags.replace_route`` set before ``set_route``); a URL that
  names a view is used as it is. Driven under node against the real page code.
* **Colors are stable and read only what the card carries.** The hash is FNV-1a over UTF-16 code
  units (pinned against a Python twin, so every device paints a project the same), and every key a
  color mode reads is one the endpoints build (``project_type`` was added to the card for this).
* **The capacity strip's arithmetic** (red past capacity, amber at 90%, people off without the kind of
  time off, pencil apart) and that it sums only the people the group filter shows.
* **Tap to move goes through the drag's own path** (``drop`` -> ``plan_drop`` -> ``commit``/``apply``
  -> ``send``), so the reason prompt and Undo apply; the 6C blocks never call a write themselves.
* **Print** is built from the loaded data, escapes everything people typed, prints only 6B's
  selection when asked, and opens its window before any request (a pop-up blocker would refuse one
  opened later).

Bench-free: installs its own ``frappe`` stub, runs the real modules, puts ``sys.modules`` back. The
node parts skip when node is not on PATH.

Run: python -m unittest erpnext_enhancements.tests.test_planner_phase6c
"""

import ast
import datetime
import importlib
import json
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
PREFS = APP / "api/planner_views_prefs.py"
PP_API = APP / "api/project_planner.py"
MP_API = APP / "api/maintenance_planner.py"
PP_JS = APP / "project_enhancements/page/project_planner/project_planner.js"
MP_JS = APP / "sapphire_maintenance/page/maintenance_planner/maintenance_planner.js"
KIT_DIR = APP / "public/js/planner_kit"
BIG_PICTURE = KIT_DIR / "big_picture.js"
BUNDLE = APP / "public/js/planner_kit.bundle.js"
CI = REPO_ROOT / ".github/workflows/ci.yml"
API_README = APP / "api/README.md"
PE_README = APP / "project_enhancements/README.md"
SM_README = APP / "sapphire_maintenance/README.md"

MARKER = "// ====================================================================== Phase 6C"

# The planner_views_prefs methods the pages call, and every argument they send. The Project
# Planner's own endpoints are test_project_planner_page.API_CONTRACT; these live in their own module.
PREFS_CONTRACT = {
	"get_views": {"planner"},
	"save_view": {"planner", "name", "data"},
	"delete_view": {"planner", "name"},
	"save_last_view": {"planner", "data"},
	"get_print_chrome": {"planner", "title", "lines"},
}
WRITES = {"save_view", "delete_view", "save_last_view"}

D = datetime.date
TODAY = D(2026, 10, 9)
NOW = datetime.datetime(2026, 10, 9, 14, 30, 0)

_saved_modules = {}
_modules_before = set()
frappe = None
prefs = None
pp = None
mp = None


class _Throw(Exception):
	pass


class _PermissionError(Exception):
	pass


def _getdate(value=None):
	if value is None:
		return TODAY
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, D):
		return value
	return D.fromisoformat(str(value)[:10])


def _throw(message, exc=None, title=None):
	raise (exc or _Throw)(message)


class _FakeTable:
	"""``__UserSettings`` as a dict, with the two statements the module runs."""

	def __init__(self):
		self.rows = {}
		self.reads = []
		self.writes = []

	def sql(self, query, values=None, **kwargs):
		if "from `__UserSettings`" in query:
			self.reads.append((query, values))
			text = self.rows.get((values[0], values[1]))
			return [(text,)] if text is not None else []
		return []

	def multisql(self, sql_dict, values=(), **kwargs):
		self.writes.append((sql_dict, values))
		user, doctype, text, again = values
		assert text == again
		self.rows[(user, doctype)] = text


def setUpModule():
	global frappe, prefs, pp, mp
	_modules_before.update(sys.modules)
	for name in list(sys.modules):
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements")):
			_saved_modules[name] = sys.modules.pop(name)

	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.getdate = _getdate
	utils.add_days = lambda value, days: _getdate(value) + datetime.timedelta(days=days)
	utils.date_diff = lambda a, b: (_getdate(a) - _getdate(b)).days
	utils.formatdate = lambda value=None, *a, **k: _getdate(value).strftime("%m-%d-%Y")
	utils.format_datetime = lambda value=None, fmt=None: "Oct 9, 2026 2:30 PM"
	utils.nowdate = lambda: str(TODAY)
	utils.now_datetime = lambda: NOW
	utils.flt = lambda value, precision=None: float(value or 0)
	utils.cint = lambda value: int(float(value or 0))
	utils.strip_html_tags = lambda text: re.sub(r"<[^>]+>", "", str(text))
	utils.escape_html = lambda text: str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
	utils.get_url = lambda path="": "https://erp.example.com" + (path or "")
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	frappe.throw = _throw
	frappe.PermissionError = _PermissionError
	frappe.get_traceback = lambda: "Traceback: boom"
	sys.modules.update({"frappe": frappe, "frappe.utils": utils})
	_reset()

	mp = importlib.import_module("erpnext_enhancements.api.maintenance_planner")
	pp = importlib.import_module("erpnext_enhancements.api.project_planner")
	prefs = importlib.import_module("erpnext_enhancements.api.planner_views_prefs")


def tearDownModule():
	for name in set(sys.modules) - _modules_before:
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements")):
			sys.modules.pop(name, None)
	for name in list(sys.modules):
		if name == "frappe" or name.startswith("frappe."):
			sys.modules.pop(name, None)
	sys.modules.update(_saved_modules)


def _reset():
	frappe.roles = ["Projects User"]
	frappe.get_roles = lambda user=None: list(frappe.roles)
	frappe.session = types.SimpleNamespace(user="nik@example.com")
	frappe.local = types.SimpleNamespace(message_log=[], flags=types.SimpleNamespace())
	frappe.flags = types.SimpleNamespace()
	frappe.table = _FakeTable()
	frappe.dropped = []
	frappe.log_error = lambda *a, **k: None
	frappe.get_all = lambda *a, **k: []
	frappe.has_permission = lambda *a, **k: True
	frappe.cache = types.SimpleNamespace(hdel=lambda name, key: frappe.dropped.append((name, key)))
	frappe.db = types.SimpleNamespace(
		sql=frappe.table.sql,
		multisql=frappe.table.multisql,
		has_column=lambda *a, **k: True,
		exists=lambda *a, **k: False,
		get_value=lambda *a, **k: None,
		get_single_value=lambda *a, **k: None,
	)


def _row(user="nik@example.com", doctype="Project Planner"):
	text = frappe.table.rows.get((user, doctype))
	return json.loads(text) if text else None


def _strip(code):
	code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
	return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in code.splitlines())


def _block(path):
	"""The Phase 6C block: from its banner to the next phase's banner (or the end of the file).

	A later phase appended after it (6B, 6D UI) writes through ``send`` on purpose and has its own
	suite pinning its own rules, so 6C's house rules must not read into it.
	"""
	code = path.read_text(encoding="utf-8")
	start = code.index(MARKER)
	end = code.find("\n// ======================================================================", start + len(MARKER))
	return code[start:] if end < 0 else code[start:end]


def _obj_method(block, name):
	"""One method of a mixin object (``\\tname(...) {`` to ``\\t},``)."""
	match = re.search(rf"^\t{name}\(.*?\) \{{\n(.*?)^\t\}},\n", block, flags=re.S | re.M)
	assert match, f"no method {name}"
	return match.group(1)


def _functions(path):
	return {
		n.name: n for n in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)
	}


# ====================================================================== the views store


class TestCleaning(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_unknown_keys_are_dropped_and_types_enforced(self):
		view = prefs.clean_view(
			"project",
			{
				"view": "crew",
				"group": "Field",
				"color_by": "job_type",
				"foreign": "0",
				"panel": 1,
				"over_only": True,
				"project": "  PRJ-00612\n",
				"pm": "x" * 500,
				"evil": "<script>",
				"__proto__": {"a": 1},
			},
		)
		self.assertEqual(
			set(view), {"view", "group", "color_by", "foreign", "panel", "over_only", "project", "pm"}
		)
		self.assertIs(view["foreign"], False)
		self.assertIs(view["panel"], True)
		self.assertEqual(view["project"], "PRJ-00612")
		self.assertEqual(len(view["pm"]), prefs.MAX_TEXT)

	def test_an_enum_outside_its_values_is_dropped_not_refused(self):
		view = prefs.clean_view(
			"project", {"view": "route", "group": "Office", "color_by": "rainbow", "pm": ["a"]}
		)
		self.assertEqual(view, {})
		self.assertEqual(
			prefs.clean_view("maintenance", {"view": "heatmap", "technician": "a@b.co"}),
			{"technician": "a@b.co"},
		)
		self.assertEqual(prefs.clean_view("nope", {"view": "week"}), {})

	def test_last_used_never_holds_the_running_over_chip(self):
		self.assertNotIn("over_only", prefs.clean_view("project", {"over_only": True}, remembered=True))
		self.assertIn("over_only", prefs.clean_view("project", {"over_only": True}))
		self.assertEqual(prefs.NOT_REMEMBERED, ("over_only",))

	def test_the_size_cap_is_checked_before_parsing(self):
		with self.assertRaises(_Throw):
			prefs.parse_view(json.dumps({"project": "x" * (prefs.MAX_VIEW_BYTES + 10)}))
		with self.assertRaises(_Throw):
			prefs.parse_view("[1, 2]")
		with self.assertRaises(_Throw):
			prefs.parse_view("{not json")
		self.assertEqual(prefs.parse_view('{"view": "week"}'), {"view": "week"})
		self.assertEqual(prefs.parse_view({"view": "week"}), {"view": "week"})

	def test_a_name_is_trimmed_capped_and_required(self):
		self.assertEqual(prefs.clean_name("  Field\tcrew,  Build only  "), "Field crew, Build only")
		self.assertEqual(len(prefs.clean_name("n" * 200)), prefs.MAX_NAME)
		with self.assertRaises(_Throw):
			prefs.clean_name(" \n ")

	def test_put_view_replaces_by_name_and_caps_the_count(self):
		views = []
		for index in range(prefs.MAX_VIEWS):
			views = prefs.put_view(views, f"View {index:02d}", {"view": "week"}, "t")
		self.assertEqual(len(views), prefs.MAX_VIEWS)
		with self.assertRaises(_Throw):
			prefs.put_view(views, "One too many", {}, "t")
		replaced = prefs.put_view(views, "view 03", {"view": "crew"}, "t2")
		self.assertEqual(len(replaced), prefs.MAX_VIEWS)
		self.assertEqual(next(v for v in replaced if v["name"] == "view 03")["data"], {"view": "crew"})
		self.assertEqual([v["name"] for v in replaced], sorted((v["name"] for v in replaced), key=str.lower))

	def test_a_stored_row_is_cleaned_again_on_the_way_out(self):
		store = prefs.read_store(
			"project",
			{
				"planner_views": {
					"last": {"view": "crew", "over_only": True, "gone": 1},
					"views": [
						{"name": "A", "data": {"group": "PM", "old": 2}},
						{"name": "a", "data": {}},
						{"name": "", "data": {}},
						"junk",
					],
				}
			},
		)
		self.assertEqual(store["last"], {"view": "crew"})
		self.assertEqual([v["name"] for v in store["views"]], ["A"])
		self.assertEqual(store["views"][0]["data"], {"group": "PM"})
		self.assertEqual(prefs.read_store("project", {}), {"last": None, "views": []})
		self.assertEqual(
			prefs.read_store("project", {"planner_views": "nonsense"}), {"last": None, "views": []}
		)

	def test_the_page_schemas_match_the_server(self):
		for path, constant, planner in ((PP_JS, "PP6C", "project"), (MP_JS, "MP6C", "maintenance")):
			block = _block(path)
			body = block[block.index(f"const {constant} = {{") :]
			schema_text = body[body.index("schema: {") : body.index("\t},\n", body.index("schema: {"))]
			keys = dict(re.findall(r"\n\t\t(\w+): \[(.*?)\],?(?=\n)", schema_text))
			server = prefs.SCHEMA[planner]
			self.assertEqual(set(keys), set(server), planner)
			for key, rule in server.items():
				self.assertIn(f'"{rule[0]}"', keys[key], key)
				if rule[0] == "enum":
					values = re.findall(r'"([^"]*)"', keys[key].split(",", 1)[1])
					self.assertEqual(tuple(values), tuple(rule[1]), key)
		# The page's own constants agree with the enums: the groups, and the color modes offered.
		pp_code = PP_JS.read_text(encoding="utf-8")
		self.assertIn('groups: ["Field", "PM", "Design", "Subcontractor"]', pp_code)
		self.assertEqual(prefs.PROJECT_GROUPS, ("", "Field", "PM", "Design", "Subcontractor"))
		pp_modes = re.findall(
			r'\["(\w+)", "[^"]+"\]', _block(PP_JS).split("color_modes: [")[1].split("],\n\tview_labels")[0]
		)
		self.assertEqual(tuple(pp_modes), prefs.PROJECT_COLOR_MODES)
		mp_modes = re.findall(
			r'\["(\w+)", "[^"]+"\]', _block(MP_JS).split("color_modes: [")[1].split("],\n\tview_labels")[0]
		)
		self.assertEqual(tuple(mp_modes), prefs.MAINTENANCE_COLOR_MODES)


class TestEndpoints(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_view_is_saved_to_the_callers_own_row_in_the_table(self):
		answer = prefs.save_view(
			"project", "Field crew, Build only", json.dumps({"view": "crew", "group": "Field", "x": 1})
		)
		self.assertEqual(answer["saved"], "Field crew, Build only")
		((sql, values),) = frappe.table.writes
		self.assertIn("ON DUPLICATE KEY UPDATE", sql["mariadb"])
		self.assertEqual(values[:2], ("nik@example.com", "Project Planner"))
		row = _row()
		self.assertEqual(row["planner_views"]["views"][0]["data"], {"view": "crew", "group": "Field"})
		self.assertEqual(row["planner_views"]["version"], prefs.STORE_VERSION)
		# The cached copy frappe's sync would write back is dropped.
		self.assertEqual(frappe.dropped, [("_user_settings", "Project Planner::nik@example.com")])
		# Another person's views are another row.
		frappe.session.user = "lisa@example.com"
		self.assertEqual(prefs.get_views("project")["views"], [])
		self.assertEqual(frappe.table.reads[-1][1], ("lisa@example.com", "Project Planner"))

	def test_get_views_answers_last_and_named_views(self):
		prefs.save_view("project", "B view", {"view": "month"})
		prefs.save_view("project", "a view", {"view": "week"})
		prefs.save_last_view("project", json.dumps({"view": "crew", "over_only": True, "color_by": "pm"}))
		answer = prefs.get_views("project")
		self.assertEqual(answer["planner"], "project")
		self.assertEqual(answer["last"], {"view": "crew", "color_by": "pm"})
		self.assertEqual([v["name"] for v in answer["views"]], ["a view", "B view"])
		self.assertEqual(answer["max_views"], prefs.MAX_VIEWS)
		self.assertTrue(all(v["saved_on"] == "2026-10-09 14:30:00" for v in answer["views"]))

	def test_last_used_is_written_only_when_it_changed(self):
		prefs.save_last_view("project", {"view": "week", "group": "PM"})
		prefs.save_last_view("project", {"view": "week", "group": "PM"})
		self.assertEqual(len(frappe.table.writes), 1)
		prefs.save_last_view("project", {"view": "crew", "group": "PM"})
		self.assertEqual(len(frappe.table.writes), 2)

	def test_other_keys_of_the_row_are_kept(self):
		frappe.table.rows[("nik@example.com", "Project Planner")] = json.dumps({"List": {"x": 1}})
		prefs.save_view("project", "Mine", {"view": "week"})
		row = _row()
		self.assertEqual(row["List"], {"x": 1})
		self.assertIn("planner_views", row)

	def test_a_garbled_row_reads_as_empty(self):
		frappe.table.rows[("nik@example.com", "Project Planner")] = "{not json"
		self.assertEqual(prefs.get_views("project")["views"], [])
		frappe.table.rows[("nik@example.com", "Project Planner")] = "[1]"
		self.assertEqual(prefs.get_views("project")["views"], [])

	def test_delete_removes_one_view_case_aside(self):
		prefs.save_view("project", "Field", {"view": "week"})
		prefs.save_view("project", "Office", {"view": "month"})
		answer = prefs.delete_view("project", "field")
		self.assertTrue(answer["deleted"])
		self.assertEqual([v["name"] for v in answer["views"]], ["Office"])
		writes = len(frappe.table.writes)
		self.assertFalse(prefs.delete_view("project", "nothing")["deleted"])
		self.assertEqual(len(frappe.table.writes), writes)

	def test_the_row_size_is_capped(self):
		original = prefs.MAX_ROW_BYTES
		prefs.MAX_ROW_BYTES = 300
		try:
			with self.assertRaises(_Throw):
				for index in range(10):
					prefs.save_view("project", f"View number {index}", {"project": "P" * 100})
		finally:
			prefs.MAX_ROW_BYTES = original

	def test_each_planner_has_its_own_row_and_its_own_gate(self):
		frappe.roles = ["Maintenance User"]
		prefs.save_view("maintenance", "Austin", {"view": "crew", "technician": "austin@example.com"})
		self.assertIsNotNone(_row(doctype="Maintenance Planner"))
		self.assertIsNone(_row(doctype="Project Planner"))
		# Projects User may use the Project Planner but not the Maintenance Planner.
		frappe.roles = ["Projects User"]
		with self.assertRaises(_PermissionError):
			prefs.get_views("maintenance")
		self.assertEqual(prefs.get_views("project")["views"], [])
		frappe.roles = ["Blogger"]
		for planner in ("project", "maintenance"):
			with self.assertRaises(_PermissionError):
				prefs.get_views(planner)
			with self.assertRaises(_PermissionError):
				prefs.save_view(planner, "x", {})
		with self.assertRaises(_Throw):
			prefs.get_views("payroll")
		frappe.roles = ["Maintenance User"]
		self.assertEqual(prefs.get_views("maintenance")["views"][0]["name"], "Austin")

	def test_the_print_chrome_is_the_print_design_systems_and_escapes(self):
		answer = prefs.get_print_chrome(
			"project",
			"Week · Oct 11 <b>",
			json.dumps(["Project: Riverwalk <script>alert(1)</script>", "Group: Field"]),
		)
		self.assertIn("Week · Oct 11 &lt;b&gt;", answer["open"])
		self.assertIn("Riverwalk &lt;script&gt;alert(1)&lt;/script&gt;", answer["open"])
		self.assertNotIn("<script>", answer["open"])
		self.assertIn("Printed Oct 9, 2026 2:30 PM", answer["open"])
		self.assertIn("height:12px", answer["open"])  # print_style's top stripe
		self.assertIn("Sapphire Fountains, LLC", answer["close"])
		self.assertIn("border-bottom:2px solid", answer["th"])
		self.assertIn("border-bottom:1px solid", answer["td"])
		# The Maintenance Planner prints under the Service pillar; at most eight lines.
		frappe.roles = ["Maintenance User"]
		service = prefs.get_print_chrome("maintenance", None, json.dumps([f"line {i}" for i in range(20)]))
		self.assertIn("SERVICE", service["open"])
		self.assertIn("line 7", service["open"])
		self.assertNotIn("line 8", service["open"])
		self.assertIn("Maintenance Planner", service["open"])

	def test_the_module_contract(self):
		functions = _functions(PREFS)
		for method, args in PREFS_CONTRACT.items():
			node = functions[method]
			params = {a.arg for a in node.args.args + node.args.kwonlyargs}
			self.assertTrue(args <= params, f"{method} lacks {sorted(args - params)}")
			decorators = [ast.unparse(d) for d in node.decorator_list]
			if method in WRITES:
				self.assertIn("frappe.whitelist(methods=['POST'])", decorators, method)
			else:
				self.assertIn("frappe.whitelist()", decorators, method)
		source = PREFS.read_text(encoding="utf-8")
		self.assertNotIn("\n    def ", source)
		self.assertIn("\tif ", source)
		self.assertNotIn("\r", source)
		# Never through frappe's cache-only user_settings.save; always the session user's row.
		tree = ast.parse(source)
		names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
		names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
		self.assertNotIn("user_settings", names)
		self.assertIn("frappe.session.user", source)
		self.assertIn('frappe.cache.hdel("_user_settings"', source)


# ====================================================================== payload keys the colors read


class TestPayloadContract(unittest.TestCase):
	def test_the_card_carries_the_project_type(self):
		task = {"name": "TASK-1", "project": "PRJ-1", "project_type": "Build", "exp_start_date": "2026-10-12"}
		card = pp.build_card(task, [], {}, [], TODAY, True)
		self.assertEqual(card["project_type"], "Build")
		self.assertIsNone(pp.build_card({"name": "TASK-2"}, [], {}, [], TODAY, True)["project_type"])

	def test_the_maintenance_cell_carries_pencil_hours(self):
		cell = mp._booking_cell({"capacity": 8, "booked": 2, "soft_booked": 3, "free": 6, "bookings": []})
		self.assertEqual(cell["soft_booked"], 3)
		self.assertEqual(mp._booking_cell({"capacity": 8, "bookings": []})["soft_booked"], 0)

	def test_every_key_a_color_mode_reads_is_built(self):
		pp_source = PP_API.read_text(encoding="utf-8")
		card_keys = set(
			re.findall(
				r'^\t\t"(\w+)":',
				pp_source[pp_source.index("def build_card(") : pp_source.index("def needs_crew(")],
				re.M,
			)
		)
		pp_color = _obj_method(_block(PP_JS), "p6c_card_color")
		read = set(re.findall(r"\bcard\.(\w+)", pp_color))
		self.assertTrue(read, "p6c_card_color reads nothing")
		self.assertEqual(sorted(read - card_keys), [])
		self.assertIn("project.pm", pp_color)
		self.assertIn('"pm": r.get("pm")', pp_source)  # _projects
		mp_source = MP_API.read_text(encoding="utf-8")
		mp_block = _block(MP_JS)
		read = set()
		for name in ("p6c_card_color", "p6c_site_key", "p6c_kind_color"):
			read |= set(re.findall(r"\bcard\.(\w+)", _obj_method(mp_block, name)))
		js = MP_JS.read_text(encoding="utf-8")
		# Built by the API (`_visit_card`, `_decorate`, the projections) or by the page (booking cards).
		missing = sorted(
			key for key in read if f'"{key}"' not in mp_source and f"{key}:" not in js.split(MARKER)[0]
		)
		self.assertEqual(missing, [])


# ====================================================================== the pages, read as files


class TestPages(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.pp = PP_JS.read_text(encoding="utf-8")
		cls.mp = MP_JS.read_text(encoding="utf-8")
		cls.pp_block = _block(PP_JS)
		cls.mp_block = _block(MP_JS)

	def test_the_hooks_are_one_line_each(self):
		for line in (
			"this.init_phase6c();",
			"if (this.p6c_before_route()) return;",
			"this.render_phase6c();",
			"links.push(...this.p6c_links(card));",
			"if (this.p6c_action(action, card)) return;",
			"this.p6c_ready();",
			"...this.p6c_legend_sections(),",
		):
			self.assertEqual(self.pp.split(MARKER)[0].count(line), 1, f"pp: {line}")
			self.assertEqual(self.mp.split(MARKER)[0].count(line), 1, f"mp: {line}")
		self.assertEqual(self.pp.count("this.p6c_set_mode(mode);"), 1)
		self.assertEqual(self.pp.count("this.p6c_project_drawer(project, handle);"), 1)
		self.assertEqual(self.mp.count("this.p6c_site_drawer(card, handle);"), 1)
		self.assertIn("Object.assign(ProjectPlanner.prototype, PP6C_METHODS);", self.pp)
		self.assertIn("Object.assign(MaintenancePlanner.prototype, MP6C_METHODS);", self.mp)
		# handle_route asks first, before it reads the route.
		for code in (self.pp, self.mp):
			body = code[code.index("\thandle_route() {") :]
			self.assertTrue(body.split("\n")[1].strip() == "if (this.p6c_before_route()) return;")
		# Every block method has the prefix, so it cannot silently replace another phase's.
		for block in (self.pp_block, self.mp_block):
			names = re.findall(r"^\t([a-z]\w*)\(.*\) \{$", block, re.M)
			self.assertTrue(names)
			self.assertEqual([n for n in names if not (n.startswith("p6c_") or n.endswith("_phase6c"))], [])

	def test_no_method_is_defined_twice(self):
		for code in (self.pp, self.mp):
			names = re.findall(r"^\t(?:async\s+)?([A-Za-z_]\w*)\s*\([^)]*\)\s*\{\s*$", code, re.M)
			names = [n for n in names if n not in {"if", "for", "while", "switch", "catch", "function"}]
			self.assertEqual(sorted({n for n in names if names.count(n) > 1}), [])

	def test_house_rules(self):
		for block in (self.pp_block, self.mp_block):
			bare = _strip(block)
			for forbidden in (
				"localStorage",
				"sessionStorage",
				"indexedDB",
				"pushState",
				"replaceState",
				"window.location",
				"location.href",
				"history.",
				"draggable",
				"dragstart",
				"dataTransfer",
				'type: "GET"',
				"frappe.xcall",
				"this.send(",
				"cloneNode",
				"outerHTML",
				"download_pdf",
				"print_format",
				"/api/method/frappe.utils.print",
			):
				self.assertNotIn(forbidden, bare, forbidden)
			raw = [
				line.strip()
				for line in block.splitlines()
				if "<" in line
				and re.search(
					r"\$\{(?:card|item|data|visit|member|row|booking|forecast|project|profile|entry|view|resource|day|person|stop|answer|result)\.\w+",
					line,
				)
			]
			self.assertEqual(raw, [])
			for british in ("colour", "cancelled", "labour", "centre", "behaviour", "favour"):
				self.assertNotIn(british, block.lower(), british)
			style = block[block.index("_STYLE = `") : block.index("`;", block.index("_STYLE = `"))]
			prefix = "pp" if block is self.pp_block else "mp"
			classes = set(re.findall(r"\.([a-zA-Z][\w-]*)", style))
			self.assertTrue(classes)
			self.assertEqual(
				{c for c in classes if not c.startswith(f"{prefix}-")}, {"btn"} if "btn" in classes else set()
			)
			self.assertIn("@media (pointer:coarse)", style)
			self.assertIn("@media (max-width:760px)", style)
			self.assertIn("min-height:40px", style)
		# The pages' storage helpers stay the only browser storage (tap to move is this device's pref).
		self.assertEqual(self.pp.count("window.localStorage"), 2)
		self.assertEqual(self.mp.count("localStorage"), 2)
		self.assertIn("this.save_pref(PP6C.tap_pref", self.pp_block)
		self.assertIn("this.save_pref(MP6C.tap_pref", self.mp_block)

	def test_the_pages_call_only_contract_methods_with_contract_arguments(self):
		for block, constant, planner in (
			(self.pp_block, "PP6C", "project"),
			(self.mp_block, "MP6C", "maintenance"),
		):
			self.assertIn(f'planner: "{planner}"', block)
			self.assertIn('prefs: "erpnext_enhancements.api.planner_views_prefs"', block)
			calls = re.findall(r"method: `\$\{" + constant + r"\.prefs\}\.(\w+)`,\s*args: \{([^}]*)\}", block)
			self.assertEqual({method for method, _args in calls}, set(PREFS_CONTRACT))
			for method, args in calls:
				sent = {part.split(":")[0].strip() for part in args.split(",") if part.strip()}
				self.assertTrue(sent <= PREFS_CONTRACT[method], f"{method}: {sent}")
			# Views go over the wire as JSON text.
			self.assertIn("data: JSON.stringify(", block)
			self.assertIn("lines: JSON.stringify(model.lines)", block)

	def test_restoring_a_view_replaces_the_bare_route_and_the_route_wins(self):
		for block, route in ((self.pp_block, "PP.route"), (self.mp_block, "MP.route")):
			body = _obj_method(block, "p6c_before_route")
			guard = body.index("if (!route[1] && view && view !== ")
			flag = body.index("frappe.route_flags.replace_route = true;")
			go = body.index(f"frappe.set_route({route}, view, frappe.datetime.get_today());")
			self.assertLess(guard, flag)
			self.assertLess(flag, go)
			# The first open restores the filters whatever the route; only once.
			self.assertIn("if (!p6c.restored) {", body)
			self.assertIn("p6c.restored = true;", body)
			# A slow answer cannot keep the planner blank.
			self.assertIn("restore_wait", _obj_method(block, "p6c_fetch_views"))

	def test_tap_to_move_goes_through_the_drags_own_path(self):
		for block in (self.pp_block, self.mp_block):
			drop = _obj_method(block, "p6c_tap_drop")
			self.assertIn("this.drop(moving.source, place);", drop)
			click = _obj_method(block, "p6c_tap_click")
			self.assertIn("const source = this.drag_source(target);", click)
			self.assertIn("this.p6c_tap_drop(place);", click)
			# Modifier clicks belong to 6B's multi-select; long-press belongs to 6B's menu.
			self.assertIn("e.ctrlKey || e.metaKey || e.shiftKey || e.altKey", click)
			self.assertNotIn("contextmenu", _strip(block))
			self.assertNotIn("setTimeout(() => this.p6c_arm", block)
			# Taken before the quick looks and the card's own click see it.
			self.assertIn('document.addEventListener("click", (e) => this.p6c_tap_click(e), true);', block)
			self.assertIn('matchMedia("(pointer: coarse)")', block)
			bare = _strip(block)
			for write in ("save_task", "move_visit", "move_projected", "add_crew", "swap_crew"):
				self.assertNotIn(write, bare, write)

	def test_print_opens_its_window_first_and_builds_from_data(self):
		for block in (self.pp_block, self.mp_block):
			body = _obj_method(block, "p6c_print")
			self.assertLess(body.index('window.open("", "_blank")'), body.index("frappe.call("))
			self.assertIn("kit.big_picture.print_document(", body)
			self.assertIn("win.print()", body)
			self.assertIn("this.p6c_print_model(!!selection_only)", body)
			model = _obj_method(block, "p6c_print_model")
			self.assertIn("this.p6c_selection()", model)
			self.assertIn("selected.has(", model)
			selection = _obj_method(block, "p6c_selection")
			self.assertIn("this.p6_selection", selection)
			self.assertIn('typeof selection.has === "function" && selection.size', selection)
			click = _obj_method(block, "p6c_print_click")
			self.assertIn('__("Print selection only ({0})", [selected.size])', click)

	def test_the_menu_providers_and_entry_points(self):
		for block in (self.pp_block, self.mp_block):
			init = _obj_method(block, "init_phase6c")
			self.assertIn("this.p6_menu_providers = this.p6_menu_providers || [];", init)
			self.assertIn("this.p6_menu_providers.push((target) => this.p6c_menu_items(target));", init)
			items = _obj_method(block, "p6c_menu_items")
			self.assertIn('target.kind !== "card"', items)
		self.assertIn('__("Highlight this project")', self.pp_block)
		self.assertIn('__("Highlight this site")', self.mp_block)
		# A project's name on a card highlights it (after 6A's own capture listener opened its drawer).
		self.assertIn('target.closest(".pp-card [data-pk-project]")', self.pp_block)
		self.assertIn(
			'this.$body[0].addEventListener("click", (e) => this.p6c_body_click(e), true);', self.pp_block
		)
		# Esc yields to a drawer or menu on top, to a Frappe dialog and to a drag.
		for block in (self.pp_block, self.mp_block):
			keydown = _obj_method(block, "p6c_keydown")
			for needle in ("kit.guard.top()", "window.cur_dialog && window.cur_dialog.display", "this.drag"):
				self.assertIn(needle, keydown)

	def test_the_strip_uses_the_group_filter_and_the_day_peek(self):
		pp_sum = _obj_method(self.pp_block, "p6c_team_sum")
		self.assertIn("this.resources()", pp_sum)
		self.assertIn("this.day_of(resource.name, ymd)", pp_sum)
		self.assertIn("this.cell_of(person.user, ymd)", _obj_method(self.mp_block, "p6c_team_sum"))
		for block in (self.pp_block, self.mp_block):
			self.assertIn('data-pp6a-day="', _obj_method(block, "p6c_strip_cell_html"))

	def test_the_kit_module_is_in_the_bundle_and_pure(self):
		bundle = BUNDLE.read_text(encoding="utf-8")
		self.assertIn('import * as big_picture from "./planner_kit/big_picture.js";', bundle)
		self.assertIn("\t\tbig_picture,\n", bundle)
		source = BIG_PICTURE.read_text(encoding="utf-8")
		self.assertNotIn("\r", source)
		bare = _strip(source)
		for forbidden in (
			"document.",
			"window.",
			"frappe.",
			"localStorage",
			"eval(",
			"new Function",
			"innerHTML",
		):
			self.assertNotIn(forbidden, bare, forbidden)

	def test_the_suite_has_its_own_ci_step_and_the_docs(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_planner_phase6c -v", ci)
		self.assertLess(
			ci.index("erpnext_enhancements.tests.test_planner_phase6d -v"),
			ci.index("erpnext_enhancements.tests.test_planner_phase6c -v"),
		)
		readme = API_README.read_text(encoding="utf-8")
		self.assertIn("planner_views_prefs.py", readme)
		for method in PREFS_CONTRACT:
			self.assertIn(method, readme)
		self.assertIn("planner_views_prefs", readme.split("**Tab-indented**")[1].split("Every other file")[0])
		pe = PE_README.read_text(encoding="utf-8")
		self.assertIn("Phase 6C", pe)
		self.assertIn("big_picture", pe)
		self.assertIn("Phase 6C", SM_README.read_text(encoding="utf-8"))


# ====================================================================== under node


def _module_source(path):
	text = path.read_text(encoding="utf-8")
	text = re.sub(r"^import .*?;\s*$", "", text, flags=re.M)
	return re.sub(r"^export (const|function|class) ", r"\1 ", text, flags=re.M)


HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const out = {};

function kit_context(extra) {
	const ctx = Object.assign({ console, Promise, setTimeout, clearTimeout, JSON, Math, Date }, extra || {});
	ctx.__ = (text, args) => String(text).replace(/\{(\d+)\}/g, (m, i) => (args && args[i] != null ? args[i] : m));
	vm.createContext(ctx);
	vm.runInContext(input.kit + `
		this.kit = {
			big_picture: { HASH_PALETTE, hash_index, hash_color, JOB_TYPES, job_type, TASK_STATUSES, task_status,
				VISIT_STATUSES, visit_status, palette_color, safe_paint, TEAM_AMBER, team_day, team_text, team_tip,
				pick_view, PRINT_CSS, print_document, palette_legend },
			format: { hours, drive, day_label, add_days, t },
			escape,
		};`, ctx);
	return ctx;
}

// ---------------------------------------------------------------- the kit module
{
	const ctx = kit_context();
	const bp = ctx.kit.big_picture;
	out.hash = input.hash_keys.map((key) => [key, bp.hash_index(key, 12), bp.hash_color(key)]);
	out.palette = bp.HASH_PALETTE;
	out.job = [["Design", false], ["Build", false], ["Service", false], ["Rent", false], ["Events", false],
		["Delivery", false], ["", false], ["Internal", false], ["Build", true]].map(([t, r]) => bp.job_type(t, r));
	out.status = [["Open", false], ["Working", false], ["Pending Review", false], ["Open", true], ["Overdue", false], ["Template", false]]
		.map(([s, o]) => bp.task_status(s, o));
	out.visit = [{ kind: "projected", movable: true }, { kind: "projected" }, { kind: "visit", status: "done" },
		{ kind: "visit", status: "pending" }, { kind: "visit", status: "draft", overdue: true }, { kind: "visit", status: "draft" }]
		.map((card) => bp.visit_status(card));
	const day = (capacity, booked, extra) => Object.assign({ capacity, booked, free: Math.max(0, capacity - booked) }, extra || {});
	out.levels = [
		bp.team_day([day(50, 45), day(50, 45)]).level,          // exactly 90%: amber
		bp.team_day([day(50, 44.9), day(50, 45)]).level,        // 89.9%: green
		bp.team_day([day(50, 50.004), day(50, 50)]).level,      // within the tolerance: amber
		bp.team_day([day(50, 60), day(50, 41)]).level,          // past capacity as a whole: red
		bp.team_day([day(0, 0, { off: "Not a work day" })]).level,
		bp.team_day([day(0, 2, { off: "Holiday" })]).level,
	];
	// One person far over and one free: the team is not over, and the free hours are the free one's.
	out.mixed = bp.team_day([day(8, 12), day(8, 2)]);
	out.crew = input.crew_cells;
	out.crew_sum = bp.team_day(input.crew_cells);
	out.crew_tip = bp.team_tip("Field crew", "Thu", out.crew_sum);
	out.crew_text = bp.team_text(out.crew_sum);
	out.empty_tip = bp.team_tip("Team", "Sun", bp.team_day([]));
	out.picked = bp.pick_view({ view: "crew", group: "Office", color_by: "job_type", foreign: 0, project: " A\n B ", evil: 1 }, {
		view: ["enum", ["week", "crew"]], group: ["enum", ["", "Field"]], color_by: ["enum", ["default", "job_type"]],
		foreign: ["bool"], project: ["text"],
	});
	out.paint = [bp.safe_paint("#12ab34"), bp.safe_paint("hsl(120,55%,42%)"), bp.safe_paint("red;x:url(1)", "#000"), bp.safe_paint("expression(1)")];
	out.print_plain = bp.print_document({
		doc_title: "Week <1>", heading: "Week <1>", lines: ["Project: A&B <i>"], columns: ["Sun <x>"],
		rows: [{ cells: [{ head: "Oct <11>", sub: "46h free", items: [
			{ title: "Dig <img src=x onerror=1>", sub: "Riverwalk \"quoted\"", color: "javascript:alert(1)", marks: ["Pencil", { text: "Over <b>", tone: "red" }] },
		] }] }],
		legend: [{ color: "#2563eb", label: "Build <b>" }, { color: "url(x)", label: "Bad" }],
		notes: ["Note <n>"],
	});
	out.print_chrome = bp.print_document({ chrome: { open: "<div id='chrome-open'>", close: "<div id='chrome-close'>", th: "color:#000", td: "padding:1px" },
		layout: "list", columns: ["Dates"], rows: [{ color: "#2563eb", cols: ["Mon <1>"] }] });
	out.print_empty = bp.print_document({ columns: ["A", "B"], rows: [], empty: "Nothing <here>" });
}

// ---------------------------------------------------------------- a page under node
function page(source, names, route) {
	const calls = { set_route: [], call: [], drop: [], alerts: [] };
	const frappe = {
		pages: { "project-planner": {}, "maintenance-planner": {} },
		utils: { escape_html: (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;") },
		boot: { sysdefaults: { first_day_of_the_week: "Sunday" } },
		datetime: { get_today: () => "2026-10-09" },
		route: route,
		get_route: () => frappe.route,
		route_flags: {},
		set_route: (...args) => calls.set_route.push({ args, replace: !!(frappe.route_flags && frappe.route_flags.replace_route) }),
		call: (opts) => { calls.call.push(opts); return Promise.resolve({ message: {} }); },
		show_alert: (msg) => calls.alerts.push(msg),
	};
	const ctx = kit_context({ frappe, window: {}, document: { getElementById: () => null } });
	vm.runInContext(source + `\nthis.Cls = ${names.cls}; this.CONST = ${names.constant};`, ctx);
	return { ctx, calls, frappe, Cls: ctx.Cls, kit: ctx.kit };
}

function pp_instance(env) {
	const p = Object.create(env.Cls.prototype);
	p.p6a = { kit: env.kit };
	p.p6c = { color_by: "default", highlight: null, moving: null, tap: false, views: [], last: null,
		loaded: true, loading: null, waiting: false, restored: false, saved_text: null, save_timer: null, drawer_tool: null };
	p.view = "week"; p.anchor = "2026-10-09"; p.project = ""; p.pm = ""; p.group = ""; p.show_foreign = true; p.panel_open = true;
	p.over_only = false; p.click_blocked_until = 0; p.modified = {}; p.task_days = {}; p.needs_crew = new Set();
	p.handled = 0; p.handle_route = function () { this.handled += 1; };
	return p;
}

{
	const env = page(input.pp, { cls: "ProjectPlanner", constant: "PP6C" }, ["project-planner"]);
	const out_pp = {};
	// A bare route and a saved crew view: the route is replaced, once, with the filters applied.
	let p = pp_instance(env);
	p.p6c.last = { view: "crew", group: "Field", color_by: "job_type", evil: "x", over_only: true };
	env.frappe.route_flags = {};
	out_pp.bare_taken = p.p6c_before_route();
	out_pp.bare_calls = env.calls.set_route.splice(0);
	out_pp.bare_state = [p.group, p.p6c.color_by, p.p6c.restored, p.p6c.saved_text, p.over_only];
	// The URL names a view: used as it is (no route change), filters still restored.
	env.frappe.route = ["project-planner", "week", "2026-10-12"];
	p = pp_instance(env);
	p.p6c.last = { view: "crew", group: "PM" };
	env.frappe.route_flags = {};
	out_pp.named_taken = p.p6c_before_route();
	out_pp.named_calls = env.calls.set_route.splice(0);
	out_pp.named_group = p.group;
	// A later bare route (the sidebar link) takes the saved view again, but the filters only once.
	env.frappe.route = ["project-planner"];
	p.group = "Design";
	env.frappe.route_flags = {};
	out_pp.again_taken = p.p6c_before_route();
	out_pp.again_calls = env.calls.set_route.splice(0);
	out_pp.again_group = p.group;
	// The default view needs no route change.
	p = pp_instance(env);
	p.p6c.last = { view: "week" };
	env.frappe.route_flags = {};
	out_pp.default_taken = p.p6c_before_route();
	out_pp.default_calls = env.calls.set_route.splice(0);
	// Not loaded yet: it waits, asks once, and runs handle_route when the answer is in.
	p = pp_instance(env);
	p.p6c.loaded = false;
	p.p6c.loading = Promise.resolve().then(() => { p.p6c.loaded = true; });
	out_pp.wait_taken = [p.p6c_before_route(), p.p6c_before_route()];
	// Another page's route: not its business.
	env.frappe.route = ["Form", "Task", "TASK-1"];
	p = pp_instance(env);
	p.p6c.last = { view: "crew" };
	out_pp.other_taken = p.p6c_before_route();
	env.frappe.route = ["project-planner"];

	// The state saved: last used never holds Running over; a named view does.
	p = pp_instance(env);
	p.view = "crew"; p.group = "Field"; p.over_only = true; p.p6c.color_by = "pm";
	out_pp.state_last = p.p6c_state(true);
	out_pp.state_named = p.p6c_state(false);
	p.view = "route";
	out_pp.state_route = p.p6c_state(true);

	// The strip sums only the people the group filter shows.
	p = pp_instance(env);
	p.data = input.pp_data;
	p.by_project = {}; (p.data.projects || []).forEach((row) => (p.by_project[row.name] = row));
	p.by_task = {}; p.data.tasks.forEach((card) => (p.by_task[card.name] = card));
	p.by_resource = {}; p.data.resources.forEach((row) => (p.by_resource[row.name] = row));
	p.range_days = () => ["2026-10-11", "2026-10-12"];
	p.title = () => "Oct 11 – 17, 2026";
	out_pp.team_all = p.p6c_team_sum("2026-10-12");
	p.group = "PM";
	out_pp.team_pm = p.p6c_team_sum("2026-10-12");
	out_pp.strip = p.p6c_strip_cells().map((cell) => [cell.ymd, cell.weekday, cell.text, cell.tone, cell.tip.split("\n")[0]]);
	p.group = "";

	// Colors per mode.
	const colors = {};
	for (const mode of ["default", "person", "job_type", "project", "pm", "status"]) {
		p.p6c.color_by = mode;
		colors[mode] = p.data.tasks.map((card) => p.p6c_card_color(card, null));
	}
	p.p6c.color_by = "person";
	colors.person_row = p.p6c_card_color(p.data.tasks[0], "RES-2");
	out_pp.colors = colors;
	p.p6c.color_by = "project";
	out_pp.legend = p.p6c_legend_entries();

	// Print: the selection only, escaped; the week as it is on screen.
	p.p6c.color_by = "job_type";
	p.p6_selection = new Set(["TASK-2"]);
	out_pp.print_selection = p.p6c_print_model(true);
	out_pp.print_selection_html = env.kit.big_picture.print_document(out_pp.print_selection);
	p.p6c.highlight = { key: "PRJ-1", label: "Riverwalk (PRJ-1)" };
	out_pp.print_week = p.p6c_print_model(false);
	out_pp.print_week_html = env.kit.big_picture.print_document(out_pp.print_week);
	p.p6_selection = undefined;
	out_pp.no_selection = p.p6c_selection().size;

	// Tap to move: the drop is the drag's own.
	p = pp_instance(env);
	p.$body = undefined;
	const dropped = [];
	p.drop = (source, target) => dropped.push([source.card.name, target.date, target.resource || null]);
	p.p6c_render_movebar = () => {};
	p.p6c_mark_moving = () => {};
	p.p6c_arm({ kind: "card", card: { name: "TASK-9", subject: "Dig" }, from_date: "2026-10-12", from_resource: null });
	out_pp.armed = p.p6c.moving && [p.p6c.moving.label, p.p6c.moving.task, p.p6c.moving.date];
	p.p6c_tap_drop({ date: "2026-10-14", resource: "RES-1" });
	out_pp.tap = { dropped, moving: p.p6c.moving };
	out.pp = out_pp;
}

{
	const env = page(input.mp, { cls: "MaintenancePlanner", constant: "MP6C" }, ["maintenance-planner"]);
	const out_mp = {};
	let m = pp_instance(env);
	m.view = "month";
	m.p6c.last = { view: "month", technician: "austin@example.com", projected: false };
	env.frappe.route_flags = {};
	out_mp.default_taken = m.p6c_before_route();
	out_mp.default_calls = env.calls.set_route.splice(0);
	out_mp.default_state = [m.technician, m.show_projected];
	m = pp_instance(env);
	m.p6c.last = { view: "crew" };
	env.frappe.route_flags = {};
	out_mp.crew_taken = m.p6c_before_route();
	out_mp.crew_calls = env.calls.set_route.splice(0);

	m = pp_instance(env);
	m.data = input.mp_data;
	m.view = "week";
	m.technician = "";
	m.show_projected = true; m.show_project_work = true;
	m.by_key = {}; m.tech_names = {}; m.tech_by_user = {};
	(m.data.technicians || []).forEach((row) => { m.tech_names[row.user] = row.name; m.tech_by_user[row.user] = row; });
	m.all_cards = () => m.data.visits.slice();
	m.range_days = () => ["2026-10-11", "2026-10-12"];
	m.title = () => "Oct 11 – 17, 2026";
	out_mp.team_all = m.p6c_team_sum("2026-10-12");
	m.technician = "austin@example.com";
	out_mp.team_one = m.p6c_team_sum("2026-10-12");
	out_mp.team_label = m.p6c_team_label();
	m.view = "crew";
	out_mp.team_crew = m.p6c_team().length;
	m.view = "week"; m.technician = "";
	const colors = {};
	for (const mode of ["default", "technician", "site", "status", "contract"]) {
		m.p6c.color_by = mode;
		colors[mode] = m.data.visits.map((card) => m.p6c_card_color(card, null));
	}
	out_mp.colors = colors;
	m.p6c.color_by = "default";
	m.p6_selection = new Set(["REC-2"]);
	out_mp.print_selection = m.p6c_print_model(true);
	out_mp.print_selection_html = env.kit.big_picture.print_document(out_mp.print_selection);
	out.mp = out_mp;
}

setTimeout(() => process.stdout.write(JSON.stringify(out)), 20);
"""


def _fnv(key, size):
	value = 0x811C9DC5
	data = str(key).encode("utf-16-le")
	for index in range(0, len(data), 2):
		value ^= data[index] | (data[index + 1] << 8)
		value = (value * 0x01000193) & 0xFFFFFFFF
	return value % size


HASH_KEYS = [
	"PRJ-00612",
	"PRJ-00001",
	"",
	"Lisa Tessier",
	"Riverwalk Plaza — Phase 2",
	"😀 emoji",
	"SMC-2026-0001",
]
CREW_CELLS = (
	[{"capacity": 8, "booked": 8, "free": 0, "soft_booked": 0, "off": None} for _ in range(8)]
	+ [{"capacity": 8, "booked": 6, "free": 2, "soft_booked": 4, "off": None}]
	+ [{"capacity": 8, "booked": 0, "free": 8, "soft_booked": 0, "off": None} for _ in range(6)]
	+ [
		{"capacity": 0, "booked": 0, "free": 0, "off": "Time off"},
		{"capacity": 0, "booked": 0, "free": 0, "off": "Holiday: Columbus Day"},
		{"capacity": 0, "booked": 0, "free": 0, "off": "Unavailable"},
		{"capacity": 0, "booked": 0, "free": 0, "off": "Not a work day"},
		{"capacity": 4, "booked": 0, "free": 4, "off": "Half day off"},
	]
)


def _pp_data():
	def cell(capacity, booked, soft=0, off=None):
		return {
			"capacity": capacity,
			"booked": booked,
			"free": max(capacity - booked, 0),
			"soft_booked": soft,
			"off": off,
		}

	return {
		"today": "2026-10-09",
		"can_edit": True,
		"resources": [
			{"name": "RES-1", "label": "Austin Healey", "group": "Field", "color": "#16a34a"},
			{"name": "RES-2", "label": "Korben Dallas", "group": "Field", "color": None},
			{"name": "RES-3", "label": "Lisa Tessier", "group": "PM", "color": "#2563eb"},
		],
		"days": {
			"RES-1": {"2026-10-11": cell(0, 0, off="Not a work day"), "2026-10-12": cell(8, 6, soft=2)},
			"RES-2": {"2026-10-11": cell(0, 0, off="Not a work day"), "2026-10-12": cell(8, 9)},
			"RES-3": {"2026-10-11": cell(0, 0, off="Not a work day"), "2026-10-12": cell(8, 0, off=None)},
		},
		"projects": [
			{"name": "PRJ-1", "title": "Riverwalk <Plaza>", "pm": "Lisa Tessier"},
			{"name": "PRJ-2", "title": "Hotel Fountain", "pm": None},
		],
		"tasks": [
			{
				"name": "TASK-1",
				"subject": "Dig <trench>",
				"project": "PRJ-1",
				"project_title": "Riverwalk <Plaza>",
				"project_type": "Build",
				"status": "Open",
				"start": "2026-10-12",
				"end": "2026-10-13",
				"expected_time": 6,
				"crew": [{"resource": "RES-1", "label": "Austin Healey", "is_lead": True}],
				"color": None,
				"overdue": False,
			},
			{
				"name": "TASK-2",
				"subject": 'Set pump & "vault"',
				"project": "PRJ-2",
				"project_title": "Hotel Fountain",
				"project_type": "Rent",
				"status": "Working",
				"start": "2026-10-12",
				"end": "2026-10-12",
				"expected_time": 0,
				"crew": [],
				"color": "#123456",
				"overdue": True,
				"tentative": True,
			},
		],
		"unscheduled": [],
		"foreign": [],
	}


def _mp_data():
	def cell(capacity, booked, soft=0, off=None):
		return {
			"capacity": capacity,
			"booked": booked,
			"free": max(capacity - booked, 0),
			"soft_booked": soft,
			"off": off,
		}

	return {
		"today": "2026-10-09",
		"technicians": [
			{
				"user": "austin@example.com",
				"name": "Austin Healey",
				"enabled": True,
				"resource": "RES-1",
				"color": "#16a34a",
			},
			{
				"user": "jesse@example.com",
				"name": "Jesse Pinkman",
				"enabled": True,
				"resource": "RES-4",
				"color": None,
			},
			{
				"user": "old@example.com",
				"name": "Old Tech",
				"enabled": False,
				"resource": "RES-9",
				"color": None,
			},
			{
				"user": "nores@example.com",
				"name": "No Resource",
				"enabled": True,
				"resource": None,
				"color": None,
			},
		],
		"bookings": {
			"austin@example.com": {"2026-10-12": cell(8, 2, soft=1)},
			"jesse@example.com": {"2026-10-12": cell(8, 8)},
			"old@example.com": {"2026-10-12": cell(8, 0)},
		},
		"visits": [
			{
				"kind": "visit",
				"key": "REC-1",
				"name": "REC-1",
				"date": "2026-10-12",
				"project": "PRJ-M1",
				"contract": "SMC-1",
				"customer": "CUST-1",
				"site": "Hotel <Lobby>",
				"site_full": "Hotel <Lobby> Fountain",
				"technician": "austin@example.com",
				"status": "draft",
				"movable": True,
				"overdue": False,
				"crew": [],
			},
			{
				"kind": "projected",
				"key": "proj|SMC-2|2026-10-12",
				"name": "proj",
				"date": "2026-10-12",
				"project": "PRJ-M2",
				"contract": "SMC-2",
				"site": "Plaza",
				"technician": None,
				"movable": True,
				"crew": [],
			},
			{
				"kind": "visit",
				"key": "REC-2",
				"name": "REC-2",
				"date": "2026-10-13",
				"project": "PRJ-M1",
				"contract": "SMC-1",
				"site": "Hotel <Lobby>",
				"site_full": "Hotel <Lobby> Fountain",
				"technician": "jesse@example.com",
				"status": "done",
				"movable": False,
				"overdue": False,
				"crew": [{"user": "austin@example.com", "name": "Austin Healey", "hours": None}],
				"flagged": True,
			},
		],
	}


class TestUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		if not node:
			raise unittest.SkipTest("node is not on PATH")
		cls.node = node
		payload = {
			"kit": _module_source(KIT_DIR / "escape.js") + "\n" + _module_source(BIG_PICTURE),
			"pp": PP_JS.read_text(encoding="utf-8"),
			"mp": MP_JS.read_text(encoding="utf-8"),
			"hash_keys": HASH_KEYS,
			"crew_cells": CREW_CELLS,
			"pp_data": _pp_data(),
			"mp_data": _mp_data(),
		}
		result = subprocess.run(
			[node, "-e", HARNESS],
			input=json.dumps(payload),
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
		if result.returncode != 0:
			raise AssertionError(result.stderr)
		cls.out = json.loads(result.stdout)

	def test_the_javascript_parses(self):
		for path in (BIG_PICTURE, BUNDLE, PP_JS, MP_JS):
			result = subprocess.run(
				[self.node, "--check", str(path)], capture_output=True, text=True, timeout=60
			)
			self.assertEqual(result.returncode, 0, f"{path.name}: {result.stderr}")

	# ------------------------------------------------------------------ colors

	def test_the_hash_is_stable_and_matches_its_python_twin(self):
		palette = self.out["palette"]
		self.assertEqual(len(palette), 12)
		self.assertEqual(len(set(palette)), 12)
		for key, index, color in self.out["hash"]:
			self.assertEqual(index, _fnv(key, 12), key)
			self.assertEqual(color, palette[index], key)
		# Pinned outright, so a change to the arithmetic cannot pass by changing both sides.
		self.assertEqual(_fnv("PRJ-00612", 12), 11)

	def test_job_types_and_statuses(self):
		self.assertEqual(
			self.out["job"],
			["Design", "Build", "Service", "Events", "Events", "Delivery", "Other", "Other", "Events"],
		)
		self.assertEqual(
			self.out["status"], ["Open", "Working", "Pending Review", "Overdue", "Overdue", "Other"]
		)
		self.assertEqual(
			self.out["visit"], ["Next visit", "Projected", "Done", "Pending review", "Overdue", "Scheduled"]
		)
		self.assertEqual(self.out["paint"], ["#12ab34", "hsl(120,55%,42%)", "#000", ""])

	def test_each_mode_colors_from_the_card(self):
		colors = self.out["pp"]["colors"]
		self.assertEqual(colors["default"], [None, None])
		# Person: the lead's own color; nobody on it is gray; in the crew view, the row's person.
		self.assertEqual(colors["person"][0], "#16a34a")
		self.assertEqual(colors["person"][1], "#94a3b8")
		self.assertTrue(colors["person_row"].startswith("hsl("))
		self.assertEqual(colors["job_type"], ["#ea580c", "#db2777"])  # Build, Rent -> Events
		palette = self.out["palette"]
		self.assertEqual(colors["project"], [palette[_fnv("PRJ-1", 12)], palette[_fnv("PRJ-2", 12)]])
		self.assertEqual(colors["pm"], [palette[_fnv("Lisa Tessier", 12)], "#94a3b8"])
		self.assertEqual(colors["status"], ["#2563eb", "#dc2626"])  # Open; Working but overdue
		legend = self.out["pp"]["legend"]
		self.assertEqual([entry["key"] for entry in legend], ["PRJ-2", "PRJ-1"])
		self.assertEqual(legend[1]["hi"], "PRJ-1")
		mp = self.out["mp"]["colors"]
		self.assertEqual(mp["default"], [None, None, None])
		self.assertEqual(mp["technician"][0], "#16a34a")
		self.assertEqual(mp["technician"][1], "#94a3b8")  # a projected visit with no technician yet
		self.assertEqual(mp["site"][0], mp["site"][2])  # the same site, the same color
		self.assertEqual(mp["site"][0], palette[_fnv("PRJ-M1", 12)])
		self.assertEqual(mp["status"], ["#2563eb", "#4f46e5", "#16a34a"])
		self.assertEqual(mp["contract"][0], palette[_fnv("SMC-1", 12)])

	# ------------------------------------------------------------------ the strip

	def test_the_team_day_thresholds(self):
		self.assertEqual(self.out["levels"], ["amber", "green", "amber", "red", "off", "red"])
		mixed = self.out["mixed"]
		self.assertEqual((mixed["capacity"], mixed["booked"], mixed["free"]), (16, 14, 6))
		self.assertEqual((mixed["level"], mixed["over_people"]), ("green", 1))

	def test_the_tooltip_reads_like_the_spec(self):
		cells = CREW_CELLS
		capacity = sum(c["capacity"] for c in cells)
		free = sum(c["free"] for c in cells)
		booked = sum(c["booked"] for c in cells)
		self.assertEqual(self.out["crew_sum"]["off"], 3)  # time off, a holiday, a block; not a pattern day
		self.assertEqual(self.out["crew_sum"]["half"], 1)
		self.assertEqual(self.out["crew_sum"]["soft"], 4)
		lines = self.out["crew_tip"].split("\n")
		self.assertEqual(lines[0], f"Field crew Thu: {free:g}h free of {capacity:g}h · 3 people off")
		self.assertEqual(lines[1], f"{booked:g}h booked of {capacity:g}h ({round(booked / capacity * 100)}%)")
		self.assertIn("+4h pencil (tentative, not counted as booked)", lines)
		self.assertIn("1 person on a half day off", lines)
		self.assertNotIn("Columbus", self.out["crew_tip"])
		self.assertNotIn("Time off", self.out["crew_tip"])
		self.assertEqual(self.out["crew_text"], f"{free:g}h free")
		self.assertEqual(self.out["empty_tip"], "Team Sun: nobody to show")

	def test_the_strip_sums_only_the_filtered_team(self):
		pp = self.out["pp"]
		self.assertEqual(
			(pp["team_all"]["people"], pp["team_all"]["capacity"], pp["team_all"]["booked"]), (3, 24, 15)
		)
		self.assertEqual(pp["team_all"]["soft"], 2)
		self.assertEqual(
			(pp["team_pm"]["people"], pp["team_pm"]["capacity"], pp["team_pm"]["booked"]), (1, 8, 0)
		)
		sunday, monday = pp["strip"]
		self.assertEqual(sunday[1:4], ["Sun", "Off", "pp-offday"])
		self.assertEqual(monday[2:4], ["8h free", "pp-green"])
		self.assertEqual(monday[4], "PM crew Mon: 8h free of 8h")
		mp = self.out["mp"]
		# Enabled technicians with a Planner Resource; one when the filter picks one; all in the crew view.
		self.assertEqual(
			(mp["team_all"]["people"], mp["team_all"]["capacity"], mp["team_all"]["booked"]), (2, 16, 10)
		)
		self.assertEqual(mp["team_all"]["soft"], 1)
		self.assertEqual(mp["team_one"]["people"], 1)
		self.assertEqual(mp["team_label"], "Austin Healey")
		self.assertEqual(mp["team_crew"], 2)

	# ------------------------------------------------------------------ saved views and the route

	def test_a_bare_route_is_replaced_by_the_saved_view_once(self):
		pp = self.out["pp"]
		self.assertIs(pp["bare_taken"], True)
		self.assertEqual(
			pp["bare_calls"], [{"args": ["project-planner", "crew", "2026-10-09"], "replace": True}]
		)
		group, color, restored, saved, over_only = pp["bare_state"]
		self.assertEqual((group, color, restored), ("Field", "job_type", True))
		self.assertNotIn("evil", saved)
		# Running over is never restored from "last used", even from a row that holds it.
		self.assertIs(over_only, False)
		self.assertNotIn("over_only", saved)

	def test_the_route_wins(self):
		pp = self.out["pp"]
		self.assertIs(pp["named_taken"], False)
		self.assertEqual(pp["named_calls"], [])
		self.assertEqual(pp["named_group"], "PM")
		# A later bare route takes the view again (replacing), and leaves the live filters alone.
		self.assertIs(pp["again_taken"], True)
		self.assertEqual(
			pp["again_calls"], [{"args": ["project-planner", "crew", "2026-10-09"], "replace": True}]
		)
		self.assertEqual(pp["again_group"], "Design")
		self.assertIs(pp["default_taken"], False)
		self.assertEqual(pp["default_calls"], [])
		self.assertEqual(pp["wait_taken"], [True, True])
		self.assertIs(pp["other_taken"], False)
		mp = self.out["mp"]
		self.assertIs(mp["default_taken"], False)
		self.assertEqual(mp["default_calls"], [])
		self.assertEqual(mp["default_state"], ["austin@example.com", False])
		self.assertIs(mp["crew_taken"], True)
		self.assertEqual(
			mp["crew_calls"], [{"args": ["maintenance-planner", "crew", "2026-10-09"], "replace": True}]
		)

	def test_the_state_a_page_saves(self):
		pp = self.out["pp"]
		self.assertEqual(
			pp["state_last"],
			{
				"view": "crew",
				"project": "",
				"pm": "",
				"group": "Field",
				"foreign": True,
				"panel": True,
				"color_by": "pm",
			},
		)
		self.assertIs(pp["state_named"]["over_only"], True)
		self.assertNotIn("view", pp["state_route"])
		self.assertEqual(
			self.out["picked"], {"view": "crew", "color_by": "job_type", "foreign": False, "project": "A B"}
		)

	# ------------------------------------------------------------------ tap to move

	def test_a_tap_drops_through_the_drags_path(self):
		pp = self.out["pp"]
		self.assertEqual(pp["armed"], ["Dig", "TASK-9", "2026-10-12"])
		self.assertEqual(pp["tap"]["dropped"], [["TASK-9", "2026-10-14", "RES-1"]])
		self.assertIsNone(pp["tap"]["moving"])

	# ------------------------------------------------------------------ print

	def test_print_escapes_everything_people_typed(self):
		html = self.out["print_plain"]
		for raw in (
			"<img src=x",
			"<1>",
			"<i>",
			"<x>",
			"<11>",
			"Over <b>",
			"Build <b>",
			"<n>",
			"javascript:",
			"url(x)",
		):
			self.assertNotIn(raw, html, raw)
		for escaped in (
			"Dig &lt;img src=x onerror=1&gt;",
			"Riverwalk &quot;quoted&quot;",
			"A&amp;B &lt;i&gt;",
			"Over &lt;b&gt;",
		):
			self.assertIn(escaped, html, escaped)
		self.assertIn("@page { size: letter landscape;", html)
		self.assertIn('class="pkp-legend"', html)
		self.assertIn("border-left-color:#64748b", html)  # the bad color fell back
		chrome = self.out["print_chrome"]
		self.assertIn("<div id='chrome-open'>", chrome)
		self.assertIn("<div id='chrome-close'>", chrome)
		self.assertLess(chrome.index("chrome-open"), chrome.index("<table"))
		self.assertIn('style="color:#000"', chrome)
		self.assertIn("border-left:4px solid #2563eb;padding:1px", chrome)
		self.assertIn("Nothing &lt;here&gt;", self.out["print_empty"])

	def test_print_selection_only_prints_the_selection(self):
		pp = self.out["pp"]
		model = pp["print_selection"]
		self.assertEqual(model["layout"], "list")
		self.assertEqual([row["cols"][1] for row in model["rows"]], ['Set pump & "vault"'])
		self.assertEqual(model["lines"][0], "Selected tasks")
		self.assertIn("Selected tasks only (1)", model["lines"])
		self.assertEqual(model["rows"][0]["color"], "#db2777")  # Job type: Rent -> Events
		html = pp["print_selection_html"]
		self.assertIn("Set pump &amp; &quot;vault&quot;", html)
		self.assertNotIn("Dig &lt;trench&gt;", html)
		self.assertEqual(pp["no_selection"], 0)
		mp = self.out["mp"]
		self.assertEqual([row["cols"][0] for row in mp["print_selection"]["rows"]], ["Tue, Oct 13"])
		self.assertIn("Hotel &lt;Lobby&gt; Fountain", mp["print_selection_html"])
		self.assertIn("Chemistry", mp["print_selection"]["rows"][0]["cols"][4])

	def test_print_the_view_on_screen(self):
		model = self.out["pp"]["print_week"]
		self.assertEqual(model["heading"], "Week · Oct 11 – 17, 2026")
		self.assertEqual(model["columns"], ["Sun", "Mon"])
		sunday, monday = model["rows"][0]["cells"]
		self.assertEqual(sunday["items"], [])
		self.assertEqual(sunday["sub"], "Off")
		titles = [item["title"] for item in monday["items"]]
		self.assertEqual(titles, ['Set pump & "vault"', "Dig <trench>"])
		dig = monday["items"][1]
		self.assertIn("Day 1 of 2", dig["marks"])
		self.assertTrue(dig["highlight"])
		pump = monday["items"][0]
		self.assertTrue(pump["faded"])  # not the highlighted project
		self.assertIn("Pencil", pump["marks"])
		self.assertIn({"text": "Overdue", "tone": "red"}, pump["marks"])
		self.assertIn("Highlighting Riverwalk (PRJ-1)", model["lines"])
		self.assertIn("Colored by Job type", model["lines"])
		self.assertEqual([entry["label"] for entry in model["legend"]][:2], ["Design", "Build"])
		html = self.out["pp"]["print_week_html"]
		self.assertIn("Dig &lt;trench&gt;", html)
		self.assertIn("Riverwalk &lt;Plaza&gt;", html)


if __name__ == "__main__":
	unittest.main()
