# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The one whitelisted method chat retrieval is reachable by. **One, deliberately.**

Triton reaches ERPNext over a **per-user OAuth2 bearer**, which Frappe resolves to
``frappe.session.user`` — so a whitelisted method called over that bearer runs *as the human
who typed the mention*, and every permission decision is made in the process that owns the
membership table. That is the whole reason retrieval lives here rather than in Triton: moving
the index out would move the filter out.

**No ``user`` parameter, and that is the point.** The acting person is the session, full stop.
A ``user`` argument on a whitelisted method is an impersonation parameter however carefully it
is documented, and the caller here is a model-driven turn assembling arguments from text.

**No ``allowed_rooms`` parameter either** — see :mod:`chat.retrieval.gate`. ``restrict_to``
exists and is intersected with the derived set, so it can only narrow.

What comes back is the assembled context and the citation manifest, not rows. There is no
shape of this response that hands over a raw ``Chat Message``, which keeps "a deleted row
cannot leak its text" structural rather than a rule this endpoint has to remember.
"""

from __future__ import annotations

from typing import Any

import frappe

from erpnext_enhancements.chat.api import _common
from erpnext_enhancements.chat.retrieval import gate


@frappe.whitelist()
def get_chat_context(
	query: str,
	room: str | None = None,
	thread_root: str | None = None,
	restrict_to: Any = None,
	request_id: str | None = None,
) -> dict[str, Any]:
	"""Assembled chat context for the **calling** user's rooms.

	``require_session`` first, like every other endpoint in this app's chat surface: it
	resolves the caller, refuses ``Guest``, and enforces the pilot whitelist. The gate then
	refuses ``Administrator`` separately, because a whitelisted method *can* be called by
	Administrator and "every room" would be the literal answer to the room-set question.

	A refusal is returned as a payload rather than thrown, because the caller is a model turn:
	a 417 with a Frappe error page becomes "Communication disruption detected" in the client,
	while ``{"refused": true, "reason": …}`` becomes a sentence the person can act on.
	"""
	user = _common.require_session()

	try:
		result = gate.retrieve(
			query=query or "",
			user=user,
			room=room or None,
			thread_root=thread_root or None,
			restrict_to=_coerce_rooms(restrict_to),
			purpose="mention",
			request_id=request_id,
		)
	except gate.RetrievalRefused as exc:
		return {"refused": True, "reason": str(exc)}

	return {
		"refused": False,
		"context": result.assembly.text() if result.assembly else "",
		"citations": [entry.as_dict() for entry in result.manifest],
		"context_truncated": result.context_truncated,
		"degradation_rung": result.degradation_rung,
		"context_tokens": result.context_tokens,
		"tiers_used": list(result.tiers_used),
		"rooms_searched": len(result.rooms_searched),
		"dropped_terms": list(result.dropped_terms),
	}


def _coerce_rooms(supplied: Any) -> list[str]:
	"""``frappe.xcall`` sends arrays as JSON strings on some paths and as lists on others.

	Modelled on ``chat.api.conversations._coerce_list`` rather than reinvented, because the
	two wire shapes are a framework fact rather than a quirk of one endpoint. Anything
	unparseable becomes ``[]``, which means "no narrowing" — the safe direction, since
	``restrict_to`` can only ever narrow.
	"""
	if not supplied:
		return []
	if isinstance(supplied, str):
		import json

		try:
			supplied = json.loads(supplied)
		except Exception:
			return []
	if isinstance(supplied, dict):
		supplied = list(supplied.values())
	if not isinstance(supplied, list | tuple):
		return []
	return [str(item).strip() for item in supplied if str(item or "").strip()]
