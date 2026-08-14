# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The drift census. Phase 6 §4.I — detection, and deliberately nothing else.

	bench --site <site> execute erpnext_enhancements.chat.governance.drift.run_drift_scan
	bench --site <site> execute erpnext_enhancements.chat.governance.drift.report

:mod:`drift_rules` holds every judgement and states, at length, why a detector written
against *absence* is a configuration detector wearing a costume. Read that first; this module
is the queries.

--------------------------------------------------------------------------------------
What this does not do, so nobody has to infer it from the absence of a function
--------------------------------------------------------------------------------------

**It makes no Google call of any kind.** Not a listing, not a `get`, not a write. Every class
in this first change is answerable from ERPNext's own tables, which is what makes it cheap
enough to run nightly and impossible to get wrong through a partial listing. The classes that
*do* need a Google read — a Chat-side tombstone ERPNext never applied, a body that diverged —
need ``reconcile._list_window`` to stop hardcoding ``show_deleted=False``, and that is a
change to the sweep with its own test surface.

**It repairs nothing**, and the reason is in :mod:`drift_rules`. In one line: three
independent reviews of a repairing design each killed the one class that looked safe, and the
worst of the three failures was a repairer that inserts a *second* live row for one Google
message which no shipped path can merge or relay away.

**It never reads message text.** Not into a row, not into an alert, not into a log line. This
is a census of what diverged; a drift table holding bodies would be a second transcript
outside the membership model.

--------------------------------------------------------------------------------------
The gate, which is the part most easily got wrong
--------------------------------------------------------------------------------------

The scan refuses entirely unless ``Chat Settings.enabled`` is on. That is not defensive
tidiness. With the master switch off no relay job and no inbound event has ever been written,
so every class here is vacuously empty and a scan would report a clean estate — a *true*
answer that reads as reassurance about a mirror that does not exist. Refusing with a stated
reason is the honest form of the same fact.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from erpnext_enhancements.chat.governance import alert_rules, alerts, drift_rules

REPORT_DOCTYPE = "Chat Drift Report"
ROOM_DOCTYPE = "Chat Room"
MESSAGE_DOCTYPE = "Chat Message"
RELAY_JOB_DOCTYPE = "Chat Relay Job"
INBOUND_EVENT_DOCTYPE = "Chat Inbound Event"

#: How many objects of one class a single scan will look at. Not a cap on findings — that is
#: :func:`drift_rules.run_verdict` — but a bound on the query, so a pathological table cannot
#: turn a nightly job into a table scan of everything.
_PER_CLASS_LIMIT = 2000

#: Rooms examined per scan, oldest watermark first, mirroring the sweep's own batching.
_ROOM_LIMIT = 500


def run_drift_scan(limit: int = 0, dry_run: int = 0) -> dict[str, Any]:
	"""Look for divergence, record it, alert on it. Returns a summary. **Never raises.**

	``dry_run`` classifies and returns the counts without writing a single row — the mode you
	want the first time this runs against a real estate, because the interesting question is
	*how many* findings there are before any of them are in a table.
	"""
	summary: dict[str, Any] = {
		"ok": True,
		"scanned_at": str(now_datetime()),
		"findings": 0,
		"by_class": {},
		"written": 0,
		"cleared": 0,
		"halted": False,
		"reason": "",
	}
	try:
		open_reason = _gate()
		if open_reason:
			summary["ok"] = False
			summary["reason"] = open_reason
			return summary

		findings = _collect(limit=cint(limit) or _ROOM_LIMIT)
		summary["findings"] = len(findings)
		by_class: dict[str, int] = {}
		for finding in findings:
			by_class[finding["drift_class"]] = by_class.get(finding["drift_class"], 0) + 1
		summary["by_class"] = by_class

		verdict = drift_rules.run_verdict(len(findings), _setting_int("drift_max_findings_per_run"))
		if verdict == drift_rules.VERDICT_HALT:
			summary["halted"] = True
			summary["reason"] = "the per-run finding cap was exceeded; nothing was written"
			_halt_alert(len(findings), by_class)
			return summary

		alerts.clear_alert(subsystem="drift", kind="run_halted")

		if cint(dry_run):
			summary["reason"] = "dry run; nothing written"
			return summary

		summary["written"] = _write(findings)
		summary["cleared"] = _clear_absent(findings)
		_class_alerts(by_class)
	except Exception as exc:  # noqa: BLE001 - a nightly census must not become an incident
		summary["ok"] = False
		summary["reason"] = f"{type(exc).__name__}: {exc}"
		alerts.raise_alert(
			subsystem="drift",
			kind="scan_failed",
			summary="the chat drift scan could not complete",
			detail=summary["reason"],
		)
	return summary


# --- the gate ------------------------------------------------------------------


def _gate() -> str:
	"""``""`` to proceed, or the reason not to.

	Deliberately gated on the **master switch** rather than on the drift toggle alone: a
	drift census on a site where chat has never been switched on reports a clean estate,
	which is true and reads as reassurance about a mirror that does not exist.
	"""
	if not _setting_int("enabled"):
		return "chat is disabled; a drift census would report a clean estate that does not exist"
	if not _setting_int("drift_detection_enabled"):
		return "drift detection is switched off in Chat Settings"
	return ""


def _setting_int(field: str, default: int = 0) -> int:
	try:
		return cint(frappe.db.get_single_value("Chat Settings", field))
	except Exception:  # noqa: BLE001 - a half-migrated site must still be able to refuse
		return default


# --- collection ----------------------------------------------------------------


def _collect(*, limit: int) -> list[dict[str, Any]]:
	settling = _setting_int("drift_settling_minutes") or drift_rules.DEFAULT_SETTLING_MINUTES
	cutoff = add_to_date(now_datetime(), minutes=-settling)

	converging = _converging_rooms()
	findings: list[dict[str, Any]] = []
	findings.extend(_relay_dead_letters(cutoff, converging))
	findings.extend(_lost_bindings(cutoff, converging))
	findings.extend(_abandoned_inbound(cutoff, converging))
	findings.extend(_room_findings(limit=limit))
	return findings


def _converging_rooms() -> set[str]:
	"""Rooms with live relay work. Their messages are in flight, not drifted.

	Room-level and not message-level, deliberately: Rule 1 head-of-line blocking means one
	stuck job holds every later message in that room, so those later messages look unmirrored
	while being perfectly healthy.
	"""
	rows = frappe.get_all(
		RELAY_JOB_DOCTYPE,
		filters={"status": ["in", list(_open_statuses())]},
		fields=["room"],
		limit=_PER_CLASS_LIMIT,
	)
	return {str(row.get("room")) for row in rows if row.get("room")}


def _open_statuses() -> tuple[str, ...]:
	from erpnext_enhancements.chat.doctype.chat_relay_job import chat_relay_job

	return chat_relay_job.OPEN_STATUSES


def _relay_dead_letters(cutoff: Any, converging: set[str]) -> list[dict[str, Any]]:
	"""A relay write that was attempted, retried, and permanently given up on.

	**The evidence and the divergence are the same row**, which is what makes this the
	cheapest honest class in the set — and it is invisible today: a dead letter bumps a
	counter that a deploy clears, and nothing else says the two sides are now permanently
	apart about a specific message.

	It is also on a clock. ``Dead`` is in ``chat_relay_job.PURGEABLE_STATUSES`` and
	``relay_job_retention_days`` defaults to 30, so **the divergence outlives the only record
	of it**. A finding row here is what survives that purge.
	"""
	rows = frappe.get_all(
		RELAY_JOB_DOCTYPE,
		filters={
			"status": "Dead",
			"modified": ["<", cutoff],
		},
		fields=[
			"name",
			"room",
			"operation",
			"reference_doctype",
			"reference_name",
			"attempts",
			"http_status",
			"google_error_status",
			"completed_at",
			"modified",
		],
		order_by="modified desc",
		limit=_PER_CLASS_LIMIT,
	)
	out = []
	for row in rows:
		room = str(row.get("room") or "")
		if room in converging:
			continue
		out.append(
			_finding(
				drift_rules.RELAY_DEAD_LETTER,
				scope=str(row.get("name")),
				room=room,
				reference_doctype=str(row.get("reference_doctype") or RELAY_JOB_DOCTYPE),
				reference_name=str(row.get("reference_name") or row.get("name")),
				summary=f"a {row.get('operation')} relay job was permanently abandoned",
				detail={
					"job": row.get("name"),
					"operation": row.get("operation"),
					"attempts": row.get("attempts"),
					"http_status": row.get("http_status"),
					"google_error_status": row.get("google_error_status"),
					"completed_at": str(row.get("completed_at") or ""),
				},
			)
		)
	return out


def _lost_bindings(cutoff: Any, converging: set[str]) -> list[dict[str, Any]]:
	"""Google accepted the write and the resource name never came back.

	The evidence is a relay job that reached ``Done``. Without it this query is *"a message
	with no ``gchat_message_name``"*, which on a site with the relay switched off is every
	message ever written — the trap the whole module is shaped around.
	"""
	rows = frappe.get_all(
		RELAY_JOB_DOCTYPE,
		filters={
			"status": "Done",
			"operation": "Message Create",
			"reference_doctype": MESSAGE_DOCTYPE,
			"modified": ["<", cutoff],
		},
		fields=["name", "room", "reference_name", "completed_at"],
		order_by="modified desc",
		limit=_PER_CLASS_LIMIT,
	)
	out = []
	for row in rows:
		room = str(row.get("room") or "")
		message = str(row.get("reference_name") or "")
		if not message or room in converging:
			continue
		bound = _message_binding(message)
		if bound is None:
			# The message is gone. A completed relay job for a deleted row is not drift; it
			# is a purge that ran, and reporting it would fill the board with history.
			continue
		if bound:
			continue
		out.append(
			_finding(
				drift_rules.BOUND_LOST_AFTER_RELAY,
				scope=message,
				room=room,
				reference_doctype=MESSAGE_DOCTYPE,
				reference_name=message,
				summary="Google accepted this message and ERPNext never recorded its resource name",
				detail={
					"job": row.get("name"),
					"completed_at": str(row.get("completed_at") or ""),
					"consequence": (
						"every later edit and delete for this message targets a name ERPNext " "does not have"
					),
				},
			)
		)
	return out


def _message_binding(message: str) -> str | None:
	"""``""`` unbound, a name if bound, ``None`` if the message no longer exists.

	Three-valued on purpose. Collapsing "gone" into "unbound" would report every purged
	message as drift forever, and the row that proves otherwise has itself been deleted.
	"""
	row = frappe.db.get_value(MESSAGE_DOCTYPE, message, ["name", "gchat_message_name"], as_dict=True)
	if not row:
		return None
	return str(row.get("gchat_message_name") or "")


def _abandoned_inbound(cutoff: Any, converging: set[str]) -> list[dict[str, Any]]:
	"""An inbound event arrived, settled ``Failed``, and produced no message.

	``Failed`` is the one inbound state that is durable: the stuck-event sweeper deliberately
	does not re-drive it, and it is not in the retention set. So this is a message a coworker
	sent that ERPNext will never have, sitting in a table, with the payload still on the row
	— which is what makes it *actionable* rather than merely sad. Resetting ``status`` to
	``Received`` replays it.
	"""
	rows = frappe.get_all(
		INBOUND_EVENT_DOCTYPE,
		filters={
			"status": "Failed",
			"resulting_message": ["in", ("", None)],
			"modified": ["<", cutoff],
		},
		fields=[
			"name",
			"event_type",
			"gchat_space_name",
			"gchat_resource_name",
			"attempts",
			"received_at",
			"last_error",
		],
		order_by="received_at desc",
		limit=_PER_CLASS_LIMIT,
	)
	out = []
	for row in rows:
		room = _room_for_space(str(row.get("gchat_space_name") or ""))
		if room and room in converging:
			continue
		out.append(
			_finding(
				drift_rules.INBOUND_ABANDONED,
				scope=str(row.get("name")),
				room=room,
				reference_doctype=INBOUND_EVENT_DOCTYPE,
				reference_name=str(row.get("name")),
				summary="an inbound event was abandoned and never became a message",
				detail={
					"event": row.get("name"),
					"event_type": row.get("event_type"),
					"resource": row.get("gchat_resource_name"),
					"attempts": row.get("attempts"),
					"received_at": str(row.get("received_at") or ""),
					# The error text, not the payload. `last_error` is an exception string;
					# `payload` is the message, and copying it here would put a body in this
					# table.
					"last_error": str(row.get("last_error") or "")[:300],
					"recovery": "reset status to Received and the inbound sweeper replays it",
				},
			)
		)
	return out


def _room_for_space(space: str) -> str:
	if not space:
		return ""
	try:
		return str(frappe.db.get_value(ROOM_DOCTYPE, {"gchat_space_name": space}, "name") or "")
	except Exception:  # noqa: BLE001
		return ""


def _room_findings(*, limit: int) -> list[dict[str, Any]]:
	"""The two room-level classes, and the count that decides the staleness threshold."""
	rooms = frappe.get_all(
		ROOM_DOCTYPE,
		filters={
			"provisioning_state": "Ready",
			"is_archived": 0,
			"gchat_space_name": ["is", "set"],
		},
		fields=["name", "gchat_space_name", "last_reconcile_at", "last_event_at"],
		order_by="last_reconcile_at asc",
		limit=limit,
	)
	stale_after = drift_rules.reconcile_stale_hours(len(rooms), _setting_int("drift_reconcile_stale_hours"))
	now = now_datetime()

	out = []
	for room in rooms:
		name = str(room.get("name"))
		subject = _subject_for_room(name)
		verdict = drift_rules.classify_room(
			{
				"is_mirrored": True,
				"subject": subject,
				"hours_since_reconcile": _hours_since(room.get("last_reconcile_at"), now),
				"stale_after_hours": stale_after,
			}
		)
		if verdict is None:
			continue
		if verdict == drift_rules.ROOM_UNREADABLE:
			summary = "this room is mirrored and has no active member to read it as"
			detail = {
				"space": room.get("gchat_space_name"),
				"consequence": (
					"spaces.messages.list is user-authenticated, so this room contributes no "
					"findings of any other class — it is indistinguishable from a clean room"
				),
			}
		else:
			summary = "the reconciliation sweep has not reached this room in far too long"
			detail = {
				"space": room.get("gchat_space_name"),
				"last_reconcile_at": str(room.get("last_reconcile_at") or "never"),
				"last_event_at": str(room.get("last_event_at") or "never"),
				"stale_after_hours": stale_after,
				"mirrored_rooms": len(rooms),
			}
		out.append(
			_finding(
				verdict,
				scope=name,
				room=name,
				reference_doctype=ROOM_DOCTYPE,
				reference_name=name,
				summary=summary,
				detail=detail,
			)
		)
	return out


def _subject_for_room(room: str) -> str:
	"""Delegated to the sweep's own chooser, so the two cannot disagree.

	If this module asked the question its own way, ``room_unreadable`` would eventually
	describe a room the sweep reads fine, or miss one it skips — and a drift report that is
	wrong about its own blind spots is worse than not having it.
	"""
	from erpnext_enhancements.chat.sync import reconcile

	try:
		return reconcile._subject_for_room(room)
	except Exception:  # noqa: BLE001
		return ""


def _hours_since(value: Any, now: Any) -> float | None:
	if not value:
		# Never swept. Reported as an infinite age rather than as unknown: a room that has
		# never been reconciled is the strongest form of this finding, and returning None
		# would silently drop it.
		return float(1 << 30)
	try:
		return (now - get_datetime(value)).total_seconds() / 3600.0
	except Exception:  # noqa: BLE001
		return None


def _finding(
	drift_class: str,
	*,
	scope: str,
	room: str,
	reference_doctype: str,
	reference_name: str,
	summary: str,
	detail: dict[str, Any],
) -> dict[str, Any]:
	return {
		"drift_class": drift_class,
		"report_key": drift_rules.report_key(drift_class, scope),
		"scope": scope,
		"room": room or None,
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
		"summary": summary[:255],
		"detail": json.dumps(detail, sort_keys=True, default=str)[:2000],
		"evidence": drift_rules.EVIDENCE[drift_class],
	}


# --- writing -------------------------------------------------------------------


def _write(findings: list[dict[str, Any]]) -> int:
	written = 0
	now = now_datetime()
	for finding in findings:
		existing = _live_finding(finding["report_key"])
		if existing:
			frappe.db.set_value(
				REPORT_DOCTYPE,
				existing["name"],
				{
					"observations": cint(existing.get("observations")) + 1,
					"last_seen_at": now,
					"detail": finding["detail"],
				},
				update_modified=False,
			)
			continue
		doc = frappe.get_doc(
			{
				"doctype": REPORT_DOCTYPE,
				"state": drift_rules.STATE_OPEN,
				"observations": 1,
				"first_seen_at": now,
				"last_seen_at": now,
				**finding,
			}
		)
		doc.insert(ignore_permissions=True)
		written += 1
	return written


def _live_finding(key: str) -> dict[str, Any] | None:
	rows = frappe.get_all(
		REPORT_DOCTYPE,
		filters={"report_key": key, "state": ["in", list(drift_rules.LIVE_STATES)]},
		fields=["name", "observations", "state"],
		order_by="creation desc",
		limit=1,
	)
	return rows[0] if rows else None


def _clear_absent(findings: list[dict[str, Any]]) -> int:
	"""Close every live finding this scan did not re-observe.

	A census that can only open findings produces a board of things that were once true,
	and a board nobody trusts is one nobody reads — the same end state as no census at all,
	reached by a longer road.

	``Accepted`` rows are cleared too, and that is right: accepting a finding means *"we know
	and are not fixing it"*, not *"never tell me it is gone"*.
	"""
	seen = {f["report_key"] for f in findings}
	live = frappe.get_all(
		REPORT_DOCTYPE,
		filters={"state": ["in", list(drift_rules.LIVE_STATES)]},
		fields=["name", "report_key"],
		limit=_PER_CLASS_LIMIT,
	)
	cleared = 0
	now = now_datetime()
	for row in live:
		if row.get("report_key") in seen:
			continue
		frappe.db.set_value(
			REPORT_DOCTYPE,
			row["name"],
			{
				"state": drift_rules.STATE_CLEARED,
				"cleared_at": now,
				"cleared_note": "not observed by the most recent scan",
			},
			update_modified=False,
		)
		cleared += 1
	return cleared


# --- alerting ------------------------------------------------------------------


def _halt_alert(count: int, by_class: dict[str, int]) -> None:
	alerts.raise_alert(
		subsystem="drift",
		kind="run_halted",
		severity=alert_rules.SEVERITY_CRITICAL,
		summary="the drift scan found more divergence than it is willing to record",
		detail=(
			f"{count} findings exceeded the per-run cap, so NOTHING was written. A mass "
			"divergence is the condition where the classifier is most likely to be the thing "
			"that is wrong, and a partial table would look like a small credible problem. "
			f"By class: {json.dumps(by_class, sort_keys=True)}"
		),
	)


def _class_alerts(by_class: dict[str, int]) -> None:
	"""One alert per class, raised or cleared. The counts go in the detail, never the key.

	``room_unreadable`` is the one that is critical rather than a warning, because it is the
	finding that invalidates the others: a room nobody can read reports clean on everything.
	"""
	for drift_class in drift_rules.CLASSES:
		count = int(by_class.get(drift_class) or 0)
		alerts.check(
			count == 0,
			subsystem="drift",
			kind=drift_class,
			severity=(
				alert_rules.SEVERITY_CRITICAL
				if drift_class == drift_rules.ROOM_UNREADABLE
				else alert_rules.SEVERITY_WARNING
			),
			summary=f"{drift_class.replace('_', ' ')}: {count} object(s)",
			detail=drift_rules.EVIDENCE[drift_class],
			count=count,
		)


# --- operator commands ----------------------------------------------------------


def accept(finding: str, note: str = "") -> dict[str, Any]:
	"""Mark a finding known-and-not-being-fixed. ``bench execute``, not an endpoint."""
	row = frappe.db.get_value(REPORT_DOCTYPE, finding, ["name", "state"], as_dict=True)
	if not row:
		return {"ok": False, "reason": "no such finding"}
	target = drift_rules.transition(str(row.get("state") or ""), drift_rules.EVENT_ACCEPT)
	if not target:
		return {"ok": False, "reason": f"a finding in {row.get('state')} cannot be accepted"}
	frappe.db.set_value(
		REPORT_DOCTYPE,
		finding,
		{
			"state": target,
			"accepted_by": frappe.session.user,
			"accepted_at": now_datetime(),
			"accepted_note": (note or "")[:500],
		},
		update_modified=False,
	)
	return {"ok": True, "state": target}


def report(limit: int = 50) -> None:
	"""Print the open findings. Prints rather than returns, like ``chat.health.report``.

	``bench execute`` JSON-encodes a truthy return value, which would render this as one
	escaped line.
	"""
	rows = frappe.get_all(
		REPORT_DOCTYPE,
		filters={"state": ["in", list(drift_rules.LIVE_STATES)]},
		fields=[
			"name",
			"drift_class",
			"state",
			"scope",
			"room",
			"summary",
			"observations",
			"first_seen_at",
		],
		order_by="drift_class asc, first_seen_at asc",
		limit=cint(limit) or 50,
	)
	lines = [f"chat drift — {len(rows)} open finding(s)"]
	if not rows:
		lines.append("  nothing is currently diverged, or the scan has not run")
	for row in rows:
		state = "" if row.get("state") == drift_rules.STATE_OPEN else f" [{row['state']}]"
		lines.append(
			f"  {row.get('drift_class')}{state}  {row.get('scope')}  "
			f"x{row.get('observations')}  since {row.get('first_seen_at')}"
		)
		lines.append(f"      {row.get('summary')}")
	print("\n".join(lines))
