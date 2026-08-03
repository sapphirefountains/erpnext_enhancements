# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Attempt — one learner's run at one version of one course.

This is the single mutable record of the learner runtime. Everything the player
does lands here: watched intervals, checkpoint answers, quiz runs, and the
server's own opinion of whether any of it can be believed.

**Deliberately not submittable.** An attempt is working state, written every few
seconds; docstatus would freeze it at exactly the wrong moment. The immutable
audit artefact is ``Training Completion``, which *is* submittable, and which
snapshots this attempt's outcome rather than pointing at live fields.

Three things about this doctype are load-bearing:

* ``shuffle_seed`` is stamped **once**, in ``validate``, and never re-rolled.
  Every draw of a quiz derives its question and option order from it, so a page
  reload gives the learner the same paper back. Re-stamping on a later save would
  reshuffle a quiz somebody is halfway through and silently invalidate the
  answers already submitted against the old order.

* ``employee`` is *derived from* ``user`` and never accepted from a caller, the
  same rule as ``Training Assignment``. ``user`` itself is the endpoint's
  responsibility: the runtime sets it from ``frappe.session.user`` and never from
  the request body. This controller cannot enforce that — a Training Manager
  legitimately opens the desk form on somebody else's attempt — so the guarantee
  lives at the API boundary, not here.

* ``progress_json`` must be written with ``db_set``/``frappe.db.set_value``, not
  ``doc.save()``. ``track_changes`` is on (an auditor asks who moved a score),
  and a full ``save()`` every heartbeat would write a Version row carrying two
  copies of the whole progress blob every sixty seconds per learner. ``db_set``
  does not run ``save_version``. ``training/progress.py`` is written to that rule.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

# An attempt in one of these has stopped moving; the runtime will not accept a
# heartbeat against it and the learner needs a fresh one to carry on.
TERMINAL_STATUSES = ("Passed", "Failed", "Abandoned")


class TrainingAttempt(Document):
	def validate(self):
		self._require_learner()
		self._stamp_shuffle_seed()
		self._resolve_employee()
		self._stamp_timestamps()
		self._default_progress_json()

	# ------------------------------------------------------------------ helpers

	def _require_learner(self):
		if not self.user:
			frappe.throw(_("An attempt needs a learner."))

	def _stamp_shuffle_seed(self):
		"""Once, and never again — see the module docstring. ``generate_hash`` is
		seeded from the platform CSPRNG, so the seed is not guessable from the
		attempt name or the time it was created."""
		if not (self.shuffle_seed or "").strip():
			self.shuffle_seed = frappe.generate_hash(length=32)

	def _resolve_employee(self):
		"""Never trusts a supplied employee. A customer contact taking a handover
		course has a User and no Employee, and that is a normal state, not an
		error — the link is simply left blank."""
		self.employee = frappe.db.get_value(
			"Employee", {"user_id": self.user, "status": "Active"}, "name"
		)

	def _stamp_timestamps(self):
		if not self.started_on:
			self.started_on = now_datetime()
		if not self.last_activity_on:
			self.last_activity_on = self.started_on
		if self.status in TERMINAL_STATUSES and not self.completed_on:
			self.completed_on = now_datetime()

	def _default_progress_json(self):
		"""An empty string is not parseable JSON. Defaulting here means
		``progress.load`` never has to special-case a brand-new attempt, and a row
		created by hand in the desk behaves the same as one the runtime opened."""
		if not (self.progress_json or "").strip():
			self.progress_json = "{}"
