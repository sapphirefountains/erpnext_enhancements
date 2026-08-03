# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Question Thread — ask the author, from inside the lesson.

One question, one answer, anchored to a lesson and usually to a timestamp in its
video. The anchor is the point: an answer attached to the second where people get
stuck is worth several paragraphs added to the script.

**``is_public`` defaults to 0 and stays a decision.** Questions arrive phrased
personally and routinely carry site names, customer names or a photo of somebody's
pump room. Publishing has to be something an author does on purpose, per thread —
so nothing here ever flips it on, and hiding a thread forces it back off.

Status is derived from whether an answer exists, with one exception: ``Hidden`` is
sticky, because it is the author saying "stop showing me this", and re-deriving it
from the presence of an answer would put the thread straight back in the queue.

Not submittable. A Q&A thread is a conversation, not an artefact — answers get
corrected, and docstatus would make that a cancel-and-amend for no audit gain.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import cint, now_datetime, strip_html

OPEN = "Open"
ANSWERED = "Answered"
HIDDEN = "Hidden"


class TrainingQuestionThread(Document):
	def validate(self):
		self._stamp_asker()
		self._fill_course_from_lesson()
		self._stamp_answer()
		self._derive_status()
		self._clamp_visibility()
		self._clamp_counters()

	# ------------------------------------------------------------------ helpers

	def _stamp_asker(self):
		"""Only on insert. Re-deriving would rewrite the asker to whichever author
		happened to open the thread to answer it."""
		if self.is_new():
			if not self.user:
				self.user = frappe.session.user
			if not self.asked_on:
				self.asked_on = now_datetime()

	def _fill_course_from_lesson(self):
		"""Denormalised off the lesson so the author's queue can be filtered by
		course without a join, and so the thread still says what it was about after
		a lesson is retired."""
		if not self.lesson or (self.course and self.course_version):
			return
		lesson = frappe.db.get_value(
			"Training Lesson", self.lesson, ["course", "course_version"], as_dict=True
		)
		if not lesson:
			return
		if not self.course:
			self.course = lesson.course
		if not self.course_version:
			self.course_version = lesson.course_version

	def _stamp_answer(self):
		"""``strip_html`` because ``answer`` is a Text Editor: an empty rich-text
		box arrives as ``<div><br></div>``, which is truthy and would otherwise mark
		the thread answered with nothing in it."""
		if not _has_text(self.answer):
			self.answered_by = None
			self.answered_on = None
			return
		if not self.answered_by:
			self.answered_by = frappe.session.user
		if not self.answered_on:
			self.answered_on = now_datetime()

	def _derive_status(self):
		if self.status == HIDDEN:
			return
		self.status = ANSWERED if _has_text(self.answer) else OPEN

	def _clamp_visibility(self):
		"""Two rules, both one-directional. A hidden thread is never public, and an
		unanswered one is never public either — publishing a question with no answer
		under it just tells the next learner that nobody knows."""
		if self.status == HIDDEN or not _has_text(self.answer):
			self.is_public = 0

	def _clamp_counters(self):
		if cint(self.upvotes) < 0:
			self.upvotes = 0


def _has_text(value):
	if not value:
		return False
	return bool(strip_html(value).strip())
