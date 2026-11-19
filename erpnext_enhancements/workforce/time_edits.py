# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Approval of time an employee changed or entered by hand.

A Job Interval whose times were changed by the employee it belongs to, or that was
added by hand (``api.time_kiosk.add_manual_interval`` and ``start_backdated``), is
marked ``Waiting`` in ``edit_approval`` and stays out of payroll until somebody else
approves it:

* ``payroll_export`` leaves it out of every total and lists it on its own sheet;
* ``api.time_kiosk.sync_interval_to_timesheet`` writes no Timesheet line for it, so it
  is not submitted with the day and does not reach a project's labour actuals.

Who decides is the correction review's rule, ``permissions.can_review`` (the manager
roles, or the employee's ``reports_to`` user), and never the interval's own employee
or the person who made the edit. An edit by such a reviewer to another person's time
needs no approval; if that interval was waiting, the reviewer's edit settles it as
approved, since the reviewer set the times.

States: blank, ``Waiting``, ``Approved``, ``Sent Back``. Blank is every interval that
was never edited this way, which includes every row that existed before the field did,
and blank counts toward payroll as it always has. ``Sent Back`` restores nothing: the
interval stays out until it is edited again (``Waiting`` once more) or approved.

Only the edit endpoints call ``record_edit``. A clock-out, the sweeper's auto-close and
an approved Time Correction Request are not hand edits and leave the state alone.
"""

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from erpnext_enhancements.workforce import permissions

WAITING = "Waiting"
APPROVED = "Approved"
SENT_BACK = "Sent Back"

#: States that keep an interval out of payroll and off the Timesheet.
HELD_STATES = (WAITING, SENT_BACK)

#: ``sync_status`` of a held interval. Not ``Pending``, so nothing that picks up
#: Completed + Pending rows for a Timesheet takes it either.
HELD_SYNC_STATUS = "Held - Not Approved"


def state(doc):
	"""The interval's ``edit_approval``, blank when unset. Works on a Document or a row."""
	return (getattr(doc, "edit_approval", None) or "").strip()


def is_held(doc):
	return state(doc) in HELD_STATES


def session_employee(user=None):
	return frappe.db.get_value("Employee", {"user_id": user or frappe.session.user}, "name")


def needs_approval(employee, user=None):
	"""Whether an edit by ``user`` to ``employee``'s time waits for somebody else.

	Always for one's own time, reviewer or not. For another person's time, unless
	``user`` may review that person's time corrections.
	"""
	user = user or frappe.session.user
	own = session_employee(user)
	if own and own == employee:
		return True
	return not permissions.can_review(employee, user)


def same_minute(a, b):
	"""The kiosk's time inputs have minute resolution, so a value sent back unchanged
	arrives with its seconds dropped. Equal to the minute is unchanged."""
	if not a or not b:
		return not a and not b
	a, b = get_datetime(a), get_datetime(b)
	return a.replace(second=0, microsecond=0) == b.replace(second=0, microsecond=0)


def record_edit(doc, added=False, user=None):
	"""Mark ``doc`` for an edit by ``user`` of its times, or for its entry by hand
	(``added``). Call it BEFORE the new times are written: the times before the edit
	are kept from here. Returns True when the interval now waits for approval.

	The times before are kept once per waiting spell, so a second edit before anyone
	has decided still shows the change from the last approved times.
	"""
	user = user or frappe.session.user
	now = now_datetime()
	if needs_approval(doc.employee, user):
		if not is_held(doc):
			doc.edit_previous_start = None if added else doc.start_time
			doc.edit_previous_end = None if added else doc.end_time
		doc.edit_approval = WAITING
		doc.edited_by = user
		doc.edited_on = now
		doc.edit_reviewed_by = None
		doc.edit_reviewed_on = None
		doc.edit_review_note = None
		return True
	if is_held(doc):
		doc.edit_approval = APPROVED
		doc.edit_reviewed_by = user
		doc.edit_reviewed_on = now
	return False


def may_decide(doc, user=None):
	"""May ``user`` approve or send back ``doc``'s edit?"""
	user = user or frappe.session.user
	if not permissions.can_review(doc.employee, user):
		return False
	own = session_employee(user)
	if own and own == doc.employee:
		return False
	return (getattr(doc, "edited_by", None) or "") != user


def _require_decider(doc):
	if not may_decide(doc):
		frappe.throw(
			_("This edit can be decided only by a reviewer who is neither the employee nor the person who made it."),
			frappe.PermissionError,
		)


def _resync(doc):
	"""Rewrite the interval's Timesheet line from its current state (none while held)."""
	from erpnext_enhancements.api.time_kiosk import resync_interval_timesheet

	return resync_interval_timesheet(doc)


def _stamp_review(doc, note):
	doc.edit_reviewed_by = frappe.session.user
	doc.edit_reviewed_on = now_datetime()
	doc.edit_review_note = (note or "").strip() or None


@frappe.whitelist(methods=["POST"])
def approve_time_edit(interval, note=None):
	"""Approve a waiting (or sent back) edit: the interval counts again and its
	Timesheet line is written."""
	doc = frappe.get_doc("Job Interval", interval)
	_require_decider(doc)
	if not is_held(doc):
		frappe.throw(_("{0} is not waiting for approval.").format(doc.name))

	doc.edit_approval = APPROVED
	_stamp_review(doc, note)
	if (getattr(doc, "sync_status", None) or "") == HELD_SYNC_STATUS:
		doc.sync_status = "Pending"
	doc.save(ignore_permissions=True)
	_resync(doc)
	return {"status": doc.edit_approval, "interval": doc.name}


@frappe.whitelist(methods=["POST"])
def send_back_time_edit(interval, note=None):
	"""Send a waiting edit back. Restores nothing: the interval stays out of payroll
	and off the Timesheet, and the employee is told."""
	doc = frappe.get_doc("Job Interval", interval)
	_require_decider(doc)
	if state(doc) != WAITING:
		frappe.throw(_("{0} is not waiting for approval.").format(doc.name))

	doc.edit_approval = SENT_BACK
	_stamp_review(doc, note)
	doc.save(ignore_permissions=True)
	# Takes off any line the interval still has, and writes none while it is held.
	_resync(doc)
	notify_sent_back(doc)
	return {"status": doc.edit_approval, "interval": doc.name}


# ------------------------------------------------------------------ describing a change


def _fmt(value):
	return get_datetime(value).strftime("%Y-%m-%d %H:%M") if value else _("open")


def describe_change(row):
	"""One line for a person reading a list: what this edit changed."""
	before_start = getattr(row, "edit_previous_start", None)
	before_end = getattr(row, "edit_previous_end", None)
	if not before_start and not before_end:
		return _("Added by hand")
	parts = []
	if not same_minute(before_start, getattr(row, "start_time", None)):
		parts.append(_("Start {0} to {1}").format(_fmt(before_start), _fmt(getattr(row, "start_time", None))))
	if not same_minute(before_end, getattr(row, "end_time", None)):
		parts.append(_("End {0} to {1}").format(_fmt(before_end), _fmt(getattr(row, "end_time", None))))
	return "; ".join(parts) or _("Times changed")


# ------------------------------------------------------------------ email


def notify_sent_back(doc):
	"""Tell the employee their entry was sent back. Never raises."""
	from erpnext_enhancements import email_style

	try:
		user = frappe.db.get_value("Employee", doc.employee, "user_id")
		if not user:
			return False
		row = frappe.db.get_value("User", user, ["email", "enabled"], as_dict=True)
		if not row or not row.enabled or not row.email:
			return False
		body = email_style.callout(
			_("Your time entry {0} was sent back. It is not counted toward payroll.").format(doc.name), "warning"
		) + email_style.kv([
			(_("Entry"), doc.name),
			(_("Start"), _fmt(doc.start_time)),
			(_("End"), _fmt(doc.end_time)),
			(_("What changed"), describe_change(doc)),
		])
		if doc.edit_review_note:
			body += email_style.p(_("Note from the reviewer:")) + email_style.prose(doc.edit_review_note)
		body += email_style.note(
			_("Edit the entry again from My Day in the Time Kiosk, or file a correction request.")
		)
		frappe.sendmail(
			recipients=[row.email],
			subject=_("Time entry {0}: sent back").format(doc.name),
			message=email_style.wrap(body, title=_("Time entry sent back"), eyebrow=_("Time Kiosk")),
			reference_doctype="Job Interval",
			reference_name=doc.name,
		)
		return True
	except Exception:
		frappe.log_error(title=f"Time edit email failed: {getattr(doc, 'name', '')}")
		return False
