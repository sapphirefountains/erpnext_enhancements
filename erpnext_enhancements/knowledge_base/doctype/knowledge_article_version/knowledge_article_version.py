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
with ``ignore_permissions``. PR 2 adds the approval rules (approver is not the author, the
submitter, a contributor or the AI requester; from a browser; unchanged since opened) to the same
two hooks.

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

Nothing is ever deleted, canceled or amended. A published version is the record of what a person
approved, and the Knowledge Base Integrity report (PR 4) checks every Article against it.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class KnowledgeArticleVersion(Document):
	def before_insert(self):
		self._refuse_amend()

	def validate(self):
		self._refuse_amend()

	def before_submit(self):
		self._refuse_unless_publishing()

	def on_submit(self):
		# before_submit is skipped under flags.ignore_validate; on_submit never is.
		self._refuse_unless_publishing()

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
