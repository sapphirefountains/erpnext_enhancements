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


def flow(
	charges,
	receipts,
	references=(),
	far_receipts=(),
	named=(),
	on_2210=None,
	billed=None,
	from_date="2026-09-01",
	to_date="2026-09-30",
	journal=None,
	**kwargs,
):
	"""``(rows, window, resolution, journal, on_2210, corrections)`` as the report's ``execute``
	builds them with the reads replaced by the arguments: the Reference Numbers resolved, the pairing
	overridden by the links, every 2210 amount taken from ``on_2210``. ``journal`` -- the backstop's
	read, every Journal Entry with a line on 2210 dated from the lookback to To Date -- is, unless
	given, every Journal Entry of ``on_2210`` so dated (its date from ``references``, ``named`` or
	``charges``)."""
	on_2210 = dict(on_2210 or {})
	resolution = matching.resolve_references(references, charges, receipts, far_receipts, named)
	window = matching.match_window(
		charges,
		receipts,
		from_date,
		to_date,
		_key,
		links=resolution["links"],
		far_receipts=far_receipts,
		excluded=resolution["excluded"],
		roots=resolution["roots"],
		**kwargs,
	)
	if journal is None:
		known = {
			row["voucher_no"]: {
				"name": row["voucher_no"],
				"reference": "",
				"docstatus": row["docstatus"],
				"day": row["day"],
				"company": "Sapphire Fountains",
				"amount": row["amount"],
				"source": row["source"],
			}
			for row in charges
			if row.get("voucher_type") == "Journal Entry"
		}
		known.update({entry["name"]: entry for entry in list(named) + list(references)})
		journal = [
			known[name]
			for (kind, name) in on_2210
			if kind == "Journal Entry"
			and name in known
			and window["early"] <= date.fromisoformat(str(known[name]["day"])) <= window["end"]
		]
	corrections = matching.correction_amounts(resolution["corrections"], on_2210)
	rows = matching.build_rows(
		window["trips"],
		on_2210=on_2210,
		billed=billed,
		accounts=ACCOUNTS,
		corrections=corrections,
		unpaired=window["unpaired"],
		window=window,
		resolution=resolution,
		journal=journal,
	)
	return rows, window, resolution, journal, on_2210, corrections


def report(*args, **kwargs):
	"""Every row the report shows (:func:`flow`)."""
	return flow(*args, **kwargs)[0]


#: The two halves of every Waiting text: the charge is found and fixed, OR -- only when there is
#: none -- the trip is billed from its receipts. Never both (v1.538.0 second review).
ONLY_IF_NONE = "Only if it has no card charge at all, bill it from the receipts after the cutover."


def find_its_draft(stock, key, account=ACCOUNT):
	"""How a waiting trip finds and links its charge. "List both" only when the draft pays for both
	(fourth review, item 1); a submitted one's correcting entry names every trip it pays for (item 3)."""
	return (
		f"find its QuickBooks draft, move {stock} of its goods debit to {account}, put this trip's run id "
		f"({key}) in its Reference Number and save it; if it is already submitted, post and submit a "
		f"correcting Journal Entry for {stock} (Dr 2210 / Cr the expense account it used) whose Reference "
		f"Number is its name followed by {key}. If that charge is already another trip's in this list and it "
		"also pays for that trip, list both trips' run ids (a draft: in its Reference Number, its 2210 debit "
		"carrying both trips' stock lines; submitted: after its name in that correcting entry). If it does not "
		"pay for that trip, take that trip's run id out of what links it to this charge (edit a draft and "
		"save it, cancel a submitted correcting entry), and that trip's row then asks for its own charge"
	)


def waiting_text(stock, key):
	"""The account is written in full once per text, by its number after that (third review)."""
	return f"No card charge paired: {find_its_draft(stock, key)}. {ONLY_IF_NONE}"


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
		charge detaches it from its sync mapping, and so from this list (v1.538.0 review). It names the
		trip too, so the correction links them (fourth review, item 3)."""
		row = self.one([charge("2026-09-03", 48.26, docstatus=1)], [receipt("2026-09-03", total=48.26)])
		self.assertEqual(
			row["action"],
			"Submitted with the goods on the expense: post and submit a correcting Journal Entry for $45.00 "
			f"(Dr {ACCOUNT} / Cr the expense account the charge used) with Reference Number ACC-JV-1 followed "
			"by sr-a",
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
			f"{ACCOUNT} already, corrected by ACC-JV-900): post and submit a correcting Journal Entry for "
			"$25.00 (Dr 2210 / Cr the expense account the charge used) with Reference Number ACC-JV-1 followed "
			"by sr-a",
		)
		self.assertEqual(
			(row["show"], row["to_move"], row["moved_to_2210"]), (matching.NEEDS_ACTION, 25.0, 0)
		)

	def test_an_over_correction_is_flagged(self):
		row = self.one({"ACC-JV-1": {"ACC-JV-900": 50.0}})
		self.assertEqual(
			row["action"],
			f"The {ACCOUNT} debit is $50.00 but the stock lines are $45.00 (corrected by ACC-JV-900): post and "
			"submit a correcting Journal Entry for $5.00 (Dr the expense account the charge used / Cr 2210) "
			"with Reference Number ACC-JV-1 followed by sr-a",
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
			"of them; post and submit a correcting Journal Entry for $45.00 (Dr the expense account the charge "
			"used / Cr 2210) with Reference Number ACC-JV-1. The S-D loop submits the draft",
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
			"reduce its own lines to $45.00, then save it, and reverse the correcting entries; post and submit "
			"a correcting Journal Entry for $10.00",
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
			f"Carries $45.00 on {ACCOUNT} but no recorded store run is paired or linked. If you moved it for "
			"a trip whose row shows no other charge, put that trip's run id in this entry's Reference Number; "
			"otherwise move it back to the expense. Then save it; the S-D loop submits it",
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
			"entry's Reference Number cannot be changed: move it back to the expense; post and submit a "
			"correcting Journal Entry for $45.00 (Dr the expense account it used / Cr 2210) with Reference "
			"Number ACC-JV-7. If you moved it for a trip, that trip's row then says how to move it again, linked",
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
			"linked: bring it back to $0.00; post and submit a correcting Journal Entry for $5.00 (Dr 2210 / "
			"Cr the expense account it used) with Reference Number ACC-JV-7",
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
		"""...and says so first (third review): the draft was told to "put that trip's run id in this
		entry's Reference Number", which Accounting believed it had done."""
		rows = report(self.late(), _trip(), [ref("ACC-JV-7", "sr-typo")], on_2210={JV7: 45.0})
		self.assertEqual(
			[(row["row_type"], row["show"]) for row in rows],
			[(matching.ROW_CHARGE, matching.NEEDS_ACTION), (matching.ROW_TRIP, matching.WAITING)],
		)
		self.assertTrue(
			rows[0]["action"].startswith(
				f"Its Reference Number lists sr-typo, which is no recorded store run. Carries $45.00 on {ACCOUNT}"
			),
			rows[0]["action"],
		)

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
		other, and doing it finishes both (no loop). The question comes FIRST (third review): the
		reduction used to lead, so following the first instruction emptied Needs action while sr-a
		stayed Waiting and the draft expensed $60.00 already in stock."""
		receipts = [
			receipt("2026-09-03", "sr-a", 60.0, total=107.0),
			receipt("2026-09-03", "sr-b", 40.0, total=107.0),
		]
		charges = [charge("2026-09-04", 107.0, "ACC-JV-1")]
		rows = {
			row["run_ref"]: row
			for row in report(charges, receipts, [ref("ACC-JV-1", "sr-b")], on_2210={JV1: 100.0})
		}
		# All three answers, the link's own mistake included (fourth review): it pays for both, for sr-a
		# and not sr-b, or for sr-b alone.
		self.assertEqual(
			rows["sr-b"]["action"],
			f"The {ACCOUNT} debit is $100.00 but the stock lines of the trip its Reference Number links "
			"(sr-b) are $40.00. If this draft also pays for sr-a, add sr-a to its Reference Number (it then "
			"carries exactly the stock lines of sr-b, sr-a together); if it pays for sr-a and not for sr-b, "
			"put sr-a in its Reference Number in place of sr-b and reduce its 2210 debit to $60.00 (the stock "
			"lines of sr-a); otherwise reduce it to $40.00. Then save it; the S-D loop submits it",
		)
		self.assertEqual(rows["sr-b"]["show"], matching.NEEDS_ACTION)
		self.assertEqual((rows["sr-a"]["charge"], rows["sr-a"]["show"]), (None, matching.WAITING))
		self.assertTrue(
			rows["sr-a"]["action"].startswith(
				"Its paired charge ACC-JV-1 now links only sr-b, by its Reference Number. If ACC-JV-1 pays "
				"for this trip as well as sr-b, add sr-a to that Reference Number and move this trip's $60.00 "
				"of stock lines too. If it pays for this trip and not for sr-b, take sr-b out of ACC-JV-1's "
				"Reference Number (edit it and save it), then put sr-a in its Reference Number, save it, and "
				"this trip's row then says what to move. If it does not pay for this trip, find its "
				"QuickBooks draft"
			),
			rows["sr-a"]["action"],
		)
		self.assertTrue(rows["sr-a"]["action"].endswith(ONLY_IF_NONE))
		done = report(charges, receipts, [ref("ACC-JV-1", "sr-b, sr-a")], on_2210={JV1: 100.0})
		self.assertEqual([row["show"] for row in done], [matching.DONE, matching.DONE])

	def test_a_link_overrides_an_automatic_pair(self):
		"""The trip's automatic charge goes back to unpaired: listed if it carries 2210 (it was moved
		for this trip before the link named another charge), and asked about under Waiting if it carries
		nothing -- the link may be the mistake, and nothing else would say so (fourth review)."""
		charges = [charge("2026-09-04", 48.26, "ACC-JV-1"), charge("2026-09-12", 50.0, "ACC-JV-7")]
		self.assertEqual(self.only(report(charges, _trip()))["charge"], "ACC-JV-1")
		references = [ref("ACC-JV-7", "sr-a", day="2026-09-12", amount=50.0)]
		rows = report(charges, _trip(), references, on_2210={JV7: 45.0})
		self.assertEqual(
			[(row["row_type"], row["charge"], row["show"]) for row in rows],
			[
				(matching.ROW_CHARGE, "ACC-JV-1", matching.WAITING),
				(matching.ROW_TRIP, "ACC-JV-7", matching.DONE),
			],
		)
		self.assertEqual(
			rows[0]["action"],
			f"It was paired with sr-a until ACC-JV-7 linked sr-a, and carries nothing on {ACCOUNT}. If this one "
			"is sr-a's charge, take sr-a out of the Reference Number that links the other one (edit a draft and "
			"save it, cancel a submitted correcting entry); if not, put its own trip's run id in its Reference "
			"Number, or not-store-run if it pays for no store run, and save it",
		)
		self.assertEqual(matching.summarize(rows)["unmatched_on_2210"], 0.0)
		# Answered "not": marked not-store-run, it leaves the list.
		marked = [*references, ref("ACC-JV-1", "not-store-run", day="2026-09-04")]
		self.assertEqual(
			[row["charge"] for row in report(charges, _trip(), marked, on_2210={JV7: 45.0})], ["ACC-JV-7"]
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
		# The trips shown carry the whole of what is still to move (third review): the row says
		# $75.00, so the summary does too. It said $45.00, the row's own stock lines, before.
		self.assertEqual(row["to_move"], 75.0)
		self.assertEqual(matching.summarize([row])["to_move"], 75.0)
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
		"""Names where each charge carries the run id, says which to keep is Accounting's call and that
		the dropped charge's 2210 goes back (third review: "keep each run id in one charge's Reference
		Number only" named neither)."""
		charges = [*self.late(), charge("2026-09-11", 48.26, "ACC-JV-8")]
		references = [ref("ACC-JV-7", "sr-a"), ref("ACC-JV-8", "sr-a", day="2026-09-11")]
		row = self.only(report(charges, _trip(), references, on_2210={JV7: 45.0}))
		self.assertEqual(
			(row["action"], row["show"]),
			(
				"Linked from more than one charge's Reference Number: sr-a by ACC-JV-7 (its own Reference "
				"Number, a draft) and ACC-JV-8 (its own Reference Number, a draft). Which charge is the trip's "
				"is Accounting's call. Take the run id out of the other one's Reference Number (a draft: edit "
				"it and save it; a submitted correcting entry: cancel it; a submitted charge's own Reference "
				"Number cannot be changed, so keep that one), and move the dropped charge's debit on "
				f"{ACCOUNT} back to the expense; its own row then says how",
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
			"Submitted with the goods on the expense: post and submit a correcting Journal Entry for $45.00 "
			f"(Dr {ACCOUNT} / Cr the expense account the charge used) with Reference Number ACC-JV-7 followed "
			"by sr-a",
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
					if row["row_type"] == matching.ROW_ENTRY:
						# A Reference Number listing sr-typo (named, whatever else it links), or a charge
						# in the lookback that pairs with nothing but carries 2210.
						self.assertTrue(
							"sr-typo" in row["action"] or "outside this range" in row["action"], row["action"]
						)
						continue
					if row["row_type"] == matching.ROW_OUTSIDE:
						self.assertIn(row["charge"], linked)
						self.assertEqual(row["show"], matching.NEEDS_ACTION)
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
# 2c. Third review: chains, the 2210 backstop, dead keys, not-store-run, and the texts
# ---------------------------------------------------------------------------


def je(name):
	return ("Journal Entry", name)


def stray(name, day="2026-09-12", docstatus=1, amount=30.0, source="ERPNext", reference=""):
	"""A Journal Entry as the backstop reads it (``_journal_2210``)."""
	return {
		"name": name,
		"reference": reference,
		"docstatus": docstatus,
		"day": day,
		"company": "Sapphire Fountains",
		"amount": amount,
		"source": source,
	}


def _two_runs():
	"""Two runs of one purchase: $60.00 and $40.00 of stock lines, one $107.00 receipt total each."""
	return [
		receipt("2026-09-03", "sr-a", 60.0, total=107.0),
		receipt("2026-09-03", "sr-b", 40.0, total=107.0),
	]


def _of(rows, kind):
	return [row for row in rows if row["row_type"] == kind]


class TestChains(unittest.TestCase):
	"""A Reference Number that names a correcting entry names that entry's charge (third review,
	finding 1: every entry with a Reference Number was in the charge universe, so a reversal naming
	the first correction was filed under it and never counted)."""

	charges = (charge("2026-09-04", 48.26, "ACC-JV-1"),)
	FIX = ref("ACC-JV-900", "ACC-JV-1", day="2026-09-05", docstatus=1, source="ERPNext")

	def only(self, references, on_2210, charges=None):
		rows = report(list(charges or self.charges), _trip(), references, on_2210=on_2210)
		self.assertEqual(len(rows), 1, rows)
		return rows[0]

	def test_p7_a_reversal_naming_the_first_correction_counts_for_the_charge(self):
		on = {JV1: 45.0, je("ACC-JV-900"): 45.0}
		before = self.only([self.FIX], on)
		self.assertIn("$45.00 too many: reverse $45.00 of them", before["action"])
		reversal = ref("ACC-JV-901", "acc-jv-900", day="2026-09-06", docstatus=1, source="ERPNext")
		after = self.only([self.FIX, reversal], {**on, je("ACC-JV-901"): -45.0})
		self.assertEqual(
			(after["show"], after["action"]),
			(
				matching.DONE,
				f"Goods debit on {ACCOUNT} (corrected by ACC-JV-900, ACC-JV-901): nothing to change; the S-D "
				"loop submits it",
			),
		)

	def test_p7b_one_reversal_too_many_is_not_done(self):
		"""Then ACC-JV-902, naming the charge, takes $45.00 more off: 2210 nets $0.00 against $45.00.
		It read Done; now the correcting entries are named and one more puts it back."""
		references = [
			self.FIX,
			ref("ACC-JV-901", "ACC-JV-900", day="2026-09-06", docstatus=1, source="ERPNext"),
			ref("ACC-JV-902", "ACC-JV-1", day="2026-09-07", docstatus=1, source="ERPNext"),
		]
		on = {JV1: 45.0, je("ACC-JV-900"): 45.0, je("ACC-JV-901"): -45.0, je("ACC-JV-902"): -45.0}
		row = self.only(references, on)
		self.assertEqual(
			row["action"],
			f"The {ACCOUNT} debit is $0.00 but the stock lines are $45.00: the draft's own lines carry $45.00 "
			"and its correcting entries (ACC-JV-900, ACC-JV-901, ACC-JV-902) take $45.00 off, $45.00 too "
			"much: post and submit a correcting Journal Entry for $45.00 (Dr 2210 / Cr the expense account "
			"the charge used) with Reference Number ACC-JV-1. The S-D loop submits the draft",
		)
		self.assertEqual(
			(row["show"], row["to_move"], row["moved_to_2210"]), (matching.NEEDS_ACTION, 45.0, 0)
		)

	def test_listing_the_correction_and_the_charge_agree(self):
		""" "ACC-JV-900 ACC-JV-1" names one charge twice over. The first token used to win, and the entry
		was filed under ACC-JV-900."""
		reversal = ref("ACC-JV-901", "ACC-JV-900 ACC-JV-1", day="2026-09-06", docstatus=1, source="ERPNext")
		row = self.only([self.FIX, reversal], {JV1: 45.0, je("ACC-JV-900"): 45.0, je("ACC-JV-901"): -45.0})
		self.assertEqual(row["show"], matching.DONE)
		self.assertEqual(
			matching.resolve_links([self.FIX, reversal], list(self.charges), _trip())[1],
			{"ACC-JV-1": ["ACC-JV-900", "ACC-JV-901"]},
		)

	def test_two_charges_in_one_reference_number(self):
		"""Tokens that lead to two different charges: the entry counts for neither and says why."""
		charges = [*self.charges, charge("2026-09-20", 30.0, "ACC-JV-2")]
		both = ref("ACC-JV-900", "ACC-JV-1, ACC-JV-2", docstatus=1, source="ERPNext")
		rows = report(charges, _trip(), [both], on_2210={je("ACC-JV-900"): 10.0})
		trip, entry = _of(rows, matching.ROW_TRIP)[0], _of(rows, matching.ROW_ENTRY)[0]
		self.assertEqual(trip["on_2210"], 0.0, "counted for neither charge")
		self.assertEqual(
			entry["action"],
			"Its Reference Number points to both ACC-JV-1 and ACC-JV-2, so the report cannot tell which charge "
			"it adjusts. It is submitted, so its Reference Number cannot be changed: cancel it and amend it "
			f"with the right one. Until then its $10.00 on {ACCOUNT} is not accounted for.",
		)
		self.assertEqual(
			(entry["charge"], entry["match_basis"], entry["show"], entry["on_2210"]),
			("ACC-JV-900", matching.REFERENCE_TO_CHECK, matching.NEEDS_ACTION, 10.0),
		)
		self.assertEqual(matching.summarize(rows)["not_accounted"], 10.0)

	def test_a_loop_resolves_to_nothing(self):
		references = [
			ref("ACC-JV-900", "ACC-JV-901", docstatus=1, source="ERPNext"),
			ref("ACC-JV-901", "ACC-JV-900", day="2026-09-11", docstatus=1, source="ERPNext"),
		]
		resolved = matching.resolve_references(references, [], [])
		self.assertEqual((resolved["links"], resolved["corrections"]), ({}, {}))
		self.assertEqual(
			resolved["problems"],
			{"ACC-JV-900": [("unresolved", ["ACC-JV-901"])], "ACC-JV-901": [("unresolved", ["ACC-JV-900"])]},
		)
		rows = report([], [], references, on_2210={je("ACC-JV-900"): 5.0, je("ACC-JV-901"): -5.0})
		self.assertEqual([row["charge"] for row in rows], ["ACC-JV-900", "ACC-JV-901"])
		self.assertTrue(all("which does not lead to one card charge" in row["action"] for row in rows))

	def test_a_chain_through_an_entry_read_by_name(self):
		"""The first correction, dated before the lookback, is looked up by name, and its own Reference
		Number carries the chain on to the charge."""
		first = ref("ACC-JV-800", "ACC-JV-1", day="2026-08-01", docstatus=1, source="ERPNext")
		second = [ref("ACC-JV-900", "ACC-JV-800", docstatus=1, source="ERPNext")]
		self.assertEqual(matching.unresolved_tokens(second, list(self.charges), _trip()), ["acc-jv-800"])
		self.assertEqual(
			matching.resolve_links(second, list(self.charges), _trip(), named=[first])[1],
			{"ACC-JV-1": ["ACC-JV-800", "ACC-JV-900"]},
		)

	def test_a_card_charge_naming_another_charge_is_listed(self):
		charges = [*self.charges, charge("2026-09-20", 10.0, "ACC-JV-2")]
		rows = report(charges, _trip(), [ref("ACC-JV-2", "ACC-JV-1", day="2026-09-20")])
		entry = _of(rows, matching.ROW_ENTRY)[0]
		self.assertEqual(
			entry["action"],
			"It is a card charge itself, and its Reference Number names ACC-JV-1: a charge's Reference Number "
			"lists only the run ids of the trips it pays for. Correct its Reference Number and save it.",
		)
		self.assertEqual((entry["charge"], entry["on_2210"]), ("ACC-JV-2", 0.0))


class TestBackstop(unittest.TestCase):
	"""Every Journal Entry line on 2210 dated from the lookback to To Date is accounted for, or gets a
	row of its own, and 2210 Not Accounted For totals those (third review, the conservation backstop)."""

	def test_p1b_a_dead_key_on_an_entry_nothing_else_reads(self):
		"""A draft under a vendor that is not a store-run vendor, carrying $45.00, typo'd "sr-a-x". It
		got no row at all, and On 2210 With No Store Run stayed $0.00, so the check passed."""
		rows = report([], _trip(), [ref("ACC-JV-50", "sr-a-x")], on_2210={je("ACC-JV-50"): 45.0})
		entry = _of(rows, matching.ROW_ENTRY)[0]
		self.assertEqual(
			entry["action"],
			"Its Reference Number lists sr-a-x, which is no recorded store run. Correct its Reference Number "
			f"and save it. Until then its $45.00 on {ACCOUNT} is not accounted for.",
		)
		self.assertEqual(
			(entry["charge_status"], entry["charge_source"]), ("Draft", matching.SOURCE_QBO_DRAFT)
		)
		summary = matching.summarize(rows)
		self.assertEqual((summary["unmatched_on_2210"], summary["not_accounted"]), (0.0, 45.0))
		self.assertEqual(_of(rows, matching.ROW_TRIP)[0]["show"], matching.WAITING)

	def test_an_entry_with_no_reference_number(self):
		"""An ERPNext entry may be an adjustment that has nothing to do with store runs; a QuickBooks
		entry is a card charge, which carries nothing on 2210 unless it pays for a store run, so it is
		never told to mark itself not-store-run while it still carries money there (fourth review)."""
		erpnext = (
			f"Carries $30.00 on {ACCOUNT} that the report cannot tie to any store run or card charge. If it "
			"adjusts a charge, its Reference Number should name that charge (or the trip's run id); if it has "
			"nothing to do with store runs, add not-store-run to its Reference Number. "
		)
		card = (
			f"Carries $30.00 on {ACCOUNT} that the report cannot tie to any store run or card charge, and a card "
			"charge that pays for no store run carries nothing there. "
		)
		for docstatus, source, action in (
			(0, "ERPNext", erpnext + "Edit its Reference Number and save it."),
			(
				1,
				"ERPNext",
				erpnext
				+ "It is submitted, so its Reference Number cannot be changed: cancel it and amend it "
				"with the right Reference Number.",
			),
			(
				0,
				"QuickBooks",
				card
				+ "If you moved it for a trip whose row shows no other charge, put that trip's run id in "
				"its Reference Number; otherwise move it back to the expense. Then save it; the S-D loop "
				"submits it",
			),
			(
				1,
				"QuickBooks",
				card + "It is a submitted QuickBooks entry, which is never cancelled: move it back to the "
				"expense; post and submit a correcting Journal Entry for $30.00 (Dr the expense account it "
				"used / Cr 2210) with Reference Number ACC-JV-60. If you moved it for a trip, that trip's row "
				"then says how to move it again, linked",
			),
		):
			with self.subTest(docstatus=docstatus, source=source):
				entry = stray("ACC-JV-60", docstatus=docstatus, source=source)
				rows = report([], [], on_2210={je("ACC-JV-60"): 30.0}, journal=[entry])
				self.assertEqual(
					[(row["row_type"], row["match_basis"], row["on_2210"]) for row in rows],
					[(matching.ROW_ENTRY, matching.NOT_TIED, 30.0)],
				)
				self.assertEqual(rows[0]["action"], action)
				self.assertEqual(matching.summarize(rows)["not_accounted"], 30.0)
				if source == "QuickBooks":
					self.assertNotIn(matching.NOT_STORE_RUN, rows[0]["action"])

	def test_not_store_run_takes_an_entry_out(self):
		entry = stray("ACC-JV-60", reference=" Not-Store-Run ")
		rows = report([], [], [entry], on_2210={je("ACC-JV-60"): 30.0}, journal=[entry])
		self.assertEqual(rows, [])
		self.assertEqual(matching.resolve_references([entry], [], [])["excluded"], {"ACC-JV-60"})

	def test_an_entry_naming_one_marked_not_store_run(self):
		"""It adjusts no store run's charge, so its 2210 amount is not accounted for; marking it
		not-store-run too takes both out."""
		marked = stray("ACC-JV-60", reference="not-store-run")
		naming = stray("ACC-JV-61", day="2026-09-13", reference="ACC-JV-60")
		on = {je("ACC-JV-60"): 30.0, je("ACC-JV-61"): -30.0}
		rows = report([], [], [marked, naming], on_2210=on, journal=[marked, naming])
		self.assertEqual([row["charge"] for row in rows], ["ACC-JV-61"])
		self.assertEqual(
			rows[0]["action"],
			"Its Reference Number names ACC-JV-60, marked not-store-run: add not-store-run to this one's too if "
			"it has nothing to do with store runs, or name the charge it adjusts. It is submitted, so its "
			"Reference Number cannot be changed: cancel it and amend it with the right one. Until then the "
			f"$30.00 it takes off {ACCOUNT} is not accounted for.",
		)
		both = dict(naming, reference="not-store-run ACC-JV-60")
		self.assertEqual(report([], [], [marked, both], on_2210=on, journal=[marked, both]), [])

	def test_not_store_run_on_a_card_charge_stops_its_pairing(self):
		"""In the report only: the KPI still pairs and counts it."""
		charges = [charge("2026-09-04", 48.26, "ACC-JV-1")]
		rows = report(charges, _trip(), [ref("ACC-JV-1", "not-store-run")])
		self.assertEqual(
			[(row["row_type"], row["charge"], row["show"]) for row in rows],
			[(matching.ROW_TRIP, None, matching.WAITING)],
		)
		self.assertEqual(matching.summarize(rows)["not_accounted"], 0.0)
		kpi, _bills = metrics.pair_store_runs(charges, _trip(), _key)
		self.assertEqual(kpi[0]["charge"]["row"]["voucher_no"], "ACC-JV-1")

	def test_m2c_a_card_charge_marked_not_store_run_still_on_2210(self):
		"""Fourth review, item 2: marking a QuickBooks card charge not-store-run took its 2210 debit
		out of the backstop, so the report read clean with the money still on 2210 and no receipt to
		clear it. Now it stays listed until its 2210 is back to $0.00, draft or submitted."""
		charges = [charge("2026-09-04", 48.26, "ACC-JV-1")]
		for docstatus, tail in (
			(
				0,
				": move it back to the expense, then save it; the S-D loop submits it. If you moved it for a "
				"trip, put that trip's run id in its Reference Number in place of not-store-run",
			),
			(
				1,
				". It is a submitted QuickBooks entry, which is never cancelled: move it back to the expense; "
				"post and submit a correcting Journal Entry for $45.00 (Dr the expense account it used / Cr "
				"2210) with Reference Number ACC-JV-1 followed by not-store-run",
			),
		):
			with self.subTest(docstatus=docstatus):
				charges[0] = charge("2026-09-04", 48.26, "ACC-JV-1", docstatus=docstatus)
				references = [ref("ACC-JV-1", "not-store-run", day="2026-09-04", docstatus=docstatus)]
				rows = report(charges, _trip(), references, on_2210={JV1: 45.0})
				entry = _of(rows, matching.ROW_ENTRY)[0]
				self.assertEqual(
					entry["action"],
					f"Its Reference Number says not-store-run, but it still carries $45.00 on {ACCOUNT}, and a "
					f"card charge that pays for no store run carries nothing there{tail}",
				)
				self.assertEqual((entry["show"], entry["on_2210"]), (matching.NEEDS_ACTION, 45.0))
				self.assertEqual(matching.summarize(rows)["not_accounted"], 45.0)
				self.assertIsNone(_of(rows, matching.ROW_TRIP)[0]["charge"], "still out of the pairing")
		# Doing what it says: the submitted one's correcting entry, marked not-store-run too, nets it.
		fix = ref("ACC-JV-900", "ACC-JV-1 not-store-run", day="2026-09-06", docstatus=1, source="ERPNext")
		references = [ref("ACC-JV-1", "not-store-run", day="2026-09-04", docstatus=1), fix]
		rows = report(charges, _trip(), references, on_2210={JV1: 45.0, je("ACC-JV-900"): -45.0})
		self.assertEqual([row["row_type"] for row in rows], [matching.ROW_TRIP])
		self.assertEqual(matching.summarize(rows)["not_accounted"], 0.0)
		# Half of it: the unit's figure is what is left, on the marked entry's own row.
		rows = report(charges, _trip(), references, on_2210={JV1: 45.0, je("ACC-JV-900"): -30.0})
		entry = _of(rows, matching.ROW_ENTRY)[0]
		self.assertEqual((entry["charge"], entry["on_2210"]), ("ACC-JV-1", 15.0))
		self.assertIn("(with ACC-JV-900, marked not-store-run, which names it)", entry["action"])
		self.assertEqual(matching.summarize(rows)["not_accounted"], 15.0)
		# And the draft brought back to $0.00 leaves the list.
		draft = [charge("2026-09-04", 48.26, "ACC-JV-1")]
		rows = report(
			draft, _trip(), [ref("ACC-JV-1", "not-store-run", day="2026-09-04")], on_2210={JV1: 0.0}
		)
		self.assertEqual([row["row_type"] for row in rows], [matching.ROW_TRIP])

	def test_an_erpnext_entry_marked_not_store_run_keeps_its_2210(self):
		"""A 2210 adjustment that has nothing to do with store runs (a PO receipt's, say) is legitimate:
		the rule above is for card charges only."""
		entry = stray("ACC-JV-60", reference="not-store-run", source="ERPNext")
		rows = report([], [], [entry], on_2210={je("ACC-JV-60"): 30.0}, journal=[entry])
		self.assertEqual(rows, [])

	def test_the_billed_trip_it_frees(self):
		"""Finding 6's stuck case: a billed trip that happened to pair with an unrelated charge could
		never leave Needs action. Marking the charge not-store-run ends it -- once nothing of it is on
		2210 (fourth review)."""
		receipts = [receipt("2026-09-03", total=48.26, name="PR-1")]
		charges = [charge("2026-09-04", 48.26, "ACC-JV-1")]
		billed = {"PR-1": "ACC-PINV-7"}
		stuck = report(charges, receipts, billed=billed)[0]
		self.assertIn(
			f"if it pays for no store run, move any debit it has on {ACCOUNT} back to the expense, put "
			f"{matching.NOT_STORE_RUN} in its Reference Number and save it",
			stuck["action"],
		)
		freed = report(charges, receipts, [ref("ACC-JV-1", "not-store-run")], billed=billed)
		self.assertEqual(
			[(row["action"], row["show"]) for row in freed],
			[("Billed from the receipts (ACC-PINV-7)", matching.DONE)],
		)

	def test_not_store_run_beside_a_key_is_a_conflict(self):
		charges = [charge("2026-09-04", 48.26, "ACC-JV-1")]
		rows = report(charges, _trip(), [ref("ACC-JV-1", "not-store-run, SR-A")], on_2210={JV1: 45.0})
		trip, entry = _of(rows, matching.ROW_TRIP)[0], _of(rows, matching.ROW_ENTRY)[0]
		self.assertEqual(trip["charge"], "ACC-JV-1", "not excluded: it pairs as the KPI pairs it")
		self.assertEqual(
			entry["action"],
			"Its Reference Number says not-store-run and also lists SR-A. Correct its Reference Number and "
			"save it.",
		)

	def test_a_charge_in_the_lookback_that_pairs_with_nothing(self):
		"""Dated before From Date, so this range lists no row for it; its 2210 is not accounted for, and
		widening the range gives it one."""
		charges = [charge("2026-08-28", 48.26, "ACC-JV-7")]
		rows = report(charges, [], on_2210={JV7: 45.0})
		self.assertEqual(
			rows[0]["action"],
			"It is a charge dated 2026-08-28, outside this range, that no recorded store run is paired or "
			"linked with: widen the range to include 2026-08-28, and its row there says what to do. Until "
			f"then its $45.00 on {ACCOUNT} is not accounted for.",
		)
		wider = report(charges, [], on_2210={JV7: 45.0}, from_date="2026-08-01")
		self.assertEqual([row["row_type"] for row in wider], [matching.ROW_CHARGE])

	def test_p11_a_draft_correcting_entry(self):
		"""Ignored silently before: the S-D loop would have submitted it beside a later one."""
		charges = [charge("2026-09-04", 48.26, "ACC-JV-1", docstatus=1)]
		draft = ref("ACC-JV-900", "ACC-JV-1", day="2026-09-05", docstatus=0, source="ERPNext")
		rows = report(charges, _trip(), [draft], on_2210={je("ACC-JV-900"): 45.0})
		trip, entry = _of(rows, matching.ROW_TRIP)[0], _of(rows, matching.ROW_ENTRY)[0]
		self.assertTrue(trip["action"].startswith("Submitted with the goods on the expense: post and submit"))
		self.assertEqual(
			entry["action"],
			"It is a draft correcting entry for ACC-JV-1: submit it (a draft correcting entry does not count "
			"until submitted), or delete it if the row of sr-a (Matched Charge ACC-JV-1) does not ask for it. "
			f"Until then its $45.00 on {ACCOUNT} is not accounted for.",
		)
		self.assertEqual(matching.summarize(rows)["not_accounted"], 45.0)
		submitted = dict(draft, docstatus=1)
		done = report(charges, _trip(), [submitted], on_2210={je("ACC-JV-900"): 45.0})
		self.assertEqual(
			[(row["row_type"], row["show"]) for row in done], [(matching.ROW_TRIP, matching.DONE)]
		)
		self.assertEqual(matching.summarize(done)["not_accounted"], 0.0)

	def test_the_2210_conservation_on_generated_histories(self):
		"""Every 2210 amount read is attributed to exactly one charge's figure, left out by not-store-run,
		or not accounted for; the attributed ones are exactly the figures the rows use; and every one not
		accounted for has a row of its own. Random trips, charges, run ids, dead keys, correction chains,
		not-store-run, junk and stray entries."""
		rng = random.Random(20261023)
		start = date(2026, 9, 1)
		for case in range(500):
			receipts = [
				receipt(
					(start + timedelta(days=rng.randint(-12, 35))).isoformat(),
					f"sr-{index}",
					rng.choice((10.0, 20.0, 45.0)),
					total=rng.choice((0.0, 21.45, 48.26)),
					name=f"MAT-PRE-{index}",
				)
				for index in range(rng.randint(0, 6))
			]
			charges = [
				charge(
					(start + timedelta(days=rng.randint(-8, 35))).isoformat(),
					rng.choice((10.7, 21.45, 48.26)),
					f"JV-{index}",
					docstatus=rng.choice((0, 1)),
				)
				for index in range(rng.randint(0, 6))
			]
			entries = [f"JV-{index}" for index in range(len(charges))]
			references = []
			strays = []
			for index in range(rng.randint(0, 6)):
				name = f"JV-X{index}"
				tokens = rng.sample(
					[row["run"] for row in receipts]
					+ [row["receipt"] for row in receipts]
					+ entries
					+ ["sr-typo", "4471", matching.NOT_STORE_RUN],
					rng.randint(0, 2),
				)
				entry = stray(
					name,
					day=(start + timedelta(days=rng.randint(-8, 35))).isoformat(),
					docstatus=rng.choice((0, 1)),
					reference=" ".join(tokens),
				)
				(references if tokens else strays).append(entry)
				entries.append(name)
			for row in charges:
				if rng.random() < 0.4:
					tokens = rng.sample([r["run"] for r in receipts] + ["sr-typo", matching.NOT_STORE_RUN], 1)
					references.append(
						ref(row["voucher_no"], tokens[0], day=row["day"], docstatus=row["docstatus"])
					)
			on_2210 = {je(name): rng.choice((0.0, 10.0, 45.0, -10.0)) for name in entries}
			known = {row["voucher_no"]: row for row in charges}
			known.update({entry["name"]: entry for entry in references + strays})
			journal = [
				stray(
					name,
					day=known[name]["day"],
					docstatus=known[name].get("docstatus", 1),
					source=known[name].get("source", "ERPNext"),
					reference=known[name].get("reference") or "",
				)
				for name in entries
				if on_2210[je(name)] and "2026-08-25" <= known[name]["day"] <= "2026-09-30"
			]
			rows, window, resolution, journal, on_2210, corrections = flow(
				charges, receipts, references, on_2210=on_2210, journal=journal
			)
			ledger = matching.attribute_2210(window, resolution, on_2210, corrections, journal)
			with self.subTest(case=case):
				considered = {entry["name"] for entry in journal}
				for group in window["groups"]:
					names = [
						group["charge"]["row"]["voucher_no"],
						*corrections.get(group["charge"]["row"]["voucher_no"], {}),
					]
					if group["accounted"]:
						considered.update(name for name in names if je(name) in on_2210)
				total = sum(on_2210[je(name)] for name in considered)
				buckets = [ledger["attributed"], ledger["unaccounted"], ledger["excluded"]]
				self.assertEqual(sorted(name for bucket in buckets for name in bucket), sorted(considered))
				self.assertAlmostEqual(sum(sum(bucket.values()) for bucket in buckets), total, places=2)
				if not any(matching.NOT_STORE_RUN in (entry.get("reference") or "") for entry in references):
					# The brief's form: attributed + not accounted for == every 2210 line read.
					self.assertAlmostEqual(
						sum(ledger["attributed"].values()) + sum(ledger["unaccounted"].values()),
						total,
						places=2,
					)
				# The attributed amounts are the accounted groups' own figures, entry for entry.
				figures = 0.0
				for group in window["groups"]:
					if group["accounted"]:
						voucher = group["charge"]["row"]["voucher_no"]
						figures += on_2210.get(je(voucher), 0.0) + sum(corrections.get(voucher, {}).values())
				self.assertAlmostEqual(figures, sum(ledger["attributed"].values()), places=2)
				# Each amount not accounted for is on a row of its own, and the summary is their total; a
				# QuickBooks entry marked not-store-run that still carries 2210 shows, on its own row, its
				# amount and that of the entries marked not-store-run that name it (fourth review).
				listed = {row["charge"]: row["on_2210"] for row in _of(rows, matching.ROW_ENTRY)}
				expected = {}
				for name, amount in ledger["unaccounted"].items():
					owner = ledger["marked"].get(name, name)
					expected[owner] = round(expected.get(owner, 0.0) + amount, 2)
				for name, amount in expected.items():
					if amount:
						self.assertEqual(listed.get(name), amount, name)
				for owner in set(ledger["marked"].values()):
					self.assertEqual(resolution["entries"][owner]["source"], "QuickBooks", owner)
				self.assertAlmostEqual(
					matching.summarize(rows)["not_accounted"], sum(ledger["unaccounted"].values()), places=2
				)
				# A shown charge's trip rows add up to its figure.
				for group in window["groups"]:
					shown = [
						row
						for row in rows
						if row["row_type"] == matching.ROW_TRIP
						and row["charge"] == group["charge"]["row"]["voucher_no"]
					]
					if shown and not group["contested"]:
						voucher = group["charge"]["row"]["voucher_no"]
						self.assertAlmostEqual(
							sum(row["on_2210"] for row in shown),
							on_2210.get(je(voucher), 0.0) + sum(corrections.get(voucher, {}).values()),
							places=2,
						)


class TestDeadKeys(unittest.TestCase):
	"""A key-shaped token that names no recorded store run is named on a row (third review, finding
	4), and any receipt's own name is a key for its trip."""

	def test_p1_a_reader_draft_with_a_dead_key_says_so_first(self):
		charges = [charge("2026-09-10", 48.26, "ACC-JV-7")]
		rows = report(charges, _trip(), [ref("ACC-JV-7", "sr-ax")], on_2210={JV7: 45.0})
		self.assertEqual(
			rows[0]["action"],
			f"Its Reference Number lists sr-ax, which is no recorded store run. Carries $45.00 on {ACCOUNT} but "
			"no recorded store run is paired or linked. If you moved it for a trip whose row shows no other "
			"charge, put that trip's run id in this entry's Reference Number; otherwise move it back to the "
			"expense. Then save it; the S-D loop submits it",
		)
		self.assertEqual(len(_of(rows, matching.ROW_ENTRY)), 0, "one row per entry: the charge's")

	def test_a_dead_key_on_a_paired_charge(self):
		rows = report(
			[charge("2026-09-04", 48.26, "ACC-JV-1")], _trip(), [ref("ACC-JV-1", "MAT-PRE-9, sr-b")]
		)
		entry = _of(rows, matching.ROW_ENTRY)[0]
		self.assertTrue(
			entry["action"].startswith(
				"Its Reference Number lists MAT-PRE-9 and sr-b, which are no recorded store runs."
			),
			entry["action"],
		)
		self.assertEqual((entry["on_2210"], entry["show"]), (0.0, matching.NEEDS_ACTION))

	def test_the_first_receipt_s_name_is_a_key_when_the_trip_has_a_run_id(self):
		receipts = [receipt("2026-09-03", "sr-a", total=48.26, name="MAT-PRE-2026-00040")]
		rows = report(self.late(), receipts, [ref("ACC-JV-7", "mat-pre-2026-00040")], on_2210={JV7: 45.0})
		self.assertEqual(
			[(row["charge"], row["match_basis"], row["show"]) for row in rows],
			[("ACC-JV-7", "Linked by Reference Number", matching.DONE)],
		)
		self.assertEqual(matching.trip_keys({"receipts": receipts}), ["sr-a", "mat-pre-2026-00040"])

	def late(self):
		return [charge("2026-09-10", 48.26, "ACC-JV-7")]

	def test_a_receipt_read_by_name_joins_its_run(self):
		"""The lookup by a receipt's name brings its whole run; a receipt the reader read, or of a run
		it read, belongs to the reader's trip rather than a second copy of it."""
		read = [receipt("2026-09-03", "sr-a", 20.0, total=48.26, name="PR-1")]
		far = [
			receipt("2026-09-03", "sr-a", 20.0, total=48.26, name="PR-1"),
			receipt("2026-08-01", "sr-a", 25.0, total=48.26, name="PR-0"),
		]
		rows = report(self.late(), read, [ref("ACC-JV-7", "PR-0")], far_receipts=far, on_2210={JV7: 20.0})
		self.assertEqual(
			[(row["run_ref"], row["charge"], row["stock_amount"], row["show"]) for row in rows],
			[("sr-a", "ACC-JV-7", 20.0, matching.DONE)],
		)

	def test_the_key_shapes(self):
		rules = (APP / "inventory_enhancements" / "stock_scan_rules.py").read_text(encoding="utf-8")
		self.assertIn(f'RUN_REF_PREFIX = "{matching.RUN_PREFIX}"', rules)
		for token, shaped in (
			("sr-abc", True),
			("mat-pre-2026-00038", True),
			("sr-", False),
			("4471", False),
		):
			with self.subTest(token=token):
				self.assertEqual(matching.is_key_shaped(token), shaped)
		self.assertEqual(
			matching.unresolved_tokens([ref("X", "not-store-run MAT-PRE-1 sr-a")], [], _trip()), ["mat-pre-1"]
		)


class TestDisplacedAndContested(unittest.TestCase):
	"""The charge a link displaced says so; the no-store-run text asks whether the trip already shows
	another charge; a trip two charges link names who carries each key (third review, finding 3)."""

	def test_p2_the_displaced_charge_names_the_link(self):
		receipts = [receipt("2026-09-01", "sr-1", 100.0, total=107.0)]
		charges = [charge("2026-09-01", 107.0, "ACC-JV-1"), charge("2026-09-02", 107.0, "ACC-JV-2")]
		for docstatus, how in (
			(0, ", then save it; the S-D loop submits it"),
			(
				1,
				"; post and submit a correcting Journal Entry for $100.00 (Dr the expense account it used / Cr "
				"2210) with Reference Number ACC-JV-1",
			),
		):
			with self.subTest(docstatus=docstatus):
				charges[0] = charge("2026-09-01", 107.0, "ACC-JV-1", docstatus=docstatus)
				rows = report(
					charges, receipts, [ref("ACC-JV-2", "sr-1", day="2026-09-02")], on_2210={JV1: 100.0}
				)
				row = _of(rows, matching.ROW_CHARGE)[0]
				# Both answers (fourth review, item 6): the link may be the mistake.
				self.assertEqual(
					row["action"],
					"It was paired with sr-1 until ACC-JV-2 linked sr-1: keep one charge for sr-1, which sr-1's "
					"row shows as its Matched Charge now. If this one is sr-1's charge, take sr-1 out of the "
					"Reference Number that links the other one (edit a draft and save it, cancel a submitted "
					f"correcting entry); if not, move this one's $100.00 on {ACCOUNT} back to the expense{how}",
				)

	def test_a_link_carried_by_a_correcting_entry_is_named_as_such(self):
		receipts = [receipt("2026-09-01", "sr-1", 100.0, total=107.0)]
		charges = [
			charge("2026-09-01", 107.0, "ACC-JV-1"),
			charge("2026-09-09", 107.0, "ACC-JV-2", docstatus=1),
		]
		fix = ref("ACC-JV-900", "ACC-JV-2 sr-1", docstatus=1, source="ERPNext")
		rows = report(charges, receipts, [fix], on_2210={JV1: 100.0, je("ACC-JV-900"): 100.0})
		self.assertTrue(
			_of(rows, matching.ROW_CHARGE)[0]["action"].startswith(
				"It was paired with sr-1 until ACC-JV-900 linked sr-1 to ACC-JV-2: keep one charge"
			)
		)

	def test_p14_contested_names_the_correcting_entry_that_carries_the_key(self):
		charges = [charge("2026-09-09", 48.26, "ACC-JV-7", docstatus=1)]
		references = [
			ref("ACC-JV-900", "ACC-JV-7 sr-a", day="2026-09-12", docstatus=1, source="ERPNext"),
			ref("ACC-JV-8", "sr-a", day="2026-09-04"),
		]
		rows = report(charges, _trip(), references, on_2210={je("ACC-JV-900"): 45.0, je("ACC-JV-8"): 45.0})
		self.assertEqual(len(rows), 1, rows)
		self.assertTrue(
			rows[0]["action"].startswith(
				"Linked from more than one charge's Reference Number: sr-a by ACC-JV-7 (the Reference Number of "
				"ACC-JV-900, submitted) and ACC-JV-8 (its own Reference Number, a draft). Which charge is the "
				"trip's is Accounting's call."
			),
			rows[0]["action"],
		)
		self.assertIn(f"move the dropped charge's debit on {ACCOUNT} back to the expense", rows[0]["action"])

	def test_p3_a_better_match_arriving_later(self):
		"""The draft moved for the trip loses it to an exact-total charge that synced later; its row no
		longer invites a second link to a trip whose row already shows a charge."""
		receipts = [receipt("2026-09-01", "sr-1", 100.0, total=107.0)]
		charges = [charge("2026-09-02", 105.0, "ACC-JV-1"), charge("2026-09-03", 107.0, "ACC-JV-0")]
		rows = report(charges, receipts, on_2210={JV1: 100.0})
		self.assertIn(
			"If you moved it for a trip whose row shows no other charge, put that trip's run id",
			_of(rows, matching.ROW_CHARGE)[0]["action"],
		)
		self.assertEqual(_of(rows, matching.ROW_TRIP)[0]["charge"], "ACC-JV-0")

	def test_a_displaced_purchase_invoice_is_not_told_to_move_its_lines(self):
		receipts = [receipt("2026-09-01", "sr-1", 100.0, total=107.0)]
		pi = charge(
			"2026-09-01", 107.0, "ACC-PINV-1", docstatus=1, source="ERPNext", voucher_type="Purchase Invoice"
		)
		rows = report(
			[pi],
			receipts,
			[ref("ACC-JV-2", "sr-1", day="2026-09-02")],
			on_2210={("Purchase Invoice", "ACC-PINV-1"): 100.0},
		)
		self.assertEqual(
			_of(rows, matching.ROW_CHARGE)[0]["action"],
			"It was paired with sr-1 until ACC-JV-2 linked sr-1: keep one charge for sr-1. A Purchase Invoice "
			f"books its stock lines to {ACCOUNT} itself, so do not move them: if this invoice is sr-1's charge, "
			"take sr-1 out of the Reference Number that links the other one; if not, it waits for its own receipt",
		)
		self.assertEqual(_of(rows, matching.ROW_CHARGE)[0]["show"], matching.WAITING)


class TestTwoRunsOfOnePurchase(unittest.TestCase):
	"""One draft pays for two runs (third review, finding 2)."""

	charges = (charge("2026-09-04", 107.0, "ACC-JV-1"),)

	def rows(self, reference, on):
		references = [ref("ACC-JV-1", reference, day="2026-09-04", amount=107.0)] if reference else []
		return {
			row["run_ref"]: row
			for row in report(list(self.charges), _two_runs(), references, on_2210={JV1: on})
		}

	def test_p12a_the_waiting_run_is_told_to_list_both(self):
		rows = self.rows("", 60.0)
		self.assertEqual((rows["sr-a"]["show"], rows["sr-b"]["show"]), (matching.DONE, matching.WAITING))
		self.assertEqual(rows["sr-b"]["action"], waiting_text("$40.00", "sr-b"))
		self.assertIn(
			"If that charge is already another trip's in this list and it also pays for that trip, list both "
			"trips' run ids",
			rows["sr-b"]["action"],
		)

	def test_p12d_short_asks_first_and_gives_both_amounts(self):
		rows = self.rows("sr-b", 0.0)
		self.assertEqual(
			rows["sr-b"]["action"],
			f"The {ACCOUNT} debit is $0.00 but the stock lines of the trip its Reference Number links (sr-b) "
			"are $40.00. If this draft also pays for sr-a, add sr-a to its Reference Number and move $100.00 of "
			"the goods debit to 2210 (the stock lines of sr-b, sr-a together); if it pays for sr-a and not for "
			"sr-b, put sr-a in its Reference Number in place of sr-b and move $60.00 of the goods debit to "
			"2210 (the stock lines of sr-a); otherwise move $40.00 of the goods debit to 2210. Then save it; "
			"the S-D loop submits it",
		)
		self.assertEqual(rows["sr-b"]["to_move"], 40.0)

	def test_p12c_the_other_run_stays_waiting_until_it_is_settled(self):
		rows = self.rows("sr-b", 40.0)
		self.assertEqual((rows["sr-b"]["show"], rows["sr-a"]["show"]), (matching.DONE, matching.WAITING))

	def test_no_text_says_instead(self):
		for reference in ("", "sr-a", "sr-b", "sr-a sr-b"):
			for on in (0.0, 40.0, 60.0, 100.0, 120.0):
				for row in self.rows(reference, on).values():
					with self.subTest(reference=reference, on_2210=on, row=row["run_ref"]):
						self.assertNotIn("instead", row["action"])


class TestBilledTrip(unittest.TestCase):
	"""The invoice is already submitted: the text says it books the purchase (third review, finding 6)."""

	def test_matched_linked_and_submitted(self):
		receipts = [receipt("2026-09-03", total=48.26, name="PR-1")]
		billed = {"PR-1": "ACC-PINV-7"}
		head = (
			"Billed from the receipts (ACC-PINV-7) and {how} too: ACC-PINV-7 already books this purchase, so "
			"this charge must not book it again. "
		)

		# Each "If it is" ends in a state the report can see (fourth review, item 4): a draft is marked
		# not-store-run once nothing of it is on the expense or 2210; a submitted charge, whose Reference
		# Number cannot change, keeps the purchase and the invoice is cancelled.
		def draft_tail(account):
			return (
				" If it is, it is the payment of ACC-PINV-7, not a second purchase: move its whole debit to the "
				f"store's payable (none of it on the expense or on {account}), make its Reference Number "
				"not-store-run alone and save it"
			)

		submitted_tail = (
			" If it is, it books the purchase and a submitted charge's Reference Number cannot be changed: "
			"cancel ACC-PINV-7 (and any payment made against it), and this row then says what the charge needs"
		)
		cases = (
			(
				[charge("2026-09-04", 48.26)],
				[],
				"matched to this charge",
				"If the charge is not this trip's, put its own trip's run id in its Reference Number and save "
				f"it; if it pays for no store run, move any debit it has on {ACCOUNT} back to the expense, put "
				"not-store-run in its Reference Number and save it.",
				draft_tail("2210"),
			),
			(
				[charge("2026-09-04", 48.26, docstatus=1)],
				[],
				"matched to this charge",
				"If the charge is not this trip's, its own trip's row says how to link it (a submitted entry's "
				"Reference Number cannot be changed).",
				submitted_tail,
			),
			(
				[charge("2026-09-10", 48.26, "ACC-JV-7")],
				[ref("ACC-JV-7", "sr-a")],
				"linked to this charge by its Reference Number",
				"If the charge is not this trip's, take this trip's run id out of the Reference Number that "
				"links it, ACC-JV-7 (its own Reference Number, a draft): edit a draft and save it, cancel a "
				"submitted correcting entry.",
				draft_tail(ACCOUNT),
			),
		)
		for charges, references, how, other, tail in cases:
			with self.subTest(how=how, other=other[:40]):
				row = report(charges, receipts, references, billed=billed)[0]
				self.assertEqual(row["action"], head.format(how=how) + other + tail)
				self.assertNotIn("before submitting", row["action"])


class TestOutsideTheRange(unittest.TestCase):
	"""A linked charge's figures go to the trips shown, and a linked charge in range whose trips are
	all outside it gets a row when it needs action (third review, finding 7)."""

	far = (receipt("2026-08-01", "sr-a", 60.0, total=107.0),)

	def test_p8_the_row_and_the_summary_agree(self):
		receipts = [receipt("2026-09-03", "sr-b", 40.0, total=50.0)]
		rows = report(
			[],
			receipts,
			[ref("ACC-JV-2", "sr-a sr-b", amount=107.0)],
			far_receipts=list(self.far),
			on_2210={je("ACC-JV-2"): 40.0},
		)
		self.assertEqual(len(rows), 1)
		self.assertTrue(
			rows[0]["action"].startswith("Move $60.00 more of the goods debit"), rows[0]["action"]
		)
		summary = matching.summarize(rows)
		self.assertEqual((rows[0]["to_move"], summary["to_move"], summary["moved"]), (60.0, 60.0, 40.0))

	def test_p8c_a_charge_whose_trips_are_all_before_from(self):
		far = [*self.far, receipt("2026-08-20", "sr-b", 40.0, total=50.0)]
		references = [ref("ACC-JV-2", "sr-a sr-b", amount=107.0)]
		rows = report([], [], references, far_receipts=far, on_2210={je("ACC-JV-2"): 40.0})
		self.assertEqual(len(rows), 1, rows)
		row = rows[0]
		self.assertEqual(
			(
				row["row_type"],
				row["run_ref"],
				row["charge"],
				row["stock_amount"],
				row["on_2210"],
				row["to_move"],
			),
			(matching.ROW_OUTSIDE, "sr-a, sr-b", "ACC-JV-2", 100.0, 40.0, 60.0),
		)
		self.assertEqual(
			row["action"],
			"The store runs its Reference Number links (sr-a, sr-b) are dated outside this range. Move $60.00 "
			f"more of the goods debit to {ACCOUNT} (the stock lines of sr-a, sr-b together) ($40.00 is there "
			"already), then save it; the S-D loop submits it",
		)
		self.assertEqual(matching.summarize(rows)["to_move"], 60.0)
		self.assertEqual(report([], [], references, far_receipts=far, on_2210={je("ACC-JV-2"): 100.0}), [])


class TestPurchaseInvoiceRow(unittest.TestCase):
	"""v16 books a stock line of an invoice made without a receipt to 2210 itself (third review,
	finding 8): an unpaired one waits for its receipt; nothing tells Accounting to move it off."""

	def test_it_waits_for_its_receipt(self):
		pi = charge(
			"2026-09-10", 50.0, "ACC-PINV-9", docstatus=1, source="ERPNext", voucher_type="Purchase Invoice"
		)
		rows = listed([pi], [], on_2210={("Purchase Invoice", "ACC-PINV-9"): 45.0})
		self.assertEqual(
			rows[0]["action"],
			"Waiting for its receipt: a Purchase Invoice books its stock lines ($45.00) to "
			f"{ACCOUNT} itself and its Purchase Receipt clears them, so leave its lines as they are. If its "
			"goods were recorded as a store run instead, that trip's row needs no other charge, and that trip "
			"must not be billed from its receipts",
		)
		self.assertEqual(rows[0]["show"], matching.WAITING)
		self.assertNotIn("move it back", rows[0]["action"])
		self.assertEqual(matching.summarize(rows)["unmatched_on_2210"], 0.0)


class TestFourthReview(unittest.TestCase):
	"""The round-4 verifier's minimal cases, each with its new outcome. The verifier followed every
	row's text to a fixed point on generated histories and checked, per purchase, that the goods on
	2210 equal its trips' stock lines and nothing is billed twice (v1.538.0 fourth review)."""

	# M1: sr-a's charge ACC-JV-1 arrived five days late; the pairing gave it to sr-b on the lines plus
	# tax, and sr-b's own charge ACC-JV-2 paired with nothing.
	m1_receipts = (
		receipt("2026-09-01", "sr-a", 100.0, total=107.0),
		receipt("2026-09-06", "sr-b", 95.0, total=101.65),
	)
	m1_charges = (charge("2026-09-06", 107.0, "ACC-JV-1"), charge("2026-09-07", 104.0, "ACC-JV-2"))

	def m1(self, references=(), on_2210=None):
		return {
			row["run_ref"] or row["charge"]: row
			for row in report(
				list(self.m1_charges), list(self.m1_receipts), list(references), on_2210=on_2210
			)
		}

	def test_m1_list_both_only_when_the_draft_pays_for_both(self):
		"""Item 1: said unconditionally, following it linked ACC-JV-1 to both trips, both read Done,
		and ACC-JV-2 was submitted with its goods on the expense."""
		rows = self.m1(on_2210={JV1: 95.0})
		self.assertEqual((rows["sr-b"]["charge"], rows["sr-b"]["show"]), ("ACC-JV-1", matching.DONE))
		self.assertEqual(rows["sr-a"]["action"], waiting_text("$100.00", "sr-a"))
		self.assertIn(
			"If that charge is already another trip's in this list and it also pays for that trip, list both "
			"trips' run ids (a draft: in its Reference Number, its 2210 debit carrying both trips' stock lines; "
			"submitted: after its name in that correcting entry). If it does not pay for that trip, take that "
			"trip's run id out of what links it to this charge",
			rows["sr-a"]["action"],
		)

	def test_m1_the_capacity_check_catches_both_listed(self):
		"""Item 1, the backstop: listed both anyway, ACC-JV-1 carries $195.00 of stock lines against a
		$107.00 charge. A card charge never moves more to 2210 than it paid, so neither row is Done."""
		reference = [ref("ACC-JV-1", "sr-a sr-b", day="2026-09-06", amount=107.0)]
		rows = self.m1(reference, on_2210={JV1: 195.0})
		for key in ("sr-a", "sr-b"):
			self.assertEqual(rows[key]["show"], matching.NEEDS_ACTION)
			self.assertEqual(
				rows[key]["action"],
				"The stock lines of the trips its Reference Number links (sr-a $100.00, sr-b $95.00: $195.00 "
				"together) are more than the charge itself ($107.00), so one of these trips is not this charge's: "
				"take the run id of each trip it does not pay for out of ACC-JV-1's Reference Number (edit it and "
				f"save it). This row then says what the charge keeps on {ACCOUNT}, and each trip taken out asks "
				"for its own charge on its own row. A trip this charge paid for only in part (the rest by store "
				"credit or a second card) cannot be matched here: that row stays until Accounting settles it by "
				"hand",
			)
			self.assertEqual(rows[key]["moved_to_2210"], 0)

	def test_m1_linked_alone_then_each_trip_its_own_charge(self):
		"""Linking sr-a alone: the draft's row cannot offer "also pays for sr-b" ($195.00 would be more
		than $107.00) and asks which one it pays for; sr-b's row asks the same the other way round.
		Following both ends with each trip on its own charge, and only then Done."""
		rows = self.m1([ref("ACC-JV-1", "sr-a", day="2026-09-06", amount=107.0)], on_2210={JV1: 195.0})
		self.assertEqual(
			rows["sr-a"]["action"],
			f"The {ACCOUNT} debit is $195.00 but the stock lines of the trip its Reference Number links (sr-a) "
			"are $100.00. If this draft pays for sr-b and not for sr-a, put sr-b in its Reference Number in "
			"place of sr-a and reduce its 2210 debit to $95.00 (the stock lines of sr-b); otherwise reduce it "
			"to $100.00. Then save it; the S-D loop submits it",
		)
		self.assertTrue(
			rows["sr-b"]["action"].startswith(
				"Its paired charge ACC-JV-1 now links only sr-a, by its Reference Number, and it cannot pay for "
				"this trip too: their stock lines and this trip's ($195.00) are more than ACC-JV-1 itself "
				"($107.00). If ACC-JV-1 is this trip's charge and not sr-a's, take sr-a out of ACC-JV-1's "
				"Reference Number (edit it and save it), then put sr-b in its Reference Number, save it, and this "
				"trip's row then says what to move. If not, find its QuickBooks draft"
			),
			rows["sr-b"]["action"],
		)
		self.assertNotIn("as well as", rows["sr-b"]["action"])
		done = self.m1(
			[
				ref("ACC-JV-1", "sr-a", day="2026-09-06", amount=107.0),
				ref("ACC-JV-2", "sr-b", day="2026-09-07", amount=104.0),
			],
			on_2210={JV1: 100.0, je("ACC-JV-2"): 95.0},
		)
		self.assertEqual(
			[(row["charge"], row["show"]) for row in done.values()],
			[("ACC-JV-1", matching.DONE), ("ACC-JV-2", matching.DONE)],
		)

	def test_capacity_allows_the_pairing_s_own_tolerance(self):
		"""An automatic pair on the lines plus tax may be a few cents under the lines; it is never
		flagged. Five cents more is."""
		receipts = [receipt("2026-09-03", "sr-a", 45.0, total=0.0)]
		self.assertEqual(
			self.only_trip(report([charge("2026-09-03", 44.96, "ACC-JV-1")], receipts))["action"],
			f"Move $45.00 of the goods debit to {ACCOUNT}, then save it; the S-D loop submits it",
		)
		over = report(
			[charge("2026-09-03", 44.94, "ACC-JV-1")],
			receipts,
			[ref("ACC-JV-1", "sr-a", day="2026-09-03", amount=44.94)],
		)
		self.assertTrue(
			self.only_trip(over)["action"].startswith(
				"The stock lines of the trip its Reference Number links (sr-a $45.00) are more than the charge "
				"itself ($44.94), so it is not this trip's charge: take sr-a out of ACC-JV-1's Reference Number"
			)
		)

	def test_an_automatic_pair_over_capacity(self):
		"""Paired on a receipt total typed wrong, to a smaller unrelated charge."""
		rows = report(
			[charge("2026-09-03", 20.0, "ACC-JV-5")], [receipt("2026-09-03", "sr-a", 45.0, total=20.0)]
		)
		row = self.only_trip(rows)
		self.assertEqual(
			(row["charge"], row["show"], row["to_move"]), ("ACC-JV-5", matching.NEEDS_ACTION, 0.0)
		)
		self.assertTrue(
			row["action"].startswith(
				f"Its stock lines ($45.00) are more than the charge itself ($20.00), and a charge never moves more "
				f"to {ACCOUNT} than it paid: it is not this trip's charge. For the trip's own charge, "
				f"{find_its_draft('$45.00', 'sr-a', account='2210')}. {ONLY_IF_NONE}"
			),
			row["action"],
		)

	def only_trip(self, rows):
		trips = _of(rows, matching.ROW_TRIP)
		self.assertEqual(len(trips), 1, rows)
		return trips[0]

	def test_m2_the_dropped_draft_is_moved_back_not_marked(self):
		"""Item 2: a contest named a non-store-run-vendor draft that carried $100.00 for sr-1. Dropped
		from the contest, its row offered not-store-run, which hid the $100.00 from the backstop."""
		receipts = [receipt("2026-09-01", "sr-1", 100.0, total=107.0)]
		charges = [charge("2026-09-09", 107.0, "ACC-JV-S", docstatus=1)]
		fix = ref("ACC-JV-900", "ACC-JV-S sr-1", day="2026-09-12", docstatus=1, source="ERPNext")
		on = {je("ACC-JV-900"): 100.0, je("ACC-JV-D"): 100.0}
		contested = report(charges, receipts, [fix, ref("ACC-JV-D", "sr-1", day="2026-09-02")], on_2210=on)
		self.assertIn("move the dropped charge's debit on", contested[0]["action"])
		dropped = report(
			charges,
			receipts,
			[fix],
			on_2210=on,
			journal=[fix, stray("ACC-JV-D", day="2026-09-02", docstatus=0, source="QuickBooks")],
		)
		entry = _of(dropped, matching.ROW_ENTRY)[0]
		self.assertEqual(entry["charge"], "ACC-JV-D")
		self.assertIn("otherwise move it back to the expense", entry["action"])
		self.assertNotIn(matching.NOT_STORE_RUN, entry["action"])
		self.assertEqual(matching.summarize(dropped)["not_accounted"], 100.0)
		# Marked not-store-run instead of moved back: still listed, still counted.
		marked = ref("ACC-JV-D", "not-store-run", day="2026-09-02")
		rows = report(charges, receipts, [fix, marked], on_2210=on, journal=[fix, marked])
		self.assertTrue(
			_of(rows, matching.ROW_ENTRY)[0]["action"].startswith(
				"Its Reference Number says not-store-run, but"
			)
		)
		self.assertEqual(matching.summarize(rows)["not_accounted"], 100.0)
		moved_back = {**on, je("ACC-JV-D"): 0.0}
		rows = report(charges, receipts, [fix, marked], on_2210=moved_back, journal=[fix, marked])
		self.assertEqual(
			[(row["row_type"], row["show"]) for row in rows], [(matching.ROW_TRIP, matching.DONE)]
		)

	def test_m2b_an_unrelated_draft_carrying_40(self):
		entry = stray("ACC-JV-D", day="2026-09-02", docstatus=0, amount=55.0, source="QuickBooks")
		rows = report([], [], on_2210={je("ACC-JV-D"): 40.0}, journal=[entry])
		self.assertIn("otherwise move it back to the expense", rows[0]["action"])
		marked = dict(entry, reference="not-store-run")
		rows = report([], [], [marked], on_2210={je("ACC-JV-D"): 40.0}, journal=[marked])
		self.assertEqual((rows[0]["show"], rows[0]["on_2210"]), (matching.NEEDS_ACTION, 40.0))
		self.assertEqual(report([], [], [marked], on_2210={je("ACC-JV-D"): 0.0}, journal=[marked]), [])

	def test_m3_a_submitted_quickbooks_entry_with_a_dead_key_and_money(self):
		"""Item 5 (v4_min3): its Reference Number never changes, so waiting for it waited for good. It
		is told to move the money back with a correcting entry naming it, and that entry counts."""
		entry = ref("ACC-JV-NV", "sr-1", day="2026-09-02", docstatus=1, amount=107.0)
		rows = report([], [], [entry], on_2210={je("ACC-JV-NV"): 100.0})
		self.assertEqual(
			rows[0]["action"],
			"Its Reference Number lists sr-1, which is no recorded store run. It is a submitted QuickBooks entry, "
			"which is never cancelled, so its Reference Number stays as it is and the report ignores what it "
			f"lists. Its $100.00 on {ACCOUNT} is therefore not accounted for: move it back to the expense; post "
			"and submit a correcting Journal Entry for $100.00 (Dr the expense account it used / Cr 2210) with "
			"Reference Number ACC-JV-NV. If you moved it for a trip, that trip's row then says how to move it "
			"again, linked.",
		)
		self.assertEqual(rows[0]["show"], matching.NEEDS_ACTION)
		fix = ref("ACC-JV-901", "ACC-JV-NV", day="2026-09-05", docstatus=1, source="ERPNext")
		rows = report([], [], [entry, fix], on_2210={je("ACC-JV-NV"): 100.0, je("ACC-JV-901"): -100.0})
		self.assertEqual([(row["charge"], row["show"]) for row in rows], [("ACC-JV-NV", matching.WAITING)])
		self.assertEqual(matching.summarize(rows)["not_accounted"], 0.0)

	def test_m3b_even_when_its_reference_number_points_two_ways(self):
		"""A submitted QuickBooks entry naming what cannot be resolved still names itself, so the
		correcting entry it is told to post counts for it."""
		charges = [
			charge("2026-09-04", 48.26, "ACC-JV-1"),
			charge("2026-09-05", 30.0, "ACC-JV-2", supplier="Lowes"),
		]
		entry = ref("ACC-JV-50", "ACC-JV-1 ACC-JV-2", day="2026-09-06", docstatus=1)
		rows = report(charges, _trip(), [entry], on_2210={je("ACC-JV-50"): 20.0})
		self.assertIn(
			"post and submit a correcting Journal Entry for $20.00 (Dr the expense account it used / Cr 2210) "
			"with Reference Number ACC-JV-50",
			_of(rows, matching.ROW_ENTRY)[0]["action"],
		)
		fix = ref("ACC-JV-901", "ACC-JV-50", day="2026-09-07", docstatus=1, source="ERPNext")
		rows = report(
			charges, _trip(), [entry, fix], on_2210={je("ACC-JV-50"): 20.0, je("ACC-JV-901"): -20.0}
		)
		self.assertEqual(_of(rows, matching.ROW_ENTRY)[0]["show"], matching.WAITING)
		self.assertEqual(matching.summarize(rows)["not_accounted"], 0.0)
		# A draft or an ERPNext entry pointing two ways still resolves to nothing.
		resolution = matching.resolve_references(
			[
				dict(entry, docstatus=0),
				ref("ACC-JV-901", "ACC-JV-50", day="2026-09-07", docstatus=1, source="ERPNext"),
			],
			charges,
			_trip(),
		)
		self.assertEqual(resolution["problems"]["ACC-JV-901"], [("unresolved", ["ACC-JV-50"])])

	def test_m4_a_submitted_charge_for_two_runs_is_never_reversed(self):
		"""Item 3: the correction for sr-a named the charge alone, the one for sr-b linked it to sr-b
		alone, and the report then had $60.00 reversed and posted again. Each correcting entry now names
		every trip it pays for, and the Waiting text asks for the other run id too."""
		charges = [charge("2026-09-04", 107.0, "ACC-JV-1", docstatus=1)]
		rows = {row["run_ref"]: row for row in report(charges, _two_runs())}
		self.assertEqual(
			rows["sr-a"]["action"],
			"Submitted with the goods on the expense: post and submit a correcting Journal Entry for $60.00 "
			f"(Dr {ACCOUNT} / Cr the expense account the charge used) with Reference Number ACC-JV-1 followed by "
			"sr-a",
		)
		self.assertIn(
			"whose Reference Number is its name followed by sr-b. If that charge is already another trip's in "
			"this list and it also pays for that trip, list both trips' run ids (a draft: in its Reference "
			"Number, its 2210 debit carrying both trips' stock lines; submitted: after its name in that "
			"correcting entry)",
			rows["sr-b"]["action"],
		)
		first = ref("ACC-JV-900", "ACC-JV-1 sr-a", day="2026-09-05", docstatus=1, source="ERPNext")
		on = {je("ACC-JV-900"): 60.0}
		rows = {row["run_ref"]: row for row in report(charges, _two_runs(), [first], on_2210=on)}
		self.assertEqual((rows["sr-a"]["show"], rows["sr-b"]["show"]), (matching.DONE, matching.WAITING))
		for second in ("ACC-JV-1 sr-b sr-a", "ACC-JV-1 sr-b"):
			with self.subTest(second=second):
				fix = ref("ACC-JV-901", second, day="2026-09-06", docstatus=1, source="ERPNext")
				rows = report(charges, _two_runs(), [first, fix], on_2210={**on, je("ACC-JV-901"): 40.0})
				self.assertEqual(
					[(row["run_ref"], row["show"], row["action"]) for row in rows],
					[
						("sr-a", matching.DONE, "Done (corrected by ACC-JV-900, ACC-JV-901)"),
						("sr-b", matching.DONE, "Done (corrected by ACC-JV-900, ACC-JV-901)"),
					],
				)

	def test_m5_billed_and_its_own_charge_leaves_needs_action(self):
		"""Item 4: "If it is" had no end the report could see. A draft marked not-store-run with
		nothing on 2210 frees the trip; for a submitted charge, cancelling the invoice leaves the
		ordinary correcting entry."""
		receipts = [receipt("2026-09-03", total=48.26, name="PR-1")]
		billed = {"PR-1": "ACC-PINV-7"}
		draft = [charge("2026-09-04", 48.26, "ACC-JV-1")]
		self.assertEqual(report(draft, receipts, billed=billed)[0]["show"], matching.NEEDS_ACTION)
		paid = report(draft, receipts, [ref("ACC-JV-1", "not-store-run", day="2026-09-04")], billed=billed)
		self.assertEqual(
			[(row["action"], row["show"]) for row in paid],
			[("Billed from the receipts (ACC-PINV-7)", matching.DONE)],
		)
		still = report(
			draft,
			receipts,
			[ref("ACC-JV-1", "not-store-run", day="2026-09-04")],
			billed=billed,
			on_2210={JV1: 45.0},
		)
		self.assertEqual(
			[(row["row_type"], row["show"]) for row in still],
			[(matching.ROW_ENTRY, matching.NEEDS_ACTION), (matching.ROW_TRIP, matching.DONE)],
		)
		submitted = [charge("2026-09-04", 48.26, "ACC-JV-1", docstatus=1)]
		self.assertIn(
			"cancel ACC-PINV-7 (and any payment made against it)",
			report(submitted, receipts, billed=billed)[0]["action"],
		)
		cancelled = report(submitted, receipts)[0]
		self.assertTrue(cancelled["action"].startswith("Submitted with the goods on the expense"))

	def test_m6_a_mistaken_link_is_asked_about_from_both_sides(self):
		"""Items 6 and 1: ACC-JV-9's Reference Number took sr-1 from its own draft ACC-JV-1. The draft's
		row gives both answers, and the $0.00 version of it is asked about too (Waiting): the link may
		be the mistake, and nothing else would say so."""
		receipts = [receipt("2026-09-01", "sr-1", 100.0, total=107.0)]
		charges = [
			charge("2026-09-01", 107.0, "ACC-JV-1"),
			charge("2026-09-02", 107.0, "ACC-JV-9", supplier="Lowes"),
		]
		references = [ref("ACC-JV-9", "sr-1", day="2026-09-02", amount=107.0)]
		for on, show, start in (
			(
				{JV1: 100.0},
				matching.NEEDS_ACTION,
				"It was paired with sr-1 until ACC-JV-9 linked sr-1: keep one charge",
			),
			(
				{},
				matching.WAITING,
				"It was paired with sr-1 until ACC-JV-9 linked sr-1, and carries nothing on",
			),
		):
			with self.subTest(on=on):
				row = _of(report(charges, receipts, references, on_2210=on), matching.ROW_CHARGE)[0]
				self.assertEqual((row["charge"], row["show"]), ("ACC-JV-1", show))
				self.assertTrue(row["action"].startswith(start), row["action"])
				self.assertIn(
					"If this one is sr-1's charge, take sr-1 out of the Reference Number", row["action"]
				)

	def test_m6b_a_displaced_trip_is_asked_all_three_ways(self):
		"""Item 1: a displaced trip was asked only whether the charge pays for it too, so a link typed on
		the wrong trip kept it and gained the right one. Asked also with no stock lines to move."""
		for stock, both in (
			(60.0, "add sr-a to that Reference Number and move this trip's $60.00 of stock lines too"),
			(0.0, "add sr-a to that Reference Number (this trip has no stock lines to move)"),
		):
			with self.subTest(stock=stock):
				receipts = [
					receipt("2026-09-03", "sr-a", 60.0, stock=stock, total=107.0),
					receipt("2026-09-03", "sr-b", 40.0, total=107.0),
				]
				rows = {
					row["run_ref"]: row
					for row in report(
						[charge("2026-09-04", 107.0, "ACC-JV-1")],
						receipts,
						[ref("ACC-JV-1", "sr-b", day="2026-09-04", amount=107.0)],
					)
				}
				action = rows["sr-a"]["action"]
				self.assertTrue(
					action.startswith(
						"Its paired charge ACC-JV-1 now links only sr-b, by its Reference Number. If ACC-JV-1 pays "
						f"for this trip as well as sr-b, {both}. If it pays for this trip and not for sr-b, take "
						"sr-b out of ACC-JV-1's Reference Number (edit it and save it), then put sr-a in its "
						"Reference Number"
					),
					action,
				)
				self.assertIn("If it does not pay for this trip, ", action)
				self.assertTrue(action.endswith(ONLY_IF_NONE))

	def test_m7_a_displaced_purchase_invoice_waits(self):
		"""Item 7: either answer leaves a correct booking behind, so it no longer blocks Needs action."""
		receipts = [receipt("2026-09-01", "sr-1", 100.0, total=107.0)]
		pi = charge(
			"2026-09-01", 107.0, "ACC-PINV-1", docstatus=1, source="ERPNext", voucher_type="Purchase Invoice"
		)
		rows = report(
			[pi],
			receipts,
			[ref("ACC-JV-2", "sr-1", day="2026-09-02")],
			on_2210={("Purchase Invoice", "ACC-PINV-1"): 100.0},
		)
		row = _of(rows, matching.ROW_CHARGE)[0]
		self.assertEqual(row["show"], matching.WAITING)
		self.assertEqual(matching.summarize(rows)["unmatched_on_2210"], 0.0)

	def test_m8_a_draft_correcting_entry_names_a_row_that_exists(self):
		"""Item 8: "ACC-JV-7's row" named a row that did not exist; a charge with trips has none. The
		trip's row is named, and a charge with no trip says so. One dated after To Date is listed too:
		the S-D loop submits it all the same."""
		charges = [charge("2026-09-05", 48.26, "ACC-JV-7", docstatus=1)]
		draft = ref("ACC-JV-900", "ACC-JV-7", day="2026-09-06", source="ERPNext")
		rows = report(charges, [], [draft], on_2210={je("ACC-JV-900"): 45.0})
		self.assertEqual(
			rows[0]["action"],
			"It is a draft correcting entry for ACC-JV-7, which no recorded store run is paired or linked with: "
			"delete it, unless ACC-JV-7 has a row of its own (No recorded store run) that asks for a correcting "
			"entry of this amount; then submit it (a draft correcting entry does not count until submitted). "
			f"Until then its $45.00 on {ACCOUNT} is not accounted for.",
		)
		late = dict(draft, day="2026-10-02")
		rows = report([charge("2026-09-04", 48.26, "ACC-JV-7", docstatus=1)], _trip(), [late])
		self.assertEqual(
			[(row["row_type"], row["show"]) for row in rows],
			[(matching.ROW_TRIP, matching.NEEDS_ACTION), (matching.ROW_ENTRY, matching.NEEDS_ACTION)],
		)
		self.assertIn(
			"or delete it if the row of sr-a (Matched Charge ACC-JV-7) does not ask for it", rows[1]["action"]
		)

	def test_the_fourth_review_rules_on_generated_histories(self):
		"""Seeded and bench-free: random trips, charges (drafts, submitted, some under no store-run
		vendor), mistaken links, two runs of one purchase, not-store-run marks and 2210 amounts. On every
		history: a charge whose trips' stock lines are more than it paid is never Done or ticked; a
		QuickBooks entry marked not-store-run that still carries 2210 is listed under Needs action and
		counted; a Journal Entry a link took from its trip is always listed; "list both" is always
		conditional; and a displaced trip is always asked all three ways."""
		rng = random.Random(20260925)
		start = date(2026, 9, 1)
		for case in range(400):
			receipts = []
			for index in range(rng.randint(1, 6)):
				lines = rng.choice((20.0, 45.0, 60.0, 95.0, 100.0))
				receipts.append(
					receipt(
						(start + timedelta(days=rng.randint(0, 20))).isoformat(),
						f"sr-{index}",
						lines,
						stock=rng.choice((lines, lines, 0.0)),
						total=rng.choice((round(lines * 1.07, 2), 0.0)),
						supplier=rng.choice(("Home Depot", "Lowes")),
					)
				)
			charges = [
				charge(
					(start + timedelta(days=rng.randint(0, 24))).isoformat(),
					rng.choice((21.4, 48.15, 64.2, 101.65, 107.0, 160.5)),
					f"ACC-JV-{index}",
					docstatus=rng.choice((0, 0, 1)),
					supplier=rng.choice(("Home Depot", "Lowes")),
				)
				for index in range(rng.randint(1, 6))
			]
			runs = [row["run"] for row in receipts]
			references, on_2210 = [], {}
			for row in charges:
				roll = rng.random()
				if roll < 0.35:
					tokens = rng.sample(runs, min(len(runs), rng.randint(1, 2)))
				elif roll < 0.45:
					tokens = [matching.NOT_STORE_RUN]
				else:
					tokens = []
				if tokens:
					references.append(
						ref(row["voucher_no"], " ".join(tokens), day=row["day"], docstatus=row["docstatus"])
					)
				on_2210[je(row["voucher_no"])] = rng.choice((0.0, 0.0, 45.0, 60.0, 100.0))
			rows, window, resolution, journal, on_2210, _corrections = flow(
				charges, receipts, references, on_2210=on_2210
			)
			ledger = matching.attribute_2210(window, resolution, on_2210, _corrections, journal)
			with self.subTest(case=case):
				by_charge = {}
				for row in _of(rows, matching.ROW_TRIP):
					by_charge.setdefault(row["charge"], []).append(row)
				for group in window["groups"]:
					stock = sum(matching._stock(trip) for trip in group["trips"])
					if (
						group["trips"]
						and stock > group["charge"]["amount"] + metrics.STORE_RUN_AMOUNT_TOLERANCE
					):
						for row in by_charge.get(group["charge"]["row"]["voucher_no"], ()):
							self.assertEqual((row["show"], row["moved_to_2210"]), (matching.NEEDS_ACTION, 0))
				listed = {row["charge"]: row for row in rows if row["row_type"] != matching.ROW_TRIP}
				for owner in set(ledger["marked"].values()):
					self.assertEqual(listed[owner]["show"], matching.NEEDS_ACTION, owner)
					self.assertTrue(
						listed[owner]["action"].startswith("Its Reference Number says not-store-run")
					)
				for bill in window["unpaired"]:
					if bill.get("unlinked") and bill["row"]["voucher_type"] == "Journal Entry":
						self.assertIn(bill["row"]["voucher_no"], listed)
				for row in rows:
					if "list both trips' run ids" in row["action"]:
						self.assertIn("in this list and it also pays for that trip, list both", row["action"])
					if row["action"].startswith("Its paired charge "):
						if "cannot pay for this trip too" in row["action"]:
							self.assertIn("is this trip's charge and not ", row["action"])
							self.assertIn(". If not, ", row["action"])
						else:
							self.assertIn("pays for this trip as well as ", row["action"])
							self.assertIn("If it does not pay for this trip, ", row["action"])


class TestTheTexts(unittest.TestCase):
	"""Plain sentences, the account written in full once, "post and submit" wherever a correcting
	entry is advised, and no amendment of a QuickBooks entry (third review)."""

	def test_over_generated_histories(self):
		rng = random.Random(20261024)
		start = date(2026, 9, 1)
		for case in range(300):
			receipts = [
				receipt(
					(start + timedelta(days=rng.randint(-10, 30))).isoformat(),
					f"sr-{index}",
					rng.choice((10.0, 45.0)),
					total=rng.choice((0.0, 48.26)),
					name=f"PR-{index}",
				)
				for index in range(rng.randint(0, 5))
			]
			charges = [
				charge(
					(start + timedelta(days=rng.randint(-5, 30))).isoformat(),
					rng.choice((10.7, 48.26)),
					f"JV-{index}",
					docstatus=rng.choice((0, 1)),
					source=rng.choice(("QuickBooks", "ERPNext")),
				)
				for index in range(rng.randint(0, 5))
			]
			pool = [row["run"] for row in receipts] + [row["voucher_no"] for row in charges] + ["sr-typo"]
			references = [
				ref(
					f"JV-X{index}",
					" ".join(rng.sample(pool, min(len(pool), 2))),
					docstatus=rng.choice((0, 1)),
					source="ERPNext",
				)
				for index in range(rng.randint(0, 3))
			]
			on_2210 = {
				je(name): rng.choice((0.0, 10.0, 45.0, 60.0, -5.0))
				for name in [c["voucher_no"] for c in charges] + [r["name"] for r in references]
			}
			billed = {row["receipt"]: "ACC-PINV-1" for row in receipts if rng.random() < 0.1}
			for row in report(charges, receipts, references, on_2210=on_2210, billed=billed):
				with self.subTest(case=case, row=row["run_ref"] or row["charge"]):
					self.assertLessEqual(row["action"].count(ACCOUNT), 1, row["action"])
					self.assertNotIn("post a correcting", row["action"])
					self.assertNotIn("instead", row["action"])
					if "QuickBooks" in (row["charge_source"] or ""):
						self.assertNotIn("amend", row["action"].lower())


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
				"not_accounted": 0.0,
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
				"not_accounted": 0.0,
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
				"not_accounted": 0.0,
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
			"early = add_days(from_date, -snapshots.STORE_RUN_LOOKBACK_DAYS)",
			"references = _references(early)",
			# Looked up round after round, so a chain of correcting entries is followed to its charge.
			"for _round in range(LOOKUP_ROUNDS):",
			"matching.unresolved_tokens(references + named, charges, receipts + far_receipts)",
			"far_receipts += _receipts_by_key(suppliers, unknown)",
			"named += _entries_by_name(unknown)",
			"matching.resolve_references(references, charges, receipts, far_receipts, named)",
			"matching.match_window(",
			"links=resolution['links']",
			"far_receipts=far_receipts",
			"excluded=resolution['excluded']",
			"roots=resolution['roots']",
			"journal = _journal_2210(early, to_date)",
			"matching.vouchers(window['trips'], window['unpaired'], window['outside'])",
			"matching.correcting_entries(found, resolution['corrections'], resolution['drafts'])",
			"matching.build_rows(",
			"billed=_billed(matching.receipt_names(window['trips'], window['outside']))",
			"accounts=_accounts(matching.companies(window, journal, resolution))",
			"corrections=matching.correction_amounts(resolution['corrections'], on_2210)",
			"unpaired=window['unpaired']",
			"window=window",
			"resolution=resolution",
			"journal=journal",
			"matching.filter_rows(",
			"matching.summarize(",
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, code)
		self.assertNotIn("pair_store_runs", code, "the pairing is reached through match_window only")
		self.assertIn("LOOKUP_ROUNDS = 4", self.source)

	def test_the_backstop_reads_every_2210_line(self):
		"""Every Journal Entry line on its own company's Stock Received But Not Billed account, draft or
		submitted, from the lookback to To Date, whatever its Reference Number, party or vendor (third
		review): a key that names nothing, or a Reference Number naming a correcting entry, used to
		drop an entry's money without a word."""
		query = self._query("as on_2210")
		for needle in (
			"select je.name, je.cheque_no as reference, je.docstatus, je.posting_date as day, je.company",
			"je.total_debit as amount, if(qm.erpnext_name is null, 'ERPNext', 'QuickBooks') as source, "
			"b.on_2210",
			"select jea.parent, coalesce(sum(jea.debit), 0) - coalesce(sum(jea.credit), 0) as on_2210 from "
			"`tabJournal Entry Account` jea join `tabJournal Entry` j on j.name = jea.parent join `tabCompany` "
			"c on c.name = j.company and c.stock_received_but_not_billed = jea.account",
			"where jea.parenttype = 'Journal Entry' and j.docstatus < 2 and j.posting_date >= %(early)s and "
			"j.posting_date <= %(to_date)s group by jea.parent ) b join `tabJournal Entry` je on je.name = "
			"b.parent",
			"left join ( select distinct m.erpnext_name from `tabQuickBooks Sync Mapping` m where "
			"m.erpnext_doctype = 'Journal Entry' ) qm on qm.erpnext_name = je.name",
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, query)
		for needle in ("supplier", "party", "cheque_no <>", "exists"):
			with self.subTest(absent=needle):
				self.assertNotIn(needle, query)
		# The charges' own lines are read the same way, so an entry read both ways reads the same.
		lines = self._query("jea.parent in %(names)s")
		self.assertIn(
			"join `tabJournal Entry` j on j.name = jea.parent join `tabCompany` c on c.name = j.company and "
			"c.stock_received_but_not_billed = jea.account",
			lines,
		)
		items = self._query("pii.parent in %(names)s")
		self.assertIn(
			"join `tabCompany` c on c.name = p.company and c.stock_received_but_not_billed = pii.expense_account",
			items,
		)
		self.assertNotIn("%(accounts)s", self.code)

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
		# A key is a run id or the name of any of the trip's receipts; a receipt named by its name
		# brings the rest of its run (third review: the First Receipt's name was refused when the
		# receipt had a run id).
		self.assertIn(
			f"and {run} in ( select coalesce(nullif(k.`custom_store_run`, ''), k.name) from `tabPurchase "
			"Receipt` k where k.name in %(keys)s or coalesce(nullif(k.`custom_store_run`, ''), k.name) in "
			"%(keys)s )",
			ours,
		)
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
		# The Reference Numbers, the Journal Entries and trips they name, the backstop's 2210 lines, a
		# Journal Entry's own 2210 lines (correcting entries' included), a Purchase Invoice's, the
		# billing.
		self.assertEqual(len(calls), 7)
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
		self.assertIn("not_accounted", keys)
		self.assertTrue(keys <= set(matching.summarize([])), keys - set(matching.summarize([])))
		self.assertIn('"label": _("2210 Not Accounted For")', self.source)

	def test_the_message_gives_the_loop_step(self):
		"""Step 5 (third review): Needs action, then Waiting, repeated until neither list changes, Needs
		action is empty and 2210 Not Accounted For reads $0.00; only then the loop."""
		function = next(
			node for node in self.tree.body if isinstance(node, ast.FunctionDef) and node.name == "_message"
		)
		message = " ".join(" ".join(_strings(function)).split())
		for needle in (
			"Then repeat: Show = <i>Needs action</i>, then Show = <i>Waiting</i>, until neither list changes, "
			"<i>Needs action</i> is empty and <i>2210 Not Accounted For</i> reads $0.00. Only then run the loop.",
			"posting and submitting one correcting Journal Entry",
			"if the draft is already another trip's charge in this list, list both only if it pays for both",
			"<i>not-store-run</i>",
			"a QuickBooks card charge must first carry nothing on 2210",
			"more than the charge itself",
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, message)

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
