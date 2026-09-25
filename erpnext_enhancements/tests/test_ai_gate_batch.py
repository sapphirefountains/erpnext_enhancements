"""Bench-free tests: deciding several AI Pending Actions at once (v1.528.0).

With the write gate on, an assistant working through a list leaves one card per write, and
each card had to be opened and confirmed on its own. ``gating_api`` now has a batch path:
``my_pending_actions`` lists the session user's own queue, and ``confirm_actions`` /
``cancel_actions`` decide up to ``MAX_BATCH`` of them in one request. These tests pin the
parts of it that are about safety rather than convenience:

- ``confirm_action`` is now a wrapper around ``_confirm_one``, and the batch goes through the
  very same function (the sealed-arguments suite still exercises it through the wrapper);
- a batch only covers the caller's own actions, **even for a System Manager**, and reads out
  nothing about anyone else's;
- expired and non-Pending actions are skipped with the reason, never decided;
- one failure is rolled back and recorded and the batch carries on, with messages muted so it
  does not end in a stack of modals;
- actions run oldest first; more than 50 is refused; a JSON string is accepted;
- ``my_pending_actions`` returns only the caller's own unexpired Pending rows, never decrypts
  or returns the sealed values, and never ticks a High-risk or hidden-value row by default;
- ``cancel_actions`` follows the same rules;
- all six endpoints in ``gating_api`` are POST-only (a GET link to ``confirm_action`` used to
  run an action on one click), none of it is reachable from the MCP, and every one of them
  refuses a request authenticated by an ``Authorization`` header, so an OAuth client holding
  the user's token can never decide its own proposals;
- a batch request starts nothing new after its time budget, and the rest come back Skipped;
- an oversized ``names`` is refused before the loop looks at it, and the loop stops at the
  first name past the cap;
- ``batch_default`` is also false, with a ``review_reason``, for a submit or cancel, a write to
  one of ``NEVER_EXEMPT`` and unreadable arguments, and the arguments are never returned. The
  reason names the kind of never-exempt target: a Task, the gate's own records, or (v1.538.0)
  the company knowledge base.

Plain ``unittest`` under the stub set ``test_assistant_tools_schema.install_stubs`` provides,
like the sibling gate suites. ``frappe.whitelist`` is installed once for the module, because
``gating_api`` needs it at import time; everything else is patched per test. Every test runs
inside a fake web request with no ``Authorization`` header, which is what the desk sends, and
against a fake clock that only moves when a test moves it.

Run: python -m unittest erpnext_enhancements.tests.test_ai_gate_batch -v
"""

import ast
import json
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_gate = None
gating_api = None

NOW = datetime(2026, 9, 23, 12, 0, 0)
ME = "nik@x"
SOMEONE_ELSE = "jordan@x"


def setUpModule():
    global _gate, gating_api
    from erpnext_enhancements.tests.test_assistant_tools_schema import install_stubs

    install_stubs()
    import frappe

    if not hasattr(frappe, "whitelist"):
        frappe.whitelist = lambda *a, **k: (lambda f: f)

    from erpnext_enhancements.assistant_tools import _gate as gate_module
    from erpnext_enhancements.assistant_tools import gating_api as api_module

    _gate = gate_module
    gating_api = api_module


def as_datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


class FakeThrow(Exception):
    """What frappe.throw raises here. Stands in for frappe.ValidationError too.

    ``exc`` is the exception class the code passed to frappe.throw, if any.
    """

    exc = None


class FakeAction:
    """An AI Pending Action row, as get_doc and get_all both see it."""

    def __init__(
        self,
        name,
        *,
        minutes_old=10,
        requested_by=ME,
        status="Pending",
        expires_in_minutes=50,
        risk="Low",
        tool_name="create_document",
        summary=None,
        arguments=None,
        sealed_arguments=None,
        password=None,
        target_doctype="ToDo",
    ):
        self.name = name
        self.requested_by = requested_by
        self.status = status
        self.creation = NOW - timedelta(minutes=minutes_old)
        self.expires_at = None if expires_in_minutes is None else NOW + timedelta(minutes=expires_in_minutes)
        self.risk = risk
        self.tool_name = tool_name
        self.summary = summary if summary is not None else f"Create ToDo for {name}"
        self.target_doctype = target_doctype
        self.target_name = None
        self.arguments = arguments if arguments is not None else json.dumps(
            {"doctype": "ToDo", "data": {"description": name}}
        )
        self.sealed_arguments = sealed_arguments
        self._password = password
        self.result = None
        self.error = None
        self.action_log = None
        self.decided_by = None
        self.decided_at = None
        self.fail_on_save = False
        self.password_reads = 0

    def get(self, key):
        return getattr(self, key, None)

    def set(self, key, value):
        setattr(self, key, value)

    def get_password(self, fieldname, raise_exception=True):
        self.password_reads += 1
        return self._password

    def save(self, ignore_permissions=False):
        if self.fail_on_save:
            raise ValueError(f"could not save {self.name}")

    def add_comment(self, comment_type, text):
        pass


class BatchHarness(unittest.TestCase):
    """The real gating_api against a fake table, a recording registry and a throw that raises."""

    def setUp(self):
        import frappe

        self.store = {}
        self.events = []
        self.executed = []
        self.logs = []
        self.error_logs = []
        self.get_all_calls = []
        self.get_doc_calls = []
        self.muted_during_execute = []
        # What the request carries. The desk sends no Authorization header.
        self.headers = {}
        # The batch's clock (gating_api.monotonic). It stands still unless a tool takes time.
        self.clock = 0.0
        self.seconds_per_tool = 0.0
        test = self

        class Registry:
            def execute_tool(self, tool_name, arguments):
                test.muted_during_execute.append(frappe.flags.mute_messages)
                test.executed.append((tool_name, (arguments.get("data") or {}).get("description")))
                test.clock += test.seconds_per_tool
                if (arguments.get("data") or {}).get("fail"):
                    raise ValueError("the tool refused")
                return {"success": True, "name": "TODO-1"}

        registry_module = types.ModuleType("frappe_assistant_core.core.tool_registry")
        registry_module.get_tool_registry = lambda: Registry()

        def throw(message, exc=None, *a, **k):
            error = FakeThrow(message)
            error.exc = exc
            raise error

        def get_doc(doctype, name):
            test.get_doc_calls.append(name)
            if name not in test.store:
                raise FakeThrow(f"AI Pending Action {name} not found")
            return test.store[name]

        db = types.SimpleNamespace(
            commit=lambda: test.events.append("commit"),
            rollback=lambda: test.events.append("rollback"),
        )
        self.flags = types.SimpleNamespace(
            ai_gate_bypass=False,
            ai_gate_pending=None,
            ai_gate_sealed=None,
            ai_action_transition=False,
            mute_messages=False,
        )
        patches = [
            mock.patch.dict(sys.modules, {"frappe_assistant_core.core.tool_registry": registry_module}),
            mock.patch.object(frappe, "session", types.SimpleNamespace(user=ME), create=True),
            # Every test runs as a System Manager: that role must not widen a batch.
            mock.patch.object(frappe, "get_roles", lambda *a: ["System Manager"], create=True),
            mock.patch.object(frappe, "get_doc", get_doc, create=True),
            mock.patch.object(frappe, "get_all", self._get_all, create=True),
            mock.patch.object(frappe, "throw", throw, create=True),
            mock.patch.object(frappe, "ValidationError", FakeThrow, create=True),
            mock.patch.object(frappe, "PermissionError", PermissionError, create=True),
            mock.patch.object(frappe, "db", db, create=True),
            mock.patch.object(frappe, "flags", self.flags, create=True),
            mock.patch.object(frappe, "log_error", lambda **kw: test.error_logs.append(kw), create=True),
            # Inside a web request, as the desk's calls are.
            mock.patch.object(frappe, "local", types.SimpleNamespace(request=object()), create=True),
            mock.patch.object(
                frappe,
                "get_request_header",
                lambda key, default=None: test.headers.get(key, default),
                create=True,
            ),
            mock.patch.object(
                gating_api, "insert_action_log", lambda **kw: test.logs.append(kw) or "AI-LOG-1"
            ),
            mock.patch.object(gating_api, "now_datetime", lambda: NOW),
            mock.patch.object(gating_api, "get_datetime", as_datetime),
            mock.patch.object(gating_api, "monotonic", lambda: test.clock),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def add(self, *actions):
        for action in actions:
            self.store[action.name] = action

    def _get_all(
        self, doctype, filters=None, or_filters=None, fields=None, order_by=None, limit=None, pluck=None
    ):
        self.assertEqual(doctype, "AI Pending Action")
        self.get_all_calls.append(
            {
                "filters": filters,
                "or_filters": or_filters,
                "fields": fields,
                "order_by": order_by,
                "limit": limit,
                "pluck": pluck,
            }
        )

        def matches(action, key, condition):
            value = getattr(action, key, None)
            if isinstance(condition, (list, tuple)):
                operator, argument = condition
                if operator == "in":
                    return value in argument
                if operator == "is":
                    return bool(value) if argument == "set" else not value
                if operator == ">":
                    return value is not None and value > argument
                raise AssertionError(f"unexpected operator {operator!r}")
            return value == condition

        # Reversed, so storage order is never creation order and sorting has to happen somewhere.
        rows = list(self.store.values())[::-1]
        rows = [a for a in rows if all(matches(a, k, c) for k, c in (filters or {}).items())]
        if or_filters:
            rows = [a for a in rows if any(matches(a, f[0], (f[1], f[2])) for f in or_filters)]
        if order_by == "creation asc":
            rows.sort(key=lambda a: a.creation)
        elif order_by:
            raise AssertionError(f"unexpected order_by {order_by!r}")
        if limit:
            rows = rows[:limit]
        if pluck:
            return [getattr(a, pluck) for a in rows]
        return [{field: getattr(a, field, None) for field in fields} for a in rows]

    def by_name(self, response):
        return {item["name"]: item for item in response["results"]}


class TestConfirmActionIsAWrapper(BatchHarness):
    def test_confirm_action_returns_what_confirm_one_returns(self):
        seen = []

        def fake(name):
            seen.append(name)
            return {"status": "sentinel"}

        with mock.patch.object(gating_api, "_confirm_one", fake):
            self.assertEqual(gating_api.confirm_action("AI-PA-1"), {"status": "sentinel"})
        self.assertEqual(seen, ["AI-PA-1"])

    def test_confirm_action_raises_what_confirm_one_raises(self):
        def refuse(name):
            raise FakeThrow("refused")

        with mock.patch.object(gating_api, "_confirm_one", refuse):
            with self.assertRaises(FakeThrow):
                gating_api.confirm_action("AI-PA-1")

    def test_the_real_path_still_executes_through_the_wrapper(self):
        self.add(FakeAction("AI-PA-1"))
        self.assertEqual(gating_api.confirm_action("AI-PA-1")["status"], "Executed")
        self.assertEqual(self.store["AI-PA-1"].status, "Executed")
        # The single path is not muted; only the batch mutes.
        self.assertEqual(self.muted_during_execute, [False])

    def test_cancel_action_is_a_wrapper_too(self):
        self.add(FakeAction("AI-PA-1"))
        self.assertEqual(gating_api.cancel_action("AI-PA-1"), {"status": "Cancelled"})
        self.assertEqual(self.store["AI-PA-1"].status, "Cancelled")


class TestConfirmActionsOwnership(BatchHarness):
    def test_another_users_action_is_skipped_even_for_a_system_manager(self):
        self.add(FakeAction("AI-PA-1"), FakeAction("AI-PA-2", requested_by=SOMEONE_ELSE))
        response = gating_api.confirm_actions(["AI-PA-1", "AI-PA-2"])
        items = self.by_name(response)
        self.assertEqual(items["AI-PA-1"]["status"], "Executed")
        self.assertEqual(items["AI-PA-2"]["status"], "Skipped")
        self.assertIn("not an action the AI proposed for you", items["AI-PA-2"]["message"])
        self.assertEqual(self.store["AI-PA-2"].status, "Pending")
        self.assertNotIn("AI-PA-2", self.get_doc_calls)
        self.assertEqual(self.executed, [("create_document", "AI-PA-1")])
        self.assertEqual((response["executed"], response["failed"], response["skipped"]), (1, 0, 1))

    def test_nothing_about_another_users_action_is_read_out(self):
        self.add(FakeAction("AI-PA-2", requested_by=SOMEONE_ELSE, summary="Delete Customer ACME"))
        (item,) = gating_api.confirm_actions(["AI-PA-2"])["results"]
        self.assertEqual((item["tool_name"], item["summary"]), ("", ""))

    def test_a_missing_action_reads_the_same_as_someone_elses(self):
        self.add(FakeAction("AI-PA-2", requested_by=SOMEONE_ELSE))
        items = self.by_name(gating_api.confirm_actions(["AI-PA-2", "AI-PA-404"]))
        self.assertEqual(items["AI-PA-404"]["status"], "Skipped")
        self.assertEqual(items["AI-PA-404"]["message"], items["AI-PA-2"]["message"])


class TestConfirmActionsSkips(BatchHarness):
    def test_expired_and_decided_actions_are_skipped_and_left_alone(self):
        self.add(
            FakeAction("AI-PA-1", expires_in_minutes=-5),
            FakeAction("AI-PA-2", status="Executed"),
            FakeAction("AI-PA-3", status="Cancelled"),
            FakeAction("AI-PA-4"),
        )
        response = gating_api.confirm_actions(["AI-PA-1", "AI-PA-2", "AI-PA-3", "AI-PA-4"])
        items = self.by_name(response)
        self.assertEqual(items["AI-PA-1"]["status"], "Skipped")
        self.assertIn("expired", items["AI-PA-1"]["message"])
        self.assertEqual(self.store["AI-PA-1"].status, "Pending")  # the sweep flips it, not the batch
        self.assertIn("already Executed", items["AI-PA-2"]["message"])
        self.assertIn("already Cancelled", items["AI-PA-3"]["message"])
        self.assertEqual(items["AI-PA-4"]["status"], "Executed")
        self.assertEqual(self.executed, [("create_document", "AI-PA-4")])
        self.assertEqual((response["executed"], response["failed"], response["skipped"]), (1, 0, 3))

    def test_an_action_with_no_expiry_is_not_expired(self):
        self.add(FakeAction("AI-PA-1", expires_in_minutes=None))
        (item,) = gating_api.confirm_actions(["AI-PA-1"])["results"]
        self.assertEqual(item["status"], "Executed")


class TestConfirmActionsFailures(BatchHarness):
    def test_one_failed_execution_does_not_stop_the_rest(self):
        failing = json.dumps({"doctype": "ToDo", "data": {"description": "AI-PA-2", "fail": True}})
        self.add(
            FakeAction("AI-PA-1", minutes_old=30),
            FakeAction("AI-PA-2", minutes_old=20, arguments=failing),
            FakeAction("AI-PA-3", minutes_old=10),
        )
        response = gating_api.confirm_actions(["AI-PA-1", "AI-PA-2", "AI-PA-3"])
        items = self.by_name(response)
        self.assertEqual([i["status"] for i in response["results"]], ["Executed", "Failed", "Executed"])
        self.assertIn("the tool refused", items["AI-PA-2"]["message"])
        self.assertEqual(self.store["AI-PA-2"].status, "Failed")  # recorded on the action, as from the form
        self.assertEqual(self.store["AI-PA-3"].status, "Executed")
        self.assertEqual((response["executed"], response["failed"], response["skipped"]), (2, 1, 0))
        # _confirm_one rolls back the partial write, commits Failed and raises; then the batch rolls
        # back, and the next action commits its own Confirmed.
        after = self.events.index("rollback") + 1
        self.assertEqual(self.events[after : after + 3], ["commit", "rollback", "commit"])
        # An ordinary refusal is on the action already, so it is not also an Error Log.
        self.assertEqual(self.error_logs, [])

    def test_an_unexpected_error_is_rolled_back_logged_without_a_traceback_and_passed_over(self):
        def decide(name):
            if name == "AI-PA-1":
                raise TypeError("'NoneType' object is not subscriptable")
            return {"status": "Executed", "action_log": "AI-LOG-9"}

        self.add(FakeAction("AI-PA-1", minutes_old=30), FakeAction("AI-PA-2", minutes_old=10))
        with mock.patch.object(gating_api, "_confirm_one", decide):
            response = gating_api.confirm_actions(["AI-PA-1", "AI-PA-2"])
        items = self.by_name(response)
        self.assertEqual(items["AI-PA-1"]["status"], "Failed")
        self.assertIn("not subscriptable", items["AI-PA-1"]["message"])
        self.assertEqual(items["AI-PA-2"]["status"], "Executed")
        self.assertIn("AI-LOG-9", items["AI-PA-2"]["message"])
        self.assertEqual(self.events[:2], ["rollback", "commit"])  # rollback, then keep the Error Log
        (log,) = self.error_logs
        self.assertEqual(log["reference_name"], "AI-PA-1")
        # An explicit message, so Frappe does not capture a traceback with frame locals.
        self.assertTrue(log["message"].startswith("TypeError: "))

    def test_messages_are_muted_for_each_decision_and_restored(self):
        failing = json.dumps({"doctype": "ToDo", "data": {"description": "AI-PA-1", "fail": True}})
        self.add(
            FakeAction("AI-PA-1", minutes_old=30, arguments=failing),
            FakeAction("AI-PA-2", minutes_old=10),
        )
        gating_api.confirm_actions(["AI-PA-1", "AI-PA-2"])
        self.assertEqual(self.muted_during_execute, [True, True])
        self.assertFalse(self.flags.mute_messages)

    def test_a_caller_that_was_already_muted_stays_muted(self):
        self.flags.mute_messages = True
        self.add(FakeAction("AI-PA-1"))
        gating_api.confirm_actions(["AI-PA-1"])
        self.assertTrue(self.flags.mute_messages)


class TestConfirmActionsOrderAndShape(BatchHarness):
    def test_actions_run_oldest_first_whatever_order_they_were_sent_in(self):
        self.add(
            FakeAction("AI-PA-3", minutes_old=5),
            FakeAction("AI-PA-1", minutes_old=30),
            FakeAction("AI-PA-2", minutes_old=20),
        )
        response = gating_api.confirm_actions(["AI-PA-3", "AI-PA-404", "AI-PA-1", "AI-PA-2"])
        self.assertEqual([d for _t, d in self.executed], ["AI-PA-1", "AI-PA-2", "AI-PA-3"])
        # The ones not found go last, in the order they were sent.
        self.assertEqual(
            [i["name"] for i in response["results"]], ["AI-PA-1", "AI-PA-2", "AI-PA-3", "AI-PA-404"]
        )

    def test_a_json_string_is_parsed_and_duplicates_and_blanks_dropped(self):
        self.add(FakeAction("AI-PA-1"))
        response = gating_api.confirm_actions(json.dumps(["AI-PA-1", " AI-PA-1 ", ""]))
        self.assertEqual([i["name"] for i in response["results"]], ["AI-PA-1"])
        self.assertEqual(len(self.executed), 1)

    def test_a_tuple_is_accepted(self):
        self.add(FakeAction("AI-PA-1"))
        self.assertEqual(gating_api.confirm_actions(("AI-PA-1",))["executed"], 1)

    def test_anything_that_is_not_a_list_of_names_is_refused(self):
        for bad in ("AI-PA-1", json.dumps("AI-PA-1"), json.dumps({"name": "AI-PA-1"}), [1, 2], None):
            with self.subTest(names=bad):
                with self.assertRaises(FakeThrow):
                    gating_api.confirm_actions(bad)
        self.assertEqual(self.get_all_calls, [])

    def test_more_than_fifty_is_refused_before_anything_is_read(self):
        self.assertEqual(gating_api.MAX_BATCH, 50)
        names = [f"AI-PA-{i}" for i in range(51)]
        with self.assertRaises(FakeThrow) as raised:
            gating_api.confirm_actions(names)
        self.assertIn("At most 50", str(raised.exception))
        self.assertEqual(self.get_all_calls, [])
        self.assertEqual(self.get_doc_calls, [])

    def test_fifty_is_allowed_and_the_cap_counts_unique_names(self):
        names = [f"AI-PA-{i}" for i in range(50)]
        response = gating_api.confirm_actions(names + names[:5])
        self.assertEqual(response["skipped"], 50)

    def test_the_summary_is_cut_to_two_hundred_characters(self):
        self.add(FakeAction("AI-PA-1", summary="x" * 500))
        (item,) = gating_api.confirm_actions(["AI-PA-1"])["results"]
        self.assertEqual(len(item["summary"]), 200)
        self.assertEqual(item["tool_name"], "create_document")

    def test_the_response_shape(self):
        self.add(FakeAction("AI-PA-1"))
        response = gating_api.confirm_actions(["AI-PA-1"])
        self.assertEqual(set(response), {"results", "executed", "failed", "skipped", "budget_reached"})
        self.assertEqual(set(response["results"][0]), {"name", "tool_name", "summary", "status", "message"})

    def test_an_empty_list_decides_nothing(self):
        self.assertEqual(
            gating_api.confirm_actions([]),
            {"results": [], "executed": 0, "failed": 0, "skipped": 0, "budget_reached": False},
        )


class NoPeeking(list):
    """A list that may be measured but not walked."""

    def __iter__(self):
        raise AssertionError("the names were iterated")


class Counting(list):
    """A list that counts how many entries were taken from it."""

    taken = 0

    def __iter__(self):
        for entry in super().__iter__():
            self.taken += 1
            yield entry


class TestOversizedNames(BatchHarness):
    """Any signed-in user can POST a request body of millions of names."""

    def test_more_than_four_times_the_cap_is_refused_before_the_loop_looks_at_it(self):
        names = NoPeeking(["AI-PA-1"] * (gating_api.MAX_BATCH * 4 + 1))
        with self.assertRaises(FakeThrow) as raised:
            gating_api.confirm_actions(names)
        self.assertIn("At most 50", str(raised.exception))
        self.assertIn("201 names were sent", str(raised.exception))
        self.assertEqual((self.get_all_calls, self.get_doc_calls), ([], []))

    def test_the_same_limit_applies_to_a_json_string(self):
        with self.assertRaises(FakeThrow):
            gating_api.cancel_actions(json.dumps(["AI-PA-1"] * 201))
        self.assertEqual(self.get_all_calls, [])

    def test_four_times_the_cap_of_repeats_is_still_one_name(self):
        self.add(FakeAction("AI-PA-1"))
        response = gating_api.confirm_actions(["AI-PA-1"] * 200)
        self.assertEqual([i["name"] for i in response["results"]], ["AI-PA-1"])
        self.assertEqual(len(self.executed), 1)

    def test_the_loop_stops_at_the_first_name_past_the_cap(self):
        names = Counting(f"AI-PA-{i}" for i in range(150))
        with self.assertRaises(FakeThrow) as raised:
            gating_api.confirm_actions(names)
        self.assertEqual(names.taken, gating_api.MAX_BATCH + 1)
        self.assertIn("At most 50", str(raised.exception))
        self.assertEqual(self.get_all_calls, [])

    def test_repeats_do_not_count_toward_the_cap(self):
        names = [f"AI-PA-{i}" for i in range(50)]
        interleaved = [name for pair in zip(names, names, strict=True) for name in pair]
        self.assertEqual(gating_api.confirm_actions(interleaved)["skipped"], 50)


class TestMyPendingActions(BatchHarness):
    def setUp(self):
        super().setUp()
        import frappe

        def no_get_doc(*a, **k):
            raise AssertionError("my_pending_actions must not load documents (that is the decrypt path)")

        patcher = mock.patch.object(frappe, "get_doc", no_get_doc, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        sealed = "*" * 40  # what the column holds; the real value is in __Auth
        self.add(
            FakeAction("AI-PA-low", minutes_old=40),
            FakeAction("AI-PA-high", minutes_old=30, risk="High", tool_name="delete_document"),
            FakeAction("AI-PA-hidden", minutes_old=20, sealed_arguments=sealed, password="dt_abcdef123456"),
            FakeAction("AI-PA-forever", minutes_old=10, expires_in_minutes=None),
            FakeAction("AI-PA-expired", minutes_old=90, expires_in_minutes=-1),
            FakeAction("AI-PA-done", minutes_old=50, status="Executed"),
            FakeAction("AI-PA-theirs", minutes_old=60, requested_by=SOMEONE_ELSE),
        )

    def test_only_my_own_unexpired_pending_rows_oldest_first(self):
        rows = gating_api.my_pending_actions()
        self.assertEqual(
            [r["name"] for r in rows], ["AI-PA-low", "AI-PA-high", "AI-PA-hidden", "AI-PA-forever"]
        )

    def test_the_query_itself_is_scoped_to_the_session_user(self):
        gating_api.my_pending_actions()
        first = self.get_all_calls[0]
        self.assertEqual(first["filters"], {"requested_by": ME, "status": "Pending"})
        self.assertEqual(first["or_filters"], [["expires_at", "is", "not set"], ["expires_at", ">", NOW]])
        self.assertEqual((first["order_by"], first["limit"]), ("creation asc", 100))

    def test_batch_default_is_false_for_high_risk_or_hidden_values(self):
        rows = {r["name"]: r for r in gating_api.my_pending_actions()}
        self.assertTrue(rows["AI-PA-low"]["batch_default"])
        self.assertTrue(rows["AI-PA-forever"]["batch_default"])
        self.assertFalse(rows["AI-PA-high"]["batch_default"])
        self.assertFalse(rows["AI-PA-hidden"]["batch_default"])
        self.assertTrue(rows["AI-PA-hidden"]["has_hidden"])
        self.assertFalse(rows["AI-PA-low"]["has_hidden"])
        self.assertEqual(rows["AI-PA-high"]["review_reason"], "high risk")
        self.assertEqual(rows["AI-PA-hidden"]["review_reason"], "has hidden values")
        self.assertEqual(rows["AI-PA-low"]["review_reason"], "")

    def test_the_sealed_value_is_never_read_decrypted_or_returned(self):
        rows = gating_api.my_pending_actions()
        self.assertEqual(self.store["AI-PA-hidden"].password_reads, 0)
        for call in self.get_all_calls:
            self.assertNotIn("sealed_arguments", call["fields"] or [])
        text = json.dumps(rows, default=str)
        self.assertNotIn("dt_abcdef123456", text)
        self.assertNotIn("*" * 40, text)
        for row in rows:
            self.assertNotIn("sealed_arguments", row)

    def test_ages_are_computed_on_the_server_in_one_clock(self):
        rows = {r["name"]: r for r in gating_api.my_pending_actions()}
        self.assertEqual(rows["AI-PA-low"]["age_seconds"], 40 * 60)
        self.assertEqual(rows["AI-PA-low"]["expires_in_seconds"], 50 * 60)
        self.assertIsNone(rows["AI-PA-forever"]["expires_in_seconds"])

    def test_nothing_pending_is_an_empty_list_and_one_query(self):
        self.store.clear()
        self.assertEqual(gating_api.my_pending_actions(), [])
        self.assertEqual(len(self.get_all_calls), 1)


class TestReviewReasons(BatchHarness):
    """Risk comes from the tool name alone, so a cancel dressed as an update reads as Medium.

    ``my_pending_actions`` asks the gate's own never-exempt questions of the redacted arguments
    and leaves those rows unticked, with the reason, without ever returning the arguments.
    """

    def rows(self, *actions):
        self.add(*actions)
        return {r["name"]: r for r in gating_api.my_pending_actions()}

    def update(self, name, data, doctype="Sales Invoice", **kwargs):
        arguments = json.dumps({"doctype": doctype, "name": "ACC-SINV-1", "data": data})
        kwargs.setdefault("risk", "Medium")
        return FakeAction(
            name,
            tool_name="update_document",
            arguments=arguments,
            target_doctype=doctype,
            summary=f"Update {doctype} ACC-SINV-1",
            **kwargs,
        )

    def test_a_plain_update_still_starts_ticked(self):
        rows = self.rows(self.update("AI-PA-1", {"remarks": "x"}))
        self.assertTrue(rows["AI-PA-1"]["batch_default"])
        self.assertEqual(rows["AI-PA-1"]["review_reason"], "")

    def test_an_update_that_sets_docstatus_is_a_submit_or_cancel(self):
        rows = self.rows(self.update("AI-PA-1", {"docstatus": 2}))
        self.assertFalse(rows["AI-PA-1"]["batch_default"])
        self.assertEqual(rows["AI-PA-1"]["review_reason"], "submits or cancels a document")

    def test_the_submit_and_cancel_tools_say_so_too(self):
        """v1.540.0: an update_document cancel is refused before it becomes a card, so a cancel
        now arrives as cancel_document. Its arguments carry no `docstatus`, so the reason has to
        come from the tool name, or the dialog would call it merely "high risk"."""
        for tool in ("submit_document", "cancel_document"):
            with self.subTest(tool=tool):
                self.store.clear()
                arguments = json.dumps({"doctype": "Material Request", "name": "MAT-MR-1", "reason": "x"})
                rows = self.rows(
                    FakeAction("AI-PA-1", tool_name=tool, arguments=arguments, target_doctype="Material Request", risk="High")
                )
                self.assertFalse(rows["AI-PA-1"]["batch_default"])
                self.assertEqual(rows["AI-PA-1"]["review_reason"], "high risk, submits or cancels a document")
        self.assertEqual(_gate.DOCSTATUS_TOOLS, frozenset({"submit_document", "cancel_document"}))

    def test_a_create_that_submits_is_a_submit(self):
        arguments = json.dumps({"doctype": "Sales Invoice", "data": {"customer": "C"}, "submit": True})
        rows = self.rows(FakeAction("AI-PA-1", arguments=arguments, target_doctype="Sales Invoice"))
        self.assertFalse(rows["AI-PA-1"]["batch_default"])
        self.assertEqual(rows["AI-PA-1"]["review_reason"], "submits or cancels a document")

    def test_a_write_to_the_gates_own_records_is_flagged(self):
        settings = "ERPNext Enhancements Settings"
        self.assertIn(settings, _gate.NEVER_EXEMPT)
        rows = self.rows(self.update("AI-PA-1", {"ai_write_gating_enabled": 0}, doctype=settings))
        self.assertFalse(rows["AI-PA-1"]["batch_default"])
        self.assertEqual(rows["AI-PA-1"]["review_reason"], "changes the AI gate's own records or settings")

    def test_a_task_card_says_it_is_a_task(self):
        """Task is never exempt for its own reason (ADR 0016 §6); it is not one of the gate's records."""
        arguments = json.dumps({"doctype": "Task", "data": {"subject": "Follow up"}})
        rows = self.rows(FakeAction("AI-PA-1", arguments=arguments, target_doctype="Task"))
        self.assertFalse(rows["AI-PA-1"]["batch_default"])
        self.assertEqual(rows["AI-PA-1"]["review_reason"], "creates or changes a Task")

    def test_a_knowledge_base_card_says_it_is_the_knowledge_base(self):
        """v1.538.0 put both KB doctypes in NEVER_EXEMPT, and the dialog called them the gate's own."""
        rows = self.rows(
            self.update("AI-PA-1", {"summary": "x"}, doctype="Knowledge Article"),
            self.update("AI-PA-2", {"summary": "x"}, doctype="Knowledge Article Version"),
        )
        for name in ("AI-PA-1", "AI-PA-2"):
            with self.subTest(action=name):
                self.assertFalse(rows[name]["batch_default"])
                self.assertEqual(rows[name]["review_reason"], "changes the company knowledge base")

    def test_the_three_kinds_make_up_never_exempt_and_do_not_overlap(self):
        kinds = ({"Task"}, _gate.GATE_OWN_DOCTYPES, _gate.KNOWLEDGE_BASE_DOCTYPES)
        self.assertEqual(frozenset().union(*kinds), _gate.NEVER_EXEMPT)
        self.assertEqual(sum(len(kind) for kind in kinds), len(_gate.NEVER_EXEMPT))

    def test_every_never_exempt_doctype_has_a_reason_of_its_own(self):
        """An entry added to NEVER_EXEMPT without a kind would fall through to the generic phrase,
        and one labelled as the gate's own records would say something false about it."""
        fallback = gating_api._never_exempt_reason("A Doctype Nobody Listed")
        own_records = gating_api._never_exempt_reason("AI Action Log")
        for doctype in sorted(_gate.NEVER_EXEMPT):
            with self.subTest(doctype=doctype):
                reason = gating_api._never_exempt_reason(doctype)
                self.assertNotEqual(reason, fallback)
                self.assertEqual(reason == own_records, doctype in _gate.GATE_OWN_DOCTYPES)

    def test_unreadable_arguments_need_a_look(self):
        rows = self.rows(
            FakeAction("AI-PA-1", minutes_old=20, arguments="{not json"),
            FakeAction("AI-PA-2", minutes_old=10, arguments=json.dumps(["a", "list"])),
        )
        for name in ("AI-PA-1", "AI-PA-2"):
            with self.subTest(action=name):
                self.assertFalse(rows[name]["batch_default"])
                self.assertEqual(rows[name]["review_reason"], "its arguments could not be read")

    def test_every_reason_that_applies_is_named(self):
        rows = self.rows(
            self.update("AI-PA-1", {"docstatus": 1}, risk="High", sealed_arguments="*" * 12)
        )
        self.assertEqual(rows["AI-PA-1"]["review_reason"], "high risk, has hidden values, submits or cancels a document")

    def test_the_arguments_are_read_but_never_returned(self):
        rows = self.rows(self.update("AI-PA-1", {"remarks": "a-marker-only-in-the-arguments"}))
        self.assertIn("arguments", self.get_all_calls[0]["fields"])
        self.assertNotIn("sealed_arguments", self.get_all_calls[0]["fields"])
        self.assertNotIn("arguments", rows["AI-PA-1"])
        self.assertNotIn("a-marker-only-in-the-arguments", json.dumps(rows, default=str))


class TestTimeBudget(BatchHarness):
    """A worker killed mid-tool leaves that action Confirmed forever, so a batch stops starting
    new ones in time. The clock is gating_api.monotonic, and here each tool takes 20 seconds."""

    def setUp(self):
        super().setUp()
        self.seconds_per_tool = 20.0

    def test_no_action_starts_after_the_budget_and_the_rest_come_back_skipped(self):
        self.assertEqual(gating_api.BATCH_TIME_BUDGET_SECONDS, 45)
        self.add(*(FakeAction(f"AI-PA-{i}", minutes_old=50 - i) for i in range(1, 6)))
        response = gating_api.confirm_actions([f"AI-PA-{i}" for i in range(1, 6)])
        # Started at 0s, 20s and 40s; the fourth would start at 60s.
        self.assertEqual([d for _t, d in self.executed], ["AI-PA-1", "AI-PA-2", "AI-PA-3"])
        self.assertEqual((response["executed"], response["failed"], response["skipped"]), (3, 0, 2))
        # The list view stops sending on this, so newer actions never run ahead of these two.
        self.assertIs(response["budget_reached"], True)
        items = self.by_name(response)
        for name in ("AI-PA-4", "AI-PA-5"):
            with self.subTest(action=name):
                self.assertEqual(items[name]["status"], "Skipped")
                self.assertIn("time budget", items[name]["message"])
                self.assertIn("run the batch again", items[name]["message"])
                self.assertEqual(self.store[name].status, "Pending")
                self.assertNotIn(name, self.get_doc_calls)
                # Your own action, so it still says what it is.
                self.assertEqual(items[name]["summary"], f"Create ToDo for {name}")

    def test_an_action_skipped_for_its_own_reason_keeps_that_reason(self):
        self.seconds_per_tool = 25.0  # started at 0s and 25s; the third would start at 50s
        self.add(
            FakeAction("AI-PA-1", minutes_old=40),
            FakeAction("AI-PA-2", minutes_old=30),
            FakeAction("AI-PA-3", minutes_old=20),
            FakeAction("AI-PA-4", minutes_old=10, requested_by=SOMEONE_ELSE),
            FakeAction("AI-PA-5", minutes_old=5, status="Executed"),
        )
        items = self.by_name(gating_api.confirm_actions([f"AI-PA-{i}" for i in range(1, 6)]))
        self.assertIn("time budget", items["AI-PA-3"]["message"])
        self.assertIn("not an action the AI proposed for you", items["AI-PA-4"]["message"])
        self.assertIn("already Executed", items["AI-PA-5"]["message"])

    def test_a_batch_inside_the_budget_runs_everything(self):
        self.seconds_per_tool = 4.0  # the tenth starts at 36s (at 5s it would be 45s: over)
        self.add(*(FakeAction(f"AI-PA-{i}", minutes_old=50 - i) for i in range(1, 11)))
        response = gating_api.confirm_actions([f"AI-PA-{i}" for i in range(1, 11)])
        self.assertEqual(response["executed"], 10)
        self.assertIs(response["budget_reached"], False)

    def test_cancel_actions_keeps_to_the_budget_too(self):
        self.add(FakeAction("AI-PA-1", minutes_old=20), FakeAction("AI-PA-2", minutes_old=10))
        clock = iter([0.0, 0.0, 46.0])  # started, first check, second check
        with mock.patch.object(gating_api, "monotonic", lambda: next(clock)):
            response = gating_api.cancel_actions(["AI-PA-1", "AI-PA-2"])
        self.assertEqual([i["status"] for i in response["results"]], ["Cancelled", "Skipped"])
        self.assertEqual(self.store["AI-PA-2"].status, "Pending")


#: Every endpoint in gating_api, and how each is called with one harmless argument.
ENDPOINT_CALLS = {
    "confirm_action": lambda: gating_api.confirm_action("AI-PA-1"),
    "cancel_action": lambda: gating_api.cancel_action("AI-PA-1"),
    "reveal_sealed": lambda: gating_api.reveal_sealed("AI-PA-1"),
    "my_pending_actions": lambda: gating_api.my_pending_actions(),
    "confirm_actions": lambda: gating_api.confirm_actions(["AI-PA-1"]),
    "cancel_actions": lambda: gating_api.cancel_actions(["AI-PA-1"]),
}


class TestDecisionsNeedADeskSession(BatchHarness):
    """An OAuth client holding the user's token must never decide its own proposals."""

    def setUp(self):
        super().setUp()
        self.add(FakeAction("AI-PA-1", sealed_arguments="*" * 12, password=json.dumps([])))

    def test_every_endpoint_refuses_a_request_with_an_authorization_header(self):
        import frappe

        # A bearer token (OAuth), an API key pair, and the same pair as Basic auth.
        for header in ("Bearer 0a1b2c3d", "token 1234abcd:5678efgh", "Basic MTIzNDphYmNk"):
            self.headers["Authorization"] = header
            for endpoint, call in ENDPOINT_CALLS.items():
                with self.subTest(endpoint=endpoint, header=header.split()[0]):
                    with self.assertRaises(FakeThrow) as raised:
                        call()
                    self.assertIs(raised.exception.exc, frappe.PermissionError)
                    self.assertIn("token or API key", str(raised.exception))
        # Refused before anything was read, decided or run.
        self.assertEqual((self.get_all_calls, self.get_doc_calls, self.executed), ([], [], []))
        self.assertEqual(self.store["AI-PA-1"].status, "Pending")
        self.assertEqual(self.store["AI-PA-1"].password_reads, 0)

    def test_without_the_header_every_endpoint_works(self):
        self.assertEqual(ENDPOINT_CALLS["my_pending_actions"]()[0]["name"], "AI-PA-1")
        self.assertEqual(ENDPOINT_CALLS["reveal_sealed"](), [])
        self.assertEqual(ENDPOINT_CALLS["cancel_actions"]()["cancelled"], 1)
        self.add(FakeAction("AI-PA-2"), FakeAction("AI-PA-3"), FakeAction("AI-PA-4"))
        self.assertEqual(gating_api.confirm_action("AI-PA-2")["status"], "Executed")
        self.assertEqual(gating_api.cancel_action("AI-PA-3"), {"status": "Cancelled"})
        self.assertEqual(gating_api.confirm_actions(["AI-PA-4"])["executed"], 1)

    def test_an_empty_header_is_no_header(self):
        self.headers["Authorization"] = ""
        self.assertEqual(gating_api.cancel_action("AI-PA-1"), {"status": "Cancelled"})

    def test_outside_a_web_request_it_does_nothing(self):
        import frappe

        def must_not_be_read(*a, **k):
            raise AssertionError("no request, so no header to read")

        for local in (types.SimpleNamespace(), None):
            with self.subTest(local=local):
                with mock.patch.object(frappe, "local", local, create=True), mock.patch.object(
                    frappe, "get_request_header", must_not_be_read, create=True
                ):
                    gating_api._require_desk_session()
        with mock.patch.object(frappe, "local", types.SimpleNamespace(), create=True):
            self.headers["Authorization"] = "Bearer 0a1b2c3d"  # never consulted
            self.assertEqual(gating_api.cancel_action("AI-PA-1"), {"status": "Cancelled"})


class TestCancelActions(BatchHarness):
    def test_the_same_ownership_and_skip_rules(self):
        self.add(
            FakeAction("AI-PA-1", minutes_old=40),
            FakeAction("AI-PA-2", minutes_old=30, requested_by=SOMEONE_ELSE),
            FakeAction("AI-PA-3", minutes_old=20, expires_in_minutes=-5),
            FakeAction("AI-PA-4", minutes_old=10, status="Executed"),
        )
        response = gating_api.cancel_actions(["AI-PA-4", "AI-PA-3", "AI-PA-2", "AI-PA-1"])
        items = self.by_name(response)
        self.assertEqual(items["AI-PA-1"]["status"], "Cancelled")
        self.assertEqual(self.store["AI-PA-1"].decided_by, ME)
        for name in ("AI-PA-2", "AI-PA-3", "AI-PA-4"):
            self.assertEqual(items[name]["status"], "Skipped", name)
        self.assertEqual(self.store["AI-PA-2"].status, "Pending")
        self.assertEqual(self.store["AI-PA-3"].status, "Pending")
        self.assertEqual(self.store["AI-PA-4"].status, "Executed")
        self.assertEqual(set(response), {"results", "cancelled", "failed", "skipped", "budget_reached"})
        self.assertEqual((response["cancelled"], response["failed"], response["skipped"]), (1, 0, 3))
        self.assertEqual(self.executed, [])  # cancelling never runs anything

    def test_each_cancel_is_committed_so_a_later_failure_cannot_undo_it(self):
        self.add(
            FakeAction("AI-PA-1", minutes_old=30),
            FakeAction("AI-PA-2", minutes_old=20),
            FakeAction("AI-PA-3", minutes_old=10),
        )
        self.store["AI-PA-2"].fail_on_save = True
        response = gating_api.cancel_actions(["AI-PA-1", "AI-PA-2", "AI-PA-3"])
        self.assertEqual([i["status"] for i in response["results"]], ["Cancelled", "Failed", "Cancelled"])
        self.assertEqual(self.events[:2], ["commit", "rollback"])
        self.assertEqual(self.events[-1], "commit")

    def test_cap_and_json_string(self):
        self.add(FakeAction("AI-PA-1"))
        self.assertEqual(gating_api.cancel_actions(json.dumps(["AI-PA-1"]))["cancelled"], 1)
        with self.assertRaises(FakeThrow):
            gating_api.cancel_actions([f"AI-PA-{i}" for i in range(51)])


def _gating_api_tree():
    return ast.parse((APP / "assistant_tools" / "gating_api.py").read_text(encoding="utf-8"))


def _decorators(tree):
    return {
        node.name: node.decorator_list
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


ENDPOINTS = (
    "confirm_action",
    "cancel_action",
    "reveal_sealed",
    "my_pending_actions",
    "confirm_actions",
    "cancel_actions",
)


class TestEndpointSurface(unittest.TestCase):
    def test_every_endpoint_is_post_only(self):
        """Frappe skips its CSRF check for GET, and _confirm_one commits for itself, so a GET
        link to confirm_action (an assistant reply can render one as a same-origin anchor) ran
        an action on one click with no dialog. Every decorator in the file is checked, so an
        endpoint added later is held to it as well."""
        tree = _gating_api_tree()
        whitelisted = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                for decorator in node.decorator_list:
                    if "whitelist" in ast.unparse(decorator):
                        whitelisted[node.name] = decorator
        self.assertEqual(sorted(whitelisted), sorted(ENDPOINTS))
        for name, decorator in whitelisted.items():
            with self.subTest(endpoint=name):
                self.assertIsInstance(decorator, ast.Call, f"{name} is a bare @frappe.whitelist")
                self.assertEqual(ast.unparse(decorator.func), "frappe.whitelist")
                self.assertEqual(decorator.args, [])
                keywords = {k.arg: ast.literal_eval(k.value) for k in decorator.keywords}
                self.assertEqual(keywords, {"methods": ["POST"]})

    def test_every_endpoint_checks_for_a_desk_session_before_anything_else(self):
        functions = {n.name: n for n in _gating_api_tree().body if isinstance(n, ast.FunctionDef)}
        for name in ENDPOINTS:
            with self.subTest(endpoint=name):
                body = functions[name].body
                if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    body = body[1:]  # the docstring
                self.assertEqual(ast.unparse(body[0]), "_require_desk_session()")

    def test_the_shared_helpers_are_not_endpoints(self):
        decorators = _decorators(_gating_api_tree())
        for name in (
            "_confirm_one",
            "_cancel_one",
            "_run_batch",
            "_batch_names",
            "_batch_plan",
            "_review_reasons",
            "_never_exempt_reason",
            "_require_desk_session",
        ):
            with self.subTest(helper=name):
                self.assertEqual(decorators[name], [])

    def test_the_gate_imports_add_no_fac_import(self):
        """_changes_docstatus and NEVER_EXEMPT come from _gate, whose FAC imports all sit inside
        function bodies, so gating_api still imports on a site without FAC."""
        for node in _gating_api_tree().body:
            if isinstance(node, ast.Import | ast.ImportFrom):
                self.assertNotIn("frappe_assistant_core", ast.unparse(node))
        self.assertTrue(callable(gating_api._changes_docstatus))
        self.assertIs(gating_api.NEVER_EXEMPT, _gate.NEVER_EXEMPT)

    def test_nothing_here_is_reachable_from_the_mcp(self):
        from erpnext_enhancements.tests.test_assistant_tools_schema import hook_value

        for path in hook_value("assistant_tools") or []:
            self.assertNotIn("gating_api", path)
        for name in ENDPOINTS:
            self.assertNotIn(name, _gate.EXPLICIT_READONLY | _gate.APP_MUTATING | _gate.EXPLICIT_MUTATING)
            self.assertFalse((APP / "assistant_tools" / f"{name}.py").exists(), name)

    def test_the_list_view_calls_these_endpoints_by_name(self):
        # A rename on either side would leave the page calling a method that does not exist.
        path = APP / "ai_governance" / "doctype" / "ai_pending_action" / "ai_pending_action_list.js"
        source = path.read_text(encoding="utf-8")
        self.assertIn("erpnext_enhancements.assistant_tools.gating_api", source)
        for name in ("my_pending_actions", "confirm_actions", "cancel_actions"):
            self.assertIn(f'"{name}"', source, name)

    def test_it_is_wired_into_ci(self):
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("python -m unittest erpnext_enhancements.tests.test_ai_gate_batch", ci)


if __name__ == "__main__":
    unittest.main()
