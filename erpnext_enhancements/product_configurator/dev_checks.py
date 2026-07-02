# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Self-contained verification functions for a dev bench (``bench execute``).

``bench run-tests`` is broken under Python 3.14 on the dev bench, so these are
plain functions that create data, assert, and return a result string:

    bench --site dev.localhost execute \
        erpnext_enhancements.product_configurator.dev_checks.check_golden_pricing
    ... dev_checks.check_config_roundtrip
    ... dev_checks.check_build_step_conditions
    ... dev_checks.check_generation          # DEV SITES ONLY — creates masters
    ... dev_checks.cleanup_generation_artifacts

The pure-engine goldens also run bench-free in CI
(tests/test_product_configurator_engine.py); these bench checks additionally
prove the *seeded DB rows* and the ERPNext generation path.
"""

import frappe
from frappe.utils import flt

PRODUCT = "PDT-0040"
GOLDEN_SURFACE = 1685.008  # PDT-0040-2-1-1-2-1 (pricing workbook example 1)
GOLDEN_COMMON = 1512.979  # PDT-0040-1-1-1-2-0 (pricing workbook example 2)


def _product_dict():
	from erpnext_enhancements.product_configurator.doctype.product_configuration.product_configuration import (
		product_as_engine_dict,
	)

	return product_as_engine_dict(frappe.get_doc("Configurable Product", PRODUCT))


def check_golden_pricing():
	"""Price both workbook examples from the SEEDED DB rows (not seed_data.py)."""
	from erpnext_enhancements.product_configurator.engine import price_configuration

	product = _product_dict()
	surface = price_configuration(
		product, {"mounting": "2", "estop_qty": 1, "timer_qty": 1, "contactor_qty": 2, "relay_qty": 1}
	)
	common = price_configuration(
		product, {"mounting": "1", "estop_qty": 1, "timer_qty": 1, "contactor_qty": 2, "relay_qty": 0}
	)
	assert surface["part_number"] == "PDT-0040-2-1-1-2-1", surface["part_number"]
	assert round(surface["total_price"], 3) == GOLDEN_SURFACE, surface["total_price"]
	assert common["part_number"] == "PDT-0040-1-1-1-2-0", common["part_number"]
	assert round(common["total_price"], 3) == GOLDEN_COMMON, common["total_price"]
	return f"OK — seeded {PRODUCT} reproduces both workbook goldens ({GOLDEN_SURFACE} / {GOLDEN_COMMON})"


def check_config_roundtrip():
	"""A bare insert with just the product set yields the default config, priced."""
	doc = frappe.get_doc({"doctype": "Product Configuration", "product": PRODUCT})
	doc.insert(ignore_permissions=True)
	try:
		assert doc.part_number == "PDT-0040-1-1-1-2-0", doc.part_number
		assert round(doc.sell_price_exact, 3) == GOLDEN_COMMON, doc.sell_price_exact
		assert flt(doc.sell_price) == 1512.98, doc.sell_price
		assert doc.parts, "parts child table empty"
		assert doc.build_steps, "build steps child table empty"
		assert doc.price_lines, "price lines child table empty"
		qc = [s for s in doc.build_steps if s.step_type == "QC"]
		assert qc, "no QC steps resolved"
		return (
			f"OK — {doc.name}: {doc.part_number} @ {doc.sell_price} "
			f"({len(doc.parts)} parts, {len(doc.build_steps)} steps)"
		)
	finally:
		doc.delete(ignore_permissions=True)


def check_build_step_conditions():
	"""Timer-terminal branching: 1 → double-pole, 2 → triple-pole, 3 → 2× double."""

	def preview(timer_qty):
		rows = [
			{"option_key": "mounting", "option_type": "Choice", "choice_code": "1", "selected": 1},
			{"option_key": "estop_qty", "option_type": "Quantity", "qty": 1},
			{"option_key": "timer_qty", "option_type": "Quantity", "qty": timer_qty},
			{"option_key": "contactor_qty", "option_type": "Quantity", "qty": 2},
			{"option_key": "relay_qty", "option_type": "Quantity", "qty": 0},
		]
		doc = frappe.get_doc({"doctype": "Product Configuration", "product": PRODUCT})
		for r in rows:
			doc.append("options", r)
		doc.sync_option_rows()
		doc.recompute()
		return " | ".join(s.instruction for s in doc.build_steps)

	assert "use double-pole terminals" in preview(1)
	assert "use triple-pole terminals" in preview(2)
	assert "2 sets of double-pole terminals" in preview(3)
	assert "timer button" not in preview(0).lower()
	return "OK — timer-terminal branching and zero-timer drop verified"


def check_generation():
	"""End-to-end generation on a DEV site: Item + submitted default BOM + price.

	Temporarily flips the master switch on, generates, asserts, and leaves the
	records in place for desk inspection (run cleanup_generation_artifacts to
	remove the configured item/BOM/price + config; component items stay).
	"""
	from erpnext_enhancements.product_configurator import erp_integration

	prior = frappe.db.get_single_value(
		"ERPNext Enhancements Settings", "product_configurator_enabled"
	)
	frappe.db.set_single_value(
		"ERPNext Enhancements Settings", "product_configurator_enabled", 1
	)
	try:
		cfg = frappe.get_doc({"doctype": "Product Configuration", "product": PRODUCT})
		cfg.insert(ignore_permissions=True)
		out = erp_integration.generate(cfg.name)

		item = frappe.get_doc("Item", out["item"])
		assert item.item_group == "Configured Products", item.item_group
		assert item.custom_source_configuration == cfg.name

		bom = frappe.get_doc("BOM", out["bom"])
		assert bom.docstatus == 1 and bom.is_active and bom.is_default
		assert frappe.db.get_value("Item", out["item"], "default_bom") == bom.name
		codes = [r.item_code for r in bom.items]
		assert len(codes) == len(set(codes)), "duplicate BOM rows not aggregated"
		assert flt(bom.raw_material_cost) > 0, "BOM cost is zero — valuation fallback broken"

		price = frappe.get_doc("Item Price", out["item_price"])
		assert flt(price.price_list_rate) == 1512.98, price.price_list_rate

		# regenerate converges (same BOM, same price row)
		out2 = erp_integration.generate(cfg.name)
		assert out2["bom"] == out["bom"] and out2["item_price"] == out["item_price"]

		return (
			f"OK — {out['item']} / {out['bom']} / {out['item_price']} "
			f"(BOM material cost {bom.raw_material_cost}); regenerate converged. "
			f"Run cleanup_generation_artifacts to remove."
		)
	finally:
		frappe.db.set_single_value(
			"ERPNext Enhancements Settings", "product_configurator_enabled", prior or 0
		)


def cleanup_generation_artifacts():
	"""Remove check_generation leftovers (configured item/BOMs/price + configs)."""
	removed = []
	for cfg_name in frappe.get_all(
		"Product Configuration", filters={"product": PRODUCT}, pluck="name"
	):
		item_code = frappe.db.get_value("Product Configuration", cfg_name, "item")
		if item_code:
			for bom_name in frappe.get_all("BOM", filters={"item": item_code}, pluck="name"):
				bom = frappe.get_doc("BOM", bom_name)
				if bom.docstatus == 1:
					bom.cancel()
				bom.delete(ignore_permissions=True)
				removed.append(bom_name)
			for price_name in frappe.get_all(
				"Item Price", filters={"item_code": item_code}, pluck="name"
			):
				frappe.delete_doc("Item Price", price_name, ignore_permissions=True)
			frappe.db.set_value("Product Configuration", cfg_name, "item", None)
			frappe.delete_doc("Item", item_code, ignore_permissions=True)
			removed.append(item_code)
		frappe.delete_doc("Product Configuration", cfg_name, ignore_permissions=True)
		removed.append(cfg_name)
	return f"Removed: {', '.join(removed) if removed else 'nothing to clean'}"
