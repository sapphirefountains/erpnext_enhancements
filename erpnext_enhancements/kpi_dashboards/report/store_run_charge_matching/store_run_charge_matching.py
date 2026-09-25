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
the same window rule (``store_run_matching.pair_window``). The list therefore pairs exactly as the
KPI does when counted from the same From Date. Every rule -- what counts as moved, what to do,
which bucket, a charge found by hand, a charge carrying 2210 with no trip -- is in
``kpi_dashboards/store_run_matching.py``, pure and tested bench-free; this module only reads.

Besides the KPI's rows it reads, for the matched charges and the charges that paired with none:
their own 2210 lines, the submitted **correcting Journal Entries** whose Reference Number
(``cheque_no``) names them (the one fix advised for a charge already submitted -- amending a
QuickBooks-synced entry would detach it from ``tabQuickBooks Sync Mapping`` and so from the
pairing), and the Purchase Invoices billed from the trips' receipts.

**Known limit**: a standalone Purchase Invoice with *Update Stock* ticked pairs as a charge, but
its stock lines post to the warehouse account, not 2210, so the report reads nothing on 2210 for
it and its *What to Do* cannot be computed (the goods would be in stock twice). Production has
none (2026-09-25).
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

	# The KPI's reader, from STORE_RUN_LOOKBACK_DAYS before the From Date; pair_window keeps the
	# charges up to STORE_RUN_PAIR_DAYS after the To Date and pairs them as the KPI does.
	charges, receipts = snapshots._store_run_rows(suppliers, from_date)
	trips, unpaired = matching.pair_window(
		charges, receipts, from_date, to_date, store_key, store=filters.get("store")
	)
	accounts = _accounts(trips, unpaired)
	on_2210, corrections = _on_2210(matching.vouchers(trips, unpaired), accounts)
	rows = matching.build_rows(
		trips,
		on_2210=on_2210,
		billed=_billed(trips),
		accounts=accounts,
		name_of=get_fullname,
		corrections=corrections,
		unpaired=unpaired,
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


def _accounts(trips, unpaired):
	"""``{company: its Stock Received But Not Billed account}`` for the companies of the trips'
	receipts and of the charges that paired with no trip."""
	companies = {row.get("company") for trip in trips for row in trip["receipts"] if row.get("company")}
	companies |= {bill["row"].get("company") for bill in unpaired if bill["row"].get("company")}
	accounts = {}
	for company in sorted(companies):
		account = frappe.get_cached_value("Company", company, "stock_received_but_not_billed")
		if account:
			accounts[company] = account
	return accounts


def _on_2210(vouchers, accounts):
	"""``(on_2210, corrections)`` for the charges in ``vouchers`` (``store_run_matching.vouchers``).

	* ``on_2210``: ``{(voucher_type, voucher_no): net debit on 2210}``, read from the voucher's own
	  lines, so a draft (which has no GL yet) is read like a submitted one: a Journal Entry's account
	  rows, and a Purchase Invoice's item rows whose expense account is 2210 (ERPNext books a stock
	  line of an invoice with no receipt there).
	* ``corrections``: ``{voucher_no: {entry: net debit on 2210}}``, every **submitted** Journal Entry
	  whose Reference Number (``cheque_no``) is one of the charges -- the correcting entry the report
	  advises for a charge already submitted. Matched ignoring case and surrounding spaces, as it is
	  typed by hand; a charge is never counted as its own correction. ``build_rows`` adds the two.
	"""
	names = [name for found in vouchers.values() for name in found]
	if not accounts or not names:
		return {}, {}

	on_2210 = {}
	params = {"accounts": tuple(sorted(set(accounts.values())))}
	if vouchers.get("Journal Entry"):
		for row in frappe.db.sql(
			"""
			select jea.parent as voucher_no, coalesce(sum(jea.debit), 0) - coalesce(sum(jea.credit), 0) as amount
			from `tabJournal Entry Account` jea
			where jea.parenttype = 'Journal Entry' and jea.parent in %(names)s and jea.account in %(accounts)s
			group by jea.parent
			""",
			dict(params, names=tuple(vouchers["Journal Entry"])),
			as_dict=True,
		):
			on_2210[("Journal Entry", row.voucher_no)] = row.amount
	if vouchers.get("Purchase Invoice"):
		for row in frappe.db.sql(
			"""
			select pii.parent as voucher_no, coalesce(sum(pii.base_net_amount), 0) as amount
			from `tabPurchase Invoice Item` pii
			where pii.parenttype = 'Purchase Invoice' and pii.parent in %(names)s
				and pii.expense_account in %(accounts)s
			group by pii.parent
			""",
			dict(params, names=tuple(vouchers["Purchase Invoice"])),
			as_dict=True,
		):
			on_2210[("Purchase Invoice", row.voucher_no)] = row.amount

	# The correcting entries. trim() and the case-insensitive lookup below forgive how a Reference
	# Number is typed; `je.name not in` keeps a charge from correcting itself.
	canonical = {name.lower(): name for name in names}
	corrections = {}
	for row in frappe.db.sql(
		"""
		select trim(je.cheque_no) as reference, je.name as entry,
			coalesce(sum(jea.debit), 0) - coalesce(sum(jea.credit), 0) as amount
		from `tabJournal Entry` je
		join `tabJournal Entry Account` jea on jea.parent = je.name and jea.parenttype = 'Journal Entry'
		where je.docstatus = 1 and trim(je.cheque_no) in %(names)s and je.name not in %(names)s
			and jea.account in %(accounts)s
		group by je.name, trim(je.cheque_no)
		""",
		dict(params, names=tuple(sorted(names))),
		as_dict=True,
	):
		charge = canonical.get(str(row.reference or "").strip().lower())
		if charge:
			corrections.setdefault(charge, {})[row.entry] = row.amount
	return on_2210, corrections


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
				"<b>At the QuickBooks cutover (runbook step S-D), set From Date to 2026-01-01 and Show to "
				"<i>Needs action</i>, and do what each row says before the submit loop.</b> A draft QuickBooks "
				"card charge that matches a recorded store run has its goods debit moved to 2210 for the trip's "
				"stock lines before tax and is saved; the loop submits it. The tax and any non-stock line stay on "
				"the expense account. A charge already submitted is fixed with one correcting Journal Entry whose "
				"Reference Number is the charge's name, which this report counts; never amend a QuickBooks charge, "
				"which detaches it from its sync mapping and so from this list. A row leaves <i>Needs action</i> "
				"once the charge carries exactly that 2210 debit."
			),
			_(
				"<b>Then set Show to <i>Waiting</i></b> and check each trip dated on or before the last "
				"QuickBooks sync: its charge did not pair (a bank-feed date more than 3 days late, an amount "
				"outside the tolerance). Find its draft by hand and move exactly the trip's stock lines to 2210; "
				"the report then shows it as the trip's charge (Match Basis <i>Its 2210 debit</i>). If there is "
				"none, bill the trip from its receipts after the cutover. A charge that carries 2210 but matches no "
				"recorded store run is listed under <i>Needs action</i>, to be moved back to the expense."
			),
			_(
				"One row per store run recorded in the range. Charges are paired exactly as the Store Runs KPI "
				"pairs them: same store (Lowes and Lowe's are one), dated on the trip's day or up to 3 days after, "
				"for the receipt total, else the lines plus up to 15% tax. Trips and charges are read from 7 days "
				"before From Date and charges up to 3 days after To Date, so a trip near either edge pairs as it "
				"does in the KPI counted from the same From Date. The figures above cover every row in range, "
				"whatever Show is set to."
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
		{
			"label": _("On 2210 With No Store Run"),
			"value": summary["unmatched_on_2210"],
			"datatype": "Currency",
			"indicator": "Red" if summary["unmatched_on_2210"] else "Green",
		},
	]
