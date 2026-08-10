# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The two seams later phases wire into, and the counters the health command reads.

Phase 2 does not implement notifications (Phase 4) or retrieval invalidation (Phase 5). It
implements the **call sites**, now, while the sync engine is being written and every path
that creates, edits or deletes a message is fresh in one author's head. Retrofitting a call
site into a finished sync engine means finding all of them again, and the ones that get
missed are the ones nobody notices: an edit that never invalidates a digest, a message that
notifies twice.

--------------------------------------------------------------------------------------
:func:`notify_new_message` is a correctness assertion wearing a counter
--------------------------------------------------------------------------------------

Called **exactly once per genuinely new message** and **zero times** for echoes, replays,
reconciliations and outbound relays. That single count is the cheapest proof the sync engine
is not duplicating: a bidirectional mirror's characteristic failure is a message that comes
back and gets re-ingested, and the symptom in production is a notification storm rather than
anything visible in a log. If ``notify_new_message`` exceeds the number of rows inserted,
the loop is closed and the counter is what says so — before Phase 4 turns the same call into
a push notification on twenty phones.

--------------------------------------------------------------------------------------
:func:`mark_room_context_stale` is what stops Phase 5 quoting a deleted message
--------------------------------------------------------------------------------------

Called on every edit and every delete, with the ``seq`` span that changed. Phase 5 caches
digests and embeddings keyed on ``seq`` watermarks; without a staleness signal at the moment
of the edit, a summary can contain text the user deleted, and it will keep containing it
until the cache happens to roll. Wiring the call now costs nothing and is the only cheap
moment to get the spans right.

--------------------------------------------------------------------------------------
Counters: fixed names, Redis-backed, and a typo is refused rather than silent
--------------------------------------------------------------------------------------

:data:`COUNTER_NAMES` is closed. The health command and the sync tests both key on those
exact strings, so a misspelled name would read as a permanent zero — a metric that says
"nothing is going wrong" because nothing is being counted. :func:`bump` therefore raises on
an unknown name. Every call site passes one of the module constants below rather than a
literal, so the raise is a development-time failure at the one line that is wrong, not a
runtime risk on a healthy path.

Storage is a plain Redis integer per counter, because the reader is a different process from
the writer: relay work happens in background workers and ``chat.health`` runs under
``bench execute``. A module-level dict would show the health command its own empty state.
The keys are prefixed with the site name explicitly rather than relying on
``frappe.cache()``'s automatic prefixing — that prefixing applies to the wrapper's own
``get_value``/``set_value``/``hset`` helpers, and this module uses raw ``incrby``/``mget``
deliberately (the wrapper's hash helpers pickle their values, which would make an integer
written by ``incrby`` unreadable).

**These are observability, not accounting.** The cache Redis is cleared by a deploy, so the
numbers are "since the last release" and the health report says so. A process-local mirror
is kept as a fallback for the case where the cache is unreachable, so a counter never
raises and never blocks a relay.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

from typing import Any, Final

import frappe

# --- the fixed counter set ---------------------------------------------------
#
# Constants rather than bare strings at the call sites: `bump(COUNTER_RETRIES)` is checked
# by the interpreter, `bump("retires")` is checked by nobody until somebody reads a zero at
# 2am and believes it.

COUNTER_MESSAGES_RELAYED: Final[str] = "messages_relayed"
COUNTER_ECHOES_SUPPRESSED: Final[str] = "echoes_suppressed"
COUNTER_EVENTS_INGESTED: Final[str] = "events_ingested"
#: Messages the §4.J reconciliation sweep found that ERPNext did not have. **Deliberately not
#: ``events_ingested``.** Every row the sweep recovers is handed to
#: ``inbound.process_inbound_event``, which bumps ``events_ingested`` from ``_apply_created``
#: when — and only when — a row is genuinely inserted, so a sweep that also bumped it reported
#: ``2N`` after recovering ``N`` messages. A counter that doubles is worse than no counter at
#: all during the incident it exists for. One writer per counter: ``_apply_created`` owns
#: "a message entered ERPNext", the sweep owns "the sweep is the reason it did".
COUNTER_RECONCILE_RECOVERED: Final[str] = "reconcile_recovered"
COUNTER_DUPLICATES_REJECTED: Final[str] = "duplicates_rejected"
COUNTER_RETRIES: Final[str] = "retries"
COUNTER_DEAD_LETTERS: Final[str] = "dead_letters"
COUNTER_QUOTA_BACKOFFS: Final[str] = "quota_backoffs"
COUNTER_NOTIFY_NEW_MESSAGE: Final[str] = "notify_new_message"
COUNTER_CONTEXT_STALE: Final[str] = "context_stale"
COUNTER_ECHO_ORPHANS: Final[str] = "echo_orphans"
COUNTER_HEURISTIC_BINDS: Final[str] = "heuristic_binds"
COUNTER_REVERTS_APPLIED: Final[str] = "reverts_applied"
COUNTER_SUBSCRIPTION_RENEWALS: Final[str] = "subscription_renewals"
COUNTER_SUBSCRIPTION_FAILURES: Final[str] = "subscription_failures"
COUNTER_LATE_ARRIVALS: Final[str] = "late_arrivals"
COUNTER_SKEW_ALERTS: Final[str] = "skew_alerts"

#: The closed set, in report order. Adding a name here is a deliberate act: the health
#: command prints every entry, so a new counter appears in the operator's report the moment
#: it exists rather than the day somebody remembers to add it there too.
COUNTER_NAMES: Final[tuple[str, ...]] = (
	COUNTER_MESSAGES_RELAYED,
	COUNTER_ECHOES_SUPPRESSED,
	COUNTER_EVENTS_INGESTED,
	COUNTER_RECONCILE_RECOVERED,
	COUNTER_DUPLICATES_REJECTED,
	COUNTER_RETRIES,
	COUNTER_DEAD_LETTERS,
	COUNTER_QUOTA_BACKOFFS,
	COUNTER_NOTIFY_NEW_MESSAGE,
	COUNTER_CONTEXT_STALE,
	COUNTER_ECHO_ORPHANS,
	COUNTER_HEURISTIC_BINDS,
	COUNTER_REVERTS_APPLIED,
	COUNTER_SUBSCRIPTION_RENEWALS,
	COUNTER_SUBSCRIPTION_FAILURES,
	COUNTER_LATE_ARRIVALS,
	COUNTER_SKEW_ALERTS,
)

_COUNTER_SET: Final[frozenset[str]] = frozenset(COUNTER_NAMES)

#: Namespace for the Redis keys. Written once; a drift between the writer's key and the
#: reader's key is another silent zero.
_KEY_NAMESPACE: Final[str] = "chat:counter"

#: Process-local mirror. Only consulted when the cache is unreachable — see :func:`counters`.
_local_counts: dict[str, int] = dict.fromkeys(COUNTER_NAMES, 0)

#: Set the first time a cache write fails, so the health report can say "these numbers are
#: this process only" instead of quietly under-reporting.
_cache_degraded: bool = False


def _logger() -> Any:
	"""``frappe.logger`` behind a helper so a missing logger cannot break a seam."""
	try:
		return frappe.logger("chat")
	except Exception:  # pragma: no cover - only reachable on a half-booted frappe
		return None


def _counter_key(name: str) -> str:
	"""``<site>|chat:counter:<name>``.

	The site prefix is explicit. ``frappe.cache()`` prefixes keys for the helpers that call
	its ``make_key`` (``get_value``, ``set_value``, ``hset`` …), and this module deliberately
	uses the raw redis-py ``incrby``/``mget``, which do not. On a multi-site bench the
	unprefixed form would have two sites incrementing one counter.
	"""
	site = getattr(frappe.local, "site", None) or "unknown-site"
	return f"{site}|{_KEY_NAMESPACE}:{name}"


def bump(name: str, by: int = 1) -> None:
	"""Increment one counter. Raises ``ValueError`` on a name outside :data:`COUNTER_NAMES`.

	The raise is the point. A counter that silently accepts any string is a counter that
	reports zero forever for the one metric somebody misspelled, and "zero dead letters" is
	the most reassuring wrong answer this system can give. Every call site passes a module
	constant, so the only way to reach the raise is to invent a name — which is a decision
	that belongs in :data:`COUNTER_NAMES`, not in a call site.

	A cache failure is swallowed: observability must never be able to fail a relay.
	"""
	if name not in _COUNTER_SET:
		raise ValueError(
			f"unknown chat counter {name!r}. The set is closed and shared with "
			"erpnext_enhancements.chat.health, which prints exactly these names: "
			f"{list(COUNTER_NAMES)}. If you genuinely need a new one, add it to COUNTER_NAMES "
			"(and a constant beside it) rather than passing a literal — an unregistered name "
			"reads as a permanent zero in the health report."
		)

	try:
		step = int(by)
	except (TypeError, ValueError):
		step = 1
	if step == 0:
		return

	_local_counts[name] = _local_counts.get(name, 0) + step

	global _cache_degraded
	try:
		frappe.cache().incrby(_counter_key(name), step)
	except Exception:
		# Deliberately silent and deliberately broad. This runs inside the relay worker's
		# hot path; a cache blip must not turn into a failed message. The degraded flag is
		# what the health report surfaces instead.
		_cache_degraded = True


def counters() -> dict[str, int]:
	"""Every counter, always all of :data:`COUNTER_NAMES`, missing values as ``0``.

	Redis is authoritative when it can be read, because it is the only view that spans the
	background workers doing the actual relay work. The process-local mirror is the fallback
	for a cache outage and is honestly worse — it sees only this process — which is why
	:func:`counters_are_degraded` exists and the health report prints it.
	"""
	keys = [_counter_key(name) for name in COUNTER_NAMES]
	# VERIFY: (unexecuted — this environment has no bench) that v16's `RedisWrapper` does not
	# override `mget`. `incrby` is confirmed available as a plain redis-py method; `mget` is
	# assumed to be one too. If the wrapper ever wraps it with `make_key`, the keys get
	# double-prefixed and every counter reads 0 — which is why the read is one call inside a
	# try/except rather than sixteen, and why the health report prints "degraded" instead of
	# a confident row of zeroes. Check with:
	#     bench --site <site> console
	#     >>> frappe.cache().incrby("probe", 1); frappe.cache().mget(["probe"])
	global _cache_degraded
	try:
		raw = frappe.cache().mget(keys)
	except Exception:
		_cache_degraded = True
		return dict(_local_counts)

	out: dict[str, int] = {}
	for name, value in zip(COUNTER_NAMES, raw or [], strict=False):
		out[name] = _coerce_count(value)
	for name in COUNTER_NAMES:
		out.setdefault(name, 0)

	# The one failure the try/except above cannot see: a read that *succeeds* and returns
	# nothing, because the key it looked under is not the key `incrby` wrote. That reads as
	# a clean row of zeroes, which is the most reassuring wrong answer this module can give.
	# This process knows better whenever it has counted anything itself, so disagreement is
	# treated as evidence the read path is broken rather than as a fact about the system.
	if any(_local_counts.values()) and not any(out.values()):
		_cache_degraded = True
		return dict(_local_counts)

	return out


def counters_are_degraded() -> bool:
	"""True once a counter read or write has failed, or been caught disagreeing with this
	process's own tally. Read by the health report, which prints it rather than presenting
	possibly-undercounted numbers as fact."""
	return _cache_degraded


def _coerce_count(value: Any) -> int:
	"""Redis hands back ``bytes`` (or ``None``); anything unparseable counts as zero."""
	if value is None:
		return 0
	if isinstance(value, bytes | bytearray):
		value = bytes(value).decode("utf-8", "replace")
	try:
		return int(value)
	except (TypeError, ValueError):
		return 0


def reset_counters() -> None:
	"""Zero everything. **Tests only.**

	Not exposed as an endpoint and not called by any scheduled job: resetting counters mid-run
	is how a duplicate-detection metric gets explained away rather than investigated.
	"""
	global _cache_degraded
	for name in COUNTER_NAMES:
		_local_counts[name] = 0
	_cache_degraded = False
	try:
		cache = frappe.cache()
		for name in COUNTER_NAMES:
			cache.delete(_counter_key(name))
	except Exception:
		_cache_degraded = True


# --- the seams ---------------------------------------------------------------


def notify_new_message(message: Any) -> None:
	"""Phase 4's notification entry point. Here: count it and debug-log the identifiers.

	**Exactly once per genuinely new message; zero times for echoes, replays,
	reconciliations and outbound relays.** An echo is our own message arriving back from
	Google — the row already exists, nobody is newly informed of anything, and notifying on
	it is how a mirror turns one message into a notification loop. Same for the
	reconciliation sweep, which re-reads rooms that are already correct.

	Accepts a ``Chat Message`` name or the document itself, because the write path has the
	document in hand and the inbound path frequently has only a name. Field reads are
	``getattr(..., None) or ""`` per house rule: ``doc_events`` fire during ERPNext's own
	test bootstrap, before this app's custom fields exist.

	**Never raises.** It is called from ``after_insert``; an exception here would roll back
	the message the seam exists to report on, which is a strictly worse outcome than a
	missing debug line.
	"""
	try:
		if isinstance(message, str):
			name, room = message.strip(), ""
		else:
			name = str(getattr(message, "name", None) or "")
			room = str(getattr(message, "room", None) or "")

		bump(COUNTER_NOTIFY_NEW_MESSAGE)

		log = _logger()
		if log is not None:
			# Identifiers only. Never the body, per the module-wide logging rule. Lazy
			# %-args rather than an f-string: the format only runs if debug is enabled,
			# and this is on the insert path of the busiest table in the feature.
			log.debug(
				"chat seam notify_new_message message=%s room=%s",
				name or "<unnamed>",
				room or "<unknown>",
			)
	except Exception:
		# See the docstring: a seam may not be able to abort the transaction it observes.
		pass


def mark_room_context_stale(room: str, from_seq: int, to_seq: int) -> None:
	"""Phase 5's digest/embedding invalidation. Here: count it and debug-log the span.

	Called on **every** edit and **every** delete, with the ``seq`` span that changed —
	``seq`` and never a timestamp, because ``seq`` is the watermark Phase 5's cache keys are
	built from and is immutable once assigned.

	A reversed or malformed span is normalised and warned about rather than rejected. That
	is a deliberate trade: this is called from the delete path, and raising would abort the
	delete, leaving the message live *and* the digest stale — the exact pair of outcomes the
	seam exists to prevent. A too-wide invalidation costs one recomputation; a missing one
	serves a user text they deleted.

	**Never raises**, for the same reason.
	"""
	try:
		room_name = str(room or "").strip()
		low = _coerce_seq(from_seq)
		high = _coerce_seq(to_seq)
		if low > high:
			low, high = high, low
			log = _logger()
			if log is not None:
				log.warning(
					"chat seam mark_room_context_stale received a reversed span for room %s "
					"(%s..%s); widening rather than dropping it",
					room_name or "<unknown>",
					to_seq,
					from_seq,
				)

		bump(COUNTER_CONTEXT_STALE)

		log = _logger()
		if log is not None:
			log.debug(
				"chat seam mark_room_context_stale room=%s seq=%s..%s",
				room_name or "<unknown>",
				low,
				high,
			)
	except Exception:
		pass


def _coerce_seq(value: Any) -> int:
	"""``seq`` as an int, with anything unparseable read as ``0``.

	``0`` widens the span towards the start of the room, which is the safe direction: a
	digest recomputed too eagerly is a cost, one skipped is a correctness bug.
	"""
	try:
		return int(value)
	except (TypeError, ValueError):
		return 0
