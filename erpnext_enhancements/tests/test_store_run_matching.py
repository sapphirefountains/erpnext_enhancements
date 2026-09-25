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
			kpi_trips, _bills = metrics.pair_store_runs(
				[row for row in charges if date.fromisoformat(row["day"]) >= early],
				[row for row in receipts if date.fromisoformat(row["day"]) >= early],
				_key,
			)
			expected = {
				trip["key"]: (trip["charge"]["row"]["voucher_no"] if trip["charge"] else None, trip["basis"])
				for trip in kpi_trips
				if from_day <= trip["day"] <= to_day
			}
			listed = matching.trips_in_window(charges, receipts, from_day, to_day, _key)
			got = {
				trip["key"]: (trip["charge"]["row"]["voucher_no"] if trip["charge"] else None, trip["basis"])
				for trip in listed
			}
			self.assertEqual(got, expected, f"case {case}, {from_day}..{to_day}")


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
		self.assertEqual(row["action"], f"Move $45.00 of the goods debit to {ACCOUNT}, then submit")
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
			f"Move $25.00 more of the goods debit to {ACCOUNT} ($20.00 is there already), then submit",
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
				self.assertEqual(row["action"], f"Goods debit on {ACCOUNT}: submit")
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
			f"The {ACCOUNT} debit is $48.26 but the stock lines are $45.00: reduce it to $45.00, then submit",
		)
		self.assertEqual((row["moved_to_2210"], row["show"]), (0, matching.NEEDS_ACTION))

	def test_no_stock_lines_submit_as_is(self):
		row = self.one(
			[charge("2026-09-03", 21.45)], [receipt("2026-09-03", amount=20.0, stock=0.0, total=21.45)]
		)
		self.assertEqual(row["action"], "No stock lines: submit as is")
		self.assertEqual((row["moved_to_2210"], row["show"], row["stock_amount"]), (0, matching.DONE, 0.0))

	def test_a_submitted_charge_without_the_move_books_the_goods_twice(self):
		row = self.one([charge("2026-09-03", 48.26, docstatus=1)], [receipt("2026-09-03", total=48.26)])
		self.assertEqual(
			row["action"],
			f"Submitted with the goods on the expense: move $45.00 from the expense to {ACCOUNT} "
			"(amend it or post a correcting Journal Entry)",
		)
		self.assertEqual(
			(row["charge_status"], row["charge_source"], row["show"]),
			("Submitted", "QuickBooks Journal Entry", matching.NEEDS_ACTION),
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
		row = self.one([], [receipt("2026-09-03", total=48.26)])
		self.assertEqual(
			(row["action"], row["show"], row["match_basis"]),
			("Waiting for the card charge", matching.WAITING, "No charge yet"),
		)
		self.assertEqual(
			(row["charge"], row["charge_type"], row["charge_status"], row["charge_source"]),
			(None, None, "", ""),
		)

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
		self.assertEqual(row["action"], "Move $45.00 of the goods debit to 2210 - SRBNB - X, then submit")
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
			{"trips": 4, "matched": 3, "needs_action": 1, "waiting": 1, "to_move": 40.0, "moved": 35.0},
		)

	def test_an_empty_range(self):
		self.assertEqual(
			matching.summarize([]),
			{"trips": 0, "matched": 0, "needs_action": 0, "waiting": 0, "to_move": 0.0, "moved": 0.0},
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
			"matching.trips_in_window(",
			"matching.build_rows(",
			"matching.filter_rows(",
			"matching.summarize(",
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, code)
		self.assertNotIn("pair_store_runs", code, "the pairing is reached through trips_in_window only")

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
		self.assertEqual(len(calls), 3)
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


if __name__ == "__main__":
	unittest.main()
