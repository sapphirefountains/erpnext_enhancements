"""Regressions for the three defects the first real execution found.

Every phase of this module was verified by reading, and all of it passed. Then it
was executed once, end to end, and three things were wrong:

1. **The supervisor sign-off gate did not gate.** ``finish_attempt`` enforced every
   lesson gate and never looked at ``require_supervisor_signoff``, so a technician
   could be certified competent to drain a basin having only answered questions
   about it. That is the exact failure the Phase-4 contract was written to prevent
   — and it did not, because those assertions checked *source presence* rather than
   behaviour.
2. **A learner who scored 100 got a certificate reading 0.** ``_issue_completion``
   wrote ``"score"`` where the field is ``score_percent``, and ``_doc_payload``
   discarded the unknown key **silently**. The value was not missing in a way
   anybody would notice; it was present, plausible and wrong.
3. Same typo on the attempt itself.

The most useful test here is the third class, and it is static: **every field name
written through ``_doc_payload`` is checked against the DocType JSON.** That one
would have caught the score bug before it ever ran, without a bench, and it
generalises to any field added later.

Run: python -m unittest erpnext_enhancements.tests.test_training_runtime_regressions
"""

import ast
import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
RUNTIME = APP / "api/training.py"
DOCTYPES = APP / "training/doctype"


def _src():
    return RUNTIME.read_text(encoding="utf-8")


def _fields(doctype):
    """Declared fieldnames for a Training doctype, from its JSON."""
    slug = doctype.lower().replace(" ", "_")
    path = DOCTYPES / slug / f"{slug}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    names = {f["fieldname"] for f in data["fields"]}
    # Frappe supplies these on every doc; they are legitimate to write.
    return names | {"doctype", "name", "owner", "docstatus", "amended_from", "naming_series"}


def _fn(name):
    src = _src()
    start = src.index(f"def {name}")
    nxt = src.find("\ndef ", start + 1)
    return src[start : nxt if nxt != -1 else len(src)]


class TestPayloadFieldNamesExist(unittest.TestCase):
    """The static check that would have caught the score bug before it shipped.

    ``_doc_payload`` filters unknown keys so an insert cannot die in front of a
    learner. That is right, but it means a typo becomes *silent data loss* rather
    than an error — so the names have to be verified somewhere, and here is
    cheaper than production.
    """

    def _payload_keys(self, doctype):
        """String keys in every ``_doc_payload("<doctype>", {...})`` call."""
        tree = ast.parse(_src())
        found = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_doc_payload"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            if node.args[0].value != doctype:
                continue
            if len(node.args) > 1 and isinstance(node.args[1], ast.Dict):
                for key in node.args[1].keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        found.add(key.value)
        return found

    def test_the_parser_finds_calls(self):
        """Guards every other assertion in this class from passing vacuously."""
        self.assertTrue(self._payload_keys("Training Completion"))

    def test_completion_payload_names_real_fields(self):
        declared = _fields("Training Completion")
        unknown = sorted(self._payload_keys("Training Completion") - declared)
        self.assertEqual(
            unknown, [], f"_doc_payload writes {unknown} which Training Completion does not declare"
        )

    def test_attempt_payload_names_real_fields(self):
        declared = _fields("Training Attempt")
        unknown = sorted(self._payload_keys("Training Attempt") - declared)
        self.assertEqual(unknown, [], f"_doc_payload writes {unknown} not on Training Attempt")

    def test_the_score_field_is_the_percent_one(self):
        """Named explicitly because this is the bug, not a general principle: a
        certificate reading 'score 0' for a learner who scored 100."""
        keys = self._payload_keys("Training Completion")
        self.assertIn("score_percent", keys)
        self.assertNotIn("score", keys)

    def test_db_set_calls_use_real_field_names(self):
        """``db_set`` is filtered through ``meta.has_field`` in the same way, so it
        swallows a typo just as quietly."""
        declared = _fields("Training Attempt")
        body = _fn("finish_attempt")
        written = set(re.findall(r'"(\w+)":\s', body))
        # Only the keys that are plausibly field writes, not dict payload keys the
        # function returns to the caller.
        response_keys = {"passed", "attempt", "status", "score", "outstanding", "completion",
                         "lesson_key", "title", "reasons"}
        unknown = sorted(k for k in written - response_keys if k not in declared)
        self.assertEqual(unknown, [], f"finish_attempt db_sets {unknown} which Training Attempt lacks")


class TestSignoffGateIsEnforced(unittest.TestCase):
    """The gate that did not gate.

    The Phase-4 contract asserted the *compliance* module never throws, which was
    the right thing to check and a different thing entirely. Nothing asserted that
    a course requiring hands-on verification actually refuses to complete without
    it, so nothing noticed when it did not.
    """

    def test_finish_attempt_consults_the_requirement(self):
        self.assertIn("_signoff_outstanding", _fn("finish_attempt"))

    def test_the_helper_reads_the_course_flag(self):
        body = _fn("_signoff_outstanding")
        self.assertIn("require_supervisor_signoff", body)

    def test_only_a_submitted_competent_signoff_counts(self):
        """A draft is a request, not a verification; 'Needs More Practice' is the
        supervisor explicitly declining. Either one satisfying the gate would make
        it decorative."""
        body = _fn("_signoff_outstanding")
        self.assertIn('"outcome": "Competent"', body)
        self.assertIn('"docstatus": 1', body)

    def test_it_reports_rather_than_throws(self):
        """Reported as outstanding like every other unmet gate, so the player can
        tell the learner what is left instead of showing an error."""
        self.assertNotIn("frappe.throw", _fn("_signoff_outstanding"))

    def test_a_site_without_the_doctype_can_still_finish(self):
        """Phase 4 may not be migrated everywhere. Not being able to complete any
        course at all is a worse failure than an unenforced gate, so the absence is
        logged and skipped rather than blocking."""
        body = _fn("_signoff_outstanding")
        self.assertIn('frappe.db.exists("DocType", "Training Signoff")', body)
        self.assertIn("log_error", body)

    def test_the_completion_records_which_signoff_unlocked_it(self):
        self.assertIn("_competent_signoff", _fn("_issue_completion"))


class TestDroppedPayloadKeysAreLoud(unittest.TestCase):
    """Defensive must not mean silent.

    The filter stays — an insert that dies in front of a learner is worse than a
    missing value — but a discarded key now reaches the Error Log. The original
    docstring claimed the missing value would 'show up in the record, where
    somebody will notice it'. Nobody did, for four phases.
    """

    def test_dropped_keys_are_logged(self):
        body = _fn("_doc_payload")
        self.assertIn("log_error", body)

    def test_the_filter_still_exists(self):
        self.assertIn("has_field", _fn("_doc_payload"))


if __name__ == "__main__":
    unittest.main()
