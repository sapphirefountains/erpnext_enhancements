# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Done work moved onto old dates on the planners (v1.795.0, ``api/planner_history.py``).

Nik, 2026-11-04: "In the Maintenance Planner and Project Planner and such I need to be able to move
previously done tasks and such to old dates for historical reasons." He picked: the Project Planner's
Gantt, its Calendar and Crew grids (behind Display ▸ Show done work) and the Maintenance Planner's visits;
all of a task's dates moving together; managers only; an automatic note on the record.

What this pins, and what would fail quietly otherwise:

* **The dates move together and stay valid for ERPNext** (erpnext 16.52.1's ``Task.validate``): the actual
  start with the first day, the actual end and Completed On with the last; Completed On never after today
  (``validate_completed_on``) and the actual start never after the actual end (``validate_from_to_dates``);
  a task that had no dates is measured from its own done dates; a done task with no Completed On is given
  the new last day. A visit's day, scheduled day and clock times move by the same days.
* **History, not schedule.** The endpoints are managers only, refuse a day after today, never draft, ask no
  reason, tell nobody and switch off ERPNext's push of dependent tasks (``flags.ignore_reschedule``), and
  every move leaves a note. Run here against stubbed planner modules.
* **Undo is exact.** Each answer's ``before`` restores every date, a blank one included, so a done task
  dated from the Gantt's Not scheduled list goes back to having none.
* **The wiring.** ``get_planner(done=1)`` adds done cards after the drafts, notes and display rule; the
  Gantt marks done tasks a manager may move and shows every task of a finished job; both pages pick up,
  drop and undo done work through their own blocks, which sit above the tour (the tour stays last); the
  page's pure rules run under node.

Bench-free: ``planner_history`` against a small ``frappe`` stub; files read; node for the page rules.

Run: python -m unittest erpnext_enhancements.tests.test_planner_history
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

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
HISTORY_API = APP / "api" / "planner_history.py"
PP_API = APP / "api" / "project_planner.py"
MP_API = APP / "api" / "maintenance_planner.py"
GANTT_API = APP / "api" / "planner_gantt.py"
PP_JS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.js"
PP_CSS = APP / "project_enhancements" / "page" / "project_planner" / "project_planner.css"
MP_JS = APP / "sapphire_maintenance" / "page" / "maintenance_planner" / "maintenance_planner.js"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

D = datetime.date
DT = datetime.datetime
TODAY = D(2026, 11, 4)

_saved = {}
history = None


class _PermissionError(Exception):
	pass


class _Thrown(Exception):
	pass


def setUpModule():
	global history
	for name in ("frappe", "erpnext_enhancements.api.planner_history"):
		_saved[name] = sys.modules.pop(name, None)
	frappe = types.ModuleType("frappe")
	frappe.PermissionError = _PermissionError
	frappe._ = lambda text: text
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.session = types.SimpleNamespace(user="manager@example.com")
	frappe.roles = ["Projects Manager"]
	frappe.get_roles = lambda user=None: list(frappe.roles)
	frappe.writes = []
	frappe.errors = []

	def throw(message, exc=_Thrown, title=None):
		raise exc(message)

	frappe.throw = throw
	frappe.utils = types.SimpleNamespace(
		nowdate=lambda: TODAY.isoformat(), get_fullname=lambda user: "Pat Manager"
	)
	frappe.db = types.SimpleNamespace(set_value=lambda *a, **k: frappe.writes.append(("set_value", a, k)))
	frappe.get_all = lambda *a, **k: []
	frappe.log_error = lambda *a, **k: frappe.errors.append(k)
	frappe.get_traceback = lambda: "traceback"
	sys.modules["frappe"] = frappe
	history = importlib.import_module("erpnext_enhancements.api.planner_history")


def tearDownModule():
	sys.modules.pop("erpnext_enhancements.api.planner_history", None)
	for name, module in _saved.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def _frappe():
	return sys.modules["frappe"]


def _text(path):
	return path.read_text(encoding="utf-8")


def _method(js, signature):
	"""One method's text: a class method ends at a tab and a brace, an object's at a tab, brace, comma."""
	start = js.index(signature)
	end = re.compile(r"\n\t\},?\n").search(js, start).start()
	return js[start:end]


def _pyfunc(source, name):
	start = source.index(f"def {name}(")
	end = re.compile(r"\n(?:def |@|class |# -{10})").search(source, start + 1)
	return source[start : end.start() if end else len(source)]


# ---------------------------------------------------------------------- pure rules


class TestWhoMay(unittest.TestCase):
	def test_managers_only(self):
		self.assertTrue(history.may_backdate("a@example.com", ["Projects Manager"]))
		self.assertTrue(history.may_backdate("a@example.com", ["Maintenance Supervisor", "Employee"]))
		self.assertTrue(history.may_backdate("a@example.com", ["System Manager"]))
		self.assertTrue(history.may_backdate("Administrator", []))
		for roles in ([], ["Projects User"], ["Maintenance User"], ["Employee", "Technician"], None):
			self.assertFalse(history.may_backdate("a@example.com", roles), roles)

	def test_only_completed_tasks_are_done_work(self):
		self.assertTrue(history.is_done_task({"status": "Completed"}))
		for status in (
			"Open",
			"Working",
			"Overdue",
			"Pending Review",
			"Canceled",
			"Cancelled",
			"Template",
			None,
		):
			self.assertFalse(history.is_done_task({"status": status}), status)


class TestDoneTaskDates(unittest.TestCase):
	def test_a_move_takes_the_actual_dates_and_completed_on_along(self):
		values = {
			"act_start_date": D(2026, 3, 3),
			"act_end_date": D(2026, 3, 5),
			"completed_on": D(2026, 3, 6),
		}
		out = history.done_task_dates(
			(D(2026, 3, 2), D(2026, 3, 4)), (D(2025, 3, 2), D(2025, 3, 4)), values, TODAY
		)
		self.assertEqual(
			out,
			{"act_start_date": D(2025, 3, 3), "act_end_date": D(2025, 3, 5), "completed_on": D(2025, 3, 6)},
		)

	def test_a_stretch_moves_the_end_dates_only(self):
		values = {
			"act_start_date": D(2026, 3, 2),
			"act_end_date": D(2026, 3, 4),
			"completed_on": D(2026, 3, 4),
		}
		out = history.done_task_dates(
			(D(2026, 3, 2), D(2026, 3, 4)), (D(2026, 3, 2), D(2026, 3, 9)), values, TODAY
		)
		self.assertEqual(out, {"act_end_date": D(2026, 3, 9), "completed_on": D(2026, 3, 9)})

	def test_completed_on_is_never_after_today(self):
		# Planned the 1st-3rd, finished late on the 20th; moved to end on the 2nd of November.
		values = {"completed_on": D(2026, 10, 20)}
		out = history.done_task_dates(
			(D(2026, 10, 1), D(2026, 10, 3)), (D(2026, 10, 31), D(2026, 11, 2)), values, TODAY
		)
		self.assertEqual(out["completed_on"], TODAY)

	def test_the_actual_start_is_never_after_the_actual_end(self):
		values = {"act_start_date": D(2026, 3, 5), "act_end_date": D(2026, 3, 6)}
		out = history.done_task_dates(
			(D(2026, 3, 1), D(2026, 3, 10)), (D(2026, 3, 7), D(2026, 3, 10)), values, TODAY
		)
		self.assertLessEqual(out["act_start_date"], out.get("act_end_date", values["act_end_date"]))

	def test_a_task_that_had_no_dates_is_measured_from_its_done_dates(self):
		values = {
			"act_start_date": D(2026, 9, 1),
			"act_end_date": D(2026, 9, 3),
			"completed_on": D(2026, 9, 4),
		}
		out = history.done_task_dates(None, (D(2024, 5, 1), D(2024, 5, 2)), values, TODAY)
		# Completed On lands on the new last day; the actual dates keep their distance from it.
		self.assertEqual(out["completed_on"], D(2024, 5, 2))
		self.assertEqual(out["act_end_date"], D(2024, 5, 1))
		self.assertEqual(out["act_start_date"], D(2024, 5, 1))

	def test_a_done_task_with_no_completed_on_is_given_the_last_day(self):
		out = history.done_task_dates(None, (D(2024, 5, 1), D(2024, 5, 3)), {}, TODAY)
		self.assertEqual(out, {"completed_on": D(2024, 5, 3)})
		out = history.done_task_dates(
			(D(2026, 1, 1), D(2026, 1, 1)), (D(2025, 1, 1), D(2025, 1, 1)), {}, TODAY
		)
		self.assertEqual(out, {"completed_on": D(2025, 1, 1)})

	def test_blank_actual_dates_stay_blank_and_unchanged_ones_are_left_out(self):
		values = {"completed_on": D(2026, 3, 4)}
		out = history.done_task_dates(
			(D(2026, 3, 2), D(2026, 3, 4)), (D(2026, 3, 1), D(2026, 3, 4)), values, TODAY
		)
		self.assertEqual(out, {})
		self.assertNotIn("act_start_date", out)

	def test_text_and_datetimes_read_as_dates(self):
		values = {"act_start_date": "2026-03-02", "completed_on": "2026-03-04 00:00:00"}
		out = history.done_task_dates(
			("2026-03-02", "2026-03-04"), (D(2026, 2, 2), D(2026, 2, 4)), values, TODAY
		)
		self.assertEqual(out, {"act_start_date": D(2026, 2, 2), "completed_on": D(2026, 2, 4)})


class TestVisitDates(unittest.TestCase):
	def test_a_finished_visit_takes_its_scheduled_day_and_clock_times_along(self):
		values = {
			"visit_date": D(2026, 10, 27),
			"scheduled_visit_date": D(2026, 10, 27),
			"clock_in_time": DT(2026, 10, 27, 7, 30),
			"clock_out_time": DT(2026, 10, 27, 9, 45),
		}
		out = history.visit_dates(values, D(2026, 10, 20))
		self.assertEqual(
			out,
			{
				"visit_date": D(2026, 10, 20),
				"scheduled_visit_date": D(2026, 10, 20),
				"clock_in_time": DT(2026, 10, 20, 7, 30),
				"clock_out_time": DT(2026, 10, 20, 9, 45),
			},
		)

	def test_an_unstarted_draft_is_dated_the_day_it_was_done(self):
		out = history.visit_dates({"scheduled_visit_date": D(2026, 11, 6)}, D(2026, 11, 1))
		self.assertEqual(out, {"visit_date": D(2026, 11, 1), "scheduled_visit_date": D(2026, 11, 1)})
		out = history.visit_dates({}, D(2026, 11, 1))
		self.assertEqual(out, {"visit_date": D(2026, 11, 1), "scheduled_visit_date": D(2026, 11, 1)})

	def test_the_same_day_changes_nothing(self):
		values = {
			"visit_date": D(2026, 10, 27),
			"scheduled_visit_date": D(2026, 10, 27),
			"clock_in_time": "2026-10-27 07:30:00",
		}
		self.assertEqual(history.visit_dates(values, D(2026, 10, 27)), {})


class TestNoteAndValues(unittest.TestCase):
	def test_changed_fields_and_the_note(self):
		before = {"exp_start_date": DT(2026, 3, 2), "completed_on": D(2026, 3, 4), "act_end_date": None}
		after = {"exp_start_date": "2025-03-02 00:00:00", "completed_on": D(2025, 3, 4), "act_end_date": None}
		changes = history.changed_fields(before, after, history.TASK_FIELDS)
		self.assertEqual(
			changes,
			[
				("exp_start_date", "2026-03-02 00:00", "2025-03-02 00:00"),
				("completed_on", "2026-03-04", "2025-03-04"),
			],
		)
		note = history.history_note("Project Planner", "Pat <b>", changes, {"completed_on": "Completed On"})
		self.assertIn(
			"Pat &lt;b&gt; moved this done work on the Project Planner, as a historical record.", note
		)
		self.assertIn("<li>Completed On: 2026-03-04 → 2025-03-04</li>", note)
		self.assertIn("<li>exp_start_date: 2026-03-02 00:00 → 2025-03-02 00:00</li>", note)
		undone = history.history_note(
			"Maintenance Planner", "Pat", [("visit_date", "", "2026-10-01")], undone=True
		)
		self.assertIn("Pat undid a move of this done work on the Maintenance Planner.", undone)
		self.assertIn("visit_date: none → 2026-10-01", undone)

	def test_before_round_trips_and_junk_is_refused(self):
		values = {"exp_start_date": DT(2026, 3, 2, 8, 0), "completed_on": D(2026, 3, 4), "act_end_date": None}
		sent = json.dumps(history.serialized(values))
		back = history.parse_values(sent, history.TASK_FIELDS)
		self.assertEqual(
			back,
			{"exp_start_date": "2026-03-02 08:00:00", "completed_on": "2026-03-04", "act_end_date": None},
		)
		# Only the allowed fields come back; anything that is not a date is refused.
		self.assertEqual(
			history.parse_values({"status": "Open", "completed_on": ""}, history.TASK_FIELDS),
			{"completed_on": None},
		)
		for junk in ('{"completed_on": "soon"}', "[]", '{"completed_on": "2026-13-01"}'):
			with self.assertRaises(ValueError):
				history.parse_values(junk, history.TASK_FIELDS)


class TestDoneBookings(unittest.TestCase):
	def test_done_cards_sit_on_their_crews_days_without_hours(self):
		cell = {
			"capacity": 8,
			"booked": 3,
			"free": 5,
			"bookings": [{"kind": "task", "ref": "T-OPEN", "hours": 3}],
		}
		days = {"R1": {"2026-10-05": dict(cell), "2026-10-06": {"bookings": []}}, "R2": {"2026-10-05": {}}}
		cards = [
			{
				"name": "T-DONE",
				"subject": "Pour",
				"start": "2026-10-04",
				"end": "2026-10-06",
				"crew": [{"resource": "R1"}, {"resource": "R9"}],
			},
			{"name": "T-UNDATED", "start": None, "crew": [{"resource": "R2"}]},
		]
		added = history.done_bookings(days, cards, D(2026, 10, 5), D(2026, 10, 11))
		self.assertEqual(added, 2)
		first = days["R1"]["2026-10-05"]
		self.assertEqual(
			first["bookings"][-1],
			{"kind": "task", "ref": "T-DONE", "label": "Pour", "hours": 0, "done": True},
		)
		# The engine's numbers are untouched; the person not on the planner and the undated card add nothing.
		self.assertEqual((first["capacity"], first["booked"], first["free"]), (8, 3, 5))
		self.assertEqual(days["R2"]["2026-10-05"], {})


# ---------------------------------------------------------------------- endpoints, against stubs


class _Doc:
	def __init__(self, name="TASK-1", status="Completed", **values):
		self.doctype = "Task"
		self.name = name
		self.status = status
		self.subject = "Pour the basin"
		self.modified = "2026-11-01 10:00:00"
		self.docstatus = values.pop("docstatus", 0)
		self.values = dict(values)
		self.flags = types.SimpleNamespace()
		self.comments = []
		self.saved = 0
		self.meta = types.SimpleNamespace(get_label=lambda field: field.replace("_", " ").title())

	def get(self, field, default=None):
		if field in ("name", "status", "subject", "modified", "docstatus", "doctype"):
			return getattr(self, field)
		return self.values.get(field, default)

	def set(self, field, value):
		self.values[field] = value

	def add_comment(self, kind, text):
		self.comments.append((kind, text))

	def check_permission(self, ptype):
		self.checked = ptype

	def reload(self):
		self.modified = "2026-11-04 12:00:00"


def _as_date(value):
	if isinstance(value, DT):
		return value.date()
	if isinstance(value, D):
		return value
	return D.fromisoformat(str(value)[:10]) if value else None


def _install(module_name, attrs):
	"""Put a fake module in place of ``module_name``: in sys.modules and as its package's attribute (which
	``from package import module`` reads first). A parent package not imported yet gets a bare stand-in,
	so no package ``__init__`` runs (``project_enhancements/__init__.py`` imports real modules)."""
	created = []
	parts = module_name.split(".")
	for depth in range(1, len(parts)):
		name = ".".join(parts[:depth])
		if name not in sys.modules:
			stand_in = types.ModuleType(name)
			stand_in.__path__ = []
			sys.modules[name] = stand_in
			created.append(name)
			if depth > 1:
				setattr(sys.modules[".".join(parts[: depth - 1])], parts[depth - 1], stand_in)
	module = types.ModuleType(module_name)
	for key, value in attrs.items():
		setattr(module, key, value)
	package_name, _, leaf = module_name.rpartition(".")
	package = sys.modules[package_name]
	old_module = sys.modules.get(module_name)
	old_attr = getattr(package, leaf, None)
	sys.modules[module_name] = module
	setattr(package, leaf, module)

	def restore():
		if old_module is None:
			sys.modules.pop(module_name, None)
		else:
			sys.modules[module_name] = old_module
		if old_attr is None:
			if hasattr(package, leaf):
				delattr(package, leaf)
		else:
			setattr(package, leaf, old_attr)
		for name in reversed(created):
			sys.modules.pop(name, None)

	return module, restore


def _code(text):
	"""``text`` without its comment lines (``#`` or ``//``) and docstrings' prose: what runs."""
	lines = []
	for line in text.splitlines():
		stripped = line.strip()
		if stripped.startswith("#") or stripped.startswith("//"):
			continue
		lines.append(line)
	return "\n".join(lines)


class TestMoveDoneTask(unittest.TestCase):
	def setUp(self):
		_frappe().roles = ["Projects Manager"]
		self.doc = _Doc(
			exp_start_date=DT(2026, 3, 2),
			exp_end_date=DT(2026, 3, 4),
			act_start_date=D(2026, 3, 2),
			act_end_date=D(2026, 3, 4),
			completed_on=D(2026, 3, 5),
		)
		doc = self.doc
		self.calls = []

		def span(state):
			first, last = _as_date(state.get("exp_start_date")), _as_date(state.get("exp_end_date"))
			return (first, last) if first else None

		def move(target, start, end):
			old = span(target.values)
			new_start = _as_date(start)
			if old and new_start and not end:
				shift = (new_start - old[0]).days
				target.values["exp_start_date"] = DT.combine(
					old[0] + datetime.timedelta(days=shift), datetime.time()
				)
				target.values["exp_end_date"] = DT.combine(
					old[1] + datetime.timedelta(days=shift), datetime.time()
				)
			elif new_start:
				target.values["exp_start_date"] = DT.combine(new_start, datetime.time())
				target.values["exp_end_date"] = DT.combine(_as_date(end) or new_start, datetime.time())

		def save(target):
			self.calls.append(("save", dict(target.values), getattr(target.flags, "ignore_reschedule", None)))
			target.modified = "2026-11-04 12:00:00"
			return []

		self.pp, self.restore_pp = _install(
			"erpnext_enhancements.api.project_planner",
			{
				"_load": lambda task, modified: doc,
				"_state": lambda d: dict(d.values),
				"_move": move,
				"_save": save,
				"given": lambda value: value if value not in (None, "", "null", "undefined") else None,
				"_subtask_refusal": lambda d: None,
			},
		)
		self.engine, self.restore_engine = _install(
			"erpnext_enhancements.project_enhancements.crew_availability", {"task_span": span}
		)

	def tearDown(self):
		self.restore_pp()
		self.restore_engine()

	def test_a_manager_moves_a_done_task_and_every_date_follows(self):
		answer = history.move_done_task("TASK-1", "2026-11-01 10:00:00", start="2025-03-02")
		self.assertTrue(answer["historical"])
		self.assertEqual(answer["modified"], "2026-11-04 12:00:00")
		saved = self.calls[-1]
		self.assertEqual(saved[0], "save")
		self.assertTrue(saved[2], "ERPNext's push of the tasks that depend on it is switched off")
		self.assertEqual(self.doc.values["act_start_date"], D(2025, 3, 2))
		self.assertEqual(self.doc.values["act_end_date"], D(2025, 3, 4))
		self.assertEqual(self.doc.values["completed_on"], D(2025, 3, 5))
		self.assertEqual(len(self.doc.comments), 1)
		kind, note = self.doc.comments[0]
		self.assertEqual(kind, "Comment")
		self.assertIn("Pat Manager moved this done work on the Project Planner", note)
		self.assertIn("Completed On: 2026-03-05 → 2025-03-05", note)
		# What an Undo sends back.
		self.assertEqual(answer["before"]["completed_on"], "2026-03-05")
		self.assertEqual(answer["before"]["exp_start_date"], "2026-03-02 00:00:00")

	def test_not_a_manager(self):
		_frappe().roles = ["Projects User"]
		with self.assertRaises(_PermissionError):
			history.move_done_task("TASK-1", "x", start="2025-03-02")
		self.assertEqual(self.calls, [])

	def test_not_done(self):
		self.doc.status = "Working"
		with self.assertRaises(_Thrown):
			history.move_done_task("TASK-1", "x", start="2025-03-02")
		self.assertEqual(self.calls, [])

	def test_never_after_today(self):
		with self.assertRaises(_Thrown) as raised:
			history.move_done_task("TASK-1", "x", start="2026-11-03")
		self.assertIn("today or an earlier day", str(raised.exception))
		self.assertEqual(self.calls, [])

	def test_the_same_days_write_nothing(self):
		answer = history.move_done_task("TASK-1", "x", start="2026-03-02")
		self.assertTrue(answer["unchanged"])
		self.assertEqual(self.calls, [])
		self.assertEqual(self.doc.comments, [])

	def test_undo_puts_back_exactly_what_was_there_blanks_included(self):
		undated = _Doc(completed_on=D(2026, 9, 4))
		self.pp._load = lambda task, modified: undated
		moved = history.move_done_task("TASK-1", "x", start="2024-05-01", end="2024-05-02")
		self.assertEqual(undated.values["completed_on"], D(2024, 5, 2))
		history.restore_done_task("TASK-1", "y", json.dumps(moved["before"]))
		self.assertIsNone(undated.values["exp_start_date"])
		self.assertIsNone(undated.values["exp_end_date"])
		self.assertEqual(undated.values["completed_on"], "2026-09-04")
		self.assertIn("undid a move of this done work", undated.comments[-1][1])


class TestMoveDoneVisit(unittest.TestCase):
	def setUp(self):
		_frappe().roles = ["Maintenance Supervisor"]
		_frappe().writes = []
		self.doc = _Doc(
			name="SMR-1",
			docstatus=1,
			visit_date=D(2026, 10, 27),
			scheduled_visit_date=D(2026, 10, 27),
			clock_in_time=DT(2026, 10, 27, 7, 30),
			clock_out_time=DT(2026, 10, 27, 9, 0),
			workflow_state="Final/Submitted",
		)
		self.doc.doctype = "Sapphire Maintenance Record"
		self.saves = []
		frappe = _frappe()
		frappe.get_doc = lambda doctype, name: self.doc

		def set_value(doctype, name, values, *args, **kwargs):
			# The row is written, and the reload that follows reads it back.
			frappe.writes.append(("set_value", (doctype, name, dict(values)), kwargs))
			self.doc.values.update(values)

		self.old_db = frappe.db
		frappe.db = types.SimpleNamespace(set_value=set_value)

		def save(doc):
			self.saves.append(dict(doc.values))
			return []

		self.mp, self.restore_mp = _install(
			"erpnext_enhancements.api.maintenance_planner",
			{
				"_require_planner": lambda: None,
				"_check_fresh": lambda doc, modified: None,
				"_save_with_warnings": save,
				"PENDING_STATE": "Pending Review",
			},
		)
		self.synced = []
		self.triggers, self.restore_triggers = _install(
			"erpnext_enhancements.project_enhancements.calendar_sync.triggers",
			{"on_change": lambda doc, method=None: self.synced.append((doc.name, method))},
		)

	def tearDown(self):
		_frappe().db = self.old_db
		self.restore_triggers()
		self.restore_mp()

	def test_a_submitted_visit_is_written_to_its_row_with_its_clock_times(self):
		answer = history.move_done_visit("SMR-1", "m", "2026-10-20")
		self.assertTrue(answer["historical"])
		self.assertEqual(self.saves, [], "a submitted visit is not saved (its dates are not allow_on_submit)")
		(kind, args, _kwargs) = _frappe().writes[-1]
		self.assertEqual(kind, "set_value")
		self.assertEqual(args[0], "Sapphire Maintenance Record")
		self.assertEqual(args[2]["clock_in_time"], DT(2026, 10, 20, 7, 30))
		self.assertEqual(args[2]["visit_date"], D(2026, 10, 20))
		self.assertEqual(self.doc.checked, "read")
		self.assertIn("on the Maintenance Planner", self.doc.comments[-1][1])
		# Its calendar event follows, as an update after submit would re-sync it.
		self.assertEqual(self.synced, [("SMR-1", "on_update_after_submit")])
		self.assertEqual(_frappe().errors, [])

	def test_an_open_draft_is_saved_under_the_callers_permissions(self):
		draft = _Doc(name="SMR-2", docstatus=0, scheduled_visit_date=D(2026, 11, 6), workflow_state="Draft")
		_frappe().get_doc = lambda doctype, name: draft
		history.move_done_visit("SMR-2", "m", "2026-11-01")
		self.assertEqual(self.saves[-1]["visit_date"], D(2026, 11, 1))
		self.assertEqual(draft.checked, "write")
		self.assertEqual(_frappe().writes, [])

	def test_in_review_is_written_to_its_row_too(self):
		self.doc.docstatus = 0
		self.doc.values["workflow_state"] = "Pending Review"
		history.move_done_visit("SMR-1", "m", "2026-10-21")
		self.assertEqual(self.saves, [])
		self.assertEqual(_frappe().writes[-1][1][2]["visit_date"], D(2026, 10, 21))

	def test_refusals(self):
		with self.assertRaises(_Thrown):
			history.move_done_visit("SMR-1", "m", "2026-11-05")  # after today
		self.doc.docstatus = 2
		with self.assertRaises(_Thrown):
			history.move_done_visit("SMR-1", "m", "2026-10-20")  # canceled
		_frappe().roles = ["Maintenance User"]
		with self.assertRaises(_PermissionError):
			history.move_done_visit("SMR-1", "m", "2026-10-20")
		self.assertEqual(_frappe().writes, [])

	def test_undo_restores_the_visit(self):
		moved = history.move_done_visit("SMR-1", "m", "2026-10-20")
		history.restore_done_visit("SMR-1", "m2", json.dumps(moved["before"]))
		restored = _frappe().writes[-1][1][2]
		self.assertEqual(restored["visit_date"], "2026-10-27")
		self.assertEqual(restored["clock_in_time"], "2026-10-27 07:30:00")


# ---------------------------------------------------------------------- wiring


def _whitelisted(path):
	out = {}
	for node in ast.parse(_text(path)).body:
		if not isinstance(node, ast.FunctionDef):
			continue
		for deco in node.decorator_list:
			call = deco if isinstance(deco, ast.Call) else None
			func = call.func if call else deco
			if getattr(func, "attr", "") == "whitelist":
				methods = None
				for kw in call.keywords if call else []:
					if kw.arg == "methods":
						methods = ast.literal_eval(kw.value)
				out[node.name] = methods
	return out


class TestServerWiring(unittest.TestCase):
	def test_the_writes_are_post(self):
		endpoints = _whitelisted(HISTORY_API)
		self.assertEqual(
			endpoints,
			{
				"move_done_task": ["POST"],
				"restore_done_task": ["POST"],
				"move_done_visit": ["POST"],
				"restore_done_visit": ["POST"],
			},
		)

	def test_history_skips_the_schedule_rules(self):
		source = _text(HISTORY_API)
		move = _pyfunc(source, "move_done_task")
		self.assertIn("_require_backdate()", move)
		self.assertIn("pp._load(task, modified)", move)
		self.assertIn("_require_done_task(doc)", move)
		save = _pyfunc(source, "_save_task")
		self.assertIn("doc.flags.ignore_reschedule = True", save)
		self.assertIn("pp._save(doc)", save)  # the project window is widened first, as every planner save
		code = _code(
			source[
				source.index(
					"# ---------------------------------------------------------------------- gates"
				) :
			]
		)
		for word in (
			"needs_reason",
			"notify",
			"_store_draft",
			"_follower_docs",
			"preview_conflicts",
			"pp.save_task",
			"pp._apply",
			"mp.move_visit",
			"mp._apply_visit",
		):
			self.assertNotIn(word, code, word)

	def test_get_planner_adds_done_work_last_and_only_when_asked(self):
		body = _pyfunc(_text(PP_API), "get_planner")
		self.assertLess(
			body.index("_with_drafts(result, overlay, start, end)"),
			body.index("planner_history.add_done_work("),
		)
		self.assertIn("if cint(given(done)):", body)
		self.assertIn("from erpnext_enhancements.api import planner_history, planner_open", _text(PP_API))

	def test_the_gantt_unlocks_done_tasks_for_managers(self):
		gantt = _text(GANTT_API)
		self.assertIn("backdate = planner_history.can_backdate()", gantt)
		self.assertIn('card["backdate"] = bool(', gantt)
		self.assertIn("planner_history._done_assignees(", gantt)
		self.assertIn("if all_tasks or whole_job:", gantt)
		self.assertIn('"can_backdate": backdate,', gantt)
		# A Fountain Move task is never moved here, done or not.
		self.assertIn('if card.get("fountain_move"):\n\t\t\tcard["backdate"] = False', gantt)

	def test_the_maintenance_planner_marks_who_may_backdate(self):
		mp = _text(MP_API)
		planner = _pyfunc(mp, "get_planner")
		self.assertIn("can_backdate = planner_history.can_backdate()", planner)
		self.assertIn('card["backdate"] = can_backdate', planner)
		self.assertIn('"can_backdate": can_backdate,', planner)
		move = _pyfunc(mp, "move_fountain_move")
		self.assertIn("and not planner_history.can_backdate():", move)
		# move_visit itself still refuses a past day: history goes through planner_history only.
		self.assertIn('frappe.throw(_("Pick today or a later day."))', _pyfunc(mp, "_edit_visit"))
		self.assertIn("_refuse_finished(doc)", _pyfunc(mp, "move_visit"))


class TestPageWiring(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.pp = _text(PP_JS)
		cls.mp = _text(MP_JS)

	def test_project_planner_hooks(self):
		pp = self.pp
		self.assertIn('done: "ee_project_planner_done",', pp)
		self.assertIn('this.show_done = this.load_pref(PP.prefs.done, "0") === "1";', pp)
		self.assertIn("this.init_phase5();\n\t\tthis.init_done_work();", pp)
		self.assertIn("this.show_done ? { done: 1 } : {}", _method(pp, "\tload() {"))
		self.assertIn("!(card.movable || this.ppdw_can_move(card))", _method(pp, "\tdrag_source(el) {"))
		drop = _method(pp, "\tdrop(source, target) {")
		self.assertLess(
			drop.index("this.p6b_drop(source, target)"), drop.index("this.ppdw_drop(source, target)")
		)
		self.assertIn("if (this.ppdw_undo(snap)) return;", _method(pp, "\tundo() {"))
		self.assertIn("this.ppdw_display_item(), // v1.795.0: Show done work", pp)
		self.assertIn("ee_readonly: !(card.backdate || (card.movable && !card.finished)),", pp)
		self.assertIn(
			"return this.ppdw_gantt_write(card, changes);", _method(pp, "\tppg_write(task, changes, info) {")
		)
		self.assertIn('if (!card.movable && !card.backdate) out.push("ppg-locked");', pp)

	def test_project_planner_block(self):
		pp = self.pp
		block = pp[
			pp.index("const PPDW = {") : pp.index("Object.assign(ProjectPlanner.prototype, PPDW_METHODS);")
		]
		self.assertIn('api: "erpnext_enhancements.api.planner_history",', block)
		self.assertIn("method: `${PPDW.api}.move_done_task`", block)
		self.assertIn("method: `${PPDW.api}.restore_done_task`", block)
		self.assertIn(
			"this.push_undo({ ppdw: true, task: card.name, subject, before: result.before || {} }, message)",
			block,
		)
		# Never through save_task or commit(): no reason, no "Notify crew?", no draft.
		for word in ("save_task", "this.commit(", "as4_prepare", "with_draft", "notify"):
			self.assertNotIn(word, _code(block), word)
		self.assertIn("this.p6_menu_providers.push((target) => this.ppdw_menu_items(target));", block)
		# Above the tour, which stays last.
		self.assertLess(pp.index("const PPDW = {"), pp.index("const PPTOUR = {"))
		self.assertTrue(pp.rstrip().endswith("Object.assign(ProjectPlanner.prototype, PPTOUR_METHODS);"))
		css = _text(PP_CSS)
		self.assertIn(".pp-card.pp-done {", css)
		self.assertIn(".pp-chip.pp-done-chip {", css)

	def test_maintenance_planner_hooks_and_block(self):
		mp = self.mp
		self.assertIn('move_done_visit: "erpnext_enhancements.api.planner_history.move_done_visit",', mp)
		self.assertIn(
			'restore_done_visit: "erpnext_enhancements.api.planner_history.restore_done_visit",', mp
		)
		self.assertIn("this.init_phase6e();\n\t\tthis.init_done_work();", mp)
		self.assertIn("!(card.movable || this.mpdw_can_move(card))", _method(mp, "\tdrag_source(el) {"))
		drop = _method(mp, "\tdrop(source, target) {")
		self.assertLess(
			drop.index("this.p6b_drop(source, target)"), drop.index("this.mpdw_drop(source, target)")
		)
		self.assertIn('frappe.confirm(__("{0} is before today. Record the move job on a past day?"', drop)
		self.assertIn("if (this.mpdw_undo(snap)) return;", _method(mp, "\tundo() {"))
		self.assertIn(
			"this.mpdw_no_drop(drag.source && drag.source.card, target.date)",
			_method(mp, "\tmark_target(target) {"),
		)
		self.assertIn("past && !(this.data && this.data.can_backdate)", mp)
		block = mp[
			mp.index("const MPDW_PURE = {") : mp.index(
				"Object.assign(MaintenancePlanner.prototype, MPDW_METHODS);"
			)
		]
		for word in ("move_visit", "MPAS", "c1_with_draft", "notify"):
			self.assertNotIn(word, _code(block), word)
		self.assertLess(mp.index("const MPDW_PURE = {"), mp.index("const MPTOUR = {"))
		self.assertTrue(mp.rstrip().endswith("Object.assign(MaintenancePlanner.prototype, MPTOUR_METHODS);"))

	def test_the_ci_step(self):
		ci = _text(CI)
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_planner_history -v", ci)


_NODE = r"""
const fs = require("fs");
const read = (path, name) => {
	const text = fs.readFileSync(path, "utf8");
	const start = text.indexOf(`const ${name} = {`);
	const end = text.indexOf("\n};\n", start);
	return text.slice(start, end + 3);
};
global.__ = (text, args) => String(text).replace(/\{(\d+)\}/g, (m, i) => (args || [])[i]);
global.moment = (ymd) => {
	const at = new Date(`${ymd}T00:00:00Z`);
	return {
		diff: (other, unit) => Math.round((at - other.at) / 86400000),
		add(days) { at.setUTCDate(at.getUTCDate() + days); return this; },
		format: () => at.toISOString().slice(0, 10),
		at,
	};
};
eval(read(process.argv[1], "PPDW_PURE").replace("const PPDW_PURE", "global.PPDW_PURE"));
eval(read(process.argv[2], "MPDW_PURE").replace("const MPDW_PURE", "global.MPDW_PURE"));
const card = { start: "2026-03-02", end: "2026-03-04" };
console.log(JSON.stringify({
	end_for: PPDW_PURE.end_for(card, "2025-03-02"),
	end_one_day: PPDW_PURE.end_for({ start: "2026-03-02" }, "2025-03-02"),
	end_undated: PPDW_PURE.end_for({}, "2025-03-02"),
	ok: PPDW_PURE.refusal("2025-03-02", "2025-03-04", "2026-11-04"),
	today_ok: PPDW_PURE.refusal("2026-11-04", "2026-11-04", "2026-11-04"),
	future: PPDW_PURE.refusal("2026-11-03", "2026-11-05", "2026-11-04"),
	backwards: PPDW_PURE.refusal("2026-03-04", "2026-03-02", "2026-11-04"),
	none: PPDW_PURE.refusal("", "", "2026-11-04"),
	visit_ok: MPDW_PURE.refusal("2026-10-01", "2026-11-04"),
	visit_future: MPDW_PURE.refusal("2026-11-05", "2026-11-04"),
}));
"""


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestPageRulesUnderNode(unittest.TestCase):
	def test_the_pure_rules(self):
		result = subprocess.run(
			[shutil.which("node"), "-e", _NODE, str(PP_JS), str(MP_JS)],
			capture_output=True,
			text=True,
			encoding="utf-8",
		)
		self.assertEqual(result.returncode, 0, result.stderr)
		out = json.loads(result.stdout)
		self.assertEqual(out["end_for"], "2025-03-04")
		self.assertEqual(out["end_one_day"], "2025-03-02")
		self.assertEqual(out["end_undated"], "2025-03-02")
		self.assertIsNone(out["ok"])
		self.assertIsNone(out["today_ok"])
		self.assertEqual(out["future"], "Done work goes on today or an earlier day.")
		self.assertEqual(out["backwards"], "The end date is before the start date.")
		self.assertEqual(out["none"], "Pick the day it was done.")
		self.assertIsNone(out["visit_ok"])
		self.assertEqual(out["visit_future"], "Done work goes on today or an earlier day.")


if __name__ == "__main__":
	unittest.main()
