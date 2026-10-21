# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""P6 of the Planner UX redesign: the Gantt tab's server side (``api/planner_gantt.py`` and the link
writes in ``api/planner_actions.py``; ``docs/planner-ux-redesign.md`` §4.3).

What this pins:

* **The feed is one project's whole schedule, as planner cards.** ``get_project_gantt`` reads the
  project's Task rows itself, never ``get_planner`` (which computes everyone's days over 100 days at
  most). The D6 display rule is the planner's (phase and crew work; everything with ``all_tasks``), a
  group is drawn only above something shown, finished work stays on the chart but cannot move, and
  no ``booked`` figure is claimed, because nothing spread the hours over days.
* **Drafts show.** A drafted task shows its drafted days and crew (another scheduler's is named); a
  drafted new task shows under the group it is filed under, with the tasks it waits on; the strip's
  ``drafts`` block is ``get_planner``'s, for every project.
* **A bare call is the picker** (the project list only), a project that is gone or not the caller's
  to read is refused, and the planner's role gate holds.
* **Links go through the planner's rules.** ``add_dependency`` loads the task as a save does (role,
  write permission, out-of-date check), refuses a draft, a group, another project's task, itself and a
  loop, asks for a reason when the task would start before the other ends (on their drafted days
  where a Draft row moves either), and saves with ERPNext's calendar-day push held back: no date
  changed. ``remove_dependency`` is the same without the question. A Draft row of the task drafted
  against it as it was moves on to the save rather than going out of date.

Bench-free. Reuses ``test_planner_phase3b``'s ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_planner_gantt
"""

import ast
import importlib
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
GANTT_PATH = APP / "api/planner_gantt.py"
ACTIONS_PATH = APP / "api/planner_actions.py"
CI = REPO_ROOT / ".github/workflows/ci.yml"

pg = None
actions = None


def setUpModule():
	global pg, actions
	h.setUpModule()
	h.frappe.ValidationError = getattr(h.frappe, "ValidationError", h._Throw)
	pg = importlib.import_module("erpnext_enhancements.api.planner_gantt")
	actions = importlib.import_module("erpnext_enhancements.api.planner_actions")


def tearDownModule():
	h.tearDownModule()


# ====================================================================== pure helpers


class TestTheTree(unittest.TestCase):
	GROUPS = {
		"G-ROOT": {"subject": "Riverwalk build", "parent_task": None},
		"G-300": {"subject": "Phase 300 Rough-in", "parent_task": "G-ROOT"},
		"G-100": {"subject": "100 Design", "parent_task": None},
		"G-ADMIN": {"subject": "Billing", "parent_task": None},
	}

	def test_a_group_is_drawn_only_above_something_shown(self):
		parents = {"T1": "G-300", "T2": "G-100", "T9": "G-ADMIN"}
		drawn = pg.drawn_groups(self.GROUPS, parents, ["T1", "T2"])
		self.assertEqual([g["name"] for g in drawn], ["G-100", "G-300", "G-ROOT"])
		self.assertEqual({g["name"]: g["parent"] for g in drawn}, {"G-100": None, "G-300": "G-ROOT", "G-ROOT": None})
		self.assertEqual({g["name"]: g["number"] for g in drawn}, {"G-100": 100, "G-300": 300, "G-ROOT": None})

	def test_phases_first_by_number_then_the_rest_by_subject(self):
		groups = {
			"A": {"subject": "Zeta work", "parent_task": None},
			"B": {"subject": "Phase 400", "parent_task": None},
			"C": {"subject": "alpha work", "parent_task": None},
			"D": {"subject": "Phase 200", "parent_task": None},
		}
		parents = {"T-A": "A", "T-B": "B", "T-C": "C", "T-D": "D"}
		self.assertEqual([g["name"] for g in pg.drawn_groups(groups, parents, list(parents))], ["D", "B", "C", "A"])

	def test_a_parent_that_is_not_one_of_the_projects_groups_puts_the_task_on_top(self):
		parents = {"T1": "SOMEONE-ELSES"}
		self.assertEqual(pg.drawn_groups(self.GROUPS, parents, ["T1"]), [])
		self.assertIsNone(pg.gantt_parent("T1", parents, set()))
		self.assertEqual(pg.gantt_parent("T1", {"T1": "G-300"}, {"G-300"}), "G-300")

	def test_a_loop_in_parent_task_still_ends(self):
		groups = {"A": {"subject": "A", "parent_task": "B"}, "B": {"subject": "B", "parent_task": "A"}}
		self.assertEqual(sorted(g["name"] for g in pg.drawn_groups(groups, {"T": "A"}, ["T"])), ["A", "B"])

	def test_the_span_takes_the_baselines_in(self):
		cards = [
			{"start": "2026-10-20", "end": "2026-10-23", "baseline": {"start": "2026-10-12", "end": "2026-10-14"}},
			{"start": "2026-11-02", "end": None},
		]
		self.assertEqual(pg.project_span(cards), ("2026-10-12", "2026-11-02"))
		self.assertEqual(pg.project_span([]), (None, None))

	def test_the_drafts_block_is_get_planners_own(self):
		self.assertNotIn("def drafts_summary", GANTT_PATH.read_text(encoding="utf-8"))
		self.assertIn('result["drafts"] = pp.drafts_block(overlay)', GANTT_PATH.read_text(encoding="utf-8"))
		overlay = {
			"drafted": {"T1": {"credentials": []}, "draft-new:PDC-2": {"new": True}},
			"phases": [{"name": "draft-new:PDC-3"}],
			"stale": [{"task": "T9"}],
			"projects": {"PRJ-1": {"count": 3, "shared": 1}},
		}
		theirs = h.api._with_drafts({"tasks": [], "unscheduled": [], "needs_crew": []}, overlay, "2026-10-01", "2026-10-31")
		self.assertEqual(theirs["drafts"]["count"], 4)
		self.assertEqual(h.api.drafts_block(overlay), theirs["drafts"])


class TestLinkRules(unittest.TestCase):
	ROWS = {
		"T1": {"project": "PRJ-1", "is_group": 0, "subject": "Excavate"},
		"T2": {"project": "PRJ-1", "is_group": 0, "subject": "Set forms"},
		"T3": {"project": "PRJ-2", "is_group": 0, "subject": "Elsewhere"},
		"G1": {"project": "PRJ-1", "is_group": 1, "subject": "Phase 300"},
	}

	def problem(self, task, depends_on, waiting=()):
		return actions.link_problem(task, depends_on, self.ROWS, waiting)

	def test_a_plain_link_is_fine(self):
		self.assertIsNone(self.problem("T2", "T1"))

	def test_what_is_refused(self):
		self.assertIn("still a draft", self.problem("draft-new:PDC-1", "T1"))
		self.assertIn("still a draft", self.problem("T2", "draft-new:PDC-1"))
		self.assertIn("itself", self.problem("T1", "T1"))
		self.assertIn("same project", self.problem("T2", "T3"))
		self.assertIn("phase or group", self.problem("T2", "G1"))
		self.assertIn("phase or group", self.problem("G1", "T2"))
		self.assertIn("not found", self.problem("T2", "T-GONE"))

	def test_a_loop_is_named(self):
		# T1 already waits on T2 (it is among those waiting on T2), so T2 cannot wait on T1.
		text = self.problem("T2", "T1", waiting={"T1"})
		self.assertIn("loop", text)
		self.assertIn("Excavate", text)


# ====================================================================== the feed


def _row(name, parent=None, group=0, start=None, end=None, status="Open", **extra):
	return dict(
		{
			"name": name,
			"subject": extra.pop("subject", name),
			"project": "PRJ-1",
			"project_title": "Riverwalk",
			"project_type": "Build",
			"status": status,
			"exp_start_date": start,
			"exp_end_date": end or start,
			"expected_time": 0,
			"color": None,
			"modified": "2026-10-08 09:00:00",
			"parent_task": parent,
			"is_group": group,
			"custom_start_datetime": None,
			"custom_end_datetime": None,
			"custom_rental_booking": None,
			"custom_rental_task_kind": None,
			"custom_crew_size": 0,
			"custom_tentative": 0,
		},
		**extra,
	)


ROWS = [
	_row("G-300", group=1, subject="Phase 300 Rough-in"),
	_row("G-ADMIN", group=1, subject="Billing"),
	_row("T1", "G-300", start="2026-10-20", end="2026-10-23", subject="Excavate", custom_baseline_start="2026-10-19", custom_baseline_end="2026-10-21"),
	_row("T2", "G-300", start="2026-10-26", end="2026-10-27", subject="Set forms"),
	_row("T3", "G-300", start="2026-10-01", end="2026-10-02", status="Completed", subject="Survey"),
	_row("T4", "G-300", subject="Pull wire"),
	_row("ADMIN-1", "G-ADMIN", start="2026-10-20", subject="Progress payment"),
	_row("CREWED", None, start="2026-10-21", subject="Crewed, under no phase"),
]


class _Feed(unittest.TestCase):
	def setUp(self):
		h._reset()
		h.frappe.tables["Project"] = [{"name": "PRJ-1"}]
		h.frappe.tables["Planner Resource"] = [dict(p) for p in h.PEOPLE]
		self.overlay = None
		self.under = {"T1", "T2", "T3", "T4"}
		pp = h.api
		engine = h.engine
		crews = {"CREWED": [h._Doc(resource="RES-1", hours=0, is_lead=1, resource_name="Austin Healey")]}
		patches = [
			mock.patch.object(pg, "_project_rows", lambda project: [dict(r) for r in ROWS]),
			mock.patch.object(engine, "read_crews", lambda names: (crews, {})),
			mock.patch.object(engine, "tasks_under_a_phase", lambda names: {n for n in names if n in self.under}),
			mock.patch.object(engine, "read_equipment", lambda names: {}),
			mock.patch.object(engine, "get_settings", lambda: {"default_day_hours": 8, "maintenance_visit_hours": 2}),
			mock.patch.object(pp, "_credentials", lambda names: {}),
			mock.patch.object(pp, "_dependencies", lambda names: {"T2": ["T1"]}),
			mock.patch.object(pp, "_task_rows", lambda names: {n: _row(n) for n in names}),
			mock.patch.object(pp, "_projects", lambda names: [{"name": "PRJ-1", "title": "Riverwalk", "schedule": {"status": "Published"}}]),
			mock.patch.object(pp, "_draft_overlay", lambda: self.overlay or {"exclude": set(), "extra": [], "drafted": {}, "stale": [], "phases": [], "projects": {}}),
			mock.patch.object(pg.planner_tracking, "task_actuals", lambda names: ({}, {})),
			mock.patch.object(pg.fountain_move_jobs, "mark_read_only", lambda cards: None),
		]
		for patch in patches:
			patch.start()
			self.addCleanup(patch.stop)

	def feed(self, **kwargs):
		return pg.get_project_gantt("PRJ-1", **kwargs)


class TestTheFeed(_Feed):
	def test_phase_and_crew_work_only_unless_all_tasks(self):
		answer = self.feed()
		names = [c["name"] for c in answer["tasks"] + answer["unscheduled"]]
		self.assertNotIn("ADMIN-1", names)
		self.assertEqual(sorted(names), ["CREWED", "T1", "T2", "T3", "T4"])
		self.assertEqual(answer["hidden_tasks"], 1)
		everything = self.feed(all_tasks=1)
		self.assertIn("ADMIN-1", [c["name"] for c in everything["tasks"]])
		self.assertEqual(everything["hidden_tasks"], 0)
		self.assertIn("G-ADMIN", [g["name"] for g in everything["gantt"]["groups"]])

	def test_groups_only_above_what_is_shown(self):
		answer = self.feed()
		self.assertEqual([g["name"] for g in answer["gantt"]["groups"]], ["G-300"])
		by = {c["name"]: c for c in answer["tasks"] + answer["unscheduled"]}
		self.assertEqual(by["T1"]["gantt_parent"], "G-300")
		self.assertIsNone(by["CREWED"]["gantt_parent"])

	def test_dated_work_earliest_first_and_undated_apart(self):
		answer = self.feed()
		self.assertEqual([c["name"] for c in answer["tasks"]], ["T3", "T1", "CREWED", "T2"])
		self.assertEqual([c["name"] for c in answer["unscheduled"]], ["T4"])
		self.assertEqual((answer["start"], answer["end"]), ("2026-10-01", "2026-10-27"))

	def test_finished_work_is_drawn_but_never_moves(self):
		by = {c["name"]: c for c in self.feed()["tasks"]}
		self.assertTrue(by["T3"]["finished"])
		self.assertFalse(by["T3"]["movable"])
		self.assertTrue(by["T1"]["movable"])

	def test_each_card_gains_its_baseline_days_and_links_and_claims_no_booked_hours(self):
		by = {c["name"]: c for c in self.feed()["tasks"]}
		self.assertEqual(by["T1"]["baseline"], {"start": "2026-10-19", "end": "2026-10-21"})
		self.assertIsNone(by["T2"]["baseline"])
		self.assertEqual(by["T1"]["span_days"], 4)
		self.assertEqual(by["T2"]["depends_on"], ["T1"])
		self.assertEqual([m["booked"] for m in by["CREWED"]["crew"]], [None])
		self.assertEqual(by["CREWED"]["crew"][0]["label"], "Austin Healey")

	def test_the_project_and_its_schedule(self):
		answer = self.feed()
		self.assertEqual(answer["gantt"]["project"], "PRJ-1")
		self.assertEqual(answer["gantt"]["title"], "Riverwalk")
		self.assertEqual(answer["gantt"]["schedule"], {"status": "Published"})
		self.assertEqual(answer["drafts"]["count"], 0)
		self.assertNotIn("drafts", self.feed(draft=0))

	def test_drafted_days_and_crew_show_and_a_drafted_new_task_joins_its_group(self):
		state = dict(_row("T2", "G-300", start="2026-11-02", end="2026-11-03", subject="Set forms"), equipment=[])
		new = dict(_row("draft-new:PDC-9", None, start="2026-11-04", subject="Strip forms"), equipment=[])
		self.overlay = {
			"exclude": {"T2"},
			"extra": [(state, [h._Doc(resource="RES-2", hours=0, is_lead=1)]), (new, [])],
			"drafted": {
				"T2": {"credentials": [], "by": "clegg@example.com"},
				"draft-new:PDC-9": {"credentials": [], "new": True, "depends_on": ["T2"], "parent_task": "G-300"},
			},
			"stale": [],
			"phases": [],
			"projects": {"PRJ-1": {"count": 2, "shared": 1}},
		}
		answer = self.feed()
		by = {c["name"]: c for c in answer["tasks"]}
		self.assertEqual((by["T2"]["start"], by["T2"]["end"]), ("2026-11-02", "2026-11-03"))
		self.assertTrue(by["T2"]["drafted"])
		self.assertEqual(by["T2"]["drafted_by"], "clegg@example.com")
		self.assertEqual([m["resource"] for m in by["T2"]["crew"]], ["RES-2"])
		self.assertEqual(by["T2"]["project_title"], "Riverwalk")
		self.assertTrue(by["draft-new:PDC-9"]["new_task"])
		self.assertEqual(by["draft-new:PDC-9"]["gantt_parent"], "G-300")
		self.assertEqual(by["draft-new:PDC-9"]["depends_on"], ["T2"])
		self.assertEqual(answer["drafts"]["projects"], {"PRJ-1": {"count": 2, "shared": 1}})

	def test_another_projects_draft_is_not_drawn_here(self):
		other = dict(_row("T-ELSE", None, start="2026-11-02"), project="PRJ-2", equipment=[])
		self.overlay = {
			"exclude": set(),
			"extra": [(other, [])],
			"drafted": {"T-ELSE": {"credentials": []}},
			"stale": [],
			"phases": [],
			"projects": {"PRJ-2": {"count": 1, "shared": 0}},
		}
		answer = self.feed()
		self.assertNotIn("T-ELSE", [c["name"] for c in answer["tasks"] + answer["unscheduled"]])
		self.assertEqual(answer["drafts"]["projects"], {"PRJ-2": {"count": 1, "shared": 0}})


class TestTheGates(_Feed):
	def test_a_bare_call_is_the_pickers_list(self):
		answer = pg.get_project_gantt()
		self.assertIsNone(answer["gantt"])
		self.assertEqual([p["name"] for p in answer["projects"]], ["PRJ-1"])
		self.assertEqual((answer["tasks"], answer["unscheduled"]), ([], []))

	def test_a_project_that_is_gone_or_not_yours(self):
		with self.assertRaises(h._DoesNotExist):
			pg.get_project_gantt("PRJ-GONE")

		def refuse(*args, **kwargs):
			if kwargs.get("throw"):
				raise h._PermissionError("no")
			return False

		h.frappe.has_permission = refuse
		with self.assertRaises(h._PermissionError):
			pg.get_project_gantt("PRJ-1")

	def test_the_planner_role_gate(self):
		h.frappe.roles = ["Accounts User"]
		with self.assertRaises(h._PermissionError):
			pg.get_project_gantt("PRJ-1")


# ====================================================================== the link writes


class _Links(h._Base):
	def setUp(self):
		super().setUp()
		self.a = h._task("TASK-A", "2026-10-12 08:00:00", "2026-10-14 17:00:00", flags=types.SimpleNamespace())
		self.b = h._task("TASK-B", "2026-10-15 08:00:00", "2026-10-16 17:00:00", flags=types.SimpleNamespace(), depends_on=[])
		h._task("TASK-G", None, None, flags=types.SimpleNamespace(), is_group=1)
		h.frappe.tables["Task"] = [
			dict(h.frappe.docs[("Task", name)], is_group=h.frappe.docs[("Task", name)].get("is_group", 0))
			for name in ("TASK-A", "TASK-B", "TASK-G")
		]
		h.frappe.tables["Task Depends On"] = []
		moves = sys.modules["erpnext_enhancements.project_enhancements.fountain_move_jobs"]
		patch = mock.patch.object(moves, "move_projects", lambda names: set())
		patch.start()
		self.addCleanup(patch.stop)

	def add(self, task="TASK-B", depends_on="TASK-A", modified=h.BASE, reason=None):
		return actions.add_dependency(task, depends_on, modified, reason=reason)

	def saved(self, name):
		return [s for s in h.frappe.saved if s.get("name") == name]


class TestAddDependency(_Links):
	def test_it_links_and_holds_back_erpnexts_push(self):
		answer = self.add()
		self.assertTrue(answer["linked"])
		self.assertEqual(answer["depends_on"], "TASK-A")
		saved = self.saved("TASK-B")
		self.assertEqual(len(saved), 1)
		self.assertEqual([row["task"] for row in saved[0]["depends_on"]], ["TASK-A"])
		self.assertIn("ignore_reschedule=True", saved[0]["flags"])
		self.assertEqual(answer["modified"], "2026-10-08 10:00:01")
		self.assertEqual(h.frappe.comments, [])

	def test_an_existing_link_changes_nothing(self):
		h.frappe.docs[("Task", "TASK-B")]["depends_on"] = [h._Doc(task="TASK-A")]
		answer = self.add()
		self.assertFalse(answer["linked"])
		self.assertEqual(self.saved("TASK-B"), [])

	def test_starting_before_the_other_ends_asks_for_a_reason(self):
		h.frappe.docs[("Task", "TASK-B")].update(exp_start_date="2026-10-13 08:00:00")
		answer = self.add()
		self.assertTrue(answer["needs_reason"])
		self.assertEqual(list(answer["conflicts"]), ["Dependencies"])
		self.assertIn("TASK-A", answer["conflicts"]["Dependencies"][0])
		self.assertEqual(self.saved("TASK-B"), [])
		done = self.add(reason="The forms go up as the dig finishes")
		self.assertTrue(done["linked"])
		self.assertEqual(len(h.frappe.comments), 1)
		self.assertIn("The forms go up as the dig finishes", h.frappe.comments[0][2])

	def test_a_pencilled_task_needs_no_reason(self):
		h.frappe.docs[("Task", "TASK-B")].update(exp_start_date="2026-10-13 08:00:00", custom_tentative=1)
		self.assertTrue(self.add()["linked"])

	def test_the_drafted_days_are_the_ones_judged(self):
		# Stored, B starts after A ends; a Draft row moves it into A's days.
		h._draft_row("TASK-B", {"start": "2026-10-13", "end": "2026-10-14"})
		self.assertTrue(self.add()["needs_reason"])

	def test_the_tasks_draft_moves_on_to_the_save(self):
		row = h._draft_row("TASK-B", {"start": "2026-10-20", "end": "2026-10-21"})
		self.add()
		rebased = [c for c in h.frappe.db_calls if isinstance(c, tuple) and c[0] == "set_value"]
		self.assertEqual(rebased, [("set_value", ("Planner Draft Change", row["name"], "base_modified", "2026-10-08 10:00:01"), {"update_modified": False})])

	def test_what_is_refused(self):
		for kwargs, text in (
			({"depends_on": "TASK-G"}, "phase or group"),
			({"depends_on": "TASK-B"}, "itself"),
			({"task": "draft-new:PDC-1"}, "still a draft"),
			({"modified": "2026-01-01 00:00:00"}, "changed by someone else"),
		):
			with self.subTest(**kwargs):
				with self.assertRaises(h._Throw) as caught:
					self.add(**kwargs)
				self.assertIn(text, str(caught.exception))
		self.assertEqual(self.saved("TASK-B"), [])

	def test_a_loop_is_refused(self):
		h.frappe.tables["Task Depends On"] = [{"parenttype": "Task", "parent": "TASK-A", "task": "TASK-B"}]
		with self.assertRaises(h._Throw) as caught:
			self.add()
		self.assertIn("loop", str(caught.exception))

	def test_another_projects_task_is_refused(self):
		h.frappe.tables["Task"][0]["project"] = "PRJ-2"
		with self.assertRaises(h._Throw) as caught:
			self.add()
		self.assertIn("same project", str(caught.exception))


class TestRemoveDependency(_Links):
	def test_it_unlinks_with_the_push_held_back(self):
		h.frappe.docs[("Task", "TASK-B")]["depends_on"] = [h._Doc(task="TASK-A"), h._Doc(task="TASK-X")]
		answer = actions.remove_dependency("TASK-B", "TASK-A", h.BASE)
		self.assertTrue(answer["removed"])
		saved = self.saved("TASK-B")[0]
		self.assertEqual([row["task"] for row in saved["depends_on"]], ["TASK-X"])
		self.assertIn("ignore_reschedule=True", saved["flags"])

	def test_no_such_link_changes_nothing(self):
		answer = actions.remove_dependency("TASK-B", "TASK-A", h.BASE)
		self.assertFalse(answer["removed"])
		self.assertEqual(self.saved("TASK-B"), [])

	def test_a_groups_rows_are_erpnexts(self):
		with self.assertRaises(h._Throw):
			actions.remove_dependency("TASK-G", "TASK-A", h.BASE)


# ====================================================================== static


class TestTheSource(unittest.TestCase):
	def test_the_writes_are_post_only_whitelisted(self):
		tree = ast.parse(ACTIONS_PATH.read_text(encoding="utf-8"))
		for name in ("add_dependency", "remove_dependency"):
			fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
			decorators = [ast.unparse(d) for d in fn.decorator_list]
			self.assertIn("frappe.whitelist(methods=['POST'])", decorators)

	def test_the_feed_is_a_whitelisted_read_that_never_calls_get_planner(self):
		source = GANTT_PATH.read_text(encoding="utf-8")
		tree = ast.parse(source)
		fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_project_gantt")
		self.assertEqual([ast.unparse(d) for d in fn.decorator_list], ["frappe.whitelist()"])
		self.assertNotIn("get_planner(", ast.unparse(fn))
		self.assertIn("pp._require_planner()", ast.unparse(fn))

	def test_ci_runs_this_suite(self):
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_planner_gantt -v", CI.read_text(encoding="utf-8"))


if __name__ == "__main__":
	unittest.main()
