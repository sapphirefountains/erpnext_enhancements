"""The planners' filters in the address, and the planner kit's stylesheet linked once (custom pages
S5c, v1.676.0, ERPNext TASK-2026-02807).

* Project Planner: ``?project=``, ``?pm=`` and ``?group=`` on every planner screen.
* Maintenance Planner: ``?technician=`` (``__none__`` for Unassigned).

The rules each page states in its "filters in the address" block:

* Read on every landing from ``frappe.route_options`` and consumed key by key, after the "last
  used" view is restored, so the address wins over it. An address naming any filter is the whole
  filter (one it does not name is All); one naming none keeps the filters on screen.
* What the address gave is that visit's only: not remembered, and the "last used" view is not
  saved while it is on screen. A bare address after it brings back the person's own. A pick in the
  bar makes what the bar shows theirs, remembered as before (S5b's rule for a form's project link,
  made general).
* A pick rewrites the entry on screen in place (``replaceState``); a filter is not a screen.
  Weeks, months, views and a person's route move by ``set_route``, carrying the filters.

And the fix: v16.50's ``frappe.require`` marks a file loaded only once it has finished loading,
so Phase 6A's and Phase 6D's requires, both from the constructor, each appended a ``<link>`` for
``planner_kit.bundle.css``. Each page now asks once, through ``p6a_require``, and both planners
share one request per Desk.

The behaviour runs in ``scripts/test_planner_url_filters.js`` against the real page scripts, a
model of v16.50's router and of its ``assets.js``. This pins the shape that harness relies on.
Bench-free: reads files and runs node, no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_planner_url_filters
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
HARNESS = REPO / "scripts" / "test_planner_url_filters.js"
CI = REPO / ".github" / "workflows" / "ci.yml"

PROJECT_PLANNER = APP / "project_enhancements/page/project_planner/project_planner.js"
MAINTENANCE_PLANNER = APP / "sapphire_maintenance/page/maintenance_planner/maintenance_planner.js"
PAGES = {
	"project": {"path": PROJECT_PLANNER, "const": "PP", "kit": "PP6A", "keys": '["project", "pm", "group", "all_tasks"]'},
	"maintenance": {"path": MAINTENANCE_PLANNER, "const": "MP", "kit": "MP6A", "keys": '["technician"]'},
}


def _code(path):
	"""The script with comments stripped: an assertion must not be satisfied by its own prose."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _method(code, header):
	start = code.index(header)
	return code[start : code.index("\n\t}", start) + 3]


class TestTheRead(unittest.TestCase):
	def test_read_after_the_last_used_view_and_consumed(self):
		for name, page in PAGES.items():
			code = _code(page["path"])
			with self.subTest(page=name):
				self.assertIn(f"url_filters: {page['keys']},", code)
				handle = _method(code, "\thandle_route() {")
				self.assertEqual(handle.split("\n")[1].strip(), "if (this.p6c_before_route()) return;")
				self.assertLess(
					handle.index("if (!target) return;"), handle.index("this.take_route_filters();")
				)
				self.assertLess(
					handle.index("this.take_route_filters();"), handle.index("this.view = target.view;")
				)
				take = _method(code, "\ttake_route_filters() {")
				self.assertIn("Object.prototype.hasOwnProperty.call(opts, key)", take)
				self.assertIn("delete opts[key];", take)
				self.assertIn("if (!Object.keys(opts).length) frappe.route_options = null;", take)
				# What the address gave is marked, unless it is what is already on screen.
				self.assertIn("else this.url_filters = Object.assign({}, next);", take)
				# The planner's own next screen is no bare address, even with every filter at All.
				self.assertIn("} else if (this.url_filters && !self_step) {", take)
				# Back onto the person's own filters makes them theirs again.
				self.assertIn(
					"this.url_filters = null;", take.split("const own = this.own_filter_values();")[1]
				)
				# Nothing remembered by a read, and the address is brought up to the screen.
				self.assertNotIn("save_pref", take)
				self.assertIn("this.write_filter_url();", take)

	def test_a_value_the_filter_cannot_take_is_ignored(self):
		for name, page in PAGES.items():
			code = _code(page["path"])
			with self.subTest(page=name):
				check = _method(code, "\tfilter_from_address(key, value) {")
				self.assertIn('if (value == null || typeof value === "object") return null;', check)
				self.assertIn(f"{page['const']}.url_value_max", check)
		check = _method(_code(PROJECT_PLANNER), "\tfilter_from_address(key, value) {")
		self.assertIn('if (key === "group" && text && !PP.groups.includes(text)) return null;', check)


class TestStoredPrefs(unittest.TestCase):
	def test_only_a_pick_is_remembered(self):
		for name, page in PAGES.items():
			code = _code(page["path"])
			with self.subTest(page=name):
				pick = _method(code, "\tpick_filter(")
				# Only the key picked becomes the person's; the address's others stay one-off (S5b).
				self.assertIn("this.own_filters = Object.assign(this.own_filter_values(), {", pick)
				self.assertIn("this.release_filter(", pick)
				self.assertEqual(pick.count("this.save_pref("), 1)
				self.assertNotIn("forEach((name) => this.save_pref", pick)
				self.assertIn("this.write_filter_url();", pick)
				# "Last used" keeps the person's own value for every key the address still holds.
				save_last = _method(code, "\tp6c_save_last() {")
				self.assertIn("Object.keys(this.url_filters || {}).forEach((key) => {", save_last)
				self.assertIn("state[key] = this.own_filter_values()[key];", save_last)
				self.assertNotIn("if (this.url_filters) return;", save_last)
				self.assertIn("this.self_route = [", _method(code, "\tgo(view, anchor) {"))
				# A value the loaded planner resets to All stops being the link's.
				fill = "\tfill_filters() {" if name == "project" else "\tfill_technicians() {"
				self.assertIn("this.release_filter(", _method(code, fill))
				apply = _method(code, "\tp6c_apply_state(state, opts) {")
				self.assertIn("this.url_filters = null;", apply)
				self.assertIn("this.own_filters = this.filters();", apply)
		pp = _code(PROJECT_PLANNER)
		for key in ("project", "pm", "group"):
			self.assertIn(f'(value) => this.pick_filter("{key}", value)', pp)
		self.assertIn('this.pick_filter("project", project);', _method(pp, "\tp6a_filter_project(project) {"))
		mp = _code(MAINTENANCE_PLANNER)
		self.assertIn('.on("change", () => this.pick_filter(this.$tech.val() || ""))', mp)
		# The bar's handlers save nothing of their own any more.
		self.assertEqual(pp.count("this.save_pref(PP.prefs.project"), 0)
		self.assertEqual(mp.count("this.save_pref(MP.tech_key"), 1)


class TestTheAddress(unittest.TestCase):
	def test_the_one_history_write_is_in_place_and_guarded(self):
		for name, page in PAGES.items():
			code = _code(page["path"])
			const = page["const"]
			with self.subTest(page=name):
				self.assertNotIn("pushState", code)
				self.assertEqual(code.count("history.replaceState("), 1)
				write = _method(code, "\twrite_filter_url() {")
				self.assertIn(
					'window.history.replaceState(window.history.state, "", path + search + window.location.hash);',
					write,
				)
				# Only on the planner's own path, never over a filter the page has not read yet, and
				# nothing written when the address already says it.
				self.assertIn(
					f'if ((frappe.get_route() || [])[0] !== {const}.route || !path.split("/").includes({const}.route)) return;',
					write,
				)
				self.assertIn(
					f"if (opts && {const}.url_filters.some((key) => Object.prototype.hasOwnProperty.call(opts, key))) return;",
					write,
				)
				self.assertIn("if (search === window.location.search) return;", write)
				# Encoded as v16.50's set_route encodes a value.
				# Encoded as v16.50's set_route encodes a value, an apostrophe as the browser reads it back.
				self.assertIn('const enc = (text) => encodeURIComponent(text).replace(/\'/g, "%27");', write)
				self.assertIn("enc(filters[key])", write)
				watch = _method(code, "\twatch_filter_url() {")
				self.assertIn(
					'window.addEventListener("popstate", () => setTimeout(() => this.write_filter_url(), 0));',
					watch,
				)
				self.assertIn("this.watch_filter_url();", _method(code, "\tconstructor(page) {"))

	def test_every_planner_screen_carries_the_filters(self):
		pp = _code(PROJECT_PLANNER)
		mp = _code(MAINTENANCE_PLANNER)
		for line in (
			# P5: a view's address is ppl_route_parts' (/lookahead/…, /calendar/week|month/…).
			"frappe.set_route(PP.route, ...parts, this.filter_options())",
			"frappe.set_route(PP.route, PP.route_view, resource, ymd, this.filter_options())",
			"frappe.set_route(PP.route, PP3B.my_week, ymd, this.filter_options())",
		):
			self.assertIn(line, pp)
		self.assertIn("frappe.set_route(MP.route, view, anchor, this.filter_options())", mp)
		# The data's resets (a PM or technician not on the list) reach the address after a load.
		self.assertIn("this.fill_filters();\n\t\t\t\tthis.write_filter_url();", pp)
		self.assertIn("this.write_filter_url();", _method(mp, "\tload() {"))


class TestTheKitLoadsOnce(unittest.TestCase):
	def test_one_require_behind_p6a_require(self):
		for name, page in PAGES.items():
			code = _code(page["path"])
			kit = page["kit"]
			with self.subTest(page=name):
				self.assertEqual(code.count("frappe.require("), 1)
				require = _method(code, "\tp6a_require(callback) {")
				self.assertIn(
					f"if (!window.planner_kit_request) window.planner_kit_request = Promise.resolve(frappe.require({kit}.kit));",
					require,
				)
				self.assertIn("return window.planner_kit_request.then(callback);", require)
				self.assertIn(
					"this.p6a_require(() => this.p6a_ready());", _method(code, "\tinit_phase6a() {")
				)
				self.assertIn(
					"this.p6a_require(() => this.p6d_ready());", _method(code, "\tinit_phase6d() {")
				)
				self.assertIn(
					f'const {kit} = {{\n\tkit: ["planner_kit.bundle.js", "planner_kit.bundle.css"],', code
				)

	def test_the_harness_models_the_opportunity_boards_panel_list(self):
		"""The board's side panel (v1.675.0) requires the kit's two files itself; the harness runs its
		list beside the planners', so it must be the board's own."""
		board = _code(APP / "crm_enhancements/page/opportunity_board/opportunity_board.js")
		listed = re.search(r"const OB_PANEL_ASSETS = \[([^\]]*)\];", board)
		self.assertIsNotNone(listed)
		names = re.findall(r'"([^"]+)"', listed.group(1))
		self.assertIn("planner_kit.bundle.css", names)
		harness = HARNESS.read_text(encoding="utf-8")
		self.assertIn(f"const OB_PANEL_ASSETS = {json.dumps(names, separators=(', ', ': '))};", harness)


class TestWiring(unittest.TestCase):
	def test_the_suite_has_its_own_ci_step(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("run: python -m unittest erpnext_enhancements.tests.test_planner_url_filters -v", ci)


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheBehaviourHarness(unittest.TestCase):
	def test_the_harness_passes(self):
		result = subprocess.run(
			[shutil.which("node"), str(HARNESS)],
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=300,
		)
		output = (result.stdout + result.stderr)[-6000:]
		self.assertEqual(result.returncode, 0, output)
		self.assertIn("checks passed", result.stdout, output)

	def test_the_scripts_parse(self):
		for path in (PROJECT_PLANNER, MAINTENANCE_PLANNER, HARNESS):
			with self.subTest(path=path.name):
				result = subprocess.run(
					[shutil.which("node"), "--check", str(path)], capture_output=True, text=True
				)
				self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
	unittest.main()
