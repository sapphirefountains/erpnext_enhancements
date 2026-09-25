# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Knowledge Article: the approved, published text of one KB number, and nothing else.

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

A determined System Manager can still write past the ORM with ``frappe.db.set_value`` or raw SQL.
The Knowledge Base Integrity report (PR 4) is what detects that; this controller only makes sure
nobody does it by accident.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class KnowledgeArticle(Document):
	def validate(self):
		self._refuse_unless_kb_action()

	def on_update(self):
		# Also runs on insert. Runs even when flags.ignore_validate skipped validate() above.
		self._refuse_unless_kb_action()

	def before_rename(self, old, new, merge=False):
		frappe.throw(
			_(
				"A KB number is permanent: people and AI tools cite it. {0} cannot be renamed or merged."
			).format(old),
			title=_("KB numbers do not change"),
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


def _refuse_delete(name):
	frappe.throw(
		_(
			"Knowledge articles are never deleted, so that {0} keeps its number and its history. "
			"Retire it instead."
		).format(name),
		title=_("Articles are never deleted"),
	)
