# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""P9 of the Planner UX redesign: the Overview tab's server side (``api/planner_overview.py``;
``docs/planner-ux-redesign.md`` §4.7 and §8).

What this pins, and what would fail quietly otherwise:

* **Overdue is counted by end date, never by the status value** (spec §8). 1,005 open tasks on
  production carry the status ``Overdue``; a task is overdue here only when it is open and its last day
  is before today. A task with that status and a future end is not overdue, and an Open one whose end has
  passed is.
* **The display rule is the planner's (D6).** A task counts when it is under a phase or has someone on
  it; the template admin tasks all dated a project's first day do not, so they cannot make a job look
  late the day it starts.
* **Behind baseline is in working days, and more than the threshold.** A phase is judged by its open work
  against that work's baseline; work under no phase task by task; finished work never; and nothing at
  all without a baseline.
* **A job with no dated work falls back to the Project's own dates**, and says so.
* **A Draft schedule is hatched on the Overview, and left out of the summary.** With drafts applied, a
  drafted task's days replace the stored row's and a drafted new task joins under its group;
  ``published_only`` drops a Draft job from the rows and names it.
* **The read is bound SQL**, one Task query for every job, and the window trims what is sent.
* **Every endpoint holds the planner's role gate**, and the preview its manager gate.

Bench-free. Reuses ``test_planner_phase3b``'s ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_planner_overview
"""

import datetime
import importlib
import re
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
OVERVIEW_PATH = APP / "api/planner_overview.py"
CI = REPO_ROOT / ".github/workflows/ci.yml"

ov = None
D = datetime.date
#: The stub's "today" is Thursday 2026-10-08; Monday 2026-10-12 starts the next working week.
TODAY = h.TODAY


def setUpModule():
	global ov
	h.setUpModule()
	ov = importlib.import_module("erpnext_enhancements.api.planner_overview")


def tearDownModule():
	h.tearDownModule()


def row(
	name, subject, start=None, end=None, parent=None, group=0, status="Open", base=None, hours=0, milestone=0
):
	"""A Task row as the SQL read gives it."""
	return {
		"name": name,
		"subject": subject,
		"status": status,
		"parent_task": parent,
		"is_group": group,
		"exp_start_date": start,
		"exp_end_date": end if end is not None else start,
		"expected_time": hours,
		"is_milestone": milestone,
		"custom_baseline_start": base[0] if base else None,
		"custom_baseline_end": base[1] if base else None,
	}


def tasks(*rows):
	return [ov.normalize_task(r) for r in rows]


PROJECT = {
	"name": "PRJ-1",
	"title": "Riverwalk Plaza",
	"pm": "Clegg",
	"schedule": {
		"status": "Published",
		"published_on": "2026-10-01 09:00:00",
		"baseline_set_on": "2026-10-01",
	},
	"expected_start": None,
	"expected_end": None,
}


# ====================================================================== pure helpers


class TestWindowsAndWeeks(unittest.TestCase):
	def test_months_are_one_of_the_choices(self):
		self.assertEqual(
			[ov.months_choice(v) for v in (3, "6", "9", 12, 5, "x", None, "")], [3, 6, 9, 12, 6, 6, 6, 6]
		)

	def test_the_window_is_whole_calendar_months(self):
		self.assertEqual(ov.month_window("2026-10-21", 6), (D(2026, 10, 1), D(2027, 3, 31)))
		self.assertEqual(ov.month_window(D(2026, 12, 31), 3), (D(2026, 12, 1), D(2027, 2, 28)))
		self.assertEqual(ov.month_window("2027-01-15", 12), (D(2027, 1, 1), D(2027, 12, 31)))
		# A leap February ends on the 29th.
		self.assertEqual(ov.month_window("2028-01-02", 3)[1], D(2028, 3, 31))
		self.assertEqual(ov.month_window("2028-02-10", 3)[1], D(2028, 4, 30))

	def test_the_summary_week_is_the_week_ahead(self):
		sunday = 6  # production's first weekday, as a Monday-0 weekday number
		monday_first = 0
		# Sunday-first: Sun Oct 25 and Mon Oct 26 are the week's first two days; Tuesday on, the next week.
		self.assertEqual(ov.default_summary_week(D(2026, 10, 25), sunday), D(2026, 10, 25))
		self.assertEqual(ov.default_summary_week(D(2026, 10, 26), sunday), D(2026, 10, 25))
		self.assertEqual(ov.default_summary_week(D(2026, 10, 27), sunday), D(2026, 11, 1))
		self.assertEqual(ov.default_summary_week(D(2026, 10, 31), sunday), D(2026, 11, 1))
		# Monday-first: Monday and Tuesday are this week, Wednesday on the next.
		self.assertEqual(ov.default_summary_week(D(2026, 10, 26), monday_first), D(2026, 10, 26))
		self.assertEqual(ov.default_summary_week(D(2026, 10, 27), monday_first), D(2026, 10, 26))
		self.assertEqual(ov.default_summary_week(D(2026, 10, 28), monday_first), D(2026, 11, 2))

	def test_the_week_that_holds_today_never_looks_ahead(self):
		sunday, monday = 6, 0
		# Sunday-first: Sun Oct 25 to Sat Oct 31 are one week, every day of it.
		for day in range(25, 32):
			self.assertEqual(ov.week_holding(D(2026, 10, day), sunday), D(2026, 10, 25), day)
		self.assertEqual(ov.week_holding(D(2026, 11, 1), sunday), D(2026, 11, 1))
		# Monday-first: the same Tuesday is in the week that began Monday Oct 26.
		self.assertEqual(ov.week_holding(D(2026, 10, 27), monday), D(2026, 10, 26))
		self.assertEqual(ov.week_holding("2026-10-31", monday), D(2026, 10, 26))
		# And that is NOT what a print defaults to on a Tuesday.
		self.assertNotEqual(
			ov.week_holding(D(2026, 10, 27), sunday), ov.default_summary_week(D(2026, 10, 27), sunday)
		)

	def test_a_requests_day_is_a_date_or_a_clear_error(self):
		today = D(2026, 10, 21)
		self.assertEqual(ov.given_day("2026-10-05"), D(2026, 10, 5))
		self.assertEqual(ov.given_day("2026-10-05 08:00:00"), D(2026, 10, 5))
		self.assertEqual(ov.given_day(D(2026, 10, 5)), D(2026, 10, 5))
		self.assertEqual(ov.given_day(None, today), today)
		self.assertEqual(ov.given_day("", today), today)
		self.assertIsNone(ov.given_day(None))
		for bad in ("garbage", "2026-02-30", "2026/10/05x", [1], {"a": 1}):
			with self.assertRaises(h._Throw, msg=repr(bad)):
				ov.given_day(bad, today)

	def test_plural_words_the_count(self):
		self.assertEqual(ov.plural(1, "{0} task", "{0} tasks"), "1 task")
		self.assertEqual(ov.plural(0, "{0} task", "{0} tasks"), "0 tasks")
		self.assertEqual(ov.plural(4, "{0} task", "{0} tasks"), "4 tasks")

	def test_behind_is_working_days_past_the_baseline(self):
		# Baseline ended Monday Oct 26; now ends Friday Oct 30: Tuesday to Friday, four working days.
		self.assertEqual(ov.behind_days(D(2026, 10, 26), D(2026, 10, 30)), 4)
		# A weekend adds none: Friday to Sunday is no working day.
		self.assertEqual(ov.behind_days(D(2026, 10, 30), D(2026, 11, 1)), 0)
		# A holiday is not a working day either.
		self.assertEqual(ov.behind_days(D(2026, 10, 26), D(2026, 10, 30), {D(2026, 10, 28)}), 3)
		# On time, early, and missing dates are all zero.
		self.assertEqual(ov.behind_days(D(2026, 10, 30), D(2026, 10, 26)), 0)
		self.assertEqual(ov.behind_days(None, D(2026, 10, 26)), 0)
		self.assertEqual(ov.behind_days(D(2026, 10, 26), None), 0)


class TestBuildJob(unittest.TestCase):
	"""One job: phases, baseline, behind, overdue, the display rule and the project-dates fallback."""

	def build(self, rows, crewed=(), project=None, today=TODAY, **kw):
		return ov.build_job(project or PROJECT, tasks(*rows), today, set(crewed), **kw)

	ROWS = [
		row("G300", "Phase 300 — Rough-in", group=1),
		row("G100", "Phase 100 Design", group=1),
		# Phase 300: one task on time, one ending four working days after its baseline.
		row("T1", "Excavate basin", "2026-10-19", "2026-10-21", "G300", base=("2026-10-19", "2026-10-20")),
		row("T2", "Pour slab", "2026-10-21", "2026-10-30", "G300", base=("2026-10-21", "2026-10-26")),
		# Phase 100 is finished.
		row(
			"T3",
			"Site survey",
			"2026-10-05",
			"2026-10-07",
			"G100",
			status="Completed",
			base=("2026-10-05", "2026-10-07"),
		),
		# The template admin checklist, dated the project's first day, with nobody on it.
		row("T4", "Add to Accounting", "2026-10-01", "2026-10-01"),
		# Crewed work under no phase.
		row("T5", "Haul spoil", "2026-10-22", "2026-10-22", base=("2026-10-20", "2026-10-20")),
		row("T6", "Set vault", "2026-10-22", "2026-11-05", base=("2026-10-22", "2026-10-30")),
		# The status says Overdue; the last day has not come.
		row("T7", "Wire lights", "2026-10-26", "2026-10-28", "G300", status="Overdue"),
		# Open, and its last day has passed.
		row("T8", "Order pumps", "2026-09-28", "2026-10-01", "G300"),
		row("T9", "Hand-off", "2026-11-15", "2026-11-15", "G300", milestone=1),
	]

	def test_phases_come_in_number_order_with_other_work_last(self):
		job, shown = self.build(self.ROWS, crewed={"T5", "T6"})
		self.assertEqual(
			[p["subject"] for p in job["phases"]], ["Phase 100 Design", "Phase 300 — Rough-in", "Other work"]
		)
		self.assertEqual([p["number"] for p in job["phases"]], [100, 300, None])
		self.assertEqual([p["key"] for p in job["phases"]], ["G100", "G300", ""])

	def test_the_admin_checklist_is_left_out_unless_somebody_is_on_it(self):
		job, shown = self.build(self.ROWS, crewed={"T5", "T6"})
		self.assertNotIn("T4", [t["name"] for t in shown])
		job, shown = self.build(self.ROWS, crewed={"T4", "T5", "T6"})
		self.assertIn("T4", [t["name"] for t in shown])
		# An uncrewed task under no phase is the only thing that is hidden.
		self.assertEqual(
			sorted(t["name"] for t in shown), ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9"]
		)

	def test_a_phase_has_its_days_its_baseline_and_its_progress(self):
		job, _ = self.build(self.ROWS, crewed={"T5", "T6"})
		rough = next(p for p in job["phases"] if p["key"] == "G300")
		self.assertEqual((rough["start"], rough["end"]), ("2026-09-28", "2026-11-15"))
		self.assertEqual(rough["baseline"], {"start": "2026-10-19", "end": "2026-10-26"})
		self.assertEqual((rough["tasks"], rough["done"], rough["open"]), (5, 0, 5))
		design = next(p for p in job["phases"] if p["key"] == "G100")
		self.assertEqual(design["progress"], 1)
		self.assertEqual(design["done"], 1)
		# Finished work has nothing to be behind on.
		self.assertIsNone(design["slip_days"])

	def test_a_phase_is_behind_when_its_open_work_ends_later_than_its_baseline(self):
		job, _ = self.build(self.ROWS, crewed={"T5", "T6"})
		rough = next(p for p in job["phases"] if p["key"] == "G300")
		self.assertEqual(rough["slip_days"], 4)
		phase_lines = [b for b in job["behind"] if b["scope"] == "phase"]
		self.assertEqual(
			phase_lines, [{"scope": "phase", "phase": "G300", "subject": "Phase 300 — Rough-in", "days": 4}]
		)

	def test_work_under_no_phase_is_judged_task_by_task_and_only_beyond_the_threshold(self):
		job, _ = self.build(self.ROWS, crewed={"T5", "T6"})
		by_task = {b["task"]: b["days"] for b in job["behind"] if b["scope"] == "task"}
		# T5: baseline Oct 20, ends Oct 22: two working days is not MORE than two. T6: Oct 30 to Nov 5: four.
		self.assertEqual(by_task, {"T6": 4})
		# The list is worst first.
		self.assertEqual([b["days"] for b in job["behind"]], [4, 4])
		job, _ = self.build(self.ROWS, crewed={"T5", "T6"}, threshold=1)
		self.assertEqual({b["task"] for b in job["behind"] if b["scope"] == "task"}, {"T5", "T6"})

	def test_nothing_is_behind_without_a_baseline(self):
		bare = [r for r in self.ROWS]
		for r in bare:
			r["custom_baseline_start"] = r["custom_baseline_end"] = None
		job, _ = self.build(bare, crewed={"T5", "T6"})
		self.assertEqual(job["behind"], [])
		self.assertEqual(job["counts"]["baselined"], 0)
		self.assertTrue(all(p["baseline"] is None for p in job["phases"]))
		# Put them back for the other tests.
		self.ROWS[2]["custom_baseline_start"], self.ROWS[2]["custom_baseline_end"] = (
			"2026-10-19",
			"2026-10-20",
		)
		self.ROWS[3]["custom_baseline_start"], self.ROWS[3]["custom_baseline_end"] = (
			"2026-10-21",
			"2026-10-26",
		)
		self.ROWS[4]["custom_baseline_start"], self.ROWS[4]["custom_baseline_end"] = (
			"2026-10-05",
			"2026-10-07",
		)
		self.ROWS[6]["custom_baseline_start"], self.ROWS[6]["custom_baseline_end"] = (
			"2026-10-20",
			"2026-10-20",
		)
		self.ROWS[7]["custom_baseline_start"], self.ROWS[7]["custom_baseline_end"] = (
			"2026-10-22",
			"2026-10-30",
		)

	def test_overdue_is_by_end_date_never_by_the_status_value(self):
		job, _ = self.build(self.ROWS, crewed={"T5", "T6"})
		# T8 is Open and ended Oct 1. T7 says "Overdue" but ends Oct 28: not overdue.
		self.assertEqual(job["overdue"]["count"], 1)
		self.assertEqual(job["overdue"]["tasks"], ["Order pumps"])
		self.assertEqual(job["overdue"]["oldest"], "2026-10-01")
		# A finished task with a past end is history, not overdue.
		self.assertNotIn("Site survey", job["overdue"]["tasks"])

	def test_the_status_value_alone_makes_nothing_overdue(self):
		rows = [row(f"T{i}", f"Task {i}", "2026-10-20", "2026-10-22", status="Overdue") for i in range(5)]
		job, _ = self.build(rows, crewed={f"T{i}" for i in range(5)})
		self.assertEqual(job["overdue"]["count"], 0)
		self.assertIsNone(job["overdue"]["oldest"])

	def test_the_overdue_line_names_the_oldest_first_and_only_a_few(self):
		rows = [row(f"T{i}", f"Task {i}", f"2026-09-{10 + i}", f"2026-09-{10 + i}") for i in range(6)]
		job, _ = self.build(rows, crewed={r["name"] for r in rows})
		self.assertEqual(job["overdue"]["count"], 6)
		self.assertEqual(job["overdue"]["tasks"], ["Task 0", "Task 1", "Task 2"])

	def test_a_milestone_is_a_diamond_with_its_day(self):
		job, _ = self.build(self.ROWS, crewed={"T5", "T6"})
		self.assertEqual(
			job["milestones"], [{"name": "T9", "subject": "Hand-off", "date": "2026-11-15", "done": False}]
		)

	def test_the_job_spans_its_phases_and_its_baselines(self):
		job, _ = self.build(self.ROWS, crewed={"T5", "T6"})
		self.assertEqual((job["first"], job["last"]), ("2026-09-28", "2026-11-15"))
		self.assertEqual((job["base_first"], job["base_last"]), ("2026-10-05", "2026-10-30"))
		self.assertEqual(job["source"], "tasks")
		self.assertFalse(job["draft"])

	def test_a_job_with_no_dated_work_falls_back_to_the_projects_own_dates(self):
		project = dict(PROJECT, expected_start=D(2026, 11, 2), expected_end=D(2027, 2, 26))
		job, shown = self.build([row("T1", "Undated", None, None)], crewed={"T1"}, project=project)
		self.assertEqual((job["first"], job["last"], job["source"]), ("2026-11-02", "2027-02-26", "project"))
		self.assertEqual(job["phases"], [])
		self.assertEqual(len(shown), 1)

	def test_a_job_with_no_dates_at_all_has_none(self):
		job, _ = self.build([row("T1", "Undated", None, None)], crewed={"T1"})
		self.assertEqual((job["first"], job["last"], job["source"]), (None, None, None))

	def test_a_draft_schedule_is_marked(self):
		project = dict(PROJECT, schedule={"status": "Draft", "published_on": None, "baseline_set_on": None})
		job, _ = self.build(self.ROWS, crewed={"T5"}, project=project)
		self.assertTrue(job["draft"])

	def test_a_project_with_no_schedule_columns_is_not_a_draft(self):
		project = dict(PROJECT)
		project["schedule"] = None
		job, _ = self.build(self.ROWS, crewed={"T5"}, project=project)
		self.assertFalse(job["draft"])

	def test_only_open_tasks_with_an_estimate_are_candidates_for_running_over(self):
		rows = [
			row("T1", "Estimated", "2026-10-20", "2026-10-22", hours=8),
			row("T2", "Done", "2026-10-20", "2026-10-22", hours=8, status="Completed"),
			row("T3", "No estimate", "2026-10-20", "2026-10-22"),
		]
		job, _ = self.build(rows, crewed={"T1", "T2", "T3"})
		self.assertEqual([t["name"] for t in job["hours_tasks"]], ["T1"])

	def test_a_loop_in_parent_task_still_ends(self):
		rows = [
			row("G1", "Phase 100", group=1, parent="G2"),
			row("G2", "Phase 200", group=1, parent="G1"),
			row("T1", "Loopy", "2026-10-20", "2026-10-22", "G1"),
		]
		job, shown = self.build(rows)
		self.assertEqual([t["name"] for t in shown], ["T1"])


class TestRunningOver(unittest.TestCase):
	def test_clocked_hours_past_the_estimate_by_more_than_ten_percent(self):
		hours = [
			{"name": "T1", "subject": "Set vault", "planned": 8, "status": "Open"},
			{"name": "T2", "subject": "Pour slab", "planned": 8, "status": "Open"},
			{"name": "T3", "subject": "Finished", "planned": 8, "status": "Completed"},
			{"name": "T4", "subject": "Unclocked", "planned": 8, "status": "Open"},
		]
		actuals = {"T1": {"RES-1": 9.5, "RES-2": 4.5}, "T2": {"RES-1": 8.5}, "T3": {"RES-1": 40}}
		got = ov.running_over(hours, actuals)
		self.assertEqual([t["name"] for t in got], ["T1"])
		self.assertEqual((got[0]["planned"], got[0]["actual"]), (8, 14.0))

	def test_the_worst_overrun_is_first(self):
		hours = [
			{"name": "A", "subject": "A", "planned": 8, "status": "Open"},
			{"name": "B", "subject": "B", "planned": 8, "status": "Open"},
		]
		got = ov.running_over(hours, {"A": {"x": 10}, "B": {"x": 20}})
		self.assertEqual([t["name"] for t in got], ["B", "A"])


def job_with(**kw):
	base = {
		"name": "PRJ-1",
		"title": "Riverwalk Plaza",
		"behind": [],
		"overdue": {"count": 0, "oldest": None, "tasks": []},
	}
	base.update(kw)
	return base


class TestExceptionLines(unittest.TestCase):
	def test_nothing_wrong_is_an_empty_list(self):
		lines, counts, more = ov.exception_lines([job_with()], {}, [])
		self.assertEqual((lines, more), ([], 0))
		self.assertEqual(counts["lines"], 0)

	def test_a_phase_behind_says_so_in_working_days(self):
		job = job_with(behind=[{"scope": "phase", "phase": "G300", "subject": "Phase 300", "days": 4}])
		lines, counts, _ = ov.exception_lines([job], {}, None)
		self.assertEqual(lines[0]["text"], "Riverwalk Plaza: Phase 300 is 4 working days behind its baseline")
		self.assertEqual(
			(lines[0]["kind"], lines[0]["project"], lines[0]["phase"], lines[0]["days"]),
			("behind", "PRJ-1", "G300", 4),
		)
		job = job_with(behind=[{"scope": "task", "task": "T1", "subject": "Set vault", "days": 3}])
		self.assertIn("is 3 working days behind", ov.exception_lines([job], {}, None)[0][0]["text"])

	def test_running_over_names_the_hours(self):
		over = {"PRJ-1": [{"name": "T1", "subject": "Set vault", "planned": 8, "actual": 14.5}]}
		lines, counts, _ = ov.exception_lines([job_with()], over, None)
		self.assertEqual(lines[0]["text"], "Riverwalk Plaza: “Set vault” is running over (14.5h of 8h)")
		self.assertEqual(counts["over"], 1)

	def test_overdue_is_one_line_a_job_with_a_count_and_the_oldest(self):
		job = job_with(overdue={"count": 6, "oldest": "2026-10-03", "tasks": ["Set vault", "Pull wire"]})
		lines, counts, _ = ov.exception_lines([job], {}, None)
		self.assertEqual(
			lines[0]["text"],
			"Riverwalk Plaza: 6 tasks overdue (the oldest was due Oct 3: Set vault, Pull wire)",
		)
		self.assertEqual((counts["overdue"], counts["overdue_jobs"]), (6, 1))
		job = job_with(overdue={"count": 1, "oldest": "2026-10-03", "tasks": ["Set vault"]})
		self.assertIn("1 task overdue", ov.exception_lines([job], {}, None)[0][0]["text"])

	def test_conflicts_are_one_line_with_the_first_few_spelled_out(self):
		conflicts = [
			{"date": "2026-10-15", "label": "Austin", "message": "Austin: double-booked"},
			{"date": "2026-10-16", "label": "Lisa", "message": "Lisa: over her hours"},
			{"date": "2026-10-16", "label": "Nik", "message": "Nik: on a day off"},
			{"date": "2026-10-20", "label": "Kyle", "message": "Kyle: double-booked"},
		]
		lines, counts, _ = ov.exception_lines([], {}, conflicts)
		self.assertEqual(len(lines), 1)
		self.assertEqual(lines[0]["kind"], "conflicts")
		self.assertEqual(lines[0]["date"], "2026-10-15")
		self.assertIn(
			"4 unresolved conflicts in the next two weeks: Oct 15: Austin: double-booked;", lines[0]["text"]
		)
		self.assertTrue(lines[0]["text"].endswith("and 1 more"))
		self.assertIsNone(lines[0]["project"])
		self.assertEqual(counts["conflicts"], 4)

	def test_the_order_is_behind_over_conflicts_then_overdue(self):
		jobs = [
			job_with(
				name="PRJ-1",
				title="Alpha",
				behind=[{"scope": "phase", "phase": "G1", "subject": "Phase 100", "days": 3}],
				overdue={"count": 9, "oldest": "2026-10-01", "tasks": ["x"]},
			),
			job_with(
				name="PRJ-2",
				title="Beta",
				behind=[{"scope": "phase", "phase": "G2", "subject": "Phase 200", "days": 8}],
			),
		]
		over = {"PRJ-2": [{"name": "T", "subject": "Slab", "planned": 8, "actual": 20}]}
		conflicts = [{"date": "2026-10-15", "label": "A", "message": "A: double-booked"}]
		lines, _, _ = ov.exception_lines(jobs, over, conflicts)
		self.assertEqual([l["kind"] for l in lines], ["behind", "behind", "over", "conflicts", "overdue"])
		# The worst slip first.
		self.assertEqual([l["days"] for l in lines[:2]], [8, 3])

	def test_the_list_is_cut_and_the_rest_counted_while_the_counts_stay_whole(self):
		jobs = [
			job_with(
				name=f"PRJ-{i}",
				title=f"Job {i:02d}",
				overdue={"count": i + 1, "oldest": "2026-10-01", "tasks": ["x"]},
			)
			for i in range(10)
		]
		lines, counts, more = ov.exception_lines(jobs, {}, None, limit=4)
		self.assertEqual((len(lines), more), (4, 6))
		self.assertEqual(counts["lines"], 10)
		self.assertEqual(counts["overdue_jobs"], 10)
		# The most overdue first within the kind.
		self.assertEqual(lines[0]["count"], 10)


class TestApplyDrafts(unittest.TestCase):
	def overlay(self):
		return {
			"exclude": {"T1"},
			"extra": [
				(
					{
						"name": "T1",
						"project": "PRJ-1",
						"subject": "Excavate (moved)",
						"status": "Open",
						"exp_start_date": "2026-11-02",
						"exp_end_date": "2026-11-04",
						"expected_time": 8,
					},
					[{"resource": "RES-1"}],
				),
				(
					{
						"name": "draft-new:PDC-2",
						"project": "PRJ-1",
						"subject": "Brand new",
						"status": "Open",
						"exp_start_date": "2026-11-09",
						"exp_end_date": "2026-11-09",
						"expected_time": 0,
					},
					[],
				),
				(
					{
						"name": "T9",
						"project": "PRJ-OTHER",
						"subject": "Not ours",
						"exp_start_date": "2026-11-09",
					},
					[],
				),
				(
					{
						"name": "GONE",
						"project": "PRJ-1",
						"subject": "Deleted since",
						"exp_start_date": "2026-11-09",
					},
					[],
				),
			],
			"drafted": {"T1": {"credentials": []}, "draft-new:PDC-2": {"new": True, "parent_task": "G300"}},
		}

	def test_a_drafted_task_replaces_the_stored_row_and_a_new_one_joins_its_group(self):
		rows = [row("G300", "Phase 300", group=1), row("T1", "Excavate", "2026-10-19", "2026-10-21", "G300")]
		out, crewed = ov.apply_drafts(rows, self.overlay(), {"PRJ-1"})
		by_name = {r["name"]: r for r in out}
		self.assertEqual(by_name["T1"]["exp_start_date"], "2026-11-02")
		self.assertEqual(by_name["T1"]["subject"], "Excavate (moved)")
		self.assertEqual(by_name["T1"]["parent_task"], "G300")
		self.assertEqual(by_name["draft-new:PDC-2"]["parent_task"], "G300")
		self.assertEqual(by_name["draft-new:PDC-2"]["project"], "PRJ-1")
		# Another project's draft, and one for a task that is not there any more, are not applied.
		self.assertNotIn("T9", by_name)
		self.assertNotIn("GONE", by_name)
		self.assertEqual(crewed, {"T1"})
		# The stored rows are not changed in place.
		self.assertEqual(rows[1]["exp_start_date"], "2026-10-19")

	def test_a_drafted_new_task_is_on_its_phase_for_the_rollup(self):
		rows = [row("G300", "Phase 300", group=1)]
		out, crewed = ov.apply_drafts(rows, self.overlay(), {"PRJ-1"})
		job, shown = ov.build_job(PROJECT, tasks(*out), TODAY, crewed)
		self.assertEqual([p["subject"] for p in job["phases"]], ["Phase 300"])
		self.assertEqual(job["phases"][0]["start"], "2026-11-09")
		self.assertEqual([t["name"] for t in shown], ["draft-new:PDC-2"])


# ====================================================================== the read


def listed(*projects):
	return [dict(p) for p in projects]


class TestCollect(unittest.TestCase):
	def setUp(self):
		h._reset()
		h._settings()
		self.rows = [
			row("G300", "Phase 300", group=1),
			row("T1", "Excavate", "2026-10-19", "2026-10-30", "G300", base=("2026-10-19", "2026-10-26")),
			row("T2", "Late thing", "2026-09-28", "2026-10-01", "G300"),
			row("G400", "Phase 400", group=1),
			row("T3", "Draft job task", "2026-11-02", "2026-11-03", "G400"),
		]
		self.rows[1]["project"] = self.rows[2]["project"] = "PRJ-1"
		self.rows[0]["project"] = "PRJ-1"
		self.rows[3]["project"] = self.rows[4]["project"] = "PRJ-2"
		self.projects = listed(
			{
				"name": "PRJ-1",
				"title": "Riverwalk Plaza",
				"pm": "Clegg",
				"schedule": {
					"status": "Published",
					"published_on": "2026-10-01 09:00:00",
					"baseline_set_on": "2026-10-01",
				},
			},
			{
				"name": "PRJ-2",
				"title": "Tanner Park",
				"pm": "Nik",
				"schedule": {"status": "Draft", "published_on": None, "baseline_set_on": None},
			},
		)
		self.patches = [
			mock.patch.object(h.api, "_projects", lambda names: self.projects),
			mock.patch.object(
				ov, "_read_task_rows", lambda names: ([r for r in self.rows if r["project"] in names], False)
			),
			mock.patch.object(ov, "_read_expected", lambda names: {}),
			mock.patch.object(ov, "_read_crewed", lambda names: set()),
			mock.patch.object(
				ov,
				"_conflicts",
				lambda today, exclude=(), cached=True: [
					{
						"date": "2026-10-09",
						"label": "Austin",
						"message": "Austin: double-booked",
						"projects": [],
					}
				],
			),
			mock.patch.object(ov, "_actuals", lambda tasks: {}),
			mock.patch.object(h.api, "_shift_holidays", lambda: frozenset()),
			mock.patch.object(h.api, "_draft_overlay", lambda: None),
		]
		for p in self.patches:
			p.start()
		self.addCleanup(lambda: [p.stop() for p in self.patches])

	def test_every_active_job_is_a_row_in_name_order(self):
		found = ov.collect(draft=False)
		self.assertEqual([j["name"] for j in found["jobs"]], ["PRJ-1", "PRJ-2"])
		self.assertEqual([j["draft"] for j in found["jobs"]], [False, True])
		self.assertEqual(found["drafts_named"], [])

	def test_the_summary_leaves_a_draft_schedule_out_and_names_it(self):
		found = ov.collect(draft=False, published_only=True)
		self.assertEqual([j["name"] for j in found["jobs"]], ["PRJ-1"])
		self.assertEqual(found["drafts_named"], ["Tanner Park"])
		self.assertEqual(list(found["shown"]), ["PRJ-1"])

	def test_conflicts_are_not_asked_for_when_the_caller_does_not_want_them(self):
		with mock.patch.object(ov, "_conflicts", side_effect=AssertionError("asked")):
			found = ov.collect(draft=False, with_conflicts=False)
		self.assertIsNone(found["conflicts"])

	def test_the_payload_trims_to_the_window_and_counts_the_rest(self):
		found = ov.collect(draft=False)
		first, last = ov.month_window("2026-10-21", 3)
		answer = ov.overview_payload(found, first, last, 3)
		self.assertEqual((answer["start"], answer["end"]), ("2026-10-01", "2026-12-31"))
		self.assertEqual([j["name"] for j in answer["jobs"]], ["PRJ-1", "PRJ-2"])
		self.assertNotIn("hours_tasks", answer["jobs"][0])
		# A window after the work has none of it.
		first, last = ov.month_window("2027-06-01", 3)
		answer = ov.overview_payload(found, first, last, 3)
		self.assertEqual((answer["jobs"], answer["outside"]), ([], 2))

	def test_a_job_with_no_dates_is_listed_apart(self):
		self.rows = [row("T1", "Undated", None, None)]
		self.rows[0]["project"] = "PRJ-1"
		found = ov.collect(draft=False)
		first, last = ov.month_window("2026-10-21", 6)
		answer = ov.overview_payload(found, first, last, 6)
		self.assertEqual(
			answer["no_dates"],
			[
				{"name": "PRJ-1", "title": "Riverwalk Plaza", "pm": "Clegg"},
				{"name": "PRJ-2", "title": "Tanner Park", "pm": "Nik"},
			],
		)
		self.assertEqual(answer["jobs"], [])

	def test_the_exception_list_and_counts_come_with_the_answer(self):
		found = ov.collect(draft=False)
		first, last = ov.month_window("2026-10-21", 6)
		answer = ov.overview_payload(found, first, last, 6)
		kinds = [l["kind"] for l in answer["exceptions"]]
		self.assertEqual(kinds, ["behind", "conflicts", "overdue"])
		self.assertEqual(answer["counts"]["jobs"], 2)
		self.assertEqual(answer["counts"]["baselined"], 1)
		self.assertEqual(answer["counts"]["drafts"], 1)
		self.assertTrue(answer["conflicts_ok"])
		self.assertEqual(answer["behind_days"], ov.BEHIND_DAYS)

	def test_the_summary_asks_for_published_conflicts_only_and_fresh(self):
		seen = {}

		def conflicts(today, published_only=False, cached=True):
			seen.update(published_only=published_only, cached=cached)
			return []

		with mock.patch.object(ov, "_conflicts", conflicts):
			found = ov.collect(draft=False, published_only=True, cached_conflicts=False)
		self.assertEqual(seen, {"published_only": True, "cached": False})
		self.assertEqual(found["drafts_listed"], {"PRJ-2"})
		# The Overview, on screen, draws drafts and so leaves nothing out; it uses the cache.
		with mock.patch.object(ov, "_conflicts", conflicts):
			found = ov.collect(draft=False)
		self.assertEqual(seen, {"published_only": False, "cached": True})
		self.assertEqual(found["drafts_listed"], set())

	def test_a_cut_task_read_is_said_in_the_answer(self):
		with mock.patch.object(ov, "_read_task_rows", lambda names: ([], True)):
			found = ov.collect(draft=False)
		first, last = ov.month_window("2026-10-21", 6)
		self.assertTrue(ov.overview_payload(found, first, last, 6)["tasks_truncated"])
		found = ov.collect(draft=False)
		self.assertFalse(ov.overview_payload(found, first, last, 6)["tasks_truncated"])

	def test_unreadable_conflicts_leave_the_rest_of_the_answer(self):
		with mock.patch.object(ov, "_conflicts", lambda today, exclude=(), cached=True: None):
			found = ov.collect(draft=False)
		first, last = ov.month_window("2026-10-21", 6)
		answer = ov.overview_payload(found, first, last, 6)
		self.assertFalse(answer["conflicts_ok"])
		self.assertNotIn("conflicts", [l["kind"] for l in answer["exceptions"]])
		self.assertTrue(answer["exceptions"])

	def test_drafts_draw_a_draft_schedule_from_its_drafts(self):
		overlay = {
			"extra": [
				(
					{
						"name": "T3",
						"project": "PRJ-2",
						"subject": "Draft job task",
						"status": "Open",
						"exp_start_date": "2026-12-07",
						"exp_end_date": "2026-12-09",
						"expected_time": 0,
					},
					[],
				)
			],
			"drafted": {"T3": {"credentials": []}},
		}
		with mock.patch.object(h.api, "_draft_overlay", lambda: overlay):
			found = ov.collect(draft=True)
		tanner = next(j for j in found["jobs"] if j["name"] == "PRJ-2")
		self.assertEqual((tanner["first"], tanner["last"]), ("2026-12-07", "2026-12-09"))
		self.assertTrue(tanner["draft"])
		self.assertIs(found["overlay"], overlay)

	def test_a_failed_draft_read_is_logged_and_the_rest_still_answers(self):
		def boom():
			raise RuntimeError("drafts unreadable")

		with mock.patch.object(h.api, "_draft_overlay", boom):
			found = ov.collect(draft=True)
		self.assertIsNone(found["overlay"])
		self.assertEqual(len(found["jobs"]), 2)
		self.assertEqual(len(h.frappe.errors), 1)


class TestTheConflictsRead(unittest.TestCase):
	"""The costly part of a read, kept for two minutes, filtered by project, never fatal."""

	def setUp(self):
		h._reset()
		self.store = {}
		self.passes = 0
		h.frappe.cache = types.SimpleNamespace(
			get_value=lambda key: self.store.get(key),
			set_value=lambda key, value, expires_in_sec=None: self.store.update({key: value}),
		)
		self.addCleanup(lambda: delattr(h.frappe, "cache"))

		def collect(start, end, planner):
			self.passes += 1
			return [
				{
					"date": "2026-10-09",
					"resource_label": "Austin",
					"message": "Austin: double-booked",
					"acknowledged": None,
					"items": [{"project": "PRJ-1"}, {"project": "PRJ-2"}],
				},
				{
					"date": "2026-10-10",
					"resource_label": "Lisa",
					"message": "Lisa: over her hours",
					"acknowledged": None,
					"items": [{"project": "PRJ-1"}, {"doctype": "Task"}],
				},
				{
					"date": "2026-10-11",
					"resource_label": "Nik",
					"message": "Nik: kept",
					"acknowledged": {"reason": "ok"},
					"items": [],
				},
			]

		from erpnext_enhancements.api import planner_conflicts

		patch = mock.patch.object(planner_conflicts, "_collect", collect)
		patch.start()
		self.addCleanup(patch.stop)

	def test_it_is_read_once_for_a_burst_of_clicks(self):
		first = ov._conflicts(TODAY)
		second = ov._conflicts(TODAY)
		self.assertEqual(self.passes, 1)
		self.assertEqual(first, second)
		self.assertEqual([c["message"] for c in first], ["Austin: double-booked", "Lisa: over her hours"])
		self.assertEqual(first[0]["projects"], ["PRJ-1", "PRJ-2"])
		self.assertEqual(first[1]["projects"], ["PRJ-1"])

	def test_one_kept_with_a_reason_is_not_listed(self):
		self.assertNotIn("Nik: kept", [c["message"] for c in ov._conflicts(TODAY)])

	def test_a_fresh_read_is_available_and_does_not_fill_the_cache(self):
		ov._conflicts(TODAY, cached=False)
		ov._conflicts(TODAY, cached=False)
		self.assertEqual((self.passes, self.store), (2, {}))

	def test_the_summary_drops_any_conflict_touching_a_project_that_is_not_published(self):
		"""Read off each conflict's own projects, whether or not the page lists them as jobs: PRJ-2 here is a
		Draft schedule, PRJ-3 has no schedule state (reads Draft), PRJ-1 is Published."""
		h.frappe.sql_rows = {
			"FROM `tabProject` WHERE name IN": [
				{"name": "PRJ-1", "custom_schedule_status": "Published"},
				{"name": "PRJ-2", "custom_schedule_status": "Draft"},
			]
		}
		with mock.patch.object(h.api, "_has_schedule_columns", lambda: True):
			both = ov._conflicts(TODAY)
			self.assertEqual(len(both), 2)
			self.assertEqual(
				[c["message"] for c in ov._conflicts(TODAY, published_only=True)], ["Lisa: over her hours"]
			)
			h.frappe.sql_rows["FROM `tabProject` WHERE name IN"][0]["custom_schedule_status"] = ""
			self.assertEqual(ov._conflicts(TODAY, published_only=True), [])
		# One cached pass served the unfiltered list and both filtered reads.
		self.assertEqual(self.passes, 1)

	def test_before_the_schedule_columns_exist_nothing_is_dropped(self):
		with mock.patch.object(h.api, "_has_schedule_columns", lambda: False):
			self.assertEqual(len(ov._conflicts(TODAY, published_only=True)), 2)

	def test_the_published_filter_is_one_bound_query_and_a_conflict_with_no_project_survives(self):
		from erpnext_enhancements.api import planner_conflicts

		def collect(start, end, planner):
			return [
				{
					"date": "2026-10-09",
					"resource_label": "Austin",
					"message": "Austin: on a day off",
					"acknowledged": None,
					"items": [{"doctype": "Task"}],
				}
			]

		h.frappe.queries.clear()
		with (
			mock.patch.object(planner_conflicts, "_collect", collect),
			mock.patch.object(h.api, "_has_schedule_columns", lambda: True),
		):
			got = ov._conflicts(TODAY, published_only=True, cached=False)
		self.assertEqual([c["message"] for c in got], ["Austin: on a day off"])
		self.assertEqual([q for kind, q in h.frappe.queries if kind == "sql"], [])

	def test_a_cache_that_is_down_is_no_cache(self):
		def boom(*a, **k):
			raise RuntimeError("redis down")

		h.frappe.cache = types.SimpleNamespace(get_value=boom, set_value=boom)
		self.assertEqual(len(ov._conflicts(TODAY)), 2)
		self.assertEqual(self.passes, 1)

	def test_a_failed_pass_is_none_logged_and_not_cached(self):
		from erpnext_enhancements.api import planner_conflicts

		with mock.patch.object(planner_conflicts, "_collect", side_effect=RuntimeError("engine fell over")):
			self.assertIsNone(ov._conflicts(TODAY))
		self.assertEqual(len(h.frappe.errors), 1)
		self.assertEqual(self.store, {})


class TestDraftProjects(unittest.TestCase):
	"""'Is this project's schedule published?', asked of any project name, whatever list it is on."""

	ROWS = [
		{"name": "PRJ-PUB", "custom_schedule_status": "Published"},
		{"name": "PRJ-DRAFT", "custom_schedule_status": "Draft"},
		{"name": "PRJ-BLANK", "custom_schedule_status": None},
		{"name": "PRJ-EMPTY", "custom_schedule_status": ""},
		{"name": "PRJ-ODD", "custom_schedule_status": "Something else"},
	]

	def setUp(self):
		h._reset()
		h.frappe.sql_rows = {"FROM `tabProject` WHERE name IN": self.ROWS}
		patch = mock.patch.object(h.api, "_has_schedule_columns", lambda: True)
		patch.start()
		self.addCleanup(patch.stop)

	def test_draft_blank_and_unknown_read_draft_published_does_not(self):
		names = {"PRJ-PUB", "PRJ-DRAFT", "PRJ-BLANK", "PRJ-EMPTY", "PRJ-ODD"}
		self.assertEqual(ov.draft_projects(names), {"PRJ-DRAFT", "PRJ-BLANK", "PRJ-EMPTY", "PRJ-ODD"})

	def test_no_names_asks_nothing(self):
		h.frappe.queries.clear()
		self.assertEqual(ov.draft_projects(set()), set())
		self.assertEqual(ov.draft_projects([None, ""]), set())
		self.assertEqual(h.frappe.queries, [])

	def test_a_name_that_is_not_a_project_is_not_a_draft(self):
		self.assertEqual(ov.draft_projects({"PRJ-GONE"}) & {"PRJ-GONE"}, set())

	def test_before_the_columns_exist_nothing_is_a_draft_and_nothing_is_asked(self):
		h.frappe.queries.clear()
		with mock.patch.object(h.api, "_has_schedule_columns", lambda: False):
			self.assertEqual(ov.draft_projects({"PRJ-DRAFT"}), set())
		self.assertEqual(h.frappe.queries, [])

	def test_one_bound_query_per_chunk(self):
		h.frappe.queries.clear()
		many = {f"PRJ-{i:04d}" for i in range(ov.CHUNK + 1)}
		ov.draft_projects(many)
		queries = [q for kind, q in h.frappe.queries if kind == "sql"]
		self.assertEqual(len(queries), 2)
		self.assertTrue(all("WHERE name IN %(names)s" in q for q in queries))
		self.assertTrue(all("PRJ-" not in q for q in queries))


class TestTheQueries(unittest.TestCase):
	"""The reads are bound SQL: no value of the request reaches the text of a query."""

	def setUp(self):
		h._reset()

	def test_one_task_query_for_every_job_with_bound_values(self):
		h.frappe.queries.clear()
		ov._read_task_rows({"PRJ-1", "PRJ-2"})
		queries = [q for kind, q in h.frappe.queries if kind == "sql"]
		self.assertEqual(len(queries), 1)
		self.assertIn("t.project IN %(projects)s", queries[0])
		self.assertIn("NOT IN %(left_out)s", queries[0])
		self.assertIn("LIMIT %(limit)s", queries[0])
		self.assertNotIn("PRJ-", queries[0])

	def test_no_projects_asks_nothing(self):
		h.frappe.queries.clear()
		self.assertEqual(ov._read_task_rows(set()), ([], False))
		self.assertEqual(h.frappe.queries, [])

	def test_the_task_cap_is_said_not_hidden(self):
		rows = [{"name": f"T{i}", "project": "PRJ-1"} for i in range(5)]
		h.frappe.sql_rows = {"FROM `tabTask` t": rows}
		with mock.patch.object(ov, "MAX_TASKS", 4):
			got, cut = ov._read_task_rows({"PRJ-1"})
		self.assertEqual((len(got), cut), (4, True))
		with mock.patch.object(ov, "MAX_TASKS", 5):
			got, cut = ov._read_task_rows({"PRJ-1"})
		self.assertEqual((len(got), cut), (5, False))
		# It asks for one row more than it keeps, to know.
		bound = [q for kind, q in h.frappe.queries if kind == "sql"][-1]
		self.assertIn("LIMIT %(limit)s", bound)

	def test_the_milestone_column_comes_from_the_planners_own_check(self):
		source = OVERVIEW_PATH.read_text(encoding="utf-8")
		self.assertIn('pp._task_has_column("is_milestone")', source)
		self.assertNotIn("def _has_column", source)

	def test_the_source_never_formats_a_value_into_sql(self):
		source = OVERVIEW_PATH.read_text(encoding="utf-8")
		for sql in re.findall(r'f?"""\s*SELECT.*?"""', source, flags=re.S):
			self.assertNotRegex(sql, r"\{(?!milestone\}|baseline\})", sql)
		# Frappe 16 refuses a SQL function written as a string field in get_all.
		self.assertNotRegex(
			source, r'fields=\[[^\]]*"(?:count|sum|max|min)\(', "get_all with a function string"
		)


class TestEndpoints(unittest.TestCase):
	def setUp(self):
		h._reset()
		h._settings()
		self.patches = [
			mock.patch.object(h.api, "_projects", lambda names: []),
			mock.patch.object(ov, "_read_task_rows", lambda names: ([], False)),
			mock.patch.object(ov, "_read_expected", lambda names: {}),
			mock.patch.object(ov, "_read_crewed", lambda names: set()),
			mock.patch.object(ov, "_conflicts", lambda today, exclude=(), cached=True: []),
			mock.patch.object(h.api, "_shift_holidays", lambda: frozenset()),
			mock.patch.object(
				h.api,
				"_draft_overlay",
				lambda: {"extra": [], "drafted": {}, "stale": [], "phases": [], "projects": {}},
			),
		]
		for p in self.patches:
			p.start()
		self.addCleanup(lambda: [p.stop() for p in self.patches])

	def test_the_overview_holds_the_planner_role_gate(self):
		h.frappe.roles = ["Sales User"]
		with self.assertRaises(h._PermissionError):
			ov.get_overview()
		with self.assertRaises(h._PermissionError):
			ov.weekly_summary_html()

	def test_the_overview_answers_for_a_planner_with_the_drafts_block(self):
		answer = ov.get_overview("2026-10-21", 6)
		self.assertEqual((answer["start"], answer["end"], answer["months"]), ("2026-10-01", "2027-03-31", 6))
		self.assertEqual(answer["choices"], [3, 6, 9, 12])
		self.assertTrue(answer["can_edit"])
		self.assertEqual(answer["drafts"]["count"], 0)

	def test_draft_zero_reads_committed_state_and_sends_no_drafts_block(self):
		with mock.patch.object(h.api, "_draft_overlay", side_effect=AssertionError("read")):
			answer = ov.get_overview("2026-10-21", 6, draft=0)
		self.assertNotIn("drafts", answer)

	def test_a_wild_months_value_is_the_default(self):
		self.assertEqual(ov.get_overview("2026-10-21", "forty")["months"], 6)

	def test_a_start_that_is_not_a_day_is_a_clear_error_not_a_500(self):
		for bad in ("garbage", "2026-13-45", "not-a-date", "20261021x", ["2026-10-21"], 7):
			with self.assertRaises(h._Throw, msg=repr(bad)) as caught:
				ov.get_overview(bad)
			self.assertIn("YYYY-MM-DD", str(caught.exception))
			self.assertNotIn("garbage", str(caught.exception))

	def test_a_start_that_is_blank_is_today(self):
		for blank in (None, "", "null", "undefined", "  "):
			answer = ov.get_overview(blank, 3)
			self.assertEqual((answer["start"], answer["end"]), ("2026-10-01", "2026-12-31"), repr(blank))

	def test_a_start_with_a_time_on_it_is_its_day(self):
		self.assertEqual(ov.get_overview("2026-11-20 14:30:00", 3)["start"], "2026-11-01")

	def test_the_preview_is_for_managers(self):
		h.frappe.roles = ["Projects User"]
		with self.assertRaises(h._PermissionError):
			ov.send_summary_preview()

	def test_the_endpoints_are_whitelisted_and_the_preview_is_post_only(self):
		source = OVERVIEW_PATH.read_text(encoding="utf-8")
		self.assertIn("@frappe.whitelist()\ndef get_overview(", source)
		self.assertIn("@frappe.whitelist()\ndef weekly_summary_html(", source)
		self.assertIn('@frappe.whitelist(methods=["POST"])\ndef send_summary_preview(', source)


class TestWiring(unittest.TestCase):
	def test_the_suite_has_its_own_ci_step(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("run: python -m unittest erpnext_enhancements.tests.test_planner_overview -v", ci)

	def test_the_module_has_a_row_in_the_api_readme(self):
		readme = (APP / "api/README.md").read_text(encoding="utf-8")
		self.assertIn("`planner_overview.py`", readme)


if __name__ == "__main__":
	unittest.main()
