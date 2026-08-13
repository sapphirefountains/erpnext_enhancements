# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The relay-job state machine — **pure functions, no I/O, no Frappe**.

This module owns three things and nothing else: which ``Chat Relay Job.status``
transitions are legal, what each of them implies for the ``Chat Message.sync_state`` the
SPA renders, and how long to wait before the next attempt. It imports only the standard
library, which is what puts it in the bench-free CI tier — the only tier in this repo with
automatic regression protection (``CLAUDE.md``: there is no Frappe integration-test job).
The state machine is also the single highest-leverage thing to hold still, because every
other module in :mod:`erpnext_enhancements.chat.sync` writes a status through it.

--------------------------------------------------------------------------------------
The states are the shipped ones, not the proposed ones
--------------------------------------------------------------------------------------

:class:`RelayState` is a transcription of the ``Select`` options already shipped on
``Chat Relay Job.status`` — ``Pending / In Progress / Done / Failed / Dead / Skipped``.
The Phase 2 brief proposes a different vocabulary (``Queued / Sending / Synced /
Retrying / Cancelled``); **this repo did not ship it**, and renaming a Select option needs
a data patch or existing rows refuse to save. ``tests/test_chat_sync_states.py`` asserts
the enum against the DocType JSON so the two cannot drift.

**There is deliberately no ``Retrying`` state.** A transient failure returns the row to
``Pending`` with ``available_at`` pushed forward and ``attempts`` incremented;
``attempts > 0`` is what distinguishes "never tried" from "retrying". One fewer state to
get wrong, and the sweeper's query —
``status = 'Pending' and available_at <= now()`` — is the same either way. ADR 0009 §G.

--------------------------------------------------------------------------------------
Why a table and not a pile of ``if`` statements
--------------------------------------------------------------------------------------

``Chat Relay Job.status`` is the state machine; ``Chat Message.sync_state`` is a
*projection* of it onto the row the SPA renders. Both are written by
:func:`assert_transition` / :func:`project_to_message_state` and by nothing else — **no
bare ``db_set`` on either field anywhere in the codebase.** The value of a declarative
table is that the test can enumerate the *whole cross product* of states and prove that
every pair is either explicitly legal or raises; a scattering of conditionals can only be
tested where somebody remembered to look, and a new state added to the DocType would slot
in silently.

Writes to ``sync_state`` use ``frappe.db.set_value(..., update_modified=False)``. The
room digest watermark is ``(max(seq), count(*), max(modified))``, so a retry that touched
``modified`` would invalidate every cached digest for that room on every attempt. That is
the caller's job, but it is stated here because this module is where a caller looks first.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Final


class RelayState(str, Enum):
	"""``Chat Relay Job.status``, exactly as the shipped DocType declares it.

	``str`` mixin so a value compares equal to the string Frappe stores and can be handed
	straight to ``db.set_value`` without an ``.value`` dance at every call site.
	"""

	PENDING = "Pending"
	IN_PROGRESS = "In Progress"
	DONE = "Done"
	FAILED = "Failed"
	DEAD = "Dead"
	SKIPPED = "Skipped"


class MessageSyncState(str, Enum):
	"""``Chat Message.sync_state`` — the projection, i.e. what a human sees on a message."""

	PENDING = "Pending"
	RELAYED = "Relayed"
	FAILED = "Failed"
	NOT_MIRRORED = "Not Mirrored"
	INBOUND = "Inbound"


#: ``Chat Relay Job.operation`` values that reference a ``Chat Message`` and therefore have
#: something to project onto. Restated here rather than imported from
#: ``chat.doctype.chat_relay_job.chat_relay_job``, which imports ``frappe`` at module scope
#: and would drag this module out of the bench-free tier. The duplication is deliberate and
#: is pinned against the DocType's own ``Select`` options by the table test.
MESSAGE_OPERATIONS: Final[frozenset[str]] = frozenset({"Message Create", "Message Update", "Message Delete"})

#: ``Chat Message.sync_origin`` for a row that came *from* Chat. Such a row is ``Inbound``
#: forever; see :func:`project_to_message_state`.
ORIGIN_GOOGLE_CHAT: Final[str] = "Google Chat"

#: ``Chat Relay Job.auth_identity``. Which of Google's two identities a write is made as.
#:
#: Spelled here, in the module that imports nothing but the standard library, because both
#: sides of the relay need the vocabulary and neither may reach the other: the writer
#: (``sync/outbox.py``) decides it, the worker (``sync/outbound.py``) obeys it, and
#: ``tests/test_chat_guardrails.py`` asserts — transitively — that no write path can import
#: ``gchat/client.py``, where the matching :class:`~gchat.client.AuthIdentity` enum lives.
#: Two spellings of one vocabulary, kept honest by ``tests/test_chat_sync_states.py``.
AUTH_IDENTITY_USER: Final[str] = "USER"
AUTH_IDENTITY_APP: Final[str] = "APP"
AUTH_IDENTITIES: Final[frozenset[str]] = frozenset({AUTH_IDENTITY_USER, AUTH_IDENTITY_APP})

#: ``2 ** attempts`` with an unbounded ``attempts`` is an ``OverflowError`` waiting for a bad
#: caller. Clamped well past the point where ``min(cap, …)`` has already saturated. Same
#: constant, same reasoning, as ``gchat/backoff.py``.
_MAX_EXPONENT: Final[int] = 30


class IllegalTransition(ValueError):
	"""A status write that the table does not permit, or a status nobody declared.

	Both are the same class of bug — a state the machine does not model — and both must
	stop the write rather than degrade into one, so they share an exception type. It
	subclasses :class:`ValueError` so a caller that only wants "bad argument" still catches
	it, but callers in this package should catch the specific class: an
	:class:`IllegalTransition` escaping a relay worker means the job row is now in a state
	the sweeper cannot reason about, which is worth an alarm rather than a retry.
	"""


#: ``state -> frozenset`` of legal successors. Exhaustive over :class:`RelayState`; the
#: table test enumerates the full cross product, so a state added to the DocType without an
#: entry here fails CI rather than falling through to "denied" at 3am.
#:
#: The edges, each with the operational event that produces it:
#:
#: * ``Pending -> Pending`` — **deferral.** Rule 1 (CREATE-BEFORE-EDIT) pushes a later job
#:   for a room forward when an earlier ``job_seq`` is not yet ``Done``, and the per-space
#:   rate limiter does the same when the 1-write/second bucket is not free. Both are a
#:   write of ``available_at`` with the status unchanged, and a self-edge is what lets that
#:   write go through this function instead of around it. Deferral **never** fails a job.
#: * ``Pending -> In Progress`` — the worker claims the row and takes the lease. This is
#:   the *only* claim edge in the table; see ``Failed -> In Progress`` below.
#: * ``Pending -> Skipped`` — the kill switch, a room that stopped mirroring, or a
#:   referenced document that no longer exists. Nothing was attempted and nothing is owed.
#: * ``Pending -> Dead`` — the retry budget was exhausted *retroactively*: an operator
#:   lowered ``relay_max_attempts`` below a row's existing ``attempts``, so the sweeper can
#:   never pick it up again. ``Dead``, not ``Skipped``, because a coworker believes they
#:   sent that message and the daily Dead digest is what tells them otherwise.
#: * ``In Progress -> Done`` — Google accepted the write. Also the Rule 2 case: a
#:   ``UniqueValidationError`` on ``gchat_message_name`` means the row already exists,
#:   which is what the writer wanted, and is success.
#: * ``In Progress -> Failed`` — a **retryable** attempt failed (``backoff.classify_error``
#:   said ``RETRY``), or the lease expired and the sweeper reaped a crashed worker. The
#:   budget decision then happens on the way out of ``Failed``.
#: * ``In Progress -> Dead`` — a **non-retryable** failure: 403 (wrong identity, missing
#:   scope, DWD not granted), 404, 400. ``backoff`` classifies these ``FAIL`` and retrying
#:   converts a fast legible failure into a slow confusing one. Going straight to ``Dead``
#:   avoids parking the row in ``Failed`` where a sweeper would pick it up again.
#: * ``In Progress -> Pending`` — **released, not attempted.** The worker claimed the row,
#:   then found the bucket busy or an earlier ``job_seq`` still open, and put it back. No
#:   Google call happened, so ``attempts`` must not be incremented on this edge.
#: * ``In Progress -> Skipped`` — the worker claimed the row and *then* discovered there is
#:   nothing to send (message deleted before the relay ran, room unmirrored mid-flight).
#: * ``Failed -> Pending`` — budget remains: increment ``attempts``, push ``available_at``
#:   by :func:`next_attempt_delay`, and wait for the sweeper.
#: * ``Failed -> Dead`` — budget spent.
#: * ``Failed -> Skipped`` — an operator cancels a failing job deliberately.
#:
#: Two absences are load-bearing:
#:
#: * **``Failed -> In Progress`` is illegal.** A "retry now" button goes through ``Pending``
#:   with ``available_at = now``. One claim edge means the in-flight set is exactly
#:   ``status = 'In Progress' and lease_expires_at > now()`` with no second way in; two
#:   claim edges make that set — which is *also* the crashed-worker detector — ambiguous.
#: * **``Done`` / ``Dead`` / ``Skipped`` are terminal, with no resurrection edge.** A
#:   ``Dead`` row an operator wants to re-send becomes a **new** relay job with a new
#:   ``job_seq``, never a revived old one: ``job_seq`` is the per-room FIFO position that
#:   Rule 1 depends on, later jobs for that room have already drained past it, and
#:   reinstating a stale position would replay an edit before the create it edits.
LEGAL_TRANSITIONS: Final[Mapping[RelayState, frozenset[RelayState]]] = {
	RelayState.PENDING: frozenset(
		{RelayState.PENDING, RelayState.IN_PROGRESS, RelayState.SKIPPED, RelayState.DEAD}
	),
	RelayState.IN_PROGRESS: frozenset(
		{
			RelayState.DONE,
			RelayState.FAILED,
			RelayState.PENDING,
			RelayState.SKIPPED,
			RelayState.DEAD,
		}
	),
	RelayState.FAILED: frozenset({RelayState.PENDING, RelayState.DEAD, RelayState.SKIPPED}),
	RelayState.DONE: frozenset(),
	RelayState.DEAD: frozenset(),
	RelayState.SKIPPED: frozenset(),
}

#: States the relay will never look at again. Also exactly ``Chat Relay Job``'s
#: ``PURGEABLE_STATUSES``, and that agreement is not a coincidence: retention may only
#: delete rows no transition can leave.
TERMINAL_STATES: Final[frozenset[RelayState]] = frozenset(
	{RelayState.DONE, RelayState.DEAD, RelayState.SKIPPED}
)

#: States that still represent live work the sweeper must account for. ``Failed`` is
#: **not** here: a ``Failed`` row is a decision point, not a queue entry — the worker that
#: recorded the failure immediately routes it to ``Pending`` or ``Dead``, and a ``Failed``
#: row still sitting there after a crash is picked up by the lease reaper, not by the
#: ordinary "what can I run next" query.
RETRYABLE_STATES: Final[frozenset[RelayState]] = frozenset({RelayState.PENDING, RelayState.IN_PROGRESS})

#: ``RelayState -> MessageSyncState | None``. Total over :class:`RelayState`, asserted by
#: the table test, because a missing key would silently mean "leave the message alone" and
#: the symptom would be a message stuck on ``Pending`` in the SPA forever.
#:
#: The two non-obvious entries:
#:
#: * ``FAILED -> None``. A transient failure is momentary — the row is on its way back to
#:   ``Pending`` with a pushed ``available_at`` — and flashing the coworker's message red
#:   on every 503 would train people to ignore the colour. ``Dead`` is the state a human
#:   is meant to act on, and ``Dead`` is where ``Failed`` is projected.
#: * ``IN_PROGRESS -> PENDING``. "Sending" is not a message-level state; the SPA renders
#:   the same spinner either way, and adding one would need a Select option and a patch.
_MESSAGE_PROJECTION: Final[Mapping[RelayState, MessageSyncState | None]] = {
	RelayState.PENDING: MessageSyncState.PENDING,
	RelayState.IN_PROGRESS: MessageSyncState.PENDING,
	RelayState.DONE: MessageSyncState.RELAYED,
	RelayState.FAILED: None,
	RelayState.DEAD: MessageSyncState.FAILED,
	RelayState.SKIPPED: MessageSyncState.NOT_MIRRORED,
}


def _as_relay_state(value: RelayState | str, *, role: str) -> RelayState:
	"""Coerce a stored string to a :class:`RelayState`, or raise :class:`IllegalTransition`.

	``role`` is ``"current"`` or ``"target"`` and exists only so the error names which end
	of the transition was wrong — an operator reading an Error Log entry that says
	``unknown relay state`` and nothing else has to go and read the code.
	"""
	if isinstance(value, RelayState):
		return value
	try:
		return RelayState(str(value))
	except ValueError:
		legal = ", ".join(sorted(state.value for state in RelayState))
		raise IllegalTransition(
			f"{role} relay status {value!r} is not a Chat Relay Job status; expected one of: {legal}"
		) from None


def assert_transition(current: RelayState | str, target: RelayState | str) -> RelayState:
	"""Return ``target``, or raise :class:`IllegalTransition`. The only gate on a status write.

	Returning the coerced target rather than ``None`` is what makes the call site read
	``job.status = assert_transition(job.status, RelayState.DONE)`` — the gate cannot be
	forgotten, because skipping it means writing nothing. A function that only validated
	would be trivially bypassable by the next person in a hurry.

	Note that a no-op write is **not** universally legal: only ``Pending -> Pending`` is,
	because only ``Pending`` has a meaning for re-writing itself (deferral). ``In Progress
	-> In Progress`` is refused on purpose — a lease *renewal* writes ``lease_expires_at``
	and leaves ``status`` alone, and a second worker "renewing" a claim it does not hold is
	precisely the bug the in-flight set exists to make visible.
	"""
	source = _as_relay_state(current, role="current")
	destination = _as_relay_state(target, role="target")

	if destination in LEGAL_TRANSITIONS[source]:
		return destination

	permitted = sorted(state.value for state in LEGAL_TRANSITIONS[source])
	allowed = ", ".join(permitted) if permitted else "nothing (terminal state)"
	raise IllegalTransition(
		f"Chat Relay Job cannot move from {source.value!r} to {destination.value!r}; "
		f"legal successors are: {allowed}"
	)


def project_to_message_state(
	relay: RelayState | str, *, operation: str, origin: str
) -> MessageSyncState | None:
	"""The ``Chat Message.sync_state`` a relay-job state implies, or ``None`` to leave it alone.

	``None`` is a real answer and there are three ways to get it, all of which a caller
	must treat as "write nothing" rather than "write empty":

	1. **The job has no message.** ``Space Create``, ``Member Add``, ``Member Remove`` and
	   friends reference a room or a membership, and ``Attachment Upload`` references a
	   ``Chat Attachment``. Projecting a membership failure onto a message would mark an
	   unrelated message failed.
	2. **The row came from Chat.** ``sync_origin = "Google Chat"`` means the message is
	   ``Inbound`` forever, whatever any relay job does to it afterwards — an outbound edit
	   of an inbound message does not make the message ours. Any other origin
	   (``ERPNext``, ``Triton``, or an unexpected value) projects, because those rows are
	   ones we are relaying outward and the safe direction for an unknown origin is to keep
	   reporting relay progress rather than to go quiet.
	3. **The relay state is ``Failed``.** Transient and momentary; see
	   ``_MESSAGE_PROJECTION``.

	``operation`` and ``origin`` are keyword-only because at a call site
	``project_to_message_state(state, op, origin)`` reads identically whichever way round
	the last two are, and getting them backwards would silently return ``None`` forever.
	"""
	state = _as_relay_state(relay, role="current")

	if (origin or "") == ORIGIN_GOOGLE_CHAT:
		return None
	if (operation or "") not in MESSAGE_OPERATIONS:
		return None

	return _MESSAGE_PROJECTION[state]


def next_attempt_delay(attempts: int, base: float, cap: float) -> float:
	"""Deterministic, **jitter-free** seconds to add to ``available_at`` before the next try.

	**This is not ``gchat.backoff.compute_backoff`` and must never be replaced by it.** The
	two look like the same arithmetic and answer different questions:

	* ``compute_backoff`` is the **jittered sleep inside a single HTTP call** — the relay
	  worker is blocked in ``time.sleep`` between attempt 1 and attempt 2 of *one*
	  ``messages.create``. Jitter is the whole point there: several workers retrying the
	  same space after a 503 must not re-collide, so it draws ``uniform(0, ceiling)``.
	* This function is the **scheduling delay between job attempts** — the worker gives up
	  on the call, writes ``available_at = now + next_attempt_delay(...)``, releases the
	  row to ``Pending`` and goes away. Nothing is blocked. The sweeper picks it up later.

	Conflating them makes the sweeper untestable, because a test can no longer say "after a
	failure at T the row becomes available at T+2s"; it can only say "somewhere in a
	window", and the assertion that catches an off-by-one in the retry budget disappears
	with it. De-synchronisation is also not this function's problem: a room is a strict
	FIFO drained at one write per second, so there is no herd to spread out.

	``attempts`` is ``Chat Relay Job.attempts`` **after** the failing attempt has been
	counted — 1 on the first failure — so the first retry waits exactly ``base`` and the
	sequence is ``base, 2·base, 4·base …`` capped at ``cap``. ``0`` and negatives are
	clamped to the ``base`` case rather than rejected: this is called on the error path,
	and raising inside the handler that records an error is how a job dies with an empty
	Error Log.

	``base`` and ``cap`` come from ``Chat Settings`` (``relay_initial_backoff_seconds``,
	``backoff_cap_seconds``); nothing here reads settings, because reading settings is I/O.
	"""
	safe_base = max(float(base), 0.0)
	safe_cap = max(float(cap), 0.0)
	steps = min(max(int(attempts) - 1, 0), _MAX_EXPONENT)
	return min(safe_cap, safe_base * float(2**steps))
