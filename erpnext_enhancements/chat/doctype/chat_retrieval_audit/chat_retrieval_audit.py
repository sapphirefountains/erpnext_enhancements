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
   refuses unless the retention purge flag is set; ``before_change`` refuses ``db_set``.
   Catches the ``ignore_permissions`` writer and Administrator.

   **``db_set`` is not silent, and that was worth checking rather than assuming.** In v16,
   ``Document.db_set`` runs ``self.run_method("before_change")`` *before* it writes and
   ``on_change`` after — and ``before_change`` is invoked from **exactly one place in the
   whole framework**, that line. So a hook on it is a tripwire with no false positives: it
   cannot fire on an insert or an ordinary save, and because it runs before the write,
   throwing **prevents** the change rather than rolling one back that already happened.

   **What layer 2 still cannot catch, stated rather than implied.** ``frappe.db.set_value``
   and raw SQL load no document and reach no controller — and ``set_value`` takes a filter
   dict, so one call can rewrite many rows. Deletion has two holes of its own: ``on_trash``
   fires only when ``not for_reload and not ignore_on_trash``, so
   ``frappe.delete_doc(..., ignore_on_trash=True)`` and ``for_reload=True`` both delete
   without consulting this controller, and ``for_reload`` additionally suppresses the
   ``Deleted Document`` copy — the row simply vanishes, leaving only the chain break behind.
   An ``after_delete`` guard does **not** close that: it fires after the rows are gone, so a
   throw there rolls back a delete that already ran, which is worth less than it sounds when
   the caller swallows. Layers 3 and 4 are the answer to all of these, which is why there are
   four.
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

	def before_change(self) -> None:
		"""**The ``db_set`` tripwire**, and the hook choice is the whole point.

		``before_save`` never sees a ``db_set`` — that path writes the column directly and
		loads no document through the save machinery. ``before_change`` does: v16's
		``Document.db_set`` calls ``run_method("before_change")`` immediately before the write.

		Chosen over ``on_change`` for two reasons, both from the same source. It runs **before**
		the database write, so a throw prevents the change instead of rolling back one that has
		already landed. And ``before_change`` is invoked from **exactly one place in the entire
		framework** — that line in ``db_set`` — whereas ``on_change`` also fires on every
		ordinary insert and save, which would mean discriminating the legitimate write from the
		illegitimate one and getting that right forever.

		So there is no condition here. Any call at all is the violation.
		"""
		frappe.throw(
			frappe._("A chat retrieval audit row cannot be changed after it is written."),
			frappe.PermissionError,
		)
