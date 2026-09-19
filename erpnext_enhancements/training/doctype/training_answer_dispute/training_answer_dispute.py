# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Answer Dispute — a learner saying the machine got it wrong.

Short Answer questions are graded by an AI as of v1.490.0, with no human sign-off
in front of it. This doctype is the other half of that decision: the AI is allowed
to decide unreviewed *because* the learner can always push back, and a push-back
reaches a named person.

Three rules hold the record honest, and each is enforced here rather than trusted
to the caller:

* **Everything the reviewer reads is snapshotted at the moment the dispute is
  raised.** The question wording, the typed answer, the accepted answers and the
  AI's reasoning are all copied onto this document. Looking them up live would
  mean a new draft version -- which can rewrite the accepted answers freely --
  changing what the reviewer believes the learner was marked against, months
  after the fact.
* **Only one open dispute per answer.** Without it, a learner refreshing the
  review screen files a queue of identical rows, and two managers can rule on the
  same answer in opposite directions.
* **The verdict is not editable after the fact.** Once a dispute leaves Open, its
  status is frozen; a reviewer who changes their mind resolves it as it stands and
  the correction is made on the answer row, which carries its own
  ``corrected_by`` / ``corrected_on``. An audit trail that can be rewritten is not
  one.

The class name is ``TrainingAnswerDispute`` because Frappe derives the controller
name as ``doctype.replace(" ", "").replace("-", "")`` with **no title-casing** --
every word here is already capitalised so the derivation is safe, but a DocType
named "Training Answer of Dispute" would resolve to ``TrainingAnsweofDispute`` and
`get_controller` would raise, at which point ``remove_orphan_doctypes()``
force-deletes the DocType on the next migrate. See ``tests/test_doctype_controller_names.py``.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class TrainingAnswerDispute(Document):
	def validate(self):
		self._one_open_per_answer()
		self._freeze_a_settled_verdict()
		if not self.raised_on:
			self.raised_on = now_datetime()

	def _one_open_per_answer(self):
		"""A second open dispute on the same answer is refused.

		The learner-facing endpoint checks this too and returns the existing one
		rather than throwing -- a learner pressing the button twice should not see
		an error. This is the backstop for every other way a row could be created,
		including the Desk.
		"""
		if self.status != "Open" or not self.attempt_question:
			return
		clash = frappe.get_all(
			"Training Answer Dispute",
			filters={
				"attempt_question": self.attempt_question,
				"status": "Open",
				"name": ("!=", self.name or ""),
			},
			pluck="name",
			limit=1,
		)
		if clash:
			frappe.throw(
				_("{0} is already open against this answer. Resolve it rather than raising a second one.").format(
					clash[0]
				)
			)

	def _freeze_a_settled_verdict(self):
		"""Upheld and Rejected are terminal.

		Not merely tidiness: upholding runs a re-score and can issue a completion
		and a certificate. Flipping the status back and forth afterwards would
		imply those were undone, and nothing undoes them.
		"""
		if self.is_new():
			return
		was = frappe.db.get_value("Training Answer Dispute", self.name, "status")
		if was and was != "Open" and self.status != was:
			frappe.throw(
				_("{0} was already resolved as {1}. A ruling is not edited after the fact — "
				  "record a new one on the answer if it was wrong.").format(self.name, was)
			)
