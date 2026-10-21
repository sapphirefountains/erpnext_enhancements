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
import json
import shutil
import subprocess
import sys
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
		result = self.api.shift_successors("TASK-1", -1, BASE, tasks=json.dumps(["TASK-2"]))
		self.assertEqual([m["task"] for m in result["moved"]], ["TASK-2"])


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


# ====================================================================== wiring


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
