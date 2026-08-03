# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Badge Award — one person holds one badge, once.

**The uniqueness rule is the whole controller.** It is enforced here rather than
by a unique index because the pair spans two Link columns and Frappe's docfield
``unique`` only covers one; a composite index would have to be added by patch and
then maintained outside the doctype JSON, which is exactly the kind of
customization this repo keeps in one place. The check is cheap — an indexed lookup
on ``badge`` and ``user``.

It matters more than it looks. The badge evaluator in ``training/gamification.py``
is idempotent by *re-running*: it recomputes what somebody should hold and awards
what is missing. Without this guard a nightly re-run would hand out the same badge
every night, and ``Training Learner Stat.total_points`` — which sums awards —
would climb for doing nothing.

``points`` is copied off the badge, not read through the link, so re-pricing a
badge later does not rewrite scores already earned.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime


class TrainingBadgeAward(Document):
	def validate(self):
		self._resolve_holder()
		self._reject_duplicate_award()
		self._copy_points_from_badge()
		self._stamp_awarded_on()

	# ------------------------------------------------------------------ helpers

	def _resolve_holder(self):
		if not self.user:
			frappe.throw(_("An award needs somebody to hold it."))
		if not self.employee:
			self.employee = frappe.db.get_value("Employee", {"user_id": self.user}, "name")

	def _reject_duplicate_award(self):
		existing = frappe.db.get_value(
			"Training Badge Award",
			{"badge": self.badge, "user": self.user, "name": ["!=", self.name or ""]},
			"name",
		)
		if existing:
			frappe.throw(
				_("{0} already holds {1} (awarded as {2}).").format(self.user, self.badge, existing)
			)

	def _copy_points_from_badge(self):
		"""Guarded on 'not already set' so an award being re-saved keeps the number
		it was made with."""
		if self.points is not None and cint(self.points):
			return
		self.points = cint(frappe.db.get_value("Training Badge", self.badge, "points"))

	def _stamp_awarded_on(self):
		if not self.awarded_on:
			self.awarded_on = now_datetime()
