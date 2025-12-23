import frappe

def execute():
	"""
	Migrates data from 'Google Calendar Event Log' (child table)
	and 'custom_google_event_id' (legacy field)
	to the new 'Global Calendar Sync Log' DocType.
	"""

	# 1. Migrate from child tables
	# We need to check all DocTypes that might have the child table: Task, Project, ToDo, Event
	doctypes_to_check = ["Task", "Project", "ToDo", "Event"]

	for dt in doctypes_to_check:
		# Check if child table field exists in metadata
		if not frappe.get_meta(dt).has_field("google_calendar_events"):
			continue

		# Get all parent docs that have entries in the child table
		# Query the child table directly: `tabGoogle Calendar Event Log`
		# Note: The child table was also named 'Google Calendar Event Log' in the previous implementation
		# but it was a child table (istable=1). Now we have a proper DocType (istable=0).
		# If the previous implementation reused the name "Google Calendar Event Log" for the child table,
		# we need to be careful.

		# Based on memory/context, the previous child table was indeed 'Google Calendar Event Log'.
		# Since we are creating a new DocType with 'Global Calendar Sync Log', there is no name collision
		# with the NEW doctype, but we need to read from the OLD table.

		# If 'Google Calendar Event Log' was a child table, it's stored in `tabGoogle Calendar Event Log`.
		# We can just query it.

		if frappe.db.table_exists("Google Calendar Event Log"):
			logs = frappe.db.sql(f"""
				SELECT name, parent, parenttype, google_calendar, event_id
				FROM `tabGoogle Calendar Event Log`
				WHERE parenttype = '{dt}'
			""", as_dict=True)

			for log in logs:
				# Check if already migrated to avoid duplicates if run multiple times
				exists = frappe.db.exists("Global Calendar Sync Log", {
					"reference_doctype": log.parenttype,
					"reference_docname": log.parent,
					"google_calendar": log.google_calendar,
					"event_id": log.event_id
				})

				if not exists:
					new_log = frappe.get_doc({
						"doctype": "Global Calendar Sync Log",
						"reference_doctype": log.parenttype,
						"reference_docname": log.parent,
						"google_calendar": log.google_calendar,
						"event_id": log.event_id
					})
					new_log.insert(ignore_permissions=True)

	# 2. Migrate from legacy field 'custom_google_event_id'
	# This field held a single event ID (usually for the primary calendar/user's calendar)
	for dt in doctypes_to_check:
		if not frappe.get_meta(dt).has_field("custom_google_event_id"):
			continue

		# Find docs with this field set
		docs = frappe.db.get_all(dt, filters={"custom_google_event_id": ["is", "set"]}, fields=["name", "owner", "custom_google_event_id"])

		for doc in docs:
			if not doc.custom_google_event_id:
				continue

			# We need to guess which calendar this belongs to.
			# Usually it was the user's personal calendar or a default one.
			# We'll try to find the user's enabled calendar.
			user_calendar = frappe.db.get_value("Google Calendar", {"user": doc.owner, "enable": 1}, "name")

			if user_calendar:
				# Check existence
				exists = frappe.db.exists("Global Calendar Sync Log", {
					"reference_doctype": dt,
					"reference_docname": doc.name,
					"google_calendar": user_calendar,
					"event_id": doc.custom_google_event_id
				})

				if not exists:
					new_log = frappe.get_doc({
						"doctype": "Global Calendar Sync Log",
						"reference_doctype": dt,
						"reference_docname": doc.name,
						"google_calendar": user_calendar,
						"event_id": doc.custom_google_event_id
					})
					new_log.insert(ignore_permissions=True)

	frappe.db.commit()
