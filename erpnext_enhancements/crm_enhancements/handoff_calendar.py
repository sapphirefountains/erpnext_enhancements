# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The hand-off and launch meetings as real Google Calendar events (v1.733.0).

Until v1.733.0 a booked meeting was an ERPNext Event plus an email carrying a
``METHOD:PUBLISH`` ``.ics``, so every invitee got their own private copy: no other
guests, no RSVP, no Meet link. Nik, 2026-10-23: "that invite doesn't show up as a
typical event ... work like an event with invites and has the Google Meet link
added". So the meeting is now created **by Google**, on an organizer's calendar,
with every attendee as a guest and ``sendUpdates="all"``. Google sends the invites
and collects the answers, and this app only stores the event id.

**Who organizes** (Nik's choice): the person who clicks Schedule, once they have
connected their own Google Calendar. Otherwise the shared calendar named in
**ERPNext Enhancements Settings → Hand-Off Fallback Google Calendar**. Otherwise
the old email, now a ``METHOD:REQUEST`` invite (``handoff._send_invite``). Each
organizer that fails is logged and reported, and the next one is tried; a Google
failure never rolls the step back.

**Frappe's plumbing for OAuth, our own calls for the event.** The OAuth flow and
the tokens are Frappe v16's own ``Google Calendar`` doctype
(``authorize_access``, ``get_google_calendar_object``). The event itself is not
pushed through Frappe's ``Event`` hooks, for two reasons. Its ``get_attendees``
reads only Event Participants rows, which need an Employee or Contact behind every
address, so a group address like ``production@`` could never be a guest. And its
hooks ``msgprint`` "Event Synced with Google Calendar." on every save. So
``sync_with_google_calendar`` stays **0**, which keeps those hooks inert, and this
module calls ``events().insert/patch/delete`` itself.

Do not set ``sync_with_google_calendar`` on these Events: Frappe's ``on_update``
hook would then push a second copy built from the participants grid.

The Event does carry ``google_calendar``, ``google_calendar_id`` and
``google_calendar_event_id``. Frappe's ``on_trash`` hook reads them, so deleting
the Event in ERPNext also takes the Google event off the calendar. All three are
always written together: that hook calls Google with ``calendarId`` straight off
the row, and a blank one would stop the Event being deleted at all.
"""

import frappe
from frappe import _
from frappe.utils import get_datetime

#: The new Settings field. A Link with no default, so the Single-default trap in
#: CLAUDE.md does not apply: an existing site reads ``None``, which is right.
FALLBACK_SETTING = "handoff_fallback_google_calendar"

#: Which organizer a booking used. Reported to the dialog as ``calendar_path``.
PATH_OWN = "own_calendar"
PATH_FALLBACK = "fallback_calendar"
PATH_ORGANIZER = "original_organizer"
PATH_EMAIL = "email"

#: How long a guest list read from Google is reused. Short: the point of the list
#: is to see answers come in, but a form that re-renders on every save must not
#: cost a Google call each time.
GUEST_CACHE_SECONDS = 120

#: Frappe's OAuth callback. Google only accepts a redirect it has been told about,
#: so this exact URL must be on the Google Cloud OAuth client. It is the site root
#: with ``?cmd=``, not ``/api/method/``, because that is how v16's
#: ``authorize_access`` builds it.
CALLBACK_METHOD = "frappe.integrations.doctype.google_calendar.google_calendar.google_callback"

#: What Google's status codes usually mean here, in words a user can act on.
#: Plain strings, translated where they are used: ``_()`` at import time runs
#: before any request has a language.
STATUS_HINTS = {
	400: "Google rejected the request",
	401: "the Google authorization has expired or was revoked; reconnect the calendar",
	403: "Google refused: no access to that calendar, or the daily quota is used up",
	404: "the event or calendar no longer exists in Google",
	410: "the event was already deleted in Google",
	429: "Google is rate-limiting requests; try again in a minute",
}


class GoogleCalendarError(Exception):
	"""One Google step failed.

	Carries a summary that is safe to show and to log: Google's HTTP status, or the
	exception's class name. Never the exception's own text, which for a network
	error can contain the request URL.
	"""

	def __init__(self, summary, status=None, connecting=False):
		super().__init__(summary)
		self.status = status
		#: True when the calendar could not be reached at all (a bad or revoked
		#: token). Another call on the same calendar would fail the same way.
		self.connecting = connecting


# ---------------------------------------------------------------------------
# Organizer resolution
# ---------------------------------------------------------------------------


def _authorized(name):
	"""True when Google Calendar ``name`` can organize a meeting.

	It must be enabled, pushing, and authorized. A record that was created but never
	authorized has no refresh token, and Frappe would raise "Click on Allow Google
	Calendar Access" on the first call.
	"""
	if not name:
		return False
	row = frappe.db.get_value(
		"Google Calendar",
		name,
		["enable", "push_to_google_calendar", "refresh_token"],
		as_dict=True,
	)
	return bool(row and row.get("enable") and row.get("push_to_google_calendar") and row.get("refresh_token"))


def own_calendar(user=None):
	"""The user's own enabled, authorized Google Calendar, or ``None``."""
	user = user or frappe.session.user
	names = frappe.get_all(
		"Google Calendar",
		filters={"user": user, "enable": 1, "push_to_google_calendar": 1},
		pluck="name",
		order_by="modified desc",
	)
	for name in names:
		if _authorized(name):
			return name
	return None


def fallback_calendar():
	"""The shared organizer calendar from Settings, when it is usable."""
	try:
		name = frappe.db.get_single_value("ERPNext Enhancements Settings", FALLBACK_SETTING)
	except Exception:
		# Before migrate adds the field, v16 raises for a field the meta lacks.
		return None
	return name if _authorized(name) else None


def organizers(user=None):
	"""``[(path, calendar), ...]`` in the order a new invite tries them."""
	out = []
	own = own_calendar(user)
	if own:
		out.append((PATH_OWN, own))
	fallback = fallback_calendar()
	if fallback and fallback != own:
		out.append((PATH_FALLBACK, fallback))
	return out


def _path_of(calendar, user=None):
	"""How a stored organizer calendar relates to the person clicking."""
	if calendar and calendar == own_calendar(user):
		return PATH_OWN
	if calendar and calendar == fallback_calendar():
		return PATH_FALLBACK
	return PATH_ORGANIZER


# ---------------------------------------------------------------------------
# Talking to Google
# ---------------------------------------------------------------------------


def _describe(exc):
	"""``(summary, status)`` for a failed Google step, without the exception's text."""
	status = getattr(getattr(exc, "resp", None), "status", None)
	try:
		status = int(status) if status is not None else None
	except (TypeError, ValueError):
		status = None
	if status:
		hint = _(STATUS_HINTS[status]) if status in STATUS_HINTS else None
		summary = _("Google returned HTTP {0}").format(status)
		return (f"{summary} ({hint})" if hint else summary), status
	name = type(exc).__name__
	if name == "RefreshError":
		return _(STATUS_HINTS[401]), 401
	if isinstance(exc, getattr(frappe, "ValidationError", ())):
		# Frappe's own refusals inside get_google_calendar_object: Google Settings
		# disabled, or the record has no refresh token yet.
		return _("the Google Calendar is not set up or not authorized yet"), None
	return _("{0} while calling Google").format(name), None


def _call(what, fn, connecting=False):
	"""Run one Google step and return its result.

	Any failure becomes a :class:`GoogleCalendarError` raised ``from None``, so
	neither the original frames nor its message travel any further. The step runs
	with ``mute_messages`` on: Frappe's helpers ``frappe.throw`` on a bad calendar,
	and a throw queues its message *before* raising, so even a caught failure would
	still pop a red modal in the browser.
	"""
	flags = getattr(frappe, "flags", None)
	muted = getattr(flags, "mute_messages", False) if flags is not None else False
	if flags is not None:
		flags.mute_messages = True
	try:
		return fn()
	except Exception as exc:
		summary, status = _describe(exc)
		failure = GoogleCalendarError(f"{what}: {summary}", status=status, connecting=connecting)
	finally:
		if flags is not None:
			flags.mute_messages = muted
	raise failure from None


def _google_service(calendar):
	"""``(service, calendar_id)`` for Google Calendar ``calendar``.

	Frappe's own ``get_google_calendar_object`` refreshes the access token from the
	stored refresh token and checks the calendar still exists. If the record has no
	Calendar ID it creates a secondary calendar named after the record, which is
	why :func:`connect_google_calendar` sets ``primary``.
	"""

	def connect():
		from frappe.integrations.doctype.google_calendar.google_calendar import (
			get_google_calendar_object,
		)

		service, account = get_google_calendar_object(calendar)
		return service, account.google_calendar_id

	return _call(_("Connecting to Google Calendar {0}").format(calendar), connect, connecting=True)


def _body(event, attendees, description, with_meet):
	"""The Google event for ERPNext ``event``, with every address as a guest."""
	# Imported here: the bench-free suites that load handoff.py stub frappe.utils thinly.
	from frappe.utils import get_system_timezone

	tz = get_system_timezone()
	body = {
		"summary": event.subject,
		"description": description or "",
		# A naive site-local time plus the site's zone. Google applies the zone, so
		# the meeting lands at the wall-clock time the dialog showed.
		"start": {"dateTime": _wall_clock(event.starts_on), "timeZone": tz},
		"end": {"dateTime": _wall_clock(event.ends_on or event.starts_on), "timeZone": tz},
		"attendees": [{"email": address} for address in attendees],
		"guestsCanSeeOtherGuests": True,
	}
	if with_meet:
		body["conferenceData"] = {
			"createRequest": {
				# Unique per request: Google ignores a repeated requestId.
				"requestId": f"{frappe.scrub(event.name)}-{frappe.generate_hash(length=10)}",
				"conferenceSolutionKey": {"type": "hangoutsMeet"},
			}
		}
	return body


def _field(event, fieldname):
	"""``event.<fieldname>``, or ``None``: works on a Document and on any stand-in."""
	return getattr(event, fieldname, None)


def _wall_clock(value):
	return get_datetime(value).replace(microsecond=0, tzinfo=None).isoformat()


def _merge_attendees(current, wanted):
	"""The new guest list, keeping each remaining guest's answer.

	Patching ``attendees`` replaces the list, so sending bare addresses would reset
	everyone's RSVP on every re-schedule. Existing entries that are still wanted are
	sent back as Google returned them; new addresses are added; dropped ones go, and
	Google tells them the meeting was cancelled for them.
	"""
	wanted_keys = {address.lower() for address in wanted}
	merged = []
	seen = set()
	for entry in current or []:
		email = (entry.get("email") or "").strip()
		if email.lower() in wanted_keys and email.lower() not in seen:
			merged.append(entry)
			seen.add(email.lower())
	for address in wanted:
		if address.lower() not in seen:
			merged.append({"email": address})
			seen.add(address.lower())
	return merged


def _store(event_name, calendar, calendar_id, google_event):
	"""Write the Google ids onto the Event. Always all of them together; see the module docstring."""
	frappe.db.set_value(
		"Event",
		event_name,
		{
			"google_calendar": calendar,
			"google_calendar_id": calendar_id,
			"google_calendar_event_id": google_event.get("id"),
			"google_meet_link": google_event.get("hangoutLink") or "",
		},
		update_modified=False,
	)
	_forget_guests(event_name)


def _report(context, event_name, calendar, failure):
	"""Log one failed organizer and return the line the response carries."""
	frappe.log_error(
		title="Hand-off meeting: Google Calendar failed",
		message=f"{context}\nEvent: {event_name}\nGoogle Calendar: {calendar}\n{failure}",
		reference_doctype="Event",
		reference_name=event_name,
	)
	return f"{context}: {failure}"


def _insert(calendar, event, attendees, description):
	service, calendar_id = _google_service(calendar)
	created = _call(
		_("Creating the Google event"),
		lambda: service.events()
		.insert(
			calendarId=calendar_id,
			body=_body(event, attendees, description, with_meet=True),
			conferenceDataVersion=1,
			sendUpdates="all",
		)
		.execute(),
	)
	return calendar_id, created


def _patch(event, attendees, description):
	"""Move the existing Google event and update its guests, on its own organizer's calendar."""
	service, account_calendar_id = _google_service(_field(event, "google_calendar"))
	calendar_id = _field(event, "google_calendar_id") or account_calendar_id
	event_id = _field(event, "google_calendar_event_id")
	current = _call(
		_("Reading the Google event"),
		lambda: service.events().get(calendarId=calendar_id, eventId=event_id).execute(),
	)
	if (current or {}).get("status") == "cancelled":
		raise GoogleCalendarError(_("The Google event was cancelled in Google"), status=410)
	body = _body(event, [], description, with_meet=not (current or {}).get("hangoutLink"))
	body["attendees"] = _merge_attendees((current or {}).get("attendees"), attendees)
	updated = _call(
		_("Moving the Google event"),
		lambda: service.events()
		.patch(
			calendarId=calendar_id,
			eventId=event_id,
			body=body,
			conferenceDataVersion=1,
			sendUpdates="all",
		)
		.execute(),
	)
	return calendar_id, updated


def deliver(event, attendees, description, user=None):
	"""Put ``event`` on Google Calendar with ``attendees`` as guests, if any organizer can.

	An Event that already has a Google event is **moved**: patched on its own
	organizer's calendar, ``sendUpdates="all"``, so the same invite changes time
	instead of a second one arriving. Otherwise, or if that fails, the organizers
	from :func:`organizers` are tried in turn. Every failure is logged and reported
	in ``warnings``; none raises.

	Returns ``{"calendar_path", "google_calendar", "google_event_id", "meet_link",
	"html_link", "organizer", "warnings", "replaced"}``. ``calendar_path`` is
	``"email"`` when no organizer worked, and the caller then sends the email
	invite. ``replaced`` is true when an older Google invite could not be moved and
	a new one was made, so the old one may still be on guests' calendars.
	"""
	warnings = []
	dead = set()
	organizer = _field(event, "google_calendar")
	had_google = bool(_field(event, "google_calendar_event_id") and organizer)

	if had_google:
		try:
			calendar_id, google_event = _patch(event, attendees, description)
			_store(event.name, organizer, calendar_id, google_event)
			return _outcome(_path_of(organizer, user), organizer, google_event, warnings)
		except GoogleCalendarError as failure:
			warnings.append(
				_report(_("Could not move the existing Google invite"), event.name, organizer, failure)
			)
			if failure.connecting:
				dead.add(organizer)

	for path, calendar in organizers(user):
		if calendar in dead:
			continue
		try:
			calendar_id, google_event = _insert(calendar, event, attendees, description)
		except GoogleCalendarError as failure:
			warnings.append(_report(_("Could not create the Google invite"), event.name, calendar, failure))
			continue
		_store(event.name, calendar, calendar_id, google_event)
		outcome = _outcome(path, calendar, google_event, warnings)
		outcome["replaced"] = had_google
		if had_google:
			warnings.append(
				_(
					"The earlier Google invite could not be moved, so a new one was sent. Guests may see the old one until its organizer deletes it."
				)
			)
		return outcome

	if had_google:
		# Nothing could move or replace it, and the email that follows is a second
		# invite for the same meeting. Say so plainly rather than leave it to guess.
		warnings.append(
			_(
				"The Google invite could not be moved, so guests still have it at the old time. The new time went by email; delete the old Google event or reconnect the calendar and re-schedule."
			)
		)
	return {
		"calendar_path": PATH_EMAIL,
		"google_calendar": None,
		"google_event_id": None,
		"meet_link": None,
		"html_link": None,
		"organizer": None,
		"warnings": warnings,
		"replaced": False,
	}


def _outcome(path, calendar, google_event, warnings):
	return {
		"calendar_path": path,
		"google_calendar": calendar,
		"google_event_id": google_event.get("id"),
		"meet_link": google_event.get("hangoutLink"),
		"html_link": google_event.get("htmlLink"),
		"organizer": (google_event.get("organizer") or {}).get("email"),
		"warnings": warnings,
		"replaced": False,
	}


def cancel(event):
	"""Withdraw the Google invite for ``event``: delete it with ``sendUpdates="all"``.

	Returns ``{"google": bool, "cancelled": bool, "warning": str | None}``. An
	event Google has already lost (404, 410) counts as cancelled. Any other failure
	is logged and returned as ``warning``, never raised: a skip must still be
	recorded when Google is down.
	"""
	if not (_field(event, "google_calendar_event_id") and _field(event, "google_calendar")):
		return {"google": False, "cancelled": False, "warning": None}
	try:
		service, account_calendar_id = _google_service(_field(event, "google_calendar"))
		calendar_id = _field(event, "google_calendar_id") or account_calendar_id
		_call(
			_("Cancelling the Google event"),
			lambda: service.events()
			.delete(
				calendarId=calendar_id, eventId=_field(event, "google_calendar_event_id"), sendUpdates="all"
			)
			.execute(),
		)
	except GoogleCalendarError as failure:
		if failure.status not in (404, 410):
			return {
				"google": True,
				"cancelled": False,
				"warning": _report(
					_("Could not cancel the Google invite"),
					event.name,
					_field(event, "google_calendar"),
					failure,
				),
			}
	_forget_guests(event.name)
	return {"google": True, "cancelled": True, "warning": None}


# ---------------------------------------------------------------------------
# Guests and RSVPs, for the step card
# ---------------------------------------------------------------------------


def _guest_cache_key(event_name):
	return f"ee_handoff_gcal_guests::{event_name}"


def _forget_guests(event_name):
	try:
		frappe.cache.delete_value(_guest_cache_key(event_name))
	except Exception:
		pass


def meeting_summary(event_name):
	"""What the step card shows without calling Google: when, and the Meet link.

	``None`` for no Event. Read from the row, so a form load never costs an API
	call; the guest list is :func:`meeting_guests`, fetched on a click.
	"""
	if not event_name:
		return None
	row = frappe.db.get_value(
		"Event",
		event_name,
		["name", "starts_on", "status", "google_calendar_event_id", "google_meet_link"],
		as_dict=True,
	)
	if not row:
		return None
	return {
		"event": row.get("name") or event_name,
		"starts_on": str(row.get("starts_on") or ""),
		"status": row.get("status"),
		"google": bool(row.get("google_calendar_event_id")),
		"meet_link": row.get("google_meet_link") or "",
	}


@frappe.whitelist()
def meeting_guests(event):
	"""Each guest's RSVP, the Meet link and the Google event link, for the step card.

	Read from Google on demand and kept for :data:`GUEST_CACHE_SECONDS`. Permission
	follows the meeting's source document: whoever may read the Opportunity or
	Project may see who is invited to its meeting. A Google failure comes back as
	``error`` rather than raising, so a revoked token makes the card say so instead
	of popping an error.
	"""
	row = frappe.db.get_value(
		"Event",
		event,
		[
			"name",
			"reference_doctype",
			"reference_docname",
			"google_calendar",
			"google_calendar_id",
			"google_calendar_event_id",
			"google_meet_link",
		],
		as_dict=True,
	)
	if not row or row.get("reference_doctype") not in ("Opportunity", "Project"):
		frappe.throw(_("This is not a hand-off or launch meeting."), frappe.PermissionError)
	if not frappe.has_permission(row.reference_doctype, "read", doc=row.reference_docname):
		frappe.throw(_("Not permitted to read this meeting."), frappe.PermissionError)
	if not (row.get("google_calendar_event_id") and row.get("google_calendar")):
		return {"google": False}

	key = _guest_cache_key(event)
	try:
		cached = frappe.cache.get_value(key)
	except Exception:
		cached = None
	if cached:
		return cached

	try:
		service, account_calendar_id = _google_service(row.google_calendar)
		calendar_id = row.get("google_calendar_id") or account_calendar_id
		google_event = _call(
			_("Reading the Google event"),
			lambda: service.events()
			.get(calendarId=calendar_id, eventId=row.google_calendar_event_id)
			.execute(),
		)
	except GoogleCalendarError as failure:
		# Not logged: this is a read someone clicked, and the card shows the reason.
		return {"google": True, "error": str(failure), "meet_link": row.get("google_meet_link") or ""}

	google_event = google_event or {}
	meet_link = google_event.get("hangoutLink") or ""
	if meet_link and meet_link != row.get("google_meet_link"):
		# A Meet created asynchronously arrives after the insert returned.
		frappe.db.set_value("Event", event, "google_meet_link", meet_link, update_modified=False)
	result = {
		"google": True,
		"status": google_event.get("status"),
		"html_link": google_event.get("htmlLink") or "",
		"meet_link": meet_link or row.get("google_meet_link") or "",
		"organizer": (google_event.get("organizer") or {}).get("email"),
		"guests": [
			{
				"email": guest.get("email"),
				"name": guest.get("displayName") or "",
				"response": guest.get("responseStatus") or "needsAction",
				"organizer": bool(guest.get("organizer")),
			}
			for guest in google_event.get("attendees") or []
			if not guest.get("resource")
		],
	}
	try:
		frappe.cache.set_value(key, result, expires_in_sec=GUEST_CACHE_SECONDS)
	except Exception:
		pass
	return result


# ---------------------------------------------------------------------------
# "Connect my Google Calendar"
# ---------------------------------------------------------------------------


def redirect_uri():
	"""The OAuth redirect v16's ``authorize_access`` sends to Google, for messages."""
	from frappe.utils import get_request_site_address

	try:
		base = get_request_site_address(full_address=True)
	except Exception:
		base = frappe.utils.get_url()
	return f"{base}?cmd={CALLBACK_METHOD}"


def _google_enabled():
	try:
		settings = frappe.get_cached_doc("Google Settings")
	except Exception:
		return False
	return bool(settings.get("enable") and settings.get("client_id"))


@frappe.whitelist()
def calendar_status():
	"""Which way the next invite will go, for the schedule dialog's one-line note."""
	own = own_calendar()
	fallback = fallback_calendar()
	if own:
		path = PATH_OWN
	elif fallback:
		path = PATH_FALLBACK
	else:
		path = PATH_EMAIL
	return {
		"calendar_path": path,
		"own_calendar": own,
		"has_fallback": bool(fallback),
		"google_enabled": _google_enabled(),
		"redirect_uri": redirect_uri(),
	}


@frappe.whitelist()
def connect_google_calendar():
	"""Create (or reuse) the session user's Google Calendar and start Frappe's authorize flow.

	Returns ``{"url", "calendar"}``; the browser goes to ``url``, the user allows
	access, and Google sends them back to Frappe's callback, which stores the
	refresh token. Only ever for the session user: the record's ``user`` is never
	taken from the request.

	A record this creates is push-only. ``pull_from_google_calendar`` is **0**, so
	nobody's personal calendar is imported into ERPNext, and ``google_calendar_id``
	is ``primary``, so invites come from the person's own calendar. An existing
	record of theirs is reused as it is, apart from being switched on and given
	``primary`` when its Calendar ID is blank.

	Permission: v16 gives ``Desk User``, which every System User holds, ``if_owner``
	create and write on Google Calendar, which is exactly "your own". Frappe's
	``authorize_access`` checks write on the record again before it starts.
	"""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Log in first."), frappe.PermissionError)
	if not _google_enabled():
		frappe.throw(
			_(
				"Google sign-in is not set up on this site. Ask a System Manager to enable Google Settings with an OAuth client."
			)
		)

	name = frappe.db.get_value("Google Calendar", {"user": user}, "name")
	if name:
		doc = frappe.get_doc("Google Calendar", name)
		# A blank Calendar ID would make Frappe create a secondary calendar on the
		# first call, and that calendar, not the person, would organize the meeting.
		if (
			not doc.get("enable")
			or not doc.get("push_to_google_calendar")
			or not doc.get("google_calendar_id")
		):
			doc.enable = 1
			doc.push_to_google_calendar = 1
			doc.google_calendar_id = doc.get("google_calendar_id") or "primary"
			doc.save()
	else:
		doc = frappe.get_doc(
			{
				"doctype": "Google Calendar",
				# calendar_name is the record's (unique) name. The address is unique
				# per user; Frappe only uses the name to title a calendar it creates,
				# and with google_calendar_id set it creates none.
				"calendar_name": user,
				"user": user,
				"enable": 1,
				"pull_from_google_calendar": 0,
				"push_to_google_calendar": 1,
				"sync_as_public": 0,
				"google_calendar_id": "primary",
			}
		).insert()

	from frappe.integrations.doctype.google_calendar.google_calendar import authorize_access

	started = authorize_access(doc.name, reauthorize=True) or {}
	return {"url": started.get("url"), "calendar": doc.name, "redirect_uri": redirect_uri()}


# ---------------------------------------------------------------------------
# Cancelled in the Desk
# ---------------------------------------------------------------------------


def cancel_on_status_change(doc, method=None):
	"""Event ``on_update``: a hand-off or launch meeting set to Cancelled cancels its Google invite.

	This covers cancelling the Event by hand in the Desk. The app's own paths
	(Skip, re-schedule) call :func:`cancel` themselves and set
	``flags.ee_handoff_calendar`` so this does not run twice. Events Frappe syncs
	itself (``sync_with_google_calendar``) are left to Frappe's own hook.
	"""
	if getattr(doc, "flags", None) is not None and doc.flags.get("ee_handoff_calendar"):
		return
	if doc.get("status") != "Cancelled" or doc.get("sync_with_google_calendar"):
		return
	if doc.get("reference_doctype") not in ("Opportunity", "Project"):
		return
	if not doc.get("google_calendar_event_id"):
		return
	before = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
	if before is not None and before.get("status") == "Cancelled":
		return
	outcome = cancel(doc)
	if outcome.get("warning"):
		frappe.msgprint(outcome["warning"], indicator="orange", alert=True)
