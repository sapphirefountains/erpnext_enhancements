# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The only code in this module that creates a ``Task``. Runs after a human confirms.

Everything upstream — the model, :mod:`product_feedback.breakdown`, the proposal child
table — produces a *suggestion*. This is where suggestions become work, and the call that
reaches it (``api.feedback.create_tasks``) is the human review, in the sense
``api/training_ai.py`` established: the accept call is what stamps a named person against a
model's output.

--------------------------------------------------------------------------------------
Four things it is careful about
--------------------------------------------------------------------------------------

**The Project allowlist is re-checked here.** ``proposal.parse_breakdown`` already restricted
the model to two Projects, but the reviewer's browser sends these rows back and a payload is
not a proposal. A row naming any other Project is refused, not created — this is the last
gate before a write to a live board.

**Permission is checked per Project, once, before anything is written.** ``create_inline_task``
in ``project_dashboard.py`` set the house shape: ``frappe.has_permission("Project", "write",
doc=project)`` first, then insert. A half-created breakdown is worse than a refused one.

**It is idempotent by ``created_task``.** A row that already names a Task is skipped. That
matters because the confirm button is a network call a reviewer can double-click, and because
the partial-failure path below deliberately leaves the successful rows written.

**A failing row does not abort the others.** Same shape as
``api/maintenance_workflow.py::process_maintenance_submission``: each step in its own
``try``, failures collected and reported, nothing rolled back. The alternative — one bad row
discarding eleven good ones — sends the reviewer back to a proposal they have already edited.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint, flt

from erpnext_enhancements.product_feedback.doctype.product_feedback_settings.product_feedback_settings import (
	allowed_projects,
)
from erpnext_enhancements.product_feedback.states import RequestState

#: ``Task.status`` for everything created here. Open, never Working: a task nobody has picked
#: up has not been started, and seeding a board with Working tasks makes the "what is actually
#: in flight" question unanswerable.
NEW_TASK_STATUS = "Open"


class ProjectRefused(frappe.PermissionError):
	"""The caller may not write to a Project one of the rows names."""


def create_tasks_for(request_name: str) -> dict[str, Any]:
	"""Create every included, not-yet-created proposed task on ``request_name``.

	The request must already carry the reviewer's edits — this function reads the child table
	and does not accept rows as an argument, so there is exactly one place the confirmed
	proposal lives and no way for the written tasks to differ from the displayed ones.

	Returns ``{"created": [task names], "failures": [strings], "groups": [task names]}``.
	Raises :class:`ProjectRefused` before writing anything if the caller lacks write
	permission on a Project the proposal names.
	"""
	doc = frappe.get_doc("Enhancement Request", request_name)
	permitted = set(allowed_projects())

	rows = [
		row
		for row in (doc.get("proposed_tasks") or [])
		if cint(row.get("include")) and not (row.get("created_task") or "").strip()
	]
	if not rows:
		return {"created": [], "failures": [], "groups": []}

	projects = {(row.get("project") or "").strip() for row in rows}
	outside = sorted(p for p in projects if p not in permitted)
	if outside:
		# Not a permission problem — a boundary one. Named separately because "you cannot
		# write to PRJ-00123" and "PRJ-00123 is not a board this feature writes to" send a
		# reader looking in completely different places.
		frappe.throw(
			frappe._(
				"These rows name a project this feature does not write to: {0}. Only {1} are "
				"configured in Product Feedback Settings."
			).format(", ".join(outside), ", ".join(sorted(permitted))),
			frappe.ValidationError,
		)

	for project in sorted(projects):
		if not frappe.has_permission("Project", ptype="write", doc=project):
			raise ProjectRefused(
				frappe._("You do not have write permission on {0}.").format(project)
			)

	failures: list[str] = []
	groups = _ensure_groups(rows, doc, failures)
	created = _create_leaves(rows, groups, failures)
	_link_dependencies(doc, rows, failures)

	# `Tasks Created` is terminal, so it is only reached when there is nothing left to
	# create. A partial run stays in `Breakdown Ready` — otherwise the first failed row
	# would close the request against a proposal that was never fully written, and the
	# retry the reviewer needs would be refused by the transition table.
	outstanding = [
		row
		for row in (doc.get("proposed_tasks") or [])
		if cint(row.get("include")) and not (row.get("created_task") or "").strip()
	]
	complete = not outstanding
	if complete:
		doc.status = RequestState.TASKS_CREATED.value

	doc.save(ignore_permissions=True)
	return {
		"created": created,
		"failures": failures,
		"groups": sorted(set(groups.values())),
		"complete": complete,
		"outstanding": len(outstanding),
	}


# ------------------------------------------------------------------------------- groups


def _ensure_groups(
	rows: list[Any],
	doc: Any,
	failures: list[str],
) -> dict[tuple[str, str], str]:
	"""One group ``Task`` per ``(project, group_subject)`` that needs one.

	Only for rows with no ``parent_task``. A row the model nested under an existing epic keeps
	that parent — PRJ-00580 already carries its epics, and adding a new top-level node per
	request would bury them, which is the whole reason ``parent_task`` is preferred upstream.

	The group is created once per distinct subject, so a five-row proposal that all shares one
	``group_subject`` produces one parent rather than five.
	"""
	groups: dict[tuple[str, str], str] = {}
	for row in rows:
		if (row.get("parent_task") or "").strip():
			continue
		subject = (row.get("group_subject") or "").strip()
		if not subject:
			continue
		project = (row.get("project") or "").strip()
		key = (project, subject)
		if key in groups:
			continue
		try:
			group = frappe.get_doc(
				{
					"doctype": "Task",
					"subject": subject,
					"project": project,
					"status": NEW_TASK_STATUS,
					"is_group": 1,
					"description": _origin_note(doc),
				}
			)
			group.insert(ignore_permissions=True)
			groups[key] = group.name
		except Exception:
			failures.append(f"Could not create the group task '{subject}' on {project}.")
			_log(f"Enhancement Request group task failed on {project}")
	return groups


# ------------------------------------------------------------------------------- leaves


def _create_leaves(
	rows: list[Any],
	groups: dict[tuple[str, str], str],
	failures: list[str],
) -> list[str]:
	"""Create one ``Task`` per row and stamp ``created_task`` back onto it.

	The stamp is written to the in-memory child row; the caller saves the request once at the
	end. Writing it per row would mean a save per task on a document whose ``validate`` runs
	a transition check each time.
	"""
	created: list[str] = []
	for row in rows:
		project = (row.get("project") or "").strip()
		parent = (row.get("parent_task") or "").strip() or groups.get(
			(project, (row.get("group_subject") or "").strip()), ""
		)
		try:
			task = frappe.get_doc(
				{
					"doctype": "Task",
					"subject": (row.get("subject") or "").strip(),
					"project": project,
					"status": NEW_TASK_STATUS,
					"priority": row.get("priority") or "Medium",
					"description": row.get("description") or "",
					"expected_time": flt(row.get("expected_hours")),
					"parent_task": parent or None,
				}
			)
			task.insert(ignore_permissions=True)
			row.created_task = task.name
			created.append(task.name)
		except Exception:
			subject = (row.get("subject") or "").strip() or "(no subject)"
			failures.append(f"Could not create '{subject}' on {project}.")
			_log(f"Enhancement Request task creation failed on {project}")
	return created


# -------------------------------------------------------------------------- dependencies


def _link_dependencies(doc: Any, rows: list[Any], failures: list[str]) -> None:
	"""Turn ``depends_on_idx`` into ``Task Depends On`` rows, once every task exists.

	Resolved against the **full** proposal, not just the rows created in this pass: a re-run
	after a partial failure must be able to link a new task to one written earlier.

	Three refusals, matching what ``project_dashboard.add_task_dependency`` enforces for a
	human doing this by hand — self-dependency, cross-project, and a target that does not
	exist. Cycles were already broken in ``proposal._resolve_dependencies``; this is the
	backstop for a reviewer who edited the numbers in the browser.
	"""
	all_rows = list(doc.get("proposed_tasks") or [])
	by_index = {index: row for index, row in enumerate(all_rows, start=1)}
	touched = {(row.get("created_task") or "").strip() for row in rows}

	for row in all_rows:
		task_name = (row.get("created_task") or "").strip()
		if not task_name or task_name not in touched:
			continue
		dependency_index = cint(row.get("depends_on_idx"))
		if not dependency_index:
			continue

		target_row = by_index.get(dependency_index)
		if target_row is None:
			continue
		target = (target_row.get("created_task") or "").strip()
		if not target or target == task_name:
			continue
		if (target_row.get("project") or "") != (row.get("project") or ""):
			failures.append(
				f"Dependency on {target} skipped: it is on a different project from {task_name}."
			)
			continue

		try:
			task = frappe.get_doc("Task", task_name)
			if any((d.task or "") == target for d in (task.get("depends_on") or [])):
				continue
			task.append("depends_on", {"task": target})
			task.save(ignore_permissions=True)
		except Exception:
			failures.append(f"Could not link {task_name} to depend on {target}.")
			_log(f"Enhancement Request dependency link failed for {task_name}")


# ------------------------------------------------------------------------------- shared


def _origin_note(doc: Any) -> str:
	"""The one-line provenance every generated task carries on its group.

	A task tree that appeared overnight with no explanation is one nobody trusts. The request
	id is the handle back to who asked and why.
	"""
	requester = frappe.utils.escape_html(
		frappe.db.get_value("User", doc.get("requested_by"), "full_name") or doc.get("requested_by") or ""
	)
	return (
		f"<p><b>Raised from {frappe.utils.escape_html(doc.name)}</b>"
		f" — an enhancement request filed by {requester}"
		f" and approved by {frappe.utils.escape_html(doc.get('decided_by') or '')}.</p>"
	)


def _log(title: str) -> None:
	"""Error Log write that cannot itself explode the caller."""
	try:
		frappe.log_error(frappe.get_traceback(), title[:140])
	except Exception:
		pass
