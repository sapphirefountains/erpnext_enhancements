"""cancel_document, and the gate's refusal of a cancel dressed as an update (v1.540.0).

On 2026-09-25 an assistant proposed cancelling Material Request MAT-MR-2026-00014 as
``update_document`` with ``{"docstatus": 2}``, the shape the gate's own comments described as
a cancel. The card was approved and failed at execution: FAC 3.0.0's update_document refuses
every change to a submitted document. The only way through was a ``run_python_code`` card.

These tests pin both halves of the fix:

1. **The tool** calls ``doc.cancel()`` and nothing else, so Frappe's permission and
   linked-document checks apply; it refuses drafts, non-submittable doctypes, missing
   permission and a workflow that owns cancelling; it reports an already-cancelled document
   as done; it rolls back to its savepoint on any failure; and it never sets
   ``ignore_links``.
2. **The gate** classifies it like ``submit_document`` (mutating, HIGH risk, never exempt,
   no per-call decider), words its card, and refuses an ``update_document`` cancel before it
   becomes a card, pointing at this tool.

Bench-free, plain ``unittest`` under the shared stub from ``test_assistant_tools_schema``.
Stubs are installed in ``setUpModule``, not at import, and every patch is scoped to one test,
so this module can share the AI-gate CI step with the other gate suites.

Run: python -m unittest erpnext_enhancements.tests.test_assistant_cancel_document -v
"""

import html
import re
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_gate = None
tool_module = None
frappe = None

INCIDENT = {"doctype": "Material Request", "name": "MAT-MR-2026-00014", "data": {"docstatus": 2}}


def setUpModule():
    global _gate, tool_module, frappe
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
    from erpnext_enhancements.assistant_tools import cancel_document as cancel_module

    _gate = gate_module
    tool_module = cancel_module


class LinkExistsError(Exception):
    """Stands in for frappe.LinkExistsError (a ValidationError subclass on v16)."""


class FakeDoc:
    def __init__(self, doctype, name, docstatus=1, *, can_cancel=True, on_cancel=None):
        self.doctype = doctype
        self.name = name
        self.docstatus = docstatus
        self.status = {0: "Draft", 1: "Pending", 2: "Cancelled"}[docstatus]
        self.flags = types.SimpleNamespace()
        self.can_cancel = can_cancel
        self.on_cancel = on_cancel
        self.cancel_calls = 0
        self.comments = []
        self.permission_asks = []

    def get(self, key, default=None):
        return getattr(self, key, default)

    def has_permission(self, permtype):
        self.permission_asks.append(permtype)
        return self.can_cancel

    def cancel(self):
        self.cancel_calls += 1
        # What Frappe's own cancel checks: it must never have been told to skip links.
        assert not getattr(self.flags, "ignore_links", False), "ignore_links must never be set"
        if self.on_cancel:
            self.on_cancel()
        self.docstatus = 2
        self.status = "Cancelled"

    def add_comment(self, comment_type, text):
        self.comments.append((comment_type, text))


class ToolHarness(unittest.TestCase):
    """Runs CancelDocument.execute against a fake frappe with one document in it."""

    def setUp(self):
        self.doc = FakeDoc("Material Request", "MAT-MR-2026-00014")
        self.submittable = {"Material Request": True, "Item": False}
        self.db_calls = []
        self.workflow_owner = None

        def exists(doctype, name=None):
            if doctype == "DocType":
                return name in self.submittable
            return self.doc is not None and doctype == self.doc.doctype and name == self.doc.name

        db = types.SimpleNamespace(
            exists=exists,
            savepoint=lambda sp: self.db_calls.append(("savepoint", sp)),
            rollback=lambda save_point=None: self.db_calls.append(("rollback", save_point)),
        )
        utils = types.SimpleNamespace(
            escape_html=html.escape,
            strip_html=lambda text: re.sub(r"<[^>]+>", "", text),
        )
        patches = [
            mock.patch.object(frappe, "db", db, create=True),
            mock.patch.object(
                frappe,
                "get_meta",
                lambda doctype: types.SimpleNamespace(is_submittable=self.submittable.get(doctype, False)),
                create=True,
            ),
            mock.patch.object(frappe, "get_doc", lambda doctype, name: self.doc, create=True),
            mock.patch.object(frappe, "LinkExistsError", LinkExistsError, create=True),
            mock.patch.object(frappe, "utils", utils, create=True),
            mock.patch.object(frappe, "session", types.SimpleNamespace(user="nik@example.com"), create=True),
            mock.patch.object(tool_module, "_workflow_owns_cancel", lambda doctype: self.workflow_owner),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.tool = tool_module.CancelDocument()

    def run_tool(self, **overrides):
        args = {"doctype": "Material Request", "name": "MAT-MR-2026-00014"}
        args.update(overrides)
        return self.tool.execute(args)


class TestCancelDocumentTool(ToolHarness):
    def test_a_submitted_document_is_cancelled_through_frappes_own_cancel(self):
        result = self.run_tool(reason="Requested far more PVC fittings than the shelves hold.")
        self.assertTrue(result["success"])
        self.assertTrue(result["cancelled"])
        self.assertEqual((result["docstatus"], result["status"]), (2, "Cancelled"))
        self.assertEqual(self.doc.cancel_calls, 1)
        self.assertEqual(self.doc.permission_asks, ["cancel"])
        self.assertEqual(self.db_calls, [("savepoint", tool_module.SAVEPOINT)])
        self.assertTrue(any("Amend" in step for step in result["next_steps"]))

    def test_the_reason_goes_on_the_timeline_escaped_and_bounded(self):
        self.run_tool(reason="<b>too many</b> " + "x" * 600)
        (comment_type, text), = self.doc.comments
        self.assertEqual(comment_type, "Comment")
        self.assertIn("nik@example.com", text)
        self.assertIn("&lt;b&gt;too many&lt;/b&gt;", text)
        self.assertNotIn("<b>", text)
        self.assertLessEqual(text.count("x"), tool_module.REASON_MAX_LENGTH)

    def test_no_reason_leaves_no_comment(self):
        self.run_tool()
        self.assertEqual(self.doc.comments, [])

    def test_an_already_cancelled_document_is_done_not_failed(self):
        # A card confirmed after someone cancelled the same document in the Desk.
        self.doc = FakeDoc("Material Request", "MAT-MR-2026-00014", docstatus=2)
        result = self.run_tool()
        self.assertTrue(result["success"])
        self.assertFalse(result["cancelled"])
        self.assertTrue(result["already_cancelled"])
        self.assertEqual(self.doc.cancel_calls, 0)
        self.assertEqual(self.db_calls, [])

    def test_a_draft_is_refused_and_pointed_at_delete(self):
        self.doc = FakeDoc("Material Request", "MAT-MR-2026-00014", docstatus=0)
        result = self.run_tool()
        self.assertIs(result["success"], False)
        self.assertIn("draft", result["error"])
        self.assertIn("delete_document", result["error"])
        self.assertEqual(self.doc.cancel_calls, 0)

    def test_a_doctype_that_is_not_submittable_is_refused(self):
        self.doc = FakeDoc("Item", "PVC-1")
        result = self.run_tool(doctype="Item", name="PVC-1")
        self.assertIs(result["success"], False)
        self.assertIn("not a submittable DocType", result["error"])
        self.assertEqual(self.doc.cancel_calls, 0)

    def test_an_unknown_doctype_or_document_is_refused(self):
        result = self.run_tool(doctype="Materiel Request")
        self.assertIs(result["success"], False)
        self.assertIn("does not exist", result["error"])
        result = self.run_tool(name="MAT-MR-9999-00001")
        self.assertIs(result["success"], False)
        self.assertIn("not found", result["error"])
        self.assertEqual(self.doc.cancel_calls, 0)

    def test_missing_arguments_are_refused(self):
        for args in ({}, {"doctype": "Material Request"}, {"name": "MAT-MR-2026-00014"}, {"doctype": " ", "name": " "}):
            with self.subTest(args=args):
                result = self.tool.execute(args)
                self.assertIs(result["success"], False)
        self.assertIs(self.tool.execute(None)["success"], False)
        self.assertEqual(self.doc.cancel_calls, 0)

    def test_no_cancel_permission_is_refused_before_cancelling(self):
        self.doc = FakeDoc("Material Request", "MAT-MR-2026-00014", can_cancel=False)
        result = self.run_tool()
        self.assertIs(result["success"], False)
        self.assertIn("permission", result["error"])
        self.assertEqual(self.doc.cancel_calls, 0)

    def test_a_workflow_that_owns_cancelling_is_refused_and_named(self):
        self.workflow_owner = "Material Request Approval"
        result = self.run_tool()
        self.assertIs(result["success"], False)
        self.assertIn("run_workflow", result["error"])
        self.assertIn("Material Request Approval", result["error"])
        self.assertEqual(self.doc.cancel_calls, 0)

    def test_a_linked_submitted_document_is_refused_and_rolled_back(self):
        def linked():
            raise LinkExistsError(
                'Cannot delete or cancel because Material Request <a href="/app/material-request/MAT-MR-1">'
                "MAT-MR-1</a> is linked with Purchase Order PUR-ORD-2026-00001"
            )

        self.doc = FakeDoc("Material Request", "MAT-MR-2026-00014", on_cancel=linked)
        result = self.run_tool(reason="x")
        self.assertIs(result["success"], False)
        self.assertIn("Purchase Order PUR-ORD-2026-00001", result["error"])
        self.assertNotIn("<a ", result["error"])
        self.assertIn("never cancels linked documents", result["error"])
        self.assertEqual(
            self.db_calls,
            [("savepoint", tool_module.SAVEPOINT), ("rollback", tool_module.SAVEPOINT)],
        )
        self.assertEqual(self.doc.comments, [])

    def test_any_other_failure_rolls_back_to_the_savepoint_and_raises(self):
        # FAC's _safe_execute turns a raised ValidationError into a result without raising, so
        # the savepoint is what keeps a half-run cancel's first writes out of the commit.
        def ledger_failure():
            raise RuntimeError("stock ledger could not be reversed")

        self.doc = FakeDoc("Material Request", "MAT-MR-2026-00014", on_cancel=ledger_failure)
        with self.assertRaises(RuntimeError):
            self.run_tool(reason="x")
        self.assertEqual(self.db_calls[-1], ("rollback", tool_module.SAVEPOINT))
        self.assertEqual(self.doc.comments, [])

    def test_the_tool_never_skips_the_linked_document_check(self):
        source = Path(tool_module.__file__).read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        # Code only: the docstring explains that it is never set, and says so by name.
        docstring_free = code.replace(tool_module.__doc__, "")
        self.assertNotRegex(docstring_free, r"ignore_links\s*=")
        self.assertNotIn("ignore_links=True", docstring_free)


class TestWorkflowRule(unittest.TestCase):
    """_workflow_owns_cancel mirrors the Desk: plain Cancel only without a workflow cancel."""

    def run_rule(self, workflow_name, can_cancel):
        fake = types.ModuleType("frappe.model.workflow")
        fake.get_workflow_name = lambda doctype: workflow_name
        fake.can_cancel_document = lambda doctype: can_cancel
        model = sys.modules.get("frappe.model") or types.ModuleType("frappe.model")
        with mock.patch.dict(sys.modules, {"frappe.model": model, "frappe.model.workflow": fake}):
            return tool_module._workflow_owns_cancel("Material Request")

    def test_no_workflow(self):
        self.assertIsNone(self.run_rule(None, True))

    def test_a_workflow_without_a_cancel_transition_leaves_plain_cancel(self):
        self.assertIsNone(self.run_rule("MR Approval", True))

    def test_a_workflow_with_a_cancel_transition_owns_it(self):
        self.assertEqual(self.run_rule("MR Approval", False), "MR Approval")


class TestGateClassification(unittest.TestCase):
    def test_gated_like_submit(self):
        self.assertIn("cancel_document", _gate.APP_MUTATING)
        self.assertIn("cancel_document", _gate.HIGH_RISK)
        self.assertNotIn("cancel_document", _gate.EXPLICIT_READONLY)
        self.assertNotIn("cancel_document", _gate.EXEMPTABLE_TOOLS)
        # A decider on a HIGH_RISK tool would run it unconfirmed.
        self.assertNotIn("cancel_document", _gate.PER_CALL_GATED)
        self.assertEqual(_gate.classify_risk("cancel_document"), "High")
        self.assertEqual(_gate.classify_risk("submit_document"), "High")

    def test_it_advertises_itself_as_destructive(self):
        ann = _gate.annotations_for("cancel_document")
        self.assertEqual(
            (ann["readOnlyHint"], ann["destructiveHint"], ann["x-ee-mutation"], ann["x-ee-risk"]),
            (False, True, True, "high"),
        )

    def test_the_card_says_what_and_why(self):
        summary = _gate.summarize_tool_call(
            "cancel_document",
            {"doctype": "Material Request", "name": "MAT-MR-2026-00014", "reason": "Asks for 218 units."},
        )
        self.assertEqual(summary, "CANCEL Material Request MAT-MR-2026-00014 (permanent): “Asks for 218 units.”")
        bare = _gate.summarize_tool_call("cancel_document", {"doctype": "Material Request", "name": "MAT-MR-1"})
        self.assertEqual(bare, "CANCEL Material Request MAT-MR-1 (permanent)")
        long = _gate.summarize_tool_call("cancel_document", {"doctype": "X", "name": "Y", "reason": "r" * 200})
        self.assertTrue(long.endswith("…”"))
        self.assertLess(len(long), 120)


class TestUpdateDocumentCancelIsRefused(unittest.TestCase):
    def test_every_spelling_of_docstatus_two_is_refused_and_names_the_tool(self):
        for value in (2, "2", 2.0, " 2 "):
            with self.subTest(value=value):
                args = dict(INCIDENT, data={"docstatus": value})
                refusal = _gate._cancel_refusal("update_document", args)
                self.assertIn("cancel_document", refusal)
                self.assertIn("Material Request MAT-MR-2026-00014", refusal)
                self.assertIn("Nothing was queued", refusal)

    def test_everything_else_is_left_alone(self):
        for tool, args in (
            ("update_document", dict(INCIDENT, data={"docstatus": 1})),
            ("update_document", dict(INCIDENT, data={"remarks": "x"})),
            ("update_document", dict(INCIDENT, data={"docstatus": None})),
            ("update_document", dict(INCIDENT, data={"docstatus": True})),
            ("update_document", dict(INCIDENT, data="not a dict")),
            ("update_document", "not a dict"),
            ("create_document", {"doctype": "Material Request", "data": {"docstatus": 2}}),
            ("cancel_document", {"doctype": "Material Request", "name": "MAT-MR-1"}),
        ):
            with self.subTest(tool=tool, args=args):
                self.assertIsNone(_gate._cancel_refusal(tool, args))

    def test_the_refusal_comes_before_any_metadata_read(self):
        def get_meta(doctype):
            raise AssertionError("a cancel refusal must not need the doctype's metadata")

        tool = types.SimpleNamespace(name="update_document")
        with mock.patch.object(frappe, "get_meta", get_meta, create=True):
            refusal = _gate._precheck_refusal(tool, INCIDENT)
        self.assertIn("cancel_document", refusal)

    def test_the_select_check_itself_still_skips_a_cancel(self):
        # Unchanged: Frappe skips _validate() on cancel, so the pure check has nothing to say.
        self.assertEqual(_gate._precheck_problems(INCIDENT, lambda doctype: None), [])


class TestThroughTheGate(unittest.TestCase):
    """`_gated_execute` itself, with gating on: the incident call and the tool that replaces it."""

    def run_gate(self, tool_name, arguments, exempt=()):
        calls = {"executed": 0, "proposed": 0, "logged": []}

        def original(tool, args):
            calls["executed"] += 1
            return {"success": True, "result": "done"}

        def propose(tool, args):
            calls["proposed"] += 1
            return {"status": "awaiting_user_confirmation"}

        patches = [
            mock.patch.object(_gate, "_gating_enabled", lambda: True),
            mock.patch.object(_gate, "_propose", propose),
            mock.patch.object(_gate, "insert_action_log", lambda **kw: calls["logged"].append(kw)),
            mock.patch.object(_gate, "_exempt_doctypes", lambda: set(exempt)),
            mock.patch.object(frappe, "session", types.SimpleNamespace(user="nik@example.com"), create=True),
            mock.patch.object(frappe, "flags", types.SimpleNamespace(), create=True),
        ]
        for p in patches:
            p.start()
        try:
            response = _gate._gated_execute(types.SimpleNamespace(name=tool_name), original, arguments)
        finally:
            for p in reversed(patches):
                p.stop()
        return response, calls

    def test_the_incident_call_is_refused_with_no_card(self):
        response, calls = self.run_gate("update_document", INCIDENT)
        self.assertEqual((calls["executed"], calls["proposed"]), (0, 0))
        self.assertIs(response["success"], False)
        self.assertEqual(response["error_type"], "AIGateValidationError")
        self.assertIn("cancel_document", response["error"])
        # Recorded, like every other queue-time refusal.
        (log,) = calls["logged"]
        self.assertIs(log["success"], False)
        self.assertEqual(log["tool_name"], "update_document")

    def test_cancel_document_is_always_a_card(self):
        args = {"doctype": "Material Request", "name": "MAT-MR-2026-00014", "reason": "x"}
        response, calls = self.run_gate("cancel_document", args)
        self.assertEqual((calls["executed"], calls["proposed"]), (0, 1))

    def test_even_when_its_doctype_is_exempt(self):
        # The settings allowlist covers create_document and update_document only.
        args = {"doctype": "Material Request", "name": "MAT-MR-2026-00014"}
        response, calls = self.run_gate("cancel_document", args, exempt=("Material Request",))
        self.assertEqual((calls["executed"], calls["proposed"]), (0, 1))


if __name__ == "__main__":
    unittest.main()
