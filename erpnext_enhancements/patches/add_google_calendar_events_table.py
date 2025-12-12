import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def execute():
	doctypes = ["ToDo", "Task", "Project"]
	for doctype in doctypes:
		if not frappe.db.exists("Custom Field", f"{doctype}-google_calendar_events"):
			create_custom_field(
				doctype,
				{
					"fieldname": "google_calendar_events",
					"label": "Google Calendar Events",
					"fieldtype": "Table",
					"options": "Google Calendar Event Log",
					"insert_after": "custom_google_event_id",
				},
			)
