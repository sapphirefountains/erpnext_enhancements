"""Bench-free tests: a finished Task is never flipped to Overdue.

ERPNext v16's daily ``set_tasks_as_overdue`` calls ``Task.update_status`` on every Task not
``Cancelled`` or ``Completed``, and the core method sets ``Overdue`` once ``exp_end_date`` has
passed. This site spells the status ``Canceled``, and ``Invoiced`` and ``Template`` are finished
too, so all three kept coming back as Overdue. This app's Task override now returns early for
them and hands everything else to the core method unchanged.

The same override marks a Task saved into a finished state before ERPNext's own ``validate``
closes its assignments, so a repeating assignment closed that way makes no next one, and lets a
dependency this site spelled "Canceled" stop blocking completion (v1.699.0).

``erpnext`` is stubbed with a base class that records the call, so this suite installs stub
modules and runs in its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_task_overdue_guard -v
"""

import importlib
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MODULE = "erpnext_enhancements.task_enhancements.doctype.task.task"
STUBS = ("frappe", "erpnext", "erpnext.projects", "erpnext.projects.doctype", "erpnext.projects.doctype.task", "erpnext.projects.doctype.task.task")
RECURRENCE = "erpnext_enhancements.task_enhancements.recurrence"
_saved = {}
task_module = None


class BaseTask:
    """Stands in for ERPNext's Task: its update_status is what the core would run."""

    def __init__(self, status):
        self.status = status
        self.core_calls = 0

    def update_status(self):
        self.core_calls += 1
        self.status = "Overdue"

    def validate(self):
        # ERPNext's validate is where a Completed Task's assignments are closed.
        self.validated_after = list(marked)

    def get(self, key):
        return getattr(self, key, None)

    def validate_status(self):
        # ERPNext's dependency check loops over depends_on; record what it would have seen.
        self.core_saw = [row.task for row in self.depends_on]
        if any(canceled_tasks.get(row.task) not in ("Completed", "Cancelled") for row in self.depends_on):
            raise RuntimeError("Cannot complete task")


#: What the stub recurrence module was told, in order.
marked = []
#: Task name -> status, for the stub frappe.get_all.
canceled_tasks = {}


def setUpModule():
    global task_module
    for name in (*STUBS, MODULE, RECURRENCE):
        _saved[name] = sys.modules.get(name)
    frappe = types.ModuleType("frappe")
    frappe.whitelist = lambda *a, **k: (lambda f: f)
    frappe.get_all = lambda doctype, filters=None, pluck=None, **k: [
        name for name in filters["name"][1] if canceled_tasks.get(name) == filters["status"]
    ]
    sys.modules["frappe"] = frappe
    for name in STUBS[1:]:
        sys.modules[name] = types.ModuleType(name)
    sys.modules["erpnext.projects.doctype.task.task"].Task = BaseTask
    recurrence = types.ModuleType(RECURRENCE)
    recurrence.mark_finishing = lambda doctype, name: marked.append((doctype, name))
    sys.modules[RECURRENCE] = recurrence
    sys.modules.pop(MODULE, None)
    task_module = importlib.import_module(MODULE)


def tearDownModule():
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class TestOverdueGuard(unittest.TestCase):
    def test_finished_statuses_are_never_flipped(self):
        for status in ("Completed", "Canceled", "Cancelled", "Invoiced", "Template"):
            with self.subTest(status=status):
                task = task_module.Task(status)
                task.update_status()
                self.assertEqual((task.status, task.core_calls), (status, 0))

    def test_open_work_still_goes_through_the_core_rule(self):
        for status in ("Open", "Working", "Pending Review", "Overdue"):
            with self.subTest(status=status):
                task = task_module.Task(status)
                task.update_status()
                self.assertEqual((task.status, task.core_calls), ("Overdue", 1))

    def test_both_spellings_of_canceled_are_covered(self):
        self.assertIn("Canceled", task_module.FINISHED_STATUSES)
        self.assertIn("Cancelled", task_module.FINISHED_STATUSES)


class TestFinishingMark(unittest.TestCase):
    def setUp(self):
        marked.clear()

    def task(self, status):
        task = task_module.Task(status)
        task.doctype, task.name = "Task", "TASK-1"
        return task

    def test_a_finishing_task_is_marked_before_erpnext_closes_its_assignments(self):
        for status in ("Completed", "Canceled", "Cancelled", "Invoiced"):
            with self.subTest(status=status):
                marked.clear()
                task = self.task(status)
                task.validate()
                self.assertEqual(task.validated_after, [("Task", "TASK-1")])

    def test_open_work_is_not_marked(self):
        task = self.task("Working")
        task.validate()
        self.assertEqual(task.validated_after, [])


class TestCanceledSpelling(unittest.TestCase):
    """This site spells it "Canceled"; ERPNext's dependency check accepts only "Cancelled"."""

    def task(self, status, *deps):
        task = task_module.Task(status)
        task.depends_on = [types.SimpleNamespace(task=name) for name in deps]
        return task

    def setUp(self):
        canceled_tasks.clear()
        canceled_tasks.update({"A": "Completed", "B": "Canceled", "C": "Working", "D": "Cancelled"})

    def test_a_canceled_dependency_no_longer_blocks_completion(self):
        task = self.task("Completed", "A", "B", "D")
        task.validate_status()
        self.assertEqual(task.core_saw, ["A", "D"])
        self.assertEqual([row.task for row in task.depends_on], ["A", "B", "D"], "rows put back")

    def test_an_open_dependency_still_blocks_and_the_rows_come_back(self):
        task = self.task("Completed", "B", "C")
        with self.assertRaises(RuntimeError):
            task.validate_status()
        self.assertEqual([row.task for row in task.depends_on], ["B", "C"])

    def test_nothing_is_hidden_unless_the_task_is_being_completed(self):
        task = self.task("Working", "A", "B")
        canceled_tasks["B"] = "Completed"
        task.validate_status()
        self.assertEqual(task.core_saw, ["A", "B"])


if __name__ == "__main__":
    unittest.main()
