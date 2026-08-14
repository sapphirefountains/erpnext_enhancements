# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The relay worker: it drains ``Chat Relay Job`` rows into Google Chat, and survives.

Everything here exists because of one operational fact, and it is worth stating before any
code: **the production deploy runs ``redis-cli -p 11000 FLUSHDB`` and restarts the whole
honcho group, and Frappe v16 wires no RQ retries at all.** So a queued job is not a
promise, a running job can be SIGKILLed mid-HTTP-call, and nothing in the queue layer will
ever try again. The only delivery guarantee in this design is a row in ``Chat Relay Job``
and :func:`sweep_relay_jobs`, which is why the enqueue path is a latency optimisation and
the sweeper is the correctness mechanism. Read that sentence again before deleting anything
below that looks redundant with the queue.

--------------------------------------------------------------------------------------
A room is a strict FIFO, and that is not a limitation
--------------------------------------------------------------------------------------

Google allows **one write per second per space**, shared with every other Chat app acting
in that space. There is therefore no parallelism to lose inside a room, which makes the
strongest ordering guarantee also the cheapest one: the worker takes the lowest open
``job_seq`` for a room and nothing else. Rule 1 (CREATE-BEFORE-EDIT) falls out of that
rather than being bolted on — a later job whose predecessor is still live is *deferred*
(``available_at`` pushed forward), **never failed**, because failing it would convert a
five-second ordering wait into a dead letter.

:func:`drain_room` is what a woken worker actually runs: it takes the head of the room's
FIFO, executes it, and comes back for the next one until the room is empty or a bound is
hit. That shape matters for the ten-messages-in-two-seconds case. Ten enqueues wake ten
workers; the first claims the head and drains all ten in order at one write per second, and
the other nine find the head claimed and exit immediately. The alternative — one worker per
job, each deferring on Rule 1 — is correct but leaves nine messages waiting for the next
five-minute sweep, which is not chat.

--------------------------------------------------------------------------------------
``lease_expires_at`` is one field doing two jobs, and cleanup is never a ``finally``
--------------------------------------------------------------------------------------

On claim the row goes ``In Progress`` with a lease. That single field is simultaneously the
§4.D **in-flight claim** the inbound pipeline reads before deciding whether it is looking at
the echo of our own POST, and the **crashed-worker detector** the sweeper reads. A row still
``In Progress`` past its lease is a worker that died.

**A ``finally`` block does not run when the process is killed**, and being killed is exactly
the case a lease exists for — a deploy restart lands in the middle of an HTTP call several
times a week. So a claim released only in Python is a claim held forever. Every release path
here writes the row, and the lease expiring is the backstop that does not depend on our
process still existing.

The lease is sized to the *worst case wall clock of one job*, not to the HTTP timeout. The
client retries synchronously inside a single call (up to ``relay_max_attempts``, sleeping a
jittered backoff between attempts) and the space bucket can block for a few seconds on top;
a lease equal to one timeout would have the sweeper reaping live workers under load, which
is a duplicate-message generator rather than a safety net. See :func:`lease_seconds`.

--------------------------------------------------------------------------------------
What is deliberately *not* an error
--------------------------------------------------------------------------------------

* **A deferral.** Rule 1, an unprovisioned space, an author who has not joined the space
  yet, a full token bucket, an open circuit breaker. None of these consume an attempt and
  none of them write ``Failed``; they push ``available_at`` and say why in ``last_error``.
  Burning retry budget on "the space does not exist yet" is how a message dead-letters
  because provisioning was slow.
* **Rule 2, first-writer-wins on the resource name.** A unique-index collision on
  ``gchat_message_name`` means the row already exists, which is what the writer wanted. It
  is success, never a logged error and never a retry.
* **A kill switch.** ``pause_outbound`` stops writes and drops nothing; on re-enable the
  backlog drains in ``job_seq`` order, because that is the only order the claim query knows.

Every exception leaving the Google surface is re-raised ``from None`` and every string
written to ``last_error`` is scrubbed. A bare re-raise out of a background job publishes the
failing frames' locals into the Error Log, and those locals hold an ``Authorization:
Bearer`` header — this app has leaked private key material exactly that way once.

--------------------------------------------------------------------------------------
Seams
--------------------------------------------------------------------------------------

:func:`build_client`, :func:`space_limiter` and :func:`project_quota` are module-level
factories rather than inline constructions so the bench-free suite can replace them. That is
the whole injection story; there is no dependency-injection container and no ``deps``
argument threaded through twelve frames.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Final

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from erpnext_enhancements.chat import seams
from erpnext_enhancements.chat.doctype.chat_relay_job.chat_relay_job import (
	BLOCKING_STATUSES,
	MESSAGE_OPERATIONS,
	OPEN_STATUSES,
	OPERATION_ATTACHMENT_UPLOAD,
	OPERATION_MESSAGE_CREATE,
	OPERATION_MESSAGE_DELETE,
	OPERATION_MESSAGE_UPDATE,
	scrub_and_truncate,
)
from erpnext_enhancements.chat.gchat import ids
from erpnext_enhancements.chat.gchat.backoff import RetryDecision, classify_error
from erpnext_enhancements.chat.gchat.client import (
	AuthIdentity,
	GoogleChatAPIError,
	GoogleChatClient,
	GoogleChatError,
	GoogleChatTransportError,
	build_create_message_call,
	message_alias,
	scrub_secrets,
)
from erpnext_enhancements.chat.retrieval import citations
from erpnext_enhancements.chat.sync import attachments, budget, ratelimit
from erpnext_enhancements.chat.sync.states import (
	AUTH_IDENTITIES,
	AUTH_IDENTITY_APP,
	AUTH_IDENTITY_USER,
	IllegalTransition,
	RelayState,
	assert_transition,
	next_attempt_delay,
	project_to_message_state,
)

RELAY_JOB: Final[str] = "Chat Relay Job"
CHAT_MESSAGE: Final[str] = "Chat Message"
CHAT_ROOM: Final[str] = "Chat Room"
CHAT_ROOM_MEMBER: Final[str] = "Chat Room Member"

#: ``Chat Room.provisioning_state`` when provisioning gave up. Restated here rather than
#: imported from ``chat.sync.provisioning``: ``tests/test_chat_guardrails.py`` asserts the
#: write path cannot reach that module transitively, and this is the write path.
PROVISIONING_FAILED: Final[str] = "Failed"

#: How far forward a Rule 1 / dependency / bucket deferral pushes ``available_at``.
#: Short, because these are all "come back in a moment" conditions and the row is not
#: consuming anything while it waits. Long enough that a wedged room does not spin.
DEFER_SECONDS: Final[int] = 5

#: A deferral caused by the **dependency gate** waits longer: provisioning a space is a
#: separate job with its own quota (60 space writes per minute, project-wide) and retrying
#: every five seconds only adds noise to the log.
DEPENDENCY_DEFER_SECONDS: Final[int] = 30

#: How long a job may sit blocked on its dependency before the sweeper says so out loud.
#: Not a failure — provisioning legitimately takes a while — but a room whose space has not
#: appeared in half an hour is a room whose coworkers think they are talking to somebody.
DEPENDENCY_STALE_SECONDS: Final[int] = 1800

#: Added to the computed worst case when sizing a lease. Covers scheduler jitter, a slow
#: commit and the handful of database round trips either side of the HTTP call.
LEASE_MARGIN_SECONDS: Final[int] = 60

#: Hard ceiling on a lease. A lease is a promise that *nobody else* will touch the row, so
#: an arithmetic accident in the settings (a 3,600-second HTTP timeout, say) must not be able
#: to strand a message for a day.
LEASE_MAX_SECONDS: Final[int] = 1800

#: Bounds on one :func:`drain_room` run. The wall-clock bound is the important one: RQ's
#: short queue kills a job at 300 seconds and the deploy kills it whenever it likes, so the
#: worker stops early, commits, and re-enqueues rather than being cut off mid-write.
MAX_JOBS_PER_RUN: Final[int] = 200
MAX_RUN_SECONDS: Final[float] = 240.0

#: The queue the relay runs on. ``short`` is where latency-sensitive work belongs, and the
#: run bound above is set under its 300-second timeout on purpose.
RELAY_QUEUE: Final[str] = "short"
RELAY_JOB_TIMEOUT: Final[int] = 300

#: Google publishes no per-minute ceiling for attachment writes on the same page as the
#: others; 600/minute is the figure in ``PHASE2_VERIFIED.md`` §2 and there is no
#: ``Chat Settings`` field for it, so it lives here where an operator can find it.
DEFAULT_ATTACHMENT_WRITES_PER_MINUTE: Final[int] = 600

#: Circuit-breaker cache keys. ``frappe.cache()`` prefixes the site's ``db_name``, so two
#: sites relaying into the same Workspace do not share a breaker — which is what we want:
#: a staging site tripping its own breaker must not stop production.
_CIRCUIT_FAILURES_KEY: Final[str] = "chat:circuit:outbound:failures"
_CIRCUIT_OPEN_UNTIL_KEY: Final[str] = "chat:circuit:outbound:open_until_ms"

#: How long the breaker's own keys live. Longer than any plausible cooldown, short enough
#: that a breaker opened by a since-fixed outage cannot outlive the incident by a day.
_CIRCUIT_KEY_TTL_SECONDS: Final[int] = 3600

_LOGGER_NAME: Final[str] = "chat.sync.outbound"

#: Error Log titles. Fixed strings so an operator can grep for exactly one of them, and
#: short enough to survive the 140-character ``Error Log.error`` title column.
ALERT_DEAD_LETTER: Final[str] = "Chat relay dead letter"
ALERT_CRASHED_WORKER: Final[str] = "Chat relay lease expired (crashed worker)"
ALERT_DUPLICATE_MESSAGE: Final[str] = "Chat relay duplicate message at Google"
ALERT_CIRCUIT_OPEN: Final[str] = "Chat relay circuit breaker opened"
ALERT_DEPENDENCY_STALE: Final[str] = "Chat relay blocked on provisioning"


# --------------------------------------------------------------------------------------
# Control-flow signals
# --------------------------------------------------------------------------------------


class Deferred(Exception):
	"""Not an error: "come back later, and do not spend an attempt on this".

	Raised by the dependency gate, the rate limiter and the circuit breaker. The handler that
	raises it has performed **no** Google write, so the row goes back to ``Pending`` with
	``available_at`` pushed and ``attempts`` untouched. Making this an exception rather than a
	return code is what stops a handler half-way down its body from having to thread a
	sentinel back out through three call frames.
	"""

	def __init__(self, reason: str, *, seconds: int = DEFER_SECONDS) -> None:
		super().__init__(reason)
		self.reason = reason
		self.seconds = max(int(seconds), 1)


class Skip(Exception):
	"""Nothing to send, and nothing will ever be. The row goes ``Skipped``.

	Distinct from :class:`Deferred` because "the message was hard-deleted before the relay
	ran" is a finished story, and from a dead letter because nobody is owed a message that no
	longer exists — a ``Dead`` row raises an alarm and this must not.
	"""


# --------------------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------------------


def _settings() -> Any:
	"""``Chat Settings``, cached. Read on every job so an incident-time flip takes effect now."""
	return frappe.get_cached_doc("Chat Settings")


def _setting_int(settings: Any, field: str, default: int) -> int:
	"""Read an integer setting defensively; a missing or junk value degrades to ``default``.

	``getattr(obj, field, None) or default`` per this app's house rule: these reads happen
	during ``bench migrate`` and during ERPNext's own test bootstrap, when the field may not
	exist yet, and a missing tuning value must never crash a relay worker.
	"""
	try:
		raw = getattr(settings, field, None)
	except Exception:
		return int(default)
	if raw in (None, "", 0):
		return int(default)
	try:
		return int(raw)
	except (TypeError, ValueError):
		return int(default)


def _setting_limit(settings: Any, field: str, default: int) -> int:
	"""Like :func:`_setting_int`, except that a stored **zero means zero**.

	The distinction is not pedantry and it is the reason there are two readers. For a tuning
	value — a timeout, a backoff base — Frappe's ``Int`` default of ``0`` is indistinguishable
	from "nobody filled this in", so treating it as unset is right. For a **quota ceiling** it
	is exactly wrong: ``quota_decision`` documents that ``limit <= 0`` refuses everything
	because a zero quota is a configured stop, and reading it as "unlimited" is the failure
	direction that gets an API client banned. Same for ``circuit_breaker_threshold``, where
	zero is the documented way to disable the breaker.

	So the fallback here fires only on genuinely absent values — ``None`` or an empty string,
	which is what ``getattr`` gives back on a site whose schema patch has not run.
	"""
	try:
		raw = getattr(settings, field, None)
	except Exception:
		return int(default)
	if raw is None or raw == "":
		return int(default)
	try:
		return int(raw)
	except (TypeError, ValueError):
		return int(default)


def _setting_flag(settings: Any, field: str, default: int = 0) -> bool:
	try:
		return bool(cint(getattr(settings, field, None) or default))
	except Exception:
		return bool(default)


def outbound_pause_reason(settings: Any | None = None) -> str:
	"""``""`` when outbound writes are permitted, otherwise the human reason they are not.

	Two switches, one answer, because a caller that had to remember both would eventually
	check only one. ``pause_outbound`` is the incident lever — the only safe response to a
	live echo storm — and ``relay_outbound_enabled`` is the feature flag the system ships
	dormant behind.

	**Neither drops anything.** A paused relay leaves every row exactly where it is, and the
	claim query's ``order by job_seq`` is what makes the drain on re-enable ordered rather
	than merely eventual.
	"""
	config = settings if settings is not None else _settings()
	if _setting_flag(config, "pause_outbound"):
		return "Chat Settings.pause_outbound is on"
	if not _setting_flag(config, "relay_outbound_enabled"):
		return "Chat Settings.relay_outbound_enabled is off"
	return ""


# --------------------------------------------------------------------------------------
# Pure arithmetic — the parts a bench-free test can pin exactly
# --------------------------------------------------------------------------------------


def lease_seconds(
	*,
	http_timeout: float,
	max_attempts: int,
	backoff_base: float,
	backoff_cap: float,
	block_seconds: float = ratelimit.DEFAULT_MAX_BLOCK_MS / 1000.0,
	margin: float = LEASE_MARGIN_SECONDS,
	ceiling: float = LEASE_MAX_SECONDS,
) -> float:
	"""Worst-case wall clock for one relay job, plus margin. Pure, total, and clamped.

	**This is deliberately not ``http_timeout + margin``**, which is the obvious formula and
	the wrong one. One job is not one HTTP call: ``GoogleChatClient._execute`` retries
	synchronously up to ``relay_max_attempts`` times, sleeping ``compute_backoff`` between
	attempts, and :meth:`SpaceRateLimiter.acquire` can block for a few seconds before the
	first attempt even starts. Under a Google slowdown — precisely when leases matter — a
	lease sized to one timeout expires while the worker is still alive, the sweeper reaps a
	live claim, a second worker starts the same job, and the mirror gets two messages. The
	idempotency keys would save us, but a design that leans on them to cover a self-inflicted
	race is a design with one fewer safety net than it thinks.

	The jitter is counted at its ceiling (``compute_backoff`` draws ``uniform(0, ceiling)``),
	so this is an upper bound rather than an expectation. ``ceiling`` then clamps the whole
	thing: a lease is a promise nobody else will touch the row, and an arithmetic accident in
	the settings must not be able to strand a message for a day.
	"""
	attempts = max(int(max_attempts), 1)
	timeout = max(float(http_timeout), 0.0)
	base = max(float(backoff_base), 0.0)
	cap = max(float(backoff_cap), 0.0)

	sleeps = 0.0
	for attempt in range(attempts - 1):
		sleeps += min(cap, base * float(2 ** min(attempt, 30)))

	total = max(float(block_seconds), 0.0) + attempts * timeout + sleeps + max(float(margin), 0.0)
	return min(total, max(float(ceiling), 1.0))


def blocking_predecessor(open_rows: Sequence[Mapping[str, Any]], job_name: str) -> Mapping[str, Any] | None:
	"""Rule 1 as a pure function: the earlier live job that stops ``job_name`` running, if any.

	``open_rows`` is the room's non-terminal jobs ordered by ``job_seq``. The answer is the
	first row that is not ``job_name`` — because the list is already ordered and already
	filtered to :data:`BLOCKING_STATUSES`, "is there an earlier live job" is exactly "is the
	head somebody else".

	Returning the row rather than a boolean is what lets the caller put the blocker's
	``job_seq`` and ``status`` into ``last_error``. A deferral that says "deferred" and
	nothing else is a deferral nobody can diagnose at 2am.
	"""
	for row in open_rows:
		if str(row.get("status") or "") not in BLOCKING_STATUSES:
			continue
		if str(row.get("name") or "") == job_name:
			return None
		return row
	return None


@dataclass(frozen=True)
class CircuitDecision:
	"""``open`` plus how long is left, so a caller can defer by exactly that much."""

	open: bool
	retry_after_seconds: int


def circuit_decision(
	*, failures: int, threshold: int, open_until_ms: int, now_ms: int, cooldown_seconds: int
) -> CircuitDecision:
	"""Is the breaker open right now? Pure, total, and the whole of the breaker's logic.

	Two inputs, because the breaker has two states that a single counter cannot express: an
	explicit ``open_until`` instant (set when the threshold was crossed) and the consecutive
	failure count (which crosses it). Consulting the instant first is what makes the breaker
	*stay* open for the cooldown even though the counter is not incremented while it is open —
	otherwise the first probe after opening would find ``failures`` unchanged and re-open for
	a fresh cooldown on every call, which is an outage that never ends.

	``threshold <= 0`` disables the breaker. That is a configured choice, not a bug: the
	breaker protects the project's per-minute quota during a Google outage, and an operator
	who wants every job to try must be able to say so.
	"""
	if int(open_until_ms) > int(now_ms):
		remaining = (int(open_until_ms) - int(now_ms) + 999) // 1000
		return CircuitDecision(open=True, retry_after_seconds=max(remaining, 1))
	if int(threshold) <= 0:
		return CircuitDecision(open=False, retry_after_seconds=0)
	if int(failures) >= int(threshold):
		return CircuitDecision(open=True, retry_after_seconds=max(int(cooldown_seconds), 1))
	return CircuitDecision(open=False, retry_after_seconds=0)


# --------------------------------------------------------------------------------------
# Seams the bench-free suite replaces
# --------------------------------------------------------------------------------------


def build_client(
	subject: str, *, identity: str = AUTH_IDENTITY_USER, correlation_id: str = ""
) -> GoogleChatClient:
	"""The Chat client for one job. **The injection point for the fake harness.**

	``subject`` is the human this write is made as, and it comes from
	``Chat Relay Job.impersonate_user`` — recorded on the row rather than resolved at run
	time, so a retry cannot drift to a different principal. Under app authentication Google
	only lets you patch or delete messages the *app* created, so editing or deleting a
	coworker's message means being that coworker; drifting to another one is a 403 that reads
	like a scope problem and sends the next person to the wrong file.

	``identity`` comes from the row too, for that same reason. ``APP`` carries **no subject**:
	the app-identity grant is the two-legged service-account flow with no ``sub`` claim, so
	passing one would be meaningless at best. Asserted rather than ignored — a subject that
	silently does nothing is how somebody later concludes the reply is attributed to a person.
	"""
	if identity == AUTH_IDENTITY_APP:
		if subject:
			raise ValueError(
				f"an APP-identity relay was given subject={subject!r}. App authentication does "
				"not impersonate anybody — there is no `sub` claim in the grant — so a subject "
				"here means the job was written by two minds about who is speaking."
			)
		return GoogleChatClient(identity=AuthIdentity.APP, correlation_id=correlation_id or None)

	return GoogleChatClient(
		subject=subject, identity=AuthIdentity.USER, correlation_id=correlation_id or None
	)


def space_limiter() -> ratelimit.SpaceRateLimiter:
	"""The shared per-space 1-write/second bucket. Redis-backed, so it spans workers."""
	return ratelimit.SpaceRateLimiter()


def project_quota() -> ratelimit.ProjectQuota:
	"""The per-project 60-second windows, one counter per category."""
	return ratelimit.ProjectQuota()


def _logger() -> Any:
	try:
		return frappe.logger(_LOGGER_NAME)
	except Exception:  # pragma: no cover - only on a half-booted frappe
		return None


def _log(level: str, message: str, **fields: Any) -> None:
	"""One structured line. Identifiers only — never a body, never a token."""
	log = _logger()
	if log is None:
		return
	try:
		record = {"event": message, **fields}
		getattr(log, level, log.info)(json.dumps(record, sort_keys=True, default=str))
	except Exception:
		return


#: How long one blocked job stays "already reported" before it announces itself again.
#:
#: The sweeper re-evaluates every blocked job on every pass, and it used to write a fresh
#: ``Error Log`` row each time. Eleven jobs stuck behind one head-of-line blocker produced
#: **305 identical rows in a day** — a real 403 from Google sat in the middle of them, and it
#: took a query grouped by title to see it at all. An alert channel that repeats a known fact
#: faster than anybody reads it is not an alert channel; it is where alerts go to be missed.
#:
#: Long enough to stop the flood, short enough that something still stuck says so a few times
#: a day rather than once and never again.
STALE_ALERT_REPEAT_SECONDS: Final[int] = 6 * 3600


def _stale_alert_is_new(job_name: str, note: str) -> bool:
	"""Whether this blocked job has already been reported for **this** reason recently.

	Keyed on the job *and* its note, so a job whose reason changes — "waiting on provisioning"
	becoming "not a joined member" — announces itself again. The reason changing is the news.

	**Fails open.** A cache that cannot answer means the alert is written, because the cost of
	a duplicate Error Log row is noise and the cost of a swallowed one is an outage nobody is
	told about. Note that a deploy flushes this Redis, so every still-blocked job re-alerts
	once after one — which is correct: a deploy is exactly when you want to know what is stuck.
	"""
	if not job_name:
		return True
	key = f"chat:relay:stale-alert:{job_name}:{sha256(note.encode('utf-8')).hexdigest()[:16]}"
	try:
		if frappe.cache().get_value(key):
			return False
		frappe.cache().set_value(key, 1, expires_in_sec=STALE_ALERT_REPEAT_SECONDS)
	except Exception:
		return True
	return True


def _alert(title: str, context: Mapping[str, Any]) -> None:
	"""Write the operator-visible record of something that needs a human.

	Two channels and neither may raise. The ``Error Log`` row is the durable one and carries
	the full request/response context **minus secrets** — status codes, Google's canonical
	error status, the correlation id, the identity, the byte length and hash of the text. The
	``Notification Log`` is the one that actually reaches somebody, and it is best-effort
	because a notification failure must never turn a dead letter into an exception inside the
	sweeper.

	**No message body and no token, on either channel.** ``context`` is serialised as-is, so
	callers pass ``text_bytes`` and ``text_hash``, never ``text``.
	"""
	body = json.dumps(dict(context), indent=2, sort_keys=True, default=str)
	body = scrub_secrets(body)
	try:
		frappe.log_error(title=title[:140], message=body)
	except Exception:
		_log("error", "alert_log_error_failed", title=title)

	try:
		recipients = frappe.get_all(
			"Has Role",
			filters={"role": "System Manager", "parenttype": "User"},
			pluck="parent",
			limit=25,
		)
		for user in recipients or []:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": title[:140],
					"for_user": user,
					"type": "Alert",
					"document_type": RELAY_JOB,
					"document_name": str(context.get("job") or ""),
				}
			).insert(ignore_permissions=True)
	except Exception:
		# Best effort by design. The Error Log above is the durable record; a desk
		# notification that cannot be written must not abort the sweeper that was writing it.
		pass


# --------------------------------------------------------------------------------------
# The circuit breaker
# --------------------------------------------------------------------------------------


def _cache() -> Any:
	return frappe.cache()


def _circuit_state(settings: Any) -> CircuitDecision:
	"""Read the breaker. A cache failure reads as *closed*, deliberately.

	Failing closed here would mean "Redis is down, therefore stop relaying", which converts a
	cache outage into a chat outage. The breaker is an optimisation that protects Google's
	quota during a Google outage; the retry budget and the sweeper are the correctness
	mechanisms and they do not need it.
	"""
	threshold = _setting_limit(settings, "circuit_breaker_threshold", 10)
	cooldown = _setting_int(settings, "circuit_breaker_cooldown_seconds", 300)
	try:
		cache = _cache()
		failures = int(cache.get_value(_CIRCUIT_FAILURES_KEY) or 0)
		open_until = int(cache.get_value(_CIRCUIT_OPEN_UNTIL_KEY) or 0)
	except Exception:
		return CircuitDecision(open=False, retry_after_seconds=0)

	return circuit_decision(
		failures=failures,
		threshold=threshold,
		open_until_ms=open_until,
		now_ms=int(time.time() * 1000),
		cooldown_seconds=cooldown,
	)


def _circuit_record_failure(settings: Any) -> None:
	"""Count one consecutive failure against Google, and open the breaker if it crosses.

	Only *Google-surface* failures reach here. A local ``ValueError`` in a builder is our bug
	and says nothing about whether Google is answering; counting it would open the breaker on
	a code defect and hide the defect behind a cooldown.
	"""
	threshold = _setting_limit(settings, "circuit_breaker_threshold", 10)
	cooldown = _setting_int(settings, "circuit_breaker_cooldown_seconds", 300)
	if threshold <= 0:
		return
	try:
		cache = _cache()
		failures = int(cache.get_value(_CIRCUIT_FAILURES_KEY) or 0) + 1
		cache.set_value(_CIRCUIT_FAILURES_KEY, failures, expires_in_sec=_CIRCUIT_KEY_TTL_SECONDS)
		if failures < threshold:
			return
		open_until = int(time.time() * 1000) + max(cooldown, 1) * 1000
		cache.set_value(_CIRCUIT_OPEN_UNTIL_KEY, open_until, expires_in_sec=_CIRCUIT_KEY_TTL_SECONDS)
	except Exception:
		return

	_alert(
		ALERT_CIRCUIT_OPEN,
		{
			"consecutive_failures": failures,
			"threshold": threshold,
			"cooldown_seconds": cooldown,
			"what_this_means": (
				"Outbound relay is paused for the cooldown rather than burning the project's "
				"per-minute quota against a Google surface that is not answering. Nothing is "
				"dropped: every job stays Pending and drains in job_seq order when it closes."
			),
		},
	)


def _circuit_record_success() -> None:
	"""Any success closes the breaker. Consecutive means consecutive."""
	try:
		cache = _cache()
		cache.set_value(_CIRCUIT_FAILURES_KEY, 0, expires_in_sec=_CIRCUIT_KEY_TTL_SECONDS)
		cache.set_value(_CIRCUIT_OPEN_UNTIL_KEY, 0, expires_in_sec=_CIRCUIT_KEY_TTL_SECONDS)
	except Exception:
		return


# --------------------------------------------------------------------------------------
# The transition function — the ONLY writer of Chat Relay Job.status
# --------------------------------------------------------------------------------------


def transition(job: dict[str, Any], target: RelayState, *, fields: Mapping[str, Any] | None = None) -> dict:
	"""Move one job to ``target``, and project the move onto its ``Chat Message``.

	**No status field is assigned by a bare ``db_set`` anywhere else in this codebase.** The
	gate is :func:`~erpnext_enhancements.chat.sync.states.assert_transition`, which returns
	the target rather than merely validating it — so skipping the gate means writing nothing,
	and the check cannot be forgotten by the next person in a hurry.

	``job`` is a plain dict (what ``frappe.db.get_value(..., as_dict=True)`` hands back), not
	a ``Document``. Loading the document would run ``validate()`` on every retry bookkeeping
	write and cost an ORM round trip per state change on the hot path; the controller's
	``validate()`` stays as the backstop for desk edits, which is the only other way in.

	The projection onto ``Chat Message.sync_state`` uses ``update_modified=False``. The room
	digest watermark is ``(max(seq), count(*), max(modified))``, so a retry that touched
	``modified`` would invalidate every cached digest for that room on every attempt.
	"""
	destination = assert_transition(job.get("status") or "", target)

	payload: dict[str, Any] = dict(fields or {})
	if "last_error" in payload:
		payload["last_error"] = scrub_and_truncate(payload["last_error"])
	payload["status"] = destination.value

	frappe.db.set_value(RELAY_JOB, job["name"], payload)
	job.update(payload)

	_project_message_state(job, destination)
	return job


def _project_message_state(job: Mapping[str, Any], state: RelayState) -> None:
	"""Write the ``Chat Message.sync_state`` this relay state implies, or nothing at all.

	``None`` from :func:`project_to_message_state` is a real answer with three causes, and all
	three mean *write nothing* rather than *write empty*: the job has no message (a membership
	or space operation), the row came from Chat and is ``Inbound`` forever, or the state is
	the momentary ``Failed``.
	"""
	operation = str(job.get("operation") or "")
	reference = str(job.get("reference_name") or "")
	if operation not in MESSAGE_OPERATIONS or not reference:
		return
	if str(job.get("reference_doctype") or "") not in ("", CHAT_MESSAGE):
		return

	origin = frappe.db.get_value(CHAT_MESSAGE, reference, "sync_origin") or ""
	projected = project_to_message_state(state, operation=operation, origin=str(origin))
	if projected is None:
		return

	frappe.db.set_value(CHAT_MESSAGE, reference, "sync_state", projected.value, update_modified=False)


# --------------------------------------------------------------------------------------
# Claiming, deferring, releasing
# --------------------------------------------------------------------------------------

_JOB_FIELDS: Final[tuple[str, ...]] = (
	"name",
	"room",
	"job_seq",
	"operation",
	"status",
	"available_at",
	"attempts",
	"lease_expires_at",
	"reference_doctype",
	"reference_name",
	"request_id",
	"impersonate_user",
	"auth_identity",
	"payload",
	"creation",
)


def load_job(job_name: str) -> dict[str, Any] | None:
	"""One job row as a plain dict, or ``None`` if it has been purged."""
	row = frappe.db.get_value(RELAY_JOB, job_name, list(_JOB_FIELDS), as_dict=True)
	return dict(row) if row else None


def open_jobs(room: str, *, limit: int = 2) -> list[dict[str, Any]]:
	"""The room's non-terminal jobs, ordered by ``job_seq``. The FIFO, as the worker sees it.

	``frappe.get_all`` is ``get_list(ignore_permissions=True)`` wearing a friendlier name, and
	that is correct **here and only here**: ``Chat Relay Job`` is an operational table with
	zero DocPerm, carrying no message content — the row holds identifiers, a status and a
	scrubbed error string. A chat *content* read through this function would be a permission
	bypass and must apply ``permissions.membership_filter_sql`` by hand instead.
	"""
	rows = frappe.get_all(
		RELAY_JOB,
		filters={"room": room, "status": ("in", list(OPEN_STATUSES))},
		fields=["name", "job_seq", "status", "available_at", "lease_expires_at", "attempts"],
		order_by="job_seq asc",
		limit=max(int(limit), 1),
	)
	return [dict(row) for row in rows or []]


def _defer(job: dict[str, Any], *, seconds: int, reason: str) -> dict[str, Any]:
	"""Push an unclaimed ``Pending`` row forward. The ``Pending -> Pending`` self-edge.

	Deferral is the mechanism by which Rule 1 *never fails a job*, and it goes through
	:func:`transition` rather than around it so that the one place a status is written stays
	one place — even when the status does not change.
	"""
	return transition(
		job,
		RelayState.PENDING,
		fields={
			"available_at": add_to_date(now_datetime(), seconds=int(seconds)),
			"last_error": f"Deferred: {reason}",
		},
	)


def _release(job: dict[str, Any], *, seconds: int, reason: str) -> dict[str, Any]:
	"""Hand a **claimed** row back without having attempted it. ``In Progress -> Pending``.

	``attempts`` is deliberately untouched: no Google call happened, so charging the retry
	budget for a full token bucket or an unprovisioned space would dead-letter a message
	because provisioning was slow. The lease is cleared in the same write, because a released
	row that still reads as in-flight would show up in the inbound pipeline's claim check and
	in the sweeper's crashed-worker query.
	"""
	return transition(
		job,
		RelayState.PENDING,
		fields={
			"available_at": add_to_date(now_datetime(), seconds=int(seconds)),
			"lease_expires_at": None,
			"last_error": f"Released: {reason}",
		},
	)


def _lease_until(settings: Any) -> Any:
	seconds = lease_seconds(
		http_timeout=_setting_int(settings, "http_timeout_seconds", 30),
		max_attempts=_setting_int(settings, "relay_max_attempts", 5),
		backoff_base=_setting_int(settings, "relay_initial_backoff_seconds", 2),
		backoff_cap=_setting_int(settings, "backoff_cap_seconds", 32),
	)
	return add_to_date(now_datetime(), seconds=int(seconds))


def _try_claim(job_name: str, *, settings: Any) -> dict[str, Any] | None:
	"""Take the lease on one row, atomically. ``None`` means somebody else got there first.

	``for_update=True`` is a ``SELECT … FOR UPDATE``: the row lock is held to commit, so the
	re-check of ``status`` and ``available_at`` inside it cannot be raced. Two workers woken
	by two enqueues for the same room meet here, one wins, and the other reads ``In Progress``
	and leaves — which is exactly the behaviour that makes ten simultaneous enqueues produce
	ten ordered writes instead of ten collisions.

	The claim is **committed** before the handler runs. That is not premature: the claim has
	to be visible to the other workers and to the inbound pipeline's in-flight check *while*
	the HTTP call is in flight, and a claim held in an uncommitted transaction is visible to
	nobody.
	"""
	locked = frappe.db.get_value(
		RELAY_JOB,
		{"name": job_name, "status": RelayState.PENDING.value},
		["name", "status", "attempts", "available_at"],
		as_dict=True,
		for_update=True,
	)
	if not locked:
		return None

	available_at = locked.get("available_at")
	if available_at and get_datetime(available_at) > now_datetime():
		return None

	job = load_job(job_name)
	if job is None:
		return None

	transition(job, RelayState.IN_PROGRESS, fields={"lease_expires_at": _lease_until(settings)})
	frappe.db.commit()
	return job


def claim_next_job(room: str, *, settings: Any | None = None) -> dict[str, Any] | None:
	"""Claim the head of ``room``'s FIFO, or return ``None``. **The only claim edge.**

	The candidate is the lowest open ``job_seq`` for the room and nothing else, which is what
	makes Rule 1 a property of the query rather than a check somebody has to remember. Four
	ways to get ``None``, all of them normal:

	* the room has no open work;
	* the head is ``In Progress`` — another worker holds it, or a dead one does and the
	  sweeper will reap it (never this function: two claim edges would make the in-flight set,
	  which is *also* the crashed-worker detector, ambiguous);
	* the head is ``Failed`` — a decision point mid-flight, routed by whoever recorded it or
	  by the sweeper if that worker died;
	* the head is ``Pending`` but not yet due, i.e. deferred by backoff or by Rule 1.
	"""
	config = settings if settings is not None else _settings()
	head = open_jobs(room, limit=1)
	if not head:
		return None

	candidate = head[0]
	if str(candidate.get("status") or "") != RelayState.PENDING.value:
		return None

	available_at = candidate.get("available_at")
	if available_at and get_datetime(available_at) > now_datetime():
		return None

	return _try_claim(str(candidate["name"]), settings=config)


# --------------------------------------------------------------------------------------
# The dependency gate
# --------------------------------------------------------------------------------------


def _require_space(room: str) -> str:
	"""The room's Google space, or a deferral saying why there isn't one yet.

	**Provisioning is a prerequisite job, not a side effect of the first message.** A relay
	that created the space on demand would do it inside a worker holding a message lease,
	against the 60-space-writes-per-minute project bucket, with no row to retry from if it
	half-succeeded. So the message waits, visibly, and says what it is waiting for.
	"""
	row = (
		frappe.db.get_value(
			CHAT_ROOM,
			room,
			["gchat_space_name", "provisioning_state", "provisioning_error"],
			as_dict=True,
		)
		or {}
	)
	space = str(row.get("gchat_space_name") or "")
	if space:
		return space

	# --- "not yet" and "never" are different answers ---------------------------------------
	#
	# `provisioning_state = Failed` is **terminal**: `sweep_pending_provisioning` selects only
	# `Pending` and `Provisioning`, so nothing ever retries it and no space will ever appear.
	# Deferring against it is a promise the system cannot keep — the job waits, the sweeper
	# re-defers it every pass, and every later message in the room queues behind it under Rule
	# 1, forever.
	#
	# Production hit this within an hour of the DM feature being used: a Direct Message whose
	# peer was a Google *group* failed `spaces.setup` with `INVALID_ARGUMENT` (a group cannot be
	# a DM member), and three messages sat deferring behind it saying "provisioning has not
	# completed" — which reads as *in progress*. It had completed. It had failed.
	#
	# So it is skipped, terminally, carrying the room's own `provisioning_error`. `Skip` rather
	# than a dead letter because the fault is the **room**, not this message: the health report
	# already ALARMs on a room in `Failed`, and one alarm per stuck message would bury it.
	# Nothing is lost either way — the ERPNext message is intact and readable.
	state = str(row.get("provisioning_state") or "")
	if state == PROVISIONING_FAILED:
		reason = str(row.get("provisioning_error") or "no reason was recorded")
		raise Skip(
			f"room {room} will not be mirrored: provisioning failed and is not retried. {reason}"
		)

	raise Deferred(
		f"room {room} has no gchat_space_name yet; provisioning has not completed "
		f"(state={state or 'unset'})",
		seconds=DEPENDENCY_DEFER_SECONDS,
	)


def _identity_of(job: Mapping[str, Any]) -> str:
	"""``Chat Relay Job.auth_identity``, defaulting to ``USER``.

	The default is what makes this safe to deploy over a queue that predates the column: every
	job already in flight was written for the human relay, and an empty value must therefore
	mean ``USER``. Defaulting the other way would silently re-attribute a backlog of coworker
	messages to the app.

	An unrecognised value is **not** coerced to the default. A column that grows a third state
	somebody forgot to handle should stop the job, not pick an identity on its behalf.
	"""
	identity = str(job.get("auth_identity") or "").strip() or AUTH_IDENTITY_USER
	if identity not in AUTH_IDENTITIES:
		raise ValueError(
			f"Chat Relay Job.auth_identity is {identity!r}, which is neither "
			f"{AUTH_IDENTITY_USER!r} nor {AUTH_IDENTITY_APP!r}. Refusing to guess which Google "
			"identity this write is made as."
		)
	return identity


def _require_joined_author(room: str, user: str, identity: str) -> None:
	"""Refuse to relay until the author is a **joined** member of the space.

	Under the DWD human-relay model the write is made *as the author*, so an author who is
	not in the space gets a 403 — a non-retryable classification that would dead-letter a
	perfectly good message because a membership job had not run yet. ``INVITED`` is not
	``JOINED``: an invited coworker cannot post, and treating the two as the same is how the
	gate passes and the write fails one call later.

	``gchat_member_state`` empty is treated as *not joined*: on a room whose membership sync
	has not run, nothing has confirmed the person is in the space, and guessing yes is the
	direction that produces the 403.

	**It does not apply to the app.** A Chat app is *installed* in a space, not a member of it
	— there is no ``Chat Room Member`` row for it and there never will be, so this check would
	defer every Triton reply forever while reporting a membership sync that is not late for
	anybody. The app's equivalent failure is a 404 on first post if it was never added to the
	space, which the retry classifier already handles as non-retryable.
	"""
	if identity == AUTH_IDENTITY_APP:
		return

	state = frappe.db.get_value(CHAT_ROOM_MEMBER, {"room": room, "user": user}, "gchat_member_state") or ""
	if str(state) != "JOINED":
		raise Deferred(
			f"{user} is not a JOINED member of room {room} (gchat_member_state="
			f"{str(state) or 'unset'}); membership sync has not completed",
			seconds=DEPENDENCY_DEFER_SECONDS,
		)


def _subject_for(job: Mapping[str, Any]) -> str:
	"""The Workspace email this job is executed as.

	``impersonate_user`` is a ``Link`` to ``User`` and Frappe names users by their email
	address, so it is normally the answer already; the ``User.email`` read is the fallback for
	a site where somebody named a user something else. An empty value is a **bug in the
	writer**, not a transient condition, so it fails rather than defers — a message-relay job
	with no principal can never be executed and deferring it forever would hide it.
	"""
	if _identity_of(job) == AUTH_IDENTITY_APP:
		# The app-identity grant has no `sub` claim, so there is no subject to resolve and an
		# empty `impersonate_user` is the correct state rather than a missing one.
		return ""

	user = str(job.get("impersonate_user") or "").strip()
	if not user:
		raise ValueError(
			"Chat Relay Job.impersonate_user is empty on a USER-identity job. A coworker mirror is "
			"written as the real author under domain-wide delegation and there is no app-identity "
			"fallback for it: app auth may only patch or delete messages the app itself created, "
			"and using it would stamp somebody's message with an App badge (CQ-1). Triton's own "
			"replies are the APP case and are written with auth_identity=APP at enqueue time — a "
			"USER job with no author is a bug in the writer, not that."
		)
	if "@" in user:
		return user
	email = frappe.db.get_value("User", user, "email") or ""
	if not email:
		raise ValueError(f"User {user!r} has no email address to impersonate.")
	return str(email)


# --------------------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------------------


def _charge_space_bucket(space: str, *, cost_ms: int) -> None:
	"""Spend ``cost_ms`` of the space's one-write-per-second budget, or defer by the wait.

	``block=True`` sleeps out a short wait inside the worker, which is safe here in a way it
	would not be in a web request: the job's lease is already held, a room is a strict FIFO,
	and the second being waited on is a second nobody else could have used. Past
	``max_block_ms`` the refusal comes back and the job is deferred instead — free, because
	the sweeper is the delivery guarantee either way.

	**The bucket is an optimisation; backoff is the correctness mechanism.** Google publishes
	two caveats no design can remove — additional backend checks, and internal per-space
	limits that are not in the Quotas page — so staying under one write per second does not
	guarantee no 429. Never delete the retry loop because this exists.
	"""
	decision = space_limiter().acquire(space, cost_ms=int(cost_ms), block=True)
	if decision.allowed:
		return
	seams.bump(seams.COUNTER_QUOTA_BACKOFFS)
	raise Deferred(
		f"space {space} write bucket busy for another {decision.wait_ms} ms",
		seconds=max(1, (decision.wait_ms + 999) // 1000),
	)


def _charge_project_quota(bucket: str, *, limit: int, cost: int = 1) -> None:
	"""Spend from one per-project 60-second window, or defer to the next one.

	The buckets are **independent counters per category** — message writes, membership writes,
	space writes, attachment writes — not one shared pool. Merging them would waste roughly
	four fifths of the API.
	"""
	if project_quota().charge(bucket, limit=int(limit), cost=int(cost)):
		return
	seams.bump(seams.COUNTER_QUOTA_BACKOFFS)
	raise Deferred(f"project quota bucket {bucket!r} is spent for this minute", seconds=DEFER_SECONDS)


def _charge_message_write(settings: Any, space: str, *, attachment_count: int = 0) -> None:
	"""One message write: the per-space second, then the project's per-minute message bucket.

	``attachment_count`` routes the *space* half through :func:`charge_attachment_write`, which
	reserves ``(n + 1)`` seconds in a single acquire — the *n* uploads plus the create they are
	attached to. The project half is charged here either way, because the ``messages.create``
	is a message write whatever it carries, and the attachment writes land in their own
	600-per-minute bucket rather than this one.
	"""
	if int(attachment_count or 0) > 0:
		charge_attachment_write(settings, space, count=int(attachment_count))
	else:
		_charge_space_bucket(space, cost_ms=ratelimit.SPACE_WRITE_COST_MS)
	_charge_project_quota(
		ratelimit.BUCKET_MESSAGE_WRITES,
		limit=_setting_limit(settings, "project_message_writes_per_minute", 3000),
	)


def charge_membership_write(settings: Any) -> None:
	"""**No per-space budget.** Membership writes appear in no per-space row of Google's table.

	Exported for the membership task rather than kept private, because getting this wrong is
	the expensive mistake: charging ``members.create`` to the 1-write-per-second space bucket
	throttles a bulk provisioning sweep roughly 300× harder than the API requires (1/second
	instead of 300/minute), and the symptom is "provisioning is mysteriously slow" rather than
	an error anybody can grep for.
	"""
	_charge_project_quota(
		ratelimit.BUCKET_MEMBERSHIP_WRITES,
		limit=_setting_limit(settings, "project_membership_writes_per_minute", 300),
	)


def charge_space_write(settings: Any) -> None:
	"""**No per-space budget either** — for ``spaces.setup``/``create`` the space does not exist
	yet. Only the project-wide 60-per-minute space-write bucket."""
	_charge_project_quota(
		ratelimit.BUCKET_SPACE_WRITES,
		limit=_setting_limit(settings, "project_space_writes_per_minute", 60),
	)


def charge_attachment_write(settings: Any, space: str, *, count: int = 1) -> None:
	"""``media.upload`` **shares** the per-space write bucket with ``messages.create``.

	So a message with *n* attachments costs the space ``n + 1`` seconds — the *n* uploads and
	then the create — which is what :func:`attachments.upload_cost_ms` computes and what this
	reserves in a **single** acquire. :data:`ratelimit.UPLOAD_WRITE_COST_MS` is the ``n = 1``
	case of that function and not a separate rule, which is why ``count`` defaults to 1.

	**Reserving the whole burst up front is the point.** Two acquires — one for the upload, one
	for the create — leave a gap another worker's ``messages.create`` can take, and the space
	then owes a second to somebody else while our half-uploaded message waits in the bucket's
	way. One acquire makes the burst atomic against every other *writer*.

	What it does not do is pace our own three calls against each other: they leave in quick
	succession and Google's own per-second bucket may 429 the second and third. That is
	expected and is the client's retry loop's job — Google publishes two caveats saying staying
	under one write per second does not guarantee no 429 anyway, so backoff is the correctness
	mechanism here and the reservation is the optimisation.

	The 600-per-minute attachment-write bucket is charged *n* times, and it is **not** the
	message-write bucket: the per-project 60-second limits are independent counters per
	category (``PHASE2_VERIFIED.md`` §2 correction 3). The ``messages.create`` that follows
	still charges the message bucket itself, in :func:`_charge_message_write`.
	"""
	uploads = max(int(count or 0), 0)
	_charge_space_bucket(space, cost_ms=attachments.upload_cost_ms(uploads))
	_charge_project_quota(
		ratelimit.BUCKET_ATTACHMENT_WRITES,
		limit=_setting_limit(
			settings, "project_attachment_writes_per_minute", DEFAULT_ATTACHMENT_WRITES_PER_MINUTE
		),
		cost=max(uploads, 1),
	)


# --------------------------------------------------------------------------------------
# Message payload
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _MessageContext:
	"""Everything a message handler needs, read once so the handler does no ad-hoc queries."""

	message: str
	room: str
	space: str
	client_message_id: str
	text: str
	truncated: bool
	stored_google_name: str
	subject: str
	identity: str
	request_id: str


def _deep_link(room: str, message: str) -> str:
	"""The ERPNext deep link appended to a truncated relay. Best effort, never fatal."""
	try:
		from erpnext_enhancements.chat.links import build_message_deep_link

		return "\n\n… (truncated) " + build_message_deep_link(room, message=message)
	except Exception:
		return "\n\n… (truncated)"


def _message_context(job: Mapping[str, Any], settings: Any, *, need_text: bool) -> _MessageContext:
	"""Load and shape the message this job relays, applying the byte budget.

	The oversize rule is **truncate with a deep link**, not reject. Rejecting at compose time
	would let Google's 32,000-byte limit dictate what an employee may say inside ERPNext,
	which inverts the whole point of ERPNext being the source of truth. The full body stays on
	the row; ``truncated_for_relay`` records that the mirror is a summary.

	The limit is measured against **the whole encoded message**, not ``len(text)`` — Google's
	published figure is "the maximum message size, including the message contents".
	"""
	reference = str(job.get("reference_name") or "")
	row = frappe.db.get_value(
		CHAT_MESSAGE,
		reference,
		["name", "room", "text", "text_plain", "client_message_id", "gchat_message_name", "is_deleted"],
		as_dict=True,
	)
	if not row:
		raise Skip(f"Chat Message {reference} no longer exists")

	client_id = str(row.get("client_message_id") or "")
	if not client_id:
		raise ValueError(
			f"Chat Message {reference} has no client_message_id. It is derived in before_insert and "
			"stored forever; without it the relay cannot address the message by its client- alias, "
			"and inbound echo suppression has nothing to match."
		)

	room = str(row.get("room") or job.get("room") or "")
	space = _require_space(room)
	identity = _identity_of(job)
	subject = _subject_for(job)
	_require_joined_author(room, str(job.get("impersonate_user") or ""), identity)

	text = ""
	truncated = False
	if need_text:
		# `[[ref:7]]` → `[7]`. Google Chat has neither the manifest nor the SPA's chip, so the
		# raw marker reached the space verbatim and read as a bug in the bot rather than as a
		# citation. Flattened at this boundary and **not** in storage: the stored `text` keeps
		# the markers because the SPA needs them to place its chips.
		raw = citations.flatten_refs(str(row.get("text_plain") or row.get("text") or ""))
		limit = _setting_int(settings, "message_byte_limit", budget.MESSAGE_BYTE_LIMIT_DEFAULT)
		text, truncated = budget.fit_to_byte_budget(raw, limit, suffix=_deep_link(room, reference))

	return _MessageContext(
		message=reference,
		room=room,
		space=space,
		client_message_id=client_id,
		text=text,
		truncated=truncated,
		stored_google_name=str(row.get("gchat_message_name") or ""),
		subject=subject,
		identity=identity,
		request_id=str(job.get("request_id") or "")
		or ids.request_id(reference, str(job.get("operation") or ""), site=frappe.local.site),
	)


# --------------------------------------------------------------------------------------
# Reconciliation — compare-and-set, never a blind write
# --------------------------------------------------------------------------------------


def _bind_google_name(ctx: _MessageContext, google_name: str, response: Mapping[str, Any]) -> str:
	"""Record the Google resource name for a message we just created. Returns what happened.

	**By the time this runs, the inbound path may already have bound it.** The Workspace Event
	for our own ``messages.create`` can be delivered, fetched and matched before the HTTP
	response carrying the resource name gets back to us — that is §4.D, and it is not rare
	under load. So this is a compare-and-set:

	* nothing stored ⇒ bind it;
	* the **same** value stored ⇒ succeed silently, the echo got there first, that is the
	  design working;
	* a **different** value stored ⇒ there really are two messages in the space, and leaving
	  both is the one outcome nobody wants. Alert, and delete the one *this job* just created.

	Why delete ours rather than "the later one" by ``createTime``: the stored name is
	necessarily the one that was bound before we arrived, so it is the one the SPA, the
	inbound pipeline, any attachment row and any cached digest already point at. Removing it
	would orphan all of those to save a wall-clock comparison. Both timestamps go into the
	alert so an operator can see if the assumption ever fails.

	The insert is wrapped in ``savepoint`` catching **both** ``DuplicateEntryError`` *and*
	``UniqueValidationError``. They share no base beyond ``Exception``:
	``DuplicateEntryError`` is raised only for a **primary-key** collision, and
	``gchat_message_name`` is a plain unique index, which raises the second one. Catching only
	the first — which is what the ADR says — would fail to catch the collision the code exists
	to catch. Rule 2: a collision is **success**, not an error.
	"""
	if not google_name:
		return "unnamed"

	stored = str(frappe.db.get_value(CHAT_MESSAGE, ctx.message, "gchat_message_name") or "")
	if not stored:
		if _set_unique_google_name(ctx.message, google_name):
			return "bound"
		stored = str(frappe.db.get_value(CHAT_MESSAGE, ctx.message, "gchat_message_name") or "")
		if not stored:
			# The value is held by a *different* Chat Message row: first-writer-wins on the
			# resource name (Rule 2). The mirror exists, which is what the writer wanted, so
			# this is success — but it is odd enough to say out loud once.
			seams.bump(seams.COUNTER_DUPLICATES_REJECTED)
			_alert(
				ALERT_DUPLICATE_MESSAGE,
				{
					"what": "another Chat Message row already holds this gchat_message_name",
					"message": ctx.message,
					"room": ctx.room,
					"gchat_message_name": google_name,
				},
			)
			return "held_elsewhere"

	if stored == google_name:
		return "same"

	return _resolve_duplicate(ctx, stored=stored, created=google_name, response=response)


def _set_unique_google_name(message: str, google_name: str) -> bool:
	"""Write ``gchat_message_name`` inside a savepoint. ``True`` if it landed.

	The savepoint is mandatory rather than hygiene: a failed statement can poison the
	surrounding transaction, and everything after this in the job — the counters, the
	``Done`` transition — happens in that transaction.
	"""
	from frappe.database.database import savepoint

	try:
		with savepoint(catch=(frappe.DuplicateEntryError, frappe.UniqueValidationError)):
			frappe.db.set_value(
				CHAT_MESSAGE, message, "gchat_message_name", google_name, update_modified=False
			)
	except Exception as exc:
		# Frappe raises the typed exceptions from the ORM's insert/update path; a raw
		# ``set_value`` can surface the driver's own IntegrityError instead. Treating a unique
		# collision as anything but success would break Rule 2, and re-raising anything else is
		# what keeps this from swallowing a real fault.
		if not _is_unique_collision(exc):
			raise
		return False

	stored = str(frappe.db.get_value(CHAT_MESSAGE, message, "gchat_message_name") or "")
	return stored == google_name


def _is_unique_collision(exc: BaseException) -> bool:
	"""Is this exception a unique-index violation, whatever layer raised it?

	Class names across the MRO plus the driver's own predicate, because the three layers that
	can raise here (the ORM, ``frappe.db``, the driver) do not agree on a type.
	``is_unique_key_violation`` is **not** used on its own: MariaDB's primary-key message
	satisfies both its predicates, and Frappe's own insert path is only correct because it
	tests the primary-key one first.
	"""
	names = {type(exc).__name__} | {klass.__name__ for klass in type(exc).__mro__}
	if names & {"DuplicateEntryError", "UniqueValidationError", "IntegrityError"}:
		return True
	try:
		return bool(frappe.db.is_unique_key_violation(exc))
	except Exception:
		return False


def _resolve_duplicate(
	ctx: _MessageContext, *, stored: str, created: str, response: Mapping[str, Any]
) -> str:
	"""Two Google messages for one ERPNext row. Alert, then delete the one we just made.

	Deleting is not optional and neither is the alert. Leaving both means every coworker in
	the space sees the message twice forever, and ERPNext believes only one of them exists —
	so nothing downstream will ever clean it up. The delete is addressed by resource name (not
	the ``client-`` alias: only one message in a space can hold a given ``messageId``, so the
	duplicate by definition does not have ours) and made as the same impersonated author, so a
	retried cleanup is a no-op rather than a second decision.
	"""
	seams.bump(seams.COUNTER_DUPLICATES_REJECTED)
	_alert(
		ALERT_DUPLICATE_MESSAGE,
		{
			"what": "Google holds two messages for one Chat Message row",
			"message": ctx.message,
			"room": ctx.room,
			"space": ctx.space,
			"bound_gchat_message_name": stored,
			"created_gchat_message_name": created,
			"bound_create_time": frappe.db.get_value(CHAT_MESSAGE, ctx.message, "gchat_create_time"),
			"created_create_time": response.get("createTime"),
			"action": "deleting the message this relay job created; the bound one is what the SPA, "
			"the inbound pipeline and any cached digest already reference",
		},
	)

	try:
		build_client(ctx.subject, identity=ctx.identity, correlation_id=f"dedupe-{ctx.message}").delete_message(created, force=True)
	except GoogleChatAPIError as exc:
		if exc.status == 404:
			return "duplicate_already_gone"
		raise
	return "duplicate_deleted"


# --------------------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------------------

#: ``operation`` → handler. The enum in the column **is** the dispatch key; the payload never
#: carries a dotted method path, so the arbitrary-code sink the older Drive outbox has to
#: guard against does not exist here. Provisioning, membership and attachment operations
#: register their own handlers with :func:`register_handler` rather than editing this file,
#: which is what keeps three concurrent Phase 2 tasks out of each other's diffs.
OPERATION_HANDLERS: dict[str, Callable[[dict[str, Any], Any], dict[str, Any]]] = {}


def register_handler(operation: str, handler: Callable[[dict[str, Any], Any], dict[str, Any]]) -> None:
	"""Register the handler for one ``operation``. Idempotent; last registration wins."""
	OPERATION_HANDLERS[str(operation)] = handler


def _outbound_attachment_plan(ctx: _MessageContext, settings: Any) -> attachments.OutboundPlan:
	"""What of this message's files Chat gets. Empty when attachment mirroring is off.

	The flag is read off the already-loaded ``Chat Settings`` document rather than through
	``attachments.mirroring_attachments()`` for one reason worth stating: this runs inside a
	claimed relay job that has the document in hand, and a second ``get_single_value`` per
	message is a query per message forever to answer a question we already know.

	Mirroring is **off by default and is a deliberate asymmetry** with inbound. Outbound is a
	choice — whether our files leave the building — and a company that has not made it must not
	have it made for them by a relay. Inbound has no such choice: the attachment already exists
	in the space and ERPNext is the record of what was said.
	"""
	if not _setting_flag(settings, "mirror_attachments"):
		return attachments.OutboundPlan()
	return attachments.plan_outbound_attachments(
		attachments.collect_outbound_attachments(ctx.message), attachments.resolve_limits()
	)


def _handle_message_create(job: dict[str, Any], settings: Any) -> dict[str, Any]:
	"""``spaces.messages.create`` as the author, with both idempotency keys — and its files.

	``messageId`` is the durable key — unique within a space **forever**, server-enforced, no
	expiry — and ``requestId`` is a best-effort optimisation for an immediate network retry
	whose deduplication window Google does not document. Sending both is what makes the
	timeout-then-retry case produce one message instead of two.

	**No thread is passed.** ``spaceThreadingState`` is output-only and the API cannot create
	a threaded space at all, so there is no create-time decision to get wrong; threading stays
	an ERPNext-side concept.

	A message that has already been soft-deleted is still relayed. The delete is the *next*
	``job_seq`` in the room and will tombstone it a second later; skipping the create instead
	would leave that delete addressing a message which never existed, which is a 404, which is
	classified non-retryable, which is a dead letter for a message the user did in fact send.

	--------------------------------------------------------------------------------------
	Attachments: uploaded **here**, before the create, and not as a relay job of their own
	--------------------------------------------------------------------------------------

	Both designs were available and this one is chosen deliberately. The binding constraint is
	that *a Chat message cannot reference an attachment that has not been uploaded*: the
	``attachmentUploadToken`` that ``Message.attachment[]`` carries only exists once
	``media.upload`` has answered. A separate ``Attachment Upload`` job would therefore have to
	persist that token and hand it to a later ``Message Create`` row — and the reference
	documents **no lifetime for a token at all**, so the hand-off would rest on an undated
	guess, across a queue the production deploy ``FLUSHDB``s. Doing both inside one job keeps
	the token in a local variable for the two seconds it is needed, keeps the room's FIFO one
	entry shorter per message, and makes the whole burst one reservation on the space bucket
	instead of two that another worker can split.

	Three consequences the code below makes explicit:

	* **The reservation is made before the loop**, sized ``(n + 1)`` seconds — see
	  :func:`charge_attachment_write`. It is taken from ``len(plan.upload)``, the files that
	  passed the size and privacy checks, because a file that is never uploaded costs nothing.
	* **The upload is user-auth only.** ``chat.bot`` is absent from ``media.upload``'s scope
	  list, so ``upload_outbound_attachments`` asserts the client is the impersonated author
	  rather than assuming it. The relay is always user-auth, so this is a check that should
	  never fire — which is exactly the kind that catches the change that breaks it.
	* **A failed upload never blocks the message.** ``upload_outbound_attachments`` returns the
	  tokens it got *and* the files it did not, the create runs with whatever succeeded, and the
	  losers are recorded ``Failed`` with a reason and named in the message text. Refusing to
	  relay because a file did not upload would lose the words as well as the file, which
	  inverts the rule that losing the mirror must never lose the message.
	"""
	ctx = _message_context(job, settings, need_text=True)
	plan = _outbound_attachment_plan(ctx, settings)
	_charge_message_write(settings, ctx.space, attachment_count=len(plan.upload))

	client = build_client(ctx.subject, identity=ctx.identity, correlation_id=str(job.get("name") or ""))
	result = attachments.upload_outbound_attachments(client, space=ctx.space, message=ctx.message, plan=plan)

	text, truncated = _text_with_attachment_notice(ctx, settings, plan.skipped + result.failed)
	response = _create_message(client, ctx, text, result.tokens)

	if truncated:
		frappe.db.set_value(CHAT_MESSAGE, ctx.message, "truncated_for_relay", 1, update_modified=False)

	attachments.record_outbound_attachments(
		message=ctx.message,
		room=ctx.room,
		uploads=result.uploads,
		skipped=plan.skipped,
		failed=result.failed,
	)

	google_name = str(response.get("name") or "")
	outcome = _bind_google_name(ctx, google_name, response)
	_record_google_timestamps(ctx.message, response)
	return {
		"outcome": outcome,
		"gchat_message_name": google_name,
		"space": ctx.space,
		"attachments_uploaded": len(result.uploads),
		"attachments_not_relayed": len(plan.skipped) + len(result.failed),
	}


def _create_message(client: Any, ctx: _MessageContext, text: str, tokens: Sequence[str]) -> dict[str, Any]:
	"""``messages.create``, carrying ``attachment[]`` when there is anything to carry.

	The text-only path is untouched — ``client.create_message`` with both idempotency keys, as
	it always was. With tokens, the same builder runs and
	:func:`attachments.with_attachments` decorates its output before
	:meth:`GoogleChatClient.execute` runs it, which is the seam that module declares. Three
	things that buys, in the order they matter:

	1. every validation ``build_create_message_call`` performs still runs — the ``client-`` id
	   rules, the identity check on notification options, the thread trap;
	2. ``chat.googleapis.com`` stays confined to ``gchat/client.py``, which
	   ``tests/test_chat_guardrails.py`` asserts;
	3. adding an ``attachment=`` parameter to the builder is the tidier long-term shape and
	   belongs to whoever owns ``client.py``, so it is reported at the checkpoint rather than
	   taken unilaterally here.
	"""
	if not tokens:
		return client.create_message(ctx.space, ctx.client_message_id, ctx.request_id, text)
	call = attachments.with_attachments(
		build_create_message_call(
			ctx.space,
			ctx.client_message_id,
			ctx.request_id,
			text,
			identity=getattr(client, "identity", AuthIdentity.USER),
		),
		tokens,
	)
	return client.execute(call)


def _text_with_attachment_notice(
	ctx: _MessageContext,
	settings: Any,
	not_relayed: Sequence[tuple[attachments.OutboundAttachment, str]],
) -> tuple[str, bool]:
	"""``(body, truncated)`` for the create, with the "these files did not come" line folded in.

	The notice cannot be appended after :func:`_message_context` has already fitted the text,
	because that is precisely how a message sitting exactly on the 32,000-byte limit becomes a
	400 — the failure mode ``budget.fit_to_byte_budget``'s ``suffix`` argument exists to
	prevent. So the notice is concatenated first and the **whole** string is re-fitted. Fitting
	a string that already fits is a no-op, so the ordinary no-attachment path pays nothing.

	If the combined body overflows, the notice is what gets cut rather than the employee's
	words — it is at the tail — and the deep link still lands, so the recipient can reach the
	ERPNext row that holds both the full text and the files.
	"""
	if not not_relayed:
		return ctx.text, ctx.truncated
	notice = attachments.skipped_attachment_notice(not_relayed, _attachment_deep_link(ctx))
	if not notice:
		return ctx.text, ctx.truncated
	combined = f"{ctx.text}\n\n{notice}" if ctx.text else notice
	limit = _setting_int(settings, "message_byte_limit", budget.MESSAGE_BYTE_LIMIT_DEFAULT)
	body, truncated = budget.fit_to_byte_budget(combined, limit, suffix=_deep_link(ctx.room, ctx.message))
	return body, bool(truncated or ctx.truncated)


def _attachment_deep_link(ctx: _MessageContext) -> str:
	"""The bare ERPNext link the attachment notice points at. Best effort, never fatal.

	Separate from :func:`_deep_link` because that one is a *truncation* suffix and carries its
	own "… (truncated)" prefix and blank lines; pasting that inside a bracketed attachment
	notice would tell the reader the message was cut when it was not.
	"""
	try:
		from erpnext_enhancements.chat.links import build_message_deep_link

		return build_message_deep_link(ctx.room, message=ctx.message)
	except Exception:
		return ""


def _handle_message_update(job: dict[str, Any], settings: Any) -> dict[str, Any]:
	"""``spaces.messages.patch`` — ``updateMask=text``, ``allowMissing=true``, ``client-`` alias.

	Addressed through the alias rather than ``gchat_message_name`` on purpose: an edit must not
	have to wait for the create's response to have been written back. Combined with
	``allowMissing``, an edit that somehow overtakes its create upserts instead of 404ing —
	the safety net under Rule 1, not its mechanism.

	``text`` is the only mask path user authentication may set; the card fields are app-auth
	only and app auth cannot touch a human's message.
	"""
	ctx = _message_context(job, settings, need_text=True)
	_charge_message_write(settings, ctx.space)

	alias = message_alias(ctx.space, ctx.client_message_id)
	client = build_client(ctx.subject, identity=ctx.identity, correlation_id=str(job.get("name") or ""))
	response = client.patch_message(alias, text=ctx.text, allow_missing=True)

	if ctx.truncated:
		frappe.db.set_value(CHAT_MESSAGE, ctx.message, "truncated_for_relay", 1, update_modified=False)

	google_name = str(response.get("name") or "")
	outcome = "patched"
	if google_name and not ctx.stored_google_name:
		# `allowMissing` upserted, or the create's response never made it back. Either way we
		# now know the resource name and the same compare-and-set applies.
		outcome = _bind_google_name(ctx, google_name, response)
	_record_google_timestamps(ctx.message, response)
	return {"outcome": outcome, "gchat_message_name": google_name, "space": ctx.space}


def _handle_message_delete(job: dict[str, Any], settings: Any) -> dict[str, Any]:
	"""``spaces.messages.delete?force=true``, addressed through the ``client-`` alias.

	``force=true`` also removes threaded replies. With ``force=false`` the delete *fails* when
	replies exist, which for a mirror means a tombstoned ERPNext message whose Google twin is
	still on screen — ERPNext has already decided the delete, and a relay that half-fails
	leaves the two sides disagreeing. ``force`` is user-authentication only, which the relay
	always is.

	A 404 is **success**: the message is not there, which is what the job wanted. Retrying it
	would burn the budget to reach the same state.

	Note what this does *not* do: ``Chat Message.text`` is left on the ERPNext row. Google's
	tombstone is rich in metadata and empty of content, so after this call ERPNext is the only
	party that still has what was said. Read paths filter ``is_deleted = 0``.
	"""
	ctx = _message_context(job, settings, need_text=False)
	_charge_message_write(settings, ctx.space)

	alias = message_alias(ctx.space, ctx.client_message_id)
	client = build_client(ctx.subject, identity=ctx.identity, correlation_id=str(job.get("name") or ""))
	try:
		client.delete_message(alias, force=True)
	except GoogleChatAPIError as exc:
		if exc.status == 404:
			return {"outcome": "already_absent", "space": ctx.space}
		raise
	return {"outcome": "deleted", "space": ctx.space}


def rfc3339_to_site_datetime(stamp: Any) -> Any:
	"""RFC-3339 UTC (what Google sends) → the **site-local naive** datetime Frappe stores.

	The conversion is the whole function, and skipping it is a bug this app has already
	shipped once: ``frappe.utils.now_datetime()`` is site-local, every ``Datetime`` column is
	site-local, and writing a UTC instant into one makes every later comparison wrong by the
	site's offset. For Rule 3 (LAST-WRITER-WINS by ``lastUpdateTime``, ERPNext breaks ties)
	that error runs **in the direction that always makes Google win**, which silently inverts
	locked decision #1 — ERPNext is the source of truth.

	Returns ``None`` for anything unparseable rather than raising: this is called after Google
	has already accepted the write, and a timestamp we cannot read must not fail a message
	that was delivered.
	"""
	if not stamp:
		return None
	try:
		import zoneinfo

		from frappe.utils import get_system_timezone

		parsed = get_datetime(str(stamp).replace("Z", "+00:00"))
		if parsed is None:
			return None
		if parsed.tzinfo is None:
			# Already naive: Frappe's parser could not see an offset, so there is nothing to
			# convert and guessing a zone would be worse than storing what we were given.
			return parsed
		return parsed.astimezone(zoneinfo.ZoneInfo(get_system_timezone())).replace(tzinfo=None)
	except Exception:
		return None


def _record_google_timestamps(message: str, response: Mapping[str, Any]) -> None:
	"""Store Google's own ``createTime`` / ``lastUpdateTime``, converted, ``update_modified=False``.

	Not decoration: Rule 3 compares an inbound edit's ``lastUpdateTime`` against these, and
	``lastUpdateTime`` missing is what "ERPNext wins" is derived from — so a row that never
	records one keeps winning ties it should have lost.
	"""
	updates: dict[str, Any] = {}
	created = rfc3339_to_site_datetime(response.get("createTime"))
	updated = rfc3339_to_site_datetime(response.get("lastUpdateTime"))
	if created is not None:
		updates["gchat_create_time"] = created
	if updated is not None:
		updates["gchat_last_update_time"] = updated
	if not updates:
		return
	try:
		frappe.db.set_value(CHAT_MESSAGE, message, updates, update_modified=False)
	except Exception:
		_log("warning", "google_timestamp_write_failed", message=message)


def _handle_attachment_upload(job: dict[str, Any], settings: Any) -> dict[str, Any]:
	"""``Attachment Upload`` as a standalone job: **there is nothing left for it to do.**

	Registered rather than omitted, and that is the whole content of this function. A missing
	dispatch entry raises ``ValueError`` in :func:`execute_claimed_job`, which classifies
	non-retryable, dead-letters, and raises an operator alert saying "no handler registered" —
	an alarm about a design decision, fired at 2am, on a row that is not actually broken.

	Uploads happen inside :func:`_handle_message_create`, before the ``messages.create`` that
	references their tokens, because a Chat message cannot reference an attachment that has not
	been uploaded and the token binding it documents no lifetime. So a row with this operation
	is either a leftover from a build that planned the other design or something written by
	hand, and ``Skipped`` — terminal, no alert, ``last_error`` explaining why — is the honest
	answer for both.
	"""
	raise Skip(
		"attachment uploads are performed inside the Message Create job, before the "
		"messages.create that references their attachmentUploadToken; a standalone "
		"Attachment Upload job has nothing to upload and no message to bind to"
	)


register_handler(OPERATION_MESSAGE_CREATE, _handle_message_create)
register_handler(OPERATION_MESSAGE_UPDATE, _handle_message_update)
register_handler(OPERATION_MESSAGE_DELETE, _handle_message_delete)
register_handler(OPERATION_ATTACHMENT_UPLOAD, _handle_attachment_upload)


# --------------------------------------------------------------------------------------
# Outcome routing
# --------------------------------------------------------------------------------------


def _succeed(job: dict[str, Any], outcome: Mapping[str, Any]) -> None:
	transition(
		job,
		RelayState.DONE,
		fields={
			"completed_at": now_datetime(),
			"lease_expires_at": None,
			"last_error": "",
			"http_status": 0,
			"google_error_status": "",
		},
	)
	if str(job.get("operation") or "") in MESSAGE_OPERATIONS:
		seams.bump(seams.COUNTER_MESSAGES_RELAYED)
	_circuit_record_success()
	_log(
		"info",
		"relay_done",
		job=job.get("name"),
		room=job.get("room"),
		job_seq=job.get("job_seq"),
		operation=job.get("operation"),
		attempts=job.get("attempts"),
		outcome=outcome.get("outcome"),
	)


def classify_job_failure(exc: BaseException) -> RetryDecision:
	"""Is this failure worth another **job** attempt? Not the same question the client asks.

	``gchat.backoff.classify_error`` decides whether to retry *inside one HTTP call*, and by
	the time an exception reaches here that budget is already spent. This decides whether the
	whole job comes back later — a different and much longer timescale, drained by the
	sweeper, which is what carries the relay across a Google outage or a deploy.

	Three branches and each one matters:

	* **An HTTP status** is authoritative. 429/5xx come back; every other 4xx does not. A 403
	  is a wrong identity, a missing scope or DWD that was never granted, and retrying it for
	  five minutes turns a fast legible failure into a slow confusing one.
	* **A transport error** — DNS, TLS, connect, read timeout — always comes back. It carries
	  no status because the request never produced one, and ``classify_error(None, exc)``
	  would read the wrapper class rather than the socket failure underneath it and answer
	  ``FAIL``. The network being down is the textbook retryable condition.
	* **Anything else** is our bug (a missing handler, a message with no ``client_message_id``)
	  and fails immediately, because no amount of waiting fixes a defect and ``Dead`` is what
	  puts it in front of a human.
	"""
	status = getattr(exc, "status", None)
	if isinstance(status, int) and status:
		return classify_error(status, None)
	if isinstance(exc, GoogleChatTransportError):
		return RetryDecision.RETRY
	return classify_error(None, exc)


def _fail(job: dict[str, Any], exc: BaseException, *, settings: Any) -> None:
	"""Record one failed attempt and route the row: back to ``Pending``, or to ``Dead``.

	The attempt is counted **on the way into ``Failed``**, and the budget decision happens on
	the way out. That ordering is what makes a crashed worker cost an attempt too: the sweeper
	reaps an expired lease through the same ``In Progress -> Failed`` edge, so a job that
	kills its worker every time dead-letters instead of looping forever.

	A non-retryable classification — 403 (wrong identity, missing scope, DWD not granted), 404,
	400 — goes straight to ``Dead`` rather than parking in ``Failed`` where the sweeper would
	pick it up again. Retrying those converts a fast legible failure into a slow confusing one.
	"""
	status = getattr(exc, "status", None)
	google_status = str(getattr(exc, "google_status", "") or "")
	if isinstance(exc, GoogleChatError):
		_circuit_record_failure(settings)

	decision = classify_job_failure(exc)
	attempts = cint(job.get("attempts")) + 1
	detail = f"{type(exc).__name__}: {exc}"

	transition(
		job,
		RelayState.FAILED,
		fields={
			"attempts": attempts,
			"lease_expires_at": None,
			"last_error": detail,
			"http_status": int(status) if isinstance(status, int) else 0,
			"google_error_status": google_status,
		},
	)

	max_attempts = max(_setting_int(settings, "relay_max_attempts", 5), 1)
	if decision is not RetryDecision.RETRY or attempts >= max_attempts:
		_dead_letter(
			job,
			reason=("retry budget exhausted" if decision is RetryDecision.RETRY else "non-retryable"),
			detail=detail,
			status=status,
			google_status=google_status,
			attempts=attempts,
			max_attempts=max_attempts,
		)
		return

	delay = next_attempt_delay(
		attempts,
		_setting_int(settings, "relay_initial_backoff_seconds", 2),
		_setting_int(settings, "backoff_cap_seconds", 32),
	)
	transition(
		job,
		RelayState.PENDING,
		fields={"available_at": add_to_date(now_datetime(), seconds=int(delay))},
	)
	seams.bump(seams.COUNTER_RETRIES)
	_log(
		"warning",
		"relay_retry",
		job=job.get("name"),
		room=job.get("room"),
		job_seq=job.get("job_seq"),
		operation=job.get("operation"),
		attempts=attempts,
		retry_in_seconds=int(delay),
		http_status=status,
		google_status=google_status,
	)


def _dead_letter(
	job: dict[str, Any],
	*,
	reason: str,
	detail: str,
	status: Any = None,
	google_status: str = "",
	attempts: int = 0,
	max_attempts: int = 0,
) -> None:
	"""``Failed -> Dead``, an Error Log with the full context, and an operator alert.

	**A Dead message is still perfectly readable in ERPNext.** Losing the mirror must never
	lose the message: the body is on the ``Chat Message`` row, which is the source of truth,
	and the only thing that has failed is the copy in Chat. What ``Dead`` buys is that a
	coworker who believes they sent something into a Google space finds out that nobody there
	received it — which is why this is an alert and not a log line.
	"""
	transition(job, RelayState.DEAD, fields={"completed_at": now_datetime(), "last_error": detail})
	seams.bump(seams.COUNTER_DEAD_LETTERS)
	_alert(
		ALERT_DEAD_LETTER,
		{
			"job": job.get("name"),
			"room": job.get("room"),
			"job_seq": job.get("job_seq"),
			"operation": job.get("operation"),
			"reference_doctype": job.get("reference_doctype"),
			"reference_name": job.get("reference_name"),
			"impersonate_user": job.get("impersonate_user"),
			"request_id": job.get("request_id"),
			"attempts": attempts,
			"max_attempts": max_attempts,
			"http_status": status,
			"google_error_status": google_status,
			"reason": reason,
			"error": detail,
			"note": (
				"The ERPNext message is intact and readable; only the Google Chat mirror failed. "
				"Re-sending means creating a NEW relay job — a Dead row is terminal because "
				"job_seq is the room's FIFO position and later jobs have drained past it."
			),
		},
	)
	_log(
		"error",
		"relay_dead",
		job=job.get("name"),
		room=job.get("room"),
		operation=job.get("operation"),
		attempts=attempts,
		http_status=status,
		google_status=google_status,
		reason=reason,
	)


# --------------------------------------------------------------------------------------
# Executing one claimed job
# --------------------------------------------------------------------------------------


def execute_claimed_job(job: dict[str, Any], *, settings: Any) -> str:
	"""Run one job that this worker already holds the lease on. Returns a short outcome word.

	Every exit path writes the row. There is **no ``finally``-only cleanup**: a ``finally``
	block does not run when the process is killed, and being killed mid-relay is the ordinary
	case a lease exists for. The lease expiring is what covers the paths this function never
	returns from.
	"""
	operation = str(job.get("operation") or "")
	handler = OPERATION_HANDLERS.get(operation)

	try:
		if handler is None:
			raise ValueError(
				f"no handler registered for Chat Relay Job.operation {operation!r}. The dispatch "
				"table is populated at import time by whichever module owns the operation; a "
				"missing entry means that module was not imported, not that the row is invalid."
			)
		outcome = handler(job, settings)
	except Deferred as exc:
		_release(job, seconds=exc.seconds, reason=exc.reason)
		_log(
			"info",
			"relay_deferred",
			job=job.get("name"),
			room=job.get("room"),
			job_seq=job.get("job_seq"),
			reason=exc.reason,
			seconds=exc.seconds,
		)
		return "deferred"
	except Skip as exc:
		transition(
			job,
			RelayState.SKIPPED,
			fields={"completed_at": now_datetime(), "lease_expires_at": None, "last_error": str(exc)},
		)
		_log("info", "relay_skipped", job=job.get("name"), room=job.get("room"), reason=str(exc))
		return "skipped"
	except IllegalTransition:
		# The row is now in a state the sweeper cannot reason about. Never retried, always
		# alarmed: a retry would write another illegal transition on top of the first.
		raise
	except Exception as exc:
		# Broad on purpose: the classifier decides retryable from fatal, not this frame.
		_fail(job, exc, settings=settings)
		return "failed"

	_succeed(job, outcome)
	return "done"


# --------------------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------------------


def drain_room(room: str, *, max_jobs: int = MAX_JOBS_PER_RUN, max_seconds: float = MAX_RUN_SECONDS) -> dict:
	"""Claim and run this room's FIFO until it is empty or a bound is hit. **The worker.**

	The kill switch is re-read **before every job**, not once at the top. That is what makes
	``pause_outbound`` usable during an incident: an operator flipping it mid-burst stops the
	next write rather than the next sweep, and nothing in flight is dropped — the current job
	finishes and everything after it stays ``Pending`` in ``job_seq`` order.

	Both bounds exist because of the deploy. RQ's short queue kills a job at 300 seconds and a
	release kills it whenever it likes, so the loop stops early, commits, and re-enqueues
	itself; a worker cut off mid-loop loses nothing but a wake-up, because every completed job
	was committed as it finished.
	"""
	started = time.monotonic()
	summary = {"room": room, "claimed": 0, "done": 0, "failed": 0, "deferred": 0, "skipped": 0, "stopped": ""}

	while summary["claimed"] < int(max_jobs):
		settings = _settings()
		paused = outbound_pause_reason(settings)
		if paused:
			summary["stopped"] = f"paused: {paused}"
			break

		circuit = _circuit_state(settings)
		if circuit.open:
			summary["stopped"] = "circuit breaker open"
			_defer_due_jobs(room, seconds=circuit.retry_after_seconds, reason="circuit breaker open")
			break

		if time.monotonic() - started > float(max_seconds):
			summary["stopped"] = "run time bound"
			enqueue_room(room)
			break

		job = claim_next_job(room, settings=settings)
		if job is None:
			summary["stopped"] = summary["stopped"] or "nothing claimable"
			break

		summary["claimed"] += 1
		outcome = execute_claimed_job(job, settings=settings)
		summary[outcome if outcome in summary else "done"] += 1
		frappe.db.commit()

		if outcome == "deferred":
			# The head of the FIFO is waiting on something outside this worker's control
			# (provisioning, membership, a full bucket). Everything behind it is blocked by
			# Rule 1 anyway, so continuing would spin.
			summary["stopped"] = "head of line deferred"
			break

	if summary["claimed"] >= int(max_jobs) and not summary["stopped"]:
		summary["stopped"] = "job count bound"
		enqueue_room(room)

	return summary


def run_relay_job(job: str) -> dict:
	"""The enqueued entry point for one outbox row. Enqueue this, never :func:`drain_room`.

	**The parameter is ``job`` and must never be ``job_name``.** ``job_name`` is one of
	``frappe.enqueue``'s *own* parameters, so a kwarg by that name is consumed by the enqueue
	machinery and never reaches this function — the worker then dies with "missing 1 required
	positional argument", having been handed nothing at all. It spent a spell named
	``job_name`` while the caller correctly passed ``job=``, which failed the other way round
	("unexpected keyword argument"), and the fix in v1.277.13 changed the caller to match this
	name and so swapped one failure for the other. ``tests/test_chat_outbox.py`` now asserts
	both halves: the caller passes this parameter's name, *and* that name is not one frappe
	reserves.

	It does **not** simply run ``job``. If an earlier ``job_seq`` in the same room is still
	live, Rule 1 says this job is deferred — and then, rather than going back to sleep, the
	worker drains the room from its head. A woken worker that declines to do the room's oldest
	work is a worker that leaves nine of ten burst messages waiting for the next five-minute
	sweep.
	"""
	row = load_job(job)
	if row is None:
		return {"job": job, "result": "missing"}

	room = str(row.get("room") or "")
	if not room:
		return {"job": job, "result": "no room"}

	head = open_jobs(room, limit=1)
	blocker = blocking_predecessor(head, job)
	if blocker is not None and str(row.get("status") or "") == RelayState.PENDING.value:
		_defer(
			row,
			seconds=DEFER_SECONDS,
			reason=(
				f"Rule 1 CREATE-BEFORE-EDIT: job_seq {blocker.get('job_seq')} in this room is "
				f"{blocker.get('status')}"
			),
		)
		frappe.db.commit()

	return drain_room(room)


def enqueue_room(room: str) -> None:
	"""Wake a worker for ``room``. A latency optimisation, never the delivery guarantee.

	``enqueue_after_commit=True`` is not optional and is asserted by
	``tests/test_chat_guardrails.py``: the worker is a different process against the same
	database, so a job dispatched inside the current transaction reads rows that do not exist
	yet. And even with it, the enqueue can simply vanish — a deploy ``FLUSHDB``s the queue
	Redis — which is why :func:`sweep_relay_jobs` exists and why nothing here treats a
	successful enqueue as proof of anything.

	No ``deduplicate=True``. It drops a new enqueue while an existing job is ``STARTED``, not
	merely ``QUEUED``, so a message written in the last second of a drain would be silently
	de-duplicated away and wait for the sweeper. The claim is atomic, so a redundant wake-up
	costs one query and nothing else.
	"""
	try:
		frappe.enqueue(
			"erpnext_enhancements.chat.sync.outbound.drain_room",
			queue=RELAY_QUEUE,
			timeout=RELAY_JOB_TIMEOUT,
			enqueue_after_commit=True,
			room=room,
		)
	except Exception:
		# The sweeper is the guarantee. A queue that will not accept work is an incident, but
		# it is not this job's incident, and raising here would fail a message that is already
		# safely on disk.
		_log("warning", "relay_enqueue_failed", room=room)


def _defer_due_jobs(room: str, *, seconds: int, reason: str) -> None:
	"""Push the room's due head forward. Used when the circuit breaker opens.

	Only the head, deliberately: everything behind it is blocked by Rule 1 regardless, and
	rewriting six hundred rows to say "the breaker is open" would turn a Google outage into a
	database one.
	"""
	head = open_jobs(room, limit=1)
	if not head or str(head[0].get("status") or "") != RelayState.PENDING.value:
		return
	job = load_job(str(head[0]["name"]))
	if job is None:
		return
	_defer(job, seconds=seconds, reason=reason)
	frappe.db.commit()


def sweep_relay_jobs() -> dict:
	"""**The delivery guarantee.** Cron, every five minutes. Assume RQ never retries — it does not.

	Four jobs, in this order:

	1. **Reap expired leases.** A row still ``In Progress`` past ``lease_expires_at`` is a
	   worker that died — SIGKILLed by a deploy, OOM-killed, or gone with its container. It is
	   returned through ``In Progress -> Failed`` so the attempt is counted (a job that kills
	   its worker every time must dead-letter, not loop) and then routed by budget. Every reap
	   is an alert: crashed workers are not routine.
	2. **Route stranded ``Failed`` rows.** A worker that died *between* recording the failure
	   and routing it leaves a row in the one state nothing else looks at.
	3. **Alert on jobs blocked too long on provisioning.** Not a failure — the gate is doing
	   its job — but a room whose space has not appeared in half an hour is a room whose
	   coworkers think they are talking to somebody.
	4. **Wake a worker for every room with due work.** Skipped while the kill switch is on, and
	   nothing is dropped: the rows stay exactly where they are and drain in ``job_seq`` order
	   when it comes off.

	This is what survives a Google outage, a worker restart, and the deploy's ``FLUSHDB``.
	"""
	settings = _settings()
	summary: dict[str, Any] = {
		"leases_reaped": 0,
		"failed_routed": 0,
		"rooms_enqueued": 0,
		"dependency_stale": 0,
		"paused": outbound_pause_reason(settings),
	}
	now = now_datetime()
	batch = max(_setting_int(settings, "sweeper_batch_size", 200), 1)

	for row in (
		frappe.get_all(
			RELAY_JOB,
			filters={"status": RelayState.IN_PROGRESS.value, "lease_expires_at": ("<", now)},
			fields=[
				"name",
				"room",
				"job_seq",
				"operation",
				"impersonate_user",
				"lease_expires_at",
				"attempts",
			],
			order_by="lease_expires_at asc",
			limit=batch,
		)
		or []
	):
		job = load_job(str(row["name"]))
		if job is None or str(job.get("status") or "") != RelayState.IN_PROGRESS.value:
			continue
		_alert(
			ALERT_CRASHED_WORKER,
			{
				"job": job.get("name"),
				"room": job.get("room"),
				"job_seq": job.get("job_seq"),
				"operation": job.get("operation"),
				"attempts": job.get("attempts"),
				"lease_expired_at": row.get("lease_expires_at"),
				"note": (
					"A worker took this job's lease and never came back — a deploy restart, an OOM "
					"kill or a crash. Cleanup is by lease expiry and never by a finally block, "
					"because a finally block does not run when the process is killed."
				),
			},
		)
		_fail(
			job,
			TimeoutError(f"lease expired at {row.get('lease_expires_at')}; the worker holding this job died"),
			settings=settings,
		)
		summary["leases_reaped"] += 1
	frappe.db.commit()

	for row in (
		frappe.get_all(
			RELAY_JOB,
			filters={"status": RelayState.FAILED.value},
			fields=["name"],
			order_by="modified asc",
			limit=batch,
		)
		or []
	):
		job = load_job(str(row["name"]))
		if job is None or str(job.get("status") or "") != RelayState.FAILED.value:
			continue
		summary["failed_routed"] += 1
		_route_stranded_failure(job, settings=settings)
	frappe.db.commit()

	stale_cutoff = add_to_date(now, seconds=-DEPENDENCY_STALE_SECONDS)
	stale = frappe.get_all(
		RELAY_JOB,
		filters={
			"status": RelayState.PENDING.value,
			"attempts": 0,
			"creation": ("<", stale_cutoff),
		},
		fields=["name", "room", "job_seq", "operation", "last_error", "creation"],
		order_by="creation asc",
		limit=25,
	)
	for row in stale or []:
		# `attempts = 0` and a deferral note is the signature of a job that has never been
		# tried because the gate never opened — as opposed to one that is merely young. The
		# prefix match is done here rather than as a SQL `like` so the filter stays a plain
		# equality query that any index can serve.
		note = str(row.get("last_error") or "")
		if not note.startswith(("Deferred:", "Released:")):
			continue
		summary["dependency_stale"] += 1
		if _stale_alert_is_new(str(row.get("name") or ""), note):
			_alert(ALERT_DEPENDENCY_STALE, dict(row))

	if summary["paused"]:
		_log("warning", "relay_sweep_paused", reason=summary["paused"])
		return summary

	seen: set[str] = set()
	for row in (
		frappe.get_all(
			RELAY_JOB,
			filters={"status": RelayState.PENDING.value, "available_at": ("<=", now)},
			fields=["room"],
			order_by="job_seq asc",
			limit=batch,
		)
		or []
	):
		room = str(row.get("room") or "")
		if not room or room in seen:
			continue
		seen.add(room)
		enqueue_room(room)
		summary["rooms_enqueued"] += 1

	# The enqueues are `enqueue_after_commit=True`, so without this they are never dispatched.
	frappe.db.commit()
	return summary


def _route_stranded_failure(job: dict[str, Any], *, settings: Any) -> None:
	"""Send a ``Failed`` row on to ``Pending`` or ``Dead`` by its remaining budget."""
	attempts = cint(job.get("attempts"))
	max_attempts = max(_setting_int(settings, "relay_max_attempts", 5), 1)
	if attempts >= max_attempts:
		_dead_letter(
			job,
			reason="retry budget exhausted (routed by the sweeper)",
			detail=str(job.get("last_error") or "retry budget exhausted"),
			attempts=attempts,
			max_attempts=max_attempts,
		)
		return
	delay = next_attempt_delay(
		attempts,
		_setting_int(settings, "relay_initial_backoff_seconds", 2),
		_setting_int(settings, "backoff_cap_seconds", 32),
	)
	transition(
		job,
		RelayState.PENDING,
		fields={"available_at": add_to_date(now_datetime(), seconds=int(delay))},
	)
	seams.bump(seams.COUNTER_RETRIES)


@frappe.whitelist()
def retry_relay_job(name: str, reset_attempts: int = 1) -> dict:
	"""Operator "try again". **System Manager only, and it re-enters the state machine.**

	It never calls Google. Everything it can do is write a status through the same transition
	table the worker uses and then wake a worker — because a button that talks to Google
	directly is a second, untested relay path that exists only on the day somebody is already
	having a bad one.

	``Dead`` is refused, and that is the interesting case. ``Dead`` is terminal with no
	resurrection edge: ``job_seq`` is the room's FIFO position, later jobs have already
	drained past it, and reinstating a stale position would replay an edit before the create
	it edits. Re-sending means a **new** relay job with a new ``job_seq``, which the outbox
	mints.

	``reset_attempts`` defaults on. An operator pressing retry means "try again"; leaving
	``attempts`` at the maximum would route the row straight back to ``Dead`` and make the
	button a no-op that looks like a bug.
	"""
	frappe.only_for("System Manager")

	job = load_job(str(name))
	if job is None:
		frappe.throw(f"Chat Relay Job {name} does not exist.")

	status = str(job.get("status") or "")
	if status in (RelayState.DONE.value, RelayState.DEAD.value, RelayState.SKIPPED.value):
		frappe.throw(
			f"Chat Relay Job {name} is {status}, which is terminal. Re-sending creates a NEW relay "
			"job with a new job_seq — job_seq is the room's FIFO position and later jobs have "
			"already drained past this one, so reviving it would replay an edit before its create."
		)

	fields: dict[str, Any] = {
		"available_at": now_datetime(),
		"lease_expires_at": None,
		"last_error": f"Manual retry by {frappe.session.user}",
	}
	if cint(reset_attempts):
		fields["attempts"] = 0

	if status == RelayState.IN_PROGRESS.value:
		# The claim edge is one-way, so a live claim is not something a button may steal. Only
		# an expired lease — a worker that is definitely gone — may be taken back.
		lease = job.get("lease_expires_at")
		if lease and get_datetime(lease) > now_datetime():
			frappe.throw(
				f"Chat Relay Job {name} is In Progress with a lease until {lease}. A worker is "
				"holding it; wait for the lease to expire (the sweeper reclaims it) rather than "
				"racing a live claim."
			)
		transition(job, RelayState.FAILED, fields={"lease_expires_at": None})

	transition(job, RelayState.PENDING, fields=fields)
	frappe.db.commit()
	enqueue_room(str(job.get("room") or ""))
	frappe.db.commit()

	_log("info", "relay_manual_retry", job=name, by=frappe.session.user, status_before=status)
	return {"job": name, "status": RelayState.PENDING.value, "was": status}
