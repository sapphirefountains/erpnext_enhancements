# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The read-only views of a saved trip on Plan a Trip, assembled in one payload.

Four ways to look at one trip, chosen by the office (2026-09-26):

* **Overview** — every day of the trip, start to end, each booking once with who is on it
  (and each person's own confirmation number), and what the checklist says is missing,
  shown where it is missing.
* **Grid** — the crew down the side, the days across the top, an icon per flight, hotel
  night, ride, stop and shipment in each cell.
* **Compare** — one column per person, one row per day.
* **View as** — exactly what one person's ``/itinerary`` shows them.

``api.travel.get_trip_views`` serves all four from :func:`build_trip_views`, which reuses
``shape_itinerary`` — the function behind ``/itinerary`` and the itinerary email — so the
page, the phone and the inbox cannot disagree about who is on what.

All but money
-------------
Anyone who can open the trip sees every person's bookings, times, confirmation numbers and
booking files here: the crew already read and edit the whole trip on the form. **Money is
for coordinators only** on these views — cost, who paid, billable, per diem, claims,
advances, totals. That is enforced by what this payload *contains*, not by the page hiding
it: ``shape_itinerary`` carries no money at all, the checklist's cost gaps are dropped, and
the money block is built by a separate function (:func:`build_money`) that only a
coordinator's payload calls. Every money field on Travel Trip is permlevel 0, so this is a
rule about these views, not a lock on money in general — the form and the Plan a Trip steps
are unchanged.

Pure: no ``frappe`` import. The shaping function is passed in, so
``tests/test_travel_views.py`` builds the whole payload without a site.
"""

from datetime import timedelta
from urllib.parse import quote

from erpnext_enhancements.travel_management.completeness import find_gaps, group_key, to_date

#: The longest run of days laid out from the trip's start. A mistyped end date (2062 for
#: 2026) would otherwise ask the page to draw sixteen thousand columns.
MAX_DAYS = 120

#: The per-person booking tables, in the order the page shows them.
BOOKING_TABLES = ("flights", "accommodations", "ground_transport")


def _get(row, field):
	if row is None:
		return None
	if isinstance(row, dict):
		return row.get(field)
	return getattr(row, field, None)


def _num(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _iso(value):
	"""'YYYY-MM-DD' for a date, a datetime or an ISO string; ``None`` for anything else."""
	try:
		day = to_date(value)
	except (TypeError, ValueError):
		return None
	return day.isoformat() if day else None


def itinerary_path(trip):
	"""The ``/itinerary`` address that opens this trip — the emails, the calendar invite
	and the page all link here. Relative; callers that need a full URL wrap it in
	``get_url``."""
	return f"/itinerary?trip={quote(str(trip or ''), safe='')}"


def crew(doc):
	"""Who is on the trip, in the trip's order, each with their own dates — or the trip's
	when they have none."""
	start, end = _iso(_get(doc, "start_date")), _iso(_get(doc, "end_date"))
	people, seen = [], set()
	for row in _get(doc, "travelers") or []:
		employee = _get(row, "employee")
		if not employee or employee in seen:
			continue
		seen.add(employee)
		people.append(
			{
				"employee": employee,
				"employee_name": _get(row, "employee_name") or employee,
				"from_date": _iso(_get(row, "from_date")) or start,
				"to_date": _iso(_get(row, "to_date")) or end,
				"is_trip_lead": 1 if _num(_get(row, "is_trip_lead")) else 0,
			}
		)
	return people


def trip_days(start, end, extra=()):
	"""Every date from ``start`` to ``end`` inclusive (at most :data:`MAX_DAYS`), plus any
	date in ``extra`` that falls outside — a flight booked the day before the trip starts
	still gets its column. Sorted, unique, 'YYYY-MM-DD'."""
	first, last = to_date(_iso(start)), to_date(_iso(end))
	days = set()
	if first and last and last >= first:
		span = min((last - first).days + 1, MAX_DAYS)
		days.update((first + timedelta(days=i)).isoformat() for i in range(span))
	else:
		days.update(day.isoformat() for day in (first, last) if day)
	for value in extra:
		day = _iso(value)
		if day:
			days.add(day)
	return sorted(days)


def booking_counts(doc):
	"""How many bookings each table holds, counted the way the Review step counts its cards:
	every row that shares a ``booking_group`` (the checklist's :func:`group_key`) is one
	booking, and each shipment is one.

	The Overview's tiles read these rather than counting itinerary items. A room with no
	dates yet has no item on any day, so counting items put "Rooms 3" on the Overview and
	"Rooms 4" on Review for the same trip.
	"""
	counts = {table: len({group_key(row) for row in _get(doc, table) or []}) for table in BOOKING_TABLES}
	counts["freight"] = len(_get(doc, "freight") or [])
	return counts


def build_money(doc, currency=None):
	"""Coordinators only: what each booking cost and who paid, keyed exactly like the
	itinerary items' ``group`` — the checklist's :func:`group_key`: a booking's or a
	shipment's ``booking_group``, or ``row:<name>`` for a row typed on the form without one.

	``cost`` is the sum of the booking's rows (one row per person, the total split across
	them). ``paid_by`` is the first row's, as the page's cards read it; ``paid_by_name``
	names whoever paid when an employee did — every distinct payer, because on a booking of
	one row per person each person may have paid for their own seat. ``total`` is the
	bookings plus the freight, the same sum as the Review step's "Booked so far".
	"""
	names = {person["employee"]: person["employee_name"] for person in crew(doc)}
	groups = {}

	def add(key, row):
		entry = groups.get(key)
		if entry is None:
			entry = groups[key] = {
				"cost": 0.0,
				"paid_by": _get(row, "paid_by") or None,
				"paid_by_name": None,
				"_payers": [],
			}
		entry["cost"] += _num(_get(row, "cost"))
		payer = _get(row, "paid_by_traveler")
		if _get(row, "paid_by") == "Employee" and payer:
			name = names.get(payer) or payer
			if name not in entry["_payers"]:
				entry["_payers"].append(name)

	for table in BOOKING_TABLES:
		for row in _get(doc, table) or []:
			add(group_key(row), row)
	for row in _get(doc, "freight") or []:
		# One row is one shipment; since trip files it has a booking_group of its own, and
		# a row typed on the form before that is still row:<name>. group_key covers both,
		# as shape_itinerary's freight items do.
		add(group_key(row), row)

	for entry in groups.values():
		payers = entry.pop("_payers")
		entry["cost"] = round(entry["cost"], 2)
		entry["paid_by_name"] = ", ".join(payers) or None

	return {
		"currency": currency or "",
		"total": round(sum(entry["cost"] for entry in groups.values()), 2),
		"groups": groups,
	}


def build_trip_views(doc, shape, is_coordinator, viewer_employee=None, currency=None):
	"""The payload behind Plan a Trip's Overview, Grid, Compare and View-as screens.

	Args:
		doc: the Travel Trip (a Document, or anything with the same attributes).
		shape: ``api.travel.shape_itinerary`` — ``shape(doc, employee, poi_cache=...)``.
		is_coordinator: whether the viewer may see money.
		viewer_employee: the session user's Employee, or None.
		currency: the trip company's currency, for the money block.

	One POI cache is shared across every person's itinerary, so a crew of eight costs one
	lookup per place, not eight.
	"""
	poi_cache = {}
	whole = shape(doc, None, poi_cache=poi_cache)
	people_list = crew(doc)
	people, people_documents = {}, {}
	for person in people_list:
		shaped = shape(doc, person["employee"], poi_cache=poi_cache)
		people[person["employee"]] = shaped["days"]
		people_documents[person["employee"]] = shaped.get("documents") or []

	item_dates = [day["date"] for day in whole["days"]]
	for days in people.values():
		item_dates.extend(day["date"] for day in days)

	gaps = find_gaps(doc)
	if not is_coordinator:
		# "No cost entered" is itself a fact about money.
		gaps = [gap for gap in gaps if gap.get("check") != "cost"]

	return {
		"trip": whole["trip"],
		"purpose": whole["purpose"],
		"status": whole["status"],
		"travel_type": whole["travel_type"],
		"start_date": _iso(_get(doc, "start_date")),
		"end_date": _iso(_get(doc, "end_date")),
		"travel_for_doctype": whole["travel_for_doctype"],
		"travel_for": whole["travel_for"],
		"is_coordinator": bool(is_coordinator),
		"viewer_employee": viewer_employee or None,
		"itinerary_url": itinerary_path(whole["trip"]),
		"days": trip_days(_get(doc, "start_date"), _get(doc, "end_date"), item_dates),
		"crew": people_list,
		"whole": whole["days"],
		"people": people,
		# The trip's files, as shape_itinerary lists them (no receipts: a receipt is money):
		# every file for the whole crew, and the ones each person sees on their /itinerary.
		"documents": whole.get("documents") or [],
		"people_documents": people_documents,
		"bookings": booking_counts(doc),
		"gaps": gaps,
		# Built only for a coordinator: a non-coordinator's payload never holds a figure
		# for the page to hide.
		"money": build_money(doc, currency) if is_coordinator else None,
	}
