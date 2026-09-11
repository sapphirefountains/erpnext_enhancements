# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The written PPE certification, per kind of work.

OSHA asks for a hazard assessment certified in writing, and almost nobody has one
-- it is paperwork with no visible consequence until an inspector asks. So this is
deliberately shaped to be worth keeping for its own sake: the PPE list it holds is
shown on the safety screen a technician taps through before **every** visit, which
means it is read at the moment it is useful rather than filed.

One assessment per **kind of work**, not per site. The hazards belong to the task
-- draining a basin is the same job at every fountain -- and a per-site copy is
sixteen documents that drift.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class PPEHazardAssessment(Document):
	def validate(self):
		self._require_ppe()

	def _require_ppe(self):
		"""An assessment listing hazards and no protection is half a document.

		It is also the half that reads as complete: somebody has written down what can
		hurt you and stopped, and the form gives no sign that anything is missing.
		"""
		if not (self.requirements or []):
			frappe.throw(
				_("List the PPE. An assessment naming hazards and no protection is half a document.")
			)
