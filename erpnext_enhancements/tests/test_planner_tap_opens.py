# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A tap on a planner item opens its record (Nik, 2026-10-27: "When using the Project Planner or other
planners, tapping the task should take you to the task, tapping the project should take you to the
project, and so on.").

He picked, through four questions: the record opens in the same tab, so Back returns to the planner on
the same tab, dates and filters; what a tap opened before (the quick-edit side panel, Project at a glance,
a person's week) moves to the hold / right-click menu; Tap to move is off by default on a touch screen;
Overview rows and Rental Planner fountains open their records too, and a person's name their Employee.

What this pins, and what would fail quietly otherwise:

* **One map from what a planner draws to its record** (``planner_kit/records.js``), run under node with
  every kind: a drafted new task is no Task yet, a projected visit is its contract however the page
  carries it, a person without an Employee is their Planner Resource, and a record this person cannot
  read is none, so the page keeps its old answer instead of sending them to "Not permitted".
* **Opening goes through ``drawer.navigate``**, in the same tab, so an open drawer's history entry is
  replaced rather than stacked on (Nik's Back/Forward rule), and **asks first** when the side panel holds
  edits that are not saved; a page's own ``set_value`` is never an edit.
* **Both pages and the quick looks route every tap through it**, run under node against the real page
  scripts in ``scripts/test_planner_tap_opens.js``; here, the wiring: the bundle, the payloads that carry
  what a tap needs (a projected visit's flag, a technician's Employee), Tap to move's default, the blocks'
  order, the styles and the CI step.

Bench-free: reads files and runs node; no frappe.

Run: python -m unittest erpnext_enhancements.tests.test_planner_tap_opens
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
KIT = APP / "public" / "js" / "planner_kit"
BUNDLE = APP / "public" / "js" / "planner_kit.bundle.js"
KIT_CSS = APP / "public" / "css" / "planner_kit.bundle.css"
PP_JS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.js"
MP_JS = APP / "sapphire_maintenance" / "page" / "maintenance_planner" / "maintenance_planner.js"
RP_JS = APP / "asset_management" / "page" / "rental_planner" / "rental_planner.js"
PP_API = APP / "api" / "project_planner.py"
MP_API = APP / "api" / "maintenance_planner.py"
HARNESS = REPO_ROOT / "scripts" / "test_planner_tap_opens.js"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _read(path):
	return path.read_text(encoding="utf-8")


def _strip_comments(js):
	"""Line comments out, so a pin on code never matches the prose that explains it."""
	return re.sub(r"(?m)^\s*//.*$", "", js)


def _method(js, signature):
	"""One method's text: a class method ends at a tab and a brace, an object's at a tab, brace, comma."""
	start = js.index(signature)
	end = re.compile(r"\n\t\},?\n").search(js, start).start()
	return js[start:end]


class TestTheKit(unittest.TestCase):
	def test_the_bundle_installs_records_over_the_panel(self):
		bundle = _read(BUNDLE)
		self.assertIn('import { create_records } from "./planner_kit/records.js";', bundle)
		self.assertIn("const panel = create_panel(env);", bundle)
		self.assertIn("env.unsaved = panel.unsaved;", bundle)
		self.assertIn("env.records = create_records(env);", bundle)
		self.assertIn("records: env.records,", bundle)
		# The quick looks read env.records, so it is made before they are.
		self.assertLess(bundle.index("env.records = create_records(env);"), bundle.index("peeks: create_peeks(env),"))

	def test_records_opens_through_the_drawer_and_asks_over_unsaved_edits(self):
		records = _strip_comments(_read(KIT / "records.js"))
		self.assertIn('env.frappe.set_route("Form", record.doctype, record.name)', records)
		self.assertIn("env.drawer.navigate(route)", records)
		self.assertIn("env.unsaved()", records)
		self.assertIn("env.frappe.confirm(", records)
		# Same tab, always: nothing opens a window.
		self.assertNotIn("window.open", records)
		self.assertNotIn("_blank", records)

	def test_the_panel_counts_only_the_persons_own_edits(self):
		panel = _strip_comments(_read(KIT / "panel.js"))
		self.assertIn('this.container.addEventListener("pointerdown", touch, true);', panel)
		self.assertIn('this.container.addEventListener("keydown", touch, true);', panel)
		self.assertIn("e.isTrusted !== false", panel)
		self.assertIn("panel.unsaved = () => !!(current && current.is_dirty());", panel)

	def test_the_quick_looks_mark_and_open_their_bookings(self):
		peeks = _strip_comments(_read(KIT / "peeks.js"))
		self.assertIn("const record_of = env.records ? env.records.record_of : null;", peeks)
		self.assertIn('data-pk-open="${escape(record.doctype)}" data-pk-open-name="${escape(record.name)}"', peeks)
		self.assertIn("if (opts && typeof opts.on_open === \"function\") opts.on_open(record);", peeks)
		css = _read(KIT_CSS)
		self.assertIn(".pk-item.pk-open {", css)


class TestThePages(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.pp = _read(PP_JS)
		cls.mp = _read(MP_JS)
		cls.pp_code = _strip_comments(cls.pp)
		cls.mp_code = _strip_comments(cls.mp)

	def test_the_blocks_sit_before_the_tour(self):
		# The tour's block stays last (test_planner_tour); these go just before it.
		for code, block, tour in (
			(self.pp, "Object.assign(ProjectPlanner.prototype, PPR_METHODS);", "const PPTOUR = {"),
			(self.mp, "Object.assign(MaintenancePlanner.prototype, MPR_METHODS);", "const MPTOUR = {"),
		):
			self.assertEqual(code.count(block), 1, block)
			self.assertLess(code.index(block), code.index(tour))

	def test_a_tap_goes_through_the_kits_records_and_falls_back(self):
		self.assertIn("return !!(kit && kit.records.open_item(kind, item));", self.pp_code)
		self.assertIn("return !!(kit && kit.records.open_item(kind, item));", self.mp_code)
		# A drafted new task, no kit, or no permission: what a tap did before.
		self.assertIn('if (card && !this.ppr_open("task", card)) this.open_card(card);', self.pp_code)
		self.assertIn(
			'if (!this.ppr_open("person", { employee: person.employee, resource })) this.p6a_open_person(resource, selected);',
			self.pp_code,
		)
		self.assertTrue(_method(self.mp, "\tmpr_open_card(card) {").rstrip().endswith("this.open_card(card);"))

	def test_every_tap_target_is_routed(self):
		activate = _method(self.pp, "\tactivate(target) {")
		for needle in (
			"this.ppr_open_task(equip_el.getAttribute(\"data-etask\"));",
			"this.ppr_open_task(od_el.getAttribute(\"data-od-task\"));",
			"if (card) this.ppr_open_card(card);",
			'const label_el = target.closest("[data-ppr-task]");',
			"this.open_foreign(item.kind, item.ref, item.date, item.projected);",
		):
			self.assertIn(needle, activate, needle)
		click = _method(self.pp, "\tp6a_click(e) {")
		self.assertIn("this.ppr_open_person(decodeURIComponent(person.getAttribute(\"data-pk-person\")));", click)
		self.assertIn("this.ppr_open_project(decodeURIComponent(project.getAttribute(\"data-pk-project\")));", click)
		self.assertIn("e.stopImmediatePropagation();", click)
		self.assertIn("this.p6a_open_day(day.getAttribute(\"data-pp6a-day\"));", click)  # a date: no record
		self.assertIn("if (card) this.ppr_open_card(card);", _method(self.pp, "\tppg_click(task) {"))
		self.assertIn('this.ppr_open("task", { name: task.ee_task })', _method(self.pp, "\tppo_click(task) {"))
		self.assertIn("on_task_context: (id, task, e) => this.ppo_menu(task, e),", self.pp_code)
		self.assertEqual(self.pp_code.count("on_open: (record) => this.ppr_peek_open(record),"), 2)
		self.assertEqual(self.mp_code.count("on_open: (record) => this.mpr_peek_open(record),"), 2)
		self.assertEqual(self.mp_code.count("if (card) this.mpr_open_card(card);"), 3)  # the click, Enter, Overdue
		self.assertIn(
			"this.mpr_open_person(decodeURIComponent(person.getAttribute(\"data-pk-person\")));",
			_method(self.mp, "\tp6a_click(e) {"),
		)

	def test_the_menus_start_with_quick_edit(self):
		self.assertIn('{ label: __("Quick edit"), on_click: () => this.open_card(card) },', self.pp_code)
		self.assertIn('fresh ? null : { label: __("Open task"), on_click: () => this.ppr_open_task(card.name) },', self.pp_code)
		self.assertIn('{ label: __("Quick edit"), on_click: () => this.open_card(card) },', self.mp_code)
		self.assertNotIn('label: __("Edit")', self.pp_code)
		self.assertNotIn('label: __("Edit")', self.mp_code)

	def test_tap_to_move_is_off_unless_chosen(self):
		for code, pref in ((self.pp, "PP6C"), (self.mp, "MP6C")):
			body = _method(code, "\tp6c_tap_default() {")
			self.assertIn(f'return this.load_pref({pref}.tap_pref, "") === "1";', body)
			self.assertNotIn("p6c_coarse", body)

	def test_a_grip_is_never_a_tap(self):
		body = _method(self.pp, "\tp6b_resize_end(save) {")
		self.assertLess(
			body.index("this.click_blocked_until = Date.now() + 400;"),
			body.index("if (!save || (resize.new_start === resize.start && resize.new_end === resize.end)) return;"),
		)

	def test_the_lookahead_labels_are_targets_and_styled(self):
		self.assertIn(' data-ppr-task="${pp_esc(phase.name)}" role="button" tabindex="0"', self.pp)
		self.assertIn('<span class="ppl-task-name" data-ppr-task="${pp_esc(', self.pp)
		css = _read(PP_JS.with_suffix(".css"))
		self.assertIn(".ppl-phase-name[data-ppr-task],", css)
		self.assertIn(".pp-hm-name.pp-p6a-name:hover span:last-child,", css)

	def test_the_rental_planners_fountain_opens_its_asset(self):
		rp = _read(RP_JS)
		self.assertIn('$(\'<button type="button" class="rp-asset"></button>\')', rp)
		self.assertIn('.on("click", () => frappe.set_route("Form", "Asset", f.asset))', rp)
		self.assertIn(".rp-asset {", _read(RP_JS.with_suffix(".css")))


class TestThePayloads(unittest.TestCase):
	def test_a_projected_visit_says_so_on_the_project_planner(self):
		self.assertIn('"projected": bool(booking.get("projected")),', _read(PP_API))

	def test_a_technician_carries_their_employee(self):
		api = _read(MP_API)
		self.assertIn('employee=resource.get("employee"),', api)
		self.assertIn('"employee": resource.get("employee"),', api)


class TestTheWiring(unittest.TestCase):
	def test_ci_runs_this_suite(self):
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_planner_tap_opens -v", _read(CI))


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


if __name__ == "__main__":
	unittest.main()
