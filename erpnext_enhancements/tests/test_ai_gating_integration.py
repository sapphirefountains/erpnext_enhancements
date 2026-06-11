"""Bench-backed integration tests for the AI write gate.

Needs a real bench with frappe_assistant_core installed; every test is
skip-guarded so the file collects cleanly on FAC-less benches and on machines
without frappe at all. All FAC imports happen inside test methods.

Run: bench --site <site> run-tests --app erpnext_enhancements \
    --module erpnext_enhancements.tests.test_ai_gating_integration
"""

import json
import unittest

try:  # collection-safe on benches/machines without frappe
    import frappe
    from frappe.utils import add_to_date, now_datetime

    _HAS_FRAPPE = True
except Exception:
    _HAS_FRAPPE = False

if _HAS_FRAPPE:
    try:
        import frappe_assistant_core  # noqa: F401

        _HAS_FAC = True
    except Exception:
        _HAS_FAC = False
else:
    _HAS_FAC = False

TODO_MARKER = "_Test AI gate todo"


@unittest.skipUnless(_HAS_FRAPPE and _HAS_FAC, "needs a bench with frappe_assistant_core installed")
class TestAIGatingIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        # importing the package applies the gate (FAC does the same per request)
        import erpnext_enhancements.assistant_tools  # noqa: F401

    def setUp(self):
        frappe.set_user("Administrator")
        self.settings = frappe.get_doc("ERPNext Enhancements Settings")
        self._old_flag = self.settings.get("ai_write_gating_enabled")
        self._cleanup()

    def tearDown(self):
        self._set_gating(self._old_flag or 0)
        self._cleanup()
        frappe.flags.ai_gate_bypass = False
        frappe.flags.ai_gate_pending = None

    def _set_gating(self, value):
        doc = frappe.get_doc("ERPNext Enhancements Settings")
        doc.ai_write_gating_enabled = value or 0
        doc.save(ignore_permissions=True)
        frappe.clear_document_cache("ERPNext Enhancements Settings", "ERPNext Enhancements Settings")
        frappe.db.commit()

    def _cleanup(self):
        for name in frappe.get_all("ToDo", filters={"description": ("like", f"%{TODO_MARKER}%")}, pluck="name"):
            frappe.delete_doc("ToDo", name, force=True, ignore_permissions=True)
        frappe.flags.ai_log_purge = True
        try:
            for doctype in ("AI Action Log", "AI Pending Action"):
                for name in frappe.get_all(doctype, filters={"tool_name": "create_document"}, pluck="name"):
                    doc = frappe.get_doc(doctype, name)
                    if doctype == "AI Pending Action" or TODO_MARKER in (doc.get("arguments") or ""):
                        frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
        finally:
            frappe.flags.ai_log_purge = False
        frappe.db.commit()

    def _registry(self):
        from frappe_assistant_core.core.tool_registry import get_tool_registry

        return get_tool_registry()

    def _todo_args(self, suffix="1"):
        return {"doctype": "ToDo", "data": {"description": f"{TODO_MARKER} {suffix}"}}

    # ------------------------------------------------------------------ tests

    def test_gate_marker_present(self):
        """Canary: screams when a FAC upgrade renames the _safe_execute seam."""
        from frappe_assistant_core.core.base_tool import BaseTool

        self.assertTrue(
            getattr(BaseTool._safe_execute, "_ee_ai_gate", False),
            "BaseTool._safe_execute is not wrapped — the AI write gate is OFF the seam",
        )

    def test_flag_off_executes_directly(self):
        self._set_gating(0)
        result = self._registry().execute_tool("create_document", self._todo_args("direct"))
        self.assertTrue(isinstance(result, dict))
        self.assertTrue(
            frappe.db.exists("ToDo", {"description": ("like", f"%{TODO_MARKER} direct%")})
        )
        self.assertFalse(frappe.db.exists("AI Pending Action", {"tool_name": "create_document"}))

    def test_flag_on_returns_envelope_and_dedupes(self):
        self._set_gating(1)
        raw = self._registry().execute_tool("create_document", self._todo_args("gated"))
        envelope = json.loads(raw)
        self.assertEqual(envelope["status"], "awaiting_user_confirmation")
        self.assertFalse(envelope["executed"])
        action_id = envelope["action_id"]
        self.assertTrue(frappe.db.exists("AI Pending Action", action_id))
        # nothing executed
        self.assertFalse(
            frappe.db.exists("ToDo", {"description": ("like", f"%{TODO_MARKER} gated%")})
        )
        # identical retry → same action, no duplicate card
        raw2 = self._registry().execute_tool("create_document", self._todo_args("gated"))
        self.assertEqual(json.loads(raw2)["action_id"], action_id)

        action = frappe.get_doc("AI Pending Action", action_id)
        self.assertEqual(action.status, "Pending")
        self.assertEqual(action.risk, "Low")
        self.assertEqual(action.requested_by, "Administrator")

    def test_confirm_executes_and_logs(self):
        self._set_gating(1)
        raw = self._registry().execute_tool("create_document", self._todo_args("confirm"))
        action_id = json.loads(raw)["action_id"]

        from erpnext_enhancements.assistant_tools.gating_api import confirm_action

        confirm_action(action_id)

        action = frappe.get_doc("AI Pending Action", action_id)
        self.assertEqual(action.status, "Executed")
        self.assertTrue(action.action_log)
        self.assertTrue(
            frappe.db.exists("ToDo", {"description": ("like", f"%{TODO_MARKER} confirm%")})
        )
        log = frappe.get_doc("AI Action Log", action.action_log)
        self.assertTrue(log.success)
        self.assertEqual(log.pending_action, action_id)
        self.assertFalse(log.auto_approved)

        # the model-side round trip sees the real outcome
        from erpnext_enhancements.assistant_tools.check_ai_pending_action import (
            CheckAiPendingAction,
        )

        status = CheckAiPendingAction().execute({"action_id": action_id})
        self.assertEqual(status["status"], "Executed")

    def test_cancel_path(self):
        self._set_gating(1)
        raw = self._registry().execute_tool("create_document", self._todo_args("cancel"))
        action_id = json.loads(raw)["action_id"]

        from erpnext_enhancements.assistant_tools.gating_api import cancel_action

        cancel_action(action_id)
        self.assertEqual(frappe.db.get_value("AI Pending Action", action_id, "status"), "Cancelled")
        self.assertFalse(
            frappe.db.exists("ToDo", {"description": ("like", f"%{TODO_MARKER} cancel%")})
        )

    def test_expired_action_cannot_confirm(self):
        self._set_gating(1)
        raw = self._registry().execute_tool("create_document", self._todo_args("expire"))
        action_id = json.loads(raw)["action_id"]
        frappe.db.set_value(
            "AI Pending Action", action_id, "expires_at", add_to_date(now_datetime(), hours=-2)
        )

        from erpnext_enhancements.assistant_tools.gating_api import confirm_action

        with self.assertRaises(frappe.ValidationError):
            confirm_action(action_id)
        self.assertEqual(frappe.db.get_value("AI Pending Action", action_id, "status"), "Expired")

    def test_expiry_sweep(self):
        self._set_gating(1)
        raw = self._registry().execute_tool("create_document", self._todo_args("sweep"))
        action_id = json.loads(raw)["action_id"]
        frappe.db.set_value(
            "AI Pending Action", action_id, "expires_at", add_to_date(now_datetime(), hours=-2)
        )

        from erpnext_enhancements.ai_governance.tasks import expire_stale_pending_actions

        expire_stale_pending_actions()
        self.assertEqual(frappe.db.get_value("AI Pending Action", action_id, "status"), "Expired")

    def test_action_log_is_append_only(self):
        self._set_gating(1)
        raw = self._registry().execute_tool("create_document", self._todo_args("log"))
        action_id = json.loads(raw)["action_id"]

        from erpnext_enhancements.assistant_tools.gating_api import confirm_action

        confirm_action(action_id)
        log_name = frappe.db.get_value("AI Pending Action", action_id, "action_log")
        log = frappe.get_doc("AI Action Log", log_name)
        log.summary = "tampered"
        with self.assertRaises(frappe.ValidationError):
            log.save(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            frappe.delete_doc("AI Action Log", log_name, ignore_permissions=True)

    def test_read_tools_pass_through(self):
        self._set_gating(1)
        result = self._registry().execute_tool(
            "workforce_time_status", {"mode": "now"}
        )
        self.assertIsInstance(result, dict)
        self.assertFalse(
            frappe.db.exists("AI Pending Action", {"tool_name": "workforce_time_status"})
        )


if __name__ == "__main__":
    unittest.main()
