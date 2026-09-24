# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The only code in this module that creates a ``Task``. Runs after a human confirms.

It is also the only code in the app that changes a feedback Task's status on its own:
:func:`mark_shipped`, the second writer, moves a Task a release shipped to ``Pending Review``
(WI-079 slice 4, ADR 0016 §5). People, AI clients through the gate, and ERPNext's own overdue
job still change it too.

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

import json
import re
from typing import Any

import frappe
from frappe.utils import add_days, cint, flt, today

from erpnext_enhancements.product_feedback.doctype.product_feedback_settings.product_feedback_settings import (
	allowed_projects,
)
from erpnext_enhancements.product_feedback.states import RequestState

#: ``Task.status`` for everything created here. Open, never Working: a task nobody has picked
#: up has not been started, and seeding a board with Working tasks makes the "what is actually
#: in flight" question unanswerable.
NEW_TASK_STATUS = "Open"

#: The only statuses :func:`mark_shipped` moves a Task from. Everything else is left alone,
#: and that is what makes replaying the whole CHANGELOG harmless: ``Completed``, both
#: spellings of cancelled (this site's Property Setter offers ``Canceled``, ERPNext v16's own
#: options say ``Cancelled``), ``Invoiced``, ``Template``, and ``Pending Review`` itself, so a
#: Task already moved is never moved again. An allowlist, so a status added later is skipped
#: until somebody decides otherwise.
SHIPPABLE_STATUSES = frozenset({"Open", "Working", "Overdue"})

#: An ``Overdue`` Task whose last status a person set was one of these is not shippable.
#: ERPNext v16's daily ``set_tasks_as_overdue`` and ``Task.update_status`` exempt only
#: ``Cancelled`` and ``Completed``, the double-l spelling, so this site's ``Canceled`` and its
#: ``Invoiced`` Tasks are flipped to ``Overdue`` once their expected end passes, and ``Overdue``
#: alone cannot tell that from real overdue work. The flip is a ``db_set``, which writes no
#: ``Version`` row (``frappe/model/document.py`` on ``version-16``: ``db_set`` goes straight to
#: ``frappe.db.set_value``; only ``save`` calls ``save_version``), while a save from the form
#: does, because Task tracks changes (the ``Task-main-track_changes`` Property Setter). A cancel
#: from the Project form's Task Tree does not: its status picker writes with ``set_value`` too.
#: Since v1.531.0 this app's Task override stops the flip at the source
#: (``task_enhancements/doctype/task/task.py``), so this check is the backstop for Tasks flipped
#: before that, and it only sees the ones canceled from the form.
OVERDUE_NOT_SHIPPABLE_AFTER = frozenset({"Canceled", "Cancelled", "Invoiced", "Completed", "Template"})

#: How many of a Task's newest ``Version`` rows are read to find its last status change.
VERSION_LOOKBACK = 20

#: Native to ERPNext's Task (``task.json``), kept by the site's ``Task-status-options``
#: Property Setter. Shipped, not verified: a person sets ``Completed`` (ADR 0016 §5).
SHIPPED_STATUS = "Pending Review"

#: ``review_date`` goes this far out. ERPNext's daily ``set_tasks_as_overdue`` skips a
#: ``Pending Review`` Task only while its ``review_date`` is in the future, and flips it to
#: ``Overdue`` otherwise when its expected end has passed. Two weeks is the review window.
REVIEW_DAYS = 14

#: The Comment every shipped Task gets. :func:`_already_noted` looks for it, so a replay of
#: the same release never moves a Task a person has since reopened.
SHIPPED_NOTE = "Shipped in erpnext_enhancements {version}"


class ProjectRefused(frappe.PermissionError):
	"""The caller may not write to a Project one of the rows names."""


def pending_rows(rows: Any) -> list[Any]:
	"""The proposed-task rows that still owe a Task: included, and not yet stamped.

	One definition, used three times — to decide whether there is anything to write, to
	decide afterwards whether the request may close, and by ``api.feedback.create_tasks`` to
	refuse a confirm that would write nothing. Three inline copies of one predicate is how
	they drift.
	"""
	return [
		row
		for row in (rows or [])
		if cint(row.get("include")) and not (row.get("created_task") or "").strip()
	]


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

	rows = pending_rows(doc.get("proposed_tasks"))
	if not rows:
		# The same keys as the full return at the bottom. The endpoint reads `complete` off
		# this dict, and the first proposal ever confirmed with nothing ticked
		# (ER-2026-458194, a breakdown that proposed zero tasks) turned the missing key
		# into a 500 on the confirm button. `complete` is False: nothing moved, and a
		# request with no work on any board must not read `Tasks Created`.
		return {"created": [], "failures": [], "groups": [], "complete": False, "outstanding": 0}

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
	created = _create_leaves(rows, groups, failures, doc.name)
	_link_dependencies(doc, rows, failures)

	# `Tasks Created` is terminal, so it is only reached when there is nothing left to
	# create. A partial run stays in `Breakdown Ready` — otherwise the first failed row
	# would close the request against a proposal that was never fully written, and the
	# retry the reviewer needs would be refused by the transition table.
	outstanding = pending_rows(doc.get("proposed_tasks"))
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

	Groups carry ``custom_enhancement_request`` as well as leaves (ADR 0016 §1). A group cannot
	be traced back through its children: on production they already hold other requests'
	leaves and hand-written tasks, so the stamp is written here, at creation, or not at all.
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
					"custom_enhancement_request": doc.name,
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
	request_name: str,
) -> list[str]:
	"""Create one ``Task`` per row and stamp ``created_task`` back onto it.

	The stamp is written to the in-memory child row; the caller saves the request once at the
	end. Writing it per row would mean a save per task on a document whose ``validate`` runs
	a transition check each time. The Task itself carries the other direction of the link,
	``custom_enhancement_request``. On a site whose migrate has not yet added that column the
	key is simply dropped (``get_valid_dict`` keeps only real fields), so no guard is needed.
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
					"custom_enhancement_request": request_name,
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


# ------------------------------------------------------------------------------- shipped


def mark_shipped(task_name: str, version: str, refs_line: str = "", requests: Any = None) -> str:
	"""Move one Task a release shipped to ``Pending Review``. Never raises.

	Called by :mod:`product_feedback.release_sync` for each ``TASK-…`` id on a ``Refs:`` line
	of a CHANGELOG section at or below the installed version, with the ``ER-…`` ids on the same
	line as ``requests``. Returns ``"marked"``, ``"skipped:<reason>"`` or
	``"failed:<message>"``; the caller writes the one Error Log.

	It acts only on a Task carrying ``custom_enhancement_request`` (work this pipeline created;
	a ``Refs:`` naming any other Task changes nothing), and only from
	:data:`SHIPPABLE_STATUSES`. It sets ``Pending Review`` and ``review_date``
	:data:`REVIEW_DAYS` days out, and adds a Comment naming the version and the Refs line. It
	never sets ``Completed``: a person closes shipped work.

	Four more skips, each a way a Refs line could otherwise move the wrong Task:

	* **A Task id that does not exist** is ``skipped:no such Task``, never ``failed``: a typo on
	  a Refs line is permanent, and a failure would hold the marker behind it forever.
	* **Another request's Task.** When the line names one or more requests, a Task that belongs
	  to none of them is skipped: a one-digit slip in a Task id lands on a neighbor, and the
	  neighbor is usually another request's. A line that names no request keeps the old rule.
	* **``Overdue`` that was really ``Canceled``** (:data:`OVERDUE_NOT_SHIPPABLE_AFTER`,
	  :func:`_last_status_change`).
	* **Already marked for this version and since reopened by a person**
	  (:func:`_already_noted`), so replaying a release never undoes their decision.

	Saved through ``doc.save(ignore_permissions=True)``, so the Task controller, this app's
	override and the ``doc_events`` run exactly as they do for a person's edit. The save and the
	Comment share one savepoint, so a failure leaves the Task as it was and the next run
	retries it.
	"""
	name = " ".join(str(task_name or "").split())
	version = " ".join(str(version or "").split())
	if not name:
		return "skipped:no task id"
	if not version:
		return "skipped:no version"
	named = [" ".join(str(r).split()) for r in (requests or []) if str(r or "").strip()]

	savepoint = None
	try:
		if not frappe.db.exists("Task", name):
			return "skipped:no such Task"
		try:
			doc = frappe.get_doc("Task", name)
		except frappe.DoesNotExistError:
			# Deleted between the two reads. Still a missing Task, not a failure to retry.
			return "skipped:no such Task"
		# `doc.get`, not the attribute: on a site whose migrate has not added the column yet
		# the key is simply absent, and an absent back-link means "not ours".
		request = (doc.get("custom_enhancement_request") or "").strip()
		if not request:
			return "skipped:not from an Enhancement Request"
		if named and request not in named:
			return f"skipped:belongs to {request}, not on the Refs line"
		status = (doc.get("status") or "").strip()
		if status not in SHIPPABLE_STATUSES:
			return f"skipped:status is {status or 'unset'}"
		if status == "Overdue":
			before = _last_status_change(name)
			if before in OVERDUE_NOT_SHIPPABLE_AFTER:
				return f"skipped:overdue after {before}"
		if _already_noted(name, version):
			return f"skipped:already noted as shipped in {version}"

		savepoint = "ee_mark_shipped"
		frappe.db.savepoint(savepoint)
		doc.status = SHIPPED_STATUS
		doc.review_date = add_days(today(), REVIEW_DAYS)
		doc.save(ignore_permissions=True)
		doc.add_comment("Comment", text=shipped_note(version, refs_line))
		frappe.db.release_savepoint(savepoint)
		return "marked"
	except Exception as exc:
		if savepoint:
			try:
				frappe.db.rollback(save_point=savepoint)
			except Exception:
				pass
		return f"failed:{_describe(exc)}"


def shipped_note(version: str, refs_line: str = "") -> str:
	"""``Shipped in erpnext_enhancements 1.529.0 (Refs: ER-…, TASK-…)``."""
	note = SHIPPED_NOTE.format(version=version)
	refs = " ".join(str(refs_line or "").split())
	return f"{note} ({refs})" if refs else note


def _already_noted(task_name: str, version: str) -> bool:
	"""Does the Task already carry this version's shipped Comment?

	Only asked of a Task in a shippable status, so it matters in one case: a person moved a
	marked Task back to Open or Working, and the same release is processed again (the marker
	stayed behind after a failed run). The version must end where the note does, so 1.52.0
	does not match a note for 1.52.01.
	"""
	prefix = SHIPPED_NOTE.format(version=version)
	contents = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Task",
			"reference_name": task_name,
			"comment_type": "Comment",
			"content": ["like", f"%{prefix}%"],
		},
		pluck="content",
		limit=20,
	)
	pattern = re.compile(re.escape(prefix) + r"(?![\d.])")
	return any(pattern.search(str(content or "")) for content in contents or [])


def _last_status_change(task_name: str) -> str:
	"""The status the newest ``Version`` that changed ``status`` set, or ``""`` for none.

	Asked only of an ``Overdue`` Task. ERPNext's flip to ``Overdue`` is a ``db_set`` and leaves
	no Version (see :data:`OVERDUE_NOT_SHIPPABLE_AFTER`), so this is the last status a save set:
	a person's, or this writer's own. A Version's ``data`` is JSON whose ``changed`` holds
	``[field, old, new]`` entries (``frappe/core/doctype/version/version.py``, ``get_diff``).
	Only the newest :data:`VERSION_LOOKBACK` rows are read, on the ``(ref_doctype, docname)``
	index Version declares; none found means today's rule, and the Task is shipped.
	"""
	rows = frappe.get_all(
		"Version",
		filters={"ref_doctype": "Task", "docname": task_name},
		pluck="data",
		order_by="creation desc",
		limit=VERSION_LOOKBACK,
	)
	for raw in rows or []:
		try:
			diff = json.loads(raw) if isinstance(raw, str) else raw
		except ValueError:
			continue
		if not isinstance(diff, dict):
			continue
		for change in diff.get("changed") or []:
			if isinstance(change, (list, tuple)) and len(change) >= 3 and change[0] == "status":
				return " ".join(str(change[2] or "").split())
	return ""


def _describe(exc: BaseException) -> str:
	"""The exception's class and first 300 characters, never its traceback or frame locals."""
	message = " ".join(str(exc or "").split())[:300]
	return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


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
