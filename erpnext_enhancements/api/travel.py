"""Read-side travel endpoints: desk calendar events, the mobile itinerary
page (``/itinerary``), and the trip-form map.

Document creation (claims, advances, outcomes, vehicle logs) lives in
``erpnext_enhancements.travel_management.api`` — this module only shapes data
for display.

Security:
	- ``get_my_trips`` / ``get_itinerary_bootstrap`` derive the employee from
	  the SESSION user (same model as ``api.time_kiosk``) — never from a
	  client-supplied parameter. The bootstrap adds the trips the user owns
	  through ``frappe.get_list``, so the row-level hooks still apply (and asks
	  only when the user can read Travel Trip at all).
	- ``get_trip_itinerary`` / ``get_trip_views`` / ``preview_itinerary_email`` /
	  ``get_trip_map_data`` gate through ``frappe.has_permission`` on the trip,
	  which the Travel Trip permission hooks scope to owner/crew/coordinators
	  (``travel_management.permissions``). Anyone who can read a trip may see
	  any crew member's itinerary on it ("all but money", 2026-09-26): the crew
	  already read the whole trip on the form. A person asked for is compared
	  against the crew and never looked up (``_crew_member_or_throw``).
	- Money (cost, who paid, billable, per diem, claims, totals) never leaves
	  this module for a non-coordinator. ``shape_itinerary`` carries none, and
	  the money block of ``get_trip_views`` is only built for a coordinator
	  (``travel_management.views``).
	- ``get_events`` uses ``frappe.get_list`` so the same row-level scoping
	  applies to the calendar.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, today

from erpnext_enhancements.travel_management import views as trip_views
from erpnext_enhancements.travel_management.completeness import (
	booking_index,
	booking_label,
	document_group,
	document_visible_to,
	file_name_of,
	group_key,
	is_image_file,
	receipt_urls,
)

# Calendar event colors per trip status (frappe palette names).
STATUS_COLOR = {
	"Planning": "orange",
	"Booked": "blue",
	"In Progress": "green",
	"Completed": "gray",
	"Closed": "gray",
}


def _session_employee():
	"""Employee linked to the current session user, or None."""
	return frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")


def _is_coordinator():
	"""True for Administrator, System Manager, HR Manager and Travel Coordinator — the
	people who see money on the trip views. Imported late: the controller module pulls in
	``frappe.model.document``, which nothing else here needs."""
	from erpnext_enhancements.travel_management.doctype.travel_trip.travel_trip import (
		user_is_travel_coordinator,
	)

	return bool(user_is_travel_coordinator())


def _crew_member_or_throw(doc, employee):
	"""Refuse anything but a person on this trip's crew, with one plain sentence, and
	without looking up who was asked for.

	The value comes from a request (``?as=``, the preview's ``employee``). It used to be
	looked up so a known Employee could be named in the refusal, which had two problems.
	First, anyone who could read one trip could learn the name behind any Employee id.
	Second, a JSON body can send a filter dict instead of a string, which
	``frappe.db.get_value`` takes as a filter without a permission check. The named and
	unnamed refusals then answered yes/no about any Employee field (CTC, bank account, date
	of birth). Now only the crew is compared against, only a string can match, and the
	refusal never says who. The endpoints also declare ``str``, so v16's whitelist type check
	turns a dict or list away before this runs.
	"""
	if isinstance(employee, str) and employee and any(t.employee == employee for t in doc.travelers):
		return
	frappe.throw(_("That person is not on this trip."))


def _poi_latlng(geolocation_json):
	"""(lat, lng) of the first Point feature in a Geolocation field value.

	The Geolocation fieldtype stores a GeoJSON FeatureCollection; GeoJSON
	Point coordinates are [lng, lat] — note the swap.
	"""
	if not geolocation_json:
		return None
	try:
		collection = json.loads(geolocation_json)
		for feature in collection.get("features") or []:
			geometry = feature.get("geometry") or {}
			if geometry.get("type") == "Point":
				lng, lat = geometry["coordinates"][:2]
				return (flt(lat), flt(lng))
	except (ValueError, TypeError, KeyError, IndexError):
		pass
	return None


# ----------------------------------------------------------------- calendar


@frappe.whitelist()
def get_events(start, end, filters=None):
	"""Desk Calendar source for Travel Trip: one all-day event per
	(trip, traveler) so overlapping crew assignments are visible per person.
	Wired via public/js/travel_trip_calendar.js (hooks ``doctype_calendar_js``)."""
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)
	if isinstance(filters, dict):
		filters = [["Travel Trip", key, "=", value] for key, value in filters.items() if value]

	trip_filters = [
		["Travel Trip", "start_date", "<=", end],
		["Travel Trip", "end_date", ">=", start],
	] + (filters or [])

	trips = frappe.get_list(
		"Travel Trip",
		filters=trip_filters,
		fields=[
			"name",
			"purpose",
			"status",
			"start_date",
			"end_date",
			"travel_for_name",
		],
	)
	if not trips:
		return []

	travelers = frappe.get_all(
		"Trip Traveler",
		filters={"parenttype": "Travel Trip", "parent": ["in", [t.name for t in trips]]},
		fields=["parent", "employee", "employee_name", "from_date", "to_date"],
	)
	by_trip = {}
	for row in travelers:
		by_trip.setdefault(row.parent, []).append(row)

	events = []
	for trip in trips:
		context = trip.travel_for_name or trip.purpose
		for row in by_trip.get(trip.name) or [None]:
			if row:
				title = f"{row.employee_name or row.employee} – {context}"
				event_start = row.from_date or trip.start_date
				event_end = row.to_date or trip.end_date
			else:
				title = context
				event_start, event_end = trip.start_date, trip.end_date
			events.append(
				{
					"name": trip.name,
					"doctype": "Travel Trip",
					"title": title,
					"start": str(event_start),
					"end": str(event_end),
					"allDay": 1,
					"color": STATUS_COLOR.get(trip.status),
					"status": trip.status,
				}
			)
	return events


# ---------------------------------------------------------------- itinerary


#: The columns every /itinerary trip chip is built from.
_TRIP_LIST_FIELDS = [
	"name",
	"purpose",
	"status",
	"travel_type",
	"start_date",
	"end_date",
	"travel_for_doctype",
	"travel_for_name",
]


@frappe.whitelist()
def get_itinerary_bootstrap():
	"""Boot payload for the /itinerary page (www/itinerary.py)."""
	employee = _session_employee()
	return {
		"user": frappe.session.user,
		"employee": employee,
		"employee_name": frappe.db.get_value("Employee", employee, "employee_name")
		if employee
		else None,
		"trips": _itinerary_trips(),
		"csrf_token": frappe.sessions.get_csrf_token(),
	}


def _itinerary_trips():
	"""The trip chips on /itinerary: the trips the user travels on (``mine``), plus the ones
	they own but are not on — the office person who booked a crew opens the page and finds
	that crew's trip, where until 2026-09-26 they found "No upcoming or recent trips".

	Same window for both: not Closed, ended less than a week ago. The owned list goes
	through ``get_list``, so the Travel Trip permission hooks decide, not this function.

	``get_list`` does not return an empty list to someone with no read on Travel Trip at all
	(a Website User, or a staff account whose profile has no Employee role). v16 raises
	``PermissionError`` first, and the web page turns that into Frappe's "Not Permitted" page,
	where the page used to show its own empty state. Such a person can open no trip they own,
	so their owned list is empty and the query is not run. A ``has_permission`` with no
	document queues no message, unlike catching the error, which ``frappe.throw`` has already
	put on screen.
	"""
	mine = get_my_trips()
	owned = (
		frappe.get_list(
			"Travel Trip",
			filters={
				"owner": frappe.session.user,
				"status": ["!=", "Closed"],
				"end_date": [">=", add_days(today(), -7)],
			},
			fields=_TRIP_LIST_FIELDS,
			order_by="start_date asc",
			limit_page_length=100,
		)
		if frappe.has_permission("Travel Trip", "read")
		else []
	)

	trips, seen = [], set()
	for row, travels in [(row, True) for row in mine] + [(row, False) for row in owned]:
		if row["name"] in seen:
			continue
		seen.add(row["name"])
		row["mine"] = travels
		trips.append(row)
	# sorted() is stable: two trips starting the same day keep the order they came in.
	trips = sorted(trips, key=lambda row: str(row.get("start_date") or ""))
	return trips


@frappe.whitelist()
def get_my_trips():
	"""Trips the session employee is travelling on: not Closed, ended less
	than 7 days ago (so just-finished trips stay reachable for receipts)."""
	employee = _session_employee()
	if not employee:
		return []

	trip_names = frappe.get_all(
		"Trip Traveler",
		filters={"parenttype": "Travel Trip", "employee": employee},
		pluck="parent",
	)
	if not trip_names:
		return []

	return frappe.get_all(
		"Travel Trip",
		filters={
			"name": ["in", trip_names],
			"status": ["!=", "Closed"],
			"end_date": [">=", add_days(today(), -7)],
		},
		fields=list(_TRIP_LIST_FIELDS),
		order_by="start_date asc",
	)


@frappe.whitelist()
def get_trip_itinerary(trip: str, as_employee: str | None = None):
	"""Day-by-day itinerary for one trip, shaped for the mobile page:
	``{trip, purpose, status, days: [{date, items: [...]}], crew, viewing, ...}`` with typed
	items (flight / hotel_checkin / hotel_checkout / ground / freight / agenda) merged
	chronologically.

	Whose itinerary (``/itinerary?trip=X&as=Y`` — ``as`` is a Python keyword, so the page
	sends it as ``as_employee``):

	* nothing — the viewer's own when they are on the crew, else the whole crew (the
	  only behavior until 2026-09-26);
	* ``"crew"`` — the whole crew, every booking once with who is on it;
	* an Employee — that person's, exactly as they see it. Anyone who can read the trip may
	  ask (owner's rule, 2026-09-26: crew, owner and coordinators alike); anything else is
	  refused with "That person is not on this trip." (``_crew_member_or_throw``).

	No money in any of them.
	"""
	doc = frappe.get_doc("Travel Trip", trip)
	frappe.has_permission("Travel Trip", "read", doc=doc, throw=True)

	session_emp = _session_employee() or None
	viewer_on_trip = bool(session_emp) and any(t.employee == session_emp for t in doc.travelers)

	if not as_employee:
		viewing = session_emp if viewer_on_trip else None
	elif as_employee == "crew":
		viewing = None
	else:
		_crew_member_or_throw(doc, as_employee)
		viewing = as_employee

	result = shape_itinerary(doc, viewing)
	result.update(
		crew=trip_views.crew(doc),
		viewing=viewing,
		viewer_employee=session_emp,
		viewer_on_trip=viewer_on_trip,
	)
	return result


@frappe.whitelist()
def get_trip_views(trip: str):
	"""Everything Plan a Trip's Overview, Grid, Compare and View-as screens draw, in one
	round trip: the whole crew's day-by-day, each person's, every day of the trip, the
	crew and the checklist — see ``travel_management.views.build_trip_views``.

	Read access to the trip is enough. Money comes back only for a coordinator; for anyone
	else ``money`` is null and the checklist's cost gaps are left out, so there is nothing
	on the wire for the page to hide."""
	doc = frappe.get_doc("Travel Trip", trip)
	frappe.has_permission("Travel Trip", "read", doc=doc, throw=True)

	is_coordinator = _is_coordinator()
	return trip_views.build_trip_views(
		doc,
		shape_itinerary,
		is_coordinator=is_coordinator,
		viewer_employee=_session_employee() or None,
		currency=_trip_currency(doc) if is_coordinator else None,
	)


def _trip_currency(doc):
	"""The trip company's currency (the one every cost row is entered in)."""
	if not doc.get("company"):
		return ""
	return frappe.db.get_value("Company", doc.company, "default_currency") or ""


def _clock(value):
	"""'HH:MM:SS' from a Time value (a ``timedelta`` from the database, a string from a
	document built in memory), or ``None``."""
	if value in (None, ""):
		return None
	if hasattr(value, "total_seconds"):
		seconds = int(value.total_seconds()) % 86400
		return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
	text = str(value)
	return text if len(text) != 7 else f"0{text}"  # "9:30:00" -> "09:30:00"


def _sort_time(value):
	"""The time-of-day part of a datetime, for ordering one day's items. Every item sorts by
	the same 'HH:MM:SS' key: until v1.520.0 flights and drives sorted by their whole
	datetime string while stops sorted by time alone, so "17:00:00" < "2026-..." put a 5 PM
	stop above a 7 AM flight."""
	return str(value)[11:19] if value else ""


def shape_itinerary(doc, viewing_employee=None, poi_cache=None):
	"""Build the typed day-by-day itinerary dict from a Travel Trip document.

	No permission checks — callers gate access. ``viewing_employee`` filters
	out segments pinned to a different single traveler (used both by the
	/itinerary page and the per-traveler itinerary emails in
	travel_management.notifications).

	No money, ever: this dict goes into emails and to every reader of the trip.
	That includes receipts: a row's ``attachment`` is its Receipt (a receipt is
	money) and is not read here. It was emitted as ``attachment`` until trip
	files replaced it (2026-09-26); no email or template read it.

	Keys are only ever ADDED here — the emails (``itinerary_text``) read the
	existing ones by name. Every booking and shipment carries ``group``, the
	same key the trip checklist and Plan a Trip's cards use
	(``completeness.group_key``), so a gap can be shown on the booking it is
	about; a stop's ``group`` is None. The whole-crew view also carries
	``members`` — each row's own person and confirmation number, which the
	joined ``booking_reference`` string loses — and ``whole_crew``; on a hotel
	each member also says whether they are a ``guest`` staying free, and their
	own ``check_in_date``/``check_out_date`` when those differ from the room's.

	Trip files (Trip Document rows): every booking and shipment item carries
	``documents``, the files on that booking this viewer sees, and the result
	carries ``documents``, every file this viewer sees — the whole trip's first,
	then by booking in itinerary order. One person sees a file that is for them,
	or for nobody in particular on the whole trip or on a booking they are on
	(``completeness.document_visible_to``); the whole-crew view sees every file.
	Each file is ``{name, title, kind, url, file_name, is_image, for_name,
	for_employee, group, booking_label}``, and a booking's file also carries
	``booking_dates`` and, on the whole-crew view, ``booking_people``
	(``_booking_facts``). A Trip Document naming one of the trip's receipts is never
	sent.

	``poi_cache`` lets several calls on one trip (one per crew member, on Plan
	a Trip's views) share their Travel POI lookups."""

	def visible(row_traveler):
		return not row_traveler or not viewing_employee or row_traveler == viewing_employee

	names = {t.employee: t.employee_name or t.employee for t in doc.travelers}
	files_by_group, files_in_order = _trip_files(doc, viewing_employee, names)

	def files_for(group):
		return [dict(entry) for entry in files_by_group.get(group) or []]

	def room_member(member, first):
		"""A hotel member's own facts in the whole-crew view: staying free, and their own
		nights when a guest's row was fitted to them (``planner.fit_guest_stays``)."""
		keys = {"guest": bool(cint(member.get("guest")))}
		mine = (member.get("check_in_date"), member.get("check_out_date"))
		room = (first.get("check_in_date"), first.get("check_out_date"))
		if tuple(str(d) if d else None for d in mine) != tuple(str(d) if d else None for d in room):
			keys["check_in_date"] = str(mine[0]) if mine[0] else None
			keys["check_out_date"] = str(mine[1]) if mine[1] else None
		return keys

	def bookings(rows, ref_field, member_keys=None):
		"""(first row, who is on it, confirmation, extra keys) per booking.

		Plan a Trip stores one row per person, so each traveler's own view holds only their
		own row and their own confirmation number — that view is unchanged here. The
		whole-crew view (no ``viewing_employee``) would otherwise print a four-person flight
		as four identical flights, so rows sharing a ``booking_group`` collapse into one
		entry that names who is on it and lists every distinct confirmation.
		"""
		shown = [row for row in rows if visible(row.traveler)]
		if viewing_employee:
			return [(row, None, row.get(ref_field), {"group": group_key(row)}) for row in shown]
		groups = {}
		for row in shown:
			groups.setdefault(row.get("booking_group") or row.name, []).append(row)
		out = []
		for members in groups.values():
			refs = []
			for member in members:
				ref = member.get(ref_field)
				if ref and ref not in refs:
					refs.append(ref)
			who = [names.get(m.traveler, m.traveler) for m in members if m.traveler]
			extra = {
				"group": group_key(members[0]),
				# One entry per row, in row order; a blank employee is a whole-crew row.
				"members": [
					dict(
						{
							"employee": m.traveler or "",
							"employee_name": names.get(m.traveler, m.traveler) if m.traveler else "",
							"ref": m.get(ref_field) or "",
						},
						**(member_keys(m, members[0]) if member_keys else {}),
					)
					for m in members
				],
				"whole_crew": any(not m.traveler for m in members),
			}
			out.append((members[0], who or None, ", ".join(refs) or None, extra))
		return out

	items = []

	for row, who, ref, extra in bookings(doc.flights, "booking_reference"):
		date = getdate(row.departure_time) if row.departure_time else getdate(doc.start_date)
		items.append(
			{
				"type": "flight",
				"date": str(date),
				"sort_time": _sort_time(row.departure_time),
				"airline": row.airline,
				"flight_number": row.flight_number,
				"departure_airport": row.departure_airport,
				"departure_time": str(row.departure_time) if row.departure_time else None,
				"arrival_airport": row.arrival_airport,
				"arrival_time": str(row.arrival_time) if row.arrival_time else None,
				"booking_reference": ref,
				"travelers": who,
				**extra,
				"documents": files_for(extra["group"]),
			}
		)

	for row, who, ref, extra in bookings(doc.accommodations, "booking_confirmation", room_member):
		check_in_time = _clock(row.get("check_in_time"))
		check_out_time = _clock(row.get("check_out_time"))
		base = {
			"hotel": row.hotel_lodging,
			"address": row.address,
			"booking_confirmation": ref,
			"travelers": who,
			**extra,
		}
		if row.check_in_date:
			items.append(
				dict(
					base,
					type="hotel_checkin",
					date=str(row.check_in_date),
					time=check_in_time,
					sort_time=check_in_time or "23:00",
					documents=files_for(extra["group"]),
				)
			)
		if row.check_out_date:
			items.append(
				dict(
					base,
					type="hotel_checkout",
					date=str(row.check_out_date),
					time=check_out_time,
					sort_time=check_out_time or "00:30",
					documents=files_for(extra["group"]),
				)
			)

	for row, who, ref, extra in bookings(doc.ground_transport, "booking_reference"):
		date = getdate(row.pickup_datetime) if row.pickup_datetime else getdate(doc.start_date)
		items.append(
			{
				"type": "ground",
				"date": str(date),
				"sort_time": _sort_time(row.pickup_datetime),
				"transport_type": row.transport_type,
				"provider": row.supplier or row.vehicle,
				"pickup_location": row.pickup_location,
				"dropoff_location": row.dropoff_location,
				"pickup_datetime": str(row.pickup_datetime) if row.pickup_datetime else None,
				"arrival_datetime": str(row.get("arrival_datetime")) if row.get("arrival_datetime") else None,
				"return_datetime": str(row.return_datetime) if row.return_datetime else None,
				"cargo": row.get("cargo"),
				"booking_reference": ref,
				"travelers": who,
				**extra,
				"documents": files_for(extra["group"]),
			}
		)

	# Freight: shown on the day it arrives (or is picked up, when no delivery window is
	# known), to whoever receives it — or to everyone when nobody is named.
	for row in doc.get("freight") or []:
		if not visible(row.traveler):
			continue
		when = row.delivery_from or row.pickup_from
		# One row is one shipment. Its key is the checklist's (its booking_group, or
		# row:<name> for a row typed on the form before shipments had one), so a gap, the
		# money block and a file all land on it; in the whole-crew view nobody is "on" it,
		# only the receiver named.
		extra = {"group": group_key(row)}
		if not viewing_employee:
			extra.update(members=[], whole_crew=not row.traveler)
		items.append(
			{
				"type": "freight",
				"date": str(getdate(when)) if when else str(getdate(doc.start_date)),
				"sort_time": _sort_time(when),
				"carrier": row.carrier,
				"tracking_number": row.tracking_number,
				"contents": row.contents,
				"ship_from": row.ship_from,
				"deliver_to": row.deliver_to,
				"pickup_from": str(row.pickup_from) if row.pickup_from else None,
				"pickup_to": str(row.pickup_to) if row.pickup_to else None,
				"delivery_from": str(row.delivery_from) if row.delivery_from else None,
				"delivery_to": str(row.delivery_to) if row.delivery_to else None,
				"received_by": names.get(row.traveler, row.traveler) if row.traveler else None,
				**extra,
				"documents": files_for(extra["group"]),
			}
		)

	if poi_cache is None:
		poi_cache = {}

	def poi_details(poi_name):
		if not poi_name:
			return None
		if poi_name not in poi_cache:
			poi = frappe.db.get_value(
				"Travel POI",
				poi_name,
				["poi_name", "category", "geolocation", "address"],
				as_dict=True,
			)
			latlng = _poi_latlng(poi.geolocation) if poi else None
			# Second rung, and here it is the only one: the /itinerary map is
			# Leaflet with no geocoder, so before v1.206.0 a POI without its own
			# Geolocation simply could not be plotted. A linked Address that was
			# picked from the autocomplete now places it.
			if poi and not latlng:
				_, addr_lat, addr_lng = _poi_address_location(poi.address)
				if addr_lat is not None:
					latlng = (addr_lat, addr_lng)
			poi_cache[poi_name] = (
				{
					"name": poi_name,
					"poi_name": poi.poi_name,
					"category": poi.category,
					"lat": latlng[0] if latlng else None,
					"lng": latlng[1] if latlng else None,
				}
				if poi
				else None
			)
		return poi_cache[poi_name]

	for row in doc.itinerary:
		items.append(
			{
				"type": "agenda",
				"date": str(row.date),
				"sort_time": _clock(row.time) or "",
				# Always "HH:MM:SS": str() of the database's timedelta is "9:30:00", which the
				# itinerary email sliced to "9:30:".
				"time": _clock(row.time),
				"end_time": _clock(row.get("end_time")),
				"activity": row.activity_description,
				"related_party_doctype": row.related_party_doctype,
				"related_party": row.related_party_name,
				"poi": poi_details(row.location),
				"visit_notes": row.visit_notes,
				"group": None,
			}
		)

	days = {}
	for item in items:
		days.setdefault(item["date"], []).append(item)
	for day_items in days.values():
		day_items.sort(key=lambda i: i.get("sort_time") or "")
	days = [{"date": d, "items": days[d]} for d in sorted(days)]

	# The whole trip's files first, then each booking's in the order the bookings come up
	# on the itinerary; a booking on no day yet (a room with no dates) comes last.
	order = [None]
	for day in days:
		for item in day["items"]:
			if item.get("group") and item["group"] not in order:
				order.append(item["group"])
	order += [group for group in files_in_order if group not in order]
	documents = [dict(entry) for group in order for entry in files_by_group.get(group) or []]

	return {
		"trip": doc.name,
		"purpose": doc.purpose,
		"status": doc.status,
		"travel_type": doc.travel_type,
		"start_date": str(doc.start_date),
		"end_date": str(doc.end_date),
		"travel_for_doctype": doc.travel_for_doctype,
		"travel_for": doc.travel_for_name,
		"days": days,
		"documents": documents,
	}


#: What a file's booking is called when the booking itself has no name yet.
_BOOKING_NOUN = {
	"flights": "Flight",
	"accommodations": "Room",
	"ground_transport": "Ride",
	"freight": "Shipment",
}


def _iso_day(value):
	return str(getdate(value)) if value else None


def _booking_facts(table, rows, names):
	"""Who is on a booking and when, for telling two bookings with one name apart (four rooms at
	one hotel, one adult to a room): ``(people, [from, to])``. ``people`` names each person on
	it in row order, and is empty for a booking the whole crew is on (a shipment: its receiver).
	The dates are ISO days or None — a room's nights, else the day it happens."""
	people = []
	if not any(not row.get("traveler") for row in rows):
		for row in rows:
			name = names.get(row.traveler, row.traveler)
			if name not in people:
				people.append(name)
	if table == "accommodations":
		# A guest's dates are their own nights, not the room's (planner.fit_guest_stays).
		first = next((row for row in rows if not cint(row.get("guest"))), rows[0])
		return people, [_iso_day(first.get("check_in_date")), _iso_day(first.get("check_out_date"))]
	first = rows[0]
	when = {
		"flights": first.get("departure_time"),
		"ground_transport": first.get("pickup_datetime"),
		"freight": first.get("delivery_from") or first.get("pickup_from"),
	}.get(table)
	return people, [_iso_day(when), None]


def _trip_files(doc, viewing_employee, names):
	"""The trip's files one viewer sees (everyone's, for the whole-crew view), shaped for
	display: ``({group or None: [file]}, [groups in row order])``. Each file is
	``{name, title, kind, url, file_name, is_image, for_name, for_employee, group,
	booking_label, booking_dates, booking_people}``; ``group`` and ``booking_label`` are None
	for a file for the whole trip, which is also what a file whose booking has been removed
	reads as. ``booking_dates`` and, on the whole-crew view, ``booking_people``
	(:func:`_booking_facts`) are only on a booking's file, for /itinerary's Documents screen to
	tell same-named bookings apart. A row with no file has nothing to open and is left out.

	Not money: a row naming one of the trip's receipts (``completeness.receipt_urls``) is left
	out too. The controller refuses to save one, and this is what keeps the rule in what is
	sent — the views, /itinerary, View as — whatever order the file and the receipt came in."""
	index = booking_index(doc)
	receipts = receipt_urls(doc)
	facts = {}
	by_group, order = {}, []
	for row in doc.get("documents") or []:
		url = str(row.get("file") or "").strip()
		if not url or url in receipts:
			continue
		if viewing_employee and not document_visible_to(row, viewing_employee, index):
			continue
		group = document_group(row, index)
		traveler = row.get("traveler") or None
		for_name = (names.get(traveler) or row.get("traveler_name") or traveler) if traveler else None
		extra = {"group": group, "booking_label": None}
		if group is not None:
			# A booking with no name yet (a flight with no airline or number) is still a
			# booking: /itinerary heads its files with a word for it, never "the whole trip".
			table, rows = index[group]
			if group not in facts:
				facts[group] = _booking_facts(table, rows, names)
			extra = {
				"group": group,
				"booking_label": booking_label(table, rows[0])
				or row.get("booking_label")
				or _BOOKING_NOUN.get(table),
				"booking_dates": list(facts[group][1]),
			}
			# Who is on it tells bookings apart on the whole crew's list; one person's own
			# list tells theirs apart by date.
			if not viewing_employee:
				extra["booking_people"] = list(facts[group][0])
		if group not in by_group:
			order.append(group)
		by_group.setdefault(group, []).append(
			{
				"name": row.name,
				"title": row.get("title") or file_name_of(url),
				"kind": row.get("kind") or "Other",
				"url": url,
				"file_name": file_name_of(url),
				"is_image": is_image_file(url),
				"for_name": for_name,
				"for_employee": traveler,
				**extra,
			}
		)
	return by_group, order


# --------------------------------------------------------------------- maps


def _maps_api_key():
	return frappe.db.get_single_value("Travel Settings", "google_maps_api_key") or ""


@frappe.whitelist()
def get_maps_api_key():
	"""The Google Maps *browser* API key (Travel Settings), for the Travel POI
	location picker (travel_poi.js) and other travel maps.

	A referrer-restricted browser key — exposed to the client by design — so any
	logged-in user may read it. Not gated to a specific record."""
	return _maps_api_key()


@frappe.whitelist()
def get_maps_config():
	"""The Google Maps *browser* API key plus Map IDs, for the shared Maps loader.
	
	Readable by any logged-in user by design (the key is referrer-restricted).
	Blank Map IDs mean the caller falls back to a legacy styles array."""
	return {
		"api_key": _maps_api_key(),
		"map_id_light": frappe.db.get_single_value("Travel Settings", "google_maps_map_id_light") or "",
		"map_id_dark": frappe.db.get_single_value("Travel Settings", "google_maps_map_id_dark") or "",
	}


def _poi_address_location(address_name):
	"""``(text, lat, lng)`` for a linked Address. Any part may be None.

	``lat``/``lng`` are the point the Places autocomplete stored when the address
	was picked (v1.205.0) -- exact, and free, where geocoding the text is neither.
	Most Addresses predate that and return ``(text, None, None)``.

	**0.0 means absent.** Float custom fields are ``NOT NULL DEFAULT 0``, so the
	whole pre-v1.205.0 table reads back as 0.0, and a hand edit blanks the pair to
	a literal 0. Testing ``is not None`` would put every legacy POI on Null Island.
	"""
	if not address_name:
		return None, None, None

	fields = ["address_line1", "address_line2", "city", "state", "pincode", "country"]
	# The columns arrive with `bench migrate`, and main auto-deploys -- selecting
	# one that does not exist yet is a SQL error, not a None (cf. api/comments.py).
	has_point = frappe.db.has_column("Address", "custom_latitude")
	if has_point:
		fields += ["custom_latitude", "custom_longitude"]

	addr = frappe.db.get_value("Address", address_name, fields, as_dict=True)
	if not addr:
		return None, None, None

	parts = [p for p in (
		addr.address_line1,
		addr.address_line2,
		addr.city,
		addr.state,
		addr.pincode,
		addr.country,
	) if p]
	text = ", ".join(parts) if parts else None

	lat = lng = None
	if has_point:
		lat = flt(addr.custom_latitude) or None
		lng = flt(addr.custom_longitude) or None
		if lat is None or lng is None:
			lat = lng = None  # never emit half a point
		elif not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
			lat = lng = None

	return text, lat, lng


def _trip_pois(doc):
	"""POIs referenced by a trip's agenda, one entry per POI with the agenda
	dates that visit it.

	Each entry's point is resolved down three rungs: the POI's own Geolocation,
	then the linked Address's stored autocomplete point (v1.205.0), then nothing
	-- in which case the ``address`` text is sent and the client geocodes it.
	POIs with no point and no text are skipped (nothing to plot).

	The middle rung is the cheap one: it costs a column read instead of a
	billable geocode, and it places the pin on the building somebody actually
	picked. ``address`` is populated whenever it is known, point or not, so the
	popup's deep links keep working. Shared by ``get_trip_map_data``."""
	pois = {}
	for row in doc.itinerary:
		if not row.location:
			continue
		entry = pois.get(row.location)
		if not entry:
			poi = frappe.db.get_value(
				"Travel POI",
				row.location,
				["poi_name", "category", "geolocation", "address"],
				as_dict=True,
			)
			if not poi:
				continue
			latlng = _poi_latlng(poi.geolocation)
			address, addr_lat, addr_lng = _poi_address_location(poi.address)
			if not latlng and addr_lat is not None:
				latlng = (addr_lat, addr_lng)
			if not latlng and not address:
				continue
			entry = pois[row.location] = {
				"poi": row.location,
				"label": poi.poi_name,
				"category": poi.category,
				"lat": latlng[0] if latlng else None,
				"lng": latlng[1] if latlng else None,
				"address": address,
				"agenda_dates": [],
			}
		if str(row.date) not in entry["agenda_dates"]:
			entry["agenda_dates"].append(str(row.date))

	return list(pois.values())


@frappe.whitelist()
def get_trip_map_data(trip):
	"""Agenda-map payload for the Travel Trip form
	(public/js/travel/travel_trip_map.js): the Google Maps browser API key plus
	the trip's mappable POIs, in one round-trip.

	The key is the *browser* Maps JavaScript API key (referrer-restricted by
	design — it is exposed to the client either way), so returning it to a
	viewer already permitted to read the trip is expected."""
	doc = frappe.get_doc("Travel Trip", trip)
	frappe.has_permission("Travel Trip", "read", doc=doc, throw=True)

	return {
		"api_key": _maps_api_key(),
		"pois": _trip_pois(doc),
	}


@frappe.whitelist()
def cache_poi_geocode(poi, lat, lng):
	"""Persist a client-geocoded point onto a Travel POI's ``geolocation`` so a
	linked Address isn't re-geocoded on every map load.

	Best-effort: needs write access to the POI and never clobbers a point that is
	already set (a manually-placed pin wins)."""
	if not frappe.has_permission("Travel POI", "write", doc=poi):
		return
	existing = frappe.db.get_value("Travel POI", poi, "geolocation")
	if existing and existing.strip() not in ("", "{}"):
		return
	geojson = json.dumps(
		{
			"type": "FeatureCollection",
			"features": [
				{
					"type": "Feature",
					"properties": {},
					"geometry": {"type": "Point", "coordinates": [flt(lng), flt(lat)]},
				}
			],
		}
	)
	frappe.db.set_value("Travel POI", poi, "geolocation", geojson)


# -------------------------------------------------------------------- email


@frappe.whitelist()
def send_itinerary_email(trip, employee=None):
	"""Send the itinerary email (with ICS attachment) to one traveler, or to
	every traveler when ``employee`` is omitted. Coordinators can send to
	anyone; a traveler can only send to themselves."""
	from erpnext_enhancements.travel_management import notifications
	from erpnext_enhancements.travel_management.doctype.travel_trip.travel_trip import (
		user_is_travel_coordinator,
	)

	doc = frappe.get_doc("Travel Trip", trip)
	frappe.has_permission("Travel Trip", "read", doc=doc, throw=True)

	if not user_is_travel_coordinator():
		session_emp = _session_employee()
		if not employee or employee != session_emp:
			frappe.throw(_("You can only send the itinerary to yourself."))

	sent = notifications.send_itinerary_emails(doc, employee=employee, force=True)
	if not sent:
		frappe.throw(_("No traveler with an email address matched."))
	return sent


@frappe.whitelist()
def preview_itinerary_email(trip: str, employee: str):
	"""The itinerary email and calendar invite exactly as ``employee`` would get them from
	"Email everyone their itinerary" — rendered, never sent.

	Nothing on this path sends, queues or logs: it renders the same template, context and
	.ics that ``notifications.send_itinerary_emails`` delivers, through the render half of
	the split ``_send`` (``notifications.render_itinerary_preview``). No email, no
	Notification Log, no background job.

	Anyone who can read the trip may preview anyone on its crew (the emails carry no
	money). The address it would go to is shown to coordinators only; ``no_email`` says
	whether there is one at all, so the page can warn that this person would get nothing.
	"""
	from erpnext_enhancements.travel_management import notifications

	doc = frappe.get_doc("Travel Trip", trip)
	frappe.has_permission("Travel Trip", "read", doc=doc, throw=True)
	_crew_member_or_throw(doc, employee)

	preview = notifications.render_itinerary_preview(doc, employee)
	if not _is_coordinator():
		preview["to_email"] = None
	return preview
