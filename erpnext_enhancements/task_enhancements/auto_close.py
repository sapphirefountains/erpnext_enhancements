"""Close a record's open assignments when its work is over (WI-081 Phase 1).

An assignment is a ToDo, and a ToDo outlives its reason. On production (2026-10-20) 225 open
ToDos sat on Completed Tasks, 35 on "Canceled" ones, 17 on Lost Opportunities and 16 on Completed
Projects: nobody closes an assignment by hand once the record says it is done, so the list fills
with work that is already finished and stops being worth reading. This closes them when the record
finishes. The finished states, and why each one is in or out, are
:data:`assignments.FINISHED_STATES`.

Wiring (``hooks.py``):

* ``on_change`` for every doctype in ``FINISHED_STATES`` -> :func:`on_change`. ``on_change`` and
  not ``on_update`` because Frappe v16 runs ``on_change`` after a save, a submit, an
  update-after-submit **and a** ``db_set`` -- and ``db_set`` loads the before-image first
  (``document.py``, 16.50.0). ERPNext moves a Purchase Order or Material Request to Completed,
  Received or Closed with ``set_status(update=True)``, which is a ``db_set`` that fires no
  ``on_update`` at all. A submit is caught here too: it runs ``on_change`` with docstatus 0 in the
  before-image and 1 after.
* ``on_cancel`` for ``"*"`` -> :func:`on_cancel`: a cancelled document is nobody's work, whatever
  its doctype.

Things it is careful about:

* **Only the change into a finished state closes anything** (``assignments.just_finished``), so an
  assignment made on a record that is already finished survives every later save of it.
* **It closes through the ToDo controller** (:func:`close_todo`), the way the sidebar's close
  does, so the record's ``_assign`` list is rebuilt and its timeline says "Assignment of X removed
  by Y". It does **not** send ``assign_to``'s "Your assignment on X has been removed by Y"
  notification, which ``close_all_assignments`` would: telling everyone on a Task that their
  assignment was "removed" because the Task was completed is noise, and a lot of it.
* **ERPNext's own Task close still runs.** ``Task.unassign_todo`` closes on "Completed" and clears
  on "Cancelled"; this adds the site's "Canceled", "Invoiced" and "Template". Both are idempotent:
  a ToDo already closed is skipped.
* **It never raises.** Finishing a record must not fail because one assignment could not be
  closed; a failure is logged and the record saves.
* Imports, installs, migrations and patches are skipped, like the crew mirrors.
"""

import frappe

from erpnext_enhancements.task_enhancements import assignments


def _skip():
	flags = frappe.flags
	return bool(
		getattr(flags, "in_import", False)
		or getattr(flags, "in_install", False)
		or getattr(flags, "in_migrate", False)
		or getattr(flags, "in_patch", False)
	)


def close_todo(todo, status="Closed", repeat=True):
	"""Set one ToDo to ``status`` through its controller and return it.

	``todo`` is a name or a ToDo document. The controller adds the "Assignment of X removed by Y"
	comment to the record and rebuilds its ``_assign`` list (``todo.py``). Like
	``assign_to.set_status``, a record with an ``assigned_to`` field naming this person has it
	cleared -- but only when it names this person, where ``set_status`` clears it whoever it names.
	"""
	if isinstance(todo, str):
		todo = frappe.get_doc("ToDo", todo)
	if todo.status == status:
		return todo
	todo.status = status
	if repeat is False:
		# A record that finished took its work with it: recurrence.py makes no next one.
		todo.flags.ee_no_repeat = True
	todo.save(ignore_permissions=True)
	_clear_assigned_to(todo)
	return todo


def _clear_assigned_to(todo):
	ref_type, ref_name = todo.get("reference_type"), todo.get("reference_name")
	if not (ref_type and ref_name and todo.get("allocated_to")):
		return
	meta = frappe.get_meta(ref_type)
	if meta.issingle or not meta.get_field("assigned_to"):
		return
	if frappe.db.get_value(ref_type, ref_name, "assigned_to") == todo.allocated_to:
		frappe.db.set_value(ref_type, ref_name, "assigned_to", None, update_modified=False)


def open_assignments(doctype, name):
	"""Names of the open ToDos on one record."""
	return frappe.get_all(
		"ToDo",
		filters={"reference_type": doctype, "reference_name": str(name), "status": "Open"},
		pluck="name",
	)


def close_open_assignments(doctype, name, status="Closed"):
	"""Close every open ToDo on one record. Returns how many were closed; never raises.

	A repeating assignment closed this way does not repeat: the record's work is over."""
	closed = 0
	for todo in open_assignments(doctype, name):
		try:
			close_todo(todo, status, repeat=False)
			closed += 1
		except Exception:
			frappe.log_error(
				title="My Work: could not close an assignment",
				message=f"{doctype} {name}, ToDo {todo}\n{frappe.get_traceback()}",
			)
	return closed


def on_change(doc, method=None):
	"""doc_events ``on_change``: close the record's open assignments when it has just finished."""
	try:
		field = assignments.finished_field(doc.doctype)
		if not field or _skip() or doc.is_new():
			return
		before = doc.get_doc_before_save()
		before_value = before.get(field) if before is not None else None
		if not assignments.just_finished(doc.doctype, before_value, doc.get(field)):
			return
		close_open_assignments(doc.doctype, doc.name)
	except Exception:
		frappe.log_error(
			title="My Work: auto-close failed",
			message=f"{getattr(doc, 'doctype', '')} {getattr(doc, 'name', '')}\n{frappe.get_traceback()}",
		)


def on_cancel(doc, method=None):
	"""doc_events ``on_cancel`` for ``"*"``: a cancelled document's assignments are cancelled.

	Cancelled, not Closed: nobody did the work. Returns on one indexed query for a document that
	has no open assignment, which is nearly all of them.
	"""
	try:
		if _skip() or doc.doctype == "ToDo":
			return
		close_open_assignments(doc.doctype, doc.name, status="Cancelled")
	except Exception:
		frappe.log_error(
			title="My Work: auto-close on cancel failed",
			message=f"{getattr(doc, 'doctype', '')} {getattr(doc, 'name', '')}\n{frappe.get_traceback()}",
		)
