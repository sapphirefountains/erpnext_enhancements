import frappe

# Customer Server Scripts migrated to native doc_events / scheduler_events.


def set_last_activity(doc, method=None):
	"""Source Server Script: "Update Last Activity on Customer Save"
	(Customer, Before Save).

	Stamp the last activity date every time a Customer is saved.
	"""
	doc.custom_last_activity_date = frappe.utils.today()


def customer_inactivity_reminder():
	"""Source Server Script: "Customer Inactivity Reminder" (Scheduler Event, Daily).

	Create follow-up ToDos for customers whose reminder period has elapsed.
	"""
	customers = frappe.get_all(
		"Customer",
		filters={
			"disabled": 0,
			"custom_reminder_days": [">", 0],
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
		follow_up_date = frappe.utils.get_datetime(
			frappe.utils.add_days(customer.custom_last_activity_date, customer.custom_reminder_days)
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
						"assigned_to": customer.owner,
						"reference_type": "Customer",
						"reference_name": customer.name,
					}
				).insert()

	frappe.db.commit()
