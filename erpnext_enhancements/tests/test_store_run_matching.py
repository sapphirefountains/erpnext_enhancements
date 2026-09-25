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
	"""Every row the report shows when no Reference Number links anything: the trips, and the
	charges that paired with none (listed when they carry 2210)."""
	trips, unpaired = matching.pair_window(charges, receipts, from_date, to_date, _key, **kwargs)
	return matching.build_rows(
		trips, on_2210=on_2210, billed=billed, accounts=ACCOUNTS, corrections=corrections, unpaired=unpaired
	)


def ref(name, reference, day="2026-09-10", docstatus=0, amount=48.26, source="QuickBooks"):
	"""A Journal Entry with a Reference Number, as the report's ``_references`` reads it."""
	return {
		"name": name,
		"reference": reference,
		"docstatus": docstatus,
		"day": day,
		"company": "Sapphire Fountains",
		"amount": amount,
		"source": source,
	}


def report(
	charges,
	receipts,
	references=(),
	far_receipts=(),
	named=(),
	on_2210=None,
	billed=None,
	from_date="2026-09-01",
	to_date="2026-09-30",
	**kwargs,
):
	"""Every row the report shows, as its ``execute`` builds them with the reads replaced by the
	arguments: the Reference Numbers resolved into links and correcting entries, the pairing
	overridden by the links, and each correcting entry's 2210 debit taken from ``on_2210`` (the
	report reads it with the charges')."""
	links, corrections = matching.resolve_links(references, charges, receipts, far_receipts, named)
	trips, unpaired = matching.pair_window(
		charges, receipts, from_date, to_date, _key, links=links, far_receipts=far_receipts, **kwargs
	)
	return matching.build_rows(
		trips,
		on_2210=on_2210,
		billed=billed,
		accounts=ACCOUNTS,
		corrections=matching.correction_amounts(corrections, on_2210 or {}),
		unpaired=unpaired,
	)


#: The two halves of every Waiting text: the charge is found and fixed, OR -- only when there is
#: none -- the trip is billed from its receipts. Never both (v1.538.0 second review).
ONLY_IF_NONE = "Only if it has no card charge at all, bill it from the receipts after the cutover."


def waiting_text(stock, key):
	return (
		f"No card charge paired: find its QuickBooks draft, move {stock} of its goods debit to {ACCOUNT}, "
		f"put this trip's run id ({key}) in its Reference Number and save it (if it is already submitted, "
		f"post a correcting Journal Entry for {stock} (Dr {ACCOUNT} / Cr the expense account it used) whose "
		f"Reference Number is its name followed by {key}). {ONLY_IF_NONE}"
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
		"""An either/or that cannot read as both (v1.538.0 second review): the first version said
		"before the cutover, move it; after the cutover, bill it from the receipts", which read as
		"fix the draft AND bill from the receipts" -- the purchase booked twice. The run id goes
		in the draft's Reference Number, which links the two; a submitted draft's cannot be
		changed, so its link rides on the correcting entry."""
		row = self.one([], [receipt("2026-09-03", total=48.26)])
		self.assertEqual(
			(row["action"], row["show"], row["match_basis"]),
			(waiting_text("$45.00", "sr-a"), matching.WAITING, "No charge yet"),
		)
		self.assertEqual(
			(row["charge"], row["charge_type"], row["charge_status"], row["charge_source"]),
			(None, None, "", ""),
		)

	def test_a_waiting_trip_with_no_stock_lines(self):
		row = self.one([], [receipt("2026-09-03", amount=20.0, stock=0.0, total=21.45)])
		self.assertEqual(
			row["action"],
			"No card charge paired and no stock lines: if it has a QuickBooks draft, change nothing "
			f"(optionally put sr-a in its Reference Number). {ONLY_IF_NONE}",
		)
		self.assertEqual(row["show"], matching.WAITING)

	def test_waiting_texts_are_either_or(self):
		"""Every Waiting text bills from the receipts ONLY when there is no card charge at all, and
		says so once, after the fix on the charge; nothing reads "and bill it" or "after the
		cutover, bill it" unconditionally."""
		cases = [
			[receipt("2026-09-03", total=48.26)],
			[receipt("2026-09-03", amount=20.0, stock=0.0, total=21.45)],
		]
		for receipts in cases:
			row = self.one([], receipts)
			with self.subTest(action=row["action"]):
				self.assertTrue(row["action"].endswith(ONLY_IF_NONE))
				self.assertEqual(row["action"].count("bill it from the receipts"), 1)
				self.assertNotIn("; after the cutover", row["action"])

	def test_the_waiting_text_names_the_trip_key(self):
		"""The run id, or the first receipt's name when the receipt has none."""
		row = self.one([], [receipt("2026-09-03", run="", name="MAT-PRE-2026-00038", total=48.26)])
		self.assertIn("put this trip's run id (MAT-PRE-2026-00038) in its Reference Number", row["action"])

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
# 2b. Correcting entries, charges with no store run (v1.538.0 review), links (second review)
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

	def test_a_draft_over_moved_by_its_correcting_entry_names_the_entry(self):
		"""Its own lines carry the stock lines and a correcting entry adds them again. The first
		version said "reduce it to $45.00" -- which read as the draft's own lines, already at
		$45.00, so following it changed nothing and the row never left Needs action (v1.538.0
		second review). The correcting entry is what is too many, so it is what is reversed."""
		row = self.one({"ACC-JV-1": {"ACC-JV-900": 45.0}}, on_2210={JV1: 45.0}, docstatus=0)
		self.assertEqual(
			row["action"],
			f"The {ACCOUNT} debit is $90.00 but the stock lines are $45.00: the draft's own lines already "
			"carry $45.00 and its correcting entries (ACC-JV-900) add $45.00, $45.00 too many: reverse $45.00 "
			"of them; post a correcting Journal Entry for $45.00 (Dr the expense account the charge used / Cr "
			f"{ACCOUNT}) with Reference Number ACC-JV-1. The S-D loop submits the draft",
		)
		self.assertNotIn("reduce", row["action"])
		self.assertEqual((row["show"], row["to_move"]), (matching.NEEDS_ACTION, 0.0))
		# Doing exactly that -- one more entry naming the charge, the other way -- ends it.
		done = self.one(
			{"ACC-JV-1": {"ACC-JV-900": 45.0, "ACC-JV-901": -45.0}}, on_2210={JV1: 45.0}, docstatus=0
		)
		self.assertEqual(done["show"], matching.DONE)

	def test_a_draft_short_on_its_own_lines_reverses_only_the_excess(self):
		row = self.one({"ACC-JV-1": {"ACC-JV-900": 30.0}}, on_2210={JV1: 20.0}, docstatus=0)
		self.assertIn(
			"the draft's own lines already carry $20.00 and its correcting entries (ACC-JV-900) add $30.00, "
			"$5.00 too many: reverse $5.00 of them",
			row["action"],
		)

	def test_a_draft_over_on_its_own_lines_too_is_cut_to_the_target_not_below(self):
		row = self.one({"ACC-JV-1": {"ACC-JV-900": 10.0}}, on_2210={JV1: 60.0}, docstatus=0)
		self.assertIn(
			"the draft's own lines alone carry $60.00 and its correcting entries (ACC-JV-900) add $10.00: "
			"reduce its own lines to $45.00, then save it, and reverse the correcting entries; post a "
			"correcting Journal Entry for $10.00",
			row["action"],
		)

	def test_a_draft_whose_correcting_entry_takes_some_off(self):
		row = self.one({"ACC-JV-1": {"ACC-JV-900": -5.0}}, on_2210={JV1: 60.0}, docstatus=0)
		self.assertIn(
			"its correcting entries (ACC-JV-900) take $5.00 off, so reduce the draft's own lines to $50.00",
			row["action"],
		)

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
	"""A charge dated in range that is neither paired nor linked but carries 2210 -- a draft
	adjusted for a trip but not linked to it, or whose trip's receipt was cancelled -- is listed
	under Needs action, never silently dropped: link it, or move it back."""

	def test_a_draft_adjusted_for_a_trip_since_cancelled_is_listed(self):
		rows = listed([charge("2026-09-05", 48.26, "ACC-JV-7")], [], on_2210={JV7: 45.0})
		self.assertEqual(len(rows), 1)
		row = rows[0]
		self.assertEqual(
			row["action"],
			f"Carries $45.00 on {ACCOUNT} but no recorded store run is paired or linked: if you moved it for "
			"a trip, put that trip's run id in this entry's Reference Number; otherwise move it back to the "
			"expense. Then save it; the S-D loop submits it",
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
		"""A submitted entry's Reference Number cannot be changed (v16: ``cheque_no`` is not
		allow_on_submit), so it is not told to put a run id there: it is moved back, and the trip's
		own row then gives the linked way to move it again."""
		rows = listed([charge("2026-09-05", 48.26, "ACC-JV-7", docstatus=1)], [], on_2210={JV7: 45.0})
		self.assertEqual(
			rows[0]["action"],
			f"Carries $45.00 on {ACCOUNT} but no recorded store run is paired or linked, and a submitted "
			"entry's Reference Number cannot be changed: move it back to the expense; post a correcting "
			f"Journal Entry for $45.00 (Dr the expense account it used / Cr {ACCOUNT}) with Reference Number "
			"ACC-JV-7. If you moved it for a trip, that trip's row then says how to move it again, linked",
		)
		self.assertEqual(rows[0]["charge_status"], "Submitted")

	def test_its_correcting_entries_count(self):
		charges = [charge("2026-09-05", 48.26, "ACC-JV-7", docstatus=1)]
		cleared = listed(charges, [], on_2210={JV7: 45.0}, corrections={"ACC-JV-7": {"ACC-JV-900": -45.0}})
		self.assertEqual(cleared, [])
		part = listed(charges, [], on_2210={JV7: 45.0}, corrections={"ACC-JV-7": {"ACC-JV-900": -20.0}})
		self.assertTrue(
			part[0]["action"].startswith(
				f"Carries $25.00 on {ACCOUNT} (corrected by ACC-JV-900) but no recorded store run is paired "
				"or linked"
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
			f"Takes $5.00 off {ACCOUNT} (corrected by ACC-JV-900) but no recorded store run is paired or "
			f"linked: bring it back to $0.00; post a correcting Journal Entry for $5.00 (Dr {ACCOUNT} / Cr "
			"the expense account it used) with Reference Number ACC-JV-7",
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


class TestLinks(unittest.TestCase):
	"""A Journal Entry whose Reference Number lists a trip's run id is that trip's charge (v1.538.0
	second review). It replaces recognizing a draft "found by hand" by a 2210 debit equal to a
	waiting trip's stock lines, which could not serve two runs of one purchase (one draft, one
	target each), lost a trip dated before From Date -- its adjusted draft was then listed to be
	moved back -- and could hand one trip's draft to another trip with the same stock lines."""

	def late(self, docstatus=0):
		"""The trip's card charge, seven days late: the pairing never takes it."""
		return [charge("2026-09-10", 48.26, "ACC-JV-7", docstatus=docstatus)]

	def only(self, rows):
		self.assertEqual(len(rows), 1, rows)
		return rows[0]

	def test_reference_tokens(self):
		self.assertEqual(
			matching.reference_tokens(" SR-abc, sr-DEF;sr-abc  MAT-PRE-2026-00038 "),
			["sr-abc", "sr-def", "mat-pre-2026-00038"],
		)
		self.assertEqual(matching.reference_tokens(None), [])
		self.assertEqual(matching.reference_tokens(" ,; "), [])

	def test_before_it_is_linked_the_trip_waits_and_the_charge_is_not_listed(self):
		rows = report(self.late(), _trip())
		self.assertEqual(
			[(row["row_type"], row["show"]) for row in rows], [(matching.ROW_TRIP, matching.WAITING)]
		)

	def test_a_linked_draft_is_the_trip_s_charge(self):
		row = self.only(report(self.late(), _trip(), [ref("ACC-JV-7", "sr-a")]))
		self.assertEqual(
			(row["charge"], row["match_basis"], row["show"], row["to_move"]),
			("ACC-JV-7", "Linked by Reference Number", matching.NEEDS_ACTION, 45.0),
		)
		self.assertEqual(
			row["action"],
			f"Move $45.00 of the goods debit to {ACCOUNT}, then save it; the S-D loop submits it",
		)

	def test_done_when_its_2210_equals_the_stock_lines(self):
		rows = report(self.late(), _trip(), [ref("ACC-JV-7", "sr-a")], on_2210={JV7: 45.0})
		row = self.only(rows)
		self.assertEqual((row["charge"], row["show"], row["moved_to_2210"]), ("ACC-JV-7", matching.DONE, 1))
		self.assertEqual(
			row["action"], f"Goods debit on {ACCOUNT}: nothing to change; the S-D loop submits it"
		)
		summary = matching.summarize(rows)
		self.assertEqual(
			(summary["matched"], summary["moved"], summary["to_move"], summary["needs_action"]),
			(1, 45.0, 0.0, 0),
		)

	def test_the_key_is_matched_trimmed_and_ignoring_case(self):
		for reference in (" SR-A ", "sr-a,", "card 4471; Sr-A"):
			with self.subTest(reference=reference):
				row = self.only(
					report(self.late(), _trip(), [ref("ACC-JV-7", reference)], on_2210={JV7: 45.0})
				)
				self.assertEqual((row["charge"], row["show"]), ("ACC-JV-7", matching.DONE))

	def test_a_trip_with_no_run_id_is_linked_by_its_receipt_s_name(self):
		receipts = [receipt("2026-09-03", run="", name="MAT-PRE-2026-00038", total=48.26)]
		row = self.only(
			report(self.late(), receipts, [ref("ACC-JV-7", "mat-pre-2026-00038")], on_2210={JV7: 45.0})
		)
		self.assertEqual((row["charge"], row["show"]), ("ACC-JV-7", matching.DONE))

	def test_a_key_that_names_no_trip_links_nothing(self):
		rows = report(self.late(), _trip(), [ref("ACC-JV-7", "sr-typo")], on_2210={JV7: 45.0})
		self.assertEqual(
			[(row["row_type"], row["show"]) for row in rows],
			[(matching.ROW_CHARGE, matching.NEEDS_ACTION), (matching.ROW_TRIP, matching.WAITING)],
		)
		self.assertIn("put that trip's run id in this entry's Reference Number", rows[0]["action"])

	def test_two_runs_of_one_purchase_one_draft(self):
		"""The case the equal-2210 rule could not serve: one draft pays for two runs, so its target
		is their stock lines together, and both trips reach Done at once."""
		receipts = [
			receipt("2026-09-03", "sr-a", 60.0, total=107.0),
			receipt("2026-09-03", "sr-b", 40.0, total=107.0),
		]
		charges = [charge("2026-09-04", 107.0, "ACC-JV-1")]
		before = {row["run_ref"]: row for row in report(charges, receipts)}
		self.assertEqual(
			(before["sr-a"]["charge"], before["sr-b"]["charge"], before["sr-b"]["show"]),
			("ACC-JV-1", None, matching.WAITING),
		)
		references = [ref("ACC-JV-1", "sr-a, sr-b", day="2026-09-04", amount=107.0)]
		for on, show, action, to_move in (
			(
				0.0,
				matching.NEEDS_ACTION,
				f"Move $100.00 of the goods debit to {ACCOUNT} (the stock lines of sr-a, sr-b together), then "
				"save it; the S-D loop submits it",
				100.0,
			),
			(
				60.0,
				matching.NEEDS_ACTION,
				f"Move $40.00 more of the goods debit to {ACCOUNT} (the stock lines of sr-a, sr-b together) "
				"($60.00 is there already), then save it; the S-D loop submits it",
				40.0,
			),
			(
				100.0,
				matching.DONE,
				f"Goods debit on {ACCOUNT}: nothing to change; the S-D loop submits it",
				0.0,
			),
		):
			with self.subTest(on_2210=on):
				rows = report(charges, receipts, references, on_2210={JV1: on})
				self.assertEqual([row["run_ref"] for row in rows], ["sr-a", "sr-b"])
				for row in rows:
					self.assertEqual(
						(row["charge"], row["match_basis"], row["show"], row["action"]),
						("ACC-JV-1", "Linked by Reference Number", show, action),
					)
				summary = matching.summarize(rows)
				self.assertEqual((summary["to_move"], summary["moved"]), (to_move, on))
				self.assertEqual(
					[row["moved_to_2210"] for row in rows], [1, 1] if show == matching.DONE else [0, 0]
				)

	def test_linking_only_the_second_run_says_how_to_add_the_first(self):
		"""Following sr-b's Waiting text on the very draft sr-a pairs with: the link takes the draft
		from sr-a, and both rows then say the same thing -- add sr-a -- rather than undoing each
		other, and doing it finishes both (no loop)."""
		receipts = [
			receipt("2026-09-03", "sr-a", 60.0, total=107.0),
			receipt("2026-09-03", "sr-b", 40.0, total=107.0),
		]
		charges = [charge("2026-09-04", 107.0, "ACC-JV-1")]
		rows = {
			row["run_ref"]: row
			for row in report(charges, receipts, [ref("ACC-JV-1", "sr-b")], on_2210={JV1: 100.0})
		}
		self.assertEqual(
			rows["sr-b"]["action"],
			f"The {ACCOUNT} debit is $100.00 but the stock lines of the trip its Reference Number links "
			"(sr-b) are $40.00: reduce it to $40.00, then save it; the S-D loop submits it. It was paired "
			"with sr-a until its Reference Number linked sr-b: if it pays for sr-a too, add sr-a to its "
			"Reference Number instead",
		)
		self.assertEqual(rows["sr-b"]["show"], matching.NEEDS_ACTION)
		self.assertEqual((rows["sr-a"]["charge"], rows["sr-a"]["show"]), (None, matching.WAITING))
		self.assertTrue(
			rows["sr-a"]["action"].startswith(
				"Its paired charge ACC-JV-1 now links only sr-b, by its Reference Number: if ACC-JV-1 pays "
				"for this trip too, add sr-a to that Reference Number. If not, find its QuickBooks draft"
			),
			rows["sr-a"]["action"],
		)
		self.assertTrue(rows["sr-a"]["action"].endswith(ONLY_IF_NONE))
		done = report(charges, receipts, [ref("ACC-JV-1", "sr-b, sr-a")], on_2210={JV1: 100.0})
		self.assertEqual([row["show"] for row in done], [matching.DONE, matching.DONE])

	def test_a_link_overrides_an_automatic_pair(self):
		"""The trip's automatic charge goes back to unpaired: listed if it carries 2210 (it was moved
		for this trip before the link named another charge), silent if it carries nothing."""
		charges = [charge("2026-09-04", 48.26, "ACC-JV-1"), charge("2026-09-12", 50.0, "ACC-JV-7")]
		self.assertEqual(self.only(report(charges, _trip()))["charge"], "ACC-JV-1")
		references = [ref("ACC-JV-7", "sr-a", day="2026-09-12", amount=50.0)]
		rows = report(charges, _trip(), references, on_2210={JV7: 45.0})
		self.assertEqual(
			[(row["row_type"], row["charge"], row["show"]) for row in rows],
			[(matching.ROW_TRIP, "ACC-JV-7", matching.DONE)],
		)
		rows = report(charges, _trip(), references, on_2210={JV7: 45.0, JV1: 45.0})
		self.assertEqual(
			[(row["row_type"], row["charge"], row["show"]) for row in rows],
			[
				(matching.ROW_CHARGE, "ACC-JV-1", matching.NEEDS_ACTION),
				(matching.ROW_TRIP, "ACC-JV-7", matching.DONE),
			],
		)

	def test_a_link_to_a_trip_before_from_keeps_its_charge_off_the_list(self):
		"""The S-D run, from 2026-01-01, links a late draft to its trip. A later run whose From Date
		falls after the trip but before the charge must not tell Accounting to undo it -- the draft
		carries 2210 and pairs with nothing in that range."""
		receipts = [receipt("2026-10-01", "sr-1", 100.0, total=107.0)]
		for docstatus in (0, 1):
			charges = [charge("2026-10-09", 107.0, "ACC-JV-7", docstatus=docstatus)]
			references = [ref("ACC-JV-7", "sr-1", day="2026-10-09", docstatus=docstatus, amount=107.0)]
			with self.subTest(docstatus=docstatus):
				at_sd = report(
					charges,
					receipts,
					references,
					on_2210={JV7: 100.0},
					from_date="2026-01-01",
					to_date="2026-10-20",
				)
				self.assertEqual(
					[(row["charge"], row["show"]) for row in at_sd], [("ACC-JV-7", matching.DONE)]
				)
				later = report(
					charges,
					receipts,
					references,
					on_2210={JV7: 100.0},
					from_date="2026-10-05",
					to_date="2026-12-01",
				)
				self.assertEqual(later, [])

	def test_a_trip_before_the_lookback_is_reached_by_key(self):
		"""The KPI's reader reads from 7 days before From Date; a trip older than that is looked up by
		its key (``far_receipts``). Without the lookup the charge would read as having no store run."""
		trip = [receipt("2026-09-01", "sr-1", 100.0, total=107.0)]
		charges = [charge("2026-10-09", 107.0, "ACC-JV-7")]
		references = [ref("ACC-JV-7", "sr-1", day="2026-10-09", amount=107.0)]
		self.assertEqual(matching.unresolved_tokens(references, charges, []), ["sr-1"])
		window = {"on_2210": {JV7: 100.0}, "from_date": "2026-10-05", "to_date": "2026-12-01"}
		self.assertEqual(report(charges, [], references, far_receipts=trip, **window), [])
		unread = report(charges, [], references, **window)
		self.assertEqual([row["row_type"] for row in unread], [matching.ROW_CHARGE])

	def test_a_linked_trip_outside_the_range_still_counts_toward_the_target(self):
		far = [receipt("2026-08-01", "sr-far", 30.0, total=32.0)]
		charges = [charge("2026-09-10", 80.26, "ACC-JV-7")]
		references = [ref("ACC-JV-7", "sr-far sr-a", amount=80.26)]
		row = self.only(report(charges, _trip(), references, far_receipts=far))
		self.assertEqual(
			row["action"],
			f"Move $75.00 of the goods debit to {ACCOUNT} (the stock lines of sr-far, sr-a together), then "
			"save it; the S-D loop submits it",
		)
		self.assertEqual(row["to_move"], 45.0, "this row's share; sr-far's $30.00 is outside the range")
		done = self.only(report(charges, _trip(), references, far_receipts=far, on_2210={JV7: 75.0}))
		self.assertEqual(done["show"], matching.DONE)

	def test_a_draft_under_a_vendor_that_is_not_a_store_run_vendor(self):
		"""The KPI's reader never sees it -- its QuickBooks vendor is not ticked Store-Run Vendor --
		but every Journal Entry's Reference Number is read, so linking it is enough."""
		row = self.only(
			report([], _trip(), [ref("ACC-JV-50", "sr-a")], on_2210={("Journal Entry", "ACC-JV-50"): 45.0})
		)
		self.assertEqual(
			(row["charge"], row["charge_source"], row["charge_date"], row["show"]),
			("ACC-JV-50", matching.SOURCE_QBO_DRAFT, date(2026, 9, 10), matching.DONE),
		)

	def test_a_submitted_charge_is_linked_by_its_correcting_entry(self):
		"""A submitted entry's Reference Number cannot be changed, so a charge submitted before it
		was linked is linked by the correcting entry that moves its goods: the charge, then the run id
		-- which is what the Waiting text says to do for one already submitted."""
		references = [ref("ACC-JV-900", "ACC-JV-7 sr-a", day="2026-10-25", docstatus=1, source="ERPNext")]
		row = self.only(
			report(
				self.late(docstatus=1), _trip(), references, on_2210={("Journal Entry", "ACC-JV-900"): 45.0}
			)
		)
		self.assertEqual(
			(row["charge"], row["match_basis"], row["action"], row["show"]),
			("ACC-JV-7", "Linked by Reference Number", "Done (corrected by ACC-JV-900)", matching.DONE),
		)

	def test_a_correcting_entry_names_a_charge_nothing_else_read(self):
		"""An unticked-vendor QuickBooks charge submitted before it was linked: the report looks its
		name up (``unresolved_tokens``, then ``_entries_by_name``) and links it through its
		correcting entry."""
		references = [ref("ACC-JV-900", "ACC-JV-50, sr-a", docstatus=1, source="ERPNext")]
		self.assertEqual(matching.unresolved_tokens(references, [], _trip()), ["acc-jv-50"])
		named = [ref("ACC-JV-50", "", day="2026-09-05", docstatus=1)]
		row = self.only(
			report([], _trip(), references, named=named, on_2210={("Journal Entry", "ACC-JV-900"): 45.0})
		)
		self.assertEqual(
			(row["charge"], row["action"], row["charge_source"]),
			("ACC-JV-50", "Done (corrected by ACC-JV-900)", matching.SOURCE_QBO),
		)

	def test_a_draft_correcting_entry_counts_for_nothing(self):
		references = [ref("ACC-JV-900", "ACC-JV-7 sr-a", docstatus=0)]
		self.assertEqual(matching.resolve_links(references, self.late(docstatus=1), _trip()), ({}, {}))

	def test_a_charge_never_corrects_another_or_itself(self):
		"""An entry the KPI reads as a charge is a charge, whatever its Reference Number names, as the
		first correcting-entry read had it (``je.name not in`` the charges); and no entry corrects
		itself."""
		charges = [
			charge("2026-09-04", 48.26, "ACC-JV-1", docstatus=1),
			charge("2026-09-20", 10.0, "ACC-JV-2", docstatus=1),
		]
		references = [ref("ACC-JV-2", "ACC-JV-1", docstatus=1)]
		self.assertEqual(matching.resolve_links(references, charges, _trip()), ({}, {}))
		self.assertEqual(
			matching.resolve_links([ref("ACC-JV-900", "acc-jv-900", docstatus=1)], [], []), ({}, {})
		)

	def test_a_correcting_entry_still_names_its_charge_alone(self):
		"""The first version's correcting entry -- the charge's name and nothing else, typed in any
		case with spaces around it -- keeps its meaning."""
		references = [ref("ACC-JV-900", " acc-jv-7 ", docstatus=1)]
		self.assertEqual(
			matching.resolve_links(references, self.late(docstatus=1), _trip()),
			({}, {"ACC-JV-7": ["ACC-JV-900"]}),
		)

	def test_two_charges_linking_one_trip(self):
		charges = [*self.late(), charge("2026-09-11", 48.26, "ACC-JV-8")]
		references = [ref("ACC-JV-7", "sr-a"), ref("ACC-JV-8", "sr-a", day="2026-09-11")]
		row = self.only(report(charges, _trip(), references, on_2210={JV7: 45.0}))
		self.assertEqual(
			(row["action"], row["show"]),
			(
				"Linked from more than one charge's Reference Number (sr-a by ACC-JV-7, ACC-JV-8): keep each "
				"run id in one charge's Reference Number only",
				matching.NEEDS_ACTION,
			),
		)

	def test_linked_and_billed_from_the_receipts(self):
		row = self.only(
			report(
				self.late(),
				[receipt("2026-09-03", total=48.26, name="PR-1")],
				[ref("ACC-JV-7", "sr-a")],
				on_2210={JV7: 45.0},
				billed={"PR-1": "ACC-PINV-7"},
			)
		)
		self.assertTrue(
			row["action"].startswith(
				"Billed from the receipts (ACC-PINV-7) and linked to this charge by its Reference Number too"
			),
			row["action"],
		)
		self.assertEqual(row["show"], matching.NEEDS_ACTION)

	def test_a_submitted_linked_charge_short_of_its_target(self):
		references = [ref("ACC-JV-7", "sr-a", docstatus=1)]
		row = self.only(report(self.late(docstatus=1), _trip(), references))
		self.assertEqual(
			row["action"],
			"Submitted with the goods on the expense: post a correcting Journal Entry for $45.00 "
			f"(Dr {ACCOUNT} / Cr the expense account the charge used) with Reference Number ACC-JV-7",
		)

	def test_generated_histories_with_links(self):
		"""Random histories and random Reference Numbers (junk tokens and typos included): a charge
		whose Reference Number names a trip is never listed as having no store run, no charge is
		listed both ways, a trip one charge links shows that charge, and a trip two charges link is
		flagged."""
		rng = random.Random(20261022)
		start = date(2026, 9, 1)
		for case in range(600):
			receipts = [
				receipt(
					(start + timedelta(days=rng.randint(-10, 40))).isoformat(),
					f"sr-{index}",
					rng.choice((10.0, 20.0, 45.0)),
					stock=rng.choice((None, 0.0)),
					total=rng.choice((0.0, 21.45, 48.26)),
				)
				for index in range(rng.randint(0, 8))
			]
			charges = [
				charge(
					(start + timedelta(days=rng.randint(-5, 40))).isoformat(),
					rng.choice((10.7, 21.45, 48.26, 60.0)),
					f"JV-{index}",
					docstatus=rng.choice((0, 1)),
				)
				for index in range(rng.randint(0, 8))
			]
			runs = [row["run"] for row in receipts]
			references = []
			for row in charges:
				if rng.random() < 0.5:
					tokens = rng.sample(runs, min(len(runs), rng.randint(0, 2))) + rng.choice(
						([], ["sr-typo"], ["4471"])
					)
					references.append(
						ref(row["voucher_no"], ", ".join(tokens), day=row["day"], docstatus=row["docstatus"])
					)
			on_2210 = {("Journal Entry", row["voucher_no"]): rng.choice((0.0, 10.0, 45.0)) for row in charges}
			# As the report reads them: the reader from 7 days before From Date, the rest by key.
			early = (start - timedelta(days=metrics.STORE_RUN_LOOKBACK_DAYS)).isoformat()
			read = [row for row in receipts if row["day"] >= early]
			far = [row for row in receipts if row["day"] < early]
			rows = report(charges, read, references, far_receipts=far, on_2210=on_2210)
			links, _corrections = matching.resolve_links(references, charges, read, far)
			claims = {}
			for (_kind, name), link in links.items():
				for key in link["keys"]:
					claims.setdefault(key, []).append(name)
			linked = {name for names in claims.values() for name in names}
			on_trips = {
				row["charge"] for row in rows if row["row_type"] == matching.ROW_TRIP and row["charge"]
			}
			for row in rows:
				with self.subTest(case=case, row=row["run_ref"] or row["charge"]):
					if row["row_type"] == matching.ROW_CHARGE:
						self.assertNotIn(row["charge"], linked)
						self.assertNotIn(row["charge"], on_trips)
						continue
					names = claims.get(row["run_ref"], [])
					if len(names) == 1:
						self.assertEqual(
							(row["charge"], row["match_basis"]), (names[0], "Linked by Reference Number")
						)
					elif names:
						self.assertTrue(
							row["action"].startswith("Linked from more than one charge"), row["action"]
						)
					else:
						self.assertNotEqual(row["match_basis"], "Linked by Reference Number")
					self.assertGreaterEqual(row["to_move"], 0.0)

	def test_no_links_is_the_kpi_s_pairing(self):
		charges = [charge("2026-09-04", 48.26, "ACC-JV-1")]
		self.assertEqual(matching.resolve_links([], charges, _trip()), ({}, {}))
		plain = matching.pair_window(charges, _trip(), "2026-09-01", "2026-09-30", _key)
		empty = matching.pair_window(charges, _trip(), "2026-09-01", "2026-09-30", _key, links={})
		self.assertEqual(repr(plain), repr(empty))

	def test_the_report_s_helpers(self):
		found = {"Journal Entry": ["ACC-JV-1", "ACC-JV-7"], "Purchase Invoice": ["ACC-PINV-1"]}
		corrections = {"ACC-JV-7": ["ACC-JV-901", "ACC-JV-900"], "ACC-JV-99": ["ACC-JV-902"]}
		self.assertEqual(matching.correcting_entries(found, corrections), ["ACC-JV-900", "ACC-JV-901"])
		self.assertEqual(
			matching.correction_amounts(corrections, {("Journal Entry", "ACC-JV-900"): 45.0}),
			{"ACC-JV-7": {"ACC-JV-900": 45.0}},
		)
		far = [receipt("2026-08-01", "sr-far", 30.0, name="PR-far")]
		receipts = [receipt("2026-09-03", name="PR-a", total=48.26)]
		links, _corrections = matching.resolve_links(
			[ref("ACC-JV-7", "sr-far sr-a")], self.late(), receipts, far
		)
		trips, _unpaired = matching.pair_window(
			self.late(),
			receipts,
			"2026-09-01",
			"2026-09-30",
			_key,
			links=links,
			far_receipts=far,
		)
		self.assertEqual(matching.receipt_names(trips), ["PR-a", "PR-far"])


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
			"_references(add_days(from_date, -snapshots.STORE_RUN_LOOKBACK_DAYS))",
			"matching.unresolved_tokens(references, charges, receipts)",
			"_receipts_by_key(suppliers, unknown)",
			"_entries_by_name(unknown)",
			"matching.resolve_links(",
			"matching.pair_window(",
			"links=links",
			"far_receipts=far_receipts",
			"matching.vouchers(trips, unpaired)",
			"matching.correcting_entries(found, corrections)",
			"matching.build_rows(",
			"billed=_billed(matching.receipt_names(trips))",
			"corrections=matching.correction_amounts(corrections, on_2210)",
			"unpaired=unpaired",
			"matching.filter_rows(",
			"matching.summarize(",
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, code)
		self.assertNotIn("pair_store_runs", code, "the pairing is reached through pair_window only")

	def _query(self, needle):
		queries = [
			" ".join(text.split()) for text in _strings(_without_docstrings(self.tree)) if needle in text
		]
		self.assertEqual(len(queries), 1, needle)
		return queries[0]

	def test_it_reads_every_reference_number(self):
		"""Links and correcting entries both come from the Reference Number (cheque_no) of Journal
		Entries, draft or submitted, read for EVERY vendor: a QuickBooks draft under a vendor that is
		not ticked Store-Run Vendor is invisible to the KPI's reader, and linking it is how Accounting
		says it is a trip's charge (v1.538.0 second review). Which entry is what is decided in
		``resolve_links``, tested above; no SQL decides it any more."""
		query = self._query("je.posting_date >= %(early)s")
		for needle in (
			"select je.name, je.cheque_no as reference, je.docstatus, je.posting_date as day",
			"from `tabJournal Entry` je",
			"where je.docstatus < 2 and je.cheque_no <> '' and je.posting_date >= %(early)s",
			"je.total_debit as amount",
			"if(qm.erpnext_name is null, 'ERPNext', 'QuickBooks') as source",
			"left join ( select distinct m.erpnext_name from `tabQuickBooks Sync Mapping` m where "
			"m.erpnext_doctype = 'Journal Entry' ) qm on qm.erpnext_name = je.name",
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, query)
		for needle in ("supplier", "party", "exists"):
			with self.subTest(absent=needle):
				self.assertNotIn(needle, query)
		named = self._query("je.name in %(names)s")
		self.assertEqual(named.split(" where ")[0], query.split(" where ")[0], "the same columns and join")
		self.assertIn("where je.docstatus < 2 and je.name in %(names)s", named)
		self.assertNotIn("trim(je.cheque_no)", self.code)

	def test_trips_read_by_key_are_read_as_the_kpi_reads_them(self):
		"""``_receipts_by_key`` repeats the receipts query of ``snapshots._store_run_rows``, read by key
		instead of by date: the same columns computed the same way (the stock lines from the GL),
		and the same rules."""
		run = "coalesce(nullif(pr.`custom_store_run`, ''), pr.name)"
		total = "coalesce(pr.`custom_receipt_total`, 0)"
		source = SNAPSHOTS.read_text(encoding="utf-8")
		node = next(
			node
			for node in ast.walk(ast.parse(source))
			if isinstance(node, ast.JoinedStr) and "as stock_amount" in ast.get_source_segment(source, node)
		)
		reader = ast.get_source_segment(source, node)[4:-3]
		reader = " ".join(reader.replace("{run_expr}", run).replace("{total_expr}", total).split())
		ours = self._query("as stock_amount")
		head = " from `tabPurchase Receipt` pr "
		self.assertEqual(ours.split(head)[0], reader.split(head)[0])
		for clause in (
			"where pr.docstatus = 1 and pr.is_return = 0",
			"and pr.supplier in %(suppliers)s",
			"and not exists ( select 1 from `tabPurchase Receipt Item` i where i.parent = pr.name and "
			"coalesce(i.purchase_order, '') <> '' )",
		):
			with self.subTest(clause=clause):
				self.assertIn(clause, reader)
				self.assertIn(clause, ours)
		self.assertIn(f"and {run} in %(keys)s", ours)
		self.assertNotIn("posting_date >=", ours)

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
		# The Reference Numbers, the Journal Entries and trips they name, a Journal Entry's own 2210
		# lines (correcting entries' included), a Purchase Invoice's, the billing.
		self.assertEqual(len(calls), 6)
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
		self.assertEqual(imported, {"datetime", "re", "erpnext_enhancements.kpi_dashboards"})

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
