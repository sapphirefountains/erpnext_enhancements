# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A reviewer can overrule the model, and a wrong duplicate flag never stops a Task (v1.729.0).

Written after ER-2026-1323804: a bug about the reorder level refusing 0 came back from the
breakdown with **no tasks** and one High-confidence duplicate, TASK-2026-02912 "Bin Manager
rejects 0 as a valid quantity" — a different bug. The reviewer had two buttons, "Close as
duplicate of this" and Reject; "Ask again" sent the same Task to the same model and got the
same answer. What these pin:

- **"Not a duplicate" is recorded, not deleted.** ``dismiss_duplicate`` stamps the candidate
  with who and when, leaves the status alone and tells the requester nothing.
- **A ruled-out Task cannot come back.** ``breakdown.run_breakdown`` leaves it out of the
  ``open_tasks`` it sends, says in the description that a reviewer ruled it out, and a model
  that names it anyway has the duplicate dropped; the ruled-out row itself survives the re-run.
- **"Plan it as new work"** rules out every remaining flag and re-queues the breakdown.
- **"Add a task" never needs the model.** ``add_proposed_task`` validates the row like an edited
  one (the field allowlist, the project allowlist, ERPNext's priorities), refuses a project
  outside the two configured boards, and works from ``Breakdown Failed`` too — and
  ``create_tasks`` then writes that row when the model proposed nothing at all.
- **A hand-added row survives a re-run.** The re-run replaces only the model's rows.

``frappe`` is a stub installed in ``setUpModule`` and removed in ``tearDownModule``; the
request is a fake Document whose ``save`` runs the real transition table. ``task_writer`` is
real, so "create_tasks with only a hand-added row" means a Task insert actually happened. Its
own CI step, so the stub cannot meet another suite's (CLAUDE.md).

Run: python -m unittest erpnext_enhancements.tests.test_feedback_duplicate_override -v
"""

import sys
import types
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.model",
	"frappe.model.document",
)
#: Imported against the stub, so dropped before and after: a copy bound to a real frappe (or to
#: another suite's stub) must not be reused here, nor this one there.
APP_MODULES = (
	"erpnext_enhancements.api.feedback",
	"erpnext_enhancements.product_feedback.breakdown",
	"erpnext_enhancements.product_feedback.task_writer",
	"erpnext_enhancements.product_feedback.code_anchors",
	"erpnext_enhancements.product_feedback.codemap",
	"erpnext_enhancements.product_feedback.proposal",
	"erpnext_enhancements.product_feedback.doctype.product_feedback_settings.product_feedback_settings",
)
TRITON = "erpnext_enhancements.product_feedback.triton_client"

_saved = {}
feedback = None
breakdown = None
task_writer = None
states = None
STATE = None

REVIEWER = "nik@example.com"
ERPNEXT = "PRJ-00580"
TRITON_PROJECT = "PRJ-00755"
WRONG_DUPLICATE = "TASK-2026-02912"
REQUEST = "ER-2026-1323804"


class ValidationError(Exception):
	pass


class PermissionError_(Exception):
	pass


class Row:
	"""A child row: attribute access plus Document's ``get``/``set``."""

	def __init__(self, **values):
		self.__dict__.update(values)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)

	def set(self, key, value):
		self.__dict__[key] = value

	def __getattr__(self, key):
		# A Document answers None for a field it has no value for.
		return None


TABLES = ("proposed_tasks", "duplicate_candidates")


class FakeRequest:
	"""``Enhancement Request``. ``save`` runs the real transition table, as ``validate`` does."""

	def __init__(self, status, proposed=(), candidates=()):
		self.doctype = "Enhancement Request"
		self.name = REQUEST
		self.status = status
		self.title = "The reorder at line is refusing to be assigned a 0"
		self.request_type = "Bug"
		self.impact = "Painful but I can work around it"
		self.description = "<p>Setting the reorder level to 0 on an Item is refused.</p>"
		self.steps_to_reproduce = ""
		self.context_url = ""
		self.context_doctype = "Item"
		self.context_app_version = "1.727.1"
		self.requested_by = "parker@example.com"
		self.decided_by = REVIEWER
		self.target_erpnext = 1
		self.target_triton = 0
		self.breakdown_error = ""
		self.breakdown_summary = ""
		self.saves = 0
		self._stored_status = status
		self._counter = 0
		for key in TABLES:
			setattr(self, key, [])
		for row in proposed:
			self.append("proposed_tasks", dict(row))
		for row in candidates:
			self.append("duplicate_candidates", dict(row))

	def get(self, key, default=None):
		return getattr(self, key, default)

	def set(self, key, value):
		if key in TABLES:
			setattr(self, key, [])
			for row in value or []:
				self.append(key, row)
		else:
			setattr(self, key, value)

	def append(self, key, value):
		row = value if isinstance(value, Row) else Row(**value)
		if not row.get("name"):
			self._counter += 1
			row.name = f"{key}-{self._counter}"
		table = getattr(self, key)
		table.append(row)
		row.idx = len(table)
		return row

	def save(self, ignore_permissions=False):
		states.assert_transition(self._stored_status, self.status)
		self._stored_status = self.status
		self.saves += 1
		return self


class FakeTask:
	def __init__(self, values):
		self.values = dict(values)
		self.name = None

	def insert(self, ignore_permissions=False):
		self.name = f"TASK-NEW-{len(STATE.inserted) + 1}"
		STATE.inserted.append(dict(self.values, name=self.name))
		return self


class State:
	def __init__(self):
		self.user = REVIEWER
		self.roles = ["System Manager"]
		self.doc = None
		self.inserted = []
		#: Task name -> {"subject", "project", "is_group", "status", "parent_task"}
		self.tasks = {
			WRONG_DUPLICATE: {
				"subject": "Bin Manager rejects 0 as a valid quantity",
				"project": ERPNEXT,
				"is_group": 0,
				"status": "Open",
				"parent_task": "",
			},
			"TASK-2026-02200": {
				"subject": "Feedback, Design Review and a task generator",
				"project": ERPNEXT,
				"is_group": 1,
				"status": "Open",
				"parent_task": "",
			},
			"TASK-2026-01000": {
				"subject": "Inventory epic",
				"project": ERPNEXT,
				"is_group": 0,
				"status": "Open",
				"parent_task": "",
			},
		}
		self.triton_payloads = []
		self.triton_body = {}
		self.enqueued = []
		self.notified = []
		self.set_values = []


def _install():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda s, *a, **k: s
	frappe.whitelist = lambda *a, **k: (lambda f: f)
	frappe.ValidationError = ValidationError
	frappe.PermissionError = PermissionError_

	def throw(message, exc=ValidationError, *a, **k):
		raise exc(message)

	frappe.throw = throw

	class Session:
		@property
		def user(self):
			return STATE.user

	frappe.session = Session()
	frappe.get_roles = lambda *a: list(STATE.roles)
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda: ""
	frappe.has_permission = lambda *a, **k: True
	frappe.enqueue = lambda *a, **k: STATE.enqueued.append(k)

	def get_doc(doctype, name=None):
		if isinstance(doctype, dict):
			assert doctype.get("doctype") == "Task", doctype
			return FakeTask(doctype)
		assert doctype == "Enhancement Request" and name == REQUEST, (doctype, name)
		return STATE.doc

	frappe.get_doc = get_doc

	def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
		filters = filters or {}
		if doctype == "Enhancement Request Duplicate Candidate":
			assert filters.get("parent") == REQUEST and filters.get("dismissed") == 1
			return [
				{"task": row.task, "task_subject": row.task_subject}
				for row in STATE.doc.duplicate_candidates
				if row.get("dismissed")
			]
		if doctype == "Task":
			projects = (filters.get("project") or ["in", []])[1]
			closed = (filters.get("status") or ["not in", ()])[1]
			return [
				dict(meta, name=name)
				for name, meta in STATE.tasks.items()
				if meta["project"] in projects and meta["status"] not in closed
			]
		return []

	frappe.get_all = get_all

	def get_value(doctype, name=None, fields=None, as_dict=False, **kwargs):
		if doctype == "Enhancement Request":
			doc = STATE.doc
			return {field: getattr(doc, field, None) for field in fields}
		if doctype == "Task":
			meta = STATE.tasks.get(name)
			if meta is None:
				return None
			return {field: meta.get(field) for field in fields}
		return None

	def set_value(doctype, name, field, value=None, **kwargs):
		values = field if isinstance(field, dict) else {field: value}
		STATE.set_values.append((doctype, name, values))
		if doctype == "Enhancement Request":
			for key, val in values.items():
				setattr(STATE.doc, key, val)
				if key == "status":
					STATE.doc._stored_status = val

	frappe.db = types.SimpleNamespace(
		get_value=get_value,
		set_value=set_value,
		has_column=lambda doctype, column: True,
		commit=lambda: None,
		exists=lambda *a, **k: True,
		get_single_value=lambda *a, **k: 0,
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(float(v or 0))
	utils.flt = lambda v: float(v or 0)
	utils.now_datetime = lambda: datetime(2026, 10, 22, 9, 30, 0)
	utils.today = lambda: "2026-10-22"
	utils.add_days = lambda d, n: d
	utils.add_to_date = lambda d, **k: d
	utils.escape_html = lambda s: s
	utils.strip_html = lambda s: s
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = type("Document", (), {})
	model.document = document
	frappe.model = model

	for name in STUBBED:
		_saved[name] = sys.modules.get(name)
	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.model": model,
			"frappe.model.document": document,
		}
	)


class FakeTriton(types.ModuleType):
	class TritonUnavailable(Exception):
		pass

	def request_breakdown(self, reviewer, payload, timeout):
		STATE.triton_payloads.append(payload)
		return STATE.triton_body


def setUpModule():
	global feedback, breakdown, task_writer, states
	_install()
	for name in APP_MODULES:
		sys.modules.pop(name, None)

	import erpnext_enhancements.product_feedback as package
	from erpnext_enhancements.api import feedback as api
	from erpnext_enhancements.product_feedback import breakdown as bd
	from erpnext_enhancements.product_feedback import states as st
	from erpnext_enhancements.product_feedback import task_writer as writer

	_saved[TRITON] = sys.modules.get(TRITON)
	_saved["package.triton_client"] = getattr(package, "triton_client", None)
	fake = FakeTriton(TRITON)
	sys.modules[TRITON] = fake
	package.triton_client = fake

	projects = lambda: (ERPNEXT, TRITON_PROJECT)  # noqa: E731
	api.allowed_projects = projects
	writer.allowed_projects = projects
	api._serialise = lambda doc, full=False: {"name": doc.name, "status": doc.status}
	api._notify = lambda event, *args: STATE.notified.append(event)
	api._enqueue_breakdown = lambda name: STATE.enqueued.append(name)

	bd.get_settings = lambda: {
		"erpnext_project": ERPNEXT,
		"triton_project": TRITON_PROJECT,
		"duplicate_scan_limit": 300,
		"max_proposed_tasks": 12,
		"breakdown_timeout": 60,
	}
	bd.build_codemap = lambda: {}
	bd.build_anchors = lambda *a, **k: {}
	bd._record_usage = lambda *a, **k: None
	bd._notify_reviewer = lambda *a, **k: None

	feedback, breakdown, task_writer, states = api, bd, writer, st


def tearDownModule():
	import erpnext_enhancements.product_feedback as package

	for name in APP_MODULES:
		sys.modules.pop(name, None)
	if _saved.get(TRITON) is None:
		sys.modules.pop(TRITON, None)
	else:
		sys.modules[TRITON] = _saved[TRITON]
	if _saved.get("package.triton_client") is None:
		if hasattr(package, "triton_client"):
			delattr(package, "triton_client")
	else:
		package.triton_client = _saved["package.triton_client"]
	for name in STUBBED:
		if _saved.get(name) is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = _saved[name]


def flagged(**overrides):
	"""ER-2026-1323804's one duplicate candidate, as the breakdown wrote it."""
	row = {
		"task": WRONG_DUPLICATE,
		"task_subject": "Bin Manager rejects 0 as a valid quantity",
		"confidence": "High",
		"why": "Both are about a 0 being refused.",
	}
	row.update(overrides)
	return row


def hand_row(**overrides):
	row = {
		"subject": "Reorder level accepts 0 on the Item form",
		"project": ERPNEXT,
		"priority": "High",
		"expected_hours": 2,
	}
	row.update(overrides)
	return row


class Base(unittest.TestCase):
	def setUp(self):
		global STATE
		STATE = State()

	def request(self, status="Breakdown Ready", proposed=(), candidates=(flagged(),)):
		STATE.doc = FakeRequest(status, proposed=proposed, candidates=candidates)
		return STATE.doc


# ------------------------------------------------------------------------- not a duplicate


class TestDismissDuplicate(Base):
	def test_it_is_stamped_with_who_and_when_and_kept(self):
		doc = self.request()
		feedback.dismiss_duplicate(REQUEST, WRONG_DUPLICATE)
		(row,) = doc.duplicate_candidates
		self.assertEqual(row.dismissed, 1)
		self.assertEqual(row.dismissed_by, REVIEWER)
		self.assertEqual(row.dismissed_on, datetime(2026, 10, 22, 9, 30, 0))
		self.assertEqual(doc.status, "Breakdown Ready")
		self.assertEqual(doc.saves, 1)

	def test_the_requester_is_not_told(self):
		self.request()
		feedback.dismiss_duplicate(REQUEST, WRONG_DUPLICATE)
		self.assertEqual(STATE.notified, [])

	def test_a_task_that_is_not_a_candidate_is_refused(self):
		doc = self.request()
		with self.assertRaises(ValidationError):
			feedback.dismiss_duplicate(REQUEST, "TASK-2026-00001")
		self.assertEqual(doc.saves, 0)

	def test_a_closed_request_is_refused(self):
		doc = self.request(status="Duplicate")
		with self.assertRaises(ValidationError):
			feedback.dismiss_duplicate(REQUEST, WRONG_DUPLICATE)
		self.assertEqual(doc.saves, 0)

	def test_reviewers_only(self):
		self.request()
		STATE.roles = ["Employee"]
		with self.assertRaises(PermissionError_):
			feedback.dismiss_duplicate(REQUEST, WRONG_DUPLICATE)

	def test_a_second_ruling_keeps_the_first_stamp(self):
		doc = self.request(candidates=(flagged(dismissed=1, dismissed_by="lisa@example.com"),))
		feedback.dismiss_duplicate(REQUEST, WRONG_DUPLICATE)
		self.assertEqual(doc.duplicate_candidates[0].dismissed_by, "lisa@example.com")


class TestPlanAsNewWork(Base):
	def test_every_flag_is_ruled_out_and_the_breakdown_requeued(self):
		doc = self.request(candidates=(flagged(), flagged(task="TASK-2026-01000", task_subject="Inventory epic")))
		feedback.plan_as_new_work(REQUEST)
		self.assertTrue(all(row.dismissed == 1 for row in doc.duplicate_candidates))
		self.assertEqual(doc.status, "Approved")
		self.assertEqual(STATE.enqueued, [REQUEST])

	def test_it_is_refused_before_a_breakdown_exists(self):
		doc = self.request(status="Approved")
		with self.assertRaises(ValidationError):
			feedback.plan_as_new_work(REQUEST)
		self.assertEqual(doc.saves, 0)
		self.assertEqual(STATE.enqueued, [])


# ------------------------------------------------------------------------------ the re-run


class TestTheRerunLeavesARuledOutTaskOut(Base):
	def run_it(self, body, candidates=(flagged(dismissed=1, dismissed_by=REVIEWER),), proposed=()):
		doc = self.request(status="Approved", candidates=candidates, proposed=proposed)
		STATE.triton_body = body
		breakdown.run_breakdown(REQUEST)
		return doc

	def test_it_is_not_in_the_tasks_sent(self):
		self.run_it({"summary": "", "tasks": [{"subject": "Fix it", "target": "erpnext"}]})
		(payload,) = STATE.triton_payloads
		sent = {task["name"] for task in payload["open_tasks"]}
		self.assertNotIn(WRONG_DUPLICATE, sent)
		# Control: the scan itself still runs, so the absence is the dismissal and not an
		# empty scan.
		self.assertIn("TASK-2026-01000", sent)

	def test_without_a_ruling_it_is_sent(self):
		"""Control for the test above: an undismissed candidate's Task is still scanned."""
		self.run_it({"summary": "", "tasks": [{"subject": "Fix it", "target": "erpnext"}]}, candidates=(flagged(),))
		(payload,) = STATE.triton_payloads
		self.assertIn(WRONG_DUPLICATE, {task["name"] for task in payload["open_tasks"]})
		self.assertNotIn("Reviewer note", payload["request"]["description"])

	def test_the_description_carries_the_reviewer_note(self):
		self.run_it({"summary": "", "tasks": [{"subject": "Fix it", "target": "erpnext"}]})
		description = STATE.triton_payloads[0]["request"]["description"]
		self.assertTrue(description.startswith("<p>Setting the reorder level to 0"))
		self.assertIn("Reviewer note (added by ERPNext, not the requester)", description)
		self.assertIn(f"{WRONG_DUPLICATE} — Bin Manager rejects 0 as a valid quantity", description)

	def test_a_model_that_names_it_anyway_has_the_duplicate_dropped(self):
		doc = self.run_it(
			{
				"summary": "Covered.",
				"tasks": [{"subject": "Fix the reorder level", "target": "erpnext"}],
				"duplicates": [{"task": WRONG_DUPLICATE, "confidence": "High", "why": "same"}],
			}
		)
		self.assertEqual(doc.status, "Breakdown Ready")
		# One row for the Task: the reviewer's ruling, still stamped. Not a fresh flag beside it.
		rows = [row for row in doc.duplicate_candidates if row.task == WRONG_DUPLICATE]
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].dismissed, 1)
		self.assertEqual(rows[0].dismissed_by, REVIEWER)
		self.assertIn("ruled not a duplicate by a reviewer", doc.breakdown_error)
		self.assertEqual([row.subject for row in doc.proposed_tasks], ["Fix the reorder level"])

	def test_other_candidates_are_still_replaced(self):
		doc = self.run_it(
			{
				"summary": "",
				"tasks": [{"subject": "Fix it", "target": "erpnext"}],
				"duplicates": [{"task": "TASK-2026-01000", "confidence": "Low", "why": "maybe"}],
			},
			candidates=(flagged(dismissed=1), flagged(task="TASK-OLD", task_subject="Old flag")),
		)
		self.assertEqual(
			[(row.task, row.get("dismissed")) for row in doc.duplicate_candidates],
			[(WRONG_DUPLICATE, 1), ("TASK-2026-01000", None)],
		)

	def test_a_hand_added_row_survives_and_loses_its_stale_dependency(self):
		doc = self.run_it(
			{"summary": "", "tasks": [{"subject": "Model row", "target": "erpnext"}]},
			proposed=(
				{"include": 1, "subject": "Old model row", "project": ERPNEXT},
				dict(hand_row(), include=1, added_by=REVIEWER, depends_on_idx=1),
			),
		)
		self.assertEqual(
			[row.subject for row in doc.proposed_tasks],
			["Model row", "Reorder level accepts 0 on the Item form"],
		)
		self.assertEqual(doc.proposed_tasks[1].added_by, REVIEWER)
		self.assertEqual(doc.proposed_tasks[1].depends_on_idx, 0)

	def test_nothing_but_the_ruled_out_duplicate_fails_and_leaves_the_way_open(self):
		"""The model still says "no work". The request fails legibly, keeps the ruling, and a
		task added by hand takes it to Breakdown Ready — the model is not the last word."""
		doc = self.run_it(
			{
				"summary": "No new development tasks are needed.",
				"tasks": [],
				"duplicates": [{"task": WRONG_DUPLICATE, "confidence": "High", "why": "same"}],
			}
		)
		self.assertEqual(doc.status, "Breakdown Failed")
		self.assertEqual(doc.duplicate_candidates[0].dismissed, 1)

		feedback.add_proposed_task(REQUEST, hand_row())
		self.assertEqual(doc.status, "Breakdown Ready")


# ---------------------------------------------------------------------------- add a task


class TestAddProposedTask(Base):
	def test_the_row_is_added_ticked_and_marked_as_the_reviewers(self):
		doc = self.request()
		result = feedback.add_proposed_task(REQUEST, hand_row())
		(row,) = doc.proposed_tasks
		self.assertEqual(row.subject, "Reorder level accepts 0 on the Item form")
		self.assertEqual(row.project, ERPNEXT)
		self.assertEqual(row.priority, "High")
		self.assertEqual(row.expected_hours, 2.0)
		self.assertEqual(row.include, 1)
		self.assertEqual(row.added_by, REVIEWER)
		self.assertEqual(row.parent_task, "")
		self.assertEqual(result["rejected"], [])
		self.assertEqual(doc.saves, 1)

	def test_a_json_string_is_accepted(self):
		import json

		doc = self.request()
		feedback.add_proposed_task(REQUEST, json.dumps(hand_row()))
		self.assertEqual(len(doc.proposed_tasks), 1)

	def test_a_project_outside_the_two_boards_is_refused(self):
		doc = self.request()
		with self.assertRaises(ValidationError) as caught:
			feedback.add_proposed_task(REQUEST, hand_row(project="PRJ-00123"))
		self.assertIn("PRJ-00123", str(caught.exception))
		self.assertEqual(doc.proposed_tasks, [])
		self.assertEqual(doc.saves, 0)

	def test_no_project_is_refused(self):
		doc = self.request()
		row = hand_row()
		row.pop("project")
		with self.assertRaises(ValidationError):
			feedback.add_proposed_task(REQUEST, row)
		self.assertEqual(doc.proposed_tasks, [])

	def test_the_triton_board_is_allowed(self):
		doc = self.request()
		feedback.add_proposed_task(REQUEST, hand_row(project=TRITON_PROJECT))
		self.assertEqual(doc.proposed_tasks[0].project, TRITON_PROJECT)

	def test_no_subject_is_refused(self):
		doc = self.request()
		with self.assertRaises(ValidationError):
			feedback.add_proposed_task(REQUEST, hand_row(subject="   "))
		self.assertEqual(doc.proposed_tasks, [])

	def test_the_subject_is_cut_to_what_a_task_holds(self):
		doc = self.request()
		feedback.add_proposed_task(REQUEST, hand_row(subject="x" * 300))
		self.assertEqual(len(doc.proposed_tasks[0].subject), 140)

	def test_fields_outside_the_allowlist_are_refused_and_reported(self):
		doc = self.request()
		result = feedback.add_proposed_task(
			REQUEST, hand_row(status="Completed", created_task="TASK-FAKE", added_by="someone@else.com")
		)
		self.assertEqual(result["rejected"], ["status"])
		row = doc.proposed_tasks[0]
		# `created_task` is the idempotency key and `added_by` is the session's, whatever the
		# client echoes.
		self.assertIsNone(row.created_task)
		self.assertEqual(row.added_by, REVIEWER)

	def test_an_unknown_priority_is_reported_and_defaults(self):
		doc = self.request()
		result = feedback.add_proposed_task(REQUEST, hand_row(priority="Critical"))
		self.assertEqual(result["rejected"], ["priority"])
		self.assertEqual(doc.proposed_tasks[0].priority, "Medium")

	def test_a_parent_must_be_a_group_on_the_same_project(self):
		doc = self.request()
		for parent in ("TASK-2026-01000", "TASK-NOWHERE"):
			with self.subTest(parent=parent), self.assertRaises(ValidationError):
				feedback.add_proposed_task(REQUEST, hand_row(parent_task=parent))
		with self.assertRaises(ValidationError):
			feedback.add_proposed_task(REQUEST, hand_row(project=TRITON_PROJECT, parent_task="TASK-2026-02200"))
		self.assertEqual(doc.proposed_tasks, [])

		feedback.add_proposed_task(REQUEST, hand_row(parent_task="TASK-2026-02200"))
		self.assertEqual(doc.proposed_tasks[0].parent_task, "TASK-2026-02200")

	def test_a_dependency_must_point_at_an_existing_row(self):
		doc = self.request(proposed=({"include": 1, "subject": "Model row", "project": ERPNEXT},))
		feedback.add_proposed_task(REQUEST, hand_row(depends_on_idx=1))
		feedback.add_proposed_task(REQUEST, hand_row(depends_on_idx=7))
		self.assertEqual([row.depends_on_idx for row in doc.proposed_tasks[1:]], [1, 0])

	def test_a_failed_breakdown_becomes_ready(self):
		doc = self.request(status="Breakdown Failed")
		feedback.add_proposed_task(REQUEST, hand_row())
		self.assertEqual(doc.status, "Breakdown Ready")

	def test_it_is_refused_before_a_breakdown_and_after_close(self):
		for status in ("Submitted", "Approved", "Tasks Created", "Rejected", "Duplicate"):
			with self.subTest(status=status):
				doc = self.request(status=status)
				with self.assertRaises(ValidationError):
					feedback.add_proposed_task(REQUEST, hand_row())
				self.assertEqual(doc.proposed_tasks, [])

	def test_reviewers_only(self):
		self.request()
		STATE.roles = ["Employee"]
		with self.assertRaises(PermissionError_):
			feedback.add_proposed_task(REQUEST, hand_row())


class TestCreateTasksFromAHandAddedRow(Base):
	def test_a_proposal_the_model_left_empty_still_reaches_the_board(self):
		"""ER-2026-1323804's shape: no proposed tasks, one wrong duplicate. Rule it out, add the
		task by hand, confirm — and the Task exists."""
		doc = self.request(proposed=())
		feedback.dismiss_duplicate(REQUEST, WRONG_DUPLICATE)
		feedback.add_proposed_task(REQUEST, hand_row())
		result = feedback.create_tasks(REQUEST)

		(task,) = STATE.inserted
		self.assertEqual(task["subject"], "Reorder level accepts 0 on the Item form")
		self.assertEqual(task["project"], ERPNEXT)
		self.assertEqual(task["priority"], "High")
		self.assertEqual(task["expected_time"], 2.0)
		self.assertEqual(task["custom_enhancement_request"], REQUEST)
		self.assertEqual(result["created"], [task["name"]])
		self.assertTrue(result["complete"])
		self.assertEqual(doc.status, "Tasks Created")
		self.assertEqual(doc.proposed_tasks[0].created_task, task["name"])
		self.assertEqual(STATE.notified, ["tasks_created"])

	def test_with_nothing_added_the_confirm_is_still_refused(self):
		"""Control: the escape hatch is the added row, not a loosened refusal."""
		self.request(proposed=())
		with self.assertRaises(ValidationError) as caught:
			feedback.create_tasks(REQUEST)
		self.assertIn("add one by hand", str(caught.exception))
		self.assertEqual(STATE.inserted, [])


class TestStates(unittest.TestCase):
	def test_failed_to_ready_is_legal_and_the_human_gate_is_not(self):
		self.assertTrue(states.is_legal("Breakdown Failed", "Breakdown Ready"))
		self.assertFalse(states.is_legal("Submitted", "Breakdown Ready"))
		self.assertFalse(states.is_legal("Breakdown Failed", "Tasks Created"))


if __name__ == "__main__":
	unittest.main()
