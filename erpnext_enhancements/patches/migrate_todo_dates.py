import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from datetime import datetime, time, timedelta

def execute():
    """
    Migrate ToDo dates to custom_calendar_datetime_start and end.
    Also hide the original date field.
    Update Notifications relying on the old date field.
    Ensures Custom Fields exist before migration.
    """
    # 0. Ensure Custom Fields Exist
    custom_fields = {
        "ToDo": [
            {
                "fieldname": "custom_calendar_datetime_start",
                "fieldtype": "Datetime",
                "label": "Calendar Start Time",
                "insert_after": "description"
            },
            {
                "fieldname": "custom_calendar_datetime_end",
                "fieldtype": "Datetime",
                "label": "Calendar End Time",
                "insert_after": "custom_calendar_datetime_start"
            }
        ]
    }

    for dt, fields in custom_fields.items():
        for field in fields:
            if not frappe.db.has_column(dt, field["fieldname"]):
                create_custom_field(dt, field)

    frappe.reload_doc("Desk", "doctype", "todo")

    # 1. Update ToDo documents
    todos = frappe.db.get_all("ToDo", filters={"date": ["is", "set"]}, fields=["name", "date", "custom_calendar_datetime_start"])

    for todo in todos:
        if not todo.custom_calendar_datetime_start and todo.date:
            start_dt = datetime.combine(todo.date, time(8, 0, 0))
            end_dt = start_dt + timedelta(hours=1)

            frappe.db.set_value("ToDo", todo.name, {
                "custom_calendar_datetime_start": start_dt,
                "custom_calendar_datetime_end": end_dt
            }, update_modified=False)

    # 2. Hide 'date' field and make it optional
    # Hiding a mandatory field breaks form submission, so we must make it optional.
    frappe.make_property_setter({
        "doctype": "ToDo",
        "fieldname": "date",
        "property": "hidden",
        "value": 1,
        "property_type": "Check",
        "doctype_or_field": "DocField"
    })

    frappe.make_property_setter({
        "doctype": "ToDo",
        "fieldname": "date",
        "property": "reqd",
        "value": 0,
        "property_type": "Check",
        "doctype_or_field": "DocField"
    })

    frappe.make_property_setter({
        "doctype": "ToDo",
        "fieldname": "date",
        "property": "in_list_view",
        "value": 0,
        "property_type": "Check",
        "doctype_or_field": "DocField"
    })

    # 3. Update Notifications
    if frappe.db.exists("DocType", "Notification"):
        notifications = frappe.db.get_all("Notification", filters={
            "document_type": "ToDo",
            "date_changed": "date"
        }, fields=["name"])

        for notification in notifications:
            frappe.db.set_value("Notification", notification.name, "date_changed", "custom_calendar_datetime_start")

if __name__ == "__main__":
    frappe.connect()
    execute()
    frappe.db.commit()
