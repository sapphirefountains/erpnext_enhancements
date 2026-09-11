# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""New-hire onboarding: raise a checklist when somebody is added.

The one thing that most obviously belongs in an HR module and most obviously did
not exist. `hrms` has an Employee Onboarding doctype and `hrms` is not installed;
the trigger point, though, already existed — `hooks.py` fires
`training.assignment.on_employee_insert` on Employee `after_insert`, and this
joins that call rather than adding a second Employee hook whose ordering nobody
declared.

**The default list is a constant here, for now.** Once a checklist is raised the
items live on the row and are edited there, which is where the editing actually
happens — the default only decides what a *new* hire starts with. If that list
ever needs changing without a deploy it becomes a DocType, and `default_items()`
is the single seam that would change.

Deliberately not a task-assignment system. Every item's owner is a plain string,
because half of them are done by whoever is free that morning, and requiring an
assignee is exactly how a checklist stops getting filled in. What matters is that
somebody can see what has not happened yet, not that the system knows whose fault
it is.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, today

CHECKLIST = "Onboarding Checklist"

JOINING = "Joining"
LEAVING = "Leaving"

#: The starting list. There is no template DocType yet, so this IS the source --
#: an earlier comment here described one that was never built, which the branch
#: review caught. Editing a checklist edits the row, not this; the list is only
#: ever read once, when the checklist is first raised.
FALLBACK_ITEMS = (
	("Paperwork signed and filed", "HR", 0),
	("Payroll details collected", "HR", 0),
	("Email and ERPNext account created", "Internal Systems", 0),
	("Phone or tablet issued", "Internal Systems", 1),
	("PPE issued (boots, gloves, hi-vis, eye protection)", "Shop", 1),
	("Site safety orientation", "Supervisor", 1),
	("Required training assigned", "HR", 2),
	("First-week ride-along booked", "Supervisor", 5),
)


def on_employee_insert(doc, method=None):
	"""Raise a checklist for a new hire. Never raises, never blocks the insert.

	An Employee record failing to save because a checklist could not be built
	would be the tail wagging the dog — and this fires inside the same
	`after_insert` as the training assignment, which has the same contract for the
	same reason.
	"""
	if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
		return
	try:
		ensure_checklist(doc.name)
	except Exception:
		frappe.log_error(
			f"Could not raise an onboarding checklist for {doc.name}\n{frappe.get_traceback()}",
			"HR onboarding",
		)


def ensure_checklist(employee, kind=JOINING):
	"""Create the checklist for one employee unless they already have one of *kind*.

	Idempotent on ``(employee, kind)``. That is the whole reason the DocField
	`unique` came off `employee` when leaving was added: a person joins once and
	leaves once, and a single-column unique index cannot say that. Checked here
	because this is reachable from a doc_event, from a patch and from a button.
	"""
	if not frappe.db.exists("DocType", CHECKLIST):
		return None
	existing = frappe.db.exists(CHECKLIST, {"employee": employee, "kind": kind})
	if existing:
		return existing

	row = frappe.db.get_value(
		"Employee", employee, ["date_of_joining", "relieving_date", "status"], as_dict=True
	) or frappe._dict()

	if kind == JOINING:
		if row.get("status") and row.status != "Active":
			# Somebody being backfilled after they left does not need a first week.
			return None
		starts_on = row.get("date_of_joining") or today()
		items = [
			{"task": task, "owner_role": owner, "due_offset_days": cint(offset)}
			for task, owner, offset in default_items()
		]
	else:
		starts_on = row.get("relieving_date") or today()
		items = leaving_items(employee)

	doc = frappe.get_doc(
		{
			"doctype": CHECKLIST,
			"kind": kind,
			"employee": employee,
			"naming_series": "HR-ONB-.#####" if kind == JOINING else "HR-OFF-.#####",
			"starts_on": starts_on,
			"items": items,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def default_items():
	"""The list a new checklist starts from.

	A seam, kept deliberately: the moment somebody wants to edit the default list
	without a deploy, this is the one function that changes, and every caller
	already goes through it.
	"""
	return FALLBACK_ITEMS


def due_date(checklist, item):
	"""When one item is due — the checklist's start plus the item's offset."""
	start = getdate(checklist.starts_on or today())
	return add_days(start, cint(item.due_offset_days))


# --------------------------------------------------------------------- leaving


#: The fixed part of a leaving checklist: things ERPNext cannot see and cannot
#: switch off. Same shape as the joining list, and the same rule about owners --
#: a plain word, because half of a last week is done by whoever is free.
LEAVING_ITEMS = (
	("Truck keys and fuel card back", "Shop", 0),
	("Phone and any company kit back", "Shop", 0),
	("Customer gate codes and site keys back", "Supervisor", 0),
	("QuickBooks Online access removed", "Finance", 1),
	("QuickBooks Time access removed", "Finance", 1),
	("Google account suspended", "HR", 1),
	("Final timesheet approved", "Supervisor", 3),
)


def leaving_items(employee):
	"""The fixed list, plus everything this person is actually holding.

	**Generated rather than fixed, and that is the whole point.** ERPNext disables
	the login by itself and does nothing else; everything that matters on the day
	after somebody leaves — the device in their van, the credential the insurer
	asked about, the four jobs assigned to them, the two people who report to them
	— is invisible unless something goes and looks. A fixed checklist cannot know
	any of it, so it gets filled in from memory, which is the failure this exists
	to remove.

	Every lookup is best-effort. A missing module means fewer derived rows, never
	an exception: the fixed list alone is still worth raising.
	"""
	items = [
		{"task": task, "owner_role": owner, "due_offset_days": cint(offset)}
		for task, owner, offset in LEAVING_ITEMS
	]
	for task, owner, offset in _derived_leaving_rows(employee):
		items.append({"task": task, "owner_role": owner, "due_offset_days": cint(offset)})
	return items


def _derived_leaving_rows(employee):
	"""``(task, owner, offset)`` for each thing this person is holding."""
	rows = []
	user = frappe.db.get_value("Employee", employee, "user_id")

	rows.extend(_devices_held(employee))
	rows.extend(_credentials_held(employee))
	rows.extend(_open_work(user))
	rows.extend(_direct_reports(employee))
	rows.extend(_vehicles_held(employee))
	return rows


def _devices_held(employee):
	if not frappe.db.exists("DocType", "Managed Device"):
		return []
	try:
		return [
			(f"Collect {row.device_name or row.name}", "Shop", 0)
			for row in frappe.get_all(
				"Managed Device", filters={"employee": employee}, fields=["name", "device_name"]
			)
		]
	except Exception:
		return []


def _credentials_held(employee):
	if not frappe.db.exists("DocType", "Employee Credential"):
		return []
	try:
		return [
			(f"File a copy of {row.credential_type} before access goes", "HR", 0)
			for row in frappe.get_all(
				"Employee Credential",
				filters={"employee": employee, "status": ["in", ("Valid", "Expiring")]},
				fields=["credential_type"],
			)
		]
	except Exception:
		return []


def _open_work(user):
	"""Open training assignments. Reassign or waive -- not leave outstanding.

	An assignment against somebody who has left sits in the compliance figures for
	ever and quietly makes the numbers wrong.
	"""
	if not user or not frappe.db.exists("DocType", "Training Assignment"):
		return []
	try:
		count = frappe.db.count(
			"Training Assignment",
			{"user": user, "status": ["in", ("Assigned", "In Progress", "Awaiting Sign-off", "Overdue")]},
		)
		if not count:
			return []
		return [(f"Close or reassign {count} open training assignment(s)", "HR", 2)]
	except Exception:
		return []


def _direct_reports(employee):
	"""The one that is easiest to forget and hardest to notice.

	Somebody whose manager has left has no manager, and nothing says so: time-off
	requests route to an empty approver and simply never move.
	"""
	try:
		reports = frappe.get_all(
			"Employee", filters={"reports_to": employee, "status": "Active"}, pluck="employee_name"
		)
	except Exception:
		return []
	if not reports:
		return []
	return [
		(
			f"Re-point {len(reports)} direct report(s) at a new manager: {', '.join(reports[:5])}",
			"HR",
			1,
		)
	]


def _vehicles_held(employee):
	if not frappe.db.exists("DocType", "Fleet Vehicle"):
		return []
	try:
		return [
			(f"Reassign {row.vehicle_name or row.name}", "Shop", 0)
			for row in frappe.get_all(
				"Fleet Vehicle",
				filters={"assigned_driver": employee, "status": ["!=", "Retired"]},
				fields=["name", "vehicle_name"],
			)
		]
	except Exception:
		return []


def on_employee_update(doc, method=None):
	"""Raise the leaving checklist when somebody's status flips to Left.

	Gated on the TRANSITION rather than on the current value, because Employee is
	saved often and an unguarded check would try on every save. Never raises: an
	Employee record failing to save because a checklist could not be built is the
	tail wagging the dog, the same contract as the joining handler.
	"""
	if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
		return
	try:
		if (doc.status or "") != "Left":
			return
		before = doc.get_doc_before_save()
		if before and (before.status or "") == "Left":
			return
		ensure_checklist(doc.name, LEAVING)
	except Exception:
		frappe.log_error(
			f"Could not raise a leaving checklist for {doc.name}\n{frappe.get_traceback()}",
			"HR offboarding",
		)


# ---------------------------------------------------------- 30 / 60 / 90 days


#: Days after joining at which a supervisor gets a nudge.
CHECK_IN_DAYS = (30, 60, 90)


def nudge_new_hire_check_ins():
	"""Three emails to a new hire's supervisor, and **no record at all**.

	That is the design rather than an omission. The value here is the prompt — go
	and ask them how it is going — and a form attached to it turns a two-minute
	conversation into an admin task, which is how the conversation stops happening.
	Nothing is stored, nothing is ticked, and nobody is chased.

	Idempotent by arithmetic rather than by a flag: it fires only on the exact day,
	so a sweep that runs twice in a day sends twice and a sweep that misses a day
	misses that one. Both are acceptable for a nudge and neither needs a table.

	Never raises.
	"""
	try:
		if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
			return 0
		from frappe.utils import date_diff

		from erpnext_enhancements.training import notifications

		sent = 0
		now = getdate(today())
		for row in frappe.get_all(
			"Employee",
			filters={"status": "Active", "date_of_joining": ["is", "set"]},
			fields=["name", "employee_name", "date_of_joining", "reports_to"],
		):
			days = date_diff(now, getdate(row.date_of_joining))
			if days not in CHECK_IN_DAYS:
				continue
			manager_user = (
				frappe.db.get_value("Employee", row.reports_to, "user_id") if row.reports_to else None
			)
			if not manager_user:
				continue
			recipient = notifications._recipient(manager_user)
			if not recipient:
				continue
			if notifications._send(
				recipient,
				_("{0} — {1} days in").format(row.employee_name, days),
				_(
					"<p><b>{0}</b> has been here {1} days.</p><p>Go and ask them how it is going. "
					"There is nothing to fill in — this is a reminder, not a form.</p>"
				).format(row.employee_name, days),
			):
				sent += 1
		return sent
	except Exception:
		frappe.log_error(
			f"New-hire check-in nudge failed\n{frappe.get_traceback()}", "HR onboarding"
		)
		return 0
