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
the same window rule (``store_run_matching.match_window``). The list therefore pairs exactly as the
KPI does when counted from the same From Date, except where a Reference Number links a charge to
its trips or says ``not-store-run``. Every rule -- what counts as moved, what to do, which bucket,
what a Reference Number says, where each 2210 line is accounted for -- is in
``kpi_dashboards/store_run_matching.py``, pure and tested bench-free; this module only reads.

Besides the KPI's rows it reads: every Journal Entry with a **Reference Number** (``cheque_no``),
whatever its vendor -- a charge linked to its trips by their run ids, a **correcting Journal
Entry** naming a charge (the one fix advised for a charge already submitted; amending a
QuickBooks-synced entry would detach it from ``tabQuickBooks Sync Mapping`` and so from the
pairing), or one saying ``not-store-run``; the earlier trips and the Journal Entries those
Reference Numbers name that nothing else read, round after round, so a chain of correcting entries
is followed to its charge; **every Journal Entry line on a company's Stock Received But Not Billed
account** from the reader's lookback to the To Date (the backstop: each must be accounted for);
the 2210 lines of the charges and of their correcting entries; and the Purchase Invoices billed
from the trips' receipts.

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

#: How many times the tokens nothing read are looked up. Each round reads the trips and Journal
#: Entries the last round's Reference Numbers name, so a chain of correcting entries dated before
#: the lookback is followed this many links deep.
LOOKUP_ROUNDS = 4


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

	# The KPI's reader, from STORE_RUN_LOOKBACK_DAYS before the From Date; match_window keeps the
	# charges up to STORE_RUN_PAIR_DAYS after the To Date and pairs them as the KPI does.
	charges, receipts = snapshots._store_run_rows(suppliers, from_date)
	early = add_days(from_date, -snapshots.STORE_RUN_LOOKBACK_DAYS)
	# Then what the Reference Numbers say: links from a charge to its trips, correcting entries,
	# not-store-run. The keys nothing read are looked up as earlier trips (a link resolves by key,
	# whatever the trip's date) and as Journal Entries, and the tokens of what that finds in turn.
	references = _references(early)
	far_receipts, named, asked = [], [], set()
	for _round in range(LOOKUP_ROUNDS):
		unknown = [
			token
			for token in matching.unresolved_tokens(references + named, charges, receipts + far_receipts)
			if token not in asked
		]
		if not unknown:
			break
		asked.update(unknown)
		far_receipts += _receipts_by_key(suppliers, unknown)
		named += _entries_by_name(unknown)
	resolution = matching.resolve_references(references, charges, receipts, far_receipts, named)
	window = matching.match_window(
		charges,
		receipts,
		from_date,
		to_date,
		store_key,
		store=filters.get("store"),
		links=resolution["links"],
		far_receipts=far_receipts,
		excluded=resolution["excluded"],
		roots=resolution["roots"],
	)
	# The backstop: every Journal Entry line on 2210 in the window, each to be accounted for.
	journal = _journal_2210(early, to_date)
	found = matching.vouchers(window["trips"], window["unpaired"], window["outside"])
	on_2210 = {("Journal Entry", entry.name): entry.on_2210 for entry in journal}
	on_2210.update(
		_on_2210(
			{
				"Journal Entry": found["Journal Entry"]
				+ matching.correcting_entries(found, resolution["corrections"], resolution["drafts"]),
				"Purchase Invoice": found["Purchase Invoice"],
			}
		)
	)
	rows = matching.build_rows(
		window["trips"],
		on_2210=on_2210,
		billed=_billed(matching.receipt_names(window["trips"], window["outside"])),
		accounts=_accounts(matching.companies(window, journal, resolution)),
		name_of=get_fullname,
		corrections=matching.correction_amounts(resolution["corrections"], on_2210),
		unpaired=window["unpaired"],
		window=window,
		resolution=resolution,
		journal=journal,
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


def _references(early):
	"""Every Journal Entry, draft or submitted, dated from ``early`` (the reader's lookback before the
	From Date) whose Reference Number (``cheque_no``) is filled in, for
	``store_run_matching.resolve_references``: a charge linked to its trips, a correcting entry, or
	one saying ``not-store-run``.

	**Not limited to the store-run vendors**: a QuickBooks draft under a vendor that is not ticked
	*Store-Run Vendor* is invisible to the KPI's reader, and linking it by Reference Number is how
	Accounting tells the report it is a trip's charge. The keys are matched in Python
	(``reference_tokens``), so a Reference Number listing several is read once. A charge is never
	dated before its purchase and a correcting entry never before its charge, so nothing dated
	before ``early`` bears on a row; an entry dated earlier that a Reference Number names is looked
	up by name. 0 rows on production on 2026-09-25 (no Journal Entry there carries a Reference
	Number yet).

	The QuickBooks mapping is joined once, as a derived table, rather than probed per row with a
	correlated ``exists``: ``tabQuickBooks Sync Mapping`` has no index on ``erpnext_name``, and on
	production the ``exists`` form took 2.8 s over 826 entries where the join took 26 ms.
	"""
	return frappe.db.sql(
		"""
		select je.name, je.cheque_no as reference, je.docstatus, je.posting_date as day, je.company,
			je.total_debit as amount, if(qm.erpnext_name is null, 'ERPNext', 'QuickBooks') as source
		from `tabJournal Entry` je
		left join (
			select distinct m.erpnext_name from `tabQuickBooks Sync Mapping` m
			where m.erpnext_doctype = 'Journal Entry'
		) qm on qm.erpnext_name = je.name
		where je.docstatus < 2 and je.cheque_no <> '' and je.posting_date >= %(early)s
		order by je.name
		""",
		{"early": early},
		as_dict=True,
	)


def _entries_by_name(names):
	"""The Journal Entries (draft or submitted) named by ``names``, in the shape of
	:func:`_references`: an entry a Reference Number names that nothing else read -- a QuickBooks
	charge under a vendor that is not a store-run vendor, submitted before it was linked, or a
	correcting entry dated before the lookback that a later one names."""
	if not names:
		return []
	return frappe.db.sql(
		"""
		select je.name, je.cheque_no as reference, je.docstatus, je.posting_date as day, je.company,
			je.total_debit as amount, if(qm.erpnext_name is null, 'ERPNext', 'QuickBooks') as source
		from `tabJournal Entry` je
		left join (
			select distinct m.erpnext_name from `tabQuickBooks Sync Mapping` m
			where m.erpnext_doctype = 'Journal Entry'
		) qm on qm.erpnext_name = je.name
		where je.docstatus < 2 and je.name in %(names)s
		order by je.name
		""",
		{"names": tuple(names)},
		as_dict=True,
	)


def _journal_2210(early, to_date):
	"""The backstop: every Journal Entry, draft or submitted (not cancelled), with a line on its own
	company's Stock Received But Not Billed account, dated from ``early`` (the reader's lookback) to
	``to_date``, with its net debit there (``on_2210``) and the columns of :func:`_references`.

	``store_run_matching.attribute_2210`` accounts for each one: the charge of a trip, a correcting
	entry of one, or a row of its own. Read from the entries' own lines, so a draft (no GL yet) is
	read like a submitted one, as :func:`_on_2210` reads the charges. Whatever its Reference Number:
	an entry adjusted for a trip but never linked, or linked by a key that names nothing, is found
	here by its money. 0 rows on production on 2026-09-25 (no Journal Entry line there posts to
	2210 yet). The lines are summed in a derived table, so the outer query groups nothing.
	"""
	return frappe.db.sql(
		"""
		select je.name, je.cheque_no as reference, je.docstatus, je.posting_date as day, je.company,
			je.total_debit as amount, if(qm.erpnext_name is null, 'ERPNext', 'QuickBooks') as source,
			b.on_2210
		from (
			select jea.parent, coalesce(sum(jea.debit), 0) - coalesce(sum(jea.credit), 0) as on_2210
			from `tabJournal Entry Account` jea
			join `tabJournal Entry` j on j.name = jea.parent
			join `tabCompany` c on c.name = j.company and c.stock_received_but_not_billed = jea.account
			where jea.parenttype = 'Journal Entry' and j.docstatus < 2
				and j.posting_date >= %(early)s and j.posting_date <= %(to_date)s
			group by jea.parent
		) b
		join `tabJournal Entry` je on je.name = b.parent
		left join (
			select distinct m.erpnext_name from `tabQuickBooks Sync Mapping` m
			where m.erpnext_doctype = 'Journal Entry'
		) qm on qm.erpnext_name = je.name
		order by je.name
		""",
		{"early": early, "to_date": to_date},
		as_dict=True,
	)


def _receipts_by_key(suppliers, keys):
	"""The receipts of the trips ``keys`` name that the KPI's reader did not read: store runs dated
	before its lookback, which a link still reaches. A key is a run id or the name of any receipt of
	the trip (the report's First Receipt, whether or not the receipt has a run id); a receipt named
	by its name brings the rest of its run with it. MariaDB's case-insensitive collation matches the
	keys as ``reference_tokens`` lower-cases them.

	The same columns and the same rules as the receipts of ``snapshots._store_run_rows`` (submitted,
	not a return, from a store-run vendor, no PO line), read by key instead of by date;
	``tests/test_store_run_matching.py`` compares the two queries. Before
	``patches/add_store_run_receipt_fields`` has run there is no run id to look up.
	"""
	if not keys:
		return []
	for field in (snapshots.STORE_RUN_RECEIPT_FIELD, snapshots.STORE_RUN_TOTAL_FIELD):
		if not frappe.db.has_column("Purchase Receipt", field):
			return []
	return frappe.db.sql(
		"""
		select pr.supplier, pr.posting_date as day, coalesce(nullif(pr.`custom_store_run`, ''), pr.name) as run,
			pr.base_grand_total as amount, coalesce(pr.`custom_receipt_total`, 0) as receipt_total,
			coalesce(pr.supplier_delivery_note, '') as receipt_number,
			pr.name as receipt, pr.company, pr.owner as recorded_by, pr.base_net_total as net_amount,
			exists(
				select 1 from `tabStock Ledger Entry` sle
				where sle.voucher_type = 'Purchase Receipt' and sle.voucher_no = pr.name
					and sle.is_cancelled = 0
			) as is_stock_item,
			(
				select coalesce(sum(g.credit) - sum(g.debit), 0) from `tabGL Entry` g
				where g.voucher_type = 'Purchase Receipt' and g.voucher_no = pr.name and g.is_cancelled = 0
					and g.account = (
						select c.stock_received_but_not_billed from `tabCompany` c where c.name = pr.company
					)
			) as stock_amount
		from `tabPurchase Receipt` pr
		where pr.docstatus = 1 and pr.is_return = 0
			and coalesce(nullif(pr.`custom_store_run`, ''), pr.name) in (
				select coalesce(nullif(k.`custom_store_run`, ''), k.name) from `tabPurchase Receipt` k
				where k.name in %(keys)s or coalesce(nullif(k.`custom_store_run`, ''), k.name) in %(keys)s
			)
			and pr.supplier in %(suppliers)s
			and not exists (
				select 1 from `tabPurchase Receipt Item` i
				where i.parent = pr.name and coalesce(i.purchase_order, '') <> ''
			)
		order by pr.posting_date, pr.name
		""",
		{"keys": tuple(keys), "suppliers": tuple(suppliers)},
		as_dict=True,
	)


def _accounts(companies):
	"""``{company: its Stock Received But Not Billed account}`` for ``companies``
	(``store_run_matching.companies``: of every receipt, charge and entry a row may name)."""
	accounts = {}
	for company in companies:
		account = frappe.get_cached_value("Company", company, "stock_received_but_not_billed")
		if account:
			accounts[company] = account
	return accounts


def _on_2210(vouchers):
	"""``{(voucher_type, voucher_no): net debit on 2210}`` for the vouchers in ``vouchers``: the
	charges (``store_run_matching.vouchers``) and their correcting entries, submitted and draft
	(``store_run_matching.correcting_entries``), whatever their date.

	Read from each voucher's own lines, so a draft (which has no GL yet) is read like a submitted
	one: a Journal Entry's account rows, and a Purchase Invoice's item rows whose expense account is
	2210 (ERPNext books a stock line of an invoice with no receipt there). **Company-scoped**, as the
	backstop (:func:`_journal_2210`) is: a line counts on the Stock Received But Not Billed account
	of the voucher's own company, so an entry read both ways reads the same. Which entry corrects
	which charge was decided from the Reference Numbers (``store_run_matching.resolve_references``);
	``store_run_matching.correction_amounts`` pairs the two up and ``build_rows`` adds them.
	"""
	on_2210 = {}
	if vouchers.get("Journal Entry"):
		for row in frappe.db.sql(
			"""
			select jea.parent as voucher_no, coalesce(sum(jea.debit), 0) - coalesce(sum(jea.credit), 0) as amount
			from `tabJournal Entry Account` jea
			join `tabJournal Entry` j on j.name = jea.parent
			join `tabCompany` c on c.name = j.company and c.stock_received_but_not_billed = jea.account
			where jea.parenttype = 'Journal Entry' and jea.parent in %(names)s
			group by jea.parent
			""",
			{"names": tuple(vouchers["Journal Entry"])},
			as_dict=True,
		):
			on_2210[("Journal Entry", row.voucher_no)] = row.amount
	if vouchers.get("Purchase Invoice"):
		for row in frappe.db.sql(
			"""
			select pii.parent as voucher_no, coalesce(sum(pii.base_net_amount), 0) as amount
			from `tabPurchase Invoice Item` pii
			join `tabPurchase Invoice` p on p.name = pii.parent
			join `tabCompany` c on c.name = p.company and c.stock_received_but_not_billed = pii.expense_account
			where pii.parenttype = 'Purchase Invoice' and pii.parent in %(names)s
			group by pii.parent
			""",
			{"names": tuple(vouchers["Purchase Invoice"])},
			as_dict=True,
		):
			on_2210[("Purchase Invoice", row.voucher_no)] = row.amount
	return on_2210


def _billed(receipts):
	"""``{receipt: Purchase Invoice}``: the receipts (``store_run_matching.receipt_names``: the
	trips' own, those of every trip their charges are linked to, and those of a linked charge's trips
	outside the range) a submitted invoice was made from.

	After the cutover a recorded trip is billed from its receipts (*Get Items From -> Purchase
	Receipt*); that invoice clears 2210 itself and is the same trip, not a charge, so the KPI never
	pairs it. The earliest invoice per receipt is named.
	"""
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
				"the expense account. A charge already submitted is fixed by posting and submitting one "
				"correcting Journal Entry whose Reference Number is the charge's name, which this report counts "
				"once submitted; never amend a QuickBooks charge, which detaches it from its sync mapping and so "
				"from this list. A row leaves <i>Needs action</i> once the charge carries exactly that 2210 debit."
			),
			_(
				"<b>Then set Show to <i>Waiting</i></b> and check each trip dated on or before the last "
				"QuickBooks sync: its charge did not pair (a bank-feed date more than 3 days late, an amount "
				"outside the tolerance, two runs of one purchase, a vendor not ticked Store-Run Vendor). Find its "
				"draft, move the trip's stock lines to 2210 and <b>put the trip's run id in the draft's Reference "
				"Number</b> (several run ids, separated by commas or spaces, when one draft pays for several "
				"trips; keep any already there, and if the draft is already another trip's charge in this list, "
				"list both); the report then shows it as the trip's charge (Match Basis <i>Linked by Reference "
				"Number</i>). Only if the trip has no card charge at all, bill it from its receipts after the "
				"cutover; never both."
			),
			_(
				"<b>Then repeat: Show = <i>Needs action</i>, then Show = <i>Waiting</i>, until neither list "
				"changes, <i>Needs action</i> is empty and <i>2210 Not Accounted For</i> reads $0.00. Only then "
				"run the loop.</b> A link can move rows back into either list. <i>2210 Not Accounted For</i> is "
				"every Journal Entry line on 2210 from 7 days before From Date to To Date that no charge accounts "
				"for: each has a row saying why. Put <i>not-store-run</i> in the Reference Number of an entry on "
				"2210 that has nothing to do with store runs, or of a card charge that pays for none, and the "
				"report leaves it out."
			),
			_(
				"One row per store run recorded in the range. Charges are paired exactly as the Store Runs KPI "
				"pairs them: same store (Lowes and Lowe's are one), dated on the trip's day or up to 3 days after, "
				"for the receipt total, else the lines plus up to 15% tax. Trips and charges are read from 7 days "
				"before From Date and charges up to 3 days after To Date, so a trip near either edge pairs as it "
				"does in the KPI counted from the same From Date. A Reference Number that lists a trip's run id "
				"(or any of its receipts' names) overrides that pairing, whatever the trip's date. A charge that "
				"carries 2210 but is neither paired nor linked, and a Journal Entry whose Reference Number names "
				"nothing usable, are listed under <i>Needs action</i>. The figures above cover every row in "
				"range, whatever Show is set to."
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
		{
			"label": _("2210 Not Accounted For"),
			"value": summary["not_accounted"],
			"datatype": "Currency",
			"indicator": "Red" if summary["not_accounted"] else "Green",
		},
	]
