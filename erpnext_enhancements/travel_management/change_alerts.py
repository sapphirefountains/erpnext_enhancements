# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Change alerts: when a booked trip changes, email only the people it affects, with what it
was and what it is now ("Departs: 7:05 AM → 9:40 AM") and an updated calendar invite.

Nik's call (2026-09-26, PR 4 of the Plan a Trip program; the switch 2026-09-27).

**When.** Only on a trip that was Booked or In Progress before the save and still is after it.
A trip being planned changes all the time and nobody holds a ticket yet; the move into Booked
already sends "Trip booked". Only while **both** Travel Settings switches are on: *Send Travel
Notifications* and *Send Change Alerts*. The second has no default and reads 0 after deploy,
on purpose: notifications were already on in production with a trip in progress, and riding
the first switch would have emailed that crew on the first edit. No backfill patch, because
off is the wanted state.

**What.** For every person on the crew before or after the save, what they see is compared,
before and after: their own itinerary (``api.travel.shape_itinerary`` for that person, one
Travel POI / Address cache for the whole save), item by item, keyed by the booking
(``<kind>:<group>``, the checklist's ``completeness.group_key``) — a flight, a room (its
check-in and check-out as one), a ride, a shipment — plus each schedule stop by its row
(``stop:<row name>``: ``shape_itinerary``'s stops carry no row name, so they are read from
the rows) and the person's own trip dates (``trip:<trip>``). Only a fixed list of fields
per kind is compared (:data:`FIELD_LABELS`): times, dates, airports, flight number, airline,
hotel and address, check-in and check-out, pick-up and drop-off, provider, the person's own
confirmation number, tracking number, delivery and pick-up windows, a stop's activity, times
and place. **Never money** (cost, who paid, receipts, per diem, claims, totals), and never
files or contacts. Values are stored as the display strings the email prints (12-hour times
via ``itinerary_text.clock``, dates via ``pretty_date``), each a function of one state only,
so "changed back" is a plain string comparison.

A person taken off the trip gets "You are no longer on this trip" and a calendar that
cancels everything they had. Someone just added gets "You were added to a trip" from the
existing dispatcher, not this. The person who made the change is not told about it: no new
alert is started for them, but one already waiting for them takes the change in, so it
never reports a value that is no longer true.

**The same booking under a new key is the same booking.** A row entered on the desk form has
no ``booking_group``, so it is keyed by its row (``flight:row:<name>``), and the first save
from Plan a Trip gives it an id (``planner.normalize_group``): same row, new key. Without
help the person would be told "Removed: Flight DL 9" and "Added: Flight DL 9" for a booking
nobody touched. So a row that kept its name and gained a group keeps its key's history
(:func:`_group_aliases`), and a booking that left under one key and came back under another
with every compared value the same — a row replaced by an identical one, as Plan a Trip does
when someone is unticked from a whole-crew booking — is paired too (:func:`_paired_keys`).

**One email per round of edits.** Plan a Trip saves on every step, so each save *merges*
into the person's one Pending **Trip Change Alert** (:func:`merge_changes`): per field the
earliest "before" and the latest "after"; a field changed back drops out; a booking added
and removed again drops out; the row is deleted when nothing is left. The scheduler
(:func:`send_due_change_alerts`, every 5 minutes, hooks.py) sends a row once its last change
is :data:`QUIET_MINUTES` old **and the trip itself has not been saved for as long**: the
quiet period is the trip's, not the person's, so a walk through Plan a Trip that touches
Ann's flight at the start and the schedule twelve minutes later is still one email to her.
Times are ``frappe.utils.now_datetime()`` (site-local) on both sides, and so is the trip's
``modified``, so the comparison is like for like.

**Re-drivable.** The rows are in the database, not a queued job, because the prod deploy
FLUSHDBs the job queue: a deploy in the middle loses nothing, and the next run sends what is
due. The before-state exists only inside the save, which is why detection runs there, in the
save's transaction (a refused save records nothing), and why nothing is enqueued.

**Never a hidden lost save.** Detection never raises into a save — except for a deadlock or a
lock timeout. Those have already rolled back the whole transaction, the trip's own UPDATE
included, so swallowing one would let the request commit what is left and report the trip
saved when it is not. They are raised, and the save fails where it can be retried. The
waiting rows are found with a plain read and each locked by its primary key (a record lock,
no gap lock), then read again under that lock: two saves of two different trips never lock
the same index gap, and a row the sender has just claimed is seen as Sent.

**Stamp first.** A row is marked Sent and committed before its email is built, as
``reminders.py`` does: at most once, even if the run dies half-way. A failure marks it
Failed with the error, logs, and never raises out of the job. Both switches and the trip's
status are checked again at send time; a row that no longer applies is Skipped.

**The calendar.** The email carries the person's whole trip calendar
(``ics.trip_events_for_traveler``) with a ``SEQUENCE`` on every event (``ics.sequence_of``:
the trip's last save, in seconds), so a calendar app replaces the entries it has under the
same UIDs, and each booking that is no longer theirs under its old UID with
``STATUS:CANCELLED`` (the event as it was is kept on the change, ``cancel``).

A calendar entry can go while nothing the person reads changed: a whole-crew booking split
into one row each keeps every value and changes every row, and so every UID. That is still
a change to their calendar (the old entry would sit next to the new one), so it is kept as a
change with no fields and only ``cancel``, and an email with nothing else to say says "Your
calendar was updated." A cancel is only ever for an entry the person had when the round of
edits began (``known_uids``, stored with the row): an entry made and lost inside one round
never reached them, and canceling it would put a "Canceled: …" event in a calendar that
ignores STATUS. What this cannot know is an entry they never received at all — one they
added themselves, or one added while change alerts were off — so removing one of those still
sends its cancel (README, "Known gaps").
"""

import json
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

from erpnext_enhancements.travel_management.completeness import group_key
from erpnext_enhancements.travel_management.ics import (
	build_ics,
	event_uid,
	sequence_of,
	trip_events_for_traveler,
)
from erpnext_enhancements.travel_management.itinerary_text import clock, pretty_date
from erpnext_enhancements.travel_management.notifications import (
	ACTIVE_STATUSES,
	_address_resolver,
	_base_context,
	_deliver,
	_employee_recipient,
	_in_maintenance_context,
	_notifications_enabled,
	_render,
)

DOCTYPE = "Trip Change Alert"

#: Minutes after the last change before a person's alert is sent: one round of edits on Plan
#: a Trip (a save per step) is one email, not one per step.
QUIET_MINUTES = 10

#: The most alerts one run sends; the rest wait five minutes for the next.
SEND_LIMIT = 200

TEMPLATE = "trip_changed.html"

#: The heading of an alert whose only change is a calendar entry replaced (:func:`alert_sections`).
CALENDAR_ONLY = "Your calendar was updated."

#: The order the email lists changes in.
KIND_ORDER = ("trip", "flight", "hotel", "ground", "freight", "stop")

#: Every field a change alert compares, per kind, with the words the email uses for it. Money
#: is not on it and must never be: the alert goes to the crew, and the rule is "all but money".
FIELD_LABELS = {
	"trip": (
		("first_day", "Your first day"),
		("last_day", "Your last day"),
	),
	"flight": (
		("airline", "Airline"),
		("flight_number", "Flight number"),
		("departure_airport", "From"),
		("arrival_airport", "To"),
		("departure_date", "Departure date"),
		("departure_time", "Departs"),
		("arrival_time", "Arrives"),
		("booking_reference", "Confirmation (PNR)"),
	),
	"hotel": (
		("hotel", "Hotel"),
		("address", "Address"),
		("check_in_date", "Check-in"),
		("check_in_time", "Check-in time"),
		("check_out_date", "Check-out"),
		("check_out_time", "Check-out time"),
		("booking_confirmation", "Confirmation number"),
	),
	"ground": (
		("transport_type", "Type"),
		("provider", "Provider"),
		("pickup_location", "Pick-up"),
		("dropoff_location", "Drop-off"),
		("pickup_date", "Pick-up date"),
		("pickup_time", "Pick-up time"),
		("arrival_time", "Arrives"),
		("return_time", "Return by"),
		("booking_reference", "Confirmation number"),
	),
	"freight": (
		("carrier", "Carrier"),
		("tracking_number", "Tracking number"),
		("deliver_to", "Deliver to"),
		("delivery_window", "Delivery window"),
		("pickup_window", "Pick-up window"),
	),
	"stop": (
		("activity", "What"),
		("date", "Date"),
		("time", "Starts"),
		("end_time", "Ends"),
		("place", "Place"),
	),
}

#: The booking tables whose rows make calendar events, and the kind each one is.
_EVENT_TABLES = (
	("flights", "flight"),
	("accommodations", "hotel"),
	("ground_transport", "ground"),
	("freight", "freight"),
)


def change_alerts_enabled():
	"""Both Travel Settings switches: *Send Travel Notifications* and *Send Change Alerts*."""
	return _notifications_enabled() and bool(
		cint(frappe.db.get_single_value("Travel Settings", "change_alerts_enabled"))
	)


# --------------------------------------------------------------- display strings


def _text(value):
	return str(value).strip() if value not in (None, "") else ""


def _day(value):
	return pretty_date(value) if value else ""


def _moment(value):
	"""'Tue Oct 6, 10:00 AM', or the date alone when no time was given (midnight)."""
	if not value:
		return ""
	time = clock(value)
	return f"{_day(value)}, {time}" if time else _day(value)


def _time_of_day(value):
	"""'9:30 AM' from a Time value. The trip as saved holds what the page sent ('09:30:00' or
	'09:30') and the trip before the save what the database returned (a ``timedelta``, and
	``timedelta(0)`` is falsy), so both are put in one form first, as ``api.travel._clock``
	does: the same time must read the same on both sides, or it is a change that never was."""
	if value in (None, ""):
		return ""
	if hasattr(value, "total_seconds"):
		seconds = int(value.total_seconds()) % 86400
		value = f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
	return clock(value)


def _later(value, since):
	"""The end of something that started at ``since``: its time alone on the same day, else
	its date and time. Decided by the one state it describes, never by the other side of the
	change, so the same state always reads the same."""
	if not value:
		return ""
	if since and str(value)[:10] == str(since)[:10]:
		return clock(value)
	return _moment(value)


def _window(start, end):
	"""A pick-up or delivery window: 'Tue Oct 6, 10:00 AM – 2:00 PM'."""
	if not start:
		return ""
	tail = _later(end, start) if end else ""
	return f"{_moment(start)} – {tail}" if tail else _moment(start)


# --------------------------------------------------------------- what one person sees


def _flight(item):
	departs, arrives = item.get("departure_time"), item.get("arrival_time")
	name = " ".join(x for x in (_text(item.get("airline")), _text(item.get("flight_number"))) if x)
	route = f"{item.get('departure_airport') or '?'} → {item.get('arrival_airport') or '?'}"
	values = {
		"airline": _text(item.get("airline")),
		"flight_number": _text(item.get("flight_number")),
		"departure_airport": _text(item.get("departure_airport")),
		"arrival_airport": _text(item.get("arrival_airport")),
		"departure_date": _day(departs),
		"departure_time": clock(departs),
		"arrival_time": _later(arrives, departs),
		"booking_reference": _text(item.get("booking_reference")),
	}
	return (f"Flight {name}, {route}" if name else f"Flight {route}"), values


def _hotel(check_in, check_out):
	first = check_in or check_out
	hotel = _text(first.get("hotel"))
	values = {
		"hotel": hotel,
		"address": _text(first.get("address")),
		"check_in_date": _day(check_in.get("date")) if check_in else "",
		"check_in_time": clock(check_in.get("time")) if check_in else "",
		"check_out_date": _day(check_out.get("date")) if check_out else "",
		"check_out_time": clock(check_out.get("time")) if check_out else "",
		"booking_confirmation": _text(first.get("booking_confirmation")),
	}
	return (f"Room at {hotel}" if hotel else "Room"), values


def _ground(item):
	provider, kind = _text(item.get("provider")), _text(item.get("transport_type"))
	pickup = item.get("pickup_datetime")
	route = f"{item.get('pickup_location') or '?'} → {item.get('dropoff_location') or '?'}"
	values = {
		"transport_type": kind,
		"provider": provider,
		"pickup_location": _text(item.get("pickup_location")),
		"dropoff_location": _text(item.get("dropoff_location")),
		"pickup_date": _day(pickup),
		"pickup_time": clock(pickup),
		"arrival_time": _later(item.get("arrival_datetime"), pickup),
		"return_time": _moment(item.get("return_datetime")),
		"booking_reference": _text(item.get("booking_reference")),
	}
	return (f"Ride with {provider}, {route}" if provider else f"{kind or 'Ride'}, {route}"), values


def _freight(item):
	carrier = _text(item.get("carrier"))
	values = {
		"carrier": carrier,
		"tracking_number": _text(item.get("tracking_number")),
		"deliver_to": _text(item.get("deliver_to")),
		"delivery_window": _window(item.get("delivery_from"), item.get("delivery_to")),
		"pickup_window": _window(item.get("pickup_from"), item.get("pickup_to")),
	}
	return (f"Shipment by {carrier}" if carrier else "Shipment"), values


_ITEM_KINDS = {"flight": ("flight", _flight), "ground": ("ground", _ground), "freight": ("freight", _freight)}


def _place_name(poi, poi_cache):
	"""A stop's Place as its name. ``shape_itinerary`` has usually read it into ``poi_cache``
	already (its entries are its own ``{name, poi_name, ...}`` dicts); otherwise it is read
	here, and not cached, so the cache keeps ``shape_itinerary``'s shape."""
	if not poi:
		return ""
	entry = poi_cache.get(poi)
	if isinstance(entry, dict):
		return _text(entry.get("poi_name")) or _text(poi)
	try:
		return _text(frappe.db.get_value("Travel POI", poi, "poi_name")) or _text(poi)
	except Exception:
		return _text(poi)


def _put(records, key, kind, label, values):
	"""Add one record, keeping a second one with the same key (two rows of one person in one
	booking, which the page never writes) as a record of its own."""
	unique, n = key, 1
	while unique in records:
		n += 1
		unique = f"{key}#{n}"
	records[unique] = (kind, label, values)


def person_records(doc, employee, poi_cache):
	"""``{key: (kind, label, {field: display string})}`` for what ``employee`` sees on the trip
	``doc``, or ``{}`` when they are not on its crew. ``poi_cache`` as ``shape_itinerary``'s,
	shared by every call of one save."""
	from erpnext_enhancements.api.travel import shape_itinerary

	row = _traveler_row(doc, employee)
	if row is None:
		return {}

	records = {}
	_put(
		records,
		f"trip:{doc.name}",
		"trip",
		_text(doc.get("purpose")) or doc.name,
		{
			"first_day": _day(row.get("from_date") or doc.get("start_date")),
			"last_day": _day(row.get("to_date") or doc.get("end_date")),
		},
	)

	itinerary = shape_itinerary(doc, viewing_employee=employee, poi_cache=poi_cache)
	rooms = {}
	for day in itinerary.get("days") or []:
		for item in day.get("items") or []:
			kind = item.get("type")
			if kind in ("hotel_checkin", "hotel_checkout"):
				pair = rooms.setdefault(item.get("group"), [None, None])
				pair[0 if kind == "hotel_checkin" else 1] = item
			elif kind in _ITEM_KINDS:
				name, build = _ITEM_KINDS[kind]
				label, values = build(item)
				_put(records, f"{name}:{item.get('group')}", name, label, values)
	for group, (check_in, check_out) in rooms.items():
		label, values = _hotel(check_in, check_out)
		_put(records, f"hotel:{group}", "hotel", label, values)

	# The schedule, from the rows: every stop is the whole crew's.
	for stop in doc.get("itinerary") or []:
		activity = _text(stop.get("activity_description"))
		_put(
			records,
			f"stop:{stop.name}",
			"stop",
			# "Walk the site (stop)", so "Added: …" and "Removed: …" read as one phrase.
			f"{activity} (stop)" if activity else "A stop",
			{
				"activity": activity,
				"date": _day(stop.get("date")),
				"time": _time_of_day(stop.get("time")),
				"end_time": _time_of_day(stop.get("end_time")),
				"place": _place_name(stop.get("location"), poi_cache),
			},
		)
	return records


def _traveler_row(doc, employee):
	return next((t for t in doc.get("travelers") or [] if t.employee == employee), None)


def _field(kind, name, before, after):
	return {"field": name, "label": dict(FIELD_LABELS[kind])[name], "before": before, "after": after}


def diff_records(before, after):
	"""``[CHANGE]`` between two :func:`person_records` answers, in the order they came:
	``{key, kind, label, label_before, change, fields}``, ``change`` one of ``changed``,
	``added``, ``removed``, and ``fields`` ``[{field, label, before, after}]`` (a value that
	is not there is ``""``). ``label`` is the booking as it is now (as it was, for one that
	was removed), ``label_before`` as the person last knew it."""
	changes = []
	for key in list(before) + [key for key in after if key not in before]:
		old, new = before.get(key), after.get(key)
		kind = (new or old)[0]
		names = [name for name, _label in FIELD_LABELS[kind]]
		if old and new:
			fields = [
				_field(kind, name, old[2].get(name, ""), new[2].get(name, ""))
				for name in names
				if old[2].get(name, "") != new[2].get(name, "")
			]
			if fields:
				changes.append(
					{
						"key": key,
						"kind": kind,
						"label": new[1],
						"label_before": old[1],
						"change": "changed",
						"fields": fields,
					}
				)
		elif old:
			fields = [_field(kind, name, old[2][name], "") for name in names if old[2].get(name)]
			changes.append(
				{
					"key": key,
					"kind": kind,
					"label": old[1],
					"label_before": old[1],
					"change": "removed",
					"fields": fields,
				}
			)
		else:
			fields = [_field(kind, name, "", new[2][name]) for name in names if new[2].get(name)]
			changes.append(
				{
					"key": key,
					"kind": kind,
					"label": new[1],
					"label_before": None,
					"change": "added",
					"fields": fields,
				}
			)
	return changes


def merge_changes(existing, incoming):
	"""One person's waiting changes with a new save's: what the email will say.

	Per field, the earliest "before" and the latest "after"; a field whose two are equal again
	(changed back) is dropped; so is a ``changed`` CHANGE with no field left and no calendar
	entry to cancel, and a booking added and then removed in the same round (it never reached
	anyone). A booking removed and then added again is ``changed``, compared across the two,
	and kept while it still cancels the old entry even when every field is back as it was:
	the person's calendar still holds that entry under its old UID. Canceled calendar events
	(``cancel``) are kept, one per UID."""
	merged = [_copy(change) for change in existing or []]
	index = {change["key"]: i for i, change in enumerate(merged)}
	for change in incoming or []:
		i = index.get(change["key"])
		if i is None:
			index[change["key"]] = len(merged)
			merged.append(_copy(change))
			continue
		old = merged[i]
		existed = old["change"] != "added"
		exists = change["change"] != "removed"
		fields = {f["field"]: dict(f) for f in old.get("fields") or []}
		for f in change.get("fields") or []:
			if f["field"] in fields:
				fields[f["field"]].update(label=f["label"], after=f["after"])
			else:
				fields[f["field"]] = dict(f)
		cancel = [dict(entry) for entry in old.get("cancel") or []]
		seen = {entry["uid"] for entry in cancel}
		cancel += [dict(entry) for entry in change.get("cancel") or [] if entry["uid"] not in seen]
		label_before = old.get("label_before") if existed else None
		if existed and exists:
			status = "changed"
		elif exists:
			status = "added"
		elif existed:
			status = "removed"
		else:
			merged[i] = None  # added, then removed again: nobody was told about it
			continue
		entry = {
			"key": change["key"],
			"kind": change["kind"],
			"label": change["label"] if exists else (label_before or change["label"]),
			"label_before": label_before,
			"change": status,
			"fields": list(fields.values()),
		}
		if cancel:
			entry["cancel"] = cancel
		merged[i] = entry

	result = []
	for change in merged:
		if change is None:
			continue
		change["fields"] = [f for f in change["fields"] if f["before"] != f["after"]]
		if change["change"] == "changed" and not change["fields"] and not change.get("cancel"):
			continue
		result.append(change)
	return result


def _copy(change):
	copied = dict(change)
	copied["fields"] = [dict(f) for f in change.get("fields") or []]
	if change.get("cancel"):
		copied["cancel"] = [dict(entry) for entry in change["cancel"]]
	return copied


# --------------------------------------------------------------- the calendar


def _uid_keys(doc, row):
	"""``{calendar UID: CHANGE key}`` for the events one person's rows on ``doc`` make."""
	keys = {event_uid(doc.name, row.name, "-span"): f"trip:{doc.name}"}
	for table, kind in _EVENT_TABLES:
		for booking in doc.get(table) or []:
			traveler = booking.get("traveler")
			if not traveler or traveler == row.employee:
				keys[event_uid(doc.name, booking.name)] = f"{kind}:{group_key(booking)}"
	return keys


def _cancel_entry(event):
	"""What is kept of a calendar event that has to be canceled later: enough to write it
	again under its UID. The summary says so, for a calendar that ignores STATUS."""
	entry = {
		"uid": event["uid"],
		"summary": f"Canceled: {event.get('summary') or ''}".strip(),
		"start": str(event["start"]),
		"all_day": bool(event.get("all_day")),
	}
	if event.get("end"):
		entry["end"] = str(event["end"])
	return entry


def _attach_cancels(
	changes, before, before_row, doc, after_row, address_text, aliases=None, known=None, labels=None
):
	"""Put each calendar event this person had before the save and has no longer on the
	CHANGE it belongs to (``cancel``), so the alert can cancel it under the same UID.

	An event whose booking has no CHANGE — the booking reads the same, but its row, and so its
	UID, was replaced — gets a CHANGE of its own with no fields, only the cancel: without it
	the person's calendar keeps the old entry beside the new one. ``known``, when given, is
	the set of UIDs the person had when this round of edits began; an event outside it was
	made and lost inside the round and never reached them, so it is not canceled. ``aliases``
	as :func:`_rekey`'s; ``labels`` ``{key: label}`` for a CHANGE made here.

	Returns the UIDs the person had before the save."""
	if before_row is None:
		return set()
	now = (
		{event["uid"] for event in trip_events_for_traveler(doc, after_row, address_text)}
		if after_row is not None
		else set()
	)
	keys = _uid_keys(before, before_row)
	by_key = {change["key"]: change for change in changes}
	had = set()
	for event in trip_events_for_traveler(before, before_row, address_text):
		had.add(event["uid"])
		if event["uid"] in now:
			continue
		if known is not None and event["uid"] not in known:
			continue
		key = keys.get(event["uid"])
		key = _rekey(key, aliases) if key else f"calendar:{event['uid']}"
		change = by_key.get(key)
		if change is None:
			label = (labels or {}).get(key) or event.get("summary") or ""
			change = {
				"key": key,
				"kind": key.split(":", 1)[0],
				"label": label,
				"label_before": label,
				"change": "changed",
				"fields": [],
			}
			changes.append(change)
			by_key[key] = change
		change.setdefault("cancel", []).append(_cancel_entry(event))
	return had


# --------------------------------------------------------------- the same booking, a new key


def _group_aliases(before, doc):
	"""``{"<kind>:row:<name>": "<kind>:<group>"}`` for every booking row that was keyed by its
	row before this save (entered on the desk form: no ``booking_group``) and has a group now.
	Plan a Trip gives every such row an id the first time it saves the trip
	(``planner.normalize_group``, ``merge_freight``), and the row, its name and its calendar
	UID are unchanged: the same booking under a new key."""
	aliases = {}
	for table, kind in _EVENT_TABLES:
		was = {row.get("name"): row for row in before.get(table) or [] if row.get("name")}
		for row in doc.get(table) or []:
			old = was.get(row.get("name"))
			group = row.get("booking_group")
			if old is None or old.get("booking_group") or not group:
				continue
			aliases[f"{kind}:row:{row.get('name')}"] = f"{kind}:{group}"
	return aliases


def _rekey(key, aliases):
	"""``key`` under its new name, ``#n`` suffix kept (:func:`_put`)."""
	if not aliases or not key:
		return key
	if key in aliases:
		return aliases[key]
	base, sep, suffix = key.partition("#")
	return aliases.get(base, base) + sep + suffix


def _rekeyed(records, aliases):
	"""``records`` (:func:`person_records`) with each key under its new name."""
	if not aliases:
		return records
	out = {}
	for key, record in records.items():
		_put(out, _rekey(key, aliases), *record)
	return out


def _paired_keys(before, after):
	"""``{old key: new key}`` for a booking that left under one key and arrived under another
	with every compared value the same: to the person it is the booking they had. Plan a Trip
	does this when someone is unticked from a whole-crew booking entered on the form — the rest
	get a row each, under a new group, with the same details. Paired one to one, in order."""
	arrived = [key for key in after if key not in before]
	pairs = {}
	for key, (kind, _label, values) in before.items():
		if key in after:
			continue
		match = next((new for new in arrived if after[new][0] == kind and after[new][2] == values), None)
		if match is not None:
			pairs[key] = match
			arrived.remove(match)
	return pairs


# --------------------------------------------------------------- detection


def record_trip_changes(doc, before):
	"""Travel Trip ``on_update`` (via ``notifications.on_trip_update``): record what this save
	changed for each person as their Pending Trip Change Alert. Never raises: a change alert
	must not stop a trip from saving — except a deadlock or lock timeout, which has already
	rolled the save back (:func:`_lost_the_transaction`)."""
	if _in_maintenance_context() or not before:
		return
	if before.status not in ACTIVE_STATUSES or doc.status not in ACTIVE_STATUSES:
		return
	try:
		if not change_alerts_enabled():
			return
		_record(doc, before)
	except Exception as exc:
		if _lost_the_transaction(exc):
			raise
		frappe.log_error(
			title="Trip change alert failed",
			message=f"{doc.name}: recording changes\n{frappe.get_traceback()}",
		)


def _lost_the_transaction(exc):
	"""True for a deadlock or a lock-wait timeout (frappe v16 ``Database.sql`` raises
	``QueryDeadlockError`` for ER_LOCK_DEADLOCK and ER_CHECKREAD, ``QueryTimeoutError`` for a
	timeout). InnoDB rolls the whole transaction back on a deadlock — the trip's own UPDATE,
	made before ``on_update`` ran, with it. Logged and swallowed, the request would go on to
	commit a Version row and the rest into a fresh transaction and answer "saved" for a trip
	the database never took; the page's next save is then refused as out of date and the edit
	is gone. Raised, the save fails where it can be seen and tried again."""
	kinds = tuple(
		kind
		for kind in (getattr(frappe, "QueryDeadlockError", None), getattr(frappe, "QueryTimeoutError", None))
		if isinstance(kind, type)
	)
	return bool(kinds) and isinstance(exc, kinds)


def _actor_employee():
	"""The Employee of the user making this save, or ``None``."""
	user = getattr(frappe.session, "user", None)
	if not user or user == "Guest":
		return None
	try:
		return frappe.db.get_value("Employee", {"user_id": user}, "name")
	except Exception:
		return None


def _pending(trip):
	"""``{employee: {name, changes, known}}`` of the trip's Pending alerts.

	Found with a plain read, then each locked by its primary key and read again under that
	lock, so a send that has just claimed one is seen as Sent (and a new one started) rather
	than written over. Not one ``WHERE trip=… AND status='Pending' … FOR UPDATE``: on the first
	save of a round that matches nothing, and under REPEATABLE READ a locking read that matches
	nothing takes a gap lock. Two saves of two different trips whose rows sort into the same gap
	each took one and then inserted into it — a textbook deadlock, whose victim is the whole
	save (:func:`_lost_the_transaction`). A lock by primary key is a record lock only. The
	sender never inserts, and two saves of the one trip are already serialized by the trip's
	own row lock (``load_doc_before_save``) and ``check_if_latest``, so nothing needs the gap."""
	names = frappe.get_all(
		DOCTYPE,
		filters={"trip": trip, "status": "Pending"},
		pluck="name",
		order_by="creation asc",
	)
	pending = {}
	for name in names or []:
		row = frappe.db.get_value(
			DOCTYPE,
			name,
			["name", "employee", "status", "changes", "known_uids"],
			as_dict=True,
			for_update=True,
		)
		if not row or row.status != "Pending":
			continue
		pending.setdefault(
			row.employee,
			{"name": row.name, "changes": load_changes(row.changes), "known": _load_known(row.known_uids)},
		)
	return pending


def _load_known(value):
	"""A stored ``known_uids`` as a set, or ``None`` when the row has none (then nothing is
	filtered)."""
	if value in (None, ""):
		return None
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except (TypeError, ValueError):
			return None
	return {str(uid) for uid in value} if isinstance(value, list) else None


def load_changes(value):
	"""A stored ``changes`` value as a list (a JSON field reads back as a string)."""
	if isinstance(value, list):
		return value
	if not value:
		return []
	try:
		loaded = json.loads(value)
	except (TypeError, ValueError):
		return []
	return loaded if isinstance(loaded, list) else []


def _record(doc, before):
	now = now_datetime()
	actor = _actor_employee()
	poi_cache = {}
	address_text = _address_resolver(poi_cache)
	before_rows = {t.employee: t for t in before.get("travelers") or [] if t.employee}
	after_rows = {t.employee: t for t in doc.get("travelers") or [] if t.employee}
	pending = _pending(doc.name)
	group_aliases = _group_aliases(before, doc)

	for employee in list(before_rows) + [e for e in after_rows if e not in before_rows]:
		before_row, after_row = before_rows.get(employee), after_rows.get(employee)
		waiting = pending.get(employee)
		# The person who made the change knows it; someone just added gets "You were added to
		# a trip". Either way no alert is started for them — but one already waiting takes
		# this change in, so it never tells them something that is no longer true.
		if waiting is None and (employee == actor or before_row is None):
			continue
		was = person_records(before, employee, poi_cache) if before_row is not None else {}
		now_records = person_records(doc, employee, poi_cache) if after_row is not None else {}
		# The same booking under a new key keeps the key's history: its row given a group by
		# this save, or replaced by a row that reads exactly the same.
		was = _rekeyed(was, group_aliases)
		aliases = dict(group_aliases)
		paired = _paired_keys(was, now_records)
		aliases.update(paired)
		was = _rekeyed(was, paired)
		changes = diff_records(was, now_records)
		labels = {key: record[1] for key, record in was.items()}
		labels.update({key: record[1] for key, record in now_records.items()})
		had = _attach_cancels(
			changes,
			before,
			before_row,
			doc,
			after_row,
			address_text,
			aliases=aliases,
			known=waiting["known"] if waiting else None,
			labels=labels,
		)
		earlier = (
			[dict(change, key=_rekey(change["key"], aliases)) for change in waiting["changes"]]
			if waiting
			else []
		)
		# A waiting change whose booking has just been re-keyed is written under its new key even
		# when nothing else changed, or the next save would find it under neither.
		if not changes and earlier == (waiting["changes"] if waiting else []):
			continue
		merged = merge_changes(earlier, changes)
		_save(doc.name, employee, waiting, merged, now, known=had)


def _save(trip, employee, waiting, merged, now, known=None):
	"""Write one person's merged changes: update the row waiting for them, start one, or delete
	the waiting row when nothing is left. ``known``: the UIDs the person had before this save,
	stored when a row is started, for the round's later cancels (:func:`_attach_cancels`)."""
	if not merged:
		if waiting:
			frappe.db.delete(DOCTYPE, {"name": waiting["name"]})
		return
	payload = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
	if waiting:
		frappe.db.set_value(DOCTYPE, waiting["name"], {"changes": payload, "last_change_at": now})
		return
	frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"trip": trip,
			"employee": employee,
			"status": "Pending",
			# A JSON field refuses a list (v16 BaseDocument), so it is stored as its text.
			"changes": payload,
			"known_uids": json.dumps(sorted(known or ()), separators=(",", ":")),
			"first_change_at": now,
			"last_change_at": now,
		}
	).insert(ignore_permissions=True)


# --------------------------------------------------------------- the email


def removed_from_trip(changes):
	return any(c.get("kind") == "trip" and c.get("change") == "removed" for c in changes)


def alert_sections(changes):
	"""``[{title, lines}]`` — the email's "What changed", one section per CHANGE, trip first,
	then flights, rooms, rides, shipments and stops. Lines are plain text; the template
	escapes them."""
	if removed_from_trip(changes):
		return [{"title": "You are no longer on this trip.", "lines": []}]

	def order(change):
		kind = change.get("kind")
		return KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER)

	sections = []
	for change in sorted(changes, key=order):
		fields = change.get("fields") or []
		status = change.get("change")
		if status == "changed" and not fields:
			continue  # only a calendar entry replaced: nothing to read, the attachment does it
		if change.get("kind") == "trip":
			title = "You were added to this trip." if status == "added" else "Your dates on this trip"
		elif status == "added":
			title = f"Added: {change.get('label')}"
		elif status == "removed":
			title = f"Removed: {change.get('label')}"
		else:
			title = change.get("label") or ""
		if status == "added":
			lines = [f"{f['label']}: {f['after']}" for f in fields if f.get("after")]
		elif status == "removed":
			lines = []
		else:
			lines = [
				f"{f['label']}: {f.get('before') or 'not set'} → {f.get('after') or 'not set'}"
				for f in fields
			]
		sections.append({"title": title, "lines": lines})
	if not sections and any(change.get("cancel") for change in changes):
		# Every change was a booking entered again with nothing about it different: the old
		# calendar entry has to go, or it shows twice next to the new one.
		sections.append(
			{
				"title": CALENDAR_ONLY,
				"lines": [
					"Nothing on your itinerary changed, but a booking was entered again. The attached "
					"calendar file replaces its old entry, so it does not show twice."
				],
			}
		)
	return sections


def alert_calendar(doc, recipient, changes, address_text=None):
	"""The person's calendar for the alert: every event they have on the trip now, and every one
	they no longer have canceled under its old UID, each with the trip's SEQUENCE. ``None``
	when there is nothing to put on it."""
	sequence = sequence_of(doc)
	events = trip_events_for_traveler(doc, recipient.row, address_text) if recipient.row is not None else []
	uids = {event["uid"] for event in events}
	events = [dict(event, sequence=sequence) for event in events]
	for change in changes:
		for entry in change.get("cancel") or []:
			if entry["uid"] in uids:
				continue
			uids.add(entry["uid"])
			events.append(dict(entry, status="CANCELLED", sequence=sequence))
	if not events:
		return None
	return {"fname": f"{frappe.scrub(doc.name)}.ics", "fcontent": build_ics(events)}


def alert_email(doc, recipient, changes):
	"""``{subject, template, context, attachments}`` for one person's alert."""
	removed = removed_from_trip(changes)
	cache = {}
	calendar = alert_calendar(doc, recipient, changes, _address_resolver(cache))
	return {
		"subject": _("Trip update: {0} ({1} – {2})").format(doc.purpose, doc.start_date, doc.end_date),
		"template": TEMPLATE,
		"context": dict(
			_base_context(doc),
			sections=alert_sections(changes),
			removed_from_trip=removed,
		),
		"attachments": [calendar] if calendar else [],
	}


# --------------------------------------------------------------- the sender


def send_due_change_alerts():
	"""Scheduler (hooks.py ``cron`` ``*/5 * * * *``): send every Pending alert whose last change
	is at least :data:`QUIET_MINUTES` old, on a trip nobody has saved for as long (the trip's
	``modified``, checked in :func:`_send_one`). Re-drivable: what a flushed queue, a failed
	run or a trip still being edited leaves Pending is sent by a later run."""
	if _in_maintenance_context():
		return
	cutoff = now_datetime() - timedelta(minutes=QUIET_MINUTES)
	due = frappe.get_all(
		DOCTYPE,
		filters={"status": "Pending", "last_change_at": ["<=", cutoff]},
		pluck="name",
		order_by="last_change_at asc",
		limit=SEND_LIMIT,
	)
	if not due:
		return
	enabled = change_alerts_enabled()
	for name in due:
		try:
			_send_one(name, cutoff, enabled)
		except Exception:
			# Never re-raise out of the job: a job's traceback is logged with its frame locals.
			try:
				frappe.db.rollback()
				frappe.db.set_value(
					DOCTYPE,
					name,
					{"status": "Failed", "error": _("The alert could not be prepared; see the Error Log.")},
				)
				frappe.log_error(
					title="Trip change alert failed", message=f"{name}\n{frappe.get_traceback()}"
				)
				frappe.db.commit()
			except Exception:
				return  # the database itself is failing; what is left stays Pending for the next run


def _finish(name, status, error=None):
	frappe.db.set_value(DOCTYPE, name, {"status": status, "error": error})
	frappe.db.commit()


def _send_one(name, cutoff, enabled):
	row = frappe.db.get_value(
		DOCTYPE,
		name,
		["name", "trip", "employee", "status", "changes", "last_change_at"],
		as_dict=True,
		for_update=True,
	)
	# Claimed by another run, or changed again since the list was read: not due any more.
	if (
		not row
		or row.status != "Pending"
		or not row.last_change_at
		or get_datetime(row.last_change_at) > cutoff
	):
		frappe.db.rollback()
		return
	if not enabled:
		return _finish(name, "Skipped", _("Change alerts were turned off before this was sent."))
	if not frappe.db.exists("Travel Trip", row.trip):
		return _finish(name, "Skipped", _("The trip no longer exists."))
	doc = frappe.get_doc("Travel Trip", row.trip)
	# The quiet period is the trip's: someone still working through Plan a Trip saved it less
	# than QUIET_MINUTES ago, and their next step may touch this person again (a stop is the
	# whole crew's). Timed per person, one walk through the steps sent Ann two emails.
	if doc.get("modified") and get_datetime(doc.get("modified")) > cutoff:
		frappe.db.rollback()
		return
	if doc.status not in ACTIVE_STATUSES:
		return _finish(name, "Skipped", _("The trip is {0}, not Booked or In Progress.").format(doc.status))
	changes = load_changes(row.changes)
	if not changes:
		return _finish(name, "Skipped", _("Nothing left to tell them."))
	traveler = _traveler_row(doc, row.employee)
	if traveler is None and not removed_from_trip(changes):
		return _finish(name, "Skipped", _("They are no longer on the trip."))
	recipient = _employee_recipient(row.employee, traveler)
	if recipient is None or not recipient.email:
		return _finish(name, "Skipped", _("No email address for this person."))

	# Stamp FIRST, as reminders.py does: at most once, even if the send below dies.
	frappe.db.set_value(DOCTYPE, name, {"status": "Sent", "sent_at": now_datetime(), "error": None})
	frappe.db.commit()
	try:
		email = alert_email(doc, recipient, changes)
		message, html = _render(recipient, email["subject"], email["template"], email["context"])
		_deliver(recipient, email["subject"], message, html, doc, email["attachments"])
		frappe.db.commit()
	except Exception as exc:
		frappe.db.rollback()
		frappe.db.set_value(
			DOCTYPE, name, {"status": "Failed", "error": f"{type(exc).__name__}: {exc}"[:500]}
		)
		frappe.log_error(
			title="Trip change alert failed",
			message=f"{name}: {row.trip} -> {row.employee}\n{frappe.get_traceback()}",
		)
		frappe.db.commit()
