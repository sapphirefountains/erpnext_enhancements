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
* **Map** (PR 3, 2026-09-26) — every airport, hotel, stop, pickup, drop-off and freight
  delivery on one map, with the flights and drives between them (:func:`trip_places`).

``api.travel.get_trip_views`` serves all of them from :func:`build_trip_views`, which reuses
``shape_itinerary`` — the function behind ``/itinerary`` and the itinerary email — so the
page, the phone and the inbox cannot disagree about who is on what. The printed **Trip
Sheet** is built here too (:func:`build_trip_sheet`), from the same shape, and so are the
contacts card's rows for print and email (:func:`contact_list`, :func:`contact_rows`).

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


#: The Print Format ``travel_management/setup_print_formats.py`` upserts on every migrate.
#: Named here so the format and the links to it cannot drift apart.
TRIP_SHEET_FORMAT = "Trip Sheet"


def trip_sheet_url(trip, as_employee=None, printview=False):
	"""The one place the Trip Sheet's address is spelled: the PDF by default, or the
	browser print view with ``printview``. ``as_employee`` makes it that person's sheet
	(``&as=``, read by ``api.travel.ee_trip_sheet``; anyone not on the crew is ignored there
	and gets the whole trip).

	The PDF link names ``pdf_generator=chrome`` on purpose: frappe v16's ``download_pdf``
	sets wkhtmltopdf when the request names none, before it ever reads the format's own
	setting, so without it the sheet would render on the backend this site moved off.
	Relative, like :func:`itinerary_path`."""
	common = (
		f"doctype=Travel%20Trip&name={quote(str(trip or ''), safe='')}"
		f"&format={quote(TRIP_SHEET_FORMAT, safe='')}&no_letterhead=1"
	)
	if printview:
		url = f"/printview?{common}"
	else:
		url = f"/api/method/frappe.utils.print_format.download_pdf?{common}&pdf_generator=chrome"
	if as_employee:
		url += f"&as={quote(str(as_employee), safe='')}"
	return url


#: Google Maps' documented URL scheme (no key, opens the app on a phone).
MAPS_SEARCH_URL = "https://www.google.com/maps/search/?api=1&query="
MAPS_DIRECTIONS_URL = "https://www.google.com/maps/dir/?api=1&destination="


def maps_search_url(text):
	"""A Google Maps search for ``text`` — "urgent care near 1 Main St, Boise"."""
	return MAPS_SEARCH_URL + quote(str(text or "").strip(), safe="")


def maps_directions_url(text):
	"""Google Maps directions from wherever the phone is to ``text``."""
	return MAPS_DIRECTIONS_URL + quote(str(text or "").strip(), safe="")


def tel_href(phone):
	"""A ``tel:`` link for a phone number stored as text, or ``None`` for one not to dial.

	Digits only, plus a leading ``+`` when the number was written with one. An extension
	("ext. 4", "x12", "#3", ";ext=4") is left off: "801-555-0100 ext. 4" dials 8015550100,
	since its digits run into the number would dial a different one, and the printed text
	still shows it. A note beside the number is dropped ("Front desk 702-555-0150" dials
	7025550150), but a number spelled in letters ("1-800-FLOWERS") would dial something else
	entirely (1800), so it is no number to dial, and neither is one with fewer than three
	digits or more than fifteen: those are shown as typed.

	The same rule as the two pages' (``plan_a_trip.js`` ``tp_tel``, ``itinerary.js``
	``telHref``); ``tests/test_travel_planner.py`` runs all three over one table."""
	import re  # lazily: this module stays import-light for the bench-free suites

	text = re.sub(r"^\s*tel:", "", str(phone or ""), flags=re.I)
	text = re.split(r"ext|x|#|;|,", text, maxsplit=1, flags=re.I)[0]
	if re.search(r"[a-z]", text, flags=re.I):
		runs = re.findall(r"\+?[0-9][0-9\s().-]*[0-9]", text)
		if len(runs) != 1 or len(re.sub(r"[^0-9]", "", runs[0])) < 7:
			return None
		text = runs[0]
	digits = re.sub(r"[^0-9]", "", text)
	if not 3 <= len(digits) <= 15:
		return None
	return "tel:" + ("+" if text.lstrip().startswith("+") else "") + digits


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


def build_trip_views(
	doc, shape, is_coordinator, viewer_employee=None, currency=None, hotels=None, sheet_available=True
):
	"""The payload behind Plan a Trip's Overview, Grid, Compare, View-as and Map screens.

	Args:
		doc: the Travel Trip (a Document, or anything with the same attributes).
		shape: ``api.travel.shape_itinerary`` — ``shape(doc, employee, poi_cache=...)``.
		is_coordinator: whether the viewer may see money.
		viewer_employee: the session user's Employee, or None.
		currency: the trip company's currency, for the money block.
		hotels: ``{hotel: {address, lat, lng}}`` (``api.travel._hotel_details``), so the
			map asks a geocoder for a hotel's street address rather than its name. Optional:
			without it a hotel is looked up by name.
		sheet_available: whether the Trip Sheet print format exists. When it does not,
			``sheet_url`` is None and ``people_sheet_urls`` is empty: frappe prints a missing
			format as Standard, costs included (``api.travel._sheet_available``).

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
	# Paperwork gaps stay, for everyone: a booking's files are not money. They are the quieter
	# tally (completeness.counted_gaps / files_not_attached), so the Overview counts them apart
	# from "Still missing" and shows them muted, never as a red flag.

	places, legs = trip_places(whole["days"], [p["employee_name"] for p in people_list], hotels)

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
		# The whole-trip map (no money: where and when, never what it cost).
		"places": places,
		"legs": legs,
		# The printed Trip Sheet: the whole trip's, and each person's for View as. None and {}
		# while the format is missing: the page then draws no link.
		"sheet_url": trip_sheet_url(whole["trip"]) if sheet_available else None,
		"people_sheet_urls": {
			p["employee"]: trip_sheet_url(whole["trip"], p["employee"]) for p in people_list
		}
		if sheet_available
		else {},
		# Built only for a coordinator: a non-coordinator's payload never holds a figure
		# for the page to hide.
		"money": build_money(doc, currency) if is_coordinator else None,
	}


# --------------------------------------------------------------------------- the whole-trip map


def _hhmm(value):
	"""'HH:MM' from a 'YYYY-MM-DD HH:MM:SS' datetime or an 'HH:MM[:SS]' time, else ``None``.

	A datetime at exactly midnight is "no time given" (Plan a Trip stores a flight or a drive
	with no time yet at 00:00:00), as the emails read it (``itinerary_text.clock``); a bare
	time of 00:00 is a real midnight."""
	text = str(value or "").strip()
	if not text:
		return None
	is_datetime = len(text) > 8 and text[4:5] == "-"
	pieces = (text[11:] if is_datetime else text).split(":")
	if len(pieces) < 2:
		return None
	try:
		hour, minute = int(pieces[0]), int(pieces[1][:2])
	except ValueError:
		return None
	if is_datetime and hour == 0 and minute == 0:
		return None
	return f"{hour:02d}:{minute:02d}"


def _airport(text):
	"""``(label, query)`` for an airport as the office typed it. A three-letter code is
	asked for as "PHX airport": a geocoder given "PHX" alone may answer with anything."""
	label = str(text or "").strip()
	if not label:
		return "", ""
	if len(label) == 3 and label.isalpha():
		label = label.upper()
	if "airport" in label.lower():
		return label, label
	return label, f"{label} airport"


def _typed_airport_code(text):
	"""Whether a drive's end is typed like an airport code ("LAS"): three capitals. Only half the
	test — :func:`trip_places` also asks that one of the trip's flights uses that airport, since
	"TBD", "TBA" or "BYU" are three capitals too."""
	value = str(text or "").strip()
	return len(value) == 3 and value.isalpha() and value.isupper()


#: What the office types for a place that is not known yet. Never a pin and never looked up:
#: "TBD airport" is a real search that answers with somewhere, and the map would zoom out to it.
PLACEHOLDERS = frozenset({"tbd", "tba", "tbc", "n/a", "na", "none", "?", "-"})


def _placeholder(text):
	return str(text or "").strip().casefold() in PLACEHOLDERS


def trip_places(days, crew_names=(), hotels=None):
	"""Every place on the trip and the flights and drives between them, for the whole-trip
	map: ``(places, legs)`` from the whole crew's itinerary (``shape_itinerary(doc)["days"]``).

	A place is ``{key, kind, label, query, lat, lng, days, first_time, who, group}``:

	* ``kind`` — ``airport`` (both ends of every flight), ``hotel``, ``stop`` (a schedule
	  stop's Place), ``pickup`` / ``dropoff`` (a drive's two ends; an end typed as the code of
	  an airport one of the trip's flights uses is that airport), ``freight`` (where a
	  shipment is delivered);
	* ``query`` — text a geocoder can use: "PHX airport", a hotel's street address (from
	  ``hotels``, else its name), a drive's end or a delivery address as typed, a stop's
	  Place name;
	* ``lat`` / ``lng`` — numbers when the point is known (a stop's Place with a point, a
	  hotel whose Address was picked from Google), else ``None`` and the page geocodes
	  ``query``;
	* ``days`` — the dates it comes up on, sorted: a hotel is on every night of each stay,
	  check-in to check-out, since the crew starts and ends each of those days there;
	  ``first_time`` — 'HH:MM' on the first of them, when a time is known; ``who`` — the
	  names of everyone who goes there; ``group`` — the booking it first came from
	  (``completeness.group_key``), ``None`` for a stop.

	One place per ``(kind, point)`` when the point is known, else per ``(kind, query)``,
	case-insensitive: two rooms at one hotel are one hotel, and the airport a flight lands
	at is the one the next flight leaves from. ``key`` is ``<kind>-<n>``. A place typed as a
	placeholder (:data:`PLACEHOLDERS`: "TBD", "N/A", ...) is left off, and so is any leg to it.

	A leg is ``{day, from, to, kind, who}`` — ``flight`` from departure to arrival airport,
	``drive`` from a drive's pickup to its drop-off — for each one with both ends. No money:
	where and when, never what it cost.
	"""
	hotels = hotels or {}
	crew_names = [name for name in crew_names if name]
	places, by_identity, visits, legs = [], {}, {}, []
	# Each room's stay, {(hotel place, booking): [check-in, check-out]}: its nights are added to
	# the hotel's days after the walk, and not to its visits, so its number and time stay those
	# of the check-in.
	stays = {}
	# The airports the trip's flights use, as they are looked up ("PHX airport", whether the flight
	# says "PHX", "phx" or "PHX Airport"). A drive end typed as the code of one of them is that
	# airport ("LAS": the rental counter is there); any other three capitals is a place as typed.
	flight_airports = set()
	for day in days or []:
		for item in day.get("items") or []:
			if item.get("type") == "flight":
				for end in (item.get("departure_airport"), item.get("arrival_airport")):
					query = _airport(end)[1]
					if query and not _placeholder(end):
						flight_airports.add(query.casefold())

	def who_of(item):
		kind = item.get("type")
		if kind == "freight":
			return [item["received_by"]] if item.get("received_by") else list(crew_names)
		if kind == "agenda" or item.get("whole_crew"):
			return list(crew_names)
		return list(item.get("travelers") or crew_names)

	def place(kind, label, query, item, day, time, lat=None, lng=None):
		query = str(query or "").strip()
		if lat is not None and lng is not None:
			identity = (kind, round(float(lat), 6), round(float(lng), 6))
		elif query:
			identity = (kind, query.casefold())
		else:
			return None  # nowhere to put it
		entry = by_identity.get(identity)
		if entry is None:
			entry = by_identity[identity] = {
				"key": f"{kind}-{sum(1 for p in places if p['kind'] == kind) + 1}",
				"kind": kind,
				"label": str(label or "").strip() or query,
				"query": query or None,
				"lat": float(lat) if lat is not None and lng is not None else None,
				"lng": float(lng) if lat is not None and lng is not None else None,
				"days": [],
				"first_time": None,
				"who": [],
				"group": item.get("group"),
			}
			places.append(entry)
			visits[entry["key"]] = []
		if day and day not in entry["days"]:
			entry["days"].append(day)
		visits[entry["key"]].append((day or "", time))
		for name in who_of(item):
			if name not in entry["who"]:
				entry["who"].append(name)
		return entry["key"]

	def airport(text, item, day, time):
		if _placeholder(text):
			return None
		return place("airport", *_airport(text), item, day, time)

	def drive_end(kind, text, item, day, time):
		if _placeholder(text):
			return None
		if _typed_airport_code(text) and _airport(text)[1].casefold() in flight_airports:
			return airport(text, item, day, time)
		return place(kind, text, text, item, day, time)

	def leg(kind, day, start, end, item):
		if start and end and start != end:
			legs.append({"day": day, "from": start, "to": end, "kind": kind, "who": who_of(item)})

	for day in days or []:
		date = day.get("date")
		for item in day.get("items") or []:
			kind = item.get("type")
			if kind == "flight":
				leaves, lands = item.get("departure_time"), item.get("arrival_time")
				start = airport(item.get("departure_airport"), item, date, _hhmm(leaves))
				end = airport(item.get("arrival_airport"), item, _iso(lands) or date, _hhmm(lands))
				leg("flight", date, start, end, item)
			elif kind in ("hotel_checkin", "hotel_checkout"):
				info = hotels.get(item.get("hotel")) or {}
				key = place(
					"hotel",
					item.get("hotel"),
					info.get("address") or item.get("hotel"),
					item,
					date,
					_hhmm(item.get("time")),
					info.get("lat"),
					info.get("lng"),
				)
				if key:
					stay = stays.setdefault((key, item.get("group")), [None, None])
					stay[0 if kind == "hotel_checkin" else 1] = date
			elif kind == "ground":
				arrives = item.get("arrival_datetime")
				start = drive_end(
					"pickup", item.get("pickup_location"), item, date, _hhmm(item.get("pickup_datetime"))
				)
				end = drive_end(
					"dropoff", item.get("dropoff_location"), item, _iso(arrives) or date, _hhmm(arrives)
				)
				leg("drive", date, start, end, item)
			elif kind == "freight":
				if not _placeholder(item.get("deliver_to")):
					place(
						"freight",
						item.get("deliver_to"),
						item.get("deliver_to"),
						item,
						date,
						_hhmm(item.get("delivery_from")),
					)
			elif kind == "agenda":
				poi = item.get("poi") or {}
				if poi.get("poi_name"):
					place(
						"stop",
						poi["poi_name"],
						poi["poi_name"],
						item,
						date,
						_hhmm(item.get("time")),
						poi.get("lat"),
						poi.get("lng"),
					)

	by_key = {entry["key"]: entry for entry in places}
	for (key, _group), (check_in, check_out) in stays.items():
		if check_in and check_out:
			entry = by_key[key]
			for night in trip_days(check_in, check_out):
				if night not in entry["days"]:
					entry["days"].append(night)

	for entry in places:
		entry["days"].sort()
		first_day = entry["days"][0] if entry["days"] else ""
		times = sorted(time for day, time in visits[entry["key"]] if day == first_day and time)
		entry["first_time"] = times[0] if times else None
	return places, legs


# --------------------------------------------------------------------------- contacts


def contact_list(contacts):
	"""The contacts card as rows, in the order you would call them: ``[{role, name, detail,
	phone, tel, email, address, links}]`` from ``api.travel._trip_contacts``. ``links`` is
	``[(url, label)]``: a hotel's nearest urgent care and directions, the job site's
	directions. The Trip Sheet prints these rows; the itinerary email is :func:`contact_rows`.

	Only the contacts ``_trip_contacts`` returns ever reach here, and it never returns a crew
	member's own phone, email, next of kin or address — the trip lead's work mobile is the
	one number from an Employee record."""
	contacts = contacts or {}
	rows = []

	def add(role, name, detail=None, phone=None, email=None, address=None, links=()):
		phone = str(phone or "").strip() or None
		rows.append(
			{
				"role": role,
				"name": str(name or "").strip(),
				"detail": str(detail or "").strip() or None,
				"phone": phone,
				"tel": tel_href(phone),
				"email": str(email or "").strip() or None,
				"address": str(address or "").strip() or None,
				"links": [(url, label) for url, label in links if url],
			}
		)

	emergency = str(contacts.get("emergency") or "911")
	add("Emergency", f"Call {emergency}", phone=emergency)
	office = contacts.get("office")
	if office:
		add(
			"Travel desk",
			office.get("label") or "Travel desk",
			phone=office.get("phone"),
			email=office.get("email"),
		)
	booked_by = contacts.get("booked_by")
	if booked_by:
		add("Booked by", booked_by.get("name"), phone=booked_by.get("phone"), email=booked_by.get("email"))
	lead = contacts.get("lead")
	if lead:
		add("Trip lead", lead.get("name"), phone=lead.get("phone"))
	site = contacts.get("site")
	if site:
		address = site.get("address")
		add(
			"Job site",
			site.get("contact_name") or site.get("label"),
			detail=site.get("label") if site.get("contact_name") else None,
			phone=site.get("phone"),
			email=site.get("email"),
			address=address,
			links=[(maps_directions_url(address), "Directions")] if address else (),
		)
	for hotel in contacts.get("hotels") or []:
		add(
			"Hotel",
			hotel.get("name"),
			phone=hotel.get("phone"),
			address=hotel.get("address"),
			links=[
				(hotel.get("urgent_care_url"), "Nearest urgent care"),
				(hotel.get("directions_url"), "Directions"),
			],
		)
	return rows


def mailto_href(email):
	"""A ``mailto:`` link for an address that is plainly one, else ``None``. The same rule as
	``/itinerary``'s ``mailHref``: ``ee.table`` writes a link cell's address into ``href``
	unescaped, so nothing but a plain address may get there."""
	import re  # lazily, as tel_href

	text = str(email or "").strip()
	if not re.fullmatch(r"[A-Za-z0-9._+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}", text):
		return None
	return "mailto:" + quote(text, safe="@")


def contact_rows(contacts):
	"""The itinerary email's Contacts table: ``[[who, name, how to reach them]]`` for ``ee.table``
	(a two-item cell is a link there). How to reach them is the phone as a ``(tel link, phone)``
	pair, printed as ``print_style.format_phone`` prints it ("(801) 555-0100" for ten North
	American digits, anything else as stored); else a phone that is no number to dial, as typed;
	else the email as a ``(mailto link, email)`` pair, or as text when it is not plainly an
	address; else ``None``. Every contact the card admits is reachable from the email: a travel
	desk set up with only an email used to be a row reading "—" (2026-09-27)."""
	from erpnext_enhancements.print_style import format_phone

	out = []
	for row in contact_list(contacts):
		name = row["name"] + (f" ({row['detail']})" if row["detail"] else "")
		if row["tel"]:
			reach = (row["tel"], format_phone(row["phone"]))
		elif row["phone"]:
			reach = row["phone"]
		elif row["email"]:
			mailto = mailto_href(row["email"])
			reach = (mailto, row["email"]) if mailto else row["email"]
		else:
			reach = None
		out.append([row["role"], name, reach])
	return out


def contact_links(contacts):
	"""The email's links under the Contacts table: each hotel's nearest urgent care and
	directions, and directions to the job site. ``[(url, label)]`` for ``ee.links``."""
	links = []
	for row in contact_list(contacts):
		for url, label in row["links"]:
			links.append(
				(url, f"{label}: {row['name']}" if row["role"] == "Hotel" else f"{label} to the job site")
			)
	return links


# --------------------------------------------------------------------------- the trip sheet


#: What a booking's number is called on the sheet (``itinerary_text.MISSING``'s keys), and
#: which field of an itinerary item holds it.
_REF_KIND = {
	"flight": "PNR",
	"hotel_checkin": "Confirmation",
	"ground": "Confirmation",
	"freight": "Tracking",
}
_REF_FIELD = {
	"flight": "booking_reference",
	"hotel_checkin": "booking_confirmation",
	"ground": "booking_reference",
	"freight": "tracking_number",
}


def _money_text(total, currency):
	return f"{currency} {total:,.2f}".strip()


def build_trip_sheet(
	doc,
	shape,
	is_coordinator,
	contacts=None,
	employee=None,
	currency=None,
	format_money=None,
	itinerary_url=None,
	printed_on=None,
	job_title=None,
):
	"""The Trip Sheet print format's payload: the whole trip, or one person's, on one or two
	Letter pages — for the job folder, or a crew lead who will not open the app.

	Args:
		doc: the Travel Trip.
		shape: ``api.travel.shape_itinerary``.
		is_coordinator: whether the person printing may see money.
		contacts: ``api.travel._trip_contacts`` for the same view (one person's hotels only
			on their sheet).
		employee: print this crew member's sheet (their own bookings and numbers, as their
			``/itinerary`` shows them). Anyone not on the crew prints the whole trip.
		currency, format_money: the coordinator's total, and how to write it
			(``frappe.utils.fmt_money``; ``"USD 1,500.00"`` without one).
		itinerary_url, printed_on: the footer's "the latest is here" link and print date.
		job_title: what the trip is for, by name (``api.travel._site_title``), for the JOB
			line; the contacts card's site label when not given. Separate from the card, which
			has no site when there is nobody to call, so the job keeps its name either way.

	All but money, and the sheet leaves the building: **money is only on a coordinator's
	whole-trip sheet**, as one total. A person's sheet is printed to be handed to that person,
	so even a coordinator's copy of it carries no figure. Everything else is anyone's who can
	read the trip: the crew, every day, every confirmation number (the whole trip's sheet
	lists each person's own), the files by title and kind (a printed link to a private file
	is no use on paper).
	"""
	from erpnext_enhancements.travel_management.completeness import UNBOOKED_TRANSPORT
	from erpnext_enhancements.travel_management.itinerary_text import MISSING, pretty_date, span

	people = crew(doc)
	person = next((p for p in people if employee and p["employee"] == employee), None)
	viewing = person["employee"] if person else None
	shaped = shape(doc, viewing)
	whole = viewing is None
	# A hotel's street address, as the contacts card resolved it: a room's own `address` is
	# the Address record's name ("Harborview Suites-Billing"), not something to print.
	hotel_addresses = {h.get("name"): h.get("address") for h in (contacts or {}).get("hotels") or []}

	def date_text(first, last):
		first, last = pretty_date(first), pretty_date(last)
		return f"{first} – {last}" if first and last and first != last else first or last

	def refs_of(item):
		"""``(refs, note)``: each number on the booking and whose it is, and what is missing —
		the same rule as the checklist's (every row of a booking needs its own, a company
		truck or a personal car none)."""
		kind = item.get("type")
		ref_kind = _REF_KIND.get(kind)
		if not ref_kind:
			return [], ""
		needed = not (kind == "ground" and item.get("transport_type") in UNBOOKED_TRANSPORT)
		if whole and kind != "freight":
			members = item.get("members") or []
			numbers = [m.get("ref") for m in members if m.get("ref")]
			if numbers and len(set(numbers)) == 1 and len(numbers) == len(members):
				refs = [{"name": "", "ref": numbers[0]}]  # one number for everyone on it
			else:
				refs = [
					{"name": m.get("employee_name") or "", "ref": m["ref"]} for m in members if m.get("ref")
				]
			missing = [m.get("employee_name") or "" for m in members if not m.get("ref")]
		else:
			value = item.get(_REF_FIELD[kind])
			refs = [{"name": "", "ref": value}] if value else []
			missing = [] if value else [""]
		note = ""
		if needed and missing:
			note = MISSING[ref_kind]
			named = [name for name in missing if name]
			if refs and named:
				note += f" for {', '.join(named)}"
		return refs, note

	def who_of(item):
		if not whole:
			return f"received by {item['received_by']}" if item.get("received_by") else ""
		if item.get("type") == "freight":
			return item.get("received_by") or "Everyone"
		if item.get("type") == "agenda" or item.get("whole_crew") or not item.get("travelers"):
			return "Everyone"
		return ", ".join(item["travelers"])

	def row_of(item):
		kind = item.get("type")
		if kind == "flight":
			lands = span(None, item.get("arrival_time"))
			time = span(item.get("departure_time"), None)
			what = " ".join(str(x) for x in (item.get("airline"), item.get("flight_number")) if x) or "Flight"
			detail = f"{item.get('departure_airport') or '?'} → {item.get('arrival_airport') or '?'}"
			detail += f", lands {lands}" if lands else ""
			what = f"Flight: {what}"
		elif kind == "hotel_checkin":
			time = span(item.get("time"), None)
			what, detail = (
				f"Check in: {item.get('hotel') or ''}",
				hotel_addresses.get(item.get("hotel")) or "",
			)
		elif kind == "hotel_checkout":
			time = span(item.get("time"), None)
			what, detail = f"Check out: {item.get('hotel') or ''}", ""
		elif kind == "ground":
			time = span(item.get("pickup_datetime"), item.get("arrival_datetime"))
			what = item.get("provider") or item.get("transport_type") or "Ground transport"
			detail = f"{item.get('pickup_location') or '?'} → {item.get('dropoff_location') or '?'}"
			if item.get("cargo"):
				detail += f", hauling {item['cargo']}"
		elif kind == "freight":
			time = span(item.get("delivery_from"), item.get("delivery_to"))
			what = f"Freight: {item.get('carrier') or ''}".strip()
			detail = ", ".join(
				part
				for part in (
					item.get("contents"),
					f"to {item['deliver_to']}" if item.get("deliver_to") else "",
				)
				if part
			)
		elif kind == "agenda":
			time = span(item.get("time"), item.get("end_time"))
			what = item.get("activity") or ""
			detail = (item.get("poi") or {}).get("poi_name") or ""
		else:
			return None
		refs, note = refs_of(item)
		return {
			"time": time or "",
			"what": what,
			"detail": detail or "",
			"who": who_of(item),
			"refs": refs,
			"ref_kind": _REF_KIND.get(kind) or "",
			"ref_note": note,
		}

	start = person["from_date"] if person else _iso(_get(doc, "start_date"))
	end = person["to_date"] if person else _iso(_get(doc, "end_date"))
	by_date = {day["date"]: day["items"] for day in shaped.get("days") or []}
	trip_start = to_date(_iso(_get(doc, "start_date")))
	days = []
	for date in trip_days(start, end, list(by_date)):
		number = (to_date(date) - trip_start).days + 1 if trip_start else 0
		rows = [row for row in (row_of(item) for item in by_date.get(date) or []) if row]
		days.append(
			{
				"date": date,
				"label": (f"Day {number} · " if number >= 1 else "") + pretty_date(date),
				"rows": rows,
			}
		)

	lead = next((p for p in people if p["is_trip_lead"]), None)
	title = str(job_title or ((contacts or {}).get("site") or {}).get("label") or "").strip()
	job = " ".join(str(x) for x in (_get(doc, "travel_for_doctype"), _get(doc, "travel_for_name")) if x)
	if title and title != _get(doc, "travel_for_name"):
		job = f"{title} ({_get(doc, 'travel_for_name')})"

	money = None
	if is_coordinator and whole:
		total = build_money(doc, currency)["total"]
		money = {
			"currency": currency or "",
			"total": total,
			"total_text": (format_money or _money_text)(total, currency or ""),
		}

	return {
		"trip": shaped.get("trip"),
		"purpose": shaped.get("purpose") or "",
		"status": shaped.get("status") or "",
		"travel_type": shaped.get("travel_type") or "",
		"eyebrow": "TRIP SHEET" + (f" · {person['employee_name'].upper()}" if person else ""),
		"for_employee": viewing,
		"for_name": person["employee_name"] if person else None,
		"dates_text": date_text(start, end),
		"job": job or "",
		"lead_name": lead["employee_name"] if lead else None,
		"crew": [
			{
				"name": p["employee_name"],
				"dates": date_text(p["from_date"], p["to_date"]),
				"is_trip_lead": bool(p["is_trip_lead"]),
				"is_you": p["employee"] == viewing,
			}
			for p in people
		],
		"contacts": contact_list(contacts),
		"days": days,
		"documents": [
			{
				"title": d.get("title") or d.get("file_name") or "",
				"kind": d.get("kind") or "",
				"for_name": d.get("for_name") or "",
				"booking_label": d.get("booking_label") or "",
			}
			for d in shaped.get("documents") or []
		],
		"itinerary_url": itinerary_url or itinerary_path(shaped.get("trip")),
		"printed_on": printed_on or "",
		# Coordinators only, on the whole trip's sheet only: one total, as the Review step's
		# "Booked so far". A crew member's sheet never holds a figure.
		"money": money,
	}
