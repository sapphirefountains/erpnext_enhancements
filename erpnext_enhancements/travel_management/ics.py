# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Dependency-free iCalendar (RFC 5545) builder for travel itineraries.

Frappe v16 ships no ICS writer (the Event doctype and HRMS integrate with
Google Calendar via its API), so this hand-rolls the small subset we need:
``METHOD:PUBLISH`` calendars attached to travel emails, which mail clients
import on tap. Full ``METHOD:REQUEST`` organizer/attendee/SEQUENCE semantics
(live invite updates, RSVP) are deliberately out of scope for v1.

Format rules implemented: CRLF line endings, 75-octet line folding, text
escaping (backslash, comma, semicolon, newline), site-timezone → UTC
conversion for timed events, all-day events as ``VALUE=DATE`` (DTEND
exclusive). UIDs are STABLE — ``{trip}-{row}@{site}`` — so a re-sent
itinerary *updates* the recipient's existing calendar entries instead of
duplicating them.
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import get_datetime, get_system_timezone, getdate, now_datetime

PRODID = "-//Sapphire Fountains//erpnext_enhancements travel//EN"


def _escape(value):
	return (
		str(value)
		.replace("\\", "\\\\")
		.replace(";", "\\;")
		.replace(",", "\\,")
		.replace("\r\n", "\\n")
		.replace("\n", "\\n")
	)


def _fold(line):
	"""Fold a content line at 75 octets (RFC 5545 §3.1), continuation lines
	start with a single space. Splits on bytes, careful not to cut a UTF-8
	sequence in half."""
	encoded = line.encode("utf-8")
	if len(encoded) <= 75:
		return [line]

	parts = []
	limit = 75
	while encoded:
		if len(encoded) <= limit:
			parts.append(encoded)
			break
		cut = limit
		# don't split inside a multi-byte sequence (continuation bytes are 0b10xxxxxx)
		while cut > 0 and (encoded[cut] & 0xC0) == 0x80:
			cut -= 1
		parts.append(encoded[:cut])
		encoded = encoded[cut:]
		limit = 74  # continuation lines lose one octet to the leading space

	folded = [parts[0].decode("utf-8")]
	folded.extend(" " + p.decode("utf-8") for p in parts[1:])
	return folded


def _utc_stamp(value):
	"""Naive site-timezone datetime -> ``YYYYMMDDTHHMMSSZ``."""
	dt = get_datetime(value)
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=ZoneInfo(get_system_timezone()))
	return dt.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")


def _date_stamp(value):
	return getdate(value).strftime("%Y%m%d")


def build_ics(events, method="PUBLISH"):
	"""Serialize events to an iCalendar string.

	Each event dict: ``uid`` (required, stable), ``summary`` (required),
	``start`` / ``end`` (datetime or date strings), ``all_day`` (bool;
	all-day DTEND is made exclusive by adding a day), optional
	``description``, ``location``, ``url``.
	"""
	lines = [
		"BEGIN:VCALENDAR",
		"VERSION:2.0",
		f"PRODID:{PRODID}",
		"CALSCALE:GREGORIAN",
		f"METHOD:{method}",
	]
	dtstamp = _utc_stamp(now_datetime())

	for event in events:
		lines.append("BEGIN:VEVENT")
		lines.append(f"UID:{_escape(event['uid'])}")
		lines.append(f"DTSTAMP:{dtstamp}")
		if event.get("all_day"):
			lines.append(f"DTSTART;VALUE=DATE:{_date_stamp(event['start'])}")
			end = getdate(event.get("end") or event["start"]) + timedelta(days=1)
			lines.append(f"DTEND;VALUE=DATE:{_date_stamp(end)}")
		else:
			lines.append(f"DTSTART:{_utc_stamp(event['start'])}")
			if event.get("end"):
				lines.append(f"DTEND:{_utc_stamp(event['end'])}")
		lines.append(f"SUMMARY:{_escape(event['summary'])}")
		if event.get("description"):
			lines.append(f"DESCRIPTION:{_escape(event['description'])}")
		if event.get("location"):
			lines.append(f"LOCATION:{_escape(event['location'])}")
		if event.get("url"):
			lines.append(f"URL:{_escape(event['url'])}")
		lines.append("END:VEVENT")

	lines.append("END:VCALENDAR")

	folded = []
	for line in lines:
		folded.extend(_fold(line))
	return "\r\n".join(folded) + "\r\n"


def trip_events_for_traveler(trip_doc, traveler_row):
	"""Calendar events for one traveler: the trip span (all-day), each visible
	flight, and each hotel check-in. Segments pinned to a different single
	traveler are skipped."""
	site = getattr(frappe.local, "site", None) or "site"
	employee = traveler_row.employee

	def uid(row_name, suffix=""):
		return f"{trip_doc.name}-{row_name}{suffix}@{site}"

	def visible(row_traveler):
		return not row_traveler or row_traveler == employee

	events = [
		{
			"uid": uid(traveler_row.name, "-span"),
			"summary": f"Trip: {trip_doc.purpose}",
			"start": traveler_row.from_date or trip_doc.start_date,
			"end": traveler_row.to_date or trip_doc.end_date,
			"all_day": True,
			"description": f"Travel Trip {trip_doc.name} ({trip_doc.travel_type})",
			"url": frappe.utils.get_url("/itinerary"),
		}
	]

	for flight in trip_doc.flights:
		if not visible(flight.traveler) or not flight.departure_time:
			continue
		end = flight.arrival_time or (get_datetime(flight.departure_time) + timedelta(hours=2))
		description = f"Flight {flight.flight_number} ({flight.airline})"
		if flight.booking_reference:
			description += f"\nPNR: {flight.booking_reference}"
		events.append(
			{
				"uid": uid(flight.name),
				"summary": f"✈ {flight.flight_number} {flight.departure_airport or ''} → {flight.arrival_airport or ''}".strip(),
				"start": flight.departure_time,
				"end": end,
				"description": description,
				"location": flight.departure_airport,
			}
		)

	for stay in trip_doc.accommodations:
		if not visible(stay.traveler) or not stay.check_in_date:
			continue
		description = f"Hotel: {stay.hotel_lodging}"
		if stay.booking_confirmation:
			description += f"\nConfirmation: {stay.booking_confirmation}"
		if stay.check_out_date:
			description += f"\nCheck-out: {stay.check_out_date}"
		events.append(
			{
				"uid": uid(stay.name),
				"summary": f"🏨 Check-in: {stay.hotel_lodging}",
				"start": stay.check_in_date,
				"all_day": True,
				"description": description,
				"location": stay.address,
			}
		)

	return events


def trip_ics_attachment(trip_doc, traveler_row):
	"""``frappe.sendmail`` attachment dict for one traveler's trip calendar."""
	return {
		"fname": f"{frappe.scrub(trip_doc.name)}.ics",
		"fcontent": build_ics(trip_events_for_traveler(trip_doc, traveler_row)),
	}
