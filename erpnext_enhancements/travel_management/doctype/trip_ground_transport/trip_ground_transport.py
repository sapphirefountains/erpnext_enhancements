# -*- coding: utf-8 -*-
# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

"""Controller for the **Trip Ground Transport** child table.

Child table (``istable``) embedded in Travel Trip via the ``ground_transport``
field. Each row records ground travel via typed links: a ``supplier`` for
Rental/Third Party and Taxi/Rideshare rows, or a ``vehicle`` (+ optional
``vehicle_log`` created by ``travel_management.api.create_vehicle_log``) for
Company Fleet rows, plus pickup/drop-off locations and times and the shared
cost block. The parent controller forces ``paid_by = Company`` on Company
Fleet rows — fleet usage is never reimbursed.

All validation lives in the parent Travel Trip controller, so this is a plain
pass-through ``Document`` subclass.
"""

import frappe
from frappe.model.document import Document

class TripGroundTransport(Document):
	"""Plain child-table controller for Trip Ground Transport; no custom behaviour."""
	pass
