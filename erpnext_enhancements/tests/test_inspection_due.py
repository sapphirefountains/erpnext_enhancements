"""Bench-free tests for when an inspection is due (WI-075 sub-phase I).

Two failures shape this file, and both of them are silent.

**The skipped stage.** ``Project.custom_build_status`` is a Select somebody types into, not a
workflow, so a project can go from Procurement straight to Ready for Install in one save. A
trigger written as ``current == "QA"`` was never true at any moment a sweep looked, the pre-final
commissioning check never comes up, and afterwards it looks exactly like a project that has not
got there yet. `test_a_project_that_skipped_the_trigger_status_is_still_due` is the whole reason
the rule is "reached or passed".

**The unplaceable status.** A renamed or blank status cannot be put on the scale. Answering "not
due" would make the sweep report clean forever, on every project, with nothing to investigate —
the trailing-space failure in a different costume. It comes back UNKNOWN.

Run: python -m unittest erpnext_enhancements.tests.test_inspection_due
"""

import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.quality import due

#: The live options of Project.custom_build_status, in order. The blank first option is dropped
#: by the caller; the order is what carries the meaning here.
ORDER = (
    "Design Complete",
    "Procurement",
    "Assembly",
    "QA",
    "Ready for Install",
    "Installed",
    "Commissioned",
)

TODAY = date(2026, 9, 14)


def _milestone(basis="Build Status", value="QA", interval=0, multi_day=0, disabled=0, key="m1"):
    return {
        "milestone_key": key,
        "trigger_basis": basis,
        "trigger_value": value,
        "interval_days": interval,
        "multi_day_only": multi_day,
        "disabled": disabled,
    }


def _context(status="QA", count=0, last=None, multi_day=True, template=True, today=TODAY):
    return {
        "build_status": status,
        "status_order": ORDER,
        "inspection_count": count,
        "last_inspection_on": last,
        "is_multi_day": multi_day,
        "has_active_template": template,
        "today": today,
    }


class TestStatusReached(unittest.TestCase):
    def test_the_exact_status_counts_as_reached(self):
        self.assertTrue(due.status_reached("QA", "QA", ORDER))

    def test_a_later_status_counts_as_reached(self):
        self.assertTrue(due.status_reached("Commissioned", "QA", ORDER))

    def test_an_earlier_status_has_not_reached(self):
        self.assertFalse(due.status_reached("Procurement", "QA", ORDER))

    def test_a_blank_status_cannot_be_placed(self):
        for blank in ("", "   ", None):
            self.assertIsNone(due.status_reached(blank, "QA", ORDER))

    def test_an_unknown_status_cannot_be_placed(self):
        """A renamed option. Answering False here is what makes a sweep report clean forever."""
        self.assertIsNone(due.status_reached("Quality Assurance", "QA", ORDER))

    def test_an_unknown_trigger_cannot_be_placed(self):
        self.assertIsNone(due.status_reached("QA", "Quality Assurance", ORDER))

    def test_none_is_not_a_polite_false(self):
        self.assertIsNot(due.status_reached("Quality Assurance", "QA", ORDER), False)


class TestBuildStatusTrigger(unittest.TestCase):
    def test_reaching_the_trigger_makes_it_due(self):
        verdict = due.assess(_milestone(), _context(status="QA"))
        self.assertEqual(verdict["state"], due.STATE_DUE)

    def test_a_project_that_skipped_the_trigger_status_is_still_due(self):
        """The failure this rule exists for.

        Build status is a Select, not a workflow. A project that jumped Procurement -> Ready for
        Install was never equal to QA at any moment a sweep looked, and an equality trigger would
        lose the commissioning check with nothing to see afterwards.
        """
        verdict = due.assess(_milestone(value="QA"), _context(status="Ready for Install"))
        self.assertEqual(verdict["state"], due.STATE_DUE)

    def test_it_stays_due_until_an_inspection_exists(self):
        self.assertEqual(
            due.assess(_milestone(), _context(status="Commissioned"))["state"], due.STATE_DUE
        )
        self.assertEqual(
            due.assess(_milestone(), _context(status="Commissioned", count=1))["state"],
            due.STATE_DONE,
        )

    def test_before_the_trigger_it_is_waiting_not_due(self):
        verdict = due.assess(_milestone(), _context(status="Procurement"))
        self.assertEqual(verdict["state"], due.STATE_WAITING)
        self.assertIn("QA", verdict["reason"])

    def test_an_unplaceable_status_is_reported_rather_than_read_as_not_due(self):
        verdict = due.assess(_milestone(), _context(status="Quality Assurance"))
        self.assertEqual(verdict["state"], due.STATE_UNKNOWN)
        self.assertIn("not one of the known options", verdict["reason"])

    def test_a_blank_status_is_unknown_rather_than_waiting(self):
        """654 projects and a Select that is blank on many of them. Silence there is the bug."""
        self.assertEqual(due.assess(_milestone(), _context(status=""))["state"], due.STATE_UNKNOWN)


class TestCalendarTrigger(unittest.TestCase):
    def test_never_inspected_is_due_and_marked_as_a_first_time(self):
        """True, and flagged: turning this on would otherwise page somebody about every service
        project at once, and 'never inspected' is a different conversation from 'overdue'."""
        verdict = due.assess(
            _milestone(basis="Calendar Interval", value=None, interval=90), _context(last=None)
        )
        self.assertEqual(verdict["state"], due.STATE_DUE)
        self.assertTrue(verdict["first_time"])

    def test_inside_the_interval_is_done(self):
        verdict = due.assess(
            _milestone(basis="Calendar Interval", value=None, interval=90),
            _context(last=date(2026, 8, 1)),
        )
        self.assertEqual(verdict["state"], due.STATE_DONE)

    def test_past_the_interval_is_due_and_not_a_first_time(self):
        verdict = due.assess(
            _milestone(basis="Calendar Interval", value=None, interval=90),
            _context(last=date(2026, 5, 1)),
        )
        self.assertEqual(verdict["state"], due.STATE_DUE)
        self.assertFalse(verdict["first_time"])

    def test_exactly_on_the_interval_is_due(self):
        verdict = due.assess(
            _milestone(basis="Calendar Interval", value=None, interval=90),
            _context(last=date(2026, 6, 16)),
        )
        self.assertEqual(verdict["state"], due.STATE_DUE)

    def test_a_missing_interval_never_comes_round_and_says_so(self):
        """Zero would otherwise mean 'due every single sweep' or 'never', depending on the
        comparison — both wrong, and neither visible."""
        verdict = due.assess(
            _milestone(basis="Calendar Interval", value=None, interval=0), _context(last=None)
        )
        self.assertEqual(verdict["state"], due.STATE_UNKNOWN)

    def test_string_dates_are_accepted(self):
        """Frappe hands these back as strings from a raw query and as dates from a doc."""
        verdict = due.assess(
            _milestone(basis="Calendar Interval", value=None, interval=90),
            _context(last="2026-05-01", today="2026-09-14"),
        )
        self.assertEqual(verdict["state"], due.STATE_DUE)

    def test_the_build_status_is_irrelevant_to_a_calendar_check(self):
        """A Service project has no build status, and requiring one would silence the whole
        recurring cadence on every project it applies to."""
        verdict = due.assess(
            _milestone(basis="Calendar Interval", value=None, interval=90),
            _context(status="", last=date(2026, 5, 1)),
        )
        self.assertEqual(verdict["state"], due.STATE_DUE)


class TestManualTrigger(unittest.TestCase):
    def test_a_manual_milestone_is_never_announced_as_due(self):
        """The correct answer for a design review gate and an event nobody has scheduled."""
        verdict = due.assess(_milestone(basis="Manual", value=None), _context(status="QA"))
        self.assertEqual(verdict["state"], due.STATE_MANUAL)

    def test_a_manual_milestone_with_an_inspection_is_done(self):
        verdict = due.assess(_milestone(basis="Manual", value=None), _context(count=1))
        self.assertEqual(verdict["state"], due.STATE_DONE)

    def test_a_milestone_with_no_basis_at_all_is_treated_as_manual(self):
        verdict = due.assess({"milestone_key": "m", "trigger_basis": None}, _context())
        self.assertEqual(verdict["state"], due.STATE_MANUAL)


class TestSkips(unittest.TestCase):
    def test_a_disabled_milestone_is_skipped(self):
        self.assertEqual(
            due.assess(_milestone(disabled=1), _context())["state"], due.STATE_SKIPPED
        )

    def test_the_mid_event_check_does_not_apply_to_a_one_day_job(self):
        verdict = due.assess(_milestone(multi_day=1), _context(multi_day=False))
        self.assertEqual(verdict["state"], due.STATE_SKIPPED)

    def test_it_applies_to_a_multi_day_job(self):
        verdict = due.assess(_milestone(multi_day=1), _context(multi_day=True, status="QA"))
        self.assertEqual(verdict["state"], due.STATE_DUE)

    def test_unknown_dates_are_skipped_with_a_reason_that_says_so(self):
        """'Why is my mid-event check not showing' deserves an answer, and 'nobody recorded how
        long this event runs' is a different problem from 'the check does not apply'."""
        verdict = due.assess(_milestone(multi_day=1), _context(multi_day=None))
        self.assertEqual(verdict["state"], due.STATE_SKIPPED)
        self.assertIn("no start and end date", verdict["reason"])


class TestBlocked(unittest.TestCase):
    def test_a_due_milestone_with_no_checklist_is_still_due(self):
        """Only the Build commissioning checklist has ever been written down. Dropping the rest
        would turn a gap in what the company has recorded into a gap nobody can see."""
        verdict = due.assess(_milestone(), _context(status="QA", template=False))
        self.assertEqual(verdict["state"], due.STATE_DUE)
        self.assertTrue(verdict["blocked"])

    def test_a_due_milestone_with_a_checklist_is_not_blocked(self):
        self.assertFalse(due.assess(_milestone(), _context(status="QA"))["blocked"])

    def test_a_waiting_milestone_is_not_reported_as_blocked(self):
        """It is not blocked on anything yet; saying so would make every project look broken."""
        self.assertFalse(
            due.assess(_milestone(), _context(status="Procurement", template=False))["blocked"]
        )


class TestCollections(unittest.TestCase):
    def test_due_milestones_returns_only_the_due_ones_in_order(self):
        pairs = [
            (_milestone(key="a"), _context(status="QA")),
            (_milestone(key="b"), _context(status="Procurement")),
            (_milestone(key="c"), _context(status="Installed")),
        ]
        out = due.due_milestones(pairs)
        self.assertEqual([m["milestone_key"] for m, _v in out], ["a", "c"])

    def test_needs_attention_also_surfaces_the_unknowable_ones(self):
        """The group a sweep would otherwise never mention, because it produces silence."""
        pairs = [
            (_milestone(key="a"), _context(status="QA")),
            (_milestone(key="b"), _context(status="Quality Assurance")),
            (_milestone(key="c"), _context(status="Procurement")),
        ]
        out = due.needs_attention(pairs)
        self.assertEqual([m["milestone_key"] for m, _v in out], ["a", "b"])

    def test_empty_is_tolerated(self):
        self.assertEqual(due.due_milestones([]), [])
        self.assertEqual(due.due_milestones(None), [])
        self.assertEqual(due.needs_attention(None), [])


class TestNoticeDue(unittest.TestCase):
    def test_never_mentioned_is_mentioned(self):
        self.assertTrue(due.notice_due(None, TODAY))

    def test_inside_the_window_is_not_mentioned_again(self):
        self.assertFalse(due.notice_due(date(2026, 9, 12), TODAY, every_days=7))

    def test_past_the_window_is_mentioned_again(self):
        self.assertTrue(due.notice_due(date(2026, 9, 1), TODAY, every_days=7))

    def test_a_zero_window_does_not_mean_every_single_sweep(self):
        """A misconfigured 0 would otherwise re-send daily about something already decided."""
        self.assertFalse(due.notice_due(TODAY, TODAY, every_days=0))

    def test_an_unparseable_today_mentions_nothing_rather_than_everything(self):
        self.assertFalse(due.notice_due(None, "not a date"))


class TestSchedulingGlue(unittest.TestCase):
    """Source-level guards on the frappe half, which no bench-free test can exercise.

    Each one is a decision that would be invisible if it drifted: the sweep would keep running,
    the list would keep rendering, and the answer would quietly be wrong.
    """

    @classmethod
    def setUpClass(cls):
        import re

        path = REPO_ROOT / "erpnext_enhancements" / "quality" / "scheduling.py"
        cls.source = path.read_text(encoding="utf-8")
        # Strip comments and docstrings: a comment explaining why something is absent
        # necessarily names the thing it is warning about.
        without_docstrings = re.sub(r'"""[\s\S]*?"""', "", cls.source)
        cls.code = re.sub(r"^\s*#.*$", "", without_docstrings, flags=re.MULTILINE)

    def test_the_status_scale_is_read_from_meta_not_copied(self):
        """A copy here would be a second definition of the company's build sequence that
        nothing keeps in step with the first, and the drift would be silent."""
        self.assertIn("get_field(\"custom_build_status\")", self.code)
        for option in ("Ready for Install", "Design Complete", "Commissioned"):
            self.assertNotIn(
                option,
                self.code,
                f"{option!r} is hardcoded in scheduling.py; the order must come from meta",
            )

    def test_a_cancelled_inspection_does_not_mark_a_milestone_done(self):
        """A cancelled inspection is one that did not happen."""
        self.assertRegex(self.code, r'"docstatus":\s*\["!=",\s*2\]')

    def test_only_a_submitted_inspection_restarts_a_calendar_cadence(self):
        """An abandoned draft would otherwise buy another ninety days of silence."""
        self.assertRegex(self.code, r'row\.get\("docstatus"\)\s*==\s*1')

    def test_the_sweep_cannot_take_down_the_daily_queue(self):
        self.assertRegex(self.code, r"def sweep\(\):[\s\S]{0,900}?except Exception:")

    def test_one_unreadable_project_does_not_stop_the_rest(self):
        self.assertRegex(self.code, r"assess_project\(project_row[\s\S]{0,400}?except Exception:")

    def test_unknown_dates_stay_none_rather_than_becoming_false(self):
        """`None` is not a polite `False` here -- due.assess reports the two differently."""
        self.assertRegex(self.code, r"def _is_multi_day[\s\S]{0,400}?return None")

    def test_the_digest_says_when_a_milestone_has_no_checklist(self):
        """Filtering those out would turn a gap in what the company has written down into a gap
        nobody can see."""
        self.assertIn("no checklist has been written", self.code)


if __name__ == "__main__":
    unittest.main()
