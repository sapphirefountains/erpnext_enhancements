# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What a purge *would* destroy. Phase 6 §4.F — and deliberately not the purge.

	bench --site <site> execute erpnext_enhancements.chat.governance.retention.report
	bench --site <site> execute erpnext_enhancements.chat.governance.retention.plan

**Nothing in this module deletes anything.** Not a row, not a file, not a cache entry. There
is no ``delete_doc``, no ``db.delete``, no ``DELETE``, and ``test_chat_purge_surface`` asserts
that against the AST rather than trusting the sentence. The reason is in
:mod:`purge_rules` and it is the finding rather than a caveat: **Phase 5's derived layer has
a staleness story and no retirement story**, so a purge can neither keep nor destroy the
chunks and digests covering what it removes without doing something wrong. Verified on disk,
not inferred — see :func:`purge_rules.can_enable`, which this module calls and refuses on.

--------------------------------------------------------------------------------------
Why build the calculator at all, then
--------------------------------------------------------------------------------------

Three reasons, and none of them is "so the destructive half is nearly done".

**Because the survives-a-purge table is the artefact.** It is a decision per chat DocType
with a written reason, asserted for set-equality against the filesystem — so a DocType added
next year fails the build until somebody decides what a purge does to it. That value does not
depend on anything ever being deleted, and it expires the moment the table is written under
pressure instead.

**Because ``retention_run`` had no writer.** It is the last of four event types
``Chat Audit Log`` has declared since v1.285.0 with nothing able to produce one. A planning
run is a run: somebody asked what a purge would destroy, and D-7 is the principle that the
record of an act survives the act. This is that writer.

**Because the number is not the question.** A retention feature that reports *"4,812
messages are eligible"* invites the answer "so turn it on". The report this prints leads with
what is **held back and why**, because the holds are where every unsafe assumption lives —
and it ends with the blocker, in full, rather than a footnote.

Decision D-6 is *keep forever* and is already the shipped state: ``message_retention_days``
defaults to ``0`` and ``0`` means never. Nothing here changes what any site does today.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from erpnext_enhancements.chat import audit
from erpnext_enhancements.chat.governance import purge_rules

MESSAGE_DOCTYPE = "Chat Message"
ROOM_DOCTYPE = "Chat Room"
RELAY_JOB_DOCTYPE = "Chat Relay Job"

#: Rooms examined per planning run, and messages per room. Bounds on a *read*, not a cap on
#: destruction — there is nothing to cap. Both are proposals: no number for either exists in
#: any document for this system.
_ROOM_LIMIT = 500
_PER_ROOM_LIMIT = 2000


def plan(rooms: str = "", limit: int = 0) -> dict[str, Any]:
	"""What a purge would destroy and what it would hold back. **Never raises, never writes
	anything but one audit row.**

	The audit row is the one deliberate side effect, and it is not an accident of the shape:
	asking what a purge would destroy is itself a governance act, and D-7 says the record of
	an act outlives the act.
	"""
	summary: dict[str, Any] = {
		"ok": True,
		"planned_at": str(now_datetime()),
		"retention_days": 0,
		"eligible": 0,
		"held": 0,
		"holds": {},
		"rooms_examined": 0,
		"blocked": list(purge_rules.blocked_doctypes()),
		"reason": "",
	}
	try:
		enabled, why_not = purge_rules.can_enable()
		summary["can_enable"] = enabled
		summary["blocker"] = why_not

		retention_days = _setting_int("message_retention_days")
		summary["retention_days"] = retention_days
		if not _setting_int("enabled"):
			summary["reason"] = "chat is disabled; nothing has been written to purge"
			return summary

		names = [r.strip() for r in str(rooms or "").split(",") if r.strip()]
		found = _plan_rooms(names, retention_days, limit=cint(limit) or _ROOM_LIMIT)
		summary.update(found)

		_record(summary)
	except Exception as exc:  # noqa: BLE001 - a read-only planner must not become an incident
		summary["ok"] = False
		summary["reason"] = f"{type(exc).__name__}: {exc}"
	return summary


def _plan_rooms(names: list[str], retention_days: int, *, limit: int) -> dict[str, Any]:
	filters: dict[str, Any] = {"is_archived": 0}
	if names:
		filters["name"] = ["in", names]
	rooms = frappe.get_all(
		ROOM_DOCTYPE,
		filters=filters,
		fields=["name", "last_message"],
		order_by="name asc",
		limit=limit,
	)

	keep_tombstones = bool(_setting_int("keep_tombstones_forever"))
	cutoff = add_to_date(now_datetime(), days=-retention_days) if retention_days > 0 else None
	now = now_datetime()

	eligible = 0
	held = 0
	holds: dict[str, int] = {}
	ranges: dict[str, list[int]] = {}

	for room in rooms:
		room_name = str(room.get("name"))
		messages = _messages(room_name)
		if not messages:
			continue
		open_jobs = _open_relay_targets(room_name)
		live_reply_roots = _live_reply_roots(room_name)
		last_message = str(room.get("last_message") or "")

		for message in messages:
			name = str(message.get("name"))
			reasons = purge_rules.holds(
				{
					"age_days": _age_days(message.get("creation"), now),
					"retention_days": retention_days,
					"open_relay_jobs": 1 if name in open_jobs else 0,
					"is_deleted": bool(message.get("is_deleted")),
					"keep_tombstones": keep_tombstones,
					"live_replies": 1 if name in live_reply_roots else 0,
					"is_room_last_message": name == last_message,
					# Every message is covered by the blocker today; stated per message
					# rather than globally so the day the retirement path lands, this
					# becomes a real per-span question and nothing else has to change.
					"derived_blocked": bool(purge_rules.blocked_doctypes()),
				}
			)
			if reasons:
				held += 1
				for reason in reasons:
					holds[reason] = holds.get(reason, 0) + 1
				continue
			eligible += 1
			seq = cint(message.get("seq"))
			span = ranges.setdefault(room_name, [seq, seq])
			span[0] = min(span[0], seq)
			span[1] = max(span[1], seq)

	return {
		"rooms_examined": len(rooms),
		"eligible": eligible,
		"held": held,
		"holds": holds,
		"ranges": {room: {"first_seq": lo, "last_seq": hi} for room, (lo, hi) in ranges.items()},
		"cutoff": str(cutoff or ""),
	}


def _messages(room: str) -> list[dict[str, Any]]:
	"""Identifiers, states and timestamps for one room. **No body column, ever.**

	A planner that read ``text`` would be a second unaudited transcript reader, and it would
	need to justify itself to the raw-SQL guard on grounds it could not meet.
	"""
	return frappe.get_all(
		MESSAGE_DOCTYPE,
		filters={"room": room},
		fields=["name", "seq", "creation", "is_deleted", "thread_root"],
		order_by="seq asc",
		limit=_PER_ROOM_LIMIT,
	)


def _open_relay_targets(room: str) -> set[str]:
	"""Messages with outstanding outbound work, as a set.

	``reference_doctype`` is matched with the empty string included because the relay worker
	treats an absent value as still meaning ``Chat Message`` — reading it strictly here would
	call a message with live work eligible, which is the one hold that cannot be got wrong.
	"""
	from erpnext_enhancements.chat.doctype.chat_relay_job import chat_relay_job

	rows = frappe.get_all(
		RELAY_JOB_DOCTYPE,
		filters={
			"room": room,
			"status": ["in", list(chat_relay_job.OPEN_STATUSES)],
			"reference_doctype": ["in", ["", MESSAGE_DOCTYPE]],
		},
		fields=["reference_name"],
		limit=_PER_ROOM_LIMIT,
	)
	return {str(r.get("reference_name")) for r in rows if r.get("reference_name")}


def _live_reply_roots(room: str) -> set[str]:
	"""Thread roots that still have an undeleted reply.

	``thread_root`` is a Link, so purging a root out from under live replies orphans the
	thread — and the replies are inside the retention window by definition, or they would be
	in the batch themselves.
	"""
	rows = frappe.get_all(
		MESSAGE_DOCTYPE,
		filters={"room": room, "is_deleted": 0, "thread_root": ["is", "set"]},
		fields=["thread_root"],
		limit=_PER_ROOM_LIMIT,
	)
	return {str(r.get("thread_root")) for r in rows if r.get("thread_root")}


def _age_days(value: Any, now: Any) -> float | None:
	if not value:
		return None
	try:
		return (now - get_datetime(value)).total_seconds() / 86400.0
	except Exception:  # noqa: BLE001
		return None


def _record(summary: dict[str, Any]) -> str | None:
	"""The ``retention_run`` row — the last declared event type without a writer.

	**Counts and ranges, never content.** ``audit.record_governance_event``'s own docstring
	states the rule this obeys: *a log of what was deleted that contains what was deleted has
	not deleted anything.* Nothing was deleted here either, and the row says so in
	``event_type`` terms by carrying ``mode: plan``.
	"""
	ranges = summary.get("ranges") or {}
	first = min((r["first_seq"] for r in ranges.values()), default=0)
	last = max((r["last_seq"] for r in ranges.values()), default=0)
	return audit.record_governance_event(
		event_type="retention_run",
		actor=frappe.session.user or "Administrator",
		detail=json.dumps(
			{
				"mode": "plan",
				"destroyed": 0,
				"eligible": summary.get("eligible"),
				"held": summary.get("held"),
				"holds": summary.get("holds"),
				"rooms_examined": summary.get("rooms_examined"),
				"retention_days": summary.get("retention_days"),
				"blocked": summary.get("blocked"),
			},
			sort_keys=True,
		)[:1000],
		affected_count=cint(summary.get("eligible")),
		first_seq=cint(first),
		last_seq=cint(last),
	)


def _setting_int(field: str, default: int = 0) -> int:
	try:
		return cint(frappe.db.get_single_value("Chat Settings", field))
	except Exception:  # noqa: BLE001
		return default


def report(rooms: str = "", limit: int = 0) -> None:
	"""Print the plan. **Leads with what is held back, and ends with the blocker.**

	Deliberately not "4,812 messages are eligible" as a headline: that is the report that
	invites the answer "so turn it on", and every unsafe assumption in a retention rule lives
	in the holds rather than in the count.
	"""
	summary = plan(rooms=rooms, limit=limit)
	lines: list[str] = ["=" * 78, "chat retention — what a purge WOULD do. Nothing here deletes.", "=" * 78]

	if not summary.get("ok"):
		lines.append(f"  [ALARM] the planner failed: {summary.get('reason')}")
	if summary.get("reason") and summary.get("ok"):
		lines.append(f"  {summary['reason']}")

	days = summary.get("retention_days") or 0
	lines.append("")
	lines.append(
		f"  message_retention_days = {days}"
		+ ("  (0 = keep forever, decision D-6, and the shipped state)" if not days else "")
	)
	lines.append(f"  rooms examined         = {summary.get('rooms_examined', 0)}")
	lines.append("")
	lines.append(f"  HELD BACK  {summary.get('held', 0)}")
	for reason, count in sorted((summary.get("holds") or {}).items(), key=lambda kv: -kv[1]):
		lines.append(f"    {count:>8}  {reason}")
	lines.append("")
	lines.append(f"  would be destroyed  {summary.get('eligible', 0)}")

	blocker = summary.get("blocker") or ""
	lines.append("")
	lines.append("-" * 78)
	if blocker:
		lines.append("WHY THE DESTRUCTIVE PATH IS NOT BUILT")
		lines.append("")
		for chunk in _wrap(blocker, 74):
			lines.append(f"  {chunk}")
		lines.append("")
		for doctype in summary.get("blocked") or []:
			reason = purge_rules.DISPOSITION[doctype][1]
			lines.append(f"  {doctype}:")
			for chunk in _wrap(reason, 70):
				lines.append(f"      {chunk}")
	else:
		lines.append("the derived-artefact blocker has cleared; the purge can now be written")

	print("\n".join(lines))


def _wrap(text: str, width: int) -> list[str]:
	out: list[str] = []
	line = ""
	for word in str(text or "").split():
		if len(line) + len(word) + 1 > width:
			out.append(line)
			line = word
		else:
			line = f"{line} {word}".strip()
	if line:
		out.append(line)
	return out
