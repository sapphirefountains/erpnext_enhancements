"""Migrated Task Client/Server Scripts, wired via ``hooks.py`` doc_events["Task"].

Hook wiring (see ``hooks.py``):
  * ``before_save`` -> :func:`calculate_project_elapsed_time`
  * ``after_insert`` -> :func:`sync_task_to_google_calendar`
  * ``on_update`` (one of several) -> :func:`sync_project_dates_from_tasks`
  * ``on_trash`` -> :func:`sync_project_dates_from_tasks`

These were originally Frappe "Server Script" records stored only in the site DB;
they now ship with the app for version control.
"""

import frappe

# Task Server Scripts migrated to native doc_events.

# Configuration for the shared Google Calendar sync (was hard-coded in the
# original "Sync All Tasks to Shared Google Calendar" Server Script).
GOOGLE_SYNC_USER_EMAIL = "nikolas.bradshaw@sapphirefountains.com"
GOOGLE_SHARED_CALENDAR_ID = (
	"c_bbb30adaf74985f859d192c1a3324a13b16251267c64a3b6917908b586e9cd67@group.calendar.google.com"
)


def sync_task_to_google_calendar(doc, method=None):
	"""Source Server Script: "Sync All Tasks to Shared Google Calendar"
	(Task, After Save).

	On creation of a Task, push it as an event to a single shared Google Calendar.
	Wired to after_insert (the original ran in After Save guarded by doc.is_new()).
	"""
	try:
		event = {
			"doctype": "Google Calendar Event",
			"google_calendar": GOOGLE_SHARED_CALENDAR_ID,
			"summary": doc.subject,
			"description": doc.description or "No description provided.",
			"start": {
				"dateTime": doc.exp_start_date.isoformat()
				if doc.exp_start_date
				else doc.get_formatted("creation"),
			},
			"end": {
				"dateTime": doc.exp_end_date.isoformat()
				if doc.exp_end_date
				else doc.get_formatted("creation"),
			},
		}

		frappe.call(
			"frappe.integrations.doctype.google_calendar.google_calendar.insert_event",
			doc=event,
			user=GOOGLE_SYNC_USER_EMAIL,
		)

		doc.add_comment(
			"Comment",
			text=f"This task was successfully synced to the shared Google Calendar: {GOOGLE_SHARED_CALENDAR_ID}",
			comment_by="Administrator",
		)

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Google Calendar Sync Failed")
		doc.add_comment(
			"Comment",
			text=f"Failed to sync this task to Google Calendar. Please contact your system administrator. Error: {e}",
			comment_by="Administrator",
		)


def calculate_project_elapsed_time(doc, method=None):
	"""Source Server Script: "Calculate Project Elapsed Time" (Task, Before Save).

	When the last open task of a project is closed, complete the project and stamp
	its total elapsed time.
	"""
	if not doc.project:
		return

	if doc.status not in ["Completed", "Cancelled"]:
		return

	open_tasks_count = frappe.db.count(
		"Task",
		{
			"project": doc.project,
			"name": ("!=", doc.name),
			"status": ("not in", ["Completed", "Cancelled"]),
		},
	)

	if open_tasks_count != 0:
		return

	try:
		project = frappe.get_doc("Project", doc.project)
		if project.status == "Completed":
			return

		start_time = project.get("custom_zoho_creation_date") or project.creation
		completion_time = frappe.utils.now_datetime()
		time_difference_seconds = frappe.utils.time_diff_in_seconds(completion_time, start_time)

		project.custom_total_time_elapsed = time_difference_seconds
		project.status = "Completed"
		project.save(ignore_permissions=True)

		frappe.msgprint(
			f"All tasks for Project '{project.name}' are complete. Project status updated."
		)
	except frappe.DoesNotExistError:
		frappe.log_error(
			f"Project '{doc.project}' not found when closing task '{doc.name}'.",
			"Final Task Completion Script",
		)


def sync_project_dates_from_tasks(doc, method=None):
	"""Keep Project.expected_start_date / expected_end_date derived from the
	project's tasks now that those fields are read-only on the Project form:
	expected_start_date mirrors the earliest task's exp_start_date and
	expected_end_date mirrors the latest task's exp_end_date.

	Wired in ``hooks.py`` as a Task ``on_update`` and ``on_trash`` doc_event.

	Side effects:
		Writes Project.expected_start_date / expected_end_date via ``db_set``
		(with ``update_modified=False``) only when they differ from the computed
		min/max. No-op if ``doc.project`` is unset or the Project is missing.
	"""
	if not doc.project:
		return

	dates = frappe.db.sql(
		"""
		SELECT MIN(exp_start_date) AS start_date, MAX(exp_end_date) AS end_date
		FROM `tabTask`
		WHERE project = %s
		""",
		doc.project,
		as_dict=True,
	)[0]

	try:
		project = frappe.get_doc("Project", doc.project)
	except frappe.DoesNotExistError:
		return

	if (
		project.expected_start_date == dates.start_date
		and project.expected_end_date == dates.end_date
	):
		return

	project.db_set("expected_start_date", dates.start_date, update_modified=False)
	project.db_set("expected_end_date", dates.end_date, update_modified=False)
