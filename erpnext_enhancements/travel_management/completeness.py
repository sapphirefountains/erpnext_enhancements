"""The trip checklist: what a Travel Trip is still missing before the crew leaves.

Four checks, chosen by the office (2026-09-23) as what "a complete trip" means:

* **A bed every night** — every traveler has somewhere to sleep on every night between
  their own from/to dates. A room shared by two people counts for both, because Plan a
  Trip stores one Trip Accommodation row *per occupant* (see ``planner.py``).
* **Travel both ways** — every traveler has a way there and a way back: a flight or a
  ground-transport row pinned to them (or to the whole crew) on the Outbound and the
  Return leg.
* **Confirmation numbers** — every flight, room and booked vehicle carries its PNR /
  confirmation, and every freight shipment its tracking / PRO / BOL number. Company Fleet
  and Personal Vehicle rows are exempt: nothing was booked.
* **Cost and who paid** — the same bookings carry a cost. "Who paid" is already enforced
  by the Travel Trip controller (``paid_by`` is required, and an employee-paid row must
  name the employee), so a missing cost is the only thing left to flag.

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


def _step(table, rows, trip):
	if table == "accommodations":
		return "lodging"
	if table == "freight":
		return "freight"
	start, end = to_date(_get(trip, "start_date")), to_date(_get(trip, "end_date"))
	return STEP_FOR_LEG.get(leg_of(table, rows[0], start, end), "there")


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
		start, end = traveler_window(trip, traveler)
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


def confirmation_gaps(trip):
	"""Bookings where someone on them has no confirmation number."""
	gaps = []
	names = _names(trip)
	for table, field in (
		("flights", "booking_reference"),
		("accommodations", "booking_confirmation"),
		("ground_transport", "booking_reference"),
		("freight", "tracking_number"),
	):
		for key, rows in _bookings(trip, table).items():
			if not _booked(table, rows):
				continue
			without = [row for row in rows if not (_get(row, field) or "").strip()]
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


def cost_gaps(trip):
	"""Bookings with no cost entered at all."""
	gaps = []
	for table in ("flights", "accommodations", "ground_transport", "freight"):
		for key, rows in _bookings(trip, table).items():
			if not _booked(table, rows):
				continue
			if any(float(_get(row, "cost") or 0) for row in rows):
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


def find_gaps(trip):
	"""Every gap, in the order the page's steps run."""
	return travel_gaps(trip) + lodging_gaps(trip) + confirmation_gaps(trip) + cost_gaps(trip)
