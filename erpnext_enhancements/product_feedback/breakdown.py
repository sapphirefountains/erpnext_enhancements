# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The background worker that asks Triton for a work breakdown, and the sweeper behind it.

Answers one question: *how does an approved request become a proposal a human can edit,
without anything reaching a live Project on the way?*

The split is the same one ``api/training_ai.py`` uses and is the whole design: **this module
writes nothing but the proposal.** It never creates a ``Task``. ``api.feedback.create_tasks``
does that, after a reviewer has edited and confirmed the rows, and that call is what stamps a
named person against the result.

--------------------------------------------------------------------------------------
Durability without a second queue table
--------------------------------------------------------------------------------------

The production deploy issues ``FLUSHDB`` against the queue Redis, so an ordinary successful
deploy destroys every job that was enqueued and had not yet run — silently, because
``enqueue`` already returned. ``Chat Relay Job`` exists because of that.

This feature does not need one, and the reason is worth stating so nobody adds it later.
**The status is the outbox.** A request sitting in ``Approved`` with no proposal *is* a lost
job, it is visible in the review queue, and :func:`sweep_stalled_breakdowns` re-drives it on
the hour. A durable outbox row is for work a human believes already happened; this is work a
human is waiting on and can also re-drive with a button.

**Double-running is harmless and is not guarded against.** Two workers that both read
``Approved`` produce two equivalent proposals, the second overwriting the first — no ``Task``
is created either way, so the cost is tokens rather than damage. ``deduplicate=True`` would
be the obvious fix and is a trap: it drops the new enqueue while an existing job is QUEUED
**or STARTED**, so a running job swallows exactly the re-run that was meant to replace it.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint, now_datetime

from erpnext_enhancements.product_feedback.codemap import build_codemap
from erpnext_enhancements.product_feedback.doctype.product_feedback_settings.product_feedback_settings import (
	get_settings,
)
from erpnext_enhancements.product_feedback.proposal import Breakdown, parse_breakdown
from erpnext_enhancements.product_feedback.states import RequestState

#: Recorded on the ``AI Model Usage`` row so work breakdowns are accounted for separately
#: from email/SMS drafting and training drafting.
FEATURE = "feedback_work_breakdown"

#: RQ job timeout. Comfortably above ``Product Feedback Settings.breakdown_timeout`` so the
#: HTTP read is what gives up first and writes a legible error, rather than the worker being
#: killed mid-call and leaving the request in ``Approved`` for the sweeper.
JOB_TIMEOUT = 600

#: A request stuck in ``Approved`` for longer than this had its job destroyed by a deploy (or
#: by a worker that died). Long enough that a slow-but-live call is never re-driven underneath
#: itself.
STALE_AFTER_MINUTES = 15

#: Statuses that mean a Task is no longer live work. Excluded from the duplicate scan: a
#: request that matches something already shipped is not a duplicate, it is a regression, and
#: telling a reviewer to close it against a finished task is the wrong advice.
#:
#: **Spelled ``Canceled``, with one l.** That is what ``Task.status`` actually offers on this
#: site — read from ``frappe.get_meta("Task")`` on production, 2026-08-17: ``Open, Working,
#: Invoiced, Completed, Canceled, Pending Review, Overdue, Template``. The British spelling is
#: the reflex and it is silently wrong here: ``status not in (…"Cancelled"…)`` matches every
#: canceled row, so abandoned work would be offered to a reviewer as a live duplicate. There
#: is one such row on PRJ-00580 today. This cannot be pinned by a bench-free test — ``Task`` is
#: an ERPNext core doctype and its JSON is not in this repo — so the verification is dated
#: instead, per the house rule for claims about the live system.
#:
#: ``Template`` is scaffolding rather than work. ``Invoiced`` is downstream of ``Completed``;
#: neither board carries one today, so it is included on reasoning rather than on evidence.
CLOSED_TASK_STATUSES = ("Completed", "Canceled", "Invoiced", "Template")

#: The Project's own ``notes`` field carries the house methodology for its task tree —
#: PRJ-00755's says "one group task per workstream, leaf tasks whose subjects state the
#: outcome or the defect, HTML descriptions opening with a bolded status/dependency line".
#: That is the style guide, it is already written, and it lives where the person who wrote it
#: will keep editing it. Sending it beats restating it in a prompt constant that drifts.
MAX_METHODOLOGY_CHARS = 4000

MAX_DESCRIPTION_CHARS = 20000


def enqueue_breakdown(request_name: str) -> None:
	"""Queue a breakdown for an already-``Approved`` request.

	``enqueue_after_commit`` because the worker reads the row this transaction just wrote;
	without it the job can start against a snapshot that has no ``Approved`` status yet and
	refuse itself. The job is given a **name**, never a Document — ``frappe.enqueue`` pickles
	its kwargs, so a Document in a job is a class-definition dependency between the process
	that queued it and the one that runs it, which a deploy breaks.
	"""
	frappe.db.set_value(
		"Enhancement Request",
		request_name,
		"breakdown_requested_at",
		now_datetime(),
		update_modified=False,
	)
	frappe.enqueue(
		"erpnext_enhancements.product_feedback.breakdown.run_breakdown",
		queue="long",
		timeout=JOB_TIMEOUT,
		enqueue_after_commit=True,
		request_name=request_name,
	)


def run_breakdown(request_name: str) -> None:
	"""Ask Triton to break one request down, and write the proposal onto it.

	Never raises. A bare re-raise out of a background job publishes the frame locals to the
	Error Log — including, here, the whole request payload — and this repo has already paid
	for that once; every exit writes a status a human can see instead.
	"""
	try:
		row = frappe.db.get_value(
			"Enhancement Request",
			request_name,
			["name", "status", "decided_by", "target_erpnext", "target_triton"],
			as_dict=True,
		)
	except Exception:
		# A dead connection at the very first read. Nothing can be written to say so, and
		# `log_error` would re-raise on the same connection; the sweeper is the recovery.
		return

	if not row:
		return
	if (row.get("status") or "") != RequestState.APPROVED:
		# Already answered, re-approved-and-answered, or closed while queued. Not an error.
		return

	settings = get_settings()
	targets = _targets(row)
	if not targets:
		_fail(request_name, "Neither ERPNext nor Triton was selected as a target, so there is nothing to plan.")
		return

	target_projects = {key: settings[f"{key}_project"] for key in targets}

	try:
		known_tasks = open_tasks(
			list(target_projects.values()), limit=cint(settings["duplicate_scan_limit"])
		)
		payload = build_payload(
			request_name,
			target_projects=target_projects,
			known_tasks=known_tasks,
			max_tasks=cint(settings["max_proposed_tasks"]),
		)
	except Exception:
		_fail(request_name, "Could not assemble the request payload; see the Error Log.")
		_log("Enhancement Request payload assembly failed")
		return

	from erpnext_enhancements.product_feedback import triton_client

	try:
		body = triton_client.request_breakdown(
			reviewer=row.get("decided_by") or frappe.session.user,
			payload=payload,
			timeout=cint(settings["breakdown_timeout"]),
		)
	except triton_client.TritonUnavailable as exc:
		# `str(exc)` here is safe by construction: `triton_client` builds its messages from a
		# status, a path and an enum, and never from a response body. See its `_error_fields`.
		_fail(request_name, str(exc))
		return
	except Exception:
		_fail(request_name, "The Triton call failed unexpectedly; see the Error Log.")
		_log("Enhancement Request breakdown call failed")
		return

	breakdown = parse_breakdown(
		body,
		target_projects=target_projects,
		known_tasks=known_tasks,
		max_tasks=cint(settings["max_proposed_tasks"]),
	)
	_record_usage(body, breakdown.model)

	# **No tasks is not automatically a failure.** The prompt explicitly tells the model that a
	# request already covered by open work should come back as duplicates and few or no tasks —
	# and then the first version of this recorded exactly that outcome as `Breakdown Failed`,
	# throwing away the duplicate list that was the whole answer. A proposal with duplicates
	# and nothing else is a result a reviewer can act on: they close the request against the
	# task it duplicates.
	#
	# Only a response with nothing in it at all is a failure, and it is worth being loud about
	# why: `{"summary": "", "tasks": [], "duplicates": []}` is a degenerate generation, not a
	# considered "nothing to do", and the two are indistinguishable without the model's own
	# finish reason. Triton now reports that; it is carried through here.
	if breakdown.is_empty and not breakdown.duplicates:
		reasons = " ".join(breakdown.dropped)
		detail = (body or {}).get("finish_reason") or ""
		summary = (breakdown.summary or "").strip()
		if not reasons and not summary:
			reasons = (
				"The model returned an empty plan — no tasks, no duplicates, no summary. That is "
				"a generation failure rather than a judgement that no work is needed."
				+ (f" Model finish reason: {detail}." if detail else "")
				+ " Re-running usually clears it."
			)
		_fail(request_name, reasons or summary)
		return

	try:
		_apply(request_name, breakdown)
	except Exception:
		_fail(request_name, "Could not save the proposal; see the Error Log.")
		_log("Enhancement Request proposal save failed")
		return

	_notify_reviewer(request_name, ready=True)


def sweep_stalled_breakdowns() -> None:
	"""Re-drive approvals whose job never ran. Registered hourly in ``hooks.py``.

	This is the whole durability story — see the module docstring. It looks for the state a
	lost job leaves behind rather than for a job that is missing, because a job that is
	missing is not observable: ``FLUSHDB`` removes it without a trace and ``enqueue`` already
	reported success.
	"""
	cutoff = frappe.utils.add_to_date(now_datetime(), minutes=-STALE_AFTER_MINUTES)
	stalled = frappe.get_all(
		"Enhancement Request",
		filters={
			"status": RequestState.APPROVED.value,
			"breakdown_requested_at": ["<", cutoff],
		},
		pluck="name",
		limit=25,
		order_by="breakdown_requested_at asc",
	)
	for name in stalled:
		try:
			enqueue_breakdown(name)
		except Exception:
			_log(f"Enhancement Request sweeper could not re-enqueue {name}")


# ------------------------------------------------------------------------------ payload


def open_tasks(projects: list[str], *, limit: int) -> dict[str, dict[str, Any]]:
	"""Live tasks on the given Projects, as ``{name: {subject, project, is_group, status}}``.

	Subjects only — never descriptions. The two boards carry ~340 open tasks between them and
	their descriptions run to paragraphs each; sending those would multiply the prompt by two
	orders of magnitude to answer a question ("have we already got this?") that subjects
	answer.

	Ordered most-recently-modified first so a `limit` that does bite drops the stalest rows,
	which are the least likely duplicates of something somebody just noticed.
	"""
	if not projects or limit <= 0:
		return {}

	rows = frappe.get_all(
		"Task",
		filters={
			"project": ["in", projects],
			"status": ["not in", CLOSED_TASK_STATUSES],
		},
		fields=["name", "subject", "project", "is_group", "status", "parent_task"],
		order_by="modified desc",
		limit=limit,
	)
	return {
		row["name"]: {
			"subject": row.get("subject") or "",
			"project": row.get("project") or "",
			"is_group": bool(cint(row.get("is_group"))),
			"status": row.get("status") or "",
			"parent_task": row.get("parent_task") or "",
		}
		for row in rows
	}


def build_payload(
	request_name: str,
	*,
	target_projects: dict[str, str],
	known_tasks: dict[str, dict[str, Any]],
	max_tasks: int,
) -> dict[str, Any]:
	"""The body Triton's ``/api/v1/planning/work-breakdown`` receives.

	ERPNext sends **facts**; Triton owns the prompt. The one piece of style that travels is
	each Project's own ``notes`` field, because that is where the methodology for its task
	tree is already written and where it will keep being edited.
	"""
	doc = frappe.db.get_value(
		"Enhancement Request",
		request_name,
		[
			"name",
			"title",
			"request_type",
			"impact",
			"description",
			"steps_to_reproduce",
			"context_url",
			"context_doctype",
			"context_docname",
			"context_app_version",
		],
		as_dict=True,
	)

	return {
		"request": {
			"name": doc.get("name"),
			"title": doc.get("title") or "",
			"type": doc.get("request_type") or "",
			"impact": doc.get("impact") or "",
			"description": (doc.get("description") or "")[:MAX_DESCRIPTION_CHARS],
			"steps_to_reproduce": (doc.get("steps_to_reproduce") or "")[:MAX_DESCRIPTION_CHARS],
			"context": {
				"url": doc.get("context_url") or "",
				"doctype": doc.get("context_doctype") or "",
				"docname": doc.get("context_docname") or "",
				"app_version": doc.get("context_app_version") or "",
			},
		},
		"targets": sorted(target_projects),
		"projects": {key: _project_brief(key, name) for key, name in target_projects.items()},
		# A map of THIS repo's source, read from the installed app. Triton adds its own for the
		# Triton repo — neither of us can see the other's code, so each side contributes the
		# half it can actually observe. Without this the model plans blind and names modules
		# and files that do not exist. See product_feedback/codemap.py.
		"codebase": {"erpnext": build_codemap()},
		"open_tasks": [
			{
				"name": name,
				"subject": meta["subject"],
				"project": meta["project"],
				"is_group": meta["is_group"],
				"status": meta["status"],
				"parent_task": meta["parent_task"],
			}
			for name, meta in known_tasks.items()
		],
		"max_tasks": max_tasks,
	}


def _project_brief(target: str, project: str) -> dict[str, Any]:
	row = (
		frappe.db.get_value(
			"Project",
			project,
			["name", "project_name", "custom_project_description", "notes"],
			as_dict=True,
		)
		or {}
	)
	return {
		"target": target,
		"id": row.get("name") or project,
		"name": row.get("project_name") or "",
		"description": row.get("custom_project_description") or "",
		# The house style, straight from the board it applies to.
		"methodology": (row.get("notes") or "")[:MAX_METHODOLOGY_CHARS],
	}


def _targets(row: dict[str, Any]) -> list[str]:
	targets = []
	if cint(row.get("target_erpnext")):
		targets.append("erpnext")
	if cint(row.get("target_triton")):
		targets.append("triton")
	return targets


# ------------------------------------------------------------------------------ writing


def _apply(request_name: str, breakdown: Breakdown) -> None:
	"""Replace the proposal on the request and move it to ``Breakdown Ready``.

	The child tables are replaced wholesale rather than appended to, so a re-run leaves one
	proposal rather than two interleaved ones.
	"""
	doc = frappe.get_doc("Enhancement Request", request_name)
	doc.set("proposed_tasks", [])
	for task in breakdown.tasks:
		doc.append(
			"proposed_tasks",
			{
				"include": 1,
				"subject": task.subject,
				"project": task.project,
				"description": task.description,
				"priority": task.priority,
				"expected_hours": task.expected_hours,
				"parent_task": task.parent_task,
				"group_subject": task.group_subject,
				"depends_on_idx": task.depends_on_idx,
			},
		)

	doc.set("duplicate_candidates", [])
	for candidate in breakdown.duplicates:
		doc.append(
			"duplicate_candidates",
			{
				"task": candidate.task,
				"task_subject": candidate.task_subject,
				"confidence": candidate.confidence,
				"why": candidate.why,
			},
		)

	doc.breakdown_summary = breakdown.summary
	doc.breakdown_model = breakdown.model
	# Not an error: the proposal is usable. This is the record of what was thrown away on the
	# way, which is the difference between a thin proposal and a thin model.
	doc.breakdown_error = " ".join(breakdown.dropped)[:1000]
	doc.status = RequestState.BREAKDOWN_READY.value
	doc.save(ignore_permissions=True)
	frappe.db.commit()


def _fail(request_name: str, message: str) -> None:
	"""Move the request to ``Breakdown Failed`` with a legible reason. Never raises.

	``db.set_value`` rather than a ``save()``: this runs on the failure path, where the
	document may be exactly what could not be loaded, and a writer that can fail while
	recording a failure turns a legible error into an empty Error Log.
	"""
	try:
		frappe.db.set_value(
			"Enhancement Request",
			request_name,
			{
				"status": RequestState.BREAKDOWN_FAILED.value,
				"breakdown_error": (message or "")[:1000],
			},
			update_modified=False,
		)
		frappe.db.commit()
	except Exception:
		return
	_notify_reviewer(request_name, ready=False)


def _notify_reviewer(request_name: str, *, ready: bool) -> None:
	"""Tell whoever approved it that the proposal is ready, or that it failed. Never raises."""
	try:
		from erpnext_enhancements.product_feedback import notify

		notify.breakdown_finished(request_name, ready=ready)
	except Exception:
		_log(f"Enhancement Request breakdown notification failed for {request_name}")


def _record_usage(body: Any, model: str) -> None:
	"""Best-effort ``AI Model Usage`` row, mirroring ``api/gemini.py::_record_usage``.

	Token accounting must never fail the breakdown that triggered it, and the switch is the
	same one every other AI feature in this app reads.
	"""
	try:
		enabled = frappe.db.get_single_value("ERPNext Enhancements Settings", "ai_usage_tracking_enabled")
		if enabled is not None and not cint(enabled):
			return

		usage = (body or {}).get("usage") or {}
		if not usage:
			return

		frappe.get_doc(
			{
				"doctype": "AI Model Usage",
				"model": model or "unknown",
				"feature": FEATURE,
				"user": frappe.session.user,
				"prompt_tokens": cint(usage.get("prompt_tokens")),
				"candidates_tokens": cint(usage.get("candidates_tokens")),
				"thoughts_tokens": cint(usage.get("thoughts_tokens")),
				"total_tokens": cint(usage.get("total_tokens")),
				"timestamp": now_datetime(),
			}
		).insert(ignore_permissions=True)
	except Exception:
		_log("AI Model Usage insert failed for a work breakdown")


def _log(title: str) -> None:
	"""Error Log write that cannot itself explode the caller.

	``frappe.log_error`` opens a transaction, so on a dead connection it raises from inside
	the ``except`` that was handling the first failure — which is how a job aborts with an
	empty Error Log. Wrapped for that reason, not for tidiness.
	"""
	try:
		frappe.log_error(frappe.get_traceback(), title[:140])
	except Exception:
		pass
