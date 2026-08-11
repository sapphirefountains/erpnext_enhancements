# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The presence store — a heartbeat with an expiry, and deliberately nothing more.

**There is no ``connect`` and no ``disconnect``. There is one verb: beat.** That is the
whole design, and it is the single most important sentence in this module.

A browser crash, a ``SIGKILL``, a closed laptop lid and a network partition all produce
**no disconnect event**. A store that set ``online = 1`` on connect and cleared it on
disconnect would leave that person permanently present-and-focused, and the notification
policy would then silently suppress every message to them — forever, until they next opened
the app. Users report that as "chat stopped working", never as a notification bug, and it
is the single most common way presence-based suppression turns into permanent message loss.
So: absence of a fresh record **is** absence, nothing here ever reads a boolean, and
:func:`forget` — the ``pagehide`` goodbye — is an optimisation whose absence changes latency
and nothing else.

--------------------------------------------------------------------------------------
One hash per user, one field per tab, and the TTL on the key
--------------------------------------------------------------------------------------

The suppression rule is quantified over a person's tabs — *"**no** client of this user has
that room focused"* — so the store has to be able to enumerate one user's clients without a
``SCAN``. A hash keyed on the user with a field per tab does that in one ``hgetall``.

The cost is that Redis 7.0.15 (production, measured) has no per-field expiry — ``HEXPIRE``
arrived in 7.4 — so the TTL sits on the **whole key** and one live tab keeps a dead
sibling's field alive past its own lifetime. **Stale fields are therefore filtered on
read**, in :func:`clients_for`, and that filter is not belt-and-braces: it is the half of
this design that makes it correct. Do not "optimise" it away on the grounds that Redis
expires things.

--------------------------------------------------------------------------------------
``focused_changed_at`` is stamped here, by the server, and that is a security property
--------------------------------------------------------------------------------------

The blur grace asks "how long have they been away from the window", which needs a moment to
measure from. If the client supplied it, a client could backdate its own blur and buy
silence indefinitely. Stamping it on receipt bounds the damage to self-harm: a hostile or
wedged client can suppress **its own** notifications and nobody else's, and even that only
until its record ages out.

--------------------------------------------------------------------------------------
An unreadable store is not an empty store
--------------------------------------------------------------------------------------

Both notify — see :mod:`~erpnext_enhancements.chat.notifications.policy` — but they are
different operational events, and the reason code is what tells an operator that Redis is
down rather than that everybody went home. So :func:`clients_for` returns the records **and**
a flag, and never conflates a raised exception with an empty hash.

Every production deploy runs ``redis-cli -p 13000 FLUSHDB``, so this keyspace is wiped on
every release by design. The degradation is "everyone shows absent for one heartbeat, and is
therefore over-notified for one heartbeat", never corruption — the same posture
``training/progress.py`` takes, for the same reason. Clients re-beat on socket reconnect
rather than waiting out their timer, which is what bounds that window to reconnect latency.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

import time
from typing import Any, Final

import frappe
from frappe.utils import cint

from erpnext_enhancements.chat.notifications import policy

#: One hash per user. The namespace is explicit because ``redis_cache`` and the socket.io
#: adapter are the same instance and the same DB 0 on this host, and because the house
#: convention is a hand-written prefix (``training:attempt:``, ``triton_user_token::``).
#:
#: Unchanged from what Phase 3 shipped. Renaming it would strand nothing — a deploy flushes
#: the keyspace regardless — but it would put two spellings of the same fact in the codebase
#: for one release, and somebody would read the wrong one.
KEY_PREFIX: Final[str] = "ee_chat_presence"

#: Fields written per client. Named so that :func:`clients_for` and the endpoint cannot drift
#: about the shape of a record neither of them owns alone.
_FIELD_ROOM: Final[str] = "room"
_FIELD_THREAD: Final[str] = "thread"
_FIELD_FOCUSED: Final[str] = "focused"
_FIELD_SURFACE: Final[str] = "surface"
_FIELD_VISIBILITY: Final[str] = "visibility"
_FIELD_SEEN: Final[str] = "ts"
_FIELD_FOCUS_CHANGED: Final[str] = "fts"


def user_key(user: str) -> str:
	"""``ee_chat_presence:<user>``. One function so the writer and the reader agree."""
	return f"{KEY_PREFIX}:{user}"


def now_epoch() -> int:
	"""The clock, in exactly one place.

	The policy module takes ``now`` as a parameter precisely so that it never calls this, and
	every caller in a single fan-out reads it **once** and passes the same value down. Two
	recipients of one message deciding against two different clocks is a bug that reproduces
	about as often as a heartbeat lands on a second boundary.
	"""
	return int(time.time())


def record_beat(
	user: str,
	client_id: str,
	*,
	room: str | None,
	focused: bool,
	surface: str | None = None,
	thread: str | None = None,
	visibility: str | None = None,
	now: int | None = None,
	ttl_seconds: int | None = None,
) -> dict[str, Any]:
	"""Write one tab's report and refresh the key's expiry. Returns the record as stored.

	``focused_changed_at`` is carried forward while ``focused`` is unchanged and re-stamped
	to ``now`` the moment it flips. That comparison is why this is a read-then-write rather
	than a blind ``hset``: without the previous value there is no transition to detect, and a
	client that re-stamped on every beat would never accumulate any blur at all — the grace
	would never expire and a tab left open on Friday would suppress all weekend.

	**Never raises.** A cache outage must not fail the request carrying the beat, or a Redis
	blip becomes a chat outage. It returns the record it *would* have stored, so the endpoint
	can still answer the client something coherent.
	"""
	stamp = now_epoch() if now is None else int(now)
	ttl = policy.PRESENCE_TTL_SECONDS if ttl_seconds is None else int(ttl_seconds)
	is_focused = 1 if focused else 0

	previous = _record(user, client_id)
	if previous and cint(previous.get(_FIELD_FOCUSED)) == is_focused:
		changed_at = cint(previous.get(_FIELD_FOCUS_CHANGED)) or stamp
	else:
		changed_at = stamp

	record: dict[str, Any] = {
		_FIELD_ROOM: (room or "").strip() or None,
		_FIELD_THREAD: (thread or "").strip() or None,
		_FIELD_FOCUSED: is_focused,
		_FIELD_SURFACE: _surface(surface),
		_FIELD_VISIBILITY: (visibility or "visible").strip()[:16],
		_FIELD_SEEN: stamp,
		_FIELD_FOCUS_CHANGED: changed_at,
	}

	key = user_key(user)
	try:
		cache = frappe.cache()
		cache.hset(key, client_id, record)
		# The expiry is re-applied on every beat and covers the whole hash. A tab that stops
		# beating does not delete its field — it simply stops refreshing the key, and the key
		# takes every field with it once the last tab goes quiet.
		cache.expire(key, ttl)
	except Exception:
		frappe.log_error(title="chat presence write failed", message=f"user={user}")

	return record


def forget(user: str, client_id: str) -> bool:
	"""Drop one tab's record early, on ``pagehide``. **An optimisation, not a mechanism.**

	Written so that never being called changes nothing but how quickly a deliberately closed
	tab stops showing as present. If correctness ever comes to depend on this being called,
	the design has regressed to a connect/disconnect flag and the crash case is broken again.
	"""
	if not user or not client_id:
		return False
	try:
		frappe.cache().hdel(user_key(user), client_id)
		return True
	except Exception:
		return False


def clients_for(
	user: str, *, now: int | None = None, ttl_seconds: int | None = None
) -> tuple[list[policy.ClientPresence], bool]:
	"""Every believable tab of one user, and whether the store could be read at all.

	Returns ``(clients, store_available)``. The flag is the point: an empty list with
	``True`` means "they are not in ERPNext", and an empty list with ``False`` means "Redis
	did not answer". Both notify, and the policy gives them different reason codes so an
	operator reading the debug output can tell an outage from a quiet afternoon.

	Stale records are filtered here — see the module docstring on why the key-level TTL is
	not sufficient on its own.
	"""
	stamp = now_epoch() if now is None else int(now)
	ttl = policy.PRESENCE_TTL_SECONDS if ttl_seconds is None else int(ttl_seconds)

	if not user or user == "Guest":
		# Not an outage: Guest has no session to be present in. Reporting "available" keeps a
		# signed-out sender from being classified as a Redis failure in the debug output.
		return [], True

	try:
		raw = frappe.cache().hgetall(user_key(user)) or {}
	except Exception:
		return [], False

	clients: list[policy.ClientPresence] = []
	for value in raw.values():
		record = _coerce(value)
		if record is None:
			continue
		client = policy.ClientPresence(
			room=(record.get(_FIELD_ROOM) or None),
			focused=bool(cint(record.get(_FIELD_FOCUSED))),
			surface=_surface_enum(record.get(_FIELD_SURFACE)),
			last_seen=cint(record.get(_FIELD_SEEN)),
			focused_changed_at=cint(record.get(_FIELD_FOCUS_CHANGED)),
		)
		if client.is_live(now=stamp, ttl=ttl):
			clients.append(client)

	return clients, True


def raw_clients(user: str) -> list[dict[str, Any]]:
	"""Every record for one user, unfiltered, as plain dicts.

	For the presence *dot*, which wants "online / away / offline" rather than a suppression
	answer, and for :mod:`~erpnext_enhancements.chat.notifications.debug`, which has to show
	an operator the records that were **rejected** as much as the ones that counted.
	"""
	if not user or user == "Guest":
		return []
	try:
		raw = frappe.cache().hgetall(user_key(user)) or {}
	except Exception:
		return []
	return [record for record in (_coerce(value) for value in raw.values()) if record is not None]


def _record(user: str, client_id: str) -> dict[str, Any] | None:
	"""One tab's stored record, or ``None``. Used only to detect a focus transition."""
	try:
		return _coerce(frappe.cache().hget(user_key(user), client_id))
	except Exception:
		return None


def _coerce(value: Any) -> dict[str, Any] | None:
	"""Whatever came back out of the cache, as a dict — or ``None`` if it is not one.

	``frappe.cache()``'s hash helpers pickle their values, so a dict written by
	:func:`record_beat` comes back as a dict. Anything else is a record written by an older
	release, by a different app, or by a hand-run debug session; discarding it silently is
	right, because a malformed presence record must never be able to raise on the read path
	that decides whether to notify.
	"""
	return dict(value) if isinstance(value, dict) else None


def _surface(value: str | None) -> str:
	"""Normalise the reported surface to one of the two we know, defaulting to the app."""
	candidate = (value or "").strip().lower()
	return candidate if candidate in {policy.Surface.APP.value, policy.Surface.DESK.value} else (
		policy.Surface.APP.value
	)


def _surface_enum(value: Any) -> policy.Surface:
	try:
		return policy.Surface(_surface(value if isinstance(value, str) else None))
	except ValueError:  # pragma: no cover - _surface already constrains the domain
		return policy.Surface.APP
