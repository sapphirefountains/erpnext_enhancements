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

    Any writer of a Chat Message must treat ``frappe.DuplicateEntryError`` on
    ``gchat_message_name`` as SUCCESS. It means the row already exists, which is
    precisely the outcome the writer wanted. It is not an error, it must not be
    logged as one, it must not fail a job, and it must not be retried.

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

PHASE 1 SCOPE — WHAT THIS CONTROLLER MUST NOT GROW
--------------------------------------------------
Nothing here talks to Google, and no ``doc_events`` handler for this DocType may
either. That is invariant I1, not a preference: a Chat API call inside a hook
runs inside the inserting transaction and on a web worker, so a Google timeout
becomes a failed message insert. ERPNext is the source of truth; a mirror that
can refuse a write is not a mirror.

Phase 2 adds ``before_insert`` (allocate ``seq`` inside the transaction, derive
``client_message_id``, set ``sync_state``) and ``after_insert`` (denormalise onto
the room, fan out realtime, and write an **outbox row** — a ``Chat Relay Job``,
in the same transaction). The outbox row is the only thing the insert path is
allowed to know about Google.
"""

from frappe.model.document import Document


class ChatMessage(Document):
	pass
