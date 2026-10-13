# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Project Planner T4 (v1.609.0): an AI-drafted task breakdown for the Build out grid.

Nik, 2026-10-12: an "AI draft..." action in the grid proposes a task breakdown, which the planner then
edits and creates as usual. No real model is called anywhere here; ``api.gemini`` is replaced by a fake
that returns whatever a test says. What this pins, because each part can fail quietly:

* **It never picks a person and never picks a day.** However the model spells it (assignee, crew,
  employee, start_date, exp_end_date, nested objects, a crew size sent as names), what comes back has
  exactly the five grid keys, and the prompt never offered the model a person or the calendar.
* **The model's answer is untrusted.** Rows capped at the build-out maximum, hours and crew size and
  subject length bounded, blank and repeated subjects dropped (also repeats of what the project has),
  phases named like the house template, and a dependency kept only when it points at another row that is
  still there: a task waiting on itself, on a missing row, or round a loop loses the link, never the row,
  and the planner is told. What survives always builds in order (``planner_actions.build_order``).
* **It writes nothing.** No Task, no Draft row, no comment, no set_value, no enqueue.
* **The prompt** carries the project's name, type, value streams, the customer's *type*, the description
  and notes, the Scope of Work, the open phases and the open tasks (so the draft does not repeat them),
  the planner's words, and the house phase guide; it carries no customer name, contact, email or phone
  (the free-text fields are scrubbed, and the customer is read for its type only).
* **Failure is graceful**: the AI not set up, a timeout, another error, a reply that is not JSON, a reply
  with nothing new, or too many drafts this hour: ``ok`` false, no rows, a plain message, no traceback and
  no prompt in the Error Log.
* **Same gate as the grid**: planner roles, Task create, Project read, customer jobs only.
* **The page** (under node): the dialog builds through a stand-in that asks every ``get_data`` /
  ``get_query`` WHILE it is being constructed (the v1.602.0 "before initialization" bug), the answer
  fills the grid through the same path as "Copy from project...", Append keeps what was typed, Replace
  sends nothing of the grid, the rows carry the AI mark, a failure leaves the grid alone.

Bench-free: reuses Phase 6B's fake site (via the build-out suite's). The node parts skip when node is not
on PATH.

Run: python -m unittest erpnext_enhancements.tests.test_planner_ai_draft
"""

import ast
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

from erpnext_enhancements.tests import test_planner_build_out as build
from erpnext_enhancements.tests import test_planner_phase6b as base

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = Path(__file__).resolve().parents[1]
PP_JS = APP / "project_enhancements/page/project_planner/project_planner.js"
MODULE = APP / "api/planner_ai_draft.py"
CI = REPO_ROOT / ".github/workflows/ci.yml"
API_README = APP / "api/README.md"
PE_README = APP / "project_enhancements/README.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

_Doc, _Throw, _PermissionError = base._Doc, base._Throw, base._PermissionError
draft = None

GRID_KEYS = {"subject", "hours", "phase", "crew_size", "needs"}


def setUpModule():
	global draft
	base.setUpModule()
	draft = importlib.import_module("erpnext_enhancements.api.planner_ai_draft")


def tearDownModule():
	base.tearDownModule()


def task(subject, **values):
	return dict({"subject": subject, "hours": 4, "phase": "", "crew_size": 2}, **values)


# ---------------------------------------------------------------------- pure helpers


class TestScrub(unittest.TestCase):
	def test_tags_entities_contacts_and_length(self):
		text = draft.scrub(
			"<p>Call Mrs&nbsp;Smith on (801) 555-1234 or 801.555.9876, mail a@b.co,"
			" see https://x.example/plan and www.y.example.</p><div>Dig&amp;fill</div>"
		)
		self.assertNotRegex(text, r"\d{3}.?\d{4}")
		for needle in ("a@b.co", "https://", "www.y"):
			self.assertNotIn(needle, text)
		self.assertIn("Dig&fill", text)
		self.assertIn("[removed]", text)
		self.assertEqual(draft.scrub(None), "")
		long = draft.scrub("word " * 2000, 100)
		self.assertLessEqual(len(long), 100)
		self.assertTrue(long.endswith("…"))

	def test_hours_and_dates_are_not_mistaken_for_phone_numbers(self):
		text = draft.scrub("Pour 16 h slab by 2026-10-12, pump 3450 gph, phase 200")
		self.assertEqual(text, "Pour 16 h slab by 2026-10-12, pump 3450 gph, phase 200")


class TestParseAnswer(unittest.TestCase):
	def test_json_object_list_and_fence(self):
		self.assertEqual(draft.parse_answer('{"tasks": []}'), {"tasks": []})
		self.assertEqual(draft.parse_answer("[1, 2]"), [1, 2])
		self.assertEqual(draft.parse_answer('```json\n{"tasks": []}\n```'), {"tasks": []})

	def test_nothing_else_is_salvaged(self):
		for text in ("", None, "   ", "Sure! Here is the plan", 'Here: {"tasks": []}', "42", '"text"'):
			self.assertIsNone(draft.parse_answer(text), repr(text))


class TestCanonicalPhase(unittest.TestCase):
	def test_house_phases(self):
		cp = draft.canonical_phase
		self.assertEqual(cp("Phase 200"), ("Phase 200", None))
		self.assertEqual(cp("200"), ("Phase 200", None))
		self.assertEqual(cp("phase 300 - rough-in"), ("Phase 300", None))
		self.assertEqual(cp("100 Pre-construction / design"), ("Phase 100", None))
		self.assertEqual(cp("Phase 700: Finishes"), ("Phase 700", None))

	def test_the_project_spelling_wins(self):
		self.assertEqual(
			draft.canonical_phase("Phase 300", ["Phase 300 Structure"]), ("Phase 300 Structure", None)
		)
		self.assertEqual(draft.canonical_phase("pumps", ["Pumps"]), ("Pumps", None))

	def test_maintenance_is_no_phase(self):
		for text in ("Phase 800", "800 Maintenance", "Maintenance visits", "maintenance"):
			self.assertEqual(draft.canonical_phase(text), ("", "maintenance"), text)

	def test_other_names_are_kept_and_bounded(self):
		self.assertEqual(draft.canonical_phase("Lighting"), ("Lighting", None))
		self.assertEqual(draft.canonical_phase("  Phase   150 "), ("Phase 150", None))
		self.assertEqual(len(draft.canonical_phase("x" * 500)[0]), 140)
		self.assertEqual(draft.canonical_phase(""), ("", None))
		self.assertEqual(draft.canonical_phase(None), ("", None))

	def test_the_house_vocabulary_is_phases_100_to_700_with_no_800(self):
		self.assertEqual(draft.PHASE_NAMES, tuple(f"Phase {n}" for n in range(100, 800, 100)))
		guide = draft.phase_guide()
		self.assertIn("500 and 600 run in parallel", guide)
		self.assertIn("There is no Phase 800", guide)
		self.assertNotIn("- Phase 800", guide)


class TestValidateBreakdown(unittest.TestCase):
	def check(self, tasks, **kwargs):
		return draft.validate_breakdown({"tasks": tasks}, **kwargs)

	def test_only_the_five_grid_keys_ever_come_back(self):
		forbidden = {
			"assignee": "Austin",
			"assigned_to": "austin@example.com",
			"crew": ["Danny", "Jesse"],
			"custom_crew": [{"resource": "RES-1"}],
			"employee": "EMP-1",
			"resource": "RES-2",
			"person": "Korben",
			"owner": "nik@example.com",
			"start_date": "2026-10-20",
			"end_date": "2026-10-22",
			"exp_start_date": "2026-10-20",
			"exp_end_date": "2026-10-22",
			"date": "2026-10-20",
			"due_date": "2026-10-21",
			"lead": "Danny",
			"parent_task": "TASK-P1",
		}
		out = self.check([task("Dig the basin", depends_on=[], **forbidden)])
		(row,) = out["rows"]
		self.assertEqual(set(row), GRID_KEYS)
		self.assertEqual(out["dropped"]["extra_keys"], len(forbidden))
		dumped = json.dumps(out)
		for value in ("Austin", "Danny", "RES-1", "2026-10-20", "nik@example.com", "TASK-P1"):
			self.assertNotIn(value, dumped)
		self.assertIn("people, crew, dates", draft.drop_note(out["dropped"]))

	def test_crew_size_is_a_number_never_names(self):
		out = self.check(
			[
				task("A", crew_size=["Danny", "Jesse"]),
				task("B", crew_size="Danny"),
				task("C", crew_size={"name": "Danny"}),
				task("D", crew_size=2.7),
				task("E", crew_size="3"),
				task("F", crew_size=99),
				task("G", crew_size=-4),
				task("H", crew_size=True),
			]
		)
		self.assertEqual([row["crew_size"] for row in out["rows"]], [0, 0, 0, 2, 3, 50, 0, 0])
		self.assertTrue(all(isinstance(row["crew_size"], int) for row in out["rows"]))

	def test_hours_are_bounded_numbers(self):
		out = self.check(
			[
				task("A", hours=-5),
				task("B", hours=9999),
				task("C", hours=float("nan")),
				task("D", hours="abc"),
				task("E", hours="6h"),
				task("F", hours="2.5 hours"),
				task("G", hours=None),
				task("H", hours=3.456),
				task("I", hours=True),
				task("J", hours=float("inf")),
				task("K", hours=[4]),
			]
		)
		self.assertEqual([row["hours"] for row in out["rows"]], [0, 400, 0, 0, 6, 2.5, 0, 3.46, 0, 0, 0])

	def test_subjects_are_bounded_and_blank_or_repeated_ones_dropped(self):
		out = self.check(
			[
				task("  Dig   the\tbasin  "),
				task(""),
				task("   "),
				task(None),
				task("DIG THE BASIN"),
				task("x" * 300),
				{"hours": 3},
				"not a task",
				42,
				task("Dig the sump"),
			],
			known_subjects=["dig the sump "],
		)
		self.assertEqual([row["subject"] for row in out["rows"]], ["Dig the basin", "x" * 140])
		self.assertEqual(out["dropped"]["duplicates"], 2)
		self.assertEqual(out["dropped"]["rows"], 6)

	def test_tasks_the_project_already_has_are_not_proposed_again(self):
		out = self.check([task("Dig the basin"), task("Hang the lights")], known_subjects=["Dig the Basin"])
		self.assertEqual([row["subject"] for row in out["rows"]], ["Hang the lights"])

	def test_at_most_a_hundred_rows_and_dependencies_past_them_are_dropped(self):
		tasks = [task(f"Task {n}") for n in range(1, 131)]
		tasks[0]["depends_on"] = ["row:120"]
		tasks[1]["depends_on"] = ["row:1"]
		out = self.check(tasks)
		self.assertEqual(len(out["rows"]), 100)
		self.assertEqual(out["dropped"]["over_cap"], 30)
		self.assertEqual(out["rows"][0]["needs"], [])
		self.assertEqual(out["rows"][1]["needs"], ["row:1"])
		self.assertEqual(out["dropped"]["missing"], 1)
		self.assertEqual(len(draft.validate_breakdown({"tasks": tasks}, cap=5)["rows"]), 5)

	def test_dependencies_follow_the_rows_that_are_kept(self):
		out = self.check(
			[
				task("A"),
				task(""),  # dropped: row 2 of the model's list is not a row of the grid
				task("B", depends_on=["row:1"]),
				task("C", depends_on=[3, "1", "row: 3"]),
				task("D", depends_on="row:3"),
			]
		)
		self.assertEqual(
			[(row["subject"], row["needs"]) for row in out["rows"]],
			[("A", []), ("B", ["row:1"]), ("C", ["row:1", "row:2"]), ("D", ["row:2"])],
		)

	def test_a_dependency_on_a_dropped_row_is_dropped_not_the_row(self):
		out = self.check([task("A"), task("", depends_on=[]), task("B", depends_on=["row:2"])])
		self.assertEqual([row["subject"] for row in out["rows"]], ["A", "B"])
		self.assertEqual(out["rows"][1]["needs"], [])
		self.assertEqual(out["dropped"]["missing"], 1)

	def test_self_missing_and_unreadable_dependencies_are_dropped(self):
		out = self.check(
			[
				task("A", depends_on=["row:1"]),
				task("B", depends_on=["row:99", "TASK-0001", "later", 0, -1, 2.5, {"row": 1}, None]),
				task("C", depends_on={"row": 1}),
				task("D", depends_on=7),
			]
		)
		self.assertEqual([row["needs"] for row in out["rows"]], [[], [], [], []])
		dropped = out["dropped"]
		self.assertEqual(dropped["self"], 1)
		self.assertGreaterEqual(dropped["missing"], 3)  # row:99, 0, -1 and 7 name no row
		self.assertGreaterEqual(dropped["unreadable"], 4)
		self.assertIn("dependencies", draft.drop_note(dropped))

	def test_loops_lose_the_closing_link_and_the_rest_builds_in_order(self):
		out = self.check(
			[
				task("A", depends_on=["row:2"]),
				task("B", depends_on=["row:3"]),
				task("C", depends_on=["row:1"]),  # closes A -> B -> C -> A
				task("D", depends_on=["row:4", "row:5"]),  # itself, and a row that waits on it
				task("E", depends_on=["row:4"]),
			]
		)
		self.assertEqual(out["dropped"]["cycles"], 2)
		self.assertEqual(out["dropped"]["self"], 1)
		waits = {
			index: {int(token[4:]) - 1 for token in row["needs"]}
			for index, row in enumerate(out["rows"])
			if row["needs"]
		}
		order, cycle = base.actions.build_order(len(out["rows"]), waits)
		self.assertEqual(cycle, [])
		self.assertEqual(sorted(order), [0, 1, 2, 3, 4])
		self.assertEqual(len(out["rows"]), 5)

	def test_two_row_loop_keeps_the_first_link_only(self):
		out = self.check([task("A", depends_on=["row:2"]), task("B", depends_on=["row:1"])])
		self.assertEqual([row["needs"] for row in out["rows"]], [["row:2"], []])
		self.assertEqual(out["dropped"]["cycles"], 1)

	def test_phases_read_like_the_house_template(self):
		out = self.check(
			[
				task("A", phase="200"),
				task("B", phase="Phase 300 - Rough-in"),
				task("C", phase="phase 500"),
				task("D", phase="Phase 800 Maintenance"),
				task("E", phase="Pumps"),
				task("F", phase=["Phase 100"]),
				task("G", phase=None),
			],
			open_phases=["Pumps", "Phase 500 Mechanical Room"],
		)
		self.assertEqual(
			[row["phase"] for row in out["rows"]],
			["Phase 200", "Phase 300", "Phase 500 Mechanical Room", "", "Pumps", "", ""],
		)
		self.assertEqual(out["dropped"]["maintenance"], 1)

	def test_too_many_new_phases_leave_the_rest_without_one(self):
		out = self.check([task(f"T{n}", phase=f"Custom {n}") for n in range(30)])
		phases = [row["phase"] for row in out["rows"]]
		self.assertEqual(len({phase for phase in phases if phase}), 25)
		self.assertEqual(phases.count(""), 5)
		self.assertEqual(out["dropped"]["phases"], 5)

	def test_a_bare_list_and_the_rows_key_are_read_too(self):
		self.assertEqual(len(draft.validate_breakdown([task("A")])["rows"]), 1)
		self.assertEqual(len(draft.validate_breakdown({"rows": [task("A")]})["rows"]), 1)

	def test_an_answer_that_is_not_tasks_is_no_rows(self):
		for data in (None, "text", 3, {}, {"tasks": "nope"}, {"tasks": {"a": 1}}, []):
			out = draft.validate_breakdown(data)
			self.assertEqual(out["rows"], [], repr(data))

	def test_the_summary_is_plain_text(self):
		out = draft.validate_breakdown({"summary": "<b>Basin</b> and pumps, call 801-555-1234", "tasks": []})
		self.assertNotIn("<b>", out["summary"])
		self.assertNotIn("555", out["summary"])


# ---------------------------------------------------------------------- the endpoint


class _AiSite(build._Site):
	"""The build-out suite's site (PRJ-1 with a phase and a task) with a fake Gemini and a fake cache."""

	def setUp(self):
		super().setUp()
		self.frappe = base.frappe
		self.model_calls = []
		self.reply = json.dumps({"summary": "A basin.", "tasks": [task("Excavate the basin", phase="200")]})
		self.fail = None
		self.configured = True
		self.counts = {}
		self.cache_down = False
		self.reads = []
		self.project_values = {
			"project_name": "Plaza Fountain",
			"project_type": "Build",
			"customer": "CUST-1",
			"notes": "<p>Call Mrs Smith at 801-555-1234 or smith@example.com. Dig basin first.</p>",
			"custom_project_description": "Rebuild the plaza fountain",
			"custom_general_scope_description": "<p>New basin and two pumps</p>",
			"custom_notes_for_scheduling": None,
		}
		self.customer_values = {"customer_type": "Company", "customer_name": "Smith Family Trust LLC"}
		self.frappe.installed |= {"Project Scope of Work", "Scope Acceptance Criterion"}
		self.frappe.tables["Project Scope of Work"] = [
			{
				"name": "SCOPE-1",
				"project": "PRJ-1",
				"status": "Locked",
				"docstatus": 1,
				"scope_summary": "<p>Replace the basin liner</p>",
				"inclusions": "Pumps and lights",
				"exclusions": "Landscaping",
				"assumptions": "Power at the pad",
				"customer": "CUST-1",
			},
			{
				"name": "SCOPE-OLD",
				"project": "PRJ-1",
				"status": "Superseded",
				"docstatus": 1,
				"scope_summary": "An older scope",
			},
		]
		self.frappe.tables["Scope Acceptance Criterion"] = [
			{
				"parent": "SCOPE-1",
				"parenttype": "Project Scope of Work",
				"criterion": "Basin holds water for 24 hours",
				"stage": "Final",
				"idx": 1,
			}
		]
		self.streams = [_Doc(stream="Build")]

		frappe = self.frappe
		real_sql = frappe.db.sql

		def sql(query, values=None, as_dict=False, **kwargs):
			if "AS stream" in query:
				return list(self.streams)
			return real_sql(query, values, as_dict=as_dict, **kwargs)

		def get_value(doctype, name, field=None, *args, **kwargs):
			self.reads.append((doctype, name, tuple(field) if isinstance(field, list) else field))
			if doctype == "Project" and isinstance(field, list):
				return _Doc({key: self.project_values.get(key) for key in field})
			if doctype == "Customer":
				return self.customer_values.get(field)
			return frappe.values.get((doctype, name, field))

		frappe.db.sql = sql
		frappe.db.get_value = get_value
		frappe.cache = types.SimpleNamespace(
			make_key=lambda key: f"site:{key}", incrby=self._incr, expire=lambda *a: None
		)

		gemini = types.ModuleType("erpnext_enhancements.api.gemini")

		def generate(prompt, system_instruction, settings, feature="unknown", model_id=None):
			self.model_calls.append(
				{
					"prompt": prompt,
					"system": system_instruction,
					"settings": settings,
					"feature": feature,
					"model_id": model_id,
				}
			)
			if self.fail:
				raise self.fail
			return self.reply, "thoughts"

		gemini.generate_content_with_vertex_ai = generate
		self.saved_gemini = sys.modules.get("erpnext_enhancements.api.gemini")
		sys.modules["erpnext_enhancements.api.gemini"] = gemini
		self.configured_patch = mock.patch.object(draft, "ai_configured", lambda: self.configured)
		self.configured_patch.start()

	def tearDown(self):
		self.configured_patch.stop()
		if self.saved_gemini is None:
			sys.modules.pop("erpnext_enhancements.api.gemini", None)
		else:
			sys.modules["erpnext_enhancements.api.gemini"] = self.saved_gemini
		super().tearDown()

	def _incr(self, key, amount):
		if self.cache_down:
			raise ConnectionError("redis is down")
		self.counts[key] = self.counts.get(key, 0) + amount
		return self.counts[key]

	def ask(self, project="PRJ-1", **kwargs):
		return draft.draft_task_breakdown(project, **kwargs)

	def prompt(self):
		(call,) = self.model_calls
		return call["prompt"]

	def assert_wrote_nothing(self):
		frappe = self.frappe
		self.assertEqual(frappe.inserted, [])
		self.assertEqual(frappe.saved, [])
		self.assertEqual(frappe.comments, [])
		self.assertEqual(frappe.deleted, [])
		written = [
			event
			for event in frappe.events
			if event[0] in ("insert", "save", "set_value", "delete", "db.delete")
		]
		self.assertEqual(written, [])


class TestDraftTaskBreakdown(_AiSite):
	def test_a_draft_comes_back_as_grid_rows_and_writes_nothing(self):
		answer = self.ask(brief="Rebuild the basin")
		self.assertTrue(answer["ok"], answer)
		self.assertEqual(answer["count"], 1)
		(row,) = answer["rows"]
		self.assertEqual(
			row,
			{
				"subject": "Excavate the basin",
				"hours": 4.0,
				"phase": "Phase 200",
				"crew_size": 2,
				"needs": [],
			},
		)
		self.assertIn("AI draft — review before creating.", answer["note"])
		self.assertEqual(answer["summary"], "A basin.")
		self.assert_wrote_nothing()

	def test_it_goes_through_the_app_gemini_client_under_its_own_feature(self):
		self.ask()
		(call,) = self.model_calls
		self.assertEqual(call["feature"], draft.FEATURE)
		self.assertEqual(call["feature"], "planner_task_breakdown")
		# No model id is pinned: the app's own MODEL_ID answers, so a retirement is changed in one place.
		self.assertIsNone(call["model_id"])
		# The settings document of the other callers is not read; the client does not use it.
		self.assertIsNone(call["settings"])

	def test_the_model_cannot_smuggle_a_person_or_a_date_through(self):
		self.reply = json.dumps(
			{
				"tasks": [
					task(
						"Excavate",
						assignee="Austin Healey",
						start_date="2026-10-20",
						crew=["Danny"],
						phase="Phase 200",
					),
					task(
						"Pour slab", depends_on=["row:1"], owner="nik@example.com", exp_end_date="2026-10-30"
					),
				]
			}
		)
		answer = self.ask()
		self.assertTrue(answer["ok"])
		for row in answer["rows"]:
			self.assertEqual(set(row), GRID_KEYS)
		dumped = json.dumps(answer)
		for value in ("Austin", "Danny", "2026-10-20", "nik@example.com", "2026-10-30"):
			self.assertNotIn(value, dumped)
		self.assertEqual(answer["rows"][1]["needs"], ["row:1"])
		self.assertIn("people, crew, dates", answer["note"])

	def test_over_a_hundred_rows_are_cut_and_the_planner_is_told(self):
		self.reply = json.dumps({"tasks": [task(f"Task {n}") for n in range(1, 151)]})
		answer = self.ask()
		self.assertEqual(answer["count"], 100)
		self.assertIn("50 tasks past the 100-task limit", answer["note"])

	def test_dependency_problems_are_dropped_and_said(self):
		self.reply = json.dumps(
			{
				"tasks": [
					task("A", depends_on=["row:1", "row:2"]),
					task("B", depends_on=["row:1"]),
					task("C", depends_on=["row:9"]),
				]
			}
		)
		answer = self.ask()
		self.assertEqual([row["needs"] for row in answer["rows"]], [["row:2"], [], []])
		self.assertIn("3 dependencies", answer["note"])  # itself, the loop it would close, and row 9

	def test_a_brief_is_scrubbed_and_capped_and_a_missing_one_is_suggested(self):
		self.ask(brief="Rebuild it. Reach Joe on 801-555-9999 / joe@example.com. " + "x" * 5000)
		prompt = self.prompt()
		self.assertIn("Rebuild it.", prompt)
		self.assertNotIn("555-9999", prompt)
		self.assertNotIn("joe@example.com", prompt)
		self.assertLess(len(prompt), draft.PROMPT_MAX + 1)
		self.assertNotIn("x" * 1600, prompt)
		self.model_calls.clear()
		answer = self.ask()
		self.assertIn("Describe the job", answer["note"])
		self.assertNotIn("The project manager says", self.prompt())

	def test_the_prompt_has_the_project_its_scope_and_what_it_already_has(self):
		self.ask(brief="Two new pumps")
		prompt = self.prompt()
		for needle in (
			"Project: Plaza Fountain",
			"Type: Build",
			"Value streams: Build",
			"Customer type: Company",
			"Rebuild the plaza fountain",
			"New basin and two pumps",
			"Dig basin first.",
			"Scope of Work 1 (Locked)",
			"Replace the basin liner",
			"Included: Pumps and lights",
			"Excluded: Landscaping",
			"Assumptions: Power at the pad",
			"Basin holds water for 24 hours [Final]",
			"The project manager says the job is: Two new pumps",
			"Phases the project already has: Pumps",
			"- Dig the basin",
			"Phase 100",
			"Phase 700",
			"500 and 600 run in parallel",
			"There is no Phase 800",
		):
			self.assertIn(needle, prompt)
		# Finished work, templates, another project's tasks and a superseded scope are not offered.
		for needle in ("Old job", "Template", "Other project's", "Elsewhere", "Done phase", "An older scope"):
			self.assertNotIn(needle, prompt)

	def test_no_customer_personal_data_is_in_the_prompt_or_was_even_read(self):
		self.ask(brief="Gate code 1234, call 801-555-0000")
		prompt = self.prompt()
		for needle in ("Smith Family Trust", "CUST-1", "801-555-1234", "smith@example.com", "555-0000"):
			self.assertNotIn(needle, prompt, needle)
		# The customer is read for its type, and nothing else of it.
		customer_reads = [read for read in self.reads if read[0] == "Customer"]
		self.assertEqual(customer_reads, [("Customer", "CUST-1", "customer_type")])
		project_fields = next(read[2] for read in self.reads if read[0] == "Project")
		for field in project_fields:
			self.assertNotRegex(field, r"contact|email|phone|mobile|address|owner|customer_name", field)
		# Nothing about people or the calendar is read: no crew, no employees, no assignments.
		doctypes = {query[1] for query in self.frappe.queries if query[0] in ("get_all",)}
		self.assertLessEqual(doctypes, {"Task", "Project Scope of Work", "Scope Acceptance Criterion"})
		for query in self.frappe.queries:
			if query[0] == "sql":
				self.assertNotRegex(query[1], r"Planner Resource|tabEmployee|tabContact|tabAddress|ToDo")
		self.assertNotIn("Austin", prompt)
		self.assertNotIn("Korben", prompt)

	def test_the_prompt_tells_the_model_not_to_pick_people_or_dates(self):
		self.ask()
		(call,) = self.model_calls
		system = call["system"]
		self.assertIn("Do NOT name or choose people", system)
		self.assertIn("do NOT give dates", system)
		for key in draft.ROW_KEYS:
			self.assertIn(f'"{key}"', system)

	def test_the_grids_own_subjects_are_told_to_the_model_and_not_proposed_back(self):
		self.reply = json.dumps({"tasks": [task("Hang the lights"), task("Test the pumps")]})
		answer = self.ask(grid_subjects=json.dumps(["Hang the lights", "  ", "Wire the panel"]))
		self.assertIn("already typed into the planner's grid", self.prompt())
		self.assertIn("- Wire the panel", self.prompt())
		self.assertEqual([row["subject"] for row in answer["rows"]], ["Test the pumps"])
		self.assertEqual(answer["rows"][0]["needs"], [])

	def test_unreadable_grid_subjects_are_ignored(self):
		for value in ("not json", '{"a": 1}', "[", 5):
			self.model_calls.clear()
			answer = self.ask(grid_subjects=value)
			self.assertTrue(answer["ok"], value)
			self.assertNotIn("already typed", self.prompt())

	def test_drafted_new_tasks_are_counted_as_tasks_the_project_has_in_draft_mode(self):
		seen = []
		real = self.actions._project_task_rows

		def rows(project, names=None, draft=False):
			seen.append(draft)
			return real(project, names, draft=False)

		with mock.patch.object(self.actions, "_project_task_rows", rows):
			self.ask()
			self.model_calls.clear()
			self.ask(draft=1)
		self.assertEqual(seen, [False, True])

	def test_a_scope_that_is_not_there_is_simply_left_out(self):
		self.frappe.installed -= {"Project Scope of Work", "Scope Acceptance Criterion"}
		answer = self.ask()
		self.assertTrue(answer["ok"])
		self.assertNotIn("Scope of Work", self.prompt())

	def test_a_caller_who_may_not_read_scopes_drafts_without_them(self):
		real = self.frappe.has_permission
		self.frappe.has_permission = lambda doctype=None, ptype="read", doc=None, *a, **k: (
			False if doctype == "Project Scope of Work" else real(doctype, ptype, doc)
		)
		answer = self.ask()
		self.assertTrue(answer["ok"])
		self.assertNotIn("Replace the basin liner", self.prompt())

	def test_a_site_without_the_custom_fields_still_drafts(self):
		self.frappe.db.has_column = lambda doctype, column: False
		self.streams = []
		answer = self.ask()
		self.assertTrue(answer["ok"])
		self.assertNotIn("Rebuild the plaza fountain", self.prompt())
		self.assertNotIn("Value streams", self.prompt())


class TestGracefulFailure(_AiSite):
	def refused_nicely(self, answer, message):
		self.assertFalse(answer["ok"])
		self.assertEqual(answer["rows"], [])
		self.assertEqual(answer["count"], 0)
		self.assertIn(message, answer["message"])
		self.assert_wrote_nothing()

	def test_ai_off_says_so_and_never_calls_the_model(self):
		self.configured = False
		answer = self.ask()
		self.refused_nicely(answer, "not set up")
		self.assertEqual(self.model_calls, [])
		self.assertEqual(self.frappe.errors, [])

	def test_ai_configured_reads_the_service_account_as_a_boolean_and_never_raises(self):
		self.configured_patch.stop()
		try:
			package = "erpnext_enhancements.google_drive.drive_utils"
			for value, want in (({"type": "service_account"}, True), (None, False), ({}, False)):
				fake = types.ModuleType(package)
				fake.get_service_account_info = lambda value=value: value
				with mock.patch.dict(sys.modules, {package: fake}):
					self.assertIs(draft.ai_configured(), want, value)
			boom = types.ModuleType(package)

			def explode():
				raise RuntimeError("no settings doctype")

			boom.get_service_account_info = explode
			with mock.patch.dict(sys.modules, {package: boom}):
				self.assertIs(draft.ai_configured(), False)
		finally:
			self.configured_patch.start()

	def test_a_timeout_has_its_own_message_and_a_log_without_the_prompt(self):
		self.fail = Exception(
			"Gemini Enterprise Agent Platform request failed: ReadTimeout: HTTPSConnectionPool(host="
			"'aiplatform.googleapis.com'): Read timed out. (read timeout=120)"
		)
		answer = self.ask(brief="Secret plan for Mrs Smith")
		self.refused_nicely(answer, "took too long")
		((args, kwargs),) = self.frappe.errors
		self.assertEqual(args, ())
		self.assertEqual(kwargs["title"], "Planner AI task breakdown failed")
		logged = kwargs["message"]
		self.assertIn("Exception", logged)
		for private in (
			"Secret plan",
			"Mrs Smith",
			"Plaza Fountain",
			"Rebuild the plaza",
			"Traceback",
			"Dig basin first",
		):
			self.assertNotIn(private, logged)

	def test_another_error_is_a_plain_message(self):
		self.fail = Exception('Gemini request failed: 403 Forbidden\nResponse: {"error": "nope"}')
		answer = self.ask()
		self.refused_nicely(answer, "did not answer")
		self.assertNotIn("403", answer["message"])
		self.assertNotIn("Traceback", answer["message"])
		self.assertEqual(len(self.frappe.errors), 1)

	def test_a_reply_that_is_not_json_is_no_rows(self):
		for reply in ("Sure! Here is the plan:", "", "  ", "[1, 2", "null"):
			self.frappe.errors.clear()
			self.reply = reply
			self.refused_nicely(self.ask(), "could not be read")

	def test_a_reply_with_nothing_new_is_no_rows(self):
		for reply in (
			json.dumps({"tasks": []}),
			json.dumps({"tasks": [task("Dig the basin")]}),  # the project already has it
			json.dumps({"tasks": [{"hours": 3}, "text"]}),
			json.dumps({"summary": "Nothing to do"}),
		):
			self.reply = reply
			answer = self.ask()
			self.refused_nicely(answer, "did not propose any tasks")
			self.assertIn("dropped", answer)

	def test_no_traceback_is_ever_logged(self):
		# Frappe renders local variables, and the prompt is one: the module never asks for a traceback.
		tree = ast.parse(MODULE.read_text(encoding="utf-8"))
		names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
		self.assertNotIn("get_traceback", names)
		self.assertNotIn("print_exc", names)

	def test_the_error_log_is_written_with_keyword_arguments(self):
		# Frappe v16 guesses the title from positional arguments (CLAUDE.md).
		tree = ast.parse(MODULE.read_text(encoding="utf-8"))
		calls = [
			node
			for node in ast.walk(tree)
			if isinstance(node, ast.Call)
			and isinstance(node.func, ast.Attribute)
			and node.func.attr == "log_error"
		]
		self.assertTrue(calls)
		for call in calls:
			self.assertEqual(call.args, [])
			self.assertEqual({keyword.arg for keyword in call.keywords}, {"title", "message"})


class TestGate(_AiSite):
	def refused(self, expected=_Throw, **kwargs):
		with self.assertRaises(expected):
			self.ask(**kwargs)
		self.assertEqual(self.model_calls, [], "a refused caller must not cost a model call")
		self.assert_wrote_nothing()

	def test_only_the_planner_roles(self):
		self.frappe.roles = ["Sales User"]
		self.refused(_PermissionError)
		self.frappe.roles = ["Projects User"]
		self.assertTrue(self.ask()["ok"])
		self.model_calls.clear()
		self.frappe.roles = ["Maintenance User"]
		self.assertTrue(self.ask()["ok"])

	def test_a_caller_who_cannot_create_tasks_is_refused_as_the_grid_refuses_them(self):
		self.frappe.perms["create"] = False
		self.refused(_PermissionError)

	def test_a_project_the_caller_cannot_read_is_refused(self):
		self.frappe.has_permission = lambda doctype=None, ptype="read", doc=None, *a, **k: not (
			doctype == "Project" and ptype == "read"
		)
		self.refused(_PermissionError)

	def test_a_caller_who_cannot_read_tasks_is_refused(self):
		self.frappe.has_permission = lambda doctype=None, ptype="read", doc=None, *a, **k: not (
			doctype == "Task" and ptype == "read"
		)
		self.refused(_PermissionError)

	def test_only_customer_jobs(self):
		self.refused(project="PRJ-INT")

	def test_a_missing_project_is_refused(self):
		self.refused(project="PRJ-NOPE")
		self.refused(project="")
		self.refused(project=None)

	def test_the_endpoint_is_post_only(self):
		tree = ast.parse(MODULE.read_text(encoding="utf-8"))
		(function,) = [
			node
			for node in tree.body
			if isinstance(node, ast.FunctionDef) and node.name == "draft_task_breakdown"
		]
		self.assertEqual(
			[ast.unparse(d) for d in function.decorator_list], ["frappe.whitelist(methods=['POST'])"]
		)
		other = [
			node.name
			for node in tree.body
			if isinstance(node, ast.FunctionDef)
			and any("whitelist" in ast.unparse(d) for d in node.decorator_list)
		]
		self.assertEqual(other, ["draft_task_breakdown"])


class TestRateLimit(_AiSite):
	def test_twenty_drafts_an_hour_then_a_plain_refusal_that_costs_no_model_call(self):
		for _call in range(draft.RATE_LIMIT):
			self.assertTrue(self.ask()["ok"])
		answer = self.ask()
		self.assertFalse(answer["ok"])
		self.assertTrue(answer["rate_limited"])
		self.assertIn("several drafts", answer["message"])
		self.assertEqual(len(self.model_calls), draft.RATE_LIMIT)

	def test_each_person_has_their_own_allowance(self):
		self.frappe.session.user = "a@example.com"
		for _call in range(draft.RATE_LIMIT):
			self.ask()
		self.assertFalse(self.ask()["ok"])
		self.frappe.session.user = "b@example.com"
		self.assertTrue(self.ask()["ok"])

	def test_a_refused_caller_or_an_unset_ai_does_not_use_up_the_allowance(self):
		self.configured = False
		for _call in range(draft.RATE_LIMIT + 5):
			self.ask()
		self.assertEqual(self.counts, {})

	def test_a_cache_that_cannot_count_lets_the_call_through(self):
		self.cache_down = True
		self.assertTrue(self.ask()["ok"])


class TestNothingIsWritten(unittest.TestCase):
	def test_the_module_has_no_write_call_in_it(self):
		tree = ast.parse(MODULE.read_text(encoding="utf-8"))
		forbidden = {
			"insert",
			"save",
			"submit",
			"cancel",
			"delete",
			"delete_doc",
			"set_value",
			"db_set",
			"db_insert",
			"new_doc",
			"get_doc",
			"get_cached_doc",
			"add_comment",
			"enqueue",
			"sendmail",
			"publish_realtime",
			"commit",
			"msgprint",
			"rename_doc",
			"insert_new_draft",
			"store_new_draft",
			"task_from_spec",
			"build_out_tasks",
		}
		used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
		self.assertEqual(used & forbidden, set())
		names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
		self.assertEqual(names & forbidden, set())

	def test_the_row_keys_name_no_person_and_no_date(self):
		for key in draft.ROW_KEYS:
			self.assertNotRegex(key, r"date|day|assign|crew_member|resource|employee|person|owner|lead|user")
		self.assertEqual(set(draft.ROW_KEYS), {"subject", "hours", "phase", "crew_size", "depends_on"})


# ---------------------------------------------------------------------- the page under node


T4_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const [source] = JSON.parse(fs.readFileSync(0, "utf8"));
const tr = (text, args) => (args ? String(text).replace(/\{(\d+)\}/g, (m, i) => String(args[i])) : String(text));
const alerts = [];
const calls = [];
const asked = [];
const dialogs = [];
// Frappe builds a field's control while the dialog is constructed, and an autocomplete or
// MultiSelectPills asks its get_data / get_query right then, before `new Dialog` has returned.
class Dialog {
	constructor(opts) {
		this.opts = opts;
		this.fields = opts.fields;
		this.fields_dict = {};
		this.fields.forEach((df) => {
			this.fields_dict[df.fieldname] = { df, $wrapper: { html: (h) => { this.said = h; this.said_all = (this.said_all || []).concat([h]); } } };
		});
		this.shown = false;
		this.hidden = false;
		this.disabled = [];
		this.ask();
		dialogs.push(this);
	}
	ask() {
		this.fields.forEach((df) => {
			if (df.get_data) df.get_data("");
			if (df.get_query) asked.push(df.get_query());
			if (df.fieldtype === "Select" && df.options) asked.push(df.options);
		});
	}
	get_primary_btn() { return { prop: (name, value) => this.disabled.push([name, value]) }; }
	show() { this.shown = true; this.ask(); }
	hide() { this.hidden = true; }
}
let reply = null;
let rejects = false;
const ctx = {
	console,
	Promise,
	setTimeout: (fn) => fn(),
	clearTimeout: () => null,
	__: tr,
	pp_esc: (v) => String(v),
	PP: {},
	PP6A: { owner: "pp" },
	$: () => ({ text: () => ({ appendTo: () => null }) }),
	frappe: {
		ui: { Dialog },
		msgprint: (m) => alerts.push(typeof m === "string" ? m : m.message),
		show_alert: (o) => alerts.push(o.message),
		call: (o) => { calls.push(o); return rejects ? Promise.reject(new Error("offline")) : Promise.resolve(reply); },
	},
	document: { getElementById: () => true },
};
vm.createContext(ctx);
vm.runInContext(source + "\nthis.METHODS = Object.assign({}, PPT1_METHODS, PPT4_METHODS); this.PPT1 = PPT1; this.PPT4 = PPT4; this.P1 = PPT1_PURE; this.P4 = PPT4_PURE;", ctx);
const out = {};
const make_page = (over) => {
	const rebuilt = [];
	const page = Object.assign(Object.create(ctx.METHODS), {
		draft_on: false,
		t1: { ids: 0, drafts: {}, needs: null },
		draft_args() { return this.draft_on ? { draft: 1 } : {}; },
		t1_rebuild_rows: (state, focus) => rebuilt.push([state.rows.length, focus]),
		t1_check: () => null,
	}, over || {});
	page.rebuilt = rebuilt;
	return page;
};
const grid = (page, n, over) => Object.assign({
	project: "PRJ-1",
	label: "Plaza",
	rows: Array.from({ length: n }, () => ctx.P1.blank_row(page.t1_new_id())),
	table: {},
	handle: { is_open: () => true, close: () => null },
}, over || {});
const view = (state) => state.rows.map((row) => [row.subject, row.hours, row.phase, row.crew_size, row.needs, !!row.ai]);
const settle = async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); };
const answer = {
	ok: true,
	count: 3,
	note: "AI draft — review before creating.",
	rows: [
		{ subject: "Excavate basin", hours: 16, phase: "Phase 200", crew_size: 2, needs: [] },
		{ subject: "Pour slab", hours: 8, phase: "Phase 300", crew_size: 0, needs: ["row:1"] },
		{ subject: "Set pumps", hours: 4.5, phase: "Phase 500", crew_size: 2, needs: ["row:2", "row:1"] },
	],
};

(async () => {
	// ---- the footer offers it, between the existing actions and the primary one
	let page = make_page();
	page.t1_new_id();
	out.t4_actions = page.t4_actions({}).map((a) => a.label);
	const footer = page.t1_actions({ rows: [] }, 2, 1);
	out.footer = footer.map((a) => a.label);
	const bare = Object.create(ctx.METHODS);
	bare.t4_actions = undefined;
	out.footer_without_t4 = bare.t1_actions({ rows: [] }, 0, 0).map((a) => a.label);

	// ---- the dialog builds while its fields ask for their options (empty grid: no Append / Replace)
	page = make_page();
	let state = grid(page, 3);
	let before = dialogs.length;
	let dlg = page.t4_open(state);
	out.empty_built = [dialogs.length - before, dlg.shown, dlg.fields.map((f) => f.fieldtype), dlg.fields.map((f) => f.fieldname)];
	out.title = dlg.opts.title;
	// ---- with tasks typed in it asks Append or Replace
	state.rows[0].subject = "Typed by hand";
	before = dialogs.length;
	dlg = page.t4_open(state);
	out.rows_built = [dialogs.length - before, dlg.fields.map((f) => f.fieldtype), dlg.fields.find((f) => f.fieldtype === "Select").options];
	// ---- no grid yet: nothing opens
	out.no_grid = [page.t4_open({ rows: null }), page.t4_open(null)];

	// ---- Draft into an empty grid: replaces it, asks the server for that project, marks the rows
	page = make_page();
	state = grid(page, 3);
	reply = { message: answer };
	dlg = page.t4_open(state);
	const pending = dlg.opts.primary_action({ t4_brief: "  Rebuild   the basin " });
	out.spinner = dlg.said_all[0];
	out.disabled_while = dlg.disabled.slice();
	await pending;
	out.call = calls.map((c) => [c.method, c.args]);
	out.rows = view(state);
	out.needs_are_ids = state.rows.map((row) => row.needs);
	out.ids = state.rows.map((row) => row.id);
	out.more = state.rows.map((row) => row.needs_more || null);
	out.note = state.note;
	out.hidden = dlg.hidden;
	out.disabled_after = dlg.disabled.slice();
	out.focus = page.rebuilt[page.rebuilt.length - 1];
	out.busy_after = !!state.t4_busy;

	// ---- Append keeps what was typed (unmarked) and tells the AI about it
	calls.length = 0;
	page = make_page();
	state = grid(page, 3);
	state.rows[0].subject = "Typed by hand";
	dlg = page.t4_open(state);
	await dlg.opts.primary_action({ t4_brief: "", t4_how: "Append" });
	out.append_call = calls.map((c) => c.args);
	out.append_rows = view(state).map((row) => [row[0], row[5]]);
	out.append_needs_first = state.rows[2].needs === `row:${state.rows[1].id}`;

	// ---- Replace drops it and sends nothing of the grid
	calls.length = 0;
	page = make_page();
	state = grid(page, 3);
	state.rows[0].subject = "Typed by hand";
	dlg = page.t4_open(state);
	await dlg.opts.primary_action({ t4_brief: "x", t4_how: "Replace" });
	out.replace_call = calls.map((c) => c.args);
	out.replace_rows = view(state).map((row) => [row[0], row[5]]);

	// ---- Draft mode sends draft=1
	calls.length = 0;
	page = make_page({ draft_on: true });
	state = grid(page, 2);
	dlg = page.t4_open(state);
	await dlg.opts.primary_action({ t4_brief: "" });
	out.draft_call = calls.map((c) => c.args);

	// ---- Append never grows the grid past its maximum
	page = make_page();
	state = grid(page, 3);
	state.rows[0].subject = "Typed by hand";
	reply = { message: { ok: true, note: "n", rows: Array.from({ length: 100 }, (_, i) => ({ subject: `T${i}`, hours: 1, phase: "", crew_size: 0, needs: [] })) } };
	dlg = page.t4_open(state);
	await dlg.opts.primary_action({ t4_how: "Append" });
	out.cap = state.rows.length;

	// ---- The AI could not help: the dialog stays, says why, and the grid is left alone
	out.fails = [];
	for (const message of [{ ok: false, rows: [], message: "AI drafting is not set up on this site." }, { ok: true, rows: [] }, null, {}]) {
		reply = { message };
		page = make_page();
		state = grid(page, 2);
		state.rows[0].subject = "Typed by hand";
		dlg = page.t4_open(state);
		await dlg.opts.primary_action({ t4_brief: "" });
		out.fails.push([view(state).map((row) => row[0]), dlg.hidden, dlg.said, dlg.disabled[dlg.disabled.length - 1]]);
	}
	// ---- The call itself failed (offline): same
	rejects = true;
	page = make_page();
	state = grid(page, 2);
	dlg = page.t4_open(state);
	await dlg.opts.primary_action({ t4_brief: "" });
	out.offline = [dlg.hidden, dlg.said, state.rows.length, !!state.t4_busy];
	rejects = false;

	// ---- A second press while it works does nothing
	reply = { message: answer };
	calls.length = 0;
	page = make_page();
	state = grid(page, 2);
	dlg = page.t4_open(state);
	const first = dlg.opts.primary_action({ t4_brief: "" });
	const second = dlg.opts.primary_action({ t4_brief: "" });
	await first; await second;
	out.double = calls.length;
	out.reopen_while_busy = (() => { state.t4_busy = true; const r = page.t4_open(state); state.t4_busy = false; return r; })();

	// ---- The planner closed the grid while it worked: nothing is applied
	calls.length = 0;
	page = make_page();
	let open = true;
	state = grid(page, 2, { handle: { is_open: () => open, close: () => null } });
	dlg = page.t4_open(state);
	const slow = dlg.opts.primary_action({ t4_brief: "" });
	open = false;
	await slow;
	out.closed = [view(state).map((row) => row[0]), dlg.hidden];

	// ---- t1_row_el marks an AI row and only that
	const made = [];
	const stub = Object.assign(make_page(), {
		t1_el: (tag, props) => {
			const el = { tag, props, value: "", attrs: {}, classes: [], classList: { add: (c) => el.classes.push(c) }, setAttribute: (k, v) => { el.attrs[k] = v; }, appendChild: () => null };
			made.push(el);
			return el;
		},
	});
	const plain = ctx.P1.blank_row("r1");
	const marked = Object.assign(ctx.P1.blank_row("r2"), { ai: true });
	stub.t1_row_el({ list_id: "l" }, plain, 0);
	stub.t1_row_el({ list_id: "l" }, marked, 1);
	const trs = made.filter((el) => el.props && el.props["data-t1-id"]);
	out.marks = trs.map((el) => [el.props["data-t1-id"], el.classes, el.attrs.title || null]);

	// ---- pure helpers
	out.args_append = ctx.P4.call_args("PRJ-1", "  a   b ", [{ subject: " X  y " }, { subject: "" }], "append", { draft: 1 });
	out.args_replace = ctx.P4.call_args("PRJ-1", "b", [{ subject: "X" }], "replace", null);
	out.args_cap = ctx.P4.call_args("PRJ-1", "z".repeat(5000), [], "replace", {}).brief.length;
	out.plan = [ctx.P4.plan_from(null), ctx.P4.plan_from({ ok: false }), ctx.P4.plan_from({ ok: true, rows: [] }), ctx.P4.plan_from({ ok: true, rows: [1], note: "" })];
	process.stdout.write(JSON.stringify(out));
})().catch((e) => {
	process.stderr.write(String(e && e.stack ? e.stack : e));
	process.exit(1);
});
"""


def _slice(code, first, last):
	return code[code.index(first) : code.index(last)]


class TestPageUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		if not node:
			raise unittest.SkipTest("node is not on PATH")
		code = PP_JS.read_text(encoding="utf-8")
		t1 = _slice(code, "const PPT1 = {", "Object.assign(ProjectPlanner.prototype, PPT1_METHODS);")
		t4 = _slice(code, "const PPT4 = {", "Object.assign(ProjectPlanner.prototype, PPT4_METHODS);")
		result = subprocess.run(
			[node, "-e", T4_HARNESS],
			input=json.dumps([t1 + "\n" + t4]),
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
		if result.returncode != 0:
			raise AssertionError(result.stderr)
		cls.out = json.loads(result.stdout)

	def test_the_footer_offers_ai_draft_before_add_row_and_create(self):
		out = self.out
		self.assertEqual(out["t4_actions"], ["AI draft…"])
		self.assertEqual(
			out["footer"],
			["Paste a list…", "Copy from project…", "AI draft…", "Add row", "Create 2 tasks and 1 phase"],
		)
		# Where only the T1 block is loaded, the footer is what it was.
		self.assertEqual(
			out["footer_without_t4"], ["Paste a list…", "Copy from project…", "Add row", "Create tasks"]
		)

	def test_the_dialog_builds_while_its_fields_ask_for_their_options(self):
		built, shown, kinds, names = self.out["empty_built"]
		self.assertEqual((built, shown), (1, True))
		self.assertEqual(kinds, ["HTML", "Small Text", "HTML"])
		self.assertEqual(names, ["t4_help", "t4_brief", "t4_status"])
		self.assertEqual(self.out["title"], "AI draft")

	def test_append_or_replace_is_asked_only_when_the_grid_has_tasks(self):
		built, kinds, options = self.out["rows_built"]
		self.assertEqual(built, 1)
		self.assertEqual(kinds, ["HTML", "Small Text", "Select", "HTML"])
		self.assertEqual(options, "Append\nReplace")
		self.assertEqual(self.out["no_grid"], [None, None])

	def test_it_shows_a_spinner_and_disables_the_button_while_it_works(self):
		self.assertIn("t4-spin", self.out["spinner"])
		self.assertIn("Drafting", self.out["spinner"])
		self.assertEqual(self.out["disabled_while"], [["disabled", True]])
		self.assertEqual(self.out["disabled_after"][-1], ["disabled", False])
		self.assertFalse(self.out["busy_after"])

	def test_the_draft_fills_an_empty_grid_through_one_call_for_that_project(self):
		out = self.out
		self.assertEqual(
			out["call"],
			[
				[
					"erpnext_enhancements.api.planner_ai_draft.draft_task_breakdown",
					{"project": "PRJ-1", "brief": "Rebuild the basin"},
				]
			],
		)
		self.assertTrue(out["hidden"])
		rows = out["rows"]
		self.assertEqual(
			[r[:4] for r in rows],
			[
				["Excavate basin", "16", "Phase 200", "2"],
				["Pour slab", "8", "Phase 300", ""],
				["Set pumps", "4.5", "Phase 500", "2"],
			],
		)
		# row:N of the draft become the ids of the grid's own rows, as with "Copy from project...".
		ids = out["ids"]
		self.assertEqual(out["needs_are_ids"], ["", f"row:{ids[0]}", f"row:{ids[1]}"])
		self.assertEqual(out["more"], [None, None, [ids[0]]])
		self.assertEqual(out["focus"][1], {"row": 0, "col": "subject"})

	def test_the_rows_are_marked_as_the_ais_and_the_note_says_to_review(self):
		self.assertTrue(all(row[5] for row in self.out["rows"]))
		self.assertEqual(self.out["note"], "AI draft — review before creating.")

	def test_append_keeps_what_was_typed_unmarked_and_tells_the_ai_about_it(self):
		out = self.out
		(args,) = out["append_call"]
		self.assertEqual(json.loads(args["grid_subjects"]), ["Typed by hand"])
		self.assertEqual(
			out["append_rows"],
			[["Typed by hand", False], ["Excavate basin", True], ["Pour slab", True], ["Set pumps", True]],
		)
		self.assertTrue(out["append_needs_first"])

	def test_replace_drops_what_was_typed_and_sends_nothing_of_the_grid(self):
		(args,) = self.out["replace_call"]
		self.assertNotIn("grid_subjects", args)
		self.assertEqual(
			[row[0] for row in self.out["replace_rows"]], ["Excavate basin", "Pour slab", "Set pumps"]
		)
		self.assertTrue(all(row[1] for row in self.out["replace_rows"]))

	def test_draft_mode_sends_draft_one(self):
		(args,) = self.out["draft_call"]
		self.assertEqual(args["draft"], 1)
		self.assertEqual(args["project"], "PRJ-1")

	def test_the_grid_never_holds_more_than_a_hundred_rows(self):
		self.assertEqual(self.out["cap"], 100)

	def test_when_the_ai_cannot_help_the_dialog_stays_says_why_and_the_grid_is_left_alone(self):
		fails = self.out["fails"]
		self.assertEqual(len(fails), 4)
		for index, (subjects, hidden, said, last) in enumerate(fails):
			self.assertEqual(subjects, ["Typed by hand", ""], index)
			self.assertFalse(hidden, index)
			self.assertIn("t4-bad", said, index)
			self.assertEqual(last, ["disabled", False], index)
		self.assertIn("not set up", fails[0][2])
		for index in (1, 2, 3):  # no message at all: the page's own words
			self.assertIn("could not be made", fails[index][2], index)

	def test_a_call_that_fails_outright_says_so_and_changes_nothing(self):
		hidden, said, rows, busy = self.out["offline"]
		self.assertFalse(hidden)
		self.assertIn("could not be reached", said)
		self.assertEqual(rows, 2)
		self.assertFalse(busy)

	def test_pressing_it_twice_asks_once_and_a_second_dialog_does_not_open_meanwhile(self):
		self.assertEqual(self.out["double"], 1)
		self.assertIsNone(self.out["reopen_while_busy"])

	def test_a_grid_closed_while_it_worked_is_left_alone(self):
		subjects, hidden = self.out["closed"]
		self.assertEqual(subjects, ["", ""])
		self.assertFalse(hidden)

	def test_an_ai_row_is_marked_on_screen_and_a_typed_one_is_not(self):
		(plain, marked) = self.out["marks"]
		self.assertEqual(plain, ["r1", [], None])
		self.assertEqual(marked, ["r2", ["t4-ai"], "AI draft — review before creating"])

	def test_the_calls_arguments(self):
		out = self.out
		self.assertEqual(
			out["args_append"], {"project": "PRJ-1", "brief": "a b", "draft": 1, "grid_subjects": '["X y"]'}
		)
		self.assertEqual(out["args_replace"], {"project": "PRJ-1", "brief": "b"})
		self.assertEqual(out["args_cap"], 1500)
		self.assertEqual(out["plan"][:3], [None, None, None])
		self.assertEqual(out["plan"][3]["note"], "AI draft — review before creating.")


class TestPageSource(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.code = PP_JS.read_text(encoding="utf-8")
		start = cls.code.index(
			"// ====================================================================== T4:"
		)
		end = cls.code.find(
			"\n// ======================================================================", start + 60
		)
		cls.block = cls.code[start:] if end < 0 else cls.code[start:end]
		cls.bare = base._strip(cls.block)

	def test_the_page_parses(self):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not on PATH")
		result = subprocess.run([node, "--check", str(PP_JS)], capture_output=True, text=True, timeout=60)
		self.assertEqual(result.returncode, 0, result.stderr)

	def test_its_dialog_is_built_with_let_not_const(self):
		# `let dialog = null; dialog = new ...`, so a field's get_data asked during construction cannot hit
		# a binding that is not initialised yet (the v1.602.0 bug).
		self.assertNotRegex(self.bare, r"const \w+ = new frappe\.ui\.Dialog")
		self.assertEqual(
			len(re.findall(r"let dialog = null;\s*dialog = new frappe\.ui\.Dialog", self.bare)), 1
		)
		self.assertNotIn("get_data", self.bare)

	def test_the_only_server_call_is_the_draft_and_it_is_a_plain_call_not_a_write_through_send(self):
		self.assertEqual(
			re.findall(r"frappe\.call\(\{ method: `\$\{PPT4\.actions\}\.(\w+)`", self.bare),
			["draft_task_breakdown"],
		)
		self.assertNotIn("this.send(", self.bare)
		self.assertNotIn("t1_send", self.bare)
		self.assertNotIn("build_out_tasks", self.bare)

	def test_the_block_is_its_own_and_t1_reaches_it_in_two_lines_only(self):
		code = self.code
		self.assertIn("...(this.t4_actions ? this.t4_actions(state) : [])", code)
		self.assertEqual(code.count("this.t4_"), self.block.count("this.t4_") + 2)
		t1 = build._t1_block(code)
		self.assertNotIn("PPT4", t1)
		self.assertEqual(code.count("Object.assign(ProjectPlanner.prototype, PPT4_METHODS);"), 1)

	def test_the_dialog_says_it_picks_nobody_and_creates_nothing(self):
		self.assertIn("never picks a person or a day", self.block)
		self.assertIn("nothing is created until you press Create", self.block)
		self.assertIn("AI draft — review before creating", self.block)


# ---------------------------------------------------------------------- wiring and docs


class TestWiring(unittest.TestCase):
	def test_ci_runs_this_suite_as_its_own_step(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_planner_ai_draft -v", ci)
		step = ci[: ci.index("test_planner_ai_draft -v")].rsplit("- name:", 1)[1]
		self.assertIn("T4)", step.splitlines()[0])

	def test_the_readmes_and_changelog_describe_it(self):
		for path in (API_README, PE_README):
			text = path.read_text(encoding="utf-8")
			self.assertIn("planner_ai_draft", text, path.name)
			self.assertIn("draft_task_breakdown", text, path.name)
		self.assertIn("## [1.609.0]", CHANGELOG.read_text(encoding="utf-8"))

	def test_the_two_versions_agree(self):
		init = (APP / "__init__.py").read_text(encoding="utf-8")
		package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
		match = re.search(r'__version__ = "([^"]+)"', init)
		self.assertEqual(match.group(1), package["version"])

	def test_files_are_lf_only(self):
		for path in (MODULE, Path(__file__), PP_JS):
			self.assertNotIn(b"\r\n", path.read_bytes(), path.name)

	def test_the_module_is_tab_indented_like_its_siblings(self):
		lines = MODULE.read_text(encoding="utf-8").splitlines()
		self.assertFalse([line for line in lines if line.startswith("    ")])
		self.assertTrue([line for line in lines if line.startswith("\t")])


if __name__ == "__main__":
	unittest.main()
