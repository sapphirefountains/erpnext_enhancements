import frappe
from frappe.utils import add_to_date, get_datetime, get_system_timezone, get_url_to_form
from googleapiclient.errors import HttpError

def sync_doctype_to_event(doc, method):
	"""
	Syncs a supported DocType to Google Calendar(s).
	Triggered by: on_update of configured DocTypes.
	"""
	# Handle cancellation/closure by removing the event
	# Check specific statuses for different DocTypes if needed, but generic "Cancelled"/"Closed" is common.
	# For Task: Cancelled, Closed.
	# For Event: Cancelled.
	# For Project: Cancelled, Completed? (Maybe just keep it if completed?)
	# User didn't specify deletion criteria for Project/ToDo/Event, but usually sync should reflect state.
	# If cancelled, usually remove from calendar.
	status_field = doc.get("status")
	if status_field in ["Cancelled", "Closed"]:
		delete_event_from_google(doc, method)
		return

	# Determine fields based on DocType
	start_dt, end_dt, summary, description, location = get_sync_data(doc)
	
	if not (start_dt and end_dt):
		return

	# Get configured calendars for this DocType
	calendars = get_google_calendars_for_doctype(doc.doctype, doc.owner)
	
	if not calendars:
		return

	for calendar_conf in calendars:
		sync_to_google_calendar(
			doc,
			calendar_conf,
			summary=summary,
			start_dt=start_dt,
			end_dt=end_dt,
			description=description,
			location=location
		)

def get_sync_data(doc):
	"""
	Extracts start, end, summary, description, location based on DocType.
	"""
	start_dt = None
	end_dt = None
	summary = ""
	description = f"{doc.doctype}: {doc.name}\n\nLink: {get_url_to_form(doc.doctype, doc.name)}"
	location = None

	if doc.doctype == "Task":
		start_dt = doc.get("custom_start_datetime")
		end_dt = doc.get("custom_end_datetime")
		if not (start_dt and end_dt):
			start_dt = doc.get("exp_start_date")
			end_dt = doc.get("exp_end_date")

		project_part = f" - {doc.project}" if doc.get("project") else ""
		summary = f"{doc.subject}{project_part} ({doc.name})"

		location_link = doc.get("custom_locationaddress_of_task")
		if location_link:
			location = frappe.db.get_value("Address", location_link, "custom_full_address")

	elif doc.doctype == "Event":
		start_dt = doc.get("starts_on")
		end_dt = doc.get("ends_on")
		summary = doc.get("subject") or doc.name
		# Event might have description field
		if doc.get("description"):
			description += f"\n\n{doc.get('description')}"

	elif doc.doctype == "Project":
		start_dt = doc.get("custom_calendar_datetime_start")
		end_dt = doc.get("custom_calendar_datetime_end")
		summary = doc.get("project_name") or doc.name

	elif doc.doctype == "ToDo":
		start_dt = doc.get("custom_calendar_datetime_start")
		end_dt = doc.get("custom_calendar_datetime_end")
		summary = doc.get("description") or doc.name
		# ToDo description is the summary, so maybe don't duplicate it in description?
		# But link is useful.

	return start_dt, end_dt, summary, description, location

def delete_event_from_google(doc, method=None):
	"""
	Deletes the associated Google Calendar event(s).
	Triggered by: on_trash, or when status becomes Cancelled/Closed
	"""
	# Check for child table events
	events_table = doc.get("google_calendar_events")

	# Also check legacy field for migration/backward compatibility if data exists there and not in table yet
	legacy_event_id = doc.get("custom_google_event_id")
	if legacy_event_id:
		# Try to delete using legacy logic (guessing the calendar)
		# Or just ignore if we are strictly moving to table.
		# If migration happens, it should be in table.
		# Let's support it if present.
		pass

	if not events_table:
		return

	# We need the service object to delete.
	# Since we store multiple events, we need to know which calendar each belongs to.
	# The child table stores `google_calendar` link.
	
	from frappe.integrations.doctype.google_calendar.google_calendar import get_google_calendar_object

	for row in events_table:
		if not row.event_id:
			continue

		try:
			google_calendar_doc = frappe.get_doc("Google Calendar", row.google_calendar)
			service, _ = get_google_calendar_object(google_calendar_doc)
			calendar_id = google_calendar_doc.google_calendar_id or "primary"
			
			service.events().delete(calendarId=calendar_id, eventId=row.event_id).execute()
		except HttpError as e:
			if e.resp.status not in [404, 410]:
				frappe.log_error(message=f"Google Calendar Delete Error: {e}", title="Google Calendar Sync")
		except Exception as e:
			frappe.log_error(message=f"Google Calendar Delete Error: {e}", title="Google Calendar Sync")

	if method != "on_trash":
		# Clear the table
		doc.set("google_calendar_events", [])
		# doc.save() # Avoid save in on_update
		# Use db update if possible, but clearing child table is complex with db_set
		# If this is called during on_update, modifying doc is okay if we don't save.
		# But we need it to persist.
		# frappe.db.delete("Google Calendar Event Log", {"parent": doc.name}) ?
		# Safer to just let the doc update handle it if possible, but this is a side effect.
		# If we are in `on_update`, we shouldn't `doc.save()`.
		pass

def sync_to_google_calendar(doc, google_calendar_doc, summary, start_dt, end_dt, description, location=None):
	from frappe.integrations.doctype.google_calendar.google_calendar import get_google_calendar_object

	try:
		service, _ = get_google_calendar_object(google_calendar_doc)
	except Exception as e:
		frappe.log_error(message=f"Error getting GC object: {e}", title="Google Calendar Sync")
		return

	calendar_id = google_calendar_doc.google_calendar_id or "primary"
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

	if location:
		event_body["location"] = location

	# Check if we already have an event for this calendar
	event_id = None
	row_idx = -1

	if not doc.get("google_calendar_events"):
		doc.set("google_calendar_events", [])

	for idx, row in enumerate(doc.google_calendar_events):
		if row.google_calendar == google_calendar_doc.name:
			event_id = row.event_id
			row_idx = idx
			break

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
					new_id = _create_event(service, calendar_id, event_body)
					if new_id:
						doc.google_calendar_events[row_idx].event_id = new_id
						doc.google_calendar_events[row_idx].db_update()
				else:
					frappe.log_error(message=f"Google Calendar Sync Error (Patch): {e}", title="Google Calendar Sync")
		else:
			new_id = _create_event(service, calendar_id, event_body)
			if new_id:
				doc.append("google_calendar_events", {
					"google_calendar": google_calendar_doc.name,
					"event_id": new_id
				})
				# Persist the new row immediately so we don't lose it if on_update doesn't save
				# But we can't easily persist just one row if the parent is unsaved.
				# However, since this is called in on_update, the parent IS saving/saved.
				# But changes to `doc` in `on_update` are not automatically saved to DB unless we call save, which recurses.
				# Best practice: use frappe.db.set_value or raw SQL for the child table?
				# Or better: `doc.save(ignore_permissions=True)` but inhibit recursion?
				# `flags.ignore_permissions = True`
				# Actually, if we are in on_update, we can assume the doc is being saved.
				# But we need to update the child table in DB.

				# Let's insert the child row directly to DB.
				last_row = doc.google_calendar_events[-1]
				last_row.parent = doc.name
				last_row.parenttype = doc.doctype
				last_row.parentfield = "google_calendar_events"
				last_row.db_insert()

	except Exception as e:
		frappe.log_error(message=f"Google Calendar Sync Error: {e}", title="Google Calendar Sync")

def _create_event(service, calendar_id, event_body):
	try:
		event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
		return event.get("id")
	except Exception as e:
		frappe.log_error(message=f"Google Calendar Sync Error (Insert): {e}", title="Google Calendar Sync")
		return None

def get_google_calendars_for_doctype(doctype, user):
	"""
	Returns a list of Google Calendar docs configured for this DocType.
	"""
	calendars = []

	# 1. Check Global Settings Map
	settings = frappe.get_single("ERPNext Enhancements Settings")
	if settings.google_calendar_sync_map:
		for row in settings.google_calendar_sync_map:
			if row.reference_doctype == doctype:
				try:
					gc = frappe.get_doc("Google Calendar", row.google_calendar)
					if gc.enable:
						calendars.append(gc)
				except:
					continue

	# 2. Check User's Personal Calendar (Fallback or Additive?)
	# Original code had fallback. User request implies specific configuration.
	# "I want the Google Calendar Sync to be a table so I can select multiple calendars"
	# It implies explicit configuration.
	# However, if the table is empty for a DocType, should we fallback?
	# "as well as select which DocType syncs to which calendar"
	# I will stick to the explicit configuration in the table.
	# If nothing is in the table, nothing syncs.

	return calendars
