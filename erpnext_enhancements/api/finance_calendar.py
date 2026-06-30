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

from erpnext_enhancements.api.finance_dashboard import _require_finance, _settings, _widget_enabled

CACHE_TTL_SECONDS = 1800  # 30 minutes


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

	try:
		from erpnext_enhancements.google_calendar.calendar_utils import fetch_upcoming_events

		events = fetch_upcoming_events(calendar_id, max_results=max_events)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Finance Calendar fetch failed")
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
