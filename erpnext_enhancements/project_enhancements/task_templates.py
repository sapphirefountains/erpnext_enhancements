# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Template Tasks carry their planning into the Tasks made from them (Project Planner Phase 5, P5.1).

A Project created from a Project Template gets its Tasks from ERPNext's
``Project.create_task_from_template`` (v16), which copies the subject, description, dates, weight,
type, group flag, color and priority, and sets ``template_task`` to the template Task it came from.
It knows nothing about this app's planning fields, so every new Task arrived on the Project Planner
with no hours, no crew size and no qualifications, however carefully its template was filled in.

:func:`copy_template_planning` (``Task.before_insert``) fills them in from the template Task:

* ``expected_time`` (the hours estimate the planner books),
* ``custom_crew_size`` (how many people it needs; the Needs crew tray reads it),
* ``custom_required_credentials`` (the qualification rows),
* ``custom_outdoor`` (weather flags, P5.2) and ``custom_customer_visit`` (the customer is told the
  date, P5.4).

Things this module is careful about, some of which look like bugs:

* **It never overwrites.** A field the new Task already has a value for keeps it: 0 hours, an
  empty crew size, an unticked box and no qualification rows are "blank"; anything else was put
  there by whoever created the task and wins.
* **``before_insert``, not ``after_insert``.** The values go in before the row is written, so they
  pass through the Task's own ``validate`` and are saved in the one insert. An ``after_insert``
  copy would need a second write behind the controller's back (or a second save, which re-runs
  every ``on_update`` hook on a half-made task).
* **Only from a real template.** The source must be a Task with ``is_template`` set, which is what
  ERPNext's Project Template links to; a ``template_task`` pointing anywhere else is ignored.
* **Crew is never copied.** Who goes is decided on the planner (Nik declined "suggest a crew").
* **Defensive, and never raises.** ``doc_events`` fire during ERPNext's own test bootstrap, before
  this app's custom fields exist, so every optional column is checked with ``has_column`` first,
  and a failure is logged and the Task still inserts.
"""

import frappe

#: Task scalar fields copied from the template when blank on the new task.
SCALAR_FIELDS = ("expected_time", "custom_crew_size", "custom_outdoor", "custom_customer_visit")
CREDENTIALS_FIELD = "custom_required_credentials"
CREDENTIAL_ROW = "Task Required Credential"


def is_blank(value):
	"""0, 0.0, None, "" and "0" are blank; anything else is a value somebody set."""
	if value is None:
		return True
	if isinstance(value, str):
		text = value.strip()
		if not text:
			return True
		try:
			return float(text) == 0
		except ValueError:
			return False
	try:
		return float(value) == 0
	except (TypeError, ValueError):
		return False


def values_to_copy(template, current):
	"""``{field: value}`` for each scalar field blank on ``current`` and set on ``template``."""
	out = {}
	for field in SCALAR_FIELDS:
		value = (template or {}).get(field)
		if is_blank(value):
			continue
		if is_blank((current or {}).get(field)):
			out[field] = value
	return out


def credentials_to_copy(template_rows, current_rows):
	"""The template's credential types, in order, when the new task has none; else []."""
	if [row for row in current_rows or [] if (row or {}).get("credential_type")]:
		return []
	out = []
	for row in template_rows or []:
		credential = (row or {}).get("credential_type")
		if credential and credential not in out:
			out.append(credential)
	return out


def _columns(columns):
	present = []
	for column in columns:
		try:
			if frappe.db.has_column("Task", column):
				present.append(column)
		except Exception:
			pass
	return present


def _template_row(name):
	fields = _columns(SCALAR_FIELDS)
	rows = frappe.get_all(
		"Task",
		filters={"name": name, "is_template": 1},
		fields=["name", *fields],
		limit_page_length=1,
	)
	return rows[0] if rows else None


def _template_credentials(name):
	try:
		if not frappe.db.exists("DocType", CREDENTIAL_ROW):
			return []
	except Exception:
		return []
	return frappe.get_all(
		CREDENTIAL_ROW,
		filters={"parenttype": "Task", "parent": name, "parentfield": CREDENTIALS_FIELD},
		fields=["credential_type"],
		order_by="idx asc",
		limit_page_length=0,
	)


def copy_template_planning(doc, method=None):
	"""``Task.before_insert``: fill the blank planning fields from the template Task. Never raises."""
	try:
		template = getattr(doc, "template_task", None)
		if not template or getattr(doc, "is_template", None):
			return
		row = _template_row(template)
		if not row:
			return
		current = {field: getattr(doc, field, None) for field in SCALAR_FIELDS}
		for field, value in values_to_copy(row, current).items():
			doc.set(field, value)
		if _has_table_field(doc):
			wanted = credentials_to_copy(
				_template_credentials(template), getattr(doc, CREDENTIALS_FIELD, None) or []
			)
			for credential in wanted:
				doc.append(CREDENTIALS_FIELD, {"credential_type": credential})
	except Exception:
		frappe.log_error(
			title="Project Planner: template planning not copied",
			message=frappe.get_traceback(),
		)


def _has_table_field(doc):
	"""A Table field has no column on the parent, so ``has_column`` cannot answer for it."""
	try:
		return bool(doc.meta.get_field(CREDENTIALS_FIELD))
	except Exception:
		return False
