"""Links between related pages, from the forms (custom pages S5b, v1.667.0).

Each form opens the page that works on the same record, in the right state:

* Project — View > Open in Project Planner: the planner with that project as its Project
  filter (``?project=``, read from ``frappe.route_options`` and consumed by the planner), on the
  week the project starts when that is still ahead.
* Project Quality Inspection — Open in Inspection Wizard, on a saved draft:
  ``/desk/inspection-wizard/<inspection>/0``, the wizard's own path form.
* Rental Booking — View > Open in Rental Planner: the board from the booking's first day
  (``?start=``, S5a).
* Maintenance Day Board — a submitted visit's card links to Visit Review
  (``/desk/visit-review/<visit>``), and the toolbar opens the Maintenance Planner's week for the
  day shown.

Every link is a route, so it is a history entry and Back returns to where it was pressed (Nik's
rule), and is shown only to those whose roles open the target page (``frappe.boot.page_info``).
None is an ``/app/`` link.

The behaviour (the route each button sends, and the target page's state after v16.50's router
has filled ``route_options`` and the address) runs in ``scripts/test_form_page_links.js``,
against the real form and page scripts. This pins the shape that harness relies on.
Bench-free: reads files and runs node, no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_form_page_links
"""

import ast
import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
HARNESS = REPO / "scripts" / "test_form_page_links.js"

PROJECT_LINK = APP / "public/js/project_enhancements/project_planner_link.js"
INSPECTION_FORM = APP / "quality/doctype/project_quality_inspection/project_quality_inspection.js"
BOOKING_FORM = APP / "asset_management/doctype/rental_booking/rental_booking.js"
PROJECT_PLANNER = APP / "project_enhancements/page/project_planner/project_planner.js"
DAY_BOARD = APP / "sapphire_maintenance/page/maintenance_day_board/maintenance_day_board.js"
HOOKS = APP / "hooks.py"

CHANGED = (PROJECT_LINK, INSPECTION_FORM, BOOKING_FORM, PROJECT_PLANNER, DAY_BOARD)


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	"""The script with comments stripped: an assertion must not be satisfied by its own prose."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _between(src, start, end):
	at = src.index(start)
	return src[at : src.index(end, at + len(start))]


def _doctype_js():
	"""hooks.py's ``doctype_js``, read as a literal."""
	tree = ast.parse(_text(HOOKS))
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "doctype_js" for t in node.targets):
			return ast.literal_eval(node.value)
	raise AssertionError("hooks.py has no doctype_js")


class TestTheButtons(unittest.TestCase):
	def test_project_opens_the_planner_with_its_project(self):
		code = _code(PROJECT_LINK)
		self.assertIn('frappe.ui.form.on("Project"', code)
		self.assertIn('__("Open in Project Planner")', code)
		self.assertIn('__("View")', code)
		self.assertIn("const options = { project: frm.doc.name };", code)
		self.assertIn('frappe.set_route("project-planner", options);', code)
		self.assertIn('frappe.set_route("project-planner", "week", starts, options);', code)
		self.assertIn('pages["project-planner"]', code)
		self.assertIn("frm.is_new()", code)

	def test_project_script_is_loaded_and_declares_nothing_at_the_top_level(self):
		"""doctype_js concatenates every Project script into one: a top-level const or function
		could collide with another file's and take the whole form's scripts down."""
		self.assertIn("public/js/project_enhancements/project_planner_link.js", _doctype_js()["Project"])
		top = [line for line in _code(PROJECT_LINK).splitlines() if line and not line[0].isspace()]
		self.assertEqual(top, ['frappe.ui.form.on("Project", {', "});"])

	def test_inspection_opens_the_wizard_on_that_inspection(self):
		code = _code(INSPECTION_FORM)
		self.assertIn('frappe.ui.form.on("Project Quality Inspection"', code)
		self.assertIn('__("Open in Inspection Wizard")', code)
		self.assertIn('frappe.set_route("inspection-wizard", frm.doc.name, "0");', code)
		self.assertIn("frm.doc.docstatus !== 0", code)
		self.assertIn('pages["inspection-wizard"]', code)
		# Cleared first, as the wizard's own route_to does.
		handler = _between(code, '__("Open in Inspection Wizard")', "});")
		self.assertLess(handler.index("frappe.route_options = null;"), handler.index("frappe.set_route("))

	def test_the_inspection_script_is_not_also_in_doctype_js(self):
		"""Frappe loads <module>/doctype/<name>/<name>.js itself; doctype_js would append it a
		second time to the same script."""
		self.assertNotIn("Project Quality Inspection", _doctype_js())

	def test_booking_opens_the_board_on_its_dates(self):
		code = _code(BOOKING_FORM)
		self.assertIn("add_planner_button(frm);", code)
		button = _between(code, "function add_planner_button(frm) {", "function schedule_or_warn(frm) {")
		self.assertIn('__("Open in Rental Planner")', button)
		self.assertIn('__("View")', button)
		self.assertIn('frappe.set_route("rental-planner", { start });', button)
		self.assertIn('pages["rental-planner"]', button)
		self.assertIn("doc.delivery_datetime || doc.setup_datetime || doc.event_start_datetime", button)
		# The old bare link is gone, not left beside the new one.
		self.assertNotIn('__("Rental Planner"), () => frappe.set_route("rental-planner")', code)

	def test_day_board_links_a_submitted_visit_to_visit_review(self):
		code = _code(DAY_BOARD)
		link = _between(code, "function reviewLink(row) {", "function column(")
		self.assertIn("if (!pages['visit-review']) return '';", link)
		self.assertIn("href=\"/desk/visit-review/' + encodeURIComponent(row.name)", link)
		self.assertIn('class="mdb-sublink"', link)
		# Submitted and flagged visits (docstatus 1), never a draft.
		submitted = _between(code, "data.submitted_today || []", "column(__('Flagged (7 days)')")
		flagged = _between(code, "column(__('Flagged (7 days)')", "].join(''));")
		scheduled = _between(code, "column(__('Scheduled')", "column(__('Clocked In')")
		self.assertIn("+ reviewLink(row)", submitted)
		self.assertIn("+ reviewLink(row)", flagged)
		self.assertNotIn("reviewLink", scheduled)

	def test_day_board_opens_the_planner_on_the_day_shown(self):
		code = _code(DAY_BOARD)
		button = _between(code, "__('Open in Maintenance Planner')", "const $board =")
		self.assertIn("frappe.set_route('maintenance-planner', 'week', day);", button)
		self.assertIn("board.day()", button)
		self.assertIn("frappe.route_options = null;", button)
		self.assertIn("if (pages['maintenance-planner']) {", code)


class TestTheProjectPlannerRead(unittest.TestCase):
	"""The planner's one new read: `project` from route_options, consumed, after the last-used
	view is restored (p6c_before_route) so the form's project wins over the remembered one."""

	def test_project_is_read_after_the_last_used_view_and_consumed(self):
		code = _code(PROJECT_PLANNER)
		handle = _between(code, "\thandle_route() {", "\tset_mode(mode) {")
		self.assertLess(handle.index("this.p6c_before_route()"), handle.index("frappe.route_options"))
		read = _between(handle, "const opts = frappe.route_options;", "this.view = target.view;")
		self.assertIn('Object.prototype.hasOwnProperty.call(opts, "project")', read)
		self.assertIn("delete opts.project;", read)
		self.assertIn("if (!Object.keys(opts).length) frappe.route_options = null;", read)
		self.assertIn('this.pm = "";', read)
		self.assertIn("this.save_pref(PP.prefs.project, this.project);", read)

	def test_the_planner_writes_no_history_of_its_own(self):
		"""The read only reads: the address is the router's, and the planner moves by set_route."""
		code = _code(PROJECT_PLANNER)
		self.assertNotIn("history.pushState", code)
		self.assertNotIn("history.replaceState", code)


class TestNoAppLinks(unittest.TestCase):
	def test_no_app_link_in_any_changed_script(self):
		"""On v16 an /app href is a full reload through a redirect, and Back reloads again."""
		for path in CHANGED:
			with self.subTest(path=path.name):
				found = re.search(r"['\"`]/app/[^'\"`]*", _code(path))
				self.assertIsNone(found, found and found.group(0))


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheBehaviourHarness(unittest.TestCase):
	def test_the_links_harness_passes(self):
		result = subprocess.run(
			[shutil.which("node"), str(HARNESS)], capture_output=True, text=True, timeout=300
		)
		output = (result.stdout + result.stderr)[-6000:]
		self.assertEqual(result.returncode, 0, output)
		self.assertIn("checks passed", result.stdout, output)

	def test_every_changed_script_parses(self):
		for path in CHANGED:
			with self.subTest(path=path.name):
				result = subprocess.run(
					[shutil.which("node"), "--check", str(path)], capture_output=True, text=True
				)
				self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
	unittest.main()
