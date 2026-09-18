"""
Workforce Approval Gate.

Why the gate is ours:
Verified on production 2026-09-18, a Custom DocPerm on Timesheet replaces the standard set
wholesale, and in force it grants submit/cancel/amend to `Employee Self Service` (14 holders)
and `Projects User`, while `Accounts Manager`, `HR Manager`, `Projects Manager` and `System Manager`
cannot submit at all. So the Desk permission is both too wide and too narrow, and inheriting it
would mean a technician could lock or reopen their own approved time.
"""

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate

#: Roles that may approve (and reopen) a day's time. Deliberately OUR list rather
#: than Timesheet's DocPerm -- see the module docstring.
APPROVER_ROLES = (
	"Finance Team",
	"Executive Team",
	"HR Manager",
	"System Manager",
	"Operations Manager",
	"Production Manager",
)


def is_approver(user=None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	user_roles = frappe.get_roles(user)
	return bool(set(APPROVER_ROLES).intersection(user_roles))


def assert_approver(user=None) -> None:
	if not is_approver(user):
		frappe.throw(_("Not permitted to approve or reopen time."), frappe.PermissionError)


def approve_day(employee, date) -> dict:
	assert_approver()
	
	target_date = getdate(date)
	start_of_day = get_datetime(target_date).strftime("%Y-%m-%d 00:00:00")
	end_of_day = get_datetime(target_date).strftime("%Y-%m-%d 23:59:59.999999")

	# Check for open/paused intervals
	unfinished = frappe.get_all(
		"Job Interval",
		filters={
			"employee": employee,
			"start_time": ["between", [start_of_day, end_of_day]],
			"status": ["in", ["Open", "Paused"]],
		},
		fields=["name"]
	)
	if unfinished:
		unfinished_names = [d.name for d in unfinished]
		frappe.throw(_("Cannot approve day. The following intervals are still open or paused: {0}").format(", ".join(unfinished_names)))

	# Check for missing time_category
	no_category = frappe.get_all(
		"Job Interval",
		filters={
			"employee": employee,
			"start_time": ["between", [start_of_day, end_of_day]],
			"time_category": ["in", ["", None]],
		},
		fields=["name"]
	)
	if no_category:
		no_category_names = [d.name for d in no_category]
		frappe.throw(_("Cannot approve day. The following intervals are missing an activity type: {0}").format(", ".join(no_category_names)))

	# Find Draft Timesheets
	timesheets = frappe.get_all(
		"Timesheet",
		filters={
			"employee": employee,
			"start_date": ["<=", target_date],
			"end_date": [">=", target_date],
			"docstatus": 0
		},
		fields=["name"]
	)

	submitted = []
	for ts in timesheets:
		doc = frappe.get_doc("Timesheet", ts.name)
		# APPROVER_ROLES is the gate, NOT Timesheet's DocPerm -- and without this
		# flag the DocPerm is the gate anyway, which would defeat the whole point.
		#
		# Verified on production 2026-09-18: a Custom DocPerm on Timesheet replaces
		# the standard set wholesale, and the set in force grants submit to
		# Employee Self Service, Accounts User, HR User, Manufacturing User and
		# Projects User -- and to NONE of the six roles above. Five of the six
		# approvers would have been refused by a permission check that was never
		# meant to apply to them, and the sixth would have succeeded by accident.
		#
		# submit() takes no ignore_permissions argument; the flag is how it is done.
		doc.flags.ignore_permissions = True
		doc.submit()
		submitted.append(ts.name)

	return {
		"status": "success",
		"timesheets": submitted,
		"message": _("Approved {0} timesheet(s) for {1}").format(len(submitted), date)
	}


def reopen_day(employee, date, reason=None) -> dict:
	assert_approver()

	target_date = getdate(date)

	# Find Submitted Timesheets
	timesheets = frappe.get_all(
		"Timesheet",
		filters={
			"employee": employee,
			"start_date": ["<=", target_date],
			"end_date": [">=", target_date],
			"docstatus": 1
		},
		fields=["name"]
	)

	cancelled = []
	for ts in timesheets:
		doc = frappe.get_doc("Timesheet", ts.name)
		# Same reasoning as approve_day: our role set is the gate, so the DocPerm
		# must not be. An approver who could lock a day but not reopen it would be
		# the worst of both — a one-way door nobody intended to build.
		doc.flags.ignore_permissions = True
		doc.cancel()
		if reason:
			frappe.get_doc({
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": "Timesheet",
				"reference_name": ts.name,
				"content": f"Reopened day. Reason: {reason}"
			}).insert(ignore_permissions=True)
		cancelled.append(ts.name)

	return {
		"status": "success",
		"timesheets": cancelled,
		"message": _("Reopened {0} timesheet(s) for {1}").format(len(cancelled), date)
	}
