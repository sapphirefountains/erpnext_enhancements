"""The Project Planner's Overview tab (P9 of the planner UX redesign, ``docs/planner-ux-redesign.md``
§4.7): every active customer job on one timeline, and what needs a manager.

Nik's D3 (2026-10-21), for James and the approvers: "All jobs on a timeline", "Exceptions only", "A
weekly summary". Not crew capacity. :func:`get_overview` answers the first two; the weekly summary
(:func:`weekly_summary_html`, and the Monday email in ``project_enhancements/planner_weekly_summary.py``)
is the third and shares this module's read, so the page, the printout and the email cannot disagree.

* **Jobs.** One row per Active customer job (``project_planner._projects``: the planner's own rule,
  internal projects left out). Under it, a bar per **phase** (a group Task whose subject starts with a
  phase number, ``planner_templates.phase_number``), plus *Other work* for crew work under none, drawn
  over a **baseline ghost** (P3's ``custom_baseline_start``/``_end``). A phase's days are the earliest
  start and latest end of the work under it. A dated Task flagged ``is_milestone`` is a diamond. A job
  with no dated work yet falls back to the Project's own expected dates (``source: "project"``) and, with
  none, is listed as having no dates. A **Draft** schedule is marked, and (with ``draft=1``, as the page
  always reads) drawn from its drafts, because that is where a Draft schedule's days are.
* **The display rule is the planner's** (D6, P2): a task counts when it is under a phase or has a crew
  or an open assignee who is a Planner Resource. About 40 template admin tasks per job, all dated the
  project's first day, would otherwise make every job look overdue the day it started.
* **Overdue is counted by end date, never by the status value** (spec §8). 1,005 open tasks carry the
  status ``Overdue`` on production, left by an old import, so the status is not evidence of anything.
  A task is overdue here when it is open and its last day is before today.
* **Behind baseline.** A phase is behind when its open work ends later than that work's baseline by
  more than :data:`BEHIND_DAYS` **working days** (weekdays that are not company holidays,
  ``project_planner.working_days_between``). Work under no phase is judged task by task. Nothing is
  behind without a baseline: production's jobs have none until someone sets one from the Gantt, so
  today this part of the list is empty, and the page says how many jobs have no baseline.
* **Running over** is Phase 4's rule (``planner_tracking.is_over_plan``: clocked hours past the
  estimate by more than 10%), for open tasks that carry an estimate (``expected_time`` > 0). A task
  with no estimate is not judged here: the card judges it against the hours the engine booked, which
  needs the whole availability pass this read does not make.
* **Conflicts** are the Conflict center's (``planner_conflicts``, one Google-free engine pass over the
  next :data:`CONFLICT_DAYS` days) that nobody has kept with a reason. The pass is the one costly part
  and does not depend on the window, so the page's reads keep the list for two minutes (a conflict kept
  with a reason stays listed that long); the Monday email always reads fresh. If it fails the rest still
  answers, with ``conflicts_ok: False``.
* **A flagged milestone always shows.** A dated Task with ``is_milestone`` is a manager's own marker
  (six exist on production, 2026-10-22), so it is a diamond whether or not the display rule would show it
  as work. It is not counted as work (phases, overdue, running over) unless the rule shows it.
* **A cap is said, not hidden.** One read takes at most :data:`MAX_TASKS` Task rows; past that the answer
  carries ``tasks_truncated`` and the page says some jobs may show less than they have.
* **Every line opens its source.** A line carries ``project`` (the page opens that job's Gantt),
  and a conflict its ``date`` (the page opens the Crew board on that day).

Reads only, after the planner's role gate. Raw SQL with bound parameters, as every planner read. The
pure helpers (:func:`build_job`, :func:`exception_lines`, the window and week rules) take plain values,
so ``tests/test_planner_overview.py`` runs them without a bench.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from erpnext_enhancements.api import project_planner as pp
from erpnext_enhancements.api.planner_lookahead import OTHER_WORK, nearest_phase
from erpnext_enhancements.project_enhancements import crew_availability as engine
from erpnext_enhancements.project_enhancements import planner_tracking
from erpnext_enhancements.project_enhancements.planner_templates import phase_number

#: How many months the timeline shows, and the default ("Oct – Mar" in the spec's picture).
MONTH_CHOICES = (3, 6, 9, 12)
DEFAULT_MONTHS = 6
#: A phase or task is an exception when it is MORE than this many working days behind its baseline.
BEHIND_DAYS = 2
#: The most Task rows one read takes. Production has about 1,400 across its 53 active customer jobs
#: (2026-10-22), so this is room, not a limit anyone meets; if one is met, the answer says so.
MAX_TASKS = 20000
#: The most exception lines one answer carries; the rest are counted ("and N more").
EXCEPTION_LIMIT = 60
#: How many overdue tasks a line names.
OVERDUE_NAMED = 3
#: Unresolved conflicts count for this many days from today.
CONFLICT_DAYS = 14
#: How many conflicts a line spells out.
CONFLICT_NAMED = 3
#: How long the Overview keeps the conflict list (it does not depend on the window).
CONFLICT_CACHE_SECONDS = 120
#: Statuses never read: a canceled task is gone from the plan, a template is not work.
LEFT_OUT = ("Canceled", "Cancelled", "Template")
#: A conflict-center query chunk: the most names one IN list carries.
CHUNK = 500
#: The kinds of exception line, in the order the list shows them.
KIND_ORDER = {"behind": 0, "over": 1, "conflicts": 2, "overdue": 3}


# ---------------------------------------------------------------------- pure helpers


def given_day(value, default=None):
	"""A request's day as a date. ``default`` when none was given (``project_planner.given``: blank, null);
	a clear error, not a 500, for one that is not a date (a typo in an address, a scrape): ``_as_date``
	raises ``ValueError``, which Frappe answers with a traceback. The message does not echo the value."""
	value = pp.given(value)
	if value is None:
		return default
	try:
		return engine._as_date(value)
	except (TypeError, ValueError):
		frappe.throw(_("That is not a date. Use the form YYYY-MM-DD."))


def months_choice(value):
	"""``value`` as one of :data:`MONTH_CHOICES`; anything else, text included, is the default."""
	try:
		months = int(str(pp.given(value) or "").strip())
	except ValueError:
		return DEFAULT_MONTHS
	return months if months in MONTH_CHOICES else DEFAULT_MONTHS


def month_window(day, months):
	"""``(first, last)`` day of the ``months`` whole calendar months from the month holding ``day``."""
	first = engine._as_date(day).replace(day=1)
	index = first.month - 1 + months_choice(months)
	return first, datetime.date(first.year + index // 12, index % 12 + 1, 1) - datetime.timedelta(days=1)


def default_summary_week(today, first_weekday):
	"""The first day of the week a weekly summary covers when nobody names one: the week holding
	``today`` while it is only a day old (a Sunday or Monday on production, so the Monday email and a
	Sunday-night print are about the week ahead), else the **next** week ("next week by job and by
	person", spec §4.7)."""
	today = engine._as_date(today)
	start = pp.week_start(today, first_weekday)
	return start if (today - start).days <= 1 else start + datetime.timedelta(days=7)


def week_holding(today, first_weekday):
	"""The first day of the week that holds ``today``. What the Monday job describes and claims, however late
	it runs: unlike :func:`default_summary_week` it never looks ahead."""
	return pp.week_start(engine._as_date(today), first_weekday)


def plural(count, one, many):
	"""``one`` for 1, else ``many``; both are ``str.format`` templates taking the count as ``{0}``."""
	return (one if count == 1 else many).format(count)


def normalize_task(row):
	"""A Task row as the rollup reads it: dates as dates, flags as bools. Pure."""
	span = engine.task_span(row)
	base_end = engine._as_date(row.get(pp.BASELINE_END))
	base_start = engine._as_date(row.get(pp.BASELINE_START)) or base_end
	return {
		"name": row.get("name"),
		"subject": row.get("subject") or row.get("name"),
		"status": row.get("status") or "",
		"parent": row.get("parent_task") or None,
		"group": bool(cint(row.get("is_group"))),
		"start": span[0] if span else None,
		"end": span[1] if span else None,
		"base_start": base_start,
		"base_end": base_end or base_start,
		"hours": flt(row.get("expected_time")),
		"milestone": bool(cint(row.get("is_milestone"))),
	}


def is_finished(task):
	return task.get("status") in engine.FINISHED_STATUSES


def behind_days(base_end, end, holidays=()):
	"""Working days ``end`` is past ``base_end``; 0 when on time or early, or either is missing."""
	if not base_end or not end:
		return 0
	days = pp.working_days_between(base_end, end, holidays)
	return days if days > 0 else 0


def _day(value):
	return str(value) if value else None


def _span(start, end):
	return {"start": _day(start), "end": _day(end)} if start else None


def _bucket_sort(bucket):
	number = bucket.get("number")
	return (
		bucket["key"] == "",
		number is None,
		number if number is not None else 0,
		str(bucket.get("subject") or "").casefold(),
	)


def rollup(bucket, holidays=()):
	"""A bucket's phase row, from its ``tasks`` (normalized, schedulable): its days, baseline, slip,
	counts and progress. ``slip_days`` is None without an open task that has a baseline."""
	tasks = bucket["tasks"]
	dated = [t for t in tasks if t["start"]]
	based = [t for t in dated if t["base_end"]]
	open_based = [t for t in based if not is_finished(t)]
	slip = None
	if open_based:
		slip = behind_days(
			max(t["base_end"] for t in open_based), max(t["end"] for t in open_based), holidays
		)
	done = sum(1 for t in tasks if is_finished(t))
	return {
		"key": bucket["key"],
		"name": bucket["name"],
		"subject": bucket["subject"],
		"number": bucket["number"],
		"start": _day(min((t["start"] for t in dated), default=None)),
		"end": _day(max((t["end"] for t in dated), default=None)),
		"baseline": _span(
			min((t["base_start"] for t in based), default=None),
			max((t["base_end"] for t in based), default=None),
		),
		"slip_days": slip,
		"tasks": len(tasks),
		"dated": len(dated),
		"done": done,
		"open": len(tasks) - done,
		"progress": round(done / len(tasks), 3) if tasks else 0,
	}


def build_job(project, tasks, today, crewed=(), holidays=(), threshold=BEHIND_DAYS):
	"""One job's row, from its normalized ``tasks`` (groups included), as ``(job, shown)``.

	``project`` is ``{"name", "title", "pm", "schedule", "expected_start", "expected_end"}`` and
	``crewed`` the names of tasks somebody is on. ``shown`` is the schedulable work (the D6 rule: under a
	phase, or crewed), which the weekly summary lists by week. The job is JSON-ready:

	``{name, title, pm, schedule, draft, first, last, base_first, base_last, source, phases, milestones,
	counts, behind, overdue, hours_tasks}``. ``phases`` are the buckets with dated work, in phase
	order, *Other work* last; ``behind`` is the exception data (a phase, or a task outside any phase);
	``overdue`` is ``{count, oldest, tasks: [subject...]}``; ``hours_tasks`` are the open shown tasks
	with an estimate, for the running-over check."""
	today = engine._as_date(today)
	parents = {t["name"]: t["parent"] for t in tasks}
	subjects = {t["name"]: t["subject"] for t in tasks}
	buckets, shown = {}, []
	for task in tasks:
		if task["group"]:
			continue
		phase = nearest_phase(task["name"], parents, subjects)
		if phase is None and task["name"] not in crewed:
			continue
		shown.append(task)
		bucket = buckets.get(phase)
		if bucket is None:
			subject = subjects.get(phase) if phase else None
			bucket = buckets[phase] = {
				"key": phase or "",
				"name": phase,
				"subject": subject or OTHER_WORK,
				"number": phase_number(subject) if subject else None,
				"tasks": [],
			}
		bucket["tasks"].append(task)

	rows = [rollup(bucket, holidays) for bucket in sorted(buckets.values(), key=_bucket_sort)]
	phases = [row for row in rows if row["start"]]
	behind = []
	for row in rows:
		if row["name"] and row["slip_days"] and row["slip_days"] > threshold:
			behind.append(
				{"scope": "phase", "phase": row["name"], "subject": row["subject"], "days": row["slip_days"]}
			)
	for task in buckets.get(None, {}).get("tasks", []):
		if is_finished(task) or not task["start"] or not task["base_end"]:
			continue
		days = behind_days(task["base_end"], task["end"], holidays)
		if days > threshold:
			behind.append({"scope": "task", "task": task["name"], "subject": task["subject"], "days": days})
	behind.sort(key=lambda b: (-b["days"], str(b["subject"]).casefold()))

	late = sorted(
		(t for t in shown if t["end"] and not is_finished(t) and t["end"] < today),
		key=lambda t: (t["end"], t["name"]),
	)
	milestones = [
		{"name": t["name"], "subject": t["subject"], "date": _day(t["start"]), "done": is_finished(t)}
		for t in tasks
		if t["milestone"] and not t["group"] and t["start"]
	]
	first = min((row["start"] for row in phases), default=None)
	last = max((row["end"] for row in phases), default=None)
	base_first = min((row["baseline"]["start"] for row in phases if row["baseline"]), default=None)
	base_last = max((row["baseline"]["end"] for row in phases if row["baseline"]), default=None)
	source = "tasks" if first else None
	if not first and (project.get("expected_start") or project.get("expected_end")):
		first = _day(project.get("expected_start") or project.get("expected_end"))
		last = _day(project.get("expected_end") or project.get("expected_start"))
		source = "project"
	schedule = project.get("schedule") or None
	return (
		{
			"name": project["name"],
			"title": project.get("title") or project["name"],
			"pm": project.get("pm"),
			"schedule": schedule,
			"draft": bool(schedule and schedule.get("status") != pp.SCHEDULE_PUBLISHED),
			"first": first,
			"last": last,
			"base_first": base_first,
			"base_last": base_last,
			"source": source,
			"phases": phases,
			"milestones": milestones,
			"counts": {
				"tasks": len(shown),
				"dated": sum(1 for t in shown if t["start"]),
				"done": sum(1 for t in shown if is_finished(t)),
				"overdue": len(late),
				"baselined": sum(1 for t in shown if t["base_end"]),
			},
			"behind": behind,
			"overdue": {
				"count": len(late),
				"oldest": _day(late[0]["end"]) if late else None,
				"tasks": [t["subject"] for t in late[:OVERDUE_NAMED]],
			},
			"hours_tasks": [
				{"name": t["name"], "subject": t["subject"], "planned": t["hours"], "status": t["status"]}
				for t in shown
				if t["hours"] > 0 and not is_finished(t)
			],
		},
		shown,
	)


def running_over(hours_tasks, actuals):
	"""The ``hours_tasks`` whose clocked hours (``actuals``: ``{task: {person: hours}}``) are past their
	estimate (Phase 4's rule), as ``[{name, subject, planned, actual}]``, the worst overrun first."""
	out = []
	for task in hours_tasks or []:
		actual = round(sum(flt(h) for h in (actuals.get(task["name"]) or {}).values()), 2)
		if planner_tracking.is_over_plan(task["planned"], actual, task.get("status")):
			out.append(
				{
					"name": task["name"],
					"subject": task["subject"],
					"planned": task["planned"],
					"actual": actual,
				}
			)
	out.sort(key=lambda t: (-(t["actual"] - t["planned"]), t["subject"].casefold()))
	return out


def _date_text(value):
	day = engine._as_date(value)
	return f"{day:%b} {day.day}" if day else ""


def exception_lines(jobs, over, conflicts, limit=EXCEPTION_LIMIT):
	"""``(lines, counts, more)``: what needs a manager, ordered behind, running over, conflicts, overdue.

	``jobs`` are :func:`build_job` rows, ``over`` is ``{project: [running_over rows]}`` and
	``conflicts`` a list of ``{date, label, message}`` (None when they could not be read). ``lines`` are
	``{kind, severity, project, project_title, phase, task, subject, days, count, date, text}``, cut to
	``limit`` with the rest counted in ``more``. ``counts`` is the whole, not the cut."""
	lines = []
	counts = {"behind": 0, "over": 0, "conflicts": 0, "overdue": 0, "overdue_jobs": 0}
	for job in jobs:
		title = job["title"]
		for item in job["behind"]:
			counts["behind"] += 1
			lines.append(
				{
					"kind": "behind",
					"severity": 2,
					"project": job["name"],
					"project_title": title,
					"phase": item.get("phase"),
					"task": item.get("task"),
					"subject": item["subject"],
					"days": item["days"],
					"count": 1,
					"date": None,
					"text": _("{0}: {1} is {2} behind its baseline").format(
						title,
						item["subject"],
						plural(item["days"], _("{0} working day"), _("{0} working days")),
					),
				}
			)
		for task in over.get(job["name"]) or []:
			counts["over"] += 1
			lines.append(
				{
					"kind": "over",
					"severity": 2,
					"project": job["name"],
					"project_title": title,
					"phase": None,
					"task": task["name"],
					"subject": task["subject"],
					"days": None,
					"count": 1,
					"date": None,
					"text": _("{0}: “{1}” is running over ({2}h of {3}h)").format(
						title,
						task["subject"],
						engine.fmt_hours(task["actual"]),
						engine.fmt_hours(task["planned"]),
					),
				}
			)
		late = job["overdue"]
		if late["count"]:
			counts["overdue"] += late["count"]
			counts["overdue_jobs"] += 1
			named = ", ".join(late["tasks"])
			lines.append(
				{
					"kind": "overdue",
					"severity": 1,
					"project": job["name"],
					"project_title": title,
					"phase": None,
					"task": None,
					"subject": named,
					"days": None,
					"count": late["count"],
					"date": late["oldest"],
					"text": _("{0}: {1} overdue (the oldest was due {2}: {3})").format(
						title,
						plural(late["count"], _("{0} task"), _("{0} tasks")),
						_date_text(late["oldest"]),
						named,
					),
				}
			)
	if conflicts:
		counts["conflicts"] = len(conflicts)
		first = conflicts[0]
		spelled = "; ".join(f"{_date_text(c['date'])}: {c['message']}" for c in conflicts[:CONFLICT_NAMED])
		extra = len(conflicts) - CONFLICT_NAMED
		if extra > 0:
			spelled += " " + _("and {0} more").format(extra)
		lines.append(
			{
				"kind": "conflicts",
				"severity": 3,
				"project": None,
				"project_title": None,
				"phase": None,
				"task": None,
				"subject": spelled,
				"days": None,
				"count": len(conflicts),
				"date": first["date"],
				"text": _("{0} in the next two weeks: {1}").format(
					plural(len(conflicts), _("{0} unresolved conflict"), _("{0} unresolved conflicts")),
					spelled,
				),
			}
		)
	lines.sort(
		key=lambda l: (
			KIND_ORDER[l["kind"]],
			-(l["days"] or 0) if l["kind"] == "behind" else 0,
			-(l["count"] or 0) if l["kind"] == "overdue" else 0,
			str(l["project_title"] or "").casefold(),
			str(l["subject"] or "").casefold(),
		)
	)
	counts["lines"] = len(lines)
	return lines[:limit], counts, max(len(lines) - limit, 0)


# ---------------------------------------------------------------------- reads


def _chunks(names):
	names = sorted({n for n in names or () if n})
	for index in range(0, len(names), CHUNK):
		yield names[index : index + CHUNK]


def _read_expected(names):
	"""``{project: (expected_start, expected_end)}`` for the Project's own dates."""
	out = {}
	for chunk in _chunks(names):
		for row in frappe.db.sql(
			"SELECT name, expected_start_date, expected_end_date FROM `tabProject` WHERE name IN %(names)s",
			{"names": tuple(chunk)},
			as_dict=True,
		):
			out[row.get("name")] = (row.get("expected_start_date"), row.get("expected_end_date"))
	return out


def _read_task_rows(projects):
	"""``(rows, truncated)``: the Task rows of ``projects`` (names), groups included, canceled and template
	work left out. ``truncated`` is True when there were more than :data:`MAX_TASKS`: the rows past the cap
	(the last projects, by name) are not read, and the page says so rather than let a job it never read look
	clean."""
	if not projects:
		return [], False
	baseline = (
		f", t.`{pp.BASELINE_START}` AS `{pp.BASELINE_START}`, t.`{pp.BASELINE_END}` AS `{pp.BASELINE_END}`"
		if pp._has_schedule_columns()
		else ""
	)
	milestone = ", t.is_milestone AS is_milestone" if pp._task_has_column("is_milestone") else ""
	rows = frappe.db.sql(
		f"""
		SELECT
			t.name, t.subject, t.project, t.status, t.parent_task, IFNULL(t.is_group, 0) AS is_group,
			t.exp_start_date, t.exp_end_date, t.expected_time{milestone}{baseline}
		FROM `tabTask` t
		WHERE t.project IN %(projects)s
			AND IFNULL(t.is_template, 0) = 0
			AND IFNULL(t.status, '') NOT IN %(left_out)s
		ORDER BY t.project, t.lft, t.creation
		LIMIT %(limit)s
		""",
		{"projects": tuple(sorted(projects)), "left_out": LEFT_OUT, "limit": MAX_TASKS + 1},
		as_dict=True,
	)
	return rows[:MAX_TASKS], len(rows) > MAX_TASKS


def apply_drafts(rows, overlay, projects):
	"""``rows`` (Task rows) with the drafts on ``projects`` applied, and the names of drafted tasks that
	have someone on them. A drafted task's days, status and subject replace the stored row's; a drafted
	**new** task joins under the group it is filed under (``overlay["drafted"][name]["parent_task"]``).
	Pure over its arguments."""
	by_name = {row.get("name"): dict(row) for row in rows}
	order = [row.get("name") for row in rows]
	crewed = set()
	for state, crew in overlay.get("extra") or []:
		name, project = state.get("name"), state.get("project")
		if not name or project not in projects:
			continue
		info = (overlay.get("drafted") or {}).get(name) or {}
		if crew:
			crewed.add(name)
		if info.get("new"):
			row = {
				"name": name,
				"project": project,
				"is_group": 0,
				"parent_task": info.get("parent_task"),
			}
			by_name[name] = row
			order.append(name)
		elif name in by_name:
			row = by_name[name]
		else:
			continue
		for key in (
			"subject",
			"status",
			"exp_start_date",
			"exp_end_date",
			"expected_time",
			"custom_start_datetime",
			"custom_end_datetime",
		):
			if key in state:
				row[key] = state[key]
	return [by_name[name] for name in order if name in by_name], crewed


def _read_crewed(names):
	"""The names among ``names`` that somebody is on: a Task Crew Member row, or an open assignee who is
	an active Planner Resource (``engine.resolve_crew``, the one definition)."""
	names = [n for n in names if n and not pp.is_draft_ref(n)]
	if not names:
		return set()
	people = frappe.get_all(
		"Planner Resource", filters={"is_active": 1}, fields=["name", "user"], limit_page_length=0
	)
	user_to_resource = {p.get("user"): p.get("name") for p in people if p.get("user")}
	crewed = set()
	for chunk in _chunks(names):
		crew_rows, todo_users = engine.read_crews(chunk)
		for name in chunk:
			if engine.resolve_crew(crew_rows.get(name), todo_users.get(name), user_to_resource):
				crewed.add(name)
	return crewed


def draft_projects(names):
	"""The subset of project ``names`` whose schedule is not Published (D8): Draft, or blank, which reads
	Draft (``project_planner.schedule_status``). Whatever list a project is on: the weekly summary's rule is
	about the booking's own project, because the availability engine books a Draft project's *stored* tasks
	like any other, and a project that is not an Active customer job (On Hold, say) is booked too.

	None before P3's columns exist (every schedule then reads as published, as the Lookahead's does); a name
	that is not a Project (a deleted one) is not in the answer. One bound query per :data:`CHUNK` names."""
	names = sorted({n for n in names or () if n})
	if not names or not pp._has_schedule_columns():
		return set()
	drafts = set()
	for chunk in _chunks(names):
		for row in frappe.db.sql(
			"SELECT name, custom_schedule_status FROM `tabProject` WHERE name IN %(names)s",
			{"names": tuple(chunk)},
			as_dict=True,
		):
			if pp.schedule_status(row.get("custom_schedule_status")) != pp.SCHEDULE_PUBLISHED:
				drafts.add(row.get("name"))
	return drafts


def _conflicts(today, published_only=False, cached=True):
	"""``[{date, label, message, projects}]``: the Conflict center's list for the next two weeks that nobody
	has kept with a reason, or None when the pass fails (the rest of the Overview is worth showing without
	it). ``published_only`` (the weekly summary) drops every conflict that involves a project whose schedule
	is not Published (:func:`draft_projects`), listed or not.

	It is the one costly part of a read, and it does not depend on the window, so a manager stepping through
	months or flipping the picker would otherwise pay for it each click: the unfiltered list is kept for
	:data:`CONFLICT_CACHE_SECONDS`. A conflict kept with a reason therefore stays listed for up to that long.
	``cached=False`` (the Monday email) always reads fresh. A cache that is down is no cache."""
	key = f"ee_planner_overview_conflicts:{today}"
	found = None
	if cached:
		try:
			found = frappe.cache.get_value(key)
		except Exception:
			found = None
	if found is None:
		try:
			from erpnext_enhancements.api import planner_conflicts

			last = today + datetime.timedelta(days=CONFLICT_DAYS - 1)
			found = [
				{
					"date": c["date"],
					"label": c.get("resource_label"),
					"message": c.get("message") or "",
					"projects": sorted({i.get("project") for i in c.get("items") or [] if i.get("project")}),
				}
				for c in planner_conflicts._collect(today, last, "project")
				if not c.get("acknowledged")
			]
		except Exception:
			frappe.log_error(
				title="Overview: the conflicts could not be read", message=frappe.get_traceback()
			)
			return None
		if cached:
			try:
				frappe.cache.set_value(key, found, expires_in_sec=CONFLICT_CACHE_SECONDS)
			except Exception:
				pass
	if not published_only:
		return found
	hidden = draft_projects({project for c in found for project in c.get("projects") or ()})
	return [c for c in found if not hidden.intersection(c.get("projects") or ())]


def _actuals(hours_tasks):
	names = [t["name"] for t in hours_tasks if not pp.is_draft_ref(t["name"])]
	if not names:
		return {}
	try:
		actuals, _labels = planner_tracking.task_actuals(names)
		return actuals
	except Exception:
		frappe.log_error(title="Overview: clocked hours could not be read", message=frappe.get_traceback())
		return {}


def collect(draft=True, published_only=False, with_conflicts=True, today=None, cached_conflicts=True):
	"""Everything the Overview and the weekly summary are drawn from.

	``{today, listed, jobs, shown, over, conflicts, drafts_named, drafts_listed, truncated, overlay}``:
	``jobs`` are :func:`build_job` rows in project-name order; ``shown`` maps a job to its schedulable
	tasks; ``listed`` is ``project_planner._projects``' list (for the page's filters); ``overlay`` is the
	drafts read (None without ``draft``); ``truncated`` is True when the task cap cut the read.
	``published_only`` (the weekly summary) leaves a Draft schedule out of ``jobs`` and names it in
	``drafts_named`` (``drafts_listed`` are the same listed jobs' names), and drops the conflicts that
	involve any project whose schedule is not Published (:func:`draft_projects`, whether or not it is a
	listed job): a Draft schedule is private until it is published (D8), so the summary never describes
	one, and the engine books a Draft project's stored tasks like any other, so anything read from the
	engine is filtered by the booking's own project."""
	today = engine._as_date(today) or getdate(nowdate())
	listed = pp._projects(set())
	overlay = None
	if draft:
		try:
			overlay = pp._draft_overlay()
		except Exception:
			frappe.log_error(title="Overview: drafts could not be read", message=frappe.get_traceback())
	drafts_named, drafts_listed = [], set()
	if published_only:
		drafted = [p for p in listed if (p.get("schedule") or {}).get("status") == pp.SCHEDULE_DRAFT]
		drafts_named = [p["title"] for p in drafted]
		drafts_listed = {p["name"] for p in drafted}
		listed_jobs = [p for p in listed if p["name"] not in drafts_listed]
	else:
		listed_jobs = listed
	names = {p["name"] for p in listed_jobs}
	rows, truncated = _read_task_rows(names)
	crewed_drafts = set()
	if overlay:
		rows, crewed_drafts = apply_drafts(rows, overlay, names)
	expected = _read_expected(names)
	tasks = {name: [] for name in names}
	for row in rows:
		tasks[row.get("project")].append(normalize_task(row))
	# Crew is only asked about work under no phase: a phase's work counts without one.
	candidates = []
	for items in tasks.values():
		parents = {t["name"]: t["parent"] for t in items}
		subjects = {t["name"]: t["subject"] for t in items}
		candidates += [
			t["name"] for t in items if not t["group"] and nearest_phase(t["name"], parents, subjects) is None
		]
	crewed = _read_crewed(candidates) | crewed_drafts
	holidays = pp._shift_holidays()
	jobs, shown = [], {}
	for project in listed_jobs:
		start, end = expected.get(project["name"], (None, None))
		info = dict(project, expected_start=start, expected_end=end)
		job, work = build_job(info, tasks.get(project["name"], []), today, crewed, holidays)
		jobs.append(job)
		shown[project["name"]] = work
	hours = [t for job in jobs for t in job["hours_tasks"]]
	actuals = _actuals(hours) if hours else {}
	over = {job["name"]: running_over(job["hours_tasks"], actuals) for job in jobs}
	conflicts = _conflicts(today, published_only, cached_conflicts) if with_conflicts else None
	return {
		"today": today,
		"listed": listed,
		"jobs": jobs,
		"shown": shown,
		"over": over,
		"conflicts": conflicts,
		"drafts_named": drafts_named,
		"drafts_listed": drafts_listed,
		"truncated": truncated,
		"overlay": overlay,
	}


# ---------------------------------------------------------------------- endpoints


def overview_payload(found, first, last, months):
	"""The Overview's answer from :func:`collect`'s: jobs trimmed to the window, the exception list."""
	first_text, last_text = str(first), str(last)
	jobs, no_dates, outside = [], [], 0
	for job in found["jobs"]:
		if not job["first"]:
			no_dates.append({"name": job["name"], "title": job["title"], "pm": job["pm"]})
			continue
		if job["last"] < first_text or job["first"] > last_text:
			outside += 1
			continue
		kept = dict(job)
		kept["phases"] = [p for p in job["phases"] if p["end"] >= first_text and p["start"] <= last_text]
		kept["milestones"] = [m for m in job["milestones"] if first_text <= m["date"] <= last_text]
		kept.pop("hours_tasks", None)
		jobs.append(kept)
	lines, counts, more = exception_lines(found["jobs"], found["over"], found["conflicts"])
	counts.update(
		jobs=len(found["jobs"]),
		baselined=sum(1 for job in found["jobs"] if job["counts"]["baselined"]),
		drafts=sum(1 for job in found["jobs"] if job["draft"]),
	)
	return {
		"today": str(found["today"]),
		"start": first_text,
		"end": last_text,
		"months": months,
		"choices": list(MONTH_CHOICES),
		"jobs": jobs,
		"no_dates": no_dates,
		"outside": outside,
		"exceptions": lines,
		"more_exceptions": more,
		"counts": counts,
		"conflicts_ok": found["conflicts"] is not None,
		"tasks_truncated": bool(found.get("truncated")),
		"behind_days": BEHIND_DAYS,
		"projects": found["listed"],
	}


@frappe.whitelist()
def get_overview(start=None, months=DEFAULT_MONTHS, draft=1):
	"""The Overview: ``{today, start, end, months, choices, jobs, no_dates, outside, exceptions,
	more_exceptions, counts, conflicts_ok, tasks_truncated, behind_days, projects, can_edit, drafts}``. See
	the module docstring. ``start`` is any day in the first month (today when not given); the window is ``months``
	(3, 6, 9 or 12; anything else is 6) whole calendar months. ``draft=1`` draws a Draft schedule from its
	drafts (the caller's own, and everyone's on a Draft schedule), as the page always reads."""
	pp._require_planner()
	months = months_choice(months)
	first, last = month_window(given_day(start, getdate(nowdate())), months)
	found = collect(draft=bool(cint(pp.given(draft))))
	answer = overview_payload(found, first, last, months)
	answer["can_edit"] = bool(frappe.has_permission("Task", "write"))
	if found["overlay"] is not None:
		answer["drafts"] = pp.drafts_block(found["overlay"])
	return answer


@frappe.whitelist()
def weekly_summary_html(start=None):
	"""The printable weekly summary for the week holding ``start`` (the week ahead when not given:
	:func:`default_summary_week`), as an HTML document. The page opens it in a window and prints it
	there; nothing here makes a PDF. The same data, in the same words, as the Monday email."""
	pp._require_planner()
	from erpnext_enhancements.project_enhancements import planner_weekly_summary as summary

	return summary.summary_document(summary.build_summary(pp.given(start)))


@frappe.whitelist(methods=["POST"])
def send_summary_preview(start=None):
	"""Email the caller the weekly summary email for the week holding ``start`` (the week ahead by
	default): "Send me a weekly summary preview" on the Settings form. System Manager or Projects Manager.
	Works with the Monday switch off, claims no week, and mails nobody else, so the email can be read
	before anybody is named to get it. ``{"sent": bool, "message": str}``."""
	if frappe.session.user != "Administrator" and not pp.DIGEST_PREVIEW_ROLES & set(frappe.get_roles()):
		frappe.throw(
			_("Only a System Manager or Projects Manager can preview the summary."), frappe.PermissionError
		)
	from erpnext_enhancements.project_enhancements import planner_weekly_summary as summary

	return summary.send_preview(frappe.session.user, pp.given(start))
