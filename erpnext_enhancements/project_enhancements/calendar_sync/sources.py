# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Read ERPNext into calendar items: what each synced record says, as a plain dict.

One reader per kind, each answering for one record (:func:`item_for`), plus the window readers the
nightly repair and the preview use (:func:`refs_in_window`, :func:`projected_items`). An item is the
dict ``payload.build_event`` takes; ``state`` is ``"active"``, ``"finished"`` (keeps its event with a
✓) or ``"gone"`` (its event is removed).

**Drafts never sync, by construction.** Both planners keep unpublished changes in ``Planner Draft
Change`` rows and never touch the Task or visit until Publish, so reading the saved records *is*
reading committed work. Nothing here reads ``Planner Draft Change``, ``api/maintenance_drafts`` or
either planner's draft overlay; ``tests/test_calendar_sync.py`` fails the build if anything starts
to.

What counts, and why:

* **Tasks** on customer jobs only, by the planners' own rule (``crew_availability.is_planner_job``
  and ``planner_job_condition``), so an internal project never reaches a shared calendar. Dated by
  ``crew_availability.task_span`` (``exp_start_date`` → ``exp_end_date``, or the day of a same-day
  slot), exactly as the planner draws it. A group task, a template, an undated task, a Canceled task
  or a task on a Canceled project is ``gone``; Completed or Invoiced, or on a Completed / Invoiced /
  Paid project, is ``finished``. A pencil (``custom_tentative``) is tentative.
* **Maintenance visits** on the day the Maintenance Planner shows them (the scheduled date while a
  draft; the visit date once done or in review). Submitted or Pending Review is ``finished``; a
  canceled or unscheduled visit is ``gone``; ``tentative`` is a pencil.
* **Projected contract visits**: ``api/maintenance_planner._projections``, the same arithmetic the
  planner draws, decorated by its own ``_decorate``. A contract whose customer is on service hold
  projects nothing here.
* **Day notes** and **unavailable blocks** (Phase 6D). A block's ``note`` is private to the person
  and the schedulers and is **never selected**: only who, which day and the times.

Never put free text a person might have typed a code into in an item: task descriptions, project
notes, visit notes, a Maintenance Profile's ``access_codes``, a block's note. ``payload`` scrubs the
few free-text fields that do appear (subject, address, a day note), and the test pins both.
"""

import datetime

import frappe

from erpnext_enhancements.project_enhancements import crew_availability as ca
from erpnext_enhancements.project_enhancements.calendar_sync import payload

TASK = "Task"
VISIT = "Sapphire Maintenance Record"
CONTRACT = "Sapphire Maintenance Contract"
DAY_NOTE = "Planner Day Note"
BLOCK = "Planner Block"
SOURCE_DOCTYPES = (TASK, VISIT, CONTRACT, DAY_NOTE, BLOCK)

CANCELED = ("Canceled", "Cancelled")
TASK_GONE_STATUSES = ("Canceled", "Cancelled", "Template")
TASK_DONE_STATUSES = ("Completed", "Invoiced")
PROJECT_DONE_STATUSES = ("Completed", "Invoiced", "Paid")
PENDING_REVIEW = "Pending Review"

WINDOW_LIMIT = 5000

PROJECT_PLANNER_ROUTE = "/app/project-planner/week/{day}"
MAINTENANCE_PLANNER_ROUTE = "/app/maintenance-planner/week/{day}"


def _today():
	from frappe.utils import getdate, nowdate

	return getdate(nowdate())


def _url(path):
	from frappe.utils import get_url

	return get_url(path)


def _has_column(doctype, column):
	try:
		return bool(frappe.db.has_column(doctype, column))
	except Exception:
		return False


def _table_exists(doctype):
	try:
		return bool(frappe.db.table_exists(doctype))
	except Exception:
		return False


def _float(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def gone(doctype, name, key=None, kind=None):
	return {"doctype": doctype, "name": name, "key": key, "kind": kind, "state": payload.STATE_GONE}


def _short_site(title):
	from erpnext_enhancements.api.maintenance_planner import short_site_name

	return short_site_name(title) if title else ""


# ---------------------------------------------------------------------- shared lookups


def _project_row(project):
	"""``{name, project_name, status, project_type, custom_project_address, custom_customer__lead_address}``."""
	if not project:
		return {}
	fields = ["name", "project_name", "status", "project_type"]
	fields += [
		c for c in ("custom_project_address", "custom_customer__lead_address") if _has_column("Project", c)
	]
	row = frappe.db.get_value("Project", project, fields, as_dict=True)
	return dict(row) if row else {}


def _project_streams(project, project_type=None):
	"""The project's value streams in priority order: its type first, then its Value Stream rows."""
	out = []
	if project_type:
		out.append(project_type)
	if project:
		columns = ca._stream_columns()
		if columns:
			for row in (
				frappe.db.sql(
					f"SELECT {', '.join(f'`{c}`' for c in columns)} FROM `tabValue Stream` "
					"WHERE parenttype = 'Project' AND parent = %(project)s ORDER BY idx",
					{"project": project},
					as_dict=True,
				)
				or []
			):
				for column in columns:
					if row.get(column) and row.get(column) not in out:
						out.append(row.get(column))
	return out


def _address_text(project_row):
	if not project_row:
		return ""
	try:
		from erpnext_enhancements.workforce.sites import project_address_text

		return project_address_text(project_row) or ""
	except Exception:
		return ""


def _locate(kind, ref, project):
	"""``(point, address text)`` the way the route view finds the stop. Never raises."""
	try:
		from erpnext_enhancements.project_enhancements import routing

		found = routing.locate([{"kind": kind, "ref": ref, "project": project}], detail=True).get(ref) or {}
		return found.get("point"), found.get("address") or ""
	except Exception:
		return None, ""


def _weather(point, first, last):
	"""``["Thu Oct 15: Rain 70%, Wind 28 mph"]`` for the flagged days of a span. Cached by
	``planner_weather`` (one Open-Meteo request per uncached site, three hours); never raises."""
	if not point:
		return []
	try:
		from erpnext_enhancements.project_enhancements import planner_weather

		forecast = planner_weather.forecasts([point]).get(planner_weather.point_key(point))
		days = planner_weather.span_weather((first, last), forecast, _today()) or []
		return [
			f"{payload.day_label(day['date'])}: {', '.join(day['flags'])}" for day in days if day.get("flags")
		]
	except Exception:
		return []


def _user_names(users):
	users = sorted({u for u in users or [] if u})
	if not users:
		return {}
	return {
		row.get("name"): row.get("full_name") or row.get("name")
		for row in frappe.get_all("User", filters={"name": ["in", users]}, fields=["name", "full_name"])
	}


# ---------------------------------------------------------------------- tasks


def _task_row(name):
	cols = ca._task_columns()
	selected = ", ".join(f"{expr} AS `{column}`" for column, expr in cols.items())
	outdoor = "t.`custom_outdoor`" if _has_column("Task", "custom_outdoor") else "0"
	type_expr, stream = ca.planner_job_sql("p")
	rows = frappe.db.sql(
		f"""
		SELECT
			t.name, t.subject, t.project, t.status, t.exp_start_date, t.exp_end_date,
			t.expected_time, t.is_group, t.is_template, {selected}, {outdoor} AS custom_outdoor,
			p.project_name AS project_title, p.status AS project_status,
			{type_expr} AS project_type, {stream} AS planner_stream
		FROM `tabTask` t
		LEFT JOIN `tabProject` p ON p.name = t.project
		WHERE t.name = %(name)s
		""",
		{"name": name, **ca.PLANNER_SQL_VALUES},
		as_dict=True,
	)
	return rows[0] if rows else None


def task_state(row):
	"""``"gone"`` / ``"finished"`` / ``"active"`` for a task row (see the module docstring)."""
	if not row or row.get("is_group") or row.get("is_template"):
		return payload.STATE_GONE
	if not ca.is_planner_job(row):
		return payload.STATE_GONE
	if row.get("status") in TASK_GONE_STATUSES or row.get("project_status") in CANCELED:
		return payload.STATE_GONE
	if not ca.task_span(row):
		return payload.STATE_GONE
	if row.get("status") in TASK_DONE_STATUSES or row.get("project_status") in PROJECT_DONE_STATUSES:
		return payload.STATE_FINISHED
	return payload.STATE_ACTIVE


def _task_crew(name):
	"""``[{"name", "first", "lead"}]``: the crew table, else open assignees who are Planner Resources."""
	rows, todos = ca.read_crews([name])
	users = todos.get(name) or []
	user_to_resource = {}
	if users:
		for row in frappe.get_all(
			"Planner Resource",
			filters={"user": ["in", users], "is_active": 1},
			fields=["name", "user"],
		):
			user_to_resource.setdefault(row.get("user"), row.get("name"))
	crew = ca.resolve_crew(rows.get(name) or [], users, user_to_resource)
	resources = {}
	if crew:
		for row in frappe.get_all(
			"Planner Resource",
			filters={"name": ["in", [c["resource"] for c in crew]]},
			fields=["name", "resource_name", "resource_type"],
		):
			resources[row.get("name")] = row
	out = []
	for member in sorted(crew, key=lambda m: not m.get("is_lead")):
		info = resources.get(member["resource"]) or {}
		label = member.get("label") or info.get("resource_name") or member["resource"]
		# An outside crew is a company: its whole name, not its first word.
		first = payload.first_name(label) if info.get("resource_type") != "Subcontractor" else label
		# ``resource``: whose route times the event (C3, timing.candidates); ``hours``: the crew row's
		# explicit hours (None for an even share), which decide a full-day task. Neither is shown.
		out.append(
			{
				"name": label,
				"first": first,
				"lead": bool(member.get("is_lead")),
				"resource": member["resource"],
				"hours": member.get("hours"),
			}
		)
	return out


def task_is_full_day(row, crew):
	"""True when the planner books the task as a whole day (C3: it stays all-day on the calendar).

	The engine's rule (``crew_availability.allocate_task``): no estimate, no time slot, no rental
	kind with default hours, and nobody on the crew with hours of their own.
	"""
	if _float(row.get("expected_time")) > 0 or ca.task_slot(row):
		return False
	if row.get("custom_rental_task_kind") in ca.RENTAL_KIND_HOURS:
		return False
	return not any(_float(member.get("hours")) > 0 for member in crew or [])


def _credentials(name):
	if not _table_exists("Task Required Credential"):
		return []
	return [
		row.get("credential_type")
		for row in frappe.get_all(
			"Task Required Credential",
			filters={"parenttype": "Task", "parent": name},
			fields=["credential_type", "idx"],
			order_by="idx asc",
		)
		if row.get("credential_type")
	]


def task_item(name):
	row = _task_row(name)
	state = task_state(row)
	if state == payload.STATE_GONE:
		return gone(TASK, name, kind=payload.KIND_TASK)
	first, last = ca.task_span(row)
	project = _project_row(row.get("project")) if row.get("project") else {}
	rental = bool(row.get("custom_rental_booking"))
	streams = ["Events"] if rental else _project_streams(row.get("project"), row.get("project_type"))
	point, address = _locate("rental" if rental else "task", name, row.get("project"))
	equipment = [e.get("label") for e in (ca.read_equipment([name]).get(name) or []) if e.get("label")]
	weather = []
	if state == payload.STATE_ACTIVE and _float(row.get("custom_outdoor")):
		weather = _weather(point, first, last)
	crew = _task_crew(name)
	return {
		"doctype": TASK,
		"name": name,
		"kind": payload.KIND_TASK,
		"calendar": "projects",
		"state": state,
		"tentative": ca.is_tentative(row),
		"start": first,
		"end": last,
		"site": _short_site(
			row.get("project_title") or project.get("project_name") or row.get("custom_rental_booking")
		),
		"label": row.get("subject") or name,
		"crew": crew,
		"hours": row.get("expected_time"),
		"full_day": task_is_full_day(row, crew),
		"equipment": equipment,
		"credentials": _credentials(name),
		"weather": weather,
		"location": address or _address_text(project),
		"streams": streams,
		"link": _url(PROJECT_PLANNER_ROUTE.format(day=first)),
	}


# ---------------------------------------------------------------------- visits


def _visit_row(name):
	tentative = "tentative" if _has_column(VISIT, "tentative") else "0 AS tentative"
	rows = frappe.db.sql(
		f"""
		SELECT name, project, customer, maintenance_contract, serial_no, visit_label, technician,
			scheduled_visit_date, visit_date, docstatus, workflow_state, planned_hours, full_day,
			{tentative}, modified
		FROM `tabSapphire Maintenance Record`
		WHERE name = %(name)s
		""",
		{"name": name},
		as_dict=True,
	)
	return rows[0] if rows else None


def visit_day(row):
	"""The day the Maintenance Planner draws a visit on (``_records_between``'s ``plan_date``)."""
	if not row:
		return None
	if int(row.get("docstatus") or 0) == 1 or row.get("workflow_state") == PENDING_REVIEW:
		value = row.get("visit_date") or row.get("scheduled_visit_date") or row.get("modified")
	else:
		value = row.get("scheduled_visit_date") or row.get("visit_date")
	return payload._date(value)


def visit_state(row):
	if not row or int(row.get("docstatus") or 0) == 2 or not visit_day(row):
		return payload.STATE_GONE
	if int(row.get("docstatus") or 0) == 1 or row.get("workflow_state") == PENDING_REVIEW:
		return payload.STATE_FINISHED
	return payload.STATE_ACTIVE


def visit_item(name):
	row = _visit_row(name)
	state = visit_state(row)
	if state == payload.STATE_GONE:
		return gone(VISIT, name, kind=payload.KIND_VISIT)
	from erpnext_enhancements.api.maintenance_planner import feature_label
	from erpnext_enhancements.sapphire_maintenance.visit_crew import crew_rows_for

	day = visit_day(row)
	project = _project_row(row.get("project")) if row.get("project") else {}
	crew_rows = (crew_rows_for([name]) or {}).get(name) or []
	names = _user_names([row.get("technician")] + [r.get("user") for r in crew_rows])
	# ``user``: whose route times the event (C3, timing.candidates); never shown.
	crew = []
	if row.get("technician"):
		crew.append(
			{
				"name": names.get(row["technician"]) or row["technician"],
				"lead": True,
				"user": row["technician"],
			}
		)
	for member in crew_rows:
		if member.get("user") and member.get("user") != row.get("technician"):
			crew.append(
				{
					"name": member.get("full_name") or names.get(member["user"]) or member["user"],
					"user": member["user"],
				}
			)
	point, address = _locate("visit", name, row.get("project"))
	return {
		"doctype": VISIT,
		"name": name,
		"kind": payload.KIND_VISIT,
		"calendar": "maintenance",
		"state": state,
		"tentative": bool(_float(row.get("tentative"))),
		"start": day,
		"end": day,
		"site": _short_site(project.get("project_name") or row.get("project") or row.get("customer")),
		"label": row.get("visit_label") or feature_label(row.get("serial_no")) or "Maintenance visit",
		"crew": crew,
		"hours": row.get("planned_hours"),
		"full_day": bool(_float(row.get("full_day"))),
		"weather": _weather(point, day, day) if state == payload.STATE_ACTIVE else [],
		"location": address or _address_text(project),
		"streams": ["Service"],
		"link": _url(MAINTENANCE_PLANNER_ROUTE.format(day=day)),
	}


# ---------------------------------------------------------------------- projected visits


def projected_items(start, end, today=None):
	"""Every projected (not yet drafted) contract visit from ``start`` to ``end`` as items.

	The Maintenance Planner's own ``_projections`` and ``_decorate``, so the calendar and the
	planner can never disagree. A contract on service hold projects nothing. One ``routing.locate``
	for all of them.
	"""
	from erpnext_enhancements.api.maintenance_planner import _decorate, _projections

	today = today or _today()
	entries = [e for e in (_projections(start, end, today) or []) if not e.get("on_hold")]
	if not entries:
		return []
	_decorate(entries)
	names = _user_names([e.get("technician") for e in entries])
	located = {}
	try:
		from erpnext_enhancements.project_enhancements import routing

		located = routing.locate(
			[
				{"kind": "visit", "ref": e["key"], "project": e.get("project")}
				for e in entries
				if e.get("project")
			],
			detail=True,
		)
	except Exception:
		located = {}
	out = []
	for entry in entries:
		day = payload._date(entry.get("date"))
		crew = []
		if entry.get("technician"):
			crew.append(
				{
					"name": names.get(entry["technician"]) or entry["technician"],
					"lead": True,
					"user": entry["technician"],
				}
			)
		for member in entry.get("crew") or []:
			crew.append({"name": member.get("name") or member.get("user"), "user": member.get("user")})
		where = located.get(entry["key"]) or {}
		out.append(
			{
				"doctype": CONTRACT,
				"name": entry["contract"],
				"key": entry["key"],
				"kind": payload.KIND_PROJECTED,
				"calendar": "maintenance",
				"state": payload.STATE_ACTIVE,
				"tentative": True,
				"start": day,
				"end": day,
				"site": entry.get("site") or "",
				"label": entry.get("label") or entry.get("feature") or "Maintenance visit",
				"crew": crew,
				"hours": entry.get("planned_hours"),
				"full_day": bool(entry.get("full_day")),
				"location": where.get("address") or "",
				"streams": ["Service"],
				"link": _url(MAINTENANCE_PLANNER_ROUTE.format(day=day)),
			}
		)
	return out


def projected_item(name, key):
	"""One projected visit by its key (``contract|feature or label|date``); ``gone`` when the
	contract no longer projects it (moved, drafted, paused, ended or on hold)."""
	day = payload._date(str(key or "").rsplit("|", 1)[-1]) if key else None
	if not day:
		return gone(CONTRACT, name, key, kind=payload.KIND_PROJECTED)
	for item in projected_items(day, day):
		if item.get("key") == key:
			return item
	return gone(CONTRACT, name, key, kind=payload.KIND_PROJECTED)


# ---------------------------------------------------------------------- day notes and blocks


def day_note_item(name):
	if not _table_exists(DAY_NOTE):
		return gone(DAY_NOTE, name, kind=payload.KIND_DAY_NOTE)
	row = frappe.db.get_value(
		DAY_NOTE, name, ["name", "date", "audience", "project", "project_title", "note"], as_dict=True
	)
	day = payload._date(row.get("date")) if row else None
	if not row or not day:
		return gone(DAY_NOTE, name, kind=payload.KIND_DAY_NOTE)
	return {
		"doctype": DAY_NOTE,
		"name": name,
		"kind": payload.KIND_DAY_NOTE,
		"calendar": "projects",
		"state": payload.STATE_ACTIVE,
		"start": day,
		"end": day,
		"site": _short_site(row.get("project_title") or row.get("project")) if row.get("project") else "",
		"note": row.get("note") or "",
		"audience": row.get("audience") or "",
		"streams": ["Day Note"],
		"link": _url(PROJECT_PLANNER_ROUTE.format(day=day)),
	}


#: What is read from a Planner Block. Never ``note``: it is private to the person and the
#: schedulers (``crew_availability.block_notes`` is its only reader), and these are shared calendars.
BLOCK_FIELDS = ("name", "resource", "resource_name", "date", "all_day", "from_time", "to_time")


def block_item(name):
	if not _table_exists(BLOCK):
		return gone(BLOCK, name, kind=payload.KIND_BLOCK)
	row = frappe.db.get_value(BLOCK, name, list(BLOCK_FIELDS), as_dict=True)
	day = payload._date(row.get("date")) if row else None
	if not row or not day:
		return gone(BLOCK, name, kind=payload.KIND_BLOCK)
	timed = not _float(row.get("all_day"))
	return {
		"doctype": BLOCK,
		"name": name,
		"kind": payload.KIND_BLOCK,
		"calendar": "holidays",
		"state": payload.STATE_ACTIVE,
		"start": day,
		"end": day,
		"person": row.get("resource_name") or row.get("resource") or "",
		"time_text": payload.time_range(row.get("from_time"), row.get("to_time")) if timed else "",
		"streams": ["Unavailable"],
		"link": _url(PROJECT_PLANNER_ROUTE.format(day=day)),
	}


# ---------------------------------------------------------------------- dispatch and windows


def item_for(doctype, name, key=None):
	"""The item for one source record (``gone`` when it no longer exists or no longer syncs)."""
	if doctype == TASK:
		return task_item(name)
	if doctype == VISIT:
		return visit_item(name)
	if doctype == CONTRACT:
		return projected_item(name, key)
	if doctype == DAY_NOTE:
		return day_note_item(name)
	if doctype == BLOCK:
		return block_item(name)
	return gone(doctype, name, key)


def refs_in_window(start, end):
	"""``[(doctype, name)]`` of every task, visit, day note and block that may be on a calendar
	between ``start`` and ``end``, finished and canceled ones included (a canceled one in range is
	how a stale event gets removed). Projected visits are separate (:func:`projected_items`)."""
	refs = []
	cols = ca._task_columns()
	slot_start, slot_end = cols["custom_start_datetime"], cols["custom_end_datetime"]
	job = ca.planner_job_condition("p", cols["custom_rental_booking"])
	for row in (
		frappe.db.sql(
			f"""
		SELECT t.name FROM `tabTask` t
		LEFT JOIN `tabProject` p ON p.name = t.project
		WHERE IFNULL(t.is_group, 0) = 0
			AND IFNULL(t.is_template, 0) = 0
			AND {job}
			AND (
				(
					COALESCE(DATE(t.exp_start_date), DATE(t.exp_end_date), DATE({slot_start})) <= %(end)s
					AND COALESCE(DATE(t.exp_end_date), DATE(t.exp_start_date), DATE({slot_end}), DATE({slot_start})) >= %(start)s
				)
				OR DATE({slot_start}) BETWEEN %(start)s AND %(end)s
			)
		ORDER BY t.exp_start_date, t.name
		LIMIT {WINDOW_LIMIT}
		""",
			{"start": start, "end": end, **ca.PLANNER_SQL_VALUES},
			as_dict=True,
		)
		or []
	):
		refs.append((TASK, row.get("name")))
	for row in (
		frappe.db.sql(
			"""
		SELECT name FROM (
			SELECT name,
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
		LIMIT 5000
		""",
			{"start": start, "end": end, "pending": PENDING_REVIEW},
			as_dict=True,
		)
		or []
	):
		refs.append((VISIT, row.get("name")))
	for doctype in (DAY_NOTE, BLOCK):
		if not _table_exists(doctype):
			continue
		for name in frappe.get_all(
			doctype, filters={"date": ["between", [start, end]]}, pluck="name", limit_page_length=0
		):
			refs.append((doctype, name))
	return refs


def window(days_back, days_ahead, today=None):
	today = today or _today()
	return today - datetime.timedelta(days=int(days_back or 0)), today + datetime.timedelta(
		days=int(days_ahead or 0)
	)
