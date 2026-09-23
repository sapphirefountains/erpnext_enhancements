# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TripFreight(Document):
	"""One carrier shipment on a trip: equipment or materials sent ahead of (or after) the
	crew, with its tracking / PRO / BOL number, pickup and delivery windows, who receives
	it, and the shared cost block. Our own truck's load is not freight — it is the Hauling
	note on that Ground Transport row. All validation lives in the parent Travel Trip
	controller."""

	pass
