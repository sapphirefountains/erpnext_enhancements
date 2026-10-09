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
def get_planner(start, end, draft=0):
	"""Everything the planner draws for ``start``..``end`` (inclusive). See the module docstring.

	``draft=1`` (Phase 3B draft mode) overlays the caller's unpublished Draft rows first; see
	:func:`_draft_overlay`. Without it the answer is exactly what it always was.
	"""
	_require_planner()
	start, end = getdate(start), getdate(end)
	if end < start:
		frappe.throw(_("The end date is before the start date."))
	if date_diff(end, start) > MAX_RANGE_DAYS:
		frappe.throw(_("Pick a range of {0} days or less.").format(MAX_RANGE_DAYS))
	today = getdate(nowdate())
	can_edit = bool(frappe.has_permission("Task", "write"))
	overlay = _draft_overlay() if cint(given(draft)) else None

	if overlay:
		data = engine._compute(start, end, exclude=overlay["exclude"], extra=overlay["extra"])
	else:
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
	result = {
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
	return _with_drafts(result, overlay, start, end) if overlay is not None else result


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
	draft=0,
):
	"""Change a task's dates, hours, crew size, crew or qualifications. Unset arguments stay as
	they are; ``crew`` and ``credentials`` are JSON lists.

	Returns ``{"needs_reason": True, "conflicts": {person: [..]}}`` without saving when the change
	creates a conflict and no ``reason`` came with it; otherwise
	``{"name", "modified", "warnings", "conflicts", "card"}``.

	``draft=1`` (Phase 3B): nothing is written to the Task; the change merges into the caller's
	Draft row instead (:func:`_store_draft`), and conflicts come back as information.
	"""
	if cint(given(draft)):
		return _store_draft(
			_draft_context(task, modified),
			start=given(start),
			end=given(end),
			expected_time=given(expected_time),
			crew_size=given(crew_size),
			crew=parse_list(given(crew)),
			credentials=parse_list(given(credentials)),
		)
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
def add_crew(task, resource, modified, reason=None, draft=0):
	"""A person dropped on a task card: add them to the crew (nothing happens if they are on it).

	A task crewed only by assignments gets crew rows for those people first, so adding one person
	does not silently drop the others from the planner. ``draft=1``: into the caller's draft.
	"""
	drafting = _draft_context(task, modified) if cint(given(draft)) else None
	doc = drafting["doc"] if drafting else _load(task, modified)
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
	if drafting:
		return _store_draft(drafting, crew=crew)
	return _apply(doc, crew=crew, reason=reason)


@frappe.whitelist(methods=["POST"])
def swap_crew(task, from_resource, to_resource, modified, date=None, reason=None, draft=0):
	"""A crew chip dropped on another person's row: hand that person's place on the task over,
	keeping its hours and lead flag. With ``date``, the task also moves there, keeping its length.
	``draft=1``: into the caller's draft.
	"""
	drafting = _draft_context(task, modified) if cint(given(draft)) else None
	doc = drafting["doc"] if drafting else _load(task, modified)
	crew = _current_crew(doc)
	place = next((i for i, m in enumerate(crew) if m["resource"] == from_resource), None)
	if place is None:
		frappe.throw(_("{0} is not on this task's crew. Refresh and try again.").format(from_resource))
	if from_resource != to_resource:
		if to_resource in [m["resource"] for m in crew]:
			crew.pop(place)
		else:
			crew[place] = dict(crew[place], resource=to_resource, label=None)
	if drafting:
		return _store_draft(drafting, start=given(date), crew=crew)
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


# ====================================================================== Phase 3B: telling people
#
# Draft and publish (P3.7), the digest preview (P3.8), the printable crew sheet (P3.9) and My week
# (P3.10). One block at the end of the module on purpose: Phase 3A changes the functions above in
# parallel, and the only Phase 3B edits up there are the ``draft`` arguments of get_planner,
# save_task, add_crew and swap_crew, each of which leaves its function exactly as it was when
# ``draft`` is off.
#
# Things this block is careful about, some of which look like bugs:
#
# * **A draft never touches the Task.** Drafting loads the task as a save would (planner gate,
#   ``check_permission("write")``, the ``modified`` lock), applies the planner's earlier draft and
#   the new change *in memory*, and stores the result on a ``Planner Draft Change`` row. No
#   ``doc.save()``, so no crew-to-assignment sync, no alert, nothing anyone else can see.
# * **Publishing replays ``save_task``**: each Draft row's arguments go through ``_apply``, the same
#   function a drag uses, so ERPNext's date rules, the crew sync and the timeline note all happen
#   exactly as for a live save. A task somebody else changed since it was drafted is skipped and
#   reported, never published over.
# * **Conflicts are judged on the whole batch.** Two drafts that are each fine can overbook a person
#   together, and two that each clash can cancel out (a swap). So the engine computes the week with
#   every draft in place, compares it with the week as stored, and asks for one reason for the lot:
#   all or nothing. Saving one at a time cannot see that, so a task the sequence alone trips over is
#   retried once the others are saved.
# * **One notice per person per publish**, summarizing every task that moved for them, sent after
#   the saves (see ``planner_notices``); the planner who published is not told about their own work.

DRAFT_DOCTYPE = "Planner Draft Change"
DRAFT, PUBLISHED, DISCARDED = "Draft", "Published", "Discarded"
#: The ``save_task`` arguments a Draft row may carry. Nothing else in a stored payload is applied.
DRAFT_KEYS = ("start", "end", "expected_time", "crew_size", "crew", "credentials", "tentative")
DRAFT_LIMIT = 300
#: The timeline reason for the rare task that only conflicts while its batch is half saved.
PUBLISH_FALLBACK_REASON = "Published from the Project Planner with other drafted changes"
#: Who may email themselves a preview of the combined 6 AM digest.
DIGEST_PREVIEW_ROLES = {"System Manager", "Projects Manager"}
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
NOTE_CHARS = 280
MAPS_SEARCH = "https://www.google.com/maps/search/?api=1&query="


# ---------------------------------------------------------------------- Phase 3B: pure helpers


def merge_payload(existing, changes, span=None):
	"""A Draft row's payload after one more drafted change.

	Keys the change sets replace the stored ones; keys it leaves unset keep theirs, so the payload
	is everything the planner has changed on the task so far. When the dates moved, ``start`` and
	``end`` are both stored from the task's resulting ``span`` (a move that only gave a start keeps
	the task's length, and the payload must say what that length came to).
	"""
	out = {key: value for key, value in (existing or {}).items() if key in DRAFT_KEYS}
	changes = {
		key: value for key, value in (changes or {}).items() if key in DRAFT_KEYS and value is not None
	}
	out.update(changes)
	if span and ("start" in changes or "end" in changes):
		out["start"], out["end"] = str(span[0]), str(span[1])
	return out


def load_payload(text):
	"""A stored payload as a dict of known keys; anything unreadable is an empty draft."""
	try:
		value = json.loads(text) if isinstance(text, str) else (text or {})
	except ValueError:
		return {}
	if not isinstance(value, dict):
		return {}
	return {key: value[key] for key in DRAFT_KEYS if key in value}


def payload_kwargs(payload, with_tentative=True):
	"""``_apply`` / ``_draft_edit`` keyword arguments for a payload. ``tentative`` (Phase 3A) is
	passed only to a function that takes it."""
	out = {key: payload[key] for key in DRAFT_KEYS if (payload or {}).get(key) is not None}
	if not with_tentative:
		out.pop("tentative", None)
	return out


def crew_payload(crew):
	"""A crew as ``save_task`` takes it: a blank or 0 hours is None (an even share)."""
	return [
		{
			"resource": member.get("resource"),
			"hours": round(flt(member.get("hours")), 2) if flt(member.get("hours")) > 0 else None,
			"is_lead": 1 if member.get("is_lead") else 0,
		}
		for member in crew or []
		if member.get("resource")
	]


def same_moment(a, b):
	"""Two ``modified`` values naming the same instant, whatever their type or text."""
	try:
		return engine._as_datetime(a) == engine._as_datetime(b)
	except (TypeError, ValueError):
		return str(a) == str(b)


def batch_conflicts(before_days, after_days, names, labels):
	"""The conflicts the drafted tasks create together, ``{person: ["2026-10-12: Over by 2h"]}``.

	``before_days``/``after_days`` are the engine's ``days`` for the same people and range, as
	stored and with every draft in place. Only days a drafted task is booked on count, a conflict
	already there before does not, and a double booking between two other tasks is not the drafts'
	doing (the same rules as ``engine._preview``, for many tasks at once).
	"""
	names = set(names or ())
	out = {}
	for resource, per_day in (after_days or {}).items():
		for day, cell in sorted((per_day or {}).items()):
			bookings = cell.get("bookings") or []
			if not any(b.get("ref") in names and b.get("kind") in ("task", "rental") for b in bookings):
				continue
			known = set((((before_days or {}).get(resource) or {}).get(day) or {}).get("conflicts") or [])
			for conflict in cell.get("conflicts") or []:
				if conflict in known:
					continue
				if conflict.startswith("Double-booked") and not any(
					f"({name}," in conflict or f", {name})" in conflict for name in names
				):
					continue
				out.setdefault((labels or {}).get(resource, resource), []).append(f"{day}: {conflict}")
	return out


def week_start(day, first_weekday="Sunday"):
	"""The first day of the week holding ``day``, weeks starting on ``first_weekday`` (the System
	Settings name, as the page reads ``frappe.boot.sysdefaults.first_day_of_the_week``)."""
	day = getdate(day)
	first = WEEKDAY_NAMES.index(first_weekday) if first_weekday in WEEKDAY_NAMES else 6
	return day - datetime.timedelta(days=(day.weekday() - first) % 7)


def maps_link(address=None, lat=None, lng=None):
	"""A Google Maps search link for an address (else the point), or None. Tapping it on a phone
	opens the Maps app at the site."""
	from urllib.parse import quote_plus

	if address and str(address).strip():
		query = " ".join(str(address).split())
	elif lat not in (None, "") and lng not in (None, ""):
		query = f"{lat},{lng}"
	else:
		return None
	return MAPS_SEARCH + quote_plus(query)


def crewmates(days, labels, crews=None, visit_people=None):
	"""``{(kind, ref): [names]}``: who is on each booking, from everything computed.

	From the day cells (everyone computed who is booked on it), the tasks' resolved crews (the
	people not computed, too) and the visits' technician and crew (``visit_people``). The caller
	leaves the person themselves out.
	"""
	out = {}

	def add(key, name):
		if name and name not in out.setdefault(key, []):
			out[key].append(name)

	for resource, per_day in (days or {}).items():
		for cell in (per_day or {}).values():
			for booking in cell.get("bookings") or []:
				if booking.get("kind") in routing.STOP_KINDS and booking.get("ref"):
					add((booking["kind"], booking["ref"]), (labels or {}).get(resource) or resource)
	for task, crew in (crews or {}).items():
		for member in crew or []:
			name = member.get("label") or (labels or {}).get(member.get("resource")) or member.get("resource")
			add(("task", task), name)
			add(("rental", task), name)
	for ref, names in (visit_people or {}).items():
		for name in names or []:
			add(("visit", ref), name)
	return out


def day_entry(person, day, cell, route, settings, details, titles, mates, notes):
	"""One person's day for My week, the crew sheet and the 6 AM digest.

	The stops come from :func:`route_payload`, so they are in the order the route drives them, with
	the arrival times the route view shows; a slotted stop shows its slot instead. Each carries the
	site, its address and a Maps link, the crewmates and the hours.
	"""
	cell = cell or {}
	payload = route_payload(person, day, cell, route, settings, details, titles, {}, None)
	me = person.get("label")
	items = []
	for stop in payload["stops"]:
		slot = stop.get("slot")
		key = (stop.get("kind"), stop.get("ref"))
		items.append(
			{
				"kind": stop.get("kind"),
				"ref": stop.get("ref"),
				"label": stop.get("label"),
				"project": stop.get("project"),
				"project_title": stop.get("project_title"),
				"time": (slot[0] if slot else stop.get("arrive")) or None,
				"slot": slot,
				"arrive": stop.get("arrive"),
				"address": stop.get("address"),
				"maps_url": maps_link(stop.get("address"), stop.get("lat"), stop.get("lng")),
				"crew": [name for name in (mates or {}).get(key) or [] if name != me],
				"hours": stop.get("hours"),
				"notes": (notes or {}).get(stop.get("ref"))
				if stop.get("kind") in ("task", "rental")
				else None,
			}
		)
	return {
		"date": str(day),
		"off": cell.get("off"),
		"capacity": cell.get("capacity"),
		"booked": cell.get("booked"),
		"free": cell.get("free"),
		"travel": payload.get("travel"),
		"conflicts": list(cell.get("conflicts") or []),
		"drive_minutes": payload.get("drive_minutes"),
		"items": items,
	}


# ---------------------------------------------------------------------- Phase 3B: drafts


def _draft_rows(user=None):
	"""``(rows, duplicates)``: the caller's Draft rows, one per task (the newest wins), oldest
	first, and any older duplicate a racing double-save left behind."""
	rows = frappe.get_all(
		DRAFT_DOCTYPE,
		filters={"owner": user or frappe.session.user, "status": DRAFT},
		fields=["name", "task", "subject", "payload", "base_modified", "modified"],
		order_by="creation asc",
		limit_page_length=DRAFT_LIMIT,
	)
	latest = {}
	for row in rows:
		known = latest.get(row.get("task"))
		if known is None or str(row.get("modified")) >= str(known.get("modified")):
			latest[row.get("task")] = row
	keep = [latest[task] for task in dict.fromkeys(row.get("task") for row in rows)]
	kept = {row.get("name") for row in keep}
	return keep, [row for row in rows if row.get("name") not in kept]


def _draft_edit(
	doc,
	start=None,
	end=None,
	expected_time=None,
	crew_size=None,
	crew=None,
	credentials=None,
	tentative=None,
):
	"""The edit half of :func:`_apply`, with no conflict check and no save. Keep the two in step:
	``tests/test_planner_phase3b.py`` runs both on the same change and compares the result."""
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
	if tentative is not None:
		# Phase 3A's pencil flag. On a site without the field this only sets an attribute on a doc
		# that is never saved here.
		doc.set("custom_tentative", 1 if cint(tentative) else 0)


def _quiet_message_log():
	"""``(log, mark)`` so a caught ``frappe.throw`` does not also pop up in the browser."""
	log = frappe.local.message_log if isinstance(getattr(frappe.local, "message_log", None), list) else None
	return log, (len(log) if log is not None else 0)


def _drafted_doc(row):
	"""``(doc, problem)``: the Task with ``row``'s draft applied in memory, or why it cannot be.

	Nothing is saved. ``problem`` is None for a draft that applies; otherwise a sentence for the
	planner (the task is gone, somebody else changed it since, or the change no longer fits).
	"""
	try:
		doc = frappe.get_doc("Task", row.get("task"))
	except Exception:
		return None, _("The task no longer exists.")
	if not same_moment(row.get("base_modified"), doc.modified):
		return doc, _("Changed by someone else since you drafted it.")
	log, mark = _quiet_message_log()
	try:
		_draft_edit(doc, **payload_kwargs(load_payload(row.get("payload"))))
	except Exception as exc:
		if log is not None:
			del log[mark:]
		return doc, _("Cannot be applied any more: {0}").format(frappe.utils.strip_html_tags(str(exc))[:300])
	return doc, None


def _draft_context(task, modified):
	"""Load a task for drafting: as a save would, then with the caller's draft so far applied.

	A Draft row whose task changed since it was drafted is not built on: the new draft starts from
	the task as it is now, and the planner is told (``reset``).
	"""
	doc = _load(task, modified)
	rows, duplicates = _draft_rows()
	row = next((r for r in rows if r.get("task") == doc.name), None)
	stored = (_state(doc), _current_crew(doc))
	stored_rest = _draft_rest(doc)
	payload, reset = {}, False
	if row:
		if same_moment(row.get("base_modified"), doc.modified):
			payload = load_payload(row.get("payload"))
			_draft_edit(doc, **payload_kwargs(payload))
		else:
			reset = True
	return {
		"doc": doc,
		"row": row,
		"payload": payload,
		"stored": stored,
		"stored_rest": stored_rest,
		"reset": reset,
		"duplicates": [r for r in duplicates if r.get("task") == doc.name],
	}


def _draft_rest(doc):
	"""What a draft can change that ``schedule_state`` does not see: crew size, qualifications,
	the lead flag and the pencil flag."""
	return (
		cint(doc.get("custom_crew_size")),
		sorted(row.get("credential_type") or "" for row in (doc.get("custom_required_credentials") or [])),
		tuple(m.get("resource") for m in _current_crew(doc) if m.get("is_lead")),
		cint(doc.get("custom_tentative")),
	)


def _store_draft(
	context,
	start=None,
	end=None,
	expected_time=None,
	crew_size=None,
	crew=None,
	credentials=None,
	tentative=None,
):
	"""Apply one change to a drafted task in memory and keep it on the caller's Draft row.

	Returns the shape a live save does, plus ``"drafted": True``. ``conflicts`` are the ones the
	draft would create against the task as stored, as information: drafting never needs a reason.
	They also come back as ``warnings`` so the page shows them without asking anything.
	"""
	doc = context["doc"]
	stored_state, stored_crew = context["stored"]
	_draft_edit(doc, start, end, expected_time, crew_size, crew, credentials, tentative)
	after_state, after_crew = _state(doc), _current_crew(doc)

	recorded = {"start": start, "end": end}
	if expected_time is not None:
		recorded["expected_time"] = flt(expected_time)
	if crew_size is not None:
		recorded["crew_size"] = max(cint(crew_size), 0)
	if crew is not None:
		recorded["crew"] = crew_payload(after_crew)
	if credentials is not None:
		recorded["credentials"] = [
			row.get("credential_type") for row in (doc.get("custom_required_credentials") or [])
		]
	if tentative is not None:
		recorded["tentative"] = 1 if cint(tentative) else 0
	payload = merge_payload(context["payload"], recorded, engine.task_span(after_state))

	conflicts = {}
	moved = schedule_state(stored_state, stored_crew) != schedule_state(after_state, after_crew)
	if moved:
		before = engine.preview_conflicts(stored_state, stored_crew) if engine.task_span(stored_state) else {}
		conflicts = conflict_delta(before, engine.preview_conflicts(after_state, after_crew))
	# Drafted back to exactly what is stored (an Undo, a drag there and back): no draft is left.
	unchanged = not moved and context.get("stored_rest") == _draft_rest(doc)

	_write_draft(context, doc, payload, discard=unchanged)
	warnings = []
	if context.get("reset"):
		warnings.append(
			_(
				"Your earlier draft of this task was replaced: someone else changed the task since you drafted it."
			)
		)
	for label, messages in conflicts.items():
		warnings.append(_("Not published yet. {0}: {1}").format(label, ", ".join(messages)))
	card = _card(doc)
	card["drafted"] = not unchanged
	return {
		"drafted": True,
		"name": doc.name,
		"modified": str(doc.modified),
		"warnings": warnings,
		"conflicts": conflicts,
		"card": card,
	}


def _write_draft(context, doc, payload, discard=False):
	"""Insert or update the caller's one Draft row for the task (permission-checked). With
	``discard`` the row, if any, is marked Discarded instead and nothing is inserted."""
	text = json.dumps(payload, sort_keys=True, default=str)
	row = context.get("row")
	if discard:
		if row:
			draft = frappe.get_doc(DRAFT_DOCTYPE, row.get("name"))
			draft.status = DISCARDED
			draft.save()
	elif row:
		draft = frappe.get_doc(DRAFT_DOCTYPE, row.get("name"))
		draft.payload = text
		draft.base_modified = doc.modified
		draft.save()
	else:
		frappe.get_doc(
			{
				"doctype": DRAFT_DOCTYPE,
				"task": doc.name,
				"status": DRAFT,
				"payload": text,
				"base_modified": doc.modified,
			}
		).insert()
	for duplicate in context.get("duplicates") or []:
		frappe.db.set_value(DRAFT_DOCTYPE, duplicate.get("name"), "status", DISCARDED)


def _draft_overlay():
	"""The caller's drafts as the engine takes them: ``exclude`` (drafted task names), ``extra``
	(``(state, crew)`` as drafted), ``drafted`` (per task, what the card must show) and ``stale``
	(drafts that cannot be shown: the task changed since, or is gone)."""
	rows, _duplicates = _draft_rows()
	overlay = {"exclude": set(), "extra": [], "drafted": {}, "stale": []}
	docs = []
	for row in rows:
		doc, problem = _drafted_doc(row)
		if problem:
			overlay["stale"].append(
				{"task": row.get("task"), "subject": row.get("subject") or row.get("task"), "reason": problem}
			)
			continue
		docs.append(doc)
	titles = _project_titles(doc.get("project") for doc in docs)
	for doc in docs:
		state = _state(doc)
		state["project_title"] = titles.get(doc.get("project")) if doc.get("project") else None
		overlay["exclude"].add(doc.name)
		overlay["extra"].append((state, _current_crew(doc)))
		overlay["drafted"][doc.name] = {
			"credentials": [
				row.get("credential_type") for row in (doc.get("custom_required_credentials") or [])
			]
		}
	return overlay


def _with_drafts(result, overlay, start, end):
	"""``get_planner``'s answer with the drafts marked. A drafted card carries ``"drafted": True``
	and its drafted qualifications; a draft moved out of the range leaves it; an undated draft stays
	in the Unscheduled tray (as drafted) and a dated one leaves the tray. ``drafts`` counts them."""
	drafted = overlay["drafted"]
	tasks, tray = [], []
	for card in result["tasks"]:
		if card["name"] in drafted:
			card["drafted"] = True
			card["credentials"] = list(drafted[card["name"]]["credentials"])
			if not card.get("start"):
				tray.append(card)
				continue
			if (card.get("end") or card["start"]) < str(start) or card["start"] > str(end):
				continue
		tasks.append(card)
	visible = {card["name"] for card in tasks}
	result["tasks"] = tasks
	result["unscheduled"] = [card for card in result["unscheduled"] if card["name"] not in drafted] + tray
	result["needs_crew"] = [name for name in result["needs_crew"] if name in visible or name not in drafted]
	result["drafts"] = {
		"count": len(drafted) + len(overlay["stale"]),
		"tasks": sorted(drafted),
		"stale": overlay["stale"],
	}
	return result


def _apply_takes_tentative():
	import inspect

	return "tentative" in inspect.signature(_apply).parameters


def _publish_plan(row):
	"""``(plan, problem)`` for one Draft row: the task before and after, and its save arguments."""
	doc, problem = _drafted_doc(row)
	if problem:
		return None, problem
	if not frappe.has_permission("Task", "write", doc=doc):
		return None, _("You cannot edit this task.")
	stored = frappe.get_doc("Task", doc.name)
	return {
		"row": row,
		"name": doc.name,
		"subject": doc.get("subject") or doc.name,
		"kwargs": payload_kwargs(load_payload(row.get("payload")), with_tentative=_apply_takes_tentative()),
		"before": (_state(stored), _current_crew(stored)),
		"after": (_state(doc), _current_crew(doc)),
	}, None


def _batch_check(plans):
	""":func:`batch_conflicts` for a publish: the week as stored against the week with every
	draft in place, for everyone on any of the drafted tasks before or after."""
	names = [plan["name"] for plan in plans]
	resources, first, last = set(), None, None
	for plan in plans:
		for state, crew in (plan["before"], plan["after"]):
			span = engine.task_span(state)
			if span:
				first = span[0] if first is None else min(first, span[0])
				last = span[1] if last is None else max(last, span[1])
			resources.update(m.get("resource") for m in crew or [] if m.get("resource"))
	if not resources or first is None:
		return {}
	people = sorted(resources)
	before = engine._compute(first, last, people)
	after = engine._compute(first, last, people, exclude=set(names), extra=[plan["after"] for plan in plans])
	labels = {r["name"]: r["label"] for r in after["resources"]}
	return batch_conflicts(before["days"], after["days"], names, labels)


def _publish_one(plan, reason):
	"""Save one draft through ``_apply``. ``("saved", result)``, ``("wait", None)`` when it asks
	for a reason, or ``("failed", sentence)``, with its own savepoint so a refusal undoes only it."""
	log, mark = _quiet_message_log()
	frappe.db.savepoint("planner_publish")
	try:
		doc = frappe.get_doc("Task", plan["name"])
		if not same_moment(plan["row"].get("base_modified"), doc.modified):
			return "failed", _("Changed by someone else since you drafted it.")
		result = _apply(doc, reason=reason, **plan["kwargs"])
	except Exception as exc:
		frappe.db.rollback(save_point="planner_publish")
		if log is not None:
			del log[mark:]
		return "failed", _("Not saved: {0}").format(frappe.utils.strip_html_tags(str(exc))[:300])
	if result.get("needs_reason"):
		return "wait", None
	return "saved", result


def _apply_plans(plans, reason):
	"""Save every plan. A task that asks for a reason only because the batch is half saved is
	retried after the rest; one still asking on the third pass is saved with the fallback reason
	(the batch check already passed, or the planner gave a reason for all of it)."""
	saved, failed, pending = [], [], list(plans)
	passes = (reason or None, reason or None, reason or _(PUBLISH_FALLBACK_REASON))
	frappe.flags.planner_publishing = True
	try:
		for pass_reason in passes:
			waiting = []
			for plan in pending:
				status, value = _publish_one(plan, pass_reason)
				if status == "saved":
					plan["result"] = value
					saved.append(plan)
				elif status == "wait":
					waiting.append(plan)
				else:
					failed.append({"task": plan["name"], "subject": plan["subject"], "reason": value})
			pending = waiting
			if not pending:
				break
	finally:
		frappe.flags.planner_publishing = False
	for plan in pending:
		failed.append({"task": plan["name"], "subject": plan["subject"], "reason": _("Not saved.")})
	return saved, failed


def _notify_publish(saved):
	"""One notice per person whose bookings moved (``planner_notices.send_publish_notices``)."""
	from erpnext_enhancements.project_enhancements import planner_notices as notices

	resources = {
		member.get("resource")
		for plan in saved
		for _state_, crew in (plan["before"], plan["after"])
		for member in crew or []
	}
	users = {name: row.get("user") for name, row in _resources(resources).items()}

	def snap(state, crew):
		people = [(users.get(m.get("resource")), m.get("hours")) for m in crew or []]
		return notices.snapshot(state, [u for u, _h in people], {u: h for u, h in people if u})

	window = notices.alert_window() if notices.setting("change_alerts") else None
	out = {}
	for plan in saved:
		before, after = snap(*plan["before"]), snap(*plan["after"])
		urgent = notices.change_lines(before, after, window, long=True) if window else {}
		for user, entries in notices.change_lines(before, after).items():
			notice = out.setdefault(user, {"lines": [], "urgent": [], "task": plan["name"], "day": None})
			notice["lines"].extend(entries)
			notice["urgent"].extend(urgent.get(user) or [])
			days = [e["day"] for e in notice["lines"] if e.get("day")]
			notice["day"] = min(days) if days else None
	out.pop(frappe.session.user, None)
	return notices.send_publish_notices(out, frappe.session.user) if out else 0


@frappe.whitelist(methods=["POST"])
def publish_drafts(reason=None):
	"""Publish every one of the caller's drafts, all or nothing on conflicts.

	* A draft whose task changed since it was drafted (or is gone, or no longer applies) is
	  **skipped** and listed in ``skipped``; its row stays a Draft for the planner to redo or discard.
	* When the drafts together create conflicts and no ``reason`` came, nothing is saved:
	  ``{"needs_reason": True, "conflicts": {person: [..]}, "skipped": [..], "count": n}``.
	* Otherwise every draft is saved through ``_apply`` (the same path as ``save_task``), its row
	  marked Published, and each person whose days moved gets one notice.

	Returns ``{"published": [{task, subject}], "skipped": [..], "failed": [..], "notified": n,
	"conflicts": {..}}``; ``failed`` lists a task ERPNext refused (its own validation), which never
	stops the others.
	"""
	_require_planner()
	reason = (given(reason) or "").strip()
	rows, _duplicates = _draft_rows()
	plans, skipped = [], []
	for row in rows:
		plan, problem = _publish_plan(row)
		if problem:
			skipped.append(
				{"task": row.get("task"), "subject": row.get("subject") or row.get("task"), "reason": problem}
			)
		else:
			plans.append(plan)
	if not plans:
		return {"published": [], "skipped": skipped, "failed": [], "notified": 0, "conflicts": {}}

	conflicts = _batch_check(plans)
	if conflicts and not reason:
		return {"needs_reason": True, "conflicts": conflicts, "skipped": skipped, "count": len(plans)}

	saved, failed = _apply_plans(plans, reason)
	now = frappe.utils.now_datetime()
	for plan in saved:
		draft = frappe.get_doc(DRAFT_DOCTYPE, plan["row"].get("name"))
		draft.status = PUBLISHED
		draft.published_on = now
		draft.reason = reason or None
		draft.save()
	notified = _notify_publish(saved) if saved else 0
	return {
		"published": [{"task": plan["name"], "subject": plan["subject"]} for plan in saved],
		"skipped": skipped,
		"failed": failed,
		"notified": notified,
		"conflicts": conflicts,
	}


@frappe.whitelist(methods=["POST"])
def discard_drafts(tasks=None):
	"""Discard the caller's drafts: all of them, or only those for ``tasks`` (a JSON list). The rows
	are marked Discarded, not deleted. Returns ``{"discarded": n}``."""
	_require_planner()
	wanted = parse_list(given(tasks))
	rows, duplicates = _draft_rows()
	count = 0
	for row in rows + duplicates:
		if wanted is not None and row.get("task") not in wanted:
			continue
		draft = frappe.get_doc(DRAFT_DOCTYPE, row.get("name"))
		draft.status = DISCARDED
		draft.save()
		count += 1
	return {"discarded": count}


# ---------------------------------------------------------------------- Phase 3B: people's days


def _first_weekday():
	try:
		return frappe.get_system_settings("first_day_of_the_week") or "Sunday"
	except Exception:
		return "Sunday"


def _visit_people(refs):
	"""``{visit record: [names]}``: each stored visit's technician and crew, by full name."""
	refs = sorted({ref for ref in refs or () if ref})
	if not refs:
		return {}
	try:
		people = {}
		for row in frappe.db.sql(
			"SELECT name, technician FROM `tabSapphire Maintenance Record` WHERE name IN %(names)s",
			{"names": tuple(refs)},
			as_dict=True,
		):
			if row.get("technician"):
				people.setdefault(row.get("name"), []).append(row.get("technician"))
		for row in frappe.db.sql(
			"""
			SELECT parent, user FROM `tabSapphire Visit Crew Member`
			WHERE parenttype = %(record)s AND parentfield = 'crew' AND parent IN %(names)s
			ORDER BY parent, idx
			""",
			{"record": "Sapphire Maintenance Record", "names": tuple(refs)},
			as_dict=True,
		):
			if row.get("user") and row.get("user") not in people.get(row.get("parent"), []):
				people.setdefault(row.get("parent"), []).append(row.get("user"))
		users = sorted({user for names in people.values() for user in names})
		full = (
			{
				row.get("name"): row.get("full_name") or row.get("name")
				for row in frappe.get_all(
					"User", filters={"name": ["in", users]}, fields=["name", "full_name"], limit_page_length=0
				)
			}
			if users
			else {}
		)
		return {ref: [full.get(user, user) for user in names] for ref, names in people.items()}
	except Exception:
		frappe.log_error(title="Project Planner: visit crew lookup failed", message=frappe.get_traceback())
		return {}


def _task_notes(refs):
	"""``{task: plain text}``: the start of each task's description, for the crew to read."""
	refs = sorted({ref for ref in refs or () if ref})
	if not refs:
		return {}
	out = {}
	for row in frappe.get_all(
		"Task", filters={"name": ["in", refs]}, fields=["name", "description"], limit_page_length=0
	):
		text = " ".join(frappe.utils.strip_html_tags(str(row.get("description") or "")).split())
		if text:
			out[row.get("name")] = text if len(text) <= NOTE_CHARS else text[: NOTE_CHARS - 1].rstrip() + "…"
	return out


def _people_days(start, end, resources=None):
	"""``(data, {resource: [day_entry, ...]})`` for ``start``..``end``: one engine call for everyone,
	one bulk locate for every stop, the same route order and arrival times the route view shows."""
	data = engine._compute(start, end, resources)
	labels = {r["name"]: r["label"] for r in data["resources"]}
	stops = [
		booking
		for per_day in data["days"].values()
		for cell in per_day.values()
		for booking in cell.get("bookings") or []
		if booking.get("kind") in routing.STOP_KINDS
	]
	details = routing.locate(stops, detail=True) if stops else {}
	titles = _project_titles(b.get("project") for b in stops)
	missing = {m.get("resource") for crew in (data.get("crews") or {}).values() for m in crew or []} - set(
		labels
	)
	names = dict(labels)
	for name, row in _resources(missing).items():
		names[name] = row.get("resource_name") or name
	mates = crewmates(
		data["days"],
		names,
		data.get("crews"),
		_visit_people(b.get("ref") for b in stops if b.get("kind") == "visit"),
	)
	notes = _task_notes(b.get("ref") for b in stops if b.get("kind") in ("task", "rental"))
	routes = data.get("routes") or {}
	out = {}
	for person in data["resources"]:
		per_day = data["days"].get(person["name"]) or {}
		out[person["name"]] = [
			day_entry(
				person,
				day,
				per_day.get(str(day)),
				routes.get((person["name"], day)),
				data["settings"],
				details,
				titles,
				mates,
				notes,
			)
			for day in engine.daterange(start, end)
		]
	return data, out


@frappe.whitelist()
def get_my_week(date=None):
	"""The signed-in person's own week, for the phone (P3.10): ``/app/project-planner/my-week``.

	Found through Planner Resource ``user``. ``{"resource", "label", "start", "end", "today",
	"days": [day_entry]}``; with no active Planner Resource, ``resource`` is None and ``message``
	says so. Open to every planner role (technicians have Maintenance User).
	"""
	_require_planner()
	from erpnext_enhancements.project_enhancements import planner_notices

	day = getdate(given(date) or nowdate())
	first = week_start(day, _first_weekday())
	last = first + datetime.timedelta(days=6)
	answer = {
		"start": str(first),
		"end": str(last),
		"today": nowdate(),
		"resource": None,
		"label": None,
		"days": [],
	}
	resource = planner_notices.resource_of(frappe.session.user)
	if not resource:
		answer["message"] = _(
			"You are not on the planner's list of people yet, so there is no week to show. "
			"Ask a project manager to add you under Planner Resources."
		)
		return answer
	data, days = _people_days(first, last, [resource])
	person = next((r for r in data["resources"] if r["name"] == resource), None)
	if not person:
		answer["message"] = _("Your planner entry is not active, so there is no week to show.")
		return answer
	answer.update(resource=resource, label=person["label"], days=days.get(resource) or [])
	return answer


def _sheet_cell(entry):
	"""One person-day of the crew sheet as print-safe HTML; every value escaped."""
	from erpnext_enhancements import print_style as ps

	esc = ps.escape_html
	parts = []
	if entry.get("travel"):
		parts.append(f'<div class="cs-note">{esc(entry["travel"])}</div>')
	elif entry.get("off") and not entry.get("items"):
		parts.append(f'<div class="cs-off">{esc(_(entry["off"]))}</div>')
	for item in entry.get("items") or []:
		when = "–".join(item["slot"]) if item.get("slot") else (item.get("arrive") or "")
		place = item.get("project_title") or item.get("label") or item.get("ref") or ""
		head = f"<b>{esc(place)}</b>"
		facts = [esc(when)] if when else []
		if flt(item.get("hours")) > 0:
			facts.append(esc(engine.fmt_hours(item["hours"])) + "h")
		lines = [head + (f' <span class="cs-when">{" · ".join(facts)}</span>' if facts else "")]
		if item.get("label") and item.get("label") != place:
			lines.append(esc(item["label"]))
		if item.get("address"):
			lines.append(f'<span class="cs-sub">{esc(item["address"])}</span>')
		if item.get("crew"):
			lines.append(f'<span class="cs-sub">{esc(_("with {0}").format(", ".join(item["crew"])))}</span>')
		parts.append('<div class="cs-item">' + "<br>".join(lines) + "</div>")
	return "".join(parts) or '<span class="cs-free">—</span>'


CREW_SHEET_CSS = """
@page { size: letter landscape; margin: 9mm; }
html, body { margin: 0; background: #ffffff; }
body { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.cs-grid { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 9px; line-height: 1.3; }
.cs-grid th { text-align: left; vertical-align: bottom; }
.cs-grid td { vertical-align: top; word-wrap: break-word; overflow-wrap: anywhere; }
.cs-grid tr { page-break-inside: avoid; break-inside: avoid; }
.cs-person { width: 11%; font-weight: 700; }
.cs-item { margin: 0 0 4px; }
.cs-when, .cs-sub { color: #363636; }
.cs-off, .cs-note, .cs-free { color: #363636; font-style: italic; }
.cs-foot { margin-top: 6px; font-size: 9px; color: #363636; }
"""


def crew_sheet_document(first, rows, title=None, group=None, printed_on=None):
	"""The printable weekly crew sheet: one row per person, one column per day (P3.9).

	``rows`` are ``[{"label", "days": [day_entry x 7]}]``. On the print design system's chrome
	(``print_style``: the stripe, the letterhead, ruled tables), landscape, sized so ten people fit
	one Letter page. People with nothing booked all week are listed under the table rather than
	given an empty row each. Every value is escaped; the page prints it in a browser window, never
	through the server PDF (broken on production, see the project_enhancements README).
	"""
	from erpnext_enhancements import print_style as ps

	esc = ps.escape_html
	first = getdate(first)
	days = [first + datetime.timedelta(days=i) for i in range(7)]
	last = days[-1]
	span = (
		f"{first:%b} {first.day} – {last.day}, {last.year}"
		if first.month == last.month
		else f"{first:%b} {first.day} – {last:%b} {last.day}, {last.year}"
	)
	title = title or _("Crew sheet")
	meta = esc(_("Week of {0}").format(span))
	if group:
		meta += "<br>" + esc(_("Group: {0}").format(_(group)))
	if printed_on:
		meta += "<br>" + esc(_("Printed {0}").format(printed_on))

	busy = [row for row in rows if any(d.get("items") or d.get("travel") for d in row.get("days") or [])]
	free = [row.get("label") or "" for row in rows if row not in busy]

	head = f'<th class="cs-person" style="{ps.th()}">{esc(_("Person"))}</th>' + "".join(
		f'<th style="{ps.th()}">{esc(f"{day:%a} {day:%b} {day.day}")}</th>' for day in days
	)
	body = []
	for row in busy:
		cells = "".join(
			f'<td style="{ps.TD}">{_sheet_cell(entry)}</td>' for entry in (row.get("days") or [])[:7]
		)
		body.append(
			f'<tr><td class="cs-person" style="{ps.TD}">{esc(row.get("label") or "")}</td>{cells}</tr>'
		)
	if not body:
		body.append(
			f'<tr><td colspan="8" style="{ps.TD}">{esc(_("Nothing is booked on anyone this week."))}</td></tr>'
		)
	foot = (
		f'<div class="cs-foot">{esc(_("Nothing booked all week: {0}").format(", ".join(free)))}</div>'
		if free and busy
		else ""
	)
	return (
		"<!doctype html><html><head><meta charset='utf-8'>"
		f"<title>{esc(title)} · {esc(span)}</title>"
		f"<style>{CREW_SHEET_CSS}</style></head><body>"
		+ ps.page_open(None)
		+ ps.letterhead(None, esc(_("Crew sheet")), esc(title), meta)
		+ f'<table class="cs-grid"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'
		+ foot
		+ ps.page_close(None)
		+ "</body></html>"
	)


@frappe.whitelist()
def crew_sheet_html(start, group=None):
	"""The printable weekly crew sheet for the week holding ``start`` (P3.9), as an HTML document.

	``group`` narrows it to one Planner Resource group, as the page's filter does. The page opens it
	in a new window and prints it there; nothing here makes a PDF.
	"""
	_require_planner()
	first = week_start(getdate(start), _first_weekday())
	last = first + datetime.timedelta(days=6)
	group = given(group)
	names = None
	if group:
		names = frappe.get_all(
			"Planner Resource",
			filters={"is_active": 1, "resource_group": group},
			pluck="name",
			limit_page_length=0,
		)
	data, days = _people_days(first, last, names)
	rows = [
		{"label": person["label"], "days": days.get(person["name"]) or []} for person in data["resources"]
	]
	return crew_sheet_document(
		first,
		rows,
		group=group,
		printed_on=frappe.utils.format_datetime(frappe.utils.now_datetime(), "MMM d, yyyy h:mm a"),
	)


@frappe.whitelist(methods=["POST"])
def send_digest_preview(date=None):
	"""Email the caller what their own combined 6 AM digest would say (P3.8 "Send me a preview").

	System Manager or Projects Manager. Works with the digest switched off, never texts, and never
	records the day as sent. ``{"sent": bool, "message": str}``.
	"""
	if frappe.session.user != "Administrator" and not DIGEST_PREVIEW_ROLES & set(frappe.get_roles()):
		frappe.throw(
			_("Only a System Manager or Projects Manager can preview the digest."), frappe.PermissionError
		)
	from erpnext_enhancements.project_enhancements import planner_digest

	return planner_digest.send_preview(frappe.session.user, getdate(given(date) or nowdate()))
