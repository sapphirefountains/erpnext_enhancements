"""The Visit Review page's backend: every visit waiting for review, and the two decisions on it.

TASK-2026-02474 (A1 of the 2026-10-12 Maintenance tools plan). Reviewers (Lisa, James, Nik
and Clegg, who hold **Maintenance Reviewer**) had no list of what was waiting, only the
review emails and the raw record list. The page ``sapphire_maintenance/page/visit_review``
(``/desk/visit-review``) shows the queue oldest first, and ``/desk/visit-review/<visit>``
one visit with every answer and its two decisions.

* **Same words as the email.** A visit's answers are ``review_digest.build_context``, the
  context the review email is rendered from, so the page and the email cannot disagree.
  That includes each reading's value at the site's previous approved visit
  (``sapphire_maintenance/visit_compare.py``, A3).
* **Through the workflow, as the reviewer.** *Approve & Submit* and *Send Back* are applied
  with ``frappe.model.workflow.apply_workflow`` in the reviewer's own session, so the
  workflow's roles and its self-approval rule hold as on the desk form. The transitions are
  found by where they lead (to a submitted state; back to the first state), not by their
  labels.
* **Nobody approves their own visit here.** The workflow's rule looks only at who *created*
  the record, and the nightly scheduler creates most visits, so their owner is Administrator
  and the person who did the work is the ``technician``. On this page a reviewer who is the
  visit's technician or on its crew is offered no Approve either, and ``approve_visit``
  refuses them (:func:`did_the_visit`). Another reviewer approves it. The desk form still
  applies only the workflow's own rule.
* **Send Back needs a note.** It goes on the visit's timeline and is emailed to the
  technician with their answers (``review_digest.send_sent_back``).
* **Nothing stale is decided.** Both decisions require the ``modified`` the page showed, and
  a visit changed since then (the technician still editing, another reviewer) is refused with
  a reload, never approved as something the reviewer did not see.
* **Missed visits ride along.** The queue answer carries ``missed_visits.missed_visits()``
  (A2), the same rows as the Missed Maintenance Visits report.

Role-checked here as well as on the Page, since a whitelisted method is callable directly:
Maintenance Reviewer, Maintenance Supervisor, System Manager. A supervisor who is not a
reviewer sees the queue but is offered no decision, because the workflow gives them none.
"""

import frappe
from frappe import _
from frappe.utils import cint, escape_html, get_datetime, getdate, nowdate

from erpnext_enhancements.sapphire_maintenance import missed_visits, review_digest

DOCTYPE = "Sapphire Maintenance Record"
REVIEW_STATE = "Pending Review"
REVIEW_ROLES = frozenset({"Maintenance Reviewer", "Maintenance Supervisor", "System Manager"})
#: Generous: the queue is a handful of visits a day, and a page that silently stopped at a
#: limit would read as an empty queue.
QUEUE_LIMIT = 200
NOTE_LIMIT = 2000


@frappe.whitelist()
def get_review_queue():
	"""Every visit waiting for review, oldest first, plus the missed and stale visits."""
	_check_access()
	today = getdate(nowdate())
	waiting = []
	for name, since in _queue():
		doc = frappe.get_doc(DOCTYPE, name)
		waiting.append(_queue_row(doc, since, today))

	missed, missed_failed = [], False
	try:
		missed = [_missed_row(row) for row in missed_visits.missed_visits(today)]
	except Exception:
		frappe.log_error(title="Visit Review: missed visits not listed", message=frappe.get_traceback())
		missed_failed = True

	return {
		"waiting": waiting,
		"missed": missed,
		"missed_failed": missed_failed,
		# The workflow's transitions name Maintenance Reviewer; nobody else gets a decision.
		"can_review": "Maintenance Reviewer" in frappe.get_roles(),
	}


@frappe.whitelist()
def get_review_visit(record):
	"""One visit: every answer, what this reviewer may decide, and where it sits in the queue."""
	_check_access()
	doc = _get(record)
	context = review_digest.build_context(doc)
	approve, send_back, blocked = _actions(doc)
	names = [name for name, _since in _queue()]
	index = names.index(doc.name) if doc.name in names else -1
	others = [name for name in names if name != doc.name]
	return {
		"name": doc.name,
		"state": doc.get("workflow_state") or "",
		"docstatus": cint(doc.docstatus),
		"modified": str(doc.modified),
		"waiting": _is_waiting(doc),
		"can_approve": bool(approve),
		"can_send_back": bool(send_back),
		"approve_blocked": blocked,
		"context": {
			key: context.get(key)
			for key in (
				"site",
				"visit_date",
				"technician_name",
				"url",
				"warnings",
				"summary",
				"blocks",
				"notes_heading",
				"notes",
				"fountain_photo",
				"first_visit",
			)
		},
		"position": index + 1 if index >= 0 else None,
		"total": len(names),
		"next": _after(names, index) if index >= 0 else (others[0] if others else None),
		"previous": names[index - 1] if index > 0 else None,
	}


@frappe.whitelist(methods=["POST"])
def approve_visit(record, modified=None):
	"""Approve & Submit, as the reviewer. The approval chain runs on submit, as from the form."""
	_check_access()
	doc = _get(record)
	_check_waiting(doc)
	_check_unchanged(doc, modified)
	approve, _send_back, blocked = _actions(doc)
	if not approve:
		frappe.throw(blocked or _("You cannot approve visits."), frappe.PermissionError)

	from frappe.model.workflow import apply_workflow

	apply_workflow(doc, approve.get("action"))
	return _after_decision(record)


@frappe.whitelist(methods=["POST"])
def send_back_visit(record, note=None, modified=None):
	"""Send Back to the technician, with a note on the timeline and in their email."""
	_check_access()
	note = (note or "").strip()
	if not note:
		frappe.throw(_("Say what needs fixing. The note goes to the technician."))
	if len(note) > NOTE_LIMIT:
		frappe.throw(_("Keep the note under {0} characters.").format(NOTE_LIMIT))
	doc = _get(record)
	_check_waiting(doc)
	_check_unchanged(doc, modified)
	_approve, send_back, _blocked = _actions(doc)
	if not send_back:
		frappe.throw(_("You cannot send visits back."), frappe.PermissionError)

	from frappe.model.workflow import apply_workflow

	apply_workflow(doc, send_back.get("action"))
	doc = frappe.get_doc(DOCTYPE, record)
	doc.add_comment("Comment", _("Sent back for changes: {0}").format(escape_html(note)))
	emailed = review_digest.send_sent_back(doc, note, frappe.session.user)
	result = _after_decision(record)
	result["emailed"] = emailed
	return result


# ---------------------------------------------------------------------- the queue


def _queue():
	"""``[(name, waiting since)]`` for every visit in Pending Review, longest waiting first."""
	rows = frappe.get_all(
		DOCTYPE,
		filters={"docstatus": 0, "workflow_state": REVIEW_STATE},
		fields=["name", "modified"],
		order_by="modified asc",
		limit=QUEUE_LIMIT,
	)
	since = _waiting_since([row.get("name") for row in rows])
	queue = [
		(row.get("name"), since.get(row.get("name")) or get_datetime(row.get("modified"))) for row in rows
	]
	queue.sort(key=lambda pair: (pair[1], pair[0]))
	return queue


def _waiting_since(names):
	"""When each visit last went into Pending Review.

	``apply_workflow`` leaves a "Workflow" Comment naming the state it moved to, from the
	Visit Wizard's Finish and the desk form's button alike. The newest one for Pending Review
	is when this wait began; a visit sent back and resubmitted waits from the resubmission.
	A visit with none (moved some other way) falls back to its ``modified``.
	"""
	if not names:
		return {}
	since = {}
	for row in frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": DOCTYPE,
			"reference_name": ["in", names],
			"comment_type": "Workflow",
			"content": REVIEW_STATE,
		},
		fields=["reference_name", "creation"],
		order_by="creation desc",
	):
		since.setdefault(row.get("reference_name"), get_datetime(row.get("creation")))
	return since


def _queue_row(doc, since, today):
	try:
		context = review_digest.build_context(doc)
	except Exception:
		frappe.log_error(title="Visit Review: queue row not built", message=frappe.get_traceback())
		context = {}
	summary = dict(context.get("summary") or [])
	return {
		"name": doc.name,
		"site": context.get("site") or doc.get("project") or doc.name,
		"visit_date": context.get("visit_date") or "",
		"visit_label": doc.get("visit_label") or "",
		"technician_name": context.get("technician_name") or doc.get("technician") or "",
		"crew": summary.get(_("Crew")) or "",
		"waiting_since": str(since),
		"days_waiting": max((today - getdate(since)).days, 0),
		"completion": cint(doc.get("completion_percent")),
		"out_of_range": context.get("out_of_range") or [],
		"unanswered": context.get("unanswered") or [],
		"unexplained": context.get("unexplained") or [],
		"photo_count": cint(context.get("photo_count")),
		"first_visit": bool(context.get("first_visit")),
	}


def _missed_row(row):
	return {
		"kind": row["kind"],
		"site": row["site"] or "",
		"fountain": row["fountain"] or "",
		"visit_label": row["visit_label"] or "",
		"due_date": str(row["due_date"]),
		"days_late": row["days_late"],
		"technician_name": row["technician_name"] or "",
		"completion": cint(row["completion"]),
		"record": row["record"],
		"contract": row["contract"],
	}


def _after(names, index):
	"""The next visit waiting after ``index``, wrapping to the first."""
	others = names[index + 1 :] + names[:index]
	return others[0] if others else None


def _after_decision(record):
	"""Where the visit stands now, and the visit to go to next (the oldest still waiting)."""
	state, docstatus = frappe.db.get_value(DOCTYPE, record, ["workflow_state", "docstatus"]) or ("", 0)
	names = [name for name, _since in _queue() if name != record]
	return {
		"name": record,
		"state": state or "",
		"docstatus": cint(docstatus),
		"next": names[0] if names else None,
		"remaining": len(names),
	}


# ---------------------------------------------------------------------- guards


def _check_access():
	if not REVIEW_ROLES & set(frappe.get_roles()):
		frappe.throw(
			_("The Visit Review page is for maintenance reviewers and supervisors."), frappe.PermissionError
		)


def _get(record):
	if not record or not frappe.db.exists(DOCTYPE, record):
		frappe.throw(_("Visit {0} was not found.").format(record), frappe.DoesNotExistError)
	doc = frappe.get_doc(DOCTYPE, record)
	doc.check_permission("read")
	return doc


def _is_waiting(doc):
	return cint(doc.docstatus) == 0 and (doc.get("workflow_state") or "") == REVIEW_STATE


def _check_waiting(doc):
	if not _is_waiting(doc):
		frappe.throw(
			_("This visit is no longer waiting for review. It is {0}.").format(
				doc.get("workflow_state") or _("a draft")
			)
		)


def _check_unchanged(doc, modified):
	"""Refuse a decision on a version of the visit the reviewer was not shown, or not named."""
	if not modified:
		frappe.throw(_("Reload the visit and decide again: the version you saw was not sent."))
	if get_datetime(modified) != get_datetime(doc.modified):
		frappe.throw(_("This visit changed since you opened it. Reload it to see the latest answers."))


def did_the_visit(doc, user):
	"""Is ``user`` the visit's technician or on its crew? Such a reviewer does not approve it.

	Found in review of PR #1204: ``has_approval_access`` compares the user with ``owner`` only,
	and a scheduler-drafted visit's owner is Administrator, so the workflow alone would let a
	reviewer who filled the visit in approve it (``patches/seed_maintenance_reviewer_role.py``
	describes the same gap).
	"""
	people = {doc.get("technician")} | {row.get("user") for row in doc.get("crew") or []}
	return bool(user) and user in (people - {None, ""})


def _actions(doc):
	"""``(approve, send_back, blocked)``: the transitions this user may apply, and why not.

	``get_transitions`` filters by role only; ``apply_workflow`` then refuses a transition
	without ``allow_self_approval`` when the user created the record, so the same test is
	applied here and the page never offers a button that would only fail. Approving is also
	withheld from the visit's own technician and crew (:func:`did_the_visit`). ``blocked``
	says why when approving is the one thing in the way.
	"""
	if not _is_waiting(doc):
		return None, None, None
	from frappe.model.workflow import get_transitions, get_workflow, get_workflow_name, has_approval_access

	if not get_workflow_name(DOCTYPE):
		return None, None, None
	workflow = get_workflow(DOCTYPE)
	first = workflow.states[0].state if workflow.states else None
	submitted = {state.state for state in workflow.states if cint(state.doc_status) == 1}
	user = frappe.session.user
	did_it = did_the_visit(doc, user)
	approve = send_back = blocked = None
	for transition in get_transitions(doc, workflow):
		allowed = has_approval_access(user, doc, transition)
		if transition.get("next_state") in submitted and approve is None:
			if not allowed:
				blocked = _("You started this visit, so another reviewer approves it.")
			elif did_it:
				blocked = _("You did this visit, so another reviewer approves it.")
			else:
				approve = transition
		elif transition.get("next_state") == first and send_back is None and allowed:
			send_back = transition
	return approve, send_back, blocked
