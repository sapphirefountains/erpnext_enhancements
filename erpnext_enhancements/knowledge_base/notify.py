# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Review to-dos for the Knowledge Base (WI-080 PR 3, ADR 0017 section 2, "Review notices are ToDos").

**A version's open ToDos describe its current state, and nothing else.** Every move a version
makes (``publish.transition``) calls :func:`after_transition`, which closes every open ToDo on it
and then raises the ones the new state needs:

* **In Review**: one ToDo per KB Approver who may approve it (``workflow.reviewers_for``: enabled
  System Users holding KB Approver, less Administrator, Guest, the owner, the submitter, the
  contributors and the AI requester, whom ``approval_problems`` would refuse anyway);
* **Draft, after Request Changes**: one ToDo for its author (the submitter, or its creator), so the
  person who has to act hears about it;
* anything else (Draft after a withdrawal, Published, Superseded, Discarded): none.

So a review ToDo closes the moment the version leaves In Review, whichever way it leaves, and the
old version's ToDos close when a newer one supersedes it. The enabled fixture Notification "New
ToDo Created - Notify Creator and Assignee" (``fixtures/notification.json``) sends the email for
each ToDo raised, through the Email Queue table.

Things this module is careful about:

* **Inline, never enqueued.** A deploy ``FLUSHDB``s the queue redis and silently destroys every
  pending job (CLAUDE.md), so a review notice raised by a job would be lost on the day it mattered.
  Each ToDo is written in the action's own transaction; the email it triggers is an Email Queue
  row, which survives a flush. (Frappe's bell entry, ``notify_assignment``, is its own
  ``enqueue_after_commit`` job: best effort, and nothing here depends on it.)
* **The description is the title and a link, never draft text.** A ToDo's description is readable
  by every System Manager (v16 ``desk/doctype/todo/todo.py:149-172`` exempts any role listed on the
  ToDo DocPerm, and System Manager is), and so by the AI tools that act for them, ``triton@``
  included; v16 also copies it into the "Assigned" Comment on the version (``todo.py:40-52``) and
  into the email. The title is the one piece of a draft that appears, as it does on every list.
  The reviewer's note from Request Changes stays on the version (``review_note``), which only KB
  roles can open, and is never copied here.
* **Written directly, not through** ``frappe.desk.form.assign_to.add``. That function builds the
  ToDo itself, so KB code could not set ``flags.kb_action`` on it, and ``references.guard_todo``
  refuses any other ToDo on a version (decision (b) of PR 3). It would also share the version with
  an assignee who cannot read it (``assign_to.py:106-118``); every assignee here holds KB Approver,
  or is the draft's own author, and can.
* **Never fatal, and never half-written.** Each ToDo is written inside its own savepoint with
  messages muted: a ToDo that cannot be written is rolled back to the savepoint, logged by version
  name and user, and the action goes on (the version still moves; the approvers can still see it
  in the "In review" list). ``frappe.flags.mute_messages`` because ``frappe.throw`` queues its
  message before raising, so a caught failure would still pop a red modal over the author's form.
  A deadlock is the exception: MariaDB has already rolled the whole transaction back, so it is
  re-raised for ``publish.run`` to retry the action from the start.
"""

import html

import frappe
from frappe.utils import get_url_to_form, nowdate

from erpnext_enhancements.knowledge_base import constants, workflow

TODO = "ToDo"
OPEN = "Open"
CLOSED = "Closed"
#: One savepoint name, reused: MariaDB replaces a savepoint of the same name.
SAVEPOINT = "kb_todo"

REVIEW_LEAD = "Review knowledge base draft"
CHANGES_LEAD = "Changes requested on knowledge base draft"


def after_transition(doc, action):
	"""Close every open ToDo on the version, then raise the ones its new state needs. Returns the
	users a ToDo was raised for."""
	close_open_todos(doc.name)
	state = workflow.state_of(doc)
	if state == workflow.IN_REVIEW:
		return raise_review_todos(doc)
	if action == workflow.REQUEST_CHANGES:
		return raise_author_todo(doc)
	return []


def raise_review_todos(doc):
	"""One review ToDo per KB Approver who may approve ``doc``. Returns who was asked."""
	description = todo_description(doc, REVIEW_LEAD)
	asked = []
	for user in workflow.reviewers_for(doc, approver_candidates()):
		if _quietly(_raise, doc, user, description, what=f"raise a review to-do for {user} on {doc.name}"):
			asked.append(user)
	return asked


def raise_author_todo(doc):
	"""The author's ToDo after Request Changes: the submitter, or whoever created it."""
	user = str(doc.get("submitted_by") or doc.get("owner") or "").strip()
	if not user or user in constants.NEVER_APPROVERS:
		return []
	description = todo_description(doc, CHANGES_LEAD)
	raised = _quietly(_raise, doc, user, description, what=f"raise a to-do for {user} on {doc.name}")
	return [user] if raised else []


def close_open_todos(version):
	"""Close every open ToDo on ``version``. Returns how many were closed."""
	names = frappe.get_all(
		TODO,
		filters={"reference_type": constants.VERSION_DOCTYPE, "reference_name": version, "status": OPEN},
		pluck="name",
		order_by="creation asc",
	)
	return sum(1 for name in names if _quietly(_close, name, what=f"close to-do {name} on {version}"))


def approver_candidates():
	"""The enabled System Users holding KB Approver, by name. Role Profile holders are included:
	a profile's roles are written to the user's own ``Has Role`` rows when the profile is added."""
	holders = frappe.get_all(
		"Has Role", filters={"role": constants.APPROVER_ROLE, "parenttype": "User"}, pluck="parent"
	)
	if not holders:
		return []
	return frappe.get_all(
		"User",
		filters={
			"name": ["in", sorted(set(holders))],
			"enabled": 1,
			"user_type": constants.APPROVER_USER_TYPE,
		},
		pluck="name",
		order_by="name asc",
	)


def todo_description(doc, lead):
	"""``lead``, the version's title as a link to it, and its name. Nothing else of the draft: see
	the module docstring. Every piece is escaped, because the title is whatever the author typed and
	the description is HTML in the ToDo, the Comment and the email."""
	title = str(doc.get("title") or "").strip() or doc.name
	url = get_url_to_form(constants.VERSION_DOCTYPE, doc.name)
	return (
		f"{html.escape(lead)}: "
		f'<a href="{html.escape(url, quote=True)}">{html.escape(title)}</a> ({html.escape(doc.name)})'
	)


# ------------------------------------------------------------------ the writes


def _raise(doc, user, description):
	todo = frappe.get_doc(
		{
			"doctype": TODO,
			"allocated_to": user,
			"reference_type": constants.VERSION_DOCTYPE,
			"reference_name": doc.name,
			"description": description,
			"priority": "Medium",
			"status": OPEN,
			"date": nowdate(),
			"assigned_by": frappe.session.user,
		}
	)
	# references.guard_todo refuses a ToDo on a version that KB code did not raise.
	todo.flags.kb_action = True
	todo.insert(ignore_permissions=True)
	try:
		from frappe.desk.form.assign_to import notify_assignment

		notify_assignment(
			todo.assigned_by, user, constants.VERSION_DOCTYPE, doc.name, action="ASSIGN", description=description
		)
	except Exception:
		# The bell is a nicety on top of the ToDo and its email; never a reason to lose either.
		pass


def _close(name):
	todo = frappe.get_doc(TODO, name)
	todo.status = CLOSED
	todo.flags.kb_action = True
	todo.save(ignore_permissions=True)


def _quietly(fn, *args, what):
	"""Run one ToDo write inside a savepoint, with messages muted. True if it was written.

	``what`` names the write for the Error Log ("raise a review to-do for james@... on KBV-00012");
	the traceback is v16's plain one (``get_traceback()``, no frame locals)."""
	muted = frappe.flags.mute_messages
	frappe.flags.mute_messages = True
	frappe.db.savepoint(SAVEPOINT)
	detail = None
	try:
		fn(*args)
	except frappe.QueryDeadlockError:
		raise
	except Exception:
		detail = frappe.get_traceback() or "no traceback"
		frappe.db.rollback(save_point=SAVEPOINT)
	finally:
		frappe.flags.mute_messages = muted
	if detail is not None:
		# Logged outside the except block, so no exception context rides along into the Error Log.
		# Deferred (v1.555.1): a plain log_error is a row in this request's transaction, and a later
		# step of the same action that raises, or publish.run's deadlock retry, rolls it back with
		# everything else (see publish.body_markdown).
		frappe.log_error(
			title="Knowledge base to-do", message=f"Could not {what}.\n\n{detail}", defer_insert=True
		)
		return False
	frappe.db.release_savepoint(SAVEPOINT)
	return True
