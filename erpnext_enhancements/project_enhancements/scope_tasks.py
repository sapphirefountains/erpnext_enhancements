# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Scope tab's task actions, the pure part (v1.777.0; Nik, 2026-11-03).

Nik: "I need the Scope tab to be rethought. The Tasks Tree should probably be at the top, and I'd
like a way to import entire templates of tasks via the Tasks Tree. Make the Add a new task button
have a good flow without leaving the project page." And later the same day: "Also a way to mass
delete tasks from the Project page of the Scope tab."

This module imports no ``frappe`` so ``tests/test_scope_tasks.py`` can run it without a bench. The
endpoints that read and write are ``api/scope_tasks.py``; what they decide is here:

* :func:`plan_import` turns a Project Template's tasks into a plan for one project: which template
  tasks become new Tasks, under which parent (an existing Task of the project, or another new one),
  which are **skipped** because a Task with the same subject already sits under that parent, and
  what each new Task waits on, with a dependency on a skipped task pointing at the Task that was
  already there. The template's own structure (parent/child, dependencies, order) is kept; the
  template's roots go under the node the user picked, or the project's root.
* :func:`delete_order` is the mass delete's list: the picked tasks plus every task under them,
  deepest first, because ERPNext refuses to delete a Task that still has children.
* :func:`preview_rows` is what the "Add from template" dialog shows before anything is written.

Things to know, some of which look like bugs:

* **A subject matches by its words, not its bytes.** ``Project Launch``, ``project launch`` and
  ``Project  Launch`` are the same task (:func:`subject_key`): the duplicate the rule exists to stop
  is the one a person types from memory.
* **A canceled Task never blocks an import.** A ``Canceled`` (or ERPNext's ``Cancelled``) Task with
  the right subject under the right parent is history, not the task; the template's one is added
  beside it.
* **A skipped task is still a parent.** When the template's ``Project Launch`` group is already on
  the job, its template children are matched against the children of the *existing* group, so a
  second import adds only what is missing under it instead of a second group.
* **A task whose template parent is not in the template is a root**, as ERPNext's own
  ``check_for_parent_tasks`` treats it (it maps parents only among the template's tasks).
* **Order is the template's.** Within a parent, new tasks keep the template's row order
  (``position``), and the endpoint places them after the siblings already there.
"""

import re

#: Statuses that mean "this task is gone", so it neither blocks an import nor counts as a sibling.
CANCELED = ("Canceled", "Cancelled")


def clean(text):
	"""One line of text: whitespace runs to one space, trimmed."""
	return re.sub(r"\s+", " ", str(text if text is not None else "")).strip()


def subject_key(text):
	"""How two subjects are compared: cleaned, in any capitals."""
	return clean(text).casefold()


def template_order(tasks):
	"""``tasks`` (rows with ``name``, ``parent_task``, in the template's row order) parent before
	child, children in their own row order. A task whose parent is not in the template is a root;
	a parent cycle (impossible through the Desk) is appended rather than lost."""
	by_name = {row["name"]: row for row in tasks}
	children = {}
	roots = []
	for row in tasks:
		parent = row.get("parent_task")
		if parent and parent in by_name and parent != row["name"]:
			children.setdefault(parent, []).append(row)
		else:
			roots.append(row)
	out, seen = [], set()

	def walk(nodes):
		for row in nodes:
			if row["name"] in seen:
				continue
			seen.add(row["name"])
			out.append(row)
			walk(children.get(row["name"], []))

	walk(roots)
	for row in tasks:
		if row["name"] not in seen:
			seen.add(row["name"])
			out.append(row)
	return out


def existing_ref(name):
	return {"kind": "existing", "name": name}


def new_ref(template_name):
	return {"kind": "new", "name": template_name}


def _existing_index(existing):
	"""``{parent or None: {subject key: existing task name}}``, canceled tasks left out; the first
	of two same-subject siblings wins."""
	index = {}
	for row in existing or []:
		if (row.get("status") or "") in CANCELED:
			continue
		siblings = index.setdefault(row.get("parent_task") or None, {})
		siblings.setdefault(subject_key(row.get("subject")), row["name"])
	return index


def plan_import(template_tasks, depends, existing, parent=None):
	"""The plan for adding ``template_tasks`` to a project.

	* ``template_tasks``: the template's Tasks (``name``, ``subject``, ``parent_task``, ...) in the
	  template's row order.
	* ``depends``: ``Task Depends On`` rows of those tasks as ``{"parent": task, "task": waits on}``.
	* ``existing``: the project's Tasks (``name``, ``subject``, ``parent_task``, ``status``).
	* ``parent``: the existing Task the template's roots go under, or None for the project's root.

	Returns ``{"create": [{template, subject, parent: ref or None, position}], "skip": [{template,
	subject, existing}], "deps": {template: [ref, ...]}, "resolved": {template: ref}}``. A ``ref`` is
	:func:`existing_ref` (a Task already on the project) or :func:`new_ref` (a task this plan
	creates, by its template name). ``position`` counts new tasks under the same parent from 0, in
	template order. ``deps`` has an entry only for created tasks and never a self-dependency.
	"""
	ordered = template_order(template_tasks)
	in_template = {row["name"] for row in ordered}
	index = _existing_index(existing)
	resolved, create, skip = {}, [], []
	positions = {}
	for row in ordered:
		template_parent = row.get("parent_task")
		if template_parent and template_parent in in_template and template_parent != row["name"]:
			parent_ref = resolved[template_parent]
		else:
			parent_ref = existing_ref(parent) if parent else None
		match = None
		if parent_ref is None or parent_ref["kind"] == "existing":
			under = parent_ref["name"] if parent_ref else None
			match = index.get(under, {}).get(subject_key(row.get("subject")))
		if match:
			resolved[row["name"]] = existing_ref(match)
			skip.append({"template": row["name"], "subject": row.get("subject"), "existing": match})
			continue
		resolved[row["name"]] = new_ref(row["name"])
		key = (parent_ref["kind"], parent_ref["name"]) if parent_ref else None
		position = positions.get(key, 0)
		positions[key] = position + 1
		create.append(
			{
				"template": row["name"],
				"subject": row.get("subject"),
				"parent": parent_ref,
				"position": position,
			}
		)

	waits = {}
	for dep in depends or []:
		task, on = dep.get("parent"), dep.get("task")
		if task and on and on != task:
			waits.setdefault(task, []).append(on)
	deps = {}
	for entry in create:
		refs = []
		for on in waits.get(entry["template"], []):
			ref = resolved.get(on)
			if ref and ref not in refs:
				refs.append(ref)
		if refs:
			deps[entry["template"]] = refs
	return {"create": create, "skip": skip, "deps": deps, "resolved": resolved}


def next_position(existing, parent=None):
	"""Where a new task lands among the Tasks already under ``parent``: one past the highest
	``custom_subtask_order`` there (1 when there is none)."""
	highest = 0.0
	for row in existing or []:
		if (row.get("parent_task") or None) != (parent or None):
			continue
		try:
			highest = max(highest, float(row.get("custom_subtask_order") or 0))
		except (TypeError, ValueError):
			continue
	return highest + 1


def delete_order(tasks, selected):
	"""The names to delete, deepest first: ``selected`` plus every task under them. ``tasks`` are the
	project's Tasks (``name``, ``parent_task``). A name that is not on the project is dropped, and
	a task is listed once however many of its ancestors were picked."""
	known = {row["name"] for row in tasks or []}
	children = {}
	for row in tasks or []:
		parent = row.get("parent_task")
		if parent and parent != row["name"]:
			children.setdefault(parent, []).append(row["name"])
	depth = {}
	queue = [(name, 0) for name in selected or [] if name in known]
	while queue:
		name, level = queue.pop(0)
		if name in depth and depth[name] >= level:
			continue
		depth[name] = level
		queue.extend((child, level + 1) for child in children.get(name, []))
	return [name for name, _level in sorted(depth.items(), key=lambda item: (-item[1], item[0]))]


def preview_rows(template_tasks, depends):
	"""The template's tasks as the dialog lists them: ``[{name, subject, level, is_group, waits:
	[subjects], start, duration, hours}]``, parent before child."""
	ordered = template_order(template_tasks)
	in_template = {row["name"]: row for row in ordered}
	level = {}
	waits = {}
	for dep in depends or []:
		on = in_template.get(dep.get("task"))
		if on and dep.get("parent") in in_template and on["name"] != dep.get("parent"):
			waits.setdefault(dep["parent"], []).append(on.get("subject") or on["name"])
	out = []
	for row in ordered:
		parent = row.get("parent_task")
		level[row["name"]] = level[parent] + 1 if parent in level and parent != row["name"] else 0
		out.append(
			{
				"name": row["name"],
				"subject": row.get("subject") or row["name"],
				"level": level[row["name"]],
				"is_group": 1 if row.get("is_group") else 0,
				"waits": waits.get(row["name"], []),
				"start": int(row.get("start") or 0),
				"duration": int(row.get("duration") or 0),
				"hours": float(row.get("expected_time") or 0),
			}
		)
	return out
