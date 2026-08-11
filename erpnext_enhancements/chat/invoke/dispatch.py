# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Acknowledge, dedupe, enqueue. **Never answer inline.**

Google's interaction webhook has a **hard 30-second deadline**. Retrieval plus a model turn
does not fit inside it reliably, and a handler that tries works in development and times out
under load — where the symptom is not an error but a mention that is silently never answered.

So both origins land here, this returns immediately, and the answer is posted asynchronously
in-thread.

--------------------------------------------------------------------------------------
There is no second world-reachable endpoint, and that is a deliberate divergence
--------------------------------------------------------------------------------------

Appendix B specifies ``chat/invoke/webhook.py`` as a new ``allow_guest`` endpoint with its own
JWT verification. It is not built. ``chat/gchat/webhook.py`` already **is** that endpoint — it
verifies signature, issuer, byte-exact audience and expiry before any body parsing and before
any database access, and its path is baked into the JWT audience Google mints against.

A second world-reachable endpoint would mean a second copy of that verifier. Two copies of an
authentication boundary is one that drifts, and the copy that drifts is a world-reachable open
relay. So the existing endpoint gained a dispatch call and this module is the dispatch.

--------------------------------------------------------------------------------------
The enqueue is not the delivery guarantee
--------------------------------------------------------------------------------------

The production deploy runs ``FLUSHDB`` against the queue Redis, Frappe v16 wires no RQ retries,
and a worker can be SIGKILLed mid-call. A queued turn is therefore *lost* by an ordinary
successful release, silently, because the enqueue already returned.

What makes that survivable is that the invocation log row is written **by the handler, first**,
and that an unanswered mention is re-driven by the sweeper. A dispatcher that treated the
enqueue as the guarantee would lose exactly the turns that arrive during a deploy.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint

from erpnext_enhancements.chat.invoke import normalize
from erpnext_enhancements.chat.invoke.envelope import Envelope

#: Where the turn runs. ``long`` rather than ``default``: a model turn is tens of seconds and
#: the default queue is shared with the relay, whose whole design depends on draining promptly.
QUEUE: str = "long"

HANDLER_PATH: str = "erpnext_enhancements.chat.invoke.handler.run"


def dispatch_chat_interaction(payload: dict[str, Any]) -> dict[str, Any]:
	"""Called by the verified Google Chat webhook. Returns what Chat should see.

	Returns an **empty object** on every path, including refusal. Google renders a
	synchronous body as a message in the space, so an error object here would post the
	system's internal state into a room full of coworkers — and a refusal that is genuinely
	worth saying is said in-thread by the handler, attributed to the bot, where it belongs.
	"""
	try:
		envelope, origin = normalize.from_chat_interaction(payload)
	except normalize.NotAMention:
		return {}
	except Exception:
		frappe.log_error("A verified Chat interaction event could not be normalised.", "Chat Triton")
		return {}

	_enqueue(envelope, origin)
	return {}


def dispatch_spa_message(message_name: str) -> str | None:
	"""Called after a message composed in ERPNext is stored. Returns the request id.

	Deliberately **not** a document event. A ``doc_events`` hook runs inside the inserting
	transaction on a web worker, and invariant I1 forbids any chat write path reaching an
	external service from there — the enqueue itself is safe, but a hook is the place the next
	person adds the call that is not. The composer calls this explicitly, after commit.
	"""
	if not normalize.mentions_triton(message_name):
		return None
	try:
		envelope, origin = normalize.from_spa_message(message_name)
	except normalize.NotAMention:
		return None
	_enqueue(envelope, origin)
	return envelope.request_id


def _enqueue(envelope: Envelope, origin: str) -> None:
	"""Queue the turn, unless this exact turn is already known.

	Dedupe is on the **invocation log row**, not on ``frappe.enqueue(deduplicate=True)``, and
	the difference matters: that flag drops a new enqueue while an existing job is ``QUEUED``
	*or* ``STARTED``, so a second mention arriving while the first is running would be silently
	swallowed. Here, two different questions are two different ``request_id`` values and both
	run; the same question redelivered is one row and one answer.
	"""
	if not _triton_enabled():
		return
	if _already_seen(envelope.request_id):
		return
	try:
		frappe.enqueue(
			HANDLER_PATH,
			queue=QUEUE,
			enqueue_after_commit=True,
			**envelope.as_job_kwargs(),
			origin=origin,
		)
	except Exception:
		frappe.log_error(f"Could not enqueue the @triton turn {envelope.request_id}.", "Chat Triton")


def _already_seen(request_id: str) -> bool:
	if not request_id:
		return False
	try:
		return bool(frappe.db.exists("Triton Invocation Log", {"request_id": request_id}))
	except Exception:
		# Fail OPEN here, unlike everywhere else in this feature, and the asymmetry is
		# deliberate: the cost of a duplicate answer is a coworker seeing the same reply
		# twice, while the cost of a swallowed turn is a mention that is never answered and
		# that nobody can distinguish from Triton being ignored.
		return False


def _triton_enabled() -> bool:
	"""Both switches. ``pause_triton`` is the incident control, ``triton_enabled`` the rollout
	one, and either being off means the mention is simply not answered — which is the correct
	behaviour for a feature that ships dormant."""
	try:
		if not cint(frappe.db.get_single_value("Chat Settings", "enabled")):
			return False
		if cint(frappe.db.get_single_value("Chat Settings", "pause_triton")):
			return False
		return bool(cint(frappe.db.get_single_value("Chat Settings", "triton_enabled")))
	except Exception:
		return False
