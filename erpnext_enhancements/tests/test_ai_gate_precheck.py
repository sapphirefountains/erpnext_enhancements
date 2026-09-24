"""Queue-time validation in the AI write gate (v1.533.0).

On 2026-09-24 an assistant queued 21 `update_document` cards, each setting a Task's status
to "Cancelled", which is not an option on this site ("Canceled"). Nik confirmed all 21 in one
batch and every one failed on execution. These tests pin the fix: a create/update whose Select
value Frappe would refuse on save gets that error back at once and no card is created, a
valid one is queued exactly as before, and a pre-check that itself fails still queues the card.

Bench-free, plain ``unittest`` under the shared stub from ``test_assistant_tools_schema``.
Stubs are installed in ``setUpModule``, not at import, for the reason ``test_ai_gate_unit``
records. Every patch is scoped to a test with ``mock.patch.object`` so this module can share
a CI step with the other gate suites.

Run: python -m unittest erpnext_enhancements.tests.test_ai_gate_precheck -v
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_gate = None
frappe = None

# The Task status options on production (a Property Setter replaces ERPNext core's list).
TASK_STATUS_OPTIONS = "Open\nWorking\nInvoiced\nCompleted\nCanceled\nPending Review\nOverdue\nTemplate"


def _df(fieldname, fieldtype, options=None, label=None):
    return SimpleNamespace(fieldname=fieldname, fieldtype=fieldtype, options=options, label=label)


METAS = {
    "Task": SimpleNamespace(
        fields=[
            _df("subject", "Data", label="Subject"),
            _df("status", "Select", TASK_STATUS_OPTIONS, "Status"),
            _df("priority", "Select", "Low\nMedium\nHigh\nUrgent", "Priority"),
            _df("naming_series", "Select", "TASK-.YYYY.-", "Series"),
            _df("depends_on", "Table", "Task Depends On", "Dependencies"),
        ]
    ),
    "Task Depends On": SimpleNamespace(
        fields=[
            _df("task", "Link", "Task", "Task"),
            _df("link_type", "Select", "\nBlocks\nFollows", "Link Type"),
        ]
    ),
    "Quirks": SimpleNamespace(
        fields=[
            _df("placeholder", "Select", "[Select]", "Placeholder"),
            _df("loading", "Select", "Loading...", "Loading"),
            _df("no_options", "Select", None, "No Options"),
            _df("blank_only", "Select", "\n\n", "Blank Only"),
        ]
    ),
}


def fake_get_meta(doctype):
    return METAS[doctype]


def setUpModule():
    global _gate, frappe
    from erpnext_enhancements.tests.test_assistant_tools_schema import install_stubs

    install_stubs()

    import frappe as frappe_module

    frappe = frappe_module
    if not hasattr(frappe, "whitelist"):
        frappe.whitelist = lambda *a, **k: (lambda f: f)
    if not hasattr(frappe, "flags"):
        frappe.flags = type("Flags", (), {})()
    if not hasattr(frappe, "session"):
        frappe.session = type("Session", (), {"user": "tester@example.com"})()

    from erpnext_enhancements.assistant_tools import _gate as gate_module

    _gate = gate_module


class TestSelectProblems(unittest.TestCase):
    """The pure half: what Frappe v16's `_validate_selects` would refuse."""

    def problems(self, doctype, data):
        return _gate._precheck_problems({"doctype": doctype, "data": data}, fake_get_meta)

    def test_the_incident_value_is_refused_with_frappes_own_wording(self):
        found = self.problems("Task", {"status": "Cancelled"})
        self.assertEqual(len(found), 1)
        self.assertEqual(
            found[0],
            'Task Status cannot be "Cancelled". It should be one of "Open", "Working", '
            '"Invoiced", "Completed", "Canceled", "Pending Review", "Overdue", "Template".',
        )

    def test_the_site_spelling_passes(self):
        self.assertEqual(self.problems("Task", {"status": "Canceled"}), [])

    def test_the_value_is_stripped_like_frappe_does(self):
        self.assertEqual(self.problems("Task", {"status": "  Canceled "}), [])

    def test_empty_and_falsy_values_are_not_checked(self):
        for value in ("", None, 0):
            with self.subTest(value=value):
                self.assertEqual(self.problems("Task", {"status": value}), [])

    def test_only_fields_in_data_are_checked(self):
        self.assertEqual(self.problems("Task", {"subject": "anything"}), [])

    def test_non_select_fields_are_ignored(self):
        self.assertEqual(self.problems("Task", {"subject": "Cancelled"}), [])

    def test_naming_series_is_skipped_like_frappe(self):
        self.assertEqual(self.problems("Task", {"naming_series": "ANY-.####"}), [])

    def test_every_bad_field_is_reported(self):
        found = self.problems("Task", {"status": "Cancelled", "priority": "Critical"})
        self.assertEqual(len(found), 2)

    def test_child_rows_are_checked_and_deleted_rows_are_not(self):
        found = self.problems(
            "Task",
            {
                "depends_on": [
                    {"task": "TASK-1", "link_type": "Blocks"},
                    {"task": "TASK-2", "link_type": "Precedes"},
                    {"name": "row-3", "link_type": "Nonsense", "_delete": True},
                    "not a row",
                ]
            },
        )
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith('Dependencies row 2: Link Type cannot be "Precedes"'))

    def test_placeholder_and_missing_options_are_skipped(self):
        data = {"placeholder": "x", "loading": "x", "no_options": "x"}
        self.assertEqual(self.problems("Quirks", data), [])

    def test_blank_only_options_refuse_like_frappe_does(self):
        """Frappe's `if not filter(None, options)` never fires on Python 3, so it refuses."""
        self.assertEqual(len(self.problems("Quirks", {"blank_only": "x"})), 1)

    def test_unrecognised_shapes_yield_nothing(self):
        for arguments in (None, [], {}, {"doctype": "Task"}, {"doctype": "Task", "data": "x"},
                          {"doctype": "", "data": {"status": "Cancelled"}}):
            with self.subTest(arguments=arguments):
                self.assertEqual(_gate._precheck_problems(arguments, fake_get_meta), [])


class _GateHarness(unittest.TestCase):
    """Drives `_gated_execute` with gating on and the card path observed, never faked away."""

    def setUp(self):
        self.calls = {"executed": 0, "proposed": 0, "logged": [], "errors": []}
        patches = [
            mock.patch.object(_gate, "_gating_enabled", lambda: True),
            mock.patch.object(_gate, "_exempt_doctypes", lambda: set()),
            mock.patch.object(_gate, "_propose", self._propose),
            mock.patch.object(_gate, "insert_action_log", self._log),
            mock.patch.object(frappe, "get_meta", fake_get_meta, create=True),
            mock.patch.object(frappe, "log_error", self._log_error, create=True),
            mock.patch.object(frappe, "get_traceback", lambda *a, **k: "traceback", create=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _propose(self, tool, arguments):
        self.calls["proposed"] += 1
        return {"success": True, "result": "card queued"}

    def _log(self, **kwargs):
        self.calls["logged"].append(kwargs)

    def _log_error(self, *args, **kwargs):
        self.calls["errors"].append(args)

    def _original(self, tool, arguments):
        self.calls["executed"] += 1
        return {"success": True, "result": "ran"}

    def run_tool(self, tool_name, arguments):
        tool = type("T", (), {"name": tool_name})()
        return _gate._gated_execute(tool, self._original, arguments)


class TestGateRefusesBeforeQueueing(_GateHarness):
    def test_off_options_update_is_refused_and_no_card_is_created(self):
        """The 2026-09-24 incident, exactly as the assistant sent it."""
        response = self.run_tool(
            "update_document",
            {"doctype": "Task", "name": "TASK-2026-02241", "data": {"status": "Cancelled"}},
        )
        self.assertEqual(self.calls["proposed"], 0, "a card was created for a write that cannot run")
        self.assertEqual(self.calls["executed"], 0)
        self.assertFalse(response["success"])
        self.assertEqual(response["error_type"], "AIGateValidationError")
        self.assertIn('Status cannot be "Cancelled"', response["error"])
        self.assertIn('"Canceled"', response["error"])
        self.assertIn("Nothing was queued for confirmation", response["error"])

    def test_the_refusal_is_recorded_in_the_action_log(self):
        self.run_tool(
            "update_document",
            {"doctype": "Task", "name": "TASK-2026-02241", "data": {"status": "Cancelled"}},
        )
        self.assertEqual(len(self.calls["logged"]), 1)
        entry = self.calls["logged"][0]
        self.assertFalse(entry["success"])
        self.assertEqual(entry["error_type"], "AIGateValidationError")
        self.assertIn("Not queued", entry["summary"])

    def test_off_options_create_is_refused_too(self):
        response = self.run_tool(
            "create_document",
            {"doctype": "Task", "data": {"subject": "x", "priority": "Critical"}},
        )
        self.assertEqual(self.calls["proposed"], 0)
        self.assertEqual(response["error_type"], "AIGateValidationError")


class TestGateStillQueuesWhatCanRun(_GateHarness):
    def test_a_valid_close_still_creates_a_card(self):
        """Closing a Task is ADR 0016 §6's confirmation; the check must not bypass it."""
        response = self.run_tool(
            "update_document",
            {"doctype": "Task", "name": "TASK-2026-02241", "data": {"status": "Canceled"}},
        )
        self.assertEqual(self.calls["proposed"], 1)
        self.assertEqual(self.calls["executed"], 0)
        self.assertEqual(response, {"success": True, "result": "card queued"})
        self.assertEqual(self.calls["logged"], [])

    def test_a_valid_create_still_creates_a_card(self):
        self.run_tool("create_document", {"doctype": "Task", "data": {"priority": "High"}})
        self.assertEqual(self.calls["proposed"], 1)

    def test_a_failing_pre_check_still_creates_the_card_and_is_logged(self):
        def broken_get_meta(doctype):
            raise RuntimeError("meta cache unavailable")

        with mock.patch.object(frappe, "get_meta", broken_get_meta, create=True):
            response = self.run_tool(
                "update_document",
                {"doctype": "Task", "name": "TASK-1", "data": {"status": "Cancelled"}},
            )
        self.assertEqual(self.calls["proposed"], 1, "a pre-check failure dropped the write")
        self.assertEqual(response, {"success": True, "result": "card queued"})
        self.assertEqual(len(self.calls["errors"]), 1)
        self.assertIn("queued for confirmation anyway", self.calls["errors"][0][0])

    def test_an_unknown_doctype_goes_on_to_propose_without_an_error_log(self):
        class DoesNotExistError(Exception):
            pass

        def missing(doctype):
            raise DoesNotExistError(doctype)

        with mock.patch.object(frappe, "DoesNotExistError", DoesNotExistError, create=True), \
                mock.patch.object(frappe, "get_meta", missing, create=True):
            self.run_tool("update_document", {"doctype": "Taks", "name": "X", "data": {"a": 1}})
        self.assertEqual(self.calls["proposed"], 1)
        self.assertEqual(self.calls["errors"], [])

    def test_other_mutating_tools_are_not_pre_checked(self):
        looked_up = []

        def recording_get_meta(doctype):
            looked_up.append(doctype)
            return fake_get_meta(doctype)

        with mock.patch.object(frappe, "get_meta", recording_get_meta, create=True):
            self.run_tool("delete_document", {"doctype": "Task", "name": "TASK-1"})
        self.assertEqual(looked_up, [])
        self.assertEqual(self.calls["proposed"], 1)

    def test_an_auto_approved_task_update_is_untouched(self):
        """ADR 0016 §6: a non-closing status runs without a card; the check is not in its way."""
        response = self.run_tool(
            "update_document",
            {"doctype": "Task", "name": "TASK-1", "data": {"status": "Working"}},
        )
        self.assertEqual(self.calls["executed"], 1)
        self.assertEqual(self.calls["proposed"], 0)
        self.assertEqual(response, {"success": True, "result": "ran"})


if __name__ == "__main__":
    unittest.main()
