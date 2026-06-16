"""Build ERPNext opening balances from QuickBooks Online.

Reconstructs a company's opening position in ERPNext as a single balanced
"Opening Entry" Journal Entry, sourced from QBO's Reports API and party balances
(the same data the manual cut-over reconciliation in MIGRATION_NOTES.md uses,
automated):

  * Per-account balances come from the QBO Trial Balance report as of the cutoff
    (``reconcile._fetch_trial_balance``), mapped to ERPNext accounts via the sync
    ledger -- one debit/credit line per leaf account.
  * Accounts Receivable / Payable are broken out by party (each Customer's /
    Vendor's open ``Balance``) because ERPNext requires a party on every line
    posting to a Receivable/Payable account -- a lump line is rejected.
  * Stock accounts are excluded (their opening value must come from a Stock
    Reconciliation, not a Journal Entry) and reported back for follow-up.
  * Any residual is squared off against the company's Temporary Opening account.

The Journal Entry is created as a **draft by default** so it can be reviewed
before it posts; pass ``auto_submit`` to submit it. Party balances use QBO's
*current* open balance, which equals the as-of balance for a present-day cut-over
(the common case); for a historical cutoff, review the draft against QBO's A/R &
A/P aging before submitting.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, today

from erpnext_enhancements.quickbooks_online.core.mapping import _linked_name
from erpnext_enhancements.quickbooks_online.core.reconcile import _fetch_trial_balance
from erpnext_enhancements.quickbooks_online.core.sync import fail_log, finish_log, start_log
from erpnext_enhancements.quickbooks_online.core.utils import get_settings

# QBO entity -> (ERPNext party DocType, company default account field, which side
# a positive QBO balance posts to). A customer who owes us is a receivable debit;
# a vendor we owe is a payable credit.
PARTY_SOURCES = {
	"Customer": ("Customer", "default_receivable_account", "debit"),
	"Vendor": ("Supplier", "default_payable_account", "credit"),
}


def sync_opening_balances(as_of_date: str | None = None, auto_submit: bool | int = 0):
	"""Create a balanced opening Journal Entry in ERPNext from QBO balances.

	``as_of_date`` defaults to today. ``auto_submit`` (falsy by default) leaves the
	Journal Entry as a draft for review. Opens an "Opening Balances" sync log,
	builds the lines (see module docstring) and inserts one Journal Entry. Returns
	``{sync_log, journal_entry, docstatus, skipped_stock, unmapped, ...}``. Raises
	(and fails the log) on error; re-raises after logging.
	"""
	settings = get_settings()
	if not settings.realm_id:
		frappe.throw(_("Connect QuickBooks Online before importing opening balances."))
	company = settings.company
	if not company:
		frappe.throw(_("Set the Company on QuickBooks Online Settings before importing opening balances."))
	as_of_date = getdate(as_of_date) if as_of_date else getdate(today())
	auto_submit = cint(auto_submit)

	log = start_log("Opening Balances")
	try:
		rows, meta = _build_opening_rows(settings, company, as_of_date)
		journal_entry = None
		docstatus = None
		if rows:
			je = _create_opening_journal_entry(company, as_of_date, rows, auto_submit)
			journal_entry = je.name
			docstatus = je.docstatus
			log.created_count = 1
		finish_log(log)
		frappe.db.commit()
		return {
			"sync_log": log.name,
			"journal_entry": journal_entry,
			"docstatus": docstatus,
			"as_of_date": str(as_of_date),
			**meta,
		}
	except Exception as exc:
		fail_log(log, exc)
		raise


def _build_opening_rows(settings, company, as_of_date):
	"""Assemble the Journal Entry account rows and a metadata summary.

	Returns ``(rows, meta)`` where ``rows`` is the list of JE account dicts and
	``meta`` reports accounts that could not be placed (``unmapped``) and stock
	accounts excluded from the JE (``skipped_stock``) so the caller can flag them.
	"""
	trial_balance = _fetch_trial_balance(settings, as_of_date)
	accounts = _account_index(company)

	rows: list[dict] = []
	skipped_stock: list[str] = []
	unmapped: list[dict] = []

	# Accounts Receivable / Payable: one party line per Customer / Vendor balance.
	for qbo_entity, (party_type, default_field, side) in PARTY_SOURCES.items():
		party_account = frappe.db.get_value("Company", company, default_field)
		if party_account:
			_append_party_lines(settings, qbo_entity, party_type, party_account, side, rows)

	# Every other leaf account: one line at its Trial Balance balance.
	for qbo_id, data in trial_balance.items():
		balance = flt(data.get("qb_balance"))
		if abs(balance) < 0.005:
			continue
		info = accounts.get(qbo_id)
		if not info:
			unmapped.append({"qb_id": qbo_id, "qb_name": data.get("qb_name"), "balance": balance})
			continue
		if info["is_group"] or info["account_type"] in ("Receivable", "Payable"):
			continue
		if info["account_type"] == "Stock":
			skipped_stock.append(info["account"])
			continue
		rows.append(_opening_account_line(info["account"], balance))

	plug = _plug_line(rows, company)
	if plug:
		rows.append(plug)
	return rows, {"skipped_stock": skipped_stock, "unmapped": unmapped, "line_count": len(rows)}


def _append_party_lines(settings, qbo_entity, party_type, party_account, side, rows):
	"""Append one opening line per party with an open QBO balance (live query).

	Skips parties not yet mapped into ERPNext and zero balances. ``side`` is the
	natural side for a positive balance; a negative QBO balance (e.g. a customer
	credit) flips to the opposite column.
	"""
	from erpnext_enhancements.quickbooks_online.core.sync import query_all

	erpnext_doctype = "Customer" if party_type == "Customer" else "Supplier"
	for payload in query_all(qbo_entity, settings=settings):
		balance = flt(payload.get("Balance"))
		if abs(balance) < 0.005:
			continue
		party = _linked_name(qbo_entity, erpnext_doctype, payload.get("Id"))
		if not party:
			continue
		rows.append(_party_opening_line(party_account, party_type, party, balance, side))


def _opening_account_line(account, balance):
	"""Pure: a JE account row placing a signed balance on the correct column."""
	balance = flt(balance)
	return {
		"account": account,
		"debit_in_account_currency": balance if balance > 0 else 0,
		"credit_in_account_currency": -balance if balance < 0 else 0,
	}


def _party_opening_line(account, party_type, party, balance, side):
	"""Pure: a party JE row (Receivable/Payable) honouring the natural side + sign."""
	signed = flt(balance) if side == "debit" else -flt(balance)
	return {
		"account": account,
		"party_type": party_type,
		"party": party,
		"debit_in_account_currency": signed if signed > 0 else 0,
		"credit_in_account_currency": -signed if signed < 0 else 0,
	}


def _plug_line(rows, company):
	"""Pure-ish: the Temporary Opening row that squares the entry, or None.

	Returns None when the rows already balance (to within rounding). Raises if an
	offset is needed but the company has no Temporary Opening account.
	"""
	total_debit = sum(flt(row.get("debit_in_account_currency")) for row in rows)
	total_credit = sum(flt(row.get("credit_in_account_currency")) for row in rows)
	net = round(total_debit - total_credit, 2)
	if abs(net) < 0.005:
		return None
	temporary = frappe.db.get_value(
		"Account", {"company": company, "account_type": "Temporary", "is_group": 0}, "name"
	)
	if not temporary:
		frappe.throw(
			_(
				"An opening-balance offset of {0} is required but no Temporary Opening account "
				"(Account Type = Temporary) exists for {1}. Create one and retry."
			).format(net, company)
		)
	# net > 0 => debits exceed credits => the plug must be a credit (and vice versa).
	return {
		"account": temporary,
		"debit_in_account_currency": -net if net < 0 else 0,
		"credit_in_account_currency": net if net > 0 else 0,
	}


def _account_index(company):
	"""``{qbo_account_id: {account, account_type, root_type, is_group}}`` from the ledger.

	Resolves every mapped (Account or TaxCode) QBO id to its ERPNext Account plus
	the metadata the row builder needs to classify it (party/stock/group).
	"""
	index: dict[str, dict] = {}
	for mapping in frappe.get_all(
		"QuickBooks Sync Mapping",
		filters={
			"erpnext_doctype": "Account",
			"deleted": 0,
			"qbo_entity_type": ["in", ["Account", "TaxCode"]],
		},
		fields=["qbo_id", "erpnext_name"],
	):
		account = mapping.erpnext_name
		if not account or not frappe.db.exists("Account", account):
			continue
		row = frappe.db.get_value("Account", account, ["account_type", "root_type", "is_group"], as_dict=True)
		index[str(mapping.qbo_id)] = {
			"account": account,
			"account_type": row.account_type,
			"root_type": row.root_type,
			"is_group": cint(row.is_group),
		}
	return index


def _create_opening_journal_entry(company, as_of_date, rows, auto_submit):
	"""Insert the opening Journal Entry (draft unless ``auto_submit``)."""
	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Opening Entry"
	je.is_opening = "Yes"
	je.company = company
	je.posting_date = as_of_date
	je.user_remark = f"Opening balances imported from QuickBooks Online as of {as_of_date}"
	for row in rows:
		je.append("accounts", row)
	je.insert(ignore_permissions=True)
	if auto_submit:
		je.submit()
	return je
