# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Task crew <-> assignment sync for the Project Planner.

A Task's crew (`custom_crew`, rows of Task Crew Member) is what the Project Planner books hours
against. But the rest of the app -- the technician digest, ToDo lists, the morning briefing, the
sidebar "Assigned to" -- all read ordinary assignments (open ToDos). Rather than teach each of
them about crew rows, the crew is *mirrored* into assignments on save, so everything that already
works keeps working.

The mirror is deliberately one-sided and conservative:

* **Add** an assignment only for a user newly in the crew with no open ToDo on the task yet.
* **Remove** one only for a user who was in the crew *before this save* and is not now. The
  "before" comes from `doc.get_doc_before_save()`. A user who was assigned from the sidebar and
  never put on the crew is therefore never touched, and neither is anyone when the crew did not
  change.
* Crew rows with no user (a subcontractor has no login) are skipped; they are booked in the
  planner but cannot hold a ToDo.
* Finished tasks are skipped: re-saving a Completed task must not hand its crew new open ToDos,
  and cancelling one should not churn assignments the close-out already settled.

`frappe.desk.form.assign_to.add` in v16 has no `notify` switch, so a new assignee gets Frappe's
standard assignment notification, exactly as if someone had used the sidebar. It also reports a
duplicate through `msgprint` rather than skipping silently, which is why this module looks for an
open ToDo first instead of letting `add` discover it -- otherwise every save of a crewed task
would raise a toast.

`on_task_update` is best effort and never raises: a task must stay saveable even if an assignee is
disabled or a share fails. `validate_crew` tidies the table before it is stored.

`doc_events` fire during ERPNext's own test bootstrap, before this app's custom fields exist, so
every read of `custom_crew` goes through `getattr(doc, "custom_crew", None) or []`.
"""

import frappe
from frappe import _
from frappe.utils import flt

# Same tuple as task_enhancements/doctype/task/task.py. Redefined rather than imported because
# that module pulls in ERPNext's Task controller, and this one must stay importable without it.
FINISHED_STATUSES = ("Completed", "Canceled", "Cancelled", "Invoiced", "Template")


def _crew_rows(doc):
	return getattr(doc, "custom_crew", None) or []


def _crew_users(rows):
	"""The set of users on a crew, ignoring rows with none (subcontractors)."""
	return {row.get("user") for row in rows if row.get("user")}


def validate_crew(doc, method=None):
	"""Tidy the crew table before it is stored.

	* The same resource twice keeps its first row: two rows would book the person twice for the
	  same task and double the hours on every day.
	* At most one lead: the first ticked row keeps the flag.
	* Hours round to two decimals (the field is stored at that precision anyway; this makes the
	  planner's totals add up exactly).
	* A negative hours value is an error, since it would *subtract* from a day's booked hours.
	"""
	rows = list(_crew_rows(doc))
	if not rows:
		return

	seen = set()
	for row in rows:
		resource = row.get("resource")
		if resource and resource in seen:
			doc.remove(row)
			continue
		if resource:
			seen.add(resource)

	led = False
	for row in _crew_rows(doc):
		hours = row.get("hours")
		if hours not in (None, ""):
			hours = flt(hours)
			if hours < 0:
				frappe.throw(_("Crew row {0}: hours cannot be negative.").format(row.get("idx")))
			row.hours = round(hours, 2)
		if row.get("is_lead"):
			if led:
				row.is_lead = 0
			led = True


def on_task_update(doc, method=None):
	"""Mirror the crew into assignments. See the module docstring for the rules."""
	flags = frappe.flags
	if (
		getattr(flags, "in_import", False)
		or getattr(flags, "in_patch", False)
		or getattr(flags, "in_install", False)
		or getattr(flags, "in_migrate", False)
	):
		return
	try:
		if doc.get("status") in FINISHED_STATUSES:
			return

		now = _crew_users(_crew_rows(doc))
		before_doc = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
		before = _crew_users(_crew_rows(before_doc)) if before_doc else set()

		added = sorted(now - before)
		removed = sorted(before - now)
		if not added and not removed:
			return

		from frappe.desk.form.assign_to import add as assign_add
		from frappe.desk.form.assign_to import remove as assign_remove

		open_todos = {
			row.get("allocated_to")
			for row in frappe.get_all(
				"ToDo",
				filters={"reference_type": "Task", "reference_name": doc.name, "status": "Open"},
				fields=["allocated_to"],
			)
		}

		for user in added:
			if user in open_todos:
				continue
			try:
				assign_add(
					{
						"assign_to": [user],
						"doctype": "Task",
						"name": doc.name,
						"description": _("On the crew for {0}").format(doc.get("subject") or doc.name),
					}
				)
			except Exception:
				frappe.log_error(
					title="Task crew assignment failed",
					message=f"{doc.name} -> {user}\n{frappe.get_traceback()}",
				)

		for user in removed:
			if user not in open_todos:
				continue
			try:
				assign_remove("Task", doc.name, user)
			except Exception:
				frappe.log_error(
					title="Task crew unassignment failed",
					message=f"{doc.name} -> {user}\n{frappe.get_traceback()}",
				)
	except Exception:
		frappe.log_error(title="Task crew sync failed", message=frappe.get_traceback())
