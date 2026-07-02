"""Bench-free unit tests for the pure product-configurator engine.

Plain ``unittest`` — the engine imports only the stdlib, so these run with no
Frappe site. Golden values come from the source workbook "PDT-0040 Stillwater
Pricing Calculator.xlsx": its two worked examples (Sheet1 rows 25–36) total
1685.008 and 1512.979, and each module's marked-up grand total is asserted
against the sheet's own cells. The product definition under test is imported
straight from ``seed_data.py`` — the goldens validate the shipped seed.

Run: python -m pytest erpnext_enhancements/tests/test_product_configurator_engine.py
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.product_configurator.engine import (
	ConditionError,
	build_context,
	build_part_number,
	explode_parts,
	price_configuration,
	render_build_steps,
	render_text,
	safe_eval_expr,
	validate_selections,
)
from erpnext_enhancements.product_configurator.seed_data import PDT_0040

MOST_COMMON = {"mounting": "1", "estop_qty": 1, "timer_qty": 1, "contactor_qty": 2, "relay_qty": 0}
EXAMPLE_SURFACE = {"mounting": "2", "estop_qty": 1, "timer_qty": 1, "contactor_qty": 2, "relay_qty": 1}


def _line(result, module_key):
	return next(ln for ln in result["lines"] if ln["module_key"] == module_key)


class TestGoldenPricing(unittest.TestCase):
	"""Sheet1's worked examples, reproduced from the shipped seed."""

	def test_example_surface_config(self):
		# PDT-0040-2-1-1-2-1 -> 1685.008 (Sheet1 row 29)
		result = price_configuration(PDT_0040, EXAMPLE_SURFACE)
		self.assertEqual(result["part_number"], "PDT-0040-2-1-1-2-1")
		self.assertAlmostEqual(result["total_price"], 1685.008, places=3)

	def test_most_common_config(self):
		# PDT-0040-1-1-1-2-0 -> 1512.979 (Sheet1 row 36)
		result = price_configuration(PDT_0040, MOST_COMMON)
		self.assertEqual(result["part_number"], "PDT-0040-1-1-1-2-0")
		self.assertAlmostEqual(result["total_price"], 1512.979, places=3)

	def test_per_module_grand_totals(self):
		goldens = {
			"base": 300.95,
			"mounting_flush": 205.621,
			"estop": 207.35,
			"timer": 185.458,
			"contactor": 306.80,
			"relay": 85.15,
		}
		result = price_configuration(
			PDT_0040,
			{"mounting": "1", "estop_qty": 1, "timer_qty": 1, "contactor_qty": 1, "relay_qty": 1},
		)
		for module_key, expected in goldens.items():
			self.assertAlmostEqual(
				_line(result, module_key)["unit_price"], expected, places=3, msg=module_key
			)

	def test_surface_flat_labor_and_pedestal(self):
		surface = price_configuration(PDT_0040, EXAMPLE_SURFACE)
		self.assertAlmostEqual(_line(surface, "mounting_surface")["unit_price"], 292.50, places=3)
		pedestal = price_configuration(PDT_0040, dict(MOST_COMMON, mounting="3"))
		self.assertAlmostEqual(_line(pedestal, "mounting_pedestal")["unit_price"], 1196.00, places=3)

	def test_mounting_cost_scales_with_estop_qty(self):
		result = price_configuration(PDT_0040, dict(MOST_COMMON, estop_qty=2))
		mounting = _line(result, "mounting_flush")
		self.assertEqual(mounting["qty"], 2)
		self.assertAlmostEqual(mounting["line_price"], 2 * 205.621, places=3)

	def test_mounting_digit_not_multiplied_by_estop_qty(self):
		# The workbook's config-number formula multiplies the mounting digit by
		# e-stop qty (bug); the decode table says the digit is just 1/2/3.
		result = price_configuration(PDT_0040, dict(MOST_COMMON, estop_qty=2))
		self.assertEqual(result["part_number"], "PDT-0040-1-2-1-2-0")

	def test_additional_cost_is_unmarked_passthrough(self):
		base = price_configuration(PDT_0040, MOST_COMMON)
		extra = price_configuration(
			PDT_0040, MOST_COMMON, additional={"description": "Custom decal", "cost": 40}
		)
		self.assertAlmostEqual(extra["total_price"] - base["total_price"], 40.0, places=9)

	def test_zero_qty_module_drops_line_and_parts(self):
		result = price_configuration(PDT_0040, dict(MOST_COMMON, timer_qty=0))
		self.assertNotIn("timer", [ln["module_key"] for ln in result["lines"]])
		parts = explode_parts(PDT_0040["components"], result["module_qtys"])
		self.assertNotIn("timer", [p["module_key"] for p in parts])

	def test_contactor_rotation_warning(self):
		result = price_configuration(PDT_0040, dict(MOST_COMMON, contactor_qty=3))
		self.assertTrue(any("90 degrees" in w for w in result["warnings"]))
		clean = price_configuration(PDT_0040, MOST_COMMON)
		self.assertFalse(any("90 degrees" in w for w in clean["warnings"]))


class TestValidation(unittest.TestCase):
	def test_qty_out_of_range(self):
		errors = validate_selections(PDT_0040["options"], dict(MOST_COMMON, estop_qty=3))
		self.assertTrue(any("E-Stop Button Qty" in e for e in errors))
		with self.assertRaises(ValueError):
			price_configuration(PDT_0040, dict(MOST_COMMON, estop_qty=3))

	def test_missing_choice(self):
		bad = dict(MOST_COMMON)
		del bad["mounting"]
		errors = validate_selections(PDT_0040["options"], bad)
		self.assertTrue(any("E-Stop Mounting" in e for e in errors))

	def test_unknown_choice_code(self):
		errors = validate_selections(PDT_0040["options"], dict(MOST_COMMON, mounting="4"))
		self.assertTrue(any("E-Stop Mounting" in e for e in errors))

	def test_non_integer_qty(self):
		errors = validate_selections(PDT_0040["options"], dict(MOST_COMMON, timer_qty="lots"))
		self.assertTrue(any("Timer & Button Qty" in e for e in errors))


class TestConditions(unittest.TestCase):
	CTX = {"timer_qty": 2, "estop_qty": 1, "mounting": "2", "relay_qty": 0}

	def test_comparisons_and_arithmetic(self):
		self.assertTrue(safe_eval_expr("timer_qty == 2", self.CTX))
		self.assertFalse(safe_eval_expr("timer_qty == 1", self.CTX))
		self.assertTrue(safe_eval_expr('mounting == "2"', self.CTX))
		self.assertTrue(safe_eval_expr("estop_qty + timer_qty >= 3", self.CTX))
		self.assertTrue(safe_eval_expr('mounting in ("2", "3")', self.CTX))
		self.assertTrue(safe_eval_expr("not relay_qty", self.CTX))
		self.assertTrue(safe_eval_expr("", self.CTX), "empty condition means always")

	def test_disallowed_syntax_rejected(self):
		for expr in (
			"__import__('os')",
			"().__class__",
			"timer_qty.__class__",
			"[x for x in (1,)]",
			"lambda: 1",
			"timer_qty ** 99",
			"f'{timer_qty}'",
			"unknown_name == 1",
			"timer_qty ==",
		):
			with self.assertRaises(ConditionError, msg=expr):
				safe_eval_expr(expr, self.CTX)

	def test_render_text(self):
		self.assertEqual(
			render_text("Insert {estop_qty + timer_qty} glands ({mounting_label})",
				dict(self.CTX, mounting_label="Surface")),
			"Insert 3 glands (Surface)",
		)


class TestPartNumber(unittest.TestCase):
	def test_context_and_template(self):
		ctx = build_context(PDT_0040["options"], EXAMPLE_SURFACE)
		self.assertEqual(ctx["mounting"], "2")
		self.assertEqual(ctx["mounting_label"], "Surface")
		self.assertEqual(ctx["timer_qty"], 1)
		self.assertEqual(
			build_part_number(PDT_0040["part_number_template"], ctx), "PDT-0040-2-1-1-2-1"
		)

	def test_unresolved_token_raises(self):
		with self.assertRaises(ConditionError):
			build_part_number("PDT-0040-{nope}", {"mounting": "1"})


class TestBuildSteps(unittest.TestCase):
	def _steps(self, selections):
		result = price_configuration(PDT_0040, selections)
		rendered, warnings = render_build_steps(PDT_0040["build_steps"], result["context"])
		self.assertEqual(warnings, [])
		return rendered

	def test_timer_terminal_branching(self):
		text_for = lambda steps: " | ".join(s["instruction"] for s in steps)
		one = text_for(self._steps(dict(MOST_COMMON, timer_qty=1)))
		two = text_for(self._steps(dict(MOST_COMMON, timer_qty=2)))
		three = text_for(self._steps(dict(MOST_COMMON, timer_qty=3)))
		self.assertIn("use double-pole terminals", one)
		self.assertNotIn("triple-pole", one)
		self.assertIn("use triple-pole terminals", two)
		self.assertIn("2 sets of double-pole terminals", three)

	def test_zero_timers_drops_timer_sections(self):
		steps = self._steps(dict(MOST_COMMON, timer_qty=0))
		sections = {s["section_title"] for s in steps}
		self.assertNotIn("Machining — Timer Button", sections)
		self.assertNotIn("Panelbuilding — Timers", sections)
		self.assertNotIn("QC — Timer", sections)

	def test_quantity_placeholders_rendered(self):
		steps = self._steps(dict(MOST_COMMON, timer_qty=2, relay_qty=1))
		gland = next(s for s in steps if "cable glands" in s["instruction"])
		self.assertIn("(3 total)", gland["instruction"])
		terminal = next(
			s for s in steps if "terminal block per contactor and relay" in s["instruction"]
		)
		self.assertIn("(3 total)", terminal["instruction"])

	def test_qc_steps_typed(self):
		steps = self._steps(MOST_COMMON)
		qc = [s for s in steps if s["step_type"] == "QC"]
		self.assertTrue(qc)
		self.assertTrue(all(s["section_title"].startswith("QC") for s in qc))

	def test_bad_condition_warns_and_skips(self):
		rendered, warnings = render_build_steps(
			[{"section_title": "X", "condition": "nope == 1", "instruction": "hi"}],
			{"timer_qty": 1},
		)
		self.assertEqual(rendered, [])
		self.assertEqual(len(warnings), 1)

	def test_bad_placeholder_warns_but_keeps_step(self):
		rendered, warnings = render_build_steps(
			[{"section_title": "X", "instruction": "count {nope}"}], {"timer_qty": 1}
		)
		self.assertEqual(len(rendered), 1)
		self.assertEqual(rendered[0]["instruction"], "count {nope}")
		self.assertEqual(len(warnings), 1)


class TestPartsExplosion(unittest.TestCase):
	def test_most_common_explosion(self):
		result = price_configuration(PDT_0040, MOST_COMMON)
		parts = explode_parts(PDT_0040["components"], result["module_qtys"])
		by_code = {}
		for p in parts:
			by_code[p["item_code"]] = by_code.get(p["item_code"], 0) + p["qty"]
		self.assertEqual(by_code["AF12Z-30-10-21"], 2)  # 2 contactors
		self.assertEqual(by_code["PC-TERM-3P"], 3)  # 3 per e-stop x 1
		# 2-pole terminal: 2/timer + 1/contactor, relay module absent
		self.assertEqual(by_code["1SNA115271R2200"], 2 * 1 + 1 * 2)
		self.assertNotIn("RV1H-G-D24", by_code)  # no relays
		self.assertEqual(by_code["PC-LABEL"], 3)

	def test_amounts(self):
		result = price_configuration(PDT_0040, MOST_COMMON)
		parts = explode_parts(PDT_0040["components"], result["module_qtys"])
		contactor = next(p for p in parts if p["item_code"] == "AF12Z-30-10-21")
		self.assertAlmostEqual(contactor["amount"], 2 * 118.41, places=6)


if __name__ == "__main__":
	unittest.main()
