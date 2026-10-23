# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Project Planner T1 (v1.602.0): build out a project's tasks without leaving the planner.

Nik, 2026-10-12: "I definitely want to be able to easily create tasks for projects in the project
planner screen so I don't have to leave it to build out tasks for a project." Three things, and what
this pins, because each can fail quietly:

* **Build out** (``planner_actions.build_out_tasks``) makes many UNDATED tasks in one request. Every
  row is checked before anything is written and a row's mistake is named by its number; new phases
  (group tasks) are made first and shared by every row that typed the same name; a typed name that
  is an open phase of the project already is that phase; a dependency is another row or a task of
  the same project, never a phase or another project's; a loop of rows is refused with the rows
  named (and a row waiting on itself); tasks are made in an order where each follows what it waits
  on. Created as the caller, on a customer job only (and, since v1.603.0, as drafts in draft mode).
* **Its Undo** (``remove_built_out_tasks``) removes the lot or nothing: anything attached since to
  any one of them (time, a file, someone else's comment, a changed field, a dependent that is not
  part of the build-out, a sub-task nobody here made) stops all of it. Never ``force``.
* **The Needs tasks list** is ONE bound query for the Active customer jobs with no task at all, using
  the planners' own customer-job rule.
* **The richer quick add** takes a phase, dependencies, a time slot, several crew, an address, the
  two flags, a description and a crew size, all optional: leave them out and it is what it was.

The pages' pure helpers (the grid's payload, the dependency order, pasting from a spreadsheet) run
under node, and ``build_order`` is checked against the server's.

Bench-free: reuses Phase 6B's fake site (its ``frappe`` stub, ``_Writes`` base class and task
builder), runs the real modules, puts ``sys.modules`` back. The node parts skip when node is not on
PATH.

Run: python -m unittest erpnext_enhancements.tests.test_planner_build_out
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from erpnext_enhancements.tests import test_planner_phase6b as base
from erpnext_enhancements.tests.test_planner_phase6b import (  # unittest looks both up in this module
	setUpModule,
	tearDownModule,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = Path(__file__).resolve().parents[1]
PP_JS = APP / "project_enhancements/page/project_planner/project_planner.js"
CI = REPO_ROOT / ".github/workflows/ci.yml"
API_README = APP / "api/README.md"
PE_README = APP / "project_enhancements/README.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

TUE, WED, FRI = base.TUE, base.WED, base.FRI
SAVED = base.SAVED
MODIFIED = base.MODIFIED
_Doc, _Throw, _PermissionError = base._Doc, base._Throw, base._PermissionError


def _group(name, subject, project="PRJ-1", status="Open", **values):
	return dict(
		{
			"name": name,
			"project": project,
			"subject": subject,
			"status": status,
			"is_group": 1,
			"is_template": 0,
			"exp_end_date": None,
		},
		**values,
	)


def _leaf(name, subject, project="PRJ-1", status="Open", **values):
	return dict(
		{
			"name": name,
			"project": project,
			"subject": subject,
			"status": status,
			"is_group": 0,
			"is_template": 0,
			"exp_end_date": None,
		},
		**values,
	)


class _Site(base._Writes):
	"""The 6B fake site plus a project that already has a phase, a task, and another project's."""

	def setUp(self):
		super().setUp()
		self.frappe = base.frappe
		self.actions = base.actions
		self.frappe.tables["Task"] = [
			_group("TASK-P1", "Pumps"),
			_group("TASK-P2", "Done phase", status="Completed"),
			_group("TASK-OTHER-P", "Elsewhere", project="PRJ-2"),
			_leaf(
				"TASK-A",
				"Dig the basin",
				exp_start_date="2026-10-12 08:00:00",
				exp_end_date="2026-10-16 17:00:00",
			),
			_leaf("TASK-DONE", "Old job", status="Completed"),
			_leaf("TASK-TPL", "Template", is_template=1),
			_leaf("TASK-OTHER", "Other project's", project="PRJ-2"),
		]
		self.frappe.docs[("Address", "ADDR-1")] = _Doc(name="ADDR-1")
		self.frappe.db.delete = lambda doctype, filters=None: self.frappe.events.append(
			("db.delete", doctype, filters)
		)

	def build(self, rows, project="PRJ-1", **kwargs):
		return self.actions.build_out_tasks(project, json.dumps(rows), **kwargs)

	def refused(self, rows, message=None, project="PRJ-1", **kwargs):
		with self.assertRaises(_Throw, msg=str(rows)) as caught:
			self.build(rows, project=project, **kwargs)
		if message:
			self.assertIn(message, str(caught.exception))
		self.assertEqual(self.frappe.inserted, [], "a refused build-out must create nothing")


# ---------------------------------------------------------------------- pure helpers


class TestHelpers(unittest.TestCase):
	def test_as_names_reads_what_frappe_call_sends(self):
		a = base.actions
		self.assertEqual(a.as_names(None), [])
		self.assertEqual(a.as_names(""), [])
		self.assertEqual(a.as_names("null"), [])
		self.assertEqual(a.as_names('["A", " B ", "A", ""]'), ["A", "B"])
		self.assertEqual(a.as_names("A, B,,A"), ["A", "B"])
		self.assertEqual(a.as_names(["x", "x", None]), ["x"])
		self.assertEqual(a.as_names("TASK-1"), ["TASK-1"])
		# Text that only looks like JSON is read as a list of names, which are then checked.
		self.assertEqual(a.as_names("[bad"), ["[bad"])

	def test_clock_and_slot(self):
		a = base.actions
		self.assertIsNone(a.parse_clock(None))
		self.assertIsNone(a.parse_clock("  "))
		self.assertEqual(str(a.parse_clock("9:30")), "09:30:00")
		self.assertEqual(str(a.parse_clock("14:05:30")), "14:05:30")
		for bad in ("25:00", "noon", "9"):
			with self.assertRaises(ValueError, msg=bad):
				a.parse_clock(bad)
		day = base.D(2026, 10, 13)
		self.assertIsNone(a.slot_for(day, None, ""))
		self.assertEqual(
			a.slot_for(day, "08:00", "10:30"), ("2026-10-13 08:00:00", "2026-10-13 10:30:00", 2.5)
		)
		for first, last in (("08:00", None), (None, "09:00"), ("10:00", "10:00"), ("11:00", "09:00")):
			with self.assertRaises(ValueError, msg=str((first, last))):
				a.slot_for(day, first, last)

	def test_build_order_is_stable_and_names_a_loop(self):
		a = base.actions
		self.assertEqual(a.build_order(3, {}), ([0, 1, 2], []))
		# Waiting on the row above leaves the grid as it is.
		self.assertEqual(a.build_order(3, {1: {0}, 2: {1}}), ([0, 1, 2], []))
		# A row that waits on a LATER row goes after it, nothing else moves more than it must.
		self.assertEqual(a.build_order(4, {0: {2}}), ([1, 2, 0, 3], []))
		self.assertEqual(a.build_order(2, {0: {1}, 1: {0}}), (None, [0, 1]))
		self.assertEqual(a.build_order(1, {0: {0}}), (None, [0]))
		# A row that merely waits on a loop is not in it.
		order, cycle = a.build_order(4, {1: {2}, 2: {1}, 3: {1}})
		self.assertIsNone(order)
		self.assertEqual(sorted(cycle), [1, 2])

	def test_deletion_order_puts_the_waiting_task_first(self):
		a = base.actions
		self.assertEqual(a.deletion_order(["A", "B", "C"], {"B": {"A"}, "C": {"B"}}), ["C", "B", "A"])
		self.assertEqual(a.deletion_order(["A", "B"], {}), ["A", "B"])
		self.assertEqual(sorted(a.deletion_order(["A", "B"], {"A": {"B"}, "B": {"A"}})), ["A", "B"])

	def test_the_needs_tasks_query_is_one_bound_statement(self):
		a = base.actions
		sql, values = a.needs_tasks_query(True)
		self.assertNotIn(";", sql)
		for needle in (
			"FROM `tabProject` p",
			"p.status = %(active)s",
			"NOT EXISTS",
			"IFNULL(t.is_group, 0) = 0",
			"IFNULL(t.is_template, 0) = 0",
			"NOT IN %(not_a_task)s",
			"LIMIT %(limit)s",
			"LEFT JOIN `tabEmployee` e ON e.name = p.custom_project_owner",
			"%(planner_types)s",
		):
			self.assertIn(needle, sql)
		self.assertEqual(values["active"], "Active")
		# Only canceled work and templates are "not a task": a project whose tasks are all finished has
		# had tasks, so it is NOT listed (Nik, 2026-10-12), and the query no longer tests finished at all.
		self.assertEqual(tuple(values["not_a_task"]), ("Canceled", "Cancelled", "Template"))
		for finished in ("Completed", "Invoiced"):
			self.assertNotIn(finished, values["not_a_task"])
		self.assertNotIn("finished", values)
		self.assertNotIn("%(finished)s", sql)
		self.assertNotIn("finished_tasks", sql)
		self.assertEqual(values["limit"], a.NEEDS_TASKS_LIMIT)
		self.assertIn("planner_types", values)
		self.assertIn("planner_streams", values)
		# Nothing is formatted in from outside: every %(name)s the SQL uses is bound.
		self.assertEqual(set(re.findall(r"%\((\w+)\)s", sql)) - set(values), set())
		# Before the project-owner custom field exists the column and the join are left out.
		bare, _values = a.needs_tasks_query(False)
		self.assertNotIn("custom_project_owner", bare)
		self.assertIn("NULL AS pm", bare)


# ---------------------------------------------------------------------- richer quick add


class TestRicherQuickAdd(_Site):
	def quick(self, **kwargs):
		kwargs.setdefault("hours", 4)
		return self.actions.quick_add_task("PRJ-1", "Hang the lights", str(TUE), **kwargs)

	def new(self):
		(new,) = self._inserted_tasks()
		return new

	def test_with_none_of_the_new_arguments_it_is_what_it_was(self):
		self.quick(resource="RES-1")
		new = self.new()
		for field in (
			"parent_task",
			"depends_on",
			"custom_start_datetime",
			"custom_end_datetime",
			"custom_crew_size",
			"custom_outdoor",
			"custom_customer_visit",
			"description",
			"custom_locationaddress_of_task",
		):
			self.assertNotIn(field, new, field)
		self.assertEqual([(r["resource"], r["is_lead"]) for r in new["custom_crew"]], [("RES-1", 0)])

	def test_several_crew_are_booked_together_and_the_first_leads(self):
		self.quick(resource="RES-1", crew='["RES-2", "RES-1", "RES-4"]')
		new = self.new()
		self.assertEqual(
			[(r["resource"], r["user"], r["is_lead"]) for r in new["custom_crew"]],
			[("RES-1", "austin@example.com", 1), ("RES-2", "korben@example.com", 0), ("RES-4", None, 0)],
		)
		((preview, crew),) = self.batches[0]
		self.assertEqual([m["resource"] for m in crew], ["RES-1", "RES-2", "RES-4"])
		self.assertEqual(preview["exp_start_date"], "2026-10-13")

	def test_crew_with_no_cell_person_leads_with_the_first_named(self):
		self.quick(crew="RES-2,RES-1")
		self.assertEqual(
			[(r["resource"], r["is_lead"]) for r in self.new()["custom_crew"]], [("RES-2", 1), ("RES-1", 0)]
		)

	def test_any_overbooked_person_asks_for_a_reason_and_creates_nothing(self):
		self.answers = [{"Korben": ["2026-10-13: Over by 2h"]}]
		result = self.quick(resource="RES-1", crew=["RES-2"])
		self.assertTrue(result["needs_reason"])
		self.assertEqual(self.frappe.inserted, [])
		self.answers = [{"Korben": ["2026-10-13: Over by 2h"]}]
		self.quick(resource="RES-1", crew=["RES-2"], reason="Korben swaps shifts")
		self.assertEqual(len(self.frappe.inserted), 1)
		self.assertIn("Reason: Korben swaps shifts", self.frappe.comments[0][2])
		# The two checks saw both people.
		self.assertEqual(
			{m["resource"] for batch in self.batches for _task, crew in batch for m in crew},
			{"RES-1", "RES-2"},
		)

	def test_an_inactive_or_unknown_crew_member_is_refused(self):
		for who in ("RES-3", "RES-99"):
			with self.assertRaises(_Throw, msg=who):
				self.quick(resource="RES-1", crew=[who])
		self.assertEqual(self.frappe.inserted, [])

	def test_a_time_slot_is_the_tasks_one_day_and_its_length_the_estimate(self):
		self.quick(hours=None, resource="RES-1", start_time="08:00", end_time="10:30")
		new = self.new()
		self.assertEqual(
			(new["custom_start_datetime"], new["custom_end_datetime"]),
			("2026-10-13 08:00:00", "2026-10-13 10:30:00"),
		)
		self.assertEqual(
			(new["exp_start_date"], new["exp_end_date"], new["expected_time"]),
			("2026-10-13", "2026-10-13", 2.5),
		)
		((preview, _crew),) = self.batches[0]
		self.assertEqual(preview["custom_start_datetime"], "2026-10-13 08:00:00")

	def test_a_slot_keeps_the_hours_typed_and_never_spreads_over_days(self):
		self.quick(hours=20, start_time="08:00:00", end_time="09:00:00")
		new = self.new()
		self.assertEqual((new["expected_time"], new["exp_end_date"]), (20.0, "2026-10-13"))

	def test_a_half_or_backwards_slot_is_refused(self):
		for first, last in (("08:00", None), (None, "09:00"), ("10:00", "09:00"), ("9am", "10am")):
			with self.assertRaises(_Throw, msg=str((first, last))):
				self.quick(start_time=first, end_time=last)
		self.assertEqual(self.frappe.inserted, [])

	def test_a_phase_of_the_same_project_files_the_task_under_it(self):
		self.quick(parent_task="TASK-P1")
		self.assertEqual(self.new()["parent_task"], "TASK-P1")

	def test_only_an_open_phase_of_this_project_will_do(self):
		for parent, why in (
			("TASK-A", "not a group"),
			("TASK-OTHER-P", "another project's"),
			("TASK-P2", "finished"),
			("TASK-NOPE", "missing"),
		):
			with self.assertRaises(_Throw, msg=why):
				self.quick(parent_task=parent)
		self.assertEqual(self.frappe.inserted, [])

	def test_a_task_cannot_run_past_the_day_its_phase_ends(self):
		self.frappe.tables["Task"].append(
			_group("TASK-P3", "Short phase", exp_end_date="2026-10-12 17:00:00")
		)
		with self.assertRaises(_Throw) as caught:
			self.quick(parent_task="TASK-P3")
		self.assertIn("ends on 2026-10-12", str(caught.exception))

	def test_dependencies_are_added_and_must_be_plain_tasks_of_the_project(self):
		self.quick(depends_on='["TASK-A"]', tentative=1)
		new = self.new()
		self.assertEqual(
			[(r["task"], r["subject"]) for r in new["depends_on"]], [("TASK-A", "Dig the basin")]
		)
		for task in ("TASK-P1", "TASK-OTHER", "TASK-TPL", "TASK-NOPE"):
			with self.assertRaises(_Throw, msg=task):
				self.quick(depends_on=[task])

	def test_starting_before_a_predecessor_ends_asks_for_a_reason(self):
		result = self.quick(depends_on=["TASK-A"])
		self.assertTrue(result["needs_reason"])
		self.assertEqual(list(result["conflicts"]), ["Dependencies"])
		self.assertIn("TASK-A", result["conflicts"]["Dependencies"][0])
		self.assertEqual(self.frappe.inserted, [])
		self.quick(depends_on=["TASK-A"], reason="They overlap on purpose")
		self.assertEqual(len(self.frappe.inserted), 1)
		self.assertIn("Dependencies", self.frappe.comments[0][2])

	def test_the_detail_fields_are_kept_escaped(self):
		self.quick(
			address="ADDR-1",
			outdoor="true",
			customer_visit=1,
			description="Bring <b>ladders</b>\nand water",
			crew_size="3",
		)
		new = self.new()
		self.assertEqual(new["custom_locationaddress_of_task"], "ADDR-1")
		self.assertEqual(
			(new["custom_outdoor"], new["custom_customer_visit"], new["custom_crew_size"]), (1, 1, 3)
		)
		self.assertEqual(new["description"], "<p>Bring &lt;b&gt;ladders&lt;/b&gt;<br>and water</p>")

	def test_a_crew_size_widens_the_days_a_long_task_needs(self):
		self.quick(hours=20, resource="RES-1", crew_size=2)
		self.assertEqual(self.new()["exp_end_date"], "2026-10-14")

	def test_bad_details_are_refused(self):
		for kwargs in (
			{"address": "ADDR-NOPE"},
			{"crew_size": 51},
			{"crew_size": -1},
			{"description": "x" * 4001},
		):
			with self.assertRaises(_Throw, msg=str(kwargs)):
				self.quick(**kwargs)
		self.assertEqual(self.frappe.inserted, [])

	def test_permissions_still_refuse(self):
		self.frappe.perms["create"] = False
		with self.assertRaises(_PermissionError):
			self.quick()
		self.assertEqual(self.frappe.inserted, [])

	def test_the_endpoint_still_takes_every_argument_the_page_sends(self):
		import ast

		tree = ast.parse((APP / "api/planner_actions.py").read_text(encoding="utf-8"))
		node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "quick_add_task")
		params = [a.arg for a in node.args.args]
		self.assertEqual(
			params[:8],
			["project", "subject", "date", "hours", "resource", "tentative", "reason", "draft"],
		)
		self.assertEqual(
			set(params[8:]),
			{
				"parent_task",
				"depends_on",
				"start_time",
				"end_time",
				"crew",
				"address",
				"outdoor",
				"customer_visit",
				"description",
				"crew_size",
			},
		)


# ---------------------------------------------------------------------- build out


class TestBuildOut(_Site):
	def test_many_undated_crewless_tasks_in_one_request(self):
		result = self.build(
			[
				{"subject": "  Set   the pumps ", "hours": "6"},
				{"subject": "Wire them", "hours": 3, "crew_size": "2"},
				{"subject": "Test run"},
			]
		)
		tasks = self._inserted_tasks()
		self.assertEqual([t["subject"] for t in tasks], ["Set the pumps", "Wire them", "Test run"])
		for task in tasks:
			self.assertEqual((task["project"], task["status"]), ("PRJ-1", "Open"))
			for field in (
				"exp_start_date",
				"exp_end_date",
				"custom_start_datetime",
				"custom_crew",
				"parent_task",
			):
				self.assertNotIn(field, task, field)
		self.assertEqual([t["expected_time"] for t in tasks], [6.0, 3.0, 0.0])
		self.assertEqual([t.get("custom_crew_size") for t in tasks], [None, 2, None])
		self.assertEqual(result["count"], 3)
		self.assertEqual(result["phases"], [])
		self.assertEqual(
			[(t["name"], t["row"], t["subject"], t["modified"]) for t in result["tasks"]],
			[
				("TASK-NEW-1", 1, "Set the pumps", SAVED),
				("TASK-NEW-2", 2, "Wire them", SAVED),
				("TASK-NEW-3", 3, "Test run", SAVED),
			],
		)
		self.assertIn(("check_permission", "create", None), self.frappe.events)
		# Nobody and no day was picked: no conflict check ran at all.
		self.assertEqual(self.batches, [])

	def test_new_phases_are_made_first_and_shared(self):
		result = self.build(
			[
				{"subject": "Dig", "phase": "Site prep"},
				{"subject": "Level", "phase": "site PREP"},
				{"subject": "Plumb", "phase": "Plumbing"},
				{"subject": "Loose"},
			]
		)
		inserted = self._inserted_tasks()
		self.assertEqual(
			[bool(t.get("is_group")) for t in inserted], [True, True, False, False, False, False]
		)
		self.assertEqual([t["subject"] for t in inserted[:2]], ["Site prep", "Plumbing"])
		self.assertEqual(
			[(t["subject"], t.get("parent_task")) for t in inserted[2:]],
			[("Dig", "TASK-NEW-1"), ("Level", "TASK-NEW-1"), ("Plumb", "TASK-NEW-2"), ("Loose", None)],
		)
		for phase in inserted[:2]:
			self.assertEqual((phase["project"], phase["status"]), ("PRJ-1", "Open"))
			self.assertNotIn("exp_start_date", phase)
		self.assertEqual(
			[(p["name"], p["subject"]) for p in result["phases"]],
			[("TASK-NEW-1", "Site prep"), ("TASK-NEW-2", "Plumbing")],
		)
		self.assertEqual(
			[t["parent_task"] for t in result["tasks"]], ["TASK-NEW-1", "TASK-NEW-1", "TASK-NEW-2", None]
		)

	def test_a_typed_name_that_is_an_open_phase_is_that_phase(self):
		self.build(
			[
				{"subject": "A", "phase": "pumps"},
				{"subject": "B", "phase": "TASK-P1"},
				{"subject": "C", "parent_task": "TASK-P1", "phase": "Ignored"},
				# A finished phase is not offered a new task: this makes a new one of that name.
				{"subject": "D", "phase": "Done phase"},
			]
		)
		inserted = self._inserted_tasks()
		self.assertEqual([t["subject"] for t in inserted if t.get("is_group")], ["Done phase"])
		leaves = [t for t in inserted if not t.get("is_group")]
		self.assertEqual(
			[t.get("parent_task") for t in leaves], ["TASK-P1", "TASK-P1", "TASK-P1", "TASK-NEW-1"]
		)

	def test_dependencies_on_rows_above_below_and_on_existing_tasks(self):
		result = self.build(
			[
				{"subject": "Install", "depends_on": ["row:3"]},  # waits on a row BELOW it
				{"subject": "Order parts", "depends_on": ["TASK-A"]},
				{"subject": "Receive parts", "depends_on": '["row:2", "TASK-A"]'},
			]
		)
		inserted = self._inserted_tasks()
		# Made in an order where each follows what it waits on; the answer is in the grid's order.
		self.assertEqual([t["subject"] for t in inserted], ["Order parts", "Receive parts", "Install"])
		by_subject = {t["subject"]: t for t in inserted}
		self.assertEqual([r["task"] for r in by_subject["Order parts"]["depends_on"]], ["TASK-A"])
		self.assertEqual(
			[r["task"] for r in by_subject["Receive parts"]["depends_on"]], ["TASK-NEW-1", "TASK-A"]
		)
		self.assertEqual([r["task"] for r in by_subject["Install"]["depends_on"]], ["TASK-NEW-2"])
		self.assertEqual([t["subject"] for t in result["tasks"]], ["Install", "Order parts", "Receive parts"])
		self.assertEqual(
			[t["depends_on"] for t in result["tasks"]],
			[["TASK-NEW-2"], ["TASK-A"], ["TASK-NEW-1", "TASK-A"]],
		)

	def test_a_loop_or_a_self_dependency_is_refused_with_the_rows_named(self):
		self.refused(
			[
				{"subject": "A", "depends_on": ["row:2"]},
				{"subject": "B", "depends_on": ["row:3"]},
				{"subject": "C", "depends_on": ["row:1"]},
			],
			"Rows 1, 2, 3 wait on each other",
		)
		self.refused([{"subject": "A", "depends_on": ["row:1"]}], "Row 1: a task cannot wait on itself")

	def test_every_row_is_checked_and_named_before_anything_is_written(self):
		cases = [
			([{"subject": "ok"}, {"subject": "  "}], "Row 2: give the task a subject"),
			([{"subject": "ok"}, {"subject": "x" * 141}], "Row 2: keep the subject"),
			([{"subject": "ok", "hours": -1}], "Row 1: hours must be between"),
			([{"subject": "ok", "hours": 401}], "Row 1: hours must be between"),
			([{"subject": "ok", "crew_size": 51}], "Row 1: crew size"),
			([{"subject": "ok", "phase": "p" * 141}], "Row 1: keep the phase name"),
			(
				[{"subject": "ok", "depends_on": ["row:9"]}],
				"Row 1: it waits on row 9, which is not in the grid",
			),
			([{"subject": "ok", "depends_on": ["row:x"]}], "not in the grid"),
			([{"subject": "ok", "depends_on": ["TASK-NOPE"]}], "Row 1: TASK-NOPE is not a task of PRJ-1"),
			([{"subject": "ok", "depends_on": ["TASK-OTHER"]}], "is not a task of PRJ-1"),
			([{"subject": "ok", "depends_on": ["TASK-P1"]}], "is a phase"),
			([{"subject": "ok", "depends_on": ["TASK-TPL"]}], "is not a task of PRJ-1"),
			([{"subject": "ok", "parent_task": "TASK-A"}], "Row 1: TASK-A is not a phase"),
			([{"subject": "ok", "parent_task": "TASK-OTHER-P"}], "is not a task of PRJ-1"),
			([{"subject": "ok", "parent_task": "TASK-P2"}], "is finished"),
			(["not a row"], "Row 1: expected a task"),
		]
		for rows, message in cases:
			self.refused(rows, message)

	def test_the_list_itself_is_checked(self):
		self.refused([], "Add at least one task")
		self.refused([{"subject": f"T{i}"} for i in range(101)], "100 tasks or fewer")
		self.refused(
			[{"subject": f"T{i}", "phase": f"Phase {i}"} for i in range(26)], "25 new phases or fewer"
		)
		with self.assertRaises(_Throw):
			self.actions.build_out_tasks("PRJ-1", "[not json")
		with self.assertRaises(_Throw):
			self.actions.build_out_tasks("PRJ-1", '{"subject": "an object, not a list"}')

	def test_exactly_the_cap_is_allowed(self):
		result = self.build([{"subject": f"T{i}"} for i in range(100)])
		self.assertEqual(result["count"], 100)

	def test_customer_jobs_only_and_the_project_must_exist(self):
		self.refused([{"subject": "x"}], "not a customer job", project="PRJ-INT")
		with self.assertRaises(_Throw):
			self.build([{"subject": "x"}], project="PRJ-NOPE")
		with self.assertRaises(_Throw):
			self.build([{"subject": "x"}], project=None)

	def test_only_with_create_permission_even_as_a_draft(self):
		self.frappe.perms["create"] = False
		with self.assertRaises(_PermissionError):
			self.build([{"subject": "x"}], draft=1)
		self.frappe.perms["create"] = True
		self.frappe.perms["create"] = False
		with self.assertRaises(_PermissionError):
			self.build([{"subject": "x"}])
		self.frappe.perms["create"] = True
		self.frappe.roles = ["Guest"]
		with self.assertRaises(_PermissionError):
			self.build([{"subject": "x"}])

	def test_a_dependency_the_caller_cannot_read_is_refused(self):
		self.frappe.perms["read"] = False
		with self.assertRaises(_PermissionError):
			self.build([{"subject": "x", "depends_on": ["TASK-A"]}])
		self.assertEqual(self.frappe.inserted, [])

	def test_a_task_erpnext_refuses_names_its_row_and_fails_the_request(self):
		self.frappe.fail_insert = True
		with self.assertRaises(_Throw) as caught:
			self.build([{"subject": "Dig"}])
		self.assertIn("Row 1 (Dig) could not be created, so nothing was", str(caught.exception))

	def test_it_reads_the_projects_tasks_once(self):
		self.build(
			[
				{"subject": "A", "phase": "Pumps", "depends_on": ["TASK-A"]},
				{"subject": "B", "parent_task": "TASK-P1", "depends_on": ["row:1"]},
			]
		)
		reads = [q for q in self.frappe.queries if q[0] == "get_all" and q[1] == "Task"]
		self.assertEqual(len(reads), 1)


class TestBuildOutReads(_Site):
	def test_the_context_offers_open_phases_and_open_tasks_of_the_project(self):
		context = self.actions.get_build_out_context("PRJ-1")
		self.assertEqual(context["phases"], [{"name": "TASK-P1", "subject": "Pumps"}])
		self.assertEqual(context["tasks"], [{"name": "TASK-A", "subject": "Dig the basin", "status": "Open"}])
		self.assertTrue(context["can_create"])
		self.assertFalse(context["truncated"])
		with self.assertRaises(_Throw):
			self.actions.get_build_out_context("PRJ-INT")

	def test_the_needs_tasks_list_is_one_query_and_re_checks_the_customer_job_rule(self):
		calls = []
		self.frappe.db.sql = lambda query, values=None, as_dict=False, **kw: (
			calls.append((query, values)),
			[
				_Doc(
					name="PRJ-1",
					title="Plaza",
					pm="Lisa",
					project_type="Build",
					planner_stream=0,
					expected_start_date=base.D(2026, 11, 2),
					expected_end_date=None,
				),
				_Doc(
					name="PRJ-INT",
					title="Shop",
					pm=None,
					project_type="Internal",
					planner_stream=0,
				),
				_Doc(
					name="PRJ-2",
					title=None,
					pm=None,
					project_type="Service",
					planner_stream=0,
				),
			],
		)[1]
		result = self.actions.get_projects_needing_tasks()
		self.assertEqual(len(calls), 1)
		self.assertIn("NOT EXISTS", calls[0][0])
		self.assertEqual(
			result["projects"],
			[
				{
					"name": "PRJ-1",
					"title": "Plaza",
					"pm": "Lisa",
					"expected_start": "2026-11-02",
					"expected_end": None,
				},
				{
					"name": "PRJ-2",
					"title": "PRJ-2",
					"pm": None,
					"expected_start": None,
					"expected_end": None,
				},
			],
		)
		self.assertTrue(result["can_create"])
		self.frappe.roles = ["Guest"]
		with self.assertRaises(_PermissionError):
			self.actions.get_projects_needing_tasks()
		self.assertEqual(len(calls), 1)


# ---------------------------------------------------------------------- undo


class TestUndoBuildOut(_Site):
	"""A phase TASK-P (made by the build-out) with TASK-A and TASK-B under it, B waiting on A."""

	def setUp(self):
		super().setUp()
		self.tasks = [{"task": "TASK-A", "modified": SAVED}, {"task": "TASK-B", "modified": SAVED}]
		self.phases = [{"task": "TASK-P", "modified": "2026-10-09 10:05:00"}]
		base._task("TASK-A", parent_task="TASK-P", modified=SAVED, owner="nik@example.com")
		base._task("TASK-B", parent_task="TASK-P", modified=SAVED, owner="nik@example.com")
		base._task("TASK-P", is_group=1, modified="2026-10-09 10:05:00", owner="nik@example.com")
		self.frappe.tables["Task"] = [
			_leaf("TASK-A", "A", parent_task="TASK-P"),
			_leaf("TASK-B", "B", parent_task="TASK-P"),
			_group("TASK-P", "Phase"),
		]
		self.rows = [
			{"parenttype": "Task", "parent": "TASK-P", "task": "TASK-A", "idx": 1},
			{"parenttype": "Task", "parent": "TASK-P", "task": "TASK-B", "idx": 2},
			{"parenttype": "Task", "parent": "TASK-B", "task": "TASK-A", "idx": 1},
		]
		self.frappe.tables["Task Depends On"] = self.rows

	def undo(self, tasks=None, phases=None):
		return self.actions.remove_built_out_tasks(
			json.dumps(self.tasks if tasks is None else tasks),
			json.dumps(self.phases if phases is None else phases),
		)

	def refused(self, message=None):
		with self.assertRaises(_Throw) as caught:
			self.undo()
		self.assertIn("Nothing was removed", str(caught.exception))
		if message:
			self.assertIn(message, str(caught.exception))
		self.assertEqual(self.frappe.deleted, [])
		self.assertEqual([e for e in self.frappe.events if e[0] == "db.delete"], [])

	def test_the_waiting_task_goes_first_then_the_phase_never_forced(self):
		result = self.undo()
		self.assertEqual(result, {"removed": ["TASK-B", "TASK-A"], "phases": ["TASK-P"], "gone": []})
		self.assertEqual([d[1] for d in self.frappe.deleted], ["TASK-B", "TASK-A", "TASK-P"])
		for _doctype, _name, kwargs in self.frappe.deleted:
			self.assertFalse(kwargs.get("force"))
		# Each task came off its phase's list of dependencies before it was deleted.
		unlinked = [e[2]["task"] for e in self.frappe.events if e[0] == "db.delete"]
		self.assertEqual(sorted(unlinked), ["TASK-A", "TASK-B"])
		first_delete = min(i for i, e in enumerate(self.frappe.events) if e[0] == "delete")
		self.assertTrue(
			all(i < first_delete for i, e in enumerate(self.frappe.events) if e[0] == "db.delete")
		)

	def test_without_a_phase_it_removes_just_the_tasks(self):
		self.frappe.tables["Task"] = [_leaf("TASK-A", "A"), _leaf("TASK-B", "B")]
		base._task("TASK-A", parent_task=None, modified=SAVED)
		base._task("TASK-B", parent_task=None, modified=SAVED)
		self.frappe.tables["Task Depends On"] = [{"parenttype": "Task", "parent": "TASK-B", "task": "TASK-A"}]
		result = self.undo(phases=[])
		self.assertEqual(result["removed"], ["TASK-B", "TASK-A"])
		self.assertEqual([e for e in self.frappe.events if e[0] == "db.delete"], [])

	def test_one_task_with_time_on_it_stops_all_of_them(self):
		self.frappe.tables["Timesheet Detail"] = [{"task": "TASK-B", "docstatus": 0}]
		self.refused("TASK-B: time is logged against it")

	def test_a_change_since_stops_it(self):
		self.frappe.docs[("Task", "TASK-A")]["modified"] = "2026-10-09 11:00:00"
		self.refused("TASK-A: it was changed after it was added")

	def test_a_phase_changed_since_stops_it(self):
		self.frappe.docs[("Task", "TASK-P")]["modified"] = "2026-10-09 12:00:00"
		self.refused("TASK-P: it was changed after it was added")

	def test_someone_elses_task_stops_it(self):
		self.frappe.docs[("Task", "TASK-A")]["owner"] = "lisa@example.com"
		self.refused("only the person who added it")

	def test_a_task_outside_the_build_out_that_waits_on_one_stops_it(self):
		self.rows.append({"parenttype": "Task", "parent": "TASK-OUTSIDE", "task": "TASK-A", "idx": 1})
		self.refused("TASK-A: another task depends on it")

	def test_a_sub_task_somebody_added_to_the_phase_stops_it(self):
		self.frappe.tables["Task"].append(_leaf("TASK-LATE", "Late", parent_task="TASK-P"))
		self.refused("TASK-P: it has sub-tasks")

	def test_a_sub_task_of_a_task_stops_it(self):
		self.frappe.tables["Task"].append(_leaf("TASK-KID", "Kid", parent_task="TASK-A"))
		self.refused("TASK-A: it has sub-tasks")

	def test_someone_elses_comment_or_a_file_stops_it(self):
		self.frappe.tables["File"] = [{"attached_to_doctype": "Task", "attached_to_name": "TASK-B"}]
		self.refused("a file is attached")

	def test_no_delete_permission_stops_it(self):
		self.frappe.perms["delete"] = False
		self.refused("you cannot delete Tasks")

	def test_a_task_already_gone_is_skipped(self):
		del self.frappe.docs[("Task", "TASK-A")]
		self.frappe.tables["Task"] = [r for r in self.frappe.tables["Task"] if r["name"] != "TASK-A"]
		result = self.undo()
		self.assertEqual(result["gone"], ["TASK-A"])
		self.assertEqual(result["removed"], ["TASK-B"])

	def test_the_arguments_are_checked(self):
		with self.assertRaises(_Throw):
			self.undo(tasks=[], phases=[])
		with self.assertRaises(_Throw):
			self.undo(tasks=[{"modified": SAVED}])
		self.frappe.roles = ["Guest"]
		with self.assertRaises(_PermissionError):
			self.undo()


class TestUndoOfAQuickAddUnderAPhase(_Site):
	"""ERPNext lists a sub-task among its phase's dependencies; that row must not make Undo refuse."""

	def test_the_phases_own_row_is_not_another_task_depending_on_it(self):
		base._task("TASK-NEW-1", parent_task="TASK-P1", modified=SAVED)
		self.frappe.tables["Task Depends On"] = [
			{"parenttype": "Task", "parent": "TASK-P1", "task": "TASK-NEW-1"}
		]
		result = self.actions.remove_created_task("TASK-NEW-1", SAVED)
		self.assertEqual(result["removed"], "TASK-NEW-1")
		self.assertEqual([e[2]["task"] for e in self.frappe.events if e[0] == "db.delete"], ["TASK-NEW-1"])

	def test_any_other_dependent_still_stops_it(self):
		base._task("TASK-NEW-1", parent_task="TASK-P1", modified=SAVED)
		self.frappe.tables["Task Depends On"] = [
			{"parenttype": "Task", "parent": "TASK-P1", "task": "TASK-NEW-1"},
			{"parenttype": "Task", "parent": "TASK-A", "task": "TASK-NEW-1"},
		]
		with self.assertRaises(_Throw):
			self.actions.remove_created_task("TASK-NEW-1", SAVED)
		self.assertEqual(self.frappe.deleted, [])


# ---------------------------------------------------------------------- the page


def _strip(code):
	return base._strip(code)


def _t1_block(code):
	start = code.index("// ====================================================================== T1:")
	end = code.find("\n// ======================================================================", start + 60)
	return code[start:] if end < 0 else code[start:end]


def _pure_source(code):
	start = code.index("const PPT1_PURE = {")
	return code[start : code.index("\n};\n", start) + 3]


PURE_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(0, "utf8");
const tr = (text, args) => (args ? String(text).replace(/\{(\d+)\}/g, (m, i) => String(args[i])) : String(text));
const ctx = { __: tr };
vm.createContext(ctx);
vm.runInContext(source + "\nthis.P = PPT1_PURE;", ctx);
const P = ctx.P;
const out = {};
const phases = [{ name: "TASK-P1", subject: "Pumps" }, { name: "TASK-P2", subject: "Electrical Work" }];
const row = (id, over) => Object.assign(P.blank_row(id), over || {});
out.blank = [P.is_blank(P.blank_row("r1")), P.is_blank(P.blank_row("r1", "Pumps")), P.is_blank(row("r1", { subject: " x " })), P.is_blank(row("r1", { hours: "2" })), P.is_blank(row("r1", { needs: "TASK-1" })), P.is_blank(row("r1", { crew_size: "0" }))];
out.phase = [P.phase_match("", phases), P.phase_match("  pumps ", phases), P.phase_match("TASK-P2", phases), P.phase_match("electrical   work", phases), P.phase_match("Site prep", phases)];
out.orders = [
	P.build_order(3, {}),
	P.build_order(3, { 1: new Set([0]), 2: new Set([1]) }),
	P.build_order(4, { 0: new Set([2]) }),
	P.build_order(2, { 0: new Set([1]), 1: new Set([0]) }),
	P.build_order(1, { 0: new Set([0]) }),
	P.build_order(4, { 1: new Set([2]), 2: new Set([1]), 3: new Set([1]) }),
];
const grid = [
	row("a", { subject: "  Set  the pumps ", hours: "6h", phase: "pumps" }),
	P.blank_row("blank", "Pumps"),
	row("b", { subject: "Wire", needs: "row:a", phase: "Site prep", crew_size: "2" }),
	row("c", { subject: "Test", needs: "TASK-1", phase: "SITE PREP" }),
	row("d", { subject: "Last", needs: "row:b" }),
];
out.payload = P.rows_payload(grid, phases);
out.empty = P.rows_payload([P.blank_row("x")], phases);
out.problems = P.rows_payload(
	[
		row("a", { hours: "3" }),
		row("b", { subject: "Bad hours", hours: "lots" }),
		row("c", { subject: "Negative", hours: "-2" }),
		row("d", { subject: "Big crew", crew_size: "99" }),
		row("e", { subject: "Self", needs: "row:e" }),
		row("f", { subject: "Gone", needs: "row:zzz" }),
		row("g", { subject: "x".repeat(141) }),
	],
	phases
).problems;
const loop = P.rows_payload([row("a", { subject: "A", needs: "row:b" }), row("b", { subject: "B", needs: "row:c" }), row("c", { subject: "C", needs: "row:a" }), row("d", { subject: "D" })], phases);
out.loop = { problems: loop.problems, cycle: loop.cycle };
out.paste = [
	P.parse_paste("Lay conduit\t3\tElectrical\t2\nFill basin\t\tSite prep\n\nHang lights\r\n"),
	P.parse_paste("just one line"),
	P.parse_paste("one cell\twith hours"),
	P.parse_paste(""),
	P.parse_paste(null),
];
out.counts = [P.summary(0, 0), P.summary(1, 0), P.summary(5, 1), P.summary(7, 3), P.counts(2, 0)];
out.needs = [
	P.needs_visible({ name: "PRJ-1", pm: "Lisa" }, "", ""),
	P.needs_visible({ name: "PRJ-1", pm: "Lisa" }, "PRJ-1", ""),
	P.needs_visible({ name: "PRJ-1", pm: "Lisa" }, "PRJ-2", ""),
	P.needs_visible({ name: "PRJ-1", pm: "Lisa" }, "", "Lisa"),
	P.needs_visible({ name: "PRJ-1", pm: "Lisa" }, "", "James"),
	P.needs_visible({ name: "PRJ-1", pm: null }, "", "James"),
];
process.stdout.write(JSON.stringify(out, (k, v) => (v instanceof Set ? [...v] : v)));
"""


class TestPureUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		if not node:
			raise unittest.SkipTest("node is not on PATH")
		cls.node = node
		code = PP_JS.read_text(encoding="utf-8")
		result = subprocess.run(
			[node, "-e", PURE_HARNESS],
			input=_pure_source(code),
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
		if result.returncode != 0:
			raise AssertionError(result.stderr)
		cls.out = json.loads(result.stdout)

	def test_blank_rows_are_the_ones_nobody_typed_in(self):
		self.assertEqual(self.out["blank"], [True, True, False, False, False, False])

	def test_a_typed_phase_is_an_existing_one_or_a_new_name(self):
		self.assertEqual(
			self.out["phase"],
			[
				None,
				{"parent_task": "TASK-P1"},
				{"parent_task": "TASK-P2"},
				{"parent_task": "TASK-P2"},
				{"phase": "Site prep"},
			],
		)

	def test_the_dependency_order_agrees_with_the_server(self):
		a = base.actions
		cases = [
			(3, {}),
			(3, {1: {0}, 2: {1}}),
			(4, {0: {2}}),
			(2, {0: {1}, 1: {0}}),
			(1, {0: {0}}),
			(4, {1: {2}, 2: {1}, 3: {1}}),
		]
		for (count, waits), js in zip(cases, self.out["orders"], strict=True):
			order, cycle = a.build_order(count, waits)
			self.assertEqual((js["order"], js["cycle"]), (order, cycle), (count, waits))

	def test_the_payload_drops_blank_rows_and_numbers_the_rest(self):
		out = self.out["payload"]
		self.assertEqual(out["problems"], [])
		self.assertEqual(
			out["rows"],
			[
				{"subject": "Set the pumps", "hours": 6, "parent_task": "TASK-P1"},
				{"subject": "Wire", "phase": "Site prep", "crew_size": 2, "depends_on": ["row:1"]},
				{"subject": "Test", "phase": "SITE PREP", "depends_on": ["TASK-1"]},
				{"subject": "Last", "depends_on": ["row:2"]},
			],
		)
		# One new phase however it was capitalized, and a phase of the project is not a new one.
		self.assertEqual(out["new_phases"], ["Site prep"])
		self.assertEqual(out["cycle"], [])
		self.assertEqual(self.out["empty"]["rows"], [])

	def test_the_payload_is_what_the_server_accepts(self):
		# The rows the page sends are the keys build_out_tasks reads, and nothing else.
		keys = {key for row in self.out["payload"]["rows"] for key in row}
		self.assertEqual(keys, {"subject", "hours", "parent_task", "phase", "crew_size", "depends_on"})
		source = (APP / "api/planner_actions.py").read_text(encoding="utf-8")
		for key in keys:
			self.assertIn(f'item.get("{key}")', source, key)

	def test_every_mistake_is_named_by_its_row(self):
		self.assertEqual(
			self.out["problems"],
			[
				"Row 1: give the task a subject.",
				"Row 2: hours must be a number from 0 to 400.",
				"Row 3: hours must be a number from 0 to 400.",
				"Row 4: crew size must be a number from 0 to 50.",
				"Row 5: a task cannot wait on itself.",
				"Row 6: it waits on a row that is empty or gone.",
				"Row 7: keep the subject to 140 characters.",
			],
		)

	def test_a_loop_is_named_and_marked(self):
		self.assertEqual(
			self.out["loop"]["problems"],
			["Rows 1, 2, 3 wait on each other, round and round. Take one of those dependencies out."],
		)
		self.assertEqual(self.out["loop"]["cycle"], ["a", "b", "c"])

	def test_pasting_lines_from_a_spreadsheet(self):
		self.assertEqual(
			self.out["paste"],
			[
				[
					{"subject": "Lay conduit", "hours": "3", "phase": "Electrical", "crew_size": "2"},
					{"subject": "Fill basin", "hours": "", "phase": "Site prep", "crew_size": ""},
					{"subject": "Hang lights", "hours": "", "phase": "", "crew_size": ""},
				],
				None,
				[{"subject": "one cell", "hours": "with hours", "phase": "", "crew_size": ""}],
				None,
				None,
			],
		)

	def test_the_button_says_what_it_will_make(self):
		self.assertEqual(
			self.out["counts"],
			[
				"Create tasks",
				"Create 1 task",
				"Create 5 tasks and 1 phase",
				"Create 7 tasks and 3 phases",
				"2 tasks",
			],
		)

	def test_the_needs_tasks_list_follows_the_page_filters(self):
		self.assertEqual(self.out["needs"], [True, True, False, True, False, False])


METHODS_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const [t1_source, six_b_source] = JSON.parse(fs.readFileSync(0, "utf8"));
const tr = (text, args) => (args ? String(text).replace(/\{(\d+)\}/g, (m, i) => String(args[i])) : String(text));
const alerts = [];
const calls = [];
const ctx = {
	console,
	Promise,
	Date,
	setTimeout: (fn) => fn(),
	clearTimeout: () => null,
	__: tr,
	pp_esc: (v) => String(v),
	pp_when: (ymd) => `when(${ymd})`,
	PP: { heatmap_view: "heatmap" },
	PP6A: { owner: "pp" },
	frappe: { msgprint: (m) => alerts.push(typeof m === "string" ? m : m.message), show_alert: (o) => alerts.push(o.message), call: (o) => { calls.push(o); return Promise.resolve({ message: { projects: [], can_create: true } }); } },
	document: { getElementById: () => true },
};
vm.createContext(ctx);
vm.runInContext(t1_source + "\n" + six_b_source + "\nthis.T1 = PPT1_METHODS; this.PPT1 = PPT1; this.B = PP6B_METHODS;", ctx);
const out = {};
(async () => {
	const sent = [];
	const undone = [];
	const page = Object.assign(Object.create(ctx.T1), {
		data: { can_edit: true },
		project: "",
		pm: "",
		view: "week",
		modified: { "TASK-1": "m-fresh" },
		draft_on: false,
		by_project: {},
		p6_send_modules: {},
		p6_menu_providers: [],
		p6_legend_providers: [],
		p6b_refuse_in_draft: () => false,
		draft_args() { return this.draft_on ? { draft: 1 } : {}; },
		send: (method, args, opts) => { sent.push([method, args, opts]); return Promise.resolve({}); },
		load: () => undone.push("load"),
		render_trays: () => undone.push("trays"),
	});
	page.t1 = { needs: null, needs_token: 0, needs_at: 0, needs_open: false, show_all: false, drafts: {}, ids: 0, grid: null };
	out.can = [page.t1_can(), Object.assign(Object.create(page), { data: { can_edit: false } }).t1_can()];
	out.actions = [page.t1_project_actions("PRJ-1").map((a) => [a.label, !!a.primary]), page.t1_project_actions(""), Object.assign(Object.create(page), { data: { can_edit: false } }).t1_project_actions("PRJ-1")];
	out.menu = [page.t1_menu_items({ kind: "card", card: { project: "PRJ-1" } }).map((i) => i.label), page.t1_menu_items({ kind: "cell" }), page.t1_menu_items({ kind: "card", card: {} })];
	out.legend = page.t1_legend_sections().map((s) => s.title);
	// The Needs tasks tray rows: filters, and nothing without permission to create.
	page.t1.needs = { can_create: true, projects: [{ name: "PRJ-1", pm: "Lisa" }, { name: "PRJ-2", pm: "James" }] };
	out.rows = [page.t1_needs_rows().map((r) => r.name)];
	page.pm = "Lisa";
	out.rows.push(page.t1_needs_rows().map((r) => r.name));
	page.pm = "";
	page.project = "PRJ-2";
	out.rows.push(page.t1_needs_rows().map((r) => r.name));
	page.project = "";
	page.t1.needs.can_create = false;
	out.rows.push(page.t1_needs_rows().map((r) => r.name));
	page.t1.needs.can_create = true;
	// The list is asked for again only after a while, unless forced.
	page.t1.needs_at = Date.now();
	await page.t1_load_needs();
	const quiet = calls.length;
	await page.t1_load_needs(true);
	out.load_calls = [quiet, calls.length, calls.map((c) => c.method)];
	// Undo: the server is asked with the freshest `modified` the page knows.
	page.modified["TASK-NEW-1"] = "m-newer";
	out.undo_other = [page.t1_undo(null), page.t1_undo({ p6b: "created" }), page.t1_undo({ t1: "other" })];
	out.undo = page.t1_undo({ t1: "built", subject: "2 tasks", tasks: [{ task: "TASK-NEW-1", modified: "m1" }, { task: "TASK-NEW-2", modified: "m2" }], phases: [{ task: "TASK-NEW-P", modified: "mp" }] });
	await Promise.resolve();
	await Promise.resolve();
	out.sent = sent.map(([method, args, opts]) => [method, JSON.parse(args.tasks), JSON.parse(args.phases), opts.message]);
	// Quick add (Phase 6B's dialog): only what was filled in is sent.
	const created = [];
	const qa = Object.assign(Object.create(ctx.B), { draft_args: () => ({}), p6b_create: (method, args, subject, message) => { created.push([method, args, subject, message]); return null; } });
	const panel = { hide: () => null };
	const base = { project: "PRJ-1", subject: " Fix ", date: "2026-10-13", hours: "3", resource: "RES-1", tentative: 1 };
	qa.p6b_quick_add_save(panel, base);
	qa.p6b_quick_add_save(panel, Object.assign({}, base, {
		parent_task: "TASK-P1", depends_on: ["TASK-A", "TASK-B"], start_time: "08:00:00", end_time: "09:30:00",
		crew: ["RES-1", "RES-2", "RES-4"], address: "ADDR-1", outdoor: 1, customer_visit: 1, description: "  Bring ladders ", crew_size: "3",
	}));
	out.slot_alert_before = alerts.length;
	out.half_slot = qa.p6b_quick_add_save(panel, Object.assign({}, base, { start_time: "08:00:00" }));
	out.backwards_slot = qa.p6b_quick_add_save(panel, Object.assign({}, base, { start_time: "09:00:00", end_time: "08:00:00" }));
	out.slot_alerts = alerts.slice(out.slot_alert_before);
	out.quick = created.map(([method, args]) => [method, args]);
	// Opening the dialog (v1.602.1). Frappe builds the fields INSIDE p6b_panel, and a MultiSelectPills
	// field calls its get_data right then, before p6b_panel returns: reading a `const panel` there
	// threw "Cannot access 'panel' before initialization" and the dialog never opened. The stand-in
	// does what Frappe does: every get_data and get_query is asked while the panel is being built,
	// and again once it exists (as typing in a field would).
	const asked = [];
	ctx.frappe.db = { get_link_options: (doctype, txt, filters) => { asked.push([doctype, filters || null]); return Promise.resolve([]); } };
	let built = null;
	const opener = Object.assign(Object.create(ctx.B), {
		data: { can_edit: true, projects: [{ name: "PRJ-1" }] },
		project: "PRJ-1",
		today: () => "2026-10-13",
		resource_label: (name) => name,
		p6b_refuse_in_draft: () => false,
		draft_args: () => ({}),
		p6b_panel: (opts) => {
			const ask = () => opts.fields.forEach((df) => {
				if (df.get_data) df.get_data("");
				if (df.get_query) asked.push(["query:" + df.fieldname, df.get_query().filters]);
			});
			ask();
			built = { ask, get_value: (name) => (name === "project" ? "PRJ-7" : null), set_primary_action: () => null, show: () => null };
			return built;
		},
	});
	try {
		opener.p6b_quick_add({ kind: "cell", resource: "RES-1", ymd: "2026-10-13" });
		out.open_error = null;
	} catch (e) {
		out.open_error = String(e && e.message ? e.message : e);
	}
	out.asked_while_building = asked.splice(0).filter(([what]) => what === "Task" || what === "query:parent_task");
	if (built) built.ask();
	out.asked_after = asked.filter(([what]) => what === "Task" || what === "query:parent_task");
	process.stdout.write(JSON.stringify(out));
})().catch((e) => {
	process.stderr.write(String(e && e.stack ? e.stack : e));
	process.exit(1);
});
"""


class TestMethodsUnderNode(unittest.TestCase):
	"""The page's methods that need no DOM, with the page and frappe stubbed. (The grid itself was
	driven by hand in a browser against the real planner kit drawer: typing, Enter, Tab, pasting,
	choosing a dependency, a loop being marked, Ctrl+Enter, and the rows surviving a close.)"""

	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		if not node:
			raise unittest.SkipTest("node is not on PATH")
		code = PP_JS.read_text(encoding="utf-8")
		t1 = code[
			code.index("const PPT1 = {") : code.index(
				"Object.assign(ProjectPlanner.prototype, PPT1_METHODS);"
			)
		]
		six_b = code[
			code.index("const PP6B = {") : code.index(
				"Object.assign(ProjectPlanner.prototype, PP6B_METHODS);"
			)
		]
		result = subprocess.run(
			[node, "-e", METHODS_HARNESS],
			input=json.dumps([t1, six_b]),
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
		if result.returncode != 0:
			raise AssertionError(result.stderr)
		cls.out = json.loads(result.stdout)

	def test_where_build_out_opens_from(self):
		out = self.out
		self.assertEqual(out["can"], [True, False])
		self.assertEqual(out["actions"], [[["Build out tasks", True]], [], []])
		self.assertEqual(out["menu"], [["Build out tasks for this project…"], [], []])
		self.assertEqual(out["legend"], ["Building out a project"])

	def test_the_needs_tasks_rows_follow_the_filters_and_the_permission(self):
		self.assertEqual(self.out["rows"], [["PRJ-1", "PRJ-2"], ["PRJ-1"], ["PRJ-2"], []])

	def test_the_list_is_not_asked_for_on_every_load(self):
		quiet, after, methods = self.out["load_calls"]
		self.assertEqual((quiet, after), (0, 1))
		self.assertEqual(methods, ["erpnext_enhancements.api.planner_actions.get_projects_needing_tasks"])

	def test_undo_is_the_servers_with_the_newest_stamps(self):
		self.assertEqual(self.out["undo_other"], [False, False, False])
		self.assertTrue(self.out["undo"])
		self.assertEqual(
			self.out["sent"],
			[
				[
					"remove_built_out_tasks",
					[{"task": "TASK-NEW-1", "modified": "m-newer"}, {"task": "TASK-NEW-2", "modified": "m2"}],
					[{"task": "TASK-NEW-P", "modified": "mp"}],
					"Undone: 2 tasks",
				]
			],
		)

	def test_a_plain_quick_add_sends_what_it_always_sent(self):
		plain = self.out["quick"][0]
		self.assertEqual(
			plain,
			[
				"quick_add_task",
				{
					"project": "PRJ-1",
					"subject": "Fix",
					"date": "2026-10-13",
					"tentative": 1,
					"hours": 3,
					"resource": "RES-1",
				},
			],
		)

	def test_the_richer_quick_add_sends_only_what_was_filled_in(self):
		args = self.out["quick"][1][1]
		self.assertEqual(
			{
				k: v
				for k, v in args.items()
				if k not in {"project", "subject", "date", "tentative", "hours", "resource"}
			},
			{
				"parent_task": "TASK-P1",
				"depends_on": '["TASK-A","TASK-B"]',
				"start_time": "08:00:00",
				"end_time": "09:30:00",
				# The person above is the lead; they are not listed twice.
				"crew": '["RES-2","RES-4"]',
				"address": "ADDR-1",
				"outdoor": 1,
				"customer_visit": 1,
				"description": "Bring ladders",
				"crew_size": 3,
			},
		)
		self.assertEqual(len(self.out["quick"]), 2)

	def test_the_dialog_opens_while_its_fields_ask_for_their_options(self):
		# v1.602.1: on production the dialog threw before it opened (a `const panel` read by
		# Depends on's get_data while Frappe was still building the panel).
		self.assertIsNone(self.out["open_error"])
		# While the panel is being built the project is the planner's own; once it exists, the
		# project chosen in the dialog.
		self.assertEqual(
			self.out["asked_while_building"],
			[
				["query:parent_task", {"project": "PRJ-1", "is_group": 1}],
				["Task", {"project": "PRJ-1", "is_group": 0}],
			],
		)
		self.assertEqual(
			self.out["asked_after"],
			[
				["query:parent_task", {"project": "PRJ-7", "is_group": 1}],
				["Task", {"project": "PRJ-7", "is_group": 0}],
			],
		)

	def test_a_half_or_backwards_slot_is_said_before_asking_the_server(self):
		self.assertIsNone(self.out["half_slot"])
		self.assertIsNone(self.out["backwards_slot"])
		self.assertEqual(len(self.out["slot_alerts"]), 2)
		self.assertIn("start and an end", self.out["slot_alerts"][0])


class TestPageSource(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.code = PP_JS.read_text(encoding="utf-8")
		cls.block = _t1_block(cls.code)

	def test_the_page_parses(self):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not on PATH")
		result = subprocess.run([node, "--check", str(PP_JS)], capture_output=True, text=True, timeout=60)
		self.assertEqual(result.returncode, 0, result.stderr)

	def test_the_hooks_into_the_class_are_one_line_each(self):
		code = self.code
		# The toolbar (v1.687.0) comes last: it folds what every phase put in the bar into its menus. P12's
		# init_tour follows it (the tour needs the toolbar built), before the drag listeners.
		self.assertIn(
			"this.init_phase6d();\n\t\tthis.init_t1();\n\t\tthis.init_toolbar();\n\t\tthis.init_tour();\n\t\tthis.bind_drag();",
			code,
		)
		self.assertIn("this.load_overdue();\n\t\t\t\tthis.t1_load_needs();", code)
		self.assertIn("this.t1_render_tray();\n\t}", code)
		self.assertIn("if (this.t1_undo(snap)) return;\n\t\tif (this.p6b_undo(snap)) return;", code)
		self.assertIn("actions.push(...this.t1_project_actions(project));", code)
		self.assertIn("Object.assign(ProjectPlanner.prototype, PPT1_METHODS);", code)

	def test_the_writes_go_through_send_with_the_servers_module(self):
		block = _strip(self.block)
		self.assertIn("t1_send(method, args, opts) {\n\t\treturn this.send(method, args, opts);", self.block)
		self.assertEqual(
			set(re.findall(r'this\.t1_send\(\s*"(\w+)"', block)),
			{"build_out_tasks", "remove_built_out_tasks"},
		)
		methods = re.search(r"methods: \[(.*?)\]", self.block).group(1)
		self.assertEqual(set(re.findall(r'"(\w+)"', methods)), {"build_out_tasks", "remove_built_out_tasks"})
		self.assertIn('actions: "erpnext_enhancements.api.planner_actions"', self.block)
		self.assertIn("this.p6_send_modules[method] = PPT1.actions", self.block)
		# Never a side door to a write; the two reads are plain frappe.calls.
		self.assertEqual(
			set(re.findall(r"frappe\.call\(\{ method: `\$\{PPT1\.actions\}\.(\w+)`", block)),
			{"get_projects_needing_tasks", "get_build_out_context", "get_copy_source"},
		)

	def test_back_and_forward_belong_to_the_kit_drawer(self):
		bare = _strip(self.block)
		for forbidden in (
			"localStorage",
			"pushState",
			"replaceState",
			"window.location",
			"location.href",
			"history.",
			"innerHTML",
			'type: "GET"',
			"frappe.xcall",
			"set_route",
		):
			self.assertNotIn(forbidden, bare, forbidden)
		self.assertIn("kit.drawer.open({", bare)
		self.assertIn("reopen: () => this.t1_open_build_out(project, title)", bare)
		# What was typed is kept for a Back that closes the panel. (v1.603.0: Draft mode no longer refuses
		# it; Create sends draft=1 instead, see test_planner_draft_new_tasks.)
		self.assertIn("this.t1.drafts[project]", bare)
		self.assertNotIn("p6b_refuse_in_draft", bare)

	def test_every_method_is_t1_prefixed_and_defined_once(self):
		table = self.block[self.block.index("const PPT1_METHODS = {") :]
		names = re.findall(r"^\t([A-Za-z_]\w*)\s*\([^)]*\)\s*\{\s*$", table, re.M)
		self.assertTrue(names)
		self.assertTrue(all(n.startswith("t1_") or n == "init_t1" for n in names), names)
		everywhere = re.findall(r"^\t(?:async\s+)?([A-Za-z_]\w*)\s*\([^)]*\)\s*\{\s*$", self.code, re.M)
		skip = {"if", "for", "while", "switch", "catch", "function"}
		everywhere = [n for n in everywhere if n not in skip]
		self.assertEqual(sorted({n for n in everywhere if everywhere.count(n) > 1}), [])

	def test_house_rules(self):
		block = self.block
		raw = [
			line.strip()
			for line in block.splitlines()
			if "<" in line and re.search(r"\$\{(?:card|item|data|row|task|phase|project|result)\.\w+", line)
		]
		self.assertEqual(raw, [])
		for british in ("colour", "cancelled", "labour", "centre", "behaviour"):
			self.assertNotIn(british, block.lower(), british)
		# The block's CSS is its section of project_planner.css (S3.4d, v1.664.0), sent with the
		# page; the script writes no <style> element. The grid's cells are Bootstrap fields, styled
		# in the grid as .t1-grid .form-control.
		self.assertNotIn("PPT1_STYLE", block)
		raw = PP_JS.with_suffix(".css").read_text(encoding="utf-8")
		start = raw.index("*/", raw.index("/* ===", raw.index("(PPT1_STYLE).")))
		style = re.sub(r"/\*.*?\*/", "", raw[start + 2 : raw.index("\n/* ===", start)], flags=re.S)
		classes = set(re.findall(r"\.([a-zA-Z][\w-]*)", style))
		self.assertTrue(classes)
		self.assertEqual({c for c in classes if not c.startswith(("t1-", "pp-"))}, {"form-control"}, classes)

	def test_the_quick_add_dialog_has_the_new_fields_and_stays_collapsed(self):
		quick = self.code[
			self.code.index("\tp6b_quick_add(target) {") : self.code.index(
				"\tp6b_quick_add_save(panel, values) {"
			)
		]
		fields = set(re.findall(r'fieldname: "(\w+)"', quick))
		self.assertEqual(
			fields,
			{
				"project",
				"subject",
				"hours",
				"resource",
				"date",
				"tentative",
				"more",
				"parent_task",
				"depends_on",
				"start_time",
				"end_time",
				"crew",
				"crew_size",
				"address",
				"outdoor",
				"customer_visit",
				"description",
			},
		)
		# One collapsible section holds the new fields, and nothing after it opens a second one.
		self.assertEqual(quick.count('fieldtype: "Section Break"'), 1)
		self.assertIn("collapsible: 1", quick)
		self.assertNotIn("innerHTML", quick)


class TestDocsAndCi(unittest.TestCase):
	def test_the_endpoints_are_documented(self):
		readme = API_README.read_text(encoding="utf-8")
		for name in (
			"build_out_tasks",
			"remove_built_out_tasks",
			"get_build_out_context",
			"get_projects_needing_tasks",
		):
			self.assertIn(name, readme, name)
		pe = PE_README.read_text(encoding="utf-8")
		for needle in ("Build out", "Needs tasks", "build_out_tasks", "remove_built_out_tasks", "T1"):
			self.assertIn(needle, pe, needle)

	def test_the_suite_runs_in_ci_as_unittest(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_planner_build_out -v", ci)
		# This suite is unittest-style: a pytest step would collect nothing of its helpers' setUpModule.
		self.assertNotIn("pytest erpnext_enhancements/tests/test_planner_build_out", ci)

	def test_the_release_is_written_up(self):
		changelog = CHANGELOG.read_text(encoding="utf-8")
		self.assertIn("## [1.602.0] - 2026-10-12", changelog)
		section = changelog[changelog.index("## [1.602.0]") :].split("\n## [", 1)[0]
		for needle in ("build_out_tasks", "Needs tasks", "quick add", "remove_built_out_tasks"):
			self.assertIn(needle, section, needle)


if __name__ == "__main__":
	unittest.main()
