# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Learner Stat — one denormalised row per learner, for the leaderboard.

Every number here is derivable from completions and badge awards, and derives
them on demand would be a table scan per viewer on a page people refresh. So the
row is maintained by ``training/gamification.py`` and read straight off an index.
That trade is fine for a leaderboard and would not be for a certificate: nothing
in this doctype is evidence of anything, and it can be rebuilt from scratch at any
time.

**``learner_type`` is derived here, never accepted from the caller.** It is the
single column that keeps staff and customers on separate leaderboards, and a
leaderboard query is a place where a wrong value shows a client's contact the
names and scores of the crew. Deriving it on every save means the guarantee does
not depend on whichever code path created the row.

``longest_streak_days`` is a high-water mark and only ever climbs. A recompute
that started from an empty streak table would otherwise quietly take away a run
somebody actually had.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

COUNTERS = (
	"total_points",
	"courses_completed",
	"current_streak_days",
	"longest_streak_days",
	"badges_earned",
)


class TrainingLearnerStat(Document):
	def validate(self):
		self._resolve_learner()
		self._clamp_counters()
		self._keep_the_high_water_mark()

	# ------------------------------------------------------------------ helpers

	def _resolve_learner(self):
		if not self.user:
			frappe.throw(_("A learner stat row needs a user."))
		employee = frappe.db.get_value("Employee", {"user_id": self.user}, "name")
		self.employee = employee
		self.learner_type = "Employee" if employee else "Website User"

	def _clamp_counters(self):
		"""A negative counter means an aggregation subtracted something it should
		not have. Zero is the honest floor; a leaderboard showing -3 points is a bug
		report from a user rather than from a log."""
		for field in COUNTERS:
			if cint(self.get(field)) < 0:
				self.set(field, 0)

	def _keep_the_high_water_mark(self):
		if cint(self.current_streak_days) > cint(self.longest_streak_days):
			self.longest_streak_days = cint(self.current_streak_days)
