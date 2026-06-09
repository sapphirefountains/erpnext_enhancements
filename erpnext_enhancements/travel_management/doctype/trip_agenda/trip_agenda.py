# -*- coding: utf-8 -*-
# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

"""Controller for the **Trip Agenda** child table (the trip itinerary).

Child table (``istable``) embedded in Travel Trip via the ``itinerary``
field. Each row is a dated/timed itinerary entry: activity description, an
optional dynamic-link to a related party (``related_party_doctype`` /
``related_party_name``), and a ``location`` Link to a Travel POI.

No custom server logic is required, so the controller is a plain pass-through
``Document`` subclass.
"""

import frappe
from frappe.model.document import Document

class TripAgenda(Document):
	"""Plain child-table controller for Trip Agenda; no custom behaviour."""
	pass
