"""Bench-free tests for goals and period reviews (WI-075 sub-phase J).

Every failure guarded here reports **good news**, which is why they are tests and not comments.

* An empty period divides by zero and rounds up to a perfect score. A quarter in which nobody
  inspected anything would report 100% first-pass yield.
* Half the metrics are better when smaller, and core's objective row carries no direction. The
  obvious comparison marks "NCRs raised: target 2" as *failed* every time the company does well.
* `target` is a `Data` field, so somebody types `95%` or `<= 2`. Treating an unreadable target as
  zero marks a goal Passed forever.
* And this module must generate reviews for **Annual only**: ERPNext's own daily scheduler owns
  the other four, and handling them here would create a second review beside every one core made.

Run: python -m unittest erpnext_enhancements.tests.test_quality_goals
"""

import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.quality import goals


def _obj(metric=None, target=None, objective=None):
    return {"custom_metric": metric, "target": target, "objective": objective}


class TestParseTarget(unittest.TestCase):
    def test_a_plain_number(self):
        self.assertEqual(goals.parse_target("95"), 95.0)
        self.assertEqual(goals.parse_target("2.5"), 2.5)
        self.assertEqual(goals.parse_target(3), 3.0)

    def test_what_people_actually_type(self):
        self.assertEqual(goals.parse_target("95%"), 95.0)
        self.assertEqual(goals.parse_target("<= 2"), 2.0)
        self.assertEqual(goals.parse_target("≤ 2"), 2.0)
        self.assertEqual(goals.parse_target("  7 days "), 7.0)
        self.assertEqual(goals.parse_target("2 per project"), 2.0)

    def test_a_negative_target(self):
        self.assertEqual(goals.parse_target("-3"), -3.0)

    def test_text_with_no_number_is_unreadable(self):
        for value in ("two", "", "   ", None, "as agreed", "%"):
            self.assertIsNone(goals.parse_target(value), value)

    def test_unreadable_is_not_zero(self):
        """The distinction the whole module rests on."""
        self.assertIsNone(goals.parse_target("two"))
        self.assertNotEqual(goals.parse_target("two"), 0.0)

    def test_a_boolean_is_not_a_number(self):
        self.assertIsNone(goals.parse_target(True))


class TestDirection(unittest.TestCase):
    def test_every_metric_declares_a_direction(self):
        for key in goals.METRIC_KEYS:
            self.assertIn(
                goals.direction_of(key), (goals.HIGHER_IS_BETTER, goals.LOWER_IS_BETTER), key
            )

    def test_an_unknown_metric_has_no_direction_and_is_not_defaulted(self):
        """A default here inverts the verdict for every 'fewer is better' metric, silently."""
        self.assertIsNone(goals.direction_of("made_up_metric"))
        self.assertIsNone(goals.direction_of(None))

    def test_yield_is_higher_better_and_failures_are_lower_better(self):
        self.assertEqual(goals.direction_of("first_pass_yield"), goals.HIGHER_IS_BETTER)
        for key in ("ncrs_raised", "critical_ncrs", "open_punch_items", "actions_reopened"):
            self.assertEqual(goals.direction_of(key), goals.LOWER_IS_BETTER, key)

    def test_the_catalog_has_no_duplicate_keys(self):
        self.assertEqual(len(goals.METRIC_KEYS), len(set(goals.METRIC_KEYS)))


class TestVerdict(unittest.TestCase):
    def test_higher_is_better_passes_at_or_above_target(self):
        self.assertEqual(goals.verdict(95, "95", "first_pass_yield", 10), goals.PASSED)
        self.assertEqual(goals.verdict(97, "95", "first_pass_yield", 10), goals.PASSED)
        self.assertEqual(goals.verdict(94, "95", "first_pass_yield", 10), goals.FAILED)

    def test_lower_is_better_passes_at_or_below_target(self):
        """The case a naive comparison gets backwards on every good month."""
        self.assertEqual(goals.verdict(1, "2", "ncrs_raised", 10), goals.PASSED)
        self.assertEqual(goals.verdict(2, "2", "ncrs_raised", 10), goals.PASSED)
        self.assertEqual(goals.verdict(3, "2", "ncrs_raised", 10), goals.FAILED)

    def test_an_empty_sample_decides_nothing(self):
        """A quarter with no inspections does not have perfect first-pass yield. It has none."""
        self.assertEqual(goals.verdict(100, "95", "first_pass_yield", 0), goals.OPEN)
        self.assertEqual(goals.verdict(0, "2", "ncrs_raised", 0), goals.OPEN)

    def test_a_sample_that_was_never_measured_decides_nothing(self):
        self.assertEqual(goals.verdict(None, "95", "first_pass_yield", 10), goals.OPEN)

    def test_an_unreadable_target_decides_nothing(self):
        """Treating it as zero would mark a lower-is-better goal Passed forever."""
        self.assertEqual(goals.verdict(5, "two", "ncrs_raised", 10), goals.OPEN)
        self.assertEqual(goals.verdict(5, "", "ncrs_raised", 10), goals.OPEN)

    def test_an_unknown_metric_decides_nothing(self):
        self.assertEqual(goals.verdict(5, "2", "made_up_metric", 10), goals.OPEN)

    def test_a_sample_size_of_none_is_not_treated_as_empty(self):
        """Some metrics are counts with no denominator; absent is not zero."""
        self.assertEqual(goals.verdict(1, "2", "ncrs_raised", None), goals.PASSED)

    def test_the_verdicts_match_cores_option_list(self):
        """These strings are written into `Quality Review Objective.status`, whose options are
        core's. An off-options value makes the row permanently unsaveable."""
        self.assertEqual(
            sorted({goals.PASSED, goals.FAILED, goals.OPEN}), ["Failed", "Open", "Passed"]
        )


class TestFloorRule(unittest.TestCase):
    def test_a_slacker_project_target_violates_the_floor(self):
        company = [_obj("first_pass_yield", "95")]
        project = [_obj("first_pass_yield", "90")]
        out = goals.floor_violations(project, company)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["company_target"], 95.0)

    def test_meeting_or_exceeding_the_floor_is_fine(self):
        company = [_obj("first_pass_yield", "95")]
        for target in ("95", "98"):
            self.assertEqual(goals.floor_violations([_obj("first_pass_yield", target)], company), [])

    def test_the_floor_runs_the_other_way_for_a_lower_is_better_metric(self):
        """Allowing MORE non-conformances than the company target is the slacker direction."""
        company = [_obj("ncrs_raised", "2")]
        self.assertEqual(len(goals.floor_violations([_obj("ncrs_raised", "5")], company)), 1)
        self.assertEqual(goals.floor_violations([_obj("ncrs_raised", "1")], company), [])

    def test_matching_is_on_metric_not_on_wording(self):
        """Two people writing 'first pass yield' and 'First-Pass Yield' is not a disagreement
        about the standard."""
        company = [_obj("first_pass_yield", "95", objective="First-Pass Yield")]
        project = [_obj("first_pass_yield", "90", objective="first pass yield")]
        self.assertEqual(len(goals.floor_violations(project, company)), 1)

    def test_a_metric_the_company_has_said_nothing_about_is_not_a_violation(self):
        """Inventing a floor from silence would block goals nobody objected to."""
        self.assertEqual(goals.floor_violations([_obj("ncrs_raised", "5")], [_obj("first_pass_yield", "95")]), [])

    def test_an_unreadable_target_is_not_silently_passed_as_compliant(self):
        """It is excluded from the floor check and reported separately by unreadable_targets."""
        company = [_obj("first_pass_yield", "95")]
        self.assertEqual(goals.floor_violations([_obj("first_pass_yield", "high")], company), [])
        self.assertEqual(goals.unreadable_targets([_obj("first_pass_yield", "high")]), ["first_pass_yield"])

    def test_an_unreadable_company_target_sets_no_floor(self):
        self.assertEqual(goals.floor_violations([_obj("ncrs_raised", "5")], [_obj("ncrs_raised", "lots")]), [])

    def test_empty_inputs_are_tolerated(self):
        self.assertEqual(goals.floor_violations([], []), [])
        self.assertEqual(goals.floor_violations(None, None), [])


class TestUnreadableTargets(unittest.TestCase):
    def test_it_names_the_offenders(self):
        rows = [_obj("ncrs_raised", "2"), _obj("first_pass_yield", "ninety-five")]
        self.assertEqual(goals.unreadable_targets(rows), ["first_pass_yield"])

    def test_a_blank_target_is_not_an_unreadable_one(self):
        """Nobody has set it yet; that is a different problem from typing something wrong."""
        self.assertEqual(goals.unreadable_targets([_obj("ncrs_raised", "")]), [])
        self.assertEqual(goals.unreadable_targets([_obj("ncrs_raised", None)]), [])


class TestReviewDue(unittest.TestCase):
    def test_it_refuses_the_frequencies_core_already_handles(self):
        """Returning False would be a correct-looking answer to a question this module must not
        be asked -- and answering it is how every review would come to be created twice."""
        for frequency in goals.CORE_FREQUENCIES:
            with self.assertRaises(ValueError):
                goals.review_due(frequency, date(2026, 1, 1))

    def test_an_annual_goal_fires_on_its_month_and_day(self):
        self.assertTrue(goals.review_due("Annual", date(2026, 1, 1), "January", 1))
        self.assertTrue(goals.review_due("Annual", date(2026, 4, 15), "April", 15))

    def test_it_does_not_fire_on_another_month(self):
        self.assertFalse(goals.review_due("Annual", date(2026, 2, 1), "January", 1))

    def test_it_does_not_fire_on_another_day(self):
        self.assertFalse(goals.review_due("Annual", date(2026, 1, 2), "January", 1))

    def test_the_month_defaults_to_january_rather_than_never(self):
        self.assertTrue(goals.review_due("Annual", date(2026, 1, 1), None, 1))

    def test_a_day_beyond_the_month_is_clamped_rather_than_silenced(self):
        """`Quality Goal.date` is a Select of 1..30, so a February goal asking for the 30th would
        otherwise never fire in any year."""
        self.assertTrue(goals.review_due("Annual", date(2026, 2, 28), "February", 30))
        self.assertFalse(goals.review_due("Annual", date(2026, 2, 27), "February", 30))

    def test_a_missing_day_defaults_to_the_first(self):
        self.assertTrue(goals.review_due("Annual", date(2026, 1, 1), "January", None))

    def test_an_unknown_frequency_never_fires(self):
        self.assertFalse(goals.review_due("None", date(2026, 1, 1)))
        self.assertFalse(goals.review_due(None, date(2026, 1, 1)))

    def test_an_unparseable_date_never_fires(self):
        self.assertFalse(goals.review_due("Annual", "not a date", "January", 1))

    def test_a_misspelt_month_never_fires_rather_than_firing_every_month(self):
        self.assertFalse(goals.review_due("Annual", date(2026, 1, 1), "Janurary", 1))


class TestPeriodBounds(unittest.TestCase):
    def test_the_period_ends_the_day_before_the_review(self):
        """Including the current day means the same inspection can land in two consecutive
        periods depending on the hour the scheduler ran."""
        start, end = goals.period_bounds("Daily", date(2026, 3, 10))
        self.assertEqual((start, end), (date(2026, 3, 9), date(2026, 3, 9)))

    def test_a_week_is_seven_days_ending_yesterday(self):
        start, end = goals.period_bounds("Weekly", date(2026, 3, 10))
        self.assertEqual((start, end), (date(2026, 3, 3), date(2026, 3, 9)))
        self.assertEqual((end - start).days, 6)

    def test_a_month_ends_the_day_before(self):
        start, end = goals.period_bounds("Monthly", date(2026, 4, 1))
        self.assertEqual((start, end), (date(2026, 3, 1), date(2026, 3, 31)))

    def test_a_quarter_covers_three_months(self):
        start, end = goals.period_bounds("Quarterly", date(2026, 4, 1))
        self.assertEqual((start, end), (date(2026, 1, 1), date(2026, 3, 31)))

    def test_a_year_covers_twelve_months(self):
        start, end = goals.period_bounds("Annual", date(2027, 1, 1))
        self.assertEqual((start, end), (date(2026, 1, 1), date(2026, 12, 31)))

    def test_month_arithmetic_clamps_rather_than_overflowing(self):
        """31 March minus one month is 28 February, not 3 March."""
        start, _end = goals.period_bounds("Monthly", date(2026, 4, 1))
        self.assertEqual(start.month, 3)
        start, end = goals.period_bounds("Monthly", date(2026, 3, 31))
        self.assertEqual(end, date(2026, 3, 30))
        self.assertEqual(start, date(2026, 3, 1))

    def test_a_leap_day_does_not_crash(self):
        start, end = goals.period_bounds("Annual", date(2024, 3, 1))
        self.assertEqual(end, date(2024, 2, 29))
        self.assertEqual(start, date(2023, 3, 1))

    def test_an_unknown_frequency_has_no_period(self):
        self.assertEqual(goals.period_bounds("None", date(2026, 1, 1)), (None, None))

    def test_an_unparseable_date_has_no_period(self):
        self.assertEqual(goals.period_bounds("Annual", "nope"), (None, None))


class TestSummarise(unittest.TestCase):
    def test_it_counts_each_verdict(self):
        rows = [{"status": "Passed"}, {"status": "Failed"}, {"status": "Open"}, {"status": "Passed"}]
        self.assertEqual(goals.summarise(rows), (2, 1, 1, 4))

    def test_a_row_with_no_status_counts_as_open(self):
        self.assertEqual(goals.summarise([{"status": None}]), (0, 0, 1, 1))

    def test_nothing_is_zero_of_zero(self):
        self.assertEqual(goals.summarise([]), (0, 0, 0, 0))
        self.assertEqual(goals.summarise(None), (0, 0, 0, 0))


class TestTheGlueMatchesTheCatalog(unittest.TestCase):
    """Source- and fixture-level guards. Each one would drift silently.

    A metric declared but not computed, or offered in the Select but not declared, produces a
    review objective that is Open forever with a note nobody reads as a bug.
    """

    @classmethod
    def setUpClass(cls):
        import io
        import json
        import re

        app = REPO_ROOT / "erpnext_enhancements"
        cls.source = (app / "quality" / "reviews.py").read_text(encoding="utf-8")
        cls.code = re.sub(r'"""[\s\S]*?"""', "", cls.source)
        cls.code = re.sub(r"^\s*#.*$", "", cls.code, flags=re.MULTILINE)
        with io.open(app / "fixtures" / "custom_field.json", encoding="utf-8") as handle:
            cls.fields = {r["name"]: r for r in json.load(handle)}
        with io.open(app / "fixtures" / "property_setter.json", encoding="utf-8") as handle:
            cls.setters = {r["name"]: r for r in json.load(handle)}

    def test_every_declared_metric_has_a_computer(self):
        import re

        registered = set(re.findall(r'"(\w+)": _\w+,', self.code))
        missing = sorted(set(goals.METRIC_KEYS) - registered)
        self.assertEqual(
            missing,
            [],
            f"These metrics can be chosen on a goal and can never be computed: {missing}. "
            "Their objectives would stay Open forever with a note nobody reads as a bug.",
        )

    def test_no_computer_exists_for_a_metric_nobody_can_choose(self):
        import re

        registered = set(re.findall(r'"(\w+)": _\w+,', self.code))
        extra = sorted(registered - set(goals.METRIC_KEYS))
        self.assertEqual(extra, [], f"computed but not offered on a goal: {extra}")

    def test_the_select_offers_exactly_the_declared_metrics(self):
        field = self.fields.get("Quality Goal Objective-custom_metric")
        self.assertIsNotNone(field, "the metric Select is missing from the fixtures")
        offered = [o for o in (field["options"] or "").split("\n") if o.strip()]
        self.assertEqual(
            offered,
            list(goals.METRIC_KEYS),
            "an off-options Select value makes the row permanently unsaveable, and a missing "
            "option makes a metric unreachable",
        )

    def test_the_select_has_a_blank_first_option(self):
        """Otherwise every objective silently acquires the first metric in the list."""
        field = self.fields["Quality Goal Objective-custom_metric"]
        self.assertTrue((field["options"] or "").startswith("\n"))

    def test_annual_is_added_to_the_frequency_options_and_nothing_is_removed(self):
        setter = self.setters.get("Quality Goal-frequency-options")
        self.assertIsNotNone(setter, "the Annual frequency Property Setter is missing")
        options = (setter["value"] or "").split("\n")
        for original in ("None", "Daily", "Weekly", "Monthly", "Quarterly"):
            self.assertIn(original, options, f"{original} was dropped; existing goals would break")
        self.assertIn(goals.ANNUAL, options)

    def test_the_review_status_is_re_derived_after_the_actuals_land(self):
        """Core ran set_status() before any actual existed. Without a second call the parent
        keeps the verdict it reached on empty rows."""
        self.assertIn("doc.set_status()", self.code)

    def test_the_annual_sweep_asks_only_about_annual_goals(self):
        self.assertRegex(self.code, r'"frequency":\s*goals\.ANNUAL')

    def test_the_annual_sweep_does_not_duplicate_a_review(self):
        """Core's own create_review has no such guard, so a re-run duplicates."""
        self.assertIn("_already_reviewed_today", self.code)
        self.assertRegex(self.code, r"if _already_reviewed_today\([\s\S]{0,60}?continue")

    def test_the_closed_stamp_is_written_where_an_action_closes(self):
        """days-to-close is otherwise guessed from `modified`, which any later edit moves."""
        routing = (REPO_ROOT / "erpnext_enhancements" / "quality" / "routing.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("custom_closed_on", routing)
        self.assertIn("Quality Action-custom_closed_on", self.fields)

    def test_a_point_in_time_metric_carries_no_sample(self):
        """Zero open punch items is meaningful whether or not the period was busy; zero
        non-conformances across zero inspections is not."""
        self.assertRegex(self.code, r"def _open_punch_items[\s\S]{0,900}?return float\(count\), None,")

    def test_a_count_metric_uses_activity_as_its_denominator(self):
        self.assertRegex(self.code, r"def _ncr_count[\s\S]{0,700}?sample = _activity\(")

    def test_nothing_in_the_glue_can_fail_a_save(self):
        for name in ("on_review_validate", "on_goal_validate", "on_meeting_validate"):
            self.assertRegex(
                self.code,
                rf"def {name}\([\s\S]{{0,1800}}?except Exception:",
                f"{name} must swallow its own failures",
            )

    def test_the_floor_block_mode_is_the_only_path_that_throws(self):
        """Warn must not raise, or the dial's two settings would be the same setting."""
        self.assertRegex(self.code, r"if mode == FLOOR_BLOCK:\s*\n\s*frappe\.throw\(")
        self.assertIn("frappe.msgprint(message", self.code)


if __name__ == "__main__":
    unittest.main()
