# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TripExpense(Document):
	"""Miscellaneous trip cost row (parking, tolls, conference fees, ...).
	All validation lives in the parent Travel Trip controller."""

	pass
