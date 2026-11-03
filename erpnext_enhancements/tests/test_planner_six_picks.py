# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Nikolas's six planner picks of 2026-11-03 (v1.784.0), page side.

The behaviour runs in ``scripts/test_planner_six_picks.js`` against the real kit modules (records.js,
popover.js) and both real page scripts: the Lookahead's month band, "Open … in new tab" and Ctrl+Enter,
the scroll kept over a redraw, the Crew board's order and its hover card, and the pencil-only hours
rule. This pins the wiring around them, each of which fails quietly:

* **The tabs sit at the bar's right end by DOM order**, after the refresh button, so the tablist, the
  phone's compact row (the "Filters and tools" button is appended after them) and the keyboard order
  all follow without a CSS ``order``.
* **Every redraw a row's arrow, the Needs crew chip, "+N more" and the Overview's Expand all cause goes
  through ``ppl_keep_scroll``**: a bare ``this.render()`` there is the bug Nikolas reported.
* **The hover card is the kit's and both pages attach it once**, from ``p6a_ready``, on the chips only;
  the ``title=`` stays on every chip as the accessible fallback.
* **The Crew board sorts through ``booking_order``** and the Calendar's foreign items too.
* **No plus sign is left** in any pencil text the pages or the kit draw.
* **The kit's one ``window.open``** is ``open_new_tab``; a tap still never opens a window.

Bench-free: reads files and runs node, no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_planner_six_picks
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
HARNESS = REPO / "scripts" / "test_planner_six_picks.js"
CI = REPO / ".github" / "workflows" / "ci.yml"
PP_JS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.js"
PP_CSS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.css"
MP_JS = APP / "sapphire_maintenance" / "page" / "maintenance_planner" / "maintenance_planner.js"
MP_CSS = APP / "sapphire_maintenance" / "page" / "maintenance_planner" / "maintenance_planner.css"
KIT = APP / "public" / "js" / "planner_kit"
BUNDLE = APP / "public" / "js" / "planner_kit.bundle.js"
KIT_CSS = APP / "public" / "css" / "planner_kit.bundle.css"


def _code(path):
	"""The script with comments stripped: an assertion must not be satisfied by its own prose."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
	return "\n".join(re.sub(r"(^|\s)//.*$", r"\1", line) for line in src.splitlines())


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


class TestTheTabs(unittest.TestCase):
	"""4. The view tabs at the toolbar's right end, by DOM order."""

	def test_the_project_planner_appends_the_tabs_after_the_refresh_button(self):
		shell = _method(_code(PP_JS), "\tbuild_shell() {")
		refresh = shell.index('class="btn btn-default btn-sm pp-refresh"')
		tabs = shell.index("$seg.appendTo($bar);")
		cal = shell.index("this.$cal_seg.appendTo($bar);")
		self.assertLess(refresh, tabs)
		self.assertLess(tabs, cal)
		# The tablist is still wired on the element, wherever it sits.
		self.assertIn("kit.toolbar.tabs(this.$seg[0]", _code(PP_JS))
		# No CSS order moves them: the phone's compact row relies on the DOM order too.
		self.assertNotRegex(
			_code(PP_CSS) if False else PP_CSS.read_text(encoding="utf-8"), r"\.pp-seg\s*\{[^}]*\border:"
		)

	def test_the_maintenance_planner_does_the_same_with_my_day_beside_them(self):
		shell = _method(_code(MP_JS), "\tbuild_shell() {")
		refresh = shell.index('class="btn btn-default btn-sm mp-refresh"')
		tabs = shell.index("$seg.appendTo($bar);")
		myday = shell.index("this.$myday.appendTo($bar);")
		self.assertLess(refresh, tabs)
		self.assertLess(tabs, myday)
		self.assertNotRegex(MP_CSS.read_text(encoding="utf-8"), r"\.mp-seg\s*\{[^}]*\border:")

	def test_the_phones_filters_and_tools_button_still_comes_last(self):
		# init_toolbar appends it to the bar after build_shell has placed the tabs.
		self.assertIn('class="btn btn-default btn-sm pp-tb-more"', _code(PP_JS))
		self.assertIn(".appendTo(this.$toolbar);", _obj_method(_code(PP_JS), "\tinit_toolbar() {"))
		self.assertIn(
			'.appendTo(this.$body.find(".mp-toolbar").first());',
			_obj_method(_code(MP_JS), "\tinit_toolbar() {"),
		)


class TestTheScrollKeeper(unittest.TestCase):
	"""3. Every redraw a row's arrow causes keeps the scroll."""

	def setUp(self):
		self.code = _code(PP_JS)

	def test_no_bare_render_is_left_on_the_lookaheads_own_clicks(self):
		for signature in ("\tppl_toggle(key) {", "\tppl_set_all(collapsed) {"):
			with self.subTest(method=signature.strip()):
				body = _obj_method(self.code, signature)
				self.assertIn("this.ppl_keep_scroll(() => this.render());", body)
				self.assertNotIn("\t\tthis.render();", body)
		bind = _obj_method(self.code, "\tppl_bind($board) {")
		self.assertEqual(bind.count("this.ppl_keep_scroll(() => this.render());"), 2)
		self.assertNotIn("\t\t\tthis.render();\n", bind)

	def test_the_overviews_expand_all_and_lines_keep_it_too(self):
		bind = _obj_method(self.code, "\tppo_bind(root) {")
		self.assertEqual(bind.count("this.ppl_keep_scroll(() => this.render());"), 2)
		self.assertNotIn("\t\t\tthis.render();\n", bind)

	def test_the_keeper_restores_in_a_finally_and_reads_the_boards_pane(self):
		keeper = _obj_method(self.code, "\tppl_keep_scroll(fn) {")
		self.assertIn("finally {", keeper)
		self.assertIn('wrap.querySelector(".pp-scroll")', _obj_method(self.code, "\tppl_scroll_save() {"))
		self.assertIn(
			"window.scrollTo(saved.win.x, saved.win.y)",
			_obj_method(self.code, "\tppl_scroll_restore(saved) {"),
		)


class TestTheMonthBand(unittest.TestCase):
	"""1. The Lookahead's month band, drawn once, sticky, above the headings."""

	def test_the_grid_draws_the_band_before_the_headings(self):
		grid = _obj_method(_code(PP_JS), "\tppl_grid_html(projects, days) {")
		self.assertLess(
			grid.index("this.ppl_monthband_html(days)"), grid.index("this.ppl_dayheads_html(days)")
		)
		self.assertEqual(_code(PP_JS).count("ppl_monthband_html(days)"), 2)

	def test_the_css_stacks_the_band_over_the_headings(self):
		css = PP_CSS.read_text(encoding="utf-8")
		band = re.search(r"\.ppl-months \{(.*?)\n\}", css, flags=re.S).group(1)
		heads = re.search(r"\.ppl-heads \{(.*?)\n\}", css, flags=re.S).group(1)
		self.assertIn("position: sticky;", band)
		self.assertIn("top: 0;", band)
		self.assertIn("grid-auto-rows: 20px;", band)
		self.assertIn("top: 20px;", heads)
		self.assertIn(".ppl-month-start {", css)
		self.assertIn(".ppl-month-first {", css)


class TestTheNewTab(unittest.TestCase):
	"""2. The kit opens the new tab; the pages only ask it to."""

	def test_the_kits_one_window_open_is_open_new_tab(self):
		records = _code(KIT / "records.js")
		self.assertEqual(records.count("win.open("), 1)
		self.assertIn(
			'env.win.open(url, "_blank", "noopener");', _method(records, "\tfunction open_new_tab(record) {")
		)
		self.assertIn("open_new_tab,", records)
		self.assertIn("url: (record) => desk_url(record, slug),", records)
		# The slug is the router's where there is one, so the address is the one a tap routes to.
		self.assertIn("router.slug(doctype)", records)

	def test_the_pages_open_no_window_of_their_own(self):
		for path in (PP_JS, MP_JS):
			with self.subTest(page=path.name):
				code = _code(path)
				self.assertNotIn('window.open("/desk', code)
				self.assertNotIn("window.open(`/desk", code)
				self.assertNotIn("frappe.router.slug(", code)
				self.assertIn("kit.records.open_new_tab(", code)

	def test_ctrl_enter_is_on_the_root_keydown_and_leaves_a_ctrl_click_alone(self):
		pp = _code(PP_JS)
		drag = _method(pp, "\tbind_drag() {")
		self.assertIn('if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {', drag)
		self.assertIn("this.ppr_new_tab_for(e.target)", drag)
		self.assertIn(
			"if (!(e.shiftKey || e.ctrlKey || e.metaKey) || e.button > 0",
			_obj_method(pp, "\tp6b_modifier_click(e) {"),
		)
		mp = _code(MP_JS)
		self.assertIn("this.mpr_open_new_tab(card)", _method(mp, "\tbind_drag() {"))

	def test_the_help_lists_the_key_on_both_planners(self):
		for path in (PP_JS, MP_JS):
			with self.subTest(page=path.name):
				self.assertIn('item(key("Ctrl") + key("Enter"), "Open the focused', _code(path))


class TestTheHoverCard(unittest.TestCase):
	"""5. The hover card is the kit's; each page attaches it once on its chips."""

	def test_the_kit_installs_it_and_the_pages_attach_it_from_p6a_ready(self):
		bundle = _code(BUNDLE)
		self.assertIn('import { create_popover } from "./planner_kit/popover.js";', bundle)
		self.assertIn("popover: create_popover(env),", bundle)
		pp = _code(PP_JS)
		self.assertIn("this.pph_ready();", _obj_method(pp, "\tp6a_ready() {"))
		self.assertIn('selector: ".pp-card[data-task], .pp-fcard",', _obj_method(pp, "\tpph_ready() {"))
		self.assertEqual(pp.count("kit.popover.attach("), 1)
		mp = _code(MP_JS)
		self.assertIn("this.mph_ready();", _obj_method(mp, "\tp6a_ready() {"))
		self.assertIn('selector: ".mp-card[data-key]"', _obj_method(mp, "\tmph_ready() {"))
		self.assertEqual(mp.count("kit.popover.attach("), 1)

	def test_the_chips_keep_their_title_as_the_fallback(self):
		pp = _code(PP_JS)
		self.assertIn(
			'tabindex="0" title="${pp_esc(', _method(pp, "\tbooking_html(resource, ymd, booking, span) {")
		)
		self.assertIn(
			'title="${pp_esc([item.label, sub].filter(Boolean).join("\\n"))}"',
			_method(pp, "\tforeign_html(item) {"),
		)
		self.assertIn(
			'tabindex="0" title="${mp_esc(tip)}"', _method(_code(MP_JS), "\tcard_row_html(card, row_user) {")
		)

	def test_the_card_is_mouse_only_and_escapes(self):
		pop = _code(KIT / "popover.js")
		self.assertIn('e.pointerType === "mouse" || e.pointerType === "pen"', pop)
		self.assertIn("escape(text(", pop)
		self.assertIn('card.setAttribute("role", "tooltip");', pop)
		self.assertIn('el.setAttribute("aria-describedby", ID);', pop)

	def test_the_kit_css_styles_it_on_the_tokens(self):
		css = KIT_CSS.read_text(encoding="utf-8")
		for selector in (".pk-pop {", ".pk-pop-title {", ".pk-pop-rows {", ".pk-pop-warnings li {"):
			with self.subTest(selector=selector):
				self.assertIn(selector, css)
		block = re.search(r"\.pk-pop \{(.*?)\n\}", css, flags=re.S).group(1)
		self.assertIn("z-index: 1046;", block)
		self.assertIn("pointer-events: none;", block)

	def test_the_crew_board_and_the_calendar_sort_through_booking_order(self):
		pp = _code(PP_JS)
		self.assertIn(".sort((a, b) => this.booking_order(a, b))", _method(pp, "\trender_crew() {"))
		self.assertIn(".sort((a, b) => this.booking_order(a, b))", _method(pp, "\trender_calendar() {"))


class TestThePencilRule(unittest.TestCase):
	"""6. No plus sign is left anywhere a pencil is said."""

	def test_no_plus_pencil_text_remains(self):
		for path in (PP_JS, MP_JS, KIT / "peeks.js"):
			with self.subTest(file=path.name):
				self.assertNotIn("+{0}h pencil", _code(path))
		self.assertIn("function pp_hours_text(free, soft, free_text) {", _code(PP_JS))
		self.assertIn("\thours_text(free, soft, free_text) {", _code(MP_JS))

	def test_the_suite_has_its_own_ci_step(self):
		self.assertIn(
			"python -m unittest erpnext_enhancements.tests.test_planner_six_picks -v",
			CI.read_text(encoding="utf-8"),
		)


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheBehaviourHarness(unittest.TestCase):
	def test_the_harness_passes(self):
		result = subprocess.run(
			[shutil.which("node"), str(HARNESS)],
			capture_output=True,
			text=True,
			timeout=180,
			encoding="utf-8",
			errors="replace",
		)
		output = (result.stdout or "") + (result.stderr or "")
		self.assertEqual(result.returncode, 0, output)
		self.assertIn("all ", output)
		self.assertIn("checks passed", output)

	def test_the_scripts_parse(self):
		for path in (KIT / "popover.js", KIT / "records.js", PP_JS, MP_JS):
			with self.subTest(script=path.name):
				args = [shutil.which("node"), "--check", str(path)]
				if path.parent == KIT:
					# ES modules: node --check wants the .mjs hint or an input type.
					result = subprocess.run(
						[shutil.which("node"), "--input-type=module", "--check"],
						input=path.read_text(encoding="utf-8"),
						capture_output=True,
						text=True,
						encoding="utf-8",
					)
				else:
					result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
				self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
	unittest.main()
