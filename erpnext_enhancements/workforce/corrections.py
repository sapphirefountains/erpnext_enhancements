# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Reviewing a Time Correction Request: approve, decline, apply, re-sync, tell people.

A Job Interval is what payroll is built from, so nobody edits one by hand. A technician
files a ``Time Correction Request`` from the kiosk (``api.time_kiosk.submit_correction_request``)
and somebody who is allowed to decide — HR Manager, Projects Manager, System Manager, or
the employee's own ``reports_to`` user, who need hold none of those roles — approves or
declines it here. The desk form's buttons call the two whitelisted functions below.

What an approval does, in order:

1. **Refuses if the interval's Timesheet line is on a SUBMITTED Timesheet.** A submitted
   Timesheet is an accounting document; the message says to cancel or amend it first,
   rather than silently leaving the Timesheet and the interval disagreeing.
2. Records ``original_start_time`` / ``original_end_time`` / ``original_project`` on the
   interval — the **first** correction only; later ones leave the originals alone, so the
   trail from "what the kiosk recorded" to "what payroll saw" is one hop.
3. Applies the proposal. A ``Missed Entry`` creates a new **Completed** interval; a
   ``Missed Clock-Out`` closes the open one, stamping the photo gate the way the sweeper
   does (``photo_gate.resolve`` — there is nobody to prompt).
4. Saves — the interval's own ``validate`` recomputes ``labor_cost`` — recomputes tracking
   health, and re-syncs the Timesheet line (``api.time_kiosk.resync_interval_timesheet``).
5. Emails the technician. Declines email too. Filing one emails the supervisor.

Every email goes through ``email_style.wrap`` — never a container or max-width div of
this module's own; ``tests/test_email_design.py`` fails the build on one.
"""

import frappe
from frappe import _
from frappe.utils import get_datetime, get_url_to_form, now_datetime

from erpnext_enhancements import email_style
from erpnext_enhancements.workforce import permissions, photo_gate

TCR = "Time Correction Request"


# ------------------------------------------------------------------ gates


def _load(name):
	doc = frappe.get_doc(TCR, name)
	if doc.status != "Requested":
		frappe.throw(_("{0} has already been {1}.").format(doc.name, doc.status.lower()))
	return doc


def _require_reviewer(doc):
	if not permissions.can_review(doc.employee, frappe.session.user):
		frappe.throw(
			_("Only HR, a Projects Manager, or {0}'s supervisor can decide this request.").format(
				doc.employee_name or doc.employee
			),
			frappe.PermissionError,
		)


def _refuse_if_timesheet_submitted(interval):
	from erpnext_enhancements.api.time_kiosk import find_timesheet_line

	line = find_timesheet_line(interval)
	if line and int(line.docstatus or 0) == 1:
		frappe.throw(
			_(
				"The hours for {0} are already on submitted Timesheet {1}. Cancel or amend that Timesheet first, then approve this request."
			).format(interval.name, line.parent),
			title=_("Timesheet already submitted"),
		)


# ------------------------------------------------------------------ endpoints


@frappe.whitelist()
def approve_request(name, note=None):
	"""Approve and apply. Returns ``{"status", "applied_interval"}``."""
	doc = _load(name)
	_require_reviewer(doc)

	interval = apply_request(doc)

	doc.flags.ee_review = True
	doc.status = "Approved"
	doc.reviewer = frappe.session.user
	doc.reviewed_on = now_datetime()
	doc.review_note = (note or "").strip() or None
	doc.applied_interval = interval.name
	doc.save(ignore_permissions=True)

	_notify_employee(doc, approved=True)
	return {"status": doc.status, "applied_interval": interval.name}


@frappe.whitelist()
def decline_request(name, note=None):
	"""Decline. Same gate; nothing on the interval changes."""
	doc = _load(name)
	_require_reviewer(doc)

	doc.flags.ee_review = True
	doc.status = "Declined"
	doc.reviewer = frappe.session.user
	doc.reviewed_on = now_datetime()
	doc.review_note = (note or "").strip() or None
	doc.save(ignore_permissions=True)

	_notify_employee(doc, approved=False)
	return {"status": doc.status}


# ------------------------------------------------------------------ apply


def apply_request(doc):
	"""Apply ``doc``'s proposal to its interval (or create one). Returns the
	Job Interval document that now carries the correction."""
	from erpnext_enhancements.api import time_kiosk

	if doc.request_type == "Missed Entry":
		interval = _create_missed_entry(doc)
	else:
		interval = frappe.get_doc("Job Interval", doc.job_interval)
		if interval.employee != doc.employee:
			frappe.throw(_("{0} belongs to a different employee.").format(interval.name), frappe.PermissionError)
		_refuse_if_timesheet_submitted(interval)
		_record_originals(interval)

		if doc.request_type == "Adjust Times":
			interval.start_time = get_datetime(doc.proposed_start)
			interval.end_time = get_datetime(doc.proposed_end)
			if interval.status != "Completed":
				_close_without_prompt(interval, doc)
		elif doc.request_type == "Change Project":
			interval.project = doc.proposed_project
			interval.task = doc.proposed_task or None
			if doc.proposed_activity:
				interval.time_category = doc.proposed_activity
		elif doc.request_type == "Missed Clock-Out":
			interval.end_time = get_datetime(doc.proposed_end)
			_close_without_prompt(interval, doc)

		interval.corrected = 1
		interval.correction_request = doc.name
		time_kiosk._stamp_tracking_health(interval)
		interval.save(ignore_permissions=True)

	# Save ran validate, which stamped the cost. Now the Timesheet line.
	time_kiosk.resync_interval_timesheet(interval)
	return interval


def _record_originals(interval):
	"""First correction only: keep what the kiosk recorded."""
	if getattr(interval, "corrected", None):
		return
	interval.original_start_time = interval.start_time
	interval.original_end_time = interval.end_time
	interval.original_project = interval.project


def _close_without_prompt(interval, doc):
	"""Close an Open/Paused interval on the reviewer's say-so. A paused interval's
	pause is left as it was recorded; the proposed end is the end."""
	interval.status = "Completed"
	interval.last_pause_time = None
	status = photo_gate.resolve(interval)
	photo_gate.stamp(interval, status, skip_reason=_("Closed by correction request {0}").format(doc.name))


def _create_missed_entry(doc):
	interval = frappe.get_doc({
		"doctype": "Job Interval",
		"employee": doc.employee,
		"project": doc.proposed_project,
		"task": doc.proposed_task or None,
		"time_category": doc.proposed_activity or None,
		"start_time": get_datetime(doc.proposed_start),
		"end_time": get_datetime(doc.proposed_end),
		"status": "Completed",
		"total_paused_seconds": 0.0,
		"description": _("Missed entry added by correction request {0}").format(doc.name),
		"corrected": 1,
		"correction_request": doc.name,
		"tracking_health": "None",
		"tracking_coverage_pct": 0,
		"gap_minutes": 0,
		"fix_count": 0,
	})
	from erpnext_enhancements.workforce import costing

	costing.stamp_position(interval)
	photo_gate.stamp(interval, photo_gate.resolve(interval), skip_reason=_("Missed entry — no kiosk session"))
	interval.insert(ignore_permissions=True)
	return interval


# ------------------------------------------------------------------ email


def _user_email(user):
	if not user:
		return None
	row = frappe.db.get_value("User", user, ["email", "enabled"], as_dict=True)
	if not row or not row.enabled:
		return None
	return row.email


def _employee_user(employee):
	return frappe.db.get_value("Employee", employee, "user_id")


def _supervisor_user(employee):
	supervisor = frappe.db.get_value("Employee", employee, "reports_to")
	return frappe.db.get_value("Employee", supervisor, "user_id") if supervisor else None


def _send(recipient, subject, body_html, title, eyebrow):
	"""Send one email, never raise: the request is the record, the email is a courtesy."""
	email = _user_email(recipient)
	if not email:
		return False
	try:
		frappe.sendmail(
			recipients=[email],
			subject=subject,
			message=email_style.wrap(body_html, title=title, eyebrow=eyebrow),
			reference_doctype=TCR,
			reference_name=None,
		)
		return True
	except Exception:
		frappe.log_error(title=f"Time correction email failed: {subject}")
		return False


def _facts(doc):
	rows = [(_("Request"), doc.name), (_("Type"), doc.request_type), (_("Employee"), doc.employee_name or doc.employee)]
	if doc.job_interval:
		rows.append((_("Interval"), doc.job_interval))
	if doc.proposed_start:
		rows.append((_("Proposed start"), frappe.utils.format_datetime(doc.proposed_start)))
	if doc.proposed_end:
		rows.append((_("Proposed end"), frappe.utils.format_datetime(doc.proposed_end)))
	if doc.proposed_project:
		title = frappe.db.get_value("Project", doc.proposed_project, "project_name") or doc.proposed_project
		rows.append((_("Proposed project"), title))
	return rows


def notify_new_request(doc):
	"""On insert: tell the employee's supervisor there is something to decide.
	Called from the controller's ``after_insert``; never raises."""
	try:
		supervisor = _supervisor_user(doc.employee)
		if not supervisor:
			return False
		body = (
			email_style.p(
				_("{0} has asked for a time correction and it is waiting for you.").format(
					doc.employee_name or doc.employee
				)
			)
			+ email_style.kv(_facts(doc))
			+ email_style.prose(doc.reason or "")
			+ email_style.button(get_url_to_form(TCR, doc.name), _("Review the request"))
		)
		return _send(
			supervisor,
			_("Time correction to review: {0}").format(doc.employee_name or doc.employee),
			body,
			_("Time correction request"),
			_("Time Kiosk"),
		)
	except Exception:
		frappe.log_error(title=f"notify_new_request {getattr(doc, 'name', '')}")
		return False


def _notify_employee(doc, approved):
	try:
		user = _employee_user(doc.employee)
		if not user:
			return False
		if approved:
			lead = _("Your time correction {0} was approved and applied.").format(doc.name)
			tone = "success"
		else:
			lead = _("Your time correction {0} was declined.").format(doc.name)
			tone = "warning"
		body = email_style.callout(lead, tone) + email_style.kv(_facts(doc))
		if doc.review_note:
			body += email_style.p(_("Note from the reviewer:")) + email_style.prose(doc.review_note)
		if approved and doc.applied_interval:
			body += email_style.note(_("Interval {0} now carries the corrected values.").format(doc.applied_interval))
		return _send(
			user,
			_("Time correction {0}: {1}").format(doc.name, _("approved") if approved else _("declined")),
			body,
			_("Time correction {0}").format(_("approved") if approved else _("declined")),
			_("Time Kiosk"),
		)
	except Exception:
		frappe.log_error(title=f"_notify_employee {getattr(doc, 'name', '')}")
		return False
