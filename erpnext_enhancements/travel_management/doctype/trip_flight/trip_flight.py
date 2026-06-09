# -*- coding: utf-8 -*-
# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

"""Controller for the **Trip Flight** child table.

Child table (``istable``) embedded in Travel Trip via the ``flights`` field.
Each row captures one flight segment: airline (Supplier link), flight number,
departure/arrival airports and times, booking reference/PNR, cost and an
optional attachment. Rows with ``cost > 0`` are picked up by
``TravelTrip.create_expense_claim_on_workflow_transition`` and turned into
"Air Travel" Expense Claim lines.

No custom server logic is required, so the controller is a plain pass-through
``Document`` subclass.
"""

import frappe
from frappe.model.document import Document

class TripFlight(Document):
	"""Plain child-table controller for Trip Flight; no custom behaviour."""
	pass
