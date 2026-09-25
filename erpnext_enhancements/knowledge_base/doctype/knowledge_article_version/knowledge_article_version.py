# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Knowledge Article Version: drafts, and the permanent history of every published version.

WI-080, ADR 0017. Only KB Author and KB Approver hold a DocPerm here. No reader role, no System
Manager, no ``Desk User``, ``All`` or ``Guest`` row, so a draft cannot leak through a reader path:
it is not in the reader's doctype. The AI gate also refuses this doctype on every generic tool
(``assistant_tools/_gate.py``, ``DENYLIST_DOCTYPES``), because raw SQL never consults DocPerm.

**Publishing is submitting**, and only the Knowledge Base's own publish action may do it. That
action sets ``doc.flags.kb_publish`` before it calls ``submit()``; any other submit is refused.
Nobody holds the ``submit`` right either, so the refusal is reached only by server code running
with ``ignore_permissions``. **The same two hooks then apply the approval rules**
(``knowledge_base/workflow.py``, ``approval_problems``, PR 2): the approver holds KB Approver, is
not the owner, the submitter, a contributor or the AI requester, is a person signed in from a
browser and not an AI gate action, and the version is In Review and still exactly the copy they
opened. The rules read the version **as stored** (``get_doc_before_save()``, loaded ``FOR UPDATE``
by ``check_if_latest``, ``model/document.py:588`` -> ``:1097`` -> ``:1432``), never the copy in
memory, which the code calling ``submit()`` could have edited; and the copy being submitted must
match it (``_refuse_unless_approvable``).

**The flag for PR 3.** ``approve_and_publish`` sets ``doc.flags.kb_opened_modified`` to the
``modified`` value the approver's page had open. Without it every approval is refused as "changed
after you opened it". It cannot be taken from the document itself: by ``before_submit`` the save
has already moved ``modified`` on (``set_user_and_timestamp``, ``:586``).

**Content rules on every save** (``validate``, PR 2). Presentation is stripped from the body
(``content.strip_presentation``). Content changes only while the stored version is a Draft
(``workflow.content_edit_problem``). A save that changes content is scanned for secrets and
refused on a finding (``content.document_secret_findings``), and records the saver in
``contributors`` (``workflow.with_contributor``). A save that does not change content (a state
change by the KB's own actions) is neither scanned nor recorded, so a version whose text predates a
stricter scan can still be sent back or withdrawn; it is scanned again before it can be approved.

Why every refusal is in two places. Frappe v16 lets a caller skip the obvious hook:

* ``flags.ignore_validate`` skips ``validate``, ``before_submit``, ``before_cancel`` and
  ``before_update_after_submit`` (frappe ``origin/version-16`` ``model/document.py:1407-1408``)
  but never ``on_submit``, ``on_cancel`` or ``on_update_after_submit`` (``:1457``, ``:1459``,
  ``:1462``), which run inside the same transaction, so raising there rolls the write back.
* ``delete_doc(ignore_on_trash=True)`` skips ``on_trash`` (``model/delete_doc.py:175-176``) but
  never ``after_delete`` (``:195-196``).
* ``before_insert`` runs on every insert, ignore_validate or not (``model/document.py:480``), so
  an amendment is refused there as well as in ``validate``.

What may change after publishing: only ``review_state`` (``allow_on_submit``), when a newer
version supersedes this one. The publish code sets ``doc.flags.kb_action`` for that write; any
other update-after-submit is refused.

**Every server-set field is at permlevel 1, and nobody holds write there.** ``read_only`` is a
Desk hint that v16 never enforces on the server; only permlevel is enforced. KB Author and KB
Approver hold write at level 0, so with these fields at level 0 either of them could rewrite
``contributors``, ``ai_requested_by`` or ``review_state`` through ``frappe.client.set_value`` or
``PUT /api/resource`` on a draft, and erase the very record PR 2's approval rules read. At level 1
they read them, and ``validate_higher_perm_levels`` (frappe ``origin/version-16``
``model/document.py:1021-1044``, called at ``:483`` on insert and ``:592`` on save) puts back the
stored value, or the default on a new document, before ``validate`` runs. The content fields
and ``amended_from`` (Frappe's own, refused above) stay at level 0.

That puts one rule on the code in later PRs: **a write to a server-set field must run with
``ignore_permissions``** (or name the field in ``flags.ignore_permlevel_for_fields``); otherwise it
is silently reset, supersede included. A value computed in ``validate`` or ``before_save`` survives
a user's save, because the reset runs before them; one set in ``before_insert`` does not, because
the reset runs after it (``:480`` then ``:483``).

Nothing is ever deleted, canceled or amended. A published version is the record of what a person
approved, and the Knowledge Base Integrity report (PR 4) checks every Article against it.
"""

import frappe
from frappe import _
from frappe.model.document import Document

# workflow imports marketing/publish/workflow.py (signed_in_browser). A controller that fails to
# import is force-deleted by the next migrate, silently (CLAUDE.md), so the chain is pinned:
# tests/test_knowledge_base_hooks.py imports this module, and a broken import fails the build.
from erpnext_enhancements.knowledge_base import content, workflow


class KnowledgeArticleVersion(Document):
	def before_insert(self):
		self._refuse_amend()

	def validate(self):
		self._refuse_amend()
		self._apply_content_rules()

	def before_submit(self):
		self._refuse_unless_publishing()
		self._refuse_unless_approvable()

	def on_submit(self):
		# before_submit is skipped under flags.ignore_validate; on_submit never is.
		self._refuse_unless_publishing()
		self._refuse_unless_approvable()

	def before_cancel(self):
		_refuse_cancel(self.name)

	def on_cancel(self):
		# before_cancel is skipped under flags.ignore_validate; on_cancel never is.
		_refuse_cancel(self.name)

	def before_update_after_submit(self):
		self._refuse_unless_kb_action()

	def on_update_after_submit(self):
		self._refuse_unless_kb_action()

	def before_rename(self, old, new, merge=False):
		frappe.throw(
			_(
				"{0} is part of an article's permanent history, and the article records it by name. "
				"It cannot be renamed or merged."
			).format(old),
			title=_("Versions keep their names"),
		)

	def on_trash(self):
		_refuse_delete(self.name)

	def after_delete(self):
		# delete_doc(ignore_on_trash=True) skips on_trash; this hook always runs, before the commit.
		_refuse_delete(self.name)

	def _refuse_amend(self):
		if not self.get("amended_from"):
			return
		frappe.throw(
			_(
				"Versions are never amended. To change a published article, start a new revision "
				"from it."
			),
			title=_("Versions are never amended"),
		)

	def _refuse_unless_publishing(self):
		if self.flags.get("kb_publish"):
			return
		frappe.throw(
			_(
				"A version is published only by Approve and Publish, by a KB Approver who did not "
				"write it. It cannot be submitted directly."
			),
			title=_("Publish through review"),
		)

	def _refuse_unless_kb_action(self):
		if self.flags.get("kb_action"):
			return
		frappe.throw(
			_("A published version is permanent history and cannot be edited."),
			title=_("Published versions are read-only"),
		)

	def _apply_content_rules(self):
		"""Strip presentation; refuse a content edit outside Draft, or one carrying a secret; record
		the contributor. ``contributors`` sits at permlevel 1, and a value set in ``validate``
		survives the user's save because v16 resets higher permlevels before ``validate`` runs."""
		if self.get("body"):
			self.body = content.strip_presentation(self.body)
		stored = self.get_doc_before_save()
		changed = workflow.changed_content_fields(stored, self)
		if not changed:
			return
		problem = workflow.content_edit_problem(stored, changed)
		if problem:
			frappe.throw(problem, title=_("Content cannot change now"))
		found = content.document_secret_findings(self)
		if found:
			frappe.throw(content.secret_refusal(found), title=_("This looks like a secret"))
		self.contributors = workflow.with_contributor(self.get("contributors"), frappe.session.user)

	def _refuse_unless_approvable(self):
		stored = self.get_doc_before_save()
		user = frappe.session.user
		problems = workflow.approval_problems(
			stored,
			user,
			frappe.get_roles(user),
			browser=_browser_request(),
			gate_flags=frappe.flags,
			opened_modified=self.flags.get("kb_opened_modified"),
		)
		if stored is not None and workflow.changed_content_fields(stored, self):
			problems.append("the copy being published is not the copy that was submitted for review")
		if content.document_secret_findings(self):
			problems.append("its content looks like it contains a secret; send it back so the author can remove it")
		if problems:
			frappe.throw(workflow.refusal(self.name, "approved", problems), title=_("Not approved"))


def _browser_request():
	"""``workflow.signed_in_browser`` for this request; no request at all (a job, the console) is not
	a browser."""
	request = getattr(frappe.local, "request", None)
	if request is None:
		return False
	return workflow.signed_in_browser(frappe.session, request.headers.get("Authorization"))


def _refuse_cancel(name):
	frappe.throw(
		_(
			"{0} is permanent history and cannot be canceled. Publish a newer version, or retire "
			"the article."
		).format(name),
		title=_("Versions are never canceled"),
	)


def _refuse_delete(name):
	frappe.throw(
		_("{0} is kept as part of the article's history. Versions are never deleted.").format(name),
		title=_("Versions are never deleted"),
	)
