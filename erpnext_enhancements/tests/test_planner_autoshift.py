# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""P4 of the Planner UX redesign: the tasks that follow move with a task, a Draft schedule drafts
every write, and "Notify crew?" (``docs/planner-ux-redesign.md``; Nik's D8, D10, D11 and D15,
2026-10-21).

What this pins, because each of them fails quietly:

* **The tasks that follow move with the task, both ways, by working days.** ``dependents=1`` on
  ``save_task`` / ``swap_crew`` / ``move_many`` moves every open, dated task that waits on it (directly
  or through others) by the working days its **end** moved: a drag later or earlier, a resize of the
  right edge, never a resize of the left edge. One request, one conflict check for the lot (the
  answer lists the moves), one reason.
* **The order of the saves.** Later: the furthest follower first and the task itself last, so
  ERPNext's own ``reschedule_dependent_tasks`` (a calendar-day push of an overlapping Open
  dependent) finds nothing to move. Earlier: the task first. A finished, rental or undated follower is
  left where it is and named.
* **Without ``dependents`` nothing changes**: no follower is touched and the old ``successors``
  report still comes back for any other caller.
* **``draft="auto"`` drafts by the project's schedule**: a Draft project's task is drafted (and its
  followers with it), a published one is saved; a follower in a project whose schedule says
  otherwise is left alone rather than crossing the line.
* **One notice for the gesture**: a "Notify crew?" answer covers the task and its followers, one
  notice per person.
* **The page's strip can count drafts per project** and name another scheduler's.
* ``scripts/test_planner_autoshift.js`` (run here under node) drives the page: Ctrl/Cmd moves just
  the one task, a Draft schedule's writes say ``draft="auto"``, the Notify default by the 14-day
  window, one question for a multi-select move, and Undo of everything.

Bench-free. Reuses ``test_planner_phase3b``'s ``frappe`` stub, as ``test_planner_publish_baseline``
does, with the P3 columns switched on.

Run: python -m unittest erpnext_enhancements.tests.test_planner_autoshift
"""

import ast
import copy
import datetime
import importlib
import json
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

from erpnext_enhancements.tests import test_planner_phase3b as h

APP = Path(__file__).resolve().parents[1]
API_PATH = APP / "api/project_planner.py"
ACTIONS_PATH = APP / "api/planner_actions.py"
HARNESS = REPO_ROOT / "scripts/test_planner_autoshift.js"
CI = REPO_ROOT / ".github/workflows/ci.yml"

D = datetime.date
BASE = h.BASE
ACTIONS = "erpnext_enhancements.api.planner_actions"
#: When ERPNext's own reschedule saves a task in these tests: after this publish began (h.NOW).
PUSHED_AT = "2026-10-08 11:11:11"


def setUpModule():
	h.setUpModule()


def tearDownModule():
	h.tearDownModule()


def _actions():
	import importlib

	return importlib.import_module(ACTIONS)


# ====================================================================== pure helpers


class TestDependentDays(unittest.TestCase):
	def test_a_move_carries_the_followers_by_the_working_days_its_end_moved(self):
		days = h.api.dependent_days
		# Mon-Wed to Wed-Fri: two working days later.
		self.assertEqual(days((D(2026, 10, 12), D(2026, 10, 14)), (D(2026, 10, 14), D(2026, 10, 16))), 2)
		# Fri to the Monday after: one working day, not three.
		self.assertEqual(days(("2026-10-16", "2026-10-16"), ("2026-10-19", "2026-10-19")), 1)
		# Earlier (D15): Wed-Fri back to Mon-Wed.
		self.assertEqual(days(("2026-10-14", "2026-10-16"), ("2026-10-12", "2026-10-14")), -2)

	def test_a_resize_moves_them_only_by_its_right_edge(self):
		days = h.api.dependent_days
		self.assertEqual(days(("2026-10-12", "2026-10-14"), ("2026-10-12", "2026-10-15")), 1)
		self.assertEqual(days(("2026-10-12", "2026-10-14"), ("2026-10-09", "2026-10-14")), 0)  # left edge
		self.assertEqual(days(("2026-10-12", "2026-10-14"), ("2026-10-12", "2026-10-13")), -1)

	def test_a_weekend_to_weekend_end_still_moves_one_day(self):
		days = h.api.dependent_days
		self.assertEqual(days(("2026-10-17", "2026-10-17"), ("2026-10-18", "2026-10-18")), 1)
		self.assertEqual(days(("2026-10-18", "2026-10-18"), ("2026-10-17", "2026-10-17")), -1)

	def test_nothing_moves_without_both_spans(self):
		self.assertEqual(h.api.dependent_days(None, ("2026-10-12", "2026-10-12")), 0)
		self.assertEqual(h.api.dependent_days(("2026-10-12", "2026-10-12"), None), 0)
		self.assertEqual(h.api.dependent_days(("2026-10-12", "2026-10-14"), ("2026-10-12", "2026-10-14")), 0)


class TestDraftChoice(unittest.TestCase):
	def test_auto_one_and_the_rest(self):
		choice = h.api.draft_choice
		self.assertEqual(choice("auto"), "auto")
		self.assertEqual(choice(" Auto "), "auto")
		self.assertIs(choice("1"), True)
		self.assertIs(choice(1), True)
		for off in (None, "", "0", 0, "null", "undefined"):
			self.assertIs(choice(off), False, off)


# ====================================================================== the harness


class _P4(h._Base):
	"""Phase 3B's harness with the P3 columns, project schedule states, a dependency table, a batch
	conflict check this test controls, and the publish notice recorded."""

	def setUp(self):
		super().setUp()
		frappe, self.api = h.frappe, h.api
		self.frappe = frappe
		frappe.db.has_column = lambda doctype, column: True
		self.projects = {
			"PRJ-1": {"custom_schedule_status": "Published"},
			"PRJ-2": {"custom_schedule_status": "Draft"},
		}
		stub_get_value = frappe.db.get_value

		def get_value(doctype, name, field=None, *args, as_dict=False, **kwargs):
			if doctype == "Project" and isinstance(field, str):
				return (self.projects.get(name) or {}).get(field)
			if doctype == "Task" and isinstance(field, str):
				row = self._stored(name)
				return row.get(field) if row else None
			if doctype == "Task" and isinstance(field, list | tuple):
				row = self._stored(name)
				return h._Doc({key: row.get(key) for key in field}) if row else None
			return stub_get_value(doctype, name, field, *args, **kwargs)

		def reload(doc):
			doc.update(copy.deepcopy(self._stored(doc.name)))

		# The harness's save snapshots the task before it bumps `modified`; the database holds the
		# bumped one (SAVED_AT), or what an ERPNext push wrote (`_db_modified`).
		self.patches.append(mock.patch.object(h._TaskDoc, "reload", reload, create=True))

		frappe.db.get_value = get_value
		frappe.tables["Task Depends On"] = []
		# Task rows as the database holds them now (each task's latest save), for _task_rows.
		frappe.tables["Task"] = lambda filters: [
			row
			for row in (self._stored(name) for (doctype, name) in list(frappe.docs) if doctype == "Task")
			if row and h._matches(row, filters)
		]
		self.batch_hook = None
		self.batches = []
		self.notified = []

		def preview_batch(changes, start=None, end=None):
			self.batches.append(
				[(state.get("name"), str(h.engine.task_span(state))) for state, _crew in changes]
			)
			return self.batch_hook(changes) if self.batch_hook else {}

		self.patches += [
			mock.patch.object(h.engine, "preview_batch", preview_batch),
			mock.patch.object(
				h.notices,
				"send_publish_notices",
				lambda out, publisher, *a: self.notified.append((out, publisher)) or len(out),
			),
		]
		for patch in self.patches[2:]:
			patch.start()

	SAVED_AT = "2026-10-08 10:00:01"

	def _stored(self, name):
		"""The Task as the database holds it now: its latest save, else as loaded."""
		snaps = [snap for snap in self.frappe.saved if snap.get("name") == name]
		if not snaps:
			doc = self.frappe.docs.get(("Task", name))
			return dict(doc) if doc else None
		row = dict(snaps[-1])
		row["modified"] = row.pop("_db_modified", None) or self.SAVED_AT
		return row

	def _erpnext_pushes(self, when_saved, pushed, start, end):
		"""Make ERPNext's own reschedule_dependent_tasks push ``pushed`` to ``start``..``end`` (by the
		calendar, and saved) right after ``when_saved`` is saved, as Task.on_update does."""
		real_save = self.api._save

		def save(doc):
			out = real_save(doc)
			if doc.name == when_saved:
				row = self._stored(pushed)
				row.update(exp_start_date=start, exp_end_date=end, _db_modified="2026-10-08 11:11:11")
				self.frappe.saved.append(row)
				self.frappe.sequence.append(f"save:{pushed}:erpnext")
			return out

		self.patches.append(mock.patch.object(self.api, "_save", save))
		self.patches[-1].start()

	def _chain(self):
		"""TASK-1 (Mon-Wed) <- TASK-2 (Thu-Fri) <- TASK-3 (the Monday after), all on PRJ-1."""
		h._task("TASK-1", crew=("RES-1",))
		h._task("TASK-2", start="2026-10-15 08:00:00", end="2026-10-16 17:00:00", crew=("RES-2",))
		h._task("TASK-3", start="2026-10-19", end="2026-10-19", crew=("RES-2",))
		self._depends("TASK-2", "TASK-1")
		self._depends("TASK-3", "TASK-2")

	def _depends(self, task, on):
		self.frappe.tables["Task Depends On"].append({"parent": task, "parenttype": "Task", "task": on})
		self.frappe.docs[("Task", task)].setdefault("depends_on", []).append(h._Doc(task=on))

	def _saves(self):
		return [step.split(":", 1)[1] for step in self.frappe.sequence if step.startswith("save:TASK")]

	def _saved(self, name):
		return [snap for snap in self.frappe.saved if snap["name"] == name][-1]


# ====================================================================== save_task


class TestTheTasksThatFollowMoveWithIt(_P4):
	def setUp(self):
		super().setUp()
		self._chain()

	def test_later_moves_every_follower_by_the_same_working_days_furthest_first(self):
		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", dependents="1")

		self.assertEqual(result["shift_days"], 2)
		self.assertEqual(
			[(m["task"], m["from"], m["to"], m["to_end"]) for m in result["shifted"]],
			[
				("TASK-3", "2026-10-19", "2026-10-21", "2026-10-21"),
				("TASK-2", "2026-10-15", "2026-10-19", "2026-10-20"),
			],
		)
		# The furthest first, the task itself last: ERPNext's own push then finds nothing to move.
		self.assertEqual(self._saves(), ["TASK-3", "TASK-2", "TASK-1"])
		# Each keeps its time of day and its length in working days (Thu-Fri -> Mon-Tue).
		task2 = self._saved("TASK-2")
		self.assertEqual(
			(task2["exp_start_date"], task2["exp_end_date"]), ("2026-10-19 08:00:00", "2026-10-20 17:00:00")
		)
		self.assertNotIn("successors", result)  # no offer: they already moved
		self.assertTrue(all(m["modified"] for m in result["shifted"]))

	def test_earlier_moves_them_earlier_with_the_task_first(self):
		result = self.api.save_task("TASK-1", BASE, start="2026-10-09", dependents=1)  # Fri before

		self.assertEqual(result["shift_days"], -3)  # its end: Wed to the Sunday before, three working days
		self.assertEqual(self._saves()[0], "TASK-1")
		self.assertEqual(
			{m["task"]: (m["to"], m["to_end"]) for m in result["shifted"]},
			{"TASK-2": ("2026-10-12", "2026-10-13"), "TASK-3": ("2026-10-14", "2026-10-14")},
		)
		# Nearest first after the task: TASK-2 before TASK-3.
		self.assertEqual(self._saves(), ["TASK-1", "TASK-2", "TASK-3"])

	def test_a_resize_of_the_right_edge_carries_them_and_of_the_left_edge_does_not(self):
		result = self.api.save_task("TASK-1", BASE, start="2026-10-12", end="2026-10-15", dependents=1)
		self.assertEqual(result["shift_days"], 1)
		self.assertEqual(len(result["shifted"]), 2)

		h._task("TASK-1", crew=("RES-1",))
		self.frappe.saved.clear()
		self.frappe.sequence.clear()
		result = self.api.save_task("TASK-1", BASE, start="2026-10-09", end="2026-10-14", dependents=1)
		self.assertNotIn("shifted", result)
		self.assertEqual(self._saves(), ["TASK-1"])

	def test_without_dependents_nothing_else_moves(self):
		with mock.patch.object(self.api, "_successor_snapshot", lambda name: {}) as _snap:
			result = self.api.save_task("TASK-1", BASE, start="2026-10-14")
		self.assertIsNotNone(_snap)
		self.assertNotIn("shifted", result)
		self.assertEqual(self._saves(), ["TASK-1"])
		self.assertEqual(self.batches, [])  # the single-task check, as before

	def test_one_conflict_check_for_the_lot_and_one_reason(self):
		def hook(changes):
			# A clash only once TASK-2 lands on the 19th with TASK-3's old day still there.
			names = [state.get("name") for state, _crew in changes]
			spans = {state.get("name"): h.engine.task_span(state) for state, _crew in changes}
			if "TASK-2" in names and spans["TASK-2"][0] == D(2026, 10, 19):
				return {"Lisa Park": ["2026-10-19: Over by 4h"]}
			return {}

		self.batch_hook = hook
		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", dependents=1)
		self.assertTrue(result["needs_reason"])
		self.assertEqual(result["conflicts"], {"Lisa Park": ["2026-10-19: Over by 4h"]})
		self.assertEqual([m["task"] for m in result["moves"]], ["TASK-2", "TASK-3"])
		self.assertEqual(self.frappe.saved, [])
		# The batch is the task and its followers, before and after.
		self.assertEqual([len(batch) for batch in self.batches], [3, 3])

		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", dependents=1, reason="Lisa is fine")
		self.assertEqual(self._saves(), ["TASK-3", "TASK-2", "TASK-1"])
		reasons = [name for name, _kind, text in self.frappe.comments if "Lisa is fine" in text]
		self.assertEqual(sorted(reasons), ["TASK-1", "TASK-2", "TASK-3"])

	def test_a_finished_rental_or_undated_follower_stays_and_is_named(self):
		self.frappe.docs[("Task", "TASK-3")]["status"] = "Completed"
		h._task("TASK-4", start="2026-10-20", end="2026-10-20", custom_rental_booking="RB-1")
		h._task("TASK-5", start=None, end=None)
		self._depends("TASK-4", "TASK-1")
		self._depends("TASK-5", "TASK-1")

		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", dependents=1)
		self.assertEqual([m["task"] for m in result["shifted"]], ["TASK-2"])
		self.assertEqual(
			{s["task"]: s["reason"] for s in result["shift_skipped"]},
			{
				"TASK-3": "It is Completed.",
				"TASK-4": "Its dates follow Rental Booking RB-1.",
				"TASK-5": "It has no dates.",
			},
		)
		self.assertEqual(self._saves(), ["TASK-2", "TASK-1"])

	def test_a_follower_erpnext_pushed_after_it_was_saved_is_put_back(self):
		# TASK-2 already overlaps TASK-1 (it starts the day TASK-1 ends): shifted alike, it still does,
		# so saving TASK-1 last makes ERPNext push it to the day after TASK-1's end, by the calendar.
		self.frappe.docs[("Task", "TASK-2")].update(exp_start_date="2026-10-14 08:00:00")
		self._erpnext_pushes("TASK-1", "TASK-2", "2026-10-17 08:00:00", "2026-10-19 17:00:00")
		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", dependents=1)
		self.assertEqual(
			[step for step in self.frappe.sequence if step.startswith("save:TASK")],
			["save:TASK-3", "save:TASK-2", "save:TASK-1", "save:TASK-2:erpnext", "save:TASK-2"],
		)
		task2 = self._stored("TASK-2")
		self.assertEqual(
			(task2["exp_start_date"], task2["exp_end_date"]), ("2026-10-16 08:00:00", "2026-10-20 17:00:00")
		)
		shifted = {m["task"]: m for m in result["shifted"]}
		self.assertEqual(shifted["TASK-2"]["modified"], self.SAVED_AT)

	def test_moving_earlier_a_pushed_follower_is_reloaded_not_a_failed_move(self):
		# Moving earlier the task is saved first; ERPNext pushes the overlapping TASK-2, and the copy in
		# hand is stale. It is reloaded and given its planned days instead of raising "changed since".
		self.frappe.docs[("Task", "TASK-2")].update(exp_start_date="2026-10-14 08:00:00")
		self._erpnext_pushes("TASK-1", "TASK-2", "2026-10-12 08:00:00", "2026-10-14 17:00:00")
		self.api.save_task("TASK-1", BASE, start="2026-10-09", dependents=1)
		task2 = self._stored("TASK-2")
		self.assertEqual(
			(task2["exp_start_date"], task2["exp_end_date"]), ("2026-10-09 08:00:00", "2026-10-13 17:00:00")
		)
		self.assertEqual(self._saves().count("TASK-2"), 1)  # ours, once, after ERPNext's

	def _fountain_move(self):
		self.frappe.docs[("Task", "TASK-3")]["project"] = "PRJ-MOVE"
		moves = sys.modules["erpnext_enhancements.project_enhancements.fountain_move_jobs"]
		patch = mock.patch.object(moves, "move_projects", lambda names: {"PRJ-MOVE"})
		patch.start()
		self.addCleanup(patch.stop)

	def test_a_fountain_move_follower_is_left_to_the_maintenance_planner(self):
		self._fountain_move()
		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", dependents=1)
		self.assertEqual([m["task"] for m in result["shifted"]], ["TASK-2"])
		self.assertIn("Fountain Move", result["shift_skipped"][0]["reason"])

	def test_a_drafted_move_names_a_fountain_move_follower_rather_than_failing(self):
		# _load refuses a Fountain Move task, so it is named before a draft context is built for it.
		self._fountain_move()
		for name in ("TASK-1", "TASK-2"):
			self.frappe.docs[("Task", name)]["project"] = "PRJ-2"  # a Draft schedule
		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", draft="auto", dependents=1)
		self.assertEqual([m["task"] for m in result["shifted"]], ["TASK-2"])
		self.assertEqual(result["shift_skipped"][0]["task"], "TASK-3")
		self.assertIn("Fountain Move", result["shift_skipped"][0]["reason"])

	def test_a_crew_handover_with_a_move_carries_them_too(self):
		result = self.api.swap_crew("TASK-1", "RES-1", "RES-2", BASE, date="2026-10-13", dependents=1)
		self.assertEqual(result["shift_days"], 1)
		self.assertEqual([m["task"] for m in result["shifted"]], ["TASK-3", "TASK-2"])


class TestNotifyCoversTheFollowers(_P4):
	def test_one_answer_tells_each_person_once_for_the_task_and_its_followers(self):
		self._chain()
		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", dependents=1, notify="1")
		((out, publisher),) = self.notified
		self.assertEqual(publisher, "nik@example.com")
		# Austin (TASK-1) and Lisa (TASK-2 and TASK-3), each once; never the person editing.
		self.assertEqual(set(out), {"austin@example.com", "lisa@example.com"})
		self.assertEqual(result["notified"], 2)

	def test_dont_notify_tells_nobody(self):
		self._chain()
		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", dependents=1, notify="0")
		self.assertEqual((self.notified, result["notified"]), ([], 0))


# ====================================================================== draft="auto"


class TestADraftScheduleDraftsEveryWrite(_P4):
	def test_a_draft_projects_task_is_drafted_with_its_followers(self):
		self._chain()
		for name in ("TASK-1", "TASK-2", "TASK-3"):
			self.frappe.docs[("Task", name)]["project"] = "PRJ-2"
		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", draft="auto", dependents=1)

		self.assertTrue(result["drafted"])
		self.assertEqual(self.frappe.saved, [])  # nothing reaches the Task
		drafts = {row["task"]: json.loads(row["payload"]) for row in self._drafts()}
		self.assertEqual(set(drafts), {"TASK-1", "TASK-2", "TASK-3"})
		self.assertEqual((drafts["TASK-2"]["start"], drafts["TASK-2"]["end"]), ("2026-10-19", "2026-10-20"))
		self.assertEqual([m["task"] for m in result["shifted"]], ["TASK-2", "TASK-3"])
		self.assertEqual(self.notified, [])

	def test_a_published_projects_task_is_saved(self):
		self._chain()
		result = self.api.save_task("TASK-1", BASE, start="2026-10-19", draft="auto")
		self.assertNotIn("drafted", result)
		self.assertEqual(self._saves(), ["TASK-1"])
		self.assertEqual(self._drafts(), [])

	def test_add_crew_and_swap_crew_follow_the_same_rule(self):
		h._task("TASK-1", crew=("RES-1",), project="PRJ-2")
		self.assertTrue(self.api.add_crew("TASK-1", "RES-2", BASE, draft="auto")["drafted"])
		h._task("TASK-7", crew=("RES-1",))
		self.assertNotIn("drafted", self.api.swap_crew("TASK-7", "RES-1", "RES-2", BASE, draft="auto"))

	def test_a_follower_never_crosses_into_a_schedule_that_says_otherwise(self):
		self._chain()
		self.frappe.docs[("Task", "TASK-3")]["project"] = "PRJ-2"  # Draft
		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", draft="auto", dependents=1)
		self.assertEqual([m["task"] for m in result["shifted"]], ["TASK-2"])
		((skip,),) = [result["shift_skipped"]]
		self.assertEqual(skip["task"], "TASK-3")
		self.assertIn("Draft", skip["reason"])
		self.assertEqual(self._drafts(), [])

	def test_the_hold_drafts_everything_whatever_the_project(self):
		self._chain()
		self.frappe.docs[("Task", "TASK-3")]["project"] = "PRJ-2"
		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", draft=1, dependents=1)
		self.assertEqual([m["task"] for m in result["shifted"]], ["TASK-2", "TASK-3"])
		self.assertEqual(self.frappe.saved, [])

	def test_before_the_columns_exist_auto_saves_as_before(self):
		self._chain()
		self.frappe.db.has_column = lambda doctype, column: column not in h.P3_COLUMNS
		self.projects["PRJ-1"]["custom_schedule_status"] = "Draft"
		result = self.api.save_task("TASK-1", BASE, start="2026-10-19", draft="auto")
		self.assertNotIn("drafted", result)
		self.assertEqual(self._saves(), ["TASK-1"])


# ====================================================================== move_many


class TestMoveMany(_P4):
	def test_a_selection_moves_its_followers_once(self):
		self._chain()
		h._task("TASK-4", start="2026-10-20", end="2026-10-20", crew=("RES-1",))
		self._depends("TASK-4", "TASK-3")
		moves = [
			{"task": "TASK-1", "modified": BASE, "start": "2026-10-14", "end": "2026-10-16"},
			{"task": "TASK-2", "modified": BASE, "start": "2026-10-19", "end": "2026-10-20"},
		]
		result = _actions().move_many(json.dumps(moves), dependents=1)

		moved = {m["task"]: m for m in result["moved"]}
		self.assertEqual(set(moved), {"TASK-1", "TASK-2", "TASK-3", "TASK-4"})
		# TASK-2 is in the selection: it moved where it was put, not twice.
		self.assertEqual(moved["TASK-2"]["to"], "2026-10-19")
		self.assertNotIn("dependent", moved["TASK-2"])
		self.assertTrue(moved["TASK-3"]["dependent"] and moved["TASK-4"]["dependent"])
		self.assertEqual((moved["TASK-3"]["to"], moved["TASK-4"]["to"]), ("2026-10-21", "2026-10-22"))
		self.assertEqual(self._saves(), ["TASK-4", "TASK-3", "TASK-2", "TASK-1"])
		self.assertEqual(result["shift_skipped"], [])

	def test_without_dependents_only_the_selection_moves(self):
		self._chain()
		moves = [{"task": "TASK-1", "modified": BASE, "start": "2026-10-14", "end": "2026-10-16"}]
		result = _actions().move_many(json.dumps(moves))
		self.assertEqual([m["task"] for m in result["moved"]], ["TASK-1"])
		self.assertNotIn("shift_skipped", result)

	def test_auto_drafts_a_draft_projects_moves_and_saves_the_rest(self):
		h._task("TASK-1", crew=("RES-1",))
		h._task("TASK-8", crew=("RES-2",), project="PRJ-2")
		moves = [
			{"task": "TASK-1", "modified": BASE, "start": "2026-10-19", "end": "2026-10-21"},
			{"task": "TASK-8", "modified": BASE, "start": "2026-10-19", "end": "2026-10-21"},
		]
		result = _actions().move_many(json.dumps(moves), draft="auto", notify="1")
		moved = {m["task"]: m for m in result["moved"]}
		self.assertNotIn("drafted", moved["TASK-1"])
		self.assertTrue(moved["TASK-8"]["drafted"])
		self.assertEqual(self._saves(), ["TASK-1"])
		self.assertEqual([row["task"] for row in self._drafts()], ["TASK-8"])
		# The notice is the saved move's only: a draft tells nobody until it is published.
		((out, _publisher),) = self.notified
		self.assertEqual(set(out), {"austin@example.com"})

	def test_all_drafted_when_every_move_is_on_a_draft_schedule(self):
		self._chain()
		for name in ("TASK-1", "TASK-2", "TASK-3"):
			self.frappe.docs[("Task", name)]["project"] = "PRJ-2"
		moves = [{"task": "TASK-1", "modified": BASE, "start": "2026-10-13", "end": "2026-10-15"}]
		result = _actions().move_many(json.dumps(moves), draft="auto", dependents=1)
		self.assertTrue(result["drafted"])
		self.assertEqual({m["task"] for m in result["moved"] if m.get("dependent")}, {"TASK-2", "TASK-3"})
		self.assertEqual(self.frappe.saved, [])


# ====================================================================== shift_successors (kept)


class TestShiftSuccessorsStillWorks(_P4):
	def test_it_moves_the_followers_alone(self):
		self._chain()
		result = self.api.shift_successors("TASK-1", 1, BASE)
		self.assertEqual([m["task"] for m in result["moved"]], ["TASK-3", "TASK-2"])
		self.assertEqual(self._saves(), ["TASK-3", "TASK-2"])

	def test_it_honors_the_names_it_is_given(self):
		self._chain()
		# One day earlier, TASK-2 starts before TASK-1 (which stays) ends: that asks for a reason now.
		asked = self.api.shift_successors("TASK-1", -1, BASE, tasks=json.dumps(["TASK-2"]))
		self.assertEqual(list(asked["conflicts"]), ["Dependencies"])
		result = self.api.shift_successors("TASK-1", -1, BASE, tasks=json.dumps(["TASK-2"]), reason="ok")
		self.assertEqual([m["task"] for m in result["moved"]], ["TASK-2"])


# ====================================================================== the manager's review (P4 #1323)


class TestADraftedChainPublishesOnItsDraftedDays(_P4):
	"""Finding 1: drafted followers must survive Publish. ERPNext's own reschedule, fired by the task
	published before them, used to make the follower's draft fail as "Changed by someone else"."""

	def setUp(self):
		super().setUp()
		self.patches.append(
			mock.patch.object(
				h.engine,
				"_compute",
				lambda *a, **k: {
					"resources": [{"name": p["name"], "label": p["resource_name"]} for p in h.PEOPLE],
					"days": {},
				},
			)
		)
		self.patches[-1].start()

	def _erpnext_reschedules(self, saved, follower, pushed_to):
		"""ERPNext's Task.on_update: saving ``saved`` pushes ``follower`` when it starts on or before
		``saved``'s new end (as the database holds it then), by the calendar, and saves it."""
		real_save = self.api._save

		def save(doc):
			out = real_save(doc)
			if doc.name == saved:
				row = self._stored(follower)
				if str(row["exp_start_date"])[:10] <= str(doc.get("exp_end_date"))[:10]:
					row.update(exp_start_date=pushed_to[0], exp_end_date=pushed_to[1], _db_modified=PUSHED_AT)
					self.frappe.saved.append(row)
					self.frappe.docs[("Task", follower)].update(
						exp_start_date=pushed_to[0], exp_end_date=pushed_to[1], modified=PUSHED_AT
					)
					self.frappe.sequence.append(f"save:{follower}:erpnext")
			return out

		self.patches.append(mock.patch.object(self.api, "_save", save))
		self.patches[-1].start()

	def _draft_chain(self, a_span, b_span, drag_to):
		h._task("TASK-A", start=a_span[0], end=a_span[1], crew=("RES-1",), project="PRJ-2")
		h._task("TASK-B", start=b_span[0], end=b_span[1], crew=("RES-2",), project="PRJ-2")
		self._depends("TASK-B", "TASK-A")
		return self.api.save_task("TASK-A", BASE, start=drag_to, draft="auto", dependents=1)

	def test_the_managers_scenario_publishes_both_on_their_drafted_days(self):
		# A Mon-Wed, B Thu-Fri after it; A dragged two days later: A Wed-Fri, B the Monday-Tuesday after.
		drafted = self._draft_chain(("2026-10-26", "2026-10-28"), ("2026-10-29", "2026-10-30"), "2026-10-28")
		self.assertEqual(
			[(m["task"], m["to"], m["to_end"]) for m in drafted["shifted"]],
			[("TASK-B", "2026-11-02", "2026-11-03")],
		)
		self._erpnext_reschedules("TASK-A", "TASK-B", ("2026-10-31", "2026-11-01"))
		result = self.api.publish_drafts(project="PRJ-2")

		self.assertEqual(result["failed"], [])
		self.assertEqual(sorted(p["task"] for p in result["published"]), ["TASK-A", "TASK-B"])
		b = self._stored("TASK-B")
		self.assertEqual((b["exp_start_date"][:10], b["exp_end_date"][:10]), ("2026-11-02", "2026-11-03"))
		# Furthest first: B is saved before A, so A's save finds it already out of the way.
		self.assertEqual(self._saves(), ["TASK-B", "TASK-A"])
		self.assertNotIn("save:TASK-B:erpnext", self.frappe.sequence)

	def test_a_follower_erpnext_pushed_during_the_publish_still_takes_its_draft(self):
		# B already overlapped A (it runs inside it), so A's save pushes it whatever the order: the
		# push happened in this publish, so it is not "changed by someone else".
		drafted = self._draft_chain(("2026-10-26", "2026-10-30"), ("2026-10-28", "2026-10-29"), "2026-10-27")
		self.assertEqual([m["to"] for m in drafted["shifted"]], ["2026-10-29"])
		self._erpnext_reschedules("TASK-A", "TASK-B", ("2026-11-03", "2026-11-04"))
		result = self.api.publish_drafts(project="PRJ-2")

		self.assertEqual(result["failed"], [])
		self.assertIn("save:TASK-B:erpnext", self.frappe.sequence)
		b = self._stored("TASK-B")
		self.assertEqual((b["exp_start_date"][:10], b["exp_end_date"][:10]), ("2026-10-29", "2026-10-30"))

	def test_a_reapplied_draft_is_judged_from_where_it_was_drafted_not_from_the_push(self):
		# Its overlap with A predates the draft, so it is no new conflict: no fallback reason on it.
		self._draft_chain(("2026-10-26", "2026-10-30"), ("2026-10-28", "2026-10-29"), "2026-10-27")
		self._erpnext_reschedules("TASK-A", "TASK-B", ("2026-11-03", "2026-11-04"))
		self.api.publish_drafts(project="PRJ-2")
		self.assertEqual([c for c in self.frappe.comments if c[0] == "TASK-B"], [])

	def test_someone_elses_change_before_the_publish_is_still_refused(self):
		self._draft_chain(("2026-10-26", "2026-10-28"), ("2026-10-29", "2026-10-30"), "2026-10-28")
		self.frappe.docs[("Task", "TASK-B")]["modified"] = "2026-10-08 06:00:00"  # before this publish
		result = self.api.publish_drafts(project="PRJ-2")
		self.assertEqual([s["task"] for s in result["skipped"]], ["TASK-B"])


class TestDraftedFollowersWarn(_P4):
	"""Finding 2: a drafted follower that overbooks someone warns, from one batch check."""

	def test_the_lot_is_checked_together_and_says_so(self):
		self._chain()
		for name in ("TASK-1", "TASK-2", "TASK-3"):
			self.frappe.docs[("Task", name)]["project"] = "PRJ-2"

		def hook(changes):
			spans = {state.get("name"): h.engine.task_span(state) for state, _crew in changes}
			if spans.get("TASK-2") and spans["TASK-2"][0] == D(2026, 10, 19):
				return {"Lisa Park": ["2026-10-19: Over by 4h"]}
			return {}

		self.batch_hook = hook
		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", draft="auto", dependents=1)
		self.assertTrue(result["drafted"])
		self.assertEqual(result["conflicts"]["Lisa Park"], ["2026-10-19: Over by 4h"])
		self.assertIn("Not published yet. Lisa Park: 2026-10-19: Over by 4h", result["warnings"])
		# One batch: the task and both followers, stored against drafted.
		self.assertEqual([len(batch) for batch in self.batches], [3, 3])


class TestADraftedFollowerAgainstOtherDrafts(_P4):
	def test_a_predecessors_draft_counts(self):
		h._task("TASK-A", start="2026-10-12", end="2026-10-14", crew=("RES-1",), project="PRJ-2")
		h._task("TASK-X", start="2026-10-12", end="2026-10-14", crew=("RES-2",), project="PRJ-2")
		h._task("TASK-C", start="2026-10-19", end="2026-10-19", crew=("RES-2",), project="PRJ-2")
		self._depends("TASK-C", "TASK-A")
		self._depends("TASK-C", "TASK-X")
		self.api.save_task("TASK-X", BASE, end="2026-10-19", draft="auto")  # X drafted to run on
		# A's right edge one day earlier: C follows to Friday, before X's DRAFTED end (stored: Wed).
		result = self.api.save_task(
			"TASK-A", BASE, start="2026-10-12", end="2026-10-13", draft="auto", dependents=1
		)
		self.assertEqual([m["to"] for m in result["shifted"]], ["2026-10-16"])
		self.assertEqual(
			result["conflicts"]["Dependencies"], ["Starts before TASK-X (Subject TASK-X) ends on 2026-10-19"]
		)


class TestEveryFollowerAgainstItsOtherPredecessors(_P4):
	"""Finding 3: a follower is checked against every task it waits on, and the walk stops at a task
	that stays."""

	def test_moved_earlier_past_a_predecessor_that_stays_asks_for_a_reason(self):
		h._task("TASK-1", crew=("RES-1",))  # Mon 12 - Wed 14
		h._task("TASK-X", start="2026-10-12", end="2026-10-16", crew=("RES-2",))  # stays
		h._task("TASK-C", start="2026-10-19", end="2026-10-19", crew=("RES-2",))
		self._depends("TASK-C", "TASK-1")
		self._depends("TASK-C", "TASK-X")
		result = self.api.save_task("TASK-1", BASE, start="2026-10-09", dependents=1)  # three earlier
		self.assertTrue(result["needs_reason"])
		self.assertEqual(
			result["conflicts"]["Dependencies"], ["Starts before TASK-X (Subject TASK-X) ends on 2026-10-16"]
		)
		self.assertEqual(self.frappe.saved, [])

	def test_moved_with_its_predecessor_is_not_news(self):
		self._chain()
		result = self.api.save_task("TASK-1", BASE, start="2026-10-09", dependents=1)
		self.assertNotIn("needs_reason", result)

	def test_the_walk_stops_at_a_task_that_stays(self):
		self._chain()
		self.frappe.docs[("Task", "TASK-2")]["status"] = "Completed"
		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", dependents=1)
		self.assertEqual(result["shifted"], [])  # TASK-3 waits on TASK-1 only through TASK-2
		self.assertEqual([s["task"] for s in result["shift_skipped"]], ["TASK-2"])
		self.assertEqual(self._saves(), ["TASK-1"])


class TestAFollowerTheCallerCannotChange(_P4):
	"""Finding 4: one unwritable follower is named and left, never the whole gesture refused."""

	def setUp(self):
		super().setUp()
		self._chain()
		self.frappe.has_permission = lambda doctype=None, ptype=None, doc=None, **k: doc != "TASK-2"

	def test_live(self):
		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", dependents=1)
		self.assertEqual(result["shifted"], [])
		self.assertEqual(
			[(s["task"], s["reason"]) for s in result["shift_skipped"]], [("TASK-2", "You cannot change it.")]
		)
		self.assertEqual(self._saves(), ["TASK-1"])

	def test_drafted(self):
		for name in ("TASK-1", "TASK-2", "TASK-3"):
			self.frappe.docs[("Task", name)]["project"] = "PRJ-2"
		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", draft="auto", dependents=1)
		self.assertEqual(result["shifted"], [])
		self.assertEqual([s["task"] for s in result["shift_skipped"]], ["TASK-2"])
		self.assertEqual([row["task"] for row in self._drafts()], ["TASK-1"])


class TestTheNearerMovedTaskCarriesAFollower(_P4):
	"""Finding 5: two selected tasks moved by different amounts; a follower takes the nearer one's."""

	def test_downstream_first(self):
		self._chain()
		self._depends("TASK-3", "TASK-1")  # C waits on both A and B
		moves = [
			{"task": "TASK-1", "modified": BASE, "start": "2026-10-13", "end": "2026-10-15"},  # +1
			{"task": "TASK-2", "modified": BASE, "start": "2026-10-21", "end": "2026-10-22"},  # +4
		]
		result = _actions().move_many(json.dumps(moves), dependents=1)
		moved = {m["task"]: m for m in result["moved"]}
		self.assertEqual(moved["TASK-3"]["to"], "2026-10-23")  # Mon 19 + 4 working days, with B
		self.assertTrue(moved["TASK-3"]["dependent"])


class TestThePencilFlagIsPutBackToo(unittest.TestCase):
	"""Finding 6."""

	def test_planned_carries_the_pencil(self):
		doc = h._TaskDoc({"exp_start_date": "2026-10-12", "custom_tentative": 1})
		self.assertEqual(h.api._planned(doc)["custom_tentative"], 1)


class TestHolidaysAndTheCap(_P4):
	"""Note 7: the company's holidays are no working days, and the tasks past the cap are named."""

	def test_the_working_day_maths_skips_holidays(self):
		api = h.api
		holiday = {D(2026, 10, 20)}  # a Tuesday
		self.assertEqual(api.add_working_days(D(2026, 10, 19), 1, holiday), D(2026, 10, 21))
		self.assertEqual(api.add_working_days(D(2026, 10, 21), -1, holiday), D(2026, 10, 19))
		self.assertEqual(api.working_days_between(D(2026, 10, 16), D(2026, 10, 21), holiday), 2)
		self.assertEqual(
			api.dependent_days(("2026-10-16", "2026-10-16"), ("2026-10-21", "2026-10-21"), holiday), 2
		)

	def test_a_shift_steps_over_the_companys_holiday(self):
		self.frappe.values[("Company", "Sapphire Fountains", "default_holiday_list")] = "HL-2026"
		self.frappe.tables["Holiday"] = [
			{
				"parent": "HL-2026",
				"parenttype": "Holiday List",
				"holiday_date": "2026-10-20",
				"weekly_off": 0,
			},
			{
				"parent": "HL-2026",
				"parenttype": "Holiday List",
				"holiday_date": "2026-10-17",
				"weekly_off": 1,
			},
		]
		self._chain()
		result = self.api.save_task("TASK-1", BASE, start="2026-10-14", dependents=1)  # +2
		moved = {m["task"]: (m["to"], m["to_end"]) for m in result["shifted"]}
		# Thu-Fri 15-16 + 2 working days, Tuesday the 20th a holiday: Mon 19 - Wed 21.
		self.assertEqual(moved["TASK-2"], ("2026-10-19", "2026-10-21"))
		self.assertEqual(moved["TASK-3"], ("2026-10-22", "2026-10-22"))

	def test_the_tasks_past_the_cap_are_named(self):
		self._chain()
		with mock.patch.object(self.api, "SUCCESSOR_LIMIT", 1):
			result = self.api.save_task("TASK-1", BASE, start="2026-10-14", dependents=1)
		self.assertEqual([m["task"] for m in result["shifted"]], ["TASK-2"])
		((cut,),) = [result["shift_skipped"]]
		self.assertEqual(cut["task"], "TASK-3")
		self.assertIn("Too many tasks follow", cut["reason"])


# ====================================================================== the drafts strip


class TestStaleDraftsCount(_P4):
	def test_an_out_of_date_draft_still_counts_on_its_project(self):
		h._task("TASK-4", project="PRJ-2")
		self.frappe.tables["Task"] = [{"name": "TASK-4", "project": "PRJ-2"}]
		h._draft_row("TASK-4", {"start": "2026-10-20"}, base="2026-09-30 00:00:00")  # changed since
		overlay = self.api._draft_overlay()
		self.assertEqual([entry["task"] for entry in overlay["stale"]], ["TASK-4"])
		self.assertEqual(overlay["projects"], {"PRJ-2": {"count": 1, "shared": 0}})


class TestDraftsPerProject(unittest.TestCase):
	def test_rows_are_counted_per_project_and_shared_ones_named(self):
		api = h.api
		overlay = {"projects": {}}
		api._count_projects(
			overlay,
			[
				("PRJ-1", {"owner": "nik@example.com"}),
				("PRJ-1", {"owner": "clegg@example.com", "shared": True}),
				("PRJ-2", {"owner": "nik@example.com"}),
				(None, {"owner": "nik@example.com"}),
			],
		)
		self.assertEqual(
			overlay["projects"], {"PRJ-1": {"count": 2, "shared": 1}, "PRJ-2": {"count": 1, "shared": 0}}
		)
		self.assertEqual(api._shared_by({"owner": "clegg@example.com", "shared": True}), "clegg@example.com")
		self.assertIsNone(api._shared_by({"owner": "nik@example.com"}))

	def test_a_shared_card_says_whose_and_the_drafts_carry_the_counts(self):
		api = h.api
		result = {
			"tasks": [{"name": "TASK-1", "start": "2026-10-12", "end": "2026-10-12"}],
			"unscheduled": [],
			"needs_crew": [],
		}
		overlay = {
			"drafted": {"TASK-1": {"credentials": [], "by": "clegg@example.com"}},
			"stale": [],
			"phases": [],
			"projects": {"PRJ-2": {"count": 1, "shared": 1}},
		}
		out = api._with_drafts(result, overlay, D(2026, 10, 11), D(2026, 10, 17))
		self.assertEqual(out["tasks"][0]["drafted_by"], "clegg@example.com")
		self.assertEqual(out["drafts"]["projects"], {"PRJ-2": {"count": 1, "shared": 1}})


# ====================================================================== publishing the active jobs (patch)


class TestPublishActiveJobSchedules(unittest.TestCase):
	"""``patches/publish_active_job_schedules`` (Nik, 2026-10-21: "publish the 47 active Draft jobs
	before P4 merges"): the planner's own customer-job rule, one raw UPDATE, never un-publishes,
	safe twice, never raises."""

	PROJECTS = {
		"PRJ-SVC": {"status": "Active", "schedule": "Draft", "project_type": "Service", "stream": 0},
		"PRJ-BLANK": {"status": "Active", "schedule": "", "project_type": "Design", "stream": 0},
		"PRJ-STREAM": {"status": "Active", "schedule": "Draft", "project_type": "", "stream": 1},
		"PRJ-RENT": {"status": "Active", "schedule": "Draft", "project_type": "Rent", "stream": 0},
		"PRJ-INTERNAL": {"status": "Active", "schedule": "Draft", "project_type": "Internal", "stream": 0},
		"PRJ-DONE": {"status": "Completed", "schedule": "Draft", "project_type": "Build", "stream": 0},
		"PRJ-GONE": {"status": "Cancelled", "schedule": "Draft", "project_type": "Service", "stream": 0},
		"PRJ-PUB": {"status": "Active", "schedule": "Published", "project_type": "Service", "stream": 0},
	}

	def setUp(self):
		h._reset()
		self.frappe = h.frappe
		self.rows = copy.deepcopy(self.PROJECTS)
		self.queries, self.printed, self.fail = [], [], None
		self.frappe.db.has_column = lambda doctype, column: True
		self.frappe.db._cursor = types.SimpleNamespace(rowcount=0)

		def sql(query, values=None, as_dict=False, **kwargs):
			self.queries.append(" ".join(query.split()))
			if self.fail and self.fail in query:
				raise RuntimeError("database is down")
			if "SELECT p.name" in query:
				# The query's own WHERE (Active, not Published); the job rule is is_planner_job's.
				return [
					h._Doc(name=name, project_type=row["project_type"], planner_stream=row["stream"])
					for name, row in sorted(self.rows.items())
					if row["status"] == values["active"] and row["schedule"] != values["published"]
				]
			if "UPDATE `tabProject`" in query:
				changed = 0
				for name in values["names"]:
					row = self.rows[name]
					if row["status"] == values["active"] and row["schedule"] != values["published"]:
						row["schedule"] = values["published"]
						changed += 1
				self.frappe.db._cursor.rowcount = changed
			return []

		self.frappe.db.sql = sql
		sys.modules.pop("erpnext_enhancements.patches.publish_active_job_schedules", None)
		self.patch = importlib.import_module("erpnext_enhancements.patches.publish_active_job_schedules")
		self.print = mock.patch("builtins.print", lambda *a, **k: self.printed.append(" ".join(map(str, a))))
		self.print.start()

	def tearDown(self):
		self.print.stop()

	def _schedules(self):
		return {name: row["schedule"] for name, row in self.rows.items()}

	def test_the_active_customer_jobs_are_published_and_nothing_else(self):
		self.patch.execute()
		self.assertEqual(
			self._schedules(),
			{
				"PRJ-SVC": "Published",
				"PRJ-BLANK": "Published",  # blank reads Draft
				"PRJ-STREAM": "Published",  # a qualifying value stream, as the planner counts it
				"PRJ-RENT": "Published",  # Rent is a planner type
				"PRJ-INTERNAL": "Draft",  # not a customer job: the planner never shows it
				"PRJ-DONE": "Draft",  # Completed: untouched
				"PRJ-GONE": "Draft",  # Cancelled: untouched
				"PRJ-PUB": "Published",
			},
		)
		self.assertIn(
			"4 active customer job(s) on Draft, 4 set Published: PRJ-BLANK, PRJ-RENT", self.printed[-1]
		)

	def test_the_rule_is_the_planners_and_the_write_is_one_raw_update(self):
		self.patch.execute()
		select = next(q for q in self.queries if "SELECT p.name" in q)
		type_expr, stream = h.engine.planner_job_sql("p")
		self.assertIn(" ".join(type_expr.split()), select)
		self.assertIn(" ".join(stream.split()), select)
		update = next(q for q in self.queries if q.startswith("UPDATE"))
		self.assertIn(
			"WHERE name IN %(names)s AND status = %(active)s AND IFNULL(custom_schedule_status, '') <> %(published)s",
			update,
		)
		self.assertNotIn("custom_schedule_published_on", update)
		self.assertNotIn("modified", update)
		self.assertEqual(self.frappe.saved, [])  # no Project or Task saved

	def test_it_is_safe_twice_and_never_unpublishes(self):
		self.patch.execute()
		first = self._schedules()
		self.patch.execute()
		self.assertEqual(self._schedules(), first)
		self.assertIn("0 active customer job(s) on Draft, 0 set Published.", self.printed[-1])

	def test_it_never_raises(self):
		self.fail = "SELECT"
		self.patch.execute()
		self.assertEqual(self.frappe.errors[-1][1]["title"], "Planner: active jobs not published")
		self.fail = "UPDATE"
		self.patch.execute()
		self.assertEqual(len(self.frappe.errors), 2)
		self.assertEqual(self._schedules(), {name: row["schedule"] for name, row in self.PROJECTS.items()})
		self.frappe.db.has_column = lambda doctype, column: False
		self.patch.execute()
		self.assertIn("is missing; nothing published", self.printed[-1])

	def test_it_runs_after_p3s_patch_and_is_documented(self):
		lines = (APP / "patches.txt").read_text(encoding="utf-8").splitlines()
		ours = lines.index("erpnext_enhancements.patches.publish_active_job_schedules")
		self.assertGreater(ours, lines.index("erpnext_enhancements.patches.mark_project_schedule_states"))
		self.assertEqual(lines.count("erpnext_enhancements.patches.publish_active_job_schedules"), 1)
		self.assertIn(
			"`publish_active_job_schedules`", (APP / "patches/README.md").read_text(encoding="utf-8")
		)


# ====================================================================== wiring


# ====================================================================== v1.740.0: Split, Copy week, Overdue tray


class TestTheOverdueTrayReschedule(_P4):
	"""bulk_update's ``reschedule`` drafts a Draft schedule's task (or any, under the hold) through
	move_many's draft path, with the followers that would otherwise start by its new end; a published
	one saves exactly as before."""

	def setUp(self):
		super().setUp()
		_count_drafts(self.frappe)

	def _on(self, project, *names):
		for name in names:
			self.frappe.docs[("Task", name)]["project"] = project

	def test_a_draft_projects_task_is_drafted_with_its_followers(self):
		self._chain()
		self._on("PRJ-2", "TASK-1", "TASK-2", "TASK-3")
		result = self.api.bulk_update(json.dumps(["TASK-1"]), "reschedule", "2026-10-14", draft="auto")

		self.assertEqual(self.frappe.saved, [])  # nothing reaches a Task
		((updated,),) = [result["updated"]]
		self.assertEqual(
			(updated["task"], updated["drafted"], updated["start"]), ("TASK-1", True, "2026-10-14")
		)
		drafts = {row["task"]: json.loads(row["payload"]) for row in self._drafts()}
		self.assertEqual(set(drafts), {"TASK-1", "TASK-2", "TASK-3"})
		# The followers move by the working days TASK-1's end moved (Wed to Fri: two), as move_many does.
		self.assertEqual((drafts["TASK-2"]["start"], drafts["TASK-2"]["end"]), ("2026-10-19", "2026-10-20"))
		self.assertEqual([m["task"] for m in result["shifted"]], ["TASK-2", "TASK-3"])
		self.assertTrue(all(m["dependent"] and m["drafted"] for m in result["shifted"]))
		self.assertEqual(result["failed"], [])

	def test_no_flag_or_draft_0_cannot_write_live_into_a_draft_schedule(self):
		# The AI connector and an old page send no flag; draft=0 is no way round it either.
		for draft in (None, 0, "0"):
			with self.subTest(draft=draft):
				self.frappe.tables["Planner Draft Change"] = []
				h._task("TASK-8", crew=("RES-2",), project="PRJ-2")
				kwargs = {} if draft is None else {"draft": draft}
				result = self.api.bulk_update(json.dumps(["TASK-8"]), "reschedule", "2026-10-19", **kwargs)
				self.assertTrue(result["updated"][0]["drafted"])
				self.assertEqual(self.frappe.saved, [])
				self.assertEqual([row["task"] for row in self._drafts()], ["TASK-8"])

	def test_a_published_projects_task_is_saved_as_before_without_its_followers(self):
		self._chain()
		result = self.api.bulk_update(json.dumps(["TASK-1"]), "reschedule", "2026-10-14", draft="auto")
		self.assertEqual(self._saves(), ["TASK-1"])
		self.assertEqual(self._drafts(), [])
		self.assertNotIn("drafted", result["updated"][0])
		self.assertNotIn("shifted", result)
		self.assertEqual(self._saved("TASK-1")["exp_end_date"], "2026-10-16 17:00:00")

	def test_the_hold_drafts_a_published_task_and_every_follower(self):
		self._chain()
		self._on("PRJ-2", "TASK-3")  # a follower on a Draft schedule is drafted too under the hold
		result = self.api.bulk_update(json.dumps(["TASK-1"]), "reschedule", "2026-10-14", draft=1)
		self.assertEqual(self.frappe.saved, [])
		self.assertEqual([m["task"] for m in result["shifted"]], ["TASK-2", "TASK-3"])
		self.assertEqual({row["task"] for row in self._drafts()}, {"TASK-1", "TASK-2", "TASK-3"})

	def test_a_draft_never_reaches_a_follower_on_a_published_schedule(self):
		self._chain()
		self._on("PRJ-2", "TASK-1", "TASK-2")
		result = self.api.bulk_update(json.dumps(["TASK-1"]), "reschedule", "2026-10-14", draft="auto")
		self.assertEqual([m["task"] for m in result["shifted"]], ["TASK-2"])
		self.assertEqual([skip["task"] for skip in result["shift_skipped"]], ["TASK-3"])
		self.assertEqual(self.frappe.saved, [])

	def test_one_batch_saves_the_published_and_drafts_the_draft(self):
		h._task("TASK-1", crew=("RES-1",))
		h._task("TASK-8", crew=("RES-2",), project="PRJ-2")
		result = self.api.bulk_update(
			json.dumps(["TASK-1", "TASK-8"]), "reschedule", "2026-10-19", draft="auto"
		)
		updated = {u["task"]: u for u in result["updated"]}
		self.assertNotIn("drafted", updated["TASK-1"])
		self.assertTrue(updated["TASK-8"]["drafted"])
		self.assertEqual(self._saves(), ["TASK-1"])
		self.assertEqual([row["task"] for row in self._drafts()], ["TASK-8"])
		# Only the saved one is checked for a reason: the drafted one is not in the batch check.
		self.assertEqual([name for name, _span in self.batches[0]], ["TASK-1"])

	def test_a_live_conflict_without_a_reason_drafts_nothing_either(self):
		h._task("TASK-1", crew=("RES-1",))
		h._task("TASK-8", crew=("RES-2",), project="PRJ-2")
		self.batch_hook = lambda changes: {"Austin Healey": ["Over on Mon"]}
		result = self.api.bulk_update(
			json.dumps(["TASK-1", "TASK-8"]), "reschedule", "2026-10-19", draft="auto"
		)
		self.assertTrue(result["needs_reason"])
		self.assertEqual((self.frappe.saved, self._drafts()), ([], []))

	def test_a_drafted_task_that_is_refused_is_reported_and_the_rest_carry_on(self):
		h._task("TASK-8", crew=("RES-2",), project="PRJ-2", custom_rental_booking="RB-1")
		h._task("TASK-9", crew=("RES-1",), project="PRJ-2")
		result = self.api.bulk_update(json.dumps(["TASK-8", "TASK-9"]), "reschedule", "2026-10-19")
		self.assertEqual([u["task"] for u in result["updated"]], ["TASK-9"])
		((failed,),) = [result["failed"]]
		self.assertEqual(failed["task"], "TASK-8")
		self.assertIn("rental crew task", failed["error"])
		self.assertIn(("rollback", "planner_bulk_draft_0"), self.frappe.db_calls)

	def test_complete_and_cancel_stay_live_on_a_draft_schedule_and_under_the_hold(self):
		# A status change is not a schedule edit (no draft row holds a status): waiting on Nik's call.
		h._task("TASK-8", crew=("RES-2",), project="PRJ-2")
		for draft in ("auto", 1):
			result = self.api.bulk_update(json.dumps(["TASK-8"]), "complete", draft=draft)
			self.assertEqual(result["updated"][0]["status"], "Completed")
			self.assertNotIn("drafted", result["updated"][0])
		self.assertEqual(self._saved("TASK-8")["status"], "Completed")
		self.assertEqual(self._drafts(), [])

	def test_the_tray_shows_where_a_drafted_task_goes(self):
		h._draft_row("TASK-1", {"start": "2026-10-20", "end": "2026-10-21"})
		h._draft_row("TASK-2", {"crew_size": 2})  # a draft that does not move it
		h._draft_row("TASK-3", {"start": "2026-10-22", "end": "2026-10-22"})  # changed since: stale
		h._draft_row("TASK-4", {"start": "2026-10-23", "end": "2026-10-23"})  # not on the tray
		modified = {"TASK-1": BASE, "TASK-2": BASE, "TASK-3": "2026-10-08 12:00:00"}
		self.assertEqual(self.api._overdue_drafts(modified), {"TASK-1": "2026-10-20"})

		def broken(*args, **kwargs):
			raise RuntimeError("no table yet")

		with mock.patch.object(self.frappe, "get_all", broken):
			self.assertEqual(self.api._overdue_drafts(modified), {})  # never fails the tray

	def test_a_task_pushed_by_a_live_save_in_the_same_batch_is_still_drafted(self):
		# TASK-8 (Draft schedule) waits on TASK-1 (published): saving TASK-1 has ERPNext push TASK-8,
		# which bumps its modified after bulk_update loaded it.
		h._task("TASK-1", crew=("RES-1",))
		h._task("TASK-8", start="2026-10-15", end="2026-10-15", crew=("RES-2",), project="PRJ-2")
		real_save = h._TaskDoc.save

		def save(doc):
			real_save(doc)
			if doc.get("name") == "TASK-1":
				self.frappe.docs[("Task", "TASK-8")]["modified"] = PUSHED_AT

		with mock.patch.object(h._TaskDoc, "save", save):
			result = self.api.bulk_update(json.dumps(["TASK-1", "TASK-8"]), "reschedule", "2026-10-26")
		self.assertEqual(result["failed"], [])
		self.assertEqual(
			{u["task"]: bool(u.get("drafted")) for u in result["updated"]}, {"TASK-1": False, "TASK-8": True}
		)


def _count_drafts(frappe):
	"""``frappe.db.count`` over the stub's Planner Draft Change rows (require_change_room)."""
	frappe.db.count = lambda doctype, filters=None: sum(
		1 for row in frappe.tables.get(doctype, []) if h._matches(row, filters)
	)


class _OverdueFollowers(_P4):
	"""An overdue TASK-A on a Draft schedule, and helpers to give it followers and reschedule it."""

	def setUp(self):
		super().setUp()
		_count_drafts(self.frappe)
		# A ran Wed 30 Sep - Thu 1 Oct and is overdue; rescheduled to Mon 26 Oct it runs Mon-Tue.
		h._task("TASK-A", start="2026-09-30", end="2026-10-01", crew=("RES-1",), project="PRJ-2")

	def _follower(self, name, start, end, on="TASK-A"):
		h._task(name, start=start, end=end, crew=("RES-2",), project="PRJ-2")
		self._depends(name, on)

	def _reschedule(self, **kwargs):
		return self.api.bulk_update(json.dumps(["TASK-A"]), "reschedule", "2026-10-26", **kwargs)

	def _drafted(self):
		out = {}
		for row in self._drafts():
			payload = json.loads(row["payload"])
			out[row["task"]] = (payload.get("start"), payload.get("end"))
		return out


class TestTheOverdueFollowerRule(_OverdueFollowers):
	"""Nik, 2026-10-23: a drafted overdue Reschedule moves a follower only when it would otherwise
	start on or before the task's new end, to the next working day after it, keeping its length in
	working days; the rest stay where they are. (A drag keeps the gap instead, D10.)"""

	def test_a_follower_already_clear_stays_where_it_is(self):
		self._follower("TASK-B", "2026-10-29", "2026-10-30")  # after A's new end (Tue 27)
		result = self._reschedule()
		self.assertEqual(self._drafted(), {"TASK-A": ("2026-10-26", "2026-10-27")})
		# Not moved, and not "skipped" either: nothing was wrong with it. (By how late A was, as a drag
		# moves followers, B would have gone to late November.)
		self.assertEqual((result["shifted"], result["shift_skipped"]), ([], []))

	def test_an_overlapping_follower_moves_to_the_working_day_after_across_a_holiday(self):
		self.frappe.local.planner_shift_holidays = frozenset({D(2026, 10, 28)})  # Wednesday
		# Tue-Thu with the Wednesday off: two working days, starting on A's new end, so in its way.
		self._follower("TASK-B", "2026-10-27", "2026-10-29")
		result = self._reschedule()
		# The next working day after Tue 27 is Thu 29 (Wed 28 is a holiday); two working days: Thu-Fri.
		self.assertEqual(self._drafted()["TASK-B"], ("2026-10-29", "2026-10-30"))
		self.assertEqual(
			[(m["task"], m["to"], m["to_end"]) for m in result["shifted"]],
			[("TASK-B", "2026-10-29", "2026-10-30")],
		)

	def test_the_rule_cascades_and_stops_at_a_follower_that_is_clear(self):
		self._follower("TASK-B", "2026-10-26", "2026-10-26")  # in A's way: to Wed 28
		self._follower("TASK-C", "2026-10-30", "2026-10-30", on="TASK-B")  # clear of B's new end
		self._follower("TASK-D", "2026-10-30", "2026-10-30", on="TASK-C")  # follows only through C
		result = self._reschedule()
		self.assertEqual(set(self._drafted()), {"TASK-A", "TASK-B"})
		self.assertEqual(self._drafted()["TASK-B"], ("2026-10-28", "2026-10-28"))
		self.assertEqual([m["task"] for m in result["shifted"]], ["TASK-B"])

	def test_a_follower_pushed_in_turn_moves_after_its_own_predecessor(self):
		self._follower("TASK-B", "2026-10-27", "2026-10-28")  # to Wed 28 - Thu 29
		self._follower("TASK-C", "2026-10-29", "2026-10-29", on="TASK-B")  # now in B's way: to Fri 30
		self._reschedule()
		self.assertEqual(self._drafted()["TASK-B"], ("2026-10-28", "2026-10-29"))
		self.assertEqual(self._drafted()["TASK-C"], ("2026-10-30", "2026-10-30"))

	def _diamond(self, f_row_first):
		"""B waits on A; F waits on A and on B. A's new end is Tue 27: B (Tue-Wed) is in its way and
		goes Wed-Thu, which puts F (Thu 29) in B's way. ``f_row_first``: F's row on A is the first
		one the database returns, so the walk meets F before B has moved."""
		h._task("TASK-B", start="2026-10-27", end="2026-10-28", crew=("RES-2",), project="PRJ-2")
		h._task("TASK-F", start="2026-10-29", end="2026-10-29", crew=("RES-2",), project="PRJ-2")
		if f_row_first:
			self._depends("TASK-F", "TASK-A")
			self._depends("TASK-B", "TASK-A")
		else:
			self._depends("TASK-B", "TASK-A")
			self._depends("TASK-F", "TASK-A")
		self._depends("TASK-F", "TASK-B")
		return self._reschedule()

	def test_a_follower_of_two_moved_tasks_clears_both_whatever_the_row_order(self):
		for f_row_first in (True, False):
			with self.subTest(f_row_first=f_row_first):
				self.frappe.tables["Planner Draft Change"] = []
				self.frappe.tables["Task Depends On"] = []
				self.frappe.docs[("Task", "TASK-A")]["depends_on"] = []
				result = self._diamond(f_row_first)
				self.assertEqual(self._drafted()["TASK-B"], ("2026-10-28", "2026-10-29"))
				# Clear of B's new end (Thu 29), not only of A's (Tue 27).
				self.assertEqual(self._drafted()["TASK-F"], ("2026-10-30", "2026-10-30"))
				moves = {m["task"]: (m["from"], m["to"]) for m in result["shifted"]}
				self.assertEqual(
					moves, {"TASK-B": ("2026-10-27", "2026-10-28"), "TASK-F": ("2026-10-29", "2026-10-30")}
				)

	def test_a_follower_of_two_selected_tasks_clears_both_in_either_order(self):
		# R1 (TASK-A) ends Tue 27 once rescheduled, R2 (TASK-R) Wed 28; F waits on both.
		for order in (["TASK-A", "TASK-R"], ["TASK-R", "TASK-A"]):
			with self.subTest(order=order):
				self.frappe.tables["Planner Draft Change"] = []
				self.frappe.tables["Task Depends On"] = []
				h._task("TASK-R", start="2026-09-28", end="2026-09-30", crew=("RES-1",), project="PRJ-2")
				self._follower("TASK-F", "2026-10-27", "2026-10-27")
				self._depends("TASK-F", "TASK-R")
				result = self.api.bulk_update(json.dumps(order), "reschedule", "2026-10-26")
				self.assertEqual(self._drafted()["TASK-R"], ("2026-10-26", "2026-10-28"))
				self.assertEqual(self._drafted()["TASK-F"], ("2026-10-29", "2026-10-29"))
				# Once in the answer, from where it was to where it ends up.
				self.assertEqual(
					[(m["task"], m["from"], m["to"]) for m in result["shifted"]],
					[("TASK-F", "2026-10-27", "2026-10-29")],
				)
				self.frappe.docs[("Task", "TASK-F")]["depends_on"] = []

	def test_a_published_follower_in_the_way_is_named_and_left(self):
		h._task("TASK-B", start="2026-10-27", end="2026-10-27", crew=("RES-2",))  # PRJ-1, published
		self._depends("TASK-B", "TASK-A")
		result = self._reschedule()
		self.assertEqual([skip["task"] for skip in result["shift_skipped"]], ["TASK-B"])
		self.assertEqual(set(self._drafted()), {"TASK-A"})

	def test_an_undated_follower_is_still_named(self):
		self._follower("TASK-B", None, None)
		result = self._reschedule()
		self.assertEqual(
			[(skip["task"], skip["reason"]) for skip in result["shift_skipped"]],
			[("TASK-B", "It has no dates.")],
		)

	def test_a_published_follower_already_clear_is_not_named(self):
		h._task("TASK-B", start="2026-11-02", end="2026-11-02", crew=("RES-2",))  # PRJ-1, clear
		self._depends("TASK-B", "TASK-A")
		self.assertEqual(self._reschedule()["shift_skipped"], [])

	def test_the_followers_own_draft_is_what_is_compared(self):
		# B is stored clear (Nov 2) but drafted into A's way (Tue 27): the draft is what moves.
		self._follower("TASK-B", "2026-11-02", "2026-11-02")
		h._draft_row("TASK-B", {"start": "2026-10-27", "end": "2026-10-27"})
		self._reschedule()
		self.assertEqual(self._drafted()["TASK-B"], ("2026-10-28", "2026-10-28"))

	def test_move_many_still_keeps_the_gap(self):
		self._follower("TASK-B", "2026-10-29", "2026-10-30")
		moves = [{"task": "TASK-A", "modified": BASE, "start": "2026-10-26", "end": "2026-10-27"}]
		result = _actions().move_many(json.dumps(moves), draft="auto", dependents=1)
		# D10: B moves by the working days A's end moved, clear or not.
		self.assertEqual([m["task"] for m in result["moved"] if m.get("dependent")], ["TASK-B"])

	def test_the_hold_on_a_published_task_uses_the_same_rule(self):
		h._task("TASK-P", start="2026-09-30", end="2026-10-01", crew=("RES-1",))  # PRJ-1, published
		h._task("TASK-Q", start="2026-10-29", end="2026-10-29", crew=("RES-2",))  # clear
		self._depends("TASK-Q", "TASK-P")
		result = self.api.bulk_update(json.dumps(["TASK-P"]), "reschedule", "2026-10-26", draft=1)
		self.assertEqual(self.frappe.saved, [])
		self.assertEqual((result["shifted"], set(self._drafted())), ([], {"TASK-P"}))


class TestADraftedRescheduleThenPublish(_OverdueFollowers):
	"""Publish applies a drafted Reschedule and the follower drafted with it on their drafted days."""

	def setUp(self):
		super().setUp()
		self.patches.append(
			mock.patch.object(
				h.engine,
				"_compute",
				lambda *a, **k: {
					"resources": [{"name": p["name"], "label": p["resource_name"]} for p in h.PEOPLE],
					"days": {},
				},
			)
		)
		self.patches[-1].start()

	def test_publish_saves_both_on_their_drafted_days(self):
		self._follower("TASK-B", "2026-10-27", "2026-10-28")
		self._reschedule()
		result = self.api.publish_drafts(project="PRJ-2")
		self.assertEqual(result["failed"], [])
		self.assertEqual(sorted(p["task"] for p in result["published"]), ["TASK-A", "TASK-B"])
		a, b = self._stored("TASK-A"), self._stored("TASK-B")
		self.assertEqual((a["exp_start_date"][:10], a["exp_end_date"][:10]), ("2026-10-26", "2026-10-27"))
		self.assertEqual((b["exp_start_date"][:10], b["exp_end_date"][:10]), ("2026-10-28", "2026-10-29"))


class TestTheDraftRoom(_P4):
	"""One tray action can draft 100 tasks and their followers, and the planner reads only the oldest
	DRAFT_LIMIT rows: past it a draft would drop out of the overlay and of Publish without a word."""

	def setUp(self):
		super().setUp()
		_count_drafts(self.frappe)
		h._task("TASK-1", crew=("RES-1",))  # published
		h._task("TASK-8", crew=("RES-2",), project="PRJ-2")
		h._task("TASK-9", crew=("RES-2",), project="PRJ-2")

	def test_no_room_for_the_drafted_ones_refuses_before_anything_is_saved(self):
		h._draft_row("TASK-X", {"start": "2026-10-20", "end": "2026-10-20"})
		with mock.patch.object(self.api, "DRAFT_LIMIT", 2):
			with self.assertRaises(h._Throw) as caught:
				self.api.bulk_update(json.dumps(["TASK-1", "TASK-8", "TASK-9"]), "reschedule", "2026-10-19")
		self.assertIn("too many unpublished changes", str(caught.exception))
		self.assertEqual(self.frappe.saved, [])  # the published one was not saved either

	def test_followers_past_the_limit_undo_that_task_and_the_rest_carry_on(self):
		# TASK-9 starts inside TASK-8's new days, so it would be drafted along: two rows, room for one.
		self.frappe.docs[("Task", "TASK-9")].update(exp_start_date="2026-10-20", exp_end_date="2026-10-20")
		self._depends("TASK-9", "TASK-8")
		with mock.patch.object(self.api, "DRAFT_LIMIT", 1):
			result = self.api.bulk_update(json.dumps(["TASK-8"]), "reschedule", "2026-10-19")
		((failed,),) = [result["failed"]]
		self.assertEqual(failed["task"], "TASK-8")
		self.assertIn("too many unpublished changes", failed["error"])
		self.assertIn(("rollback", "planner_bulk_draft_0"), self.frappe.db_calls)

	def test_re_drafting_an_already_drafted_task_adds_no_row_and_is_not_refused(self):
		# At the cap, with that cap's one row being TASK-8's own: rescheduling it again merges into it.
		h._draft_row("TASK-8", {"start": "2026-10-20", "end": "2026-10-22"})
		with mock.patch.object(self.api, "DRAFT_LIMIT", 1):
			result = self.api.bulk_update(json.dumps(["TASK-8"]), "reschedule", "2026-10-26")
			self.assertEqual(result["failed"], [])
			self.assertTrue(result["updated"][0]["drafted"])
			# A task with no row of the caller's would add one: refused at the cap.
			with self.assertRaises(h._Throw):
				self.api.bulk_update(json.dumps(["TASK-9"]), "reschedule", "2026-10-26")

	def test_room_is_counted_on_the_callers_task_rows_only(self):
		h._draft_row("TASK-X", {"start": "2026-10-20"}, owner="clegg@example.com")
		h._draft_row("TASK-Y", {"start": "2026-10-20"}, status="Published")
		with mock.patch.object(self.api, "DRAFT_LIMIT", 1):
			result = self.api.bulk_update(json.dumps(["TASK-8"]), "reschedule", "2026-10-26")
		self.assertTrue(result["updated"][0]["drafted"])


class _NewTask(h._TaskDoc):
	"""A new Task (Copy week's live copy or a spec's): insert() records it, as the framework would."""

	def insert(self):
		self.frappe.counter += 1
		self["name"] = self.get("name") or f"TASK-NEW-{self.frappe.counter}"
		self["modified"] = BASE
		self.frappe.sequence.append(f"insert:Task:{self['name']}")
		return self


class TestCopyWeekOnADraftSchedule(_P4):
	"""copy_week keeps a copy into a Draft schedule (or any, under the hold) as a New task draft, the
	way a drafted duplicate is kept; a copy into a published one is inserted exactly as before."""

	def setUp(self):
		super().setUp()
		_NewTask.frappe = self.frappe
		real_get_doc = self.frappe.get_doc

		def get_doc(doctype, name=None):
			if isinstance(doctype, dict) and doctype.get("doctype") == "Task":
				return _NewTask(doctype)
			return real_get_doc(doctype, name)

		self.patches += [
			mock.patch.object(self.frappe, "get_doc", get_doc),
			# Both test projects are customer jobs.
			mock.patch.object(h.engine, "planner_projects", lambda names: set(names)),
		]
		for patch in self.patches[-2:]:
			patch.start()
		h._task("TASK-1", crew=("RES-1",))  # Published, Mon-Wed 12-14 October
		h._task("TASK-8", crew=("RES-2",), project="PRJ-2")  # Draft

	def _copy(self, names, **kwargs):
		return self.api.copy_week("2026-10-12", "2026-10-19", json.dumps(names), **kwargs)

	def _inserted(self):
		return [step.split(":", 2)[2] for step in self.frappe.sequence if step.startswith("insert:Task:")]

	def test_the_dry_run_says_which_copies_will_be_drafts(self):
		self.batch_hook = lambda changes: (
			{"Lisa Park": ["Over on Mon"]}
			if any(state.get("name") == "TASK-8 (copy)" for state, _c in changes)
			else {}
		)
		result = self._copy(["TASK-1", "TASK-8"], draft="auto")
		copies = {c["task"]: c for c in result["copies"]}
		self.assertNotIn("drafted", copies["TASK-1"])
		self.assertTrue(copies["TASK-8"]["drafted"])
		# The drafted copy's overbooking is information, never a reason the live copy must give.
		self.assertEqual(result["conflicts"], {})
		self.assertEqual(result["drafted_conflicts"], {"Lisa Park": ["Over on Mon"]})
		self.assertEqual((self._inserted(), self._drafts()), ([], []))

	def test_a_live_copy_and_a_drafted_one_that_double_book_someone_warn(self):
		both = {"TASK-1 (copy)", "TASK-8 (copy)"}

		def hook(changes):
			names = {state.get("name") for state, _crew in changes}
			out = {"Austin Healey": ["Over on Tue"]} if "TASK-1 (copy)" in names else {}
			if both <= names:
				out["Lisa Park"] = ["In two places on Mon"]
			return out

		self.batch_hook = hook
		result = self._copy(["TASK-1", "TASK-8"], draft="auto")
		# The live copy's own conflict asks for a reason, as before; what the two make together is the
		# drafted copy's information, and the live line is not said twice.
		self.assertEqual(result["conflicts"], {"Austin Healey": ["Over on Tue"]})
		self.assertEqual(result["drafted_conflicts"], {"Lisa Park": ["In two places on Mon"]})

	def test_a_drafted_copy_keeps_what_a_copy_keeps(self):
		self.frappe.docs[("Task", "TASK-8")].update(
			expected_time=9,
			custom_crew_size=3,
			custom_locationaddress_of_task="ADDR-7",
			custom_required_credentials=[h._Doc(credential_type="Forklift")],
		)
		self._copy(["TASK-8"], dry_run=0, draft="auto")
		((row,),) = [[r for r in self.frappe.tables["Planner Draft Change"] if r.get("kind") == "New task"]]
		spec = json.loads(row["payload"])
		values = spec["values"]
		self.assertEqual(
			(values["expected_time"], values["custom_crew_size"], values["custom_locationaddress_of_task"]),
			(9.0, 3, "ADDR-7"),
		)
		self.assertEqual(spec["credentials"], ["Forklift"])
		self.assertIn("Copied from TASK-8 on the Project Planner", values["description"])

	def test_a_draft_schedules_copy_is_a_new_task_draft_and_no_task(self):
		result = self._copy(["TASK-8"], dry_run=0, draft="auto")
		((created,),) = [result["created"]]
		self.assertTrue(created["task"].startswith("draft-new:"))
		self.assertEqual((created["source"], created["drafted"]), ("TASK-8", True))
		self.assertEqual((created["start"], created["end"]), ("2026-10-19", "2026-10-21"))
		self.assertEqual(self._inserted(), [])  # the calendar sync reads Tasks only
		rows = [r for r in self.frappe.tables["Planner Draft Change"] if r.get("kind") == "New task"]
		self.assertEqual([(r["project"], r["subject"]) for r in rows], [("PRJ-2", "Subject TASK-8")])
		spec = json.loads(rows[0]["payload"])
		self.assertEqual((spec["op"], spec["source"]), ("copy_week", "TASK-8"))
		self.assertEqual([member["resource"] for member in spec["crew"]], ["RES-2"])

	def test_no_flag_or_draft_0_cannot_insert_into_a_draft_schedule(self):
		for draft in (None, 0):
			with self.subTest(draft=draft):
				kwargs = {} if draft is None else {"draft": draft}
				result = self._copy(["TASK-8"], dry_run=0, **kwargs)
				self.assertTrue(result["created"][0]["drafted"])
		self.assertEqual(self._inserted(), [])

	def test_a_published_projects_copy_is_inserted_as_before(self):
		result = self._copy(["TASK-1"], dry_run=0, draft="auto")
		((created,),) = [result["created"]]
		self.assertNotIn("drafted", created)
		self.assertEqual(self._inserted(), [created["task"]])
		self.assertEqual(self._drafts(), [])
		self.assertNotIn("drafted_conflicts", result)

	def test_the_hold_drafts_every_copy(self):
		result = self._copy(["TASK-1", "TASK-8"], dry_run=0, draft=1)
		self.assertEqual([c["drafted"] for c in result["created"]], [True, True])
		self.assertEqual(self._inserted(), [])

	def test_one_run_inserts_the_published_copy_and_drafts_the_other(self):
		result = self._copy(["TASK-1", "TASK-8"], dry_run=0, draft="auto")
		created = {c["source"]: c for c in result["created"]}
		self.assertEqual(self._inserted(), [created["TASK-1"]["task"]])
		self.assertTrue(created["TASK-8"]["drafted"])

	def test_drafting_copies_needs_room_for_all_of_them_first(self):
		with mock.patch.object(self.api, "DRAFT_LIMIT", 0):
			with self.assertRaises(h._Throw):
				self._copy(["TASK-1", "TASK-8"], dry_run=0, draft="auto")
		self.assertEqual(self._inserted(), [])  # refused before the live copy too


class TestSplitOnADraftSchedule(_P4):
	"""No draft row can hold a split, so a Draft schedule refuses it on the server, whatever the flag."""

	class _PastTheGate(Exception):
		pass

	def setUp(self):
		super().setUp()
		h._task("TASK-1", crew=("RES-1",))
		h._task("TASK-8", crew=("RES-2",), project="PRJ-2")
		self.patches.append(mock.patch.object(_actions(), "_require_create", self._past))
		self.patches[-1].start()

	def _past(self):
		raise self._PastTheGate()

	def test_a_draft_schedule_refuses_it_with_what_to_do(self):
		for draft in (None, 0, "auto"):
			with self.subTest(draft=draft):
				kwargs = {} if draft is None else {"draft": draft}
				with self.assertRaises(h._Throw) as caught:
					_actions().split_task("TASK-8", "2026-10-13", BASE, **kwargs)
				self.assertIn("Publish the project's schedule first", str(caught.exception))
		self.assertEqual((self.frappe.saved, self._drafts()), ([], []))

	def test_a_published_one_goes_on_to_split(self):
		with self.assertRaises(self._PastTheGate):
			_actions().split_task("TASK-1", "2026-10-13", BASE, draft="auto")

	def test_the_hold_still_refuses_with_its_own_reason(self):
		with self.assertRaises(h._Throw) as caught:
			_actions().split_task("TASK-1", "2026-10-13", BASE, draft=1)
		self.assertIn("Turn Draft mode off", str(caught.exception))

	def test_a_drafted_new_task_is_refused_as_before_not_as_a_draft_schedule(self):
		with self.assertRaises(Exception) as caught:
			_actions().split_task("draft-new:PDC-1", "2026-10-13", BASE)
		self.assertNotIn("Publish the project's schedule first", str(caught.exception))

	def test_before_the_columns_exist_nothing_changes(self):
		self.frappe.db.has_column = lambda doctype, column: column not in h.P3_COLUMNS
		with self.assertRaises(self._PastTheGate):
			_actions().split_task("TASK-8", "2026-10-13", BASE)


class TestWiring(unittest.TestCase):
	def _args(self, path, name):
		fn = next(
			node
			for node in ast.parse(path.read_text(encoding="utf-8")).body
			if isinstance(node, ast.FunctionDef) and node.name == name
		)
		names = [a.arg for a in fn.args.args]
		defaults = dict(zip(names[len(names) - len(fn.args.defaults) :], fn.args.defaults, strict=True))
		return names, {key: ast.literal_eval(value) for key, value in defaults.items()}

	def test_dependents_is_unset_by_default_and_comes_before_notify_and_draft(self):
		for path, name in ((API_PATH, "save_task"), (API_PATH, "swap_crew"), (ACTIONS_PATH, "move_many")):
			names, defaults = self._args(path, name)
			self.assertEqual(names[-3:], ["dependents", "notify", "draft"], name)
			self.assertIsNone(defaults["dependents"], name)

	def test_the_overdue_tray_and_copy_week_take_draft_last_and_unset(self):
		# v1.740.0: unset reads as "auto" for these two (_held), so an old caller keeps working.
		for name in ("bulk_update", "copy_week"):
			names, defaults = self._args(API_PATH, name)
			self.assertEqual(names[-1], "draft", name)
			self.assertIsNone(defaults["draft"], name)

	def test_the_maintenance_add_crew_takes_the_answer_too(self):
		path = APP / "api/maintenance_planner.py"
		names, defaults = self._args(path, "add_crew")
		self.assertEqual(names[-2:], ["notify", "draft"])
		self.assertIsNone(defaults["notify"])
		self.assertIn(
			"return result if before is None else _with_visit_notice(doc, notify, before, result)",
			path.read_text(encoding="utf-8"),
		)

	def test_the_suite_has_its_own_ci_step(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_planner_autoshift -v", ci)


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestThePageUnderNode(unittest.TestCase):
	def test_the_harness_passes(self):
		result = subprocess.run(
			[shutil.which("node"), str(HARNESS)],
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=120,
		)
		output = result.stdout + result.stderr
		self.assertEqual(result.returncode, 0, output)
		self.assertIn("checks passed", output)


if __name__ == "__main__":
	unittest.main()
