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

``seq_high_water`` IS THE SEQUENCE ALLOCATOR, NOT A MIRROR
-----------------------------------------------------------
The column's own field description still calls it an *"advisory mirror"* of
``MAX(seq)`` and says never to allocate from it. Phase 2 supersedes that, and the
description is the thing that needs the edit:
:func:`erpnext_enhancements.chat.sync.outbox.allocate_seq` issues
``UPDATE tabChat Room SET seq_high_water = seq_high_water + 1 WHERE name = %s``
and reads the value back, both inside the inserting transaction. The ``UPDATE``
takes an exclusive lock on **one row** — this one — held to commit, which is what
makes the read-back safe under concurrency. The rejected alternative,
``MAX(seq) … FOR UPDATE`` over ``tabChat Message``, takes range locks across the
busiest table in the feature and grows a deadlock surface with the size of the
room.

``unique(room, seq)`` remains the *guarantee*; this counter is the
*optimisation* that stops the guarantee ever being tested. Which is exactly why
:meth:`ChatRoom.validate` refuses to let the counter move backwards: a decrement
re-issues sequence numbers that already exist on messages, so every subsequent
insert into that room collides with the unique index, forever, and the error
names a constraint nobody set on purpose.

Phase 1 scope was schema plus the two normalisations below. No Google call, no
space provisioning, no relay. ``provisioning_mode`` and ``provisioning_state``
exist; the automation that reads them does not yet.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import cint


class ChatRoom(Document):
	def validate(self) -> None:
		"""Refuse a backwards ``seq_high_water``. The counter only ever goes up.

		``seq_high_water`` is ``read_only`` on the DocField, so the Desk cannot lower it —
		but ``read_only`` is a form property and not a constraint, and a script, a fixture
		or a well-meaning data fix reaches the column regardless. Lowering it hands the next
		message a ``seq`` that an existing message already holds, and ``unique(room, seq)``
		then refuses every insert into that room until somebody works out what happened.

		Only *document saves* pass through here. The allocator's raw ``UPDATE`` deliberately
		does not, which is the point: it is the one writer that may move the counter, it only
		ever increments, and routing it through a full document save would take a second lock
		and churn ``modified`` on the room for every message sent.
		"""
		if self.is_new():
			# A new room starts at zero. Not None: `coalesce` in the allocator covers a NULL,
			# but an explicit zero means the drift check has something to compare from day one.
			self.seq_high_water = cint(getattr(self, "seq_high_water", None))
			return

		previous = cint(frappe.db.get_value(self.doctype, self.name, "seq_high_water"))
		current = cint(getattr(self, "seq_high_water", None))
		if current < previous:
			frappe.throw(
				frappe._(
					"Chat Room {0}: seq_high_water cannot go backwards ({1} to {2}). It is the "
					"per-room message sequence allocator, not a statistic — lowering it re-issues "
					"sequence numbers that existing messages already hold, and unique(room, seq) "
					"then rejects every new message in this room."
				).format(self.name, previous, current),
				title=frappe._("Chat Room"),
			)

		self._refuse_backwards_retirement(previous_high_water=previous)

	def _refuse_backwards_retirement(self, *, previous_high_water: int) -> None:
		"""``retired_below_seq`` only goes up, and never past ``seq_high_water``.

		The mirror of the rule above it, and refused for a different reason. Lowering the
		retirement floor does not corrupt an allocator — it **re-admits derived coverage of
		messages that no longer exist**, and nothing can rebuild that coverage correctly
		because the source is gone. The stale chunks and digests simply become servable again,
		which is the one outcome retirement exists to prevent.

		Both invariants are backstops rather than the enforcement point: the field is
		``read_only`` on the DocField, which is a form property and not a database constraint,
		and the writer moves it with ``db.set_value``, which skips ``validate()`` entirely.
		What this catches is a Desk save, a fixture, or a data fix — which, per
		``retire_rules``, is exactly the case that produces a mark the boundary snap never saw.
		"""
		from erpnext_enhancements.chat import retire_rules

		previous = cint(frappe.db.get_value(self.doctype, self.name, "retired_below_seq"))
		current = cint(getattr(self, "retired_below_seq", None))

		refusal = retire_rules.refuse_lowering(previous, current)
		if refusal:
			frappe.throw(
				frappe._("Chat Room {0}: {1}").format(self.name, refusal),
				title=frappe._("Chat Room"),
			)

		high_water = max(cint(getattr(self, "seq_high_water", None)), previous_high_water)
		if current > high_water:
			frappe.throw(
				frappe._(
					"Chat Room {0}: retired_below_seq ({1}) cannot exceed seq_high_water ({2}). "
					"That would declare messages retired which were never allocated, and the "
					"indexer's watermark floor would then skip every message the room goes on "
					"to receive."
				).format(self.name, current, high_water),
				title=frappe._("Chat Room"),
			)

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
