# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""P5 of the Planner UX redesign: the Project Planner's Lookahead tab, page side
(``docs/planner-ux-redesign.md`` §4.2; Nik's D2, 2026-10-21: the screen a PM lands on).

The behaviour runs in ``scripts/test_planner_lookahead.js`` against the real page script (the board,
its bars and chips, the filters, the phone's list, print, the call ``load()`` makes and the drop
target), and its addresses, the old ``/week`` and ``/month`` redirects and Back/Forward in
``scripts/test_planner_url_filters.js`` against the model of v16.50's router. This pins the wiring
around them, because each of these fails quietly:

* **No second write path.** A bar is the planner's own card (``.pp-card[data-task]``), so drags,
  resizes, the side panel, the right-click menu and every write go through ``commit``/``send``, and
  P4's auto-draft, tasks that follow, Ctrl/⌘ and "Notify crew?" apply unchanged. The block itself
  calls no write endpoint.
* **A tab is a route.** The address of every screen is ``ppl_route_parts``'; the old addresses are
  replaced, never pushed; a collapsed row and the Needs crew chip change nothing in the address.
* **The hooks into the class are one line each**, so the parallel PRs editing ``project_planner.js``
  (P7, P8, P11) merge without touching this block.
* **A bar lets the day under it take a drop** while anything is dragged (``pointer-events: none``),
  or a drop onto a busy row would land nowhere.

Bench-free: reads files and runs node, no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_planner_lookahead_page
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
HARNESS = REPO / "scripts" / "test_planner_lookahead.js"
URL_HARNESS = REPO / "scripts" / "test_planner_url_filters.js"
CI = REPO / ".github" / "workflows" / "ci.yml"
PP_JS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.js"
PP_CSS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.css"
PREFS = APP / "api" / "planner_views_prefs.py"


def _code(path):
	"""The script with comments stripped: an assertion must not be satisfied by its own prose."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
	return "\n".join(re.sub(r"(^|\s)//.*$", r"\1", line) for line in src.splitlines())


def _block(code):
	start = code.index("const PPL = {")
	return code[start:]


def _method(code, signature):
	"""A class method's body (it ends at a bare closing brace)."""
	start = code.index(signature)
	end = code.index("\n\t}\n", start)
	return code[start:end]


def _obj_method(code, signature):
	"""A method of a block's *_METHODS object (it ends at a closing brace and a comma)."""
	start = code.index(signature)
	end = code.index("\n\t},\n", start)
	return code[start:end]


class TestTheBlock(unittest.TestCase):
	def setUp(self):
		self.code = _code(PP_JS)
		self.block = _block(self.code)

	def test_it_writes_nothing_of_its_own(self):
		for write in ("save_task", "add_crew", "swap_crew", "move_many", "shift_successors", "frappe.call("):
			self.assertNotIn(write, self.block, write)
		# The bars are the planner's cards, so the page's own drag, resize and click take them.
		self.assertIn('const classes = ["pp-card", "ppl-bar"];', self.block)
		self.assertIn('data-task="${pp_esc(card.name)}"', self.block)
		self.assertIn('class="p6b-grip p6b-grip-l" data-p6b-edge="start"', self.block)
		# Publish… is P4's own confirmation.
		self.assertIn("this.as4_confirm_publish({", self.block)

	def test_it_reads_get_lookahead_through_load(self):
		self.assertIn('method: "erpnext_enhancements.api.planner_lookahead.get_lookahead"', self.block)
		load = _method(self.code, "\tload() {")
		self.assertIn("this.ppl_call(start) ||", load)

	def test_the_hooks_into_the_class_are_one_line_each(self):
		code = self.code
		self.assertIn("const lookahead = this.ppl_route_target(route);", _method(code, "\troute_target() {"))
		handle = _method(code, "\thandle_route() {")
		self.assertIn("if (target.redirect) return this.ppl_redirect(target.redirect);", handle)
		self.assertIn("this.ppl_take_target(target);", handle)
		self.assertIn("if (this.view === PPL.view) return this.ppl_range();", _method(code, "\trange() {"))
		go = _method(code, "\tgo(view, anchor) {")
		self.assertIn("const parts = this.ppl_route_parts(view, anchor);", go)
		self.assertIn("frappe.set_route(PP.route, ...parts, this.filter_options())", go)
		render = _method(code, "\trender() {")
		self.assertIn("if (lookahead) this.ppl_render();", render)
		self.assertIn("if (lookahead && !this.data.lookahead) return;", render)
		self.assertIn("if (this.view === PPL.view) return;", _method(code, "\trender_trays() {"))
		self.assertIn('".pp-day[data-date], .ppl-day[data-date]"', _method(code, "\tfind_target(x, y) {"))

	def test_the_tabs_and_the_calendar_switch(self):
		code = self.code
		self.assertIn('[PPL.view, __("Lookahead")],', code)
		self.assertIn('[PPL.calendar, __("Calendar")],', code)
		# Week and Month are the Calendar tab's switch, never tabs themselves.
		self.assertIn('.attr("data-ppl-cal", view)', code)
		self.assertIn("this.tb.tabs.sync(this.ppl_tab());", code)
		self.assertEqual(code.count("this.ppl_sync_tabs();"), 3)

	def test_the_home_tab_and_last_used(self):
		code = self.code
		self.assertIn('default_view: "lookahead",', code)
		self.assertIn('view: ["enum", ["week", "month", "crew", "heatmap", "lookahead"]],', code)
		self.assertIn("const view = last && last.tabbed && last.view;", code)
		self.assertIn("state.tabbed = true;", code)
		prefs = PREFS.read_text(encoding="utf-8")
		self.assertIn('"view": ("enum", ("week", "month", "crew", "heatmap", "lookahead")),', prefs)
		self.assertIn('"tabbed": ("bool",),', prefs)

	def test_a_collapse_and_the_needs_crew_chip_are_not_screens(self):
		for name in ("ppl_toggle(key) {", "ppl_set_all(collapsed) {"):
			body = _obj_method(self.block, f"\t{name}")
			self.assertNotIn("set_route", body)
			self.assertNotIn("this.go(", body)
		self.assertIn("this.go(PPL.view, this.anchor);", _obj_method(self.block, "\tppl_pick_weeks(weeks) {"))

	def test_a_bar_lets_the_day_under_it_take_a_drop(self):
		css = PP_CSS.read_text(encoding="utf-8")
		self.assertRegex(
			css, r"body\.pp-drag-active \.ppl-bar,\s*body\.p6b-resizing \.ppl-bar \{\s*pointer-events: none;"
		)

	def test_the_routing_scenarios_live_in_the_router_harness(self):
		harness = URL_HARNESS.read_text(encoding="utf-8")
		for name in (
			"P5: an old /week address is replaced by /calendar/week",
			"P5: the range is a screen: Back and Forward step through the weeks shown and the days",
			"a last-used view saved before the tabs opens the Lookahead",
		):
			self.assertIn(name, harness)


class TestWiring(unittest.TestCase):
	def test_the_suite_has_its_own_ci_step(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("run: python -m unittest erpnext_enhancements.tests.test_planner_lookahead_page -v", ci)


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
		result = subprocess.run([shutil.which("node"), "--check", str(HARNESS)], capture_output=True, text=True)
		self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
	unittest.main()
