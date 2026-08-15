# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The purge. Phase 6 §4.F — the only code in this system that deliberately destroys a message.

	bench --site <site> execute erpnext_enhancements.chat.governance.purge.report
	bench --site <site> execute erpnext_enhancements.chat.governance.purge.run_purge \\
		--kwargs "{'dry_run': 0}"

Everything else in §4.F exists so that this can be written safely: :mod:`purge_rules` decides
what may go, :mod:`retention` reports what would, :mod:`chat.retire_rules` and
:mod:`chat.indexing.retire` retire the derived coverage. **Read those first.** This module is
the ordering and the deletion, and almost all of its content is the ordering.

--------------------------------------------------------------------------------------
The order, and what each step is protecting against
--------------------------------------------------------------------------------------

**1. The audit row, before anything is destroyed, and a failed write refuses the purge.**
``outbox.refuse_hard_delete``'s own docstring names this: the escape hatch exists for *"the
Phase 6 retention/erasure path, which has to write its audit row first"*.
``record_governance_event`` swallows its failures and returns ``None``, so a caller that
ignores the return has assumed a record that does not exist — the mistake the first version of
``request_export`` made, and here the consequence is bodies destroyed with nothing saying so.

**2. The message, then its sidecars, in that order, in one transaction per message.** The
reverse — sidecars first — is what a review of the earlier design killed, and the reason is
asymmetric recoverability. ``delete_doc`` can raise on lock contention, so a message delete is
*allowed to fail*; if its revisions and attachments are already gone, that leaves a **live
message stripped of the only copies of its superseded bodies**, and nothing can put them back.
An orphaned revision, by contrast, is a row whose message no longer exists — findable and
removable. Fail toward the state you can repair.

**3. The retirement mark last, and only over the contiguous purged prefix.**
``retire.set_retirement_mark`` refuses unless every message at or below the mark is gone, which
is exactly right and means the mark cannot simply be the batch's high seq: a message held back
by an open relay job or a live thread reply is still there. So the mark advances to *one below
the lowest surviving seq*, and stops. A room with a held message at seq 40 retires to 39 no
matter how much above 40 was purged, and the rest follows on a later run once the hold clears.

--------------------------------------------------------------------------------------
Three framework facts, read from Frappe's source rather than assumed
--------------------------------------------------------------------------------------

* **``delete_permanently=True`` is mandatory.** Without it ``delete_doc`` calls
  ``add_to_deleted_document``, which stores ``doc.as_json()`` — the whole message, body
  included — in ``tabDeleted Document``. A purge without that flag reports success and moves
  every body to another table. This is the single likeliest way to ship a retention feature
  that retains everything.
* **``ignore_links`` is not a parameter.** The signature has ``ignore_doctypes``; passing
  ``ignore_links`` is a ``TypeError``, not a no-op. ``force=True`` is the only link bypass.
* **``delete_doc`` takes ``for_update=True, wait=False``**, so it raises on lock contention
  rather than waiting. That is why this commits per message and does not hold a room lock
  across the batch: the lock is transaction-scoped and would be released by the first commit
  anyway, so holding it would buy the guarantee for exactly one message.

--------------------------------------------------------------------------------------
It ships disabled and stays disabled
--------------------------------------------------------------------------------------

``message_retention_days`` defaults to ``0``, which means never, and the gate refuses on it
before reading anything. Decision D-6 is *keep forever*. Nothing here runs on any site until
somebody types a number into that field, and there is no scheduler entry — a job that destroys
conversation on a timer is not something to add and then remember to think about.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import cint, now_datetime

from erpnext_enhancements.chat import audit
from erpnext_enhancements.chat.governance import purge_rules, retention

ROOM_DOCTYPE = "Chat Room"
MESSAGE_DOCTYPE = "Chat Message"
REVISION_DOCTYPE = "Chat Message Revision"
ATTACHMENT_DOCTYPE = "Chat Attachment"
FILE_DOCTYPE = "File"

#: Messages destroyed per room per run. Small on purpose: a purge is not in a hurry, and a
#: smaller batch means an interrupted run has done less and the next one recomputes
#: eligibility from data that has moved on.
MESSAGES_PER_ROOM = 200

#: Rooms per run.
ROOMS_PER_RUN = 25


class PurgeRefused(frappe.ValidationError):
	"""Refused before anything was destroyed."""


def run_purge(dry_run: int = 1, rooms: str = "", limit: int = 0) -> dict[str, Any]:
	"""Destroy what retention says may go. **Defaults to a dry run.**

	``dry_run`` defaults to 1 rather than 0, and that is not politeness: the difference between
	the two is irreversible, and the shape of a mistake here is somebody running the obvious
	incantation to *see what it would do*.
	"""
	summary: dict[str, Any] = {
		"ok": True,
		"dry_run": bool(cint(dry_run)),
		"ran_at": str(now_datetime()),
		"rooms": 0,
		"destroyed": 0,
		"held": 0,
		"retired_to": {},
		"reason": "",
	}
	try:
		refusal = _gate()
		if refusal:
			summary["ok"] = False
			summary["reason"] = refusal
			return summary

		plan = retention.plan(rooms=rooms, limit=limit)
		summary["held"] = cint(plan.get("held"))
		if not plan.get("ok"):
			summary["ok"] = False
			summary["reason"] = str(plan.get("reason") or "the retention planner failed")
			return summary

		if summary["dry_run"]:
			summary["destroyed"] = cint(plan.get("eligible"))
			summary["reason"] = "dry run; nothing was destroyed"
			return summary

		for room in _rooms(rooms, limit):
			result = _purge_room(room)
			if result["destroyed"]:
				summary["rooms"] += 1
				summary["destroyed"] += result["destroyed"]
			if result.get("retired_to"):
				summary["retired_to"][room] = result["retired_to"]
	except Exception as exc:  # noqa: BLE001 - a destroying job must not also become an incident
		summary["ok"] = False
		summary["reason"] = f"{type(exc).__name__}: {exc}"
		frappe.db.rollback()
	return summary


def _gate() -> str:
	"""``""`` to proceed, or the reason not to. Every door, in the cheapest order."""
	if not _setting("enabled"):
		return "chat is disabled"
	days = _setting("message_retention_days")
	if days <= 0:
		return (
			"message_retention_days is 0, which means keep forever (decision D-6). Nothing is "
			"eligible and nothing will be destroyed until somebody changes that field."
		)
	ok, why = purge_rules.can_enable()
	if not ok:
		return why
	return ""


def _setting(field: str) -> int:
	try:
		return cint(frappe.db.get_single_value("Chat Settings", field))
	except Exception:  # noqa: BLE001
		return 0


def _rooms(rooms: str, limit: int) -> list[str]:
	names = [r.strip() for r in str(rooms or "").split(",") if r.strip()]
	filters: dict[str, Any] = {"is_archived": 0}
	if names:
		filters["name"] = ["in", names]
	return [
		str(r["name"])
		for r in frappe.get_all(
			ROOM_DOCTYPE,
			filters=filters,
			fields=["name"],
			order_by="name asc",
			limit=cint(limit) or ROOMS_PER_RUN,
		)
	]


# --- one room --------------------------------------------------------------------


def _purge_room(room: str) -> dict[str, Any]:
	"""Destroy this room's eligible messages, then retire what that makes retirable.

	Returns ``{"destroyed": n, "retired_to": seq}``. Never raises past the caller's handler.
	"""
	candidates = _eligible(room)
	if not candidates:
		return {"destroyed": 0, "retired_to": 0}

	recorded = audit.record_governance_event(
		event_type="retention_run",
		actor=frappe.session.user or "Administrator",
		room=room,
		detail=json.dumps(
			{
				"mode": "purge",
				"eligible": len(candidates),
				"retention_days": _setting("message_retention_days"),
			},
			sort_keys=True,
		)[:1000],
		affected_count=len(candidates),
		first_seq=candidates[0]["seq"],
		last_seq=candidates[-1]["seq"],
	)
	if not recorded:
		# Fail closed. `outbox.refuse_hard_delete`'s own docstring says this path "has to
		# write its audit row first", and `record_governance_event` swallows its failures and
		# returns None — so a caller that ignores the return has assumed a record that does
		# not exist. Bodies destroyed with nothing saying so is the one outcome this whole
		# phase exists to prevent.
		raise PurgeRefused(frappe._("The retention run could not be recorded, so nothing was destroyed."))

	destroyed = 0
	for row in candidates:
		if _destroy(row["name"]):
			destroyed += 1
			frappe.db.commit()
		else:
			frappe.db.rollback()

	return {"destroyed": destroyed, "retired_to": _retire(room)}


def _eligible(room: str) -> list[dict[str, Any]]:
	"""This room's purgeable messages, oldest first, bounded.

	The eligibility rule is :func:`purge_rules.holds` and is not restated here — one place
	decides, and the planner and the purge ask the same question so their answers cannot
	disagree.
	"""
	plan = retention.plan(rooms=room, limit=1)
	if not plan.get("ok") or not plan.get("eligible"):
		return []

	span = (plan.get("ranges") or {}).get(room) or {}
	low, high = cint(span.get("first_seq")), cint(span.get("last_seq"))
	if not high:
		return []

	rows = frappe.get_all(
		MESSAGE_DOCTYPE,
		filters={"room": room, "seq": ["between", [low, high]]},
		fields=["name", "seq"],
		order_by="seq asc",
		limit=MESSAGES_PER_ROOM,
	)
	return [dict(r) for r in rows]


def _destroy(message: str) -> bool:
	"""One message and its sidecars, in one transaction. ``True`` if it went.

	**The message first, its sidecars second**, and the order is the whole point:
	``delete_doc`` can raise on lock contention, so this is allowed to fail — and failing with
	the sidecars already gone would leave a live message stripped of the only copies of its
	superseded bodies, which nothing can put back. An orphaned revision is a row whose message
	no longer exists, which is findable and removable. Fail toward the repairable state.
	"""
	try:
		frappe.delete_doc(
			MESSAGE_DOCTYPE,
			message,
			force=True,
			delete_permanently=True,
			ignore_permissions=True,
			# `update_flags` runs before `on_trash`, so this legitimately satisfies
			# `outbox.refuse_hard_delete` rather than skipping it. NOT `ignore_on_trash`,
			# which would skip the hook for every doctype in the call and for anything a
			# future maintainer writes into it.
			flags={"chat_allow_hard_delete": True},
		)
	except Exception:  # noqa: BLE001 - contention is expected; the next run retries
		return False

	_destroy_sidecars(message)
	return True


def _destroy_sidecars(message: str) -> None:
	"""Revisions and attachments, after the message they belong to is already gone.

	Attachment bytes go too. ``Chat Attachment.file`` is a ``File`` docname, so deleting the
	attachment row alone would leave the bytes on disk reachable by nothing —
	``sync/attachments.download`` resolves by attachment name and there is no other door, so
	they would be unreadable *and* undeleted, which is the worst of both.
	"""
	# A filtered delete with **no preceding read**, deliberately. `Chat Message Revision` is
	# where superseded and deleted bodies live — the one table where a body survives the
	# user's own decision to delete it — so `test_chat_rawsql_guard` allows exactly two
	# functions to query it and offers no exemption mechanism for a third. Nothing here needs
	# the rows: they are being destroyed, not inspected, and reading them first would pull the
	# bodies into a local variable for no reason at all.
	frappe.db.delete(REVISION_DOCTYPE, {"message": message})

	for row in frappe.get_all(
		ATTACHMENT_DOCTYPE, filters={"message": message}, fields=["name", "file"], limit=1_000
	):
		file_name = str(row.get("file") or "")
		frappe.db.delete(ATTACHMENT_DOCTYPE, {"name": row["name"]})
		if file_name:
			try:
				frappe.delete_doc(
					FILE_DOCTYPE,
					file_name,
					force=True,
					delete_permanently=True,
					ignore_permissions=True,
				)
			except Exception:  # noqa: BLE001 - a missing File is already the desired state
				pass


def _retire(room: str) -> int:
	"""Advance the retirement mark over the contiguous purged prefix. Returns the new mark.

	**Only the contiguous prefix**, and that is forced by
	:func:`chat.indexing.retire.set_retirement_mark`, which refuses unless *every* message at
	or below the mark is gone. A message held back by an open relay job or a live thread reply
	is still there, so the mark stops one below the lowest survivor — no matter how much above
	it was purged. The rest follows on a later run once the hold clears.

	Retirement is deliberately last. Doing it first would delete the derived coverage of
	messages that still exist, which is the mutilation the writer's own refusal exists to
	prevent.
	"""
	from erpnext_enhancements.chat.indexing import retire

	lowest = frappe.get_all(
		MESSAGE_DOCTYPE, filters={"room": room}, fields=["seq"], order_by="seq asc", limit=1
	)
	if not lowest:
		# Every message is gone. The mark may go to the room's high water.
		through = cint(frappe.db.get_value("Chat Room", room, "seq_high_water"))
	else:
		through = cint(lowest[0]["seq"]) - 1

	if through <= 0:
		return 0
	try:
		result = retire.set_retirement_mark(room, through)
		return cint(result.get("effective"))
	except Exception:  # noqa: BLE001 - a refusal here is correct and must not lose the purge
		return 0


def report(rooms: str = "", limit: int = 0) -> None:
	"""Print what a purge would destroy. Prints rather than returns, and writes nothing."""
	summary = run_purge(dry_run=1, rooms=rooms, limit=limit)
	lines = [
		"=" * 74,
		"chat purge — DRY RUN. Nothing has been destroyed.",
		"=" * 74,
	]
	if not summary["ok"]:
		lines.append(f"  REFUSED: {summary['reason']}")
	else:
		lines.append(f"  would destroy   {summary['destroyed']}")
		lines.append(f"  held back       {summary['held']}")
		lines.append("")
		lines.append("  To actually destroy, pass dry_run=0. That is irreversible.")
	print("\n".join(lines))
