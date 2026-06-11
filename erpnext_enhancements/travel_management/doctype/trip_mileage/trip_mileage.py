# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TripMileage(Document):
	"""Personal-vehicle mileage row (reimbursed at the Travel Settings rate).
	Company fleet usage goes through Trip Ground Transport + Vehicle Log
	instead. All validation lives in the parent Travel Trip controller."""

	pass
