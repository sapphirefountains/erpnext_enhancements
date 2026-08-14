# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The one alert path. Phase 6 §4.H.

Five private helpers used to spell "tell somebody" in this package and all five ended in the
``Error Log``. :mod:`alert_rules` explains why that was worse than it sounds; this module is
the replacement, and the five call sites now route through :func:`raise_alert`.

--------------------------------------------------------------------------------------
The order is the design, so it is stated once here
--------------------------------------------------------------------------------------

1. **A log-file line, first, unconditionally, for every occurrence.** Not the ``Error Log``
   — ``frappe.logger`` writes to a file on disk. This matters more than it looks: the
   ``Error Log`` is a database table, so the failure class where an alert is most valuable
   (*the database is unhappy*) is exactly the class where a database-backed alert is least
   likely to survive. The file line is the floor, and it is off the floor being reported on.
2. **The alert row** — deduplicated, counted, and given a lifecycle.
3. **An ``Error Log`` row on notification steps only**, so it stays desk-visible without
   reproducing the hourly-row problem this module exists to end.
4. **The transports** — the operations space, and email. Chosen by
   :func:`alert_rules.transports_for`, which refuses the space to any subsystem that would
   be delivering its own alert.

**Nothing here raises.** An alerting path that can abort the job reporting the problem turns
a warning into an outage — the rule ``sync/inbound.py::_alarm`` already states and this
module inherits. :func:`raise_alert` returns the alert name, or ``None`` when it could not
record one; a caller that needs the guarantee checks the return, and a caller that does not
has still had step 1 happen.

**The re-entrancy guard is load-bearing.** Every step below can fail, and the obvious
handling for a failure is to raise an alert about it. That is a loop which ends in a full
disk. :data:`_ACTIVE` makes the alert path unable to alert about itself; the failure goes to
the log file, which is where a failure of the alert path belongs.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import add_to_date, cint, escape_html, get_datetime, now_datetime

from erpnext_enhancements.chat.governance import alert_rules

ALERT_DOCTYPE = "Chat Ops Alert"

#: Re-entrancy guard. Module-level rather than threaded through, because the loop it stops is
#: created by an exception handler and an exception handler has no parameters.
_ACTIVE = False

#: How far back :func:`_recent_incident_count` looks for the rate limit. One hour, matching
#: the setting's name, and a fixed window rather than a rolling one — a rolling window over a
#: table indexed by ``creation`` is a second query on the path that fires during an incident.
_RATE_WINDOW_MINUTES = 60


def raise_alert(
	*,
	subsystem: str,
	kind: str,
	summary: str,
	detail: str = "",
	severity: str = alert_rules.SEVERITY_WARNING,
	scope: str = "",
	room: str | None = None,
	**context: Any,
) -> str | None:
	"""Record an occurrence of a problem. Returns the alert name, or ``None``.

	``scope`` identifies *which* instance of the problem — a room, a chain, a space. It goes
	into the deduplication key; ``detail`` and ``context`` carry the measured values and do
	not, because a key containing the number that changes deduplicates nothing while looking
	like it does.
	"""
	global _ACTIVE

	if _ACTIVE:
		# Something inside the alert path called back into it. The log file is the only safe
		# place left, and it is a better place than a loop.
		_logline("error", f"alert path re-entered: {subsystem}/{kind}: {summary}")
		return None

	try:
		key = alert_rules.dedup_key(subsystem, kind, scope)
	except ValueError as exc:
		# A bad subsystem is a programming error, and swallowing it would hide an alert
		# nobody can find. It still must not raise into the caller's transaction.
		_logline("error", f"refusing malformed alert ({exc}): {subsystem}/{kind}: {summary}")
		return None

	body = _render_detail(detail, context)
	_logline(
		"warning" if severity != alert_rules.SEVERITY_CRITICAL else "error",
		f"{alert_rules.summary_line(severity, subsystem, kind, 1)} {summary} :: {body}",
	)

	_ACTIVE = True
	try:
		return _record(
			key=key,
			subsystem=subsystem.strip().lower(),
			kind=kind,
			scope=scope,
			severity=severity,
			summary=summary,
			detail=body,
			room=room,
		)
	except Exception as exc:  # noqa: BLE001 - see the module docstring; never raise
		_logline("error", f"alert could not be recorded ({exc.__class__.__name__}): {summary}")
		_error_log(f"chat alert (unrecorded): {kind}", f"{summary}\n\n{body}")
		return None
	finally:
		_ACTIVE = False


def clear_alert(*, subsystem: str, kind: str, scope: str = "", note: str = "") -> int:
	"""Resolve every live alert with this key. Returns how many were resolved.

	**A checker that can only alarm produces a board of stale alarms**, and a board nobody
	trusts is one nobody reads — the same end state as no alerting, reached by a longer road.
	So every periodic check that can raise calls this on its healthy branch, and the board
	shows what is wrong *now*.
	"""
	global _ACTIVE

	if _ACTIVE:
		return 0
	try:
		key = alert_rules.dedup_key(subsystem, kind, scope)
	except ValueError:
		return 0

	_ACTIVE = True
	try:
		live = _live_alerts(key)
		for row in live:
			frappe.db.set_value(
				ALERT_DOCTYPE,
				row["name"],
				{
					"state": alert_rules.STATE_RESOLVED,
					"resolved_at": now_datetime(),
					"resolved_note": (note or "the condition cleared")[:500],
				},
				update_modified=False,
			)
			_logline("info", f"alert resolved: {key} after {row.get('occurrence_count')} occurrence(s)")
		return len(live)
	except Exception as exc:  # noqa: BLE001 - never raise out of the alert path
		_logline("error", f"alert could not be resolved ({exc.__class__.__name__}): {key}")
		return 0
	finally:
		_ACTIVE = False


def check(
	ok: bool,
	*,
	subsystem: str,
	kind: str,
	summary: str = "",
	scope: str = "",
	**kwargs: Any,
) -> str | None:
	"""``raise_alert`` when ``ok`` is false, ``clear_alert`` when it is true.

	The single entry point for a periodic check, so that raising and clearing cannot drift
	apart — the way they do when the healthy branch is an early ``return`` somebody wrote
	before there was anything to clear.
	"""
	if ok:
		clear_alert(subsystem=subsystem, kind=kind, scope=scope)
		return None
	return raise_alert(subsystem=subsystem, kind=kind, summary=summary, scope=scope, **kwargs)


# --- the record ----------------------------------------------------------------


def _record(
	*,
	key: str,
	subsystem: str,
	kind: str,
	scope: str,
	severity: str,
	summary: str,
	detail: str,
	room: str | None,
) -> str | None:
	existing = _live_alerts(key)
	if existing:
		return _bump(existing[0], severity=severity, summary=summary, detail=detail)

	verdict = alert_rules.rate_limit_verdict(_recent_incident_count(), _rate_limit())
	if verdict == alert_rules.VERDICT_SUPPRESS:
		_logline("error", f"alert suppressed by the storm limit: {key} :: {summary}")
		return None
	if verdict == alert_rules.VERDICT_STORM:
		_open_storm_alert()
		return None

	return _open(
		key=key,
		subsystem=subsystem,
		kind=kind,
		scope=scope,
		severity=severity,
		summary=summary,
		detail=detail,
		room=room,
	)


def _open(
	*,
	key: str,
	subsystem: str,
	kind: str,
	scope: str,
	severity: str,
	summary: str,
	detail: str,
	room: str | None,
) -> str:
	now = now_datetime()
	space, email = _channels()
	transports = alert_rules.transports_for(
		subsystem, severity, space_configured=bool(space), email_configured=bool(email)
	)
	undeliverable = alert_rules.undeliverable_reason(
		subsystem, space_configured=bool(space), email_configured=bool(email)
	)

	doc = frappe.get_doc(
		{
			"doctype": ALERT_DOCTYPE,
			"dedup_key": key,
			"subsystem": subsystem,
			"kind": kind[:140],
			"scope": (scope or "")[:140],
			"room": room or None,
			"severity": severity,
			"state": alert_rules.STATE_OPEN,
			"summary": summary[:255],
			"detail": detail[:2000],
			"occurrence_count": 1,
			"notified_count": 0,
			"first_seen_at": now,
			"last_seen_at": now,
			"transports": ",".join(transports),
			"undeliverable": undeliverable or None,
		}
	)
	doc.insert(ignore_permissions=True)
	_notify(doc.name, key, subsystem, kind, severity, summary, detail, transports, 1)
	return doc.name


def _bump(row: dict[str, Any], *, severity: str, summary: str, detail: str) -> str:
	name = row["name"]
	occurrence = cint(row.get("occurrence_count")) + 1
	updates: dict[str, Any] = {
		"occurrence_count": occurrence,
		"last_seen_at": now_datetime(),
		"detail": detail[:2000],
	}
	# Severity ratchets up and never down. A problem that was critical once is not made
	# routine by a later, milder measurement of the same problem.
	if severity == alert_rules.SEVERITY_CRITICAL and row.get("severity") != severity:
		updates["severity"] = severity

	frappe.db.set_value(ALERT_DOCTYPE, name, updates, update_modified=False)

	if alert_rules.notifies_at(occurrence):
		transports = tuple(str(row.get("transports") or "").split(",")) if row.get("transports") else ()
		_notify(
			name,
			str(row.get("dedup_key") or ""),
			str(row.get("subsystem") or ""),
			str(row.get("kind") or ""),
			str(updates.get("severity") or row.get("severity") or severity),
			summary,
			detail,
			tuple(t for t in transports if t),
			occurrence,
		)
	return name


def _live_alerts(key: str) -> list[dict[str, Any]]:
	"""Open or acknowledged rows for this key, newest first.

	``frappe.get_all`` without a membership filter, which every other module in this package
	is forbidden from doing. It is correct here and the reason is structural rather than a
	judgement call: ``Chat Ops Alert`` holds no message text in any field, by schema — a
	subsystem name, a kind, a room *identifier*, counts and timestamps. There is no
	membership to filter by, because an alert is about the machinery rather than about
	anything anybody said.
	"""
	return frappe.get_all(
		ALERT_DOCTYPE,
		filters={"dedup_key": key, "state": ["in", list(alert_rules.LIVE_STATES)]},
		fields=["name", "occurrence_count", "severity", "transports", "dedup_key", "subsystem", "kind"],
		order_by="creation desc",
		limit=5,
	)


def _recent_incident_count() -> int:
	"""Distinct incidents opened in the last hour — occurrences of one do not count.

	That distinction is what makes the limit safe to set low: a check firing every minute
	updates a single row and consumes one of the budget, so reaching twenty means twenty
	*different* things broke, which is an outage or a broken classifier and wants one loud
	row rather than two hundred.
	"""
	since = add_to_date(now_datetime(), minutes=-_RATE_WINDOW_MINUTES)
	return cint(frappe.db.count(ALERT_DOCTYPE, {"creation": [">=", since]}))


def _open_storm_alert() -> None:
	"""One row saying alerting is suppressing, itself exempt from the suppression.

	A rate-limited notice that the rate limit is engaged is one nobody ever sees, and the
	suppression is precisely the news — every alert after this point is invisible until an
	operator does something.
	"""
	if _live_alerts(alert_rules.STORM_KEY):
		return
	now = now_datetime()
	_, email = _channels()
	doc = frappe.get_doc(
		{
			"doctype": ALERT_DOCTYPE,
			"dedup_key": alert_rules.STORM_KEY,
			"subsystem": "alerting",
			"kind": "storm",
			"severity": alert_rules.SEVERITY_CRITICAL,
			"state": alert_rules.STATE_OPEN,
			"summary": "chat alerting has hit its hourly limit and is suppressing new alerts",
			"detail": (
				f"More than {_rate_limit()} distinct problems were raised in an hour. Every "
				"alert after this one is being dropped, so the absence of alerts now means "
				"nothing. Resolve this row once the cause is understood."
			),
			"occurrence_count": 1,
			"notified_count": 0,
			"first_seen_at": now,
			"last_seen_at": now,
			"transports": ",".join(
				alert_rules.transports_for(
					"alerting",
					alert_rules.SEVERITY_CRITICAL,
					space_configured=False,
					email_configured=bool(email),
				)
			),
		}
	)
	doc.insert(ignore_permissions=True)
	_logline("error", "chat alerting is suppressing: hourly incident limit reached")


# --- delivery ------------------------------------------------------------------


def _notify(
	name: str,
	key: str,
	subsystem: str,
	kind: str,
	severity: str,
	summary: str,
	detail: str,
	transports: tuple[str, ...],
	occurrence: int,
) -> None:
	"""Steps 3 and 4. Each channel is guarded on its own: one that fails must not take the
	others with it, which is the whole point of having more than one."""
	line = alert_rules.summary_line(severity, subsystem, kind, occurrence)
	errors: list[str] = []

	_error_log(f"chat alert: {kind}", f"{line}\n\n{summary}\n\n{detail}\n\nalert: {name} ({key})")

	if alert_rules.TRANSPORT_SPACE in transports:
		try:
			_to_ops_space(name, f"{line}\n{summary}\n{detail}")
		except Exception as exc:  # noqa: BLE001
			errors.append(f"space: {exc.__class__.__name__}")
	if alert_rules.TRANSPORT_EMAIL in transports:
		try:
			_to_email(line, f"{summary}\n\n{detail}\n\nAlert record: {name}\nKey: {key}")
		except Exception as exc:  # noqa: BLE001
			errors.append(f"email: {exc.__class__.__name__}")

	updates: dict[str, Any] = {
		"notified_count": _notified_count(name) + 1,
		"last_notified_at": now_datetime(),
	}
	if errors:
		updates["delivery_error"] = "; ".join(errors)[:500]
		_logline("error", f"alert delivery failed ({'; '.join(errors)}): {key}")
	try:
		frappe.db.set_value(ALERT_DOCTYPE, name, updates, update_modified=False)
	except Exception:  # noqa: BLE001
		pass


def _notified_count(name: str) -> int:
	try:
		return cint(frappe.db.get_value(ALERT_DOCTYPE, name, "notified_count"))
	except Exception:  # noqa: BLE001
		return 0


#: Where the ops-space post actually happens. A **dotted string, not an import** — see
#: :mod:`chat.governance.alert_delivery`. Importing that module here would restore the static
#: path `chat.permissions -> chat.audit -> alerts -> chat.api.compose -> …gchat.client`, which
#: `test_chat_guardrails` refuses because it puts two `doc_events` handlers one import from
#: the Google transport. The runtime edge is gone too: delivery runs in a worker, after
#: commit, in its own transaction.
_SPACE_WORKER = "erpnext_enhancements.chat.governance.alert_delivery.post_to_ops_space"

#: An alert is small and wants to be timely, and it does not want to be ahead of somebody's
#: message in a shared queue.
_SPACE_QUEUE = "short"
_SPACE_TIMEOUT = 120


def _to_ops_space(alert: str, body: str) -> None:
	"""Queue the post rather than making it, and never insert a ``Chat Message`` here.

	``enqueue_after_commit`` because the alert row is written in the caller's transaction: a
	post queued before commit and a caller that then rolls back would announce an incident
	whose record does not exist.

	The other half — why this is a queue at all rather than a call — is in
	:mod:`alert_delivery`. Briefly: a caller's rollback would take an inline post with it, and
	alerting that vanishes when things go wrong is alerting that works only when it is not
	needed.
	"""
	if not _ops_room() or not _post_as() or not body:
		return
	frappe.enqueue(
		_SPACE_WORKER,
		queue=_SPACE_QUEUE,
		timeout=_SPACE_TIMEOUT,
		enqueue_after_commit=True,
		# `alert` and `body` only. Every reserved `enqueue` kwarg — `method`, `queue`,
		# `timeout`, `event`, `is_async`, `job_name`, `now`, `enqueue_after_commit` — is
		# eaten before the job sees it, and this package has been bitten by that once
		# already with a kwarg called `event`.
		alert=alert,
		body=body[:3000],
	)


def _to_email(subject: str, body: str) -> None:
	"""``frappe.sendmail`` directly — **never** a ``Notification Log`` row.

	G6-18. ``hooks.py`` registers ``Chat Message`` and ``Chat Mention`` in
	``notification_skip_email_types`` so that chat notifications can never be emailed, and
	that suppression is structural rather than a default. Routing an operational alert
	through ``Notification Log`` would put the operational channel and the deliberately
	suppressed user channel on the same wire — and the way that fails is not "the alert does
	not arrive", it is somebody widening the notification path to make alerts work and
	re-enabling 12,000 emails a day as a side effect.
	"""
	recipients = _alert_emails()
	if not recipients:
		return
	frappe.sendmail(
		recipients=recipients,
		subject=subject[:200],
		message=f"<pre>{escape_html(body[:4000])}</pre>",
		now=False,
		# Not a Notification Log row, and not a Communication either: an alert is not
		# correspondence with anybody and should not appear in a contact's timeline.
		reference_doctype=None,
		reference_name=None,
	)


def _error_log(title: str, message: str) -> None:
	try:
		frappe.log_error(title=title[:140], message=message)
	except Exception:  # noqa: BLE001 - the log file already has it
		pass


def _logline(level: str, message: str) -> None:
	"""Step 1: a line in a file, off the database being reported on. Cannot raise."""
	try:
		logger = frappe.logger("chat.alerts", allow_site=True)
		getattr(logger, level, logger.info)(message)
	except Exception:  # noqa: BLE001
		pass


# --- configuration --------------------------------------------------------------


def _setting(field: str, default: Any = None) -> Any:
	try:
		value = frappe.db.get_single_value("Chat Settings", field)
	except Exception:  # noqa: BLE001 - a half-migrated site must still be able to alert
		return default
	return default if value is None else value


def _enabled() -> bool:
	return bool(cint(_setting("alerts_enabled", 0)))


def _ops_room() -> str:
	return str(_setting("alert_ops_room", "") or "").strip()


def _post_as() -> str:
	"""The identity that posts into the operations room. Empty means the channel is off.

	Separate from the room on purpose: naming a room without naming somebody who is in it
	produces a channel that looks configured and delivers nothing.
	"""
	return str(_setting("alert_post_as", "") or "").strip()


def _alert_emails() -> list[str]:
	raw = str(_setting("alert_email_recipients", "") or "")
	return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]


def _rate_limit() -> int:
	return cint(_setting("alert_rate_limit_per_hour", 0)) or alert_rules.DEFAULT_RATE_LIMIT_PER_HOUR


def _channels() -> tuple[str, list[str]]:
	"""``(ops room, email recipients)`` — both empty when alerting is off.

	``alerts_enabled`` gates *delivery*, never recording. An operator who turns alerting off
	is asking not to be paged; they are not asking the system to stop noticing, and a row
	written while nobody was watching is the first thing anybody wants afterwards.
	"""
	if not _enabled():
		return "", []
	# The space channel needs both halves. A room with nobody configured to post into it is
	# a channel that reports as configured and delivers nothing, which is the one failure
	# mode an alerting system may not have.
	room = _ops_room() if (_ops_room() and _post_as()) else ""
	return room, _alert_emails()


def _render_detail(detail: str, context: dict[str, Any]) -> str:
	if not context:
		return detail or ""
	try:
		rendered = json.dumps({k: _plain(v) for k, v in context.items()}, sort_keys=True, default=str)
	except Exception:  # noqa: BLE001
		rendered = str(context)
	return f"{detail}\n{rendered}".strip()


def _plain(value: Any) -> Any:
	if value is None or isinstance(value, str | int | float | bool):
		return value
	return str(value)


# --- operator commands ----------------------------------------------------------
#
# `bench execute`, not `@frappe.whitelist()`. `Chat Ops Alert` ships zero DocPerm like every
# other chat DocType, so there is no desk form to acknowledge from — and an HTTP surface here
# would be a new endpoint to rate-limit, scope and audit for the sake of a button. The health
# report is where these are read; §4.H's web dashboard is a separate change.


def acknowledge(alert: str, note: str = "") -> dict[str, Any]:
	"""``bench --site <site> execute …chat.governance.alerts.acknowledge --kwargs "{'alert': '…'}"``"""
	row = frappe.db.get_value(ALERT_DOCTYPE, alert, ["name", "state"], as_dict=True)
	if not row:
		return {"ok": False, "reason": "no such alert"}
	target = alert_rules.transition(str(row.get("state") or ""), alert_rules.EVENT_ACKNOWLEDGE)
	if not target:
		return {"ok": False, "reason": f"an alert in {row.get('state')} cannot be acknowledged"}
	frappe.db.set_value(
		ALERT_DOCTYPE,
		alert,
		{
			"state": target,
			"acknowledged_by": frappe.session.user,
			"acknowledged_at": now_datetime(),
			"acknowledged_note": (note or "")[:500],
		},
		update_modified=False,
	)
	return {"ok": True, "state": target}


def resolve(alert: str, note: str = "") -> dict[str, Any]:
	"""Close an alert by hand, for the incidents no check can see the end of."""
	row = frappe.db.get_value(ALERT_DOCTYPE, alert, ["name", "state"], as_dict=True)
	if not row:
		return {"ok": False, "reason": "no such alert"}
	target = alert_rules.transition(str(row.get("state") or ""), alert_rules.EVENT_RESOLVE)
	if not target:
		return {"ok": False, "reason": f"an alert in {row.get('state')} is already closed"}
	frappe.db.set_value(
		ALERT_DOCTYPE,
		alert,
		{
			"state": target,
			"resolved_at": now_datetime(),
			"resolved_note": (note or "resolved by hand")[:500],
		},
		update_modified=False,
	)
	return {"ok": True, "state": target}


def open_alerts(limit: int = 50) -> list[dict[str, Any]]:
	"""Every live alert, worst first. Read by the health report and by a human at 2am."""
	rows = frappe.get_all(
		ALERT_DOCTYPE,
		filters={"state": ["in", list(alert_rules.LIVE_STATES)]},
		fields=[
			"name",
			"severity",
			"state",
			"subsystem",
			"kind",
			"scope",
			"summary",
			"occurrence_count",
			"first_seen_at",
			"last_seen_at",
			"undeliverable",
			"delivery_error",
		],
		order_by="last_seen_at desc",
		limit=cint(limit) or 50,
	)
	for row in rows:
		row["age_seconds"] = _age(row.get("first_seen_at"))
	# Criticals first, then longest-running: an incident open six hours outranks one open two
	# minutes, and a list sorted newest-first buries it. Sorted here rather than in the
	# query, because expressing it in SQL means a `field(severity, …)` fragment inside an
	# `order_by` string, which is a hand-written SQL expression for a cosmetic ordering.
	rows.sort(
		key=lambda r: (
			0 if r.get("severity") == alert_rules.SEVERITY_CRITICAL else 1,
			-(r.get("age_seconds") or 0),
		)
	)
	return rows


def _age(value: Any) -> float | None:
	if not value:
		return None
	try:
		return (now_datetime() - get_datetime(value)).total_seconds()
	except Exception:  # noqa: BLE001
		return None
