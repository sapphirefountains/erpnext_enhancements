"""Bench-free tests: a finished Task is never flipped to Overdue.

ERPNext v16's daily ``set_tasks_as_overdue`` calls ``Task.update_status`` on every Task not
``Cancelled`` or ``Completed``, and the core method sets ``Overdue`` once ``exp_end_date`` has
passed. This site spells the status ``Canceled``, and ``Invoiced`` and ``Template`` are finished
too, so all three kept coming back as Overdue. This app's Task override now returns early for
them and hands everything else to the core method unchanged.

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


def setUpModule():
    global task_module
    for name in (*STUBS, MODULE):
        _saved[name] = sys.modules.get(name)
    frappe = types.ModuleType("frappe")
    frappe.whitelist = lambda *a, **k: (lambda f: f)
    sys.modules["frappe"] = frappe
    for name in STUBS[1:]:
        sys.modules[name] = types.ModuleType(name)
    sys.modules["erpnext.projects.doctype.task.task"].Task = BaseTask
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


if __name__ == "__main__":
    unittest.main()
