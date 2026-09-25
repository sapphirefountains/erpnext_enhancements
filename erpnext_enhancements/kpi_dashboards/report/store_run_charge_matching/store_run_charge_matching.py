# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Store Run Charge Matching -- each recorded store run beside the card charge it pairs with.

Accounting's list for step S-D of ``docs/migration/backlog-gl-posting-runbook.md``. A store run
recorded on the Stock Scan page already posted Dr 1410 / Cr 2210 (its stock lines, before tax);
QuickBooks holds the same purchase as a draft Journal Entry, Dr the expense / Cr the card. Before
the 2026 drafts are submitted, each draft listed here under **Needs action** has its goods debit
moved to 2210 for the amount the row gives, or the purchase is booked twice and 2210 never clears
(CHANGELOG 1.536.0, "For Accounting" note 2; how this list is used: CHANGELOG 1.538.0).

**Read-only.** No write of any kind and no button: every change is made on the voucher itself,
by a person, in the Desk.

**The pairing is the Store Runs KPI's own**: the rows come from ``snapshots._store_run_rows``, the
KPI's reader, and are paired by ``metrics.pair_store_runs``, the function the KPI counts with, over
the same window rule (``store_run_matching.trips_in_window``). The list and the KPI therefore
cannot disagree about which charge belongs to which trip. Every rule -- what counts as moved, what
to do, which bucket -- is in ``kpi_dashboards/store_run_matching.py``, pure and tested bench-free;
this module only reads.
"""

import frappe
from frappe import _
from frappe.utils import add_days, get_fullname, getdate, nowdate

from erpnext_enhancements.inventory_enhancements.stock_scan_rules import store_key
from erpnext_enhancements.kpi_dashboards import snapshots
from erpnext_enhancements.kpi_dashboards import store_run_matching as matching

#: The default range: the last 60 days to today.
DEFAULT_DAYS = 60


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date, to_date = _window(filters)
	if from_date > to_date:
		frappe.throw(_("From Date must be on or before To Date."))

	suppliers = snapshots._store_run_suppliers()
	if not suppliers:
		return (
			get_columns(),
			[],
			_("No Supplier is ticked <b>Store-Run Vendor</b>, so there is no store run to match."),
		)

	# The KPI's reader, from STORE_RUN_LOOKBACK_DAYS before the From Date; trips_in_window keeps
	# the charges up to STORE_RUN_PAIR_DAYS after the To Date and pairs them as the KPI does.
	charges, receipts = snapshots._store_run_rows(suppliers, from_date)
	trips = matching.trips_in_window(
		charges, receipts, from_date, to_date, store_key, store=filters.get("store")
	)
	accounts = _accounts(trips)
	rows = matching.build_rows(
		trips,
		on_2210=_on_2210(trips, accounts),
		billed=_billed(trips),
		accounts=accounts,
		name_of=get_fullname,
	)
	summary = matching.summarize(rows)
	data = matching.filter_rows(rows, filters.get("show"))
	return get_columns(), data, _message(), None, _report_summary(summary)


def _window(filters):
	to_date = getdate(filters.get("to_date") or nowdate())
	from_date = getdate(filters.get("from_date") or add_days(to_date, -DEFAULT_DAYS))
	return from_date, to_date


def get_columns():
	return [
		{"label": _("Trip Day"), "fieldname": "trip_day", "fieldtype": "Date", "width": 100},
		{"label": _("Store"), "fieldname": "store", "fieldtype": "Link", "options": "Supplier", "width": 130},
		{"label": _("Run / Receipt No."), "fieldname": "run_ref", "fieldtype": "Data", "width": 200},
		{"label": _("Receipts"), "fieldname": "receipts", "fieldtype": "Int", "width": 80},
		{
			"label": _("First Receipt"),
			"fieldname": "first_receipt",
			"fieldtype": "Link",
			"options": "Purchase Receipt",
			"width": 160,
		},
		{"label": _("Recorded By"), "fieldname": "recorded_by", "fieldtype": "Data", "width": 140},
		{
			"label": _("Lines Before Tax"),
			"fieldname": "lines_before_tax",
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"label": _("Stock Lines Before Tax (Move to 2210)"),
			"fieldname": "stock_amount",
			"fieldtype": "Currency",
			"width": 150,
		},
		{
			"label": _("Receipt Total (Tax Included)"),
			"fieldname": "receipt_total",
			"fieldtype": "Currency",
			"width": 140,
		},
		{
			"label": _("Matched Charge"),
			"fieldname": "charge",
			"fieldtype": "Dynamic Link",
			"options": "charge_type",
			"width": 170,
		},
		{"label": _("Charge Date"), "fieldname": "charge_date", "fieldtype": "Date", "width": 100},
		{"label": _("Charge Amount"), "fieldname": "charge_amount", "fieldtype": "Currency", "width": 120},
		{"label": _("Charge Source"), "fieldname": "charge_source", "fieldtype": "Data", "width": 200},
		{"label": _("Match Basis"), "fieldname": "match_basis", "fieldtype": "Data", "width": 150},
		{"label": _("Charge Status"), "fieldname": "charge_status", "fieldtype": "Data", "width": 100},
		{"label": _("Moved to 2210"), "fieldname": "moved_to_2210", "fieldtype": "Check", "width": 110},
		{"label": _("What to Do"), "fieldname": "action", "fieldtype": "Data", "width": 460},
		# Hidden support column for the Dynamic Link above.
		{"label": _("Charge Type"), "fieldname": "charge_type", "fieldtype": "Data", "width": 1, "hidden": 1},
	]


def _accounts(trips):
	"""``{company: its Stock Received But Not Billed account}`` for the companies of the trips."""
	companies = {row.get("company") for trip in trips for row in trip["receipts"] if row.get("company")}
	accounts = {}
	for company in sorted(companies):
		account = frappe.get_cached_value("Company", company, "stock_received_but_not_billed")
		if account:
			accounts[company] = account
	return accounts


def _on_2210(trips, accounts):
	"""``{(voucher_type, voucher_no): net debit on 2210}`` for every matched charge.

	Read from the voucher's own lines, so a draft (which has no GL yet) is read like a submitted
	one: a Journal Entry's account rows, and a Purchase Invoice's item rows whose expense account
	is 2210 (ERPNext books a stock line of an invoice with no receipt there).
	"""
	names = {"Journal Entry": set(), "Purchase Invoice": set()}
	for trip in trips:
		charge = trip.get("charge")
		if charge is None:
			continue
		row = charge["row"]
		if row.get("voucher_type") in names and row.get("voucher_no"):
			names[row["voucher_type"]].add(row["voucher_no"])
	if not accounts or not any(names.values()):
		return {}

	on_2210 = {}
	params = {"accounts": tuple(sorted(set(accounts.values())))}
	if names["Journal Entry"]:
		for row in frappe.db.sql(
			"""
			select jea.parent as voucher_no, coalesce(sum(jea.debit), 0) - coalesce(sum(jea.credit), 0) as amount
			from `tabJournal Entry Account` jea
			where jea.parenttype = 'Journal Entry' and jea.parent in %(names)s and jea.account in %(accounts)s
			group by jea.parent
			""",
			dict(params, names=tuple(sorted(names["Journal Entry"]))),
			as_dict=True,
		):
			on_2210[("Journal Entry", row.voucher_no)] = row.amount
	if names["Purchase Invoice"]:
		for row in frappe.db.sql(
			"""
			select pii.parent as voucher_no, coalesce(sum(pii.base_net_amount), 0) as amount
			from `tabPurchase Invoice Item` pii
			where pii.parenttype = 'Purchase Invoice' and pii.parent in %(names)s
				and pii.expense_account in %(accounts)s
			group by pii.parent
			""",
			dict(params, names=tuple(sorted(names["Purchase Invoice"]))),
			as_dict=True,
		):
			on_2210[("Purchase Invoice", row.voucher_no)] = row.amount
	return on_2210


def _billed(trips):
	"""``{receipt: Purchase Invoice}``: the trips' receipts a submitted invoice was made from.

	After the cutover a recorded trip is billed from its receipts (*Get Items From -> Purchase
	Receipt*); that invoice clears 2210 itself and is the same trip, not a charge, so the KPI never
	pairs it. The earliest invoice per receipt is named.
	"""
	receipts = sorted(
		{row.get("receipt") for trip in trips for row in trip["receipts"] if row.get("receipt")}
	)
	if not receipts:
		return {}
	billed = {}
	for row in frappe.db.sql(
		"""
		select pii.purchase_receipt as receipt, pii.parent as invoice
		from `tabPurchase Invoice Item` pii
		join `tabPurchase Invoice` pi on pi.name = pii.parent
		where pii.parenttype = 'Purchase Invoice' and pi.docstatus = 1 and pi.is_return = 0
			and pii.purchase_receipt in %(receipts)s
		order by pi.posting_date, pi.name
		""",
		{"receipts": tuple(receipts)},
		as_dict=True,
	):
		billed.setdefault(row.receipt, row.invoice)
	return billed


def _message():
	return "<br><br>".join(
		[
			_(
				"<b>At the QuickBooks cutover (runbook step S-D), set Show to <i>Needs action</i> and do what "
				"each row says before the submit loop.</b> A draft QuickBooks card charge that matches a recorded "
				"store run has its goods debit moved to 2210 for the trip's stock lines before tax, then is "
				"submitted; the tax and any non-stock line stay on the expense account. A row leaves "
				"<i>Needs action</i> once the draft carries exactly that 2210 debit."
			),
			_(
				"One row per store run recorded in the range. Charges are paired exactly as the Store Runs KPI "
				"pairs them: same store (Lowes and Lowe's are one), dated on the trip's day or up to 3 days after, "
				"for the receipt total, else the lines plus up to 15% tax. Trips and charges are read from 7 days "
				"before From Date and charges up to 3 days after To Date, so a trip near either edge pairs as it "
				"does in the KPI. The figures above cover every trip in range, whatever Show is set to."
			),
		]
	)


def _report_summary(summary):
	return [
		{"label": _("Store Runs"), "value": summary["trips"], "datatype": "Int"},
		{"label": _("Matched to a Charge"), "value": summary["matched"], "datatype": "Int"},
		{
			"label": _("Needs Action"),
			"value": summary["needs_action"],
			"datatype": "Int",
			"indicator": "Red" if summary["needs_action"] else "Green",
		},
		{
			"label": _("Still to Move to 2210"),
			"value": summary["to_move"],
			"datatype": "Currency",
			"indicator": "Red" if summary["to_move"] else "Green",
		},
		{"label": _("Already Moved to 2210"), "value": summary["moved"], "datatype": "Currency"},
	]
