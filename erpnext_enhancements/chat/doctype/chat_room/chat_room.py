# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chat Room — one conversation, in any of the four shapes the design needs.

``room_type`` is the discriminator: ``Direct Message``, ``Group``,
``Organization`` (a mirror of an org-structure unit) and ``Document`` (a room
hung off a Project, a Task, whatever). One table, four shapes, hash naming —
uniqueness lives in indexes, not in a readable primary key.

**This is the one chat DocType that carries a DocPerm row, and the exception is
deliberate.** Every other chat DocType ships ``"permissions": []`` so that the
third-party MCP server's generic ``get_document`` / ``list_documents`` refuse
them outright. ``Chat Room`` cannot: Frappe v16's socket server permission-checks
a ``doc_subscribe`` by calling back into Python under the joining user's own
session, and with zero DocPerm that join is refused **silently** — no error, no
ack, no timeout, the promise simply never settles. Realtime chat would appear to
work and deliver nothing. So the room gets a bare ``read`` grant for
``Chat User`` and pays for it with the ``permission_query_conditions`` +
``has_permission`` pair that narrows the grant to "your own rooms". The room row
carries a title, membership metadata and the Google space name — not message
text — so what the grant buys back is bounded. ``Chat Room`` stays on the
assistant-tool denylist regardless, because raw-SQL tooling consults no
permission layer at all.

Phase 1 scope: schema plus the two normalisations below. No Google call, no
space provisioning, no relay. ``provisioning_mode`` and ``provisioning_state``
exist; the automation that reads them does not yet.
"""

from frappe.model.document import Document


class ChatRoom(Document):
	def before_insert(self) -> None:
		"""Normalise the two column pairs that back composite UNIQUE indexes.

		``(linked_doctype, linked_document)`` and ``(dm_user_1, dm_user_2)`` are
		each a composite unique constraint added by patch, and Frappe cannot
		declare those on a DocField. That matters more than it looks:

		Frappe coerces an empty string to ``NULL`` **only** for a field that
		carries ``unique: 1`` on the DocField itself. Neither column here does —
		they cannot, the constraint is composite — so an empty ``Data``/``Link``
		reaches the database as ``''``, not as ``NULL``. MariaDB permits
		unlimited ``NULL``s in a unique index and exactly **one** ``''``. Leave
		the columns empty-stringed and the *second* group room ever created
		fails to insert, in production, with an error that names an index nobody
		set on purpose.

		So: both columns of a pair are set, or both are ``None``. Never one.
		"""
		self._blank_pair_to_null("linked_doctype", "linked_document")
		self._order_dm_pair()
		self._blank_pair_to_null("dm_user_1", "dm_user_2")

	def _blank_pair_to_null(self, first: str, second: str) -> None:
		"""Set both columns of a composite-key pair to ``None`` unless both are filled."""
		if not (self.get(first) and self.get(second)):
			self.set(first, None)
			self.set(second, None)

	def _order_dm_pair(self) -> None:
		"""Store a DM's two users in lexicographic order.

		The pair is the room's identity, and a conversation between A and B is
		the same conversation as one between B and A. Sorting here is what makes
		``unique(dm_user_1, dm_user_2)`` express that, instead of allowing two
		rooms and two divergent Google spaces for one pair of people.
		"""
		one: str | None = self.get("dm_user_1")
		two: str | None = self.get("dm_user_2")
		if one and two and one > two:
			self.dm_user_1, self.dm_user_2 = two, one
