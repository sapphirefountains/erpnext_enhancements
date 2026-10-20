"""Repeating assignments (WI-081 Phase 3): "every Monday, check the Gateway pump log".

A ToDo with ``custom_repeat`` set ("Weekly", "Monthly" ...) makes the next one when it is marked
**done** -- from My Work, the banner, the sidebar's close, anywhere that closes it through the ToDo
controller. Wired as doc_events ToDo ``on_update`` -> :func:`on_update`.

The next one is the same person (or the same team queue), the same record, the same ask and
priority, the same repeat, and the checklist's steps unticked, due one step after this one's due
date (``assignments.next_occurrence``: counted from the due date, never already overdue).

Things it is careful about:

* **Only Closed repeats.** Cancelled means nobody is doing it any more; withdrawing or reassigning a
  repeating assignment must not spawn another.
* **Auto-close and the backlog triage never repeat.** A record that finished took its work with it:
  ``auto_close`` sets ``flags.ee_no_repeat`` on the ToDos it closes, and the triage writes the status
  with ``db.set_value``, which runs no hooks at all.
* **Nor does anything closed because its record finished** (v1.699.0). ERPNext closes every open
  assignment on a Task the moment it is Completed -- from ``validate_status``, then again from
  ``on_update`` -- through the ToDo controller with no flag, and before the Task's row says
  Completed. Unguarded, a weekly on a Task made a next one that the second close closed again,
  which made another. The Task controller marks the Task as finishing for the request
  (:func:`mark_finishing`) before ERPNext's validate runs, and a record already stored as finished
  (``assignments.FINISHED_STATES``) stops a repeat too.
* **The next one is not a new assignment to be texted about.** It carries ``flags.ee_repeat_of``,
  which ``assignment_alerts.on_todo_insert`` reads, so a weekly High chore does not text every week.
* **Only the change into Closed** (``get_doc_before_save``) -- re-saving a closed ToDo makes nothing.
* ``custom_repeat`` is read with ``getattr`` (doc_events fire during ERPNext's test bootstrap,
  before this app's fields exist), and it never raises: closing must always work.
"""

import frappe
from frappe.utils import getdate, nowdate

from erpnext_enhancements.task_enhancements import assignments

#: ``frappe.flags`` key: the ``(doctype, name)`` records being saved into a finished state in this
#: request (:func:`mark_finishing`).
FINISHING = "ee_finishing_records"


def mark_finishing(doctype, name):
	"""Note, for this request, that ``doctype`` ``name`` is being saved into a finished state, so
	the repeating assignments its save closes make no next one."""
	marked = getattr(frappe.flags, FINISHING, None) or set()
	marked.add((doctype, str(name)))
	setattr(frappe.flags, FINISHING, marked)


def _record_finished(doc):
	"""True when the record ``doc`` is on is finishing in this request or is stored as finished."""
	ref_type, ref_name = doc.get("reference_type"), doc.get("reference_name")
	if not (ref_type and ref_name):
		return False
	if (ref_type, str(ref_name)) in (getattr(frappe.flags, FINISHING, None) or ()):
		return True
	field = assignments.finished_field(ref_type)
	if not field:
		return False
	return assignments.finished_value(ref_type, frappe.db.get_value(ref_type, ref_name, field))


def on_update(doc, method=None):
	try:
		repeat = getattr(doc, "custom_repeat", None) or ""
		if not repeat or doc.status != "Closed" or doc.flags.get("ee_no_repeat"):
			return
		flags = frappe.flags
		if (
			getattr(flags, "in_import", False)
			or getattr(flags, "in_install", False)
			or getattr(flags, "in_migrate", False)
		):
			return
		before = doc.get_doc_before_save()
		if before is None or before.status == "Closed":
			return
		if _record_finished(doc):
			return
		make_next(doc, repeat)
	except Exception:
		frappe.log_error(
			title="My Work: could not make the next repeat", message=f"{doc.name}\n{frappe.get_traceback()}"
		)


def make_next(doc, repeat):
	"""Insert the next occurrence of ``doc`` and return it (``None`` for an unknown repeat)."""
	due = assignments.due_date(doc.date, doc.creation)
	nxt = assignments.next_occurrence(due, getdate(nowdate()), repeat)
	if not nxt:
		return None
	steps = [{"item": row.item} for row in (getattr(doc, "custom_checklist", None) or []) if row.get("item")]
	new = frappe.get_doc(
		{
			"doctype": "ToDo",
			"status": "Open",
			"allocated_to": doc.allocated_to,
			"assigned_by": doc.assigned_by,
			"role": doc.role,
			"reference_type": doc.reference_type,
			"reference_name": doc.reference_name,
			"description": doc.description,
			"priority": doc.priority,
			"date": str(nxt),
			"custom_repeat": repeat,
			"custom_checklist": steps,
		}
	)
	new.flags.ee_repeat_of = doc.name
	new.insert(ignore_permissions=True)
	return new
