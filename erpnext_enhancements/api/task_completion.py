"""One-tap complete, and its Undo, for a Task shown somewhere other than its own form.

Triton's Projects Dashboard opens on a deadline board (Nik, 2026-11-25: "driven by deadlines,
milestones and task due dates", with "the dopamine release from marking a milestone or task as
complete"). Every card there has a check button. Triton calls :func:`complete_task` as the person
who pressed it, over that person's own ERPNext sign-in, and :func:`reopen_task` when they press
Undo. ERPNext owns the rule; Triton draws the card and the celebration.

What the two calls promise:

* **Nothing is said about a Task the caller may not change.** Whether it exists, its status and
  its subject are answered only after the caller is found entitled. Anyone else gets the same
  sentence for a Task that is missing and one they may not change, so the endpoint cannot be
  walked to read subjects. A blocking dependency is named only when the caller may read it.
* **Who is entitled: Task write permission on this Task, and access to its Project.** Completing
  and undoing need ``frappe.has_permission("Task", "write", doc)``, with user permissions applied,
  and pass :func:`require_project_access`. A ToDo never grants anything here: an assignment does not
  stand in for permission on the Task (review of #1521). My Work's Done follows the same rule.
* **ERPNext's own Task save does the work.** Nothing here copies a Task rule. The status changes
  and ``doc.save()`` runs under the caller's own permissions, so ``validate_status`` (a Task it
  depends on must be done first), ``validate_project_access``, ``close_all_assignments``, the
  project's % complete, the dependent-task reschedule and this site's own Task hooks all happen as
  they would from the form. The 16.60 date checks judge only the expected dates, and only when one
  changed, so completing never trips them.
* **A refusal is a sentence, never a crash.** Whatever ERPNext refuses comes back as
  ``{"ok": False, "message": ...}`` in plain text, with the transaction rolled back and the
  message log cleared, so the dashboard shows it on its toast and the card stays open. A second
  tap that loses the race to the first answers "already completed", not an error.
* **Undo puts back what the completion changed, and only that.** :func:`complete_task` keeps,
  for 15 minutes in the cache, the Task's previous status and progress, the assignments it was
  about to close, and the Task's ``modified`` after the save. :func:`reopen_task` refuses when
  the Task has changed since, or was completed by someone else, so a late Undo cannot wipe a
  manager's own reopen and complete. It restores the status and progress (ERPNext's
  ``clear_completion_on_reopen`` clears Completed On and Completed By, and zeroes a progress the
  save left unchanged, so a Task that was at 100% gets its 100 back with a second save), then
  reopens exactly those assignments. Undo cannot recall a "Task Completed" notification the
  completion already queued. Reopening an assignment goes through the ToDo controller,
  whose ``validate`` words any status change as "Assignment of ... removed by ...", so that
  line appears on the Task's timeline after an Undo. Without the record (it expired, or a deploy
  flushed redis) Undo still reopens the Task, as Open, for someone with write permission, and
  says that no assignment came back.

Both are POST only: a GET is CSRF-free in Frappe, and these write.
"""

import re
from html import unescape

import frappe
from frappe import _
from frappe.utils import flt, now_datetime, nowdate

from erpnext_enhancements.task_enhancements import assignments

#: How long Undo stays possible after a completion, in seconds. The dashboard shows Undo for
#: 8 seconds; the record outlives the toast so a slow network or a second tab still finds it.
UNDO_SECONDS = 15 * 60

#: The statuses Undo may put back. A Task finished some other way (Invoiced) is not undone here.
REOPENABLE = ("Open", "Working", "Pending Review", "Overdue")

#: The statuses of a Task another depends on that let that other Task be completed.
DEPENDENCY_DONE = assignments.TASK_DEPENDENCY_DONE

#: The one answer for a Task that is missing and for one the caller may not change.
NOT_FOUND = "That task wasn't found, or you can't change it."


@frappe.whitelist(methods=["POST"])
def complete_task(task: str) -> dict:
	"""Complete ``task`` as the session user, today. See the module docstring."""
	return _guarded(lambda: _complete(task), _("complete"))


@frappe.whitelist(methods=["POST"])
def reopen_task(task: str) -> dict:
	"""Undo a completion: the Task's previous status, progress and assignments come back."""
	return _guarded(lambda: _reopen(task), _("reopen"))


def require_project_access(doc):
	"""Raise ``frappe.PermissionError`` unless the session user may pick the Task's project.

	ERPNext's ``Task.validate_project_access`` makes the same check during the save; asking first
	turns it into a refusal before anything is written, and keeps the rule explicit should a save
	ever run with ``ignore_permissions``. It prefers ERPNext's own ``check_project_access`` (v16.60)
	and falls back to a Project select check where an older ERPNext lacks it. My Work's Done uses
	it too.
	"""
	project = doc.get("project")
	if not project:
		return
	check = getattr(doc, "check_project_access", None)
	if callable(check):
		check(project)
		return
	if not frappe.has_permission("Project", "select", doc=project):
		raise frappe.PermissionError(_("You can't pick project {0}.").format(project))


# ---------------------------------------------------------------------------
# The two actions


def _complete(task):
	name = (task or "").strip()
	if not name:
		return _refuse(_("No task was named."))
	if not frappe.db.exists("Task", name):
		return _refuse(_(NOT_FOUND))

	doc = frappe.get_doc("Task", name)
	user = frappe.session.user
	if not _may_change(doc):
		return _refuse(_(NOT_FOUND))

	if doc.status == "Completed":
		return _result(doc, already=True)
	if doc.status in assignments.TASK_FINISHED:
		return _refuse(_("{0} is {1}, so there is nothing to complete.").format(_label(doc), _(doc.status)))

	blocker = _blocker(doc)
	if blocker:
		return _refuse(blocker)

	open_todos = _open_assignments(name)
	previous = {
		"status": doc.status,
		"progress": flt(doc.get("progress")),
		"todos": [row.get("name") for row in open_todos if row.get("name")],
		"at": str(now_datetime()),
	}
	doc.status = "Completed"
	doc.completed_on = nowdate()
	doc.completed_by = user
	require_project_access(doc)
	try:
		doc.save()
	except frappe.TimestampMismatchError:
		# Two taps, or two screens, raced: the other save won. Say what is true now.
		_abandon()
		fresh = frappe.get_doc("Task", name)
		if fresh.status == "Completed":
			return _result(fresh, already=True)
		return _refuse(_("{0} changed while you were completing it. Try again.").format(_label(fresh)))

	notes = _take_messages()
	previous["modified"] = _modified(name, doc)
	frappe.cache.set_value(_undo_key(user, name), previous, expires_in_sec=UNDO_SECONDS)
	return _result(doc, notes=notes, undo=True)


def _reopen(task):
	name = (task or "").strip()
	if not name:
		return _refuse(_("No task was named."))
	if not frappe.db.exists("Task", name):
		return _refuse(_(NOT_FOUND))

	doc = frappe.get_doc("Task", name)
	user = frappe.session.user
	key = _undo_key(user, name)
	record = frappe.cache.get_value(key)
	if not isinstance(record, dict):
		record = None

	# Undo is an edit like any other: it needs write permission now, whatever entitled the
	# completion. The record only says what to put back.
	if not _may_change(doc):
		return _refuse(_(NOT_FOUND))

	if doc.status != "Completed":
		frappe.cache.delete_value(key)
		return _result(doc, already=True, restored=0)

	if record:
		changed = (record.get("modified") and str(doc.get("modified") or "") != record.get("modified")) or (
			doc.get("completed_by") and doc.get("completed_by") != user
		)
		if changed:
			frappe.cache.delete_value(key)
			return _refuse(
				_(
					"{0} has changed since you completed it, so Undo can't put it back. Reopen it from the Task itself."
				).format(_label(doc))
			)

	previous = (record or {}).get("status")
	restore_progress = record.get("progress") if record else None
	doc.status = previous if previous in REOPENABLE else "Open"
	if restore_progress is not None:
		doc.progress = flt(restore_progress)
	require_project_access(doc)
	doc.save()
	if restore_progress is not None and flt(doc.get("progress")) != flt(restore_progress):
		# clear_completion_on_reopen zeroed a progress this save left unchanged (it was 100 before
		# the completion). Now the Task is open that rule no longer applies, so a second save keeps it.
		doc.progress = flt(restore_progress)
		doc.save()

	restored = _reopen_assignments(name, (record or {}).get("todos") or [])
	frappe.cache.delete_value(key)
	notes = _take_messages()
	if not record:
		notes.append(_("Reopened as Open. Its assignments were not restored."))
	return _result(doc, notes=notes, restored=restored)


# ---------------------------------------------------------------------------
# Helpers


def _may_change(doc):
	"""Whether the session user may complete or reopen ``doc``: Task write permission on it."""
	return bool(frappe.has_permission("Task", "write", doc=doc))


def _undo_key(user, task):
	return f"ee_task_completion_undo::{user}::{task}"


def _modified(name, doc):
	"""The Task's ``modified`` as stored once every hook of the save has run."""
	value = frappe.db.get_value("Task", name, "modified")
	return str(value or doc.get("modified") or "")


def _label(doc):
	subject = assignments.truncate(doc.get("subject") or "", 60)
	return f"{doc.name} ({subject})" if subject else doc.name


def _open_assignments(task):
	"""The open ToDos on ``task``: the ones ERPNext's completion is about to close."""
	return frappe.get_all(
		"ToDo",
		filters={"reference_type": "Task", "reference_name": task, "status": "Open"},
		fields=["name", "allocated_to"],
	)


def _blocker(doc):
	"""Why ERPNext would refuse to complete ``doc``, as a sentence, or ``None``.

	ERPNext refuses while any Task in ``depends_on`` is not Completed or Cancelled (this site's
	Task override lets "Canceled" through too). A group Task lists each of its subtasks there
	(``populate_depends_on``), so for a phase the sentence counts the open subtasks instead of
	naming the first. A dependency the caller may not read is not named. ERPNext still makes the
	decision when the Task saves; this only words it.
	"""
	names = [row.get("task") for row in (doc.get("depends_on") or []) if row.get("task")]
	if not names:
		return None
	rows = {
		row.name: row
		for row in frappe.get_all(
			"Task", filters={"name": ["in", names]}, fields=["name", "subject", "status", "parent_task"]
		)
	}
	blocking = [n for n in names if (rows.get(n) or {}).get("status") not in DEPENDENCY_DONE]
	if not blocking:
		return None

	children = [n for n in blocking if (rows.get(n) or {}).get("parent_task") == doc.name]
	if doc.get("is_group") and children:
		count = len(children)
		return (
			_("Not completed: {0} still has 1 open task under it. Finish it first.").format(_label(doc))
			if count == 1
			else _("Not completed: {0} still has {1} open tasks under it. Finish those first.").format(
				_label(doc), count
			)
		)

	other = blocking[0]
	if other not in rows or not frappe.has_permission("Task", "read", doc=other):
		return _(
			"Not completed: {0} depends on another task that isn't done yet. Ask its project manager."
		).format(_label(doc))
	row = rows[other]
	subject = assignments.truncate(row.get("subject") or "", 60)
	label = f"{other} ({subject})" if subject else other
	return _(
		"Not completed: {0} depends on {1}, which is {2}. ERPNext completes a Task only once every "
		"Task it depends on is Completed or Cancelled."
	).format(_label(doc), label, _(row.get("status")))


def _reopen_assignments(task, names):
	"""Reopen the ToDos a completion closed: only ones on ``task`` that are still Closed."""
	restored = 0
	for todo in names:
		row = frappe.db.get_value("ToDo", todo, ["reference_type", "reference_name", "status"], as_dict=True)
		if not row or row.get("reference_type") != "Task" or row.get("reference_name") != task:
			continue
		if row.get("status") != "Closed":
			continue
		doc = frappe.get_doc("ToDo", todo)
		doc.status = "Open"
		doc.save(ignore_permissions=True)
		restored += 1
	return restored


def _result(doc, notes=None, already=False, undo=False, restored=None):
	out = {
		"ok": True,
		"task": doc.name,
		"status": doc.status,
		"completed_on": str(doc.get("completed_on") or ""),
		"modified": str(doc.get("modified") or ""),
		"already": already,
		"undo": undo,
		"notes": notes or [],
	}
	if restored is not None:
		out["restored_assignments"] = restored
	return out


def _refuse(message):
	return {"ok": False, "message": message}


_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


def _plain(text):
	"""``text`` as one plain line: tags out, entities decoded, whitespace collapsed."""
	if not text:
		return ""
	return _SPACE.sub(" ", unescape(_TAG.sub(" ", str(text)))).strip()


def _message_texts():
	texts = []
	for entry in getattr(frappe.local, "message_log", None) or []:
		if isinstance(entry, dict):
			entry = entry.get("message")
		text = _plain(entry)
		if text and text not in texts:
			texts.append(text)
	return texts


def _take_messages():
	"""What ERPNext said during a save that succeeded (a dependent task moved, say), as plain
	lines, and the log cleared so the caller is not sent the same words twice."""
	texts = _message_texts()
	frappe.clear_messages()
	return texts


def _abandon():
	frappe.db.rollback()
	frappe.clear_messages()


def _guarded(action, verb):
	"""Run ``action``; any refusal or failure comes back as ``{"ok": False, "message": ...}``."""
	try:
		return action()
	except frappe.PermissionError:
		_abandon()
		return _refuse(_("You don't have permission to change this task."))
	except frappe.ValidationError as e:
		texts = _message_texts()
		message = _plain(str(e)) or (texts[-1] if texts else "") or _("ERPNext refused the change.")
		_abandon()
		return _refuse(message)
	except Exception:
		trace = frappe.get_traceback()
		_abandon()
		# After the rollback, or the rollback would take the Error Log row with it.
		frappe.log_error(title="Task completion from a dashboard failed", message=trace)
		return _refuse(_("ERPNext could not {0} this task. The error is in the Error Log.").format(verb))
