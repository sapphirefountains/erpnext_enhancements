# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Project Materials: what was bought for a job, and where each item stands (v1.805.0).

Nik, 2026-11-12: "I want to keep a historical record of each projects items and what was ordered
for them." Two views in one report, chosen by the Project filter.

**With a Project: one row per item.**

| Column | Read from |
|---|---|
| Ordered | the job's submitted Purchase Order lines, in the item's stock unit |
| Received | the job's submitted Purchase Receipt lines (a store run is a receipt with no order) |
| On Project Shelf | ``inventory_report_data.shelf_stock_by_project`` for this job |
| At Job Site | ``tabBin`` at the job's own site warehouse |
| Used on Job | the job's stock ledger: Material Issues, Delivery Notes and Sales Invoices, less parts brought back |
| Returned to Supplier | ERPNext's return receipts, plus the Supplier Return seam |
| Still to Come | what open order lines are short |
| Order Closed, Not Received | what lines on Closed or Delivered orders were short |
| Not Located | received and kept, but not on the shelf, at the site or used |

*Purchase Orders* and *Receipts* name the documents behind the row, and each name opens its
document. It works the same for a finished job: nothing here reads the job's status.

**Which lines are the job's.** A Purchase Order line's own Project, else its order's. The line's
is mandatory now, but older lines carry only the header's (Pending Items by Project found 32 of
production's 40 blank lines under an order that named the job). A receipt line's own Project,
else its receipt's, else the order line's it was received against, else that order's.

**Not Located is the honest column.** Most of what was bought for past jobs never entered stock:
on 2026-11-11, 1,102 of 1,586 enabled items had Maintain Stock off, and about 395 of the last
year's 431 receipt lines wrote no stock ledger row. For those, ordered and received are the whole
record and the note says "Not a stock item: where it is was never recorded". For a stock item it
is stock received into a shop bin, where stock is not kept by job (*Received Into* names the bin,
read from the stock ledger), or received before Maintain Stock was switched on.

**Stock Item is the item's flag today**, not what it was when the line was received.

**Without a Project: one row per job that physically holds something**, on the Project Shelf or at
its job site, with quantity and value for each. Shelf stock no job claims is counted in the note
over the table, not in a row. The Customer filter and "Include finished jobs" apply to this view.

**What is not read.** A Purchase Invoice that receives stock itself (*Update Stock*): none of
production's does. Rejected quantities on a receipt. Draft and canceled documents.

**A Script Report**: the Project filter is optional, and a Query Report cannot have an optional
filter on Frappe 16.53 and later. Opens at ``/desk/query-report/Project Materials``; another
page can route here with ``frappe.route_options = {project: ...}``.
"""

import frappe
from frappe import _

from erpnext_enhancements.inventory_enhancements import inventory_report_data as data
from erpnext_enhancements.inventory_enhancements import inventory_report_rules as rules
from erpnext_enhancements.inventory_enhancements import stock_scan_rules as scan_rules


def execute(filters=None):
	filters = frappe._dict(filters or {})
	where = data.places()
	project = (filters.get("project") or "").strip()
	if project:
		return one_project(project, filters, where)
	return every_project(filters, where)


# --- One project ---------------------------------------------------------------------------


def one_project(project, filters, where):
	info = data.project_info([project]).get(project)
	if not info:
		frappe.throw(_("There is no project {0}.").format(project))
	rows = project_rows(project, where)
	item_code = (filters.get("item_code") or "").strip()
	show = (filters.get("show") or "").strip() or rules.SHOW_ALL
	shown = [
		row for row in rows if (not item_code or row["item_code"] == item_code) and rules.show_row(row, show)
	]
	return item_columns(), shown, project_message(project, info, where), None, item_summary(rows)


def order_lines(project):
	"""The job's submitted Purchase Order lines. The line's own Project, else its order's."""
	return frappe.db.sql(
		"""
		select poi.item_code, poi.item_name, poi.parent as purchase_order, po.status as po_status,
			po.transaction_date, poi.qty, poi.stock_qty, poi.received_qty, poi.conversion_factor,
			poi.stock_uom, poi.base_net_amount as amount
		from `tabPurchase Order Item` poi
		join `tabPurchase Order` po on po.name = poi.parent
		where po.docstatus = 1
			and (
				poi.project = %(project)s
				or (coalesce(poi.project, '') = '' and po.project = %(project)s)
			)
		""",
		{"project": project},
		as_dict=True,
	)


def receipt_lines(project):
	"""The job's submitted Purchase Receipt lines, returns included (``is_return``, negative
	quantities). The line's own Project, else its receipt's, else the order line's it was
	received against, else that order's."""
	return frappe.db.sql(
		"""
		select pri.item_code, pri.item_name, pri.parent as purchase_receipt, pr.is_return,
			pr.posting_date, pri.qty, pri.stock_qty, pri.conversion_factor, pri.stock_uom
		from `tabPurchase Receipt Item` pri
		join `tabPurchase Receipt` pr on pr.name = pri.parent
		left join `tabPurchase Order Item` poi on poi.name = pri.purchase_order_item
		left join `tabPurchase Order` po on po.name = poi.parent
		where pr.docstatus = 1
			and coalesce(
				nullif(pri.project, ''), nullif(pr.project, ''), nullif(poi.project, ''), po.project
			) = %(project)s
		""",
		{"project": project},
		as_dict=True,
	)


def job_ledger(project):
	"""The job's live stock ledger rows, grouped the way ``rules.used_by_item`` and
	``rules.received_into`` read them."""
	return frappe.db.sql(
		"""
		select s.item_code, s.voucher_type, se.purpose, s.warehouse, sum(s.actual_qty) as qty
		from `tabStock Ledger Entry` s
		left join `tabStock Entry` se on s.voucher_type = 'Stock Entry' and se.name = s.voucher_no
		where s.is_cancelled = 0 and s.project = %(project)s
		group by s.item_code, s.voucher_type, se.purpose, s.warehouse
		""",
		{"project": project},
		as_dict=True,
	)


def project_rows(project, where):
	orders = order_lines(project)
	receipts = receipt_lines(project)
	shelf = {}
	if where.shelf:
		for share in data.shelf_stock_by_project(where.shelf, projects=[project]):
			shelf[share.get("item_code")] = shelf.get(share.get("item_code"), 0.0) + rules._num(
				share.get("qty")
			)
	site = {}
	for row in data.site_bins(where, projects=[project]):
		qty, value = site.get(row["item_code"], (0.0, 0.0))
		site[row["item_code"]] = (qty + row["qty"], value + row["value"])
	holding = set(where.sites)
	if where.shelf:
		holding.add(where.shelf)
	ledger = job_ledger(project)
	used = rules.used_by_item(ledger, holding=holding)
	into = {
		code: [where.names.get(name) or name for name in names]
		for code, names in rules.received_into(ledger, holding=holding).items()
	}
	returns = data.supplier_returns_by_item(project)
	codes = {line.get("item_code") for line in orders} | {line.get("item_code") for line in receipts}
	codes.update(shelf, site, used, returns)
	rows = rules.project_item_rows(
		orders,
		receipts,
		shelf=shelf,
		site=site,
		used=used,
		supplier_returns=returns,
		items=data.item_info(codes),
		received_into=into,
	)
	rows.sort(key=lambda r: (scan_rules.natural_key(r["item_name"]), r["item_code"]))
	return rows


def item_columns():
	return [
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 160},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 240},
		{
			"label": _("Item Group"),
			"fieldname": "item_group",
			"fieldtype": "Link",
			"options": "Item Group",
			"width": 130,
		},
		{"label": _("Stock Item"), "fieldname": "is_stock_item", "fieldtype": "Check", "width": 80},
		{"label": _("UOM"), "fieldname": "stock_uom", "fieldtype": "Data", "width": 70},
		{"label": _("Ordered"), "fieldname": "ordered", "fieldtype": "Float", "width": 90},
		{"label": _("Received"), "fieldname": "received", "fieldtype": "Float", "width": 90},
		{"label": _("On Project Shelf"), "fieldname": "on_shelf", "fieldtype": "Float", "width": 120},
		{"label": _("At Job Site"), "fieldname": "at_site", "fieldtype": "Float", "width": 100},
		{"label": _("Used on Job"), "fieldname": "used", "fieldtype": "Float", "width": 100},
		{"label": _("Returned to Supplier"), "fieldname": "returned", "fieldtype": "Float", "width": 140},
		{"label": _("Still to Come"), "fieldname": "still_to_come", "fieldtype": "Float", "width": 110},
		{
			"label": _("Order Closed, Not Received"),
			"fieldname": "closed_short",
			"fieldtype": "Float",
			"width": 170,
		},
		{"label": _("Not Located"), "fieldname": "not_located", "fieldtype": "Float", "width": 100},
		{"label": _("Standing"), "fieldname": "standing", "fieldtype": "Data", "width": 170},
		{"label": _("Received Into"), "fieldname": "received_into", "fieldtype": "Data", "width": 160},
		{"label": _("Note"), "fieldname": "where_note", "fieldtype": "Data", "width": 300},
		{"label": _("Ordered Amount"), "fieldname": "ordered_amount", "fieldtype": "Currency", "width": 120},
		{"label": _("First Ordered"), "fieldname": "first_ordered", "fieldtype": "Date", "width": 105},
		{"label": _("Last Received"), "fieldname": "last_received", "fieldtype": "Date", "width": 105},
		{"label": _("Purchase Orders"), "fieldname": "purchase_orders", "fieldtype": "Data", "width": 220},
		{"label": _("Receipts"), "fieldname": "receipts", "fieldtype": "Data", "width": 220},
	]


def project_message(project, info, where):
	"""The line over the table: whose job it is, and where its stock would be."""
	parts = [project]
	for key in ("project_name", "status", "customer"):
		if info.get(key):
			parts.append(str(info.get(key)))
	lines = [frappe.utils.escape_html(" | ".join(parts))]
	sites = sorted(name for name, job in where.site_projects.items() if job == project)
	if sites:
		lines.append(
			_("Job site: {0}").format(
				frappe.utils.escape_html(", ".join(where.names.get(s) or s for s in sites))
			)
		)
	else:
		lines.append(_("This job has no job site warehouse, so nothing is shown as at the job site."))
	if not where.shelf:
		lines.append(_(data.NO_SHELF_NOTE))
	return "<br>".join(lines)


def item_summary(rows):
	summary = rules.project_item_summary(rows)
	return [
		{"label": _("Items"), "value": summary["items"], "datatype": "Int", "indicator": "Blue"},
		{"label": _("Still to come"), "value": summary["waiting"], "datatype": "Int", "indicator": "Orange"},
		{
			"label": _("On the shelf or at the site"),
			"value": summary["held"],
			"datatype": "Int",
			"indicator": "Green",
		},
		{
			"label": _("Received, not located"),
			"value": summary["not_located"],
			"datatype": "Int",
			"indicator": "Red",
		},
		{
			"label": _("Not stock items"),
			"value": summary["not_stock"],
			"datatype": "Int",
			"indicator": "Grey",
		},
	]


# --- Every project -------------------------------------------------------------------------


def every_project(filters, where):
	shelf_rows = data.shelf_stock_by_project(where.shelf) if where.shelf else []
	site_rows = data.site_bins(where)
	projects = {row.get("project") for row in shelf_rows} | {row.get("project") for row in site_rows}
	info = data.project_info(projects)
	rows = rules.held_by_project(shelf_rows, site_rows, info)
	customer = (filters.get("customer") or "").strip()
	include_finished = rules._on(filters.get("include_finished"))
	shown = [
		row
		for row in rows
		if (not customer or row["customer"] == customer)
		and (include_finished or row["project_status"] not in scan_rules.CLOSED_JOB_STATUSES)
	]
	shown.sort(key=lambda r: (-r["total_value"], r["project"]))
	return project_columns(), shown, held_message(shelf_rows, where), None, held_summary(shown)


def project_columns():
	return [
		{
			"label": _("Project"),
			"fieldname": "project",
			"fieldtype": "Link",
			"options": "Project",
			"width": 120,
		},
		{"label": _("Project Name"), "fieldname": "project_name", "fieldtype": "Data", "width": 240},
		{
			"label": _("Customer"),
			"fieldname": "customer",
			"fieldtype": "Link",
			"options": "Customer",
			"width": 180,
		},
		{"label": _("Project Type"), "fieldname": "project_type", "fieldtype": "Data", "width": 110},
		{"label": _("Status"), "fieldname": "project_status", "fieldtype": "Data", "width": 100},
		{"label": _("Items Held"), "fieldname": "items", "fieldtype": "Int", "width": 95},
		{"label": _("On Project Shelf"), "fieldname": "shelf_qty", "fieldtype": "Float", "width": 125},
		{"label": _("Shelf Value"), "fieldname": "shelf_value", "fieldtype": "Currency", "width": 115},
		{"label": _("At Job Site"), "fieldname": "site_qty", "fieldtype": "Float", "width": 105},
		{"label": _("Site Value"), "fieldname": "site_value", "fieldtype": "Currency", "width": 115},
		{"label": _("Total Value"), "fieldname": "total_value", "fieldtype": "Currency", "width": 120},
	]


def held_message(shelf_rows, where):
	lines = [
		_("Choose a Project to see every item ordered for it. With none, this lists what each job holds now.")
	]
	if not where.shelf:
		lines.append(_(data.NO_SHELF_NOTE))
	else:
		unclaimed = [row for row in shelf_rows if not row.get("project")]
		if unclaimed:
			lines.append(
				_("{0} item(s) on the Project Shelf are under no job and are not in this list.").format(
					len({row.get("item_code") for row in unclaimed})
				)
			)
	if not where.sites_ready:
		lines.append(_("Job sites are not set up on this site, so nothing is shown as at a job site."))
	return "<br>".join(lines)


def held_summary(rows):
	return [
		{"label": _("Jobs holding stock"), "value": len(rows), "datatype": "Int", "indicator": "Blue"},
		{
			"label": _("On the Project Shelf"),
			"value": sum(r["shelf_value"] for r in rows),
			"datatype": "Currency",
			"indicator": "Blue",
		},
		{
			"label": _("At job sites"),
			"value": sum(r["site_value"] for r in rows),
			"datatype": "Currency",
			"indicator": "Orange",
		},
		{
			"label": _("Held in all"),
			"value": sum(r["total_value"] for r in rows),
			"datatype": "Currency",
			"indicator": "Green",
		},
	]
