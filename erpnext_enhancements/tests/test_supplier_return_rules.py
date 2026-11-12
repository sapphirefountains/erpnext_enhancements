# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the Supplier Return rules (v1.807.0).

``inventory_enhancements/supplier_return_rules.py`` holds every judgement the return process
makes and imports nothing from Frappe, so this suite needs no stub:

* **The steps**: which action leads where, from which status, for which roles; that nothing skips
  "Sent Back" on the way to an outcome; days waiting; who the return is waiting on.
* **The quantity limit**: no more than was received, less what other returns hold and what was
  returned with ERPNext's own button; two lines of one return on the same receipt line judged
  together.
* **Which stock document**, for each kind of line: a stock item received into stock, a non-stock
  line, and a line received before its item kept stock (record only, with the reason in words).
* **The standard return cut to this return's lines**: a partial return, a second return, a line
  ERPNext cannot cover.
* **The money rule**: typed by hand only while no Debit Note or Payment Entry is linked; what each
  document says; a debit note the supplier still owes on is not "received".
* **The bill**, the email's words and recipients, the report's rows and views, and the contract
  other modules read (``returned_line``).

Fake item codes, names and addresses only: the repository is public.

Run: python -m unittest erpnext_enhancements.tests.test_supplier_return_rules -v
"""

import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.inventory_enhancements import supplier_return_rules as rules

BUYER = {"Purchase User"}
CLERK = {"Stock User"}
BOOKS = {"Accounts User"}
MANAGER = {"Purchase Manager"}
NOBODY = {"Employee", "Projects User"}


def line(
	idx=1, item="TST-VALVE-1", qty=1.0, returnable=5.0, received=5.0, reason="Damaged", notes="", key="PRI-1"
):
	return {
		"idx": idx,
		"item_code": item,
		"qty": qty,
		"returnable": returnable,
		"received": received,
		"reason": reason,
		"condition_notes": notes,
		"purchase_receipt_item": key,
	}


class TestSteps(unittest.TestCase):
	def test_the_happy_path_in_order(self):
		status = rules.DRAFT
		for action, expected in (
			("request", rules.REQUESTED),
			("approve", rules.APPROVED),
			("send_back", rules.SENT_BACK),
			("refund", rules.REFUND_RECEIVED),
		):
			status = rules.next_status(action, status)
			self.assertEqual(status, expected)

	def test_a_counter_return_skips_the_request(self):
		"""Dropped off at the supplier's counter: Draft straight to Sent Back is allowed."""
		self.assertEqual(rules.next_status("send_back", rules.DRAFT), rules.SENT_BACK)
		self.assertEqual(rules.next_status("approve", rules.DRAFT), rules.APPROVED)

	def test_no_outcome_before_the_items_have_gone(self):
		for action in ("refund", "credit", "replacement", "close"):
			for status in (rules.DRAFT, rules.REQUESTED, rules.APPROVED):
				with self.assertRaises(rules.RuleError) as caught:
					rules.next_status(action, status)
				self.assertIn("Sent Back first", str(caught.exception))

	def test_every_outcome_starts_from_sent_back_only(self):
		for action, target in (
			("refund", rules.REFUND_RECEIVED),
			("credit", rules.CREDIT_RECEIVED),
			("replacement", rules.REPLACEMENT_RECEIVED),
			("close", rules.CLOSED_NO_CREDIT),
		):
			self.assertEqual(rules.ACTIONS[action][0], (rules.SENT_BACK,))
			self.assertEqual(rules.next_status(action, rules.SENT_BACK), target)

	def test_a_return_that_has_gone_cannot_be_canceled(self):
		for status in rules.GONE:
			with self.assertRaises(rules.RuleError) as caught:
				rules.next_status("cancel", status)
		self.assertIn("Close it without credit", str(caught.exception))
		self.assertEqual(rules.next_status("cancel", rules.REQUESTED), rules.CANCELED)

	def test_a_final_status_takes_no_step_but_reopen(self):
		for status in rules.FINAL:
			for action in rules.ACTIONS:
				with self.assertRaises(rules.RuleError):
					rules.next_status(action, status)
			self.assertEqual(rules.next_status("reopen", status), rules.REOPEN_TO[status])

	def test_reopen_only_from_a_final_status(self):
		for status in (rules.DRAFT, rules.REQUESTED, rules.APPROVED, rules.SENT_BACK):
			with self.assertRaises(rules.RuleError):
				rules.next_status("reopen", status)

	def test_every_status_is_reachable_and_named_once(self):
		targets = {spec[1] for spec in rules.ACTIONS.values()} | {rules.DRAFT}
		self.assertEqual(targets, set(rules.STATUSES))
		self.assertEqual(len(rules.STATUSES), len(set(rules.STATUSES)))
		self.assertTrue(rules.FINAL < set(rules.STATUSES))
		self.assertTrue(rules.GONE < set(rules.STATUSES))
		self.assertEqual(rules.NOT_COUNTED, {rules.DENIED, rules.CANCELED})

	def test_american_spelling(self):
		self.assertIn("Canceled", rules.STATUSES)
		self.assertFalse([status for status in rules.STATUSES if "Cancelled" in status])

	def test_unknown_action(self):
		with self.assertRaises(rules.RuleError):
			rules.next_status("ship_it", rules.DRAFT)


class TestWhoMay(unittest.TestCase):
	def test_buyers_and_stock_people_create_and_send(self):
		for roles in (BUYER, CLERK, MANAGER, {"Stock Manager"}):
			for action in ("request", "approve", "deny", "send_back", "replacement", "cancel"):
				self.assertTrue(rules.may(action, roles), (action, roles))

	def test_only_accounts_and_the_purchase_manager_mark_money(self):
		for action in ("refund", "credit", "close"):
			self.assertTrue(rules.may(action, BOOKS))
			self.assertTrue(rules.may(action, {"Accounts Manager"}))
			self.assertTrue(rules.may(action, MANAGER))
			self.assertFalse(rules.may(action, BUYER), action)
			self.assertFalse(rules.may(action, CLERK), action)

	def test_accounts_do_not_send_or_ship(self):
		for action in ("request", "send_back", "cancel"):
			self.assertFalse(rules.may(action, BOOKS))

	def test_nobody_else_does_anything(self):
		for action in [*rules.ACTIONS, "reopen"]:
			self.assertFalse(rules.may(action, NOBODY))
			self.assertFalse(rules.may(action, set()))

	def test_reopen_is_a_managers(self):
		self.assertTrue(rules.may("reopen", MANAGER))
		self.assertTrue(rules.may("reopen", {"Accounts Manager"}))
		self.assertFalse(rules.may("reopen", BUYER))
		self.assertFalse(rules.may("reopen", BOOKS))

	def test_the_buttons_offered(self):
		self.assertEqual(
			rules.allowed_actions(rules.DRAFT, BUYER), ["request", "approve", "deny", "send_back", "cancel"]
		)
		self.assertEqual(rules.allowed_actions(rules.SENT_BACK, BUYER), ["replacement"])
		self.assertEqual(rules.allowed_actions(rules.SENT_BACK, BOOKS), ["refund", "credit", "close"])
		self.assertEqual(rules.allowed_actions(rules.REFUND_RECEIVED, BUYER), [])
		self.assertEqual(rules.allowed_actions(rules.REFUND_RECEIVED, MANAGER), ["reopen"])

	def test_no_typed_money_button_once_a_document_decides(self):
		self.assertEqual(rules.allowed_actions(rules.SENT_BACK, BOOKS, money_backed=True), ["close"])


class TestWaiting(unittest.TestCase):
	def test_whose_move(self):
		self.assertEqual(rules.waiting_on(rules.DRAFT, rules.REFUND), "us")
		self.assertEqual(rules.waiting_on(rules.REQUESTED, rules.REFUND), "supplier")
		self.assertEqual(rules.waiting_on(rules.APPROVED, rules.REFUND), "us")
		self.assertEqual(rules.waiting_on(rules.SENT_BACK, rules.REFUND), "money")
		self.assertEqual(rules.waiting_on(rules.SENT_BACK, rules.CREDIT), "money")
		self.assertEqual(rules.waiting_on(rules.SENT_BACK, rules.REPLACEMENT), "replacement")
		for status in rules.FINAL:
			self.assertEqual(rules.waiting_on(status, rules.REFUND), "")

	def test_waiting_on_money_is_sent_back_and_owed_money(self):
		self.assertTrue(rules.waiting_on_money(rules.SENT_BACK, rules.REFUND))
		self.assertFalse(rules.waiting_on_money(rules.SENT_BACK, rules.REPLACEMENT))
		self.assertFalse(rules.waiting_on_money(rules.APPROVED, rules.REFUND))
		self.assertFalse(rules.waiting_on_money(rules.REFUND_RECEIVED, rules.REFUND))

	def test_days_waiting(self):
		self.assertEqual(rules.days_waiting("2026-11-01 09:30:00", "2026-11-12"), 11)
		self.assertEqual(rules.days_waiting(date(2026, 11, 12), date(2026, 11, 12)), 0)
		self.assertEqual(rules.days_waiting("2026-11-20", "2026-11-12"), 0, "never negative")
		self.assertIsNone(rules.days_waiting(None, "2026-11-12"))


class TestQuantities(unittest.TestCase):
	def test_returnable_is_received_less_what_is_held(self):
		self.assertEqual(rules.returnable_qty(10), 10)
		self.assertEqual(rules.returnable_qty(10, claimed_elsewhere=4), 6)
		self.assertEqual(rules.returnable_qty(10, claimed_elsewhere=4, returned_outside=3), 3)
		self.assertEqual(rules.returnable_qty(10, 8, 5), 0, "never negative")

	def test_a_good_line_has_no_problem(self):
		self.assertEqual(rules.quantity_problems([line(qty=5)]), [])

	def test_more_than_was_received(self):
		problems = rules.quantity_problems([line(qty=6, returnable=5, received=5)])
		self.assertEqual(len(problems), 1)
		self.assertIn("only 5 of TST-VALVE-1 can still go back", problems[0])

	def test_a_second_return_gets_what_the_first_left(self):
		"""10 received, a first return holds 4: a second may take 6, not 7."""
		left = rules.returnable_qty(10, claimed_elsewhere=4)
		self.assertEqual(rules.quantity_problems([line(qty=6, returnable=left, received=10)]), [])
		problems = rules.quantity_problems([line(qty=7, returnable=left, received=10)])
		self.assertIn("4 already returned or on another return", problems[0])

	def test_nothing_left(self):
		problems = rules.quantity_problems([line(qty=1, returnable=0, received=5)])
		self.assertIn("have already been returned or are on another return", problems[0])

	def test_two_lines_on_one_receipt_line_are_judged_together(self):
		lines = [line(idx=1, qty=3), line(idx=2, qty=3)]
		problems = rules.quantity_problems(lines)
		self.assertEqual(len(problems), 1)
		self.assertIn("asks for 6", problems[0])

	def test_zero_and_negative(self):
		for qty in (0, -1, None):
			self.assertIn("enter how many", rules.quantity_problems([line(qty=qty)])[0])

	def test_a_reason_is_required_and_other_needs_words(self):
		self.assertIn("pick a reason", rules.quantity_problems([line(reason="")])[0])
		self.assertIn("pick a reason", rules.quantity_problems([line(reason="Because")])[0])
		self.assertIn("say what is wrong", rules.quantity_problems([line(reason="Other")])[0])
		self.assertEqual(rules.quantity_problems([line(reason="Other", notes="Bent flange")]), [])

	def test_the_reasons_nik_named(self):
		self.assertEqual(
			rules.REASONS, ("Wrong item", "Damaged", "Defective", "Not needed", "Over-shipped", "Other")
		)


class TestAmounts(unittest.TestCase):
	LINES = [{"qty": 2, "rate": 50.0}, {"qty": 1, "rate": 25.5}]

	def test_expected_is_items_plus_tax_less_fee(self):
		answer = rules.totals(self.LINES, tax_amount=8.0, restocking_fee=15.0)
		self.assertEqual(answer, {"total_amount": 125.5, "expected_amount": 118.5})

	def test_shipping_we_pay_is_not_taken_off(self):
		answer = rules.totals(self.LINES, return_shipping_cost=20.0, shipping_deducted=False)
		self.assertEqual(answer["expected_amount"], 125.5)

	def test_shipping_the_supplier_deducts_is(self):
		answer = rules.totals(self.LINES, return_shipping_cost=20.0, shipping_deducted=True)
		self.assertEqual(answer["expected_amount"], 105.5)

	def test_never_below_zero(self):
		self.assertEqual(rules.totals(self.LINES, restocking_fee=500)["expected_amount"], 0.0)

	def test_tax_in_proportion(self):
		self.assertEqual(rules.tax_share(receipt_tax=20.0, receipt_net=200.0, return_net=50.0), 5.0)
		self.assertEqual(rules.tax_share(20.0, 200.0, 400.0), 20.0, "never more than the receipt's tax")
		self.assertEqual(rules.tax_share(20.0, 0, 50.0), 0.0)
		self.assertEqual(rules.tax_share(None, 200.0, 50.0), 0.0)

	def test_difference(self):
		self.assertEqual(rules.difference(118.5, 100), 18.5)
		self.assertEqual(rules.difference(100, 118.5), -18.5)


class TestWhichStockDocument(unittest.TestCase):
	"""The cases the brief names, each to its document."""

	def test_line_kinds(self):
		self.assertEqual(rules.line_kind(is_stock_item=True, wrote_stock=True), rules.STOCK)
		self.assertEqual(rules.line_kind(is_stock_item=False, wrote_stock=False), rules.NON_STOCK)
		self.assertEqual(rules.line_kind(is_stock_item=True, wrote_stock=False), rules.RECORD_ONLY)
		self.assertEqual(
			rules.line_kind(is_stock_item=False, wrote_stock=True),
			rules.NON_STOCK,
			"the item's flag today decides whether anything is in stock to take out",
		)

	def test_a_stock_item_received_into_stock(self):
		plan = rules.stock_plan([{"item_code": "TST-PUMP-1", "line_kind": rules.STOCK}], "PR-0001")
		self.assertTrue(plan["needs_purchase_return"])
		self.assertTrue(plan["takes_stock"])
		self.assertEqual(plan["note"], "")
		self.assertEqual(plan["record_only_lines"], [])

	def test_a_non_stock_line_still_gets_the_standard_return(self):
		"""Nothing is in stock, but the Purchase Return lowers the order's received quantity, which
		is what lets a replacement be received and makes ERPNext bill the receipt for less."""
		plan = rules.stock_plan([{"item_code": "TST-FITTING-1", "line_kind": rules.NON_STOCK}], "PR-0001")
		self.assertTrue(plan["needs_purchase_return"])
		self.assertFalse(plan["takes_stock"])
		self.assertEqual(plan["note"], "")

	def test_an_old_line_of_a_switched_item_is_record_only(self):
		plan = rules.stock_plan([{"item_code": "TST-OLD-1", "line_kind": rules.RECORD_ONLY}], "PR-0001")
		self.assertFalse(plan["needs_purchase_return"])
		self.assertFalse(plan["takes_stock"])
		self.assertEqual(plan["return_lines"], [])
		for words in (
			"No stock document is made for TST-OLD-1",
			"PR-0001 never added it to stock",
			"record only",
		):
			self.assertIn(words, plan["note"])

	def test_a_mixed_return_keeps_the_record_only_line_off_the_document(self):
		lines = [
			{"item_code": "TST-PUMP-1", "line_kind": rules.STOCK},
			{"item_code": "TST-OLD-1", "line_kind": rules.RECORD_ONLY},
			{"item_code": "TST-FITTING-1", "line_kind": rules.NON_STOCK},
		]
		plan = rules.stock_plan(lines, "PR-0001")
		self.assertEqual([l["item_code"] for l in plan["return_lines"]], ["TST-PUMP-1", "TST-FITTING-1"])
		self.assertEqual([l["item_code"] for l in plan["record_only_lines"]], ["TST-OLD-1"])
		self.assertTrue(plan["needs_purchase_return"])
		self.assertIn("TST-OLD-1", plan["note"])
		self.assertNotIn("TST-PUMP-1", plan["note"])

	def test_the_stock_line_on_the_record(self):
		plan = rules.stock_plan([{"item_code": "TST-PUMP-1", "line_kind": rules.STOCK}])
		self.assertIn("When this is marked Sent Back", rules.stock_state(rules.APPROVED, plan))
		self.assertIn("Not done yet", rules.stock_state(rules.SENT_BACK, plan))
		draft = {"name": "PR-RET-1", "docstatus": 0}
		self.assertIn("is a draft. Submit it", rules.stock_state(rules.SENT_BACK, plan, draft))
		done = {"name": "PR-RET-1", "docstatus": 1}
		self.assertEqual(
			rules.stock_state(rules.SENT_BACK, plan, done),
			"Purchase Return PR-RET-1 took the items out of stock.",
		)
		canceled = {"name": "PR-RET-1", "docstatus": 2}
		self.assertIn("Not done yet", rules.stock_state(rules.SENT_BACK, plan, canceled))
		self.assertIn("stock is unchanged", rules.stock_state(rules.CANCELED, plan))

	def test_a_record_only_return_says_so(self):
		plan = rules.stock_plan([{"item_code": "TST-OLD-1", "line_kind": rules.RECORD_ONLY}])
		self.assertEqual(
			rules.stock_state(rules.SENT_BACK, plan), "No stock document: this return is a record only."
		)

	def test_when_the_form_asks_for_the_purchase_return(self):
		plan = rules.stock_plan([{"item_code": "TST-PUMP-1", "line_kind": rules.STOCK}])
		self.assertFalse(rules.needs_stock_action(rules.APPROVED, plan), "stock leaves when the items do")
		self.assertTrue(rules.needs_stock_action(rules.SENT_BACK, plan))
		self.assertTrue(rules.needs_stock_action(rules.SENT_BACK, plan, {"name": "X", "docstatus": 0}))
		self.assertFalse(rules.needs_stock_action(rules.SENT_BACK, plan, {"name": "X", "docstatus": 1}))
		self.assertTrue(
			rules.needs_stock_action(rules.REFUND_RECEIVED, plan), "money back does not move stock"
		)
		record_only = rules.stock_plan([{"item_code": "TST-OLD-1", "line_kind": rules.RECORD_ONLY}])
		self.assertFalse(rules.needs_stock_action(rules.SENT_BACK, record_only))


class TestTrimmingTheStandardReturn(unittest.TestCase):
	"""ERPNext's mapper gives every line at everything still returnable; the return keeps its own."""

	MAPPED = [
		{"purchase_receipt_item": "PRI-1", "qty": -10.0},
		{"purchase_receipt_item": "PRI-2", "qty": -4.0},
		{"purchase_receipt_item": "PRI-3", "qty": -2.0},
	]

	def test_a_partial_return(self):
		kept, short = rules.trim_return_rows(self.MAPPED, {"PRI-1": 3})
		self.assertEqual(kept, [{"purchase_receipt_item": "PRI-1", "qty": -3.0}])
		self.assertEqual(short, {})

	def test_lines_not_on_this_return_are_dropped(self):
		kept, _short = rules.trim_return_rows(self.MAPPED, {"PRI-2": 4, "PRI-3": 1})
		self.assertEqual([row["purchase_receipt_item"] for row in kept], ["PRI-2", "PRI-3"])
		self.assertEqual([row["qty"] for row in kept], [-4.0, -1.0])

	def test_quantities_are_negative(self):
		kept, _short = rules.trim_return_rows(self.MAPPED, {"PRI-1": 10})
		self.assertTrue(all(row["qty"] < 0 for row in kept))

	def test_a_second_return_after_the_first_was_submitted(self):
		"""10 received and 4 already returned: the mapper offers -6, and a second return of 6 fits."""
		mapped = [{"purchase_receipt_item": "PRI-1", "qty": -6.0}]
		kept, short = rules.trim_return_rows(mapped, {"PRI-1": 6})
		self.assertEqual(kept[0]["qty"], -6.0)
		self.assertEqual(short, {})

	def test_more_than_erpnext_can_return_is_reported_not_trimmed_in_silence(self):
		mapped = [{"purchase_receipt_item": "PRI-1", "qty": -6.0}]
		_kept, short = rules.trim_return_rows(mapped, {"PRI-1": 7})
		self.assertEqual(short, {"PRI-1": (7.0, 6.0)})

	def test_a_line_erpnext_did_not_offer(self):
		"""Fully returned already: the mapper leaves the line out, and that is reported."""
		_kept, short = rules.trim_return_rows([], {"PRI-9": 2})
		self.assertEqual(short, {"PRI-9": (2.0, 0.0)})

	def test_the_mapped_rows_are_not_mutated(self):
		before = [dict(row) for row in self.MAPPED]
		rules.trim_return_rows(self.MAPPED, {"PRI-1": 3})
		self.assertEqual(self.MAPPED, before)


class TestMoney(unittest.TestCase):
	NOTE = {
		"name": "DN-1",
		"docstatus": 1,
		"amount": 118.5,
		"owed": 0,
		"posting_date": "2026-11-10",
		"reference": "CM-77",
	}
	PAYMENT = {
		"name": "PE-1",
		"docstatus": 1,
		"amount": 110.0,
		"posting_date": "2026-11-11",
		"reference": "CHK 4410",
	}

	def test_no_documents_means_a_person_types_it(self):
		answer = rules.money_from_documents()
		self.assertFalse(answer["backed"])
		self.assertIsNone(answer["status"])

	def test_drafts_and_canceled_documents_decide_nothing(self):
		for docstatus in (0, 2):
			answer = rules.money_from_documents(
				debit_note=dict(self.NOTE, docstatus=docstatus),
				payment_entry=dict(self.PAYMENT, docstatus=docstatus),
			)
			self.assertFalse(answer["backed"], docstatus)

	def test_a_refund_payment_is_refund_received_at_its_own_amount(self):
		answer = rules.money_from_documents(debit_note=self.NOTE, payment_entry=self.PAYMENT)
		self.assertEqual(answer["status"], rules.REFUND_RECEIVED)
		self.assertEqual(answer["source"], rules.FROM_PAYMENT)
		self.assertEqual(answer["amount"], 110.0)
		self.assertEqual(answer["date"], "2026-11-11")
		self.assertEqual(answer["reference"], "CHK 4410")

	def test_a_settled_debit_note_is_credit_received(self):
		"""Set against the bill (return before the bill was paid): what we owe went down."""
		answer = rules.money_from_documents(debit_note=self.NOTE)
		self.assertEqual(answer["status"], rules.CREDIT_RECEIVED)
		self.assertEqual(answer["source"], rules.FROM_DEBIT_NOTE)
		self.assertEqual(answer["amount"], 118.5)
		self.assertEqual(answer["reference"], "CM-77")

	def test_a_debit_note_the_supplier_still_owes_on_is_not_received(self):
		"""After the bill was paid: the note carries the money owed back until the refund arrives."""
		answer = rules.money_from_documents(debit_note=dict(self.NOTE, owed=118.5))
		self.assertIsNone(answer["status"])
		self.assertTrue(answer["backed"], "but nobody may type an outcome over it")
		self.assertIn("still owes 118.50", answer["note"])
		self.assertIn("Payment Entry", answer["note"])

	def test_typed_money_needs_amount_date_and_reference(self):
		good = rules.check_typed_money("refund", "118.50", "2026-11-10", " CHK 4410 ", "2026-11-12")
		self.assertEqual(good, {"amount": 118.5, "date": date(2026, 11, 10), "reference": "CHK 4410"})
		for args, words in (
			(("refund", 0, "2026-11-10", "x"), "amount that came back"),
			(("refund", "abc", "2026-11-10", "x"), "amount that came back"),
			(("refund", 10, None, "x"), "date the money came back"),
			(("refund", 10, "2026-11-13", "x"), "future"),
			(("refund", 10, "2026-11-10", "  "), "reference"),
			(("gift", 10, "2026-11-10", "x"), "refund or a credit"),
		):
			with self.assertRaises(rules.RuleError) as caught:
				rules.check_typed_money(*args, "2026-11-12")
			self.assertIn(words, str(caught.exception))

	def test_typed_money_is_refused_once_a_document_is_linked(self):
		for documents in (
			rules.money_from_documents(payment_entry=self.PAYMENT),
			rules.money_from_documents(debit_note=self.NOTE),
			rules.money_from_documents(debit_note=dict(self.NOTE, owed=50)),
		):
			with self.assertRaises(rules.RuleError) as caught:
				rules.check_typed_money("refund", 118.5, "2026-11-10", "CHK 1", "2026-11-12", documents)
			self.assertIn("can't be typed", str(caught.exception))

	def test_typed_money_is_allowed_beside_a_draft_document(self):
		documents = rules.money_from_documents(debit_note=dict(self.NOTE, docstatus=0))
		self.assertTrue(rules.check_typed_money("credit", 5, "2026-11-10", "CM-1", "2026-11-12", documents))

	def test_the_record_says_it_plainly(self):
		self.assertIn("not backed by an ERPNext payment", rules.NOT_BACKED)
		self.assertEqual(rules.MONEY_SOURCES, (rules.TYPED, rules.FROM_DEBIT_NOTE, rules.FROM_PAYMENT))

	def test_sent_back_needs_how(self):
		rules.check_send_back("Dropped off", None, None)
		rules.check_send_back("Supplier pickup", "", "")
		rules.check_send_back("Carrier", "Test Freight", "1Z999")
		for args in (
			("", None, None),
			("Pigeon", "x", "y"),
			("Carrier", "Test Freight", ""),
			("Carrier", "", "1Z999"),
		):
			with self.assertRaises(rules.RuleError):
				rules.check_send_back(*args)


class TestTheBill(unittest.TestCase):
	def test_nothing_to_say(self):
		self.assertEqual(rules.bill_notice([]), "")

	def test_it_names_the_return_and_what_went_back(self):
		notice = rules.bill_notice(
			[
				{
					"name": "SRET-2026-00001",
					"status": rules.SENT_BACK,
					"items": [("TST-PUMP-1", 2.0)],
					"expected_amount": 100,
				}
			]
		)
		for words in (
			"SRET-2026-00001",
			"2 x TST-PUMP-1",
			"went back to the supplier",
			"does not charge for",
		):
			self.assertIn(words, notice)

	def test_a_return_that_has_not_gone_yet(self):
		notice = rules.bill_notice(
			[{"name": "SRET-2", "status": rules.REQUESTED, "items": [("TST-PUMP-1", 1)]}]
		)
		self.assertIn("are being returned", notice)

	def test_the_report_tells_the_person_entering_the_bill(self):
		self.assertEqual(rules.bill_state(None, []), "Not billed in ERPNext. The bill should come in lower")
		self.assertEqual(rules.bill_state(None, ["PINV-1"]), "Billed: PINV-1. Needs a Debit Note")
		self.assertEqual(rules.bill_state("DN-1", ["PINV-1"]), "Debit Note DN-1")


class TestTheEmail(unittest.TestCase):
	DOC = {
		"name": "SRET-2026-00001",
		"supplier": "Test Supply Co",
		"supplier_name": "Test Supply Co",
		"purchase_receipt": "PR-0001",
		"requested_resolution": rules.REFUND,
		"request_notes": " Box was crushed on arrival. ",
	}
	LINES = [
		{
			"item_code": "TST-PUMP-1",
			"item_name": "Test pump",
			"qty": 2.0,
			"uom": "Nos",
			"reason": "Damaged",
			"condition_notes": "Cracked housing",
			"purchase_order": "PO-0009",
			"supplier_part_no": "SP-88",
		},
		{
			"item_code": "TST-TEE-2",
			"item_name": "Test tee",
			"qty": 1.5,
			"uom": "Ft",
			"reason": "Not needed",
			"condition_notes": "",
			"purchase_order": "PO-0009",
			"supplier_part_no": None,
		},
	]

	def test_context(self):
		context = rules.email_context(
			self.DOC, self.LINES, "Test Company", "Pat Buyer", "Sam", "(555) 010-0100"
		)
		self.assertEqual(context["purchase_order"], "PO-0009")
		self.assertEqual(context["asking_for"], "a refund")
		self.assertEqual(context["notes"], "Box was crushed on arrival.")
		self.assertEqual(context["contact_name"], "Sam")
		self.assertEqual(context["sender_name"], "Pat Buyer")

	def test_what_we_ask_for_in_words(self):
		for resolution, words in (
			(rules.REFUND, "a refund"),
			(rules.CREDIT, "a credit on our account"),
			(rules.REPLACEMENT, "a replacement"),
		):
			context = rules.email_context(
				dict(self.DOC, requested_resolution=resolution), self.LINES, "C", "P"
			)
			self.assertEqual(context["asking_for"], words)

	def test_lines_use_the_suppliers_part_number_when_there_is_one(self):
		rows = rules.email_lines(self.LINES)
		self.assertEqual(rows[0], ["SP-88", "Test pump", "2 Nos", "Damaged: Cracked housing"])
		self.assertEqual(rows[1], ["TST-TEE-2", "Test tee", "1.5 Ft", "Not needed"])
		self.assertTrue(all(len(row) == 4 for row in rows), "four columns is what fits a phone")

	def test_the_built_in_wording_is_content_only(self):
		"""docs/email-design-system.md: the shell owns the chrome. No container, width or color."""
		body = rules.DEFAULT_BODY.lower()
		for banned in ("max-width", "margin:0 auto", "width=", "style=", "<table", "<img", "#"):
			self.assertNotIn(banned, body, banned)
		self.assertIn("{{ lines_table }}", rules.DEFAULT_BODY)

	def test_the_template_renders(self):
		import jinja2

		context = rules.email_context(self.DOC, self.LINES, "Test Company", "Pat Buyer", "Sam", "")
		env = jinja2.Environment(undefined=jinja2.StrictUndefined)
		subject = env.from_string(rules.DEFAULT_SUBJECT).render(context)
		self.assertEqual(subject, "Return request SRET-2026-00001 from Test Company (PO PO-0009)")
		body = env.from_string(rules.DEFAULT_BODY).render(dict(context, lines_table="<TABLE>"))
		for words in (
			"Hello Sam,",
			"purchase order PO-0009",
			"asking for a refund",
			"<TABLE>",
			"Box was crushed",
			"Pat Buyer",
		):
			self.assertIn(words, body)

	def test_the_template_renders_with_nothing_optional(self):
		import jinja2

		doc = dict(self.DOC, request_notes="")
		lines = [dict(self.LINES[0], purchase_order=None)]
		context = rules.email_context(doc, lines, "Test Company", "Pat Buyer")
		env = jinja2.Environment(undefined=jinja2.StrictUndefined)
		self.assertEqual(
			env.from_string(rules.DEFAULT_SUBJECT).render(context),
			"Return request SRET-2026-00001 from Test Company",
		)
		body = env.from_string(rules.DEFAULT_BODY).render(dict(context, lines_table=""))
		self.assertIn("<p>Hello,</p>", body)
		self.assertNotIn("purchase order", body)

	def test_the_items_table_replaces_the_marker(self):
		marker = rules.LINES_MARKER
		self.assertEqual(
			rules.place_lines_table(f"<p>Hi</p><p>{marker}</p><p>Bye</p>", "<T>"), "<p>Hi</p><T><p>Bye</p>"
		)
		self.assertEqual(rules.place_lines_table(f'<p class="x"> {marker} </p>', "<T>"), "<T>")
		self.assertEqual(rules.place_lines_table(f"<p>See {marker} below</p>", "<T>"), "<p>See <T> below</p>")

	def test_a_deleted_marker_still_gets_the_table(self):
		"""A return request with no items in it is not one."""
		self.assertEqual(rules.place_lines_table("<p>Hi</p>", "<T>"), "<p>Hi</p><T>")
		self.assertEqual(rules.place_lines_table("", "<T>"), "<T>")
		self.assertEqual(rules.place_lines_table(None, "<T>"), "<T>")

	def test_the_table_goes_in_once(self):
		marker = rules.LINES_MARKER
		placed = rules.place_lines_table(f"<p>{marker}</p><p>{marker}</p><p>and {marker}</p>", "<T>")
		self.assertEqual(placed.count("<T>"), 1)
		self.assertNotIn(marker, placed)

	def test_recipient_choices_are_distinct_and_in_order(self):
		choices = rules.recipient_choices(
			[
				("returns@supplier.example", ""),
				("sam@supplier.example", "Sam Lee"),
				("SAM@supplier.example", "Sam again"),
				("", "Nobody"),
				("not an address", "Bad"),
				(None, "None"),
				("sales@supplier.example", "Test Supply Co"),
			]
		)
		self.assertEqual(
			[c["email"] for c in choices],
			["returns@supplier.example", "sam@supplier.example", "sales@supplier.example"],
		)
		self.assertEqual(choices[1]["label"], "Sam Lee <sam@supplier.example>")

	def test_one_recipient_shaped_like_an_address(self):
		self.assertEqual(rules.check_recipient(" sam@supplier.example "), "sam@supplier.example")
		for bad in (
			"",
			None,
			"sam",
			"sam@supplier",
			"a@b.example, c@d.example",
			"a@b.example;c@d.example",
			"sam @supplier.example",
		):
			with self.assertRaises(rules.RuleError):
				rules.check_recipient(bad)

	def test_email_is_not_a_way_a_person_can_claim(self):
		"""'Requested by Email' is set only by the send; the other ways are a person's word."""
		self.assertEqual(rules.REQUEST_WAYS[0], "Email")
		self.assertEqual(set(rules.REQUEST_WAYS[1:]), {"Phone", "In person", "Supplier's website"})


class TestTheReport(unittest.TestCase):
	def doc(self, **overrides):
		base = {
			"name": "SRET-2026-00001",
			"supplier": "Test Supply Co",
			"supplier_name": "Test Supply Co",
			"status": rules.SENT_BACK,
			"requested_resolution": rules.REFUND,
			"sent_back_on": "2026-11-02",
			"expected_amount": 118.5,
			"amount_received": 0,
			"money_source": "",
			"debit_note": None,
			"purchase_receipt": "PR-0001",
		}
		base.update(overrides)
		return base

	def test_a_waiting_row(self):
		row = rules.report_row(self.doc(), [], "2026-11-12")
		self.assertEqual(row["days_waiting"], 10)
		self.assertEqual(row["expected_amount"], 118.5)
		self.assertIsNone(row["amount_received"])
		self.assertEqual(row["outstanding"], 118.5)
		self.assertEqual(row["bill"], "Not billed in ERPNext. The bill should come in lower")

	def test_a_typed_refund_says_so_in_full(self):
		row = rules.report_row(
			self.doc(status=rules.REFUND_RECEIVED, amount_received=100, money_source=rules.TYPED),
			[],
			"2026-11-12",
		)
		self.assertEqual(row["money_source"], rules.NOT_BACKED)
		self.assertEqual(row["amount_received"], 100.0)
		self.assertEqual(row["outstanding"], 18.5)
		self.assertIsNone(row["days_waiting"])

	def test_a_document_backed_refund_names_the_document_kind(self):
		row = rules.report_row(
			self.doc(
				status=rules.REFUND_RECEIVED,
				amount_received=118.5,
				money_source=rules.FROM_PAYMENT,
				debit_note="DN-1",
			),
			["PINV-1"],
			"2026-11-12",
		)
		self.assertEqual(row["money_source"], "Payment Entry")
		self.assertEqual(row["outstanding"], 0.0)
		self.assertEqual(row["bill"], "Debit Note DN-1")

	def test_views(self):
		waiting = (rules.SENT_BACK, rules.REFUND, 100, 0)
		short = (rules.REFUND_RECEIVED, rules.REFUND, 100, 80)
		exact = (rules.REFUND_RECEIVED, rules.REFUND, 100, 100)
		closed = (rules.CLOSED_NO_CREDIT, rules.REFUND, 100, 0)
		replacement = (rules.SENT_BACK, rules.REPLACEMENT, 0, 0)
		canceled = (rules.CANCELED, rules.REFUND, 100, 0)
		self.assertEqual(
			[
				rules.in_view(rules.VIEW_WAITING, *case)
				for case in (waiting, short, exact, closed, replacement, canceled)
			],
			[True, False, False, False, False, False],
		)
		self.assertEqual(
			[
				rules.in_view(rules.VIEW_SHORT, *case)
				for case in (waiting, short, exact, closed, replacement, canceled)
			],
			[False, True, False, True, False, False],
		)
		self.assertEqual(
			[
				rules.in_view(rules.VIEW_ALL, *case)
				for case in (waiting, short, exact, closed, replacement, canceled)
			],
			[True, True, True, True, True, False],
		)

	def test_summary(self):
		rows = [
			rules.report_row(self.doc(), [], "2026-11-12"),
			rules.report_row(
				self.doc(name="SRET-2", sent_back_on="2026-10-01", expected_amount=50), [], "2026-11-12"
			),
			rules.report_row(
				self.doc(name="SRET-3", status=rules.REFUND_RECEIVED, amount_received=10), [], "2026-11-12"
			),
		]
		self.assertEqual(rules.report_summary(rows), {"waiting": 2, "owed": 168.5, "oldest_days": 42})
		self.assertEqual(rules.report_summary([]), {"waiting": 0, "owed": 0, "oldest_days": 0})


class TestWhatOtherModulesRead(unittest.TestCase):
	"""``supplier_returns.returned_lines`` is a contract with the Project Materials report."""

	ROW = {
		"supplier_return": "SRET-2026-00001",
		"status": rules.SENT_BACK,
		"supplier": "Test Supply Co",
		"purchase_order": "PO-0009",
		"purchase_order_item": "POI-1",
		"purchase_receipt": "PR-0001",
		"purchase_receipt_item": "PRI-1",
		"item_code": "TST-PUMP-1",
		"project": "PRJ-TEST",
		"qty": 2,
		"uom": "Box",
		"conversion_factor": 12,
		"amount": 100,
	}

	def test_the_keys_are_exactly_the_contract(self):
		self.assertEqual(tuple(rules.returned_line(self.ROW)), rules.RETURNED_LINE_KEYS)
		self.assertEqual(
			rules.RETURNED_LINE_KEYS,
			(
				"supplier_return",
				"status",
				"supplier",
				"purchase_order",
				"purchase_order_item",
				"purchase_receipt",
				"purchase_receipt_item",
				"item_code",
				"project",
				"qty",
				"uom",
				"stock_qty",
				"amount",
				"gone",
				"open",
			),
			"added to, never renamed: another module's report reads these",
		)

	def test_quantities_and_flags(self):
		line_ = rules.returned_line(self.ROW)
		self.assertEqual((line_["qty"], line_["stock_qty"], line_["amount"]), (2.0, 24.0, 100.0))
		self.assertTrue(line_["gone"])
		self.assertTrue(line_["open"])

	def test_gone_and_open_by_status(self):
		expect = {
			rules.DRAFT: (False, True),
			rules.REQUESTED: (False, True),
			rules.APPROVED: (False, True),
			rules.SENT_BACK: (True, True),
			rules.REFUND_RECEIVED: (True, False),
			rules.CREDIT_RECEIVED: (True, False),
			rules.REPLACEMENT_RECEIVED: (True, False),
			rules.CLOSED_NO_CREDIT: (True, False),
		}
		for status, (gone, open_) in expect.items():
			line_ = rules.returned_line(dict(self.ROW, status=status))
			self.assertEqual((line_["gone"], line_["open"]), (gone, open_), status)

	def test_returns_that_never_happened_are_left_out(self):
		for status in (rules.DENIED, rules.CANCELED):
			self.assertIsNone(rules.returned_line(dict(self.ROW, status=status)))

	def test_a_missing_factor_is_one(self):
		self.assertEqual(rules.returned_line(dict(self.ROW, conversion_factor=None))["stock_qty"], 2.0)


if __name__ == "__main__":
	unittest.main()
