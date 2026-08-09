# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The transactional outbox. One row per outbound Google Chat operation.

**Why a table and not ``frappe.enqueue``. This is the load-bearing sentence of the whole relay
design: the production deploy issues ``FLUSHDB`` against the queue Redis.** An ordinary,
successful deploy therefore destroys every job that was enqueued but had not yet run — silently,
because the enqueue call already returned success and nothing observes the loss. This has been
confirmed here before: pending Drive-folder jobs vanished across a deploy and the missing folders
were the only symptom. So the queue is a *latency optimisation*, never the delivery guarantee.
The guarantee is this row plus the sweeper that picks it up: a row survives the deploy, and the
worst a lost enqueue can cost is one sweep interval of delay. Phase 2 must enqueue **after
commit** (``enqueue_after_commit=True``) for the same reason in miniature — a job that starts
before its row is committed cannot find it.

**Why a separate table rather than sweeping ``Chat Message`` itself**, which is the cheaper shape
and the one an existing house sweeper uses:

1. Retry bookkeeping on the message row would churn ``modified``, and the room digest watermark
   is ``(max(seq), count(*), max(modified))`` — every relay attempt would invalidate every cached
   digest for that room.
2. Most outbound operations have no message at all: space creation, space patching, membership
   add/remove and attachment upload each need a retry budget, an error field and an ordering
   position.
3. Ordering is over *operations*, not messages. ``unique(room, job_seq)`` gives the per-room FIFO
   that Create-Before-Edit depends on directly.

``Chat Message.sync_state`` stays as the denormalised, read-only mirror the SPA renders, written
with ``update_modified=False``.

Schema only in Phase 1. No worker, no scheduler, no enqueue, no Google call. Phase 2 owns the
transition function; everything here is inert.
"""

from typing import Final

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, cint, now_datetime

#: ``operation`` values. The enum selects the handler; the payload carries arguments only.
OPERATION_MESSAGE_CREATE: Final[str] = "Message Create"
OPERATION_MESSAGE_UPDATE: Final[str] = "Message Update"
OPERATION_MESSAGE_DELETE: Final[str] = "Message Delete"
OPERATION_SPACE_CREATE: Final[str] = "Space Create"
OPERATION_SPACE_UPDATE: Final[str] = "Space Update"
OPERATION_MEMBER_ADD: Final[str] = "Member Add"
OPERATION_MEMBER_REMOVE: Final[str] = "Member Remove"
OPERATION_ATTACHMENT_UPLOAD: Final[str] = "Attachment Upload"

#: ``status`` values.
STATUS_PENDING: Final[str] = "Pending"
STATUS_IN_PROGRESS: Final[str] = "In Progress"
STATUS_DONE: Final[str] = "Done"
STATUS_FAILED: Final[str] = "Failed"
STATUS_DEAD: Final[str] = "Dead"
STATUS_SKIPPED: Final[str] = "Skipped"

#: States the relay will never look at again, and therefore the only ones retention may delete.
#: ``Pending`` / ``In Progress`` / ``Failed`` are live work — ``Failed`` still has retry budget
#: left, and a retention sweep that deleted it would drop a message a coworker believes they sent.
PURGEABLE_STATUSES: Final[tuple[str, ...]] = (STATUS_DONE, STATUS_SKIPPED, STATUS_DEAD)

#: Matches the ``default_log_clearing_doctypes`` hook. Frappe's ``add_default_logtypes`` never
#: updates an existing ``Logs To Clear`` row, so changing this later has no effect on a site that
#: already has one.
DEFAULT_RETENTION_DAYS: Final[int] = 30

#: Deletes are batched because ``run_log_clean_up`` runs inside ``daily_maintenance``, and one
#: unbounded ``DELETE`` over the outbox is exactly the kind of long MariaDB transaction that
#: turns a maintenance job into an outage.
_PURGE_BATCH: Final[int] = 500


class ChatRelayJob(Document):
	@staticmethod
	def clear_old_logs(days: int | None = None) -> None:
		"""Retention entry point Frappe's log clearing calls.

		This method's *existence* is load-bearing, not just its body:
		``remove_unsupported_doctypes()`` runs first in ``run_log_clean_up`` and silently deletes
		the ``Logs To Clear`` row of any doctype whose controller does not implement
		``clear_old_logs(days)``. Retention would then stop with no error anywhere.
		"""
		cutoff = add_days(now_datetime(), -cint(days or DEFAULT_RETENTION_DAYS))
		while True:
			names: list[str] = frappe.get_all(
				"Chat Relay Job",
				filters={"creation": ("<", cutoff), "status": ("in", list(PURGEABLE_STATUSES))},
				pluck="name",
				order_by="creation asc",
				limit=_PURGE_BATCH,
			)
			if not names:
				return
			frappe.db.delete("Chat Relay Job", {"name": ("in", names)})
			frappe.db.commit()
			if len(names) < _PURGE_BATCH:
				return
