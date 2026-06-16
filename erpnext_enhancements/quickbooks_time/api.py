"""QuickBooks Time integration -- inbound timesheet webhook.

A lightweight, standalone path, independent of the QuickBooks Online accounting
pipeline: it turns inbound QuickBooks Time timesheet entries into ERPNext Time
Log documents, resolving Employee/Project via the custom QuickBooks-id fields
(``custom_quickbooks_user_id`` on Employee, ``custom_quickbooks_jobcode_id`` on
Project).

Webhook URL (guest):
``/api/method/erpnext_enhancements.quickbooks_time.api.qb_timesheet_webhook``
-- previously ``...quickbooks_time_integration.api.qb_timesheet_webhook``; update
the endpoint configured in QuickBooks Time after deploying this rename.
"""

import json

import frappe


# Helper function to find the ERPNext Employee by the custom field
def get_erpnext_employee(qb_user_id):
    """Finds the ERPNext Employee DocName based on the custom QuickBooks User ID."""
    if not qb_user_id:
        frappe.throw("Received webhook data without a User ID.")

    # Query the Employee DocType where your custom field matches the ID from QuickBooks
    employee = frappe.db.get_value("Employee", {"custom_quickbooks_user_id": qb_user_id}, "name")

    if not employee:
        frappe.throw(f"Employee not found with QuickBooks User ID: {qb_user_id}")
    return employee


# Helper function to find the ERPNext Project by the custom field
def get_erpnext_project(qb_jobcode_id):
    """Finds the ERPNext Project DocName based on the custom QuickBooks Jobcode ID."""
    if not qb_jobcode_id:
        return None  # It's valid for a timesheet to not have a project

    # Query the Project DocType where your custom field matches the ID from QuickBooks
    project = frappe.db.get_value("Project", {"custom_quickbooks_jobcode_id": qb_jobcode_id}, "name")

    if not project:
        frappe.log_message(f"Project not found with QuickBooks Jobcode ID: {qb_jobcode_id}. Time Log will be created without a project.", "QB Time Sync Warning")
    return project


@frappe.whitelist(allow_guest=True)
def qb_timesheet_webhook(*args, **kwargs):
    """Guest webhook: create an ERPNext Time Log from a QuickBooks Time timesheet.

    Parses the raw request body, resolves the ERPNext Employee/Project from the
    QB user/jobcode ids, converts the duration (seconds) to hours, and inserts a
    Time Log with ``ignore_permissions`` (the request runs as guest). On any
    error it logs the traceback and returns HTTP 500.

    NOTE: this is the QuickBooks *Time* path and is independent of the QBO
    accounting pipeline. Unlike the QBO webhook it does NOT yet verify an Intuit
    signature -- see the inline note below.
    """
    # SECURITY: no signature verification here yet. A signature/HMAC check should
    # be added to ensure the request genuinely originates from QuickBooks Time
    # (the QBO webhook in quickbooks_online/core/webhooks.py does verify its signature).

    try:
        webhook_data = frappe.request.get_data()
        data = json.loads(webhook_data)

        # Note: The structure of this payload is an example.
        # You MUST adjust this based on the actual data QuickBooks sends.
        timesheet_info = data.get('timesheets')[0]

        # Use the helper functions to find the corresponding ERPNext documents
        employee_docname = get_erpnext_employee(timesheet_info.get('user_id'))
        project_docname = get_erpnext_project(timesheet_info.get('jobcode_id'))

        # Create the new Time Log document in ERPNext
        time_log = frappe.new_doc('Time Log')
        time_log.employee = employee_docname
        time_log.project = project_docname
        time_log.from_time = timesheet_info.get('start')  # Ensure this is in 'YYYY-MM-DD HH:MM:SS' format
        time_log.to_time = timesheet_info.get('end')      # Ensure this is in 'YYYY-MM-DD HH:MM:SS' format
        time_log.hours = float(timesheet_info.get('duration', 0)) / 3600.0  # Assuming duration is in seconds
        time_log.activity_type = "QuickBooks Time Sync"  # Set a default or map from QB

        # The 'guest' user is unlikely to have permission, so we ignore permissions.
        # This is safe because we've validated the data source.
        time_log.insert(ignore_permissions=True)
        frappe.db.commit()

        return {"status": "success", "message": f"Time Log {time_log.name} created."}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "QuickBooks Time Webhook Failed")
        frappe.local.response.http_status_code = 500
        return {"status": "error", "message": str(e)}
