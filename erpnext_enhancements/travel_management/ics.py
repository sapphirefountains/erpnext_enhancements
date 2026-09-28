# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Dependency-free iCalendar (RFC 5545) builder for travel itineraries.

Frappe v16 ships no ICS writer (the Event doctype and HRMS integrate with
Google Calendar via its API), so this hand-rolls the small subset we need:
``METHOD:PUBLISH`` calendars attached to travel emails, which mail clients
import on tap. Full ``METHOD:REQUEST`` organizer/attendee semantics (RSVP)
are deliberately out of scope.

Format rules implemented: CRLF line endings, 75-octet line folding, text
escaping (backslash, comma, semicolon, newline), site-timezone → UTC
conversion for timed events, all-day events as ``VALUE=DATE`` (DTEND
exclusive). UIDs are STABLE — ``{trip}-{row}@{site}`` — so a re-sent
itinerary *updates* the recipient's existing calendar entries instead of
duplicating them.

**SEQUENCE and cancellations** (change alerts, ``change_alerts.py``). An event
may carry ``sequence`` (an int) and ``status`` (``"CANCELLED"``); without them
:func:`build_ics` writes exactly what it always has, which the CRM hand-off
invite relies on. Every travel calendar a trip email attaches carries
``SEQUENCE`` = the trip's last save as seconds since the epoch
(:func:`sequence_of`): a change alert's invite must outrank the one the person
already has, and a later itinerary email must not then look *older* than the
alert's, which a calendar that honors SEQUENCE would ignore. A booking taken
off someone's trip is sent under its old UID with ``STATUS:CANCELLED``.
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
	``description``, ``location``, ``url``, ``sequence`` (int: which version of
	the event this is, so a calendar replaces an older copy of the same UID) and
	``status`` (``"CANCELLED"`` takes the event off the calendar). An event
	without the last two is written exactly as before they existed.
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
		if event.get("sequence") is not None:
			lines.append(f"SEQUENCE:{int(event['sequence'])}")
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
		if event.get("status"):
			lines.append(f"STATUS:{_escape(event['status'])}")
		lines.append("END:VEVENT")

	lines.append("END:VCALENDAR")

	folded = []
	for line in lines:
		folded.extend(_fold(line))
	return "\r\n".join(folded) + "\r\n"


def _time_unknown(value):
	"""True for a datetime stored at exactly midnight.

	Plan a Trip stores a flight or drive whose time is not known yet at 00:00:00,
	because a Datetime column cannot hold a date alone. A midnight calendar event
	would tell the traveler to be somewhere at 12 AM, so those become all-day events
	on their date instead."""
	return str(value)[11:19] in ("", "00:00:00")


def _clock_text(value):
	"""'3:00 PM' from a Time value — a ``timedelta`` from the database, or 'HH:MM:SS'."""
	if hasattr(value, "total_seconds"):
		seconds = int(value.total_seconds()) % 86400
		hour, minute = seconds // 3600, seconds % 3600 // 60
	else:
		parts = str(value).split(":")
		hour, minute = int(parts[0]), int(parts[1])
	return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def _friendly(value):
	"""'Thu Oct 8, 10:00 AM' — a 12-hour time for a line a traveler reads. Built by
	hand because strftime's no-padding flags differ between Linux and Windows."""
	dt = get_datetime(value)
	if _time_unknown(value):
		return f"{dt:%a %b} {dt.day}"
	return f"{dt:%a %b} {dt.day}, {dt.hour % 12 or 12}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"


def event_uid(trip_name, row_name, suffix=""):
	"""The UID of the calendar event a trip row makes: ``{trip}-{row}@{site}`` for a booking,
	``{trip}-{traveler row}-span@{site}`` (``suffix="-span"``) for the person's trip. Spelled
	once, because a change alert has to name the UID an earlier email sent to cancel it."""
	site = getattr(frappe.local, "site", None) or "site"
	return f"{trip_name}-{row_name}{suffix}@{site}"


def sequence_of(trip_doc):
	"""``SEQUENCE`` for a trip's calendar events: the trip's last save in seconds since the
	epoch, or ``None`` when the document has no ``modified`` (one built in memory).

	It only grows, so every email about a trip carries a version at least as new as the
	last one sent: a change alert's updated invite replaces the entry the person already
	has, and a later itinerary email is never taken for an older copy. Seconds, not a
	counter, because nothing has to be stored to compute it; it fits a 32-bit integer until
	2038."""
	modified = getattr(trip_doc, "modified", None)
	if not modified:
		return None
	try:
		return int(get_datetime(modified).timestamp())
	except (TypeError, ValueError, OverflowError, OSError):
		return None


def trip_events_for_traveler(trip_doc, traveler_row, address_text=None):
	"""Calendar events for one traveler: the trip span (all-day), each visible
	flight, each hotel check-in, each rental, ride or drive with a pickup time, and
	each freight delivery (or pickup) window they receive. Every booking event
	carries its confirmation or tracking number when there is one. Segments pinned
	to a different single traveler are skipped.

	``address_text`` turns a room's ``address`` into the check-in's LOCATION. The row
	holds the Address record's *name* ("Harborview Suites-Billing"; it is fetched from
	the hotel's primary address, a Link), so the itinerary emails pass
	``api.travel._address_text``, which resolves it to the street address. Without it the
	value is used as stored."""
	# Imported here, not at the top: this module must stay importable under the bench-free
	# suites' minimal frappe stub, and ``views`` is the one place the link is spelled.
	from erpnext_enhancements.travel_management.views import itinerary_path

	employee = traveler_row.employee

	def uid(row_name, suffix=""):
		return event_uid(trip_doc.name, row_name, suffix)

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
			# This trip's page, not bare /itinerary (which opens whichever trip is current).
			"url": frappe.utils.get_url(itinerary_path(trip_doc.name)),
		}
	]

	for flight in trip_doc.flights:
		if not visible(flight.traveler) or not flight.departure_time:
			continue
		description = f"Flight {flight.flight_number} ({flight.airline})"
		if flight.booking_reference:
			description += f"\nPNR: {flight.booking_reference}"
		event = {
			"uid": uid(flight.name),
			"summary": f"✈ {flight.flight_number} {flight.departure_airport or ''} → {flight.arrival_airport or ''}".strip(),
			"description": description,
			"location": flight.departure_airport,
		}
		if _time_unknown(flight.departure_time):
			event.update(start=str(flight.departure_time)[:10], all_day=True)
		else:
			event.update(
				start=flight.departure_time,
				end=flight.arrival_time or (get_datetime(flight.departure_time) + timedelta(hours=2)),
			)
		events.append(event)

	for stay in trip_doc.accommodations:
		if not visible(stay.traveler) or not stay.check_in_date:
			continue
		description = f"Hotel: {stay.hotel_lodging}"
		if stay.booking_confirmation:
			description += f"\nConfirmation: {stay.booking_confirmation}"
		if getattr(stay, "check_in_time", None):
			description += f"\nCheck-in from: {_clock_text(stay.check_in_time)}"
		if stay.check_out_date:
			description += f"\nCheck-out: {stay.check_out_date}"
			if getattr(stay, "check_out_time", None):
				description += f" by {_clock_text(stay.check_out_time)}"
		events.append(
			{
				"uid": uid(stay.name),
				"summary": f"🏨 Check-in: {stay.hotel_lodging}",
				"start": stay.check_in_date,
				"all_day": True,
				"description": description,
				"location": address_text(stay.address) if address_text else stay.address,
			}
		)

	# Rentals, rides and drives. Before Plan a Trip these never reached the calendar at
	# all, so a rental's confirmation number was in nobody's pocket at the counter.
	for ride in getattr(trip_doc, "ground_transport", None) or []:
		if not visible(ride.traveler) or not ride.pickup_datetime:
			continue
		provider = ride.supplier or ride.vehicle or ride.transport_type
		description = f"{ride.transport_type}: {provider}" if provider != ride.transport_type else provider
		if ride.booking_reference:
			description += f"\nConfirmation: {ride.booking_reference}"
		if ride.return_datetime:
			description += f"\nReturn by: {_friendly(ride.return_datetime)}"
		if getattr(ride, "cargo", None):
			description += f"\nHauling: {ride.cargo}"
		event = {
			"uid": uid(ride.name),
			"summary": f"🚗 {provider}: {ride.pickup_location or '?'} → {ride.dropoff_location or '?'}",
			"description": description,
			"location": ride.pickup_location,
		}
		arrival = getattr(ride, "arrival_datetime", None)
		if _time_unknown(ride.pickup_datetime):
			event.update(start=str(ride.pickup_datetime)[:10], all_day=True)
		elif arrival and not _time_unknown(arrival) and get_datetime(arrival) > get_datetime(ride.pickup_datetime):
			# A drive with a known arrival spans the drive.
			event.update(start=ride.pickup_datetime, end=arrival)
		else:
			# One hour: the event marks the pickup, not the whole rental.
			event.update(
				start=ride.pickup_datetime,
				end=get_datetime(ride.pickup_datetime) + timedelta(hours=1),
			)
		events.append(event)

	# Freight: the delivery window (or the pickup window when no delivery is set), for
	# whoever receives it — the whole crew when nobody is named.
	for shipment in getattr(trip_doc, "freight", None) or []:
		if not visible(shipment.traveler):
			continue
		start = shipment.delivery_from or shipment.pickup_from
		if not start:
			continue
		end = shipment.delivery_to if shipment.delivery_from else shipment.pickup_to
		kind = "delivery" if shipment.delivery_from else "pickup"
		description = f"Freight {kind}: {shipment.carrier}"
		if shipment.contents:
			description += f"\n{shipment.contents}"
		if shipment.tracking_number:
			description += f"\nTracking: {shipment.tracking_number}"
		if shipment.ship_from:
			description += f"\nFrom: {shipment.ship_from}"
		if shipment.deliver_to:
			description += f"\nTo: {shipment.deliver_to}"
		event = {
			"uid": uid(shipment.name),
			"summary": f"📦 {shipment.carrier} {kind}",
			"description": description,
			"location": shipment.deliver_to if kind == "delivery" else shipment.ship_from,
		}
		if _time_unknown(start):
			event.update(start=str(start)[:10], all_day=True)
		elif end and not _time_unknown(end) and get_datetime(end) > get_datetime(start):
			event.update(start=start, end=end)
		else:
			event.update(start=start, end=get_datetime(start) + timedelta(hours=1))
		events.append(event)

	return events


def trip_ics_attachment(trip_doc, traveler_row, address_text=None):
	"""``frappe.sendmail`` attachment dict for one traveler's trip calendar
	(``address_text`` as :func:`trip_events_for_traveler`). Each event carries the trip's
	:func:`sequence_of`, so this invite is never older than a change alert's."""
	events = trip_events_for_traveler(trip_doc, traveler_row, address_text)
	sequence = sequence_of(trip_doc)
	if sequence is not None:
		events = [dict(event, sequence=sequence) for event in events]
	return {
		"fname": f"{frappe.scrub(trip_doc.name)}.ics",
		"fcontent": build_ics(events),
	}
