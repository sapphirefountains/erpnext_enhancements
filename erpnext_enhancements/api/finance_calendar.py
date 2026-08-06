# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted feed for the Finance Calendar widget (Google Calendar).

Reads upcoming events from the configured "Finance" Google Calendar via the
shared Drive service account (see ``google_calendar/calendar_utils.py``). Results
are cached ~30 min so the widget is cheap and the Calendar API isn't hammered.
Defensive throughout: an unconfigured or unreachable calendar degrades to an
empty list + reason, never an error on the board.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime
from googleapiclient.errors import HttpError

from erpnext_enhancements.api.finance_dashboard import _require_finance, _settings, _widget_enabled
from erpnext_enhancements.utils.error_throttle import log_error_throttled

CACHE_TTL_SECONDS = 1800  # 30 minutes

# How long a *misconfiguration* is remembered. Much shorter than the success
# cache: an admin who has just fixed the calendar id should see the widget
# recover within a couple of minutes, not sit staring at a stale error for half
# an hour wondering whether the fix took.
NEGATIVE_CACHE_TTL = 120

# Google statuses that mean "this will never work as configured", mapped to what
# an operator should actually go and do about it. Anything else is treated as
# transient and retried on the next request.
_MISCONFIGURED_STATUSES = {
	403: (
		"The Finance calendar is not shared with the Drive service account. "
		"Share it with the service account address (Viewer is enough)."
	),
	404: (
		"The configured Finance calendar id does not exist, or is not shared with "
		"the Drive service account. Check Finance Settings > Finance Calendar ID."
	),
}


def _reason_key(cache_key: str) -> str:
	"""Cache slot for a remembered misconfiguration reason."""
	return f"{cache_key}::reason"


@frappe.whitelist()
def get_finance_calendar():
	"""Upcoming events from the Finance Google Calendar (cached)."""
	_require_finance()
	settings = _settings()
	if not _widget_enabled("finance_calendar_enabled", settings):
		return {"enabled": False}

	calendar_id = (settings.get("finance_calendar_id") or "").strip()
	if not calendar_id:
		return {"enabled": True, "events": [], "reason": "No calendar configured."}

	max_events = cint(settings.get("finance_calendar_max_events")) or 10
	cache_key = f"ee_finance_calendar::{calendar_id}::{max_events}"
	cached = frappe.cache().get_value(cache_key)
	if cached is not None:
		return {"enabled": True, "events": cached, "generated_at": str(now_datetime()), "cached": True}

	# A known-bad configuration short-circuits before we call Google at all.
	# Without this the widget re-asked on every dashboard load and every reload
	# was another 404 and another traceback.
	remembered = frappe.cache().get_value(_reason_key(cache_key))
	if remembered:
		return {"enabled": True, "events": [], "reason": remembered, "cached": True}

	try:
		from erpnext_enhancements.google_calendar.calendar_utils import fetch_upcoming_events

		events = fetch_upcoming_events(calendar_id, max_results=max_events)
	except HttpError as exc:
		status = getattr(getattr(exc, "resp", None), "status", None)
		if status in _MISCONFIGURED_STATUSES:
			# A permanent answer, not an outage: the calendar id is wrong, or it
			# was never shared with the Drive service account. Retrying cannot
			# fix either, so cache the refusal (briefly) and say which it is —
			# "Calendar service unavailable" sent people looking for a Google
			# outage that was not happening. Fourteen identical 404 tracebacks a
			# month came through here.
			reason = _MISCONFIGURED_STATUSES[status]
			frappe.cache().set_value(_reason_key(cache_key), reason, expires_in_sec=NEGATIVE_CACHE_TTL)
			log_error_throttled(
				f"Finance Calendar {calendar_id!r} returned {status}: {reason}\n{frappe.get_traceback()}",
				"Finance Calendar fetch failed",
				key=calendar_id,
			)
			return {"enabled": True, "events": [], "reason": reason}
		log_error_throttled(frappe.get_traceback(), "Finance Calendar fetch failed", key=calendar_id)
		return {"enabled": True, "events": [], "reason": "Calendar service unavailable."}
	except Exception:
		log_error_throttled(frappe.get_traceback(), "Finance Calendar fetch failed", key=calendar_id)
		return {"enabled": True, "events": [], "reason": "Calendar service unavailable."}

	frappe.cache().set_value(cache_key, events, expires_in_sec=CACHE_TTL_SECONDS)
	return {"enabled": True, "events": events, "generated_at": str(now_datetime())}


@frappe.whitelist()
def list_calendars():
	"""Discovery helper: calendars the service account can see (admin only)."""
	if not ({"System Manager", "Accounts Manager"} & set(frappe.get_roles())):
		frappe.throw(_("Not permitted."), frappe.PermissionError)
	from erpnext_enhancements.google_calendar.calendar_utils import list_calendars as _list

	return _list()
