import frappe

# Project Server Scripts migrated to native doc_events / scheduler_events.


def remove_open_status(doc, method=None):
	"""Source Server Script: "Project - Remove Status 'Open'" (Project, Before Save).

	'Open' is not a desired Project status; coerce it to 'Active'. The original
	script used doc.db_set inside before_save, which the subsequent ORM save would
	overwrite; setting doc.status directly is the correct equivalent.
	"""
	if doc.status == "Open":
		doc.status = "Active"
		frappe.msgprint("Project status has been automatically moved from 'Open' to 'Active'.")


def update_elapsed_time_daily():
	"""Source Server Script: "Update Project Elapsed Time Daily" (Scheduler Event, Daily).

	Refresh custom_total_time_elapsed for every project that is not yet closed.
	"""
	now = frappe.utils.now_datetime()

	open_projects = frappe.get_list(
		"Project",
		filters={"status": ("not in", ["Completed", "Cancelled"])},
		fields=["name", "creation", "custom_zoho_creation_date"],
	)

	if not open_projects:
		frappe.logger().info("Project Elapsed Time Updater: No open projects to update.")
		return

	for project in open_projects:
		start_time = project.get("custom_zoho_creation_date") or project.creation
		time_difference_seconds = frappe.utils.time_diff_in_seconds(now, start_time)
		frappe.db.set_value(
			"Project",
			project.name,
			"custom_total_time_elapsed",
			time_difference_seconds,
			update_modified=False,
		)

	frappe.db.commit()
	frappe.logger().info(
		f"Project Elapsed Time Updater: Updated elapsed time for {len(open_projects)} open projects."
	)
