# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Tracked Link: builds the tagged address and its QR code on validate."""

from frappe.model.document import Document


class TrackedLink(Document):
	def validate(self):
		from erpnext_enhancements.marketing.performance.links import refresh_link

		refresh_link(self)
