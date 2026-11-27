"""Bench-free tests for one-tap complete and Undo (``api/task_completion.py``).

Triton's Projects Dashboard completes a Task from a card, as the person who pressed the check.
What each part guards, in the terms it fails in:

* **It goes through ERPNext's own save.** The Task is saved, never written with ``db_set`` or
  ``db.set_value``, so ERPNext's dependency check, project access check, assignment closing and
  project % all run. A shortcut there completes a Task ERPNext would refuse.
* **Task permissions hold.** No write permission and no assignment: refused, nothing saved. An
  assignee without write completes it the way My Work's Done does. A person who may write saves
  without ``ignore_permissions``, so ERPNext's own checks still apply to them.
* **A refusal is a plain sentence.** A ValidationError, a PermissionError or anything unexpected
  comes back as ``{"ok": False, "message": ...}`` with tags stripped, the transaction rolled back
  and the message log cleared. An unexpected error is logged after the rollback, or the rollback
  would take the Error Log row with it.
* **The sentence names what is in the way.** An open dependency is named; a phase with open
  subtasks says how many. This site's "Canceled" spelling does not block.
* **Undo puts back exactly what the completion changed.** Previous status and progress, and only
  the assignments the completion closed, only while they are still Closed and still on this Task.
  Without the record, Undo needs write permission and says no assignment came back.

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
_saved = {}

api = None
fake = None


class ValidationError(Exception):
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


class FakeTask:
	def __init__(self, name, **fields):
		self.name = name
		self.fields = {"status": "Open", "progress": 0, "subject": f"Subject of {name}"}
		self.fields.update(fields)
		self.flags = Flags()
		self.saves = []
		self.raise_on_save = None
		self.on_save = None

	def get(self, key, default=None):
		if key == "name":
			return self.name
		return self.fields.get(key, default)

	def __getattr__(self, key):
		if key in ("fields", "name", "flags", "saves", "raise_on_save", "on_save"):
			raise AttributeError(key)
		return self.fields.get(key)

	def __setattr__(self, key, value):
		if key in ("fields", "name", "flags", "saves", "raise_on_save", "on_save"):
			object.__setattr__(self, key, value)
		else:
			self.fields[key] = value

	def save(self, ignore_permissions=False):
		if self.raise_on_save:
			raise self.raise_on_save
		if self.on_save:
			self.on_save(self)
		self.saves.append(
			{
				"status": self.fields.get("status"),
				"progress": self.fields.get("progress"),
				"completed_on": self.fields.get("completed_on"),
				"completed_by": self.fields.get("completed_by"),
				"ignore_permissions": bool(ignore_permissions or self.flags.get("ignore_permissions")),
			}
		)
		self.fields["modified"] = "2026-11-27 10:00:00"


class FakeToDo:
	def __init__(self, name, task, status="Open", allocated_to="someone@x"):
		self.name = name
		self.reference_type = "Task"
		self.reference_name = task
		self.status = status
		self.allocated_to = allocated_to
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
		if doctype == "Task":
			return name in self.fr.tasks
		return False

	def get_value(self, doctype, name, fields=None, as_dict=False):
		if doctype == "ToDo" and name in self.fr.todos:
			t = self.fr.todos[name]
			return Row(reference_type=t.reference_type, reference_name=t.reference_name, status=t.status)
		return None

	def rollback(self):
		self.rollbacks += 1
		self.fr.events.append("rollback")


class FakeFrappe(types.ModuleType):
	ValidationError = ValidationError
	PermissionError = PermissionError_

	def __init__(self):
		super().__init__("frappe")
		self.session = types.SimpleNamespace(user="me@x")
		self.local = types.SimpleNamespace(message_log=[])
		self.tasks = {}
		self.todos = {}
		self.writers = set()  # (user, task) pairs with Task write permission
		self.errors = []
		self.events = []
		self.db = FakeDB(self)
		self.cache = FakeCache()

	def whitelist(self, *args, **kwargs):
		return lambda fn: fn

	def _(self, text, *args, **kwargs):
		return text

	def has_permission(self, doctype=None, ptype="read", doc=None, user=None, **kwargs):
		return ((user or self.session.user), getattr(doc, "name", doc)) in self.writers

	def get_doc(self, doctype, name=None):
		if doctype == "Task":
			return self.tasks[name]
		return self.todos[name]

	def get_all(self, doctype, filters=None, fields=None, **kwargs):
		filters = filters or {}
		if doctype == "ToDo":
			return [
				Row(name=t.name, allocated_to=t.allocated_to)
				for t in self.todos.values()
				if t.reference_name == filters.get("reference_name") and t.status == filters.get("status")
			]
		if doctype == "Task":
			wanted = filters.get("name", ["in", []])[1]
			return [
				Row(
					name=n,
					subject=self.tasks[n].get("subject"),
					status=self.tasks[n].get("status"),
					parent_task=self.tasks[n].get("parent_task"),
				)
				for n in wanted
				if n in self.tasks
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
		fake.tasks.clear()
		fake.todos.clear()
		fake.writers.clear()
		fake.errors.clear()
		fake.events.clear()
		fake.cache.store.clear()
		fake.local.message_log = []
		fake.db.rollbacks = 0
		fake.session.user = "me@x"

	def task(self, name="TASK-1", **fields):
		t = FakeTask(name, **fields)
		fake.tasks[name] = t
		return t

	def todo(self, name, task="TASK-1", **kw):
		t = FakeToDo(name, task, **kw)
		fake.todos[name] = t
		return t


class CompleteTests(Base):
	def test_writer_completes_through_the_task_save(self):
		t = self.task(status="Working", progress=40)
		self.todo("TD-1", allocated_to="ann@x")
		fake.writers.add(("me@x", "TASK-1"))

		out = api.complete_task("TASK-1")

		self.assertTrue(out["ok"], out)
		self.assertEqual(out["status"], "Completed")
		self.assertTrue(out["undo"])
		self.assertEqual(len(t.saves), 1)
		saved = t.saves[0]
		self.assertEqual(saved["status"], "Completed")
		self.assertEqual(saved["completed_on"], "2026-11-27")
		self.assertEqual(saved["completed_by"], "me@x")
		self.assertFalse(saved["ignore_permissions"], "a writer must meet ERPNext's own checks")
		record = fake.cache.store["ee_task_completion_undo::me@x::TASK-1"]
		self.assertEqual(record["status"], "Working")
		self.assertEqual(record["progress"], 40.0)
		self.assertEqual(record["todos"], ["TD-1"])
		self.assertEqual(fake.cache.ttl["ee_task_completion_undo::me@x::TASK-1"], 15 * 60)

	def test_assignee_without_write_completes_like_my_work(self):
		t = self.task()
		self.todo("TD-1", allocated_to="me@x")

		out = api.complete_task("TASK-1")

		self.assertTrue(out["ok"], out)
		self.assertTrue(t.saves[0]["ignore_permissions"])

	def test_no_write_and_no_assignment_is_refused_and_nothing_saves(self):
		t = self.task(subject="Membrane cure")
		self.todo("TD-1", allocated_to="ann@x")

		out = api.complete_task("TASK-1")

		self.assertFalse(out["ok"])
		self.assertIn("TASK-1 (Membrane cure)", out["message"])
		self.assertIn("write permission", out["message"])
		self.assertEqual(t.saves, [])
		self.assertEqual(fake.cache.store, {})

	def test_already_completed_is_fine_and_changes_nothing(self):
		t = self.task(status="Completed")
		fake.writers.add(("me@x", "TASK-1"))

		out = api.complete_task("TASK-1")

		self.assertTrue(out["ok"])
		self.assertTrue(out["already"])
		self.assertFalse(out["undo"])
		self.assertEqual(t.saves, [])

	def test_finished_statuses_are_refused(self):
		fake.writers.add(("me@x", "TASK-1"))
		for status in ("Canceled", "Cancelled", "Template", "Invoiced"):
			with self.subTest(status=status):
				t = self.task(status=status)
				out = api.complete_task("TASK-1")
				self.assertFalse(out["ok"])
				self.assertIn("nothing to complete", out["message"])
				self.assertEqual(t.saves, [])

	def test_missing_or_empty_task_is_refused(self):
		self.assertFalse(api.complete_task("TASK-NOPE")["ok"])
		self.assertFalse(api.complete_task("")["ok"])
		self.assertFalse(api.complete_task(None)["ok"])

	def test_open_dependency_is_named(self):
		self.task("TASK-0", status="Open", subject="Pressure test")
		t = self.task(depends_on=[Row(task="TASK-0")])
		fake.writers.add(("me@x", "TASK-1"))

		out = api.complete_task("TASK-1")

		self.assertFalse(out["ok"])
		self.assertIn("depends on TASK-0 (Pressure test), which is Open", out["message"])
		self.assertEqual(t.saves, [])

	def test_canceled_spelling_does_not_block(self):
		self.task("TASK-0", status="Canceled")
		self.task("TASK-9", status="Completed")
		t = self.task(depends_on=[Row(task="TASK-0"), Row(task="TASK-9")])
		fake.writers.add(("me@x", "TASK-1"))

		out = api.complete_task("TASK-1")

		self.assertTrue(out["ok"], out)
		self.assertEqual(len(t.saves), 1)

	def test_phase_with_open_subtasks_counts_them(self):
		for n, status in (("S1", "Open"), ("S2", "Overdue"), ("S3", "Completed"), ("S4", "Working")):
			self.task(n, status=status, parent_task="PHASE")
		t = self.task(
			"PHASE",
			is_group=1,
			subject="Phase 400",
			depends_on=[Row(task=n) for n in ("S1", "S2", "S3", "S4")],
		)
		fake.writers.add(("me@x", "PHASE"))

		out = api.complete_task("PHASE")

		self.assertFalse(out["ok"])
		self.assertIn("PHASE (Phase 400) still has 3 open tasks under it", out["message"])
		self.assertEqual(t.saves, [])

	def test_phase_with_one_open_subtask_says_one(self):
		self.task("S1", status="Open", parent_task="PHASE")
		self.task("PHASE", is_group=1, depends_on=[Row(task="S1")])
		fake.writers.add(("me@x", "PHASE"))

		out = api.complete_task("PHASE")

		self.assertIn("still has 1 open task under it", out["message"])

	def test_erpnext_refusal_on_save_is_one_plain_line_and_rolled_back(self):
		t = self.task()
		fake.writers.add(("me@x", "TASK-1"))
		fake.local.message_log = [{"message": "<b>Cannot</b> complete"}]
		t.raise_on_save = ValidationError("Cannot complete task <strong>TASK-1</strong> &amp; more.")

		out = api.complete_task("TASK-1")

		self.assertEqual(out, {"ok": False, "message": "Cannot complete task TASK-1 & more."})
		self.assertEqual(fake.db.rollbacks, 1)
		self.assertEqual(fake.local.message_log, [])
		self.assertEqual(fake.cache.store, {}, "no Undo record for a completion that did not happen")

	def test_empty_validation_error_falls_back_to_the_message_log(self):
		t = self.task()
		fake.writers.add(("me@x", "TASK-1"))
		fake.local.message_log = [{"message": "<p>Completed On cannot be greater than Today</p>"}]
		t.raise_on_save = ValidationError("")

		out = api.complete_task("TASK-1")

		self.assertEqual(out["message"], "Completed On cannot be greater than Today")

	def test_permission_error_on_save_is_a_plain_refusal(self):
		t = self.task()
		fake.writers.add(("me@x", "TASK-1"))
		t.raise_on_save = PermissionError_("Project")

		out = api.complete_task("TASK-1")

		self.assertFalse(out["ok"])
		self.assertEqual(out["message"], "You don't have permission to change this task.")
		self.assertEqual(fake.db.rollbacks, 1)

	def test_unexpected_error_is_logged_after_the_rollback(self):
		t = self.task()
		fake.writers.add(("me@x", "TASK-1"))
		t.raise_on_save = RuntimeError("boom")

		out = api.complete_task("TASK-1")

		self.assertFalse(out["ok"])
		self.assertIn("Error Log", out["message"])
		self.assertEqual(fake.events, ["rollback", "log_error"])
		self.assertEqual(fake.errors[0][1], "Traceback: boom")

	def test_what_erpnext_said_during_a_good_save_comes_back_plain(self):
		t = self.task()
		fake.writers.add(("me@x", "TASK-1"))
		t.on_save = lambda doc: fake.local.message_log.append(
			{"message": '<a href="/app/task/TASK-2">TASK-2</a> was moved after the task it depends on.'}
		)

		out = api.complete_task("TASK-1")

		self.assertEqual(out["notes"], ["TASK-2 was moved after the task it depends on."])
		self.assertEqual(fake.local.message_log, [])


class ReopenTests(Base):
	KEY = "ee_task_completion_undo::me@x::TASK-1"

	def test_undo_puts_back_status_progress_and_only_its_own_assignments(self):
		t = self.task(status="Completed", progress=100)
		fake.writers.add(("me@x", "TASK-1"))
		mine = self.todo("TD-1", status="Closed")
		other_task = self.todo("TD-2", task="TASK-2", status="Closed")
		still_open = self.todo("TD-3", status="Open")
		fake.cache.store[self.KEY] = {
			"status": "Working",
			"progress": 40,
			"todos": ["TD-1", "TD-2", "TD-3", "TD-X"],
		}

		out = api.reopen_task("TASK-1")

		self.assertTrue(out["ok"], out)
		self.assertEqual(t.saves[-1]["status"], "Working")
		self.assertEqual(t.saves[-1]["progress"], 40.0)
		self.assertEqual(out["restored_assignments"], 1)
		self.assertEqual(mine.status, "Open")
		self.assertEqual(mine.saved, [("Open", True)])
		self.assertEqual(other_task.status, "Closed")
		self.assertEqual(still_open.saved, [])
		self.assertNotIn(self.KEY, fake.cache.store)

	def test_the_completer_may_undo_without_write(self):
		t = self.task(status="Completed")
		fake.cache.store[self.KEY] = {"status": "Open", "progress": 0, "todos": []}

		out = api.reopen_task("TASK-1")

		self.assertTrue(out["ok"], out)
		self.assertTrue(t.saves[-1]["ignore_permissions"])

	def test_without_a_record_reopening_needs_write(self):
		t = self.task(status="Completed")

		out = api.reopen_task("TASK-1")

		self.assertFalse(out["ok"])
		self.assertIn("write permission", out["message"])
		self.assertEqual(t.saves, [])

	def test_without_a_record_a_writer_reopens_as_open_and_is_told(self):
		t = self.task(status="Completed")
		fake.writers.add(("me@x", "TASK-1"))

		out = api.reopen_task("TASK-1")

		self.assertTrue(out["ok"], out)
		self.assertEqual(t.saves[-1]["status"], "Open")
		self.assertFalse(t.saves[-1]["ignore_permissions"])
		self.assertEqual(out["restored_assignments"], 0)
		self.assertTrue(any("not restored" in n for n in out["notes"]))

	def test_a_record_naming_a_finished_status_reopens_as_open(self):
		t = self.task(status="Completed")
		fake.writers.add(("me@x", "TASK-1"))
		fake.cache.store[self.KEY] = {"status": "Invoiced", "progress": 0, "todos": []}

		api.reopen_task("TASK-1")

		self.assertEqual(t.saves[-1]["status"], "Open")

	def test_reopening_an_open_task_does_nothing(self):
		t = self.task(status="Open")
		fake.writers.add(("me@x", "TASK-1"))
		fake.cache.store[self.KEY] = {"status": "Open"}

		out = api.reopen_task("TASK-1")

		self.assertTrue(out["ok"])
		self.assertTrue(out["already"])
		self.assertEqual(t.saves, [])
		self.assertNotIn(self.KEY, fake.cache.store)

	def test_another_persons_record_does_not_count(self):
		t = self.task(status="Completed")
		fake.cache.store["ee_task_completion_undo::ann@x::TASK-1"] = {"status": "Open", "todos": []}

		out = api.reopen_task("TASK-1")

		self.assertFalse(out["ok"])
		self.assertEqual(t.saves, [])

	def test_a_refused_reopen_is_plain_and_rolled_back(self):
		t = self.task(status="Completed")
		fake.writers.add(("me@x", "TASK-1"))
		t.raise_on_save = ValidationError("<b>Nope</b>")

		out = api.reopen_task("TASK-1")

		self.assertEqual(out, {"ok": False, "message": "Nope"})
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

	def test_tabs_like_every_new_api_file(self):
		indented = [line for line in self.source.splitlines() if line[:1] in (" ", "\t")]
		self.assertTrue(indented)
		self.assertFalse(
			[line for line in indented if line.startswith("    ") and not line.startswith("\t")][:1],
			"api/task_completion.py is tab-indented",
		)


if __name__ == "__main__":
	unittest.main()
