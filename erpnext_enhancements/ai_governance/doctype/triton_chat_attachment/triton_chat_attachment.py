# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Anchor row for one file attached to a Triton chat turn.

**Why this table exists at all**, since the obvious answer is "it doesn't need to".

A Triton conversation lives in Triton's own database. There is no ERPNext row per turn, so
there is nothing for a ``File`` to attach to, and the tempting shortcut is to leave the
uploaded ``File`` orphaned -- ``attached_to_doctype`` blank -- exactly as
``public/js/feedback/transport.js`` does for the seconds before a Feedback Request exists.
Verified against the v16 tree, an orphan private File is *readable by its uploader*:
``/private/files/…`` routes through ``download_private_file`` -> ``find_file_by_url`` ->
``File.is_downloadable`` -> ``File.has_permission``, which short-circuits on
``doc.owner == user``. So it works.

It works and it is still wrong, for two reasons that only show up later:

* **Permission.** ``doc.owner == user`` is a per-uploader accident, not a policy. There is
  no hook on it, no audit, and no way to widen or narrow it the day somebody asks for one.
* **Garbage collection.** Core deletes attachments only through ``delete_doc``'s
  ``remove_all(parent)``. Nothing anywhere sweeps an unattached File. ``feedback.py`` gets
  away with its orphan window only because a real row appears seconds later; a Triton turn
  never produces one, so the orphan lives forever, stays in the uploader's File list, and
  consumes disk with no owner willing to claim it.

Pointing the ``File`` at a row of *this* doctype fixes both at once: the file inherits this
row's ``has_permission`` hook (``ai_governance.permissions``), and deleting this row deletes
the file with it. The daily sweep in ``ai_governance.tasks`` deletes these rows, and the
files follow.

**AI Governance, not Chat.** ``tests/test_doctype_modules.py`` only requires that the module
matches the directory, so either would pass -- and Chat is the wrong one anyway. The Chat
module is a membership-scoped world: zero DocPerm by doctrine (ADR 0009 §F.18), every read
routed through ``chat/permissions.py``'s room rules, ``validate_share`` refusing DocShare
outright. None of that governs a Triton attachment, which is owner-scoped and private to one
person. Filing it under Chat would make the membership doctrine *appear* to cover a doctype
it does not, which is worse than filing it anywhere else. It belongs beside
``Triton Assistant Settings`` and ``Triton Allowed User``.

**The DocPerm is load-bearing and is not decoration.** ``All`` holds ``read``/``delete`` with
``if_owner``. It has to: the whole point of anchoring the file here is that
``fac_extract_file_content`` (Frappe Assistant Core, in Triton's CORE tool pack on every
session) calls ``frappe.has_permission(attached_to_doctype, "read", attached_to_name)`` before
it reads a byte -- and on v16 a doctype with an empty ``permissions`` array refuses everyone
but Administrator *before any controller hook is consulted*. Ship this with zero DocPerm the
way the Chat doctypes do and the feature silently does nothing for every ordinary user, while
the same tool's other branch (``frappe.only_for("System Manager")`` for a private *unattached*
file) quietly explains why the orphan shortcut would not have worked either.

So the gate is doubled, deliberately: the ``if_owner`` DocPerm, and
``triton_chat_attachment_has_permission`` returning a hard boolean. On v16 a ``has_permission``
hook that returns ``None`` DENIES, so that function returns an explicit ``bool`` on every path,
exception paths included.
"""

from typing import Final

import frappe
from frappe.model.document import Document

#: ``source`` values.
SOURCE_UPLOAD: Final[str] = "ERPNext Upload"
SOURCE_DRIVE_LINK: Final[str] = "Drive Link"


class TritonChatAttachment(Document):
	def validate(self) -> None:
		"""The two invariants that hold the attachment ACL story together.

		Every field read is ``getattr(obj, "field", None) or ""`` per the house rule: this
		controller runs during ``bench migrate`` and during ERPNext's own test bootstrap,
		before this app's fields necessarily exist, and a controller that assumes a column
		turns a fresh install into a crash.

		1. **A Drive Link must never carry a local File.** The moment the bytes are on our
		   filesystem they are governed by our permission model instead of Drive's, which is
		   precisely the re-homing this feature refuses. Verified consequence rather than a
		   preference: the app's only Drive client is a service account with no domain-wide
		   delegation, so ERPNext *cannot* fetch a picked file anyway -- it 404s. A row that
		   somehow carried both would mean somebody added a fetch path.
		2. **A linked File must be private.** This is the one that would be a real leak. A
		   public file is served straight off disk by the web server with no permission check
		   at all -- nothing in Frappe, and nothing in this app, gets a chance to say no.
		   ``register_upload`` re-saves a public upload as private (which moves the bytes off
		   the public path; ``File.validate`` calls ``handle_is_private_changed`` on the
		   change) before it ever reaches here, so this is the assertion that the repair
		   happened, not the repair itself. Unreadable File rows are tolerated rather than
		   assumed private: this check can only ever *refuse*, never grant.
		"""
		source = getattr(self, "source", None) or ""
		file_name = getattr(self, "file", None) or ""

		if source == SOURCE_DRIVE_LINK and file_name:
			frappe.throw(
				frappe._(
					"A Drive Link attachment must not carry a local File — Triton reads it from "
					"Drive with the picking user's own credential. Copying the bytes here would "
					"re-home somebody else's sharing decision inside our permission model."
				),
				title=frappe._("Triton Chat Attachment"),
			)

		if file_name:
			try:
				is_private = frappe.db.get_value("File", file_name, "is_private")
			except Exception:
				is_private = None
			if is_private is not None and not int(is_private or 0):
				frappe.throw(
					frappe._(
						"Triton attachment {0} points at a PUBLIC File. A public file is served "
						"off disk by the web server with no permission check at all, so there is "
						"no safe variant of a public attachment — store it with is_private = 1."
					).format(file_name),
					title=frappe._("Triton Chat Attachment"),
				)
