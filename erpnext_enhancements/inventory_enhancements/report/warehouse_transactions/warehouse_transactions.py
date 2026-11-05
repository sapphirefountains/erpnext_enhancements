# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Warehouse Transactions — every stock movement, and who made it (v1.797.0, TASK-2026-03136).

One row per Stock Ledger Entry: the item, how much moved (positive added, negative taken out),
the warehouse, when, the voucher that moved it, and the person. Pick a person in the User filter
and the report is their history of additions and removals; pick a bin and it is that bin's.

**A Script Report, although the task asked for a Query Report.** It shows up and opens the same
way, at ``/desk/query-report/Warehouse Transactions``. The difference is optional filters. Frappe
16.53's report view sends only the filters that hold a value (``query_report.js``
``get_filter_values`` drops the empty ones), and a Query Report hands that dict straight to
``frappe.db.sql(self.query, filters)``. So a ``%(item_code)s`` placeholder with Item left blank is
a missing key and the run fails. Item, Warehouse and User are optional here, so the conditions are
built in Python.

**The person is the entry's ``owner``.** ERPNext writes the Stock Ledger Entry when the voucher is
submitted, so ``owner`` is whoever submitted it, which is the person who made the stock move. On
production on 2026-11-05 every one of the 819 entries had the same owner as its voucher (Stock
Entry, Stock Reconciliation and Purchase Receipt alike). The Stock Scan and the Count Desk submit
as the signed-in user, never as Administrator, so the name is the person on the floor.

**Canceled entries are left out.** Cancelling a voucher marks its entries ``is_cancelled`` and
writes reversing entries that are marked the same way. Skipping both, as ERPNext's own Stock
Ledger report does, keeps a canceled move from showing as an addition and a removal that never
happened.

**A group warehouse covers the bins under it.** Each storage bin is a Warehouse of its own
(``Bin B2-3-4 - SF``), so choosing the store a bin sits in includes every bin below it,
``frappe.db.get_descendants`` on the warehouse tree.

**Permissions.** The rows come through ``frappe.get_list``, so the reader's Stock Ledger Entry read
permission and any User Permissions apply. The report's roles are the ones ERPNext gives its own
Stock Ledger report: Stock User and Accounts Manager. Item names and people's full names are
display lookups for rows the reader may already see.
"""

import frappe
from frappe import _
from frappe.utils import add_days, getdate, nowdate

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
	"voucher_type",
	"voucher_no",
	"owner",
)

#: Newest first. ``posting_datetime`` is v16's single sortable stamp; ``creation`` breaks ties
#: between the rows of one voucher.
ORDER_BY = "posting_datetime desc, creation desc"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	rows = get_data(filters)
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
		{"label": _("Actual Qty"), "fieldname": "actual_qty", "fieldtype": "Float", "width": 100},
		{
			"label": _("Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 170,
		},
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
		{"label": _("User"), "fieldname": "user", "fieldtype": "Link", "options": "User", "width": 220},
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


def build_conditions(filters):
	"""The ``frappe.get_list`` filters for these report filters."""
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
	return conditions


def get_data(filters):
	entries = frappe.get_list(
		"Stock Ledger Entry",
		filters=build_conditions(filters),
		fields=list(ENTRY_FIELDS),
		order_by=ORDER_BY,
	)
	item_names = _lookup("Item", "item_name", {e.get("item_code") for e in entries})
	full_names = _lookup("User", "full_name", {e.get("owner") for e in entries})
	return [_shape(e, item_names, full_names) for e in entries]


def _lookup(doctype, field, names):
	"""``{name: field}`` for the given record names, in one query."""
	names = sorted(n for n in names if n)
	if not names:
		return {}
	rows = frappe.get_all(doctype, filters={"name": ["in", names]}, fields=["name", field])
	return {r.get("name"): r.get(field) for r in rows}


def _shape(entry, item_names, full_names):
	item_code = entry.get("item_code")
	user = entry.get("owner")
	return {
		"posting_date": entry.get("posting_date"),
		"posting_time": entry.get("posting_time"),
		"item_code": item_code,
		"item_name": item_names.get(item_code) or "",
		"actual_qty": entry.get("actual_qty"),
		"warehouse": entry.get("warehouse"),
		"voucher_type": entry.get("voucher_type"),
		"voucher_no": entry.get("voucher_no"),
		"user": user,
		"full_name": full_names.get(user) or "",
	}


def get_summary(rows):
	"""The cards over the table: movements, how many added and removed, and how many people."""
	added = sum(1 for r in rows if (r.get("actual_qty") or 0) > 0)
	removed = sum(1 for r in rows if (r.get("actual_qty") or 0) < 0)
	people = len({r.get("user") for r in rows if r.get("user")})
	return [
		{"label": _("Movements"), "value": len(rows), "datatype": "Int", "indicator": "Blue"},
		{"label": _("Additions"), "value": added, "datatype": "Int", "indicator": "Green"},
		{"label": _("Removals"), "value": removed, "datatype": "Int", "indicator": "Red"},
		{"label": _("People"), "value": people, "datatype": "Int", "indicator": "Grey"},
	]
