# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Estimate Rate Card: the company's estimating rates (D15 level one: rate card -> estimate -> phase -> line).

An Estimate copies these when it is made and never reads them again, so editing a card changes
future estimates only. Finance owns the numbers in the Desk (Nik, 2026-09-21); the seed patch makes
one empty "Standard" card and never writes a rate, because this repository is public.
"""

import frappe
from frappe import _
from frappe.model.document import Document

PERCENT_FIELDS = (
	"tax_pct",
	"markup_material",
	"markup_equipment",
	"markup_labor",
	"markup_subcontract",
	"markup_expense",
	"contingency_pct",
	"insurance_pct",
	"fee_pct",
	"commission_pct",
)


class EstimateRateCard(Document):
	def validate(self):
		for field in PERCENT_FIELDS:
			value = self.get(field) or 0
			if value < 0 or value > 1000:
				frappe.throw(_("{0}: {1}% is not a usable rate.").format(self.meta.get_label(field), value))
		if (self.labor_rate or 0) < 0:
			frappe.throw(_("The labor cost per hour cannot be negative."))
		if self.is_default and self.disabled:
			frappe.throw(_("A disabled rate card cannot be the default."))

	def on_update(self):
		# One default: the newest one ticked wins, and the others are unticked quietly.
		if self.is_default:
			for other in frappe.get_all(
				"Estimate Rate Card", filters={"is_default": 1, "name": ["!=", self.name]}, pluck="name"
			):
				frappe.db.set_value("Estimate Rate Card", other, "is_default", 0)
