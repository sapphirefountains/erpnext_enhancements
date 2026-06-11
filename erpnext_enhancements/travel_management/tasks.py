# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Scheduled jobs for Travel Management (registered in hooks.py
``scheduler_events.daily``). Reminder emails live in reminders.py."""

import frappe
from frappe.utils import cint, getdate


def auto_advance_trip_statuses():
	"""Daily: flip trips to In Progress / Completed based on their dates.

	Booked and Closed remain manual-only transitions *into* them; Closed is
	never touched. Planning trips inside their date window advance too —
	collaborative crews forget to click Booked, and the trip is factually
	happening (drop "Planning" from the first tuple to require Booked).

	Uses ``db_set`` (not ``save``) on purpose: the job must never trip the
	Closed lock, recompute rollups mid-edit, or collide with a user's open
	form timestamp.
	"""
	if not cint(frappe.db.get_single_value("Travel Settings", "auto_advance_statuses")):
		return

	today = getdate()
	transitions = [
		(
			"In Progress",
			{
				"status": ["in", ("Planning", "Booked")],
				"start_date": ["<=", today],
				"end_date": [">=", today],
			},
		),
		(
			"Completed",
			{
				"status": ["in", ("Planning", "Booked", "In Progress")],
				"end_date": ["<", today],
			},
		),
	]

	for new_status, filters in transitions:
		for name in frappe.get_all("Travel Trip", filters=filters, pluck="name"):
			try:
				doc = frappe.get_doc("Travel Trip", name)
				doc.db_set("status", new_status, notify=True)
				doc.add_comment(
					"Comment", frappe._("Status auto-advanced to {0}").format(frappe._(new_status))
				)
			except Exception:
				frappe.log_error(
					title="Travel Trip status auto-advance failed",
					message=f"{name} -> {new_status}\n{frappe.get_traceback()}",
				)
