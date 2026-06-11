"""Read-side travel endpoints: desk calendar events, the mobile itinerary
page (``/itinerary``), and the trip-form map.

Document creation (claims, advances, outcomes, vehicle logs) lives in
``erpnext_enhancements.travel_management.api`` — this module only shapes data
for display.

Security:
	- ``get_my_trips`` / ``get_itinerary_bootstrap`` derive the employee from
	  the SESSION user (same model as ``api.time_kiosk``) — never from a
	  client-supplied parameter.
	- ``get_trip_itinerary`` / ``get_trip_pois`` gate through
	  ``frappe.has_permission`` on the trip, which the Travel Trip permission
	  hooks scope to owner/crew/coordinators
	  (``travel_management.permissions``).
	- ``get_events`` uses ``frappe.get_list`` so the same row-level scoping
	  applies to the calendar.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, today

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
		"trips": get_my_trips(),
		"csrf_token": frappe.sessions.get_csrf_token(),
	}


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
		fields=[
			"name",
			"purpose",
			"status",
			"travel_type",
			"start_date",
			"end_date",
			"travel_for_doctype",
			"travel_for_name",
		],
		order_by="start_date asc",
	)


@frappe.whitelist()
def get_trip_itinerary(trip):
	"""Day-by-day itinerary for one trip, shaped for the mobile page:
	``{trip, purpose, status, days: [{date, items: [...]}]}`` with typed items
	(flight / hotel_checkin / hotel_checkout / ground / agenda) merged
	chronologically. When the viewer is a traveler (not a coordinator),
	segments pinned to a different single traveler are filtered out."""
	doc = frappe.get_doc("Travel Trip", trip)
	frappe.has_permission("Travel Trip", "read", doc=doc, throw=True)

	session_emp = _session_employee()
	viewing_employee = (
		session_emp if any(t.employee == session_emp for t in doc.travelers) else None
	)
	return shape_itinerary(doc, viewing_employee)


def shape_itinerary(doc, viewing_employee=None):
	"""Build the typed day-by-day itinerary dict from a Travel Trip document.

	No permission checks — callers gate access. ``viewing_employee`` filters
	out segments pinned to a different single traveler (used both by the
	/itinerary page and the per-traveler itinerary emails in
	travel_management.notifications)."""

	def visible(row_traveler):
		return not row_traveler or not viewing_employee or row_traveler == viewing_employee

	items = []

	for row in doc.flights:
		if not visible(row.traveler):
			continue
		date = getdate(row.departure_time) if row.departure_time else getdate(doc.start_date)
		items.append(
			{
				"type": "flight",
				"date": str(date),
				"sort_time": str(row.departure_time or ""),
				"airline": row.airline,
				"flight_number": row.flight_number,
				"departure_airport": row.departure_airport,
				"departure_time": str(row.departure_time) if row.departure_time else None,
				"arrival_airport": row.arrival_airport,
				"arrival_time": str(row.arrival_time) if row.arrival_time else None,
				"booking_reference": row.booking_reference,
				"attachment": row.attachment,
			}
		)

	for row in doc.accommodations:
		if not visible(row.traveler):
			continue
		base = {
			"hotel": row.hotel_lodging,
			"address": row.address,
			"booking_confirmation": row.booking_confirmation,
			"attachment": row.attachment,
		}
		if row.check_in_date:
			items.append(
				dict(base, type="hotel_checkin", date=str(row.check_in_date), sort_time="23:00")
			)
		if row.check_out_date:
			items.append(
				dict(base, type="hotel_checkout", date=str(row.check_out_date), sort_time="00:30")
			)

	for row in doc.ground_transport:
		if not visible(row.traveler):
			continue
		date = getdate(row.pickup_datetime) if row.pickup_datetime else getdate(doc.start_date)
		items.append(
			{
				"type": "ground",
				"date": str(date),
				"sort_time": str(row.pickup_datetime or ""),
				"transport_type": row.transport_type,
				"provider": row.supplier or row.vehicle,
				"pickup_location": row.pickup_location,
				"dropoff_location": row.dropoff_location,
				"pickup_datetime": str(row.pickup_datetime) if row.pickup_datetime else None,
				"booking_reference": row.booking_reference,
				"attachment": row.attachment,
			}
		)

	poi_cache = {}

	def poi_details(poi_name):
		if not poi_name:
			return None
		if poi_name not in poi_cache:
			poi = frappe.db.get_value(
				"Travel POI",
				poi_name,
				["poi_name", "category", "geolocation"],
				as_dict=True,
			)
			latlng = _poi_latlng(poi.geolocation) if poi else None
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
				"sort_time": str(row.time or ""),
				"time": str(row.time) if row.time else None,
				"activity": row.activity_description,
				"related_party_doctype": row.related_party_doctype,
				"related_party": row.related_party_name,
				"poi": poi_details(row.location),
				"visit_notes": row.visit_notes,
			}
		)

	days = {}
	for item in items:
		days.setdefault(item["date"], []).append(item)
	for day_items in days.values():
		day_items.sort(key=lambda i: i.get("sort_time") or "")

	return {
		"trip": doc.name,
		"purpose": doc.purpose,
		"status": doc.status,
		"travel_type": doc.travel_type,
		"start_date": str(doc.start_date),
		"end_date": str(doc.end_date),
		"travel_for_doctype": doc.travel_for_doctype,
		"travel_for": doc.travel_for_name,
		"days": [{"date": d, "items": days[d]} for d in sorted(days)],
	}


# --------------------------------------------------------------------- maps


@frappe.whitelist()
def get_trip_pois(trip):
	"""POIs referenced by a trip's agenda, with coordinates, for the form map
	(public/js/travel/travel_trip_map.js). One entry per POI, carrying the
	agenda dates that visit it."""
	doc = frappe.get_doc("Travel Trip", trip)
	frappe.has_permission("Travel Trip", "read", doc=doc, throw=True)

	pois = {}
	for row in doc.itinerary:
		if not row.location:
			continue
		entry = pois.get(row.location)
		if not entry:
			poi = frappe.db.get_value(
				"Travel POI",
				row.location,
				["poi_name", "category", "geolocation"],
				as_dict=True,
			)
			if not poi:
				continue
			latlng = _poi_latlng(poi.geolocation)
			if not latlng:
				continue
			entry = pois[row.location] = {
				"poi": row.location,
				"label": poi.poi_name,
				"category": poi.category,
				"lat": latlng[0],
				"lng": latlng[1],
				"agenda_dates": [],
			}
		if str(row.date) not in entry["agenda_dates"]:
			entry["agenda_dates"].append(str(row.date))

	return list(pois.values())


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
