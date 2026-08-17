# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Triton client for one work breakdown, carrying the **approving reviewer's** identity.

Modelled closely on :mod:`erpnext_enhancements.chat.invoke.triton_client`, which is the
worked example of calling Triton from a background job in this app. Read that file first;
the identity reasoning, the "never repeat a response body" rule and the ``TritonUnavailable``
shape are all lifted from it deliberately rather than reinvented.

--------------------------------------------------------------------------------------
Two ways this differs from the chat client, both on purpose
--------------------------------------------------------------------------------------

**1. It is not gated on the assistant widget's switch.** ``triton_chat.get_settings()``
reports ``enabled`` from ``Triton Assistant Settings``, which is the *desk widget's* on/off.
The chat client refuses when it is off, correctly — ``@triton`` in a room is the same
product as the widget. A work breakdown is not: it has its own kill switch
(``Product Feedback Settings.paused``), and coupling the two would mean somebody turning the
floating chat bubble off silently breaks feature intake with no error that names the cause.
This module reads ``base_url`` and ``gateway_secret`` from the same settings and ignores
``enabled``.

**2. There is no session.** The chat client keeps a long-lived Triton session per (person,
room) so a follow-up mention continues the conversation. A breakdown is one shot: there is
no follow-up, nothing to continue, and a cached session id would only add the stale-404
recovery path that file needed. This posts once to a route that returns structured JSON and
is done. See ADR 0010.

The identity chain is otherwise identical: the reviewer's approval → the reviewer's Triton
token → whatever Triton does under their name. **The token cache is keyed on the session
user**, so this module sets the session for the exchange and restores it in a ``finally``.
Without that, a background job mints a token for ``Administrator`` and the call runs as a
superuser — the two-identity rule defeated by a cache key.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any

import frappe

#: Added to Triton in the same change as this module. Unlike the assistant routes this one is
#: **not** session-scoped — there is no session to create first, which is the mistake the
#: chat client's docstring records paying for.
WORK_BREAKDOWN_PATH: str = "/api/v1/planning/work-breakdown"

#: The "Expand with AI" button. Called by the *requester*, an ordinary employee — not a
#: reviewer — which the bridge handles: it auto-provisions a Triton user on first mint, so
#: somebody who has never opened Triton can still use the button.
DRAFT_DESCRIPTION_PATH: str = "/api/v1/planning/draft-description"

DEFAULT_TIMEOUT: int = 180

#: Somebody is watching a spinner. Long enough for a paragraph, short enough to give up on.
DRAFT_TIMEOUT: int = 60


class TritonUnavailable(Exception):
	"""Triton could not be reached or refused the breakdown. Never carries a token.

	Carries ``status`` (HTTP status, ``0`` when the failure was before or after the response)
	and ``code`` (Triton's structured error code, ``""`` when it gave none). Both are
	machine-readable so a caller can tell "Triton is down" from "this reviewer has not linked
	ERPNext" without parsing the message.
	"""

	def __init__(self, message: str, *, status: int = 0, code: str = "") -> None:
		super().__init__(message)
		self.status = status
		self.code = code


#: What Triton's structured error codes mean for a breakdown, in the words the person reading
#: an Error Log needs. Same vocabulary as the chat client, restated because the remedy differs:
#: here it is the *reviewer* who must link, not the person who filed the request.
_ERROR_CODE_MEANINGS: dict[str, str] = {
	"erpnext_link_required": (
		"the approving reviewer has not linked ERPNext inside Triton, so Triton has no grant "
		"to run its own ERPNext calls with — they must click 'Link ERPNext' in Triton once; "
		"it is not a problem with the token we sent"
	),
}


def request_breakdown(
	*,
	reviewer: str,
	payload: dict[str, Any],
	timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
	"""Ask Triton to break one request into tasks, **as ``reviewer``**.

	``payload`` is built by :mod:`erpnext_enhancements.product_feedback.breakdown` and carries
	the request text, the captured context, the two candidate Projects and the open-task
	subjects for the duplicate check. It is passed through unread: this module is transport.

	Returns the parsed JSON body. Validating its *shape* is the caller's job — see
	``breakdown.parse_breakdown``, which is stdlib-only so the parsing rules can be tested
	without a bench.
	"""
	# Configuration is read BEFORE assuming anybody's identity, and the reason is attribution
	# rather than permission.
	#
	# An earlier version of this comment claimed `get_password()` is permission-checked and
	# that reading it inside the impersonation window would raise for the reviewer. That was
	# inherited from the chat client and is **not what v16 does**: `Document.get_password`
	# delegates to `frappe.utils.password.get_decrypted_password`, which is a direct query
	# against `__Auth` with no permission check at all (verified against the version-16 tree,
	# 2026-08-17), and `get_cached_doc` does not check either.
	#
	# The ordering still matters. `frappe.set_user` moves the session for everything after it,
	# and the token cache is keyed on the session user. Keep configuration outside the window
	# and only the mint inside it. That is what `_call_as` does.
	return _call_as(reviewer, WORK_BREAKDOWN_PATH, payload, timeout)


def request_description_draft(
	*,
	user: str,
	payload: dict[str, Any],
	timeout: int = DRAFT_TIMEOUT,
) -> dict[str, Any]:
	"""Expand a one-liner into a description, **as ``user``** — the person filing the request.

	Same transport and identity rules as :func:`request_breakdown`; only the path and the
	caller differ. This one runs for any signed-in employee rather than a System Manager,
	which is fine: the bridge auto-provisions a Triton user on first mint.

	Returns Triton's ``{description, model, usage}``. Persists nothing on either side.
	"""
	return _call_as(user, DRAFT_DESCRIPTION_PATH, payload, timeout)


def _call_as(user: str, path: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
	"""Shared body of both calls: read config, assume the identity, post, restore.

	Extracted rather than duplicated because the identity handling is the part that is easy to
	get subtly wrong, and two copies means one of them eventually loses the ``finally``.
	"""
	from erpnext_enhancements import triton_chat

	# Configuration is read BEFORE assuming anybody's identity — see request_breakdown.
	settings = triton_chat.get_settings()
	base_url = (settings.get("base_url") or "").strip()
	if not base_url:
		raise TritonUnavailable("Triton Settings has no Gateway URL, so there is nothing to ask.")
	if not settings.get("gateway_secret"):
		raise TritonUnavailable("Triton Settings has no Admin Webhook Secret, so no token can be minted.")

	previous = frappe.session.user
	try:
		# The token cache is keyed on the session user and this may run in a background job
		# whose session is Administrator. See the module docstring.
		frappe.set_user(user)
		return _post(base_url, path, payload, timeout=timeout)
	finally:
		# Restored on every path including the exception one.
		if previous:
			frappe.set_user(previous)


def _post(base_url: str, path: str, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
	"""One authed POST. Status and error *code* only — never the body.

	The body echoes the request text back, which is an employee's own words about something
	that annoyed them, sometimes about a coworker. An exception message is the least
	controlled string in the system: it reaches the Error Log, and from there a screenshot.
	"""
	import requests

	from erpnext_enhancements import triton_chat

	try:
		token = triton_chat.mint_user_token()
	except Exception as exc:
		# The class only. A mint failure is an outage of this integration rather than a bug in
		# the caller, and naming it as one saves the hour that "the model was unavailable"
		# costs when the real cause is a permission problem.
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
		body = response.json()
	except Exception:
		raise TritonUnavailable("Triton returned a body that is not JSON") from None

	if not isinstance(body, dict):
		raise TritonUnavailable("Triton returned a JSON body that is not an object") from None
	return body


def _error_fields(response: Any) -> tuple[str, str]:
	"""``code`` and ``request_id`` off an error body, and **nothing else, ever**.

	The rule is not "never repeat a response body" — it is *repeat only what a type
	guarantees is content-free*. ``message`` fails that test: it is ``str(exc)`` at the other
	end and can be any string the failing code chose. ``code`` and ``request_id`` pass it: an
	enum Triton picked from a fixed list, and a uuid. Both are type-checked and truncated
	rather than trusted, so a server that put a dict where the enum goes cannot smuggle
	anything through this seam.
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

	``request_id`` is included because it is the only handle tying this failure to the line in
	Triton's own logs — without it, correlating the two sides means guessing by timestamp
	across a UTC/site-local boundary this repository has already been bitten by.
	"""
	detail = f"Triton returned HTTP {status} for {path}"
	if code:
		meaning = _ERROR_CODE_MEANINGS.get(code)
		detail += f" ({code}: {meaning})" if meaning else f" ({code})"
	if upstream_request_id:
		detail += f" [Triton request {upstream_request_id}]"
	return detail
