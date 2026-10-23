# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Fountain Move jobs: a project that moves a customer's fountain, scheduled on the Maintenance Planner.

Nik, 2026-10-21: "the Maintenance planner also handles Fountain Moves". A move job is an ordinary
**Service** project with the Check ``Project.custom_is_fountain_move`` ticked (not a Project Type of
its own, so Service reports keep counting moves). What the tick does:

* **A new project gets the Fountain Move template** (:func:`apply_on_insert`, ``Project.before_insert``).
  Ticking the box fills ``project_template`` with :data:`TEMPLATE` when no template was chosen, and
  choosing that template ticks the box; an explicit other template is never replaced. A ticked project
  with no type becomes Service. ERPNext's own ``Project.after_insert`` (``copy_from_template``) then
  makes the tasks, and ``task_templates.copy_template_planning`` copies their hours and crew size.
* **An Opportunity from the /fountain-move intake** (a Fountain Move Request whose
  ``created_opportunity`` is it) makes a ticked project, and the Create Project dialog preselects the
  template (:func:`opportunity_defaults`).
* **Its tasks are moved on the Maintenance Planner** (:func:`planner_cards`, :func:`move_task`, and
  found by its search box through :func:`search_cards`): a deliberate exception to "each planner moves
  only its own work". Only tasks with hours get a card (the Move day; the office tasks have 0 h and
  stay on the project). The Project Planner still shows
  them and the engine still counts their hours, but read-only: :func:`mark_read_only` clears
  ``movable`` on its cards and :func:`refuse_on_project_planner` refuses a single-task edit there.
  The Project Planner's Overdue tray (bulk complete / cancel / reschedule) and its successor shift
  are left as they are: tidying an overdue task is not scheduling it.

The template itself (:data:`TEMPLATE_TASKS`, Nik's compact list) is seeded once by
``patches.seed_fountain_move_template`` and is Nik's to edit in the Desk afterwards. Everything here
that runs inside another document's save is defensive and never raises: ``doc_events`` fire during
ERPNext's own test bootstrap, before this app's custom fields exist.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

#: The Check on Project that makes it a move job.
FLAG = "custom_is_fountain_move"
#: The Project Template the seed patch creates. Renaming it in the Desk stops the automatic fill.
TEMPLATE = "Fountain Move"
TEMPLATE_DOCTYPE = "Project Template"
#: Nik, 2026-10-21: a move job is a Service project with the box ticked.
PROJECT_TYPE = "Service"
#: This site's spelling of a canceled Task (a Property Setter on Task.status).
CANCELED = "Canceled"
LOG_TITLE = "Fountain Move jobs"
#: How many move tasks the Maintenance Planner reads at most (a range is weeks, not years).
CARD_LIMIT = 500
#: Which tasks the Maintenance Planner draws as cards, as a SQL condition on ``tabTask t`` joined to
#: ``tabProject p``: an open leaf task with hours on a move project that is not closed. One definition,
#: used by :func:`planner_cards` (the calendar) and :func:`search_cards` (the search box), so the box
#: can never list a move the calendar would not draw. Binds ``finished`` and ``closed``.
OPEN_MOVE_TASKS_SQL = f"""p.`{FLAG}` = 1
		AND IFNULL(t.is_group, 0) = 0
		AND IFNULL(t.is_template, 0) = 0
		AND IFNULL(t.expected_time, 0) > 0
		AND IFNULL(t.status, '') NOT IN %(finished)s
		AND IFNULL(p.status, '') NOT IN %(closed)s"""

#: Nik's compact list, 2026-10-21. ``hours`` is the task's whole estimate, which the planner splits
#: over its crew: the Move day is 4 h with two people, so each is booked 2 h. The office tasks have
#: no hours, so they book nobody and get no Maintenance Planner card.
TEMPLATE_TASKS = (
	{
		"subject": "Confirm move date with customer",
		"description": "Call the customer to confirm the move date, the pickup and the new location.",
		"hours": 0,
		"crew_size": 0,
		"outdoor": 0,
	},
	{
		"subject": "Move day",
		"description": (
			"Drain and disconnect, load, transport, set at the new location, reconnect and fill, "
			"startup test, and walk the customer through it."
		),
		"hours": 4,
		"crew_size": 2,
		"outdoor": 1,
	},
	{
		"subject": "Invoice the move",
		"description": "Invoice the customer once the fountain is running at its new location.",
		"hours": 0,
		"crew_size": 0,
		"outdoor": 0,
	},
)

PP_REFUSAL = "{0} is part of a Fountain Move. Move jobs are scheduled on the Maintenance Planner."
NOT_A_MOVE = "{0} is not a task of a Fountain Move project."


# ---------------------------------------------------------------------- pure


def truthy(value):
	"""A Check's value as a bool: 1, "1", True are ticked; 0, "0", "", None are not."""
	if isinstance(value, str):
		value = value.strip()
		return value not in ("", "0", "false", "False")
	return bool(value)


def insert_defaults(values, template_exists=True):
	"""``{field: value}`` to set on a new Project from what it already has.

	* the Fountain Move template ticks the box;
	* a ticked box fills a blank template (when the template exists) and a blank project type.

	Never changes a value somebody set: an explicit other template, or any project type, stays.
	"""
	values = values or {}
	out = {}
	ticked = truthy(values.get(FLAG))
	template = (values.get("project_template") or "").strip()
	if template == TEMPLATE and not ticked:
		out[FLAG] = 1
		ticked = True
	if ticked and not template and template_exists:
		out["project_template"] = TEMPLATE
	if ticked and not (values.get("project_type") or "").strip():
		out["project_type"] = PROJECT_TYPE
	return out


def cardable(row):
	"""True when a move task gets a Maintenance Planner card: a leaf task with hours."""
	row = row or {}
	return not cint(row.get("is_group")) and flt(row.get("expected_time")) > 0


def move_card(row, crew, today, can_move, first_day=None, last_day=None, shown=None):
	"""One Maintenance Planner card for a move task.

	``shown`` is the day the card sits on when that is not its first day (a task that started before
	the visible range); ``start`` then says when it really starts.

	``row`` is the Task row (``project_title``, ``customer`` joined in), ``crew`` its people as
	``[{"user", "name", "hours"}]``, the lead first. The card's ``technician`` is the lead and
	``crew`` the rest, the shape a visit card has, so the page's technician filter, crew view and
	badges read it as they read a visit.
	"""
	row = row or {}
	today = getdate(today)
	people = [p for p in crew or [] if p.get("user")]
	lead = people[0]["user"] if people else None
	crew_size = max(cint(row.get("custom_crew_size")), 0)
	title = row.get("project_title") or row.get("project") or ""
	tentative = truthy(row.get("custom_tentative"))
	return {
		"kind": "move",
		"key": f"move|{row.get('name')}",
		"name": row.get("name"),
		"subject": row.get("subject") or row.get("name"),
		"date": str(shown or first_day) if first_day else None,
		"start": str(first_day) if first_day else None,
		"end": str(last_day) if last_day and last_day != first_day else None,
		"project": row.get("project"),
		"customer": row.get("customer"),
		"site": title,
		"site_full": title,
		"status": "move",
		"task_status": row.get("status"),
		"technician": lead,
		"crew": people[1:],
		"hours": flt(row.get("expected_time")),
		"crew_size": crew_size,
		"short": max(crew_size - len(people), 0),
		"movable": bool(can_move),
		"modified": str(row.get("modified")),
		"overdue": bool(first_day and getdate(last_day or first_day) < today),
		"tentative": tentative,
	}


def crew_users(crew, resources):
	"""The crew's people as ``[{"user", "name", "hours"}]``, lead first, from engine crew entries.

	``crew`` is ``engine.resolve_crew``'s ``[{"resource", "hours", "is_lead", "label"}]`` and
	``resources`` maps a Planner Resource name to its row (``user``, ``resource_name``). A resource
	with no user (a subcontractor, a vehicle) has no place on the Maintenance Planner and is left out.
	"""
	ordered = sorted(crew or [], key=lambda m: 0 if m.get("is_lead") else 1)
	out = []
	for member in ordered:
		row = (resources or {}).get(member.get("resource")) or {}
		user = row.get("user")
		if not user or any(p["user"] == user for p in out):
			continue
		out.append(
			{
				"user": user,
				"name": member.get("label") or row.get("resource_name") or user,
				"hours": member.get("hours"),
			}
		)
	return out


def crew_entries(users, current, user_to_resource):
	"""The Project Planner's crew list for ``users`` (lead first), keeping current rows' hours.

	``current`` is the task's crew now (``[{"resource", "hours", "is_lead", "user"}]``, ``user`` None
	for a resource with no login: a subcontractor, a vehicle); ``user_to_resource`` maps a user to
	their active Planner Resource. A person already on the crew keeps their own resource even when it
	is no longer active, and every crew member with no user is kept, after the people: the
	Maintenance Planner only shows people, so it must not drop what it cannot see. Returns
	``(entries, missing)``: ``missing`` are the users with no resource, which the caller refuses. The
	first user is the lead.
	"""
	by_resource = {m.get("resource"): m for m in current or []}
	mapping = dict(user_to_resource or {})
	mapping.update({m["user"]: m.get("resource") for m in current or [] if m.get("user") and m.get("resource")})
	entries, missing, seen = [], [], set()
	for user in users or []:
		resource = mapping.get(user)
		if not resource:
			missing.append(user)
			continue
		if resource in seen:
			continue
		seen.add(resource)
		kept = by_resource.get(resource) or {}
		entries.append({"resource": resource, "hours": kept.get("hours") or 0, "is_lead": 0 if entries else 1})
	for member in current or []:
		resource = member.get("resource")
		if resource and not member.get("user") and resource not in seen:
			seen.add(resource)
			entries.append({"resource": resource, "hours": member.get("hours") or 0, "is_lead": 0})
	return entries, missing


def template_task_values(spec):
	"""The template Task one :data:`TEMPLATE_TASKS` entry seeds (``custom_outdoor`` when set)."""
	values = {
		"subject": spec["subject"],
		"description": spec.get("description") or "",
		"expected_time": flt(spec.get("hours")),
	}
	if cint(spec.get("crew_size")):
		values["custom_crew_size"] = cint(spec["crew_size"])
	if cint(spec.get("outdoor")):
		values["custom_outdoor"] = 1
	return values


# ---------------------------------------------------------------------- reads


def _has_flag_column():
	try:
		return bool(frappe.db.has_column("Project", FLAG))
	except Exception:
		return False


def template_exists():
	try:
		return bool(frappe.db.exists(TEMPLATE_DOCTYPE, TEMPLATE))
	except Exception:
		return False


def move_projects(names):
	"""The subset of the Project ``names`` that are move jobs. Empty when the field is not there yet."""
	names = sorted({n for n in names or () if n})
	if not names or not _has_flag_column():
		return set()
	return set(
		frappe.get_all("Project", filters={"name": ["in", names], FLAG: 1}, pluck="name", limit_page_length=0)
	)


def from_intake(opportunity):
	"""True when ``opportunity`` was made by the /fountain-move intake (a Fountain Move Request)."""
	if not opportunity:
		return False
	try:
		if not frappe.db.exists("DocType", "Fountain Move Request"):
			return False
		return bool(frappe.db.exists("Fountain Move Request", {"created_opportunity": opportunity}))
	except Exception:
		return False


# ---------------------------------------------------------------------- hooks


def apply_on_insert(doc, method=None):
	"""``Project.before_insert``: tick the box for an intake move, then fill the template and type.

	Before insert, so ERPNext's ``after_insert`` copies the template's tasks in the same insert. The
	project creation from an Opportunity sets ``flags.ignore_validate``; before_insert still runs.
	Never raises: a problem here is logged and the project is created as it was asked for.
	"""
	try:
		if not _has_flag_column():
			return
		if not truthy(doc.get(FLAG)) and from_intake(doc.get("custom_opportunity")):
			doc.set(FLAG, 1)
		values = {
			FLAG: doc.get(FLAG),
			"project_template": doc.get("project_template"),
			"project_type": doc.get("project_type"),
		}
		wanted = insert_defaults(values, template_exists=True)
		if "project_template" in wanted and not template_exists():
			wanted.pop("project_template")
		for field, value in wanted.items():
			doc.set(field, value)
	except Exception:
		_log("A new project's Fountain Move defaults were not applied.")


@frappe.whitelist()
def opportunity_defaults(opportunity):
	"""``{"template", "is_fountain_move"}`` for the Create Project dialog of ``opportunity``."""
	if not opportunity or not frappe.has_permission("Opportunity", "read", doc=opportunity):
		return {"template": None, "is_fountain_move": False}
	move = from_intake(opportunity)
	return {"template": TEMPLATE if move and template_exists() else None, "is_fountain_move": move}


# ---------------------------------------------------------------------- Project Planner


def mark_read_only(cards):
	"""Project Planner cards of move tasks: not movable there, and marked ``fountain_move``."""
	try:
		moves = move_projects(card.get("project") for card in cards or [])
		for card in cards or []:
			if card.get("project") in moves:
				card["movable"] = False
				card["fountain_move"] = True
	except Exception:
		_log("The Project Planner could not tell which tasks belong to Fountain Moves.")


def refuse_on_project_planner(doc):
	"""Refuse a Project Planner edit of a move task (the Maintenance Planner moves them)."""
	if doc.get("project") and doc.get("project") in move_projects([doc.get("project")]):
		frappe.throw(_(PP_REFUSAL).format(doc.get("subject") or doc.name), title=_("Fountain Move"))


# ---------------------------------------------------------------------- Maintenance Planner


def planner_cards(start, end, today, can_move):
	"""The move-task cards for the Maintenance Planner: ``(dated, undated)``.

	Open leaf tasks with hours on move projects that are not closed. A dated task overlapping
	``start``..``end`` shows once, on its first day (or the range's first day when it began earlier);
	an undated one goes to the Unscheduled tray.
	"""
	from erpnext_enhancements.project_enhancements import crew_availability as engine

	if not _has_flag_column():
		return [], []
	start, end = getdate(start), getdate(end)
	cols = engine._task_columns()
	selected = ", ".join(f"{expr} AS `{column}`" for column, expr in cols.items())
	rows = frappe.db.sql(
		f"""
		SELECT
			t.name, t.subject, t.project, t.status, t.is_group, t.exp_start_date, t.exp_end_date,
			t.expected_time, t.modified, {selected},
			p.project_name AS project_title, p.customer
		FROM `tabTask` t
		INNER JOIN `tabProject` p ON p.name = t.project
		WHERE {OPEN_MOVE_TASKS_SQL}
			AND (t.exp_start_date IS NULL OR DATE(t.exp_start_date) <= %(end)s)
			AND (t.exp_end_date IS NULL OR DATE(t.exp_end_date) >= %(start)s)
		ORDER BY t.exp_start_date, p.project_name, t.name
		LIMIT %(limit)s
		""",
		{
			"finished": engine.FINISHED_STATUSES,
			"closed": engine.CLOSED_PROJECT_STATUSES,
			"start": start,
			"end": end,
			"limit": CARD_LIMIT,
		},
		as_dict=True,
	)
	rows = [row for row in rows if cardable(row)]
	if not rows:
		return [], []
	crews = _crews([row.get("name") for row in rows])
	dated, undated = [], []
	for row in rows:
		span = engine.task_span(row)
		crew = crews.get(row.get("name")) or []
		if not span:
			undated.append(move_card(row, crew, today, can_move))
		elif span[0] <= end and span[1] >= start:
			# One card per task: on its first day, or on the range's first day when it began earlier.
			shown = span[0] if span[0] >= start else start
			dated.append(move_card(row, crew, today, can_move, span[0], span[1], shown))
	return dated, undated


def _project_column(column):
	"""SQL for one of Project's columns (alias ``p``), or ``NULL`` when it is not there: a site
	part-way through a migrate still answers."""
	try:
		return f"p.`{column}`" if frappe.db.has_column("Project", column) else "NULL"
	except Exception:
		return "NULL"


def search_cards(like, anchor, limit=60):
	"""The move tasks the Maintenance Planner's search box lists for ``like``, as dicts for the page.

	``like`` is a SQL LIKE pattern with its wildcards already escaped (``planner_actions.like_pattern``).
	It is matched against what a person types to find a move: the task's number and subject, the
	project's number, name and free-text address, its customer (number and name) and the city of
	either Address the project links to (``primary_address``, or the older
	``custom_customer__lead_address``). A column a site has not migrated yet reads as NULL.

	Which tasks are listed is :data:`OPEN_MOVE_TASKS_SQL`, the calendar's own rule, but with no date
	range: a move months away, or one nobody has dated yet, is exactly what someone searches for. Each
	result is ``{"kind": "move", name, label (the project's title, as the card's), subject, project,
	customer, city, date, status}``, ``date`` being the task's first day (the day its card sits on) or
	None for the Unscheduled tray. Read-only; the caller has passed the planner's role gate, as
	``get_planner`` has when it draws these cards. Rows nearest ``anchor`` first, undated ones last.
	"""
	from erpnext_enhancements.project_enhancements import crew_availability as engine

	if not _has_flag_column():
		return []
	cols = engine._task_columns()
	selected = ", ".join(f"{expr} AS `{column}`" for column, expr in cols.items())
	primary, lead, typed = (
		_project_column(column)
		for column in ("primary_address", "custom_customer__lead_address", "custom_project_address")
	)
	rows = frappe.db.sql(
		f"""
		SELECT
			t.name, t.subject, t.project, t.status, t.is_group, t.exp_start_date, t.exp_end_date,
			t.expected_time, {selected},
			p.project_name AS project_title, p.customer,
			COALESCE(a.city, la.city) AS city
		FROM `tabTask` t
		INNER JOIN `tabProject` p ON p.name = t.project
		LEFT JOIN `tabCustomer` c ON c.name = p.customer
		LEFT JOIN `tabAddress` a ON a.name = {primary}
		LEFT JOIN `tabAddress` la ON la.name = {lead}
		WHERE {OPEN_MOVE_TASKS_SQL}
			AND (
				t.name LIKE %(like)s OR t.subject LIKE %(like)s
				OR p.name LIKE %(like)s OR p.project_name LIKE %(like)s
				OR p.customer LIKE %(like)s OR c.customer_name LIKE %(like)s
				OR a.city LIKE %(like)s OR la.city LIKE %(like)s OR {typed} LIKE %(like)s
			)
		ORDER BY (t.exp_start_date IS NULL), ABS(DATEDIFF(t.exp_start_date, %(anchor)s)), t.name
		LIMIT %(scan)s
		""",
		{
			"like": like,
			"anchor": str(anchor),
			"finished": engine.FINISHED_STATUSES,
			"closed": engine.CLOSED_PROJECT_STATUSES,
			"scan": cint(limit) or 60,
		},
		as_dict=True,
	)
	out = []
	for row in rows or []:
		if not cardable(row):
			continue
		span = engine.task_span(row)
		out.append(
			{
				"kind": "move",
				"name": row.get("name"),
				"label": row.get("project_title") or row.get("project") or row.get("name"),
				"subject": row.get("subject") or row.get("name"),
				"project": row.get("project"),
				"customer": row.get("customer"),
				"city": row.get("city"),
				"date": str(span[0]) if span else None,
				"status": row.get("status"),
			}
		)
	return out


def _crews(task_names):
	"""``{task: [{"user", "name", "hours"}]}`` for the move tasks, lead first."""
	from erpnext_enhancements.project_enhancements import crew_availability as engine

	crew_rows, todo_users = engine.read_crews(task_names)
	people = frappe.get_all(
		"Planner Resource",
		fields=["name", "resource_name", "user", "is_active"],
		limit_page_length=0,
	)
	resources = {p.get("name"): p for p in people}
	user_to_resource = {p.get("user"): p.get("name") for p in people if p.get("user") and cint(p.get("is_active"))}
	return {
		name: crew_users(
			engine.resolve_crew(crew_rows.get(name), todo_users.get(name), user_to_resource), resources
		)
		for name in task_names
	}


def move_task(task, modified, date=None, crew=None, reason=None):
	"""Move a move task to ``date`` and/or set its crew (``crew``: users, the lead first).

	Through the Project Planner's own write (``_apply``), so the conflict check, the needs_reason
	answer, the reason comment, the successor report and the task's validate hooks are all the same.
	A task that is not on a move project is refused: this door is for move jobs only.
	"""
	from erpnext_enhancements.api import project_planner

	doc = project_planner._load(task, modified, allow_move_job=True)
	if not doc.get("project") or doc.get("project") not in move_projects([doc.get("project")]):
		frappe.throw(_(NOT_A_MOVE).format(doc.get("subject") or doc.name))
	entries = None
	if crew is not None:
		users = crew if isinstance(crew, list) else json.loads(crew or "[]")
		users = [str(u) for u in users if u]
		active = frappe.get_all(
			"Planner Resource", filters={"is_active": 1}, fields=["name", "user"], limit_page_length=0
		)
		user_to_resource = {r.get("user"): r.get("name") for r in active if r.get("user")}
		current = project_planner._current_crew(doc)
		owners = {
			r.get("name"): r.get("user")
			for r in frappe.get_all(
				"Planner Resource",
				filters={"name": ["in", [m.get("resource") for m in current] or [""]]},
				fields=["name", "user"],
				limit_page_length=0,
			)
		}
		current = [dict(m, user=owners.get(m.get("resource"))) for m in current]
		entries, missing = crew_entries(users, current, user_to_resource)
		if missing:
			names = ", ".join(frappe.utils.get_fullname(u) or u for u in missing)
			frappe.throw(_("{0} is not on the planner's resource list, so they cannot be booked on a task.").format(names))
	return project_planner._apply(doc, start=str(getdate(date)) if date else None, crew=entries, reason=reason)


# ---------------------------------------------------------------------- seeding and conversion


def seed_template():
	"""Create the Fountain Move Project Template and its template Tasks, unless it exists.

	Insert-only by name: once seeded it is Nik's to edit, and a later run never touches it.
	Returns ``"created"`` or ``"exists"``; raises on a failure (the patch catches and logs).
	"""
	if template_exists():
		return "exists"
	docs = []
	for spec in TEMPLATE_TASKS:
		values = template_task_values(spec)
		if "custom_outdoor" in values and not _task_has("custom_outdoor"):
			values.pop("custom_outdoor")
		if "custom_crew_size" in values and not _task_has("custom_crew_size"):
			values.pop("custom_crew_size")
		doc = frappe.get_doc(dict(values, doctype="Task", is_template=1, status="Template"))
		doc.insert(ignore_permissions=True)
		docs.append(doc)
	template = {
		"doctype": TEMPLATE_DOCTYPE,
		"project_type": PROJECT_TYPE if frappe.db.exists("Project Type", PROJECT_TYPE) else None,
		"tasks": [{"task": doc.name, "subject": doc.subject} for doc in docs],
	}
	frappe.get_doc(template).insert(ignore_permissions=True, set_name=TEMPLATE)
	return "created"


def _task_has(column):
	try:
		return bool(frappe.db.has_column("Task", column))
	except Exception:
		return False


def make_move_job(project, cancel_from_template=None):
	"""Turn an existing project into a move job (PRJ-00775, Nik's approval of 2026-10-21).

	* project type Service, the box ticked, the template set to Fountain Move;
	* when ``cancel_from_template`` names a Project Template, each of the project's tasks made from
	  it (``template_task`` in that template) that is still **Open and untouched** (no assignee, no
	  hours booked, no timesheet) is set to Canceled; nothing is deleted;
	* the template's tasks are added **undated** (the Move day goes to the Maintenance Planner's
	  Unscheduled tray), unless the project already has tasks made from this template.

	Safe twice: a second run changes nothing. Returns a report of what it did.
	"""
	report = {"project": project, "fields": {}, "canceled": [], "kept": [], "added": []}
	doc = frappe.get_doc("Project", project)
	wanted = {FLAG: 1, "project_template": TEMPLATE}
	if (doc.get("project_type") or "") != PROJECT_TYPE:
		wanted["project_type"] = PROJECT_TYPE
	changed = {k: v for k, v in wanted.items() if doc.get(k) != v}
	if changed:
		# A field-level write: a full save of an old project re-runs every hook (hand-off gate, budget
		# rollup, contact sync) for a change to three fields none of them read.
		frappe.db.set_value("Project", project, changed)
		report["fields"] = changed

	template = frappe.get_doc(TEMPLATE_DOCTYPE, TEMPLATE)
	template_tasks = [row.task for row in template.tasks if row.task]
	have = set(
		frappe.get_all(
			"Task",
			filters={"project": project, "template_task": ["in", template_tasks or [""]]},
			pluck="template_task",
		)
	)

	if cancel_from_template:
		old = frappe.get_all(
			"Project Template Task", filters={"parent": cancel_from_template}, pluck="task"
		)
		for row in frappe.get_all(
			"Task",
			filters={"project": project, "template_task": ["in", old or [""]]},
			fields=["name", "status", "actual_time", "_assign"],
			order_by="lft desc",
		):
			if row.get("status") in (CANCELED, "Cancelled"):
				continue
			if row.get("status") == "Open" and not flt(row.get("actual_time")) and not _assigned(row) and not _timesheets(row.name):
				frappe.db.set_value("Task", row.name, "status", CANCELED)
				report["canceled"].append(row.name)
			else:
				report["kept"].append(row.name)

	for name in template_tasks:
		if name in have:
			continue
		source = frappe.get_doc("Task", name)
		task = frappe.get_doc(
			{
				"doctype": "Task",
				"subject": source.subject,
				"project": project,
				"status": "Open",
				"description": source.description,
				"is_group": source.is_group,
				"template_task": source.name,
			}
		)
		# Task.before_insert (task_templates.copy_template_planning) copies the hours, crew size and
		# outdoor flag from the template task.
		task.insert(ignore_permissions=True)
		report["added"].append(task.name)
	return report


def _assigned(row):
	try:
		return bool(json.loads(row.get("_assign") or "[]"))
	except Exception:
		return True


def _timesheets(task):
	try:
		return bool(frappe.db.exists("Timesheet Detail", {"task": task}))
	except Exception:
		return True


def _log(message):
	try:
		frappe.log_error(title=LOG_TITLE, message=message + "\n\n" + frappe.get_traceback())
	except Exception:
		pass
