# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""How many hours does each person have free on each day? The one engine both planners share.

The Project Planner books people onto Tasks by hours; the Maintenance Planner books technicians
onto visits. Nik's rule (2026-10-08) is that the two must agree to the hour about who is free, so
neither keeps its own arithmetic: both call :func:`availability`, which answers for a set of
**Planner Resources** over a date range.

A person's day starts from their **work pattern** (Austin works Monday to Wednesday), then loses
capacity to a company **holiday** (0) and to approved **time off** (0, or half for a half day).
Against that capacity it sets everything that uses their hours:

* **Project tasks**, through the Task's crew table (``custom_crew``), or its open assignments when
  it has no crew rows yet: before this module existed a task's people were its ToDos, and a task
  nobody has re-crewed still has to count.
* **Rental crew tasks**, which are ordinary Tasks carrying ``custom_rental_booking``; the booking
  owns their dates, so they are reported as kind ``rental``.
* **Maintenance visits**: every open or done visit record on its plan date, plus the visits the
  scheduler has not drafted yet, projected exactly as the Maintenance Planner projects them. A
  visit books its technician **and every crew member** (P1.8), each for their own hours: a crew
  row's hours, else the person's whole day on a full-day visit, else the visit's planned hours,
  else Settings' maintenance visit hours (:func:`visit_person_hours`). So a multi-person visit is
  on every one of those people's days, and on every one of their drive routes.
* **Travel Trip** days: the whole day is spoken for.
* **Driving** (Phase 2): each person-day with stops is routed from the shop and back
  (``project_enhancements.routing``), and with Settings ``pad_drive_time`` on the drive time is
  booked as a ``drive`` booking. Every day cell carries ``drive_minutes`` (0 with no route),
  ``drive_source`` (``"google"``/``"estimate"``/``"mixed"``, None with no driving), ``long_drive``
  and ``unlocated`` (stops with no coordinates, which are not driven to). Routing failing never
  fails the answer: it is logged and the day reads without driving.

**Overbooking warns and never blocks.** :func:`day_conflicts` returns sentences; whether to save
anyway is the caller's decision, and the Project Planner asks for a reason when it does.

Things this module is careful about, some of which look like bugs:

* **A task with no estimate books a full day**, the person's pattern hours that day, or the
  Settings' full-day hours when the pattern says they do not work. Booking nothing would make an
  unestimated task invisible to the free-hours count, which is the opposite of useful.
* **A blank crew ``hours`` is 0, not None.** Frappe stores a Float as ``NOT NULL DEFAULT 0``, so
  "blank" and "0" are the same fact by the time a row is read; 0 therefore means "an even share".
* **Time off never exposes its type or reason.** The label is "Time off" or "Half day off" and
  nothing else, because this answer is shown to everyone who can open a planner.
* **Weekly-off holidays are ignored.** A Holiday List carries every Saturday and Sunday as a
  ``weekly_off`` row; the work pattern is what says whether someone works weekends.
* **Nullable dates are filtered in Python.** A ``<=`` filter in ``frappe.get_all`` silently matches
  NULLs, and v16 refuses SQL functions written as field strings, so readers fetch a little more
  than they need and decide in plain Python.
* **Work restrictions are warnings only**, read with the same rules as
  ``hr_enhancements.availability.restrictions_covering`` (dates, not status).
* **A tentative ("pencil") task books softly** (P3.3, ``Task.custom_tentative``). Its bookings
  carry ``"tentative": True`` and land in the day's ``soft_booked``; ``booked``, ``free`` and
  ``conflicts`` count firm work only, so a pencil never asks anyone for a reason. When the pencil
  would push the day over, the cell gets a warning instead ("Pencilled work would put them over
  by 2h"). A pencil is not driven to either: routes and drive padding are worked out from firm
  stops, so pencilled work never books driving hours.

* **Customer jobs only** (Nik, 2026-10-09). A task counts only when its project is a Design,
  Build, Service, Events/Rent or Delivery job, by ``project_type`` or by a value-stream row
  (:data:`PLANNER_PROJECT_TYPES`, :data:`PLANNER_VALUE_STREAMS`); internal projects and tasks
  with no project are left out of every planner surface, because every surface reads
  :func:`read_tasks`. A rental crew task always counts.
* **Vehicles and equipment** (Phase 4, P4.4) are booked on tasks (``Task.custom_equipment``),
  not people: a vehicle or asset is used or not used on a day, by the same span rules. Two firm
  tasks on one vehicle, a vehicle In Shop or Retired, or an asset already out on a submitted
  Asset Booking are conflicts under the ``"Equipment"`` key (:func:`equipment_conflicts`).

The pure functions take plain dicts and dates, so ``tests/test_project_planner.py`` runs them
without a bench.
"""

import datetime
from collections import defaultdict

import frappe

from erpnext_enhancements.project_enhancements import routing

#: Mirrors ``erpnext_enhancements.task_enhancements.doctype.task.task.FINISHED_STATUSES``.
#: Not imported: that module imports ERPNext's Task class, which would drag ERPNext into every
#: caller, including the bench-free tests. ``test_project_planner`` asserts the two literals match.
FINISHED_STATUSES = ("Completed", "Canceled", "Cancelled", "Invoiced", "Template")

#: A task on a project in one of these is not work anyone will do. The site's Project status
#: options (a Property Setter) are Active, Client Hold, Parked, Completed, Invoiced, Paid and
#: Canceled; Invoiced and Paid come after Completed, so a stale open task on one is no booking.
#: Client Hold and Parked stay in: the work is paused, not done, and a PM moving it needs to see it.
CLOSED_PROJECT_STATUSES = ("Completed", "Invoiced", "Paid", "Cancelled", "Canceled")

#: Nik, 2026-10-09: "remove internal projects from the Project Planner. It should only do Design,
#: Build, Service, Events (Rent), Delivery." A task counts only when its project is a customer job:
#: its ``project_type`` is one of these, or it carries a qualifying value-stream row (below). So
#: Internal, Group Projects, Overhead, Other, External, Organizational Projects, a blank type with
#: no qualifying stream, and a task with no project are all left out of every planner surface.
#: The one exception is a rental crew task (``custom_rental_booking``), which always counts, with
#: or without a project: it is Events work by nature.
PLANNER_PROJECT_TYPES = ("Design", "Build", "Service", "Events", "Rent", "Delivery")

#: The value streams (``tabValue Stream`` rows on the Project, either the live ``value_stream``
#: column or the older ``value_streams`` one) that make a project a customer job when its type does
#: not. Products is deliberately absent: Products jobs are ``project_type`` Design, so they are in.
PLANNER_VALUE_STREAMS = ("Design", "Build", "Service", "Events", "Delivery")

#: Bound into every query that uses :func:`planner_job_sql`.
PLANNER_SQL_VALUES = {"planner_types": PLANNER_PROJECT_TYPES, "planner_streams": PLANNER_VALUE_STREAMS}

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

#: Project Planner Settings, with the defaults its JSON declares. Used when the Single is
#: missing or a value reads None; a deliberate 0 is kept. The route settings (Phase 2) matter
#: most here: a Single row saved before they existed has no ``tabSingles`` row for them until
#: the backfill patch runs, so None must read as the default (padding on, Google on).
DEFAULT_SETTINGS = {
	"default_day_hours": 8.0,
	"maintenance_visit_hours": 2.0,
	"rental_delivery_hours": 2.0,
	"rental_setup_hours": 3.0,
	"rental_takedown_hours": 2.0,
	"rental_cleaning_hours": 2.0,
	"pad_drive_time": 1.0,
	"use_google_routes": 1.0,
	"long_drive_minutes": 90.0,
	"day_start_time": "08:00:00",
}

#: Settings that are not numbers (``day_start_time`` is a Time: text, or a timedelta from the
#: database), so get_settings must not run them through ``float``.
TEXT_SETTINGS = ("day_start_time",)

#: ``Task.custom_rental_task_kind`` → the Settings field with that kind's hours.
RENTAL_KIND_HOURS = {
	"Delivery": "rental_delivery_hours",
	"Setup": "rental_setup_hours",
	"Take-down": "rental_takedown_hours",
	"Cleaning": "rental_cleaning_hours",
}

#: How far past the visible range capacity is worked out for. A task that started before the
#: range spreads its estimate over every working day it covers, so those days need a capacity
#: too; beyond this the pattern alone is used.
CAPACITY_MARGIN_DAYS = 62

TOLERANCE = 0.01

#: The booking kinds that are Tasks (as opposed to visits, travel and driving).
TASK_KINDS = ("task", "rental")

#: Phase 4 (P4.4): vehicles and equipment booked on Tasks (``Task.custom_equipment``, child doctype
#: Task Equipment). Their conflicts are filed under this needs_reason key; the others are people.
EQUIPMENT_KEY = "Equipment"
EQUIPMENT_TYPES = ("Vehicle", "Asset")
#: A Fleet Vehicle in one of these cannot go out, so booking it is a conflict.
UNAVAILABLE_VEHICLE_STATUSES = ("In Shop", "Retired")


# ---------------------------------------------------------------------- pure helpers


def _as_date(value):
	if not value:
		return None
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	text = str(value).strip()
	return datetime.date.fromisoformat(text[:10]) if text else None


def _as_datetime(value):
	if not value:
		return None
	if isinstance(value, datetime.datetime):
		return value
	if isinstance(value, datetime.date):
		return datetime.datetime.combine(value, datetime.time())
	text = str(value).strip()
	if not text:
		return None
	try:
		return datetime.datetime.fromisoformat(text)
	except ValueError:
		return datetime.datetime.fromisoformat(text[:19])


def _float(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def is_tentative(task):
	"""True when the task is a pencil booking (``custom_tentative``, a Check: 1/0/None/"1")."""
	return bool(_float((task or {}).get("custom_tentative")))


def is_planner_job(row):
	"""True when a task row belongs on the planners (see :data:`PLANNER_PROJECT_TYPES`).

	``row`` carries ``project``, ``custom_rental_booking`` and, from :func:`planner_job_sql`,
	``project_type`` and ``planner_stream`` (1 when the project has a qualifying value stream).
	A rental crew task always counts; a task with no project never does.
	"""
	row = row or {}
	if row.get("custom_rental_booking"):
		return True
	if not row.get("project"):
		return False
	return (row.get("project_type") or "") in PLANNER_PROJECT_TYPES or bool(_float(row.get("planner_stream")))


def daterange(start, end):
	"""Every date from ``start`` to ``end`` inclusive."""
	start, end = _as_date(start), _as_date(end)
	if not start or not end:
		return []
	return [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)]


def fmt_hours(hours):
	"""``2`` → ``"2"``, ``2.5`` → ``"2.5"``, ``1.25`` → ``"1.25"``."""
	value = round(_float(hours), 2)
	if value == int(value):
		return str(int(value))
	return f"{value:.2f}".rstrip("0").rstrip(".")


def pattern_hours(patterns, day, default_hours=8.0):
	"""The hours a work pattern gives ``day``.

	``patterns`` are rows with ``effective_from``/``effective_to`` (either may be blank, an open
	end) and ``monday``..``sunday``. Of the rows whose range covers the day, a row **with dates**
	wins over an undated one, and among dated rows the latest ``effective_from`` wins: a summer
	schedule entered on top of a standing pattern is the point of the dates. With no covering row
	the day is ``default_hours`` Monday to Friday and 0 at the weekend.
	"""
	day = _as_date(day)
	covering = []
	for index, row in enumerate(patterns or []):
		starts, ends = _as_date(row.get("effective_from")), _as_date(row.get("effective_to"))
		if (starts and day < starts) or (ends and day > ends):
			continue
		covering.append((starts, ends, index, row))
	dated = [entry for entry in covering if entry[0] or entry[1]]
	if dated:
		# Latest start wins; a tie goes to the row with an end (the narrower one), then the later row.
		row = max(dated, key=lambda e: (e[0] or datetime.date.min, 1 if e[1] else 0, e[2]))[3]
	elif covering:
		row = covering[0][3]
	else:
		return float(default_hours) if day.weekday() < 5 else 0.0
	return max(_float(row.get(WEEKDAYS[day.weekday()])), 0.0)


def day_capacity(base_hours, holiday=None, time_off=None):
	"""``(capacity, off)`` for one person on one day.

	``base_hours`` comes from :func:`pattern_hours`. ``holiday`` is ``{"description", "half_day"}``
	or None; ``time_off`` is ``"full"``, ``"half"`` or None. ``off`` is the label a planner shows:
	``"Not a work day"``, ``"Holiday: <name>"``, ``"Time off"``, ``"Half day off"``, or None. It
	never names the kind of time off or its reason.
	"""
	capacity = max(_float(base_hours), 0.0)
	off = None if capacity > 0 else "Not a work day"
	if holiday:
		name = (holiday.get("description") or "").strip()
		if holiday.get("half_day"):
			if capacity > 0:
				capacity *= 0.5
				off = "Holiday (half day): " + name if name else "Holiday (half day)"
		else:
			capacity = 0.0
			off = "Holiday: " + name if name else "Holiday"
	if time_off and capacity > 0:
		if time_off == "half":
			capacity *= 0.5
			off = off or "Half day off"
		else:
			capacity = 0.0
			off = "Time off"
	return round(capacity, 2), off


def task_slot(task):
	"""``(start, end)`` datetimes when the task is a same-day time slot, else None."""
	start = _as_datetime(task.get("custom_start_datetime"))
	end = _as_datetime(task.get("custom_end_datetime"))
	if start and end and start.date() == end.date() and end > start:
		return start, end
	return None


def task_span(task):
	"""``(first_day, last_day)`` the task covers, or None when it is undated.

	From ``exp_start_date``/``exp_end_date`` (Datetime fields on v16; either alone is one day).
	When both custom datetimes are set on the same date, that date: the slot is the most precise
	thing the task says about when it happens.
	"""
	start = _as_datetime(task.get("custom_start_datetime"))
	end = _as_datetime(task.get("custom_end_datetime"))
	if start and end and start.date() == end.date():
		return start.date(), start.date()
	first, last = _as_date(task.get("exp_start_date")), _as_date(task.get("exp_end_date"))
	if first and last:
		return (first, last) if last >= first else (first, first)
	if first or last:
		return (first or last), (first or last)
	return None


def span_for_estimate(first_day, hours, people=1, day_hours=8.0):
	"""``(first_day, last_day)`` long enough for ``hours`` of work by ``people`` at ``day_hours``.

	For a task scheduled from nowhere (the Unscheduled tray): it has no length of its own, and
	dropping a 40-hour job onto one day would only produce an "Over by 32h" the PM then has to
	explain. Days after the first skip Saturday and Sunday, the usual week; the engine still checks
	each person's real pattern once the task is placed. No estimate is one day.
	"""
	first_day = _as_date(first_day)
	per_day = _float(day_hours) * max(int(people or 1), 1)
	hours = _float(hours)
	days = max(int(-(-hours // per_day)), 1) if (hours > 0 and per_day > 0) else 1
	last = first_day
	while days > 1:
		last += datetime.timedelta(days=1)
		if last.weekday() < 5:
			days -= 1
	return first_day, last


def _explicit_hours(member):
	# Task Crew Member.hours is a Float: a blank cell is stored and read back as 0.0, never None.
	# So 0 / 0.0 / None all mean "unset" (an even share of expected_time, or a full day); only a
	# positive value is explicit hours.
	hours = member.get("hours")
	return _float(hours) if hours is not None and _float(hours) > 0 else None


def allocate_task(task, crew, capacity_of, settings):
	"""Who the task books, on which days, for how many hours.

	Args:
		task: a Task row (dates, ``expected_time``, slot datetimes, ``custom_rental_task_kind``).
		crew: ``[{"resource", "hours"}]``; ``hours`` blank or 0 means "an even share".
		capacity_of: ``(resource, date) -> float``.
		settings: :data:`DEFAULT_SETTINGS`-shaped.

	Returns:
		``[(resource, date, hours, estimated)]``; ``estimated`` is True when the hours came from a
		default because the task carries no estimate. A member's days are the task's days on which
		they have capacity; when there are none, all of them, so the booking lands on a day off and
		:func:`day_conflicts` says so rather than the hours vanishing.
	"""
	span = task_span(task)
	if not span or not crew:
		return []
	days = daterange(*span)
	settings = settings or DEFAULT_SETTINGS
	members, seen = [], set()
	for member in crew:
		resource = member.get("resource")
		if resource and resource not in seen:
			seen.add(resource)
			members.append(member)

	def member_days(resource):
		working = [day for day in days if _float(capacity_of(resource, day)) > 0]
		return working or list(days)

	slot = task_slot(task)
	expected = _float(task.get("expected_time"))
	explicit_total = sum(_explicit_hours(m) or 0 for m in members)
	sharing = [m for m in members if _explicit_hours(m) is None]
	share = max(expected - explicit_total, 0.0) / len(sharing) if (expected > 0 and sharing) else 0.0
	kind = task.get("custom_rental_task_kind")
	rental_hours = None
	if kind in RENTAL_KIND_HOURS:
		value = settings.get(RENTAL_KIND_HOURS[kind])
		rental_hours = _float(value if value is not None else DEFAULT_SETTINGS[RENTAL_KIND_HOURS[kind]])
	day_hours = settings.get("default_day_hours")
	day_hours = _float(day_hours if day_hours is not None else DEFAULT_SETTINGS["default_day_hours"])

	out = []
	for member in members:
		resource = member["resource"]
		explicit = _explicit_hours(member)
		if slot:
			hours = explicit if explicit is not None else (slot[1] - slot[0]).total_seconds() / 3600
			out.append((resource, slot[0].date(), round(hours, 2), False))
			continue
		mine = member_days(resource)
		if explicit is not None:
			out.extend((resource, day, round(explicit / len(mine), 2), False) for day in mine)
		elif expected > 0:
			if share > 0:
				out.extend((resource, day, round(share / len(mine), 2), False) for day in mine)
		elif rental_hours is not None:
			out.extend((resource, day, round(rental_hours, 2), True) for day in mine)
		else:
			for day in mine:
				capacity = _float(capacity_of(resource, day))
				out.append((resource, day, round(capacity if capacity > 0 else day_hours, 2), True))
	return [entry for entry in out if entry[2] > 0]


def day_conflicts(capacity, off, bookings):
	"""What is wrong with one person's day, as sentences.

	* Over capacity (beyond a 0.01h tolerance): ``"Over by 2h"``.
	* Two time slots that overlap: ``"Double-booked 09:00–11:00 (TASK-1, TASK-2)"``.
	* Anything booked on a day with no capacity: ``"Booked on a day off (Time off)"``. A half day
	  off still has capacity, so it only conflicts once it is over. A zero-hour booking does not
	  count: a trip across a weekend books nothing on the Saturday, and showing every such trip
	  red would teach people to ignore the color.

	Tentative (pencil) bookings are left out entirely: a pencil never conflicts, it only warns
	(:func:`pencil_warning`). Doing it here rather than in each caller means the Maintenance
	Planner's own conflict check, which calls this with a whole day's bookings, agrees.
	"""
	bookings = [b for b in bookings or [] if not b.get("tentative")]
	capacity = _float(capacity)
	out = []
	if capacity <= 0 and any(_float(b.get("hours")) > 0 for b in bookings):
		out.append(f"Booked on a day off ({off})" if off else "Booked on a day off")
	else:
		booked = sum(_float(b.get("hours")) for b in bookings)
		if booked - capacity > TOLERANCE:
			out.append(f"Over by {fmt_hours(booked - capacity)}h")
	slotted = sorted((b for b in bookings if b.get("slot")), key=lambda b: tuple(b["slot"]))
	for i, first in enumerate(slotted):
		for second in slotted[i + 1 :]:
			if second["slot"][0] >= first["slot"][1]:
				break
			start = max(first["slot"][0], second["slot"][0])
			end = min(first["slot"][1], second["slot"][1])
			out.append(f"Double-booked {start}–{end} ({first.get('ref')}, {second.get('ref')})")
	return out


def pencil_warning(capacity, bookings):
	"""``"Pencilled work would put them over by 2h"``, or None.

	Firm and tentative hours together against capacity, reported only when the day has pencilled
	work and the two together exceed it. The figure is the whole overrun, firm part included.
	"""
	bookings = bookings or []
	soft = sum(_float(b.get("hours")) for b in bookings if b.get("tentative"))
	if soft <= TOLERANCE:
		return None
	firm = sum(_float(b.get("hours")) for b in bookings if not b.get("tentative"))
	over = firm + soft - _float(capacity)
	if over <= TOLERANCE:
		return None
	return f"Pencilled work would put them over by {fmt_hours(over)}h"


def resolve_crew(rows, todo_users, user_to_resource):
	"""A task's crew: its crew rows, else its open assignees who are Planner Resources.

	Returns ``[{"resource", "hours", "is_lead", "label"}]``, one entry per resource. ``hours`` is
	None for a blank (0) row. Assignees who are not resources are left out; the caller decides
	whether an assignee with no resource still counts as somebody on the job.
	"""
	out, seen = [], set()
	if rows:
		for row in rows:
			resource = row.get("resource")
			if not resource or resource in seen:
				continue
			seen.add(resource)
			out.append(
				{
					"resource": resource,
					"hours": _explicit_hours(row),
					"is_lead": bool(row.get("is_lead")),
					"label": row.get("resource_name") or None,
				}
			)
		return out
	for user in todo_users or []:
		resource = (user_to_resource or {}).get(user)
		if resource and resource not in seen:
			seen.add(resource)
			out.append({"resource": resource, "hours": None, "is_lead": False, "label": None})
	return out


def equipment_ref(row):
	"""``(type, name)`` for a Task Equipment row (``equipment_type`` + ``vehicle``/``asset``), or
	for an already-normalized ``{"type", "name"}`` entry; None when it names nothing."""
	row = row or {}
	if row.get("type") and row.get("name") and not row.get("equipment_type"):
		kind, name = row.get("type"), row.get("name")
	else:
		kind = row.get("equipment_type") or (
			"Vehicle" if row.get("vehicle") else ("Asset" if row.get("asset") else None)
		)
		name = row.get("vehicle") if kind == "Vehicle" else (row.get("asset") if kind == "Asset" else None)
	if kind not in EQUIPMENT_TYPES or not name:
		return None
	return kind, name


def task_equipment(rows, labels=None):
	"""A task's equipment as ``[{"type", "name", "label"}]``, one entry per vehicle/asset.

	``labels`` maps ``(type, name)`` to a display name; else the row's own ``label``, else the name
	(a Fleet Vehicle is named by its vehicle name, so that is already readable).
	"""
	out, seen = [], set()
	for row in rows or []:
		ref = equipment_ref(row)
		if not ref or ref in seen:
			continue
		seen.add(ref)
		label = (labels or {}).get(ref) or (row or {}).get("label") or ref[1]
		out.append({"type": ref[0], "name": ref[1], "label": label})
	return out


def _booking_days(booking):
	"""``(first, last)`` dates of an Asset Booking, or None when it has no start."""
	first = _as_date(booking.get("from_datetime"))
	if not first:
		return None
	last = _as_date(booking.get("to_datetime")) or first
	return (first, last) if last >= first else (first, first)


def equipment_conflicts(tasks, equipment, start, end, statuses=None, bookings=None):
	"""``{task: [sentence, ...]}``: what is wrong with each firm task's vehicles and assets.

	``tasks`` are Task rows; ``equipment`` maps a task name to :func:`task_equipment` entries.
	Only days from ``start`` to ``end`` are judged, and a vehicle or asset is simply used or not
	used on a day, by the same span rules as people (:func:`task_span`). Three findings:

	* another **firm** task uses it on one of the task's days: ``"2026-10-12: Truck 2 is also on
	  TASK-9 (Dig)"``, once per other task, on the first day they share;
	* a Fleet Vehicle whose ``statuses`` entry is In Shop or Retired: ``"Truck 2 is In Shop"``;
	* an Asset with a submitted Asset Booking (``bookings``: rows with ``asset``, ``name``,
	  ``from_datetime``, ``to_datetime``, ``booking_type``, ``project``) overlapping a task day:
	  ``"2026-10-12: Fountain A is booked on Asset Booking AB-1 (Rental)"``. A booking for the
	  task's own project is the job it is booked for, not a clash, and is not reported.

	Tentative tasks are left out on both sides, as for people: a pencil never needs a reason and
	never makes a firm task need one.
	"""
	start, end = _as_date(start), _as_date(end)
	firm = [t for t in tasks or [] if not is_tentative(t) and (equipment or {}).get(t.get("name"))]
	usage = defaultdict(lambda: defaultdict(list))
	days_of = {}
	for task in firm:
		span = task_span(task)
		if not span:
			continue
		days = [d for d in daterange(*span) if start <= d <= end]
		days_of[task.get("name")] = days
		for entry in equipment[task.get("name")]:
			for day in days:
				usage[(entry["type"], entry["name"])][day].append(task)
	by_asset = defaultdict(list)
	for booking in bookings or []:
		days = _booking_days(booking)
		if booking.get("asset") and days:
			by_asset[booking["asset"]].append((booking, days))

	out = {}
	for task in firm:
		name = task.get("name")
		days = days_of.get(name) or []
		if not days:
			continue
		lines = []
		for entry in equipment[name]:
			ref = (entry["type"], entry["name"])
			label = entry.get("label") or entry["name"]
			status = (statuses or {}).get(entry["name"]) if entry["type"] == "Vehicle" else None
			if status in UNAVAILABLE_VEHICLE_STATUSES:
				lines.append(f"{label} is {status}")
			reported = set()
			for day in days:
				for other in usage[ref].get(day) or []:
					if other.get("name") == name or other.get("name") in reported:
						continue
					reported.add(other.get("name"))
					lines.append(
						f"{day}: {label} is also on {other.get('name')} ({other.get('subject') or other.get('name')})"
					)
			if entry["type"] == "Asset":
				for booking, (first, last) in by_asset.get(entry["name"]) or []:
					if task.get("project") and booking.get("project") == task.get("project"):
						continue
					shared = [d for d in days if first <= d <= last]
					if shared:
						kind = f" ({booking.get('booking_type')})" if booking.get("booking_type") else ""
						lines.append(
							f"{shared[0]}: {label} is booked on Asset Booking {booking.get('name')}{kind}"
						)
		if lines:
			out[name] = lines
	return out


def equipment_board(tasks, equipment, start, end, statuses=None, bookings=None):
	"""``{(type, name): {date: {"tasks": [...], "bookings": [...]}}}``: each vehicle/asset's days.

	Every task counts here, pencils included (marked ``tentative``), because the board shows what
	is planned; :func:`equipment_conflicts` decides what clashes.
	"""
	start, end = _as_date(start), _as_date(end)
	board = defaultdict(lambda: defaultdict(lambda: {"tasks": [], "bookings": []}))
	for task in tasks or []:
		span = task_span(task)
		if not span:
			continue
		for entry in (equipment or {}).get(task.get("name")) or []:
			for day in daterange(*span):
				if start <= day <= end:
					board[(entry["type"], entry["name"])][day]["tasks"].append(
						{
							"task": task.get("name"),
							"subject": task.get("subject") or task.get("name"),
							"project": task.get("project"),
							"tentative": is_tentative(task),
						}
					)
	for booking in bookings or []:
		days = _booking_days(booking)
		if not booking.get("asset") or not days:
			continue
		for day in daterange(max(days[0], start), min(days[1], end)):
			board[("Asset", booking["asset"])][day]["bookings"].append(
				{
					"name": booking.get("name"),
					"booking_type": booking.get("booking_type"),
					"project": booking.get("project"),
				}
			)
	return board


def _slot_text(slot):
	return [slot[0].strftime("%H:%M"), slot[1].strftime("%H:%M")] if slot else None


def _range_overlaps(first, last, start, end):
	return bool(first and last and first <= end and last >= start)


# ---------------------------------------------------------------------- readers


def get_settings():
	"""Project Planner Settings as a plain dict.

	Read through ``get_cached_doc``, never ``db.get_single_value``: a Single nobody has saved
	has no ``tabSingles`` rows, so ``get_single_value`` returns None, while ``load_from_db`` hands
	back the JSON defaults. None still falls back here defensively, but a deliberate 0 stays 0.
	"""
	out = dict(DEFAULT_SETTINGS)
	try:
		doc = frappe.get_cached_doc("Project Planner Settings")
	except Exception:
		return out
	for key, default in DEFAULT_SETTINGS.items():
		value = doc.get(key)
		if key in TEXT_SETTINGS:
			out[key] = str(value) if value not in (None, "") else default
		else:
			out[key] = _float(value) if value is not None else default
	return out


def _read_resources(names=None):
	"""Active Planner Resources (optionally only ``names``) and their pattern rows."""
	filters = {"is_active": 1}
	if names is not None:
		names = [n for n in dict.fromkeys(names) if n]
		if not names:
			return [], {}
		filters["name"] = ["in", names]
	people = frappe.get_all(
		"Planner Resource",
		filters=filters,
		fields=[
			"name",
			"resource_name",
			"resource_type",
			"employee",
			"user",
			"supplier",
			"resource_group",
			"home_team",
			"color",
		],
		order_by="resource_name asc",
		limit_page_length=0,
	)
	patterns = defaultdict(list)
	if people:
		for row in frappe.get_all(
			"Planner Resource Work Pattern",
			filters={"parenttype": "Planner Resource", "parent": ["in", [p.get("name") for p in people]]},
			fields=["parent", "idx", "effective_from", "effective_to", *WEEKDAYS],
			order_by="idx asc",
			limit_page_length=0,
		):
			patterns[row.get("parent")].append(row)
	return people, patterns


def _default_holiday_list():
	company = None
	try:
		company = frappe.defaults.get_user_default("Company")
	except Exception:
		company = None
	if not company:
		first = frappe.get_all("Company", pluck="name", limit_page_length=1, order_by="creation asc")
		company = first[0] if first else None
	if not company:
		return None
	return frappe.db.get_value("Company", company, "default_holiday_list")


def _read_holidays(people, start, end):
	"""``{resource: {date: {"description", "half_day"}}}`` from each employee's Holiday List.

	An employee without one uses the default company's. Subcontractors have no holidays: we do
	not know their calendar, and assuming ours would hide days they can work.
	"""
	from frappe.utils import strip_html_tags

	staff = [p for p in people if p.get("resource_type") != "Subcontractor"]
	if not staff:
		return {}
	employees = [p.get("employee") for p in staff if p.get("employee")]
	own = (
		dict(
			frappe.get_all(
				"Employee",
				filters={"name": ["in", employees]},
				fields=["name", "holiday_list"],
				as_list=True,
				limit_page_length=0,
			)
		)
		if employees
		else {}
	)
	fallback = _default_holiday_list()
	list_of = {p.get("name"): own.get(p.get("employee")) or fallback for p in staff}
	lists = {name for name in list_of.values() if name}
	if not lists:
		return {}

	fields = ["parent", "holiday_date", "description", "weekly_off"]
	try:
		if frappe.get_meta("Holiday").has_field("is_half_day"):
			fields.append("is_half_day")
	except Exception:
		pass
	by_list = defaultdict(dict)
	for row in frappe.get_all(
		"Holiday",
		filters={
			"parenttype": "Holiday List",
			"parent": ["in", sorted(lists)],
			"holiday_date": ["between", [start, end]],
		},
		fields=fields,
		limit_page_length=0,
	):
		day = _as_date(row.get("holiday_date"))
		if not day or not (start <= day <= end) or int(row.get("weekly_off") or 0) == 1:
			continue
		by_list[row.get("parent")][day] = {
			"description": strip_html_tags(str(row.get("description") or "")).strip(),
			"half_day": bool(row.get("is_half_day")),
		}
	return {resource: by_list.get(name, {}) for resource, name in list_of.items() if name}


def _read_time_off(people, start, end):
	"""``{resource: {date: "full" | "half"}}`` from Approved Time Off Requests.

	Matched by user, falling back to employee. Only dates are read: the type and the reason are
	nobody's business on a planner.
	"""
	if not frappe.db.exists("DocType", "Time Off Request"):
		return {}
	by_user = {p.get("user"): p.get("name") for p in people if p.get("user")}
	by_employee = {p.get("employee"): p.get("name") for p in people if p.get("employee")}
	if not by_user and not by_employee:
		return {}
	rows = frappe.get_all(
		"Time Off Request",
		filters={"status": "Approved", "from_date": ["<=", end]},
		or_filters={
			"user": ["in", list(by_user) or ["__none__"]],
			"employee": ["in", list(by_employee) or ["__none__"]],
		},
		fields=["user", "employee", "from_date", "to_date", "half_day"],
		limit_page_length=0,
	)
	out = defaultdict(dict)
	for row in rows:
		resource = by_user.get(row.get("user")) or by_employee.get(row.get("employee"))
		first = _as_date(row.get("from_date"))
		if not resource or not first:
			continue
		last = _as_date(row.get("to_date")) or first
		if not _range_overlaps(first, last, start, end):
			continue
		kind = "half" if row.get("half_day") else "full"
		for day in daterange(max(first, start), min(last, end)):
			if out[resource].get(day) != "full":
				out[resource][day] = kind
	return dict(out)


def _read_restrictions(people, start, end):
	"""``{resource: {date: [warning, ...]}}`` for restricted duty in force.

	One query for the range, then ``restrictions_covering``'s rules per day: dates are the fact,
	status only a summary; a row that stopped (``ended_on``/``canceled_on``) on or before a day
	does not apply to it; a row marked Ended or Canceled with no date cannot be placed and is left
	out, as ``_restrictions`` leaves out the unknowns.
	"""
	restriction = "Work Restriction"
	by_user = {p.get("user"): p.get("name") for p in people if p.get("user")}
	if not by_user or not frappe.db.exists("DocType", restriction):
		return {}
	dated = frappe.get_meta(restriction).has_field("ended_on")
	fields = ["name", "user", "from_date", "to_date", "status"] + (
		["ended_on", "canceled_on"] if dated else []
	)
	out = defaultdict(lambda: defaultdict(list))
	for row in frappe.get_all(
		restriction,
		filters={"user": ["in", list(by_user)], "from_date": ["<=", end]},
		fields=fields,
		limit_page_length=0,
	):
		# A `<=` filter matches a NULL from_date, as restrictions_covering's does: open start.
		first = _as_date(row.get("from_date")) or start
		last = _as_date(row.get("to_date")) or end
		if first > end or last < start:
			continue
		stopped = _as_date(row.get("ended_on") or row.get("canceled_on")) if dated else None
		if not stopped and row.get("status") in ("Ended", "Canceled"):
			continue
		try:
			summary = frappe.get_cached_doc(restriction, row.get("name")).summary()
		except Exception:
			summary = ""
		sentence = f"On restricted duty: {summary}" if summary else "On restricted duty"
		for day in daterange(max(first, start), min(last, end)):
			if stopped and stopped <= day:
				break
			out[by_user[row.get("user")]][day].append(sentence)
	return {resource: dict(days) for resource, days in out.items()}


def _task_columns():
	"""``{column: SQL expression}`` for the optional Task columns.

	A site part-way through a migrate (or one where a fixture has not landed) still answers: a
	missing column reads as NULL instead of failing the whole planner.
	"""
	optional = (
		"custom_start_datetime",
		"custom_end_datetime",
		"custom_rental_booking",
		"custom_rental_task_kind",
		"custom_crew_size",
		"custom_tentative",
	)
	out = {}
	for column in optional:
		try:
			present = frappe.db.has_column("Task", column)
		except Exception:
			present = False
		out[column] = f"t.`{column}`" if present else "NULL"
	return out


def _stream_columns():
	"""The ``tabValue Stream`` columns that exist: ``value_stream`` (live) and ``value_streams``
	(older rows). A site part-way through a migrate answers without the missing one."""
	out = []
	for column in ("value_stream", "value_streams"):
		try:
			present = frappe.db.has_column("Value Stream", column)
		except Exception:
			present = False
		if present:
			out.append(column)
	return out


def planner_job_sql(project="p"):
	"""``(type_expr, stream_expr)``: SQL for "is the project aliased ``project`` a customer job".

	``type_expr`` is the project's type ('' when blank); ``stream_expr`` is 1 when the project has
	a value-stream row in :data:`PLANNER_VALUE_STREAMS` (either column), else 0. A query using
	them binds :data:`PLANNER_SQL_VALUES`. The condition itself is :func:`planner_job_condition`;
	Python re-checks every row with :func:`is_planner_job`, which is the definition.
	"""
	columns = _stream_columns()
	if columns:
		matches = " OR ".join(f"vs.`{column}` IN %(planner_streams)s" for column in columns)
		stream = (
			"EXISTS (SELECT 1 FROM `tabValue Stream` vs WHERE vs.parenttype = 'Project' "
			f"AND vs.parent = {project}.name AND ({matches}))"
		)
	else:
		stream = "0"
	return f"IFNULL({project}.project_type, '')", stream


def planner_job_condition(project="p", rental=None):
	"""The WHERE condition for :func:`is_planner_job`: a customer-job project, or (with ``rental``,
	the SQL for the task's ``custom_rental_booking``) a rental crew task."""
	type_expr, stream = planner_job_sql(project)
	job = f"({project}.name IS NOT NULL AND ({type_expr} IN %(planner_types)s OR {stream}))"
	if rental and rental != "NULL":
		return f"(IFNULL({rental}, '') <> '' OR {job})"
	return job


def planner_projects(names):
	"""The subset of the Project ``names`` that are customer jobs (:data:`PLANNER_PROJECT_TYPES`)."""
	names = sorted({n for n in names or () if n})
	if not names:
		return set()
	type_expr, stream = planner_job_sql("proj")
	rows = frappe.db.sql(
		f"""
		SELECT proj.name, {type_expr} AS project_type, {stream} AS planner_stream
		FROM `tabProject` proj
		WHERE proj.name IN %(names)s
		""",
		{"names": tuple(names), **PLANNER_SQL_VALUES},
		as_dict=True,
	)
	return {
		row.get("name")
		for row in rows or []
		if is_planner_job({"project": row.get("name"), **row}) and row.get("name") in names
	}


def read_tasks(start, end):
	"""Open, non-group, non-template Tasks whose span overlaps ``start``..``end``, on customer jobs.

	Raw SQL after the planners' role gate, for the COALESCE over nullable dates (Frappe's ``<=``
	filter matches NULLs). Re-checked against :func:`task_span` and :func:`is_planner_job` in
	Python, which are the definitions. Internal projects are left out (Nik, 2026-10-09: see
	:data:`PLANNER_PROJECT_TYPES`); a rental crew task always counts.
	"""
	cols = _task_columns()
	selected = ", ".join(f"{expr} AS `{column}`" for column, expr in cols.items())
	slot_start, slot_end = cols["custom_start_datetime"], cols["custom_end_datetime"]
	type_expr, stream = planner_job_sql("p")
	job = planner_job_condition("p", cols["custom_rental_booking"])
	rows = frappe.db.sql(
		f"""
		SELECT
			t.name, t.subject, t.project, t.status, t.exp_start_date, t.exp_end_date,
			t.expected_time, t.color, t.modified, {selected},
			p.project_name AS project_title, p.status AS project_status,
			{type_expr} AS project_type, {stream} AS planner_stream
		FROM `tabTask` t
		LEFT JOIN `tabProject` p ON p.name = t.project
		WHERE IFNULL(t.is_group, 0) = 0
			AND IFNULL(t.is_template, 0) = 0
			AND IFNULL(t.status, '') NOT IN %(finished)s
			AND (IFNULL(t.project, '') = '' OR IFNULL(p.status, '') NOT IN %(closed)s)
			AND {job}
			AND (
				(
					COALESCE(DATE(t.exp_start_date), DATE(t.exp_end_date), DATE({slot_start})) <= %(end)s
					AND COALESCE(DATE(t.exp_end_date), DATE(t.exp_start_date), DATE({slot_end}), DATE({slot_start})) >= %(start)s
				)
				OR DATE({slot_start}) BETWEEN %(start)s AND %(end)s
			)
		ORDER BY t.exp_start_date, t.name
		LIMIT 3000
		""",
		{
			"start": start,
			"end": end,
			"finished": FINISHED_STATUSES,
			"closed": CLOSED_PROJECT_STATUSES,
			**PLANNER_SQL_VALUES,
		},
		as_dict=True,
	)
	return [
		row
		for row in rows
		if _task_is_live(row) and _range_overlaps(*(task_span(row) or (None, None)), start, end)
	]


def _task_is_live(row):
	if row.get("status") in FINISHED_STATUSES or row.get("is_group") or row.get("is_template"):
		return False
	if row.get("project") and row.get("project_status") in CLOSED_PROJECT_STATUSES:
		return False
	return is_planner_job(row)


def read_crews(task_names):
	"""``(crew_rows, todo_users)``: each task's Task Crew Member rows and its open assignees."""
	rows, todos = defaultdict(list), defaultdict(list)
	names = [n for n in dict.fromkeys(task_names or []) if n]
	if not names:
		return rows, todos
	for row in frappe.get_all(
		"Task Crew Member",
		filters={"parenttype": "Task", "parent": ["in", names]},
		fields=["parent", "idx", "resource", "resource_name", "user", "hours", "is_lead"],
		order_by="idx asc",
		limit_page_length=0,
	):
		rows[row.get("parent")].append(row)
	for row in frappe.get_all(
		"ToDo",
		filters={"reference_type": "Task", "reference_name": ["in", names], "status": "Open"},
		fields=["reference_name", "allocated_to"],
		limit_page_length=0,
	):
		if row.get("allocated_to") and row.get("allocated_to") not in todos[row.get("reference_name")]:
			todos[row.get("reference_name")].append(row.get("allocated_to"))
	return rows, todos


EQUIPMENT_DOCTYPE = "Task Equipment"


def _equipment_installed():
	try:
		return bool(frappe.db.exists("DocType", EQUIPMENT_DOCTYPE))
	except Exception:
		return False


def read_equipment(task_names):
	"""``{task: [{"type", "name", "label"}]}`` from the tasks' Task Equipment rows (P4.4).

	Empty on a site where the doctype has not landed yet, so the planners answer as before.
	"""
	names = [n for n in dict.fromkeys(task_names or []) if n]
	if not names or not _equipment_installed():
		return {}
	rows = defaultdict(list)
	for row in frappe.get_all(
		EQUIPMENT_DOCTYPE,
		filters={"parenttype": "Task", "parent": ["in", names]},
		fields=["parent", "idx", "equipment_type", "vehicle", "asset", "label"],
		order_by="idx asc",
		limit_page_length=0,
	):
		rows[row.get("parent")].append(row)
	return {name: task_equipment(found) for name, found in rows.items() if task_equipment(found)}


def read_equipment_details(refs):
	"""``(labels, statuses)`` for ``(type, name)`` refs: ``{ref: label}`` (an Asset's
	``asset_name``; a Fleet Vehicle is named by its vehicle name) and ``{vehicle: status}``."""
	vehicles = sorted({name for kind, name in refs or () if kind == "Vehicle"})
	assets = sorted({name for kind, name in refs or () if kind == "Asset"})
	labels, statuses = {}, {}
	if vehicles:
		for row in frappe.get_all(
			"Fleet Vehicle",
			filters={"name": ["in", vehicles]},
			fields=["name", "vehicle_name", "status"],
			limit_page_length=0,
		):
			labels[("Vehicle", row.get("name"))] = row.get("vehicle_name") or row.get("name")
			statuses[row.get("name")] = row.get("status") or None
	if assets:
		for row in frappe.get_all(
			"Asset", filters={"name": ["in", assets]}, fields=["name", "asset_name"], limit_page_length=0
		):
			labels[("Asset", row.get("name"))] = row.get("asset_name") or row.get("name")
	return labels, statuses


def read_asset_bookings(assets, start, end):
	"""Submitted Asset Bookings (asset_management) of ``assets`` overlapping ``start``..``end``.

	Fetched by asset and docstatus only; the dates are nullable Datetimes, so the overlap is
	decided in Python (a ``<=`` filter would match a blank end).
	"""
	assets = sorted({a for a in assets or () if a})
	if not assets:
		return []
	try:
		if not frappe.db.exists("DocType", "Asset Booking"):
			return []
	except Exception:
		return []
	start, end = _as_date(start), _as_date(end)
	out = []
	for row in frappe.get_all(
		"Asset Booking",
		filters={"docstatus": 1, "asset": ["in", assets]},
		fields=["name", "asset", "from_datetime", "to_datetime", "booking_type", "project"],
		limit_page_length=0,
	):
		days = _booking_days(row)
		if days and _range_overlaps(days[0], days[1], start, end):
			out.append(row)
	return out


def _equipment_state(tasks, start, end, overrides=None):
	"""``{"equipment", "conflicts", "labels", "statuses", "bookings"}`` for ``tasks``.

	``overrides`` maps a task name to equipment entries that replace the stored ones (the
	hypothetical tasks of a preview). One read each for the rows, the vehicles and assets, and the
	asset bookings.
	"""
	names = [t.get("name") for t in tasks if t.get("name") not in (overrides or {})]
	equipment = dict(read_equipment(names))
	equipment.update({name: list(rows) for name, rows in (overrides or {}).items()})
	equipment = {name: rows for name, rows in equipment.items() if rows}
	refs = {(e["type"], e["name"]) for rows in equipment.values() for e in rows}
	labels, statuses = read_equipment_details(refs) if refs else ({}, {})
	for name, rows in equipment.items():
		equipment[name] = [
			dict(e, label=labels.get((e["type"], e["name"])) or e.get("label") or e["name"]) for e in rows
		]
	bookings = read_asset_bookings([n for kind, n in refs if kind == "Asset"], start, end) if refs else []
	return {
		"equipment": equipment,
		"conflicts": equipment_conflicts(tasks, equipment, start, end, statuses, bookings),
		"labels": labels,
		"statuses": statuses,
		"bookings": bookings,
	}


def visit_person_hours(person_hours, full_day, planned_hours, default_hours):
	"""``(hours, estimated)`` one person books for a visit (P1.8). ``hours`` None: their full day.

	The rule, in order: the person's own crew-row ``hours`` when set (> 0); else, on a full-day
	visit, that person's whole day, which the caller works out from their capacity the way an
	unestimated task is (:func:`full_day_hours`); else the visit's ``planned_hours`` when set;
	else Project Planner Settings' ``maintenance_visit_hours``. A clocked visit books its clocked
	length for the technician instead, and the caller handles that before asking here.
	``estimated`` is True when the hours are a default rather than something somebody entered.
	"""
	explicit = _float(person_hours)
	if explicit > 0:
		return round(explicit, 2), False
	if full_day:
		return None, True
	planned = _float(planned_hours)
	if planned > 0:
		return round(planned, 2), False
	return round(_float(default_hours), 2), True


def full_day_hours(capacity, day_hours):
	"""A person's whole day: their capacity that day, or the Settings' full day when it is 0.

	The same rule as an unestimated task (:func:`allocate_task`): on a day they do not work the
	booking still lands, so :func:`day_conflicts` says "Booked on a day off" rather than the visit
	vanishing from the count.
	"""
	capacity = _float(capacity)
	return round(capacity if capacity > 0 else _float(day_hours), 2)


def _visit_entries(base, people, full_day, planned_hours, default_hours, clocked_hours=None, slot=None):
	"""One booking per person on a visit. ``people``: ``[(user, crew-row hours or None)]``, lead first."""
	out = []
	seen = set()
	for index, (user, person_hours) in enumerate(people):
		if not user or user in seen:
			continue
		seen.add(user)
		if index == 0 and clocked_hours is not None:
			hours, estimated, mine = clocked_hours, False, slot
		else:
			hours, estimated = visit_person_hours(person_hours, full_day, planned_hours, default_hours)
			mine = None
		out.append(dict(base, user=user, hours=hours, slot=mine, estimated=estimated, full_day=hours is None))
	return out


def _read_visit_crews(names):
	"""``{record: [(user, hours)]}`` for the visit records named. One query, crew table only."""
	if not names:
		return {}
	out = defaultdict(list)
	for row in frappe.db.sql(
		"""
		SELECT parent, user, hours, idx
		FROM `tabSapphire Visit Crew Member`
		WHERE parenttype = %(parenttype)s AND parentfield = 'crew' AND parent IN %(names)s
		ORDER BY parent, idx
		""",
		{"parenttype": "Sapphire Maintenance Record", "names": tuple(sorted(names))},
		as_dict=True,
	) or []:
		if row.get("parent") and row.get("user"):
			out[row.get("parent")].append((row.get("user"), row.get("hours")))
	return out


def _read_visits(users, start, end, settings):
	"""``[{"user", "date", "ref", "label", "project", "hours", "slot", "estimated", "full_day"}]``.

	Visit records on their plan date (the Maintenance Planner's own CASE: a finished visit sits on
	the day it was done, an open one on its scheduled day), plus projected visits the scheduler has
	not drafted, which carry the site's default technician and default crew.

	A visit books **every person on it** (P1.8): its technician and each crew member, one entry
	each, so a multi-person visit shows on every one of their days and every one of their routes.
	Hours per person follow :func:`visit_person_hours`; a clocked visit (clock in and out) books
	its clocked length for the technician. An entry with ``hours`` None is a full-day booking,
	which :func:`_compute` turns into that person's capacity.
	"""
	from erpnext_enhancements.api import maintenance_planner as mp

	if not users:
		return []
	wanted = set(users)
	visit_hours = settings.get("maintenance_visit_hours")
	visit_hours = _float(
		visit_hours if visit_hours is not None else DEFAULT_SETTINGS["maintenance_visit_hours"]
	)
	rows = []
	for row in frappe.db.sql(
		"""
		SELECT * FROM (
			SELECT
				r.name, r.project, r.visit_label, r.technician, r.clock_in_time, r.clock_out_time,
				r.planned_hours, r.full_day,
				p.project_name AS project_title,
				CASE
					WHEN r.docstatus = 1 OR r.workflow_state = %(pending)s
						THEN COALESCE(r.visit_date, r.scheduled_visit_date, DATE(r.modified))
					ELSE COALESCE(r.scheduled_visit_date, r.visit_date)
				END AS plan_date
			FROM `tabSapphire Maintenance Record` r
			LEFT JOIN `tabProject` p ON p.name = r.project
			WHERE r.docstatus < 2
				AND (
					r.technician IN %(users)s
					OR r.name IN (
						SELECT c.parent FROM `tabSapphire Visit Crew Member` c
						WHERE c.parenttype = %(record)s AND c.parentfield = 'crew' AND c.user IN %(users)s
					)
				)
		) visits
		WHERE plan_date BETWEEN %(start)s AND %(end)s
		ORDER BY plan_date, name
		LIMIT 5000
		""",
		{
			"start": start,
			"end": end,
			"users": tuple(users),
			"pending": mp.PENDING_STATE,
			"record": "Sapphire Maintenance Record",
		},
		as_dict=True,
	):
		day = _as_date(row.get("plan_date"))
		if day and start <= day <= end:
			rows.append((row, day))

	crews = _read_visit_crews({row.get("name") for row, _day in rows})
	out = []
	for row, day in rows:
		people = [(row.get("technician"), None)] + list(crews.get(row.get("name")) or [])
		clock_in, clock_out = _as_datetime(row.get("clock_in_time")), _as_datetime(row.get("clock_out_time"))
		clocked = bool(clock_in and clock_out and clock_out > clock_in)
		site = mp.short_site_name(row.get("project_title") or row.get("project") or "") or row.get("name")
		base = {
			"date": day,
			"ref": row.get("name"),
			"label": f"{site} · {row.get('visit_label')}" if row.get("visit_label") else site,
			"project": row.get("project"),
		}
		entries = _visit_entries(
			base,
			people,
			bool(row.get("full_day")),
			row.get("planned_hours"),
			visit_hours,
			# Only a technician on the visit can have clocked it; no technician, nobody's clock.
			clocked_hours=round((clock_out - clock_in).total_seconds() / 3600, 2)
			if clocked and row.get("technician")
			else None,
			slot=_slot_text((clock_in, clock_out))
			if clocked and clock_in.date() == clock_out.date()
			else None,
		)
		out.extend(entry for entry in entries if entry["user"] in wanted)

	try:
		from frappe.utils import nowdate

		today = _as_date(nowdate())
		projected = mp._projections(start, end, today)
		mp._decorate(projected)
	except Exception:
		# A projection bug must not blank a planner; the stored visits above still count.
		frappe.log_error(title="Project Planner: visit projections failed", message=frappe.get_traceback())
		projected = []
	for card in projected:
		day = _as_date(card.get("date"))
		if not day or not (start <= day <= end):
			continue
		# A projected visit carries the site's default crew, length and full-day flag (P1.8), so the
		# planners show the crew days of visits that are not drafted yet.
		people = [(card.get("technician"), None)] + [
			(member.get("user"), member.get("hours")) for member in card.get("crew") or []
		]
		if not any(user in wanted for user, _hours in people):
			continue
		base = {
			"date": day,
			"ref": card.get("contract"),
			"key": card.get("key"),
			"label": "Projected visit: " + (card.get("site") or card.get("project") or ""),
			"project": card.get("project"),
		}
		entries = _visit_entries(
			base, people, bool(card.get("full_day")), card.get("planned_hours"), visit_hours
		)
		out.extend(entry for entry in entries if entry["user"] in wanted)
	return out


def _read_travel(employees, start, end):
	"""``[{"employee", "date", "ref", "label", "project"}]``, one per traveler per day.

	A traveler row's own dates, else the trip's. Every status counts: a trip has no canceled state,
	and a Completed or Closed one in the range was still a day away.
	"""
	if not employees:
		return []
	out = []
	for row in frappe.db.sql(
		"""
		SELECT
			tt.employee, tt.from_date, tt.to_date,
			t.name AS trip, t.purpose, t.start_date, t.end_date, t.project
		FROM `tabTrip Traveler` tt
		INNER JOIN `tabTravel Trip` t ON t.name = tt.parent
		WHERE tt.parenttype = 'Travel Trip'
			AND tt.employee IN %(employees)s
			AND COALESCE(tt.from_date, t.start_date, tt.to_date, t.end_date) <= %(end)s
			AND COALESCE(tt.to_date, t.end_date, tt.from_date, t.start_date) >= %(start)s
		""",
		{"employees": tuple(employees), "start": start, "end": end},
		as_dict=True,
	):
		first = _as_date(row.get("from_date")) or _as_date(row.get("start_date"))
		last = _as_date(row.get("to_date")) or _as_date(row.get("end_date"))
		first, last = first or last, last or first
		if not _range_overlaps(first, last, start, end) or last < first:
			continue
		for day in daterange(max(first, start), min(last, end)):
			out.append(
				{
					"employee": row.get("employee"),
					"date": day,
					"ref": row.get("trip"),
					"label": "Travel: " + (row.get("purpose") or row.get("trip") or ""),
					"project": row.get("project"),
				}
			)
	return out


# ---------------------------------------------------------------------- assembly


def _compute(start, end, resources=None, exclude=(), extra=(), google=True, equipment=False):
	"""Everything :func:`availability` returns, plus what the Project Planner API reuses.

	``exclude`` drops tasks by name and ``extra`` adds ``(task_like, crew)`` pairs: together they
	answer "what if this task had these dates and this crew" for :func:`preview_conflicts`.
	``google=False`` prices drives from the cache and the straight-line estimate only (see
	``routing.plan_routes``), for long ranges such as the capacity heatmap.
	``equipment=True`` (P4.4) also reads the tasks' vehicles and assets and adds ``equipment``
	(``{task: [{type, name, label}]}``) and ``equipment_conflicts`` (``{task: [sentence]}``, see
	:func:`equipment_conflicts`); a hypothetical task's ``equipment`` key replaces its stored rows.
	"""
	start, end = _as_date(start), _as_date(end)
	settings = get_settings()
	people, patterns = _read_resources(resources)
	by_name = {p.get("name"): p for p in people}
	user_to_resource = {p.get("user"): p.get("name") for p in people if p.get("user")}
	employee_to_resource = {p.get("employee"): p.get("name") for p in people if p.get("employee")}

	exclude = set(exclude or ())
	# Equipment is booked on tasks, not people, so a crewless check still needs the tasks.
	tasks = (
		[t for t in read_tasks(start, end) if t.get("name") not in exclude] if (people or equipment) else []
	)
	crew_rows, todo_users = read_crews([t.get("name") for t in tasks])
	crews = {
		t.get("name"): resolve_crew(
			crew_rows.get(t.get("name")), todo_users.get(t.get("name")), user_to_resource
		)
		for t in tasks
	}
	for task_like, crew in extra or ():
		tasks.append(task_like)
		crews[task_like.get("name")] = list(crew or [])

	# Capacity is needed on every day a visible task covers, not only the visible ones.
	low, high = start, end
	for task in tasks:
		span = task_span(task)
		if span:
			low, high = min(low, span[0]), max(high, span[1])
	low = max(low, start - datetime.timedelta(days=CAPACITY_MARGIN_DAYS))
	high = min(high, end + datetime.timedelta(days=CAPACITY_MARGIN_DAYS))
	holidays = _read_holidays(people, low, high) if people else {}
	time_off = _read_time_off(people, low, high) if people else {}
	day_hours = settings["default_day_hours"]

	cache = {}

	def capacity_and_off(resource, day):
		key = (resource, day)
		if key not in cache:
			base = pattern_hours(patterns.get(resource, []), day, day_hours)
			cache[key] = day_capacity(
				base, (holidays.get(resource) or {}).get(day), (time_off.get(resource) or {}).get(day)
			)
		return cache[key]

	def capacity_of(resource, day):
		return capacity_and_off(resource, day)[0] if resource in by_name else 0.0

	bookings = defaultdict(list)
	task_hours = defaultdict(lambda: defaultdict(float))
	for task in tasks:
		crew = [m for m in crews.get(task.get("name")) or [] if m.get("resource") in by_name]
		kind = "rental" if task.get("custom_rental_booking") else "task"
		slot = _slot_text(task_slot(task))
		tentative = is_tentative(task)
		for resource, day, hours, estimated in allocate_task(task, crew, capacity_of, settings):
			task_hours[task.get("name")][resource] += hours
			if start <= day <= end:
				booking = {
					"kind": kind,
					"ref": task.get("name"),
					"label": task.get("subject") or task.get("name"),
					"project": task.get("project"),
					"hours": hours,
					"slot": slot,
					"estimated": estimated,
				}
				if tentative:
					booking["tentative"] = True
				bookings[(resource, day)].append(booking)

	for visit in _read_visits(list(user_to_resource), start, end, settings) if user_to_resource else []:
		resource = user_to_resource.get(visit["user"])
		hours = visit["hours"]
		if hours is None:
			# A full-day visit (P1.8): the person's whole day, the same rule as an unestimated task.
			hours = full_day_hours(capacity_of(resource, visit["date"]), day_hours)
		bookings[(resource, visit["date"])].append(
			{
				"kind": "visit",
				"ref": visit["ref"],
				"key": visit.get("key") or visit["ref"],
				"label": visit["label"],
				"project": visit["project"],
				"hours": hours,
				"slot": visit["slot"],
				"estimated": visit["estimated"],
			}
		)

	for trip in _read_travel(list(employee_to_resource), start, end) if employee_to_resource else []:
		resource = employee_to_resource.get(trip["employee"])
		capacity = capacity_of(resource, trip["date"])
		bookings[(resource, trip["date"])].append(
			{
				"kind": "travel",
				"ref": trip["ref"],
				"label": trip["label"],
				"project": trip["project"],
				# The whole working day is spoken for; a day they do not work loses nothing, so the
				# Saturday of a trip is shown as away without turning red (see day_conflicts).
				"hours": round(capacity, 2),
				"slot": None,
				"estimated": True,
			}
		)

	try:
		routes = _apply_routes(bookings, by_name, start, end, settings, google=google)
	except Exception:
		# Routing is a refinement: a bug or an outage there must never blank a planner. The
		# day reads as it did before Phase 2, with no driving counted.
		frappe.log_error(title="Project Planner: routes failed", message=frappe.get_traceback())
		routes = {}

	long_limit = settings.get("long_drive_minutes")
	long_limit = _float(long_limit if long_limit is not None else DEFAULT_SETTINGS["long_drive_minutes"])
	restrictions = _read_restrictions(people, start, end) if people else {}
	days = {}
	for person in people:
		name = person.get("name")
		per_day = {}
		for day in daterange(start, end):
			capacity, off = capacity_and_off(name, day)
			mine = bookings.get((name, day), [])
			booked = round(sum(_float(b["hours"]) for b in mine if not b.get("tentative")), 2)
			soft = round(sum(_float(b["hours"]) for b in mine if b.get("tentative")), 2)
			route = routes.get((name, day))
			drive = route["drive_minutes"] if route else 0.0
			warnings = list((restrictions.get(name) or {}).get(day, []))
			pencil = pencil_warning(capacity, mine)
			if pencil:
				warnings.append(pencil)
			per_day[str(day)] = {
				"capacity": capacity,
				"booked": booked,
				"soft_booked": soft,
				"free": round(max(capacity - booked, 0.0), 2),
				"off": off,
				"bookings": mine,
				"conflicts": day_conflicts(capacity, off, mine),
				"warnings": warnings,
				"drive_minutes": drive,
				"drive_source": route["source"] if route and drive > 0 else None,
				"long_drive": bool(drive > long_limit) if route else False,
				"unlocated": len(route["unlocated"]) if route else 0,
			}
		days[name] = per_day

	out = {
		"resources": [
			{
				"name": p.get("name"),
				"label": p.get("resource_name") or p.get("name"),
				"group": p.get("resource_group"),
				"home_team": p.get("home_team"),
				"color": p.get("color"),
				"user": p.get("user"),
				"employee": p.get("employee"),
				"type": p.get("resource_type"),
			}
			for p in people
		],
		"days": days,
		"user_to_resource": user_to_resource,
		"settings": settings,
		"routes": routes,
		"tasks": tasks,
		"crews": crews,
		"todo_users": todo_users,
		"task_hours": {task: dict(hours) for task, hours in task_hours.items()},
	}
	if equipment:
		overrides = {
			task_like.get("name"): task_equipment(task_like.get("equipment"))
			for task_like, _crew in extra or ()
			if "equipment" in (task_like or {})
		}
		state = _equipment_state(tasks, start, end, overrides)
		out["equipment"] = state["equipment"]
		out["equipment_conflicts"] = state["conflicts"]
	return out


def drive_booking(minutes, source):
	"""The booking that counts a day's driving against the person's hours."""
	return {
		"kind": "drive",
		"ref": None,
		"label": "Driving",
		"project": None,
		"hours": round(_float(minutes) / 60, 2),
		"slot": None,
		"estimated": source != "google",
	}


def _apply_routes(bookings, people, start, end, settings, google=True):
	"""Route every visible person-day that has stops, and pad the day with its driving.

	``{(resource, day): route}`` from ``routing.plan_routes``, which locates every stop and
	prices every leg of every day in one go (one ``drive_matrix`` call per load, never one per
	person-day). When Settings ``pad_drive_time`` is on, a ``drive`` booking is appended so free
	hours, "Over by" and the preview a save is checked against all include the driving. Nothing is
	appended until every route is worked out, so a failure leaves the bookings as they were.

	Tentative bookings are not routed: a pencil is not a drive anyone has committed to, and its
	driving would otherwise book firm hours. ``google=False`` is passed on to
	``routing.plan_routes`` (cached and estimated legs only).
	"""
	day_bookings = {}
	for key, value in bookings.items():
		if key[0] not in people or not (start <= key[1] <= end):
			continue
		firm = [b for b in value or [] if not b.get("tentative")]
		if firm:
			day_bookings[key] = firm
	options = {} if google else {"google": False}
	routes = routing.plan_routes(day_bookings, settings, **options) if day_bookings else {}
	pad = settings.get("pad_drive_time")
	if pad is None or _float(pad):
		padding = [
			(key, drive_booking(route["drive_minutes"], route["source"]))
			for key, route in routes.items()
			if _float(route.get("drive_minutes")) > 0
		]
		for key, booking in padding:
			bookings[key].append(booking)
	return routes


def availability(start, end, resources=None, google=True):
	"""Capacity, bookings, conflicts and warnings per Planner Resource per day.

	See the module docstring for what counts. ``resources`` limits the answer to those Planner
	Resource names. Callers apply their own role gate first: the task and visit reads are raw SQL.
	``google=False`` never asks Google anything: legs come from the drive time cache or the
	straight-line estimate, and the shop only from its cached coordinates.
	"""
	data = _compute(start, end, resources, google=google)
	return {key: data[key] for key in ("resources", "days", "user_to_resource")}


def _preview_many(changes, start=None, end=None, google=True):
	"""``(conflicts, task_hours)`` for hypothetical states of several tasks at once.

	``changes`` is ``[(task_like, crew)]``. Every task named is taken out of the stored data and
	its hypothetical state put in, so moves that land on one person's day count against each
	other (the batch writes: shifting successors, rescheduling overdue work, copying a week).
	``conflicts`` is ``{resource label: ["YYYY-MM-DD: ...", ...]}`` for the days a **firm** task
	of the batch books; a tentative one never conflicts. Vehicle and equipment clashes of the
	batch's firm tasks (P4.4) are filed under :data:`EQUIPMENT_KEY`; a ``task_like`` carrying an
	``equipment`` list is judged with it, one without with its stored rows. ``google=False`` (the
	Phase 4 actuals) prices drives without Google, as in :func:`_compute`. ``task_hours`` is
	``{task: {resource: hours}}`` over each task's whole span. ``start``/``end`` default to the
	union of the tasks' spans.
	"""
	prepared, spans, resources = [], [], []
	for task_like, crew in changes or ():
		crew = [m for m in (crew or []) if m.get("resource")]
		span = task_span(task_like)
		# A crewless task with a vehicle on it still has something to check (P4.4). Without the
		# ``equipment`` key nothing is known about its equipment here, so it is read as stored.
		has_equipment = (
			bool(task_equipment(task_like.get("equipment")))
			if "equipment" in task_like
			else bool(read_equipment([task_like.get("name")]))
		)
		if not span or not (crew or has_equipment):
			continue
		prepared.append((task_like, crew))
		spans.append(span)
		resources.extend(m["resource"] for m in crew)
	start = _as_date(start) or (min(s[0] for s in spans) if spans else None)
	end = _as_date(end) or (max(s[1] for s in spans) if spans else None)
	if not prepared or not start or not end:
		return {}, {}
	names = {task.get("name") for task, _crew in prepared}
	firm = {task.get("name") for task, _crew in prepared if not is_tentative(task)}
	data = _compute(
		start,
		end,
		list(dict.fromkeys(resources)),
		exclude=names,
		extra=prepared,
		google=google,
		equipment=True,
	)
	labels = {r["name"]: r["label"] for r in data["resources"]}
	out = {}
	for name in sorted(firm):
		for line in (data.get("equipment_conflicts") or {}).get(name) or []:
			if line not in out.setdefault(EQUIPMENT_KEY, []):
				out[EQUIPMENT_KEY].append(line)
	if not out.get(EQUIPMENT_KEY):
		out.pop(EQUIPMENT_KEY, None)
	for resource, per_day in data["days"].items():
		for day, cell in sorted(per_day.items()):
			if not any(b["ref"] in firm and b["kind"] in TASK_KINDS for b in cell["bookings"]):
				continue
			for conflict in cell["conflicts"]:
				# A double booking between two other tasks is not this batch's doing.
				if conflict.startswith("Double-booked") and not any(
					f"({name}," in conflict or f", {name})" in conflict for name in firm
				):
					continue
				out.setdefault(labels.get(resource, resource), []).append(f"{day}: {conflict}")
	return out, {name: data["task_hours"].get(name, {}) for name in names}


def preview_batch(changes, start=None, end=None):
	"""What would be wrong if every ``(task_like, crew)`` in ``changes`` had that state at once.

	The many-task form of :func:`preview_conflicts`, with the same answer shape.
	"""
	return _preview_many(changes, start, end)[0]


def _preview(task_like, crew, start=None, end=None):
	"""``(conflicts, task_hours)`` for a hypothetical state of one task. See preview_conflicts."""
	conflicts, hours = _preview_many([(task_like, crew)], start, end)
	return conflicts, hours.get(task_like.get("name"), {})


def preview_conflicts(task_like, crew, start=None, end=None):
	"""What would be wrong if this task had these dates and this crew.

	``{resource label: ["2026-10-12: Over by 2h", ...]}`` for the crew's days that the task would
	book, with the task's stored state left out and the hypothetical one put in. ``start``/``end``
	default to the task's span. An empty dict means nothing would conflict, which is always the
	answer for a tentative task: a pencil never needs a reason.
	"""
	return _preview(task_like, crew, start, end)[0]
