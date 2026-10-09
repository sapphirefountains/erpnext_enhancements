# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Project Planner's availability engine and its API.

``project_enhancements.crew_availability`` decides how many hours each person has free on each
day, for both planners: Nik's rule (2026-10-08) is that the Maintenance Planner and the Project
Planner must agree to the hour. So the arithmetic is pinned here case by case: work patterns with
seasonal date ranges, holidays (never the weekly-off rows), approved time off (half a day is half
the capacity), and how a task's hours land on its crew's days, including the task with no
estimate, which books a full day rather than vanishing.

The API half pins the contract the page codes against: overbooking never blocks, it comes back as
``needs_reason`` without saving; a stale card is refused; a move that only gives a start keeps the
task's length; and every write is POST-only.

Bench-free: it installs its own ``frappe`` stub, runs the real modules, and puts ``sys.modules``
back afterwards.

Run: python -m unittest erpnext_enhancements.tests.test_project_planner
"""

import ast
import datetime
import importlib
import json
import re
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
ENGINE_PATH = APP / "project_enhancements/crew_availability.py"
API_PATH = APP / "api/project_planner.py"
ROUTING_PATH = APP / "project_enhancements/routing.py"
TASK_CONTROLLER = APP / "task_enhancements/doctype/task/task.py"
PAGE_JSON = APP / "project_enhancements/page/project_planner/project_planner.json"

D = datetime.date
DT = datetime.datetime
TODAY = D(2026, 10, 8)
MON, TUE, WED, THU, FRI, SAT, SUN = (D(2026, 10, 12) + datetime.timedelta(days=i) for i in range(7))

STUBBED = (
	"frappe",
	"frappe.utils",
	"erpnext_enhancements.project_enhancements",
	"erpnext_enhancements.project_enhancements.crew_availability",
	"erpnext_enhancements.project_enhancements.routing",
	"erpnext_enhancements.api.project_planner",
	"erpnext_enhancements.api.maintenance_planner",
)
_saved_modules = {}
_modules_before = set()
frappe = None
engine = None
routing = None
api = None
mp = None


class _Doc(dict):
	"""Enough of a frappe Document / _dict: dict access plus attribute access."""

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name) from None

	def __setattr__(self, name, value):
		self[name] = value


class _Throw(Exception):
	pass


class _PermissionError(Exception):
	pass


def _getdate(value=None):
	if value is None:
		return TODAY
	if isinstance(value, DT):
		return value.date()
	if isinstance(value, D):
		return value
	return D.fromisoformat(str(value)[:10])


def _throw(message, exc=None, title=None):
	raise (exc or _Throw)(message)


class _Meta:
	def __init__(self, fields):
		self.fields = set(fields)

	def has_field(self, fieldname):
		return fieldname in self.fields


def setUpModule():
	global frappe, engine, routing, api, mp
	_modules_before.update(sys.modules)
	for name in STUBBED:
		_saved_modules[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.getdate = _getdate
	utils.add_days = lambda value, days: _getdate(value) + datetime.timedelta(days=days)
	utils.date_diff = lambda a, b: (_getdate(a) - _getdate(b)).days
	utils.formatdate = lambda value=None, *a, **k: _getdate(value).strftime("%m-%d-%Y")
	utils.nowdate = lambda: str(TODAY)
	utils.flt = lambda value, precision=None: float(value or 0)
	utils.cint = lambda value: int(float(value or 0))
	utils.strip_html_tags = lambda text: re.sub(r"<[^>]+>", "", str(text))
	utils.escape_html = lambda text: str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	frappe.throw = _throw
	frappe.PermissionError = _PermissionError
	frappe.session = types.SimpleNamespace(user="nik@example.com")
	frappe.local = types.SimpleNamespace(message_log=[])
	frappe.parse_json = json.loads
	frappe.get_traceback = lambda: "traceback"
	sys.modules.update({"frappe": frappe, "frappe.utils": utils})
	_reset()

	engine = importlib.import_module("erpnext_enhancements.project_enhancements.crew_availability")
	routing = importlib.import_module("erpnext_enhancements.project_enhancements.routing")
	api = importlib.import_module("erpnext_enhancements.api.project_planner")
	mp = importlib.import_module("erpnext_enhancements.api.maintenance_planner")


def tearDownModule():
	# Drop everything imported against this stub, then put back what was there before.
	for name in set(sys.modules) - _modules_before:
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements")):
			sys.modules.pop(name, None)
	for name, module in _saved_modules.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def _reset():
	frappe.roles = ["Projects User"]
	frappe.get_roles = lambda user=None: list(frappe.roles)
	frappe.session.user = "nik@example.com"
	frappe.local.message_log = []
	frappe.tables = {}
	frappe.queries = []
	frappe.sql_rows = {}
	frappe.errors = []
	frappe.docs = {}
	frappe.cached = {}
	frappe.metas = {
		"Holiday": _Meta(["holiday_date", "weekly_off", "is_half_day"]),
		"Work Restriction": _Meta(["ended_on", "canceled_on"]),
	}
	frappe.log_error = lambda *a, **k: frappe.errors.append((a, k))
	frappe.get_all = _get_all
	frappe.get_doc = lambda doctype, name=None: frappe.docs[(doctype, name)]
	frappe.get_cached_doc = _get_cached_doc
	frappe.get_meta = lambda doctype: frappe.metas.get(doctype, _Meta([]))
	frappe.has_permission = lambda *a, **k: True
	frappe.defaults = types.SimpleNamespace(get_user_default=lambda key: "Sapphire Fountains")
	frappe.db = types.SimpleNamespace(
		sql=_sql,
		exists=lambda *a, **k: True,
		has_column=lambda doctype, column: True,
		get_value=lambda doctype, name, field=None, *a, **k: frappe.values.get((doctype, name, field)),
		get_single_value=lambda doctype, field, *a, **k: frappe.singles.get((doctype, field)),
	)
	frappe.values = {}
	frappe.singles = {}
	frappe.enqueued = []
	frappe.enqueue = lambda *a, **k: frappe.enqueued.append((a, k))


def _get_all(doctype, filters=None, fields=None, pluck=None, as_list=False, **kwargs):
	frappe.queries.append((doctype, {"filters": filters, "fields": fields, **kwargs}))
	rows = frappe.tables.get(doctype, [])
	rows = rows(filters) if callable(rows) else rows
	if pluck:
		return [row[pluck] for row in rows]
	if as_list:
		return [tuple(row.get(f) for f in fields) for row in rows]
	return [_Doc(row) for row in rows]


def _sql(query, values=None, as_dict=False, **kwargs):
	frappe.queries.append(("sql", query))
	for marker, rows in frappe.sql_rows.items():
		if marker in query:
			return [_Doc(row) for row in rows]
	return []


def _get_cached_doc(doctype, name=None):
	key = (doctype, name) if name else doctype
	if key not in frappe.cached:
		raise KeyError(key)
	return frappe.cached[key]


def _person(name="RES-1", **values):
	row = {
		"name": name,
		"resource_name": values.pop("label", "Austin Healey"),
		"resource_type": "Employee",
		"employee": f"EMP-{name}",
		"user": f"{name.lower()}@example.com",
		"supplier": None,
		"resource_group": "Field",
		"home_team": "Projects",
		"color": None,
	}
	row.update(values)
	return _Doc(row)


def _task(name="TASK-1", start=MON, end=None, **values):
	row = {
		"name": name,
		"subject": f"Subject {name}",
		"project": "PRJ-1",
		"status": "Open",
		"exp_start_date": start,
		"exp_end_date": end,
		"expected_time": 0,
		"custom_start_datetime": None,
		"custom_end_datetime": None,
		"custom_rental_booking": None,
		"custom_rental_task_kind": None,
		"custom_crew_size": 0,
		"color": None,
		"modified": "2026-10-08 09:00:00",
	}
	row.update(values)
	return _Doc(row)


def _always(hours):
	return lambda resource, day: hours


def _weekdays(hours=8.0):
	return lambda resource, day: hours if day.weekday() < 5 else 0.0


SETTINGS = None


def _settings(**overrides):
	out = dict(engine.DEFAULT_SETTINGS)
	out.update(overrides)
	return out


# ---------------------------------------------------------------------- pure engine


class TestFmtHours(unittest.TestCase):
	def test_hours_drop_a_trailing_zero(self):
		self.assertEqual(engine.fmt_hours(2), "2")
		self.assertEqual(engine.fmt_hours(2.0), "2")
		self.assertEqual(engine.fmt_hours(2.5), "2.5")
		self.assertEqual(engine.fmt_hours(1.25), "1.25")
		self.assertEqual(engine.fmt_hours(1.2), "1.2")
		self.assertEqual(engine.fmt_hours(None), "0")


class TestPatternHours(unittest.TestCase):
	def test_no_pattern_is_monday_to_friday_at_the_default(self):
		self.assertEqual(engine.pattern_hours([], MON), 8.0)
		self.assertEqual(engine.pattern_hours([], FRI, 7.5), 7.5)
		self.assertEqual(engine.pattern_hours(None, SAT), 0.0)
		self.assertEqual(engine.pattern_hours([], SUN, 10), 0.0)

	def test_an_undated_row_is_the_standing_pattern(self):
		# Austin works Monday to Wednesday.
		austin = [{"monday": 10, "tuesday": 10, "wednesday": 10, "thursday": 0, "friday": 0}]
		self.assertEqual(engine.pattern_hours(austin, MON), 10.0)
		self.assertEqual(engine.pattern_hours(austin, THU), 0.0)
		self.assertEqual(engine.pattern_hours(austin, SAT), 0.0)

	def test_a_dated_row_beats_an_undated_one(self):
		rows = [
			{"monday": 8},
			{"effective_from": D(2026, 10, 1), "effective_to": D(2026, 10, 31), "monday": 4},
		]
		self.assertEqual(engine.pattern_hours(rows, MON), 4.0)
		# Outside its dates the undated row is back in charge.
		self.assertEqual(engine.pattern_hours(rows, D(2026, 11, 2)), 8.0)

	def test_the_latest_start_wins_among_dated_rows(self):
		rows = [
			{"effective_from": "2026-01-01", "monday": 6},
			{"effective_from": "2026-10-01", "monday": 3},
			{"effective_from": "2026-06-01", "monday": 5},
		]
		self.assertEqual(engine.pattern_hours(rows, MON), 3.0)
		self.assertEqual(engine.pattern_hours(rows, D(2026, 7, 6)), 5.0)

	def test_open_ended_ranges(self):
		until = [{"effective_to": "2026-10-13", "monday": 2, "wednesday": 2}]
		self.assertEqual(engine.pattern_hours(until, MON), 2.0)
		self.assertEqual(engine.pattern_hours(until, WED), 8.0)  # past its end: the default
		since = [{"effective_from": "2026-10-13", "tuesday": 9}]
		self.assertEqual(engine.pattern_hours(since, TUE), 9.0)
		self.assertEqual(engine.pattern_hours(since, MON), 8.0)  # before it starts

	def test_a_blank_weekday_in_a_row_is_zero(self):
		self.assertEqual(engine.pattern_hours([{"monday": None}], MON), 0.0)


class TestDayCapacity(unittest.TestCase):
	def test_a_working_day_and_a_day_not_worked(self):
		self.assertEqual(engine.day_capacity(8), (8.0, None))
		self.assertEqual(engine.day_capacity(0), (0.0, "Not a work day"))

	def test_a_holiday_takes_the_day_and_says_which(self):
		self.assertEqual(
			engine.day_capacity(8, {"description": "Columbus Day", "half_day": False}),
			(0.0, "Holiday: Columbus Day"),
		)
		self.assertEqual(engine.day_capacity(8, {"description": "", "half_day": False}), (0.0, "Holiday"))
		self.assertEqual(
			engine.day_capacity(8, {"description": "Christmas Eve", "half_day": True}),
			(4.0, "Holiday (half day): Christmas Eve"),
		)

	def test_time_off_is_zero_and_a_half_day_is_half(self):
		self.assertEqual(engine.day_capacity(8, None, "full"), (0.0, "Time off"))
		self.assertEqual(engine.day_capacity(10, None, "half"), (5.0, "Half day off"))
		# Time off on a holiday keeps the holiday's (public) label.
		self.assertEqual(
			engine.day_capacity(8, {"description": "Columbus Day"}, "full"), (0.0, "Holiday: Columbus Day")
		)


class TestSpanAndSlot(unittest.TestCase):
	def test_span_from_the_expected_dates(self):
		self.assertEqual(engine.task_span(_task(start=MON, end=WED)), (MON, WED))
		self.assertEqual(
			engine.task_span(_task(start="2026-10-12 00:00:00", end="2026-10-14 17:00:00")), (MON, WED)
		)

	def test_either_date_alone_is_one_day(self):
		self.assertEqual(engine.task_span(_task(start=TUE, end=None)), (TUE, TUE))
		self.assertEqual(engine.task_span(_task(start=None, end=THU)), (THU, THU))

	def test_an_end_before_the_start_is_one_day_and_undated_is_none(self):
		self.assertEqual(engine.task_span(_task(start=WED, end=MON)), (WED, WED))
		self.assertIsNone(engine.task_span(_task(start=None, end=None)))

	def test_a_same_day_slot_decides_the_day(self):
		task = _task(
			start=MON,
			end=FRI,
			custom_start_datetime="2026-10-14 09:00:00",
			custom_end_datetime="2026-10-14 13:00:00",
		)
		self.assertEqual(engine.task_span(task), (WED, WED))
		self.assertEqual(engine.task_slot(task), (DT(2026, 10, 14, 9), DT(2026, 10, 14, 13)))

	def test_a_slot_needs_one_day_and_an_end_after_the_start(self):
		over_night = _task(
			custom_start_datetime="2026-10-12 20:00:00", custom_end_datetime="2026-10-13 02:00:00"
		)
		self.assertIsNone(engine.task_slot(over_night))
		self.assertEqual(engine.task_span(over_night), (MON, MON))  # back to the expected dates
		backwards = _task(
			custom_start_datetime="2026-10-12 13:00:00", custom_end_datetime="2026-10-12 09:00:00"
		)
		self.assertIsNone(engine.task_slot(backwards))
		self.assertIsNone(engine.task_slot(_task(custom_start_datetime="2026-10-12 09:00:00")))


class TestAllocateTask(unittest.TestCase):
	def _by(self, allocations):
		return {(resource, day): (hours, estimated) for resource, day, hours, estimated in allocations}

	def test_an_estimate_is_split_across_the_crew_and_their_days(self):
		task = _task(start=MON, end=TUE, expected_time=16)
		got = engine.allocate_task(
			task, [{"resource": "A", "hours": None}, {"resource": "B", "hours": 0}], _weekdays(), _settings()
		)
		self.assertEqual(
			self._by(got),
			{
				("A", MON): (4.0, False),
				("A", TUE): (4.0, False),
				("B", MON): (4.0, False),
				("B", TUE): (4.0, False),
			},
		)

	def test_explicit_row_hours_are_spread_and_the_rest_is_shared(self):
		task = _task(start=MON, end=WED, expected_time=12)
		got = self._by(
			engine.allocate_task(
				task,
				[{"resource": "A", "hours": 6}, {"resource": "B", "hours": None}],
				_weekdays(),
				_settings(),
			)
		)
		self.assertEqual([got[("A", d)] for d in (MON, TUE, WED)], [(2.0, False)] * 3)
		self.assertEqual([got[("B", d)] for d in (MON, TUE, WED)], [(2.0, False)] * 3)

	def test_a_blank_float_row_hours_of_zero_behaves_like_none(self):
		# A blank Float cell reads back as 0.0, so 0.0 must mean "unset", never "zero hours".
		for task in (_task(start=MON, end=TUE, expected_time=10), _task(start=MON, end=TUE)):
			blank = engine.allocate_task(task, [{"resource": "A", "hours": 0.0}], _weekdays(), _settings())
			unset = engine.allocate_task(task, [{"resource": "A", "hours": None}], _weekdays(), _settings())
			self.assertEqual(blank, unset)
			self.assertTrue(blank)
		self.assertIsNone(engine.resolve_crew([{"resource": "A", "hours": 0.0}], [], {})[0]["hours"])

	def test_explicit_hours_beyond_the_estimate_leave_nothing_to_share(self):
		task = _task(start=MON, expected_time=4)
		got = engine.allocate_task(
			task, [{"resource": "A", "hours": 6}, {"resource": "B", "hours": None}], _weekdays(), _settings()
		)
		self.assertEqual(got, [("A", MON, 6.0, False)])

	def test_no_estimate_books_a_full_day_of_each_persons_capacity(self):
		capacity = {"A": 10.0, "B": 6.0}
		got = engine.allocate_task(
			_task(start=MON, end=TUE),
			[{"resource": "A"}, {"resource": "B"}],
			lambda resource, day: capacity[resource],
			_settings(),
		)
		self.assertEqual(
			self._by(got),
			{
				("A", MON): (10.0, True),
				("A", TUE): (10.0, True),
				("B", MON): (6.0, True),
				("B", TUE): (6.0, True),
			},
		)

	def test_no_estimate_on_a_day_off_falls_back_to_the_full_day_hours(self):
		got = engine.allocate_task(_task(start=SAT), [{"resource": "A"}], _weekdays(), _settings())
		self.assertEqual(got, [("A", SAT, 8.0, True)])
		got = engine.allocate_task(
			_task(start=SAT), [{"resource": "A"}], _weekdays(), _settings(default_day_hours=7.5)
		)
		self.assertEqual(got, [("A", SAT, 7.5, True)])

	def test_a_member_books_only_their_working_days(self):
		# Austin works Monday only this week; the 12 hours all land on Monday.
		austin = lambda resource, day: 10.0 if day == MON else 0.0  # noqa: E731
		got = engine.allocate_task(
			_task(start=MON, end=WED, expected_time=12), [{"resource": "A"}], austin, _settings()
		)
		self.assertEqual(got, [("A", MON, 12.0, False)])

	def test_a_member_with_no_working_days_lands_on_the_days_off(self):
		got = engine.allocate_task(
			_task(start=SAT, end=SUN, expected_time=6), [{"resource": "A"}], _weekdays(), _settings()
		)
		self.assertEqual(self._by(got), {("A", SAT): (3.0, False), ("A", SUN): (3.0, False)})

	def test_a_rental_task_without_an_estimate_uses_its_kinds_hours(self):
		task = _task(start=MON, custom_rental_booking="RB-1", custom_rental_task_kind="Setup")
		self.assertEqual(
			engine.allocate_task(task, [{"resource": "A"}], _weekdays(), _settings()), [("A", MON, 3.0, True)]
		)
		task["custom_rental_task_kind"] = "Take-down"
		self.assertEqual(
			engine.allocate_task(
				task, [{"resource": "A"}], _weekdays(), _settings(rental_takedown_hours=1.5)
			),
			[("A", MON, 1.5, True)],
		)
		# An estimate on the task wins over the kind's default.
		task["expected_time"] = 5
		self.assertEqual(
			engine.allocate_task(task, [{"resource": "A"}], _weekdays(), _settings()),
			[("A", MON, 5.0, False)],
		)

	def test_a_slot_books_its_length_for_everyone_unless_a_row_says_otherwise(self):
		task = _task(
			start=MON,
			expected_time=20,
			custom_start_datetime="2026-10-12 09:00:00",
			custom_end_datetime="2026-10-12 13:30:00",
		)
		got = engine.allocate_task(
			task, [{"resource": "A"}, {"resource": "B", "hours": 2}], _always(0.0), _settings()
		)
		self.assertEqual(got, [("A", MON, 4.5, False), ("B", MON, 2.0, False)])

	def test_a_repeated_member_counts_once_and_no_crew_or_no_dates_books_nothing(self):
		got = engine.allocate_task(
			_task(start=MON, expected_time=8),
			[{"resource": "A"}, {"resource": "A"}],
			_weekdays(),
			_settings(),
		)
		self.assertEqual(got, [("A", MON, 8.0, False)])
		self.assertEqual(engine.allocate_task(_task(), [], _weekdays(), _settings()), [])
		self.assertEqual(
			engine.allocate_task(_task(start=None), [{"resource": "A"}], _weekdays(), _settings()), []
		)


class TestDayConflicts(unittest.TestCase):
	def test_over_capacity_beyond_the_tolerance(self):
		self.assertEqual(engine.day_conflicts(8, None, [{"hours": 6}, {"hours": 4}]), ["Over by 2h"])
		self.assertEqual(engine.day_conflicts(8, None, [{"hours": 9.5}]), ["Over by 1.5h"])
		self.assertEqual(engine.day_conflicts(8, None, [{"hours": 8.005}]), [])
		self.assertEqual(engine.day_conflicts(8, None, []), [])

	def test_overlapping_slots_are_double_booked(self):
		bookings = [
			{"ref": "TASK-2", "hours": 3, "slot": ["10:00", "13:00"]},
			{"ref": "TASK-1", "hours": 2, "slot": ["09:00", "11:00"]},
			{"ref": "TASK-3", "hours": 1, "slot": ["13:00", "14:00"]},  # touches, does not overlap
		]
		self.assertEqual(
			engine.day_conflicts(8, None, bookings), ["Double-booked 10:00–11:00 (TASK-1, TASK-2)"]
		)

	def test_anything_on_a_day_with_no_capacity(self):
		self.assertEqual(
			engine.day_conflicts(0, "Time off", [{"hours": 2}]), ["Booked on a day off (Time off)"]
		)
		self.assertEqual(engine.day_conflicts(0, None, [{"hours": 2}]), ["Booked on a day off"])
		self.assertEqual(engine.day_conflicts(0, "Holiday: Columbus Day", []), [])

	def test_a_half_day_off_only_conflicts_once_it_is_over(self):
		self.assertEqual(engine.day_conflicts(4, "Half day off", [{"hours": 3}]), [])
		self.assertEqual(engine.day_conflicts(4, "Half day off", [{"hours": 6}]), ["Over by 2h"])

	def test_a_zero_hour_booking_on_a_day_off_is_not_a_conflict(self):
		# The Saturday of a trip: away, but nothing is booked against a day nobody works.
		self.assertEqual(engine.day_conflicts(0, "Not a work day", [{"kind": "travel", "hours": 0}]), [])
		self.assertEqual(
			engine.day_conflicts(0, "Not a work day", [{"hours": 0}, {"hours": 3}]),
			["Booked on a day off (Not a work day)"],
		)


class TestSpanForEstimate(unittest.TestCase):
	def test_no_estimate_is_one_day(self):
		self.assertEqual(engine.span_for_estimate(MON, 0), (MON, MON))
		self.assertEqual(engine.span_for_estimate(MON, None, 3), (MON, MON))

	def test_the_estimate_sets_the_length_for_the_people_on_it(self):
		self.assertEqual(engine.span_for_estimate(MON, 8), (MON, MON))
		self.assertEqual(engine.span_for_estimate(MON, 40), (MON, FRI))
		self.assertEqual(engine.span_for_estimate(MON, 40, 2), (MON, WED))  # 2.5 days rounds up
		self.assertEqual(engine.span_for_estimate(MON, 12, 1, day_hours=10), (MON, TUE))

	def test_days_after_the_first_skip_the_weekend(self):
		self.assertEqual(engine.span_for_estimate(FRI, 16), (FRI, D(2026, 10, 19)))


class TestClosedProjects(unittest.TestCase):
	def test_every_finished_project_status_on_the_site_is_closed(self):
		# The site's Project status options: Active, Client Hold, Parked, Completed, Invoiced, Paid,
		# Canceled. A stale open task on a project past Completed is not a booking.
		for status in ("Completed", "Invoiced", "Paid", "Canceled", "Cancelled"):
			self.assertIn(status, engine.CLOSED_PROJECT_STATUSES)
		for status in ("Active", "Client Hold", "Parked"):
			self.assertNotIn(status, engine.CLOSED_PROJECT_STATUSES)


class TestResolveCrew(unittest.TestCase):
	def test_crew_rows_win_and_a_blank_hours_is_a_share(self):
		rows = [
			{"resource": "RES-1", "resource_name": "Austin", "hours": 0, "is_lead": 1},
			{"resource": "RES-2", "resource_name": "Lisa", "hours": 3.5},
			{"resource": "RES-1", "resource_name": "Austin", "hours": 9},
		]
		crew = engine.resolve_crew(rows, ["james@example.com"], {"james@example.com": "RES-3"})
		self.assertEqual(
			crew,
			[
				{"resource": "RES-1", "hours": None, "is_lead": True, "label": "Austin"},
				{"resource": "RES-2", "hours": 3.5, "is_lead": False, "label": "Lisa"},
			],
		)

	def test_without_rows_the_open_assignees_who_are_resources(self):
		crew = engine.resolve_crew(
			[], ["austin@example.com", "office@example.com"], {"austin@example.com": "RES-1"}
		)
		self.assertEqual(crew, [{"resource": "RES-1", "hours": None, "is_lead": False, "label": None}])


# ---------------------------------------------------------------------- readers


class TestSettings(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_none_is_the_default_and_a_deliberate_zero_stays(self):
		frappe.cached["Project Planner Settings"] = _Doc(
			default_day_hours=None, maintenance_visit_hours=0, rental_setup_hours=4.5
		)
		got = engine.get_settings()
		self.assertEqual(got["default_day_hours"], 8.0)
		self.assertEqual(got["maintenance_visit_hours"], 0.0)
		self.assertEqual(got["rental_setup_hours"], 4.5)
		self.assertEqual(got["rental_delivery_hours"], 2.0)

	def test_a_missing_single_is_all_defaults(self):
		self.assertEqual(engine.get_settings(), engine.DEFAULT_SETTINGS)

	def test_it_is_read_through_get_cached_doc_never_get_single_value(self):
		tree = ast.parse(ENGINE_PATH.read_text(encoding="utf-8"))
		calls = {
			node.func.attr
			for node in ast.walk(tree)
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
		}
		self.assertIn("get_cached_doc", calls)
		self.assertNotIn("get_single_value", calls)


class TestReadTasks(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_python_has_the_last_word_on_what_is_live_and_in_range(self):
		frappe.sql_rows["FROM `tabTask` t"] = [
			_task("IN", start=MON, end=WED, project_status="Active"),
			_task("NO-PROJECT", start=TUE, project=None, project_status=None),
			_task("DONE", start=MON, status="Completed"),
			_task("CANCELED", start=MON, status="Canceled"),
			_task("CLOSED-PROJECT", start=MON, project_status="Cancelled"),
			_task("UNDATED", start=None),
			_task("BEFORE", start=D(2026, 10, 1), end=D(2026, 10, 2)),
			_task(
				"SLOT",
				start=D(2026, 10, 1),
				custom_start_datetime="2026-10-13 08:00:00",
				custom_end_datetime="2026-10-13 10:00:00",
			),
		]
		names = [row["name"] for row in engine.read_tasks(MON, FRI)]
		self.assertEqual(names, ["IN", "NO-PROJECT", "SLOT"])

	def test_the_query_binds_its_values_and_survives_a_missing_column(self):
		frappe.db.has_column = lambda doctype, column: column != "custom_crew_size"
		engine.read_tasks(MON, FRI)
		query = frappe.queries[-1][1]
		self.assertIn("NULL AS `custom_crew_size`", query)
		self.assertIn("NOT IN %(finished)s", query)

	def test_finished_statuses_match_the_task_controller(self):
		source = TASK_CONTROLLER.read_text(encoding="utf-8")
		literal = re.search(r"^FINISHED_STATUSES = (\(.*\))$", source, re.MULTILINE).group(1)
		self.assertEqual(ast.literal_eval(literal), engine.FINISHED_STATUSES)


class TestReadCrews(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_rows_and_open_assignees_per_task(self):
		frappe.tables["Task Crew Member"] = [{"parent": "T1", "resource": "RES-1", "hours": 0}]
		frappe.tables["ToDo"] = [
			{"reference_name": "T2", "allocated_to": "a@example.com"},
			{"reference_name": "T2", "allocated_to": "a@example.com"},
			{"reference_name": "T2", "allocated_to": "b@example.com"},
		]
		rows, todos = engine.read_crews(["T1", "T2", "T1"])
		self.assertEqual([r["resource"] for r in rows["T1"]], ["RES-1"])
		self.assertEqual(todos["T2"], ["a@example.com", "b@example.com"])
		todo_filters = next(q[1]["filters"] for q in frappe.queries if q[0] == "ToDo")
		self.assertEqual(todo_filters["status"], "Open")
		self.assertEqual(todo_filters["reference_type"], "Task")


class TestReadHolidays(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.values[("Company", "Sapphire Fountains", "default_holiday_list")] = "US 2026"

	def test_own_list_else_the_companys_and_never_weekly_offs(self):
		people = [
			_person("RES-1", employee="EMP-1"),
			_person("RES-2", employee="EMP-2"),
			_person("RES-3", resource_type="Subcontractor", employee=None, user=None),
		]
		frappe.tables["Employee"] = [
			{"name": "EMP-1", "holiday_list": "Crew 2026"},
			{"name": "EMP-2", "holiday_list": None},
		]
		frappe.tables["Holiday"] = [
			{"parent": "US 2026", "holiday_date": MON, "description": "<p>Columbus Day</p>", "weekly_off": 0},
			{"parent": "US 2026", "holiday_date": SAT, "description": "Saturday", "weekly_off": 1},
			{
				"parent": "Crew 2026",
				"holiday_date": TUE,
				"description": "Crew day",
				"weekly_off": 0,
				"is_half_day": 1,
			},
			{
				"parent": "US 2026",
				"holiday_date": D(2026, 12, 25),
				"description": "Christmas",
				"weekly_off": 0,
			},
		]
		got = engine._read_holidays(people, MON, SUN)
		self.assertEqual(got["RES-2"], {MON: {"description": "Columbus Day", "half_day": False}})
		self.assertEqual(got["RES-1"], {TUE: {"description": "Crew day", "half_day": True}})
		self.assertNotIn("RES-3", got)

	def test_a_site_without_half_day_holidays_does_not_ask_for_the_column(self):
		frappe.metas["Holiday"] = _Meta(["holiday_date", "weekly_off"])
		frappe.tables["Employee"] = []
		engine._read_holidays([_person()], MON, SUN)
		fields = next(q[1]["fields"] for q in frappe.queries if q[0] == "Holiday")
		self.assertNotIn("is_half_day", fields)


class TestReadTimeOff(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_approved_time_off_by_user_else_employee_never_its_type(self):
		people = [
			_person("RES-1", user="austin@example.com", employee="EMP-1"),
			_person("RES-2", user=None, employee="EMP-2"),
		]
		frappe.tables["Time Off Request"] = [
			{
				"user": "austin@example.com",
				"employee": "EMP-1",
				"from_date": MON,
				"to_date": TUE,
				"half_day": 0,
			},
			{
				"user": "austin@example.com",
				"employee": "EMP-1",
				"from_date": TUE,
				"to_date": TUE,
				"half_day": 1,
			},
			{"user": None, "employee": "EMP-2", "from_date": THU, "to_date": None, "half_day": 1},
			{
				"user": "austin@example.com",
				"employee": "EMP-1",
				"from_date": None,
				"to_date": WED,
				"half_day": 0,
			},
			{
				"user": "austin@example.com",
				"employee": "EMP-1",
				"from_date": D(2026, 10, 1),
				"to_date": D(2026, 10, 2),
			},
		]
		got = engine._read_time_off(people, MON, SUN)
		self.assertEqual(got["RES-1"], {MON: "full", TUE: "full"})  # a full day beats a half
		self.assertEqual(got["RES-2"], {THU: "half"})
		query = next(q[1] for q in frappe.queries if q[0] == "Time Off Request")
		self.assertEqual(query["filters"]["status"], "Approved")
		self.assertNotIn("time_off_type", query["fields"])
		self.assertNotIn("reason", query["fields"])

	def test_a_range_is_clipped_to_the_window(self):
		frappe.tables["Time Off Request"] = [
			{"user": "res-1@example.com", "from_date": D(2026, 10, 1), "to_date": TUE, "half_day": 0}
		]
		self.assertEqual(engine._read_time_off([_person()], MON, SUN)["RES-1"], {MON: "full", TUE: "full"})


class TestReadRestrictions(unittest.TestCase):
	def setUp(self):
		_reset()
		for name, summary in (("R1", "no heights"), ("R2", "no lifting"), ("R3", ""), ("R4", "no driving")):
			frappe.cached[("Work Restriction", name)] = types.SimpleNamespace(summary=lambda s=summary: s)

	def test_dates_decide_and_a_dateless_end_is_left_out(self):
		frappe.tables["Work Restriction"] = [
			{
				"name": "R1",
				"user": "res-1@example.com",
				"from_date": TUE,
				"to_date": None,
				"status": "Active",
			},
			{
				"name": "R2",
				"user": "res-1@example.com",
				"from_date": D(2026, 10, 1),
				"to_date": None,
				"status": "Ended",
				"ended_on": WED,
			},
			{"name": "R3", "user": "res-1@example.com", "from_date": MON, "to_date": MON, "status": "Active"},
			{"name": "R4", "user": "res-1@example.com", "from_date": MON, "to_date": None, "status": "Ended"},
		]
		got = engine._read_restrictions([_person()], MON, THU)["RES-1"]
		self.assertEqual(got[MON], ["On restricted duty: no lifting", "On restricted duty"])
		self.assertEqual(got[TUE], ["On restricted duty: no heights", "On restricted duty: no lifting"])
		self.assertEqual(got[WED], ["On restricted duty: no heights"])  # ended on Wednesday
		self.assertEqual(got[THU], ["On restricted duty: no heights"])


class TestReadVisits(unittest.TestCase):
	def setUp(self):
		_reset()
		self._projections, self._decorate = mp._projections, mp._decorate

	def tearDown(self):
		mp._projections, mp._decorate = self._projections, self._decorate

	def test_records_projections_and_their_hours(self):
		frappe.sql_rows["tabSapphire Maintenance Record"] = [
			{
				"name": "SMR-1",
				"project": "PRJ-1",
				"visit_label": None,
				"technician": "res-1@example.com",
				"plan_date": MON,
				"project_title": "Highlands Maintenance Contract",
				"clock_in_time": None,
				"clock_out_time": None,
			},
			{
				"name": "SMR-2",
				"project": "PRJ-2",
				"visit_label": "Winterization",
				"technician": "res-1@example.com",
				"plan_date": TUE,
				"project_title": "The Charles",
				"clock_in_time": "2026-10-13 08:00:00",
				"clock_out_time": "2026-10-13 11:30:00",
			},
			{
				"name": "SMR-3",
				"project": "PRJ-1",
				"visit_label": None,
				"technician": "someone@example.com",
				"plan_date": MON,
				"project_title": "Highlands",
				"clock_in_time": None,
				"clock_out_time": None,
			},
		]

		def projections(start, end, today):
			return [
				{
					"kind": "projected",
					"key": "C1||2026-10-14",
					"contract": "C1",
					"project": "PRJ-1",
					"date": "2026-10-14",
				},
				{
					"kind": "projected",
					"key": "C2||2026-10-14",
					"contract": "C2",
					"project": "PRJ-9",
					"date": "2026-10-14",
				},
			]

		def decorate(cards):
			for card in cards:
				card["site"] = "Highlands"
				card["technician"] = "res-1@example.com" if card["contract"] == "C1" else None

		mp._projections, mp._decorate = projections, decorate
		got = engine._read_visits(["res-1@example.com"], MON, FRI, _settings(maintenance_visit_hours=2))
		self.assertEqual(
			[(v["ref"], v["date"], v["hours"], v["slot"], v["estimated"], v["label"]) for v in got],
			[
				("SMR-1", MON, 2.0, None, True, "Highlands"),
				("SMR-2", TUE, 3.5, ["08:00", "11:30"], False, "The Charles · Winterization"),
				("C1", WED, 2.0, None, True, "Projected visit: Highlands"),
			],
		)

	def test_a_projection_bug_is_logged_and_the_records_still_count(self):
		frappe.sql_rows["tabSapphire Maintenance Record"] = [
			{"name": "SMR-1", "project": "PRJ-1", "technician": "res-1@example.com", "plan_date": MON}
		]

		def boom(*args):
			raise RuntimeError("bad contract")

		mp._projections = boom
		got = engine._read_visits(["res-1@example.com"], MON, FRI, _settings())
		self.assertEqual([v["ref"] for v in got], ["SMR-1"])
		self.assertEqual(frappe.errors[0][1]["title"], "Project Planner: visit projections failed")


class TestReadTravel(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_travelers_dates_else_the_trips(self):
		frappe.sql_rows["tabTrip Traveler"] = [
			{
				"employee": "EMP-1",
				"from_date": None,
				"to_date": None,
				"trip": "TRIP-1",
				"purpose": "Vegas install",
				"start_date": THU,
				"end_date": D(2026, 10, 20),
				"project": "PRJ-1",
			},
			{
				"employee": "EMP-2",
				"from_date": MON,
				"to_date": TUE,
				"trip": "TRIP-2",
				"purpose": None,
				"start_date": D(2026, 10, 1),
				"end_date": D(2026, 10, 30),
				"project": None,
			},
			{
				"employee": "EMP-3",
				"from_date": None,
				"to_date": None,
				"trip": "TRIP-3",
				"purpose": "No dates",
				"start_date": None,
				"end_date": None,
				"project": None,
			},
		]
		got = engine._read_travel(["EMP-1", "EMP-2", "EMP-3"], MON, FRI)
		self.assertEqual(
			[(t["employee"], t["date"], t["label"]) for t in got],
			[
				("EMP-1", THU, "Travel: Vegas install"),
				("EMP-1", FRI, "Travel: Vegas install"),
				("EMP-2", MON, "Travel: TRIP-2"),
				("EMP-2", TUE, "Travel: TRIP-2"),
			],
		)


# ---------------------------------------------------------------------- assembly


class _Readers:
	"""Patch the engine's readers with canned answers for one test."""

	def __init__(
		self,
		people,
		patterns=None,
		tasks=(),
		crew_rows=None,
		todos=None,
		holidays=None,
		time_off=None,
		restrictions=None,
		visits=(),
		travel=(),
		points=None,
		shop=None,
		matrix=None,
		settings=None,
		real_routing=False,
	):
		# Routing: where each stop is (``points`` by ref), the shop, and the legs. With no points
		# every stop is unlocated and no day is padded, which is what the Phase 1 tests assume.
		self.points = dict(points or {})
		self.matrix_calls = []
		self.missing = []
		# The ``google`` flag every drive_matrix / start_point call was made with (Phase 3A).
		self.google = []
		self.shop_google = []

		def locate(bookings, detail=False):
			out = {}
			for booking in bookings:
				point = self.points.get(booking.get("ref"))
				out[booking.get("ref")] = (
					{"point": point, "address": f"Address of {booking.get('ref')}" if point else None}
					if detail
					else point
				)
			return out

		def drive_matrix(pairs, settings=None, google=True):
			self.matrix_calls.append(set(pairs))
			self.google.append(google)
			out = {}
			for a, b in pairs:
				found = (matrix or {}).get((a, b))
				out[(a, b)] = dict(found, source="google") if found else routing.estimate_leg(a, b)
			return out

		def start_point(google=True):
			self.shop_google.append(google)
			return shop

		# real_routing keeps the module's own start_point and drive_matrix, for proving what
		# they would ask Google; the rest of the readers stay canned.
		routing_patches = (
			[]
			if real_routing
			else [
				mock.patch.object(routing, "start_point", start_point),
				mock.patch.object(routing, "drive_matrix", drive_matrix),
			]
		)
		self.patches = [
			mock.patch.object(engine, "get_settings", lambda: _settings(**(settings or {}))),
			mock.patch.object(routing, "locate", locate),
			*routing_patches,
			mock.patch.object(routing, "enqueue_missing", lambda refs: self.missing.append(list(refs))),
			mock.patch.object(
				engine,
				"_read_resources",
				lambda names=None: (
					[p for p in people if names is None or p["name"] in names],
					patterns or {},
				),
			),
			mock.patch.object(engine, "read_tasks", lambda start, end: [_Doc(t) for t in tasks]),
			mock.patch.object(engine, "read_crews", lambda names: (crew_rows or {}, todos or {})),
			mock.patch.object(engine, "_read_holidays", lambda people, start, end: holidays or {}),
			mock.patch.object(engine, "_read_time_off", lambda people, start, end: time_off or {}),
			mock.patch.object(engine, "_read_restrictions", lambda people, start, end: restrictions or {}),
			mock.patch.object(engine, "_read_visits", lambda users, start, end, settings: list(visits)),
			mock.patch.object(engine, "_read_travel", lambda employees, start, end: list(travel)),
		]

	def __enter__(self):
		for patch in self.patches:
			patch.start()
		return self

	def __exit__(self, *exc):
		for patch in self.patches:
			patch.stop()


class TestAvailability(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_one_week_of_one_person(self):
		austin = _person("RES-1", user="austin@example.com", employee="EMP-1")
		with _Readers(
			[austin],
			patterns={"RES-1": [{"monday": 10, "tuesday": 10, "wednesday": 10, "thursday": 0, "friday": 0}]},
			tasks=[
				_task("T-EST", start=MON, end=TUE, expected_time=12),
				_task("T-TODO", start=TUE, expected_time=0),
				_task(
					"T-SLOT",
					start=MON,
					custom_start_datetime="2026-10-12 09:00:00",
					custom_end_datetime="2026-10-12 11:00:00",
				),
			],
			crew_rows={"T-EST": [{"resource": "RES-1", "hours": 0}], "T-SLOT": [{"resource": "RES-1"}]},
			todos={"T-TODO": ["austin@example.com", "office@example.com"]},
			holidays={"RES-1": {WED: {"description": "Columbus Day", "half_day": False}}},
			restrictions={"RES-1": {MON: ["On restricted duty: no heights"]}},
			visits=[
				{
					"user": "austin@example.com",
					"date": MON,
					"ref": "SMR-1",
					"label": "Highlands",
					"project": "PRJ-9",
					"hours": 2.0,
					"slot": None,
					"estimated": True,
				}
			],
			travel=[
				{"employee": "EMP-1", "date": THU, "ref": "TRIP-1", "label": "Travel: Vegas", "project": None}
			],
		):
			data = engine.availability(MON, THU)
		self.assertEqual(set(data), {"resources", "days", "user_to_resource"})
		self.assertEqual(data["user_to_resource"], {"austin@example.com": "RES-1"})
		self.assertEqual(data["resources"][0]["label"], "Austin Healey")
		days = data["days"]["RES-1"]

		monday = days["2026-10-12"]
		self.assertEqual((monday["capacity"], monday["booked"], monday["free"]), (10.0, 10.0, 0.0))
		self.assertEqual([b["kind"] for b in monday["bookings"]], ["task", "task", "visit"])
		self.assertEqual(monday["bookings"][1]["slot"], ["09:00", "11:00"])
		self.assertEqual(monday["conflicts"], [])
		self.assertEqual(monday["warnings"], ["On restricted duty: no heights"])

		tuesday = days["2026-10-13"]
		# 6h of the estimate plus the unestimated ToDo task, which books his full 10h day.
		self.assertEqual((tuesday["booked"], tuesday["conflicts"]), (16.0, ["Over by 6h"]))
		self.assertTrue(tuesday["bookings"][1]["estimated"])

		wednesday = days["2026-10-14"]
		self.assertEqual(
			(wednesday["capacity"], wednesday["off"], wednesday["bookings"]),
			(0.0, "Holiday: Columbus Day", []),
		)

		thursday = days["2026-10-15"]
		self.assertEqual(thursday["off"], "Not a work day")
		# Travel on a day he does not work: shown as away, books nothing, and is not a conflict
		# (otherwise every trip across a weekend would show red).
		self.assertEqual(thursday["bookings"][0]["kind"], "travel")
		self.assertEqual(thursday["bookings"][0]["hours"], 0.0)
		self.assertEqual(thursday["conflicts"], [])

	def test_travel_on_a_working_day_takes_the_whole_day(self):
		with _Readers(
			[_person("RES-1", employee="EMP-1")],
			tasks=[_task("T", start=MON, expected_time=2)],
			crew_rows={"T": [{"resource": "RES-1"}]},
			travel=[{"employee": "EMP-1", "date": MON, "ref": "TRIP-1", "label": "Travel: Vegas", "project": None}],
		):
			monday = engine.availability(MON, MON)["days"]["RES-1"]["2026-10-12"]
		self.assertEqual((monday["capacity"], monday["booked"]), (8.0, 10.0))
		self.assertEqual(monday["conflicts"], ["Over by 2h"])

	def test_time_off_empties_the_day_and_a_half_day_halves_it(self):
		with _Readers([_person()], time_off={"RES-1": {MON: "full", TUE: "half"}}):
			days = engine.availability(MON, TUE)["days"]["RES-1"]
		self.assertEqual((days["2026-10-12"]["capacity"], days["2026-10-12"]["off"]), (0.0, "Time off"))
		self.assertEqual((days["2026-10-13"]["capacity"], days["2026-10-13"]["off"]), (4.0, "Half day off"))

	def test_a_task_that_started_before_the_range_spreads_over_its_whole_span(self):
		with _Readers(
			[_person()],
			tasks=[_task("LONG", start=D(2026, 10, 5), end=FRI, expected_time=80)],
			crew_rows={"LONG": [{"resource": "RES-1"}]},
		):
			days = engine.availability(MON, TUE)["days"]["RES-1"]
		self.assertEqual(days["2026-10-12"]["booked"], 8.0)  # 80h over ten working days

	def test_crew_on_an_inactive_resource_books_nobody(self):
		with _Readers(
			[_person("RES-1")],
			tasks=[_task("T", start=MON, expected_time=4)],
			crew_rows={"T": [{"resource": "RES-GONE"}]},
		):
			self.assertEqual(engine.availability(MON, MON)["days"]["RES-1"]["2026-10-12"]["bookings"], [])


class TestPreviewConflicts(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_the_stored_task_is_replaced_by_the_hypothetical_one(self):
		stored = _task("T-MOVE", start=MON, expected_time=4)
		other = _task("T-OTHER", start=TUE, expected_time=6)
		with _Readers(
			[_person()],
			tasks=[stored, other],
			crew_rows={"T-MOVE": [{"resource": "RES-1"}], "T-OTHER": [{"resource": "RES-1"}]},
		):
			self.assertEqual(engine.preview_conflicts(dict(stored), [{"resource": "RES-1"}]), {})
			moved = dict(stored, exp_start_date=TUE)
			self.assertEqual(
				engine.preview_conflicts(moved, [{"resource": "RES-1"}]),
				{"Austin Healey": ["2026-10-13: Over by 2h"]},
			)

	def test_a_double_booking_between_two_other_tasks_is_not_this_ones(self):
		slot = {"custom_start_datetime": "2026-10-12 09:00:00", "custom_end_datetime": "2026-10-12 10:00:00"}
		with _Readers(
			[_person()],
			tasks=[_task("T-A", start=MON, **slot), _task("T-B", start=MON, **slot)],
			crew_rows={"T-A": [{"resource": "RES-1"}], "T-B": [{"resource": "RES-1"}]},
		):
			got = engine.preview_conflicts(
				_task("T-NEW", start=MON, expected_time=1), [{"resource": "RES-1"}]
			)
		self.assertEqual(got, {})

	def test_no_crew_or_no_dates_is_nothing_to_check(self):
		self.assertEqual(engine.preview_conflicts(_task(), []), {})
		self.assertEqual(engine.preview_conflicts(_task(start=None), [{"resource": "RES-1"}]), {})


# ---------------------------------------------------------------------- API helpers


class TestApiHelpers(unittest.TestCase):
	def test_moving_only_the_start_keeps_the_length(self):
		self.assertEqual(api.plan_span((MON, WED), start=THU), (THU, D(2026, 10, 17)))
		self.assertEqual(api.plan_span((MON, WED), end=FRI), (MON, FRI))
		self.assertEqual(api.plan_span((MON, WED), start=TUE, end=TUE), (TUE, TUE))
		self.assertEqual(api.plan_span(None, start=TUE), (TUE, TUE))
		self.assertEqual(api.plan_span(None, end=TUE), (TUE, TUE))
		self.assertEqual(api.plan_span((MON, WED)), (MON, WED))

	def test_shifting_keeps_the_time_of_day(self):
		self.assertEqual(api.shift_datetime("2026-10-12 09:30:00", 3), "2026-10-15 09:30:00")
		self.assertEqual(api.shift_datetime(MON, -1), "2026-10-11 00:00:00")
		self.assertIsNone(api.shift_datetime(None, 2))
		self.assertEqual(api.on_date("2026-10-12 17:00:00", FRI), "2026-10-16 17:00:00")
		self.assertEqual(api.on_date("2026-10-12 00:00:00", FRI), "2026-10-16")
		self.assertEqual(api.on_date(None, FRI), "2026-10-16")

	def test_only_new_conflicts_need_a_reason(self):
		before = {"Austin": ["2026-10-12: Over by 2h"]}
		after = {
			"Austin": ["2026-10-12: Over by 2h", "2026-10-13: Over by 1h"],
			"Lisa": ["2026-10-13: Booked on a day off"],
		}
		self.assertEqual(
			api.conflict_delta(before, after),
			{"Austin": ["2026-10-13: Over by 1h"], "Lisa": ["2026-10-13: Booked on a day off"]},
		)
		self.assertEqual(api.conflict_delta(after, before), {})

	def test_arguments_the_caller_did_not_set(self):
		for value in (None, "", " ", "null", "undefined"):
			self.assertIsNone(api.given(value), value)
		self.assertEqual(api.given(0), 0)
		self.assertEqual(api.given("0"), "0")
		self.assertIsNone(api.parse_list(None))
		self.assertEqual(api.parse_list(""), [])
		self.assertEqual(api.parse_list('["RES-1"]'), ["RES-1"])
		with self.assertRaises(_Throw):
			api.parse_list('{"resource": "RES-1"}')

	def test_a_card(self):
		task = _task(
			"TASK-9",
			start=D(2026, 10, 5),
			end=D(2026, 10, 7),
			expected_time=12,
			custom_crew_size=3,
			project_title="Vegas Water Wall",
			color="#123456",
		)
		crew = [{"resource": "RES-1", "label": "Austin", "hours": None, "is_lead": True}]
		card = api.build_card(task, crew, {"RES-1": 12}, ["Forklift"], TODAY, True)
		self.assertEqual(
			{
				k: card[k]
				for k in (
					"start",
					"end",
					"slot",
					"short",
					"crew_size",
					"overdue",
					"movable",
					"color",
					"project_title",
				)
			},
			{
				"start": "2026-10-05",
				"end": "2026-10-07",
				"slot": None,
				"short": 2,
				"crew_size": 3,
				"overdue": True,
				"movable": True,
				"color": "#123456",
				"project_title": "Vegas Water Wall",
			},
		)
		self.assertEqual(
			card["crew"],
			[{"resource": "RES-1", "label": "Austin", "hours": None, "is_lead": True, "booked": 12.0}],
		)
		self.assertEqual(card["credentials"], ["Forklift"])
		self.assertEqual(card["modified"], "2026-10-08 09:00:00")
		rental = api.build_card(
			_task(start=MON, custom_rental_booking="RB-1", custom_rental_task_kind="Delivery"),
			[],
			{},
			[],
			TODAY,
			True,
		)
		self.assertEqual(
			(rental["movable"], rental["rental_kind"], rental["overdue"]), (False, "Delivery", False)
		)
		self.assertFalse(api.build_card(_task(start=MON), [], {}, [], TODAY, False)["movable"])
		slot = api.build_card(
			_task(custom_start_datetime="2026-10-12 09:00:00", custom_end_datetime="2026-10-12 13:00:00"),
			[],
			{},
			[],
			TODAY,
			True,
		)
		self.assertEqual(slot["slot"], ["09:00", "13:00"])

	def test_short_of_crew(self):
		card = api.build_card(_task(start=MON), [], {}, [], TODAY, True)
		self.assertTrue(api.needs_crew(card, []))
		self.assertFalse(api.needs_crew(card, ["office@example.com"]))  # an assignee who is not a resource
		crewed = api.build_card(
			_task(start=MON, custom_crew_size=2), [{"resource": "RES-1"}], {}, [], TODAY, True
		)
		self.assertTrue(api.needs_crew(crewed, []))
		crewed["crew_size"] = 1
		self.assertFalse(api.needs_crew(crewed, []))


# ---------------------------------------------------------------------- API writes


class _TaskDoc(_Doc):
	def check_permission(self, ptype):
		frappe.calls.append(("check_permission", ptype))

	def set(self, field, value):
		self[field] = [_Doc(row) for row in value] if isinstance(value, list) else value

	def append(self, field, row):
		self.setdefault(field, []).append(_Doc(row))

	def save(self):
		frappe.saved.append(json.loads(json.dumps(dict(self), default=str)))
		frappe.local.message_log.append({"message": "<b>Austin Healey</b> has approved time off."})
		self.modified = "2026-10-08 10:00:01"

	def add_comment(self, kind, text):
		frappe.comments.append((kind, text))


class TestWrites(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.saved, frappe.comments, frappe.calls, frappe.previews = [], [], [], []
		frappe.tables["Planner Resource"] = [
			{"name": "RES-1", "resource_name": "Austin Healey", "user": "austin@example.com", "is_active": 1},
			{"name": "RES-2", "resource_name": "Lisa", "user": "lisa@example.com", "is_active": 1},
			{"name": "RES-3", "resource_name": "Retired", "user": None, "is_active": 0},
		]
		frappe.values[("Project", "PRJ-1", "project_name")] = "Vegas Water Wall"
		self.conflicts = {}
		self.patches = [
			mock.patch.object(engine, "preview_conflicts", self._preview),
			mock.patch.object(engine, "_preview", lambda task, crew, *a: ({}, {})),
		]
		for patch in self.patches:
			patch.start()

	def tearDown(self):
		for patch in self.patches:
			patch.stop()

	def _preview(self, task, crew, start=None, end=None):
		frappe.previews.append((dict(task), list(crew)))
		span = engine.task_span(task)
		return self.conflicts.get(span[0] if span else None, {})

	def _doc(self, **values):
		doc = _TaskDoc(
			_task("TASK-1", start="2026-10-12 08:00:00", end="2026-10-14 17:00:00", expected_time=12)
		)
		doc["doctype"] = "Task"
		doc["custom_crew"] = [_Doc(resource="RES-1", resource_name="Austin Healey", hours=0, is_lead=1)]
		doc["custom_required_credentials"] = []
		doc.update(values)
		frappe.docs[("Task", "TASK-1")] = doc
		return doc

	def test_moving_only_the_start_keeps_the_length_and_the_times(self):
		self._doc()
		result = api.save_task("TASK-1", "2026-10-08 09:00:00", start="2026-10-19")
		saved = frappe.saved[0]
		self.assertEqual(
			(saved["exp_start_date"], saved["exp_end_date"]), ("2026-10-19 08:00:00", "2026-10-21 17:00:00")
		)
		self.assertEqual(result["modified"], "2026-10-08 10:00:01")
		self.assertEqual(result["card"]["start"], "2026-10-19")
		self.assertEqual(result["card"]["project_title"], "Vegas Water Wall")
		self.assertIn(("check_permission", "write"), frappe.calls)

	def test_an_undated_task_is_scheduled_long_enough_for_its_estimate(self):
		# From the Unscheduled tray: 24h for one person is three days, not 24h on one day.
		self._doc(exp_start_date=None, exp_end_date=None, expected_time=24)
		api.save_task("TASK-1", "2026-10-08 09:00:00", start="2026-10-12")
		saved = frappe.saved[0]
		self.assertEqual((saved["exp_start_date"], saved["exp_end_date"]), ("2026-10-12", "2026-10-14"))

	def test_a_slot_travels_with_the_start(self):
		self._doc(
			custom_start_datetime="2026-10-12 09:00:00",
			custom_end_datetime="2026-10-12 11:00:00",
			exp_end_date="2026-10-12 00:00:00",
		)
		api.save_task("TASK-1", "2026-10-08 09:00:00", start="2026-10-13")
		saved = frappe.saved[0]
		self.assertEqual(
			(saved["custom_start_datetime"], saved["custom_end_datetime"]),
			("2026-10-13 09:00:00", "2026-10-13 11:00:00"),
		)

	def test_a_stale_card_is_refused(self):
		self._doc()
		with self.assertRaises(_Throw):
			api.save_task("TASK-1", "2026-10-01 09:00:00", start="2026-10-19")
		self.assertEqual(frappe.saved, [])

	def test_a_new_conflict_without_a_reason_comes_back_unsaved(self):
		self._doc()
		self.conflicts[D(2026, 10, 19)] = {"Austin Healey": ["2026-10-19: Over by 2h"]}
		result = api.save_task("TASK-1", "2026-10-08 09:00:00", start="2026-10-19")
		self.assertEqual(
			result, {"needs_reason": True, "conflicts": {"Austin Healey": ["2026-10-19: Over by 2h"]}}
		)
		self.assertEqual(frappe.saved, [])
		self.assertEqual(frappe.comments, [])

	def test_with_a_reason_it_saves_and_the_reason_goes_on_the_timeline(self):
		self._doc()
		self.conflicts[D(2026, 10, 19)] = {"Austin Healey": ["2026-10-19: Over by 2h"]}
		result = api.save_task(
			"TASK-1", "2026-10-08 09:00:00", start="2026-10-19", reason="Client <deadline>"
		)
		self.assertEqual(len(frappe.saved), 1)
		kind, text = frappe.comments[0]
		self.assertEqual(kind, "Comment")
		self.assertIn("Austin Healey: 2026-10-19: Over by 2h", text)
		self.assertIn("Reason: Client &lt;deadline&gt;", text)
		self.assertEqual(result["warnings"], ["Austin Healey has approved time off."])
		self.assertEqual(frappe.local.message_log, [])  # taken off, so no dialog pops up as well

	def test_a_conflict_that_was_already_there_needs_no_reason(self):
		self._doc()
		self.conflicts[MON] = {"Austin Healey": ["2026-10-12: Over by 2h"]}
		api.save_task(
			"TASK-1", "2026-10-08 09:00:00", expected_time="12.0", crew='[{"resource": "RES-1", "hours": 0}]'
		)
		self.assertEqual(frappe.previews, [])  # nothing the engine reads changed: no check at all
		self.assertEqual(len(frappe.saved), 1)
		frappe.saved.clear()
		self._doc(modified="2026-10-08 09:00:00")
		api.save_task("TASK-1", "2026-10-08 09:00:00", expected_time=13)
		self.assertEqual(len(frappe.saved), 1)  # still over on Monday, but not newly

	def test_unset_arguments_leave_the_task_alone(self):
		self._doc(custom_crew_size=2)
		api.save_task(
			"TASK-1",
			"2026-10-08 09:00:00",
			expected_time="",
			crew_size="null",
			crew=None,
			credentials='["Forklift"]',
		)
		saved = frappe.saved[0]
		self.assertEqual((saved["expected_time"], saved["custom_crew_size"]), (12, 2))
		self.assertEqual(saved["custom_required_credentials"], [{"credential_type": "Forklift"}])
		self.assertEqual(saved["custom_crew"][0]["resource"], "RES-1")

	def test_a_crew_is_checked_against_the_resources(self):
		self._doc()
		with self.assertRaises(_Throw):
			api.save_task("TASK-1", "2026-10-08 09:00:00", crew='["RES-404"]')
		with self.assertRaises(_Throw):
			api.save_task(
				"TASK-1", "2026-10-08 09:00:00", crew='["RES-3"]'
			)  # inactive, and not on it already
		with self.assertRaises(_Throw):
			api.save_task("TASK-1", "2026-10-08 09:00:00", crew='[{"resource": "RES-2", "hours": -1}]')
		self.assertEqual(frappe.saved, [])
		api.save_task(
			"TASK-1", "2026-10-08 09:00:00", crew='[{"resource": "RES-2", "hours": 3, "is_lead": 1}, "RES-2"]'
		)
		self.assertEqual(
			frappe.saved[0]["custom_crew"],
			[
				{
					"resource": "RES-2",
					"resource_name": "Lisa",
					"user": "lisa@example.com",
					"hours": 3.0,
					"is_lead": 1,
				}
			],
		)

	def test_a_rental_tasks_dates_belong_to_its_booking(self):
		self._doc(custom_rental_booking="RB-1")
		with self.assertRaises(_Throw):
			api.save_task("TASK-1", "2026-10-08 09:00:00", start="2026-10-19")
		api.save_task("TASK-1", "2026-10-08 09:00:00", start="2026-10-12")  # unchanged dates are fine
		self.assertEqual(len(frappe.saved), 1)

	def test_add_crew_appends_once_and_keeps_assignment_crew(self):
		self._doc()
		result = api.add_crew("TASK-1", "RES-1", "2026-10-08 09:00:00")
		self.assertEqual((frappe.saved, result["name"]), ([], "TASK-1"))  # already on it
		self._doc(custom_crew=[])
		frappe.tables["ToDo"] = [{"allocated_to": "austin@example.com"}]
		api.add_crew("TASK-1", "RES-2", "2026-10-08 09:00:00")
		self.assertEqual([row["resource"] for row in frappe.saved[0]["custom_crew"]], ["RES-1", "RES-2"])

	def test_swap_crew_hands_the_place_over(self):
		self._doc(custom_crew=[_Doc(resource="RES-1", resource_name="Austin Healey", hours=5, is_lead=1)])
		api.swap_crew("TASK-1", "RES-1", "RES-2", "2026-10-08 09:00:00", date="2026-10-19")
		saved = frappe.saved[0]
		self.assertEqual(
			[(r["resource"], r["hours"], r["is_lead"]) for r in saved["custom_crew"]], [("RES-2", 5.0, 1)]
		)
		self.assertEqual(saved["exp_start_date"], "2026-10-19 08:00:00")
		self._doc(modified="2026-10-08 09:00:00")
		with self.assertRaises(_Throw):
			api.swap_crew("TASK-1", "RES-9", "RES-2", "2026-10-08 09:00:00")

	def test_outsiders_are_refused(self):
		frappe.roles = ["Customer"]
		self._doc()
		with self.assertRaises(_PermissionError):
			api.save_task("TASK-1", "2026-10-08 09:00:00", start="2026-10-19")
		with self.assertRaises(_PermissionError):
			api.get_planner("2026-10-12", "2026-10-18")
		frappe.session.user = "Administrator"
		api.save_task("TASK-1", "2026-10-08 09:00:00", start="2026-10-19")

	def test_ranges_are_bounded(self):
		with self.assertRaises(_Throw):
			api.get_planner("2026-10-01", "2027-03-01")
		with self.assertRaises(_Throw):
			api.get_planner("2026-10-31", "2026-10-01")


class TestGetPlanner(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_tasks_trays_and_the_bookings_it_does_not_own(self):
		austin = _person("RES-1", user="austin@example.com", employee="EMP-1")
		frappe.sql_rows["INNER JOIN `tabProject` p"] = [_task("T-UNDATED", start=None, project_title="Vegas")]
		frappe.sql_rows["FROM `tabProject` p"] = [{"name": "PRJ-1", "title": "Vegas", "pm": "Nik Bradshaw"}]
		frappe.tables["Task Required Credential"] = [{"parent": "T-SHORT", "credential_type": "Forklift"}]
		with _Readers(
			[austin],
			tasks=[
				_task("T-SHORT", start=MON, expected_time=4, custom_crew_size=2),
				_task("T-NOBODY", start=TUE),
				_task("T-RENTAL", start=WED, custom_rental_booking="RB-1", custom_rental_task_kind="Setup"),
				_task(
					"T-RENTAL-EMPTY",
					start=THU,
					custom_rental_booking="RB-2",
					custom_rental_task_kind="Delivery",
				),
			],
			crew_rows={"T-SHORT": [{"resource": "RES-1"}], "T-RENTAL": [{"resource": "RES-1"}]},
			visits=[
				{
					"user": "austin@example.com",
					"date": MON,
					"ref": "SMR-1",
					"label": "Highlands",
					"project": "PRJ-9",
					"hours": 2.0,
					"slot": None,
					"estimated": True,
				}
			],
		):
			data = api.get_planner("2026-10-12", "2026-10-18")

		self.assertEqual([card["name"] for card in data["tasks"]], ["T-SHORT", "T-NOBODY"])
		short = data["tasks"][0]
		self.assertEqual(
			(short["short"], short["credentials"], short["crew"][0]["booked"]), (1, ["Forklift"], 4.0)
		)
		self.assertEqual(short["crew"][0]["label"], "Austin Healey")
		self.assertEqual(data["needs_crew"], ["T-SHORT", "T-NOBODY"])
		self.assertEqual([card["name"] for card in data["unscheduled"]], ["T-UNDATED"])
		self.assertEqual(
			sorted((f["kind"], f["ref"], f["date"], f["resource"]) for f in data["foreign"]),
			[
				("rental", "T-RENTAL", "2026-10-14", "RES-1"),
				("rental", "T-RENTAL-EMPTY", "2026-10-15", None),
				("visit", "SMR-1", "2026-10-12", "RES-1"),
			],
		)
		self.assertEqual(data["projects"], [{"name": "PRJ-1", "title": "Vegas", "pm": "Nik Bradshaw"}])
		self.assertEqual(data["settings"], {"default_day_hours": 8.0, "maintenance_visit_hours": 2.0})
		self.assertTrue(data["can_edit"])
		self.assertEqual(
			set(data),
			{
				"start",
				"end",
				"today",
				"resources",
				"days",
				"tasks",
				"unscheduled",
				"needs_crew",
				"foreign",
				"projects",
				"settings",
				"can_edit",
			},
		)


# ---------------------------------------------------------------------- routes (Phase 2)

# The shop in Bountiful, a Highlands site 30-odd km south, a second job 2 km from it, and a job up
# in Ogden: enough geography for "already nearby" to mean something.
SHOP = (40.884, -111.882)
HIGHLANDS = (40.6, -111.85)
NEAR_HIGHLANDS = (40.62, -111.86)
OGDEN = (41.1, -112.0)


class TestDrivePadding(unittest.TestCase):
	def setUp(self):
		_reset()

	def _two_sites(self, **kwargs):
		return _Readers(
			[_person("RES-1", employee="EMP-1")],
			tasks=[_task("T-H", start=MON, expected_time=4), _task("T-O", start=MON, expected_time=4)],
			crew_rows={"T-H": [{"resource": "RES-1"}], "T-O": [{"resource": "RES-1"}]},
			points={"T-H": HIGHLANDS, "T-O": OGDEN},
			shop=SHOP,
			**kwargs,
		)

	def test_the_days_driving_is_booked_and_makes_a_full_day_over(self):
		with self._two_sites() as readers:
			monday = engine.availability(MON, MON)["days"]["RES-1"]["2026-10-12"]
		drive = [b for b in monday["bookings"] if b["kind"] == "drive"]
		self.assertEqual(len(drive), 1)
		self.assertEqual(
			{k: drive[0][k] for k in ("ref", "label", "project", "slot", "estimated")},
			{"ref": None, "label": "Driving", "project": None, "slot": None, "estimated": True},
		)
		self.assertGreater(monday["drive_minutes"], 60)  # Bountiful → Highlands → Ogden → Bountiful
		self.assertEqual(drive[0]["hours"], round(monday["drive_minutes"] / 60, 2))
		self.assertEqual(monday["booked"], round(8 + drive[0]["hours"], 2))
		self.assertEqual(monday["free"], 0.0)
		self.assertEqual(monday["conflicts"], [f"Over by {engine.fmt_hours(drive[0]['hours'])}h"])
		self.assertEqual((monday["drive_source"], monday["unlocated"]), ("estimate", 0))
		self.assertTrue(monday["long_drive"])  # well past the default 90 minutes
		self.assertEqual(len(readers.matrix_calls), 1)

	def test_padding_off_still_reports_the_driving(self):
		with self._two_sites(settings={"pad_drive_time": 0}):
			monday = engine.availability(MON, MON)["days"]["RES-1"]["2026-10-12"]
		self.assertEqual([b["kind"] for b in monday["bookings"]], ["task", "task"])
		self.assertEqual((monday["booked"], monday["conflicts"]), (8.0, []))
		self.assertGreater(monday["drive_minutes"], 0)

	def test_a_long_drive_is_against_the_settings_threshold(self):
		with self._two_sites(settings={"long_drive_minutes": 600}):
			monday = engine.availability(MON, MON)["days"]["RES-1"]["2026-10-12"]
		self.assertFalse(monday["long_drive"])

	def test_google_legs_are_not_estimated(self):
		legs = {}
		for a in (SHOP, HIGHLANDS, OGDEN):
			for b in (SHOP, HIGHLANDS, OGDEN):
				if a != b:
					legs[(a, b)] = {"minutes": 30.0, "km": 30.0}
		with self._two_sites(matrix=legs):
			monday = engine.availability(MON, MON)["days"]["RES-1"]["2026-10-12"]
		self.assertEqual((monday["drive_minutes"], monday["drive_source"]), (90.0, "google"))
		self.assertFalse(next(b for b in monday["bookings"] if b["kind"] == "drive")["estimated"])

	def test_one_drive_matrix_call_for_every_person_day(self):
		people = [_person("RES-1", employee="EMP-1"), _person("RES-2", employee="EMP-2", label="Lisa")]
		tasks = [_task(f"T-{i}", start=MON, end=FRI, expected_time=10) for i in range(4)]
		crew_rows = {f"T-{i}": [{"resource": "RES-1"}, {"resource": "RES-2"}] for i in range(4)}
		points = {"T-0": HIGHLANDS, "T-1": OGDEN, "T-2": NEAR_HIGHLANDS, "T-3": HIGHLANDS}
		with _Readers(people, tasks=tasks, crew_rows=crew_rows, points=points, shop=SHOP) as readers:
			days = engine.availability(MON, FRI)["days"]
		self.assertEqual(len(readers.matrix_calls), 1)  # ten person-days, one call
		self.assertTrue(all(days[r][str(d)]["drive_minutes"] > 0 for r in days for d in (MON, FRI)))
		# Distinct points only: shop + three sites (two tasks share Highlands) → 4 × 3 legs.
		self.assertEqual(len(readers.matrix_calls[0]), 12)

	def test_stops_with_no_location_are_counted_and_not_driven(self):
		with _Readers(
			[_person()],
			tasks=[_task("T-H", start=MON, expected_time=2), _task("T-X", start=MON, expected_time=2)],
			crew_rows={"T-H": [{"resource": "RES-1"}], "T-X": [{"resource": "RES-1"}]},
			points={"T-H": HIGHLANDS},
			shop=SHOP,
		) as readers:
			monday = engine.availability(MON, MON)["days"]["RES-1"]["2026-10-12"]
		self.assertEqual(monday["unlocated"], 1)
		round_trip = routing.estimate_leg(SHOP, HIGHLANDS)["minutes"] + routing.estimate_leg(HIGHLANDS, SHOP)["minutes"]
		self.assertEqual(monday["drive_minutes"], round(round_trip, 1))
		self.assertEqual(readers.missing, [["T-X"]])  # the backfill is asked to find it

	def test_nothing_located_is_no_padding_and_no_source(self):
		with _Readers([_person()], tasks=[_task("T", start=MON, expected_time=2)], crew_rows={"T": [{"resource": "RES-1"}]}):
			monday = engine.availability(MON, MON)["days"]["RES-1"]["2026-10-12"]
		self.assertEqual(
			{k: monday[k] for k in ("drive_minutes", "drive_source", "long_drive", "unlocated")},
			{"drive_minutes": 0.0, "drive_source": None, "long_drive": False, "unlocated": 1},
		)
		self.assertEqual([b["kind"] for b in monday["bookings"]], ["task"])

	def test_a_travel_day_has_no_route(self):
		with self._two_sites(
			travel=[{"employee": "EMP-1", "date": MON, "ref": "TRIP-1", "label": "Travel: Vegas", "project": None}]
		) as readers:
			monday = engine.availability(MON, MON)["days"]["RES-1"]["2026-10-12"]
		self.assertNotIn("drive", [b["kind"] for b in monday["bookings"]])
		self.assertEqual((monday["drive_minutes"], monday["unlocated"]), (0.0, 0))
		self.assertEqual(readers.matrix_calls, [])

	def test_routing_raising_never_breaks_availability(self):
		def boom(*args, **kwargs):
			raise RuntimeError("Google exploded")

		with self._two_sites(), mock.patch.object(routing, "plan_routes", boom):
			monday = engine.availability(MON, MON)["days"]["RES-1"]["2026-10-12"]
		self.assertEqual((monday["booked"], monday["conflicts"]), (8.0, []))
		self.assertEqual((monday["drive_minutes"], monday["drive_source"]), (0.0, None))
		self.assertEqual(frappe.errors[-1][1]["title"], "Project Planner: routes failed")
		self.assertEqual(frappe.errors[-1][0], ())  # keyword arguments only

	def test_a_preview_counts_the_driving_a_far_task_adds(self):
		# 7h at Highlands already; moving a 1h Ogden task onto the same day fits by hours but not
		# once the drive up to Ogden is counted.
		stored = _task("T-O", start=TUE, expected_time=1)
		with _Readers(
			[_person()],
			tasks=[_task("T-H", start=MON, expected_time=7), stored],
			crew_rows={"T-H": [{"resource": "RES-1"}], "T-O": [{"resource": "RES-1"}]},
			points={"T-H": HIGHLANDS, "T-O": OGDEN},
			shop=SHOP,
		):
			moved = dict(stored, exp_start_date=MON)
			got = engine.preview_conflicts(moved, [{"resource": "RES-1"}])
		self.assertEqual(list(got), ["Austin Healey"])
		self.assertTrue(got["Austin Healey"][0].startswith("2026-10-12: Over by"))


class TestSettingsWithRoutes(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_unsaved_route_fields_read_as_their_defaults(self):
		# An existing Single row has no tabSingles row for the Phase 2 fields until the backfill.
		frappe.cached["Project Planner Settings"] = _Doc(default_day_hours=8, pad_drive_time=None)
		got = engine.get_settings()
		self.assertEqual(
			{k: got[k] for k in ("pad_drive_time", "use_google_routes", "long_drive_minutes", "day_start_time")},
			{"pad_drive_time": 1.0, "use_google_routes": 1.0, "long_drive_minutes": 90.0, "day_start_time": "08:00:00"},
		)

	def test_a_deliberate_off_and_a_time_stay(self):
		frappe.cached["Project Planner Settings"] = _Doc(
			pad_drive_time=0, use_google_routes=0, long_drive_minutes=0, day_start_time="07:30:00"
		)
		got = engine.get_settings()
		self.assertEqual((got["pad_drive_time"], got["use_google_routes"], got["long_drive_minutes"]), (0.0, 0.0, 0.0))
		self.assertEqual(got["day_start_time"], "07:30:00")


class TestGetRoute(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.tables["Project"] = [
			{"name": "PRJ-H", "project_name": "Highlands Maintenance Contract"},
			{"name": "PRJ-O", "project_name": "Ogden Plaza"},
		]
		frappe.singles[("Travel Settings", "google_maps_api_key")] = "browser-key"
		frappe.singles[("Travel Settings", "google_maps_map_id_light")] = "light-id"

	def _readers(self, **kwargs):
		return _Readers(
			[_person("RES-1", label="Jesse Pinkman", employee="EMP-1")],
			tasks=[
				_task("T-O", start=MON, expected_time=2, project="PRJ-O", subject="Install pump"),
				_task(
					"T-X",
					start=MON,
					project=None,
					subject="Pick up parts",
					custom_start_datetime="2026-10-12 10:00:00",
					custom_end_datetime="2026-10-12 11:00:00",
				),
			],
			crew_rows={"T-O": [{"resource": "RES-1"}], "T-X": [{"resource": "RES-1"}]},
			visits=[
				{
					"user": "res-1@example.com",
					"date": MON,
					"ref": "SMR-1",
					"label": "Highlands · Winterization",
					"project": "PRJ-H",
					"hours": 2.0,
					"slot": None,
					"estimated": True,
				}
			],
			points={"SMR-1": HIGHLANDS, "T-O": OGDEN},
			shop=SHOP,
			# Google legs that make Highlands-first the only sensible order (100 min) and the
			# reverse loop expensive (180 min).
			matrix={
				(SHOP, HIGHLANDS): {"minutes": 30.0, "km": 35.0},
				(HIGHLANDS, OGDEN): {"minutes": 45.0, "km": 60.0},
				(OGDEN, SHOP): {"minutes": 25.0, "km": 28.0},
				(SHOP, OGDEN): {"minutes": 60.0, "km": 28.0},
				(OGDEN, HIGHLANDS): {"minutes": 60.0, "km": 60.0},
				(HIGHLANDS, SHOP): {"minutes": 60.0, "km": 35.0},
			},
			**kwargs,
		)

	def test_the_day_as_a_drive(self):
		with self._readers():
			got = api.get_route("RES-1", "2026-10-12")
		self.assertEqual((got["resource"], got["label"], got["date"]), ("RES-1", "Jesse Pinkman", "2026-10-12"))
		self.assertEqual(
			got["start"],
			{"label": "Shop", "address": "85 W 300 S, Bountiful, UT 84010", "lat": SHOP[0], "lng": SHOP[1]},
		)
		self.assertEqual([s["ref"] for s in got["stops"]], ["SMR-1", "T-X", "T-O"])
		visit, unlocated, ogden = got["stops"]
		# A visit is labelled with its site; the contract boilerplate is dropped.
		self.assertEqual((visit["label"], visit["kind"], visit["located"]), ("Highlands", "visit", True))
		self.assertEqual(visit["project_title"], "Highlands Maintenance Contract")
		self.assertEqual((visit["arrive"], visit["depart"], visit["drive_minutes"], visit["km"]), ("08:30", "10:30", 30.0, 35.0))
		self.assertEqual(visit["address"], "Address of SMR-1")
		# The slotted stop with no location sits by its time (after the visit that starts before
		# 10:00, before Ogden) and is not driven to: it starts when the visit ends.
		self.assertEqual(
			{k: unlocated[k] for k in ("located", "lat", "lng", "drive_minutes", "km", "arrive")},
			{"located": False, "lat": None, "lng": None, "drive_minutes": None, "km": None, "arrive": visit["depart"]},
		)
		# Driven from Highlands, past the stop with no location; it waited for nothing.
		self.assertEqual((ogden["drive_minutes"], ogden["km"], ogden["arrive"]), (45.0, 60.0, "12:15"))
		self.assertEqual(got["unlocated"], [{"kind": "task", "ref": "T-X", "label": "Pick up parts"}])
		self.assertEqual([s["order"] for s in got["stops"]], [1, 2, 3])
		self.assertEqual((got["drive_minutes"], got["km"], got["source"]), (100.0, 123.0, "google"))
		self.assertTrue(got["long_drive"])  # over the default 90 minutes
		self.assertEqual(
			{k: got["end"][k] for k in ("label", "arrive", "drive_minutes", "km")},
			{"label": "Shop", "arrive": "14:40", "drive_minutes": 25.0, "km": 28.0},
		)
		self.assertEqual(
			got["maps_url"],
			"https://www.google.com/maps/dir/?api=1&origin=40.88400,-111.88200&destination=40.88400,-111.88200"
			"&travelmode=driving&waypoints=40.60000,-111.85000|41.10000,-112.00000",
		)
		self.assertEqual(
			(got["maps_key"], got["map_ids"]), ("browser-key", {"map_id_light": "light-id", "map_id_dark": ""})
		)
		self.assertEqual((got["travel"], got["off"]), (None, None))
		self.assertEqual(
			set(got),
			{
				"resource", "label", "date", "day_start", "start", "end", "stops", "drive_minutes", "km",
				"source", "long_drive", "unlocated", "maps_url", "maps_key", "map_ids", "travel", "off",
			},
		)

	def test_a_travel_day_lists_the_stops_without_a_route(self):
		with self._readers(
			travel=[{"employee": "EMP-1", "date": MON, "ref": "TRIP-1", "label": "Travel: Vegas", "project": None}]
		):
			got = api.get_route("RES-1", "2026-10-12")
		self.assertEqual(got["travel"], "Travel: Vegas")
		self.assertEqual((got["start"], got["maps_url"], got["drive_minutes"]), (None, None, 0.0))
		self.assertEqual(len(got["stops"]), 3)
		self.assertTrue(all(s["arrive"] is None and not s["located"] for s in got["stops"]))

	def test_an_unknown_resource_is_refused(self):
		with self._readers(), self.assertRaises(_Throw):
			api.get_route("RES-404", "2026-10-12")

	def test_outsiders_are_refused(self):
		frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			api.get_route("RES-1", "2026-10-12")


class TestSuggestDates(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.tables["Project"] = [{"name": "PRJ-H", "project_name": "Highlands"}]
		frappe.tables["Planner Resource"] = [{"name": "RES-1"}, {"name": "RES-2"}]
		frappe.values[("Project", "PRJ-N", "project_name")] = "Highlands East"
		doc = _TaskDoc(_task("T-NEW", start=None, expected_time=4, project="PRJ-N", subject="Replace lights"))
		doc["custom_crew"] = []
		frappe.docs[("Task", "T-NEW")] = doc

	def _readers(self, points):
		return _Readers(
			[_person("RES-1", label="Jesse Pinkman"), _person("RES-2", label="Korben Dallas")],
			tasks=[_task("T-H", start=TUE, expected_time=2, project="PRJ-H")],
			crew_rows={"T-H": [{"resource": "RES-1"}]},
			points=points,
			shop=SHOP,
		)

	def test_the_person_already_nearby_comes_first(self):
		with self._readers({"T-H": HIGHLANDS, "T-NEW": NEAR_HIGHLANDS}):
			got = api.suggest_dates("T-NEW", start="2026-10-12", days=3)
		self.assertEqual((got["task"], got["subject"], got["hours_needed"], got["note"]), ("T-NEW", "Replace lights", 4.0, None))
		self.assertEqual(got["site"], {"lat": NEAR_HIGHLANDS[0], "lng": NEAR_HIGHLANDS[1], "label": "Highlands East"})
		best = got["suggestions"][0]
		self.assertEqual((best["date"], best["resource"], best["label"]), ("2026-10-13", "RES-1", "Jesse Pinkman"))
		self.assertEqual(best["nearby"], [{"ref": "T-H", "label": "Highlands", "km": 2.4}])
		self.assertLess(best["added_minutes"], 15)
		self.assertTrue(best["reason"].startswith("Jesse is already at Highlands that day (2.4 km away): +"))
		self.assertEqual(best["source"], "estimate")
		# Everyone else has an empty day: a round trip from the shop, same score, by date then name.
		rest = got["suggestions"][1:]
		self.assertEqual(
			[(s["date"], s["resource"]) for s in rest],
			[("2026-10-12", "RES-1"), ("2026-10-12", "RES-2"), ("2026-10-13", "RES-2"), ("2026-10-14", "RES-1"), ("2026-10-14", "RES-2")],
		)
		self.assertTrue(rest[0]["reason"].startswith("Nobody is near; Jesse's day is empty: +"))
		self.assertTrue(all(s["score"] > best["score"] for s in rest))
		self.assertEqual(
			set(best),
			{"date", "resource", "label", "free_hours", "added_minutes", "source", "nearby", "long_drive", "score", "reason"},
		)

	def test_a_task_with_no_location_is_ranked_by_free_hours(self):
		with self._readers({"T-H": HIGHLANDS}):
			got = api.suggest_dates("T-NEW", start="2026-10-12", days=3)
		self.assertIsNone(got["site"])
		self.assertEqual(got["note"], api.NO_LOCATION_NOTE)
		first = got["suggestions"][0]
		self.assertEqual((first["added_minutes"], first["source"], first["score"]), (None, None, 0.0))
		self.assertEqual(first["free_hours"], 8.0)
		self.assertEqual(first["reason"], "Jesse has 8h free.")
		# Jesse's Tuesday (2h booked plus the drive) has the least free time, so it comes last.
		self.assertEqual((got["suggestions"][-1]["resource"], got["suggestions"][-1]["date"]), ("RES-1", "2026-10-13"))

	def test_the_past_is_never_suggested_and_the_window_is_capped(self):
		with self._readers({}):
			got = api.suggest_dates("T-NEW", start="2026-01-01", days=99)
		self.assertTrue(all(s["date"] >= str(TODAY) for s in got["suggestions"]))
		self.assertEqual(len(got["suggestions"]), api.SUGGEST_LIMIT)

	def test_hours_per_day(self):
		settings = _settings()
		self.assertIsNone(api.hours_per_day(_task(start=None, expected_time=0), 0, settings, MON))
		self.assertEqual(api.hours_per_day(_task(start=MON, end=TUE, expected_time=16), 2, settings, MON), 4.0)
		self.assertEqual(api.hours_per_day(_task(start=None, expected_time=24), 1, settings, MON), 8.0)
		slot = _task(custom_start_datetime="2026-10-12 09:00:00", custom_end_datetime="2026-10-12 10:30:00")
		self.assertEqual(api.hours_per_day(slot, 3, settings, MON), 1.5)


class TestCheckRoutes(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_only_managers_may_ask_google(self):
		with mock.patch.object(routing, "routes_status", lambda: {"google": True, "detail": "ok"}):
			with self.assertRaises(_PermissionError):
				api.check_routes()  # a Projects User
			frappe.roles = ["Projects Manager"]
			self.assertEqual(api.check_routes(), {"google": True, "detail": "ok"})


# ---------------------------------------------------------------------- Phase 3A: tentative (engine)


class TestTentativeEngine(unittest.TestCase):
	"""A pencil books softly: soft hours and a warning, never a conflict and never a drive."""

	def setUp(self):
		_reset()

	def _day(self, tasks, crew_rows, **kwargs):
		with _Readers([_person()], tasks=tasks, crew_rows=crew_rows, **kwargs) as readers:
			day = engine.availability(MON, MON)["days"]["RES-1"]["2026-10-12"]
		return day, readers

	def test_a_pencil_books_softly_and_warns_instead_of_conflicting(self):
		monday, _readers = self._day(
			[
				_task("FIRM", start=MON, expected_time=6),
				_task("PENCIL", start=MON, expected_time=4, custom_tentative=1),
			],
			{"FIRM": [{"resource": "RES-1"}], "PENCIL": [{"resource": "RES-1"}]},
		)
		by_ref = {b["ref"]: b for b in monday["bookings"]}
		self.assertTrue(by_ref["PENCIL"]["tentative"])
		self.assertNotIn("tentative", by_ref["FIRM"])
		self.assertEqual((monday["booked"], monday["soft_booked"], monday["free"]), (6.0, 4.0, 2.0))
		self.assertEqual(monday["conflicts"], [])
		self.assertEqual(monday["warnings"], ["Pencilled work would put them over by 2h"])

	def test_a_day_without_a_pencil_has_no_soft_hours(self):
		monday, _readers = self._day(
			[_task("FIRM", start=MON, expected_time=10)], {"FIRM": [{"resource": "RES-1"}]}
		)
		self.assertEqual((monday["soft_booked"], monday["conflicts"], monday["warnings"]), (0.0, ["Over by 2h"], []))

	def test_day_conflicts_leave_pencils_out_and_pencil_warning_counts_both(self):
		firm = {"hours": 6, "slot": ["09:00", "11:00"], "ref": "A"}
		pencil = {"hours": 4, "slot": ["10:00", "12:00"], "ref": "B", "tentative": True}
		self.assertEqual(engine.day_conflicts(8, None, [firm, pencil]), [])  # no overlap, no "Over by"
		self.assertEqual(engine.day_conflicts(0, "Time off", [pencil]), [])
		self.assertEqual(engine.pencil_warning(8, [firm, pencil]), "Pencilled work would put them over by 2h")
		self.assertEqual(engine.pencil_warning(0, [pencil]), "Pencilled work would put them over by 4h")
		self.assertIsNone(engine.pencil_warning(10, [firm, pencil]))
		self.assertIsNone(engine.pencil_warning(4, [firm]))  # over with no pencil is a conflict, not this
		self.assertTrue(engine.is_tentative({"custom_tentative": "1"}))
		for value in (0, "0", None, ""):
			self.assertFalse(engine.is_tentative({"custom_tentative": value}))

	def test_a_pencil_never_needs_a_reason_but_firming_it_up_is_checked(self):
		other = _task("T-OTHER", start=MON, expected_time=6)
		pencil = _task("T-PEN", start=TUE, expected_time=4, custom_tentative=1)
		crew = [{"resource": "RES-1"}]
		with _Readers(
			[_person()], tasks=[other, pencil], crew_rows={"T-OTHER": crew, "T-PEN": crew}
		):
			moved = dict(pencil, exp_start_date=MON)
			self.assertEqual(engine.preview_conflicts(moved, crew), {})
			self.assertEqual(engine._preview(moved, crew)[1], {"RES-1": 4.0})  # its card still shows hours
			self.assertEqual(
				engine.preview_conflicts(dict(moved, custom_tentative=0), crew),
				{"Austin Healey": ["2026-10-12: Over by 2h"]},
			)

	def test_pencilled_work_is_not_driven_to(self):
		monday, readers = self._day(
			[_task("T-H", start=MON, expected_time=2, custom_tentative=1)],
			{"T-H": [{"resource": "RES-1"}]},
			points={"T-H": HIGHLANDS},
			shop=SHOP,
		)
		self.assertEqual([b["kind"] for b in monday["bookings"]], ["task"])
		self.assertEqual((monday["drive_minutes"], readers.matrix_calls), (0.0, []))

	def test_a_batch_preview_counts_its_moves_against_each_other(self):
		first = _task("T-A", start=TUE, expected_time=5)
		second = _task("T-B", start=WED, expected_time=5)
		crew = [{"resource": "RES-1"}]
		with _Readers([_person()], tasks=[first, second], crew_rows={"T-A": crew, "T-B": crew}):
			self.assertEqual(engine.preview_conflicts(dict(first, exp_start_date=MON), crew), {})
			got = engine.preview_batch(
				[(dict(first, exp_start_date=MON), crew), (dict(second, exp_start_date=MON), crew)]
			)
			pencilled = engine.preview_batch(
				[
					(dict(first, exp_start_date=MON), crew),
					(dict(second, exp_start_date=MON, custom_tentative=1), crew),
				]
			)
		self.assertEqual(got, {"Austin Healey": ["2026-10-12: Over by 2h"]})
		self.assertEqual(pencilled, {})  # the pencil is soft, so the firm one fits
		self.assertEqual(engine.preview_batch([]), {})


# ---------------------------------------------------------------------- Phase 3A: pure API helpers


class TestPhase3AHelpers(unittest.TestCase):
	def test_working_days(self):
		self.assertEqual(api.add_working_days(MON, 2), WED)
		self.assertEqual(api.add_working_days(FRI, 1), D(2026, 10, 19))
		self.assertEqual(api.add_working_days(SAT, 1), D(2026, 10, 19))
		self.assertEqual(api.add_working_days(MON, -1), D(2026, 10, 9))
		self.assertEqual(api.add_working_days(MON, 0), MON)
		self.assertEqual(api.working_days_between(MON, WED), 2)
		self.assertEqual(api.working_days_between(FRI, D(2026, 10, 19)), 1)
		self.assertEqual(api.working_days_between(FRI, SUN), 0)
		self.assertEqual(api.working_days_between(WED, MON), -2)
		self.assertEqual(api.shift_working("2026-10-16 09:30:00", 2), "2026-10-20 09:30:00")
		self.assertEqual(api.shift_working("2026-10-16", 1), "2026-10-19")
		self.assertIsNone(api.shift_working(None, 1))
		# Weeks start on the site's first weekday; Sunday is the default (and production's).
		self.assertEqual(api.week_start(SUN, 0), MON)
		self.assertEqual(api.week_start(SUN), SUN)
		self.assertEqual(api.week_start(SAT), D(2026, 10, 11))
		self.assertEqual((api.first_weekday("Monday"), api.first_weekday("sunday")), (0, 6))
		self.assertEqual((api.first_weekday(""), api.first_weekday(None), api.first_weekday("Funday")), (6, 6, 6))
		self.assertTrue(api.as_bool("true") and api.as_bool(1) and api.as_bool("1"))
		self.assertFalse(api.as_bool("false") or api.as_bool("0") or api.as_bool(0) or api.as_bool(""))

	def test_blocked_by(self):
		task = _task("T", start="2026-10-14 08:00:00", end="2026-10-15 17:00:00")
		pred = _task("P", start=MON, end=WED, subject="Dig")
		self.assertEqual(api.blocked_by(task, [pred]), [])  # a same-day handoff with no times is fine
		late = dict(pred, exp_end_date=THU)
		self.assertEqual(api.blocked_by(task, [late]), [{"task": "P", "subject": "Dig", "end": "2026-10-15"}])
		timed = dict(pred, exp_end_date="2026-10-14 12:00:00")
		self.assertEqual(len(api.blocked_by(task, [timed])), 1)  # both have times: noon is after 8:00
		early = dict(pred, exp_end_date="2026-10-14 07:00:00")
		self.assertEqual(api.blocked_by(task, [early]), [])
		self.assertEqual(api.blocked_by(task, [dict(late, status="Completed")]), [])
		self.assertEqual(api.blocked_by(task, [dict(late, exp_start_date=None, exp_end_date=None)]), [])
		self.assertEqual(api.blocked_by(_task("U", start=None), [late]), [])
		self.assertEqual(
			api.dependency_conflicts(task, [late]), ["Starts before P (Dig) ends on 2026-10-15"]
		)

	def test_qualification_gaps(self):
		crew = [{"resource": "RES-1"}, {"resource": "RES-2"}]
		held = {
			"RES-1": [{"credential_type": "Forklift", "status": "Valid", "expires_on": None}],
			"RES-2": [
				{"credential_type": "Confined Space Entry", "status": "Expiring", "expires_on": "2026-10-20"},
				{"credential_type": "First Aid", "status": "Expired", "expires_on": None},
				{"credential_type": "Rigging", "status": "Valid", "expires_on": "2026-10-13"},
			],
		}
		required = ["Forklift", "Confined Space Entry", "First Aid", "Rigging", "Forklift"]
		self.assertEqual(api.qualification_gaps(required, crew, held, WED), ["First Aid", "Rigging"])
		self.assertEqual(api.qualification_gaps(required, crew, held, D(2026, 10, 21)), ["Confined Space Entry", "First Aid", "Rigging"])
		self.assertEqual(api.qualification_gaps(required, [], held, WED), [])  # no crew: the tray says so
		self.assertEqual(api.qualification_gaps([], crew, held, WED), [])
		self.assertEqual(
			api.qualification_warning(["First Aid", "Rigging"]), "No one on the crew holds: First Aid, Rigging"
		)
		self.assertIsNone(api.qualification_warning([]))

	def test_card_fields(self):
		task = _task("T", start=WED, custom_tentative=1)
		card = api.build_card(
			task,
			[{"resource": "RES-1"}],
			{},
			["Forklift"],
			TODAY,
			True,
			depends_on=["P"],
			predecessors=[_task("P", start=MON, end=THU, subject="Dig")],
			held={},
		)
		self.assertEqual(
			{k: card[k] for k in ("tentative", "depends_on", "blocked_by", "qualification_gaps", "qualification_warning")},
			{
				"tentative": True,
				"depends_on": ["P"],
				"blocked_by": [{"task": "P", "subject": "Dig", "end": "2026-10-15"}],
				"qualification_gaps": ["Forklift"],
				"qualification_warning": "No one on the crew holds: Forklift",
			},
		)
		# Unknown holdings (no HR module) are never a gap; the old positional call still works.
		plain = api.build_card(task, [{"resource": "RES-1"}], {}, ["Forklift"], TODAY, True)
		self.assertEqual((plain["qualification_gaps"], plain["depends_on"], plain["blocked_by"]), ([], [], []))

	def test_heatmap_cell(self):
		cells = [
			{"capacity": 8, "booked": 6, "soft_booked": 2, "free": 2, "off": None},
			{"capacity": 8, "booked": 10, "soft_booked": 0, "free": 0, "off": None},
			{"capacity": 0, "booked": 0, "soft_booked": 0, "free": 0, "off": "Holiday: Columbus Day"},
			{"capacity": 4, "booked": 0, "soft_booked": 0, "free": 4, "off": "Half day off"},
			{"capacity": 0, "booked": 0, "soft_booked": 0, "free": 0, "off": "Not a work day"},
		]
		self.assertEqual(
			api.heatmap_cell(cells),
			{
				"capacity": 20.0,
				"booked": 16.0,
				"soft_booked": 2.0,
				"free": 6.0,
				"over_days": 1,
				"off_days": 2,
				"load": 0.8,
				"level": "amber",
			},
		)

		def level(capacity, booked):
			return api.heatmap_cell([{"capacity": capacity, "booked": booked}])["level"]

		self.assertEqual(
			[level(20, 15), level(20, 20), level(20, 21), level(0, 0), level(0, 2)],
			["green", "amber", "red", None, "red"],
		)


# ---------------------------------------------------------------------- Phase 3A: writes


class _Task3A(_TaskDoc):
	"""A Task doc that knows its name in calls, can be refused or fail, and can be inserted."""

	def check_permission(self, ptype):
		frappe.calls.append(("check_permission", ptype, self.get("name")))
		if self.get("name") in frappe.denied:
			frappe.local.message_log.append({"message": "<b>No permission</b> for Task"})
			raise _PermissionError("No permission for Task")

	def save(self):
		if self.get("name") in frappe.failing:
			frappe.local.message_log.append({"message": "Cannot complete task <b>X</b>."})
			raise _Throw("Cannot complete task X.")
		super().save()
		for hook in frappe.save_hooks:
			hook(self)

	def insert(self):
		self["name"] = f"TASK-NEW-{len(frappe.inserted) + 1}"
		self["modified"] = "2026-10-09 08:00:00"
		frappe.inserted.append(json.loads(json.dumps(dict(self), default=str)))
		return self


MODIFIED = "2026-10-08 09:00:00"


def _tdoc(name, start, end=None, **values):
	doc = _Task3A(_task(name, start=start, end=end, **values))
	doc["doctype"] = "Task"
	doc.setdefault("custom_crew", [_Doc(resource="RES-1", resource_name="Austin Healey", hours=0, is_lead=1)])
	doc.setdefault("custom_required_credentials", [])
	doc.setdefault("depends_on", [])
	frappe.docs[("Task", name)] = doc
	return doc


class _Phase3AWrites(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe.saved, frappe.comments, frappe.calls, frappe.previews = [], [], [], []
		frappe.batches, frappe.inserted, frappe.savepoints, frappe.save_hooks = [], [], [], []
		frappe.denied, frappe.failing = set(), set()
		frappe.db.savepoint = lambda name: frappe.savepoints.append(("savepoint", name))
		frappe.db.rollback = lambda save_point=None: frappe.savepoints.append(("rollback", save_point))
		frappe.tables["Planner Resource"] = [
			{"name": "RES-1", "resource_name": "Austin Healey", "user": "austin@example.com", "is_active": 1},
			{"name": "RES-2", "resource_name": "Lisa", "user": "lisa@example.com", "is_active": 1},
		]
		frappe.values[("Project", "PRJ-1", "project_name")] = "Vegas Water Wall"
		# ERPNext's dependency table as (task that depends, the task it depends on) pairs, and the
		# Task rows the API reads back for spans and successors.
		self.edges = []
		self.rows = {}
		frappe.tables["Task Depends On"] = self._depends_on
		frappe.tables["Task"] = lambda filters: [
			dict(self.rows[name]) for name in (filters or {}).get("name", [None, []])[1] if name in self.rows
		]
		self.conflicts, self.batch = {}, []
		self.patches = [
			mock.patch.object(engine, "preview_conflicts", self._preview),
			mock.patch.object(engine, "_preview", lambda task, crew, *a: ({}, {})),
			mock.patch.object(engine, "preview_batch", self._batch),
		]
		for patch in self.patches:
			patch.start()

	def tearDown(self):
		for patch in self.patches:
			patch.stop()

	def _depends_on(self, filters):
		filters = filters or {}
		if "task" in filters:
			return [{"parent": parent} for parent, task in self.edges if task in filters["task"][1]]
		return [{"parent": parent, "task": task} for parent, task in self.edges if parent in filters["parent"][1]]

	def _preview(self, task, crew, start=None, end=None):
		frappe.previews.append(dict(task))
		if engine.is_tentative(task):
			return {}
		span = engine.task_span(task)
		return self.conflicts.get(span[0] if span else None, {})

	def _batch(self, changes, start=None, end=None):
		frappe.batches.append([(dict(task), list(crew)) for task, crew in changes])
		return self.batch.pop(0) if self.batch else {}

	def _row(self, name, start, end=None, **values):
		self.rows[name] = dict(_task(name, start=start, end=end, **values))
		return self.rows[name]


class TestTentativeWrites(_Phase3AWrites):
	def test_firming_up_a_pencil_is_checked_like_any_change(self):
		_tdoc("TASK-1", "2026-10-12 08:00:00", "2026-10-14 17:00:00", expected_time=12, custom_tentative=1)
		self.conflicts[MON] = {"Austin Healey": ["2026-10-12: Over by 2h"]}
		result = api.save_task("TASK-1", MODIFIED, tentative="false")
		self.assertEqual(result, {"needs_reason": True, "conflicts": {"Austin Healey": ["2026-10-12: Over by 2h"]}})
		self.assertEqual(frappe.saved, [])
		result = api.save_task("TASK-1", MODIFIED, tentative=0, reason="Client confirmed")
		self.assertEqual(frappe.saved[0]["custom_tentative"], 0)
		self.assertFalse(result["card"]["tentative"])

	def test_pencilling_needs_no_reason_and_unset_leaves_it_alone(self):
		_tdoc("TASK-1", "2026-10-12 08:00:00", "2026-10-14 17:00:00", expected_time=12)
		self.conflicts[MON] = {"Austin Healey": ["2026-10-12: Over by 2h"]}
		result = api.save_task("TASK-1", MODIFIED, tentative="true")
		self.assertEqual(frappe.saved[0]["custom_tentative"], 1)
		self.assertTrue(result["card"]["tentative"])
		frappe.saved.clear()
		_tdoc("TASK-1", "2026-10-12 08:00:00", "2026-10-14 17:00:00", custom_tentative=1)
		api.save_task("TASK-1", MODIFIED, tentative="null", expected_time=4)
		self.assertEqual(frappe.saved[0]["custom_tentative"], 1)


class TestDependencyWrites(_Phase3AWrites):
	def _doc_after(self, pred_end, **values):
		self._row("TASK-0", D(2026, 10, 5), pred_end, subject="Dig")
		return _tdoc(
			"TASK-1",
			"2026-10-12 08:00:00",
			"2026-10-14 17:00:00",
			expected_time=12,
			depends_on=[_Doc(task="TASK-0")],
			**values,
		)

	def test_starting_before_a_predecessor_ends_needs_a_reason(self):
		self._doc_after(D(2026, 10, 9))
		result = api.save_task("TASK-1", MODIFIED, start="2026-10-08")
		self.assertEqual(
			result,
			{"needs_reason": True, "conflicts": {"Dependencies": ["Starts before TASK-0 (Dig) ends on 2026-10-09"]}},
		)
		self.assertEqual(frappe.saved, [])
		self._doc_after(D(2026, 10, 9))  # a fresh load, as the page's second request gets
		result = api.save_task("TASK-1", MODIFIED, start="2026-10-08", reason="Overlap is fine")
		self.assertEqual(len(frappe.saved), 1)
		self.assertIn("Dependencies: Starts before TASK-0 (Dig) ends on 2026-10-09", frappe.comments[0][1])
		self.assertEqual(result["card"]["depends_on"], ["TASK-0"])
		self.assertEqual(result["card"]["blocked_by"], [{"task": "TASK-0", "subject": "Dig", "end": "2026-10-09"}])

	def test_a_block_that_was_already_there_needs_no_reason(self):
		self._doc_after(D(2026, 10, 13))
		result = api.save_task("TASK-1", MODIFIED, expected_time=16)
		self.assertEqual((len(frappe.saved), result["conflicts"]), (1, {}))

	def test_a_pencil_is_never_blocked(self):
		self._doc_after(D(2026, 10, 9), custom_tentative=1)
		api.save_task("TASK-1", MODIFIED, start="2026-10-08")
		self.assertEqual(len(frappe.saved), 1)

	def test_successors_are_transitive_bounded_and_cycle_safe(self):
		self.edges = [("B", "A"), ("C", "B"), ("A", "C"), ("D", "A")]
		self.assertEqual(api.successors("A"), ["B", "D", "C"])
		self.assertEqual(api.successors("A", limit=1), ["B"])
		self.assertEqual(api.successors("A", depth=1), ["B", "D"])
		self.assertEqual(api.successors("Z"), [])

	def test_a_move_later_reports_the_successors_erpnext_did_not_already_push(self):
		_tdoc("TASK-1", "2026-10-12 08:00:00", "2026-10-13 17:00:00")
		self.edges = [("TASK-2", "TASK-1"), ("TASK-3", "TASK-2"), ("TASK-4", "TASK-1"), ("TASK-5", "TASK-1")]
		self._row("TASK-2", WED, THU)
		self._row("TASK-3", D(2026, 10, 20))
		self._row("TASK-4", WED, status="Completed")
		self._row("TASK-5", WED, custom_rental_booking="RB-1")

		def erpnext_reschedules(doc):  # Task.reschedule_dependent_tasks, during the save
			self.rows["TASK-2"]["exp_start_date"] = FRI

		frappe.save_hooks.append(erpnext_reschedules)
		result = api.save_task("TASK-1", MODIFIED, start="2026-10-14")
		self.assertEqual(
			result["successors"], {"count": 1, "names": ["TASK-3"], "days": 2, "auto_moved": ["TASK-2"]}
		)
		# Moving earlier offers nothing.
		_tdoc("TASK-1", "2026-10-12 08:00:00", "2026-10-13 17:00:00")
		self.assertNotIn("successors", api.save_task("TASK-1", MODIFIED, start="2026-10-09"))

	def _chain(self):
		root = _tdoc("TASK-1", "2026-10-12 08:00:00", "2026-10-13 17:00:00")
		_tdoc("TASK-2", "2026-10-12 08:00:00", "2026-10-13 17:00:00", custom_crew=[_Doc(resource="RES-2", hours=0)])
		_tdoc(
			"TASK-3",
			"2026-10-16 00:00:00",
			"2026-10-16 00:00:00",
			custom_start_datetime="2026-10-16 09:00:00",
			custom_end_datetime="2026-10-16 11:00:00",
		)
		_tdoc("TASK-4", MON, status="Completed")
		_tdoc("TASK-5", MON, custom_rental_booking="RB-1")
		self.edges = [("TASK-2", "TASK-1"), ("TASK-3", "TASK-2"), ("TASK-4", "TASK-1"), ("TASK-5", "TASK-1")]
		return root

	def test_shift_successors_is_all_or_nothing(self):
		self._chain()
		self.batch = [{}, {"Lisa": ["2026-10-14: Over by 1h"]}]  # before, after
		result = api.shift_successors("TASK-1", 2, MODIFIED)
		self.assertTrue(result["needs_reason"])
		self.assertEqual(result["conflicts"], {"Lisa": ["2026-10-14: Over by 1h"]})
		self.assertEqual(
			[(m["task"], m["from"], m["to"], m["to_end"]) for m in result["moves"]],
			[("TASK-2", "2026-10-12", "2026-10-14", "2026-10-15"), ("TASK-3", "2026-10-16", "2026-10-20", "2026-10-20")],
		)
		self.assertEqual(frappe.saved, [])
		# The check saw both moves together, against their state before.
		self.assertEqual([[t["name"] for t, _c in batch] for batch in frappe.batches], [["TASK-2", "TASK-3"]] * 2)

	def test_shift_successors_saves_every_one_furthest_first(self):
		self._chain()
		self.batch = [{}, {"Lisa": ["2026-10-14: Over by 1h"]}]
		result = api.shift_successors("TASK-1", "2", MODIFIED, reason="Pump is late")
		self.assertEqual([s["name"] for s in frappe.saved], ["TASK-3", "TASK-2"])
		task3 = frappe.saved[0]
		self.assertEqual(
			(task3["custom_start_datetime"], task3["custom_end_datetime"]),
			("2026-10-20 09:00:00", "2026-10-20 11:00:00"),
		)
		self.assertEqual(
			(frappe.saved[1]["exp_start_date"], frappe.saved[1]["exp_end_date"]),
			("2026-10-14 08:00:00", "2026-10-15 17:00:00"),
		)
		self.assertEqual([m["task"] for m in result["moved"]], ["TASK-3", "TASK-2"])
		self.assertEqual(result["moved"][0]["modified"], "2026-10-08 10:00:01")
		self.assertEqual(sorted(s["task"] for s in result["skipped"]), ["TASK-4", "TASK-5"])
		self.assertEqual(len(frappe.comments), 2)
		self.assertIn("Reason: Pump is late", frappe.comments[0][1])
		for name in ("TASK-1", "TASK-2", "TASK-3"):
			self.assertIn(("check_permission", "write", name), frappe.calls)

	def test_shift_successors_can_be_limited_and_refuses_nonsense(self):
		self._chain()
		result = api.shift_successors("TASK-1", 1, MODIFIED, tasks='["TASK-3"]')
		self.assertEqual([m["task"] for m in result["moved"]], ["TASK-3"])
		self.assertEqual(result["moved"][0]["to"], "2026-10-19")
		with self.assertRaises(_Throw):
			api.shift_successors("TASK-1", 0, MODIFIED)
		with self.assertRaises(_Throw):
			api.shift_successors("TASK-1", 1, "2026-01-01 00:00:00")  # stale
		frappe.denied = {"TASK-2"}
		frappe.saved.clear()
		with self.assertRaises(_PermissionError):
			api.shift_successors("TASK-1", 1, MODIFIED)
		self.assertEqual(frappe.saved, [])  # checked before anything is written


class TestBulkUpdate(_Phase3AWrites):
	def _three(self):
		for name in ("TASK-A", "TASK-B", "TASK-C"):
			_tdoc(name, "2026-10-01 08:00:00", "2026-10-02 17:00:00")

	def test_complete_and_cancel_report_failures_and_carry_on(self):
		self._three()
		frappe.failing, frappe.denied = {"TASK-B"}, {"TASK-C"}
		result = api.bulk_update('["TASK-A", "TASK-B", "TASK-C", "TASK-A"]', "complete")
		self.assertEqual([u["task"] for u in result["updated"]], ["TASK-A"])
		self.assertEqual(frappe.saved[0]["status"], "Completed")
		self.assertEqual(
			result["failed"],
			[
				{"task": "TASK-C", "error": "No permission for Task"},
				{"task": "TASK-B", "error": "Cannot complete task X."},
			],
		)
		self.assertEqual(frappe.local.message_log, [])  # no dialogs on top of the report
		self.assertIn(("rollback", "planner_bulk_1"), frappe.savepoints)
		self.assertEqual(result["warnings"], ["Austin Healey has approved time off."])
		frappe.saved.clear()
		frappe.failing, frappe.denied = set(), set()
		api.bulk_update(["TASK-A"], "cancel")
		self.assertEqual(frappe.saved[0]["status"], "Canceled")  # the site's spelling, one l

	def test_reschedule_keeps_lengths_and_asks_once_for_a_reason(self):
		self._three()
		self.batch = [{"Austin Healey": ["2026-10-19: Over by 4h"]}]
		result = api.bulk_update('["TASK-A", "TASK-B"]', "reschedule", date="2026-10-19")
		self.assertTrue(result["needs_reason"])
		self.assertEqual(
			[(m["task"], m["to"], m["to_end"]) for m in result["moves"]],
			[("TASK-A", "2026-10-19", "2026-10-20"), ("TASK-B", "2026-10-19", "2026-10-20")],
		)
		self.assertEqual(frappe.saved, [])
		self._three()
		self.batch = [{"Austin Healey": ["2026-10-19: Over by 4h"]}]
		result = api.bulk_update('["TASK-A", "TASK-B"]', "reschedule", date="2026-10-19", reason="Catch-up week")
		self.assertEqual(
			[(s["exp_start_date"], s["exp_end_date"]) for s in frappe.saved],
			[("2026-10-19 08:00:00", "2026-10-20 17:00:00")] * 2,
		)
		self.assertEqual([u["start"] for u in result["updated"]], ["2026-10-19", "2026-10-19"])
		self.assertEqual(len(frappe.comments), 2)

	def test_a_rental_task_cannot_be_rescheduled_here_but_the_rest_are(self):
		self._three()
		_tdoc("TASK-R", "2026-10-01 08:00:00", custom_rental_booking="RB-1")
		result = api.bulk_update('["TASK-R", "TASK-A"]', "reschedule", date="2026-10-19")
		self.assertEqual([u["task"] for u in result["updated"]], ["TASK-A"])
		self.assertIn("RB-1", result["failed"][0]["error"])

	def test_bad_requests_are_refused(self):
		with self.assertRaises(_Throw):
			api.bulk_update('["TASK-A"]', "delete")
		with self.assertRaises(_Throw):
			api.bulk_update("[]", "complete")
		with self.assertRaises(_Throw):
			api.bulk_update(json.dumps([f"T-{i}" for i in range(101)]), "complete")
		with self.assertRaises(_Throw):
			api.bulk_update('["TASK-A"]', "reschedule")
		frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			api.bulk_update('["TASK-A"]', "complete")


class TestCopyWeek(_Phase3AWrites):
	def _source(self):
		frappe.tables["Planner Resource"].append(
			{"name": "RES-9", "resource_name": "Retired", "user": None, "is_active": 0}
		)
		_tdoc(
			"TASK-1",
			"2026-10-12 08:00:00",
			"2026-10-14 17:00:00",
			expected_time=12,
			custom_crew_size=2,
			custom_tentative=1,
			description="<p>Pump set</p>",
			parent_task="TASK-P",
			custom_locationaddress_of_task="ADDR-1",
			color="#123456",
			custom_crew=[
				_Doc(resource="RES-1", resource_name="Austin Healey", hours=0, is_lead=1),
				_Doc(resource="RES-9", resource_name="Retired", hours=3, is_lead=0),
			],
			custom_required_credentials=[_Doc(credential_type="Forklift")],
		)
		_tdoc("TASK-OLD", "2026-10-01 08:00:00")
		_tdoc("TASK-R", "2026-10-13 08:00:00", custom_rental_booking="RB-1")
		frappe.get_doc = lambda doctype, name=None: (
			_Task3A(doctype) if isinstance(doctype, dict) else frappe.docs[(doctype, name)]
		)

	def test_a_dry_run_previews_and_writes_nothing(self):
		self._source()
		self.batch = [{"Austin Healey": ["2026-10-19: Over by 2h"]}]
		result = api.copy_week("2026-10-14", "2026-10-21", '["TASK-1", "TASK-OLD", "TASK-R"]')
		self.assertEqual(
			result["copies"],
			[{"task": "TASK-1", "subject": "Subject TASK-1", "to_start": "2026-10-19", "to_end": "2026-10-21", "tentative": True}],
		)
		self.assertEqual(result["conflicts"], {"Austin Healey": ["2026-10-19: Over by 2h"]})
		self.assertEqual([s["task"] for s in result["skipped"]], ["TASK-OLD", "TASK-R"])
		self.assertTrue(result["dry_run"])
		self.assertEqual((result["week_start"], result["target_week_start"]), ("2026-10-11", "2026-10-18"))
		self.assertEqual(frappe.inserted, [])
		((preview, crew),) = frappe.batches[0]
		self.assertEqual((preview["name"], preview["exp_start_date"]), ("TASK-1 (copy)", "2026-10-19 08:00:00"))
		self.assertEqual([m["resource"] for m in crew], ["RES-1", "RES-9"])

	def test_a_real_run_creates_new_tasks_and_needs_a_reason_over_a_conflict(self):
		self._source()
		self.batch = [{"Austin Healey": ["2026-10-19: Over by 2h"]}]
		result = api.copy_week("2026-10-12", "2026-10-19", '["TASK-1"]', dry_run=0)
		self.assertEqual(result["needs_reason"], True)
		self.assertEqual(frappe.inserted, [])
		self.batch = [{"Austin Healey": ["2026-10-19: Over by 2h"]}]
		result = api.copy_week("2026-10-12", "2026-10-19", '["TASK-1"]', dry_run="0", reason="Same again")
		(new,) = frappe.inserted
		self.assertEqual(
			{k: new[k] for k in ("subject", "project", "parent_task", "status", "expected_time", "custom_crew_size", "custom_tentative", "custom_locationaddress_of_task", "color")},
			{
				"subject": "Subject TASK-1",
				"project": "PRJ-1",
				"parent_task": "TASK-P",
				"status": "Open",
				"expected_time": 12.0,
				"custom_crew_size": 2,
				"custom_tentative": 1,
				"custom_locationaddress_of_task": "ADDR-1",
				"color": "#123456",
			},
		)
		self.assertEqual((new["exp_start_date"], new["exp_end_date"]), ("2026-10-19 08:00:00", "2026-10-21 17:00:00"))
		self.assertEqual(new["description"], "<p>Pump set</p><p>Copied from TASK-1 on the Project Planner</p>")
		self.assertEqual(
			[(r["resource"], r["user"], r["hours"], r["is_lead"]) for r in new["custom_crew"]],
			[("RES-1", "austin@example.com", 0.0, 1), ("RES-9", None, 3.0, 0)],  # inactive, but on the original
		)
		self.assertEqual(new["custom_required_credentials"], [{"credential_type": "Forklift"}])
		self.assertEqual(
			result["created"],
			[{"task": "TASK-NEW-1", "source": "TASK-1", "subject": "Subject TASK-1", "start": "2026-10-19", "end": "2026-10-21"}],
		)
		self.assertIn("Reason: Same again", frappe.comments[0][1])
		self.assertEqual(result["week_start"], "2026-10-11")

	def test_on_a_sunday_first_site_a_task_starting_sunday_is_in_that_week(self):
		self._source()
		_tdoc("TASK-SUN", "2026-10-18 08:00:00")
		frappe.singles[("System Settings", "first_day_of_the_week")] = "Sunday"
		result = api.copy_week("2026-10-21", "2026-10-28", '["TASK-SUN", "TASK-1"]')
		self.assertEqual(result["week_start"], "2026-10-18")
		self.assertEqual(
			[(c["task"], c["to_start"]) for c in result["copies"]], [("TASK-SUN", "2026-10-25")]
		)
		self.assertEqual([s["task"] for s in result["skipped"]], ["TASK-1"])  # the week before
		# A Monday-first site puts that Sunday in the week before, so it is skipped instead.
		frappe.singles[("System Settings", "first_day_of_the_week")] = "Monday"
		result = api.copy_week("2026-10-21", "2026-10-28", '["TASK-SUN"]')
		self.assertEqual((result["week_start"], result["copies"]), ("2026-10-19", []))

	def test_copying_needs_another_week_and_create_permission(self):
		self._source()
		with self.assertRaises(_Throw):
			api.copy_week("2026-10-12", "2026-10-16", '["TASK-1"]')
		frappe.has_permission = lambda doctype, ptype=None, *a, **k: ptype != "create"
		self.assertEqual(len(api.copy_week("2026-10-12", "2026-10-19", '["TASK-1"]')["copies"]), 1)  # a dry run is fine
		with self.assertRaises(_PermissionError):
			api.copy_week("2026-10-12", "2026-10-19", '["TASK-1"]', dry_run=0)


# ---------------------------------------------------------------------- Phase 3A: reads


class TestPhase3AReads(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_cards_carry_dependencies_and_qualification_gaps(self):
		austin = _person("RES-1", user="austin@example.com", employee="EMP-1")
		frappe.tables["Task Required Credential"] = [
			{"parent": "T-JOB", "credential_type": "Forklift"},
			{"parent": "T-JOB", "credential_type": "Confined Space Entry"},
		]
		frappe.tables["Task Depends On"] = [{"parent": "T-JOB", "task": "T-PRE"}]
		frappe.tables["Task"] = [dict(_task("T-PRE", start=MON, end=WED, subject="Dig"))]
		frappe.tables["Employee Credential"] = [
			{"employee": "EMP-1", "user": None, "credential_type": "Forklift", "status": "Valid", "expires_on": None}
		]
		with _Readers(
			[austin],
			tasks=[_task("T-JOB", start=TUE, expected_time=4, custom_tentative=1)],
			crew_rows={"T-JOB": [{"resource": "RES-1"}]},
		):
			card = api.get_planner("2026-10-12", "2026-10-18")["tasks"][0]
		self.assertEqual(card["depends_on"], ["T-PRE"])
		self.assertEqual(card["blocked_by"], [{"task": "T-PRE", "subject": "Dig", "end": "2026-10-14"}])
		self.assertEqual(card["qualification_gaps"], ["Confined Space Entry"])
		self.assertEqual(card["qualification_warning"], "No one on the crew holds: Confined Space Entry")
		self.assertTrue(card["tentative"])
		query = next(q for q in frappe.queries if q[0] == "Employee Credential")[1]
		self.assertEqual(query["filters"]["status"], ["in", ["Valid", "Expiring"]])
		self.assertEqual(query["or_filters"]["employee"], ["in", ["EMP-1"]])

	def test_no_hr_module_means_no_gaps_not_all_gaps(self):
		frappe.db.exists = lambda doctype, name=None, *a, **k: name != "Employee Credential"
		self.assertIsNone(api._held_credentials({"RES-1": {"employee": "EMP-1"}}, ["Forklift"]))

	def test_the_heatmap_is_eight_weeks_from_the_monday_with_google_off(self):
		frappe.singles[("System Settings", "first_day_of_the_week")] = "Monday"
		with _Readers(
			[_person()],
			tasks=[
				_task("T-H", start=MON, end=FRI, expected_time=20),
				_task("T-P", start=D(2026, 10, 19), expected_time=8, custom_tentative=1),
			],
			crew_rows={"T-H": [{"resource": "RES-1"}], "T-P": [{"resource": "RES-1"}]},
			points={"T-H": HIGHLANDS},
			shop=SHOP,
		) as readers:
			data = api.get_heatmap("2026-10-14")
			self.assertEqual(len(api.get_heatmap("2026-10-14", weeks=40)["weeks"]), 12)
			self.assertEqual(len(api.get_heatmap("2026-10-14", weeks="")["weeks"]), 8)
			self.assertEqual(len(api.get_heatmap("2026-10-14", weeks=-3)["weeks"]), 1)
		self.assertEqual((data["start"], data["end"], data["week_start"]), ("2026-10-12", "2026-12-06", "2026-10-12"))
		self.assertEqual(len(data["weeks"]), 8)
		self.assertEqual(data["weeks"][0], {"start": "2026-10-12", "end": "2026-10-18", "label": "2026-W42"})
		first, second = data["cells"]["RES-1"]["2026-10-12"], data["cells"]["RES-1"]["2026-10-19"]
		self.assertEqual(first["capacity"], 40.0)
		self.assertGreater(first["booked"], 20.0)  # 20h of work plus the (estimated) driving
		self.assertEqual((second["booked"], second["soft_booked"]), (0.0, 8.0))
		self.assertEqual(set(readers.google), {False})  # every drive priced with Google off
		self.assertEqual(set(readers.shop_google), {False})
		frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			api.get_heatmap("2026-10-14")

	def test_a_sunday_first_site_buckets_from_sunday(self):
		# Production's System Settings: first_day_of_the_week = Sunday (blank reads the same).
		frappe.singles[("System Settings", "first_day_of_the_week")] = "Sunday"
		with _Readers(
			[_person(), _person("RES-2", label="Lisa")],
			patterns={"RES-2": [{"sunday": 8, "monday": 8}]},
			tasks=[_task("T-SUN", start=D(2026, 10, 18), expected_time=6)],
			crew_rows={"T-SUN": [{"resource": "RES-2"}]},
		):
			data = api.get_heatmap("2026-10-21", weeks=2)
		self.assertEqual((data["start"], data["end"], data["week_start"]), ("2026-10-18", "2026-10-31", "2026-10-18"))
		self.assertEqual(
			data["weeks"],
			[
				{"start": "2026-10-18", "end": "2026-10-24", "label": "2026-W43"},
				{"start": "2026-10-25", "end": "2026-10-31", "label": "2026-W44"},
			],
		)
		self.assertEqual(data["cells"]["RES-2"]["2026-10-18"]["booked"], 6.0)  # Sunday's work is this week's
		self.assertEqual(data["cells"]["RES-1"]["2026-10-18"]["capacity"], 40.0)
		frappe.singles.clear()
		self.assertEqual(api.get_heatmap("2026-10-21", weeks=1)["week_start"], "2026-10-18")

	def test_the_heatmap_never_sends_a_google_request_even_with_nothing_cached(self):
		"""The real routing chain, with Google on in Settings, a key, and an empty cache."""
		calls = []
		depot = types.ModuleType("_pp_depot_stub")
		depot._depot_address = lambda: "85 W 300 S, Bountiful, UT 84010"
		settings_module = types.ModuleType("_pp_settings_stub")
		settings_module.materialize_defaults = lambda: None
		controller = "erpnext_enhancements.project_enhancements.doctype.project_planner_settings.project_planner_settings"
		frappe.cached["Project Planner Settings"] = _Doc(
			use_google_routes=1, start_latitude=None, start_longitude=None, start_geocoded_from=None
		)
		frappe.db.set_single_value = lambda *a, **k: None
		with (
			_Readers(
				[_person()],
				tasks=[_task("T-H", start=MON, expected_time=2), _task("T-O", start=MON, expected_time=2)],
				crew_rows={"T-H": [{"resource": "RES-1"}], "T-O": [{"resource": "RES-1"}]},
				points={"T-H": HIGHLANDS, "T-O": OGDEN},
				real_routing=True,
			),
			mock.patch.dict(sys.modules, {"erpnext_enhancements.api.pickup_routing": depot, controller: settings_module}),
			mock.patch.object(routing, "_google_post", lambda body: (calls.append("routes"), (None, None, "refused"))[1]),
			mock.patch.object(routing, "_google_geocode", lambda address: (calls.append("geocode"), (SHOP, None))[1]),
			mock.patch.object(routing, "_has_key", lambda: True),
			mock.patch.object(routing, "_flag", lambda name: False),
			mock.patch.object(routing, "_set_flag", lambda *a, **k: None),
			mock.patch.object(routing, "_read_cache", lambda pairs: ({}, {})),
		):
			data = api.get_heatmap("2026-10-12", weeks=1)
			self.assertEqual(calls, [])
			monday = engine.availability(MON, MON, google=False)["days"]["RES-1"]["2026-10-12"]
			self.assertEqual(calls, [])
			self.assertGreater(monday["drive_minutes"], 0)  # Highlands and Ogden, estimated between
			self.assertEqual(monday["drive_source"], "estimate")
			# The control: the same day with Google allowed does ask (geocode the shop, price legs).
			engine.availability(MON, MON)
		self.assertEqual(set(calls), {"geocode", "routes"})
		self.assertGreater(data["cells"]["RES-1"][data["week_start"]]["booked"], 4.0)

	def test_overdue_is_grouped_by_project_without_rentals_or_future_work(self):
		frappe.sql_rows["< %(today)s"] = [
			_task("T-1", start=D(2026, 9, 28), end=D(2026, 10, 1), project_title="Vegas"),
			_task("T-2", start=D(2026, 10, 5), project_title="Vegas", custom_tentative=1),
			_task("T-FUTURE", start=D(2026, 10, 20), project_title="Vegas"),
			_task("T-RENTAL", start=D(2026, 10, 1), custom_rental_booking="RB-1", project_title="Vegas"),
			_task("T-3", start=D(2026, 10, 7), project="PRJ-2", project_title="Highlands"),
		]
		frappe.tables["Task Crew Member"] = [{"parent": "T-1", "resource": "RES-2", "resource_name": "Lisa", "hours": 0}]
		frappe.tables["ToDo"] = [{"reference_name": "T-2", "allocated_to": "austin@example.com"}]
		frappe.tables["Planner Resource"] = [
			{"name": "RES-1", "resource_name": "Austin Healey", "user": "austin@example.com"},
			{"name": "RES-2", "resource_name": "Lisa", "user": "lisa@example.com"},
		]
		data = api.get_overdue()
		self.assertEqual((data["today"], data["total"]), ("2026-10-08", 3))
		self.assertEqual(
			[(g["project"], g["title"], g["count"]) for g in data["projects"]],
			[("PRJ-1", "Vegas", 2), ("PRJ-2", "Highlands", 1)],
		)
		first = data["projects"][0]["tasks"][0]
		self.assertEqual(
			first,
			{
				"name": "T-1",
				"subject": "Subject T-1",
				"status": "Open",
				"start": "2026-09-28",
				"end": "2026-10-01",
				"crew": ["Lisa"],
				"tentative": False,
				"modified": "2026-10-08 09:00:00",
			},
		)
		self.assertEqual(data["projects"][0]["tasks"][1]["crew"], ["Austin Healey"])  # from the assignment
		sql = next(q[1] for q in frappe.queries if q[0] == "sql" and "< %(today)s" in q[1])
		self.assertNotIn("t.project = %(project)s", sql)
		api.get_overdue(project="PRJ-2", limit=9999)
		sql = [q[1] for q in frappe.queries if q[0] == "sql" and "< %(today)s" in q[1]][-1]
		self.assertIn("AND t.project = %(project)s", sql)


# ---------------------------------------------------------------------- wiring


def _whitelisted(path):
	"""``{function name: methods}`` for every ``@frappe.whitelist`` function in a module."""
	out = {}
	for node in ast.parse(path.read_text(encoding="utf-8")).body:
		if not isinstance(node, ast.FunctionDef):
			continue
		for decorator in node.decorator_list:
			target = decorator.func if isinstance(decorator, ast.Call) else decorator
			if isinstance(target, ast.Attribute) and target.attr == "whitelist":
				methods = None
				if isinstance(decorator, ast.Call):
					for keyword in decorator.keywords:
						if keyword.arg == "methods":
							methods = ast.literal_eval(keyword.value)
				out[node.name] = methods
	return out


class TestWiring(unittest.TestCase):
	def test_the_role_gate(self):
		self.assertEqual(
			api.PLANNER_ROLES,
			{
				"System Manager",
				"Projects Manager",
				"Projects User",
				"Maintenance Supervisor",
				"Maintenance User",
			},
		)
		self.assertEqual(api.MAX_RANGE_DAYS, 100)

	@unittest.skipUnless(PAGE_JSON.exists(), "the page is built separately")
	def test_the_page_opens_for_exactly_the_planner_roles(self):
		page = json.loads(PAGE_JSON.read_text(encoding="utf-8"))
		self.assertEqual({row["role"] for row in page.get("roles", [])}, api.PLANNER_ROLES)

	def test_every_write_is_post_only_and_the_read_is_not(self):
		endpoints = _whitelisted(API_PATH)
		writes = ("save_task", "add_crew", "swap_crew", "shift_successors", "bulk_update", "copy_week")
		reads = ("get_planner", "get_route", "suggest_dates", "check_routes", "get_heatmap", "get_overdue")
		self.assertEqual(set(endpoints), set(writes) | set(reads))
		for name in writes:
			self.assertEqual(endpoints[name], ["POST"], name)
		# The reads (and the read-only route check) take GET, as the UI and the AI tools call them.
		for name in reads:
			self.assertIsNone(endpoints[name], name)
		self.assertEqual(_whitelisted(ENGINE_PATH), {})  # the engine is not an endpoint
		self.assertEqual(_whitelisted(ROUTING_PATH), {})  # nor is the routing module

	def test_log_error_is_called_with_keywords(self):
		# v16's log_error guesses which positional argument is the title; keywords leave nothing to guess.
		for path in (ENGINE_PATH, API_PATH, ROUTING_PATH):
			for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
				if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "log_error":
					self.assertEqual(node.args, [], path.name)

	def test_no_sql_function_strings_in_get_all_fields(self):
		# Frappe 16 raises "SQL functions are not allowed as strings in SELECT".
		for path in (ENGINE_PATH, API_PATH, ROUTING_PATH):
			for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
				if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "get_all":
					for keyword in node.keywords:
						if keyword.arg == "fields":
							for value in ast.walk(keyword.value):
								if isinstance(value, ast.Constant) and isinstance(value.value, str):
									self.assertNotIn("(", value.value, path.name)

	def test_the_api_readme_lists_the_module(self):
		readme = (APP / "api/README.md").read_text(encoding="utf-8")
		self.assertIn("| `project_planner.py` |", readme)
		tabbed = readme[readme.index("**Tab-indented**") : readme.index("Every other file is 4 spaces")]
		self.assertIn("`project_planner`", tabbed)
		# The README says the file is tab-indented; keep it true.
		self.assertIn("\n\tif frappe.session.user", API_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
	unittest.main()
