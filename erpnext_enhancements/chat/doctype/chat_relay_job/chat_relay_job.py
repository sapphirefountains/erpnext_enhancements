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

--------------------------------------------------------------------------------------
What Phase 2 adds here, and what it deliberately does not
--------------------------------------------------------------------------------------

The worker lives in :mod:`erpnext_enhancements.chat.sync.outbound` and writes this row through
``frappe.db.set_value``, which does **not** run ``validate()``. So :meth:`ChatRelayJob.validate`
is not the enforcement point for the state machine — it is the *backstop* for the other way in,
an ORM ``save()`` from the desk, a patch or a console session. Both paths funnel through the one
transition table in :mod:`erpnext_enhancements.chat.sync.states`; there is no second copy of the
rules here, only a second call site.

This module holds the vocabulary (:data:`OPERATION_MESSAGE_CREATE` and friends,
:data:`TERMINAL_STATUSES`, :data:`BLOCKING_STATUSES`) because the DocType's ``Select`` options are
the source of truth for it, and the worker imports it from here rather than re-typing strings.
``states.py`` keeps a *pure* twin of the operation set so it can stay importable in the bench-free
CI tier; ``tests/test_chat_sync_states.py`` pins the two against this JSON so they cannot drift.
"""

import json
import re
from typing import Any, Final

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, now_datetime

from erpnext_enhancements.chat.sync.states import IllegalTransition, assert_transition

#: ``operation`` values. The enum selects the handler; the payload carries arguments only.
OPERATION_MESSAGE_CREATE: Final[str] = "Message Create"
OPERATION_MESSAGE_UPDATE: Final[str] = "Message Update"
OPERATION_MESSAGE_DELETE: Final[str] = "Message Delete"
OPERATION_SPACE_CREATE: Final[str] = "Space Create"
OPERATION_SPACE_UPDATE: Final[str] = "Space Update"
OPERATION_MEMBER_ADD: Final[str] = "Member Add"
OPERATION_MEMBER_REMOVE: Final[str] = "Member Remove"
OPERATION_ATTACHMENT_UPLOAD: Final[str] = "Attachment Upload"

#: The operations that reference a ``Chat Message`` and therefore project onto its
#: ``sync_state``. The pure twin is ``states.MESSAGE_OPERATIONS``; the table test asserts the
#: two agree with the DocType JSON, which is why neither is allowed to grow a member alone.
MESSAGE_OPERATIONS: Final[tuple[str, ...]] = (
	OPERATION_MESSAGE_CREATE,
	OPERATION_MESSAGE_UPDATE,
	OPERATION_MESSAGE_DELETE,
)

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

#: Same set, under the name the state machine uses. ``states.TERMINAL_STATES`` is the pure twin
#: and the agreement is not a coincidence: retention may only delete rows no transition can
#: leave, so "terminal" and "purgeable" are the same question asked twice.
TERMINAL_STATUSES: Final[tuple[str, ...]] = PURGEABLE_STATUSES

#: Rows the worker still has to account for. Exactly the complement of
#: :data:`TERMINAL_STATUSES`, and the ``status in (...)`` list every claim query uses.
OPEN_STATUSES: Final[tuple[str, ...]] = (STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_FAILED)

#: **Rule 1, CREATE-BEFORE-EDIT, as a data structure.** A job may not run while an earlier
#: ``job_seq`` in the same room is in one of these states; the later job is *deferred*
#: (``available_at`` pushed forward), never failed.
#:
#: Note what is **not** here, because a literal reading of the rule would put it here.
#: ``Dead`` and ``Skipped`` do not block. The rule as written in the ADR is "an earlier job that
#: is not ``Done``", and taken literally one dead-lettered create would wedge its room's mirror
#: forever, silently, with every later message piling up ``Pending`` behind it — a strictly worse
#: outcome than the one Rule 1 exists to prevent, and one nobody would notice until a coworker
#: asked why Chat had gone quiet. An edit whose create died still converges, because
#: ``messages.patch`` is sent with ``allowMissing=true`` against the ``client-`` alias and
#: upserts; that upsert is documented as "the safety net under Rule 1" precisely for this case.
#: A *live* predecessor still blocks, which is the ordering guarantee anyone actually depends on.
BLOCKING_STATUSES: Final[tuple[str, ...]] = OPEN_STATUSES

#: Matches the ``default_log_clearing_doctypes`` hook. Frappe's ``add_default_logtypes`` never
#: updates an existing ``Logs To Clear`` row, so changing this later has no effect on a site that
#: already has one.
DEFAULT_RETENTION_DAYS: Final[int] = 30

#: Deletes are batched because ``run_log_clean_up`` runs inside ``daily_maintenance``, and one
#: unbounded ``DELETE`` over the outbox is exactly the kind of long MariaDB transaction that
#: turns a maintenance job into an outage.
_PURGE_BATCH: Final[int] = 500

#: ``last_error`` is a ``Small Text`` and is read from the desk by anyone with the DocType. The
#: field description promises truncation at write; this is that promise.
MAX_LAST_ERROR_CHARS: Final[int] = 1000

#: Anything shaped like a bearer token, stripped before it can be stored.
#:
#: This duplicates ``chat.gchat.client.scrub_secrets``, and the duplication is deliberate rather
#: than sloppy: ``tests/test_chat_guardrails.py`` forbids **any** DocType controller under
#: ``chat/doctype/`` from importing the ``gchat`` package, because a controller that can reach
#: the transport is a ``doc_events`` handler one refactor away from spending the space's single
#: write per second inside a user's save. Three lines of regex is the cheaper side of that trade.
#: The relay worker scrubs before it writes; this is the backstop for every other writer.
_BEARER_RE: Final[re.Pattern[str]] = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")


def scrub_and_truncate(value: Any) -> str:
	"""Make an error string safe to store: no bearer token, no more than 1,000 characters.

	Never raises. It is called from ``validate()`` on the failure path, and a writer that can
	fail while recording a failure turns a legible error into an empty Error Log — which this
	repo has shipped before.
	"""
	try:
		text = _BEARER_RE.sub("Bearer [redacted]", str(value or ""))
	except Exception:
		return ""
	if len(text) <= MAX_LAST_ERROR_CHARS:
		return text
	return text[: MAX_LAST_ERROR_CHARS - 3] + "..."


class ChatRelayJob(Document):
	"""One outbound operation, its retry budget, its FIFO position and its lease."""

	def validate(self) -> None:
		"""Backstop the state machine and keep ``last_error`` storable.

		**This is not where the relay's status writes are policed.** The worker writes through
		``frappe.db.set_value``, which never reaches ``validate()``; it is gated by
		:func:`~erpnext_enhancements.chat.sync.states.assert_transition` at its own call site,
		and that function is the single gate. What arrives here instead is the *other* way a
		status changes — a desk edit, a patch, a console session during an incident — and those
		deserve the same table rather than a second, laxer set of rules.

		The check is skipped for a new row: an insert has no predecessor state, and the initial
		status is whatever the outbox chose (``Pending`` in every real case, ``Skipped`` for a
		room that is not mirrored).
		"""
		self.last_error = scrub_and_truncate(getattr(self, "last_error", None))
		self._validate_payload_is_json()

		if self.is_new():
			return

		previous = self.get_doc_before_save()
		if previous is None:
			# Frappe could not load the pre-save copy (a bulk update, a stripped cache). Refusing
			# the save here would block an operator mid-incident on a check that has no evidence
			# to work with, so the transition simply goes unverified — the worker's own gate is
			# the one that matters.
			return

		before = getattr(previous, "status", None) or ""
		after = getattr(self, "status", None) or ""
		if before == after:
			return

		try:
			assert_transition(before, after)
		except IllegalTransition as exc:
			frappe.throw(
				_(
					"{0}. Chat Relay Job statuses move through one transition table "
					"(erpnext_enhancements.chat.sync.states); a Dead row an operator wants to "
					"re-send becomes a NEW job with a new job_seq, never a revived old one — "
					"job_seq is the per-room FIFO position, and later jobs have already drained "
					"past it."
				).format(str(exc)),
				title=_("Illegal Relay Job Transition"),
			)

	def _validate_payload_is_json(self) -> None:
		"""``payload`` is a ``Code`` field with ``options: JSON``; make that true rather than hoped.

		The worker reads arguments out of it. A row whose payload does not parse would fail at
		the moment it is drained — inside a background job, minutes later, with the person who
		wrote it long gone — instead of at the save that introduced it.
		"""
		raw = getattr(self, "payload", None)
		if raw in (None, ""):
			return
		if not isinstance(raw, str):
			return
		try:
			json.loads(raw)
		except ValueError as exc:
			frappe.throw(
				_("Payload is not valid JSON: {0}").format(str(exc)),
				title=_("Chat Relay Job"),
			)

	def payload_dict(self) -> dict[str, Any]:
		"""``payload`` parsed, or ``{}``. Never raises, never returns a non-mapping.

		The enum in ``operation`` selects the handler; the payload carries **arguments only**
		and never a dotted method path, so the arbitrary-code sink the older Drive outbox has to
		guard against does not exist here. Returning ``{}`` for junk keeps that property true
		even if a row is hand-edited: a handler reads the keys it knows and ignores the rest.
		"""
		raw = getattr(self, "payload", None)
		if not raw:
			return {}
		try:
			parsed = json.loads(raw) if isinstance(raw, str) else raw
		except ValueError:
			return {}
		return dict(parsed) if isinstance(parsed, dict) else {}

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
