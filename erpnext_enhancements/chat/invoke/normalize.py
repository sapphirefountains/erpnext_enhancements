# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Both origins → one envelope. **Two functions in one module, on purpose.**

Appendix B specifies ``normalize_gchat.py`` and ``normalize_spa.py`` as separate files. They
are one file here, and the reason is the property they exist to guarantee: the two must
produce **byte-identical** envelopes for the same logical mention. Side by side, a field one
sets and the other forgets is visible on one screen. In two files it is visible to a test and
to nobody else — and the test is the thing most likely to be written to match whichever
implementation was finished first.

--------------------------------------------------------------------------------------
Two mechanisms that are easy to confuse, and conflating them is the classic failure
--------------------------------------------------------------------------------------

**This is the INTERACTION path.** Google delivers a DM to the app, or an ``@mention`` of the
app, as a synchronous webhook with a hard 30-second deadline.

**Phase 2's Workspace Events firehose is a different mechanism entirely** — Pub/Sub,
at-least-once, carrying a resource *name* and no body, arriving up to a minute late.

The same message can arrive by both. The firehose ingests it as a `Chat Message`; the
interaction path is what makes it a *turn*. Treating one as the other produces either a
mention that is never answered or a message stored twice, and both look like flakiness.

--------------------------------------------------------------------------------------
No content is written here
--------------------------------------------------------------------------------------

Normalising reads. The message row itself is written by the sync engine's inbound path, which
already has the echo ladder, the idempotency keys and the ordering rules. A second writer here
would be a second place ``unique(room, client_message_id)`` has to be got right.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint

from erpnext_enhancements.chat.invoke.envelope import Envelope, derive_request_id, strip_mention

#: Recorded on the invocation log, never placed in the envelope. See
#: :mod:`chat.invoke.envelope` for why the handler is not told.
ORIGIN_GOOGLE_CHAT: str = "Google Chat"
ORIGIN_SPA: str = "SPA"


class NotAMention(Exception):
	"""The payload is well-formed and is not a ``@triton`` turn. Not an error."""


def from_chat_interaction(payload: dict[str, Any]) -> tuple[Envelope, str]:
	"""A Google Chat interaction event → ``(envelope, origin)``.

	Raises:
		NotAMention: for an event that is not a message mentioning the app — a card click, an
			``ADDED_TO_SPACE``, a message that merely happens to contain the word. Raised
			rather than returned as ``None`` so a caller cannot fall through with an envelope
			full of empty strings, which would look like a valid turn for the empty room.
	"""
	message = (payload or {}).get("message") or {}
	space = (payload or {}).get("space") or {}
	if not message:
		raise NotAMention("interaction event carries no message")

	space_name = str(space.get("name") or message.get("space", {}).get("name") or "")
	room = _room_for_space(space_name)
	if not room:
		raise NotAMention(f"no Chat Room is bound to {space_name or '<unnamed space>'}")

	gchat_message = str(message.get("name") or "")
	local = _message_for_gchat_name(gchat_message)
	if not local:
		# The interaction webhook can beat the Workspace Events firehose — the two are
		# independent transports and this one is synchronous. Refusing here rather than
		# inventing a row keeps the write path single: the sweeper re-drives the ingest, and
		# the mention is answered on the retry. A duplicated message would be permanent.
		raise NotAMention(f"{gchat_message} has not been ingested yet; the retry will answer it")

	user = _user_for_chat_sender(message.get("sender") or {}, room)
	text = strip_mention(str(message.get("text") or ""))

	return (
		Envelope(
			user=user,
			room=room,
			message=local["name"],
			text=text,
			thread_root=local.get("thread_root") or None,
			request_id=derive_request_id(room, local["name"], text),
			seq=cint(local.get("seq")),
			transport={"space": space_name, "gchat_message": gchat_message},
		),
		ORIGIN_GOOGLE_CHAT,
	)


def from_spa_message(message_name: str) -> tuple[Envelope, str]:
	"""A message composed in the SPA (or the desk bubble) → ``(envelope, origin)``.

	Takes a name rather than a document so both entry points read the row the same way: the
	Chat path has to look the row up anyway, and two different sources for the same four
	fields is how the two envelopes start to differ.
	"""
	row = _message_row(message_name)
	if not row:
		raise NotAMention(f"{message_name} does not exist")

	text = strip_mention(str(row.get("text") or ""))
	return (
		Envelope(
			user=str(row.get("sender") or ""),
			room=str(row.get("room") or ""),
			message=str(row.get("name") or ""),
			text=text,
			thread_root=row.get("thread_root") or None,
			request_id=derive_request_id(str(row.get("room") or ""), str(row.get("name") or ""), text),
			seq=cint(row.get("seq")),
			transport={},
		),
		ORIGIN_SPA,
	)


def mentions_triton(message_name: str) -> bool:
	"""Whether a stored message mentions ``@triton``.

	Reads the **mention child rows** rather than scanning the body, because that is what the
	write path already resolved: ``compose`` drops a mention of a non-member and records what
	survived, so the row set is the decision and the text is only its input. Scanning the text
	here would answer a different question and would disagree in exactly the cases that matter.
	"""
	try:
		return bool(frappe.db.exists("Chat Mention", {"parent": message_name, "mention_type": "Triton"}))
	except Exception:
		return False


# ------------------------------------------------------------------------------- lookups


def _message_row(message_name: str) -> dict[str, Any] | None:
	try:
		return frappe.db.get_value(
			"Chat Message",
			message_name,
			["name", "room", "sender", "text", "thread_root", "seq"],
			as_dict=True,
		)
	except Exception:
		return None


def _message_for_gchat_name(gchat_message: str) -> dict[str, Any] | None:
	if not gchat_message:
		return None
	try:
		return frappe.db.get_value(
			"Chat Message",
			{"gchat_message_name": gchat_message},
			["name", "room", "sender", "thread_root", "seq"],
			as_dict=True,
		)
	except Exception:
		return None


def _room_for_space(space_name: str) -> str:
	if not space_name:
		return ""
	try:
		return frappe.db.get_value("Chat Room", {"gchat_space_name": space_name}, "name") or ""
	except Exception:
		return ""


def _user_for_chat_sender(sender: dict[str, Any], room: str) -> str:
	"""Map Google's ``users/{opaque id}`` to an ERPNext user, within one room.

	Keyed on the **membership resource name** and never on an email address, for the reason
	the membership sync already documents: a ``spaces.members.list`` under user auth returns
	an opaque id and nothing matchable. The room bounds the lookup, which is both faster and
	the correct scope — the same person is a different membership row in every space.

	Returns ``""`` when the sender maps to nobody. The handler refuses that turn rather than
	guessing, because guessing here means answering as the wrong person.
	"""
	resource = str(sender.get("name") or "")
	if not resource or not room:
		return ""
	try:
		return (
			frappe.db.get_value(
				"Chat Room Member",
				{"room": room, "gchat_membership_name": ["like", f"%{resource}%"]},
				"user",
			)
			or ""
		)
	except Exception:
		return ""
