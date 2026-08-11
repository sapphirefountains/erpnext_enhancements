# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Message search, room-scoped and global. The endpoint most likely to leak, written first.

A search box is precisely where somebody writes raw SQL for performance, and raw SQL is the
one path the permission stack does not cover: ``permission_query_conditions`` is appended by
``get_list`` and by nothing else. So this module's single query ANDs in
:func:`chat.permissions.membership_filter_sql` itself, and
``tests/test_chat_rawsql_guard.py`` fails the build if that ever stops being true.

**Why ``LIKE`` and not ``MATCH``.** ``Chat Message.text_plain`` exists and is documented as
suitable for a FULLTEXT index, but no FULLTEXT index has been created — Phase 1 shipped the
column, not the index. At this data volume (a fifty-person company, currently zero stored
messages) an indexed prefix scan plus a ``LIKE '%term%'`` filter over one user's rooms is
comfortably fast, and adding a FULLTEXT index is a migration with its own rollout. The
choice is stated rather than left implicit, per §4.4, and the upgrade path is one patch:
``alter table `tabChat Message` add fulltext index (text_plain)`` plus a ``MATCH … AGAINST``
branch here. Revisit when a room passes ~100k messages.

**The oversight read is audited.** Decision #12 lets a configured role read conversations it
is not a participant in. ``membership_filter_sql`` returns ``"1 = 1"`` for that role — which
is correct and is *why* the audit call has to be here: an unrestricted search is exactly the
non-participant read the audit obligation is about. One call to
:func:`chat.permissions.note_privileged_read` per privileged search, at the one place a
privileged search can happen.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint

from erpnext_enhancements.chat import permissions
from erpnext_enhancements.chat.api._common import (
	TOMBSTONE_TEXT,
	dm_counterpart,
	page_size,
	require_room,
	require_session,
)
from erpnext_enhancements.chat.links import build_chat_route

#: Below this many characters a query matches so much that the result list is noise and the
#: scan is a table scan. Two characters is the shortest useful search ("SO", "PO", a set of
#: initials); one is not a search.
MIN_QUERY_CHARS = 2

#: Characters either side of the match in the returned snippet. Enough to read the sentence,
#: short enough that fifty results are not a transcript.
SNIPPET_RADIUS = 90


@frappe.whitelist()
def search_messages(
	query: str,
	room: str | None = None,
	limit: Any = None,
	before_seq: Any = None,
) -> dict[str, Any]:
	"""Search the caller's readable history. Room-scoped when ``room`` is given.

	Results are newest-first and keyset-paged on ``seq`` exactly like the transcript, so
	"more results" costs the same at any depth.

	Each result carries the deep link that opens it in context — built by
	:func:`chat.links.build_chat_route`, the **one** builder this repo has, shared with
	Phase 4's notifications and Phase 5's ``chat_message`` citations. A second URL-shape
	implementation is how a notification lands on an error page while the SPA's own links
	work fine.

	Deleted messages are excluded. A tombstone has no text to match and surfacing "this
	message was deleted" as a search hit is noise; the transcript still shows it in place.
	"""
	user = require_session()
	term = (query or "").strip()
	if len(term) < MIN_QUERY_CHARS:
		return {"query": term, "results": [], "has_more": False, "too_short": True}

	size = page_size(limit, default=25)

	if (room or "").strip():
		_user, scoped_room = require_room(room, user=user)
	else:
		scoped_room = None

	scope = permissions.membership_filter_sql("`m`.`room`", user, seq_column="`m`.`seq`")
	if scope == "1 = 1":
		# Unrestricted means the oversight role (or Administrator). That is a read of
		# conversations this person is not in, which is exactly what decision #12 requires be
		# audited. One call, at the one place it can happen.
		permissions.note_privileged_read("Chat Message", scoped_room, user, "read")

	where = [
		"coalesce(`m`.`is_deleted`, 0) = 0",
		"coalesce(`m`.`text_plain`, '') like %(pattern)s",
		scope,
	]
	params: dict[str, Any] = {
		# escape() the term's own wildcards so a user searching for "100%" does not match
		# every message in the company.
		"pattern": "%" + term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%",
		"limit": size,
	}
	if scoped_room:
		where.append("`m`.`room` = %(room)s")
		params["room"] = scoped_room
	if before_seq not in (None, ""):
		where.append("`m`.`seq` < %(before_seq)s")
		params["before_seq"] = cint(before_seq)

	rows = frappe.db.sql(
		f"""select
				`m`.`name`, `m`.`room`, `m`.`seq`, `m`.`sender`, `m`.`sender_email`,
				`m`.`sender_kind`, `m`.`text_plain`, `m`.`thread_root`, `m`.`creation`,
				`r`.`title` as `room_title`, `r`.`room_type`,
				`r`.`dm_user_1`, `r`.`dm_user_2`,
				`u`.`full_name` as `sender_name`
			from `tabChat Message` `m`
			join `tabChat Room` `r` on `r`.`name` = `m`.`room`
			left join `tabUser` `u` on `u`.`name` = `m`.`sender`
			where {" and ".join(where)}
			order by `m`.`seq` desc
			limit %(limit)s""",
		params,
		as_dict=True,
	)

	results = [_result(row, term, user) for row in rows]
	return {
		"query": term,
		"room": scoped_room,
		"results": results,
		"has_more": len(rows) >= size,
	}


def _result(row: dict[str, Any], term: str, viewer: str) -> dict[str, Any]:
	"""One search hit, with a snippet and the shared deep link.

	The snippet carries ``match_start``/``match_length`` rather than pre-wrapped ``<mark>``
	HTML. Message bodies are user-authored, and handing the client a string of HTML to
	``innerHTML`` is a stored-XSS vector with a straight path from any employee to every
	employee. The client highlights by slicing at those offsets and building nodes with
	``textContent``.

	``room_title`` is resolved **here**, through the same :func:`_common.dm_counterpart` the
	room list uses, because a DM has no stored title — it is named after whoever the viewer is
	not. This module builds its own result dict instead of going through ``room_payload``, and
	that is exactly how it shipped emitting ``""`` for every DM hit, which the client rendered
	as the room's random hash docname. One shared resolver, called from both places, is the
	only version of this that stays fixed.

	The ``User`` lookup inside ``dm_counterpart`` is a primary-key read per DM hit, bounded by
	the page size (25 by default). Worth it to keep one definition of the label.
	"""
	text = row.get("text_plain") or ""
	lowered = text.lower()
	index = lowered.find(term.lower())
	if index < 0:
		snippet, offset = text[: SNIPPET_RADIUS * 2], -1
	else:
		start = max(0, index - SNIPPET_RADIUS)
		end = min(len(text), index + len(term) + SNIPPET_RADIUS)
		snippet = ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")
		offset = index - start + (1 if start > 0 else 0)

	title = (row.get("room_title") or "").strip()
	if not title and (row.get("room_type") or "") == "Direct Message":
		_other, title = dm_counterpart(row, viewer)

	return {
		"message": row["name"],
		"room": row["room"],
		# Never falls back to `row["room"]`. A docname here is a random hash, and the client
		# rendering one is the defect this resolution exists to prevent.
		"room_title": title or "",
		"room_type": row.get("room_type") or "Group",
		"seq": cint(row.get("seq")),
		"sender": row.get("sender") or None,
		"sender_name": row.get("sender_name") or row.get("sender_email") or "",
		"sender_kind": row.get("sender_kind") or "Human",
		"creation": row.get("creation") and str(row["creation"]) or None,
		"snippet": snippet,
		"match_start": offset,
		"match_length": len(term) if offset >= 0 else 0,
		"thread": row.get("thread_root") or None,
		"route": build_chat_route(
			row["room"], message=row["name"], thread=row.get("thread_root") or None
		),
	}


def tombstone_text() -> str:
	"""Re-exported so the room-preview and search surfaces agree on one wording."""
	return TOMBSTONE_TEXT
