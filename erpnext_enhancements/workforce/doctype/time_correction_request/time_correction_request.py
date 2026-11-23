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
employee's ``reports_to`` user**, who need hold none of those roles
(``permissions.can_review``), and never the employee the request is for or the person
who filed it (``permissions.may_decide_request``). The endpoint enforces it, this
controller holds a status change saved on the form or through the REST resource to the
same rule (``_check_decider``), and the form draws Approve / Decline only when
``onload`` says the user may decide.

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

#: Statuses that record a decision on the request.
DECIDED_STATUSES = ("Approved", "Declined")


class TimeCorrectionRequest(Document):
	def validate(self):
		if not self.requested_on:
			self.requested_on = now_datetime()
		if not self.status:
			self.status = OPEN_STATUS

		self._check_filer()
		self._check_proposals()
		self._check_interval_ownership()
		self._check_employee_edit_window()
		self._check_decider()

	def onload(self):
		# Read by the form to decide whether to draw Approve / Decline. Frappe runs
		# ``onload`` when the Desk form loads the document (frappe/desk/form/load.py,
		# ``getdoc`` -> ``run_onload``, lines 48 and 413-415 at v16.56.0).
		from erpnext_enhancements.workforce import permissions

		self.set_onload(
			"ee_may_decide", permissions.may_decide_request(self.employee, self.owner, frappe.session.user)
		)

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

	def _check_filer(self):
		"""A new request is filed for the caller's own Employee, unless the caller may
		review that employee's requests (``permissions.can_review``: the manager roles,
		Administrator, or the employee's ``reports_to`` login). Whoever files it cannot
		decide it (``_check_decider``)."""
		if not self.is_new():
			return
		from erpnext_enhancements.workforce import permissions

		user = frappe.session.user
		if permissions.can_review(self.employee, user):
			return
		if self.employee and self.employee == permissions._session_employee(user):
			return
		frappe.throw(
			_("You can file a time correction request only for yourself."),
			frappe.PermissionError,
		)

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

	def _check_decider(self):
		"""A save that moves the request to a decided status (``DECIDED_STATUSES``) is a
		decision, however it arrives: the review endpoints, the Desk form or the REST
		resource. It is allowed only to a reviewer who is neither the employee the request
		is for nor the person who filed it (``permissions.may_decide_request``). On a new
		request the person saving it is the one filing it, so a request is never filed
		already decided."""
		if self.status not in DECIDED_STATUSES:
			return
		before = None if self.is_new() else self.get_doc_before_save()
		if before is not None and before.status == self.status:
			return
		from erpnext_enhancements.workforce import permissions

		user = frappe.session.user
		filer = user if self.is_new() else self.owner
		if not permissions.may_decide_request(self.employee, filer, user):
			frappe.throw(
				_(
					"A time correction request can be decided only by a reviewer who is neither the employee it is for nor the person who filed it."
				),
				frappe.PermissionError,
			)
