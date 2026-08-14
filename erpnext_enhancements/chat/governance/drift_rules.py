# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What counts as drift, and what merely counts as *switched off*. **No Frappe, no imports.**

Phase 6 §4.I. :mod:`drift` does the I/O; every judgement is here, so it can be executed
rather than inspected — the split :mod:`chat.governance.alert_rules`,
:mod:`chat.notifications.policy` and :mod:`chat.doctype.chat_settings.chat_settings_rules`
already use.

--------------------------------------------------------------------------------------
The rule everything below is built on, because the obvious detector is a trap
--------------------------------------------------------------------------------------

**Every drift detector written against absence is a configuration detector wearing a
costume.** The obvious question — *"is there a Google message with no ERPNext row?"*, *"is
there an ERPNext message with no Google name?"* — has the same answer on a site that drifted
and on a site where the mirror was never switched on. And this app **ships switched off**:
``enabled = 0``, ``dry_run_mode = 1``, ``relay_outbound_enabled = 0``,
``relay_inbound_enabled = 0``, the last one described in its own settings field as *"inbound
events are still received and stored, but never turned into messages"*.

So on a shipped-state site, *every* message in *every* mirrored room satisfies the naive
predicate. A detector built that way reports the entire corpus as drifted, forever, and
cannot tell "the mirror drifted" from "the mirror has not been turned on yet" — which is the
one distinction the operator reading it needs.

**This repository has already learned this lesson once, about backfill patches**, and wrote
it down: *prefer a backfill keyed on the rule the writer applies over one keyed on emptiness
— emptiness is a fact about the schema migration, not about the data.* The same sentence
holds here with two words changed. Emptiness is a fact about the **configuration**, not about
the mirror.

Hence the rule every class below obeys, and :data:`EVIDENCE` states per class:

    **A class may only fire on positive evidence that the mirror acted on this object and
    the two sides then disagreed. Never on the absence of a value.**

A dead-lettered relay job, a completed relay job, a settled inbound event, a populated
reconcile watermark — each is a record that the machinery *ran*. An empty column is not.

--------------------------------------------------------------------------------------
Why nothing here repairs anything
--------------------------------------------------------------------------------------

Detection only, deliberately, and not out of caution. Three separate reviews of a design that
*did* repair the one class that looked safe — *"Google has a message ERPNext does not"* —
each independently killed it, on grounds that were checked against the source:

* Its detection cannot separate itself from the class it refused. An ERPNext-authored message
  whose Google binding was lost is present in Google and absent by ``gchat_message_name``, so
  it satisfies the predicate exactly. Replaying it reaches ``classify_inbound``, which needs
  ``is_client_message_id(facts.client_message_id)`` to be true to reach ``ECHO`` at all — and
  whether Google returns ``clientAssignedMessageId`` on a read is an open ``VERIFY:`` in this
  repo. If it does not, the replay classifies ``NEW`` and inserts a row whose
  ``client_message_id`` carries the ``inbound-`` prefix, which does **not** collide with the
  original on ``unique(room, client_message_id)``. **Two live rows for one Google message,
  created by the repairer**, and no shipped path can merge or relay them away.
* Its repair is a guaranteed no-op on the population that most needs it. The synthetic
  delivery id keys on the resource name alone and ``Chat Inbound Event.pubsub_message_id`` is
  unique, so a resource whose first replay settled ``Failed`` — never re-driven, never purged
  — collides and returns "duplicate". Nothing happens, three nights running.
* Its findings are, by the design's own admission, a subset of what the hourly sweep already
  recovers through the same ingest path.

A repair path that ships disabled *and* cannot be shown safe is strictly worse than no repair
path: it reads as a safety net.

--------------------------------------------------------------------------------------
Two guards, not three, and the third is named rather than faked
--------------------------------------------------------------------------------------

The specification asks for three: a settling window, a per-object cap that demotes a repeat
offender to report-only, and a per-run cap. **Everything here is already report-only**, so
the per-object cap has nothing to demote and is not built. Building a vestigial one would put
a dial in the settings form that does nothing — the failure this phase has spent four changes
removing. It arrives with repair.

The two that do apply are both moved off repairs and onto **findings**, which is where the
worst case actually lives: a mass false positive in a report-only class is invisible to a cap
that counts repairs.
"""

from __future__ import annotations

from typing import Final

# --- classes -------------------------------------------------------------------
#
# Slugs, not D-numbers. The tokens D1-D7 are cited in shipped code with a *different*
# meaning -- `chat/audit.py`, `chat/governance/tombstone.py` and `chat/governance/export.py`
# all use them for the prompt-vs-code divergences in `docs/chat-phase6-plan.md` -- and two
# incompatible meanings for one token inside one package is a permanent tax on every reader.

#: A relay write was attempted and permanently abandoned.
RELAY_DEAD_LETTER: Final[str] = "relay_dead_letter"

#: A relay write completed and the resource name never came back.
BOUND_LOST_AFTER_RELAY: Final[str] = "bound_lost_after_relay"

#: An inbound event arrived, was durably abandoned, and produced no message.
INBOUND_ABANDONED: Final[str] = "inbound_abandoned"

#: A mirrored room the reconciliation sweep has not reached in far too long.
RECONCILE_STALE: Final[str] = "reconcile_stale"

#: A mirrored room nobody can read, which therefore reports clean on everything else.
ROOM_UNREADABLE: Final[str] = "room_unreadable"

MESSAGE_CLASSES: Final[tuple[str, ...]] = (
	RELAY_DEAD_LETTER,
	BOUND_LOST_AFTER_RELAY,
	INBOUND_ABANDONED,
)
ROOM_CLASSES: Final[tuple[str, ...]] = (RECONCILE_STALE, ROOM_UNREADABLE)
CLASSES: Final[tuple[str, ...]] = MESSAGE_CLASSES + ROOM_CLASSES

#: The positive fact each class fires on. Not documentation — :func:`missing_evidence` reads
#: it, and a class added without an entry cannot be emitted.
EVIDENCE: Final[dict[str, str]] = {
	RELAY_DEAD_LETTER: (
		"a Chat Relay Job for this object reached `Dead`, which is a record that the write was "
		"attempted, retried, and given up on. An unrelayed message on a site with the relay "
		"switched off has no such row."
	),
	BOUND_LOST_AFTER_RELAY: (
		"a Chat Relay Job for this object reached `Done` — Google accepted the write — and the "
		"message still carries no `gchat_message_name`. The completed job is the evidence; the "
		"empty column alone would be every message on a dormant site."
	),
	INBOUND_ABANDONED: (
		"a Chat Inbound Event for this resource reached `Failed`, which the inbound sweeper "
		"never re-drives and retention never purges. The event arriving is the evidence that "
		"the mirror was live; `resulting_message` being empty is the divergence."
	),
	RECONCILE_STALE: (
		"the room is `Ready` with a Google space, so the sweep is supposed to visit it. This is "
		"the one threshold with a documented value — the ADR's `chat_reconcile_stale` alarm, "
		"specified at seven days and never built."
	),
	ROOM_UNREADABLE: (
		"the room is `Ready` with a Google space and has **no active member at all**. "
		"`spaces.messages.list` is user-authenticated, so there is nobody to impersonate. "
		"Precisely this case, and not the broader 'the read failed': a member whose grant is "
		"bad produces a 403 that the sweep already alerts on, whereas an empty membership "
		"makes the sweep return with a reason string and **no alert** — which is why it has "
		"been invisible, and why the room reports clean on every other class."
	),
}

#: One object, one class. A `Dead` create job on a message with no resource name satisfies
#: two predicates; the dead letter is the more specific and more actionable fact, so it wins.
#: Order is precedence, highest first.
PRECEDENCE: Final[tuple[str, ...]] = (
	RELAY_DEAD_LETTER,
	BOUND_LOST_AFTER_RELAY,
	INBOUND_ABANDONED,
)

# --- lifecycle -----------------------------------------------------------------

STATE_OPEN: Final[str] = "Open"
STATE_CLEARED: Final[str] = "Cleared"
STATE_ACCEPTED: Final[str] = "Accepted"
STATES: Final[tuple[str, ...]] = (STATE_OPEN, STATE_CLEARED, STATE_ACCEPTED)

#: States a re-observation updates rather than re-opening.
LIVE_STATES: Final[frozenset[str]] = frozenset({STATE_OPEN, STATE_ACCEPTED})

EVENT_OBSERVE: Final[str] = "observe"
EVENT_ACCEPT: Final[str] = "accept"
EVENT_CLEAR: Final[str] = "clear"

_TRANSITIONS: Final[dict[tuple[str, str], str]] = {
	(STATE_OPEN, EVENT_OBSERVE): STATE_OPEN,
	(STATE_OPEN, EVENT_ACCEPT): STATE_ACCEPTED,
	(STATE_OPEN, EVENT_CLEAR): STATE_CLEARED,
	# `Accepted` is "we know, and we are not fixing it" — a dead letter for a message whose
	# room was deleted, say. Re-observing must not undo that, or the operator who triaged it
	# gets it back tomorrow.
	(STATE_ACCEPTED, EVENT_OBSERVE): STATE_ACCEPTED,
	(STATE_ACCEPTED, EVENT_CLEAR): STATE_CLEARED,
	# `Cleared` is terminal. A recurrence opens a new row, for the reason the alert path
	# gives: reopening overwrites the timestamp that says this was resolved once, and losing
	# it destroys the only evidence the condition is flapping rather than continuing.
}

# --- guards --------------------------------------------------------------------

#: Fallback for ``Chat Settings.drift_settling_minutes``. Thirty minutes is a **proposal, not
#: a lookup** — no settling duration exists anywhere in this repo or its documents. It is
#: chosen against the relay's own worst case: a room drains at one write per second, so a
#: 600-message backlog is ten minutes of legitimate work, and thirty gives that three times
#: over before anything it produced is called drift.
DEFAULT_SETTLING_MINUTES: Final[int] = 30

#: Fallback for ``Chat Settings.drift_max_findings_per_run``. Also a proposal. It counts
#: **findings**, not repairs, which is the whole point — see :func:`run_verdict`.
DEFAULT_MAX_FINDINGS_PER_RUN: Final[int] = 200

#: Fallback for ``Chat Settings.drift_reconcile_stale_hours``. 168 = seven days, the one
#: number in this module with a documented source: the ADR's `chat_reconcile_stale` alarm.
DEFAULT_RECONCILE_STALE_HOURS: Final[int] = 168

#: How many rooms the reconciliation sweep visits per hourly pass. Mirrors
#: ``reconcile.RECONCILE_ROOM_BATCH``; :func:`reconcile_stale_hours` needs it to avoid
#: alarming on a site that is simply large.
RECONCILE_ROOM_BATCH: Final[int] = 25

VERDICT_REPORT: Final[str] = "report"
VERDICT_HALT: Final[str] = "halt"


def classify_message(facts: dict) -> str | None:
	"""The class this object is in, or ``None``.

	``facts`` is a plain dict, so the caller's query shape is the caller's business:

	``has_dead_relay_job``  a ``Chat Relay Job`` for this message reached ``Dead``
	``has_done_relay_job``  one reached ``Done``
	``gchat_message_name``  the binding, or empty
	``inbound_failed``      a ``Chat Inbound Event`` for it settled ``Failed``
	``resulting_message``   what that event produced, or empty

	Every branch tests an **affirmative** fact first and the empty column second. Reversing
	either pair turns the class into a configuration detector — see the module docstring.
	"""
	if facts.get("has_dead_relay_job"):
		return RELAY_DEAD_LETTER
	if facts.get("has_done_relay_job") and not (facts.get("gchat_message_name") or "").strip():
		return BOUND_LOST_AFTER_RELAY
	if facts.get("inbound_failed") and not (facts.get("resulting_message") or "").strip():
		return INBOUND_ABANDONED
	return None


def classify_room(facts: dict) -> str | None:
	"""``room_unreadable`` beats ``reconcile_stale``, and the order is load-bearing.

	A room nobody can impersonate into is *why* its watermark is old, so reporting the
	staleness would describe the symptom and bury the cause. It is also the more serious
	finding on its own: such a room returns nothing for every other class, which is
	indistinguishable from a clean room unless something says so.
	"""
	if not facts.get("is_mirrored"):
		return None
	if not (facts.get("subject") or "").strip():
		return ROOM_UNREADABLE
	hours = facts.get("hours_since_reconcile")
	if hours is None or hours < 0:
		return None
	if hours >= float(facts.get("stale_after_hours") or DEFAULT_RECONCILE_STALE_HOURS):
		return RECONCILE_STALE
	return None


def missing_evidence(drift_class: str) -> bool:
	"""True when a class has no :data:`EVIDENCE` entry, and therefore may not be emitted.

	The property that keeps this module honest in a year: a class added without writing down
	what positive fact it fires on fails here rather than shipping as an absence test.
	"""
	return drift_class not in EVIDENCE


def reconcile_stale_hours(room_count: int, configured_hours: int = 0) -> int:
	"""The staleness threshold, floored at twice the sweep's own legitimate interval.

	The ADR's seven days is right on a small site and **wrong on a large one**, in the
	direction that matters. The sweep takes :data:`RECONCILE_ROOM_BATCH` rooms per hourly
	pass ordered by oldest watermark, so on an N-room site every room is legitimately
	``ceil(N / batch)`` hours stale and none of them is drifted. At 200 rooms that is 8
	hours — fine against seven days. At 5,000 rooms it is 200 hours, and a flat seven-day
	threshold would alarm on the whole estate for doing exactly what it was built to do.

	Doubling is the margin: a threshold at exactly the interval alarms on the room that
	happens to be last in the rotation, every single pass.
	"""
	configured = int(configured_hours or 0) or DEFAULT_RECONCILE_STALE_HOURS
	rooms = max(0, int(room_count or 0))
	if rooms <= 0:
		return configured
	batch = max(1, RECONCILE_ROOM_BATCH)
	full_pass_hours = -(-rooms // batch)  # ceil, without importing math
	return max(configured, full_pass_hours * 2)


def is_settled(age_seconds: float | None, settling_minutes: int) -> bool:
	"""Has this object been quiet long enough to be called drifted rather than in flight?

	``age_seconds`` is how long since the object last changed, computed by the caller from a
	**single** clock. That is not a style note: ``now_datetime()`` is site-local, Google's
	timestamps are UTC instants, and subtracting one from the other has already shipped a bug
	in this app. The pure function takes the difference so it cannot make that mistake.
	"""
	if age_seconds is None:
		# Unknown age. Treated as unsettled: a finding withheld this run is reported next
		# run, and a finding raised against an object still being written is a false one
		# that an operator has to chase.
		return False
	minutes = int(settling_minutes or 0) or DEFAULT_SETTLING_MINUTES
	return float(age_seconds) >= minutes * 60


def room_is_converging(open_relay_jobs: int) -> bool:
	"""A room with live relay work is mid-flight, not drifted.

	Room-level rather than message-level, because Rule 1 head-of-line blocking means one
	stuck job holds up every later message in that room — so the later messages look
	unmirrored while being perfectly healthy.
	"""
	return int(open_relay_jobs or 0) > 0


def run_verdict(finding_count: int, cap: int) -> str:
	"""``report`` or ``halt``, over the **whole run** and counting findings.

	Two departures from the specification, both deliberate.

	**It counts findings, not repairs.** The cap exists because *"a mass divergence is the
	exact condition where the classifier is most likely to be the thing that is wrong"* — and
	that is at its most true for classes nothing repairs, which a repair-counting cap cannot
	see at all. Everything in this module is report-only, so a cap on repairs would be a
	circuit breaker wired to a circuit with no current in it.

	**It halts rather than trims.** Writing the first N findings of a suspect ten thousand
	would leave a table that looks like a small, credible problem, which is worse than an
	empty one plus an alarm — the same asymmetry the membership engine already makes explicit
	when it declines to apply a truncated diff.
	"""
	ceiling = int(cap or 0) or DEFAULT_MAX_FINDINGS_PER_RUN
	return VERDICT_HALT if int(finding_count or 0) > ceiling else VERDICT_REPORT


def report_key(drift_class: str, scope: str) -> str:
	"""``class::scope`` — the identity of a *finding*, not of an observation.

	Carries nothing measured, for the reason the alert path gives at length: a key holding
	the number that changes matches no predecessor, so every observation looks new and the
	table grows exactly as it would with no deduplication, while every surface claims
	otherwise.
	"""
	cls = (drift_class or "").strip().lower()
	if cls not in CLASSES:
		raise ValueError(f"unknown drift class {drift_class!r}; add it to drift_rules.CLASSES")
	if missing_evidence(cls):
		raise ValueError(
			f"drift class {cls!r} has no EVIDENCE entry, so nothing states what positive fact "
			"it fires on. Write one; a class without it is an absence test."
		)
	slug = _slug(scope)
	return f"{cls}::{slug}" if slug else cls


def _slug(value: str | None) -> str:
	text = (value or "").strip().lower()
	out: list[str] = []
	for ch in text:
		if ch.isalnum() or ch in "-_":
			out.append(ch)
		elif out and out[-1] != "-":
			out.append("-")
	return "".join(out).strip("-")


def transition(state: str, event: str) -> str | None:
	"""The next state, or ``None`` if refused. ``None`` rather than "unchanged" on purpose."""
	return _TRANSITIONS.get((state or "", event or ""))


def is_live(state: str) -> bool:
	return (state or "") in LIVE_STATES
