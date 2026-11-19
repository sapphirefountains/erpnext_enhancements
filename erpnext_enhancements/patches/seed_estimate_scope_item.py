"""Seed the Item "EST-SCOPE" that an Estimate's quotation lines use (v1.836.0, estimating milestone 1).

An Estimate issues a native Quotation with one line per phase (concept flow step 4). A Quotation line
needs an Item, and a phase of a fountain is not a product, so every line uses this one non-stock,
sales-only service Item and carries the phase's name, scope text and price itself
(``api/estimating.issue_quotation``).

Insert-only. The item group is "Service" when it exists (it does on prod), else any leaf group, and
the unit "Nos" when it exists, else "Unit". Never a stock item, so it can never reach a warehouse.
"""

import frappe

ITEM = "EST-SCOPE"


def execute():
	if frappe.db.exists("Item", ITEM):
		return
	group = "Service" if frappe.db.exists("Item Group", "Service") else None
	if not group:
		group = frappe.db.get_value("Item Group", {"is_group": 0}, "name", order_by="lft asc")
	uom = "Nos" if frappe.db.exists("UOM", "Nos") else "Unit"
	if not group or not frappe.db.exists("UOM", uom):
		return
	item = frappe.new_doc("Item")
	item.item_code = ITEM
	item.item_name = "Estimated scope"
	item.description = (
		"A phase or line of an Estimate, priced on the Estimate. Used only on quotations an Estimate issues."
	)
	item.item_group = group
	item.stock_uom = uom
	item.is_stock_item = 0
	item.include_item_in_manufacturing = 0
	item.is_purchase_item = 0
	item.is_sales_item = 1
	item.insert(ignore_permissions=True)
