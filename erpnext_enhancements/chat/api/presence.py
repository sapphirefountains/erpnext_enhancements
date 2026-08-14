# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Presence, typing, and the focus signal Phase 4's suppression matrix runs on.

**None of this is sourced from Google Chat, and none of it can be.** Chat has no
typing-indicator API at all, for anyone; its ``users.availability`` and read-state surfaces
are *self-scoped* — a caller reads their own, never a coworker's. So a colleague chatting
from the native Chat client contributes no typing signal and no presence signal here, and
their dot reflects their ERPNext session rather than their Chat session. That is invariant
I14 and a real product limitation: it is stated in ``chat/README.md``, and the UI renders it
honestly (an explicit "no ERPNext session" state) rather than shipping a dot that lies.

**Redis with a TTL, never a DocType.** At fifty users heartbeating every 30 s a DocType
would take ~144,000 writes a day for data that is worthless after sixty seconds. More
importantly the TTL *is* the crash handling: a browser that dies, a laptop lid that closes,
a killed tab — all of them simply expire. There is deliberately **no cleanup code path** for
the crash case, and that is the design rather than an omission. A "goodbye" message is an
optimisation layered on top; it is never the thing correctness depends on.

**Keyed on user *and* client, because a user has three tabs.** Presence is the union across
a user's clients and focus is evaluated as "**no** client of this user has that room
focused". Keying on the user alone makes the last tab to heartbeat overwrite the others,
which reads as "notifications stopped working when I opened a second tab" and is the single
most likely thing to get wrong here. :func:`focus_state` is the pure function that union is
written in, and it has its own test.

Typing writes **nothing** to the database — no DocType, no row, no column. It is one
permission check and one realtime publish.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import time
from typing import Any

import frappe
from frappe.utils import cint

from erpnext_enhancements.chat import realtime
from erpnext_enhancements.chat.api._common import require_room, require_session
from erpnext_enhancements.chat.notifications import policy, settings
from erpnext_enhancements.chat.notifications import presence as store

#: One key per (user, client). The hash lets a single ``hgetall`` answer "every client of
#: this user" without a key scan, which is what the focus union needs on every publish.
#:
#: Owned by :mod:`chat.notifications.presence` since Phase 4 — the store and the reader had
#: to agree on a key shape, and two modules holding the same string literal is how they stop
#: agreeing. Re-exported here because this module's own tests and README refer to it.
_PRESENCE_KEY = store.KEY_PREFIX

#: **Changed in Phase 4, deliberately, from the house 30 s / 75 s pair.**
#:
#: Phase 3 copied ``public/js/collab/live_form_sync.js``'s constants, which is ordinarily the
#: right instinct — except that **30 s is exactly the Google Cloud load balancer's
#: idle-connection cut**, and the only reason realtime works on this site at all is that
#: socket.io's stock 25 s ``pingInterval`` beats that cut by five seconds. Inheriting the
#: house pair into a subsystem that has no reason to sit on that boundary inherits a
#: five-second margin and buys nothing (ADR §H.3.1).
#:
#: These now live in :mod:`chat.notifications.policy`, next to the suppression rule that
#: reads them, because a TTL in one file and a freshness check in another is a drift that
#: presents as "notifications stopped working" rather than as a mismatch anybody can see.
PRESENCE_TTL_SECONDS = policy.PRESENCE_TTL_SECONDS

#: The client heartbeat interval this TTL is sized against. Exported so the SPA and the
#: server cannot drift: the SPA reads it out of the bootstrap payload rather than hardcoding.
HEARTBEAT_SECONDS = policy.HEARTBEAT_SECONDS

#: A client that has been hidden for longer than this is "away" rather than "online".
AWAY_AFTER_SECONDS = 300

#: Typing is throttled to one call per 3 s per client and expires 5 s after the last refresh
#: on the *receiving* side. Both numbers ship to the client in the bootstrap payload.
TYPING_THROTTLE_SECONDS = 3
TYPING_EXPIRY_SECONDS = 5


# --------------------------------------------------------------------------- typing


@frappe.whitelist(methods=["POST"])
def set_typing(room: str, thread: str | None = None, stopped: Any = 0) -> dict[str, Any]:
	"""Publish a typing indicator to the room's document room. **No database write at all.**

	The whole endpoint is: check membership, publish. There is no row to write, nothing to
	clean up, and nothing that survives a restart — which is exactly right for a signal whose
	meaning expires in five seconds.

	``thread`` scopes the indicator, so "Jane is typing" appears in the thread pane she is
	replying in rather than in the main transcript she is not.

	``stopped`` sends the explicit stop event (on send, on clearing the composer, on blur).
	It is optional in the sense that the receiver's 5 s expiry is the safety net — but
	relying only on the expiry makes indicators feel laggy, which is the whole reason
	somebody asked for typing indicators in the first place.
	"""
	_user, name = require_room(room)
	event = realtime.EVENT_TYPING_STOPPED if cint(stopped) else realtime.EVENT_TYPING
	payload: dict[str, Any] = {"room": name, "user": frappe.session.user}
	if (thread or "").strip():
		payload["thread_root"] = thread.strip()

	realtime.publish_room_event(name, event, payload)
	return {"published": True}


# --------------------------------------------------------------------------- presence


@frappe.whitelist(methods=["POST"])
def heartbeat(
	client_id: str,
	active_room: str | None = None,
	thread: str | None = None,
	focused: Any = 1,
	visibility: str | None = None,
	surface: str | None = None,
) -> dict[str, Any]:
	"""One client's liveness and focus, every ~20 s. Writes one Redis field with a TTL.

	Args:
		client_id: a per-tab identifier the SPA mints once and keeps in ``sessionStorage``.
			**Required**, and the reason the multi-tab union works: without it three tabs
			overwrite one another and the last one to report decides for all of them.
		active_room: the conversation this client is looking at, or ``None``.
		thread: the thread pane open in that conversation, if any.
		focused: ``document.hasFocus()``. Distinct from visibility — a chat tab sitting
			behind a spreadsheet is visible and not focused, and §4.7 says that is *not*
			being read.
		visibility: ``document.visibilityState``.
		surface: ``"app"`` from the SPA, ``"desk"`` from the floating bubble. **Phase 4.**
			It is what makes ADR §H.1's row 5 — "in ERPNext, chat closed" — an observable
			state rather than a wish: the bubble beats from every Desk page with no active
			room, so a record with ``room = None`` distinguishes "at their desk, not in
			chat" from "not in ERPNext at all", which are different reason codes for the
			same outcome and therefore different things to tell an operator.

	Returns the coarse state now published for this user plus the presence of everyone in
	``active_room``, so the SPA's dots refresh on the same beat and need no second call.

	**The client reports; the server decides.** Nothing here suppresses anything — the
	record exists so that :mod:`chat.notifications.policy` can make every suppression
	decision server-side (invariant I6). A client that decided by simply not rendering a
	notification would have made the decision unauditable, and the test for I6 stubs the
	client's suppression out entirely and asserts the server still emits nothing.

	Note what this endpoint does **not** accept: ``focused_changed_at``. It is stamped by
	the store, on receipt, because a client that could supply it could backdate its own blur
	and buy silence for as long as it liked.
	"""
	user = require_session()
	client = (client_id or "").strip()
	if not client:
		frappe.throw(frappe._("A presence heartbeat needs a client id."))

	room = (active_room or "").strip()
	if room:
		# Gated: "which room is this user looking at" is only reportable for a room they can
		# read. Otherwise the focus record is a way to assert presence in someone else's room,
		# and the notifier would then suppress that room's notifications on the strength of it.
		_user, room = require_room(room, user=user)

	# The effective values, not the module constants: an operator may have retuned them, and a
	# client beating on one cadence under a TTL sized for another drops to "absent" between
	# beats and is over-notified permanently. Read once per beat, from the cached Single.
	tuning = settings.load()
	beat_seconds = settings.heartbeat_seconds()

	previous = state_of(user)
	store.record_beat(
		user,
		client,
		room=room or None,
		focused=bool(cint(focused)),
		surface=surface,
		thread=thread,
		visibility=visibility,
		ttl_seconds=tuning.presence_ttl_seconds,
	)

	current = state_of(user)
	if current != previous:
		# Transitions only, never the heartbeat itself. Publishing every beat is a
		# 50-users × 2-per-minute firehose through Redis pub-sub for information that has
		# not changed.
		_publish_presence(user, current, room)

	return {
		"state": current,
		"ttl": tuning.presence_ttl_seconds,
		"heartbeat_seconds": beat_seconds,
		"typing_throttle_seconds": TYPING_THROTTLE_SECONDS,
		"typing_expiry_seconds": TYPING_EXPIRY_SECONDS,
		"room_presence": get_presence(room) if room else {},
	}


@frappe.whitelist(methods=["POST"])
def goodbye(client_id: str) -> dict[str, Any]:
	"""Expire one client's presence early, on ``pagehide``. **An optimisation, not a mechanism.**

	The TTL is what makes presence correct. This exists so that closing a tab deliberately
	looks instant rather than taking 75 seconds, and it is written so that never being called
	changes nothing but the latency.
	"""
	user = require_session()
	client = (client_id or "").strip()
	if not client:
		return {"cleared": False}

	previous = state_of(user)
	if not store.forget(user, client):
		return {"cleared": False}

	current = state_of(user)
	if current != previous:
		_publish_presence(user, current, None)
	return {"cleared": True, "state": current}


@frappe.whitelist()
def get_presence(room: str) -> dict[str, str]:
	"""Coarse presence for every member of one room: ``online`` / ``away`` / ``offline``.

	``offline`` here means "no live ERPNext session", which for a coworker who lives in the
	native Google Chat client is permanently true. The SPA renders that as an explicit
	"not in ERPNext" state rather than as a plain grey dot, because a dot that reads
	"offline" about somebody who is actively typing in Chat is worse than no dot.
	"""
	_user, name = require_room(room)

	from erpnext_enhancements.chat.api.rooms import _members

	return {member["user"]: state_of(member["user"]) for member in _members(name)}


def state_of(user: str) -> str:
	"""The union across one user's clients. Never raises; an unreadable cache is ``offline``."""
	return focus_state(_clients(user), now=int(time.time()))["state"]


def focused_on(user: str, room: str) -> bool:
	"""Whether **any** client of ``user`` has ``room`` open *and* focused.

	The signal decision #3 rule (a) turns on, and the one Phase 4 will read. Phrased as a
	union deliberately: the question is never "is this user's most recent tab focused here",
	it is "is there any client of theirs watching this conversation right now".
	"""
	return focus_state(_clients(user), now=int(time.time()), room=room)["focused_here"]


def focus_state(
	clients: list[dict[str, Any]], *, now: int, room: str | None = None
) -> dict[str, Any]:
	"""**Pure.** Collapse a user's client records into one coarse state and a focus answer.

	No Frappe, no Redis, no clock — ``now`` is passed in. That is what makes the multi-tab
	union testable in the bench-free CI tier, which is the only tier in this repo with
	automatic regression protection.

	The rules, in order:

	* a client whose record is older than :data:`PRESENCE_TTL_SECONDS` does not count. Redis
	  would normally have expired it; the check is here as well because the hash's TTL is
	  per-hash, so one live tab keeps a dead sibling's field alive.
	* ``online`` if any live client is visible or focused;
	* ``away`` if every live client has been hidden for longer than
	  :data:`AWAY_AFTER_SECONDS`;
	* ``offline`` if no client is live.
	* ``focused_here`` is true only if some live client has ``room`` open **and** reports
	  ``focused``. Room open but window blurred is not focused; that is the distinction
	  §4.7 draws and the reason read receipts do not fire behind a spreadsheet.
	"""
	live = [c for c in clients if now - cint(c.get("ts")) <= PRESENCE_TTL_SECONDS]
	if not live:
		return {"state": "offline", "focused_here": False, "clients": 0}

	focused_here = any(
		bool(cint(c.get("focused"))) and (c.get("room") or None) == room and room is not None
		for c in live
	)

	visible = [c for c in live if (c.get("visibility") or "visible") != "hidden"]
	if visible or any(cint(c.get("focused")) for c in live):
		state = "online"
	elif all(now - cint(c.get("ts")) >= AWAY_AFTER_SECONDS for c in live):
		state = "away"
	else:
		state = "away"

	return {"state": state, "focused_here": focused_here, "clients": len(live)}


def _clients(user: str) -> list[dict[str, Any]]:
	"""Every client record for one user. ``[]`` on any cache failure.

	**The dot fails to "offline"; the notifier does not.** That asymmetry is deliberate and
	it is the one thing to understand about this function. A grey dot during a Redis outage
	is a cosmetic wrong answer somebody shrugs at. Applying the same "assume nothing is
	there" to *suppression* would be silent message loss — so the notifier calls
	:func:`chat.notifications.presence.clients_for`, which returns an explicit
	store-unavailable flag and fails toward notifying. Two readers, two failure directions,
	on purpose.
	"""
	return store.raw_clients(user)


def _user_key(user: str) -> str:
	return store.user_key(user)


def _publish_presence(user: str, state: str, room: str | None) -> None:
	"""Announce a presence **transition** to the rooms that can see it.

	Fan-out is per room rather than site-wide: presence is confidential in the same way the
	room list is (it tells you who somebody is talking to, by telling you which rooms they
	show up in), and a site-wide broadcast would hand every user the online state of every
	other user regardless of whether they share a conversation.

	Bounded to the user's own rooms, which at this company is a handful. If that ever stops
	being true the answer is to publish only to the room the user is active in, not to widen
	the fan-out.
	"""
	from erpnext_enhancements.chat.permissions import visible_room_names

	payload = {"user": user, "state": state, "ts": int(time.time())}
	targets = [room] if room else visible_room_names(user)
	for target in targets:
		if not target:
			continue
		try:
			realtime.publish_room_event(target, realtime.EVENT_PRESENCE, dict(payload))
		except Exception:
			# One unpublishable room must not stop the others.
			continue
