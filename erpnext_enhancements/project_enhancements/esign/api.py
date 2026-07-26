# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Desk endpoints for sending a contract out to be signed.

Authenticated and role-gated. The guest half of the flow lives in
:mod:`~erpnext_enhancements.project_enhancements.esign.portal`.

``@frappe.whitelist()`` stays **outermost** on every endpoint: it registers the
function object by identity, so a decorator applied above it would whitelist the
inner function and leave the exposed one unroutable.
"""

import re

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import escape_html, get_url, now_datetime, validate_email_address

from erpnext_enhancements.feature_flags import throw_if_contract_esign_disabled
from erpnext_enhancements.project_enhancements.esign.providers import (
	configured_provider_key,
	get_provider,
)
from erpnext_enhancements.project_enhancements.esign.tokens import text_fingerprint

SEND_ROLES = ("System Manager", "Projects Manager", "Sales Manager")
SENDABLE_CONTRACT_STATUSES = ("Draft", "Out for Signature")
LIVE_REQUEST_STATUSES = ("Sent", "Viewed")

INVITE_TEMPLATE = (
	"erpnext_enhancements/templates/emails/project_enhancements/contract_signature_invite.html"
)


@frappe.whitelist()
@rate_limit(limit=60, seconds=3600, methods=["POST"])
def send_for_signature(project_contract, recipient_email=None, message=None):
	"""Email a signing link for a submitted contract.

	Returns ``{"request", "to", "expires_on", "url"}``. The URL is returned once,
	for a copy-to-clipboard action — it embeds the token and is never stored.
	"""
	throw_if_contract_esign_disabled()
	frappe.only_for(SEND_ROLES)

	contract = frappe.get_doc("Project Contract", project_contract)
	contract.check_permission("submit")
	_assert_sendable(contract)
	_assert_template_can_show_a_signature(contract)

	recipient = _valid_single_email(recipient_email or contract.get("contact_email"))

	# A double-click must not mint two live tokens for the same document.
	existing = frappe.db.get_value(
		"Contract Signature Request",
		{
			"project_contract": contract.name,
			"status": ["in", LIVE_REQUEST_STATUSES],
		},
		"name",
	)
	if existing:
		return resend_for_signature(existing, recipient_email=recipient, message=message)

	body = contract.render_body()
	request = frappe.get_doc(
		{
			"doctype": "Contract Signature Request",
			"project_contract": contract.name,
			"contract_revision": contract.get("revision") or 0,
			"status": "Draft",
			"provider": configured_provider_key(),
			"signer_email": recipient,
			"signer_name": contract.get("contact_person") or contract.get("party_display"),
			"document_snapshot": body,
			"document_hash": text_fingerprint(body),
			"template_body_hash": text_fingerprint(
				frappe.db.get_value("Contract Template", contract.contract_template, "body") or ""
			),
			"sent_by": frappe.session.user,
		}
	).insert(ignore_permissions=True)

	result = get_provider(request.provider).create_request(request, contract)
	request.status = "Sent"
	request.sent_on = now_datetime()
	request.provider_reference = result.get("provider_reference")
	request.provider_status = result.get("provider_status")
	request.save(ignore_permissions=True)

	_email_invite(request, contract, result["signing_url"], message)

	# The contract moves to "Out for Signature" if it is still Draft. db_set is
	# correct HERE and nowhere else in this feature: the automation chain keys on
	# the edge into *Signed*, so this transition must not fire document events.
	if contract.status == "Draft":
		contract.db_set("status", "Out for Signature", update_modified=False)

	contract.add_comment(
		"Comment",
		_("Sent for signature to {0} (link expires {1}).").format(
			escape_html(recipient), frappe.utils.format_datetime(request.expires_on)
		),
	)
	frappe.db.commit()
	return {
		"request": request.name,
		"to": recipient,
		"expires_on": request.expires_on,
		"url": result["signing_url"],
	}


@frappe.whitelist()
@rate_limit(limit=60, seconds=3600, methods=["POST"])
def resend_for_signature(request_name, recipient_email=None, message=None, reset_attempts=True):
	"""Issue a fresh link, retiring the previous one.

	A resend **supersedes**: the stored hash is replaced, so the link in the older
	email stops working. The plaintext of the old token cannot be recovered from
	its hash even if we wanted to keep it alive, and one live bearer credential is
	a better posture than several accumulating over a 30-day window. Someone who
	opens the older email gets an explanation and a self-service "send me a new
	one", not a dead end.

	``reset_attempts`` clears the persisted email-confirmation counter, which is
	what actually makes the lockout notice's "re-send it if the customer needs
	another try" true — without it a capped request stays capped forever, and the
	field is read-only so nobody could clear it from the desk either. It defaults
	to True because a resend is a deliberate staff act; the **self-service** path
	passes False, so a link-holder cannot farm fresh guesses by asking for a new
	link.
	"""
	throw_if_contract_esign_disabled()
	frappe.only_for(SEND_ROLES)

	request = frappe.get_doc("Contract Signature Request", request_name)
	if request.status in ("Signed", "Declined", "Void"):
		frappe.throw(
			_("This request is already {0} — start a new one instead.").format(request.status),
			title=_("Cannot Resend"),
		)
	if request.status == "Locked" and not frappe.utils.cint(reset_attempts):
		frappe.throw(
			_("This link was paused after too many failed confirmations. A staff resend is needed."),
			title=_("Cannot Resend"),
		)

	contract = frappe.get_doc("Project Contract", request.project_contract)
	contract.check_permission("submit")
	_assert_sendable(contract)
	_assert_template_can_show_a_signature(contract)

	if recipient_email:
		request.signer_email = _valid_single_email(recipient_email)

	# Re-snapshot: the customer must sign what the agreement says NOW, not what it
	# said when the first link went out.
	body = contract.render_body()
	request.document_snapshot = body
	request.document_hash = text_fingerprint(body)
	request.contract_revision = contract.get("revision") or 0

	result = get_provider(request.provider).refresh_request(request, contract)
	request.status = "Sent"
	request.sent_on = now_datetime()
	request.resend_count = (request.resend_count or 0) + 1
	if frappe.utils.cint(reset_attempts):
		# A deliberate staff resend is a fresh start: it clears the confirmation
		# lockout AND restarts the reminder schedule, so a contract someone chased
		# to exhaustion can be chased again after a phone call. The automated
		# reminder path passes False and therefore does neither.
		request.email_attempts = 0
		request.reminders_sent = 0
		request.stale_alerted = 0
	request.save(ignore_permissions=True)

	_email_invite(request, contract, result["signing_url"], message)
	contract.add_comment(
		"Comment",
		_("Signing link re-sent to {0} (the previous link no longer works).").format(
			escape_html(request.signer_email)
		),
	)
	frappe.db.commit()
	return {
		"request": request.name,
		"to": request.signer_email,
		"expires_on": request.expires_on,
		"url": result["signing_url"],
	}


@frappe.whitelist()
def void_signature_request(request_name, reason=None):
	"""Kill a live signing link (wrong address, superseded terms, changed mind)."""
	throw_if_contract_esign_disabled()
	frappe.only_for(SEND_ROLES)

	request = frappe.get_doc("Contract Signature Request", request_name)
	if request.status == "Signed":
		frappe.throw(_("This contract has already been signed."), title=_("Cannot Void"))

	frappe.get_doc("Project Contract", request.project_contract).check_permission("submit")
	get_provider(request.provider).void_request(request, reason)

	request.status = "Void"
	request.voided_on = now_datetime()
	request.voided_by = frappe.session.user
	request.void_reason = (reason or "")[:500]
	request.token_hash = None  # the link stops resolving immediately
	request.save(ignore_permissions=True)
	frappe.db.commit()
	return {"request": request.name, "status": request.status}


@frappe.whitelist()
def get_signature_state(project_contract):
	"""Summary of the latest signing request, for the form banner.

	Never returns the token — it is not stored, and the signing URL is only ever
	handed back in the response to an explicit send/resend by an authorised user.
	"""
	contract = frappe.get_doc("Project Contract", project_contract)
	contract.check_permission("read")
	row = frappe.db.get_value(
		"Contract Signature Request",
		{"project_contract": project_contract},
		[
			"name",
			"status",
			"signer_email",
			"sent_on",
			"expires_on",
			"first_viewed_on",
			"view_count",
			"signed_on",
			"signed_name",
			"resend_count",
			"document_drifted",
		],
		order_by="creation desc",
		as_dict=True,
	)
	return row or {}


# ---------------------------------------------------------------------------
# Guards and helpers
# ---------------------------------------------------------------------------


def _assert_sendable(contract):
	if contract.docstatus != 1:
		frappe.throw(
			_("Submit the contract before sending it for signature."), title=_("Not Submitted")
		)
	if contract.status not in SENDABLE_CONTRACT_STATUSES:
		frappe.throw(
			_("This contract is {0} and cannot be sent for signature.").format(contract.status),
			title=_("Cannot Send"),
		)


def _assert_template_can_show_a_signature(contract):
	"""Refuse to send a contract whose live template cannot print a signature.

	``seed_contract_templates`` is insert-only and the ``add_esign_signature_block``
	patch refuses to guess at a body someone rewrote, so a site can legitimately end
	up with a template that has no ``sig(`` in it. Without this check that site
	would collect a real, binding signature and produce a PDF with an empty
	signature line — the worst possible failure. Three lines turn it into a blocked
	send with an explanation.
	"""
	body = frappe.db.get_value("Contract Template", contract.contract_template, "body") or ""
	if "sig(" not in body:
		frappe.throw(
			_(
				"The template for this contract has no e-signature block, so a signed copy "
				"would print a blank signature line. Add the signature block to the "
				"<b>{0}</b> Contract Template (copy it from the shipped template), then send again."
			).format(escape_html(contract.contract_template)),
			title=_("Template Not Ready"),
		)


def _valid_single_email(value):
	"""Exactly one address.

	``validate_email_address`` is an extractor, not a predicate: it comma-splits
	and returns the valid parts joined, so "a@b.com, evil@x.com" would sail through
	a truthiness check and become two recipients — here, two people who could each
	execute the contract.
	"""
	email = (value or "").strip()
	if not email or re.search(r"[,;\s]", email):
		frappe.throw(
			_("This contract needs a single Contact Email before it can be sent for signature."),
			title=_("Recipient Needed"),
		)
	if validate_email_address(email) != email:
		frappe.throw(_("Enter a valid email address."), title=_("Recipient Needed"))
	return email.lower()


def _email_invite(request, contract, signing_url, message=None):
	"""Send the signing invitation. Raises — a send failure must not look like a
	success to the person who pressed the button."""
	frappe.sendmail(
		recipients=[request.signer_email],
		subject=_("Please sign: {0}").format(_contract_label(contract)),
		message=frappe.render_template(
			INVITE_TEMPLATE,
			{
				"recipient_name": request.signer_name,
				"contract_label": _contract_label(contract),
				"signing_url": signing_url,
				"expires_on": frappe.utils.format_datetime(request.expires_on),
				"note": (message or "").strip()[:2000],
				"company": _company_name(),
				"site_url": get_url(),
			},
		),
		reference_doctype="Contract Signature Request",
		reference_name=request.name,
	)


def _contract_label(contract):
	return (
		frappe.db.get_value("Contract Template", contract.contract_template, "title")
		or contract.get("title")
		or contract.name
	)


def _company_name():
	return frappe.defaults.get_global_default("company") or "Sapphire Fountains"
