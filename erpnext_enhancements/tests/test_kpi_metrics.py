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

	def test_years_unit_renders_via_fallback(self):
		# HR tenure KPIs use the free-text "years" unit
		self.assertEqual(metrics.fmt_value(3.25, "years"), "3.25 years")

	def test_non_numeric_passthrough(self):
		self.assertEqual(metrics.fmt_value(None, "USD"), "")


class TestTurnoverRate(unittest.TestCase):
	def test_one_exit_on_fourteen(self):
		# 1 separation, headcount 14 -> 13: avg 13.5 -> ~7.41% (the small-n swing
		# that justifies the 365-day window)
		self.assertAlmostEqual(metrics.turnover_rate_pct(1, 14, 13), 7.4074, places=3)

	def test_zero_headcount_returns_none(self):
		self.assertIsNone(metrics.turnover_rate_pct(0, 0, 0))

	def test_zero_separations_is_zero_not_none(self):
		self.assertEqual(metrics.turnover_rate_pct(0, 14, 14), 0.0)

	def test_non_numeric_returns_none(self):
		self.assertIsNone(metrics.turnover_rate_pct("x", 14, 14))
		self.assertIsNone(metrics.turnover_rate_pct(1, None, 14))


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


def _key(name):
	"""``stock_scan_rules.store_key``, restated so this suite imports nothing but ``metrics``."""
	return "".join(ch for ch in (name or "").lower() if ch.isalnum())


class TestCombineStoreRuns(unittest.TestCase):
	"""``metrics.combine_store_runs``: a trip recorded on the Stock Scan page and its card charge
	count once (v1.535.0). Before that, only charges existed, and adding recorded receipts to
	them would have counted most trips twice."""

	SINCE = "2026-09-01"

	def charge(self, day, amount=20.0, supplier="Home Depot"):
		return {"supplier": supplier, "day": day, "amount": amount}

	def receipt(self, day, run, amount=10.0, supplier="Home Depot", total=0.0, number=""):
		return {"supplier": supplier, "day": day, "run": run, "amount": amount, "receipt_total": total, "receipt_number": number}

	def combine(self, charges=(), receipts=()):
		return metrics.combine_store_runs(list(charges), list(receipts), self.SINCE, _key)

	def test_empty(self):
		self.assertEqual(self.combine(), (0, 0.0))

	def test_charges_only_count_as_before(self):
		"""With nothing recorded the figure is exactly the old one: one run per charge."""
		charges = [self.charge("2026-09-02", 10), self.charge("2026-09-05", 15.5, "Lowes"), self.charge("2026-09-09", 4.5)]
		self.assertEqual(self.combine(charges), (3, 30.0))

	def test_one_trip_of_three_receipts_is_one_run(self):
		receipts = [self.receipt("2026-09-03", "sr-a", 4), self.receipt("2026-09-03", "sr-a", 6), self.receipt("2026-09-03", "sr-a", 5)]
		self.assertEqual(self.combine(receipts=receipts), (1, 15.0))

	def test_a_trip_and_its_charge_the_same_day(self):
		receipts = [self.receipt("2026-09-03", "sr-a", 10, total=10.78)]
		self.assertEqual(self.combine([self.charge("2026-09-03", 10.78)], receipts), (1, 10.78))

	def test_the_charge_two_days_later_is_the_same_trip(self):
		"""A bank-feed entry carries the bank's posting date: Lowes $16.60 on the Capital One card
		is ACC-JV-2026-27340 (receipt email, 2026-02-07) and ACC-JV-2026-27137 ("LOWES #02662* -
		2486" from the feed, 2026-02-09). Exact-day matching counted 2."""
		receipts = [self.receipt("2026-09-07", "sr-a", 15.4, supplier="Lowes", total=16.6)]
		self.assertEqual(self.combine([self.charge("2026-09-09", 16.6, "Lowes")], receipts), (1, 16.6))
		self.assertEqual(self.combine([self.charge("2026-09-10", 16.6, "Lowes")], receipts), (1, 16.6))

	def test_a_charge_four_days_later_is_another_trip(self):
		"""Known limit: a feed entry posted more than three days late counts as a second trip."""
		receipts = [self.receipt("2026-09-07", "sr-a", 15.4, supplier="Lowes", total=16.6)]
		self.assertEqual(self.combine([self.charge("2026-09-11", 16.6, "Lowes")], receipts), (2, 33.2))

	def test_a_charge_before_the_trip_is_another_trip(self):
		"""A charge never comes before the purchase."""
		receipts = [self.receipt("2026-09-04", "sr-a", 10.0, total=10.78)]
		self.assertEqual(self.combine([self.charge("2026-09-03", 10.78)], receipts), (2, 21.56))

	def test_two_charges_and_one_run(self):
		receipts = [self.receipt("2026-09-03", "sr-a", 10.0, total=10.78)]
		charges = [self.charge("2026-09-03", 55.0), self.charge("2026-09-03", 10.78)]
		self.assertEqual(self.combine(charges, receipts), (2, 65.78))

	def test_one_charge_and_two_runs(self):
		"""The charge goes to the run whose receipt total it equals; the other run is a trip whose
		charge has not arrived, counted at its own receipt total."""
		receipts = [self.receipt("2026-09-03", "sr-a", 10.0, total=10.78), self.receipt("2026-09-03", "sr-b", 30.0, total=32.34)]
		self.assertEqual(self.combine([self.charge("2026-09-03", 32.34)], receipts), (2, 43.12))

	def test_an_amount_match_beats_a_nearer_day(self):
		receipts = [self.receipt("2026-09-03", "sr-a", 40.0, total=43.12)]
		charges = [self.charge("2026-09-03", 7.0), self.charge("2026-09-05", 43.12)]
		# sr-a takes the $43.12 two days later; the $7 the same day is its own trip.
		self.assertEqual(self.combine(charges, receipts), (2, 50.12))

	def test_a_trip_never_takes_another_amount(self):
		"""The review's case: a recorded trip whose own charge is missing (cash, a personal card, a
		Bill, not synced yet) must not swallow the next trip's charge at the same store. A $20
		trip with a $21.50 receipt and an unrelated $480 charge two days later are two runs."""
		receipts = [self.receipt("2026-09-10", "sr-a", 20.0, total=21.5)]
		self.assertEqual(self.combine([self.charge("2026-09-12", 480.0)], receipts), (2, 501.5))
		# Without a total the lines-plus-tax band is the only other way in; $480 is far outside it.
		bare = [self.receipt("2026-09-10", "sr-a", 20.0)]
		self.assertEqual(self.combine([self.charge("2026-09-12", 480.0)], bare), (2, 500.0))

	def test_lines_plus_tax_match_without_a_total(self):
		receipts = [self.receipt("2026-09-03", "PR-0001", 20.0)]
		charges = [self.charge("2026-09-04", 90.0), self.charge("2026-09-04", 21.6)]
		self.assertEqual(self.combine(charges, receipts), (2, 111.6))

	def test_two_stores_the_same_day(self):
		receipts = [self.receipt("2026-09-03", "sr-a", 10.0), self.receipt("2026-09-03", "sr-b", 12.0, supplier="Lowe's")]
		self.assertEqual(self.combine(receipts=receipts), (2, 22.0))

	def test_lowes_and_lowe_s_are_one_store(self):
		receipts = [self.receipt("2026-09-03", "sr-a", 10.0, supplier="Lowe's", total=10.78)]
		self.assertEqual(self.combine([self.charge("2026-09-03", 10.78, "Lowes")], receipts), (1, 10.78))

	def test_receipts_with_no_run_id_are_one_trip_each(self):
		"""A Desk receipt carries no run id: the SQL gives it its own name as the run."""
		receipts = [self.receipt("2026-09-03", "MAT-PRE-1", 5.0), self.receipt("2026-09-03", "MAT-PRE-2", 6.0)]
		self.assertEqual(self.combine(receipts=receipts), (2, 11.0))

	def test_two_runs_with_one_receipt_number_are_one_trip(self):
		"""Two people who shopped together and each started a run: one paper receipt."""
		receipts = [
			self.receipt("2026-09-03", "sr-a", 10.0, total=30.0, number="H-0412-88"),
			self.receipt("2026-09-03", "sr-b", 18.0, total=30.0, number="h 0412 88"),
		]
		self.assertEqual(self.combine(receipts=receipts), (1, 30.0))
		self.assertEqual(self.combine([self.charge("2026-09-04", 30.0)], receipts), (1, 30.0))

	def test_the_window_edge(self):
		"""A trip on ``since`` counts; one the day before does not -- and still claims its own
		charge inside the window, so that charge does not count as a trip of its own."""
		self.assertEqual(self.combine(receipts=[self.receipt("2026-09-01", "sr-a", 9.0)]), (1, 9.0))
		self.assertEqual(self.combine(receipts=[self.receipt("2026-08-31", "sr-a", 9.0)]), (0, 0.0))
		early = [self.receipt("2026-08-31", "sr-a", 9.0, total=9.7)]
		self.assertEqual(self.combine([self.charge("2026-09-01", 9.7)], early), (0, 0.0))
		self.assertEqual(self.combine([self.charge("2026-08-31", 9.7)]), (0, 0.0))

	def test_two_identical_charges_the_same_day_count_twice(self):
		"""Pinned, not ideal: QuickBooks can hold one purchase twice (a receipt entry and a bank
		feed entry). Without a recorded trip to pair with, there is no telling a duplicate from a
		second purchase, and the KPI errs on counting."""
		charges = [self.charge("2026-09-03", 12.0), self.charge("2026-09-03", 12.0)]
		self.assertEqual(self.combine(charges), (2, 24.0))

	def test_accepts_dates_and_strings(self):
		from datetime import date

		receipts = [{"supplier": "Home Depot", "day": date(2026, 9, 3), "run": "sr-a", "amount": 5, "receipt_total": None}]
		charges = [{"supplier": "Home Depot", "day": datetime(2026, 9, 3, 0, 0), "amount": "5.40"}]
		self.assertEqual(metrics.combine_store_runs(charges, receipts, date(2026, 9, 1), _key), (1, 5.4))


class TestJournalStoreCharges(unittest.TestCase):
	"""``metrics.journal_store_charges``: from a Journal Entry only a credit to the store's
	payable is a purchase. A debit is a payment, and counting it too made a bill and its payment,
	booked as two unlinked entries, two store runs (v1.535.0 review). Payment Entries are not a
	source at all (``snapshots._store_run_rows``)."""

	def line(self, entry, credit=0.0, debit=0.0, day="2026-09-03", supplier="Bolt & Nut Supply", reference_type=""):
		return {"entry": entry, "supplier": supplier, "day": day, "credit": credit, "debit": debit, "reference_type": reference_type}

	def runs(self, lines):
		return metrics.combine_store_runs(metrics.journal_store_charges(lines), [], "2026-09-01", _key)

	def test_a_bill_and_its_payment_count_once(self):
		"""The shape QuickBooks' Bill/BillPayment imports have at the flagged stores (ACC-JV-2026-25882
		Cr 2110 Bolt & Nut Supply 26.58; ACC-JV-2026-26711 Dr 2110 Bolt & Nut Supply 26.58)."""
		lines = [self.line("JV-BILL", credit=26.58), self.line("JV-PAY", debit=26.58, day="2026-09-05")]
		self.assertEqual(metrics.journal_store_charges(lines), [{"supplier": "Bolt & Nut Supply", "day": "2026-09-03", "amount": 26.58}])
		self.assertEqual(self.runs(lines), (1, 26.58))

	def test_an_entry_that_only_debits_the_store_is_no_charge(self):
		lines = [self.line("JV-PAY", debit=26.58)]
		self.assertEqual(metrics.journal_store_charges(lines), [])
		self.assertEqual(self.runs(lines), (0, 0.0))

	def test_a_pass_through_entry_counts_its_credit_once(self):
		"""Billed and paid in one entry: the store credited and debited the same amount."""
		lines = [self.line("JV-1", credit=12.0), self.line("JV-1", debit=12.0)]
		self.assertEqual(self.runs(lines), (1, 12.0))

	def test_a_referenced_credit_is_not_a_purchase(self):
		"""A credit against an invoice or another entry settles or reverses what was booked."""
		self.assertEqual(metrics.journal_store_charges([self.line("JV-1", credit=9.0, reference_type="Purchase Invoice")]), [])

	def test_one_charge_per_entry_and_store(self):
		lines = [
			self.line("JV-1", credit=4.0),
			self.line("JV-1", credit=6.0),
			self.line("JV-1", credit=3.0, supplier="Home Depot"),
			self.line("JV-2", credit=5.0),
		]
		self.assertEqual(
			[(c["supplier"], c["amount"]) for c in metrics.journal_store_charges(lines)],
			[("Bolt & Nut Supply", 10.0), ("Home Depot", 3.0), ("Bolt & Nut Supply", 5.0)],
		)

	def test_garbage_is_nothing(self):
		self.assertEqual(metrics.journal_store_charges(None), [])
		self.assertEqual(metrics.journal_store_charges([self.line("JV-1", credit="x")]), [])


if __name__ == "__main__":
	unittest.main()
