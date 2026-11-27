"""Bench-free tests for one-tap complete and Undo (``api/task_completion.py``).

Triton's Projects Dashboard completes a Task from a card, as the person who pressed the check.
What each part guards, in the terms it fails in:

* **Nothing leaks before the permission check.** A missing Task and one the caller may not change
  get the same sentence with no subject, so the endpoint cannot be walked to read subjects. Its
  status ("already completed", "Canceled") is told only to someone entitled. A blocking dependency
  is named only when the caller may read it.
* **Who is entitled.** Task write permission on the Task and access to its Project, for completing
  and for undoing alike. An assignment grants nothing, whoever made it.
* **It goes through ERPNext's own save.** The Task is saved under the caller's own permissions,
  never with ``ignore_permissions`` and never written with ``db_set`` or ``db.set_value``, after
  the project access check.
* **A refusal is a plain sentence.** A ValidationError, a PermissionError or anything unexpected
  comes back as ``{"ok": False, "message": ...}`` with tags stripped, the transaction rolled back
  and the message log cleared. An unexpected error is logged after the rollback. A second tap
  that lost the race answers "already completed".
* **Undo puts back exactly what the completion changed, and only if nothing else did.** Previous
  status and progress (100 survives ERPNext's reset), and only the assignments the completion
  closed. Refused when the Task changed since or someone else completed it.

Installs its own ``frappe`` stub in ``setUpModule``, so it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_task_completion -v
"""

import ast
import datetime
import importlib
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
if str(REPO) not in sys.path:
	sys.path.insert(0, str(REPO))

API = "erpnext_enhancements.api.task_completion"
RULES = "erpnext_enhancements.task_enhancements.assignments"
SOURCE = APP / "api" / "task_completion.py"
STUBS = ("frappe", "frappe.utils")
NOT_FOUND = "That task wasn't found, or you can't change it."
_saved = {}

api = None
fake = None


class ValidationError(Exception):
	pass


class TimestampMismatchError(ValidationError):
	pass


class PermissionError_(Exception):
	pass


class Flags(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class Row(dict):
	"""frappe._dict: read with .get or as attributes."""

	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as e:
			raise AttributeError(key) from e


_OWN = ("name", "fields", "flags", "_fr")


class FakeTask:
	"""A Task loaded from the fake's store; save writes it back and logs the save."""

	def __init__(self, fr, name, fields):
		object.__setattr__(self, "_fr", fr)
		object.__setattr__(self, "name", name)
		object.__setattr__(self, "fields", dict(fields))
		object.__setattr__(self, "flags", Flags())

	def get(self, key, default=None):
		if key == "name":
			return self.name
		return self.fields.get(key, default)

	def __getattr__(self, key):
		return self.fields.get(key)

	def __setattr__(self, key, value):
		if key in _OWN:
			object.__setattr__(self, key, value)
		else:
			self.fields[key] = value

	def save(self, ignore_permissions=False):
		fr = self._fr
		hook = fr.on_save.get(self.name)
		if hook:
			hook(self)
		error = fr.raise_on_save.pop(self.name, None)
		if error:
			raise error
		# ERPNext's own completion side: a completed Task is at 100%; a reopen of a Task whose
		# progress this save left unchanged goes to 0 (clear_completion_on_reopen).
		stored = fr.store[self.name]
		if self.fields.get("status") == "Completed":
			self.fields["progress"] = 100
		elif stored.get("status") == "Completed" and self.fields.get("status") not in (
			"Completed",
			"Cancelled",
		):
			self.fields["completed_on"] = None
			self.fields["completed_by"] = None
			if self.fields.get("progress") == stored.get("progress"):
				self.fields["progress"] = 0
		fr.tick += 1
		self.fields["modified"] = f"2026-11-27 10:00:{fr.tick:02d}"
		fr.store[self.name] = dict(self.fields)
		fr.saves.setdefault(self.name, []).append(
			{
				"status": self.fields.get("status"),
				"progress": self.fields.get("progress"),
				"completed_on": self.fields.get("completed_on"),
				"completed_by": self.fields.get("completed_by"),
				"ignore_permissions": bool(ignore_permissions or self.flags.get("ignore_permissions")),
			}
		)


class FakeToDo:
	def __init__(
		self, name, task, status="Open", allocated_to="someone@x", owner="boss@x", assigned_by="boss@x"
	):
		self.name = name
		self.reference_type = "Task"
		self.reference_name = task
		self.status = status
		self.allocated_to = allocated_to
		self.owner = owner
		self.assigned_by = assigned_by
		self.saved = []

	def save(self, ignore_permissions=False):
		self.saved.append((self.status, ignore_permissions))


class FakeCache:
	def __init__(self):
		self.store = {}
		self.ttl = {}

	def set_value(self, key, value, expires_in_sec=None, **kwargs):
		self.store[key] = value
		self.ttl[key] = expires_in_sec

	def get_value(self, key, **kwargs):
		return self.store.get(key)

	def delete_value(self, key, **kwargs):
		self.store.pop(key, None)


class FakeDB:
	def __init__(self, fr):
		self.fr = fr
		self.rollbacks = 0

	def exists(self, doctype, name=None):
		return doctype == "Task" and name in self.fr.store

	def get_value(self, doctype, name, fields=None, as_dict=False):
		if doctype == "ToDo" and name in self.fr.todos:
			t = self.fr.todos[name]
			return Row(reference_type=t.reference_type, reference_name=t.reference_name, status=t.status)
		if doctype == "Task" and fields == "modified":
			return (self.fr.store.get(name) or {}).get("modified")
		return None

	def rollback(self):
		self.rollbacks += 1
		self.fr.events.append("rollback")


class FakeFrappe(types.ModuleType):
	ValidationError = ValidationError
	TimestampMismatchError = TimestampMismatchError
	PermissionError = PermissionError_

	def __init__(self):
		super().__init__("frappe")
		self.reset()

	def reset(self):
		self.session = types.SimpleNamespace(user="me@x")
		self.local = types.SimpleNamespace(message_log=[])
		self.store = {}
		self.saves = {}
		self.on_save = {}
		self.raise_on_save = {}
		self.tick = 0
		self.todos = {}
		self.writers = set()  # (user, task)
		self.readers = set()  # (user, task); writers read too
		self.projects = set()  # (user, project) the user may select
		self.errors = []
		self.events = []
		self.db = FakeDB(self)
		self.cache = FakeCache()

	def whitelist(self, *args, **kwargs):
		return lambda fn: fn

	def _(self, text, *args, **kwargs):
		return text

	def has_permission(self, doctype=None, ptype="read", doc=None, user=None, **kwargs):
		who = user or self.session.user
		name = getattr(doc, "name", doc)
		if doctype == "Project":
			return (who, name) in self.projects
		if ptype == "write":
			return (who, name) in self.writers
		return (who, name) in self.writers or (who, name) in self.readers

	def get_doc(self, doctype, name=None):
		if doctype == "Task":
			return FakeTask(self, name, self.store[name])
		return self.todos[name]

	def get_all(self, doctype, filters=None, fields=None, **kwargs):
		filters = filters or {}
		if doctype == "ToDo":
			return [
				Row(name=t.name, allocated_to=t.allocated_to, owner=t.owner, assigned_by=t.assigned_by)
				for t in self.todos.values()
				if t.reference_name == filters.get("reference_name") and t.status == filters.get("status")
			]
		if doctype == "Task":
			wanted = filters.get("name", ["in", []])[1]
			return [
				Row(
					name=n,
					subject=self.store[n].get("subject"),
					status=self.store[n].get("status"),
					parent_task=self.store[n].get("parent_task"),
				)
				for n in wanted
				if n in self.store
			]
		return []

	def clear_messages(self):
		self.local.message_log = []

	def log_error(self, title=None, message=None, **kwargs):
		self.errors.append((title, message))
		self.events.append("log_error")

	def get_traceback(self):
		return "Traceback: boom"


def _install():
	fr = FakeFrappe()
	utils = types.ModuleType("frappe.utils")
	utils.flt = lambda v, *a: float(v or 0)
	utils.nowdate = lambda: "2026-11-27"
	utils.now_datetime = lambda: datetime.datetime(2026, 11, 27, 10, 0)
	fr.utils = utils
	sys.modules["frappe"] = fr
	sys.modules["frappe.utils"] = utils
	return fr


def setUpModule():
	global api, fake
	for name in (*STUBS, API, RULES):
		_saved[name] = sys.modules.get(name)
		if name not in STUBS:
			sys.modules.pop(name, None)
	fake = _install()
	api = importlib.import_module(API)


def tearDownModule():
	for name, mod in _saved.items():
		if mod is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = mod


class Base(unittest.TestCase):
	def setUp(self):
		fake.reset()

	def task(self, name="TASK-1", **fields):
		base = {
			"status": "Open",
			"progress": 0,
			"subject": f"Subject of {name}",
			"modified": "2026-11-27 09:00:00",
		}
		base.update(fields)
		fake.store[name] = base
		return name

	def todo(self, name, task="TASK-1", **kw):
		t = FakeToDo(name, task, **kw)
		fake.todos[name] = t
		return t

	def saves(self, name="TASK-1"):
		return fake.saves.get(name, [])

	def stored(self, name="TASK-1"):
		return fake.store[name]


class CompleteTests(Base):
	def test_writer_completes_through_the_task_save(self):
		self.task(status="Working", progress=40)
		self.todo("TD-1", allocated_to="ann@x")
		fake.writers.add(("me@x", "TASK-1"))

		out = api.complete_task("TASK-1")

		self.assertTrue(out["ok"], out)
		self.assertEqual(out["status"], "Completed")
		self.assertTrue(out["undo"])
		self.assertEqual(len(self.saves()), 1)
		saved = self.saves()[0]
		self.assertEqual(saved["status"], "Completed")
		self.assertEqual(saved["completed_on"], "2026-11-27")
		self.assertEqual(saved["completed_by"], "me@x")
		self.assertFalse(saved["ignore_permissions"], "a writer must meet ERPNext's own checks")
		record = fake.cache.store["ee_task_completion_undo::me@x::TASK-1"]
		self.assertEqual(record["status"], "Working")
		self.assertEqual(record["progress"], 40.0)
		self.assertEqual(record["todos"], ["TD-1"])
		self.assertEqual(record["modified"], self.stored()["modified"], "Undo must know the state it left")
		self.assertEqual(fake.cache.ttl["ee_task_completion_undo::me@x::TASK-1"], 15 * 60)

	def test_a_writer_outside_the_project_is_refused_and_nothing_saves(self):
		self.task(project="PRJ-1")
		fake.writers.add(("me@x", "TASK-1"))

		out = api.complete_task("TASK-1")

		self.assertEqual(out, {"ok": False, "message": "You don't have permission to change this task."})
		self.assertEqual(self.saves(), [])
		self.assertEqual(fake.cache.store, {})

	def test_a_writer_inside_the_project_completes(self):
		self.task(project="PRJ-1")
		fake.writers.add(("me@x", "TASK-1"))
		fake.projects.add(("me@x", "PRJ-1"))

		self.assertTrue(api.complete_task("TASK-1")["ok"])
		self.assertFalse(self.saves()[0]["ignore_permissions"])

	def test_an_assignment_never_stands_in_for_write_permission(self):
		"""Review of #1521: whoever made the ToDo, it grants nothing on the Task."""
		self.task(subject="Secret subject")
		fake.readers.add(("me@x", "TASK-1"))
		for i, (owner, assigned_by) in enumerate((("boss@x", "boss@x"), ("me@x", "me@x"))):
			with self.subTest(owner=owner, assigned_by=assigned_by):
				fake.todos.clear()
				self.todo(f"TD-{i}", allocated_to="me@x", owner=owner, assigned_by=assigned_by)
				self.assertEqual(api.complete_task("TASK-1"), {"ok": False, "message": NOT_FOUND})
		self.assertEqual(self.saves(), [])

	def test_missing_and_forbidden_get_the_same_sentence_with_no_subject(self):
		self.task(subject="Secret subject")
		missing = api.complete_task("TASK-NOPE")
		forbidden = api.complete_task("TASK-1")
		self.assertEqual(missing, forbidden)
		self.assertEqual(forbidden, {"ok": False, "message": NOT_FOUND})
		self.assertNotIn("Secret", str(forbidden))

	def test_status_is_told_only_to_someone_entitled(self):
		for status in ("Completed", "Canceled", "Template"):
			with self.subTest(status=status):
				fake.reset()
				self.task(status=status, subject="Secret subject")
				self.assertEqual(api.complete_task("TASK-1"), {"ok": False, "message": NOT_FOUND})

	def test_already_completed_is_fine_and_changes_nothing(self):
		self.task(status="Completed")
		fake.writers.add(("me@x", "TASK-1"))

		out = api.complete_task("TASK-1")

		self.assertTrue(out["ok"])
		self.assertTrue(out["already"])
		self.assertFalse(out["undo"])
		self.assertEqual(self.saves(), [])

	def test_finished_statuses_are_refused(self):
		for status in ("Canceled", "Cancelled", "Template", "Invoiced"):
			with self.subTest(status=status):
				fake.reset()
				self.task(status=status)
				fake.writers.add(("me@x", "TASK-1"))
				out = api.complete_task("TASK-1")
				self.assertFalse(out["ok"])
				self.assertIn("nothing to complete", out["message"])
				self.assertEqual(self.saves(), [])

	def test_empty_task_is_refused(self):
		self.assertFalse(api.complete_task("")["ok"])
		self.assertFalse(api.complete_task(None)["ok"])

	def test_open_dependency_the_caller_can_read_is_named(self):
		self.task("TASK-0", status="Open", subject="Pressure test")
		self.task(depends_on=[Row(task="TASK-0")])
		fake.writers.add(("me@x", "TASK-1"))
		fake.readers.add(("me@x", "TASK-0"))

		out = api.complete_task("TASK-1")

		self.assertFalse(out["ok"])
		self.assertIn("depends on TASK-0 (Pressure test), which is Open", out["message"])
		self.assertEqual(self.saves(), [])

	def test_open_dependency_the_caller_cannot_read_is_not_named(self):
		self.task("TASK-0", status="Open", subject="Secret dependency")
		self.task(depends_on=[Row(task="TASK-0")])
		fake.writers.add(("me@x", "TASK-1"))

		out = api.complete_task("TASK-1")

		self.assertFalse(out["ok"])
		self.assertIn("depends on another task that isn't done yet", out["message"])
		self.assertNotIn("TASK-0", out["message"])
		self.assertNotIn("Secret", out["message"])

	def test_canceled_spelling_does_not_block(self):
		self.task("TASK-0", status="Canceled")
		self.task("TASK-9", status="Completed")
		self.task(depends_on=[Row(task="TASK-0"), Row(task="TASK-9")])
		fake.writers.add(("me@x", "TASK-1"))

		out = api.complete_task("TASK-1")

		self.assertTrue(out["ok"], out)
		self.assertEqual(len(self.saves()), 1)

	def test_phase_with_open_subtasks_counts_them(self):
		for n, status in (("S1", "Open"), ("S2", "Overdue"), ("S3", "Completed"), ("S4", "Working")):
			self.task(n, status=status, parent_task="PHASE")
		self.task(
			"PHASE",
			is_group=1,
			subject="Phase 400",
			depends_on=[Row(task=n) for n in ("S1", "S2", "S3", "S4")],
		)
		fake.writers.add(("me@x", "PHASE"))

		out = api.complete_task("PHASE")

		self.assertFalse(out["ok"])
		self.assertIn("PHASE (Phase 400) still has 3 open tasks under it", out["message"])
		self.assertEqual(self.saves("PHASE"), [])

	def test_phase_with_one_open_subtask_says_one(self):
		self.task("S1", status="Open", parent_task="PHASE")
		self.task("PHASE", is_group=1, depends_on=[Row(task="S1")])
		fake.writers.add(("me@x", "PHASE"))

		self.assertIn("still has 1 open task under it", api.complete_task("PHASE")["message"])

	def test_a_double_tap_that_loses_the_race_says_already(self):
		self.task()
		fake.writers.add(("me@x", "TASK-1"))

		def other_screen_won(doc):
			fake.store["TASK-1"] = dict(fake.store["TASK-1"], status="Completed", completed_by="ann@x")

		fake.on_save["TASK-1"] = other_screen_won
		fake.raise_on_save["TASK-1"] = TimestampMismatchError(
			"Document has been modified after you opened it"
		)

		out = api.complete_task("TASK-1")

		self.assertTrue(out["ok"], out)
		self.assertTrue(out["already"])
		self.assertFalse(out["undo"])
		self.assertEqual(fake.db.rollbacks, 1)
		self.assertEqual(fake.cache.store, {}, "this tap did not complete it, so it has nothing to undo")

	def test_a_race_that_left_it_open_asks_to_try_again(self):
		self.task()
		fake.writers.add(("me@x", "TASK-1"))
		fake.raise_on_save["TASK-1"] = TimestampMismatchError("modified")

		out = api.complete_task("TASK-1")

		self.assertFalse(out["ok"])
		self.assertIn("Try again", out["message"])

	def test_erpnext_refusal_on_save_is_one_plain_line_and_rolled_back(self):
		self.task()
		fake.writers.add(("me@x", "TASK-1"))
		fake.local.message_log = [{"message": "<b>Cannot</b> complete"}]
		fake.raise_on_save["TASK-1"] = ValidationError(
			"Cannot complete task <strong>TASK-1</strong> &amp; more."
		)

		out = api.complete_task("TASK-1")

		self.assertEqual(out, {"ok": False, "message": "Cannot complete task TASK-1 & more."})
		self.assertEqual(fake.db.rollbacks, 1)
		self.assertEqual(fake.local.message_log, [])
		self.assertEqual(fake.cache.store, {}, "no Undo record for a completion that did not happen")

	def test_empty_validation_error_falls_back_to_the_message_log(self):
		self.task()
		fake.writers.add(("me@x", "TASK-1"))
		fake.local.message_log = [{"message": "<p>Completed On cannot be greater than Today</p>"}]
		fake.raise_on_save["TASK-1"] = ValidationError("")

		self.assertEqual(api.complete_task("TASK-1")["message"], "Completed On cannot be greater than Today")

	def test_permission_error_on_save_is_a_plain_refusal(self):
		self.task()
		fake.writers.add(("me@x", "TASK-1"))
		fake.raise_on_save["TASK-1"] = PermissionError_("Project")

		out = api.complete_task("TASK-1")

		self.assertEqual(out["message"], "You don't have permission to change this task.")
		self.assertEqual(fake.db.rollbacks, 1)

	def test_unexpected_error_is_logged_after_the_rollback(self):
		self.task()
		fake.writers.add(("me@x", "TASK-1"))
		fake.raise_on_save["TASK-1"] = RuntimeError("boom")

		out = api.complete_task("TASK-1")

		self.assertFalse(out["ok"])
		self.assertIn("Error Log", out["message"])
		self.assertEqual(fake.events, ["rollback", "log_error"])
		self.assertEqual(fake.errors[0][1], "Traceback: boom")

	def test_what_erpnext_said_during_a_good_save_comes_back_plain(self):
		self.task()
		fake.writers.add(("me@x", "TASK-1"))
		fake.on_save["TASK-1"] = lambda doc: fake.local.message_log.append(
			{"message": '<a href="/app/task/TASK-2">TASK-2</a> was moved after the task it depends on.'}
		)

		out = api.complete_task("TASK-1")

		self.assertEqual(out["notes"], ["TASK-2 was moved after the task it depends on."])
		self.assertEqual(fake.local.message_log, [])


class ReopenTests(Base):
	KEY = "ee_task_completion_undo::me@x::TASK-1"

	def complete_first(self, **fields):
		self.task(**fields)
		out = api.complete_task("TASK-1")
		self.assertTrue(out["ok"], out)
		return out

	def test_undo_puts_back_status_progress_and_only_its_own_assignments(self):
		fake.writers.add(("me@x", "TASK-1"))
		mine = self.todo("TD-1", allocated_to="ann@x")
		self.complete_first(status="Working", progress=40)
		mine.status = "Closed"  # ERPNext's close_all_assignments
		other_task = self.todo("TD-2", task="TASK-2", status="Closed")
		still_open = self.todo("TD-3", status="Open")
		fake.cache.store[self.KEY]["todos"] += ["TD-2", "TD-3", "TD-X"]

		out = api.reopen_task("TASK-1")

		self.assertTrue(out["ok"], out)
		self.assertEqual(self.stored()["status"], "Working")
		self.assertEqual(self.stored()["progress"], 40.0)
		self.assertIsNone(self.stored()["completed_by"])
		self.assertEqual(out["restored_assignments"], 1)
		self.assertEqual(mine.status, "Open")
		self.assertEqual(mine.saved, [("Open", True)])
		self.assertEqual(other_task.status, "Closed")
		self.assertEqual(still_open.saved, [])
		self.assertNotIn(self.KEY, fake.cache.store)

	def test_a_task_that_was_at_100_gets_its_100_back(self):
		"""clear_completion_on_reopen zeroes a progress the reopen left unchanged."""
		fake.writers.add(("me@x", "TASK-1"))
		self.complete_first(status="Working", progress=100)

		api.reopen_task("TASK-1")

		self.assertEqual(self.stored()["status"], "Working")
		self.assertEqual(self.stored()["progress"], 100.0)
		self.assertEqual([s["progress"] for s in self.saves()], [100, 0, 100.0])

	def test_undo_is_refused_once_the_task_has_changed_since(self):
		fake.writers.add(("me@x", "TASK-1"))
		self.complete_first()
		# A manager reopened and completed it again in the meantime.
		fake.store["TASK-1"] = dict(fake.store["TASK-1"], modified="2026-11-27 11:30:00")

		out = api.reopen_task("TASK-1")

		self.assertFalse(out["ok"])
		self.assertIn("has changed since you completed it", out["message"])
		self.assertEqual(self.stored()["status"], "Completed")
		self.assertNotIn(self.KEY, fake.cache.store)

	def test_undo_is_refused_when_someone_else_completed_it(self):
		fake.writers.add(("me@x", "TASK-1"))
		self.complete_first()
		fake.store["TASK-1"] = dict(fake.store["TASK-1"], completed_by="boss@x")

		out = api.reopen_task("TASK-1")

		self.assertFalse(out["ok"])
		self.assertEqual(self.stored()["status"], "Completed")

	def test_the_completer_who_lost_write_cannot_undo(self):
		fake.writers.add(("me@x", "TASK-1"))
		self.complete_first()
		fake.writers.clear()
		fake.readers.add(("me@x", "TASK-1"))

		self.assertEqual(api.reopen_task("TASK-1"), {"ok": False, "message": NOT_FOUND})
		self.assertEqual(self.stored()["status"], "Completed")

	def test_undo_makes_the_project_check_too(self):
		fake.writers.add(("me@x", "TASK-1"))
		fake.projects.add(("me@x", "PRJ-1"))
		self.complete_first(project="PRJ-1")
		fake.projects.clear()

		out = api.reopen_task("TASK-1")

		self.assertEqual(out["message"], "You don't have permission to change this task.")
		self.assertEqual(self.stored()["status"], "Completed")

	def test_without_a_record_reopening_needs_write_and_says_nothing_otherwise(self):
		self.task(status="Completed", subject="Secret subject")
		fake.readers.add(("me@x", "TASK-1"))

		out = api.reopen_task("TASK-1")

		self.assertEqual(out, {"ok": False, "message": NOT_FOUND})
		self.assertEqual(self.saves(), [])

	def test_without_a_record_a_writer_reopens_as_open_and_is_told(self):
		self.task(status="Completed", progress=100)
		fake.writers.add(("me@x", "TASK-1"))

		out = api.reopen_task("TASK-1")

		self.assertTrue(out["ok"], out)
		self.assertEqual(self.stored()["status"], "Open")
		self.assertFalse(self.saves()[-1]["ignore_permissions"])
		self.assertEqual(out["restored_assignments"], 0)
		self.assertTrue(any("not restored" in n for n in out["notes"]))

	def test_a_record_naming_a_finished_status_reopens_as_open(self):
		fake.writers.add(("me@x", "TASK-1"))
		self.complete_first()
		fake.cache.store[self.KEY]["status"] = "Invoiced"

		api.reopen_task("TASK-1")

		self.assertEqual(self.stored()["status"], "Open")

	def test_reopening_an_open_task_does_nothing(self):
		self.task(status="Open")
		fake.writers.add(("me@x", "TASK-1"))
		fake.cache.store[self.KEY] = {"status": "Open"}

		out = api.reopen_task("TASK-1")

		self.assertTrue(out["ok"])
		self.assertTrue(out["already"])
		self.assertEqual(self.saves(), [])
		self.assertNotIn(self.KEY, fake.cache.store)

	def test_another_persons_record_does_not_count(self):
		self.task(status="Completed")
		fake.cache.store["ee_task_completion_undo::ann@x::TASK-1"] = {"status": "Open", "todos": []}

		self.assertEqual(api.reopen_task("TASK-1"), {"ok": False, "message": NOT_FOUND})
		self.assertEqual(self.saves(), [])

	def test_a_refused_reopen_is_plain_and_rolled_back(self):
		self.task(status="Completed")
		fake.writers.add(("me@x", "TASK-1"))
		fake.raise_on_save["TASK-1"] = ValidationError("<b>Nope</b>")

		self.assertEqual(api.reopen_task("TASK-1"), {"ok": False, "message": "Nope"})
		self.assertEqual(fake.db.rollbacks, 1)


class SourceTests(unittest.TestCase):
	def setUp(self):
		self.source = SOURCE.read_text(encoding="utf-8")
		self.tree = ast.parse(self.source)

	def test_both_endpoints_are_post_only(self):
		found = {}
		for node in self.tree.body:
			if isinstance(node, ast.FunctionDef):
				for dec in node.decorator_list:
					if isinstance(dec, ast.Call) and getattr(dec.func, "attr", None) == "whitelist":
						methods = [kw.value for kw in dec.keywords if kw.arg == "methods"]
						found[node.name] = [ast.literal_eval(m) for m in methods]
		self.assertEqual(found, {"complete_task": [["POST"]], "reopen_task": [["POST"]]})

	def test_the_task_is_never_written_around_its_controller(self):
		code = "\n".join(line for line in self.source.splitlines() if not line.lstrip().startswith("#"))
		for shortcut in (".db_set(", "db.set_value(", "db.sql(", "set_single_value("):
			self.assertNotIn(shortcut, code, f"{shortcut} skips ERPNext's Task rules")

	def test_the_task_is_never_saved_with_ignore_permissions(self):
		"""Review of #1521: the caller's own Task write permission, or nothing."""
		code = "\n".join(line for line in self.source.splitlines() if not line.lstrip().startswith("#"))
		self.assertNotIn("flags.ignore_permissions", code)
		self.assertEqual(code.count("ignore_permissions=True"), 1, "only restoring the closed ToDos")
		self.assertIn('doc = frappe.get_doc("ToDo", todo)', code)

	def test_each_task_save_follows_the_project_check(self):
		lines = self.source.splitlines()
		for fn in ("def _complete(", "def _reopen("):
			body = lines[next(i for i, line in enumerate(lines) if line.startswith(fn)) :]
			first_save = next(i for i, line in enumerate(body) if "doc.save()" in line)
			self.assertTrue(
				any("require_project_access(doc)" in line for line in body[:first_save]),
				f"{fn} saves before the project check",
			)

	def test_tabs_like_every_new_api_file(self):
		indented = [line for line in self.source.splitlines() if line[:1] in (" ", "\t")]
		self.assertTrue(indented)
		self.assertFalse(
			[line for line in indented if line.startswith("    ") and not line.startswith("\t")][:1],
			"api/task_completion.py is tab-indented",
		)


if __name__ == "__main__":
	unittest.main()
