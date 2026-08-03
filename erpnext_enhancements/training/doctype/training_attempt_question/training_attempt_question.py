# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Attempt Question — one question, as it was actually asked and answered.

A flat row per answer rather than a child table on the attempt, and that is not an
accident. The questions worth asking of this data are cross-attempt — "which
checkpoint does everybody miss", "is this bank question doing any work" — and a
child table can only be queried through its parent. It also keeps the attempt's
own row small, which matters because the attempt is rewritten every heartbeat.

``track_changes`` is deliberately **off** here. These rows are written once and
never edited; version history on them would double the write volume of a quiz
submission for nothing.

The denormalised ``course``/``course_version``/``lesson``/``user`` columns are
filled in from the attempt rather than passed in, so a caller cannot file an
answer under somebody else's name, and so reporting never has to join back.

``given_answer`` holds what the learner picked. The correct answer is not stored
here and never travels to a client — grading reads the published answer key
server-side (``training/grading.py``) and only the verdict comes back.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

# Fields copied down from the parent attempt. Listed once so the derivation and
# the doctype cannot drift apart.
_INHERITED_FIELDS = ("course", "course_version", "user")


class TrainingAttemptQuestion(Document):
	def validate(self):
		self._inherit_from_attempt()
		self._stamp_answered_on()
		self._require_a_source()

	# ------------------------------------------------------------------ helpers

	def _inherit_from_attempt(self):
		"""Copy the attempt's identity down. Always overwrites: these columns are a
		cache of the parent, and a caller-supplied value for any of them is either
		redundant or a lie."""
		if not self.attempt:
			return
		parent = frappe.db.get_value(
			"Training Attempt", self.attempt, ["course", "course_version", "user"], as_dict=True
		)
		if not parent:
			return
		for field in _INHERITED_FIELDS:
			self.set(field, parent.get(field))

	def _stamp_answered_on(self):
		if not self.answered_on:
			self.answered_on = now_datetime()

	def _require_a_source(self):
		"""A row that names neither a question nor a checkpoint cannot be traced
		back to anything, which makes it useless in exactly the audit it exists
		for."""
		if not (self.question or self.checkpoint):
			frappe.throw(_("An answer has to record which question or checkpoint it answered."))
