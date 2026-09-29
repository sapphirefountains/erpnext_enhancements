# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Knowledge Base actions: review, approve and publish, retire (WI-080 PR 3, ADR 0017).

Answers one question: *how does a draft become the text every staff member and every AI tool
reads, with a second person's approval enforced rather than requested?*

| Endpoint | Method | Move | Who |
|---|---|---|---|
| :func:`start_revision` | POST | a published article -> a new Draft (or the open one) | KB Author, KB Approver |
| :func:`submit_for_review` | POST | Draft -> In Review | KB Author, KB Approver |
| :func:`withdraw` | POST | In Review -> Draft | its creator, submitter or a contributor |
| :func:`request_changes` | POST | In Review -> Draft, with a note | a KB Approver who had no hand in it, from a browser |
| :func:`approve_and_publish` | POST | In Review -> Published, the article written | a KB Approver who had no hand in it, from a browser |
| :func:`discard` | POST | Draft -> Discarded | its creator, submitter, a contributor, or a KB Approver |
| :func:`confirm_still_accurate` | POST | the review date restarts | the process owner or a KB Approver, from a browser |
| :func:`retire` | POST | Published -> Retired, with a reason | a KB Approver, from a browser |
| :func:`review_diff` | GET | none: the published text against the draft | KB Author, KB Approver, from a browser |

The rules are ``knowledge_base/workflow.py`` (pure, every branch tested bench-free); the writes are
``knowledge_base/publish.py``, which is the only code that changes a version's ``review_state`` or
writes an article. These endpoints are the doors between them. One plain function is shared with a
second door (PR 6b): :func:`submit_version`, the body of Submit for Review, which the AI drafting tool
(``knowledge_base/ai_draft.py``) also calls when the person who asked confirms a card that submits.
It is not whitelisted.

Things this module is careful about:

* **Every endpoint names its method** (``@frappe.whitelist(methods=[...])``). Every move is POST:
  a GET that changed state would be a link someone could be sent. ``review_diff`` is the one GET,
  and it changes nothing.
* **Each checks, explicitly and in this order, before it writes anything:** that the person holds
  a KB role (Confirm excepted: a process owner need not), that they may read or write the document
  (``check_permission``, which a whitelisted method does not get for free), and that the move is
  one the rules allow (``workflow.*_problems``). A refusal is one sentence naming every broken
  rule (``workflow.refusal``), so the person knows what to fix.
* **The approval path refuses a token.** Approve, Request Changes, Retire and Confirm are
  decisions, and so is reading a draft (``review_diff``): each refuses a request authenticated by
  an API key or an OAuth bearer (how Triton and the MCP connect), a background job, the console,
  and an AI gate card, through ``workflow.signed_in_browser`` and the gate's own flags.
  ``approve_and_publish`` asks ``workflow.approval_problems`` with the approver's ``user_type`` read
  from the User row at that moment (``publish.asker``), before anything is written; the controller
  asks it again inside the submit.
* **What is checked is what is written.** Each action loads the version or article ``for_update``,
  in the transaction that writes it, so the rules read the row as it is, not as the page last saw
  it; and each runs inside ``publish.run``, which starts the whole action again (reads and checks
  included) if MariaDB rolls it back to break a deadlock. See ``publish.py`` on concurrency.
* **No draft text leaves through an answer** except ``review_diff``'s, which only a KB role in a
  browser gets. The others return names, states and who was asked.

Indentation is tabs, the ``.editorconfig`` default for a new file.
"""

import frappe
from frappe import _
from frappe.utils import cstr, get_fullname, now_datetime, to_markdown

from erpnext_enhancements.knowledge_base import constants, content, publish, workflow

ARTICLE = constants.ARTICLE_DOCTYPE
VERSION = constants.VERSION_DOCTYPE

#: The fields ``review_diff`` compares one by one (the body is diffed as text, and the change note
#: belongs to the new version, so it is shown rather than compared).
DIFF_FIELDS = (
	("title", "Title"),
	("department_block", "Department"),
	("kind", "Kind"),
	("summary", "Summary"),
	("keywords", "Keywords"),
	("process_owner", "Process Owner"),
	("review_every_months", "Review Every (Months)"),
)


# ------------------------------------------------------------------ versions


@frappe.whitelist(methods=["POST"])
def start_revision(article):
	"""A new Draft of a published article, copied from its live version; or, when the article
	already has an open version (Draft or In Review), that one. One open version per article."""
	name = _name(article, "article")

	def attempt():
		ask = publish.asker()
		_require_kb_role(ask)
		doc = frappe.get_doc(ARTICLE, name, for_update=True)
		doc.check_permission("read")
		_refuse(doc.name, workflow.START_REVISION, workflow.start_revision_problems(doc, ask.roles))
		# Under the article's row lock, and a locking read, so two people pressing Start Revision at
		# once get the same draft rather than one each.
		open_row = publish.open_version(doc.name, lock=True)
		if open_row:
			return {"version": open_row.get("name"), "created": False, "review_state": open_row.get("review_state")}
		if not frappe.has_permission(VERSION, "create"):
			frappe.throw(_("You cannot create a knowledge base version."), frappe.PermissionError)
		version = publish.start_revision(doc)
		return {"version": version.name, "created": True, "review_state": version.review_state}

	return publish.run(attempt)


@frappe.whitelist(methods=["POST"])
def submit_for_review(version):
	"""Draft -> In Review, and a review ToDo for every KB Approver who may approve it."""
	name = _name(version, "version")

	def attempt():
		ask = publish.asker()
		_require_kb_role(ask)
		doc = _load_version(name)
		return submit_version(doc, ask)

	return publish.run(attempt)


@frappe.whitelist(methods=["POST"])
def withdraw(version):
	"""In Review -> Draft, by the author's side. Its review ToDos close."""
	name = _name(version, "version")

	def attempt():
		ask = publish.asker()
		_require_kb_role(ask)
		doc = _load_version(name)
		_refuse(doc.name, workflow.WITHDRAW, workflow.withdraw_problems(doc, ask.user, ask.roles))
		return _moved(doc, publish.transition(doc, workflow.WITHDRAW))

	return publish.run(attempt)


@frappe.whitelist(methods=["POST"])
def request_changes(version, note=None):
	"""In Review -> Draft, by a reviewer, with a note saying what to change. The note is kept on the
	version (``review_note``), never in a ToDo or a Comment; the author gets a ToDo that names the
	draft."""
	name = _name(version, "version")

	def attempt():
		ask = publish.asker()
		_require_kb_role(ask)
		doc = _load_version(name)
		problems = workflow.request_changes_problems(
			doc, ask.user, ask.roles, user_type=ask.user_type, browser=ask.browser, gate_flags=ask.gate_flags
		)
		problems += _text_problems(note, "say what needs to change, so the author knows what to fix", "The note")
		_refuse(doc.name, workflow.REQUEST_CHANGES, problems)
		asked = publish.transition(
			doc, workflow.REQUEST_CHANGES, {"reviewer": ask.user, "review_note": cstr(note).strip()}
		)
		return _moved(doc, asked)

	return publish.run(attempt)


@frappe.whitelist(methods=["POST"])
def approve_and_publish(version, modified=None):
	"""In Review -> Published: the article is written, the version submitted, the one it replaces
	superseded, in one transaction (``publish.publish``).

	``modified`` is the version's ``modified`` as the approver's page loaded it. A version that
	changed since is refused, so what is published is what was read.
	"""
	name = _name(version, "version")

	def attempt():
		ask = publish.asker()
		_require_kb_role(ask)
		doc = _load_version(name)
		article = frappe.get_doc(ARTICLE, doc.article, for_update=True) if doc.get("article") else None
		problems = workflow.approval_problems(
			doc,
			ask.user,
			ask.roles,
			user_type=ask.user_type,
			browser=ask.browser,
			gate_flags=ask.gate_flags,
			opened_modified=modified,
		)
		problems += workflow.publish_problems(doc, article)
		found = content.document_secret_findings(doc)
		if found:
			problems.append(workflow.secret_problem(found))
		_refuse(doc.name, workflow.APPROVE_AND_PUBLISH, problems)
		return publish.publish(doc, article, opened_modified=modified, approver=ask.user)

	return publish.run(attempt)


@frappe.whitelist(methods=["POST"])
def discard(version):
	"""Draft -> Discarded. Kept as history, never deleted."""
	name = _name(version, "version")

	def attempt():
		ask = publish.asker()
		_require_kb_role(ask)
		doc = _load_version(name)
		_refuse(doc.name, workflow.DISCARD, workflow.discard_problems(doc, ask.user, ask.roles))
		return _moved(doc, publish.transition(doc, workflow.DISCARD))

	return publish.run(attempt)


@frappe.whitelist(methods=["GET"])
def review_diff(version):
	"""The published text against the draft, as the reviewer should read it: each changed field,
	and the body as a line diff of the two ``to_markdown`` texts (the form ``body_md`` and the AI
	tools use). A first version is compared with nothing. Draft text: KB roles in a browser only."""
	name = _name(version, "version")
	ask = publish.asker()
	_refuse(
		name,
		workflow.REVIEW_DIFF,
		workflow.review_diff_problems(ask.roles, browser=ask.browser, gate_flags=ask.gate_flags),
	)
	doc = frappe.get_doc(VERSION, name)
	doc.check_permission("read")
	live = None
	if doc.get("article") and frappe.db.exists(ARTICLE, doc.article):
		live = frappe.get_doc(ARTICLE, doc.article)
	changed = set(workflow.changed_content_fields(live, doc))
	fields = [
		{
			"field": field,
			"label": label,
			"before": cstr(live.get(field)) if live is not None else "",
			"after": cstr(doc.get(field)),
		}
		for field, label in DIFF_FIELDS
		if field in changed
	]
	before = _markdown(live.get("body")) if live is not None else ""
	return {
		"version": doc.name,
		"review_state": workflow.state_of(doc),
		"article": live.name if live is not None else None,
		"live_version": live.get("live_version") if live is not None else None,
		"fields": fields,
		"body": content.text_diff(before, _markdown(doc.get("body"))),
		"change_note": cstr(doc.get("change_note")),
	}


# ------------------------------------------------------------------ articles


@frappe.whitelist(methods=["POST"])
def confirm_still_accurate(article):
	"""The process owner or a KB Approver has checked the published text and it is still right:
	``last_reviewed_on``/``by`` are stamped and ``review_by`` restarts from today."""
	name = _name(article, "article")

	def attempt():
		ask = publish.asker()
		doc = frappe.get_doc(ARTICLE, name, for_update=True)
		doc.check_permission("read")
		problems = workflow.confirm_problems(
			doc, ask.user, ask.roles, user_type=ask.user_type, browser=ask.browser, gate_flags=ask.gate_flags
		)
		_refuse(doc.name, workflow.CONFIRM_STILL_ACCURATE, problems)
		return publish.confirm_still_accurate(doc, ask.user)

	return publish.run(attempt)


@frappe.whitelist(methods=["POST"])
def retire(article, reason=None):
	"""Published -> Retired, with the reason readers are shown. Nothing is deleted: the article,
	its number, its versions and its Files stay."""
	name = _name(article, "article")

	def attempt():
		ask = publish.asker()
		# First, before the article is locked or its open version read: the refusal below names the
		# open draft, and a reader is told nothing about drafts (publish.article_onload).
		_require_kb_role(ask)
		doc = frappe.get_doc(ARTICLE, name, for_update=True)
		doc.check_permission("read")
		open_row = publish.open_version(doc.name, lock=True)
		problems = workflow.retire_problems(
			doc,
			ask.user,
			ask.roles,
			user_type=ask.user_type,
			browser=ask.browser,
			gate_flags=ask.gate_flags,
			open_version=open_row.get("name") if open_row else None,
		)
		problems += _text_problems(reason, "give a reason, so readers know why it was retired", "The reason")
		_refuse(doc.name, workflow.RETIRE, problems)
		return publish.retire(doc, ask.user, cstr(reason).strip())

	return publish.run(attempt)


# ------------------------------------------------------------------ shared with the drafting tool


def submit_version(doc, ask):
	"""Draft -> In Review for ``doc``, as ``ask`` (``publish.asker()``): the body of
	:func:`submit_for_review`, moved out unchanged (WI-080 PR 6b) so that the endpoint and the AI
	drafting tool (``knowledge_base/ai_draft.py``) submit through one set of rules and one write.

	**Not an endpoint, and it must never become one**: it takes a loaded document and trusts its caller
	to have checked the person first. ``submit_for_review`` checks the KB role and loads the version
	``for_update`` with ``write`` checked; the drafting tool checks ``write`` on the version its own
	card has just written, as the confirming user, who it has required to be the person who asked. It
	asks ``workflow.submit_problems`` (the role, the state, the title, department, kind and text, the
	article, and secrets) and refuses in words, then moves the version with ``publish.transition``,
	which raises the review ToDos inline for every KB Approver who had no hand in it. Returns the
	version, its state, and who was asked.
	"""
	problems = workflow.submit_problems(
		doc,
		ask.user,
		ask.roles,
		article=publish.article_row(doc.get("article")),
		secrets=content.document_secret_findings(doc),
	)
	_refuse(doc.name, workflow.SUBMIT_FOR_REVIEW, problems)
	asked = publish.transition(
		doc, workflow.SUBMIT_FOR_REVIEW, {"submitted_by": ask.user, "submitted_on": now_datetime()}
	)
	return _moved(doc, asked)


# ------------------------------------------------------------------ helpers


def _name(value, what):
	"""A document name from the request: a non-blank string."""
	if not isinstance(value, str) or not value.strip():
		frappe.throw(_("Say which {0}.").format(what), title=_("Not done"))
	return value.strip()


def _require_kb_role(ask):
	if not workflow.holds_kb_role(ask.roles):
		frappe.throw(
			_("Only a {0} or {1} can do this.").format(constants.AUTHOR_ROLE, constants.APPROVER_ROLE),
			frappe.PermissionError,
		)


def _load_version(name):
	"""The version, locked for this transaction, and the person allowed to write it."""
	doc = frappe.get_doc(VERSION, name, for_update=True)
	doc.check_permission("write")
	return doc


def _refuse(name, action, problems):
	if problems:
		frappe.throw(workflow.refusal(name, workflow.VERBS[action], problems), title=_("Not done"))


def _text_problems(value, missing, label):
	"""A required note or reason: present, and not carrying a secret. The retire reason is shown to
	every reader, and the review note is kept with the draft; neither is a place for a password."""
	problem = workflow.required_text_problem(value, missing)
	if problem:
		return [problem]
	found = content.secret_findings(cstr(value))
	if found:
		places = "; ".join(f"line {f.line} looks like {f.kind}" for f in found)
		return [f"{label} looks like it contains a secret ({places}); take it out"]
	return []


def _moved(doc, asked):
	return {
		"version": doc.name,
		"review_state": doc.review_state,
		"notified": [{"user": user, "full_name": get_fullname(user)} for user in asked or ()],
	}


def _markdown(html):
	"""``to_markdown`` for reading; on the rare body it cannot convert, the text as stored, so the
	reviewer still sees a diff (publishing refuses such a body outright)."""
	try:
		return to_markdown(html or "") or ""
	except Exception:
		return cstr(html)
