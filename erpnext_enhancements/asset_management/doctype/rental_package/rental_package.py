# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Rental Package — a preset of fountain models, accessories and fees.

Like a maintenance Service Plan, a package is copied onto a Rental Booking when it is
applied (``rental_availability.plan_package``); editing a package later changes no
existing booking. A package names fountain *models* (Items), not particular fountains —
the free ones of that model are picked for the booking's dates.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class RentalPackage(Document):
	def validate(self):
		problems = []
		for label, rows, key in ((_("Fountain"), self.fountains, "item"), (_("Accessory"), self.accessories, "pool")):
			seen = set()
			for row in rows or []:
				if row.get(key) in seen:
					problems.append(_("{0} row {1}: {2} is listed twice.").format(label, row.idx, row.get(key)))
				seen.add(row.get(key))
				if cint(row.qty) <= 0:
					problems.append(_("{0} row {1}: quantity has to be at least 1.").format(label, row.idx))
		if problems:
			frappe.throw("<br>".join(problems))
