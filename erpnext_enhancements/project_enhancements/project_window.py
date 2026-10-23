# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A Project's expected start and end, widened so a Task save that needs it is not refused.

On this site ``Project.expected_start_date`` and ``expected_end_date`` are DERIVED: they are the
earliest task start and the latest task end, recomputed on every Task save and delete
(``script_migrations.task.sync_project_dates_from_tasks``), and the Project form shows them
read-only. ERPNext v16's ``Task.validate_parent_project_dates`` meanwhile refuses a task whose
dates fall outside them (``InvalidDates``). So the commonest edits there are, moving the last task
one day later or the first one day earlier, and every task of a job whose tasks are all in the
past, are refused against a value that the same save is about to recompute.

:func:`widen_for_task` widens the window first, in the same transaction as the save, and never
shrinks it: the sync hook then sets the authoritative value straight after. The Gantt has done this
for its drags since it was written (``api/gantt.py``, which now calls this); the Project Planner
does it for every save and insert it makes, its Publish included (v1.742.0,
``api/project_planner._save`` / ``_insert_doc``). Like the Gantt, it leaves no comment: the window
is derived, so the next save says the same thing anyway.
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
	first = _day(frappe.db.get_value("Project", project, "expected_start_date"))
	last = _day(frappe.db.get_value("Project", project, "expected_end_date"))
	changed = {}
	if start and first and start < first:
		changed["expected_start_date"] = (first, start)
	if end and last and end > last:
		changed["expected_end_date"] = (last, end)
	# One field per call, as the Gantt has always written it.
	for field, (_old, new) in changed.items():
		frappe.db.set_value("Project", project, field, new, update_modified=False)
	return changed


def widen_for_task(doc):
	""":func:`widen` for a Task about to be saved or inserted, from its expected start and end."""
	if (doc.get("doctype") or "Task") != "Task":
		return {}
	return widen(doc.get("project"), doc.get("exp_start_date"), doc.get("exp_end_date"))
