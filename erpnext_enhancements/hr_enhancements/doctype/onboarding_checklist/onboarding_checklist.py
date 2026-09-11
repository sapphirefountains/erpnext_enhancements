# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Onboarding Checklist — what has to happen in somebody's first week.

Created automatically when an Employee is added. The trigger point already
existed: `hooks.py` fires `training.assignment.on_employee_insert` on Employee
`after_insert`, and this joins it rather than adding a second Employee hook that
would run in an order nobody declared.

**Owners are plain words, not Links.** Half of these are done by whoever is free
that morning, and a required assignee is precisely how a checklist stops getting
filled in. The point of the record is that somebody can see what has not happened
yet, not that the system knows whose fault it is.

Progress is derived on every save. A stored percentage is a percentage that goes
stale the moment somebody ticks a box in the grid.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt, today


class OnboardingChecklist(Document):
	def validate(self):
		self._resolve_user()
		self._default_start()
		self._stamp_ticks()
		self._derive_progress()

	def _resolve_user(self):
		self.user = frappe.db.get_value("Employee", self.employee, "user_id") or None

	def _default_start(self):
		if not self.starts_on:
			self.starts_on = (
				frappe.db.get_value("Employee", self.employee, "date_of_joining") or today()
			)

	def _stamp_ticks(self):
		"""Record when and by whom, once.

		Only on the transition to done: re-stamping on every save would rewrite the
		date somebody actually did the thing to the date somebody else opened the
		form.
		"""
		for row in self.items or []:
			if cint(row.done) and not row.done_on:
				row.done_on = today()
				row.done_by = frappe.session.user
			elif not cint(row.done):
				# Un-ticked. Clear both, or the record claims it was done by somebody
				# on a date, while showing it as outstanding.
				row.done_on = None
				row.done_by = None

	def _derive_progress(self):
		items = self.items or []
		self.total_count = len(items)
		self.done_count = sum(1 for row in items if cint(row.done))
		self.percent_done = flt(self.done_count) / len(items) * 100 if items else 0
		if self.status != "Canceled":
			self.status = "Complete" if items and self.done_count == len(items) else "Open"
