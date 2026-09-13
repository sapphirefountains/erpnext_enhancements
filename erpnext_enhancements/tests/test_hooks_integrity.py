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


#: Server-side document events Frappe actually dispatches via ``run_method`` (v16). NOTABLY
#: ABSENT: ``after_save``, which is a CLIENT-side (form) event only — a ``doc_event`` registered
#: under it resolves to a real function but is silently never invoked. That dead-hook class
#: shipped more than once (global Triton sync, Opportunity→Project attachment sync) and survived
#: every CI run, because the *path* checks below pass; only the event *name* was wrong.
DISPATCHED_DOC_EVENTS = frozenset(
    {
        "before_insert",
        "after_insert",
        "before_naming",
        "autoname",
        "before_validate",
        "validate",
        "before_save",
        "on_update",
        "before_submit",
        "on_submit",
        "before_cancel",
        "on_cancel",
        "before_update_after_submit",
        "on_update_after_submit",
        "on_change",
        "before_rename",
        "after_rename",
        "before_delete",
        "on_trash",
        "after_delete",
        "before_print",
    }
)


class TestHandlersLookReal(unittest.TestCase):
    def test_doc_event_names_are_dispatched_by_frappe(self):
        """The dead-``after_save`` class: a doc_event under a name Frappe never dispatches
        server-side resolves fine but never fires. Assert every registered event name is one
        Frappe actually runs, so a mistyped or client-only name fails the build."""
        events = ast.literal_eval(top_level_assignments()["doc_events"])
        for doctype, handlers in events.items():
            if not isinstance(handlers, dict):
                continue
            for event in handlers:
                with self.subTest(f"{doctype}.{event}"):
                    self.assertIn(
                        event,
                        DISPATCHED_DOC_EVENTS,
                        f"{doctype!r} registers a handler on {event!r}, which Frappe does not "
                        f"dispatch server-side — the handler would never fire.",
                    )

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
        # The shared-calendar sync (Task after_insert) was removed in v1.346.0: it
        # broadcast every task to one calendar with no per-person filtering. Assert
        # the absence so a stale merge cannot quietly bring the broadcast back.
        self.assertNotIn(
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


class TestDoctypeJsDoesNotDoubleLoad(unittest.TestCase):
    """A `doctype_js` entry pointing at one of this app's OWN doctype-folder scripts loads it twice.

    `FormMeta.add_code` (frappe/desk/form/meta.py, v16) always reads
    `<module>/doctype/<name>/<name>.js` for a DocType this app owns, then appends every
    `doctype_js` entry to the SAME `__js` string with no dedupe, and the client evaluates the
    whole thing as one `new Function(...)` body. Duplicate `function` declarations survive that;
    a duplicate top-level `const` / `let` / `class` is a SyntaxError, and the form then loads
    with no custom buttons at all -- which is how the Plaid settings form shipped, twice.
    The hook is for scripts under `public/` and for scripts bound to erpnext's / frappe's
    DocTypes (those folders hold no DocType JSON of ours, so Frappe does not auto-load them).
    """

    def test_no_entry_points_at_an_app_owned_doctypes_own_script(self):
        doctype_js = ast.literal_eval(top_level_assignments()["doctype_js"])
        offenders = []
        for doctype, files in doctype_js.items():
            for rel in files if isinstance(files, list) else [files]:
                parts = rel.strip("/").split("/")
                if len(parts) != 4 or parts[1] != "doctype" or parts[3] != f"{parts[2]}.js":
                    continue
                if (APP_ROOT / parts[0] / "doctype" / parts[2] / f"{parts[2]}.json").exists():
                    offenders.append(f"{doctype}: {rel}")
        self.assertEqual(
            offenders,
            [],
            "doctype_js lists a script Frappe already auto-loads for an app-owned DocType; it "
            "would be evaluated twice in one Function body and any top-level const/let/class "
            "declaration in it becomes a SyntaxError that disables the whole form:\n  "
            + "\n  ".join(offenders),
        )


class TestLogRetention(unittest.TestCase):
    """`default_log_clearing_doctypes` is one line whose absence is invisible.

    `tabNotification Log` grew to 9,717 rows over thirteen months because nothing declared
    it — not this app, not Frappe, not ERPNext. There is no error for an unregistered log
    table; there is only a table that never stops growing, discovered when somebody looks.
    """

    def test_notification_log_is_registered_for_retention(self):
        retention = ast.literal_eval(top_level_assignments()["default_log_clearing_doctypes"])
        self.assertIn(
            "Notification Log",
            retention,
            "tabNotification Log is registered for retention in neither frappe's hooks nor "
            "ERPNext's (both checked), so removing it here means it grows forever again. It "
            "was at 9,717 rows / 13 months when this was added.",
        )

    def test_the_retention_value_is_the_one_that_was_argued(self):
        """Pinned, because the number can only be chosen once.

        `LogSettings.add_default_logtypes` APPENDS rows that are absent and never updates one
        that exists, so editing this value later changes nothing on a site that has already
        run a daily maintenance pass — the only remedy is editing `Logs To Clear` by hand.
        Error Log is the live proof: its hook value is 14 and its row on production says 90.

        90 matches all fifteen rows already on the site. Changing it here is therefore a
        decision about NEW sites only, and this test is where you notice that.
        """
        retention = ast.literal_eval(top_level_assignments()["default_log_clearing_doctypes"])
        self.assertEqual(retention["Notification Log"], 90)

    def test_every_retained_doctype_is_one_we_verified_supports_clearing(self):
        """`remove_unsupported_doctypes()` runs FIRST in `run_log_clean_up` and DELETES the
        `Logs To Clear` row of any doctype whose controller has no `clear_old_logs(days)`.

        So registering a doctype that does not implement it is worse than not registering it:
        the row is created, silently removed on the next daily run, and retention never
        happens — with the hook sitting in the file looking like it works. This list is what
        has actually been checked against the deployed build, and adding a name to the hook
        without adding it here is the reminder to go and check.
        """
        verified = {
            # frappe/desk/doctype/notification_log/notification_log.py — confirmed on the
            # deployed v16 build 2026-08-11: `clear_old_logs(days)` exists.
            "Notification Log",
        }
        retention = ast.literal_eval(top_level_assignments()["default_log_clearing_doctypes"])
        self.assertEqual(
            set(retention) - verified,
            set(),
            "a doctype was registered for retention without confirming its controller "
            "implements clear_old_logs(days); Frappe will delete its Logs To Clear row on the "
            "next daily run and retention will silently never happen",
        )

if __name__ == "__main__":
    unittest.main()
