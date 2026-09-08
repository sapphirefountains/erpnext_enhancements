# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Evaluation — a booked slot for a hands-on competency check.

Phase C of the LMS expansion (WI-071). Some things are not proved by a quiz —
"can drain and refill a basin unsupervised" — so a course can require a supervisor
sign-off (``training/signoff.py``). Today that sign-off happens *whenever* the
supervisor gets to it; this books it: an evaluator, a time, a learner, an
auto-invite. When the evaluator records the outcome (``training/evaluations.py::
record_evaluation``) it creates and submits a real ``Training Signoff`` through the
existing engine, so the completion gate is satisfied by the same attestation the
rest of the module already trusts. The evaluation is the scheduling wrapper; the
sign-off is the evidence.

The calendar-invite side (an ICS / Google Calendar event) is the *native* half and
is deliberately deferred, as in live classes — the Google Calendar accounts are
disabled on prod.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class TrainingEvaluation(Document):
	def validate(self):
		self._resolve_evaluator_user()

	def after_insert(self):
		self._invite()

	def _resolve_evaluator_user(self):
		"""Derive the evaluator's login from their Employee, and refuse a self-check.

		``evaluator_user`` is what ``record_evaluation`` compares the caller against,
		so it is derived here rather than typed. A learner cannot evaluate their own
		competency — the whole point of a sign-off is that somebody *else* watched."""
		self.evaluator_user = frappe.db.get_value("Employee", self.evaluator, "user_id") or None
		if self.evaluator_user and self.evaluator_user == self.learner:
			frappe.throw(_("The evaluator and the learner cannot be the same person."))

	def _invite(self):
		"""Auto-invite the learner and the evaluator that the evaluation is booked.

		Best-effort, and only for a Scheduled evaluation. Kept out of the controller's
		body in ``evaluations.notify_scheduled`` so the notification text lives beside
		the outcome-recording that answers it. Never fatal: a booking that saved but
		could not email must not roll back."""
		if self.status != "Scheduled":
			return
		if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch or frappe.flags.in_import:
			return
		from erpnext_enhancements.training import evaluations

		evaluations.notify_scheduled(self)
