# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Sidecar carrying the Google-side identity of a chat attachment.

The bytes live in a core Frappe ``File`` row; only the Google identity lives here. A sidecar
rather than Custom Fields on ``File`` because ``File`` is a core table every app on the bench
writes to, and hanging our own identity off somebody else's doctype is how two apps end up
fighting over one column.

**The convention Phase 3 must honour, recorded here because it is the whole attachment
security model.** Every stored chat attachment is written with ``is_private = 1`` and
``attached_to_doctype = "Chat Message"`` / ``attached_to_name = <message>``. Frappe's
private-file check consults those two fields and therefore delegates to our ``has_permission``,
so row-level chat security covers the file by construction and the ``File`` row garbage-collects
with the message. A **public** file is served straight off disk by the web server with **no auth
at all** — nothing in Frappe, and nothing in this app, gets a chance to say no. There is no
variant of a public chat attachment that is safe.

Two corollaries that follow from the same decision, both of which a later phase would otherwise
have to rediscover:

* Because ``Chat Message`` ships zero DocPerm (ADR §F.18), Frappe's own ``/private/files/<f>``
  route fails for every user except Administrator — including the room members who are entitled
  to the file. Downloads therefore go through a whitelisted endpoint that establishes membership
  itself and writes the audit row. The SPA addresses an attachment by its ``Chat Attachment``
  name, never by a ``File`` name and never by a ``/private/files/`` URL: there is no reason to
  publish the key to a door and then rely on the lock.

  **Phase 2 built it:** :func:`erpnext_enhancements.chat.sync.attachments.download`. It is the
  only path to a chat attachment's bytes, it refuses ``Guest`` before it asks anything else, it
  reaches its verdict through ``chat.permissions.chat_attachment_has_permission`` rather than a
  second copy of the membership rule, and it answers the same 403 whether the row is missing,
  unreadable or empty — distinguishing those would answer *"does room R contain a file called
  X"* for somebody who is not in room R. Verified against the v16 tree rather than assumed:
  ``/private/files/…`` → ``frappe/app.py`` → ``download_private_file`` → ``find_file_by_url`` →
  ``File.is_downloadable`` → ``File.has_permission`` → ``ref_doc.has_permission("read")``.
* ``source`` is a permission decision wearing a data-model costume. A ``DRIVE_FILE`` attachment
  is *linked*, never copied — Chat-hosted blobs are gated by space membership, Drive files by
  Drive permissions independent of the space, and copying the bytes would re-home somebody
  else's ACL decision inside ours. ``media.download`` cannot fetch Drive files in any case.

The behaviour — planning, ingest, upload, the sweeper and the byte path — lives in
``chat/sync/attachments.py``. This module is the **schema and its invariants**, and it stays
free of the Google transport on purpose: ``tests/test_chat_guardrails.py`` forbids a DocType
controller importing ``chat.gchat`` at all, because a controller method *is* a ``doc_event``
and a ``doc_event`` that calls Google spends the space's single write per second inside a
user's save.
"""

from typing import Final

import frappe
from frappe.model.document import Document

#: ``source`` values. The first two mirror Google's ``Attachment.source`` enum; ``ERPNext``
#: marks an attachment uploaded on our side and relayed outward.
SOURCE_UPLOADED: Final[str] = "Uploaded"
SOURCE_DRIVE_LINK: Final[str] = "Drive Link"
SOURCE_ERPNEXT: Final[str] = "ERPNext"

#: Google's ``Attachment.source`` enum members, spelled once. ``SOURCE_UNSPECIFIED`` is
#: deliberately absent: the reference marks it *"Reserved"*, and the ingest treats an
#: unrecognised source as a recorded ``Skipped`` rather than mapping it to either of the two
#: real ones — the two have opposite ACL consequences, so guessing is a coin flip on somebody
#: else's permission model.
GOOGLE_SOURCE_UPLOADED_CONTENT: Final[str] = "UPLOADED_CONTENT"
GOOGLE_SOURCE_DRIVE_FILE: Final[str] = "DRIVE_FILE"

#: Google's ``Attachment.source`` enum → our ``source`` value. Phase 2's ingest reads this
#: rather than string-matching at the call site, so the mapping exists in exactly one place.
GOOGLE_SOURCE_MAP: Final[dict[str, str]] = {
	GOOGLE_SOURCE_UPLOADED_CONTENT: SOURCE_UPLOADED,
	GOOGLE_SOURCE_DRIVE_FILE: SOURCE_DRIVE_LINK,
}

#: ``ingest_state`` values.
INGEST_PENDING: Final[str] = "Pending"
INGEST_STORED: Final[str] = "Stored"
INGEST_LINKED: Final[str] = "Linked"
INGEST_SKIPPED: Final[str] = "Skipped"
INGEST_FAILED: Final[str] = "Failed"


#: ``ingest_state`` values a row may hold while its bytes are still expected to arrive. The
#: sweeper re-drives exactly these; everything else is settled.
INGEST_UNSETTLED: Final[frozenset[str]] = frozenset({INGEST_PENDING, INGEST_FAILED})

#: SHA-256, lowercase hex, 64 characters. Ours, not Frappe's — ``File.content_hash`` is MD5
#: and is what ``File.save_file`` deduplicates on, so it cannot answer "are these the bytes we
#: downloaded?" once a chosen-prefix collision is on the table.
_SHA256_HEX_LENGTH: Final[int] = 64


class ChatAttachment(Document):
	def validate(self) -> None:
		"""The invariants that hold the attachment ACL story together.

		Every field read is ``getattr(obj, "field", None) or ""`` per the house rule: this
		runs during ``bench migrate`` and during ERPNext's own test bootstrap, before this
		app's fields necessarily exist, and a controller that assumes a column turns a fresh
		install into a crash.

		1. **A ``Drive Link`` row must never carry a local ``File``.** The moment the bytes are
		   on our filesystem they are governed by our permission model instead of Drive's,
		   which is precisely the re-homing this table exists to prevent. The positive half —
		   that a ``Drive Link`` eventually has a ``drive_file_id`` — is deliberately *not*
		   asserted, because a row is created before ingest resolves and would fail on insert.
		2. **A linked ``File`` must be private.** This is the one that would be a real leak. A
		   public file is served straight off disk by the web server with no auth at all:
		   nothing in Frappe, and nothing in this app, gets a chance to say no. ADR §F.8's
		   ``is_private = 1`` is only a rule if something refuses the alternative, and this is
		   that something. Unreadable ``File`` rows are tolerated rather than assumed private —
		   the check can only ever *refuse*, never grant.
		3. **``room`` agrees with the message's room.** Denormalised for the byte path's
		   benefit, so a disagreement means one of the two is lying about which room's ACL
		   applies. Filled in silently when blank; refused when it contradicts the message.
		4. **``content_hash``, when set, is 64 lowercase hex characters.** A truncated or
		   upper-cased digest compares unequal to a correctly derived one, which would silently
		   turn re-download verification and cross-room dedupe into permanent misses.
		"""
		source = getattr(self, "source", None) or ""
		file_name = getattr(self, "file", None) or ""

		if source == SOURCE_DRIVE_LINK and file_name:
			frappe.throw(
				frappe._("A Drive Link attachment must not carry a local File — link it, never copy it."),
				title=frappe._("Chat Attachment"),
			)

		if file_name:
			try:
				is_private = frappe.db.get_value("File", file_name, "is_private")
			except Exception:
				is_private = None
			if is_private is not None and not int(is_private or 0):
				frappe.throw(
					frappe._(
						"Chat attachment {0} points at a PUBLIC File. A public file is served off "
						"disk by the web server with no permission check at all, so there is no "
						"safe variant of a public chat attachment — store it with is_private = 1."
					).format(file_name),
					title=frappe._("Chat Attachment"),
				)

		message = getattr(self, "message", None) or ""
		if message:
			try:
				message_room = frappe.db.get_value("Chat Message", message, "room") or ""
			except Exception:
				message_room = ""
			if message_room:
				current_room = getattr(self, "room", None) or ""
				if not current_room:
					self.room = message_room
				elif current_room != message_room:
					frappe.throw(
						frappe._(
							"Chat attachment room {0} disagrees with message {1}, which is in room "
							"{2}. The denormalised room is what the byte path scopes on; two "
							"answers means one of them is the wrong ACL."
						).format(current_room, message, message_room),
						title=frappe._("Chat Attachment"),
					)

		digest = getattr(self, "content_hash", None) or ""
		if digest and (len(digest) != _SHA256_HEX_LENGTH or digest != digest.lower()):
			frappe.throw(
				frappe._(
					"content_hash must be 64 lowercase hex characters (SHA-256); got {0} "
					"characters. A malformed digest compares unequal to a correct one, which "
					"turns re-download verification into a permanent silent miss."
				).format(len(digest)),
				title=frappe._("Chat Attachment"),
			)
