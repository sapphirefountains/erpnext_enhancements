# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Time Correction Request — a technician's ask to change a clock-in record.

The kiosk cannot be trusted to have recorded the truth: a phone dies, a clock-out
is forgotten, the wrong project is picked in a hurry. Rather than letting anybody
edit a Job Interval (which is what payroll is built from), the technician files
one of these from the kiosk's My Day view and a reviewer decides. The review —
approve, decline, apply, re-sync the Timesheet line, email the technician — lives
in ``workforce/corrections.py``; this controller only guards the shape of a
request.

Who may review: HR Manager, Projects Manager, System Manager, **or the
employee's ``reports_to`` user**, who need hold none of those roles. The desk form
draws Approve / Decline for exactly that set; the endpoint enforces it
(``permissions.can_review``).

Statuses: Requested → Approved / Declined, or Canceled by the employee while it is
still Requested. ``Canceled`` with one *l* — house style.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

#: request_type -> the proposal fields it cannot do without.
REQUIRED_PROPOSALS = {
	"Adjust Times": ("proposed_start", "proposed_end"),
	"Change Project": ("proposed_project",),
	"Missed Clock-Out": ("proposed_end",),
	"Missed Entry": ("proposed_start", "proposed_end", "proposed_project"),
}

OPEN_STATUS = "Requested"


class TimeCorrectionRequest(Document):
	def validate(self):
		if not self.requested_on:
			self.requested_on = now_datetime()
		if not self.status:
			self.status = OPEN_STATUS

		self._check_proposals()
		self._check_interval_ownership()
		self._check_employee_edit_window()

	def after_insert(self):
		# Tell the supervisor. Never raises — a request that exists but whose
		# email failed is still a request; corrections.py logs the failure.
		from erpnext_enhancements.workforce import corrections

		corrections.notify_new_request(self)

	# ------------------------------------------------------------------ rules

	def _check_proposals(self):
		required = REQUIRED_PROPOSALS.get(self.request_type)
		if required is None:
			frappe.throw(_("Unknown request type: {0}").format(self.request_type))

		missing = [self.meta.get_label(f) for f in required if not self.get(f)]
		if missing:
			frappe.throw(
				_("A {0} request needs: {1}.").format(self.request_type, ", ".join(missing)),
				title=_("Incomplete request"),
			)

		if self.request_type == "Missed Entry":
			if self.job_interval:
				frappe.throw(_("A Missed Entry describes an interval that does not exist yet; leave Job Interval blank."))
		elif not self.job_interval:
			frappe.throw(_("A {0} request must name the Job Interval it corrects.").format(self.request_type))

		if self.proposed_start and self.proposed_end and self.proposed_end <= self.proposed_start:
			frappe.throw(_("Proposed End must be after Proposed Start."))

	def _check_interval_ownership(self):
		if not self.job_interval:
			return
		owner = frappe.db.get_value("Job Interval", self.job_interval, "employee")
		if not owner:
			frappe.throw(_("Job Interval {0} does not exist.").format(self.job_interval))
		if owner != self.employee:
			frappe.throw(
				_("Job Interval {0} belongs to a different employee.").format(self.job_interval),
				frappe.PermissionError,
			)

	def _check_employee_edit_window(self):
		"""The employee may edit only while the request is still ``Requested``.

		Reviewers (and the app's own approve/decline/cancel paths, which set
		``flags.ee_review``) are exempt — a decision has to be able to change the
		status of a Requested row, and a review note may follow later.
		"""
		if self.is_new() or self.flags.get("ee_review"):
			return
		from erpnext_enhancements.workforce import permissions

		if permissions.can_review(self.employee, frappe.session.user):
			return
		before = self.get_doc_before_save()
		previous_status = before.status if before else self.status
		if previous_status != OPEN_STATUS:
			frappe.throw(
				_("This request has been {0} and can no longer be edited.").format(previous_status.lower()),
				frappe.PermissionError,
			)
		if self.status != OPEN_STATUS:
			frappe.throw(_("Only the reviewer can change a request's status."), frappe.PermissionError)
