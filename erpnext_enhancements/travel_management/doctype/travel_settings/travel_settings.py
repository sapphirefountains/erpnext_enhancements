# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from erpnext_enhancements.travel_management import expense_claims_available

# The Expense Claim Type link fields, mapping a travel cost category to an HRMS
# Expense Claim Type. They are only meaningful when HRMS is installed.
EXPENSE_TYPE_FIELDS = (
	"flight_expense_type",
	"hotel_expense_type",
	"ground_expense_type",
	"misc_expense_type",
	"per_diem_expense_type",
	"mileage_expense_type",
)


class TravelSettings(Document):
	"""Single doctype: rate rules (per diem, mileage), Expense Claim Type
	mapping, and the travel automation/notification switches.

	HRMS is optional (see ``travel_management.expense_claims_available``). When
	it is absent the Expense Claim Type fields are meaningless — and worse, a
	*stored* value pointing at the now-missing ``Expense Claim Type`` doctype
	makes Frappe's ``getdoc`` raise ``DoesNotExistError`` (404) while resolving
	the link title, bricking this form. So we both (a) clear those fields on
	save when the doctype is gone, and (b) flag availability to the client
	(``travel_settings.js`` hides the whole section), keeping the form openable
	and self-explanatory either way."""

	def onload(self):
		self.set_onload("expense_claims_available", expense_claims_available())

	def validate(self):
		# Never let a value referencing a non-existent Expense Claim Type
		# persist — it is what 404s this very form on the next load.
		if not expense_claims_available():
			for fieldname in EXPENSE_TYPE_FIELDS:
				self.set(fieldname, None)
