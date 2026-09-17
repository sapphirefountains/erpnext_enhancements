"""Bench-free tests for ``workforce/overtime.py`` — the weekly regular / overtime split.

Guards the class of bug where the payroll export puts hours in the wrong column:
a workweek that starts on the wrong day, hours worked before the period in the
same workweek not pushing the threshold, an interval straddling the 40-hour line
credited entirely to one side, paused time counted as worked, or an interval
outside the period leaking into the result. Pure functions; no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_workforce_overtime
"""

import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.workforce import overtime  # noqa: E402


def shift(day, start_hour, hours, paused=0):
    start = datetime(day.year, day.month, day.day, start_hour, 0, 0)
    return {"start": start, "end": start + timedelta(hours=hours), "paused_seconds": paused}


# 2026-09-13 is a Sunday; 2026-09-14 a Monday.
SUN = date(2026, 9, 13)
MON = date(2026, 9, 14)


class TestWorkweekStart(unittest.TestCase):
    def test_sunday_week_from_each_weekday(self):
        for offset in range(7):
            self.assertEqual(overtime.workweek_start(SUN + timedelta(days=offset), "Sunday"), SUN)

    def test_monday_week_from_each_weekday(self):
        for offset in range(7):
            self.assertEqual(overtime.workweek_start(MON + timedelta(days=offset), "Monday"), MON)

    def test_a_sunday_under_monday_weeks_belongs_to_the_previous_week(self):
        self.assertEqual(overtime.workweek_start(SUN, "Monday"), MON - timedelta(days=7))

    def test_accepts_a_datetime(self):
        self.assertEqual(overtime.workweek_start(datetime(2026, 9, 16, 14, 30), "Sunday"), SUN)

    def test_unknown_week_start_defaults_to_sunday(self):
        self.assertEqual(overtime.workweek_start(MON, "Wednesday"), SUN)


class TestIntervalHours(unittest.TestCase):
    def test_net_of_pauses(self):
        self.assertEqual(overtime.interval_hours(shift(MON, 8, 9, paused=3600)), 8.0)

    def test_never_negative(self):
        self.assertEqual(overtime.interval_hours(shift(MON, 8, 1, paused=7200)), 0.0)

    def test_missing_end_is_zero(self):
        self.assertEqual(overtime.interval_hours({"start": datetime(2026, 9, 14, 8), "end": None}), 0.0)


class TestSplitHours(unittest.TestCase):
    def test_under_forty_is_all_regular(self):
        intervals = [shift(MON + timedelta(days=d), 8, 8) for d in range(4)]  # 32 h
        self.assertEqual(overtime.split_hours(intervals, MON, MON + timedelta(days=6)),
                         {"regular_hours": 32.0, "overtime_hours": 0.0})

    def test_over_forty_is_overtime(self):
        intervals = [shift(MON + timedelta(days=d), 8, 10) for d in range(5)]  # 50 h
        self.assertEqual(overtime.split_hours(intervals, MON, MON + timedelta(days=6)),
                         {"regular_hours": 40.0, "overtime_hours": 10.0})

    def test_the_interval_that_crosses_the_line_is_split(self):
        intervals = [shift(MON + timedelta(days=d), 8, 9) for d in range(5)]  # 45 h; Friday is 36 -> 45
        result = overtime.split_hours(intervals, MON + timedelta(days=4), MON + timedelta(days=4))
        self.assertEqual(result, {"regular_hours": 4.0, "overtime_hours": 5.0})

    def test_hours_before_the_period_push_the_threshold(self):
        # 38 h Sun-Thu (before the 16th), then 10 h on the 16th (Wednesday)
        before = [shift(SUN + timedelta(days=d), 8, 9.5) for d in range(4)]  # 13,14,15 + Sun 13? -> Sun..Wed
        # keep the "before" set strictly before the 16th
        before = [shift(SUN, 8, 9.5), shift(MON, 8, 9.5), shift(MON + timedelta(days=1), 8, 9.5), shift(MON + timedelta(days=1), 18, 9.5)]
        on_16th = [shift(date(2026, 9, 16), 8, 10)]
        result = overtime.split_hours(before + on_16th, date(2026, 9, 16), date(2026, 9, 30))
        self.assertEqual(result, {"regular_hours": 2.0, "overtime_hours": 8.0})

    def test_intervals_outside_the_period_are_not_counted(self):
        intervals = [shift(MON + timedelta(days=d), 8, 10) for d in range(5)]  # 50 h in one week
        # period covers only Monday: 10 h regular, and Friday's OT is not in the result
        self.assertEqual(overtime.split_hours(intervals, MON, MON), {"regular_hours": 10.0, "overtime_hours": 0.0})

    def test_weeks_do_not_bleed_into_each_other(self):
        week1 = [shift(MON + timedelta(days=d), 8, 10) for d in range(5)]  # 50 h
        week2 = [shift(MON + timedelta(days=7 + d), 8, 6) for d in range(5)]  # 30 h
        result = overtime.split_hours(week1 + week2, MON, MON + timedelta(days=13))
        self.assertEqual(result, {"regular_hours": 70.0, "overtime_hours": 10.0})

    def test_week_start_changes_the_answer(self):
        # Sat 12 h + Sun 12 h + Mon..Wed 8 h each = 48 h in a Sunday-start week only if Sat is in it.
        sat = SUN - timedelta(days=1)
        intervals = [shift(sat, 8, 12), shift(SUN, 8, 12)] + [shift(MON + timedelta(days=d), 8, 8) for d in range(3)]
        period = (sat, MON + timedelta(days=2))
        sunday = overtime.split_hours(intervals, *period, week_start="Sunday")
        monday = overtime.split_hours(intervals, *period, week_start="Monday")
        # Sunday weeks: Sat alone (12), then Sun..Wed = 36 -> no OT at all
        self.assertEqual(sunday, {"regular_hours": 48.0, "overtime_hours": 0.0})
        # Monday weeks: Sat+Sun = 24 in the prior week, Mon..Wed = 24 -> still no OT
        self.assertEqual(monday, {"regular_hours": 48.0, "overtime_hours": 0.0})
        # but push Monday-week hours over: add Thu+Fri 10 h each
        more = intervals + [shift(MON + timedelta(days=3), 8, 10), shift(MON + timedelta(days=4), 8, 10)]
        period = (sat, MON + timedelta(days=4))
        self.assertEqual(overtime.split_hours(more, *period, week_start="Monday")["overtime_hours"], 4.0)
        self.assertEqual(overtime.split_hours(more, *period, week_start="Sunday")["overtime_hours"], 16.0)

    def test_paused_time_is_not_worked(self):
        intervals = [shift(MON + timedelta(days=d), 8, 10, paused=3600) for d in range(5)]  # 45 h net
        self.assertEqual(overtime.split_hours(intervals, MON, MON + timedelta(days=6)),
                         {"regular_hours": 40.0, "overtime_hours": 5.0})

    def test_custom_threshold(self):
        intervals = [shift(MON + timedelta(days=d), 8, 8) for d in range(5)]  # 40 h
        self.assertEqual(overtime.split_hours(intervals, MON, MON + timedelta(days=6), weekly_hours=35),
                         {"regular_hours": 35.0, "overtime_hours": 5.0})

    def test_zero_threshold_means_no_overtime_rule(self):
        intervals = [shift(MON + timedelta(days=d), 8, 12) for d in range(6)]  # 72 h
        self.assertEqual(overtime.split_hours(intervals, MON, MON + timedelta(days=6), weekly_hours=0),
                         {"regular_hours": 72.0, "overtime_hours": 0.0})

    def test_chronology_within_the_week_is_enforced(self):
        # given out of order, the LAST shift of the week is still the one that goes to OT
        shifts = [shift(MON + timedelta(days=d), 8, 10) for d in range(5)]
        result = overtime.split_hours(list(reversed(shifts)), MON + timedelta(days=4), MON + timedelta(days=4))
        self.assertEqual(result, {"regular_hours": 0.0, "overtime_hours": 10.0})

    def test_empty_input(self):
        self.assertEqual(overtime.split_hours([], MON, MON), {"regular_hours": 0.0, "overtime_hours": 0.0})

    def test_accepts_string_dates(self):
        intervals = [shift(MON, 8, 8)]
        self.assertEqual(overtime.split_hours(intervals, "2026-09-14", "2026-09-14"),
                         {"regular_hours": 8.0, "overtime_hours": 0.0})


if __name__ == "__main__":
    unittest.main()
