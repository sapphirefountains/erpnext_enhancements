"""The Scope tab's task actions (v1.777.0; Nik, 2026-11-03): a Project Template's tasks into the
Tasks Tree, a new task from an in-page dialog, and a mass delete, all without leaving the Project
form.

The rules (which template task becomes which Task, under what, what is skipped, what is deleted
first) are the pure ``project_enhancements/scope_tasks.py``; this module reads, checks permissions
and writes.

* :func:`get_template_catalog` lists the enabled Project Templates with a preview of each one's tasks
  (hierarchy, dependencies, days, hours), plus the project's dates for the dialog's default start.
* :func:`import_template` creates a template's tasks on the project in ONE request: the template's
  parent/child structure, ``depends_on`` chains and durations are kept, the roots go under the node
  the user picked (or the project's root), and a task that already exists under its parent with the
  same subject is skipped and reported rather than duplicated. All or nothing: a Task ERPNext refuses
  (a date outside the project's window, a child ending after its parent) fails the request and
  nothing is written.
* :func:`add_task` is the "Add task" dialog's save: subject, parent, group flag, priority, type, dates,
  hours, assignee and description, through the Task controller as the caller.
* :func:`move_tasks` (v1.787.0) is the tree's drag-and-drop and *Move under…* save: the rows as the
  tree holds them after the drop, only the ones whose parent or position changed saved, a leaf
  dropped on made a group first, and ERPNext's refusal returned (never thrown) so the tree can show
  it and put itself back. All or nothing.
* :func:`delete_tasks` deletes the picked tasks and everything under them, deepest first, each under
  a savepoint, and reports the ones it had to keep (linked from a timesheet, locked, not permitted)
  by name and reason instead of failing the lot. :func:`delete_preview` counts first, so the confirm
  can say how many subtasks the pick really covers.

Things this module is careful about, some of which look like bugs:

* **ERPNext's template-to-task code is called, not copied.** ``Project.create_task_from_template``
  (erpnext 16.52.1) builds each Task: subject, description, weight, type, group flag, color,
  priority, ``template_task``, and the dates from the template's *Begin On* / *Duration* days off
  the project's expected start, moved off holidays. It reads the start from the Project document it
  is called on, so the chosen start date is set on an **in-memory** copy of the Project that is never
  saved: the project's own dates do not change. The app's ``Task.before_insert`` hook
  (``task_templates.copy_template_planning``) then carries the template's hours, crew size and
  qualifications across, because ``template_task`` is set.
* **Linking is a second save, like ERPNext's own.** ``create_task_from_template`` inserts every task
  unlinked; ERPNext's ``dependency_mapping`` then sets ``parent_task`` and ``depends_on`` with a
  second save. The same two steps here, except that a parent or a dependency may be a Task that was
  already on the project (a skipped one, or the node the user picked), which ERPNext's mapping, built
  for an empty project, never has to resolve.
* **A parent must be a group.** ERPNext refuses a child under a Task whose *Is Parent* box is off
  (``validate_parent_is_group``). The node the user picked, or a skipped task that is about to get
  children, is made a group first, by a normal save, and the answer names it.
* **The Project's permission is the gate for reads, the Task controller's for writes.** Project
  Templates and template Tasks are read with ``get_all`` after a *write* check on the project
  (Projects User holds only *select* on Project Template); every Task is inserted, saved and deleted
  as the caller, so a role that may not create or delete Tasks is refused by Frappe itself.
* **Mass delete never leaves a message queued.** A refused delete is a ``frappe.throw`` from ERPNext
  ("Child Task exists ...", a timesheet link); catching it does not unqueue its message, and a 200
  that still carries ``_server_messages`` pops a red modal over the summary. Each delete runs with
  ``frappe.flags.mute_messages`` on, and the reason reaches the user through the answer instead.
* **Deepest first, each under a savepoint.** ERPNext refuses to delete a Task with children, so a
  picked group is deleted after its subtree; a refusal rolls back only that one task, and the parent
  of a kept task is then kept too, with the child's reason in its own.
* **A dependency between two picked tasks is unlinked first.** Frappe's link check counts a ``Task
  Depends On`` row in another Task as a link, and ERPNext's ``on_trash`` clears only the parent's
  rows, so an imported chain would refuse every task another picked task waits on. Inside each task's
  savepoint the rows of the *other picked* tasks that name it are deleted before the Task is; a task
  outside the pick that waits on it keeps its row, and the delete is refused with Frappe's reason.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from erpnext_enhancements.project_enhancements import scope_tasks as rules

TEMPLATE_DOCTYPE = "Project Template"
TEMPLATE_ROW = "Project Template Task"
#: Most tasks one import or one delete may touch; the whole tree view is a few dozen rows.
MAX_IMPORT = 200
MAX_DELETE = 200
#: The Task fields the "Add task" dialog may set, and nothing else reaches the document.
ADD_FIELDS = (
	"subject",
	"parent_task",
	"is_group",
	"priority",
	"type",
	"exp_start_date",
	"exp_end_date",
	"expected_time",
	"description",
)
PRIORITIES = ("Low", "Medium", "High", "Urgent")
TEMPLATE_TASK_FIELDS = (
	"name",
	"subject",
	"is_group",
	"parent_task",
	"start",
	"duration",
	"expected_time",
	"priority",
)
ORDER_FIELD = "custom_subtask_order"
LOG_TITLE = "Scope tab tasks"


# ---------------------------------------------------------------------- helpers


def _text(value):
	return value.strip() if isinstance(value, str) else ""


def _require_project(project, ptype="write"):
	"""The project's name once it exists and the caller holds ``ptype`` on it."""
	project = _text(project)
	if not project or not frappe.db.exists("Project", project):
		frappe.throw(_("Pick a project."))
	if not frappe.has_permission("Project", ptype, doc=project):
		frappe.throw(
			_("You are not permitted to change the tasks of {0}.").format(project), frappe.PermissionError
		)
	return project


def _has_order_column():
	try:
		return bool(frappe.db.has_column("Task", ORDER_FIELD))
	except Exception:
		return False


def _project_tasks(project):
	"""Every Task of ``project`` (``get_all``: a task the caller may not read still blocks a duplicate)."""
	fields = ["name", "subject", "parent_task", "status", "is_group"]
	if _has_order_column():
		fields.append(ORDER_FIELD)
	return frappe.get_all("Task", filters={"project": project}, fields=fields, limit_page_length=0)


def _parse_list(value):
	if value is None:
		return []
	if isinstance(value, str):
		value = value.strip()
		if not value:
			return []
		try:
			value = json.loads(value)
		except ValueError:
			frappe.throw(_("The task list could not be read. Reload the page and try again."))
	if not isinstance(value, list | tuple):
		frappe.throw(_("Expected a list of tasks."))
	return [_text(item) for item in value if _text(item)]


def _parse_dict(value):
	if value is None:
		return {}
	if isinstance(value, str):
		try:
			value = json.loads(value) if value.strip() else {}
		except ValueError:
			frappe.throw(_("The task could not be read. Reload the page and try again."))
	if not isinstance(value, dict):
		frappe.throw(_("Expected the task's values."))
	return value


def _require_project_task(project, task, label):
	"""``task`` once it is a Task of ``project``; blank is None."""
	task = _text(task)
	if not task:
		return None
	owner = frappe.db.get_value("Task", task, "project")
	if owner is None:
		frappe.throw(_("{0} {1} does not exist.").format(label, task))
	if owner != project:
		frappe.throw(_("{0} {1} is not on {2}.").format(label, task, project))
	return task


def _make_group(task):
	"""Tick *Is Parent* on ``task`` so it can hold children; a normal save, as the caller."""
	doc = frappe.get_doc("Task", task)
	if cint(doc.get("is_group")):
		return False
	doc.is_group = 1
	doc.save()
	return True


def _template_links(names):
	return frappe.get_all(
		TEMPLATE_ROW,
		filters={"parenttype": TEMPLATE_DOCTYPE, "parent": ["in", list(names)]},
		fields=["parent", "task", "idx"],
		order_by="parent asc, idx asc",
		limit_page_length=0,
	)


def _template_tasks(links):
	"""``{template name: [task rows in row order]}`` and the ``Task Depends On`` rows among them."""
	task_names = sorted({row.get("task") for row in links if row.get("task")})
	if not task_names:
		return {}, []
	tasks = {
		row["name"]: row
		for row in frappe.get_all(
			"Task",
			filters={"name": ["in", task_names], "is_template": 1},
			fields=list(TEMPLATE_TASK_FIELDS),
			limit_page_length=0,
		)
	}
	depends = frappe.get_all(
		"Task Depends On",
		filters={"parenttype": "Task", "parent": ["in", sorted(tasks)]},
		fields=["parent", "task"],
		limit_page_length=0,
	)
	by_template = {}
	for row in sorted(links, key=lambda r: (r.get("parent"), cint(r.get("idx")))):
		task = tasks.get(row.get("task"))
		rows = by_template.setdefault(row.get("parent"), [])
		if task and task not in rows:
			rows.append(task)
	return by_template, depends


# ---------------------------------------------------------------------- templates


@frappe.whitelist()
def get_template_catalog(project):
	"""The "Add from template" dialog's catalog for ``project``.

	``{"templates": [{name, project_type, count, rows: [preview rows]}], "project_type",
	"expected_start_date", "expected_end_date", "today"}``: every enabled Project Template, the ones
	whose project type matches the project's first, each with :func:`rules.preview_rows` of its
	tasks. Read-only; needs *write* on the project, since that is what the import needs.
	"""
	project = _require_project(project, "write")
	# `or (...)`: get_value answers None, not a row of Nones, for a row that vanished between the
	# permission check and this read (tests/test_get_value_unpacking.py guards the spelling).
	project_type, start, end = frappe.db.get_value(
		"Project", project, ["project_type", "expected_start_date", "expected_end_date"]
	) or (None, None, None)
	templates = frappe.get_all(
		TEMPLATE_DOCTYPE,
		filters={"disabled": 0},
		fields=["name", "project_type"],
		order_by="name asc",
		limit_page_length=0,
	)
	links = _template_links([row["name"] for row in templates]) if templates else []
	by_template, depends = _template_tasks(links)
	out = []
	for row in templates:
		tasks = by_template.get(row["name"], [])
		names = {task["name"] for task in tasks}
		own_depends = [dep for dep in depends if dep.get("parent") in names]
		out.append(
			{
				"name": row["name"],
				"project_type": row.get("project_type") or "",
				"count": len(tasks),
				"rows": rules.preview_rows(tasks, own_depends),
			}
		)
	out.sort(
		key=lambda entry: (0 if project_type and entry["project_type"] == project_type else 1, entry["name"])
	)
	return {
		"templates": out,
		"project_type": project_type or "",
		"expected_start_date": str(start) if start else None,
		"expected_end_date": str(end) if end else None,
		"today": nowdate(),
	}


def _ref_name(ref, made):
	"""The Task name a plan ref points at once the new ones exist."""
	if not ref:
		return None
	return made[ref["name"]] if ref["kind"] == "new" else ref["name"]


@frappe.whitelist()
def import_template(project, template, parent_task=None, start_date=None):
	"""Add ``template``'s tasks to ``project`` under ``parent_task`` (blank: the project's root),
	dated from ``start_date`` (blank: the project's expected start, else today).

	Returns ``{"created": [{name, subject, parent_task}], "skipped": [{subject, existing}],
	"converted": [task names made groups], "count", "start_date"}``. Nothing is written when every
	template task is already there. See the module docstring for what is kept and what is refused.
	"""
	project = _require_project(project, "write")
	template = _text(template)
	if not template or not frappe.db.exists(TEMPLATE_DOCTYPE, template):
		frappe.throw(_("Pick a project template."))
	if cint(frappe.db.get_value(TEMPLATE_DOCTYPE, template, "disabled")):
		frappe.throw(_("Project Template {0} is disabled.").format(template))
	parent = _require_project_task(project, parent_task, _("Parent task"))
	by_template, depends = _template_tasks(_template_links([template]))
	template_tasks = by_template.get(template, [])
	if not template_tasks:
		frappe.throw(_("Project Template {0} has no tasks.").format(template))
	existing = _project_tasks(project)
	plan = rules.plan_import(template_tasks, depends, existing, parent)
	skipped = [{"subject": row["subject"], "existing": row["existing"]} for row in plan["skip"]]
	if not plan["create"]:
		return {"created": [], "skipped": skipped, "converted": [], "count": 0, "start_date": None}
	if len(plan["create"]) > MAX_IMPORT:
		frappe.throw(
			_("That template has {0} tasks; add {1} or fewer at once.").format(
				len(plan["create"]), MAX_IMPORT
			)
		)

	project_doc = frappe.get_doc("Project", project)
	start = (
		getdate(start_date) if _text(start_date) else (project_doc.expected_start_date or getdate(nowdate()))
	)
	# In memory only: create_task_from_template dates each task off this field and the document is
	# never saved, so the project's own dates stay as they were.
	project_doc.expected_start_date = start

	existing_by_name = {row["name"]: row for row in existing}
	has_order = _has_order_column()
	converted, made, created = [], {}, []
	savepoint = "scope_import_template"
	frappe.db.savepoint(savepoint)
	try:
		# Every parent that is about to hold children must be a group: the picked node and any
		# skipped task whose template children are new.
		for entry in plan["create"]:
			ref = entry["parent"]
			if ref and ref["kind"] == "existing" and ref["name"] not in converted:
				if not cint((existing_by_name.get(ref["name"]) or {}).get("is_group")):
					if _make_group(ref["name"]):
						converted.append(ref["name"])

		for entry in plan["create"]:
			template_task = frappe.get_doc("Task", entry["template"])
			doc = project_doc.create_task_from_template(template_task)
			made[entry["template"]] = doc.name

		# The second save, as ERPNext's dependency_mapping does it: parent, dependencies, position.
		bases = {}
		for entry in plan["create"]:
			ref = entry["parent"]
			doc = frappe.get_doc("Task", made[entry["template"]])
			parent_name = _ref_name(ref, made)
			doc.parent_task = parent_name
			for dep in plan["deps"].get(entry["template"], []):
				on = _ref_name(dep, made)
				if on and on not in [row.task for row in doc.get("depends_on") or []]:
					doc.append("depends_on", {"task": on})
			if has_order:
				# New roots and children of an existing parent land after the siblings already
				# there; children of a parent made by this import start at 1.
				key = (ref["kind"] if ref else "root", parent_name)
				if key not in bases:
					bases[key] = (
						1 if (ref and ref["kind"] == "new") else rules.next_position(existing, parent_name)
					)
				doc.set(ORDER_FIELD, bases[key] + entry["position"])
			doc.save()
			created.append({"name": doc.name, "subject": doc.subject, "parent_task": parent_name})
	except Exception:
		# Back to before the first write of this import, and nothing earlier in the request.
		frappe.db.rollback(save_point=savepoint)
		raise

	return {
		"created": created,
		"skipped": skipped,
		"converted": converted,
		"count": len(created),
		"start_date": str(start),
	}


# ---------------------------------------------------------------------- add one


def clean_add_values(values):
	"""The "Add task" dialog's values as the Task gets them: only :data:`ADD_FIELDS`, a required
	subject, a known priority, numbers as numbers. Raises ``ValueError`` with the user's message."""
	values = values or {}
	unknown = sorted(k for k in values if k not in ADD_FIELDS and k != "assign_to")
	if unknown:
		raise ValueError(f"These fields cannot be set here: {', '.join(unknown)}.")
	out = {}
	subject = rules.clean(values.get("subject"))
	if not subject:
		raise ValueError("Give the task a subject.")
	out["subject"] = subject
	for field in ("parent_task", "type", "description"):
		text = values.get(field)
		if isinstance(text, str) and text.strip():
			out[field] = text.strip() if field != "description" else text
	out["is_group"] = 1 if cint(values.get("is_group")) else 0
	priority = _text(values.get("priority"))
	if priority:
		if priority not in PRIORITIES:
			raise ValueError("Priority must be Low, Medium, High or Urgent.")
		out["priority"] = priority
	for field in ("exp_start_date", "exp_end_date"):
		text = _text(values.get(field))
		if text:
			out[field] = text
	if values.get("expected_time") not in (None, ""):
		hours = flt(values.get("expected_time"))
		if hours < 0:
			raise ValueError("Expected hours cannot be negative.")
		out["expected_time"] = hours
	return out


def _assignees(value):
	if isinstance(value, str):
		value = value.strip()
		if value.startswith("["):
			try:
				value = json.loads(value)
			except ValueError:
				value = [value]
		else:
			value = [value] if value else []
	return [_text(user) for user in value or [] if _text(user)]


@frappe.whitelist()
def add_task(project, values):
	"""Create one Task on ``project`` from the dialog's ``values`` (:func:`clean_add_values`), as
	the caller. A parent that is not a group becomes one. ``assign_to`` (a user, or a list) is
	assigned after the insert through Frappe's own assignment, and a failure there is a warning,
	not a lost task. Returns ``{"name", "subject", "parent_task", "converted", "warnings"}``.
	"""
	project = _require_project(project, "write")
	given = _parse_dict(values)
	try:
		fields = clean_add_values(given)
	except ValueError as e:
		frappe.throw(str(e))
	parent = _require_project_task(project, fields.get("parent_task"), _("Parent task"))
	fields["parent_task"] = parent
	converted = []
	if parent and _make_group(parent):
		converted.append(parent)
	if _has_order_column():
		fields[ORDER_FIELD] = rules.next_position(_project_tasks(project), parent)
	doc = frappe.get_doc(dict(fields, doctype="Task", project=project, status="Open"))
	doc.insert()
	warnings = []
	users = _assignees(given.get("assign_to"))
	if users:
		from frappe.desk.form.assign_to import add

		try:
			add({"doctype": "Task", "name": doc.name, "assign_to": users})
		except Exception:
			frappe.log_error(title=LOG_TITLE, message=frappe.get_traceback())
			warnings.append(
				_("The task was added but could not be assigned to {0}.").format(", ".join(users))
			)
	return {
		"name": doc.name,
		"subject": doc.subject,
		"parent_task": doc.parent_task,
		"converted": converted,
		"warnings": warnings,
	}


# ---------------------------------------------------------------------- delete many


def _delete_set(project, tasks):
	names = _parse_list(tasks)
	if not names:
		frappe.throw(_("Pick at least one task."))
	rows = _project_tasks(project)
	known = {row["name"] for row in rows}
	strangers = [name for name in names if name not in known]
	if strangers:
		frappe.throw(_("Not on {0}: {1}.").format(project, ", ".join(strangers)))
	order = rules.delete_order(rows, names)
	subjects = {row["name"]: row.get("subject") or row["name"] for row in rows}
	return names, order, subjects


@frappe.whitelist()
def delete_preview(project, tasks):
	"""How many Tasks :func:`delete_tasks` would remove for ``tasks``: ``{"picked", "under",
	"total", "subjects"}`` (the first few subjects, for the confirm)."""
	project = _require_project(project, "write")
	names, order, subjects = _delete_set(project, tasks)
	return {
		"picked": len(names),
		"under": len(order) - len(names),
		"total": len(order),
		"subjects": [subjects[name] for name in names[:8]],
	}


@frappe.whitelist()
def delete_tasks(project, tasks):
	"""Delete ``tasks`` (a JSON list of names on ``project``) and every Task under them, deepest
	first, each under a savepoint. Returns ``{"deleted": [names], "kept": [{name, subject,
	reason}]}``; a kept task is one Frappe or ERPNext refused, with their reason, and nothing is
	logged for an expected refusal (the user can read and act on it).
	"""
	project = _require_project(project, "write")
	names, order, subjects = _delete_set(project, tasks)
	if len(order) > MAX_DELETE:
		frappe.throw(_("That is {0} tasks; delete {1} or fewer at once.").format(len(order), MAX_DELETE))
	deleted, kept = [], []
	flags = getattr(frappe, "flags", None)
	for name in order:
		savepoint = "scope_delete_task"
		frappe.db.savepoint(savepoint)
		muted = getattr(flags, "mute_messages", False) if flags is not None else False
		if flags is not None:
			flags.mute_messages = True
		try:
			if not frappe.has_permission("Task", "delete", doc=name):
				raise frappe.PermissionError(_("You are not permitted to delete it."))
			# A `Task Depends On` row in another Task is a link, and ERPNext's `on_trash` (16.52.1)
			# clears only the parent's depends_on, so an imported chain (SD waits on PD) would refuse
			# PD while SD still lists it. Unlink the dependents that are themselves being deleted,
			# and only those: a task outside the pick that waits on this one still blocks it.
			frappe.db.delete("Task Depends On", {"parenttype": "Task", "parent": ["in", order], "task": name})
			frappe.delete_doc("Task", name)
			deleted.append(name)
		except (
			frappe.LinkExistsError,
			frappe.DocumentLockedError,
			frappe.PermissionError,
			frappe.ValidationError,
		) as e:
			frappe.db.rollback(save_point=savepoint)
			kept.append({"name": name, "subject": subjects.get(name, name), "reason": str(e)})
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			frappe.log_error(title=LOG_TITLE, message=frappe.get_traceback())
			kept.append(
				{"name": name, "subject": subjects.get(name, name), "reason": _("An error was logged.")}
			)
		finally:
			if flags is not None:
				flags.mute_messages = muted
	return {"deleted": deleted, "kept": kept}


# ---------------------------------------------------------------------- move (drag-and-drop, Move under...)


def _parse_rows(value):
	if value is None:
		return []
	if isinstance(value, str):
		value = value.strip()
		if not value:
			return []
		try:
			value = json.loads(value)
		except ValueError:
			frappe.throw(_("The task list could not be read. Reload the page and try again."))
	if not isinstance(value, list | tuple):
		frappe.throw(_("Expected a list of tasks."))
	rows = []
	for item in value:
		if not isinstance(item, dict) or not _text(item.get("name")):
			continue
		order = item.get(ORDER_FIELD)
		rows.append(
			{
				"name": _text(item.get("name")),
				"parent_task": _text(item.get("parent_task")) or None,
				ORDER_FIELD: None if order in (None, "") else cint(order),
			}
		)
	return rows


def _same_order(stored, wanted):
	return cint(stored or 0) == cint(wanted or 0)


def _descendants(rows, root):
	"""Names of every task under ``root`` among ``rows`` (dicts with ``name`` and ``parent_task``)."""
	kids = {}
	for row in rows:
		if row.get("parent_task"):
			kids.setdefault(row["parent_task"], []).append(row["name"])
	out, queue = set(), list(kids.get(root, []))
	while queue:
		name = queue.pop()
		if name in out:
			continue
		out.add(name)
		queue.extend(kids.get(name, []))
	return out


@frappe.whitelist()
def move_tasks(project, tasks):
	"""The Tasks Tree's drag-and-drop and *Move under…* save (v1.787.0): ``tasks`` is a JSON list of
	``{name, parent_task, custom_subtask_order}``, as the tree holds them after the drop (``parent_task``
	empty for the top level; ``custom_subtask_order`` missing to be placed after the parent's last
	subtask). Only a task whose parent or position differs from what is stored is saved, through the
	Task controller as the caller; a parent that is not yet a group is made one first.

	Returns ``{"moved": [names], "converted": [names], "refused": None | {"name", "subject", "reason"}}``.
	A refusal rolls back the whole request (nothing is half moved), carries ERPNext's own reason (an
	open task under a completed parent, a project mismatch) and is not logged; an unexpected failure
	is logged and reported as such. The message is returned, never thrown, so the tree shows it in its
	own dialog and puts itself back, instead of a silent revert.
	"""
	project = _require_project(project, "write")
	rows = _parse_rows(tasks)
	if not rows:
		frappe.throw(_("Nothing to move."))
	stored_rows = _project_tasks(project)
	stored = {row["name"]: row for row in stored_rows}
	strangers = [row["name"] for row in rows if row["name"] not in stored]
	if strangers:
		frappe.throw(_("Not on {0}: {1}.").format(project, ", ".join(strangers)))
	after = {row["name"]: dict(stored[row["name"]], parent_task=row["parent_task"]) for row in rows}
	layout = [after.get(row["name"], row) for row in stored_rows]
	changes = []
	for row in rows:
		name, parent = row["name"], row["parent_task"]
		if parent == name:
			frappe.throw(_("{0} cannot be its own parent.").format(name))
		if parent and parent not in stored:
			frappe.throw(_("Not on {0}: {1}.").format(project, parent))
		if parent and parent in _descendants(layout, name):
			frappe.throw(
				_("{0} cannot be moved under {1}: that task is one of its own subtasks.").format(
					stored[name].get("subject") or name, stored[parent].get("subject") or parent
				)
			)
		was = stored[name]
		parent_changed = (was.get("parent_task") or None) != parent
		order = row[ORDER_FIELD]
		if order is None:
			if not parent_changed:
				continue
			order = rules.next_position(stored_rows, parent)
		if not parent_changed and _same_order(was.get(ORDER_FIELD), order):
			continue
		changes.append((name, parent, order, parent_changed))
	moved, converted = [], []
	if not changes:
		return {"moved": moved, "converted": converted, "refused": None}
	savepoint = "scope_move_tasks"
	flags = getattr(frappe, "flags", None)
	muted = getattr(flags, "mute_messages", False) if flags is not None else False
	frappe.db.savepoint(savepoint)
	if flags is not None:
		flags.mute_messages = True
	current = None
	try:
		for name, parent, order, parent_changed in changes:
			current = name
			if (
				parent_changed
				and parent
				and not cint(stored[parent].get("is_group"))
				and parent not in converted
			):
				if _make_group(parent):
					converted.append(parent)
			doc = frappe.get_doc("Task", name)
			doc.parent_task = parent
			if _has_order_column():
				doc.set(ORDER_FIELD, order)
			doc.save()
			moved.append(name)
	except (frappe.ValidationError, frappe.PermissionError, frappe.DocumentLockedError) as e:
		frappe.db.rollback(save_point=savepoint)
		return {
			"moved": [],
			"converted": [],
			"refused": {
				"name": current,
				"subject": stored.get(current, {}).get("subject") or current,
				"reason": str(e),
			},
		}
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(title=LOG_TITLE, message=frappe.get_traceback())
		return {
			"moved": [],
			"converted": [],
			"refused": {
				"name": current,
				"subject": stored.get(current, {}).get("subject") or current,
				"reason": _("An error was logged."),
			},
		}
	finally:
		if flags is not None:
			flags.mute_messages = muted
	return {"moved": moved, "converted": converted, "refused": None}
