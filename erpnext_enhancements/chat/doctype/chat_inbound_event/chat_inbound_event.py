# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Raw capture of everything arriving from Google, before any interpretation.

The webhook's whole job is: verify the JWT, write one of these rows, enqueue, return 200 fast.
Parsing happens later, in a worker, where a bug is recoverable.

**``payload`` holds the event body UNTOUCHED.** Not normalised, not re-serialised, not trimmed to
the fields today's parser happens to read. Three things depend on that:

* it is the only way to reprocess after a parser bug — the interpretation is redone, the delivery
  is not lost;
* it is the fixture source. Parser tests run against verbatim captured payloads rather than
  hand-written approximations of what we think Google sends;
* it is the only evidence available when Google changes a payload shape, which they do — eleven
  Chat API changes landed in the seven months to 2026-08-07.

**``pubsub_message_id`` is unique, and that single constraint is the entire duplicate defence.**
Pub/Sub is at-least-once by contract: the same delivery *will* arrive twice, and not rarely. The
second insert therefore raises **``frappe.UniqueValidationError``**, which the puller treats as
success and acks — structural dedupe, not a procedural "have I seen this?" check that races with
itself under concurrency.

**The exception class matters and the obvious guess is wrong.** ``frappe.DuplicateEntryError`` is
raised *only* for a **primary key** collision — a duplicate ``name``. Every other unique index,
including this one, raises ``frappe.UniqueValidationError``, and the two share no base beyond
``Exception`` (``DuplicateEntryError(NameError)`` vs ``UniqueValidationError(ValidationError)``).
So ``except frappe.DuplicateEntryError`` alone would **not** catch the collision this design
depends on, and structural dedupe would fail open into a logged error on every redelivery — the
exact opposite of the intent. Catch both, inside ``frappe.database.database.savepoint``, because a
failed statement can otherwise poison the surrounding transaction. Verified against Frappe v16
``model/base_document.py:db_insert`` on 2026-08-09; ADR 0009 §G.3.1 and §G.8 Rule 2 both name only
``DuplicateEntryError`` and are wrong on this point.

The HTTP interaction transport carries no Pub/Sub id and leaves the field empty;
Frappe coerces empty to ``NULL`` on a unique DocField and MariaDB allows unlimited ``NULL``s in a
unique index, so those rows coexist. That coercion only fires because ``unique: 1`` is declared on
the DocField — an index added by patch alone would let the *second* empty row collide, and only in
production.

One row is one **delivery**, not one contained resource: Workspace Events emits batched
``batchCreated`` / ``batchUpdated`` / ``batchDeleted`` / ``batchChanged`` variants, and the worker
fans one row out to N message operations so that acking the delivery stays a single decision.

WHO WRITES AND READS THESE ROWS (Phase 2)
-----------------------------------------
* :func:`erpnext_enhancements.chat.sync.pubsub.ingest_delivery` writes them — one per Pub/Sub
  delivery, payload verbatim, **committed before the ack**.
* :func:`erpnext_enhancements.chat.sync.inbound.process_inbound_event` settles them, and is safe
  to run twice on the same row: ``status`` is the idempotence gate.
* :func:`erpnext_enhancements.chat.sync.inbound.sweep_stuck_inbound_events` re-drives ``Received``
  rows the queue lost. It is the **only** delivery guarantee there is — the production deploy
  ``FLUSHDB``s the queue Redis and Frappe v16 wires no RQ retries — which is why ``status`` and
  ``received_at`` carry a composite index and why ``Failed`` is never swept: a ``Failed`` row is
  evidence somebody has to look at, not work to redo.
"""

from typing import Final

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, cint, now_datetime

#: ``transport`` values — which of the two inbound mechanisms delivered the event.
TRANSPORT_INTERACTION: Final[str] = "Interaction"
TRANSPORT_WORKSPACE_EVENT: Final[str] = "Workspace Event"

#: ``status`` values.
STATUS_RECEIVED: Final[str] = "Received"
STATUS_PROCESSED: Final[str] = "Processed"
STATUS_IGNORED: Final[str] = "Ignored"
STATUS_FAILED: Final[str] = "Failed"

#: Only settled deliveries are purged. ``Failed`` is exempt on purpose — deleting the evidence of
#: the events we could not process is how an outage becomes invisible — and ``Received`` is exempt
#: because it means the worker has not finished with the row yet.
PURGEABLE_STATUSES: Final[tuple[str, ...]] = (STATUS_PROCESSED, STATUS_IGNORED)

#: Matches the ``default_log_clearing_doctypes`` hook. Frappe's ``add_default_logtypes`` never
#: updates an existing ``Logs To Clear`` row, so this number is chosen once.
DEFAULT_RETENTION_DAYS: Final[int] = 30

#: Batched so the delete stays bounded inside ``daily_maintenance``.
_PURGE_BATCH: Final[int] = 500


class ChatInboundEvent(Document):
	@staticmethod
	def clear_old_logs(days: int | None = None) -> None:
		"""Retention entry point Frappe's log clearing calls.

		Its *existence* is load-bearing: ``remove_unsupported_doctypes()`` runs first in
		``run_log_clean_up`` and silently drops the ``Logs To Clear`` row of any doctype whose
		controller has no ``clear_old_logs(days)``. Retention then stops with no error anywhere.
		"""
		cutoff = add_days(now_datetime(), -cint(days or DEFAULT_RETENTION_DAYS))
		while True:
			names: list[str] = frappe.get_all(
				"Chat Inbound Event",
				filters={"creation": ("<", cutoff), "status": ("in", list(PURGEABLE_STATUSES))},
				pluck="name",
				order_by="creation asc",
				limit=_PURGE_BATCH,
			)
			if not names:
				return
			frappe.db.delete("Chat Inbound Event", {"name": ("in", names)})
			frappe.db.commit()
			if len(names) < _PURGE_BATCH:
				return
