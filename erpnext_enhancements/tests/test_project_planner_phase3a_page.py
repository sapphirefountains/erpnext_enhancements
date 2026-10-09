# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Phase 3A of the Project Planner page (planning helpers), read as files.

Tentative (pencil) bookings, dependency warnings and the shift-successors prompt, qualification
gaps, the capacity heatmap, the Overdue tray and Copy week. All of it is wiring in
``project_planner.js``, and every way it breaks is silent: a heatmap step that is not a route
breaks Back and Forward, a bulk write sent as a GET fails only when somebody clicks, a text
field that skips ``pp_esc`` is an injection, and a checkbox inside a draggable row that starts a
drag makes the tray impossible to use.

Nothing here needs a bench or a ``frappe`` import. The endpoint contract itself (names and
arguments) lives in ``test_project_planner_page.API_CONTRACT``.

Run: python -m unittest erpnext_enhancements.tests.test_project_planner_phase3a_page
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PAGE_JS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.js"
API = APP / "api" / "project_planner.py"
ENGINE = APP / "project_enhancements" / "crew_availability.py"


def _code():
	return PAGE_JS.read_text(encoding="utf-8")


def _code_without_comments():
	# A comment that explains why something is absent names it; strip comments before asserting.
	code = re.sub(r"/\*.*?\*/", "", _code(), flags=re.S)
	return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in code.splitlines())


def _method(code, name):
	"""The source of one class method (up to the next method at the same indent)."""
	match = re.search(rf"^\t{name}\(.*?\) \{{\n(.*?)^\t\}}\n", code, flags=re.S | re.M)
	assert match, f"no method {name}"
	return match.group(1)


class TestTentative(unittest.TestCase):
	def test_a_tentative_card_is_hatched_and_says_pencil(self):
		code = _code_without_comments()
		self.assertIn("pp-card.pp-tentative", code)
		self.assertIn("repeating-linear-gradient", code.split(".pp-card.pp-tentative")[1].split("\n")[0])
		card = _method(code, "card_html")
		self.assertIn('classes.push("pp-tentative")', card)
		self.assertIn('class="pp-chip pp-pencil"', card)
		self.assertIn('__("Pencil")', card)
		# The crew view's chips are hatched too.
		self.assertIn('classes.push("pp-tentative")', _method(code, "booking_html"))

	def test_the_dialog_has_a_tentative_checkbox_that_is_saved(self):
		code = _code_without_comments()
		self.assertRegex(
			code, r'fieldtype: "Check",\s*fieldname: "tentative",\s*label: __\("Tentative \(pencil\)"\)'
		)
		save = _method(code, "save_dialog")
		self.assertIn("values.tentative", save)
		self.assertIn("args.tentative = tentative", save)
		# Only a change is sent.
		self.assertIn("card.tentative ? 1 : 0", save)

	def test_firm_up_is_a_save_task_that_clears_the_flag(self):
		code = _code_without_comments()
		# On the card and in the dialog, through the same commit() path (a conflict asks for a reason).
		self.assertIn('class="btn btn-default btn-xs pp-firm"', code)
		self.assertIn('links.push(["firm", __("Firm up")])', code)
		self.assertRegex(_method(code, "firm_up"), r'this\.commit\(\s*card,\s*"save_task",\s*\{ task: card\.name, tentative: 0 \}')
		self.assertIn("firm_el", _method(code, "activate"))

	def test_soft_load_is_drawn_apart_from_firm_hours(self):
		code = _code_without_comments()
		state = _method(code, "avail_state")
		self.assertIn("day.soft_booked", state)
		self.assertIn("soft_ratio", state)
		# The firm figure is still `booked`; the pencilled hours are an extra, hatched segment.
		self.assertIn("pp-bar b{", code)
		self.assertIn("bar_html(state)", code)
		self.assertIn("pp-softover", code)
		# In the tooltip and for a pencilled booking.
		tip = _method(code, "day_tip")
		self.assertIn("day.soft_booked", tip)
		self.assertIn("booking.tentative", tip)

	def test_undo_restores_the_pencil_flag(self):
		code = _code_without_comments()
		self.assertIn("tentative: card.tentative ? 1 : 0", _method(code, "snapshot"))
		self.assertIn("args.tentative = snap.tentative", _method(code, "undo"))


class TestDependenciesAndQualifications(unittest.TestCase):
	def test_cards_carry_dependency_and_qualification_warnings(self):
		code = _code_without_comments()
		self.assertIn("card.blocked_by", code)
		self.assertIn("card.depends_on", code)
		self.assertIn("card.qualification_gaps", code)
		self.assertIn("Starts before {0} ends on {1}", code)
		self.assertIn("No one on the crew holds: {0}", code)
		card = _method(code, "card_html")
		self.assertIn("blocked_by", card)
		self.assertIn("qualification_gaps", card)
		# In the tooltip and in the dialog summary.
		self.assertIn("this.dependency_lines(card)", card)
		self.assertIn("this.gap_lines(card)", card)
		dialog = _method(code, "open_card")
		self.assertIn("dependency_lines(card)", dialog)
		self.assertIn("gap_lines(card)", dialog)

	def test_qualification_gaps_are_warnings_never_part_of_the_reason_flow(self):
		code = _code_without_comments()
		self.assertNotIn("qualification", _method(code, "ask_reason").lower())
		self.assertNotIn("qualification_gaps", _method(code, "send"))

	def test_moving_later_offers_to_shift_the_tasks_that_follow(self):
		code = _code_without_comments()
		commit = _method(code, "commit")
		self.assertIn("this.offer_shift(snapshot, method, result)", commit)
		offer = _method(code, "offer_shift")
		self.assertIn("result.successors", offer)
		self.assertIn("Shift {0} tasks that follow", offer)
		self.assertIn("working days", offer)
		self.assertIn("frappe.confirm(", offer)
		self.assertIn("this.shift_successors(", offer)
		# The shift goes through send(), so a conflict asks for a reason like any other change.
		self.assertIn('this.send("shift_successors", args)', code)
		# It is given the successors still to move, so ERPNext's own reschedule is not doubled.
		self.assertIn("args.tasks = JSON.stringify(names)", code)
		# Working days skip Saturday and Sunday.
		self.assertIn("isoWeekday() <= 5", _method(code, "working_days_between"))


class TestHeatmap(unittest.TestCase):
	def test_the_heatmap_is_a_route_and_steps_four_weeks_as_routes(self):
		code = _code_without_comments()
		self.assertIn('heatmap_view: "heatmap"', code)
		self.assertIn("heatmap_weeks: 8", code)
		target = _method(code, "route_target")
		self.assertIn("route[1] === PP.heatmap_view", target)
		self.assertIn('moment(route[2], "YYYY-MM-DD", true).isValid()', target)
		self.assertIn("PP.heatmap_weeks / 2", _method(code, "shift"))
		# Stepping and the toolbar button are route changes, so Back and Forward step through them.
		self.assertIn("this.go(this.view, pp_ymd(next))", _method(code, "shift"))
		self.assertIn("[PP.heatmap_view, __(", code)
		# It is not one of the three calendar views.
		self.assertIn('views: ["week", "month", "crew"]', code)

	def test_the_heatmap_reads_get_heatmap_for_eight_weeks(self):
		code = _code_without_comments()
		load = _method(code, "load_heatmap")
		self.assertIn("${PP.api}.get_heatmap", load)
		self.assertIn("weeks: PP.heatmap_weeks", load)
		# One load per view change: the heatmap never calls get_planner.
		self.assertNotIn("get_planner", load)
		self.assertIn("if (this.view === PP.heatmap_view) return this.load_heatmap();", code)

	def test_cells_are_colored_by_firm_load_with_a_hatch_for_pencil(self):
		code = _code_without_comments()
		state = _method(code, "heat_state")
		self.assertIn("0.75", state)
		self.assertIn('"pp-red"', state)
		self.assertIn('"pp-green"', state)
		self.assertIn('"pp-amber"', state)
		self.assertIn("cell.soft_booked", state)
		self.assertIn("over_days", state)
		self.assertIn("cell.level", state)
		# The heatmap's weeks start on the site's first weekday, like the calendar's.
		self.assertIn("(anchor.day() - first + 7) % 7", _method(code, "range"))
		self.assertIn("pp-soft-key", code)

	def test_a_cell_opens_that_week(self):
		code = _code_without_comments()
		self.assertIn('data-hm-week="${pp_esc(week.start)}"', code)
		self.assertIn('this.go("week", cell_el.getAttribute("data-hm-week"))', code)
		# Reachable by keyboard like a card.
		self.assertIn(".pp-hm-cell", re.search(r'closest\("\.pp-card, \.pp-fcard[^"]*"\)', code).group(0))

	def test_the_heatmap_hides_the_calendar_only_controls(self):
		code = _code_without_comments()
		mode = _method(code, "set_mode")
		self.assertIn("PP.heatmap_view", mode)
		for control in ("$project", "$pm", "$foreign", "$undo", "$copy"):
			self.assertIn(control, mode)
		self.assertIn("this.$trays.toggle(!route && !heatmap)", mode)


class TestOverdueTray(unittest.TestCase):
	def test_the_tray_is_collapsed_until_opened_and_loads_get_overdue(self):
		code = _code_without_comments()
		self.assertIn("this.overdue_open = false", code)
		self.assertIn("${PP.api}.get_overdue", _method(code, "load_overdue"))
		# Loaded after every calendar load, drawn first in the trays.
		self.assertIn("this.load_overdue();", _method(code, "load"))
		self.assertRegex(_method(code, "render_trays"), r"this\.\$trays\.empty\(\);\s*this\.render_overdue_tray\(\);")
		self.assertIn("Overdue ({0})", _method(code, "render_overdue_tray"))

	def test_rows_are_grouped_by_project_with_checkboxes(self):
		code = _code_without_comments()
		tray = _method(code, "render_overdue_tray")
		self.assertIn("pp-od-project", tray)
		self.assertIn('type="checkbox" data-od-pick=', tray)
		self.assertIn('type="checkbox" data-od-project=', tray)
		for label in ("Reschedule to…", "Mark done", '__("Cancel")'):
			self.assertIn(label, tray)

	def test_bulk_actions_go_through_bulk_update_and_confirm_first(self):
		code = _code_without_comments()
		act = _method(code, "overdue_act")
		# Mark done and Cancel confirm with the tasks listed; Reschedule asks for a day.
		self.assertIn("frappe.confirm(", act)
		self.assertIn("overdue_list_html(rows)", act)
		self.assertIn("this.ask_reschedule(rows)", act)
		for action in ('"reschedule"', '"complete"', '"cancel"'):
			self.assertIn(action, code)
		bulk = _method(code, "run_bulk")
		self.assertIn('this.send("bulk_update", args)', bulk)
		self.assertIn("tasks: JSON.stringify(names)", bulk)
		# One failed task is reported and never hides the others.
		self.assertIn("result.failed", bulk)
		self.assertIn("Some tasks were not changed", bulk)

	def test_one_overdue_card_can_be_dragged_to_a_day(self):
		code = _code_without_comments()
		source = _method(code, "drag_source")
		self.assertIn('kind: "overdue"', source)
		# The tick box and buttons stay clickable instead of starting a drag.
		self.assertIn('el.closest("input, button")', source)
		self.assertIn("this.drop_overdue(source.row, target)", _method(code, "drop"))
		drop = _method(code, "drop_overdue")
		self.assertIn('this.run_bulk([row.name], "reschedule", target.date)', drop)
		self.assertIn("frappe.confirm(", drop)

	def test_overdue_text_is_escaped(self):
		code = _code_without_comments()
		tray = _method(code, "render_overdue_tray")
		for field in ("row.subject", "row.name", "group.title", "crew"):
			self.assertIn(f"pp_esc({field}", tray)
		self.assertIn("pp_esc(row.subject)", _method(code, "overdue_list_html"))


class TestCopyWeek(unittest.TestCase):
	def test_copy_week_is_a_toolbar_action_on_week_and_crew_views(self):
		code = _code_without_comments()
		self.assertIn('.text(__("Copy week…"))', code)
		self.assertIn('this.$copy.toggle(this.view !== "month" && !!this.data.can_edit)', code)
		self.assertIn("this.open_copy_week()", code)

	def test_the_planner_picks_tasks_and_a_target_week(self):
		code = _code_without_comments()
		open_ = _method(code, "open_copy_week")
		# Every task is ticked by default; the target defaults to next week.
		self.assertIn('data-copy="${pp_esc(card.name)}" checked', open_)
		self.assertIn('.add(7, "days")', open_)
		self.assertIn("data-copy-all", open_)
		self.assertIn("this.week_start_of(", open_)
		# The source week, and the target week, begin on the site's first weekday.
		self.assertIn("this.first_weekday()", _method(code, "week_start_of"))
		self.assertIn("this.week_start_of(this.anchor)", open_)

	def test_a_dry_run_previews_then_a_confirmed_run_writes(self):
		code = _code_without_comments()
		preview = _method(code, "preview_copy")
		self.assertIn('this.send("copy_week", Object.assign({ dry_run: 1 }, base))', preview)
		self.assertIn('this.send("copy_week", Object.assign({ dry_run: 0 }, base))', preview)
		self.assertLess(preview.index("dry_run: 1"), preview.index("dry_run: 0"))
		self.assertIn("tasks: JSON.stringify(names)", preview)
		# Conflicts are shown before confirming; the real run asks for the reason (send()).
		self.assertIn("result.conflicts", preview)
		self.assertIn("Copy anyway", preview)
		# Afterwards the planner is taken to the target week, as a route.
		self.assertIn("this.go(this.view, target_start)", preview)


class TestWeeksFollowTheSite(unittest.TestCase):
	def test_no_monday_is_hard_coded_as_a_week_start(self):
		code = _code_without_comments()
		parts = [
			_method(code, name)
			for name in (
				"week_start_of",
				"open_copy_week",
				"preview_copy",
				"load_heatmap",
				"normalize_heatmap",
				"render_heatmap",
			)
		]
		# range() serves every view: only its heatmap branch is checked.
		parts.append(_method(code, "range").split("PP.heatmap_view")[1].split("if (this.view")[0])
		for part in parts:
			for monday in ("isoWeekday", ".day(1)", ".weekday(", "startOf(\"isoWeek\")", "startOf(\"week\")"):
				self.assertNotIn(monday, part)
		self.assertIn("frappe.boot.sysdefaults.first_day_of_the_week", _method(code, "first_weekday"))

	def test_the_page_sends_each_weeks_first_day(self):
		code = _code_without_comments()
		self.assertIn("args: { start: pp_ymd(start), weeks: PP.heatmap_weeks }", _method(code, "load_heatmap"))
		preview = _method(code, "preview_copy")
		self.assertIn("{ source_start, target_start,", preview)
		# Columns come from the server's weeks (or its week_start), and a cell routes to its exact start.
		self.assertIn("raw.week_start", _method(code, "normalize_heatmap"))
		self.assertIn('this.go("week", cell_el.getAttribute("data-hm-week"))', _method(code, "activate"))


class TestPhase3aConventions(unittest.TestCase):
	def test_the_new_endpoints_are_called_and_none_as_a_get(self):
		code = _code_without_comments()
		for method in ("get_heatmap", "get_overdue"):
			self.assertIn(f"${{PP.api}}.{method}", code)
		for method in ("shift_successors", "bulk_update", "copy_week"):
			self.assertRegex(code, rf'this\.send\("{method}"')
		self.assertNotRegex(code, r"type:\s*[\"']GET[\"']")

	def test_lists_go_over_the_wire_as_json(self):
		code = _code()
		self.assertRegex(code, r"tasks: JSON\.stringify\(")

	def test_the_new_styles_have_phone_rules(self):
		code = _code()
		style = code[code.index("const PP_STYLE = `") : code.index("`;", code.index("const PP_STYLE = `"))]
		phone = style[style.index("@media (max-width:760px)") :]
		self.assertIn(".pp-hm{", phone)

	def test_no_new_localstorage_access(self):
		# Preferences go through load_pref/save_pref, which guard it.
		self.assertEqual(_code().count("window.localStorage"), 2)

	def test_the_api_builds_the_keys_the_page_reads(self):
		if not API.exists() or "def get_heatmap" not in API.read_text(encoding="utf-8"):
			self.skipTest("api/project_planner.py has no Phase 3A endpoints yet")
		source = API.read_text(encoding="utf-8") + ENGINE.read_text(encoding="utf-8")
		for key in (
			"tentative",
			"depends_on",
			"blocked_by",
			"qualification_gaps",
			"successors",
			"soft_booked",
			"capacity",
			"over_days",
			"off_days",
		):
			self.assertIn(f'"{key}"', source, key)


if __name__ == "__main__":
	unittest.main()
