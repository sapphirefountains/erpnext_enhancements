# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A register of every below-grade space the crew works in.

Fountain vaults, wet wells and pump pits are the textbook permit-required case and
almost nobody writes them down: an atmospheric hazard from decomposing organic
matter and chlorine dosing, an engulfment risk from water that can rise, and
restricted egress through a hatch. The classification is the thing that decides
whether anybody fills in a permit, so it lives on a record rather than in
somebody's judgement at the hatch.

**The default is permit-required, and the default is the whole safety margin.** A
space wrongly classified as needing a permit costs twenty minutes; one wrongly
classified as not needing one is how people die in pits. Downgrading is a decision
somebody makes, signs and dates — which is why `last_reviewed_on` and
`reviewed_by` are on the record and the classification is not read-only.

`reclassification` is deliberately not a workflow. A space changes — new dosing
equipment, a failed vent, standing water that was not there last year — and a
review is a conversation followed by an edit, not a four-state approval.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

PERMIT_REQUIRED = "Permit-required"
NON_PERMIT = "Non-permit confined space"
NOT_CONFINED = "Not a confined space"


class ConfinedSpace(Document):
	def validate(self):
		self._require_a_reason_to_downgrade()
		self._require_a_rescue_plan()

	def _require_a_reason_to_downgrade(self):
		"""Saying a space is not permit-required means saying who decided.

		Not an approval step -- just a name and a date, because "who said this pit was
		safe" is the first question after an incident and the honest answer should not
		be "the record does not say".
		"""
		if self.classification == PERMIT_REQUIRED:
			return
		if not (self.reviewed_by and self.last_reviewed_on):
			frappe.throw(
				_(
					"Saying this is not permit-required means recording who decided and when. "
					"Fill in <b>Classification reviewed</b> and <b>Reviewed by</b>."
				)
			)
		if any(
			cint(self.get(field))
			for field in ("hazard_atmosphere", "hazard_engulfment", "hazard_configuration", "hazard_other")
		):
			frappe.throw(
				_(
					"This space still has a hazard ticked, so it is permit-required by "
					"definition. Untick the hazard, or leave the classification alone."
				)
			)

	def _require_a_rescue_plan(self):
		""""Call 911" is not a rescue plan.

		Most confined-space deaths are would-be rescuers, and the minutes that matter
		are the ones before anybody arrives. Required only on a permit-required space,
		because that is where it is load-bearing.
		"""
		if self.classification == PERMIT_REQUIRED and not (self.rescue_plan or "").strip():
			frappe.throw(
				_(
					"Write the rescue plan. Say who retrieves, with what, from outside — most "
					"confined-space deaths are the people who went in after somebody."
				)
			)
