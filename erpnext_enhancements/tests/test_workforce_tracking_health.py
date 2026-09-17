"""Bench-free tests for ``workforce/tracking_health.py`` — the trail scoring.

Guards the class of bug where a supervisor is told a technician's phone was
reporting when it was not (or the reverse): a gap that goes uncounted because it
touches the start or end of the interval, coverage that exceeds 100 % or drops
below 0, a "Good" verdict on an interval with no fixes at all, a stop that creeps
across a car park one fix at a time, or dwell credited to a site from a fix pair
that straddled the fence. Every function here is pure, so no ``frappe`` stub is
needed and this suite can share a process with anything.

Run: python -m unittest erpnext_enhancements.tests.test_workforce_tracking_health
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.workforce import tracking_health as th  # noqa: E402

T0 = datetime(2026, 9, 17, 8, 0, 0)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def fixes(*minutes):
    return [at(m) for m in minutes]


def point(minutes, lat, lng):
    return {"timestamp": at(minutes), "latitude": lat, "longitude": lng}


class TestHaversine(unittest.TestCase):
    def test_zero_distance(self):
        self.assertEqual(th.haversine_m(40.76, -111.89, 40.76, -111.89), 0.0)

    def test_one_degree_of_latitude_is_about_111_km(self):
        self.assertAlmostEqual(th.haversine_m(40.0, -111.0, 41.0, -111.0) / 1000.0, 111.2, delta=0.5)

    def test_tolerates_strings_and_none(self):
        self.assertEqual(th.haversine_m("40.0", "-111.0", None, None), th.haversine_m(40.0, -111.0, 0, 0))


class TestGaps(unittest.TestCase):
    def test_no_fixes_is_one_gap_over_the_whole_span(self):
        self.assertEqual(th.gaps(at(0), at(60), []), [{"from": at(0), "to": at(60), "minutes": 60.0}])

    def test_regular_heartbeats_produce_no_gap(self):
        self.assertEqual(th.gaps(at(0), at(60), fixes(5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55)), [])

    def test_a_leading_silence_counts(self):
        # phone woke up 40 minutes in
        self.assertEqual(th.gaps(at(0), at(60), fixes(40, 45, 50, 55)), [{"from": at(0), "to": at(40), "minutes": 40.0}])

    def test_a_trailing_silence_counts(self):
        self.assertEqual(th.gaps(at(0), at(60), fixes(5, 10)), [{"from": at(10), "to": at(60), "minutes": 50.0}])

    def test_the_gap_spans_the_two_fixes_around_it(self):
        gaps = th.gaps(at(0), at(60), fixes(5, 10, 40, 45, 50, 55))
        self.assertEqual(gaps, [{"from": at(10), "to": at(40), "minutes": 30.0}])

    def test_a_silence_just_over_the_threshold_is_a_gap_and_just_under_is_not(self):
        self.assertEqual(len(th.gaps(at(0), at(60), fixes(5, 21, 30, 40, 50, 55), gap_minutes=15)), 1)
        self.assertEqual(th.gaps(at(0), at(60), fixes(5, 20, 30, 40, 50, 55), gap_minutes=15), [])

    def test_fixes_outside_the_span_are_ignored(self):
        # a fix from the previous job says nothing about this one
        self.assertEqual(th.gaps(at(0), at(30), fixes(-5, 5, 10, 15, 20, 25, 40)), [])

    def test_unsorted_input_is_sorted(self):
        self.assertEqual(th.gaps(at(0), at(60), fixes(55, 5, 30, 20, 45, 10, 40)), [])

    def test_a_span_under_a_minute_has_no_gaps(self):
        self.assertEqual(th.gaps(at(0), at(0) + timedelta(seconds=30), []), [])


class TestComputeHealth(unittest.TestCase):
    def test_full_coverage_is_good(self):
        h = th.compute_health(at(0), at(60), fixes(5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55))
        self.assertEqual(h["health"], "Good")
        self.assertEqual(h["coverage_pct"], 100.0)
        self.assertEqual(h["gap_minutes"], 0.0)
        self.assertEqual(h["fix_count"], 11)
        self.assertEqual(h["last_fix_at"], at(55))

    def test_no_fixes_is_none_not_gaps(self):
        h = th.compute_health(at(0), at(60), [])
        self.assertEqual(h["health"], "None")
        self.assertEqual(h["coverage_pct"], 0.0)
        self.assertEqual(h["gap_minutes"], 60.0)
        self.assertIsNone(h["last_fix_at"])

    def test_tracking_off_is_off_whatever_the_trail(self):
        self.assertEqual(th.compute_health(at(0), at(60), [], tracking_enabled=False)["health"], "Off")
        self.assertEqual(th.compute_health(at(0), at(60), fixes(5, 10), tracking_enabled=False)["health"], "Off")

    def test_ninety_percent_is_the_line(self):
        # 60-minute span, one 6-minute silence over a 5-minute threshold -> 90 % -> Good
        good = th.compute_health(at(0), at(60), fixes(1, 5, 11, 16, 21, 26, 31, 36, 41, 46, 51, 56), gap_minutes=5)
        self.assertEqual(good["coverage_pct"], 90.0)
        self.assertEqual(good["health"], "Good")
        # one more minute of silence -> Gaps
        gaps = th.compute_health(at(0), at(60), fixes(1, 5, 12, 16, 21, 26, 31, 36, 41, 46, 51, 56), gap_minutes=5)
        self.assertLess(gaps["coverage_pct"], 90.0)
        self.assertEqual(gaps["health"], "Gaps")

    def test_coverage_is_bounded(self):
        h = th.compute_health(at(0), at(60), fixes(-100, 200))
        self.assertGreaterEqual(h["coverage_pct"], 0.0)
        self.assertLessEqual(h["coverage_pct"], 100.0)
        self.assertEqual(h["health"], "None")  # no fix inside the span

    def test_a_span_shorter_than_a_minute_is_fully_covered(self):
        h = th.compute_health(at(0), at(0) + timedelta(seconds=20), fixes(0))
        self.assertEqual(h["coverage_pct"], 100.0)
        self.assertEqual(h["health"], "Good")

    def test_gap_minutes_and_coverage_agree(self):
        h = th.compute_health(at(0), at(100), fixes(5, 10, 60, 65, 70, 75, 80, 85, 90, 95))
        # gaps: 10 -> 60 (50 min)
        self.assertEqual(h["gap_minutes"], 50.0)
        self.assertEqual(h["coverage_pct"], 50.0)

    def test_result_keys_are_the_contract(self):
        self.assertEqual(
            set(th.compute_health(at(0), at(10), fixes(1))),
            {"fix_count", "last_fix_at", "gap_minutes", "coverage_pct", "health"},
        )


class TestPathDistance(unittest.TestCase):
    def test_empty_and_single_point_are_zero(self):
        self.assertEqual(th.path_distance_m([]), 0.0)
        self.assertEqual(th.path_distance_m([point(0, 40.0, -111.0)]), 0.0)

    def test_sums_consecutive_legs(self):
        pts = [point(0, 40.0, -111.0), point(1, 40.001, -111.0), point(2, 40.002, -111.0)]
        self.assertAlmostEqual(th.path_distance_m(pts), 2 * th.haversine_m(40.0, -111.0, 40.001, -111.0), places=6)


class TestDetectStops(unittest.TestCase):
    def test_stationary_run_long_enough_is_a_stop(self):
        pts = [point(m, 40.7600 + 0.00005 * (m % 2), -111.8900) for m in range(0, 30, 5)]
        stops = th.detect_stops(pts, radius_m=40, min_minutes=5)
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]["from"], at(0))
        self.assertEqual(stops[0]["to"], at(25))
        self.assertEqual(stops[0]["minutes"], 25.0)
        self.assertAlmostEqual(stops[0]["lat"], 40.760025, places=5)

    def test_a_short_pause_is_not_a_stop(self):
        pts = [point(0, 40.76, -111.89), point(2, 40.76, -111.89), point(4, 40.76, -111.89)]
        self.assertEqual(th.detect_stops(pts, radius_m=40, min_minutes=5), [])

    def test_driving_produces_no_stop(self):
        pts = [point(m, 40.76 + 0.01 * m, -111.89) for m in range(0, 30, 5)]  # ~1.1 km per fix
        self.assertEqual(th.detect_stops(pts, radius_m=40, min_minutes=5), [])

    def test_a_run_cannot_creep(self):
        # each fix 30 m from the last: individually inside 40 m of its neighbour,
        # but the run's centroid drifts and early members fall outside -> two stops, not one
        pts = [point(m, 40.76 + 0.00027 * (m // 5), -111.89) for m in range(0, 60, 5)]
        stops = th.detect_stops(pts, radius_m=40, min_minutes=5)
        self.assertGreater(len(stops), 1)

    def test_two_sites_two_stops(self):
        site_a = [point(m, 40.76, -111.89) for m in range(0, 20, 5)]
        drive = [point(25, 40.80, -111.89)]
        site_b = [point(m, 40.85, -111.89) for m in range(30, 50, 5)]
        stops = th.detect_stops(site_a + drive + site_b, radius_m=40, min_minutes=5)
        self.assertEqual([(s["from"], s["to"]) for s in stops], [(at(0), at(15)), (at(30), at(45))])

    def test_empty_input(self):
        self.assertEqual(th.detect_stops([]), [])


class TestDwellAndTravel(unittest.TestCase):
    SITE = (40.76, -111.89)

    def test_both_inside_credits_the_pair(self):
        pts = [point(0, 40.76, -111.89), point(10, 40.7601, -111.89), point(20, 40.7601, -111.8901)]
        self.assertEqual(th.dwell_minutes(pts, *self.SITE, radius_m=100), 20.0)

    def test_a_pair_straddling_the_fence_is_not_dwell(self):
        pts = [point(0, 40.76, -111.89), point(10, 40.80, -111.89), point(20, 40.76, -111.89)]
        self.assertEqual(th.dwell_minutes(pts, *self.SITE, radius_m=100), 0.0)

    def test_no_radius_means_no_dwell(self):
        pts = [point(0, 40.76, -111.89), point(10, 40.76, -111.89)]
        self.assertEqual(th.dwell_minutes(pts, *self.SITE, radius_m=0), 0.0)
        self.assertEqual(th.dwell_minutes(pts, None, None, radius_m=100), 0.0)

    def test_travel_is_the_complement_of_dwell_and_stops(self):
        on_site = [point(0, 40.76, -111.89), point(10, 40.76, -111.89)]
        driving = [point(20, 40.80, -111.89), point(30, 40.85, -111.89)]
        lunch = [point(40, 40.85, -111.89), point(50, 40.85, -111.89)]
        pts = on_site + driving + lunch
        stops = th.detect_stops(pts, radius_m=40, min_minutes=5)
        dwell = th.dwell_minutes(pts, *self.SITE, radius_m=100)
        travel = th.travel_minutes(pts, stops, *self.SITE, radius_m=100)
        self.assertEqual(dwell, 10.0)
        # pairs: 0-10 site, 10-20 travel, 20-30 travel, 30-40 stop (40.85 run started at 30), 40-50 stop
        self.assertEqual(travel, 20.0)
        # A stop AT the site is the dwell, not a third thing: the on-site pair is both a
        # 10-minute stop and 10 minutes of dwell. Off-site stops + dwell + travel is the span.
        off_site = [s for s in stops if th.haversine_m(s["lat"], s["lng"], *self.SITE) > 100]
        self.assertEqual([s["minutes"] for s in off_site], [20.0])
        self.assertEqual(dwell + travel + sum(s["minutes"] for s in off_site), 50.0)


class TestAsDatetime(unittest.TestCase):
    def test_parses_frappe_strings(self):
        self.assertEqual(th.as_datetime("2026-09-17 08:00:00"), T0)
        self.assertEqual(th.as_datetime("2026-09-17 08:00:00.000000"), T0)

    def test_passes_datetimes_and_none_through(self):
        self.assertIs(th.as_datetime(T0), T0)
        self.assertIsNone(th.as_datetime(None))


if __name__ == "__main__":
    unittest.main()
