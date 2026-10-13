# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Phase templates and task bundles for the Build out grid (Project Planner T3, v1.608.0).

Nik, 2026-10-12: start a job's plan from DOC-0041's phases instead of typing it, and keep reusable
**bundles** of tasks ("Control panel design + build" and its subtasks) to add to any phase. Both are
ERPNext **Project Templates** whose tasks are template Tasks (``is_template``), so they are edited in
the Desk like any template; four Task fields and three Project Template fields (fixtures) carry what
ERPNext's model has no place for:

* ``Project Template.custom_template_kind``: *Phase template* or *Bundle* (blank: an ordinary ERPNext
  project template, which the planner never offers). ``custom_review_status``: *Needs review* until
  Nik has corrected a seeded one. ``custom_template_notes``: where it came from.
* A phase is a **group** template Task named ``Phase 300`` (the name a project's own phase group has,
  decision 2026-10-12; its descriptive name is its description), with ``custom_phase_number`` (300),
  ``custom_phase_runs_after`` ("500, 600": the phases it waits on) and ``custom_procurement_checklist``
  (one item per line: the materials, equipment and expenses to line up, which are never calendar
  tasks). :func:`phase_number` is THE reading of a phase name's number, which the build-out uses to
  find a project's existing phase.
* A task is a template Task under its phase with its default hours (``expected_time``), crew size,
  ``custom_template_line`` (Labor, Subcontract or Project management) and ``custom_cost_code`` (the
  corrected catalog's code: the seam T5 will use to put an estimate's hours in).

This module is pure (no frappe): it turns a template into **grid rows**, the shape
``get_copy_source`` already hands the grid (``{"subject", "hours", "phase", "crew_size", "needs":
["row:N"]}``), so the page loads them through the same code as "Copy from project".

The sequence rule (:func:`template_rows`):

* **Inside a phase or a bundle, tasks run in order** (each waits on the one before) unless the data
  says otherwise: when any task of the group has a dependency on another task of the group, the
  template's own dependencies are used as they are. A *Project management* task stays out of the
  chain: it runs alongside its phase.
* **Phases run after the phases they name** (``custom_phase_runs_after``). A phase's FIRST tasks (those
  waiting on nothing in their phase, the PM task among them) wait on the LAST tasks of the nearest
  picked phases before it (those nothing else in their phase waits on, PM aside). A phase that was not
  picked is walked through: pick 100, 300 and 700 and 300 waits on 100, and 700 on 300. With no
  ``runs_after`` data at all, each phase runs after the one above it. DOC-0041's seed: 100 > 200 >
  300 > 400, then 500 and 600 side by side, then 700 (which waits on both).
* **Phase 800 (Maintenance) is never offered**: maintenance contracts own it.
* **Bundles** are appended after the phase rows, each filed under the phase the planner chose, with
  their own order and nothing waiting across bundles.
"""

import html
import itertools
import json
import re
from html.parser import HTMLParser
from pathlib import Path

PHASE_TEMPLATE = "Phase template"
BUNDLE = "Bundle"
KINDS = (PHASE_TEMPLATE, BUNDLE)
NEEDS_REVIEW = "Needs review"
REVIEWED = "Reviewed"
LINE_LABOR = "Labor"
LINE_SUBCONTRACT = "Subcontract"
LINE_PM = "Project management"
LINES = (LINE_LABOR, LINE_SUBCONTRACT, LINE_PM)
#: Phases the planner never offers, whatever a template holds: maintenance contracts own 800.
EXCLUDED_PHASES = (800,)
#: The Build out grid holds this many rows (planner_actions.BUILD_OUT_MAX_ROWS).
MAX_ROWS = 100
SUBJECT_MAX = 140
#: A phase's procurement checklist: at most this many items, each at most this long.
CHECKLIST_MAX_LINES = 60
CHECKLIST_LINE_MAX = 200

#: The seed the patch reads (derived from DOC-0041 by scripts/derive_planner_templates.py).
SEED_PATH = Path(__file__).parent / "data" / "planner_templates.json"

#: The custom fields a template needs, by fixture name (fixtures/custom_field.json).
TEMPLATE_FIELDS = (
	"Project Template-custom_template_kind",
	"Project Template-custom_review_status",
	"Project Template-custom_template_notes",
	"Task-custom_planner_template_section",
	"Task-custom_template_line",
	"Task-custom_cost_code",
	"Task-custom_phase_number",
	"Task-custom_phase_runs_after",
	"Task-custom_procurement_checklist",
)


def clean(text):
	"""One line of text: whitespace runs to one space, trimmed."""
	return re.sub(r"\s+", " ", str(text if text is not None else "")).strip()


def number(value):
	"""A phase number as an int, or None (``"300"``, ``300.0`` and ``300`` are all 300)."""
	try:
		out = int(float(str(value).strip()))
	except (TypeError, ValueError):
		return None
	return out if out > 0 else None


#: A phase name that starts with a three-digit phase number: "Phase 300", "300 Rough-in",
#: "phase 300 - design". The same pattern as T4's ``planner_ai_draft.canonical_phase``.
PHASE_NUMBER = re.compile(r"^\s*(?:phase\s*)?(\d{3})\b", re.IGNORECASE)


def phase_number(text):
	"""The phase number a phase name starts with, as an int, or None.

	THE one reading of a phase's number (decision 2026-10-12, "Project task trees use Phase 100-700
	top-level groups"): a project's phases are named ``Phase 100`` ... ``Phase 700``, and a phase typed
	or loaded under any spelling that starts with its number is that phase. ``build_out_tasks`` matches a
	project's open phase by it, so a template never makes a second set of phase groups."""
	found = PHASE_NUMBER.match(str(text or ""))
	return int(found.group(1)) if found else None


def phase_name(value):
	"""The canonical name of phase ``value`` (a number): ``Phase 300``."""
	return f"Phase {int(value)}"


def phase_key(text):
	"""How two phase names are compared: by number when the name has one ("#300"), else by the name in
	any capitals."""
	value = phase_number(text)
	return f"#{value}" if value is not None else clean(text).casefold()


def phase_label(title, name=None):
	"""A phase as the dialog lists it: "Phase 100 — Full System Overview" (just the title without a
	descriptive name, or when the name only repeats it)."""
	title, name = clean(title), clean(name)
	if not name or name.casefold() == title.casefold():
		return title
	return f"{title} — {name}"


class _Text(HTMLParser):
	def __init__(self):
		super().__init__()
		self.parts = []

	def handle_data(self, data):
		self.parts.append(data)


def plain_text(value):
	"""The text of a (Text Editor) description, tags dropped, for a label. Read, never rewritten."""
	parser = _Text()
	try:
		parser.feed(str(value or ""))
		parser.close()
	except Exception:
		return clean(value)
	return clean(" ".join(parser.parts))


def parse_runs_after(text):
	"""``custom_phase_runs_after`` as phase numbers, in order, once each: "500, 600" -> [500, 600]."""
	out = []
	for token in re.split(r"[\s,;/+&]+", str(text or "")):
		value = number(token)
		if value is not None and value not in out:
			out.append(value)
	return out


def format_runs_after(numbers):
	return ", ".join(str(value) for value in numbers or [])


def checklist_lines(text):
	"""A procurement checklist (one item per line, bullets and boxes dropped) as its items."""
	out = []
	for raw in str(text or "").splitlines():
		line = clean(raw)
		line = re.sub(r"^(?:[-*•□☐]|\[[ xX]?\])\s*", "", line)
		if line:
			out.append(line[:CHECKLIST_LINE_MAX])
	return out[:CHECKLIST_MAX_LINES]


def checklist_html(lines, source=None):
	"""A phase's procurement checklist as the Task description the planner writes on a NEW phase.

	A Quill 2 task list (``<ol><li data-list="unchecked">``, what v16's Text Editor itself stores, and an
	attribute Frappe's sanitizer keeps), so each item can be ticked off on the phase's form. Every item
	is escaped. Empty when there is nothing to buy.
	"""
	items = [line for line in (clean(line)[:CHECKLIST_LINE_MAX] for line in lines or []) if line]
	if not items:
		return ""
	heading = "Procurement checklist"
	if source:
		heading += f" (from {clean(source)})"
	body = "".join(f'<li data-list="unchecked">{html.escape(item, quote=False)}</li>' for item in items)
	return f"<p><strong>{html.escape(heading, quote=False)}</strong></p><ol>{body}</ol>"


# ---------------------------------------------------------------------- the seed


def load_seed(path=None):
	"""The seed JSON, or ``{}`` when it is missing or unreadable (the patch then seeds nothing and says
	so: a patch that raises aborts ``bench migrate``, which is the deploy)."""
	try:
		value = json.loads(Path(path or SEED_PATH).read_text(encoding="utf-8"))
	except (OSError, ValueError):
		return {}
	return value if isinstance(value, dict) else {}


def seed_problems(seed):
	"""What is wrong with a seed, as sentences ([] when it can be seeded). Checked by the tests and by
	the patch, which seeds nothing of a template that has a problem."""
	problems = []
	names = set()
	for template in (seed or {}).get("templates") or []:
		name = clean(template.get("name"))
		where = name or "A template"
		if not name or len(name) > SUBJECT_MAX:
			problems.append(f"{where}: needs a name of at most {SUBJECT_MAX} characters.")
		if name in names:
			problems.append(f"{where}: two templates share the name.")
		names.add(name)
		if template.get("kind") not in KINDS:
			problems.append(f"{where}: kind must be one of {', '.join(KINDS)}.")
		groups = (
			template.get("phases")
			if template.get("kind") == PHASE_TEMPLATE
			else [{"tasks": template.get("tasks")}]
		)
		numbers = [phase.get("number") for phase in groups if phase.get("number") is not None]
		for phase in groups or []:
			label = f"{where}, {phase.get('title') or 'its tasks'}"
			if template.get("kind") == PHASE_TEMPLATE:
				if number(phase.get("number")) is None:
					problems.append(f"{label}: a phase needs a number.")
				elif clean(phase.get("title")) != phase_name(number(phase.get("number"))):
					problems.append(f"{label}: a phase is named 'Phase {phase.get('number')}'.")
				if number(phase.get("number")) in EXCLUDED_PHASES:
					problems.append(f"{label}: phase {phase.get('number')} is never seeded.")
				for other in phase.get("runs_after") or []:
					if other not in numbers:
						problems.append(f"{label}: runs after phase {other}, which is not in the template.")
			subjects = [clean(task.get("subject")) for task in phase.get("tasks") or []]
			if not subjects:
				problems.append(f"{label}: has no tasks.")
			for index, task in enumerate(phase.get("tasks") or []):
				subject = subjects[index]
				if not subject or len(subject) > SUBJECT_MAX:
					problems.append(
						f"{label}: task {index + 1} needs a subject of at most {SUBJECT_MAX} characters."
					)
				if subjects.count(subject) > 1:
					problems.append(f"{label}: '{subject}' is there twice.")
				hours = task.get("hours")
				if hours is not None and not (isinstance(hours, int | float) and 0 <= hours <= 400):
					problems.append(f"{label}: '{subject}' has hours that are not a number from 0 to 400.")
				if task.get("line") is not None and task.get("line") not in LINES:
					problems.append(f"{label}: '{subject}' has an unknown line '{task.get('line')}'.")
				for wait in task.get("waits_on") or []:
					if wait not in subjects[:index]:
						problems.append(
							f"{label}: '{subject}' waits on '{wait}', which is not a task above it."
						)
	return problems


def structure_from_seed(template):
	"""A seed template in the shape :func:`structure_from_tasks` reads from the Desk (keys are the
	subjects, there being no Task yet), so the tests run the seed through the same rows code."""
	if template.get("kind") == BUNDLE:
		return {"kind": BUNDLE, "tasks": [_seed_task(task) for task in template.get("tasks") or []]}
	phases = []
	for phase in template.get("phases") or []:
		title = clean(phase.get("title"))
		phases.append(
			{
				"id": str(number(phase.get("number"))),
				"number": number(phase.get("number")),
				"title": title,
				"label": phase_label(title, phase.get("name")),
				"runs_after": [str(value) for value in phase.get("runs_after") or []],
				"checklist": checklist_lines("\n".join(phase.get("checklist") or [])),
				"tasks": [_seed_task(task) for task in phase.get("tasks") or []],
			}
		)
	return {"kind": PHASE_TEMPLATE, "phases": phases, "loose": 0}


def _seed_task(task):
	return {
		"key": clean(task.get("subject")),
		"subject": clean(task.get("subject")),
		"hours": float(task.get("hours") or 0),
		"crew_size": int(task.get("crew_size") or 0),
		"cost_code": task.get("cost_code") or "",
		"line": task.get("line") or "",
		"waits_on": [clean(wait) for wait in task.get("waits_on") or []],
		"template_task": None,
	}


# ---------------------------------------------------------------------- a template read from the Desk


def structure_from_tasks(kind, tasks, depends):
	"""A Project Template's template Tasks (``tasks``, in the template's own order) and their ``Task
	Depends On`` rows (``depends``: ``parent`` waits on ``task``) as a phase template or a bundle.

	* Phase template: each group Task is a phase (number from ``custom_phase_number``, else the number
	  its subject starts with, else the group's name stands in for it); its leaf Tasks, in order, are its
	  tasks. A leaf under no phase of the template is counted in ``loose`` and left out.
	* Bundle: its leaf Tasks, in order (a group in a bundle is ignored).
	* ``runs_after``: the phase's own numbers; when NO phase has any, each runs after the one above.
	* A task's ``waits_on``: the other tasks of its own phase or bundle it depends on. ERPNext lists a
	  phase's sub-tasks among the PHASE's dependencies (``Task.populate_depends_on``); a group's rows are
	  never read.
	"""
	waits = {}
	for row in depends or []:
		parent, task = row.get("parent"), row.get("task")
		if parent and task and parent != task:
			waits.setdefault(parent, []).append(task)
	leaves = [task for task in tasks if not _flag(task.get("is_group"))]
	if kind == BUNDLE:
		names = {task.get("name") for task in leaves}
		return {
			"kind": BUNDLE,
			"tasks": [
				_task(task, [w for w in waits.get(task.get("name"), []) if w in names]) for task in leaves
			],
		}
	groups = [task for task in tasks if _flag(task.get("is_group"))]
	phases, ids = [], {}
	for group in groups:
		subject = clean(group.get("subject"))
		value = number(group.get("custom_phase_number")) or phase_number(subject)
		ident = str(value) if value else str(group.get("name"))
		if ident in ids.values():
			ident = str(group.get("name"))
		ids[group.get("name")] = ident
		members = [task for task in leaves if task.get("parent_task") == group.get("name")]
		names = {task.get("name") for task in members}
		# A numbered phase is always "Phase <number>" on a project (decision 2026-10-12); what else its
		# subject or description says is its descriptive name, shown beside it in the dialog.
		title = phase_name(value) if value else (subject or str(group.get("name")))
		rest = PHASE_NUMBER.sub("", subject, count=1).strip(" -:–—") if value else ""
		phases.append(
			{
				"id": ident,
				"number": value,
				"title": title,
				"label": phase_label(title, plain_text(group.get("description"))[:80] or rest),
				"runs_after": [str(n) for n in parse_runs_after(group.get("custom_phase_runs_after"))],
				"checklist": checklist_lines(group.get("custom_procurement_checklist")),
				"tasks": [
					_task(task, [w for w in waits.get(task.get("name"), []) if w in names])
					for task in members
				],
			}
		)
	if phases and not any(phase["runs_after"] for phase in phases):
		for before, phase in itertools.pairwise(phases):
			phase["runs_after"] = [before["id"]]
	loose = sum(1 for task in leaves if task.get("parent_task") not in ids)
	return {"kind": PHASE_TEMPLATE, "phases": phases, "loose": loose}


def _flag(value):
	try:
		return bool(int(float(value or 0)))
	except (TypeError, ValueError):
		return False


def _task(row, waits_on):
	hours = row.get("expected_time")
	size = row.get("custom_crew_size")
	try:
		hours = round(float(hours or 0), 2)
	except (TypeError, ValueError):
		hours = 0.0
	try:
		size = int(float(size or 0))
	except (TypeError, ValueError):
		size = 0
	return {
		"key": row.get("name"),
		"subject": clean(row.get("subject"))[:SUBJECT_MAX],
		"hours": hours,
		"crew_size": size,
		"cost_code": clean(row.get("custom_cost_code")),
		"line": clean(row.get("custom_template_line")),
		"waits_on": list(waits_on),
		"template_task": row.get("name"),
	}


def offered_phases(structure):
	"""The phases a planner may tick: ``[{"id", "number", "title", "label", "tasks", "checklist"}]``,
	never 800. ``title`` is the phase's name on a project ("Phase 100"); ``label`` adds its descriptive
	name for the dialog ("Phase 100 — Full System Overview")."""
	return [
		{
			"id": phase["id"],
			"number": phase["number"],
			"title": phase["title"],
			"label": phase.get("label") or phase["title"],
			"tasks": len(phase["tasks"]),
			"checklist": len(phase["checklist"]),
		}
		for phase in (structure or {}).get("phases") or []
		if phase["number"] not in EXCLUDED_PHASES
	]


# ---------------------------------------------------------------------- template -> grid rows


def group_waits(tasks):
	"""``{key: [keys]}``: what each task of one phase or bundle waits on inside it.

	The template's own dependencies when any task has one; otherwise the tasks run in order, each after
	the one before, a Project management task left out of the chain (it runs alongside).
	"""
	keys = [task["key"] for task in tasks]
	data = {
		task["key"]: [w for w in task.get("waits_on") or [] if w in keys and w != task["key"]]
		for task in tasks
	}
	if any(data.values()):
		return data
	chain = [task["key"] for task in tasks if task.get("line") != LINE_PM]
	for before, key in itertools.pairwise(chain):
		data[key] = [before]
	return data


def _lasts(tasks, waits):
	"""The tasks nothing else in their group waits on: PM aside, unless the group is PM alone."""
	waited = {other for key in waits for other in waits[key]}
	work = [task["key"] for task in tasks if task.get("line") != LINE_PM and task["key"] not in waited]
	if work:
		return work
	ends = [task["key"] for task in tasks if task["key"] not in waited]
	return ends or [task["key"] for task in tasks]


def nearest_picked(phase_id, by_id, picked):
	"""The picked phases ``phase_id`` waits on: its own ``runs_after``, walking through any that were
	not picked (or are never offered) to theirs. In order, once each; a loop in the data stops."""
	out, seen = [], {phase_id}
	stack = list(reversed(by_id[phase_id]["runs_after"])) if phase_id in by_id else []
	while stack:
		other = stack.pop()
		if other in seen or other not in by_id:
			continue
		seen.add(other)
		if other in picked:
			if other not in out:
				out.append(other)
		else:
			stack.extend(reversed(by_id[other]["runs_after"]))
	return out


def template_rows(structure=None, picked=None, bundles=()):
	"""Grid rows for the picked phases of a phase template, then each bundle under its phase.

	``picked``: the phase ids to use (None: every phase offered); 800 is dropped whatever is asked.
	``bundles``: ``[(bundle structure, phase title), ...]``. Returns ``{"rows": [{"subject", "hours",
	"phase", "crew_size", "needs": ["row:N"], "cost_code", "line", "template_task"}], "checklists":
	{phase title: [items]}, "phases": [titles used]}``; ``row:N`` is the N-th row returned, from 1.
	"""
	rows, places = [], {}
	pending = []
	phases = [p for p in ((structure or {}).get("phases") or []) if p["number"] not in EXCLUDED_PHASES]
	by_id = {phase["id"]: phase for phase in (structure or {}).get("phases") or []}
	wanted = {str(value) for value in picked} if picked is not None else {phase["id"] for phase in phases}
	chosen = [phase for phase in phases if phase["id"] in wanted]
	chosen_ids = {phase["id"] for phase in chosen}
	waits_of, lasts_of = {}, {}
	for phase in chosen:
		waits = group_waits(phase["tasks"])
		waits_of[phase["id"]] = waits
		lasts_of[phase["id"]] = _lasts(phase["tasks"], waits)
		for task in phase["tasks"]:
			places[(phase["id"], task["key"])] = len(rows) + 1
			rows.append(_row(task, phase["title"]))
			pending.append((phase["id"], task))
	for (phase_id, task), row in zip(pending, rows, strict=True):
		own = waits_of[phase_id][task["key"]]
		needs = {places[(phase_id, key)] for key in own}
		if not own:
			for other in nearest_picked(phase_id, by_id, chosen_ids):
				needs.update(places[(other, key)] for key in lasts_of[other])
		row["needs"] = [f"row:{place}" for place in sorted(needs)]
	for bundle, phase in bundles or ():
		tasks = (bundle or {}).get("tasks") or []
		waits = group_waits(tasks)
		start = len(rows)
		local = {task["key"]: start + index + 1 for index, task in enumerate(tasks)}
		for task in tasks:
			row = _row(task, clean(phase)[:SUBJECT_MAX])
			row["needs"] = [f"row:{place}" for place in sorted(local[key] for key in waits[task["key"]])]
			rows.append(row)
	checklists = {phase["title"]: list(phase["checklist"]) for phase in chosen if phase["checklist"]}
	return {"rows": rows, "checklists": checklists, "phases": [phase["title"] for phase in chosen]}


def _row(task, phase):
	return {
		"subject": task["subject"],
		"hours": task["hours"],
		"phase": phase,
		"crew_size": task["crew_size"],
		"needs": [],
		"cost_code": task["cost_code"],
		"line": task["line"],
		"template_task": task["template_task"],
	}


def apply_estimate_hours(rows, hours_by_code):
	"""T5's seam: an estimate's hours by cost code replace a row's template hours. Nothing calls it
	with anything yet (``{}``: every row keeps its template hours); a code the estimate does not price
	keeps them too. Each row says where its hours came from (``hours_from``)."""
	for row in rows:
		code = row.get("cost_code")
		if code and code in (hours_by_code or {}):
			row["hours"] = round(float(hours_by_code[code] or 0), 2)
			row["hours_from"] = "estimate"
		else:
			row["hours_from"] = "template"
	return rows
