"""P12 of the planner UX redesign: the first-visit tour and "?" for the new tabs.

Nik, 2026-10-21: "When we are close to finishing, I'd like a brief and simple tutorial on the planner
for every first time visitor." ``docs/planner-ux-redesign.md`` section 7, P12: "?" sections for the new
tabs, first-run tips, README sections for both modules, the doc marked as built.

Three halves, none needing a bench:

* **The flags** (``api/planner_views_prefs.py``) against a frappe stub: whether a person has been shown
  each planner's tour (a desktop's and a phone's are separate) lives in their own ``__UserSettings`` row
  for that planner, beside the saved views and without touching them; written straight to the table (never
  frappe's Redis-only user_settings, which a deploy flushes), POST only, behind the planner's own role
  gate, the Opportunity Board refused, and a reply that is not a real ``true`` reading as not seen.
* **The wiring**: ``planner_kit/tour.js`` in the bundle with no history in it, the stylesheet (tokens
  only, a phone card along the bottom), the one-line hooks in both pages, the help's footer button, the
  pages' storage helpers still the only browser storage, the READMEs, the spec marked as built, CI.
* **The behaviour**, under node (``scripts/test_planner_tour.js``): the real kit module and the real page
  scripts. First visit shows it, the second does not, Skip leaves it seen, Take the tour shows it again,
  a phone and a desktop each get their own, the Overview step is for the Executive Team only, no history
  entry, and nothing throws before the data and the toolbar exist.

Run: python -m unittest erpnext_enhancements.tests.test_planner_tour -v
"""

import ast
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

APP = REPO_ROOT / "erpnext_enhancements"
KIT = APP / "public" / "js" / "planner_kit"
BUNDLE = APP / "public" / "js" / "planner_kit.bundle.js"
KIT_CSS = APP / "public" / "css" / "planner_kit.bundle.css"
PP_JS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.js"
MP_JS = APP / "sapphire_maintenance" / "page" / "maintenance_planner" / "maintenance_planner.js"
PREFS_PY = APP / "api" / "planner_views_prefs.py"
HARNESS = REPO_ROOT / "scripts" / "test_planner_tour.js"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
SPEC = REPO_ROOT / "docs" / "planner-ux-redesign.md"
PE_README = APP / "project_enhancements" / "README.md"
SM_README = APP / "sapphire_maintenance" / "README.md"
API_README = APP / "api" / "README.md"
PREFS = "erpnext_enhancements.api.planner_views_prefs"
USER = "nik@example.com"


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	"""The script with comments stripped: an assertion must not be satisfied by its own prose."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(re.sub(r"(^|\s)//.*$", r"\1", line) for line in src.splitlines())


def _block(source, marker):
	"""A page's P12 block: from its banner to the end of the file."""
	return source[source.index(marker) :]


PP_MARKER = "// ====================================================================== P12: the first-visit tour"
MP_MARKER = PP_MARKER


def _literal(name):
	"""A module-level constant of planner_views_prefs.py, read without importing it (it imports frappe)."""
	for node in ast.parse(_text(PREFS_PY)).body:
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
			return ast.literal_eval(node.value)
	raise AssertionError(f"no {name}")


# ---------------------------------------------------------------------------------- the flags

_saved_modules = {}
_modules_before = set()
_STUBBED = ("frappe", "erpnext_enhancements.api")


class _Throw(Exception):
	pass


class _PermissionError(Exception):
	pass


class _Deadlock(Exception):
	"""frappe.QueryDeadlockError: the database picked this request as a deadlock victim."""


class _FakeTable:
	"""``__UserSettings`` as a dict, with the two statements the module runs."""

	def __init__(self):
		self.rows = {}
		self.writes = []
		# How many of the next writes the database refuses as a deadlock victim.
		self.deadlocks = 0

	def sql(self, query, values=None, **kwargs):
		if "from `__UserSettings`" in query:
			text = self.rows.get((values[0], values[1]))
			return [(text,)] if text is not None else []
		return []

	def multisql(self, sql_dict, values=(), **kwargs):
		if self.deadlocks:
			self.deadlocks -= 1
			raise _Deadlock("Deadlock found when trying to get lock")
		self.writes.append((sql_dict, values))
		user, doctype, text, again = values
		assert text == again
		self.rows[(user, doctype)] = text


STATE = {}


def _throw(message, exc=None, title=None):
	raise (exc or _Throw)(message)


def _gate(name):
	def require():
		STATE["gates"].append(name)
		if STATE.get("deny"):
			raise _PermissionError(name)

	return require


def _install():
	global _modules_before
	_modules_before = set(sys.modules)
	for name in list(sys.modules):
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements.api")):
			_saved_modules[name] = sys.modules.pop(name)
	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.now_datetime = lambda: "2026-10-22 09:00:00"
	utils.format_datetime = lambda *a, **k: "Oct 22, 2026"
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	frappe.throw = _throw
	frappe.PermissionError = _PermissionError
	# The Opportunity Board's gate, a read on Opportunity (it has no tour, but it shares get_views).
	frappe.has_permission = lambda *a, **k: True
	frappe.QueryDeadlockError = _Deadlock
	sys.modules.update({"frappe": frappe, "frappe.utils": utils})
	# The two planners' own role gates, which the module imports lazily.
	for name in ("project_planner", "maintenance_planner"):
		stub = types.ModuleType(f"erpnext_enhancements.api.{name}")
		stub._require_planner = _gate(name)
		sys.modules[f"erpnext_enhancements.api.{name}"] = stub
	return frappe


def _uninstall():
	for name in set(sys.modules) - _modules_before:
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements.api")):
			sys.modules.pop(name, None)
	sys.modules.update(_saved_modules)
	_saved_modules.clear()


class TestTheTourFlags(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.frappe = _install()
		cls.prefs = importlib.import_module(PREFS)

	@classmethod
	def tearDownClass(cls):
		_uninstall()

	def setUp(self):
		f = self.frappe
		STATE.clear()
		STATE["gates"] = []
		f.table = _FakeTable()
		f.dropped = []
		f.session = types.SimpleNamespace(user=USER)
		f.cache = types.SimpleNamespace(hdel=lambda name, key: f.dropped.append((name, key)))
		f.rollbacks = []
		f.db = types.SimpleNamespace(
			sql=f.table.sql, multisql=f.table.multisql, rollback=lambda: f.rollbacks.append(1)
		)

	def _row(self, planner="Project Planner", user=USER):
		text = self.frappe.table.rows.get((user, planner))
		return json.loads(text) if text else None

	def test_a_person_never_shown_a_tour_reads_as_unseen(self):
		answer = self.prefs.get_views("project")
		self.assertEqual(answer["tour"], {"desktop": False, "phone": False})
		self.assertEqual(self.prefs.get_views("maintenance")["tour"], {"desktop": False, "phone": False})
		# Reading writes nothing.
		self.assertEqual(self.frappe.table.writes, [])

	def test_marking_one_tour_marks_only_that_one(self):
		answer = self.prefs.mark_tour_seen("project", "desktop")
		self.assertEqual(answer, {"planner": "project", "tour": {"desktop": True, "phone": False}})
		self.assertEqual(self.prefs.get_views("project")["tour"], {"desktop": True, "phone": False})
		self.prefs.mark_tour_seen("project", "phone")
		self.assertEqual(self.prefs.get_views("project")["tour"], {"desktop": True, "phone": True})

	def test_each_planner_has_its_own_row_and_flags(self):
		self.prefs.mark_tour_seen("project", "desktop")
		self.assertEqual(self.prefs.get_views("maintenance")["tour"], {"desktop": False, "phone": False})
		self.prefs.mark_tour_seen("maintenance", "phone")
		self.assertEqual(self._row("Maintenance Planner")["planner_tour"]["seen"], {"desktop": False, "phone": True})
		self.assertEqual(self._row("Project Planner")["planner_tour"]["seen"], {"desktop": True, "phone": False})
		self.assertEqual(self._row("Project Planner")["planner_tour"]["version"], 1)

	def test_written_straight_to_the_table_once_and_the_cached_copy_dropped(self):
		self.prefs.mark_tour_seen("project", "desktop")
		self.assertEqual(len(self.frappe.table.writes), 1)
		self.assertIn(("_user_settings", f"Project Planner::{USER}"), self.frappe.dropped)
		self.prefs.mark_tour_seen("project", "desktop")
		self.assertEqual(len(self.frappe.table.writes), 1, "seen once is seen: nothing more to write")

	def test_the_saved_views_and_the_rest_of_the_row_are_kept(self):
		self.prefs.save_view("project", "Field crew", {"view": "crew", "group": "Field"})
		self.prefs.save_last_view("project", {"view": "week"})
		self.frappe.table.rows[(USER, "Project Planner")] = json.dumps(
			dict(self._row(), something_else={"a": 1})
		)
		self.prefs.mark_tour_seen("project", "desktop")
		row = self._row()
		self.assertEqual(row["something_else"], {"a": 1})
		self.assertEqual([v["name"] for v in row["planner_views"]["views"]], ["Field crew"])
		self.assertEqual(row["planner_views"]["last"], {"view": "week"})
		self.assertEqual(row["planner_tour"]["seen"], {"desktop": True, "phone": False})
		# And the other way: saving a view afterwards keeps the flag.
		self.prefs.save_view("project", "Another", {"view": "month"})
		self.prefs.save_last_view("project", {"view": "crew"})
		self.prefs.delete_view("project", "Another")
		self.assertEqual(self._row()["planner_tour"]["seen"], {"desktop": True, "phone": False})
		self.assertEqual(self.prefs.get_views("project")["tour"], {"desktop": True, "phone": False})
		self.assertEqual(self.prefs.get_views("project")["last"], {"view": "crew"})

	def test_only_the_callers_own_row(self):
		self.prefs.mark_tour_seen("project", "desktop")
		self.frappe.session.user = "lisa@example.com"
		self.assertEqual(self.prefs.get_views("project")["tour"], {"desktop": False, "phone": False})
		self.prefs.mark_tour_seen("project", "phone")
		self.assertEqual(self._row("Project Planner", USER)["planner_tour"]["seen"], {"desktop": True, "phone": False})
		self.assertEqual(
			self._row("Project Planner", "lisa@example.com")["planner_tour"]["seen"],
			{"desktop": False, "phone": True},
		)

	def test_a_bad_kind_a_bad_planner_and_the_board_are_refused(self):
		for planner, kind in (
			("project", "tablet"),
			("project", ""),
			("project", None),
			("project", {"desktop": True}),
			("payroll", "desktop"),
			("board", "desktop"),
		):
			with self.subTest(planner=planner, kind=kind):
				with self.assertRaises(_Throw):
					self.prefs.mark_tour_seen(planner, kind)
		self.assertEqual(self.frappe.table.writes, [])
		# The board has no tour to report either.
		self.assertEqual(self.prefs.get_views("board")["tour"], {})

	def test_each_planners_own_role_gate_answers_first(self):
		self.prefs.mark_tour_seen("project", "desktop")
		self.prefs.mark_tour_seen("maintenance", "desktop")
		self.assertEqual(STATE["gates"], ["project_planner", "maintenance_planner"])
		STATE["deny"] = True
		for planner in ("project", "maintenance"):
			with self.assertRaises(_PermissionError):
				self.prefs.mark_tour_seen(planner, "phone")
			with self.assertRaises(_PermissionError):
				self.prefs.get_views(planner)
		self.assertEqual(len(self.frappe.table.writes), 2, "a refused request writes nothing")

	def test_a_deadlock_on_the_first_write_of_a_row_is_retried_once(self):
		"""Two requests making a person's brand-new row at once can pick one as a deadlock victim under
		REPEATABLE READ (the tour's mark and the "last used" view). The mark starts again, once."""
		self.frappe.table.deadlocks = 1
		answer = self.prefs.mark_tour_seen("project", "desktop")
		self.assertEqual(answer["tour"], {"desktop": True, "phone": False})
		self.assertEqual(self.frappe.rollbacks, [1], "rolled back before the second try")
		self.assertEqual(len(self.frappe.table.writes), 1, "and written once")
		self.assertEqual(self._row()["planner_tour"]["seen"], {"desktop": True, "phone": False})

	def test_a_second_deadlock_is_the_pages_to_handle_and_other_errors_are_not_retried(self):
		self.frappe.table.deadlocks = 2
		with self.assertRaises(_Deadlock):
			self.prefs.mark_tour_seen("project", "desktop")
		self.assertEqual(self.frappe.rollbacks, [1], "one retry, not a loop")
		self.assertEqual(self.frappe.table.writes, [])
		# A refusal is not a deadlock: no rollback, no second try.
		self.frappe.rollbacks.clear()
		with self.assertRaises(_Throw):
			self.prefs.mark_tour_seen("project", "tablet")
		self.assertEqual(self.frappe.rollbacks, [])
		# The already-seen path writes nothing, so it has nothing to deadlock on.
		self.prefs.mark_tour_seen("project", "phone")
		self.frappe.table.deadlocks = 1
		self.prefs.mark_tour_seen("project", "phone")
		self.assertEqual(self.frappe.table.deadlocks, 1)
		self.assertEqual(self.frappe.rollbacks, [])

	def test_only_a_real_true_counts_as_seen(self):
		damaged = {
			"planner_tour": {"version": 1, "seen": {"desktop": "yes", "phone": 1}},
			"planner_views": {"views": []},
		}
		self.frappe.table.rows[(USER, "Project Planner")] = json.dumps(damaged)
		self.assertEqual(self.prefs.get_views("project")["tour"], {"desktop": False, "phone": False})
		self.frappe.table.rows[(USER, "Project Planner")] = json.dumps({"planner_tour": "yes"})
		self.assertEqual(self.prefs.get_views("project")["tour"], {"desktop": False, "phone": False})
		self.frappe.table.rows[(USER, "Project Planner")] = "{not json"
		self.assertEqual(self.prefs.get_views("project")["tour"], {"desktop": False, "phone": False})
		self.prefs.mark_tour_seen("project", "desktop")
		self.assertEqual(self.prefs.get_views("project")["tour"], {"desktop": True, "phone": False})

	def test_the_endpoint_is_post_only_and_the_read_stays_get(self):
		tree = ast.parse(_text(PREFS_PY))
		functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
		decorators = [ast.unparse(d) for d in functions["mark_tour_seen"].decorator_list]
		self.assertEqual(decorators, ["frappe.whitelist(methods=['POST'])"])
		self.assertEqual([ast.unparse(d) for d in functions["get_views"].decorator_list], ["frappe.whitelist()"])
		params = [a.arg for a in functions["mark_tour_seen"].args.args]
		self.assertEqual(params, ["planner", "kind"])
		# Never frappe's cache-only user_settings.save; never the cache as the record.
		source = _text(PREFS_PY)
		names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
		names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
		self.assertNotIn("user_settings", names)
		self.assertNotIn("\n    def ", source)

	def test_the_constants_are_the_pages(self):
		kinds = list(_literal("TOUR_KINDS"))
		self.assertEqual(kinds, ["desktop", "phone"])
		self.assertEqual(list(_literal("TOUR_PLANNERS")), ["project", "maintenance"])
		self.assertEqual(_literal("TOUR_ROW_KEY"), "planner_tour")
		for path, name, planner in ((PP_JS, "PPTOUR", "project"), (MP_JS, "MPTOUR", "maintenance")):
			block = _block(_text(path), PP_MARKER)
			with self.subTest(page=name):
				self.assertIn('kinds: ["desktop", "phone"],', block)
				self.assertIn(f'planner: "{planner}",', block)
				self.assertIn('prefs: "erpnext_enhancements.api.planner_views_prefs",', block)
				self.assertIn(f"`${{{name}.prefs}}.mark_tour_seen`", block)


# ---------------------------------------------------------------------------------- the wiring


class TestTheKitModule(unittest.TestCase):
	def test_it_is_in_the_bundle_and_the_help_carries_actions(self):
		bundle = _text(BUNDLE)
		self.assertIn('import { create_tour } from "./planner_kit/tour.js";', bundle)
		self.assertIn("tour: create_tour(env),", bundle)
		self.assertIn("tour.open({title, steps, owner, on_close})", bundle)
		legend = _text(KIT / "legend.js")
		self.assertIn("actions: opts.actions || null,", legend)

	def test_it_joins_no_history_guard_and_touches_no_history(self):
		code = _code(KIT / "tour.js")
		for word in ("pushState", "replaceState", "history", "guard", "popstate", "set_route", "location", "window"):
			with self.subTest(word=word):
				self.assertNotIn(word, code)
		# The only things it reads from the host are the document and the words.
		self.assertEqual(sorted(set(re.findall(r"\benv\.(\w+)", code))), ["doc", "t", "warn"])
		# Every word is text: nothing is written as markup.
		self.assertNotIn("innerHTML", code)
		self.assertNotIn("insertAdjacentHTML", code)
		self.assertNotIn("eval(", code)

	def test_it_is_a_modal_dialog_a_screen_reader_can_read(self):
		code = _code(KIT / "tour.js")
		for needle in (
			'card.setAttribute("role", "dialog");',
			'card.setAttribute("aria-modal", "true");',
			'if (name) card.setAttribute("aria-label", name);',
			'else card.setAttribute("aria-labelledby", "pk-tour-title");',
			'card.setAttribute("aria-describedby", "pk-tour-text");',
			'live.setAttribute("aria-live", "polite");',
			'dots.setAttribute("aria-hidden", "true");',
			'overlay.addEventListener("pointerdown"',
			'overlay.addEventListener("click"',
			'doc.addEventListener("keydown", on_key, true);',
			'doc.removeEventListener("keydown", handle.on_key, true);',
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, code)
		self.assertEqual(code.count("MAX_STEPS = 8"), 1)
		# One name for the dialog: aria-labelledby wins over aria-label, so setting both left the tour's own
		# title ("Project Planner tour") unread. The card gets one or the other, never both.
		self.assertEqual(code.count('card.setAttribute("aria-label"'), 1)
		self.assertEqual(code.count('card.setAttribute("aria-labelledby"'), 1)

	def test_the_stylesheet_is_the_kits_bundle_on_the_tokens(self):
		raw = _text(KIT_CSS)
		css = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
		for selector in (
			".pk-tour",
			".pk-tour-card",
			".pk-tour-count",
			".pk-tour-title",
			".pk-tour-text",
			".pk-tour-keys",
			".pk-tour-dots",
			".pk-tour-dot",
			".pk-tour-dot-on",
			".pk-tour-actions",
			".pk-tour-spacer",
		):
			with self.subTest(selector=selector):
				self.assertRegex(css, rf"(?m)^{re.escape(selector)}\s*\{{")
		tour = css[css.index(".pk-tour {") : css.index("@media (pointer: coarse)")]
		self.assertIn("z-index: 1048;", tour)
		self.assertIn("overflow-wrap: anywhere;", tour)
		self.assertIn("width: min(460px, 100%);", tour)
		self.assertNotIn("shadow", tour)
		self.assertNotIn("animation", tour)
		coarse = re.search(r"@media \(pointer: coarse\) \{(.*?)\n\}", css, flags=re.S).group(1)
		for selector in (".pk-tour-skip", ".pk-tour-back", ".pk-tour-next"):
			self.assertIn(selector, coarse)
		phone = css[css.index("@media (max-width: 767px)") :]
		self.assertIn(".pk-tour {\n\t\talign-items: flex-end;", phone)
		self.assertIn(".pk-tour-card {\n\t\twidth: 100%;", phone)
		# The step it shows is where a screen reader hears the change.
		self.assertIn(".pk-tour-keys[hidden]", css)


class TestThePages(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.pp = _text(PP_JS)
		cls.mp = _text(MP_JS)
		cls.pp_code = _code(PP_JS)
		cls.mp_code = _code(MP_JS)

	def test_each_page_ends_with_its_tour_block(self):
		self.assertTrue(self.pp.rstrip().endswith("Object.assign(ProjectPlanner.prototype, PPTOUR_METHODS);"))
		self.assertTrue(self.mp.rstrip().endswith("Object.assign(MaintenancePlanner.prototype, MPTOUR_METHODS);"))
		self.assertEqual(self.pp.count(PP_MARKER), 1)
		self.assertEqual(self.mp.count(PP_MARKER), 1)

	def test_the_hooks_into_the_pages_are_one_line_each(self):
		for code, name in ((self.pp_code, "PP"), (self.mp_code, "MP")):
			with self.subTest(page=name):
				self.assertIn("this.init_tour();\n\t\tthis.bind_drag();", code)
				# handle_route: once the route is known, before anything else of the landing.
				route = code[code.index("\thandle_route() {") :]
				route = route[: route.index("\n\t}\n")]
				self.assertIn("if (!target) return;\n\t\tthis.tour_route();", route)
				# The saved views' answer carries the flags.
				self.assertEqual(code.count("this.tour_take(answer.tour);"), 1)
				# The page's shortcuts treat the tour as a dialog.
				self.assertIn("|| this.tour_open(),", code[code.index("\tp6b_key(e) {") :][:1200])
				self.assertIn("...this.p12_legend_sections(),", code)
				self.assertIn("actions: this.tour_legend_actions()", code)
		# Today has no toolbar, so no "?": its own.
		today = self.pp_code[self.pp_code.index("\trender_today(token) {") :]
		self.assertIn("this.tour_today_help($head);", today[: today.index("\n\t},\n")])

	def test_it_shows_after_the_data_the_toolbar_and_the_flags_are_here(self):
		block = _code(PP_JS)[_code(PP_JS).index("const PPTOUR = {") :]
		verdict = block[block.index("\ttour_verdict() {") :]
		verdict = verdict[: verdict.index("\n\t},\n")]
		for needle in (
			"if (!this.p6b_page_live()) return \"\";",
			"if (!state.seen || !kit) return \"wait\";",
			"if (!kit.tour) return \"\";",
			"if (!this.tb || !this.tb.kit) return \"wait\";",
			"const screen = PPTOUR_PURE.auto(kind, this.view, this.tour_has_day());",
			'if (screen !== "ok") return screen;',
			'if (!this.tour_screen_ready()) return "wait";',
			'if (this.tour_busy(kit)) return "wait";',
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, verdict)
		# It settles for 0.7 s after a landing and asks again each second, for a minute at most.
		self.assertIn("settle_ms: 700,", block)
		self.assertIn("retry_ms: 1000,", block)
		self.assertIn("retries: 60,", block)
		mp = _code(MP_JS)[_code(MP_JS).index("const MPTOUR = {") :]
		for needle in ("settle_ms: 700,", "retry_ms: 1000,", "retries: 60,", 'if (!this.data || this.tour_loading()) return "wait";'):
			self.assertIn(needle, mp)

	def test_it_is_marked_on_the_server_when_it_shows_and_the_browser_is_only_the_fallback(self):
		for code, name in ((self.pp_code, "PPTOUR"), (self.mp_code, "MPTOUR")):
			with self.subTest(page=name):
				block = code[code.index(f"const {name} = {{") :]
				start = block[block.index("\ttour_start(replay) {") :]
				start = start[: start.index("\n\t},\n")]
				self.assertLess(start.index("kit.tour.open("), start.index("this.tour_mark(kind)"))
				self.assertIn("if (handle && !replay) this.tour_mark(kind);", start)
				mark = block[block.index("\ttour_mark(kind) {") :]
				mark = mark[: mark.index("\n\t},\n")]
				self.assertIn("mark_tour_seen", mark)
				self.assertIn("kit.quiet_call(method, args)", mark)
				self.assertIn("frappe.call({ method, args, silent: true })", mark)
				# The browser is told only when the request fails.
				self.assertIn("const failed = () => this.save_pref(", mark)
				self.assertEqual(mark.count("save_pref"), 1)
				self.assertIn("Promise.resolve(call).then(null, failed);", mark)
		# The pages' storage helpers stay the only browser storage (test_planner_phase6c counts it).
		self.assertEqual(self.pp.count("window.localStorage"), 2)
		self.assertEqual(self.mp.count("localStorage"), 2)

	def test_every_string_the_tour_and_its_help_say_is_a_literal_in_gettext(self):
		"""A translation extractor finds __("...") with a string inside it; __(variable) is invisible to it.
		The step lists, the help's sections and the buttons write each string out at its call site."""
		for code, name in ((self.pp_code, "PPTOUR"), (self.mp_code, "MPTOUR")):
			with self.subTest(page=name):
				block = code[code.index(f"const {name} = {{") :]
				starts = re.findall(r"__\(\s*(.)", block)
				self.assertGreater(len(starts), 40)
				self.assertEqual([c for c in starts if c not in "\"'`"], [])
				# The helpers of p12_legend_sections take words that are already translated.
				self.assertIn('const item = (text) => ({ sample_html: "", text });', block)
				self.assertIn(
					'const chip = (label, text, tone) => ({ chip: { text: label, tone: tone || "plain" }, text });',
					block,
				)
				legend = block[block.index("\tp12_legend_sections() {") :]
				legend = legend[: legend.index("\n\t},\n")]
				# Every call site hands them a string wrapped in __(): item(__("...")), chip(__("..."), __("...")).
				self.assertEqual(re.findall(r"\bitem\((?!__\()", legend), [])
				self.assertEqual(re.findall(r"\bchip\((?!__\()", legend), [])
				self.assertGreaterEqual(
					len(re.findall(r"\bitem\(__\(", legend)) + len(re.findall(r"\bchip\(__\(", legend)), 8
				)

	def test_no_history_no_route_no_guard_in_either_block(self):
		for code, name in ((self.pp_code, "PPTOUR"), (self.mp_code, "MPTOUR")):
			with self.subTest(page=name):
				block = code[code.index(f"const {name} = {{") :]
				for word in ("pushState", "replaceState", "set_route", "history.", "guard.open", "p6a_route", "location"):
					self.assertNotIn(word, block)
				# Only a read of what the guard has on top, so the tour waits for it.
				self.assertEqual(block.count("kit.guard.top()"), 1)
				self.assertNotIn("kit.guard.open", block)
				self.assertNotIn("kit.guard.close", block)
		# A route or the page hiding closes it.
		self.assertEqual(self.pp_code.count('this.tour_close("route");'), 2)
		self.assertEqual(self.mp_code.count('this.tour_close("route");'), 2)

	def test_the_replay_is_in_the_help_and_does_not_mark(self):
		for code, name in ((self.pp_code, "PPTOUR"), (self.mp_code, "MPTOUR")):
			with self.subTest(page=name):
				block = code[code.index(f"const {name} = {{") :]
				replay = block[block.index("\ttour_replay() {") :]
				replay = replay[: replay.index("\n\t},\n")]
				self.assertIn("kit.drawer.navigate(open)", replay)
				self.assertIn("this.tour_start(true);", replay)
				actions = block[block.index("\ttour_legend_actions() {") :]
				self.assertIn('label: __("Take the tour"),', actions[: actions.index("\n\t},\n")])
				self.assertIn("on_click: () => this.tour_replay(),", actions)

	def test_the_overview_step_is_the_executive_teams_only(self):
		block = _code(PP_JS)[_code(PP_JS).index("const PPTOUR_PURE = {") :]
		self.assertIn("if (w.executive) {", block)
		self.assertIn("Overview is for managers", block)
		self.assertEqual(block.count("Overview is for managers"), 1)
		# The role is the landing's own constant, read from the boot, nothing fetched.
		self.assertIn("frappe.user.has_role(PPO.role)", block)
		self.assertIn('role: "Executive Team",', _code(PP_JS))

	def test_the_pages_still_load_the_kit_once_and_no_raw_asset_path_is_added(self):
		for path, name in ((PP_JS, "PPTOUR"), (MP_JS, "MPTOUR")):
			with self.subTest(page=path.name):
				code = _code(path)
				self.assertEqual(len(re.findall(r"frappe\.require\(", code)), 1)
				self.assertNotIn("/assets/", code[code.index(f"const {name} = {{") :])
		self.assertNotIn("/assets/", _code(KIT / "tour.js"))
		self.assertNotIn("/assets/", _text(KIT_CSS))
		# Loaded by the planners beside the kit, never on every Desk page.
		self.assertNotIn("planner_kit", _text(APP / "hooks.py"))


class TestTheDocs(unittest.TestCase):
	def test_the_readmes_say_what_the_tour_and_the_help_are(self):
		pe = _text(PE_README)
		self.assertIn("### The first-visit tour and the help (P12, v1.716.0, Planner UX redesign)", pe)
		section = pe[pe.index("### The first-visit tour and the help (P12") :]
		section = section[: section.index("\n## ")]
		for needle in (
			"mark_tour_seen",
			"tour_seen",
			"Take the tour",
			"Executive Team",
			"history",
			"Today",
			"planner_kit.tour",
			"scripts/test_planner_tour.js",
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, section)
		self.assertIn("`tour.open({title, steps, owner, return_focus, on_close})`", pe)
		sm = _text(SM_README)
		self.assertIn("### The first-visit tour and the help (P12, v1.716.0, Planner UX redesign)", sm)
		self.assertIn("planner_views_prefs.mark_tour_seen", sm)
		api = _text(API_README)
		self.assertIn("mark_tour_seen(planner, kind)", api)

	def test_the_spec_is_marked_as_built_row_by_row(self):
		spec = _text(SPEC)
		head = spec[: spec.index("## 1. Why")]
		self.assertIn("**Built.**", head)
		self.assertIn("P12", head)
		self.assertNotIn("Nothing is built yet.", head)
		table = spec[spec.index("## 7. Build plan") :]
		table = table[: table.index("**Merge order:**")]
		rows = [r for r in table.splitlines() if re.match(r"\| P\d+ ", r)]
		self.assertEqual(len(rows), 12)
		for row in rows:
			with self.subTest(row=row[:40]):
				self.assertRegex(row, r"\| Built in #\d+")

	def test_the_changelog_and_the_versions_agree(self):
		init = _text(APP / "__init__.py")
		version = re.search(r'__version__ = "([\d.]+)"', init).group(1)
		package = json.loads(_text(REPO_ROOT / "package.json"))["version"]
		self.assertEqual(version, package)
		self.assertEqual(version, "1.716.0")
		changelog = _text(REPO_ROOT / "CHANGELOG.md")
		self.assertIn("## [1.716.0] - 2026-10-22", changelog)
		self.assertLess(changelog.index("## [1.716.0]"), changelog.index("## [1.715.0]"))

	def test_ci_runs_the_suite_and_the_harness(self):
		ci = _text(CI)
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_planner_tour -v", ci)
		self.assertIn("scripts/test_planner_tour.js", ci)


# ---------------------------------------------------------------------------------- the behaviour


class TestTheBehaviourHarness(unittest.TestCase):
	def test_the_harness_passes(self):
		node = shutil.which("node")
		self.assertIsNotNone(node, "node is on PATH in CI; this harness cannot skip there")
		result = subprocess.run(
			[node, str(HARNESS)], capture_output=True, text=True, encoding="utf-8", timeout=300
		)
		output = (result.stdout + result.stderr)[-6000:]
		self.assertEqual(result.returncode, 0, output)
		self.assertRegex(result.stdout, r"(?m)^0 of \d+ checks failed", output)

	def test_the_scripts_parse(self):
		node = shutil.which("node")
		self.assertIsNotNone(node)
		for path in (PP_JS, MP_JS, HARNESS):
			with self.subTest(path=path.name):
				result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, encoding="utf-8")
				self.assertEqual(result.returncode, 0, result.stderr)
		result = subprocess.run(
			[node, "--check", "--input-type=module", "-"],
			input=_text(KIT / "tour.js"),
			capture_output=True,
			text=True,
			encoding="utf-8",
		)
		self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
	unittest.main()
