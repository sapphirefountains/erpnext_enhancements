"""Bench-free tests for the Estimate's pricing engine (``estimating/estimate_math.py``).

The cases live in ``tests/data/estimate_math_cases.json`` and are shared with
``scripts/test_estimate_math.mjs``, which runs the browser twin against the same expectations. So a
change to the money math has to land in both engines and in the cases, or one of the two suites
fails - the form must never show a total the server will not save.

The numbers are made up. This repository is public.

Run: python -m unittest erpnext_enhancements.tests.test_estimate_math -v
"""

import json
import os
import unittest

from erpnext_enhancements.estimating import estimate_math as em

CASES = json.load(open(os.path.join(os.path.dirname(__file__), "data", "estimate_math_cases.json")))


class TestRounding(unittest.TestCase):
	def test_shared_cases(self):
		for value, expected in CASES["round_money"]:
			self.assertEqual(em.round_money(value), expected, value)
		for value, expected in CASES["round_money_1dp"]:
			self.assertEqual(em.round_money(value, 1), expected, value)

	def test_float_noise_is_not_a_half(self):
		# WI-077's pinned case: 2.10 x 2.45 is 5.1450000000000005 in floating point.
		self.assertEqual(em.round_money(2.10 * 2.45), 5.14)


class TestRates(unittest.TestCase):
	def test_parse_rate(self):
		for raw, expected in CASES["parse_rate"]:
			self.assertEqual(em.parse_rate(raw), expected, raw)
		with self.assertRaises(ValueError):
			em.parse_rate("five")

	def test_phase_chain(self):
		for number, chain in CASES["phase_chain"]:
			self.assertEqual(em.phase_chain(number), chain)
		self.assertEqual(em.top_phase(130), 100)
		self.assertEqual(em.top_phase(""), 0)
		self.assertEqual(em.phase_number("not a number"), 0)

	def test_zero_override_is_a_rate_not_a_blank(self):
		est = {"fee_pct": 12}
		phases = {100: {"phase_number": 100, "fee_override": "0"}}
		self.assertEqual(em.resolve("fee", {"phase_number": 100}, phases, est), 0)
		self.assertEqual(em.resolve("fee", {"phase_number": 200}, phases, est), 12)

	def test_line_beats_subphase_beats_phase_beats_estimate(self):
		est = {"contingency_pct": 5}
		phases = {
			100: {"contingency_override": "4"},
			120: {"contingency_override": "3"},
		}
		line = {"phase_number": 120}
		self.assertEqual(em.resolve("contingency", line, phases, est), 3)
		self.assertEqual(em.resolve("contingency", {"phase_number": 110}, phases, est), 4)
		self.assertEqual(em.resolve("contingency", dict(line, contingency_override="2"), phases, est), 2)

	def test_markup_comes_from_the_cost_type(self):
		est = {"markup_material": 15, "markup_labor": 0}
		self.assertEqual(em.resolve_markup({"cost_type": "Material"}, est), 15)
		self.assertEqual(em.resolve_markup({"cost_type": "Labor"}, est), 0)
		self.assertEqual(em.resolve_markup({"cost_type": "Material", "markup_override": "20"}, est), 20)
		self.assertEqual(em.resolve_markup({"cost_type": ""}, est), 0)


class TestLine(unittest.TestCase):
	RATES = dict(
		markup_pct=15, tax_pct=7.25, contingency_pct=5, insurance_pct=7, fee_pct=12, commission_pct=10
	)

	def test_worked_example(self):
		# Worked by hand: 120 ft at 5.91.
		r = em.price_line(120, 5.91, "Material", **self.RATES)
		self.assertEqual(r["base_cost"], 709.20)
		self.assertEqual(r["tax_amount"], 51.42)
		self.assertEqual(r["cost_amount"], 760.62)
		self.assertEqual(r["markup_amount"], 114.09)
		self.assertEqual(r["cost_of_work"], 874.71)
		self.assertEqual(r["contingency_amount"], 43.74)
		self.assertEqual(r["insurance_amount"], 61.23)
		self.assertEqual(r["fee_amount"], 104.97)
		self.assertEqual(r["amount"], 1084.65)
		# 108.465 is a true half after the noise is gone, so it goes to the even cent.
		self.assertEqual(r["commission_amount"], 108.46)
		self.assertEqual(r["margin_amount"], 324.03)

	def test_add_ons_are_on_cost_of_work_not_stacked(self):
		r = em.price_line(1, 1000, "Labor", contingency_pct=5, insurance_pct=7, fee_pct=12)
		self.assertEqual(r["amount"], 1240.0)

	def test_commission_is_internal(self):
		with_c = em.price_line(1, 1000, "Labor", fee_pct=12, commission_pct=10)
		without = em.price_line(1, 1000, "Labor", fee_pct=12)
		self.assertEqual(with_c["amount"], without["amount"])
		self.assertEqual(with_c["commission_amount"], 112.0)

	def test_tax_only_on_material_and_equipment(self):
		for ct in ("Labor", "Subcontract", "Expense"):
			self.assertEqual(em.price_line(1, 100, ct, tax_pct=8)["tax_amount"], 0, ct)
		for ct in ("Material", "Equipment"):
			self.assertEqual(em.price_line(1, 100, ct, tax_pct=8)["tax_amount"], 8, ct)

	def test_parts_sum_exactly(self):
		for qty in (0.333, 1, 2.1, 7, 13.37, 88, 1000.5):
			for cost in (0.01, 2.45, 5.91, 58, 1234.567):
				r = em.price_line(qty, cost, "Material", **self.RATES)
				self.assertEqual(r["cost_of_work"], em.round_money(r["cost_amount"] + r["markup_amount"]))
				self.assertEqual(
					r["amount"],
					em.round_money(
						r["cost_of_work"] + r["contingency_amount"] + r["insurance_amount"] + r["fee_amount"]
					),
				)

	def test_sheet_price_without_cost(self):
		r = em.price_line(1, 0, "Subcontract", sheet_price=2500, commission_pct=10)
		self.assertTrue(r["needs_cost"])
		self.assertFalse(r["priced"])
		self.assertEqual(r["amount"], 2500)
		self.assertEqual(r["cost_amount"], 0)
		self.assertEqual(r["commission_amount"], 250)

	def test_by_others_is_worth_nothing(self):
		r = em.price_line(1, 9999, "Subcontract", by_others=True, **self.RATES)
		self.assertEqual(r["amount"], 0)
		self.assertFalse(r["needs_cost"])


class TestBackfill(unittest.TestCase):
	def test_shared_cases(self):
		for case in CASES["backfill"]:
			uc = em.backfill_unit_cost(case["price"], case["qty"], case["cost_type"], **case["rates"])
			self.assertEqual(uc, case["unit_cost"])
			amount = em.price_line(case["qty"], uc, case["cost_type"], **case["rates"])["amount"]
			self.assertEqual(amount, case["reprices_to"])
			self.assertLessEqual(abs(amount - case["price"]), 0.011 * max(1, case["qty"]))


class TestEstimate(unittest.TestCase):
	def test_shared_cases(self):
		for case in CASES["estimates"]:
			got = em.price_estimate(case["estimate"], case["phases"], case["lines"])
			self.assertEqual(got, case["expect"], case["name"])

	def test_totals_equal_the_sum_of_lines(self):
		case = CASES["estimates"][0]
		got = em.price_estimate(case["estimate"], case["phases"], case["lines"])
		self.assertEqual(got["totals"]["amount"], em.round_money(sum(r["amount"] for r in got["lines"])))
		top = [k for k in got["phases"] if int(k) % 100 == 0]
		self.assertEqual(
			got["totals"]["amount"], em.round_money(sum(got["phases"][k]["amount"] for k in top))
		)

	def test_unphased_lines_group_under_zero(self):
		case = CASES["estimates"][1]
		got = em.price_estimate(case["estimate"], case["phases"], case["lines"])
		self.assertEqual(list(got["phases"]), ["0"])


if __name__ == "__main__":
	unittest.main()
