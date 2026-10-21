"""The Project Planner's Lookahead (P5 of the planner UX redesign, ``docs/planner-ux-redesign.md`` §4.2).

Nik's D2 (2026-10-21): the screen a PM lands on is a **lookahead across all jobs**. Rows are projects,
then their phases, then tasks, and the next three weeks run across, with crew initials on the bars.

:func:`get_lookahead` is :func:`project_planner.get_planner` over the lookahead's window, plus the tree
the tab draws (``"lookahead"``). It is deliberately the same answer with more in it, not a second read:

* **The bars are the planner's own cards.** The page indexes ``tasks`` and ``unscheduled`` exactly as
  it does for the week view, so a drag, a resize, the side panel, the right-click menu, the selection
  and every write go through the page's one ``send()``, with its reason prompt, drafts, Undo and the
  conflict check, unchanged. The tree only names cards; it never carries a second copy of one.
* **The display rule is get_planner's** (D6, P2): phase and crew work only, unless ``all_tasks=1``.
  So are drafts (``draft=1``, P3's shared rows for a Draft schedule), the schedule state of each
  project (P3) and the Fountain Move rule: a move task's card is not movable here
  (``fountain_move_jobs.mark_read_only``), and the tab draws it read-only.
* **The window** starts on the site's first weekday (System Settings, Sunday on production) and runs
  2, 3, 4 or 6 whole weeks, 3 by default (§4.2's range picker).
* **The tree** (:func:`lookahead_tree`, pure) is every project with a card in the window or an
  unscheduled task, each with its phases in phase-number order and the work under them, plus the
  Active customer jobs with **no tasks yet** (``planner_actions.get_projects_needing_tasks``, the
  query the Needs tasks tray used, which this row replaces). A task's phase is its nearest ancestor,
  through ``parent_task``, whose subject starts with a phase number (``planner_templates.phase_number``,
  the one reading of it); a task under none is *Other work*.
* **Each card gains** ``phase`` (its phase's Task name, or None), ``baseline`` (``{start, end}`` from
  P3's ``custom_baseline_start``/``_end``, or None) and ``span_days`` (its size in working days, D7).
* ``project``, ``pm`` and ``group`` narrow the tree for a caller that asks (the AI tools, a link); the
  page filters its own copy, as it does on every tab, so a filter picked there does not ask again.

Reads only, after the planner's role gate (``get_planner`` checks it). Pure helpers take plain values
so ``tests/test_planner_lookahead.py`` runs them without a bench.
"""

import datetime

import frappe
from frappe.utils import getdate, nowdate

from erpnext_enhancements.api import project_planner as pp
from erpnext_enhancements.project_enhancements import crew_availability as engine
from erpnext_enhancements.project_enhancements.planner_templates import phase_number

#: The range picker's choices, in weeks, and the default (§4.2: "2 / 3 / 4 / 6", 3 by default).
LOOKAHEAD_WEEKS = (2, 3, 4, 6)
DEFAULT_WEEKS = 3
#: The label of the work in a project that sits under no phase.
OTHER_WORK = "Other work"


# ---------------------------------------------------------------------- pure helpers


def lookahead_weeks(value):
	"""``value`` as one of :data:`LOOKAHEAD_WEEKS`; anything else, text included, is the default."""
	try:
		weeks = int(str(pp.given(value) or "").strip())
	except ValueError:
		return DEFAULT_WEEKS
	return weeks if weeks in LOOKAHEAD_WEEKS else DEFAULT_WEEKS


def lookahead_window(day, weeks, first):
	"""``(first day, last day)`` of the lookahead holding ``day``: whole weeks from the week start.

	``first`` is the site's first weekday, as a weekday number or a System Settings name
	(:func:`project_planner.week_start`)."""
	start = pp.week_start(getdate(day), first)
	return start, start + datetime.timedelta(days=7 * lookahead_weeks(weeks) - 1)


def span_days(start, end):
	"""A task's size in working days (D7): the weekdays from ``start`` to ``end``, both counted.

	A task that sits on a weekend only is its calendar days, never 0."""
	first, last = engine._as_date(start), engine._as_date(end or start)
	if not first:
		return 0
	if not last or last < first:
		last = first
	days = list(engine.daterange(first, last))
	return sum(1 for day in days if day.weekday() < 5) or len(days)


def nearest_phase(name, parents, subjects, limit=engine.PHASE_CLIMB):
	"""The nearest ancestor of task ``name`` that is a phase (its subject has a phase number), or None.

	``parents`` maps a task to its ``parent_task``; ``subjects`` a task to its subject. The task itself
	is never its own phase: a phase group is not drawn as a task."""
	node, hops = parents.get(name), 0
	while node and hops <= limit:
		if phase_number(subjects.get(node)) is not None:
			return node
		node, hops = parents.get(node), hops + 1
	return None


def _card_sort(card):
	return (card.get("start") or "9999-99-99", str(card.get("subject") or "").casefold(), card.get("name") or "")


def _phase_sort(phase):
	number = phase.get("number")
	return (
		phase.get("name") is None,
		number if number is not None else 10**6,
		str(phase.get("subject") or "").casefold(),
	)


def lookahead_tree(cards, unscheduled, projects, phases, empty_projects=(), filters=None, groups=None):
	"""The rows the Lookahead draws, from plain values.

	``cards`` are the dated cards of the window and ``unscheduled`` the undated ones, each with
	``phase`` set (a phase's Task name or None). ``projects`` maps a project's name to its row from
	``get_planner`` (``title``, ``pm``, ``schedule``); ``phases`` maps a phase's Task name to its
	subject. ``empty_projects`` are the no-task projects (``name``, ``title``, ``pm``), drawn last.
	``filters`` may hold ``project``, ``pm`` and ``group``; ``groups`` maps a Planner Resource to its
	group, for the last.

	Returns ``[{name, title, pm, schedule, fountain_move, no_tasks, first, phases: [{name, subject,
	number, tasks, not_scheduled}], not_scheduled, counts}]``. Projects with work in the window come
	first, earliest bar first; then the ones with only unscheduled work; then the no-task ones.
	"""
	filters = filters or {}
	groups = groups or {}
	want_project, want_pm, want_group = (filters.get(key) or "" for key in ("project", "pm", "group"))

	def project_row(name):
		return projects.get(name) or {}

	def keep_project(name, row):
		if want_project and name != want_project:
			return False
		return not (want_pm and (row.get("pm") or "") != want_pm)

	def keep_card(card):
		if not want_group:
			return True
		return any(groups.get(member.get("resource")) == want_group for member in card.get("crew") or [])

	out = {}

	def entry(name):
		if name not in out:
			row = project_row(name)
			out[name] = {
				"name": name,
				"title": row.get("title") or name,
				"pm": row.get("pm"),
				"schedule": row.get("schedule"),
				"fountain_move": False,
				"no_tasks": False,
				"first": None,
				"phases": {},
				"not_scheduled": [],
				"counts": {"tasks": 0, "not_scheduled": 0},
			}
		return out[name]

	def phase_of(project, phase):
		bucket = project["phases"]
		if phase not in bucket:
			subject = phases.get(phase) if phase else None
			bucket[phase] = {
				"name": phase,
				"subject": subject or (phase if phase else OTHER_WORK),
				"number": phase_number(subject) if subject else None,
				"tasks": [],
				"not_scheduled": [],
			}
		return bucket[phase]

	for undated, rows in ((False, cards), (True, unscheduled)):
		for card in sorted(rows or [], key=_card_sort):
			name = card.get("project")
			if not name or not keep_project(name, project_row(name)) or not keep_card(card):
				continue
			project = entry(name)
			if card.get("fountain_move"):
				project["fountain_move"] = True
			phase = phase_of(project, card.get("phase") or None)
			if undated:
				phase["not_scheduled"].append(card.get("name"))
				project["not_scheduled"].append(card.get("name"))
				project["counts"]["not_scheduled"] += 1
			else:
				phase["tasks"].append(card.get("name"))
				project["counts"]["tasks"] += 1
				start = card.get("start")
				if start and (project["first"] is None or start < project["first"]):
					project["first"] = start

	listed = []
	for project in out.values():
		project["phases"] = sorted(project["phases"].values(), key=_phase_sort)
		listed.append(project)
	listed.sort(
		key=lambda p: (
			p["first"] is None,
			p["first"] or "",
			str(p["title"] or "").casefold(),
			p["name"],
		)
	)
	if not want_group:
		for row in empty_projects or ():
			name = row.get("name")
			if not name or name in out or not keep_project(name, row):
				continue
			listed.append(
				{
					"name": name,
					"title": row.get("title") or name,
					"pm": row.get("pm"),
					"schedule": project_row(name).get("schedule"),
					"fountain_move": False,
					"no_tasks": True,
					"first": None,
					"phases": [],
					"not_scheduled": [],
					"counts": {"tasks": 0, "not_scheduled": 0},
				}
			)
	return listed


# ---------------------------------------------------------------------- reads


def _ancestry(names):
	"""``(parents, subjects, baselines)`` for ``names`` and every ancestor up to the phase climb.

	One read per level for the whole set, as ``crew_availability.tasks_under_a_phase`` climbs. The
	first level also reads P3's baseline columns, once they exist."""
	names = sorted({n for n in names or () if n and not pp.is_draft_ref(n)})
	parents, subjects, baselines = {}, {}, {}
	if not names:
		return parents, subjects, baselines
	with_baseline = pp._has_schedule_columns()
	seen, frontier, first = set(), names, True
	for _level in range(engine.PHASE_CLIMB + 1):
		frontier = [n for n in frontier if n not in seen]
		if not frontier:
			break
		seen.update(frontier)
		fields = ["name", "subject", "parent_task"]
		if first and with_baseline:
			fields += [pp.BASELINE_START, pp.BASELINE_END]
		for row in frappe.get_all(
			"Task", filters={"name": ["in", frontier]}, fields=fields, limit_page_length=0
		):
			parents[row.get("name")] = row.get("parent_task") or None
			subjects[row.get("name")] = row.get("subject")
			if first and with_baseline and (row.get(pp.BASELINE_START) or row.get(pp.BASELINE_END)):
				start = engine._as_date(row.get(pp.BASELINE_START))
				end = engine._as_date(row.get(pp.BASELINE_END)) or start
				baselines[row.get("name")] = {
					"start": str(start or end),
					"end": str(end or start),
				}
		first = False
		frontier = sorted({parents.get(n) for n in frontier if parents.get(n)})
	return parents, subjects, baselines


def _empty_projects():
	"""The Active customer jobs with no task at all (the Needs tasks list). Never raises: the rest of
	the Lookahead is worth drawing without them."""
	try:
		from erpnext_enhancements.api import planner_actions

		return (planner_actions.get_projects_needing_tasks() or {}).get("projects") or []
	except Exception:
		frappe.log_error(title="Lookahead: the projects with no tasks could not be read")
		return []


@frappe.whitelist()
def get_lookahead(start=None, weeks=DEFAULT_WEEKS, project=None, pm=None, group=None, all_tasks=0, draft=0):
	"""The Lookahead: :func:`project_planner.get_planner` over whole weeks from the week holding
	``start`` (today when not given), plus ``"lookahead"``: ``{start, end, weeks, projects, needs_crew}``.
	See the module docstring."""
	first, last = lookahead_window(pp.given(start) or nowdate(), weeks, pp._site_first_weekday())
	result = pp.get_planner(str(first), str(last), draft=draft, all_tasks=all_tasks)
	cards = list(result.get("tasks") or [])
	undated = list(result.get("unscheduled") or [])
	parents, subjects, baselines = _ancestry(card.get("name") for card in cards + undated)
	for card in cards + undated:
		name = card.get("name")
		if name not in parents and card.get("parent_task"):
			# A drafted new task has no Task row yet; its card may name the phase it was filed under.
			parents[name] = card.get("parent_task")
		card["phase"] = nearest_phase(name, parents, subjects)
		card["baseline"] = baselines.get(name)
		card["span_days"] = span_days(card.get("start"), card.get("end")) if card.get("start") else 0
	missing = {card["phase"] for card in cards + undated if card["phase"] and card["phase"] not in subjects}
	if missing:
		_parents, more, _baselines = _ancestry(missing)
		subjects.update(more)
	phases = {card["phase"]: subjects.get(card["phase"]) for card in cards + undated if card["phase"]}
	projects = {row.get("name"): row for row in result.get("projects") or []}
	groups = {row.get("name"): row.get("group") for row in result.get("resources") or []}
	needs = set(result.get("needs_crew") or [])
	needs.update(card.get("name") for card in undated if pp.needs_crew(card, None))
	result["lookahead"] = {
		"start": str(first),
		"end": str(last),
		"weeks": lookahead_weeks(weeks),
		"choices": list(LOOKAHEAD_WEEKS),
		"projects": lookahead_tree(
			cards,
			undated,
			projects,
			phases,
			_empty_projects(),
			{"project": pp.given(project) or "", "pm": pp.given(pm) or "", "group": pp.given(group) or ""},
			groups,
		),
		"needs_crew": sorted(needs),
	}
	return result
