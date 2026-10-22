# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""P3 of the Planner UX redesign: per-project schedule state, baseline, shared drafts and the
"Notify crew?" answer (``docs/planner-ux-redesign.md`` §4.8; Nik's D5, D8 and D11, 2026-10-21).

What this pins, because each of them fails quietly:

* **Publishing a project sets its baseline, once.** ``publish_project_schedule`` publishes only that
  project's drafts, then copies each dated task's first and last day into its baseline and marks
  the project Published. A task that has a baseline keeps it; ``reset_baseline`` re-copies them all.
  The copies are ``db.set_value(update_modified=False)``, never a save, so no planner that has the
  task open is told "changed by someone else". A publish that asks for a reason, or leaves a draft
  behind, changes no state and no baseline.
* **Publish and discard filter by project**, and without a project act on the caller's own rows
  only, as they always did: the board-wide draft mode never publishes somebody else's work.
* **A Draft project's drafts are shared.** Another scheduler's row on a Draft project is read,
  built on (one row per task, not one per person) and saved with ``ignore_permissions``; on a site
  without the P3 columns nothing changes.
* **"Notify crew?"**: unset changes nothing; Notify tells the people whose bookings moved, never the
  person editing; Don't notify tells nobody; ``auto`` is Notify for a change touching the next 14
  days. Any answer holds back that save's 48-hour change alert, and puts the flag back after.
* **The patch never raises and is safe twice**, publishes by the rule rather than by emptiness
  (the column's ``ALTER`` writes ``Draft`` into every row), and never un-publishes a project.

Bench-free. Reuses ``test_planner_phase3b``'s ``frappe`` stub (its ``setUpModule`` installs it and
its ``tearDownModule`` puts ``sys.modules`` back), with the P3 columns switched on.

Run: python -m unittest erpnext_enhancements.tests.test_planner_publish_baseline
"""

import ast
import copy
import datetime
import importlib
import json
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
MAINTENANCE_PATH = APP / "api/maintenance_planner.py"
PATCH_PATH = APP / "patches/mark_project_schedule_states.py"
FIXTURE = APP / "fixtures/custom_field.json"
PROPERTY_SETTERS = APP / "fixtures/property_setter.json"
CI = REPO_ROOT / ".github/workflows/ci.yml"

D = datetime.date
BASE = h.BASE
SHARED_MARKER = "INNER JOIN `tabTask` t ON t.name = d.task"
SHARED_NEW_MARKER = "INNER JOIN `tabProject` p ON p.name = d.project"


def setUpModule():
	h.setUpModule()


def tearDownModule():
	h.tearDownModule()


def _functions(path):
	return {
		node.name: node
		for node in ast.parse(path.read_text(encoding="utf-8")).body
		if isinstance(node, ast.FunctionDef)
	}


def _whitelisted(path):
	out = {}
	for name, node in _functions(path).items():
		for deco in node.decorator_list:
			if isinstance(deco, ast.Call) and getattr(deco.func, "attr", None) == "whitelist":
				methods = next((k.value for k in deco.keywords if k.arg == "methods"), None)
				out[name] = ast.literal_eval(methods) if methods is not None else None
	return out


class _P3(h._Base):
	"""The phase 3B harness with the P3 columns present, a Task table that reads what was saved and
	set, and a Project row whose schedule state the endpoints write."""

	def setUp(self):
		super().setUp()
		frappe = h.frappe
		self.frappe, self.api = frappe, h.api
		frappe.db.has_column = lambda doctype, column: True
		self.set_values = {}
		self.projects = {
			"PRJ-1": {"custom_schedule_status": "Draft"},
			"PRJ-2": {"custom_schedule_status": "Draft"},
		}

		def set_value(doctype, name, field, value=None, update_modified=True, **kwargs):
			values = field if isinstance(field, dict) else {field: value}
			frappe.db_calls.append(("set_value", doctype, name, dict(values), update_modified))
			store = self.projects.setdefault(name, {}) if doctype == "Project" else None
			if store is None:
				store = self.set_values.setdefault((doctype, name), {})
			store.update(values)

		stub_get_value = frappe.db.get_value

		def get_value(doctype, name, field=None, *args, as_dict=False, **kwargs):
			if doctype == "Project" and isinstance(field, list | tuple):
				row = self.projects.get(name)
				return h._Doc({key: (row or {}).get(key) for key in field}) if row is not None else None
			return stub_get_value(doctype, name, field, *args, **kwargs)

		frappe.db.set_value = set_value
		frappe.db.get_value = get_value
		frappe.tables["Task"] = self._task_rows

		self.notified = []
		self.batch = {"before": {}, "after": {}}

		def compute(start, end, resources=None, exclude=(), extra=(), **_kwargs):
			return {
				"resources": [{"name": p["name"], "label": p["resource_name"]} for p in h.PEOPLE],
				"days": self.batch["after" if exclude else "before"],
			}

		self.patches += [
			mock.patch.object(h.engine, "_compute", compute),
			mock.patch.object(
				h.notices,
				"send_publish_notices",
				lambda out, publisher, *a: self.notified.append((out, publisher)) or len(out),
			),
			mock.patch.object(self.api, "_project_comment", lambda project, text: self.comments.append(text)),
		]
		self.comments = []
		for patch in self.patches[2:]:
			patch.start()

	def _task_rows(self, filters):
		"""Task rows as the database would hold them: each task's latest save, then any set_value."""
		latest = {}
		for (doctype, name), doc in self.frappe.docs.items():
			if doctype == "Task":
				latest[name] = dict(doc)
		for snap in self.frappe.saved:
			if snap.get("name") in latest:
				latest[snap["name"]].update(snap)
		for (doctype, name), values in self.set_values.items():
			if doctype == "Task" and name in latest:
				latest[name].update(values)
		return [row for row in latest.values() if h._matches(row, filters)]

	def _baseline_writes(self):
		return [call for call in self.frappe.db_calls if call[:2] == ("set_value", "Task")]


# ====================================================================== pure helpers


class TestNotifyDefault(unittest.TestCase):
	TODAY = D(2026, 10, 8)

	def test_a_change_inside_the_next_14_days_defaults_to_notify(self):
		api = h.api
		self.assertTrue(api.notify_by_default([(D(2026, 10, 8), D(2026, 10, 8))], self.TODAY))  # today
		self.assertTrue(api.notify_by_default([(D(2026, 10, 21), D(2026, 10, 23))], self.TODAY))  # day 14
		self.assertFalse(api.notify_by_default([(D(2026, 10, 22), D(2026, 10, 23))], self.TODAY))  # day 15
		self.assertFalse(api.notify_by_default([(D(2026, 10, 1), D(2026, 10, 7))], self.TODAY))  # past
		self.assertTrue(api.notify_by_default([(D(2026, 10, 1), D(2026, 10, 9))], self.TODAY))  # runs into it
		self.assertFalse(api.notify_by_default([None, None], self.TODAY))

	def test_a_task_moved_out_of_the_window_still_notifies_its_crew(self):
		# Next week to March: the crew had it in their next two weeks, so the old days count.
		spans = [(D(2026, 10, 13), D(2026, 10, 14)), (D(2027, 3, 2), D(2027, 3, 3))]
		self.assertTrue(h.api.notify_by_default(spans, self.TODAY))
		self.assertFalse(h.api.notify_by_default(list(reversed(spans))[:1], self.TODAY))

	def test_the_answer_reads_unset_auto_and_checks(self):
		api = h.api
		near, far = [(D(2026, 10, 9), D(2026, 10, 9))], [(D(2026, 12, 1), D(2026, 12, 1))]
		for unset in (None, "", "null", "undefined"):
			self.assertIsNone(api.notify_choice(unset, near, self.TODAY), unset)
		self.assertTrue(api.notify_choice("auto", near, self.TODAY))
		self.assertFalse(api.notify_choice(" Auto ", far, self.TODAY))
		for yes in (1, "1", "true", True):
			self.assertTrue(api.notify_choice(yes, far, self.TODAY), yes)
		for no in (0, "0", "false", False):
			self.assertFalse(api.notify_choice(no, near, self.TODAY), no)

	def test_the_window_is_fourteen_days_today_included(self):
		self.assertEqual(h.api.NOTIFY_WINDOW_DAYS, 14)
		self.assertEqual(h.api.notify_window("2026-10-08"), (D(2026, 10, 8), D(2026, 10, 21)))


class TestBaselineRule(unittest.TestCase):
	def _row(self, name, start=None, end=None, status="Open", base=(None, None), **values):
		return dict(
			name=name,
			status=status,
			exp_start_date=start,
			exp_end_date=end,
			custom_start_datetime=None,
			custom_end_datetime=None,
			custom_baseline_start=base[0],
			custom_baseline_end=base[1],
			**values,
		)

	def test_a_publish_copies_each_dated_task_once(self):
		rows = [
			self._row("T-NEW", "2026-10-12 08:00:00", "2026-10-14 17:00:00"),
			self._row("T-HAS", "2026-10-19", "2026-10-20", base=("2026-10-12", "2026-10-13")),
			self._row("T-UNDATED"),
			self._row("T-GONE", "2026-10-12", "2026-10-12", status="Canceled"),
			self._row("T-GONE2", "2026-10-12", "2026-10-12", status="Cancelled"),
			self._row("T-TPL", "2026-10-12", "2026-10-12", status="Template"),
			self._row("T-DONE", "2026-10-01", "2026-10-02", status="Completed"),
		]
		self.assertEqual(
			h.api.baseline_changes(rows),
			[
				("T-NEW", {"custom_baseline_start": D(2026, 10, 12), "custom_baseline_end": D(2026, 10, 14)}),
				("T-DONE", {"custom_baseline_start": D(2026, 10, 1), "custom_baseline_end": D(2026, 10, 2)}),
			],
		)

	def test_a_same_day_slot_is_the_day_the_planner_draws(self):
		row = self._row("T-SLOT", "2026-10-12", "2026-10-15")
		row.update(custom_start_datetime="2026-10-13 08:00:00", custom_end_datetime="2026-10-13 12:00:00")
		((_name, values),) = h.api.baseline_changes([row])
		self.assertEqual(
			values, {"custom_baseline_start": D(2026, 10, 13), "custom_baseline_end": D(2026, 10, 13)}
		)

	def test_a_reset_rewrites_what_moved_and_clears_what_lost_its_dates(self):
		rows = [
			self._row("T-SAME", "2026-10-12", "2026-10-14", base=("2026-10-12", "2026-10-14")),
			self._row("T-MOVED", "2026-10-19", "2026-10-20", base=("2026-10-12", "2026-10-13")),
			self._row("T-UNDATED", base=("2026-10-12", "2026-10-13")),
			self._row("T-NEVER"),
			self._row(
				"T-GONE", "2026-10-19", "2026-10-19", status="Canceled", base=("2026-10-12", "2026-10-12")
			),
		]
		self.assertEqual(
			h.api.baseline_changes(rows, overwrite=True),
			[
				(
					"T-MOVED",
					{"custom_baseline_start": D(2026, 10, 19), "custom_baseline_end": D(2026, 10, 20)},
				),
				("T-UNDATED", {"custom_baseline_start": None, "custom_baseline_end": None}),
			],
		)

	def test_a_blank_state_reads_draft(self):
		api = h.api
		self.assertEqual(api.schedule_status(None), "Draft")
		self.assertEqual(api.schedule_status(""), "Draft")
		self.assertEqual(api.schedule_status("Published"), "Published")
		self.assertEqual(
			api.project_schedule(
				{"custom_schedule_status": "Published", "custom_baseline_set_on": "2026-10-21 09:00:00"}
			),
			{"status": "Published", "published_on": None, "baseline_set_on": "2026-10-21 09:00:00"},
		)


# ====================================================================== publish and baseline


class TestPublishProjectSchedule(_P3):
	def setUp(self):
		super().setUp()
		h._task("TASK-1", crew=("RES-1",))
		h._task("TASK-2", start="2026-10-13 08:00:00", end="2026-10-13 17:00:00", crew=("RES-2",))
		h._task("TASK-3", start=None, end=None, crew=())  # undated: no baseline to copy
		h._task("TASK-9", project="PRJ-2", crew=("RES-1",))  # another project
		h._draft_row("TASK-1", {"start": "2026-10-19", "end": "2026-10-21"})
		h._draft_row("TASK-9", {"start": "2026-10-20", "end": "2026-10-20"})

	def test_publishing_publishes_that_project_then_sets_its_baseline_and_state(self):
		result = self.api.publish_project_schedule("PRJ-1")

		self.assertEqual([p["task"] for p in result["published"]], ["TASK-1"])
		self.assertEqual([s["name"] for s in self.frappe.saved], ["TASK-1"])  # TASK-9 is PRJ-2's
		self.assertEqual(self._drafts("Draft")[0]["task"], "TASK-9")
		writes = {call[2]: call[3] for call in self._baseline_writes()}
		# The baseline is the published days, not the days before the publish.
		self.assertEqual(
			writes,
			{
				"TASK-1": {"custom_baseline_start": D(2026, 10, 19), "custom_baseline_end": D(2026, 10, 21)},
				"TASK-2": {"custom_baseline_start": D(2026, 10, 13), "custom_baseline_end": D(2026, 10, 13)},
			},
		)
		self.assertTrue(all(call[4] is False for call in self._baseline_writes()))  # update_modified
		project = self.projects["PRJ-1"]
		self.assertEqual(project["custom_schedule_status"], "Published")
		self.assertEqual(project["custom_schedule_published_on"], h.NOW)
		self.assertEqual(project["custom_baseline_set_on"], h.NOW)
		self.assertEqual(self.projects["PRJ-2"]["custom_schedule_status"], "Draft")
		self.assertEqual(result["schedule"]["status"], "Published")
		self.assertEqual(result["baselined"], 2)
		self.assertIn("2 given a baseline", self.comments[0])
		((out, publisher),) = self.notified  # the publish notice, once
		self.assertEqual((set(out), publisher), ({"austin@example.com"}, "nik@example.com"))

	def test_a_baseline_is_copied_once_and_republishing_keeps_it(self):
		self.api.publish_project_schedule("PRJ-1")
		stamped = self.projects["PRJ-1"]["custom_baseline_set_on"]
		h._task("TASK-4", start="2026-10-26", end="2026-10-27", crew=("RES-1",))  # added after
		self.frappe.db_calls.clear()
		self.projects["PRJ-1"]["custom_baseline_set_on"] = "2026-10-08 07:00:00"

		result = self.api.publish_project_schedule("PRJ-1")
		self.assertEqual([call[2] for call in self._baseline_writes()], ["TASK-4"])
		self.assertEqual(result["baselined"], 1)
		self.assertEqual(self.projects["PRJ-1"]["custom_baseline_set_on"], "2026-10-08 07:00:00")
		self.assertIsNotNone(stamped)

	def test_a_publish_that_needs_a_reason_changes_nothing(self):
		self.batch["after"] = {"RES-1": {"2026-10-19": {"conflicts": ["Over by 4h"]}}}
		with mock.patch.object(
			self.api, "batch_conflicts", lambda *a: {"Austin Healey": ["Mon: Over by 4h"]}
		):
			result = self.api.publish_project_schedule("PRJ-1")
		self.assertTrue(result["needs_reason"])
		self.assertNotIn("schedule", result)
		self.assertEqual(self.frappe.saved, [])
		self.assertEqual(self._baseline_writes(), [])
		self.assertEqual(self.projects["PRJ-1"]["custom_schedule_status"], "Draft")

	def test_a_draft_left_behind_keeps_the_project_draft(self):
		h._draft_row("TASK-2", {"start": "2026-10-22"}, base="2026-09-30 00:00:00")  # changed since
		result = self.api.publish_project_schedule("PRJ-1")
		self.assertEqual([p["task"] for p in result["published"]], ["TASK-1"])
		self.assertEqual([s["task"] for s in result["skipped"]], ["TASK-2"])
		self.assertEqual(result["schedule"]["status"], "Draft")
		self.assertEqual(result["baselined"], 0)
		self.assertEqual(self._baseline_writes(), [])
		self.assertEqual(self.projects["PRJ-1"]["custom_schedule_status"], "Draft")

	def test_a_project_with_no_drafts_still_publishes(self):
		self.api.discard_drafts(project="PRJ-1")
		result = self.api.publish_project_schedule("PRJ-1")
		self.assertEqual(result["published"], [])
		self.assertEqual(result["schedule"]["status"], "Published")
		self.assertEqual(result["baselined"], 2)

	def test_the_checks_run_first(self):
		self.frappe.roles = ["Sales User"]
		with self.assertRaises(h._PermissionError):
			self.api.publish_project_schedule("PRJ-1")
		self.frappe.roles = ["Projects User"]
		with mock.patch.object(self.frappe, "has_permission", lambda *a, **k: False):
			with self.assertRaises(h._PermissionError):
				self.api.reset_baseline("PRJ-1")
		with self.assertRaises(h._Throw):
			self.api.publish_project_schedule("")
		self.frappe.db.has_column = lambda doctype, column: False
		with self.assertRaises(h._Throw):
			self.api.reset_baseline("PRJ-1")
		self.assertEqual(self.frappe.saved, [])
		self.assertEqual(self._baseline_writes(), [])


class TestResetBaseline(_P3):
	def setUp(self):
		super().setUp()
		h._task("TASK-1", crew=("RES-1",))
		h._task("TASK-2", start="2026-10-19", end="2026-10-20", crew=("RES-2",))
		self.set_values[("Task", "TASK-1")] = {
			"custom_baseline_start": "2026-10-12",
			"custom_baseline_end": "2026-10-14",
		}
		self.set_values[("Task", "TASK-2")] = {
			"custom_baseline_start": "2026-10-12",
			"custom_baseline_end": "2026-10-13",
		}
		self.projects["PRJ-1"].update(
			custom_schedule_status="Published", custom_baseline_set_on="2026-10-01 09:00:00"
		)

	def test_a_dry_run_counts_and_writes_nothing(self):
		result = self.api.reset_baseline("PRJ-1", dry_run="1")
		self.assertEqual((result["changed"], result["tasks"]), (1, ["TASK-2"]))
		self.assertEqual(self.frappe.db_calls, [])
		self.assertEqual(self.comments, [])

	def test_a_reset_recopies_todays_dates_and_leaves_the_state(self):
		result = self.api.reset_baseline("PRJ-1")
		self.assertEqual(result["changed"], 1)
		self.assertEqual(
			[call[2:] for call in self._baseline_writes()],
			[
				(
					"TASK-2",
					{"custom_baseline_start": D(2026, 10, 19), "custom_baseline_end": D(2026, 10, 20)},
					False,
				)
			],
		)
		self.assertEqual(self.projects["PRJ-1"]["custom_baseline_set_on"], h.NOW)
		self.assertEqual(self.projects["PRJ-1"]["custom_schedule_status"], "Published")
		self.assertEqual(result["schedule"]["baseline_set_on"], str(h.NOW))
		self.assertEqual(self.frappe.saved, [])  # never a Task save
		self.assertIn("1 task(s) changed", self.comments[0])

	def test_a_published_project_without_a_baseline_gets_its_first(self):
		self.set_values.clear()
		self.projects["PRJ-1"]["custom_baseline_set_on"] = None
		result = self.api.reset_baseline("PRJ-1")
		self.assertEqual(sorted(result["tasks"]), ["TASK-1", "TASK-2"])


# ====================================================================== project filters, shared drafts


class TestProjectFilters(_P3):
	def setUp(self):
		super().setUp()
		h._task("TASK-1", crew=("RES-1",))
		h._task("TASK-9", project="PRJ-2", crew=("RES-1",))
		h._draft_row("TASK-1", {"start": "2026-10-19", "end": "2026-10-21"})
		h._draft_row("TASK-9", {"start": "2026-10-20", "end": "2026-10-20"})

	def test_publish_takes_one_project(self):
		result = self.api.publish_drafts(project="PRJ-2")
		self.assertEqual([p["task"] for p in result["published"]], ["TASK-9"])
		self.assertEqual([r["task"] for r in self._drafts("Draft")], ["TASK-1"])

	def test_discard_takes_one_project(self):
		self.assertEqual(self.api.discard_drafts(project="PRJ-1"), {"discarded": 1})
		self.assertEqual([r["task"] for r in self._drafts("Draft")], ["TASK-9"])

	def test_without_a_project_both_act_on_all_the_callers_rows(self):
		self.assertEqual(len(self.api.publish_drafts()["published"]), 2)
		h._draft_row("TASK-1", {"start": "2026-10-26"})
		self.assertEqual(self.api.discard_drafts(), {"discarded": 1})

	def test_a_drafted_new_task_belongs_to_its_project(self):
		row = h._draft_row("NONE", {})
		row.update(task=None, kind="New task", project="PRJ-2")
		rows, _dups = self.api._draft_rows()
		self.assertEqual(
			[r.get("task") or r.get("project") for r in self.api._rows_in_scope(rows, "PRJ-2")],
			["TASK-9", "PRJ-2"],
		)


class TestSharedDrafts(_P3):
	def setUp(self):
		super().setUp()
		h._task("TASK-1", crew=("RES-1",))
		h._task("TASK-2", start="2026-10-13 08:00:00", end="2026-10-13 17:00:00", crew=("RES-2",))
		# Lisa's draft of TASK-2, on PRJ-1, whose schedule is Draft: the SQL returns it.
		self.lisa = h._draft_row(
			"TASK-2", {"start": "2026-10-20", "end": "2026-10-20"}, owner="lisa@example.com"
		)
		self.frappe.sql_rows[SHARED_MARKER] = [dict(self.lisa, creation="2026-10-08 08:00:00")]

	def test_the_query_shares_only_other_peoples_rows_on_draft_projects(self):
		self.api._draft_rows()
		query = next(q[1] for q in self.frappe.queries if q[0] == "sql" and SHARED_MARKER in q[1])
		self.assertIn("d.owner <> %(owner)s", query)
		self.assertIn("IFNULL(p.custom_schedule_status, '') <> %(published)s", query)
		self.assertIn("d.status = %(draft)s", query)
		new_query = next(q[1] for q in self.frappe.queries if q[0] == "sql" and SHARED_NEW_MARKER in q[1])
		self.assertIn("d.kind = %(new_task)s", new_query)

	def test_a_shared_row_is_read_and_marked(self):
		rows, _dups = self.api._draft_rows()
		((row,),) = [[r for r in rows if r["task"] == "TASK-2"]]
		self.assertTrue(row["shared"])
		self.assertEqual(row["name"], self.lisa["name"])

	def test_editing_a_shared_task_builds_on_that_row_not_a_new_one(self):
		result = self.api.save_task("TASK-2", BASE, expected_time=4, draft=1)
		self.assertTrue(result["drafted"])
		drafts = self._drafts("Draft")
		self.assertEqual([r["name"] for r in drafts], [self.lisa["name"]])  # still one row
		self.assertEqual(json.loads(drafts[0]["payload"])["start"], "2026-10-20")  # built on Lisa's
		self.assertEqual(json.loads(drafts[0]["payload"])["expected_time"], 4.0)
		self.assertIn(
			("save_ignoring_permissions", "Planner Draft Change", self.lisa["name"]), self.frappe.calls
		)

	def test_the_projects_publish_includes_it_and_the_board_wide_one_does_not(self):
		self.assertEqual(self.api.publish_drafts()["published"], [])
		self.assertEqual(self.api.discard_drafts(), {"discarded": 0})
		result = self.api.publish_drafts(project="PRJ-1")
		self.assertEqual([p["task"] for p in result["published"]], ["TASK-2"])
		self.assertEqual(self._drafts("Published")[0]["name"], self.lisa["name"])

	def test_the_projects_discard_includes_it(self):
		self.assertEqual(self.api.discard_drafts(project="PRJ-1"), {"discarded": 1})
		self.assertEqual(self._drafts("Draft"), [])

	def test_the_overlay_shows_it(self):
		overlay = self.api._draft_overlay()
		self.assertIn("TASK-2", overlay["drafted"])

	def test_before_the_columns_exist_nothing_is_shared(self):
		self.frappe.db.has_column = lambda doctype, column: column != "custom_schedule_status"
		rows, _dups = self.api._draft_rows()
		self.assertEqual(rows, [])
		self.assertFalse(any(q[0] == "sql" and SHARED_MARKER in q[1] for q in self.frappe.queries))


# ====================================================================== Notify crew?


class TestNotify(_P3):
	def setUp(self):
		super().setUp()
		h._task("TASK-1", crew=("RES-1",))  # Mon Oct 12 - Wed Oct 14: inside the 14 days
		self.flags_at_save = []
		real_save = h._TaskDoc.save

		def save(doc):
			self.flags_at_save.append(getattr(self.frappe.flags, "planner_publishing", None))
			return real_save(doc)

		self.patches.append(mock.patch.object(h._TaskDoc, "save", save))
		self.patches[-1].start()

	def test_no_answer_changes_nothing(self):
		result = self.api.save_task("TASK-1", BASE, start="2026-10-19")
		self.assertNotIn("notified", result)
		self.assertEqual(self.notified, [])
		self.assertEqual(self.flags_at_save, [None])

	def test_notify_tells_the_crew_and_not_the_editor(self):
		self.frappe.flags.planner_publishing = "outer"
		result = self.api.save_task("TASK-1", BASE, start="2026-10-19", notify="1")
		self.assertEqual(result["notified"], 1)
		((out, publisher),) = self.notified
		self.assertEqual(set(out), {"austin@example.com"})
		self.assertIn("moved from", out["austin@example.com"]["lines"][0]["text"])
		self.assertEqual(publisher, "nik@example.com")
		self.assertEqual(self.flags_at_save, [True])  # no 48-hour alert on top
		self.assertEqual(self.frappe.flags.planner_publishing, "outer")  # put back

	def test_the_editor_on_the_crew_is_not_told(self):
		h._task("TASK-2", crew=("RES-3",))  # Nik's own task
		result = self.api.save_task("TASK-2", BASE, start="2026-10-19", notify=1)
		self.assertEqual(result["notified"], 0)

	def test_dont_notify_tells_nobody_and_still_holds_the_alert(self):
		result = self.api.save_task("TASK-1", BASE, start="2026-10-19", notify="0")
		self.assertEqual(result["notified"], 0)
		self.assertEqual(self.notified, [])
		self.assertEqual(self.flags_at_save, [True])
		self.assertFalse(getattr(self.frappe.flags, "planner_publishing", False))

	def test_auto_notifies_inside_14_days_only(self):
		self.assertEqual(self.api.save_task("TASK-1", BASE, start="2026-10-19", notify="auto")["notified"], 1)
		h._task("TASK-FAR", start="2026-12-01", end="2026-12-02", crew=("RES-1",))
		result = self.api.save_task("TASK-FAR", BASE, start="2026-12-08", notify="auto")
		self.assertEqual(result["notified"], 0)
		self.assertEqual(len(self.notified), 1)

	def test_a_change_that_needs_a_reason_tells_nobody(self):
		self.conflicts[D(2026, 10, 19)] = {"Austin Healey": ["Over by 4h"]}
		result = self.api.save_task("TASK-1", BASE, start="2026-10-19", notify=1)
		self.assertTrue(result["needs_reason"])
		self.assertNotIn("notified", result)
		self.assertEqual(self.notified, [])

	def test_add_and_swap_crew_take_it(self):
		self.assertEqual(self.api.add_crew("TASK-1", "RES-2", BASE, notify=1)["notified"], 1)
		((out, _publisher),) = self.notified
		self.assertEqual(set(out), {"lisa@example.com"})  # newly on it
		h._task("TASK-5", crew=("RES-1",))
		self.api.swap_crew("TASK-5", "RES-1", "RES-2", BASE, notify=1)
		self.assertEqual(set(self.notified[1][0]), {"austin@example.com", "lisa@example.com"})

	def test_a_draft_ignores_it(self):
		result = self.api.save_task("TASK-1", BASE, start="2026-10-19", draft=1, notify=1)
		self.assertTrue(result["drafted"])
		self.assertNotIn("notified", result)
		self.assertEqual(self.notified, [])


class TestShiftSuccessorsNotify(_P3):
	def setUp(self):
		super().setUp()
		h._task("TASK-1", crew=("RES-1",))
		h._task("TASK-2", start="2026-10-15", end="2026-10-16", crew=("RES-2",))
		self.frappe.tables["Task Depends On"] = [{"parent": "TASK-2", "task": "TASK-1", "parenttype": "Task"}]
		self.patches.append(mock.patch.object(h.engine, "preview_batch", lambda pairs: {}))
		self.patches[-1].start()

	def test_shifting_with_notify_tells_each_moved_crew_once(self):
		result = self.api.shift_successors("TASK-1", 2, BASE, notify="auto")
		self.assertEqual([m["task"] for m in result["moved"]], ["TASK-2"])
		self.assertEqual(result["notified"], 1)
		self.assertEqual(set(self.notified[0][0]), {"lisa@example.com"})

	def test_without_an_answer_it_is_unchanged(self):
		result = self.api.shift_successors("TASK-1", 2, BASE)
		self.assertNotIn("notified", result)


# ====================================================================== wiring


class TestWiring(unittest.TestCase):
	def test_the_new_endpoints_are_post_only(self):
		endpoints = _whitelisted(API_PATH)
		self.assertEqual(endpoints["publish_project_schedule"], ["POST"])
		self.assertEqual(endpoints["reset_baseline"], ["POST"])

	def _args(self, path, name):
		fn = _functions(path)[name]
		names = [a.arg for a in fn.args.args]
		defaults = dict(zip(names[len(names) - len(fn.args.defaults) :], fn.args.defaults, strict=True))
		return names, {key: ast.literal_eval(value) for key, value in defaults.items()}

	def test_the_signatures_p4_calls(self):
		self.assertEqual(
			self._args(API_PATH, "publish_project_schedule"), (["project", "reason"], {"reason": None})
		)
		self.assertEqual(self._args(API_PATH, "reset_baseline"), (["project", "dry_run"], {"dry_run": 0}))
		names, defaults = self._args(API_PATH, "publish_drafts")
		self.assertEqual((names, defaults), (["reason", "project"], {"reason": None, "project": None}))
		names, defaults = self._args(API_PATH, "discard_drafts")
		self.assertEqual((names, defaults), (["tasks", "project"], {"tasks": None, "project": None}))

	def test_every_live_write_takes_notify_unset_and_keeps_draft_last(self):
		for path, name in (
			(API_PATH, "save_task"),
			(API_PATH, "add_crew"),
			(API_PATH, "swap_crew"),
			(ACTIONS_PATH, "move_many"),
			(MAINTENANCE_PATH, "move_visit"),
		):
			names, defaults = self._args(path, name)
			self.assertEqual(names[-2:], ["notify", "draft"], name)
			self.assertEqual((defaults["notify"], defaults["draft"]), (None, 0), name)
		names, defaults = self._args(API_PATH, "shift_successors")
		self.assertEqual((names[-1], defaults["notify"]), ("notify", None))

	def test_move_many_and_move_visit_route_the_answer(self):
		actions = ACTIONS_PATH.read_text(encoding="utf-8")
		self.assertIn("with pp._quiet_alerts(notify):", actions)
		self.assertIn("pp._with_notice(", actions)
		maintenance = MAINTENANCE_PATH.read_text(encoding="utf-8")
		self.assertIn("return _apply_visit_notified(doc, notify, reason=reason, **edits)", maintenance)
		self.assertIn("maintenance_drafts._notify_publish([plan])", maintenance)

	def test_the_suite_has_its_own_ci_step(self):
		self.assertIn(
			"python -m unittest erpnext_enhancements.tests.test_planner_publish_baseline",
			CI.read_text(encoding="utf-8"),
		)


class TestFixtures(unittest.TestCase):
	FIELDS = {
		"Project-custom_schedule_state_section": ("Section Break", "custom_schedule"),
		"Project-custom_schedule_status": ("Select", "custom_schedule_state_section"),
		"Project-custom_schedule_published_on": ("Datetime", "custom_schedule_status"),
		"Project-custom_schedule_state_column": ("Column Break", "custom_schedule_published_on"),
		"Project-custom_baseline_set_on": ("Datetime", "custom_schedule_state_column"),
		"Task-custom_baseline_section": ("Section Break", "act_end_date"),
		"Task-custom_baseline_start": ("Date", "custom_baseline_section"),
		"Task-custom_baseline_column": ("Column Break", "custom_baseline_start"),
		"Task-custom_baseline_end": ("Date", "custom_baseline_column"),
	}

	@classmethod
	def setUpClass(cls):
		cls.records = {r["name"]: r for r in json.loads(FIXTURE.read_text(encoding="utf-8"))}

	def test_each_field_is_declared_where_the_code_reads_it(self):
		for name, (fieldtype, after) in self.FIELDS.items():
			record = self.records[name]
			self.assertEqual((record["fieldtype"], record["insert_after"]), (fieldtype, after), name)
			self.assertEqual(record["module"], "ERPNext Enhancements", name)
			self.assertEqual(record["is_system_generated"], 0, name)
			if fieldtype not in ("Section Break", "Column Break"):
				# Set by the planner only, and a copied project or task starts without them.
				self.assertEqual((record["read_only"], record["no_copy"]), (1, 1), name)

	def test_a_new_project_starts_draft(self):
		status = self.records["Project-custom_schedule_status"]
		self.assertEqual((status["options"], status["default"]), ("Draft\nPublished", "Draft"))

	def test_the_planner_schedule_section_is_hidden_on_the_project_form(self):
		# Nik, 2026-10-22: "hide the planner schedule from the form view of Projects". The whole
		# section goes (status, published on, baseline set on) -- hiding the Section Break hides
		# what it holds -- but the fields stay: the planner reads and writes them straight from the
		# database, and a hidden field still takes its default on a new Project.
		section = self.records["Project-custom_schedule_state_section"]
		self.assertEqual(section["hidden"], 1)
		self.assertEqual(section["label"], "Planner schedule")
		for name in ("Project-custom_schedule_status", "Project-custom_schedule_published_on"):
			self.assertIn(name, self.records)
		# Only that section: the Schedule tab keeps its Gantt, and the tab itself stays.
		for name in (
			"Project-custom_schedule",
			"Project-custom_gantt_chart_section",
			"Project-custom_gantt_chart_html",
		):
			self.assertEqual(self.records[name]["hidden"], 0, name)

	def test_no_form_script_shows_the_planner_schedule_section_again(self):
		root = FIXTURE.parent.parent / "public" / "js"
		for path in root.rglob("*.js"):
			text = path.read_text(encoding="utf-8", errors="ignore")
			self.assertNotIn("custom_schedule_state_section", text, path.name)

	def test_the_anchors_exist(self):
		setters = json.loads(PROPERTY_SETTERS.read_text(encoding="utf-8"))
		order = {
			s["doc_type"]: json.loads(s["value"])
			for s in setters
			if s["property"] == "field_order" and s["doc_type"] in ("Project", "Task")
		}
		self.assertIn("custom_schedule", order["Project"])
		self.assertIn("act_end_date", order["Task"])


# ====================================================================== the patch


class _FakeDB:
	"""Just enough database for the patch: Project rows with a status, and the three statements."""

	def __init__(self, statuses, scheduled, column=False):
		self.statuses = dict(statuses)
		self.scheduled = list(scheduled)
		self.column = column
		self.inserted = []
		self.statements = []
		self._cursor = types.SimpleNamespace(rowcount=0)
		self.fail = None

	def has_column(self, doctype, column):
		return self.column and doctype == "Project" and column == "custom_schedule_status"

	def exists(self, doctype, name=None):
		return name in self.inserted

	def sql(self, query, values=None, **kwargs):
		self.statements.append(" ".join(query.split()))
		if self.fail and self.fail in query:
			raise RuntimeError("database says no")
		if query.strip().startswith("SELECT"):
			return [(name,) for name in self.scheduled]
		if "IN %(names)s" in query:
			hit = [n for n in values["names"] if (self.statuses.get(n) or "") != values["published"]]
			for name in hit:
				self.statuses[name] = values["published"]
		else:
			hit = [n for n, s in self.statuses.items() if not (s or "")]
			for name in hit:
				self.statuses[name] = values["draft"]
		self._cursor.rowcount = len(hit)
		return []


class TestPatch(unittest.TestCase):
	def setUp(self):
		self.db = _FakeDB(
			{"PRJ-1": "Draft", "PRJ-2": "Draft", "PRJ-3": "Published", "PRJ-4": None}, ["PRJ-1", "PRJ-3"]
		)
		self.errors, self.printed = [], []
		fake = types.ModuleType("frappe")
		fake.db = self.db
		fake.get_app_path = lambda *parts: str(APP / Path(*parts[1:]))
		fake.log_error = lambda **kwargs: self.errors.append(kwargs)
		fake.get_traceback = lambda: "traceback"

		def get_doc(record):
			doc = types.SimpleNamespace(record=record)

			def insert(ignore_permissions=False):
				if self.db.fail == "insert":
					raise RuntimeError("cannot create")
				self.db.inserted.append(record["name"])
				if record["fieldname"] == "custom_schedule_status":
					self.db.column = True

			doc.insert = insert
			return doc

		fake.get_doc = get_doc
		self.saved = sys.modules.get("frappe")
		sys.modules["frappe"] = fake
		sys.modules.pop("erpnext_enhancements.patches.mark_project_schedule_states", None)
		self.patch = importlib.import_module("erpnext_enhancements.patches.mark_project_schedule_states")
		self.print = mock.patch("builtins.print", lambda *a, **k: self.printed.append(" ".join(map(str, a))))
		self.print.start()

	def tearDown(self):
		self.print.stop()
		sys.modules.pop("erpnext_enhancements.patches.mark_project_schedule_states", None)
		if self.saved is not None:
			sys.modules["frappe"] = self.saved

	def test_it_creates_the_project_fields_in_order_from_the_fixture(self):
		self.patch.execute()
		self.assertEqual(self.db.inserted, list(self.patch.PROJECT_FIELDS))

	def test_it_publishes_by_the_rule_and_drafts_only_blanks(self):
		self.patch.execute()
		self.assertEqual(
			self.db.statuses, {"PRJ-1": "Published", "PRJ-2": "Draft", "PRJ-3": "Published", "PRJ-4": "Draft"}
		)
		self.assertIn("2 project(s) already scheduled, 1 set Published, 1 blank set Draft.", self.printed[-1])

	def test_it_is_safe_twice_and_never_unpublishes(self):
		self.patch.execute()
		after_first = dict(self.db.statuses)
		self.db.scheduled = []  # the rule no longer matches PRJ-1: it stays Published
		self.patch.execute()
		self.assertEqual(self.db.statuses, after_first)
		self.assertEqual(self.db.inserted, list(self.patch.PROJECT_FIELDS))  # not created twice
		self.assertIn("0 set Published, 0 blank set Draft.", self.printed[-1])

	def test_it_never_raises(self):
		self.db.fail = "insert"
		self.patch.execute()
		self.assertEqual(self.errors[-1]["title"], "Planner schedule states: fields not created")
		self.db.fail, self.db.column = "UPDATE", True
		self.patch.execute()
		self.assertEqual(self.errors[-1]["title"], "Planner schedule states: not marked")
		self.db.fail = "tabToDo"
		self.patch.execute()
		self.assertEqual(len(self.errors), 3)

	def test_the_rule_is_dated_crewed_work(self):
		sql = " ".join(self.patch.SCHEDULED_SQL.split())
		for part in (
			"t.status NOT IN ('Canceled', 'Cancelled', 'Template')",
			"(t.exp_start_date IS NOT NULL OR t.custom_start_datetime IS NOT NULL)",
			"`tabTask Crew Member` c WHERE c.parenttype = 'Task' AND c.parent = t.name AND IFNULL(c.resource, '') <> ''",
			"INNER JOIN `tabPlanner Resource` r ON r.user = d.allocated_to AND r.is_active = 1",
			"d.status = 'Open'",
		):
			self.assertIn(part, sql)

	def test_it_is_registered_once(self):
		lines = (APP / "patches.txt").read_text(encoding="utf-8").splitlines()
		self.assertEqual(lines.count("erpnext_enhancements.patches.mark_project_schedule_states"), 1)
		self.assertIn(
			"`mark_project_schedule_states`", (APP / "patches/README.md").read_text(encoding="utf-8")
		)


# ====================================================================== review follow-ups


class TestReviewFollowUps(_P3):
	def test_drafts_a_publish_could_not_reach_keep_the_project_draft(self):
		h._task("TASK-1", crew=("RES-1",))
		stub = self.frappe.db.sql
		self.frappe.db.sql = lambda query, values=None, **kwargs: (
			[(3,)] if "SELECT COUNT(*)" in query else stub(query, values, **kwargs)
		)
		result = self.api.publish_project_schedule("PRJ-1")
		self.assertEqual((result["schedule"]["status"], result["drafts_left"]), ("Draft", 3))
		self.assertEqual(self._baseline_writes(), [])
		self.assertEqual(self.projects["PRJ-1"]["custom_schedule_status"], "Draft")

	def test_a_published_project_is_not_held_by_other_peoples_private_drafts(self):
		h._task("TASK-1", crew=("RES-1",))
		self.projects["PRJ-1"]["custom_schedule_status"] = "Published"
		self.frappe.db.sql = lambda query, values=None, **kwargs: [(5,)] if "COUNT(*)" in query else []
		result = self.api.publish_project_schedule("PRJ-1")
		self.assertEqual(result["baselined"], 1)

	def test_a_board_wide_publish_says_when_a_shared_draft_replaced_yours(self):
		h._task("TASK-2", start="2026-10-13", end="2026-10-13", crew=("RES-2",))
		mine = h._draft_row("TASK-2", {"start": "2026-10-19"})
		mine["modified"] = "2026-10-08 07:00:00"
		lisa = h._draft_row("TASK-2", {"start": "2026-10-20"}, owner="lisa@example.com")
		self.frappe.sql_rows[SHARED_MARKER] = [dict(lisa, creation="2026-10-08 08:00:00")]
		result = self.api.publish_drafts()
		self.assertEqual(result["published"], [])
		self.assertEqual([s["task"] for s in result["skipped"]], ["TASK-2"])
		self.assertIn("newer draft", result["skipped"][0]["reason"])

	def test_an_unknown_notify_answer_is_refused_not_read_as_no(self):
		with self.assertRaises(h._Throw):
			self.api.notify_choice("notify", [], D(2026, 10, 8))
		h._task("TASK-1", crew=("RES-1",))
		with self.assertRaises(h._Throw):
			self.api.save_task("TASK-1", BASE, start="2026-10-19", notify="yes please")

	def test_a_shift_with_nothing_to_move_still_answers_notified(self):
		h._task("TASK-1", crew=("RES-1",))
		result = self.api.shift_successors("TASK-1", 2, BASE, notify=1)
		self.assertEqual((result["moved"], result["notified"]), ([], 0))

	def test_the_alert_hold_is_put_back_after_a_failed_save(self):
		self.frappe.flags.planner_publishing = False
		with self.assertRaises(RuntimeError):
			with self.api._quiet_alerts("1"):
				self.assertTrue(self.frappe.flags.planner_publishing)
				raise RuntimeError("save failed")
		self.assertFalse(self.frappe.flags.planner_publishing)
		with self.api._quiet_alerts(None):
			self.assertFalse(self.frappe.flags.planner_publishing)


if __name__ == "__main__":
	unittest.main()
