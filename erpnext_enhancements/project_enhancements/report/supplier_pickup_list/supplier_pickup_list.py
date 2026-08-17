# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Supplier Pickup List — everything still sitting at one vendor's counter.

The Pick Routing Map (``api/pickup_routing.py``) answers this question per *job*:
one project, every vendor it is waiting on, in driving order. This report answers
the other half of the same question — one *vendor*, every job — because that is
the sheet somebody actually carries to a will-call counter. A run to Automation
Direct should collect the parts for all four jobs waiting on them, and until now
finding those four meant opening four projects.

One row per unreceived Purchase Order line, newest promise date first.

**"Still at the supplier" is defined once, in ``procurement_project``, and this
report imports it** rather than restating the rule — the Pick Routing Map, the
Project form's "+ Purchase Receipt" button and this report must not disagree
about what is outstanding.

That definition is ``docstatus == 1``, ``status`` not in
``SETTLED_PO_STATUSES``, and quantity left on the line. The status half is not a
nicety. On production today, of the 123 submitted Purchase Orders reading
``per_received < 100``, **81 are Closed** — 161 item rows against 27 suppliers
that nobody is going to collect, versus 148 live ones. Selecting on the numeric
field alone would put more dead lines on the driver's sheet than real ones.
``Closed`` is a deliberate "stop chasing this" and ``Delivered`` is a drop-ship
that never comes here; the *Include Closed / Delivered* filter brings them back
for the office reconciling why an order was closed short, and defaults off.

**The line filter is not redundant with ``per_received < 100``.** The percentage
is a whole-order rollup: a two-line order half received reads 50 and both lines
qualify, one of which has nothing left to collect. The order-level test stays
because it is the shared rule and it lets MariaDB cut most orders before the
join; the line-level test is what makes each printed row true.

**Pending quantity is in the line's own UOM, never stock UOM.** ERPNext maintains
``Purchase Order Item.received_qty`` against ``qty`` (see
``Purchase Receipt.status_updater``, ``target_ref_field="qty"``), so
``qty - received_qty`` is a number in ``uom``. That is why UOM is a column and
not decoration: the live pending lines are mostly ``Unit`` but also ``FT`` and
``Square Foot``, and "6" at a trade counter is a different collection depending
on which.

**Project is read through the same union everything else here uses** — the
item-row ``project`` falling back to the header — because the two disagree on
real data. Of the 148 live pending lines, 40 have a blank item-row project and 32
of those sit under an order whose header names the job (v1.190.0 found the same
shape on 44 of 204 lines). Reading only the row would blank the Job column on a
fifth of the sheet.

``supplier_pickup_list.html`` beside this file is the print template
(TASK-2026-01588): Frappe's ``get_html_format`` picks up ``<report>.html`` from
the report folder automatically and uses it for Print and PDF.
"""

import frappe
from frappe import _

from erpnext_enhancements.procurement_project import SETTLED_PO_STATUSES


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Supplier"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 180},
		{"label": _("Purchase Order"), "fieldname": "purchase_order", "fieldtype": "Link", "options": "Purchase Order", "width": 140},
		{"label": _("Expected"), "fieldname": "schedule_date", "fieldtype": "Date", "width": 100},
		{"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 160},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 280},
		{"label": _("Pending Qty"), "fieldname": "pending_qty", "fieldtype": "Float", "width": 100},
		{"label": _("UOM"), "fieldname": "uom", "fieldtype": "Link", "options": "UOM", "width": 80},
		{"label": _("Ordered"), "fieldname": "qty", "fieldtype": "Float", "width": 90},
		{"label": _("Received"), "fieldname": "received_qty", "fieldtype": "Float", "width": 90},
		{"label": _("Job"), "fieldname": "project", "fieldtype": "Link", "options": "Project", "width": 120},
		{"label": _("PO Status"), "fieldname": "status", "fieldtype": "Data", "width": 130},
	]


def get_data(filters):
	conditions = [
		"po.docstatus = 1",
		"po.per_received < 100",
		"poi.qty - ifnull(poi.received_qty, 0) > 0",
	]
	values = {}

	if not filters.get("include_settled"):
		conditions.append("po.status not in %(settled)s")
		values["settled"] = SETTLED_PO_STATUSES

	if filters.get("supplier"):
		conditions.append("po.supplier = %(supplier)s")
		values["supplier"] = filters.get("supplier")

	if filters.get("project"):
		# Same union as api/pickup_routing._project_po_names: the row wins, the
		# header is the fallback, and a job named on only one of the two is still
		# found.
		conditions.append("ifnull(nullif(poi.project, ''), po.project) = %(project)s")
		values["project"] = filters.get("project")

	if filters.get("expected_by"):
		conditions.append("poi.schedule_date <= %(expected_by)s")
		values["expected_by"] = filters.get("expected_by")

	return frappe.db.sql(
		"""
		SELECT
			po.supplier,
			po.supplier_name,
			po.status,
			poi.parent AS purchase_order,
			poi.schedule_date,
			poi.item_code,
			poi.item_name,
			poi.uom,
			poi.qty,
			ifnull(poi.received_qty, 0) AS received_qty,
			poi.qty - ifnull(poi.received_qty, 0) AS pending_qty,
			ifnull(nullif(poi.project, ''), po.project) AS project
		FROM `tabPurchase Order` po
		INNER JOIN `tabPurchase Order Item` poi ON poi.parent = po.name
		WHERE {conditions}
		ORDER BY po.supplier ASC, poi.schedule_date ASC, poi.parent ASC, poi.idx ASC
		""".format(conditions=" AND ".join(conditions)),
		values,
		as_dict=True,
	)
