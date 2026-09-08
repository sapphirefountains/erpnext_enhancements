# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A learner's work submission and its grade.

The write paths that matter — a learner handing work in, a manager grading it —
live in ``training/submissions.py``, which owns the file re-parenting, the
permission checks and the notifications. This controller only keeps a record
internally consistent whichever door it comes through, including a manager editing
the form directly in the Desk (which skips the endpoint entirely):

* every submission is stamped ``submitted_on`` even when created by hand;
* a ``Needs Rework`` verdict must carry feedback — the one grade that is useless
  without words;
* a terminal verdict (Passed / Needs Rework) records who graded it and when, so a
  status flipped on the form is as accountable as one filed through the button.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class TrainingSubmission(Document):
	def before_insert(self):
		if not self.submitted_on:
			self.submitted_on = frappe.utils.now_datetime()

	def validate(self):
		if self.status == "Needs Rework" and not (self.feedback or "").strip():
			frappe.throw(
				_("A Needs Rework submission needs feedback saying what to change.")
			)
		# A terminal verdict is accountable: record the grader and the time even when
		# the status was flipped on the form rather than filed through grade_submission.
		if self.status in ("Passed", "Needs Rework"):
			if not self.graded_by:
				self.graded_by = frappe.session.user
			if not self.graded_on:
				self.graded_on = frappe.utils.now_datetime()
