# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chat Thread Digest — one summary per long thread, used by the degradation ladder.

Same contract as :mod:`chat.doctype.chat_room_digest.chat_room_digest`, and it shares that
module's span validator rather than re-implementing it: two copies of one invariant is how
the two drift, and the copy that drifts is the one further from the rule.

The docname is the thread root, which is a ``Chat Message`` name and therefore already
globally unique — so one digest per thread is enforced by the primary key.

**A thread only earns a digest once it exceeds the configured length.** Below it the thread
tier carries the whole thread verbatim, and a summary would be strictly worse than the text
it replaces. Its consumer is rung 4 of the degradation ladder: thread root plus the last N
replies stay verbatim and the middle is replaced by this.

``room`` is denormalised off the thread root, for the same reason ``Chat Attachment.room``
is: the retrieval gate filters on ``room`` in the ``WHERE`` clause and must not have to join
through ``Chat Message`` to find out which room a digest belongs to. It is backfilled here
rather than trusted from the caller, so a writer cannot get the denormalisation wrong.

Indentation is tabs.
"""

from __future__ import annotations

import frappe
from frappe.model.document import Document

from erpnext_enhancements.chat.doctype.chat_room_digest.chat_room_digest import validate_digest_span


class ChatThreadDigest(Document):
	def before_insert(self) -> None:
		if not self.digest_version:
			self.digest_version = 1
		self._backfill_room()

	def validate(self) -> None:
		self._backfill_room()
		validate_digest_span(self)

	def _backfill_room(self) -> None:
		if self.room or not self.thread_root:
			return
		room = frappe.db.get_value("Chat Message", self.thread_root, "room")
		if not room:
			frappe.throw(
				frappe._("Chat Thread Digest thread root {0} does not exist.").format(self.thread_root)
			)
		self.room = room
