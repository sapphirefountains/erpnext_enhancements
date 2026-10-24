# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A Project's expected start and end, widened so a Task save that needs it is not refused.

On this site ``Project.expected_start_date`` and ``expected_end_date`` are DERIVED: they are the
earliest task start and the latest task end, recomputed on every Task save and delete
(``script_migrations.task.sync_project_dates_from_tasks``), and the Project form shows them
read-only. ERPNext's ``Task.validate_parent_project_dates`` meanwhile refuses a task whose dates
fall outside them (``InvalidDates``). Refusing a save against a value that the same save is about to
recompute is a contradiction, and it blocked the commonest edits there are: the last task one day
later, the first one day earlier, any save of a task on a job whose tasks are all in the past.

**Prod runs ERPNext 16.50.0**, and there the check is broader than on the version-16 branch head
(which changed it on 2026-10-13, after that tag): it judges ``exp_start_date``, ``exp_end_date``,
``act_start_date`` and ``act_end_date``, on every save, changed or not. So a crew-only edit of a task
whose timesheets ran past the derived end is refused too, and so is the save of an Open dependent
that ``Task.reschedule_dependent_tasks`` pushes past the project's end (and the save that pushed it).

So the widening is a Task ``before_validate`` doc_event (:func:`before_task_validate`, v1.742.0),
which frappe 16.50.0's ``Document.run_before_save_methods`` runs before the controller's
``validate``, on insert and save alike: the Desk form, ERPNext's dependent push, the Project Planner,
the Gantt, the rental code and anything else. It widens the window to take in every set date of the
four, in the same transaction, and never shrinks it: the sync hook then sets the authoritative
value straight after. The planner (``api/project_planner._save`` / ``_insert_doc``) and the Gantt
still call :func:`widen_for_task` themselves first; widening twice is harmless, and their bench-free
tests exercise it there. Like the Gantt always has, it leaves no comment: the window is derived, so
the next save says the same thing anyway.
"""

import frappe
from frappe.utils import getdate


def _day(value):
	try:
		return getdate(value) if value else None
	except Exception:
		return None


def widen(project, start=None, end=None):
	"""Move ``project``'s expected start back to ``start`` and its expected end on to ``end`` where
	they do not already reach them. Only a set date is moved (ERPNext checks nothing against an unset
	one), and never inward. Returns ``{field: (old, new)}`` for what changed, empty when nothing did."""
	if not project:
		return {}
	start, end = _day(start), _day(end)
	# One read of both. A tuple of fields: frappe 16.50.0's get_value turns a list into one anyway,
	# and answers a tuple of values, or None for a project that is not there.
	row = frappe.db.get_value("Project", project, ("expected_start_date", "expected_end_date"))
	first, last = (
		_day(value) for value in (row if isinstance(row, list | tuple) and len(row) == 2 else (None, None))
	)
	changed = {}
	if start and first and start < first:
		changed["expected_start_date"] = (first, start)
	if end and last and end > last:
		changed["expected_end_date"] = (last, end)
	# One field per call, as the Gantt has always written it.
	for field, (_old, new) in changed.items():
		frappe.db.set_value("Project", project, field, new, update_modified=False)
	return changed


#: The Task dates ERPNext 16.50.0's ``validate_parent_project_dates`` judges against the project.
TASK_DATE_FIELDS = ("exp_start_date", "exp_end_date", "act_start_date", "act_end_date")


def widen_for_task(doc):
	""":func:`widen` for a Task about to be saved or inserted: from the earliest to the latest of its
	set dates among :data:`TASK_DATE_FIELDS`, so every one of them is inside the window."""
	if (doc.get("doctype") or "Task") != "Task":
		return {}
	days = [day for day in (_day(doc.get(field)) for field in TASK_DATE_FIELDS) if day]
	if not days:
		return {}
	return widen(doc.get("project"), min(days), max(days))


def _database_failed(exc):
	"""Whether ``exc`` means the database itself failed, so the transaction cannot carry on: a deadlock
	or a lock-wait timeout (InnoDB has already rolled the whole transaction back by then), or any
	other ``OperationalError``, ``InternalError`` or ``InterfaceError`` of the connection.

	As frappe 16.50.0 raises them (``frappe/database/database.py`` ``Database.sql``): a deadlock comes
	back as ``frappe.QueryDeadlockError(e)`` and a timeout as ``frappe.QueryTimeoutError(e)``, plain
	``Exception`` subclasses raised ``from`` the pymysql error, so those two are matched by kind and the
	chain of causes is followed; the pymysql kinds and checks are read off ``frappe.db``
	(``frappe/database/mariadb/database.py`` ``MariaDBExceptionUtil``). A missing table or column is
	not a failure: it is what ERPNext's own test bootstrap meets before this app's fields exist, and
	the statement failing does not end the transaction."""
	db = getattr(frappe, "db", None)
	wrapped = tuple(
		kind
		for kind in (getattr(frappe, "QueryDeadlockError", None), getattr(frappe, "QueryTimeoutError", None))
		if isinstance(kind, type) and issubclass(kind, BaseException)
	)
	kinds = tuple(
		kind
		for kind in (
			getattr(db, "OperationalError", None),
			getattr(db, "InternalError", None),
			getattr(db, "InterfaceError", None),
		)
		if isinstance(kind, type) and issubclass(kind, BaseException)
	)
	missing = getattr(db, "is_missing_table_or_column", None)
	seen = set()
	while exc is not None and id(exc) not in seen:
		seen.add(id(exc))
		if wrapped and isinstance(exc, wrapped):
			return True
		for name in ("is_deadlocked", "is_timedout"):
			check = getattr(db, name, None)
			try:
				if callable(check) and check(exc):
					return True
			except Exception:
				pass
		if kinds and isinstance(exc, kinds):
			try:
				return not (callable(missing) and missing(exc))
			except Exception:
				return True
		exc = exc.__cause__ or exc.__context__
	return False


def before_task_validate(doc, method=None):
	"""Task ``before_validate`` doc_event (hooks.py, v1.742.0): :func:`widen_for_task` before ERPNext's
	own ``validate`` judges the task against its project's window. Defensive, as doc_events here must
	be (they fire during ERPNext's own test bootstrap, before this app's fields exist): attributes are
	read with ``getattr``, and a widening that fails for any other reason (a missing column, a bad
	value) is logged and the save goes on, to be judged by ERPNext as it always was.

	**Except a failure of the database itself** (:func:`_database_failed`), which is raised: after a
	deadlock or a lock-wait timeout InnoDB has rolled the whole transaction back, and carrying on would
	commit only what the request writes afterwards, so a planner Publish would silently lose part of
	its batch. Raised, the request fails whole and Frappe's own retry or the person's next try starts
	clean, as the Gantt's call (which never caught anything) always did."""
	try:
		if getattr(doc, "doctype", None) != "Task" or not getattr(doc, "project", None):
			return
		widen_for_task(doc)
	except Exception as exc:
		if _database_failed(exc):
			raise
		try:
			frappe.log_error(
				title="Task: the project's window could not be widened", message=frappe.get_traceback()
			)
		except Exception:
			pass
