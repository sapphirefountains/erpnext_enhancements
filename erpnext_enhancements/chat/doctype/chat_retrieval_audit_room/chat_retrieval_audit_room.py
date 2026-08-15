# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chat Retrieval Audit Room — one room touched by one privileged read.

``was_participant`` is the field the parent log exists for: a non-empty set of
``was_participant = 0`` rows is precisely the oversight event decision #12 makes
visible. It is resolved per room **at read time** by ``chat/audit.py`` and stored,
never re-derived later — membership changes, and a row that has to be recomputed
against today's roster answers a different question every time it is read.

**It has its own guards, and the claim that it did not need them was wrong.** This
docstring used to say that reaching a child row without loading its parent "means
bypassing the ORM altogether". It does not: ``frappe.delete_doc("Chat Retrieval
Audit Room", name)`` and ``frappe.get_doc(...).db_set(...)`` are ordinary ORM
paths that never touch the parent, and this DocType had no ``before_save`` and no
``on_trash`` to refuse them. The parent's protection is real and does not extend
here — a child row is a document in its own right.

That matters more here than the row count suggests. ``was_participant`` is signed
into the parent's ``chain_hash``, so deleting a child row does not erase the
evidence silently: it breaks the chain. But a break is discovered by the nightly
verifier at best, and reports as *tampering somewhere in the log* rather than as
*this row was removed*. Refusing at the controller is the difference between an
alarm and a prevention.

**This file's existence is load-bearing, which is not obvious.** Frappe's
``load_doctype_module`` is called for *every* DocType during ``bench migrate`` —
child tables included — and raises ``ModuleNotFoundError`` when the controller is
absent. Shipping the JSON without it aborted the v1.268.0 migrate partway, leaving
this DocType registered with no table for its parent, and blocked every subsequent
deploy of any change until the module appeared. ``tests/test_doctype_modules.py``
now asserts the sibling ``.py`` exists for exactly this reason.
"""

import frappe
from frappe.model.document import Document

from erpnext_enhancements.chat.doctype.chat_retrieval_audit.chat_retrieval_audit import (
	RETENTION_PURGE_FLAG,
)


class ChatRetrievalAuditRoom(Document):
	def before_save(self) -> None:
		"""Insert once, never update. The parent's rule, applied where it actually binds."""
		if not self.is_new():
			frappe.throw(
				frappe._("A chat retrieval audit room row cannot be modified after it is written."),
				frappe.PermissionError,
			)

	def on_trash(self) -> None:
		"""Refuse deletion unless the retention purge is explicitly in progress.

		The same flag as the parent, imported rather than re-declared: two constants spelled
		the same way are two constants, and the question "who may delete an audit row" has to
		have one answer.
		"""
		if not frappe.flags.get(RETENTION_PURGE_FLAG):
			frappe.throw(
				frappe._("Chat retrieval audit room rows are not deletable."),
				frappe.PermissionError,
			)

	def before_change(self) -> None:
		"""**The ``db_set`` tripwire.** See the parent controller for why this hook and not
		another."""
		frappe.throw(
			frappe._("A chat retrieval audit room row cannot be changed after it is written."),
			frappe.PermissionError,
		)
