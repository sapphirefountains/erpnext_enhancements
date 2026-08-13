# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Triton client for a chat turn, carrying the **mentioning person's** identity.

It reuses ``erpnext_enhancements.triton_chat``'s existing bridge rather than minting a second
one, which is the shape recommended for CQ-24 and is the one built here. The trade is stated
rather than glossed: the endpoint and its secret keep saying "erpnext" while serving Chat, and
**that same secret is also the telephony gateway's**, so its blast radius is wider than its
name suggests. The security posture is identical either way — it is a token-exchange grant
authorising the *impersonation request*, not a superuser session — and the naming smell is
real. A sibling bridge is ~150 lines in the other repository if Nikolas prefers it.

--------------------------------------------------------------------------------------
The identity, spelled out because it is the thing most easily got wrong
--------------------------------------------------------------------------------------

``mint_user_token`` exchanges an email for that person's short-lived Triton JWT, authenticating
machine-to-machine with the gateway secret. Triton then runs the turn as them, and its own
ERPNext tool calls go back over *their* OAuth bearer. So the chain is: the human's mention →
the human's Triton token → the human's ERPNext permissions. The bot identity appears nowhere in
it; that one is used only to post the answer.

The token cache is per-user and keyed on the session user, so this module **sets the session**
for the exchange. Without that, a background job would mint a token for ``Administrator`` and
Triton would answer as a superuser — which is precisely the failure the two-identity rule
exists to prevent, arriving through a cache key.

--------------------------------------------------------------------------------------
Not streamed, and why that is not a regression here
--------------------------------------------------------------------------------------

The widget streams because a person is watching a text box fill in. A chat turn is not watched:
it is acknowledged, enqueued, and posted when it is ready. Streaming into a background job
would mean accumulating the whole answer anyway and would add the one constraint the relay
already documents — that a streaming generator runs *after* Frappe has torn down the request,
with no session and no usable database handle, so it cannot write a ``Chat Message`` at all.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint

#: Triton's query endpoint is **session-scoped**. There is no session-less
#: ``/api/v1/assistant/query`` — the first version of this module assumed one and would have
#: 404'd on every single turn, which is what comes of writing a client against a remembered
#: API instead of against the repository sitting next to this one.
SESSIONS_PATH: str = "/api/v1/assistant/sessions"
QUERY_PATH_TEMPLATE: str = "/api/v1/assistant/sessions/{session_id}/query"

DEFAULT_TIMEOUT: int = 120

#: One Triton session per (person, room), cached. Long-lived: a follow-up mention in the same
#: room continues the same conversation, which is what "what about the other one" needs.
#:
#: **The duplication this accepts, stated rather than discovered later.** The assembled context
#: carries the current thread verbatim *and* Triton keeps its own history of previous mentions,
#: so a follow-up shows the model some of the same text twice. It is bounded — Triton's history
#: is only the mentions, which are sparse — and the alternative, a fresh session per turn,
#: throws away continuity and fills the person's Triton session list with one entry per
#: question. Revisit by switching this to a per-turn session if the duplication shows up in
#: `Triton Invocation Log.prompt_tokens`.
SESSION_CACHE_TTL: int = 30 * 24 * 3600
SESSION_TITLE: str = "Google Chat"


#: What Triton's structured error codes mean **for a chat turn**, in the words the person
#: reading an Error Log needs.
#:
#: Triton answers a handled failure with ``{"message", "request_id", "code"}`` and the ``code``
#: is a fixed vocabulary of its own making — an enum, not a sentence, and in particular not
#: anything derived from the prompt. That is what makes it the one part of an error body this
#: module is willing to repeat. See :func:`_error_fields`.
_ERROR_CODE_MEANINGS: dict[str, str] = {
	# The 401 that cost a week. It is NOT a rejection of our token: Triton authenticated the
	# identity fine, then tried to make its own ERPNext tool calls *as* them and found no OAuth
	# grant to make them with. Triton refuses to fall back to its system key there — correctly,
	# since that would silently answer with service-account permissions instead of the person's.
	# Whoever the turn runs as, human or bot, has to click 'Link ERPNext' in Triton once.
	"erpnext_link_required": (
		"this identity has not linked ERPNext inside Triton, so Triton has no grant to run its "
		"ERPNext tool calls with — that person (or the bot account, for a digest) must click "
		"'Link ERPNext' in Triton once; it is not a problem with the token we sent"
	),
}


class TritonUnavailable(Exception):
	"""Triton could not be reached or refused the turn. Never carries a token.

	Carries ``status`` (the HTTP status, ``0`` when the failure was before or after the
	response) and ``code`` (Triton's structured error code, ``""`` when it gave none). Both are
	machine-readable on purpose: ``ask`` retries a stale session on a 404 by looking at
	``status``, and a caller that wants to distinguish "Triton is down" from "this person needs
	to link ERPNext" should not have to parse the message to do it.
	"""

	def __init__(self, message: str, *, status: int = 0, code: str = "") -> None:
		super().__init__(message)
		self.status = status
		self.code = code


def ask(*, user: str, question: str, context: str, request_id: str = "", room: str = "") -> dict[str, Any]:
	"""Ask Triton one question **as ``user``**, with the assembled chat context attached.

	The context rides on the **user turn**, not in the system instruction, and that is a cost
	decision rather than an ergonomic one: the system instruction is the cached prefix, so
	putting per-question context there would invalidate the cache on every single turn. The
	widget's own page-context preamble is placed the same way, for the same reason.

	Returns:
		``{"text", "model", "prompt_tokens", "completion_tokens", "cached_content_tokens"}``.
		Token counts are best-effort: they are what the invocation log's cost columns are for,
		and a missing count must not fail a turn that otherwise worked.
	"""
	from erpnext_enhancements import triton_chat

	# --- configuration, read BEFORE assuming anybody's identity ---------------------------
	#
	# `get_settings()` reads Triton Settings, and that includes `get_password()` on the gateway
	# secret — which Frappe permission-checks. Reading it inside the impersonation window
	# below asked the *mentioning person* for permission to read a password field, and they do
	# not have it. Nor does the bot account, which is how it surfaced: the digest summariser
	# raised PermissionError on every run.
	#
	# It would have done the same for **every mention**, since `handle()` passes the employee
	# who typed it. Configuration is the app's, not the asker's; only the token mint needs
	# their identity, and that stays inside the window.
	settings = triton_chat.get_settings()
	if not settings.get("enabled"):
		raise TritonUnavailable("The Triton assistant is switched off in ERPNext.")
	base_url = settings.get("base_url") or ""
	if not base_url:
		raise TritonUnavailable("Triton Settings has no Gateway URL, so there is nothing to ask.")
	timeout = cint(settings.get("timeout")) or DEFAULT_TIMEOUT

	previous = frappe.session.user
	try:
		# The token cache is keyed on the session user, and this runs in a background job
		# whose session is Administrator. Minting without setting this would hand Triton a
		# superuser token — the two-identity rule defeated by a cache key.
		frappe.set_user(user)
		session_id, from_cache = _session_for(base_url, user=user, room=room, timeout=timeout)
		try:
			body = _post(
				base_url,
				QUERY_PATH_TEMPLATE.format(session_id=session_id),
				{"prompt": _prompt(question, context)},
				timeout=timeout,
			)
		except TritonUnavailable as exc:
			# The cached id is a hint, and this is where it gets verified — by use, which is the
			# only way to verify it. Triton answers 404 for a session it does not have *or* does
			# not own, so a person deleting the thread from Triton's web app lands here. Until
			# now `_session_for`'s docstring promised the recovery and no code performed it: the
			# stale id stayed cached for its full thirty days and `@triton` was dead in that room
			# for a month with no way back.
			#
			# Only when the id came from cache. A session created seconds ago that 404s is a
			# real fault, and retrying it would create a second orphan per turn forever.
			if exc.status != 404 or not from_cache:
				raise
			_forget_session(user=user, room=room)
			session_id, _ = _session_for(base_url, user=user, room=room, timeout=timeout)
			body = _post(
				base_url,
				QUERY_PATH_TEMPLATE.format(session_id=session_id),
				{"prompt": _prompt(question, context)},
				timeout=timeout,
			)

		# `ChatMessage` on the wire: {role, content, id, session_id, tokens, ui_metadata,
		# created_at}. The answer is `content` — NOT `response` or `text`, which is what the
		# first version of this read and which would have produced an empty reply on every
		# turn while looking like the model had nothing to say.
		meta = body.get("ui_metadata") or {}
		return {
			"text": str(body.get("content") or "").strip(),
			"model": str(meta.get("model") or meta.get("model_name") or ""),
			# One number upstream, and it is the turn's total. Recorded as the prompt count
			# rather than split across two columns that would then both be guesses.
			"prompt_tokens": cint(body.get("tokens")),
			"completion_tokens": 0,
			"cached_content_tokens": cint(meta.get("cached_content_token_count")),
		}
	finally:
		# Restored on every path including the exception one. A job that left the session
		# pointing at a coworker would attribute everything it did afterwards to them.
		if previous:
			frappe.set_user(previous)


def _post(base_url: str, path: str, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
	"""One authed POST. Status only in the error, never the body.

	The body can echo the prompt back, and the prompt contains the assembled chat context —
	which is employee-private conversation. An exception message is the least controlled
	string in the system: it reaches the Error Log, and from there a screenshot.
	"""
	import requests

	from erpnext_enhancements import triton_chat

	try:
		token = triton_chat.mint_user_token()
	except Exception as exc:
		# A mint failure is an outage of this integration, not a bug in the caller, so it is
		# reported as one — and named, because "the model was unavailable" for a permission
		# problem sent us looking at the model for an hour. The class only: this message is
		# not known to be content-free the way the ones below are.
		raise TritonUnavailable(
			f"could not mint a Triton token for this identity: {exc.__class__.__name__}"
		) from None

	try:
		response = requests.post(
			f"{base_url}{path}",
			json=payload,
			headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
			timeout=timeout,
		)
	except Exception as exc:
		raise TritonUnavailable(f"Could not reach Triton: {exc.__class__.__name__}") from None

	if response.status_code >= 400:
		code, upstream_request_id = _error_fields(response)
		raise TritonUnavailable(
			_error_detail(response.status_code, path, code, upstream_request_id),
			status=response.status_code,
			code=code,
		) from None

	try:
		return response.json() or {}
	except Exception:
		raise TritonUnavailable("Triton returned a body that is not JSON") from None


def _error_fields(response: Any) -> tuple[str, str]:
	"""``code`` and ``request_id`` off an error body, and **nothing else, ever**.

	The rule this module works to is not "never repeat a response body" — it is *repeat only
	what a type guarantees is content-free*. ``message`` fails that test: it is ``str(exc)`` at
	the other end and can be any string the failing code chose. ``code`` and ``request_id``
	pass it: an enum Triton picked from a fixed list, and a uuid.

	This function is the correction of a real cost. ``_post`` discarded the whole body, so a
	string that already said ``erpnext_link_required`` reached the Error Log as "Triton
	returned HTTP 401" — and a week went into the wrong question, *why is our token being
	rejected*, when the answer was that it wasn't. Protecting message content is right;
	throwing away the diagnosis with it is not the same act, and I had them fused.

	Both values are type-checked and truncated rather than trusted, so a server that put a dict
	where the enum goes cannot smuggle anything through this seam.
	"""
	try:
		body = response.json()
	except Exception:
		return "", ""
	if not isinstance(body, dict):
		return "", ""
	code = body.get("code")
	request_id = body.get("request_id")
	return (
		code[:64] if isinstance(code, str) else "",
		request_id[:64] if isinstance(request_id, str) else "",
	)


def _error_detail(status: int, path: str, code: str, upstream_request_id: str) -> str:
	"""The sentence that goes in the log. Pure, so its wording is under test.

	``request_id`` is included because it is the only handle that ties this failure to the line
	in Triton's own logs — without it, correlating the two sides means guessing by timestamp
	across a UTC/site-local boundary this repository has already been bitten by.
	"""
	detail = f"Triton returned HTTP {status} for {path}"
	if code:
		meaning = _ERROR_CODE_MEANINGS.get(code)
		detail += f" ({code}: {meaning})" if meaning else f" ({code})"
	if upstream_request_id:
		detail += f" [Triton request {upstream_request_id}]"
	return detail


def _session_cache_key(*, user: str, room: str) -> str:
	return f"triton:chat_session:{user}:{room or 'global'}"


def _forget_session(*, user: str, room: str) -> None:
	"""Drop a cached session id that Triton no longer recognises. Never raises."""
	try:
		frappe.cache().delete_value(_session_cache_key(user=user, room=room))
	except Exception:
		# Worst case the next turn 404s and retries again — one wasted request, not a failure.
		pass


def _session_for(base_url: str, *, user: str, room: str, timeout: int) -> tuple[int, bool]:
	"""The Triton session this room's mentions continue in. Created on first use, then cached.

	Returns ``(session_id, came_from_cache)``. The flag is not decoration: it is what lets
	``ask`` tell a stale cached id (retry once, transparently) from a session Triton rejected
	the moment after creating it (a real fault, and retrying would mint an orphan per turn).

	A **404 on a cached id is expected**, not exceptional: the person can delete the session
	from the Triton web app at any time, and a client that treated that as an outage would
	break `@triton` for them permanently with no way back. So the cached id is a hint —
	verified by use, discarded on a miss, and recreated. The verification happens in ``ask``,
	because using it *is* the only verification available.
	"""
	key = _session_cache_key(user=user, room=room)
	try:
		cached = frappe.cache().get_value(key)
	except Exception:
		cached = None

	if cached:
		try:
			return int(cached), True
		except (TypeError, ValueError):
			pass

	created = _post(
		base_url,
		SESSIONS_PATH,
		{"title": f"{SESSION_TITLE}{f' — {room}' if room else ''}"[:80]},
		timeout=timeout,
	)
	session_id = cint(created.get("id"))
	if not session_id:
		raise TritonUnavailable("Triton created a session with no id") from None

	try:
		frappe.cache().set_value(key, session_id, expires_in_sec=SESSION_CACHE_TTL)
	except Exception:
		# A deploy FLUSHDBs this cache, so a miss is normal and costs one extra session.
		pass
	return session_id, False


def _prompt(question: str, context: str) -> str:
	"""The user turn: context first, then the question.

	Context first because the model reads it as the ground it answers from, and a question
	stated before its evidence invites an answer composed before the evidence is read. The
	widget's preamble uses the same ordering and the same ``USER QUERY:`` marker, so a change
	to one should be a change to both.
	"""
	if not context:
		return question
	return f"{context}\n\nUSER QUERY: {question}"
