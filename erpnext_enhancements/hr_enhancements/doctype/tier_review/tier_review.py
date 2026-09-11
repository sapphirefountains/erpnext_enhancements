# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A promotion, with a stated basis.

WI-072 made a `Position` tier **be** the sign-off authority. That turns a
promotion into a permission grant — moving somebody from Junior to Senior
Technician hands them the standing to attest that other people can work alone —
and today it is a free-text edit to one field on an Employee record, with no
stated reason and nothing to look back at.

So this is not a performance review. It is the **basis for a permission change**,
and everything it does not have follows from that:

* **no score, no rating out of five, no weighting**, because the output is a
  binary — they stand on the rung or they do not;
* **no pay field, ever.** Compensation is QuickBooks' world. The moment this
  record grows one it stops being evidence for an authority grant and becomes a
  salary negotiation, and the two must not share a document;
* **no cycle, no template, no KRA tree, no 360.** That machinery is what the HR
  category sells and it earns its keep somewhere above a hundred people. Here it
  would be four forms nobody fills in.

Three things are load-bearing.

**The lines are a snapshot.** They are derived once, from the target rung's
requirements, and frozen. If somebody changes what a Senior Technician must hold
next March, this review still says what *it* was measured against. Evidence that
silently re-bases is not evidence — the same doctrine as the sign-off, which
snapshots the supervisor's position rather than reading it live.

**It refuses to open against a rung nobody has configured.** A review with no
lines renders as an empty checklist, everybody signs it, and the promotion has a
paper trail that proves nothing. That is this release's recurring bug — the
advisory that could never fire, the queries that passed vacuously, the backfill
that recorded success having written nothing — and here it would launder a
permission grant.

**The decision and the act are separate.** `Promote` records what was decided;
somebody then presses Apply, and *that* writes `Employee.custom_position`. WI-072
decision 6: the system proposes, a human promotes. Core `Version` captures the
field change, so the audit trail is the framework's rather than ours.
"""

import frappe
from frappe import _
from frappe.model.document import Document

DRAFT = "Draft"
WITH_REVIEWER = "With the reviewer"
DECIDED = "Decided"
CANCELED = "Canceled"

PROMOTE = "Promote"
NOT_YET = "Not yet"


class TierReview(Document):
	def validate(self):
		self._resolve_user()
		self._guard_status()
		self._guard_decision()
		self._reject_self_review()
		self._resolve_reviewer_user()

	# ------------------------------------------------------------------ helpers

	def _resolve_user(self):
		"""Derived from the Employee, never accepted.

		Every readiness read is keyed on it, so a caller that could set it could
		review somebody else's record against their own qualifications.
		"""
		if self.employee:
			self.user = frappe.db.get_value("Employee", self.employee, "user_id")

	def _guard_status(self):
		"""`status` moves only through `hr_enhancements/tier_review.py`.

		`read_only` hides the field in the Desk form and nothing more — Frappe does
		not enforce read-only against the API, and the `Employee` role holds write
		here so somebody can fill in their own self-assessment. Without this, any of
		the sixteen could POST their own review straight to `Decided`.

		Exactly the hole the WI-072 branch review found in `Time Off Request`,
		avoided here by writing the guard at the same time as the field.
		"""
		if self.flags.get("tier_transition") or self.is_new():
			return
		if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
			return
		before = self.get_doc_before_save()
		if before and before.status != self.status:
			frappe.throw(
				_("A review moves through the buttons on this form, not by editing the status."),
				frappe.PermissionError,
			)

	def _guard_decision(self):
		"""Same rule for the decision itself, and the reason is sharper.

		The decision is what authorises a position change, which is what grants
		sign-off authority over other people. It is the single most
		privilege-relevant field in the HR module.
		"""
		if self.flags.get("tier_transition") or self.is_new():
			return
		if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
			return
		before = self.get_doc_before_save()
		if before and before.decision != self.decision:
			frappe.throw(
				_("Record the decision with the buttons on this form."), frappe.PermissionError
			)

	def _reject_self_review(self):
		"""First and unconditional, whatever roles anybody holds.

		The same line the sign-off draws, and for the same reason: it is the
		sentence an auditor reads out. A promotion somebody granted themselves is
		not evidence of anything.
		"""
		if not self.reviewer_user or not self.user:
			return
		if self.reviewer_user == self.user:
			frappe.throw(_("Somebody else has to review this."), frappe.PermissionError)

	def _resolve_reviewer_user(self):
		if self.reviewer:
			self.reviewer_user = frappe.db.get_value("Employee", self.reviewer, "user_id")
