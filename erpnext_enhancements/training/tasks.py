# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Scheduled Training jobs — due nudges and overdue escalation.

Both are gated on ``Training Settings`` and both batch by learner rather than by
assignment: someone who owes four courses gets one email, not four. That is the
difference between a reminder people read and a reminder people filter.

The reminder job runs at 07:15 site time, deliberately *after* the 06:00
technician dispatch digest, so a tech opening their phone finds two clearly
separated emails rather than two competing ones in the same minute.
"""

import frappe
from frappe.utils import add_days, cint, getdate, nowdate, today

from erpnext_enhancements.training import notifications
from erpnext_enhancements.training.doctype.training_assignment.training_assignment import (
	OPEN_STATUSES,
)
from erpnext_enhancements.training.doctype.training_settings.training_settings import is_enabled

# How far ahead a "due soon" nudge looks, and how long to wait before nudging the
# same person again. Three days stops a fortnight-long assignment generating a
# fortnight of identical emails.
DUE_SOON_DAYS = 3
REMINDER_COOLDOWN_DAYS = 3


def send_due_reminders():
	"""One digest per learner covering everything due soon or already overdue."""
	if not is_enabled("notifications_enabled"):
		return

	horizon = add_days(nowdate(), DUE_SOON_DAYS)
	rows = frappe.get_all(
		"Training Assignment",
		filters={
			"status": ["in", OPEN_STATUSES],
			"due_date": ["<=", horizon],
		},
		fields=["name", "user", "course", "course_title", "due_date", "last_reminder_on"],
	)

	by_user = {}
	for row in rows:
		if _reminded_recently(row.last_reminder_on):
			continue
		by_user.setdefault(row.user, []).append(row)

	for user, assignments in by_user.items():
		notifications.send_due_digest(user, assignments)
		for row in assignments:
			# Stamped whether or not the email landed. A permanently unreachable
			# address must not make this job re-send to everyone else every night.
			frappe.db.set_value(
				"Training Assignment", row["name"], "last_reminder_on", today(), update_modified=False
			)

	frappe.db.commit()


def escalate_overdue_assignments():
	"""Tell the escalation role about assignments that have stayed overdue."""
	if not is_enabled("overdue_escalation_enabled"):
		return

	default_after = cint(
		frappe.db.get_single_value("Training Settings", "default_escalate_after_days")
	) or 7
	default_role = frappe.db.get_single_value("Training Settings", "escalation_role")

	rows = frappe.get_all(
		"Training Assignment",
		filters={"status": "Overdue", "escalated_on": ["is", "not set"]},
		fields=["name", "user", "employee", "course", "course_title", "due_date"],
	)

	by_role = {}
	for row in rows:
		course = frappe.db.get_value(
			"Training Course", row.course, ["escalate_after_days", "escalation_role"], as_dict=True
		)
		if not course:
			continue

		after = cint(course.escalate_after_days) or default_after
		if getdate(row.due_date) > getdate(add_days(nowdate(), -after)):
			continue

		role = course.escalation_role or default_role
		if not role:
			# Nothing to escalate to. Skipping silently would look like the feature
			# is off rather than misconfigured, so say so once per run.
			frappe.log_error(
				f"{row.course} is overdue for {row.user} but neither the course nor Training Settings "
				f"names an escalation role.",
				"Training escalation",
			)
			continue

		row["learner_name"] = frappe.db.get_value("User", row.user, "full_name") or row.user
		by_role.setdefault(role, []).append(row)

	for role, items in by_role.items():
		notifications.send_escalation(role, items)
		for row in items:
			frappe.db.set_value(
				"Training Assignment", row["name"], "escalated_on", today(), update_modified=False
			)

	frappe.db.commit()


def refresh_overdue_status():
	"""Move assignments past their due date into Overdue.

	A separate pass rather than something the reminder job does as a side effect,
	because the status has to be right whether or not notifications are switched
	on — reports and the compliance warning both read it.
	"""
	if not is_enabled():
		return

	stale = frappe.get_all(
		"Training Assignment",
		filters={
			"status": ["in", ("Not Started", "In Progress")],
			"due_date": ["<", nowdate()],
		},
		pluck="name",
	)
	for name in stale:
		frappe.db.set_value("Training Assignment", name, "status", "Overdue", update_modified=False)
	if stale:
		frappe.db.commit()


# ---------------------------------------------------------------------- helpers


def _reminded_recently(last_reminder_on):
	if not last_reminder_on:
		return False
	return getdate(last_reminder_on) > getdate(add_days(nowdate(), -REMINDER_COOLDOWN_DAYS))
