# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""`@mention` autocomplete: room members first, then other users, plus `@triton`.

Two-tier ordering because the answer is almost always in the room, and a menu that puts a
colleague from another department above the person you are talking to is a menu people stop
trusting. Members are ranked ahead of non-members and both tiers are matched on the same
prefix rule.

**`@triton` is offered in every room** (§4.4), at the top, visually distinct. It is not a
``User``: mentioning it produces a ``Chat Mention`` row with ``mention_type = "Triton"`` and
no user link. What happens *next* — Triton answering in the thread — is Phase 5's, and this
phase deliberately builds only the mention and the "Triton is thinking…" placeholder.

**Non-members are offerable but not mentionable.** The menu lists coworkers so a user can
discover that somebody exists; :func:`compose._clean_mentions` then **drops** a ``User``
mention of a non-member, because a stored mention of somebody who cannot open the room would
have Phase 4 notifying them about a conversation they cannot read. The client shows the
distinction (a non-member entry offers "add to room" rather than a plain insert) instead of
hiding people and making the room feel broken.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint

from erpnext_enhancements.chat import permissions
from erpnext_enhancements.chat.api._common import require_room

#: Result ceiling. A capped list is a design decision, not a performance hack: an
#: autocomplete that can return 400 rows is one nobody can navigate with a keyboard.
MAX_RESULTS = 12

#: The sentinel the client inserts for ``@triton``. Matches ``Chat Mention.mention_type``'s
#: ``Triton`` option so the composer and the child table agree without a translation layer.
TRITON_KEY = "__triton__"


@frappe.whitelist()
def search_mention_targets(room: str, query: str | None = None) -> dict[str, Any]:
	"""Autocomplete candidates for one room. Debounced and capped by the client too.

	Args:
		room: the room being composed in. Gated — the roster of a room you cannot read is
			itself confidential (who is talking to whom), which is why this is not a thin
			wrapper over Frappe's generic user search.
		query: the text after ``@``. Empty is legitimate — it is what the menu shows the
			instant ``@`` is typed — and returns the room's own members.
	"""
	_user, name = require_room(room)
	term = (query or "").strip()
	pattern = "%" + term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"

	# The bot's own ERPNext User is not a person and must never be offered as one.
	#
	# **Mentioning it does nothing at all, silently.** ``dispatch_spa_message`` gates on a
	# ``Chat Mention`` row with ``mention_type = "Triton"``, which only the sentinel below
	# produces; a mention of the bot's User is an ordinary ``User`` mention, so the turn is
	# never dispatched, nothing is enqueued, and nothing is logged. That is the worst shape a
	# failure can take, and it cost an hour of production debugging on the day the bot became
	# a real account: the menu offered "Triton" and "Triton Chat Bot" side by side, one of them
	# worked, and the other produced no answer and no error.
	bot = _bot_user_id()
	members = [row for row in _member_candidates(name, pattern) if row["user"] != bot]
	seen = {row["user"] for row in members} | ({bot} if bot else set())
	others = _other_candidates(pattern, exclude=seen, room_type_limit=MAX_RESULTS - len(members))

	results = members + others
	if _triton_matches(term):
		results.insert(
			0,
			{
				"kind": "triton",
				"user": TRITON_KEY,
				"label": "Triton",
				"description": frappe._("Ask the assistant in this conversation"),
				"is_member": True,
				"user_image": None,
			},
		)

	return {"room": name, "query": term, "results": results[:MAX_RESULTS]}


def _triton_matches(term: str) -> bool:
	"""Whether ``@triton`` should be offered for this prefix.

	Matched on a prefix of the literal name rather than fuzzily: ``@tri`` should offer it,
	``@jane`` should not, and a substring rule would put the assistant in the menu every
	time somebody mentioned a colleague with a ``t`` in their name.
	"""
	return not term or "triton".startswith(term.lower())


def _bot_user_id() -> str:
	"""``Chat Settings.bot_user``, or ``""``.

	Read directly rather than through ``handler._bot_user``, which **raises** when the field is
	empty by design — correct there, because a turn that cannot name its bot must not proceed.
	Here the answer is only used to hide a row, and a settings page mid-configuration must not
	take the mention menu down with it.
	"""
	try:
		return str(frappe.db.get_single_value("Chat Settings", "bot_user") or "")
	except Exception:
		return ""


def _member_candidates(room: str, pattern: str) -> list[dict[str, Any]]:
	"""Active members of the room whose name or email matches. Ranked first."""
	scope = permissions.membership_filter_sql("`m`.`room`")
	rows = frappe.db.sql(
		f"""select `m`.`user`, `u`.`full_name`, `u`.`user_image`, `u`.`enabled`
			from `tabChat Room Member` `m`
			join `tabUser` `u` on `u`.`name` = `m`.`user`
			where `m`.`room` = %(room)s
				and `m`.`is_active` = 1
				and coalesce(`u`.`enabled`, 1) = 1
				and (`u`.`full_name` like %(pattern)s or `m`.`user` like %(pattern)s)
				and {scope}
			order by `u`.`full_name` asc
			limit %(limit)s""",
		{"room": room, "pattern": pattern, "limit": MAX_RESULTS},
		as_dict=True,
	)
	return [
		{
			"kind": "user",
			"user": row["user"],
			"label": row.get("full_name") or row["user"],
			"description": row["user"],
			"is_member": True,
			"user_image": row.get("user_image") or None,
		}
		for row in rows
	]


def _other_candidates(
	pattern: str, *, exclude: set[str], room_type_limit: int
) -> list[dict[str, Any]]:
	"""Enabled System Users who are not already in the room. Ranked second.

	Reads ``tabUser`` only — no chat table is touched, so no membership filter applies and
	none is claimed. Every authenticated user can already read ``User``; this endpoint
	discloses nothing the link-field search on any form does not.
	"""
	if room_type_limit < 1:
		return []

	rows = frappe.get_all(
		"User",
		filters={
			"enabled": 1,
			"user_type": "System User",
			"name": ["not in", list(exclude) + ["Guest", "Administrator"]],
		},
		or_filters={"full_name": ["like", pattern], "name": ["like", pattern]},
		fields=["name", "full_name", "user_image"],
		order_by="full_name asc",
		limit_page_length=room_type_limit,
	)
	return [
		{
			"kind": "user",
			"user": row["name"],
			"label": row.get("full_name") or row["name"],
			"description": row["name"],
			"is_member": False,
			"user_image": row.get("user_image") or None,
		}
		for row in rows
	]


def extract_mentions(text: str, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
	"""Pure: turn a body and the composer's chosen targets into ``Chat Mention`` rows.

	Kept pure and in this module — no Frappe, no database — so the offset arithmetic is
	exercisable in the bench-free CI tier, which is the only tier with automatic regression
	protection in this repo. The composer calls it, and ``send_message`` re-validates the
	result server-side rather than trusting it.

	``targets`` are ``{"user", "kind", "start", "length"}`` as recorded by the composer at
	the moment the user picked from the menu. Offsets that no longer fall inside ``text``
	are dropped rather than clamped: a clamped offset renders a chip over the wrong words,
	which looks like corruption rather than like a bug.
	"""
	rows: list[dict[str, Any]] = []
	length = len(text or "")
	for target in targets or []:
		start = cint(target.get("start"))
		span = cint(target.get("length"))
		if start < 0 or span < 1 or start + span > length:
			continue
		kind = "Triton" if target.get("kind") == "triton" else "User"
		rows.append(
			{
				"mention_type": kind,
				"user": None if kind == "Triton" else (target.get("user") or None),
				"start_index": start,
				"length": span,
			}
		)
	return rows
