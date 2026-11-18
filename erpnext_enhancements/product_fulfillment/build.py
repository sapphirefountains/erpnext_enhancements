# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Building product units from Work Orders (TASK-2026-03229).

A configured product line on a submitted Sales Order gets one Work Order per unit to build, from the
Item's default BOM (the configurator submits one when it generates the Item). When finished units of
the same Item sit on the shelf and no other open order claims them, the line ships from stock
instead (Nikolas, 2026-11-18: 2-3 built-ahead units of ``PDT-0040-1-1-1-2-0``, everything else
built to order). ``rules.plan_line`` decides; this module gathers the facts and makes the records.

**One Work Order per unit**, not one per line. Each unit then has its own builder, its own
checklist and its own serial number, and the person who built it can be told apart from the person
who tests it, which is the point of the QC gate. It also keeps ERPNext's Quality Inspection usable:
an inspection names one row of the Manufacture entry it was made from, and a Work Order for one unit
has one finished row with one serial.

* ``on_sales_order_submit`` (doc_event): plans every configured product line and makes its Work
  Orders. Never stops the Sales Order: a failure is rolled back to a savepoint, logged, and shown
  as an orange message, and the Sales Order's **Make Work Orders** button runs the same plan again.
  Running it twice builds nothing twice (``rules.planned_qty``).
* ``make_work_orders`` (whitelisted): that button.
* ``on_sales_order_validate`` (doc_event): a configured product line on a draft order delivers
  from the finished units warehouse, when one is set, so the Delivery Note looks where the units are.
* ``fill_checklist`` (Work Order ``before_insert``): copies the configuration's build steps onto the
  Work Order, whoever made it (this module, or ERPNext's own Sales Order > Work Order button).

The Work Orders are made as the system, not as the person submitting the Sales Order: a salesperson
holds no Work Order rights, and the build is a consequence of a sale they were allowed to submit.
They are submitted at once (released to the shop) when a work-in-progress and a finished warehouse
resolve, and left as drafts, with a message saying what to set, when they do not.
"""

import frappe
from frappe import _
from frappe.utils import flt

from erpnext_enhancements.product_fulfillment import rules
from erpnext_enhancements.product_fulfillment.settings import (
	get_settings,
	has_column,
	item_facts,
	manufacturing_defaults,
	needs_test,
)

WORK_ORDER = "Work Order"
SALES_ORDER = "Sales Order"


# ---------------------------------------------------------------------------------------------
# Sales Order hooks
# ---------------------------------------------------------------------------------------------


def on_sales_order_validate(doc, method=None):
	"""A configured product line on a draft order delivers from the finished units warehouse.

	Only when Product Build Settings names one, and only on a draft: a submitted order's warehouse
	is ERPNext's to keep. Never raises.
	"""
	try:
		if int(doc.docstatus or 0) != 0:
			return
		finished = get_settings().finished_warehouse
		if not finished:
			return
		facts = item_facts([row.item_code for row in doc.get("items") or ()])
		for row in doc.get("items") or ():
			if needs_test(facts.get(row.item_code)) and row.warehouse != finished:
				row.warehouse = finished
	except Exception:
		frappe.log_error(title="Product Fulfillment: product line warehouse", message=frappe.get_traceback())


def on_sales_order_submit(doc, method=None):
	"""Plan the order's configured product lines and make their Work Orders. Never raises."""
	settings = get_settings()
	if not int(settings.make_work_orders or 0):
		return
	if not _configured_lines(doc):
		return
	frappe.db.savepoint("product_fulfillment_work_orders")
	try:
		result = plan_and_make(doc, settings, ignore_permissions=True)
	except Exception:
		frappe.db.rollback(save_point="product_fulfillment_work_orders")
		frappe.log_error(
			title=f"Product Fulfillment: Work Orders for {doc.name}", message=frappe.get_traceback()
		)
		frappe.msgprint(
			_(
				"The Sales Order is submitted, but its Work Orders could not be made. Use Make Work "
				"Orders on the order to try again; the error is in the Error Log."
			),
			indicator="orange",
			alert=True,
		)
		return
	_announce(result)


@frappe.whitelist(methods=["POST"])
def make_work_orders(sales_order):
	"""The Sales Order's Make Work Orders button: the same plan, as the person pressing it."""
	doc = frappe.get_doc(SALES_ORDER, sales_order)
	doc.check_permission("read")
	if int(doc.docstatus or 0) != 1:
		frappe.throw(_("Submit the Sales Order first."))
	if doc.status in ("Closed", "Completed", "On Hold"):
		frappe.throw(_("The Sales Order is {0}; it takes no more Work Orders.").format(_(doc.status)))
	if not frappe.has_permission(WORK_ORDER, "create"):
		frappe.throw(_("You need permission to create Work Orders."), frappe.PermissionError)
	result = plan_and_make(doc, get_settings(), ignore_permissions=False)
	_announce(result)
	return result


# ---------------------------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------------------------


def _configured_lines(doc):
	facts = item_facts([row.item_code for row in doc.get("items") or ()])
	return [
		(row, facts[row.item_code]) for row in doc.get("items") or () if needs_test(facts.get(row.item_code))
	]


def plan_and_make(doc, settings, ignore_permissions=False):
	"""Plan each configured product line and make the Work Orders it needs.

	Returns ``{"created": [names], "submitted": [names], "drafts": [names], "from_stock":
	[{item_code, qty}], "missing_bom": [item_code], "missing": [warehouse labels]}``.
	"""
	result = {
		"created": [],
		"submitted": [],
		"drafts": [],
		"from_stock": [],
		"missing_bom": [],
		"missing": [],
	}
	manufacturing = manufacturing_defaults()
	for row, facts in _configured_lines(doc):
		bom = facts.get("default_bom")
		if not bom:
			result["missing_bom"].append(row.item_code)
			continue
		warehouses = rules.resolve_warehouses(settings, row.warehouse, manufacturing)
		pending = max(0.0, flt(row.stock_qty) - flt(row.delivered_qty) * (flt(row.conversion_factor) or 1.0))
		planned = rules.planned_qty(_line_work_orders(row.name))
		free = rules.free_stock(
			_on_hand(row.item_code, warehouses["fg"]), _claims(row.item_code, warehouses["fg"], doc.name)
		)
		plan = rules.plan_line(pending, free, planned)
		if plan["from_stock"]:
			result["from_stock"].append({"item_code": row.item_code, "qty": plan["from_stock"]})
		for _unit in range(plan["to_build"]):
			wo = _make_work_order(doc, row, bom, warehouses, ignore_permissions)
			result["created"].append(wo.name)
			(result["submitted"] if wo.docstatus == 1 else result["drafts"]).append(wo.name)
			if wo.docstatus == 0:
				for label in warehouses["missing"]:
					if label not in result["missing"]:
						result["missing"].append(label)
	return result


def _line_work_orders(so_item):
	return frappe.get_all(
		WORK_ORDER,
		filters={"sales_order_item": so_item, "docstatus": ("<", 2)},
		fields=["name", "status", "docstatus", "qty", "produced_qty"],
	)


def _on_hand(item_code, warehouse):
	"""Finished units of the Item: in the finished warehouse when there is one, else everywhere."""
	if warehouse:
		value = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty")
		return flt(value)
	rows = frappe.db.sql(
		"SELECT COALESCE(SUM(actual_qty), 0) FROM `tabBin` WHERE item_code = %(item)s",
		{"item": item_code},
	)
	return flt(rows[0][0]) if rows else 0.0


def _claims(item_code, warehouse, this_order):
	"""What other open orders still have to deliver of the Item from the shelf.

	Lines of submitted Sales Orders that are not Closed, Completed or Cancelled, for the same Item
	(and the same warehouse, when there is one), that have no Work Order of their own. An order being
	built for claims its own unit, not the shelf's (``product_orders.allocate_stock``).
	"""
	where_warehouse = "AND soi.warehouse = %(warehouse)s" if warehouse else ""
	rows = frappe.db.sql(
		f"""
		SELECT soi.stock_qty - soi.delivered_qty * COALESCE(NULLIF(soi.conversion_factor, 0), 1) AS pending
		FROM `tabSales Order Item` soi
		INNER JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE soi.item_code = %(item)s
			AND so.docstatus = 1
			AND so.status NOT IN ('Closed', 'Completed', 'Cancelled')
			AND so.name != %(order)s
			{where_warehouse}
			AND NOT EXISTS (
				SELECT 1 FROM `tabWork Order` wo
				WHERE wo.sales_order_item = soi.name AND wo.docstatus < 2 AND wo.status != 'Cancelled'
			)
		""",
		{"item": item_code, "order": this_order, "warehouse": warehouse},
		as_dict=True,
	)
	return [max(0.0, flt(row.pending)) for row in rows]


def _configuration_of(row):
	"""The Product Configuration a line was quoted from, else the one its Item was generated from.

	Quote to order (#1465) copies ``custom_product_configuration`` onto the Sales Order line; the Item
	alone cannot tell two configurations with the same part number apart, but it names the one it was
	generated from.
	"""
	config = (
		row.get("custom_product_configuration")
		if has_column("Sales Order Item", "custom_product_configuration")
		else None
	)
	if config:
		return config
	if has_column("Item", "custom_source_configuration"):
		return frappe.db.get_value("Item", row.item_code, "custom_source_configuration")
	return None


def _make_work_order(doc, row, bom, warehouses, ignore_permissions):
	wo = frappe.get_doc(
		{
			"doctype": WORK_ORDER,
			"production_item": row.item_code,
			"bom_no": bom,
			"qty": 1,
			"company": doc.company,
			"sales_order": doc.name,
			"sales_order_item": row.name,
			"project": doc.get("project"),
			"description": row.get("description"),
			"source_warehouse": warehouses["source"],
			"wip_warehouse": warehouses["wip"],
			"fg_warehouse": warehouses["fg"],
			"expected_delivery_date": row.get("delivery_date") or doc.get("delivery_date"),
		}
	)
	config = _configuration_of(row)
	if config and has_column(WORK_ORDER, "custom_build_configuration"):
		wo.custom_build_configuration = config
	wo.flags.ignore_permissions = ignore_permissions
	wo.insert()
	# ERPNext fills a blank warehouse from the Company's defaults on validate, so ask the record.
	if wo.wip_warehouse and wo.fg_warehouse:
		wo.flags.ignore_permissions = ignore_permissions
		wo.submit()
	return wo


def _announce(result):
	if not result:
		return
	bits = []
	if result["submitted"]:
		bits.append(_("Work Orders started: {0}.").format(", ".join(result["submitted"])))
	if result["drafts"]:
		bits.append(
			_(
				"Work Orders left as drafts: {0}. Set the {1} warehouse in Product Build Settings, then submit them."
			).format(", ".join(result["drafts"]), _(" and ").join(result["missing"]) or _("work-in-progress"))
		)
	for line in result["from_stock"]:
		bits.append(
			_("{0}: {1} from finished units on the shelf.").format(line["item_code"], int(line["qty"]))
		)
	if result["missing_bom"]:
		bits.append(
			_(
				"No default BOM, so nothing was built for: {0}. Generate the Item and BOM from its configuration."
			).format(", ".join(result["missing_bom"]))
		)
	if bits:
		frappe.msgprint("<br>".join(bits), title=_("Product build"), indicator="blue")


# ---------------------------------------------------------------------------------------------
# The checklist snapshot
# ---------------------------------------------------------------------------------------------


def fill_checklist(doc, method=None):
	"""Work Order ``before_insert``: copy the configuration's build steps onto a new Work Order.

	Whoever made the Work Order: this module, or ERPNext's own Sales Order > Work Order button. Only
	for a configured product whose Work Order has no steps yet; an amendment keeps the steps it was
	copied with. Never raises: a Work Order without a checklist is still a Work Order.
	"""
	try:
		if not has_column(WORK_ORDER, "custom_build_configuration"):
			return
		if doc.get("custom_build_steps"):
			return
		config = doc.get("custom_build_configuration")
		if (
			not config
			and doc.get("sales_order_item")
			and has_column("Sales Order Item", "custom_product_configuration")
		):
			config = frappe.db.get_value(
				"Sales Order Item", doc.sales_order_item, "custom_product_configuration"
			)
		if not config and has_column("Item", "custom_source_configuration"):
			config = frappe.db.get_value("Item", doc.production_item, "custom_source_configuration")
		if not config or not frappe.db.exists("Product Configuration", config):
			return
		doc.custom_build_configuration = config
		steps = frappe.get_all(
			"Product Configuration Build Step",
			filters={"parent": config, "parenttype": "Product Configuration"},
			fields=["section_title", "step_type", "instruction"],
			order_by="idx asc",
		)
		for step in rules.checklist_rows(steps):
			doc.append("custom_build_steps", step)
	except Exception:
		frappe.log_error(title="Product Fulfillment: build checklist", message=frappe.get_traceback())
