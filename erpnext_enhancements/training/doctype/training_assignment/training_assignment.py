# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Assignment — one learner owes one course by one date.

Deliberately **not** submittable. Assignments get re-dated, waived and cancelled
as a matter of routine; docstatus would add friction to all three and buy no audit
value, because the audit artefact is the ``Training Completion``, which *is*
submittable and immutable.

The rule that matters here is **one open assignment per (course, user)**, enforced
in ``validate`` rather than by a unique index — because a second row is entirely
legitimate once the first has completed. That is exactly what recertification is.
An index could not tell the two cases apart.

``user`` is the canonical identity, not ``employee``. A customer contact taking a
handover course has a User and no Employee, and keying everything on User means
the runtime never branches on who the learner is.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, getdate, nowdate, today

# Statuses that still represent work outstanding. Everything else has either
# concluded or been called off.
OPEN_STATUSES = ("Not Started", "In Progress", "Awaiting Sign-off", "Overdue")
CLOSED_STATUSES = ("Completed", "Waived", "Cancelled")


class TrainingAssignment(Document):
	def validate(self):
		self._resolve_learner()
		self._stamp_assigner()
		self._default_due_date()
		self._require_waiver_reason()
		self._reject_duplicate_open_assignment()
		self._derive_overdue()

	# ------------------------------------------------------------------ helpers

	def _resolve_learner(self):
		"""Fill in whichever of employee/customer follows from the user, and keep
		``learner_type`` honest. Never trusts a caller-supplied employee — the link
		is always derived from the User."""
		if not self.user:
			frappe.throw(_("An assignment needs a learner."))

		employee = frappe.db.get_value("Employee", {"user_id": self.user, "status": "Active"}, "name")
		if employee:
			self.learner_type = "Employee"
			self.employee = employee
			self.customer = None
			return

		self.learner_type = "Website User"
		self.employee = None
		if not self.customer:
			self.customer = _customer_for_user(self.user)

	def _stamp_assigner(self):
		if self.is_new() and not self.assigned_by:
			self.assigned_by = frappe.session.user
		if not self.assigned_on:
			self.assigned_on = today()

	def _default_due_date(self):
		"""Only Required courses carry a due date. Putting one on an optional
		library course would make it overdue and start chasing people over
		something nobody asked them to do."""
		weight = frappe.db.get_value("Training Course", self.course, "weight")
		if weight != "Required":
			self.due_date = None
			return
		if self.due_date:
			return
		due_days = cint(frappe.db.get_value("Training Course", self.course, "due_days"))
		if not due_days:
			due_days = cint(
				frappe.db.get_single_value("Training Settings", "default_due_days")
			) or 14
		self.due_date = add_days(self.assigned_on or today(), due_days)

	def _require_waiver_reason(self):
		if self.status in ("Waived", "Cancelled") and not (self.waiver_reason or "").strip():
			frappe.throw(
				_("Say why this assignment is being {0}. An unexplained waiver on a required course is "
				  "exactly what gets asked about later.").format(self.status.lower())
			)

	def _reject_duplicate_open_assignment(self):
		"""Two open rows for the same person and course means two reminder emails,
		two entries in their list, and an ambiguous answer to "is this person
		compliant". Recertification is not affected — it only ever raises a new row
		once the previous one has completed."""
		if self.status in CLOSED_STATUSES:
			return
		existing = frappe.get_all(
			"Training Assignment",
			filters={
				"course": self.course,
				"user": self.user,
				"status": ["in", OPEN_STATUSES],
				"name": ["!=", self.name or ""],
			},
			fields=["name", "due_date"],
			limit=1,
		)
		if existing:
			frappe.throw(
				_("{0} already has {1} open for this course, due {2}. Re-date that one rather than "
				  "raising a second.").format(
					self.user, existing[0].name, existing[0].due_date or _("no date")
				)
			)

	def _derive_overdue(self):
		"""Overdue is a fact about the date, not a state somebody sets — and it has
		to be able to fall *back* when a manager extends the deadline."""
		if self.status in CLOSED_STATUSES or self.status == "Awaiting Sign-off":
			return
		if not self.due_date:
			return
		if getdate(self.due_date) < getdate(nowdate()):
			self.status = "Overdue"
		elif self.status == "Overdue":
			# Falls back to Not Started; the learner runtime moves it to In Progress
			# the moment an attempt is opened against it (Phase 2).
			self.status = "Not Started"


def _customer_for_user(user):
	"""The Customer a Website User belongs to, via Contact → Dynamic Link.

	Returns None rather than throwing: an internal user with no Employee record
	yet is a normal state during onboarding, and an assignment should not be
	blocked on it.
	"""
	if not user:
		return None
	contact = frappe.db.get_value("Contact", {"user": user}, "name")
	if not contact:
		return None
	return frappe.db.get_value(
		"Dynamic Link",
		{
			"parent": contact,
			"parenttype": "Contact",
			"link_doctype": "Customer",
		},
		"link_name",
	)
