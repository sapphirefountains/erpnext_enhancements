"""Bench-free tests for the schedule of values and pay application arithmetic (estimating/pay_app_math.py).

No frappe. What it holds, all in made-up numbers:

* the shared cases (tests/data/pay_app_math_cases.json), which the browser twin is held to as well;
* **column D is the earlier applications' work completed, never their column G**: stored material is
  a balance, so a stored amount installed next month is not counted twice;
* line 7 sums what was certified, so a short certification carries into the next payment due;
* retainage steps down by the percent complete at the START of the period, never the end, and a
  step-down keeps holding what was held at the old rate (Nik, 2026-11-19);
* a line finished with the percent remaining closes to the cent;
* the projection from an estimate is a partition: rows sum to the lines exactly, the same estimate in
  any order gives the same rows, "by others" lines are rows worth nothing;
* every refusal is reported at once.

Run: python -m unittest erpnext_enhancements.tests.test_pay_app_math -v
"""

import json
import os
import random
import unittest

from erpnext_enhancements.estimating import pay_app_math as pm

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = json.load(open(os.path.join(HERE, "data", "pay_app_math_cases.json"), encoding="utf-8"))["cases"]
SCHEDULE = {"contract_sum": 10000, "retainage_applies": 1, "retainage_pct": 5, "stored_retainage_pct": 5}


def L(key, c, pct=0, **kw):
	return dict({"line_key": key, "scheduled_value": c, "this_period_pct": pct}, **kw)


class TestSharedCases(unittest.TestCase):
	def test_every_case(self):
		for case in CASES:
			with self.subTest(case["name"]):
				got = pm.price_application(
					case["schedule"], case["application"], case["lines"], case["previous"]
				)
				self.assertEqual(got, case["expected"])


class TestG703(unittest.TestCase):
	def test_stored_material_is_not_counted_twice(self):
		# Month 1: 1,000 of pumps stored on site. Month 2: they are installed (work) and nothing is stored.
		schedule = dict(SCHEDULE, stored_materials=1)
		m1 = pm.price_application(schedule, {}, [L("p", 4000, 0, stored=1000)], {})
		self.assertEqual(m1["lines"][0]["total_completed"], 1000.0)
		self.assertEqual(m1["lines"][0]["work_completed_to_date"], 0.0)
		# D for month 2 is month 1's WORK completed (0), not its column G (1,000)
		m2 = pm.price_application(
			schedule, {}, [L("p", 4000, 25, stored=0)], {"p": m1["lines"][0]["work_completed_to_date"]}
		)
		self.assertEqual(m2["lines"][0]["from_previous"], 0.0)
		self.assertEqual(m2["lines"][0]["total_completed"], 1000.0)

	def test_percent_is_none_on_a_zero_line(self):
		got = pm.price_application(SCHEDULE, {}, [L("x", 0, 0, by_others=1)], {})
		self.assertIsNone(got["lines"][0]["percent_complete"])

	def test_g702_lines(self):
		got = pm.price_application(
			SCHEDULE,
			{"less_previous_certificates": 1000},
			[L("a", 10000, 30), L("co", 2000, 50, is_change_order=1)],
			{"a": 1000},
		)["totals"]
		self.assertEqual(got["original_contract_sum"], 10000.0)
		self.assertEqual(got["net_change_orders"], 2000.0)
		self.assertEqual(got["contract_sum_to_date"], 12000.0)
		self.assertEqual(got["total_completed_stored"], 5000.0)  # 1000 + 3000 + 1000
		self.assertEqual(got["total_retainage"], 250.0)
		self.assertEqual(got["total_earned_less_retainage"], 4750.0)
		self.assertEqual(got["current_payment_due"], 3750.0)
		self.assertEqual(got["balance_to_finish"], 7250.0)  # 12000 - 4750

	def test_a_short_certification_carries_forward(self):
		app1 = pm.price_application(SCHEDULE, {}, [L("a", 10000, 40)], {})
		self.assertEqual(app1["totals"]["current_payment_due"], 3800.0)
		prev = {"less_previous_certificates": 0, "amount_certified": 3500}  # the GC certified 300 less
		line7 = pm.next_previous_certificates(prev)
		app2 = pm.price_application(
			SCHEDULE, {"less_previous_certificates": line7}, [L("a", 10000, 10)], {"a": 4000}
		)
		# earned 5000 - 250 = 4750, less 3500 certified = 1250: this month's 950 plus the 300 shortfall
		self.assertEqual(app2["totals"]["current_payment_due"], 1250.0)

	def test_retainage_steps_by_the_start_of_the_period(self):
		steps = [
			{"from_percent_complete": 0, "work_pct": 10, "stored_pct": 10},
			{"from_percent_complete": 50, "work_pct": 5, "stored_pct": 5},
		]
		schedule = dict(SCHEDULE, steps=steps)
		# starts at 45%, ends at 75%: still billed at 10%, because the start decides
		got = pm.price_application(schedule, {}, [L("a", 10000, 30)], {"a": 4500})
		self.assertEqual(got["totals"]["work_retainage_pct"], 10.0)
		self.assertEqual(got["totals"]["percent_complete_at_start"], 45.0)
		self.assertEqual(got["totals"]["retainage_source"], "step from 0%")

	def test_a_step_down_keeps_holding_what_was_held(self):
		# Nik, 2026-11-19: "Keep holding it." 10% until 50% complete, then 5%, on a 10,000 job.
		steps = [
			{"from_percent_complete": 0, "work_pct": 10, "stored_pct": 10},
			{"from_percent_complete": 50, "work_pct": 5, "stored_pct": 5},
		]
		schedule = dict(SCHEDULE, steps=steps)
		previous, line7, held = {}, 0.0, []
		for pct in (40, 20, 10):
			got = pm.price_application(
				schedule, {"less_previous_certificates": line7}, [L("a", 10000, pct)], previous
			)
			row, totals = got["lines"][0], got["totals"]
			held.append(row["retainage_work"])
			previous = {"a": {"work": row["work_completed_to_date"], "retainage_work": row["retainage_work"]}}
			line7 = pm.next_previous_certificates(
				{"less_previous_certificates": line7, "amount_certified": totals["current_payment_due"]}
			)
		# 4,000 and 2,000 held at 10% stay held; the 1,000 after the step adds 5%. Re-rating all work at
		# 5% would have held 350 and paid out 250 more than the month earned.
		self.assertEqual(held, [400.0, 600.0, 650.0])
		self.assertEqual(totals["current_payment_due"], 950.0)

	def test_clearing_an_override_falls_back(self):
		got = pm.price_application(SCHEDULE, {"work_retainage_override": "  "}, [L("a", 10000, 10)], {})
		self.assertEqual(got["totals"]["work_retainage_pct"], 5.0)
		got = pm.price_application(SCHEDULE, {"work_retainage_override": "0"}, [L("a", 10000, 10)], {})
		self.assertEqual(got["totals"]["work_retainage_pct"], 0.0)

	def test_finishing_a_line_closes_it(self):
		for c, d in ((1000, 333.33), (2717.39, 1000), (99.99, 33.33)):
			remaining = pm.percent(pm.cents(c) - pm.cents(d), pm.cents(c))
			got = pm.price_application(dict(SCHEDULE, contract_sum=c), {}, [L("a", c, remaining)], {"a": d})
			self.assertEqual(got["lines"][0]["balance"], 0.0, (c, d))


class TestRefusals(unittest.TestCase):
	def context(self, lines, **kw):
		ctx = {
			"schedule_locked": True,
			"schedule_keys": [l["line_key"] for l in lines],
			"previous": None,
			"period_from": "2026-11-01",
			"period_to": "2026-11-30",
			"stored_materials": False,
			"lines": lines,
		}
		ctx.update(kw)
		return ctx

	def codes(self, lines, previous_work=None, schedule=SCHEDULE, application=None, **kw):
		priced = pm.price_application(schedule, application or {}, lines, previous_work or {})
		return [code for code, _msg in pm.refusals(self.context(lines, **kw), priced)]

	def test_a_clean_application_passes(self):
		self.assertEqual(self.codes([L("a", 10000, 10)]), [])

	def test_every_refusal_at_once(self):
		lines = [
			L("a", 1000, 60, item_no="1.1", description="Pumps"),
			L("b", 1000, -10, item_no="1.2", description="Labor"),
			L("z", 0, 10, item_no="1.3", description="Nothing"),
			L("x", 0, 10, item_no="1.4", description="Tile", by_others=1),
			L("s", 1000, 0, item_no="1.5", description="Stored", stored=100),
			L("r", 1000, 10, item_no="1.6", description="Rate", work_retainage_override="150"),
		]
		codes = self.codes(
			lines,
			previous_work={"a": 600, "b": 200},
			schedule_locked=False,
			schedule_keys=["a", "b", "x", "s", "r"],
			previous={"name": "PAY-1", "docstatus": 0, "period_to": "2026-11-05"},
			amount_certified=999999,
		)
		for code in (
			"SCHEDULE_NOT_LOCKED",
			"PREVIOUS_NOT_CERTIFIED",
			"PERIOD_OVERLAPS",
			"LINE_NOT_ON_SCHEDULE",
			"OVER_SCHEDULED",
			"NEGATIVE_WITHOUT_NOTE",
			"WORK_ON_ZERO_LINE",
			"BY_OTHERS_BILLED",
			"STORED_WHILE_OFF",
			"RETAINAGE_OUT_OF_RANGE",
			"CERTIFIED_OVER_DUE",
		):
			self.assertIn(code, codes)

	def test_a_noted_deduction_is_allowed(self):
		self.assertEqual(self.codes([L("a", 1000, -10, adjustment_note="Rebilled in error")], {"a": 500}), [])

	def test_negative_completed_or_stored_is_refused(self):
		codes = self.codes([L("a", 1000, -60, adjustment_note="credit")], {"a": 500})
		self.assertIn("COMPLETED_BELOW_ZERO", codes)
		codes = self.codes(
			[L("a", 1000, 0, stored=-50)], schedule=dict(SCHEDULE, stored_materials=1), stored_materials=True
		)
		self.assertIn("STORED_NEGATIVE", codes)

	def test_a_negative_payment_due_is_refused(self):
		# retainage going up after a big earlier payment: nothing earned this month, more held back
		lines = [L("a", 10000, 0)]
		codes = self.codes(lines, {"a": 5000}, application={"less_previous_certificates": 5000})
		self.assertIn("PAYMENT_DUE_NEGATIVE", codes)


ESTIMATE_LINES = [
	{
		"idx": 1,
		"phase_number": 120,
		"cost_type": "Labor",
		"description": "Schematic design",
		"amount": 1000.0,
	},
	{"idx": 2, "phase_number": 500, "cost_type": "Equipment", "description": "Pump", "amount": 2717.39},
	{"idx": 3, "phase_number": 500, "cost_type": "Equipment", "description": "Filter", "amount": 1000.01},
	{"idx": 4, "phase_number": 500, "cost_type": "Labor", "description": "Install", "amount": 750.0},
	{
		"idx": 5,
		"phase_number": 700,
		"cost_type": "Subcontract",
		"description": "Tile",
		"amount": 0,
		"by_others": 1,
	},
	{
		"idx": 6,
		"phase_number": 100,
		"cost_type": "Labor",
		"description": "Project management",
		"amount": 333.33,
	},
]
PHASES = [{"phase_number": 500, "label": "Mechanical room"}]


class TestProjection(unittest.TestCase):
	def test_rows_by_phase_and_cost_type(self):
		rows, check = pm.sov_rows(PHASES, ESTIMATE_LINES, total=5800.73)
		self.assertEqual(
			[(r["item_no"], r["description"], r["scheduled_value"], r["by_others"]) for r in rows],
			[
				("1.1", "Project management", 333.33, 0),
				("1.2", "Schematic design", 1000.0, 0),
				("5.1", "Mechanical room: equipment", 3717.4, 0),
				("5.2", "Install", 750.0, 0),
				("7.1", "Tile", 0.0, 1),
			],
		)
		self.assertEqual(check["difference"], 0.0)
		self.assertEqual(rows[2]["source_lines"], "2,3")

	def test_one_row_per_line(self):
		rows, _check = pm.sov_rows(PHASES, ESTIMATE_LINES, rule="line")
		self.assertEqual(len(rows), 6)

	def test_the_same_estimate_in_any_order_gives_the_same_rows(self):
		expected, _ = pm.sov_rows(PHASES, ESTIMATE_LINES)
		shuffled = list(ESTIMATE_LINES)
		for seed in range(5):
			random.Random(seed).shuffle(shuffled)
			self.assertEqual(pm.sov_rows(PHASES, shuffled)[0], expected)

	def test_a_difference_from_the_contract_is_reported(self):
		_rows, check = pm.sov_rows(PHASES, ESTIMATE_LINES, total=6000)
		self.assertEqual(check["difference"], 199.27)

	def test_an_event_without_phases_numbers_plainly(self):
		lines = [
			{
				"idx": 1,
				"phase_number": 0,
				"cost_type": "Equipment",
				"description": "Fountain rental",
				"amount": 900,
			}
		]
		rows, _ = pm.sov_rows([], lines)
		self.assertEqual(rows[0]["item_no"], "1")


if __name__ == "__main__":
	unittest.main()
