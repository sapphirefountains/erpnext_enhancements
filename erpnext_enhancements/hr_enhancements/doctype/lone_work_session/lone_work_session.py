# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Somebody going to a site alone, and the clock that notices if they do not come
back.

The mechanism is one field: `expected_out_by`. If it passes without a check-out,
`hr_enhancements/lonework.py` chases them, then their supervisor, then the CEO.
Everything else here is bookkeeping around that.

**Anybody can open their own**, and the record is deliberately trivial to create:
a check-in somebody else has to open is a check-in that does not happen, and one
that takes four fields is one that gets skipped on the day it would have mattered.

The status is derived by the sweep rather than set by hand, because "Overdue" is a
statement about the clock and not about anybody's intention.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime

OPEN = "Open"
CLOSED = "Closed"
OVERDUE = "Overdue"
ESCALATED = "Escalated"


class LoneWorkSession(Document):
	def before_insert(self):
		self.started_on = now_datetime()
		self.status = OPEN
		self.escalation_stage = 0

	def validate(self):
		self._resolve_user()
		self._reject_backwards_clock()

	def _resolve_user(self):
		if self.employee:
			self.user = frappe.db.get_value("Employee", self.employee, "user_id")

	def _reject_backwards_clock(self):
		"""An expected-out time already in the past is a session that escalates on the
		next sweep, which is noise rather than safety."""
		if not self.expected_out_by:
			return
		basis = get_datetime(self.started_on or now_datetime())
		if get_datetime(self.expected_out_by) <= basis:
			frappe.throw(_("Expected out has to be after you started."))

	def is_overdue(self, when=None):
		if self.status in (CLOSED,):
			return False
		if not self.expected_out_by:
			return False
		return get_datetime(when or now_datetime()) > get_datetime(self.expected_out_by)
