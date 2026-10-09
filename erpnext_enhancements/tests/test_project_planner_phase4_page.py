# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Phase 4 of the Project Planner page (tracking), read as files.

Worked hours against the plan ("14h of 12h", the Running over chip), the labor forecast of the
project on screen, and the vehicles and assets a task uses. All of it is wiring in
``project_planner.js``, and every way it breaks is silent: a wage worked out in the browser from
hours shows a number the server never approved, a filter chip saved to the browser hides the
board the next morning, and an equipment chip in the crew view that starts a drag moves a task by
its vehicle.

Nothing here needs a bench or a ``frappe`` import. The endpoint contract itself (names and
arguments) lives in ``test_project_planner_page.API_CONTRACT``.

Run: python -m unittest erpnext_enhancements.tests.test_project_planner_phase4_page
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PAGE_JS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.js"

MARKER = "// ====================================================================== Phase 4"


def _code():
	return PAGE_JS.read_text(encoding="utf-8")


def _strip(code):
	code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
	return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in code.splitlines())


def _block():
	"""The Phase 4 block, from its banner to the end of the file."""
	code = _code()
	return code[code.index(MARKER) :]


def _method(code, name):
	"""One class method (``\\tname(...) {`` to ``\\t}``)."""
	match = re.search(rf"^\t{name}\(.*?\) \{{\n(.*?)^\t\}}\n", code, flags=re.S | re.M)
	assert match, f"no method {name}"
	return match.group(1)


def _obj_method(block, name):
	"""One method of a mixin object (``\\tname(...) {`` to ``\\t},``)."""
	match = re.search(rf"^\t{name}\(.*?\) \{{\n(.*?)^\t\}},\n", block, flags=re.S | re.M)
	assert match, f"no method {name}"
	return match.group(1)


class TestHooks(unittest.TestCase):
	def test_the_class_hooks_are_one_line_each(self):
		code = _code()
		for line in (
			"this.init_phase4();",
			"this.p4_set_mode(mode);",
			"this.load_phase4();",
			"this.render_phase4();",
			"html.push(this.p4_equipment_rows(days));",
			"Object.assign(ProjectPlanner.prototype, PP4_METHODS);",
		):
			self.assertIn(line, code, line)

	def test_no_method_is_defined_in_two_places(self):
		# The class, Phase 3B's mixin and this one all land on one prototype: a name used twice
		# silently replaces the first. (test_project_planner checks the class on its own.)
		code = _code()
		names = re.findall(r"^\t(?:async\s+)?([A-Za-z_]\w*)\s*\([^)]*\)\s*\{\s*$", code, re.M)
		self.assertEqual(sorted({n for n in names if names.count(n) > 1}), [])
		for name in re.findall(r"^\t(p4_\w+|\w+_phase4)\(", _block(), re.M):
			self.assertEqual(names.count(name), 1, name)


class TestWorkedHours(unittest.TestCase):
	def test_a_card_says_worked_of_planned_and_goes_red_when_over_plan(self):
		code = _strip(_code())
		chips = _obj_method(_block(), "p4_card_chips")
		self.assertIn("this.p4_actual_text(card)", chips)
		self.assertIn('card.over_plan ? " pp-red" : ""', chips)
		self.assertIn("pp-chip${tone}", chips)
		text = _obj_method(_block(), "p4_actual_text")
		self.assertIn("card.actual_hours", text)
		self.assertIn('__("{0}h of {1}h"', text)
		# On the board's cards, the crew view's chips and the trays.
		self.assertIn("this.p4_card_chips(card).forEach((chip) => chips.push(chip));", _method(code, "card_html"))
		self.assertIn("this.p4_card_chips(card).join(", _method(code, "booking_html"))

	def test_the_plan_is_the_estimate_else_what_the_crew_is_booked(self):
		planned = _obj_method(_block(), "p4_planned")
		self.assertIn("card.expected_time", planned)
		self.assertIn("member.booked", planned)

	def test_the_dialog_lists_each_persons_hours_and_refines_them_from_get_actuals(self):
		block = _block()
		rows = _obj_method(block, "p4_dialog_rows")
		self.assertIn('__("Hours worked")', rows)
		self.assertIn("pp-actuals-people", rows)
		people = _obj_method(block, "p4_people_html")
		self.assertIn("member.actual", people)
		self.assertIn("row.planned", people)
		refine = _strip(_obj_method(block, "p4_refine_actuals"))
		self.assertIn("${PP.api}.get_actuals", refine)
		self.assertIn("by_person", refine + _obj_method(block, "p4_actuals_entry") + people)
		# Only a task with clocked hours asks, and a failure costs nothing.
		self.assertIn("card.actual_hours", refine)
		self.assertIn(".catch(() => null)", refine)
		code = _strip(_code())
		self.assertIn("rows.push(...this.p4_dialog_rows(card));", _method(code, "open_card"))
		self.assertIn("this.p4_refine_actuals(card, dialog);", _method(code, "open_card"))

	def test_running_over_is_a_filter_chip_that_is_not_remembered(self):
		code = _strip(_code())
		self.assertIn("if (this.over_only && !card.over_plan) return false;", _method(code, "task_visible"))
		block = _block()
		init = _obj_method(block, "init_phase4")
		self.assertIn("this.over_only = !this.over_only;", init)
		self.assertIn('__("Running over")', _obj_method(block, "p4_update_over"))
		# A saved chip would hide the board the next morning: it never touches the prefs.
		self.assertNotIn("over", " ".join(re.findall(r"prefs: \{.*?\}", _code(), flags=re.S)))
		self.assertNotRegex(_strip(block), r"(save_pref|load_pref)\([^)]*over")
		# It is a calendar-view control: set_mode takes it away from the others.
		self.assertIn('const calendar = mode === "";', _obj_method(block, "p4_set_mode"))


class TestLaborForecast(unittest.TestCase):
	def test_the_forecast_is_read_for_the_project_on_screen(self):
		block = _strip(_block())
		fetch = _obj_method(block, "p4_fetch_forecast")
		self.assertIn("${PP.api}.get_labor_forecast", fetch)
		self.assertIn("args: { project }", fetch)
		# Nothing is asked for without a project, and a different project drops the old figures.
		self.assertIn("if (!this.project)", fetch)
		self.assertIn("this.forecast = null;", _obj_method(block, "p4_sync_forecast"))

	def test_money_is_shown_only_when_the_server_says_so_and_never_worked_out_here(self):
		block = _strip(_block())
		render = _obj_method(block, "p4_render_forecast")
		self.assertIn("forecast.can_see_cost === true", render)
		# Every cost figure sits inside that guard.
		guarded = render[render.index("forecast.can_see_cost === true") :]
		before = render[: render.index("forecast.can_see_cost === true")]
		self.assertNotRegex(before, r"_cost|\.rate\b|\.cost\b")
		for field in ("forecast_cost", "actual_cost", "booked_cost"):
			self.assertIn(f"forecast.{field}", guarded)
		dialog = _obj_method(block, "p4_forecast_dialog")
		self.assertIn("forecast.can_see_cost === true", dialog)
		self.assertLess(dialog.index("forecast.can_see_cost === true"), dialog.index("row.rate"))
		# Hours are never multiplied into a figure on this side.
		self.assertNotRegex(block, r"(hours|rate|cost|booked|actual)\w*\s*\*\s*\w")
		self.assertNotRegex(block, r"\w\s*\*\s*(rate|burden)")

	def test_the_rate_it_is_at_is_stated(self):
		render = _obj_method(_block(), "p4_render_forecast")
		self.assertIn("forecast.burdened", render)
		self.assertIn("base pay rates", render)
		self.assertIn("without a rate are counted in hours only", render)

	def test_hours_are_always_shown(self):
		render = _obj_method(_block(), "p4_render_forecast")
		self.assertIn("forecast.booked_hours", render)
		self.assertIn("forecast.actual_hours", render)
		self.assertLess(render.index("forecast.booked_hours"), render.index("forecast.can_see_cost === true"))


class TestEquipment(unittest.TestCase):
	def test_cards_show_equipment_chips(self):
		chips = _obj_method(_block(), "p4_card_chips")
		self.assertIn('class="pp-chip pp-equip"', chips)
		self.assertIn("p4_equipment_list(card)", chips)
		self.assertIn("pp_esc(", chips)
		self.assertIn("this.p4_tip_lines(card)", _method(_strip(_code()), "card_html"))

	def test_the_dialog_has_an_equipment_table_of_vehicles_and_assets(self):
		block = _strip(_block())
		fields = _obj_method(block, "p4_equipment_fields")
		self.assertIn('fieldname: "equipment"', fields)
		self.assertIn('fieldtype: "Table"', fields)
		self.assertIn('options: "Fleet Vehicle"', fields)
		self.assertIn('options: "Asset"', fields)
		self.assertIn('options: "Vehicle\\nAsset"', fields)
		self.assertIn("depends_on", fields)
		# Only active vehicles can be picked.
		self.assertIn('status: "Active"', fields)
		# Offered to planners who can edit the task, and only by a server that sends equipment.
		code = _strip(_code())
		open_card = _method(code, "open_card")
		self.assertIn("fields.push(...this.p4_equipment_fields(card));", open_card)
		self.assertLess(open_card.index("if (editable) {"), open_card.index("this.p4_equipment_fields(card)"))
		self.assertIn("if (!before) return [];", fields)

	def test_equipment_goes_to_save_task_as_json_and_only_when_changed(self):
		block = _strip(_block())
		arg = _obj_method(block, "p4_equipment_arg")
		self.assertIn("args.equipment = JSON.stringify(next)", arg)
		self.assertIn("!==", arg)
		self.assertIn("this.p4_equipment_arg(card, values, args);", _method(_strip(_code()), "save_dialog"))
		# A row is {equipment_type, vehicle | asset}; blank rows and repeats are dropped.
		rows = _obj_method(block, "p4_rows_from_dialog")
		self.assertIn("seen.has(", rows)
		self.assertIn("if (!name", rows)
		row = _obj_method(block, "p4_equipment_row")
		self.assertIn("equipment_type", row)
		self.assertIn("vehicle: name", row)
		self.assertIn("asset: name", row)

	def test_undo_restores_the_equipment(self):
		code = _strip(_code())
		self.assertIn("equipment: this.p4_equipment_of(card)", _method(code, "snapshot"))
		self.assertIn("args.equipment = JSON.stringify(snap.equipment)", _method(code, "undo"))
		# Nothing is sent for equipment when the server did not send any.
		self.assertIn("if (!Array.isArray(card.equipment)) return null;", _obj_method(_block(), "p4_equipment_of"))

	def test_an_equipment_conflict_is_asked_about_like_any_other(self):
		code = _strip(_code())
		# The "Equipment" key of needs_reason lists under its own heading in every reason dialog.
		self.assertIn('equipment_key: "Equipment"', _block())
		self.assertIn("this.p4_conflict_heading(who)", _method(code, "ask_reason"))
		self.assertIn("this.p4_conflict_heading(who)", _method(code, "preview_copy"))
		self.assertIn("who === PP4.equipment_key", _obj_method(_block(), "p4_conflict_heading"))
		# And never a second, separate conflict flow.
		self.assertNotIn("equipment", _method(code, "send").lower())
		# Problems the server puts on a card join the card's other conflicts.
		self.assertIn("this.p4_card_conflicts(card)", _method(code, "task_conflicts"))

	def test_the_crew_view_has_an_equipment_row_group(self):
		block = _strip(_block())
		load = _obj_method(block, "p4_load_equipment")
		self.assertIn("${PP.api}.get_equipment", load)
		self.assertIn('if (this.view !== "crew") return;', load)
		rows = _obj_method(block, "p4_equipment_rows")
		self.assertIn('class="pp-crew-group"', rows)
		self.assertIn('__("Equipment")', rows)
		self.assertIn("equip.start !== days[0]", rows)
		self.assertIn("PP4.unavailable.includes(item.status)", rows)
		self.assertIn('unavailable: ["In Shop", "Retired"]', block)
		# Two tasks on one day, or a task on a vehicle in the shop, is outlined.
		self.assertIn("used.length > 1", rows)

	def test_an_equipment_chip_opens_the_task_and_is_never_dragged(self):
		code = _strip(_code())
		rows = _obj_method(_block(), "p4_equipment_rows")
		# Not a .pp-card, not a cell: nothing in the drag code can pick it up or drop onto it.
		self.assertIn('class="pp-ecard', rows)
		self.assertNotIn("pp-card", rows)
		self.assertNotIn("data-task", rows)
		self.assertNotIn("data-resource", rows)
		self.assertIn("data-etask", rows)
		self.assertIn('target.closest(".pp-ecard[data-etask]")', _method(code, "activate"))
		self.assertNotIn("pp-ecard", _method(code, "drag_source"))
		self.assertNotIn("pp-ecard", _method(code, "find_target"))
		# Enter and Space open it from the keyboard like any card.
		self.assertIn(".pp-hm-cell, .pp-ecard", code)


class TestUtilizationLink(unittest.TestCase):
	def test_the_toolbar_opens_the_crew_utilization_report_by_route(self):
		block = _strip(_block())
		self.assertIn('report: "Crew Utilization"', block)
		self.assertIn('frappe.set_route("query-report", PP4.report)', _obj_method(block, "init_phase4"))
		self.assertIn('__("Utilization")', block)


class TestHouseRules(unittest.TestCase):
	def test_nothing_in_the_block_touches_storage_history_or_html5_drag(self):
		block = _strip(_block())
		for forbidden in (
			"localStorage",
			"sessionStorage",
			"pushState",
			"replaceState",
			"window.location",
			"location.href",
			"history.",
			"draggable",
			"dragstart",
			"dragover",
			"dataTransfer",
			'type: "GET"',
			"frappe.xcall",
		):
			self.assertNotIn(forbidden, block, forbidden)

	def test_text_is_escaped_before_it_becomes_html(self):
		block = _block()
		raw = [
			line.strip()
			for line in block.splitlines()
			if "<" in line
			and re.search(r"\$\{(?:card|item|use|row|forecast|entry|stop|data|result|project)\.\w+", line)
		]
		self.assertEqual(raw, [])
		for field in ("item.label", "row.label", "subject", "title"):
			self.assertIn(f"pp_esc({field}", block, field)
		# The note under the strip is free text from the server: it goes through .text().
		self.assertRegex(block, r"pp-forecast-note\"></div>'\)\.text\(note\)")

	def test_styles_are_namespaced_and_phone_ready(self):
		code = _code()
		start = code.index("const PP4_STYLE = `")
		style = code[start : code.index("`;", start)]
		classes = set(re.findall(r"\.([a-zA-Z][\w-]*)", style))
		self.assertTrue(classes)
		self.assertEqual({c for c in classes if not c.startswith("pp-")}, {"btn"})
		self.assertIn("@media (max-width:760px)", style)
		self.assertIn("id='pp-style-4'", code)

	def test_user_facing_strings_go_through_translation(self):
		block = _strip(_block())
		for text in (
			"Running over",
			"Labor forecast",
			"Hours worked",
			"Vehicles and assets",
			"Utilization",
			"By person",
		):
			self.assertIn(f'__("{text}"', block, text)


if __name__ == "__main__":
	unittest.main()
