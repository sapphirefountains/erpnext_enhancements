# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""P6 of the Planner UX redesign: the Project Planner's Gantt tab, page side, and the embed hooks it
added to the app's Gantt widget (``docs/planner-ux-redesign.md`` §4.3).

The behaviour runs in ``scripts/test_planner_gantt.js``: the real ``gantt_widget.js`` on a recording
DHTMLX stub, and the real page script. This pins the wiring around them, because each of these fails
quietly:

* **No second write path.** A drag reaches the page's ``commit()`` through the widget's ``on_write``,
  so ``update_gantt_row`` (which knows none of the planner's rules) is never called from the planner,
  and a link goes through ``send()``. The block calls no write endpoint of its own.
* **The baseline does not rely on ``addTaskLayer``**, which the Standard build deletes after mixing it
  in (checked in a browser harness on 2026-10-21). It is a type renderer.
* **The hooks into the class are one line each**, so P7 (which merges after this) and the other
  parallel PRs editing ``project_planner.js`` meet this block only at those lines.
* **A project is a screen**: ``/gantt/<project>`` is a route, so Back and Forward step through the
  projects opened, and the Project form's "Open in Project Planner" lands on it.

Bench-free: reads files and runs node, no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_planner_gantt_page
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
HARNESS = REPO / "scripts" / "test_planner_gantt.js"
CI = REPO / ".github" / "workflows" / "ci.yml"
PP_JS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.js"
WIDGET_JS = APP / "public" / "js" / "gantt_widget" / "gantt_widget.js"
WIDGET_CSS = APP / "public" / "css" / "gantt_widget.bundle.css"
LINK_JS = APP / "public" / "js" / "project_enhancements" / "project_planner_link.js"


def _code(path):
	"""The script with comments stripped: an assertion must not be satisfied by its own prose."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
	return "\n".join(re.sub(r"(^|\s)//.*$", r"\1", line) for line in src.splitlines())


def _block(code):
	"""The PPG block alone: from its constants to its methods' Object.assign."""
	start = code.index("const PPG = {")
	end = code.index("Object.assign(ProjectPlanner.prototype, PPG_METHODS);", start)
	return code[start:end]


def _method(code, signature):
	"""A class method's body (it ends at a bare closing brace)."""
	start = code.index(signature)
	end = code.index("\n\t}\n", start)
	return code[start:end]


class TestTheBlock(unittest.TestCase):
	def setUp(self):
		self.code = _code(PP_JS)
		self.block = _block(self.code)

	def test_every_drag_goes_through_commit_and_every_link_through_send(self):
		self.assertNotIn("update_gantt_row", self.block)
		for write in ("save_task\",\n", "move_many", "shift_successors", "swap_crew"):
			self.assertNotIn(write, self.block, write)
		self.assertIn('this.commit(card, "save_task", Object.assign({ task: card.name }, args), message, { solo })', self.block)
		self.assertIn("on_write: (task, changes, info) => this.ppg_write(task, changes, info),", self.block)
		self.assertIn('this.send(\n\t\t\t"add_dependency",', self.block)
		self.assertIn('this.send(\n\t\t\t\t"remove_dependency",', self.block)
		# The only frappe.call is the read.
		self.assertEqual(self.block.count("frappe.call("), 1)
		self.assertIn("frappe.call({ method: PPG.method, args })", self.block)

	def test_publish_and_reset_baseline_are_p4s_own(self):
		self.assertIn("this.as4_confirm_publish({", self.block)
		self.assertIn("this.as4_reset_baseline(state.project)", self.block)

	def test_the_hooks_into_the_class_are_one_line_each(self):
		code = self.code
		self.assertIn("const gantt = this.ppg_route_target(route);", _method(code, "\troute_target() {"))
		self.assertIn("this.ppg_take_target(target);", _method(code, "\thandle_route() {"))
		self.assertIn("if (view === PPG.view) return this.ppg_open();", _method(code, "\tgo(view, anchor) {"))
		self.assertIn("if (this.view === PPG.view) return this.ppg_load();", _method(code, "\tload() {"))
		self.assertIn("if (this.view === PPG.view) return this.ppg_render();", _method(code, "\trender() {"))
		self.assertIn("if (this.view === PPG.view) return this.ppg_title();", _method(code, "\ttitle() {"))
		self.assertIn("if (this.view === PPG.view) return this.ppg_shift(sign);", _method(code, "\tshift(sign) {"))
		self.assertIn("if (this.ppg_undo(snap)) return;", _method(code, "\tundo() {"))
		self.assertIn('[PPG.view, __("Gantt")],', code)
		self.assertIn("if (view === PPG.view) return this.ppg_route_parts();", code)
		self.assertIn("if (view === PPG.view) return Object.assign(model, this.ppg_print());", code)
		self.assertIn("if (this.view === PPG.view) return [this.tb_all_tasks_item()];", code)

	def test_the_tab_sits_after_the_lookahead(self):
		lookahead = self.code.index('[PPL.view, __("Lookahead")],')
		gantt = self.code.index('[PPG.view, __("Gantt")],')
		crew = self.code.index('["crew", __("Crew")],')
		self.assertLess(lookahead, gantt)
		self.assertLess(gantt, crew)

	def test_a_zoom_and_a_collapse_are_not_screens(self):
		zoom = self.block[self.block.index("\tppg_zoom(zoom) {") :]
		zoom = zoom[: zoom.index("\n\t},\n")]
		self.assertNotIn("set_route", zoom)
		self.assertNotIn("this.go(", zoom)

	def test_the_chart_is_taken_down_off_its_tab(self):
		take = self.block[self.block.index("\tppg_take_target(target) {") :]
		take = take[: take.index("\n\t},\n")]
		self.assertIn("this.ppg_destroy();", take)


class TestTheWidget(unittest.TestCase):
	def setUp(self):
		self.code = _code(WIDGET_JS)

	def test_the_hooks_are_in_and_the_old_path_stays_the_default(self):
		self.assertIn("this.config.fetch", self.code)
		self.assertIn("this.config.on_write", self.code)
		self.assertIn('method: "erpnext_enhancements.api.gantt.update_gantt_row"', self.code)
		self.assertIn('method: "erpnext_enhancements.api.gantt.get_gantt_data"', self.code)
		self.assertIn("g.config.drag_links = !!(edit.links && this.config.on_link);", self.code)

	def test_the_baseline_is_a_type_renderer_not_add_task_layer(self):
		self.assertNotRegex(self.code, r"addTaskLayer\s*\(")
		self.assertIn("g.config.type_renderers[g.config.types.task] =", self.code)
		self.assertIn(".ee-gantt-baseline", WIDGET_CSS.read_text(encoding="utf-8"))

	def test_the_dhtmlx_globals_shim_is_untouched(self):
		self.assertIn("frappe.dom.eval(src);", self.code)
		self.assertIn("window.Gantt = prev_upper;", self.code)


class TestTheFormLink(unittest.TestCase):
	def test_open_in_project_planner_lands_on_the_gantt(self):
		self.assertIn(
			'frappe.set_route("project-planner", "gantt", frm.doc.name, { project: frm.doc.name })', _code(LINK_JS)
		)


class TestWiring(unittest.TestCase):
	def test_the_suite_has_its_own_ci_step(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("run: python -m unittest erpnext_enhancements.tests.test_planner_gantt_page -v", ci)


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
