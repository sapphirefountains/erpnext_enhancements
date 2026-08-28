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


class ChatDocTypesAreReachableOnlyThroughAPermissionHook(unittest.TestCase):
    """A new chat DocType must fail this by default rather than ship unguarded.

    The rule, derived from the filesystem so it cannot be satisfied by remembering:

        a chat DocType either grants **no role `read`** — unreachable by role, which is the
        posture nearly all of them take — or it is registered in **both**
        `permission_query_conditions` and `has_permission`.

    **Keyed on `read`, not on "has any DocPerm row",** and the distinction is load-bearing
    rather than a loophole. Both hooks gate row access and nothing else: the query-conditions
    hook narrows a `get_list`, the single-document hook answers "may this person read *this
    row*". A DocPerm that grants `report` alone — `Triton Invocation Log` is the one — makes no
    row reachable through either path, because the list view, the form view and
    `/api/resource` all check `read`. Requiring hooks there would mean writing two functions
    that can never be consulted, and a hook nobody calls is worse than no hook: it reads as
    protection.

    What `report` alone *does* buy is the ability to run a report over the doctype, including
    one somebody authors themselves. That is a real widening and it is why the grant was a
    decision rather than a detail — but it is a decision about **reports**, and a permission
    hook is not the control for it. The control is the Report's own `roles` child table.

    Both, because they cover different paths and passing one proves nothing about the other:
    the query-conditions hook filters `get_list` and report reads, while the single-document
    hook covers everything else. The single-document hook is also the **realtime** boundary —
    `doc_subscribe` runs the full document permission stack before joining a document room, so
    a DocType with a DocPerm and no hook leaks over the socket even with every REST endpoint
    locked down.

    Child tables and Singles are exempt, and for the same reason rather than as a convenience:
    neither has rows to filter. A child row's access is its parent's, and a Single is one row
    whose DocPerm *is* the access decision. `Chat Settings` is the Single this exempts, and its
    one DocPerm row is System Manager.

    The pattern — glob the doctype directory rather than list the names — is the one already
    proven by `test_chat_mcp_denylist.py`. Enumerating names in the test is the failure mode
    the test exists to prevent: it passes forever and covers whatever was true when it was
    written.
    """

    CHAT_DOCTYPE_DIR = APP_ROOT / "chat" / "doctype"

    def _registers(self):
        assignments = top_level_assignments()
        out = {}
        for key in ("permission_query_conditions", "has_permission"):
            node = assignments[key]
            self.assertIsInstance(
                node,
                ast.Dict,
                f"{key} is no longer a dict literal in hooks.py; this guard reads it "
                "statically and would otherwise silently cover nothing",
            )
            out[key] = {k.value for k in node.keys if isinstance(k, ast.Constant)}
        return out

    def _chat_doctypes(self):
        import json

        for path in sorted(self.CHAT_DOCTYPE_DIR.glob("*/*.json")):
            # `<dir>/<dir>.json` is the DocType definition; siblings are test fixtures.
            if path.parent.name != path.stem:
                continue
            yield json.loads(path.read_text(encoding="utf-8"))

    def test_a_chat_doctype_granting_read_is_registered_in_both_registers(self):
        registers = self._registers()
        unguarded = []
        for definition in self._chat_doctypes():
            name = definition.get("name")
            if definition.get("istable") or definition.get("issingle"):
                continue
            grants_read = any(p.get("read") for p in (definition.get("permissions") or []))
            if not grants_read:
                continue
            missing = [key for key, names in registers.items() if name not in names]
            if missing:
                unguarded.append(f"{name} (missing from {', '.join(sorted(missing))})")

        self.assertEqual(
            unguarded,
            [],
            "a chat DocType grants a role `read` but has no row-level hook. Either give it "
            "a permission_query_conditions AND a has_permission entry in hooks.py, or drop the "
            "read grant so no role can reach its rows at all. A readable DocType without a hook "
            "is exposed over the realtime socket as well as over REST.",
        )

    def test_the_two_registers_agree_on_chat(self):
        """House doctrine is parity. A DocType in one register and not the other is the bug
        shape that passes every list-view test and leaks a single-document read."""
        registers = self._registers()
        chat = {
            definition.get("name")
            for definition in self._chat_doctypes()
            if not (definition.get("istable") or definition.get("issingle"))
        }
        in_query = registers["permission_query_conditions"] & chat
        in_single = registers["has_permission"] & chat
        self.assertEqual(
            sorted(in_query),
            sorted(in_single),
            "permission_query_conditions and has_permission disagree about which chat "
            "DocTypes they cover; the two hooks protect different paths and must be in step",
        )


if __name__ == "__main__":
    unittest.main()
