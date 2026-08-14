# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Every decision the alert path makes, as pure functions. **No Frappe, no imports.**

Phase 6 §4.H. The companion module :mod:`alerts` does the I/O; everything that constitutes
a *judgement* lives here, so it can be tested without a bench — the same split
:mod:`chat.notifications.policy` and :mod:`chat.doctype.chat_settings.chat_settings_rules`
already use in this package.

--------------------------------------------------------------------------------------
What was actually wrong, since "add alerting" understates it
--------------------------------------------------------------------------------------

The chat module already alerts. It does it **five different ways**, each with its own
private helper, and all five end in the same place:

* ``sync/inbound.py::_alarm`` — a counter, a log line and an ``Error Log`` row
* ``indexing/digest.py::_note`` — an ``Error Log`` row
* ``sync/membership.py::_alert_external`` — a comment on the room
* ``audit.py::verify_all_chains`` — ``frappe.log_error`` inline, with a docstring
  admitting the channel is *"necessary and not sufficient"*
* ``governance/role_grants.py`` — ``frappe.log_error`` inline

None of them deduplicate, and that is not a cosmetic complaint. ``check_digest_staleness``
is on an hourly cron: while the summariser is stopped it writes **one Error Log row an
hour, forever**. A weekend of that is 48 rows describing one incident, which is the exact
shape of the failure this module exists to prevent — not "no alerts" but "so many that the
alert stops carrying information".

--------------------------------------------------------------------------------------
The four rules, and why each is here rather than at the call site
--------------------------------------------------------------------------------------

**1. The deduplication key names the problem, never the measurement.**
:func:`dedup_key` is built from subsystem, kind and the object it is about. It must not
contain a count, a timestamp, a duration, or any measured value — a key containing the
number that changes is a key that never matches its own predecessor, so the alert
deduplicates nothing while looking like it does. That failure is invisible until somebody
counts the rows, which is why the rule is a function with a test rather than a convention.

**2. Repeats update the open alert, and re-notify on a doubling schedule.**
:func:`notifies_at` — occurrences 1, 2, 4, 8, 16… Total silence after the first occurrence
is the opposite error to twelve pages: an incident still firing eight hours later is
different news from one that just started, and an alert path that cannot say so leaves the
reader unable to distinguish "handled" from "ignored".

**3. Resolved is final; a recurrence opens a new incident.**
:func:`transition` refuses ``Resolved -> Open``. Reopening would overwrite the resolution
timestamp, and losing the record that the problem was fixed once destroys the only evidence
that it is *flapping* rather than continuing.

**4. An alert cannot be delivered by the thing it is about.**
:func:`transports_for` is the whole reason the ops-space channel is not simply "always".
A message to an operations space is a ``Chat Message``, relayed to Google by the relay
worker — so an alert *about the relay* joins the queue it is reporting on and arrives when
the incident ends. Subsystems that carry their own delivery are email-only, by name, in
:data:`SELF_DELIVERING`. This is the "email fallback for the cases where the transport
itself is what failed" in the task, made structural rather than a runtime guess.
"""

from __future__ import annotations

from typing import Final

# --- severities ----------------------------------------------------------------

SEVERITY_WARNING: Final[str] = "Warning"
SEVERITY_CRITICAL: Final[str] = "Critical"
SEVERITIES: Final[tuple[str, ...]] = (SEVERITY_WARNING, SEVERITY_CRITICAL)

# --- lifecycle -----------------------------------------------------------------

STATE_OPEN: Final[str] = "Open"
STATE_ACKNOWLEDGED: Final[str] = "Acknowledged"
STATE_RESOLVED: Final[str] = "Resolved"
STATES: Final[tuple[str, ...]] = (STATE_OPEN, STATE_ACKNOWLEDGED, STATE_RESOLVED)

#: States in which a new occurrence updates the existing row instead of opening a new one.
LIVE_STATES: Final[frozenset[str]] = frozenset({STATE_OPEN, STATE_ACKNOWLEDGED})

EVENT_RECUR: Final[str] = "recur"
EVENT_ACKNOWLEDGE: Final[str] = "acknowledge"
EVENT_RESOLVE: Final[str] = "resolve"

#: ``(state, event) -> state``. Absent means "refused", not "no change" — see
#: :func:`transition`, which returns ``None`` and makes the caller decide.
_TRANSITIONS: Final[dict[tuple[str, str], str]] = {
	(STATE_OPEN, EVENT_RECUR): STATE_OPEN,
	(STATE_OPEN, EVENT_ACKNOWLEDGE): STATE_ACKNOWLEDGED,
	(STATE_OPEN, EVENT_RESOLVE): STATE_RESOLVED,
	# A recurrence does NOT un-acknowledge. Somebody said "I am on it"; the problem
	# continuing is what they are on. Bouncing it back to Open would page them for the
	# thing they already answered.
	(STATE_ACKNOWLEDGED, EVENT_RECUR): STATE_ACKNOWLEDGED,
	(STATE_ACKNOWLEDGED, EVENT_RESOLVE): STATE_RESOLVED,
	# Resolved is terminal. Deliberately no (Resolved, recur) row — see rule 3.
}

# --- transports ----------------------------------------------------------------

TRANSPORT_LOG: Final[str] = "log"
TRANSPORT_RECORD: Final[str] = "record"
TRANSPORT_SPACE: Final[str] = "space"
TRANSPORT_EMAIL: Final[str] = "email"

#: Subsystems whose failure would swallow a message sent to the operations space.
#:
#: The ops-space channel writes a ``Chat Message`` and lets the relay carry it to Google.
#: Every subsystem named here is on that path, so an alert about one of them would be
#: queued behind the incident and delivered after it ends. They go to email, which shares
#: no machinery with chat delivery.
SELF_DELIVERING: Final[frozenset[str]] = frozenset(
	{
		"relay",
		"inbound",
		"gchat",
		"quota",
		"subscriptions",
		"notifications",
	}
)

# --- subsystems ----------------------------------------------------------------
#
# Free strings would drift into "relay", "Relay" and "relay_worker" inside a month, and the
# dedup key is built from this value — three spellings are three incidents. Enumerated, and
# `dedup_key` refuses anything not listed.

SUBSYSTEMS: Final[frozenset[str]] = SELF_DELIVERING | frozenset(
	{
		"audit",
		"governance",
		"retention",
		"indexing",
		"drift",
		"export",
		"alerting",
	}
)

#: The key the storm alert uses. Exempt from the rate limit, because a rate-limited
#: rate-limit warning is one nobody ever sees — the suppression is the news.
STORM_KEY: Final[str] = "alerting::storm"

#: Fallback when ``Chat Settings.alert_rate_limit_per_hour`` reads 0, which it does on
#: every existing site until the backfill patch runs (a `default` on a new field of a
#: Single never reaches the row that already exists). Zero must not mean "no alerts".
DEFAULT_RATE_LIMIT_PER_HOUR: Final[int] = 20

VERDICT_ALLOW: Final[str] = "allow"
VERDICT_STORM: Final[str] = "storm"
VERDICT_SUPPRESS: Final[str] = "suppress"


def dedup_key(subsystem: str, kind: str, scope: str = "") -> str:
	"""``subsystem::kind::scope`` — the identity of a *problem*, not of an occurrence.

	``scope`` is the object the problem is about: a room, a chain name, a space. Two rooms
	with stuck queues are two incidents; one room reporting hourly is one.

	**Nothing measured belongs in here.** A key carrying the queue depth changes every time
	the depth does, so every occurrence looks new and the row count grows exactly as fast as
	it would with no deduplication at all — while the code, the schema and the dashboard all
	claim the alerts are deduplicated. Callers pass measurements as ``detail``.
	"""
	sub = (subsystem or "").strip().lower()
	if sub not in SUBSYSTEMS:
		raise ValueError(
			f"unknown alert subsystem {subsystem!r}; add it to alert_rules.SUBSYSTEMS "
			"deliberately, and decide there whether it is self-delivering"
		)
	slug = _slug(kind)
	if not slug:
		raise ValueError("an alert needs a kind")
	scope_slug = _slug(scope)
	return f"{sub}::{slug}::{scope_slug}" if scope_slug else f"{sub}::{slug}"


def _slug(value: str | None) -> str:
	"""Lowercase, ``[a-z0-9_-]`` only, collapsed. Deterministic across occurrences.

	Room names are hashes and space names contain ``/``; both must survive into a key that
	is stable and readable at 2am.
	"""
	text = (value or "").strip().lower()
	out: list[str] = []
	for ch in text:
		if ch.isalnum() or ch in "-_":
			out.append(ch)
		elif out and out[-1] != "-":
			out.append("-")
	return "".join(out).strip("-")


def notifies_at(occurrence: int) -> bool:
	"""Notify on occurrences 1, 2, 4, 8, 16, 32… — a doubling schedule.

	Thirty-two occurrences produce six notifications instead of thirty-two, and the gaps
	widen as the incident ages rather than stopping, so a long-running problem keeps saying
	so without ever accelerating.
	"""
	if occurrence < 1:
		return False
	return occurrence & (occurrence - 1) == 0


def transition(state: str, event: str) -> str | None:
	"""The next state, or ``None`` if the transition is refused.

	``None`` rather than "unchanged" on purpose: ``Resolved`` + ``recur`` is the case that
	matters, and the caller must open a new incident rather than quietly discard the event.
	"""
	return _TRANSITIONS.get((state or "", event or ""))


def is_live(state: str) -> bool:
	return (state or "") in LIVE_STATES


def transports_for(
	subsystem: str,
	severity: str,
	*,
	space_configured: bool,
	email_configured: bool,
) -> tuple[str, ...]:
	"""Which channels this alert may use. ``log`` and ``record`` are unconditional.

	The ops space is skipped entirely for :data:`SELF_DELIVERING` subsystems — see rule 4.
	Email carries every alert from those subsystems, and from anywhere else carries only the
	criticals, so routine warnings do not train the reader to filter the address.
	"""
	channels = [TRANSPORT_LOG, TRANSPORT_RECORD]
	sub = (subsystem or "").strip().lower()
	self_delivering = sub in SELF_DELIVERING

	if space_configured and not self_delivering:
		channels.append(TRANSPORT_SPACE)
	if email_configured and (self_delivering or severity == SEVERITY_CRITICAL):
		channels.append(TRANSPORT_EMAIL)
	return tuple(channels)


def undeliverable_reason(
	subsystem: str,
	*,
	space_configured: bool,
	email_configured: bool,
) -> str:
	"""Why this alert will reach nobody but the log, or ``""`` if it will reach somebody.

	A configuration gap that silences alerting is the one gap that cannot report itself, so
	it is surfaced on the alert row and printed by the health report rather than inferred
	from an absence.
	"""
	sub = (subsystem or "").strip().lower()
	if sub in SELF_DELIVERING:
		if email_configured:
			return ""
		return (
			f"{sub} alerts cannot use the operations space (the space is delivered by the "
			"subsystem being reported on), and no alert email recipient is configured"
		)
	if space_configured or email_configured:
		return ""
	return "neither an operations room nor an alert email recipient is configured"


def rate_limit_verdict(recent: int, limit: int) -> str:
	"""``allow`` / ``storm`` / ``suppress`` for a *new* incident in the last hour.

	The limit counts newly-opened incidents, never occurrences of one — an incident that
	fires every minute updates a single row and consumes exactly one of the budget. So
	reaching the limit means many *distinct* things broke at once, which is either a real
	outage or a broken classifier, and both want one loud row rather than two hundred.

	``storm`` is returned exactly once, at the crossing, and the storm alert itself is keyed
	:data:`STORM_KEY` and exempt.
	"""
	ceiling = int(limit) if int(limit or 0) > 0 else DEFAULT_RATE_LIMIT_PER_HOUR
	if recent < ceiling:
		return VERDICT_ALLOW
	if recent == ceiling:
		return VERDICT_STORM
	return VERDICT_SUPPRESS


def summary_line(severity: str, subsystem: str, kind: str, occurrence: int) -> str:
	"""One line, fixed shape, safe to put in a subject or a log file.

	Never carries ``detail``: detail is caller-supplied and this string ends up in an email
	subject and a chat message.
	"""
	flag = "CRITICAL" if severity == SEVERITY_CRITICAL else "WARNING"
	repeat = f" (x{occurrence})" if occurrence > 1 else ""
	return f"[{flag}] chat {(subsystem or '?').lower()}: {kind}{repeat}"
