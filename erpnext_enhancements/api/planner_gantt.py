"""The Project Planner's Gantt tab: one project from start to finish (P6 of the planner UX redesign,
``docs/planner-ux-redesign.md`` §4.3).

:func:`get_project_gantt` is the planner's task feed for one project. The tab draws it with the app's
DHTMLX widget (``erpnext_enhancements.gantt.mount``), and the page indexes it as it indexes
``get_planner``, so every edit made on the chart goes through the page's one ``send()``: the reason
prompt, drafts, Undo, the conflict check, P4's auto-draft, the tasks that follow and "Notify crew?".
It is **not** ``api/gantt.py``'s feed. That one plots any doctype through a client config and writes
through ``update_gantt_row``, which knows none of the planner's rules.

Why it does not call ``get_planner``: a job runs for months, and ``get_planner`` computes everyone's
days over its range (100 days at most). The Gantt needs one project's tasks, whatever their dates, not
the bookings around them. So it reads the project's Task rows itself and builds each card with the
planner's own :func:`project_planner.build_card`. A card is the same shape as everywhere else; only
``booked`` (hours the engine spread over the days) is left out, because nothing computed it.

* **The display rule is the planner's** (D6, P2): phase and crew work only, unless ``all_tasks=1``.
  Group Tasks are the chart's tree. A group is drawn when something under it is.
* **Drafts** (``draft=1``, the default, as the page always reads) are the caller's and, on a Draft
  schedule, everyone's (P3's shared rows), from :func:`project_planner._draft_overlay`. A drafted task
  shows its drafted days and crew, and a drafted new task (``draft-new:…``) shows under the group it
  is filed under. ``drafts`` is ``get_planner``'s own block (:func:`project_planner.drafts_block`), for
  every project, so the page's strip stays whole on this tab.
* **Finished work stays on the chart, read-only.** Completed tasks are part of a job's history, so
  they draw but never move. Canceled and template tasks are left out.
* **Each card gains** ``baseline`` (P3's ``custom_baseline_start``/``_end``), ``span_days`` (working
  days, D7), ``parent_task``, ``gantt_parent`` (the group drawn above it, or None) and ``finished``.

Reads only, after the planner's role gate and a read permission check on the project.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate, nowdate

from erpnext_enhancements.api import project_planner as pp
from erpnext_enhancements.api.planner_lookahead import span_days
from erpnext_enhancements.project_enhancements import crew_availability as engine
from erpnext_enhancements.project_enhancements import fountain_move_jobs, planner_tracking
from erpnext_enhancements.project_enhancements.planner_templates import phase_number

#: The most Task rows one chart reads. A customer job has a few hundred at most.
MAX_TASKS = 1000
#: Statuses never drawn: a canceled task is gone from the plan, a template is not work.
LEFT_OUT = ("Canceled", "Cancelled", "Template")


# ---------------------------------------------------------------------- pure helpers


def group_sort(group):
	"""Phase groups in phase-number order, then the other groups by subject."""
	number = group.get("number")
	return (number is None, number if number is not None else 0, str(group.get("subject") or "").casefold())


def card_sort(card):
	"""Dated work earliest first, then by subject; the name breaks ties so the order is stable."""
	return (card.get("start") or "9999-99-99", str(card.get("subject") or "").casefold(), card.get("name") or "")


def drawn_groups(groups, parents, shown):
	"""The groups the chart draws: every group above a ``shown`` task, through ``parents``.

	``groups`` maps a group Task to its row (``subject``, ``parent_task``); ``parents`` maps any task to
	its ``parent_task``. A group above nothing shown is left out, and so is a parent that is not one of
	the project's groups (a broken or foreign ``parent_task``): the task then sits at the top. Returns
	``[{name, subject, parent, number}]`` in :func:`group_sort` order, each ``parent`` a drawn group or
	None."""
	keep = set()
	for name in shown:
		node, hops = parents.get(name), 0
		while node and node in groups and node not in keep and hops <= len(groups):
			keep.add(node)
			node, hops = groups[node].get("parent_task"), hops + 1
	out = []
	for name in keep:
		row = groups[name]
		parent = row.get("parent_task")
		out.append(
			{
				"name": name,
				"subject": row.get("subject") or name,
				"parent": parent if parent in keep else None,
				"number": phase_number(row.get("subject")),
			}
		)
	return sorted(out, key=group_sort)


def gantt_parent(name, parents, drawn):
	"""The group drawn above task ``name``, or None: its ``parent_task`` when that group is drawn."""
	parent = parents.get(name)
	return parent if parent in drawn else None


def project_span(cards):
	"""``(first, last)`` day across the dated ``cards`` (their baselines included), or ``(None, None)``."""
	days = []
	for card in cards:
		if card.get("start"):
			days += [card["start"], card.get("end") or card["start"]]
		base = card.get("baseline") or {}
		days += [day for day in (base.get("start"), base.get("end")) if day]
	return (min(days), max(days)) if days else (None, None)


# ---------------------------------------------------------------------- reads


def _project_rows(project):
	"""Every Task of ``project`` the chart may draw (groups and work), with what a card needs."""
	cols = engine._task_columns()
	selected = ", ".join(f"{expr} AS `{column}`" for column, expr in cols.items())
	baseline = (
		f", t.`{pp.BASELINE_START}` AS `{pp.BASELINE_START}`, t.`{pp.BASELINE_END}` AS `{pp.BASELINE_END}`"
		if pp._has_schedule_columns()
		else ""
	)
	type_expr, _stream = engine.planner_job_sql("p")
	return frappe.db.sql(
		f"""
		SELECT
			t.name, t.subject, t.project, t.status, t.exp_start_date, t.exp_end_date,
			t.expected_time, t.color, t.modified, t.parent_task, IFNULL(t.is_group, 0) AS is_group,
			{selected}{baseline},
			p.project_name AS project_title, {type_expr} AS project_type
		FROM `tabTask` t
		INNER JOIN `tabProject` p ON p.name = t.project
		WHERE t.project = %(project)s
			AND IFNULL(t.is_template, 0) = 0
			AND IFNULL(t.status, '') NOT IN %(left_out)s
		ORDER BY t.lft, t.creation
		LIMIT %(limit)s
		""",
		{"project": project, "left_out": LEFT_OUT, "limit": MAX_TASKS, **engine.PLANNER_SQL_VALUES},
		as_dict=True,
	)


def _baseline(row):
	start = engine._as_date(row.get(pp.BASELINE_START))
	end = engine._as_date(row.get(pp.BASELINE_END)) or start
	if not start and not end:
		return None
	return {"start": str(start or end), "end": str(end or start)}


@frappe.whitelist()
def get_project_gantt(project=None, all_tasks=0, draft=1):
	"""One project's schedule for the Gantt tab: get_planner-shaped cards plus ``"gantt"``. See the
	module docstring. With no ``project``, only ``projects`` (the picker's list) and ``"gantt": None``."""
	pp._require_planner()
	project = pp.given(project)
	if not project:
		# A bare /gantt: the projects to pick one from, as the toolbar's Project filter lists them.
		return {
			"today": nowdate(),
			"can_edit": bool(frappe.has_permission("Task", "write")),
			"projects": pp._projects(set()),
			"tasks": [],
			"unscheduled": [],
			"gantt": None,
		}
	if not frappe.db.exists("Project", project):
		frappe.throw(_("Project {0} was not found.").format(project or ""), frappe.DoesNotExistError)
	frappe.has_permission("Project", "read", doc=project, throw=True)
	today = getdate(nowdate())
	can_edit = bool(frappe.has_permission("Task", "write"))
	all_tasks = bool(cint(pp.given(all_tasks)))
	overlay = pp._draft_overlay() if cint(pp.given(draft)) else None

	rows = _project_rows(project)
	groups = {row["name"]: row for row in rows if cint(row.get("is_group"))}
	parents = {row["name"]: row.get("parent_task") or None for row in rows}
	work = {row["name"]: row for row in rows if not cint(row.get("is_group"))}
	baselines = {name: _baseline(row) for name, row in work.items()}

	# The drafts on this project: the drafted state replaces the stored row, and a drafted new task
	# joins the work under the group it is filed under.
	drafted, drafted_crew = {}, {}
	if overlay:
		for state, crew in overlay["extra"]:
			name = state.get("name")
			if state.get("project") != project or not name:
				continue
			info = overlay["drafted"].get(name) or {}
			if info.get("new"):
				parents[name] = info.get("parent_task") or None
				work[name] = dict(state, project_type=(work.get(name) or {}).get("project_type"))
			elif name in work:
				work[name] = dict(work[name], **{k: v for k, v in state.items() if k != "project_title"})
			else:
				continue
			drafted[name] = info
			drafted_crew[name] = crew

	# The active Planner Resources, for crew labels and the page's people (their work patterns are not
	# needed: nothing here spreads hours over days).
	resources = frappe.get_all(
		"Planner Resource",
		filters={"is_active": 1},
		fields=["name", "resource_name", "resource_type", "employee", "user", "resource_group", "home_team", "color"],
		order_by="resource_name asc",
		limit_page_length=0,
	)
	user_to_resource = {p.get("user"): p.get("name") for p in resources if p.get("user")}
	labels = {p.get("name"): p.get("resource_name") or p.get("name") for p in resources}
	stored = [name for name in work if not pp.is_draft_ref(name)]
	crew_rows, todo_users = engine.read_crews(stored)

	def crew_of(name):
		crew = (
			drafted_crew[name]
			if name in drafted_crew
			else engine.resolve_crew(crew_rows.get(name), todo_users.get(name), user_to_resource)
		)
		return [dict(m, label=m.get("label") or labels.get(m.get("resource")) or m.get("resource")) for m in crew]

	crews = {name: crew_of(name) for name in work}
	if all_tasks:
		hidden = set()
	else:
		candidates = [
			name for name, row in work.items() if not row.get("custom_rental_booking") and name not in drafted
		]
		under = engine.tasks_under_a_phase([name for name in candidates if not crews[name]])
		hidden = {name for name in candidates if not crews[name] and name not in under}

	shown = [name for name in work if name not in hidden]
	credentials = pp._credentials(stored)
	depends_on = pp._dependencies(stored)
	for name, info in drafted.items():
		credentials[name] = list(info.get("credentials") or [])
		if info.get("new"):
			depends_on[name] = list(info.get("depends_on") or [])
	predecessors = pp._task_rows({p for name in shown for p in depends_on.get(name) or [] if not pp.is_draft_ref(p)})
	people = {p.get("name"): p for p in resources}
	held = pp._held_credentials(people, {c for name in shown for c in credentials.get(name) or []})
	equipment = pp._equipment_labelled(engine.read_equipment([n for n in shown if not pp.is_draft_ref(n)]))
	actuals, _labels = planner_tracking.task_actuals([n for n in shown if not pp.is_draft_ref(n)])

	tasks, unscheduled, needs = [], [], []
	drawn = drawn_groups(groups, parents, shown)
	drawn_names = {group["name"] for group in drawn}
	for name in shown:
		row = work[name]
		preds = depends_on.get(name) or []
		card = pp.build_card(
			row,
			crews[name],
			{},
			credentials.get(name),
			today,
			can_edit,
			depends_on=preds,
			predecessors=[predecessors[p] for p in preds if p in predecessors],
			held=held,
			equipment=equipment.get(name),
			actuals=actuals.get(name),
		)
		# Nothing spread the hours over days here, so no "booked" figure is claimed.
		for member in card["crew"]:
			member["booked"] = None
		finished = row.get("status") in engine.FINISHED_STATUSES
		if finished:
			card["movable"] = False
		card["finished"] = finished
		card["parent_task"] = parents.get(name)
		card["gantt_parent"] = gantt_parent(name, parents, drawn_names)
		card["baseline"] = baselines.get(name)
		card["span_days"] = span_days(card.get("start"), card.get("end")) if card.get("start") else 0
		if name in drafted:
			card["drafted"] = True
			if drafted[name].get("by"):
				card["drafted_by"] = drafted[name]["by"]
			if drafted[name].get("new"):
				card["new_task"] = True
		(tasks if card.get("start") else unscheduled).append(card)
		if not finished and pp.needs_crew(card, todo_users.get(name)):
			needs.append(name)
	fountain_move_jobs.mark_read_only(tasks + unscheduled)
	tasks.sort(key=card_sort)
	unscheduled.sort(key=card_sort)

	first, last = project_span(tasks)
	listed = pp._projects({project})
	mine = next((p for p in listed if p.get("name") == project), {})
	settings = engine.get_settings()
	result = {
		"start": first,
		"end": last,
		"today": str(today),
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
			for p in resources
		],
		"days": {},
		"tasks": tasks,
		"unscheduled": unscheduled,
		"needs_crew": needs,
		"foreign": [],
		"projects": listed,
		"settings": {
			"default_day_hours": settings.get("default_day_hours"),
			"maintenance_visit_hours": settings.get("maintenance_visit_hours"),
		},
		"can_edit": can_edit,
		"all_tasks": all_tasks,
		"hidden_tasks": len(hidden),
		"gantt": {
			"project": project,
			"title": mine.get("title") or frappe.db.get_value("Project", project, "project_name") or project,
			"schedule": mine.get("schedule"),
			"groups": drawn,
			"first": first,
			"last": last,
			"truncated": len(rows) >= MAX_TASKS,
		},
	}
	if overlay is not None:
		result["drafts"] = pp.drafts_block(overlay)
	return result
