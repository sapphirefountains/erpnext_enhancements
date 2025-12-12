import frappe
from frappe.utils import nowdate, get_datetime

@frappe.whitelist()
def get_todo_events(start, end, user=None, filters=None):
    if not user:
        user = frappe.session.user

    events = frappe.get_all("ToDo",
        filters=[
            {"owner": user},
            {"custom_calendar_datetime_start": ["<=", end]},
            {"custom_calendar_datetime_end": [">=", start]}
        ],
        fields=["name", "description", "custom_calendar_datetime_start", "custom_calendar_datetime_end"],
        as_dict=True
    )

    return [
        {
            "name": event["name"],
            "title": event["description"],
            "start": event["custom_calendar_datetime_start"],
            "end": event["custom_calendar_datetime_end"],
            "allDay": 0
        }
        for event in events
    ]

def validate_todo_dates(doc, method):
    if doc.custom_calendar_datetime_start and doc.custom_calendar_datetime_end:
        start_datetime = get_datetime(doc.custom_calendar_datetime_start)
        end_datetime = get_datetime(doc.custom_calendar_datetime_end)

        if start_datetime > end_datetime:
            frappe.throw("End date and time cannot be before start date and time")
