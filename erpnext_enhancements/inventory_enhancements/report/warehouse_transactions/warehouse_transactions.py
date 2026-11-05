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
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, nowdate

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
		{"label": _("Qty Change"), "fieldname": "qty_change", "fieldtype": "Float", "width": 100},
		{"label": _("Balance After"), "fieldname": "balance_after", "fieldtype": "Float", "width": 110},
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
	counted = count_changes(entries)
	item_names = _lookup("Item", "item_name", {e.get("item_code") for e in entries})
	full_names = _lookup("User", "full_name", {e.get("owner") for e in entries})
	return [_shape(e, counted, item_names, full_names) for e in entries]


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


def _shape(entry, counted, item_names, full_names):
	item_code = entry.get("item_code")
	user = entry.get("owner")
	change = counted.get(entry.get("name"), entry.get("actual_qty"))
	return {
		"posting_date": entry.get("posting_date"),
		"posting_time": entry.get("posting_time"),
		"item_code": item_code,
		"item_name": item_names.get(item_code) or "",
		"qty_change": change,
		"balance_after": entry.get("qty_after_transaction"),
		"warehouse": entry.get("warehouse"),
		"voucher_type": entry.get("voucher_type"),
		"voucher_no": entry.get("voucher_no"),
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
