# -*- coding: utf-8 -*-
# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

"""Controller for the **Trip Flight** child table.

Child table (``istable``) embedded in Travel Trip via the ``flights`` field.
Each row captures one flight segment: airline (Supplier link), flight number,
departure/arrival airports and times, booking reference/PNR, an optional
single ``traveler`` (blank = whole crew), an attachment, and the shared cost
block (estimated_cost / cost / paid_by / paid_by_traveler / billable /
expense_claim stamp). Employee-paid rows are pulled onto that traveler's
Expense Claim by ``travel_management.api.create_expense_claim``.

All validation lives in the parent Travel Trip controller, so this is a plain
pass-through ``Document`` subclass.
"""

import frappe
from frappe.model.document import Document

class TripFlight(Document):
	"""Plain child-table controller for Trip Flight; no custom behaviour."""
	pass
