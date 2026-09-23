"""Bench-free unit tests for the AI write gate's pure helpers.

Plain ``unittest`` under the same stub environment as
``test_assistant_tools_schema`` (no Frappe site, no FAC). Covers risk/mutation
classification, the summary templates, the anti-fabrication envelope, the
args fingerprint, argument sanitization (fallback heuristic — FAC is absent
here), result truncation, and that ``apply_gate()`` no-ops cleanly against
the stub BaseTool (which has no ``_safe_execute`` seam).

Stubs are installed in ``setUpModule`` (execution time), NOT at import time:
pytest imports every test module before running anything, and stubbing
``frappe`` during collection would fool the bench-only integration suites'
``import frappe`` skip-guards into running against the stub.

Run: python -m pytest erpnext_enhancements/tests/test_ai_gate_unit.py
"""

import json
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

    # gating_api decorates with @frappe.whitelist() at import time; the shared
    # stub doesn't carry it (tool modules never need it).
    import frappe

    if not hasattr(frappe, "whitelist"):
        frappe.whitelist = lambda *a, **k: (lambda f: f)

    from erpnext_enhancements.assistant_tools import _gate as gate_module

    _gate = gate_module


class TestClassification(unittest.TestCase):
    def test_risk_levels(self):
        self.assertEqual(_gate.classify_risk("delete_document"), "High")
        self.assertEqual(_gate.classify_risk("submit_document"), "High")
        self.assertEqual(_gate.classify_risk("run_python_code"), "High")
        self.assertEqual(_gate.classify_risk("run_workflow"), "High")
        self.assertEqual(_gate.classify_risk("anything", category="privileged"), "High")
        self.assertEqual(_gate.classify_risk("update_document"), "Medium")
        self.assertEqual(_gate.classify_risk("some_unknown_writer"), "Medium")
        self.assertEqual(_gate.classify_risk("create_document"), "Low")
        self.assertEqual(_gate.classify_risk("create_dashboard"), "Low")
        # this app's own write tool — a create, so Low
        self.assertEqual(_gate.classify_risk("create_followup_task"), "Low")

    def test_explicit_sets_are_disjoint(self):
        self.assertFalse(_gate.EXPLICIT_MUTATING & _gate.EXPLICIT_READONLY)
        # app write tools must never be misclassified as read-only
        self.assertFalse(_gate.APP_MUTATING & _gate.EXPLICIT_READONLY)

    def test_own_tools_are_explicit_readonly(self):
        for name in ("workforce_time_status", "check_ai_pending_action", "run_database_query"):
            self.assertIn(name, _gate.EXPLICIT_READONLY)

    def test_app_write_tool_is_mutating(self):
        # An app write tool gates without relying on FAC's category detector:
        # is_mutating() returns True from APP_MUTATING alone (no _tool_category
        # call, so no FAC import needed here).
        fake = type("T", (), {"name": "create_followup_task"})()
        self.assertTrue(_gate.is_mutating(fake))
        self.assertIn("create_followup_task", _gate.APP_MUTATING)

    def test_exemptable_is_subset_of_mutating(self):
        self.assertTrue(_gate.EXEMPTABLE_TOOLS <= _gate.EXPLICIT_MUTATING)
        # privileged/irreversible tools must never be exemptable
        self.assertFalse(_gate.EXEMPTABLE_TOOLS & _gate.HIGH_RISK)

    def test_fac_3_faco_writes_gate_without_a_config_row(self):
        # FAC 3.0.0's faco plugin. send_email mails any address the model names from the
        # site's own account and needs no DocPerm, so it must gate -- and be High -- from the
        # explicit set alone, with no FAC Tool Configuration row to consult (none exists until
        # someone enables the plugin, and is_mutating must not need FAC importable to decide).
        for name, risk in (("send_email", "High"), ("generate_document", "Low")):
            fake = type("T", (), {"name": name})()
            self.assertTrue(_gate.is_mutating(fake), name)
            self.assertEqual(_gate.classify_risk(name), risk, name)
            # and neither may ever skip confirmation through the doctype allowlist
            self.assertNotIn(name, _gate.EXEMPTABLE_TOOLS)

    def test_fac_3_removed_search_tools_are_not_classified(self):
        # search_doctype / search_link left FAC in 3.0.0 (folded into search_documents). A
        # classification entry for a tool that no longer exists is a claim that rots.
        every = _gate.EXPLICIT_MUTATING | _gate.EXPLICIT_READONLY | _gate.APP_MUTATING
        self.assertFalse({"search_doctype", "search_link"} & every)


class TestSummaries(unittest.TestCase):
    def test_templates(self):
        self.assertEqual(
            _gate.summarize_tool_call("create_document", {"doctype": "ToDo"}), "Create ToDo"
        )
        self.assertEqual(
            _gate.summarize_tool_call("update_document", {"doctype": "Task", "name": "T-1"}),
            "Update Task T-1",
        )
        self.assertEqual(
            _gate.summarize_tool_call("delete_document", {"doctype": "Note", "name": "N-9"}),
            "Delete Note N-9",
        )
        self.assertIn("Python", _gate.summarize_tool_call("run_python_code", {}))
        self.assertEqual(
            _gate.summarize_tool_call("some_custom_writer", {}), "Some custom writer"
        )

    def test_followup_task_summary(self):
        plain = _gate.summarize_tool_call(
            "create_followup_task", {"description": "Call the customer back"}
        )
        self.assertEqual(plain, "Create follow-up task “Call the customer back”")
        linked = _gate.summarize_tool_call(
            "create_followup_task",
            {
                "description": "Order the pump",
                "reference_doctype": "Project",
                "reference_name": "PROJ-0042",
            },
        )
        self.assertIn("on Project PROJ-0042", linked)
        # long descriptions are trimmed for the desk card
        long_summary = _gate.summarize_tool_call(
            "create_followup_task", {"description": "x" * 200}
        )
        self.assertIn("…", long_summary)
        self.assertLess(len(long_summary), 120)

    def test_send_email_summary_names_the_recipients(self):
        # The recipients are the decision a human is being asked to make, so they are named,
        # not counted -- "1 recipient" reads the same for a colleague and for an attacker.
        summary = _gate.summarize_tool_call(
            "send_email",
            {"recipients": ["ops@example.com", "x@elsewhere.test"], "subject": "Q3 figures"},
        )
        self.assertIn("ops@example.com", summary)
        self.assertIn("x@elsewhere.test", summary)
        self.assertIn("Q3 figures", summary)
        many = _gate.summarize_tool_call(
            "send_email", {"recipients": [f"u{i}@example.com" for i in range(7)], "subject": "s"}
        )
        self.assertIn("(+4 more)", many)
        # a bare string where FAC's schema says array must not be split into characters
        self.assertIn(
            "solo@example.com",
            _gate.summarize_tool_call("send_email", {"recipients": "solo@example.com", "subject": "s"}),
        )

    def test_generate_document_summary(self):
        self.assertEqual(
            _gate.summarize_tool_call("generate_document", {"filename": "q3.pdf", "title": "Q3 Review"}),
            "Generate a PDF document Q3 Review",
        )
        self.assertEqual(
            _gate.summarize_tool_call("generate_document", {"filename": "q3.pdf"}),
            "Generate a PDF document q3.pdf",
        )


class TestRequestContext(unittest.TestCase):
    """FAC 3.0.0 stamps FAC Chat's conversation id on frappe.local.ar_session_id."""

    def setUp(self):
        import frappe

        self.frappe = frappe
        self._saved = getattr(frappe, "local", None)
        frappe.local = type("Local", (), {})()

    def tearDown(self):
        if self._saved is None:
            del self.frappe.local
        else:
            self.frappe.local = self._saved

    def test_fac_chat_conversation_wins_over_per_request_uuid(self):
        self.frappe.local.assistant_session_id = "3f0c-random-per-request"
        self.frappe.local.ar_session_id = "conv-42"
        self.assertEqual(_gate._session_id(), "conv-42")
        self.assertEqual(_gate._client_id(), _gate.FAC_CHAT_CLIENT_ID)

    def test_plain_mcp_client_unchanged(self):
        self.frappe.local.assistant_session_id = "mcp-session"
        self.frappe.local.assistant_client_id = "claude-desktop"
        self.assertEqual(_gate._session_id(), "mcp-session")
        self.assertEqual(_gate._client_id(), "claude-desktop")

    def test_nothing_set_is_none_not_an_error(self):
        self.assertIsNone(_gate._session_id())
        self.assertIsNone(_gate._client_id())


class TestEnvelope(unittest.TestCase):
    def test_anti_fabrication_language(self):
        envelope = _gate.build_envelope("AI-PA-2026-00001", "Create ToDo", "Low", "2026-06-11 15:00:00")
        self.assertEqual(envelope["status"], "awaiting_user_confirmation")
        self.assertIs(envelope["executed"], False)
        self.assertIsNone(envelope["output"])
        self.assertEqual(envelope["action_id"], "AI-PA-2026-00001")
        self.assertIn("NOT been executed", envelope["message"])
        self.assertIn("fabricate", envelope["message"])
        self.assertIn("check_ai_pending_action", envelope["message"])
        # must serialize cleanly (the gate returns it as a JSON string)
        json.dumps(envelope)


class TestFingerprintAndSanitize(unittest.TestCase):
    def test_fingerprint_is_stable_and_order_independent(self):
        a = _gate.args_fingerprint("u@x", "create_document", {"a": 1, "b": 2})
        b = _gate.args_fingerprint("u@x", "create_document", {"b": 2, "a": 1})
        self.assertEqual(a, b)
        self.assertNotEqual(a, _gate.args_fingerprint("other@x", "create_document", {"a": 1, "b": 2}))
        self.assertNotEqual(a, _gate.args_fingerprint("u@x", "update_document", {"a": 1, "b": 2}))

    def test_sanitize_redacts_credentials(self):
        scrubbed = _gate.sanitize_arguments(
            {
                "doctype": "User",
                "data": {"api_key": "abc", "password": "hunter2", "full_name": "Jo"},
                "items": [{"secret": "x", "qty": 2}],
            }
        )
        self.assertEqual(scrubbed["data"]["api_key"], "***REDACTED***")
        self.assertEqual(scrubbed["data"]["password"], "***REDACTED***")
        self.assertEqual(scrubbed["data"]["full_name"], "Jo")
        self.assertEqual(scrubbed["items"][0]["secret"], "***REDACTED***")
        self.assertEqual(scrubbed["items"][0]["qty"], 2)

    def test_truncate_json_caps_size(self):
        big = {"blob": "x" * (2 * 50_000)}
        text = _gate.truncate_json(big)
        self.assertLessEqual(len(text), _gate.RESULT_MAX_BYTES + 50)
        self.assertIn("[truncated]", text)


class TestApplyGateStubSafety(unittest.TestCase):
    def test_noop_against_stub_basetool(self):
        from frappe_assistant_core.core.base_tool import BaseTool

        self.assertFalse(hasattr(BaseTool, "_safe_execute"))
        _gate.apply_gate()  # must not raise, must not invent the seam
        self.assertFalse(hasattr(BaseTool, "_safe_execute"))

    def test_gating_api_imports_under_stubs(self):
        from erpnext_enhancements.assistant_tools import gating_api

        self.assertTrue(callable(gating_api.confirm_action))
        self.assertTrue(callable(gating_api.cancel_action))


if __name__ == "__main__":
    unittest.main()
