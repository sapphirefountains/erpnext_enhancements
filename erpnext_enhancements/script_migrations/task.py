"""Migrated Task Client/Server Scripts, wired via ``hooks.py`` doc_events["Task"].

Hook wiring (see ``hooks.py``):
  * ``before_save`` -> :func:`calculate_project_elapsed_time`
  * ``on_update`` (one of several) -> :func:`sync_project_dates_from_tasks`
  * ``on_trash`` -> :func:`sync_project_dates_from_tasks`

These were originally Frappe "Server Script" records stored only in the site DB;
they now ship with the app for version control.

A fourth migrated script, "Sync All Tasks to Shared Google Calendar"
(``after_insert`` -> ``sync_task_to_google_calendar``), was **removed in v1.346.0**
as a product decision, not a cleanup: it pushed *every* Task site-wide into one
shared calendar with no per-person filtering — whoever the calendar was shared
with saw everyone's tasks — and it had never actually delivered an event since the
Server Script migration (it called a Frappe function that does not exist; the call
was fixed in v1.344.4 and the feature retired before the disabled "ERPNext Tasks"
Google Calendar account was ever re-enabled). A personalised replacement
(per-assignee events in each person's own calendar) is a deliberate build — per-user
OAuth grants or a calendar DWD scope, plus the update/cancel lifecycle the original
never had — and lives in git history if wanted. See CHANGELOG 1.346.0.
"""

import frappe

# Task Server Scripts migrated to native doc_events.


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
