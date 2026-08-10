# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Starting a conversation — the one thing Phase 3 shipped without.

Phase 3 built the whole client and the whole read surface, and left no way to *make* a room.
Phase 2 creates rooms three ways (the org sweep, per-document rooms, and inbound
provisioning from Google), all of which either need an org structure or a document to hang
off, so a pilot on an empty site had a correct application with nothing in it.

**Nothing here is a second room creator.** Both writes go through the helpers Phase 2
already wrote and already made race-safe:

* :func:`chat.sync.provisioning._insert_room_deduped` — probe, insert, and treat a composite
  unique collision as SUCCESS rather than an error;
* :func:`chat.sync.membership.insert_room_member` — the single place a ``Chat Room Member``
  row is created, where ``unique(room, user)`` colliding means "already a member", which is
  what the caller wanted.

Reaching for ``provisioning``'s underscore-prefixed helper across a module boundary is a
deliberate trade. The alternative is a second implementation of "create a room without
duplicating one", and both of those docstrings exist because the first version of that rule
was got wrong. A slightly impure import is cheaper than a divergent copy.

**Why a DM cannot be created twice.** ``unique(dm_user_1, dm_user_2)`` is a real index (added
by ``patches/add_chat_indexes.py``) and ``ChatRoom.before_insert`` sorts the pair
lexicographically, so a conversation between A and B is the same row as one between B and A
no matter who clicks first. This module therefore does not need a lock, and deliberately does
not take one: it probes in the canonical order, and lets the index settle the race it cannot
see.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint

from erpnext_enhancements.chat.api._common import (
	ROOM_DOCTYPE,
	require_session,
	room_payload,
)

#: Ceiling on a group's initial roster. Not a schema limit — a blast-radius limit. Creating a
#: room mirrors to a Google space eventually, and a fat-fingered "select all" that provisions
#: a 200-person space cannot be undone by this codebase (there is no ``spaces.delete`` call,
#: on purpose: deleting a space takes the conversation with it).
MAX_INITIAL_MEMBERS = 50

#: Result ceiling for the people picker.
MAX_PEOPLE_RESULTS = 20


@frappe.whitelist()
def create_direct_message(user: str) -> dict[str, Any]:
	"""Open the DM with ``user``, creating it only if it does not already exist.

	Idempotent by construction rather than by checking: the probe runs in the canonical
	(sorted) pair order and ``unique(dm_user_1, dm_user_2)`` catches anybody who wins the race
	between the probe and the insert. Clicking twice, or two people opening the same
	conversation at the same moment, both return the same room.

	Returns the room in the same shape ``get_rooms`` uses, plus ``created``, so the client can
	drop it straight into the room list without a refetch.
	"""
	me = require_session()
	other = _resolve_person(user)

	if other == me:
		# A note-to-self room is a real feature in some products. It is not one here: the
		# schema's DM identity is a PAIR, and a self-DM would store the same user twice and
		# read as a room with one member. Refused rather than quietly producing something odd.
		frappe.throw(frappe._("You cannot start a direct message with yourself."))

	# Canonical order, matching ChatRoom.before_insert. The probe must use the same ordering
	# the index does or it misses an existing room half the time — the half where the caller's
	# address sorts second.
	first, second = sorted([me, other])

	from erpnext_enhancements.chat.sync import membership, provisioning

	room, created = provisioning._insert_room_deduped(
		{
			"room_type": provisioning.ROOM_TYPE_DM,
			# No title: a DM is named after the other participant, and which participant that
			# is depends on who is looking. The client resolves it per viewer (`roomTitle`).
			"title": None,
			"dm_user_1": first,
			"dm_user_2": second,
			"provisioning_mode": provisioning.PROVISION_ON_FIRST_MESSAGE,
			"provisioning_state": provisioning.STATE_PENDING,
			"membership_authority": "ERPNext",
		},
		probe={"dm_user_1": first, "dm_user_2": second},
	)
	if not room:
		frappe.throw(frappe._("Could not open that conversation. Please try again."))

	# Both people, always — a DM with one member row is a conversation the other person cannot
	# see, and `insert_room_member` treats an existing row as success.
	for person in (first, second):
		membership.insert_room_member(room, person, role="Member")

	return _room_result(room, created)


@frappe.whitelist()
def create_group(title: str, members: Any = None) -> dict[str, Any]:
	"""Create a named group room with the caller as its Manager.

	**Not deduplicated, and that is correct.** Two group rooms may legitimately share a title —
	"Riverwalk" the design review and "Riverwalk" the install crew are different conversations.
	Only DMs and document rooms have an identity the database can enforce; a group's identity
	is that somebody deliberately made it. The client guards against the double-click; the
	server does not pretend a title is a key.
	"""
	me = require_session()

	name = (title or "").strip()
	if not name:
		frappe.throw(frappe._("A group conversation needs a name."))
	if len(name) > 140:
		frappe.throw(frappe._("That name is too long; 140 characters is the limit."))

	people = []
	for candidate in _coerce_list(members):
		resolved = _resolve_person(candidate)
		if resolved and resolved != me and resolved not in people:
			people.append(resolved)

	if len(people) > MAX_INITIAL_MEMBERS:
		frappe.throw(
			frappe._("A new conversation can start with at most {0} other people.").format(
				MAX_INITIAL_MEMBERS
			)
		)

	from erpnext_enhancements.chat.sync import membership, provisioning

	doc = frappe.get_doc(
		{
			"doctype": ROOM_DOCTYPE,
			"room_type": provisioning.ROOM_TYPE_GROUP,
			"title": name,
			"provisioning_mode": provisioning.PROVISION_ON_FIRST_MESSAGE,
			"provisioning_state": provisioning.STATE_PENDING,
			# ERPNext owns the roster of a room ERPNext created. `Bidirectional` is the field
			# default and is right for a space that already exists on the Google side and whose
			# membership Chat may also change; it is wrong for one born here, where an inbound
			# diff has nothing to reconcile against yet.
			"membership_authority": "ERPNext",
		}
	)
	# ignore_permissions: Chat Room's DocPerm grants `read` only (ADR §H.4.2) — it exists to
	# make the socket join work, not to authorise writes. Authorisation for this write is the
	# pilot gate in require_session, which has already run.
	doc.insert(ignore_permissions=True)
	room = str(doc.name)

	membership.insert_room_member(room, me, role="Manager")
	for person in people:
		membership.insert_room_member(room, person, role="Member")

	return _room_result(room, True)


@frappe.whitelist()
def search_people(query: str | None = None, limit: Any = None) -> list[dict[str, Any]]:
	"""Coworkers the caller can start a conversation with.

	Distinct from ``chat.api.mentions.search_mention_targets``, which is scoped to a room and
	ranks that room's members first. This one has no room yet — it is the picker you use
	*before* the conversation exists — so it is a plain enabled-System-User search.

	It reads ``tabUser`` only and touches no chat table, so no membership filter applies and
	none is claimed. It discloses nothing the link-field search on any form does not: the same
	list, the same columns, `name`/`full_name`/`user_image`.
	"""
	require_session()
	term = (query or "").strip()
	pattern = "%" + term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
	size = min(max(cint(limit) or MAX_PEOPLE_RESULTS, 1), MAX_PEOPLE_RESULTS)

	rows = frappe.get_all(
		"User",
		filters={
			"enabled": 1,
			"user_type": "System User",
			"name": ["not in", [frappe.session.user, "Guest", "Administrator"]],
		},
		or_filters={"full_name": ["like", pattern], "name": ["like", pattern]},
		fields=["name", "full_name", "user_image"],
		order_by="full_name asc",
		limit_page_length=size,
	)

	return [
		{
			"user": row["name"],
			"label": row.get("full_name") or row["name"],
			"description": row["name"],
			"user_image": row.get("user_image") or None,
		}
		for row in rows
	]


# --------------------------------------------------------------------------- internals


def _room_result(room: str, created: bool) -> dict[str, Any]:
	"""The new room in the room-list shape, so the client needs no second fetch."""
	row = frappe.db.get_value(
		ROOM_DOCTYPE,
		room,
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
	member = frappe.db.get_value(
		"Chat Room Member",
		{"room": room, "user": frappe.session.user, "is_active": 1},
		["last_read_seq", "role", "notification_mode", "muted_until"],
		as_dict=True,
	) or {}

	payload = room_payload(row, member=member)
	payload["created"] = bool(created)
	return payload


def _resolve_person(value: Any) -> str:
	"""One address → one enabled ``User`` name, or a refusal.

	Goes through ``membership.resolve_user`` so the spelling rule is shared with the sync
	engine: an address that arrives as ``Jane.Doe@…`` and a ``User`` named ``jane.doe@…`` are
	the same person, and deciding that in two places is how a DM gets created twice under two
	spellings — which the unique index would then happily allow, because the pair differs.
	"""
	from erpnext_enhancements.chat.sync.membership import resolve_user

	raw = (str(value or "")).strip()
	if not raw:
		frappe.throw(frappe._("Choose somebody to start a conversation with."))

	user = resolve_user(raw)
	if not user:
		frappe.throw(frappe._("{0} does not have an ERPNext account.").format(raw))

	# Disabled users and Guest are refused rather than silently producing a room nobody can
	# open. `resolve_user` answers "which User row is this", not "should this person be in a
	# conversation", and those are different questions.
	if user in ("Guest", "Administrator"):
		frappe.throw(frappe._("That account cannot take part in chat."))
	if not cint(frappe.db.get_value("User", user, "enabled")):
		frappe.throw(frappe._("{0}'s account is disabled.").format(user))

	return user


def _coerce_list(value: Any) -> list[str]:
	"""``frappe.xcall`` sends arrays as JSON strings on some paths and lists on others."""
	if not value:
		return []
	if isinstance(value, str):
		try:
			value = frappe.parse_json(value)
		except Exception:
			return []
	if isinstance(value, dict):
		value = list(value.values())
	if not isinstance(value, list | tuple):
		return []
	return [str(item).strip() for item in value if str(item).strip()]
