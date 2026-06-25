"""Bench-free unit tests for the pure KPI grading math (kpi_dashboards.metrics).

No frappe/bench required — metrics.py imports nothing from frappe, so the
Good/Watch/Bad status, trend, display formatting, and source-staleness logic are
exercised here as plain unittest (runs in the CI unit-tests job).

Run: python -m unittest erpnext_enhancements.tests.test_kpi_metrics
"""

import unittest
from datetime import datetime

from erpnext_enhancements.kpi_dashboards import metrics


class TestComputeStatus(unittest.TestCase):
	def test_no_target_returns_blank(self):
		self.assertEqual(metrics.compute_status(50, None), "")

	def test_non_numeric_returns_blank(self):
		self.assertEqual(metrics.compute_status("n/a", 10), "")

	def test_higher_is_better(self):
		# at/above target -> Good; within the 10% band -> Watch; below -> Bad
		self.assertEqual(metrics.compute_status(100, 100, metrics.HIGHER), "Good")
		self.assertEqual(metrics.compute_status(120, 100, metrics.HIGHER), "Good")
		self.assertEqual(metrics.compute_status(95, 100, metrics.HIGHER), "Watch")
		self.assertEqual(metrics.compute_status(80, 100, metrics.HIGHER), "Bad")

	def test_lower_is_better(self):
		# at/below target -> Good; within band over -> Watch; well over -> Bad
		self.assertEqual(metrics.compute_status(100, 100, metrics.LOWER), "Good")
		self.assertEqual(metrics.compute_status(80, 100, metrics.LOWER), "Good")
		self.assertEqual(metrics.compute_status(105, 100, metrics.LOWER), "Watch")
		self.assertEqual(metrics.compute_status(130, 100, metrics.LOWER), "Bad")

	def test_zero_target_lower_is_better(self):
		# e.g. target of 0 failed syncs: exactly zero good, anything above bad
		self.assertEqual(metrics.compute_status(0, 0, metrics.LOWER), "Good")
		self.assertEqual(metrics.compute_status(3, 0, metrics.LOWER), "Bad")

	def test_zero_target_higher_is_better_is_meaningless(self):
		self.assertEqual(metrics.compute_status(5, 0, metrics.HIGHER), "")


class TestTrend(unittest.TestCase):
	def test_normal(self):
		self.assertAlmostEqual(metrics.compute_trend_pct(120, 100), 20.0)
		self.assertAlmostEqual(metrics.compute_trend_pct(80, 100), -20.0)

	def test_prior_none_or_zero(self):
		self.assertIsNone(metrics.compute_trend_pct(100, None))
		self.assertIsNone(metrics.compute_trend_pct(100, 0))

	def test_non_numeric(self):
		self.assertIsNone(metrics.compute_trend_pct("x", 100))


class TestFormat(unittest.TestCase):
	def test_usd(self):
		self.assertEqual(metrics.fmt_value(12400.0, "USD"), "$12,400")

	def test_percent(self):
		self.assertEqual(metrics.fmt_value(38.25, "%"), "38.2%")

	def test_days(self):
		self.assertEqual(metrics.fmt_value(12.0, "days"), "12.0 d")

	def test_count_integer_and_float(self):
		self.assertEqual(metrics.fmt_value(7.0, "count"), "7")
		self.assertEqual(metrics.fmt_value(7.5, "count"), "7.5")

	def test_blank_unit_is_count_like(self):
		self.assertEqual(metrics.fmt_value(1234, ""), "1,234")

	def test_unknown_unit(self):
		self.assertEqual(metrics.fmt_value(3.5, "ratio"), "3.50 ratio")

	def test_non_numeric_passthrough(self):
		self.assertEqual(metrics.fmt_value(None, "USD"), "")


class TestSourceStale(unittest.TestCase):
	NOW = datetime(2026, 6, 25, 12, 0, 0)

	def test_missing_is_stale(self):
		self.assertTrue(metrics.is_source_stale(None, now=self.NOW))

	def test_recent_not_stale(self):
		recent = datetime(2026, 6, 25, 9, 0, 0)  # 3h old, threshold 6h
		self.assertFalse(metrics.is_source_stale(recent, max_age_hours=6, now=self.NOW))

	def test_old_is_stale(self):
		old = datetime(2026, 6, 24, 12, 0, 0)  # 24h old
		self.assertTrue(metrics.is_source_stale(old, max_age_hours=6, now=self.NOW))

	def test_accepts_string_datetime(self):
		self.assertFalse(
			metrics.is_source_stale("2026-06-25 09:00:00", max_age_hours=6, now=self.NOW)
		)
		self.assertTrue(
			metrics.is_source_stale("2026-06-20 09:00:00", max_age_hours=6, now=self.NOW)
		)


if __name__ == "__main__":
	unittest.main()
