# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Announcement — a notice on the learner's training home.

Phase D of the LMS expansion (WI-071). A short, plain-text message an author or
manager posts to **all** learners, or scoped to one **course** or one **batch**.
Learners see the ones relevant to them on ``/training`` (``api/training.
_learner_announcements``), pinned first. Deliberately small: no rich text, no
threads, no per-learner state — those are other phases. The optional "email
everyone it reaches" notification is left for a follow-up; posting one is the value.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class TrainingAnnouncement(Document):
	def validate(self):
		self._validate_scope()
		if not self.posted_on:
			self.posted_on = now_datetime()

	def _validate_scope(self):
		"""A scoped announcement must name its target, and clear the field that does
		not belong to the chosen scope — otherwise switching from Course to Batch
		leaves a stale course quietly narrowing who sees it."""
		if self.scope == "Course" and not self.course:
			frappe.throw(_("Pick the course this announcement is for."))
		if self.scope == "Batch" and not self.batch:
			frappe.throw(_("Pick the batch this announcement is for."))
		if self.scope != "Course":
			self.course = None
		if self.scope != "Batch":
			self.batch = None
