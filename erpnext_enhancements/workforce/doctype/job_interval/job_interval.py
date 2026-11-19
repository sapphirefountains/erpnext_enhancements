"""Controller for the Job Interval doctype.

A Job Interval is one Time Kiosk clock-in *session* for an Employee against a
Project/Task: ``start_time`` -> ``end_time`` with a ``status`` (Open / Paused /
Completed), accumulated ``total_paused_seconds`` and a ``last_pause_time`` for
pause/resume, plus a ``sync_status``/``sync_attempts`` block (QuickBooks Time
sync) and the location the session was started at.

Since v1.480.0 it also carries the clock-in / clock-out **anchors** and the
resolved **site** (``workforce/sites.py``), a **tracking health** block
(``workforce/tracking_health.py``), the **position** the employee held when the
session started, an **auto-close** block written by ``workforce/sweeper.py``, a
**corrections** block written by ``workforce/corrections.py``, and a permlevel-1
**pay & cost** block written by ``workforce/costing.py``.

Relationship: Time Kiosk Log rows (the individual geolocation points) link back
to a Job Interval via their ``job_interval`` field, so a session owns the trail
of points captured while it was active (replayed by the Location Timeline page).

Created/driven by the Time Kiosk PWA via the ``api.time_kiosk`` endpoints.
Row-level visibility is scoped in ``workforce/permissions.py`` (hooks.py).
"""

import frappe
from frappe import _
from frappe.model.document import Document


class JobInterval(Document):
	def onload(self):
		"""Whether the reader may approve or send back this interval's hand edit, for
		the form's buttons. The endpoints in ``workforce/time_edits.py`` decide it again."""
		from erpnext_enhancements.workforce import time_edits

		if time_edits.is_held(self):
			self.set_onload("ee_may_decide_edit", time_edits.may_decide(self))

	def validate(self):
		"""Lifecycle hook: enforce one open session per employee and time sanity,
		then keep the cost consistent with the times.

		Throws if the Employee already has another Open Job Interval (you must
		complete the current session before starting a new one), or if
		``end_time`` precedes ``start_time``.

		``labor_cost`` is recomputed on every save that has an ``end_time``. Worked
		hours are never stored (the module README's rule: elapsed is derived), so the
		only way an approved correction — which moves the times — keeps the cost
		honest is to recompute it here, on the save the correction performs.
		"""
		from erpnext_enhancements.workforce.timesheet_lock import assert_not_locked
		from erpnext_enhancements.workforce.overlap import assert_no_overlap

		# Refuse first. An edit to a locked interval must not have its overlap or cost recomputed on the way to being rejected.
		assert_not_locked(self)

		# A save through the DocType's own write permission (this form, the REST resource)
		# follows the edit endpoints' rule: hand-changed hours wait for approval, and the
		# approval fields keep what is stored (workforce/time_edits.py).
		from erpnext_enhancements.workforce import time_edits

		time_edits.guard_direct_save(self)

		# Overlap is a hard block, always.
		assert_no_overlap(self)

		# If manual_start is set, require a non-blank manual_start_reason
		if self.manual_start:
			reason = (self.manual_start_reason or "").strip()
			if not reason:
				frappe.throw(_("A manual start reason is required when the interval is manually started."))

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

		if self.end_time:
			# Never let a costing failure block a clock-out: stamp_cost logs and leaves
			# the pay block blank when no rate resolves, and raises only on a bug.
			from erpnext_enhancements.workforce import costing

			costing.stamp_cost(self)

	def on_update(self):
		"""A direct save that changed whether the interval's hand edit is held
		(``time_edits.guard_direct_save``): its Timesheet line follows, none while it is
		held, as it does after the edit endpoints' own saves."""
		if self.flags.get("edit_state_changed"):
			from erpnext_enhancements.api.time_kiosk import resync_interval_timesheet

			resync_interval_timesheet(self)
