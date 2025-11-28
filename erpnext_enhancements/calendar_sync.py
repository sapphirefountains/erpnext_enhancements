import frappe
from frappe.utils import add_to_date, get_datetime, get_system_timezone
from googleapiclient.errors import HttpError

def sync_task_to_event(doc, method):
	"""
	Syncs Task to Google Calendar.
	Triggered by: Task > on_update
	"""
	if not (doc.exp_start_date and doc.exp_end_date):
		return

	# Title: "{doc.subject} - {doc.project} ({doc.name})"
	project_part = f" - {doc.project}" if doc.project else ""
	summary = f"{doc.subject}{project_part} ({doc.name})"
	
	sync_to_google_calendar(
		doc,
		summary=summary,
		start_dt=doc.exp_start_date,
		end_dt=doc.exp_end_date,
		description=f"Task: {doc.name}"
	)

def sync_todo_to_event(doc, method):
	"""
	Syncs ToDo to Google Calendar.
	Triggered by: ToDo > on_update
	"""
	if not doc.date:
		return

	# Title: "{doc.description} - {doc.reference_name} ({doc.name})"
	ref_part = f" - {doc.reference_name}" if doc.reference_name else ""
	summary = f"{doc.description}{ref_part} ({doc.name})"
	
	start_dt = doc.date
	# Default duration: 1 hour
	end_dt = add_to_date(start_dt, hours=1)

	sync_to_google_calendar(
		doc,
		summary=summary,
		start_dt=start_dt,
		end_dt=end_dt,
		description=f"ToDo: {doc.name}"
	)

def sync_to_google_calendar(doc, summary, start_dt, end_dt, description):
	service = get_google_calendar_service(doc.owner)
	if not service:
		# No credentials found, skip sync
		return

	time_zone = get_system_timezone()
	event_body = {
		"summary": summary,
		"description": description,
		"start": {
			"dateTime": get_datetime(start_dt).isoformat(),
			"timeZone": time_zone,
		},
		"end": {
			"dateTime": get_datetime(end_dt).isoformat(),
			"timeZone": time_zone,
		},
	}

	event_id = doc.get("custom_google_event_id")

	try:
		if event_id:
			try:
				service.events().patch(
					calendarId='primary', 
					eventId=event_id, 
					body=event_body
				).execute()
			except HttpError as e:
				if e.resp.status == 404:
					# Event ID found in doc but not in Google (deleted remotely?)
					# Re-create it
					_create_event(doc, service, event_body)
				else:
					frappe.log_error(f"Google Calendar Sync Error (Patch): {e}", "Google Calendar Sync")
		else:
			_create_event(doc, service, event_body)

	except Exception as e:
		frappe.log_error(f"Google Calendar Sync Error: {e}", "Google Calendar Sync")

def _create_event(doc, service, event_body):
	try:
		event = service.events().insert(calendarId='primary', body=event_body).execute()
		# Use db_set to avoid triggering on_update recursion
		if event.get("id"):
			doc.db_set("custom_google_event_id", event.get("id"))
	except Exception as e:
		frappe.log_error(f"Google Calendar Sync Error (Insert): {e}", "Google Calendar Sync")

def get_google_calendar_service(user):
	"""
	Returns a Google Calendar Service object for the specified user.
	"""
	# Check for 'Google Calendar' integration doctype
	try:
		# Use standard ERPNext utility if available
		from frappe.integrations.doctype.google_calendar.google_calendar import get_google_calendar_object
		
		# Find the Google Calendar record for this user
		google_calendar_name = frappe.db.get_value("Google Calendar", {"user": user, "enable": 1}, "name")
		
		if google_calendar_name:
			google_calendar_doc = frappe.get_doc("Google Calendar", google_calendar_name)
			return get_google_calendar_object(google_calendar_doc)
			
	except ImportError:
		frappe.log_error("Google Calendar Integration module not found.", "Google Calendar Sync")
	except Exception as e:
		frappe.log_error(f"Error retrieving Google Calendar service: {e}", "Google Calendar Sync")

	# Alternative: Check 'User Social Login' (Stub)
	# Implementing robust OAuth flow from Social Login tokens is complex without helper functions
	# ensuring 'offline' access and refresh tokens. 
	# For now, we rely on the standard 'Google Calendar' doctype as preferred.
	
	return None
