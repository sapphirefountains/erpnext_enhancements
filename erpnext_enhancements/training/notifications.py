# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training emails (code-driven, ``frappe.sendmail``) — assigned / due soon /
overdue / escalated.

Architecture follows ``travel_management/notifications.py``: delivery runs in
background jobs (``enqueue_after_commit``) so a slow SMTP can never block or roll
back a save, per-recipient failures are logged and skipped, and every emailed user
also gets a Notification Log entry for the in-app audit trail.

Why not Notification fixtures for these: the escalation recipient list is
*computed* (holders of a role, minus the learner, resolved to addresses), and the
due-soon nudge batches one email per learner covering several courses. The
Notification doctype only does role or field recipients, one document at a time —
it would send a technician four separate emails on the same morning.

The whole surface is gated by **Training Settings → Send Notifications**, off by
default, so a bulk first assignment does not surprise fifteen people the day the
module lands.
"""

import frappe
from frappe import _
from frappe.utils import cint, get_url, getdate, nowdate

from erpnext_enhancements.training.doctype.training_settings.training_settings import is_enabled


def _in_maintenance_context():
	"""True while migrating/installing/patching/importing — never email then."""
	flags = frappe.flags
	return bool(flags.in_migrate or flags.in_install or flags.in_patch or flags.in_import)


def _enabled():
	return not _in_maintenance_context() and is_enabled("notifications_enabled")


def _recipient(user):
	"""``{user, full_name, email}`` for a learner, or None when unreachable.

	Employees are resolved through the same address preference travel uses
	(``prefered_email`` → ``user_id`` → company → personal); a Website User has
	only their login address.
	"""
	if not user:
		return None
	info = frappe.db.get_value("User", user, ["full_name", "email", "enabled"], as_dict=True)
	if not info or not cint(info.enabled):
		return None

	email = info.email
	employee = frappe.db.get_value(
		"Employee",
		{"user_id": user},
		["prefered_email", "company_email", "personal_email"],
		as_dict=True,
	)
	if employee:
		email = employee.prefered_email or info.email or employee.company_email or employee.personal_email

	if not email:
		return None
	return frappe._dict(user=user, full_name=info.full_name or user, email=email)


def _send(recipient, subject, body_html, reference_name=None):
	"""One email + Notification Log, failure logged and swallowed.

	Swallowed on purpose: a bad address on one learner must not abort a nightly
	sweep across everyone else.
	"""
	if not recipient or not recipient.email:
		return False
	try:
		frappe.sendmail(
			recipients=[recipient.email],
			subject=subject,
			message=body_html,
			reference_doctype="Training Assignment" if reference_name else None,
			reference_name=reference_name,
		)
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"subject": subject,
				"email_content": body_html,
				"for_user": recipient.user,
				"type": "Alert",
				"document_type": "Training Assignment" if reference_name else None,
				"document_name": reference_name,
			}
		).insert(ignore_permissions=True)
		return True
	except Exception:
		frappe.log_error(
			title="Training notification failed",
			message=f"{reference_name or '-'} -> {recipient.email}\n{frappe.get_traceback()}",
		)
		return False


def _course_line(assignment):
	due = assignment.get("due_date")
	title = assignment.get("course_title") or assignment.get("course")
	if due:
		return f"<li><b>{frappe.utils.escape_html(title)}</b> — due {frappe.utils.formatdate(due)}</li>"
	return f"<li><b>{frappe.utils.escape_html(title)}</b></li>"


# ------------------------------------------------------------------ assigned


def notify_assigned(doc):
	"""Tell a learner they have been given a course. Enqueued, never inline."""
	if not _enabled():
		return
	frappe.enqueue(
		"erpnext_enhancements.training.notifications.send_assigned",
		queue="short",
		enqueue_after_commit=True,
		assignment=doc.name,
	)


def send_assigned(assignment):
	if not _enabled():
		return
	doc = frappe.db.get_value(
		"Training Assignment",
		assignment,
		["name", "user", "course", "course_title", "due_date", "status"],
		as_dict=True,
	)
	if not doc or doc.status in ("Waived", "Cancelled", "Completed"):
		return

	recipient = _recipient(doc.user)
	if not recipient:
		return

	title = doc.course_title or doc.course
	due = (
		_(" It is due by {0}.").format(frappe.utils.formatdate(doc.due_date))
		if doc.due_date
		else ""
	)
	body = f"""
		<p>Hi {frappe.utils.escape_html(recipient.full_name)},</p>
		<p>You have been assigned the training <b>{frappe.utils.escape_html(title)}</b>.{due}</p>
		<p><a href="{get_url('/training')}">Open your training</a></p>
	"""
	_send(recipient, _("Training assigned: {0}").format(title), body, doc.name)


# ------------------------------------------------------------------ reminders


def send_due_digest(user, assignments):
	"""One email per learner covering everything due or overdue.

	Batched rather than one-per-assignment: a technician who owes four courses
	should get one email on a Monday morning, not four.
	"""
	if not _enabled():
		return
	recipient = _recipient(user)
	if not recipient:
		return

	overdue = [a for a in assignments if a.get("due_date") and getdate(a["due_date"]) < getdate(nowdate())]
	upcoming = [a for a in assignments if a not in overdue]

	parts = [f"<p>Hi {frappe.utils.escape_html(recipient.full_name)},</p>"]
	if overdue:
		parts.append("<p>These are now <b>overdue</b>:</p><ul>")
		parts.extend(_course_line(a) for a in overdue)
		parts.append("</ul>")
	if upcoming:
		parts.append("<p>These are coming up:</p><ul>")
		parts.extend(_course_line(a) for a in upcoming)
		parts.append("</ul>")
	parts.append(f'<p><a href="{get_url("/training")}">Open your training</a></p>')

	subject = (
		_("Training overdue ({0})").format(len(overdue))
		if overdue
		else _("Training due soon ({0})").format(len(upcoming))
	)
	_send(recipient, subject, "".join(parts))


# ---------------------------------------------------------------- escalation


def send_escalation(role, rows):
	"""Tell the escalation role who is overdue.

	The learner is deliberately excluded from this list even if they hold the
	role — they have already had their own reminder, and receiving an escalation
	about yourself reads as a reprimand rather than a nudge.
	"""
	if not _enabled() or not role or not rows:
		return

	learners = {row.get("user") for row in rows}
	for user in frappe.get_all(
		"Has Role",
		filters={"role": role, "parenttype": "User"},
		pluck="parent",
		distinct=True,
	):
		if user in learners:
			continue
		recipient = _recipient(user)
		if not recipient:
			continue

		body = [
			f"<p>Hi {frappe.utils.escape_html(recipient.full_name)},</p>",
			"<p>The following required training is overdue:</p><ul>",
		]
		for row in rows:
			who = frappe.utils.escape_html(row.get("learner_name") or row.get("user"))
			what = frappe.utils.escape_html(row.get("course_title") or row.get("course"))
			body.append(f"<li>{who} — {what} (due {frappe.utils.formatdate(row.get('due_date'))})</li>")
		body.append("</ul>")

		_send(recipient, _("Overdue training ({0})").format(len(rows)), "".join(body))
