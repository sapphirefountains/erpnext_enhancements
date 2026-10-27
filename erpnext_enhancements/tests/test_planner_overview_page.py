# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""P9 of the Planner UX redesign: the Project Planner's Overview tab, page side
(``docs/planner-ux-redesign.md`` §4.7; Nik's D3, D14 and D16).

The behaviour runs in ``scripts/test_planner_overview.js`` (the real page script: the chart's rows, the
header, Needs attention, the foot, the widget it mounts, every control) and, for the landing and
Back/Forward, in ``scripts/test_planner_today.js`` (a model of v16.50's router). This pins the wiring
around them, because each of these fails quietly:

* **Nothing here writes.** The block makes two calls, the read and the printout, and neither is a
  planner write; the chart it mounts is read-only (no ``on_write``, no ``on_link``), so a drag cannot
  reach ``update_gantt_row`` or ``save_task``.
* **The page never reads a Settings Single.** A desk script that does shows "No permission for X
  Settings" to everyone who is not a System Manager. The Monday switch is the server's.
* **The hooks into the class are one line each**, so the other PRs editing ``project_planner.js`` meet
  this block only at those lines.
* **The Overview is a remembered device tab and the Executive Team's first desktop visit**, a screen
  (a route, so Back and Forward step through it), and the weekly summary is in More > Print.
* **Colours are the design tokens.** No hex value in the Overview's stylesheet, and every phase class
  the script names has a rule.

Bench-free: reads files and runs node, no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_planner_overview_page
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
HARNESS = REPO / "scripts" / "test_planner_overview.js"
TODAY_HARNESS = REPO / "scripts" / "test_planner_today.js"
CI = REPO / ".github" / "workflows" / "ci.yml"
PP_JS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.js"
PP_CSS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.css"
SETTINGS_JS = (
	APP / "project_enhancements" / "doctype" / "project_planner_settings" / "project_planner_settings.js"
)


def _code(path):
	"""The script with comments stripped: an assertion must not be satisfied by its own prose."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
	return "\n".join(re.sub(r"(^|\s)//.*$", r"\1", line) for line in src.splitlines())


def _block(code):
	"""The PPO block alone: from its constants to its methods' Object.assign."""
	start = code.index("const PPO = {")
	end = code.index("Object.assign(ProjectPlanner.prototype, PPO_METHODS);", start)
	return code[start:end]


def _method(code, signature):
	"""A class method's body (it ends at a bare closing brace)."""
	start = code.index(signature)
	end = code.index("\n\t}\n", start)
	return code[start:end]


def _object_method(block, signature):
	"""A method of an object literal in the block (it ends at `\\n\\t},\\n`)."""
	start = block.index(signature)
	return block[start : block.index("\n\t},\n", start)]


class TestTheBlock(unittest.TestCase):
	def setUp(self):
		self.code = _code(PP_JS)
		self.block = _block(self.code)

	def test_it_writes_nothing(self):
		for forbidden in (
			"update_gantt_row",
			"save_task",
			"move_many",
			"shift_successors",
			"swap_crew",
			"add_dependency",
			"remove_dependency",
			"publish_project_schedule",
			"reset_baseline",
			"this.send(",
			"this.commit(",
		):
			self.assertNotIn(forbidden, self.block, forbidden)
		# The only server calls are the read and the printout.
		self.assertEqual(self.block.count("frappe.call("), 2)
		self.assertIn("frappe.call({ method: PPO.method, args })", self.block)
		self.assertIn("frappe.call({ method: PPO.summary, args: {} })", self.block)

	def test_the_chart_is_read_only(self):
		mount = _object_method(self.block, "\tppo_mount(chart) {")
		self.assertIn("editable: {},", mount)
		for hook in ("on_write", "on_link", "on_link_dblclick", "on_task_expand"):
			self.assertNotIn(hook, mount, hook)
		# A right-click is a menu that only opens records and the job's Gantt (a tap opens the record, v1.751.0).
		self.assertIn("on_task_context: (id, task, e) => this.ppo_menu(task, e),", mount)
		self.assertIn("baseline: true,", mount)
		self.assertIn('zoom: "month",', mount)
		self.assertIn("on_task_click: (id, task) => this.ppo_click(task),", mount)
		# CSV and Excel would export Projects, which is not this chart.
		self.assertIn('formats: ["print", "png", "svg"]', mount)

	def test_the_page_reads_no_settings_single(self):
		for forbidden in (
			"get_single_value",
			"frappe.client.get",
			"frappe.db.get_value",
			"Project Planner Settings",
			"weekly_summary_email",
		):
			self.assertNotIn(forbidden, self.block, forbidden)

	def test_the_hooks_into_the_class_are_one_line_each(self):
		code = self.code
		self.assertIn("const overview = this.ppo_route_target(route);", _method(code, "\troute_target() {"))
		self.assertIn("this.ppo_take_target(target);", _method(code, "\thandle_route() {"))
		self.assertIn("if (this.view === PPO.view) return this.ppo_load();", _method(code, "\tload() {"))
		self.assertIn("if (this.view === PPO.view) return this.ppo_render();", _method(code, "\trender() {"))
		self.assertIn("if (this.view === PPO.view) return this.ppo_title();", _method(code, "\ttitle() {"))
		self.assertIn(
			"if (this.view === PPO.view) return this.ppo_shift(sign);", _method(code, "\tshift(sign) {")
		)
		self.assertIn(
			"if (view === PPO.view) return PPO_PURE.overview_address(anchor, this.ppo_state().months);", code
		)
		self.assertIn('[PPO.view, __("Overview")],', code)
		self.assertIn(".concat(this.ppo_print_items());", code)
		self.assertIn("this.ppo_first_tab(link)", _object_method(code, "\tp8_landing() {"))

	def test_the_route_target_is_checked_before_the_bare_address_redirect(self):
		target = _method(self.code, "\troute_target() {")
		self.assertLess(
			target.index("this.ppo_route_target(route)"), target.index("this.ppl_route_target(route)")
		)

	def test_the_tab_sits_after_the_calendar(self):
		calendar = self.code.index('[PPL.calendar, __("Calendar")],')
		overview = self.code.index('[PPO.view, __("Overview")],')
		self.assertLess(calendar, overview)

	def test_it_is_a_remembered_device_tab_and_never_the_servers_saved_view(self):
		self.assertIn(
			'tabs: ["week", "month", "crew", "heatmap", "capacity", "today", "lookahead", "gantt", "overview", "simple"],',
			self.code,
		)
		self.assertIn(
			'view: ["enum", ["week", "month", "crew", "heatmap", "capacity", "lookahead"]],', self.code
		)
		self.assertNotIn(
			"overview", self.code[self.code.index("schema: {") : self.code.index("view_labels:")]
		)

	def test_a_link_that_names_a_filter_never_lands_on_it(self):
		landing = _object_method(self.code, "\tp8_landing() {")
		self.assertIn("if (link && tab === PPO.view) tab = PP6C.default_view;", landing)
		first = _object_method(self.block, "\tppo_first_tab(link) {")
		self.assertIn("if (link) return PP6C.default_view;", first)
		self.assertIn("frappe.user.has_role(PPO.role)", first)
		self.assertIn('role: "Executive Team",', self.block)

	def test_the_phone_still_opens_today_first(self):
		landing = _object_method(self.code, "\tp8_landing() {")
		self.assertIn("this.p8_is_phone() ? PP8.view : this.ppo_first_tab(link)", landing)

	def test_a_month_a_window_and_a_tab_are_screens_and_a_toggle_is_not(self):
		self.assertIn("this.go(PPO.view, PPO_PURE.step(this.anchor, sign));", self.block)
		self.assertIn(
			"this.go(PPO.view, this.anchor);", _object_method(self.block, "\tppo_pick_months(months) {")
		)
		take = _object_method(self.block, "\tppo_take_target(target) {")
		self.assertIn("this.ppo_destroy();", take)
		bind = _object_method(self.block, "\tppo_bind(root) {")
		for toggle in ("data-ppo-expand", "data-ppo-lines"):
			body = bind[bind.index(toggle) : bind.index("});", bind.index(toggle))]
			self.assertNotIn("set_route", body)
			self.assertNotIn("this.go(", body)

	def test_a_row_and_a_line_open_what_they_are_about(self):
		self.assertIn("this.ppg_open(project)", _object_method(self.block, "\tppo_click(task) {"))
		line = _object_method(self.block, "\tppo_open_line(line) {")
		self.assertIn("this.ppg_open(target.project)", line)
		self.assertIn('this.go("crew", target.date || this.today())', line)

	def test_publish_is_p4s_own_confirmation(self):
		self.assertIn("this.as4_confirm_publish({", self.block)
		self.assertIn("draft_schedule: true,", self.block)

	def test_the_summary_window_opens_on_the_click_and_fills_in_later(self):
		body = _object_method(self.block, "\tppo_print_summary() {")
		self.assertLess(body.index('window.open("", "_blank")'), body.index("frappe.call("))
		self.assertIn("win.print()", body)
		self.assertIn("Allow pop-ups for this site to print the weekly summary.", body)

	def test_the_weekly_summary_is_in_more_print(self):
		print_items = _object_method(self.code, "\ttb_print_items() {")
		self.assertIn("this.ppo_print_items()", print_items)
		self.assertIn('label: __("Weekly summary"),', self.block)

	def test_markup_is_escaped_where_the_block_writes_it(self):
		"""Every interpolation into HTML in the block's own templates is pp_esc(...), a literal choice
		between fixed words, or a piece of markup this block built itself; none puts a record's field in raw."""
		# `n` is one of PPO.months (a number the block lists itself), `index` a position in a list.
		# `bit` is a line of the foot, escaped where it was made (ppo_foot_html pushes pp_esc(...) only).
		built = {
			"n",
			"index",
			"bit",
			"chips",
			"more",
			"items",
			"toggle",
			"rest",
			"note",
			"buttons",
			"names",
			"this.ppo_drafts_html()",
		}
		for line in self.block.splitlines():
			if not any(mark in line for mark in ("<", "class=", "data-")):
				continue
			for expression in re.findall(r"\$\{([^}]+)\}", line):
				ok = (
					"pp_esc(" in expression
					or expression in built
					or re.fullmatch(r'[\w.]+ \? "[^"]*" : "[^"]*"', expression)
				)
				self.assertTrue(ok, f"unescaped interpolation: ${{{expression}}} in: {line.strip()}")

	def test_the_range_of_the_chart_is_the_windows(self):
		self.assertIn("const span = PPO_PURE.clip(job.first, job.last, first, last);", self.block)
		self.assertIn("PPO_PURE.clip(phase.start, phase.end, first, last)", self.block)

	def test_a_title_a_method_name_or_a_pref_never_repeats_another_blocks(self):
		names = re.findall(r"^\t(?:async\s+)?([A-Za-z_]\w*)\s*\([^)]*\)\s*\{\s*$", self.code, re.M)
		self.assertEqual(sorted({n for n in names if names.count(n) > 1}), [])
		# Running over is the Phase 4 chip: no preference of the planner's may spell it (a guard of its own).
		prefs = " ".join(re.findall(r"prefs: \{.*?\}", self.code, flags=re.S))
		self.assertNotIn("over", prefs)
		self.assertIn("ee_project_planner_portfolio_months", prefs)


class TestTheStylesheet(unittest.TestCase):
	def setUp(self):
		css = PP_CSS.read_text(encoding="utf-8")
		start = css.index("/* P9: the Overview tab")
		self.section = re.sub(r"/\*[\s\S]*?\*/", "", css[start:])
		self.js = _block(_code(PP_JS))

	def test_colours_are_tokens_never_hex(self):
		self.assertNotRegex(self.section, r"#[0-9a-fA-F]{3,8}\b")
		self.assertNotRegex(self.section, r"\brgba?\(")

	def test_every_phase_class_the_script_names_has_a_rule(self):
		named = set(re.findall(r'"(ppo-ph-\d)"', self.js))
		self.assertEqual(named, {f"ppo-ph-{n}" for n in range(8)})
		for name in named:
			self.assertIn(f".ppo-chart .gantt_task_line.{name} {{", self.section, name)

	def test_selectors_are_the_overviews_own(self):
		rules = re.findall(r"([^{}]+)\{([^{}]*)\}", self.section)
		self.assertTrue(rules)
		for selector, _body in rules:
			for part in selector.split(","):
				self.assertTrue(part.strip().startswith(".ppo-"), part)


class TestWiring(unittest.TestCase):
	def test_the_suite_and_the_harness_are_in_ci(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("run: python -m unittest erpnext_enhancements.tests.test_planner_overview_page -v", ci)

	def test_the_settings_form_offers_the_summary_preview(self):
		js = SETTINGS_JS.read_text(encoding="utf-8")
		self.assertIn('frm.add_custom_button(__("Send me a weekly summary preview")', js)
		self.assertIn("erpnext_enhancements.api.planner_overview.send_summary_preview", js)

	def test_the_landing_scenarios_live_in_the_router_harness(self):
		harness = TODAY_HARNESS.read_text(encoding="utf-8")
		for name in (
			"landing (P9): an Executive Team member's desktop first visit opens the Overview, in place; Back leaves",
			"landing (P9): after the first visit the device's last-used tab applies to them like anyone else",
			"landing (P9): on a phone the first visit is Today, executive or not; an ordinary desktop gets the Lookahead",
			"landing (P9): a link that names a filter never opens the Overview from the landing",
			"P9: the Overview is a screen: a step, the months picker and a tab each take one entry; Back and Forward step through them",
		):
			self.assertIn(name, harness)


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

	def test_the_harness_parses(self):
		result = subprocess.run(
			[shutil.which("node"), "--check", str(HARNESS)], capture_output=True, text=True
		)
		self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
	unittest.main()
