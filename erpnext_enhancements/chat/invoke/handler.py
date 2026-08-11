# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The one ``@triton`` handler. **It consumes envelopes and cannot see the origin.**

Locked decision #4 says a mention behaves identically from the native Chat client and from the
SPA. This module is how that is guaranteed rather than hoped for: it takes an
:class:`~chat.invoke.envelope.Envelope`, which has no origin field, so there is nothing here to
branch on. A reviewer cannot miss a branch that cannot be written.

--------------------------------------------------------------------------------------
Two identities, never merged
--------------------------------------------------------------------------------------

| Concern | Identity |
|---|---|
| reading chat context, calling ERPNext tools, acting on data | **the mentioning human** |
| posting the answer | **the bot** |

Conflating them is how a superuser service account gets built, and the failure mode is
seductive rather than obvious: the relay already needs a machine credential to post, and
reusing it for the *read* half would make this file shorter and would silently give Triton
every user's data. So :func:`handle` retrieves as ``envelope.user`` and posts as the bot, and
both halves are asserted in one turn.

--------------------------------------------------------------------------------------
Never inline, because the deadline is not ours
--------------------------------------------------------------------------------------

Google's interaction webhook has a **hard 30-second deadline**. A model turn plus retrieval
does not fit inside it reliably, and a handler that answers inline works in development and
times out under load. So the webhook acknowledges and enqueues, and this runs on a worker.

Which means it inherits this deployment's background-job facts: a deploy FLUSHDBs the queue
Redis, Frappe v16 wires no RQ retries, and a job can be SIGKILLed mid-call. The invocation log
row is written **first**, so a turn that vanishes leaves evidence that it started rather than
no trace at all.

--------------------------------------------------------------------------------------
The rollout task that looks like a bug
--------------------------------------------------------------------------------------

Every person Triton acts for must have completed the ERPNext OAuth link, or the client raises
before the first token — and on the streaming path that surfaces as a generic failure rather
than a "link ERPNext" prompt. :func:`handle` checks first and answers with an explicit,
actionable sentence in-thread. For ~50 people that is an afternoon of onboarding, but only if
somebody schedules the afternoon; it is not a Phase 5 implementation detail and it will look
like one.
"""

from __future__ import annotations

import time
from typing import Any

import frappe
from frappe.utils import cint, now_datetime

from erpnext_enhancements.chat.doctype.triton_invocation_log.triton_invocation_log import (
	STATUS_ANSWERED,
	STATUS_FAILED,
	STATUS_GENERATING,
	STATUS_RECEIVED,
	STATUS_REFUSED,
	STATUS_RETRIEVING,
)
from erpnext_enhancements.chat.invoke.envelope import Envelope, from_job_kwargs
from erpnext_enhancements.chat.retrieval import gate

LOG_DOCTYPE = "Triton Invocation Log"

#: What the person sees when they have not linked ERPNext. A sentence they can act on, not an
#: error — "Communication disruption detected" is what the streaming path produces otherwise,
#: and nobody has ever fixed anything from that.
UNLINKED_REPLY = (
	"I can't answer that yet — your ERPNext account isn't linked to me, so I can't read "
	"anything on your behalf. Open the Triton assistant in ERPNext once and click **Link "
	"ERPNext**; after that, mentions here will work."
)

REFUSED_REPLY = "I can't answer that right now: {reason}"

FAILED_REPLY = (
	"Something went wrong answering that. It's been logged — an administrator can find it "
	"under Triton Invocation Log."
)


def run(**kwargs: Any) -> str | None:
	"""The background-job entry point. Takes plain kwargs, rebuilds the envelope, handles.

	Separate from :func:`handle` because ``frappe.enqueue`` pickles its kwargs, and a pickled
	dataclass makes the queueing process and the running process share a class definition — a
	dependency a deploy breaks in the one direction nobody tests.
	"""
	try:
		envelope = from_job_kwargs(kwargs)
	except ValueError:
		frappe.log_error(
			"A queued @triton turn was built by a different release and was refused rather "
			"than interpreted under the current envelope rules.",
			"Chat Triton",
		)
		return None
	return handle(envelope)


def handle(envelope: Envelope, *, origin: str = "") -> str | None:
	"""Answer one mention. Returns the invocation-log row name.

	``origin`` is accepted **only** to be written to the log and is never read below this
	function's first three lines. It is not on the envelope precisely so it cannot be reached
	from anywhere the answer is decided; if a future change needs it further down, that is the
	change decision #4 forbids and it should be hard.
	"""
	started = time.monotonic()
	log_name = _open_log(envelope, origin)

	if not envelope.user or not envelope.room:
		_close_log(log_name, STATUS_REFUSED, error="the mention resolved to no user or no room")
		return log_name

	if not _has_erpnext_link(envelope.user):
		_reply(envelope, UNLINKED_REPLY, log_name=log_name)
		_close_log(log_name, STATUS_REFUSED, error="no ERPNext OAuth link for the mentioning user")
		return log_name

	# --- retrieve, as the human ------------------------------------------------------
	_set_status(log_name, STATUS_RETRIEVING)
	retrieval_started = time.monotonic()
	try:
		result = gate.retrieve(
			query=envelope.text,
			user=envelope.user,
			room=envelope.room,
			thread_root=envelope.thread_root,
			purpose="mention",
			request_id=envelope.request_id,
		)
	except gate.RetrievalRefused as exc:
		_reply(envelope, REFUSED_REPLY.format(reason=str(exc)), log_name=log_name)
		_close_log(log_name, STATUS_REFUSED, error=str(exc))
		return log_name
	except Exception as exc:
		# `from None` discipline applies to the log too: never publish frame locals.
		_close_log(log_name, STATUS_FAILED, error=f"retrieval failed: {exc.__class__.__name__}")
		_reply(envelope, FAILED_REPLY, log_name=log_name)
		return log_name
	retrieval_ms = int((time.monotonic() - retrieval_started) * 1000)

	# --- generate, as the human ------------------------------------------------------
	_set_status(log_name, STATUS_GENERATING)
	model_started = time.monotonic()
	try:
		from erpnext_enhancements.chat.invoke import triton_client

		answer = triton_client.ask(
			user=envelope.user,
			question=envelope.text,
			context=result.assembly.text() if result.assembly else "",
			request_id=envelope.request_id,
			room=envelope.room,
		)
	except Exception as exc:
		_close_log(
			log_name,
			STATUS_FAILED,
			error=f"model turn failed: {exc.__class__.__name__}",
			retrieval_ms=retrieval_ms,
			result=result,
		)
		_reply(envelope, FAILED_REPLY, log_name=log_name)
		return log_name
	model_ms = int((time.monotonic() - model_started) * 1000)

	# --- post, as the bot ------------------------------------------------------------
	from erpnext_enhancements.chat.retrieval import citations

	text = citations.strip_unknown_refs(answer.get("text") or "", result.manifest)
	_, missing = citations.split_known_refs(answer.get("text") or "", result.manifest)
	reply_name = _reply(envelope, text, log_name=log_name, manifest=result.manifest)

	_close_log(
		log_name,
		STATUS_ANSWERED,
		result=result,
		retrieval_ms=retrieval_ms,
		model_ms=model_ms,
		total_ms=int((time.monotonic() - started) * 1000),
		answer_message=reply_name,
		citation_miss_count=len(missing),
		answer=answer,
		manifest=result.manifest,
	)
	return log_name


# ------------------------------------------------------------------------------- the reply


def _reply(
	envelope: Envelope,
	text: str,
	*,
	log_name: str | None = None,
	manifest: list[Any] | None = None,
) -> str | None:
	"""Post Triton's answer **as the bot**, into the mention's own room and thread.

	Three conditions, and they are the ones that keep the confirmation exemption narrow enough
	to defend:

	1. **the room and thread come from the envelope**, never from model output — there is no
	   parameter here by which the answer could be posted somewhere else;
	2. **the sender is the bot identity**, so the reply is visibly attributed and cannot
	   impersonate the person who asked;
	3. **the turn is still recorded** in the invocation log, so "not confirmed" never means
	   "not recorded".

	Written through the sync engine's own ``insert_message`` rather than around it, so the
	outbox, the seq allocation and the relay all behave exactly as they do for a human message
	— a second write path is a second place the ordering rules have to be right.
	"""
	if not text.strip():
		return None
	try:
		from erpnext_enhancements.chat.sync import outbox

		doc = frappe.new_doc("Chat Message")
		doc.room = envelope.room
		doc.sender = _bot_user()
		doc.text = text
		doc.thread_root = envelope.thread_root
		doc.sync_origin = "Triton"
		outbox.insert_message(doc)
		return doc.name
	except Exception as exc:
		try:
			frappe.log_error(
				f"Triton could not post its reply for {log_name or envelope.request_id}: "
				f"{exc.__class__.__name__}",
				"Chat Triton",
			)
		except Exception:
			pass
		return None


def _bot_user() -> str:
	"""The User the bot posts as.

	Falls back to the mentioning-agnostic ``Administrator`` **never** — that would attribute
	Triton's answers to a superuser and make the two-identity rule invisible in the transcript.
	A missing bot user is a configuration failure and is raised as one.
	"""
	user = frappe.db.get_single_value("Chat Settings", "chat_app_service_account") or ""
	if user and frappe.db.exists("User", user):
		return user
	if frappe.db.exists("User", "triton@sapphirefountains.com"):
		return "triton@sapphirefountains.com"
	raise RuntimeError(
		"No bot User is configured for Triton's chat replies. The reply must be posted by a "
		"distinct identity, not by the mentioning person and not by Administrator — that "
		"separation is what stops Triton's posting identity and Triton's permissions becoming "
		"the same thing."
	)


def has_erpnext_link(user: str) -> bool:
	"""Whether this person has completed the ERPNext OAuth link Triton needs.

	Checked before the turn rather than discovered as an exception mid-turn, because the
	exception surfaces to the user as *"Communication disruption detected"* — a sentence
	nobody has ever fixed anything from — and the actionable prompt is the whole point.

	**Public, and deliberately the only implementation.** ``chat/rollout.py``'s readiness
	report calls this exact function rather than writing its own predicate: a report that
	answers "who is ready" differently from the code that decides "can this turn run" is worse
	than no report, because it is confidently wrong at the moment somebody trusts it.

	ERPNext can answer this authoritatively because ERPNext is the OAuth *provider* — Triton
	holds a bearer issued here, so the grant is a row in this database. What ERPNext cannot
	see is whether Triton's own copy is intact; that residual is stated in the report.

	Any failure to answer reads as **not linked** — the safe direction, since the cost is one
	unnecessary prompt and the alternative is a turn that dies with a generic error.
	"""
	try:
		return bool(
			frappe.db.exists("OAuth Bearer Token", {"user": user, "status": "Active"})
			or frappe.db.exists("Token Cache", {"user": user})
		)
	except Exception:
		return False


#: Kept as a private alias so the call site below reads as a guard rather than as a query.
_has_erpnext_link = has_erpnext_link


# ------------------------------------------------------------------------------ the log


def _open_log(envelope: Envelope, origin: str) -> str:
	"""Write the row **before** any work, so a job that vanishes leaves evidence.

	Idempotent on ``request_id``: a redelivered interaction event finds the existing row and
	reuses it rather than answering twice. Best-effort throughout — this is instrumentation,
	and instrumentation that fails closed is an outage caused by a metric.
	"""
	try:
		existing = frappe.db.get_value(LOG_DOCTYPE, {"request_id": envelope.request_id}, "name")
		if existing:
			return existing
		doc = frappe.new_doc(LOG_DOCTYPE)
		doc.request_id = envelope.request_id
		doc.status = STATUS_RECEIVED
		doc.origin = origin or None
		doc.asked_by = envelope.user or None
		doc.room = envelope.room or None
		doc.mention_message = envelope.message or None
		doc.thread_root = envelope.thread_root or None
		doc.insert(ignore_permissions=True)
		return doc.name
	except Exception:
		return ""


def _set_status(log_name: str, status: str) -> None:
	if not log_name:
		return
	try:
		frappe.db.set_value(LOG_DOCTYPE, log_name, "status", status, update_modified=False)
	except Exception:
		pass


def _close_log(
	log_name: str,
	status: str,
	*,
	error: str = "",
	result: Any = None,
	retrieval_ms: int = 0,
	model_ms: int = 0,
	total_ms: int = 0,
	answer_message: str | None = None,
	citation_miss_count: int = 0,
	answer: dict[str, Any] | None = None,
	manifest: list[Any] | None = None,
) -> None:
	if not log_name:
		return
	values: dict[str, Any] = {"status": status}
	if error:
		values["error"] = error[:2000]
	if retrieval_ms:
		values["retrieval_ms"] = retrieval_ms
	if model_ms:
		values["model_ms"] = model_ms
	if total_ms:
		values["total_ms"] = total_ms
	if answer_message:
		values["answer_message"] = answer_message
		values["answered_at"] = now_datetime()
	if citation_miss_count:
		values["citation_miss_count"] = citation_miss_count
	if result is not None:
		values.update(
			{
				"context_tokens": cint(getattr(result, "context_tokens", 0)),
				"context_truncated": 1 if getattr(result, "context_truncated", False) else 0,
				"degradation_rung": cint(getattr(result, "degradation_rung", 0)),
				"chunks_considered": cint(getattr(result, "chunks_considered", 0)),
				"chunks_returned": cint(getattr(result, "chunks_returned", 0)),
				"digests_used": cint(getattr(result, "digests_used", 0)),
				"citation_count": len(getattr(result, "manifest", []) or []),
			}
		)
	if manifest:
		# Stored so the SPA can render inline citations on an answer it loads later — a
		# transcript scrolled back to next week must show the same links it showed live, and
		# the manifest is the only thing that makes an id resolvable.
		#
		# The wire shape is produced by `Citation.as_dict`, which matches the renderer that
		# shipped in Phase 3. No snippet, deliberately: a snippet is message text, and this
		# table is content-free by construction.
		values["citations"] = frappe.as_json([entry.as_dict() for entry in manifest])
	if answer:
		values.update(
			{
				"model_used": (answer.get("model") or "")[:140] or None,
				"prompt_tokens": cint(answer.get("prompt_tokens")),
				"completion_tokens": cint(answer.get("completion_tokens")),
				"cached_content_tokens": cint(answer.get("cached_content_tokens")),
			}
		)
	try:
		frappe.db.set_value(LOG_DOCTYPE, log_name, values, update_modified=False)
	except Exception:
		pass
