"""The planner toolbar redesign (v1.687.0) and toolbar v2 (P1 of the planner UX redesign).

Nik picked the first from a mockup on 2026-10-14. Every phase had appended its own buttons to the
bar, which had grown into one long flat row; v1.687.0 folded them into four menus. On 2026-10-21 he
approved the planner UX redesign ("Approve as is"), whose first PR (P1) folds those four into two for
BOTH planners, so the bar is the same structure on every screen. It is still one bar, made shorter by:

* **+ Add ▾ and More ▾**. + Add holds Quick add task…, Build out tasks… and Day note… on the Project
  Planner, Add visit… and Day note… on the Maintenance Planner. More holds four sections: **Display**
  (Color by, default Job type; the color key; Highlight a project…; Maintenance, rentals, travel),
  **Views**, **Tools** (Copy week…, Block time…, Conflicts…, Tap to move, Hold all my changes, Undo) and
  **Print**. The Move by switch left the selection bar for a field of the Move… panel; Split stays in
  the right-click menu. The view buttons are a WAI-ARIA tablist (``toolbar.tabs``);
* menu buttons from ``planner_kit/toolbar.js``: a button
  with ``aria-haspopup`` / ``aria-expanded``, a ``role="menu"`` list of ``menuitem``,
  ``menuitemcheckbox`` and ``menuitemradio`` items (``aria-checked`` and a check mark), arrow keys,
  Home/End, Esc giving focus back, a click elsewhere closing it, and **no history entry**;
* **alert chips** (Conflicts, Running over and Overdue on the Project Planner; Conflicts and Overdue
  on the Maintenance Planner), a count and a word, there only above zero, each opening what its
  button opened;
* **the draft strip** under the bar while Draft mode is on, with the existing Publish and Discard;
* **Utilization** as the Project Planner's fifth view, a route like the others;
* **tips in the "?" help**, and the color keys in Display;
* on a phone, **one compact bar** and a "Filters and tools" sheet that takes the kit's one history
  entry, so Back closes it.

The behaviour runs in ``scripts/test_planner_toolbar.js`` against the real kit module and the real
page scripts (a fake DOM and a recording jQuery; the sheet over history.js and a fake browser), and
the Utilization view's address in ``scripts/test_planner_url_filters.js``. This pins the wiring
around them. Bench-free: reads files and runs node, no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_planner_toolbar
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
HARNESS = REPO / "scripts" / "test_planner_toolbar.js"
CI = REPO / ".github" / "workflows" / "ci.yml"
KIT = APP / "public" / "js" / "planner_kit"
BUNDLE = APP / "public" / "js" / "planner_kit.bundle.js"
KIT_CSS = APP / "public" / "css" / "planner_kit.bundle.css"
PP_JS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.js"
MP_JS = APP / "sapphire_maintenance" / "page" / "maintenance_planner" / "maintenance_planner.js"


def _code(path):
	"""The script with comments stripped: an assertion must not be satisfied by its own prose."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
	return "\n".join(re.sub(r"(^|\s)//.*$", r"\1", line) for line in src.splitlines())


def _css(path):
	return re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)


def _obj_method(code, name):
	match = re.search(rf"^\t{name}\(.*?\) \{{\n(.*?)^\t\}},\n", code, flags=re.S | re.M)
	assert match, f"no method {name}"
	return match.group(1)


class TestTheKitModule(unittest.TestCase):
	def test_it_is_in_the_bundle(self):
		bundle = BUNDLE.read_text(encoding="utf-8")
		self.assertIn('import { create_toolbar } from "./planner_kit/toolbar.js";', bundle)
		self.assertIn("toolbar: create_toolbar(env),", bundle)
		# Utilization's request shows no frappe dialog: sf_kit's quiet call, carried by the kit.
		self.assertIn('import { call as sf_call } from "./sf_kit/call.js";', bundle)
		self.assertIn("Object.assign({}, opts, { quiet: true })", bundle)
		self.assertIn("kit.quiet_call(PPU.run, args)", _code(PP_JS))
		# drawer.js is another session's; this change does not touch it.
		self.assertNotIn("toolbar", (KIT / "drawer.js").read_text(encoding="utf-8"))

	def test_the_tabs_control_and_the_sections_helper_are_the_kits(self):
		code = _code(KIT / "toolbar.js")
		self.assertIn("export function sections_to_items(sections) {", code)
		tabs = code[code.index("function tabs(container, opts)") : code.index("function menu(button, opts)")]
		for needle in (
			'container.setAttribute("role", "tablist");',
			'button.setAttribute("role", "tab");',
			'button.setAttribute("aria-selected", on ? "true" : "false");',
			'["ArrowLeft", "ArrowRight", "Home", "End"]',
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, tabs)
		# Choosing a tab is a route change with its own history entry; an arrow key only moves focus.
		self.assertNotIn("click", tabs)
		self.assertNotIn("pushState", tabs)
		self.assertIn("\t\ttabs,\n\t\tsections_to_items,", code)

	def test_a_menu_pushes_no_history_and_the_sheet_takes_the_kits_one_entry(self):
		code = _code(KIT / "toolbar.js")
		self.assertNotIn("pushState", code)
		self.assertNotIn("history", code)
		# Only the sheet joins the guard (history.js), which owns the one kit entry.
		self.assertEqual(code.count("guard.open("), 1)
		sheet = code[code.index("function sheet(opts)") :]
		self.assertIn("if (guard) guard.open(overlay);", sheet)
		self.assertIn('on_escape: () => finish("user", false)', sheet)
		menu = code[code.index("function menu(button, opts)") : code.index("function sheet(opts)")]
		self.assertNotIn("guard", menu)

	def test_the_menu_is_the_aria_menu_button_pattern(self):
		code = _code(KIT / "toolbar.js")
		for needle in (
			'button.setAttribute("aria-haspopup", "menu");',
			'button.setAttribute("aria-expanded", "true");',
			'el.setAttribute("role", "menu");',
			'item.setAttribute("aria-checked", entry.checked ? "true" : "false");',
			'return "menuitemradio";',
			'return "menuitemcheckbox";',
			'e.key === "Home" || e.key === "End"',
			'close("escape");',
			'doc.addEventListener("pointerdown", current.away, true);',
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, code)
		# Its list carries pk-menu, so the planners' own shortcut handlers already leave its keys alone.
		self.assertIn('el.className = "pk-menu pk-tb-menu";', code)
		for path in (PP_JS, MP_JS):
			with self.subTest(page=path.name):
				self.assertIn('target.closest(".pk-menu")', _code(path))
				self.assertIn('e.target.closest(".pk-menu, .pk-sheet")', _code(path))

	def test_the_kit_styles_them_on_the_tokens(self):
		css = _css(KIT_CSS)
		for selector in (".pk-tb-menu", ".pk-tb-check", ".pk-sheet", ".pk-sheet-backdrop", ".pk-sheet-item"):
			with self.subTest(selector=selector):
				self.assertRegex(css, rf"(?m)^{re.escape(selector)}\s*[,{{]")
		coarse = re.search(r"@media \(pointer: coarse\) \{(.*?)\n\}", css, flags=re.S).group(1)
		self.assertIn(".pk-sheet-item", coarse)
		self.assertIn("min-height: var(--sf-touch-min);", css[css.index(".pk-sheet-close {") :])
		reduced = re.search(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", css, flags=re.S).group(
			1
		)
		self.assertIn(".pk-sheet", reduced)


class TestThePages(unittest.TestCase):
	def test_the_toolbar_block_runs_last_and_hooks_in_one_line_each(self):
		pp, mp = _code(PP_JS), _code(MP_JS)
		self.assertIn("this.init_t1();\n\t\tthis.init_toolbar();\n\t\tthis.bind_drag();", pp)
		self.assertIn("this.init_phase_c1();\n\t\tthis.init_toolbar();\n\t\tthis.bind_drag();", mp)
		for code, prefix in ((pp, "PP"), (mp, "MP")):
			with self.subTest(page=prefix):
				self.assertIn(
					f"Object.assign({'ProjectPlanner' if prefix == 'PP' else 'MaintenancePlanner'}.prototype, {prefix}TB_METHODS);",
					code,
				)
				self.assertEqual(code.count("this.tb_ready();"), 1)
				self.assertIn("this.tb_close_sheet();", _obj_method(code, "p6b_pick"))
				self.assertIn("this.tb_render();", _obj_method(code, "p6d_after_list"))

	def test_no_folded_control_is_drawn_in_the_bar_any_more(self):
		for path, gone in (
			(
				PP_JS,
				(
					"$p6c_color",
					"$p6c_tap",
					"$p6c_views",
					"$p6c_print",
					"$p6c_key",
					"$p6d_conflicts",
					"$p6d_block",
					"$draft_toggle",
					"$foreign",
					"$copy",
					"$over",
					"$util",
					"$p6a_hints",
				),
			),
			(
				MP_JS,
				(
					"$p6c_color",
					"$p6c_tap",
					"$p6c_views",
					"$p6c_print",
					"$p6c_key",
					"$p6d_conflicts",
					"$p6d_block",
					"$p6e_add",
					"$draft_toggle",
					"$p6a_hints",
					"mp-legend",
				),
			),
		):
			code = _code(path)
			for name in gone:
				with self.subTest(page=path.name, control=name):
					self.assertNotIn(name, code)
			with self.subTest(page=path.name, rule="no hint banner on load"):
				self.assertNotIn("kit.hint(", code)

	def test_the_draft_strip_is_under_the_toolbar(self):
		self.assertIn(".insertAfter(this.$toolbar);", _obj_method(_code(PP_JS), "init_phase3b"))
		self.assertIn(
			'.insertAfter(this.$body.find(".mp-toolbar").first());',
			_obj_method(_code(MP_JS), "init_phase_c1"),
		)

	def test_the_phone_bar_is_one_compact_row(self):
		for path, prefix in (
			(PP_JS.with_suffix(".css"), "pp"),
			(MP_JS.with_suffix(".css"), "mp"),
		):
			css = _css(path)
			phones = re.findall(r"@media \(max-width: 767px\) \{(.*?)\n\}", css, flags=re.S)
			hide = [block for block in phones if f".{prefix}-toolbar .{prefix}-tb-tools" in block]
			with self.subTest(page=prefix):
				self.assertEqual(len(hide), 1)
				for part in ("tb-filters", "tb-alerts", "tb-tools", "undo", "p6a-help", "refresh"):
					self.assertIn(f".{prefix}-toolbar .{prefix}-{part}", hide[0])
				self.assertIn(f".{prefix}-tb-more {{\n\t\tdisplay: inline-flex;", hide[0])
				# Off a phone, the sheet's button is not drawn.
				self.assertRegex(css, rf"(?m)^\.{prefix}-tb-more \{{\s*display: none;")


class TestToolbarV2(unittest.TestCase):
	"""P1 of the planner UX redesign: the same structure in both planners."""

	def test_both_pages_have_two_menus_and_the_same_sections(self):
		for path, prefix in ((PP_JS, "PP"), (MP_JS, "MP")):
			code = _code(path)
			with self.subTest(page=path.name):
				self.assertIn('["add", __("Add")', code)
				self.assertIn('["more", __("More")', code)
				for gone in ("tb_edit_items", '["views", __("Views")', '["print", __("Print")', '["edit", __("Edit")'):
					self.assertNotIn(gone, code)
				sections = _obj_method(code, "tb_more_sections")
				order = [m.group(1) for m in re.finditer(r'key: "(\w+)"', sections)]
				self.assertEqual(order, ["display", "views", "tools", "print"])
				self.assertIn("tb_add_items", code)
				self.assertIn("tb_tools_items", code)
				# The phone's sheet draws the same sections.
				sheet = _obj_method(code, "tb_sheet_sections")
				self.assertIn("this.tb_more_sections()", sheet)
				self.assertIn("this.tb_add_items()", sheet)

	def test_the_view_buttons_are_tabs_the_kit_wires_when_it_loads(self):
		for path in (PP_JS, MP_JS):
			code = _code(path)
			with self.subTest(page=path.name):
				self.assertIn('.attr("data-tb-tab", view)', code)
				self.assertIn("kit.toolbar.tabs(this.$seg[0]", code)
				# P5: the Project Planner's tab is ppl_tab() (week and month are the Calendar tab).
				sync = "this.tb.tabs.sync(this.ppl_tab());" if path == PP_JS else "this.tb.tabs.sync(this.view);"
				self.assertIn(sync, _obj_method(code, "tb_render"))

	def test_move_by_left_the_selection_bar_and_split_is_still_in_the_right_click_menu(self):
		for path in (PP_JS, MP_JS):
			code = _code(path)
			with self.subTest(page=path.name):
				self.assertNotIn("p6e_bar_switch", code)
				self.assertIn("this.p6e_panel_unit(panel)", code)
		self.assertIn('multi ? __("Split across days…") : __("Split in two…")', _code(PP_JS))

	def test_job_type_is_the_default_color_for_the_project_planner(self):
		code = _code(PP_JS)
		self.assertIn('default_color: "job_type",', code)
		self.assertIn("color_by: PP6C.default_color,", code)
		# A "Task color" that only the old default put in "last used" is not restored as a wish: the view
		# says whether the color was chosen (color_chosen, on the server), not a device preference.
		self.assertIn('last.color_by === "default" && !last.color_chosen', code)
		self.assertIn("this.p6c.color_chosen = true;", _obj_method(code, "p6c_set_color"))
		self.assertIn("color_chosen: !!this.p6c.color_chosen,", _obj_method(code, "p6c_state"))
		self.assertNotIn("ee_project_planner_color_chosen", code)

	def test_a_more_section_heading_is_styled_apart_from_a_sub_heading(self):
		self.assertRegex(_css(KIT_CSS), r"(?m)^\.pk-tb-section\s*\{")


class TestWiring(unittest.TestCase):
	def test_the_suite_has_its_own_ci_step(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("run: python -m unittest erpnext_enhancements.tests.test_planner_toolbar -v", ci)


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
		for path in (PP_JS, MP_JS, HARNESS, KIT / "toolbar.js"):
			with self.subTest(path=path.name):
				args = [shutil.which("node"), "--check", str(path)]
				if path.parent == KIT:
					args.insert(1, "--input-type=module")
					result = subprocess.run(
						args[:2] + ["-"],
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
