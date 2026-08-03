"""Structural guards on `hooks.py` (bench-free).

`hooks.py` is one large dict literal, and Python resolves a repeated key by **keeping the
last one and silently discarding the earlier value**. There is no error, no warning at
import, and nothing at runtime — the hooks simply never fire.

That is not hypothetical. Two `doc_events` keys were duplicated by the Training module's
compliance hooks, and the earlier blocks lost:

* **`Task`** — elapsed-time calculation, Google Calendar sync, recurring-task generation,
  the project dashboard's realtime update, and project date sync on both `on_update` and
  `on_trash`. Six handlers.
* **`Sapphire Maintenance Record`** — the next-visit-date update on submit.

Nobody noticed because the symptom is absence: recurring tasks quietly stop generating,
project dates quietly stop moving. Ruff flags this as `F601`, but that job is advisory on
this repo because of a pre-existing backlog, so it does not fail a PR. These tests do.

Read with `ast` rather than by importing, because importing `hooks.py` pulls in `frappe`.

Run: python -m unittest erpnext_enhancements.tests.test_hooks_integrity
"""

import ast
import collections
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

HOOKS = APP_ROOT / "hooks.py"


def hooks_tree():
    return ast.parse(HOOKS.read_text(encoding="utf-8"))


def walk_dicts(node, path="hooks.py"):
    """Yield (path, ast.Dict) for every dict literal, nested ones included."""
    for child in ast.walk(node):
        if isinstance(child, ast.Dict):
            yield path, child


def top_level_assignments():
    out = {}
    for node in hooks_tree().body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = node.value
    return out


class TestNoDuplicateKeys(unittest.TestCase):
    def test_no_duplicate_keys_in_any_dict(self):
        """The F601 class: a repeated key silently discards everything under the first."""
        offenders = []
        for _path, node in walk_dicts(hooks_tree()):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            for key, count in collections.Counter(keys).items():
                if count > 1:
                    offenders.append(f"line {node.lineno}: {key!r} appears {count}x")
        self.assertEqual(
            offenders,
            [],
            "Duplicate dict keys in hooks.py -- the earlier value is silently discarded:\n  "
            + "\n  ".join(offenders),
        )

    def test_doc_events_keys_are_unique(self):
        """Named explicitly: this is the one that actually cost us handlers."""
        value = top_level_assignments().get("doc_events")
        self.assertIsNotNone(value, "doc_events not found in hooks.py")
        keys = [k.value for k in value.keys if isinstance(k, ast.Constant)]
        dupes = sorted(k for k, c in collections.Counter(keys).items() if c > 1)
        self.assertEqual(dupes, [], f"doctypes registered twice in doc_events: {dupes}")


class TestNoDuplicateHandlers(unittest.TestCase):
    def test_no_handler_registered_twice_for_one_event(self):
        """A handler listed twice under one event runs twice per save."""
        value = top_level_assignments().get("doc_events")
        events = ast.literal_eval(value)
        offenders = []
        for doctype, handlers in events.items():
            for event, target in handlers.items():
                targets = target if isinstance(target, list) else [target]
                for name, count in collections.Counter(targets).items():
                    if count > 1:
                        offenders.append(f"{doctype}.{event}: {name} x{count}")
        self.assertEqual(offenders, [], "handler registered more than once:\n  " + "\n  ".join(offenders))

    def test_hook_lists_have_no_repeats(self):
        """Same idea for the flat lists -- after_migrate, before_migrate and friends."""
        offenders = []
        for name, value in top_level_assignments().items():
            if not isinstance(value, ast.List):
                continue
            try:
                items = ast.literal_eval(value)
            except ValueError:
                continue
            strings = [i for i in items if isinstance(i, str)]
            for entry, count in collections.Counter(strings).items():
                if count > 1:
                    offenders.append(f"{name}: {entry} x{count}")
        self.assertEqual(offenders, [], "entry repeated in a hook list:\n  " + "\n  ".join(offenders))


class TestHandlersLookReal(unittest.TestCase):
    def test_doc_event_handlers_are_app_dotted_paths(self):
        """A typo'd module path fails silently at runtime, the same way."""
        events = ast.literal_eval(top_level_assignments()["doc_events"])
        for doctype, handlers in events.items():
            for event, target in handlers.items():
                for name in target if isinstance(target, list) else [target]:
                    with self.subTest(f"{doctype}.{event}"):
                        self.assertTrue(
                            name.startswith("erpnext_enhancements."),
                            f"{doctype}.{event} -> {name}",
                        )
                        self.assertGreaterEqual(name.count("."), 2, name)

    def test_the_previously_lost_handlers_are_registered(self):
        """Regression guard, named. These six were silently dead in production."""
        events = ast.literal_eval(top_level_assignments()["doc_events"])

        def flat(doctype, event):
            target = events.get(doctype, {}).get(event, [])
            return target if isinstance(target, list) else [target]

        self.assertIn(
            "erpnext_enhancements.script_migrations.task.calculate_project_elapsed_time",
            flat("Task", "before_save"),
        )
        self.assertIn(
            "erpnext_enhancements.script_migrations.task.sync_task_to_google_calendar",
            flat("Task", "after_insert"),
        )
        self.assertIn("erpnext_enhancements.tasks.generate_next_task", flat("Task", "on_update"))
        self.assertIn(
            "erpnext_enhancements.script_migrations.task.sync_project_dates_from_tasks",
            flat("Task", "on_update"),
        )
        self.assertIn(
            "erpnext_enhancements.script_migrations.task.sync_project_dates_from_tasks",
            flat("Task", "on_trash"),
        )
        self.assertIn(
            "erpnext_enhancements.api.maintenance_scheduling.update_next_visit_dates",
            flat("Sapphire Maintenance Record", "on_submit"),
        )
        # ...and the training hooks that displaced them are still registered too.
        self.assertIn(
            "erpnext_enhancements.training.compliance.warn_uncertified_assignee",
            flat("Task", "validate"),
        )
        self.assertIn(
            "erpnext_enhancements.training.compliance.warn_uncertified_technician",
            flat("Sapphire Maintenance Record", "validate"),
        )


if __name__ == "__main__":
    unittest.main()
