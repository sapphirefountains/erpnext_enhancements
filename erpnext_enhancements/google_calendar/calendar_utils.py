# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Google Calendar v3 read helpers for the Finance Calendar widget.

Authenticates with the **same service account** the Google Drive automation
already uses (the JSON key on ``Project Folder Google Drive Settings``), scoped
read-only to Calendar. The target "Finance" calendar must be **shared with the
service account's client_email** (the same "add the SA as a member" requirement
as the Drive shared drive) and the Calendar API enabled in that GCP project.

Mirrors ``erpnext_enhancements/google_drive/drive_utils.get_drive_service``.
``google-api-python-client`` + ``google-auth`` are already app dependencies, so
no new package is needed.
"""

import json

import frappe
from frappe.utils import now_datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
DRIVE_SETTINGS_DOCTYPE = "Project Folder Google Drive Settings"


def get_calendar_service():
	"""Build an authenticated Google Calendar v3 service from the Drive service
	account JSON. Raises via ``frappe.throw`` if not configured."""
	settings = frappe.get_single(DRIVE_SETTINGS_DOCTYPE)
	if not settings.service_account_json:
		frappe.throw(
			f"Service Account JSON is not configured in {DRIVE_SETTINGS_DOCTYPE}; "
			"the Finance Calendar widget reuses it."
		)
	try:
		creds_info = json.loads(settings.service_account_json)
		creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
		return build("calendar", "v3", credentials=creds, cache_discovery=False)
	except Exception as e:
		frappe.throw(f"Failed to initialize Google Calendar service: {e!s}")


def list_calendars() -> list[dict]:
	"""Return ``[{id, summary}]`` for every calendar the service account can see —
	a discovery helper so the operator can find the "Finance" calendar id to paste
	into Settings."""
	service = get_calendar_service()
	result = service.calendarList().list().execute()
	return [
		{"id": item.get("id"), "summary": item.get("summary")}
		for item in (result.get("items") or [])
	]


def fetch_upcoming_events(calendar_id: str, max_results: int = 10) -> list[dict]:
	"""Return the next ``max_results`` upcoming events for ``calendar_id``.

	Single, expanded (recurring instances flattened), ordered by start time, from
	now forward. Each event is reduced to the display fields the widget renders.
	"""
	service = get_calendar_service()
	time_min = now_datetime().astimezone().isoformat()
	result = (
		service.events()
		.list(
			calendarId=calendar_id,
			timeMin=time_min,
			singleEvents=True,
			orderBy="startTime",
			maxResults=max_results,
		)
		.execute()
	)
	events = []
	for item in result.get("items") or []:
		start = item.get("start") or {}
		end = item.get("end") or {}
		all_day = bool(start.get("date") and not start.get("dateTime"))
		events.append(
			{
				"summary": item.get("summary") or "(no title)",
				"start": start.get("dateTime") or start.get("date") or "",
				"end": end.get("dateTime") or end.get("date") or "",
				"all_day": all_day,
				"location": item.get("location") or "",
				"html_link": item.get("htmlLink") or "",
			}
		)
	return events
