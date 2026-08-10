# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chat Message — the hot table, and the four properties that cannot be changed later.

Once messages exist, four things on this DocType are effectively unfixable, so
they are stated here rather than left to be inferred from the JSON:

1. **``autoname: hash`` / ``naming_rule: Random``.** Never a naming series: a
   series allocates from one ``tabSeries`` row under a lock held for the whole
   inserting transaction, so every message on the site would serialize on it,
   and the counter leaks message volume into a key that gets quoted in URLs.
   Never a child table of ``Chat Room``: Frappe loads child tables *in full*
   with the parent, so opening a room would materialise every message ever sent
   in it.
2. **``sort_field`` is ``creation`` and never ``modified``.** Editing a
   three-week-old message must not teleport it to the bottom of the transcript.
   Reads sort by ``seq`` — stronger still, because two inserts into one room can
   share ``creation`` to the microsecond under concurrency, and because Frappe
   writes ``creation`` in site-local time while the database clock is UTC, which
   makes any SQL-side comparison against ``NOW()`` wrong by the site's offset.
   ``seq`` has no timezone.
3. **``track_changes = 0``.** A ``Version`` row per edit carries two full copies
   of the message body, into the site's already-busiest table, which has no
   retention rule and which raw-SQL tooling can read. Edit history that matters
   lives on the row itself (``is_edited``, ``edited_at``).
4. **``gchat_message_name`` is ``Data(255)`` with ``unique: 1`` on the DocField.**
   See below — this is invariant I2 made structural.

Also, and for the same "silently expensive" reason: no field is
``in_global_search`` and ``index_web_pages_for_search`` is 0. The site's
``__global_search`` is already its fourth-largest table; message bodies would
make it the largest inside a year and would put chat text in a search surface
whose behaviour under a zero-DocPerm DocType nobody has verified. Comments have
no toggle in Frappe's DocType schema, so they are closed the same way everything
else in the desk is: this DocType ships an **empty permissions array**, so it has
no list view, no form, no report view, no export, and therefore no comment box.

WHY ``gchat_message_name`` IS UNIQUE, AND THE RULE EVERY WRITER MUST FOLLOW
--------------------------------------------------------------------------
The same Google message reaches us by three independent routes — a Workspace
Events redelivery, an at-least-once Pub/Sub duplicate, and a reconciliation
replay of the 28-day event window — and all three converge on one resource name.
The unique index makes dedupe **structural rather than procedural**: the second
insert cannot happen.

    Any writer of a Chat Message must treat a unique-key collision on
    ``gchat_message_name`` as SUCCESS. It means the row already exists, which is
    precisely the outcome the writer wanted. It is not an error, it must not be
    logged as one, it must not fail a job, and it must not be retried.

**Catch BOTH exceptions, and the obvious guess is the wrong one.**
``frappe.DuplicateEntryError`` is raised *only* for a **primary key** collision — a
duplicate ``name``. Every other unique index, this one included, raises
``frappe.UniqueValidationError``, and the two share no base beyond ``Exception``
(``DuplicateEntryError`` derives from Frappe's own ``NameError``,
``UniqueValidationError`` from ``ValidationError``). So an ``except
frappe.DuplicateEntryError`` here — which is what ADR 0009 §G.3.1 and §G.8 Rule 2 both
instruct, and what an earlier revision of this docstring instructed — would **not catch
the collision the whole design rests on**, and structural dedupe would fail open into a
logged error on every at-least-once Pub/Sub redelivery. The correct form:

    from frappe.database.database import savepoint

    with savepoint(catch=(frappe.DuplicateEntryError, frappe.UniqueValidationError)):
        doc.insert()

The savepoint is mandatory rather than hygiene: a failed statement can poison the
surrounding transaction, and Frappe's own docstring models exactly this pattern. Note
also that ``insert(ignore_if_duplicate=True)`` covers only the primary-key branch and is
not enough, and that ``show_unique_validation_message`` calls ``frappe.msgprint`` before
raising — so an expected duplicate leaks a user-visible "must be unique" message unless
it is cleared. Verified against Frappe v16 ``model/base_document.py:db_insert``,
2026-08-09.

Attempt the insert and catch the duplicate. Never ``SELECT``-then-``INSERT``:
that is a TOCTOU bug at exactly the moment it matters — two workers, one
redelivered event — and it is the shape that produces the duplicate message that
then gets relayed back out.

The width is load-bearing too. Google documents the *format* of a message
resource name and declines to bound its length, so we bound it: ``Data(255)`` is
``varchar(255)``, ~3x inside MariaDB's 3072-byte index-key limit under
``ROW_FORMAT=DYNAMIC`` and ~5x the largest name anyone has observed. Frappe's
``Data`` default is ``varchar(140)``, and at 140 two distinct Google messages can
truncate to the same string and collide into one row — silent message loss.
``unique`` is declared on the DocField and not only in a patch, because Frappe
coerces ``""`` to ``NULL`` *only* for fields carrying ``unique: 1``; without it,
every not-yet-relayed message stores ``""`` and the second one fails to insert.

``sender`` IS NOT REQUIRED, AND ``sender_email`` IS WHY
-------------------------------------------------------
``sender`` carried ``reqd: 1`` through Phase 1. Phase 2 relaxes it, deliberately
and while the table is still empty, because a Google Chat member need not map to
an ERPNext ``User`` at all — an external participant in a space, or a Workspace
user nobody has provisioned here. With ``reqd`` on, those messages **cannot be
inserted**: the mirror silently drops precisely the messages in which the
conversation left the company, and ERPNext — which is the record of what was
said — acquires holes in the rows most likely to be asked about later.

So the constraint moved from "``sender`` is present" to **"``sender`` OR
``sender_email``, never neither"**, and it moved from the DocField to
:meth:`ChatMessage.validate`, because Frappe has no way to express an
either-or across two fields in the JSON. Relaxing the DocField without adding the
pair check would not be a smaller rule, it would be *no* rule — an unattributable
message row, which is worse than the insert failing.

WHAT THIS CONTROLLER MUST NOT GROW
----------------------------------
Nothing here talks to Google, and no ``doc_events`` handler for this DocType may
either. That is invariant I1, not a preference: a Chat API call inside a hook
runs inside the inserting transaction and on a web worker, so a Google timeout
becomes a failed message insert. ERPNext is the source of truth; a mirror that
can refuse a write is not a mirror. The rule is enforced by an AST check over
this file and its siblings in ``tests/test_chat_outbox.py``, not by a comment.

Phase 2 adds the write path, and **the body of it lives in
:mod:`erpnext_enhancements.chat.sync.outbox`, not here.** There are three writers
— the ERPNext composer, the inbound ingest and the reconciliation backfill — and
a controller that implemented the path itself would be one of three copies that
agree on a good day. This class is the wiring; ``outbox`` is the design, and its
module docstring is where the reasoning lives (why ``unique(room, seq)`` is the
guarantee and the ``seq_high_water`` counter only the optimisation, why the
``Chat Room`` row lock is also what serialises ``job_seq``, why the enqueue is
``after_commit`` and why it is not the delivery guarantee).

**One ordering fact that surprised the implementation and is worth stating here,
because it contradicts the sentence this docstring used to carry.**
``Document.insert`` runs ``before_insert`` *before* ``set_new_name``, and
``set_new_name`` begins by assigning ``doc.name = None`` for every autoname other
than ``prompt``/``UUID`` (``frappe/model/naming.py``, v16). So ``doc.name`` does
not exist during ``before_insert`` and ``client_message_id`` — which is derived
from it — **cannot** be minted there. It is minted in :meth:`validate`, the first
hook that runs after the name is final and still before Frappe's mandatory-field
check. ``gchat/ids.py`` carried the same stale claim and now records the ordering
instead, so nobody moves the derivation back on the strength of a comment.

WHAT AN EDIT AND A DELETE DO, AND WHERE THE DELETE IS NOT
----------------------------------------------------------
:meth:`on_update` relays an edited body and a soft delete; :meth:`on_trash`
**refuses**. The delete in this system is ``is_deleted`` flipping 0 → 1 with
``text`` left on the row, because Google's tombstone is content-free — so this
row is the last copy of what was said, and a hard delete destroys it rather than
hiding it. Both bodies live in ``sync.outbox`` with the rest of the write path,
and both have to say no far more often than yes: Frappe runs ``on_update`` on the
*insert* as well, and most saves of a message change nothing a mirror can see.
"""

import frappe
from frappe.model.document import Document

from erpnext_enhancements.chat.sync import outbox


class ChatMessage(Document):
	def before_insert(self) -> None:
		"""Allocate ``seq``, normalise the body, resolve the thread, decide the relay gate.

		Everything that does not need ``doc.name`` — see the module docstring for why that
		is a real constraint rather than a preference. Delegated wholesale to
		:func:`erpnext_enhancements.chat.sync.outbox.prepare_new_message`.
		"""
		outbox.prepare_new_message(self)

	def validate(self) -> None:
		"""Attribution, the naming-dependent derivation, and the edited body.

		Runs on every save, not only the first, which is why the ``client_message_id``
		derivation inside is idempotent: re-minting an idempotency key Google has already
		seen would make an edit look like a new message. ``text_plain``, by contrast, must
		*not* be idempotent across an edit — it is the column the byte budget, the relay
		worker and Phase 5's retrieval all read instead of ``text``, so leaving it at the
		value ``before_insert`` computed would relay the wording the author just replaced.
		"""
		self._validate_attribution()
		outbox.derive_client_message_id(self)
		outbox.refresh_text_plain(self)

	def after_insert(self) -> None:
		"""Denormalise onto the room, mark it read for the author, write the outbox row,
		publish, notify — all in this transaction, and none of it touching Google."""
		outbox.finalise_new_message(self)

	def on_update(self) -> None:
		"""Propagate an edit or a soft delete outbound — §4.F's ERPNext → Chat half.

		**This fires on the insert as well**, immediately after :meth:`after_insert`, because
		``Document.insert`` calls ``run_post_save_methods()``. The body decides; it is in
		``outbox`` and not here, and the reason it has to say no more often than yes is in
		that module's docstring.
		"""
		outbox.propagate_message_change(self)

	def on_trash(self) -> None:
		"""Refuse a hard delete. The delete in this system is ``is_deleted``, and only that.

		Google's tombstone keeps no content, so this row is the last copy of what was said —
		see :func:`erpnext_enhancements.chat.sync.outbox.refuse_hard_delete` for the whole
		argument, including why ``on_trash`` could not relay the delete even if it were
		allowed to happen.
		"""
		outbox.refuse_hard_delete(self)

	def _validate_attribution(self) -> None:
		"""Enforce the one rule the DocField cannot: ``sender`` OR ``sender_email``.

		``sender`` is no longer ``reqd`` — see the module docstring for why an
		unattributable Chat member must still be stored — so this is the whole of
		the attribution guarantee. Frappe cannot express an either-or across two
		fields in DocType JSON, which is exactly why it lives here rather than
		being left to the writers: there are three of them (the ERPNext compose
		path, the inbound ingest and the reconciliation backfill) and the one that
		forgets is the one that runs at 2am against a space with a guest in it.

		``getattr(..., None) or ""`` rather than ``self.sender``: ``validate``
		fires during ERPNext's own test bootstrap, before this app's fields
		necessarily exist, and a missing attribute here would turn a fresh-DB
		install into a crash rather than a validation error.
		"""
		sender = (getattr(self, "sender", None) or "").strip()
		sender_email = (getattr(self, "sender_email", None) or "").strip()

		if not sender and not sender_email:
			frappe.throw(
				frappe._(
					"A chat message must record who wrote it: set Sender (an ERPNext User) or "
					"Sender Email (a Chat member with no ERPNext account). Never neither — "
					"ERPNext is the record of what was said."
				),
				title=frappe._("Chat Message"),
			)
