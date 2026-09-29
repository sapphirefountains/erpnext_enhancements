# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Rental Accessory Pool — a counted stock of one kind of rental accessory.

Accessories (uplights, pumps, skirting) are not tracked one by one: a pool says how many the
company owns and how many are broken, and a Rental Booking takes a quantity from it for a
window. ``rental_availability.pool_peak`` works out how many are out at the busiest moment of
any window, so two bookings that never overlap each other can both use the whole pool.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class RentalAccessoryPool(Document):
	def validate(self):
		if cint(self.out_of_service_qty) > cint(self.total_qty):
			frappe.throw(_("Out of Service cannot be more than Total Owned."))
