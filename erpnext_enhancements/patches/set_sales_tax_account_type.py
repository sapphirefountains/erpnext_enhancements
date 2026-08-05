"""Mark the sales-tax destination account as a Tax account (v1.246.0).

WI-067 starts posting imported QuickBooks sales tax to `25010 Sales Tax Agency
Payable`, which was created by the QBO Account import and so carries a BLANK
`account_type` -- the importer maps QBO's `AccountType` ("Other Current Liability")
to an ERPNext root_type and leaves the finer type unset.

Blank is not merely untidy. ERPNext keys real behaviour off `account_type = "Tax"`:
the Sales/Purchase Taxes and Charges account_head link filters on it, so the account
does not appear in the picker an accountant uses to correct a parked invoice by hand,
and tax-report grouping skips it. The importer itself already sets `account_type =
"Tax"` on every account it creates from a QBO **TaxCode** (`_map_tax_code`), so this
just brings the one account we actually post to in line with the accounts we do not.

Scoped by the settings-configured account (falling back to
`DEFAULT_SALES_TAX_ACCOUNT_NUMBER` on the configured Company), and only ever fills a
BLANK type -- an account somebody deliberately typed as something else is left alone
and reported, because silently retyping a GL account is not a migration, it is a
surprise. Idempotent: a second run finds the type already set and returns.
"""

import frappe

from erpnext_enhancements.quickbooks_online.core.constants import DEFAULT_SALES_TAX_ACCOUNT_NUMBER


def execute():
	company = frappe.db.get_single_value("QuickBooks Online Settings", "company")
	if not company:
		return
	account = frappe.db.get_single_value("QuickBooks Online Settings", "sales_tax_account")
	if not account:
		account = frappe.db.get_value(
			"Account",
			{"account_number": DEFAULT_SALES_TAX_ACCOUNT_NUMBER, "company": company},
			"name",
		)
	if not account:
		return

	current = frappe.db.get_value("Account", account, "account_type")
	if current == "Tax":
		return
	if current:
		# Somebody chose this. Say so and change nothing.
		frappe.log_error(
			message=(
				f"{account} is the configured QuickBooks sales-tax destination but its "
				f"account_type is {current!r}, not 'Tax'. Left unchanged; set it by hand if "
				"that is wrong."
			),
			title="QBO sales tax account type",
		)
		return

	frappe.db.set_value("Account", account, "account_type", "Tax", update_modified=False)
