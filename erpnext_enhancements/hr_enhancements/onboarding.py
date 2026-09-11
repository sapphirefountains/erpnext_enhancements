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
from frappe.utils import add_days, cint, getdate, today

CHECKLIST = "Onboarding Checklist"

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


def ensure_checklist(employee):
	"""Create the checklist for one employee unless they already have one.

	Idempotent on the Employee, which is enforced by the unique field as well —
	belt and braces, because this is reachable from a doc_event, from a patch and
	from a button.
	"""
	if not frappe.db.exists("DocType", CHECKLIST):
		return None
	existing = frappe.db.exists(CHECKLIST, {"employee": employee})
	if existing:
		return existing

	row = frappe.db.get_value(
		"Employee", employee, ["date_of_joining", "status"], as_dict=True
	) or frappe._dict()
	if row.get("status") and row.status != "Active":
		# Somebody being backfilled after they left does not need a first week.
		return None

	doc = frappe.get_doc(
		{
			"doctype": CHECKLIST,
			"employee": employee,
			"starts_on": row.get("date_of_joining") or today(),
			"items": [
				{"task": task, "owner_role": owner, "due_offset_days": cint(offset)}
				for task, owner, offset in default_items()
			],
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
