# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The injury and illness log — WI-073 C.

Technicians work with water, chemicals, pumps and live electrical, and this is the
record OSHA asks for first. It is shaped by the regulation rather than by what
would be tidy, so most of these assertions are really "does the code still match
the rule".

Four properties carry the weight:

* **the record exists the moment it is filed**, whether or not anybody agrees with
  it — a log a manager can suppress at intake is not a log;
* **recordability is derived, never typed** — the rule is mechanical, and the one
  distinction that decides most cases is *first aid only* versus *medical
  treatment beyond first aid*, which is exactly the one people get wrong;
* **the reporting clock is shown at filing time** — a deadline surfaced next week
  is a deadline already missed;
* **privacy cases carry no name on the posted log**, and that is enforced in one
  function so there is one place to get it right.

Run: python -m unittest erpnext_enhancements.tests.test_hr_safety
"""

import ast
import io
import json
import re
import tokenize
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
MODULE = APP / "hr_enhancements"
INCIDENT_JSON = MODULE / "doctype/safety_incident/safety_incident.json"
INCIDENT_PY = MODULE / "doctype/safety_incident/safety_incident.py"
INCIDENT_JS = MODULE / "doctype/safety_incident/safety_incident.js"
ACTION_JSON = MODULE / "doctype/safety_incident_action/safety_incident_action.json"
SAFETY = MODULE / "safety.py"
LOG_300 = MODULE / "report/osha_300_log/osha_300_log.py"
SUMMARY_300A = MODULE / "report/osha_300a_summary/osha_300a_summary.py"
PRIVACY_LIST = MODULE / "report/osha_privacy_case_list/osha_privacy_case_list.py"
PRIVACY_JSON = MODULE / "report/osha_privacy_case_list/osha_privacy_case_list.json"
LOG_JSON = MODULE / "report/osha_300_log/osha_300_log.json"
PERMISSIONS = MODULE / "permissions.py"
HOOKS = APP / "hooks.py"
WIZARD = APP / "sapphire_maintenance/page/visit_wizard/visit_wizard.js"


def _text(path):
    return path.read_text(encoding="utf-8")


def _js(path):
    return re.sub(r"//.*$", "", _text(path), flags=re.M)


def _src(path):
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


def _fn(name, path):
    src = _text(path)
    lines = src.splitlines()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            stmts = node.body
            if (
                stmts
                and isinstance(stmts[0], ast.Expr)
                and isinstance(stmts[0].value, ast.Constant)
                and isinstance(stmts[0].value.value, str)
            ):
                stmts = stmts[1:]
            return "\n".join(lines[stmts[0].lineno - 1 : node.end_lineno]) if stmts else ""
    raise AssertionError(f"{name} not found in {path.name}")


def _fields(path):
    return {f["fieldname"]: f for f in json.loads(_text(path))["fields"]}


class TestTheRecordExistsTheMomentItIsFiled(unittest.TestCase):
    """A log a manager can suppress at intake is not a log."""

    def test_any_employee_can_create_one(self):
        perms = {p["role"]: p for p in json.loads(_text(INCIDENT_JSON))["permissions"]}
        self.assertEqual(perms["Employee"].get("create"), 1)

    def test_there_is_no_approval_state(self):
        """No countersignature, no acceptance step. Every gate between a technician
        and filing one is a reason it does not get filed."""
        options = _fields(INCIDENT_JSON)["status"]["options"].splitlines()
        self.assertEqual(options, ["Open", "Under investigation", "Closed"])
        for absent in ("Approved", "Accepted", "Rejected", "Confirmed", "Verified"):
            with self.subTest(state=absent):
                self.assertNotIn(absent, options)
        self.assertNotIn("is_submittable", json.loads(_text(INCIDENT_JSON)))

    def test_the_reported_stamp_cannot_be_edited(self):
        """The gap between when it happened and when it was reported is the first
        thing an inspector looks at, and a field that can be quietly corrected is
        not evidence of anything."""
        fields = _fields(INCIDENT_JSON)
        self.assertEqual(fields["reported_on"].get("read_only"), 1)
        self.assertEqual(fields["reported_by"].get("read_only"), 1)
        # Stamped in before_insert, not validate -- validate runs again on every save.
        self.assertIn("self.reported_on = now_datetime()", _fn("before_insert", INCIDENT_PY))

    def test_the_phone_path_asks_for_two_things_it_cannot_reconstruct(self):
        body = _fn("report_incident", SAFETY)
        self.assertIn("what_happened", body)
        self.assertIn("location_text", body)
        self.assertEqual(body.count("frappe.throw"), 2)

    def test_a_missing_doing_before_does_not_refuse_the_report(self):
        """It is required on the record because OSHA 301 asks it, and refusing the
        whole report over it would be choosing the form over the fact."""
        body = _fn("report_incident", SAFETY)
        self.assertIn("Not recorded at the time", body)


class TestRecordabilityIsDerivedNeverTyped(unittest.TestCase):
    """The rule is mechanical. A tick box labelled "recordable" invites a
    judgement call at the worst possible moment.
    """

    def test_the_field_is_read_only(self):
        self.assertEqual(_fields(INCIDENT_JSON)["is_recordable"].get("read_only"), 1)

    def test_first_aid_only_is_not_recordable(self):
        """The single distinction that decides most cases."""
        src = _text(INCIDENT_PY)
        self.assertIn('FIRST_AID = "First aid only"', src)
        recordable = _fn("_derive_recordable", INCIDENT_PY)
        self.assertIn("RECORDABLE_TREATMENT", recordable)
        # Split into NAMES, not a substring scan: "FIRST_AID" is a substring of
        # "BEYOND_FIRST_AID", so `assertNotIn` on the raw text fails on correct
        # code -- the same trap as every other absence assertion in this release,
        # one level down.
        tup = re.search(r"RECORDABLE_TREATMENT = \((.*?)\)", src, re.S).group(1)
        names = {n.strip() for n in tup.split(",") if n.strip()}
        self.assertNotIn("FIRST_AID", names)
        self.assertIn("BEYOND_FIRST_AID", names)

    def test_loss_of_consciousness_is_recordable_on_its_own(self):
        self.assertIn("lost_consciousness", _fn("_derive_recordable", INCIDENT_PY))

    def test_a_near_miss_is_never_recordable(self):
        """And that is not a technicality to hide: it is the most useful row in the
        log precisely because it cost nothing."""
        body = _fn("_derive_recordable", INCIDENT_PY)
        at = body.index("NEAR_MISS")
        self.assertIn("self.is_recordable = 0", body[at : at + 200])

    def test_the_day_counts_are_capped_at_the_rule(self):
        self.assertIn("MAX_DAYS = 180", _text(INCIDENT_PY))
        self.assertIn("_cap_days", _fn("validate", INCIDENT_PY))


class TestTheReportingClockIsShownAtFilingTime(unittest.TestCase):
    """A deadline that appears in a report next week is a deadline already
    missed.
    """

    def test_utah_gives_eight_and_twenty_four_hours(self):
        src = _text(INCIDENT_PY)
        self.assertIn("FATALITY_HOURS = 8", src)
        self.assertIn("HOSPITALISATION_HOURS = 24", src)

    def test_the_clock_runs_from_when_we_learned_not_when_it_happened(self):
        """What the rule says, and the only defensible reading — a company cannot
        start a clock on something it did not know about."""
        body = _fn("_derive_reporting_clock", INCIDENT_PY)
        self.assertIn("self.reported_on", body)
        self.assertNotIn("occurred_on", body)

    def test_a_reportable_case_sets_the_evidence_hold(self):
        """The moment somebody is deciding whether to move the pump is the moment
        nobody is reading a policy document."""
        body = _fn("_derive_reporting_clock", INCIDENT_PY)
        self.assertIn("self.evidence_hold = 1", body)

    def test_the_amputation_check_over_reports_rather_than_under(self):
        """The cost is not symmetric: a needless call costs a phone call, a missed
        one is a citation."""
        body = _fn("_is_amputation_or_eye", INCIDENT_PY)
        self.assertIn("amputat", body)
        self.assertIn("casefold()", body)

    def test_the_form_puts_the_clock_in_front_of_somebody(self):
        body = _js(INCIDENT_JS)
        self.assertIn("ee_clock", body)
        self.assertIn('"red"', body)
        # Hours remaining, not only the timestamp: "by 14:20" needs arithmetic done
        # in somebody's head at the worst moment.
        self.assertIn("36e5", body)

    def test_the_phone_path_says_so_immediately(self):
        body = _js(WIZARD)
        at = body.index("safety.report_incident")
        block = body[at : at + 1600]
        self.assertIn("reportable", block)
        self.assertIn("UOSH", block)


class TestPrivacyCasesCarryNoNameOnThePostedLog(unittest.TestCase):
    """Six categories, and retrofitting this is how a name ends up printed."""

    def test_the_six_categories_are_the_six(self):
        options = [
            o for o in _fields(INCIDENT_JSON)["privacy_basis"]["options"].splitlines() if o
        ]
        self.assertEqual(len(options), 6)
        for fragment in ("intimate", "sexual assault", "Mental illness", "HIV", "Needlestick", "asked"):
            with self.subTest(fragment=fragment):
                self.assertTrue(
                    any(fragment.casefold() in o.casefold() for o in options), fragment
                )

    def test_the_basis_is_required_when_the_box_is_ticked(self):
        body = _fn("_require_privacy_basis", INCIDENT_PY)
        self.assertIn("frappe.throw", body)

    def test_there_is_one_function_that_decides_the_printed_name(self):
        """One place to get right instead of one per report."""
        body = _fn("log_name", INCIDENT_PY)
        self.assertIn("is_privacy_case", body)
        self.assertIn("Privacy Case", body)

    def test_the_300_log_never_reads_employee_name_directly(self):
        """The whole privacy rule reduces to this line holding."""
        body = _src(LOG_300)
        self.assertIn("doc.log_name()", body)
        at = body.index("def execute")
        block = body[at : body.index("def _cases")]
        self.assertNotIn("doc.employee_name", block)

    def test_the_privacy_list_is_role_gated_more_tightly_than_the_log(self):
        """It maps case numbers back to names."""
        private = {r["role"] for r in json.loads(_text(PRIVACY_JSON))["roles"]}
        posted = {r["role"] for r in json.loads(_text(LOG_JSON))["roles"]}
        self.assertTrue(private < posted, "the privacy list must be strictly narrower")
        self.assertNotIn("HR User", private)

    def test_the_privacy_list_does_not_carry_the_injury_detail(self):
        """The point of a privacy case is that the NATURE of it stays off the list
        people read. A "helpful" description column would undo the rule in the one
        report that exists to honour it."""
        body = _src(PRIVACY_LIST)
        for absent in ("injury_description", "what_happened", "body_part", "harmed_by", "treatment"):
            with self.subTest(field=absent):
                self.assertNotIn(absent, body)


class TestTheFormsMatchTheForms(unittest.TestCase):
    def test_the_300_log_carries_every_osha_column(self):
        body = _text(LOG_300)
        for column in ("g_death", "h_days_away", "i_restriction", "j_other", "k_days_away", "l_days_restricted"):
            with self.subTest(column=column):
                self.assertIn(column, body)
        # Six, declared once each in ILLNESS_COLUMNS and rendered from it. Counting
        # twelve assumed they were also written out in _columns(), which would be
        # the duplication this avoids.
        self.assertEqual(len(re.findall(r'"m\d_', body)), 6)

    def test_only_recordable_cases_appear_on_the_300_log(self):
        """The form's own rule. Padding it is not generosity, it is a wrong form."""
        self.assertIn('"is_recordable": 1', _fn("_cases", LOG_300))

    def test_a_case_belongs_to_the_year_of_the_injury(self):
        """A case opened in January for a December injury sits on the previous
        year's log — getting that backwards moves a case between two forms that
        have both already been posted."""
        body = _fn("_cases", LOG_300)
        self.assertIn("occurred_on", body)
        self.assertNotIn("creation", body)

    def test_the_300a_says_an_empty_summary_still_has_to_be_posted(self):
        """The commonest one to forget."""
        self.assertIn("1 February to 30 April", _fn("_message", SUMMARY_300A))

    def test_the_300a_does_not_invent_total_hours(self):
        """It is the denominator of every incidence rate an insurer computes, and a
        figure guessed from headcount x 2,080 would be wrong by exactly the
        overtime this crew works."""
        body = _src(SUMMARY_300A)
        # Quote-agnostic: `_src` round-trips through ast.unparse, which normalises
        # double quotes to single. Asserting on the source spelling would fail on
        # code that is correct.
        self.assertIn("filters.get(", body)
        self.assertIn("total_hours", body)
        self.assertNotIn("2080", body)
        self.assertNotIn("* 52", body)


class TestACorrectiveActionBecomesRealWork(unittest.TestCase):
    """One that stays a sentence on a form is one nobody does."""

    def test_it_can_be_raised_as_a_task_or_an_assignment(self):
        body = _fn("raise_action", SAFETY)
        self.assertIn("Training Assignment", body)
        self.assertIn("_raise_task", body)

    def test_the_same_action_cannot_be_raised_twice(self):
        """Raised three times is three people doing it once between them."""
        body = _fn("raise_action", SAFETY)
        self.assertIn("line.raised_reference", body)
        self.assertIn("frappe.throw", body)

    def test_it_refuses_to_guess_which_course_was_meant(self):
        """Same class of mistake as matching a reimbursement Supplier by name: it
        succeeds confidently and assigns the wrong thing, and a wrongly assigned
        safety course is worse than none because it reads as done."""
        body = _fn("_raise_training", SAFETY)
        self.assertIn("frappe.throw", body)
        for fuzzy in ("like", "%", "difflib", "startswith"):
            with self.subTest(token=fuzzy):
                self.assertNotIn(fuzzy, _src(SAFETY).split("def _raise_training")[1])

    def test_the_action_owner_is_a_plain_name(self):
        """Same rule as the onboarding checklist: a required assignee is how a list
        stops getting filled in."""
        self.assertEqual(_fields(ACTION_JSON)["owner_name"]["fieldtype"], "Data")


class TestItIsReachableFromWhereItHappens(unittest.TestCase):
    """This app has shipped three correct server sides with no caller. Not four."""

    def test_every_endpoint_has_a_caller(self):
        tree = ast.parse(_text(SAFETY))
        whitelisted = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if getattr(target, "attr", None) == "whitelist":
                    whitelisted.add(node.name)
        self.assertTrue(whitelisted)
        callers = _js(INCIDENT_JS) + _js(WIZARD)
        for name in sorted(whitelisted):
            with self.subTest(endpoint=name):
                self.assertIn(f"safety.{name}", callers)

    def test_the_phone_path_is_on_the_screen_technicians_already_use(self):
        """A feature reachable only from its own Desk list is one nobody finds when
        they are hurt."""
        self.assertIn("render_report_incident", _js(WIZARD))

    def test_it_links_back_to_the_visit(self):
        body = _js(WIZARD)
        self.assertIn("maintenance_record: this.doc && this.doc.name", body)
        # `this.docname` does not exist on the wizard -- an undefined would file the
        # incident with no link back to the visit it happened on.
        self.assertNotIn("this.docname", body)


class TestTheIncidentIsNotReadableByEveryColleague(unittest.TestCase):
    def test_it_is_row_scoped(self):
        body = _fn("incident_query_conditions", PERMISSIONS)
        self.assertIn("reports_to", body)
        self.assertIn("reported_by", body)

    def test_there_is_a_document_level_twin(self):
        self.assertIn("def incident_has_permission", _text(PERMISSIONS))

    def test_both_are_registered(self):
        hooks = _text(HOOKS)
        for fn in ("incident_query_conditions", "incident_has_permission"):
            with self.subTest(fn=fn):
                self.assertIn(fn, hooks)


if __name__ == "__main__":
    unittest.main()
