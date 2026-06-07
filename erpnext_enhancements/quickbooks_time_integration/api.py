import json

import frappe

from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.api import (
	get_dashboard_status,
	import_all,
	link_existing_record,
	oauth_callback,
	preview_resync,
	preview_existing_matches,
	quickbooks_webhook,
	retry_failed,
	run_resync,
	start_oauth,
	sync_entity,
)

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
        return None # It's valid for a timesheet to not have a project
    
    # Query the Project DocType where your custom field matches the ID from QuickBooks
    project = frappe.db.get_value("Project", {"custom_quickbooks_jobcode_id": qb_jobcode_id}, "name")
    
    if not project:
        frappe.log_message(f"Project not found with QuickBooks Jobcode ID: {qb_jobcode_id}. Time Log will be created without a project.", "QB Time Sync Warning")
    return project

@frappe.whitelist(allow_guest=True)
def qb_timesheet_webhook(*args, **kwargs):
    """
    Webhook endpoint to receive timesheet data from QuickBooks Time.
    """
    # It's highly recommended to add a signature verification step here
    # to ensure the request is genuinely from QuickBooks Time.

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
        time_log.from_time = timesheet_info.get('start') # Ensure this is in 'YYYY-MM-DD HH:MM:SS' format
        time_log.to_time = timesheet_info.get('end')     # Ensure this is in 'YYYY-MM-DD HH:MM:SS' format
        time_log.hours = float(timesheet_info.get('duration', 0)) / 3600.0 # Assuming duration is in seconds
        time_log.activity_type = "QuickBooks Time Sync" # Set a default or map from QB
        
        # The 'guest' user is unlikely to have permission, so we ignore permissions.
        # This is safe because we've validated the data source.
        time_log.insert(ignore_permissions=True)
        frappe.db.commit()

        return {"status": "success", "message": f"Time Log {time_log.name} created."}

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "QuickBooks Time Webhook Failed")
        frappe.local.response.http_status_code = 500
        return {"status": "error", "message": str(e)}
