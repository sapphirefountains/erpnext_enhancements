# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One thing a rung of the ladder asks for.

A child of `Position`, so a rung's requirements live on the rung. The alternative
— a standalone doctype with a Link back — buys nothing here and costs a DocPerm
surface on a table that is pure configuration.

**Three kinds, and the third is the one that matters.** A course is content and a
credential is a ticket somebody else issues, and both are evidence you can hold
without anybody watching you work. A **sign-off** is the competency itself: a
supervisor saying they watched this person drain a basin and would send them
alone. WI-072 built the sign-off machinery and nothing said which competencies a
rung actually demands, so the gate existed with no stated target.

Deliberately thin. No due date, no owner, no percent-complete, no status. A
requirement is a fact about the *rung*, not about a person — who has it and who
does not is computed in `hr_enhancements/progression.py` against live records, so
it can never be stale.
"""

import frappe
from frappe import _
from frappe.model.document import Document

COURSE = "Training Course"
CREDENTIAL = "Credential"
SIGNOFF = "Sign-off"


class PositionRequirement(Document):
	def validate(self):
		self._require_a_target()

	def _require_a_target(self):
		"""A requirement that names nothing is worse than no requirement.

		It renders as a row in the checklist, counts toward the total, and can
		never be satisfied — so somebody's readiness sits permanently short by one
		and nobody can see why. Same failure direction as everything else in this
		release: it looks configured.
		"""
		if self.requirement_type == CREDENTIAL:
			if not self.credential_type:
				frappe.throw(_("Row {0}: pick the credential this rung asks for.").format(self.idx))
			# Cleared rather than left behind: a Credential row carrying a stale
			# course link would be read by whichever branch looks at it first.
			self.training_course = None
			return

		if not self.training_course:
			label = _("course") if self.requirement_type == COURSE else _("course to be signed off on")
			frappe.throw(_("Row {0}: pick the {1}.").format(self.idx, label))
		self.credential_type = None
