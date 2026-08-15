# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The writer for ``Chat Room.retired_below_seq``. Phase 6 §4.F.

	bench --site <site> execute erpnext_enhancements.chat.indexing.retire.set_retirement_mark \\
		--kwargs "{'room': 'abc123', 'through_seq': 4200}"

v1.295.0 shipped the arithmetic (:mod:`chat.retire_rules`), v1.297.0 gave it a column and
made every consumer honour it. This is what moves it, and what deletes the derived coverage
the move retires.

--------------------------------------------------------------------------------------
The refusal that makes this safe to have at all
--------------------------------------------------------------------------------------

**It refuses unless the messages at or below the mark are already gone.**

That is not belt-and-braces, it is the whole safety argument. The mark means *"every message
at or below this seq is gone forever"*, and every consumer acts on that claim: the chunk
sweep will never re-read below it, the digest sweep drops the room, the retrieval gate stops
serving anything that covers it. Set it over messages that still **exist** and you have not
retired anything — you have made a stretch of live conversation permanently invisible to
Triton while it sits there, readable, in the SPA. Nobody would report that as a bug; they
would report that the assistant "doesn't know about" a conversation, and the cause would be
a column nobody thought to look at.

So the order is forced rather than documented: **destroy first, then retire.** A purge
computes the effective mark, deletes the messages at or below it, and only then calls this —
which verifies the deletion actually happened before it writes anything. There is no argument
that bypasses the check and no flag to skip it, because the case for skipping it is always
"I know they are gone", which is exactly the belief the check exists to test.

--------------------------------------------------------------------------------------
Why the snap happens here rather than in the caller
--------------------------------------------------------------------------------------

:func:`chat.retire_rules.snap_to_chunk_boundary` lowers a requested mark to clear any chunk
straddling it, because deleting a straddler would destroy retrieval coverage of the surviving
messages in its upper half — permanently, since nothing rebuilds a mid-range hole.

Doing that here, and returning ``held_back``, means a caller cannot forget it. A purge that
snapped in its own code and then deleted messages up to the *unsnapped* seq would destroy
exactly the messages the snap was protecting. So this function is also the thing a purge asks
*"how far may I actually go?"* — see :func:`plan_retirement`, which answers without writing.

--------------------------------------------------------------------------------------
What it deletes, and what it deliberately does not
--------------------------------------------------------------------------------------

Chunks wholly at or below the mark, and any digest whose ``covered_from`` intersects it.
Nothing else — no messages, no revisions, no attachments, no audit rows. Those are the purge's
business and are classified in ``chat/governance/purge_rules.py``.

Deleting a digest is safe in the specific way deleting a chunk is not: the digest's own
watermark lives on the row being deleted, so removing it retreats nothing and manufactures no
new copy. The room is simply re-selected once, and either a correct digest is written from
what survives or none exists.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import cint, now_datetime

from erpnext_enhancements.chat import retire_rules

ROOM_DOCTYPE = "Chat Room"
MESSAGE_DOCTYPE = "Chat Message"
CHUNK_DOCTYPE = "Chat Context Chunk"
ROOM_DIGEST_DOCTYPE = "Chat Room Digest"
THREAD_DIGEST_DOCTYPE = "Chat Thread Digest"

#: Rooms reconciled per sweep pass. A bound on a read, not a cap on destruction — the sweep
#: only finishes work a previous call already authorised.
ROOMS_PER_PASS = 50


class RetirementRefused(frappe.ValidationError):
	"""Refused before anything was written or deleted."""


def plan_retirement(room: str, through_seq: int) -> dict[str, Any]:
	"""How far the mark may actually move, and what it would remove. **Writes nothing.**

	The question a purge has to ask *before* it deletes messages, because the snap decides
	which messages it is allowed to delete. Deleting up to the requested seq and then
	retiring only to the snapped one would destroy the very messages the snap protects.
	"""
	current, high_water = _room_marks(room)
	effective, held_back = retire_rules.snap_to_chunk_boundary(through_seq, _sealed_spans(room))

	refusal = retire_rules.refuse_lowering(current, effective)
	blocking = 0 if refusal else _messages_at_or_below(room, effective)

	return {
		"room": room,
		"requested": cint(through_seq),
		"effective": effective,
		"held_back": held_back,
		"current": current,
		"seq_high_water": high_water,
		"messages_still_present": blocking,
		"refusal": refusal
		or (
			_still_present_refusal(blocking, effective)
			if blocking
			else _over_high_water_refusal(effective, high_water)
		),
	}


def set_retirement_mark(room: str, through_seq: int) -> dict[str, Any]:
	"""Advance the mark and delete the derived coverage it retires. **Raises on refusal.**

	One room, one transaction. The caller commits — a purge that has just deleted messages
	wants the retirement in the same transaction as the deletion, so an interrupted run cannot
	leave messages destroyed with their coverage still servable.
	"""
	plan = plan_retirement(room, through_seq)
	if plan["refusal"]:
		raise RetirementRefused(frappe._(plan["refusal"]))

	effective = cint(plan["effective"])
	if effective <= retire_rules.NOT_RETIRED:
		return {**plan, "chunks_deleted": 0, "room_digests_deleted": 0, "thread_digests_deleted": 0}

	frappe.db.set_value(ROOM_DOCTYPE, room, "retired_below_seq", effective, update_modified=False)

	removed = {
		"chunks_deleted": _delete_chunks(room, effective),
		"room_digests_deleted": _delete_room_digests(room, effective),
		"thread_digests_deleted": _delete_thread_digests(room, effective),
	}
	_record(room, plan, removed)
	return {**plan, **removed}


def sweep_retirement(limit: int = 0) -> dict[str, Any]:
	"""Finish any retirement whose deletes did not happen. Scheduler job.

	It exists for two cases, and the second is why it carries **no ``is_archived`` filter** —
	the only sweep in this package that does not:

	* the mark was moved by something other than :func:`set_retirement_mark`: a Desk save by a
	  System Manager, a patch, a data fix. The field is ``read_only`` on the DocField, which is
	  a form property and not a database constraint;
	* the room is **archived**. ``digest._dirty_rooms`` and ``indexer._rooms_needing_chunks``
	  both open ``where is_archived = 0``, so archiving a room is the single action that would
	  otherwise make its retired coverage permanent — nothing would ever revisit it.

	It never advances a mark and never deletes a message. It only completes deletions that an
	already-written mark authorises, so it cannot destroy anything a human did not ask for.
	"""
	summary: dict[str, Any] = {"rooms": 0, "chunks_deleted": 0, "digests_deleted": 0, "errors": 0}
	try:
		rooms = frappe.get_all(
			ROOM_DOCTYPE,
			filters={"retired_below_seq": [">", retire_rules.NOT_RETIRED]},
			fields=["name", "retired_below_seq"],
			order_by="modified asc",
			limit=cint(limit) or ROOMS_PER_PASS,
		)
	except Exception:  # noqa: BLE001 - a reconciler must not become an incident
		return {**summary, "errors": 1}

	for row in rooms:
		room = str(row.get("name"))
		mark = cint(row.get("retired_below_seq"))
		try:
			chunks = _delete_chunks(room, mark)
			digests = _delete_room_digests(room, mark) + _delete_thread_digests(room, mark)
			if chunks or digests:
				summary["rooms"] += 1
				summary["chunks_deleted"] += chunks
				summary["digests_deleted"] += digests
		except Exception:  # noqa: BLE001
			summary["errors"] += 1
			frappe.db.rollback()
	frappe.db.commit()
	return summary


# --- the refusals ---------------------------------------------------------------


def _still_present_refusal(count: int, effective: int) -> str:
	return (
		f"{count} message(s) at or below seq {effective} still exist in this room. The mark "
		"asserts they are gone forever, and every consumer acts on that: the chunk sweep will "
		"never read below it, the digest sweep drops the room, and the gate stops serving "
		"anything covering it. Setting it over live messages does not retire them — it makes a "
		"stretch of readable conversation permanently invisible to the assistant. Destroy them "
		"first, then retire."
	)


def _over_high_water_refusal(effective: int, high_water: int) -> str:
	if effective <= high_water:
		return ""
	return (
		f"retired_below_seq ({effective}) would exceed seq_high_water ({high_water}), which "
		"declares messages retired that were never allocated."
	)


# --- reads ----------------------------------------------------------------------


def _room_marks(room: str) -> tuple[int, int]:
	row = frappe.db.get_value(ROOM_DOCTYPE, room, ["retired_below_seq", "seq_high_water"], as_dict=True)
	if not row:
		raise RetirementRefused(frappe._("No such room."))
	return cint(row.get("retired_below_seq")), cint(row.get("seq_high_water"))


def _sealed_spans(room: str) -> list[tuple[int, int]]:
	"""``(first_seq, last_seq)`` per sealed chunk. What the snap measures against."""
	rows = frappe.get_all(
		CHUNK_DOCTYPE,
		filters={"room": room, "sealed": 1},
		fields=["first_seq", "last_seq"],
		limit=100_000,
	)
	return [(cint(r.get("first_seq")), cint(r.get("last_seq"))) for r in rows]


def _messages_at_or_below(room: str, effective: int) -> int:
	"""How many message rows still exist in the range the mark would claim is empty.

	**Existence, not `is_deleted`.** A tombstoned message is still a row holding its text —
	`is_deleted` hides it from the transcript and Phase 6's divergence D4 is explicit that the
	body stays. Counting only live rows here would let the mark be set over a room full of
	tombstones whose bodies an oversight expansion can still reveal, which is the opposite of
	retired.
	"""
	if effective <= retire_rules.NOT_RETIRED:
		return 0
	return cint(frappe.db.count(MESSAGE_DOCTYPE, {"room": room, "seq": ["<=", effective]}))


# --- deletes --------------------------------------------------------------------


def _delete_chunks(room: str, mark: int) -> int:
	"""Chunks wholly at or below the mark.

	``first_seq <= mark``, per :func:`retire_rules.wholly_retired` — not ``last_seq``. For a
	mark this module snapped the two are equivalent; for one set by hand they are not, and
	``last_seq`` would leave the straddling chunk in place holding the retired transcript
	verbatim, unreachable forever because the mark can never be lowered to re-snap it.
	"""
	names = frappe.get_all(
		CHUNK_DOCTYPE,
		filters={"room": room, "first_seq": ["<=", mark]},
		pluck="name",
		limit=100_000,
	)
	for name in names:
		frappe.db.delete(CHUNK_DOCTYPE, {"name": name})
	return len(names)


def _delete_room_digests(room: str, mark: int) -> int:
	names = frappe.get_all(
		ROOM_DIGEST_DOCTYPE,
		filters={"room": room, "covered_from": ["<=", mark]},
		pluck="name",
		limit=10_000,
	)
	for name in names:
		frappe.db.delete(ROOM_DIGEST_DOCTYPE, {"name": name})
	return len(names)


def _delete_thread_digests(room: str, mark: int) -> int:
	names = frappe.get_all(
		THREAD_DIGEST_DOCTYPE,
		filters={"room": room, "covered_from": ["<=", mark]},
		pluck="name",
		limit=10_000,
	)
	for name in names:
		frappe.db.delete(THREAD_DIGEST_DOCTYPE, {"name": name})
	return len(names)


# --- the record -----------------------------------------------------------------


def _record(room: str, plan: dict[str, Any], removed: dict[str, int]) -> None:
	"""A ``retention_run`` row, ``mode: retire``. Counts and a range, never content.

	The same event type the planner writes, deliberately: a retirement *is* a retention run —
	it is the half that destroys derived coverage — and giving it a second event type would
	split one question across two vocabularies.
	"""
	from erpnext_enhancements.chat import audit

	audit.record_governance_event(
		event_type="retention_run",
		actor=frappe.session.user or "Administrator",
		room=room,
		detail=json.dumps(
			{
				"mode": "retire",
				"requested": plan.get("requested"),
				"effective": plan.get("effective"),
				"held_back": plan.get("held_back"),
				**removed,
			},
			sort_keys=True,
		)[:1000],
		affected_count=sum(removed.values()),
		first_seq=0,
		last_seq=cint(plan.get("effective")),
	)


def report(room: str = "", through_seq: int = 0) -> None:
	"""Print what a retirement would do. ``bench execute``, prints rather than returns."""
	plan = plan_retirement(room, through_seq)
	lines = [
		"=" * 74,
		f"chat retirement plan — room {room}",
		"=" * 74,
		f"  requested through seq   {plan['requested']}",
		f"  effective (snapped)     {plan['effective']}",
		f"  held back by a chunk    {plan['held_back']}",
		f"  currently retired below {plan['current']}",
		f"  seq_high_water          {plan['seq_high_water']}",
		f"  messages still present  {plan['messages_still_present']}",
	]
	if plan["refusal"]:
		lines += ["", "  REFUSED:", f"    {plan['refusal']}"]
	else:
		lines += ["", "  would proceed. Nothing has been written."]
	print("\n".join(lines))
