"""The planner toolbar redesign (v1.687.0): both planners' bars folded into four menus.

Nik picked it from a mockup on 2026-10-14. Every phase had appended its own buttons to the bar,
which had grown into one long flat row. It is still one bar, made shorter by:

* **Views ▾, Display ▾, Edit ▾, Print ▾**, menu buttons from ``planner_kit/toolbar.js``: a button
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
		# drawer.js is another session's; this change does not touch it.
		self.assertNotIn("toolbar", (KIT / "drawer.js").read_text(encoding="utf-8"))

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
