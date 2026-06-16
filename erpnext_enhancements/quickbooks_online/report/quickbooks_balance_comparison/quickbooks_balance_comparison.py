# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""QuickBooks Balance Comparison — account-by-account reconciliation.

Thin report wrapper around
``erpnext_enhancements.quickbooks_online.core.reconcile.compare_account_balances``:
it pulls the QBO Trial Balance and the matching ERPNext GL balances as of a date
and flattens the matched / mismatched / QuickBooks-only / ERPNext-only buckets
into one tabular view, mismatches first.
"""

import frappe
from frappe import _

from erpnext_enhancements.quickbooks_online.core.reconcile import compare_account_balances


def execute(filters=None):
	filters = frappe._dict(filters or {})
	result = compare_account_balances(
		as_of_date=filters.get("as_of_date"), tolerance=filters.get("tolerance") or 0.01
	)
	return get_columns(), get_data(result, filters)


def get_columns():
	return [
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 130},
		{
			"label": _("ERPNext Account"),
			"fieldname": "account",
			"fieldtype": "Link",
			"options": "Account",
			"width": 260,
		},
		{"label": _("QuickBooks Account"), "fieldname": "qb_name", "fieldtype": "Data", "width": 200},
		{"label": _("QBO ID"), "fieldname": "qb_id", "fieldtype": "Data", "width": 80},
		{"label": _("QuickBooks Balance"), "fieldname": "qb_balance", "fieldtype": "Currency", "width": 160},
		{"label": _("ERPNext Balance"), "fieldname": "erp_balance", "fieldtype": "Currency", "width": 160},
		{"label": _("Difference"), "fieldname": "difference", "fieldtype": "Currency", "width": 140},
	]


def get_data(result, filters):
	rows = []
	for row in result["mismatched"]:
		rows.append({"status": _("Mismatch"), **_balance_row(row)})
	for row in result["qb_only"]:
		rows.append(
			{
				"status": _("QuickBooks only"),
				"account": None,
				"qb_name": row.get("qb_name"),
				"qb_id": row.get("qb_id"),
				"qb_balance": row.get("qb_balance"),
				"erp_balance": 0,
				"difference": abs(row.get("qb_balance", 0)),
			}
		)
	for row in result["erp_only"]:
		rows.append(
			{
				"status": _("ERPNext only"),
				"account": row.get("erp_account"),
				"qb_name": row.get("erp_name"),
				"qb_id": row.get("qb_id"),
				"qb_balance": 0,
				"erp_balance": row.get("erp_balance"),
				"difference": abs(row.get("erp_balance", 0)),
			}
		)
	if not filters.get("only_discrepancies"):
		for row in result["matched"]:
			rows.append({"status": _("Match"), **_balance_row(row)})
	return rows


def _balance_row(row):
	"""Shape a matched/mismatched comparison row for the report table."""
	return {
		"account": row.get("erp_account"),
		"qb_name": row.get("qb_name"),
		"qb_id": row.get("qb_id"),
		"qb_balance": row.get("qb_balance"),
		"erp_balance": row.get("erp_balance"),
		"difference": row.get("difference"),
	}
