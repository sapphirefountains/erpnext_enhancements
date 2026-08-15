# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The one door onto chat history. **Every chat query in the retrieval path lives here.**

Read this before adding a line. This module is a **security boundary**, not a correctness
boundary, and the distinction decides how it is written. Every other risk in Phase 5 produces
a *wrong answer*, which somebody notices and reports. A mistake here produces **a correct
answer delivered to the wrong reader** — no exception, no user-visible symptom, nothing in any
log, and no complaint, because the person who received it has no idea they should not have.

Five rules. They are not style; each closes a specific way the boundary fails.

1. **``allowed_rooms`` is derived here and is never a parameter.** There is no argument,
   keyword or otherwise, by which a caller supplies room ids — because the caller is a
   model-driven turn assembling arguments from text, and a parameter is a thing a caller can
   get wrong. ``restrict_to`` exists and can only **narrow**: it is intersected, so a
   ``restrict_to`` naming a room the person cannot see contributes nothing.
2. **Every private search function takes ``allowed_rooms`` as a required first positional
   parameter.** Not a keyword, not defaulted. Omitting a required positional is a ``TypeError``
   during development; omitting a defaulted keyword is an unfiltered query in production that
   looks like nothing at all.
3. **The permission filter is in the ``WHERE`` clause, before any vector is loaded, any score
   computed or any ranking applied.** Filtering after ranking is forbidden *even where the
   visible output would be identical*, for two independent reasons: the ranker has then already
   read content the person may not see, and any quantity derived from that read — a score
   distribution, a count, a latency difference — is a side channel. More prosaically,
   post-filtering is one ``return`` away from being no filter at all.
4. **The audit row is written and committed before content is returned, and if it cannot be
   written the read does not happen.** An audit that fails open is not an audit.
5. **``retrieve(user="Administrator")`` raises.** Administrator short-circuits Frappe's
   permission stack entirely, so "every room" would be the literal, correct answer to "which
   rooms may this user see" — and the caller would never know it had asked a meaningless
   question. ``Guest`` raises for the same reason in the other direction.

--------------------------------------------------------------------------------------
Why the filter is applied twice
--------------------------------------------------------------------------------------

Every statement below constrains rooms **both** by ``room in (<derived set>)`` **and** by
``permissions.membership_filter_sql(..., allow_oversight=True)``. That is not redundancy for its own sake:

* the derived set is the narrow bound, and it is what makes the candidate scan cheap;
* the shared fragment is the *same expression* the ``permission_query_conditions`` hooks and
  the socket server use, so the raw path here cannot drift away from the hook path — and a
  drift on this seam is a leak rather than a bug.

The two are ANDed, so the fragment can never widen the derived set. That matters for the
oversight path specifically: ``membership_filter_sql`` returns ``1 = 1`` for an oversight role,
and here it lands inside an ``AND`` against an explicit room list.

--------------------------------------------------------------------------------------
What is deliberately not here
--------------------------------------------------------------------------------------

No ranking (:mod:`chat.retrieval.rank`), no budgeting (:mod:`chat.retrieval.budget`), no
assembly (:mod:`chat.retrieval.assemble`), no vector arithmetic
(:mod:`chat.retrieval.vectors`). Those are pure and tested bench-free. This module fetches,
and it is the only thing in the app that may.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import frappe
from frappe.utils import cint, get_datetime, now_datetime

from erpnext_enhancements.chat import audit, permissions
from erpnext_enhancements.chat.retrieval import assemble, budget, citations, lexical, rank, vectors

__all__ = ["retrieve", "retrieve_for_oversight", "retrieve_transcript"]

CHUNK_DOCTYPE = "Chat Context Chunk"
ROOM_DIGEST_DOCTYPE = "Chat Room Digest"
THREAD_DIGEST_DOCTYPE = "Chat Thread Digest"

_CHUNK_TABLE = "`tabChat Context Chunk`"
_ROOM_DIGEST_TABLE = "`tabChat Room Digest`"
_THREAD_DIGEST_TABLE = "`tabChat Thread Digest`"
_MESSAGE_TABLE = "`tabChat Message`"
_ROOM_TABLE = "`tabChat Room`"

#: **The retirement floor, as a WHERE fragment.** One copy, used by every chunk and digest
#: query below, because five hand-written copies of a correctness filter is five chances to
#: forget one — and the one that gets forgotten serves the transcript of messages that were
#: destroyed.
#:
#: Keyed on the chunk's ``first_seq``, never its ``last_seq``. For a mark that was snapped to a
#: chunk boundary the two are equivalent; for a mark set by hand — the field is ``read_only``
#: on the DocField, which is not a database constraint — they are not, and ``last_seq`` would
#: serve a chunk straddling the mark, whose body is the retired transcript verbatim. See
#: ``chat/retire_rules.wholly_retired``, which makes the same choice for the same reason.
_RETIRED_CHUNK_SQL = (
	f"{_CHUNK_TABLE}.`first_seq` > coalesce("
	f"(select `retired_below_seq` from {_ROOM_TABLE} where `name` = {_CHUNK_TABLE}.`room`), 0)"
)

#: The digest form of the same floor, one constant per table rather than a builder. A digest
#: is a summary crossing time and topic boundaries, so ANY intersection with the retired range
#: disqualifies it — there is no partial un-saying of a summary — and ``covered_from`` being
#: above the mark is that test.
#:
#: Constants rather than a function on purpose: every private *function* in this module takes
#: ``allowed_rooms`` as its required first positional, and `test_chat_gate_source_scan`
#: enforces it. That rule is about the failure mode — omitting a required positional is a
#: TypeError in development, while omitting a defaulted keyword is a query with no membership
#: filter, which is a leak in production that looks like nothing at all. A fragment builder
#: that took a table name first would have been the first exception to a rule worth keeping
#: exceptionless.
_RETIRED_ROOM_DIGEST_SQL = (
	f"{_ROOM_DIGEST_TABLE}.`covered_from` > coalesce("
	f"(select `retired_below_seq` from {_ROOM_TABLE} where `name` = {_ROOM_DIGEST_TABLE}.`room`), 0)"
)
_RETIRED_THREAD_DIGEST_SQL = (
	f"{_THREAD_DIGEST_TABLE}.`covered_from` > coalesce("
	f"(select `retired_below_seq` from {_ROOM_TABLE} where `name` = {_THREAD_DIGEST_TABLE}.`room`), 0)"
)

#: Hard cap on the T1 verbatim thread, independent of the token budget. The budget is a cost
#: control; this is a statement-cost control, so one enormous thread cannot make the query
#: itself the problem.
MAX_THREAD_MESSAGES: int = 200

#: Hard cap on the T3 authored tier.
MAX_AUTHORED_MESSAGES: int = 60

#: The two things a retrieval can be for. A constant rather than the bare strings that used to
#: be passed, because ``purpose`` stopped being a label the moment it started deciding what
#: goes into the assembly: **an oversight read is the one path whose output is rendered to a
#: person rather than sent to a model**, and the citation instruction is addressed to a model.
PURPOSE_MENTION: str = "mention"
PURPOSE_OVERSIGHT: str = "oversight"

#: The third: a verbatim read of one room's conversation, with no query and no budget. Kept
#: distinct from ``oversight`` deliberately — the compliance report projects ``purpose``, and
#: "they searched these rooms for a term" and "they read this conversation end to end" are
#: different acts that a single label would merge.
PURPOSE_TRANSCRIPT: str = "transcript"


class RetrievalRefused(Exception):
	"""Raised when retrieval must not proceed. Never carries content."""


@dataclass
class RetrievalResult:
	"""What one gated retrieval returns. No raw rows escape this dataclass."""

	assembly: assemble.Assembly | None = None
	manifest: list[citations.Citation] = field(default_factory=list)
	plan: budget.Plan | None = None
	rooms_searched: tuple[str, ...] = ()
	chunks_considered: int = 0
	chunks_returned: int = 0
	digests_used: int = 0
	context_tokens: int = 0
	context_truncated: bool = False
	degradation_rung: int = 0
	tiers_used: tuple[str, ...] = ()
	audit_row: str | None = None
	#: Terms the FULLTEXT index cannot match. Surfaced so a caller can say "your search
	#: terms were too short" rather than "no results", which are different answers.
	dropped_terms: tuple[str, ...] = ()


# --------------------------------------------------------------------- the derived room set


def _assert_real_user(user: str | None) -> str:
	"""Resolve the acting user, refusing the two that make the question meaningless.

	``Administrator`` bypasses the permission stack unconditionally — ``frappe/permissions.py``
	short-circuits on it — so "which rooms may Administrator see" answers "all of them",
	truthfully, and a caller that accepted the answer would have built the cross-user reach
	the whole design forbids. ``Guest`` is refused because chat has no anonymous surface at
	all and a Guest reaching here means something upstream is wrong.

	This is invariant I4's first line of defence and it raises rather than returning empty: an
	empty room set would produce a valid-looking answer with no sources, which reads as "there
	was nothing relevant" instead of "you asked the wrong question".
	"""
	resolved = (user or frappe.session.user or "").strip()
	if not resolved:
		raise RetrievalRefused("Chat retrieval requires a user and none was resolved.")
	if resolved in ("Administrator", "Guest"):
		raise RetrievalRefused(
			f"Chat retrieval refuses to run as {resolved}. Retrieval must run as the real "
			"person asking, because the room set is derived from their membership — and "
			f"{resolved} either bypasses the permission stack entirely or has no membership "
			"at all. Pass the mentioning user."
		)
	return resolved


def _visible_room_ids(user: str, restrict_to: list[str] | None = None) -> frozenset[str]:
	"""The rooms ``user`` is an **active** member of, optionally narrowed.

	Derived here, from :func:`permissions.visible_room_names`, which is the same helper the
	room list uses. Never cached — a room removal must take effect on the **very next** call,
	and a cache with any TTET at all means a departed member keeps reading for that long.
	That is the one entry on the never-cache list, and this is the function it is about.

	``restrict_to`` is intersected. It cannot widen, it cannot introduce a room, and a name
	in it that the person cannot see contributes nothing rather than raising — narrowing to a
	room you have just been removed from is a legitimate race, not an attack.
	"""
	allowed = frozenset(permissions.visible_room_names(user))
	if restrict_to:
		requested = frozenset(str(room).strip() for room in restrict_to if str(room or "").strip())
		allowed = allowed & requested
	return _cap_rooms(allowed)


def _cap_rooms(rooms: frozenset[str]) -> frozenset[str]:
	"""Apply ``max_rooms_per_retrieval``, which narrows and can never widen.

	Sorted before slicing, so the cap is deterministic rather than dependent on set iteration
	order — an unstable cap would make two identical retrievals search different rooms, which
	is both a cache miss and a support call nobody can reproduce.
	"""
	cap = cint(_setting("max_rooms_per_retrieval"))
	if cap <= 0 or len(rooms) <= cap:
		return rooms
	return frozenset(sorted(rooms)[:cap])


def _room_list_sql(allowed_rooms: frozenset[str]) -> str:
	"""``('a','b')`` for an ``IN`` clause, every value escaped.

	There is no parameter binding on this seam — the room set is variable-length — so
	``frappe.db.escape`` is the whole defence and it is applied per value, always. Room
	docnames are framework-generated hashes today; escaping them anyway costs nothing and
	survives the day a room name comes from somewhere else.

	Returns ``('')`` for an empty set rather than ``()``, because ``in ()`` is a syntax error
	in MariaDB while ``in ('')`` is a legal predicate that matches nothing. Failing closed
	has to also mean *parsing*.
	"""
	if not allowed_rooms:
		return "('')"
	values = ", ".join(frappe.db.escape(room) for room in sorted(allowed_rooms))
	return f"({values})"


# ------------------------------------------------------------------------- the fetch surface
#
# Every function below takes `allowed_rooms` as a required FIRST POSITIONAL parameter, and
# every one references permissions.membership_filter_sql. Both are asserted by
# tests/test_chat_gate_source_scan.py and tests/test_chat_rawsql_guard.py respectively.


def _semantic_candidate_rows(
	allowed_rooms: frozenset[str],
	*,
	limit: int,
) -> list[tuple[str, str, str, int]]:
	"""``(chunk, room, embedding, embedding_dim)`` for embeddable chunks in scope.

	Sealed, not stale, and carrying a vector. The **unsealed tail is excluded by design** —
	see ``Chat Context Chunk``'s controller for why, and for what covers it instead.

	Ordered by ``last_seq desc`` so that when the cap bites it keeps the *recent* history
	rather than an arbitrary slice. An arbitrary slice is the worse failure: retrieval would
	work perfectly for old conversations and mysteriously not for this morning's.
	"""
	scope = permissions.membership_filter_sql(f"{_CHUNK_TABLE}.`room`", _acting_user(), allow_oversight=True)
	rooms = _room_list_sql(allowed_rooms)
	rows = frappe.db.sql(
		f"""
		select `name`, `room`, `embedding`, `embedding_dim`
		from {_CHUNK_TABLE}
		where `room` in {rooms}
			and {scope}
			and {_RETIRED_CHUNK_SQL}
			and `sealed` = 1
			and `is_stale` = 0
			and `embedding` is not null
			and `embedding` != ''
		order by `last_seq` desc
		limit %(limit)s
		""",
		{"limit": max(cint(limit), 0)},
	)
	return [(row[0], row[1], row[2], cint(row[3])) for row in rows or []]


def _lexical_chunk_order(
	allowed_rooms: frozenset[str],
	*,
	expression: str,
	limit: int,
) -> list[str]:
	"""Chunk names in FULLTEXT relevance order, best first.

	``expression`` comes from :func:`chat.retrieval.lexical.build_boolean_query` and is bound
	as a parameter, never interpolated. An empty expression is refused here rather than passed
	through: an empty ``AGAINST`` is a full-corpus read wearing a search's clothes.

	InnoDB FULLTEXT sees **committed rows only**, so a chunk sealed in the calling transaction
	is invisible to this until that transaction commits. The indexer commits before anything
	expects to find what it wrote.
	"""
	if not expression:
		return []
	scope = permissions.membership_filter_sql(f"{_CHUNK_TABLE}.`room`", _acting_user(), allow_oversight=True)
	rooms = _room_list_sql(allowed_rooms)
	rows = frappe.db.sql(
		f"""
		select `name`
		from {_CHUNK_TABLE}
		where `room` in {rooms}
			and {scope}
			and {_RETIRED_CHUNK_SQL}
			and `is_stale` = 0
			and match(`body`) against (%(expression)s in boolean mode)
		order by match(`body`) against (%(expression)s in boolean mode) desc, `name` asc
		limit %(limit)s
		""",
		{"expression": expression, "limit": max(cint(limit), 0)},
	)
	return [row[0] for row in rows or []]


def _chunk_rows(allowed_rooms: frozenset[str], *, names: list[str]) -> list[dict[str, Any]]:
	"""Full chunk rows for a named set — the only place a chunk **body** is read.

	Filtered on the room set again even though the names came from a filtered query. The names
	have travelled through pure ranking code by this point, and "the list I was handed was
	already filtered" is the assumption that becomes a leak the day some intermediate step
	starts merging in a second source.
	"""
	if not names:
		return []
	scope = permissions.membership_filter_sql(f"{_CHUNK_TABLE}.`room`", _acting_user(), allow_oversight=True)
	rooms = _room_list_sql(allowed_rooms)
	placeholders = ", ".join(frappe.db.escape(name) for name in names)
	rows = frappe.db.sql(
		f"""
		select `name`, `room`, `thread_root`, `first_seq`, `last_seq`, `body`,
			`token_count`, `participants`, `first_message`, `last_message`,
			`first_message_at`, `last_message_at`
		from {_CHUNK_TABLE}
		where `name` in ({placeholders})
			and `room` in {rooms}
			and {scope}
			and {_RETIRED_CHUNK_SQL}
			and `is_stale` = 0
		""",
		as_dict=True,
	)
	return list(rows or [])


def _room_digest_rows(allowed_rooms: frozenset[str]) -> list[dict[str, Any]]:
	"""Room digests in scope, **excluding stale and poisoned ones**.

	Excluded rather than served with a caveat, and that is the invalidation contract rather
	than caution: a stale digest may summarise a message somebody deleted, ERPNext holds the
	only copy of that text, and a summary cannot unsay it. Skipping costs a less complete
	answer; serving costs the delete.
	"""
	scope = permissions.membership_filter_sql(
		f"{_ROOM_DIGEST_TABLE}.`room`", _acting_user(), allow_oversight=True
	)
	rooms = _room_list_sql(allowed_rooms)
	rows = frappe.db.sql(
		f"""
		select `name`, `room`, `summary_text`, `token_count`, `digest_version`,
			`covered_from`, `covered_to`, `generated_at`
		from {_ROOM_DIGEST_TABLE}
		where `room` in {rooms}
			and {scope}
			and {_RETIRED_ROOM_DIGEST_SQL}
			and `is_stale` = 0
			and `poisoned` = 0
			and `summary_text` is not null
			and `summary_text` != ''
		""",
		as_dict=True,
	)
	return list(rows or [])


def _thread_digest_row(allowed_rooms: frozenset[str], *, thread_root: str) -> dict[str, Any] | None:
	"""The digest for one thread, if it exists and is usable. Rung 4's substitute."""
	if not thread_root:
		return None
	scope = permissions.membership_filter_sql(
		f"{_THREAD_DIGEST_TABLE}.`room`", _acting_user(), allow_oversight=True
	)
	rooms = _room_list_sql(allowed_rooms)
	rows = frappe.db.sql(
		f"""
		select `name`, `room`, `thread_root`, `summary_text`, `token_count`, `digest_version`
		from {_THREAD_DIGEST_TABLE}
		where `thread_root` = %(thread_root)s
			and `room` in {rooms}
			and {scope}
			and {_RETIRED_THREAD_DIGEST_SQL}
			and `is_stale` = 0
			and `poisoned` = 0
		limit 1
		""",
		{"thread_root": thread_root},
		as_dict=True,
	)
	return (rows or [None])[0]


def _thread_messages(
	allowed_rooms: frozenset[str],
	*,
	room: str,
	thread_root: str | None,
	limit: int,
) -> list[dict[str, Any]]:
	"""T1 — the current thread, or the room's tail when the mention is not in a thread.

	**Ordered by ``seq``, never by a timestamp.** ``seq`` is immutable once assigned and is
	never renumbered; ``creation`` is site-local while an origin timestamp is UTC, and sorting
	a mixed-origin transcript by either produces an order that is wrong by the site offset in
	the direction that always favours one origin.

	Deleted rows are excluded. This path returns **bodies**, and the tombstone rule is that
	only the oversight surface sees through a delete.
	"""
	if room not in allowed_rooms:
		# Not a filter refinement — a caller asking for a room outside the derived set is a
		# bug, and answering it from the other rooms would hide the bug behind a plausible
		# answer.
		return []
	scope = permissions.membership_filter_sql(
		f"{_MESSAGE_TABLE}.`room`", _acting_user(), "seq", allow_oversight=True
	)
	rooms = _room_list_sql(allowed_rooms)
	thread_clause = "and `thread_root` = %(thread_root)s" if thread_root else ""
	rows = frappe.db.sql(
		f"""
		select `name`, `room`, `seq`, `sender`, `sender_email`, `text`, `thread_root`,
			`creation`, `gchat_create_time`
		from {_MESSAGE_TABLE}
		where `room` = %(room)s
			and `room` in {rooms}
			and {scope}
			and `is_deleted` = 0
			{thread_clause}
		order by `seq` desc
		limit %(limit)s
		""",
		{"room": room, "thread_root": thread_root, "limit": max(cint(limit), 0)},
		as_dict=True,
	)
	# Fetched newest-first so the LIMIT keeps the most recent, returned oldest-first so the
	# model reads the conversation in the order it happened.
	return list(reversed(rows or []))


#: Marks the oldest row of a room's block when that room had more to give than its share.
#: Set on the row in flight and read by :func:`_render`; it is not a column, nothing selects
#: it and nothing writes it back. The budget only ever reads ``text`` off a T1 payload
#: (:func:`_budget_items`), so an extra key is inert everywhere except the one place that
#: looks for it.
_TAIL_CUT = "earlier_matches_unread"


def _per_room_limits(allowed_rooms: frozenset[str], limit: int) -> dict[str, int]:
	"""How deep a tail each named room gets when one read names several.

	``seq`` is a **per-room** counter — the field's own description says so, "unique per
	(room, seq) … never client-assigned" — so a single ``order by seq desc limit 200`` over a
	room *set* ranks counters that were never comparable. A room holding 50,000 messages tops
	out near seq 50000 while a room holding forty tops out at 40, and the busy room's tail
	takes every slot: the quiet room named in the same read contributes **no verbatim message
	at all**, while T2 and T3 still answer. That is this tier's own failure mode — summaries
	in place of a transcript — arriving through the ``ORDER BY`` rather than through the call
	site, which is how it survived the fix that gave this function its name.

	**Even division, and a quiet room's unspent share is not handed to a busy one.** An audit
	read is bought breadth: the auditor named these rooms and the read has to answer for each
	of them. Redistributing the slack would restore exactly the bias being removed here, in a
	form that looks principled. Depth on a single room is already expressible — name one
	room — and that narrower read is audited like any other.

	The remainder is dealt to the first rooms in ``sorted()`` order rather than dropped, so
	the shares sum to ``limit`` exactly. ``sorted()`` is the tie-break :func:`_room_list_sql`
	already uses and for the same reason: a read that varies its own shape run to run records
	a different act each time.

	The floor of one beats the cap deliberately. The two constants cannot divide to zero
	today — ``MAX_ROOMS_PER_READ`` is 25 against a ``MAX_THREAD_MESSAGES`` of 200 — but if
	they ever did, every room would return nothing while looking exactly like a read that
	found nothing, which is the shape this function exists to make impossible. A cap of zero
	is different and is honoured: that is a caller asking for no verbatim tier, not a caller
	asking for one and being starved.
	"""
	rooms = sorted(allowed_rooms)
	cap = max(cint(limit), 0)
	if not rooms or cap <= 0:
		return {}
	base, remainder = divmod(cap, len(rooms))
	return {room: max(base + (1 if i < remainder else 0), 1) for i, room in enumerate(rooms)}


def _oversight_room_messages(
	allowed_rooms: frozenset[str],
	*,
	expression: str,
	limit: int,
) -> list[dict[str, Any]]:
	"""T1 for the oversight path — verbatim messages across **all** the named rooms.

	:func:`_thread_messages` cannot serve this, and the difference is not a detail. It takes
	a single ``room`` and is gated on one being supplied, so the oversight caller — which
	names a *set* of rooms and no thread — got an empty verbatim tier and never noticed:
	chunks and digests still came back, so the answer looked complete while containing no
	actual message the auditor asked to see. **A read that returns summaries when it was
	asked for a transcript is worse than one that returns nothing**, because only the second
	one gets reported.

	Ordered by ``seq`` for the same reason as its sibling — immutable, never renumbered,
	and immune to the site-local-vs-UTC skew that makes a mixed-origin transcript sort wrong
	by the site offset.

	**One statement per room, because that ordering is only valid inside one.** The sibling
	takes a single room, where a ``seq`` sort is exact; carrying the same ``ORDER BY`` across
	a set silently reintroduced the defect described above, one room at a time. See
	:func:`_per_room_limits` for how the cap divides. The fan-out is bounded at 25 by
	``governance.viewer.MAX_ROOMS_PER_READ`` and each statement is an index-ordered dive that
	stops after its share, which is *cheaper* than the single statement it replaces: ranking
	one global 200 meant reading every message in every named room first.

	Rows come back grouped by room and ascending within a room, and there is deliberately no
	order **across** rooms. Two per-room counters have no meaningful comparison, and the
	timestamps that would give one are the very thing this tier refuses to sort on. Grouping
	states what is true; an interleave would state something that is not.

	**Deleted rows stay excluded here.** Seeing through a tombstone is its own audited act
	with its own event type (``tombstone_expanded``, §4.E), and folding it into the ordinary
	oversight read would make every such read an expansion — which is precisely the
	distinction §4.E exists to preserve.
	"""
	if not allowed_rooms:
		return []
	scope = permissions.membership_filter_sql(
		f"{_MESSAGE_TABLE}.`room`", _acting_user(), "seq", allow_oversight=True
	)
	rooms = _room_list_sql(allowed_rooms)
	shares = _per_room_limits(allowed_rooms, limit)
	# With a query, the matching messages; without one, the tail. An oversight read with no
	# search terms is a legitimate "show me this conversation" request, and returning nothing
	# for it would push the auditor towards a broader read rather than a narrower one.
	match_clause = "and match(`text_plain`) against (%(expression)s in boolean mode)" if expression else ""
	out: list[dict[str, Any]] = []
	for room, share in shares.items():
		rows = frappe.db.sql(
			f"""
			select `name`, `room`, `seq`, `sender`, `sender_email`, `text`, `thread_root`,
				`creation`, `gchat_create_time`
			from {_MESSAGE_TABLE}
			where `room` = %(room)s
				and `room` in {rooms}
				and {scope}
				and `is_deleted` = 0
				{match_clause}
			order by `seq` desc
			limit %(limit)s
			""",
			# `room in {rooms}` is redundant beside `room = %(room)s` and stays anyway: the
			# bound value comes from `shares`, and a later edit that derives those from
			# anywhere but `allowed_rooms` would otherwise read outside the authorised set
			# with nothing in the statement to stop it.
			#
			# One row past the share, fetched and discarded: the cheapest answer to "was this
			# room's tail cut", which the auditor otherwise cannot tell apart from "this room
			# said exactly eight things".
			{"room": room, "expression": expression, "limit": share + 1},
			as_dict=True,
		)
		fetched = list(rows or [])
		kept = fetched[:share]
		if kept and len(fetched) > share:
			# Newest-first here, so the last kept row is the OLDEST — which after the reverse
			# below is the first line of this room's block, where "there is more above this"
			# belongs.
			kept[-1][_TAIL_CUT] = 1
		out.extend(reversed(kept))
	return out


def _authored_messages(
	allowed_rooms: frozenset[str],
	*,
	user: str,
	expression: str,
	limit: int,
) -> list[dict[str, Any]]:
	"""T3 — what the asking person said themselves, matching the query.

	This tier exists because "what did I agree to" is the commonest question a chat assistant
	is asked, and the person's own messages are the answer. It is scoped to the same derived
	room set as everything else: authorship is not a permission, and a message you wrote in a
	room you have since left is not readable because you wrote it.
	"""
	if not expression:
		return []
	scope = permissions.membership_filter_sql(
		f"{_MESSAGE_TABLE}.`room`", _acting_user(), "seq", allow_oversight=True
	)
	rooms = _room_list_sql(allowed_rooms)
	rows = frappe.db.sql(
		f"""
		select `name`, `room`, `seq`, `sender`, `text`, `thread_root`, `creation`
		from {_MESSAGE_TABLE}
		where `sender` = %(user)s
			and `room` in {rooms}
			and {scope}
			and `is_deleted` = 0
			and match(`text_plain`) against (%(expression)s in boolean mode)
		order by `seq` desc
		limit %(limit)s
		""",
		{"user": user, "expression": expression, "limit": max(cint(limit), 0)},
		as_dict=True,
	)
	return list(rows or [])


def _room_watermarks(allowed_rooms: frozenset[str], *, room: str) -> tuple[int, int, str]:
	"""The **three-value watermark** for one room: ``(max(seq), count(*), max(modified))``.

	All three, because each catches something the others cannot: ``seq`` catches a new
	message, ``modified`` catches an **edit**, and ``count`` catches a **hard delete** which
	moves neither. A cache key built on ``seq`` alone is unchanged by a delete, so the cached
	context containing the deleted message is served again.

	``max(modified)`` is formatted rather than returned as a datetime, because it goes into a
	cache **key** and a key must be a stable string. Frappe writes ``modified`` in site-local
	time and this value is only ever compared against itself, so no conversion is needed here
	— but nothing may compare it to SQL ``NOW()``, which is UTC on this host and six hours
	adrift.
	"""
	if room not in allowed_rooms:
		return (0, 0, "")
	scope = permissions.membership_filter_sql(
		f"{_MESSAGE_TABLE}.`room`", _acting_user(), "seq", allow_oversight=True
	)
	rooms = _room_list_sql(allowed_rooms)
	rows = frappe.db.sql(
		f"""
		select coalesce(max(`seq`), 0), count(*), coalesce(max(`modified`), '1970-01-01')
		from {_MESSAGE_TABLE}
		where `room` = %(room)s
			and `room` in {rooms}
			and {scope}
		""",
		{"room": room},
	)
	if not rows:
		return (0, 0, "")
	seq, count, modified = rows[0]
	return (cint(seq), cint(count), str(modified))


# ---------------------------------------------------------------------------- the entry point


def retrieve(
	*,
	query: str,
	user: str | None = None,
	room: str | None = None,
	thread_root: str | None = None,
	restrict_to: list[str] | None = None,
	purpose: str = "mention",
	request_id: str | None = None,
	system_prompt: str = "",
	glossary_lines: list[str] | None = None,
	user_card_lines: list[str] | None = None,
) -> RetrievalResult:
	"""Retrieve and assemble chat context **as ``user``**, from the rooms they are in.

	There is no ``allowed_rooms`` parameter and there must never be one. ``restrict_to``
	narrows by intersection.

	Raises:
		RetrievalRefused: for ``Administrator``, ``Guest``, an unresolvable user, retrieval
			paused by the kill switch, or an audit row that could not be written.
	"""
	acting = _assert_real_user(user)
	_assert_retrieval_enabled()

	with _acting_as(acting):
		allowed_rooms = _visible_room_ids(acting, restrict_to)
		if not allowed_rooms:
			# A person in no rooms is not an error and not an empty search: there is
			# genuinely nothing to search. Still audited, because "Triton read nothing on
			# your behalf" is a fact worth being able to prove afterwards.
			return _empty_result(acting, query, purpose, request_id)

		return _run(
			allowed_rooms,
			acting=acting,
			query=query,
			room=room,
			thread_root=thread_root,
			purpose=purpose,
			request_id=request_id,
			system_prompt=system_prompt,
			glossary_lines=glossary_lines or [],
			user_card_lines=user_card_lines or [],
			actor_type="Triton",
			# "What did I agree to" is the commonest question asked of a chat assistant, and
			# the asker's own messages are the answer. This is the tier that exists for it.
			authored_user=acting,
		)


def retrieve_for_oversight(
	*,
	query: str,
	rooms: list[str],
	reason: str,
	# Travels beside `reason` and is signed with it. It used to be written onto the audit row
	# by the viewer *after* this function had already hashed it, which meant the one field the
	# subject is ever shown was the one field the chain did not cover. See `audit.py`'s
	# `_OPTIONAL_CHAINED_FIELDS`.
	reason_category: str | None = None,
	subject: str | None = None,
	user: str | None = None,
	request_id: str | None = None,
) -> RetrievalResult:
	"""The oversight read: named rooms the auditor is **not** in, and it costs a reason.

	A separate function rather than a flag on :func:`retrieve`, because a boolean is one typo
	from being ``True`` while a distinctly named function cannot be reached by a caller who
	did not mean to reach it — and it appears as itself in a stack trace and in a grep.

	Three things it does that :func:`retrieve` does not, and each is a deliberate cost:

	* it requires the configured oversight role, read from settings and never a literal role
	  name;
	* it requires an explicit, non-trivial ``reason``, which is **refused** when too short
	  rather than accepted-and-blanked;
	* it takes ``rooms`` explicitly. Nothing is inferred. An oversight read of "everything" is
	  not expressible here, because the audit row has to name what was read.

	``subject`` is who the read is *about*, and it is optional because not every oversight
	read has one — "what was said in this room about the Henderson contract" is a legitimate
	question with no subject. When given, the authored tier returns that person's messages;
	when absent there is no authored tier. It was previously neither: the tier ran as the
	auditor and returned **their own** messages, which they could already read, in place of
	the ones they had just stated a reason to see.

	**What this does not do: see through a delete.** Every tier here excludes ``is_deleted``.
	Expanding a tombstone is its own act with its own audit event (§4.E), and folding it in
	here would make every oversight read an expansion.
	"""
	acting = _assert_real_user(user)
	_assert_retrieval_enabled()

	if not permissions._has_oversight(acting):
		raise RetrievalRefused(
			"Oversight retrieval requires the role named in Chat Settings → Admin Oversight "
			"Role. That field ships blank, so this path fails closed until somebody "
			"deliberately configures it."
		)

	cleaned_reason = (reason or "").strip()
	if len(cleaned_reason) < _MIN_OVERSIGHT_REASON:
		raise RetrievalRefused(
			f"An oversight read needs a stated reason of at least {_MIN_OVERSIGHT_REASON} "
			"characters. The reason is the thing that makes this read defensible later; an "
			"empty one is refused rather than recorded as blank."
		)

	named = frozenset(str(r).strip() for r in (rooms or []) if str(r or "").strip())
	if not named:
		raise RetrievalRefused("An oversight read must name the rooms it reads.")

	with _acting_as(acting):
		return _run(
			_cap_rooms(named),
			acting=acting,
			query=query,
			room=None,
			thread_root=None,
			purpose=PURPOSE_OVERSIGHT,
			request_id=request_id,
			system_prompt="",
			glossary_lines=[],
			user_card_lines=[],
			actor_type="Admin",
			reason=cleaned_reason,
			reason_category=reason_category,
			# The subject, or no authored tier at all. Never `acting`: that returned the
			# auditor their own messages — content they could already read, in place of the
			# content they came to audit.
			authored_user=subject,
			# T1 across the named rooms. `room` is None here and always will be, so the
			# thread-shaped tier can never fire on this path.
			verbatim_across_rooms=True,
		)


#: An oversight reason shorter than this is refused. Long enough to be a sentence, short
#: enough that a legitimate one-line justification passes.
_MIN_OVERSIGHT_REASON: int = 12

#: One page of a transcript. Independent of :data:`MAX_THREAD_MESSAGES`, which sizes a tier
#: inside a token budget; this sizes a **statement** and a scroll.
MAX_TRANSCRIPT_PAGE: int = 100


@dataclass
class TranscriptPage:
	"""One audited page of one room's conversation, in ``seq`` order."""

	room: str
	rows: list[dict[str, Any]] = field(default_factory=list)
	first_seq: int = 0
	last_seq: int = 0
	has_more_before: bool = False
	audit_row: str = ""


def _transcript_rows(
	allowed_rooms: frozenset[str],
	*,
	room: str,
	before_seq: int | None,
	limit: int,
) -> list[dict[str, Any]]:
	"""One room's messages, newest-first from ``before_seq``, returned oldest-first.

	**Tombstones are INCLUDED here, and that is the one place this path differs from every
	tier above.** The retrieval tiers exclude ``is_deleted`` because folding a deleted body
	into an ordinary read would make every read a tombstone expansion (§4.E). A transcript
	has the opposite obligation: a conversation with the deleted messages quietly removed is
	a **misleading** transcript, and the gap is exactly where an investigation is most likely
	to be looking. So the row comes back, keeps its place in the sequence, and its **body does
	not** — the serialiser withholds it, and seeing through it stays a separate audited act
	with its own event.

	Keyset on ``seq``, never ``OFFSET``: the same rule as the SPA's own paging, and for the
	same reason. ``seq`` is per-room, which is valid here because this reads exactly one room
	— see :func:`_per_room_limits` for what goes wrong when that assumption is stretched
	across a set.
	"""
	if not allowed_rooms or not room:
		return []
	scope = permissions.membership_filter_sql(
		f"{_MESSAGE_TABLE}.`room`", _acting_user(), "seq", allow_oversight=True
	)
	rooms = _room_list_sql(allowed_rooms)
	before = "and `seq` < %(before_seq)s" if before_seq else ""
	rows = frappe.db.sql(
		f"""
		select `name`, `room`, `seq`, `sender`, `sender_email`, `text`, `thread_root`,
			`is_deleted`, `is_edited`, `edited_at`, `creation`, `gchat_create_time`
		from {_MESSAGE_TABLE}
		where `room` = %(room)s
			and `room` in {rooms}
			and {scope}
			{before}
		order by `seq` desc
		limit %(limit)s
		""",
		{"room": room, "before_seq": cint(before_seq), "limit": max(cint(limit), 0) + 1},
		as_dict=True,
	)
	# Fetched newest-first so the LIMIT keeps the most recent page; returned oldest-first,
	# because that is the order the conversation happened in and the order it is read in.
	return list(reversed(list(rows or [])))


def retrieve_transcript(
	*,
	room: str,
	reason: str,
	reason_category: str | None = None,
	before_seq: int | None = None,
	limit: int | None = None,
	user: str | None = None,
	request_id: str | None = None,
) -> TranscriptPage:
	"""One room's conversation, verbatim and in order. **Not a retrieval.**

	:func:`retrieve_for_oversight` cannot serve this and the difference is not a preference.
	That function answers *"what in these rooms is relevant to this question"*: it returns a
	200-message tail, becomes a **search** the moment the derived boolean expression is
	non-empty, and hands its result to :mod:`chat.retrieval.budget`, whose rung 4 discards
	messages from the **middle** of the thread to fit a token ceiling. Every one of those is
	correct for assembling a model's context and disqualifying for a transcript: *a record
	that silently elides its middle is not a record.* There is no token budget here, no
	ranking, no assembly, and no query.

	It lives in this module rather than in ``chat/governance`` because opening the oversight
	hatch is this module's exclusive privilege — ``membership_filter_sql(allow_oversight=True)``
	is refused anywhere else, and ``tests/test_chat_audit_immutability`` enforces that across
	the whole package. The endpoint, the reason gate and the shaping live in
	``chat/governance/viewer.py``; what lives here is the fetch.

	**One audit row per page**, written before the rows are returned and refusing the read if
	it cannot be written. ``request_id`` correlates the pages of one sitting, so an auditor
	scrolling a long conversation is one act of reading in the record rather than eleven
	unrelated ones — which is the whole reason §4.D.2 asks for the correlation.
	"""
	acting = _assert_real_user(user)
	_assert_retrieval_enabled()

	if not permissions._has_oversight(acting):
		raise RetrievalRefused(
			"Reading a transcript you are not a participant in requires the role named in "
			"Chat Settings → Admin Oversight Role."
		)

	cleaned_reason = (reason or "").strip()
	if len(cleaned_reason) < _MIN_OVERSIGHT_REASON:
		raise RetrievalRefused(
			f"A transcript read needs a stated reason of at least {_MIN_OVERSIGHT_REASON} "
			"characters. It is the thing that makes this read defensible later."
		)

	named = str(room or "").strip()
	if not named:
		raise RetrievalRefused("A transcript read must name the room it reads.")

	page = max(cint(limit) or MAX_TRANSCRIPT_PAGE, 1)
	page = min(page, MAX_TRANSCRIPT_PAGE)

	with _acting_as(acting):
		allowed = frozenset({named})
		fetched = _transcript_rows(allowed, room=named, before_seq=before_seq, limit=page)
		# One row past the page, discarded: the cheapest answer to "is there more above this",
		# which the auditor otherwise cannot tell apart from "the conversation starts here".
		has_more = len(fetched) > page
		rows = fetched[-page:] if has_more else fetched

		seqs = [cint(r.get("seq")) for r in rows]
		audit_row = audit.record_or_refuse(
			user=acting,
			purpose=PURPOSE_TRANSCRIPT,
			actor_type="Admin",
			rooms=(
				[
					{
						"room": named,
						"messages_read": len(rows),
						"first_seq": min(seqs),
						"last_seq": max(seqs),
					}
				]
				if rows
				else []
			),
			reason=cleaned_reason,
			reason_category=reason_category,
			request_id=request_id,
			message_count=len(rows),
		)

	return TranscriptPage(
		room=named,
		rows=rows,
		first_seq=min(seqs) if rows else 0,
		last_seq=max(seqs) if rows else 0,
		has_more_before=has_more,
		audit_row=audit_row,
	)


def _run(
	allowed_rooms: frozenset[str],
	*,
	acting: str,
	query: str,
	room: str | None,
	thread_root: str | None,
	purpose: str,
	request_id: str | None,
	system_prompt: str,
	glossary_lines: list[str],
	user_card_lines: list[str],
	actor_type: str,
	reason: str | None = None,
	reason_category: str | None = None,
	authored_user: str | None,
	verbatim_across_rooms: bool = False,
) -> RetrievalResult:
	"""Fetch → rank → budget → **audit** → assemble. The order of the last two is the rule.

	``allowed_rooms`` is the first positional parameter here too, even though this function
	runs no SQL itself: it is the value every function it calls needs, and a signature that
	makes it optional here would make forgetting it possible one level down.

	**``authored_user`` has no default, deliberately.** It used to be implicit — the tier
	always ran as ``acting`` — which is right for an ordinary retrieve and silently wrong for
	oversight, where it fed the auditor their *own* messages in place of the ones they were
	auditing. A required keyword makes every caller answer "whose messages is this tier
	about?" out loud, and ``None`` means "no such tier" rather than "the obvious person".

	``verbatim_across_rooms`` picks T1's shape: a single room's thread or tail (the mention
	path, which has a room and often a thread), or the named set (the oversight path, which
	has neither).
	"""
	settings = _settings_dict()
	expression = lexical.build_boolean_query(query)
	dropped = lexical.dropped_terms(query)

	# --- fetch, already filtered -------------------------------------------------------
	semantic_order: list[str] = []
	candidate_rows: list[tuple[str, str, str, int]] = []
	if _truthy(settings.get("semantic_tier_enabled"), default=True):
		candidate_rows = _semantic_candidate_rows(
			allowed_rooms, limit=cint(settings.get("max_candidate_chunks")) or 8_000
		)
		semantic_order = _score_semantic(allowed_rooms, query=query, rows=candidate_rows)

	lexical_order: list[str] = []
	if _truthy(settings.get("lexical_tier_enabled"), default=True):
		lexical_order = _lexical_chunk_order(
			allowed_rooms,
			expression=expression,
			limit=cint(settings.get("retrieval_top_k")) or 24,
		)

	top_k = cint(settings.get("retrieval_top_k")) or 24
	fused_keys = _fused_keys(semantic_order, lexical_order, settings, top_k)
	chunk_rows = _chunk_rows(allowed_rooms, names=fused_keys)

	digest_rows = _room_digest_rows(allowed_rooms)
	if verbatim_across_rooms:
		thread_rows = _oversight_room_messages(
			allowed_rooms, expression=expression, limit=MAX_THREAD_MESSAGES
		)
	elif room:
		thread_rows = _thread_messages(
			allowed_rooms, room=room, thread_root=thread_root, limit=MAX_THREAD_MESSAGES
		)
	else:
		thread_rows = []
	# `None` is "this read has no authored tier", not "default to whoever is asking". The
	# oversight path passes the subject or nothing; passing `acting` there returned the
	# auditor's own messages, which they could already read and which are not the ones under
	# audit.
	authored_rows = (
		_authored_messages(
			allowed_rooms, user=authored_user, expression=expression, limit=MAX_AUTHORED_MESSAGES
		)
		if authored_user
		else []
	)

	# --- rank, on already-filtered rows ------------------------------------------------
	ranked = rank.rank(
		[_as_candidate(row) for row in chunk_rows],
		semantic_order=semantic_order,
		lexical_order=lexical_order,
		user=acting,
		current_room=room,
		current_thread=thread_root,
		rrf_k=cint(settings.get("rrf_k")) or rank.DEFAULT_RRF_K,
		half_life_days=float(cint(settings.get("recency_half_life_days")) or 30),
		limit=top_k,
	)

	# --- budget ------------------------------------------------------------------------
	resolved_budget = budget.budget_from_settings(settings)
	digest_by_room = {row["room"]: row for row in digest_rows}
	items = _budget_items(ranked, thread_rows, authored_rows, digest_rows, digest_by_room)
	fitted = budget.plan(items, resolved_budget)

	# --- audit BEFORE content is returned ----------------------------------------------
	# record_or_refuse commits the row and raises if it cannot be written. Nothing below this
	# line may move above it: the whole trade decision #12 makes is a non-participant read in
	# exchange for a record of it, and a read that happened without the record is the half of
	# that trade nobody agreed to.
	audit_row = _write_audit(
		allowed_rooms,
		acting=acting,
		query=query,
		purpose=purpose,
		actor_type=actor_type,
		request_id=request_id,
		reason=reason,
		reason_category=reason_category,
		plan=fitted,
		thread_rows=thread_rows,
		authored_rows=authored_rows,
		chunk_rows=chunk_rows,
	)

	# --- assemble ----------------------------------------------------------------------
	manifest_entries, t0_lines, t2_t3_lines, thread_lines = _render(
		fitted,
		digest_by_room,
		thread_rows,
		# Only the oversight path *named* its rooms; the mention path's `allowed_rooms` is
		# every room the asker belongs to, and reporting coverage against that would tell a
		# model about forty rooms nobody asked it to consider.
		named_rooms=allowed_rooms if verbatim_across_rooms else None,
	)
	manifest = citations.resolve_urls(citations.build_manifest(manifest_entries))

	assembly = assemble.assemble(
		system_prompt=system_prompt,
		glossary_lines=glossary_lines,
		user_card_lines=user_card_lines,
		t0_lines=t0_lines,
		t2_t3_lines=t2_t3_lines,
		thread_lines=thread_lines,
		question=query,
		context_truncated=fitted.context_truncated,
		# **Not for an oversight read.** `assemble()` defaults this on, and every other caller
		# is building a prompt, so on is right for them. The oversight path is the one place
		# the assembly is rendered to a *person* — `governance/viewer.py` returns
		# `assembly.text()` straight into the auditor's result box — and `CITATION_INSTRUCTION`
		# is an instruction addressed to a language model. From v1.299.1 until v1.306.1 an
		# investigator's transcript therefore opened with "Cite the numbered context lines you
		# actually used, inline, as [[ref:N]]…", and a screenshot of that surface would have
		# carried prompt scaffolding into a compliance record.
		#
		# The citations themselves are unaffected: they travel separately as `manifest`, which
		# `viewer.search()` returns under its own key.
		include_citation_instruction=purpose != PURPOSE_OVERSIGHT,
	)

	return RetrievalResult(
		assembly=assembly,
		manifest=manifest,
		plan=fitted,
		rooms_searched=tuple(sorted(allowed_rooms)),
		chunks_considered=len(candidate_rows) or len(chunk_rows),
		chunks_returned=len([item for item in fitted.kept if item.tier == budget.TIER_T2]),
		digests_used=len(digest_rows),
		context_tokens=fitted.total_tokens,
		context_truncated=fitted.context_truncated,
		degradation_rung=fitted.rung,
		tiers_used=fitted.tiers_used,
		audit_row=audit_row,
		dropped_terms=tuple(dropped),
	)


# ------------------------------------------------------------------------------- internals


def _score_semantic(
	allowed_rooms: frozenset[str],
	*,
	query: str,
	rows: list[tuple[str, str, str, int]],
) -> list[str]:
	"""Chunk names in cosine order, or ``[]`` when the semantic tier cannot run.

	The vector backend is constructed with a loader over rows this module already fetched and
	filtered, which is why no SQL lives in :mod:`chat.retrieval.vectors`. It re-checks the
	room of every row anyway, because from that module's point of view "the caller filtered"
	is an assumption rather than a fact.

	A failure here degrades to the lexical tier rather than failing the turn: an answer from
	exact matches beats no answer, and the embedding provider being unavailable is an
	operational event rather than a security one.
	"""
	if not rows:
		return []
	try:
		from erpnext_enhancements.chat.indexing import embed

		query_vector = embed.embed_query(query)
	except Exception:
		return []
	if not query_vector:
		return []

	backend = vectors.NumpyVectorBackend(
		candidate_loader=lambda: rows,
		expected_dim=len(query_vector),
	)
	try:
		hits = backend.search(allowed_rooms, query_vector, limit=len(rows))
	except vectors.VectorBackendError:
		return []
	return [hit.chunk for hit in hits]


def _fused_keys(
	semantic_order: list[str],
	lexical_order: list[str],
	settings: dict[str, Any],
	top_k: int,
) -> list[str]:
	"""The union of both tiers' top slices, fused, then truncated.

	Fusing here rather than after loading bodies means the expensive read — the one that
	fetches conversation text — is over ``top_k`` rows rather than over every candidate.
	"""
	fused = rank.rrf_scores(
		[semantic_order, lexical_order], k=cint(settings.get("rrf_k")) or rank.DEFAULT_RRF_K
	)
	ordered = sorted(fused.items(), key=lambda pair: (-pair[1], pair[0]))
	return [key for key, _ in ordered[: max(top_k, 0)]]


def _as_candidate(row: dict[str, Any]) -> rank.Candidate:
	import json

	try:
		participants = tuple(json.loads(row.get("participants") or "[]"))
	except Exception:
		participants = ()
	return rank.Candidate(
		key=row["name"],
		room=row["room"],
		age_days=_age_days(row.get("last_message_at") or row.get("first_message_at")),
		thread_root=row.get("thread_root"),
		participants=participants,
		tokens=cint(row.get("token_count")),
		payload=row,
	)


def _age_days(value: Any) -> float:
	"""Age in days, computed **here** and passed into the pure ranker.

	The clock read lives in this module on purpose: :mod:`chat.retrieval.rank` and
	:mod:`chat.retrieval.assemble` must contain no ``now()`` at all, because a clock read
	above the volatile segment invalidates the whole cached prefix on every request —
	silently, with no error and no log line.

	``now_datetime()`` rather than SQL ``NOW()``: the database runs UTC and Frappe writes
	site-local, so mixing them reports a row written a minute ago as six hours old.
	"""
	if not value:
		return 0.0
	try:
		delta = now_datetime() - get_datetime(value)
	except Exception:
		return 0.0
	return max(delta.total_seconds() / 86_400.0, 0.0)


def _budget_items(
	ranked: list[rank.Candidate],
	thread_rows: list[dict[str, Any]],
	authored_rows: list[dict[str, Any]],
	digest_rows: list[dict[str, Any]],
	digest_by_room: dict[str, dict[str, Any]],
) -> list[budget.Item]:
	"""Map fetched rows onto budget items, tier by tier, best-ranked first within each."""
	items: list[budget.Item] = []

	for row in digest_rows:
		items.append(
			budget.Item(
				key=f"digest:{row['room']}",
				tier=budget.TIER_T0,
				tokens=cint(row.get("token_count")) or _estimate(row.get("summary_text")),
				payload=row,
			)
		)

	for row in thread_rows:
		items.append(
			budget.Item(
				key=f"msg:{row['name']}",
				tier=budget.TIER_T1,
				tokens=_estimate(row.get("text")),
				payload=row,
			)
		)

	for candidate in ranked:
		digest = digest_by_room.get(candidate.room)
		items.append(
			budget.Item(
				key=candidate.key,
				tier=budget.TIER_T2,
				tokens=candidate.tokens or _estimate((candidate.payload or {}).get("body")),
				score=candidate.score,
				digest_key=f"digest:{candidate.room}" if digest else None,
				digest_tokens=cint((digest or {}).get("token_count")),
				payload=candidate.payload,
			)
		)

	for row in authored_rows:
		items.append(
			budget.Item(
				key=f"mine:{row['name']}",
				tier=budget.TIER_T3,
				tokens=_estimate(row.get("text")),
				payload=row,
			)
		)

	return items


def _render(
	fitted: budget.Plan,
	digest_by_room: dict[str, dict[str, Any]],
	thread_rows: list[dict[str, Any]],
	*,
	named_rooms: frozenset[str] | None = None,
) -> tuple[list[dict[str, object]], list[str], list[str], list[str]]:
	"""Turn the surviving items into manifest entries and labelled context lines.

	Manifest numbering follows **assembly order**, which is why this function builds both at
	once: numbering in one place and rendering in another is how the model ends up citing
	``[[ref:1]]`` for the third thing it read.

	``named_rooms`` is the set the *caller named*, and only the oversight path has one — the
	mention path's ``allowed_rooms`` is "every room this person is in", which is not a request
	and must not be reported against. When it is supplied, every room in it is accounted for
	below even if it contributed nothing, because **a room that returned no rows produces no
	evidence of its own absence**: silence from a room nobody read and silence from a room
	that had nothing to say are the same silence on the page, and that identity is what let
	the per-room ``seq`` defect run unnoticed.
	"""
	manifest_entries: list[dict[str, object]] = []
	t0_lines: list[str] = []
	t2_t3_lines: list[str] = []
	thread_lines: list[str] = []

	# `citations.context_line` renders ref, author, time and body — never the room. Grouped
	# blocks from several rooms would therefore read as one conversation that never happened,
	# so the headers go in whenever a read actually spans rooms. A single-room read renders
	# byte-identically to before.
	multi_room = len({str(row.get("room") or "") for row in thread_rows}) > 1
	current_room: str | None = None

	ref = 0
	for item in fitted.kept:
		payload = item.payload or {}
		if item.tier == budget.TIER_T0:
			row = payload or digest_by_room.get(str(item.key).removeprefix("digest:"), {})
			t0_lines.append(f"[room summary: {row.get('room', '')}] {row.get('summary_text', '')}")
			continue

		ref += 1
		if item.tier == budget.TIER_T1:
			entry = {
				"room": payload.get("room"),
				"message": payload.get("name"),
				"label": _message_label(payload),
				"kind": "message",
				"thread_root": payload.get("thread_root"),
			}
			row_room = str(payload.get("room") or "")
			if multi_room and row_room != current_room:
				thread_lines.append(f"[room {row_room}]")
				current_room = row_room
			if payload.get(_TAIL_CUT):
				# Database-side truncation, and a different fact from the ladder-side loss
				# reported at the end of this function: this room had more to give than its
				# share of the cap, and the remedy is a *narrower* read. The two causes get
				# two sentences because they have two different fixes, and a gap reported
				# without its remedy trains people to skip the report.
				thread_lines.append(
					f"[room {row_room}: this room has more messages than this read's share — "
					"read it on its own for the rest]"
				)
			thread_lines.append(
				citations.context_line(
					citations.Citation(
						ref=ref, room=str(payload.get("room") or ""), message=payload.get("name"), label=""
					),
					author=str(payload.get("sender") or payload.get("sender_email") or ""),
					timestamp=str(payload.get("gchat_create_time") or payload.get("creation") or ""),
					body=str(payload.get("text") or ""),
				)
			)
		elif item.tier == budget.TIER_T2 and item.payload is None and item.digest_key:
			# Rung 3 replaced this body with its room digest line.
			room = str(item.digest_key).removeprefix("digest:")
			row = digest_by_room.get(room, {})
			entry = {
				"room": room,
				"message": None,
				"label": f"summary of {room}",
				"kind": "digest",
			}
			t2_t3_lines.append(f"⟦ref:{ref}⟧ [room summary: {room}] {row.get('summary_text', '')}")
		else:
			entry = {
				"room": payload.get("room"),
				"message": payload.get("first_message") or payload.get("name"),
				"label": _chunk_label(payload),
				"kind": "chunk" if item.tier == budget.TIER_T2 else "message",
				"thread_root": payload.get("thread_root"),
			}
			t2_t3_lines.append(
				citations.context_line(
					citations.Citation(ref=ref, room=str(payload.get("room") or ""), message=None, label=""),
					author=str(payload.get("sender") or "conversation"),
					timestamp=str(payload.get("last_message_at") or payload.get("creation") or ""),
					body=str(payload.get("body") or payload.get("text") or ""),
				)
			)
		manifest_entries.append(entry)

	tier_dropped = not thread_lines and bool(thread_rows)
	if tier_dropped:
		# The thread was dropped entirely by the hard floor. Say so rather than silently
		# presenting no thread at all, which reads to the model as "there is no conversation".
		thread_lines.append("[the current thread was omitted to fit the context limit]")

	if named_rooms:
		shown = {
			str((item.payload or {}).get("room") or "")
			for item in fitted.kept
			if item.tier == budget.TIER_T1
		}
		fetched = {str(row.get("room") or "") for row in thread_rows}
		# Every room the auditor named is accounted for, including the ones that said nothing.
		# Two silences with two causes and two remedies: a room that matched nothing is a fact
		# about the room, and a room whose block the ladder ate is a fact about this read being
		# too wide. Neither line discloses anything — the auditor supplied these room ids, and
		# saying "nothing matched" reveals strictly less than the messages would have.
		for room in sorted(named_rooms):
			if not room or room in shown:
				continue
			if room not in fetched:
				thread_lines.append(f"[room {room}: no matching messages in this read]")
			elif not tier_dropped:
				# The rungs are room-blind — `_take_while_fits` keeps a prefix, `_compress_t1`
				# removes from the middle outward — so one room's whole block can vanish
				# between the fetch and here while its neighbours survive intact.
				thread_lines.append(
					f"[room {room}: verbatim messages were omitted to fit the context limit — "
					"name fewer rooms in one read]"
				)

	return manifest_entries, t0_lines, t2_t3_lines, thread_lines


def _message_label(row: dict[str, Any]) -> str:
	sender = row.get("sender") or row.get("sender_email") or "someone"
	return f"{sender} in {row.get('room', '')}"


def _chunk_label(row: dict[str, Any]) -> str:
	return f"conversation in {row.get('room', '')} (seq {row.get('first_seq')}-{row.get('last_seq')})"


def _write_audit(
	allowed_rooms: frozenset[str],
	*,
	acting: str,
	query: str,
	purpose: str,
	actor_type: str,
	request_id: str | None,
	reason: str | None,
	reason_category: str | None,
	plan: budget.Plan,
	thread_rows: list[dict[str, Any]],
	authored_rows: list[dict[str, Any]],
	chunk_rows: list[dict[str, Any]],
) -> str:
	"""Write the fail-closed audit row. Raises through :func:`audit.record_or_refuse`.

	One child row per room actually read, with the seq range — not one per room the person
	*could* have read. "Triton looked at these four rooms" is the auditable fact; "Triton was
	allowed to look at forty" is noise that makes the log unreadable and hides the four.
	"""
	rooms_touched: dict[str, dict[str, Any]] = {}

	def _touch(room: str, seq: Any, count: int = 1) -> None:
		if not room:
			return
		bucket = rooms_touched.setdefault(
			room, {"room": room, "messages_read": 0, "first_seq": None, "last_seq": None}
		)
		bucket["messages_read"] += count
		value = cint(seq)
		if value:
			if bucket["first_seq"] is None or value < bucket["first_seq"]:
				bucket["first_seq"] = value
			if bucket["last_seq"] is None or value > bucket["last_seq"]:
				bucket["last_seq"] = value

	for row in thread_rows:
		_touch(row.get("room"), row.get("seq"))
	for row in authored_rows:
		_touch(row.get("room"), row.get("seq"))
	for row in chunk_rows:
		_touch(row.get("room"), row.get("last_seq"), count=1)
		_touch(row.get("room"), row.get("first_seq"), count=0)

	return audit.record_or_refuse(
		user=acting,
		purpose=purpose,
		actor_type=actor_type,
		rooms=list(rooms_touched.values()),
		query=query,
		reason=reason,
		# Signed here, with everything else, rather than written onto the row afterwards. The
		# writer normalises it again — the viewer already refused anything outside the Select,
		# so this is the belt that stops a future caller signing free text.
		reason_category=reason_category,
		request_id=request_id,
		message_count=len(thread_rows) + len(authored_rows),
		chunk_count=len(chunk_rows),
		token_count=plan.total_tokens,
		tiers_used=",".join(plan.tiers_used),
		context_truncated=1 if plan.context_truncated else 0,
	)


def _empty_result(acting: str, query: str, purpose: str, request_id: str | None) -> RetrievalResult:
	"""A person in no rooms. Audited anyway, because "nothing was read" is also a fact."""
	audit_row = audit.record_or_refuse(
		user=acting,
		purpose=purpose,
		actor_type="Triton",
		rooms=[],
		query=query,
		request_id=request_id,
		message_count=0,
		chunk_count=0,
		token_count=0,
		tiers_used="",
		context_truncated=0,
	)
	return RetrievalResult(audit_row=audit_row)


def _assert_retrieval_enabled() -> None:
	"""Both kill switches, checked before anything is read.

	``pause_retrieval`` is the incident control and ``enabled`` is the rollout one; either
	being off refuses the turn with a sentence rather than returning an empty result, because
	"chat retrieval is switched off" and "there is nothing relevant in your history" must not
	look the same to the person asking.
	"""
	if not cint(_setting("enabled")):
		raise RetrievalRefused("Chat is switched off (Chat Settings → Enable Chat).")
	if cint(_setting("pause_retrieval")):
		raise RetrievalRefused("Chat retrieval is paused (Chat Settings → Pause Retrieval).")


def _settings_dict() -> dict[str, Any]:
	try:
		return dict(frappe.get_cached_doc("Chat Settings").as_dict())
	except Exception:
		return {}


def _setting(fieldname: str) -> Any:
	try:
		return frappe.db.get_single_value("Chat Settings", fieldname)
	except Exception:
		return None


def _truthy(value: Any, *, default: bool) -> bool:
	if value is None:
		return default
	return bool(cint(value))


def _estimate(text: Any) -> int:
	from erpnext_enhancements.chat.doctype.chat_context_chunk.chat_context_chunk import estimate_tokens

	return estimate_tokens(str(text or ""))


# --------------------------------------------------------------- acting-as, for the filter


class _acting_as:
	"""Run a block with ``frappe.session.user`` set to the acting person.

	This exists because of where retrieval runs. The handler is a **background job**, and a
	background job's session user is ``Administrator`` — for which
	``permissions.membership_filter_sql`` returns ``1 = 1``. Passing the user explicitly to
	every helper is necessary and not sufficient: the shared fragment resolves its own default
	from the session, and a helper added later that forgets the argument would silently widen
	to every room.

	So the session is set for the duration, and :func:`_acting_user` reads it back. Restored in
	``finally``, including on the exception path, because a job that leaves the session
	pointing at a coworker would attribute everything it does afterwards to them.
	"""

	def __init__(self, user: str) -> None:
		self._user = user
		self._previous: str | None = None

	def __enter__(self) -> str:
		self._previous = frappe.session.user
		frappe.set_user(self._user)
		return self._user

	def __exit__(self, *_exc: object) -> None:
		if self._previous:
			frappe.set_user(self._previous)


def _acting_user() -> str:
	"""The user every membership fragment is built for. Never defaulted to the session
	implicitly — read explicitly here so the one place it comes from is greppable."""
	return frappe.session.user
