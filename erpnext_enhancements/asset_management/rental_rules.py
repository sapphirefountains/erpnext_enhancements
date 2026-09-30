# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The decisions behind event-rental bookings. **No Frappe, no I/O.**

``rental_availability.py`` and the ``Rental Booking`` controller do the reads and writes;
every judgement about *which window* a fountain is blocked for, *how many* of an
accessory pool are already out, *which* status may follow which, and *what* has to change
on the per-fountain calendar is made here, so it runs bench-free in CI. There is no Frappe
integration-test job, so bench-free code is the only code that runs on every push.

Nothing here raises. Functions return a value or a list of sentences in the user's terms,
and the caller decides what a refusal means.

**The per-fountain calendar is Asset Booking.** A Rental Booking does not keep a second
calendar of its own: each fountain on it becomes up to three ``Asset Booking`` legs —
``Prep`` (before delivery), ``Rental`` (delivery through take-down) and ``Turnaround``
(cleaning after take-down). Asset Booking already refuses an overlap on the same asset for
*any* booking type, so a rental cannot collide with a travel or maintenance booking made
by hand, and the hourly status job and the rental inspections keep working unchanged.
"""

import datetime

#: Every status a Rental Booking can hold, in lifecycle order. Mirrors the Select options
#: in ``rental_booking.json``; a test pins the pair.
STATUSES = ("Tentative", "Confirmed", "Out", "Returned", "Closed", "Expired", "Canceled")

#: Statuses whose fountains and accessories are still held on the calendar. ``Closed`` is
#: here on purpose: a closed rental's legs are its history, and the window is normally in
#: the past anyway. Only a booking that never happened lets go of its dates.
HOLDING_STATUSES = ("Tentative", "Confirmed", "Out", "Returned", "Closed")

#: Statuses that let go of the dates.
RELEASED_STATUSES = ("Expired", "Canceled")

#: Statuses whose Asset Booking legs are *submitted*. A Tentative hold is a draft leg —
#: drafts block the calendar too (``docstatus < 2``), so a hold is a real hold; submitting
#: is what "firm" means, and it is what the inspection buttons on a leg look for.
FIRM_STATUSES = ("Confirmed", "Out", "Returned", "Closed")

#: Statuses where the schedule and the lines can no longer change.
FROZEN_STATUSES = ("Closed", "Expired", "Canceled")

#: Which status may follow which. ``Expired -> Tentative`` is "renew the hold", and it
#: re-checks availability like any other save.
TRANSITIONS = {
	"Tentative": ("Confirmed", "Expired", "Canceled"),
	"Confirmed": ("Out", "Canceled"),
	"Out": ("Returned",),
	"Returned": ("Closed",),
	"Closed": (),
	"Expired": ("Tentative", "Canceled"),
	"Canceled": (),
}

#: A new booking may start as a hold or, for a deal already agreed, as firm.
INITIAL_STATUSES = ("Tentative", "Confirmed")

#: Days a tentative hold lasts before it is due to expire.
DEFAULT_HOLD_DAYS = 7

#: Hours a fountain is blocked after take-down for cleaning, when the Asset says nothing.
DEFAULT_TURNAROUND_HOURS = 24

#: The three kinds of Asset Booking leg a fountain line produces, and the Asset Booking
#: ``booking_type`` each one is filed under. Prep and Turnaround are "Maintenance" so the
#: Asset's rental status reads Maintenance while the fountain is being cleaned.
LEG_PREP = "Prep"
LEG_RENTAL = "Rental"
LEG_TURNAROUND = "Turnaround"
LEG_BOOKING_TYPE = {LEG_PREP: "Maintenance", LEG_RENTAL: "Rental", LEG_TURNAROUND: "Maintenance"}


def can_transition(old, new):
	"""True when a booking may move from status ``old`` to ``new``.

	``old`` is None for a new booking. Staying put is always allowed.
	"""
	if old is None:
		return new in INITIAL_STATUSES
	if old == new:
		return True
	return new in TRANSITIONS.get(old, ())


def whole(value, default=0):
	"""A whole, non-negative number (hours, or a quantity), or ``default`` when ``value`` is blank.

	``0`` is a real answer ("no buffer") and is kept; only None and "" fall back.
	"""
	if value is None or value == "":
		return default
	try:
		number = int(float(value))
	except (TypeError, ValueError):
		return default
	return max(number, 0)


def schedule_problems(delivery, takedown, setup=None, event_start=None, event_end=None):
	"""Sentences describing what is wrong with a rental's schedule; empty when it is fine.

	Delivery and take-down bound the rental. Setup and the event are optional, but when
	they are given they have to fall inside that window and in a sensible order.
	"""
	problems = []
	if not delivery:
		problems.append("Delivery date and time is required.")
	if not takedown:
		problems.append("Take-down date and time is required.")
	if problems:
		return problems
	if takedown <= delivery:
		problems.append("Take-down has to be after delivery.")
		return problems
	for label, value in (("Setup", setup), ("Event start", event_start), ("Event end", event_end)):
		if value and not (delivery <= value <= takedown):
			problems.append(f"{label} has to fall between delivery and take-down.")
	if setup and event_start and event_start < setup:
		problems.append("The event cannot start before setup.")
	if event_start and event_end and event_end < event_start:
		problems.append("The event cannot end before it starts.")
	return problems


def leg_windows(delivery, takedown, prep_hours=0, turnaround_hours=0):
	"""The Asset Booking legs one fountain needs, as ``[(leg, from, to), ...]``.

	The Rental leg is delivery through take-down. Prep and Turnaround are added only when
	the fountain has a non-zero buffer, so a fountain with none produces a single leg.
	Legs touch end to start and never overlap each other (Asset Booking's overlap test is
	strict), so one rental's legs can never collide among themselves.
	"""
	legs = []
	prep = whole(prep_hours)
	turnaround = whole(turnaround_hours)
	if prep:
		legs.append((LEG_PREP, delivery - datetime.timedelta(hours=prep), delivery))
	legs.append((LEG_RENTAL, delivery, takedown))
	if turnaround:
		legs.append((LEG_TURNAROUND, takedown, takedown + datetime.timedelta(hours=turnaround)))
	return legs


def block_span(legs):
	"""``(first from, last to)`` across a fountain's legs."""
	return min(leg[1] for leg in legs), max(leg[2] for leg in legs)


def pool_window(delivery, takedown, turnaround_hours=0):
	"""The window an accessory is out for: delivery through take-down plus the pool's turnaround."""
	return delivery, takedown + datetime.timedelta(hours=whole(turnaround_hours))


def overlaps(a_from, a_to, b_from, b_to):
	"""Strict interval overlap; windows that only touch do not overlap.

	``b_to`` may be None, meaning open-ended (an out-of-service fountain with no end).
	"""
	if b_to is None:
		return b_from < a_to
	return a_from < b_to and b_from < a_to


def peak_usage(rows, window_from, window_to):
	"""The most units of a pool out at any one moment inside ``[window_from, window_to)``.

	``rows`` is ``[(from, to, qty), ...]`` — other bookings' lines for the same pool. Summing
	every overlapping row would be wrong in the unsafe-looking-safe direction: two bookings
	on Friday and Sunday both touch a Friday-to-Sunday request but never coexist, and adding
	them up would refuse a booking that fits. So this sweeps the start and end points and
	reports the true peak.
	"""
	events = []
	for start, end, qty in rows:
		if not overlaps(window_from, window_to, start, end):
			continue
		start = max(start, window_from)
		end = min(end, window_to)
		events.append((start, 1, qty))
		events.append((end, 0, qty))
	# At the same instant, ends (0) sort before starts (1): a unit returned at noon can go
	# out again at noon, matching the touching-is-not-overlapping rule above.
	events.sort(key=lambda e: (e[0], e[1]))
	current = peak = 0
	for _when, is_start, qty in events:
		current += qty if is_start else -qty
		peak = max(peak, current)
	return peak


def pool_problem(pool_label, requested, capacity, peak):
	"""A sentence when ``requested`` more units do not fit alongside ``peak`` already out, else None."""
	requested = whole(requested)
	available = max(capacity - peak, 0)
	if requested <= available:
		return None
	return (
		f"Only {available} of {pool_label} are free for those dates "
		f"({capacity} in the pool, up to {peak} already booked); this booking asks for {requested}."
	)


def out_of_service_end(status, returned_on=None, expected_back=None):
	"""Until when an out-of-service record blocks its fountain; None means until further notice.

	Returned to service: until it came back. Still out with an expected-back date: through
	the end of that day, so a fountain in for repair can still be booked for next season.
	Still out with no date: open-ended — nobody can promise it.
	"""
	if status == "Returned to Service":
		return returned_on
	if expected_back:
		if isinstance(expected_back, datetime.datetime):
			return expected_back
		return datetime.datetime.combine(expected_back, datetime.time.max.replace(microsecond=0))
	return None


def default_hold_expiry(today, days=DEFAULT_HOLD_DAYS):
	"""The date a hold placed today lapses on."""
	return today + datetime.timedelta(days=days)


def plan_leg_changes(desired, existing):
	"""What has to happen on the calendar to make it match the booking.

	``desired`` is ``[(asset, leg, from, to), ...]``, what the booking needs now.
	``existing`` is ``[{"name", "asset", "leg", "from", "to"}, ...]``, the live legs it owns.

	Returns ``(create, move, remove)``:

	* ``create`` — ``[(asset, leg, from, to)]`` with no live leg yet;
	* ``move`` — ``[(name, from, to)]`` whose window changed. Moved, not re-created, so a
	  submitted leg keeps its name and every inspection filed against it;
	* ``remove`` — ``[name]`` the booking no longer needs (a fountain taken off, a buffer
	  set to zero, or a duplicate left behind by an earlier failure).
	"""
	by_key = {}
	remove = []
	for row in existing:
		key = (row["asset"], row["leg"])
		if key in by_key:
			remove.append(row["name"])
		else:
			by_key[key] = row

	create, move = [], []
	wanted = set()
	for asset, leg, start, end in desired:
		key = (asset, leg)
		wanted.add(key)
		row = by_key.get(key)
		if row is None:
			create.append((asset, leg, start, end))
		elif row["from"] != start or row["to"] != end:
			move.append((row["name"], start, end))

	for key, row in by_key.items():
		if key not in wanted:
			remove.append(row["name"])
	return create, move, remove


def booking_total(fountain_rates, accessory_lines, fees):
	"""The rental's total: fountain rates, accessory rate × qty, and the flat fees."""
	total = sum(float(rate or 0) for rate in fountain_rates)
	total += sum(float(rate or 0) * whole(qty) for rate, qty in accessory_lines)
	total += sum(float(fee or 0) for fee in fees)
	return round(total, 2)


def covered_hours(windows, window_from, window_to):
	"""Hours inside ``[window_from, window_to)`` covered by any of ``windows`` (``[(from, to), ...]``).

	Overlapping windows count once: two bookings on the same fountain the same afternoon are one
	afternoon of use, not two. Used for fleet utilization, where a double count would report a
	fountain busier than a day has hours.
	"""
	clipped = sorted(
		(max(start, window_from), min(end, window_to))
		for start, end in windows
		if overlaps(window_from, window_to, start, end)
	)
	total = datetime.timedelta(0)
	current_start = current_end = None
	for start, end in clipped:
		if current_end is None or start > current_end:
			if current_end is not None:
				total += current_end - current_start
			current_start, current_end = start, end
		else:
			current_end = max(current_end, end)
	if current_end is not None:
		total += current_end - current_start
	return total.total_seconds() / 3600
