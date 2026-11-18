"""Build, test and ship a product unit: the rules (v1.826.0, TASK-2026-03229 / -03230).

Nikolas, 2026-11-18 (vault decisions/2026-11-18-estop-product-line.md): each E-Stop is built from
a Work Order, gets a serial number, and must pass the Pre-Shipment Functional Test before it ships.
Logan Penrod or Nathan Cox builds each unit and the other tests it: **the tester is never the
builder.** Two or three units of the common configuration wait on a shelf; the rest are built to
order. ``erpnext_enhancements/product_fulfillment/rules.py`` holds every decision, with no Frappe in
it, so this suite runs bench-free and stub-free.

What goes wrong without these checks, concretely:

* the person who built a unit signs its test, which is the one thing the shop asked never to
  happen, and a case or a stray space in the user id ("Logan@..." against "logan@...") lets it by;
* a unit that passed and then failed a re-test ships on the strength of the old pass, or one that
  failed and was fixed is held for ever on the strength of the old failure;
* ERPNext's "Auto Create Serial and Batch Bundle For Outward" (on, on prod) picks the oldest unit
  at submit, tested or not, because the gate never insisted on a serial per unit;
* planning a Sales Order twice builds every unit twice, or a shelf unit already promised to one
  order is promised again to the next;
* a hard-coded warehouse, or a Work Order submitted with none, which ERPNext refuses.

Run: python -m unittest erpnext_enhancements.tests.test_product_fulfillment_rules
"""

import unittest

from erpnext_enhancements import product_orders as po
from erpnext_enhancements.product_fulfillment import rules

LOGAN = "logan.penrod@example.com"
NATHAN = "nathan.cox@example.com"
TEMPLATE = "Pre-Shipment Functional Test - E-Stop"
ITEM = "PDT-0040-1-1-1-2-0"


def qi(status, by, day="2026-11-18", docstatus=1, template=TEMPLATE, item=ITEM, name=None, created=None):
	return {
		"name": name or f"QI-{status}-{by}-{day}",
		"status": status,
		"inspected_by": by,
		"report_date": day,
		"creation": created or f"{day} 09:00:00",
		"docstatus": docstatus,
		"quality_inspection_template": template,
		"item_code": item,
	}


class TestPlanningALine(unittest.TestCase):
	def test_a_built_ahead_unit_on_the_shelf_ships_from_stock(self):
		self.assertEqual(rules.plan_line(1, 2), {"from_stock": 1, "to_build": 0})

	def test_nothing_on_the_shelf_builds_every_unit(self):
		self.assertEqual(rules.plan_line(3, 0), {"from_stock": 0, "to_build": 3})

	def test_a_shortfall_is_built(self):
		self.assertEqual(rules.plan_line(3, 1), {"from_stock": 1, "to_build": 2})

	def test_planning_again_never_builds_twice(self):
		self.assertEqual(rules.plan_line(2, 0, planned_qty=2), {"from_stock": 0, "to_build": 0})
		self.assertEqual(rules.plan_line(3, 0, planned_qty=2), {"from_stock": 0, "to_build": 1})

	def test_a_fraction_rounds_up_to_a_unit_to_build(self):
		self.assertEqual(rules.plan_line(1.5, 0)["to_build"], 2)
		self.assertEqual(rules.plan_line(1.0000000001, 0)["to_build"], 1, "float noise is not a unit")

	def test_free_stock_leaves_out_other_orders_claims(self):
		self.assertEqual(rules.free_stock(3, [1, 1]), 1)
		self.assertEqual(rules.free_stock(1, [2]), 0, "never negative")
		self.assertEqual(rules.free_stock(None, None), 0)

	def test_a_line_with_no_work_order_claims_what_it_still_has_to_deliver(self):
		self.assertEqual(rules.line_claim(2, False), 2)
		self.assertEqual(rules.line_claim(-1, False), 0)

	def test_a_line_being_built_claims_only_its_finished_undelivered_units(self):
		# Built for another order and waiting on the shelf: that unit is spoken for (review of #1470).
		self.assertEqual(rules.line_claim(1, True, produced_qty=1), 1)
		self.assertEqual(
			rules.line_claim(2, True, produced_qty=1), 1, "the unit still being built is not on the shelf"
		)
		self.assertEqual(rules.line_claim(1, True, produced_qty=2, delivered_qty=1), 1)
		self.assertEqual(rules.line_claim(1, True, produced_qty=0), 0)

	def test_planned_counts_what_a_closed_work_order_produced(self):
		work_orders = [
			{"status": "Not Started", "docstatus": 1, "qty": 1},
			{"status": "Draft", "docstatus": 0, "qty": 1},
			{"status": "Closed", "docstatus": 1, "qty": 2, "produced_qty": 1},
			{"status": "Cancelled", "docstatus": 2, "qty": 5},
		]
		self.assertEqual(rules.planned_qty(work_orders), 3)


class TestWarehouses(unittest.TestCase):
	def test_empty_settings_and_empty_manufacturing_defaults_leave_a_draft(self):
		answer = rules.resolve_warehouses({}, None, {})
		self.assertEqual((answer["source"], answer["wip"], answer["fg"]), (None, None, None))
		self.assertFalse(answer["can_submit"])
		self.assertEqual(answer["missing"], ["work in progress", "finished units"])

	def test_settings_come_first(self):
		settings = {
			"build_warehouse": "BR-1 - SF",
			"wip_warehouse": "WIP - SF",
			"finished_warehouse": "FG - SF",
		}
		answer = rules.resolve_warehouses(
			settings, "Line - SF", {"default_wip_warehouse": "X", "default_fg_warehouse": "Y"}
		)
		self.assertEqual(
			(answer["source"], answer["wip"], answer["fg"]), ("BR-1 - SF", "WIP - SF", "FG - SF")
		)
		self.assertTrue(answer["can_submit"])

	def test_then_the_order_line_then_manufacturing_settings(self):
		answer = rules.resolve_warehouses(
			{}, "Line - SF", {"default_wip_warehouse": "W", "default_fg_warehouse": "F"}
		)
		self.assertEqual((answer["wip"], answer["fg"]), ("W", "Line - SF"))
		answer = rules.resolve_warehouses(
			{}, None, {"default_wip_warehouse": "W", "default_fg_warehouse": "F"}
		)
		self.assertEqual(answer["fg"], "F")


class TestTheChecklist(unittest.TestCase):
	STEPS = [
		{"section_title": "Machining", "step_type": "Build", "instruction": "Drill the cover."},
		{"section_title": "Machining", "step_type": None, "instruction": "  Ream the hole.  "},
		{"section_title": "Panel", "step_type": "Build", "instruction": "   "},
		{"section_title": "QC - Timer", "step_type": "QC", "instruction": "Runs 15 minutes."},
	]

	def test_steps_are_copied_with_their_kind_and_blanks_dropped(self):
		rows = rules.checklist_rows(self.STEPS)
		self.assertEqual(
			[r["instruction"] for r in rows], ["Drill the cover.", "Ream the hole.", "Runs 15 minutes."]
		)
		self.assertEqual([r["step_type"] for r in rows], ["Build", "Build", "QC"])

	def test_progress_counts_build_steps_only(self):
		rows = rules.checklist_rows(self.STEPS)
		rows[0]["done"] = 1
		rows[2]["done"] = 1  # a QC note ticked somehow counts for nothing
		self.assertEqual(rules.checklist_progress(rows), {"done": 1, "total": 2, "complete": False})
		rows[1]["done"] = 1
		self.assertTrue(rules.checklist_progress(rows)["complete"])

	def test_no_steps_is_never_complete(self):
		self.assertEqual(rules.checklist_progress([]), {"done": 0, "total": 0, "complete": False})


class TestTheTesterIsNeverTheBuilder(unittest.TestCase):
	def test_someone_else_may_test(self):
		self.assertIsNone(rules.tester_problem(NATHAN, LOGAN))

	def test_the_builder_may_not(self):
		self.assertEqual(rules.tester_problem(LOGAN, LOGAN), rules.TESTER_IS_BUILDER)

	def test_case_and_spaces_do_not_make_two_people(self):
		self.assertEqual(rules.tester_problem(" Logan.Penrod@Example.com ", LOGAN), rules.TESTER_IS_BUILDER)

	def test_no_builder_means_nobody_can_show_it(self):
		self.assertEqual(rules.tester_problem(NATHAN, ""), rules.NO_BUILDER)
		self.assertEqual(rules.tester_problem(NATHAN, None), rules.NO_BUILDER)

	def test_no_tester(self):
		self.assertEqual(rules.tester_problem("", LOGAN), rules.NO_TESTER)

	def test_the_signer_is_the_tester(self):
		self.assertIsNone(rules.tester_problem(NATHAN, LOGAN, signer=NATHAN))
		self.assertEqual(rules.tester_problem(NATHAN, LOGAN, signer=LOGAN), rules.SIGNER_NOT_TESTER)

	def test_blanks_are_never_the_same_person(self):
		self.assertFalse(rules.same_person("", ""))
		self.assertFalse(rules.same_person(None, None))


class TestAUnitsState(unittest.TestCase):
	def test_untested(self):
		self.assertEqual(rules.unit_state([], LOGAN), rules.UNIT_UNTESTED)
		self.assertEqual(rules.unit_state(None, LOGAN), rules.UNIT_UNTESTED)

	def test_a_draft_or_cancelled_test_is_not_a_test(self):
		self.assertEqual(rules.unit_state([qi("Accepted", NATHAN, docstatus=0)], LOGAN), rules.UNIT_UNTESTED)
		self.assertEqual(rules.unit_state([qi("Cancelled", NATHAN, docstatus=2)], LOGAN), rules.UNIT_UNTESTED)

	def test_passed_by_someone_else(self):
		self.assertEqual(rules.unit_state([qi("Accepted", NATHAN)], LOGAN), rules.UNIT_PASSED)

	def test_passed_by_its_builder_does_not_stand(self):
		self.assertEqual(rules.unit_state([qi("Accepted", LOGAN)], LOGAN), rules.UNIT_TESTER_IS_BUILDER)

	def test_no_builder_does_not_stand(self):
		self.assertEqual(rules.unit_state([qi("Accepted", NATHAN)], None), rules.UNIT_NO_BUILDER)

	def test_the_newest_test_decides(self):
		failed_then_passed = [qi("Rejected", NATHAN, "2026-11-16"), qi("Accepted", NATHAN, "2026-11-18")]
		passed_then_failed = [qi("Accepted", NATHAN, "2026-11-16"), qi("Rejected", NATHAN, "2026-11-18")]
		self.assertEqual(rules.unit_state(failed_then_passed, LOGAN), rules.UNIT_PASSED)
		self.assertEqual(rules.unit_state(passed_then_failed, LOGAN), rules.UNIT_FAILED)

	def test_same_day_tests_go_by_when_they_were_made(self):
		tests = [
			qi("Accepted", NATHAN, created="2026-11-18 08:00:00", name="QI-1"),
			qi("Rejected", NATHAN, created="2026-11-18 15:00:00", name="QI-2"),
		]
		self.assertEqual(rules.unit_state(tests, LOGAN), rules.UNIT_FAILED)

	def test_only_the_pre_shipment_template_counts_when_one_is_set(self):
		other = [qi("Accepted", NATHAN, template="Incoming Components")]
		self.assertEqual(rules.unit_state(other, LOGAN, ITEM, TEMPLATE), rules.UNIT_UNTESTED)
		self.assertEqual(
			rules.unit_state(other, LOGAN, ITEM, None), rules.UNIT_PASSED, "no template: any counts"
		)

	def test_a_test_of_another_item_does_not_count(self):
		self.assertEqual(
			rules.unit_state([qi("Accepted", NATHAN, item="PDT-0040-2-1-0-0-0")], LOGAN, ITEM),
			rules.UNIT_UNTESTED,
		)


class TestTheDeliveryGate(unittest.TestCase):
	def row(self, serials, qty=1, idx=1):
		return {"idx": idx, "item_code": ITEM, "stock_qty": qty, "serials": serials}

	def test_a_tested_unit_ships(self):
		problems = rules.delivery_gate(
			[self.row(["SF-26-00001"])],
			{"SF-26-00001": [qi("Accepted", NATHAN)]},
			{"SF-26-00001": LOGAN},
			TEMPLATE,
		)
		self.assertEqual(problems, [])

	def test_every_unit_needs_a_serial_picked(self):
		problems = rules.delivery_gate(
			[self.row(["SF-26-00001"], qty=2)],
			{"SF-26-00001": [qi("Accepted", NATHAN)]},
			{"SF-26-00001": LOGAN},
		)
		self.assertEqual([p["code"] for p in problems], [rules.SERIALS_MISSING])
		problems = rules.delivery_gate([self.row([], qty=1)], {}, {})
		self.assertEqual([p["code"] for p in problems], [rules.SERIALS_MISSING])

	def test_each_serial_is_judged_on_its_own(self):
		problems = rules.delivery_gate(
			[self.row(["A", "B", "C", "D"], qty=4)],
			{
				"A": [qi("Accepted", NATHAN)],
				"B": [qi("Rejected", NATHAN)],
				"C": [qi("Accepted", LOGAN)],
				"D": [],
			},
			{"A": LOGAN, "B": LOGAN, "C": LOGAN, "D": LOGAN},
		)
		self.assertEqual(
			[(p["serial"], p["code"]) for p in problems],
			[("B", rules.UNIT_FAILED), ("C", rules.UNIT_TESTER_IS_BUILDER), ("D", rules.UNIT_UNTESTED)],
		)

	def test_blank_serials_are_not_serials(self):
		problems = rules.delivery_gate([self.row(["", "  "], qty=1)], {}, {})
		self.assertEqual([p["code"] for p in problems], [rules.SERIALS_MISSING])

	def test_every_code_has_a_sentence(self):
		for code in (
			rules.SERIALS_MISSING,
			rules.UNIT_UNTESTED,
			rules.UNIT_FAILED,
			rules.UNIT_TESTER_IS_BUILDER,
			rules.UNIT_NO_BUILDER,
		):
			self.assertTrue(rules.GATE_MESSAGES.get(code), code)

	def test_only_configured_stock_items_need_the_test(self):
		self.assertTrue(rules.needs_test(1, "PCFG-2026-00001"))
		self.assertFalse(rules.needs_test(0, "PCFG-2026-00001"), "a non-stock Item moves no unit")
		self.assertFalse(rules.needs_test(1, None), "a hand-made PDT Item is not the configurator's")
		self.assertFalse(rules.needs_test(1, "  "))


class TestTheTestingColumn(unittest.TestCase):
	def test_order_qc(self):
		self.assertIsNone(rules.order_qc([]))
		self.assertEqual(rules.order_qc([rules.UNIT_PASSED, rules.UNIT_PASSED]), po.QC_PASSED)
		self.assertEqual(rules.order_qc([rules.UNIT_PASSED, rules.UNIT_FAILED]), po.QC_FAILED)
		self.assertEqual(rules.order_qc([rules.UNIT_PASSED, rules.UNIT_UNTESTED]), po.QC_PENDING)
		self.assertEqual(rules.order_qc([rules.UNIT_TESTER_IS_BUILDER]), po.QC_PENDING)

	def test_stock_qc(self):
		self.assertEqual(rules.stock_qc(2, 1), po.QC_PASSED)
		self.assertEqual(rules.stock_qc(0, 1), po.QC_PENDING)
		self.assertIsNone(rules.stock_qc(3, 0))

	def test_the_stage_rule_reads_them(self):
		built = [{"status": "Completed", "docstatus": 1, "qty": 1, "produced_qty": 1}]
		self.assertEqual(
			po.order_stage(0, built, qc=rules.order_qc([rules.UNIT_UNTESTED]))["stage"], po.STAGE_TESTING
		)
		self.assertEqual(
			po.order_stage(0, built, qc=rules.order_qc([rules.UNIT_PASSED]))["stage"], po.STAGE_READY
		)
		self.assertEqual(
			po.order_stage(0, built, qc=rules.order_qc([rules.UNIT_FAILED]))["note"], "Test failed"
		)


class TestSerialNumbers(unittest.TestCase):
	def test_the_default_series_counts(self):
		self.assertTrue(rules.valid_serial_series(rules.DEFAULT_SERIAL_SERIES))
		self.assertEqual(rules.DEFAULT_SERIAL_SERIES, "SF-.YY.-.#####")

	def test_a_series_without_a_number_does_not(self):
		self.assertFalse(rules.valid_serial_series("SF-.YY.-"))
		self.assertFalse(rules.valid_serial_series(""))
		self.assertFalse(rules.valid_serial_series(None))

	def test_the_warranty_is_a_year(self):
		self.assertEqual(rules.DEFAULT_WARRANTY_DAYS, 365)


if __name__ == "__main__":
	unittest.main()
