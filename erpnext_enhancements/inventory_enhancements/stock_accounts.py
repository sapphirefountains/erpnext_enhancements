# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The account the other side of a Stock Entry row posts to. One rule, every builder.

Every Stock Entry this app builds — a take, an add-without-PO or a move on the Stock Scan
page (``api/stock_scan.py``), and the consumables a Sapphire Maintenance Record issues
(``api/maintenance_workflow.py``) — needs a **difference account** on each row, and asks
this module for it. ``tests/test_stock_entry_builders.py`` fails the build on a builder
that does not.

**Why ERPNext's own default is not enough here.** On a Material Issue / Receipt /
Transfer, ``StockEntry.get_item_details`` fills ``expense_account`` from the Item's
default for the company, then its Item Group's, then ``Company.stock_adjustment_account``.
Production's Company has **no** Stock Adjustment Account (checked 2026-09-23) and no Item
or Item Group names one, so with perpetual inventory on,
``StockEntry.validate_difference_account`` refuses every such entry: "Please enter
Difference Account or set default Stock Adjustment Account for company". The site has
exactly one leaf ``Stock Adjustment`` account, ``5119 - Stock Adjustment - SF``, which is
where ERPNext would have posted had the Company named it.

So :func:`difference_account` walks ERPNext's chain in ERPNext's order, puts the account a
Stock Manager chose in Inventory Scanner Settings where ERPNext would read the Company's,
and ends at the company's one Stock Adjustment account. It refuses rather than guesses
when there are several. Setting ``Company.stock_adjustment_account`` on the site would
make that last step unnecessary — and is a settings change for accounting to make, not
something a deploy should do.

Not in ``stock_scan_rules`` because it reads the database; that module is bench-free by
design.
"""

import frappe
from frappe import _


def usable_account(account, company):
	"""``account`` when a Stock Entry row may post to it for ``company``, else ``None``.

	ERPNext refuses a group, a disabled account, another company's, and a ``Stock``-type
	account as a difference account ("the Difference Account must not be a Stock type
	account"). A candidate that fails is skipped, so a stale setting falls through to the
	next rule instead of failing the save.
	"""
	if not account:
		return None
	row = frappe.db.get_value(
		"Account", account, ["company", "is_group", "disabled", "account_type"], as_dict=True
	)
	if not row or row.company != company or row.is_group or row.disabled or row.account_type == "Stock":
		return None
	return account


def difference_account(company, item_code, configured=None):
	"""The account a Stock Entry row's other side posts to.

	ERPNext's own order first — the Item's default for this company, then its Item
	Group's — then ``configured`` (the account chosen in Inventory Scanner Settings) where
	ERPNext would read the Company's Stock Adjustment Account, then the Company's, then the
	company's one leaf Stock Adjustment account. Throws when none of those gives a usable
	account and the company has several Stock Adjustment accounts to choose between.
	"""
	from erpnext.setup.doctype.item_group.item_group import get_item_group_defaults
	from erpnext.stock.doctype.item.item import get_item_defaults

	for candidate in (
		(get_item_defaults(item_code, company) or {}).get("expense_account"),
		(get_item_group_defaults(item_code, company) or {}).get("expense_account"),
		configured,
		frappe.get_cached_value("Company", company, "stock_adjustment_account"),
	):
		account = usable_account(candidate, company)
		if account:
			return account
	leaves = frappe.get_all(
		"Account",
		filters={"company": company, "account_type": "Stock Adjustment", "is_group": 0, "disabled": 0},
		pluck="name",
		limit=2,
	)
	if len(leaves) == 1:
		return leaves[0]
	frappe.throw(
		_(
			"There is no account to post this stock movement to. A Stock Manager needs to choose one in Inventory Scanner Settings (Parts Taken: Expense Account), or set a Stock Adjustment Account on Company {0}."
		).format(company)
	)
