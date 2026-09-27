"""Read-side travel endpoints: desk calendar events, the mobile itinerary
page (``/itinerary``), the trip-form map, the trip's contacts card
(``_trip_contacts``) and the Trip Sheet print format's data (``ee_trip_sheet``).

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
	  (``travel_management.views``). The Trip Sheet carries one total, for a
	  coordinator's whole-trip sheet printed in a web request only, never on an emailed
	  copy (``ee_trip_sheet``), and it is built from the saved trip, never from a document
	  posted to the print view.
	- Contacts are not money, but ``_trip_contacts`` reads records the crew
	  cannot open (a colleague's Employee, the customer's Contact), so it names
	  every field it reads and returns only those: never a crew member's
	  personal phone or email, next of kin, home address or health details.
	- ``get_events`` uses ``frappe.get_list`` so the same row-level scoping
	  applies to the calendar.
"""

import functools
import json
import re
from html import unescape as _unescape

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

	# One lookup cache for the itinerary and the contacts card: a hotel's Address is read once.
	cache = {}
	result = shape_itinerary(doc, viewing, poi_cache=cache)
	sheet = _sheet_available()
	result.update(
		crew=trip_views.crew(doc),
		viewing=viewing,
		viewer_employee=session_emp,
		viewer_on_trip=viewer_on_trip,
		# Who to call, for the person shown: their own hotels only (``_trip_contacts``).
		contacts=_trip_contacts(doc, viewing, hotels=_hotel_details(doc, cache)),
		# The printed Trip Sheet: the whole trip's, and the one for the person shown (the
		# whole trip's again when that is the whole crew). Null while the format is missing
		# (``_sheet_available``): the page then draws no link.
		sheet_url=trip_views.trip_sheet_url(doc.name) if sheet else None,
		my_sheet_url=trip_views.trip_sheet_url(doc.name, viewing) if sheet else None,
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
	# One lookup cache for every person's itinerary and the hotels: an Address is read once.
	cache = {}
	hotels = _hotel_details(doc, cache)
	payload = trip_views.build_trip_views(
		doc,
		shape_itinerary,
		is_coordinator=is_coordinator,
		viewer_employee=_session_employee() or None,
		currency=_trip_currency(doc) if is_coordinator else None,
		hotels=hotels,
		sheet_available=_sheet_available(),
		poi_cache=cache,
	)
	payload.update(
		# The whole trip's contacts: every hotel.
		contacts=_trip_contacts(doc, hotels=hotels),
		# The hotels on each person's card, by the rule their /itinerary uses (``_hotel_contacts``:
		# a room pinned to them or to the whole crew, dates or none), so View as shows the card
		# their phone shows. Until 2026-09-27 the page rebuilt it from their check-in items,
		# which leave out a room with no dates yet.
		people_hotels={
			row.employee: [hotel["name"] for hotel in _hotel_contacts(doc, row.employee, hotels)]
			for row in doc.travelers
			if row.employee
		},
		# The Map view's key and Map IDs: the browser key any signed-in user can already
		# read (get_maps_config), in the same round trip.
		maps=_maps_config_or_blank(),
	)
	return payload


def _sheet_available():
	"""Whether the Trip Sheet print format exists on this site. Every link to the sheet is sent
	only when it does: frappe renders a ``format=`` it cannot find as **Standard**
	(``printview.get_print_format_doc`` returns None on ``DoesNotExistError``), which prints every
	cost on the trip, so a missing format turned "Your trip sheet" into the priced Standard copy.
	The format is missing when ``ensure_travel_print_formats`` failed (it logs, never raises) or
	the site has not migrated since this app was installed."""
	try:
		return bool(frappe.db.exists("Print Format", trip_views.TRIP_SHEET_FORMAT))
	except Exception:
		return False


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

	A hotel item's ``address`` is the room's street address as one line
	("1 Harbor Dr, San Diego, CA 92101"). The room row's ``address`` is fetched
	from the hotel Supplier's primary address, a Link, so it holds the Address
	record's *name* ("Harborview Suites-Billing"), and until 2026-09-27 that name
	was what ``/itinerary`` (since v1.15.0) and Plan a Trip's views printed under
	the hotel. It is resolved here (``_address_text``); a
	value no Address is named is text someone typed and is sent as it is.

	``poi_cache`` lets several calls on one trip (one per crew member, on Plan
	a Trip's views; one per recipient of an itinerary send) share their Travel
	POI and Address lookups. Addresses are kept under ``("Address", name)``
	keys, which no POI name can equal, so the one dict serves both."""

	if poi_cache is None:
		poi_cache = {}

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
			# The street address, never the Address record's name the row holds.
			"address": _address_text(row.address, poi_cache),
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


def _maps_config_or_blank():
	"""``get_maps_config()`` for the trip views, or blanks. The Map view says "no key" and
	falls back to its list of places; the rest of the views must not fail with it."""
	try:
		return get_maps_config()
	except Exception:
		return {"api_key": "", "map_id_light": "", "map_id_dark": ""}


def _poi_address_location(address_name):
	"""``(text, lat, lng)`` for a linked Address. Any part may be None.

	``lat``/``lng`` are the point the Places autocomplete stored when the address
	was picked (v1.205.0) -- exact, and free, where geocoding the text is neither.
	Most Addresses predate that and return ``(text, None, None)``.

	**0.0 means absent.** Float custom fields are ``NOT NULL DEFAULT 0``, so the
	whole pre-v1.205.0 table reads back as 0.0, and a hand edit blanks the pair to
	a literal 0. Testing ``is not None`` would put every legacy POI on Null Island.
	"""
	return _address_lookup(address_name)[:3]


def _home_country():
	"""The country this company works in, which an address printed for its crew need not name:
	Global Defaults' ``country``, else the default company's. ``None`` when neither can be read
	(``views.one_line_address`` then leaves out "United States" only). Never raises.

	Both reads are cached by frappe (``get_single_value`` for the request, ``get_cached_value``
	across requests), so asking once per address costs nothing; the Company is read only on a
	site whose Global Defaults name no country."""
	try:
		country = frappe.db.get_single_value("Global Defaults", "country")
		if not country:
			company = frappe.db.get_single_value("Global Defaults", "default_company")
			country = frappe.get_cached_value("Company", company, "country") if company else None
	except Exception:
		return None
	return str(country or "").strip() or None


def _address_lookup(address_name, extra=()):
	"""``(text, lat, lng, row)``: :func:`_poi_address_location`'s answer plus the Address row,
	with any ``extra`` columns this site's Address has read in the same query (the contacts
	card's hotel phone: one read of the Address, not two). ``row`` is ``{}`` when no Address
	has that name.

	``text`` is one line, "1 Harbor Dr, San Diego, CA 92101" (``views.one_line_address``: state
	and ZIP together, the country only when it is not this company's), for every reader: the
	contacts card, a hotel on the itinerary and the Trip Sheet, the Map's geocoding query and
	the trip form's map."""
	if not address_name:
		return None, None, None, {}

	fields = ["address_line1", "address_line2", "city", "state", "pincode", "country"]
	# The columns arrive with `bench migrate`, and main auto-deploys -- selecting
	# one that does not exist yet is a SQL error, not a None (cf. api/comments.py).
	has_point = frappe.db.has_column("Address", "custom_latitude")
	if has_point:
		fields += ["custom_latitude", "custom_longitude"]
	fields += [field for field in extra if field not in fields and frappe.db.has_column("Address", field)]

	addr = frappe.db.get_value("Address", address_name, fields, as_dict=True)
	if not addr:
		return None, None, None, {}

	text = trip_views.one_line_address(
		addr.address_line1,
		addr.address_line2,
		addr.city,
		addr.state,
		addr.pincode,
		addr.country,
		home_country=_home_country() if addr.country else None,
	)

	lat = lng = None
	if has_point:
		lat = flt(addr.custom_latitude) or None
		lng = flt(addr.custom_longitude) or None
		if lat is None or lng is None:
			lat = lng = None  # never emit half a point
		elif not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
			lat = lng = None

	return text, lat, lng, addr


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


# ----------------------------------------------------------------- contacts

#: A Contact's phone, email and name, this app's own fields first: they are the ones this
#: site fills (print_lookup.py measured it — Contact.custom_email on 1,741 of 2,792 contacts,
#: the stock email_id on 436). Each is asked for only where the site has the field.
_CONTACT_PHONE = ("custom_mobile_number", "custom_phone_number", "mobile_no", "phone")
_CONTACT_EMAIL = ("custom_email", "email_id")
_CONTACT_NAME = ("full_name", "first_name", "last_name")

#: What a trip is for, and the field that names it.
_SITE_TITLE = {
	"Project": ("project_name",),
	"Opportunity": ("title", "customer_name"),
	"Lead": ("title", "lead_name", "company_name"),
	"Customer": ("customer_name",),
}

_TAG = re.compile(r"<[^>]+>")


def _record(doctype, name, fields):
	"""``{field: value}`` of one record — only the ``fields`` this site's doctype has — or
	``{}`` for a blank name, a missing record, doctype or field, or any failure. Never raises:
	a contacts card with a blank line is a small thing, an itinerary that will not load is not.

	No permission check, as ``frappe.db.get_value`` makes none: the crew cannot read a
	colleague's Employee record or the customer's Contact, and the card is for them. What
	keeps that safe is that every caller names its fields and returns only those."""
	if not name or not isinstance(name, str):
		return {}
	try:
		meta = frappe.get_meta(doctype)
		wanted = [field for field in fields if meta.has_field(field)]
		if not wanted:
			return {}
		row = frappe.db.get_value(doctype, name, wanted, as_dict=True)
	except Exception:
		return {}
	return dict(row or {})


def _first_text(record, fields):
	"""The first of ``fields`` with a value in ``record``, stripped, or ``None``."""
	for field in fields:
		value = str((record or {}).get(field) or "").strip()
		if value:
			return value
	return None


def _plain(html):
	"""A snapshot's text (an Opportunity's address or contact): tags off, entities decoded, one
	line, parts joined by commas."""
	text = _unescape(_TAG.sub("\n", str(html or "")))
	return ", ".join(part.strip() for part in text.splitlines() if part.strip()) or None


def _contact_name(contact):
	parts = (_first_text(contact, ("first_name",)), _first_text(contact, ("last_name",)))
	return _first_text(contact, ("full_name",)) or " ".join(p for p in parts if p) or None


def _address_row(address_name, cache=None):
	"""``(text, lat, lng, row)`` of an Address record (:func:`_address_lookup`, its phone
	included), read once per ``cache``: a dict shared by the calls that serve one answer
	(``shape_itinerary``'s ``poi_cache``, ``_hotel_details``), keyed ``("Address", name)``.
	``row`` is ``{}`` when no Address has that name or the read failed. Never raises."""
	key = ("Address", address_name)
	if cache is not None and key in cache:
		return cache[key]
	try:
		found = _address_lookup(address_name, extra=("phone",))
	except Exception:
		found = (None, None, None, {})
	if cache is not None:
		cache[key] = found
	return found


def _address_facts(address_name, cache=None):
	"""``(text, lat, lng, phone)`` of an Address record, any part ``None``: one read of it, the
	phone asked for only where this site's Address has the column."""
	if not address_name or not isinstance(address_name, str):
		return None, None, None, None
	text, lat, lng, row = _address_row(address_name, cache)
	return text, lat, lng, _first_text(row, ("phone",))


def _address_text(value, cache=None):
	"""A room's ``address`` as something to print, or ``None``. Never raises.

	Trip Accommodation's ``address`` is ``fetch_from: hotel_lodging.supplier_primary_address``,
	and that is a Link, so the row holds the Address record's **name** ("Harborview
	Suites-Billing"), which is what every itinerary printed under the hotel from v1.15.0 to
	2026-09-27. A value that names an Address is its street address as one line
	(:func:`_address_lookup`), or ``None`` for an Address with nothing written on it — never the
	record's name. A value no Address is named is text somebody typed, and is returned as it is.
	``cache`` as :func:`_address_row`."""
	raw = str(value or "").strip()
	if not raw:
		return None
	text, _lat, _lng, row = _address_row(raw, cache)
	return text if row else raw


def _employee_of_user(user):
	try:
		return frappe.db.get_value("Employee", {"user_id": user}, "name")
	except Exception:
		return None


def _office_contact():
	"""The office travel desk, from Travel Settings (Travel desk section), or ``None`` until
	it has a phone or an email. Three plain fields with no default: a default on a new field
	of a Single never reaches the row a site already has (CLAUDE.md)."""
	values = {}
	for field in ("travel_desk_label", "travel_desk_phone", "travel_desk_email"):
		try:
			values[field] = str(frappe.db.get_single_value("Travel Settings", field) or "").strip() or None
		except Exception:
			values[field] = None
	if not (values["travel_desk_phone"] or values["travel_desk_email"]):
		return None
	return {
		"label": values["travel_desk_label"] or _("Travel desk"),
		"phone": values["travel_desk_phone"],
		"email": values["travel_desk_email"],
	}


def _booked_by_contact(doc):
	"""Whoever made the trip (its owner): their User's name, mobile (else phone, else their
	Employee's work cell) and email. Not Administrator or Guest — nobody to call there."""
	owner = doc.get("owner")
	if not owner or owner in ("Administrator", "Guest"):
		return None
	user = _record("User", owner, ("full_name", "mobile_no", "phone", "email"))
	if not user:
		return None
	phone = _first_text(user, ("mobile_no", "phone"))
	if not phone:
		phone = _first_text(_record("Employee", _employee_of_user(owner), ("cell_number",)), ("cell_number",))
	return {
		"name": _first_text(user, ("full_name",)) or owner,
		"phone": phone,
		"email": _first_text(user, ("email",)),
	}


def _lead_contact(doc):
	"""The trip lead: their name and work cell (Employee ``cell_number``) — and nothing else
	from their Employee record, which also holds their personal email, home address, next of
	kin and health details."""
	row = next(
		(t for t in doc.get("travelers") or [] if cint(t.get("is_trip_lead")) and t.get("employee")), None
	)
	if row is None:
		return None
	employee = _record("Employee", row.get("employee"), ("employee_name", "cell_number"))
	return {
		"name": _first_text(employee, ("employee_name",)) or row.get("employee_name") or row.get("employee"),
		"phone": _first_text(employee, ("cell_number",)),
	}


def _customer_contact(customer):
	"""A customer's primary contact (name, phone, email) and primary address."""
	record = _record(
		"Customer",
		customer,
		("customer_primary_contact", "customer_primary_address", "mobile_no", "email_id"),
	)
	contact = _record(
		"Contact", record.get("customer_primary_contact"), _CONTACT_NAME + _CONTACT_PHONE + _CONTACT_EMAIL
	)
	return {
		"contact_name": _contact_name(contact),
		"phone": _first_text(contact, _CONTACT_PHONE) or _first_text(record, ("mobile_no",)),
		"email": _first_text(contact, _CONTACT_EMAIL) or _first_text(record, ("email_id",)),
		"address": _address_facts(record.get("customer_primary_address"))[0],
	}


def _opportunity_contact(record):
	"""An Opportunity's own contact and address (v16: ``contact_person``, ``contact_display``,
	``contact_mobile``, ``phone``, ``contact_email``, ``customer_address``, ``address_display``),
	then its Contact's, then — when it is a customer's and has none of its own — the
	customer's."""
	contact = _record(
		"Contact", record.get("contact_person"), _CONTACT_NAME + _CONTACT_PHONE + _CONTACT_EMAIL
	)
	found = {
		"contact_name": _plain(record.get("contact_display")) or _contact_name(contact),
		"phone": _first_text(record, ("contact_mobile", "phone")) or _first_text(contact, _CONTACT_PHONE),
		"email": _first_text(record, ("contact_email",)) or _first_text(contact, _CONTACT_EMAIL),
		"address": _address_facts(record.get("customer_address"))[0] or _plain(record.get("address_display")),
	}
	if not any(found.values()) and record.get("opportunity_from") == "Customer":
		found = _customer_contact(record.get("party_name"))
	return found


#: A Project's own job site (this app's Custom Fields on Project, fixtures/custom_field.json):
#: where it is, then who is there, each in the order the rest of the app reads them —
#: ``workforce/sites.project_address_text`` for the address, the Project Contract's prefill
#: (``project_contract._prefill_from_project``) and the Primary Contact section for the person.
_PROJECT_SITE_ADDRESS = "custom_project_address"
_PROJECT_SITE_ADDRESS_LINK = "custom_customer__lead_address"
_PROJECT_SITE_NAME = ("custom_primary_first_name", "custom_customer_name")
_PROJECT_SITE_PHONE = ("custom_primary_phone", "custom_contact_phone", "custom_customer_phone")
_PROJECT_SITE_EMAIL = ("custom_primary_email_address", "custom_customer_email")


def _project_contact(record):
	"""A Project's job site: its own address and contact first, the customer's where it has none.

	The Project is where the site is. Its customer's primary Address is usually the billing
	office (a Las Vegas install for a Salt Lake City customer), so "Directions to the job site"
	went to the wrong city until 2026-09-27, and a Project with no customer had no site at all.
	The address is the typed PROJECT ADDRESS, else the Customer / Lead Address link, else the
	customer's primary Address. The person is taken whole — name, phone and email from the
	Project, or all three from the customer's primary Contact when the Project has no phone or
	email of its own — so one person's name is never printed over another person's number."""
	person = {
		"contact_name": _first_text(record, _PROJECT_SITE_NAME),
		"phone": _first_text(record, _PROJECT_SITE_PHONE),
		"email": _first_text(record, _PROJECT_SITE_EMAIL),
	}
	address = (
		_first_text(record, (_PROJECT_SITE_ADDRESS,))
		or _address_facts(record.get(_PROJECT_SITE_ADDRESS_LINK))[0]
	)
	customer = record.get("customer")
	if customer and (not address or not (person["phone"] or person["email"])):
		theirs = _customer_contact(customer)
		if not (person["phone"] or person["email"]) and (
			theirs.get("phone") or theirs.get("email") or not person["contact_name"]
		):
			person = {key: theirs.get(key) for key in ("contact_name", "phone", "email")}
		address = address or theirs.get("address")
	return dict(person, address=address)


def _site_title(doc):
	"""What the trip is for, by name ("Harbor Fountain"), or ``None`` when its record is gone. The
	Trip Sheet's JOB line reads this, so the job keeps its name when there is nobody to call."""
	doctype, name = doc.get("travel_for_doctype"), doc.get("travel_for_name")
	if doctype not in _SITE_TITLE or not name:
		return None
	return _first_text(_record(doctype, name, _SITE_TITLE[doctype]), _SITE_TITLE[doctype])


def _site_contact(doc):
	"""The job-site contact, from what the trip is for: a Project (its own site, then its
	customer's; :func:`_project_contact`), a Customer, an Opportunity or a Lead. ``None`` when that
	record is gone or holds no name, phone, email or address — a card line that says only the
	job's name is nobody to call."""
	doctype, name = doc.get("travel_for_doctype"), doc.get("travel_for_name")
	if doctype not in _SITE_TITLE or not name:
		return None
	if doctype == "Project":
		record = _record(
			"Project",
			name,
			(
				"project_name",
				"customer",
				_PROJECT_SITE_ADDRESS,
				_PROJECT_SITE_ADDRESS_LINK,
				*_PROJECT_SITE_NAME,
				*_PROJECT_SITE_PHONE,
				*_PROJECT_SITE_EMAIL,
			),
		)
		found = _project_contact(record) if record else {}
	elif doctype == "Customer":
		record = _record("Customer", name, ("customer_name",))
		found = _customer_contact(name) if record else {}
	elif doctype == "Opportunity":
		record = _record(
			"Opportunity",
			name,
			(
				"title",
				"customer_name",
				"opportunity_from",
				"party_name",
				"contact_person",
				"contact_display",
				"contact_mobile",
				"phone",
				"contact_email",
				"customer_address",
				"address_display",
			),
		)
		found = _opportunity_contact(record) if record else {}
	else:
		record = _record(
			"Lead", name, ("title", "lead_name", "company_name", "mobile_no", "phone", "email_id")
		)
		found = {
			"contact_name": _first_text(record, ("lead_name",)),
			"phone": _first_text(record, ("mobile_no", "phone")),
			"email": _first_text(record, ("email_id",)),
			"address": None,
		}
	if not record or not any(found.values()):
		return None
	return {
		"label": _first_text(record, _SITE_TITLE[doctype]) or name,
		"contact_name": found.get("contact_name"),
		"phone": found.get("phone"),
		"email": found.get("email"),
		"address": found.get("address"),
	}


def _hotel_detail(hotel, address_name=None, cache=None):
	"""``{name, phone, address, lat, lng}`` for one hotel (a Supplier).

	The address is the room's own Address (a room's ``address`` is fetched from the hotel's
	primary address, so it holds the Address record's *name* — "Harborview Suites-Billing" —
	and is resolved here, never printed), else the Supplier's; ``lat``/``lng`` are the point
	the Address was picked with, when it was. The phone is that Address's, else the hotel's
	primary Contact's, else the Supplier's own. ``cache`` as ``_address_row``."""
	supplier = _record(
		"Supplier",
		hotel,
		("supplier_primary_address", "supplier_primary_contact", "mobile_no", "custom_phone_number"),
	)
	text = lat = lng = phone = None
	for candidate in dict.fromkeys(a for a in (address_name, supplier.get("supplier_primary_address")) if a):
		text, lat, lng, phone = _address_facts(candidate, cache)
		if text or lat is not None or phone:
			break
	if not phone:
		phone = _first_text(
			_record("Contact", supplier.get("supplier_primary_contact"), _CONTACT_PHONE), _CONTACT_PHONE
		) or _first_text(supplier, ("mobile_no", "custom_phone_number"))
	return {"name": hotel, "phone": phone, "address": text, "lat": lat, "lng": lng}


def _hotel_details(doc, cache=None):
	"""``{hotel: {name, phone, address, lat, lng}}`` for every hotel on the trip, keyed by the
	room rows' ``hotel_lodging`` — the value an itinerary item's ``hotel`` carries. Shared by the
	contacts card and the Map view, so each hotel is looked up once; ``cache`` (the
	``poi_cache`` handed to ``shape_itinerary`` for the same answer) shares its Address reads
	with the itinerary's hotel items."""
	details = {}
	for row in doc.get("accommodations") or []:
		hotel = row.get("hotel_lodging")
		if not hotel or hotel in details:
			continue
		try:
			details[hotel] = _hotel_detail(hotel, row.get("address"), cache)
		except Exception:
			details[hotel] = {"name": hotel, "phone": None, "address": None, "lat": None, "lng": None}
	return details


def _hotel_contacts(doc, viewing_employee=None, hotels=None):
	"""One entry per hotel the viewer stays at (every hotel on the trip for the whole crew), in
	check-in order: ``{name, phone, address, urgent_care_url, directions_url}``. The two links
	are Google Maps searches — no key and nothing stored: "urgent care near" the hotel's street
	address (its name when there is none), and directions to it."""
	if hotels is None:
		hotels = _hotel_details(doc)
	rows = [
		row
		for row in doc.get("accommodations") or []
		if row.get("hotel_lodging")
		and (not viewing_employee or not row.get("traveler") or row.get("traveler") == viewing_employee)
	]
	rows.sort(key=lambda row: str(row.get("check_in_date") or "9999"))
	out, seen = [], set()
	for row in rows:
		hotel = row.get("hotel_lodging")
		if hotel in seen:
			continue
		seen.add(hotel)
		info = hotels.get(hotel) or {}
		where = info.get("address") or hotel
		out.append(
			{
				"name": hotel,
				"phone": info.get("phone"),
				"address": info.get("address"),
				"urgent_care_url": trip_views.maps_search_url(f"urgent care near {where}"),
				"directions_url": trip_views.maps_directions_url(where),
			}
		)
	return out


def _trip_contacts(doc, viewing_employee=None, hotels=None):
	"""Who to call on this trip — the contacts card on Plan a Trip's Overview and View as, the
	top of ``/itinerary``, the itinerary email and the Trip Sheet. Exactly these keys:

	* ``emergency`` — ``"911"``;
	* ``office`` — ``{label, phone, email}`` from Travel Settings' Travel Desk section, or None;
	* ``booked_by`` — ``{name, phone, email}``, the trip's owner, or None;
	* ``lead`` — ``{name, phone}``, the trip lead and their work cell, or None;
	* ``site`` — ``{label, contact_name, phone, email, address}``, the job-site contact from
	  what the trip is for, or None;
	* ``hotels`` — ``[{name, phone, address, urgent_care_url, directions_url}]``.

	``viewing_employee`` narrows ``hotels`` to that person's (rows pinned to them and rows for
	the whole crew); the rest is the same for everyone on the trip.

	Contacts are not money, so this goes to anyone who can read the trip. It reads records the
	crew could not open themselves (a colleague's Employee, the customer's Contact) and so
	returns only these keys, never a record: **never a crew member's personal phone or email,
	next of kin, home address or health details.** The trip lead's work cell is the one number
	taken from an Employee record (and the owner's, when their User has none).

	Never raises: every lookup is guarded (``_record``), a missing record or field is ``None``,
	and a part that fails for any other reason is ``None`` (``[]`` for hotels) with an Error Log.
	Phones are returned as stored; a page builds its ``tel:`` link from the digits
	(``views.tel_href``)."""
	contacts = {
		"emergency": "911",
		"office": None,
		"booked_by": None,
		"lead": None,
		"site": None,
		"hotels": [],
	}
	parts = (
		("office", _office_contact),
		("booked_by", lambda: _booked_by_contact(doc)),
		("lead", lambda: _lead_contact(doc)),
		("site", lambda: _site_contact(doc)),
		("hotels", lambda: _hotel_contacts(doc, viewing_employee, hotels)),
	)
	for key, build in parts:
		try:
			contacts[key] = build()
		except Exception:
			contacts[key] = [] if key == "hotels" else None
			frappe.log_error(
				title="Trip contacts", message=f"{doc.get('name')}: {key}\n{frappe.get_traceback()}"
			)
	return contacts


# ---------------------------------------------------------------- trip sheet


def _sheet_employee(doc):
	"""Whose Trip Sheet is being printed: the request's ``as`` (``&as=<employee>`` on the print
	and PDF links) when that is someone on this trip's crew, else ``None`` — the whole trip.

	Read from ``frappe.form_dict`` because frappe's print routes hand a format nothing else.
	Checked against frappe ``version-16``: ``frappe.call`` passes a whitelisted method only the
	arguments its signature names (``get_newargs``), so ``download_pdf`` never sees ``as``, but
	``form_dict`` keeps it, and ``print_utils.get_print`` only *sets* ``doctype``/``name``/
	``format``/... on it before rendering ``printview`` in the same request. Compared against
	the crew and never looked up, as ``_crew_member_or_throw`` does."""
	form = getattr(frappe, "form_dict", None) or {}
	try:
		asked = form.get("as")
	except Exception:
		return None
	if isinstance(asked, str) and asked and any(t.employee == asked for t in doc.get("travelers") or []):
		return asked
	return None


def _format_money(total, currency):
	try:
		from frappe.utils import fmt_money

		return fmt_money(total, currency=currency or None)
	except Exception:
		return f"{currency} {total:,.2f}".strip()


def ee_trip_sheet(doc):
	"""The Trip Sheet print format's data — a Jinja global (hooks.py ``jinja.methods``) that
	``travel_management/print_formats/trip_sheet.html`` reads instead of ``doc``.

	Why a global and not ``doc.*``: every money field on Travel Trip is permlevel 0, so a
	template reading the document could print any of them to anyone who can print the trip
	(the Employee role can). Only what the server hands the template can keep a crew member's
	copy clean, so the whole payload is built here: ``views.build_trip_sheet`` from
	``shape_itinerary`` (no money), ``_trip_contacts`` and the trip's files (titles and kinds).
	The one figure — the cost total — is added only for a travel coordinator, and only on the
	whole trip's sheet.

	``&as=<employee>`` prints that person's sheet (``_sheet_employee``). The caller's right to
	the trip is checked again here, as the print view checks it (read or print), because a
	Jinja global is reachable from any template on the site, not just this format.

	**The sheet is built from the trip as saved, never from the document it is handed.**
	frappe v16's whitelisted ``printview.get_html_and_style`` renders a document posted as
	JSON (``frappe.get_doc(json.loads(doc), check_permission=True)``, the desk's own print
	preview), and the Travel Trip permission hook passes a document with no ``creation``
	(``permissions.py``: it is being created). Built from that copy, the contacts card read
	whichever owner, trip lead, job and hotels the caller named — any user's phone and email,
	any customer's contact — with no record saved and no trace. Its ``creation``, ``owner`` and
	crew are the caller's to write as well, so no check on the copy can vouch for it: the trip
	is loaded again by name and checked, and a name that is not a saved trip is refused ("Save
	the trip before printing its Trip Sheet."). So the sheet shows the trip as last saved, like
	every other view of it, whatever the form holds unsaved.

	**Money only for a coordinator who asked for it.** The total is added when the person
	printing is a coordinator, in a web request, and not for an attachment. An emailed sheet
	(the composer's "Attach Document Print", a Notification's attachment) is rendered later by
	the email queue as Administrator (``print_utils.attach_print`` sets
	``flags.ignore_print_permissions`` for exactly that render), and Administrator is a
	coordinator, so a crew member who emailed the default format sent the priced copy. An
	emailed sheet is always the unpriced one, a coordinator's included: it leaves the building.
	"""
	if getattr(doc, "doctype", None) != "Travel Trip":
		return {}
	name = doc.get("name") if hasattr(doc, "get") else getattr(doc, "name", None)
	if not isinstance(name, str) or not name or not frappe.db.exists("Travel Trip", name):
		frappe.throw(_("Save the trip before printing its Trip Sheet."))
	doc = frappe.get_doc("Travel Trip", name)
	if not (
		frappe.has_permission("Travel Trip", "read", doc=doc)
		or frappe.has_permission("Travel Trip", "print", doc=doc)
	):
		frappe.throw(_("You do not have access to this trip."), frappe.PermissionError)

	employee = _sheet_employee(doc)
	is_coordinator = (
		_is_coordinator()
		and not getattr(frappe.flags, "ignore_print_permissions", False)
		and getattr(frappe.local, "request", None) is not None
	)
	printed_on = ""
	try:
		from frappe.utils import format_date, nowdate

		printed_on = format_date(nowdate())
	except Exception:
		pass
	try:
		itinerary_url = frappe.utils.get_url(trip_views.itinerary_path(doc.name))
	except Exception:
		itinerary_url = None
	# One lookup cache for the itinerary and the contacts card: a hotel's Address is read once.
	cache = {}
	return trip_views.build_trip_sheet(
		doc,
		functools.partial(shape_itinerary, poi_cache=cache),
		is_coordinator=is_coordinator,
		contacts=_trip_contacts(doc, employee, hotels=_hotel_details(doc, cache)),
		employee=employee,
		currency=_trip_currency(doc) if is_coordinator and not employee else None,
		format_money=_format_money,
		itinerary_url=itinerary_url,
		printed_on=printed_on,
		job_title=_site_title(doc),
	)


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
