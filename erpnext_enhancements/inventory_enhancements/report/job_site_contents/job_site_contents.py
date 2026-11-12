# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Job Site Contents: what is at each job site, by job (v1.805.0).

Nik, 2026-11-12: "I want to be able to view what's at the Job Site, and have it be useful so it
filters by project as well." One row per item at a job's site warehouse: how many, what they are
worth, when they arrived and how long they have been there. Filters: Project, Customer, Item and
Item Group, every one optional. With none, it is everything out on every job.

**What a job site is** is ``inventory_enhancements/job_sites.py``'s answer, not this report's: a
warehouse whose ``custom_job_site_project`` names a Project, and everything under the Job Sites
group. A place under that group with no job of its own (production's hand-made ``Job Site - SF``)
lists with a blank Project, and drops out as soon as a Project or Customer is chosen.

**A finished job's site still lists.** Stock left at a job that is Completed is the stock most
worth finding, so nothing here reads the job's status except to show it.

**On Order** is what open orders will deliver to the site (``tabBin.ordered_qty``). A row with
none on hand and some on order lists, as it does on the site's own Stock Scan screen.

**Arrived and Days There** come from the item's stock ledger at that site
(``inventory_report_rules.stay``): the day the balance last rose from nothing, so parts that were
all used and then sent again are not counted from the first delivery. *Last Arrival* is the
newest row that raised the balance. The ledger is read only for the sites that hold something,
in ledger order (``posting_datetime``, then ``creation``), canceled entries left out.

**Value** is ERPNext's own stock value for the item at the site. A transfer to a site moves no
value in the books (a site shares the shop's inventory account), but each warehouse keeps its own
valuation, so this is what the stock at the site cost.

**Non-stock items are not here.** An item with Maintain Stock off never enters stock, so it never
reaches a site in the ledger. Project Materials lists those as ordered and received.

Before job sites exist on a site (``job_sites.ready()`` false) the report is empty and says so.

**A Script Report** because every filter is optional. Opens at
``/desk/query-report/Job Site Contents``.
"""

import frappe
from frappe import _
from frappe.utils import nowdate

from erpnext_enhancements.inventory_enhancements import inventory_report_data as data
from erpnext_enhancements.inventory_enhancements import inventory_report_rules as rules
from erpnext_enhancements.inventory_enhancements import stock_scan_rules as scan_rules


def execute(filters=None):
	filters = frappe._dict(filters or {})
	where = data.places()
	if not where.sites_ready:
		return get_columns(), [], _("Job sites are not set up on this site yet."), None, get_summary([])
	rows = get_rows(filters, where)
	message = None if where.sites else _("There are no job site warehouses yet.")
	return get_columns(), rows, message, None, get_summary(rows)


def get_columns():
	return [
		{
			"label": _("Project"),
			"fieldname": "project",
			"fieldtype": "Link",
			"options": "Project",
			"width": 110,
		},
		{"label": _("Project Name"), "fieldname": "project_name", "fieldtype": "Data", "width": 200},
		{
			"label": _("Customer"),
			"fieldname": "customer",
			"fieldtype": "Link",
			"options": "Customer",
			"width": 160,
		},
		{"label": _("Project Status"), "fieldname": "project_status", "fieldtype": "Data", "width": 110},
		{
			"label": _("Job Site"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 190,
		},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 160},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 230},
		{
			"label": _("Item Group"),
			"fieldname": "item_group",
			"fieldtype": "Link",
			"options": "Item Group",
			"width": 130,
		},
		{"label": _("On Hand"), "fieldname": "on_hand", "fieldtype": "Float", "width": 90},
		{"label": _("On Order"), "fieldname": "on_order", "fieldtype": "Float", "width": 90},
		{"label": _("UOM"), "fieldname": "stock_uom", "fieldtype": "Data", "width": 70},
		{"label": _("Value"), "fieldname": "value", "fieldtype": "Currency", "width": 105},
		{"label": _("Arrived"), "fieldname": "arrived", "fieldtype": "Date", "width": 105},
		{"label": _("Last Arrival"), "fieldname": "last_arrival", "fieldtype": "Date", "width": 105},
		{"label": _("Days There"), "fieldname": "days_there", "fieldtype": "Int", "width": 95},
	]


def site_ledger(warehouses):
	"""The live stock ledger rows at the given sites, oldest first, as
	``{(item_code, warehouse): [row, ...]}``."""
	out = {}
	for part in data.chunks(warehouses):
		for row in frappe.db.sql(
			"""
			select item_code, warehouse, posting_date, qty_after_transaction
			from `tabStock Ledger Entry`
			where is_cancelled = 0 and warehouse in %(sites)s
			order by posting_datetime asc, creation asc
			""",
			{"sites": tuple(part)},
			as_dict=True,
		):
			out.setdefault((row.get("item_code"), row.get("warehouse")), []).append(row)
	return out


def get_rows(filters, where):
	project = (filters.get("project") or "").strip()
	customer = (filters.get("customer") or "").strip()
	item_code = (filters.get("item_code") or "").strip()

	projects = data.project_info(set(where.site_projects.values()))
	wanted = None
	if project or customer:
		wanted = {
			name
			for name, info in projects.items()
			if (not project or name == project) and (not customer or info.get("customer") == customer)
		}
		if not wanted:
			return []
	bins = data.site_bins(where, projects=wanted, item_codes=[item_code] if item_code else None)
	if not bins:
		return []

	items = data.item_info({row["item_code"] for row in bins})
	groups = set(data.item_groups_under(filters.get("item_group")))
	ledger = site_ledger({row["warehouse"] for row in bins if row["qty"] > 0})
	today = nowdate()

	rows = []
	for row in bins:
		info = items.get(row["item_code"]) or {}
		if groups and info.get("item_group") not in groups:
			continue
		job = projects.get(row["project"]) or {}
		arrived, last_in = (None, None)
		if row["qty"] > 0:
			arrived, last_in = rules.stay(ledger.get((row["item_code"], row["warehouse"])))
		rows.append(
			{
				"project": row["project"],
				"project_name": job.get("project_name") or "",
				"customer": job.get("customer") or "",
				"project_status": job.get("status") or "",
				"warehouse": row["warehouse"],
				"item_code": row["item_code"],
				"item_name": info.get("item_name") or row["item_code"],
				"item_group": info.get("item_group") or "",
				"on_hand": row["qty"],
				"on_order": row["on_order"],
				"stock_uom": info.get("stock_uom") or "",
				"value": row["value"],
				"arrived": arrived,
				"last_arrival": last_in,
				"days_there": rules.days_since(arrived, today),
			}
		)
	rows.sort(
		key=lambda r: (
			not r["project"],
			r["project"],
			scan_rules.natural_key(where.names.get(r["warehouse"]) or r["warehouse"]),
			scan_rules.natural_key(r["item_name"]),
		)
	)
	return rows


def get_summary(rows):
	summary = rules.job_site_summary(rows)
	return [
		{
			"label": _("Job sites with stock"),
			"value": summary["sites"],
			"datatype": "Int",
			"indicator": "Blue",
		},
		{"label": _("Item lines"), "value": summary["lines"], "datatype": "Int", "indicator": "Blue"},
		{
			"label": _("Value on job sites"),
			"value": summary["value"],
			"datatype": "Currency",
			"indicator": "Green",
		},
		{
			"label": _("Longest there (days)"),
			"value": summary["oldest_days"] or 0,
			"datatype": "Int",
			"indicator": "Orange",
		},
	]
