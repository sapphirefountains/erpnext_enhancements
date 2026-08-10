# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chat Message Revision — the edit/delete audit trail, and the tightest table in the module.

Phase 2 §4.F needs a record of *what a message used to say*, who changed it, from which
side, and when. ADR 0009 never named a table for it; this DocType is an addition rather
than a rename of anything, so nothing else in the ADR refers to it by another name.

**THIS TABLE NEEDS TIGHTER PERMISSIONS THAN ``Chat Message``, NOT THE SAME ONES.**
That is the whole reason this docstring exists, and it is not the intuition a reader
arrives with — an audit table sounds like metadata.

``text_before`` is **superseded content**: the body a coworker wrote and then chose to
replace, which by definition exists nowhere else. ``Chat Message.text`` holds only the
current body, and Google's tombstone is content-free (``showDeleted=true`` returns the
delete time and ``deletionMetadata``, never the text — ADR §F.6.5 / §G.7.4). So the
sentence someone typed, regretted and edited out lives in exactly one column on this site,
and it is this one. A room member can read the *current* message; nobody gets to read the
version that was withdrawn without that read being a deliberate, audited act.

Concretely, and this is the standing obligation any future commit inherits:

* **Zero DocPerm, same as every chat DocType but ``Chat Room``** (ADR §F.18.1 Layer 1). A
  ``has_permission`` hook can only restrict what a DocPerm already grants, so with no
  DocPerm row the permission stack refuses before any hook is consulted — for everybody
  except ``Administrator``, who bypasses it unconditionally.
* **No ``permission_query_conditions`` / ``has_permission`` pair is registered for this
  DocType, and that is not an omission.** ``chat/permissions.py`` explains why the
  operational tables get no pair: "the rooms you are in" is not the right filter here.
  Room membership is what grants the *message*; it must not silently grant the withdrawn
  drafts of that message too. The only intended reader is the Phase 6 oversight role read
  from ``Chat Settings.admin_oversight_role`` — which ships **blank**, so the escape hatch
  fails closed — and every such read pays for itself with a ``Chat Retrieval Audit`` row via
  ``permissions.note_privileged_read``.
* **If a DocPerm is ever added here, the same commit owes a narrower pair than
  ``Chat Message``'s** — narrower, not copied. Copying ``chat_message_query`` would hand
  every room member the edit history of every message in the room, which is precisely the
  widening this table's existence makes possible for the first time.
* ``track_changes = 0`` and no ``in_global_search``, for the reason the message table has
  them: a ``Version`` row per write would put two more copies of the withdrawn body into the
  site's busiest table, which has no retention rule and which raw-SQL tooling can read.

WHY ``room`` IS DENORMALISED HERE
---------------------------------
Same reason as ``Chat Attachment.room``: the oversight read's membership filter must not
have to join through ``Chat Message`` to learn which room a revision belongs to, and
``(room, creation)`` is the index that answers "everything that changed in this room" in one
range scan. :meth:`ChatMessageRevision.before_insert` backfills it from the message so a
caller cannot get the denormalisation wrong — the field is ``reqd``, and mandatory checks run
*after* ``before_insert``, so the backfill lands in time.

WHY ``revision_no`` IS UNIQUE WITH ``message``
----------------------------------------------
Every writer of this table is retried: the relay sweeper re-drives a crashed job, and an
inbound event is redelivered at least once by Pub/Sub. ``unique(message, revision_no)``
makes "the same change recorded twice" a **failed insert** rather than two audit rows that
both claim to be revision 4. The constraint is composite, so Frappe cannot declare it on a
DocField — it exists only because ``patches/add_chat_phase2_indexes.py`` runs. Both columns
are ``reqd``, so neither hits ADR §F.2's empty-string-versus-NULL trap.
"""

from typing import Final

import frappe
from frappe.model.document import Document

#: ``change_type`` values. A ``Create`` row is written at insert, so the trail is complete
#: from the beginning; reconstructing an original body from the first edit's ``text_before``
#: works right up until the first edit that was itself lost.
CHANGE_CREATE: Final[str] = "Create"
CHANGE_EDIT: Final[str] = "Edit"
CHANGE_DELETE: Final[str] = "Delete"

#: ``origin`` values — the same three, spelled the same way, as ``Chat Message.sync_origin``.
#: Deliberate: an operator reading both tables during an incident should not have to translate.
ORIGIN_ERPNEXT: Final[str] = "ERPNext"
ORIGIN_GOOGLE_CHAT: Final[str] = "Google Chat"
ORIGIN_TRITON: Final[str] = "Triton"


class ChatMessageRevision(Document):
	def before_insert(self) -> None:
		"""Backfill ``room`` from the message so the denormalisation cannot be got wrong.

		``room`` is ``reqd``, and Frappe checks mandatory fields in ``_validate()`` — which
		runs *after* ``before_insert`` — so filling it here is in time and a caller that
		omits it gets a correct row rather than a validation error.

		Deliberately does **not** overwrite a value the caller supplied: a reconciliation
		writer that has already resolved the room should not pay for a second read, and a
		mismatch is a caller bug worth surfacing in the data rather than papering over here.

		``frappe.db.get_value`` rather than ``get_doc``: this runs on the hot edit path and
		the whole question is one column. Guarded with ``getattr(..., None) or ""`` because
		``doc_events`` fire during ERPNext's own test bootstrap, before this app's fields
		necessarily exist.
		"""
		if getattr(self, "room", None):
			return

		message = getattr(self, "message", None) or ""
		if not message:
			return

		self.room = frappe.db.get_value("Chat Message", message, "room")

	@staticmethod
	def next_revision_no(message: str) -> int:
		"""``max(revision_no) + 1`` for one message, allocated **inside the caller's transaction**.

		Lives on the controller rather than in the writer because the rule it encodes is a
		property of this table, and there are three writers (inbound ingest, the outbound relay
		recording an ERPNext-side edit, and the reconciliation sweep). Three copies of a
		``max() + 1`` is three chances for one of them to allocate from outside the transaction
		that inserts the row, at which point two concurrent writers both compute the same number.

		**That collision is expected, not prevented, and that is the design.** Every writer of
		this table is retried — the relay sweeper re-drives a crashed job, Pub/Sub redelivers at
		least once — so ``unique(message, revision_no)`` is what turns "the same change recorded
		twice" into a failed insert instead of two audit rows both claiming to be revision 4. The
		caller therefore attempts the insert inside
		``erpnext_enhancements.chat.sync.inbound.savepoint_catching_duplicates()`` and treats the
		collision as success. **Catching only ``frappe.DuplicateEntryError`` would not work**: the
		constraint is composite and non-primary, so it raises ``frappe.UniqueValidationError``
		(``PHASE2_VERIFIED.md`` §1.1).

		Returns ``1`` when there is nothing yet, which is the ``Create`` row — the trail starts at
		insert rather than at the first edit, so reconstructing an original body never depends on
		an edit that was itself lost.
		"""
		if not message:
			return 1
		row = frappe.db.get_value(
			"Chat Message Revision",
			{"message": message},
			"max(revision_no) as revision_no",
			as_dict=True,
		)
		return int((row or {}).get("revision_no") or 0) + 1
