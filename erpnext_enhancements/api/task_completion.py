"""One-tap complete, and its Undo, for a Task shown somewhere other than its own form.

Triton's Projects Dashboard opens on a deadline board (Nik, 2026-11-25: "driven by deadlines,
milestones and task due dates", with "the dopamine release from marking a milestone or task as
complete"). Every card there has a check button. Triton calls :func:`complete_task` as the person
who pressed it, over that person's own ERPNext sign-in, and :func:`reopen_task` when they press
Undo. ERPNext owns the rule; Triton draws the card and the celebration.

What the two calls promise:

* **ERPNext's own Task save does the work.** Nothing here copies a Task rule. The status changes
  and ``doc.save()`` runs, so ERPNext's ``validate_status`` (a Task it depends on must be done
  first), its project access check, ``close_all_assignments``, the project's % complete, the
  dependent-task reschedule and this site's own Task hooks all happen as they would from the form.
  The 16.60 date checks judge only the expected dates, and only when one changed, so completing
  never trips them.
* **Task permissions hold.** The person needs Task write permission on this Task, which
  ``frappe.has_permission`` answers with their user permissions applied. An open assignment on it
  counts too, as it does for My Work's Done (``api/my_work._complete_task``): being assigned the
  Task is what entitles someone to finish it, and an assignee may hold only the read share
  Assign To gives.
* **A refusal is a sentence, never a crash.** Whatever ERPNext refuses comes back as
  ``{"ok": False, "message": ...}`` in plain text, with the transaction rolled back and the
  message log cleared, so the dashboard shows it on its toast and the card stays open. A
  dependency or an open subtask is caught before the save, so the sentence names the Task in the
  way rather than quoting ERPNext's bolded one.
* **Undo puts back what the completion changed.** :func:`complete_task` keeps, for 15 minutes in
  the cache, the Task's previous status and progress and the assignments it was about to close.
  :func:`reopen_task` restores the status and progress (ERPNext's ``clear_completion_on_reopen``
  clears Completed On and Completed By) and reopens exactly those assignments. Without the record
  (it expired, or a deploy flushed redis) Undo still reopens the Task, as Open, for someone with
  write permission, and says that no assignment came back.

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


@frappe.whitelist(methods=["POST"])
def complete_task(task: str) -> dict:
	"""Complete ``task`` as the session user, today. See the module docstring."""
	return _guarded(lambda: _complete(task), _("complete"))


@frappe.whitelist(methods=["POST"])
def reopen_task(task: str) -> dict:
	"""Undo a completion: the Task's previous status, progress and assignments come back."""
	return _guarded(lambda: _reopen(task), _("reopen"))


# ---------------------------------------------------------------------------
# The two actions


def _complete(task):
	name = (task or "").strip()
	if not name:
		return _refuse(_("No task was named."))
	if not frappe.db.exists("Task", name):
		return _refuse(_("That task no longer exists."))

	doc = frappe.get_doc("Task", name)
	if doc.status == "Completed":
		return _result(doc, already=True)
	if doc.status in assignments.TASK_FINISHED:
		return _refuse(_("{0} is {1}, so there is nothing to complete.").format(_label(doc), _(doc.status)))

	user = frappe.session.user
	open_todos = _open_assignments(name)
	can_write = bool(frappe.has_permission("Task", "write", doc=doc))
	if not can_write and not any(row.get("allocated_to") == user for row in open_todos):
		return _refuse(
			_(
				"You can't change {0}. Completing it needs Task write permission, or an assignment on it."
			).format(_label(doc))
		)

	blocker = _blocker(doc)
	if blocker:
		return _refuse(blocker)

	previous = {
		"status": doc.status,
		"progress": flt(doc.get("progress")),
		"todos": [row.get("name") for row in open_todos if row.get("name")],
		"at": str(now_datetime()),
	}
	doc.status = "Completed"
	doc.completed_on = nowdate()
	doc.completed_by = user
	if not can_write:
		doc.flags.ignore_permissions = True
	doc.save()

	notes = _take_messages()
	frappe.cache.set_value(_undo_key(user, name), previous, expires_in_sec=UNDO_SECONDS)
	return _result(doc, notes=notes, undo=True)


def _reopen(task):
	name = (task or "").strip()
	if not name:
		return _refuse(_("No task was named."))
	if not frappe.db.exists("Task", name):
		return _refuse(_("That task no longer exists."))

	doc = frappe.get_doc("Task", name)
	user = frappe.session.user
	key = _undo_key(user, name)
	record = frappe.cache.get_value(key)
	if not isinstance(record, dict):
		record = None

	if doc.status != "Completed":
		frappe.cache.delete_value(key)
		return _result(doc, already=True, restored=0)

	# The record says this person completed it in the last 15 minutes, which is what entitled them
	# then; without it, reopening is an ordinary edit and needs write permission.
	can_write = bool(frappe.has_permission("Task", "write", doc=doc))
	if not can_write and not record:
		return _refuse(
			_("You can't reopen {0}. Reopening it needs Task write permission.").format(_label(doc))
		)

	previous = (record or {}).get("status")
	doc.status = previous if previous in REOPENABLE else "Open"
	if record and record.get("progress") is not None:
		doc.progress = flt(record.get("progress"))
	if not can_write:
		doc.flags.ignore_permissions = True
	doc.save()

	restored = _reopen_assignments(name, (record or {}).get("todos") or [])
	frappe.cache.delete_value(key)
	notes = _take_messages()
	if not record:
		notes.append(_("Reopened as Open. Its assignments were not restored."))
	return _result(doc, notes=notes, restored=restored)


# ---------------------------------------------------------------------------
# Helpers


def _undo_key(user, task):
	return f"ee_task_completion_undo::{user}::{task}"


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
	naming the first. ERPNext still makes the decision when the Task saves; this only words it.
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
	row = rows.get(other) or {}
	subject = assignments.truncate(row.get("subject") or "", 60)
	label = f"{other} ({subject})" if subject else other
	status = row.get("status")
	return _(
		"Not completed: {0} depends on {1}, which is {2}. ERPNext completes a Task only once every "
		"Task it depends on is Completed or Cancelled."
	).format(_label(doc), label, _(status) if status else _("missing"))


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
