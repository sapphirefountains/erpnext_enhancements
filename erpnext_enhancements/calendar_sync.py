import frappe
from frappe.utils import add_to_date

def sync_task_to_event(doc, method):
	"""
	Syncs Task to Calendar Event on save.
	Triggered by: Task > on_update
	"""
	# Only proceed if we have valid dates
	if not (doc.exp_start_date and doc.exp_end_date):
		return

	subject = f"Task: {doc.subject}"
	description = f"Linked Task: {doc.name}"
	
	_sync_to_event(
		source_doc=doc,
		subject=subject,
		starts_on=doc.exp_start_date,
		ends_on=doc.exp_end_date,
		description=description
	)

def sync_todo_to_event(doc, method):
	"""
	Syncs ToDo to Calendar Event on save.
	Triggered by: ToDo > on_update
	"""
	# ToDo usually only has a 'date' (Due Date)
	if not doc.date:
		return

	subject = f"ToDo: {doc.description}"
	# Truncate subject if it's too long (Events have char limits)
	if len(subject) > 140:
		subject = subject[:137] + "..."

	# Default duration: 1 hour from the due date/time
	starts_on = doc.date
	ends_on = add_to_date(starts_on, hours=1)
	description = f"Linked ToDo: {doc.name}"

	_sync_to_event(
		source_doc=doc,
		subject=subject,
		starts_on=starts_on,
		ends_on=ends_on,
		description=description
	)

def _sync_to_event(source_doc, subject, starts_on, ends_on, description):
	"""
	Internal helper to create or update the Event.
	"""
	# Check if an event already exists for this document
	# We use the description to store the Link ID as a simple identifier
	existing_event_name = frappe.db.get_value("Event", {"description": description}, "name")

	if existing_event_name:
		# Update existing event
		event = frappe.get_doc("Event", existing_event_name)
		event.subject = subject
		event.starts_on = starts_on
		event.ends_on = ends_on
		# status mapping could be added here (e.g. if Task is Closed, close Event)
		if source_doc.status in ["Closed", "Cancelled", "Completed"]:
			event.status = "Closed"
		else:
			event.status = "Open"
		
		event.save(ignore_permissions=True)
		# frappe.msgprint(f"Updated Calendar Event for {source_doc.doctype}")
	
	else:
		# Create new event
		# Only create if the source doc is not closed/cancelled
		if source_doc.status in ["Closed", "Cancelled", "Completed"]:
			return

		new_event = frappe.get_doc({
			"doctype": "Event",
			"subject": subject,
			"starts_on": starts_on,
			"ends_on": ends_on,
			"status": "Open",
			"event_type": "Private", # Change to 'Public' if you want others to see it
			"description": description, 
			"sync_with_google_calendar": 1, # Important for Google Sync
			"owner": source_doc.owner # Assign event to the document owner
		})
		new_event.insert(ignore_permissions=True)
		frappe.msgprint(f"Calendar Event created for {source_doc.doctype}")
