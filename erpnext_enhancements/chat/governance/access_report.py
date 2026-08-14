"""The unified access report — **one** definition of a non-participant read.

Two audit tables exist and both are correct. `Chat Retrieval Audit` (Phase 5) records
anything that reached message content, with a child row per room carrying
``was_participant``. `Chat Audit Log` (Phase 6) records governance events — role grants,
tombstone expands, exports, retention runs — which are acts *about* the data rather than
reads *of* it.

ADR 0009 addendum 2 is explicit that neither migrates into the other and that no third
table appears. **Two tables is acceptable; two definitions of a read is not.** So this
module is the union, and everything downstream in this phase — the oversight viewer, the
cross-room search, the export bundle, and the employee-facing "who read my messages" view
— reads *here* rather than querying either table directly.

That indirection is the whole point. The acceptance bar (G6-7) is **exactly one** access
record per non-participant read via every path, and **zero** for a participant doing the
same thing. A consumer that queries `Chat Retrieval Audit` directly gets the parent row and
has to remember that a single row can span several rooms, only some of which the reader was
a member of — which is how "one read" quietly becomes "one row" and the count stops meaning
anything. Here, **the unit is (reader, room, occasion)**, because that is the unit the
policy is written in.

What this does not do
---------------------
It does not decide *whether* a read was allowed — that is the gate's job, upstream, and by
the time a row exists the content has already been returned. This is the record, not the
control.

It also does not enforce a reason. :func:`reason_quality` grades one and the report surfaces
the grade, but refusing a read for a bad reason has to land with the surface that can
*collect* one. Enforcing it here would fail `Administrator`'s existing reads — which are
privileged, live today, and have no field to type a reason into — and a governance control
whose first act is to break the admin is one that gets switched off. See
:func:`reason_quality` for the grading, and §4.B for where refusal belongs.
"""

from __future__ import annotations

from typing import Any

import frappe

from erpnext_enhancements.chat import audit

#: What an access row *is*. Content reads come from the retrieval audit; governance events
#: from the audit log. Both are "somebody touched this" and belong on one timeline.
KIND_CONTENT = "content_read"
KIND_GOVERNANCE = "governance"

#: Mirrors `Chat Audit Log`'s own rule rather than inventing a second number. A reason
#: shorter than this is not a reason, it is a keystroke.
REASON_MIN_LENGTH = 12

#: Re-exported, not redefined. The vocabulary lives in :mod:`chat.audit` beside the schema it
#: validates and the writer that normalises against it; a second copy here would be two
#: definitions of the same Select, which is the shape of defect this whole module exists to
#: remove one level up.
#:
#: **The list itself is a proposal flagged for confirmation.** Decision D-3 settled that the
#: subject sees a *category*; it did not settle *which* categories. Wrong now is cheap; wrong
#: once rows carry values is a data migration.
REASON_CATEGORIES = audit.REASON_CATEGORIES

#: Reasons that pass a length check and still say nothing. Compared against the reason
#: reduced to its letters, so "test", "TEST.", "t e s t" and "test test" all land here.
_PLACEHOLDERS = frozenset(
	{
		"test",
		"testing",
		"na",
		"n/a",
		"none",
		"nil",
		"tbd",
		"todo",
		"reason",
		"noreason",
		"asdf",
		"qwerty",
		"x",
		"foo",
		"bar",
		"check",
		"checking",
		"audit",
		"investigation",
	}
)

#: Grades :func:`reason_quality` can return. Only ``ok`` is compliant with §4.D.2.
REASON_OK = "ok"
REASON_MISSING = "missing"
REASON_TOO_SHORT = "too_short"
REASON_PLACEHOLDER = "placeholder"


def reason_quality(reason: str | None) -> str:
	"""Grade a reason against §4.D.2. Pure — no database, no session.

	Three failure modes, kept distinct because they need different fixes. ``missing`` is a
	caller that never collected one. ``too_short`` is a human in a hurry. ``placeholder`` is
	a human who worked out that the field is not read, which is the one that matters: it
	means the control has already decayed into a formality, and it is invisible to any check
	that only counts non-empty strings.

	Deliberately not a bool. "Is this reason acceptable" collapses the three into one and
	throws away the only information an operator could act on.
	"""
	text = (reason or "").strip()
	if not text:
		return REASON_MISSING
	if len(text) < REASON_MIN_LENGTH:
		# Measured after stripping, so padding a short word with spaces does not clear the
		# bar — "testing     " is seven characters of reason, whatever it is stored as.
		return REASON_TOO_SHORT

	letters = "".join(ch for ch in text.lower() if ch.isalpha())
	if not letters:
		# Punctuation and digits only — "............", "1234567890123".
		return REASON_PLACEHOLDER
	# A single character repeated to clear the length bar: "aaaaaaaaaaaa".
	if len(set(letters)) == 1:
		return REASON_PLACEHOLDER

	# Word-wise, not whole-string. Padding a placeholder with spaces fails the length check
	# above, so the way to clear both bars is to *repeat* it — "test test test", "n/a n/a
	# n/a n/a". Checking the reduced string as a single token missed exactly that, which is
	# the form somebody who has worked out the field is unread would actually type.
	words = {w for w in (_letters_only(w) for w in text.lower().split()) if w}
	if words and words <= _PLACEHOLDERS:
		return REASON_PLACEHOLDER
	return REASON_OK


def _letters_only(word: str) -> str:
	"""A word reduced to its letters, so "n/a," and "n/a" are the same token."""
	return "".join(ch for ch in word if ch.isalpha())


def redact_for_subject(row: dict[str, Any]) -> dict[str, Any]:
	"""The employee-facing projection of one access row.

	Three decisions from §A2.4 live here, together, because they only make sense together:

	* **The admin is named** (D-2). "An administrator read your messages" is the version
	  that generates a rumour; a name is the version that generates a conversation.
	* **The reason is the category, never the free text** (D-3). The free text is written
	  for a compliance reviewer and can name a third party or an open investigation — the
	  transparency view must not become the leak.
	* **Triton's reads are shown** (D-5), carrying ``actor_type`` so the caller can put them
	  in their own tab rather than burying admin reads under hundreds of assistant rows.

	Returns a **new** dict. Mutating in place would mean one caller's redaction silently
	applied to another's compliance view, which is the failure this function exists to
	prevent.
	"""
	category = (row.get("reason_category") or "").strip()
	return {
		"kind": row.get("kind"),
		"occurred_at": row.get("occurred_at"),
		"actor": row.get("actor"),
		"actor_type": row.get("actor_type"),
		"room": row.get("room"),
		"messages_read": row.get("messages_read"),
		# Absent rather than empty-string: a subject view that renders "" for a missing
		# category looks like a category called nothing. `None` renders as "Not recorded".
		"reason_category": category or None,
		"purpose": row.get("purpose"),
	}


def _content_rows(filters: dict[str, Any], limit: int) -> list[dict[str, Any]]:
	"""Non-participant content reads, one row per (audit row × room).

	The join is the point. `Chat Retrieval Audit` is one row per *request*; its child is one
	row per *room touched*. A request that reached four rooms, three of which the reader was
	in, is **one** non-participant read — and reporting the parent row would count it as
	four, or as one with the wrong room attached.

	``was_participant = 0`` is the filter that makes this the non-participant report, and it
	is applied in SQL rather than in Python so the ``limit`` bounds the answer rather than
	the candidate set.
	"""
	conditions = ["r.was_participant = 0"]
	values: dict[str, Any] = {"limit": limit}

	if filters.get("room"):
		conditions.append("r.room = %(room)s")
		values["room"] = filters["room"]
	if filters.get("actor"):
		conditions.append("a.accessed_by = %(actor)s")
		values["actor"] = filters["actor"]
	if filters.get("from_date"):
		conditions.append("a.recorded_at >= %(from_date)s")
		values["from_date"] = filters["from_date"]
	if filters.get("to_date"):
		conditions.append("a.recorded_at <= %(to_date)s")
		values["to_date"] = filters["to_date"]
	if filters.get("actor_type"):
		conditions.append("a.actor_type = %(actor_type)s")
		values["actor_type"] = filters["actor_type"]

	# Every fragment above is a literal; every value is bound. `chat/permissions.py` is not
	# consulted here on purpose — this report *is* the oversight surface, and its caller is
	# responsible for having checked the role. See the module docstring.
	rows = frappe.db.sql(
		f"""
		select
			a.name             as audit_name,
			a.accessed_by      as actor,
			a.actor_type       as actor_type,
			a.purpose          as purpose,
			a.recorded_at      as occurred_at,
			a.reason           as reason,
			a.reason_category  as reason_category,
			a.request_id       as request_id,
			r.room             as room,
			r.messages_read    as messages_read,
			r.first_seq        as first_seq,
			r.last_seq         as last_seq
		from `tabChat Retrieval Audit` a
		join `tabChat Retrieval Audit Room` r on r.parent = a.name
		where {' and '.join(conditions)}
		order by a.recorded_at desc
		limit %(limit)s
		""",
		values,
		as_dict=True,
	)
	for row in rows:
		row["kind"] = KIND_CONTENT
		row["reason_grade"] = reason_quality(row.get("reason"))
	return rows


def _governance_rows(filters: dict[str, Any], limit: int) -> list[dict[str, Any]]:
	"""Governance events, which are acts about the data rather than reads of it.

	Included in the same report because an auditor asking "what happened to this room" wants
	the export that left the building and the role that was granted in the same list as the
	transcripts that were read — and because a role grant is very often the *explanation* for
	the reads that follow it.
	"""
	conditions = ["1 = 1"]
	values: dict[str, Any] = {"limit": limit}

	if filters.get("room"):
		conditions.append("room = %(room)s")
		values["room"] = filters["room"]
	if filters.get("actor"):
		conditions.append("actor = %(actor)s")
		values["actor"] = filters["actor"]
	if filters.get("from_date"):
		conditions.append("recorded_at >= %(from_date)s")
		values["from_date"] = filters["from_date"]
	if filters.get("to_date"):
		conditions.append("recorded_at <= %(to_date)s")
		values["to_date"] = filters["to_date"]

	rows = frappe.db.sql(
		f"""
		select
			name            as audit_name,
			actor           as actor,
			event_type      as event_type,
			recorded_at     as occurred_at,
			reason          as reason,
			reason_category as reason_category,
			subject_user    as subject_user,
			room            as room,
			affected_count  as affected_count
		from `tabChat Audit Log`
		where {' and '.join(conditions)}
		order by recorded_at desc
		limit %(limit)s
		""",
		values,
		as_dict=True,
	)
	for row in rows:
		row["kind"] = KIND_GOVERNANCE
		row["actor_type"] = "Admin"
		row["reason_grade"] = reason_quality(row.get("reason"))
	return rows


def access_rows(
	*,
	room: str | None = None,
	actor: str | None = None,
	actor_type: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	include_governance: bool = True,
	limit: int = 200,
) -> list[dict[str, Any]]:
	"""The union, newest first. **The one definition every consumer in this phase reads.**

	Not whitelisted, and that is deliberate: it is a library call. The endpoints that expose
	it live with the viewer and the search, and each has its own role check and its own audit
	row — a read *of* the access report by a non-participant is itself a non-participant
	read, and giving this function an HTTP door of its own would create a path that records
	nothing.
	"""
	limit = max(1, min(int(limit or 200), 2000))
	filters = {
		"room": room,
		"actor": actor,
		"actor_type": actor_type,
		"from_date": from_date,
		"to_date": to_date,
	}

	rows = _content_rows(filters, limit)
	if include_governance and not actor_type:
		# `actor_type` filters the retrieval audit's Admin/Triton distinction, which governance
		# events do not carry. Applying it would silently drop every governance row rather
		# than filter it, so the filter and this section are mutually exclusive by construction.
		rows = rows + _governance_rows(filters, limit)

	rows.sort(key=lambda r: (r.get("occurred_at") or "", r.get("audit_name") or ""), reverse=True)
	return rows[:limit]


def subject_rows(*, subject: str, limit: int = 200) -> list[dict[str, Any]]:
	"""What one employee is shown about reads of *their* messages.

	Scoped by room membership rather than by message authorship, and that is the honest
	scope: a non-participant who read a room read the whole conversation, not just the lines
	the subject happened to write. Reporting only authored messages would understate it.

	Every row goes through :func:`redact_for_subject`. The caller cannot opt out — a
	transparency view that can be asked for the un-redacted reason is a compliance view with
	a different name.
	"""
	rooms = frappe.get_all(
		"Chat Room Member",
		filters={"user": subject},
		pluck="parent",
	)
	if not rooms:
		return []

	out: list[dict[str, Any]] = []
	for room in rooms:
		out.extend(access_rows(room=room, limit=limit))

	out.sort(key=lambda r: (r.get("occurred_at") or "", r.get("audit_name") or ""), reverse=True)
	# The subject never sees their own room-mates' participant reads, because those are not
	# in the report at all — `_content_rows` filters `was_participant = 0` in SQL. I9 stated
	# as a query rather than as a promise.
	return [redact_for_subject(row) for row in out[:limit]]


def compliance_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
	"""How many of these rows would satisfy §4.D.2, by failure mode.

	Exists so the health dashboard can show the number and the operator can watch it move.
	A control nobody measures is a control that has already stopped working; the specific
	number to watch is ``placeholder``, because that one only appears once somebody has
	noticed the field is not read.
	"""
	summary = {REASON_OK: 0, REASON_MISSING: 0, REASON_TOO_SHORT: 0, REASON_PLACEHOLDER: 0}
	for row in rows:
		grade = row.get("reason_grade") or reason_quality(row.get("reason"))
		if grade in summary:
			summary[grade] += 1
	return summary


__all__ = [
	"KIND_CONTENT",
	"KIND_GOVERNANCE",
	"REASON_CATEGORIES",
	"REASON_MIN_LENGTH",
	"REASON_OK",
	"REASON_MISSING",
	"REASON_TOO_SHORT",
	"REASON_PLACEHOLDER",
	"access_rows",
	"compliance_summary",
	"reason_quality",
	"redact_for_subject",
	"subject_rows",
]
