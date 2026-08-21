# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The ``Chat Push Subscription`` registry — one row per device, and the pruning it needs.

--------------------------------------------------------------------------------------
Re-registering the same endpoint must UPDATE, never insert
--------------------------------------------------------------------------------------

Browsers hand back the **same** endpoint every time a page reloads and calls
``pushManager.subscribe``. A registry that inserts on every subscribe therefore grows one row
per page load per device, forever — a classic leak, and one that degrades silently rather
than loudly: each duplicate costs one HTTPS request per notification, so the symptom is push
getting slower and the push service starting to rate-limit a site that appears to be
shouting.

--------------------------------------------------------------------------------------
The endpoint cannot carry the unique index, and that is not a style choice
--------------------------------------------------------------------------------------

Push endpoints are URLs with long opaque tails and routinely exceed 255 characters. Frappe's
``Data`` defaults to ``varchar(140)``; MariaDB **truncates on insert** rather than refusing,
and two distinct devices then collide on a unique index — one device silently stops receiving
anything, and the visible symptom is "push works on my laptop but not my phone".

So ``endpoint`` is ``Small Text`` with no index, and the uniqueness lives on
``endpoint_hash``: a SHA-256 of the endpoint as a fixed 64-character ``Data``. That is the
ADR's own recorded escape hatch for a natural key that will not fit an index
(``InnoDB``'s ``DYNAMIC`` row format caps an index key at 3072 bytes = 768 utf8mb4
characters, and Frappe cannot emit a prefix index at all), and this is its first use in the
chat package.

The unique constraint is declared **on the DocField**, not in a patch, and that matters:
Frappe coerces ``"" -> NULL`` only when the DocField itself carries ``unique: 1``. Without it,
any row that left the column empty would store ``""`` and the *second* such row in the whole
table would refuse to insert — in production only, invisible to every synchronous test.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

import hashlib
from typing import Any, Final

import frappe
from frappe.utils import cint, now_datetime

from erpnext_enhancements.chat.notifications.webpush import vapid

DOCTYPE: Final[str] = "Chat Push Subscription"

#: Prune a subscription that has not accepted a message in this long. Distinct from the
#: immediate prune on 404/410: this one catches the device that was wiped, reassigned or
#: simply never opened again, which no push service ever tells us about.
STALE_AFTER_DAYS: Final[int] = 60

#: Consecutive hard failures before a row is retired. Not one: a push service returning 500
#: for an afternoon must not cost everybody their subscriptions.
MAX_FAILURES: Final[int] = 10


def endpoint_hash(endpoint: str) -> str:
	"""SHA-256 hex of the endpoint. The unique key the endpoint itself cannot be.

	Hex rather than base64url: 64 characters of ``[0-9a-f]`` are collation-safe under every
	MariaDB configuration, and a case-insensitive collation would fold a base64url hash's
	``A`` and ``a`` together — which turns a unique index into an occasional, unreproducible
	collision between two devices.
	"""
	return hashlib.sha256((endpoint or "").strip().encode("utf-8")).hexdigest()


def register(
	user: str,
	*,
	endpoint: str,
	p256dh: str,
	auth: str,
	user_agent: str | None = None,
	device_label: str | None = None,
) -> str:
	"""Create or refresh one subscription. Returns the row name.

	Matched on :func:`endpoint_hash` rather than on ``(user, endpoint)``: a shared machine can
	hand the same endpoint to a second person after a re-login, and the endpoint is the thing
	the push service addresses. Treating it as owned by whoever most recently proved they hold
	it is the only reading that does not deliver one person's notifications to another's
	browser.
	"""
	digest = endpoint_hash(endpoint)
	if not digest or not p256dh or not auth:
		frappe.throw(frappe._("A push subscription needs an endpoint and its two keys."))

	existing = frappe.db.get_value(DOCTYPE, {"endpoint_hash": digest}, "name")
	values = {
		"user": user,
		"endpoint": endpoint,
		"endpoint_hash": digest,
		"p256dh": p256dh,
		"auth": auth,
		"user_agent": (user_agent or "")[:255],
		"device_label": (device_label or "")[:140],
		"vapid_public_key": _current_public_key(),
		"last_seen": now_datetime(),
		"is_active": 1,
		# A returning device is a working device. Leaving a stale failure count would let a row
		# that recovered be retired by the next transient error.
		"failure_count": 0,
	}

	if existing:
		for field, value in values.items():
			frappe.db.set_value(DOCTYPE, existing, field, value, update_modified=False)
		return str(existing)

	doc = frappe.get_doc({"doctype": DOCTYPE, **values})
	doc.insert(ignore_permissions=True)
	return doc.name


def unregister(endpoint: str) -> bool:
	"""Drop one subscription, by endpoint. What the client calls when it unsubscribes."""
	name = frappe.db.get_value(DOCTYPE, {"endpoint_hash": endpoint_hash(endpoint)}, "name")
	if not name:
		return False
	frappe.delete_doc(DOCTYPE, name, ignore_permissions=True, force=True)
	return True


def active_for(user: str) -> list[dict[str, Any]]:
	"""Every deliverable subscription for one person.

	Rows created under a **different VAPID public key** are excluded rather than attempted.
	A push service answers 403 for those — not 410 — so they would never be pruned by the
	gone-rule, and every notification would spend a request per dead device forever.
	"""
	if not user:
		return []
	rows = frappe.get_all(
		DOCTYPE,
		filters={"user": user, "is_active": 1},
		fields=["name", "endpoint", "p256dh", "auth", "vapid_public_key", "failure_count"],
	)
	return [row for row in rows if not vapid.public_key_changed_since(row.get("vapid_public_key"))]


def note_success(name: str) -> None:
	"""Record a delivery. Clears the failure count so a recovered device is not retired."""
	try:
		frappe.db.set_value(
			DOCTYPE,
			name,
			{"last_success": now_datetime(), "failure_count": 0},
			update_modified=False,
		)
	except Exception:
		# Bookkeeping must never fail a send that already succeeded.
		pass


def note_failure(name: str, *, terminal: bool) -> None:
	"""Record a failed delivery, and retire the row when it is hopeless.

	``terminal`` is the push service saying the subscription is **gone** — 404 or 410 — which
	is the only signal that a device is genuinely never coming back. Everything else is
	counted, because a push service having a bad hour must not cost the whole company their
	notifications.
	"""
	try:
		if terminal:
			frappe.db.set_value(DOCTYPE, name, {"is_active": 0}, update_modified=False)
			return
		count = cint(frappe.db.get_value(DOCTYPE, name, "failure_count")) + 1
		update: dict[str, Any] = {"failure_count": count}
		if count >= MAX_FAILURES:
			update["is_active"] = 0
		frappe.db.set_value(DOCTYPE, name, update, update_modified=False)
	except Exception:
		pass


def prune_stale() -> int:
	"""Retire subscriptions nothing has been delivered to in :data:`STALE_AFTER_DAYS`.

	Scheduled, because the push services never tell us about a device that was wiped,
	reassigned or simply never opened again — a 410 only arrives if the browser deliberately
	unsubscribed. Without this the table only ever grows, and every row in it costs one HTTPS
	request per notification to that person.

	Deactivates rather than deletes: a row that comes back is re-activated by
	:func:`register`, and keeping it means the device history survives a user asking why their
	phone stopped buzzing.
	"""
	from frappe.utils import add_days

	cutoff = add_days(now_datetime(), -STALE_AFTER_DAYS)
	rows = frappe.get_all(
		DOCTYPE,
		filters={"is_active": 1, "last_seen": ("<", cutoff)},
		pluck="name",
	)
	for name in rows:
		frappe.db.set_value(DOCTYPE, name, {"is_active": 0}, update_modified=False)

	# Then DELETE rows that have been inactive beyond the retention window. The doctype's
	# clear_old_logs does this, but nothing ever ran it: Frappe's log clearing never invoked
	# it (the doctype was never registered with Log Settings), and chat/retention.py manages
	# only the two queue tables. Folding it into this daily job keeps the table bounded — each
	# dead row otherwise costs one HTTPS request per notification forever — without adding a
	# Chat Settings (Single) field and its backfill.
	from erpnext_enhancements.chat.doctype.chat_push_subscription.chat_push_subscription import (
		ChatPushSubscription,
	)

	ChatPushSubscription.clear_old_logs()
	return len(rows)


def _current_public_key() -> str:
	"""This site's VAPID public key, or ``""`` when push is not configured."""
	try:
		return vapid.public_key()
	except Exception:
		return ""
