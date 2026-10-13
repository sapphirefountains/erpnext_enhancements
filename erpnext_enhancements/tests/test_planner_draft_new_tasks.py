# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Project Planner T2 (v1.603.0): new tasks in Draft mode, paste a list, copy from another project.

Nik, 2026-10-12: "build out a whole new schedule as a draft and publish it once". Publishing is also
the moment work reaches Google Calendar (the sync reads saved Tasks only), so a drafted new task must
NEVER be a Task before Publish. What this pins, because each part can fail quietly:

* **Drafting a new task makes no Task.** Quick add, duplicate and build out with ``draft=1`` make the
  same checks as the live call (same errors, same row numbers), then keep the task-to-be as a
  ``New task`` Planner Draft Change and answer with a card named ``draft-new:<row>``. Nothing is
  inserted or saved on ``tabTask`` (the invariant the calendar sync relies on), and a split is still
  refused in draft mode.
* **Each planner reads its own rows.** The Project Planner reads ``task`` rows and its own ``New
  task`` rows and never a visit's; the Maintenance Planner reads ``visit`` rows only.
* **The planner draws them and the engine counts them**: a drafted new task is a card (dated ones on
  their day, undated ones in the tray) and an ``extra`` of ``engine._compute`` (never an ``exclude``),
  so free hours and conflicts include it; a row whose phase was discarded or whose project is no
  longer a customer job is out of date, not drawn.
* **Editing and removing**: a drag, resize or crew change goes through the same draft path and
  rewrites the row's spec; the Undo of a drafted create discards the row, and refuses while another
  drafted task is filed under or waits on it.
* **Publish creates them through the live path**: phases first, tasks after what they wait on,
  ``draft-new:`` names mapped to the real Tasks, every task checked with the Change rows in the one
  batch check (all or nothing on conflicts), each in its own savepoint (a refused one is reported and
  stays a Draft, and what waits on it is reported too), published rows record ``created_task``.
* **Copy from project** reads another project's leaf tasks as grid rows: subject, hours, phase, crew
  size and the dependencies between them; never dates, people, notes or addresses; templates and
  canceled tasks left out, finished ones kept; capped at the build-out maximum.
* **The grid's pure helpers** (the pasted-list parser, the copied rows) and its dialogs run under
  node. Every dialog is built through a stand-in that asks every ``get_data``/``get_query`` WHILE it
  is being constructed, the way Frappe does (the v1.602.0 "before initialization" bug).

Bench-free: reuses Phase 6B's fake site (via the build-out suite's), keeps Planner Draft Change rows
in ``frappe.docs`` and lists them from there. The node parts skip when node is not on PATH.

Run: python -m unittest erpnext_enhancements.tests.test_planner_draft_new_tasks
"""

import copy
import importlib
import importlib.util
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
from erpnext_enhancements.tests.test_planner_phase6b import (  # unittest looks both up in this module
	setUpModule,
	tearDownModule,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = Path(__file__).resolve().parents[1]
PP_JS = APP / "project_enhancements/page/project_planner/project_planner.js"
CONTROLLER = APP / "project_enhancements/doctype/planner_draft_change/planner_draft_change.py"
DOCTYPE_JSON = APP / "project_enhancements/doctype/planner_draft_change/planner_draft_change.json"
CI = REPO_ROOT / ".github/workflows/ci.yml"
API_README = APP / "api/README.md"
PE_README = APP / "project_enhancements/README.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

TUE, WED, FRI = base.TUE, base.WED, base.FRI
SAVED, MODIFIED = base.SAVED, base.MODIFIED
_Doc, _TaskDoc, _Throw, _PermissionError = base._Doc, base._TaskDoc, base._Throw, base._PermissionError
DRAFT = "Planner Draft Change"
ME = "nik@example.com"
REF = "draft-new:"


class _Site(build._Site):
	"""The build-out suite's site (a project with a phase and tasks) plus a table of Draft rows."""

	def setUp(self):
		super().setUp()
		self.api = base.api
		self.rows = []  # Draft rows a test seeds that are not documents
		self.frappe.tables[DRAFT] = self._draft_table
		self.real_batch_check = self.api._batch_check
		self.real_notify = self.api._notify_publish
		self.notified = []
		self.batch = {}
		self.batches_seen = []
		self.extra_patches = [
			mock.patch.object(self.api, "_batch_check", self._batch_check),
			mock.patch.object(self.api, "_notify_publish", self._notify),
		]
		for patch in self.extra_patches:
			patch.start()
		original = self.frappe.get_doc

		def get_doc(doctype, name=None):
			doc = original(doctype, name)
			# A fresh load each time, as from the database.
			return copy.deepcopy(doc) if doctype == "Task" and name else doc

		self.frappe.get_doc = get_doc

	def tearDown(self):
		for patch in self.extra_patches:
			patch.stop()
		super().tearDown()

	# -- the fake Planner Draft Change table -------------------------------------------------------

	def _draft_table(self, filters):
		rows = [dict(doc) for (doctype, _name), doc in self.frappe.docs.items() if doctype == DRAFT]
		rows += [dict(row) for row in self.rows]
		out = []
		for row in rows:
			ok = True
			for key, want in (filters or {}).items():
				value = row.get(key)
				if isinstance(want, list) and want[:1] == ["is"]:
					ok = ok and ((want[1] == "set") == bool(value))
				elif value != want:
					ok = False
			if ok:
				out.append(row)
		return out

	def _batch_check(self, plans):
		# A copy: publishing renames the plans (draft name to the Task's) as it creates them.
		self.batches_seen.append(copy.deepcopy(list(plans)))
		return copy.deepcopy(self.batch)

	def _notify(self, saved):
		self.notified.append([plan["name"] for plan in saved])
		return len(saved)

	def seed(self, name, **fields):
		row = dict(
			{
				"doctype": DRAFT,
				"name": name,
				"status": "Draft",
				"owner": ME,
				"kind": "Change",
				"modified": SAVED,
				"payload": "{}",
			},
			**fields,
		)
		self.frappe.docs[(DRAFT, name)] = _TaskDoc(row)
		return row

	def reverse_drafts(self):
		keys = [key for key in self.frappe.docs if key[0] == DRAFT]
		docs = [self.frappe.docs.pop(key) for key in keys]
		for key, doc in zip(reversed(keys), reversed(docs), strict=True):
			self.frappe.docs[key] = doc

	def draft_rows(self, status="Draft"):
		return [
			doc
			for (doctype, _n), doc in self.frappe.docs.items()
			if doctype == DRAFT and doc.get("status") == status
		]

	def new_rows(self, status="Draft"):
		return [row for row in self.draft_rows(status) if row.get("kind") == "New task"]

	def task_writes(self):
		"""Every Task inserted or saved: the invariant a calendar sync relies on."""
		return [row for row in self.frappe.inserted if row.get("doctype") == "Task"] + [
			row for row in self.frappe.saved if row.get("doctype") == "Task"
		]

	# -- the three calls ----------------------------------------------------------------------------

	def quick(self, **kwargs):
		args = dict(project="PRJ-1", subject="Dig", date=str(TUE), draft=1)
		args.update(kwargs)
		return self.actions.quick_add_task(**args)

	def build(self, rows, project="PRJ-1", **kwargs):
		kwargs.setdefault("draft", 1)
		return self.actions.build_out_tasks(project, json.dumps(rows), **kwargs)

	def spec(self, row):
		return self.api.load_spec(row.get("payload"))


# ====================================================================== the doctype


class TestDoctype(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		model = types.ModuleType("frappe.model")
		document = types.ModuleType("frappe.model.document")

		class Document(dict):
			pass

		document.Document = Document
		with mock.patch.dict(sys.modules, {"frappe.model": model, "frappe.model.document": document}):
			spec = importlib.util.spec_from_file_location("pdc_controller", CONTROLLER)
			cls.module = importlib.util.module_from_spec(spec)
			spec.loader.exec_module(cls.module)
		cls.meta = json.loads(DOCTYPE_JSON.read_text(encoding="utf-8"))

	def validate(self, **fields):
		self.module.PlannerDraftChange(fields).validate()

	def refused(self, **fields):
		with self.assertRaises(_Throw, msg=str(fields)):
			self.validate(**fields)

	def test_a_change_names_exactly_one_task_or_one_visit(self):
		self.validate(task="TASK-1")
		self.validate(task="TASK-1", kind="Change")
		self.validate(visit="SMR-1")
		self.refused(task="TASK-1", visit="SMR-1")
		self.refused()
		self.refused(kind="Change")

	def test_a_new_task_names_neither_and_carries_its_project(self):
		self.validate(kind="New task", project="PRJ-1")
		self.refused(kind="New task", project="PRJ-1", task="TASK-1")
		self.refused(kind="New task", project="PRJ-1", visit="SMR-1")
		self.refused(kind="New task")

	def test_only_a_new_task_names_a_project(self):
		self.refused(task="TASK-1", project="PRJ-1")
		self.refused(visit="SMR-1", project="PRJ-1")

	def test_the_columns(self):
		fields = {f["fieldname"]: f for f in self.meta["fields"]}
		kind = fields["kind"]
		self.assertEqual(kind["fieldtype"], "Select")
		self.assertEqual(kind["options"].split("\n"), ["Change", "New task"])
		# A normal doctype: the ALTER writes the default into every existing row, so no patch is needed.
		self.assertEqual(kind["default"], "Change")
		self.assertEqual(fields["project"]["options"], "Project")
		self.assertEqual(fields["created_task"]["options"], "Task")
		for name in ("kind", "project", "created_task", "payload"):
			self.assertEqual(fields[name].get("read_only"), 1, name)
		self.assertNotIn("reqd", fields["task"])
		self.assertEqual([p["role"] for p in self.meta["permissions"]][:1], ["System Manager"])
		self.assertEqual(self.meta["module"], "Project Enhancements")
		self.assertGreaterEqual(self.meta["modified"], "2026-10-12 21:00:00")

	def test_no_backfill_patch_was_written(self):
		patches = (APP / "patches.txt").read_text(encoding="utf-8")
		self.assertNotIn("planner_draft_change", patches.lower())
		self.assertNotIn("draft_change_kind", patches.lower())


# ====================================================================== which planner reads which rows


class TestWhichPlannerReadsWhich(_Site):
	def setUp(self):
		super().setUp()
		self.mdrafts = importlib.import_module("erpnext_enhancements.api.maintenance_drafts")
		self.seed("CH", task="TASK-1")
		self.seed("VI", visit="SMR-1", subject="Highlands")
		self.seed("NEW-MINE", kind="New task", project="PRJ-1", subject="Mine")
		self.seed("NEW-HERS", kind="New task", project="PRJ-1", subject="Hers", owner="lisa@example.com")
		self.seed("NEW-GONE", kind="New task", project="PRJ-1", subject="Gone", status="Discarded")
		self.seed("NEW-DONE", kind="New task", project="PRJ-1", subject="Done", status="Published")

	def names(self, rows):
		return [row.get("name") for row in rows]

	def test_the_project_planner_reads_its_task_rows_and_its_own_new_tasks_never_a_visit(self):
		rows, duplicates = self.api._draft_rows()
		self.assertEqual(self.names(rows), ["CH", "NEW-MINE"])
		self.assertEqual(duplicates, [])
		self.assertEqual([self.api.is_new_row(row) for row in rows], [False, True])
		self.assertNotIn("VI", self.names(rows))

	def test_the_maintenance_planner_never_sees_a_new_task(self):
		rows, _duplicates = self.mdrafts._draft_rows()
		self.assertEqual(self.names(rows), ["VI"])

	def test_before_the_migrate_the_new_column_is_not_asked_for(self):
		self.frappe.db.has_column = lambda doctype, column: column != "kind"
		rows, _duplicates = self.api._draft_rows()
		self.assertEqual(self.names(rows), ["CH"])
		self.frappe.db.has_column = mock.Mock(side_effect=RuntimeError("no table"))
		self.assertEqual(self.names(self.api._draft_rows()[0]), ["CH"])

	def test_the_source_filters(self):
		source = (APP / "api/project_planner.py").read_text(encoding="utf-8")
		at = source.index("def _draft_rows(user=None):")
		window = source[at : source.index("def _draft_edit(", at)]
		self.assertIn('"task": ["is", "set"]', window)
		self.assertIn('"kind": NEW_TASK', window)
		mdrafts = (APP / "api/maintenance_drafts.py").read_text(encoding="utf-8")
		self.assertIn('"visit": ["is", "set"]', mdrafts)
		self.assertNotIn("New task", mdrafts)


# ====================================================================== drafting never makes a Task


class TestDraftingMakesNoTask(_Site):
	def test_a_drafted_quick_add_is_one_new_task_row_and_no_task(self):
		tasks_before = copy.deepcopy(self.frappe.tables["Task"])
		result = self.quick(resource="RES-1", hours=4, tentative=1)
		self.assertTrue(result["drafted"])
		(row,) = self.new_rows()
		self.assertEqual(result["name"], f"{REF}{row['name']}")
		self.assertEqual(result["modified"], row["modified"])
		self.assertEqual(
			(row["kind"], row["project"], row["subject"], row["status"]),
			("New task", "PRJ-1", "Dig", "Draft"),
		)
		self.assertEqual(row.get("task"), None)
		self.assertEqual(row.get("visit"), None)
		spec = self.spec(row)
		self.assertEqual(spec["op"], "quick_add")
		self.assertEqual(
			spec["values"],
			{
				"subject": "Dig",
				"project": "PRJ-1",
				"exp_start_date": str(TUE),
				"exp_end_date": str(TUE),
				"expected_time": 4.0,
				"custom_tentative": 1,
			},
		)
		self.assertEqual(spec["crew"], [{"resource": "RES-1", "hours": None, "is_lead": 0}])
		# The invariant C2 relies on: nothing was inserted or saved on tabTask.
		self.assertEqual(self.task_writes(), [])
		self.assertEqual(self.frappe.tables["Task"], tasks_before)
		self.assertEqual([key for key in self.frappe.docs if key[0] == "Task"], [])
		self.assertEqual(self.frappe.comments, [])

	def test_the_card_is_a_hypothetical_task_named_by_its_row(self):
		result = self.quick()
		card = result["card"]
		self.assertEqual(card["name"], result["name"])
		self.assertTrue(card["name"].startswith(REF))
		self.assertTrue(card["drafted"])
		self.assertTrue(card["new_task"])
		self.assertEqual(card["start"], str(TUE))
		self.assertEqual(card["modified"], result["modified"])

	def test_drafting_checks_exactly_what_the_live_call_checks(self):
		cases = [
			dict(project="PRJ-INT"),
			dict(project="PRJ-NOPE"),
			dict(subject="   "),
			dict(subject="x" * 141),
			dict(date=None),
			dict(hours=-1),
			dict(hours=401),
			dict(resource="RES-3"),  # inactive
			dict(resource="RES-99"),
			dict(parent_task="TASK-A"),  # not a phase
			dict(parent_task="TASK-OTHER-P"),
			dict(depends_on='["TASK-P1"]'),  # a phase
			dict(address="ADDR-NOPE"),
			dict(start_time="09:00", end_time="08:00"),
		]
		for kwargs in cases:
			messages = []
			for draft in (0, 1):
				with self.assertRaises(_Throw, msg=str(kwargs)) as caught:
					self.quick(draft=draft, **kwargs)
				messages.append(str(caught.exception))
			self.assertEqual(messages[0], messages[1], kwargs)
		self.assertEqual(self.draft_rows(), [])
		self.assertEqual(self.frappe.inserted, [])

	def test_drafting_needs_create_permission_and_a_planner_role(self):
		self.frappe.perms["create"] = False
		with self.assertRaises(_PermissionError):
			self.quick()
		self.frappe.perms["create"] = True
		self.frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			self.quick()
		self.assertEqual(self.frappe.inserted, [])

	def test_a_conflict_is_a_warning_and_never_a_reason_prompt(self):
		self.answers = [{"Austin Healey": ["2026-10-13: Over by 2h"]}]
		result = self.quick(resource="RES-1")
		self.assertNotIn("needs_reason", result)
		self.assertEqual(result["warnings"], ["Not published yet. Austin Healey: 2026-10-13: Over by 2h"])
		self.assertEqual(result["conflicts"], {"Austin Healey": ["2026-10-13: Over by 2h"]})
		self.assertEqual(len(self.new_rows()), 1)

	def test_a_phase_and_a_wait_on_an_existing_task_are_kept_in_the_spec(self):
		self.quick(parent_task="TASK-P1", depends_on='["TASK-A"]', description="Bring ladders")
		spec = self.spec(self.new_rows()[0])
		self.assertEqual(spec["values"]["parent_task"], "TASK-P1")
		self.assertEqual(spec["depends_on"], [{"task": "TASK-A", "subject": "Dig the basin"}])
		self.assertEqual(spec["values"]["description"], "<p>Bring ladders</p>")

	def test_a_drafted_duplicate_copies_like_the_live_one_and_changes_nothing(self):
		base._task("TASK-1", custom_equipment=[])
		live = self.actions.duplicate_task("TASK-1")
		(inserted,) = self._inserted_tasks()
		self.frappe.inserted.clear()
		drafted = self.actions.duplicate_task("TASK-1", draft=1)
		self.assertTrue(drafted["drafted"])
		self.assertEqual(drafted["source"], "TASK-1")
		self.assertEqual(self._inserted_tasks(), [])
		(row,) = self.new_rows()
		spec = self.spec(row)
		self.assertEqual(spec["op"], "duplicate")
		self.assertEqual(spec["source"], "TASK-1")
		# The task the draft would make is the task the live call made.
		for key in (
			"subject",
			"project",
			"expected_time",
			"custom_crew_size",
			"custom_tentative",
			"custom_outdoor",
			"custom_customer_visit",
			"custom_locationaddress_of_task",
			"color",
			"description",
			"exp_start_date",
			"exp_end_date",
		):
			self.assertEqual(spec["values"].get(key), inserted.get(key), key)
		self.assertEqual(
			[(m["resource"], m["is_lead"]) for m in spec["crew"]],
			[(m["resource"], m["is_lead"]) for m in inserted["custom_crew"]],
		)
		self.assertEqual(spec["credentials"], ["Forklift"])
		self.assertEqual(spec["keep_crew"], ["RES-1", "RES-2"])
		self.assertNotIn("needs_reason", live)
		# The source task is untouched.
		self.assertEqual(self.frappe.saved, [])

	def test_a_drafted_duplicate_to_another_day_keeps_the_length(self):
		base._task("TASK-1")
		self.actions.duplicate_task("TASK-1", date="2026-10-19", draft=1)
		spec = self.spec(self.new_rows()[0])
		self.assertEqual(spec["values"]["exp_start_date"][:10], "2026-10-19")
		self.assertEqual(spec["values"]["exp_end_date"][:10], "2026-10-22")

	def test_a_drafted_duplicate_is_refused_like_a_live_one(self):
		base._task("TASK-1", custom_rental_booking="RB-1")
		for draft in (0, 1):
			with self.assertRaises(_Throw) as caught:
				self.actions.duplicate_task("TASK-1", draft=draft)
			self.assertIn("Rental Booking RB-1", str(caught.exception))
		self.assertEqual(self.draft_rows(), [])

	def test_a_split_is_still_refused_in_draft_mode(self):
		base._task("TASK-1")
		with self.assertRaises(_Throw) as caught:
			self.actions.split_task("TASK-1", str(WED), MODIFIED, draft=1)
		text = str(caught.exception)
		self.assertIn("Draft mode", text)
		self.assertIn("split", text)
		self.assertIn("cannot be a draft", text)
		self.assertEqual(self.draft_rows(), [])
		self.assertEqual(self.frappe.saved, [])
		self.assertEqual(self.frappe.inserted, [])

	def test_the_drafts_stop_at_the_limit(self):
		for index in range(self.api.DRAFT_LIMIT):
			self.seed(f"FULL-{index}", kind="New task", project="PRJ-1", subject=f"T{index}")
		with self.assertRaises(_Throw) as caught:
			self.quick()
		self.assertIn("too many unpublished", str(caught.exception))
		with self.assertRaises(_Throw):
			self.build([{"subject": "x"}])


class TestDraftedBuildOut(_Site):
	def test_every_row_and_phase_is_a_new_task_row_and_no_task(self):
		result = self.build(
			[
				{"subject": "Set pumps", "hours": 6, "phase": "Pumping"},
				{"subject": "Wire pumps", "phase": "Pumping", "depends_on": ["row:1"], "crew_size": 2},
				{"subject": "Test", "depends_on": ["row:2", "TASK-A"], "parent_task": "TASK-P1"},
			]
		)
		self.assertTrue(result["drafted"])
		self.assertEqual(result["count"], 3)
		self.assertEqual(self.task_writes(), [])
		rows = self.new_rows()
		self.assertEqual([row["subject"] for row in rows], ["Pumping", "Set pumps", "Wire pumps", "Test"])
		phase, pumps, wire, test = (self.spec(row) for row in rows)
		self.assertEqual(phase["values"], {"subject": "Pumping", "project": "PRJ-1", "is_group": 1})
		refs = [f"{REF}{row['name']}" for row in rows]
		self.assertEqual(pumps["values"]["parent_task"], refs[0])
		self.assertEqual(wire["values"]["parent_task"], refs[0])
		self.assertEqual(wire["values"]["custom_crew_size"], 2)
		self.assertEqual([d["task"] for d in wire["depends_on"]], [refs[1]])
		self.assertEqual(wire["depends_on"][0]["subject"], "Set pumps")
		self.assertEqual(test["values"]["parent_task"], "TASK-P1")
		self.assertEqual([d["task"] for d in test["depends_on"]], [refs[2], "TASK-A"])
		# The answer has the live answer's shape, in draft names, in the grid's order.
		self.assertEqual([phase["name"] for phase in result["phases"]], [refs[0]])
		self.assertEqual([task["name"] for task in result["tasks"]], refs[1:])
		self.assertEqual([task["row"] for task in result["tasks"]], [1, 2, 3])
		self.assertEqual(result["tasks"][2]["depends_on"], [refs[2], "TASK-A"])
		self.assertEqual(result["tasks"][1]["parent_task"], refs[0])

	def test_the_rows_are_made_in_dependency_order(self):
		self.build([{"subject": "Second", "depends_on": ["row:2"]}, {"subject": "First"}])
		self.assertEqual([row["subject"] for row in self.new_rows()], ["First", "Second"])

	def test_the_same_errors_name_the_same_rows(self):
		cases = [
			([{"subject": "ok"}, {"subject": ""}], "Row 2: give the task a subject."),
			([{"subject": "a", "depends_on": ["row:9"]}], "Row 1: it waits on row 9"),
			([{"subject": "a", "depends_on": ["row:1"]}], "a task cannot wait on itself"),
			(
				[{"subject": "a", "depends_on": ["row:2"]}, {"subject": "b", "depends_on": ["row:1"]}],
				"Rows 1, 2 wait on each other",
			),
			([{"subject": "a", "parent_task": "TASK-A"}], "Row 1: TASK-A is not a phase"),
			([{"subject": "a", "depends_on": ["TASK-P1"]}], "Row 1: TASK-P1 is a phase"),
			([{"subject": "a", "hours": 999}], "Row 1: hours must be between"),
		]
		for rows, message in cases:
			for draft in (0, 1):
				with self.assertRaises(_Throw, msg=str(rows)) as caught:
					self.build(rows, draft=draft)
				self.assertIn(message, str(caught.exception))
		self.assertEqual(self.draft_rows(), [])
		self.assertEqual(self.frappe.inserted, [])

	def test_a_second_drafted_build_out_can_build_on_the_first(self):
		self.build([{"subject": "Set pumps", "phase": "Pumping"}])
		(phase_row, task_row) = self.new_rows()
		phase_ref, task_ref = (f"{REF}{row['name']}" for row in (phase_row, task_row))
		context = self.actions.get_build_out_context("PRJ-1", draft=1)
		self.assertIn({"name": phase_ref, "subject": "Pumping", "draft": True}, context["phases"])
		self.assertIn(
			{"name": task_ref, "subject": "Set pumps", "status": "Open", "draft": True}, context["tasks"]
		)
		# ... and the live context never offers a draft.
		live = self.actions.get_build_out_context("PRJ-1")
		self.assertFalse([e for e in live["phases"] + live["tasks"] if e["name"].startswith(REF)])
		# A typed phase name that is the drafted phase is that phase, not a second one.
		self.build([{"subject": "Wire", "phase": "pumping", "depends_on": [task_ref]}])
		rows = self.new_rows()
		self.assertEqual(len(rows), 3)
		wire = self.spec(rows[2])
		self.assertEqual(wire["values"]["parent_task"], phase_ref)
		self.assertEqual([d["task"] for d in wire["depends_on"]], [task_ref])

	def test_a_live_build_out_never_accepts_a_draft_name(self):
		self.build([{"subject": "Set pumps", "phase": "Pumping"}])
		ref = f"{REF}{self.new_rows()[1]['name']}"
		with self.assertRaises(_Throw) as caught:
			self.build([{"subject": "x", "depends_on": [ref]}], draft=0)
		self.assertIn("is not a task of", str(caught.exception))
		self.assertEqual(self._inserted_tasks(), [])


# ====================================================================== showing drafts


class TestShowingDrafts(_Site):
	def test_a_drafted_new_task_is_an_extra_of_the_engine_and_never_an_exclude(self):
		self.quick(resource="RES-1", hours=4)
		overlay = self.api._draft_overlay()
		(ref,) = overlay["drafted"]
		self.assertTrue(ref.startswith(REF))
		self.assertEqual(overlay["exclude"], set())
		((state, crew),) = overlay["extra"]
		self.assertEqual((state["name"], state["subject"], state["project"]), (ref, "Dig", "PRJ-1"))
		self.assertEqual([m["resource"] for m in crew], ["RES-1"])
		self.assertTrue(overlay["drafted"][ref]["new"])
		self.assertEqual(overlay["stale"], [])

	def test_get_planner_hands_the_engine_the_extras(self):
		self.quick(resource="RES-1")
		seen = {}

		class Stop(Exception):
			pass

		def compute(start, end, *args, **kwargs):
			seen.update(kwargs)
			raise Stop

		with mock.patch.object(base.engine, "_compute", compute):
			with self.assertRaises(Stop):
				self.api.get_planner(str(base.MON), str(base.SUN), draft=1)
		(extra,) = seen["extra"]
		self.assertTrue(extra[0]["name"].startswith(REF))
		self.assertEqual(seen["exclude"], set())

	def test_an_undated_one_is_in_the_tray_and_a_dated_one_on_its_day(self):
		self.build([{"subject": "Undated", "hours": 2}])
		self.quick(subject="Dated", date=str(TUE))
		overlay = self.api._draft_overlay()
		cards = []
		for state, crew in overlay["extra"]:
			cards.append(
				self.api.build_card(
					state,
					crew,
					{},
					[],
					base.TODAY,
					True,
					depends_on=overlay["drafted"][state["name"]]["depends_on"],
				)
			)
		by_subject = {card["subject"]: card for card in cards}
		self.assertIsNone(by_subject["Undated"]["start"])
		self.assertEqual(by_subject["Dated"]["start"], str(TUE))
		result = {"tasks": cards, "unscheduled": [], "needs_crew": []}
		shown = self.api._with_drafts(result, overlay, base.MON, base.SUN)
		self.assertEqual([c["subject"] for c in shown["tasks"]], ["Dated"])
		self.assertEqual([c["subject"] for c in shown["unscheduled"]], ["Undated"])
		for card in cards:
			self.assertTrue(card["drafted"])
			self.assertTrue(card["new_task"])
		# Outside the range it leaves the calendar but stays counted.
		out_of_range = self.api._with_drafts(
			{"tasks": [dict(c) for c in cards], "unscheduled": [], "needs_crew": []},
			overlay,
			base.D(2026, 11, 2),
			base.D(2026, 11, 8),
		)
		self.assertEqual([c["subject"] for c in out_of_range["tasks"]], [])
		self.assertEqual(out_of_range["drafts"]["count"], 2)

	def test_a_drafted_phase_has_no_card_but_is_counted(self):
		self.build([{"subject": "Set pumps", "phase": "Pumping"}])
		overlay = self.api._draft_overlay()
		self.assertEqual(len(overlay["extra"]), 1)
		self.assertEqual([p["subject"] for p in overlay["phases"]], ["Pumping"])
		result = self.api._with_drafts(
			{"tasks": [], "unscheduled": [], "needs_crew": []}, overlay, base.MON, base.SUN
		)
		self.assertEqual(result["drafts"]["count"], 2)
		self.assertEqual(len(result["drafts"]["phases"]), 1)

	def test_a_card_says_what_it_waits_on_by_name_not_by_draft_name(self):
		self.build([{"subject": "First"}, {"subject": "Second", "depends_on": ["row:1", "TASK-A"]}])
		overlay = self.api._draft_overlay()
		waits = {
			state["subject"]: overlay["drafted"][state["name"]]["depends_on"]
			for state, _crew in overlay["extra"]
		}
		self.assertEqual(waits["Second"], ["First", "TASK-A"])
		second = next(state for state, _crew in overlay["extra"] if state["subject"] == "Second")
		card = {"name": second["name"], "credentials": [], "start": None}
		shown = self.api._with_drafts(
			{"tasks": [card], "unscheduled": [], "needs_crew": []}, overlay, base.MON, base.SUN
		)
		self.assertEqual(shown["unscheduled"][0]["depends_on"], ["First", "TASK-A"])

	def test_a_row_that_cannot_be_drawn_is_out_of_date(self):
		self.build([{"subject": "Set pumps", "phase": "Pumping"}, {"subject": "Wire", "phase": "Pumping"}])
		phase_row = self.new_rows()[0]
		# The phase is discarded: its tasks are out of date, not drawn.
		self.frappe.docs[(DRAFT, phase_row["name"])]["status"] = "Discarded"
		overlay = self.api._draft_overlay()
		self.assertEqual(overlay["extra"], [])
		self.assertEqual(sorted(s["subject"] for s in overlay["stale"]), ["Set pumps", "Wire"])
		self.assertTrue(all("discarded" in s["reason"] for s in overlay["stale"]))
		# The project stops being a customer job.
		self.frappe.docs[(DRAFT, phase_row["name"])]["status"] = "Draft"
		self.frappe.jobs = [dict(job, project_type="Internal") for job in self.frappe.jobs]
		overlay = self.api._draft_overlay()
		self.assertEqual(overlay["extra"], [])
		self.assertTrue(all("customer job" in s["reason"] for s in overlay["stale"]))
		self.assertEqual(len(overlay["stale"]), 3)

	def test_a_person_who_left_makes_it_out_of_date(self):
		self.quick(resource="RES-1")
		self.frappe.tables["Planner Resource"] = []
		overlay = self.api._draft_overlay()
		self.assertEqual(overlay["extra"], [])
		self.assertIn("Cannot be applied any more", overlay["stale"][0]["reason"])


# ====================================================================== editing and removing


class TestEditingAndRemoving(_Site):
	def drafted(self, **kwargs):
		result = self.quick(**kwargs)
		return result["name"], result["modified"]

	def test_a_drag_rewrites_the_spec_and_never_a_task(self):
		ref, modified = self.drafted(resource="RES-1", hours=4)
		result = self.api.save_task(ref, modified, start="2026-10-20", end="2026-10-21", draft=1)
		self.assertTrue(result["drafted"])
		self.assertEqual(result["name"], ref)
		self.assertEqual(result["card"]["start"], "2026-10-20")
		self.assertEqual(result["card"]["end"], "2026-10-21")
		(row,) = self.new_rows()
		spec = self.spec(row)
		self.assertEqual(
			(spec["values"]["exp_start_date"][:10], spec["values"]["exp_end_date"][:10]),
			("2026-10-20", "2026-10-21"),
		)
		self.assertEqual(spec["values"]["subject"], "Dig")
		self.assertEqual(self.task_writes(), [])
		self.assertEqual(len(self.new_rows()), 1)  # edited in place, not a second row

	def test_a_resize_hours_crew_size_pencil_and_qualifications_are_kept(self):
		ref, modified = self.drafted(resource="RES-1")
		self.api.save_task(
			ref,
			modified,
			expected_time=9,
			crew_size=3,
			tentative=1,
			credentials=json.dumps(["Forklift"]),
			draft=1,
		)
		values = self.spec(self.new_rows()[0])
		self.assertEqual(values["values"]["expected_time"], 9)
		self.assertEqual(values["values"]["custom_crew_size"], 3)
		self.assertEqual(values["values"]["custom_tentative"], 1)
		self.assertEqual(values["credentials"], ["Forklift"])

	def test_re_crewing_goes_through_add_crew_and_swap_crew(self):
		ref, modified = self.drafted(resource="RES-1")
		added = self.api.add_crew(ref, "RES-2", modified, draft=1)
		self.assertEqual(
			sorted(m["resource"] for m in self.spec(self.new_rows()[0])["crew"]), ["RES-1", "RES-2"]
		)
		self.api.swap_crew(ref, "RES-2", "RES-4", added["modified"], draft=1)
		self.assertEqual(
			sorted(m["resource"] for m in self.spec(self.new_rows()[0])["crew"]), ["RES-1", "RES-4"]
		)
		self.assertEqual(self.task_writes(), [])

	def test_an_edit_keeps_what_it_waits_on_and_where_it_came_from(self):
		self.build([{"subject": "First"}, {"subject": "Second", "depends_on": ["row:1"], "phase": "Pumping"}])
		first, second = [r for r in self.new_rows() if r["subject"] in ("First", "Second")]
		before = self.spec(second)
		ref = f"{REF}{second['name']}"
		self.api.save_task(ref, second["modified"], start="2026-10-20", draft=1)
		after = self.spec(self.frappe.docs[(DRAFT, second["name"])])
		self.assertEqual(after["depends_on"], before["depends_on"])
		self.assertEqual(after["values"]["parent_task"], before["values"]["parent_task"])
		self.assertEqual(after["op"], "build_out")
		self.assertEqual(after["values"]["exp_start_date"][:10], "2026-10-20")

	def test_an_out_of_date_or_missing_draft_is_refused(self):
		ref, modified = self.drafted()
		with self.assertRaises(_Throw) as caught:
			self.api.save_task(ref, "2020-01-01 00:00:00", start="2026-10-20", draft=1)
		self.assertIn("changed since", str(caught.exception))
		with self.assertRaises(_Throw) as caught:
			self.api.save_task(f"{REF}NOPE", modified, start="2026-10-20", draft=1)
		self.assertIn("not in your drafts", str(caught.exception))
		self.frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			self.api.save_task(ref, modified, start="2026-10-20", draft=1)

	def test_a_draft_of_another_person_is_not_editable(self):
		ref, modified = self.drafted()
		self.frappe.docs[(DRAFT, ref[len(REF) :])]["owner"] = "lisa@example.com"
		with self.assertRaises(_Throw):
			self.api.save_task(ref, modified, start="2026-10-20", draft=1)

	def test_move_many_in_draft_mode_moves_drafted_new_tasks_too(self):
		ref, modified = self.drafted(resource="RES-1")
		result = self.actions.move_many(
			json.dumps([{"task": ref, "modified": modified, "start": "2026-10-21", "end": "2026-10-21"}]),
			draft=1,
		)
		self.assertTrue(result["drafted"])
		self.assertEqual(result["moved"][0]["to"], "2026-10-21")
		self.assertEqual(self.spec(self.new_rows()[0])["values"]["exp_start_date"][:10], "2026-10-21")
		self.assertEqual(self.task_writes(), [])

	def test_undo_of_a_drafted_quick_add_discards_the_row(self):
		ref, modified = self.drafted()
		result = self.actions.remove_created_task(ref, modified)
		self.assertEqual((result["removed"], result["discarded"]), (ref, True))
		self.assertEqual(self.new_rows(), [])
		self.assertEqual(len(self.new_rows("Discarded")), 1)
		self.assertEqual(self.frappe.deleted, [])  # no Task was deleted: there was none

	def test_undo_of_a_drafted_build_out_discards_every_row(self):
		result = self.build(
			[{"subject": "A", "phase": "P"}, {"subject": "B", "depends_on": ["row:1"], "phase": "P"}]
		)
		undo = self.actions.remove_built_out_tasks(
			json.dumps([{"task": t["name"], "modified": t["modified"]} for t in result["tasks"]]),
			json.dumps([{"task": p["name"], "modified": p["modified"]} for p in result["phases"]]),
		)
		self.assertTrue(undo["discarded"])
		self.assertEqual(sorted(undo["removed"]), sorted(t["name"] for t in result["tasks"]))
		self.assertEqual(undo["phases"], [result["phases"][0]["name"]])
		self.assertEqual(self.new_rows(), [])
		self.assertEqual(self.frappe.deleted, [])

	def test_undo_is_refused_while_another_drafted_task_depends_on_it(self):
		result = self.build([{"subject": "A"}, {"subject": "B", "depends_on": ["row:1"]}])
		a, b = result["tasks"]
		with self.assertRaises(_Throw) as caught:
			self.actions.remove_created_task(a["name"], a["modified"])
		self.assertIn("Nothing was removed", str(caught.exception))
		self.assertEqual(len(self.new_rows()), 2)
		# Both together are fine.
		self.actions.remove_built_out_tasks(json.dumps([{"task": a["name"]}, {"task": b["name"]}]))
		self.assertEqual(self.new_rows(), [])

	def test_a_drafted_and_a_saved_task_cannot_be_undone_together(self):
		result = self.build([{"subject": "A"}])
		with self.assertRaises(_Throw):
			self.actions.remove_built_out_tasks(
				json.dumps([{"task": result["tasks"][0]["name"]}, {"task": "TASK-A"}])
			)
		self.assertEqual(len(self.new_rows()), 1)

	def test_discard_drafts_covers_new_task_rows(self):
		self.build([{"subject": "A"}, {"subject": "B"}])
		self.seed("CH", task="TASK-1")
		refs = [f"{REF}{row['name']}" for row in self.new_rows()]
		self.assertEqual(self.api.discard_drafts(json.dumps([refs[0]])), {"discarded": 1})
		self.assertEqual(len(self.new_rows()), 1)
		self.assertEqual(self.api.discard_drafts(), {"discarded": 2})  # the other new task and the change
		self.assertEqual(self.draft_rows(), [])

	def test_discard_never_touches_a_visit_draft(self):
		self.build([{"subject": "A"}])
		self.seed("VI", visit="SMR-1")
		self.assertEqual(self.api.discard_drafts(), {"discarded": 1})
		self.assertEqual(self.frappe.docs[(DRAFT, "VI")]["status"], "Draft")


# ====================================================================== publish


class TestPublish(_Site):
	def published(self, **kwargs):
		return self.api.publish_drafts(**kwargs)

	def created(self):
		return self._inserted_tasks()

	def test_publish_creates_phases_first_then_tasks_after_what_they_wait_on(self):
		# Drafted in an order where a task comes before what it waits on, and a phase after.
		self.build(
			[
				{"subject": "Test", "depends_on": ["row:2"], "phase": "Pumping"},
				{"subject": "Wire", "depends_on": ["row:3"], "phase": "Pumping"},
				{"subject": "Set pumps", "hours": 6, "phase": "Pumping", "depends_on": ["TASK-A"]},
			]
		)
		# The rows come back newest first: the tasks before what they wait on, the phase last.
		self.reverse_drafts()
		self.assertEqual([r["subject"] for r in self.new_rows()], ["Test", "Wire", "Set pumps", "Pumping"])
		result = self.published()
		made = self.created()
		self.assertEqual([t["subject"] for t in made], ["Pumping", "Set pumps", "Wire", "Test"])
		phase, pumps, wire, test = made
		# Real names everywhere: no draft name ever reaches a Task.
		self.assertTrue(phase["is_group"])
		for task in made:
			self.assertNotIn(REF, json.dumps(task))
			self.assertEqual(task["status"], "Open")
		real = {t["subject"]: name for t, name in zip(made, self._created_names(), strict=True)}
		self.assertEqual(pumps["parent_task"], real["Pumping"])
		self.assertEqual([d["task"] for d in pumps["depends_on"]], ["TASK-A"])
		self.assertEqual([d["task"] for d in wire["depends_on"]], [real["Set pumps"]])
		self.assertEqual([d["task"] for d in test["depends_on"]], [real["Wire"]])
		self.assertEqual(wire["parent_task"], real["Pumping"])
		self.assertEqual(pumps["expected_time"], 6)
		# Every row is Published and says which Task it became.
		self.assertEqual(self.new_rows(), [])
		published = self.new_rows("Published")
		self.assertEqual(len(published), 4)
		self.assertEqual(sorted(row["created_task"] for row in published), sorted(real.values()))
		self.assertEqual(result["failed"], [])
		self.assertEqual(result["skipped"], [])
		self.assertEqual(
			[(p["subject"], p["new"]) for p in result["published"]],
			[("Pumping", True), ("Set pumps", True), ("Wire", True), ("Test", True)],
		)
		self.assertTrue(all(p["task"].startswith("TASK-NEW-") for p in result["published"]))
		self.assertEqual(self.notified, [[p["task"] for p in result["published"]]])

	def _created_names(self):
		"""The names the Tasks were given, in the order they were created."""
		return [event[2] for event in self.frappe.events if event[0] == "insert" and event[1] == "Task"]

	def test_publishing_makes_no_task_until_it_is_called(self):
		self.quick(resource="RES-1")
		self.build([{"subject": "A"}])
		self.assertEqual(self.created(), [])
		self.published()
		self.assertEqual(len(self.created()), 2)

	def test_the_new_tasks_join_the_batch_check(self):
		self.quick(resource="RES-1", hours=4)
		self.build([{"subject": "Undated"}, {"subject": "Phased", "phase": "P"}])
		base._task("TASK-1")
		self.seed(
			"CH",
			task="TASK-1",
			base_modified=MODIFIED,
			payload=json.dumps({"start": "2026-10-09", "end": "2026-10-09"}),
		)
		self.published()
		(plans,) = self.batches_seen
		by_name = {plan["name"] for plan in plans}
		self.assertIn("TASK-1", by_name)
		new = [plan for plan in plans if plan.get("new")]
		# Tasks with an "after" are checked; a drafted phase has no days and no people.
		self.assertEqual(sorted(plan["subject"] for plan in new), ["Dig", "Phased", "Undated"])
		for plan in new:
			self.assertTrue(plan["name"].startswith(REF))
			self.assertIsNone(plan["before"])
			self.assertEqual(plan["after"][0]["name"], plan["name"])
		dig = next(plan for plan in new if plan["subject"] == "Dig")
		self.assertEqual([m["resource"] for m in dig["after"][1]], ["RES-1"])
		# ... and the change was published beside them.
		self.assertTrue(any(s["name"] == "TASK-1" and s.get("doctype") == "Task" for s in self.frappe.saved))
		self.assertEqual(len(self.created()), 4)  # Dig, Undated, the phase, Phased

	def test_all_or_nothing_on_conflicts(self):
		self.quick(resource="RES-1", hours=12)
		self.batch = {"Austin Healey": ["2026-10-13: Over by 4h"]}
		result = self.published()
		self.assertTrue(result["needs_reason"])
		self.assertEqual(result["conflicts"], {"Austin Healey": ["2026-10-13: Over by 4h"]})
		self.assertEqual(result["count"], 1)
		self.assertEqual(self.created(), [])
		self.assertEqual(len(self.new_rows()), 1)  # still a Draft
		self.assertEqual(self.new_rows("Published"), [])
		# With the one reason it publishes, and the reason is on the new task's timeline.
		result = self.published(reason="Two crews that day")
		self.assertEqual(len(self.created()), 1)
		self.assertEqual(len(result["published"]), 1)
		self.assertEqual(self.new_rows("Published")[0]["reason"], "Two crews that day")
		(comment,) = self.frappe.comments
		self.assertIn("Reason: Two crews that day", comment[2])
		self.assertIn("Over by 4h", comment[2])

	def test_conflict_lines_name_a_new_task_by_its_subject(self):
		plans = [{"ref": f"{REF}DRAFT-1", "subject": "Dig"}, {"ref": f"{REF}DRAFT-10", "subject": "Wire"}]
		fixed = self.api._name_new_tasks(
			{"Austin": [f"2026-10-13: Double-booked ({REF}DRAFT-10, {REF}DRAFT-1)"], "Equipment": ["x"]},
			plans,
		)
		self.assertEqual(
			fixed,
			{"Austin": ["2026-10-13: Double-booked (Wire (new), Dig (new))"], "Equipment": ["x"]},
		)
		self.assertEqual(self.api._name_new_tasks({}, plans), {})

	def test_the_real_batch_check_counts_the_new_task_as_an_extra(self):
		self.quick(resource="RES-1", hours=4)
		(row,) = self.new_rows()
		plan, problem = self.api._publish_new_plan(row, {"PRJ-1"}, {f"{REF}{row['name']}"})
		self.assertIsNone(problem)
		calls = []

		def compute(first, last, people, **kwargs):
			calls.append((people, kwargs))
			if "extra" not in kwargs:
				return {"days": {}, "resources": [], "equipment_conflicts": {}}
			ref = plan["name"]
			return {
				"days": {
					"RES-1": {
						str(TUE): {"bookings": [{"ref": ref, "kind": "task"}], "conflicts": ["Over by 2h"]}
					}
				},
				"resources": [{"name": "RES-1", "label": "Austin Healey"}],
				"equipment_conflicts": {},
			}

		with mock.patch.object(self.api, "_batch_check", self.real_batch_check):
			with mock.patch.object(base.engine, "_compute", compute):
				conflicts = self.api._batch_check([plan])
		self.assertEqual(conflicts, {"Austin Healey": [f"{TUE}: Over by 2h"]})
		(_before_people, before_kwargs), (_after_people, after_kwargs) = calls
		self.assertNotIn("extra", before_kwargs)
		self.assertEqual(after_kwargs["exclude"], {plan["name"]})
		self.assertEqual([state["name"] for state, _crew in after_kwargs["extra"]], [plan["name"]])

	def test_a_task_erpnext_refuses_is_reported_and_stays_a_draft(self):
		self.build(
			[
				{"subject": "Good"},
				{"subject": "Bad"},
				{"subject": "Waits on bad", "depends_on": ["row:2"]},
				{"subject": "Also good", "depends_on": ["row:1"]},
			]
		)
		original = base._TaskDoc.insert

		def insert(doc):
			if doc.get("subject") == "Bad":
				raise _Throw("Date is out of the project's range")
			return original(doc)

		with mock.patch.object(base._TaskDoc, "insert", insert):
			result = self.published()
		self.assertEqual([t["subject"] for t in self.created()], ["Good", "Also good"])
		failed = {entry["subject"]: entry["reason"] for entry in result["failed"]}
		self.assertEqual(sorted(failed), ["Bad", "Waits on bad"])
		self.assertIn("Date is out of the project's range", failed["Bad"])
		self.assertIn("could not be created", failed["Waits on bad"])
		self.assertEqual(sorted(row["subject"] for row in self.new_rows()), ["Bad", "Waits on bad"])
		self.assertEqual(len(self.new_rows("Published")), 2)
		self.assertIn(("rollback", "planner_publish_new"), self.frappe.events)
		self.assertEqual(sorted(p["subject"] for p in result["published"]), ["Also good", "Good"])
		# Nothing half-created: the failed one left no Task and no comment behind.
		self.assertEqual(self.frappe.comments, [])

	def test_a_row_that_no_longer_applies_is_skipped_and_kept(self):
		self.build([{"subject": "A"}, {"subject": "B"}])
		self.frappe.jobs = [dict(job, project_type="Internal") for job in self.frappe.jobs]
		result = self.published()
		self.assertEqual(result["published"], [])
		self.assertEqual(sorted(s["subject"] for s in result["skipped"]), ["A", "B"])
		self.assertTrue(all(s["task"].startswith(REF) for s in result["skipped"]))
		self.assertTrue(all("customer job" in s["reason"] for s in result["skipped"]))
		self.assertEqual(self.created(), [])
		self.assertEqual(len(self.new_rows()), 2)

	def test_a_task_under_a_discarded_phase_is_reported_not_created(self):
		self.build([{"subject": "A", "phase": "P"}])
		phase = self.new_rows()[0]
		self.frappe.docs[(DRAFT, phase["name"])]["status"] = "Discarded"
		result = self.published()
		self.assertEqual(self.created(), [])
		self.assertEqual(result["published"], [])
		# It is out of date (skipped) rather than half made.
		self.assertEqual([s["subject"] for s in result["skipped"]], ["A"])
		self.assertEqual(len(self.new_rows()), 1)

	def test_a_loop_in_the_drafts_is_reported(self):
		self.build([{"subject": "A"}, {"subject": "B", "depends_on": ["row:1"]}])
		a, b = self.new_rows()
		spec = self.spec(a)
		spec["depends_on"] = [{"task": f"{REF}{b['name']}", "subject": "B"}]
		a["payload"] = json.dumps(spec)
		result = self.published()
		self.assertEqual(self.created(), [])
		self.assertEqual(sorted(f["subject"] for f in result["failed"]), ["A", "B"])
		self.assertTrue(all("round and round" in f["reason"] for f in result["failed"]))

	def test_the_planner_publishing_flag_is_up_while_they_are_created_and_down_after(self):
		self.quick()
		seen = []
		original = base._TaskDoc.insert

		def insert(doc):
			seen.append(bool(getattr(self.frappe.flags, "planner_publishing", False)))
			return original(doc)

		with mock.patch.object(base._TaskDoc, "insert", insert):
			self.published()
		self.assertEqual(seen, [True])
		self.assertFalse(self.frappe.flags.planner_publishing)

	def test_publish_needs_a_planner_role_and_only_publishes_the_callers_rows(self):
		self.quick()
		self.seed(
			"HERS",
			kind="New task",
			project="PRJ-1",
			subject="Hers",
			owner="lisa@example.com",
			payload=json.dumps({"values": {"subject": "Hers", "project": "PRJ-1"}}),
		)
		self.published()
		self.assertEqual([t["subject"] for t in self.created()], ["Dig"])
		self.assertEqual(self.frappe.docs[(DRAFT, "HERS")]["status"], "Draft")
		self.frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			self.published()

	def test_nothing_to_publish(self):
		self.assertEqual(
			self.published(), {"published": [], "skipped": [], "failed": [], "notified": 0, "conflicts": {}}
		)

	def test_the_publish_notice_is_about_the_task_that_was_created(self):
		# The real _notify_publish, with a recording notices module: a new task is new to its crew.
		notices = types.ModuleType("erpnext_enhancements.project_enhancements.planner_notices")
		calls, sent = [], []
		notices.snapshot = lambda state, users, hours=None: {
			"task": state.get("name"),
			"users": list(users),
			"span": base.engine.task_span(state),
		}
		notices.alert_window = lambda: None
		notices.setting = lambda key: False

		def change_lines(before, after, window=None, long=False):
			calls.append((before, after))
			return {
				"austin@example.com": [{"task": after["task"], "text": "You're on it", "day": "2026-10-13"}]
			}

		notices.change_lines = change_lines
		notices.send_publish_notices = lambda out, user: sent.append((out, user)) or len(out)
		self.quick(resource="RES-1")
		self.build([{"subject": "Undated", "phase": "Pumping"}])  # no days, and a phase: nobody to tell
		package = importlib.import_module("erpnext_enhancements.project_enhancements")
		with mock.patch.object(self.api, "_notify_publish", self.real_notify):
			with mock.patch.dict(
				sys.modules, {"erpnext_enhancements.project_enhancements.planner_notices": notices}
			):
				with mock.patch.object(package, "planner_notices", notices, create=True):
					result = self.published()
		self.assertEqual(len(result["published"]), 3)
		self.assertEqual(len(calls), 1)
		before, after = calls[0]
		self.assertIsNone(before)
		self.assertEqual(after["users"], ["austin@example.com"])
		((out, user),) = sent
		self.assertEqual(user, ME)
		dig = next(
			t for t, name in zip(self.created(), self._created_names(), strict=True) if t["subject"] == "Dig"
		)
		dig_name = self._created_names()[self.created().index(dig)]
		self.assertEqual(out["austin@example.com"]["task"], dig_name)
		self.assertFalse(dig_name.startswith(REF))


# ====================================================================== copy from another project


def _t(name, subject, parent=None, group=0, status="Open", hours=0, crew=0, lft=0, idx=0, **values):
	return dict(
		{
			"name": name,
			"subject": subject,
			"status": status,
			"is_group": group,
			"is_template": 0,
			"parent_task": parent,
			"expected_time": hours,
			"custom_crew_size": crew,
			"lft": lft,
			"idx": idx,
			"creation": f"2026-01-{(lft or idx or 1):02d} 09:00:00",
		},
		**values,
	)


class TestCopyPlan(unittest.TestCase):
	def plan(self, tasks, depends=(), **kwargs):
		return base.actions.copy_plan(tasks, list(depends), **kwargs)

	def test_leaf_tasks_become_rows_with_their_phase_hours_and_crew_size(self):
		tasks = [
			_t("P1", "Site prep", group=1, lft=1),
			_t("T1", "Excavate", parent="P1", hours=16, crew=2, lft=2),
			_t("T2", "Pour slab", parent="P1", hours=8, lft=3),
			_t("P2", "Pumps", group=1, lft=4),
			_t("T3", "Set pumps", parent="P2", hours=4.5, lft=5),
			_t("T4", "Loose task", hours=1, lft=6),
		]
		plan = self.plan(tasks)
		self.assertEqual(
			plan["rows"],
			[
				{"subject": "Loose task", "hours": 1.0, "phase": "", "crew_size": 0, "needs": []},
				{"subject": "Excavate", "hours": 16.0, "phase": "Site prep", "crew_size": 2, "needs": []},
				{"subject": "Pour slab", "hours": 8.0, "phase": "Site prep", "crew_size": 0, "needs": []},
				{"subject": "Set pumps", "hours": 4.5, "phase": "Pumps", "crew_size": 0, "needs": []},
			],
		)
		self.assertEqual((plan["count"], plan["total"], plan["truncated"]), (4, 4, False))

	def test_dependencies_become_row_numbers_and_one_outside_the_set_is_dropped_and_counted(self):
		tasks = [
			_t("A", "First", lft=1),
			_t("B", "Second", lft=2),
			_t("C", "Third", lft=3),
			_t("TPL", "Template", status="Template", lft=4),
			_t("X", "Canceled", status="Canceled", lft=5),
			_t("G", "A phase", group=1, lft=6),
		]
		depends = [
			{"parent": "B", "task": "A"},
			{"parent": "C", "task": "B"},
			{"parent": "C", "task": "A"},
			{"parent": "C", "task": "TPL"},  # a template: not copied
			{"parent": "C", "task": "X"},  # canceled
			{"parent": "C", "task": "G"},  # a phase
			{"parent": "C", "task": "ELSEWHERE"},
			{"parent": "TPL", "task": "A"},  # not a row at all
		]
		plan = self.plan(tasks, depends)
		self.assertEqual([row["subject"] for row in plan["rows"]], ["First", "Second", "Third"])
		self.assertEqual([row["needs"] for row in plan["rows"]], [[], ["row:1"], ["row:1", "row:2"]])
		self.assertEqual(plan["dropped_dependencies"], 4)

	def test_templates_and_canceled_are_left_out_finished_are_kept(self):
		tasks = [
			_t("A", "Finished", status="Completed", lft=1),
			_t("B", "Template", status="Template", lft=2),
			_t("C", "Also template", lft=3, is_template=1),
			_t("D", "Canceled", status="Canceled", lft=4),
			_t("E", "Cancelled", status="Cancelled", lft=5),
			_t("F", "Overdue", status="Overdue", lft=6),
			_t("G", "   ", lft=7),
		]
		plan = self.plan(tasks)
		self.assertEqual([row["subject"] for row in plan["rows"]], ["Finished", "Overdue"])
		self.assertEqual(plan["skipped"], {"templates": 2, "canceled": 2})

	def test_the_projects_own_order_phase_then_tree_position(self):
		tasks = [
			_t("T3", "In B, later", parent="PB", lft=9),
			_t("PB", "Phase B", group=1, lft=8),
			_t("T2", "In A, second", parent="PA", lft=3, idx=2),
			_t("T1", "In A, first", parent="PA", lft=3, idx=1),
			_t("PA", "Phase A", group=1, lft=2),
			_t("T0", "In B, first", parent="PB", lft=8, idx=1),
		]
		plan = self.plan(tasks)
		self.assertEqual(
			[row["subject"] for row in plan["rows"]],
			["In A, first", "In A, second", "In B, first", "In B, later"],
		)

	def test_the_cap_and_the_flag(self):
		tasks = [_t(f"T{i}", f"Task {i}", lft=i + 1) for i in range(130)]
		depends = [
			{"parent": "T1", "task": "T0"},
			{"parent": "T120", "task": "T0"},
			{"parent": "T0", "task": "T125"},
		]
		plan = self.plan(tasks, depends)
		self.assertEqual((plan["count"], plan["total"], plan["truncated"]), (100, 130, True))
		self.assertEqual(plan["rows"][1]["needs"], ["row:1"])
		self.assertEqual(plan["dropped_dependencies"], 1)  # T0 waits on T125, which is past the cap
		self.assertEqual(base.actions.BUILD_OUT_MAX_ROWS, 100)

	def test_nothing_personal_is_copied(self):
		tasks = [
			_t(
				"A",
				"Pump",
				hours=3,
				lft=1,
				exp_start_date="2026-10-12",
				exp_end_date="2026-10-13",
				description="<p>Gate code 1234, ask for Mrs. Smith</p>",
				custom_locationaddress_of_task="ADDR-1",
				custom_crew=[{"resource": "RES-1"}],
				owner="lisa@example.com",
				custom_tentative=1,
			)
		]
		(row,) = self.plan(tasks)["rows"]
		self.assertEqual(sorted(row), ["crew_size", "hours", "needs", "phase", "subject"])
		self.assertNotIn("1234", json.dumps(row))

	def test_an_empty_project(self):
		plan = self.plan([_t("P", "Phase only", group=1)])
		self.assertEqual((plan["rows"], plan["count"], plan["truncated"]), ([], 0, False))


class TestGetCopySource(_Site):
	def setUp(self):
		super().setUp()
		self.frappe.docs[("Project", "PRJ-9")] = _Doc(name="PRJ-9")
		self.frappe.values[("Project", "PRJ-9", "project_name")] = "Old fountain"
		self.frappe.tables["Task"] = [
			dict(_t("P", "Basin", group=1, lft=1), project="PRJ-9"),
			dict(_t("A", "Dig", parent="P", hours=8, lft=2), project="PRJ-9"),
			dict(_t("B", "Line", parent="P", hours=4, crew=2, lft=3), project="PRJ-9"),
			dict(_t("T", "Template", status="Template", lft=4), project="PRJ-9"),
			dict(_t("O", "Other project's", lft=5), project="PRJ-1"),
		]
		self.frappe.tables["Task Depends On"] = [
			{"parenttype": "Task", "parent": "B", "task": "A"},
			{"parenttype": "Task", "parent": "B", "task": "ELSEWHERE"},
		]

	def test_the_rows_of_any_project_the_user_can_read(self):
		# PRJ-9 is not a customer job on the planner's list: it is only a source.
		self.assertNotIn("PRJ-9", [job["name"] for job in self.frappe.jobs])
		result = self.actions.get_copy_source("PRJ-9")
		self.assertEqual(result["project"], "PRJ-9")
		self.assertEqual(result["title"], "Old fountain")
		self.assertEqual(
			result["rows"],
			[
				{"subject": "Dig", "hours": 8.0, "phase": "Basin", "crew_size": 0, "needs": []},
				{"subject": "Line", "hours": 4.0, "phase": "Basin", "crew_size": 2, "needs": ["row:1"]},
			],
		)
		self.assertEqual(result["dropped_dependencies"], 1)
		self.assertEqual(result["skipped"], {"templates": 1, "canceled": 0})
		self.assertIn("2 tasks copied", result["note"])
		self.assertIn("1 dependencies on tasks that were not copied", result["note"])
		self.assertIn("1 template", result["note"])

	def test_it_reads_the_callers_tasks_through_get_list_so_permissions_apply(self):
		seen = []
		original = self.frappe.get_list

		def get_list(doctype, **kwargs):
			seen.append((doctype, kwargs.get("filters")))
			return original(doctype, **kwargs)

		self.frappe.get_list = get_list
		self.actions.get_copy_source("PRJ-9")
		self.assertEqual(seen, [("Task", {"project": "PRJ-9"})])

	def test_refusals(self):
		for project in ("", None, "PRJ-NOPE"):
			with self.assertRaises(_Throw):
				self.actions.get_copy_source(project)
		self.frappe.perms["read"] = False
		with self.assertRaises(_PermissionError):
			self.actions.get_copy_source("PRJ-9")
		self.frappe.perms["read"] = True
		self.frappe.roles = ["Customer"]
		with self.assertRaises(_PermissionError):
			self.actions.get_copy_source("PRJ-9")

	def test_a_project_with_no_tasks(self):
		self.frappe.docs[("Project", "PRJ-EMPTY")] = _Doc(name="PRJ-EMPTY")
		result = self.actions.get_copy_source("PRJ-EMPTY")
		self.assertEqual((result["rows"], result["count"]), ([], 0))

	def test_it_is_a_get_and_writes_nothing(self):
		import ast

		source = (APP / "api/planner_actions.py").read_text(encoding="utf-8")
		node = next(
			n
			for n in ast.parse(source).body
			if isinstance(n, ast.FunctionDef) and n.name == "get_copy_source"
		)
		self.assertEqual([ast.unparse(d) for d in node.decorator_list], ["frappe.whitelist()"])
		self.actions.get_copy_source("PRJ-9")
		self.assertEqual(self.frappe.inserted, [])
		self.assertEqual(self.frappe.saved, [])


# ====================================================================== the page under node


def _t1_block(code):
	return build._t1_block(code)


def _pure_source(code):
	return build._pure_source(code)


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
out.list = P.parse_lines([
	"Excavate basin, 16h",
	"Pour slab - 8 hours",
	"Set pumps 4h",
	"• Pull wire (6 hrs)",
	"3. Commission system",
	"",
	"   ",
	"Electrical: Pull wire, 6h",
	"- Hang lights",
	"* [ ] Check GFCI, 1.5 hr",
	"[x] Walk the site",
	"2) Install 3 pumps, 4",
	"(4) Seal basin – 2.5h",
	"Phase 2: Fill and test",
	"Meet at 10:30",
	"Wire 12 gauge",
	"8h",
	"Pour slab: 8h",
	"Dig 2.5 hours",
	"Review, 0h",
	"http://example.com/plan",
	"• 1. Nested bullet, 3 hrs",
].join("\n"));
out.empty = [P.parse_lines(""), P.parse_lines(null), P.parse_lines("\n\n  \n")];
out.crlf = P.parse_lines("A task, 2h\r\nB task\r\n");
const made = P.copy_rows(
	[
		{ subject: "Dig", hours: 8, phase: "Basin", crew_size: 0, needs: [] },
		{ subject: "Line", hours: 4.5, phase: "Basin", crew_size: 2, needs: ["row:1"] },
		{ subject: "Fill", hours: 0, phase: "", crew_size: 0, needs: ["row:2", "row:1", "row:9"] },
	],
	(() => { let n = 0; return () => `c${++n}`; })()
);
out.copied = made;
const phases = [];
const payload = P.rows_payload(made, phases);
out.payload = payload;
// A copied task that waits on a row that is deleted is simply no longer waiting on it.
const trimmed = made.filter((row) => row.id !== "c1");
out.trimmed = P.rows_payload(trimmed, phases).rows.map((row) => row.depends_on || null);
process.stdout.write(JSON.stringify(out));
"""


class TestPureUnderNode(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		node = shutil.which("node")
		if not node:
			raise unittest.SkipTest("node is not on PATH")
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

	def row(self, subject, hours="", phase=""):
		return {"subject": subject, "hours": hours, "phase": phase, "crew_size": ""}

	def test_a_pasted_list_becomes_rows(self):
		self.assertEqual(
			self.out["list"],
			[
				self.row("Excavate basin", "16"),
				self.row("Pour slab", "8"),
				self.row("Set pumps", "4"),
				self.row("Pull wire", "6"),
				self.row("Commission system"),
				self.row("Pull wire", "6", "Electrical"),
				self.row("Hang lights"),
				self.row("Check GFCI", "1.5"),
				self.row("Walk the site"),
				self.row("Install 3 pumps", "4"),
				self.row("Seal basin", "2.5"),
				self.row("Fill and test", "", "Phase 2"),
				self.row("Meet at 10:30"),
				self.row("Wire 12 gauge"),
				self.row("Pour slab", "8"),
				self.row("Dig", "2.5"),
				self.row("Review", "0"),
				self.row("http://example.com/plan"),
				self.row("Nested bullet", "3"),
			],
		)

	def test_blank_lines_and_a_line_with_nothing_but_hours_are_skipped(self):
		self.assertEqual(self.out["empty"], [[], [], []])
		subjects = [row["subject"] for row in self.out["list"]]
		self.assertNotIn("8h", subjects)
		self.assertNotIn("", subjects)

	def test_windows_line_endings(self):
		self.assertEqual(self.out["crlf"], [self.row("A task", "2"), self.row("B task")])

	def test_copied_rows_carry_their_dependencies_as_the_ids_of_the_new_rows(self):
		copied = self.out["copied"]
		self.assertEqual([r["id"] for r in copied], ["c1", "c2", "c3"])
		self.assertEqual([r["subject"] for r in copied], ["Dig", "Line", "Fill"])
		self.assertEqual([r["hours"] for r in copied], ["8", "4.5", ""])
		self.assertEqual([r["crew_size"] for r in copied], ["", "2", ""])
		self.assertEqual([r["phase"] for r in copied], ["Basin", "Basin", ""])
		self.assertEqual([r["needs"] for r in copied], ["", "row:c1", "row:c2"])
		self.assertEqual(copied[2].get("needs_more"), ["c1"])  # row:9 is no row of this copy
		self.assertNotIn("needs_more", copied[0])

	def test_every_dependency_of_a_copied_row_reaches_the_server(self):
		payload = self.out["payload"]
		self.assertEqual(payload["problems"], [])
		self.assertEqual(
			[row.get("depends_on") for row in payload["rows"]], [None, ["row:1"], ["row:2", "row:1"]]
		)
		self.assertEqual(payload["new_phases"], ["Basin"])
		# Take the first row out and what waited on it waits on nothing there.
		self.assertEqual(self.out["trimmed"], [None, ["row:1"]])


class TestBuildOutServerAcceptsWhatTheGridSends(unittest.TestCase):
	def test_the_keys_the_copy_and_the_grid_send_are_keys_build_out_reads(self):
		source = (APP / "api/planner_actions.py").read_text(encoding="utf-8")
		for key in ("subject", "hours", "parent_task", "phase", "crew_size", "depends_on"):
			self.assertIn(f'item.get("{key}")', source, key)


DIALOG_HARNESS = r"""
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
		this.shown = false;
		this.hidden = false;
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
	show() { this.shown = true; this.ask(); }
	hide() { this.hidden = true; }
}
let reply = { message: { rows: [], note: "" } };
const ctx = {
	console,
	Promise,
	setTimeout: (fn) => fn(),
	clearTimeout: () => null,
	__: tr,
	pp_esc: (v) => String(v),
	pp_when: (ymd) => ymd,
	PP: {},
	PP6A: { owner: "pp" },
	frappe: {
		ui: { Dialog },
		msgprint: (m) => alerts.push(typeof m === "string" ? m : m.message),
		show_alert: (o) => alerts.push(o.message),
		call: (o) => { calls.push(o); return Promise.resolve(reply); },
	},
	document: { getElementById: () => true },
};
vm.createContext(ctx);
vm.runInContext(source + "\nthis.T1 = PPT1_METHODS; this.PPT1 = PPT1; this.P = PPT1_PURE;", ctx);
const out = {};
const make_page = (over) => {
	const rebuilt = [];
	const page = Object.assign(Object.create(ctx.T1), {
		draft_on: false,
		data: { can_edit: true },
		t1: { ids: 0, drafts: {}, needs: null },
		modified: {},
		draft_args() { return this.draft_on ? { draft: 1 } : {}; },
		t1_rebuild_rows: (state, focus) => rebuilt.push([state.rows.length, focus]),
		t1_check: () => null,
		t1_load_needs: () => null,
		load: () => null,
		push_undo: () => false,
	}, over || {});
	page.rebuilt = rebuilt;
	return page;
};
const grid = (page, n) => ({
	project: "PRJ-1",
	label: "Plaza",
	rows: Array.from({ length: n }, () => ctx.P.blank_row(page.t1_new_id())),
	table: {},
	handle: { is_open: () => true, close: () => null },
});
const filled = (state) => state.rows.map((row) => [row.subject, row.hours, row.phase, row.needs, row.needs_more || null]);

(async () => {
	// ---- Paste a list: the dialog opens, and its button fills the rows from the first blank one.
	let page = make_page();
	let state = grid(page, 3);
	state.rows[0].subject = "Already typed";
	const paste = page.t1_open_paste_list(state);
	out.paste_built = [dialogs.length, paste.shown, paste.fields.map((f) => f.fieldtype)];
	paste.opts.primary_action({ t1_list: "Excavate basin, 16h\nElectrical: Pull wire, 6h\n\n3. Commission" });
	out.paste_rows = filled(state);
	out.paste_hidden = paste.hidden;
	paste.opts.primary_action({ t1_list: "   \n\n" });
	out.paste_empty_alert = alerts[alerts.length - 1];
	// More lines than the grid holds: the rest are left out and the planner is told.
	state = grid(page, 1);
	page.t1_open_paste_list(state).opts.primary_action({ t1_list: Array.from({ length: 103 }, (_, i) => `Task ${i + 1}, 1h`).join("\n") });
	out.cap = [state.rows.length, alerts.filter((a) => /lines were left out/.test(a)).length];
	// No grid yet: nothing opens.
	out.no_grid = page.t1_open_paste_list({ rows: null });

	// ---- Copy from project: the dialog, then the server's rows into an empty grid.
	page = make_page();
	state = grid(page, 5);
	const before = dialogs.length;
	const copy = page.t1_open_copy(state);
	out.copy_built = [dialogs.length - before, copy.fields.map((f) => [f.fieldtype, f.options || null])];
	reply = { message: { rows: [
		{ subject: "Dig", hours: 8, phase: "Basin", crew_size: 0, needs: [] },
		{ subject: "Line", hours: 4, phase: "Basin", crew_size: 2, needs: ["row:1"] },
	], note: "2 tasks copied.", count: 2 } };
	copy.opts.primary_action({ t1_source: "PRJ-9" });
	await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
	out.copy_call = calls.map((c) => [c.method, c.args]);
	out.copy_rows = filled(state);
	out.copy_ids = state.rows.map((row) => row.id);
	out.copy_note = state.note;
	// A grid with something in it asks Append or Replace; Append keeps what was typed.
	page = make_page();
	state = grid(page, 3);
	state.rows[0].subject = "Typed by hand";
	const asking = dialogs.length;
	page.t1_load_copy(state, "PRJ-9");
	await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
	const how = dialogs[dialogs.length - 1];
	out.how_built = [dialogs.length - asking, how.fields.map((f) => f.fieldtype), how.opts.title];
	how.opts.primary_action({ t1_how: "Append" });
	out.append_rows = filled(state).map((row) => row.slice(0, 3));
	// ... and Replace drops it.
	page = make_page();
	state = grid(page, 3);
	state.rows[0].subject = "Typed by hand";
	page.t1_load_copy(state, "PRJ-9");
	await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
	dialogs[dialogs.length - 1].opts.primary_action({ t1_how: "Replace" });
	out.replace_rows = filled(state).map((row) => row.slice(0, 3));
	// A project with nothing to copy says so and leaves the grid alone.
	reply = { message: { rows: [], note: "0 tasks copied." } };
	page = make_page();
	state = grid(page, 2);
	const quiet = dialogs.length;
	page.t1_load_copy(state, "PRJ-EMPTY");
	await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
	out.empty_copy = [dialogs.length - quiet, state.rows.length, alerts[alerts.length - 1]];
	// Too many with what is there: the grid holds at most 100.
	reply = { message: { rows: Array.from({ length: 100 }, (_, i) => ({ subject: `T${i}`, hours: 1, phase: "", crew_size: 0, needs: [] })), note: "100 tasks copied.", count: 100 } };
	page = make_page();
	state = grid(page, 3);
	state.rows[0].subject = "Typed by hand";
	page.t1_load_copy(state, "PRJ-9");
	await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
	dialogs[dialogs.length - 1].opts.primary_action({ t1_how: "Append" });
	out.append_cap = state.rows.length;

	// ---- Create in Draft mode sends draft=1; otherwise it sends what it always sent.
	const sent = [];
	for (const draft_on of [false, true]) {
		page = make_page({
			draft_on,
			t1_send: (method, args) => { sent.push([method, args]); return Promise.resolve({ tasks: [{ name: "draft-new:X", subject: "A", modified: "m" }], phases: [], drafted: draft_on }); },
		});
		state = grid(page, 1);
		state.rows[0].subject = "A";
		state.phases = [];
		await page.t1_create(state);
	}
	out.create = sent.map(([method, args]) => [method, args.draft === undefined ? null : args.draft, args.project, JSON.parse(args.rows).length]);

	// ---- Opening the grid in Draft mode asks the context for the drafts too.
	const opened = [];
	ctx.frappe.call = (o) => { opened.push([o.method, o.args]); return Promise.resolve({ message: { phases: [], tasks: [] } }); };
	for (const draft_on of [false, true]) {
		page = make_page({
			draft_on,
			by_project: {},
			p6b_kit: () => ({ drawer: { open: () => ({ is_open: () => true, close: () => null, set_actions: () => null }) } }),
			$body: [{}],
			t1_el: () => ({}),
			t1_build_grid: () => null,
			t1_actions: () => [],
		});
		page.t1_can = () => true;
		await page.t1_open_build_out("PRJ-1", "Plaza");
	}
	out.opened = opened;
	process.stdout.write(JSON.stringify(out));
})().catch((e) => {
	process.stderr.write(String(e && e.stack ? e.stack : e));
	process.exit(1);
});
"""


class TestDialogsUnderNode(unittest.TestCase):
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
		result = subprocess.run(
			[node, "-e", DIALOG_HARNESS],
			input=json.dumps([t1]),
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
		if result.returncode != 0:
			raise AssertionError(result.stderr)
		cls.out = json.loads(result.stdout)

	def test_the_paste_dialog_builds_while_its_fields_ask_for_their_options(self):
		built, shown, kinds = self.out["paste_built"]
		self.assertEqual((built, shown), (1, True))
		self.assertEqual(kinds, ["HTML", "Small Text"])

	def test_pasting_a_list_fills_the_rows_from_the_first_blank_one(self):
		self.assertEqual(
			self.out["paste_rows"],
			[
				["Already typed", "", "", "", None],
				["Excavate basin", "16", "", "", None],
				["Pull wire", "6", "Electrical", "", None],
				["Commission", "", "", "", None],
			],
		)
		self.assertTrue(self.out["paste_hidden"])
		self.assertIn("Nothing to add", self.out["paste_empty_alert"])

	def test_the_grid_holds_at_most_a_hundred_rows(self):
		rows, told = self.out["cap"]
		self.assertEqual(rows, 100)
		self.assertGreaterEqual(told, 1)
		self.assertIsNone(self.out["no_grid"])

	def test_the_copy_dialog_builds_while_its_fields_ask_for_their_options(self):
		built, fields = self.out["copy_built"]
		self.assertEqual(built, 1)
		self.assertEqual([field[0] for field in fields], ["HTML", "Link"])
		self.assertEqual(fields[1][1], "Project")

	def test_copying_into_an_empty_grid_replaces_it_and_asks_the_server_for_that_project(self):
		self.assertEqual(
			self.out["copy_call"],
			[["erpnext_enhancements.api.planner_actions.get_copy_source", {"project": "PRJ-9"}]],
		)
		rows, ids = self.out["copy_rows"], self.out["copy_ids"]
		self.assertEqual([r[:3] for r in rows], [["Dig", "8", "Basin"], ["Line", "4", "Basin"]])
		self.assertEqual([r[3] for r in rows], ["", f"row:{ids[0]}"])
		self.assertEqual(self.out["copy_note"], "2 tasks copied.")

	def test_copying_into_a_grid_with_tasks_asks_append_or_replace(self):
		built, kinds, title = self.out["how_built"]
		self.assertEqual((built, kinds), (1, ["HTML", "Select"]))
		self.assertEqual(title, "The grid already has tasks")
		self.assertEqual(
			self.out["append_rows"],
			[["Typed by hand", "", ""], ["Dig", "8", "Basin"], ["Line", "4", "Basin"]],
		)
		self.assertEqual(self.out["replace_rows"], [["Dig", "8", "Basin"], ["Line", "4", "Basin"]])

	def test_a_project_with_nothing_to_copy_leaves_the_grid_alone(self):
		asked, rows, said = self.out["empty_copy"]
		self.assertEqual((asked, rows), (0, 2))
		self.assertIn("no tasks to copy", said)

	def test_appending_never_grows_the_grid_past_its_maximum(self):
		self.assertEqual(self.out["append_cap"], 100)

	def test_create_sends_draft_one_only_in_draft_mode(self):
		live, drafted = self.out["create"]
		self.assertEqual(live, ["build_out_tasks", None, "PRJ-1", 1])
		self.assertEqual(drafted, ["build_out_tasks", 1, "PRJ-1", 1])

	def test_opening_the_grid_in_draft_mode_asks_for_the_drafted_tasks_too(self):
		(live_call, draft_call) = self.out["opened"]
		self.assertEqual(live_call[1], {"project": "PRJ-1"})
		self.assertEqual(draft_call[1], {"project": "PRJ-1", "draft": 1})


class TestPageSource(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.code = PP_JS.read_text(encoding="utf-8")
		cls.block = _t1_block(cls.code)
		cls.bare = base._strip(cls.block)

	def test_the_page_parses(self):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not on PATH")
		result = subprocess.run([node, "--check", str(PP_JS)], capture_output=True, text=True, timeout=60)
		self.assertEqual(result.returncode, 0, result.stderr)

	def test_no_dialog_reads_a_const_that_its_own_construction_would_ask_for(self):
		# A dialog is `let x = null; x = new frappe.ui.Dialog(...)`, never `const x = new ...`, so a field's
		# get_data asked during construction cannot hit a binding that is not initialised yet.
		self.assertNotRegex(self.bare, r"const \w+ = new frappe\.ui\.Dialog")
		self.assertGreaterEqual(
			len(re.findall(r"let dialog = null;\s*dialog = new frappe\.ui\.Dialog", self.bare)), 3
		)

	def test_the_reads_are_plain_calls_and_the_writes_go_through_send(self):
		reads = set(re.findall(r"frappe\.call\(\{ method: `\$\{PPT1\.actions\}\.(\w+)`", self.bare))
		self.assertEqual(reads, {"get_projects_needing_tasks", "get_build_out_context", "get_copy_source"})
		self.assertEqual(
			set(re.findall(r'this\.t1_send\(\s*"(\w+)"', self.bare)),
			{"build_out_tasks", "remove_built_out_tasks"},
		)

	def test_the_page_sends_draft_one_for_the_three_creating_calls(self):
		self.assertIn("this.draft_args()", self._method("p6b_duplicate"))
		self.assertIn("this.draft_args()", self._method("p6b_quick_add_save"))
		self.assertRegex(
			self.bare, r'this\.t1_send\("build_out_tasks", Object\.assign\(\{[^}]*\}, this\.draft_args\(\)\)'
		)

	def _method(self, name):
		start = self.code.index(f"\n\t{name}(")
		return self.code[start : self.code.index("\n\t},\n", start)]

	def test_the_footer_has_the_two_new_actions_and_the_intro_mentions_them(self):
		actions = self.block[self.block.index("t1_actions(state, count, phases) {") :]
		actions = actions[: actions.index("\n\t},")]
		self.assertIn('__("Paste a list…")', actions)
		self.assertIn('__("Copy from project…")', actions)
		self.assertIn("paste a list", self.block)

	def test_the_spreadsheet_paste_is_kept_and_shares_its_filling(self):
		self.assertIn("parse_paste(text)", self.block)
		self.assertIn("this.t1_fill_rows(state, lines, at)", self.block)

	def test_a_split_is_the_only_new_task_the_page_still_refuses_in_draft_mode(self):
		self.assertIn("this.p6b_refuse_in_draft()", self._method("p6b_split"))
		for name in (
			"p6b_duplicate",
			"p6b_duplicate_to",
			"p6b_quick_add",
			"p6b_quick_add_save",
			"t1_open_build_out",
			"t1_create",
		):
			self.assertNotIn("p6b_refuse_in_draft", self._method(name), name)

	def test_a_drafted_new_task_gets_a_chip_and_no_task_lookups(self):
		self.assertIn("card.new_task", self.code)
		self.assertIn('__("New")', self.code)
		dialog = self.code[
			self.code.index("open_card(card) {") : self.code.index("save_dialog(card, dialog, values) {")
		]
		self.assertIn("const fresh = !!card.new_task;", dialog)


# ====================================================================== docs, release, CI


class TestDocsAndCi(unittest.TestCase):
	def test_the_version_is_bumped_in_both_places_and_written_up(self):
		init = (APP / "__init__.py").read_text(encoding="utf-8")
		package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
		# Not pinned to 1.603.0: every later release bumps both, and this test must not break on them.
		version = re.search(r'__version__ = "(\d+)\.(\d+)\.(\d+)"', init)
		self.assertIsNotNone(version)
		self.assertGreaterEqual(tuple(int(part) for part in version.groups()), (1, 603, 0))
		self.assertEqual(package["version"], ".".join(version.groups()))
		changelog = CHANGELOG.read_text(encoding="utf-8")
		self.assertIn("## [1.603.0] - 2026-10-12", changelog)
		section = changelog[changelog.index("## [1.603.0]") :].split("\n## [", 1)[0]
		for needle in (
			"New task",
			"draft-new:",
			"Paste a list",
			"Copy from project",
			"get_copy_source",
			"created_task",
			"split",
			"Planner Draft Change",
		):
			self.assertIn(needle, section, needle)

	def test_the_readmes_describe_it(self):
		api_readme = API_README.read_text(encoding="utf-8")
		for name in ("get_copy_source", "draft-new:", "New task"):
			self.assertIn(name, api_readme, name)
		pe = PE_README.read_text(encoding="utf-8")
		for needle in (
			"T2",
			"Paste a list",
			"Copy from project",
			"get_copy_source",
			"draft-new:",
			"created_task",
		):
			self.assertIn(needle, pe, needle)
		self.assertNotIn("A new task cannot be a draft", pe)

	def test_the_suite_runs_in_ci_as_unittest(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_planner_draft_new_tasks -v", ci)
		self.assertNotIn("pytest erpnext_enhancements/tests/test_planner_draft_new_tasks", ci)
		self.assertLess(
			ci.index("erpnext_enhancements.tests.test_planner_build_out -v"),
			ci.index("erpnext_enhancements.tests.test_planner_draft_new_tasks -v"),
		)


if __name__ == "__main__":
	unittest.main()
