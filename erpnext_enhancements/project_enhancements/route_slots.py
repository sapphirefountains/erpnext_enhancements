# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Clock times for every stop of a person's day, exactly as the planner's route view prints them.

Calendar sync C3 (v1.606.0) and the leave-by ping both need "when is Austin on site at Riverwalk
on Thursday, and when must he leave to get there". The planner already answers that on its route
view (``api.project_planner.route_payload``): leave the shop at Project Planner Settings
``day_start_time``, drive each leg (the Planner Drive Time cache, Google Routes for a miss, else the
straight-line estimate), work the stop's booked hours, and wait for a stop whose time slot starts
later. This module runs **the same two passes** over the same engine answer, so the calendar, the
ping and the route view can never disagree:

1. ``routing.route_times`` over the located stops, to get arrival times;
2. ``api.project_planner.merge_unlocated`` puts the stops that have no location where they most
   likely happen (they are never driven to), and ``route_times`` runs again over the merged day.

**Times are computed on read and never stored** on the task or visit. The engine call is
``crew_availability._compute``, the one ``get_route`` makes, so tentative (pencil) bookings are not
routed (a pencil is never a stop), a travel day has no route, and Project Planner Settings
``use_google_routes`` and the routes-down flag are respected exactly as they are for the planner.

**Drive padding** (``pad_drive_time``) decides whether the driving is *counted against a person's
hours*; it does not move the clock. The route view always drives the legs before a stop, and so do
these times, whatever that switch says.

A slot is ``{"order", "first", "arrive", "depart", "drive_minutes", "leave_by", "kind", "ref",
"label", "project", "tentative"}``; ``arrive``/``depart``/``leave_by`` are minutes after midnight
(past midnight keeps counting, as ``routing.fmt_clock`` does). ``leave_by`` is the arrival less the
drive to it: when the person must leave the shop (or their previous stop) to arrive on time, and
None when the stop is not driven to (no location, or the shop could not be located).

:func:`day_slots` and :func:`stop_key` are pure; ``tests/test_calendar_route_timing.py`` runs them.
"""

from erpnext_enhancements.project_enhancements import routing

TASK_FAMILY = "task"
VISIT_FAMILY = "visit"


def stop_key(kind, ref, key=None):
	"""What identifies a stop across the planner and the calendar.

	``("task", task name)`` for a task or rental booking; ``("visit", record name)`` for a visit
	record, and ``("visit", projected key)`` for a projected contract visit (whose booking ``ref`` is
	the contract, shared by every visit it projects). None for anything that is not a stop.
	"""
	if kind in ("task", "rental"):
		return (TASK_FAMILY, ref) if ref else None
	if kind == "visit":
		value = key or ref
		return (VISIT_FAMILY, value) if value else None
	return None


def _merge_unlocated():
	from erpnext_enhancements.api.project_planner import merge_unlocated

	return merge_unlocated


def day_slots(route, day_start, merge_unlocated=None):
	"""``{stop_key: slot}`` for one person-day's ``route`` (a ``routing.build_route`` answer).

	The same arithmetic as ``api.project_planner.route_payload``: see the module docstring.
	``merge_unlocated`` defaults to the route view's own function.
	"""
	if not route:
		return {}
	merge = merge_unlocated or _merge_unlocated()
	shop = route.get("shop")
	legs = {(entry["from"], entry["to"]): entry for entry in route.get("legs") or []}

	def between(a, b):
		found = legs.get((a, b)) or routing.leg({}, a, b)
		return found["minutes"] if found else 0.0

	def hours_of(stop):
		return stop.get("hours")

	day_start = day_start or routing.DEFAULT_DAY_START
	located = list(route.get("stops") or [])
	first_pass = routing.route_times(day_start, located, between, hours_of, start=shop, end=shop)
	arrivals = [routing.parse_minutes(t["arrive"]) for t in first_pass["stops"]]
	ordered = merge(located, route.get("unlocated") or [], arrivals)
	times = routing.route_times(day_start, ordered, between, hours_of, start=shop, end=shop)

	out = {}
	for index, (stop, timing) in enumerate(zip(ordered, times["stops"], strict=False)):
		booking = stop.get("booking") or {}
		key = stop_key(stop.get("kind"), stop.get("ref"), booking.get("key"))
		if key is None or key in out:
			continue
		arrive = routing.parse_minutes(timing["arrive"])
		depart = routing.parse_minutes(timing["depart"])
		drive = timing.get("drive_minutes")
		out[key] = {
			"order": index + 1,
			"first": index == 0,
			"arrive": arrive,
			"depart": depart,
			"drive_minutes": drive,
			"leave_by": (arrive - drive) if drive is not None else None,
			"kind": stop.get("kind"),
			"ref": stop.get("ref"),
			"label": stop.get("label") or booking.get("label") or stop.get("ref") or "",
			"project": stop.get("project"),
			"tentative": bool(booking.get("tentative")),
		}
	return out


def first_slot(slots):
	"""``(key, slot)`` of the first stop of a day, or ``(None, None)``."""
	for key, slot in (slots or {}).items():
		if slot.get("first"):
			return key, slot
	return None, None


def compute(start, end, resources=None, google=True):
	"""Every routed person-day from ``start`` to ``end`` with its slots.

	``{"days": {(resource, date): {"slots", "capacity", "off"}}, "user_to_resource", "labels",
	"users", "day_start"}``. One ``crew_availability._compute`` (so one ``drive_matrix`` call for the
	whole range); ``google=False`` makes no Google request of any kind. Raises what the engine
	raises: the caller decides what a failure means.
	"""
	from erpnext_enhancements.project_enhancements import crew_availability as ca

	data = ca._compute(start, end, resources, google=google)
	settings = data.get("settings") or {}
	day_start = settings.get("day_start_time") or routing.DEFAULT_DAY_START
	merge = _merge_unlocated()
	days = {}
	for (resource, day), route in (data.get("routes") or {}).items():
		cell = ((data.get("days") or {}).get(resource) or {}).get(str(day)) or {}
		days[(resource, day)] = {
			"slots": day_slots(route, day_start, merge),
			"capacity": cell.get("capacity"),
			"off": cell.get("off"),
		}
	people = data.get("resources") or []
	return {
		"days": days,
		"user_to_resource": dict(data.get("user_to_resource") or {}),
		"labels": {p.get("name"): p.get("label") or p.get("name") for p in people},
		"users": {p.get("name"): p.get("user") for p in people},
		"day_start": day_start,
	}
