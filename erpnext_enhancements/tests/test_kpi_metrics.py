"""Bench-free unit tests for the pure KPI grading math (kpi_dashboards.metrics).

No frappe/bench required — metrics.py imports nothing from frappe, so the
Good/Watch/Bad status, trend, display formatting, and source-staleness logic are
exercised here as plain unittest (runs in the CI unit-tests job).

Run: python -m unittest erpnext_enhancements.tests.test_kpi_metrics
"""

import copy
import random
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from erpnext_enhancements.kpi_dashboards import metrics
from erpnext_enhancements.kpi_dashboards.metrics import (  # the v1.536.0 reference's names
	STORE_RUN_AMOUNT_TOLERANCE,
	STORE_RUN_PAIR_DAYS,
	STORE_RUN_TAX_ALLOWANCE,
	_amount,
	_as_day,
	_receipt_number,
)


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
	count once (v1.536.0). Before that, only charges existed, and adding recorded receipts to
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
		2486" from the feed, 2026-02-09). The window lets a trip pair with the feed charge when that is
		its only charge; exact-day matching counted 2."""
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
	booked as two unlinked entries, two store runs (v1.536.0 review). Payment Entries are not a
	source at all (``snapshots._store_run_rows``)."""

	def line(self, entry, credit=0.0, debit=0.0, day="2026-09-03", supplier="Bolt & Nut Supply", reference_type=""):
		return {"entry": entry, "supplier": supplier, "day": day, "credit": credit, "debit": debit, "reference_type": reference_type}

	def runs(self, lines):
		return metrics.combine_store_runs(metrics.journal_store_charges(lines), [], "2026-09-01", _key)

	def test_a_bill_and_its_payment_count_once(self):
		"""The shape QuickBooks' Bill/BillPayment imports have at the flagged stores (ACC-JV-2026-25882
		Cr 2110 Bolt & Nut Supply 26.58; ACC-JV-2026-26711 Dr 2110 Bolt & Nut Supply 26.58)."""
		lines = [self.line("JV-BILL", credit=26.58), self.line("JV-PAY", debit=26.58, day="2026-09-05")]
		# The voucher (v1.538.0) is for the Store Run Charge Matching report; the count reads none of it.
		self.assertEqual(
			metrics.journal_store_charges(lines),
			[
				{
					"supplier": "Bolt & Nut Supply",
					"day": "2026-09-03",
					"amount": 26.58,
					"voucher_type": "Journal Entry",
					"voucher_no": "JV-BILL",
				}
			],
		)
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


# ---------------------------------------------------------------------------
# v1.538.0: the pairing moved out of combine_store_runs into pair_store_runs, so the Store Run
# Charge Matching report pairs exactly as the KPI counts. The v1.536.0 body is kept below,
# verbatim, as the reference the refactored function must equal on every input.
# ---------------------------------------------------------------------------


def _combine_store_runs_v1_536_0(charges, receipts, since, store_key, pair_days=STORE_RUN_PAIR_DAYS):
	"""``metrics.combine_store_runs`` as v1.536.0 shipped it. Do not edit: it is the reference."""
	since_day = _as_day(since)
	trips = {}
	for row in receipts or ():
		day = _as_day(row.get("day"))
		if day is None:
			continue
		store = store_key(row.get("supplier") or "")
		number = _receipt_number(row.get("receipt_number"))
		key = ("receipt", store, day, number) if number else ("run", str(row.get("run") or ""))
		trip = trips.setdefault(key, {"key": key, "store": store, "day": day, "net": 0.0, "total": 0.0})
		trip["day"] = min(trip["day"], day)
		trip["net"] += _amount(row.get("amount"))
		trip["total"] = max(trip["total"], _amount(row.get("receipt_total")))
	runs = sorted(trips.values(), key=lambda t: (t["day"], t["store"], str(t["key"])))

	bills = []
	for index, row in enumerate(charges or ()):
		day = _as_day(row.get("day"))
		if day is None:
			continue
		bills.append(
			{
				"index": index,
				"store": store_key(row.get("supplier") or ""),
				"day": day,
				"amount": _amount(row.get("amount")),
				"paired": False,
			}
		)

	tolerance = STORE_RUN_AMOUNT_TOLERANCE

	def equal_to_total(trip, bill):
		return trip["total"] > 0 and abs(bill["amount"] - trip["total"]) <= tolerance

	def lines_plus_tax(trip, bill):
		net = trip["net"]
		return (
			net > 0 and net - tolerance <= bill["amount"] <= net * (1 + STORE_RUN_TAX_ALLOWANCE) + tolerance
		)

	for matches in (equal_to_total, lines_plus_tax):
		for trip in runs:
			if trip.get("bill") is not None:
				continue
			options = [
				bill
				for bill in bills
				if not bill["paired"]
				and bill["store"] == trip["store"]
				and 0 <= (bill["day"] - trip["day"]).days <= pair_days
				and matches(trip, bill)
			]
			if not options:
				continue
			target = trip["total"] or trip["net"]
			best = min(
				options,
				key=lambda bill: (
					(bill["day"] - trip["day"]).days,
					abs(bill["amount"] - target),
					bill["index"],
				),
			)
			best["paired"] = True
			trip["bill"] = best

	count = 0
	spend = 0.0
	for trip in runs:
		if since_day and trip["day"] < since_day:
			continue
		count += 1
		bill = trip.get("bill")
		spend += bill["amount"] if bill else (trip["total"] or trip["net"])
	for bill in bills:
		if bill["paired"] or (since_day and bill["day"] < since_day):
			continue
		count += 1
		spend += bill["amount"]
	return count, round(spend, 2)


class _BothWays:
	"""Mixin: every call a test makes to ``metrics.combine_store_runs`` also runs the v1.536.0 body
	on a deep copy of the same arguments, and the two results must be identical to the last bit
	(``repr`` compares the float exactly, not to a tolerance)."""

	calls = 0

	def setUp(self):
		super().setUp()
		refactored = metrics.combine_store_runs

		def both(*args, **kwargs):
			reference = _combine_store_runs_v1_536_0(*copy.deepcopy(args), **copy.deepcopy(kwargs))
			result = refactored(*args, **kwargs)
			self.assertEqual(repr(result), repr(reference))
			type(self).calls += 1
			return result

		patcher = mock.patch.object(metrics, "combine_store_runs", both)
		patcher.start()
		self.addCleanup(patcher.stop)

	@classmethod
	def tearDownClass(cls):
		super().tearDownClass()
		# A mixin that never intercepted anything would pass by asserting nothing.
		assert cls.calls > 0, f"{cls.__name__} compared no call"


class TestCombineStoreRunsBothWays(_BothWays, TestCombineStoreRuns):
	"""Every TestCombineStoreRuns case again, each call checked against v1.536.0."""


class TestJournalStoreChargesBothWays(_BothWays, TestJournalStoreCharges):
	"""Every TestJournalStoreCharges case again, each count checked against v1.536.0."""


_STORES = ("Home Depot", "Lowes", "Lowe's", "Harbor Freight Tools", "")


def _generated_case(rng):
	"""One random but deterministic store-run history: shared runs and receipt numbers, totals
	present or not, charges equal to a total, inside the lines-plus-tax band, or unrelated, some
	rows with no usable day. Built to reach every branch of the pairing, ties included."""
	start = date(2026, 8, 20)
	receipts = []
	for index in range(rng.randint(0, 9)):
		day = start + timedelta(days=rng.randint(0, 20))
		amount = rng.choice((0, 4.5, 10, 12.34, 20, 99.99, rng.randint(1, 30000) / 100))
		if rng.random() < 0.1:
			shown_day = rng.choice((day, datetime(day.year, day.month, day.day, 23, 59), None, "junk"))
		else:
			shown_day = day.isoformat()
		receipts.append(
			{
				"supplier": rng.choice(_STORES),
				"day": shown_day,
				"run": rng.choice(("sr-a", "sr-b", "sr-c", f"MAT-PRE-{index}", "", None)),
				"amount": amount,
				"receipt_total": rng.choice((0, 0, None, round(amount * 1.0725, 2), round(amount * 1.3, 2), "x")),
				"receipt_number": rng.choice(("", "", "H-0412-88", "h 0412 88", "77", None)),
				"receipt": f"MAT-PRE-{index}",
				"stock_amount": amount,
			}
		)
	charges = []
	for index in range(rng.randint(0, 9)):
		base = rng.choice(receipts) if receipts and rng.random() < 0.7 else None
		base_day = metrics._as_day(base["day"]) if base else None
		day = (base_day or start + timedelta(days=rng.randint(0, 20))) + timedelta(days=rng.randint(-2, 6))
		if base and rng.random() < 0.6:
			amount = rng.choice(
				(
					metrics._amount(base["receipt_total"]),
					round(metrics._amount(base["amount"]) * rng.choice((1.0, 1.06, 1.15, 1.2)), 2),
					metrics._amount(base["amount"]) + rng.choice((-0.05, 0.04, 0.06)),
					# Either side of the receipt total by the same amount: a tie only the index breaks.
					round(metrics._amount(base["receipt_total"]) + rng.choice((-0.02, 0.02)), 2),
				)
			)
		else:
			amount = rng.choice((5.0, 10.0, 16.6, 480.0, rng.randint(1, 30000) / 100))
		charges.append(
			{
				"supplier": base["supplier"] if base and rng.random() < 0.8 else rng.choice(_STORES),
				"day": None if rng.random() < 0.05 else rng.choice((day, day.isoformat())),
				"amount": (rng.choice((amount, str(amount))) if rng.random() < 0.9 else None),
				"voucher_type": "Journal Entry",
				"voucher_no": f"JV-{index}",
			}
		)
	return charges, receipts


class TestPairStoreRuns(unittest.TestCase):
	"""``metrics.pair_store_runs``: the trips and charges ``combine_store_runs`` counts, with the
	pairing kept so the Store Run Charge Matching report can list it (v1.538.0)."""

	def test_generated_histories_count_exactly_as_v1_536_0(self):
		rng = random.Random(15380)
		for case in range(1500):
			charges, receipts = _generated_case(rng)
			for since in ("2026-08-20", "2026-08-27", "2026-09-05", date(2026, 9, 12), None):
				for pair_days in (0, 3, 5):
					with self.subTest(case=case, since=since, pair_days=pair_days):
						self.assertEqual(
							repr(metrics.combine_store_runs(charges, receipts, since, _key, pair_days)),
							repr(
								_combine_store_runs_v1_536_0(
									copy.deepcopy(charges), copy.deepcopy(receipts), since, _key, pair_days
								)
							),
						)

	def test_generated_pairings_keep_the_rules(self):
		"""Whatever the input: one charge per trip and one trip per charge, same store, never
		before the trip and at most pair_days after, on an amount the named pass allows."""
		rng = random.Random(538)
		tolerance = metrics.STORE_RUN_AMOUNT_TOLERANCE
		for case in range(1500):
			charges, receipts = _generated_case(rng)
			trips, bills = metrics.pair_store_runs(charges, receipts, _key)
			taken = [trip["charge"]["index"] for trip in trips if trip["charge"]]
			self.assertEqual(len(taken), len(set(taken)), case)
			self.assertEqual(sorted(taken), sorted(bill["index"] for bill in bills if bill["paired"]), case)
			dated = sum(1 for row in receipts if metrics._as_day(row["day"]))
			self.assertEqual(sum(len(trip["receipts"]) for trip in trips), dated, case)
			for trip in trips:
				charge = trip["charge"]
				if charge is None:
					self.assertIsNone(trip["basis"], case)
					continue
				self.assertIs(charge["row"], charges[charge["index"]])
				self.assertEqual(charge["store"], trip["store"], case)
				self.assertTrue(0 <= (charge["day"] - trip["day"]).days <= metrics.STORE_RUN_PAIR_DAYS, case)
				if trip["basis"] == metrics.PAIRED_ON_RECEIPT_TOTAL:
					self.assertLessEqual(abs(charge["amount"] - trip["total"]), tolerance, case)
				else:
					self.assertEqual(trip["basis"], metrics.PAIRED_ON_LINES_PLUS_TAX, case)
					self.assertLessEqual(trip["net"] - tolerance, charge["amount"], case)
					ceiling = trip["net"] * (1 + metrics.STORE_RUN_TAX_ALLOWANCE) + tolerance
					self.assertLessEqual(charge["amount"], ceiling, case)

	def test_the_basis_names_the_pass(self):
		receipts = [
			{"supplier": "Home Depot", "day": "2026-09-03", "run": "sr-a", "amount": 10.0, "receipt_total": 10.78},
			{"supplier": "Home Depot", "day": "2026-09-03", "run": "sr-b", "amount": 20.0, "receipt_total": 99.0},
		]
		charges = [
			{"supplier": "Home Depot", "day": "2026-09-04", "amount": 21.6, "voucher_no": "JV-2"},
			{"supplier": "Home Depot", "day": "2026-09-03", "amount": 10.78, "voucher_no": "JV-1"},
		]
		trips, bills = metrics.pair_store_runs(charges, receipts, _key)
		by_run = {trip["key"][1]: trip for trip in trips}
		self.assertEqual(by_run["sr-a"]["basis"], metrics.PAIRED_ON_RECEIPT_TOTAL)
		self.assertEqual(by_run["sr-a"]["charge"]["row"]["voucher_no"], "JV-1")
		# sr-b's total was mistyped: the card charge is its lines plus tax.
		self.assertEqual(by_run["sr-b"]["basis"], metrics.PAIRED_ON_LINES_PLUS_TAX)
		self.assertEqual(by_run["sr-b"]["charge"]["row"]["voucher_no"], "JV-2")
		self.assertEqual([bill["paired"] for bill in bills], [True, True])

	def test_receipts_ride_along_unchanged_and_nothing_is_mutated(self):
		receipts = [
			{"supplier": "Lowe's", "day": "2026-09-03", "run": "sr-a", "amount": 4, "receipt": "PR-1", "stock_amount": 4},
			{"supplier": "Lowes", "day": "2026-09-03", "run": "sr-a", "amount": 6, "receipt": "PR-2", "stock_amount": 0},
		]
		charges = [{"supplier": "Lowes", "day": "2026-09-05", "amount": 10.7, "voucher_no": "JV-1", "docstatus": 0}]
		before = copy.deepcopy((charges, receipts))
		trips, bills = metrics.pair_store_runs(charges, receipts, _key)
		self.assertEqual((charges, receipts), before)
		self.assertEqual(len(trips), 1)
		self.assertEqual([row["receipt"] for row in trips[0]["receipts"]], ["PR-1", "PR-2"])
		self.assertIs(trips[0]["receipts"][0], receipts[0])
		self.assertEqual((trips[0]["net"], trips[0]["total"]), (10.0, 0.0))
		self.assertIs(trips[0]["charge"], bills[0])
		self.assertEqual(bills[0]["row"]["docstatus"], 0)

	def test_a_tie_goes_to_the_earlier_charge(self):
		"""Same day, the same distance from the receipt total: the first charge in the input wins.
		The report names that charge, so the tie-break is pinned, not only the count."""
		receipts = [{"supplier": "Home Depot", "day": "2026-09-03", "run": "sr-a", "amount": 9.0, "receipt_total": 10.0}]
		charges = [
			{"supplier": "Home Depot", "day": "2026-09-03", "amount": 10.02, "voucher_no": "JV-1"},
			{"supplier": "Home Depot", "day": "2026-09-03", "amount": 9.98, "voucher_no": "JV-2"},
		]
		trips, _bills = metrics.pair_store_runs(charges, receipts, _key)
		self.assertEqual(trips[0]["charge"]["row"]["voucher_no"], "JV-1")
		self.assertEqual(metrics.combine_store_runs(charges, receipts, "2026-09-01", _key), (2, 20.0))
		trips, _bills = metrics.pair_store_runs(charges[::-1], receipts, _key)
		self.assertEqual(trips[0]["charge"]["row"]["voucher_no"], "JV-2")

	def test_a_row_with_no_day_is_left_out_but_keeps_the_index(self):
		charges = [
			{"supplier": "Home Depot", "day": None, "amount": 5},
			{"supplier": "Home Depot", "day": "2026-09-03", "amount": 5},
		]
		_trips, bills = metrics.pair_store_runs(charges, [], _key)
		self.assertEqual([bill["index"] for bill in bills], [1])

	def test_extra_keys_change_nothing(self):
		"""The identifying columns snapshots._store_run_rows adds for the report (v1.538.0) are
		never read by the count: the same history with and without them counts the same."""
		rng = random.Random(1538)
		extra_charge = {"voucher_type": "Journal Entry", "voucher_no": "ACC-JV-1", "docstatus": 0, "source": "QuickBooks"}
		extra_receipt = {
			"receipt": "MAT-PRE-1",
			"company": "Sapphire Fountains",
			"recorded_by": "someone@example.com",
			"net_amount": 12345.0,
			"is_stock_item": 1,
			"stock_amount": 999.0,
		}
		receipt_keys = ("supplier", "day", "run", "amount", "receipt_total", "receipt_number")
		for case in range(300):
			charges, receipts = _generated_case(rng)
			bare_charges = [{key: row[key] for key in ("supplier", "day", "amount")} for row in charges]
			bare_receipts = [{key: row[key] for key in receipt_keys} for row in receipts]
			with self.subTest(case=case):
				self.assertEqual(
					repr(metrics.combine_store_runs(bare_charges, bare_receipts, "2026-09-01", _key)),
					repr(
						metrics.combine_store_runs(
							[dict(row, **extra_charge) for row in bare_charges],
							[dict(row, **extra_receipt) for row in bare_receipts],
							"2026-09-01",
							_key,
						)
					),
				)

	def test_the_lookback_constant_has_one_home(self):
		self.assertEqual(metrics.STORE_RUN_LOOKBACK_DAYS, 7)
		self.assertGreater(metrics.STORE_RUN_LOOKBACK_DAYS, metrics.STORE_RUN_PAIR_DAYS)


if __name__ == "__main__":
	unittest.main()
