# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The "Check with Triton" button — a second opinion on the parts a rule cannot decide.

The deterministic check runs first and its findings are **sent to Triton as context**, so the
model is asked only for the judgements :mod:`item_naming_rules` explicitly refuses to make:
parsing a vendor description into the seven segments, choosing a CATEGORY, deciding two records
are the same physical part, and telling a swapped code/name from a merely wrong one. Everything
mechanical — case, separators, whitespace, block occupancy, collisions — is already answered
before the prompt is built, and the prompt says so. Asking a model to re-derive a fact a regex
just established is slower, costs a token budget, and produces a second answer that can
disagree with the first.

--------------------------------------------------------------------------------------
Why this file is thin: `triton_chat` is the seam, and it is already correct
--------------------------------------------------------------------------------------

There are two other Triton clients in this app and this is deliberately not a third.

:mod:`chat.invoke.triton_client` is a **background-job** client: it calls
``frappe.set_user`` unconditionally, which overwrites ``session.sid`` with the username and
destroys a live browser session. Calling it from a Desk request would log the user out — that
shipped as v1.325.0, where "Expand with AI" worked exactly once per login.
:mod:`product_feedback.triton_client` exists because the planning routes are *not*
session-scoped and because it has to impersonate a reviewer inside a job. Neither condition
holds here: this runs synchronously inside the user's own request, on the session-scoped
assistant routes, already as the right person.

So this module uses :func:`erpnext_enhancements.triton_chat._request`, the same seam the
Desk widget uses. It is private by name and public by role — every non-streaming Triton
response in the app passes through it — and it already does four things this button would
otherwise have to reimplement and eventually get wrong: it refuses when the assistant is
switched off, mints the token **under the current session user** (so identity needs no
handling at all here), retries once on a stale-token 401, and runs the response through
``scrub_urls``. It also throws with the status only and never the response body, which is the
rule that matters most: a Triton error ``message`` is ``str(exc)`` at the far end and can quote
the prompt back, and the prompt here contains the catalogue.

--------------------------------------------------------------------------------------
Sessions
--------------------------------------------------------------------------------------

Triton's assistant API is session-scoped — there is no session-less ``/query``. One session is
cached per user rather than created per click, so a person's Triton history does not fill with
one-line sessions. The cache key is **new** rather than shared with the chat client on purpose:
``ChatSession`` pins itself to the tool catalogue it started with, and a session created before
v1.335.0 predates ``item_naming_check``.

Nothing here writes to the Item, and nothing here is persisted on either side.
"""

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements import triton_chat
from erpnext_enhancements.inventory_enhancements import item_naming
from erpnext_enhancements.inventory_enhancements import item_naming_rules as rules

#: 30 days, matching the chat client's session cache. A session that has fallen out of Triton
#: comes back as a 404 and is recreated once — see :func:`_query`.
SESSION_TTL_SECONDS: int = 30 * 24 * 3600

#: The request timeout is NOT set here — `triton_chat._request` applies
#: `Triton Assistant Settings.request_timeout` (120s default) to every call, and a second
#: constant beside it would be a number that looks authoritative and changes nothing.
_SESSION_TITLE: str = "ERPNext — item naming"


def is_configured() -> bool:
	"""Can this button work at all? Read by :func:`naming_review_available`.

	A button that cannot work reads as a bug in the feature rather than as a missing setting,
	which is how the "Expand with AI" one was reported. The form hides it instead.
	"""
	try:
		settings = triton_chat.get_settings()
	except Exception:
		return False
	return bool(settings.get("enabled") and settings.get("base_url") and settings.get("gateway_secret"))


@frappe.whitelist()
def naming_review_available() -> dict:
	"""Whether to render the Triton button. Cheap, and safe for any signed-in user."""
	return {"available": is_configured()}


@frappe.whitelist(methods=["POST"])
def review_item(item_code=None, item_name=None, item_group=None, stock_uom=None, existing=False) -> dict:
	"""Deterministic check first, then Triton on what is left. Persists nothing.

	Returns ``{"verdict", "findings", "text"}`` — the local verdict and findings are the
	authoritative half and are returned even when Triton fails, so a model outage degrades this
	button to the deterministic check rather than to nothing.
	"""
	if not frappe.has_permission("Item", "read"):
		frappe.throw(_("You do not have read access to Items."), frappe.PermissionError)
	if not is_configured():
		frappe.throw(
			_("Triton is not configured — Triton Settings has no Gateway URL or secret, or the "
			  "assistant is switched off. The 'Check naming' button still works; it is the same "
			  "check without the suggestions."),
			frappe.ValidationError,
		)

	local = item_naming.check_item(
		item_code=item_code,
		item_name=item_name,
		item_group=item_group,
		stock_uom=stock_uom,
		mode="full",
		existing=existing,
	)

	try:
		text = _query(_prompt(local, item_code, item_name, item_group, stock_uom))
	except frappe.ValidationError:
		# triton_chat._request throws this with a status and never a body. Safe to re-raise:
		# the user sees "Triton error (503)" and still has the deterministic result below it.
		raise
	except Exception:
		# Anything else contributes its class and nothing more — an arbitrary exception's
		# message can carry the prompt, and the prompt is the catalogue.
		frappe.log_error(frappe.get_traceback(), "Item naming Triton review failed")
		frappe.throw(
			_("Triton could not be reached. The deterministic check above still stands; "
			  "nothing is blocked."),
			frappe.ValidationError,
		)

	return {
		"verdict": local.get("verdict"),
		"findings": local.get("findings") or [],
		"block": local.get("block"),
		"duplicates": local.get("duplicates"),
		"similar": local.get("similar") or [],
		"text": text,
	}


def _query(prompt: str) -> str:
	"""One turn on a cached session, recreating it once if Triton has forgotten it.

	The retry is bounded to a *cached* session id for the reason the chat client records: a
	freshly created session that 404s must not be retried, or a persistent fault mints one
	orphan session per click forever.
	"""
	key = f"item_naming:triton_session:{frappe.session.user}"
	cached = frappe.cache().get_value(key)

	session_id = cint(cached) if cached else _new_session(key)
	try:
		return _post_query(session_id, prompt)
	except frappe.ValidationError:
		if not cached:
			raise
		frappe.cache().delete_value(key)
		return _post_query(_new_session(key), prompt)


def _new_session(key: str) -> int:
	session = triton_chat.start_session(title=_SESSION_TITLE) or {}
	session_id = cint(session.get("id"))
	if not session_id:
		frappe.throw(_("Triton created a session with no id."), frappe.ValidationError)
	frappe.cache().set_value(key, session_id, expires_in_sec=SESSION_TTL_SECONDS)
	return session_id


def _post_query(session_id: int, prompt: str) -> str:
	body = triton_chat._request(
		"POST",
		f"/api/v1/assistant/sessions/{cint(session_id)}/query",
		{"prompt": prompt},
	) or {}
	# `content` — NOT `response` or `text`. Confirmed against Triton's own ChatMessage schema;
	# the chat client's docstring records that reading the wrong key produced an empty reply on
	# every turn while looking like the model had nothing to say.
	return str(body.get("content") or "").strip()


def _prompt(local: dict, item_code, item_name, item_group, stock_uom) -> str:
	"""Context first, then the ask — the ordering both other Triton callers use.

	The deterministic findings are declared authoritative in the prompt itself. Without that
	the model re-litigates them, and a confident second opinion that contradicts a regex is
	worse than no second opinion: the reader cannot tell which half to trust.
	"""
	reference = local.get("reference") or {}
	lines = [
		"You are reviewing a proposed ERPNext Item for Sapphire Fountains, against the",
		"ERPNext Item Naming Schema SOP v1.0.",
		"",
		"PROPOSED RECORD",
		f"  Item Code:  {item_code or '(none given)'}",
		f"  Item Name:  {item_name or '(none given)'}",
		f"  Item Group: {item_group or '(none given)'}",
		f"  Stock UOM:  {stock_uom or '(none given)'}",
		f"  Code family (determined): {local.get('family')}",
		"",
		"THE MECHANICAL CHECK HAS ALREADY RUN. Its verdict is "
		f"{local.get('verdict')}. These findings are AUTHORITATIVE — they come from executable",
		"rules, not from judgement. Do not re-derive them, do not dispute them, and do not",
		"repeat them back except where a fix you propose depends on one.",
	]

	findings = local.get("findings") or []
	if findings:
		for item in findings:
			lines.append(f"  [{item.get('severity')}] {item.get('code')}: {item.get('message')}")
	else:
		lines.append("  (none — the record is mechanically clean)")

	block = local.get("block")
	if block:
		free = ", ".join(str(n) for n in (block.get("free") or [])[:12])
		lines += [
			"",
			f"BLOCK OCCUPANCY for {block.get('prefix')}-{block.get('block_start')}"
			f"..{block.get('block_end')}",
			f"  taken: {sorted(block.get('occupied') or {})}",
			f"  free:  {free}",
			"  Blocks are semantic, not sequential. Which block a product belongs in is a",
			"  judgement about what the product is; never suggest max+1.",
		]

	similar = local.get("similar") or []
	if similar:
		lines += ["", "NEAREST EXISTING RECORDS (scored by rare shared tokens)"]
		for row in similar[:6]:
			lines.append(f"  {row.get('score')}  {row.get('item_code')}  {row.get('item_name')}")

	tier1 = reference.get("tier1") or []
	tier2 = reference.get("tier2") or []
	if tier1 or tier2:
		lines += [
			"",
			"APPROVED CATEGORIES (Appendix A — the ONLY admissible first segment).",
			"A category outside this list is a STOP for the Process Owner, never a new category.",
			"  Tier 1: " + ", ".join(tier1),
			"  Tier 2: " + ", ".join(tier2),
		]

	lines += [
		"",
		"THE ITEM NAME SCHEMA is comma-delimited, ALL UPPERCASE, broadest first:",
		"  " + ", ".join(rules.SEGMENTS),
		"Omit a segment that carries no information rather than writing N/A.",
		"",
		"USER QUERY: Judge only what the mechanical check cannot. Specifically:",
		"  1. If a segment is missing or wrong, propose the corrected full Item Name.",
		"  2. Choose the CATEGORY from the approved list, and say which one and why.",
		"  3. Say whether any nearest record above is the SAME PHYSICAL PART under a different",
		"     code — that is the failure no normalisation can catch, and it is the main thing",
		"     you are here for.",
		"  4. If the code and the name disagree, say whether they look swapped or whether the",
		"     name is simply wrong.",
		"  5. Name anything you could not determine, rather than guessing a size, a material or",
		"     a rating. A blank is better than a plausible invention.",
		"Be brief. Do not write to ERPNext; you are advising a human who will type the result.",
	]
	return "\n".join(lines)
