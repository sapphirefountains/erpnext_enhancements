# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the public contract signing page (``/contract-sign``).

**The filename matters.** Frappe derives this module from the *template's*
basename with hyphens turned into underscores, so ``contract-sign.html`` needs
``contract_sign.py``. A hyphenated controller is never imported, ``get_context``
silently never runs, and the template still renders with every variable
undefined — a failure mode this app has shipped before. ``scripts/check_www_controllers.py``
enforces it in CI.

Two deliberate departures from the sibling page (``www/fountain_move.py``):

* **An unresolvable token does not 404.** There, a bare visit is legitimate and a
  missing token means nothing. Here the link *is* the invitation, so a customer
  clicking a 31-day-old email deserves an explanation and a way out, not a dead
  end. The 404 is reserved for the feature being switched off — probing a route
  gets nothing. Unknown and malformed tokens are rendered identically, so the
  page never confirms whether a token exists.
* **The agreement is rendered from the stored snapshot**, never a live re-render,
  so the customer signs exactly the text that was sent to them and the stored
  hash provably matches what was on screen.
"""

import frappe

from erpnext_enhancements.feature_flags import contract_esign_public_page_enabled
from erpnext_enhancements.project_enhancements import contract_style
from erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract import (
	_contract_css,
)
from erpnext_enhancements.project_enhancements.esign import lifecycle, turnstile
from erpnext_enhancements.utils.deploy import get_deploy_version

# A cached signing page would be catastrophic in a way most caching bugs are not:
# it would serve one customer's agreement, and their signing link, to another.
# Belt (module-level, for the renderer) and braces (per-render, on the response).
no_cache = 1


def get_context(context):
	if not contract_esign_public_page_enabled():
		raise frappe.DoesNotExistError

	context.no_cache = 1
	context.deploy_version = get_deploy_version()

	ref = frappe.form_dict.get("ref")
	request = lifecycle.resolve_request(ref)

	state, agreement_html = _state_for(request)
	context.state = state
	# The agreement carries the same letterhead and the same stylesheet the desk
	# print and the emailed PDF use, so the customer signs the document they will
	# later receive — not a plainer web rendering of it. The chrome wraps the
	# snapshot without touching it; the snapshot itself is still byte-for-byte
	# what was sent, which is what the stored hash attests to.
	context.agreement_html = (
		contract_style.wrap(agreement_html, _contract_ref(request)) if agreement_html else ""
	)
	context.contract_css = _safe_css(_contract_css())
	context.boot = _boot(request, state, ref)
	return context


def _contract_ref(request):
	"""Just enough of the contract for the footer to name it."""
	name = request.project_contract if request else None
	return frappe._dict({"name": name}) if name else None


def _safe_css(css):
	"""The print format's CSS, with any chance of escaping its <style> removed.

	The desk viewer publishes the same stylesheet through ``style.textContent``,
	where markup cannot escape. Here it goes into the page as raw HTML, so a
	``</style>`` inside it would end the element and everything after it would
	parse as content — on a **public, unauthenticated** page. ``<`` has no
	legitimate use in CSS, so dropping it costs nothing and closes the hole
	without trusting whoever last edited the Print Format record.
	"""
	return (css or "").replace("<", "")


def _state_for(request):
	"""Map a resolved request to what the visitor should see.

	Distinguishing expired / signed / declined / replaced is a kindness to a real
	customer and leaks nothing: they already hold the link. Only *unknown* and
	*malformed* must be indistinguishable, and both arrive here as ``None``.
	"""
	if not request:
		return "invalid", ""
	# Opened from an email whose link a resend has since replaced.
	if request.flags.get("opened_with_superseded_link"):
		return "replaced", ""
	if request.status == "Signed":
		return "signed", ""
	if request.status == "Declined":
		return "declined", ""
	if request.status == "Superseded":
		return "replaced", ""
	if request.status in ("Void", "Locked") or lifecycle.request_expired(request):
		return "expired", ""
	if not lifecycle.is_signable(request):
		return "unavailable", ""

	# Track the open in the background: a GET is not an unsafe method, so frappe
	# does not commit the request transaction and an inline write would be rolled
	# back on the way out.
	frappe.enqueue(
		"erpnext_enhancements.project_enhancements.esign.lifecycle.mark_viewed",
		queue="short",
		request_name=request.name,
	)
	return "signable", request.document_snapshot or ""


def _boot(request, state, ref):
	"""Explicit allowlist of what the page's JavaScript may see.

	There is deliberately no config endpoint, and deliberately no full recipient
	address: the whole identity check is "re-type the address we sent this to", so
	shipping it to the page would defeat the control for anyone holding a
	forwarded link. The mask tells a customer with several addresses which one to
	use without disclosing it.
	"""
	settings = frappe.get_cached_doc("ERPNext Enhancements Settings")
	return {
		# A Guest session carries no CSRF token, so validation short-circuits and
		# nothing needs sending. A LOGGED-IN staff member previewing this page does
		# have one, and a POST without the header would be a 400. Emit the existing
		# token — never frappe.sessions.get_csrf_token(), which mints one that a
		# Guest session never persists.
		"csrf_token": (getattr(frappe.session, "data", None) or {}).get("csrf_token") or "",
		"state": state,
		"ref": ref or "",
		"turnstile_sitekey": turnstile.site_key() or "",
		"turnstile_action": turnstile.TURNSTILE_ACTION,
		"contact_phone": _contact_phone(),
		"signer_email_hint": _mask(request.signer_email) if request else "",
		"contract_label": _label(request) if request else "",
		"expires_on": frappe.utils.format_datetime(request.expires_on) if request else "",
		"signed_on": frappe.utils.format_datetime(request.signed_on) if request and request.signed_on else "",
		"consent_text": settings.get("contract_esign_consent_text") or "",
		"disclosure_text": settings.get("contract_esign_disclosure_text") or "",
		"company": frappe.defaults.get_global_default("company") or "Sapphire Fountains",
	}


def _mask(email):
	email = (email or "").strip()
	if "@" not in email:
		return ""
	local, _, domain = email.partition("@")
	return f"{local[:1]}{'•' * max(len(local) - 1, 1)}@{domain}"


def _label(request):
	try:
		contract = frappe.db.get_value(
			"Project Contract", request.project_contract, ["contract_template", "title"], as_dict=True
		)
		if not contract:
			return request.project_contract
		return (
			frappe.db.get_value("Contract Template", contract.contract_template, "title")
			or contract.title
			or request.project_contract
		)
	except Exception:
		return request.project_contract


def _contact_phone():
	try:
		from erpnext_enhancements.crm_enhancements.fountain_move import get_contact_phone

		return get_contact_phone()
	except Exception:
		return ""
