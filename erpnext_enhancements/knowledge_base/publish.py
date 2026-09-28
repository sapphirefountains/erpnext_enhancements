# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Every write the Knowledge Base's actions make, and the one-transaction publish (WI-080 PR 3).

``api/knowledge_base.py`` holds the endpoints: each loads what it acts on, checks permission and
asks the rules in ``workflow.py`` before it calls anything here. This module is the writing half,
and it is the **only** code that changes a version's ``review_state`` (:func:`transition`) or
writes a Knowledge Article (:func:`publish`, :func:`retire`, :func:`confirm_still_accurate`).
Each write opts in by name: ``flags.kb_publish`` and ``flags.kb_opened_modified`` to submit a
version, ``flags.kb_action`` to change a submitted version, to write an article, and to move a File
(the controllers and ``files.py`` refuse every write that does not say so). Server-set fields sit at
permlevel 1, which nobody can write, so every write here runs with ``ignore_permissions`` (the
permission check is the endpoint's, done first and explicitly).

**Publishing is one transaction** (:func:`publish`), in this order:

1. **The KB number** (a first version only): ``SELECT name ... WHERE name LIKE %s FOR UPDATE`` with
   ``workflow.kb_number_prefix(block) + "%"`` as the bound parameter, then
   ``workflow.next_kb_number`` over what it returned (:func:`allocate_number`). A revision instead
   locks its article's row (the endpoint loads it ``for_update``).
2. **The article**, inserted or saved under ``flags.kb_action``, from the version **as stored**:
   the endpoint loaded it ``for_update`` in this transaction and nothing here changes its content.
   ``body_md`` (v16's ``frappe.utils.to_markdown``, ``utils/data.py:2468-2477``) and
   ``content_hash`` (``content.content_hash``) are computed here, from that stored body, and never
   in ``validate``. ``review_by`` restarts from the approval (``workflow.review_by``), and the
   interval the version was approved with (``workflow.review_interval``) is stored with it.
3. **The Files** the version's body uses (``content.referenced_files``: every ``?fid=`` and every
   ``/private/files/`` path in an ``src`` or ``href``) that are attached to the version are moved
   onto the article, each under ``flags.kb_action`` (``files.force_private`` refuses any other
   change to a KB File's attachment). The links in the body keep working, because a File's URL
   does not change when it moves, and every staff user can read a File attached to an article.
   Nothing is deleted: a File the body no longer uses stays where it is.
4. **The version is submitted** through :func:`transition`, with ``flags.kb_publish`` and the
   ``modified`` value the approver opened (``flags.kb_opened_modified``). The controller asks the
   approval rules again, in ``before_submit`` and ``on_submit``, against the row as stored.
5. **The previous live version is superseded** (``flags.kb_action``: a submitted version refuses
   any other update), and its ToDos, and this version's review ToDos, are closed (``notify.py``).

Any failure at any step raises, and Frappe rolls the whole request back: the number, the article,
the File moves and the submit happen together or not at all.

**Concurrency.** Two approvers publishing two new articles in one block at once get different
numbers: the second one's ``FOR UPDATE`` waits for the first to commit, then reads its number too.
When the block is empty and no article sorts after it, both ``FOR UPDATE`` reads take only a gap
lock, which InnoDB grants to both, and their inserts deadlock; MariaDB rolls one transaction back
(``frappe.QueryDeadlockError``), and :func:`run` retries that action from the start, in a new
transaction that sees the first one's number. MariaDB 11.8's ``innodb_snapshot_isolation`` reports
a locking read of a row changed since the transaction's snapshot the same way (``ER_CHECKREAD``,
which v16 maps to the same error, ``database/mariadb/database.py:26-29``), and gets the same retry.
A double-click on Approve is two requests for one version: the second waits on the version's row
lock, then reads it as Published (or is retried and does), and ``approval_problems`` refuses it
("it is Published, not In Review"; "it changed after you opened it"). The primary key on
``kb_number`` means a number can never be given twice, whatever happens.
"""

from collections import namedtuple

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, to_markdown

from erpnext_enhancements.knowledge_base import constants, content, notify, workflow
from erpnext_enhancements.knowledge_base.doctype.knowledge_article_version import (
	knowledge_article_version as version_controller,
)

ARTICLE = constants.ARTICLE_DOCTYPE
VERSION = constants.VERSION_DOCTYPE
PUBLISHED_STATUS, RETIRED_STATUS = constants.ARTICLE_STATUSES

#: How many times an action is tried when MariaDB rolls its transaction back (a deadlock, or a
#: snapshot-isolation conflict). Each try is a fresh transaction.
ATTEMPTS = 3

#: What a revision copies from the published version: its content, less the change note, which
#: describes the new revision and starts empty.
REVISION_FIELDS = tuple(f for f in constants.VERSION_CONTENT_FIELDS if f != "change_note")

#: The person asking, as the rules take them. ``user_type`` is read from the User row at the moment
#: of asking, never from the session, which recorded it at login.
Asker = namedtuple("Asker", "user roles user_type browser gate_flags")


def asker():
	"""Who is asking: the one place the endpoints and the forms read it from."""
	user = frappe.session.user
	return Asker(
		user=user,
		roles=tuple(frappe.get_roles(user)),
		user_type=version_controller._user_type(user),
		browser=version_controller._browser_request(),
		gate_flags=frappe.flags,
	)


def run(action):
	"""Run ``action()``, one whole action (its locked reads, its checks and its writes), again from
	the start if MariaDB rolls its transaction back. See "Concurrency" in the module docstring."""
	for _attempt in range(ATTEMPTS):
		try:
			return action()
		except (frappe.QueryDeadlockError, frappe.DuplicateEntryError):
			# The whole transaction goes, so the next try reads what the other request committed.
			frappe.db.rollback()
			frappe.clear_messages()
	frappe.throw(
		_(
			"Someone else was changing the knowledge base at the same moment, and this could not be "
			"finished. Nothing was saved. Reload the page and try again."
		),
		title=_("Try again"),
	)


# ------------------------------------------------------------------ the state machine's one writer


def transition(doc, action, values=None, *, opened_modified=None):
	"""Move a version by ``action`` (``workflow.TRANSITIONS``), writing ``values`` with it.

	**The only writer of ``review_state``.** A move the table does not allow is refused here,
	whoever calls. Approve submits the version; every other move saves it (a submitted version
	under ``flags.kb_action``). Then ``notify.after_transition`` closes the version's open ToDos and
	raises the ones its new state needs; the users it raised them for are returned.
	"""
	problem = workflow.transition_problem(action, workflow.state_of(doc))
	if problem:
		frappe.throw(workflow.refusal(doc.name, workflow.VERBS.get(action, action), [problem]), title=_("Not done"))
	doc.update(values or {})
	doc.review_state = workflow.TRANSITIONS[action][1]
	# Server-set fields are at permlevel 1, which nobody can write, and nobody holds submit: the
	# endpoint checked the person's permission before it got here.
	doc.flags.ignore_permissions = True
	if action == workflow.APPROVE_AND_PUBLISH:
		doc.flags.kb_publish = True
		doc.flags.kb_opened_modified = opened_modified
		doc.submit()
	else:
		if cint(doc.docstatus) == 1:
			doc.flags.kb_action = True
		doc.save()
	return notify.after_transition(doc, action)


# ------------------------------------------------------------------ publishing


def publish(version, article, *, opened_modified, approver):
	"""Publish ``version`` (loaded ``for_update``, In Review, already checked by the endpoint) into
	``article`` (loaded ``for_update``; ``None`` for a first version). One transaction; see the
	module docstring for the order and why."""
	now = now_datetime()
	previous = None
	if article is None:
		number = allocate_number(version.get("department_block"))
		article = frappe.new_doc(ARTICLE)
		article.kb_number = number
		article.first_published_on = now
	else:
		number = article.name
		previous = article.get("live_version")
	version_number = cint(article.get("version_number")) + 1
	months = workflow.review_interval(version.get("review_every_months"))

	article.update(
		{
			"title": version.get("title"),
			"department_block": version.get("department_block"),
			"status": PUBLISHED_STATUS,
			"summary": version.get("summary"),
			"keywords": version.get("keywords"),
			"body": version.get("body"),
			"body_md": body_markdown(version),
			"content_hash": content.content_hash(version),
			"version_number": version_number,
			"live_version": version.name,
			"change_note": version.get("change_note"),
			"author": version.get("owner"),
			"approved_by": approver,
			"approved_on": now,
			"process_owner": version.get("process_owner"),
			"review_every_months": months,
			"review_by": workflow.review_by(now, months),
			"last_reviewed_on": now,
			"last_reviewed_by": approver,
			"ai_drafted": cint(version.get("ai_drafted")),
		}
	)
	_write_article(article)

	moved = move_files(version, number)
	transition(
		version,
		workflow.APPROVE_AND_PUBLISH,
		{
			"article": number,
			"version_number": version_number,
			"approved_by": approver,
			"approved_on": now,
			"reviewer": approver,
		},
		opened_modified=opened_modified,
	)
	superseded = supersede(previous) if previous and previous != version.name else None
	return {
		"article": number,
		"version": version.name,
		"version_number": version_number,
		"files_moved": moved,
		"superseded": superseded,
	}


def allocate_number(block):
	"""The next KB number in ``block``, read under ``FOR UPDATE``. The ``%`` is inside the bound
	parameter, never in the SQL text."""
	taken = frappe.db.sql(
		"select name from `tabKnowledge Article` where name like %s for update",
		(workflow.kb_number_prefix(block) + "%",),
		pluck=True,
	)
	message = None
	try:
		return workflow.next_kb_number(block, taken)
	except ValueError as exc:  # BlockFullError, or a block that is not one
		message = str(exc)
	frappe.throw(message, title=_("No KB number"))


def body_markdown(version):
	"""``body_md``: v16's ``to_markdown`` (``html2text``) of the stored body.

	v16's ``to_markdown`` catches ``HTMLParser.HTMLParseError``, which Python 3 no longer has, so a
	converter failure surfaces as an ``AttributeError`` from its own ``except`` line. Any failure is
	refused in words, and nothing is published: an article is never published without the copy the
	AI tools read. Only the exception's type is logged, never the text.

	The log is a **deferred** insert, because the refusal that follows rolls the request back: v16's
	``log_error`` inserts the Error Log in the request's own transaction unless ``defer_insert``
	(``utils/error.py:95-98``), ``application()`` rolls that transaction back on the throw
	(``app.py:181-184``), and a 417 gets no snapshot of its own (``app.py:448``: 500 and up only).
	Written the plain way, the row the message sends Nik to never existed (v1.555.1). Deferred, it
	goes to redis and the scheduler's ``deferred_insert.save_to_db`` writes it within minutes, which
	is how prod's own request errors arrive (and, like theirs, a deploy in those minutes loses it).
	"""
	failed = None
	try:
		return to_markdown(version.get("body") or "") or ""
	except Exception as exc:
		failed = type(exc).__name__
	frappe.log_error(
		title="Knowledge base publish",
		message=f"to_markdown raised {failed} on {version.name}",
		defer_insert=True,
	)
	frappe.throw(
		_(
			"{0} was not published: its text could not be turned into the plain copy the AI tools "
			"read. Nothing was changed. Nik can find the details in the Error Log."
		).format(version.name),
		title=_("Not published"),
	)


def move_files(version, number):
	"""Move the Files ``version``'s body uses from the version onto article ``number``. Returns
	their names. Never deletes anything."""
	fids, paths = content.referenced_files(version.get("body"))
	if not fids and not paths:
		return []
	rows = frappe.get_all(
		"File",
		filters={"attached_to_doctype": VERSION, "attached_to_name": version.name},
		fields=["name", "file_url"],
		order_by="creation asc",
	)
	moved = []
	for row in rows:
		if row.get("name") not in fids and (row.get("file_url") or "") not in paths:
			continue
		file = frappe.get_doc("File", row.get("name"))
		file.attached_to_doctype = ARTICLE
		file.attached_to_name = number
		# files.force_private refuses any other change to where a KB File is attached.
		file.flags.kb_action = True
		file.save(ignore_permissions=True)
		moved.append(file.name)
	return moved


def supersede(name):
	"""Mark the previous live version Superseded. A row that is not Published (only possible if
	someone wrote past the ORM) is left for the Integrity report rather than blocking a publish."""
	old = frappe.get_doc(VERSION, name, for_update=True)
	if workflow.state_of(old) != workflow.PUBLISHED:
		return None
	transition(old, workflow.SUPERSEDE)
	return old.name


# ------------------------------------------------------------------ revisions and articles


def start_revision(article):
	"""A new Draft of ``article``, copied from the version that is live (the approved record), or
	from the article itself if that version cannot be found. The endpoint has already checked
	that none is open (under the article's row lock) and that the person may create a version."""
	live = article.get("live_version")
	source = frappe.get_doc(VERSION, live) if live and frappe.db.exists(VERSION, live) else article
	doc = frappe.get_doc(
		{
			"doctype": VERSION,
			**{field: source.get(field) for field in REVISION_FIELDS},
			"article": article.name,
			"base_version": live,
			"version_number": cint(article.get("version_number")) + 1,
			"review_state": workflow.DRAFT,
		}
	)
	# `article`, `base_version` and `version_number` are at permlevel 1, which v16 resets to the
	# default on insert for anyone who cannot write it (model/document.py:1021-1044).
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc


def open_version(article, *, lock=False):
	"""The article's open version (Draft or In Review) as ``{name, review_state}``, or ``None``.
	With ``lock``, read ``FOR UPDATE`` (a locking read sees what another request committed).

	Only a docstatus 0 row is open. A Draft or In Review row at docstatus 2 is what Frappe's own
	Discard would leave (the controller refuses it since v1.555.1), or a write past the ORM: it can
	never be saved again, so counting it as open would block every revision and every retire of its
	article for good."""
	query = (
		"select name, review_state from `tabKnowledge Article Version` "
		"where article = %s and docstatus = 0 and review_state in %s order by creation asc"
	)
	if lock:
		query += " for update"
	rows = frappe.db.sql(query, (article, tuple(constants.OPEN_REVIEW_STATES)), as_dict=True)
	return rows[0] if rows else None


def retire(article, user, reason):
	"""Retire ``article``: it stays, with its number, its history and its Files, and readers see
	it marked Retired with the reason."""
	article.update(
		{"status": RETIRED_STATUS, "retired_on": now_datetime(), "retired_by": user, "retired_reason": reason}
	)
	_write_article(article)
	return {"article": article.name, "status": article.status}


def confirm_still_accurate(article, user):
	"""Record that ``user`` checked ``article`` and it is still right: the review clock restarts."""
	now = now_datetime()
	months = workflow.review_interval(article.get("review_every_months"))
	article.update({"last_reviewed_on": now, "last_reviewed_by": user, "review_by": workflow.review_by(now, months)})
	_write_article(article)
	return {"article": article.name, "review_by": str(article.review_by)}


def _write_article(article):
	"""The one way an article is written: the controller refuses any save without the flag."""
	article.flags.kb_action = True
	article.flags.ignore_permissions = True
	if article.is_new():
		article.insert()
	else:
		article.save()


# ------------------------------------------------------------------ what the forms show


def version_onload(doc):
	"""``__onload.kb`` for a version's form: the actions this person may take now (so the form
	shows exactly those buttons) and, for a KB Approver who may not approve it, why not."""
	ask = asker()
	if not workflow.holds_kb_role(ask.roles):
		return {"actions": [], "approve_blockers": []}
	article = article_row(doc.get("article"))
	context = {"user_type": ask.user_type, "browser": ask.browser, "gate_flags": ask.gate_flags}
	actions = workflow.version_actions(doc, ask.user, ask.roles, article=article, **context)
	blockers = []
	if (
		workflow.state_of(doc) == workflow.IN_REVIEW
		and constants.APPROVER_ROLE in ask.roles
		and workflow.APPROVE_AND_PUBLISH not in actions
	):
		blockers = workflow.approval_problems(
			doc, ask.user, ask.roles, opened_modified=doc.get("modified"), **context
		) + workflow.publish_problems(doc, article)
	return {"actions": list(actions), "approve_blockers": blockers}


def article_onload(doc):
	"""``__onload.kb`` for an article's form: the actions this person may take now, and the open
	revision (KB roles only: a reader is told nothing about drafts)."""
	ask = asker()
	open_row = open_version(doc.name) if workflow.holds_kb_role(ask.roles) else None
	open_name = open_row.get("name") if open_row else None
	actions = workflow.article_actions(
		doc,
		ask.user,
		ask.roles,
		user_type=ask.user_type,
		browser=ask.browser,
		gate_flags=ask.gate_flags,
		open_version=open_name,
	)
	return {
		"actions": list(actions),
		"open_version": open_name,
		"open_state": open_row.get("review_state") if open_row else None,
	}


def article_row(name):
	if not name:
		return None
	return frappe.db.get_value(
		ARTICLE, name, ["name", "status", "department_block", "version_number", "live_version"], as_dict=True
	)
