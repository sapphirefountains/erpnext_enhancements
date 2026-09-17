# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The hourly sweeper: close the clock-ins nobody clocked out of.

A forgotten clock-out is the commonest bad row a time clock produces, and left alone it
does two things at once: it blocks the technician from clocking in tomorrow ("you already
have an active job") and it grows into a 30-hour interval that payroll has to notice by
eye. So an interval still Open or Paused ``auto_close_after_hours`` (Time Kiosk Settings,
14 by default) after it started is closed here, with the most defensible end time the
evidence supports:

1. **Paused** → the pause time. The technician told us when they stopped.
2. Else the **latest location fix** for the interval, if it is after the start. The phone
   stopped reporting when they went home; that is the best clock-out we have.
3. Else **start + the limit**. Nothing to go on; the limit is the policy.

The interval is flagged ``auto_closed`` with the rule that decided (``auto_close_reason``),
so the hours get a second look — the supervisor digest lists them and My Day badges them —
and the employee and their ``reports_to`` are emailed. The technician can then file a
Missed Clock-Out correction with the real time.

Stamp-first, at-most-once, per-interval: the closing fields are set before anything
else touches the row, the candidate query only ever sees Open/Paused rows so a closed
one is never revisited, and each interval is its own try/except with a commit, so one
bad row cannot stop the rest of the sweep or roll back the ones already done.

The decision itself — ``decide_end_time`` — is a pure function so
``tests/test_workforce_sweeper.py`` can pin the three rules without a bench.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, get_url_to_form, now_datetime

from erpnext_enhancements import email_style
from erpnext_enhancements.workforce import photo_gate
from erpnext_enhancements.workforce.doctype.time_kiosk_settings.time_kiosk_settings import (
	get_settings,
)

REASON_PAUSED = "paused"
REASON_LAST_FIX = "last_fix"
REASON_LIMIT = "limit"

REASON_TEXT = {
	REASON_PAUSED: "End time = the pause time (the interval was Paused when the limit passed).",
	REASON_LAST_FIX: "End time = the last location fix recorded for this interval.",
	REASON_LIMIT: "End time = start + {hours} h (no pause and no location fix to go on).",
}


# ------------------------------------------------------------------ pure


def decide_end_time(status, start_time, last_pause_time, latest_fix, limit_hours, now=None):
	"""``(end_time, reason)`` for a stale interval. See the module docstring.

	Never returns an end before ``start_time`` and never one after ``now``; a
	fix stamped in the future (a phone with a wrong clock) falls back to the limit.
	"""
	start = get_datetime(start_time)
	now = get_datetime(now) if now else now_datetime()
	limit = start + timedelta(hours=max(float(limit_hours or 0), 0.0))

	if status == "Paused" and last_pause_time:
		paused_at = get_datetime(last_pause_time)
		if start <= paused_at <= now:
			return paused_at, REASON_PAUSED

	if latest_fix:
		fix = get_datetime(latest_fix)
		if start < fix <= now:
			return fix, REASON_LAST_FIX

	return min(limit, now) if limit > start else start, REASON_LIMIT


def reason_text(reason, limit_hours):
	return (REASON_TEXT.get(reason) or REASON_TEXT[REASON_LIMIT]).format(hours=cint(limit_hours))


# ------------------------------------------------------------------ scheduler


def auto_close_stale_intervals():
	"""Hourly scheduler job. Returns the number of intervals closed."""
	settings = get_settings()
	limit_hours = cint(settings.get("auto_close_after_hours")) or 14
	now = now_datetime()
	cutoff = now - timedelta(hours=limit_hours)

	candidates = frappe.get_all(
		"Job Interval",
		filters={"status": ["in", ["Open", "Paused"]], "start_time": ["<", cutoff]},
		fields=["name"],
		order_by="start_time asc",
		limit=200,
	)
	closed = 0
	for row in candidates:
		try:
			if _close_one(row.name, limit_hours, now, settings):
				closed += 1
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Time Kiosk sweeper: {row.name}")
	return closed


def _latest_fix(job_interval):
	rows = frappe.db.sql(
		"""
		select max(timestamp) as ts
		from `tabTime Kiosk Log`
		where job_interval = %(name)s and log_status in ('Success', 'Low Accuracy')
		""",
		{"name": job_interval},
		as_dict=True,
	)
	return rows[0].ts if rows and rows[0].ts else None


def _close_one(name, limit_hours, now, settings):
	from erpnext_enhancements.api import time_kiosk

	doc = frappe.get_doc("Job Interval", name)
	if doc.status not in ("Open", "Paused"):
		return False

	end_time, reason = decide_end_time(
		doc.status, doc.start_time, doc.last_pause_time, _latest_fix(doc.name), limit_hours, now
	)

	# Stamp first. Everything below is derived from these five fields.
	doc.end_time = end_time
	doc.status = "Completed"
	doc.last_pause_time = None
	doc.auto_closed = 1
	doc.auto_close_reason = reason_text(reason, limit_hours)

	# Photo gate: stamp the verdict, never prompt (there is nobody to ask).
	status = photo_gate.resolve(doc, settings=settings)
	photo_gate.stamp(
		doc, status,
		skip_reason=_("Auto-closed after {0} h with no clock-out").format(limit_hours),
	)

	time_kiosk._stamp_tracking_health(doc, settings)
	doc.save(ignore_permissions=True)  # validate stamps the cost
	time_kiosk.sync_interval_to_timesheet(doc)

	_notify(doc, limit_hours)
	return True


# ------------------------------------------------------------------ email


def _notify(doc, limit_hours):
	"""Tell the employee and their supervisor. Never raises."""
	try:
		recipients = []
		user = frappe.db.get_value("Employee", doc.employee, "user_id")
		supervisor = frappe.db.get_value("Employee", doc.employee, "reports_to")
		supervisor_user = frappe.db.get_value("Employee", supervisor, "user_id") if supervisor else None
		for candidate in (user, supervisor_user):
			if not candidate:
				continue
			row = frappe.db.get_value("User", candidate, ["email", "enabled"], as_dict=True)
			if row and row.enabled and row.email and row.email not in recipients:
				recipients.append(row.email)
		if not recipients:
			return

		employee_name = frappe.db.get_value("Employee", doc.employee, "employee_name") or doc.employee
		project = frappe.db.get_value("Project", doc.project, "project_name") or doc.project
		body = (
			email_style.callout(
				_("{0}'s clock-in on {1} was still open {2} hours after it started, so it has been closed automatically.").format(
					employee_name, project, limit_hours
				),
				"warning",
			)
			+ email_style.kv([
				(_("Interval"), doc.name),
				(_("Started"), frappe.utils.format_datetime(doc.start_time)),
				(_("Closed at"), frappe.utils.format_datetime(doc.end_time)),
				(_("How the end time was chosen"), doc.auto_close_reason or ""),
			])
			+ email_style.p(
				_("If the real clock-out was different, file a correction from My Day in the kiosk and it will be reviewed.")
			)
			+ email_style.button(get_url_to_form("Job Interval", doc.name), _("Open the interval"))
		)
		frappe.sendmail(
			recipients=recipients,
			subject=_("Clock-in closed automatically: {0} on {1}").format(employee_name, project),
			message=email_style.wrap(body, title=_("Clock-in closed automatically"), eyebrow=_("Time Kiosk")),
			reference_doctype="Job Interval",
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(title=f"Time Kiosk sweeper email: {doc.name}")
