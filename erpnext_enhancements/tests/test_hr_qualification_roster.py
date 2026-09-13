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
import sys
import tokenize
import types
import unittest
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
REPORT = APP / "hr_enhancements/report/crew_qualification_roster"
PY = REPORT / "crew_qualification_roster.py"
JS = REPORT / "crew_qualification_roster.js"
HTML = REPORT / "crew_qualification_roster.html"
JSON = REPORT / "crew_qualification_roster.json"

#: What `frappe.render_grid` puts in the context when it renders a report
#: `html_format` (v16: microtemplate.js `render_grid`, query_report.js
#: `print_report` / `pdf_report`). Read with `git show origin/version-16`, not from
#: the sibling checkout, which is on develop.
#:
#: `content` is deliberately ABSENT, and that absence is the whole point of
#: `TestThePrintSheetCanActuallyRender`: `render_grid` assigns `opts.content` FROM
#: this render, so it is unset while the custom format runs.
RENDER_CONTEXT = {
    "data",
    "original_data",
    "columns",
    "filters",
    "print_settings",
    "title",
    "subtitle",
    "landscape",
    "report",
    "can_use_smaller_font",
    "template",
    "grid",
    "lang",
}

#: Globals the desk page really does provide to a template running under `with(obj)`.
#: `content` is not one of them -- checked in Chrome: it is not a property of window.
BROWSER_GLOBALS = {"frappe", "Math", "JSON", "String", "Number", "Boolean", "Array", "Object"}

JS_KEYWORDS = {
    "var", "let", "const", "if", "else", "for", "while", "return", "typeof",
    "new", "function", "true", "false", "null", "undefined", "in", "of",
    "instanceof", "this", "do", "break", "continue", "delete", "void",
}


class _Dict(dict):
    """`frappe._dict`: a row that answers both `row.field` and `row.get("field")`.

    Both forms appear in this report and mixing them up is how a stub ends up testing
    itself rather than the code.
    """

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None


class _MetaStub:
    def __init__(self, fields):
        self._fields = set(fields)

    def has_field(self, name):
        return name in self._fields


def setUpModule():
    """A frappe stub, so the date logic can be CALLED rather than grepped.

    Which of the two people on a sign-off gets printed, and whether a withdrawn course
    counts on a past day, are both things an AST assertion about the shape of a branch
    cannot claim. `getdate` and `add_days` are REAL here for the same reason: a stub
    that hands back its own argument makes every date comparison vacuously true.
    """
    fake = types.ModuleType("frappe")
    fake._ = lambda text: text
    fake.whitelist = lambda *a, **k: (lambda fn: fn)
    fake.db = types.SimpleNamespace(exists=lambda *a, **k: True)
    fake.get_all = lambda *a, **k: []
    fake.get_meta = lambda *a, **k: _MetaStub(())
    fake.format = lambda value, spec=None: str(value)
    sys.modules["frappe"] = fake

    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda v: int(v or 0)
    utils.getdate = lambda d=None: (
        d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    )
    utils.add_days = lambda d, n: utils.getdate(d) + timedelta(days=n)
    utils.today = lambda: "2026-01-01"
    sys.modules["frappe.utils"] = utils
    fake.utils = utils

    nested = types.ModuleType("frappe.utils.nestedset")
    nested.NestedSet = type("NestedSet", (), {})
    sys.modules["frappe.utils.nestedset"] = nested


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


def _template_blocks(text):
    """Every template block, AFTER the rewrite frappe does first.

    `frappe.template.compile` runs a blanket replace of every double brace across
    the WHOLE file before it looks for anything else -- HTML comments very much
    included. Modelling that here is what makes the identifier check below catch
    prose that turned itself into code, rather than only the literal spelling.
    """
    rewritten = text.replace("{{", "{%=").replace("}}", "%}")
    return re.findall(r"\{%=?(.*?)%\}", rewritten, flags=re.S)


def _free_identifiers(text):
    """Names a template block reads from its scope: not after a dot, not declared."""
    names, declared = set(), set()
    for block in _template_blocks(text):
        code = re.sub(r'"[^"]*"', '""', block)
        code = re.sub(r"'[^']*'", "''", code)
        declared |= set(re.findall(r"\bvar\s+([A-Za-z_$][\w$]*)", code))
        for match in re.finditer(r"(\.?)\b([A-Za-z_$][\w$]*)\b", code):
            if match.group(1) != ".":
                names.add(match.group(2))
    return names - declared - JS_KEYWORDS


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


class TestThePrintSheetCanActuallyRender(unittest.TestCase):
    """The sheet did not render at all, from v1.397.0 until v1.424.0.

    `frappe.template.compile` rewrites every double brace in the file before it
    looks for anything else, so this header comment --

        the wrapper emits print_settings.letter_head around <double-braced content>

    -- compiled to a live read of `content`. `render_grid` assigns `opts.content`
    FROM this very render, so it is not in the context, and `content` is not a
    browser global either (checked in Chrome: it is not a property of window). Under
    the `with(obj)` scope the compiled template runs in, that is a ReferenceError:
    Print and Download PDF both threw and produced nothing, on the one artefact in
    this app whose entire purpose is to be printed and handed to somebody.

    Nothing caught it because no test rendered the template, and the text reads as a
    comment. Proven by porting microtemplate.js verbatim into node and rendering this
    file; pinned here by the two rules that port demonstrated.
    """

    def test_no_double_brace_anywhere_in_the_file(self):
        """Not a style rule. The rewrite does not care that it is inside a comment."""
        self.assertNotIn("{{", _text(HTML))
        self.assertNotIn("}}", _text(HTML))

    def test_no_template_tag_hides_inside_a_comment(self):
        comments = re.findall(r"<!--.*?-->", _text(HTML), flags=re.S)
        self.assertTrue(comments, "the header comment went missing")
        for comment in comments:
            with self.subTest(comment=comment[:60]):
                self.assertNotIn("{%", comment)

    def test_every_name_the_template_reads_is_one_the_renderer_passes(self):
        """The general form, and the one that would have caught the defect above on
        its own: `content` is not in RENDER_CONTEXT, so the old header failed here."""
        unknown = _free_identifiers(_text(HTML)) - RENDER_CONTEXT - BROWSER_GLOBALS
        self.assertEqual(unknown, set(), f"not in the render context: {sorted(unknown)}")

    def test_the_identifier_scan_is_not_passing_vacuously(self):
        """An absence assertion over an extractor. If the extractor found nothing --
        a changed tag syntax, a regex that stopped matching -- it would report clean
        on a file that could not render."""
        found = _free_identifiers(_text(HTML))
        self.assertIn("frappe", found)
        self.assertIn("filters", found)
        self.assertIn("original_data", found)

    def test_the_scan_would_catch_a_double_braced_name_in_a_comment(self):
        """Prove the modelled rewrite is doing the work, on the exact historical
        text, rather than the check passing because comments are skipped."""
        planted = "<!-- letter_head around {{ content }} -->\n<div>x</div>"
        self.assertIn("content", _free_identifiers(planted))


class TestThePrintSheetPrintsTheWholeResult(unittest.TestCase):
    """`data` is get_data_for_print(): it maps datatable.datamanager.rowViewOrder and
    keeps only bodyRenderer.visibleRowIndices, so it is the rows as currently sorted
    AND inline-filtered on screen. Typing in a column filter drops rows from the
    printed sheet with nothing to show it happened. `original_data` is this.data, the
    canonical server result, passed on the Print and the PDF path alike."""

    def test_the_body_loops_the_canonical_result(self):
        body = re.sub(r"<!--.*?-->", "", _text(HTML), flags=re.S)
        self.assertIn("original_data", body)
        self.assertNotIn("data[i]", body.replace("original_data", ""))
        self.assertNotIn("data.length", body.replace("original_data", ""))

    def test_it_falls_back_rather_than_printing_nothing(self):
        """A guarded read, because `typeof` is the only form that does not throw for
        a name absent from the `with(obj)` scope -- and a future frappe that stopped
        passing it should cost the sheet its ordering guarantee, not its contents."""
        body = re.sub(r"<!--.*?-->", "", _text(HTML), flags=re.S)
        self.assertIn("typeof original_data", body)

    def test_the_footer_no_longer_claims_the_on_screen_order(self):
        """The sheet states its own caveats, so a caveat that has stopped being true
        is a false statement on an evidence document rather than dead prose."""
        body = re.sub(r"<!--.*?-->", "", _text(HTML), flags=re.S)
        self.assertNotIn("the order shown on screen at the time of printing", body)


class TestWhoAttestedIsReadThroughTheBasis(unittest.TestCase):
    """A submitted sign-off carries TWO people and only under `Observed Supervisor`
    are they the same one. Until v1.424.0 the snapshot froze the supervisor fields
    from the login that pressed submit, so under a delegate basis -- a Training
    Manager typing up a verdict relayed over the radio -- this sheet printed the
    typist as the attester on a document handed to an insurer."""

    def setUp(self):
        from erpnext_enhancements.hr_enhancements.report.crew_qualification_roster import (
            crew_qualification_roster as report,
        )

        self.report = report
        self.row = {
            "supervisor_name_at_time": "Sam Senior",
            "supervisor_position_title": "Senior Technician",
            "recorded_by_at_time": "Tina Typist",
            "recorded_by_position_title": "Internal Systems Manager",
        }

    def test_the_delegate_case_names_the_supervisor_and_credits_the_typist(self):
        note = self.report._attestation_note(dict(self.row, authority_basis="Manager Delegate"))
        self.assertIn("Attested by Sam Senior (Senior Technician)", note)
        self.assertIn("recorded by Tina Typist", note)

    def test_the_tier_case_names_the_recorder_because_it_is_their_rung(self):
        """The inverse, and the reason swapping the two fields would not have been a
        fix. Here the recorder signs on their own rung and the named supervisor is
        only the address the request was routed to."""
        note = self.report._attestation_note(dict(self.row, authority_basis="Position Tier"))
        self.assertTrue(note.startswith("Attested by Tina Typist"))
        self.assertIn("Sam Senior", note)
        self.assertIn("addressed to", note)

    def test_the_observed_case_names_one_person_once(self):
        note = self.report._attestation_note(
            dict(self.row, authority_basis="Observed Supervisor", recorded_by_at_time="Sam Senior")
        )
        self.assertEqual(note.count("Sam Senior"), 1)
        self.assertNotIn("recorded by", note)

    def test_an_observed_row_is_not_split_by_a_spelling_difference(self):
        """Keyed on the basis rather than on whether the two names differ. Under an
        observed basis they are the same human read out of two different tables --
        Employee.employee_name and User.full_name -- and a spelling difference would
        otherwise print every ordinary sign-off as though two people were involved."""
        note = self.report._attestation_note(
            dict(self.row, authority_basis="Observed Supervisor", recorded_by_at_time="Samuel Senior")
        )
        self.assertNotIn("recorded by", note)

    def test_a_row_from_before_the_basis_existed_still_names_somebody(self):
        """Pre-v1.386.0 rows carry no basis and whatever was frozen at the time. They
        must read exactly as they did before rather than going blank."""
        note = self.report._attestation_note(
            {"supervisor_name_at_time": "Sam Senior", "supervisor_position_title": "Senior Technician"}
        )
        self.assertEqual(note, "Attested by Sam Senior (Senior Technician).")

    def test_an_empty_row_says_nothing_rather_than_inventing_a_name(self):
        self.assertEqual(self.report._attestation_note({}), "")

    def test_the_vocabulary_is_imported_rather_than_retyped(self):
        """Three string literals copied out of `training/authority.py` would drift the
        first time an option is renamed, and drift silently: an unmatched basis falls
        through to the plain branch, which is the old wrong behaviour."""
        code = _code(PY)
        self.assertIn("from erpnext_enhancements.training.authority import", code)
        for name in ("DELEGATE", "OBSERVED", "TIER"):
            with self.subTest(constant=name):
                self.assertIn(name, code)

    def test_the_query_asks_for_both_identities(self):
        """The branches above are unreachable if the fields are never fetched."""
        body = _fn("_signoff_rows")
        for field in ("authority_basis", "recorded_by_at_time", "recorded_by_position_title"):
            with self.subTest(field=field):
                self.assertIn(field, body)


class TestCoursesPassedAreDerivedFromDates(unittest.TestCase):
    """`Training Completion` is the module central audit artefact and this report could
    not read it until v1.425.0, because there was nothing to read it BY.

    The withdrawal path wrote `status = "Revoked"` through
    `db_set(..., update_modified=False)` and no date at all -- not even `modified`
    moved -- so a completion withdrawn last week was indistinguishable from one
    withdrawn two years ago. `Training Certificate` had been given `revoked_on` for
    exactly this reason in v1.396.0 and the completion behind it had not, which left
    the more important of the two records the less answerable.

    These call the function. The three-way outcome is the whole value of the change
    and an AST assertion about a branch would not exercise a single date comparison.
    """

    AS_OF = date(2026, 3, 1)

    def setUp(self):
        from erpnext_enhancements.hr_enhancements.report.crew_qualification_roster import (
            crew_qualification_roster as report,
        )

        self.report = report
        self.fake = sys.modules["frappe"]
        self.fake.get_meta = lambda doctype: _MetaStub({"revoked_on", "attested_on"})
        self.person = types.SimpleNamespace(
            name="HR-EMP-0001",
            employee_name="Jim Junior",
            user_id="junior@x",
            relieving_date=None,
        )

    def _rows(self, *completions):
        self.fake.get_all = lambda doctype, **kw: [_Dict(c) for c in completions]
        return self.report._completion_rows(self.person, self.AS_OF)

    def _completion(self, **overrides):
        row = {
            "name": "TRN-CMP-0001",
            "docstatus": 1,
            "course": "COURSE-0001",
            "course_title_snapshot": "Draining a Fountain Basin Safely",
            "completed_on": "2026-01-10 09:00:00",
            "expires_on": None,
            "revoked_on": None,
        }
        row.update(overrides)
        return row

    def test_a_course_passed_before_the_date_counts(self):
        rows = self._rows(self._completion())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "Course")
        self.assertEqual(rows[0]["what"], "Draining a Fountain Basin Safely")
        self.assertEqual(rows[0]["verdict"], "Passed")

    def test_a_course_passed_after_the_date_does_not(self):
        """The report answers the as-of date, not today. Somebody trained in June was
        not trained in March, and saying otherwise on an incident report is the single
        most damaging thing this page could do."""
        self.assertEqual(self._rows(self._completion(completed_on="2026-06-01 09:00:00")), [])

    def test_a_withdrawal_before_the_date_removes_it(self):
        self.assertEqual(
            self._rows(self._completion(docstatus=2, revoked_on="2026-02-01")), []
        )

    def test_a_withdrawal_after_the_date_leaves_it_standing(self):
        """THE case the whole field exists for. It was valid in March; it was withdrawn
        in August. Dropping it because of its state today is exactly the mistake."""
        rows = self._rows(self._completion(docstatus=2, revoked_on="2026-08-01"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], "Passed")

    def test_a_withdrawal_with_no_date_is_reported_as_unknown(self):
        """Cancelled before v1.425.0. It must never be assumed to have stood, nor
        assumed not to -- an empty cell would read as "nothing to report"."""
        rows = self._rows(self._completion(docstatus=2, revoked_on=None))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], "Unknown")
        self.assertIn("not reconstructible", rows[0]["note"])

    def test_the_boundaries_match_the_credential_rows(self):
        """Three off-by-one decisions, pinned so a later edit cannot quietly flip one.
        They follow `_credential_rows` exactly, because two kinds of row on one sheet
        disagreeing about what "on this date" means is worse than either answer."""
        # passed ON the day: counts.
        self.assertEqual(len(self._rows(self._completion(completed_on="2026-03-01 09:00:00"))), 1)
        # withdrawn ON the day: does not count.
        self.assertEqual(
            self._rows(self._completion(docstatus=2, revoked_on="2026-03-01")), []
        )
        # expiring ON the day: still counts -- `expires_on` is the last valid day.
        self.assertEqual(len(self._rows(self._completion(expires_on="2026-03-01"))), 1)

    def test_an_expiry_before_the_date_removes_it(self):
        self.assertEqual(self._rows(self._completion(expires_on="2026-02-01")), [])

    def test_expiring_counts_as_passed_on_the_as_of_horizon(self):
        """The 90-day horizon runs from the AS-OF date, not from today -- the same rule
        the credential rows follow."""
        rows = self._rows(self._completion(expires_on="2026-04-15"))
        self.assertEqual(len(rows), 1)
        self.assertIn("expiring", rows[0]["verdict"])

    def test_an_expiry_well_beyond_the_horizon_is_plain_passed(self):
        rows = self._rows(self._completion(expires_on="2027-01-01"))
        self.assertEqual(rows[0]["verdict"], "Passed")

    def test_the_withdrawal_date_outranks_the_expiry(self):
        """Withdrawn in February, would have expired in April. On 1 March it is gone,
        and the reason it is gone is the withdrawal."""
        self.assertEqual(
            self._rows(
                self._completion(docstatus=2, revoked_on="2026-02-01", expires_on="2026-04-01")
            ),
            [],
        )

    def test_it_still_works_on_a_site_that_has_not_migrated_the_field(self):
        """Mid-deploy, `revoked_on` does not exist yet. Every cancelled completion is
        then unknown, which is the correct answer, and nothing raises."""
        self.fake.get_meta = lambda doctype: _MetaStub(set())
        rows = self._rows(self._completion(docstatus=2, revoked_on="2026-08-01"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], "Unknown")

    def test_position_is_still_refused_on_a_course_row(self):
        """Every row goes through `_row`, so the rung refusal cannot be forgotten on a
        newly added kind."""
        self.assertEqual(self._rows(self._completion())[0]["position_as_of"], "not reconstructible")


class TestTheCompletionReaderNeverTrustsAStatus(unittest.TestCase):
    def test_cancelled_completions_are_deliberately_fetched(self):
        """Every other enumeration here takes `docstatus = 1`. This one must not: a
        completion withdrawn in August was still in force in March."""
        body = _fn("_completion_rows")
        self.assertIn('"docstatus": ["in", (1, 2)]', body)

    def test_it_reads_no_status_column(self):
        """A superseded completion is reported as passed, because it was. The material
        changed afterwards, which is a fact about the course, not about the person."""
        body = _fn("_completion_rows")
        self.assertNotIn('"status"', body)
        self.assertNotIn("row.status", body)

    def test_it_is_wired_into_the_rows_for_a_person(self):
        """A reader nothing calls is the defect this repo keeps hitting."""
        self.assertIn("_completion_rows(person, as_of)", _fn("_rows_for"))

    def test_the_module_constant_is_no_longer_dead(self):
        """`COMPLETION` sat unused at the top of this file from the day it was written
        -- the shape of the gap, not a tidy-up.

        Quotes normalised because `_code` goes through `ast.unparse`, which rewrites
        every string literal to single quotes. Asserting a double-quoted token against
        its output fails for a reason that has nothing to do with the claim."""
        code = _code(PY).replace("'", '"')
        self.assertIn("COMPLETION = ", code)
        self.assertIn('frappe.db.exists("DocType", COMPLETION)', code)


class TestThePrintGuard(unittest.TestCase):
    """v16 offers no server-side defence, so the guard is client-side and is a warning
    rather than a block. Its BEHAVIOUR is asserted by `scripts/test_roster_print_guard.mjs`,
    which runs it; these only pin that it exists and is reachable."""

    def test_the_guard_is_installed_from_onload(self):
        js = _text(JS)
        self.assertIn("guard_the_designed_sheet", js)
        at = js.index("onload: function (report)")
        self.assertIn("guard_the_designed_sheet(report)", js[at : at + 400])

    def test_both_print_entry_points_are_wrapped(self):
        js = re.sub(r"/\*.*?\*/", "", _text(JS), flags=re.S)
        self.assertIn("print_report", js)
        self.assertIn("pdf_report", js)

    def test_it_warns_rather_than_blocking(self):
        """Somebody who genuinely wants a column subset must still be able to have one.
        What they must not get is the swap by surprise."""
        js = _text(JS)
        self.assertIn("frappe.warn", js)
        self.assertNotIn("frappe.throw", js)

    def test_the_executing_test_is_wired_into_ci(self):
        """The lesson of v1.424.0: a static check cannot tell you whether code runs."""
        ci = (APP.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/test_roster_print_guard.mjs", ci)
        self.assertTrue((APP.parent / "scripts/test_roster_print_guard.mjs").is_file())


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
