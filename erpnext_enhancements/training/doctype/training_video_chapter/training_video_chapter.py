# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A clickable timestamp inside a lesson video.

Mirrors :class:`~erpnext_enhancements.training.doctype.training_checkpoint.training_checkpoint.TrainingCheckpoint`
deliberately, down to the keying: a **standalone doctype** hanging off
``(lesson, block_key)`` rather than a child table. `Training Content Block` is
itself a child of `Training Lesson`, and Frappe has no grandchild tables — the
same constraint that put checkpoints here, and the reason both join on a stable
``block_key`` rather than an ``idx``. Reordering a lesson's blocks must not move a
chapter onto a different video.

**Chapters are public and checkpoints are not**, which is the one place the mirror
stops. A checkpoint's options and per-option explanations live behind
``permlevel: 1`` and only a *count* ever reaches the browser, because shipping them
hands over the answer. A chapter is a label and a number; it has to reach the
browser to be clickable, and there is nothing in it to protect.

**Seeking to a chapter earns no watch coverage.** That is not a rule added here —
it falls out of the design already in `video.js`, which credits a media span only
when the media advance is consistent with elapsed wall time × rate, so a forward
seek credits nothing. Chapter-jumping therefore cannot be used to pass a
coverage-gated course, and nobody had to remember to make that true.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class TrainingVideoChapter(Document):
	def validate(self):
		self.at_seconds = max(0, cint(self.at_seconds))
		self.title = (self.title or "").strip()
		if not self.title:
			frappe.throw(_("A chapter needs a title."))
		self._fill_course_version()
		self._refuse_a_duplicate_timestamp()

	def _fill_course_version(self):
		"""Denormalised from the lesson, so a version's chapters are findable without
		a join. Read-only on the form for the same reason it is derived: two places to
		set it is one place for it to be wrong."""
		if self.lesson:
			self.course_version = frappe.db.get_value(
				"Training Lesson", self.lesson, "course_version"
			)

	def _refuse_a_duplicate_timestamp(self):
		"""Two chapters at the same second in the same video.

		Refused rather than tolerated because the list is ordered by `at_seconds`, so
		a duplicate renders as two adjacent entries in an arbitrary order that changes
		between reads — the author sees one order, the learner sees another, and
		neither is wrong.
		"""
		if not (self.lesson and self.block_key):
			return
		twin = frappe.db.exists(
			"Training Video Chapter",
			{
				"lesson": self.lesson,
				"block_key": self.block_key,
				"at_seconds": cint(self.at_seconds),
				"name": ["!=", self.name or ""],
			},
		)
		if twin:
			frappe.throw(
				_("This video already has a chapter at {0} seconds.").format(cint(self.at_seconds))
			)
