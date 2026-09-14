"""Bench-free tests for the advisory inspector-qualification check (WI-075 sub-phase H).

The check is advisory and never blocks a save, which is exactly why it needs tests: nothing
downstream will ever fail because it got the answer wrong. A warning that is wrong in the
*quiet* direction is indistinguishable from a warning that is right.

The one that matters most is `test_same_tier_in_a_different_family_is_not_satisfied`. `Position`
carries an integer `tier`, so the obvious implementation compares tiers — and a bare `tier >=
tier` passes a tier-3 Designer as a qualified tier-2 Technician. It *passes*, so nobody
investigates it, which is the same failure direction as the trailing-space audit that reported
clean on three broken rows.

Run: python -m unittest erpnext_enhancements.tests.test_inspector_qualification
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.quality import qualification

SENIOR_TECH = {"name": "Senior Technician", "job_family": "Technician", "tier": 2}
MASTER_TECH = {"name": "Master Technician", "job_family": "Technician", "tier": 3}
JUNIOR_TECH = {"name": "Junior Technician", "job_family": "Technician", "tier": 1}
SENIOR_DESIGNER = {"name": "Senior Designer", "job_family": "Designer", "tier": 3}


def _template(position=None, course=None, position_doc=None):
    return {
        "template_name": "Build — Pre-final",
        "required_position": position,
        "required_position_doc": position_doc,
        "required_course": course,
    }


def _inspector(employee="HR-EMP-0001", position=None, courses=()):
    return {
        "user": "tech@x.com",
        "full_name": "A Technician",
        "employee": employee,
        "position": position,
        "valid_courses": courses,
    }


class TestHasRequirements(unittest.TestCase):
    def test_an_unconfigured_template_asks_for_nothing(self):
        self.assertFalse(qualification.has_requirements(_template()))

    def test_either_requirement_alone_counts(self):
        self.assertTrue(qualification.has_requirements(_template(position="Senior Technician")))
        self.assertTrue(qualification.has_requirements(_template(course="QA-101")))


class TestPositionSatisfies(unittest.TestCase):
    def test_no_requirement_is_always_satisfied(self):
        self.assertTrue(qualification.position_satisfies(None, None))

    def test_the_exact_position_satisfies(self):
        self.assertTrue(qualification.position_satisfies(SENIOR_TECH, dict(SENIOR_TECH)))

    def test_a_higher_tier_in_the_same_family_satisfies(self):
        """A Master Technician can do a Senior Technician's inspection."""
        self.assertTrue(qualification.position_satisfies(SENIOR_TECH, MASTER_TECH))

    def test_a_lower_tier_in_the_same_family_does_not(self):
        self.assertFalse(qualification.position_satisfies(SENIOR_TECH, JUNIOR_TECH))

    def test_same_tier_in_a_different_family_is_not_satisfied(self):
        """The comparison that must never be made.

        Tier 3 Designer and tier 3 Technician are both threes and nothing follows from that. A
        bare tier comparison PASSES this, and a check that passes is a check nobody looks at.
        """
        self.assertFalse(qualification.position_satisfies(SENIOR_TECH, SENIOR_DESIGNER))

    def test_a_higher_tier_in_a_different_family_is_still_not_satisfied(self):
        self.assertFalse(
            qualification.position_satisfies(
                SENIOR_TECH, {"name": "Director", "job_family": "Leadership", "tier": 9}
            )
        )

    def test_holding_nothing_does_not_satisfy(self):
        self.assertFalse(qualification.position_satisfies(SENIOR_TECH, None))

    def test_an_unmodelled_family_falls_back_to_an_exact_match(self):
        """An unmodelled hierarchy is unknown, not flat."""
        required = {"name": "Senior Technician", "job_family": None, "tier": 2}
        held = {"name": "Master Technician", "job_family": None, "tier": 3}
        self.assertFalse(qualification.position_satisfies(required, held))
        self.assertTrue(qualification.position_satisfies(required, dict(required)))

    def test_a_missing_tier_falls_back_to_an_exact_match(self):
        required = {"name": "Senior Technician", "job_family": "Technician", "tier": None}
        held = {"name": "Master Technician", "job_family": "Technician", "tier": 3}
        self.assertFalse(qualification.position_satisfies(required, held))

    def test_a_non_numeric_tier_does_not_crash_the_save(self):
        required = {"name": "Senior Technician", "job_family": "Technician", "tier": "senior"}
        held = {"name": "Master Technician", "job_family": "Technician", "tier": 3}
        self.assertFalse(qualification.position_satisfies(required, held))


class TestFindings(unittest.TestCase):
    def test_an_unconfigured_template_says_nothing(self):
        """Absence of a requirement is not a failed requirement.

        Warning on every unconfigured template is the fastest way to teach people to dismiss
        the warning, which costs the real ones too.
        """
        self.assertEqual(qualification.findings(_template(), _inspector()), [])

    def test_a_met_requirement_says_nothing(self):
        out = qualification.findings(
            _template(position="Senior Technician", position_doc=SENIOR_TECH, course="QA-101"),
            _inspector(position=MASTER_TECH, courses=["QA-101"]),
        )
        self.assertEqual(out, [])

    def test_an_unmet_position_is_reported_with_what_they_hold(self):
        out = qualification.findings(
            _template(position="Senior Technician", position_doc=SENIOR_TECH),
            _inspector(position=JUNIOR_TECH),
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], qualification.KIND_POSITION)
        self.assertEqual(out[0]["requirement"], "Senior Technician")
        self.assertEqual(out[0]["held"], "Junior Technician")

    def test_holding_no_position_reads_differently_from_holding_the_wrong_one(self):
        out = qualification.findings(
            _template(position="Senior Technician", position_doc=SENIOR_TECH), _inspector()
        )
        self.assertIn("no position", out[0]["reason"])

    def test_a_missing_course_is_reported(self):
        out = qualification.findings(_template(course="QA-101"), _inspector(courses=["OTHER"]))
        self.assertEqual([f["kind"] for f in out], [qualification.KIND_COURSE])

    def test_a_lapsed_course_is_the_callers_problem_and_reads_as_one(self):
        """valid_courses holds only live completions, so an expired one simply is not in it."""
        out = qualification.findings(_template(course="QA-101"), _inspector(courses=[]))
        self.assertIn("lapsed", out[0]["reason"])

    def test_no_employee_record_is_unknown_rather_than_unqualified(self):
        """Two different claims, and only one of them is true."""
        out = qualification.findings(
            _template(position="Senior Technician", position_doc=SENIOR_TECH, course="QA-101"),
            _inspector(employee=None),
        )
        self.assertEqual([f["kind"] for f in out], [qualification.KIND_UNKNOWN])
        self.assertIn("Employee", out[0]["reason"])

    def test_both_requirements_can_fail_at_once_in_a_fixed_order(self):
        out = qualification.findings(
            _template(position="Senior Technician", position_doc=SENIOR_TECH, course="QA-101"),
            _inspector(position=JUNIOR_TECH, courses=[]),
        )
        self.assertEqual(
            [f["kind"] for f in out], [qualification.KIND_POSITION, qualification.KIND_COURSE]
        )


class TestFingerprint(unittest.TestCase):
    def test_the_same_findings_fingerprint_the_same(self):
        rows = [
            {"kind": "position", "requirement": "Senior Technician", "held": "Junior Technician"},
            {"kind": "course", "requirement": "QA-101", "held": ""},
        ]
        self.assertEqual(
            qualification.fingerprint("a@x.com", rows),
            qualification.fingerprint("a@x.com", list(reversed(rows))),
        )

    def test_a_different_person_fingerprints_differently(self):
        rows = [{"kind": "course", "requirement": "QA-101", "held": ""}]
        self.assertNotEqual(
            qualification.fingerprint("a@x.com", rows), qualification.fingerprint("b@x.com", rows)
        )

    def test_a_changed_finding_fingerprints_differently(self):
        """Otherwise a promotion would be silently deduped against the old warning."""
        before = [{"kind": "position", "requirement": "Senior Technician", "held": "Junior Technician"}]
        after = [{"kind": "position", "requirement": "Senior Technician", "held": "Senior Designer"}]
        self.assertNotEqual(
            qualification.fingerprint("a@x.com", before), qualification.fingerprint("a@x.com", after)
        )

    def test_no_findings_is_not_an_error(self):
        self.assertTrue(qualification.fingerprint("a@x.com", []))
        self.assertTrue(qualification.fingerprint("a@x.com", None))


class TestSummaryLine(unittest.TestCase):
    def test_it_names_the_requirement_and_what_they_hold(self):
        line = qualification.summary_line(
            {"kind": "position", "requirement": "Senior Technician", "held": "Junior Technician", "reason": "too junior"}
        )
        self.assertIn("Senior Technician", line)
        self.assertIn("Junior Technician", line)

    def test_a_course_line_does_not_claim_they_hold_nothing(self):
        line = qualification.summary_line(
            {"kind": "course", "requirement": "QA-101", "held": "", "reason": "not completed"}
        )
        self.assertNotIn("holds", line)


if __name__ == "__main__":
    unittest.main()
