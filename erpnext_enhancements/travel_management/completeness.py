"""The trip checklist: what a Travel Trip is still missing before the crew leaves.

Four checks, chosen by the office (2026-09-23) as what "a complete trip" means, and a fifth
added with trip files (2026-09-26):

* **A bed every night** — every traveler has somewhere to sleep on every night they are
  away: their own from/to dates, narrowed by their own travel (:func:`stay_window`), so
  someone who flies out and back on one day needs no bed whatever dates they are down for.
  A room shared by two people counts for both, because Plan a Trip stores one Trip
  Accommodation row *per occupant* (see ``planner.py``) — a guest staying free in someone
  else's room included.
* **Travel both ways** — every traveler has a way there and a way back: a flight or a
  ground-transport row pinned to them (or to the whole crew) on the Outbound and the
  Return leg.
* **Confirmation numbers** — every flight, room and booked vehicle carries its PNR /
  confirmation, and every freight shipment its tracking / PRO / BOL number. Company Fleet
  and Personal Vehicle rows are exempt: nothing was booked.
* **Cost and who paid** — the same bookings carry a cost. "Who paid" is already enforced
  by the Travel Trip controller (``paid_by`` is required, and an employee-paid row must
  name the employee), so a missing cost is the only thing left to flag. A round-trip
  ticket is ONE charge, so the flight home carries no cost of its own: a flight with no
  cost is not flagged when its confirmation number is on a flight that has one
  (:func:`on_another_ticket`).
* **Paperwork** — the booking is made, so its paperwork should be here: each person's
  boarding pass (or the ticket confirmation), the hotel's confirmation, the rental
  agreement, the bill of lading, as a Trip Document on that booking
  (:func:`document_gaps`). Only a booking that already has its confirmation or tracking
  number is asked — one without is already flagged above, and one flag is enough.

These are flags, not blockers. The office asked for the trip to *show* what is missing;
nothing here stops a save or a status change.

The functions return data, never sentences. The page (``plan_a_trip.js``) and the Travel
Trip form both word the result in the browser, so the wording lives in one language layer
and this module stays importable without a site — ``tests/test_travel_planner.py`` runs it
bench-free.

Rows are read with :func:`_get`, which accepts a Frappe ``Document``, a plain ``dict`` or a
``SimpleNamespace`` alike.
"""

from datetime import date, datetime, timedelta

#: Ground transport that nobody books or buys, so it has no confirmation number or cost to
#: chase. It still counts as a way there / a way back.
UNBOOKED_TRANSPORT = frozenset({"Company Fleet", "Personal Vehicle"})

OUTBOUND = "Outbound"
RETURN = "Return"
DURING = "During Trip"

#: Which page step fixes a gap, keyed by leg. Blank legs are inferred from dates.
STEP_FOR_LEG = {OUTBOUND: "there", RETURN: "back", DURING: "around"}

#: The tables whose rows are bookings, and each one's confirmation field: the PNR, the
#: hotel's confirmation, the rental's reference, the shipment's tracking / PRO / BOL number.
REF_FIELDS = (
	("flights", "booking_reference"),
	("accommodations", "booking_confirmation"),
	("ground_transport", "booking_reference"),
	("freight", "tracking_number"),
)

#: Trip Document ``kind`` — exactly the Select options in trip_document.json.
DOCUMENT_KINDS = (
	"Boarding pass",
	"Booking confirmation",
	"Rental agreement",
	"Bill of lading",
	"Site map",
	"Safety plan",
	"Insurance certificate",
	"Job packet",
	"Other",
)

#: What counts as a booking's paperwork, per table (:func:`document_gaps`).
PAPERWORK = {
	"flights": ("Boarding pass", "Booking confirmation"),
	"accommodations": ("Booking confirmation",),
	"ground_transport": ("Rental agreement", "Booking confirmation"),
	"freight": ("Bill of lading", "Booking confirmation"),
}

#: The only ground transport that comes with paperwork: our own truck, a personal car and a
#: taxi or rideshare come with nothing to file.
PAPERWORK_TRANSPORT = frozenset({"Rental/Third Party"})

#: File extensions ``/itinerary`` opens inside the page rather than handing to the phone.
IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "heic"})


def _get(row, field):
	if row is None:
		return None
	if isinstance(row, dict):
		return row.get(field)
	return getattr(row, field, None)


def to_date(value):
	"""A ``date`` from a date, a datetime, or an ISO string; ``None`` when blank."""
	if not value:
		return None
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	text = str(value).strip()
	if not text:
		return None
	return date.fromisoformat(text[:10])


def group_key(row):
	"""The id that ties the rows of one booking together.

	Rows written by Plan a Trip carry a ``booking_group``. A row added on the form does not,
	and stands alone as its own booking.
	"""
	return _get(row, "booking_group") or f"row:{_get(row, 'name')}"


def visible_to(row, employee):
	"""True when ``row`` belongs to ``employee``: pinned to them, or to nobody (whole crew).

	Same rule as ``api/travel.py`` ``shape_itinerary`` and ``ics.trip_events_for_traveler``,
	so the checklist and each traveler's itinerary always agree on whose booking it is.
	"""
	traveler = _get(row, "traveler")
	return not traveler or traveler == employee


def traveler_window(trip, traveler):
	"""(from, to) for one traveler, falling back to the trip dates."""
	start = to_date(_get(traveler, "from_date")) or to_date(_get(trip, "start_date"))
	end = to_date(_get(traveler, "to_date")) or to_date(_get(trip, "end_date"))
	return start, end


def segment_date(table, row):
	if table == "flights":
		return to_date(_get(row, "departure_time"))
	return to_date(_get(row, "pickup_datetime"))


def leg_of(table, row, start, end):
	"""The row's leg, inferring a blank one from its date against ``start``/``end``.

	Rows from Plan a Trip always carry a leg. The inference covers rows typed on the form,
	where the field is optional: on or before the first day is the way there, on or after the
	last day is the way back, anything between is getting around.
	"""
	leg = _get(row, "leg")
	if leg:
		return leg
	when = segment_date(table, row)
	if when is None or start is None or end is None:
		return None
	if when <= start:
		return OUTBOUND
	if when >= end:
		return RETURN
	return DURING


def stay_window(trip, traveler):
	"""(first night, the morning they leave) — the nights one traveler needs a bed.

	Their own dates (:func:`traveler_window`), narrowed by their own travel: no bed before the
	day their way there leaves, and none from the day their way home leaves. Travel only ever
	narrows the window, never widens it.

	The first real trip is why: one of the crew flew out at 7:20 AM and home at 3:45 PM the
	same day, but was down for the whole trip, and the checklist asked for five nights of
	hotel. Dates are one tick box on the crew step; the flights are what actually got booked.

	``plan_a_trip.js`` ``stay_window`` is the same rule for the page's nights grid.
	"""
	start, end = traveler_window(trip, traveler)
	employee = _get(traveler, "employee")
	first = {}
	for table in ("flights", "ground_transport"):
		for row in _get(trip, table) or []:
			if not visible_to(row, employee):
				continue
			when = segment_date(table, row)
			leg = leg_of(table, row, start, end)
			if when is None or leg not in (OUTBOUND, RETURN):
				continue
			if leg not in first or when < first[leg]:
				first[leg] = when
	if OUTBOUND in first:
		start = max(start, first[OUTBOUND]) if start else first[OUTBOUND]
	if RETURN in first:
		end = min(end, first[RETURN]) if end else first[RETURN]
	return start, end


def _nights(start, end):
	if start is None or end is None or end <= start:
		return []
	return [start + timedelta(days=i) for i in range((end - start).days)]


def _travelers(trip):
	return [t for t in (_get(trip, "travelers") or []) if _get(t, "employee")]


def _names(trip):
	return {_get(t, "employee"): _get(t, "employee_name") or _get(t, "employee") for t in _travelers(trip)}


def booking_label(table, row):
	if table == "flights":
		return " ".join(str(x) for x in (_get(row, "airline"), _get(row, "flight_number")) if x)
	if table == "accommodations":
		return _get(row, "hotel_lodging") or ""
	if table == "freight":
		return " ".join(str(x) for x in (_get(row, "carrier"), _get(row, "tracking_number")) if x)
	return _get(row, "supplier") or _get(row, "vehicle") or _get(row, "transport_type") or ""


def _bookings(trip, table):
	"""Rows of ``table`` grouped by booking, in first-seen order."""
	groups = {}
	for row in _get(trip, table) or []:
		groups.setdefault(group_key(row), []).append(row)
	return groups


def booking_index(trip):
	"""{group key: (table, rows)} for every booking and shipment on the trip — what a Trip
	Document's ``booking_group`` points at."""
	index = {}
	for table, _field in REF_FIELDS:
		for key, rows in _bookings(trip, table).items():
			index.setdefault(key, (table, rows))
	return index


def document_group(document, index):
	"""The booking a Trip Document belongs to, or None for a file for the whole trip.

	A file whose booking is gone (deleted on the form, or on the page) reads as a file for the
	whole trip: it is still on the trip, and hiding it would lose it."""
	group = _get(document, "booking_group") or None
	return group if group in index else None


def document_visible_to(document, employee, index):
	"""True when one person sees this file on their own itinerary: it is for them, or it is
	for nobody in particular and either belongs to the whole trip or to a booking they are on
	(any of its rows :func:`visible_to` them)."""
	traveler = _get(document, "traveler")
	if traveler:
		return traveler == employee
	group = document_group(document, index)
	if group is None:
		return True
	return any(visible_to(row, employee) for row in index[group][1])


def file_name_of(url):
	"""The last path segment of a file URL: ``/private/files/pass.png`` -> ``pass.png``."""
	text = str(url or "").split("?", 1)[0].split("#", 1)[0].rstrip("/")
	return text.rsplit("/", 1)[-1]


def is_image_file(url):
	name = file_name_of(url)
	return "." in name and name.rsplit(".", 1)[-1].lower() in IMAGE_EXTENSIONS


#: Ground transport that is hired for getting around, not for getting there: an undated
#: one is placed under "Getting around" rather than "Getting there".
HIRED_TRANSPORT = frozenset({"Rental/Third Party", "Taxi/Rideshare"})


def booking_leg(table, rows, trip):
	"""The leg one booking belongs to — its own, or the one its people's dates imply.

	The page places a leg-less booking with this same rule (``infer_legs`` in
	``plan_a_trip.js``) and writes it on the next save, so it must agree with the
	per-traveler reading in :func:`travel_gaps`. Using the TRIP's dates instead got this
	wrong on the first real trip: a whole-crew flight on day two, which is how two of the
	four got there, read as "getting around" — and saving would have stored that, leaving
	both with no way there.

	Outbound if the date is on or before ANY of its people's first day, Return if on or
	after any last day, otherwise During Trip. No date: a rental or taxi is getting around,
	anything else is taken as the way there.
	"""
	leg = _get(rows[0], "leg")
	if leg:
		return leg
	when = segment_date(table, rows[0])
	if when is None:
		if table == "ground_transport" and _get(rows[0], "transport_type") in HIRED_TRANSPORT:
			return DURING
		return OUTBOUND
	travelers = _travelers(trip)
	named = {_get(row, "traveler") for row in rows if _get(row, "traveler")}
	whole_crew = any(not _get(row, "traveler") for row in rows)
	pool = [t for t in travelers if whole_crew or _get(t, "employee") in named] or travelers
	windows = [traveler_window(trip, t) for t in pool] or [
		(to_date(_get(trip, "start_date")), to_date(_get(trip, "end_date")))
	]
	if any(start and when <= start for start, _end in windows):
		return OUTBOUND
	if any(end and when >= end for _start, end in windows):
		return RETURN
	return DURING


def _step(table, rows, trip):
	if table == "accommodations":
		return "lodging"
	if table == "freight":
		return "freight"
	return STEP_FOR_LEG.get(booking_leg(table, rows, trip), "there")


# --------------------------------------------------------------------------- checks


def lodging_gaps(trip):
	"""Nights with no bed, per traveler, plus rooms whose dates are not filled in.

	A room without both dates cannot cover a night, so it is flagged on its own rather than
	silently counting for nothing.
	"""
	gaps = []
	stays = _get(trip, "accommodations") or []

	for key, rows in _bookings(trip, "accommodations").items():
		if not (to_date(_get(rows[0], "check_in_date")) and to_date(_get(rows[0], "check_out_date"))):
			gaps.append(
				{
					"check": "lodging",
					"kind": "dates",
					"step": "lodging",
					"group": key,
					"label": booking_label("accommodations", rows[0]),
				}
			)

	for traveler in _travelers(trip):
		employee = _get(traveler, "employee")
		start, end = stay_window(trip, traveler)
		missing = []
		for night in _nights(start, end):
			covered = False
			for stay in stays:
				if not visible_to(stay, employee):
					continue
				check_in = to_date(_get(stay, "check_in_date"))
				check_out = to_date(_get(stay, "check_out_date"))
				if check_in and check_out and check_in <= night < check_out:
					covered = True
					break
			if not covered:
				missing.append(night.isoformat())
		if missing:
			gaps.append(
				{
					"check": "lodging",
					"kind": "nights",
					"step": "lodging",
					"employee": employee,
					"employee_name": _get(traveler, "employee_name") or employee,
					"nights": missing,
				}
			)
	return gaps


def travel_gaps(trip):
	"""Travelers with no way there, or no way back."""
	gaps = []
	segments = [("flights", row) for row in _get(trip, "flights") or []] + [
		("ground_transport", row) for row in _get(trip, "ground_transport") or []
	]
	for traveler in _travelers(trip):
		employee = _get(traveler, "employee")
		start, end = traveler_window(trip, traveler)
		legs = {
			leg_of(table, row, start, end) for table, row in segments if visible_to(row, employee)
		}
		for leg, step in ((OUTBOUND, "there"), (RETURN, "back")):
			if leg not in legs:
				gaps.append(
					{
						"check": "travel",
						"kind": leg,
						"step": step,
						"employee": employee,
						"employee_name": _get(traveler, "employee_name") or employee,
					}
				)
	return gaps


def _booked(table, rows):
	return not (table == "ground_transport" and _get(rows[0], "transport_type") in UNBOOKED_TRANSPORT)


def _has_ref(row, field):
	return bool(str(_get(row, field) or "").strip())


def confirmation_gaps(trip):
	"""Bookings where someone on them has no confirmation number."""
	gaps = []
	names = _names(trip)
	for table, field in REF_FIELDS:
		for key, rows in _bookings(trip, table).items():
			if not _booked(table, rows):
				continue
			without = [row for row in rows if not _has_ref(row, field)]
			if not without:
				continue
			gaps.append(
				{
					"check": "confirmation",
					"step": _step(table, rows, trip),
					"table": table,
					"group": key,
					"label": booking_label(table, rows[0]),
					# A freight row's traveler is who receives it, not whose number it is.
					"employee_names": []
					if table == "freight"
					else [
						names.get(_get(row, "traveler"), _get(row, "traveler"))
						for row in without
						if _get(row, "traveler")
					],
				}
			)
	return gaps


def _pnr(row):
	return (_get(row, "booking_reference") or "").strip().upper()


def _has_cost(rows):
	return any(float(_get(row, "cost") or 0) for row in rows)


def fares(trip):
	"""{PNR: keys of the flight bookings that carry a cost under it}."""
	out = {}
	for key, rows in _bookings(trip, "flights").items():
		if _has_cost(rows):
			for row in rows:
				if _pnr(row):
					out.setdefault(_pnr(row), set()).add(key)
	return out


def on_another_ticket(key, rows, fare_map):
	"""True when a flight booking with no cost of its own rides on another booking's fare.

	A round-trip ticket is one charge — it shows once on the company's invoicing — so the way
	home has no cost to enter, and splitting the fare across the two flights would give an
	Expense Claim two lines for a charge that exists once. The fare goes on one flight; every
	other flight on the same ticket shares its confirmation number (PNR). So: every person on
	this booking has a PNR, and each PNR is on a *different* booking that has a cost. A fare
	upgrade on the way home is a real extra charge, entered on that flight as its own cost,
	and then there is nothing to decide here.

	``plan_a_trip.js`` ``fare_card`` is the same rule for the note on the page's flight card.
	"""
	refs = [_pnr(row) for row in rows]
	return bool(refs) and all(ref and fare_map.get(ref, set()) - {key} for ref in refs)


def cost_gaps(trip):
	"""Bookings with no cost entered at all, except the way home on a round-trip ticket."""
	gaps = []
	fare_map = fares(trip)
	for table in ("flights", "accommodations", "ground_transport", "freight"):
		for key, rows in _bookings(trip, table).items():
			if not _booked(table, rows):
				continue
			if _has_cost(rows):
				continue
			if table == "flights" and on_another_ticket(key, rows, fare_map):
				continue
			gaps.append(
				{
					"check": "cost",
					"step": _step(table, rows, trip),
					"table": table,
					"group": key,
					"label": booking_label(table, rows[0]),
				}
			)
	return gaps


def _papers_by_group(trip):
	"""{booking group: [Trip Document]} — the trip's files that belong to a booking."""
	out = {}
	for document in _get(trip, "documents") or []:
		group = _get(document, "booking_group")
		if group and str(_get(document, "file") or "").strip():
			out.setdefault(group, []).append(document)
	return out


def document_gaps(trip):
	"""Bookings that are made but whose paperwork is not on the trip.

	The rule is "the booking is made, so its paperwork should be here". So only a booking
	that already has its confirmation number is asked — a shipment, its tracking
	number — and one without gets no second flag: :func:`confirmation_gaps` has already
	flagged it. Flights are per person, because a boarding pass is: each person with their
	PNR needs a Boarding pass or Booking confirmation that is theirs or is for everyone on
	the flight, and ``employee_names`` names who is still missing one. The rest are per
	booking: a room its Booking confirmation, a rental its Rental agreement (or the
	confirmation), a shipment its Bill of lading (or the confirmation). Our own truck, a
	personal car and a taxi come with no paperwork. ``kinds`` says which files count.

	Reads the Trip Document rows only (``trip.documents``); never asks whether the File is
	still there.
	"""
	gaps = []
	names = _names(trip)
	crew = [_get(t, "employee") for t in _travelers(trip)]
	papers_by_group = _papers_by_group(trip)
	for table, field in REF_FIELDS:
		kinds = PAPERWORK[table]
		for key, rows in _bookings(trip, table).items():
			if table == "ground_transport" and _get(rows[0], "transport_type") not in PAPERWORK_TRANSPORT:
				continue
			papers = [d for d in papers_by_group.get(key, []) if _get(d, "kind") in kinds]
			numbered = [row for row in rows if _has_ref(row, field)]
			if table == "flights":
				if not numbered or any(not _get(d, "traveler") for d in papers):
					continue
				covered = {_get(d, "traveler") for d in papers}
				missing = []
				for row in numbered:
					# A whole-crew row is everyone's seat.
					for person in [_get(row, "traveler")] if _get(row, "traveler") else crew:
						if person not in covered and person not in missing:
							missing.append(person)
				if not missing:
					continue
				employee_names = [names.get(person, person) for person in missing]
			else:
				if len(numbered) < len(rows) or papers:
					continue
				employee_names = []
			gaps.append(
				{
					"check": "documents",
					"step": _step(table, rows, trip),
					"table": table,
					"group": key,
					"label": booking_label(table, rows[0]),
					"employee_names": employee_names,
					"kinds": list(kinds),
				}
			)
	return gaps


def find_gaps(trip):
	"""Every gap, in the order the page's steps run."""
	return (
		travel_gaps(trip)
		+ lodging_gaps(trip)
		+ confirmation_gaps(trip)
		+ cost_gaps(trip)
		+ document_gaps(trip)
	)
