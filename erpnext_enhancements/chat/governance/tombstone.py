# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Seeing through a delete. Phase 6 §4.E — and the third declared-but-unwritten event.

`Chat Audit Log` has declared ``tombstone_expanded`` since v1.285.0 with nothing able to
produce it. This produces it.

--------------------------------------------------------------------------------------
What a delete actually does here, because the prompt and the schema disagree
--------------------------------------------------------------------------------------

Phase 6's prompt says a user-facing delete *moves* the body off the live row and clears it.
**It does not.** Divergence D4: ``is_deleted = 1`` and the body **stays** on
``Chat Message.text`` — argued in five places in the ADR, and visible in
``api/compose.py``'s own comment. What changes is who can see it:
``_common.message_payload`` strips the text on read and ``history`` filters the row out.

That distinction is the whole design of this module. The body is not somewhere else needing
restoration; it is right there, one column away, behind a read path that declines to return
it. So "expanding a tombstone" is not a recovery operation — **it is a decision to look**,
and the only thing that makes it different from an ordinary read is that somebody is
accountable for it afterwards.

Which is why the audit row is written first and a failure to write it **refuses the read**.
That is the same trade `download_export` makes, and the opposite of what the first version of
`request_export` did — `record_governance_event` swallows its failures and returns ``None``,
so a caller that ignores the return has assumed a record exists that does not.

--------------------------------------------------------------------------------------
The trail, not just the body
--------------------------------------------------------------------------------------

An expand returns the edit history too. A message deleted after three edits has four bodies
in its past, and handing over only the last one answers "what did it say" with the version
that happened to be current when somebody removed it — which is the least interesting of the
four and reads as though it were the whole story.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.rate_limiter import rate_limit

from erpnext_enhancements.chat import audit, permissions
from erpnext_enhancements.chat.governance import access_report

#: Fields returned for the deleted message itself. An allowlist, like the export's: a column
#: added next year is not automatically something an auditor sees through a tombstone.
MESSAGE_FIELDS = (
	"name",
	"room",
	"seq",
	"sender",
	"sender_email",
	"text",
	"is_edited",
	"is_deleted",
	"deleted_at",
	"deleted_by",
	"deletion_source",
	"thread_root",
	"creation",
)

REVISION_FIELDS = (
	"name",
	"revision_no",
	"change_type",
	"origin",
	"origin_timestamp",
	"actor",
	"actor_email",
	"text_before",
	"text_after",
)


class TombstoneRefused(frappe.ValidationError):
	"""Refused before any body was read."""


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=3600, methods=["POST"])
def expand(message: str = "", reason: str = "", reason_category: str = "") -> dict[str, Any]:
	"""Return a deleted message's original text and its full revision trail.

	POST, because the reason travels in the body and a reason in a query string lands in the
	access log, the browser history and the next ``Referer`` header.

	**Only a deleted message.** A live one is readable through the ordinary oversight path,
	which has its own audit row; accepting either here would mean ``tombstone_expanded``
	sometimes meant "saw through a delete" and sometimes meant "read a message", and an event
	type with two meanings is one nobody can count.
	"""
	user = _require_auditor()
	cleaned_reason, category = _require_reason(reason, reason_category)

	name = str(message or "").strip()
	if not name:
		raise TombstoneRefused(frappe._("Name the message to expand."))

	row = _message(name)
	# One uniform refusal for missing and not-deleted. Distinguishing them would answer
	# "does this message exist" to somebody who has not yet paid for an answer.
	if not row or not row.get("is_deleted"):
		raise TombstoneRefused(
			frappe._(
				"That message is not a deleted one. A live message is read through the "
				"oversight viewer, which records it there."
			)
		)

	revisions = _revisions(name)

	recorded = audit.record_governance_event(
		event_type="tombstone_expanded",
		actor=user,
		subject_user=str(row.get("sender_email") or row.get("sender") or ""),
		room=str(row.get("room") or ""),
		reference_doctype="Chat Message",
		reference_name=name,
		reason=cleaned_reason,
		detail=json.dumps(
			{
				"reason_category": category,
				"deleted_by": row.get("deleted_by"),
				"deletion_source": row.get("deletion_source"),
				"revision_count": len(revisions),
			},
			sort_keys=True,
		)[:1000],
		affected_count=1,
		first_seq=int(row.get("seq") or 0),
		last_seq=int(row.get("seq") or 0),
	)
	if not recorded:
		# Fail closed. `record_governance_event` swallows its own failures and returns None,
		# so a caller that ignores the return has assumed a record that does not exist — and
		# an unrecorded look through a tombstone is the one act this module exists to make
		# impossible.
		raise TombstoneRefused(frappe._("This expansion could not be recorded, so it was refused."))

	return {
		"message": {key: _clean(row.get(key)) for key in MESSAGE_FIELDS},
		"revisions": [{key: _clean(r.get(key)) for key in REVISION_FIELDS} for r in revisions],
		"audit_row": recorded,
		"reason_category": category,
	}


def _clean(value: Any) -> Any:
	if value is None or isinstance(value, str | int | float | bool):
		return value
	return str(value)


def _message(name: str) -> dict[str, Any] | None:
	"""The deleted row, body included.

	No membership filter, and that is the point rather than an omission: the auditor is not a
	participant, and filtering by membership would return nothing and look correct doing it.
	The gate is the role and the reason, both checked above, and the price is the audit row.
	"""
	return frappe.db.get_value("Chat Message", name, list(MESSAGE_FIELDS), as_dict=True)


def _revisions(message: str) -> list[dict[str, Any]]:
	"""The whole trail, oldest first.

	A ``Create`` row is written at insert, so the trail is complete from the beginning —
	reconstructing an original body from the first edit's ``text_before`` works right up
	until the first edit that was itself lost.
	"""
	return frappe.db.sql(
		f"""
		select {", ".join(f"`{f}`" for f in REVISION_FIELDS)}
		from `tabChat Message Revision`
		where `message` = %(message)s
		order by `revision_no` asc
		""",
		{"message": message},
		as_dict=True,
	)


def _require_auditor() -> str:
	user = frappe.session.user
	if not user or user == "Guest":
		raise TombstoneRefused(frappe._("Seeing through a delete requires a signed-in user."))
	if not permissions._has_oversight(user):
		raise TombstoneRefused(
			frappe._(
				"Seeing through a delete requires the role named in Chat Settings → Admin "
				"Oversight Role. That field ships blank, so this fails closed until somebody "
				"configures it."
			)
		)
	return user


def _require_reason(reason: str | None, category: str | None) -> tuple[str, str]:
	"""The same grader the viewer, the export and the compliance report use.

	One threshold across all four. Two would admit an expansion at the door that the report
	then marks non-compliant, which reads as tampering rather than as components disagreeing.
	"""
	cleaned = (reason or "").strip()
	grade = access_report.reason_quality(cleaned)
	if grade != access_report.REASON_OK:
		raise TombstoneRefused(
			frappe._(
				"Seeing through a delete needs a stated reason of at least {0} characters "
				"that says something ({1}). Somebody chose to remove this text; the reason is "
				"what makes overriding that defensible later."
			).format(access_report.REASON_MIN_LENGTH, grade)
		)
	normalised = audit.normalise_reason_category(category)
	if not normalised:
		raise TombstoneRefused(frappe._("Choose a reason category."))
	return cleaned, normalised
