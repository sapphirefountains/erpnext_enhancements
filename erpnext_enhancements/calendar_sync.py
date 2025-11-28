import frappe
from frappe.utils import add_to_date, get_datetime, get_system_timezone, get_url_to_form
from googleapiclient.errors import HttpError

def sync_task_to_event(doc, method):
	"""
	Syncs Task to Google Calendar.
	Triggered by: Task > on_update
	"""
	# Handle cancellation/closure by removing the event
	if doc.status in ["Cancelled", "Closed"]:
		delete_event_from_google(doc, method)
		return

	# Use custom datetime fields if available, otherwise fallback or return
	start_dt = doc.get("custom_start_datetime")
	end_dt = doc.get("custom_end_datetime")

	if not (start_dt and end_dt):
		# Fallback to standard expected dates if custom ones are empty? 
		# Or return? User requested custom fields specifically.
		# Let's try standard as fallback to be safe, or check if strictly required.
		# "I'd like to use the custom_start_datetime..." -> usually implies replacement.
		# But to prevent breaking if fields are empty, let's allow fallback if user didn't fill them?
		# Actually, better to strictly follow "I'd like to use..." and maybe log/return if missing.
		# But 'exp_start_date' is standard. Let's try custom, if None, try exp.
		start_dt = doc.exp_start_date
		end_dt = doc.exp_end_date
	
	if not (start_dt and end_dt):
		return

	# Title: "{doc.subject} - {doc.project} ({doc.name})"
	project_part = f" - {doc.project}" if doc.project else ""
	summary = f"{doc.subject}{project_part} ({doc.name})"
	
	sync_to_google_calendar(
		doc,
		summary=summary,
		start_dt=start_dt,
		end_dt=end_dt,
		description=f"Task: {doc.name}\n\nLink: {get_url_to_form(doc.doctype, doc.name)}"
	)

def sync_todo_to_event(doc, method):
	"""
	Syncs ToDo to Google Calendar.
	Triggered by: ToDo > on_update
	"""
	# Handle cancellation by removing the event
	if doc.status == "Cancelled":
		delete_event_from_google(doc, method)
		return

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
		description=f"ToDo: {doc.name}\n\nLink: {get_url_to_form(doc.doctype, doc.name)}"
	)

def delete_event_from_google(doc, method=None):
	"""
	Deletes the associated Google Calendar event.
	Triggered by: on_trash, or when status becomes Cancelled/Closed
	"""
	event_id = doc.get("custom_google_event_id")
	if not event_id:
		return

	service, calendar_id = get_google_calendar_conf(doc.owner)
	if not service:
		return
	
	calendar_id = calendar_id or "primary"

	try:
		service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
		# Clear the ID if the document is not being deleted (i.e. just cancelled)
		# method == "on_trash" implies the doc is being deleted, so no need to db_set
		if method != "on_trash":
			doc.db_set("custom_google_event_id", None)
			
	except HttpError as e:
		if e.resp.status in [404, 410]:
			# Event already gone
			if method != "on_trash":
				doc.db_set("custom_google_event_id", None)
		else:
			frappe.log_error(message=f"Google Calendar Delete Error: {e}", title="Google Calendar Sync")
	except Exception as e:
		frappe.log_error(message=f"Google Calendar Delete Error: {e}", title="Google Calendar Sync")

def sync_to_google_calendar(doc, summary, start_dt, end_dt, description):
	service, calendar_id = get_google_calendar_conf(doc.owner)
	if not service:
		# No credentials found, skip sync
		return

	calendar_id = calendar_id or "primary"
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
					calendarId=calendar_id, 
					eventId=event_id, 
					body=event_body
				).execute()
			except HttpError as e:
				if e.resp.status == 404:
					# Event ID found in doc but not in Google (deleted remotely?)
					# Re-create it
					_create_event(doc, service, calendar_id, event_body)
				else:
					frappe.log_error(message=f"Google Calendar Sync Error (Patch): {e}", title="Google Calendar Sync")
		else:
			_create_event(doc, service, calendar_id, event_body)

	except Exception as e:
		frappe.log_error(message=f"Google Calendar Sync Error: {e}", title="Google Calendar Sync")

def _create_event(doc, service, calendar_id, event_body):
	try:
		event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
		# Use db_set to avoid triggering on_update recursion
		if event.get("id"):
			doc.db_set("custom_google_event_id", event.get("id"))
	except Exception as e:
		frappe.log_error(message=f"Google Calendar Sync Error (Insert): {e}", title="Google Calendar Sync")

def get_google_calendar_conf(user):
	"""
	Returns (service, calendar_id) for the sync.
	Checks Global Settings first, then User settings.
	"""
	try:
		# Use standard ERPNext utility if available
		from frappe.integrations.doctype.google_calendar.google_calendar import get_google_calendar_object
		
		# 1. Check Global Shared Calendar Settings
		global_settings = frappe.get_single("ERPNext Enhancements Settings")
		if global_settings.default_google_calendar_config:
			google_calendar_doc = frappe.get_doc("Google Calendar", global_settings.default_google_calendar_config)
			if google_calendar_doc.enable:
				gcal_service, _ = get_google_calendar_object(google_calendar_doc)
				return gcal_service, google_calendar_doc.google_calendar_id

		# 2. Fallback: User-specific Calendar
		# Find the Google Calendar record for this user
		google_calendar_name = frappe.db.get_value("Google Calendar", {"user": user, "enable": 1}, "name")
		
		if google_calendar_name:
			google_calendar_doc = frappe.get_doc("Google Calendar", google_calendar_name)
			gcal_service, _ = get_google_calendar_object(google_calendar_doc)
			return gcal_service, google_calendar_doc.google_calendar_id

	except ImportError:
		frappe.log_error(message="Google Calendar Integration module not found.", title="Google Calendar Sync")
	except Exception as e:
		frappe.log_error(message=f"Error retrieving Google Calendar service: {e}", title="Google Calendar Sync")

	return None, None
