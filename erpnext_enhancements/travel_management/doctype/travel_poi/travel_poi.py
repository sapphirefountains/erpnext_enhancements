# -*- coding: utf-8 -*-
# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

"""Controller for the **Travel POI** doctype.

A Travel POI (Point of Interest) is a standalone, reusable location record
(name, category, geolocation, optional linked Address and notes). Trip Agenda
rows reference it through their ``location`` Link field so itinerary stops can
point at known places (client HQ, hotels, supply depots, etc.).

No custom server logic is required, so the controller is a plain pass-through
``Document`` subclass.
"""

import frappe
from frappe.model.document import Document

class TravelPOI(Document):
	"""Plain Document controller for Travel POI; no custom behaviour."""
	pass
