# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One privileged read of chat content. **Append-only, and enforced here rather than hoped for.**

Decision #12 lets a configured role read conversations it is not a participant in. This row
is the other half of that bargain: the read is allowed *because* it is recorded. A log the
reader can edit afterwards is not a record of what they did, it is a record of what they were
willing to leave behind — so this controller refuses every update and every delete.

**Four layers, because each one is bypassable and they fail in different directions.**

1. **DocPerm** (the JSON): ``System Manager`` gets ``read`` and ``report``, nothing else. No
   ``write``, no ``create``, no ``delete``. Bypassed by ``ignore_permissions=True`` — which
   the writer itself needs — and by Administrator.
2. **This controller.** ``before_save`` refuses any save that is not an insert; ``on_trash``
   refuses unless the retention purge flag is set. Catches the ``ignore_permissions`` writer
   and Administrator. Bypassed by ``db_set``/``frappe.db.set_value``/raw SQL, which never
   load a document and so never reach a controller.
3. **A source scan** (``tests/test_chat_audit_immutability.py``) forbidding exactly those
   bypasses against these tables anywhere outside the one writer module. Catches what layer 2
   cannot see. Bypassed by anything that is not Python in this repo.
4. **The hash chain** (``chain_hash``, computed in ``chat/audit.py``). Catches a direct
   database edit — after the fact, by making it *detectable*. It does not make tampering
   impossible: anyone with MariaDB write access or root on the VM can rewrite rows and
   recompute the chain forward. Offsite append-only shipment is the answer to that, and it is
   a decision to be taken deliberately rather than assumed here.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

#: The one flag that permits a delete, set by the one caller allowed to purge. Named rather
#: than inlined so that ``grep`` finds every setter, and so that "who is allowed to delete an
#: audit row" is answerable by reading one constant instead of auditing every caller.
#:
#: Nothing sets it today. The retention purge is explicitly documented as **never** covering
#: these tables (ADR §F.17) — this exists so that a future retention rule has exactly one
#: door rather than reaching for ``frappe.db.delete``.
RETENTION_PURGE_FLAG = "chat_audit_retention_purge"


class ChatRetrievalAudit(Document):
	def before_save(self) -> None:
		"""Insert once, never update.

		``is_new()`` rather than a ``docstatus`` check: this DocType is not submittable, so
		submit/cancel are not available as an immutability mechanism and pretending otherwise
		would leave the row editable.
		"""
		if not self.is_new():
			frappe.throw(
				frappe._("A chat retrieval audit row cannot be modified after it is written."),
				frappe.PermissionError,
			)

	def on_trash(self) -> None:
		"""Refuse deletion unless the retention purge is explicitly in progress.

		``on_trash`` rather than ``after_delete``: after the row is gone, throwing rolls back
		a delete that already happened rather than preventing one, and the difference matters
		when the caller has swallowed exceptions.
		"""
		if not frappe.flags.get(RETENTION_PURGE_FLAG):
			frappe.throw(
				frappe._("Chat retrieval audit rows are not deletable."),
				frappe.PermissionError,
			)
