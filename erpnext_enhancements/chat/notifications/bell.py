# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""``Notification Log`` rows — the bell surface, and the four traps in getting there.

One row per **(person, room, unread cycle)**, never one per message. That is not an
optimisation, it is a schema constraint discovered by arithmetic. The table holds 9,714 rows
accumulated over thirteen months (measured on production, 2026-08-11) and is registered for
retention in neither Frappe's hooks nor ERPNext's, so it grows forever. A row per message
fanned out to a ten-person room would add ~12,000 rows a day — roughly two hundred times the
site's current rate, and about 2 GB a year on a table nothing trims.

--------------------------------------------------------------------------------------
Frappe's ``dedupe_on`` cannot be used for this, and the reason is a single clause
--------------------------------------------------------------------------------------

``enqueue_create_notification(users, doc, dedupe_on=[...])`` looks exactly right, and the
ADR specifies it. Its own docstring, read from the deployed v16 build, says what it actually
does:

	*dedupe_on: optional list of field names; for each recipient, skip creation if a
	Notification Log already exists matching those field values (prevents duplicate rows —
	**it does not re-surface an existing notification**)*

and ``_notification_exists`` is a ``frappe.db.exists`` on ``for_user`` plus those fields,
**with no ``read`` filter**. So under ``dedupe_on=["document_type", "document_name"]`` the
first message in a room writes a bell row, the person reads the room, the row is marked
read — and every subsequent message for the rest of that row's life matches the existence
check and writes nothing. The bell would go quiet for that room permanently, with no error
anywhere and nothing in a test to catch it, which is the exact class of silent loss this
whole phase exists to prevent.

**So the dedupe is scoped to the unread row and done here.** Look for an *unread* row for
this (person, room); if one exists, the bell already says what it needs to say and a second
row would be a duplicate; if there is none — because there never was one, or because the
last one has been read — write one. Volume stays bounded, because opening a new cycle
requires a human to have read the previous one.

The residual, stated rather than hidden: two messages landing in the same room within the
same instant can both find no unread row and both write one. The bound is two rows rather
than N, both clear on the next read, and closing it would mean a lock on the notification
path. That trade is deliberate.

--------------------------------------------------------------------------------------
Email is impossible here, not merely switched off
--------------------------------------------------------------------------------------

``NotificationLog.after_insert`` calls ``send_notification_email`` whenever
``is_email_notifications_enabled_for_type(for_user, type)`` is true. Decision #3 says
neither of the two chat notifications is email, so the suppression is
``notification_skip_email_types`` in ``hooks.py`` — a first-class v16 extension point,
merged across apps, consulted as the **first** gate inside that predicate, **before** it
reads the user's own settings. No per-user opt-in can defeat it, and the bench test asserts
zero ``Email Queue`` rows for a user who has explicitly opted the type *in*.

--------------------------------------------------------------------------------------
Two more traps, both silent, both confirmed against the live bench
--------------------------------------------------------------------------------------

**The type is a Link.** ``Notification Log.type`` links to a real ``Notification Type``
record (confirmed: ``fieldtype = Link``, ``options = Notification Type``, and production
holds exactly five — Alert, Assignment, Energy Point, Mention, Share). Writing
``type = "Chat Mention"`` without installing the record is a link-validation failure on
insert. :func:`install_notification_types` ships them, from ``after_migrate`` rather than as
a fixture, because a fixture's removal is a two-step dance this does not need.

**Recipients are matched on the email, and the function returns the name.**
``_get_user_ids`` filters ``{"enabled": 1, "email": ("in", user_emails)}``. Hand it
``Chat Room Member.user`` — a ``User`` docname — on a site where a login id differs from the
email field and it matches nothing, returns no recipients, and the notification vanishes
with no error at any layer. So every recipient here is resolved to ``User.email`` explicitly.

--------------------------------------------------------------------------------------
No message text, ever, in a notification row
--------------------------------------------------------------------------------------

``Chat Message`` ships **zero DocPerm** so that message bodies are unreachable through
``/api/resource``, the desk, global search and the generic MCP tools. A ``Notification Log``
row is an ordinary document with ordinary permissions. Putting a snippet in the subject
would move employee-private conversation out of the one table built to contain it and into
one that is not — a leak whose blast radius is every reader of that table, and it would look
like a nice touch in review. Subjects here name a sender and a room and nothing else, which
is also why they never go stale: there is no count in them to be wrong.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

from typing import Final

import frappe

from erpnext_enhancements.chat import links

#: The two ``Notification Type`` names this app owns. They became public API the moment they
#: shipped, because ``notification_skip_email_types`` is keyed by name — so a rename is a
#: breaking change that silently re-enables email, and a third name is a decision rather
#: than a convenience.
TYPE_MESSAGE: Final[str] = "Chat Message"
TYPE_MENTION: Final[str] = "Chat Mention"

CHAT_NOTIFICATION_TYPES: Final[tuple[str, ...]] = (TYPE_MESSAGE, TYPE_MENTION)

#: The DocTypes the two row shapes point at. A room row is per-room and per-unread-cycle; a
#: mention row is genuinely per-message, and is low enough volume to afford it.
_ROOM_DOCTYPE: Final[str] = "Chat Room"
_MESSAGE_DOCTYPE: Final[str] = "Chat Message"

_LOG_DOCTYPE: Final[str] = "Notification Log"


def install_notification_types() -> list[str]:
	"""Create the two ``Notification Type`` records if they are absent. Idempotent.

	Registered on **both** ``after_migrate`` and ``after_install``: ``after_migrate`` does not
	run during ``bench install-app``, and ``install-app`` marks every line of ``patches.txt``
	completed without executing any of it — so a patch alone would be recorded as done on a
	fresh site and never actually run.

	``enabled`` is set explicitly rather than left to the DocField default. That is the same
	trap Frappe ``Notification`` fixtures have, where an omitted ``enabled`` imports as
	disabled and re-disables on every migrate; here the consequence would be quieter and
	worse, because a disabled type still satisfies the link validation and simply stops
	delivering.

	Returns the names it created, so the caller can log something true rather than "ok".
	"""
	created: list[str] = []
	if not frappe.db.table_exists("Notification Type"):
		# A site mid-migration, before the framework's own DocType has been synced. The
		# after_migrate backstop runs again on the next deploy, so returning empty is right.
		return created

	for name in CHAT_NOTIFICATION_TYPES:
		if frappe.db.exists("Notification Type", name):
			continue
		doc = frappe.new_doc("Notification Type")
		doc.type_name = name
		doc.enabled = 1
		doc.insert(ignore_permissions=True)
		created.append(name)

	return created


def notify_room(
	recipient: str,
	*,
	room: str,
	room_label: str,
	sender_label: str,
	message: str | None = None,
) -> bool:
	"""One bell row saying "this conversation wants you". Returns whether a row was written.

	``message`` is used only to build a deep link that lands on the newest message rather than
	at the top of the room; it never reaches the subject or the row's ``document_name``, which
	stay room-scoped so that the row survives being deduped without going stale.
	"""
	return _write(
		recipient,
		notification_type=TYPE_MESSAGE,
		document_type=_ROOM_DOCTYPE,
		document_name=room,
		subject=frappe._("New messages in {0}").format(room_label or room),
		link=links.build_message_deep_link(room, message=message),
		from_user=sender_label,
		dedupe_unread=True,
	)


def notify_mention(
	recipient: str,
	*,
	room: str,
	room_label: str,
	message: str,
	sender_label: str,
) -> bool:
	"""One bell row for a direct mention. Per message, and deliberately so.

	A mention is the one signal people explicitly opt into and the one whose loss they
	escalate, so it gets its own type, its own row, and a link to the **message** rather than
	the room. It is also about 5% of traffic addressed to one person, so the per-message cost
	the room rows exist to avoid does not apply — roughly thirty rows a day at this site's
	measured volume, against the twelve thousand a naive per-message design would produce.

	``dedupe_unread`` is off: two mentions of the same person in the same room are two
	separate things to be told about, and collapsing them is how somebody misses the second
	one. The row's ``document_name`` is the message, so nothing collides anyway.
	"""
	return _write(
		recipient,
		notification_type=TYPE_MENTION,
		document_type=_MESSAGE_DOCTYPE,
		document_name=message,
		subject=frappe._("{0} mentioned you in {1}").format(sender_label, room_label or room),
		link=links.build_message_deep_link(room, message=message),
		from_user=sender_label,
		dedupe_unread=False,
	)


def has_unread_row(recipient_email: str, document_type: str, document_name: str) -> bool:
	"""Whether an **unread** bell row already covers this (person, target).

	``frappe.db.exists`` with a filter dict rather than a raw query: this table is not a chat
	table, the row set is tiny and indexed on ``for_user``, and a raw statement here would owe
	the raw-SQL guard a written justification for a lookup that does not need one.
	"""
	if not recipient_email or not document_name:
		return False
	try:
		return bool(
			frappe.db.exists(
				_LOG_DOCTYPE,
				{
					"for_user": recipient_email,
					"document_type": document_type,
					"document_name": document_name,
					"read": 0,
				},
			)
		)
	except Exception:
		# An unreadable table must not silence the bell. Failing toward "no row exists" writes
		# a possibly-duplicate row, which is visible and self-correcting; failing the other way
		# would drop the notification, which is not.
		return False


def resolve_email(user: str) -> str:
	"""``User.email`` for a ``User`` docname. **Not the docname.**

	The whole trap in one function. ``_get_user_ids`` filters on the ``email`` field, so a
	docname that differs from it — which is legal, and which this site's own login ids are one
	admin decision away from — matches nothing and the notification disappears silently.

	Falls back to the docname, because on this site they are in fact the same string today and
	a missing ``User`` row should degrade to "probably right" rather than to "no recipients".
	"""
	who = (user or "").strip()
	if not who:
		return ""
	try:
		return (frappe.db.get_value("User", who, "email") or who).strip()
	except Exception:
		return who


def _write(
	recipient: str,
	*,
	notification_type: str,
	document_type: str,
	document_name: str,
	subject: str,
	link: str,
	from_user: str,
	dedupe_unread: bool,
) -> bool:
	"""Build and enqueue one row. **Never raises** — it runs on the message insert path.

	``dedupe_on`` is deliberately **not** passed to Frappe; see the module docstring. The
	existence check is ours and is scoped to unread rows, which is the difference between
	"one row per room" and "one row per room, ever".
	"""
	try:
		email = resolve_email(recipient)
		if not email:
			return False

		if dedupe_unread and has_unread_row(email, document_type, document_name):
			return False

		from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

		enqueue_create_notification(
			[email],
			{
				"type": notification_type,
				"document_type": document_type,
				"document_name": document_name,
				"subject": subject,
				"from_user": from_user or None,
				# `link` wins. v16's desk reads `notification_doc.link` first and short-circuits
				# before `document_type`/`document_name` are consulted, and it is set on 0 of the
				# 9,714 rows on production, so writing it collides with nothing. Both are set
				# anyway: the pair keeps the row queryable, auditable and repointable by a
				# document merge, and it is what the row means rather than where it goes.
				#
				# Setting ONLY the pair would route a click at `/app/chat-message/<name>` — a
				# DocType with zero DocPerm — so every notification in the feature would land on
				# a 403.
				"link": link,
			},
		)
		return True
	except Exception:
		frappe.log_error(
			title="chat bell notification failed",
			message=f"recipient={recipient} type={notification_type} target={document_name}",
		)
		return False


def mark_rows_read(recipient_email: str, document_type: str, document_names: list[str]) -> list[str]:
	"""Clear the bell rows a read covers. Returns the names cleared, for the realtime payload.

	Part two of the four-part cross-surface sync
	(:mod:`~erpnext_enhancements.chat.notifications.read_state` owns the other three). The
	names are returned rather than merely counted because the client uses them to clear the
	right rows out of an already-rendered bell list without refetching it.
	"""
	if not recipient_email or not document_names:
		return []
	try:
		rows = frappe.get_all(
			_LOG_DOCTYPE,
			filters={
				"for_user": recipient_email,
				"document_type": document_type,
				"document_name": ("in", document_names),
				"read": 0,
			},
			pluck="name",
		)
		for name in rows:
			# `set_value` rather than a loaded document: this is one column on a log row, and
			# `save()` would run the whole validation stack — plus `after_insert`'s email branch
			# is the reason this table is touched carefully at all.
			frappe.db.set_value(_LOG_DOCTYPE, name, "read", 1, update_modified=False)
		return list(rows)
	except Exception:
		frappe.log_error(
			title="chat bell mark-read failed",
			message=f"recipient={recipient_email} type={document_type}",
		)
		return []


def unread_row_names(recipient_email: str) -> list[str]:
	"""Every unread chat bell row for one person. The debug and reconciliation read."""
	if not recipient_email:
		return []
	try:
		return frappe.get_all(
			_LOG_DOCTYPE,
			filters={
				"for_user": recipient_email,
				"type": ("in", list(CHAT_NOTIFICATION_TYPES)),
				"read": 0,
			},
			pluck="name",
		)
	except Exception:
		return []
