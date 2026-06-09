# -*- coding: utf-8 -*-
# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

"""Controller for the **Trip Ground Transport** child table.

Child table (``istable``) embedded in Travel Trip via the ``ground_transport``
field. Each row records ground travel: transport type (Rental/Third Party or
Company Fleet), an optional dynamic-link reference to the underlying record
(``transport_ref_doctype`` / ``transport_ref_name``), and pickup/drop-off
locations. These rows are informational only and do not feed the Expense
Claim roll-up.

No custom server logic is required, so the controller is a plain pass-through
``Document`` subclass.
"""

import frappe
from frappe.model.document import Document

class TripGroundTransport(Document):
	"""Plain child-table controller for Trip Ground Transport; no custom behaviour."""
	pass
