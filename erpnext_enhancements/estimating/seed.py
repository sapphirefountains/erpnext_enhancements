# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What the estimating seed patches create, and the one wrapper they all run through.

**A seed patch here cannot raise.** A patch that raises aborts ``bench migrate``, and on this repo
that is the deploy (CLAUDE.md: v1.395.0 left prod half-migrated that way). So each step runs inside
:func:`run`: committed when it works, rolled back when it does not, and a failure is one Error Log
row titled ``Estimating seed: <step>`` that says what to do by hand. This is the same shape as
``patches/seed_warehouse_map_editor_role.py`` and the other role seeds. A patch runs once, so a
step that failed stays failed until somebody does it by hand; the Error Log says how.

The Item ``EST-SCOPE`` and its zero price live here rather than in the patch because
``api/estimating.issue_quotation`` uses the same price helper on every issue (see
:func:`hold_zero_price`).
"""

import frappe

#: The one non-stock Item every quotation line an Estimate issues uses.
SCOPE_ITEM = "EST-SCOPE"

#: Its name, in the Item Naming Schema's shape (POL-0602): uppercase, comma-separated segments,
#: CATEGORY first. ``SERVICE`` is not yet on Appendix A, whose categories are all physical parts, so
#: the naming audit still reports ``name_category_unapproved`` for this one Item until the Process
#: Owner adds a service category (a one-line change to ``item_naming_rules``). The two fix-level
#: findings the first name had (not uppercase, no comma) are gone.
SCOPE_ITEM_NAME = "SERVICE, ESTIMATE SCOPE"

SCOPE_ITEM_DESCRIPTION = (
	"A phase or line of an Estimate, priced on the Estimate. Used only on quotations an Estimate issues."
)


def run(patch, step, fn, by_hand):
	"""Run one seed step; never raise. ``by_hand`` says what to do if it fails."""
	try:
		result = fn()
		frappe.db.commit()
		print(f"{patch}: {step}: {result}")
		return result
	except Exception:
		try:
			frappe.db.rollback()
			frappe.log_error(
				title=f"Estimating seed: {step}",
				message=f"{patch}: {step} failed. By hand: {by_hand}\n\n{frappe.get_traceback()}",
			)
			frappe.db.commit()
		except Exception:
			# The database went away mid-migrate. The estimating module then lacks a seed record,
			# which the Estimate form and Issue Quotation both say plainly; quieter than a broken deploy.
			pass
		print(f"{patch}: {step}: failed, see the Error Log")
		return None


def scope_uom():
	"""The unit for EST-SCOPE: "Unit" (enabled on prod; "Nos" is disabled there), else "Nos"."""
	for uom in ("Unit", "Nos"):
		if frappe.db.get_value("UOM", uom, "enabled"):
			return uom
	return None


def scope_group():
	if frappe.db.exists("Item Group", "Service"):
		return "Service"
	return frappe.db.get_value("Item Group", {"is_group": 0}, "name", order_by="lft asc")


def seed_scope_item():
	if frappe.db.exists("Item", SCOPE_ITEM):
		return "already there"
	group, uom = scope_group(), scope_uom()
	if not group or not uom:
		raise frappe.ValidationError(f"no leaf Item Group ({group}) or no enabled UOM Unit/Nos ({uom})")
	item = frappe.new_doc("Item")
	item.item_code = SCOPE_ITEM
	item.item_name = SCOPE_ITEM_NAME
	item.description = SCOPE_ITEM_DESCRIPTION
	item.item_group = group
	item.stock_uom = uom
	item.is_stock_item = 0
	item.include_item_in_manufacturing = 0
	item.is_purchase_item = 0
	item.is_sales_item = 1
	item.insert(ignore_permissions=True)
	return "created"


def default_selling_price_list():
	name = frappe.db.get_single_value("Selling Settings", "selling_price_list")
	if name and frappe.db.exists("Price List", name):
		return name
	return "Standard Selling" if frappe.db.exists("Price List", "Standard Selling") else None


def hold_zero_price(price_list):
	"""Make sure EST-SCOPE has a 0 Item Price on ``price_list``. Returns what it did.

	Why: prod has Stock Settings "Auto Insert Item Price If Missing" on. Without a price, the first
	Quotation an Estimate issued would make "EST-SCOPE / <price list>" at the first phase's price
	(ERPNext ``insert_item_price``, run from the Quotation's validate), and every later EST-SCOPE line
	somebody added by hand would default to that stranger's number. With a 0 price there, ERPNext
	finds one and inserts nothing ("Update Existing Price List Rate" is off on prod), and the
	estimate's own rates still win, because a 0 list price never overrides a line's rate.
	"""
	if not price_list or not frappe.db.exists("Item", SCOPE_ITEM):
		return "skipped: no price list or no item"
	uom = frappe.db.get_value("Item", SCOPE_ITEM, "stock_uom")
	if frappe.db.exists("Item Price", {"item_code": SCOPE_ITEM, "price_list": price_list, "uom": uom}):
		return "already there"
	price = frappe.new_doc("Item Price")
	price.item_code = SCOPE_ITEM
	price.price_list = price_list
	price.uom = uom
	price.price_list_rate = 0
	price.note = "Held at 0 on purpose: an Estimate sets each quotation line's price."
	price.insert(ignore_permissions=True)
	return "created"
