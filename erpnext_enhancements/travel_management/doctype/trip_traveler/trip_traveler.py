# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class TripTraveler(Document):
	"""Crew member on a Travel Trip. All validation lives in the parent
	Travel Trip controller (children validate through the parent save)."""

	pass
