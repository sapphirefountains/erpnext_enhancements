"""Bench-free tests for the AI gate's private-content denylist (v1.538.0, WI-080 PR 1).

The denylist is the one refusal in ``assistant_tools/_gate.py`` that no role, flag, setting or
confirmation can get past. Since v1.538.0 it holds two doctypes, for two reasons:

- ``Triton Chat Attachment``: one person's private assistant uploads (since v1.426.0);
- ``Knowledge Article Version``: knowledge-base drafts and the history behind them, text no second
  person has approved (ADR 0017). The Knowledge Base's rule is that only approved text reaches an
  assistant, and raw SQL ignores the DocPerm that keeps readers out of drafts.

What must hold, and why each one is a test rather than a hope:

- the Version doctype is refused on **every** path the gate knows: a ``doctype`` argument on any
  tool, ``fetch``'s ``"<doctype>/<name>"`` id, ``run_python_code``'s ``data_query.doctype``, and
  the free text of ``run_database_query`` and ``run_python_code``;
- raw SQL is refused through comment, backtick, quoting, case and whitespace variants, because
  string-matching SQL only works if it refuses on contact;
- ``tabKnowledge Article``, the **published** doctype, is NOT refused. Its needle is a prefix of the
  Version's, so a careless needle would break every generic tool on the text staff read anyway,
  and the WI-080 acceptance queries that check a deploy;
- ``Triton Chat Attachment`` is still refused, with its message unchanged;
- the refusal happens before the confirm-flow bypass and before the gating switch, so it holds
  with gating off;
- both KB doctypes are in ``NEVER_EXEMPT``, so no settings row can let a write to either skip its
  card.

Plain ``unittest`` under ``test_assistant_tools_schema.install_stubs``, which is why it sits on the
"AI gate + assistant-tool contract" CI step. Every frappe attribute is patched per test and
restored.

Run: python -m unittest erpnext_enhancements.tests.test_ai_gate_denylist -v
"""

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_gate = None

VERSION = "Knowledge Article Version"
ARTICLE = "Knowledge Article"
ATTACHMENT = "Triton Chat Attachment"

#: Every FAC 3.0.0 tool that takes a top-level ``doctype`` argument (checked against the v3.0.0
#: tag's inputSchemas, 2026-09-25), plus two names FAC does not have yet. The gate compares the
#: ``doctype`` argument on every tool rather than a list, and the made-up names hold it to that.
DOCTYPE_TOOLS = (
    "get_document",
    "list_documents",
    "search_documents",
    "create_document",
    "update_document",
    "delete_document",
    "submit_document",
    "get_doctype_info",
    "get_pending_approvals",
    "run_workflow",
    "analyze_business_data",
    "create_dashboard",
    "create_dashboard_chart",
    "a_tool_fac_adds_next_year",
    "",
)

#: Raw SQL that reaches the Version table. Each must be refused under both argument names.
VERSION_SQL = (
    "select name from `tabKnowledge Article Version`",
    "SELECT body FROM `tabKnowledge Article Version` WHERE article = 'KB-0612'",
    "select name from tabKnowledge Article Version",
    'select name from "tabKnowledge Article Version"',
    "select name from `tabKNOWLEDGE article VERSION`",
    "select name\nfrom\n  `tabKnowledge\n   Article\tVersion`",
    "SELECT/*x*/ NAME FROM   tabKnowledge/**/Article/* hidden */Version",
    "select name from `tabKnowledge Article /* not a comment you can hide behind */ Version`",
    "select name from `tabKnowledge Article Version` -- trailing comment",
    "select name from `tabKnowledge Article Version` # trailing comment",
    "select a.name from `tabKnowledge Article` a # the published one\n"
    "join `tabKnowledge Article Version` v on v.article = a.name",
    "select name from /* `tabKnowledge Article` */ `tabKnowledge Article Version`",
    "select table_name from information_schema.tables "
    "where table_name = 'tabKnowledge Article Version'",
    "select count(*) from `tabKnowledge Article Version` where review_state = 'Draft'",
)

#: Queries that must keep working: the WI-080 PR 1 acceptance checks, and ordinary reads of the
#: published doctype. Every one names `Knowledge Article` and none reaches a draft.
ARTICLE_SQL = (
    "SELECT name, module, is_submittable, track_changes, has_web_view, show_in_global_search "
    "FROM tabDocType WHERE name LIKE 'Knowledge Article%'",
    "SELECT COUNT(*) FROM `tabDeleted Document` WHERE deleted_doctype='DocType' "
    "AND deleted_name LIKE 'Knowledge%'",
    "SELECT parent, role, share, submit, `delete`, export FROM tabDocPerm "
    "WHERE parent LIKE 'Knowledge Article%'",
    "SELECT COUNT(*) FROM `tabCustom DocPerm` WHERE parent LIKE 'Knowledge Article%'",
    "SELECT name, author, approved_by, version_number FROM `tabKnowledge Article`",
    "SELECT COUNT(*) FROM tabFile WHERE attached_to_doctype LIKE 'Knowledge Article%' "
    "AND is_private = 0",
    "select name, title, live_version from `tabKnowledge Article` where status = 'Published'",
    "select * from `tabKnowledge Article` where version_number > 1",
)

#: The Triton Chat Attachment refusal, word for word as it read before v1.538.0 made the message
#: a per-doctype map. The map must not have changed what a model is told about that doctype.
ATTACHMENT_MESSAGE = (
    "Refused: Triton Chat Attachment holds one person's private assistant context and is not "
    "readable through the generic Frappe tools, by any role, with AI gating on or off. "
    "Raw SQL consults no permission hook, so this refusal is the only thing standing "
    "between a System Manager and everybody else's rows. Ask the person, or read it as "
    "yourself in the Triton widget."
)


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
        frappe.session = type("Session", (), {"user": "tester@example.com"})()

    from erpnext_enhancements.assistant_tools import _gate as gate_module

    _gate = gate_module


class TestTheLists(unittest.TestCase):
    def test_the_version_doctype_is_denylisted_and_the_published_one_is_not(self):
        self.assertIn(VERSION, _gate.DENYLIST_DOCTYPES)
        self.assertIn(ATTACHMENT, _gate.DENYLIST_DOCTYPES)
        self.assertNotIn(ARTICLE, _gate.DENYLIST_DOCTYPES)

    def test_every_denylisted_doctype_has_its_own_reason(self):
        self.assertEqual(set(_gate.DENYLIST_REASONS), set(_gate.DENYLIST_DOCTYPES))
        for doctype, reason in _gate.DENYLIST_REASONS.items():
            with self.subTest(doctype=doctype):
                self.assertTrue(reason.strip())

    def test_both_kb_doctypes_are_never_exempt(self):
        self.assertIn(ARTICLE, _gate.NEVER_EXEMPT)
        self.assertIn(VERSION, _gate.NEVER_EXEMPT)

    def test_a_settings_row_cannot_exempt_either_kb_doctype(self):
        rows = [
            types.SimpleNamespace(document_type=ARTICLE, exempt_until=None),
            types.SimpleNamespace(document_type=VERSION, exempt_until=None),
            types.SimpleNamespace(document_type="Comment", exempt_until=None),
        ]
        settings = types.SimpleNamespace(get=lambda key: rows if key == "ai_exempt_doctypes" else None)
        with mock.patch.object(_gate.frappe, "get_cached_doc", lambda *a, **k: settings, create=True):
            exempt = _gate._exempt_doctypes()
        self.assertEqual(exempt, {"Comment"})

    def test_the_needles_keep_the_published_doctype_out(self):
        """`knowledgearticle` must never be a needle: it is a prefix of the Version's needle and
        would refuse every read of the published text."""
        needles = {needle for needle, _doctype in _gate._denylist_needles()}
        self.assertIn("knowledgearticleversion", needles)
        self.assertNotIn("knowledgearticle", needles)


class TestTheVersionDoctypeIsRefusedOnEveryPath(unittest.TestCase):
    def test_a_doctype_argument_on_any_tool(self):
        for tool in DOCTYPE_TOOLS:
            with self.subTest(tool=tool):
                self.assertEqual(_gate.denylist_hit(tool, {"doctype": VERSION, "name": "KBV-00001"}), VERSION)

    def test_a_doctype_argument_however_it_is_spelled(self):
        for spelling in (
            VERSION,
            f"  {VERSION} ",
            "knowledge article version",
            "KNOWLEDGE ARTICLE VERSION",
            "Knowledge  Article\tVersion",
        ):
            with self.subTest(spelling=spelling):
                self.assertEqual(_gate.denylist_hit("get_document", {"doctype": spelling}), VERSION)

    def test_fetch_by_id(self):
        for doc_id in (
            f"{VERSION}/KBV-00001",
            f" {VERSION}/KBV-00001",
            "knowledge article version/KBV-00001",
            f"{VERSION}/a name/with slashes",
        ):
            with self.subTest(id=doc_id):
                self.assertEqual(_gate.denylist_hit("fetch", {"id": doc_id}), VERSION)

    def test_run_python_code_data_query(self):
        arguments = {"code": "print(len(data))", "data_query": {"doctype": VERSION, "fields": ["*"]}}
        self.assertEqual(_gate.denylist_hit("run_python_code", arguments), VERSION)

    def test_run_python_code_text(self):
        for code in (
            f'rows = frappe.get_all("{VERSION}", fields=["body"])',
            'rows = frappe.db.sql("select body from `tabKnowledge Article Version`")',
            'dt = "Knowledge Article " + "Version"\nrows = frappe.get_all(dt)',
        ):
            with self.subTest(code=code):
                self.assertEqual(_gate.denylist_hit("run_python_code", {"code": code}), VERSION)

    def test_raw_sql_every_variant_and_both_argument_names(self):
        for query in VERSION_SQL:
            for key in ("query", "sql"):
                with self.subTest(query=query, key=key):
                    self.assertEqual(_gate.denylist_hit("run_database_query", {key: query}), VERSION)

    def test_an_alias_named_version_is_refused_too(self):
        """Accepted over-refusal, pinned so it is known rather than discovered. The contact match
        cannot tell a table from an alias, and does not try to (see _normalise_for_denylist)."""
        query = "select version.name from `tabKnowledge Article` version"
        self.assertEqual(_gate.denylist_hit("run_database_query", {"query": query}), VERSION)


class TestThePublishedDoctypeIsNotRefused(unittest.TestCase):
    def test_raw_sql_on_the_published_doctype_and_the_acceptance_queries(self):
        for query in ARTICLE_SQL:
            for key in ("query", "sql"):
                with self.subTest(query=query, key=key):
                    self.assertIsNone(_gate.denylist_hit("run_database_query", {key: query}))

    def test_generic_tools_on_the_published_doctype(self):
        for tool in DOCTYPE_TOOLS:
            with self.subTest(tool=tool):
                self.assertIsNone(_gate.denylist_hit(tool, {"doctype": ARTICLE, "name": "KB-0612"}))
        self.assertIsNone(_gate.denylist_hit("fetch", {"id": f"{ARTICLE}/KB-0612"}))
        self.assertIsNone(_gate.denylist_hit("run_python_code", {"data_query": {"doctype": ARTICLE}}))

    def test_other_shapes_are_left_alone(self):
        self.assertIsNone(_gate.denylist_hit("fetch", {"id": "Customer/CUST-00001"}))
        self.assertIsNone(_gate.denylist_hit("fetch", {"id": "no slash at all"}))
        self.assertIsNone(_gate.denylist_hit("fetch", {"id": None}))
        self.assertIsNone(_gate.denylist_hit("run_python_code", {"data_query": "not an object"}))
        self.assertIsNone(_gate.denylist_hit("get_document", None))
        self.assertIsNone(_gate.denylist_hit("get_document", ["not", "a", "dict"]))
        # The id shape is read only for fetch, whose contract it is.
        self.assertIsNone(_gate.denylist_hit("get_document", {"id": f"{VERSION}/KBV-00001"}))


class TestTritonChatAttachmentIsStillRefused(unittest.TestCase):
    def test_every_path(self):
        self.assertEqual(_gate.denylist_hit("get_document", {"doctype": ATTACHMENT}), ATTACHMENT)
        self.assertEqual(_gate.denylist_hit("fetch", {"id": f"{ATTACHMENT}/abc123"}), ATTACHMENT)
        self.assertEqual(
            _gate.denylist_hit("run_python_code", {"data_query": {"doctype": ATTACHMENT}}), ATTACHMENT
        )
        for query in (
            "select name from `tabTriton Chat Attachment`",
            "SELECT/*x*/ NAME FROM   tabTRITON chat attachment",
        ):
            with self.subTest(query=query):
                self.assertEqual(_gate.denylist_hit("run_database_query", {"query": query}), ATTACHMENT)

    def test_its_message_did_not_change(self):
        self.assertEqual(_gate._denylist_refusal_message(ATTACHMENT), ATTACHMENT_MESSAGE)


class TestRefusalMessages(unittest.TestCase):
    def test_the_version_message_names_drafts_and_points_at_the_published_doctype(self):
        message = _gate._denylist_refusal_message(VERSION)
        self.assertTrue(message.startswith(f"Refused: {VERSION} "))
        self.assertIn("draft", message)
        self.assertIn("approved", message)
        self.assertIn("read the Knowledge Article instead", message)
        self.assertNotIn("private assistant context", message)

    def test_a_doctype_with_no_reason_still_gets_a_refusal(self):
        message = _gate._denylist_refusal_message("Some Future Doctype")
        self.assertTrue(message.startswith("Refused: Some Future Doctype "))
        self.assertIn("not readable through the generic Frappe tools", message)


class TestTheRefusalComesFirst(unittest.TestCase):
    """Through ``_gated_execute`` itself: the refusal must hold with gating off and with the
    confirm-flow bypass flag set, because both are switched off below it."""

    def _run(self, tool_name, arguments, gating=False, bypass=False):
        tool = type("T", (), {"name": tool_name})()
        calls = {"executed": 0, "logged": []}

        def original(t, args):
            calls["executed"] += 1
            return {"success": True, "result": "rows"}

        def _log(**kwargs):
            calls["logged"].append(kwargs)

        flags = types.SimpleNamespace(ai_gate_bypass=bypass)
        with mock.patch.object(_gate, "_gating_enabled", lambda: gating), mock.patch.object(
            _gate, "insert_action_log", _log
        ), mock.patch.object(_gate.frappe, "flags", flags, create=True):
            response = _gate._gated_execute(tool, original, arguments)
        return response, calls

    def test_refused_with_gating_off_and_with_the_bypass_flag(self):
        cases = (
            ("get_document", {"doctype": VERSION, "name": "KBV-00001"}),
            ("list_documents", {"doctype": VERSION}),
            ("fetch", {"id": f"{VERSION}/KBV-00001"}),
            ("run_database_query", {"query": "select body from `tabKnowledge Article Version`"}),
            ("run_python_code", {"code": "x = 1", "data_query": {"doctype": VERSION}}),
        )
        for tool_name, arguments in cases:
            for gating, bypass in ((False, False), (True, False), (False, True)):
                with self.subTest(tool=tool_name, gating=gating, bypass=bypass):
                    response, calls = self._run(tool_name, arguments, gating=gating, bypass=bypass)
                    self.assertEqual(calls["executed"], 0)
                    self.assertFalse(response["success"])
                    self.assertEqual(response["error"], _gate._denylist_refusal_message(VERSION))
                    self.assertEqual(len(calls["logged"]), 1)
                    logged = calls["logged"][0]
                    self.assertFalse(logged["success"])
                    self.assertEqual(logged["risk"], "High")
                    self.assertIn(VERSION, logged["summary"])

    def test_the_published_doctype_passes_through_with_gating_off(self):
        response, calls = self._run("get_document", {"doctype": ARTICLE, "name": "KB-0612"})
        self.assertEqual(calls["executed"], 1)
        self.assertEqual(calls["logged"], [])
        self.assertTrue(response["success"])


if __name__ == "__main__":
    unittest.main()
