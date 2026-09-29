# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Rental Settings — hold lengths, reminders and how rental invoices are drafted.

A new Single: on the deploy that adds it there are no ``tabSingles`` rows at all, and v16's
``load_from_db`` then builds it from ``new_doc`` — so every default here applies on existing
sites too. (The trap in CLAUDE.md is a *new field on an existing* Single; this is neither.) Read
it with ``frappe.get_cached_doc("Rental Settings")``, never ``get_single_value``, which returns
None for a field that was never saved.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class RentalSettings(Document):
	def validate(self):
		if not 0 <= flt(self.deposit_percent) <= 100:
			frappe.throw(_("Deposit has to be between 0 and 100 percent."))
		if self.security_deposit_account:
			root_type = frappe.db.get_value("Account", self.security_deposit_account, "root_type")
			if root_type != "Liability":
				frappe.throw(
					_("The Security Deposit Account has to be a Liability account: the deposit is owed back to the customer.")
				)
