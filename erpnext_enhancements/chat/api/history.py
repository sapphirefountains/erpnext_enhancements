# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Transcript paging, thread paging, and the deep-link "page containing this message" read.

**Keyset paging on ``seq``, never ``OFFSET``, never a timestamp.**

``OFFSET`` over a growing table gets slower the further back you scroll and — worse — can
skip or duplicate rows when new messages arrive mid-scroll, which presents as "a message
went missing when I scrolled up" and is unreproducible on a quiet site. Keyset paging
(``where seq < :cursor order by seq desc limit :n``) is O(page) at any depth and is stable
under concurrent inserts.

``seq`` rather than ``creation`` is the schema's own choice and it is load-bearing: ``seq``
is allocated under the room's row lock, immutable, dense per room, and has no timezone. A
late-arriving inbound message (Google ``createTime`` weeks old) still gets the next ``seq``
and renders **in ``seq`` order** with a "recovered" affordance rather than being re-sorted
into the past, which is the only ordering two systems with independent clocks can agree on.
The Phase 3 brief §4.13 says "keyed on ``creation``"; the schema, ``Chat Message.seq``'s
own field description, and Phase 5's digest watermarks all say ``seq``. Recorded in
``chat/README.md`` as a deliberate divergence.

The index this rides on is Phase 1's composite ``unique(room, seq)``. ``EXPLAIN`` on the
history query must show it in use rather than a filesort over the room's whole history;
that check is a bench task and is listed as outstanding in the phase report.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import cint

from erpnext_enhancements.chat import permissions
from erpnext_enhancements.chat.api._common import (
	attachment_payload,
	mention_payload,
	is_privileged_scope,
	message_payload,
	page_size,
	record_privileged_content_read,
	require_message,
	require_room,
)

#: Columns every transcript read selects. One tuple so history, thread, backfill and the
#: deep-link read cannot drift into selecting different shapes — the client has one message
#: renderer and it must be handed one shape.
_MESSAGE_COLUMNS = """
	`m`.`name`, `m`.`room`, `m`.`seq`, `m`.`sender`, `m`.`sender_email`, `m`.`sender_kind`,
	`m`.`message_type`, `m`.`text`, `m`.`parent_message`, `m`.`thread_root`,
	`m`.`has_attachments`, `m`.`is_edited`, `m`.`edited_at`, `m`.`is_deleted`, `m`.`deleted_at`,
	`m`.`creation`, `m`.`client_message_id`, `m`.`late_arrival`, `m`.`sync_state`
"""


@frappe.whitelist()
def get_messages(
	room: str,
	before_seq: Any = None,
	after_seq: Any = None,
	limit: Any = None,
	thread: str | None = None,
) -> dict[str, Any]:
	"""One page of a room's transcript, or of one thread.

	Three cursors, one query shape:

	* neither cursor — the **newest** page. What the SPA asks for on open.
	* ``before_seq`` — scrollback. Rows strictly older than the cursor, newest-first
	  internally, returned oldest-first so the client can prepend without re-sorting.
	* ``after_seq`` — **the reconnect backfill** (§4.5). Rows strictly newer than the
	  newest row the client holds. This is the reconciliation path that makes realtime an
	  accelerator rather than a dependency: Frappe realtime is fire-and-forget and an event
	  fired while a client was disconnected is simply gone.

	``thread`` scopes to one thread root (the thread pane). Without it the main transcript
	excludes replies — ``thread_root is null`` — so a threaded reply updates the root's
	reply count instead of appearing as a new top-level message.

	Deleted rows are **returned**, with their body stripped by
	:func:`_common.message_payload`. A message that silently vanishes reads as a bug, and
	decision #12 wants the tombstone visible.

	**Records a `Chat Retrieval Audit` row when the caller reached the room through the
	oversight escape hatch** (invariant I9). A member reading their own room records nothing.
	See :func:`_page` for why the recording lives at the whitelisted entry point rather than
	one layer down.
	"""
	user, name = require_room(room)
	rows, payload = _page(name, before_seq=before_seq, after_seq=after_seq, limit=limit, thread=thread)
	privileged = is_privileged_scope(
		permissions.membership_filter_sql("`m`.`room`", seq_column="`m`.`seq`")
	)
	record_privileged_content_read(rows, user=user, privileged=privileged, purpose="transcript")
	return payload


def _page(
	name: str,
	*,
	before_seq: Any = None,
	after_seq: Any = None,
	limit: Any = None,
	thread: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
	"""The transcript query, returning the raw rows **and** the client payload.

	Split out from :func:`get_messages` so that :func:`get_thread` — which needs the same
	page plus its root row — can record **one** audit row covering both rather than two
	covering half each. §4.D.2 asks for exactly one record per non-participant read, and a
	call that produces two records is as wrong as one that produces none: it makes the
	compliance query's counts meaningless in the direction that looks like more diligence.

	The raw rows come back because ``message_payload`` strips deleted bodies and drops
	columns; the audit record needs the ``seq`` values, which is a question about what was
	read rather than about what was shown.

	**Deliberately not whitelisted, and the recording is deliberately not in here.** If this
	took a "should I record?" flag it would be one query parameter away from being the
	endpoint that reads a transcript without recording it.
	"""
	size = page_size(limit)
	scope = permissions.membership_filter_sql("`m`.`room`", seq_column="`m`.`seq`")

	params: dict[str, Any] = {"room": name, "limit": size}
	where = [f"`m`.`room` = %(room)s", scope]

	if (thread or "").strip():
		where.append("`m`.`thread_root` = %(thread)s")
		params["thread"] = thread.strip()
	else:
		where.append("`m`.`thread_root` is null")

	if after_seq not in (None, ""):
		# Ascending: the backfill wants the OLDEST unseen rows first, so a client that was
		# offline for a long time fills the gap in order and stops at the limit rather than
		# skipping the middle of it.
		where.append("`m`.`seq` > %(after_seq)s")
		params["after_seq"] = cint(after_seq)
		order = "asc"
	else:
		if before_seq not in (None, ""):
			where.append("`m`.`seq` < %(before_seq)s")
			params["before_seq"] = cint(before_seq)
		order = "desc"

	rows = frappe.db.sql(
		f"""select {_MESSAGE_COLUMNS}
			from `tabChat Message` `m`
			where {" and ".join(where)}
			order by `m`.`seq` {order}
			limit %(limit)s""",
		params,
		as_dict=True,
	)

	if order == "desc":
		rows.reverse()

	messages = [message_payload(row) for row in rows]
	_attach_attachments(messages)
	_attach_mentions(messages)
	_attach_citations(messages)
	if not (thread or "").strip():
		_attach_reply_counts(name, messages)

	return rows, {
		"room": name,
		"thread": (thread or "").strip() or None,
		"messages": messages,
		# `has_more` is "the page came back full", not a second COUNT(*). A full page that
		# happens to be the last one costs the client one empty fetch; a COUNT(*) over the
		# room's history costs every scroll.
		"has_more": len(rows) >= size,
		"page_size": size,
	}


@frappe.whitelist()
def get_thread(root: str, limit: Any = None) -> dict[str, Any]:
	"""A thread: its root message plus its replies, oldest first.

	Threading is exactly one level deep by construction — ``outbox._set_thread_root``
	attaches a reply-to-a-reply to the same root — so this is one indexed range scan on
	``(thread_root, seq)`` and there is no recursion to write.

	Google cannot represent this at all: ``spaceThreadingState`` is output-only and
	``spaces.setup`` states verbatim that threaded replies are not supported, so the Chat
	side renders flat and **the thread structure is ERPNext's alone**. That makes the thread
	pane a feature with no Google dependency, which is why it works with chat dormant.
	"""
	user, row = require_message(root)
	root_name = row["name"]
	# A reply's own thread_root is the root; asking for a thread by a reply's name should
	# open the thread it is in rather than an empty one.
	root_name = (row.get("thread_root") or root_name) or root_name

	scope = permissions.membership_filter_sql("`m`.`room`", seq_column="`m`.`seq`")
	root_row = frappe.db.sql(
		f"""select {_MESSAGE_COLUMNS}
			from `tabChat Message` `m`
			where `m`.`name` = %(root)s and {scope}""",
		{"root": root_name},
		as_dict=True,
	)
	if not root_row:
		frappe.throw(frappe._("That thread is no longer available."), frappe.DoesNotExistError)

	reply_rows, page = _page(root_row[0]["room"], thread=root_name, limit=limit)
	# One record for the whole thread — root and replies together. Calling `get_messages`
	# here (as this did) would have recorded the replies and not the root, and would have
	# done it from inside another endpoint's audit row.
	record_privileged_content_read(
		root_row + reply_rows,
		user=user,
		privileged=is_privileged_scope(scope),
		purpose="thread",
	)

	root_payload = message_payload(root_row[0])
	_attach_attachments([root_payload])
	_attach_mentions([root_payload])
	_attach_citations([root_payload])

	return {
		"root": root_payload,
		"room": root_row[0]["room"],
		"messages": page["messages"],
		"has_more": page["has_more"],
	}


@frappe.whitelist()
def get_message_context(message: str, limit: Any = None) -> dict[str, Any]:
	"""The history page **containing** a given message, plus one page either side.

	This is what a deep link resolves to — from a notification (Phase 4), from a citation
	(Phase 5), from a search result, or from a pasted URL. The client cannot page backwards
	from "newest" until it happens to reach a message from March; it asks for the window.

	Returns the thread context too, so ``?message=`` on a *reply* opens the thread pane on
	the right root rather than scrolling the main transcript to a message that is not in it.
	"""
	user, row = require_message(message)
	size = page_size(limit)
	target_seq = cint(row["seq"])
	thread_root = row.get("thread_root") or None

	# One window, centred: half a page older and half a page newer than the target. Two
	# queries rather than one with a BETWEEN, because the room may have gaps (deleted rows
	# keep their seq) and "50 rows either side" is what the client's scroll needs, not
	# "seq ± 25".
	scope = permissions.membership_filter_sql("`m`.`room`", seq_column="`m`.`seq`")
	thread_clause = (
		"`m`.`thread_root` = %(thread)s" if thread_root else "`m`.`thread_root` is null"
	)
	params: dict[str, Any] = {"room": row["room"], "seq": target_seq, "limit": size}
	if thread_root:
		params["thread"] = thread_root

	older = frappe.db.sql(
		f"""select {_MESSAGE_COLUMNS}
			from `tabChat Message` `m`
			where `m`.`room` = %(room)s and {thread_clause} and `m`.`seq` <= %(seq)s and {scope}
			order by `m`.`seq` desc
			limit %(limit)s""",
		params,
		as_dict=True,
	)
	newer = frappe.db.sql(
		f"""select {_MESSAGE_COLUMNS}
			from `tabChat Message` `m`
			where `m`.`room` = %(room)s and {thread_clause} and `m`.`seq` > %(seq)s and {scope}
			order by `m`.`seq` asc
			limit %(limit)s""",
		params,
		as_dict=True,
	)

	older.reverse()
	record_privileged_content_read(
		older + newer,
		user=user,
		privileged=is_privileged_scope(scope),
		purpose="message_context",
	)
	messages = [message_payload(r) for r in older + newer]
	_attach_attachments(messages)
	_attach_mentions(messages)
	_attach_citations(messages)
	if not thread_root:
		_attach_reply_counts(row["room"], messages)

	return {
		"room": row["room"],
		"message": row["name"],
		"thread": thread_root,
		"messages": messages,
		"has_more_before": len(older) >= size,
		"has_more_after": len(newer) >= size,
	}


def _attach_reply_counts(room: str, messages: list[dict[str, Any]]) -> None:
	"""Fill ``reply_count`` on the thread roots in this page. One grouped query, or none.

	Only messages that could *be* a root are asked about, so a page with no threads costs
	nothing. Deleted replies are excluded from the count — a thread whose replies were all
	deleted should read "no replies" rather than advertising three tombstones.
	"""
	names = [m["name"] for m in messages if m.get("name")]
	if not names:
		return

	scope = permissions.membership_filter_sql("`m`.`room`", seq_column="`m`.`seq`")
	rows = frappe.db.sql(
		f"""select `m`.`thread_root` as `root`, count(*) as `replies`, max(`m`.`seq`) as `last_seq`
			from `tabChat Message` `m`
			where `m`.`room` = %(room)s
				and `m`.`thread_root` in %(names)s
				and coalesce(`m`.`is_deleted`, 0) = 0
				and {scope}
			group by `m`.`thread_root`""",
		{"room": room, "names": tuple(names)},
		as_dict=True,
	)
	counts = {row["root"]: row for row in rows}
	for message in messages:
		row = counts.get(message["name"])
		message["reply_count"] = cint(row["replies"]) if row else 0
		message["thread_last_seq"] = cint(row["last_seq"]) if row else 0


def _attach_mentions(messages: list[dict[str, Any]]) -> None:
	"""Fill ``mentions`` on the messages in this page. One query, or none.

	Without this the write side is inert. :func:`compose.send_message` validates and stores
	``Chat Mention`` child rows on every send, and both renderers call ``tokenizeMentions(text,
	message.mentions || [])`` — but ``message_payload`` builds an explicit dict, so a child row
	that is never selected can never reach them. The whole feature therefore rendered as plain
	text: ``.ee-mention`` was a CSS rule nothing could match, and a person who was mentioned by
	name saw their name styled exactly like every other word in the sentence.

	The spans are ``(start_index, length)`` offsets into the body, not markup. The client
	slices at them and builds nodes; nothing here emits HTML, for the reason
	:func:`search._result` gives at length.

	Deleted messages are skipped: :func:`_common.message_payload` has already replaced the
	body with the tombstone, so offsets into the original text now point at nothing, and
	handing the client spans that overrun the string it holds is how a renderer throws.
	"""
	wanted = [m["name"] for m in messages if m.get("name") and not m.get("is_deleted")]
	if not wanted:
		return

	scope = permissions.membership_filter_sql("`m`.`room`", seq_column="`m`.`seq`")
	rows = frappe.db.sql(
		f"""select
				`x`.`parent` as `message`, `x`.`mention_type`, `x`.`user`,
				`x`.`start_index`, `x`.`length`
			from `tabChat Mention` `x`
			join `tabChat Message` `m` on `m`.`name` = `x`.`parent`
			where `x`.`parent` in %(names)s
				and `x`.`parenttype` = 'Chat Message'
				and {scope}
			order by `x`.`parent` asc, `x`.`start_index` asc""",
		{"names": tuple(wanted)},
		as_dict=True,
	)

	by_message: dict[str, list[dict[str, Any]]] = {}
	for row in rows:
		by_message.setdefault(row["message"], []).append(mention_payload(row))

	for message in messages:
		if not message.get("is_deleted"):
			message["mentions"] = by_message.get(message["name"], [])


def _attach_citations(messages: list[dict[str, Any]]) -> None:
	"""Fill ``citations`` on any Triton answers in this page. One query, or none.

	Without this the Phase 3 renderer is inert in exactly the way ``_attach_mentions`` was
	before it was written: ``public/js/chat/message_view.js`` reads ``message.citations`` and
	calls ``applyCitations``, which is a **no-op on an empty manifest** — so every inline
	``[[ref:N]]`` marker would be stripped from the answer and nothing would say why. Not an
	error, not a broken link: the numbers would simply not be there, and the sources row would
	fall back to the pre-manifest renderer.

	The manifest lives on ``Triton Invocation Log`` rather than on ``Chat Message`` because the
	hot table must not grow a column that is null for every message but one sender's, and
	because a manifest is metadata about a *turn*. The join is on ``answer_message``.

	**Scoped like every other read here.** The membership fragment is ANDed in, so a manifest
	cannot be read for a message in a room the caller is not in — which matters more than it
	looks: a citation label carries a person's name and a room's identity, so an unscoped read
	here would leak who is talking to whom without leaking a single message body.

	Deleted messages are skipped, same as mentions: the body has already been replaced by the
	tombstone, so there is nothing left for a citation to annotate.
	"""
	wanted = [m["name"] for m in messages if m.get("name") and not m.get("is_deleted")]
	if not wanted:
		return

	scope = permissions.membership_filter_sql("`m`.`room`", seq_column="`m`.`seq`")
	try:
		rows = frappe.db.sql(
			f"""select `t`.`answer_message` as `message`, `t`.`citations` as `citations`
				from `tabTriton Invocation Log` `t`
				join `tabChat Message` `m` on `m`.`name` = `t`.`answer_message`
				where `t`.`answer_message` in %(names)s
					and `t`.`citations` is not null
					and `t`.`citations` != ''
					and {scope}""",
			{"names": tuple(wanted)},
			as_dict=True,
		)
	except Exception:
		# A transcript must render without its citations rather than not render. This is the
		# one hydrator whose data is an enrichment rather than part of the message, so its
		# failure degrades to Phase 3's behaviour instead of failing the page.
		return

	by_message: dict[str, list[dict[str, Any]]] = {}
	for row in rows:
		try:
			entries = json.loads(row.get("citations") or "[]")
		except Exception:
			continue
		if isinstance(entries, list):
			by_message[row["message"]] = entries

	for message in messages:
		if not message.get("is_deleted") and message["name"] in by_message:
			message["citations"] = by_message[message["name"]]


def _attach_attachments(messages: list[dict[str, Any]]) -> None:
	"""Fill ``attachments`` on the messages in this page that have any. One query, or none.

	``file_url`` is joined off ``tabFile`` rather than stored twice. The bytes behind it are
	guarded by Frappe's own private-file check, which delegates to ``Chat Message``'s
	permission because ADR §F.8 requires ``is_private = 1`` on every chat attachment — so
	the URL in this payload is safe to hand to a member and useless to anybody else.
	"""
	wanted = [m["name"] for m in messages if m.get("has_attachments")]
	if not wanted:
		return

	scope = permissions.membership_filter_sql("`a`.`room`")
	rows = frappe.db.sql(
		f"""select
				`a`.`name`, `a`.`message`, `a`.`file`, `a`.`file_name`,
				`a`.`content_type`, `a`.`file_size`, `a`.`ingest_state`,
				`f`.`file_url`
			from `tabChat Attachment` `a`
			left join `tabFile` `f` on `f`.`name` = `a`.`file`
			where `a`.`message` in %(names)s and {scope}
			order by `a`.`creation` asc""",
		{"names": tuple(wanted)},
		as_dict=True,
	)

	by_message: dict[str, list[dict[str, Any]]] = {}
	for row in rows:
		by_message.setdefault(row["message"], []).append(attachment_payload(row))

	for message in messages:
		if message.get("has_attachments"):
			message["attachments"] = by_message.get(message["name"], [])
