# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The notification HTTP surface: subscribe, unsubscribe, and the read a click reports.

Four endpoints, all thin. Each begins with :func:`require_session`, which resolves the
caller, refuses Guest and enforces the pilot gate — the same first line every other chat
endpoint has, for the same reason.

--------------------------------------------------------------------------------------
Not one of these takes a user parameter, and that is the design
--------------------------------------------------------------------------------------

Every function here acts on ``frappe.session.user`` and has no way to name anybody else. A
subscribe endpoint that accepted a user would let one person register a device against a
colleague's account and receive their notifications; a read endpoint that accepted one would
let anybody mark a coworker's conversations read. Neither is a permission check somebody
might forget — there is no parameter to check.

--------------------------------------------------------------------------------------
``read_from_notification`` is called from a service worker, which changes what it may do
--------------------------------------------------------------------------------------

A worker has no access to the page's CSRF token, so this one call arrives with the session
cookie alone. It is written to be safe under exactly that: it advances a **monotonic** read
mark for the caller's own membership, takes no user parameter, and can therefore do nothing
a repeated or forged-origin call could turn into damage — the worst outcome is a
conversation the caller is already a member of being marked read slightly early, by them.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

from typing import Any

import frappe

from erpnext_enhancements.chat.api._common import require_room, require_session


@frappe.whitelist()
def push_config() -> dict[str, Any]:
	"""What the client needs before it can offer push at all.

	Returns ``enabled: False`` rather than raising when the site has no VAPID keypair, so the
	SPA can simply not show the affordance instead of rendering a control that fails when
	pressed.
	"""
	require_session()

	from erpnext_enhancements.chat.notifications.webpush import vapid

	try:
		configured = vapid.is_configured()
		key = vapid.public_key() if configured else ""
	except Exception:
		configured, key = False, ""

	return {
		"enabled": bool(configured and _flag("push_enabled")),
		"application_server_key": key,
		# The scope the worker must be registered at. Sent by the server rather than hardcoded
		# in two client files, because getRegistration() resolves against the current page and
		# a mismatch here is a client that silently talks to the wrong registration.
		"scope": "/chat/",
		"worker": "/chat-sw.js",
		"show_preview": _flag("push_show_preview"),
	}


@frappe.whitelist()
def subscribe(
	endpoint: str, p256dh: str, auth: str, user_agent: str | None = None, device_label: str | None = None
) -> dict[str, Any]:
	"""Register this browser for push. Idempotent on the endpoint.

	Called on every page load once permission has been granted, because browsers reissue the
	same endpoint and the registry uses that to refresh ``last_seen`` — which is what the
	sixty-day stale sweep measures against.
	"""
	user = require_session()

	from erpnext_enhancements.chat.notifications.webpush import subscriptions

	name = subscriptions.register(
		user,
		endpoint=endpoint,
		p256dh=p256dh,
		auth=auth,
		user_agent=user_agent or frappe.get_request_header("User-Agent"),
		device_label=device_label,
	)
	return {"subscription": name}


@frappe.whitelist()
def unsubscribe(endpoint: str) -> dict[str, Any]:
	"""Drop this browser's subscription. What the client calls when somebody turns push off."""
	require_session()

	from erpnext_enhancements.chat.notifications.webpush import subscriptions

	return {"removed": subscriptions.unregister(endpoint)}


@frappe.whitelist()
def read_from_notification(room: str, message: str | None = None) -> dict[str, Any]:
	"""A notification was clicked, so the room has been read. Runs the whole cross-surface sync.

	The other direction of "reading in one place clears everything everywhere": the SPA's own
	read already clears the bell and the banners, and this is the same four steps entered from
	the banner instead.

	Reads to the tail rather than to the clicked message. Somebody who taps a notification is
	opening the conversation, not one line of it, and leaving the rest unread would put them
	straight back into an unread badge for messages they are about to look at.
	"""
	user, name = require_room(room)

	from erpnext_enhancements.chat.notifications import read_state

	tail = frappe.db.get_value("Chat Room", name, "seq_high_water")
	return read_state.mark_room_read(name, user=user, up_to_seq=tail, source="notification")


@frappe.whitelist()
def explain(room: str, user: str | None = None) -> dict[str, Any]:
	"""Why would this person be notified about this room right now? The support tool.

	Every failure in a notification system is invisible — nobody reports the ping they did not
	get — so "why didn't Jane get that" has to be answerable without reading the code or
	guessing. This returns the presence records that were considered, which were rejected as
	stale, the resulting state and the decision with its reason code.

	Restricted to System Manager when asking about somebody else. Presence is confidential in
	the same way the room list is: it says who somebody is talking to by saying which rooms
	they show up in.
	"""
	caller = require_session()
	subject = (user or "").strip() or caller
	if subject != caller and "System Manager" not in frappe.get_roles(caller):
		frappe.throw(frappe._("Only a System Manager can ask about somebody else's notifications."))

	from erpnext_enhancements.chat.notifications import debug

	return debug.explain(subject, room)


def _flag(fieldname: str) -> bool:
	"""One Chat Settings check, defaulting to off. Never raises on a site without the Single."""
	from frappe.utils import cint

	try:
		return bool(cint(frappe.db.get_single_value("Chat Settings", fieldname)))
	except Exception:
		return False
