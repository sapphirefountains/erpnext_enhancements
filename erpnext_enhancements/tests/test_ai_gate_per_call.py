"""test_ai_gate_per_call.py"""
import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
