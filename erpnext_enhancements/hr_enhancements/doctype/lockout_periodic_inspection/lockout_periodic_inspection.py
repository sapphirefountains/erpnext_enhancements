# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The annual check that the procedure still matches the machine.

Required every twelve months, and the half everybody gets wrong is **who does
it**: the inspector must not be one of the people who uses the procedure. An
authorised employee inspecting their own habits finds nothing, which is precisely
why the rule names somebody else.

So that is enforced here rather than trusted, and it is the only validation on
this doctype worth having.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class LockoutPeriodicInspection(Document):
	def validate(self):
		self._reject_self_inspection()

	def on_update(self):
		"""Move the date on the procedure without anybody remembering to."""
		if not self.procedure:
			return
		try:
			doc = frappe.get_doc("Lockout Tagout Procedure", self.procedure)
			doc.refresh_inspection_dates()
			doc.db_set("last_inspected_on", doc.last_inspected_on, update_modified=False)
			doc.db_set("inspection_due_on", doc.inspection_due_on, update_modified=False)
		except Exception:
			# The inspection is the record that matters; a stale derived date on the
			# parent is a nightly-sweep problem, not a reason to refuse the filing.
			frappe.log_error(
				f"Could not refresh inspection dates on {self.procedure}\n{frappe.get_traceback()}",
				"Lockout inspection",
			)

	def _reject_self_inspection(self):
		"""The inspector is not the person whose use was observed.

		An authorised employee inspecting their own habits finds nothing, which is why
		the rule names somebody else — and it is the half of this requirement that is
		almost always missed, usually because one person is the only one who knows the
		equipment.
		"""
		if self.inspector and self.authorised_employee and self.inspector == self.authorised_employee:
			frappe.throw(
				_(
					"The inspector cannot be the person whose use is being observed. Somebody "
					"checking their own habits finds nothing, which is the reason the annual "
					"inspection names a second person."
				)
			)
