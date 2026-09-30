# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Knowledge Article: the approved, published text of one article number, and nothing else.

WI-080, ADR 0017. Every staff user reads this doctype (through ``Desk User``), and the AI tools
that arrive in PR 6 read only this doctype, so its rows must only ever hold text a second person
approved. Drafts live in ``Knowledge Article Version``, which readers cannot open at all.

**Nothing writes a row here except the Knowledge Base's own publishing code**, and that code says
so by setting ``doc.flags.kb_action`` before it saves. Every other save is refused, whoever makes
it. No role has write, create or delete on this doctype, so a refusal here is only ever reached by
server code running with ``ignore_permissions``; the point is that such code has to opt in by name
rather than wander in.

Why each refusal is in two places. Frappe v16 lets a caller skip the obvious hook:

* ``flags.ignore_validate`` skips ``validate`` (``run_before_save_methods``, frappe
  ``origin/version-16`` ``model/document.py:1407-1408``) but never ``on_update``
  (``run_post_save_methods``, ``:1454``), which runs inside the same transaction, so raising
  there rolls the write back.
* ``delete_doc(ignore_on_trash=True)`` skips ``on_trash`` (``model/delete_doc.py:175-176``) but
  never ``after_delete``, which runs right after the row is deleted and before the commit
  (``:195-196``).

**The number, the kind and the department are fixed** (2026-09-29). An article is named by its
number, ``<PREFIX>-<DD>-<NNNN>`` (``SOP-06-0001``: the kind's prefix, the department block, a
sequence), and a published article's number never changes, so neither does its kind or its
department. The last of four layers holds it here, where it holds even if the publishing code were
wrong: a new row's number must be canonical and match its kind and department
(``workflow.number_problems``), and a saved row's kind and department must equal the stored ones.
Refused in ``validate`` and again in ``on_update``, like the flag.

A determined System Manager can still write past the ORM with ``frappe.db.set_value`` or raw SQL.
The Knowledge Base Integrity report (PR 4) is what detects that; this controller only makes sure
nobody does it by accident.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class KnowledgeArticle(Document):
	def onload(self):
		# PR 3: Start Revision, Confirm Still Accurate and Retire, offered exactly to the people the
		# endpoints would let through (publish.article_onload). A reader with no KB role is told
		# nothing about drafts. Imported here so that loading this controller stays cheap.
		from erpnext_enhancements.knowledge_base import publish

		self.set_onload("kb", publish.article_onload(self))

	def validate(self):
		self._refuse_unless_kb_action()
		self._refuse_identity_change()

	def on_update(self):
		# Also runs on insert. Runs even when flags.ignore_validate skipped validate() above.
		self._refuse_unless_kb_action()
		self._refuse_identity_change()

	def before_rename(self, old, new, merge=False):
		frappe.throw(
			_(
				"An article number is permanent: people and AI tools cite it. {0} cannot be renamed or "
				"merged."
			).format(old),
			title=_("Article numbers do not change"),
		)

	def on_trash(self):
		_refuse_delete(self.name)

	def after_delete(self):
		# delete_doc(ignore_on_trash=True) skips on_trash; this hook always runs, before the commit.
		_refuse_delete(self.name)

	def _refuse_unless_kb_action(self):
		if self.flags.get("kb_action"):
			return
		frappe.throw(
			_(
				"A published article cannot be edited directly. Changes are made in a new Knowledge "
				"Article Version, and a KB Approver who did not write it publishes them."
			),
			title=_("Published articles are read-only"),
		)

	def _refuse_identity_change(self):
		"""A new row's number must fit its kind and department; a saved row keeps both. With no
		stored row to compare (an insert: v16 loads none, and ``is_new`` still reads true in
		``on_update``), the number is checked instead."""
		# Imported here, as onload imports publish: a controller that fails to import is force-deleted
		# by the next migrate (CLAUDE.md), so this one's own import stays frappe only.
		from erpnext_enhancements.knowledge_base import workflow

		stored = self.get_doc_before_save()
		if stored is None:
			number = self.name or self.get("kb_number")
			problems = workflow.number_problems(number, self.get("kind"), self.get("department_block"))
			if problems:
				frappe.throw(
					_(
						"{0} cannot be written: {1}. An article's number is made from its kind and "
						"department when its first version is approved (SOP-06-0001 is an SOP in 06 "
						"Operations)."
					).format(number, "; ".join(problems)),
					title=_("Not an article number"),
				)
			return
		changed = [
			label
			for field, label in (("kind", "kind"), ("department_block", "department"))
			if (stored.get(field) or None) != (self.get(field) or None)
		]
		if not changed:
			return
		frappe.throw(
			_(
				"{0} keeps its kind and department: they are part of its number, which never changes, "
				"and this would change its {1}. To reclassify it or move it to another department, "
				"start a new article with that kind and department, and once it is published, retire "
				"{0} and name the new article in the reason."
			).format(self.name, " and ".join(changed)),
			title=_("Article numbers do not change"),
		)


def _refuse_delete(name):
	frappe.throw(
		_(
			"Knowledge articles are never deleted, so that {0} keeps its number and its history. "
			"Retire it instead."
		).format(name),
		title=_("Articles are never deleted"),
	)
