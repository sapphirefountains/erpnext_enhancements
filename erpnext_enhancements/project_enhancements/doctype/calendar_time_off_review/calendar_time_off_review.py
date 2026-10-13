# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""An event on the Company Holidays & Vacations calendar that needs a planner (C4, v1.607.0).

The time-off read (``calendar_sync/time_off.py``) makes one row per Google event it could not put
on one person by itself: no name in the title, a name with no time-off word, a first name two people
share. A planner (the Planner Block schedulers: System Manager, Projects Manager, Projects User,
Maintenance Supervisor) picks:

* **Assigned** and a person: an Unavailable block for them on each day of the event;
* **Everyone**: one for every active staff member, as for "Company holiday";
* **Ignored**: nothing.

The decision is kept **for that event id** (a later edit of the event in Google keeps it) and, with
*Same for this title* ticked, for every event whose title matches (without case, punctuation or
numbers). Saving a decision queues ``time_off.apply_review`` after commit, which applies it from the
stored event with no Google call; the deploy's ``FLUSHDB`` can lose that job, and the nightly full
read applies every decision again.

Rows are made only by the read (Desk create is System Manager only). An Open row whose event becomes
clear (renamed "Austin off") or disappears is removed by the read; a decided row stays, because it
is the decision.

The class name is the one Frappe derives (``doctype.replace(" ", "")``).
"""

import frappe
from frappe import _
from frappe.model.document import Document

OPEN = "Open"
ASSIGNED = "Assigned"
#: Fields whose change re-applies the decision.
DECISION_FIELDS = ("status", "resource", "remember_title")


class CalendarTimeOffReview(Document):
	def validate(self):
		self.status = self.status or OPEN
		if self.status == ASSIGNED:
			if not self.resource:
				frappe.throw(_("Pick the person this time off is for."))
			active = frappe.db.get_value("Planner Resource", self.resource, "is_active")
			if not int(active or 0):
				frappe.throw(_("{0} is not active on the planner.").format(self.resource))
		else:
			self.resource = None
			self.resource_name = None
		if self.status == OPEN:
			self.remember_title = 0
		before = None if self.is_new() else self.get_doc_before_save()
		if (
			before is not None
			and before.get("status") != self.status
			and not self.flags.get("calendar_import")
		):
			self.decided_by = None if self.status == OPEN else frappe.session.user
			self.decided_on = None if self.status == OPEN else frappe.utils.now_datetime()

	def on_update(self):
		"""A planner changed the decision: apply it after commit. Never raises into the save."""
		if self.flags.get("calendar_import"):
			return
		try:
			before = self.get_doc_before_save()
			if before is not None and not any(before.get(f) != self.get(f) for f in DECISION_FIELDS):
				return
			from erpnext_enhancements.project_enhancements.calendar_sync import time_off

			time_off.queue_review(self.name)
		except Exception:
			frappe.log_error(title="Calendar time off: decision not queued", message=frappe.get_traceback())
