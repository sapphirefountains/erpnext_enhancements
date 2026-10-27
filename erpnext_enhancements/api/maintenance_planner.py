"""Backend for the Maintenance Planner desk page (``/desk/maintenance-planner``).

The planner is a month/week calendar of maintenance visits that people move by
dragging cards. :func:`get_planner` feeds it three kinds of card:

* **Visit records** (Sapphire Maintenance Record). An open draft sits on its
  ``scheduled_visit_date``; a visit pending review or submitted sits on the day
  the work was done. Only a draft that nobody has started (no ``visit_date``)
  can be moved, and :func:`move_visit` moves it by saving the record under the
  mover's own permissions, so the workflow and the validate hooks all apply.
* **Projected visits**: the ones the nightly scheduler
  (``tasks.generate_predictive_maintenance_records``) will draft later. They are
  worked out from each Active contract's feature rows, ``next_visit_date`` then
  ``frequency``, through the same winter pause as the roll-forward
  (``maintenance_scheduling.defer_for_winter``). Without them the calendar would
  go blank a week out, because drafts only exist for the next seven days. The
  first card of each series is a stored date, the contract's next visit, so
  :func:`move_projected` can move it. The cards after it are arithmetic and stay
  put: they follow wherever the visit before them actually happens.
* **Seasonal visits** that are not drafted yet, on the 1st of their month (the
  scheduler drafts them that day). Not movable.

Moving a projected visit into the drafting window drafts it right away, on the
day it was dropped. Otherwise a visit dragged onto today would only be drafted
by tomorrow's run, which clamps an overdue date forward to tomorrow.

**Overbooking warns and never blocks**, exactly as on the Project Planner. A move
that would leave a technician over their hours, on a day off or double-booked
returns ``needs_reason`` with the conflicts and saves nothing; sent again with a
``reason`` it saves, and the reason goes on the visit's (or, for a projected
visit, the contract's) timeline. Only conflicts the move *creates* count: the
target day is checked with and without this visit, using the shared availability
engine (``project_enhancements.crew_availability``), so a day that was already
over does not ask again for a visit that changes nothing about it.

**Multi-person visits** (P1.8). A visit card carries its ``crew`` (``[{"user", "name",
"hours"}]``, the technician never among them), ``planned_hours``, ``full_day`` and ``hours``, the
technician's effective length (a full-day visit is their capacity that day). A projected card
carries the site's default crew, length and full-day flag from its Maintenance Profile.
:func:`move_visit` edits the crew, planned hours and full-day flag and checks every affected
person's day; :func:`add_crew` adds one helper. The record's on_update crew mirror
(``sapphire_maintenance.visit_crew``) moves the assignments, so nothing here touches a crew ToDo.

**Draft mode and pencil visits** (v1.601.0, C1). ``move_visit`` and ``add_crew`` take
``draft=1``: the change goes into the caller's Planner Draft Change row instead of the record
(``api/maintenance_drafts.py``), and Publish saves it through :func:`_apply_visit`, the same path a
live move takes. ``get_planner(draft=1)`` shows the drafts. ``move_projected`` and *Add visit
here* refuse in draft mode. A visit can be pencilled in (``tentative``, :func:`is_pencil`): the
engine books it softly, it never asks for a reason, and nobody is assigned, texted or (as a
customer) told its date until it is firmed up.

The pure pieces (series, site names, which feature rows a move touches) take
plain values so ``tests/test_maintenance_planner.py`` runs them without a bench.
"""

import datetime
import re

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, formatdate, getdate, nowdate

# Who may open the planner: exactly the roles that read every maintenance record
# (sapphire_maintenance.permissions.VIEW_ALL_ROLES), because the record query below
# runs as raw SQL. Kept as a literal so the bench-free tests need no permissions stub;
# test_maintenance_planner asserts the two sets agree.
PLANNER_ROLES = {"System Manager", "Projects Manager", "Maintenance Supervisor", "Maintenance User"}

# A month view shows six weeks; anything much longer is a mistake or a scrape.
MAX_RANGE_DAYS = 100
# A daily series over a long range is the worst case. Far more than one range needs.
MAX_SERIES_LENGTH = 400

PENDING_STATE = "Pending Review"

# Employee designations of the people who do visits ("Junior Technician", "Senior Technician").
TECHNICIAN_DESIGNATION = "Technician"

MONTHS = [
	"January",
	"February",
	"March",
	"April",
	"May",
	"June",
	"July",
	"August",
	"September",
	"October",
	"November",
	"December",
]

# Project titles carry the contract boilerplate: "Highlands Maintenance Contract",
# "The Charles - Fountain Maintenance Contract", "Red Butte Garden Maintenance 2026".
# On a calendar card only the site name earns its space.
_SITE_SUFFIX = re.compile(
	r"\s*[-–—:]?\s*(?:fountain\s+)?(?:preventative\s+|preventive\s+)?maintenance(?:\s+contract)?(?:\s+\d{4})?\s*$",
	re.IGNORECASE,
)


# ---------------------------------------------------------------------- pure helpers


def short_site_name(title):
	"""A project title without the maintenance-contract boilerplate."""
	if not title:
		return title
	short = _SITE_SUFFIX.sub("", str(title)).strip(" -–—:")
	return short or title


def feature_label(serial_no):
	"""``MAINT-HARDWARE-East-Courtyard`` → ``East Courtyard``."""
	if not serial_no:
		return ""
	text = re.sub(r"^MAINT-[^-]+-", "", str(serial_no)).replace("-", " ").strip()
	return text or serial_no


def project_series(first, step, start, end, limit=MAX_SERIES_LENGTH):
	"""The dates of a visit series that fall between ``start`` and ``end``.

	``first`` is the next visit; ``step(date)`` returns the one after it, or
	None when the series stops. A step that fails to move forward also stops
	it, so a blank frequency can never spin.
	"""
	dates = []
	current = getdate(first) if first else None
	start, end = getdate(start), getdate(end)
	for _step in range(limit):
		if not current or current > end:
			break
		if current >= start:
			dates.append(current)
		following = step(current)
		following = getdate(following) if following else None
		if not following or following <= current:
			break
		current = following
	return dates


def make_step(contract, frequencies):
	"""``step`` for :func:`project_series`: the soonest next visit over ``frequencies``.

	One frequency for a Per Feature row. Every row's frequency for a Per Site
	Visit contract, because one site visit rolls every feature forward from the
	same day (``update_next_visit_dates``) and the site is due again as soon as
	the first of them is.
	"""
	from erpnext_enhancements.api.maintenance_scheduling import calculate_next_date, defer_for_winter

	frequencies = [f for f in frequencies if f]

	def step(day):
		candidates = [defer_for_winter(contract, calculate_next_date(day, f)) for f in frequencies]
		candidates = [getdate(c) for c in candidates if c]
		return min(candidates) if candidates else None

	return step


def series_end(contract, end):
	"""``end``, cut short at the contract's end date when it will not renew."""
	end = getdate(end)
	if contract.get("end_date") and (not contract.get("auto_renew") or contract.get("non_renewal_notice")):
		return min(end, getdate(contract.get("end_date")))
	return end


def build_projections(contract, open_drafts, seasonal, start, end, today):
	"""The projected (not yet drafted) visits for one Active contract.

	Args:
		contract: the contract doc (``covered_features``, ``visit_shape``, dates).
		open_drafts: the scheduler's dedup keys for open *regular* drafts mapped to
			that draft's date (or None): the contract name for a Per Site Visit
			contract, ``(project, serial_no)`` for a Per Feature one. An open draft
			stands for the next visit, so the series continues from it.
		seasonal: ``[{label, target_month, last_generated_year, drafted}]``.
		start, end, today: dates.

	Returns:
		list[dict] in date order. ``movable`` marks the card that is the
		contract's stored next visit; ``from_date`` is that stored date, which
		:func:`move_projected` checks before it writes.
	"""
	start, end, today = getdate(start), getdate(end), getdate(today)
	last = series_end(contract, end)
	starts_on = getdate(contract.get("start_date")) if contract.get("start_date") else None
	rows = [
		row
		for row in (contract.get("covered_features") or [])
		if row.get("frequency") or row.get("next_visit_date")
	]
	entries = []

	def add_series(rows_in_series, key, serial_no):
		dated = [getdate(row.get("next_visit_date")) for row in rows_in_series if row.get("next_visit_date")]
		if not dated:
			return
		step = make_step(contract, [row.get("frequency") for row in rows_in_series])
		if key in open_drafts:
			# The draft is the next visit; what follows it rolls from the day it is done.
			drafted_on = open_drafts.get(key)
			base = max(getdate(drafted_on), today) if drafted_on else today
			first, stored = step(base), None
		else:
			first = stored = min(dated)
			if starts_on and first < starts_on:
				first = starts_on
		for index, day in enumerate(project_series(first, step, start, last)):
			movable = bool(stored) and index == 0 and day == first
			entries.append(
				{
					"kind": "projected",
					"key": f"{contract.name}|{serial_no or ''}|{day}",
					"contract": contract.name,
					"project": contract.get("project"),
					"serial_no": serial_no,
					"frequency": ", ".join(
						sorted({row.get("frequency") for row in rows_in_series if row.get("frequency")})
					),
					"date": str(day),
					"movable": movable,
					"from_date": str(stored) if movable else None,
					"overdue": day < today,
				}
			)

	if contract.get("visit_shape") == "Per Site Visit":
		add_series(rows, contract.name, None)
	else:
		for row in rows:
			add_series([row], (contract.get("project"), row.get("serial_no")), row.get("serial_no"))

	for visit in seasonal or []:
		if visit.get("drafted") or visit.get("target_month") not in MONTHS:
			continue
		month = MONTHS.index(visit["target_month"]) + 1
		for year in range(start.year, end.year + 1):
			if (visit.get("last_generated_year") or 0) >= year:
				continue
			# The scheduler drafts it on its first run in the target month. In the
			# current month that run is the next one; a month already gone is
			# skipped for the year, so nothing would be drafted.
			day = datetime.date(year, month, 1)
			if day <= today:
				if (year, month) != (today.year, today.month):
					continue
				day = today
			if not (start <= day <= last) or (starts_on and day < starts_on):
				continue
			entries.append(
				{
					"kind": "projected",
					"key": f"{contract.name}|{visit['label']}|{day}",
					"contract": contract.name,
					"project": contract.get("project"),
					"serial_no": None,
					"label": visit["label"],
					"frequency": _("Once a year"),
					"date": str(day),
					"movable": False,
					"from_date": None,
					"overdue": False,
				}
			)

	entries.sort(key=lambda entry: entry["date"])
	return entries


def rows_to_move(contract, source, target, serial_no=None):
	"""The feature rows whose ``next_visit_date`` a projected move rewrites.

	Per Feature: the one row, and only while it still holds ``source``.
	Per Site Visit: one visit covers every feature, so the card is the site's
	soonest next date. Moving it carries every row due on that day, plus any
	row due before the new day: that visit will see to those features too.
	An empty list means the card is stale (someone moved it, or it got drafted).
	"""
	source, target = getdate(source), getdate(target)
	rows = [row for row in (contract.get("covered_features") or []) if row.get("next_visit_date")]
	if contract.get("visit_shape") == "Per Site Visit":
		if not rows or min(getdate(row.get("next_visit_date")) for row in rows) != source:
			return []
		return [
			row
			for row in rows
			if getdate(row.get("next_visit_date")) == source or getdate(row.get("next_visit_date")) < target
		]
	return [
		row
		for row in rows
		if row.get("serial_no") == serial_no and getdate(row.get("next_visit_date")) == source
	]


# ---------------------------------------------------------------------- endpoints


def _require_planner():
	if frappe.session.user == "Administrator":
		return
	if not PLANNER_ROLES & set(frappe.get_roles()):
		frappe.throw(_("Only maintenance staff can use the planner."), frappe.PermissionError)


@frappe.whitelist()
def get_planner(start, end, draft=0):
	"""Every card between ``start`` and ``end`` (inclusive), plus the unscheduled drafts.

	Technician filtering happens in the browser: the whole schedule is a few
	hundred cards at most, and switching people should not cost a round trip.

	``draft=1`` (v1.601.0, draft mode) overlays the caller's unpublished visit drafts
	(``api/maintenance_drafts.py``): a drafted visit's card shows the drafted day, people, length
	and pencil flag with ``drafted: True``, the engine's day cells count it there, and ``drafts``
	says how many there are. Without it the answer is exactly what it always was.
	"""
	_require_planner()
	start, end = getdate(start), getdate(end)
	if end < start:
		frappe.throw(_("The end date is before the start date."))
	if date_diff(end, start) > MAX_RANGE_DAYS:
		frappe.throw(_("Pick a range of {0} days or less.").format(MAX_RANGE_DAYS))
	today = getdate(nowdate())

	can_move_visits = bool(frappe.has_permission("Sapphire Maintenance Record", "write"))
	can_move_projected = bool(frappe.has_permission("Sapphire Maintenance Contract", "write"))

	overlay = None
	if _truthy(draft):
		from erpnext_enhancements.api import maintenance_drafts

		overlay = maintenance_drafts.draft_overlay()

	visits = [_visit_card(row, today, can_move_visits) for row in _records_between(start, end)]
	unscheduled = [_visit_card(row, today, can_move_visits) for row in _unscheduled_drafts()]
	_attach_crews(visits + unscheduled)
	if overlay is not None:
		visits, unscheduled = maintenance_drafts.with_drafts(
			visits, unscheduled, overlay, start, end, today, can_move_visits
		)
	projected = _projections(start, end, today)
	for card in projected:
		card["movable"] = card["movable"] and can_move_projected
	moves, unscheduled_moves = _move_cards(start, end, today)

	_decorate(visits + unscheduled + projected)
	view = _project_view(
		_technicians(visits + unscheduled + projected + moves + unscheduled_moves),
		start,
		end,
		visit_overrides=(overlay or {}).get("engine"),
	)
	_drop_move_bookings(view["bookings"], moves + unscheduled_moves)
	_set_lead_hours(visits + unscheduled + projected, view["bookings"])
	extra = {"drafts": maintenance_drafts.summary(overlay)} if overlay is not None else {}
	return {
		**extra,
		"start": str(start),
		"end": str(end),
		"today": str(today),
		"visits": visits,
		"unscheduled": unscheduled,
		"projected": projected,
		# Fountain Move tasks (v1.703.0): moved here, read-only on the Project Planner.
		"moves": moves,
		"unscheduled_moves": unscheduled_moves,
		"technicians": view["technicians"],
		"bookings": view["bookings"],
		"can_move_visits": can_move_visits,
		"can_move_projected": can_move_projected,
		# Phase 6E: whether "Add visit here" is offered (api/maintenance_actions.create_visit).
		"can_create_visits": bool(frappe.has_permission("Sapphire Maintenance Record", "create")),
		**_planner_extras(start, end, view["bookings"]),  # Phase 6D: day_notes, block_notes, can_schedule
	}


def _move_cards(start, end, today):
	"""``(dated, undated)`` Fountain Move task cards (``project_enhancements.fountain_move_jobs``).

	Movable for anyone who may write Tasks. Never raises: a failure is logged and the calendar shows
	its visits without the moves, because the visits come first here.
	"""
	try:
		from erpnext_enhancements.project_enhancements import fountain_move_jobs

		return fountain_move_jobs.planner_cards(
			start, end, today, bool(frappe.has_permission("Task", "write"))
		)
	except Exception:
		_log_failure("Maintenance Planner: Fountain Move cards failed")
		return [], []


def _drop_move_bookings(bookings, moves):
	"""Take the move tasks out of the read-only project bookings: each has a card of its own.

	Only the cards (``items``) go; the day's capacity, booked and free hours still count them.
	"""
	refs = {card.get("name") for card in moves or [] if card.get("name")}
	if not refs:
		return
	for per_day in (bookings or {}).values():
		for cell in (per_day or {}).values():
			cell["items"] = [
				item for item in cell.get("items") or [] if not (item.get("kind") == "task" and item.get("ref") in refs)
			]


@frappe.whitelist(methods=["POST"])
def move_fountain_move(task, modified, date=None, crew=None, reason=None):
	"""Move a Fountain Move task to ``date`` and/or set its crew (a JSON list of users, lead first).

	The Project Planner's own write does the work (``fountain_move_jobs.move_task``), so it answers
	the same way: ``needs_reason`` with the conflicts when the change creates one and no ``reason``
	came with it, otherwise the saved task. Overbooking warns and never blocks.
	"""
	_require_planner()
	from erpnext_enhancements.project_enhancements import fountain_move_jobs

	if date and getdate(date) < getdate(nowdate()):
		frappe.throw(_("Move jobs can only be moved to today or a later day."))
	return fountain_move_jobs.move_task(task, modified, date=date, crew=crew, reason=reason)


# Booking kinds the Maintenance Planner shows read-only. Visits are its own cards; the engine's
# "drive" padding is not a card either, it only shows as the day's drive time.
FOREIGN_BOOKING_KINDS = ("task", "rental", "travel")

# The Planner Resource group whose people help with visits (Korben, Jesse and Daniel are Field
# techs on a project team). They are offered as technicians so a visit can be handed to them.
HELPER_GROUP = "Field"

# Day-cell keys the engine adds for the Project Planner's routes. Passed through when present, so
# this page needs no change when they are absent (an older engine) and none when they arrive.
DRIVE_KEYS = ("drive_minutes", "drive_source", "long_drive", "unlocated")


def _project_bookings(technicians, start, end):
	"""Each technician's free hours and non-visit bookings, from the shared availability engine.

	``{user: {"YYYY-MM-DD": {"capacity", "booked", "free", "off", "conflicts", "warnings", "items"}}}``
	where ``items`` are the project tasks, rental crew tasks and travel days that use the
	technician's hours (``{kind, ref, label, project, hours, slot}``). A cell also carries
	``drive_minutes``, ``drive_source``, ``long_drive`` and ``unlocated`` whenever the engine
	provides them. Maintenance and Projects share technicians, and a technician's free hours must
	read the same in both planners, so the numbers come from
	``project_enhancements.crew_availability`` and are never computed here.

	A technician with no active Planner Resource is simply absent: the planner then shows no
	free hours for them rather than a wrong number. A failure in the engine returns ``{}``
	(logged), because a bug on the project side must never blank the maintenance calendar.
	The engine is imported lazily: it imports this module back for the visit projections.
	"""
	return _project_view(technicians, start, end)["bookings"]


def _project_view(technicians, start, end, visit_overrides=None):
	"""``{"technicians", "bookings"}``: :func:`_project_bookings` plus the people it was worked out for.

	The technicians come back with the Planner Resource they map to (``resource``, ``group``,
	``color``; None when they have none), and active Field resources that have a user and are not
	technicians yet are appended (``helper: True``): people on a project team who help with visits.
	Both come from the one engine call that provides the bookings. ``visit_overrides`` (draft mode,
	v1.601.0) puts the caller's drafted visits in place of the stored ones for that call.
	"""
	people = [
		dict(t, resource=None, group=None, color=None, employee=None, open_as=None) for t in technicians or []
	]
	users = [t["user"] for t in people if t.get("user")]
	if not users:
		return {"technicians": people, "bookings": {}}
	try:
		from erpnext_enhancements.project_enhancements.crew_availability import availability

		# Passed only with drafts, so the call (and any stand-in for it) is unchanged without them.
		data = availability(start, end, **({"visit_overrides": visit_overrides} if visit_overrides else {}))
		user_to_resource = data.get("user_to_resource") or {}
		resources = {r.get("name"): r for r in data.get("resources") or []}
		for person in people:
			resource = resources.get(user_to_resource.get(person.get("user"))) or {}
			# The Employee is what a tap on a technician's name opens (v1.751.0).
			person.update(
				resource=resource.get("name"),
				group=resource.get("group"),
				color=resource.get("color"),
				employee=resource.get("employee"),
			)
		known = {p["user"] for p in people}
		helpers = sorted(
			(
				r
				for r in resources.values()
				if r.get("group") == HELPER_GROUP and r.get("user") and r["user"] not in known
			),
			key=lambda r: str(r.get("label") or r.get("name")).lower(),
		)
		for resource in helpers:
			people.append(
				{
					"user": resource["user"],
					"name": resource.get("label") or resource["user"],
					"enabled": True,
					"resource": resource.get("name"),
					"group": resource.get("group"),
					"color": resource.get("color"),
					"employee": resource.get("employee"),
					"helper": True,
				}
			)
		# v1.751.0: what a tap on a name opens for this person, and which trips they may open.
		from erpnext_enhancements.api import planner_open

		planner_open.mark_people(people, resource_key="resource")
		planner_open.mark_bookings(planner_open.day_bookings(data.get("days")))
		out = {}
		for person in people:
			user = person["user"]
			resource = user_to_resource.get(user)
			per_day = (data.get("days") or {}).get(resource) if resource else None
			if not per_day:
				continue
			out[user] = {day: _booking_cell(cell) for day, cell in per_day.items()}
		return {"technicians": people, "bookings": out}
	except Exception:
		_log_failure("Maintenance Planner: project bookings failed")
		return {"technicians": people, "bookings": {}}


def _booking_cell(cell):
	"""One engine day cell, as the planner page reads it."""
	out = {
		"capacity": cell.get("capacity"),
		"booked": cell.get("booked"),
		"soft_booked": cell.get("soft_booked") or 0,  # Phase 6C: the team strip's pencil hours
		"free": cell.get("free"),
		"off": cell.get("off"),
		"conflicts": list(cell.get("conflicts") or []),
		"warnings": list(cell.get("warnings") or []),
		"items": [
			{
				"kind": b.get("kind"),
				"ref": b.get("ref"),
				"label": b.get("label"),
				"project": b.get("project"),
				"hours": b.get("hours"),
				"slot": b.get("slot"),
				# A pencil task is drawn as one (v1.601.0); the key is there only when it is.
				**({"tentative": True} if b.get("tentative") else {}),
				# A trip this person may not open says so (v1.751.0); the key is there only then.
				**({"open": False} if b.get("open") is False else {}),
			}
			for b in cell.get("bookings") or []
			if b.get("kind") in FOREIGN_BOOKING_KINDS
		],
		# Phase 6D: personal blocks ("Unavailable", the window, the hours), never the note.
		"blocks": [dict(b) for b in cell.get("bookings") or [] if b.get("kind") == "block"],
	}
	for key in DRIVE_KEYS:
		if key in cell:
			out[key] = cell[key]
	return out


def _planner_extras(start, end, bookings):
	"""Phase 6D: ``day_notes``, ``block_notes`` and ``can_schedule`` from ``api/planner_blocks``
	(imported late: it imports this module). ``payload_extras`` logs its own failures and never
	raises; a module that cannot even be imported is a broken deploy every Project Planner endpoint
	already shows, so here it only falls back to empty values: the visit calendar comes first."""
	try:
		from erpnext_enhancements.api import planner_blocks
	except Exception:
		return {"day_notes": {}, "block_notes": {}, "can_schedule": False}
	return planner_blocks.payload_extras(start, end, bookings)


def _log_failure(title):
	"""Log the current exception without ever raising: a logging problem must not break a planner."""
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


def has_tentative_column():
	"""Whether the visit record has its pencil column (``tentative``, v1.601.0) yet.

	The reads below are raw SQL, and a planner can load in the window between new code reaching the
	workers and ``bench migrate`` adding the column; selecting it then would blank the calendar.
	Never raises: a failed check answers False and every visit reads as firm.
	"""
	try:
		return bool(frappe.db.has_column("Sapphire Maintenance Record", "tentative"))
	except Exception:
		return False


def _tentative_select():
	return "tentative" if has_tentative_column() else "0 AS tentative"


def is_pencil(record):
	"""True when a visit (record, doc, row or card) is pencilled in (``tentative``, v1.601.0).

	A pencilled visit is soft-booked by the engine, never asks for a reason, assigns nobody, sends
	the customer no date and is left out of the technician's 6 AM digest, as a pencil Task is."""
	if record is None:
		return False
	value = record.get("tentative") if hasattr(record, "get") else getattr(record, "tentative", None)
	try:
		return bool(float(value or 0))
	except (TypeError, ValueError):
		return False


def _records_between(start, end):
	# Raw SQL for the date expression, after the role gate in _require_planner: every
	# planner role sees every record (sapphire_maintenance.permissions).
	return frappe.db.sql(
		f"""
		SELECT * FROM (
			SELECT
				name, project, customer, maintenance_contract, serial_no, visit_label,
				technician, scheduled_visit_date, visit_date, docstatus, workflow_state,
				completion_percent, has_out_of_range_readings, modified,
				planned_hours, full_day, clock_in_time, clock_out_time, {_tentative_select()},
				CASE
					WHEN docstatus = 1 OR workflow_state = %(pending)s
						THEN COALESCE(visit_date, scheduled_visit_date, DATE(modified))
					ELSE COALESCE(scheduled_visit_date, visit_date)
				END AS plan_date
			FROM `tabSapphire Maintenance Record`
			WHERE docstatus < 2
		) visits
		WHERE plan_date BETWEEN %(start)s AND %(end)s
		ORDER BY plan_date, name
		LIMIT 2000
		""",
		{"start": start, "end": end, "pending": PENDING_STATE},
		as_dict=True,
	)


def _unscheduled_drafts():
	return frappe.db.sql(
		f"""
		SELECT
			name, project, customer, maintenance_contract, serial_no, visit_label,
			technician, scheduled_visit_date, visit_date, docstatus, workflow_state,
			completion_percent, has_out_of_range_readings, modified,
			planned_hours, full_day, clock_in_time, clock_out_time, {_tentative_select()},
			NULL AS plan_date
		FROM `tabSapphire Maintenance Record`
		WHERE docstatus = 0
			AND IFNULL(workflow_state, '') != %(pending)s
			AND scheduled_visit_date IS NULL
			AND visit_date IS NULL
		ORDER BY creation
		LIMIT 200
		""",
		{"pending": PENDING_STATE},
		as_dict=True,
	)


def _visit_card(row, today, can_move):
	if row.docstatus == 1:
		status = "done"
	elif row.workflow_state == PENDING_STATE:
		status = "pending"
	else:
		status = "draft"
	day = getdate(row.plan_date) if row.plan_date else None
	return {
		"kind": "visit",
		"name": row.name,
		"key": row.name,
		"date": str(day) if day else None,
		"project": row.project,
		"contract": row.maintenance_contract,
		"customer": row.customer,
		"serial_no": row.serial_no,
		"label": row.visit_label,
		"technician": row.technician,
		"status": status,
		"completion": row.completion_percent or 0,
		"flagged": bool(row.has_out_of_range_readings),
		# Started visits (a visit_date) record work already done or underway.
		"movable": bool(can_move and status == "draft" and not row.visit_date),
		"started": bool(row.visit_date),
		"modified": str(row.modified),
		"overdue": bool(status == "draft" and day and day < today),
		# Multi-person visits (P1.8). ``crew`` is filled in by _attach_crews (one query for every
		# card) and ``hours``, the lead's effective length, by _set_lead_hours once the engine's
		# day cells are known (a full-day visit is the lead's capacity that day).
		"crew": [],
		"planned_hours": _positive(row.get("planned_hours")),
		"full_day": bool(row.get("full_day")),
		"hours": None,
		"clocked_hours": _clocked_hours(row.get("clock_in_time"), row.get("clock_out_time")),
		# v1.601.0: a pencilled visit (soft-booked, nobody assigned, nobody told).
		"tentative": is_pencil(row),
	}


def _projections(start, end, today, projects=None):
	"""The projected visits of every Active contract, or (``projects``, a list of Project names) only of the
	contracts on those projects: Simple mode (``planner_simple``) projects one page of sites, not every contract."""
	filters = {"status": "Active"}
	if projects is not None:
		if not projects:
			return []
		filters["project"] = ["in", list(projects)]

	from erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract import (
		iter_seasonal_visits,
	)

	contracts = frappe.get_all("Sapphire Maintenance Contract", filters=filters, pluck="name")
	if not contracts:
		return []

	open_drafts = {}
	open_labels = set()
	for draft in frappe.get_all(
		"Sapphire Maintenance Record",
		filters={"docstatus": 0},
		fields=["maintenance_contract", "project", "serial_no", "visit_label", "scheduled_visit_date"],
	):
		if draft.visit_label:
			open_labels.add((draft.maintenance_contract, draft.visit_label))
			continue
		# The scheduler's two dedup keys (tasks.generate_predictive_maintenance_records).
		for key in (draft.maintenance_contract, (draft.project, draft.serial_no)):
			if key and (key not in open_drafts or not open_drafts[key]):
				open_drafts[key] = draft.scheduled_visit_date

	held = set(
		frappe.get_all("Customer", filters={"custom_service_hold": 1}, pluck="name")
		if frappe.db.has_column("Customer", "custom_service_hold")
		else []
	)

	entries = []
	for name in contracts:
		contract = frappe.get_doc("Sapphire Maintenance Contract", name)
		seasonal = [
			{
				"label": visit["label"],
				"target_month": visit["target_month"],
				"last_generated_year": visit["last_generated_year"],
				"drafted": (contract.name, visit["label"]) in open_labels,
			}
			for visit in iter_seasonal_visits(contract)
		]
		for entry in build_projections(contract, open_drafts, seasonal, start, end, today):
			entry["on_hold"] = contract.customer in held
			entry["customer"] = contract.customer
			entries.append(entry)
	return entries


def _decorate(cards):
	"""Site names, feature names, default technicians and default crews, one query each.

	A projected card also takes the site's default crew, visit length and full-day flag from its
	Maintenance Profile (P1.8: ``crew``, ``planned_hours``, ``full_day``), which the availability
	engine reads to book every person on a visit that is not drafted yet.
	"""
	from erpnext_enhancements.api.maintenance_dispatch import default_technician_for

	projects = {card["project"] for card in cards if card.get("project")}
	titles = (
		dict(
			frappe.get_all(
				"Project",
				filters={"name": ["in", list(projects)]},
				fields=["name", "project_name"],
				as_list=True,
			)
		)
		if projects
		else {}
	)
	defaults = {project: default_technician_for(project) for project in projects}
	crews = _default_crews(
		{card["project"] for card in cards if card["kind"] == "projected" and card.get("project")}
	)
	for card in cards:
		title = titles.get(card.get("project")) or card.get("project") or card.get("customer") or ""
		card["site_full"] = title
		card["site"] = short_site_name(title)
		card["feature"] = feature_label(card.get("serial_no"))
		if card["kind"] == "projected":
			card["technician"] = defaults.get(card.get("project"))
			crew = crews.get(card.get("project")) or {}
			card["crew"] = [
				dict(member)
				for member in crew.get("members") or []
				if member.get("user") and member.get("user") != card["technician"]
			]
			card["planned_hours"] = crew.get("hours")
			card["full_day"] = bool(crew.get("full_day"))
			card.setdefault("hours", None)


def _default_crews(projects):
	"""``{project: {"members": [{"user", "name", "hours"}], "hours", "full_day"}}`` from the profiles.

	Never raises: a crew lookup going wrong leaves projected visits with their technician alone,
	which is what they showed before P1.8.
	"""
	if not projects:
		return {}
	try:
		from erpnext_enhancements.api.maintenance_dispatch import default_crews_for

		found = default_crews_for(sorted(projects)) or {}
		users = sorted({row["user"] for crew in found.values() for row in crew.get("rows") or []})
		names = _full_names(users)
		return {
			project: {
				"members": [
					{
						"user": row["user"],
						"name": names.get(row["user"]) or row["user"],
						"hours": row.get("hours"),
					}
					for row in crew.get("rows") or []
				],
				"hours": crew.get("hours"),
				"full_day": crew.get("full_day"),
			}
			for project, crew in found.items()
		}
	except Exception:
		_log_failure("Maintenance Planner: default crews failed")
		return {}


def _full_names(users):
	users = sorted({u for u in users or [] if u})
	if not users:
		return {}
	return {
		row.get("name"): row.get("full_name") or row.get("name")
		for row in frappe.get_all("User", filters={"name": ["in", users]}, fields=["name", "full_name"])
	}


def _attach_crews(cards):
	"""Fill each visit card's ``crew`` (``[{"user", "name", "hours"}]``) with one query for all of them.

	``hours`` is the person's own hours when set, else None: the visit's length. Never raises: a
	failure leaves the cards with an empty crew (logged), and the calendar stands.
	"""
	names = [card["name"] for card in cards if card.get("kind") == "visit" and card.get("name")]
	if not names:
		return
	try:
		from erpnext_enhancements.sapphire_maintenance.visit_crew import crew_rows_for, explicit_hours

		crews = crew_rows_for(names)
		for card in cards:
			card["crew"] = [
				{
					"user": row["user"],
					"name": row.get("full_name") or row["user"],
					"hours": explicit_hours(row.get("hours")),
				}
				for row in crews.get(card.get("name")) or []
				if row["user"] != card.get("technician")
			]
	except Exception:
		_log_failure("Maintenance Planner: visit crews failed")


def _positive(value):
	"""A Float field's value when set: blank reads back as 0, so only > 0 is a value."""
	try:
		value = float(value or 0)
	except (TypeError, ValueError):
		return None
	return round(value, 2) if value > 0 else None


def _clocked_hours(clock_in, clock_out):
	"""The clocked length of a visit with both times, else None."""
	if not clock_in or not clock_out:
		return None
	def parse(value):
		if isinstance(value, datetime.datetime):
			return value
		return datetime.datetime.fromisoformat(str(value)[:19])

	try:
		start, end = parse(clock_in), parse(clock_out)
	except ValueError:
		return None
	return round((end - start).total_seconds() / 3600, 2) if end > start else None


def _hour_settings():
	"""``(maintenance visit hours, full-day hours)`` from Project Planner Settings, with defaults."""
	visit_hours, day_hours = 2.0, 8.0
	try:
		from erpnext_enhancements.project_enhancements.crew_availability import get_settings

		settings = get_settings() or {}
		if settings.get("maintenance_visit_hours") is not None:
			visit_hours = float(settings.get("maintenance_visit_hours"))
		if settings.get("default_day_hours") is not None:
			day_hours = float(settings.get("default_day_hours"))
	except Exception:
		_log_failure("Maintenance Planner: settings failed")
	return visit_hours, day_hours


def person_visit_hours(person_hours, full_day, planned_hours, visit_hours, capacity, day_hours, clocked=None):
	"""What one person books for a visit, the engine's rule (``crew_availability.visit_person_hours``).

	Their own hours when set; a clocked length (the technician only); on a full-day visit their
	capacity that day, or the full-day hours when that is 0; the visit's planned hours; else the
	Settings default. Plain values, so the bench-free tests check it against the engine.
	"""
	own = _positive(person_hours)
	if own is not None:
		return own
	if clocked is not None:
		return clocked
	if full_day:
		capacity = float(capacity or 0)
		return round(capacity if capacity > 0 else float(day_hours or 0), 2)
	planned = _positive(planned_hours)
	return planned if planned is not None else round(float(visit_hours or 0), 2)


def _set_lead_hours(cards, bookings):
	"""Each card's ``hours``: the effective length for its technician (P1.8).

	A full-day visit is the technician's capacity that day, taken from the engine's day cells the
	planner already has (``bookings``), so no second engine call.
	"""
	if not cards:
		return
	visit_hours, day_hours = _hour_settings()
	for card in cards:
		lead = card.get("technician")
		cell = ((bookings or {}).get(lead) or {}).get(card.get("date") or "") or {}
		card["hours"] = person_visit_hours(
			None,
			card.get("full_day"),
			card.get("planned_hours"),
			visit_hours,
			cell.get("capacity"),
			day_hours,
			clocked=card.get("clocked_hours"),
		)


def _technicians(cards):
	"""The people visits go to, plus anyone already on a card.

	Holding the Maintenance User role is not enough: on this site the office
	holds it too (Lisa reviews visits before billing, she does not do them), as
	do service accounts. A technician is an active employee whose designation
	says so (Junior/Senior Technician) and who holds the role.
	"""
	holders = frappe.get_all(
		"Has Role", filters={"role": "Maintenance User", "parenttype": "User"}, pluck="parent"
	)
	staff = set(
		frappe.get_all(
			"Employee",
			filters={
				"user_id": ["in", holders or [""]],
				"status": "Active",
				"designation": ["like", f"%{TECHNICIAN_DESIGNATION}%"],
			},
			pluck="user_id",
		)
	)
	staff |= {card["technician"] for card in cards if card.get("technician")}
	# Everyone on a visit's crew gets a row too (P1.8), so the crew view can show a multi-person
	# visit in each of their rows.
	staff |= {m["user"] for card in cards for m in card.get("crew") or [] if m.get("user")}
	if not staff:
		return []
	users = frappe.get_all(
		"User",
		filters={"name": ["in", list(staff)]},
		fields=["name", "full_name", "enabled"],
		order_by="full_name asc",
	)
	return [{"user": u.name, "name": u.full_name or u.name, "enabled": bool(u.enabled)} for u in users]


@frappe.whitelist()
def move_visit(
	record,
	date=None,
	technician=None,
	modified=None,
	reason=None,
	crew=None,
	planned_hours=None,
	full_day=None,
	from_user=None,
	tentative=None,
	notify=None,
	draft=0,
):
	"""Reschedule an open draft visit, hand it to someone else, or change who is on it.

	Saved through the record under the caller's permissions, so the workflow
	and both validate warnings run (time off, training). Their messages come
	back as ``warnings`` for the planner to show beside the card, rather than
	as a dialog after every drag.

	Multi-person visits (P1.8), all optional:

	* ``crew``: the whole crew, as a JSON list of users or of ``{"user", "hours"}`` rows
	  (``hours`` blank: the visit's length). Replaces the crew; a person who stays keeps
	  their row. The technician is never also crew.
	* ``planned_hours`` (blank or 0: Settings' maintenance visit hours) and ``full_day``.
	* ``from_user`` with ``technician``: the crew view's handover. When ``from_user`` is a
	  helper (on the crew, not the technician) only THAT helper is swapped for
	  ``technician``; without it, or when it is the technician, the technician is replaced.
	  Assignments follow through the record's on_update crew mirror.

	Overbooking never blocks. The conflict check runs for **every affected person** on the
	visit's day: everyone on it when the date or the visit's length changes, otherwise each
	person newly on it or whose own hours changed. Only conflicts the move *creates* count.
	When there are any, nothing is saved and the answer is ``{"needs_reason": True,
	"conflicts": {person: ["YYYY-MM-DD: Over by 2h"]}}``. Sent again with a ``reason`` it
	saves and the reason goes on the visit's timeline.

	``tentative`` (v1.601.0) pencils the visit in (1) or firms it up (0). A pencilled visit is
	soft-booked, never conflicts and assigns nobody (see :func:`is_pencil`); firming one up checks
	everyone on it, like a move.

	``draft=1`` (v1.601.0, draft mode) changes nothing on the record: the change goes into the
	caller's Planner Draft Change row for the visit (``maintenance_drafts.draft_move``) and is saved
	through :func:`_apply_visit`, this very path, when they publish.

	``notify`` (P3, the "Notify crew?" answer): ``1`` sends the people whose visit moved, or who
	came on or off it, the Maintenance Planner's publish notice (never the person editing), ``0``
	sends nothing, ``auto`` notifies when the visit's day before or after is within the next 14
	days. The answer carries ``"notified"``. Unset, nothing changes. Ignored with ``draft=1``.

	Returns ``{"name", "date", "technician", "crew": [{"user", "name", "hours"}],
	"planned_hours", "full_day", "tentative", "modified", "warnings"}``, plus ``conflicts`` when
	saved over some.
	"""
	_require_planner()
	edits = {
		"date": date,
		"technician": technician,
		"crew": crew,
		"planned_hours": planned_hours,
		"full_day": full_day,
		"from_user": from_user,
		"tentative": tentative,
	}
	if _truthy(draft):
		from erpnext_enhancements.api import maintenance_drafts

		return maintenance_drafts.draft_move(record, modified, **edits)
	doc = frappe.get_doc("Sapphire Maintenance Record", record)
	doc.check_permission("write")
	_refuse_finished(doc)
	_check_fresh(doc, modified)
	from erpnext_enhancements.api.maintenance_drafts import _given

	if _given(notify) is None:
		return _apply_visit(doc, reason=reason, **edits)
	return _apply_visit_notified(doc, notify, reason=reason, **edits)


def _apply_visit_notified(doc, notify, reason=None, **edits):
	""":func:`_apply_visit` with a "Notify crew?" answer (P3): the visit as the engine reads it
	before and after (``maintenance_drafts.engine_row``) goes to that planner's own publish notice,
	so a live move with Notify tells people exactly what publishing the same draft would."""
	from erpnext_enhancements.api import maintenance_drafts

	before = maintenance_drafts.engine_row(doc)
	return _with_visit_notice(doc, notify, before, _apply_visit(doc, reason=reason, **edits))


def _with_visit_notice(doc, notify, before, result):
	"""``result`` with ``"notified"``: the visit's publish notice to the people it moved when the
	"Notify crew?" answer is Notify (``before`` is ``maintenance_drafts.engine_row`` before the edit)."""
	from erpnext_enhancements.api import maintenance_drafts
	from erpnext_enhancements.api.project_planner import notify_choice

	if result.get("needs_reason"):
		return result
	after = maintenance_drafts.engine_row(doc)
	spans = [(row["plan_date"], row["plan_date"]) for row in (before, after) if row.get("plan_date")]
	choice = notify_choice(notify, spans, getdate(nowdate()))
	plan = {"name": doc.name, "subject": maintenance_drafts._subject(doc), "before": before, "after": after}
	result["notified"] = maintenance_drafts._notify_publish([plan]) if choice else 0
	return result


def _apply_visit(doc, reason=None, **edits):
	"""Edit a visit (:func:`_edit_visit`), check the people it affects, and save it.

	The one save path of :func:`move_visit` and of a published draft
	(``maintenance_drafts.publish_drafts``), so a published draft is checked exactly like a drag.
	Answers ``{"needs_reason": True, "conflicts"}`` unsaved when the change creates conflicts and
	no ``reason`` came; otherwise :func:`_visit_result`.
	"""
	before = _hours_specs(doc)
	edit = _edit_visit(doc, **edits)
	conflicts = {}
	after = _hours_specs(doc)
	if edit["date_changed"] or edit["length_changed"] or edit["firmed"]:
		affected = list(after)
	else:
		affected = [user for user, spec in after.items() if user not in before or before[user] != spec]
	if edit["changed"] and affected and doc.scheduled_visit_date:
		conflicts = _people_conflicts(doc, affected)
		reason = (reason or "").strip()
		if conflicts and not reason:
			return {"needs_reason": True, "conflicts": conflicts}

	warnings = []
	if edit["changed"]:
		warnings = _save_with_warnings(doc)
		if edit["prior"] is not None:
			_move_assignment(doc, edit["prior"])
		if conflicts and reason:
			_comment_conflict(doc, conflicts, reason)

	result = _visit_result(doc, warnings)
	if conflicts:
		result["conflicts"] = conflicts
	return result


def _edit_visit(
	doc,
	date=None,
	technician=None,
	crew=None,
	planned_hours=None,
	full_day=None,
	from_user=None,
	tentative=None,
):
	"""Apply one planner change to a visit in memory; nothing is saved.

	Shared by the live save (:func:`_apply_visit`) and draft mode (``maintenance_drafts``), so a
	drafted change and a live one can never drift apart. Raises (``frappe.throw``) on what the
	planner refuses: a started visit's date, a day that has passed, an inactive person, a helper
	who is no longer on the visit. Returns ``{"changed", "date_changed", "length_changed",
	"firmed", "prior"}``: ``prior`` is the technician replaced (``""`` for nobody), else None.
	"""
	from erpnext_enhancements.sapphire_maintenance.visit_crew import set_crew

	changed = False
	date_changed = False
	length_changed = False
	firmed = False
	if date:
		target = getdate(date)
		if doc.get("visit_date"):
			frappe.throw(
				_("This visit was started for {0}. Change its date on the visit form.").format(
					formatdate(doc.visit_date)
				)
			)
		if target < getdate(nowdate()):
			frappe.throw(_("Pick today or a later day."))
		if not doc.scheduled_visit_date or getdate(doc.scheduled_visit_date) != target:
			doc.scheduled_visit_date = target
			changed = date_changed = True

	prior = None
	if technician is not None:
		new = technician or None
		if new:
			_require_active(new)
		if from_user and from_user != doc.get("technician"):
			# A helper's chip dragged to another person's row: swap that helper only.
			current = _crew_entries(doc)
			if from_user not in [entry["user"] for entry in current]:
				frappe.throw(
					_("{0} is no longer on this visit. Refresh the planner and try again.").format(from_user)
				)
			entries = []
			for entry in current:
				if entry["user"] == from_user:
					if new:
						entries.append({"user": new, "hours": entry["hours"]})
				else:
					entries.append(entry)
			if set_crew(doc, entries):
				changed = True
		elif new != (doc.get("technician") or None):
			prior = doc.get("technician") or ""
			doc.technician = new
			changed = True

	if crew is not None:
		entries = _parse_crew(crew)
		for entry in entries:
			_require_active(entry["user"])
		if set_crew(doc, entries):
			changed = True

	if planned_hours is not None:
		value = _positive(planned_hours) or 0
		if value != (_positive(doc.get("planned_hours")) or 0):
			doc.planned_hours = value
			changed = length_changed = True

	if full_day is not None:
		flag = 1 if _truthy(full_day) else 0
		if flag != (1 if doc.get("full_day") else 0):
			doc.full_day = flag
			changed = length_changed = True

	if tentative is not None:
		flag = 1 if _truthy(tentative) else 0
		if flag != (1 if is_pencil(doc) else 0):
			# doc.set, not an attribute: on a site migrated before the field existed this only
			# carries a value the save ignores, and the planner offers no pencil there anyway.
			doc.set("tentative", flag)
			changed = True
			firmed = not flag

	return {
		"changed": changed,
		"date_changed": date_changed,
		"length_changed": length_changed,
		"firmed": firmed,
		"prior": prior,
	}


@frappe.whitelist(methods=["POST"])
def add_crew(record, user, modified=None, reason=None, hours=None, notify=None, draft=0):
	"""Add a helper to a visit's crew (P1.8): a technician chip dropped on a visit.

	Refuses a finished or started visit, and a projected one (it is not a record yet:
	"Set the site's default crew on its Maintenance Profile"). Adding the technician or
	someone already on the crew changes nothing. The new person's day is checked like any
	move: only a conflict this creates counts, and it answers ``needs_reason`` until sent
	with a ``reason``. Their assignment comes from the record's on_update crew mirror.

	``draft=1`` (v1.601.0): the visit's drafted crew grows instead and nothing is saved
	(``maintenance_drafts.draft_add_crew``); publishing saves the whole drafted crew through
	:func:`_apply_visit`, which checks the new person's day exactly as this does.

	Returns the same shape as :func:`move_visit`.

	``notify`` (P4, the page's "Notify crew?" answer): as :func:`move_visit` takes it. Unset, nothing
	changes; ignored with ``draft=1``.
	"""
	_require_planner()
	if "|" in str(record or "") or not frappe.db.exists("Sapphire Maintenance Record", record):
		if "|" in str(record or "") or frappe.db.exists("Sapphire Maintenance Contract", record):
			frappe.throw(
				_(
					"This visit is not drafted yet, so it has no crew of its own. Set the site's default "
					"crew on its Maintenance Profile."
				)
			)
		frappe.throw(_("Visit {0} was not found.").format(record))
	if _truthy(draft):
		from erpnext_enhancements.api import maintenance_drafts

		return maintenance_drafts.draft_add_crew(record, user, modified, hours)
	doc = frappe.get_doc("Sapphire Maintenance Record", record)
	doc.check_permission("write")
	_refuse_finished(doc)
	if doc.get("visit_date"):
		frappe.throw(
			_("This visit was started for {0}. Change who is on it on the visit form.").format(
				formatdate(doc.visit_date)
			)
		)
	_check_fresh(doc, modified)
	if not user:
		frappe.throw(_("Pick a person to add."))
	_require_active(user)

	from erpnext_enhancements.sapphire_maintenance.visit_crew import set_crew

	current = _crew_entries(doc)
	if user == doc.get("technician") or user in [entry["user"] for entry in current]:
		return _visit_result(doc, [])

	from erpnext_enhancements.api import maintenance_drafts
	from erpnext_enhancements.api.maintenance_drafts import _given

	before = maintenance_drafts.engine_row(doc) if _given(notify) is not None else None
	set_crew(doc, [*current, {"user": user, "hours": hours}])
	conflicts = {}
	if doc.get("scheduled_visit_date"):
		conflicts = _people_conflicts(doc, [user])
		reason = (reason or "").strip()
		if conflicts and not reason:
			return {"needs_reason": True, "conflicts": conflicts}

	warnings = _save_with_warnings(doc)
	if conflicts and reason:
		_comment_conflict(doc, conflicts, reason)
	result = _visit_result(doc, warnings)
	if conflicts:
		result["conflicts"] = conflicts
	return result if before is None else _with_visit_notice(doc, notify, before, result)


def _refuse_finished(doc):
	state = doc.get("workflow_state") or "Draft"
	if doc.docstatus != 0 or state == PENDING_STATE:
		frappe.throw(
			_("{0} is already finished ({1}), so it stays on the day it was done.").format(doc.name, _(state))
		)


def _check_fresh(doc, modified):
	if modified and str(doc.modified) != str(modified):
		frappe.throw(
			_("This visit was changed by someone else since the planner loaded. Refresh and try again."),
			title=_("Visit Out of Date"),
		)


def _require_active(user):
	if not frappe.db.get_value("User", user, "enabled"):
		frappe.throw(_("{0} is not an active user.").format(user))


def _truthy(value):
	if isinstance(value, str):
		return value.strip().lower() in ("1", "true", "yes", "on")
	return bool(value)


def _parse_crew(crew):
	"""The page's ``crew`` (JSON: users, or ``{"user", "hours"}`` rows) as ``[{"user", "hours"}]``."""
	if isinstance(crew, str):
		crew = frappe.parse_json(crew) if crew.strip() else []
	entries = []
	for item in crew or []:
		if isinstance(item, str):
			user, hours = item, None
		elif isinstance(item, dict):
			user, hours = item.get("user"), item.get("hours")
		else:
			continue
		if user and user not in [entry["user"] for entry in entries]:
			entries.append({"user": user, "hours": _positive(hours)})
	return entries


def _crew_entries(doc):
	"""The visit's crew as ``[{"user", "hours"}]`` (``hours`` None when blank), technician excluded."""
	out = []
	for row in doc.get("crew") or []:
		user = row.get("user")
		if user and user != doc.get("technician") and user not in [entry["user"] for entry in out]:
			out.append({"user": user, "hours": _positive(row.get("hours"))})
	return out


def _hours_specs(doc):
	"""``{person: their own hours or None}`` for everyone on the visit, the technician first."""
	specs = {}
	if doc.get("technician"):
		specs[doc.get("technician")] = None
	for entry in _crew_entries(doc):
		specs.setdefault(entry["user"], entry["hours"])
	return specs


def _people_conflicts(doc, users):
	"""Conflicts for putting this visit on each of ``users``' day, merged into one dict.

	Always empty for a pencilled visit (v1.601.0): a pencil is soft-booked and never needs a
	reason, the engine's rule for a pencil Task (``crew_availability.preview_conflicts``)."""
	if is_pencil(doc):
		return {}
	specs = _hours_specs(doc)
	planned = _positive(doc.get("planned_hours"))
	full_day = bool(doc.get("full_day"))
	out = {}
	for user in users:
		own = specs.get(user)
		hours = own if own is not None else (None if full_day else planned)
		found = visit_conflicts(
			user,
			doc.scheduled_visit_date,
			ref=doc.name,
			label=doc.name,
			hours=hours,
			full_day=full_day and own is None,
		)
		for person, messages in found.items():
			out.setdefault(person, []).extend(messages)
	return out


def _save_with_warnings(doc):
	"""Save, returning the validate hooks' messages as text instead of letting them pop up."""
	log = frappe.local.message_log if isinstance(getattr(frappe.local, "message_log", None), list) else None
	before = len(log) if log is not None else 0
	doc.save()
	warnings = []
	if log is not None:
		warnings = [_message_text(message) for message in log[before:]]
		del log[before:]
	return [w for w in warnings if w]


def _visit_result(doc, warnings):
	return {
		"name": doc.name,
		"date": str(doc.scheduled_visit_date) if doc.get("scheduled_visit_date") else None,
		"technician": doc.get("technician"),
		"crew": [
			{
				"user": row.get("user"),
				"name": row.get("full_name") or row.get("user"),
				"hours": _positive(row.get("hours")),
			}
			for row in doc.get("crew") or []
			if row.get("user") and row.get("user") != doc.get("technician")
		],
		"planned_hours": _positive(doc.get("planned_hours")),
		"full_day": bool(doc.get("full_day")),
		"tentative": is_pencil(doc),
		"modified": str(doc.modified),
		"warnings": list(warnings or []),
	}


def _message_text(message):
	from frappe.utils import strip_html_tags

	if isinstance(message, str):
		try:
			message = frappe.parse_json(message)
		except Exception:
			return strip_html_tags(message)
	text = message.get("message") if isinstance(message, dict) else message
	return strip_html_tags(str(text or "")).strip()


# ---------------------------------------------------------------------- conflicts


def conflict_summary(conflicts):
	"""``{"Austin": ["2026-10-12: Over by 2h"]}`` as one sentence for a timeline comment."""
	return "; ".join(f"{label}: {', '.join(messages)}" for label, messages in (conflicts or {}).items())


def new_visit_conflicts(day_conflicts, cell, ref, hours, label=None):
	"""The conflicts that adding one visit to a technician's day creates.

	``cell`` is the engine's day cell (``capacity``, ``off``, ``bookings``); ``day_conflicts`` is
	``crew_availability.day_conflicts``. The day is judged twice, without this visit and with it,
	and only the sentences the second has and the first lacks are returned, so a day that was
	already over (or already booked on a day off) does not ask again for a visit that changes
	nothing about it. A visit already on the day (same ``ref``) is taken out of both sides first.
	"""
	capacity, off = cell.get("capacity"), cell.get("off")
	others = [b for b in cell.get("bookings") or [] if not (b.get("kind") == "visit" and b.get("ref") == ref)]
	visit = {
		"kind": "visit",
		"ref": ref,
		"label": label,
		"project": None,
		"hours": hours,
		"slot": None,
		"estimated": True,
	}
	known = set(day_conflicts(capacity, off, others))
	return [text for text in day_conflicts(capacity, off, [*others, visit]) if text not in known]


def visit_conflicts(user, day, ref, label=None, hours=None, full_day=False):
	"""``{technician: ["YYYY-MM-DD: ..."]}`` for putting one visit on ``user``'s ``day``; {} when clear.

	Worked out by the shared availability engine for just that technician's Planner Resource.
	A technician with no resource has no hours to overbook, and an engine failure must never
	stop a move (it is logged and the move goes ahead): both answer {}.

	``hours`` None is Settings' maintenance visit hours; with ``full_day`` it is the person's whole
	day instead, their capacity that day or Settings' full-day hours when that is 0 (P1.8, the
	engine's own rule for a full-day visit).
	"""
	try:
		from erpnext_enhancements.project_enhancements.crew_availability import (
			availability,
			day_conflicts,
			get_settings,
		)

		resource = frappe.get_all(
			"Planner Resource",
			filters={"user": user, "is_active": 1},
			fields=["name", "resource_name"],
			limit_page_length=1,
		)
		if not resource:
			return {}
		name, label_of = resource[0].get("name"), resource[0].get("resource_name")
		data = availability(day, day, [name])
		cell = ((data.get("days") or {}).get(name) or {}).get(str(getdate(day)))
		if not cell:
			return {}
		if hours is None and full_day:
			capacity = float(cell.get("capacity") or 0)
			day_hours = get_settings().get("default_day_hours")
			hours = capacity if capacity > 0 else (8.0 if day_hours is None else float(day_hours))
		if hours is None:
			hours = get_settings().get("maintenance_visit_hours")
			hours = 2.0 if hours is None else hours
		new = new_visit_conflicts(day_conflicts, cell, ref, hours, label)
		if not new:
			return {}
		return {label_of or user: [f"{getdate(day)}: {text}" for text in new]}
	except Exception:
		_log_failure("Maintenance Planner: conflict check failed")
		return {}


def _comment_conflict(doc, conflicts, reason):
	doc.add_comment(
		"Comment",
		_("Scheduled over a conflict on the Maintenance Planner: {0}. Reason: {1}").format(
			frappe.utils.escape_html(conflict_summary(conflicts)), frappe.utils.escape_html(reason)
		),
	)


def _move_assignment(doc, prior):
	"""Follow the technician change with the dispatch assignment (ToDo + share).

	Best-effort, like the dispatcher's own (``maintenance_dispatch``): a missing
	or duplicate assignment is logged, never raised into the move.

	A pencilled visit assigns nobody (v1.601.0, as ``crew_sync`` does for a pencil Task): the new
	technician is not assigned until the visit is firmed up, when the crew mirror
	(``visit_crew.on_record_update``) assigns everyone on it. The old one still loses theirs.
	"""
	from frappe.desk.form.assign_to import remove

	from erpnext_enhancements.api.maintenance_dispatch import assign_to_technician

	# A technician handed a visit who stays on it as crew (the page sent them in ``crew``) keeps
	# their ToDo: the crew mirror leaves it alone, and so must this.
	staying = prior and prior in [row.get("user") for row in doc.get("crew") or []]
	if prior and not staying:
		try:
			remove(doc.doctype, doc.name, prior)
		except Exception:
			frappe.log_error(
				title="Maintenance Planner: unassign failed",
				reference_doctype=doc.doctype,
				reference_name=doc.name,
			)
	if doc.technician and not is_pencil(doc):
		assign_to_technician(doc.name, doc.technician)


@frappe.whitelist()
def move_projected(contract, from_date, to_date, serial_no=None, reason=None, draft=0):
	"""Move a contract's next (not yet drafted) visit to another day.

	Refused in draft mode (``draft=1``, v1.601.0, ``maintenance_drafts.PROJECTED_REFUSAL``): it
	rewrites the contract's feature rows and may draft a visit record on the spot, neither of which
	a Planner Draft Change of an existing visit can hold back.

	Rewrites ``next_visit_date`` on the feature rows :func:`rows_to_move` picks,
	leaves a note on the contract's timeline, and drafts the visit at once when
	the new day is inside the scheduler's drafting window.

	The visit stays with the contract's default technician (the Maintenance
	Profile's), so only its day moves. Like :func:`move_visit` it never blocks on
	overbooking: a day that would leave that technician over their hours, on a day
	off or double-booked answers ``{"needs_reason": True, "conflicts": {...}}``
	before anything is written, and with a ``reason`` it moves and the reason goes
	on the contract's timeline.

	Returns:
		dict: ``{"moved": rows rewritten, "drafted": the new record or None}``, plus
		``"conflicts"`` when the move was made over some.
	"""
	_require_planner()
	if _truthy(draft):
		from erpnext_enhancements.api import maintenance_drafts

		maintenance_drafts.refuse(maintenance_drafts.PROJECTED_REFUSAL)
	from erpnext_enhancements.tasks import MAINTENANCE_DRAFT_HORIZON_DAYS

	doc = frappe.get_doc("Sapphire Maintenance Contract", contract)
	doc.check_permission("write")
	if doc.status != "Active":
		frappe.throw(_("{0} is not an Active contract.").format(doc.name))
	source, target = getdate(from_date), getdate(to_date)
	today = getdate(nowdate())
	if target < today:
		frappe.throw(_("Pick today or a later day."))
	if _open_regular_draft_exists(doc, serial_no):
		frappe.throw(
			_("This visit has already been drafted. Refresh the planner and move the visit card instead.")
		)
	rows = rows_to_move(doc, source, target, serial_no)
	if not rows:
		frappe.throw(_("This visit has moved since the planner loaded. Refresh and try again."))
	if source == target:
		return {"moved": 0, "drafted": None}

	conflicts = _projected_conflicts(doc, serial_no, target)
	reason = (reason or "").strip()
	if conflicts and not reason:
		return {"needs_reason": True, "conflicts": conflicts}

	for row in rows:
		frappe.db.set_value("Sapphire Contract Feature", row.name, "next_visit_date", target)
		row.next_visit_date = target
	what = feature_label(serial_no) if serial_no else _("Next visit")
	doc.add_comment(
		"Info",
		_("{0} moved from {1} to {2} on the Maintenance Planner.").format(
			what,
			formatdate(source),
			formatdate(target),
		),
	)

	if conflicts:
		_comment_conflict(doc, conflicts, reason)

	drafted = None
	if target <= getdate(add_days(today, MAINTENANCE_DRAFT_HORIZON_DAYS)) and _scheduler_would_draft(
		doc, today
	):
		from erpnext_enhancements.tasks import _draft_maintenance_record

		record = _draft_maintenance_record(
			doc,
			serial_no=None if doc.visit_shape == "Per Site Visit" else serial_no,
			scheduled_date=target,
			exact_date=True,
		)
		drafted = record.name
	result = {"moved": len(rows), "drafted": drafted}
	if conflicts:
		result["conflicts"] = conflicts
	return result


def _projected_conflicts(contract, serial_no, target):
	"""Conflicts for the projected visit of ``contract`` landing on ``target``.

	For its default technician and, since P1.8, each person on the site's default crew, each
	for their own hours (the profile's crew-row hours, else its full-day flag or visit hours).
	"""
	try:
		from erpnext_enhancements.api.maintenance_dispatch import default_technician_for

		technician = default_technician_for(contract.get("project"))
	except Exception:
		_log_failure("Maintenance Planner: default technician lookup failed")
		return {}
	crew = _default_crews({contract.get("project")} if contract.get("project") else set()).get(
		contract.get("project")
	) or {}
	full_day = bool(crew.get("full_day"))
	planned = _positive(crew.get("hours"))
	people = [(technician, None)] if technician else []
	people += [(m.get("user"), _positive(m.get("hours"))) for m in crew.get("members") or [] if m.get("user")]
	out, seen = {}, set()
	ref = f"{contract.name}|{serial_no or ''}|{target}"
	for user, own in people:
		if user in seen:
			continue
		seen.add(user)
		found = visit_conflicts(
			user,
			target,
			ref=ref,
			label=contract.name,
			hours=own if own is not None else (None if full_day else planned),
			full_day=full_day and own is None,
		)
		for person, messages in found.items():
			out.setdefault(person, []).extend(messages)
	return out


def _open_regular_draft_exists(contract, serial_no):
	"""The scheduler's own dedup checks (tasks.generate_predictive_maintenance_records)."""
	if contract.visit_shape == "Per Site Visit":
		filters = {"maintenance_contract": contract.name}
	else:
		filters = {"project": contract.project, "serial_no": serial_no}
	filters.update({"visit_label": ["is", "not set"], "docstatus": 0})
	return bool(frappe.db.exists("Sapphire Maintenance Record", filters))


def _scheduler_would_draft(contract, today):
	"""The scheduler skips held customers and contracts that have not started."""
	if contract.start_date and getdate(contract.start_date) > today:
		return False
	if (
		contract.customer
		and frappe.db.has_column("Customer", "custom_service_hold")
		and frappe.db.get_value("Customer", contract.customer, "custom_service_hold")
	):
		return False
	return True
