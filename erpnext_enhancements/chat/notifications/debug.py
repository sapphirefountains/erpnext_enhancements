# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

""""Why didn't Jane get that?" — the one question this system cannot otherwise answer.

Every failure mode in a notification system is invisible by construction. Nobody reports the
ping they did not get, there is no error when a message is correctly suppressed, and a
correctly-suppressed message and a silently-broken one look identical from every side: the
sender sees a delivered message, the recipient sees nothing, and the server logs a success.

So the decision is reconstructible on demand, for a named person and a named room, with the
reason code and the presence records it was made from — **including the records that were
rejected as stale**, because "there were three tabs and all of them had expired" and "there
were no tabs" are different problems with different fixes, and they produce the same outcome.

This is also what the live production walkthrough shows beside each row of the matrix, so a
reviewer sees the decision *and* its reason rather than only whether a phone buzzed.

**It reads presence and settings. It never sends anything and never writes.** Running it
during an incident cannot make the incident worse.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

from typing import Any

import frappe

from erpnext_enhancements.chat.notifications import policy
from erpnext_enhancements.chat.notifications import presence as presence_store
from erpnext_enhancements.chat.notifications import settings as notification_settings


def explain(user: str, room: str) -> dict[str, Any]:
	"""Reconstruct the notification decision for one person and one room, right now.

	Returns the tunables in force, every presence record with a live/stale verdict per record,
	the classified state, and the decision each of the two message kinds would produce — an
	ordinary message and one that mentions them — because a report of "notifications are
	broken" is very often "mentions work and ordinary messages do not", or the reverse, and
	asking about only one of them answers the wrong question.
	"""
	tuning = notification_settings.load()
	now = presence_store.now_epoch()

	clients, store_available = presence_store.clients_for(
		user, now=now, ttl_seconds=tuning.presence_ttl_seconds
	)
	state = policy.classify_presence(
		clients, room=room, now=now, policy=tuning, store_available=store_available
	)

	recipient = policy.Recipient(
		is_author=False,
		is_mentioned=False,
		is_muted=_is_muted(user, room),
		notifications_enabled=_notifications_enabled(user),
	)
	mentioned = policy.Recipient(
		is_author=recipient.is_author,
		is_mentioned=True,
		is_muted=recipient.is_muted,
		notifications_enabled=recipient.notifications_enabled,
	)

	return {
		"user": user,
		"room": room,
		"now": now,
		"policy": {
			"presence_ttl_seconds": tuning.presence_ttl_seconds,
			"blur_grace_seconds": tuning.blur_grace_seconds,
			"mention_beats_mute": tuning.mention_beats_mute,
		},
		"presence": {
			"store_available": store_available,
			# Every record, not only the live ones. A row of expired tabs is the single most
			# common cause of "it suddenly started notifying me about everything", and it is
			# invisible if the stale ones are filtered out before anybody sees them.
			"clients": _client_report(user, now, tuning.presence_ttl_seconds),
			"live_clients": len(clients),
			"state": state.value,
		},
		"recipient": {
			"is_muted": recipient.is_muted,
			"notifications_enabled": recipient.notifications_enabled,
		},
		"would_notify": {
			"ordinary_message": _decision_report(recipient, state, tuning),
			"a_mention_of_them": _decision_report(mentioned, state, tuning),
		},
		"push": _push_report(user),
	}


def _client_report(user: str, now: int, ttl: int) -> list[dict[str, Any]]:
	"""Every stored presence record, with why it counted or did not."""
	out: list[dict[str, Any]] = []
	for record in presence_store.raw_clients(user):
		seen = int(record.get("ts") or 0)
		age = now - seen if seen else None
		out.append(
			{
				"room": record.get("room"),
				"focused": bool(record.get("focused")),
				"surface": record.get("surface") or "app",
				"visibility": record.get("visibility"),
				"age_seconds": age,
				"live": bool(seen and age is not None and age <= ttl),
				"blurred_for_seconds": (now - int(record.get("fts") or 0)) if record.get("fts") else None,
			}
		)
	return out


def _decision_report(
	recipient: policy.Recipient, state: policy.Reason, tuning: policy.Policy
) -> dict[str, Any]:
	decision = policy.decide(recipient=recipient, presence=state, policy=tuning)
	return {
		"reason": decision.reason.value,
		"bell": decision.bell,
		"push": decision.push,
		"room_indicator": decision.room_indicator,
		"counter": decision.counter,
		"auto_read": decision.auto_read,
	}


def _push_report(user: str) -> dict[str, Any]:
	"""Whether a push could physically be delivered, separately from whether one is wanted.

	The two are constantly confused during an incident: "the policy says push" and "there is a
	device to push to" are different facts, and somebody with notifications denied in their
	browser has zero subscriptions while the decision quite correctly says push.
	"""
	try:
		from erpnext_enhancements.chat.notifications.webpush import subscriptions, vapid

		return {
			"vapid_configured": vapid.is_configured(),
			"active_subscriptions": len(subscriptions.active_for(user)),
		}
	except Exception as exc:
		return {"vapid_configured": False, "active_subscriptions": 0, "error": str(exc)[:200]}


def _is_muted(user: str, room: str) -> bool:
	try:
		row = frappe.db.get_value(
			"Chat Room Member",
			{"room": room, "user": user},
			["notification_mode", "muted_until"],
			as_dict=True,
		)
	except Exception:
		return False
	if not row:
		return False

	from erpnext_enhancements.chat.notifications.fanout import _is_muted as rule

	return rule(dict(row))


def _notifications_enabled(user: str) -> bool:
	from erpnext_enhancements.chat.notifications.fanout import _notifications_enabled as rule

	return rule(user)
