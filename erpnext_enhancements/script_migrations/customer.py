"""Migrated Customer Server Scripts, wired via ``hooks.py``.

Hook wiring (see ``hooks.py``):
  * doc_events["Customer"]["before_save"] -> :func:`set_last_activity`
  * scheduler_events["daily"] -> :func:`customer_inactivity_reminder`

Originally Frappe "Server Script" records stored only in the site DB; now
versioned with the app.
"""

import frappe

# Customer Server Scripts migrated to native doc_events / scheduler_events.


def set_last_activity(doc, method=None):
	"""Source Server Script: "Update Last Activity on Customer Save"
	(Customer, Before Save).

	Stamp the last activity date every time a Customer is saved. Wired as a
	Customer ``before_save`` doc_event. Mutates ``doc`` in place (the enclosing
	save persists it); no DB write of its own.
	"""
	doc.custom_last_activity_date = frappe.utils.today()


def customer_inactivity_reminder():
	"""Source Server Script: "Customer Inactivity Reminder" (Scheduler Event, Daily).

	Create follow-up ToDos for customers whose reminder period has elapsed. Wired
	as a daily ``scheduler_event``. The reminder window is the Customer's own
	``custom_reminder_days`` when positive, else the global fallback from the
	"Sales Activity Settings" Single (``inactivity_threshold``; the Single ships
	with the app since v0.8.0). Set ``custom_reminder_days = -1`` to opt a
	customer out entirely (any negative value skips; 0 means "unset" because it
	is the Int default, and falls through to the global fallback). Setting the
	global ``inactivity_threshold`` to 0 disables the fallback site-wide. For
	each enabled Customer with a set ``custom_last_activity_date`` whose
	(last activity + reminder window) is now due, inserts an Open ToDo allocated
	to the Customer's owner — unless an Open ToDo for that Customer already
	exists.

	Side effects:
		Inserts ToDo documents and commits the DB.
	"""
	default_reminder_days = frappe.utils.cint(
		frappe.db.get_single_value("Sales Activity Settings", "inactivity_threshold")
	)

	customers = frappe.get_all(
		"Customer",
		filters={
			"disabled": 0,
			"custom_last_activity_date": ["is", "set"],
		},
		fields=[
			"name",
			"customer_name",
			"custom_last_activity_date",
			"custom_reminder_days",
			"owner",
		],
	)

	current_date = frappe.utils.get_datetime(frappe.utils.today())

	for customer in customers:
		reminder_days = frappe.utils.cint(customer.custom_reminder_days) or default_reminder_days
		if reminder_days <= 0:
			continue

		follow_up_date = frappe.utils.get_datetime(
			frappe.utils.add_days(customer.custom_last_activity_date, reminder_days)
		)

		if follow_up_date <= current_date:
			existing_todo = frappe.db.exists(
				"ToDo",
				{
					"reference_type": "Customer",
					"reference_name": customer.name,
					"status": "Open",
				},
			)

			if not existing_todo:
				todo_description = f"Follow up with {customer.customer_name} ({customer.name})"
				frappe.get_doc(
					{
						"doctype": "ToDo",
						"description": todo_description,
						"status": "Open",
						# ToDo's assignee field is allocated_to (the source server
						# script's assigned_to never existed on v16 ToDo and was
						# silently dropped, leaving every ToDo unassigned)
						"allocated_to": customer.owner,
						"reference_type": "Customer",
						"reference_name": customer.name,
					}
				).insert()

	frappe.db.commit()
