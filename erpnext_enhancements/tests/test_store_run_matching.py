"""Bench-free tests for the Store Run Charge Matching report (v1.538.0).

Accounting's list of recorded store runs beside the card charge each pairs with, used at the
QuickBooks cutover (runbook step S-D) to move a matching draft's goods debit to 2210 before it
is submitted. Every rule is in ``kpi_dashboards/store_run_matching.py``, which imports no frappe;
the report's own ``.py`` only reads, and is checked here from its source.

Nothing here imports frappe: the rules are plain functions, and the report files are read with
``ast`` and ``json``. The pairing itself is ``metrics.pair_store_runs``, pinned against the KPI's
v1.536.0 count in ``test_kpi_metrics``.

Run: python -m unittest erpnext_enhancements.tests.test_store_run_matching
"""

import ast
import json
import random
import re
import unittest
from datetime import date, timedelta
from pathlib import Path

from erpnext_enhancements.kpi_dashboards import metrics
from erpnext_enhancements.kpi_dashboards import store_run_matching as matching

APP = Path(__file__).resolve().parent.parent
MODULE = APP / "kpi_dashboards" / "store_run_matching.py"
SNAPSHOTS = APP / "kpi_dashboards" / "snapshots.py"
REPORT_DIR = APP / "kpi_dashboards" / "report" / "store_run_charge_matching"
REPORT_PY = REPORT_DIR / "store_run_charge_matching.py"
REPORT_JS = REPORT_DIR / "store_run_charge_matching.js"
REPORT_JSON = REPORT_DIR / "store_run_charge_matching.json"

ACCOUNT = "2210 - Stock Received But Not Billed - SF"
ACCOUNTS = {"Sapphire Fountains": ACCOUNT}


def _key(name):
	"""``stock_scan_rules.store_key``, restated so this suite imports nothing but the module."""
	return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def receipt(
	day,
	run="sr-a",
	amount=45.0,
	stock=None,
	total=0.0,
	number="",
	name=None,
	supplier="Home Depot",
	owner="tech@example.com",
):
	return {
		"supplier": supplier,
		"day": day,
		"run": run,
		"amount": amount,
		"receipt_total": total,
		"receipt_number": number,
		"receipt": name or f"MAT-PRE-{run}-{day}",
		"company": "Sapphire Fountains",
		"recorded_by": owner,
		"net_amount": amount,
		"is_stock_item": 1 if (amount if stock is None else stock) else 0,
		"stock_amount": amount if stock is None else stock,
	}


def charge(
	day,
	amount,
	voucher_no="ACC-JV-1",
	docstatus=0,
	source="QuickBooks",
	voucher_type="Journal Entry",
	supplier="Home Depot",
):
	return {
		"supplier": supplier,
		"day": day,
		"amount": amount,
		"voucher_type": voucher_type,
		"voucher_no": voucher_no,
		"docstatus": docstatus,
		"source": source,
	}


def rows_for(
	charges, receipts, on_2210=None, billed=None, from_date="2026-09-01", to_date="2026-09-30", **kwargs
):
	trips = matching.trips_in_window(charges, receipts, from_date, to_date, _key, **kwargs)
	return matching.build_rows(trips, on_2210=on_2210, billed=billed, accounts=ACCOUNTS)


def listed(
	charges,
	receipts,
	on_2210=None,
	corrections=None,
	billed=None,
	from_date="2026-09-01",
	to_date="2026-09-30",
	**kwargs,
):
	"""Every row the report shows, as its ``execute`` builds them: the trips, and the charges that
	paired with none (listed when they carry 2210, or taken by a trip when found by hand)."""
	trips, unpaired = matching.pair_window(charges, receipts, from_date, to_date, _key, **kwargs)
	return matching.build_rows(
		trips, on_2210=on_2210, billed=billed, accounts=ACCOUNTS, corrections=corrections, unpaired=unpaired
	)


# ---------------------------------------------------------------------------
# 1. The window: the KPI's pairing, trips near either edge included
# ---------------------------------------------------------------------------


class TestTripsInWindow(unittest.TestCase):
	def test_a_trip_just_before_from_claims_its_charge_and_is_not_listed(self):
		"""The KPI reads rows 7 days early for exactly this: the Aug 31 trip takes the Sep 1
		charge, so the Sep 1 trip of the same amount is still waiting for its own."""
		receipts = [
			receipt("2026-08-31", "sr-early", 10.0, total=10.78),
			receipt("2026-09-01", "sr-b", 10.0, total=10.78),
		]
		trips = matching.trips_in_window(
			[charge("2026-09-01", 10.78)], receipts, "2026-09-01", "2026-09-30", _key
		)
		self.assertEqual([trip["key"] for trip in trips], [("run", "sr-b")])
		self.assertIsNone(trips[0]["charge"])

	def test_a_trip_on_the_to_date_pairs_with_a_charge_three_days_later(self):
		receipts = [receipt("2026-09-30", total=48.24)]
		trips = matching.trips_in_window(
			[charge("2026-10-03", 48.24)], receipts, "2026-09-01", "2026-09-30", _key
		)
		self.assertEqual(trips[0]["charge"]["row"]["voucher_no"], "ACC-JV-1")
		trips = matching.trips_in_window(
			[charge("2026-10-04", 48.24)], receipts, "2026-09-01", "2026-09-30", _key
		)
		self.assertIsNone(trips[0]["charge"])

	def test_a_trip_after_the_to_date_is_not_listed(self):
		receipts = [receipt("2026-10-01", total=48.24)]
		self.assertEqual(
			matching.trips_in_window(
				[charge("2026-10-01", 48.24)], receipts, "2026-09-01", "2026-09-30", _key
			),
			[],
		)

	def test_rows_before_the_lookback_play_no_part(self):
		"""A receipt eight days before From is not read, as in the KPI -- even where reading it
		would reach a trip in range through a chain of same-amount trips: read, sr-old takes the
		Aug 26 charge, each later trip slides to the next one, and the Sep 1 trip is left waiting."""
		receipts = [
			receipt("2026-08-24", "sr-old", 10.0, total=10.78),
			receipt("2026-08-26", "sr-a", 10.0, total=10.78),
			receipt("2026-08-29", "sr-b", 10.0, total=10.78),
			receipt("2026-09-01", "sr-c", 10.0, total=10.78),
		]
		charges = [
			charge("2026-08-26", 10.78, "JV-1"),
			charge("2026-08-29", 10.78, "JV-2"),
			charge("2026-09-01", 10.78, "JV-3"),
		]
		trips = matching.trips_in_window(charges, receipts, "2026-09-01", "2026-09-30", _key)
		self.assertEqual(trips[0]["key"], ("run", "sr-c"))
		self.assertEqual(trips[0]["charge"]["row"]["voucher_no"], "JV-3")
		unbounded, _bills = metrics.pair_store_runs(charges, receipts, _key)
		self.assertIsNone(unbounded[-1]["charge"], "the chain this test guards against")

	def test_a_run_with_a_receipt_after_the_window_is_paired_whole(self):
		"""The page keeps a run to one day; the Desk does not. A receipt dated after To Date + 3 that
		carries the run id still belongs to the trip that started in range, and cutting it off would
		change the trip's lines and so the charge it pairs with (the generated-history test below
		found this when receipts were cut at the same date as charges)."""
		receipts = [receipt("2026-09-30", "sr-a", 20.0), receipt("2026-10-06", "sr-a", 20.0)]
		# $42.90 is 40.00 of lines plus tax, and far more than 20.00 of lines plus tax.
		trips = matching.trips_in_window(
			[charge("2026-10-02", 42.9)], receipts, "2026-09-01", "2026-09-30", _key
		)
		self.assertEqual(trips[0]["net"], 40.0)
		self.assertEqual(trips[0]["basis"], metrics.PAIRED_ON_LINES_PLUS_TAX)

	def test_a_store_filter_keeps_both_spellings_of_one_store(self):
		receipts = [
			receipt("2026-09-03", "sr-a", supplier="Lowe's"),
			receipt("2026-09-04", "sr-b", supplier="Lowes"),
			receipt("2026-09-05", "sr-c", supplier="Home Depot"),
		]
		trips = matching.trips_in_window([], receipts, "2026-09-01", "2026-09-30", _key, store="Lowes")
		self.assertEqual([trip["key"][1] for trip in trips], ["sr-a", "sr-b"])

	def test_a_store_filter_does_not_change_the_pairing(self):
		"""The filter is applied after the pairing, never to the rows: a Lowe's charge still
		pairs with the Lowes trip."""
		receipts = [receipt("2026-09-03", supplier="Lowes", total=20.0)]
		charges = [charge("2026-09-03", 20.0, supplier="Lowe's")]
		trips = matching.trips_in_window(charges, receipts, "2026-09-01", "2026-09-30", _key, store="Lowe's")
		self.assertIsNotNone(trips[0]["charge"])

	def test_from_after_to_lists_nothing(self):
		self.assertEqual(
			matching.trips_in_window([], [receipt("2026-09-03")], "2026-09-30", "2026-09-01", _key), []
		)

	def test_generated_histories_pair_as_the_kpi_counts(self):
		"""The report reads up to 3 days after To; the KPI reads to today. Every trip in range must
		take the same charge on the same basis either way (the module docstring's argument)."""
		rng = random.Random(20261021)
		stores = ("Home Depot", "Lowes", "Lowe's")
		start = date(2026, 8, 1)
		for case in range(800):
			receipts = []
			for index in range(rng.randint(0, 14)):
				day = start + timedelta(days=rng.randint(0, 60))
				amount = rng.choice((10.0, 20.0, 45.0, rng.randint(100, 9000) / 100))
				receipts.append(
					receipt(
						day.isoformat(),
						rng.choice(("sr-a", "sr-b", f"PR-{index}")),
						amount,
						total=rng.choice((0.0, round(amount * 1.0725, 2), round(amount * 1.4, 2))),
						number=rng.choice(("", "", "R-1")),
						supplier=rng.choice(stores),
					)
				)
			charges = []
			for index in range(rng.randint(0, 14)):
				base = rng.choice(receipts) if receipts and rng.random() < 0.8 else None
				day = date.fromisoformat(base["day"]) if base else start + timedelta(days=rng.randint(0, 60))
				day += timedelta(days=rng.randint(-1, 5))
				amount = (
					base["receipt_total"] or round(base["amount"] * 1.07, 2)
					if base
					else rng.choice((10.0, 45.0))
				)
				charges.append(
					charge(
						day.isoformat(),
						amount,
						f"JV-{index}",
						supplier=base["supplier"] if base else rng.choice(stores),
					)
				)
			from_day = start + timedelta(days=rng.randint(5, 40))
			to_day = from_day + timedelta(days=rng.randint(0, 20))
			early = from_day - timedelta(days=metrics.STORE_RUN_LOOKBACK_DAYS)
			# The KPI's read: everything from the lookback on, no upper bound.
			kpi_trips, kpi_bills = metrics.pair_store_runs(
				[row for row in charges if date.fromisoformat(row["day"]) >= early],
				[row for row in receipts if date.fromisoformat(row["day"]) >= early],
				_key,
			)
			expected = {
				trip["key"]: (trip["charge"]["row"]["voucher_no"] if trip["charge"] else None, trip["basis"])
				for trip in kpi_trips
				if from_day <= trip["day"] <= to_day
			}
			listed, unpaired = matching.pair_window(charges, receipts, from_day, to_day, _key)
			got = {
				trip["key"]: (trip["charge"]["row"]["voucher_no"] if trip["charge"] else None, trip["basis"])
				for trip in listed
			}
			self.assertEqual(got, expected, f"case {case}, {from_day}..{to_day}")
			# And the charges in range that paired with no trip are the KPI's too: the ones the
			# report may list as carrying 2210 with no store run.
			self.assertEqual(
				sorted(bill["row"]["voucher_no"] for bill in unpaired),
				sorted(
					bill["row"]["voucher_no"]
					for bill in kpi_bills
					if not bill["paired"] and from_day <= bill["day"] <= to_day
				),
				f"case {case}, {from_day}..{to_day}",
			)


# ---------------------------------------------------------------------------
# 2. One row per trip, and what to do with it
# ---------------------------------------------------------------------------


class TestWhatToDo(unittest.TestCase):
	def one(self, charges, receipts, **kwargs):
		rows = rows_for(charges, receipts, **kwargs)
		self.assertEqual(len(rows), 1)
		return rows[0]

	def test_a_matching_draft_moves_its_stock_lines_to_2210(self):
		row = self.one([charge("2026-09-05", 48.26)], [receipt("2026-09-03", amount=45.0, total=48.26)])
		self.assertEqual(
			row["action"],
			f"Move $45.00 of the goods debit to {ACCOUNT}, then save it; the S-D loop submits it",
		)
		self.assertEqual(row["row_type"], matching.ROW_TRIP)
		self.assertEqual(row["show"], matching.NEEDS_ACTION)
		self.assertEqual(
			(row["charge"], row["charge_type"], row["charge_status"]), ("ACC-JV-1", "Journal Entry", "Draft")
		)
		self.assertEqual(row["charge_source"], "QuickBooks draft Journal Entry")
		self.assertEqual(row["match_basis"], "Receipt total")
		self.assertEqual((row["moved_to_2210"], row["to_move"]), (0, 45.0))

	def test_only_the_stock_lines_move(self):
		"""A non-stock line posted nothing to 2210, so it stays on the expense with the tax."""
		receipts = [
			receipt("2026-09-03", amount=30.0, stock=30.0, total=53.63, name="PR-1"),
			receipt("2026-09-03", amount=20.0, stock=0.0, total=53.63, name="PR-2"),
		]
		row = self.one([charge("2026-09-04", 53.63)], receipts)
		self.assertEqual((row["lines_before_tax"], row["stock_amount"], row["receipts"]), (50.0, 30.0, 2))
		self.assertIn("Move $30.00 of the goods debit", row["action"])

	def test_a_partly_moved_draft_says_how_much_more(self):
		row = self.one(
			[charge("2026-09-03", 48.26)],
			[receipt("2026-09-03", total=48.26)],
			on_2210={("Journal Entry", "ACC-JV-1"): 20.0},
		)
		self.assertEqual(
			row["action"],
			f"Move $25.00 more of the goods debit to {ACCOUNT} ($20.00 is there already), then save it; "
			"the S-D loop submits it",
		)
		self.assertEqual(
			(row["moved_to_2210"], row["to_move"], row["show"]), (0, 25.0, matching.NEEDS_ACTION)
		)

	def test_a_draft_carrying_exactly_the_stock_lines_is_done(self):
		for on in (45.0, 45.004, 44.996):
			with self.subTest(on_2210=on):
				row = self.one(
					[charge("2026-09-03", 48.26)],
					[receipt("2026-09-03", total=48.26)],
					on_2210={("Journal Entry", "ACC-JV-1"): on},
				)
				self.assertEqual(
					row["action"], f"Goods debit on {ACCOUNT}: nothing to change; the S-D loop submits it"
				)
				self.assertEqual((row["moved_to_2210"], row["show"], row["to_move"]), (1, matching.DONE, 0.0))

	def test_a_cent_short_is_not_moved(self):
		"""2210 must clear to the cent; a near miss leaves a residue nobody will find later."""
		row = self.one(
			[charge("2026-09-03", 48.26)],
			[receipt("2026-09-03", total=48.26)],
			on_2210={("Journal Entry", "ACC-JV-1"): 44.99},
		)
		self.assertEqual(
			(row["moved_to_2210"], row["show"], row["to_move"]), (0, matching.NEEDS_ACTION, 0.01)
		)

	def test_too_much_on_2210_is_flagged(self):
		row = self.one(
			[charge("2026-09-03", 48.26)],
			[receipt("2026-09-03", total=48.26)],
			on_2210={("Journal Entry", "ACC-JV-1"): 48.26},
		)
		self.assertEqual(
			row["action"],
			f"The {ACCOUNT} debit is $48.26 but the stock lines are $45.00: reduce it to $45.00, then save it; "
			"the S-D loop submits it",
		)
		self.assertEqual((row["moved_to_2210"], row["show"]), (0, matching.NEEDS_ACTION))

	def test_no_stock_lines_nothing_to_move(self):
		row = self.one(
			[charge("2026-09-03", 21.45)], [receipt("2026-09-03", amount=20.0, stock=0.0, total=21.45)]
		)
		self.assertEqual(row["action"], "No stock lines: nothing to move; the S-D loop submits it")
		self.assertEqual((row["moved_to_2210"], row["show"], row["stock_amount"]), (0, matching.DONE, 0.0))

	def test_a_submitted_charge_without_the_move_books_the_goods_twice(self):
		"""One correcting Journal Entry naming the charge, never an amendment: amending a QuickBooks
		charge detaches it from its sync mapping, and so from this list (v1.538.0 review)."""
		row = self.one([charge("2026-09-03", 48.26, docstatus=1)], [receipt("2026-09-03", total=48.26)])
		self.assertEqual(
			row["action"],
			"Submitted with the goods on the expense: post a correcting Journal Entry for $45.00 "
			f"(Dr {ACCOUNT} / Cr the expense account the charge used) with Reference Number ACC-JV-1",
		)
		self.assertEqual(
			(row["charge_status"], row["charge_source"], row["show"], row["to_move"]),
			("Submitted", "QuickBooks Journal Entry", matching.NEEDS_ACTION, 45.0),
		)

	def test_a_submitted_charge_with_the_move_is_done(self):
		row = self.one(
			[charge("2026-09-03", 48.26, docstatus=1)],
			[receipt("2026-09-03", total=48.26)],
			on_2210={("Journal Entry", "ACC-JV-1"): 45.0},
		)
		self.assertEqual((row["action"], row["show"], row["moved_to_2210"]), ("Done", matching.DONE, 1))

	def test_a_standalone_purchase_invoice_is_read_from_its_items(self):
		"""ERPNext books a stock line of an invoice with no receipt to 2210 itself."""
		pi = charge(
			"2026-09-03", 48.26, "ACC-PINV-1", docstatus=1, source="ERPNext", voucher_type="Purchase Invoice"
		)
		row = self.one(
			[pi], [receipt("2026-09-03", total=48.26)], on_2210={("Purchase Invoice", "ACC-PINV-1"): 45.0}
		)
		self.assertEqual(
			(row["charge_source"], row["charge_type"], row["action"]),
			("Purchase Invoice", "Purchase Invoice", "Done"),
		)

	def test_a_journal_entry_crediting_the_store(self):
		je = charge("2026-09-03", 48.26, "ACC-JV-9", docstatus=1, source="ERPNext")
		row = self.one([je], [receipt("2026-09-03", total=48.26)])
		self.assertEqual(row["charge_source"], "Journal Entry")
		self.assertEqual(row["show"], matching.NEEDS_ACTION)

	def test_no_charge_yet_is_waiting(self):
		"""True before the cutover and after it: after it no card charge arrives from QuickBooks, so
		"waiting for the card charge" would be wrong advice (v1.538.0 review)."""
		row = self.one([], [receipt("2026-09-03", total=48.26)])
		self.assertEqual(
			(row["action"], row["show"], row["match_basis"]),
			(
				"No card charge paired yet: before the cutover, find its QuickBooks draft and move $45.00 of "
				f"its goods debit to {ACCOUNT}; after the cutover, bill it from the receipts",
				matching.WAITING,
				"No charge yet",
			),
		)
		self.assertEqual(
			(row["charge"], row["charge_type"], row["charge_status"], row["charge_source"]),
			(None, None, "", ""),
		)

	def test_a_waiting_trip_with_no_stock_lines(self):
		row = self.one([], [receipt("2026-09-03", amount=20.0, stock=0.0, total=21.45)])
		self.assertEqual(
			row["action"],
			"No card charge paired yet: no stock lines, so its QuickBooks draft needs no change before the "
			"cutover; after the cutover, bill it from the receipts",
		)
		self.assertEqual(row["show"], matching.WAITING)

	def test_a_charge_of_another_amount_leaves_it_waiting(self):
		row = self.one([charge("2026-09-04", 480.0)], [receipt("2026-09-03", total=48.26)])
		self.assertEqual(row["show"], matching.WAITING)

	def test_billed_from_the_receipts_is_done(self):
		receipts = [
			receipt("2026-09-03", name="PR-1", total=90.0),
			receipt("2026-09-03", name="PR-2", total=90.0),
		]
		row = self.one([], receipts, billed={"PR-1": "ACC-PINV-7", "PR-2": "ACC-PINV-7"})
		self.assertEqual(
			(row["action"], row["show"], row["match_basis"]),
			("Billed from the receipts (ACC-PINV-7)", matching.DONE, "Billed from the receipts"),
		)

	def test_billed_from_some_receipts_bills_the_rest(self):
		receipts = [
			receipt("2026-09-03", name="PR-1", total=90.0),
			receipt("2026-09-03", name="PR-2", total=90.0),
		]
		row = self.one([], receipts, billed={"PR-1": "ACC-PINV-7"})
		self.assertEqual(
			(row["action"], row["show"]),
			("Billed from 1 of 2 receipts (ACC-PINV-7): bill the rest", matching.NEEDS_ACTION),
		)

	def test_billed_and_matched_is_two_records_of_one_purchase(self):
		row = self.one(
			[charge("2026-09-03", 48.26)],
			[receipt("2026-09-03", name="PR-1", total=48.26)],
			billed={"PR-1": "ACC-PINV-7"},
		)
		self.assertTrue(
			row["action"].startswith("Billed from the receipts (ACC-PINV-7) and matched to this charge too")
		)
		self.assertEqual((row["show"], row["to_move"]), (matching.NEEDS_ACTION, 0.0))

	def test_lines_plus_tax_basis(self):
		row = self.one([charge("2026-09-03", 48.15)], [receipt("2026-09-03", total=0.0)])
		self.assertEqual(row["match_basis"], "Lines plus tax")
		self.assertIsNone(row["receipt_total"], "no total was entered: blank, not $0.00")

	def test_the_account_is_the_receipt_company_s(self):
		trips = matching.trips_in_window(
			[charge("2026-09-03", 48.26)],
			[receipt("2026-09-03", total=48.26)],
			"2026-09-01",
			"2026-09-30",
			_key,
		)
		row = matching.build_rows(trips, accounts={"Sapphire Fountains": "2210 - SRBNB - X"})[0]
		self.assertEqual(
			row["action"],
			"Move $45.00 of the goods debit to 2210 - SRBNB - X, then save it; the S-D loop submits it",
		)
		row = matching.build_rows(trips)[0]
		self.assertIn(matching.DEFAULT_2210, row["action"])

	def test_the_identifying_columns(self):
		receipts = [
			receipt("2026-09-03", "sr-b", 10.0, total=30.0, number="H-0412-88", name="PR-9", owner="b@x"),
			receipt("2026-09-03", "sr-a", 18.0, total=30.0, number="h 0412 88", name="PR-3", owner="a@x"),
			receipt("2026-09-03", "sr-a", 2.0, total=30.0, number="H-0412-88", name="PR-4", owner="a@x"),
		]
		trips = matching.trips_in_window([], receipts, "2026-09-01", "2026-09-30", _key)
		row = matching.build_rows(trips, accounts=ACCOUNTS, name_of=lambda user: user.upper())[0]
		self.assertEqual(row["receipts"], 3)
		self.assertEqual(row["first_receipt"], "PR-3")
		self.assertEqual(row["run_ref"], "sr-a, sr-b; receipt h 0412 88")
		self.assertEqual(row["recorded_by"], "A@X, B@X")
		self.assertEqual(
			(row["lines_before_tax"], row["receipt_total"], row["trip_day"]), (30.0, 30.0, date(2026, 9, 3))
		)
		self.assertEqual(row["store"], "Home Depot")

	def test_rows_sort_by_what_needs_doing(self):
		receipts = [
			receipt("2026-09-02", "sr-done", 20.0, stock=0.0, total=21.45),
			receipt("2026-09-03", "sr-wait", total=99.0),
			receipt("2026-09-04", "sr-act", total=48.26),
		]
		charges = [charge("2026-09-02", 21.45, "JV-1"), charge("2026-09-04", 48.26, "JV-2")]
		rows = rows_for(charges, receipts)
		self.assertEqual(
			[row["show"] for row in rows], [matching.NEEDS_ACTION, matching.WAITING, matching.DONE]
		)


# ---------------------------------------------------------------------------
# 2b. Correcting entries, charges with no store run, charges found by hand (v1.538.0 review)
# ---------------------------------------------------------------------------

JV1 = ("Journal Entry", "ACC-JV-1")
JV7 = ("Journal Entry", "ACC-JV-7")


def _trip():
	"""One recorded trip at Home Depot on Sep 3: $45.00 of stock lines, $48.26 receipt total."""
	return [receipt("2026-09-03", total=48.26)]


class TestCorrectingEntries(unittest.TestCase):
	"""A charge already submitted with the goods on the expense is fixed by ONE correcting Journal
	Entry whose Reference Number is the charge; its 2210 debit is added to the charge's own, so
	the row reaches Done once corrected."""

	def one(self, corrections, on_2210=None, docstatus=1):
		rows = listed(
			[charge("2026-09-03", 48.26, docstatus=docstatus)],
			_trip(),
			on_2210=on_2210,
			corrections=corrections,
		)
		self.assertEqual(len(rows), 1)
		return rows[0]

	def test_a_submitted_charge_plus_its_correcting_entry_is_done(self):
		row = self.one({"ACC-JV-1": {"ACC-JV-900": 45.0}})
		self.assertEqual((row["action"], row["show"]), ("Done (corrected by ACC-JV-900)", matching.DONE))
		self.assertEqual((row["moved_to_2210"], row["on_2210"], row["to_move"]), (1, 45.0, 0.0))

	def test_a_partial_correction_shows_the_remainder(self):
		row = self.one({"ACC-JV-1": {"ACC-JV-900": 20.0}})
		self.assertEqual(
			row["action"],
			"Submitted with $25.00 of the goods still on the expense ($20.00 is on "
			f"{ACCOUNT} already, corrected by ACC-JV-900): post a correcting Journal Entry for $25.00 "
			f"(Dr {ACCOUNT} / Cr the expense account the charge used) with Reference Number ACC-JV-1",
		)
		self.assertEqual(
			(row["show"], row["to_move"], row["moved_to_2210"]), (matching.NEEDS_ACTION, 25.0, 0)
		)

	def test_an_over_correction_is_flagged(self):
		row = self.one({"ACC-JV-1": {"ACC-JV-900": 50.0}})
		self.assertEqual(
			row["action"],
			f"The {ACCOUNT} debit is $50.00 but the stock lines are $45.00 (corrected by ACC-JV-900): post a "
			f"correcting Journal Entry for $5.00 (Dr the expense account the charge used / Cr {ACCOUNT}) with "
			"Reference Number ACC-JV-1",
		)
		self.assertEqual((row["show"], row["moved_to_2210"], row["to_move"]), (matching.NEEDS_ACTION, 0, 0.0))

	def test_the_reversing_entry_it_asks_for_brings_it_to_done(self):
		row = self.one({"ACC-JV-1": {"ACC-JV-900": 50.0, "ACC-JV-901": -5.0}})
		self.assertEqual(
			(row["action"], row["show"]), ("Done (corrected by ACC-JV-900, ACC-JV-901)", matching.DONE)
		)

	def test_a_correction_adds_to_the_charge_s_own_lines(self):
		row = self.one({"ACC-JV-1": {"ACC-JV-900": 25.0}}, on_2210={JV1: 20.0})
		self.assertEqual((row["show"], row["on_2210"], row["moved_to_2210"]), (matching.DONE, 45.0, 1))

	def test_another_charge_s_correction_does_not_count(self):
		row = self.one({"ACC-JV-2": {"ACC-JV-900": 45.0}})
		self.assertEqual((row["show"], row["on_2210"], row["to_move"]), (matching.NEEDS_ACTION, 0.0, 45.0))

	def test_a_draft_counts_a_correcting_entry_too(self):
		row = self.one({"ACC-JV-1": {"ACC-JV-900": 45.0}}, docstatus=0)
		self.assertEqual(
			row["action"],
			f"Goods debit on {ACCOUNT} (corrected by ACC-JV-900): nothing to change; the S-D loop submits it",
		)
		self.assertEqual(row["show"], matching.DONE)

	def test_no_advice_is_to_amend(self):
		"""Amending a QuickBooks-synced Journal Entry cancels the original, and tabQuickBooks Sync
		Mapping stays on it; the pairing follows the mapping, so the charge would drop out of it."""
		for docstatus in (0, 1):
			for on in (0.0, 20.0, 45.0, 50.0, -5.0):
				with self.subTest(docstatus=docstatus, on_2210=on):
					self.assertNotIn(
						"amend", self.one({}, on_2210={JV1: on}, docstatus=docstatus)["action"].lower()
					)
					rows = listed(
						[charge("2026-09-05", 1.0, "ACC-JV-7", docstatus=docstatus)], [], on_2210={JV7: on}
					)
					for row in rows:
						self.assertNotIn("amend", row["action"].lower())


class TestChargesWithNoStoreRun(unittest.TestCase):
	"""A charge dated in range that paired with no trip but carries 2210 -- a draft adjusted for a
	trip whose pairing then changed -- is listed under Needs action, never silently dropped."""

	def test_a_draft_adjusted_for_a_trip_since_cancelled_is_listed(self):
		rows = listed([charge("2026-09-05", 48.26, "ACC-JV-7")], [], on_2210={JV7: 45.0})
		self.assertEqual(len(rows), 1)
		row = rows[0]
		self.assertEqual(
			row["action"],
			f"Carries $45.00 on {ACCOUNT} but matches no recorded store run: move it back to the expense, "
			"then save it; the S-D loop submits it",
		)
		self.assertEqual(
			(row["show"], row["row_type"], row["match_basis"]),
			(matching.NEEDS_ACTION, matching.ROW_CHARGE, matching.NO_STORE_RUN),
		)
		self.assertEqual(
			(row["charge"], row["charge_type"], row["charge_status"], row["charge_source"]),
			("ACC-JV-7", "Journal Entry", "Draft", "QuickBooks draft Journal Entry"),
		)
		self.assertEqual(
			(row["trip_day"], row["receipts"], row["charge_date"], row["on_2210"], row["to_move"]),
			(None, 0, date(2026, 9, 5), 45.0, 0.0),
		)

	def test_a_charge_with_nothing_on_2210_is_not_listed(self):
		charges = [charge("2026-09-05", 48.26, "ACC-JV-7")]
		self.assertEqual(listed(charges, []), [])
		self.assertEqual(listed(charges, [], on_2210={JV7: 0.004}), [])

	def test_a_submitted_one_is_moved_back_with_a_correcting_entry(self):
		rows = listed([charge("2026-09-05", 48.26, "ACC-JV-7", docstatus=1)], [], on_2210={JV7: 45.0})
		self.assertEqual(
			rows[0]["action"],
			f"Carries $45.00 on {ACCOUNT} but matches no recorded store run: move it back to the expense; "
			f"post a correcting Journal Entry for $45.00 (Dr the expense account it used / Cr {ACCOUNT}) "
			"with Reference Number ACC-JV-7",
		)
		self.assertEqual(rows[0]["charge_status"], "Submitted")

	def test_its_correcting_entries_count(self):
		charges = [charge("2026-09-05", 48.26, "ACC-JV-7", docstatus=1)]
		cleared = listed(charges, [], on_2210={JV7: 45.0}, corrections={"ACC-JV-7": {"ACC-JV-900": -45.0}})
		self.assertEqual(cleared, [])
		part = listed(charges, [], on_2210={JV7: 45.0}, corrections={"ACC-JV-7": {"ACC-JV-900": -20.0}})
		self.assertTrue(
			part[0]["action"].startswith(
				f"Carries $25.00 on {ACCOUNT} (corrected by ACC-JV-900) but matches no recorded store run"
			),
			part[0]["action"],
		)

	def test_one_reversed_too_far_is_brought_back_to_zero(self):
		rows = listed(
			[charge("2026-09-05", 48.26, "ACC-JV-7", docstatus=1)],
			[],
			corrections={"ACC-JV-7": {"ACC-JV-900": -5.0}},
		)
		self.assertEqual(
			rows[0]["action"],
			f"Takes $5.00 off {ACCOUNT} (corrected by ACC-JV-900) but matches no recorded store run: bring "
			f"it back to $0.00; post a correcting Journal Entry for $5.00 (Dr {ACCOUNT} / Cr the expense "
			"account it used) with Reference Number ACC-JV-7",
		)
		self.assertEqual(matching.summarize(rows)["unmatched_on_2210"], -5.0)

	def test_only_charges_dated_in_range_are_listed(self):
		"""One in the lookback may belong to a trip before From Date; one after To Date to a trip
		after it. Neither is this range's to judge."""
		names = ("JV-early", "JV-in", "JV-late")
		charges = [
			charge("2026-08-28", 48.26, "JV-early"),
			charge("2026-09-15", 48.26, "JV-in"),
			charge("2026-10-02", 48.26, "JV-late"),
		]
		rows = listed(charges, [], on_2210={("Journal Entry", name): 45.0 for name in names})
		self.assertEqual([row["charge"] for row in rows], ["JV-in"])

	def test_the_store_filter_applies_to_them(self):
		charges = [charge("2026-09-05", 48.26, "ACC-JV-7", supplier="Lowe's")]
		self.assertEqual(listed(charges, [], on_2210={JV7: 45.0}, store="Home Depot"), [])
		self.assertEqual(
			[row["charge"] for row in listed(charges, [], on_2210={JV7: 45.0}, store="Lowes")], ["ACC-JV-7"]
		)

	def test_a_charge_row_names_its_company_s_account(self):
		row = dict(charge("2026-09-05", 48.26, "ACC-JV-7"), company="Other Co")
		trips, unpaired = matching.pair_window([row], [], "2026-09-01", "2026-09-30", _key)
		rows = matching.build_rows(
			trips, on_2210={JV7: 45.0}, accounts={"Other Co": "2210 - SRBNB - OC"}, unpaired=unpaired
		)
		self.assertIn("on 2210 - SRBNB - OC but", rows[0]["action"])

	def test_they_sort_among_needs_action_by_charge_date(self):
		receipts = [receipt("2026-09-06", "sr-act", total=48.26)]
		charges = [
			charge("2026-09-06", 48.26, "JV-trip"),
			charge("2026-09-10", 30.0, "JV-late"),
			charge("2026-09-02", 30.0, "JV-early"),
		]
		rows = listed(
			charges,
			receipts,
			on_2210={("Journal Entry", "JV-late"): 5.0, ("Journal Entry", "JV-early"): 5.0},
		)
		self.assertEqual([row["charge"] for row in rows], ["JV-early", "JV-trip", "JV-late"])

	def test_vouchers_names_every_charge_the_report_reads(self):
		charges = [
			charge("2026-09-03", 48.26, "JV-1"),
			charge("2026-09-10", 20.0, "JV-2"),
			charge(
				"2026-09-11", 21.0, "PINV-1", docstatus=1, source="ERPNext", voucher_type="Purchase Invoice"
			),
		]
		trips, unpaired = matching.pair_window(charges, _trip(), "2026-09-01", "2026-09-30", _key)
		self.assertEqual(
			matching.vouchers(trips, unpaired),
			{"Journal Entry": ["JV-1", "JV-2"], "Purchase Invoice": ["PINV-1"]},
		)
		self.assertEqual(matching.vouchers([], []), {"Journal Entry": [], "Purchase Invoice": []})


class TestFoundByHand(unittest.TestCase):
	"""A trip whose charge never pairs -- a bank-feed date more than 3 days late, an amount outside
	the tolerance -- is found by hand at step S-D, and the draft given the trip's stock lines on
	2210. That debit is the link; without it the adjusted draft would be listed to be moved back."""

	def late(self, **kwargs):
		"""The trip's card charge, seven days late: the pairing never takes it."""
		return [charge("2026-09-10", 48.26, "ACC-JV-7", **kwargs)]

	def test_before_it_is_adjusted_the_trip_waits_and_the_charge_is_not_listed(self):
		rows = listed(self.late(), _trip())
		self.assertEqual(
			[(row["row_type"], row["show"]) for row in rows], [(matching.ROW_TRIP, matching.WAITING)]
		)

	def test_once_adjusted_it_is_the_trip_s_charge(self):
		rows = listed(self.late(), _trip(), on_2210={JV7: 45.0})
		self.assertEqual(len(rows), 1)
		row = rows[0]
		self.assertEqual(
			(row["charge"], row["match_basis"], row["show"], row["moved_to_2210"]),
			("ACC-JV-7", matching.MATCHED_BY_HAND, matching.DONE, 1),
		)
		self.assertEqual(
			row["action"], f"Goods debit on {ACCOUNT}: nothing to change; the S-D loop submits it"
		)
		self.assertEqual(matching.summarize(rows)["matched"], 1)

	def test_a_cent_off_is_not_a_match(self):
		rows = listed(self.late(), _trip(), on_2210={JV7: 44.99})
		self.assertEqual(
			[(row["row_type"], row["show"]) for row in rows],
			[(matching.ROW_CHARGE, matching.NEEDS_ACTION), (matching.ROW_TRIP, matching.WAITING)],
		)

	def test_only_at_the_same_store(self):
		rows = listed(self.late(supplier="Lowes"), _trip(), on_2210={JV7: 45.0})
		self.assertEqual(
			sorted(row["row_type"] for row in rows), sorted([matching.ROW_CHARGE, matching.ROW_TRIP])
		)

	def test_the_nearest_dated_charge_is_taken(self):
		charges = [charge("2026-09-20", 48.26, "JV-far"), charge("2026-09-09", 48.26, "JV-near")]
		on = {("Journal Entry", "JV-far"): 45.0, ("Journal Entry", "JV-near"): 45.0}
		rows = {row["row_type"]: row for row in listed(charges, _trip(), on_2210=on)}
		self.assertEqual(rows[matching.ROW_TRIP]["charge"], "JV-near")
		self.assertEqual(rows[matching.ROW_CHARGE]["charge"], "JV-far")

	def test_one_charge_serves_one_trip_the_earlier_first(self):
		receipts = [receipt("2026-09-03", "sr-a", total=48.26), receipt("2026-09-04", "sr-b", total=48.26)]
		rows = listed([charge("2026-09-12", 48.26, "ACC-JV-7")], receipts, on_2210={JV7: 45.0})
		self.assertEqual(
			sorted((row["run_ref"], row["charge"] or "") for row in rows),
			[("sr-a", "ACC-JV-7"), ("sr-b", "")],
		)

	def test_a_submitted_one_with_its_correcting_entry(self):
		rows = listed(self.late(docstatus=1), _trip(), corrections={"ACC-JV-7": {"ACC-JV-900": 45.0}})
		self.assertEqual(len(rows), 1)
		self.assertEqual(
			(rows[0]["action"], rows[0]["match_basis"]),
			("Done (corrected by ACC-JV-900)", matching.MATCHED_BY_HAND),
		)

	def test_a_trip_billed_from_its_receipts_is_never_matched_by_hand(self):
		rows = listed(
			self.late(),
			[receipt("2026-09-03", total=48.26, name="PR-1")],
			on_2210={JV7: 45.0},
			billed={"PR-1": "ACC-PINV-7"},
		)
		shown = {row["row_type"]: row["show"] for row in rows}
		self.assertEqual(
			shown, {matching.ROW_TRIP: matching.DONE, matching.ROW_CHARGE: matching.NEEDS_ACTION}
		)

	def test_a_charge_that_paired_is_never_taken_by_hand(self):
		receipts = [receipt("2026-09-03", "sr-a", total=48.26), receipt("2026-09-20", "sr-b", total=99.0)]
		rows = listed(
			[charge("2026-09-04", 48.26, "JV-1")], receipts, on_2210={("Journal Entry", "JV-1"): 45.0}
		)
		by_run = {row["run_ref"]: row for row in rows}
		self.assertEqual((by_run["sr-a"]["charge"], by_run["sr-b"]["charge"]), ("JV-1", None))


# ---------------------------------------------------------------------------
# 3. Show and the summary
# ---------------------------------------------------------------------------


class TestShowAndSummary(unittest.TestCase):
	def setUp(self):
		receipts = [
			receipt("2026-09-02", "sr-done", 20.0, stock=0.0, total=21.45),
			receipt("2026-09-03", "sr-wait", total=99.0),
			receipt("2026-09-04", "sr-act", total=48.26),
			receipt("2026-09-05", "sr-moved", 30.0, total=32.15),
		]
		charges = [
			charge("2026-09-02", 21.45, "JV-1"),
			charge("2026-09-04", 48.26, "JV-2"),
			charge("2026-09-06", 32.15, "JV-3"),
		]
		self.rows = rows_for(
			charges, receipts, on_2210={("Journal Entry", "JV-2"): 5.0, ("Journal Entry", "JV-3"): 30.0}
		)

	def test_each_bucket(self):
		self.assertEqual(
			[r["run_ref"] for r in matching.filter_rows(self.rows, matching.NEEDS_ACTION)], ["sr-act"]
		)
		self.assertEqual(
			[r["run_ref"] for r in matching.filter_rows(self.rows, matching.WAITING)], ["sr-wait"]
		)
		self.assertEqual(
			[r["run_ref"] for r in matching.filter_rows(self.rows, matching.DONE)], ["sr-done", "sr-moved"]
		)

	def test_all_blank_or_unknown_keeps_every_row(self):
		for show in (matching.SHOW_ALL, "", None, "Something else"):
			with self.subTest(show=show):
				self.assertEqual(len(matching.filter_rows(self.rows, show)), 4)

	def test_the_summary(self):
		self.assertEqual(
			matching.summarize(self.rows),
			{
				"trips": 4,
				"matched": 3,
				"needs_action": 1,
				"waiting": 1,
				"to_move": 40.0,
				"moved": 35.0,
				"unmatched_on_2210": 0.0,
			},
		)

	def test_an_empty_range(self):
		self.assertEqual(
			matching.summarize([]),
			{
				"trips": 0,
				"matched": 0,
				"needs_action": 0,
				"waiting": 0,
				"to_move": 0.0,
				"moved": 0.0,
				"unmatched_on_2210": 0.0,
			},
		)

	def test_a_charge_with_no_store_run_is_counted_apart(self):
		"""It needs action, but it is not a store run, not matched, and its 2210 debit is not
		"already moved": it is what must come back off 2210."""
		receipts = [receipt("2026-09-04", "sr-act", total=48.26)]
		charges = [charge("2026-09-04", 48.26, "JV-2"), charge("2026-09-10", 30.0, "JV-9")]
		trips, unpaired = matching.pair_window(charges, receipts, "2026-09-01", "2026-09-30", _key)
		rows = matching.build_rows(
			trips,
			on_2210={("Journal Entry", "JV-2"): 5.0, ("Journal Entry", "JV-9"): 12.5},
			accounts=ACCOUNTS,
			unpaired=unpaired,
		)
		self.assertEqual(
			matching.summarize(rows),
			{
				"trips": 1,
				"matched": 1,
				"needs_action": 2,
				"waiting": 0,
				"to_move": 40.0,
				"moved": 5.0,
				"unmatched_on_2210": 12.5,
			},
		)


# ---------------------------------------------------------------------------
# 4. The report's files: placement, reads only, and the JS agreeing with the Python
# ---------------------------------------------------------------------------


def _without_docstrings(tree):
	"""The module with every docstring removed: the prose explaining what the report does not do
	names the very calls an absence check looks for."""
	tree = ast.parse(ast.unparse(tree))
	for node in ast.walk(tree):
		if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
			first = node.body[0]
			if (
				isinstance(first, ast.Expr)
				and isinstance(first.value, ast.Constant)
				and isinstance(first.value.value, str)
			):
				node.body = node.body[1:] or [ast.Pass()]
	return tree


def _strings(tree):
	return [
		node.value
		for node in ast.walk(tree)
		if isinstance(node, ast.Constant) and isinstance(node.value, str)
	]


class TestReportFiles(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.record = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
		cls.source = REPORT_PY.read_text(encoding="utf-8")
		cls.tree = ast.parse(cls.source)
		cls.code = ast.unparse(_without_docstrings(cls.tree))
		cls.js = REPORT_JS.read_text(encoding="utf-8")

	def test_the_record(self):
		self.assertEqual(self.record["name"], "Store Run Charge Matching")
		self.assertEqual(self.record["report_name"], self.record["name"])
		self.assertEqual(self.record["report_type"], "Script Report")
		self.assertEqual(self.record["module"], "KPI Dashboards")
		self.assertEqual(self.record["ref_doctype"], "Purchase Receipt")
		self.assertEqual(self.record["is_standard"], "Yes")
		self.assertEqual(
			{row["role"] for row in self.record["roles"]},
			{"Accounts Manager", "Accounts User", "Purchase Manager", "System Manager"},
		)

	def test_it_sits_where_frappe_computes_it(self):
		"""``<app>/<scrub(module)>/report/<scrub(name)>/<scrub(name)>.py`` (test_report_modules
		checks every report; this one is named so a move cannot drop it from that sweep)."""
		self.assertEqual(REPORT_DIR.parent.parent.name, "kpi_dashboards")
		self.assertTrue((REPORT_DIR / "__init__.py").exists())
		self.assertTrue((REPORT_DIR.parent / "__init__.py").exists())
		functions = {node.name for node in self.tree.body if isinstance(node, ast.FunctionDef)}
		self.assertIn("execute", functions)

	def test_it_reads_through_the_kpi_and_the_pure_rules(self):
		code = self.code
		for needle in (
			"snapshots._store_run_suppliers()",
			"snapshots._store_run_rows(suppliers, from_date)",
			"matching.pair_window(",
			"_on_2210(matching.vouchers(trips, unpaired), accounts)",
			"matching.build_rows(",
			"corrections=corrections",
			"unpaired=unpaired",
			"matching.filter_rows(",
			"matching.summarize(",
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, code)
		self.assertNotIn("pair_store_runs", code, "the pairing is reached through pair_window only")

	def test_it_counts_submitted_correcting_entries_by_reference_number(self):
		"""The one fix advised for a charge already submitted is a Journal Entry whose Reference
		Number (cheque_no) is the charge: only submitted ones count, only their 2210 lines, and a
		charge is never its own correction."""
		queries = [
			" ".join(text.split()) for text in _strings(_without_docstrings(self.tree)) if "cheque_no" in text
		]
		self.assertEqual(len(queries), 1)
		query = queries[0]
		for needle in (
			"from `tabJournal Entry` je",
			"je.docstatus = 1",
			"trim(je.cheque_no) in %(names)s",
			"je.name not in %(names)s",
			"jea.account in %(accounts)s",
			"coalesce(sum(jea.debit), 0) - coalesce(sum(jea.credit), 0) as amount",
			"group by je.name, trim(je.cheque_no)",
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, query)
		self.assertIn("canonical.get(str(row.reference or '').strip().lower())", self.code)

	def test_it_writes_nothing(self):
		code = self.code
		for needle in (
			".insert(",
			".save(",
			".submit(",
			".cancel(",
			".delete(",
			"set_value(",
			"delete_doc(",
			"db_set(",
			"commit(",
			"enqueue(",
			"frappe.whitelist",
			"ignore_permissions",
		):
			with self.subTest(needle=needle):
				self.assertNotIn(needle, code)
		for text in _strings(_without_docstrings(self.tree)):
			self.assertIsNone(
				re.search(r"\b(insert|update|delete|replace)\s+(into|from)?\s*`", text, re.I), text
			)

	def test_every_query_is_a_select_with_bound_params(self):
		calls = [
			node
			for node in ast.walk(self.tree)
			if isinstance(node, ast.Call) and ast.unparse(node.func) == "frappe.db.sql"
		]
		# A Journal Entry's own 2210 lines, a Purchase Invoice's, the correcting entries, the billing.
		self.assertEqual(len(calls), 4)
		for call in calls:
			with self.subTest(line=call.lineno):
				query = call.args[0]
				self.assertIsInstance(query, ast.Constant, "a literal query, no f-string")
				self.assertTrue(query.value.strip().lower().startswith("select"))
				self.assertGreaterEqual(len(call.args), 2, "params are bound")
				self.assertNotIn("%s", query.value)
		self.assertNotIn("get_all(", self.source)
		self.assertNotIn("get_list(", self.source)

	def test_the_rules_module_imports_no_frappe(self):
		tree = ast.parse(MODULE.read_text(encoding="utf-8"))
		imported = set()
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				imported.update(alias.name for alias in node.names)
			elif isinstance(node, ast.ImportFrom):
				imported.add(node.module)
		self.assertEqual(imported, {"datetime", "erpnext_enhancements.kpi_dashboards"})

	def _js_filters(self):
		return re.findall(r'fieldname:\s*"([a-z_]+)"', self.js)

	def test_the_filters_are_the_ones_the_python_reads(self):
		read = set(re.findall(r'filters\.get\("([a-z_]+)"\)', self.source))
		self.assertEqual(set(self._js_filters()), read)
		self.assertEqual(self._js_filters(), ["from_date", "to_date", "store", "show"])

	def test_show_options_are_the_buckets(self):
		options = re.search(r'options:\s*\[([^\]]*)\]\.join\("\\n"\)', self.js)
		self.assertIsNotNone(options)
		self.assertEqual(tuple(re.findall(r'"([^"]+)"', options.group(1))), matching.SHOW_OPTIONS)
		self.assertIn('default: "All"', self.js)
		colored = set(re.findall(r'^\s*"?([A-Za-z ]+?)"?:\s*"var\(', self.js, re.M))
		self.assertEqual(colored, {matching.NEEDS_ACTION, matching.WAITING, matching.DONE})

	def test_the_store_filter_offers_store_run_vendors_only(self):
		field = re.search(r'STORE_RUN_FIELD = "([a-z_]+)"', SNAPSHOTS.read_text(encoding="utf-8")).group(1)
		self.assertIn(f"filters: {{ {field}: 1 }}", self.js)

	def test_the_default_range_is_sixty_days_on_both_sides(self):
		self.assertIn("DEFAULT_DAYS = 60", self.source)
		self.assertIn("frappe.datetime.add_days(frappe.datetime.get_today(), -60)", self.js)

	def test_no_buttons(self):
		for needle in ("add_inner_button", "page.add_", "frappe.call", "onload"):
			with self.subTest(needle=needle):
				self.assertNotIn(needle, self.js)

	def test_every_column_is_a_row_key(self):
		fieldnames = set(re.findall(r'"fieldname": "([a-z_0-9]+)"', self.source))
		trips = matching.trips_in_window(
			[charge("2026-09-03", 48.26)],
			[receipt("2026-09-03", total=48.26)],
			"2026-09-01",
			"2026-09-30",
			_key,
		)
		row = matching.build_rows(trips, accounts=ACCOUNTS)[0]
		self.assertTrue(fieldnames <= set(row), fieldnames - set(row))
		self.assertIn('"options": "charge_type"', self.source)
		self.assertIn("charge_type", fieldnames)
		# ...and of a charge that carries 2210 with no store run.
		trips, unpaired = matching.pair_window(
			[charge("2026-09-03", 48.26)], [], "2026-09-01", "2026-09-30", _key
		)
		rows = matching.build_rows(
			trips, on_2210={("Journal Entry", "ACC-JV-1"): 45.0}, accounts=ACCOUNTS, unpaired=unpaired
		)
		self.assertEqual([row["row_type"] for row in rows], [matching.ROW_CHARGE])
		self.assertTrue(fieldnames <= set(rows[0]), fieldnames - set(rows[0]))

	def test_the_summary_cards_read_keys_the_summary_has(self):
		keys = set(re.findall(r'summary\["([a-z_0-9]+)"\]', self.source))
		self.assertIn("unmatched_on_2210", keys)
		self.assertTrue(keys <= set(matching.summarize([])), keys - set(matching.summarize([])))

	def test_the_kpi_reader_returns_charges_in_a_fixed_order(self):
		"""pair_store_runs breaks an exact tie by input order, so each charge arm of the KPI's
		reader orders by posting date and voucher name (v1.538.0 review); before, a tie went to
		whichever row the database returned first."""
		source = SNAPSHOTS.read_text(encoding="utf-8")
		function = next(
			node
			for node in ast.walk(ast.parse(source))
			if isinstance(node, ast.FunctionDef) and node.name == "_store_run_rows"
		)
		# The statements after the docstring, which names the clause it explains.
		body = " ".join(
			" ".join(ast.get_source_segment(source, statement).split()) for statement in function.body[1:]
		)
		for needle in (
			'and vm.erpnext_name in %(suppliers)s order by je.posting_date, je.name """',
			') order by pi.posting_date, pi.name """',
			'{not_qbo} order by je.posting_date, je.name, jea.idx """',
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, body)
		self.assertEqual(body.count("order by"), 4, "three charge arms and the raw-payload pick")


if __name__ == "__main__":
	unittest.main()
