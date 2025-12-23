import frappe
from frappe.utils import add_to_date, get_datetime, get_system_timezone, get_url_to_form, strip_html
from googleapiclient.errors import HttpError


def sync_doctype_to_event(doc, method):
	"""
	Syncs a supported DocType to Google Calendar(s).
	Triggered by: on_update of configured DocTypes.
	"""
	# Check flags to avoid recursion
	if doc.flags.in_google_calendar_sync or frappe.flags.sync_source == "background_worker":
		return

	# Enqueue the background job
	frappe.enqueue(
		"erpnext_enhancements.calendar_sync.run_google_calendar_sync",
		queue="default",
		doc=doc,
		trigger_method=method,
		sync_source="background_worker"
	)


def run_google_calendar_sync(doc, trigger_method, sync_source=None):
	"""
	Background worker for Google Calendar sync.
	"""
	try:
		# Set flag to identify source
		if sync_source:
			frappe.flags.sync_source = sync_source

		# Better to fetch fresh doc to avoid stale data
		if not doc.get_doc_before_save():
			pass

		_sync_doctype_to_event(doc, trigger_method)
	except Exception as e:
		frappe.log_error(message=f"Google Calendar Background Sync Error: {e}", title="Google Calendar Sync Worker")
	finally:
		frappe.flags.sync_source = None


def _sync_doctype_to_event(doc, trigger_method):
	# Handle cancellation/closure by removing the event
	deletion_statuses = {
		"Task": ["Cancelled", "Closed"],
		"Event": ["Cancelled"],
		"Project": ["Cancelled"],
		"ToDo": ["Cancelled"],
	}

	status_field = doc.get("status")
	if status_field in deletion_statuses.get(doc.doctype, ["Cancelled"]):
		delete_event_from_google(doc, trigger_method)
		return

	# Check if relevant fields have changed (Optimization)
	if not has_relevant_fields_changed(doc):
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
			location=location,
		)


def has_relevant_fields_changed(doc):
	"""
	Checks if fields relevant to Google Calendar sync have changed.
	"""
	if doc.is_new():
		return True

	doc_before_save = doc.get_doc_before_save()
	if not doc_before_save:
		return True

	fields_to_check = {
		"Task": [
			"custom_start_datetime",
			"custom_end_datetime",
			"exp_start_date",
			"exp_end_date",
			"subject",
			"project",
			"custom_locationaddress_of_task",
			"status",
			"description",
		],
		"Event": ["starts_on", "ends_on", "subject", "status", "description"],
		"Project": [
			"custom_calendar_datetime_start",
			"custom_calendar_datetime_end",
			"expected_start_date",
			"expected_end_date",
			"project_name",
			"status",
			"description",
		],
		"ToDo": [
			"custom_calendar_datetime_start",
			"custom_calendar_datetime_end",
			"due_date",
			"status",
			"description",
		],
	}

	# Helper to normalize values for comparison
	def normalize(value):
		if value is None:
			return ""
		try:
			dt = get_datetime(value)
			return dt.strftime("%Y-%m-%d %H:%M:%S")
		except (ValueError, TypeError):
			return str(value)

	for field in fields_to_check.get(doc.doctype, []):
		old_value = normalize(doc_before_save.get(field))
		new_value = normalize(doc.get(field))

		if old_value != new_value:
			return True

	return False


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
		if doc.get("description"):
			description += f"\n\n{doc.get('description')}"

	elif doc.doctype == "Project":
		start_dt = doc.get("custom_calendar_datetime_start")
		end_dt = doc.get("custom_calendar_datetime_end")
		if not (start_dt and end_dt):
			start_dt = doc.get("expected_start_date")
			end_dt = doc.get("expected_end_date")
		summary = doc.get("project_name") or doc.name

	elif doc.doctype == "ToDo":
		start_dt = doc.get("custom_calendar_datetime_start")
		end_dt = doc.get("custom_calendar_datetime_end")
		if not (start_dt and end_dt):
			if doc.get("due_date"):
				start_dt = doc.get("due_date")
				end_dt = add_to_date(start_dt, hours=1)
		summary = strip_html(doc.get("description") or doc.name)
		description = f"Link: {get_url_to_form(doc.doctype, doc.name)}"

	return start_dt, end_dt, summary, description, location


def delete_event_from_google(doc, trigger_method=None):
	"""
	Wrapper to run deletion in background.
	"""
	if frappe.flags.sync_source == "background_worker":
		_delete_event_from_google(doc, trigger_method)
	else:
		frappe.enqueue(
			"erpnext_enhancements.calendar_sync.run_google_calendar_delete",
			queue="default",
			doc=doc,
			trigger_method=trigger_method,
			sync_source="background_worker"
		)


def run_google_calendar_delete(doc, trigger_method, sync_source=None):
	try:
		if sync_source:
			frappe.flags.sync_source = sync_source
		_delete_event_from_google(doc, trigger_method)
	except Exception as e:
		frappe.log_error(message=f"Google Calendar Background Delete Error: {e}", title="Google Calendar Sync Worker")
	finally:
		frappe.flags.sync_source = None


def _delete_event_from_google(doc, trigger_method=None):
	"""
	Deletes the associated Google Calendar event(s).
	Triggered by: on_trash, or when status becomes Cancelled/Closed
	"""
	# Get events from Global Calendar Sync Log
	sync_logs = frappe.get_all(
		"Global Calendar Sync Log",
		filters={"reference_doctype": doc.doctype, "reference_docname": doc.name},
		fields=["name", "google_calendar", "event_id"]
	)

	# Legacy support: check field just in case
	legacy_event_id = doc.get("custom_google_event_id")
	if legacy_event_id:
		calendars = get_google_calendars_for_doctype(doc.doctype, doc.owner)
		if calendars:
			from frappe.integrations.doctype.google_calendar.google_calendar import get_google_calendar_object

			for calendar_conf in calendars:
				try:
					service, _ = get_google_calendar_object(calendar_conf)
					calendar_id = calendar_conf.google_calendar_id or "primary"
					service.events().delete(calendarId=calendar_id, eventId=legacy_event_id).execute()
				except HttpError as e:
					if e.resp.status not in [404, 410]:
						frappe.log_error(message=f"Google Calendar Legacy Delete Error: {e}", title="Google Calendar Sync")
				except Exception as e:
					frappe.log_error(message=f"Google Calendar Legacy Delete Error: {e}", title="Google Calendar Sync")

		# Clear legacy field
		if trigger_method != "on_trash" and doc.meta.has_field("custom_google_event_id"):
			doc.custom_google_event_id = None
			doc.save(ignore_permissions=True)

	if not sync_logs:
		return

	from frappe.integrations.doctype.google_calendar.google_calendar import get_google_calendar_object

	for log in sync_logs:
		if not log.event_id:
			continue

		try:
			google_calendar_doc = frappe.get_doc("Google Calendar", log.google_calendar)
			service, _ = get_google_calendar_object(google_calendar_doc)
			calendar_id = google_calendar_doc.google_calendar_id or "primary"

			service.events().delete(calendarId=calendar_id, eventId=log.event_id).execute()
		except HttpError as e:
			if e.resp.status not in [404, 410]:
				frappe.log_error(message=f"Google Calendar Delete Error: {e}", title="Google Calendar Sync")
		except Exception as e:
			frappe.log_error(message=f"Google Calendar Delete Error: {e}", title="Google Calendar Sync")

		# Delete the log entry
		frappe.delete_doc("Global Calendar Sync Log", log.name, ignore_permissions=True)


def sync_to_google_calendar(doc, google_calendar_doc, summary, start_dt, end_dt, description, location=None):
	# Ensure end time is after start time to avoid Google API errors
	if get_datetime(end_dt) <= get_datetime(start_dt):
		end_dt = add_to_date(start_dt, minutes=30)

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

	# Check for existing event log
	event_id = None
	existing_log_name = None

	existing_logs = frappe.get_all(
		"Global Calendar Sync Log",
		filters={
			"reference_doctype": doc.doctype,
			"reference_docname": doc.name,
			"google_calendar": google_calendar_doc.name
		},
		fields=["name", "event_id"]
	)

	if existing_logs:
		event_id = existing_logs[0].event_id
		existing_log_name = existing_logs[0].name

	try:
		if event_id:
			try:
				service.events().patch(calendarId=calendar_id, eventId=event_id, body=event_body).execute()
			except HttpError as e:
				if e.resp.status == 404 or e.resp.status == 410:
					# Re-create it
					new_id = _create_event(service, calendar_id, event_body)
					if new_id:
						frappe.db.set_value(
							"Global Calendar Sync Log", existing_log_name, "event_id", new_id
						)
				else:
					frappe.log_error(message=f"Google Calendar Sync Error (Patch): {e}", title="Google Calendar Sync")
		else:
			new_id = _create_event(service, calendar_id, event_body)
			if new_id:
				try:
					# Create new sync log
					new_log = frappe.get_doc({
						"doctype": "Global Calendar Sync Log",
						"reference_doctype": doc.doctype,
						"reference_docname": doc.name,
						"google_calendar": google_calendar_doc.name,
						"event_id": new_id
					})
					new_log.insert(ignore_permissions=True)

				except Exception as e:
					frappe.log_error(message=f"Google Calendar Sync Save Error (Insert): {e}", title="Google Calendar Sync")

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
	Combines calendars from global settings and the user's personal calendar.
	"""
	if doctype not in ["ToDo", "Task", "Project", "Event"]:
		return []

	calendars = {}

	# 1. Check Global Settings Map
	settings = frappe.get_single("ERPNext Enhancements Settings")
	if settings.google_calendar_sync_map:
		for row in settings.google_calendar_sync_map:
			if row.reference_doctype == doctype:
				try:
					gc = frappe.get_doc("Google Calendar", row.google_calendar)
					if gc.enable:
						calendars[gc.name] = gc
				except Exception:
					pass

	# 2. Add User's Personal Calendar
	user_calendar_name = frappe.db.get_value("Google Calendar", {"user": user, "enable": 1}, "name")
	if user_calendar_name:
		if user_calendar_name not in calendars:
			try:
				gc = frappe.get_doc("Google Calendar", user_calendar_name)
				calendars[gc.name] = gc
			except Exception:
				pass

	return list(calendars.values())
