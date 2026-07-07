"""Seed the PDT-0040 STILLWATER E-Stop Configurable Product (v1.142.0).

Creates the product definition (options, components, build-step templates)
from ``product_configurator.seed_data`` — the same dict the bench-free golden
tests price against, so the seeded product provably reproduces the source
pricing workbook (1685.008 / 1512.979).

Insert-only: if PDT-0040 already exists the patch does nothing, so shop edits
to costs or steps survive every future migrate. No ERPNext masters (Items /
Suppliers) are touched here — those are created on demand by the "Create
Component Items" button once the module is switched on.
"""

import frappe

from erpnext_enhancements.product_configurator.seed_data import PDT_0040


def execute():
	if not frappe.db.exists("DocType", "Configurable Product"):
		return
	if frappe.db.exists("Configurable Product", PDT_0040["product_code"]):
		return

	doc = frappe.get_doc(
		{
			"doctype": "Configurable Product",
			"product_code": PDT_0040["product_code"],
			"product_name": PDT_0040["product_name"],
			"description": PDT_0040["description"],
			"labor_rate": PDT_0040["labor_rate"],
			"markup_percent": PDT_0040["markup_percent"],
			"part_number_template": PDT_0040["part_number_template"],
			"item_group": PDT_0040["item_group"],
			"component_item_group": PDT_0040["component_item_group"],
			"options": PDT_0040["options"],
			"components": PDT_0040["components"],
			"build_steps": PDT_0040["build_steps"],
		}
	)
	doc.insert(ignore_permissions=True)
