# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Who can I send, and who can't go — WI-073 B.

Two reasons somebody should not be sent were already recorded and nothing read
them when a visit was scheduled: **approved time off**, and **restricted duty**.
A visit could be booked for a technician with an approved day off and nobody found
out until the morning.

The assertions here cluster around three decisions:

* **warn, never block** — copied from `training/compliance.py` rather than
  re-argued, because by the time a maintenance record is saved there is often a
  truck already moving, and refusing the form moves the work off the books;
* **a restriction records what, never why** — a diagnosis is not the company's
  business, and the pull toward a free-text "reason" field is strong enough to
  need a test holding it shut;
* **absence is not refusal** — nobody having filed a driving licence means nobody
  filed one, not that the person cannot drive. Getting that backwards flags
  fifteen of sixteen people on day one and gets the whole warning muted.

Run: python -m unittest erpnext_enhancements.tests.test_hr_availability
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
AVAILABILITY = MODULE / "availability.py"
RESTRICTION_JSON = MODULE / "doctype/work_restriction/work_restriction.json"
RESTRICTION_PY = MODULE / "doctype/work_restriction/work_restriction.py"
COVERAGE = MODULE / "report/qualification_coverage/qualification_coverage.py"
COVERAGE_JSON = MODULE / "report/qualification_coverage/qualification_coverage.json"
PERMISSIONS = MODULE / "permissions.py"
HOOKS = APP / "hooks.py"
FLEET_JSON = APP / "fleet_maintenance/doctype/fleet_vehicle/fleet_vehicle.json"
SIDEBAR = APP / "workspace_sidebar/hr.json"


def _text(path):
    return path.read_text(encoding="utf-8")


def _src(path):
    """Source with comments and docstrings stripped.

    Every absence assertion here is about what the code DOES, and this module's
    prose necessarily names what is excluded — the docstring explaining that there
    is no reason field contains the word "reason". Six assertions in a recent
    release passed on broken code for exactly that.
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


class TestARestrictionRecordsWhatNeverWhy(unittest.TestCase):
    """The pull toward a free-text reason field is strong, it feels helpful, and it
    would put medical information in a doctype the Employee role can read. Once it
    is there every future feature that joins this table inherits it.
    """

    def test_there_is_no_reason_or_diagnosis_field(self):
        fields = {f["fieldname"] for f in json.loads(_text(RESTRICTION_JSON))["fields"]}
        for absent in ("reason", "diagnosis", "condition", "injury", "medical", "doctor", "illness"):
            with self.subTest(field=absent):
                self.assertFalse(
                    any(absent in f for f in fields), f"a field matching {absent!r} exists"
                )

    def test_the_note_field_says_what_it_is_for(self):
        """It is the field somebody would put the reason in, so its description has
        to point the other way."""
        fields = {f["fieldname"]: f for f in json.loads(_text(RESTRICTION_JSON))["fields"]}
        self.assertIn("NOT the reason", fields["note"]["description"])

    def test_the_dispatch_warning_carries_only_the_summary(self):
        """The sentence that reaches a dispatcher's screen is the one most likely
        to be pasted into a message."""
        body = _fn("_restrictions", AVAILABILITY)
        self.assertIn("doc.summary()", body)
        self.assertNotIn("note", _src(AVAILABILITY).split("def _restrictions")[1].split("def ")[0])

    def test_a_restriction_must_restrict_something(self):
        """A row that restricts nothing reads as a limitation, worries whoever sees
        it, and changes no dispatch decision."""
        body = _fn("_require_a_restriction", RESTRICTION_PY)
        self.assertIn("frappe.throw", body)
        self.assertIn("RESTRICTIONS", body)


class TestOpenEndedIsNotExpired(unittest.TestCase):
    """A restriction usually has no end date when it is first written down, and
    reading that as "finished" is the unsafe direction.
    """

    def test_covers_treats_a_missing_end_date_as_still_running(self):
        body = _fn("covers", RESTRICTION_PY)
        at = body.index("if self.to_date")
        self.assertIn("self.to_date and", body[at : at + 80])

    def test_the_query_filters_the_end_date_in_python(self):
        """Not in the `filters` dict. A `>=` on a nullable date silently matches
        NULLs in Frappe — the coalesce trap — which would be right by accident
        here and wrong the next time somebody copies the query."""
        body = _fn("_restrictions", AVAILABILITY)
        at = body.index("filters={")
        self.assertNotIn("to_date", body[at : body.index("fields=", at)])
        self.assertIn("if row.to_date and getdate(row.to_date) < when:", body)


class TestItWarnsAndNeverBlocks(unittest.TestCase):
    """By the time a maintenance record is saved there is often a truck already
    moving. Refusing the form does not stop the work, it moves the work off the
    books — where nobody can see it at all.
    """

    def test_the_technician_advisory_never_throws(self):
        body = _src(AVAILABILITY)
        at = body.index("def warn_unavailable_technician")
        block = body[at : body.index("def unavailable_on")]
        self.assertIn("msgprint", block)
        self.assertNotIn("throw", block)

    def test_the_driver_advisory_never_throws(self):
        body = _src(AVAILABILITY)
        at = body.index("def warn_driver_cannot_drive")
        self.assertIn("msgprint", body[at:])
        self.assertNotIn("throw", body[at:])

    def test_a_bug_in_a_warning_cannot_fail_a_save(self):
        """The swallow is the point of the decorator."""
        body = _fn("_swallow", AVAILABILITY)
        self.assertIn("except Exception", body)
        self.assertIn("log_error", body)

    def test_it_is_dormant_during_a_migrate(self):
        body = _fn("_swallow", AVAILABILITY)
        for flag in ("in_migrate", "in_install", "in_patch", "in_import"):
            with self.subTest(flag=flag):
                self.assertIn(flag, body)

    def test_the_two_advisories_are_separate_hooks(self):
        """They ask different questions about the same person — may they, versus
        can they be there — and a site may want one without the other."""
        hooks = _text(HOOKS)
        at = hooks.index('"Sapphire Maintenance Record"')
        block = hooks[at : at + 1400]
        self.assertIn("compliance.warn_uncertified_technician", block)
        self.assertIn("availability.warn_unavailable_technician", block)

    def test_it_warns_on_the_scheduled_date_not_only_the_visit_date(self):
        """A warning that only appears once the truck has arrived is a warning
        nobody can act on."""
        body = _fn("warn_unavailable_technician", AVAILABILITY)
        at = body.index("scheduled_visit_date")
        self.assertLess(at, body.index("visit_date", at + 1))


class TestOnlyApprovedTimeOffCounts(unittest.TestCase):
    def test_requested_is_not_a_day_off_yet(self):
        """Warning about a Requested day trains people to ignore the warning, which
        costs more than the case it catches."""
        body = _fn("_time_off", AVAILABILITY)
        self.assertIn('"status": APPROVED', body)
        self.assertNotIn("Requested", body)

    def test_the_range_is_compared_at_both_ends(self):
        """A request is a range and the visit is a point inside it."""
        body = _fn("_time_off", AVAILABILITY)
        self.assertIn('"from_date": ["<=", when]', body)
        self.assertIn('"to_date": [">=", when]', body)


class TestAbsenceIsNotRefusal(unittest.TestCase):
    """Nobody having filed a driving licence means nobody filed one. Treating that
    as "cannot drive" flags fifteen of sixteen people on day one and gets the
    warning muted by the end of the week.
    """

    def test_no_licence_on_file_is_allowed(self):
        body = _fn("may_drive", AVAILABILITY)
        at = body.index("if not held:")
        self.assertIn("return True, None", body[at : at + 200])

    def test_an_expired_licence_on_file_is_not(self):
        body = _fn("may_drive", AVAILABILITY)
        self.assertIn('row.status in ("Valid", "Expiring")', body)

    def test_a_no_driving_restriction_stops_it(self):
        body = _fn("may_drive", AVAILABILITY)
        self.assertIn('kinds=("no_driving",)', body)

    def test_the_credential_match_is_whole_name_not_substring(self):
        """A substring match on "licence" also catches a contractor licence and a
        pesticide applicator licence, and reporting somebody as unable to drive
        because their pesticide ticket lapsed is the kind of wrong that gets the
        whole warning switched off."""
        body = _fn("_driving_credential_types", AVAILABILITY)
        self.assertIn("casefold() in DRIVING_CREDENTIALS", body)
        self.assertNotIn("like", body.lower())

    def test_an_empty_type_list_matches_nothing_rather_than_everything(self):
        """An empty `in` list is a filter Frappe DROPS, which would turn the query
        into "every credential this person holds" and report somebody as unable to
        drive because their first aid card lapsed."""
        body = _fn("_driving_credential_types", AVAILABILITY)
        self.assertIn('["__none__"]', body)
        self.assertIn("names or", body)

    def test_the_vehicle_driver_is_a_link_not_a_typed_name(self):
        """A name in a text field cannot be joined to a licence expiry, so nothing
        could answer "may this person still drive this truck"."""
        fields = {f["fieldname"]: f for f in json.loads(_text(FLEET_JSON))["fields"]}
        self.assertEqual(fields["assigned_driver"]["fieldtype"], "Link")
        self.assertEqual(fields["assigned_driver"]["options"], "Employee")


class TestCoverageCountsTheThingNobodyLooksAt(unittest.TestCase):
    """Every existing view is per-person, and a per-person view cannot show you a
    count of one.
    """

    def test_sign_off_authority_is_counted_as_a_qualification(self):
        """The one nobody would think to look at, and the one with the smallest
        number against it: prod has four Junior Technicians and one Senior."""
        self.assertIn("def _authority", _text(COVERAGE))
        body = _fn("_authority", COVERAGE)
        self.assertIn("job_family", body)
        self.assertIn('"tier": [">", cint(rung.tier)]', body)

    def test_authority_is_counted_on_the_same_ladder_only(self):
        """A Master Technician covers a Junior; a Senior Designer does not."""
        body = _fn("_authority", COVERAGE)
        at = body.index("filters={")
        self.assertIn("job_family", body[at : body.index("pluck=", at)])

    def test_an_empty_rung_is_not_counted(self):
        """Nobody stands on it, so nobody needs signing off on it — counting it
        would fill the report with empty ladders."""
        self.assertIn("if not holders.get(rung.name):", _fn("_authority", COVERAGE))

    def test_credentials_are_counted_by_person_not_by_row(self):
        """Somebody who renewed keeps both rows, and counting both reports cover of
        two where there is one person."""
        body = _fn("_credentials", COVERAGE)
        self.assertIn("by_employee", body)
        self.assertIn("len(by_employee)", body)

    def test_a_customer_completion_is_not_company_cover(self):
        body = _fn("_courses", COVERAGE)
        self.assertIn("if row.user not in staff:", body)

    def test_it_opens_on_the_rows_that_matter(self):
        """A report opening on forty rows of "eight people hold this" buries the
        three rows that are the whole point."""
        body = _fn("execute", COVERAGE)
        self.assertIn('cint(filters.get("show_all"))', body)
        self.assertIn('r["current"] <= THIN', body)

    def test_the_report_declares_a_ref_doctype_and_roles(self):
        """A Script Report with no resolvable ref_doctype errors for its intended
        reader — the trap Training Completion Matrix already hit."""
        report = json.loads(_text(COVERAGE_JSON))
        self.assertEqual(report["ref_doctype"], "Employee")
        self.assertEqual(report["report_type"], "Script Report")
        self.assertTrue(report["roles"])


class TestTheRestrictionIsNotReadableByEveryColleague(unittest.TestCase):
    def test_it_is_scoped_like_time_off(self):
        body = _fn("restriction_query_conditions", PERMISSIONS)
        self.assertIn("reports_to", body)

    def test_there_is_a_document_level_twin(self):
        self.assertIn("def restriction_has_permission", _text(PERMISSIONS))

    def test_both_are_registered(self):
        hooks = _text(HOOKS)
        for fn in ("restriction_query_conditions", "restriction_has_permission"):
            with self.subTest(fn=fn):
                self.assertIn(fn, hooks)

    def test_creating_one_for_a_report_is_not_refused(self):
        """`user` is derived in validate(), so it is empty on a NEW row."""
        self.assertIn('doc.get("employee")', _fn("restriction_has_permission", PERMISSIONS))

    def test_the_who_is_unavailable_endpoint_is_staff_only(self):
        """Every customer contact on this site holds a login, and who is off is a
        rough map of the company's week."""
        body = _fn("unavailable_on", AVAILABILITY)
        self.assertIn('frappe.db.exists("Employee"', body)
        self.assertIn("PermissionError", body)


class TestTheSidebarIsStillCoherent(unittest.TestCase):
    def test_the_new_rows_are_there(self):
        labels = [i.get("label") for i in json.loads(_text(SIDEBAR))["items"]]
        self.assertIn("Restricted duty", labels)
        self.assertIn("Coverage", labels)

    def test_it_is_stamped_newer_than_the_row_on_prod(self):
        """Workspace Sidebar is TIMESTAMP-gated on import, unlike a DocType."""
        self.assertGreater(
            json.loads(_text(SIDEBAR))["modified"], "2026-02-08 10:52:20.227777"
        )

    def test_no_row_uses_an_icon_v16_does_not_ship(self):
        for item in json.loads(_text(SIDEBAR))["items"]:
            with self.subTest(item=item.get("label")):
                self.assertNotEqual(item.get("icon"), "sitemap")


if __name__ == "__main__":
    unittest.main()
