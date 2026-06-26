"""Reconcile QuickBooks Online balances and transactions against ERPNext.

A read-only verification layer on top of the import: it answers "does what we
imported actually agree with QuickBooks?" without writing anything. Two checks:

  * ``compare_account_balances`` -- pulls QBO's Trial Balance report (the
    computed per-account balance, which the Reports API returns even when
    transaction exports are empty) and compares it, account by account, against
    each linked ERPNext account's General Ledger balance as of the same date.
    This is the automated form of the post-import Trial Balance reconciliation
    described in MIGRATION_NOTES.md.

  * ``reconcile_transactions`` -- walks the ``QuickBooks Sync Mapping`` ledger
    for transaction entities and compares the amount on each stored QBO raw
    payload against the grand total of the ERPNext document it was mapped to,
    surfacing amount drift and mappings whose ERPNext document has since gone.

The bridge between the two systems is the mapping ledger (qbo_id <-> ERPNext
name), so reconciliation reflects exactly what the sync linked -- no fuzzy
re-matching here.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, getdate, today

from erpnext_enhancements.quickbooks_online.core.client import QuickBooksClient
from erpnext_enhancements.quickbooks_online.core.constants import TRANSACTION_ENTITIES
from erpnext_enhancements.quickbooks_online.core.utils import get_settings, json_loads

# ERPNext field holding the comparable "total" for each transaction DocType the
# integration creates. Journal Entries balance, so either side works -- total_debit
# is used. Payment Entries compare on the amount paid/received.
TRANSACTION_AMOUNT_FIELD = {
	"Sales Invoice": "grand_total",
	"Purchase Invoice": "grand_total",
	"Quotation": "grand_total",
	"Purchase Order": "grand_total",
	"Payment Entry": "paid_amount",
	"Journal Entry": "total_debit",
}


# ---------------------------------------------------------------------------
# Account balance comparison (QBO Trial Balance vs ERPNext General Ledger).
# ---------------------------------------------------------------------------


def compare_account_balances(as_of_date: str | None = None, tolerance: float = 0.01):
	"""Compare QBO Trial Balance balances against ERPNext GL balances per account.

	Fetches QBO's ``TrialBalance`` report (cumulative through ``as_of_date``,
	defaulting to today) and, for every ERPNext Account linked via the mapping
	ledger, the signed GL balance (debit - credit) through the same date. Buckets
	each account as matched / mismatched (difference > ``tolerance``) / QuickBooks-
	only (a balance QBO has but ERPNext has no mapped account for) / ERPNext-only.

	Read-only. ``tolerance`` is in company currency. Returns a dict with a
	``summary`` and the four buckets, consumed by the "QuickBooks Balance
	Comparison" report and the dashboard. Raises if not connected.
	"""
	settings = get_settings()
	if not settings.realm_id:
		frappe.throw("Connect QuickBooks Online before comparing balances.")
	as_of_date = getdate(as_of_date) if as_of_date else getdate(today())
	tolerance = abs(flt(tolerance))

	qb_balances = _fetch_trial_balance(settings, as_of_date)
	erp_balances = _fetch_erpnext_balances(settings.company, as_of_date)
	return _compare(qb_balances, erp_balances, tolerance, as_of_date)


def _reports_testing_migration() -> bool:
	"""Whether to preview QBO's modernized ("v2") Reports service.

	Off by default. Set ``quickbooks_reports_testing_migration`` to a truthy value
	in the site config (``common_site_config.json`` / ``site_config.json``) to route
	the Trial Balance request through Intuit's modernized Reports service via the
	temporary ``testing_migration`` flag — letting an operator validate
	reconciliation and opening balances against real data before Intuit retires the
	v1 service in 2026 (MIGRATION_NOTES §5). ``_parse_trial_balance`` is v2-safe
	either way; this only controls early opt-in.
	"""
	try:
		return bool(frappe.conf.get("quickbooks_reports_testing_migration"))
	except Exception:
		return False


def _fetch_trial_balance(settings, as_of_date) -> dict[str, dict]:
	"""Pull and parse QBO's Trial Balance into ``{qbo_account_id: {...}}``.

	The report is requested cumulatively from far in the past through
	``as_of_date`` so balance-sheet accounts reflect their full inception-to-date
	balance (a bare date_macro window would only net activity inside it).
	"""
	params = {"start_date": "1901-01-01", "end_date": str(as_of_date)}
	response = QuickBooksClient(settings).report(
		"TrialBalance", params, testing_migration=_reports_testing_migration()
	)
	return _parse_trial_balance(response)


def _resolve_amount_columns(response: dict) -> tuple[int, int]:
	"""Locate the Debit and Credit column indices from the report's ``Columns`` header.

	QBO's modernized ("v2") Reports service warns "Do not rely on row index
	positions" and standardizes ``ColTitle`` to Title Case (v1 sometimes returned
	ALL CAPS). We therefore resolve the Debit/Credit columns from the header by
	title (case-insensitive) instead of assuming fixed ``ColData`` offsets, so a
	relabelled or reordered report still parses. Falls back to the historical
	layout (account=0, debit=1, credit=2) when no usable header is present —
	preserving v1 behaviour and header-less canned fixtures.
	"""
	columns = ((response or {}).get("Columns") or {}).get("Column") or []
	debit_idx = credit_idx = None
	for idx, col in enumerate(columns):
		title = ((col or {}).get("ColTitle") or "").strip().lower()
		if title == "debit" and debit_idx is None:
			debit_idx = idx
		elif title == "credit" and credit_idx is None:
			credit_idx = idx
	return (1 if debit_idx is None else debit_idx, 2 if credit_idx is None else credit_idx)


def _parse_trial_balance(response: dict) -> dict[str, dict]:
	"""Parse a QBO TrialBalance response into ``{qbo_id: {qb_name, qb_balance}}``.

	Pure function (no I/O) so it is unit-testable against canned QBO JSON. Walks
	the nested ``Rows -> Row`` tree (sections recurse to any depth, which also
	absorbs v2's "child accounts always nested under their parent" change) and reads
	each data row's ``ColData``: the account is column 0 (value+id); the Debit and
	Credit columns are resolved from the header (see ``_resolve_amount_columns``)
	rather than hardcoded, per Intuit's v2 "do not rely on index positions". The
	balance is stored signed debit-positive (debit - credit) to match ERPNext's GL
	convention. v2 returns ``""`` (never 0/None) for an empty amount, which ``flt``
	already coerces to 0.
	"""
	balances: dict[str, dict] = {}
	debit_idx, credit_idx = _resolve_amount_columns(response)
	max_idx = max(debit_idx, credit_idx)
	rows = ((response or {}).get("Rows") or {}).get("Row") or []

	def cell(col_data, idx):
		return flt((col_data[idx] or {}).get("value") or 0) if idx < len(col_data) else 0.0

	def walk(row_list):
		for row in row_list:
			if "Rows" in row:
				walk((row.get("Rows") or {}).get("Row") or [])
				continue
			col_data = row.get("ColData")
			# Need at least the account column plus both amount columns present.
			if not col_data or len(col_data) <= max_idx:
				continue
			account_col = col_data[0]
			# Only true account rows carry an id; section headers / "Total" rows don't.
			if not isinstance(account_col, dict) or not account_col.get("id"):
				continue
			qbo_id = str(account_col.get("id"))
			balances[qbo_id] = {
				"qb_id": qbo_id,
				"qb_name": account_col.get("value"),
				"qb_balance": cell(col_data, debit_idx) - cell(col_data, credit_idx),
			}

	walk(rows)
	return balances


def _fetch_erpnext_balances(company: str, as_of_date) -> dict[str, dict]:
	"""Return ``{qbo_account_id: {erp_account, erp_name, erp_balance, ...}}``.

	Joins the mapping ledger (Account + TaxCode entities both target Account) to
	each leaf ERPNext Account and computes its signed GL balance through
	``as_of_date``. Group accounts are skipped -- only ledger accounts carry a
	comparable balance.
	"""
	balances: dict[str, dict] = {}
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
		account_row = frappe.db.get_value(
			"Account", account, ["is_group", "account_name", "root_type", "account_currency"], as_dict=True
		)
		if not account_row or account_row.is_group:
			continue
		balances[str(mapping.qbo_id)] = {
			"qb_id": str(mapping.qbo_id),
			"erp_account": account,
			"erp_name": account_row.account_name,
			"root_type": account_row.root_type,
			"erp_balance": _erpnext_account_balance(account, company, as_of_date),
			"currency": account_row.account_currency,
		}
	return balances


def _erpnext_account_balance(account: str, company: str, as_of_date) -> float:
	"""Signed GL balance (debit - credit) for an account through ``as_of_date``.

	Sums posted, non-cancelled GL Entries so the figure matches what the Trial
	Balance and the Chart of Accounts show (cancelled and draft entries excluded).
	"""
	row = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(debit), 0) - COALESCE(SUM(credit), 0) AS balance
		FROM `tabGL Entry`
		WHERE account = %s AND company = %s AND posting_date <= %s
		AND is_cancelled = 0 AND docstatus = 1
		""",
		(account, company, as_of_date),
		as_dict=True,
	)
	return flt(row[0].balance) if row else 0.0


def _compare(qb_balances: dict, erp_balances: dict, tolerance: float, as_of_date) -> dict:
	"""Bucket QBO vs ERPNext balances into matched/mismatched/qb_only/erp_only."""
	matched, mismatched, qb_only, erp_only = [], [], [], []

	for qb_id, qb in qb_balances.items():
		erp = erp_balances.get(qb_id)
		if not erp:
			# QBO has a balance for an account ERPNext has no (live) mapping for.
			if abs(qb["qb_balance"]) > tolerance:
				qb_only.append(qb)
			continue
		difference = abs(qb["qb_balance"] - erp["erp_balance"])
		row = {
			"qb_id": qb_id,
			"qb_name": qb["qb_name"],
			"erp_account": erp["erp_account"],
			"qb_balance": qb["qb_balance"],
			"erp_balance": erp["erp_balance"],
			"difference": difference,
		}
		(matched if difference <= tolerance else mismatched).append(row)

	for qb_id, erp in erp_balances.items():
		if qb_id not in qb_balances and abs(erp["erp_balance"]) > tolerance:
			erp_only.append(erp)

	total_qb = sum(qb["qb_balance"] for qb in qb_balances.values())
	total_erp = sum(erp["erp_balance"] for erp in erp_balances.values())
	return {
		"as_of_date": str(as_of_date),
		"tolerance": tolerance,
		"summary": {
			"qb_accounts": len(qb_balances),
			"erp_accounts": len(erp_balances),
			"matched": len(matched),
			"mismatched": len(mismatched),
			"qb_only": len(qb_only),
			"erp_only": len(erp_only),
			"total_qb_balance": total_qb,
			"total_erp_balance": total_erp,
			"total_difference": total_qb - total_erp,
		},
		"matched": matched,
		"mismatched": mismatched,
		"qb_only": qb_only,
		"erp_only": erp_only,
	}


# ---------------------------------------------------------------------------
# Transaction amount reconciliation (QBO raw payload vs ERPNext document).
# ---------------------------------------------------------------------------


def reconcile_transactions(entity_types=None, tolerance: float = 1.0):
	"""Compare imported transaction amounts against their QBO raw payloads.

	For each mapped transaction entity (every ``TRANSACTION_ENTITIES`` type by
	default), reads the latest stored ``QuickBooks Raw Payload`` and compares its
	QBO total against the linked ERPNext document's amount field
	(``TRANSACTION_AMOUNT_FIELD``). Surfaces three buckets: ``mismatched`` (totals
	differ by more than ``tolerance``), ``missing`` (mapping exists but the
	ERPNext document is gone), and a ``matched`` count. Read-only.
	"""
	entity_types = entity_types or TRANSACTION_ENTITIES
	tolerance = abs(flt(tolerance))
	mismatched, missing = [], []
	matched = 0

	for mapping in frappe.get_all(
		"QuickBooks Sync Mapping",
		filters={"qbo_entity_type": ["in", entity_types], "deleted": 0},
		fields=["qbo_entity_type", "qbo_id", "erpnext_doctype", "erpnext_name"],
	):
		if not mapping.erpnext_name or not mapping.erpnext_doctype:
			continue
		if not frappe.db.exists(mapping.erpnext_doctype, mapping.erpnext_name):
			missing.append(
				{
					"entity_type": mapping.qbo_entity_type,
					"qbo_id": mapping.qbo_id,
					"doctype": mapping.erpnext_doctype,
					"name": mapping.erpnext_name,
				}
			)
			continue
		amount_field = TRANSACTION_AMOUNT_FIELD.get(mapping.erpnext_doctype)
		if not amount_field:
			continue
		erp_amount = flt(frappe.db.get_value(mapping.erpnext_doctype, mapping.erpnext_name, amount_field))
		qb_amount = _payload_total(mapping.qbo_entity_type, mapping.qbo_id)
		if qb_amount is None:
			continue
		difference = abs(abs(erp_amount) - abs(qb_amount))
		if difference > tolerance:
			mismatched.append(
				{
					"entity_type": mapping.qbo_entity_type,
					"qbo_id": mapping.qbo_id,
					"doctype": mapping.erpnext_doctype,
					"name": mapping.erpnext_name,
					"qb_amount": qb_amount,
					"erp_amount": erp_amount,
					"difference": difference,
				}
			)
		else:
			matched += 1

	return {
		"tolerance": tolerance,
		"summary": {"matched": matched, "mismatched": len(mismatched), "missing": len(missing)},
		"mismatched": mismatched,
		"missing": missing,
	}


def _payload_total(entity_type: str, qbo_id: str) -> float | None:
	"""Total amount on the latest stored raw payload for a QBO transaction, or None.

	Reads ``TotalAmt`` (or ``Amount`` for Transfers/credit-card payments); for a
	Journal Entry with no header total it sums the debit lines so the comparison
	has something to match against.
	"""
	name = frappe.db.get_value(
		"QuickBooks Raw Payload",
		{"qbo_entity_type": entity_type, "qbo_id": str(qbo_id)},
		"name",
		order_by="creation desc",
	)
	if not name:
		return None
	payload = json_loads(frappe.db.get_value("QuickBooks Raw Payload", name, "payload"), default={}) or {}
	return _extract_total(entity_type, payload)


def _extract_total(entity_type: str, payload: dict) -> float:
	"""Pure helper: best total for a QBO payload (header amount, else summed debits)."""
	for key in ("TotalAmt", "Amount"):
		if payload.get(key) not in (None, ""):
			return flt(payload.get(key))
	if entity_type == "JournalEntry":
		total = 0.0
		for line in payload.get("Line") or []:
			detail = line.get("JournalEntryLineDetail") or {}
			if detail.get("PostingType") == "Debit":
				total += flt(line.get("Amount"))
		return total
	return 0.0
