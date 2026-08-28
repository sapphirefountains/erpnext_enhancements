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
from frappe.utils import add_days, getdate, today

from erpnext_enhancements.utils.error_throttle import log_error_throttled

# Task Server Scripts migrated to native doc_events.

# Configuration for the shared Google Calendar sync (was hard-coded in the
# original "Sync All Tasks to Shared Google Calendar" Server Script).
GOOGLE_SHARED_CALENDAR_ID = (
	"c_bbb30adaf74985f859d192c1a3324a13b16251267c64a3b6917908b586e9cd67@group.calendar.google.com"
)


def _calendar_account():
	"""The enabled ``Google Calendar`` account for the shared Tasks calendar, or None.

	Looked up by calendar id rather than by doc name ("ERPNext Tasks" on prod) so a
	rename survives. Filtered on ``enable`` because a disabled account is the
	operator's off switch: while it is off the sync must skip quietly, not leave a
	red "contact your system administrator" comment on every Task created —
	which is what months of prod Tasks collected while the account sat disabled.
	"""
	return frappe.db.get_value(
		"Google Calendar",
		{"google_calendar_id": GOOGLE_SHARED_CALENDAR_ID, "enable": 1},
		"name",
	)


def _insert_calendar_event(account_name, body):
	"""Insert one event body into the account's Google calendar.

	Goes through frappe's Google Calendar integration — the account row carries the
	OAuth refresh token — but calls the Google API directly, because there is no
	framework helper for pushing an *arbitrary* event: the function the migrated
	Server Script named, ``google_calendar.insert_event``, does not exist in Frappe
	v16 (``insert_event_in_google_calendar`` is a doc_event for the Event doctype,
	which a Task is not). Every Task sync since the migration died here with
	AttributeError before this was rewritten.

	Deferred import: the integration module pulls in the Google client stack, and
	the common case above is skipping before this is ever needed.
	"""
	from frappe.integrations.doctype.google_calendar.google_calendar import (
		get_google_calendar_object,
	)

	google_calendar, account = get_google_calendar_object(account_name)
	google_calendar.events().insert(calendarId=account.google_calendar_id, body=body).execute()


def _calendar_date(value):
	"""One Task date field as ``YYYY-MM-DD``, or None if it is not set.

	``getdate`` is the whole point. A Task's ``exp_start_date`` is a
	``datetime.date`` once the row has been read back from the database, but on
	the in-memory doc that ``after_insert`` receives — built straight from the
	request payload — it is still a plain string. The original migrated Server
	Script called ``.isoformat()`` on it unconditionally, so every Task created
	through the desk raised ``AttributeError: 'str' object has no attribute
	'isoformat'`` and logged a "Google Calendar Sync Failed" row: 299 of them in
	the month before this was fixed, and 541 in total.

	The old ``else`` branch was wrong too, just more quietly:
	``doc.get_formatted("creation")`` returns a *display* string in the user's
	date format ("06-08-2026 16:09:03"), which Google would have rejected had
	the code ever reached it.
	"""
	date = getdate(value) if value else None
	return date.isoformat() if date else None


def sync_task_to_google_calendar(doc, method=None):
	"""Source Server Script: "Sync All Tasks to Shared Google Calendar"
	(Task, After Save).

	On creation of a Task, push it as an event to a single shared Google Calendar.
	Wired to after_insert (the original ran in After Save guarded by doc.is_new()).

	Skips silently when no *enabled* Google Calendar account exists for the shared
	calendar — see :func:`_calendar_account`. Re-enabling the account in the Desk
	turns the sync back on without a deploy.
	"""
	account_name = _calendar_account()
	if not account_name:
		return

	try:
		start_date = _calendar_date(doc.exp_start_date) or _calendar_date(doc.creation) or today()
		# A Task with no expected end is a single-day event on its start date.
		end_date = _calendar_date(doc.exp_end_date) or start_date

		event = {
			"summary": doc.subject,
			"description": doc.description or "No description provided.",
			# `date`, not `dateTime`: Task's expected start/end are Date fields with
			# no time component, and Google rejects a bare `YYYY-MM-DD` in a
			# `dateTime` slot (it wants a full RFC 3339 timestamp with an offset).
			# An all-day event is also the honest representation — a task due
			# Thursday is not a task due 00:00 Thursday.
			"start": {"date": start_date},
			# Google treats an all-day `end.date` as *exclusive*, so a task that
			# runs through the 6th ends on the 7th. Without the +1 every task
			# rendered a day short, and a single-day task rendered as zero-length
			# and vanished from the calendar grid entirely.
			# `add_days` keeps a string a string, so the body stays JSON-safe.
			"end": {"date": add_days(end_date, 1)},
		}

		_insert_calendar_event(account_name, event)

		doc.add_comment(
			"Comment",
			text=f"This task was successfully synced to the shared Google Calendar: {GOOGLE_SHARED_CALENDAR_ID}",
			comment_by="Administrator",
		)

	except Exception as e:
		# Throttled: this fires from an after_insert hook, so a systemic failure
		# (a revoked token, a deleted calendar) writes one row per Task created
		# site-wide until someone notices.
		log_error_throttled(frappe.get_traceback(), "Google Calendar Sync Failed")
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
