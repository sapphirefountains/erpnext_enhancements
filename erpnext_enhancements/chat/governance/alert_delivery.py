# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The ops-space post, in a background job. A separate module, and the split is structural.

--------------------------------------------------------------------------------------
Why this is not a function in `alerts.py`
--------------------------------------------------------------------------------------

`tests/test_chat_guardrails.py` asserts that **no `doc_events` handler reaches the Google
transport**, transitively, through any import. It caught this: `alerts.py` importing
`chat.api.compose` created a static path

    chat.permissions -> chat.audit -> chat.governance.alerts
        -> chat.api.compose -> chat.sync.attachments -> chat.gchat.client

which put `readstate.announce_unread` and `role_grants.on_user_roles_changed` — both
`doc_events` handlers — one import away from the transport.

**The guardrail was right, and moving the import here is not a way around it.** Delivering an
alert inline was wrong for three reasons the static walk was standing in for:

* **A caller's rollback would take the alert with it.** The post is a `Chat Message` insert in
  whatever transaction raised the alert; if that transaction rolls back — which is exactly
  what a lot of alerting conditions end in — the message vanishes too. Alerting that
  disappears when things go wrong is alerting that works only when it is not needed. (The
  alert *row* has the same exposure, which is why step 1 of the path is a log-file line.)
* **`frappe.set_user` inside somebody else's transaction is dangerous.** The post has to run
  as a room member; doing that in the middle of an arbitrary caller's save means a failure
  between the two `set_user` calls leaves the rest of that save running as the wrong person.
* **Latency lands on whoever noticed the problem.** The save that detected a stale digest
  should not also pay for telling someone about it.

So delivery is enqueued with `enqueue_after_commit=True` and runs here, in a worker, with its
own transaction. `alerts.py` names this module as a dotted string and imports nothing from it,
so the static edge is gone because the runtime edge is gone.
"""

from __future__ import annotations

import frappe

ALERT_DOCTYPE = "Chat Ops Alert"


def post_to_ops_space(alert: str = "", body: str = "") -> dict[str, object]:
	"""Post one alert into the configured operations room, as the configured user.

	Runs in a worker. Returns a result dict rather than raising, so a delivery failure is
	recorded against the alert instead of becoming a failed job nobody reads — a background
	job that dies while reporting an incident adds a second incident.

	**No `ignore_permissions`, deliberately.** `send_message` asserts membership, so this
	needs an identity that is genuinely in the room. The alternative — a write-into-any-room
	primitive with a comment saying it is only for alerts — is not a property anything
	enforces, and not one that survives the next person who needs to post something.
	"""
	from erpnext_enhancements.chat.api import compose

	room = _setting("alert_ops_room")
	poster = _setting("alert_post_as")
	if not room or not poster or not body:
		return {"ok": False, "reason": "the operations space channel is not configured"}

	original = frappe.session.user
	try:
		frappe.set_user(poster)
		compose.send_message(room=room, text=str(body)[:3000])
	except Exception as exc:  # noqa: BLE001 - recorded on the alert, never re-raised
		_record_failure(alert, f"space: {exc.__class__.__name__}")
		return {"ok": False, "reason": exc.__class__.__name__}
	finally:
		# In a `finally` because `set_user` is global: a worker that alerts and then carries
		# on as somebody else is a worse bug than the one being reported.
		frappe.set_user(original)

	return {"ok": True, "room": room}


def _setting(field: str) -> str:
	try:
		return str(frappe.db.get_single_value("Chat Settings", field) or "").strip()
	except Exception:  # noqa: BLE001
		return ""


def _record_failure(alert: str, message: str) -> None:
	if not alert:
		return
	try:
		frappe.db.set_value(ALERT_DOCTYPE, alert, {"delivery_error": message[:500]}, update_modified=False)
	except Exception:  # noqa: BLE001
		pass
