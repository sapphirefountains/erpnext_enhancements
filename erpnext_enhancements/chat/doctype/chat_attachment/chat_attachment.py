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
* ``source`` is a permission decision wearing a data-model costume. A ``DRIVE_FILE`` attachment
  is *linked*, never copied — Chat-hosted blobs are gated by space membership, Drive files by
  Drive permissions independent of the space, and copying the bytes would re-home somebody
  else's ACL decision inside ours. ``media.download`` cannot fetch Drive files in any case.

Schema only in Phase 1: no ingest job, no download endpoint, no Google call.
"""

from typing import Final

import frappe
from frappe.model.document import Document

#: ``source`` values. The first two mirror Google's ``Attachment.source`` enum; ``ERPNext``
#: marks an attachment uploaded on our side and relayed outward.
SOURCE_UPLOADED: Final[str] = "Uploaded"
SOURCE_DRIVE_LINK: Final[str] = "Drive Link"
SOURCE_ERPNEXT: Final[str] = "ERPNext"

#: Google's ``Attachment.source`` enum → our ``source`` value. Phase 2's ingest reads this
#: rather than string-matching at the call site, so the mapping exists in exactly one place.
GOOGLE_SOURCE_MAP: Final[dict[str, str]] = {
	"UPLOADED_CONTENT": SOURCE_UPLOADED,
	"DRIVE_FILE": SOURCE_DRIVE_LINK,
}

#: ``ingest_state`` values.
INGEST_PENDING: Final[str] = "Pending"
INGEST_STORED: Final[str] = "Stored"
INGEST_LINKED: Final[str] = "Linked"
INGEST_SKIPPED: Final[str] = "Skipped"
INGEST_FAILED: Final[str] = "Failed"


class ChatAttachment(Document):
	def validate(self) -> None:
		"""Refuse the one combination that would break the ACL story.

		A ``Drive Link`` row must never carry a local ``File``: the moment the bytes are in our
		filesystem they are governed by our permission model instead of Drive's, which is
		precisely the re-homing this table exists to prevent. The positive half — that a
		``Drive Link`` eventually has a ``drive_file_id`` — is deliberately *not* asserted here,
		because a row is created before ingest resolves and would fail validation on insert.
		"""
		if getattr(self, "source", None) == SOURCE_DRIVE_LINK and getattr(self, "file", None):
			frappe.throw(
				frappe._("A Drive Link attachment must not carry a local File — link it, never copy it."),
				title=frappe._("Chat Attachment"),
			)
