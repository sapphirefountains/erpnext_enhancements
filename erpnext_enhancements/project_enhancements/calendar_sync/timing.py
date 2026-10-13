# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Calendar sync C3 (v1.605.0): give route stops real times, and a leave-by line.

C2 writes every item as an all-day event. A task or visit that is a **stop on someone's route that
day** now gets a timed event: on site from the route's arrival to its departure, exactly as the
planner's route view computes it (``route_slots``: leave the shop at the planner's day start, drive
each leg, work the booked hours). The first stop of the day also says **"Leave by 7:35 am"**.

Nothing here is stored on a task or visit: times are computed on read, every time an item is
reconciled. :func:`attach` puts a ``timing`` dict on the item (``payload.event_times`` turns it into
``dateTime`` start and end) or leaves the item all-day.

Which items are timed (Nik, 2026-10-12):

* A **single-day, firm, active** task, visit or projected visit **in the near window** (Calendar Sync
  Settings ``timed_days_ahead``, default 14 days, today included) that is a stop on its timing
  person's route that day. The planner routes projected visits (they are not pencils), so they are
  timed too.
* **All-day** stays all-day: multi-day tasks, full-day tasks and visits (a task with no estimate,
  no slot and no rental kind books the whole day), day notes, blocks, pencils (never routed), a day
  beyond the near window, and a day whose route could not be worked out (logged once an hour).
* **Past days keep whatever they had**, and so does finished work: the times stored on the item's
  ``Calendar Sync Link`` (``timing``) when its event was last written are reused while the item is
  still on that day, so a ✓ added to yesterday's job leaves its slot alone, and the nightly repair
  never prices a route in the past.

**One event per task, shared by its crew; the crew's routes may differ** (a judgement call Nik can
revisit). The event is timed from the **lead's** route; with no lead, from the first crew member in
order; if that person has no route that day (a travel day), from the next one who does. When
another crew member's slot differs, the description says "(times differ by person)". A leave-by line
shows only when the stop is the first of the timing person's day.

**Bounded Google Routes cost.** Routes are worked out only for days in the near window, through the
planner's own engine (the drive-time cache first; Google only for a missing leg, and only where the
planner itself would ask). The nightly repair primes the whole near window in **one** engine call
(:func:`prime`); an item job works out only its own day. The preview (``engine.plan``) asks with
``google=False``: no Google request at all.
"""

import datetime
import json

import frappe

from erpnext_enhancements.project_enhancements import route_slots
from erpnext_enhancements.project_enhancements.calendar_sync import payload

NEAR_DEFAULT = 14
NEAR_MAX = 60
DEFAULT_TZ = "America/Denver"
LOCAL_KEY = "ee_calendar_route_days"
FAILED_FLAG = "ee_calendar_timing_failed"
FAILED_SECONDS = 3600
ROUTABLE = (payload.KIND_TASK, payload.KIND_VISIT, payload.KIND_PROJECTED)


def _today():
	from frappe.utils import getdate, nowdate

	return getdate(nowdate())


def timezone():
	"""The site's time zone (System Settings), else America/Denver. Never raises."""
	try:
		from frappe.utils import get_system_timezone

		return get_system_timezone() or DEFAULT_TZ
	except Exception:
		return DEFAULT_TZ


# ---------------------------------------------------------------------- pure rules


def near_days(doc):
	"""Calendar Sync Settings ``timed_days_ahead``. A field with no ``tabSingles`` row reads None,
	which is the default (14); a deliberate 0 switches timing off (every event all-day, as in C2)."""
	value = (doc or {}).get("timed_days_ahead")
	if value in (None, ""):
		return NEAR_DEFAULT
	try:
		return max(min(int(value), NEAR_MAX), 0)
	except (TypeError, ValueError):
		return NEAR_DEFAULT


def near_window(doc, today):
	"""``(today, last day)`` that gets timed slots, or None when timing is off."""
	days = near_days(doc)
	if days <= 0:
		return None
	return today, today + datetime.timedelta(days=days - 1)


def in_window(day, window):
	return bool(window and day and window[0] <= day <= window[1])


def single_day(item):
	"""The item's one day, or None when it spans several (or has none)."""
	start = payload._date(item.get("start"))
	end = payload._date(item.get("end")) or start
	return start if start and end == start else None


def is_pencil(item):
	"""A pencilled task or visit. A projected visit carries ``tentative`` too, but the planner routes
	it, so it is not a pencil here."""
	return bool(item.get("tentative")) and item.get("kind") != payload.KIND_PROJECTED


def is_full_day(item):
	return bool(item.get("full_day"))


def item_stop_key(item):
	kind = item.get("kind")
	if kind == payload.KIND_TASK:
		return route_slots.stop_key("task", item.get("name"))
	if kind == payload.KIND_VISIT:
		return route_slots.stop_key("visit", item.get("name"))
	if kind == payload.KIND_PROJECTED:
		return route_slots.stop_key("visit", item.get("name"), item.get("key"))
	return None


def timeable(item):
	"""Could this item be a timed stop at all (before asking any route)?"""
	return (
		item.get("kind") in ROUTABLE
		and item.get("state") == payload.STATE_ACTIVE
		and not is_pencil(item)
		and not is_full_day(item)
		and single_day(item) is not None
	)


def candidates(item, user_to_resource):
	"""Planner Resources whose route can time the item, best first: the lead, then the crew in order.

	A task's crew member carries ``resource``; a visit's carries ``user`` (its technician is the
	lead). Duplicates and people with no resource are dropped.
	"""
	crew = [m for m in item.get("crew") or [] if isinstance(m, dict)]
	ordered = [m for m in crew if m.get("lead")] + [m for m in crew if not m.get("lead")]
	out = []
	for member in ordered:
		resource = member.get("resource") or (user_to_resource or {}).get(member.get("user"))
		if resource and resource not in out:
			out.append(resource)
	return out


def pick(item, day, days, user_to_resource, labels=None):
	"""The ``timing`` for an item from the routed person-days, or None (stays all-day).

	``days`` is ``route_slots.compute``'s ``days``. See the module docstring for the lead rule.
	"""
	key = item_stop_key(item)
	if key is None:
		return None
	found = []
	for resource in candidates(item, user_to_resource):
		slot = (((days or {}).get((resource, day)) or {}).get("slots") or {}).get(key)
		if slot:
			found.append((resource, slot))
	if not found:
		return None
	resource, slot = found[0]
	differs = any(
		(round(other["arrive"]), round(other["depart"])) != (round(slot["arrive"]), round(slot["depart"]))
		for _r, other in found[1:]
	)
	leave_by = slot.get("leave_by") if slot.get("first") else None
	return {
		"date": str(day),
		"start": round(slot["arrive"]),
		"end": round(slot["depart"]),
		"leave_by": round(leave_by) if leave_by is not None else None,
		"differs": differs,
		"person": payload.first_name((labels or {}).get(resource) or "") or "",
	}


def stored_timing(link, day):
	"""The timing last written for this item, when it is still for ``day``; else None."""
	if link is None or not day:
		return None
	try:
		value = json.loads(link.get("timing") or "null")
	except (TypeError, ValueError):
		return None
	if not isinstance(value, dict) or value.get("date") != str(day):
		return None
	return value


# ---------------------------------------------------------------------- routes, cached per job


def _store(google):
	"""This request's (or job's) routed days: one engine call per day, or per window when primed."""
	cache = getattr(frappe.local, LOCAL_KEY, None)
	if cache is None:
		cache = {}
		setattr(frappe.local, LOCAL_KEY, cache)
	return cache.setdefault(
		bool(google), {"days": {}, "done": set(), "failed": set(), "users": {}, "labels": {}}
	)


def forget():
	"""Drop the cached routes (a long job that wants fresh ones)."""
	setattr(frappe.local, LOCAL_KEY, None)


def _failed(days, start):
	"""Log once an hour that a day's route could not be worked out; the day stays all-day."""
	try:
		if frappe.cache.get_value(FAILED_FLAG):
			return
		frappe.cache.set_value(FAILED_FLAG, 1, expires_in_sec=FAILED_SECONDS)
		frappe.log_error(
			title="Calendar sync: route times could not be worked out",
			message=f"Routes from {start} ({len(days)} day(s)) failed: their events stay all-day until "
			"the next sync. The Project Planner's own route view will show the same failure.",
		)
	except Exception:
		pass


def load(start, end, google=True):
	"""Work out and cache every routed person-day from ``start`` to ``end``. Never raises."""
	store = _store(google)
	wanted = []
	day = start
	while day <= end:
		if day not in store["done"]:
			wanted.append(day)
		day += datetime.timedelta(days=1)
	if not wanted:
		return store
	try:
		data = route_slots.compute(wanted[0], wanted[-1], google=google)
	except Exception:
		store["failed"].update(wanted)
		store["done"].update(wanted)
		_failed(wanted, wanted[0])
		return store
	store["days"].update(data.get("days") or {})
	store["users"].update(data.get("user_to_resource") or {})
	store["labels"].update(data.get("labels") or {})
	store["done"].update(wanted)
	return store


def prime(doc, today=None, google=True):
	"""Work out the whole near window in one engine call (the nightly repair, a day job)."""
	window = near_window(doc, today or _today())
	if window:
		load(window[0], window[1], google=google)


def timing_for(item, doc, today=None, google=True):
	"""The computed ``timing`` for an item, or None. Reads routes only for a timeable item in the
	near window."""
	today = today or _today()
	if not timeable(item):
		return None
	day = single_day(item)
	if not in_window(day, near_window(doc, today)):
		return None
	store = load(day, day, google=google)
	if day in store["failed"]:
		return None
	timing = pick(item, day, store["days"], store["users"], store["labels"])
	if timing:
		timing["tz"] = timezone()
	return timing


def attach(item, doc, link=None, today=None, google=True):
	"""``item`` with ``timing`` set when its event should be timed (a copy; the input is untouched).

	* gone: unchanged;
	* a past day, or finished work: the timing stored on its link for the same day, else all-day;
	* otherwise: :func:`timing_for`.

	Never raises: on any failure the item goes out all-day, which is what C2 wrote.
	"""
	if not item or item.get("state") == payload.STATE_GONE or item.get("kind") not in ROUTABLE:
		return item
	out = dict(item)
	out.pop("timing", None)
	try:
		today = today or _today()
		day = single_day(item)
		if day and (day < today or item.get("state") == payload.STATE_FINISHED):
			stored = stored_timing(link, day)
			if stored and not is_pencil(item) and not is_full_day(item):
				out["timing"] = stored
			return out
		timing = timing_for(item, doc, today, google)
		if timing:
			out["timing"] = timing
	except Exception:
		try:
			frappe.log_error(
				title="Calendar sync: route times failed for an item",
				message=f"{item.get('doctype')} {item.get('name')}: its event stays all-day this time.",
			)
		except Exception:
			pass
	return out


def stored_value(item):
	"""What goes in the link's ``timing`` field: the JSON of the item's timing, or ''."""
	timing = item.get("timing")
	return json.dumps(timing, sort_keys=True) if timing else ""


# ---------------------------------------------------------------------- the day fan-out


def fan_out_days(item, link_before, doc, today=None):
	"""Days whose OTHER stops must be re-timed after this item synced.

	Moving one stop changes the times of every other stop that person drives that day, and the
	event of each of them. So for a task or visit: every day it covers now, plus the day its event
	was on before (a move, a cancellation), kept to the near window. A pencil that was a pencil
	before (or never synced) changes no route, so it fans out nothing.
	"""
	if (item or {}).get("kind") not in (payload.KIND_TASK, payload.KIND_VISIT):
		return set()
	window = near_window(doc, today or _today())
	if not window:
		return set()
	before_kind = (link_before.get("kind") if link_before is not None else "") or ""
	before_live = link_before is not None and link_before.get("status") != "Deleted"
	if is_pencil(item) and (not before_live or before_kind.startswith("Pencil")):
		return set()
	days = set()
	if item.get("state") != payload.STATE_GONE:
		start = payload._date(item.get("start"))
		end = payload._date(item.get("end")) or start
		if start and end and end >= start:
			day = max(start, window[0])
			while day <= min(end, window[1]):
				days.add(day)
				day += datetime.timedelta(days=1)
	if before_live:
		old = payload._date(link_before.get("event_date"))
		if in_window(old, window):
			days.add(old)
	return days
