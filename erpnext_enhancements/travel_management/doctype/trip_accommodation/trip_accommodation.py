# -*- coding: utf-8 -*-
# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

"""Controller for the **Trip Accommodation** child table.

Child table (``istable``) embedded in Travel Trip via the ``accommodation``
field. Each row captures one lodging stay: hotel/lodging (Supplier link),
address, check-in/check-out dates, booking confirmation number and cost. Rows
with ``cost > 0`` are picked up by
``TravelTrip.create_expense_claim_on_workflow_transition`` and turned into
"Hotel Accommodation" Expense Claim lines.

No custom server logic is required, so the controller is a plain pass-through
``Document`` subclass.
"""

import frappe
from frappe.model.document import Document

class TripAccommodation(Document):
	"""Plain child-table controller for Trip Accommodation; no custom behaviour."""
	pass
