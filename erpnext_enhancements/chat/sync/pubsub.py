# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Pub/Sub consumer — a **bounded synchronous pull, driven by a one-minute cron**.

Why a cron and not a daemon
---------------------------
ADR §G.4.2 names a streaming-pull worker as the ideal and a bounded synchronous pull as the
shipping-first fallback, priced at up to a minute of inbound latency. This deployment takes
the fallback, and the reason is structural rather than preferential: **there is no Procfile in
this repository.** The bench's Procfile is generated on the VM by ``bench`` and systemd runs
``honcho start`` against it; neither file is under version control here, so a supervised
long-running worker is not a change this repo is able to make. A ``scheduler_events`` cron
entry is.

Frappe evaluates cron in the site's timezone and takes the standard five-field form, and
``"* * * * *"`` is available — other entries in ``hooks.py`` already use this shape::

    scheduler_events = {
        "cron": {
            "* * * * *": [
                "erpnext_enhancements.chat.sync.pubsub.pull_inbound_events",
            ]
        }
    }

Each tick takes a Redis lock, pulls until its wall-clock budget is spent (~50 s, comfortably
inside the minute), and returns. The next minute re-arms it. The lock is what keeps two ticks —
or two benches — from pulling the same subscription concurrently and splitting one stream of
events between two workers.

The ordering that makes this correct, and the two ways to get it wrong
----------------------------------------------------------------------
For every delivery: **write the row, COMMIT, then ack, then enqueue processing.**

* **Ack before the row is committed** and a worker crash — or a deploy restarting honcho
  mid-flight, which happens on every release — loses the event outright. Pub/Sub has been told
  it was handled; nothing anywhere remembers it existed.
* **Do the real work before acking** and every slow message is redelivered while it is still
  being processed, because the ack deadline expires. That is a redelivery storm, and it grows
  with load, which is exactly when it hurts.

The window this ordering leaves open is *committed but not acked*, which produces a duplicate
delivery — and a duplicate is absorbed structurally by ``unique(pubsub_message_id)``. That is
the correct direction to be wrong in.

A 50-second job **will** be killed mid-flight by a deploy. That is fine precisely because the
ack comes after the commit: the deliveries already written are safe, and everything else is
redelivered.

Dedupe is the index, never a check
-----------------------------------
``Chat Inbound Event.pubsub_message_id`` carries ``unique: 1``. The insert is *attempted* and
the collision is treated as "already ingested". Never ``SELECT``-then-``INSERT``: that is a
TOCTOU bug at exactly the moment it matters — two workers, one redelivered event — and the
window between the check and the insert is where the duplicate row gets created.

**Catch both exception classes.** ``frappe.DuplicateEntryError`` is raised only for a
primary-key collision; a non-PK unique index raises ``frappe.UniqueValidationError``, and the
two share no base beyond ``Exception``. ADR §G.3.1 names only the first and is wrong
(``PHASE2_VERIFIED.md`` §1.1). The catch also has to happen inside
``frappe.database.database.savepoint``, because a failed statement can poison the surrounding
transaction — which here would mean the *rest of the batch* silently failing to commit.

This module names a Google host
-------------------------------
``pubsub.googleapis.com`` — a different service from both the Chat API and the Workspace
Events API. ``tests/test_chat_guardrails.py`` enforces "the modules that speak HTTP to Google
are a short, named list", so this module is added to ``TRANSPORT_MODULES`` and the host to
``PERMITTED_GOOGLE_HOSTS`` rather than being allowed to slip past a check that only watches
hosts it already knows about.

Indentation is tabs, per ``CLAUDE.md`` and the Frappe convention this package follows.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from typing import Any, Final

import frappe

from erpnext_enhancements.chat.doctype.chat_inbound_event.chat_inbound_event import (
	STATUS_RECEIVED,
	TRANSPORT_WORKSPACE_EVENT,
)
from erpnext_enhancements.chat.sync import inbound

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

#: The Pub/Sub REST surface. Not the Chat API and not the Workspace Events API — three
#: different services, and this is the third.
PUBSUB_HOST: Final[str] = "https://pubsub.googleapis.com"
PUBSUB_VERSION: Final[str] = "v1"

#: Wall-clock budget for one tick. Ten seconds of headroom inside the minute, so a slow final
#: HTTP round trip cannot make one tick overlap the next — the lock would refuse the overlap
#: anyway, which would silently halve the poll rate.
DEFAULT_BUDGET_SECONDS: Final[int] = 50

#: ``maxMessages`` per pull. Pub/Sub caps a synchronous pull at 1,000; 100 keeps one batch's
#: worth of row writes inside a single short commit, so a kill mid-tick loses at most one
#: batch's worth of un-acked (therefore redelivered) work.
DEFAULT_MAX_MESSAGES: Final[int] = 100

#: How long the tick lock is held. Longer than the budget so a hard-killed worker's lock still
#: expires on its own; a lock with no expiry is a stuck puller nobody can see.
LOCK_TTL_SECONDS: Final[int] = DEFAULT_BUDGET_SECONDS + 20

#: ``projects/{project}/subscriptions/{subscription}``. Validated rather than trusted: the
#: value is operator-entered in ``Chat Settings``, and a malformed one produces a 404 from a URL
#: that reads fine in a log line.
_SUBSCRIPTION_RE: Final[re.Pattern[str]] = re.compile(
	r"\Aprojects/[a-z0-9][a-z0-9-]{4,28}[a-z0-9]/subscriptions/[A-Za-z0-9][A-Za-z0-9._~%+-]{2,254}\Z"
)


class PubSubError(frappe.ValidationError):
	"""A Pub/Sub call this module refuses to interpret as success.

	Carries a status and a short reason and **never** a response body: a Pub/Sub payload is a
	base64 blob of Chat content and an error frame can hold the bearer token.
	"""


# --------------------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------------------


def _settings() -> Any:
	try:
		return frappe.get_cached_doc("Chat Settings")
	except Exception:
		return None


def _setting(fieldname: str, default: Any = None) -> Any:
	"""``getattr(obj, field, None) or default`` per house rule — these reads run during migrate."""
	raw = getattr(_settings(), fieldname, None)
	return default if raw in (None, "") else raw


def _setting_int(fieldname: str, default: int) -> int:
	try:
		return int(_setting(fieldname, default))
	except (TypeError, ValueError):
		return int(default)


def events_subscription() -> str:
	"""``Chat Settings.pubsub_events_subscription``, validated, with the one live-config trap.

	**Refuses to run when the two subscription fields hold the same value.** On 2026-08-09
	production had ``pubsub_interaction_subscription`` set to the *same* subscription as
	``pubsub_events_subscription``. Nothing read it, so nothing was broken — but two consumers
	on one subscription split the stream at random between two pipelines and lose messages with
	nothing in any log, which is the single hardest inbound failure to diagnose. Refusing here
	is cheap; the ``Chat Settings`` save-time validation that stops it being entered at all is a
	separate change on that DocType.
	"""
	name = str(_setting("pubsub_events_subscription", "") or "").strip()
	if not name:
		raise PubSubError(
			"Chat Settings.pubsub_events_subscription is empty; there is no subscription to pull."
		)
	if not _SUBSCRIPTION_RE.match(name):
		raise PubSubError(
			"Chat Settings.pubsub_events_subscription must be "
			"'projects/{project}/subscriptions/{subscription}'."
		)

	interaction = str(_setting("pubsub_interaction_subscription", "") or "").strip()
	if interaction and interaction == name:
		raise PubSubError(
			"Chat Settings names the SAME subscription for events and interactions. Two consumers "
			"on one subscription split the stream at random and lose messages with nothing in any "
			"log. Clear pubsub_interaction_subscription or point it at its own subscription."
		)
	return name


# --------------------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------------------


def _access_token() -> str:
	"""A token for the **VM's own** service account, which is the Pub/Sub subscriber.

	Not a DWD token. The subscription lives in our project and is pulled by the machine, not on
	behalf of any coworker — the per-coworker identities exist to *create* the Workspace Events
	subscriptions (shape B), not to drain the topic they publish to.

	``auth`` owns every credential path in this app; this reaches for a public alias first and
	falls back to the existing private helper so that adding the alias is a one-line change in
	the module that owns tokens rather than a second token path in this one.
	"""
	from erpnext_enhancements.chat.gchat import auth

	timeout = int(_setting_int("http_timeout_seconds", 30))
	getter = getattr(auth, "get_adc_access_token", None) or getattr(auth, "_adc_access_token", None)
	if not callable(getter):
		raise PubSubError("chat.gchat.auth exposes no Application Default Credentials token helper.")
	try:
		return str(getter(timeout))
	except Exception as exc:
		# `from None`: the frames below hold credential material.
		raise PubSubError(f"could not mint an ADC token for Pub/Sub: {type(exc).__name__}") from None


def _post(url: str, body: Mapping[str, Any], *, token: str, transport: Any, timeout: float) -> Any:
	"""One POST. ``transport`` is the injection point; ``None`` means ``requests``.

	Imported inside the function so this module stays importable on a runner with no
	``requests``, the same discipline ``gchat/client.py`` follows.
	"""
	if transport is None:
		import requests

		transport = requests

	return transport.request(
		"POST",
		url,
		json=dict(body),
		headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
		timeout=timeout,
	)


def _decode(response: Any, *, what: str) -> dict[str, Any]:
	"""Status check plus JSON decode. Raises ``PubSubError`` carrying **no body**."""
	status = int(getattr(response, "status_code", 0) or 0)
	if not (200 <= status < 300):
		raise PubSubError(f"Pub/Sub {what} returned HTTP {status}") from None
	try:
		payload = response.json()
	except Exception:
		raise PubSubError(f"Pub/Sub {what} returned a body that is not JSON") from None
	return dict(payload or {})


def pull(subscription: str, *, max_messages: int, transport: Any, token: str, timeout: float) -> list[dict]:
	"""``subscriptions.pull`` — one synchronous batch. ``[]`` means the topic is quiet.

	``returnImmediately`` is deliberately **not** sent: it is documented as deprecated and its
	behaviour under load is to return empty even when messages are available, which would turn
	a quiet-looking poll into dropped latency. The call blocks server-side for a short while and
	that is what we want inside a bounded tick.
	"""
	url = f"{PUBSUB_HOST}/{PUBSUB_VERSION}/{subscription}:pull"
	payload = _decode(
		_post(url, {"maxMessages": int(max_messages)}, token=token, transport=transport, timeout=timeout),
		what="pull",
	)
	received = payload.get("receivedMessages") or []
	return [dict(item) for item in received if isinstance(item, Mapping)]


def acknowledge(
	subscription: str, ack_ids: Sequence[str], *, transport: Any, token: str, timeout: float
) -> None:
	"""``subscriptions.acknowledge`` — **only ever called after the rows are committed.**"""
	ids = [str(a) for a in ack_ids if a]
	if not ids:
		return
	url = f"{PUBSUB_HOST}/{PUBSUB_VERSION}/{subscription}:acknowledge"
	_decode(
		_post(url, {"ackIds": ids}, token=token, transport=transport, timeout=timeout),
		what="acknowledge",
	)


# --------------------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------------------


def ingest_delivery(received: Mapping[str, Any]) -> tuple[str | None, bool]:
	"""Write one delivery to ``Chat Inbound Event``. Returns ``(row name or None, created)``.

	``payload`` holds the delivery **verbatim** — the whole ``receivedMessage``, which nests the
	Pub/Sub envelope under ``message`` exactly where
	:func:`~erpnext_enhancements.chat.sync.decisions.parse_pubsub_envelope` looks for it. Not
	normalised, not re-serialised, not trimmed to the fields today's parser happens to read.
	Three things depend on that: it is the only way to reprocess after a parser bug, it is the
	fixture source for the parser's own tests, and it is the only evidence available when
	Google changes a payload shape — which they do, eleven times in the seven months to
	2026-08-07.

	Routing columns (``gchat_space_name``, ``gchat_resource_name``, ``event_type``) are a
	**best-effort** convenience for operators grepping the table. A payload this parser cannot
	read is still stored, still deduped and still queued; interpretation is the worker's job,
	where a bug is recoverable, and refusing the row here would throw away the one artefact that
	makes the fix possible.

	``created=False`` means ``unique(pubsub_message_id)`` refused it: an at-least-once
	redelivery, which is a **success**. Ack it and do not enqueue a second processing job.
	"""
	message = received.get("message")
	if not isinstance(message, Mapping):
		raise PubSubError("receivedMessage has no 'message' object")

	pubsub_message_id = str(message.get("messageId") or "").strip()
	if not pubsub_message_id:
		# Without it there is no dedupe key, and an event stored without one would be
		# re-ingested on every redelivery for as long as Pub/Sub retries. Refusing means the
		# delivery is nacked by timeout and retried, which is the recoverable direction.
		raise PubSubError("receivedMessage carries no messageId; there would be no dedupe key")

	event_type, space_name, resource_name = _routing_hints(received)

	doc = frappe.new_doc("Chat Inbound Event")
	doc.transport = TRANSPORT_WORKSPACE_EVENT
	doc.event_type = event_type[:140]
	doc.status = STATUS_RECEIVED
	doc.attempts = 0
	doc.received_at = _now()
	doc.gchat_space_name = space_name[:255]
	doc.gchat_resource_name = resource_name[:255]
	doc.pubsub_message_id = pubsub_message_id[:140]
	doc.payload = frappe.as_json(received)

	created = False
	with inbound.savepoint_catching_duplicates():
		doc.insert(ignore_permissions=True)
		created = True

	if not created:
		# `show_unique_validation_message` msgprints before it raises, and an expected
		# redelivery must not surface a "must be unique" banner.
		inbound.clear_expected_duplicate_message()
		existing = frappe.db.get_value("Chat Inbound Event", {"pubsub_message_id": pubsub_message_id}, "name")
		return (str(existing) if existing else None, False)

	return (str(doc.name), True)


def _routing_hints(received: Mapping[str, Any]) -> tuple[str, str, str]:
	"""``(event_type, space_name, resource_name)``, best effort and never raising."""
	from erpnext_enhancements.chat.sync.decisions import parse_pubsub_envelope

	try:
		parsed = parse_pubsub_envelope(received)
	except Exception:
		attributes = (received.get("message") or {}).get("attributes") or {}
		guessed = ""
		for key, value in attributes.items() if isinstance(attributes, Mapping) else ():
			if str(key).lower() == "ce-type":
				guessed = str(value or "")
		return (guessed, "", "")
	return (parsed.event_type, parsed.space_name, parsed.resource_name)


def _now() -> Any:
	"""``frappe.utils.now_datetime()`` — the **site** clock, never SQL ``NOW()``.

	``Chat Inbound Event.received_at``'s own field description says so: the site clock and the
	database clock are not guaranteed to be the same reading, and this column is one half of
	the skew measurement §4.G takes.
	"""
	from frappe.utils import now_datetime

	return now_datetime()


# --------------------------------------------------------------------------------------
# The tick
# --------------------------------------------------------------------------------------


def _log(level: str, summary: str, **fields: Any) -> None:
	"""A log line. **Never** a payload, never a token, never an ackId.

	Identifiers, counts and exception *class names* only — a Pub/Sub error frame can hold the
	bearer token, and the payload is employee chat content.
	"""
	try:
		log = frappe.logger("chat")
	except Exception:  # pragma: no cover - only on a half-booted frappe
		return
	try:
		rendered = " ".join(f"{key}={value}" for key, value in fields.items())
		getattr(log, level, log.info)(f"chat pubsub {summary} {rendered}".rstrip())
	except Exception:
		return


def _lock_key() -> str:
	"""Site-prefixed explicitly: raw redis-py methods are not prefixed by ``frappe.cache()``."""
	site = getattr(frappe.local, "site", None) or "unknown-site"
	return f"{site}|chat:pubsub:pull-lock"


def _acquire_lock(ttl_seconds: int = LOCK_TTL_SECONDS) -> bool:
	"""``SET key value NX EX ttl``. ``False`` ⇒ another tick is already pulling.

	``frappe.cache()`` **is** a ``redis.Redis`` subclass, so ``set(nx=…, ex=…)`` is available
	directly (``PHASE2_VERIFIED.md`` §6). A cache that cannot be reached returns ``False``:
	pulling without a lock is worse than not pulling, because two consumers on one subscription
	split the stream and the symptom is missing messages rather than an error.
	"""
	try:
		got = frappe.cache().set(_lock_key(), b"1", nx=True, ex=int(ttl_seconds))
	except Exception:
		return False
	return bool(got)


def _release_lock() -> None:
	try:
		frappe.cache().delete(_lock_key())
	except Exception:
		return


def pull_inbound_events() -> dict[str, Any]:
	"""**The cron entry point.** One bounded tick. Registered on ``"* * * * *"``.

	Returns a small dict so ``bench execute`` and the health report can read it. Never raises
	past the scheduler: a scheduler job that throws is retried by nothing in v16 and only fills
	the Error Log, and the next minute re-arms this anyway.
	"""
	if not _setting("enabled") or not _setting("relay_inbound_enabled"):
		return {"skipped": "inbound disabled"}
	if _setting("pause_inbound"):
		return {"skipped": "inbound paused"}
	if _setting("dry_run_mode"):
		# Dry run performs zero I/O by definition, and a puller that "dry-ran" a pull would ack
		# nothing while looking healthy.
		return {"skipped": "dry run"}

	try:
		subscription = events_subscription()
	except PubSubError as exc:
		_log("warning", "puller not configured", reason=str(exc))
		return {"skipped": str(exc)}

	if not _acquire_lock():
		return {"skipped": "another tick holds the lock"}

	try:
		return run_pull_cycle(subscription=subscription)
	except Exception as exc:
		# `from None` is not needed on a swallow, but the reason is the same: nothing from the
		# Google surface, including this frame's locals, goes into the Error Log.
		_log("warning", "pull cycle failed", error=type(exc).__name__)
		return {"error": type(exc).__name__}
	finally:
		_release_lock()


def run_pull_cycle(
	*,
	subscription: str,
	transport: Any = None,
	token: str | None = None,
	budget_seconds: int | None = None,
	max_messages: int | None = None,
	clock: Any = None,
) -> dict[str, Any]:
	"""Pull → **write → commit → ack → enqueue**, until the wall-clock budget is spent.

	``transport``, ``token`` and ``clock`` are injection points for the bench-free suite; the
	cron path passes none of them.

	Each batch is one commit. That bounds the work a mid-flight kill can lose to a single
	batch's un-acked deliveries, all of which Pub/Sub redelivers and
	``unique(pubsub_message_id)`` then absorbs.

	The enqueues carry ``enqueue_after_commit=True`` (in
	:func:`~erpnext_enhancements.chat.sync.inbound.enqueue_inbound_event`), which means they
	fire on the **next** commit — so the cycle commits once more after enqueuing. Without that
	trailing commit the jobs would sit in ``frappe.db``'s after-commit list and never be
	dispatched, and the only thing that would ever process the rows is the ten-minute sweeper.
	"""
	monotonic = clock or time.monotonic
	deadline = monotonic() + int(budget_seconds or DEFAULT_BUDGET_SECONDS)
	timeout = float(_setting_int("http_timeout_seconds", 30))
	batch_size = int(max_messages or DEFAULT_MAX_MESSAGES)
	access_token = token or _access_token()

	pulled = ingested = duplicates = acked = batches = 0

	while monotonic() < deadline:
		received = pull(
			subscription,
			max_messages=batch_size,
			transport=transport,
			token=access_token,
			timeout=timeout,
		)
		if not received:
			break

		batches += 1
		pulled += len(received)
		ack_ids: list[str] = []
		to_process: list[str] = []

		for item in received:
			ack_id = str(item.get("ackId") or "")
			try:
				name, created = ingest_delivery(item)
			except Exception as exc:
				# Not acked. Pub/Sub redelivers, and the row that could not be written is not
				# silently dropped. One malformed delivery must not cost the rest of the batch,
				# which is why every insert is inside its own savepoint.
				_log("warning", "delivery not ingested", error=type(exc).__name__)
				continue

			ack_ids.append(ack_id)
			if created and name:
				ingested += 1
				to_process.append(name)
			elif not created:
				duplicates += 1

		# COMMIT BEFORE ACK. Reversing these two loses events on a worker crash, and a deploy
		# restarts the whole honcho group mid-tick on every release.
		frappe.db.commit()

		acknowledge(subscription, ack_ids, transport=transport, token=access_token, timeout=timeout)
		acked += len(ack_ids)

		for name in to_process:
			inbound.enqueue_inbound_event(name)
		# Flush the after-commit enqueues registered above.
		frappe.db.commit()

		if len(received) < batch_size:
			# The subscription is drained; spending the rest of the budget on empty pulls buys
			# nothing and holds a worker slot.
			break

	return {
		"pulled": pulled,
		"ingested": ingested,
		"duplicates": duplicates,
		"acked": acked,
		"batches": batches,
	}
