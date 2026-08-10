# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Room list, room detail, member list, and the SPA's one-round-trip bootstrap.

The room list is the surface that has to be fast, and it is fast because Phase 1
denormalised ``last_message_at`` / ``last_message_preview`` / ``last_message_sender`` onto
``Chat Room`` and Phase 2 maintains them on every insert. This module therefore reads
**one row per room and joins nothing to ``Chat Message``**. Resist the fresher preview.

Unread is likewise derived arithmetically — ``seq_high_water - last_read_seq`` — rather
than counted. Two integers already in hand beat a ``COUNT(*)`` per room, and the count is
exact because ``seq`` is dense per room by construction (allocated under the room's row
lock, one per message, never reused).

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint

from erpnext_enhancements.chat import permissions
from erpnext_enhancements.chat.api import _common
from erpnext_enhancements.chat.api._common import (
	MEMBER_DOCTYPE,
	ROOM_DOCTYPE,
	require_room,
	require_session,
	room_payload,
)

#: Where the server-side "last open room" lives. **Redis, not a DocType.**
#:
#: §4.3 tier 3 needs a per-user room name for cold loads and other devices. It is a hint,
#: not a fact: losing it costs the user one click (the SPA falls back to the room list),
#: which does not justify a table, a migration, or a write on every room switch. A prod
#: deploy flushes Redis, so the first visit after a deploy lands on the room list — that is
#: the documented behaviour, not a bug to work around.
_LAST_OPEN_KEY = "ee_chat_last_open"

#: The debounce marker, server-side. §4.3 asks for at most one write per 30 s per user; the
#: client throttles too, but a throttle in the client is a courtesy and this is the control.
_LAST_OPEN_THROTTLE_KEY = "ee_chat_last_open_gate"
_LAST_OPEN_THROTTLE_SECONDS = 30

#: Thirty days. Long enough that "where was I?" survives a holiday, short enough that a
#: departed employee's key expires without a cleanup path.
_LAST_OPEN_TTL_SECONDS = 30 * 24 * 60 * 60


def _cache() -> Any:
	return frappe.cache()


@frappe.whitelist()
def get_rooms(include_archived: Any = 0) -> list[dict[str, Any]]:
	"""Every room the caller is an **active** member of, newest conversation first.

	One statement, one index probe per room. The join to ``Chat Room Member`` is the
	caller's own membership row — it carries ``last_read_seq``, ``role`` and the mute state
	the list renders — and :func:`chat.permissions.membership_filter_sql` is ANDed in on top
	of it rather than being considered redundant with it. The join could be widened by an
	edit six months from now; the filter cannot be widened without editing the module whose
	entire job is that rule.

	Archived rooms are excluded unless asked for (§4.4: "archived rooms hidden behind a
	toggle"), and they sort with everything else when shown.
	"""
	user = require_session()
	scope = permissions.membership_filter_sql("`r`.`name`", user)

	archived_clause = "" if cint(include_archived) else " and coalesce(`r`.`is_archived`, 0) = 0"

	rows = frappe.db.sql(
		f"""select
				`r`.`name`, `r`.`room_type`, `r`.`title`, `r`.`description`,
				`r`.`is_archived`, `r`.`linked_doctype`, `r`.`linked_document`,
				`r`.`dm_user_1`, `r`.`dm_user_2`,
				`r`.`last_message`, `r`.`last_message_at`, `r`.`last_message_sender`,
				`r`.`last_message_preview`, `r`.`seq_high_water`,
				`m`.`last_read_seq`, `m`.`role`, `m`.`notification_mode`, `m`.`muted_until`
			from `tabChat Room` `r`
			join `tabChat Room Member` `m`
				on `m`.`room` = `r`.`name` and `m`.`user` = %(user)s and `m`.`is_active` = 1
			where {scope}{archived_clause}
			order by `r`.`last_message_at` desc, `r`.`name` asc""",
		{"user": user},
		as_dict=True,
	)

	return [room_payload(row, member=row) for row in rows]


@frappe.whitelist()
def get_room(room: str) -> dict[str, Any]:
	"""One room, plus its roster and the caller's own membership state.

	Used on a deep link, where the SPA has a room name from the URL and nothing else. The
	gate runs first: an unreadable room is refused with the same message as a nonexistent
	one, so a 403 cannot be used to enumerate rooms.
	"""
	user, name = require_room(room)

	row = frappe.db.get_value(
		ROOM_DOCTYPE,
		name,
		[
			"name",
			"room_type",
			"title",
			"description",
			"is_archived",
			"linked_doctype",
			"linked_document",
			"dm_user_1",
			"dm_user_2",
			"last_message",
			"last_message_at",
			"last_message_sender",
			"last_message_preview",
			"seq_high_water",
		],
		as_dict=True,
	)
	if not row:
		# require_room let this through only for Administrator / the oversight role, whose
		# gate does not check existence. Answer the same way as a refusal.
		frappe.throw(frappe._("That conversation is no longer available."), frappe.DoesNotExistError)

	mine = frappe.db.get_value(
		MEMBER_DOCTYPE,
		{"room": name, "user": user, "is_active": 1},
		["last_read_seq", "role", "notification_mode", "muted_until"],
		as_dict=True,
	) or {}

	payload = room_payload(row, member=mine)
	payload["members"] = _members(name)
	payload["linked_document_url"] = _linked_document_url(row)
	return payload


@frappe.whitelist()
def get_members(room: str) -> list[dict[str, Any]]:
	"""The roster of one room: who is in it, their role, and their read high-water mark.

	The read marks are what the transcript renders per-message receipts from (§4.7): "user X
	has read message Y" is derived from ``Y.seq <= member.last_read_seq``, not stored per
	message. One row per member beats one row per (member × message) by roughly the ratio
	this feature would have failed on.
	"""
	_user, name = require_room(room)
	return _members(name)


def _members(room: str) -> list[dict[str, Any]]:
	"""Roster rows for a room the caller has **already** been gated on.

	Private and taking a pre-validated room name, so there is exactly one gate for this data
	and it is in the caller. ``full_name``/``user_image`` come from ``tabUser``, which every
	authenticated user can already read — the join saves the client fifty round trips to
	render fifty avatars.
	"""
	scope = permissions.membership_filter_sql("`m`.`room`")

	rows = frappe.db.sql(
		f"""select
				`m`.`user`, `m`.`role`, `m`.`is_active`, `m`.`joined_at`,
				`m`.`last_read_seq`, `m`.`last_read_at`,
				`u`.`full_name`, `u`.`user_image`, `u`.`enabled`
			from `tabChat Room Member` `m`
			left join `tabUser` `u` on `u`.`name` = `m`.`user`
			where `m`.`room` = %(room)s and `m`.`is_active` = 1 and {scope}
			order by `u`.`full_name` asc, `m`.`user` asc""",
		{"room": room},
		as_dict=True,
	)

	return [
		{
			"user": row["user"],
			"full_name": row.get("full_name") or row["user"],
			"user_image": row.get("user_image") or None,
			"role": row.get("role") or "Member",
			"joined_at": _common._dt(row.get("joined_at")),
			"last_read_seq": cint(row.get("last_read_seq")),
			"last_read_at": _common._dt(row.get("last_read_at")),
			"enabled": bool(cint(row.get("enabled", 1))),
		}
		for row in rows
	]


def _linked_document_url(row: Any) -> str | None:
	"""Desk URL for a document-linked room's document, resolved **server-side**.

	``frappe.utils.get_url_to_form`` rather than a hand-built ``/app/{slug}/{name}``: desk
	routing and doctype slugification have changed between versions, and a client that
	builds the path itself is a client that breaks on an upgrade nobody connected to chat.
	"""
	doctype = (row.get("linked_doctype") or "").strip() if isinstance(row, dict) else ""
	name = (row.get("linked_document") or "").strip() if isinstance(row, dict) else ""
	if not doctype or not name:
		return None
	try:
		from frappe.utils import get_url_to_form

		return get_url_to_form(doctype, name)
	except Exception:
		return None


@frappe.whitelist()
def set_last_open_room(room: str | None = None, thread: str | None = None) -> dict[str, Any]:
	"""Remember where the caller was, for a cold load on another device (§4.3 tier 3).

	**Debounced server-side to one write per 30 s per user**, and only called by the client
	on an actual room change — never on scroll. The throttle lives here as well as in the
	client because a client-side throttle is one bug away from a write per scroll frame, and
	this is the cheapest possible ceiling on that.

	Passing no room clears the hint. Passing a room the caller cannot read is refused rather
	than stored: a hint that resolves to a 403 on the next cold load is worse than no hint.
	"""
	user = require_session()
	cache = _cache()

	if not (room or "").strip():
		cache.hdel(_LAST_OPEN_KEY, user)
		return {"stored": False}

	_user, name = require_room(room, user=user)

	gate_key = f"{_LAST_OPEN_THROTTLE_KEY}:{user}"
	if cache.get_value(gate_key):
		# Inside the window. Not an error — the client is allowed to be eager, it just does
		# not get to write. The previous value stands.
		return {"stored": False, "throttled": True}
	cache.set_value(gate_key, "1", expires_in_sec=_LAST_OPEN_THROTTLE_SECONDS)

	cache.hset(
		_LAST_OPEN_KEY,
		user,
		{"room": name, "thread": (thread or "").strip() or None},
	)
	# hset does not take a TTL, so the expiry rides on the hash as a whole. Refreshed on
	# every write; a user who stops using chat expires 30 days after their last room switch.
	try:
		cache.expire(_LAST_OPEN_KEY, _LAST_OPEN_TTL_SECONDS)
	except Exception:
		# Not every Frappe cache backend exposes expire(). A key that never expires is a
		# small waste, not a correctness problem, so this must not fail the request.
		pass

	return {"stored": True, "room": name}


def last_open_room(user: str | None = None) -> dict[str, Any]:
	"""Read the tier-3 hint. Returns ``{}`` when there is none, never raises.

	Not whitelisted: it is consumed by :func:`get_bootstrap` and by ``boot.py``, both of
	which have already resolved the user. An endpoint that hands back "where was this user
	last?" for an arbitrary user argument is a surveillance surface nobody asked for.
	"""
	who = (user or frappe.session.user or "").strip()
	if not who or who == "Guest":
		return {}
	try:
		value = _cache().hget(_LAST_OPEN_KEY, who)
	except Exception:
		return {}
	if not isinstance(value, dict):
		return {}
	return {"room": value.get("room") or None, "thread": value.get("thread") or None}


@frappe.whitelist()
def get_bootstrap() -> dict[str, Any]:
	"""Everything the SPA needs to paint its first frame, in one round trip.

	Deliberately **not** ``extend_bootinfo``. Bootinfo is serialised into every Desk page
	load for every user; the room list belongs to the one page that renders it. What does go
	into bootinfo (``boot.py``) is the single ``ee_chat`` boolean and the unread totals the
	*bubble badge* needs — a number and a room name, not a list.

	The flags are re-read here rather than trusted from the shell so that a settings change
	reaches a long-lived SPA tab on its next reconnect.
	"""
	user = require_session()

	from erpnext_enhancements.api.chat import get_settings_public

	rooms = get_rooms()
	total_unread = sum(cint(room["unread"]) for room in rooms)

	return {
		"user": user,
		"full_name": frappe.db.get_value("User", user, "full_name") or user,
		"user_image": frappe.db.get_value("User", user, "user_image") or None,
		"flags": get_settings_public(),
		"rooms": rooms,
		"total_unread": total_unread,
		"last_open": last_open_room(user),
		"receipt_avatar_member_limit": _common.RECEIPT_AVATAR_MEMBER_LIMIT,
		"page_size": _common.DEFAULT_PAGE_SIZE,
	}
