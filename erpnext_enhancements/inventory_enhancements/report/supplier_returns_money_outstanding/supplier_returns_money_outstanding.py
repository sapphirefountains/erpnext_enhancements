# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Supplier Returns: Money Outstanding (v1.807.0).

The question it answers: **which returns have gone back and not been paid for, by whom, for how
long and for how much?** One row per Supplier Return, by supplier and then oldest first: when it
went back, days waiting, what we expect, what came back, the difference, where the received
figure comes from, and where the bill stands.

Three views (``supplier_return_rules.in_view``):

* **Waiting on money** (the default): sent back, a refund or credit still owed.
* **Came back short or over**: a refund or credit was recorded and it is not the expected amount,
  or the return was closed without credit.
* **All returns**: everything but Canceled, for a reconciliation.

**"Where the amount comes from" is never softened.** A figure a person typed reads "Typed by hand.
It is not backed by an ERPNext payment."; a figure from a Debit Note or a Payment Entry names the
kind of document. While bills are entered outside ERPNext, every received figure is typed, and
this column is how a reader knows that.

**"Bill" is for the person who enters the supplier's bill.** For a return that has gone back and
has no Debit Note: "Not billed in ERPNext. The bill should come in lower" (check the bill against
this row before entering it), or "Billed: <invoice>. Needs a Debit Note".

**A Script Report**, because Supplier and the dates are optional: frappe 16's report view sends
only the filters that hold a value, and a Query Report's ``%(supplier)s`` with Supplier blank is a
missing key (the reason ``warehouse_transactions`` is one too).

**Permissions.** The rows come from ``frappe.get_list``, so the reader's Supplier Return read
permission and any User Permissions apply. That a bill exists is said to every reader; its number
is shown only to one who may read Purchase Invoices. No aggregate is written as a string field (frappe 16 refuses that in ``get_list``).
"""

import frappe
from frappe import _
from frappe.utils import nowdate

from erpnext_enhancements.inventory_enhancements import supplier_return_rules as rules

DOCTYPE = "Supplier Return"

FIELDS = [
	"name",
	"supplier",
	"supplier_name",
	"status",
	"requested_resolution",
	"sent_back_on",
	"status_changed_on",
	"expected_amount",
	"amount_received",
	"money_source",
	"debit_note",
	"supplier_return_number",
	"purchase_receipt",
	"purchase_order",
]

#: More than any real site holds; a guard against an unbounded read, not a page size.
ROW_LIMIT = 2000


def execute(filters=None):
	filters = frappe._dict(filters or {})
	view = filters.get("view") if filters.get("view") in rules.REPORT_VIEWS else rules.VIEW_WAITING
	rows = _rows(filters, view)
	return _columns(), rows, None, None, _summary(rows)


def _conditions(filters, view):
	conditions = {"status": ("!=", rules.CANCELED)}
	if view == rules.VIEW_WAITING:
		conditions["waiting_on_money"] = 1
	if filters.get("supplier"):
		conditions["supplier"] = filters.supplier
	if filters.get("from_date") and filters.get("to_date"):
		conditions["posting_date"] = ("between", [filters.from_date, filters.to_date])
	elif filters.get("from_date"):
		conditions["posting_date"] = (">=", filters.from_date)
	elif filters.get("to_date"):
		conditions["posting_date"] = ("<=", filters.to_date)
	return conditions


def _bills(receipts):
	"""``{receipt: [bill, ...]}`` for submitted bills (not debit notes) that name the receipt."""
	receipts = sorted({receipt for receipt in receipts if receipt})
	if not receipts:
		return {}
	rows = frappe.db.sql(
		"""
		select distinct c.purchase_receipt, p.name
		from `tabPurchase Invoice Item` c
		join `tabPurchase Invoice` p on p.name = c.parent
		where p.docstatus = 1 and p.is_return = 0 and c.purchase_receipt in %(receipts)s
		order by p.posting_date asc, p.name asc
		""",
		{"receipts": tuple(receipts)},
	)
	out = {}
	for receipt, bill in rows:
		out.setdefault(receipt, []).append(bill)
	return out


def _rows(filters, view):
	docs = frappe.get_list(
		DOCTYPE,
		filters=_conditions(filters, view),
		fields=FIELDS,
		order_by="supplier_name asc, sent_back_on asc, creation asc",
		limit_page_length=ROW_LIMIT,
	)
	docs = [
		doc
		for doc in docs
		if rules.in_view(view, doc.status, doc.requested_resolution, doc.expected_amount, doc.amount_received)
	]
	bills = _bills(doc.purchase_receipt for doc in docs)
	today = nowdate()
	# The rows are the reader's own (get_list). A bill's number is another document's: it is named
	# only for a reader who may read Purchase Invoices, and otherwise only said to exist.
	may_read_bills = bool(frappe.has_permission("Purchase Invoice", "read"))
	return [
		rules.report_row(doc, bills.get(doc.purchase_receipt) or [], today, may_read_bills) for doc in docs
	]


def _summary(rows):
	summary = rules.report_summary(rows)
	return [
		{
			"label": _("Returns waiting on money"),
			"value": summary["waiting"],
			"datatype": "Int",
			"indicator": "orange" if summary["waiting"] else "green",
		},
		{"label": _("Expected back"), "value": summary["owed"], "datatype": "Currency", "indicator": "blue"},
		{
			"label": _("Longest wait (days)"),
			"value": summary["oldest_days"],
			"datatype": "Int",
			"indicator": "red" if summary["oldest_days"] > 30 else "blue",
		},
	]


def _columns():
	return [
		{
			"fieldname": "supplier_return",
			"label": _("Supplier Return"),
			"fieldtype": "Link",
			"options": DOCTYPE,
			"width": 150,
		},
		{"fieldname": "supplier_name", "label": _("Supplier"), "fieldtype": "Data", "width": 200},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 150},
		{"fieldname": "requested_resolution", "label": _("Asked For"), "fieldtype": "Data", "width": 130},
		{"fieldname": "sent_back_on", "label": _("Sent Back On"), "fieldtype": "Date", "width": 110},
		{"fieldname": "days_waiting", "label": _("Days Waiting"), "fieldtype": "Int", "width": 100},
		{"fieldname": "expected_amount", "label": _("Expected"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "amount_received", "label": _("Received"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "outstanding", "label": _("Difference"), "fieldtype": "Currency", "width": 110},
		{
			"fieldname": "money_source",
			"label": _("Where the Amount Comes From"),
			"fieldtype": "Data",
			"width": 260,
		},
		{"fieldname": "bill", "label": _("Bill"), "fieldtype": "Data", "width": 280},
		{
			"fieldname": "supplier_return_number",
			"label": _("Supplier's Return Number"),
			"fieldtype": "Data",
			"width": 150,
		},
		{
			"fieldname": "purchase_receipt",
			"label": _("Purchase Receipt"),
			"fieldtype": "Link",
			"options": "Purchase Receipt",
			"width": 150,
		},
		{
			"fieldname": "purchase_order",
			"label": _("Purchase Order"),
			"fieldtype": "Link",
			"options": "Purchase Order",
			"width": 150,
		},
	]
