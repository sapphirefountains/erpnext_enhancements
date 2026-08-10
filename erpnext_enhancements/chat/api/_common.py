# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Shared gates and shapes for the chat HTTP surface. Every endpoint starts here.

Three things are centralised, each because the alternative is one endpoint forgetting:

* **the gate** — :func:`require_session` and :func:`require_room`. A membership decision
  taken once, at the top of the request, using the same helper the permission hooks use.
* **the shapes** — :func:`message_payload` and friends. One serialiser per DocType means a
  column added to a read path is added everywhere, and a body accidentally exposed on a
  deleted row is impossible in one place rather than unlikely in six.
* **the tombstone rule** — ``is_deleted = 1`` rows keep their ``text`` (ADR: Google's
  tombstone is content-free, so ERPNext is the only copy). Every serialiser here strips it.
  A read path that hand-builds a dict is a read path that will one day ship a deleted body.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any, Final

import frappe
from frappe.utils import cint, get_datetime_str

from erpnext_enhancements.chat import permissions

#: DocType names, written once — a typo in a string literal is a runtime error on a read path.
ROOM_DOCTYPE: Final[str] = "Chat Room"
MESSAGE_DOCTYPE: Final[str] = "Chat Message"
MEMBER_DOCTYPE: Final[str] = "Chat Room Member"
ATTACHMENT_DOCTYPE: Final[str] = "Chat Attachment"

#: What the transcript asks for in one page, and the ceiling a caller may request.
#: 50 is the brief's initial page; the ceiling exists because ``limit`` arrives from a
#: browser and ``limit=100000`` is a denial of service written as a query parameter.
DEFAULT_PAGE_SIZE: Final[int] = 50
MAX_PAGE_SIZE: Final[int] = 100

#: The text shown in place of a deleted message. Rendered client-side from ``is_deleted``;
#: this constant exists so search snippets and room previews agree with the transcript.
TOMBSTONE_TEXT: Final[str] = "This message was deleted"

#: Above this many members a room collapses per-message read receipts to "Read by N" with a
#: popover, rather than rendering an avatar per reader (§4.7). Computing and painting 30
#: avatars per message is neither useful nor cheap; it is a constant rather than a magic
#: number so the threshold can be argued about in one place.
RECEIPT_AVATAR_MEMBER_LIMIT: Final[int] = 10


class ChatAccessError(frappe.PermissionError):
	"""Refused for a chat-specific reason. A ``PermissionError`` so Frappe answers 403.

	Subclassed rather than raising ``frappe.PermissionError`` directly so that the SPA can
	tell "you are not in this room" apart from a generic framework refusal, and so the
	message stays uniform: it never names whether the room exists. A 403 that distinguishes
	"no such room" from "not your room" is a room-existence oracle.
	"""


def _deny(message: str) -> None:
	"""Refuse uniformly. Never leaks whether the room exists."""
	raise ChatAccessError(frappe._(message))


def require_session() -> str:
	"""Resolve the caller, or refuse. The first line of every whitelisted method here.

	Refuses Guest explicitly rather than relying on ``@frappe.whitelist()``'s default:
	``allow_guest`` is one keyword away from being added by somebody wiring a public
	surface, and a chat endpoint that answers Guest is one somebody later hangs data off.

	Also enforces the **pilot gate** (``Chat Settings.restrict_to_whitelist``). A gate
	implemented only in the client is not a gate; the SPA's own shell check
	(``www/chat.py``) is a courtesy that keeps people off a page they cannot use, and this
	is the control.
	"""
	user = (frappe.session.user or "").strip()
	if not user or user == "Guest":
		_deny("Chat is not available to signed-out users.")

	try:
		from erpnext_enhancements.chat.doctype.chat_settings.chat_settings import is_user_allowed

		if not cint(frappe.db.get_single_value("Chat Settings", "enabled")):
			_deny("Chat is not enabled on this site.")
		if not is_user_allowed(user):
			_deny("Chat is not open to your account yet.")
	except ChatAccessError:
		raise
	except Exception:
		# A site with this code but no migration has no Chat Settings row. Refuse rather
		# than fail open: an unanswerable "is this feature on?" is a "no".
		_deny("Chat is not enabled on this site.")

	return user


def require_room(room: str, *, user: str | None = None) -> tuple[str, str]:
	"""Gate a room-scoped request. Returns ``(user, room)`` or raises.

	Membership is asserted through :func:`chat.permissions.chat_room_has_permission` — the
	same function the socket server calls on ``doc_subscribe`` — rather than through a
	second ``frappe.db.exists`` written here. One rule, one implementation: if the socket
	boundary and the REST boundary can disagree, one of them is wrong and nobody will know
	which.
	"""
	user = user or require_session()
	name = (room or "").strip()
	if not name:
		_deny("That conversation is no longer available.")

	if not permissions.chat_room_has_permission(name, "read", user):
		_deny("That conversation is no longer available.")

	return user, name


def require_message(message: str, *, user: str | None = None) -> tuple[str, dict[str, Any]]:
	"""Gate a message-scoped request. Returns ``(user, row)`` with ``room``/``seq``/``sender``.

	Reads the row with ``frappe.db.get_value`` (which bypasses permissions) and then applies
	the rule explicitly, because the alternative — ``frappe.get_doc`` — would run
	``has_permission`` on a DocType with **zero DocPerm**, where the stack refuses before any
	hook is consulted and every caller would get a 403 including legitimate ones.
	"""
	user = user or require_session()
	name = (message or "").strip()
	if not name:
		_deny("That message is no longer available.")

	row = frappe.db.get_value(
		MESSAGE_DOCTYPE,
		name,
		["name", "room", "seq", "sender", "sender_kind", "thread_root", "is_deleted"],
		as_dict=True,
	)
	if not row:
		_deny("That message is no longer available.")
	if not permissions.chat_room_has_permission(row["room"], "read", user):
		_deny("That message is no longer available.")

	return user, dict(row)


def page_size(limit: Any, default: int = DEFAULT_PAGE_SIZE) -> int:
	"""Clamp a client-supplied page size into ``[1, MAX_PAGE_SIZE]``.

	``cint`` rather than ``int``: ``limit`` arrives as a string from a query string, and
	``int("")`` raises where ``cint("")`` is 0 — which then falls through to the default.
	"""
	value = cint(limit)
	if value < 1:
		return default
	return min(value, MAX_PAGE_SIZE)


def _dt(value: Any) -> str | None:
	"""Datetime → ISO string, or ``None``. JSON has no datetime and ``str(datetime)`` drifts."""
	if not value:
		return None
	try:
		return get_datetime_str(value)
	except Exception:
		return str(value)


def message_payload(row: Any, *, include_body: bool = True) -> dict[str, Any]:
	"""One ``Chat Message`` as the SPA sees it. **The only place a body is emitted.**

	``is_deleted`` rows are returned — never dropped — because a message that vanishes reads
	as a bug and decision #12 keeps an audit trail. But the body is replaced here, at the
	serialiser, rather than at each of the four call sites: history, thread, search and the
	realtime backfill all pass through this function, so "a deleted row cannot leak its
	text" is structural rather than a rule four readers have to remember.

	``sender`` may be ``NULL``. An external Chat participant is stored with ``sender_email``
	and no ``User`` link, because ERPNext is the record of what was said, not of who has an
	account. The client renders ``sender_email`` in that case and must not assume a ``User``.
	"""
	get = row.get if isinstance(row, dict) else (lambda k, d=None: getattr(row, k, d))
	deleted = bool(cint(get("is_deleted", 0)))

	payload: dict[str, Any] = {
		"name": get("name"),
		"room": get("room"),
		"seq": cint(get("seq", 0)),
		"sender": get("sender") or None,
		"sender_email": get("sender_email") or None,
		"sender_kind": get("sender_kind") or "Human",
		"message_type": get("message_type") or "Text",
		"parent_message": get("parent_message") or None,
		"thread_root": get("thread_root") or None,
		"has_attachments": bool(cint(get("has_attachments", 0))),
		"is_edited": bool(cint(get("is_edited", 0))),
		"edited_at": _dt(get("edited_at")),
		"is_deleted": deleted,
		"deleted_at": _dt(get("deleted_at")),
		"creation": _dt(get("creation")),
		"client_message_id": get("client_message_id") or None,
		"late_arrival": bool(cint(get("late_arrival", 0))),
		"sync_state": get("sync_state") or None,
	}

	if deleted or not include_body:
		payload["text"] = ""
		payload["deleted"] = True
	else:
		payload["text"] = get("text") or ""

	return payload


def room_payload(row: Any, *, member: Any = None) -> dict[str, Any]:
	"""One ``Chat Room`` as the room list sees it, rendered from the denormalised columns.

	``last_message_at`` / ``last_message_preview`` / ``last_message_sender`` are written by
	``outbox._denormalise_room`` on every insert precisely so this list needs **zero joins**
	— the single biggest render win in the app. Do not "improve" this by joining
	``Chat Message`` for a fresher preview; the denormalisation is the design.
	"""
	get = row.get if isinstance(row, dict) else (lambda k, d=None: getattr(row, k, d))
	last_seq = cint(get("seq_high_water", 0))
	read_seq = cint((member or {}).get("last_read_seq", 0)) if member else 0

	return {
		"name": get("name"),
		"room_type": get("room_type") or "Group",
		"title": get("title") or "",
		"description": get("description") or "",
		"is_archived": bool(cint(get("is_archived", 0))),
		"linked_doctype": get("linked_doctype") or None,
		"linked_document": get("linked_document") or None,
		"dm_user_1": get("dm_user_1") or None,
		"dm_user_2": get("dm_user_2") or None,
		"last_message": get("last_message") or None,
		"last_message_at": _dt(get("last_message_at")),
		"last_message_sender": get("last_message_sender") or None,
		"last_message_preview": get("last_message_preview") or "",
		"seq_high_water": last_seq,
		"last_read_seq": read_seq,
		# Unread is derived here, from two integers already in hand, rather than counted.
		# A COUNT(*) per room is the query that makes a room list slow at exactly the moment
		# somebody has a lot to read.
		"unread": max(0, last_seq - read_seq),
		"role": (member or {}).get("role") or "Member",
		"notification_mode": (member or {}).get("notification_mode") or "All",
		"muted_until": _dt((member or {}).get("muted_until")),
	}


def attachment_payload(row: Any) -> dict[str, Any]:
	"""One ``Chat Attachment``. ``file_url`` is resolved from the ``File`` link by the caller.

	No bytes and no signed URL: the client fetches ``/private/files/...`` and Frappe's own
	private-file check delegates to ``Chat Message``'s permission, which is the whole reason
	ADR §F.8 requires ``is_private = 1`` on every chat attachment.
	"""
	get = row.get if isinstance(row, dict) else (lambda k, d=None: getattr(row, k, d))
	return {
		"name": get("name"),
		"message": get("message"),
		"file": get("file") or None,
		"file_url": get("file_url") or None,
		"file_name": get("file_name") or "",
		"content_type": get("content_type") or "",
		"file_size": cint(get("file_size", 0)),
		"ingest_state": get("ingest_state") or "Pending",
	}
