"""Simple mode for both planners (``docs/planner-simple-mode.md``; Nik, 2026-10-22).

Nik asked for "an easy view that is simple and doesn't overwhelm the user, but enables them to quickly
and easily see all the data in a simple view, both historical data and future data". Simple mode is a
switch on each planner: one search box and a card per job (the Project Planner) or per site (the
Maintenance Planner), split into **Done** and **Coming up**, from the job's first day to its last.

* **Which cards** (S10). Every customer job ever, by the planner's own rule for one
  (``crew_availability.planner_job_condition``: internal, overhead and group projects left out), whatever
  its status. On the Maintenance Planner every project that has a maintenance contract or a Maintenance
  Record: a site is the record's project (every record on production has one, 2026-10-22).
* **In what order.** Cards with something coming up first, soonest first; then the rest by their last
  activity, newest first. The order is one SQL read over every candidate and every task, so it judges
  "coming up" from all of a job's open dated work; the card lists only the work the display rule shows.
  :data:`PAGE_LENGTH` cards at a time; ``more`` says whether there are others. A refresh asks for as many
  as the page shows (``limit``, at most :data:`MAX_PAGE_LENGTH`).
* **Search** matches the project's number, its name, its customer and (on jobs) its PM, across all of
  them, server side. ``%`` and ``_`` in the text are literal.
* **What a job's card lists** (S11, S13). The planner's display rule (D6): a task under a Phase 100–700
  group or with someone on it (a crew row, or an open assignee who is a Planner Resource,
  ``crew_availability.resolve_crew``). A job with none of that (every job from before the phase
  templates: 565 finished jobs, one assigned task among them) lists its own **top-level groups** instead,
  each "N of M done", and any top-level task outside a group.
* **What a site's card lists** (S12, S14). Its Maintenance Records (dated as the Maintenance Planner dates
  them; done once submitted or in review), its contract's projected visits for the next
  :data:`PROJECTION_DAYS` days (``maintenance_planner._projections``: drafts exist only a week ahead, so
  without them every site would look finished), and the completed tasks on its project, which are its
  history from before the Maintenance Records (Highlands: 73 since 2019).
* **Done and late.** A task is done when Completed (or Invoiced), dated by ``completed_on``, else its end.
  An open task whose last day has passed is *late*; open work with no date is *not scheduled*. On a
  finished or canceled job every item is history: nothing on it is late, and what was never done says so.
* **Who** is the crew (Task Crew Member) and the assignees (ToDo, open or closed: a closed ToDo is who did
  it); a visit's technician. Full names; the page draws initials.

Reads only, after each planner's own role gate. Raw SQL with bound parameters, as every planner read.
The card builders (:func:`build_job_card`, :func:`build_site_card`) take plain values, so
``tests/test_planner_simple.py`` runs them without a bench.
"""

import frappe
from frappe.utils import add_days, cint, getdate, nowdate

from erpnext_enhancements.api import maintenance_planner as mp
from erpnext_enhancements.api import project_planner as pp
from erpnext_enhancements.api.planner_lookahead import nearest_phase
from erpnext_enhancements.api.planner_overview import _chunks
from erpnext_enhancements.project_enhancements import crew_availability as engine

#: Cards one answer carries, unless the page asks for more (a refresh of every card it shows), up to
#: :data:`MAX_PAGE_LENGTH`: more than every customer job on production (640, 2026-10-22), so a refresh after
#: many *Show more* presses never drops cards the person has scrolled to.
PAGE_LENGTH = 20
MAX_PAGE_LENGTH = 1000
#: The longest search text read; anything past it is cut.
SEARCH_MAX = 140
#: Task statuses never read: a canceled task is gone from the plan, a template is not work.
LEFT_OUT = ("Canceled", "Cancelled", "Template")
#: Task statuses that mean the work was done.
DONE_STATUSES = ("Completed", "Invoiced")
#: How far ahead a site's projected visits are read.
PROJECTION_DAYS = 120
#: The most names a card says for one item.
WHO_MAX = 4


# ---------------------------------------------------------------------- pure helpers


def page_start(value):
	"""The offset a request asks for: a whole number, never below 0."""
	try:
		return max(0, int(value or 0))
	except (TypeError, ValueError):
		return 0


def page_length(value):
	"""The cards a request asks for: :data:`PAGE_LENGTH` unless it names 1 to :data:`MAX_PAGE_LENGTH`."""
	try:
		length = int(value)
	except (TypeError, ValueError):
		return PAGE_LENGTH
	return min(max(length, 1), MAX_PAGE_LENGTH)


def search_text(value):
	"""The search as the queries read it: trimmed, at most :data:`SEARCH_MAX` characters, "" for none."""
	if value is None:
		return ""
	return " ".join(str(value).split())[:SEARCH_MAX]


def like_pattern(text):
	"""``%text%`` with ``%``, ``_`` and the escape character itself literal (MariaDB's default escape)."""
	escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
	return f"%{escaped}%"


def _day(value):
	day = engine._as_date(value)
	return str(day) if day else None


def _who(names):
	out = []
	for name in names or ():
		if name and name not in out:
			out.append(name)
	return out[:WHO_MAX]


def task_item(task, today, finished_job, who=(), phase=None):
	"""One task as a card item: ``{kind, doctype, name, label, phase, date, end, state, who}``.

	``state`` is ``done``, ``late``, ``open``, ``unscheduled`` (open, no date) or, on a finished job,
	``closed`` (open work nobody will do now). ``date`` is when it was done, or when it starts."""
	start, end = task.get("start"), task.get("end")
	if task.get("status") in DONE_STATUSES:
		state, date = "done", _day(task.get("completed_on")) or _day(end) or _day(start)
	elif finished_job:
		state, date = "closed", _day(end) or _day(start)
	elif not start:
		state, date = "unscheduled", None
	else:
		state, date = ("late" if end and end < today else "open"), _day(start)
	return {
		"kind": "task",
		"doctype": "Task",
		"name": task.get("name"),
		"label": task.get("subject") or task.get("name"),
		"phase": phase,
		"date": date,
		"end": _day(end) if state in ("late", "open") and end and end != start else None,
		"state": state,
		"who": _who(who),
	}


def group_item(group, leaves, today, finished_job, who=()):
	"""An old job's top-level group as one item (S13): "N of M done", its days from the work under it."""
	total = len(leaves)
	done = sum(1 for t in leaves if t.get("status") in DONE_STATUSES)
	all_done = done == total if total else group.get("status") in DONE_STATUSES
	if all_done:
		days = [_day(t.get("completed_on")) or _day(t.get("end")) or _day(t.get("start")) for t in leaves]
		days = [d for d in days if d] or [_day(group.get("completed_on")) or _day(group.get("end"))]
		state, date, end = "done", max([d for d in days if d], default=None), None
	else:
		open_work = [t for t in leaves if t.get("status") not in DONE_STATUSES] or [group]
		starts = [t.get("start") for t in open_work if t.get("start")] or (
			[group.get("start")] if group.get("start") else []
		)
		ends = [t.get("end") for t in open_work if t.get("end")] or (
			[group.get("end")] if group.get("end") else []
		)
		first, last = (min(starts) if starts else None), (max(ends) if ends else None)
		if finished_job:
			state = "closed"
		elif not first:
			state = "unscheduled"
		else:
			state = "late" if last and last < today else "open"
		date = _day(first) or (_day(last) if finished_job else None)
		end = _day(last) if state in ("late", "open") and last and last != first else None
	return {
		"kind": "group",
		"doctype": "Task",
		"name": group.get("name"),
		"label": group.get("subject") or group.get("name"),
		"phase": None,
		"date": date,
		"end": end,
		"state": state,
		"who": _who(who),
		"of": [done, total],
	}


def split_items(items):
	"""``(done, coming, unscheduled)``: done and closed newest first; late, then the rest, soonest first."""
	past = [i for i in items if i["state"] in ("done", "closed")]
	coming = [i for i in items if i["state"] in ("late", "open")]
	waiting = [i for i in items if i["state"] == "unscheduled"]
	past.sort(key=lambda i: i["date"] or "", reverse=True)
	past.sort(key=lambda i: i["date"] is None)
	coming.sort(key=lambda i: (i["state"] != "late", i["date"] or "", str(i["label"]).casefold()))
	waiting.sort(key=lambda i: str(i["label"]).casefold())
	return past, coming, waiting


def _span(items, fallback=(None, None)):
	days = [d for i in items for d in (i.get("date"), i.get("end")) if d]
	if days:
		return min(days), max(days)
	return _day(fallback[0]), _day(fallback[1])


def normalize_task(row):
	"""A Task row as the card reads it: dates as dates. Pure."""
	span = engine.task_span(row)
	return {
		"name": row.get("name"),
		"subject": row.get("subject") or row.get("name"),
		"status": row.get("status") or "",
		"parent": row.get("parent_task") or None,
		"group": bool(cint(row.get("is_group"))),
		"start": span[0] if span else None,
		"end": span[1] if span else None,
		"completed_on": engine._as_date(row.get("completed_on")),
	}


def _descendants(tasks, children):
	"""``{group: [leaf tasks under it]}`` for every group in ``tasks``. Climbs no further than the tree."""
	by_name = {t["name"]: t for t in tasks}
	out = {}
	for task in tasks:
		if not task["group"]:
			continue
		leaves, stack, seen = [], list(children.get(task["name"], ())), set()
		while stack:
			name = stack.pop()
			if name in seen or name not in by_name:
				continue
			seen.add(name)
			kid = by_name[name]
			if kid["group"]:
				stack.extend(children.get(name, ()))
			else:
				leaves.append(kid)
		out[task["name"]] = leaves
	return out


def build_job_card(project, rows, today, who=None, crewed=frozenset()):
	"""A job's card from its Task rows (pure).

	``project``: ``{name, title, status, customer, pm, expected_start, expected_end}``. ``who`` maps a
	task to the names on it; ``crewed`` is the set of tasks somebody is on (the D6 rule's second half).
	``mode`` is ``work`` (the display rule's tasks) or ``groups`` (S13: an old job's own top-level groups)."""
	who = who or {}
	finished = (project.get("status") or "") in engine.CLOSED_PROJECT_STATUSES
	tasks = [normalize_task(row) for row in rows]
	parents = {t["name"]: t["parent"] for t in tasks}
	subjects = {t["name"]: t["subject"] for t in tasks}
	shown = []
	for task in tasks:
		if task["group"]:
			continue
		phase = nearest_phase(task["name"], parents, subjects)
		if phase is not None or task["name"] in crewed:
			shown.append((task, subjects.get(phase) if phase else None))
	if shown:
		mode = "work"
		items = [task_item(t, today, finished, who.get(t["name"]), phase) for t, phase in shown]
	else:
		mode = "groups"
		children = {}
		for task in tasks:
			if task["parent"]:
				children.setdefault(task["parent"], []).append(task["name"])
		under = _descendants(tasks, children)
		items = []
		for task in tasks:
			if task["parent"] and task["parent"] in subjects:
				continue
			if task["group"]:
				leaves = under.get(task["name"], [])
				names = [n for leaf in leaves for n in who.get(leaf["name"], ())]
				items.append(group_item(task, leaves, today, finished, names))
			else:
				items.append(task_item(task, today, finished, who.get(task["name"])))
	done, coming, waiting = split_items(items)
	first, last = _span(items, (project.get("expected_start"), project.get("expected_end")))
	return {
		"name": project.get("name"),
		"title": project.get("title") or project.get("name"),
		"customer": project.get("customer") or "",
		"pm": project.get("pm") or "",
		"status": project.get("status") or "",
		"finished": finished,
		"mode": mode,
		"first": first,
		"last": last,
		"done": done,
		"coming": coming,
		"unscheduled": waiting,
		"counts": {
			"done": len(done),
			"coming": len(coming) + len(waiting),
			"late": sum(1 for i in coming if i["state"] == "late"),
		},
	}


def visit_item(visit, today, who=()):
	"""A Maintenance Record as a card item. Done once submitted or in review, as the Maintenance Planner
	says; a draft whose day has passed and that nobody started is late."""
	day = engine._as_date(visit.get("plan_date"))
	in_review = visit.get("workflow_state") == mp.PENDING_STATE
	if cint(visit.get("docstatus")) == 1 or in_review:
		state = "done"
	elif not day:
		state = "unscheduled"
	else:
		state = "late" if day < today and not visit.get("visit_date") else "open"
	return {
		"kind": "visit",
		"doctype": "Sapphire Maintenance Record",
		"name": visit.get("name"),
		"label": visit.get("visit_label") or "Maintenance visit",
		"phase": None,
		"date": _day(day),
		"end": None,
		"state": state,
		"review": bool(in_review and cint(visit.get("docstatus")) != 1),
		"who": _who(who),
	}


def projected_item(entry, who=()):
	"""A projected visit (not drafted yet) as a card item: always ahead, never late, opens its contract. A
	Per Feature contract projects one series per feature, so the item names its feature."""
	feature = entry.get("feature") or ""
	return {
		"kind": "projected",
		"doctype": "Sapphire Maintenance Contract",
		"name": entry.get("contract") or "",
		"label": entry.get("label") or (f"Visit: {feature}" if feature else "Maintenance visit"),
		"phase": None,
		"date": _day(entry.get("date")),
		"end": None,
		"state": "open",
		"who": _who(who),
	}


def build_site_card(project, visits, projected, history, today, who=None):
	"""A site's card (pure): its visits, its projected visits and its project's completed tasks.

	``visits`` are Maintenance Record rows with ``plan_date``; ``projected`` are
	``maintenance_planner._projections`` entries (already the site's); ``history`` are its completed
	Task rows; ``who`` maps a record, a projected entry's key or a task to the names on it."""
	who = who or {}
	finished = (project.get("status") or "") in engine.CLOSED_PROJECT_STATUSES
	items = [visit_item(v, today, who.get(v.get("name"))) for v in visits]
	items += [projected_item(e, who.get(e.get("key"))) for e in projected if _day(e.get("date"))]
	for row in history:
		task = normalize_task(row)
		if task["group"] or task["status"] not in DONE_STATUSES:
			continue
		items.append(task_item(task, today, finished, who.get(task["name"])))
	done, coming, waiting = split_items(items)
	first, last = _span(items, (project.get("expected_start"), project.get("expected_end")))
	return {
		"name": project.get("name"),
		"title": project.get("title") or project.get("name"),
		"customer": project.get("customer") or "",
		"pm": "",
		"status": project.get("status") or "",
		"finished": finished,
		"mode": "site",
		"first": first,
		"last": last,
		"done": done,
		"coming": coming,
		"unscheduled": waiting,
		"counts": {
			"done": len(done),
			"coming": len(coming) + len(waiting),
			"late": sum(1 for i in coming if i["state"] == "late"),
		},
	}


# ---------------------------------------------------------------------- reads


def _owner_sql():
	"""``(expression, join)`` for a project's PM as a name, when the column exists."""
	try:
		has = frappe.db.has_column("Project", "custom_project_owner")
	except Exception:
		has = False
	if not has:
		return "NULL", ""
	return "e.employee_name", "LEFT JOIN `tabEmployee` e ON e.name = p.custom_project_owner"


def _search_sql(columns, text):
	"""``(condition, values)``: every column LIKE the search, or no condition for none."""
	if not text:
		return "", {}
	return " AND (" + " OR ".join(f"IFNULL({c}, '') LIKE %(q)s" for c in columns) + ")", {
		"q": like_pattern(text)
	}


def _page_of_jobs(text, start, today, limit=PAGE_LENGTH):
	"""``(projects, total)``: one page of customer jobs in Simple mode's order, and how many match."""
	owner, join = _owner_sql()
	search, values = _search_sql(("p.name", "p.project_name", "p.customer", owner), text)
	values.update(
		{
			"today": today,
			"closed": engine.CLOSED_PROJECT_STATUSES,
			"left_out": LEFT_OUT,
			"done": DONE_STATUSES,
			"limit": limit,
			"start": start,
			**engine.PLANNER_SQL_VALUES,
		}
	)
	where = f"WHERE {engine.planner_job_condition('p')}{search}"
	total = frappe.db.sql(f"SELECT COUNT(*) FROM `tabProject` p {join} {where}", values)[0][0]
	rows = frappe.db.sql(
		f"""
		SELECT
			p.name, p.project_name AS title, p.status, p.customer, {owner} AS pm,
			p.expected_start_date AS expected_start, p.expected_end_date AS expected_end,
			agg.next_day, agg.last_day
		FROM `tabProject` p
		{join}
		LEFT JOIN (
			SELECT
				t.project,
				MIN(CASE
					WHEN IFNULL(t.status, '') NOT IN %(done)s
						AND COALESCE(t.exp_end_date, t.exp_start_date) >= %(today)s
					THEN COALESCE(t.exp_start_date, t.exp_end_date)
				END) AS next_day,
				MAX(COALESCE(t.completed_on, t.exp_end_date, t.exp_start_date)) AS last_day
			FROM `tabTask` t
			WHERE IFNULL(t.project, '') <> ''
				AND IFNULL(t.is_template, 0) = 0
				AND IFNULL(t.status, '') NOT IN %(left_out)s
			GROUP BY t.project
		) agg ON agg.project = p.name
		{where}
		ORDER BY
			CASE WHEN IFNULL(p.status, '') NOT IN %(closed)s AND agg.next_day IS NOT NULL THEN 0 ELSE 1 END,
			CASE WHEN IFNULL(p.status, '') NOT IN %(closed)s THEN agg.next_day END,
			COALESCE(agg.last_day, p.expected_end_date, p.expected_start_date, DATE(p.creation)) DESC,
			p.name DESC
		LIMIT %(limit)s OFFSET %(start)s
		""",
		values,
		as_dict=True,
	)
	return rows, cint(total)


def _read_tasks(projects):
	"""Every Task row of ``projects`` (groups included; canceled and template work left out)."""
	if not projects:
		return []
	slot = (
		", t.custom_start_datetime AS custom_start_datetime, t.custom_end_datetime AS custom_end_datetime"
		if pp._task_has_column("custom_start_datetime")
		else ""
	)
	return frappe.db.sql(
		f"""
		SELECT
			t.name, t.subject, t.project, t.status, t.parent_task, IFNULL(t.is_group, 0) AS is_group,
			t.exp_start_date, t.exp_end_date, t.completed_on{slot}
		FROM `tabTask` t
		WHERE t.project IN %(projects)s
			AND IFNULL(t.is_template, 0) = 0
			AND IFNULL(t.status, '') NOT IN %(left_out)s
		ORDER BY t.project, t.lft, t.creation
		""",
		{"projects": tuple(sorted(projects)), "left_out": LEFT_OUT},
		as_dict=True,
	)


def _read_who(task_names):
	"""``(who, crewed)``: ``{task: [full names]}`` (crew first, then assignees, open or closed) and the set
	of tasks somebody is on by the planner's rule (a crew row, or an open assignee who is a Planner
	Resource: ``crew_availability.resolve_crew``)."""
	who, crewed = {}, set()
	names = [n for n in task_names if n]
	if not names:
		return who, crewed
	people = frappe.get_all(
		"Planner Resource", filters={"is_active": 1}, fields=["name", "user"], limit_page_length=0
	)
	user_to_resource = {p.get("user"): p.get("name") for p in people if p.get("user")}
	crews, todos = {}, {}
	for chunk in _chunks(names):
		for row in frappe.get_all(
			"Task Crew Member",
			filters={"parenttype": "Task", "parent": ["in", chunk]},
			fields=["parent", "resource", "resource_name"],
			order_by="idx asc",
			limit_page_length=0,
		):
			crews.setdefault(row.get("parent"), []).append(row)
		for row in frappe.get_all(
			"ToDo",
			filters={
				"reference_type": "Task",
				"reference_name": ["in", chunk],
				"status": ["in", ["Open", "Closed"]],
			},
			fields=["reference_name", "allocated_to", "status"],
			limit_page_length=0,
		):
			todos.setdefault(row.get("reference_name"), []).append(row)
	users = {row.get("allocated_to") for rows in todos.values() for row in rows}
	full = mp._full_names(users)
	for name in names:
		crew_rows = crews.get(name) or []
		todo_rows = todos.get(name) or []
		open_users = [r.get("allocated_to") for r in todo_rows if r.get("status") == "Open"]
		if engine.resolve_crew(crew_rows, open_users, user_to_resource):
			crewed.add(name)
		people_on = [r.get("resource_name") or r.get("resource") for r in crew_rows]
		people_on += [full.get(r.get("allocated_to")) or r.get("allocated_to") for r in todo_rows]
		if any(people_on):
			who[name] = _who(people_on)
	return who, crewed


def _page_of_sites(text, start, today, limit=PAGE_LENGTH):
	"""``(projects, total)``: one page of maintenance sites in Simple mode's order, and how many match."""
	search, values = _search_sql(("p.name", "p.project_name", "p.customer"), text)
	values.update(
		{
			"today": today,
			"closed": engine.CLOSED_PROJECT_STATUSES,
			"done": DONE_STATUSES,
			"pending": mp.PENDING_STATE,
			"limit": limit,
			"start": start,
		}
	)
	sites = """
		p.name IN (
			SELECT c.project FROM `tabSapphire Maintenance Contract` c WHERE IFNULL(c.project, '') <> ''
			UNION
			SELECT r.project FROM `tabSapphire Maintenance Record` r
			WHERE IFNULL(r.project, '') <> '' AND r.docstatus < 2
		)
	"""
	where = f"WHERE {sites}{search}"
	total = frappe.db.sql(f"SELECT COUNT(*) FROM `tabProject` p {where}", values)[0][0]
	rows = frappe.db.sql(
		f"""
		SELECT
			p.name, p.project_name AS title, p.status, p.customer,
			p.expected_start_date AS expected_start, p.expected_end_date AS expected_end
		FROM `tabProject` p
		LEFT JOIN (
			SELECT
				r.project,
				MIN(CASE
					WHEN r.docstatus = 0 AND IFNULL(r.workflow_state, '') <> %(pending)s
						AND COALESCE(r.scheduled_visit_date, r.visit_date) >= %(today)s
					THEN COALESCE(r.scheduled_visit_date, r.visit_date)
				END) AS next_day,
				MAX(CASE
					WHEN r.docstatus = 1 OR r.workflow_state = %(pending)s
					THEN COALESCE(r.visit_date, r.scheduled_visit_date, DATE(r.modified))
				END) AS last_day
			FROM `tabSapphire Maintenance Record` r
			WHERE r.docstatus < 2
			GROUP BY r.project
		) v ON v.project = p.name
		LEFT JOIN (
			SELECT c.project, MIN(cf.next_visit_date) AS next_day
			FROM `tabSapphire Maintenance Contract` c
			JOIN `tabSapphire Contract Feature` cf ON cf.parent = c.name AND cf.parenttype = 'Sapphire Maintenance Contract'
			WHERE c.status = 'Active' AND cf.next_visit_date >= %(today)s
			GROUP BY c.project
		) f ON f.project = p.name
		LEFT JOIN (
			SELECT t.project, MAX(COALESCE(t.completed_on, t.exp_end_date, t.exp_start_date)) AS last_day
			FROM `tabTask` t
			WHERE IFNULL(t.project, '') <> '' AND IFNULL(t.status, '') IN %(done)s
			GROUP BY t.project
		) h ON h.project = p.name
		{where}
		ORDER BY
			CASE WHEN IFNULL(p.status, '') NOT IN %(closed)s
				AND COALESCE(v.next_day, f.next_day) IS NOT NULL THEN 0 ELSE 1 END,
			CASE WHEN IFNULL(p.status, '') NOT IN %(closed)s THEN
				LEAST(COALESCE(v.next_day, f.next_day), COALESCE(f.next_day, v.next_day)) END,
			COALESCE(
				GREATEST(COALESCE(v.last_day, h.last_day), COALESCE(h.last_day, v.last_day)),
				p.expected_end_date,
				DATE(p.creation)
			) DESC,
			p.name DESC
		LIMIT %(limit)s OFFSET %(start)s
		""",
		values,
		as_dict=True,
	)
	return rows, cint(total)


def _read_visits(projects):
	"""Every Maintenance Record of ``projects``, dated as the Maintenance Planner dates it (``plan_date``)."""
	if not projects:
		return []
	return frappe.db.sql(
		"""
		SELECT
			name, project, visit_label, technician, scheduled_visit_date, visit_date, docstatus, workflow_state,
			CASE
				WHEN docstatus = 1 OR workflow_state = %(pending)s
					THEN COALESCE(visit_date, scheduled_visit_date, DATE(modified))
				ELSE COALESCE(scheduled_visit_date, visit_date)
			END AS plan_date
		FROM `tabSapphire Maintenance Record`
		WHERE docstatus < 2 AND project IN %(projects)s
		ORDER BY plan_date, name
		""",
		{"projects": tuple(sorted(projects)), "pending": mp.PENDING_STATE},
		as_dict=True,
	)


def _read_history(projects):
	"""The completed Task rows of ``projects``: a site's history from before the Maintenance Records."""
	if not projects:
		return []
	return frappe.db.sql(
		"""
		SELECT
			t.name, t.subject, t.project, t.status, t.parent_task, IFNULL(t.is_group, 0) AS is_group,
			t.exp_start_date, t.exp_end_date, t.completed_on
		FROM `tabTask` t
		WHERE t.project IN %(projects)s
			AND IFNULL(t.is_template, 0) = 0
			AND IFNULL(t.is_group, 0) = 0
			AND IFNULL(t.status, '') IN %(done)s
		ORDER BY t.project, t.completed_on, t.creation
		""",
		{"projects": tuple(sorted(projects)), "done": DONE_STATUSES},
		as_dict=True,
	)


def _projected(projects, today):
	"""``{project: [projected entries]}`` for the next :data:`PROJECTION_DAYS` days. A failure is logged and
	leaves the sites with their records and history: the projection is the one costly read here."""
	if not projects:
		return {}
	out = {}
	try:
		entries = mp._projections(
			today, getdate(add_days(today, PROJECTION_DAYS)), today, projects=sorted(projects)
		)
		entries = [entry for entry in entries if entry.get("project") in projects]
		mp._decorate(entries)
	except Exception:
		frappe.log_error(
			title="Simple mode: projected visits could not be read", message=frappe.get_traceback()
		)
		return {}
	for entry in entries:
		if entry.get("project") in projects:
			out.setdefault(entry["project"], []).append(entry)
	return out


def _answer(cards, total, start, text, today, limit=PAGE_LENGTH):
	return {
		"today": str(today),
		"search": text,
		"start": start,
		"page_length": limit,
		"total": total,
		"more": start + len(cards) < total,
		"cards": cards,
	}


# ---------------------------------------------------------------------- endpoints


@frappe.whitelist()
def get_simple_jobs(search=None, start=0, limit=None):
	"""The Project Planner's Simple mode: ``{today, search, start, page_length, total, more, cards}``, one
	card per customer job (:func:`build_job_card`). ``start`` is the offset of the first card, ``limit`` how
	many (:data:`PAGE_LENGTH` unless 1 to :data:`MAX_PAGE_LENGTH`)."""
	pp._require_planner()
	today = getdate(nowdate())
	text, start, limit = search_text(search), page_start(start), page_length(limit)
	projects, total = _page_of_jobs(text, start, today, limit)
	names = [p.get("name") for p in projects]
	rows = _read_tasks(names)
	who, crewed = _read_who([r.get("name") for r in rows if not cint(r.get("is_group"))])
	by_project = {}
	for row in rows:
		by_project.setdefault(row.get("project"), []).append(row)
	cards = [build_job_card(p, by_project.get(p.get("name"), []), today, who, crewed) for p in projects]
	return _answer(cards, total, start, text, today, limit)


@frappe.whitelist()
def get_simple_sites(search=None, start=0, limit=None):
	"""The Maintenance Planner's Simple mode: the same answer, one card per site (:func:`build_site_card`)."""
	mp._require_planner()
	today = getdate(nowdate())
	text, start, limit = search_text(search), page_start(start), page_length(limit)
	projects, total = _page_of_sites(text, start, today, limit)
	names = [p.get("name") for p in projects]
	visits = _read_visits(names)
	history = _read_history(names)
	projected = _projected(set(names), today)
	technicians = mp._full_names(
		{v.get("technician") for v in visits}
		| {e.get("technician") for entries in projected.values() for e in entries}
	)
	who, _crewed = _read_who([r.get("name") for r in history])
	for visit in visits:
		if visit.get("technician"):
			who[visit["name"]] = [technicians.get(visit["technician"]) or visit["technician"]]
	for entries in projected.values():
		for entry in entries:
			if entry.get("technician") and entry.get("key"):
				who[entry["key"]] = [technicians.get(entry["technician"]) or entry["technician"]]
	by_visits, by_history = {}, {}
	for visit in visits:
		by_visits.setdefault(visit.get("project"), []).append(visit)
	for row in history:
		by_history.setdefault(row.get("project"), []).append(row)
	cards = [
		build_site_card(
			p,
			by_visits.get(p.get("name"), []),
			projected.get(p.get("name"), []),
			by_history.get(p.get("name"), []),
			today,
			who,
		)
		for p in projects
	]
	return _answer(cards, total, start, text, today, limit)
