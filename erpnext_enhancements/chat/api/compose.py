# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Send, edit, delete, and the attachment gate. The SPA's write surface.

Everything here goes through Phase 2's ``sync.outbox`` rather than around it.
:func:`send_message` builds a ``Chat Message`` and calls :func:`outbox.insert_message`;
the document events Phase 2 registered then do the rest — allocate ``seq`` under the room
lock, denormalise the room, advance the author's own read mark, create the relay job if the
room is mirrored, publish ``chat_message_created`` to the room's document room, and fire
``seams.notify_new_message`` exactly once. **Phase 3 adds no second copy of any of that**,
which is what keeps the soak's "exactly once per genuinely new message, zero for echoes"
proof true through this phase.

The idempotency contract (§4.8)
===============================

The client mints a ``client_message_id`` **before** sending and reuses it on retry. That
makes retry safe against the one case that produces duplicates in every chat system ever
written: the insert succeeded and the response was lost. On the retry the unique index
fires, this module catches it, and returns **the existing row as success**.

Two exception classes, not one. ``frappe.DuplicateEntryError`` is raised *only* for a
primary-key collision; every other unique index raises ``frappe.UniqueValidationError``,
confirmed against production on 2026-08-10. ``client_message_id`` is not the primary key,
so catching only ``DuplicateEntryError`` — which the ADR recommends twice — makes the
dedupe **fail open** and ships the duplicate it was written to prevent.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import re
from typing import Any

import frappe
from frappe.utils import cint, now_datetime

from erpnext_enhancements.chat import permissions, realtime
from erpnext_enhancements.chat.api._common import (
	ATTACHMENT_DOCTYPE,
	MESSAGE_DOCTYPE,
	message_payload,
	require_message,
	require_room,
)

#: Body ceiling, in characters, enforced **here** rather than only in the composer. Client
#: validation is a courtesy; this is the control. Sized well above Google's per-message byte
#: budget on purpose — Phase 2 owns truncation-for-relay and stores the full text either
#: way, because ERPNext is the record of what was said.
MAX_MESSAGE_CHARS = 20000

#: How many attachments one message may carry. Bounded because the outbound relay uploads
#: them serially inside a job with a lease, and an unbounded count is an unbounded lease.
MAX_ATTACHMENTS_PER_MESSAGE = 10

#: ``@`` followed by a non-space run. Used only to *validate* what the client sent, never to
#: parse prose into mentions: the client sends structured mention data alongside the text
#: (§4.4) precisely so the server does not have to guess where a display name ends.
_MENTION_TOKEN = re.compile(r"@[^\s@]+")


def _duplicate_exceptions() -> tuple[type[Exception], ...]:
	"""Both unique-collision classes, tolerating a Frappe that lacks one of them.

	``UniqueValidationError`` is the one that actually fires for ``client_message_id``;
	``DuplicateEntryError`` is kept because it is what fires if the key ever becomes the
	primary one. ``getattr`` with a fallback so an older Frappe cannot turn this into an
	``AttributeError`` at import time on a permission-adjacent path.
	"""
	classes: list[type[Exception]] = []
	for attr in ("UniqueValidationError", "DuplicateEntryError"):
		candidate = getattr(frappe, attr, None)
		if isinstance(candidate, type) and issubclass(candidate, Exception):
			classes.append(candidate)
	return tuple(classes) or (frappe.ValidationError,)


@frappe.whitelist(methods=["POST"])
def send_message(
	room: str,
	text: str,
	client_message_id: str | None = None,
	parent_message: str | None = None,
	mentions: Any = None,
	attachments: Any = None,
) -> dict[str, Any]:
	"""Post a message. Idempotent on ``client_message_id``.

	Returns the stored row in the same shape the transcript renders, so the optimistic
	bubble is replaced rather than re-fetched.

	Args:
		room: the ``Chat Room``. Membership is asserted before anything is written.
		text: the body. Trimmed; an empty body with no attachments is refused.
		client_message_id: the client's idempotency key. Optional — Phase 2 derives one
			from the docname when it is absent — but a client that wants safe retry must
			send one, because a server-derived id is different on every attempt.
		parent_message: reply target. ``thread_root`` is derived from it by Phase 2, which
			flattens a reply-to-a-reply onto the same root.
		mentions: ``[{"mention_type", "user", "start_index", "length"}]`` from the composer.
		attachments: ``File`` docnames already uploaded and attached to this room's message.

	Raises:
		frappe.ValidationError: empty body, oversized body, or a mention/attachment the
			caller is not entitled to reference.
	"""
	user, name = require_room(room)
	body = (text or "").strip()
	files = _coerce_list(attachments)

	if not body and not files:
		frappe.throw(frappe._("A message needs text or an attachment."))
	if len(body) > MAX_MESSAGE_CHARS:
		frappe.throw(
			frappe._("That message is {0} characters; the limit is {1}.").format(
				len(body), MAX_MESSAGE_CHARS
			)
		)
	if len(files) > MAX_ATTACHMENTS_PER_MESSAGE:
		frappe.throw(
			frappe._("A message may carry at most {0} attachments.").format(
				MAX_ATTACHMENTS_PER_MESSAGE
			)
		)

	key = (client_message_id or "").strip()
	if key:
		existing = _existing_by_client_id(name, key)
		if existing:
			# The retry-after-success path, taken before any write. Same answer as a fresh
			# send, so the client's reconciliation has exactly one code path.
			return _sent(existing, deduplicated=True)

	if (parent_message or "").strip():
		# Gated rather than trusted: a reply's parent decides which room's thread it lands
		# in, and an unvalidated parent is a way to write into a room you can read.
		_parent_user, parent_row = require_message(parent_message, user=user)
		if parent_row["room"] != name:
			frappe.throw(frappe._("That reply target is in a different conversation."))

	doc = frappe.new_doc(MESSAGE_DOCTYPE)
	doc.room = name
	doc.sender = user
	doc.sender_email = frappe.db.get_value("User", user, "email") or user
	doc.sender_kind = "Human"
	doc.message_type = "File" if (files and not body) else "Text"
	doc.text = body
	doc.parent_message = (parent_message or "").strip() or None
	doc.sync_origin = "ERPNext"
	if key:
		doc.client_message_id = key

	for mention in _clean_mentions(mentions, room=name, body=body):
		doc.append("mentions", mention)

	from erpnext_enhancements.chat.sync import outbox

	try:
		outbox.insert_message(doc)
	except _duplicate_exceptions():
		# The insert lost a race with the caller's own retry, or with a second tab. Both
		# mean "this message is already stored", which is success. Re-read rather than
		# reusing `doc`: the winning row is the one everybody else will see.
		frappe.db.rollback()
		existing = _existing_by_client_id(name, key) if key else None
		if not existing:
			raise
		return _sent(existing, deduplicated=True)

	if files:
		_link_attachments(doc.name, name, files)

	_dispatch_triton(doc.name)

	row = frappe.db.get_value(MESSAGE_DOCTYPE, doc.name, "*", as_dict=True)
	return _sent(row)


def _dispatch_triton(message: str) -> None:
	"""Hand a ``@triton`` mention to the one handler. Called from here, not from a hook.

	Deliberately **not** a ``doc_events`` entry, and the distinction is invariant I1's: a
	document event runs inside the inserting transaction on a web worker, and the rule this
	package keeps is that no chat write path reaches an external service from there. The
	enqueue itself is safe — it is ``enqueue_after_commit`` and touches no network — but a
	hook is where the next person adds the call that is not, and the failure mode of that
	mistake is a Google timeout turning into a *failed message insert*.

	It is also called for the SPA, the desk bubble and any future ERPNext-side composer at
	once, because all three go through ``send_message``. The native Chat client's mentions
	arrive by the interaction webhook instead, and both paths meet at the same envelope.

	Never raises. A mention that cannot be dispatched must not fail the message it was
	written in: the person typed something to their colleagues as well as to Triton, and
	losing the message is strictly worse than losing the answer.
	"""
	try:
		from erpnext_enhancements.chat.invoke import dispatch

		dispatch.dispatch_spa_message(message)
	except Exception:
		try:
			frappe.log_error(f"Could not dispatch the @triton mention in {message}.", "Chat Triton")
		except Exception:
			pass


@frappe.whitelist(methods=["POST"])
def edit_message(message: str, text: str) -> dict[str, Any]:
	"""Edit your own message. Sets ``is_edited`` and lets Phase 2 propagate the change.

	Only the author, and only a live row. An admin "edit" is not a feature — decision #12
	gives oversight a *read*, and a body an auditor can change is not an audit trail.
	"""
	user, row = require_message(message)
	if (row.get("sender") or "") != user:
		frappe.throw(frappe._("You can only edit your own messages."), frappe.PermissionError)
	if cint(row.get("is_deleted")):
		frappe.throw(frappe._("That message was deleted."))

	body = (text or "").strip()
	if not body:
		frappe.throw(frappe._("An edited message still needs text. Delete it instead."))
	if len(body) > MAX_MESSAGE_CHARS:
		frappe.throw(
			frappe._("That message is {0} characters; the limit is {1}.").format(
				len(body), MAX_MESSAGE_CHARS
			)
		)

	doc = frappe.get_doc(MESSAGE_DOCTYPE, row["name"])
	doc.text = body
	doc.is_edited = 1
	doc.edited_at = now_datetime()
	# ignore_permissions: Chat Message ships with **zero DocPerm** (ADR §F.18.1 Layer 1), so
	# the stack refuses every writer including this one. Authorisation was decided above, by
	# the membership gate plus the author check — which is stricter than any DocPerm would be.
	doc.save(ignore_permissions=True)
	_publish_change(row["room"], realtime.EVENT_MESSAGE_EDITED, row["name"], cint(row.get("seq")))

	return message_payload(frappe.db.get_value(MESSAGE_DOCTYPE, row["name"], "*", as_dict=True))


@frappe.whitelist(methods=["POST"])
def delete_message(message: str) -> dict[str, Any]:
	"""Tombstone your own message. **Never a row delete.**

	``is_deleted = 1`` and the body **stays**. Google's tombstone is content-free, so
	ERPNext is the only copy of what was said, and Phase 6's audit needs it. Every read path
	filters ``is_deleted`` itself and :func:`_common.message_payload` strips the text on the
	way out, so the retained body is reachable by the auditor and by nobody else.

	``outbox.refuse_hard_delete`` enforces the same rule on ``on_trash``; this is the door
	users actually walk through.
	"""
	user, row = require_message(message)
	if (row.get("sender") or "") != user:
		frappe.throw(frappe._("You can only delete your own messages."), frappe.PermissionError)
	if cint(row.get("is_deleted")):
		return message_payload(frappe.db.get_value(MESSAGE_DOCTYPE, row["name"], "*", as_dict=True))

	doc = frappe.get_doc(MESSAGE_DOCTYPE, row["name"])
	doc.is_deleted = 1
	doc.deleted_at = now_datetime()
	doc.deleted_by = user
	doc.deletion_source = "ERPNext"
	doc.save(ignore_permissions=True)
	_publish_change(row["room"], realtime.EVENT_MESSAGE_DELETED, row["name"], cint(row.get("seq")))

	return message_payload(frappe.db.get_value(MESSAGE_DOCTYPE, row["name"], "*", as_dict=True))


@frappe.whitelist()
def prepare_upload(room: str) -> dict[str, Any]:
	"""Gate an upload before the bytes move, and hand back the limits the composer enforces.

	The SPA uploads through Frappe's own ``/api/method/upload_file`` (so it inherits the
	framework's size handling, virus hooks and ``File`` creation) and then calls
	:func:`send_message` with the resulting ``File`` names — **upload first, then send**, so
	a failed upload never produces a message pointing at nothing.

	This call exists so the composer can refuse an oversized file *before* a 40 MB body
	crosses a mobile connection, and so the "you are allowed to upload into this room"
	decision is made server-side even though the upload itself is a framework endpoint.
	"""
	_user, name = require_room(room)

	from erpnext_enhancements.chat.sync import attachments as attachments_sync

	limits = attachments_sync.resolve_limits()
	return {
		"room": name,
		"max_file_bytes": cint(getattr(limits, "outbound_max_bytes", 0))
		or cint(getattr(limits, "site_max_bytes", 0)),
		"max_files": MAX_ATTACHMENTS_PER_MESSAGE,
		"max_chars": MAX_MESSAGE_CHARS,
		# The doctype/name an upload must be attached to. Private, always: a public file is
		# served by the web server with no auth at all, and no hook of ours is consulted.
		"is_private": 1,
	}


# --------------------------------------------------------------------------- internals


def _publish_change(room: str, event: str, message: str, seq: int) -> None:
	"""Announce an ERPNext-side edit or delete to the room's document room.

	**Published here rather than from ``outbox.propagate_message_change``, and that is a
	reported gap rather than a design.** ``sync/inbound.py:_publish`` fans out
	``chat_message_edited`` / ``chat_message_deleted`` for a change arriving *from Google*;
	``propagate_message_change`` — the ERPNext-side twin — relays the change onward but
	publishes no realtime event at all. So before this phase, an edit made in ERPNext
	reached the Google space and reached no other ERPNext client until they refreshed.

	Phase 3's own write path announcing its own writes closes that for the SPA without
	touching the sync engine (§5 forbids changing it, and Phase 2's soak asserts against it).
	It does **not** close the general case: an edit made through the desk, a patch, or any
	future admin tool still publishes nothing. That asymmetry belongs to whoever owns
	``propagate_message_change`` and is carried into the checkpoint report as a Phase 2
	defect rather than papered over here.

	Never raises. The change is already committed; a failed publish costs one refresh.
	"""
	try:
		realtime.publish_room_event(
			room, event, {"message": message, "room": room, "seq": cint(seq), "origin": "ERPNext"}
		)
	except Exception:
		frappe.log_error(
			title="chat compose: realtime publish failed",
			message=f"{event} message={message} room={room}",
		)


def _sent(row: Any, *, deduplicated: bool = False) -> dict[str, Any]:
	"""The send response. Same shape as a transcript row, plus the dedupe flag.

	"Same shape" has to include the mention spans. ``reconcile()`` assigns this payload over
	the optimistic entry the composer rendered, so a response without them would strip the
	sender's own mention chips the instant their message was acknowledged — and only for the
	sender, which is the kind of asymmetry nobody reproduces.
	"""
	payload = message_payload(row)
	# Delegated rather than reimplemented. `_attach_mentions` carries the membership filter,
	# and a second child-row query here would be a second thing to keep scoped — the exact
	# shape `tests/test_chat_rawsql_guard.py` exists to prevent.
	from erpnext_enhancements.chat.api.history import _attach_citations, _attach_mentions

	_attach_mentions([payload])
	_attach_citations([payload])
	payload["deduplicated"] = deduplicated
	return payload


def _existing_by_client_id(room: str, key: str) -> Any:
	"""Look up a message by the client's idempotency key, scoped to the room.

	Scoped to the room even though ``client_message_id`` is globally unique, because the
	caller has been gated on *this* room and a cross-room lookup would answer a question
	about a room they were never checked against.
	"""
	scope = permissions.membership_filter_sql("`m`.`room`", seq_column="`m`.`seq`")
	rows = frappe.db.sql(
		f"""select `m`.* from `tabChat Message` `m`
			where `m`.`room` = %(room)s and `m`.`client_message_id` = %(key)s and {scope}
			limit 1""",
		{"room": room, "key": key},
		as_dict=True,
	)
	return rows[0] if rows else None


def _coerce_list(value: Any) -> list[str]:
	"""``frappe.xcall`` sends arrays as JSON strings on some paths and lists on others."""
	if not value:
		return []
	if isinstance(value, str):
		try:
			value = frappe.parse_json(value)
		except Exception:
			return []
	if isinstance(value, dict):
		value = list(value.values())
	if not isinstance(value, list | tuple):
		return []
	return [str(item).strip() for item in value if str(item).strip()]


def _clean_mentions(raw: Any, *, room: str, body: str) -> list[dict[str, Any]]:
	"""Validate the composer's structured mentions. Never re-parses prose.

	The client sends offsets because only the client knows where a display name ended. The
	server's job is to refuse the ones that would be a privilege escalation or a lie:

	* a ``User`` mention of somebody who is not an active member of the room is dropped —
	  it would otherwise be a way to make a name appear in a room the mentioned person
	  cannot open, and Phase 4 would then try to notify them about it;
	* ``@triton`` is always allowed, in every room (§4.4), and carries no user;
	* offsets outside the body are dropped rather than clamped. A clamped offset renders a
	  chip over the wrong words, which looks like corruption.
	"""
	if isinstance(raw, str):
		try:
			raw = frappe.parse_json(raw)
		except Exception:
			return []
	if not isinstance(raw, list | tuple):
		return []

	# Raw SQL with the shared filter rather than ``frappe.get_all``, which is
	# ``get_list(ignore_permissions=True)`` wearing a friendlier name. The caller has already
	# been gated on this room, so the filter is redundant *today* — and it is ANDed in anyway,
	# because this function is one refactor away from being called with a room the caller
	# supplied, and ``tests/test_chat_rawsql_guard.py`` will not let it drift either way.
	scope = permissions.membership_filter_sql("`m`.`room`")
	members = {
		row["user"]
		for row in frappe.db.sql(
			f"""select `m`.`user` from `tabChat Room Member` `m`
				where `m`.`room` = %(room)s and `m`.`is_active` = 1 and {scope}""",
			{"room": room},
			as_dict=True,
		)
	}

	cleaned: list[dict[str, Any]] = []
	seen: set[tuple[str, str, int]] = set()
	for entry in raw:
		if not isinstance(entry, dict):
			continue
		kind = (entry.get("mention_type") or "User").strip()
		start = cint(entry.get("start_index"))
		length = cint(entry.get("length"))
		if start < 0 or length < 1 or start + length > len(body):
			continue

		if kind == "Triton":
			target = ""
		else:
			target = (entry.get("user") or "").strip()
			if not target or target not in members:
				continue
			kind = "User"

		fingerprint = (kind, target, start)
		if fingerprint in seen:
			continue
		seen.add(fingerprint)
		cleaned.append(
			{"mention_type": kind, "user": target or None, "start_index": start, "length": length}
		)

	return cleaned


def _link_attachments(message: str, room: str, files: list[str]) -> None:
	"""Re-point uploaded ``File`` rows at the message and record ``Chat Attachment`` rows.

	The upload happened before the message existed, so the ``File`` was attached to the
	*room*. This moves it onto the message, which is what makes Frappe's private-file check
	delegate to ``Chat Message``'s permission — the single decision that makes attachment
	security correct by construction (ADR §F.8).

	A file the caller did not upload is refused. Without that check, ``send_message`` would
	be an "attach any File in the system to a message and read it through the room" tool.
	"""
	user = frappe.session.user
	for file_name in files:
		row = frappe.db.get_value(
			"File",
			file_name,
			["name", "file_url", "file_name", "file_size", "is_private", "owner"],
			as_dict=True,
		)
		if not row:
			frappe.throw(frappe._("That upload is no longer available."))
		if (row.get("owner") or "") != user:
			frappe.throw(frappe._("You can only attach files you uploaded."), frappe.PermissionError)
		if not cint(row.get("is_private")):
			frappe.throw(
				frappe._(
					"Chat attachments must be private. A public file is served with no "
					"authentication at all."
				)
			)

		frappe.db.set_value(
			"File",
			row["name"],
			{"attached_to_doctype": MESSAGE_DOCTYPE, "attached_to_name": message},
			update_modified=False,
		)

		attachment = frappe.new_doc(ATTACHMENT_DOCTYPE)
		attachment.message = message
		attachment.room = room
		attachment.source = "Uploaded"
		attachment.ingest_state = "Stored"
		attachment.file = row["name"]
		attachment.file_name = row.get("file_name") or ""
		attachment.file_size = cint(row.get("file_size"))
		attachment.insert(ignore_permissions=True)

	frappe.db.set_value(MESSAGE_DOCTYPE, message, "has_attachments", 1, update_modified=False)
