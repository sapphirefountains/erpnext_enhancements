# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Warehouse Transactions — every stock movement, and who made it (v1.797.0, TASK-2026-03136).

One row per Stock Ledger Entry: the item, how much the quantity changed (positive added, negative
taken out), the balance after it, the warehouse, when, the voucher that moved it, and the person.
Pick a person in the User filter and the report is their history of additions and removals; pick a
bin and it is that bin's.

**A Script Report, although the task asked for a Query Report.** It shows up and opens the same
way, at ``/desk/query-report/Warehouse Transactions``. The difference is optional filters. Frappe
16.53's report view sends only the filters that hold a value (``query_report.js``
``get_filter_values`` drops the empty ones), and a Query Report hands that dict straight to
``frappe.db.sql(self.query, filters)``. So a ``%(item_code)s`` placeholder with Item left blank is
a missing key and the run fails. Item, Warehouse and User are optional here, so the conditions are
built in Python.

**A count's change is worked out, because the ledger stores it as 0.** ERPNext 16.52.1 writes a
Stock Reconciliation's entries with ``actual_qty = 0`` and the counted balance in
``qty_after_transaction`` (``stock_reconciliation.py`` ``get_sle_for_items``). Its own Stock Ledger
report derives the change from the balance before (``set_reconciliation_in_out_qty`` /
``get_qty_before_entry``), and so does this one: the count's balance after, minus the
``qty_after_transaction`` of the previous live entry for the same item and warehouse, ordered by
``posting_datetime`` then ``creation``, and 0 when there is none (a first count). That previous
entry can sit before From Date and can belong to someone the User filter hides, so it is read
from the whole ledger of those items and warehouses, not from the report's own rows. On production
on 2026-11-05, 667 of the 819 live entries were reconciliations, every one at ``actual_qty = 0``;
in the default 30 days that was 200 of 335 rows, 57 of them additions and 143 removals.

**The person is the entry's ``owner``.** ERPNext writes the Stock Ledger Entry when the voucher is
submitted, as the user who submits it. On production on 2026-11-05 every live entry's owner was
also the owner (creator) of its voucher, so far the person who made each voucher submitted it
too. The Stock Scan and the Count Desk submit as the signed-in user, never as Administrator.

**Canceled entries are left out.** Cancelling a voucher marks its entries ``is_cancelled`` and
writes reversing entries that are marked the same way. Skipping both, as ERPNext's own Stock
Ledger report does, keeps a canceled move from showing as an addition and a removal that never
happened.

**A group warehouse covers the bins under it.** Each storage bin is a Warehouse of its own
(``Bin B2-3-4 - SF``), so choosing the store a bin sits in includes every bin below it,
``frappe.db.get_descendants`` on the warehouse tree.

**One row per ledger line, not per movement.** A transfer between bins, or a repack, is two
entries, one out of the source and one into the target, so it is two rows and counts once under
"lines adding stock" and once under "lines removing stock". The cards say lines for that reason.

**Permissions.** Every ledger read, the report's rows and the balances before a count, goes
through ``frappe.get_list``, so the reader's Stock Ledger Entry read permission and any User
Permissions apply. The report's roles are the ones ERPNext gives its own Stock Ledger report:
Stock User and Accounts Manager. Item names and people's full names are display lookups for rows
the reader may already see. The User column is plain text, not a link: User grants Desk User only
``select``, so a link would open onto a permission error for most readers.

**v1.831.0 (the 2026-11-19 operations review): a job, a room or rack, and totals.**

* **Project.** Stock moved for a job: every ledger row that carries the Project (a take with the job
  picked, a receipt against the job's order line, a move onto or off the Project Shelf for it, a move
  to its site), and every row at the job's own job-site warehouse (``Warehouse.custom_job_site_project``),
  which includes what was later moved back or counted there. ``frappe.get_list``'s ``or_filters``
  joins the two halves, so everything else still narrows both. A count corrects stock without a job, so
  a count on the Project Shelf is not a job's movement; a count at the job's site is.
* **A room or a rack.** Picking a group warehouse (``Atrium``, ``North Large Rack 1``) includes every
  shelf and bin under it, as v1.797.0 already did; the filter is now labeled for it and a **Room /
  Rack** column says where under the room each row was.
* **Kind.** Each row says what it was (Take, Receive, Add, Move, Count, Return to supplier, Delivered,
  Other; ``stock_movement_rules.movement_kind``) and the Kind filter keeps one. A Stock Entry's
  purpose is read through ``frappe.get_list("Stock Entry")``, so a Stock Entry the reader may not see
  reads as Other.
* **By person / By item** (the View filter) total the same rows (``stock_movement_rules.by_person`` and
  ``by_item``). By person also lists, with zeros, everyone who keeps stock and made none (enabled System
  Users holding Stock User, Stock Manager or Inventory Clerk, without Administrator, Guest and the
  service account): "who made none" is the question. With the User filter set it lists that person
  alone, zeros included. The people come from ``Has Role`` and ``User`` through ``get_all``: names
  only, the same people the User filter already offers.
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, nowdate

from erpnext_enhancements.inventory_enhancements import stock_movement_rules as rules
from erpnext_enhancements.inventory_enhancements.job_site_rules import SITE_FIELD
from erpnext_enhancements.inventory_enhancements.reorder_rules import EXCLUDED_RECIPIENTS

#: How far back the report looks when no From Date is given (the filter's own default is the same).
DEFAULT_DAYS = 30

#: The Stock Ledger Entry columns the report reads.
ENTRY_FIELDS = (
	"name",
	"posting_date",
	"posting_time",
	"item_code",
	"warehouse",
	"actual_qty",
	"qty_after_transaction",
	"voucher_type",
	"voucher_no",
	"project",
	"owner",
)

#: What the balance-before walk reads for each earlier entry.
LEDGER_FIELDS = ("name", "item_code", "warehouse", "qty_after_transaction")

#: Newest first. ``posting_datetime`` is v16's single sortable stamp; ``creation`` breaks ties
#: between the rows of one voucher.
ORDER_BY = "posting_datetime desc, creation desc"

#: The ledger's own order, which the balance-before walk follows (ERPNext's get_qty_before_entry).
LEDGER_ORDER = "posting_datetime asc, creation asc"

RECONCILIATION = "Stock Reconciliation"

#: The roles of the people the By person view lists even when they made no movement: the ones who
#: keep stock. System Manager alone is not one (an administrator need not touch stock).
STOCK_ROLES = ("Stock User", "Stock Manager", "Inventory Clerk")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	view = rules.clean_view(filters.get("view"))
	rows = get_data(filters)
	if view == rules.VIEW_PEOPLE:
		people = rules.by_person(rows, stock_people(filters))
		return person_columns(), people, None, None, person_summary(people)
	if view == rules.VIEW_ITEMS:
		items = rules.by_item(rows, _lookup("Item", "stock_uom", {r.get("item_code") for r in rows}))
		return item_columns(), items, None, None, item_summary(items)
	return get_columns(), rows, None, None, get_summary(rows)


def get_columns():
	return [
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 105},
		{"label": _("Posting Time"), "fieldname": "posting_time", "fieldtype": "Time", "width": 95},
		{
			"label": _("Item Code"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 180,
		},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 240},
		{"label": _("Kind"), "fieldname": "kind", "fieldtype": "Data", "width": 110},
		{"label": _("Qty Change"), "fieldname": "qty_change", "fieldtype": "Float", "width": 100},
		{"label": _("Balance After"), "fieldname": "balance_after", "fieldtype": "Float", "width": 110},
		{
			"label": _("Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 170,
		},
		# The groups above the warehouse, the company's root left out: "Atrium › North Large Rack 1".
		{"label": _("Room / Rack"), "fieldname": "where", "fieldtype": "Data", "width": 200},
		{
			"label": _("Voucher Type"),
			"fieldname": "voucher_type",
			"fieldtype": "Link",
			"options": "DocType",
			"width": 140,
		},
		{
			"label": _("Voucher No"),
			"fieldname": "voucher_no",
			"fieldtype": "Dynamic Link",
			"options": "voucher_type",
			"width": 170,
		},
		{
			"label": _("Project"),
			"fieldname": "project",
			"fieldtype": "Link",
			"options": "Project",
			"width": 120,
		},
		# Data, not a Link to User: Desk User holds only `select` on User, so a link opens onto a
		# permission error for most readers.
		{"label": _("User"), "fieldname": "user", "fieldtype": "Data", "width": 220},
		{"label": _("Full Name"), "fieldname": "full_name", "fieldtype": "Data", "width": 160},
	]


def date_range(filters):
	"""``(from_date, to_date)`` as dates. A blank To Date is today; a blank From Date is
	``DEFAULT_DAYS`` before the To Date. A From Date after the To Date is refused."""
	to_date = getdate(filters.get("to_date") or nowdate())
	from_date = (
		getdate(filters.get("from_date")) if filters.get("from_date") else add_days(to_date, -DEFAULT_DAYS)
	)
	if from_date > to_date:
		frappe.throw(_("From Date must be on or before To Date."))
	return from_date, to_date


def warehouse_and_descendants(warehouse):
	"""The warehouse itself and every warehouse below it in the tree."""
	return [warehouse, *(frappe.db.get_descendants("Warehouse", warehouse) or [])]


def build_conditions(filters, sites=None):
	"""The ``frappe.get_list`` filters for these report filters. ``sites``: the job-site warehouses
	of the Project filter's job (:func:`project_sites`); with none, the Project filter is a plain
	condition on the ledger row's project, and with some it is :func:`project_or_filters`."""
	from_date, to_date = date_range(filters)
	conditions = {
		"is_cancelled": 0,
		"posting_date": ["between", [from_date, to_date]],
	}
	if filters.get("item_code"):
		conditions["item_code"] = filters.get("item_code")
	if filters.get("warehouse"):
		conditions["warehouse"] = ["in", warehouse_and_descendants(filters.get("warehouse"))]
	if filters.get("user"):
		conditions["owner"] = filters.get("user")
	if filters.get("project") and not sites:
		conditions["project"] = filters.get("project")
	return conditions


def project_or_filters(filters, sites):
	"""``(project is the job) OR (warehouse is one of its sites)``, or ``None``. ``get_list`` ANDs
	this group with the plain filters, so a date, item, place or person still narrows both halves."""
	if not filters.get("project") or not sites:
		return None
	return [["project", "=", filters.get("project")], ["warehouse", "in", list(sites)]]


def project_sites(project):
	"""The job's job-site warehouses and anything under them, sorted, or ``[]``.

	A site is a Warehouse whose ``custom_job_site_project`` names the job (``job_sites``). Before that
	field exists (a fresh bench, a deploy between code and patch) there are no sites, and
	``frappe.db.has_column`` raises on a missing table rather than answering False, so both read as none.
	"""
	if not project:
		return []
	try:
		if not frappe.db.has_column("Warehouse", SITE_FIELD):
			return []
	except Exception:
		return []
	names = set()
	for site in frappe.get_all("Warehouse", filters={SITE_FIELD: project}, pluck="name") or []:
		names.update(warehouse_and_descendants(site))
	return sorted(names)


def get_data(filters):
	sites = project_sites(filters.get("project"))
	kwargs = {}
	or_filters = project_or_filters(filters, sites)
	if or_filters:
		kwargs["or_filters"] = or_filters
	entries = frappe.get_list(
		"Stock Ledger Entry",
		filters=build_conditions(filters, sites),
		fields=list(ENTRY_FIELDS),
		order_by=ORDER_BY,
		**kwargs,
	)
	counted = count_changes(entries)
	purposes = stock_entry_purposes(entries)
	item_names = _lookup("Item", "item_name", {e.get("item_code") for e in entries})
	full_names = _lookup("User", "full_name", {e.get("owner") for e in entries})
	trails = warehouse_trails(entries)
	rows = [_shape(e, counted, item_names, full_names, purposes, trails) for e in entries]
	kind = rules.clean_kind(filters.get("kind"))
	if kind:
		rows = [r for r in rows if r.get("kind") == kind]
	return rows


def stock_entry_purposes(entries):
	"""``{Stock Entry name: purpose}`` for the Stock Entries among ``entries``, in one permission-checked
	read: a Stock Entry the reader may not see is missing, and its rows read as Other."""
	names = sorted({e.get("voucher_no") for e in entries if e.get("voucher_type") == "Stock Entry"} - {None})
	if not names:
		return {}
	rows = frappe.get_list("Stock Entry", filters={"name": ["in", names]}, fields=["name", "purpose"])
	return {r.get("name"): r.get("purpose") for r in rows}


def warehouse_trails(entries):
	"""``{warehouse: "Atrium › North Large Rack 1"}`` for the warehouses among ``entries``: the
	groups above each one, outermost first, the root (the warehouse with no parent) left out. One
	read of the warehouse tree's names and parents, a few hundred rows."""
	wanted = {e.get("warehouse") for e in entries} - {None, ""}
	if not wanted:
		return {}
	tree = frappe.get_all("Warehouse", fields=["name", "warehouse_name", "parent_warehouse"])
	parents = {r.get("name"): r.get("parent_warehouse") for r in tree}
	labels = {r.get("name"): r.get("warehouse_name") or r.get("name") for r in tree}
	return {name: rules.group_trail(name, parents, labels) for name in wanted}


def stock_people(filters):
	"""``{user: full_name}`` of the people the By person view lists even with no movement.

	With the User filter set, that person alone, so "did they make any?" has an answer whichever way it
	goes. Otherwise everyone who keeps stock (:data:`STOCK_ROLES`, enabled System Users, without
	Frappe's own accounts and the service account: ``reorder_rules.EXCLUDED_RECIPIENTS``).
	"""
	user = filters.get("user")
	if user:
		return {user: _lookup("User", "full_name", {user}).get(user) or user}
	holders = frappe.get_all(
		"Has Role", filters={"parenttype": "User", "role": ["in", list(STOCK_ROLES)]}, pluck="parent"
	)
	holders = sorted(set(holders or []) - {None, ""})
	if not holders:
		return {}
	users = frappe.get_all(
		"User",
		filters={"name": ["in", holders]},
		fields=["name", "full_name", "enabled", "user_type"],
	)
	return rules.stock_people(users, holders, EXCLUDED_RECIPIENTS)


def is_count_row(entry):
	"""A Stock Reconciliation entry that stores no change: ERPNext's own test in the Stock Ledger
	report (``voucher_type == "Stock Reconciliation" and not actual_qty``)."""
	return entry.get("voucher_type") == RECONCILIATION and not flt(entry.get("actual_qty"))


def count_changes(entries):
	"""``{entry name: change}`` for each count row among ``entries``.

	The change is the count's ``qty_after_transaction`` minus that of the previous live entry for
	the same item and warehouse (0 when there is none). One ``get_list`` reads the ledger of the
	counted items in the counted warehouses up to the last count's date, in ledger order, and the
	walk keeps each pair's running balance. Reading from the start of the ledger rather than
	from From Date is what finds a balance set before the window; at this site's volume (819 live
	entries on 2026-11-05) that is a few hundred rows. A count missing from the walk keeps its
	stored 0.
	"""
	targets = [e for e in entries if is_count_row(e)]
	if not targets:
		return {}
	ledger = frappe.get_list(
		"Stock Ledger Entry",
		filters={
			"is_cancelled": 0,
			"item_code": ["in", sorted({e.get("item_code") for e in targets})],
			"warehouse": ["in", sorted({e.get("warehouse") for e in targets})],
			"posting_date": ["<=", max(getdate(e.get("posting_date")) for e in targets)],
		},
		fields=list(LEDGER_FIELDS),
		order_by=LEDGER_ORDER,
	)
	wanted = {e.get("name") for e in targets}
	balance = {}
	changes = {}
	for row in ledger:
		key = (row.get("item_code"), row.get("warehouse"))
		after = flt(row.get("qty_after_transaction"))
		if row.get("name") in wanted:
			changes[row.get("name")] = after - balance.get(key, 0.0)
		balance[key] = after
	return changes


def _lookup(doctype, field, names):
	"""``{name: field}`` for the given record names, in one query."""
	names = sorted(n for n in names if n)
	if not names:
		return {}
	rows = frappe.get_all(doctype, filters={"name": ["in", names]}, fields=["name", field])
	return {r.get("name"): r.get(field) for r in rows}


def _shape(entry, counted, item_names, full_names, purposes=None, trails=None):
	item_code = entry.get("item_code")
	user = entry.get("owner")
	warehouse = entry.get("warehouse")
	change = counted.get(entry.get("name"), entry.get("actual_qty"))
	return {
		"posting_date": entry.get("posting_date"),
		"posting_time": entry.get("posting_time"),
		"item_code": item_code,
		"item_name": item_names.get(item_code) or "",
		"kind": rules.movement_kind(
			entry.get("voucher_type"), (purposes or {}).get(entry.get("voucher_no")), change
		),
		"qty_change": change,
		"balance_after": entry.get("qty_after_transaction"),
		"warehouse": warehouse,
		"where": (trails or {}).get(warehouse) or "",
		"voucher_type": entry.get("voucher_type"),
		"voucher_no": entry.get("voucher_no"),
		"project": entry.get("project") or "",
		"user": user,
		"full_name": full_names.get(user) or "",
	}


def get_summary(rows):
	"""The cards over the table. They count ledger lines: a transfer between bins is one line out
	and one line in, so it counts under both."""
	adding = sum(1 for r in rows if flt(r.get("qty_change")) > 0)
	removing = sum(1 for r in rows if flt(r.get("qty_change")) < 0)
	people = len({r.get("user") for r in rows if r.get("user")})
	return [
		{"label": _("Ledger lines"), "value": len(rows), "datatype": "Int", "indicator": "Blue"},
		{"label": _("Lines adding stock"), "value": adding, "datatype": "Int", "indicator": "Green"},
		{"label": _("Lines removing stock"), "value": removing, "datatype": "Int", "indicator": "Red"},
		{"label": _("People"), "value": people, "datatype": "Int", "indicator": "Grey"},
	]


def person_columns():
	"""The By person view. The take, receipt, move and count columns count one per voucher and item,
	so a transfer is one move however many of its two ledger lines the filters show."""
	return [
		{"label": _("Person"), "fieldname": "full_name", "fieldtype": "Data", "width": 170},
		# Data, not a Link to User: see get_columns.
		{"label": _("User"), "fieldname": "user", "fieldtype": "Data", "width": 220},
		{"label": _("Saves"), "fieldname": "saves", "fieldtype": "Int", "width": 80},
		{"label": _("Ledger Lines"), "fieldname": "lines", "fieldtype": "Int", "width": 100},
		{"label": _("Takes"), "fieldname": "takes", "fieldtype": "Int", "width": 80},
		{"label": _("Receipts"), "fieldname": "receipts", "fieldtype": "Int", "width": 90},
		{"label": _("Moves"), "fieldname": "moves", "fieldtype": "Int", "width": 80},
		{"label": _("Counts"), "fieldname": "counts", "fieldtype": "Int", "width": 80},
		{"label": _("Other"), "fieldname": "others", "fieldtype": "Int", "width": 80},
		{"label": _("Items"), "fieldname": "items", "fieldtype": "Int", "width": 80},
		{"label": _("Days Active"), "fieldname": "days_active", "fieldtype": "Int", "width": 100},
		{"label": _("Last Movement"), "fieldname": "last_movement", "fieldtype": "Date", "width": 115},
	]


def person_summary(people):
	active = sum(1 for p in people if p.get("lines"))
	return [
		{"label": _("People with movements"), "value": active, "datatype": "Int", "indicator": "Green"},
		{
			"label": _("People with none"),
			"value": len(people) - active,
			"datatype": "Int",
			"indicator": "Orange",
		},
		{
			"label": _("Saves"),
			"value": sum(p.get("saves") or 0 for p in people),
			"datatype": "Int",
			"indicator": "Blue",
		},
		{
			"label": _("Ledger lines"),
			"value": sum(p.get("lines") or 0 for p in people),
			"datatype": "Int",
			"indicator": "Grey",
		},
	]


def item_columns():
	"""The By item view, every quantity in the item's stock unit. Used is what was taken for a job or
	delivered; a count correction is never use (``stock_movement_rules``)."""
	return [
		{
			"label": _("Item Code"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 180,
		},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 240},
		{"label": _("Unit"), "fieldname": "stock_uom", "fieldtype": "Data", "width": 70},
		{"label": _("Used"), "fieldname": "used", "fieldtype": "Float", "width": 90},
		{"label": _("Uses"), "fieldname": "uses", "fieldtype": "Int", "width": 70},
		{"label": _("Received"), "fieldname": "received", "fieldtype": "Float", "width": 90},
		{"label": _("Moved"), "fieldname": "moved", "fieldtype": "Float", "width": 90},
		{"label": _("Count Corrections"), "fieldname": "counted", "fieldtype": "Float", "width": 130},
		{"label": _("Returned"), "fieldname": "returned", "fieldtype": "Float", "width": 90},
		{"label": _("Net Change"), "fieldname": "net_change", "fieldtype": "Float", "width": 100},
		{"label": _("People"), "fieldname": "people", "fieldtype": "Int", "width": 80},
		{"label": _("Ledger Lines"), "fieldname": "lines", "fieldtype": "Int", "width": 100},
		{"label": _("Last Movement"), "fieldname": "last_movement", "fieldtype": "Date", "width": 115},
	]


def item_summary(items):
	return [
		{"label": _("Items moved"), "value": len(items), "datatype": "Int", "indicator": "Blue"},
		{
			"label": _("Items used"),
			"value": sum(1 for i in items if (i.get("uses") or 0) > 0),
			"datatype": "Int",
			"indicator": "Green",
		},
		{
			"label": _("Ledger lines"),
			"value": sum(i.get("lines") or 0 for i in items),
			"datatype": "Int",
			"indicator": "Grey",
		},
	]
