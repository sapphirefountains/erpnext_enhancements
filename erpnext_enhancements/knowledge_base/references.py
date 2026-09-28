# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""No typed text is kept about a draft outside the draft (WI-080 PR 3, decision (b)).

A draft cannot leak through a reader path because it is not in the reader's doctype (ADR 0017
section 1): only KB Authors and KB Approvers can open ``Knowledge Article Version``, and the AI
gate refuses it on every generic tool. **Two other doctypes hold text *about* a version, and they
are readable more widely:**

* **Comment.** A System Manager reads every Comment on the site (v16 ``core/doctype/comment/
  comment.json`` gives System Manager, and Website Manager, full read, and there is no permission
  query), and so does every AI tool acting for one, ``triton@`` included: ``list_documents
  (doctype="Comment", filters={"reference_doctype": "Knowledge Article Version"})`` names no
  denylisted doctype. The Desk's comment box on the version's form writes one
  (``frappe.desk.form.utils.add_comment``, which needs only read on the version), and so does
  ``api/comments.add_comment``. A reviewer's "change 'close valve B' to 'close valve A'" is draft
  text.
* **ToDo.** A System Manager reads every ToDo too (v16 ``desk/doctype/todo/todo.py:149-172``
  exempts any role on the ToDo DocPerm, and System Manager is one), and the sidebar's Assign To
  dialog has a free-text comment that becomes the description, which v16 also copies into an
  "Assigned" Comment on the version (``todo.py:40-52``).

So both are refused on a version unless the Knowledge Base's own code writes them
(``flags.kb_action``):

* :func:`guard_comment` (``doc_events["Comment"]["before_validate"]``) refuses a Comment of type
  **"Comment"** (the one people type) that references a version, on insert and on every later
  save. The other types are Frappe's own record-keeping (Attachment, Assigned, Assignment
  Completed, Like, Info and so on), written by code with text the code chooses: a file's name, or
  the KB's ToDo description, which is the title and a link. Nothing in PR 3 writes a Comment on a
  version, so the flag is there for later KB code, not for this one. The discussion a review needs
  has a home that stays inside the version: Request Changes writes its note to ``review_note``,
  which only KB roles can read.
* :func:`guard_todo` (``doc_events["ToDo"]["before_validate"]``) refuses a ToDo on a version that
  KB code did not raise, and any change to the description or the reference of one that it did.
  Closing, reprioritising or re-dating one is allowed. The KB raises every ToDo a version needs
  (``notify.py``), so the sidebar's Assign To is not needed there.

Both run for **every** Comment and ToDo on the site, so each returns at once for one that does not
touch a version, reading nothing beyond the row and the stored copy v16 has already loaded (no
query), and neither raises for an unrelated row (``doc_events`` fire during ERPNext's own test
bootstrap, before this app's doctypes exist). ``before_validate`` because v16 runs it on every save
before it looks at ``flags.ignore_validate`` (``model/document.py:1403-1408``), so no flag skips it.

**Deleting is not refused.** Neither hook is on ``on_trash``: removing a Comment that should not
have been written is the safe direction.
"""

import frappe
from frappe import _

from erpnext_enhancements.knowledge_base.constants import VERSION_DOCTYPE

#: The flag KB code sets on a Comment or ToDo it writes about a version.
ACTION_FLAG = "kb_action"

#: The Comment type people type into (the form's comment box, ``add_comment``). Every other type
#: is Frappe's own record of something that happened, with text chosen by code.
TYPED_COMMENT = "Comment"

#: The ToDo fields that carry text or say what the ToDo is about. Status, priority, date, colour
#: and the assignee may change.
TODO_TEXT_FIELDS = ("description", "reference_type", "reference_name")


def guard_comment(doc, method=None):
	"""Refuse a typed Comment on a Knowledge Article Version. See the module docstring."""
	stored = _stored(doc)
	on_version = _is_typed_version_comment(doc) or (stored is not None and _is_typed_version_comment(stored))
	if not on_version or _flag(doc, ACTION_FLAG):
		return
	frappe.throw(
		_(
			"Comments are not kept on a knowledge base draft. A comment is readable by every System "
			"Manager and by the AI tools that act for them, and only approved text may reach them. "
			"To ask the author for a change, use Request Changes: its note stays on the version, "
			"where only KB Authors and KB Approvers can read it."
		),
		title=_("No comments on drafts"),
	)


def guard_todo(doc, method=None):
	"""Refuse a ToDo on a Knowledge Article Version that KB code did not raise, and a change to the
	text or reference of one it did. See the module docstring."""
	stored = _stored(doc)
	on_version = _read(doc, "reference_type") == VERSION_DOCTYPE or (
		stored is not None and _read(stored, "reference_type") == VERSION_DOCTYPE
	)
	if not on_version or _flag(doc, ACTION_FLAG):
		return
	if stored is None:
		frappe.throw(
			_(
				"A knowledge base draft cannot be assigned by hand. The knowledge base asks every KB "
				"Approver who may approve a version when it is submitted for review, and the author "
				"when changes are requested. A to-do's text is readable by every System Manager and "
				"the AI tools that act for them, so none is written about a draft except by the "
				"knowledge base itself."
			),
			title=_("Assigned by the knowledge base"),
		)
	changed = [f for f in TODO_TEXT_FIELDS if str(_read(doc, f) or "") != str(_read(stored, f) or "")]
	if changed:
		frappe.throw(
			_(
				"The text of a knowledge base review to-do is written by the knowledge base and "
				"cannot be changed. It can be closed."
			),
			title=_("Assigned by the knowledge base"),
		)


def _is_typed_version_comment(row):
	return _read(row, "comment_type") == TYPED_COMMENT and _read(row, "reference_doctype") == VERSION_DOCTYPE


def _stored(doc):
	"""The row as stored, which v16 loaded before any hook ran (``None`` on insert); no query."""
	getter = getattr(doc, "get_doc_before_save", None)
	return getter() if callable(getter) else None


def _read(obj, key):
	if obj is None:
		return ""
	getter = getattr(obj, "get", None)
	value = getter(key) if callable(getter) else getattr(obj, key, None)
	return value or ""


def _flag(doc, name):
	flags = getattr(doc, "flags", None)
	if flags is None:
		return None
	getter = getattr(flags, "get", None)
	return getter(name) if callable(getter) else getattr(flags, name, None)
