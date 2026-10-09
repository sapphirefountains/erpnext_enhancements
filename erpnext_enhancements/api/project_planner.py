"""Backend for the Project Planner desk page (``/app/project-planner``).

The planner books people onto project Tasks by the hour. It answers two questions on one
screen: *when is each task happening and who is on it*, and *how many hours does each person
have left that day*. The second comes from ``project_enhancements.crew_availability``, the
engine the Maintenance Planner uses too, so the two planners can never disagree about who is
free (Nik, 2026-10-08).

* :func:`get_planner` returns the engine's per-person, per-day capacity for a range, a card for
  every dated task in it, the undated tasks of Active projects (the Unscheduled tray), the tasks
  short of crew, and the bookings the planner shows but does not own (``foreign``: maintenance
  visits, rental crew tasks, travel).
* :func:`save_task` is the one write: a drag, the card dialog and Undo all call it. A move that
  only gives ``start`` keeps the task's length. :func:`add_crew` (a person dropped on a card)
  and :func:`swap_crew` (a chip dropped on another person's row) build a crew and hand it to the
  same path.
* **Routes (Phase 2).** :func:`get_route` is one person's day as a drive: shop → stops in the
  order ``project_enhancements.routing`` picks → shop, with arrival times, drive minutes, a
  Google Maps link and what could not be located. :func:`suggest_dates` ranks the next days (and
  people) for a task by how much driving it would add to each person-day's route, so a task goes
  to whoever is already nearby. :func:`check_routes` asks Google whether the Routes API answers
  for this site's server key, for confirming a Cloud console change without reading a key.

Things this module is careful about, some of which look like bugs:

* **Overbooking never blocks.** When a change would leave someone over their hours, double-booked
  or booked on a day off, the save is *not* made and the caller gets ``needs_reason`` with the
  conflicts. Sent again with a ``reason``, it saves and the reason goes on the Task's timeline.
  A refusal would only move the work off the planner, where nobody sees the clash at all.
* **Only conflicts the change creates need a reason.** The check compares the task's day-by-day
  conflicts before and after; a task that was already over and only had its qualifications
  edited saves without a dialog, and nothing is checked when the dates, hours and crew are
  untouched.
* **Every save goes through ``doc.save()`` as the caller**, after ``check_permission("write")``,
  so ERPNext's own date checks speak (a task may not leave its project's expected window) and the
  crew-to-assignment sync runs. Their messages come back as ``warnings`` instead of a dialog per
  drag, as on the Maintenance Planner.
* **Optimistic locking on ``modified``**: a card that changed since the planner loaded is refused.
* **Rental crew tasks are not moved here.** Their dates follow the Rental Booking
  (``rental_logistics.sync_tasks`` rewrites them on every booking save), so they are reported as
  ``foreign`` and a date change on one is refused with a pointer to the booking.
* **Reads are raw SQL after a role gate**, like the Maintenance Planner's: the planner shows every
  project's tasks and every technician's visits to the people who schedule them.

The pure helpers (spans, cards, conflict deltas) take plain values so
``tests/test_project_planner.py`` runs them without a bench.
"""

import datetime
import json

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate, nowdate

from erpnext_enhancements.project_enhancements import crew_availability as engine
from erpnext_enhancements.project_enhancements import routing

# The people who schedule work: projects staff, and maintenance staff who must see project
# bookings on their technicians. The task reads below are raw SQL, so this set is the gate.
PLANNER_ROLES = {
	"System Manager",
	"Projects Manager",
	"Projects User",
	"Maintenance Supervisor",
	"Maintenance User",
}

# A month view shows six weeks; anything much longer is a mistake or a scrape.
MAX_RANGE_DAYS = 100
UNSCHEDULED_LIMIT = 300
ACTIVE_PROJECT = "Active"

# Who may ask Google whether Routes works (a billable call, and a question about the site's setup).
ROUTE_CHECK_ROLES = {"System Manager", "Projects Manager"}
# Route-aware date suggestions: how far ahead they look, and how many come back.
SUGGEST_MAX_DAYS = 30
SUGGEST_DEFAULT_DAYS = 10
SUGGEST_LIMIT = 8
NO_LOCATION_NOTE = (
	"This task has no location, so drive time is not considered. "
	"Set its address to get route-aware suggestions."
)


# ---------------------------------------------------------------------- pure helpers


def given(value):
	"""None for an argument the caller did not set. A form-encoded request turns a JavaScript
	``null`` or ``undefined`` into text, and an empty field into ``""``; none of them is a value to
	write, so a cleared field never wipes an estimate by accident (send 0 to clear one)."""
	if value is None or (isinstance(value, str) and value.strip() in ("", "null", "undefined")):
		return None
	return value


def parse_list(value):
	"""A list argument as sent by ``frappe.call``: a JSON string, a list, or None (unchanged)."""
	if value is None:
		return None
	if isinstance(value, str):
		value = value.strip()
		if not value:
			return []
		value = json.loads(value)
	if not isinstance(value, list | tuple):
		frappe.throw(_("Expected a list."))
	return list(value)


def plan_span(old_span, start=None, end=None):
	"""The task's new ``(first_day, last_day)``.

	Only ``start``: the task moves and keeps its length (an undated task becomes one day here;
	``_move`` first works out an end from its estimate, see ``engine.span_for_estimate``). Only
	``end``: the start stays. Both: as given. Neither: unchanged.
	"""
	first, last = engine._as_date(start), engine._as_date(end)
	if first and last:
		return first, last
	if first:
		if old_span:
			return first, first + (old_span[1] - old_span[0])
		return first, first
	if last:
		return (old_span[0] if old_span else last), last
	return old_span


def shift_datetime(value, days):
	"""A stored Date/Datetime value moved by ``days``, keeping its time of day."""
	moved = engine._as_datetime(value)
	if not moved:
		return None
	return (moved + datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def on_date(value, day):
	"""``day`` with the time of day ``value`` carried (midnight when it has none)."""
	old = engine._as_datetime(value)
	if old and old.time() != datetime.time():
		return f"{day} {old.strftime('%H:%M:%S')}"
	return str(day)


def conflict_delta(before, after):
	"""The conflicts in ``after`` that ``before`` did not already have, per person."""
	out = {}
	for label, messages in (after or {}).items():
		known = set((before or {}).get(label) or [])
		new = [message for message in messages if message not in known]
		if new:
			out[label] = new
	return out


def conflict_summary(conflicts):
	return "; ".join(f"{label}: {', '.join(messages)}" for label, messages in (conflicts or {}).items())


def schedule_state(task, crew):
	"""What the availability engine reads from a task: changing any of it can move hours."""
	return (
		str(engine.task_span(task)),
		str(engine.task_slot(task)),
		flt(task.get("expected_time")),
		tuple((m.get("resource"), flt(m.get("hours"))) for m in crew or []),
	)


def build_card(task, crew, booked, credentials, today, can_edit):
	"""One task card, from a Task row (or doc) and its resolved crew.

	``crew`` is ``[{"resource", "label", "hours", "is_lead"}]``; ``booked`` maps a resource to
	the hours the engine allocated it over the task's whole span.
	"""
	span = engine.task_span(task)
	slot = engine.task_slot(task)
	crew = crew or []
	crew_size = max(cint(task.get("custom_crew_size")), 0)
	today = getdate(today)
	return {
		"name": task.get("name"),
		"subject": task.get("subject") or task.get("name"),
		"project": task.get("project"),
		"project_title": task.get("project_title") or task.get("project"),
		"status": task.get("status"),
		"start": str(span[0]) if span else None,
		"end": str(span[1]) if span else None,
		"slot": engine._slot_text(slot),
		"expected_time": flt(task.get("expected_time")),
		"crew": [
			{
				"resource": member.get("resource"),
				"label": member.get("label") or member.get("resource"),
				"hours": member.get("hours"),
				"is_lead": bool(member.get("is_lead")),
				"booked": round(flt((booked or {}).get(member.get("resource"))), 2),
			}
			for member in crew
		],
		"crew_size": crew_size,
		"credentials": list(credentials or []),
		"short": max(crew_size - len(crew), 0),
		"modified": str(task.get("modified")),
		"movable": bool(can_edit and not task.get("custom_rental_booking")),
		"overdue": bool(
			task.get("status") == "Overdue"
			or (span and span[1] < today and task.get("status") not in engine.FINISHED_STATUSES)
		),
		"rental_kind": task.get("custom_rental_task_kind") or None,
		"color": task.get("color") or None,
	}


def needs_crew(card, assignees):
	"""Short of crew: nobody on it at all (no crew, no open assignee), or fewer than it needs."""
	if not card["crew"] and not assignees:
		return True
	return bool(card["crew_size"] and len(card["crew"]) < card["crew_size"])


# ---------------------------------------------------------------------- reads


def _require_planner():
	if frappe.session.user == "Administrator":
		return
	if not PLANNER_ROLES & set(frappe.get_roles()):
		frappe.throw(_("Only projects and maintenance staff can use the planner."), frappe.PermissionError)


@frappe.whitelist()
def get_planner(start, end):
	"""Everything the planner draws for ``start``..``end`` (inclusive). See the module docstring."""
	_require_planner()
	start, end = getdate(start), getdate(end)
	if end < start:
		frappe.throw(_("The end date is before the start date."))
	if date_diff(end, start) > MAX_RANGE_DAYS:
		frappe.throw(_("Pick a range of {0} days or less.").format(MAX_RANGE_DAYS))
	today = getdate(nowdate())
	can_edit = bool(frappe.has_permission("Task", "write"))

	data = engine._compute(start, end)
	labels = {r["name"]: r["label"] for r in data["resources"]}
	unscheduled_rows = _unscheduled()
	crew_rows, todo_users = engine.read_crews([row.get("name") for row in unscheduled_rows])
	credentials = _credentials(
		[t.get("name") for t in data["tasks"]] + [r.get("name") for r in unscheduled_rows]
	)

	def labelled(crew):
		return [dict(m, label=m.get("label") or labels.get(m["resource"]) or m["resource"]) for m in crew]

	tasks, needs, foreign = [], [], []
	for task in data["tasks"]:
		name = task.get("name")
		if task.get("custom_rental_booking"):
			if not data["crews"].get(name):
				foreign.extend(_crewless_rental(task, start, end))
			continue
		card = build_card(
			task,
			labelled(data["crews"].get(name) or []),
			data["task_hours"].get(name),
			credentials.get(name),
			today,
			can_edit,
		)
		tasks.append(card)
		if needs_crew(card, data["todo_users"].get(name)):
			needs.append(name)

	for resource, per_day in data["days"].items():
		for day, cell in per_day.items():
			for booking in cell["bookings"]:
				if booking["kind"] in ("visit", "rental", "travel"):
					foreign.append(
						{
							"kind": booking["kind"],
							"key": f"{booking['kind']}|{booking.get('key') or booking['ref']}|{resource}|{day}",
							"ref": booking["ref"],
							"label": booking["label"],
							"date": day,
							"resource": resource,
							"hours": booking["hours"],
						}
					)

	unscheduled = [
		build_card(
			row,
			labelled(
				engine.resolve_crew(
					crew_rows.get(row.get("name")), todo_users.get(row.get("name")), data["user_to_resource"]
				)
			),
			{},
			credentials.get(row.get("name")),
			today,
			can_edit,
		)
		for row in unscheduled_rows
	]

	project_names = {card["project"] for card in tasks + unscheduled if card.get("project")}
	return {
		"start": str(start),
		"end": str(end),
		"today": str(today),
		"resources": data["resources"],
		"days": data["days"],
		"tasks": tasks,
		"unscheduled": unscheduled,
		"needs_crew": needs,
		"foreign": foreign,
		"projects": _projects(project_names),
		"settings": {
			"default_day_hours": data["settings"]["default_day_hours"],
			"maintenance_visit_hours": data["settings"]["maintenance_visit_hours"],
		},
		"can_edit": can_edit,
	}


def _crewless_rental(task, start, end):
	"""A rental crew task nobody is on still belongs on the calendar, against nobody."""
	span = engine.task_span(task)
	if not span:
		return []
	return [
		{
			"kind": "rental",
			"key": f"rental|{task.get('name')}||{day}",
			"ref": task.get("name"),
			"label": task.get("subject") or task.get("name"),
			"date": str(day),
			"resource": None,
			"hours": None,
		}
		for day in engine.daterange(max(span[0], start), min(span[1], end))
	]


def _unscheduled():
	"""Undated open tasks on Active projects, newest project first. Python re-checks "undated"."""
	cols = engine._task_columns()
	selected = ", ".join(f"{expr} AS `{column}`" for column, expr in cols.items())
	rows = frappe.db.sql(
		f"""
		SELECT
			t.name, t.subject, t.project, t.status, t.exp_start_date, t.exp_end_date,
			t.expected_time, t.color, t.modified, {selected},
			p.project_name AS project_title
		FROM `tabTask` t
		INNER JOIN `tabProject` p ON p.name = t.project
		WHERE IFNULL(t.is_group, 0) = 0
			AND IFNULL(t.is_template, 0) = 0
			AND IFNULL(t.status, '') NOT IN %(finished)s
			AND p.status = %(active)s
			AND t.exp_start_date IS NULL
			AND t.exp_end_date IS NULL
		ORDER BY p.creation DESC, t.creation
		LIMIT %(limit)s
		""",
		{"finished": engine.FINISHED_STATUSES, "active": ACTIVE_PROJECT, "limit": UNSCHEDULED_LIMIT},
		as_dict=True,
	)
	return [row for row in rows if not engine.task_span(row) and not row.get("custom_rental_booking")]


def _credentials(task_names):
	out = {}
	names = [n for n in dict.fromkeys(task_names or []) if n]
	if not names:
		return out
	for row in frappe.get_all(
		"Task Required Credential",
		filters={"parenttype": "Task", "parent": ["in", names]},
		fields=["parent", "credential_type"],
		order_by="idx asc",
		limit_page_length=0,
	):
		if row.get("credential_type"):
			out.setdefault(row.get("parent"), []).append(row.get("credential_type"))
	return out


def _projects(names):
	"""Active projects plus any project on a card, for the project and PM filters."""
	try:
		owner = "e.employee_name" if frappe.db.has_column("Project", "custom_project_owner") else "NULL"
	except Exception:
		owner = "NULL"
	join = "LEFT JOIN `tabEmployee` e ON e.name = p.custom_project_owner" if owner != "NULL" else ""
	rows = frappe.db.sql(
		f"""
		SELECT p.name, p.project_name AS title, {owner} AS pm
		FROM `tabProject` p
		{join}
		WHERE p.status = %(active)s OR p.name IN %(names)s
		ORDER BY p.project_name
		""",
		{"active": ACTIVE_PROJECT, "names": tuple(sorted(names)) or ("__none__",)},
		as_dict=True,
	)
	return [
		{"name": r.get("name"), "title": r.get("title") or r.get("name"), "pm": r.get("pm")} for r in rows
	]


# ---------------------------------------------------------------------- writes


def _load(task, modified):
	_require_planner()
	doc = frappe.get_doc("Task", task)
	doc.check_permission("write")
	if str(doc.modified) != str(modified):
		frappe.throw(
			_("This task was changed by someone else since the planner loaded. Refresh and try again."),
			title=_("Task Out of Date"),
		)
	return doc


def _state(doc):
	"""The task as the engine reads it, from a doc that may have unsaved edits."""
	keys = (
		"name",
		"subject",
		"project",
		"status",
		"modified",
		"color",
		"exp_start_date",
		"exp_end_date",
		"expected_time",
		"custom_start_datetime",
		"custom_end_datetime",
		"custom_rental_booking",
		"custom_rental_task_kind",
		"custom_crew_size",
	)
	return {key: doc.get(key) for key in keys}


def _resources(names):
	names = [n for n in dict.fromkeys(names or []) if n]
	if not names:
		return {}
	rows = frappe.get_all(
		"Planner Resource",
		filters={"name": ["in", names]},
		fields=["name", "resource_name", "user", "is_active"],
		limit_page_length=0,
	)
	return {row.get("name"): row for row in rows}


def _current_crew(doc):
	"""The task's crew as the engine sees it: crew rows, else open assignees who are resources."""
	rows = [
		{
			"resource": row.get("resource"),
			"resource_name": row.get("resource_name"),
			"hours": row.get("hours"),
			"is_lead": row.get("is_lead"),
		}
		for row in (doc.get("custom_crew") or [])
	]
	user_to_resource = {}
	todo_users = []
	if not rows:
		todo_users = frappe.get_all(
			"ToDo",
			filters={"reference_type": "Task", "reference_name": doc.name, "status": "Open"},
			pluck="allocated_to",
		)
		if todo_users:
			for row in frappe.get_all(
				"Planner Resource",
				filters={"user": ["in", todo_users], "is_active": 1},
				fields=["name", "user"],
				limit_page_length=0,
			):
				user_to_resource[row.get("user")] = row.get("name")
	crew = engine.resolve_crew(rows, todo_users, user_to_resource)
	missing = [m["resource"] for m in crew if not m.get("label")]
	if missing:
		found = _resources(missing)
		for member in crew:
			if not member.get("label"):
				member["label"] = (found.get(member["resource"]) or {}).get("resource_name") or member[
					"resource"
				]
	return crew


def _set_crew(doc, crew, previous):
	"""Replace the crew rows. A resource must exist, and be active unless it was already on."""
	wanted = []
	for entry in crew:
		entry = {"resource": entry} if isinstance(entry, str) else dict(entry or {})
		if entry.get("resource") and entry["resource"] not in [w["resource"] for w in wanted]:
			wanted.append(entry)
	found = _resources([w["resource"] for w in wanted])
	already = {m.get("resource") for m in previous}
	doc.set("custom_crew", [])
	for entry in wanted:
		row = found.get(entry["resource"])
		if not row:
			frappe.throw(_("{0} is not a Planner Resource.").format(entry["resource"]))
		if not cint(row.get("is_active")) and entry["resource"] not in already:
			frappe.throw(
				_("{0} is not active on the planner.").format(row.get("resource_name") or row.get("name"))
			)
		hours = flt(entry.get("hours"))
		if hours < 0:
			frappe.throw(_("Hours cannot be negative."))
		doc.append(
			"custom_crew",
			{
				"resource": entry["resource"],
				"resource_name": row.get("resource_name"),
				"user": row.get("user"),
				"hours": hours,
				"is_lead": cint(entry.get("is_lead")),
			},
		)


def _move(doc, start, end):
	"""Set the task's dates. Only ``start``: every date field shifts by the same days."""
	state = _state(doc)
	old = engine.task_span(state)
	if not old and engine._as_date(start) and not engine._as_date(end):
		# Scheduled from the Unscheduled tray: long enough for its estimate, not always one day.
		people = max(len(_current_crew(doc)), cint(doc.get("custom_crew_size")), 1)
		end = engine.span_for_estimate(
			start, doc.get("expected_time"), people, engine.get_settings()["default_day_hours"]
		)[1]
	new = plan_span(old, start, end)
	if not new or new == old:
		return
	if new[1] < new[0]:
		frappe.throw(_("The end date is before the start date."))
	if doc.get("custom_rental_booking"):
		frappe.throw(
			_("This task's dates follow Rental Booking {0}. Change them on the booking.").format(
				doc.custom_rental_booking
			)
		)
	shift = (new[0] - old[0]).days if old else None
	if old and engine._as_date(start) and not engine._as_date(end):
		for field in ("exp_start_date", "exp_end_date"):
			if doc.get(field):
				doc.set(field, shift_datetime(doc.get(field), shift))
		if not doc.get("exp_start_date"):
			doc.exp_start_date = str(new[0])
		if not doc.get("exp_end_date"):
			doc.exp_end_date = on_date(doc.get("exp_start_date"), new[1])
	else:
		doc.exp_start_date = on_date(doc.get("exp_start_date"), new[0])
		doc.exp_end_date = on_date(doc.get("exp_end_date"), new[1])
	# A same-day slot decides the task's day (task_span), so it travels with the start.
	if shift:
		for field in ("custom_start_datetime", "custom_end_datetime"):
			if doc.get(field):
				doc.set(field, shift_datetime(doc.get(field), shift))


def _apply(
	doc, start=None, end=None, expected_time=None, crew_size=None, crew=None, credentials=None, reason=None
):
	"""The shared write: edit, check for new conflicts, save or ask for a reason."""
	before_state = _state(doc)
	before_crew = _current_crew(doc)

	_move(doc, start, end)
	if expected_time is not None:
		hours = flt(expected_time)
		if hours < 0:
			frappe.throw(_("Expected hours cannot be negative."))
		doc.expected_time = hours
	if crew_size is not None:
		doc.custom_crew_size = max(cint(crew_size), 0)
	if crew is not None:
		_set_crew(doc, crew, before_crew)
	if credentials is not None:
		doc.set(
			"custom_required_credentials",
			[
				{"credential_type": c if isinstance(c, str) else (c or {}).get("credential_type")}
				for c in credentials
				if (c if isinstance(c, str) else (c or {}).get("credential_type"))
			],
		)

	after_state = _state(doc)
	after_crew = _current_crew(doc)
	conflicts = {}
	if schedule_state(before_state, before_crew) != schedule_state(after_state, after_crew):
		before = engine.preview_conflicts(before_state, before_crew) if engine.task_span(before_state) else {}
		conflicts = conflict_delta(before, engine.preview_conflicts(after_state, after_crew))
		reason = (reason or "").strip()
		if conflicts and not reason:
			return {"needs_reason": True, "conflicts": conflicts}

	log = frappe.local.message_log if isinstance(getattr(frappe.local, "message_log", None), list) else None
	mark = len(log) if log is not None else 0
	doc.save()
	warnings = []
	if log is not None:
		from erpnext_enhancements.api.maintenance_planner import _message_text

		warnings = [w for w in (_message_text(message) for message in log[mark:]) if w]
		del log[mark:]

	if conflicts and reason:
		doc.add_comment(
			"Comment",
			_("Scheduled over a conflict on the Project Planner: {0}. Reason: {1}").format(
				frappe.utils.escape_html(conflict_summary(conflicts)), frappe.utils.escape_html(reason)
			),
		)
	return {
		"name": doc.name,
		"modified": str(doc.modified),
		"warnings": warnings,
		"conflicts": conflicts,
		"card": _card(doc),
	}


def _card(doc):
	state = _state(doc)
	if doc.get("project"):
		state["project_title"] = frappe.db.get_value("Project", doc.project, "project_name")
	crew = _current_crew(doc)
	booked = engine._preview(state, crew)[1] if engine.task_span(state) else {}
	credentials = [row.get("credential_type") for row in (doc.get("custom_required_credentials") or [])]
	return build_card(state, crew, booked, credentials, nowdate(), True)


@frappe.whitelist(methods=["POST"])
def save_task(
	task,
	modified,
	start=None,
	end=None,
	expected_time=None,
	crew_size=None,
	crew=None,
	credentials=None,
	reason=None,
):
	"""Change a task's dates, hours, crew size, crew or qualifications. Unset arguments stay as
	they are; ``crew`` and ``credentials`` are JSON lists.

	Returns ``{"needs_reason": True, "conflicts": {person: [..]}}`` without saving when the change
	creates a conflict and no ``reason`` came with it; otherwise
	``{"name", "modified", "warnings", "conflicts", "card"}``.
	"""
	doc = _load(task, modified)
	return _apply(
		doc,
		start=given(start),
		end=given(end),
		expected_time=given(expected_time),
		crew_size=given(crew_size),
		crew=parse_list(given(crew)),
		credentials=parse_list(given(credentials)),
		reason=reason,
	)


@frappe.whitelist(methods=["POST"])
def add_crew(task, resource, modified, reason=None):
	"""A person dropped on a task card: add them to the crew (nothing happens if they are on it).

	A task crewed only by assignments gets crew rows for those people first, so adding one person
	does not silently drop the others from the planner.
	"""
	doc = _load(task, modified)
	crew = _current_crew(doc)
	if resource in [m["resource"] for m in crew]:
		return {
			"name": doc.name,
			"modified": str(doc.modified),
			"warnings": [],
			"conflicts": {},
			"card": _card(doc),
		}
	crew.append({"resource": resource, "hours": None, "is_lead": False})
	return _apply(doc, crew=crew, reason=reason)


@frappe.whitelist(methods=["POST"])
def swap_crew(task, from_resource, to_resource, modified, date=None, reason=None):
	"""A crew chip dropped on another person's row: hand that person's place on the task over,
	keeping its hours and lead flag. With ``date``, the task also moves there, keeping its length.
	"""
	doc = _load(task, modified)
	crew = _current_crew(doc)
	place = next((i for i, m in enumerate(crew) if m["resource"] == from_resource), None)
	if place is None:
		frappe.throw(_("{0} is not on this task's crew. Refresh and try again.").format(from_resource))
	if from_resource != to_resource:
		if to_resource in [m["resource"] for m in crew]:
			crew.pop(place)
		else:
			crew[place] = dict(crew[place], resource=to_resource, label=None)
	return _apply(doc, start=given(date), crew=crew, reason=reason)


# ---------------------------------------------------------------------- routes: pure helpers


def fmt_km(km):
	"""``4.0`` → ``"4"``, ``3.46`` → ``"3.5"``, ``12.7`` → ``"13"``: a driver's precision."""
	km = flt(km)
	if km >= 10:
		return str(int(round(km)))
	return engine.fmt_hours(round(km, 1))


def fmt_minutes(minutes):
	return str(int(round(flt(minutes))))


def first_name(label):
	label = (label or "").strip()
	return label.split()[0] if label else label


def place_label(stop, titles):
	"""What a driver calls a stop's place: a visit's site name, else the project's title, else the
	booking's own label."""
	title = (titles or {}).get(stop.get("project")) if stop.get("project") else None
	if stop.get("kind") == "visit":
		from erpnext_enhancements.api.maintenance_planner import short_site_name

		return short_site_name(title) if title else (stop.get("label") or "")
	return title or stop.get("label") or stop.get("ref") or ""


def stop_label(stop, titles):
	"""A route stop's label: a visit is its site name (Phase 2 spec); a task its subject."""
	if stop.get("kind") == "visit":
		return place_label(stop, titles)
	return stop.get("label") or stop.get("ref") or ""


def hours_per_day(task, crew_count, settings, first_day):
	"""Hours one person needs on one day of the task, or None for "a full day" (no estimate).

	A time slot is its length. Otherwise the estimate is shared by the people on it (its crew, or
	``custom_crew_size`` when that is larger) over its weekdays: the task's own span when it is
	dated, else the span its estimate would get (``engine.span_for_estimate``).
	"""
	slot = engine.task_slot(task)
	if slot:
		return round((slot[1] - slot[0]).total_seconds() / 3600, 2)
	expected = flt(task.get("expected_time"))
	if expected <= 0:
		return None
	people = max(cint(crew_count), cint(task.get("custom_crew_size")), 1)
	span = engine.task_span(task) or engine.span_for_estimate(
		first_day, expected, people, settings.get("default_day_hours") or 8.0
	)
	days = engine.daterange(*span)
	working = [day for day in days if day.weekday() < 5] or days
	return round(expected / people / max(len(working), 1), 2)


def merge_unlocated(ordered, unlocated, arrivals):
	"""Put the stops with no location where they most likely happen in the day.

	``ordered`` are the routed stops and ``arrivals`` their arrival minutes. A slotted unlocated
	stop goes before the first routed stop that arrives after its slot starts; the rest go at the
	end of the day, by ref. They are never driven to (``routing.route_times`` skips them).
	"""
	out = list(ordered)
	times = list(arrivals)
	for stop in sorted(
		(s for s in unlocated if s.get("slot")), key=lambda s: (str(s["slot"][0]), str(s.get("ref") or ""))
	):
		start = routing.parse_minutes(stop["slot"][0])
		position = next((i for i, t in enumerate(times) if t > start), len(out))
		out.insert(position, stop)
		times.insert(position, start)
	out.extend(sorted((s for s in unlocated if not s.get("slot")), key=lambda s: str(s.get("ref") or "")))
	return out


def _long_limit(settings):
	value = (settings or {}).get("long_drive_minutes")
	return flt(value if value is not None else engine.DEFAULT_SETTINGS["long_drive_minutes"])


def route_payload(person, day, cell, route, settings, details, titles, maps, shop_address):
	"""The :func:`get_route` answer from the engine's cell and route for one person-day."""
	bookings = cell.get("bookings") or []
	travel = next((b.get("label") for b in bookings if b.get("kind") == "travel"), None)
	shop = route["shop"] if route else None
	legs = {(entry["from"], entry["to"]): entry for entry in (route or {}).get("legs") or []}

	def leg_of(a, b):
		return legs.get((a, b)) or routing.leg({}, a, b)

	def between(a, b):
		found = leg_of(a, b)
		return found["minutes"] if found else 0.0

	def hours_of(stop):
		return stop.get("hours")

	day_start = settings.get("day_start_time") or routing.DEFAULT_DAY_START
	if route:
		located = list(route["stops"])
		first_pass = routing.route_times(day_start, located, between, hours_of, start=shop, end=shop)
		arrivals = [routing.parse_minutes(t["arrive"]) for t in first_pass["stops"]]
		ordered = merge_unlocated(located, route["unlocated"], arrivals)
		times = routing.route_times(day_start, ordered, between, hours_of, start=shop, end=shop)
	else:
		# A travel day (or a day with nothing to drive to): the stops are listed, not routed.
		ordered = sorted(
			(
				{
					"ref": b.get("ref"),
					"kind": b.get("kind"),
					"label": b.get("label"),
					"project": b.get("project"),
					"slot": b.get("slot"),
					"hours": flt(b.get("hours")),
					"point": None,
				}
				for b in bookings
				if b.get("kind") in routing.STOP_KINDS
			),
			key=lambda s: (str((s.get("slot") or ["99:99"])[0]), str(s.get("ref") or "")),
		)
		times = None

	stops = []
	for index, stop in enumerate(ordered):
		detail = (details or {}).get(stop.get("ref")) or {}
		point = stop.get("point") if route else None
		timing = times["stops"][index] if times else {}
		origin = timing.get("from_point")
		driven = leg_of(origin, point) if (origin is not None and point is not None) else None
		stops.append(
			{
				"order": index + 1,
				"kind": stop.get("kind"),
				"ref": stop.get("ref"),
				"label": stop_label(stop, titles),
				"project": stop.get("project"),
				"project_title": (titles or {}).get(stop.get("project")) if stop.get("project") else None,
				"address": detail.get("address"),
				"lat": point[0] if point else None,
				"lng": point[1] if point else None,
				"slot": stop.get("slot"),
				"hours": flt(stop.get("hours")),
				"arrive": timing.get("arrive"),
				"depart": timing.get("depart"),
				"drive_minutes": timing.get("drive_minutes"),
				"wait_minutes": timing.get("wait_minutes"),
				"km": round(flt(driven["km"]), 1) if driven else None,
				"located": point is not None,
			}
		)

	def shop_end(extra=None):
		if shop is None:
			return None
		return {"label": _("Shop"), "address": shop_address, "lat": shop[0], "lng": shop[1], **(extra or {})}

	back = None
	if route and route["points"] and shop is not None:
		back = leg_of(route["points"][-2], shop) if len(route["points"]) > 1 else None
	located_points = [s["point"] for s in (route or {}).get("stops") or []]
	drive = flt((route or {}).get("drive_minutes"))
	return {
		"resource": person.get("name"),
		"label": person.get("label"),
		"date": str(day),
		"day_start": routing.fmt_clock(routing.parse_minutes(day_start)),
		"start": shop_end(),
		"end": shop_end(
			{
				"arrive": times["finish"] if times and located_points else None,
				"drive_minutes": times["back_minutes"] if times and located_points else None,
				"km": round(flt(back["km"]), 1) if back else None,
			}
		),
		"stops": stops,
		"drive_minutes": drive,
		"km": flt((route or {}).get("km")),
		"source": (route or {}).get("source") if drive > 0 else None,
		"long_drive": bool(route and drive > _long_limit(settings)),
		"unlocated": [
			{"kind": s.get("kind"), "ref": s.get("ref"), "label": stop_label(s, titles)}
			for s in (route or {}).get("unlocated") or []
		],
		"maps_url": routing.maps_url(shop, shop, located_points) if located_points else None,
		"maps_key": (maps or {}).get("maps_key") or "",
		"map_ids": (maps or {}).get("map_ids") or {"map_id_light": "", "map_id_dark": ""},
		"travel": travel,
		"off": cell.get("off"),
	}


def suggestion(person, day, cell, route, site, shop, matrix, needed, settings, titles):
	"""One candidate day for a task, scored by the driving it adds, or None when it does not fit.

	A day fits when the person has capacity, is not travelling, and has at least ``needed`` free
	hours (their whole capacity when the task has no estimate). Score = minutes the task adds to
	the day's route at its cheapest insertion (an empty day: shop → site → shop) + 0.5 × hours
	over capacity once that driving is counted (0 when it fits). Lower is better.
	"""
	capacity, free = flt(cell.get("capacity")), flt(cell.get("free"))
	if capacity <= 0 or any(b.get("kind") == "travel" for b in cell.get("bookings") or []):
		return None
	need = needed if needed is not None else capacity
	if free + engine.TOLERANCE < need:
		return None
	name = first_name(person.get("label")) or person.get("name")
	base = {
		"date": str(day),
		"resource": person.get("name"),
		"label": person.get("label"),
		"free_hours": round(free, 2),
	}
	if site is None:
		return dict(
			base,
			added_minutes=None,
			source=None,
			nearby=[],
			long_drive=bool(cell.get("long_drive")),
			score=0.0,
			reason=_("{0} has {1}h free.").format(name, engine.fmt_hours(free)),
		)

	def between(a, b):
		found = routing.leg(matrix, a, b)
		return found["minutes"] if found else 0.0

	stops = (route or {}).get("stops") or []
	points = list((route or {}).get("points") or [])
	if not points:
		points = [shop, shop] if shop is not None else [s["point"] for s in stops]
	added, position = routing.best_insertion(points, site, between)
	used = []
	clean = [p for p in points if p is not None]
	if len(clean) == 1:
		used = [routing.leg(matrix, clean[0], site), routing.leg(matrix, site, clean[0])]
	elif len(clean) > 1:
		used = [routing.leg(matrix, clean[position - 1], site), routing.leg(matrix, site, clean[position])]
	source = routing.combine_sources((entry or {}).get("source") for entry in used)

	pad = settings.get("pad_drive_time")
	pad = pad is None or bool(flt(pad))
	over = max(need + (added / 60 if pad else 0.0) - free, 0.0)
	score = round(added + 0.5 * over, 2)

	near, seen = [], set()
	for stop in sorted(stops, key=lambda s: (routing.haversine_km(site, s["point"]), str(s.get("ref")))):
		km = routing.haversine_km(site, stop["point"])
		if km > routing.NEARBY_KM or stop.get("ref") in seen:
			continue
		seen.add(stop.get("ref"))
		near.append({"ref": stop.get("ref"), "label": place_label(stop, titles), "km": round(km, 1)})
	near = near[:3]

	drive_total = flt((route or {}).get("drive_minutes")) + added
	if near:
		reason = _("{0} is already at {1} that day ({2} km away): +{3} min driving.").format(
			name, near[0]["label"], fmt_km(near[0]["km"]), fmt_minutes(added)
		)
	elif stops:
		nearest = min(routing.haversine_km(site, s["point"]) for s in stops)
		reason = _("{0} has other stops that day, the nearest {1} km away: +{2} min driving.").format(
			name, fmt_km(nearest), fmt_minutes(added)
		)
	elif any(b.get("kind") in routing.STOP_KINDS for b in cell.get("bookings") or []):
		reason = _("Nothing else on {0}'s day has a location: +{1} min driving.").format(
			name, fmt_minutes(added)
		)
	else:
		reason = _("Nobody is near; {0}'s day is empty: +{1} min driving.").format(name, fmt_minutes(added))
	if over > engine.TOLERANCE:
		reason += " " + _("Over by {0}h with the driving.").format(engine.fmt_hours(over))
	return dict(
		base,
		added_minutes=round(added, 1),
		source=source,
		nearby=near,
		long_drive=bool(drive_total > _long_limit(settings)),
		score=score,
		reason=reason,
	)


def rank_suggestions(entries, with_site, limit=SUGGEST_LIMIT):
	"""Best first: by score then date with a site; by free hours then date without one."""
	if with_site:
		key = lambda s: (s["score"], s["date"], s.get("label") or "")  # noqa: E731
	else:
		key = lambda s: (-s["free_hours"], s["date"], s.get("label") or "")  # noqa: E731
	return sorted((e for e in entries if e), key=key)[:limit]


# ---------------------------------------------------------------------- routes: reads


def _project_titles(names):
	names = sorted({n for n in names or () if n})
	if not names:
		return {}
	return {
		row.get("name"): row.get("project_name") or row.get("name")
		for row in frappe.get_all(
			"Project", filters={"name": ["in", names]}, fields=["name", "project_name"], limit_page_length=0
		)
	}


def _maps_config():
	"""The browser Maps key and Map IDs (Travel Settings), as ``api.travel.get_maps_config``
	hands any logged-in user: the key is referrer-restricted by design."""
	out = {"maps_key": "", "map_ids": {"map_id_light": "", "map_id_dark": ""}}
	try:
		out["maps_key"] = frappe.db.get_single_value("Travel Settings", "google_maps_api_key") or ""
		out["map_ids"] = {
			"map_id_light": frappe.db.get_single_value("Travel Settings", "google_maps_map_id_light") or "",
			"map_id_dark": frappe.db.get_single_value("Travel Settings", "google_maps_map_id_dark") or "",
		}
	except Exception:
		pass
	return out


@frappe.whitelist()
def get_route(resource, date):
	"""One person's day as a drive. See :func:`route_payload` for the shape.

	``resource`` is a Planner Resource name. The day comes from the same engine call the planner
	makes (so its order, drive minutes and padding are exactly what the week view counted).
	"""
	_require_planner()
	from erpnext_enhancements.api.pickup_routing import _depot_address

	day = getdate(date)
	data = engine._compute(day, day, [resource])
	person = next((r for r in data["resources"] if r["name"] == resource), None)
	if not person:
		frappe.throw(_("{0} is not an active Planner Resource.").format(resource))
	cell = (data["days"].get(resource) or {}).get(str(day)) or {}
	route = (data.get("routes") or {}).get((resource, day))
	stop_bookings = [b for b in cell.get("bookings") or [] if b.get("kind") in routing.STOP_KINDS]
	details = routing.locate(stop_bookings, detail=True) if stop_bookings else {}
	titles = _project_titles(b.get("project") for b in stop_bookings)
	return route_payload(
		person, day, cell, route, data["settings"], details, titles, _maps_config(), _depot_address()
	)


def _suggest_candidates(crew, resource=None):
	"""The task's crew; with no crew, every active Field resource; ``resource`` narrows to one."""
	if resource:
		return [resource]
	if crew:
		return [m["resource"] for m in crew]
	return frappe.get_all(
		"Planner Resource",
		filters={"is_active": 1, "resource_group": "Field"},
		pluck="name",
		order_by="resource_name asc",
		limit_page_length=0,
	)


@frappe.whitelist()
def suggest_dates(task, start=None, days=None, resource=None):
	"""The best days, and people, for a task by drive time. Read-only: it books nothing.

	Looks at the next ``days`` days (default 10, at most 30) from ``start`` (default today, never
	before today) for the task's crew, or every active Field resource when it has none, or just
	``resource``. A day qualifies when the person has the hours (see :func:`suggestion`); the
	best eight come back with a one-sentence ``reason``. The task's own current booking is left
	out of the free hours, so moving it does not count against itself. A task with no location
	is ranked by free hours alone, with a ``note`` saying so.
	"""
	_require_planner()
	doc = frappe.get_doc("Task", task)
	state = _state(doc)
	project_title = (
		frappe.db.get_value("Project", doc.project, "project_name") if doc.get("project") else None
	)
	crew = _current_crew(doc)
	today = getdate(nowdate())
	first = max(getdate(start) if given(start) else today, today)
	count = min(max(cint(given(days) or SUGGEST_DEFAULT_DAYS), 1), SUGGEST_MAX_DAYS)
	last = first + datetime.timedelta(days=count - 1)
	settings = engine.get_settings()
	needed = hours_per_day(state, len(crew), settings, first)

	kind = "rental" if state.get("custom_rental_booking") else "task"
	point = routing.locate([{"kind": kind, "ref": doc.name, "project": doc.get("project")}]).get(doc.name)
	site = {"lat": point[0], "lng": point[1], "label": project_title or doc.get("subject")} if point else None
	result = {
		"task": doc.name,
		"subject": doc.get("subject") or doc.name,
		"site": site,
		"hours_needed": needed,
		"suggestions": [],
		"note": None if point else _(NO_LOCATION_NOTE),
	}

	candidates = _suggest_candidates(crew, given(resource))
	if not candidates:
		result["note"] = " ".join(
			n
			for n in (
				result["note"],
				_("Nobody to suggest: the task has no crew and no Field resource is active."),
			)
			if n
		)
		return result

	data = engine._compute(first, last, candidates, exclude={doc.name})
	routes = data.get("routes") or {}
	people = {r["name"]: r for r in data["resources"]}
	shop = routing.start_point() if point else None

	matrix = {}
	if point:
		pairs = set()
		for (resource_name, _day), route in routes.items():
			if resource_name in people:
				for p in route.get("points") or [s["point"] for s in route.get("stops") or []]:
					pairs.update({(p, point), (point, p)})
		if shop is not None:
			pairs.update({(shop, point), (point, shop)})
		matrix = routing.drive_matrix(pairs, settings)

	titles = _project_titles(s.get("project") for route in routes.values() for s in route.get("stops") or [])
	entries = []
	for name, person in people.items():
		for day_text, cell in sorted((data["days"].get(name) or {}).items()):
			day = getdate(day_text)
			entries.append(
				suggestion(
					person, day, cell, routes.get((name, day)), point, shop, matrix, needed, settings, titles
				)
			)
	result["suggestions"] = rank_suggestions(entries, with_site=bool(point))
	if not result["suggestions"]:
		hours = engine.fmt_hours(needed) + "h" if needed is not None else _("a full day")
		result["note"] = " ".join(
			n
			for n in (
				result["note"],
				_("Nobody has {0} free in the {1} days from {2}.").format(hours, count, first),
			)
			if n
		)
	return result


@frappe.whitelist()
def check_routes():
	"""``{"google": bool, "detail": str}``: whether Google Routes answers for the server key now.

	One tiny billable call, so System Manager / Projects Manager only. Success also clears the
	hour-long "Google is down" flag, so the planner uses Google again at once.
	"""
	if frappe.session.user != "Administrator" and not ROUTE_CHECK_ROLES & set(frappe.get_roles()):
		frappe.throw(
			_("Only a System Manager or Projects Manager can check Google Routes."), frappe.PermissionError
		)
	return routing.routes_status()
