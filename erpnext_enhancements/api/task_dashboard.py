"""Backend for the "Task Dashboard" Custom HTML Block (the morning TV screen).

One whitelisted call, :func:`get_task_dashboard_data`, returns everything the
block renders — the Jun 9 meeting's morning-screen wishlist:

* **Top 10 priority projects, all at once** ("I'd like to see all of them at
  once… just have a list of the top 10") — Active projects ranked by
  ``custom_company_priority`` (1-30, same eligibility rule as the home
  widget), each with its PM and tech lead so "who would I talk to about
  this?" is answered from across the room.
* **Overdue / at-risk tasks** — open tasks past their expected end date,
  oldest first, with days-overdue and assignee names.
* **Today's tasks with technicians** — open tasks whose expected window spans
  today (a 3-day task shows all 3 days), with the assigned people resolved
  to full names ("it won't say Joe and Bob are going to this site today —
  we can").
* **Today's calendar events** — public, open Events overlapping today.

Access mirrors the Sales Pipeline page: this is a shared wall/workspace
display, so the gate is role-level (any staff role) and the data is then
fetched permission-free (``frappe.get_all``) — per-user User Permissions
must not silently empty a shared board (see the Project-visibility cascade:
Employee user-perms would otherwise hide most Projects from non-admins).

Open-task filter note: this site's Task statuses are customized
(``Open, Working, Invoiced, Completed, Canceled, Pending Review, Overdue,
Template``) — "Canceled" has one L here, and Invoiced/Template are not
actionable, so all are excluded explicitly (both cancel spellings, for
safety against future option edits).
"""

import json

import frappe
from frappe.query_builder.functions import Count
from frappe.utils import cint, date_diff, nowdate

CLOSED_TASK_STATUSES = ("Completed", "Canceled", "Cancelled", "Template", "Invoiced")

TOP_PROJECT_LIMIT = 10
OVERDUE_LIMIT = 20
TODAY_LIMIT = 30
EVENT_LIMIT = 10

# Same eligibility rule the priority widgets use: a usable company rank is 1-30.
MAX_COMPANY_PRIORITY = 30

STAFF_ROLES = {
	"System Manager",
	"Sales Master Manager",
	"Sales Manager",
	"Sales User",
	"Projects Manager",
	"Projects User",
	"Employee",
	# Low-privilege role for the dedicated wall-display users (one per TV/Pi)
	# that sign in to /wall. Seeded by patches/seed_wall_display_role.
	"Wall Display",
}


def _check_access():
	if not STAFF_ROLES.intersection(set(frappe.get_roles())):
		frappe.throw(
			frappe._("You do not have permission to view the Task Dashboard."), frappe.PermissionError
		)


def _assignee_names(tasks):
	"""Resolve every task's ``_assign`` JSON into full names, in two bulk queries."""
	emails = set()
	parsed = {}
	for task in tasks:
		try:
			users = json.loads(task.get("_assign") or "[]")
		except Exception:
			users = []
		parsed[task.name] = users
		emails.update(users)

	full_names = {}
	if emails:
		full_names = dict(
			frappe.get_all(
				"User",
				filters={"name": ("in", list(emails))},
				fields=["name", "full_name"],
				as_list=True,
			)
		)
	return {
		task.name: [full_names.get(email) or email for email in parsed[task.name]] for task in tasks
	}


def _project_labels(project_ids):
	if not project_ids:
		return {}
	return {
		p.name: p
		for p in frappe.get_all(
			"Project",
			filters={"name": ("in", list(project_ids))},
			fields=["name", "project_name", "custom_company_priority"],
		)
	}


def _task_card(task, names, projects):
	project = projects.get(task.project)
	rank = cint(project.custom_company_priority) if project else 0
	return {
		"name": task.name,
		"subject": task.subject,
		"priority": task.priority or "Low",
		"project": task.project or "",
		"project_label": (project.project_name or project.name) if project else "",
		"project_rank": rank if 0 < rank <= MAX_COMPANY_PRIORITY else 0,
		"assignees": names.get(task.name, []),
		"exp_start_date": str(task.exp_start_date) if task.exp_start_date else "",
		"exp_end_date": str(task.exp_end_date) if task.exp_end_date else "",
	}


@frappe.whitelist()
def get_task_dashboard_data():
	"""Everything the Task Dashboard block renders, in one call."""
	_check_access()
	today = nowdate()

	# --- Top 10 priority projects, with PM + tech lead resolved ---------------
	# custom_company_priority is a Select field, so it is stored as text: a SQL
	# "ORDER BY custom_company_priority asc" sorts it lexically ("1","10","11",
	# …,"2"), which floats every 1x rank above rank 2 and — because the LIMIT
	# runs after that sort — surfaces the wrong ten projects, not merely the
	# right ten mis-ordered. Sort numerically (matching priority_overview.js's
	# get_priority_weight) before slicing. Fetch ordered by modified desc so the
	# stable sort below keeps that as the within-rank tiebreaker.
	eligible_projects = frappe.get_all(
		"Project",
		filters={
			"status": "Active",
			"custom_company_priority": ("between", [1, MAX_COMPANY_PRIORITY]),
		},
		fields=[
			"name",
			"project_name",
			"status",
			"custom_company_priority",
			"custom_project_owner",
			"custom_technical_lead",
			"percent_complete",
		],
		order_by="modified desc",
	)
	eligible_projects.sort(key=lambda p: cint(p["custom_company_priority"]))
	top_projects = eligible_projects[:TOP_PROJECT_LIMIT]

	employee_ids = {
		p[field] for p in top_projects for field in ("custom_project_owner", "custom_technical_lead") if p[field]
	}
	employee_names = {}
	if employee_ids:
		employee_names = dict(
			frappe.get_all(
				"Employee",
				filters={"name": ("in", list(employee_ids))},
				fields=["name", "employee_name"],
				as_list=True,
			)
		)
	for p in top_projects:
		p["rank"] = cint(p.pop("custom_company_priority"))
		p["pm"] = employee_names.get(p.pop("custom_project_owner")) or ""
		p["tech_lead"] = employee_names.get(p.pop("custom_technical_lead")) or ""
		p["percent_complete"] = cint(p.get("percent_complete"))

	# --- Overdue / at-risk tasks ----------------------------------------------
	task_fields = ["name", "subject", "priority", "status", "project", "_assign", "exp_start_date", "exp_end_date"]
	overdue = frappe.get_all(
		"Task",
		filters={"status": ("not in", CLOSED_TASK_STATUSES), "exp_end_date": ("<", today)},
		fields=task_fields,
		order_by="exp_end_date asc",
		limit_page_length=OVERDUE_LIMIT + 1,
	)
	overdue_overflow = len(overdue) > OVERDUE_LIMIT
	overdue = overdue[:OVERDUE_LIMIT]

	# --- Today's tasks: any open task whose expected window spans today -------
	spanning = frappe.get_all(
		"Task",
		filters={
			"status": ("not in", CLOSED_TASK_STATUSES),
			"exp_start_date": ("<=", today),
			"exp_end_date": (">=", today),
		},
		fields=task_fields,
		order_by="priority desc, exp_end_date asc",
		limit_page_length=TODAY_LIMIT,
	)
	# open-ended edges: only one of the two dates set, landing on today
	edges = frappe.get_all(
		"Task",
		filters={"status": ("not in", CLOSED_TASK_STATUSES), "exp_start_date": today, "exp_end_date": ("is", "not set")},
		fields=task_fields,
	) + frappe.get_all(
		"Task",
		filters={"status": ("not in", CLOSED_TASK_STATUSES), "exp_end_date": today, "exp_start_date": ("is", "not set")},
		fields=task_fields,
	)
	seen = {t.name for t in spanning}
	today_tasks = spanning + [t for t in edges if t.name not in seen]
	today_tasks = today_tasks[:TODAY_LIMIT]

	all_tasks = overdue + today_tasks
	names = _assignee_names(all_tasks)
	projects = _project_labels({t.project for t in all_tasks if t.project})

	overdue_cards = []
	for task in overdue:
		card = _task_card(task, names, projects)
		card["days_overdue"] = max(date_diff(today, task.exp_end_date), 0)
		overdue_cards.append(card)

	today_cards = [_task_card(task, names, projects) for task in today_tasks]

	# --- Today's calendar events (public, open, overlapping today) ------------
	events = frappe.db.sql(
		"""
		SELECT subject, starts_on, ends_on, all_day
		FROM `tabEvent`
		WHERE status = 'Open'
		  AND event_type = 'Public'
		  AND starts_on <= %(day_end)s
		  AND COALESCE(ends_on, starts_on) >= %(day_start)s
		ORDER BY all_day DESC, starts_on ASC
		LIMIT %(limit)s
		""",
		{"day_start": f"{today} 00:00:00", "day_end": f"{today} 23:59:59", "limit": EVENT_LIMIT},
		as_dict=True,
	)
	for event in events:
		event["starts_on"] = str(event["starts_on"]) if event["starts_on"] else ""
		event["ends_on"] = str(event["ends_on"]) if event["ends_on"] else ""
		event["all_day"] = cint(event["all_day"])

	return {
		"top_projects": top_projects,
		"overdue_tasks": overdue_cards,
		"overdue_overflow": overdue_overflow,
		"today_tasks": today_cards,
		"events": events,
		"today": today,
		"generated_at": frappe.utils.now(),
	}


# ------------------------------------------------------------------ wall display

# Donut semantics on the wall: Invoiced counts as done (it is excluded from
# CLOSED_TASK_STATUSES above only because it isn't *actionable* — a different
# question). Cancelled work belongs in neither slice.
WALL_DONE_STATUSES = {"Completed", "Invoiced"}
WALL_SKIP_STATUSES = {"Canceled", "Cancelled", "Template"}


def _project_task_stats(project_ids):
	"""Per-project task-completion counts for the wall carousel donuts —
	one GROUP BY query for all projects."""
	if not project_ids:
		return {}
	# Query builder rather than get_all: frappe 16 rejects SQL functions
	# passed as field strings ("SQL functions are not allowed as strings in
	# SELECT"), which 500'd the whole /wall page.
	task = frappe.qb.DocType("Task")
	rows = (
		frappe.qb.from_(task)
		.select(task.project, task.status, Count(task.name).as_("qty"))
		.where(task.project.isin(list(project_ids)))
		.groupby(task.project, task.status)
	).run(as_dict=True)
	stats = {}
	for row in rows:
		if row.status in WALL_SKIP_STATUSES:
			continue
		entry = stats.setdefault(row.project, {"total": 0, "completed": 0})
		entry["total"] += cint(row.qty)
		if row.status in WALL_DONE_STATUSES:
			entry["completed"] += cint(row.qty)
	for entry in stats.values():
		entry["pending"] = entry["total"] - entry["completed"]
	return stats


def _wall_settings():
	settings = frappe.get_cached_doc("ERPNext Enhancements Settings")

	def check_default_on(field):
		value = settings.get(field)
		return 1 if value is None else cint(value)

	return {
		"rotation_seconds": cint(settings.get("wall_rotation_seconds")) or 60,
		"refresh_seconds": cint(settings.get("wall_data_refresh_seconds")) or 300,
		"show_weather": check_default_on("wall_show_weather"),
		"weather_latitude": float(settings.get("weather_latitude") or 40.8894),
		"weather_longitude": float(settings.get("weather_longitude") or -111.8808),
		"weather_label": settings.get("weather_label") or "Bountiful, UT",
	}


@frappe.whitelist()
def get_wall_dashboard_data():
	"""Everything the /wall display renders: the Task Dashboard payload plus
	per-project completion stats, wall settings, and the deploy version (the
	page reloads itself when the server's token no longer matches its own —
	belt two of the deploy-pickup story, the service worker being belt one)."""
	from erpnext_enhancements.utils.deploy import get_deploy_version

	data = get_task_dashboard_data()  # role gate happens in there
	data["task_stats"] = _project_task_stats([p["name"] for p in data["top_projects"]])
	data["settings"] = _wall_settings()
	data["deploy_version"] = get_deploy_version()
	return data
