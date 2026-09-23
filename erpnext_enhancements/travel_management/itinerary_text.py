"""Plain-text lines for the travel emails: one line per itinerary item, with its
confirmation, tracking or PNR number, and times on a 12-hour clock.

Three emails list a traveler's bookings — the itinerary (also the pre-travel reminder),
"Trip booked" and "You were added to a trip" — and until v1.520.0 each worded them in its
own Jinja, the booked and added ones not at all: "Your bookings are confirmed" with no
booking and no number in sight. The lines are built here once, from the same
``shape_itinerary`` output the ``/itinerary`` page draws, so every email says the same thing
and ``tests/test_travel_planner.py`` can read them without a site.

Lines are plain text. ``ee.bullets`` escapes them on the way into the email.
"""

from datetime import date, datetime

from erpnext_enhancements.travel_management.completeness import UNBOOKED_TRANSPORT


def clock(value):
	"""'2:30 PM' from 'HH:MM[:SS]' or a 'YYYY-MM-DD HH:MM:SS' datetime.

	A datetime at exactly midnight means the time is not known yet (Plan a Trip stores a
	flight or drive without a time at 00:00:00), so it prints nothing; a bare Time of
	00:00 is a real midnight."""
	if not value:
		return ""
	text = str(value)
	is_datetime = len(text) > 8 and text[4:5] == "-"
	part = text[11:19] if is_datetime else text
	pieces = part.split(":")
	if len(pieces) < 2:
		return ""
	try:
		hour, minute = int(pieces[0]), int(pieces[1])
	except ValueError:
		return ""
	if is_datetime and hour == 0 and minute == 0:
		return ""
	return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def span(start, end):
	a, b = clock(start), clock(end)
	if a and b:
		return f"{a} – {b}"
	return a or b


def pretty_date(value):
	"""'Mon Oct 5' — built by hand: strftime's no-padding flags differ between platforms."""
	if not value:
		return ""
	if isinstance(value, datetime):
		value = value.date()
	if not isinstance(value, date):
		value = date.fromisoformat(str(value)[:10])
	return f"{value:%a %b} {value.day}"


def _who(item):
	return f" (with {', '.join(item['travelers'])})" if item.get("travelers") else ""


#: What a line says when its number is not in yet.
MISSING = {"PNR": "no PNR yet", "Confirmation": "no confirmation number yet", "Tracking": "no tracking number yet"}


def _number(label, value, missing=True):
	if value:
		return f" · {label} {value}"
	return f" · {MISSING[label]}" if missing else ""


def item_line(item, dated=False):
	"""One line for one itinerary item, or ``None`` for a type this does not describe."""
	kind = item.get("type")
	on = f", {pretty_date(item.get('date'))}" if dated else ""
	if kind == "flight":
		when = clock(item.get("departure_time"))
		return (
			f"✈ {item.get('airline') or ''} {item.get('flight_number') or ''}".rstrip()
			+ f" {item.get('departure_airport') or '?'} → {item.get('arrival_airport') or '?'}"
			+ on
			+ (f", departs {when}" if when else "")
			+ _number("PNR", item.get("booking_reference"))
			+ _who(item)
		)
	if kind == "hotel_checkin":
		when = clock(item.get("time"))
		return (
			f"🏨 Check-in: {item.get('hotel') or ''}"
			+ on
			+ (f", from {when}" if when else "")
			+ _number("Confirmation", item.get("booking_confirmation"))
			+ _who(item)
		)
	if kind == "hotel_checkout":
		when = clock(item.get("time"))
		return f"🏨 Check-out: {item.get('hotel') or ''}" + on + (f", by {when}" if when else "")
	if kind == "ground":
		booked = item.get("transport_type") not in UNBOOKED_TRANSPORT
		when = span(item.get("pickup_datetime"), item.get("arrival_datetime"))
		return (
			f"🚗 {item.get('provider') or item.get('transport_type') or 'Ground transport'}: "
			f"{item.get('pickup_location') or '?'} → {item.get('dropoff_location') or '?'}"
			+ on
			+ (f", {when}" if when else "")
			+ (f" · hauling {item['cargo']}" if item.get("cargo") else "")
			+ _number("Confirmation", item.get("booking_reference"), missing=booked)
			+ _who(item)
		)
	if kind == "freight":
		window = span(item.get("delivery_from"), item.get("delivery_to"))
		return (
			f"📦 Freight: {item.get('carrier') or ''}"
			+ (f" — {item['contents']}" if item.get("contents") else "")
			+ on
			+ (f", delivers {window}" if window else "")
			+ (f" to {item['deliver_to']}" if item.get("deliver_to") else "")
			+ _number("Tracking", item.get("tracking_number"))
			+ (f" (received by {item['received_by']})" if item.get("received_by") else "")
		)
	if kind == "agenda":
		when = span(item.get("time"), item.get("end_time"))
		place = (item.get("poi") or {}).get("poi_name")
		return (
			"📍 "
			+ (f"{when} · " if when else "")
			+ (item.get("activity") or "")
			+ (f" · {place}" if place else "")
		)
	return None


def day_lines(itinerary):
	"""``[{date, lines}]`` for the itinerary email, one bullet list per day."""
	out = []
	for day in itinerary.get("days") or []:
		lines = [line for line in (item_line(item) for item in day.get("items") or []) if line]
		if lines:
			out.append({"date": day["date"], "lines": lines})
	return out


#: The item types that are something booked or shipped — what "Trip booked" lists.
BOOKING_TYPES = ("flight", "hotel_checkin", "ground", "freight")


def booking_lines(itinerary):
	"""Every booking in the itinerary, dated, each with its number or a note that it is
	missing. For "Trip booked" and "You were added to a trip"."""
	return [
		item_line(dict(item, date=item.get("date") or day.get("date")), dated=True)
		for day in itinerary.get("days") or []
		for item in day.get("items") or []
		if item.get("type") in BOOKING_TYPES
	]
