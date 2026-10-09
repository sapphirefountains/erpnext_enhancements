# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Quick looks for both planners (Phase 6A, TASK-2026-02467): one person's week, one day, one
project, one maintenance site.

Nik, 2026-10-09: "be able to click the Technicians name or something and see what their specific
schedule is in a pop up or something so as not to lose context overall." Both planner pages open
these answers in a side drawer (``public/js/planner_kit``) while the calendar stays usable behind
it, so nothing here moves anything: every endpoint is a plain read.

* :func:`get_person_schedule` is one person's days (7 by default, at most 31): every booking with
  its slot, hours and site, free hours, the day off ("Off", "Holiday": never a type or a reason),
  conflicts, each day's stops in driving order, their group and home team, and the phone number and
  email the call/text/email buttons need. The Project Planner names people by Planner Resource and
  the Maintenance Planner by User, so it takes either and resolves one to the other through
  ``Planner Resource.user``.
* :func:`get_day_overview` is everyone's day side by side, from **one** engine call for everybody.
* :func:`get_project_overview` is one customer job: its open tasks as planner cards (each with a
  timeline bar over the project's span, or over the next eight weeks when that is shorter), planned
  against worked hours from Phase 4's ``get_actuals``, and the labor forecast from Phase 4's
  ``get_labor_forecast``, which gives money only to ``planner_tracking.COST_ROLES``.
* :func:`get_site_overview` is the Maintenance Planner's equivalent: one site's recent, upcoming and
  projected visits, its default technician and crew, and its Maintenance Profile.

Things this module is careful about:

* **No Google.** Every engine call is ``google=False`` (the drive time cache and the straight-line
  estimate), so opening a drawer never costs a Routes request, and every answer that shows driving
  carries ``estimate_note`` for the page to say so. The route view itself, one click away, still
  asks Google.
* **The builders are the planners' own.** A person's days are ``project_planner._people_days`` (the
  rows My week and the crew sheet print), the task cards are ``project_planner.build_card`` (so the
  page can drag a task out of a drawer exactly as it drags a card off the board), and a site's
  visits are ``maintenance_planner._visit_card``. Nothing is computed twice in two ways.
* **Gates.** The person, day and project reads open to everyone who can open either planner (the
  union of their roles), because both pages call them. The site read is the Maintenance Planner's
  own gate, the roles that read every maintenance record: its record query is raw SQL, exactly as
  ``maintenance_planner.get_planner``'s is.
* **Customer jobs only.** :func:`get_project_overview` answers "not on the planner" for anything
  ``crew_availability.planner_projects`` does not accept (Internal, Overhead, no type).
"""

import datetime
import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from erpnext_enhancements.api import maintenance_planner as mp
from erpnext_enhancements.api import project_planner as pp
from erpnext_enhancements.project_enhancements import crew_availability as engine

#: Everyone who can open either planner: both pages call the person, day and project reads.
VIEW_ROLES = frozenset(pp.PLANNER_ROLES | mp.PLANNER_ROLES)
#: The most days one person's schedule covers (a month view's worth).
MAX_DAYS = 31
DEFAULT_DAYS = 7
#: The most tasks a project overview lists (``get_actuals`` takes at most 300 too).
PROJECT_TASK_LIMIT = 300
#: The project timeline covers the project's span, or the next eight weeks when that is shorter.
TIMELINE_DAYS = 56
#: A site's recent visits, how far ahead its upcoming and projected ones are looked for, and how
#: many of each come back.
SITE_RECENT = 6
SITE_AHEAD_DAYS = 90
SITE_UPCOMING = 12
#: The Project Planner's group order, so the day overview lists people the way its panel does.
GROUP_ORDER = ("Field", "PM", "Design", "Subcontractor")
ESTIMATE_NOTE = "Drive times here are estimates (saved and straight-line times). The route view has Google's."

_EMAIL = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+$")


# ---------------------------------------------------------------------- pure helpers


def off_label(off):
	"""The day-off label a drawer shows: "Holiday", "Holiday (half day)", "Off", "Half day off" or
	"Not a work day", or None on a working day.

	The engine's own labels already never name a kind of time off, but the drawer is a new place to
	show them, so anything it does not recognise reads as plain "Off" rather than passing through.
	A holiday's name is left out too: the spec asks for "Off" / "Holiday" and nothing else.
	"""
	text = str(off or "").strip()
	if not text:
		return None
	if text.startswith("Holiday (half day)"):
		return "Holiday (half day)"
	if text.startswith("Holiday"):
		return "Holiday"
	if text in ("Half day off", "Not a work day"):
		return text
	return "Off"


def off_kind(off):
	"""``"holiday"``, ``"time_off"``, ``"half_day"``, ``"not_working"`` or None, for styling."""
	label = off_label(off)
	return {
		None: None,
		"Holiday": "holiday",
		"Holiday (half day)": "half_day",
		"Half day off": "half_day",
		"Not a work day": "not_working",
	}.get(label, "time_off")


def tel_number(raw):
	"""A number a ``tel:`` / ``sms:`` link can dial, or None.

	Employee cell numbers on this site are sparse and often bare digits ("8015551234"), so ten digits
	are taken as a US number and eleven starting with 1 as one with its country code. Anything else
	keeps a leading "+" when it had one, and a number shorter than seven digits is no number.
	"""
	text = str(raw or "").strip()
	if not text:
		return None
	plus = text.startswith("+")
	digits = re.sub(r"\D", "", text)
	if len(digits) == 10 and not plus:
		return "+1" + digits
	if len(digits) == 11 and digits.startswith("1") and not plus:
		return "+" + digits
	if 7 <= len(digits) <= 15:
		return ("+" if plus else "") + digits
	return None


def clean_email(raw):
	"""An address a ``mailto:`` link can use, or None."""
	text = str(raw or "").strip()
	return text if _EMAIL.match(text) else None


def _slot_start(slot):
	return str((slot or ["99:99"])[0])


def booking_entry(booking, stop=None):
	"""One engine booking as a drawer lists it, with its stop's arrival time and address."""
	stop = stop or {}
	kind = booking.get("kind")
	return {
		"kind": kind,
		"ref": booking.get("ref"),
		# A projected visit's own key (its contract, feature and day); everything else is its ref.
		"key": booking.get("key") or booking.get("ref"),
		"label": _("Driving") if kind == "drive" else (booking.get("label") or booking.get("ref") or ""),
		"project": booking.get("project"),
		"project_title": stop.get("project_title"),
		"hours": round(flt(booking.get("hours")), 2),
		"slot": booking.get("slot"),
		"tentative": bool(booking.get("tentative")),
		"estimated": bool(booking.get("estimated")),
		"arrive": stop.get("arrive"),
		"address": stop.get("address"),
		"order": stop.get("order"),
	}


def day_view(entry, cell):
	"""One person-day for a drawer, from a ``project_planner.day_entry`` and the engine's cell.

	``bookings`` is every booking of the day (pencils and the drive included), in driving order
	first and by time slot after; ``stops`` are the day entry's items, already in driving order
	with arrival times, addresses, Maps links and crewmates.
	"""
	entry = entry or {}
	cell = cell or {}
	stops = list(entry.get("items") or [])
	found = {}
	for index, stop in enumerate(stops):
		found.setdefault((stop.get("kind"), stop.get("ref")), dict(stop, order=index + 1))
	bookings = [
		booking_entry(b, found.get((b.get("kind"), b.get("ref")))) for b in cell.get("bookings") or []
	]
	bookings.sort(
		key=lambda b: (
			b["kind"] == "drive",
			b["order"] is None,
			b["order"] or 0,
			_slot_start(b["slot"]),
			str(b["label"]),
		)
	)
	return {
		"date": entry.get("date"),
		"off": off_label(cell.get("off")),
		"off_kind": off_kind(cell.get("off")),
		"capacity": round(flt(cell.get("capacity")), 2),
		"booked": round(flt(cell.get("booked")), 2),
		"soft_booked": round(flt(cell.get("soft_booked")), 2),
		"free": round(flt(cell.get("free")), 2),
		"conflicts": list(cell.get("conflicts") or []),
		"warnings": list(cell.get("warnings") or []),
		"travel": entry.get("travel"),
		"drive_minutes": round(flt(cell.get("drive_minutes")), 1),
		"drive_source": cell.get("drive_source"),
		"long_drive": bool(cell.get("long_drive")),
		"unlocated": cint(cell.get("unlocated")),
		"bookings": bookings,
		"stops": stops,
	}


def group_rank(person):
	"""Sort key: the Project Planner panel's group order, then the name."""
	group = (person or {}).get("group")
	rank = GROUP_ORDER.index(group) if group in GROUP_ORDER else len(GROUP_ORDER)
	return (rank, str((person or {}).get("label") or "").lower())


def timeline_window(spans, today, first_weekday=6, max_days=TIMELINE_DAYS):
	"""``(first, last)`` the project timeline covers, or None with no dated task.

	The project's span (its first open task's start to its last one's end) when that is
	``max_days`` or less; otherwise the next ``max_days`` days from the start of this week (the
	site's ``first_weekday``, Monday 0 … Sunday 6), slid back to end on the last task when the
	project finishes sooner, and forward to the first task when it has not started yet.
	"""
	spans = [(getdate(a), getdate(b or a)) for a, b in spans or () if a]
	if not spans:
		return None
	first = min(a for a, _b in spans)
	last = max(b for _a, b in spans)
	length = datetime.timedelta(days=max_days - 1)
	if (last - first).days + 1 <= max_days:
		return first, last
	start = max(pp.week_start(getdate(today), first_weekday), first)
	end = start + length
	if end > last:
		end = last
		start = max(first, end - length)
	return start, end


def timeline_bar(first, last, window):
	"""Where one task sits on the timeline: ``left_pct``/``width_pct`` and whether its bar is cut off
	at either edge, or ``{"outside": "before" | "after"}`` when it is wholly off the window."""
	first, last = getdate(first), getdate(last or first)
	start, end = window
	if last < start:
		return {"outside": "before"}
	if first > end:
		return {"outside": "after"}
	total = (end - start).days + 1
	shown_first, shown_last = max(first, start), min(last, end)
	return {
		"left_pct": round((shown_first - start).days / total * 100, 2),
		"width_pct": round(((shown_last - shown_first).days + 1) / total * 100, 2),
		"clipped_start": first < start,
		"clipped_end": last > end,
	}


# ---------------------------------------------------------------------- gates and lookups


def _require_viewer():
	if frappe.session.user == "Administrator":
		return
	if not VIEW_ROLES & set(frappe.get_roles()):
		frappe.throw(_("Only projects and maintenance staff can use the planners."), frappe.PermissionError)


def _require_maintenance():
	"""The Maintenance Planner's own gate: the roles that read every maintenance record."""
	if frappe.session.user == "Administrator":
		return
	if not mp.PLANNER_ROLES & set(frappe.get_roles()):
		frappe.throw(_("Only maintenance staff can see a site's visits."), frappe.PermissionError)


PERSON_FIELDS = [
	"name",
	"resource_name",
	"resource_type",
	"employee",
	"user",
	"supplier",
	"resource_group",
	"home_team",
	"color",
	"is_active",
]


def _resolve_person(resource=None, user=None):
	"""The Planner Resource row for ``resource``, else the one whose ``user`` is ``user`` (an active
	one first), else None."""
	resource, user = pp.given(resource), pp.given(user)
	if resource:
		row = frappe.db.get_value("Planner Resource", resource, PERSON_FIELDS, as_dict=True)
		return row or None
	if user:
		rows = frappe.get_all(
			"Planner Resource",
			filters={"user": user},
			fields=PERSON_FIELDS,
			order_by="is_active desc, creation asc",
			limit_page_length=0,
		)
		rows = sorted(rows, key=lambda r: 0 if cint(r.get("is_active")) else 1)
		return rows[0] if rows else None
	frappe.throw(_("Name a person: a Planner Resource or a user."))


def _value(doctype, name, fields):
	"""``frappe.db.get_value`` as a dict, or {} when the record, the doctype or a field is missing."""
	if not name:
		return {}
	try:
		return frappe.db.get_value(doctype, name, fields, as_dict=True) or {}
	except Exception:
		return {}


def _contact(person):
	"""``{"phone", "tel", "email"}`` for the call/text/email buttons, each None when there is none.

	An employee's own cell number and company email first, then their User's mobile number and
	address; a subcontractor's Supplier mobile and email. Personal email is never offered.
	"""
	phone = email = None
	if person.get("employee"):
		row = _value("Employee", person["employee"], ["cell_number", "company_email"])
		phone = row.get("cell_number") or None
		email = clean_email(row.get("company_email"))
	if person.get("user") and (not phone or not email):
		row = _value("User", person["user"], ["mobile_no", "email"])
		phone = phone or row.get("mobile_no") or None
		email = email or clean_email(row.get("email") or person.get("user"))
	if person.get("supplier") and (not phone or not email):
		row = _value("Supplier", person["supplier"], ["mobile_no", "email_id"])
		phone = phone or row.get("mobile_no") or None
		email = email or clean_email(row.get("email_id"))
	tel = tel_number(phone)
	return {"phone": str(phone).strip() if tel else None, "tel": tel, "email": email}


def _resource_map():
	"""``(user_to_resource, labels)`` for every active Planner Resource, the map the engine resolves
	crews with, so a task's crew here is the crew the board shows."""
	user_to_resource, labels = {}, {}
	for row in frappe.get_all(
		"Planner Resource",
		filters={"is_active": 1},
		fields=["name", "resource_name", "user"],
		limit_page_length=0,
	):
		labels[row.get("name")] = row.get("resource_name") or row.get("name")
		if row.get("user"):
			user_to_resource.setdefault(row.get("user"), row.get("name"))
	return user_to_resource, labels


def _cards(rows, today, can_edit):
	"""Planner cards (``project_planner.build_card``) for Task rows, with the crew, qualifications and
	equipment the board's own cards carry, so the page can drag one out of a drawer and Undo puts
	back exactly what was there."""
	rows = [row for row in rows or [] if row.get("name")]
	if not rows:
		return []
	names = [row.get("name") for row in rows]
	user_to_resource, labels = _resource_map()
	crew_rows, todo_users = engine.read_crews(names)
	credentials = pp._credentials(names)
	equipment = pp._equipment_labelled(engine.read_equipment(names))
	out = []
	for row in rows:
		name = row.get("name")
		crew = engine.resolve_crew(crew_rows.get(name), todo_users.get(name), user_to_resource)
		crew = [dict(m, label=m.get("label") or labels.get(m["resource"]) or m["resource"]) for m in crew]
		out.append(
			pp.build_card(
				row, crew, {}, credentials.get(name), today, can_edit, equipment=equipment.get(name)
			)
		)
	return out


def _drag_cards(data, refs, today):
	"""``{task: card}`` for the project tasks among ``refs`` that the engine pass read."""
	refs = set(refs or ())
	rows = [
		row
		for row in (data or {}).get("tasks") or []
		if row.get("name") in refs and not row.get("custom_rental_booking")
	]
	can_edit = bool(frappe.has_permission("Task", "write"))
	return {card["name"]: card for card in _cards(rows, today, can_edit)}


def _task_refs(days):
	return {
		b["ref"]
		for day in days
		for b in day.get("bookings") or []
		if b.get("kind") == "task" and b.get("ref")
	}


# ---------------------------------------------------------------------- one person


@frappe.whitelist()
def get_person_schedule(resource=None, start=None, days=DEFAULT_DAYS, user=None):
	"""One person's days from ``start`` (default today), ``days`` long (default 7, at most 31).

	``resource`` is a Planner Resource; ``user`` a User, resolved through ``Planner Resource.user``
	(the Maintenance Planner names people by User). Returns ``{"resource", "user", "label", "group",
	"home_team", "type", "color", "active", "contact": {"phone", "tel", "email"}, "start", "end",
	"today", "days": [day_view], "cards": {task: card}, "can_edit", "estimate_note", "message"}``.
	Someone with no Planner Resource, or an inactive one, gets ``resource`` and ``days`` empty and a
	``message`` saying why. Driving is priced without Google.
	"""
	_require_viewer()
	first = getdate(pp.given(start) or nowdate())
	count = cint(days) if pp.given(days) is not None else DEFAULT_DAYS
	if count < 1 or count > MAX_DAYS:
		frappe.throw(_("Ask for 1 to {0} days.").format(MAX_DAYS))
	last = first + datetime.timedelta(days=count - 1)
	today = getdate(nowdate())
	person = _resolve_person(resource, user)
	answer = {
		"resource": None,
		"user": pp.given(user),
		"label": None,
		"group": None,
		"home_team": None,
		"type": None,
		"color": None,
		"active": False,
		"contact": {"phone": None, "tel": None, "email": None},
		"start": str(first),
		"end": str(last),
		"today": str(today),
		"days": [],
		"cards": {},
		"can_edit": False,
		"estimate_note": _(ESTIMATE_NOTE),
		"message": None,
	}
	if not person:
		answer["message"] = _(
			"This person is not on the planners' list of people, so there is no schedule to show. "
			"Add them under Planner Resources."
		)
		return answer
	answer.update(
		resource=person.get("name"),
		user=person.get("user") or answer["user"],
		label=person.get("resource_name") or person.get("name"),
		group=person.get("resource_group"),
		home_team=person.get("home_team"),
		type=person.get("resource_type"),
		color=person.get("color"),
		active=bool(cint(person.get("is_active"))),
		contact=_contact(person),
	)
	if not answer["active"]:
		answer["message"] = _("{0} is not active on the planners, so there is no schedule to show.").format(
			answer["label"]
		)
		return answer
	data, entries = pp._people_days(first, last, [person["name"]], google=False)
	cells = (data.get("days") or {}).get(person["name"]) or {}
	answer["days"] = [
		day_view(entry, cells.get(entry.get("date"))) for entry in entries.get(person["name"]) or []
	]
	answer["cards"] = _drag_cards(data, _task_refs(answer["days"]), today)
	answer["can_edit"] = bool(frappe.has_permission("Task", "write"))
	return answer


# ---------------------------------------------------------------------- one day, everyone


@frappe.whitelist()
def get_day_overview(date, group=None):
	"""Everyone's ``date`` side by side, from one engine call for all of them (``google=False``).

	``group`` narrows to one Planner Resource group. Returns ``{"date", "today", "group", "people":
	[{"resource", "label", "group", "home_team", "user", **day_view}], "cards", "can_edit",
	"estimate_note", "note"}``.
	"""
	_require_viewer()
	day = getdate(date)
	today = getdate(nowdate())
	group = pp.given(group)
	answer = {
		"date": str(day),
		"today": str(today),
		"group": group,
		"people": [],
		"cards": {},
		"can_edit": bool(frappe.has_permission("Task", "write")),
		"estimate_note": _(ESTIMATE_NOTE),
		"note": None,
	}
	names = None
	if group:
		names = frappe.get_all(
			"Planner Resource",
			filters={"is_active": 1, "resource_group": group},
			pluck="name",
			limit_page_length=0,
		)
		if not names:
			answer["note"] = _("Nobody active is in the {0} group.").format(group)
			return answer
	data, entries = pp._people_days(day, day, names, google=False)
	people = []
	for person in sorted(data.get("resources") or [], key=group_rank):
		name = person.get("name")
		entry = (entries.get(name) or [{"date": str(day)}])[0]
		cell = ((data.get("days") or {}).get(name) or {}).get(str(day))
		people.append(
			{
				"resource": name,
				"label": person.get("label") or name,
				"group": person.get("group"),
				"home_team": person.get("home_team"),
				"user": person.get("user"),
				**day_view(entry, cell),
			}
		)
	answer["people"] = people
	answer["cards"] = _drag_cards(data, _task_refs(people), today)
	return answer


# ---------------------------------------------------------------------- one project


def _project_info(project):
	fields = [
		"name",
		"project_name",
		"customer",
		"status",
		"project_type",
		"expected_start_date",
		"expected_end_date",
		"percent_complete",
	]
	try:
		owner = bool(frappe.db.has_column("Project", "custom_project_owner"))
	except Exception:
		owner = False
	if owner:
		fields.append("custom_project_owner")
	row = _value("Project", project, fields)
	pm = None
	if row.get("custom_project_owner"):
		pm = _value("Employee", row["custom_project_owner"], ["employee_name"]).get("employee_name")
	customer_name = None
	if row.get("customer"):
		customer_name = _value("Customer", row["customer"], ["customer_name"]).get("customer_name")
	return {
		"title": row.get("project_name") or project,
		"customer": row.get("customer"),
		"customer_name": customer_name or row.get("customer"),
		"status": row.get("status"),
		"project_type": row.get("project_type"),
		"pm": pm,
		"expected_start": str(row["expected_start_date"]) if row.get("expected_start_date") else None,
		"expected_end": str(row["expected_end_date"]) if row.get("expected_end_date") else None,
		"percent_complete": flt(row.get("percent_complete")),
	}


def _project_tasks(project, limit):
	"""The project's open, non-group, non-template tasks: dated first by start, then undated."""
	cols = engine._task_columns()
	selected = ", ".join(f"{expr} AS `{column}`" for column, expr in cols.items())
	return frappe.db.sql(
		f"""
		SELECT
			t.name, t.subject, t.project, t.status, t.exp_start_date, t.exp_end_date,
			t.expected_time, t.color, t.modified, {selected},
			p.project_name AS project_title
		FROM `tabTask` t
		LEFT JOIN `tabProject` p ON p.name = t.project
		WHERE t.project = %(project)s
			AND IFNULL(t.is_group, 0) = 0
			AND IFNULL(t.is_template, 0) = 0
			AND IFNULL(t.status, '') NOT IN %(finished)s
		ORDER BY (t.exp_start_date IS NULL), t.exp_start_date, t.creation
		LIMIT %(limit)s
		""",
		{"project": project, "finished": engine.FINISHED_STATUSES, "limit": limit},
		as_dict=True,
	)


def _forecast(project):
	"""Phase 4's labor forecast, money only for cost roles; None when it cannot be worked out."""
	try:
		return pp.get_labor_forecast(project)
	except frappe.PermissionError:
		return None
	except Exception:
		frappe.log_error(title="Planner views: labor forecast failed", message=frappe.get_traceback())
		return None


def _actuals(names):
	"""Phase 4's ``get_actuals`` for the cards (planned from the engine with Google off), or {}."""
	if not names:
		return {}
	try:
		return pp.get_actuals(tasks=json.dumps(names)) or {}
	except Exception:
		frappe.log_error(title="Planner views: actuals failed", message=frappe.get_traceback())
		return {}


@frappe.whitelist()
def get_project_overview(project):
	"""One customer job at a glance. Read-only.

	Returns ``{"project", "on_planner", "title", "customer", "customer_name", "status",
	"project_type", "pm", "expected_start", "expected_end", "percent_complete", "today", "window":
	{"start", "end"} | None, "tasks": [card + "bar"], "undated": [card], "truncated", "limit",
	"forecast", "can_edit"}``. Each card is ``project_planner.build_card``'s, with ``planned_hours``,
	``actual_hours``, ``over_plan`` and each crew member's ``booked``/``actual`` from Phase 4's
	``get_actuals``. ``forecast`` is ``get_labor_forecast``'s answer: hours for everyone, money only
	for ``planner_tracking.COST_ROLES``. A project that is not a customer job answers
	``on_planner`` False and a ``message``, with no tasks.
	"""
	_require_viewer()
	project = pp.given(project)
	if not project or not frappe.db.exists("Project", project):
		frappe.throw(_("Project {0} does not exist.").format(frappe.utils.escape_html(str(project or ""))))
	if project not in engine.planner_projects([project]):
		return {
			"project": project,
			"on_planner": False,
			"message": _(
				"This project is not on the planner. The planners show customer jobs only: "
				"Design, Build, Service, Events and Delivery."
			),
		}
	today = getdate(nowdate())
	rows = _project_tasks(project, PROJECT_TASK_LIMIT + 1)
	truncated = len(rows) > PROJECT_TASK_LIMIT
	rows = rows[:PROJECT_TASK_LIMIT]
	can_edit = bool(frappe.has_permission("Task", "write"))
	cards = _cards(rows, today, can_edit)
	actuals = _actuals([card["name"] for card in cards])
	for card in cards:
		entry = actuals.get(card["name"]) or {}
		if not entry:
			continue
		card["planned_hours"] = flt(entry.get("planned"))
		card["actual_hours"] = flt(entry.get("actual"))
		card["over_plan"] = bool(entry.get("over_plan"))
		people = {p.get("resource"): p for p in entry.get("by_person") or [] if p.get("resource")}
		for member in card["crew"]:
			mine = people.get(member["resource"]) or {}
			member["booked"] = round(flt(mine.get("planned")), 2)
			member["actual"] = round(flt(mine.get("actual")), 2)
	dated = [card for card in cards if card.get("start")]
	undated = [card for card in cards if not card.get("start")]
	window = timeline_window(
		[(card["start"], card.get("end")) for card in dated], today, pp._site_first_weekday()
	)
	for card in dated:
		card["bar"] = timeline_bar(card["start"], card.get("end"), window) if window else None
	return {
		"project": project,
		"on_planner": True,
		**_project_info(project),
		"today": str(today),
		"window": {"start": str(window[0]), "end": str(window[1])} if window else None,
		"tasks": dated,
		"undated": undated,
		"truncated": truncated,
		"limit": PROJECT_TASK_LIMIT,
		"forecast": _forecast(project),
		"can_edit": can_edit,
	}


# ---------------------------------------------------------------------- one maintenance site


def _site_records(project):
	# Raw SQL for the plan-date expression (the Maintenance Planner's own CASE), after
	# _require_maintenance: every one of those roles reads every record.
	return frappe.db.sql(
		"""
		SELECT * FROM (
			SELECT
				name, project, customer, maintenance_contract, serial_no, visit_label,
				technician, scheduled_visit_date, visit_date, docstatus, workflow_state,
				completion_percent, has_out_of_range_readings, modified,
				planned_hours, full_day, clock_in_time, clock_out_time,
				CASE
					WHEN docstatus = 1 OR workflow_state = %(pending)s
						THEN COALESCE(visit_date, scheduled_visit_date, DATE(modified))
					ELSE COALESCE(scheduled_visit_date, visit_date)
				END AS plan_date
			FROM `tabSapphire Maintenance Record`
			WHERE docstatus < 2 AND project = %(project)s
		) visits
		ORDER BY plan_date IS NULL, plan_date DESC, name
		LIMIT 400
		""",
		{"project": project, "pending": mp.PENDING_STATE},
		as_dict=True,
	)


def _site_profile(project):
	"""The site's Maintenance Profile: its name, default technician, default crew, visit hours and
	full-day flag. Never its access codes or safety notes: the drawer only links to the profile."""
	rows = frappe.get_all(
		"Sapphire Maintenance Profile",
		filters={"project": project},
		fields=["name", "default_technician", "visit_hours", "visit_full_day"],
		limit_page_length=1,
	)
	if not rows:
		return None
	row = rows[0]
	crew = mp._default_crews([project]).get(project) or {}
	return {
		"name": row.get("name"),
		"default_technician": row.get("default_technician"),
		"crew": list(crew.get("members") or []),
		"visit_hours": flt(row.get("visit_hours")) or None,
		"full_day": bool(cint(row.get("visit_full_day"))),
	}


def _visit_entry(card, names):
	return {
		"kind": card.get("kind"),
		"name": card.get("name"),
		"key": card.get("key"),
		"date": card.get("date"),
		"status": card.get("status") or ("projected" if card.get("kind") == "projected" else None),
		"label": card.get("label"),
		"feature": card.get("feature"),
		"technician": card.get("technician"),
		"technician_name": names.get(card.get("technician")) or card.get("technician"),
		"crew": [
			{"user": m.get("user"), "name": m.get("name") or names.get(m.get("user")) or m.get("user")}
			for m in card.get("crew") or []
			if m.get("user")
		],
		"movable": bool(card.get("movable")),
		"overdue": bool(card.get("overdue")),
		"contract": card.get("contract"),
	}


@frappe.whitelist()
def get_site_overview(project):
	"""One maintenance site at a glance, for the Maintenance Planner. Read-only.

	``project`` is the site's Project. Returns ``{"project", "title", "site", "customer",
	"customer_name", "today", "profile": {"name", "default_technician", "default_technician_name",
	"crew": [{"user", "name", "hours"}], "visit_hours", "full_day"} | None, "contracts": [{"name",
	"status"}], "recent": [visit], "upcoming": [visit], "projected": [visit]}``: the last six visits
	before today, the visits from today on (and any undated draft), and the projected visits of the
	next 90 days.
	"""
	_require_maintenance()
	project = pp.given(project)
	if not project or not frappe.db.exists("Project", project):
		frappe.throw(_("Project {0} does not exist.").format(frappe.utils.escape_html(str(project or ""))))
	today = getdate(nowdate())
	info = _value("Project", project, ["project_name", "customer"])
	title = info.get("project_name") or project
	can_move = bool(frappe.has_permission("Sapphire Maintenance Record", "write"))
	cards = [mp._visit_card(row, today, can_move) for row in _site_records(project)]
	mp._attach_crews(cards)
	try:
		projected = [
			card
			for card in mp._projections(today, today + datetime.timedelta(days=SITE_AHEAD_DAYS), today)
			if card.get("project") == project
		]
		mp._decorate(projected)
	except Exception:
		frappe.log_error(title="Planner views: site projections failed", message=frappe.get_traceback())
		projected = []
	profile = _site_profile(project)
	users = {c.get("technician") for c in cards + projected} | {
		m.get("user") for c in cards + projected for m in c.get("crew") or []
	}
	if profile:
		users |= {profile.get("default_technician")} | {m.get("user") for m in profile["crew"]}
	names = mp._full_names(users)
	if profile:
		profile["default_technician_name"] = names.get(profile.get("default_technician")) or profile.get(
			"default_technician"
		)
	recent = [c for c in cards if c.get("date") and getdate(c["date"]) < today][:SITE_RECENT]
	upcoming = sorted(
		(c for c in cards if not c.get("date") or getdate(c["date"]) >= today),
		key=lambda c: (c.get("date") is None, c.get("date") or ""),
	)[:SITE_UPCOMING]
	projected = sorted(projected, key=lambda c: c.get("date") or "")[:SITE_UPCOMING]
	customer = info.get("customer")
	return {
		"project": project,
		"title": title,
		"site": mp.short_site_name(title),
		"customer": customer,
		"customer_name": _value("Customer", customer, ["customer_name"]).get("customer_name") or customer,
		"today": str(today),
		"profile": profile,
		"contracts": frappe.get_all(
			"Sapphire Maintenance Contract",
			filters={"project": project},
			fields=["name", "status"],
			order_by="modified desc",
			limit_page_length=10,
		),
		"recent": [_visit_entry(c, names) for c in recent],
		"upcoming": [_visit_entry(c, names) for c in upcoming],
		"projected": [_visit_entry(c, names) for c in projected],
	}
