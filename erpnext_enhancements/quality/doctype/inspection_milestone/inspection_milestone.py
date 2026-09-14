# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A point in a project's life where an inspection is due — WI-075 sub-phase C.

The catalog, not the engine. Sub-phase D is what reads these and generates an inspection; until
then a milestone is a declaration of intent that a person acts on.

Keyed to ``Project.project_type`` **as it exists on production**, which is not the vocabulary
the build spec uses. "Service" (354 projects) is what that document calls Maintenance, and is
deliberately not renamed — the rename buys vocabulary rather than capability, and would be a
data migration across the largest category on the site. "Controls Fab" has no project type at
all, so it rides on the new "Products" one.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class InspectionMilestone(Document):
	def validate(self):
		self._require_trigger_value()

	def _require_trigger_value(self):
		"""A trigger with nothing to fire on is the failure that looks like configuration.

		It would sit in the catalog looking complete, and sub-phase D would simply never match
		it — no error, no log, just a milestone that never produces an inspection.
		"""
		if self.trigger_basis == "Build Status" and not (self.trigger_value or "").strip():
			frappe.throw(
				_("A Build Status trigger needs the status value that opens it, or this milestone will never fire."),
				title=_("Trigger has nothing to match"),
			)
		if self.trigger_basis == "Calendar Interval" and not self.interval_days:
			frappe.throw(
				_("A Calendar Interval trigger needs an interval in days, or this milestone will never come round."),
				title=_("Interval missing"),
			)
