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

#: Non-streaming query endpoint. The streaming sibling is what the widget uses.
QUERY_PATH: str = "/api/v1/assistant/query"

DEFAULT_TIMEOUT: int = 120


class TritonUnavailable(Exception):
	"""Triton could not be reached or refused the turn. Never carries a token."""


def ask(*, user: str, question: str, context: str, request_id: str = "") -> dict[str, Any]:
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
	import requests

	from erpnext_enhancements import triton_chat

	previous = frappe.session.user
	try:
		# The token cache is keyed on the session user, and this runs in a background job
		# whose session is Administrator. Minting without setting this would hand Triton a
		# superuser token — the two-identity rule defeated by a cache key.
		frappe.set_user(user)
		settings = triton_chat.get_settings()
		if not settings.get("enabled"):
			raise TritonUnavailable("The Triton assistant is switched off in ERPNext.")
		base_url = settings.get("base_url") or ""
		if not base_url:
			raise TritonUnavailable("Triton Settings has no Gateway URL, so there is nothing to ask.")

		token = triton_chat.mint_user_token()
		payload = {
			"prompt": _prompt(question, context),
			"request_id": request_id or None,
		}
		try:
			response = requests.post(
				f"{base_url}{QUERY_PATH}",
				json=payload,
				headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
				timeout=cint(settings.get("timeout")) or DEFAULT_TIMEOUT,
			)
		except Exception as exc:
			raise TritonUnavailable(f"Could not reach Triton: {exc.__class__.__name__}") from None

		if response.status_code >= 400:
			# Status only. The body can echo the prompt back, and the prompt contains the
			# assembled chat context — which is employee-private conversation.
			raise TritonUnavailable(f"Triton returned HTTP {response.status_code}") from None

		try:
			body = response.json() or {}
		except Exception:
			raise TritonUnavailable("Triton returned a body that is not JSON") from None

		return {
			"text": str(body.get("response") or body.get("text") or "").strip(),
			"model": str(body.get("model") or ""),
			"prompt_tokens": cint((body.get("usage") or {}).get("prompt_tokens")),
			"completion_tokens": cint((body.get("usage") or {}).get("completion_tokens")),
			"cached_content_tokens": cint((body.get("usage") or {}).get("cached_content_tokens")),
		}
	finally:
		# Restored on every path including the exception one. A job that left the session
		# pointing at a coworker would attribute everything it did afterwards to them.
		if previous:
			frappe.set_user(previous)


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
