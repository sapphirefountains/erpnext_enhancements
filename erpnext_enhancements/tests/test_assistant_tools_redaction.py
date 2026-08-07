"""What the A1 read tools must never put in a chat transcript.

Three of the four tools added by TASK-2026-01188 wrap a function that returns
more than a reader should see, and in each case the extra is not merely noisy:

* ``get_pickup_route_data`` returns ``api_key`` — a live, **billable** Google Maps
  *browser* key. Its real caller is a dialog that has to draw a map; an MCP client
  is not a browser, and a key in a transcript gets quoted back, logged, and
  eventually pasted somewhere it can be spent.
* Contract Signature Request carries the signing **evidence**: the token hashes
  that make a signing URL unforgeable, the agreement text as signed, the drawn
  signature, the signer's IP and user agent, the consent wording. That is what
  makes the record admissible, and none of it is an answer to "where is this
  contract".
* Training Certificate carries ``verification_code``, which is what proves a
  certificate genuine to a third party.

The tests are **static** — they read the tool sources and the doctype JSONs — for
the same reason the training wire tests are: there is no bench in CI, and a
redaction that only holds when a particular code path runs is not a redaction.

The important assertion is the last class: every sensitive field is checked
against the **live doctype**, so a field renamed or added later cannot silently
drop out of the list it is supposed to be in.

Run: python -m unittest erpnext_enhancements.tests.test_assistant_tools_redaction
"""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
TOOLS = APP / "assistant_tools"

SIGNING_EVIDENCE = (
    "token_hash",
    "previous_token_hash",
    "agreement_html",
    "document_snapshot",
    "signature_image",
    "signer_ip_claimed",
    "signer_ip_peer",
    "user_agent",
    "consent_text",
)


def tool_source(name):
    return (TOOLS / f"{name}.py").read_text(encoding="utf-8")


def code_only(source):
    """Source with comments and docstrings stripped.

    Every one of these fields is *named* in a comment explaining why it is not
    returned, so a naive substring search finds them all and passes nothing.
    """
    source = re.sub(r'""".*?"""', "", source, flags=re.S)
    source = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    return source


def doctype_fields(module, doctype_dir):
    path = APP / module / "doctype" / doctype_dir / f"{doctype_dir}.json"
    return {f["fieldname"] for f in json.loads(path.read_text(encoding="utf-8"))["fields"]}


class TestTheScanWorks(unittest.TestCase):
    def test_the_sources_are_readable(self):
        for name in (
            "contract_signing_status",
            "project_pickup_route",
            "training_course_catalog",
            "training_compliance_status",
        ):
            self.assertGreater(len(tool_source(name)), 1000, name)

    def test_comments_are_stripped_before_searching(self):
        """Guards every assertion below: these fields are all named in prose that
        explains why they are withheld, so a search that counted comments would
        find them everywhere and prove nothing."""
        stripped = code_only(tool_source("contract_signing_status"))
        self.assertNotIn("the frozen text as signed", stripped)


class TestContractSigningEvidenceStaysOnTheServer(unittest.TestCase):
    def test_no_evidence_field_is_requested(self):
        code = code_only(tool_source("contract_signing_status"))
        # The NEVER_RETURN tuple is a declaration, not a request — drop it before
        # looking, or the tool's own manifest of what it withholds trips the test.
        code = re.sub(r"NEVER_RETURN = \(.*?\)", "", code, flags=re.S)
        for field in SIGNING_EVIDENCE:
            self.assertNotIn(field, code, f"contract_signing_status references {field}")

    def test_the_projection_is_written_out_not_a_wildcard(self):
        """`fields=["*"]` on this doctype is the whole problem in one keystroke."""
        code = code_only(tool_source("contract_signing_status"))
        self.assertNotIn('"*"', code)
        self.assertIn("REQUEST_FIELDS", code)

    def test_every_evidence_field_still_exists_on_the_doctype(self):
        """If one is renamed, this list is stale and the rename has to reach here
        too — the point of the test is that the redaction cannot drift."""
        fields = doctype_fields(
            "project_enhancements", "contract_signature_request"
        )
        missing = sorted(set(SIGNING_EVIDENCE) - fields)
        self.assertEqual(
            missing,
            [],
            f"redaction list names fields Contract Signature Request no longer has: {missing}",
        )

    def test_the_backlog_is_ordered_by_first_sent_on(self):
        """Every reminder rewrites `sent_on`, so a backlog ordered by it reports
        the most-chased contract as the freshest — the exact inversion the digest
        job documents at esign/tasks.py."""
        code = code_only(tool_source("contract_signing_status"))
        self.assertIn("first_sent_on asc", code)

    def test_the_backlog_is_permission_aware(self):
        """`frappe.get_all` ignores permissions; this is the one view not anchored
        to a document the caller named."""
        code = code_only(tool_source("contract_signing_status"))
        self.assertIn("frappe.get_list(", code)


class TestTheMapsKeyNeverLeaves(unittest.TestCase):
    def test_the_key_is_stripped(self):
        code = code_only(tool_source("project_pickup_route"))
        self.assertIn("NEVER_RETURN", code)
        self.assertIn("data.pop(", code)

    def test_both_client_only_keys_are_named(self):
        source = tool_source("project_pickup_route")
        match = re.search(r"NEVER_RETURN = \((.*?)\)", source, flags=re.S)
        self.assertIsNotNone(match, "NEVER_RETURN not found")
        self.assertIn("api_key", match.group(1))
        self.assertIn("use_routes_api", match.group(1))

    def test_the_wrapped_endpoint_really_does_return_it(self):
        """The stripping is only meaningful while the source still leaks it. If
        pickup_routing stops returning api_key, this test says so rather than
        leaving a pop() nobody understands."""
        source = (APP / "api/pickup_routing.py").read_text(encoding="utf-8")
        self.assertIn('"api_key"', source)

    def test_address_less_suppliers_are_reported_not_dropped(self):
        code = code_only(tool_source("project_pickup_route"))
        self.assertIn("unroutable_stops", code)


class TestTrainingAggregatesStayAggregate(unittest.TestCase):
    def test_item_analysis_does_not_read_per_learner_columns(self):
        """Training Attempt Question carries `user`, `given_answer` and `attempt`.
        Any of them turns an aggregate back into a record of what one person did."""
        code = code_only(tool_source("training_course_catalog"))
        match = re.search(r'"Training Attempt Question",(.*?)\)', code, flags=re.S)
        self.assertIsNotNone(match, "the item-analysis query was not found")
        query = match.group(1)
        for field in ("user", "given_answer", "attempt", "answered_on"):
            self.assertNotIn(f'"{field}"', query, f"item_analysis reads {field}")

    def test_thin_questions_are_withheld(self):
        code = code_only(tool_source("training_course_catalog"))
        self.assertIn("MIN_ATTEMPTS_FOR_ITEM_ANALYSIS", code)
        self.assertIn("withheld_below_threshold", code)

    def test_the_threshold_is_above_one(self):
        """A threshold of 1 withholds nothing; 2 still identifies a pair."""
        source = tool_source("training_course_catalog")
        value = int(re.search(r"MIN_ATTEMPTS_FOR_ITEM_ANALYSIS = (\d+)", source).group(1))
        self.assertGreaterEqual(value, 3)

    def test_the_withheld_count_is_reported(self):
        """An item analysis that silently omits its thin questions reads as though
        every question is well answered."""
        self.assertIn("withheld_below_threshold", code_only(tool_source("training_course_catalog")))

    def test_certificates_never_return_the_verification_code(self):
        """It is what proves a certificate genuine to a third party."""
        code = code_only(tool_source("training_compliance_status"))
        self.assertNotIn("verification_code", code)
        self.assertNotIn("certificate_html", code)

    def test_the_certificate_field_names_are_real(self):
        fields = doctype_fields("training", "training_certificate")
        for field in ("expires_on", "issued_on", "holder_name"):
            self.assertIn(field, fields)
        self.assertIn("verification_code", fields)  # it exists; it is simply not read


class TestTheReadOnlyPromiseHolds(unittest.TestCase):
    def test_no_new_tool_writes(self):
        for name in (
            "contract_signing_status",
            "kpi_dashboard_status",
            "project_pickup_route",
            "training_course_catalog",
        ):
            code = code_only(tool_source(name))
            for forbidden in (".save(", ".insert(", ".submit(", "db.set_value", "db.commit"):
                self.assertNotIn(forbidden, code, f"{name} contains {forbidden}")

    def test_the_kpi_tool_does_not_reach_for_refresh(self):
        """`refresh_kpi_dashboard` rebuilds a snapshot and commits. It is a write
        wearing a dashboard's name."""
        code = code_only(tool_source("kpi_dashboard_status"))
        self.assertNotIn("refresh_kpi_dashboard", code)

    def test_the_kpi_tool_carries_freshness(self):
        """A KPI built from a feed that last updated a week ago is a plausible
        number that is not true today, and the value cannot say so itself."""
        code = code_only(tool_source("kpi_dashboard_status"))
        self.assertIn("source_freshness", code)

    def test_wrapped_module_imports_are_lazy(self):
        """A top-level import of app code pulls frappe.utils names the schema
        test's stub lacks, which takes down the whole registry rather than one
        tool. Only frappe / typing / BaseTool / _common / _gate at module scope."""
        for name in (
            "contract_signing_status",
            "kpi_dashboard_status",
            "project_pickup_route",
            "training_course_catalog",
        ):
            for line in tool_source(name).splitlines():
                if line.startswith("from erpnext_enhancements."):
                    self.assertRegex(
                        line,
                        r"^from erpnext_enhancements\.assistant_tools\._",
                        f"{name} imports app code at module scope: {line!r}",
                    )


if __name__ == "__main__":
    unittest.main()
