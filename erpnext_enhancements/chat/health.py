# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""``bench execute``-able health report for the Google Chat sync engine.

	bench --site <site> execute erpnext_enhancements.chat.health.report
	bench --site <site> execute erpnext_enhancements.chat.health.report --kwargs "{'room': 'abc123'}"

**Written for somebody at 2am who did not write the sync engine.** That constraint decides
most of what is below. Every number is named, carries its unit, and is *judged* — the report
says ``[ALARM] the relay worker is not draining`` rather than printing a timestamp and
leaving the reader to work out whether 4,215 seconds is bad. A dashboard that requires you
to already know the thresholds is a dashboard you cannot use at the moment you need it.

**It never raises.** Every query is guarded individually and the whole render is wrapped, so
a half-configured site — no ``Chat Settings``, a DocType the deploy did not install, a
column a Phase 2 patch has not added yet — still produces a report, with the missing pieces
named. A health command that crashes tells the operator nothing except that something is
broken, which they already knew. Problems collecting are printed at the bottom rather than
swallowed, so an incomplete report cannot be mistaken for a clean one.

**It reads counts and identifiers, never message text.** ``chat/permissions.py``'s standing
rule — no ``frappe.get_all`` on a chat table without a membership filter — exists to stop
content leaking past the membership model. This module reads no content: aggregates, states,
timestamps and room names only. It is also ``bench``-only: deliberately **not**
``@frappe.whitelist()``, deliberately not registered in ``hooks.py``, so there is no HTTP
surface to scope in the first place. Raw ``frappe.db.sql`` is used throughout rather than
``frappe.get_all`` so that the choice is visible at every call site instead of looking like
somebody forgot the filter.

--------------------------------------------------------------------------------------
What the numbers mean, since the thresholds encode design decisions
--------------------------------------------------------------------------------------

* **A room drains at one write per second** (ADR §G.8 Rule 4 — Google's per-space write
  quota, shared with ``media.upload``, and shared with humans typing in the native client).
  So a room that accumulated 600 messages during an outage takes 600 seconds to drain **and
  that is the system working correctly**. This is why the report separates "oldest Pending
  job" from "oldest Pending job that is already **due**": a job deferred by ordering or
  backoff is healthy, and only a due job that is still waiting means nobody is draining.
* **The in-flight claim is the relay job row.** In-flight writes for a room are
  ``Chat Relay Job`` rows with ``status = 'In Progress'`` and ``lease_expires_at > now``.
  A row past its lease is a **crashed worker**, not a slow one, and it will block its room's
  FIFO until the sweeper reclaims it. Any non-zero count here is worth a look.
* **Subscriptions expire on Google's schedule, not ours.** ``expire_time`` is read off the
  create/patch response; the published TTLs are ceilings and nothing guarantees the server
  granted one. A subscription that lapses stops inbound entirely and there is no error
  anywhere — messages simply stop arriving.
* **Counters reset when the cache Redis is cleared**, which a deploy does. They are
  observability, not accounting, and the report says so rather than letting somebody read
  "0 dead letters" as a fact about all of history.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

import traceback
from typing import Any, Final

import frappe

from erpnext_enhancements.chat import seams

# --- thresholds ---------------------------------------------------------------
#
# Each of these is a design fact, not a preference. The comment is the justification a
# reader needs before they are allowed to change the number.

#: A *due* Pending job older than this means nothing is draining the queue. Ten minutes is
#: chosen against the 1-write/second drain rate: a 600-message backlog is 600 seconds of
#: legitimate work, so anything under it is indistinguishable from a busy room.
DUE_PENDING_WARN_SECONDS: Final[int] = 600

#: An hour of undrained due work is not a backlog. Either the worker is dead, the kill
#: switch is on, or the queue Redis was flushed by a deploy and the sweeper is not running.
DUE_PENDING_ALARM_SECONDS: Final[int] = 3600

#: One expired lease is a worker that died mid-write; several is a systemic problem
#: (a restart loop, an OOM, a deploy that killed workers mid-job).
EXPIRED_LEASE_ALARM_COUNT: Final[int] = 5

#: Fallback for ``Chat Settings.subscription_renew_before_seconds`` when settings cannot be
#: read. Matches the shipped default (24 hours).
DEFAULT_RENEW_BEFORE_SECONDS: Final[int] = 86_400

#: Inbound events sitting in ``Received`` past this are not being consumed.
INBOUND_STUCK_WARN_SECONDS: Final[int] = 600

#: Counters whose mere non-zero-ness is news, with the reason. Everything else is volume.
COUNTER_ALARM_IF_NONZERO: Final[dict[str, str]] = {
	"dead_letters": "a relay job gave up permanently; the two sides are now divergent",
	"echo_orphans": "a client- id we minted has no local row; ADR G.3.2 says alarm, do not guess",
	"subscription_failures": "inbound stops silently when a subscription cannot be renewed",
}
COUNTER_WARN_IF_NONZERO: Final[dict[str, str]] = {
	"heuristic_binds": "the bounded echo fallback fired; it ships disabled, so somebody enabled it",
	"skew_alerts": "Google's clock and this site's disagree by more than the configured threshold",
	"quota_backoffs": "the per-space write bucket is saturating; relay latency is rising",
	"reverts_applied": "ERPNext overwrote an inbound edit; check the revert cap is holding",
}

#: Counter display grouping. Purely presentational — anything not listed still prints, under
#: "other", so a counter added to ``seams.COUNTER_NAMES`` can never fall out of this report.
_COUNTER_GROUPS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
	("outbound relay", ("messages_relayed", "retries", "quota_backoffs", "dead_letters")),
	(
		"inbound ingest",
		(
			"events_ingested",
			"duplicates_rejected",
			"echoes_suppressed",
			"echo_orphans",
			"heuristic_binds",
			"late_arrivals",
			"skew_alerts",
		),
	),
	("convergence", ("reverts_applied", "context_stale", "notify_new_message")),
	("subscriptions", ("subscription_renewals", "subscription_failures")),
)

#: Flags, fixed width and ASCII. Fixed width so the right-hand column stays a column when
#: the report is pasted into a monospace chat window; ASCII because this is printed to
#: whatever terminal the operator has, and a UnicodeEncodeError from a health command is a
#: particularly stupid way to learn nothing.
_OK: Final[str] = "[ ok ]"
_WARN: Final[str] = "[warn]"
_ALARM: Final[str] = "[ALARM]"

_WIDTH: Final[int] = 96
_LABEL_WIDTH: Final[int] = 38

_RELAY_JOB: Final[str] = "Chat Relay Job"
_MESSAGE: Final[str] = "Chat Message"
_ROOM: Final[str] = "Chat Room"
_SUBSCRIPTION: Final[str] = "Chat Event Subscription"
_INBOUND_EVENT: Final[str] = "Chat Inbound Event"


# --- tiny formatting helpers ---------------------------------------------------


def _rule(char: str = "=") -> str:
	return char * _WIDTH


def _line(label: str, value: Any, flag: str = "", note: str = "") -> str:
	"""``  label ......... value   [flag] note`` — one row of the report.

	The dotted leader is not decoration: these reports get pasted into chat and read on a
	phone, and a leader is what keeps a label attached to its number after the terminal
	rewraps.
	"""
	text = f"  {label} "
	pad = max(2, _LABEL_WIDTH - len(label))
	rendered = f"{text}{'.' * pad} {value}"
	if flag:
		rendered = f"{rendered:<70} {flag}"
	if note:
		rendered = f"{rendered} {note}"
	return rendered.rstrip()


def _humanise_seconds(seconds: float | None) -> str:
	"""``4215 s (1 h 10 m)``. Both, always — the raw number to compare, the human one to read."""
	if seconds is None:
		return "unknown"
	total = int(round(float(seconds)))
	sign = "-" if total < 0 else ""
	total = abs(total)
	if total < 90:
		return f"{sign}{total} s"
	minutes, secs = divmod(total, 60)
	hours, minutes = divmod(minutes, 60)
	days, hours = divmod(hours, 24)
	parts: list[str] = []
	if days:
		parts.append(f"{days} d")
	if hours:
		parts.append(f"{hours} h")
	if minutes and not days:
		parts.append(f"{minutes} m")
	if not parts:
		parts.append(f"{secs} s")
	return f"{sign}{total} s ({' '.join(parts)})"


def _as_int(value: Any, default: int) -> int:
	try:
		return int(value)
	except (TypeError, ValueError):
		return default


def _age_seconds(value: Any, now: Any) -> float | None:
	"""Seconds between ``value`` and ``now``, or ``None`` if the value will not parse."""
	if not value:
		return None
	try:
		from frappe.utils import get_datetime

		return (now - get_datetime(value)).total_seconds()
	except Exception:
		return None


def _onoff(value: Any) -> str:
	return "on" if _as_int(value, 0) else "off"


def _yesno(value: Any) -> str:
	return "yes" if _as_int(value, 0) else "no"


# --- guarded data access -------------------------------------------------------


def _sql(errors: list[str], what: str, query: str, values: Any = None) -> list[dict[str, Any]]:
	"""Run one aggregate query. A failure becomes a named entry in ``errors``, never a raise.

	``as_dict=True`` throughout so the render code reads by column name — a positional tuple
	is how a column added to the middle of a ``select`` silently shifts every reading below it.
	"""
	try:
		return frappe.db.sql(query, values or {}, as_dict=True) or []
	except Exception as exc:
		errors.append(f"{what}: {type(exc).__name__}: {exc}")
		return []


def _table_ready(errors: list[str], doctype: str) -> bool:
	"""Is the table actually installed? A green deploy can install nothing (Phase 1, twice)."""
	try:
		if frappe.db.table_exists(doctype):
			return True
		errors.append(f"{doctype}: table is not installed on this site, so its section is blank")
		return False
	except Exception as exc:
		errors.append(f"{doctype}: could not check whether the table exists: {type(exc).__name__}: {exc}")
		return False


def _has_column(doctype: str, column: str) -> bool:
	try:
		return bool(frappe.db.has_column(doctype, column))
	except Exception:
		return False


def _setting(settings: Any, field: str, default: Any) -> Any:
	"""Defensive settings read — ``Chat Settings`` may be absent or mid-migration."""
	if settings is None:
		return default
	value = getattr(settings, field, None)
	return default if value is None else value


# --- collection ----------------------------------------------------------------


def collect(room: str | None = None, max_rooms: int = 20) -> dict[str, Any]:
	"""Gather everything the report prints, as a plain dict. Never raises.

	Split from rendering so a future Phase 6 dashboard can reuse the numbers without
	scraping text, and so the render code has no I/O in it and stays readable.
	"""
	errors: list[str] = []
	data: dict[str, Any] = {"errors": errors}

	try:
		from frappe.utils import now_datetime

		now = now_datetime()
	except Exception as exc:  # pragma: no cover - a frappe this broken has bigger problems
		errors.append(f"clock: {type(exc).__name__}: {exc}")
		return data

	data["now"] = now
	data["site"] = getattr(frappe.local, "site", None) or "unknown-site"

	try:
		settings = frappe.get_cached_doc("Chat Settings")
	except Exception as exc:
		settings = None
		errors.append(f"Chat Settings: unreadable ({type(exc).__name__}: {exc}); thresholds use defaults")
	data["settings"] = settings

	room_filter = (room or "").strip() or None
	data["room_filter"] = room_filter
	max_rooms = max(1, _as_int(max_rooms, 20))

	_collect_relay(data, errors, now=now, room_filter=room_filter)
	_collect_rooms(data, errors, now=now, room_filter=room_filter, max_rooms=max_rooms)
	_collect_subscriptions(data, errors, now=now, settings=settings)
	_collect_inbound(data, errors, now=now)

	try:
		data["counters"] = seams.counters()
		data["counters_degraded"] = seams.counters_are_degraded()
	except Exception as exc:
		data["counters"] = {}
		data["counters_degraded"] = True
		errors.append(f"counters: {type(exc).__name__}: {exc}")

	return data


def _collect_relay(data: dict[str, Any], errors: list[str], *, now: Any, room_filter: str | None) -> None:
	"""Queue depth, the two oldest-Pending ages, and the crashed-worker detector."""
	data["relay_ready"] = _table_ready(errors, _RELAY_JOB)
	if not data["relay_ready"]:
		return

	where_room = " and room = %(room)s" if room_filter else ""
	params = {"now": now, "room": room_filter}

	data["relay_by_status"] = {
		row["status"]: _as_int(row["n"], 0)
		for row in _sql(
			errors,
			"relay job status counts",
			f"select status, count(*) as n from `tab{_RELAY_JOB}` where 1=1{where_room} group by status",
			params,
		)
	}

	oldest = _sql(
		errors,
		"oldest Pending relay job",
		f"""select name, room, job_seq, operation, attempts, creation, available_at
			from `tab{_RELAY_JOB}`
			where status = 'Pending'{where_room}
			order by creation asc limit 1""",
		params,
	)
	data["oldest_pending"] = oldest[0] if oldest else None
	data["oldest_pending_age"] = _age_seconds(oldest[0]["creation"], now) if oldest else None

	# The actionable one. A Pending job whose available_at is in the future is *deferred* —
	# by CREATE-BEFORE-EDIT ordering or by retry backoff — and waiting is what it is supposed
	# to be doing. Only a job that is already due and still sitting means nobody is draining.
	due = _sql(
		errors,
		"oldest due Pending relay job",
		f"""select name, room, job_seq, operation, attempts, creation, available_at
			from `tab{_RELAY_JOB}`
			where status = 'Pending' and (available_at is null or available_at <= %(now)s){where_room}
			order by creation asc limit 1""",
		params,
	)
	data["oldest_due_pending"] = due[0] if due else None
	data["oldest_due_pending_age"] = _age_seconds(due[0]["creation"], now) if due else None

	due_count = _sql(
		errors,
		"due Pending relay job count",
		f"""select count(*) as n from `tab{_RELAY_JOB}`
			where status = 'Pending' and (available_at is null or available_at <= %(now)s){where_room}""",
		params,
	)
	data["due_pending_count"] = _as_int(due_count[0]["n"], 0) if due_count else 0

	# lease_expires_at is a Phase 2 field. On a site whose schema patch has not run, the
	# crashed-worker detector is simply blind, and saying so is much better than printing 0.
	data["lease_column"] = _has_column(_RELAY_JOB, "lease_expires_at")
	if not data["lease_column"]:
		return

	expired = _sql(
		errors,
		"expired in-flight leases",
		f"""select name, room, job_seq, operation, impersonate_user, lease_expires_at
			from `tab{_RELAY_JOB}`
			where status = 'In Progress' and lease_expires_at is not null
			  and lease_expires_at < %(now)s{where_room}
			order by lease_expires_at asc limit 10""",
		params,
	)
	data["expired_leases"] = expired
	expired_count = _sql(
		errors,
		"expired in-flight lease count",
		f"""select count(*) as n from `tab{_RELAY_JOB}`
			where status = 'In Progress' and lease_expires_at is not null
			  and lease_expires_at < %(now)s{where_room}""",
		params,
	)
	data["expired_lease_count"] = _as_int(expired_count[0]["n"], 0) if expired_count else 0


def _collect_rooms(
	data: dict[str, Any], errors: list[str], *, now: Any, room_filter: str | None, max_rooms: int
) -> None:
	"""Per-room sync state: relay backlog, stuck messages, and the estimated drain time.

	Two aggregates rather than a join, because they answer different questions and one of
	them can fail without taking the other with it. The ``Chat Message`` aggregate is
	filtered to the two states that mean something is wrong, so on a healthy site it touches
	very little — and if it is slow, that slowness is itself information about the index.
	"""
	rooms: dict[str, dict[str, Any]] = {}

	if data.get("relay_ready"):
		where_room = " and room = %(room)s" if room_filter else ""
		for row in _sql(
			errors,
			"per-room relay backlog",
			f"""select room, status, count(*) as n, min(creation) as oldest
				from `tab{_RELAY_JOB}`
				where status in ('Pending', 'In Progress', 'Failed'){where_room}
				group by room, status""",
			{"room": room_filter},
		):
			name = row.get("room") or "<no room>"
			entry = rooms.setdefault(name, {"jobs": {}, "messages": {}, "oldest_job": None})
			entry["jobs"][row.get("status")] = _as_int(row.get("n"), 0)
			oldest = row.get("oldest")
			if oldest and (entry["oldest_job"] is None or oldest < entry["oldest_job"]):
				entry["oldest_job"] = oldest

	if _table_ready(errors, _MESSAGE):
		where_room = " and room = %(room)s" if room_filter else ""
		for row in _sql(
			errors,
			"per-room stuck messages",
			f"""select room, sync_state, count(*) as n
				from `tab{_MESSAGE}`
				where sync_state in ('Pending', 'Failed') and ifnull(is_deleted, 0) = 0{where_room}
				group by room, sync_state""",
			{"room": room_filter},
		):
			name = row.get("room") or "<no room>"
			entry = rooms.setdefault(name, {"jobs": {}, "messages": {}, "oldest_job": None})
			entry["messages"][row.get("sync_state")] = _as_int(row.get("n"), 0)

	for name, entry in rooms.items():
		entry["room"] = name
		entry["pending_jobs"] = _as_int(entry["jobs"].get("Pending"), 0)
		entry["in_progress_jobs"] = _as_int(entry["jobs"].get("In Progress"), 0)
		entry["failed_jobs"] = _as_int(entry["jobs"].get("Failed"), 0)
		entry["pending_messages"] = _as_int(entry["messages"].get("Pending"), 0)
		entry["failed_messages"] = _as_int(entry["messages"].get("Failed"), 0)
		entry["oldest_job_age"] = _age_seconds(entry["oldest_job"], now)
		# One write per second per space, strict FIFO. This is the number to put in front of
		# a human: it converts a queue depth into "how long until this room is caught up".
		entry["drain_seconds"] = entry["pending_jobs"]

	ordered = sorted(
		rooms.values(),
		key=lambda e: (e["failed_jobs"], e["pending_jobs"], e["failed_messages"]),
		reverse=True,
	)
	shown = ordered[:max_rooms]
	data["rooms"] = shown
	data["rooms_total"] = len(ordered)

	if not shown or not _table_ready(errors, _ROOM):
		data["room_meta"] = {}
		return

	names = [entry["room"] for entry in shown if entry["room"] != "<no room>"]
	if not names:
		data["room_meta"] = {}
		return
	placeholders = ", ".join(["%s"] * len(names))
	meta = _sql(
		errors,
		"room metadata",
		f"""select name, title, room_type, provisioning_state, gchat_space_name,
				ifnull(is_archived, 0) as is_archived, last_message_at
			from `tab{_ROOM}` where name in ({placeholders})""",
		tuple(names),
	)
	data["room_meta"] = {row["name"]: row for row in meta}


def _collect_subscriptions(data: dict[str, Any], errors: list[str], *, now: Any, settings: Any) -> None:
	"""Subscription states and expiries — the thing whose failure is completely silent."""
	data["renew_before_seconds"] = _as_int(
		_setting(settings, "subscription_renew_before_seconds", DEFAULT_RENEW_BEFORE_SECONDS),
		DEFAULT_RENEW_BEFORE_SECONDS,
	)
	if not _table_ready(errors, _SUBSCRIPTION):
		data["subscriptions"] = []
		return

	# VERIFY: (unexecuted — this environment has no bench) which timezone
	# `Chat Event Subscription.expire_time` is stored in. Google returns RFC-3339 **UTC** on
	# the create/patch response; `frappe.utils.now_datetime()` is **site-local**. If the
	# subscription writer stores Google's value unconverted, every "time to expiry" below is
	# wrong by the site's UTC offset — and wrong in the direction that makes an expiring
	# subscription look healthier than it is. The report prints the caveat rather than
	# silently picking a convention. Settle it against sync/subscriptions.py when that lands
	# and delete both this comment and the printed note.
	rows = _sql(
		errors,
		"event subscriptions",
		f"""select name, subscription_uid, target_user, target_resource, state, suspension_reason,
				expire_time, last_renewed, ifnull(consecutive_failures, 0) as consecutive_failures
			from `tab{_SUBSCRIPTION}`
			order by (expire_time is null), expire_time asc""",
	)
	for row in rows:
		age = _age_seconds(row.get("expire_time"), now)
		# _age_seconds is now-minus-then; expiry is then-minus-now, so negate it.
		row["seconds_to_expiry"] = None if age is None else -age
	data["subscriptions"] = rows


def _collect_inbound(data: dict[str, Any], errors: list[str], *, now: Any) -> None:
	"""A three-line inbound summary. A stuck consumer is invisible from the relay side."""
	if not _table_ready(errors, _INBOUND_EVENT):
		data["inbound_by_status"] = {}
		return

	data["inbound_by_status"] = {
		row["status"]: _as_int(row["n"], 0)
		for row in _sql(
			errors,
			"inbound event status counts",
			f"select status, count(*) as n from `tab{_INBOUND_EVENT}` group by status",
		)
	}
	oldest = _sql(
		errors,
		"oldest unprocessed inbound event",
		f"""select name, event_type, received_at from `tab{_INBOUND_EVENT}`
			where status = 'Received' order by received_at asc limit 1""",
	)
	data["oldest_inbound"] = oldest[0] if oldest else None
	data["oldest_inbound_age"] = _age_seconds(oldest[0]["received_at"], now) if oldest else None


# --- rendering ------------------------------------------------------------------


def _render(data: dict[str, Any]) -> list[str]:
	lines: list[str] = []
	lines.append(_rule())
	lines.append(f"Google Chat sync health   site {data.get('site')}   {data.get('now')}")
	if data.get("room_filter"):
		lines.append(f"filtered to room {data['room_filter']}")
	lines.append(_rule())

	_render_config(lines, data)
	_render_queue(lines, data)
	_render_rooms(lines, data)
	_render_subscriptions(lines, data)
	_render_counters(lines, data)
	_render_errors(lines, data)
	return lines


def _render_config(lines: list[str], data: dict[str, Any]) -> None:
	settings = data.get("settings")
	backlog = _as_int(data.get("due_pending_count"), 0)

	lines.append("")
	lines.append("CONFIGURATION  (the switches that make everything below expected or alarming)")

	enabled = _as_int(_setting(settings, "enabled", 0), 0)
	# Off with nothing queued is a site that has not turned chat on: informational. Off with
	# work already queued is the actual outage, because the queue grows and nothing drains.
	flag = _OK if enabled else (_ALARM if backlog else _WARN)
	note = ""
	if not enabled and backlog:
		note = f"- {backlog} relay job(s) are due and NOTHING will send them"
	elif not enabled:
		note = "- chat is switched off; every idle number below is expected"
	lines.append(_line("chat enabled", _yesno(enabled), flag, note))

	dry_run = _as_int(_setting(settings, "dry_run_mode", 1), 1)
	lines.append(
		_line(
			"dry run mode",
			_onoff(dry_run),
			_WARN if dry_run else _OK,
			"- calls are built and logged, never sent" if dry_run else "",
		)
	)

	for label, field in (
		("outbound relay enabled", "relay_outbound_enabled"),
		("inbound relay enabled", "relay_inbound_enabled"),
		("google sync enabled", "google_sync_enabled"),
	):
		value = _as_int(_setting(settings, field, 0), 0)
		lines.append(_line(label, _yesno(value), _OK if value else _WARN))

	for label, field in (
		("kill switch: pause_outbound", "pause_outbound"),
		("kill switch: pause_inbound", "pause_inbound"),
	):
		value = _as_int(_setting(settings, field, 0), 0)
		note = "- deliberately paused; this is why nothing is moving" if value else ""
		lines.append(_line(label, _onoff(value), _ALARM if value else _OK, note))


def _render_queue(lines: list[str], data: dict[str, Any]) -> None:
	lines.append("")
	lines.append("RELAY QUEUE  (a room drains at 1 write/second, so a backlog is not by itself a fault)")

	if not data.get("relay_ready"):
		lines.append(_line("Chat Relay Job table", "not installed", _ALARM, "- see problems below"))
		return

	by_status = data.get("relay_by_status") or {}
	for status in ("Pending", "In Progress", "Done", "Failed", "Dead", "Skipped"):
		count = _as_int(by_status.get(status), 0)
		flag = ""
		note = ""
		if status == "Dead" and count:
			flag, note = _ALARM, "- gave up permanently; the two sides are divergent"
		elif status == "Failed" and count:
			flag, note = _WARN, "- will retry; check attempts and last_error"
		lines.append(_line(f"jobs {status.lower()}", f"{count} job(s)", flag, note))

	due = _as_int(data.get("due_pending_count"), 0)
	lines.append(_line("of which due now", f"{due} job(s)", "", "- available_at has passed"))

	age = data.get("oldest_pending_age")
	oldest = data.get("oldest_pending") or {}
	lines.append(
		_line(
			"oldest Pending job, any",
			_humanise_seconds(age) if age is not None else "none pending",
			_OK if age is None else "",
			f"- room {oldest.get('room')} seq {oldest.get('job_seq')} {oldest.get('operation') or ''}".rstrip()
			if oldest
			else "",
		)
	)

	due_age = data.get("oldest_due_pending_age")
	due_job = data.get("oldest_due_pending") or {}
	if due_age is None:
		lines.append(_line("oldest Pending job, DUE", "none due", _OK, "- the queue is caught up"))
	else:
		if due_age >= DUE_PENDING_ALARM_SECONDS:
			flag = _ALARM
			note = "- nothing is draining the queue; is the worker alive and the sweeper scheduled?"
		elif due_age >= DUE_PENDING_WARN_SECONDS:
			flag = _WARN
			note = f"- over the {DUE_PENDING_WARN_SECONDS}s drain-rate allowance"
		else:
			flag = _OK
			note = "- within the 1-write/second drain allowance"
		lines.append(_line("oldest Pending job, DUE", _humanise_seconds(due_age), flag, note))
		lines.append(
			_line(
				"  that job",
				f"{due_job.get('name')} room {due_job.get('room')} "
				f"seq {due_job.get('job_seq')} attempts {due_job.get('attempts')}",
			)
		)

	if not data.get("lease_column", False):
		lines.append(
			_line(
				"expired in-flight leases",
				"cannot tell",
				_WARN,
				"- Chat Relay Job.lease_expires_at is not installed, so crashed workers are undetectable",
			)
		)
	else:
		count = _as_int(data.get("expired_lease_count"), 0)
		if count >= EXPIRED_LEASE_ALARM_COUNT:
			flag, note = _ALARM, "- workers are dying repeatedly, not just once"
		elif count:
			flag, note = _WARN, "- a worker crashed mid-write; the room's FIFO is blocked until reclaimed"
		else:
			flag, note = _OK, ""
		lines.append(_line("expired in-flight leases", f"{count} job(s)", flag, note))
		for row in (data.get("expired_leases") or [])[:5]:
			lines.append(
				_line(
					"  stuck since",
					f"{row.get('lease_expires_at')}  {row.get('name')} room {row.get('room')} "
					f"seq {row.get('job_seq')} {row.get('operation') or ''}".rstrip(),
				)
			)

	inbound = data.get("inbound_by_status") or {}
	if inbound:
		summary = ", ".join(f"{k or '?'} {v}" for k, v in sorted(inbound.items()))
		lines.append(_line("inbound events by status", summary))
		in_age = data.get("oldest_inbound_age")
		if in_age is not None:
			flag = _WARN if in_age >= INBOUND_STUCK_WARN_SECONDS else _OK
			note = "- the Pub/Sub consumer is not draining" if flag == _WARN else ""
			lines.append(_line("oldest unprocessed inbound", _humanise_seconds(in_age), flag, note))


def _render_rooms(lines: list[str], data: dict[str, Any]) -> None:
	rooms = data.get("rooms") or []
	total = _as_int(data.get("rooms_total"), 0)

	lines.append("")
	lines.append("PER-ROOM SYNC STATE  (rooms with backlog or stuck messages; worst first)")
	if not rooms:
		lines.append(_line("rooms needing attention", "0", _OK, "- every room is fully relayed"))
		return
	if total > len(rooms):
		lines.append(f"  showing {len(rooms)} of {total}; pass max_rooms to see more")

	meta_all = data.get("room_meta") or {}
	for entry in rooms:
		meta = meta_all.get(entry["room"], {})
		title = (meta.get("title") or "").strip() or "<untitled>"
		lines.append("")
		lines.append(f"  room {entry['room']}  {title}")
		state = meta.get("provisioning_state") or "unknown"
		space = (meta.get("gchat_space_name") or "").strip()
		if state != "Ready":
			lines.append(
				_line(
					"  provisioning state",
					state,
					_ALARM if state == "Failed" else _WARN,
					"- nothing relays until the space exists",
				)
			)
		else:
			lines.append(_line("  provisioning state", state, _OK))
		lines.append(
			_line(
				"  google space",
				space or "not bound",
				_OK if space else _ALARM,
				"" if space else "- no gchat_space_name; outbound has nowhere to write",
			)
		)
		if _as_int(meta.get("is_archived"), 0):
			lines.append(_line("  archived", "yes", _WARN, "- archived rooms should not be accruing work"))

		pending = entry["pending_jobs"]
		if pending:
			lines.append(
				_line(
					"  relay jobs pending",
					f"{pending} job(s)",
					"",
					f"- about {_humanise_seconds(entry['drain_seconds'])} to drain at 1 write/second",
				)
			)
		if entry["in_progress_jobs"]:
			lines.append(_line("  relay jobs in flight", f"{entry['in_progress_jobs']} job(s)"))
		if entry["failed_jobs"]:
			lines.append(
				_line("  relay jobs failed", f"{entry['failed_jobs']} job(s)", _WARN, "- check last_error")
			)
		age = entry.get("oldest_job_age")
		if age is not None:
			lines.append(_line("  oldest unfinished job", _humanise_seconds(age)))
		if entry["pending_messages"]:
			lines.append(_line("  messages sync_state Pending", f"{entry['pending_messages']}"))
		if entry["failed_messages"]:
			lines.append(
				_line(
					"  messages sync_state Failed",
					f"{entry['failed_messages']}",
					_WARN,
					"- these exist in ERPNext and not in Google Chat",
				)
			)


def _render_subscriptions(lines: list[str], data: dict[str, Any]) -> None:
	rows = data.get("subscriptions") or []
	renew_before = _as_int(data.get("renew_before_seconds"), DEFAULT_RENEW_BEFORE_SECONDS)

	lines.append("")
	lines.append("EVENT SUBSCRIPTIONS  (one per coworker; when these lapse, inbound stops with no error)")
	lines.append(
		"  note: 'time to expiry' assumes expire_time is stored in this site's local time. "
		"Google returns RFC-3339 UTC."
	)
	if not rows:
		lines.append(_line("subscriptions", "none", _ALARM, "- nothing is subscribed; inbound is dead"))
		return

	active = sum(1 for row in rows if (row.get("state") or "") == "ACTIVE")
	lines.append(_line("subscriptions total", f"{len(rows)} ({active} ACTIVE)"))

	for row in rows:
		state = row.get("state") or "STATE_UNSPECIFIED"
		who = row.get("target_user") or row.get("subscription_uid") or row.get("name")
		seconds = row.get("seconds_to_expiry")
		failures = _as_int(row.get("consecutive_failures"), 0)

		if state == "SUSPENDED":
			flag = _ALARM
			note = f"- suspended: {row.get('suspension_reason') or 'no reason recorded'}; reactivate it"
		elif state == "DELETED":
			flag = _ALARM
			note = "- deleted at Google; this user receives nothing"
		elif state != "ACTIVE":
			flag = _WARN
			note = "- state was never read back from the create/patch response"
		elif seconds is None:
			flag = _WARN
			note = "- no expire_time stored; the renewal scheduler has nothing to derive a period from"
		elif seconds <= 0:
			flag = _ALARM
			note = "- ALREADY EXPIRED; inbound for this user has stopped"
		elif seconds <= renew_before:
			flag = _WARN
			note = f"- inside the {_humanise_seconds(renew_before)} renewal window and not yet renewed"
		else:
			flag = _OK
			note = ""

		# Rendered as three distinct phrasings rather than one signed number: "expires in
		# -7200 s" is the kind of line an operator reads as "expires soon" at 2am.
		if seconds is None:
			when = "expiry unknown"
		elif seconds <= 0:
			when = f"EXPIRED {_humanise_seconds(-seconds)} ago"
		else:
			when = f"expires in {_humanise_seconds(seconds)}"
		lines.append(_line(f"  {who}", f"{state}, {when}", flag, note))
		if failures:
			lines.append(
				_line(
					"    consecutive failures",
					str(failures),
					_ALARM if failures >= 3 else _WARN,
					"- renewal keeps failing",
				)
			)


def _render_counters(lines: list[str], data: dict[str, Any]) -> None:
	values: dict[str, int] = data.get("counters") or {}

	lines.append("")
	lines.append("COUNTERS  (since the cache Redis was last cleared, which a deploy does)")
	if data.get("counters_degraded"):
		lines.append(
			_line(
				"counter storage",
				"degraded",
				_WARN,
				"- a cache write failed; these may be this process only, i.e. undercounted",
			)
		)

	printed: set[str] = set()
	for group, names in _COUNTER_GROUPS:
		lines.append(f"  {group}")
		for name in names:
			if name not in values:
				continue
			printed.add(name)
			lines.append(_counter_line(name, values[name]))

	leftover = [name for name in values if name not in printed]
	if leftover:
		lines.append("  other")
		for name in leftover:
			lines.append(_counter_line(name, values[name]))
	if not values:
		lines.append(_line("counters", "unavailable", _WARN, "- see problems below"))


def _counter_line(name: str, value: int) -> str:
	count = _as_int(value, 0)
	if count and name in COUNTER_ALARM_IF_NONZERO:
		return _line(f"  {name}", str(count), _ALARM, f"- {COUNTER_ALARM_IF_NONZERO[name]}")
	if count and name in COUNTER_WARN_IF_NONZERO:
		return _line(f"  {name}", str(count), _WARN, f"- {COUNTER_WARN_IF_NONZERO[name]}")
	return _line(f"  {name}", str(count))


def _render_errors(lines: list[str], data: dict[str, Any]) -> None:
	errors = data.get("errors") or []
	lines.append("")
	if not errors:
		lines.append(_rule("-"))
		lines.append("no problems collecting this report; every number above was actually read")
		return
	lines.append(_rule("-"))
	lines.append("PROBLEMS COLLECTING THIS REPORT - sections above may be incomplete")
	for item in errors:
		lines.append(f"  - {item}")


# --- entry point -----------------------------------------------------------------


def report(room: str | None = None, max_rooms: int = 20) -> None:
	"""Print the report. **Never raises, never returns a value.**

	Returns ``None`` on purpose: ``bench execute`` JSON-encodes a truthy return value, which
	would render this as one escaped line with ``\\n`` in it. Printing is what makes it
	readable in the terminal it is run from.

	The outer ``except`` is the last line of defence behind the per-query guards. If it ever
	fires, the traceback is printed rather than logged — a stack in the operator's terminal
	is worth more at 2am than an Error Log row they then have to go and find. It is a
	traceback string, not frame locals, so it cannot carry a token the way a bare re-raise
	out of a background job can.
	"""
	lines: list[str] = []
	try:
		lines = _render(collect(room=room, max_rooms=max_rooms))
	except Exception:
		lines = [
			_rule(),
			"Google Chat sync health - THE HEALTH REPORT ITSELF FAILED",
			_rule(),
			"This is a bug in chat/health.py, not necessarily in the sync engine.",
			"",
			traceback.format_exc(),
		]

	body = "\n".join(lines)
	try:
		print(body)
	except UnicodeEncodeError:
		# Every string this module *prints* is deliberately ASCII, but a room title or a
		# Google error string is not ours to control, and `print` to a pipe under a POSIX
		# locale raises rather than degrades. "never raises" has to survive its own output.
		print(body.encode("ascii", "replace").decode("ascii"))
