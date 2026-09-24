"""test_ai_gate_per_call.py"""
import datetime
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_gate = None

def setUpModule():
    global _gate
    from erpnext_enhancements.tests.test_assistant_tools_schema import install_stubs
    install_stubs()
    
    import frappe
    if not hasattr(frappe, "whitelist"):
        frappe.whitelist = lambda *a, **k: (lambda f: f)
    if not hasattr(frappe, "flags"):
        frappe.flags = type("Flags", (), {})()
    if not hasattr(frappe, "session"):
        # The auto-approved Action Log row reads frappe.session.user, so a stub
        # without one turns a behavioural test into an AttributeError.
        frappe.session = type("Session", (), {"user": "tester@example.com"})()


    from erpnext_enhancements.assistant_tools import _gate as gate_module
    _gate = gate_module

class TestPerCallGate(unittest.TestCase):
    def test_workforce_clock_out_decider(self):
        decider = _gate.PER_CALL_GATED["workforce_clock_out"]
        self.assertFalse(decider({}))
        self.assertFalse(decider(None))
        self.assertTrue(decider({"employee": "HR-EMP-1"}))
        
    def test_workforce_clock_in_decider(self):
        decider = _gate.PER_CALL_GATED["workforce_clock_in"]
        self.assertFalse(decider({}))
        self.assertFalse(decider({"project": "PROJ-1"}))
        
    def test_mutation_sets(self):
        tools = ["workforce_clock_in", "workforce_clock_out"]
        for tool in tools:
            self.assertIn(tool, _gate.APP_MUTATING)
            self.assertNotIn(tool, _gate.EXEMPTABLE_TOOLS)
            self.assertNotIn(tool, _gate.EXPLICIT_READONLY)
            
            fake = type("T", (), {"name": tool})()
            self.assertTrue(_gate.is_mutating(fake))

    def test_decider_raise_falls_through_to_gating(self):
        tool_name = "workforce_clock_out"
        fake_tool = type("T", (), {"name": tool_name})()
        
        def original(t, args):
            raise Exception("Should not reach original if gating is triggered")
            
        def decider_raise(args):
            raise ValueError("Decider blew up")
            
        original_decider = _gate.PER_CALL_GATED[tool_name]
        _gate.PER_CALL_GATED[tool_name] = decider_raise
        
        try:
            original_gating_enabled = getattr(_gate, "_gating_enabled", None)
            _gate._gating_enabled = lambda: True
            
            original_propose = getattr(_gate, "_propose", None)
            _gate._propose = lambda t, args: {"propose_called": True}
            
            res = _gate._gated_execute(fake_tool, original, {})
            self.assertEqual(res, {"propose_called": True})
            
            _gate._gating_enabled = original_gating_enabled
            _gate._propose = original_propose
        finally:
            _gate.PER_CALL_GATED[tool_name] = original_decider

class TestAnExecutedCallIsNeverAlsoProposed(unittest.TestCase):
    """The failure mode that makes the nesting of the guard load-bearing.

    If `original(...)` or the auto-approved logging sat inside the same
    try/except as the decider, then a failure AFTER the write had already
    happened would be swallowed and fall through to the Pending Action path —
    handing a human a confirmation card for an action that had already run. On
    confirm, the gate re-executes it. For these tools that is clocking somebody
    out twice; for a future one it could be worse.

    So: once a decider has returned False, the call executes exactly once and
    the proposal path must be unreachable, no matter what else raises.
    """

    def _run(self, log_raises):
        tool_name = "workforce_clock_out"
        fake_tool = type("T", (), {"name": tool_name})()
        calls = {"executed": 0, "proposed": 0}

        def original(t, args):
            calls["executed"] += 1
            return {"success": True, "result": "stopped"}

        saved = {
            "gating": getattr(_gate, "_gating_enabled", None),
            "propose": getattr(_gate, "_propose", None),
            "log": getattr(_gate, "insert_action_log", None),
        }
        _gate._gating_enabled = lambda: True

        def _propose(t, args):
            calls["proposed"] += 1
            return {"propose_called": True}

        _gate._propose = _propose

        def _log(**kwargs):
            if log_raises:
                raise RuntimeError("the Action Log doctype is missing on this site")

        _gate.insert_action_log = _log
        try:
            # self-service: no `employee`, so the decider returns False
            response = _gate._gated_execute(fake_tool, original, {})
        finally:
            _gate._gating_enabled = saved["gating"]
            _gate._propose = saved["propose"]
            _gate.insert_action_log = saved["log"]
        return response, calls

    def test_a_self_service_call_executes_once_and_is_not_proposed(self):
        response, calls = self._run(log_raises=False)
        self.assertEqual(calls["executed"], 1)
        self.assertEqual(calls["proposed"], 0)
        self.assertEqual(response, {"success": True, "result": "stopped"})

    def test_a_failure_after_execution_does_not_become_a_proposal(self):
        """The regression itself: logging blows up after the write landed."""
        with self.assertRaises(RuntimeError):
            self._run(log_raises=True)
        # Reaching the raise is the point — the exception must propagate rather
        # than be swallowed into a second, human-confirmable execution.

    def test_execution_is_not_reachable_when_the_decider_raises(self):
        tool_name = "workforce_clock_out"
        fake_tool = type("T", (), {"name": tool_name})()
        calls = {"executed": 0}

        def original(t, args):
            calls["executed"] += 1
            return {"success": True}

        saved_decider = _gate.PER_CALL_GATED[tool_name]
        saved_gating = getattr(_gate, "_gating_enabled", None)
        saved_propose = getattr(_gate, "_propose", None)

        def boom(args):
            raise ValueError("undecidable")

        _gate.PER_CALL_GATED[tool_name] = boom
        _gate._gating_enabled = lambda: True
        _gate._propose = lambda t, args: {"propose_called": True}
        try:
            res = _gate._gated_execute(fake_tool, original, {})
        finally:
            _gate.PER_CALL_GATED[tool_name] = saved_decider
            _gate._gating_enabled = saved_gating
            _gate._propose = saved_propose

        self.assertEqual(res, {"propose_called": True})
        self.assertEqual(calls["executed"], 0, "a raising decider must not execute the write")


class TestGatingOffIsUnchanged(unittest.TestCase):
    def test_with_gating_disabled_the_registry_is_never_consulted(self):
        """ai_write_gating_enabled is 0 on production. A site with the flag off
        must behave exactly as it did before ADR 0014."""
        tool_name = "workforce_clock_out"
        fake_tool = type("T", (), {"name": tool_name})()
        seen = {"decider": 0, "executed": 0}

        def original(t, args):
            seen["executed"] += 1
            return {"success": True}

        saved_decider = _gate.PER_CALL_GATED[tool_name]
        saved_gating = getattr(_gate, "_gating_enabled", None)

        def counting(args):
            seen["decider"] += 1
            return True

        _gate.PER_CALL_GATED[tool_name] = counting
        _gate._gating_enabled = lambda: False
        try:
            # on-behalf arguments, which WOULD be gated if the flag were on
            _gate._gated_execute(fake_tool, original, {"employee": "HR-EMP-1"})
        finally:
            _gate.PER_CALL_GATED[tool_name] = saved_decider
            _gate._gating_enabled = saved_gating

        self.assertEqual(seen["executed"], 1)
        self.assertEqual(seen["decider"], 0, "the registry must sit below the enabled check")


class TestNoDeciderOnAHighRiskTool(unittest.TestCase):
    """Step 3b never consults HIGH_RISK, so a decider is the one way to run a HIGH_RISK tool
    unconfirmed. Nothing pinned that before WI-079 slice 1.

    run_python_code is the case that forced it: as deployed (FAC 3.0.0) it hands the caller the
    whole `frappe` module on a read-write connection, and production holds rows it created. Ungating
    it would also ungate every Task create and close ADR 0016 §6 gates, one `.insert()` away.
    Changing this is a governance decision, so it has to be a deliberate edit of this test.
    """

    def test_per_call_gated_and_high_risk_are_disjoint(self):
        self.assertEqual(set(_gate.PER_CALL_GATED) & _gate.HIGH_RISK, set())

    def test_run_python_code_stays_gated(self):
        self.assertIn("run_python_code", _gate.HIGH_RISK)
        self.assertNotIn("run_python_code", _gate.PER_CALL_GATED)
        self.assertNotIn("run_python_code", _gate.EXEMPTABLE_TOOLS)


class TestUpdateDocumentDecider(unittest.TestCase):
    """ADR 0016 §6: an AI may update a Task without a confirmation, but not close one."""

    def setUp(self):
        self.decider = _gate.PER_CALL_GATED["update_document"]

    def test_task_updates_that_execute(self):
        for data in (
            {"status": "Working"},
            {"status": "Open"},
            {"status": "Pending Review"},
            {"status": "Overdue"},
            {"description": "no status key"},
            {},
        ):
            with self.subTest(data=data):
                self.assertFalse(self.decider({"doctype": "Task", "name": "T-1", "data": data}))

    def test_closing_or_anything_unrecognised_waits(self):
        for data in (
            {"status": "Completed"},
            {"status": "Canceled"},
            {"status": "Cancelled"},  # ERPNext core's spelling; this site's option is "Canceled"
            {"status": "Invoiced"},
            {"status": "Template"},
            {"status": " working"},
            {"status": "completed"},
            {"status": None},
            {"status": 1},
        ):
            with self.subTest(data=data):
                self.assertTrue(self.decider({"doctype": "Task", "name": "T-1", "data": data}))

    def test_a_key_that_changes_which_record_is_written_waits(self):
        # {"name": other Task, "modified": its modified} with no status would save this Task's
        # fields — status included — over the other one, unconfirmed (review of WI-079 slice 1).
        for data in (
            {"name": "TASK-B", "modified": "2026-09-23 10:00:00"},
            {"name": "TASK-B"},
            {"modified": "2026-09-23 10:00:00", "status": "Working"},
            {"docstatus": 1},
            {"owner": "someone@example.com"},
            {"doctype": "Project"},
            {"is_template": 1},  # ERPNext derives status "Template" from it
            {"lft": 1, "rgt": 2},
            {"__unsaved": 1},
            {"_assign": "[]"},
            {1: "x"},
        ):
            with self.subTest(data=data):
                self.assertTrue(self.decider({"doctype": "Task", "name": "T-1", "data": data}))

    def test_malformed_calls_and_other_doctypes_wait(self):
        for args in (
            None,
            "Task",
            {},
            {"doctype": "task", "data": {"status": "Working"}},
            {"doctype": "Project", "data": {"status": "Open"}},
            {"doctype": "Task", "data": "status=Working"},
            {"doctype": "Task", "data": None},
            {"doctype": "Task"},
        ):
            with self.subTest(args=args):
                self.assertTrue(self.decider(args))

    def test_it_is_registered_only_for_update_document(self):
        self.assertNotIn("create_document", _gate.PER_CALL_GATED)
        self.assertIs(_gate.PER_CALL_GATED["update_document"], _gate._update_document_needs_human)


class GateHarness(unittest.TestCase):
    """Runs `_gated_execute` itself with the flag on and a stubbed settings doc."""

    def _run(self, tool_name, arguments, exempt_rows=()):
        import frappe

        fake_tool = type("T", (), {"name": tool_name})()
        calls = {"executed": 0, "proposed": 0, "logged": []}

        def original(t, args):
            calls["executed"] += 1
            return {"success": True, "result": "done"}

        def _propose(t, args):
            calls["proposed"] += 1
            return {"propose_called": True}

        def _log(**kwargs):
            calls["logged"].append(kwargs)

        def _rows(self, key):
            # Only the real table field answers, so a renamed field fails these tests instead of
            # being papered over by a stub that returns rows for any key. A row is a doctype
            # (permanent) or a (doctype, exempt_until) pair (a time-boxed window, v1.525.0).
            if key != "ai_exempt_doctypes":
                return []
            rows = []
            for d in exempt_rows:
                doctype, until = (d, None) if isinstance(d, str) else d
                rows.append(type("Row", (), {"document_type": doctype, "exempt_until": until})())
            return rows

        settings = type("Settings", (), {"get": _rows})()

        def _cached(doctype, *a, **k):
            assert doctype == "ERPNext Enhancements Settings", doctype
            return settings

        gate_frappe = _gate.frappe
        saved = {
            "gating": _gate._gating_enabled,
            "propose": _gate._propose,
            "log": _gate.insert_action_log,
            "cached": getattr(gate_frappe, "get_cached_doc", None),
        }
        _gate._gating_enabled = lambda: True
        _gate._propose = _propose
        _gate.insert_action_log = _log
        gate_frappe.get_cached_doc = _cached
        try:
            response = _gate._gated_execute(fake_tool, original, arguments)
        finally:
            _gate._gating_enabled = saved["gating"]
            _gate._propose = saved["propose"]
            _gate.insert_action_log = saved["log"]
            if saved["cached"] is None:
                del gate_frappe.get_cached_doc
            else:
                gate_frappe.get_cached_doc = saved["cached"]
        return response, calls


class TestTheScopedGateEndToEnd(GateHarness):
    """The flag on, the three outcomes ADR 0016 §6 promises, through `_gated_execute` itself."""

    def test_a_task_moved_to_working_executes_once_and_is_logged(self):
        response, calls = self._run(
            "update_document", {"doctype": "Task", "name": "T-1", "data": {"status": "Working"}}
        )
        self.assertEqual((calls["executed"], calls["proposed"]), (1, 0))
        self.assertEqual(calls["logged"][0]["auto_approved"], 1)
        self.assertEqual(response, {"success": True, "result": "done"})

    def test_a_task_closed_is_proposed_not_executed(self):
        response, calls = self._run(
            "update_document", {"doctype": "Task", "name": "T-1", "data": {"status": "Completed"}}
        )
        self.assertEqual((calls["executed"], calls["proposed"]), (0, 1))
        self.assertEqual(response, {"propose_called": True})

    def test_an_exempt_comment_still_executes_after_the_decider_says_wait(self):
        response, calls = self._run(
            "update_document",
            {"doctype": "Comment", "name": "c1", "data": {"content": "x"}},
            exempt_rows=("Comment",),
        )
        self.assertEqual((calls["executed"], calls["proposed"]), (1, 0))

    def test_a_task_row_in_the_exempt_table_cannot_ungate_task_creation(self):
        response, calls = self._run(
            "create_document", {"doctype": "Task", "data": {"subject": "x"}}, exempt_rows=("Task",)
        )
        self.assertEqual((calls["executed"], calls["proposed"]), (0, 1))

    def test_a_task_row_in_the_exempt_table_cannot_ungate_a_task_close(self):
        response, calls = self._run(
            "update_document",
            {"doctype": "Task", "name": "T-1", "data": {"status": "Completed"}},
            exempt_rows=("Task",),
        )
        self.assertEqual((calls["executed"], calls["proposed"]), (0, 1))

    def test_the_record_swap_is_proposed_not_executed(self):
        response, calls = self._run(
            "update_document",
            {"doctype": "Task", "name": "T-A", "data": {"name": "T-B", "modified": "2026-09-23 10:00:00"}},
        )
        self.assertEqual((calls["executed"], calls["proposed"]), (0, 1))

    def test_task_creation_is_proposed(self):
        response, calls = self._run("create_document", {"doctype": "Task", "data": {"subject": "x"}})
        self.assertEqual((calls["executed"], calls["proposed"]), (0, 1))


class TestTimeBoxedExemptions(GateHarness):
    """v1.525.0: an exemption row with exempt_until is a window that closes by itself."""

    NOW = datetime.datetime(2026, 9, 23, 15, 0, 0)

    def setUp(self):
        import frappe

        patches = [
            mock.patch.object(frappe.utils, "now_datetime", lambda: self.NOW, create=True),
            mock.patch.object(
                frappe.utils,
                "get_datetime",
                lambda v: v if isinstance(v, datetime.datetime) else datetime.datetime.fromisoformat(str(v)),
                create=True,
            ),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    ITEM = {"doctype": "Item", "data": {"item_code": "PUMP-001", "item_name": "PUMP"}}

    def test_an_open_window_lets_a_bulk_create_through(self):
        _, calls = self._run("create_document", self.ITEM, exempt_rows=(("Item", "2026-09-23 18:00:00"),))
        self.assertEqual((calls["executed"], calls["proposed"]), (1, 0))
        self.assertEqual(calls["logged"][0]["auto_approved"], 1)

    def test_a_closed_window_is_a_card_again(self):
        _, calls = self._run("create_document", self.ITEM, exempt_rows=(("Item", "2026-09-23 14:59:59"),))
        self.assertEqual((calls["executed"], calls["proposed"]), (0, 1))

    def test_the_window_closes_at_the_exact_moment(self):
        _, calls = self._run("create_document", self.ITEM, exempt_rows=((("Item", self.NOW)),))
        self.assertEqual((calls["executed"], calls["proposed"]), (0, 1))

    def test_a_window_for_one_doctype_leaves_the_others_gated(self):
        _, calls = self._run(
            "create_document",
            {"doctype": "Item Price", "data": {"item_code": "PUMP-001", "price_list_rate": 1}},
            exempt_rows=(("Item", "2026-09-23 18:00:00"),),
        )
        self.assertEqual((calls["executed"], calls["proposed"]), (0, 1))

    def test_an_unreadable_window_counts_as_closed_for_that_row_only(self):
        rows = (("Item", "not a date"), "Comment")
        _, calls = self._run("create_document", self.ITEM, exempt_rows=rows)
        self.assertEqual((calls["executed"], calls["proposed"]), (0, 1))
        _, calls = self._run("create_document", {"doctype": "Comment", "data": {"content": "x"}}, exempt_rows=rows)
        self.assertEqual((calls["executed"], calls["proposed"]), (1, 0))

    def test_the_gates_own_records_can_never_be_exempted(self):
        # An assistant that could write these could open its own window, rewrite a card after a
        # human read it, or edit its own audit trail.
        for doctype in (
            "ERPNext Enhancements Settings",
            "AI Confirmation Exempt Doctype",
            "AI Pending Action",
            "AI Action Log",
        ):
            with self.subTest(doctype=doctype):
                _, calls = self._run(
                    "update_document",
                    {"doctype": doctype, "name": "x", "data": {"note": "y"}},
                    exempt_rows=(doctype, (doctype, "2026-09-23 18:00:00")),
                )
                self.assertEqual((calls["executed"], calls["proposed"]), (0, 1))


if __name__ == "__main__":
    unittest.main()
