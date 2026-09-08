# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Row-level scoping for the AI Governance doctypes that are private to one person.

Wired in ``hooks.py`` under both ``permission_query_conditions`` (what a list shows) and
``has_permission`` (what a direct read of one row answers). Parity between the two is the
house doctrine and it is not cosmetic: a query condition filters lists and says nothing at
all about ``frappe.get_doc(...)``, while a ``has_permission`` hook says nothing about what a
report or the desk sidebar will enumerate. Ship one without the other and the hole is in
whichever half you skipped.

**On v16 a ``has_permission`` hook that returns ``None`` DENIES.** Every path below returns
an explicit ``bool``, exception paths included -- a bare ``except: pass`` here would read as
a refusal, which is the safe direction but an invisible one, so the refusals are written out.

**These hooks do not protect raw SQL.** ``frappe.db.sql`` consults neither of them. Anything
in this module's package that queries ``tabTriton Chat Attachment`` directly must carry its
own owner predicate; ``triton_attachments.py`` uses ``frappe.get_all`` with an explicit
``owner`` filter for exactly this reason rather than relying on the hook to arrive.
"""

import frappe

TRITON_ATTACHMENT_DOCTYPE = "Triton Chat Attachment"


def _is_unscoped(user: str | None) -> bool:
	"""Administrator sees everything; nobody else gets a bypass here.

	Deliberately narrower than the training module's equivalent, which also exempts a
	manager role. There is no "Triton attachment manager": these rows are one person's
	private chat context, and a role that could read them would be a role that could read
	what everybody asked the assistant about.
	"""
	return (user or frappe.session.user) == "Administrator"


def triton_chat_attachment_query(user: str | None = None) -> str:
	"""List/report scoping: your own rows only."""
	user = user or frappe.session.user
	if _is_unscoped(user):
		return ""
	return f"`tabTriton Chat Attachment`.`owner` = {frappe.db.escape(user)}"


def triton_chat_attachment_has_permission(doc, ptype=None, user=None) -> bool:
	"""Single-document gate: you may read and delete your own attachment, and nothing else.

	This is also the gate that decides whether the model can read the file *at all*.
	``fac_extract_file_content`` resolves a Frappe ``file_url``, then calls
	``frappe.has_permission(file.attached_to_doctype, "read", file.attached_to_name)`` --
	which lands here, under the ERPNext identity the user linked to Triton. So the answer to
	"can Triton read this attachment" is the same boolean as "can this person open it", by
	construction rather than by a second rule that has to be kept in step.

	``doc`` may arrive as a ``Document`` or as a dict (the byte path passes the
	``frappe.db.get_value(..., as_dict=True)`` row straight in), so read it with ``.get``.
	"""
	user = user or frappe.session.user
	if _is_unscoped(user):
		return True
	if not user or user == "Guest":
		return False
	try:
		owner = doc.get("owner") if hasattr(doc, "get") else getattr(doc, "owner", None)
	except Exception:
		# An unreadable doc is a refusal, and it has to be spelled `False`: `None` would
		# also deny on v16, but only by accident, and the next reader would not know that.
		return False
	return bool(owner) and owner == user
