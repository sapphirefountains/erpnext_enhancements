# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Planner Phase 6A (TASK-2026-02467): quick looks and polish on both planners, and the planner kit.

Nik, 2026-10-09: "click the Technicians name or something and see what their specific schedule is in
a pop up or something so as not to lose context overall." What this pins, because each part fails
quietly:

* **The reads are gated and bounded, and never ask Google.** ``api/planner_views.py`` opens to the
  union of both planners' roles (the site read to the Maintenance Planner's own, since its record
  query is raw SQL), resolves a Maintenance Planner User to their Planner Resource, refuses more
  than 31 days, and passes ``google=False`` to every engine call: a drawer must not cost a Routes
  request per person per day.
* **What a drawer must never show.** A day off reads "Off" or "Holiday", never the kind or reason of
  time off, even if the engine's label ever carried one; a project overview gives money only to
  ``planner_tracking.COST_ROLES``; an internal project answers "not on the planner".
* **The kit loads the right way and Back closes a drawer.** The bundle exists, is not a global
  include, is ``frappe.require``-d by both pages, and decides about a popstate inside a wrapper of
  ``frappe.router.route`` before the router routes (a capture listener cannot run ahead of frappe's:
  a browser runs window's listeners in the order they were added). A node harness drives
  ``history.js`` against a fake browser history that behaves that way: Back closes without
  re-routing, a closed drawer steps back off its entry, a route change in the same tick replaces
  the entry instead, Forward reopens.
* **The pages keep their contract.** Every name element is decorated to open the person peek, the
  undo toast comes from ``push_undo``'s success path (and replaces the green alert, never doubles
  it), drawers' tasks and visits reach the page's own drag code, and the page reads only keys the
  endpoints build. The planner_views methods and the arguments the pages and the kit send are a
  contract of their own (``VIEWS_CONTRACT``), checked against the module.

Bench-free: installs its own ``frappe`` stub, runs the real modules, puts ``sys.modules`` back.
The node parts skip when node is not on PATH.

Run: python -m unittest erpnext_enhancements.tests.test_planner_phase6a
"""

import ast
import datetime
import importlib
import json
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
VIEWS = APP / "api/planner_views.py"
PP_JS = APP / "project_enhancements/page/project_planner/project_planner.js"
MP_JS = APP / "sapphire_maintenance/page/maintenance_planner/maintenance_planner.js"
KIT_DIR = APP / "public/js/planner_kit"
BUNDLE = APP / "public/js/planner_kit.bundle.js"
HOOKS = APP / "hooks.py"
CI = REPO_ROOT / ".github/workflows/ci.yml"
API_README = APP / "api/README.md"
PE_README = APP / "project_enhancements/README.md"
SM_README = APP / "sapphire_maintenance/README.md"

D = datetime.date
TODAY = D(2026, 10, 9)  # a Friday
NOW = datetime.datetime(2026, 10, 9, 7, 0, 0)

# The planner_views methods the pages and the kit call, and every argument they send. The Project
# Planner's own endpoints are test_project_planner_page.API_CONTRACT; these live in their own module.
VIEWS_CONTRACT = {
	"get_person_schedule": {"resource", "user", "start", "days"},
	"get_day_overview": {"date", "group"},
	"get_project_overview": {"project"},
	"get_site_overview": {"project"},
}

_saved_modules = {}
_modules_before = set()
frappe = None
engine = None
routing = None
tracking = None
pp = None
mp = None
views = None


class _Throw(Exception):
	pass


class _PermissionError(Exception):
	pass


class _Doc(dict):
	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None

	def __setattr__(self, name, value):
		self[name] = value


def _getdate(value=None):
	if value is None:
		return TODAY
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, D):
		return value
	return D.fromisoformat(str(value)[:10])


def _throw(message, exc=None, title=None):
	raise (exc or _Throw)(message)


def setUpModule():
	global frappe, engine, routing, tracking, pp, mp, views
	_modules_before.update(sys.modules)
	for name in list(sys.modules):
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements")):
			_saved_modules[name] = sys.modules.pop(name)

	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.getdate = _getdate
	utils.add_days = lambda value, days: _getdate(value) + datetime.timedelta(days=days)
	utils.date_diff = lambda a, b: (_getdate(a) - _getdate(b)).days
	utils.formatdate = lambda value=None, *a, **k: _getdate(value).strftime("%m-%d-%Y")
	utils.nowdate = lambda: str(TODAY)
	utils.now_datetime = lambda: NOW
	utils.flt = lambda value, precision=None: float(value or 0)
	utils.cint = lambda value: int(float(value or 0))
	utils.strip_html_tags = lambda text: re.sub(r"<[^>]+>", "", str(text))
	utils.escape_html = lambda text: (
		str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
	)
	utils.get_url = lambda path="": "https://erp.example.com" + (path or "")
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	frappe.throw = _throw
	frappe.PermissionError = _PermissionError
	frappe.get_traceback = lambda: "Traceback: boom"
	sys.modules.update({"frappe": frappe, "frappe.utils": utils})
	_reset()

	engine = importlib.import_module("erpnext_enhancements.project_enhancements.crew_availability")
	routing = importlib.import_module("erpnext_enhancements.project_enhancements.routing")
	tracking = importlib.import_module("erpnext_enhancements.project_enhancements.planner_tracking")
	mp = importlib.import_module("erpnext_enhancements.api.maintenance_planner")
	pp = importlib.import_module("erpnext_enhancements.api.project_planner")
	views = importlib.import_module("erpnext_enhancements.api.planner_views")


def tearDownModule():
	for name in set(sys.modules) - _modules_before:
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements")):
			sys.modules.pop(name, None)
	for name in list(sys.modules):
		if name == "frappe" or name.startswith("frappe."):
			sys.modules.pop(name, None)
	sys.modules.update(_saved_modules)


# ---------------------------------------------------------------------- the fake site


def _matches(row, filters):
	for key, wanted in (filters or {}).items():
		value = row.get(key)
		if isinstance(wanted, list | tuple) and len(wanted) == 2 and isinstance(wanted[0], str):
			op, operand = wanted
			if op == "in" and value not in operand:
				return False
			if op == "not in" and value in operand:
				return False
			continue
		if value != wanted:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
	frappe.calls.append(("get_all", doctype, filters))
	rows = [_Doc(r) for r in frappe.tables.get(doctype, [])]
	rows = [r for r in rows if _matches(r, filters)]
	limit = kwargs.get("limit_page_length")
	if limit:
		rows = rows[:limit]
	if pluck:
		return [r.get(pluck) for r in rows]
	return rows


def _get_value(doctype, name, field=None, *args, as_dict=False, **kwargs):
	row = None
	if isinstance(name, dict):
		row = next((r for r in frappe.tables.get(doctype, []) if _matches(r, name)), None)
	else:
		row = next((r for r in frappe.tables.get(doctype, []) if r.get("name") == name), None)
	if row is None:
		return None
	if isinstance(field, list | tuple):
		out = _Doc({f: row.get(f) for f in field})
		return out if as_dict else tuple(out.values())
	return row.get(field)


def _exists(doctype, name=None):
	if doctype == "DocType":
		return name not in frappe.missing_doctypes
	if doctype == "Role":
		return name in frappe.site_roles
	return any(r.get("name") == name for r in frappe.tables.get(doctype, []))


def _sql(query, values=None, as_dict=False, **kwargs):
	frappe.sql_log.append((query, values))
	handler = frappe.sql_handler
	return handler(query, values or {}) if handler else []


def _reset():
	frappe.roles = ["Projects User"]
	frappe.get_roles = lambda user=None: list(frappe.roles)
	frappe.session = types.SimpleNamespace(user="nik@example.com")
	frappe.local = types.SimpleNamespace(message_log=[], flags=types.SimpleNamespace())
	frappe.flags = types.SimpleNamespace()
	frappe.tables = {}
	frappe.calls, frappe.errors, frappe.sql_log = [], [], []
	frappe.sql_handler = None
	frappe.missing_columns = set()
	frappe.missing_doctypes = set()
	frappe.site_roles = set()
	frappe.write_ok = True
	frappe.log_error = lambda *a, **k: frappe.errors.append((a, k))
	frappe.get_all = _get_all
	frappe.get_list = _get_all
	frappe.get_cached_doc = lambda doctype, name=None: _Doc({})
	frappe.has_permission = lambda *a, **k: frappe.write_ok
	frappe.get_system_settings = lambda key: "Sunday"
	frappe.db = types.SimpleNamespace(
		has_column=lambda doctype, column: (doctype, column) not in frappe.missing_columns,
		get_value=_get_value,
		exists=_exists,
		sql=_sql,
		get_single_value=lambda *a, **k: None,
	)


def _person(name="RES-00001", label="Austin Healey", user="austin@example.com", **extra):
	row = {
		"name": name,
		"resource_name": label,
		"resource_type": "Employee",
		"employee": "EMP-1",
		"user": user,
		"supplier": None,
		"resource_group": "Field",
		"home_team": "Maintenance",
		"color": "#2563eb",
		"is_active": 1,
	}
	row.update(extra)
	return row


def _engine_resource(row):
	return {
		"name": row["name"],
		"label": row["resource_name"],
		"group": row["resource_group"],
		"home_team": row["home_team"],
		"color": row["color"],
		"user": row["user"],
		"employee": row["employee"],
		"type": row["resource_type"],
	}


def _cell(capacity=8, booked=0, off=None, bookings=None, **extra):
	cell = {
		"capacity": capacity,
		"booked": booked,
		"soft_booked": 0,
		"free": max(capacity - booked, 0),
		"off": off,
		"bookings": bookings or [],
		"conflicts": [],
		"warnings": [],
		"drive_minutes": 0,
		"drive_source": None,
		"long_drive": False,
		"unlocated": 0,
	}
	cell.update(extra)
	return cell


def _task(name="TASK-1", project="PRJ-1", start="2026-10-12", end="2026-10-12", **extra):
	row = _Doc(
		{
			"name": name,
			"subject": f"Subject of {name}",
			"project": project,
			"project_title": "Highlands Plaza",
			"status": "Open",
			"exp_start_date": start,
			"exp_end_date": end,
			"expected_time": 6,
			"color": None,
			"modified": "2026-10-01 10:00:00",
			"custom_start_datetime": None,
			"custom_end_datetime": None,
			"custom_rental_booking": None,
			"custom_rental_task_kind": None,
			"custom_crew_size": 0,
			"custom_tentative": 0,
		}
	)
	row.update(extra)
	return row


def _compute_factory(people, days, tasks=(), calls=None):
	"""A fake engine._compute that records every call (and its google flag)."""

	def compute(start, end, resources=None, exclude=(), extra=(), google=True, equipment=False):
		if calls is not None:
			calls.append({"start": start, "end": end, "resources": resources, "google": google})
		chosen = [p for p in people if resources is None or p["name"] in resources]
		return {
			"resources": [_engine_resource(p) for p in chosen],
			"days": {p["name"]: days.get(p["name"], {}) for p in chosen},
			"user_to_resource": {p["user"]: p["name"] for p in chosen if p.get("user")},
			"settings": dict(engine.DEFAULT_SETTINGS),
			"routes": {},
			"tasks": list(tasks),
			"crews": {t["name"]: [] for t in tasks},
			"todo_users": {},
			"task_hours": {},
		}

	return compute


# ====================================================================== pure helpers


class TestPureHelpers(unittest.TestCase):
	def test_a_day_off_never_names_its_kind_or_reason(self):
		self.assertIsNone(views.off_label(None))
		self.assertEqual(views.off_label("Time off"), "Off")
		self.assertEqual(views.off_label("Half day off"), "Half day off")
		self.assertEqual(views.off_label("Not a work day"), "Not a work day")
		self.assertEqual(views.off_label("Holiday: Thanksgiving"), "Holiday")
		self.assertEqual(views.off_label("Holiday (half day): Christmas Eve"), "Holiday (half day)")
		# Anything the engine never says reads as plain Off, so a leaked reason cannot pass through.
		self.assertEqual(views.off_label("Time off: Medical (surgery)"), "Off")
		self.assertEqual(views.off_label("Sick Leave"), "Off")
		self.assertEqual(views.off_kind("Time off"), "time_off")
		self.assertEqual(views.off_kind("Holiday: X"), "holiday")
		self.assertEqual(views.off_kind("Not a work day"), "not_working")
		self.assertIsNone(views.off_kind(None))

	def test_phone_numbers_become_dialable_or_nothing(self):
		self.assertEqual(views.tel_number("8015551234"), "+18015551234")
		self.assertEqual(views.tel_number("(801) 555-1234"), "+18015551234")
		self.assertEqual(views.tel_number("1-801-555-1234"), "+18015551234")
		self.assertEqual(views.tel_number("+44 20 7946 0958"), "+442079460958")
		self.assertIsNone(views.tel_number("555"))
		self.assertIsNone(views.tel_number(""))
		self.assertIsNone(views.tel_number(None))
		self.assertEqual(views.clean_email("austin@example.com"), "austin@example.com")
		self.assertIsNone(views.clean_email("not an email"))
		self.assertIsNone(views.clean_email('a"b@example.com'))

	def test_the_timeline_is_the_span_or_the_next_eight_weeks(self):
		short = views.timeline_window([("2026-10-12", "2026-10-20"), ("2026-11-02", "2026-11-05")], TODAY)
		self.assertEqual(short, (D(2026, 10, 12), D(2026, 11, 5)))
		# Longer than eight weeks: the next 56 days from the start of this week (Sunday).
		long = views.timeline_window([("2026-09-01", "2026-09-02"), ("2027-03-01", "2027-03-01")], TODAY)
		self.assertEqual(long, (D(2026, 10, 4), D(2026, 11, 28)))
		# A project that has not started yet starts the window on its first task.
		later = views.timeline_window([("2026-12-01", "2026-12-01"), ("2027-06-01", "2027-06-01")], TODAY)
		self.assertEqual(later[0], D(2026, 12, 1))
		self.assertIsNone(views.timeline_window([(None, None)], TODAY))
		window = (D(2026, 10, 4), D(2026, 11, 28))
		self.assertEqual(views.timeline_bar("2026-09-01", "2026-09-02", window), {"outside": "before"})
		self.assertEqual(views.timeline_bar("2027-01-01", "2027-01-02", window), {"outside": "after"})
		bar = views.timeline_bar("2026-10-01", "2026-10-10", window)
		self.assertTrue(bar["clipped_start"])
		self.assertFalse(bar["clipped_end"])
		self.assertEqual(bar["left_pct"], 0.0)
		self.assertAlmostEqual(bar["width_pct"], round(7 / 56 * 100, 2))

	def test_a_day_view_orders_bookings_by_the_route_and_hides_nothing_else(self):
		entry = {
			"date": "2026-10-12",
			"travel": None,
			"items": [
				{"kind": "visit", "ref": "V-2", "label": "Hotel", "arrive": "08:30", "address": "1 Main"},
				{
					"kind": "task",
					"ref": "TASK-1",
					"label": "Dig",
					"arrive": "11:00",
					"project_title": "Plaza",
				},
			],
		}
		cell = _cell(
			booked=6,
			off="Time off: Medical",
			bookings=[
				{
					"kind": "task",
					"ref": "TASK-1",
					"label": "Dig",
					"hours": 3,
					"slot": None,
					"project": "PRJ-1",
				},
				{"kind": "drive", "ref": None, "label": "Driving", "hours": 1, "estimated": True},
				{"kind": "task", "ref": "TASK-9", "label": "Pencil job", "hours": 2, "tentative": True},
				{"kind": "visit", "ref": "V-2", "key": "V-2", "label": "Hotel", "hours": 2},
			],
		)
		view = views.day_view(entry, cell)
		self.assertEqual([b["ref"] for b in view["bookings"]], ["V-2", "TASK-1", "TASK-9", None])
		self.assertEqual(view["bookings"][0]["arrive"], "08:30")
		self.assertEqual(view["bookings"][1]["project_title"], "Plaza")
		self.assertTrue(view["bookings"][2]["tentative"])
		self.assertEqual(view["bookings"][3]["kind"], "drive")
		self.assertEqual(view["off"], "Off")
		self.assertNotIn("Medical", json.dumps(view))
		self.assertEqual(len(view["stops"]), 2)


# ====================================================================== one person


class TestPersonSchedule(unittest.TestCase):
	def setUp(self):
		_reset()
		self.austin = _person()
		frappe.tables["Planner Resource"] = [
			self.austin,
			_person("RES-00002", "Korben Fox", "korben@example.com"),
		]
		frappe.tables["Employee"] = [
			{"name": "EMP-1", "cell_number": "8015551234", "company_email": "austin@sf.com"}
		]
		self.calls = []
		self.days = {
			"RES-00001": {
				"2026-10-11": _cell(capacity=0, off="Not a work day"),
				"2026-10-12": _cell(
					booked=6,
					bookings=[
						{
							"kind": "task",
							"ref": "TASK-1",
							"label": "Dig",
							"project": "PRJ-1",
							"hours": 6,
							"slot": None,
						},
					],
				),
				"2026-10-13": _cell(capacity=0, off="Time off"),
			}
		}
		self.tasks = [_task("TASK-1")]
		self.patches = [
			mock.patch.object(
				engine, "_compute", _compute_factory([self.austin], self.days, self.tasks, self.calls)
			),
			mock.patch.object(routing, "locate", lambda bookings, detail=False: {}),
		]
		for patch in self.patches:
			patch.start()

	def tearDown(self):
		for patch in self.patches:
			patch.stop()

	def test_the_gate_is_both_planners_roles(self):
		frappe.roles = []
		with self.assertRaises(_PermissionError):
			views.get_person_schedule(resource="RES-00001")
		for role in ("Projects User", "Maintenance User", "Maintenance Supervisor", "Projects Manager"):
			frappe.roles = [role]
			self.assertEqual(
				views.get_person_schedule(resource="RES-00001", start="2026-10-11")["resource"], "RES-00001"
			)
		self.assertEqual(views.VIEW_ROLES, pp.PLANNER_ROLES | mp.PLANNER_ROLES)

	def test_a_user_resolves_to_their_planner_resource(self):
		answer = views.get_person_schedule(user="austin@example.com", start="2026-10-11", days=3)
		self.assertEqual(answer["resource"], "RES-00001")
		self.assertEqual(answer["user"], "austin@example.com")
		self.assertEqual(answer["label"], "Austin Healey")
		self.assertEqual((answer["group"], answer["home_team"]), ("Field", "Maintenance"))
		self.assertEqual(self.calls[-1]["resources"], ["RES-00001"])
		unknown = views.get_person_schedule(user="nobody@example.com")
		self.assertIsNone(unknown["resource"])
		self.assertIn("not on the planners' list", unknown["message"])
		self.assertEqual(unknown["days"], [])

	def test_an_inactive_person_has_no_schedule(self):
		self.austin["is_active"] = 0
		answer = views.get_person_schedule(resource="RES-00001")
		self.assertFalse(answer["active"])
		self.assertIn("not active", answer["message"])
		self.assertEqual(self.calls, [])

	def test_the_engine_is_asked_without_google_and_the_range_is_capped(self):
		answer = views.get_person_schedule(resource="RES-00001", start="2026-10-11", days="3")
		self.assertEqual(len(self.calls), 1)
		self.assertIs(self.calls[0]["google"], False)
		self.assertEqual((self.calls[0]["start"], self.calls[0]["end"]), (D(2026, 10, 11), D(2026, 10, 13)))
		self.assertEqual((answer["start"], answer["end"]), ("2026-10-11", "2026-10-13"))
		self.assertTrue(answer["estimate_note"])
		with self.assertRaises(_Throw):
			views.get_person_schedule(resource="RES-00001", days=32)
		with self.assertRaises(_Throw):
			views.get_person_schedule(resource="RES-00001", days=0)
		views.get_person_schedule(resource="RES-00001", days=31)
		self.assertEqual((self.calls[-1]["end"] - self.calls[-1]["start"]).days, 30)

	def test_days_off_contact_and_cards(self):
		answer = views.get_person_schedule(resource="RES-00001", start="2026-10-11", days=3)
		self.assertEqual([d["off"] for d in answer["days"]], ["Not a work day", None, "Off"])
		self.assertEqual(
			answer["contact"], {"phone": "8015551234", "tel": "+18015551234", "email": "austin@sf.com"}
		)
		monday = answer["days"][1]
		self.assertEqual(monday["bookings"][0]["ref"], "TASK-1")
		# The task's full planner card, so the page can drag it out of the drawer.
		card = answer["cards"]["TASK-1"]
		for key in ("name", "start", "end", "crew", "credentials", "equipment", "modified", "movable"):
			self.assertIn(key, card)
		self.assertTrue(card["movable"])

	def test_a_time_off_reason_never_leaks(self):
		self.days["RES-00001"]["2026-10-13"] = _cell(capacity=0, off="Time off: Medical leave (surgery)")
		answer = views.get_person_schedule(resource="RES-00001", start="2026-10-11", days=3)
		text = json.dumps(answer)
		self.assertNotIn("Medical", text)
		self.assertNotIn("surgery", text)
		self.assertEqual(answer["days"][2]["off"], "Off")


# ====================================================================== one day


class TestDayOverview(unittest.TestCase):
	def setUp(self):
		_reset()
		self.people = [
			_person("RES-00003", "Zed Designer", "zed@example.com", resource_group="Design"),
			_person(),
			_person("RES-00002", "Korben Fox", "korben@example.com"),
		]
		frappe.tables["Planner Resource"] = self.people
		self.calls = []
		days = {p["name"]: {"2026-10-12": _cell(booked=2)} for p in self.people}
		self.patches = [
			mock.patch.object(engine, "_compute", _compute_factory(self.people, days, (), self.calls)),
			mock.patch.object(routing, "locate", lambda bookings, detail=False: {}),
		]
		for patch in self.patches:
			patch.start()

	def tearDown(self):
		for patch in self.patches:
			patch.stop()

	def test_one_engine_call_for_everyone_without_google(self):
		answer = views.get_day_overview("2026-10-12")
		self.assertEqual(len(self.calls), 1)
		self.assertIsNone(self.calls[0]["resources"])
		self.assertIs(self.calls[0]["google"], False)
		self.assertEqual(self.calls[0]["start"], self.calls[0]["end"])
		# The panel's order: Field before Design, then by name.
		self.assertEqual(
			[p["label"] for p in answer["people"]], ["Austin Healey", "Korben Fox", "Zed Designer"]
		)
		self.assertEqual(answer["people"][0]["free"], 6)

	def test_a_group_narrows_and_an_empty_group_says_so(self):
		views.get_day_overview("2026-10-12", group="Design")
		self.assertEqual(self.calls[-1]["resources"], ["RES-00003"])
		answer = views.get_day_overview("2026-10-12", group="Subcontractor")
		self.assertIn("Nobody active", answer["note"])
		self.assertEqual(len(self.calls), 1)

	def test_the_gate(self):
		frappe.roles = ["Website User"]
		with self.assertRaises(_PermissionError):
			views.get_day_overview("2026-10-12")


# ====================================================================== one project


def _project_sql(projects, tasks):
	def handler(query, values):
		if "FROM `tabProject` proj" in query:
			return [
				_Doc({"name": name, "project_type": projects[name], "planner_stream": 0})
				for name in values.get("names") or ()
				if name in projects
			]
		if "FROM `tabTask` t" in query and "t.project = %(project)s" in query:
			return [t for t in tasks if t["project"] == values["project"]][: values["limit"]]
		return []

	return handler


class TestProjectOverview(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.tables["Project"] = [
			{
				"name": "PRJ-1",
				"project_name": "Highlands Plaza",
				"customer": "CUST-1",
				"status": "Active",
				"project_type": "Build",
				"custom_project_owner": "EMP-9",
			},
			{"name": "PRJ-INT", "project_name": "Shop cleanup", "project_type": "Internal"},
		]
		frappe.tables["Employee"] = [{"name": "EMP-9", "employee_name": "Clegg Mabey"}]
		frappe.tables["Customer"] = [{"name": "CUST-1", "customer_name": "Highlands HOA"}]
		frappe.tables["Planner Resource"] = [_person()]
		self.tasks = [
			_task("TASK-1", start="2026-10-12", end="2026-10-14"),
			_task("TASK-2", start="2026-10-20", end="2026-10-20", custom_tentative=1),
			_task("TASK-3", start=None, end=None),
		]
		frappe.sql_handler = _project_sql({"PRJ-1": "Build", "PRJ-INT": "Internal"}, self.tasks)
		self.forecasts = []

		def labor_forecast(project, with_cost=False, now=None):
			self.forecasts.append(with_cost)
			people = {
				"RES-00001": {
					"booked": 12.0,
					"actual": 3.0,
					"booked_cost": 300.0,
					"actual_cost": 75.0,
					"rate": 25.0,
					"burdened": False,
				}
			}
			return tracking.forecast_answer(project, people, {"RES-00001": "Austin Healey"}, with_cost, TODAY)

		self.actuals = mock.patch.object(
			pp,
			"get_actuals",
			return_value={"TASK-1": {"planned": 12, "actual": 14, "over_plan": True, "by_person": []}},
		)
		self.patches = [mock.patch.object(tracking, "labor_forecast", labor_forecast), self.actuals]
		self.patches[0].start()
		self.actuals_mock = self.actuals.start()

	def tearDown(self):
		for patch in self.patches:
			patch.stop()

	def test_an_internal_project_is_not_on_the_planner(self):
		answer = views.get_project_overview("PRJ-INT")
		self.assertIs(answer["on_planner"], False)
		self.assertIn("not on the planner", answer["message"])
		self.assertNotIn("tasks", answer)
		with self.assertRaises(_Throw):
			views.get_project_overview("PRJ-NOPE")

	def test_the_overview_has_cards_bars_actuals_and_hours(self):
		answer = views.get_project_overview("PRJ-1")
		self.assertTrue(answer["on_planner"])
		self.assertEqual(
			(answer["title"], answer["customer_name"], answer["pm"]),
			("Highlands Plaza", "Highlands HOA", "Clegg Mabey"),
		)
		self.assertEqual([c["name"] for c in answer["tasks"]], ["TASK-1", "TASK-2"])
		self.assertEqual([c["name"] for c in answer["undated"]], ["TASK-3"])
		self.assertEqual(answer["window"], {"start": "2026-10-12", "end": "2026-10-20"})
		first = answer["tasks"][0]
		self.assertEqual(
			(first["planned_hours"], first["actual_hours"], first["over_plan"]), (12.0, 14.0, True)
		)
		self.assertEqual(first["bar"]["left_pct"], 0.0)
		self.assertTrue(answer["tasks"][1]["tentative"])
		self.assertEqual(
			json.loads(self.actuals_mock.call_args.kwargs["tasks"]), ["TASK-1", "TASK-2", "TASK-3"]
		)
		self.assertFalse(answer["truncated"])

	def test_money_only_for_cost_roles(self):
		frappe.site_roles = set(tracking.COST_ROLES)
		frappe.roles = ["Projects User"]
		forecast = views.get_project_overview("PRJ-1")["forecast"]
		self.assertEqual(self.forecasts[-1], False)
		self.assertEqual(forecast["booked_hours"], 12.0)
		for key in ("forecast_cost", "booked_cost", "actual_cost", "rate_label", "no_rate"):
			self.assertNotIn(key, forecast)
		self.assertNotIn("rate", forecast["by_person"][0])
		self.assertNotIn("cost", json.dumps(forecast).replace("can_see_cost", ""))
		frappe.roles = ["System Manager"]
		forecast = views.get_project_overview("PRJ-1")["forecast"]
		self.assertEqual(self.forecasts[-1], True)
		self.assertEqual(forecast["forecast_cost"], 375.0)

	def test_at_most_three_hundred_tasks(self):
		self.tasks[:] = [_task(f"TASK-{i:03d}", start="2026-10-12", end="2026-10-12") for i in range(305)]
		answer = views.get_project_overview("PRJ-1")
		self.assertTrue(answer["truncated"])
		self.assertEqual(len(answer["tasks"]) + len(answer["undated"]), 300)

	def test_the_engine_is_asked_without_google(self):
		self.actuals.stop()
		self.patches.remove(self.actuals)
		calls = []
		frappe.tables["Task"] = [dict(t) for t in self.tasks]
		frappe.tables["Task Crew Member"] = [
			{
				"parent": "TASK-1",
				"parenttype": "Task",
				"idx": 1,
				"resource": "RES-00001",
				"resource_name": "Austin Healey",
				"hours": 0,
				"is_lead": 1,
			}
		]
		with mock.patch.object(engine, "_compute", _compute_factory([_person()], {}, (), calls)):
			views.get_project_overview("PRJ-1")
		self.assertTrue(calls)
		self.assertTrue(all(call["google"] is False for call in calls), calls)

	def test_the_gate(self):
		frappe.roles = []
		with self.assertRaises(_PermissionError):
			views.get_project_overview("PRJ-1")


# ====================================================================== one maintenance site


def _record(name, plan_date, status="draft", technician="austin@example.com", **extra):
	row = _Doc(
		{
			"name": name,
			"project": "PRJ-SITE",
			"customer": "CUST-1",
			"maintenance_contract": "MNT-CON-1",
			"serial_no": None,
			"visit_label": None,
			"technician": technician,
			"scheduled_visit_date": plan_date,
			"visit_date": plan_date if status == "done" else None,
			"docstatus": 1 if status == "done" else 0,
			"workflow_state": "Approved" if status == "done" else "Draft",
			"completion_percent": 0,
			"has_out_of_range_readings": 0,
			"modified": "2026-10-01 10:00:00",
			"planned_hours": 0,
			"full_day": 0,
			"clock_in_time": None,
			"clock_out_time": None,
			"plan_date": plan_date,
		}
	)
	row.update(extra)
	return row


class TestSiteOverview(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.roles = ["Maintenance User"]
		frappe.tables["Project"] = [
			{
				"name": "PRJ-SITE",
				"project_name": "Red Butte Garden Maintenance Contract",
				"customer": "CUST-1",
			}
		]
		frappe.tables["Customer"] = [{"name": "CUST-1", "customer_name": "Red Butte"}]
		frappe.tables["Sapphire Maintenance Profile"] = [
			{
				"name": "PROF-1",
				"project": "PRJ-SITE",
				"default_technician": "austin@example.com",
				"visit_hours": 3,
				"visit_full_day": 0,
				"access_codes": "1234#",
			}
		]
		frappe.tables["Sapphire Maintenance Contract"] = [
			{"name": "MNT-CON-1", "project": "PRJ-SITE", "status": "Active"}
		]
		frappe.tables["User"] = [{"name": "austin@example.com", "full_name": "Austin Healey"}]
		records = [
			_record("REC-3", "2026-10-15"),
			_record("REC-2", "2026-10-01", status="done"),
			_record("REC-1", "2026-09-01", status="done"),
		]
		frappe.sql_handler = (
			lambda query, values: records if "tabSapphire Maintenance Record" in query else []
		)
		projected = [
			{
				"kind": "projected",
				"key": "MNT-CON-1||2026-11-01",
				"project": "PRJ-SITE",
				"date": "2026-11-01",
				"contract": "MNT-CON-1",
				"crew": [],
				"technician": "austin@example.com",
				"movable": True,
			}
		]
		self.patches = [
			mock.patch.object(mp, "_projections", lambda start, end, today: projected),
			mock.patch.object(mp, "_decorate", lambda cards: None),
			mock.patch.object(mp, "_attach_crews", lambda cards: None),
			mock.patch.object(
				mp,
				"_default_crews",
				lambda projects: {
					"PRJ-SITE": {
						"members": [{"user": "korben@example.com", "name": "Korben Fox", "hours": None}]
					}
				},
			),
		]
		for patch in self.patches:
			patch.start()

	def tearDown(self):
		for patch in self.patches:
			patch.stop()

	def test_recent_upcoming_projected_and_the_profile(self):
		answer = views.get_site_overview("PRJ-SITE")
		self.assertEqual(answer["site"], "Red Butte Garden")
		self.assertEqual([v["name"] for v in answer["upcoming"]], ["REC-3"])
		self.assertEqual([v["name"] for v in answer["recent"]], ["REC-2", "REC-1"])
		self.assertEqual([v["key"] for v in answer["projected"]], ["MNT-CON-1||2026-11-01"])
		self.assertEqual(answer["recent"][0]["status"], "done")
		self.assertEqual(answer["upcoming"][0]["technician_name"], "Austin Healey")
		profile = answer["profile"]
		self.assertEqual((profile["name"], profile["default_technician_name"]), ("PROF-1", "Austin Healey"))
		self.assertEqual([m["user"] for m in profile["crew"]], ["korben@example.com"])
		# The drawer only links to the profile: no access codes or safety notes in the answer.
		self.assertNotIn("1234#", json.dumps(answer))
		self.assertNotIn("access_codes", json.dumps(answer))

	def test_only_the_maintenance_planners_roles(self):
		frappe.roles = ["Projects User"]
		with self.assertRaises(_PermissionError):
			views.get_site_overview("PRJ-SITE")
		self.assertEqual(views.mp.PLANNER_ROLES, mp.PLANNER_ROLES)


# ====================================================================== wiring and the module contract


def _functions(path):
	return {
		n.name: n for n in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)
	}


def _strip(code):
	code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
	return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in code.splitlines())


def _block(code, marker):
	"""The Phase 6A block: from its banner to the next phase's banner (or the end of the file).

	Its house rules (it never saves, so no ``this.send(``; it reads only keys planner_views builds)
	are 6A's own. A later phase appended after it (6B–6D) writes through ``send`` on purpose and reads
	its own endpoints' keys, and has its own suite pinning its own rules.
	"""
	start = code.index(marker)
	end = code.find("\n// ======================================================================", start + len(marker))
	return code[start:] if end < 0 else code[start:end]


PP_MARKER = "// ====================================================================== Phase 6A"
MP_MARKER = "// ====================================================================== Phase 6A"


class TestViewsModule(unittest.TestCase):
	def test_every_endpoint_is_a_plain_read_with_the_arguments_sent(self):
		functions = _functions(VIEWS)
		for method, args in VIEWS_CONTRACT.items():
			node = functions[method]
			params = {a.arg for a in node.args.args + node.args.kwonlyargs}
			self.assertTrue(args <= params, f"{method} lacks {sorted(args - params)}")
			decorators = [ast.unparse(d) for d in node.decorator_list]
			# A read: whitelisted, and not POST-only.
			self.assertIn("frappe.whitelist()", decorators, method)

	def test_the_module_is_tab_indented_and_documented(self):
		source = VIEWS.read_text(encoding="utf-8")
		self.assertNotIn("\n    def ", source)
		self.assertIn("\tif ", source)
		self.assertNotIn("\r", source)
		readme = API_README.read_text(encoding="utf-8")
		self.assertIn("planner_views.py", readme)
		for method in VIEWS_CONTRACT:
			self.assertIn(method, readme)
		self.assertIn("planner_views", readme.split("**Tab-indented**")[1].split("Every other file")[0])

	def test_the_pages_and_the_kit_call_only_contract_methods_with_contract_arguments(self):
		calls = {}
		texts = {
			"pp": PP_JS.read_text(encoding="utf-8"),
			"mp": MP_JS.read_text(encoding="utf-8"),
			"kit": (KIT_DIR / "peeks.js").read_text(encoding="utf-8"),
		}
		for text in texts.values():
			for method in re.findall(r"planner_views\.(\w+)", _strip(text)):
				calls.setdefault(method, set())
		self.assertIn("get_project_overview", texts["pp"])
		self.assertIn("get_site_overview", texts["mp"])
		self.assertTrue(set(calls) <= set(VIEWS_CONTRACT), calls)
		peeks = texts["kit"]
		for key in ("start", "days", "resource", "user"):
			self.assertIn(f"args.{key}" if key in ("resource", "user") else key, peeks)
		self.assertIn("{ date: state.date }", peeks)
		self.assertIn("args.group = opts.group", peeks)
		self.assertIn("args: { project }", texts["pp"])
		self.assertIn("args: { project }", texts["mp"])

	def test_people_days_passes_google_through(self):
		source = (APP / "api/project_planner.py").read_text(encoding="utf-8")
		self.assertIn("def _people_days(start, end, resources=None, google=True):", source)
		self.assertIn("engine._compute(start, end, resources, google=google)", source)
		calls = re.findall(r"pp\._people_days\(([^)]*)\)", VIEWS.read_text(encoding="utf-8"))
		self.assertEqual(len(calls), 2)
		for args in calls:
			self.assertIn("google=False", args)

	def test_the_suite_has_its_own_ci_step_and_the_docs_name_the_kit(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_planner_phase6a -v", ci)
		readme = PE_README.read_text(encoding="utf-8")
		self.assertIn("### Planner kit", readme)
		for name in ("drawer", "toast", "menu", "legend", "hint", "hover_glow", "panel", "peeks", "escape"):
			self.assertIn(name, readme)
		self.assertIn("Phase 6A", SM_README.read_text(encoding="utf-8"))


class TestKitFiles(unittest.TestCase):
	def test_the_bundle_exists_and_only_the_planners_load_it(self):
		self.assertTrue(BUNDLE.exists())
		for name in (
			"escape",
			"history",
			"drawer",
			"panel",
			"toast",
			"menu",
			"legend",
			"hint",
			"glow",
			"peeks",
			"styles",
		):
			self.assertTrue((KIT_DIR / f"{name}.js").exists(), name)
			self.assertIn(f'from "./planner_kit/{name}.js"', BUNDLE.read_text(encoding="utf-8"))
		hooks = HOOKS.read_text(encoding="utf-8")
		self.assertNotIn("planner_kit", hooks.split("app_include_js")[1].split("]")[0])
		for path, constant in ((PP_JS, "PP6A"), (MP_JS, "MP6A")):
			code = path.read_text(encoding="utf-8")
			self.assertIn('kit: "planner_kit.bundle.js"', code)
			self.assertIn(f"frappe.require({constant}.kit, () => this.p6a_ready());", code)
			self.assertIn("window.planner_kit", code)

	def test_back_is_decided_before_the_router_routes(self):
		history = (KIT_DIR / "history.js").read_text(encoding="utf-8")
		self.assertIn("env.gate_router(guard.on_router_pop)", history)
		self.assertIn("guard.window_event_works()", history)
		self.assertIn('win.addEventListener("popstate", guard.on_late_pop)', history)
		bundle = BUNDLE.read_text(encoding="utf-8")
		gate = bundle[bundle.index("gate_router: (decide) => {") : bundle.index("hide_open_dialog: () => {")]
		for needle in (
			"const original = router.route;",
			"const ev = window.event;",
			'if (ev && ev.type === "popstate" && !ev.__planner_kit_seen) {',
			"if (skip) return Promise.resolve();",
			"return original.apply(this, arguments);",
			"router.route = gated;",
		):
			self.assertIn(needle, gate)
		self.assertIn('pushState({ [HISTORY_KEY]: id }, "")', history)
		# It never writes an address of its own, and it never calls the router.
		self.assertNotRegex(_strip(history), r"pushState\([^)]*,\s*\"\",\s*\S")
		self.assertNotIn("frappe.router.route(", _strip(history))
		bundle = BUNDLE.read_text(encoding="utf-8")
		self.assertIn('frappe.router.on("change", fn)', bundle)
		self.assertIn("frappe.route_flags.replace_route = true", bundle)

	def test_the_kit_never_publishes_a_frappe_global_and_its_styles_are_namespaced(self):
		css = (KIT_DIR / "styles.js").read_text(encoding="utf-8")
		classes = set(re.findall(r"\.([a-zA-Z][\w-]*)", css.split("export const CSS = `")[1]))
		self.assertTrue(classes)
		self.assertEqual({c for c in classes if not c.startswith("pk-")}, {"form-layout", "frappe-control"})
		self.assertIn("@media (max-width:767px)", css)
		for path in KIT_DIR.glob("*.js"):
			text = path.read_text(encoding="utf-8")
			self.assertNotIn("\r", text, path.name)
			for forbidden in ("draggable", "dragstart", "dataTransfer", "eval(", "new Function"):
				self.assertNotIn(forbidden, _strip(text), f"{path.name}: {forbidden}")
		hint = (KIT_DIR / "hint.js").read_text(encoding="utf-8")
		for accessor in ("store.getItem", "store.setItem"):
			before = hint[: hint.index(accessor)]
			self.assertGreater(before.rfind("try {"), before.rfind("} catch"), accessor)


class TestPages(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.pp = PP_JS.read_text(encoding="utf-8")
		cls.mp = MP_JS.read_text(encoding="utf-8")
		cls.pp_block = _block(cls.pp, PP_MARKER)
		cls.mp_block = _block(cls.mp, MP_MARKER)

	def test_the_hooks_are_one_line_each(self):
		for line in (
			"this.init_phase6a();",
			"this.render_phase6a();",
			'if (el.closest(".pk-drawer")) return this.p6a_drag_source(el);',
			"this.p6a_edge_scroll(x, y);",
			"return this.p6a_undo_toast(snapshot, message);",
			"this.p6a_close_toast();",
			"links.push(...this.p6a_links(card));",
			"if (this.p6a_action(action, card)) return;",
		):
			self.assertIn(line, self.pp, line)
			self.assertIn(line, self.mp, line)
		self.assertIn("this.p6a_route(() => frappe.set_route(PP.route, view, anchor), true);", self.pp)
		self.assertIn(
			"this.p6a_route(() => frappe.set_route(PP.route, PP.route_view, resource, ymd), false);", self.pp
		)
		self.assertIn("this.p6a_route(() => frappe.set_route(PP.route, PP3B.my_week, ymd), false);", self.pp)
		self.assertIn("this.p6a_route(() => frappe.set_route(MP.route, view, anchor), true);", self.mp)
		self.assertIn(
			'const dialog = this.p6a_dialog({ title: card.subject || card.name, fields, size: "large" }, card);',
			self.pp,
		)
		self.assertIn(
			"const dialog = this.p6a_dialog({ title: card.site || card.site_full || card.name, fields }, card);",
			self.mp,
		)
		self.assertIn("Object.assign(ProjectPlanner.prototype, PP6A_METHODS);", self.pp)
		self.assertIn("Object.assign(MaintenancePlanner.prototype, MP6A_METHODS);", self.mp)

	def test_every_name_opens_the_person_peek(self):
		pp, mp = self.pp_block, self.mp_block
		for needle in (
			'root.querySelectorAll(".pp-person[data-resource]")',
			'el.setAttribute("data-pk-person", key(el.getAttribute("data-resource")))',
			'el.querySelectorAll(".pp-badges .pp-badge:not(.pp-more)")',
			'badge.setAttribute("data-pk-person", key(member.resource))',
			'target.closest(".pp-route-title") && this.route_resource',
			'const person = target.closest("[data-pk-person]");',
			'this.p6a_open_person(decodeURIComponent(person.getAttribute("data-pk-person")))',
		):
			self.assertIn(needle, pp, needle)
		for needle in (
			'root.querySelectorAll(".mp-person[data-user]")',
			'el.setAttribute("data-pk-person", key(el.getAttribute("data-user")))',
			'el.querySelectorAll(".mp-techs .mp-tech")',
			'el.querySelectorAll(".mp-off-badges .mp-tech")',
			'this.p6a_open_person(decodeURIComponent(person.getAttribute("data-pk-person")))',
		):
			self.assertIn(needle, mp, needle)
		# The click is taken in the capture phase, before the card's own click opens the task.
		self.assertIn('root.addEventListener("click", (e) => this.p6a_click(e), true);', pp)
		self.assertIn('root.addEventListener("click", (e) => this.p6a_click(e), true);', mp)
		# The Project Planner names people by resource, the Maintenance Planner by user.
		self.assertIn("kit.peeks.person({\n\t\t\tresource,", pp)
		self.assertIn("kit.peeks.person({\n\t\t\tuser,", mp)

	def test_the_undo_toast_comes_from_push_undo_and_replaces_the_alert(self):
		for code, body_end in ((self.pp, "update_undo_button() {"), (self.mp, "update_undo_button() {")):
			push = code[code.index("push_undo(snapshot, message) {") : code.index(body_end)]
			self.assertIn("return this.p6a_undo_toast(snapshot, message);", push)
		self.assertIn(
			"const toasted = opts.snapshot ? this.push_undo(opts.snapshot, opts.message) : false;", self.pp
		)
		self.assertIn("if (opts.message && !toasted) frappe.show_alert(", self.pp)
		self.assertIn("toasted = this.push_undo(", self.mp)
		self.assertIn(
			'if (message && !toasted) frappe.show_alert({ message, indicator: "green" }, 5);', self.mp
		)
		for block in (self.pp_block, self.mp_block):
			toast = block[
				block.index("p6a_undo_toast(snapshot, message) {") : block.index("p6a_close_toast() {")
			]
			self.assertIn('action_label: __("Undo")', toast)
			# Only the change the toast announced is undone.
			self.assertIn("this.undo_stack[this.undo_stack.length - 1] !== snapshot", toast)
			self.assertIn("this.undo();", toast)

	def test_a_drawer_booking_reaches_the_pages_own_drag(self):
		for block, kind in ((self.pp_block, "task"), (self.mp_block, "visit")):
			source = block[block.index("p6a_drag_source(el) {") : block.index("p6a_edge_scroll(x, y) {")]
			self.assertIn('el.closest("[data-pk-drag]")', source)
			self.assertIn(f'item.getAttribute("data-pk-kind") !== "{kind}"', source)
			self.assertIn("!card.movable || card.saving", source)
			self.assertIn('return { kind: "card", el: item, card', source.replace("\n\t\t\t", " "))
			self.assertIn('kit.drawer.element().addEventListener("pointerdown"', block)
			self.assertIn("this.on_down(e);", block)
		self.assertIn("from_resource:", self.pp_block)
		self.assertIn("from_user:", self.mp_block)

	def test_the_card_editor_is_a_side_panel_with_the_same_fields(self):
		for block in (self.pp_block, self.mp_block):
			dialog = block[block.index("p6a_dialog(opts, card) {") : block.index("p6a_links(card) {")]
			self.assertIn("if (!kit) return new frappe.ui.Dialog(opts);", dialog)
			self.assertIn("kit.panel(", dialog)
			self.assertIn("Object.assign({}, opts,", dialog)
			self.assertIn("reopen:", dialog)
		# After a load the open panel is reopened from its new card, so its `modified` lock is fresh.
		self.assertIn("this.p6a_signature(fresh) !== this.p6a_signature(p6a.panel_card)", self.pp_block)
		self.assertIn("this.p6a_signature(fresh) !== this.p6a_signature(p6a.panel_card)", self.mp_block)
		panel = (KIT_DIR / "panel.js").read_text(encoding="utf-8")
		for needle in (
			"new env.frappe.ui.FieldGroup(",
			"set_primary_action(label, fn)",
			"this.$wrapper = env.$(this.container);",
			"const values = this.get_values();",
			"if (!values) return;",
		):
			self.assertIn(needle, panel)

	def test_the_page_reads_only_keys_the_endpoints_build(self):
		source = VIEWS.read_text(encoding="utf-8")
		build = (APP / "api/project_planner.py").read_text(encoding="utf-8")

		def built(key):
			return f'"{key}"' in source or f'"{key}"' in build or re.search(rf"\b{key}\s*=", source)

		read = set()
		for block in (self.pp_block, self.mp_block):
			read |= set(re.findall(r"\bdata\.(\w+)", block))
			read |= set(re.findall(r"\bvisit\.(\w+)", block))
			read |= set(re.findall(r"\bbar\.(\w+)", block))
			read |= set(re.findall(r"\bprofile\.(\w+)", block))
		peeks = (KIT_DIR / "peeks.js").read_text(encoding="utf-8")
		read |= set(re.findall(r"\b(?:day|booking|stop|contact|person)\.(\w+)(?![\w(])", peeks))
		read |= set(re.findall(r"\bdata\.(\w+)", peeks))
		page_only = {"can_edit", "length", "technicians", "push", "join"}
		missing = sorted(key for key in read - page_only if not built(key))
		self.assertEqual(missing, [])

	def test_hover_glow_sticky_headers_legend_and_hints(self):
		for block, prefix in ((self.pp_block, "pp"), (self.mp_block, "mp")):
			self.assertIn('kit.hover_glow(this.$body[0], "person")', block)
			self.assertIn('setAttribute("data-pk-persons"', block)
			self.assertIn(f".{prefix}-crew-view .{prefix}-crew-head", block)
			self.assertIn("position:sticky;top:0", block)
			self.assertIn(f".{prefix}-week .{prefix}-grid{{max-height:", block)
			self.assertIn("kit.legend(", block)
			self.assertIn("kit.hint(key, __(text), {", block)
			self.assertIn(f'class="btn btn-default btn-sm {prefix}-p6a-help">?</button>', block)
			style = block[block.index("_STYLE = `") : block.index("`;", block.index("_STYLE = `"))]
			classes = set(re.findall(r"\.([a-zA-Z][\w-]*)", style))
			self.assertEqual({c for c in classes if not c.startswith(f"{prefix}-")}, set(), classes)
		self.assertIn('kit.hover_glow(this.$body[0], "project")', self.pp_block)
		self.assertIn('el.setAttribute("data-pk-projects"', self.pp_block)

	def test_house_rules(self):
		for block in (self.pp_block, self.mp_block):
			bare = _strip(block)
			for forbidden in (
				"localStorage",
				"pushState",
				"replaceState",
				"window.location",
				"location.href",
				"history.",
				"draggable",
				"dragstart",
				"dataTransfer",
				'type: "GET"',
				"frappe.xcall",
				"this.send(",
			):
				self.assertNotIn(forbidden, bare, forbidden)
			raw = [
				line.strip()
				for line in block.splitlines()
				if "<" in line
				and re.search(
					r"\$\{(?:card|item|data|visit|member|row|booking|forecast|project|profile)\.\w+", line
				)
			]
			self.assertEqual(raw, [])
			# American English in what people read.
			for british in ("colour", "cancelled", "labour", "centre", "behaviour"):
				self.assertNotIn(british, block.lower(), british)
		self.assertEqual(self.mp.count("localStorage"), 2)
		self.assertEqual(self.pp.count("window.localStorage"), 2)

	def test_no_method_is_defined_twice(self):
		for code in (self.pp, self.mp):
			names = re.findall(r"^\t(?:async\s+)?([A-Za-z_]\w*)\s*\([^)]*\)\s*\{\s*$", code, re.M)
			names = [n for n in names if n not in {"if", "for", "while", "switch", "catch", "function"}]
			self.assertEqual(sorted({n for n in names if names.count(n) > 1}), [])


# ====================================================================== the kit under node


def _kit_source():
	"""The kit's pure modules as one plain script: imports dropped, `export` taken off."""
	parts = []
	for name in ("escape", "history", "legend", "peeks"):
		text = (KIT_DIR / f"{name}.js").read_text(encoding="utf-8")
		text = re.sub(r"^import .*?;\s*$", "", text, flags=re.M)
		text = re.sub(r"^export (const|function|class) ", r"\1 ", text, flags=re.M)
		parts.append(text)
	return "\n".join(parts)


KIT_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(0, "utf8");
const ctx = { console };
vm.createContext(ctx);
vm.runInContext(source + "\nthis.kit = { escape, safe_color, glow_keys, contact_href, legend_html, person_week_html, day_overview_html, day_state, create_guard, HISTORY_KEY };", ctx);
const kit = ctx.kit;
const out = {};

out.escape = kit.escape(`<b a="1" b='2'>&\``);
out.escape_null = kit.escape(null);
out.color_ok = kit.safe_color("#12ab34", "x");
out.color_bad = kit.safe_color("red;background:url(x)", "#000");
out.keys = kit.glow_keys(["RES-1", "a b", "RES-1", "", null]);
out.href = [kit.contact_href("tel", "+18015551234"), kit.contact_href("tel", "801-555"), kit.contact_href("mailto", "a@b.co"), kit.contact_href("mailto", "x\" onmouseover=\"y@b.co")];

out.legend = kit.legend_html([
	{ title: "Chips <i>", note: "n&", items: [
		{ sample_html: '<span class="pp-chip pp-red">Conflict</span>', text: "Over <script>" },
		{ chip: { text: "Pen<cil>", tone: "evil" }, text: "t" },
		{ swatch: { color: "javascript:1", style: "wavy", fill: "#fff" }, text: "s" },
		{ text: "" },
	] },
	{ title: "Empty", items: [] },
]);

const data = {
	resource: "RES-1", user: "a@x.com", label: "Austin <Healey>", group: "Field", home_team: "Maintenance",
	today: "2026-10-12", estimate_note: "Estimates.",
	contact: { tel: "+18015551234", phone: "801 555 1234", email: "austin@sf.com" },
	days: [
		{ date: "2026-10-12", capacity: 8, booked: 10, free: 0, soft_booked: 0, off: null, conflicts: ["Over by 2h"], warnings: [],
			drive_minutes: 70, long_drive: true,
			bookings: [
				{ kind: "task", ref: "TASK-1", key: "TASK-1", label: "Dig <now>", project: "PRJ-1", project_title: "Plaza", hours: 6, slot: ["09:00", "11:00"], arrive: "09:00" },
				{ kind: "visit", ref: "V-1", key: "V-1", label: "Hotel", hours: 2 },
				{ kind: "drive", label: "Driving", hours: 1.17, estimated: true },
			],
			stops: [{ kind: "task", ref: "TASK-1", label: "Dig <now>", arrive: "09:00", address: "1 Main", maps_url: "https://www.google.com/maps/search/?api=1&query=1+Main", crew: ["Korben"] },
				{ kind: "visit", ref: "V-1", label: "Hotel", address: "2 Elm", maps_url: "javascript:alert(1)" }] },
		{ date: "2026-10-13", capacity: 0, booked: 0, free: 0, off: "Off", conflicts: [], bookings: [], stops: [] },
	],
};
const drag_calls = [];
out.person = kit.person_week_html(data, { can_drag: (b, day, person, d) => { drag_calls.push([b.ref, day.date, person.resource, !!d]); return b.kind === "task"; }, data });
out.drag_calls = drag_calls;
out.person_message = kit.person_week_html({ message: "Not on the list <x>", contact: {} }, {});
out.states = [
	kit.day_state({ capacity: 8, booked: 2, free: 6 }),
	kit.day_state({ capacity: 8, booked: 7, free: 1, soft_booked: 2 }),
	kit.day_state({ capacity: 8, booked: 10, free: 0 }),
	kit.day_state({ capacity: 0, booked: 0, off: "Holiday" }),
	kit.day_state({ capacity: 0, booked: 3, off: "Off" }),
];
out.day = kit.day_overview_html({ estimate_note: "E", people: [
	{ resource: "RES-1", user: "a@x.com", label: "Austin <H>", capacity: 8, booked: 2, free: 6, drive_minutes: 30, bookings: [
		{ kind: "task", ref: "TASK-1", key: "TASK-1", label: "Dig", hours: 2 },
		{ kind: "travel", ref: "TRIP-1", label: "Denver trip", hours: 8 }],
		stops: [{ kind: "task", ref: "TASK-1", label: "Dig", arrive: "08:30" }] },
] }, { person_key: (p) => p.user, can_drag: () => false });
out.day_note = kit.day_overview_html({ note: "Nobody <active>" }, {});

// ---------------------------------------------------------------- history.js against a fake browser
function browser(opts) {
	opts = opts || {};
	const listeners = [];
	const timers = [];
	const tasks = [];
	const entries = [{ state: null, url: "/desk/project-planner/week/2026-10-12" }];
	let index = 0;
	const log = [];
	const win = {
		event: undefined,
		Event: function (type) { this.type = type; },
		addEventListener(type, fn, capture) { listeners.push({ type, fn, capture: !!capture }); },
		removeEventListener(type, fn, capture) {
			const i = listeners.findIndex((l) => l.type === type && l.fn === fn && l.capture === !!capture);
			if (i >= 0) listeners.splice(i, 1);
		},
		dispatchEvent(event) { fire(event); },
		history: {
			get state() { return entries[index].state; },
			pushState(state, title, url) { log.push(["push", state]); entries.splice(index + 1); entries.push({ state, url: url || entries[index].url }); index += 1; },
			replaceState(state, title, url) { log.push(["replace", state]); entries[index] = { state, url: url || entries[index].url }; },
			back() { log.push(["back"]); tasks.push(() => traverse(-1)); },
			forward() { log.push(["forward"]); tasks.push(() => traverse(1)); },
		},
	};
	// A browser runs the listeners on window in the order they were added, capture or not (what
	// Chromium 152 does; see history.js), with window.event set to the event meanwhile.
	function fire(event) {
		for (const l of listeners.filter((x) => x.type === event.type)) {
			win.event = opts.no_window_event ? undefined : event;
			try { l.fn(event); } finally { win.event = undefined; }
		}
		return event;
	}
	function traverse(step) {
		const next = index + step;
		if (next < 0 || next >= entries.length) return;
		index = next;
		fire({ type: "popstate", state: entries[index].state });
	}
	const router = { route: "project-planner/week/2026-10-12", routed: 0, replace: false, change: [] };
	// frappe.router.route as router.js has it: re-render the address on screen, then "change".
	let route_impl = function () {
		router.routed += 1;
		router.route = entries[index].url.replace(/^\/desk\//, "");
		router.change.forEach((fn) => fn());
	};
	// frappe's own popstate listener, added at boot (first): it calls frappe.router.route().
	win.addEventListener("popstate", () => route_impl());
	const env = {
		win,
		// Only short timers fire inside run(); a long one (the 1.5 s settle) would land much later.
		set_timeout: (fn, ms) => { timers.push({ fn, ms: Number(ms) || 0 }); return timers.length; },
		clear_timeout: (id) => { if (timers[id - 1]) timers[id - 1].dead = true; },
		route: () => router.route,
		page: () => router.route.split("/")[0],
		set_replace: (on) => { router.replace = !!on; },
		on_route_change: (fn) => router.change.push(fn),
		hide_open_dialog: () => { router.hidden = (router.hidden || 0) + 1; },
		// The bundle's wrapper, the same shape: during a popstate the kit decides first.
		gate_router: (decide) => {
			if (opts.no_router) return false;
			const original = route_impl;
			route_impl = function () {
				const ev = win.event;
				if (ev && ev.type === "popstate" && !ev.__planner_kit_seen && decide(ev) === true) return;
				return original.apply(this, arguments);
			};
			return true;
		},
	};
	const run = () => {
		for (let i = 0; i < 20; i++) {
			const t = timers.filter((x) => x && !x.dead && !x.done && x.ms < 1000);
			const q = tasks.splice(0);
			if (!t.length && !q.length) break;
			t.forEach((x) => { x.done = true; x.fn(); });
			q.forEach((fn) => fn());
		}
	};
	// set_route as frappe v16 does it: replace when the flag is set, else push; then route.
	const set_route = (route) => {
		if (router.replace) win.history.replaceState(null, null, "/desk/" + route);
		else win.history.pushState(null, null, "/desk/" + route);
		router.route = route;
		router.change.forEach((fn) => fn());
	};
	return { win, env, router, entries, log, run, set_route, at: () => index, back: () => traverse(-1), forward: () => traverse(1) };
}

function overlay(name, closed, reopen) {
	return { kind: name, close: (how) => closed.push([name, how]), reopen };
}

const h = {};
{
	// Back while a drawer is open closes it and the router never sees the popstate.
	const b = browser();
	const guard = kit.create_guard(b.env).install();
	const closed = [];
	const drawer = overlay("drawer", closed);
	guard.open(drawer);
	guard.open(drawer); // a second drawer shares the entry
	h.pushes = b.log.filter((e) => e[0] === "push").length;
	b.back();
	h.back_closed = closed.slice();
	h.back_routed = b.router.routed;
	h.back_stack = guard.stack().length;
	h.back_hidden_dialog = b.router.hidden || 0;
	// Forward onto the closed drawer's entry opens it again, on the same screen.
	const reopened = [];
	const d2 = overlay("drawer2", [], () => { reopened.push(1); guard.open(d2); });
	guard.open(d2);
	b.run();
	b.back();
	b.forward();
	h.forward_reopened = reopened.length;
	h.forward_routed = b.router.routed;
	h.forward_stack = guard.stack().length;
}
{
	// Closing any other way steps back off the entry, once, and that popstate is the kit's.
	const b = browser();
	const guard = kit.create_guard(b.env).install();
	const closed = [];
	const drawer = overlay("drawer", closed);
	guard.open(drawer);
	guard.close(drawer, "user");
	h.release_replace_flag = b.router.replace;
	b.run();
	h.release_log = b.log.map((e) => e[0]);
	h.release_at = b.at();
	h.release_routed = b.router.routed;
	h.release_flag_after = b.router.replace;
}
{
	// "dialog.hide(); frappe.set_route(...)": the route replaces the kit's entry; nothing to step off.
	const b = browser();
	const guard = kit.create_guard(b.env).install();
	const drawer = overlay("drawer", []);
	guard.open(drawer);
	guard.close(drawer, "user");
	b.set_route("Form/Task/TASK-1");
	b.run();
	h.navigate_log = b.log.map((e) => e[0]);
	h.navigate_entries = b.entries.map((e) => e.url);
	h.navigate_at = b.at();
}
{
	// The planner's own route change keeps the drawer open over the new week, with a fresh entry.
	const b = browser();
	const guard = kit.create_guard(b.env).install();
	const closed = [];
	const drawer = overlay("drawer", closed);
	guard.open(drawer);
	guard.route(() => b.set_route("project-planner/week/2026-10-19"), true);
	h.keep_flag_after = b.router.replace;
	h.keep_entries = b.entries.map((e) => [e.url, !!(e.state && e.state[kit.HISTORY_KEY])]);
	h.keep_closed = closed.slice();
	b.back();
	h.keep_back_closed = closed.slice();
	h.keep_back_routed = b.router.routed;
	b.back();
	h.keep_second_back_routed = b.router.routed;
}
{
	// Without keep, the drawer closes and the route replaces its entry.
	const b = browser();
	const guard = kit.create_guard(b.env).install();
	const closed = [];
	const drawer = overlay("drawer", closed);
	guard.open(drawer);
	guard.route(() => b.set_route("project-planner/route/RES-1/2026-10-12"), false);
	b.run();
	h.leave_closed = closed.slice();
	h.leave_entries = b.entries.map((e) => e.url);
}
{
	// Somebody else's route change (a sidebar link) closes the overlays.
	const b = browser();
	const guard = kit.create_guard(b.env).install();
	const closed = [];
	guard.open(overlay("drawer", closed));
	b.set_route("todo");
	h.other_closed = closed.slice();
}
{
	// A menu replaced by a drawer hands its entry over instead of stacking a second one.
	const b = browser();
	const guard = kit.create_guard(b.env).install();
	const menu = overlay("menu", []);
	guard.open(menu);
	guard.close(menu, "user");
	guard.open(overlay("drawer", []));
	b.run();
	h.handover_log = b.log.map((e) => e[0]);
	h.handover_entries = b.entries.length;
}
{
	// Where window.event is not set during a dispatch, or there is no router to wrap, the kit
	// takes no entry at all (Esc and the close button still work).
	for (const [name, flags] of [["no_event", { no_window_event: true }], ["no_router", { no_router: true }]]) {
		const b = browser(flags);
		const guard = kit.create_guard(b.env).install();
		guard.open(overlay("drawer", []));
		h[name] = [guard.enabled, b.log.length];
	}
}
out.history = h;
process.stdout.write(JSON.stringify(out));
"""


class TestKitUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		if not node:
			raise unittest.SkipTest("node is not on PATH")
		cls.node = node
		result = subprocess.run(
			[node, "-e", KIT_HARNESS],
			input=_kit_source(),
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
		if result.returncode != 0:
			raise AssertionError(result.stderr)
		cls.out = json.loads(result.stdout)

	def test_the_javascript_parses(self):
		for path in [*sorted(KIT_DIR.glob("*.js")), BUNDLE, PP_JS, MP_JS]:
			result = subprocess.run(
				[self.node, "--check", str(path)], capture_output=True, text=True, timeout=60
			)
			self.assertEqual(result.returncode, 0, f"{path.name}: {result.stderr}")

	def test_escape_and_the_small_helpers(self):
		out = self.out
		self.assertEqual(out["escape"], "&lt;b a=&quot;1&quot; b=&#39;2&#39;&gt;&amp;&#96;")
		self.assertEqual(out["escape_null"], "")
		self.assertEqual((out["color_ok"], out["color_bad"]), ("#12ab34", "#000"))
		self.assertEqual(out["keys"], "RES-1 a%20b")
		self.assertEqual(out["href"], ["tel:+18015551234", "", "mailto:a@b.co", ""])

	def test_the_legend_renderer(self):
		legend = self.out["legend"]
		self.assertIn("Chips &lt;i&gt;", legend)
		self.assertIn("n&amp;", legend)
		self.assertIn("Over &lt;script&gt;", legend)
		# The page's own sample markup goes through as written; everything else is escaped.
		self.assertIn('<span class="pp-chip pp-red">Conflict</span>', legend)
		self.assertIn('class="pk-chip pk-chip-plain">Pen&lt;cil&gt;</span>', legend)
		self.assertIn("border-left-color:#94a3b8;border-style:solid;background:#fff;", legend)
		self.assertNotIn("javascript", legend)
		self.assertNotIn("Empty", legend)
		self.assertEqual(legend.count('class="pk-legend-item"'), 3)

	def test_the_person_week(self):
		html = self.out["person"]
		self.assertIn("Dig &lt;now&gt;", html)
		self.assertNotIn("<now>", html)
		self.assertIn('href="tel:+18015551234"', html)
		self.assertIn('href="sms:+18015551234"', html)
		self.assertIn('href="mailto:austin@sf.com"', html)
		# Only the bookings the page owns carry drag attributes; the drive is never one.
		self.assertEqual(html.count('data-pk-drag="1"'), 1)
		self.assertIn('data-pk-ref="TASK-1"', html)
		self.assertIn('data-pk-resource="RES-1" data-pk-user="a@x.com"', html)
		self.assertEqual(
			self.out["drag_calls"],
			[["TASK-1", "2026-10-12", "RES-1", True], ["V-1", "2026-10-12", "RES-1", True]],
		)
		self.assertIn("Over 2h", html)
		self.assertIn("1h 10m drive", html)
		self.assertIn("Long drive", html)
		self.assertIn("Conflict: Over by 2h", html)
		# Today's stops in driving order, a Maps link only to Google.
		self.assertIn("Stops on Mon, Oct 12", html)
		self.assertIn('data-pk-full-route="2026-10-12"', html)
		self.assertIn('href="https://www.google.com/maps/search/?api=1&amp;query=1+Main"', html)
		self.assertNotIn("javascript:", html)
		self.assertIn("With Korben", html)
		self.assertIn("Estimates.", html)
		self.assertIn("pk-day-off", html)
		self.assertIn("Not on the list &lt;x&gt;", self.out["person_message"])

	def test_day_states_read_like_the_planners(self):
		self.assertEqual(
			[(s["tone"], s["text"]) for s in self.out["states"]],
			[
				("green", "6h free"),
				("amber", "1h free · +2h pencil"),
				("red", "Over 2h"),
				("off", "Holiday"),
				("red", "Off, 3h booked"),
			],
		)

	def test_the_day_overview(self):
		html = self.out["day"]
		self.assertIn('data-pk-person-open="a@x.com"', html)
		self.assertIn("Austin &lt;H&gt;", html)
		self.assertIn("2h of 8h booked · 30m drive", html)
		self.assertIn("Denver trip", html)
		self.assertNotIn('data-pk-drag="1"', html)
		self.assertIn("Nobody &lt;active&gt;", self.out["day_note"])

	def test_back_closes_a_drawer_without_the_router(self):
		h = self.out["history"]
		self.assertEqual(h["pushes"], 1)
		self.assertEqual(h["back_closed"], [["drawer", "back"]])
		self.assertEqual(h["back_routed"], 0)
		self.assertEqual(h["back_stack"], 0)
		self.assertEqual(h["back_hidden_dialog"], 1)
		self.assertEqual(h["forward_reopened"], 1)
		self.assertEqual(h["forward_routed"], 0)
		self.assertEqual(h["forward_stack"], 1)

	def test_a_drawer_closed_otherwise_steps_off_its_entry(self):
		h = self.out["history"]
		self.assertIs(h["release_replace_flag"], True)
		self.assertEqual(h["release_log"], ["push", "back"])
		self.assertEqual(h["release_at"], 0)
		self.assertEqual(h["release_routed"], 0)
		self.assertIs(h["release_flag_after"], False)
		# A route change in the same tick replaced the entry: no Back at all.
		self.assertEqual(h["navigate_log"], ["push", "replace"])
		self.assertEqual(
			h["navigate_entries"], ["/desk/project-planner/week/2026-10-12", "/desk/Form/Task/TASK-1"]
		)
		self.assertEqual(h["navigate_at"], 1)

	def test_the_planners_own_route_changes(self):
		h = self.out["history"]
		self.assertIs(h["keep_flag_after"], False)
		self.assertEqual(
			h["keep_entries"],
			[
				["/desk/project-planner/week/2026-10-12", False],
				["/desk/project-planner/week/2026-10-19", False],
				["/desk/project-planner/week/2026-10-19", True],
			],
		)
		self.assertEqual(h["keep_closed"], [])
		self.assertEqual(h["keep_back_closed"], [["drawer", "back"]])
		self.assertEqual(h["keep_back_routed"], 0)
		self.assertEqual(h["keep_second_back_routed"], 1)
		self.assertEqual(h["leave_closed"], [["drawer", "route"]])
		self.assertEqual(
			h["leave_entries"],
			["/desk/project-planner/week/2026-10-12", "/desk/project-planner/route/RES-1/2026-10-12"],
		)
		self.assertEqual(h["other_closed"], [["drawer", "route"]])
		self.assertEqual(h["handover_log"], ["push"])
		self.assertEqual(h["handover_entries"], 2)

	def test_no_entry_where_the_router_cannot_be_gated(self):
		h = self.out["history"]
		self.assertEqual(h["no_event"], [False, 0])
		self.assertEqual(h["no_router"], [False, 0])


if __name__ == "__main__":
	unittest.main()
