# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The audit roster answers a DATE, not today. Bench-free.

The report exists because "who was qualified on 1 March" is the question an insurer
asks after an incident, and every existing view in this app answers "who is qualified
now". Getting that wrong is not a cosmetic failure: a sheet handed to an insurer that
silently restates today over a past date is worse than no sheet, because somebody
relies on it.

What makes it correct is one rule, and these tests exist to keep it: **re-derive
every state from stored dates, never read a `status` column.** Every derived-status
write in both modules goes through ``db_set(..., update_modified=False)``, which never
reaches ``save_version()`` — so a stored status is a fact about today and
``track_changes = 1`` recorded nothing about how it got there.

Run: python -m unittest erpnext_enhancements.tests.test_hr_qualification_roster
"""

import ast
import io
import re
import tokenize
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPORT = APP / "hr_enhancements/report/crew_qualification_roster"
PY = REPORT / "crew_qualification_roster.py"
JS = REPORT / "crew_qualification_roster.js"
HTML = REPORT / "crew_qualification_roster.html"
JSON = REPORT / "crew_qualification_roster.json"


def _text(path):
    return path.read_text(encoding="utf-8")


def _code(path):
    """Source with comments AND docstrings stripped.

    Every absence assertion below would otherwise match the prose explaining the
    absence. That trap has bitten this repo six times; strip first, assert second.
    """
    kept = [
        t
        for t in tokenize.generate_tokens(io.StringIO(_text(path)).readline)
        if t.type != tokenize.COMMENT
    ]
    tree = ast.parse(tokenize.untokenize(kept))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body[0].value.value = ""
    return ast.unparse(tree)


def _fn(name):
    src = _text(PY)
    lines = src.splitlines()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            return "\n".join(lines[body[0].lineno - 1 : node.end_lineno]) if body else ""
    raise AssertionError(f"{name} not found")


class TestItAnswersTheDateNotToday(unittest.TestCase):
    def test_the_roster_of_people_is_not_who_is_active_now(self):
        """`Employee.status` carries no transition date, so it cannot place anybody
        relative to a past day. Joining and relieving dates can, and they are core
        ERPNext fields this app had never used."""
        body = _fn("_people_on")
        self.assertIn("date_of_joining", body)
        self.assertIn("relieving_date", body)
        self.assertNotIn('"status": "Active"', body)

    def test_leavers_are_included(self):
        """After an incident, a leaver is exactly who gets asked about. Every other
        enumeration in this app filters them out."""
        body = _fn("_people_on")
        self.assertIn("not r.relieving_date or", body)

    def test_the_nullable_date_is_filtered_in_python(self):
        """A `>=` filter on a nullable date silently matches NULLs in Frappe, and
        here NULL means "still employed" -- the opposite of what the query would
        conclude."""
        body = _fn("_people_on")
        at = body.index("filters=conditions")
        self.assertNotIn("relieving_date", body[body.index("conditions = {") : at])


class TestNoStatusColumnIsTrusted(unittest.TestCase):
    """The single rule that makes the report correct."""

    def test_credentials_are_derived_from_their_three_dates(self):
        body = _fn("_credential_rows")
        for field in ("issued_on", "expires_on", "revoked_on"):
            with self.subTest(field=field):
                self.assertIn(field, body)

    def test_no_derived_status_column_is_read(self):
        code = _code(PY)
        for banned in ('"status"', "'status'"):
            with self.subTest(token=banned):
                self.assertNotIn(f"filters={{{banned}", code)

    def test_signoffs_key_on_the_attestation_not_the_request(self):
        """`signed_on` is when the learner RAISED the request. Keying on it would
        report somebody as competent from the moment they asked to be."""
        body = _fn("_signoff_rows")
        self.assertIn("attested_on", body)
        self.assertNotIn("getdate(row.signed_on)", body)


class TestTheThreeInvertedInstincts(unittest.TestCase):
    def test_expiring_counts_as_held(self):
        """The 90-day horizon is a warning, not a lapse -- and it runs from the
        AS-OF date, so a card inside its window in March reads that way in March."""
        body = _fn("_credential_rows")
        self.assertIn("add_days(as_of, EXPIRY_HORIZON_DAYS)", body)
        self.assertIn("Held", body)

    def test_supervised_only_never_reads_as_cleared(self):
        """It shares doctype, docstatus and date stamp with Competent, so a query
        filtering only on docstatus=1 reports it as competence."""
        body = _fn("_signoff_rows")
        self.assertIn("NOT cleared to work alone", body)

    def test_position_is_refused_rather_than_stated(self):
        """`Employee.custom_position` is a bare Link with no effective-from date and
        no history table. Printing today's rung against a past date on a document
        handed to an insurer would be a claim nobody can support."""
        body = _fn("_row")
        self.assertIn("NOT_RECONSTRUCTIBLE", body)
        self.assertIn("position_as_of", body)


class TestItRefusesRatherThanRendersBlank(unittest.TestCase):
    """An empty roster reads as "everyone is clear", which is the most dangerous
    sentence this page could produce."""

    def test_an_empty_result_carries_a_refusal(self):
        body = _fn("execute")
        self.assertIn("_refusal(", body)
        self.assertIn("_nothing_on_file(", body)

    def test_the_refusal_says_which_question_had_no_answer(self):
        body = _fn("_refusal")
        self.assertIn("employee", body)
        self.assertIn("department", body)

    def test_nobody_on_file_is_reported_as_a_finding(self):
        self.assertIn("That is a finding, not an empty report", _text(PY))


class TestThePrintSheet(unittest.TestCase):
    def test_it_draws_no_letterhead_of_its_own(self):
        """Frappe wrapper already emits print_settings.letter_head around the
        content, so a letterhead here prints twice -- the exact inverse of this app
        Jinja print-format rule."""
        html = _text(HTML)
        body = re.sub(r"<!--.*?-->", "", html, flags=re.S)
        for banned in ("letter_head", "letterhead"):
            with self.subTest(token=banned):
                self.assertNotIn(banned, body)

    def test_it_carries_no_apostrophes_in_markup(self):
        """frappe.template escapes only the LAST apostrophe in each text run."""
        body = re.sub(r"<!--.*?-->", "", _text(HTML), flags=re.S)
        self.assertNotIn("'", body)

    def test_it_states_the_as_of_date_and_the_caveats(self):
        body = _text(HTML)
        self.assertIn("filters.as_of", body)
        self.assertIn("not reconstructible", body)
        self.assertIn("since left", body)


class TestTheFilters(unittest.TestCase):
    def test_the_person_filter_does_not_exclude_leavers(self):
        """A status filter would make a leaver unselectable, and the report would
        then look complete while being unable to answer the question it exists for."""
        js = re.sub(r"//.*$", "", _text(JS), flags=re.M)
        js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
        at = js.index('options: "Employee"')
        self.assertNotIn("Active", js[at : at + 400])

    def test_the_as_of_date_is_required(self):
        js = _text(JS)
        at = js.index('fieldname: "as_of"')
        self.assertIn("reqd: 1", js[at : at + 300])


class TestItIsRegistered(unittest.TestCase):
    def test_the_report_record_declares_a_script_report(self):
        import json as _json

        doc = _json.loads(_text(JSON))
        self.assertEqual(doc["report_type"], "Script Report")
        self.assertEqual(doc["module"], "HR Enhancements")
        self.assertEqual(doc["ref_doctype"], "Employee")
        self.assertEqual(doc["is_standard"], "Yes")

    def test_it_is_wired_into_ci(self):
        """A bench-free suite that is never named in ci.yml runs nowhere, and
        nothing detects it."""
        ci = (APP.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("test_hr_qualification_roster", ci)


if __name__ == "__main__":
    unittest.main()
