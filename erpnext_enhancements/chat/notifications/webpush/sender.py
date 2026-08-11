# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Posting the encrypted payload, and reading what the push service says back.

The transport is trivial. **The status handling is the module**, because each code means a
different thing about the *subscription* rather than about the request, and treating them
alike produces either a table full of dead rows or a working device that gets retired for one
bad afternoon.

--------------------------------------------------------------------------------------
The status table, and the decision each one forces
--------------------------------------------------------------------------------------

===== ============================================================================
201   Accepted. Also 200 from some services. Record the success and clear the
      failure count — a device that just worked is not a device with a history.
404   The subscription does not exist. Terminal.
410   Gone; the browser unsubscribed. Terminal, and the *only* code that means it.
413   The payload is too large for this service. Retry once **without the preview**
      rather than dropping the notification: the sender and room alone are worth
      delivering, and this is exactly the case a long message produces.
429   Rate limited. Honour ``Retry-After``. Not the subscription's fault and it must
      not count against it.
5xx   The service is having trouble. Count it, but do not retire on one.
403   Almost always a VAPID mismatch — this site's keypair changed after the browser
      subscribed. Counted rather than terminal, because retiring on it would delete
      every subscription on the site the moment somebody rotated a key by mistake.
===== ============================================================================

**Only 404 and 410 retire a row immediately.** That distinction is what stops a dead-
subscription table growing forever *and* stops a transient outage wiping the roster; getting
it wrong in either direction is invisible until somebody says push stopped working weeks ago.

--------------------------------------------------------------------------------------
What is in the payload, and what deliberately is not
--------------------------------------------------------------------------------------

Sender and room name, a deep link, and a stable tag. **The message body only when
``Chat Settings.push_show_preview`` is on, and it ships off** — this is a business chat
carrying customer names, prices and complaints about colleagues, and a lock screen is legible
to anybody glancing at a phone left on a table (CQ-6).

Say the honest thing about that: it is a **rendering** decision, not a security control. The
payload is encrypted end to end either way, and the moment it arrives the text is on the
device. Hiding it on the lock screen is about shoulders in a room, not about the network.

The ``tag`` is per room and stable. That is what makes a later push *replace* the previous
one for that conversation instead of stacking twenty banners, and it is what lets a single
``getNotifications({tag})`` call from the service worker clear the lot when the person reads
the room somewhere else.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

import json
from typing import Any, Final

import frappe
from frappe.utils import cint

from erpnext_enhancements.chat import links
from erpnext_enhancements.chat.notifications.webpush import encrypt, subscriptions, vapid

#: Seconds a push service should hold an undelivered message for. Ten minutes: long enough to
#: cover a phone in a lift, short enough that nobody is told about a conversation that moved
#: on an hour ago.
TTL_SECONDS: Final[int] = 600

#: Push services commonly cap a request at 4096 bytes total. The record size has to leave room
#: for the 86-byte header on top of it.
RECORD_SIZE: Final[int] = 4096

#: How long to wait on the push service. Short: this runs in a background job that may have
#: twenty subscriptions to work through, and a service that has stopped answering must not
#: hold the worker for a minute per device.
HTTP_TIMEOUT_SECONDS: Final[int] = 10

#: The codes that mean the subscription is genuinely gone, and nothing else means it.
TERMINAL_STATUSES: Final[frozenset[int]] = frozenset({404, 410})

#: Preview text is trimmed to this before encryption. A notification is a prompt to open the
#: app, not a way to read the conversation from the lock screen.
PREVIEW_LIMIT: Final[int] = 120


def push_to_user(
	user: str,
	*,
	room: str,
	room_label: str,
	message: str,
	sender_label: str,
	preview: str | None = None,
) -> int:
	"""Push to every live subscription of one person. Returns how many were accepted.

	Returns ``0`` rather than raising when push is switched off or unconfigured: the bell row
	and the in-app counters are separate surfaces and must not be lost because this one is not
	set up.
	"""
	if not _push_enabled():
		return 0
	if not vapid.is_configured():
		# Once per fan-out, not once per subscription. A site that has not run `generate` yet
		# would otherwise write one Error Log row per recipient per message.
		frappe.logger("chat").debug("chat push skipped: no VAPID keypair in site_config")
		return 0

	payload = build_payload(
		room=room,
		room_label=room_label,
		message=message,
		sender_label=sender_label,
		preview=preview,
	)

	delivered = 0
	for row in subscriptions.active_for(user):
		if send_one(row, payload):
			delivered += 1
	return delivered


def build_payload(
	*,
	room: str,
	room_label: str,
	message: str,
	sender_label: str,
	preview: str | None = None,
	show_preview: bool | None = None,
) -> dict[str, Any]:
	"""What the service worker receives. Identifiers, a link, a tag — and maybe a preview.

	``tag`` is ``chat-room-<room>`` and it is the load-bearing field: it makes a second push
	for the same conversation replace the first rather than stack, which is what keeps twenty
	messages to a busy room from producing twenty banners.
	"""
	include = _show_preview() if show_preview is None else bool(show_preview)
	body = (preview or "").strip()[:PREVIEW_LIMIT] if include and preview else ""

	return {
		"kind": "chat",
		"room": room,
		"message": message,
		"title": sender_label or frappe._("New message"),
		"room_label": room_label or "",
		"body": body,
		"tag": room_tag(room),
		"url": links.build_message_deep_link(room, message=message),
	}


def room_tag(room: str) -> str:
	"""The stable per-room notification tag. One spelling, three consumers.

	Written once because the sender, the read-sync's close instruction and the service worker
	all have to agree on it exactly. If they drift, notifications stack instead of replacing
	and reading a room stops clearing its banner — two separate bugs from one typo.
	"""
	return f"chat-room-{room}"


def send_one(subscription: dict[str, Any], payload: dict[str, Any]) -> bool:
	"""Encrypt and POST to one endpoint. Returns whether it was accepted.

	Never raises: one unreachable device must not stop the others, and this is called in a
	loop over everything one person owns.
	"""
	name = subscription.get("name")
	endpoint = (subscription.get("endpoint") or "").strip()
	if not endpoint:
		return False

	try:
		accepted, terminal, retry_without_preview = _post(endpoint, subscription, payload)
	except Exception:
		frappe.log_error(title="chat push request failed", message=f"subscription={name}")
		subscriptions.note_failure(str(name), terminal=False)
		return False

	if accepted:
		subscriptions.note_success(str(name))
		return True

	if retry_without_preview and payload.get("body"):
		# 413. The preview is the only variable-length field, so dropping it is the one retry
		# that can change the outcome — and a notification naming the sender and the room is
		# worth far more than none at all.
		stripped = dict(payload, body="")
		try:
			accepted, terminal, _ = _post(endpoint, subscription, stripped)
		except Exception:
			accepted, terminal = False, False
		if accepted:
			subscriptions.note_success(str(name))
			return True

	subscriptions.note_failure(str(name), terminal=terminal)
	return False


def _post(
	endpoint: str, subscription: dict[str, Any], payload: dict[str, Any]
) -> tuple[bool, bool, bool]:
	"""One HTTPS POST. Returns ``(accepted, terminal, retry_without_preview)``.

	``requests`` is imported inside the function for the same reason the transport client does
	it: the bench-free test tier deliberately runs with ``requests`` absent, and a module-scope
	import would make this file unimportable there.
	"""
	import requests

	body = encrypt.encrypt(
		json.dumps(payload, separators=(",", ":")).encode("utf-8"),
		ua_public=encrypt.b64u_decode(subscription["p256dh"]),
		auth_secret=encrypt.b64u_decode(subscription["auth"]),
		record_size=RECORD_SIZE,
	)

	response = requests.post(
		endpoint,
		data=body,
		headers={
			"Authorization": vapid.authorization_header(endpoint),
			"Content-Encoding": "aes128gcm",
			"Content-Type": "application/octet-stream",
			"TTL": str(TTL_SECONDS),
			# `normal` rather than `high`: a chat message is not worth waking a sleeping phone's
			# radio ahead of schedule, and services throttle senders who always claim urgency.
			"Urgency": "normal",
			# Same string as the notification tag, so the SERVICE collapses superseded messages
			# for a conversation before they are ever delivered. This is the half of the
			# coalescing that happens off our infrastructure entirely.
			"Topic": _topic(payload),
		},
		timeout=HTTP_TIMEOUT_SECONDS,
	)

	status = cint(response.status_code)
	if status in (200, 201, 202):
		return True, False, False
	if status in TERMINAL_STATUSES:
		return False, True, False
	if status == 413:
		return False, False, True
	if status == 429:
		_note_retry_after(response)
		return False, False, False

	frappe.logger("chat").debug("chat push rejected status=%s endpoint=%s", status, _origin(endpoint))
	return False, False, False


def _topic(payload: dict[str, Any]) -> str:
	"""The ``Topic`` header. Must be URL-safe base64 and at most 32 characters.

	The room tag is longer than that and contains a hyphen, so it is hashed down rather than
	sent raw — a service that rejects the header rejects the whole request, which would turn a
	coalescing optimisation into a total delivery failure.
	"""
	import hashlib

	digest = hashlib.sha256((payload.get("tag") or "").encode("utf-8")).digest()
	return encrypt.b64u_encode(digest)[:32]


def _note_retry_after(response: Any) -> None:
	"""Log what the service asked for. There is no retry queue here yet, and that is honest.

	A 429 means this message is lost for that device. The bell row still exists, the unread
	counter still moved, and the next message in the room will try again — so the failure is
	one missed banner rather than a missing conversation. Building a retry queue for push would
	mean a second outbox, and it is not worth one until 429s are actually observed.
	"""
	try:
		retry_after = response.headers.get("Retry-After")
	except Exception:
		retry_after = None
	frappe.logger("chat").debug("chat push rate limited retry_after=%s", retry_after)


def _origin(endpoint: str) -> str:
	"""The push service's host, for a log line. **Never the full endpoint.**

	An endpoint URL is a bearer capability: anybody holding it can push to that browser. It
	does not belong in the Error Log, which every System Manager can read.
	"""
	try:
		return vapid.audience(endpoint)
	except Exception:
		return "<unparseable>"


def _push_enabled() -> bool:
	try:
		return bool(cint(frappe.db.get_single_value("Chat Settings", "push_enabled")))
	except Exception:
		return False


def _show_preview() -> bool:
	try:
		return bool(cint(frappe.db.get_single_value("Chat Settings", "push_show_preview")))
	except Exception:
		return False
