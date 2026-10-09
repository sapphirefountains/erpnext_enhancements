# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Project Planner Settings: how many hours the planner books when nobody wrote an estimate.

A Single. Read it with `frappe.get_cached_doc("Project Planner Settings")` and never with
`frappe.db.get_single_value`: a Single that has never been saved has no `tabSingles` rows, so
`get_single_value` returns None for every field, while `get_cached_doc` goes through
`load_from_db`, which hands back the JSON defaults. Readers still treat None as "use the default"
(`x if x is not None else default`), but a deliberate 0 stays 0.

The validation only refuses what cannot mean anything -- negative hours, or more than a day -- and
the one value the engine falls back on, a full day of zero hours. It does not reject a zero visit
length or rental length: those mean "do not book hours for this", which is a choice.

There is no backfill patch for the defaults, unlike a Single that gained fields later (see
CLAUDE.md): this doctype is new, so every field is declared before any row can exist.
"""

import frappe
from frappe import _
from frappe.model.document import Document

HOUR_FIELDS = (
	"default_day_hours",
	"maintenance_visit_hours",
	"rental_delivery_hours",
	"rental_setup_hours",
	"rental_takedown_hours",
	"rental_cleaning_hours",
)


class ProjectPlannerSettings(Document):
	def validate(self):
		for field in HOUR_FIELDS:
			value = self.get(field)
			if value is not None and not 0 <= float(value) <= 24:
				frappe.throw(_("{0} must be between 0 and 24 hours.").format(self.meta.get_label(field)))
		if self.default_day_hours is not None and float(self.default_day_hours) <= 0:
			frappe.throw(_("Full-day hours must be more than 0."))
