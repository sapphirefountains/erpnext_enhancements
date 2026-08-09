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
#
# The *calendar id* is the one that matters: :func:`_shared_calendar_account`
# resolves it to the `Google Calendar` record that carries the OAuth grant. The
# *user* is no longer a parameter of the push — v16 takes the account from that
# record — but it is kept because it records which Google account the grant was
# established under, which is the first thing to check when the push stops. The
# Google Calendar tile in ``api/integrations_health.py`` reports the record's
# actual `user` against it.
GOOGLE_SYNC_USER_EMAIL = "nikolas.bradshaw@sapphirefountains.com"
GOOGLE_SHARED_CALENDAR_ID = (
	"c_bbb30adaf74985f859d192c1a3324a13b16251267c64a3b6917908b586e9cd67@group.calendar.google.com"
)


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


def _shared_calendar_account():
	"""The ``Google Calendar`` record that pushes to :data:`GOOGLE_SHARED_CALENDAR_ID`,
	or None when there is nothing to push through.

	``Event.google_calendar`` is a **Link to Frappe's ``Google Calendar`` doctype**,
	not a Google calendar id. The id lives on that record as ``google_calendar_id``,
	and ``insert_event_in_google_calendar`` reads it back through the Event's fetched
	``google_calendar_id`` to address the API call. So the module constant has to be
	resolved to a *record name* before an Event can carry it — passing the raw id
	would fail Frappe's own ``frappe.db.exists("Google Calendar", ...)`` guard and
	return without pushing anything.

	Returns None — with no Error Log row and no comment on the Task — when the record
	is missing or when ``enable`` / ``push_to_google_calendar`` are off. That is a
	site-configuration state, not a per-Task failure, and one row per Task created
	site-wide is exactly the noise :func:`log_error_throttled` exists to stop. The
	"Google Calendar (Task sync)" tile in ``api/integrations_health.py`` reports it
	instead, which is the difference between "off" being visible and "off" being
	invisible for two months.

	``get_all``, not ``get_list``: this runs inside whichever user created the Task,
	and ordinary users have no read permission on ``Google Calendar``. A
	permission-checked query would come back empty and turn a working sync into a
	silent no-op for everyone except System Managers.
	"""
	try:
		accounts = frappe.get_all(
			"Google Calendar",
			filters={"google_calendar_id": GOOGLE_SHARED_CALENDAR_ID},
			fields=["name", "enable", "push_to_google_calendar"],
			limit_page_length=1,
		)
	except Exception:
		# doc_events fire during ERPNext's test bootstrap, before the site is fully
		# built. A missing table must not crash a Task insert.
		return None

	if not accounts:
		return None

	account = accounts[0]
	if not account.enable or not account.push_to_google_calendar:
		return None

	return account.name


def sync_task_to_google_calendar(doc, method=None):
	"""Source Server Script: "Sync All Tasks to Shared Google Calendar"
	(Task, After Save).

	On creation of a Task, push it as an all-day event to a single shared Google
	Calendar. Wired to after_insert (the original ran in After Save guarded by
	doc.is_new()).

	**Why this creates an ``Event`` instead of calling the integration directly.**
	Until v1.262.0 this called
	``frappe.integrations.doctype.google_calendar.google_calendar.insert_event``
	with a hand-built ``{"doctype": "Google Calendar Event", ...}`` dict. Neither of
	those exists. Frappe v16 has no ``insert_event`` — its module-level event
	functions are ``insert_event_to_calendar`` (Google → Frappe) and
	``insert_event_in_google_calendar`` (Frappe → Google) — and there has never been
	a "Google Calendar Event" doctype; the carrier doctype is ``Event``. So this was
	not a rename, and every Task created raised ``AttributeError: module ... has no
	attribute 'insert_event'`` into the throttled log: 547 rows from 2026-06-08 to
	the fix, none of them visible to anyone, because the ``except`` below caught it
	and the throttle kept the volume too low to notice.

	Frappe's supported outbound path is an ``Event`` with ``sync_with_google_calendar``
	set and a ``google_calendar`` link; Frappe's own ``Event`` ``after_insert`` hook
	(``frappe/hooks.py``) then calls ``insert_event_in_google_calendar``. Building
	that Event *is* the job here. It also keeps the sync on a code path Frappe tests
	and documents, rather than on a private function that can be renamed under us
	again without a deprecation.
	"""
	try:
		calendar = _shared_calendar_account()
		if not calendar:
			return

		start_date = _calendar_date(doc.exp_start_date) or _calendar_date(doc.creation) or today()
		# A Task with no expected end is a single-day event on its start date.
		end_date = _calendar_date(doc.exp_end_date) or start_date
		# A backdated Task can end before the ``creation`` we fall back to, and
		# Event.validate throws on ends_on < starts_on — which would turn a harmless
		# date oddity into a logged sync failure.
		if getdate(end_date) < getdate(start_date):
			end_date = start_date

		event = frappe.get_doc(
			{
				"doctype": "Event",
				"subject": doc.subject,
				"description": doc.description or "No description provided.",
				# Private keeps this off every other user's desk Calendar, and has no
				# bearing on the Google side — who can see the shared calendar is
				# decided by that calendar's own sharing. Public would only duplicate,
				# inside ERPNext, a calendar the team already reads in Google.
				"event_type": "Private",
				# Frappe's daily digest (`send_event_digest`) mails every Event whose
				# send_reminder is set, and the field defaults to 1. This sync exists
				# to fill a Google calendar, not to open a new mail flow; Google sends
				# its own reminders according to the calendar's settings.
				"send_reminder": 0,
				# `all_day`, not a timed event: Task's expected start/end are Date
				# fields with no time component, and `all_day` is precisely what makes
				# Frappe emit Google's `start.date`/`end.date` pair instead of
				# `dateTime` — Google rejects a bare `YYYY-MM-DD` in a `dateTime` slot
				# (it wants a full RFC 3339 timestamp with an offset). An all-day event
				# is also the honest representation — a task due Thursday is not a task
				# due 00:00 Thursday.
				"all_day": 1,
				"starts_on": f"{start_date} 00:00:00",
				# Google treats an all-day `end.date` as *exclusive*, so a task that
				# runs through the 6th ends on the 7th. Frappe passes `ends_on`'s date
				# component straight into `end.date`, so the +1 still has to be applied
				# here. Without it every task rendered a day short, and a single-day
				# task rendered as zero-length and vanished from the calendar grid
				# entirely.
				"ends_on": f"{_calendar_date(add_days(end_date, 1))} 00:00:00",
				"sync_with_google_calendar": 1,
				"google_calendar": calendar,
				"reference_doctype": "Task",
				"reference_docname": doc.name,
			}
		)

		# Frappe's Event after_insert hook is what talks to Google, and it msgprints
		# "Event Synced with Google Calendar." on success. Muting keeps a modal off
		# every Task save — and off all 69 of a bulk create — without losing anything:
		# the comment below records the success, and `frappe.throw` still raises
		# through a mute, so a failure still lands in `except` with its message.
		muted = frappe.flags.mute_messages
		frappe.flags.mute_messages = True
		try:
			event.insert(ignore_permissions=True)
		finally:
			frappe.flags.mute_messages = muted

		doc.add_comment(
			"Comment",
			text=f"This task was successfully synced to the shared Google Calendar: {GOOGLE_SHARED_CALENDAR_ID}",
			comment_by="Administrator",
		)

	except Exception as e:
		# Throttled: this fires from an after_insert hook, so a systemic failure
		# (a revoked token, a deleted calendar) writes one row per Task created
		# site-wide until someone notices.
		#
		# "Until someone notices" turned out to be two months. The throttle is still
		# the right call — 547 identical rows told nobody anything the first four
		# would not have — but it is a *volume* control, not a monitor, and on its
		# own it made a dead integration quieter rather than louder. The monitor is
		# the Google Calendar tile in api/integrations_health.py, which counts these
		# rows and, more usefully, counts Tasks created against Events pushed. Keep
		# the title stable: that tile matches on it.
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
