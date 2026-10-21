# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""P5 of the Planner UX redesign: the Lookahead's server side (``api/planner_lookahead.py``,
``docs/planner-ux-redesign.md`` §4.2; Nik's D2, 2026-10-21).

What this pins:

* **The window is whole weeks from the site's first weekday** (Sunday on production): 2, 3, 4 or 6
  weeks, 3 for anything else, so a lookahead opened on a Wednesday still starts on its Sunday.
* **The bars are get_planner's own cards.** ``get_lookahead`` is ``get_planner`` over the window, with
  ``draft`` and ``all_tasks`` passed through untouched (the P2 display rule and P3's drafts are its),
  plus the tree. Each card gains ``phase``, ``baseline`` and ``span_days``; nothing else of it changes.
* **Grouping:** projects, then phases in phase-number order (a task's nearest ancestor whose subject
  starts with a phase number), then tasks by start; work under no phase is *Other work*, last.
  Undated work is the project's *Not scheduled* line. Projects with work in the window come first,
  earliest first, then those with only undated work, then the no-task projects.
* **Filters** (``project``, ``pm``, ``group``) narrow the tree only, for a caller that asks; ``group``
  keeps the tasks with someone of that group on the crew and drops the no-task projects.
* **Schedule state and Fountain Move** ride along: a project's ``schedule`` is ``get_planner``'s (P3),
  and a project with a move task (whose card ``get_planner`` already made read-only) says so.
* **Baselines are read only once P3's columns exist**, and a site without them still answers.

Bench-free. Reuses ``test_planner_phase3b``'s ``frappe`` stub; ``get_planner`` itself is replaced by a
recorder, since its own suites cover it.

Run: python -m unittest erpnext_enhancements.tests.test_planner_lookahead
"""

import ast
import datetime
import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.tests import test_planner_phase3b as h

APP = Path(__file__).resolve().parents[1]
MODULE_PATH = APP / "api/planner_lookahead.py"
API_README = APP / "api/README.md"
CI = REPO_ROOT / ".github/workflows/ci.yml"

D = datetime.date
la = None


def setUpModule():
	global la
	h.setUpModule()
	la = importlib.import_module("erpnext_enhancements.api.planner_lookahead")


def tearDownModule():
	h.tearDownModule()


def _card(name, project="PRJ-1", start=None, end=None, phase=None, crew=(), **extra):
	return dict(
		{
			"name": name,
			"subject": extra.pop("subject", name),
			"project": project,
			"start": start,
			"end": end or start,
			"phase": phase,
			"crew": [{"resource": r} for r in crew],
			"crew_size": 0,
			"short": 0,
		},
		**extra,
	)


# ====================================================================== pure helpers


class TestTheWindow(unittest.TestCase):
	def test_the_weeks_are_two_three_four_or_six_and_three_otherwise(self):
		for value, want in ((2, 2), ("4", 4), (6, 6), (3, 3), (5, 3), (0, 3), (None, 3), ("x", 3), ("", 3)):
			with self.subTest(value=value):
				self.assertEqual(la.lookahead_weeks(value), want)

	def test_a_wednesday_opens_on_its_sunday_for_three_weeks(self):
		first, last = la.lookahead_window("2026-10-21", 3, "Sunday")
		self.assertEqual((first, last), (D(2026, 10, 18), D(2026, 11, 7)))
		self.assertEqual(first.weekday(), 6)
		self.assertEqual((last - first).days + 1, 21)

	def test_a_sunday_is_its_own_first_day_and_the_week_number_works_too(self):
		self.assertEqual(la.lookahead_window("2026-10-18", 3, 6)[0], D(2026, 10, 18))
		self.assertEqual(la.lookahead_window("2026-10-17", 3, 6)[0], D(2026, 10, 11))

	def test_a_monday_site_starts_on_monday(self):
		self.assertEqual(la.lookahead_window("2026-10-21", 3, "Monday"), (D(2026, 10, 19), D(2026, 11, 8)))

	def test_other_lengths(self):
		self.assertEqual(la.lookahead_window("2026-10-21", 2, "Sunday")[1], D(2026, 10, 31))
		self.assertEqual(la.lookahead_window("2026-10-21", 6, "Sunday")[1], D(2026, 11, 28))
		self.assertEqual(la.lookahead_window("2026-10-21", 9, "Sunday")[1], D(2026, 11, 7))


class TestSpanDays(unittest.TestCase):
	def test_working_days_both_ends_counted(self):
		self.assertEqual(la.span_days("2026-10-19", "2026-10-23"), 5)  # Mon-Fri
		self.assertEqual(la.span_days("2026-10-23", "2026-10-26"), 2)  # Fri-Mon over a weekend
		self.assertEqual(la.span_days("2026-10-21", "2026-10-21"), 1)
		self.assertEqual(la.span_days("2026-10-21", None), 1)

	def test_a_weekend_only_task_is_its_calendar_days_never_zero(self):
		self.assertEqual(la.span_days("2026-10-24", "2026-10-25"), 2)
		self.assertEqual(la.span_days("2026-10-24", "2026-10-24"), 1)

	def test_no_start_is_zero_and_an_end_before_the_start_is_one_day(self):
		self.assertEqual(la.span_days(None, "2026-10-21"), 0)
		self.assertEqual(la.span_days("2026-10-21", "2026-10-19"), 1)


class TestNearestPhase(unittest.TestCase):
	PARENTS = {"T1": "G1", "G1": "P300", "P300": None, "T2": "P400", "T3": "G9", "G9": None}
	SUBJECTS = {"G1": "Basin", "P300": "Phase 300 — Rough-in", "P400": "400 Mechanical", "G9": "Admin"}

	def test_the_nearest_phase_ancestor_through_plain_groups(self):
		self.assertEqual(la.nearest_phase("T1", self.PARENTS, self.SUBJECTS), "P300")
		self.assertEqual(la.nearest_phase("T2", self.PARENTS, self.SUBJECTS), "P400")

	def test_no_phase_above_is_none(self):
		self.assertIsNone(la.nearest_phase("T3", self.PARENTS, self.SUBJECTS))
		self.assertIsNone(la.nearest_phase("UNKNOWN", self.PARENTS, self.SUBJECTS))

	def test_a_phase_group_is_never_its_own_phase(self):
		self.assertIsNone(la.nearest_phase("P300", self.PARENTS, self.SUBJECTS))

	def test_a_loop_in_parent_task_ends(self):
		self.assertIsNone(la.nearest_phase("A", {"A": "B", "B": "A"}, {"A": "x", "B": "y"}))


class TestTheTree(unittest.TestCase):
	PROJECTS = {
		"PRJ-1": {"name": "PRJ-1", "title": "Riverwalk", "pm": "Clegg", "schedule": {"status": "Published"}},
		"PRJ-2": {"name": "PRJ-2", "title": "Tanner Park", "pm": "Nik", "schedule": {"status": "Draft"}},
		"PRJ-3": {"name": "PRJ-3", "title": "Myers-Roy", "pm": "Nik", "schedule": {"status": "Draft"}},
		"PRJ-4": {"name": "PRJ-4", "title": "Aspen", "pm": "Clegg", "schedule": {"status": "Draft"}},
	}
	PHASES = {"P300": "Phase 300 — Rough-in", "P100": "Phase 100 — Design", "P400": "Phase 400"}

	def cards(self):
		return [
			_card("pour", "PRJ-1", "2026-10-27", phase="P300", crew=("RES-1",)),
			_card("excavate", "PRJ-1", "2026-10-20", "2026-10-22", phase="P300", crew=("RES-2",)),
			_card("design", "PRJ-1", "2026-10-19", phase="P100"),
			_card("misc", "PRJ-1", "2026-10-18", phase=None),
			_card("sketch", "PRJ-2", "2026-10-19", phase="P100", crew=("RES-2",)),
		]

	def undated(self):
		return [
			_card("wire", "PRJ-1", phase="P400"),
			_card("vault", "PRJ-4", phase=None, crew=("RES-1",)),
		]

	def tree(self, **kwargs):
		return la.lookahead_tree(
			self.cards(),
			self.undated(),
			self.PROJECTS,
			self.PHASES,
			kwargs.pop("empty", [{"name": "PRJ-3", "title": "Myers-Roy", "pm": "Nik"}]),
			kwargs.pop("filters", None),
			kwargs.pop("groups", {"RES-1": "Field", "RES-2": "PM"}),
		)

	def test_projects_with_work_first_by_their_first_bar_then_undated_only_then_no_tasks(self):
		tree = self.tree()
		self.assertEqual([p["name"] for p in tree], ["PRJ-1", "PRJ-2", "PRJ-4", "PRJ-3"])
		self.assertEqual(tree[0]["first"], "2026-10-18")
		self.assertIsNone(tree[2]["first"])
		self.assertEqual([p["no_tasks"] for p in tree], [False, False, False, True])

	def test_phases_by_number_other_work_last_and_tasks_by_start(self):
		project = self.tree()[0]
		self.assertEqual(
			[(f["name"], f["subject"], f["number"]) for f in project["phases"]],
			[
				("P100", "Phase 100 — Design", 100),
				("P300", "Phase 300 — Rough-in", 300),
				("P400", "Phase 400", 400),
				(None, "Other work", None),
			],
		)
		self.assertEqual(project["phases"][1]["tasks"], ["excavate", "pour"])
		self.assertEqual(project["phases"][2]["not_scheduled"], ["wire"])
		self.assertEqual(project["not_scheduled"], ["wire"])
		self.assertEqual(project["counts"], {"tasks": 4, "not_scheduled": 1})

	def test_the_project_row_carries_its_title_pm_and_schedule(self):
		project = self.tree()[1]
		self.assertEqual((project["title"], project["pm"]), ("Tanner Park", "Nik"))
		self.assertEqual(project["schedule"], {"status": "Draft"})
		self.assertEqual(self.tree()[3]["schedule"], {"status": "Draft"})

	def test_a_no_task_project_with_cards_is_not_listed_twice(self):
		tree = self.tree(empty=[{"name": "PRJ-1", "title": "Riverwalk"}, {"name": "PRJ-3", "title": "Myers-Roy"}])
		self.assertEqual([p["name"] for p in tree].count("PRJ-1"), 1)
		self.assertFalse(tree[0]["no_tasks"])

	def test_the_project_and_pm_filters(self):
		self.assertEqual([p["name"] for p in self.tree(filters={"project": "PRJ-2"})], ["PRJ-2"])
		self.assertEqual([p["name"] for p in self.tree(filters={"pm": "Nik"})], ["PRJ-2", "PRJ-3"])

	def test_the_group_filter_keeps_that_groups_crewed_work_and_drops_no_task_projects(self):
		tree = self.tree(filters={"group": "Field"})
		self.assertEqual([p["name"] for p in tree], ["PRJ-1", "PRJ-4"])
		self.assertEqual([t for f in tree[0]["phases"] for t in f["tasks"]], ["pour"])
		self.assertEqual(tree[1]["not_scheduled"], ["vault"])

	def test_a_fountain_move_project_says_so(self):
		cards = self.cards() + [_card("move", "PRJ-2", "2026-10-21", fountain_move=True, movable=False)]
		tree = la.lookahead_tree(cards, [], self.PROJECTS, self.PHASES)
		self.assertTrue(next(p for p in tree if p["name"] == "PRJ-2")["fountain_move"])
		self.assertFalse(next(p for p in tree if p["name"] == "PRJ-1")["fountain_move"])

	def test_a_card_without_a_project_is_left_out(self):
		tree = la.lookahead_tree([_card("loose", None, "2026-10-21")], [], {}, {})
		self.assertEqual(tree, [])


# ====================================================================== the endpoint


class TestGetLookahead(h._Base):
	def setUp(self):
		super().setUp()
		self.frappe = h.frappe
		self.calls = []
		self.answer = {
			"tasks": [
				_card("T1", "PRJ-1", "2026-10-20", "2026-10-23", crew=("RES-1",)),
				_card("T2", "PRJ-1", "2026-10-26", crew=()),
				_card("draft-new:abc", "PRJ-1", "2026-10-27", parent_task="P300"),
			],
			"unscheduled": [_card("T3", "PRJ-2"), _card("T4", "PRJ-2", crew=("RES-2",))],
			"projects": [
				{"name": "PRJ-1", "title": "Riverwalk", "pm": "Clegg", "schedule": {"status": "Published"}},
				{"name": "PRJ-2", "title": "Tanner", "pm": "Nik", "schedule": {"status": "Draft"}},
			],
			"resources": [{"name": "RES-1", "group": "Field"}, {"name": "RES-2", "group": "PM"}],
			"needs_crew": ["T2"],
		}
		for card in self.answer["tasks"] + self.answer["unscheduled"]:
			card.pop("phase")
		self.frappe.tables["Task"] = [
			{"name": "T1", "subject": "Excavate", "parent_task": "G1", "custom_baseline_start": D(2026, 10, 19), "custom_baseline_end": D(2026, 10, 21)},
			{"name": "T2", "subject": "Pour", "parent_task": "P300"},
			{"name": "T3", "subject": "Wire", "parent_task": None},
			{"name": "T4", "subject": "Vault", "parent_task": "P400"},
			{"name": "G1", "subject": "Basin", "parent_task": "P300"},
			{"name": "P300", "subject": "Phase 300 — Rough-in", "parent_task": None},
			{"name": "P400", "subject": "Phase 400 — Mechanical", "parent_task": None},
		]
		self.frappe.db.has_column = lambda doctype, column: True

		def get_planner(start, end, draft=0, all_tasks=0):
			self.calls.append({"start": start, "end": end, "draft": draft, "all_tasks": all_tasks})
			return self.answer

		self.patches += [
			mock.patch.object(h.api, "get_planner", get_planner),
			mock.patch.object(h.api, "_site_first_weekday", lambda: 6),
			mock.patch.object(la, "_empty_projects", lambda: [{"name": "PRJ-9", "title": "Empty", "pm": "Nik"}]),
		]
		for patch in self.patches[-3:]:
			patch.start()

	def test_it_is_get_planner_over_the_window_with_draft_and_all_tasks_passed_through(self):
		result = la.get_lookahead("2026-10-21", weeks="3", draft=1, all_tasks="1")
		self.assertEqual(self.calls, [{"start": "2026-10-18", "end": "2026-11-07", "draft": 1, "all_tasks": "1"}])
		self.assertIs(result, self.answer)
		self.assertEqual(
			{k: result["lookahead"][k] for k in ("start", "end", "weeks", "choices")},
			{"start": "2026-10-18", "end": "2026-11-07", "weeks": 3, "choices": [2, 3, 4, 6]},
		)

	def test_no_start_is_today_and_an_unknown_length_is_three_weeks(self):
		la.get_lookahead(None, weeks="17")
		self.assertEqual(self.calls[-1]["start"], "2026-10-04")  # TODAY is Thursday 2026-10-08
		self.assertEqual(self.calls[-1]["end"], "2026-10-24")

	def test_each_card_gains_its_phase_baseline_and_size(self):
		result = la.get_lookahead("2026-10-21")
		cards = {c["name"]: c for c in result["tasks"] + result["unscheduled"]}
		self.assertEqual({n: c["phase"] for n, c in cards.items()}, {"T1": "P300", "T2": "P300", "draft-new:abc": "P300", "T3": None, "T4": "P400"})
		self.assertEqual(cards["T1"]["baseline"], {"start": "2026-10-19", "end": "2026-10-21"})
		self.assertIsNone(cards["T2"]["baseline"])
		self.assertEqual((cards["T1"]["span_days"], cards["T2"]["span_days"], cards["T3"]["span_days"]), (4, 1, 0))

	def test_the_tree_and_needs_crew(self):
		look = la.get_lookahead("2026-10-21")["lookahead"]
		self.assertEqual([p["name"] for p in look["projects"]], ["PRJ-1", "PRJ-2", "PRJ-9"])
		phases = look["projects"][1]["phases"]
		self.assertEqual([(f["name"], f["subject"]) for f in phases], [("P400", "Phase 400 — Mechanical"), (None, "Other work")])
		# The dated tasks get_planner found short, and the undated ones nobody is on.
		self.assertEqual(look["needs_crew"], ["T2", "T3"])

	def test_the_filters_reach_the_tree_only(self):
		result = la.get_lookahead("2026-10-21", project="PRJ-2")
		self.assertEqual([p["name"] for p in result["lookahead"]["projects"]], ["PRJ-2"])
		self.assertEqual(len(result["tasks"]), 3)
		result = la.get_lookahead("2026-10-21", group="Field")
		self.assertEqual([p["name"] for p in result["lookahead"]["projects"]], ["PRJ-1"])

	def test_before_the_p3_columns_no_baseline_is_asked_for(self):
		self.frappe.db.has_column = lambda doctype, column: False
		result = la.get_lookahead("2026-10-21")
		fields = [q[1]["fields"] for q in self.frappe.queries if q[0] == "Task"]
		self.assertTrue(fields)
		self.assertTrue(all("custom_baseline_start" not in f for f in fields))
		self.assertTrue(all(c["baseline"] is None for c in result["tasks"]))

	def test_a_drafted_new_task_is_never_read_as_a_task(self):
		la.get_lookahead("2026-10-21")
		asked = [n for q in self.frappe.queries if q[0] == "Task" for n in q[1]["filters"]["name"][1]]
		self.assertNotIn("draft-new:abc", asked)

	def test_the_no_task_projects_never_break_the_lookahead(self):
		self.patches[-1].stop()
		self.patches.pop()
		with mock.patch(
			"erpnext_enhancements.api.planner_actions.get_projects_needing_tasks", side_effect=RuntimeError("boom")
		):
			look = la.get_lookahead("2026-10-21")["lookahead"]
		self.assertEqual([p["name"] for p in look["projects"]], ["PRJ-1", "PRJ-2"])
		self.assertTrue(self.frappe.errors)


# ====================================================================== wiring


class TestWiring(unittest.TestCase):
	def test_it_is_a_whitelisted_read(self):
		tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
		fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_lookahead")
		deco = fn.decorator_list[0]
		self.assertEqual(getattr(deco.func, "attr", None), "whitelist")
		self.assertEqual(deco.keywords, [], "a read: no POST-only methods")

	def test_it_reads_through_get_planner_which_holds_the_role_gate(self):
		code = MODULE_PATH.read_text(encoding="utf-8")
		self.assertIn("result = pp.get_planner(str(first), str(last), draft=draft, all_tasks=all_tasks)", code)
		self.assertNotIn("frappe.db.sql", code)

	def test_the_readme_and_ci_know_it(self):
		self.assertIn("planner_lookahead.py", API_README.read_text(encoding="utf-8"))
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_planner_lookahead -v", CI.read_text(encoding="utf-8"))


if __name__ == "__main__":
	unittest.main()
