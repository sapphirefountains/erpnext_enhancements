# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A parent task's expected dates follow its subtasks (v1.784.0).

Nikolas, 2026-11-03, after the meeting with James and Clegg: "for the parent child tasks, make rigid
start and end dates based off the child tasks so a child task is always within the range of its
parent task."

**The rule.** A Task with subtasks has ``exp_start_date`` = the earliest ``exp_start_date`` among its
subtasks and ``exp_end_date`` = the latest ``exp_end_date`` among them. Where no subtask has a start
(or an end), the parent keeps its own: dropping an undated task under a dated one does not wipe the
dated one's plan. Every subtask counts, whatever its status: ERPNext refuses a child ending after
its parent whatever the child's status, so a canceled subtask left outside the range would still
block the next save. The rule walks up every ancestor, so a grandparent follows too.

**When it runs** (all wired in ``hooks.py`` ``doc_events["Task"]``):

* :func:`before_task_validate` (``before_validate``), on the subtask's save, **before** ERPNext's
  ``validate_parent_expected_end_date`` (erpnext 16.52.1, ``task.py``) reads the parent's stored end:
  the parent and its ancestors are widened to take in the dates this save carries, so the check never
  refuses a drag or a date change. The same hook, on a parent's own save, re-derives the parent's
  dates from its subtasks, so a Desk form that posts the fields back (they are read-only there, but a
  form posts what it loaded) cannot drift them.
* :func:`on_task_update` (``on_update``): the new parent and, after a move, the old one are recomputed
  from what the database now holds, which also shrinks a range a subtask left.
* :func:`after_task_delete` (``after_delete``): the deleted subtask's parent is recomputed.

**How it writes.** The ancestors are written with ``frappe.db.set_value`` (``update_modified`` off),
never ``doc.save()``: a save of the parent would run this same hook again (one flag per level to stop
it), ERPNext's whole ``validate`` against a grandparent not yet widened, ``populate_depends_on`` and
``reschedule_dependent_tasks``, and would bump ``modified`` under a Planner publish that saves the
parent in the same batch. Two columns change; the Scope tab's tree and the Project's own derived
window (``script_migrations.task.sync_project_dates_from_tasks``, which runs on the subtask's
``on_update`` and reads MIN/MAX over the project) read them back from the database. The direct
writers in ``project_dashboard.py`` (the tree's inline date editor, the Gantt) call
:func:`sync_task_ancestors` themselves, because ``frappe.db.set_value`` fires no hook. It is listed
before ``sync_project_dates_from_tasks`` in the ``on_update`` hooks, so the Project's MIN/MAX (which
counts parents too) sees the old parent already shrunk. For the same reason a parent whose end moves
later does **not** push the tasks that depend on it: ERPNext's ``reschedule_dependent_tasks`` runs
only in a Task's own ``on_update``, and a parent is written, not saved. A parent is a container, not
a predecessor; the subtask whose end moved pushes its own dependents when it is saved.

**What it never does.** It never raises out of a hook (a hook that raises refuses the save; a failure
is logged and ERPNext judges the task as it always did), except a failure of the database itself,
which ``project_window._database_failed`` recognises and which must end the request. It never
touches ERPNext's Project-window check (``validate_parent_project_dates``), which
``project_enhancements/project_window.py`` already serves. Reads are defensive (``getattr``,
``frappe.db.has_column``): doc_events fire during ERPNext's own test bootstrap too.

**The patch** ``patches/sync_parent_task_dates`` runs :func:`sync_all` once over every existing
parent on prod, bottom-up, logs instead of raising, and is safe to re-run (a second run changes
nothing). The pure :func:`derive_all` is what both it and the tests use.
"""

import datetime

import frappe

DATE_FIELDS = ("exp_start_date", "exp_end_date")
#: Ancestors walked at most; a task tree deeper than this is a cycle (NestedSet refuses one) or a fault.
MAX_DEPTH = 50
LOG_TITLE = "Task: parent dates could not follow the subtasks"


# ---------------------------------------------------------------------- pure


def when(value):
	"""``value`` as a datetime, or None. Accepts a datetime, a date, or the strings the Desk and the
	database hand over (``2026-11-03``, ``2026-11-03 08:00:00``, with or without microseconds)."""
	if not value:
		return None
	if isinstance(value, datetime.datetime):
		return value
	if isinstance(value, datetime.date):
		return datetime.datetime.combine(value, datetime.time())
	text = str(value).strip()[:19]
	try:
		return datetime.datetime.fromisoformat(text)
	except ValueError:
		return None


def window(dates):
	"""``(earliest start, latest end)`` over ``dates``, an iterable of ``(start, end)`` pairs in any of
	the forms :func:`when` reads; None on a side no pair has."""
	starts, ends = [], []
	for start, end in dates:
		start, end = when(start), when(end)
		if start:
			starts.append(start)
		if end:
			ends.append(end)
	return (min(starts) if starts else None, max(ends) if ends else None)


def derived(own, children_window):
	"""What a parent's ``(start, end)`` become: the subtasks' window where it has a value, else the
	parent's own. ``own`` in any form :func:`when` reads."""
	own_start, own_end = when(own[0]), when(own[1])
	start, end = children_window
	return (start or own_start, end or own_end)


def changes(stored, wanted):
	"""``{field: value}`` of what differs between a task's stored ``(start, end)`` and ``wanted``."""
	out = {}
	for field, old, new in zip(DATE_FIELDS, stored, wanted, strict=True):
		if when(old) != new:
			out[field] = new
	return out


def derive_all(rows):
	"""The rule over a whole set of tasks at once, pure: ``rows`` are dicts with ``name``,
	``parent_task``, ``exp_start_date`` and ``exp_end_date``; returns ``{name: (start, end)}`` for
	every task that has children among the rows, children derived first (so a grandparent sees the
	parent as the rule leaves it). A parent cycle is cut where it closes."""
	by_name = {}
	children = {}
	for row in rows:
		name = row.get("name")
		if not name:
			continue
		by_name[name] = row
		parent = row.get("parent_task")
		if parent:
			children.setdefault(parent, []).append(name)
	memo = {}

	def dates(name, stack):
		if name in memo:
			return memo[name]
		row = by_name.get(name) or {}
		own = (row.get("exp_start_date"), row.get("exp_end_date"))
		kids = children.get(name)
		if not kids or name in stack:
			out = (when(own[0]), when(own[1]))
			memo[name] = out
			return out
		stack.add(name)
		out = derived(own, window(dates(kid, stack) for kid in kids))
		stack.discard(name)
		memo[name] = out
		return out

	return {name: dates(name, set()) for name in children if name in by_name}


# ---------------------------------------------------------------------- reads and writes


def _ready():
	"""Whether the Task table carries the columns this module reads (ERPNext's own test bootstrap runs
	the hooks before every column exists)."""
	try:
		return all(frappe.db.has_column("Task", field) for field in ("parent_task", *DATE_FIELDS))
	except Exception:
		return False


def children_window(parent, override=None, exclude=None):
	"""``(earliest start, latest end)`` over ``parent``'s subtasks as the database holds them,
	with ``override`` (``{name: (start, end)}``) standing in for a subtask about to be saved, whether
	the database already has it under ``parent`` or not, and ``exclude`` (names) left out."""
	rows = frappe.get_all(
		"Task",
		filters={"parent_task": parent},
		fields=["name", *DATE_FIELDS],
		limit_page_length=0,
	)
	override = override or {}
	exclude = set(exclude or ())
	pairs = []
	seen = set()
	for row in rows:
		name = row.get("name")
		seen.add(name)
		if name in exclude:
			continue
		if name in override:
			pairs.append(override[name])
		else:
			pairs.append((row.get("exp_start_date"), row.get("exp_end_date")))
	for name, pair in override.items():
		if name not in seen and name not in exclude:
			pairs.append(pair)
	return window(pairs)


def has_children(task):
	"""Whether ``task`` has at least one subtask in the database."""
	if not task:
		return False
	# One row through get_all rather than db.exists with a filter: the bench-free planner suites
	# stub exists(doctype, dict) as True for any filter, which made every planner move look like a
	# parent's; get_all is answered from data by the framework and every stub alike.
	return bool(frappe.get_all("Task", filters={"parent_task": task}, pluck="name", limit_page_length=1))


def sync_task_ancestors(parent, override=None, exclude=None):
	"""Make ``parent`` and every ancestor above it follow their subtasks, bottom-up; ``override`` and
	``exclude`` apply to ``parent``'s own subtasks only (see :func:`children_window`). Writes the two
	date columns with ``frappe.db.set_value`` where they differ. Returns ``[(name, {field: value})]``
	for what changed."""
	changed = []
	name = parent
	depth = 0
	while name and depth < MAX_DEPTH:
		depth += 1
		stored = frappe.db.get_value("Task", name, ["parent_task", *DATE_FIELDS], as_dict=True)
		if not stored:
			break
		wanted = derived(
			(stored.get("exp_start_date"), stored.get("exp_end_date")),
			children_window(name, override, exclude),
		)
		diff = changes((stored.get("exp_start_date"), stored.get("exp_end_date")), wanted)
		if diff:
			frappe.db.set_value("Task", name, diff, update_modified=False)
			changed.append((name, diff))
		override, exclude = None, None
		name = stored.get("parent_task")
	return changed


def sync_all():
	"""Every parent task on the site made to follow its subtasks, bottom-up, in one pass of
	:func:`derive_all`. Returns ``(parents, written)``: how many tasks have subtasks, how many were
	changed. Idempotent: a second run writes nothing."""
	rows = frappe.get_all(
		"Task",
		fields=["name", "parent_task", *DATE_FIELDS],
		limit_page_length=0,
	)
	wanted = derive_all(rows)
	stored = {row.get("name"): row for row in rows}
	written = 0
	for name, dates in wanted.items():
		row = stored.get(name) or {}
		diff = changes((row.get("exp_start_date"), row.get("exp_end_date")), dates)
		if not diff:
			continue
		frappe.db.set_value("Task", name, diff, update_modified=False)
		written += 1
	return len(wanted), written


def refusal(task):
	"""Why a direct write of ``task``'s dates must be refused, or None: a task with subtasks takes its
	dates from them. The tree's inline editor and the Gantt ask this before ``frappe.db.set_value``."""
	if has_children(task):
		return frappe._(
			"The dates of {0} follow its subtasks (earliest start, latest end). Change the subtasks' dates instead."
		).format(task)
	return None


# ---------------------------------------------------------------------- hooks


def _is_task(doc):
	return getattr(doc, "doctype", None) == "Task" and getattr(doc, "name", None)


def _is_new(doc):
	is_new = getattr(doc, "is_new", None)
	try:
		return bool(is_new()) if callable(is_new) else False
	except Exception:
		return False


def _guard(doc, work):
	"""Run ``work()`` for a Task hook: never raise out of it, except a failure of the database itself
	(``project_window._database_failed``), which must end the request."""
	try:
		if not _is_task(doc) or not _ready():
			return None
		return work()
	except Exception as exc:
		from erpnext_enhancements.project_enhancements import project_window

		if project_window._database_failed(exc):
			raise
		try:
			frappe.log_error(title=LOG_TITLE, message=frappe.get_traceback())
		except Exception:
			pass
	return None


def before_task_validate(doc, method=None):
	"""Task ``before_validate``: a parent re-derives its own dates from its subtasks; then its
	ancestors are widened to take in the dates this save carries, before ERPNext's
	``validate_parent_expected_end_date`` reads the parent's stored end. Listed before
	``project_window.before_task_validate`` in ``hooks.py``, so the Project's window is widened for the
	dates this hook settles on."""

	def work():
		name = doc.name
		if not _is_new(doc) and has_children(name):
			own = (getattr(doc, "exp_start_date", None), getattr(doc, "exp_end_date", None))
			wanted = derived(own, children_window(name))
			for field, value in changes(own, wanted).items():
				setattr(doc, field, value)
		parent = getattr(doc, "parent_task", None)
		if parent and parent != name:
			override = {name: (getattr(doc, "exp_start_date", None), getattr(doc, "exp_end_date", None))}
			sync_task_ancestors(parent, override=override)

	return _guard(doc, work)


def _parent_before_save(doc):
	before = getattr(doc, "get_doc_before_save", None)
	try:
		previous = before() if callable(before) else None
	except Exception:
		previous = None
	return getattr(previous, "parent_task", None) if previous is not None else None


def on_task_update(doc, method=None):
	"""Task ``on_update``: the parent (and, after a move, the previous parent) and their ancestors
	follow what the database now holds."""

	def work():
		parent = getattr(doc, "parent_task", None)
		if parent and parent != doc.name:
			sync_task_ancestors(parent)
		previous = _parent_before_save(doc)
		if previous and previous != parent and previous != doc.name:
			sync_task_ancestors(previous)

	return _guard(doc, work)


def after_task_delete(doc, method=None):
	"""Task ``after_delete``: the deleted subtask's parent and its ancestors follow the subtasks left."""

	def work():
		parent = getattr(doc, "parent_task", None)
		if parent and parent != doc.name:
			sync_task_ancestors(parent)

	return _guard(doc, work)
