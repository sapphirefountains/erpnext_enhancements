# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Signoff — a named person attesting that a learner can actually do it.

Some things a quiz cannot prove. A course with ``require_supervisor_signoff`` set
stalls its assignment at ``Awaiting Sign-off`` until one of these is submitted,
and the value of that gate is entirely in *who* signed. So the two rules this
controller enforces are both about identity:

* **nobody signs off their own competence.** A self-signoff is not a weaker
  attestation, it is no attestation at all;
* **``supervisor_user`` is derived from the Employee record**, never accepted from
  the caller, so the submit-time check has something trustworthy to compare the
  session user against.

Submittable, because a submitted sign-off is an attestation somebody's name is on
and the framework then refuses to let it be edited. Withdrawing one is a cancel,
which leaves it visible — and ``training/grading.py`` is what re-opens the
assignment when that happens.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

COMPETENT = "Competent"
NEEDS_PRACTICE = "Needs More Practice"

# Roles allowed to submit somebody else's sign-off without being the named
# supervisor. A Training Manager records sign-offs made on paper or over the
# radio, which is a real workflow — the audit value is in the named supervisor
# on the document, not in which login pressed submit.
DELEGATE_ROLES = {"System Manager", "Training Manager"}


class TrainingSignoff(Document):
	def validate(self):
		self._resolve_supervisor()
		self._resolve_learner()
		self._reject_self_signoff()
		self._require_notes_when_not_competent()
		self._stamp_signed_on()

	def before_submit(self):
		"""Checked here rather than in ``on_submit``: by the time ``on_submit`` runs
		the framework has already flipped docstatus and started firing links, and
		throwing there reports the problem from the wrong end of the operation.

		Scoped to submit on purpose. The learner runtime may well raise the *draft*
		— "I'm ready, please sign me off" is the learner's action — so refusing on
		validate would block a legitimate request. It is the submitted attestation
		that must not come from the learner."""
		self._reject_learner_submitting()

	# ------------------------------------------------------------------ helpers

	def _resolve_supervisor(self):
		"""Always re-derived. A caller-supplied ``supervisor_user`` would let the
		one check that makes this document mean anything be pointed at somebody
		else's login."""
		if not self.supervisor:
			frappe.throw(_("A sign-off has to name the supervisor making it."))
		self.supervisor_user = frappe.db.get_value("Employee", self.supervisor, "user_id")

	def _resolve_learner(self):
		if not self.user:
			frappe.throw(_("A sign-off needs a learner."))
		if not self.employee:
			self.employee = frappe.db.get_value("Employee", {"user_id": self.user}, "name")

	def _reject_self_signoff(self):
		"""The named supervisor may not be the learner — checked on both links,
		because a person can be reached either way and only one of the two is
		guaranteed to be filled in."""
		if self.supervisor_user and self.supervisor_user == self.user:
			frappe.throw(_("A learner cannot sign off their own competence."))
		if self.employee and self.supervisor == self.employee:
			frappe.throw(_("A learner cannot sign off their own competence."))

	def _reject_learner_submitting(self):
		"""Catches what the link comparison cannot: the learner submitting a
		document that names their manager. Managers keep the delegate path because
		sign-offs really are relayed over the radio and typed up afterwards."""
		if frappe.session.user != self.user:
			return
		if DELEGATE_ROLES & set(frappe.get_roles(frappe.session.user)):
			return
		frappe.throw(_("A sign-off has to be submitted by the supervisor, not by the learner."))

	def _require_notes_when_not_competent(self):
		if self.outcome == NEEDS_PRACTICE and not (self.competency_notes or "").strip():
			frappe.throw(
				_("Say what still needs work. \"Needs more practice\" with no note leaves the learner "
				  "nothing to act on and the next supervisor nothing to check.")
			)

	def _stamp_signed_on(self):
		if not self.signed_on:
			self.signed_on = now_datetime()
