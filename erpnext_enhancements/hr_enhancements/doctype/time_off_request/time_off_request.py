# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Time Off Request — ask, approve, and let dispatch see it.

Built from scratch because ``hrms`` is not installed on this site and installing
it now would collide with the ``HR`` module name and with six ``Training *``
doctype names. Scoped to exactly what Nik asked for: **request → approve →
calendar**. No balances, no accrual, no carryover.

That scope is a decision, not an omission. Balances are where the real complexity
and every payroll argument live, and they are only worth carrying if PTO is being
tracked as a liability. It is not, here. The daily need is "can I have next
Thursday" and "who is out that week", and both are answered without a single
allocation record.

**Calendar days, not working days.** ``total_days`` counts the dates as they fall,
because there is no holiday calendar on this site to subtract from them. A number
that quietly pretended to be working days would be wrong by an amount nobody could
predict, and wrong in the direction that shortens somebody's leave.

Not submittable. A submittable request would make cancelling a Draft a ``docstatus
2``, and "I put in for Thursday and then didn't" is a change of mind rather than
an amendment of a document. Status drives it, and the transitions live in
``hr_enhancements/timeoff.py`` where the notifications are.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, date_diff, getdate

DRAFT = "Draft"
REQUESTED = "Requested"
APPROVED = "Approved"
DECLINED = "Declined"
# One l. House style, and worth spelling out here because renaming a Select option
# later needs a data patch or every existing row refuses to save.
CANCELED = "Canceled"

OPEN_STATUSES = (DRAFT, REQUESTED)
BLOCKING_STATUSES = (REQUESTED, APPROVED)


class TimeOffRequest(Document):
	def validate(self):
		self._resolve_user()
		self._reject_backwards_dates()
		self._compute_days()
		self._resolve_approver()
		self._require_decline_reason()
		self._warn_on_overlap()

	# ------------------------------------------------------------------ helpers

	def _resolve_user(self):
		self.user = frappe.db.get_value("Employee", self.employee, "user_id") or None

	def _reject_backwards_dates(self):
		if self.from_date and self.to_date and getdate(self.to_date) < getdate(self.from_date):
			frappe.throw(_("The end date is before the start date."))

	def _compute_days(self):
		if not (self.from_date and self.to_date):
			self.total_days = 0
			return
		days = date_diff(getdate(self.to_date), getdate(self.from_date)) + 1
		if cint(self.half_day) and days == 1:
			days = 0.5
		elif cint(self.half_day):
			# Half a day only means something on a single day. Silently ignoring the
			# tick on a week-long request would leave the form claiming something it
			# is not doing.
			self.half_day = 0
		self.total_days = days

	def _resolve_approver(self):
		"""Frozen once set.

		Same doctrine as the training sign-off: a reporting-line change between the
		request and the decision must not silently move who was allowed to decide.
		"""
		if self.approver:
			self.approver_user = frappe.db.get_value("Employee", self.approver, "user_id")
			return
		manager = frappe.db.get_value("Employee", self.employee, "reports_to")
		self.approver = manager or None
		self.approver_user = (
			frappe.db.get_value("Employee", manager, "user_id") if manager else None
		)

	def _require_decline_reason(self):
		if self.status == DECLINED and not (self.decision_note or "").strip():
			frappe.throw(
				_("Say why. A refusal with no reason leaves somebody nothing to plan around.")
			)

	def _warn_on_overlap(self):
		"""Advisory, never a refusal.

		Two people off the same week is a real problem and a scheduling
		conversation; it is not this record's business to prevent. Blocking here
		would mean somebody who genuinely needs the day simply not asking.
		"""
		if self.status not in BLOCKING_STATUSES or not (self.from_date and self.to_date):
			return
		clash = frappe.get_all(
			"Time Off Request",
			filters={
				"name": ["!=", self.name or ""],
				"status": ["in", BLOCKING_STATUSES],
				"from_date": ["<=", self.to_date],
				"to_date": [">=", self.from_date],
			},
			fields=["employee_name"],
			limit=5,
		)
		others = [row.employee_name for row in clash if row.employee_name != self.employee_name]
		if others:
			frappe.msgprint(
				_("Also off then: {0}.").format(", ".join(sorted(set(others)))),
				indicator="orange",
				alert=True,
			)
