# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Today on the phone (P8 of the Planner UX redesign, ``docs/planner-ux-redesign.md`` §4.4).

On a phone the Project Planner opens on **Today**: the signed-in person's stops for the day and the day
after, in route order, each with its leave-by and arrival times, the site, what the work is, the address,
crewmates, equipment and weather, then the day's route map and *Block my time*. Nik's D4 (2026-10-21):
"Today + tomorrow list", "Today's route map", "Block my own time". Declined and absent: the "running
late" / "can't make it" field buttons and the phone ICS feed.

What this pins, because each of them fails quietly:

* **``get_today`` is the person's own day, read-only.** It finds them through Planner Resource ``user``
  as ``get_my_week`` does, answers the day asked and the next, hands the route map the same
  ``route_payload`` ``get_route`` does (with the browser Maps key, for today only), adds the leave-by
  time (the route view's arithmetic: arrival less the drive), the drive in **miles**, equipment, which
  day of a multi-day task it is and whether it is a Fountain Move task, and writes nothing.
* **Distances are imperial** on the screen (``mi``, never ``km``); routing stays in km underneath.
* **The page's rules** (landing, history, Back, Block my time) run in ``scripts/test_planner_today.js``
  against the real page script. This pins the wiring around them: the hooks are where the other suites
  expect them, Today adds no write, and the landing opens the tab this DEVICE showed last, or Today the first time on a phone (Nik, 2026-10-21; the tab is per device, the filters per person).

Server tests are bench-free: they reuse ``test_planner_phase3b``'s ``frappe`` stub and the real modules.

Run: python -m unittest erpnext_enhancements.tests.test_planner_today
"""

import datetime
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from erpnext_enhancements.tests import test_planner_phase3b as base

setUpModule = base.setUpModule
tearDownModule = base.tearDownModule

D = datetime.date
REPO = Path(__file__).resolve().parents[2]
APP = REPO / "erpnext_enhancements"
PAGE = APP / "project_enhancements" / "page" / "project_planner"
PAGE_JS = PAGE / "project_planner.js"
PAGE_CSS = PAGE / "project_planner.css"
HARNESS = REPO / "scripts" / "test_planner_today.js"
CI = REPO / ".github" / "workflows" / "ci.yml"
TODAY = D(2026, 10, 8)  # the stub's "now": a Thursday
TOMORROW = D(2026, 10, 9)


def _code(path=PAGE_JS):
	"""The script with comments stripped: an assertion must not be satisfied by its own prose."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
	return "\n".join(re.sub(r"(^|\s)//.*$", r"\1", line) for line in src.splitlines())


def _block(code):
	"""The P8 block of the page script: from its constants to the mixin that carries its methods."""
	start = code.index("const PP8 = {")
	return code[start : code.index("Object.assign(ProjectPlanner.prototype, PP8_METHODS);", start)]


# ====================================================================== the arithmetic


class TestLeaveBy(base._Base):
	def test_leave_by_is_the_arrival_less_the_drive(self):
		self.assertEqual(base.api.leave_by_clock("08:10", 35), "07:35")
		self.assertEqual(base.api.leave_by_clock("08:10", 35.4), "07:35")

	def test_no_drive_no_leave_by(self):
		for drive in (None, "", 0, -3):
			self.assertIsNone(base.api.leave_by_clock("08:10", drive), drive)
		self.assertIsNone(base.api.leave_by_clock(None, 20))
		self.assertIsNone(base.api.leave_by_clock("", 20))

	def test_it_agrees_with_the_calendar_and_the_ping(self):
		"""``route_slots`` is what the calendar and the leave-by ping use; Today must say the same."""
		routing = sys.modules["erpnext_enhancements.project_enhancements.routing"]
		source = (APP / "project_enhancements" / "route_slots.py").read_text(encoding="utf-8")
		self.assertIn('"leave_by": (arrive - drive) if drive is not None else None,', source)
		self.assertEqual(
			base.api.leave_by_clock("09:00", 45),
			routing.fmt_clock(routing.parse_minutes("09:00") - 45),
		)


class TestDayOf(base._Base):
	def test_the_nth_of_a_tasks_working_days(self):
		task = {"exp_start_date": "2026-10-05 08:00:00", "exp_end_date": "2026-10-09 17:00:00"}
		self.assertEqual(base.api.task_day_of(task, D(2026, 10, 5)), (1, 5))
		self.assertEqual(base.api.task_day_of(task, D(2026, 10, 7)), (3, 5))
		self.assertEqual(base.api.task_day_of(task, D(2026, 10, 9)), (5, 5))

	def test_weekends_are_not_days_of_the_task(self):
		task = {"exp_start_date": "2026-10-08 08:00:00", "exp_end_date": "2026-10-13 17:00:00"}
		# Thu, Fri, (Sat, Sun), Mon, Tue: four working days, Monday the third.
		self.assertEqual(base.api.task_day_of(task, D(2026, 10, 12)), (3, 4))

	def test_a_one_day_or_undated_task_has_no_day_of(self):
		self.assertIsNone(base.api.task_day_of({"exp_start_date": "2026-10-08", "exp_end_date": "2026-10-08"}, TODAY))
		self.assertIsNone(base.api.task_day_of({}, TODAY))
		self.assertIsNone(base.api.task_day_of(None, TODAY))

	def test_a_day_outside_the_span_has_none(self):
		task = {"exp_start_date": "2026-10-05", "exp_end_date": "2026-10-09"}
		self.assertIsNone(base.api.task_day_of(task, D(2026, 10, 12)))


class TestTodayItems(base._Base):
	def _entry(self):
		return {
			"date": "2026-10-08",
			"items": [
				{"kind": "task", "ref": "TASK-1", "label": "Excavate basin", "crew": ["Jesse"], "address": "1 Main"},
				{"kind": "visit", "ref": "VIS-1", "label": "Sequoia fountain", "crew": []},
				{"kind": "task", "ref": "TASK-MOVE", "label": "Move day", "crew": []},
			],
		}

	def _payload(self):
		return {
			"stops": [
				{
					"order": 1,
					"kind": "task",
					"ref": "TASK-1",
					"project": "PRJ-1",
					"arrive": "08:10",
					"drive_minutes": 35,
					"miles": 12.4,
					"located": True,
					"weather": ["Wind 28 mph"],
				},
				{"order": 2, "kind": "visit", "ref": "VIS-1", "arrive": "11:00", "drive_minutes": 20, "miles": 7.0, "located": True},
				{"order": 3, "kind": "task", "ref": "TASK-MOVE", "project": "PRJ-MOVE", "arrive": None, "drive_minutes": None, "located": False},
			]
		}

	def test_each_stop_gets_what_the_phone_shows(self):
		tasks = {"TASK-1": {"exp_start_date": "2026-10-07", "exp_end_date": "2026-10-09"}}
		equipment = {"TASK-1": [{"type": "Fleet Vehicle", "name": "V1", "label": "Skid steer"}]}
		out = base.api.today_items(self._entry(), self._payload(), tasks, equipment, {"PRJ-MOVE"}, TODAY)
		first, visit, move = out
		self.assertEqual(
			(first["order"], first["leave_by"], first["miles"], first["located"]), (1, "07:35", 12.4, True)
		)
		self.assertEqual(first["weather"], ["Wind 28 mph"])
		self.assertEqual(first["equipment"], ["Skid steer"])
		self.assertEqual(first["day_of"], [2, 3])
		self.assertEqual(first["crew"], ["Jesse"])  # day_entry's own fields are kept
		self.assertFalse(first["fountain_move"])
		self.assertEqual((visit["leave_by"], visit["equipment"], visit["day_of"]), ("10:40", [], None))
		self.assertTrue(move["fountain_move"])
		self.assertIsNone(move["leave_by"])  # no location, no drive, no leave-by
		self.assertFalse(move["located"])

	def test_a_visit_is_never_a_task_of_equipment(self):
		equipment = {"VIS-1": [{"label": "should not show"}]}
		out = base.api.today_items(self._entry(), self._payload(), {}, equipment, set(), TODAY)
		self.assertEqual(out[1]["equipment"], [])

	def test_nothing_is_dropped_when_the_payload_is_short(self):
		out = base.api.today_items(self._entry(), {"stops": []}, {}, {}, set(), TODAY)
		self.assertEqual(out, [])


# ====================================================================== get_today


class TestGetToday(base._Base):
	def setUp(self):
		super().setUp()
		base.frappe.session.user = "austin@example.com"
		base.frappe.roles = ["Maintenance User"]  # technicians can open it
		self.asked = {}
		self.entries = [
			{"date": "2026-10-08", "off": None, "travel": None, "drive_minutes": 100, "items": [{"kind": "task", "ref": "TASK-1", "label": "Excavate basin"}]},
			{"date": "2026-10-09", "off": None, "travel": None, "drive_minutes": 0, "items": []},
		]
		self.data = {
			"resources": [{"name": "RES-1", "label": "Austin Healey"}],
			"days": {"RES-1": {"2026-10-08": {"bookings": [{"kind": "task", "ref": "TASK-1", "project": "PRJ-1"}]}, "2026-10-09": {"bookings": []}}},
			"routes": {},
			"settings": {},
			"tasks": [{"name": "TASK-1", "exp_start_date": "2026-10-07", "exp_end_date": "2026-10-09"}],
		}
		self.payloads = []

		def route_payload(person, day, cell, route, settings, details, titles, maps, shop):
			self.payloads.append((day, maps, shop))
			return {
				"date": str(day),
				"miles": 62.1,
				"long_drive": day == TODAY,
				"maps_key": (maps or {}).get("maps_key", ""),
				"stops": [{"order": 1, "kind": "task", "ref": "TASK-1", "project": "PRJ-1", "arrive": "08:10", "drive_minutes": 35, "miles": 12.4, "located": True}]
				if day == TODAY
				else [],
			}

		def people_days(start, end, resources=None):
			self.asked["people_days"] = (start, end, resources)
			return self.data, {"RES-1": self.entries}

		pickup = types.ModuleType("erpnext_enhancements.api.pickup_routing")
		pickup._depot_address = lambda: "Shop, Lehi UT"
		blocks = types.SimpleNamespace(
			can_schedule=lambda: False,
			my_week_extras=lambda resource, first, last: {
				"blocks": [{"name": "PB-1", "date": "2026-10-08"}],
				"day_notes": {"2026-10-08": [{"text": "Shop meeting"}]},
			},
		)
		api = base.api
		self.mine = [
			mock.patch.object(api, "_people_days", people_days),
			mock.patch.object(api, "route_payload", route_payload),
			mock.patch.object(api, "_route_weather", lambda payload: payload),
			mock.patch.object(api, "_maps_config", lambda: {"maps_key": "key-123"}),
			mock.patch.object(api, "_project_titles", lambda projects: {}),
			mock.patch.object(api, "_phase6d", lambda: blocks),
			mock.patch.object(api.routing, "locate", lambda bookings, detail=False: {}),
			mock.patch.object(api.fountain_move_jobs, "move_projects", lambda names: {"PRJ-1"}),
			mock.patch.object(api.engine, "read_equipment", lambda names: {"TASK-1": [{"type": "Asset", "name": "A1", "label": "Old row label"}]}),
			mock.patch.object(api.engine, "read_equipment_details", lambda refs: ({("Asset", "A1"): "Skid steer"}, {})),
			mock.patch.dict(sys.modules, {"erpnext_enhancements.api.pickup_routing": pickup}),
		]
		for patch in self.mine:
			patch.start()

	def tearDown(self):
		for patch in self.mine:
			patch.stop()
		super().tearDown()

	def test_the_day_asked_and_the_next_for_the_signed_in_person(self):
		answer = base.api.get_today("2026-10-08")
		self.assertEqual(self.asked["people_days"], (TODAY, TOMORROW, ["RES-1"]))
		self.assertEqual((answer["resource"], answer["label"]), ("RES-1", "Austin Healey"))
		self.assertEqual((answer["date"], answer["tomorrow"], answer["today"]), ("2026-10-08", "2026-10-09", "2026-10-08"))
		self.assertEqual([entry["date"] for entry in answer["days"]], ["2026-10-08", "2026-10-09"])

	def test_it_defaults_to_today(self):
		answer = base.api.get_today()
		self.assertEqual(answer["date"], str(TODAY))

	def test_stops_carry_leave_by_miles_equipment_and_the_day_of_the_task(self):
		stop = base.api.get_today("2026-10-08")["days"][0]["items"][0]
		self.assertEqual(stop["leave_by"], "07:35")
		self.assertEqual(stop["miles"], 12.4)
		# The label is read fresh (an Asset's asset_name), as get_planner does, not the row's frozen one.
		self.assertEqual(stop["equipment"], ["Skid steer"])
		self.assertEqual(stop["day_of"], [2, 3])
		self.assertTrue(stop["fountain_move"])
		self.assertEqual(stop["label"], "Excavate basin")

	def test_the_day_totals_are_in_miles(self):
		today, tomorrow = base.api.get_today("2026-10-08")["days"]
		self.assertEqual((today["miles"], today["drive_minutes"], today["long_drive"]), (62.1, 100, True))
		self.assertEqual(tomorrow["items"], [])
		answer = base.api.get_today("2026-10-08")
		self.assertNotIn("km", repr(answer["days"]).replace("km_", ""))

	def test_the_route_is_todays_and_carries_the_maps_key(self):
		answer = base.api.get_today("2026-10-08")
		self.assertEqual(answer["route"]["date"], "2026-10-08")
		self.assertEqual(answer["route"]["maps_key"], "key-123")
		(today, maps_today, shop_today), (tomorrow, maps_tomorrow, shop_tomorrow) = self.payloads[:2]
		self.assertEqual((today, tomorrow), (TODAY, TOMORROW))
		# Only the map on screen needs the browser key and the shop's address.
		self.assertEqual((maps_today, shop_today), ({"maps_key": "key-123"}, "Shop, Lehi UT"))
		self.assertEqual((maps_tomorrow, shop_tomorrow), ({}, None))

	def test_their_own_blocks_and_the_days_notes_come_with_it(self):
		answer = base.api.get_today("2026-10-08")
		self.assertEqual([b["name"] for b in answer["blocks"]], ["PB-1"])
		self.assertEqual(answer["day_notes"]["2026-10-08"][0]["text"], "Shop meeting")
		self.assertFalse(answer["can_schedule"])

	def test_someone_not_on_the_planners_list_is_told_so(self):
		base.frappe.session.user = "stranger@example.com"
		answer = base.api.get_today("2026-10-08")
		self.assertIsNone(answer["resource"])
		self.assertIn("not on the planner's list", answer["message"])
		self.assertEqual((answer["days"], answer["route"]), ([], None))
		self.assertNotIn("people_days", self.asked)  # no engine pass for nobody

	def test_an_inactive_entry_is_told_so(self):
		self.data["resources"] = []
		answer = base.api.get_today("2026-10-08")
		self.assertIsNone(answer["resource"])
		self.assertIn("not active", answer["message"])

	def test_it_needs_a_planner_role(self):
		base.frappe.roles = ["Customer"]
		with self.assertRaises(base._PermissionError):
			base.api.get_today()

	def test_it_writes_nothing(self):
		base.api.get_today("2026-10-08")
		self.assertEqual(base.frappe.saved, [])
		self.assertEqual(base.frappe.tables["Planner Draft Change"], [])
		self.assertEqual([c for c in base.frappe.db_calls if c != "commit" and c[0] == "set_value"], [])

	def test_it_is_a_get_with_one_argument(self):
		source = (APP / "api" / "project_planner.py").read_text(encoding="utf-8")
		start = source.index("def get_today(")
		self.assertTrue(source[:start].rstrip().endswith("@frappe.whitelist()"))
		self.assertIn("def get_today(date=None):", source)


class TestWithTheRealRoutePayload(base._Base):
	"""A travel day has no route: ``route_payload`` lists the stops unrouted. Today still reads them."""

	def test_unrouted_stops_have_no_leave_by_and_no_miles_to_invent(self):
		person = {"name": "RES-1", "label": "Austin Healey"}
		cell = {"bookings": [{"kind": "task", "ref": "TASK-1", "label": "Excavate basin", "project": "PRJ-1", "hours": 4}]}
		payload = base.api.route_payload(person, TODAY, cell, None, {}, {}, {}, {}, None)
		entry = {"date": str(TODAY), "items": [{"kind": "task", "ref": "TASK-1", "label": "Excavate basin"}]}
		(stop,) = base.api.today_items(entry, payload, {}, {}, set(), TODAY)
		self.assertEqual((stop["order"], stop["located"], stop["leave_by"]), (1, False, None))
		self.assertIsNone(stop["miles"])


# ====================================================================== the page


class TestThePageScript(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.code = _code()
		cls.raw = PAGE_JS.read_text(encoding="utf-8")
		cls.block = _block(cls.code)

	def test_the_hooks_into_the_class_are_one_line_each(self):
		for line in (
			"this.init_phase8();",
			"const today = this.today_target(route);",
			"this.show_today(target.anchor);",
			"return !route[1] && this.p8_landing();",
			"this.p8_remember_tab(target.view);",
			"if (this.$today) this.$today.hide();",
			"if (this.view === PP8.view) return this.load_today();",
			"if (mine) this.p8_block(mine.date, mine);",
		):
			self.assertEqual(self.code.count(line), 1, line)

	def test_the_landing_waits_for_the_saved_filters_and_never_reads_a_saved_view(self):
		"""Nik, 2026-10-21: the tab is per device; the filters stay per person."""
		body = self.code[self.code.index("\tp6c_before_route() {") :]
		body = body[: body.index("\n\t},")]
		# After the person's saved views are in and their filters are applied...
		hook = body.index("this.p8_landing()")
		self.assertGreater(hook, body.index("if (!p6c.loaded) {"))
		self.assertGreater(hook, body.index("p6c.restored = true;"))
		# ...and the server row's view is never read for the landing.
		self.assertNotIn("last.view", body)
		self.assertNotIn("PP6C.schema.view", body)
		self.assertEqual(body.count("this.p8_landing()"), 1)

	def test_the_tab_is_remembered_per_device_and_per_user_never_on_the_server(self):
		block = self.block
		self.assertIn('tab_pref: "ee_project_planner_tab"', block)
		for method in ("p8_tab", "p8_remember_tab"):
			body = block[block.index(f"\t{method}(") :]
			body = body[: body.index("\n\t},")]
			self.assertIn("frappe.session.user", body, method)
			self.assertRegex(body, r"(load|save)_pref\(`\$\{PP8\.tab_pref\}:\$\{user\}`", method)
		# load_pref/save_pref wrap the browser store in try/catch: a blocked one is a first visit.
		for name in ("load_pref", "save_pref"):
			body = self.code[self.code.index(f"\t{name}(") :]
			body = body[: body.index("\n\t}")]
			self.assertIn("catch (e)", body, name)
		# The person's saved row has no view; only a named view ("Save view as…") carries one.
		state = self.code[self.code.index("\tp6c_state(remembered) {") :]
		state = state[: state.index("\n\t},")]
		self.assertIn("if (!remembered && views.includes(this.view)) state.view = this.view;", state)

	def test_a_link_that_names_a_filter_never_restores_today(self):
		body = self.block[self.block.index("\tp8_landing() {") :]
		body = body[: body.index("\n\t},")]
		self.assertLess(body.index("PP.url_filters.some("), body.index("frappe.set_route("))
		self.assertIn("if (link && tab === PP8.view) tab = PP6C.default_view;", body)
		self.assertIn("this.p8_landed = true", body)

	def test_the_landing_does_not_look_at_roles(self):
		block = self.block
		self.assertNotIn("scheduler_roles", block)
		self.assertNotIn("p8_is_scheduler", block)
		self.assertNotIn("has_role", block)

	def test_the_landing_replaces_and_never_pushes(self):
		body = self.block[self.block.index("\tp8_landing() {") :]
		body = body[: body.index("\n\t},")]
		self.assertIn("frappe.route_flags.replace_route = true;", body)
		self.assertLess(body.index("replace_route"), body.index("frappe.set_route("))
		# A bare address only, a phone only, and a filter link left alone.
		for needle in ("route[1]) return false", "PP.url_filters.some(", "this.p8_is_phone()"):
			self.assertIn(needle, body)

	def test_the_only_server_call_is_get_today_and_nothing_is_written(self):
		self.assertEqual(re.findall(r"\$\{PP\.api\}\.(\w+)", self.block), ["get_today"])
		for forbidden in ("save_task", "add_crew", "swap_crew", "this.send(", "shift_successors", "bulk_update", "with_draft", "draft_args"):
			self.assertNotIn(forbidden, self.block, forbidden)

	def test_the_field_buttons_nik_declined_are_not_here(self):
		lowered = self.block.lower()
		for declined in ("running late", "can't make it", "cannot make it", ".ics", "webcal"):
			self.assertNotIn(declined, lowered, declined)

	def test_distances_are_miles(self):
		stop = self.block[self.block.index("const pp8_stop_html") : self.block.index("const PP8_METHODS")]
		self.assertIn(" mi`", stop)
		self.assertNotRegex(stop, r"\bkm\b")
		self.assertNotIn("kilomet", stop.lower())

	def test_block_my_time_is_the_personal_block_form_for_oneself(self):
		body = self.block[self.block.index("\tp8_block(ymd, block) {") :]
		body = body[: body.index("\n\t},")]
		self.assertIn("self_only: true,", body)
		self.assertIn("kit.blocks.form(", body)

	def test_today_is_not_in_the_servers_saved_view_list(self):
		"""The tab is the device's, so Today is not in the server's list of views a saved row may hold.
		The Lookahead (P5) is: a named view ("Save view as…") saved on it carries it, as the calendar
		views' do; Today has no filters to save."""
		server = (APP / "api" / "planner_views_prefs.py").read_text(encoding="utf-8")
		match = re.search(r'"view": \("enum", \(([^)]*)\)\),\n\t\t"project": \("text",\),', server)
		self.assertTrue(match)
		views = ["week", "month", "crew", "heatmap", "capacity", "lookahead"]
		self.assertEqual(re.findall(r'"([^"]+)"', match.group(1)), views)
		page = re.search(r'view: \["enum", \[(.*?)\]\]', self.code)
		self.assertEqual(re.findall(r'"([^"]+)"', page.group(1)), views)
		# The tabs a device remembers are the page's own list: those (Capacity is P7's; the old "heatmap" is
		# still read), plus Today.
		tabs = re.search(r"tabs: \[(.*?)\]", self.block)
		self.assertEqual(
			re.findall(r'"([^"]+)"', tabs.group(1)), ["week", "month", "crew", "heatmap", "capacity", "today", "lookahead"]
		)

	def test_every_class_it_uses_is_styled(self):
		css = re.sub(r"/\*.*?\*/", "", PAGE_CSS.read_text(encoding="utf-8"), flags=re.S)
		used = set(re.findall(r"pp-td-[a-z-]+", self.block)) | {"pp-today"}
		for name in sorted(used):
			self.assertIn(f".{name}", css, name)

	def test_its_section_of_the_stylesheet_uses_tokens_and_phone_sized_targets(self):
		css = PAGE_CSS.read_text(encoding="utf-8")
		start = css.index("/* P8: Today (phone)")
		section = css[start : css.index("/* Phase 4: vehicles", start)]
		self.assertNotRegex(section, r"#[0-9a-fA-F]{3,8}\b")
		self.assertIn("var(--sf-touch-min)", section)


class TestTheHarness(unittest.TestCase):
	def test_ci_runs_this_suite_and_the_harness(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_planner_today -v", ci)

	@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
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

	@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
	def test_the_harness_parses(self):
		result = subprocess.run(
			[shutil.which("node"), "--check", str(HARNESS)], capture_output=True, text=True, encoding="utf-8"
		)
		self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
	unittest.main()
