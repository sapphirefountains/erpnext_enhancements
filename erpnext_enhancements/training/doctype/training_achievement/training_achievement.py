# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Achievement — the thing colleagues may react to.

**Its whole reason for existing is what it does not carry.**

The obvious design is to let people react to a ``Training Completion``. That
record is the compliance artefact — its own controller calls it "the only record
in the module that anybody outside it will ever be asked to produce: to a client,
to an insurer" — and it carries ``score_percent``, ``video_coverage_percent``,
``attempt``, ``status`` (Valid / Superseded / Expired / Revoked) and
``revoked_reason``. Both plausible reuse routes for reactions begin with a read
check on the referenced document (``api/comments.py`` and frappe's own
``desk/like.py``), so "let colleagues react to a completion" reduces exactly to
"publish second-attempt scores and revocations to the whole company". That is the
single worst thing this feature could do.

So an achievement is a separate, deliberately public row minted alongside the
completion. It carries a **snapshot title**, a kind, a date and who — no score, no
attempt, no coverage, no failure and no expiry. There is no field here that a
future change could widen into performance data, because there is no field here
that holds any.

Two consequences fall out of that, and both are the point:

* a **failed** attempt never produces one, so the feed cannot become a record of
  who struggled;
* a **revoked or superseded** completion has its achievement cancelled, so the
  feed does not keep celebrating something that has since been withdrawn — while
  the completion itself, which is evidence, stays exactly where it is.

``learner_type`` is derived and mandatory on every read. That is copied verbatim
from ``gamification.py``, including the reason it gives: *"an optional privacy
filter is a privacy filter somebody eventually leaves out."* Customer contacts
hold ``Training Learner`` and must never see a staff feed.
"""

import frappe
from frappe.model.document import Document

STAFF = "Staff"
CUSTOMER = "Customer"

COURSE_COMPLETED = "Course Completed"
BADGE_EARNED = "Badge Earned"
SIGNED_OFF = "Signed Off"
WORK_ANNIVERSARY = "Work Anniversary"


class TrainingAchievement(Document):
	def validate(self):
		self._resolve_learner()
		self._apply_visibility()

	def _resolve_learner(self):
		"""Employment decides the audience, and it is derived here rather than sent.

		Same rule as everywhere else in this release: a role can be granted by
		accident — every customer contact holds Training Learner — but whether
		somebody works here cannot be.
		"""
		employee = frappe.db.get_value(
			"Employee", {"user_id": self.user}, ["name", "employee_name"], as_dict=True
		)
		self.employee = employee.name if employee else None
		self.learner_type = STAFF if employee else CUSTOMER

	def _apply_visibility(self):
		"""Honour the person's own opt-out at creation.

		Stamped onto the row rather than joined at read time, so a feed query stays
		one indexed read and an opt-out cannot be forgotten by a future caller that
		writes its own filter. ``notifications.resweep_visibility`` keeps it true
		when somebody changes their mind.

		**Gated on ``is_new()``, not on the field being empty**, and that is the
		whole correctness of it. The field carries ``"default": "Team"`` in the
		JSON, and v16 applies defaults in ``_set_defaults()``
		(``frappe/model/document.py:474``) *twelve lines before* ``validate`` runs
		at ``:486`` — so an ``if self.visibility: return`` guard was already true on
		every insert and this branch never executed. Every achievement was published
		to the team feed, including those of people who had opted out, and the
		preference silently did nothing. Found by the migrate-safety audit.

		Deleting the JSON default would not have fixed it either:
		``frappe/model/create_new.py:117-118`` falls back to the first option of a
		Select with options, which is also ``"Team"``.
		"""
		if not self.is_new():
			# An existing row keeps what it has: `resweep_visibility` owns changes
			# after creation, and re-deriving here would fight it.
			return
		self.visibility = "Team" if wants_feed(self.user) else "Private"


def wants_feed(user):
	"""Whether this person has left themselves on the team feed. Defaults to yes.

	Absent preferences mean the default, not silence: a site where nobody has
	touched the setting should have a working feed, and an opt-in feed in a
	sixteen-person company is an empty one.
	"""
	if not frappe.db.exists("DocType", "Training Profile Preference"):
		return True
	value = frappe.db.get_value("Training Profile Preference", {"user": user}, "show_on_feed")
	return True if value is None else bool(value)
