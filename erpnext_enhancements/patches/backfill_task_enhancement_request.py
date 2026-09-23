"""Backfill ``Task.custom_enhancement_request`` on the Tasks the feedback pipeline already wrote.

WI-079 slice 1 / ADR 0016 §1. From v1.524.0 ``product_feedback/task_writer.py`` stamps every
Task it creates with the Enhancement Request it came from. This one-shot does the same for the
Tasks it created before the field existed — measured on production on 2026-09-23 as 37: 30 leaves
and 7 groups.

The two kinds come from two different sources, deliberately:

- **Leaves** from ``Enhancement Request Proposed Task.created_task``, which the writer stamps on
  the proposal row when it creates the leaf. That is the writer's own record of what it wrote.
- **Groups** from the origin note ``_origin_note`` writes into every group's description,
  ``<p><b>Raised from ER-…</b>``, cross-checked against a proposal row of that request whose
  ``group_subject`` and ``project`` match the group and which had no ``parent_task`` (the only rows
  that make the writer create a group). A group is **never** traced through its children: on
  production they already hold other requests' leaves (TASK-2026-01584 has leaves from three
  requests, TASK-2026-01638 from five) and hand-written tasks, and two older epics
  (TASK-2026-00872, TASK-2026-01076) hold pipeline leaves without being pipeline Tasks at all.
  An unanchored ``ER-2026-`` match would also catch the hand-written TASK-2026-01641 and 02032,
  which name requests in their prose.

It fills blanks only and never overwrites a stamp. Writes go through ``frappe.db.set_value`` with
``update_modified=False``: a ``save()`` would fire Task's ``on_update`` hooks (the recurring-task
generator, the realtime publish, the project date sync) and bump ``modified``, which reorders the
open-task list the breakdown sends to Triton.

Ordering: patches run **before** fixture sync, so on the deploy that introduces the field the
column does not exist yet. The patch creates the Custom Field itself, with the fixture's exact
definition and ``is_system_generated=False``, and fixture sync adopts the same record (same name)
later in the migrate — the shape of ``backfill_stage_changed_on``. ``tests/test_feedback_task_backlink``
pins the two definitions to each other.

Every step is guarded and nothing here raises: a patch that raises aborts ``bench migrate``, which
on this repo is the deploy. Safe to run twice; the second run finds nothing to stamp.
"""

import re

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field

FIELD = {
	"fieldname": "custom_enhancement_request",
	"fieldtype": "Link",
	"options": "Enhancement Request",
	"label": "Enhancement Request",
	"insert_after": "issue",
	"read_only": 1,
	"no_copy": 1,
	"search_index": 1,
	"description": (
		"The Enhancement Request this Task was created from, stamped by "
		"product_feedback/task_writer.py (ADR 0016)."
	),
}

#: The writer's origin note, anchored at the start of a group's description.
ORIGIN_NOTE = re.compile(r"^<p><b>Raised from (ER-\d{4}-\d+)</b>")


def execute():
	if not _ensure_field():
		return
	leaves = _stamp_leaves()
	groups = _stamp_groups()
	print(f"backfill_task_enhancement_request: stamped {leaves} leaf Task(s) and {groups} group Task(s).")


def _ensure_field() -> bool:
	try:
		if not frappe.db.exists("Custom Field", "Task-custom_enhancement_request"):
			create_custom_field("Task", dict(FIELD), is_system_generated=False)
		return bool(frappe.db.has_column("Task", "custom_enhancement_request"))
	except Exception:
		frappe.log_error(title="backfill_task_enhancement_request: could not create the field")
		print("backfill_task_enhancement_request: could not create the field; nothing stamped.")
		return False


def _stamp_leaves() -> int:
	try:
		rows = frappe.db.sql(
			"""
			select pt.created_task as task, pt.parent as request
			from `tabEnhancement Request Proposed Task` pt
			join `tabTask` t on t.name = pt.created_task
			where pt.parenttype = 'Enhancement Request'
				and pt.parentfield = 'proposed_tasks'
				and coalesce(pt.created_task, '') != ''
				and coalesce(t.custom_enhancement_request, '') = ''
			""",
			as_dict=True,
		)
	except Exception:
		frappe.log_error(title="backfill_task_enhancement_request: leaf query failed")
		return 0

	stamped = 0
	for row in rows:
		try:
			frappe.db.set_value(
				"Task", row.task, "custom_enhancement_request", row.request, update_modified=False
			)
			stamped += 1
		except Exception:
			frappe.log_error(title=f"backfill_task_enhancement_request: could not stamp {row.task}")
	return stamped


def _stamp_groups() -> int:
	try:
		groups = frappe.db.sql(
			"""
			select name, subject, project, description
			from `tabTask`
			where is_group = 1
				and description like %s
				and coalesce(custom_enhancement_request, '') = ''
			""",
			("<p><b>Raised from ER-%",),
			as_dict=True,
		)
	except Exception:
		frappe.log_error(title="backfill_task_enhancement_request: group query failed")
		return 0

	stamped = 0
	for group in groups:
		match = ORIGIN_NOTE.match(group.description or "")
		if not match:
			continue
		request = match.group(1)
		try:
			if not frappe.db.exists("Enhancement Request", request):
				continue
			# The writer made a group only for a row with this subject, on this project, and no
			# parent_task; anything else carrying the note is not the writer's group for it.
			proposed_it = frappe.db.sql(
				"""
				select 1 from `tabEnhancement Request Proposed Task`
				where parent = %s
					and parenttype = 'Enhancement Request'
					and parentfield = 'proposed_tasks'
					and group_subject = %s
					and coalesce(project, '') = %s
					and coalesce(parent_task, '') = ''
				limit 1
				""",
				(request, group.subject or "", group.project or ""),
			)
			if not proposed_it:
				continue
			frappe.db.set_value(
				"Task", group.name, "custom_enhancement_request", request, update_modified=False
			)
			stamped += 1
		except Exception:
			frappe.log_error(title=f"backfill_task_enhancement_request: could not stamp {group.name}")
	return stamped
