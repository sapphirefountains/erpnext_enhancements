# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One act of administration over chat. **Append-only, enforced here rather than hoped for.**

Where :class:`~erpnext_enhancements.chat.doctype.chat_retrieval_audit.chat_retrieval_audit.ChatRetrievalAudit`
records a **read** of chat content, this records an **act of governance over it**: who was
granted the oversight role, who expanded a deleted message, what a retention run destroyed,
which export left the building, when the hash chain failed verification.

WHY THERE ARE TWO TABLES AND NOT ONE
====================================

ADR 0009 **X-1 rejected this DocType**, and it is reinstated here by explicit amendment
(addendum 2), approved 2026-08-13 — not by an agent deciding the ADR was inconvenient.

The reason X-1 was wrong is shape rather than principle. ``Chat Retrieval Audit`` is
read-shaped all the way down: its required fields are ``accessed_by``, ``actor_type``,
``purpose``; its child table is one row per *room read* with a ``seq`` range and a
``was_participant`` flag; its whole value is that ``was_participant = 0`` names the oversight
event decision #12 exists to make visible. A role grant has no rooms, no seq range and no
participation — stored there it is a row with every load-bearing field null, and the
compliance query ``child rows where was_participant = 0`` stops being one line.

**Two audit tables is acceptable; two definitions of "a read" is not** (§4.D.1). These two do
not overlap: nothing that returns message content is recorded here, and nothing recorded here
returns message content. The unified ``Chat Access Report`` reads both.

WHAT MUST NEVER APPEAR IN THIS TABLE
====================================

**Message text, in any field.** A retention run records how many rows it destroyed and over
which ``seq`` range; it does not record what they said. A log of what was deleted that contains
what was deleted has not deleted anything — it has moved it somewhere with weaker permissions
and called that an audit trail.

IMMUTABILITY IS FOUR LAYERS, AND EACH IS BYPASSED BY THE NEXT ONE DOWN
======================================================================

Identical in shape to ``Chat Retrieval Audit``, deliberately, because a second immutability
design would be a second thing to get right:

1. **DocPerm** — ``System Manager`` gets ``read`` and ``report``. No ``write``, no ``create``,
   no ``delete``, no ``export``. Bypassed by ``ignore_permissions=True``, which the writer
   itself needs, and entirely by ``Administrator``.
2. **This controller.** ``before_save`` refuses any save that is not an insert; ``on_trash``
   refuses unless the retention flag is set. Catches ``ignore_permissions`` and Administrator,
   because it is application code rather than a permission check. Bypassed by ``db_set`` /
   ``frappe.db.set_value`` / raw SQL, none of which load a document.
3. **A source scan** (``tests/test_chat_audit_immutability.py``) forbidding exactly those
   bypasses anywhere outside the one writer module. It is the only layer that can see layer
   2's blind spot.
4. **The hash chain** (``chain_hash``, computed in ``chat/audit.py``, on its **own stream**).
   Catches a direct database edit after the fact by making it *detectable*.

**Layer 2 has two holes and they are named rather than papered over.** ``on_trash`` fires only
when ``not for_reload and not ignore_on_trash`` (verified against frappe v16.9.0's
``delete_doc.py``), so ``frappe.delete_doc(..., ignore_on_trash=True)`` and ``for_reload=True``
both delete a row with this controller never consulted — and ``for_reload`` additionally
suppresses the ``Deleted Document`` copy, so the row leaves no trace but the chain break.
An ``after_delete`` guard does **not** close this: it runs after the rows are gone, so throwing
there rolls back a delete that already happened rather than preventing one. Layer 3 covers both
holes *inside this repo*; from ``bench console``, a patch in another app, or the desk, they are
open, and layer 4 is what remains.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

#: Shared with ``Chat Retrieval Audit`` on purpose: one flag, one setter, one call site, for
#: both audit tables. Two flags would mean two things to grep for and two ways to be wrong
#: about which one a given purge honours.
#:
#: Nothing sets it today. Retention ships ``Disabled`` and ``audit_survives_purge`` on, so the
#: retention job never reaches these tables — this exists so that a future rule has exactly one
#: door rather than reaching for ``frappe.db.delete``.
RETENTION_PURGE_FLAG = "chat_audit_retention_purge"

#: Events an actor performs deliberately, and which therefore owe a reason. A retention run and
#: a failed chain verification are **not** here: both are the scheduler acting, and demanding a
#: reason from a cron job produces a constant string that means nothing.
_REASON_REQUIRED_EVENTS = frozenset(
	{
		"tombstone_expanded",
		"export_requested",
		"export_downloaded",
	}
)

#: The floor a reason has to clear. Deliberately crude, and §4.D.2 says so: reject the empty
#: and the placeholder ("x", "-", "test"), and do not attempt cleverer validation. A reason
#: filter somebody has to defeat is a reason filter somebody defeats with "investigating".
_MIN_REASON_LENGTH = 12


class ChatAuditLog(Document):
	def validate(self) -> None:
		"""A deliberate act of oversight owes a reason. §4.D.2, G6-10.

		Enforced in the controller rather than as ``reqd`` on the field, because the
		requirement is conditional on ``event_type`` and a JSON ``reqd`` cannot express that —
		it would demand a reason from the retention scheduler too, which would produce a
		constant string and teach everyone that the field means nothing.

		**Refused, not accepted-and-blanked.** G6-10 is explicit: a read with a too-short
		reason must fail. Storing the read with an empty reason records that somebody looked
		and destroys the only field that says why.
		"""
		if self.event_type not in _REASON_REQUIRED_EVENTS:
			return
		reason = (self.reason or "").strip()
		if len(reason) < _MIN_REASON_LENGTH:
			frappe.throw(
				frappe._(
					"{0} needs a reason of at least {1} characters, recorded with the action."
				).format(self.event_type, _MIN_REASON_LENGTH),
				frappe.ValidationError,
			)

	def before_save(self) -> None:
		"""Insert once, never update.

		``is_new()`` rather than a ``docstatus`` check: this DocType is not submittable, so
		submit/cancel are not available as an immutability mechanism and pretending otherwise
		would leave the row editable.
		"""
		if not self.is_new():
			frappe.throw(
				frappe._("A chat audit log row cannot be modified after it is written."),
				frappe.PermissionError,
			)

	def on_trash(self) -> None:
		"""Refuse deletion unless a retention purge is explicitly in progress.

		``on_trash`` rather than ``after_delete``: after the row is gone, throwing rolls back
		a delete that already happened rather than preventing one, and the difference matters
		when the caller has swallowed exceptions.
		"""
		if not frappe.flags.get(RETENTION_PURGE_FLAG):
			frappe.throw(
				frappe._("Chat audit log rows are not deletable."),
				frappe.PermissionError,
			)

	def before_change(self) -> None:
		"""**The ``db_set`` tripwire.** See ``Chat Retrieval Audit`` for the reasoning in full.

		Short version: ``before_save`` never sees a ``db_set``, ``before_change`` does, it runs
		before the write so a throw prevents rather than rolls back, and in v16 it is invoked
		from exactly one place in the framework — so any call at all is the violation and no
		condition is needed.
		"""
		frappe.throw(
			frappe._("A chat audit log row cannot be changed after it is written."),
			frappe.PermissionError,
			)
