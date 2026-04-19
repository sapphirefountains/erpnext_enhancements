import frappe

def execute():
	"""
	Removes stale Google Calendar custom fields and DocTypes that cause 404 errors.
	The Google Calendar sync feature was removed but some metadata remains in the database.
	"""
	# 1. Remove Custom Fields related to Google Calendar Sync
	# We search by fieldname and by options (for the table fields)
	
	fields_to_remove = [
		"google_calendar_events",
		"custom_calendar_datetime_start",
		"custom_calendar_datetime_end",
		"custom_google_event_id"
	]
	
	for fieldname in fields_to_remove:
		frappe.db.delete("Custom Field", {"fieldname": fieldname})
	
	# Also search by options just in case there are fields with different names linking to the deleted DocTypes
	deleted_doctypes = [
		"Google Calendar Event Log",
		"Google Calendar Sync Map",
		"Global Calendar Sync Log"
	]
	
	for dt_name in deleted_doctypes:
		frappe.db.delete("Custom Field", {"options": dt_name})
		
		# 2. Delete the DocType records themselves if they still exist in the DB
		if frappe.db.exists("DocType", dt_name):
			frappe.delete_doc("DocType", dt_name, ignore_missing=True, force=True)

	# 3. Clear cache to ensure changes take effect immediately
	frappe.clear_cache()
