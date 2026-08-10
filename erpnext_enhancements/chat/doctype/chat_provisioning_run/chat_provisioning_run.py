# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chat Provisioning Run — the checkpoint row that makes a bulk org sweep resumable.

§4.H mode 2 walks the org structure and creates a Google space per department, team or
project. Without a durable checkpoint an interrupted sweep can only **restart**, and
restarting is not merely slow:

* Project-wide space writes are capped at **60 per minute** (``PHASE2_VERIFIED.md §2``).
  Re-walking units that already have spaces spends that budget re-confirming them, and
  while it does, every other space operation on the site — a new per-document room, a DM
  space — is queued behind it.
* The sweep is exactly the kind of long job a deploy lands in the middle of, and the prod
  deploy ``FLUSHDB``s the queue Redis, so a job that only existed as a queue entry is gone
  with nothing in any log (ADR §G.8 Rule 4). A **row** survives; an enqueue does not.

So the run is a document, the position is :attr:`cursor`, and resumption is reading one row.

TWO PROPERTIES THAT LOOK LIKE DETAILS AND ARE NOT
--------------------------------------------------
**``dry_run`` defaults to 1.** The safe default is the *on* one here because the action is
creating spaces: they appear in every coworker's native Chat client, they consume the
60/minute bucket, and the only way to undo one is ``spaces.delete`` — a call this codebase
never makes without an explicit human confirmation step, because it cascades and takes the
conversation history with it (ADR §F/§G, and the ``chat.delete`` scopes are deliberately not
granted). A destructive default on a sweep is the wrong default, so the run ships planning
and a human unticks it after reading :attr:`log`.

**The cursor is written after the unit commits, never before.** A cursor written ahead of
the work skips a unit on resume, silently — no error, no failed count, just a department
that never got a space and a run that reports Completed. That is the one failure mode this
field cannot afford, and it is the reverse of the usual "claim before you work" instinct: a
unit provisioned twice is caught by ``unique(gchat_space_name)`` and is recoverable, a unit
never provisioned is invisible.

WHAT THIS CONTROLLER DOES NOT DO
---------------------------------
No Google call, no scheduling, no state transitions. The sweep in ``sync/provisioning.py``
owns all of that; this module owns the row's vocabulary so the sweep and any later reporting
agree on the strings rather than each spelling ``"Paused"`` at their own call sites. It must
not import ``chat.gchat`` either: ``tests/test_chat_guardrails.py`` asserts that no DocType
controller reaches the transport, because a controller method *is* a document event and a
document event that calls Google spends the space's single write per second inside somebody's
save.

WHAT IT DOES DO: refuse the one edit that would make the row lie
-----------------------------------------------------------------
``dry_run`` cannot be changed once the run has started. Flipping it mid-run is the edit that
looks harmless and destroys the row's meaning: the units before the flip were *planned* and
counted in ``skipped_count``, the units after it were *created*, the cursor has already
passed the planned ones so they will never be revisited, and the run reports ``Completed``
over an org half of which has no space. Two activities need two runs; that is free, and
un-picking one row that did both is not.
"""

from typing import Final

import frappe
from frappe.model.document import Document

#: ``mode`` values — one run is one slice of the org. A single row meaning "departments and
#: then teams" could not be resumed without re-deriving where the boundary fell, which is the
#: exact question the cursor exists to answer.
MODE_DEPARTMENT: Final[str] = "Department"
MODE_TEAM: Final[str] = "Team"
MODE_PROJECT: Final[str] = "Project"

#: ``status`` values. ``Paused`` is first-class and is not a synonym for ``Failed``: an
#: operator stopping a sweep, and the 60/minute space-write bucket deferring one, both leave a
#: run that should RESUME from the cursor. ``Failed`` means the run itself broke — a single
#: unit erroring is counted in ``failed_count`` and does not fail the run.
STATUS_PENDING: Final[str] = "Pending"
STATUS_RUNNING: Final[str] = "Running"
STATUS_PAUSED: Final[str] = "Paused"
STATUS_COMPLETED: Final[str] = "Completed"
STATUS_FAILED: Final[str] = "Failed"

#: A run in one of these is finished and must never be resumed. ``Pending``/``Running``/
#: ``Paused`` are the resumable set — ``Running`` included, because a worker killed mid-unit
#: leaves the row saying ``Running`` forever and the resume path is what recovers it.
TERMINAL_STATUSES: Final[frozenset[str]] = frozenset({STATUS_COMPLETED, STATUS_FAILED})

#: The statuses :func:`~erpnext_enhancements.chat.sync.provisioning.resume_provisioning_runs`
#: picks up. ``Running`` is in the set on purpose — see the note on
#: :data:`TERMINAL_STATUSES` — because a worker killed by a deploy leaves the row saying
#: ``Running`` forever and the resume path is the only thing that recovers it.
RESUMABLE_STATUSES: Final[frozenset[str]] = frozenset({STATUS_PENDING, STATUS_RUNNING, STATUS_PAUSED})

#: ``last_error`` is a ``Small Text``; the field description promises truncation at write and
#: this is where the promise is kept. A Google error body can run to kilobytes, and a column
#: silently truncated by MariaDB loses the *end* of the message, which is where the useful
#: part usually is.
MAX_ERROR_CHARS: Final[int] = 1000


class ChatProvisioningRun(Document):
	def validate(self) -> None:
		self._freeze_dry_run()
		self._clamp_counters()
		self._trim_error()
		self._align_finished_at()

	def _freeze_dry_run(self) -> None:
		"""``dry_run`` is immutable once the run has moved off ``Pending``.

		See the module docstring: flipping it mid-run makes the counters describe two
		different activities and leaves the already-passed units permanently unprovisioned
		behind a cursor that will never go back for them. A guard rather than
		``set_only_once`` on the field, because the value legitimately *is* editable while
		the run is still Pending — that is the whole workflow: plan, read the log, untick,
		start again.
		"""
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if before is None:
			return
		if int(before.get("dry_run") or 0) == int(self.get("dry_run") or 0):
			return
		if str(before.get("status") or "") != STATUS_PENDING:
			frappe.throw(
				frappe._(
					"Dry Run cannot be changed once a provisioning run has started. The units "
					"already walked were planned, not created, and the cursor will not go back "
					"for them — start a new run instead."
				)
			)

	def _clamp_counters(self) -> None:
		"""No counter may go negative. A negative count reads as a data-entry slip in a
		report and as an underflow in arithmetic that compares ``created + skipped + failed``
		against ``planned_count``, which is the only completeness check this row supports."""
		for field in ("planned_count", "created_count", "skipped_count", "failed_count"):
			value = int(self.get(field) or 0)
			if value < 0:
				self.set(field, 0)

	def _trim_error(self) -> None:
		error = str(self.get("last_error") or "")
		if len(error) > MAX_ERROR_CHARS:
			self.last_error = error[:MAX_ERROR_CHARS]

	def _align_finished_at(self) -> None:
		"""``finished_at`` is set exactly on the terminal statuses and cleared otherwise.

		Empty on a ``Paused`` run is not an oversight — it is how the resume path recognises
		one, and a stale ``finished_at`` left behind by a run that was resumed would make a
		live run look finished in every report that sorts on it.
		"""
		from frappe.utils import now_datetime

		if str(self.get("status") or "") in TERMINAL_STATUSES:
			if not self.get("finished_at"):
				self.finished_at = now_datetime()
		elif self.get("finished_at"):
			self.finished_at = None
