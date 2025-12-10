import frappe

def execute():
	"""
	Migrates legacy Google Calendar settings and Task event IDs to the new structure.
	"""
	migrate_settings()
	migrate_task_event_ids()

def migrate_settings():
	try:
		settings = frappe.get_single("ERPNext Enhancements Settings")
		if settings.default_google_calendar_config and not settings.google_calendar_sync_map:
			# Create a new row in the sync map
			row = settings.append("google_calendar_sync_map", {})
			row.reference_doctype = "Task"
			row.google_calendar = settings.default_google_calendar_config
			settings.save()
	except Exception as e:
		frappe.log_error(f"Migration Error (Settings): {e}", "Google Calendar Sync Migration")

def migrate_task_event_ids():
	try:
		# Find Tasks with legacy custom_google_event_id
		# Note: Since the child table field 'google_calendar_events' was just added,
		# we assume we can populate it.

		# We need to know which calendar the legacy ID belongs to.
		# Currently, it was either the Global Default or User Default.
		# This is tricky because we don't know FOR SURE which one was used for a specific task.
		# However, `calendar_sync.py` logic was: Global first, then User.

		# Let's get the Global setting again to see what it WAS.
		# But we just migrated it in `migrate_settings`.

		settings = frappe.get_single("ERPNext Enhancements Settings")
		# We assume the first row for Task in the new map is the likely candidate for the legacy global config.
		global_calendar = None
		for row in settings.google_calendar_sync_map:
			if row.reference_doctype == "Task":
				global_calendar = row.google_calendar
				break

		tasks = frappe.get_all("Task", filters={"custom_google_event_id": ["is", "set"]}, fields=["name", "owner", "custom_google_event_id"])

		for task_data in tasks:
			task = frappe.get_doc("Task", task_data.name)

			if task.google_calendar_events:
				continue # Already has entries? Skip.

			# Determine which calendar to assign
			assigned_calendar = global_calendar

			if not assigned_calendar:
				# Fallback to User's calendar as per old logic
				assigned_calendar = frappe.db.get_value("Google Calendar", {"user": task.owner, "enable": 1}, "name")

			if assigned_calendar and task.custom_google_event_id:
				task.append("google_calendar_events", {
					"google_calendar": assigned_calendar,
					"event_id": task.custom_google_event_id
				})
				# Disable validation/hooks during migration to prevent double sync attempts
				task.flags.ignore_validate = True
				task.flags.ignore_links = True
				# We just want to update the child table in DB.
				# Calling save() might trigger on_update hooks which we might not want yet.
				# But hooks are fine if they just sync.
				# However, since we are setting the ID, sync patch might run.
				# Ideally we just update DB.
				task.save(ignore_permissions=True)

	except Exception as e:
		frappe.log_error(f"Migration Error (Tasks): {e}", "Google Calendar Sync Migration")
