"""Controller for the Job Interval doctype.

A Job Interval is one Time Kiosk clock-in *session* for an Employee against a
Project/Task: ``start_time`` -> ``end_time`` with a ``status`` (Open / Paused /
Completed), accumulated ``total_paused_seconds`` and a ``last_pause_time`` for
pause/resume, plus a ``sync_status``/``sync_attempts`` block (QuickBooks Time
sync) and the location the session was started at.

Relationship: Time Kiosk Log rows (the individual geolocation points) link back
to a Job Interval via their ``job_interval`` field, so a session owns the trail
of points captured while it was active (replayed by the Location Timeline page).

Created/driven by the Time Kiosk PWA via the ``api.time_kiosk`` endpoints.
"""

import frappe
from frappe.model.document import Document
from frappe import _

class JobInterval(Document):
	def validate(self):
		"""Lifecycle hook: enforce one open session per employee and time sanity.

		Throws if the Employee already has another Open Job Interval (you must
		complete the current session before starting a new one), or if
		``end_time`` precedes ``start_time``.
		"""
		if self.status == "Open":
			# Check if employee already has an open interval
			existing = frappe.db.exists("Job Interval", {
				"employee": self.employee,
				"status": "Open",
				"name": ["!=", self.name]
			})
			if existing:
				frappe.throw(_("Employee {0} already has an open Job Interval ({1}). Please complete it before starting a new one.").format(self.employee, existing))

		if self.end_time and self.start_time and self.end_time < self.start_time:
			frappe.throw(_("End Time cannot be before Start Time"))
