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
from frappe.utils import add_to_date, escape_html, get_url, now_datetime, validate_email_address

from erpnext_enhancements import email_style
from erpnext_enhancements.feature_flags import throw_if_contract_esign_disabled
from erpnext_enhancements.project_enhancements.esign.providers import (
	configured_provider_key,
	get_provider,
)
from erpnext_enhancements.project_enhancements.esign.tokens import text_fingerprint

SEND_ROLES = ("System Manager", "Projects Manager", "Sales Manager")
SENDABLE_CONTRACT_STATUSES = ("Draft", "Out for Signature")
LIVE_REQUEST_STATUSES = ("Sent", "Viewed")
# How long a link opened for in-person signing stays usable. It is meant for the table it
# was opened at; "Send for Signature" re-issues it by email with the normal expiry.
IN_PERSON_LINK_HOURS = 12

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
	# Stable anchor for "how long has this been outstanding" — sent_on moves with
	# every resend and every reminder, so it cannot answer that question.
	request.first_sent_on = request.sent_on
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

	# A resend is always an email: a request first opened in person and then sent on for the
	# client to finish at their own pace is no longer being presented by anyone.
	request.signing_channel = "Email Link"
	request.presented_by = None

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
@rate_limit(limit=60, seconds=3600, methods=["POST"])
def start_in_person_signing(project_contract, signer_email=None):
	"""Open the signing page on this device, for a client who is here to sign now.

	Everything that makes an emailed signature trustworthy still applies, because it is
	the same request, the same single-use token and the same /contract-sign page: the
	client re-types their own address, the bot check runs, the snapshot is frozen and
	hashed, and the executed copy is emailed to them afterwards. Only the delivery
	differs. Nothing is emailed now; the link comes back once, to the staff member who
	asked (who could equally have sent it to themselves); and the request records that it
	was signed in person and who presented it, which the completion certificate prints.

	The link is short-lived (:data:`IN_PERSON_LINK_HOURS`): it exists to be used at the
	table. If the client would rather read it at home, "Send for Signature" re-issues it
	by email with the normal expiry and retires this one.

	Returns ``{"request", "url", "expires_on", "to"}``. A live request for the contract
	(emailed or in person) is reused with a fresh token, so there is never more than one
	live link.
	"""
	throw_if_contract_esign_disabled()
	frappe.only_for(SEND_ROLES)

	contract = frappe.get_doc("Project Contract", project_contract)
	contract.check_permission("submit")
	recipient = _valid_single_email(signer_email or contract.get("contact_email"))
	request, result = open_in_person_request(contract, recipient)

	contract.add_comment(
		"Comment",
		_("Opened for signing in person by {0} ({1}); the link works until {2}.").format(
			escape_html(frappe.utils.get_fullname(frappe.session.user)),
			escape_html(recipient),
			frappe.utils.format_datetime(request.expires_on),
		),
	)
	frappe.db.commit()
	return {
		"request": request.name,
		"to": recipient,
		"expires_on": request.expires_on,
		"url": result["signing_url"],
	}


def open_in_person_request(contract, recipient):
	"""The contract's live request, opened (or re-opened) for signing in person: ``(request, result)``.

	Freezes the snapshot the signer will see and mints a fresh single-use token, reusing a
	live request rather than adding a second one. Shared by the in-person signing tab
	(:func:`start_in_person_signing`) and the Contract Builder's own signing
	(:func:`sign_in_person`). Does not commit: each caller decides what the transaction is.
	"""
	_assert_sendable(contract)
	_assert_template_can_show_a_signature(contract)
	body = contract.render_body()

	existing = frappe.db.get_value(
		"Contract Signature Request",
		{"project_contract": contract.name, "status": ["in", LIVE_REQUEST_STATUSES]},
		"name",
	)
	if existing:
		request = frappe.get_doc("Contract Signature Request", existing)
		request.signer_email = recipient
		request.document_snapshot = body
		request.document_hash = text_fingerprint(body)
		request.contract_revision = contract.get("revision") or 0
		result = get_provider(request.provider).refresh_request(request, contract)
		request.resend_count = (request.resend_count or 0) + 1
		request.email_attempts = 0
	else:
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
		request.provider_reference = result.get("provider_reference")
		request.provider_status = result.get("provider_status")

	request.status = "Sent"
	request.sent_on = now_datetime()
	request.first_sent_on = request.get("first_sent_on") or request.sent_on
	request.signing_channel = "In Person"
	request.presented_by = frappe.session.user
	request.expires_on = add_to_date(request.sent_on, hours=IN_PERSON_LINK_HOURS)
	request.save(ignore_permissions=True)

	# Same edge as an emailed send, for the same reason: no document events here.
	if contract.status == "Draft":
		contract.db_set("status", "Out for Signature", update_modified=False)
	return request, result


def sign_in_person(contract, recipient, *, mode, signed_name, signed_title="", signature_image="", initials=None):
	"""Execute ``contract`` with a signature captured in the Contract Builder, the client beside you.

	Nik, 2026-10-13: "let them initial and sign as it gets filled out with information."
	The client initials each clause in the builder's live preview as it is completed and
	signs at the end, on the staff member's own signed-in device. This is the same
	evidence path as the signing page, minus the two checks that exist because the signer
	is somewhere else: the re-typed email address (the address here is where the signed
	copy goes, entered at the table) and the bot check (the session is a staff login).
	What replaces them is the request recording that it was signed in person and who
	presented it, which the completion certificate prints, as it does for the in-person tab.

	* The snapshot is frozen here, from the issued contract, exactly as for a link.
	* ``initials`` is ``{key: {"image": PNG data URI, "clause": text}}``. Every box in the
	  snapshot must be initialed, each set drawn separately (``validate_initials``, the
	  signing page's own rule), and beside the wording the snapshot has: a clause that
	  changed after its initials were drawn is returned in ``redo`` and nothing is signed.
	* The contract row is re-read under a lock before execution, as on the signing page.

	Returns ``{"ok": True, "request"}``, or ``{"ok": False, "error", "redo"}`` having signed
	nothing. The caller owns the transaction: on a refusal it rolls back, so an issue made
	in the same request is undone with it.
	"""
	from erpnext_enhancements.project_enhancements.esign import initials as initials_module
	from erpnext_enhancements.project_enhancements.esign import lifecycle, portal
	from erpnext_enhancements.project_enhancements.esign.lifecycle import SignatureEvidence

	request, _result = open_in_person_request(contract, recipient)
	drawn = initials if isinstance(initials, dict) else {}

	redo = initials_module.stale_initials(drawn, request.document_snapshot)
	if redo:
		return {
			"ok": False,
			"redo": redo,
			"error": _("The wording beside some initials changed after they were drawn. Please initial those clauses again."),
		}
	images = {key: (entry or {}).get("image") for key, entry in drawn.items() if isinstance(entry, dict)}
	captured, error = portal._validated_initials(images, request.document_snapshot)
	if error:
		missing = [key for key, _label in initials_module.initial_slots(request.document_snapshot) if not images.get(key)]
		return {"ok": False, "error": error, "redo": missing}

	settings = frappe.get_cached_doc("ERPNext Enhancements Settings")
	evidence = SignatureEvidence(
		mode=mode,
		signed_name=signed_name,
		signed_title=signed_title,
		signature_image=signature_image,
		email_confirmed=0,
		ip_claimed=frappe.local.request_ip,
		ip_peer=getattr(getattr(frappe, "request", None), "remote_addr", None),
		user_agent=frappe.get_request_header("User-Agent"),
		consent_text=settings.get("contract_esign_consent_text") or "",
		disclosure_text=settings.get("contract_esign_disclosure_text") or "",
		provider_status="Signed",
		initials=captured,
	)

	locked = frappe.db.get_value(
		"Project Contract", contract.name, ["docstatus", "status"], as_dict=True, for_update=True
	)
	if not locked or locked.docstatus != 1 or locked.status not in lifecycle.SIGNABLE_CONTRACT_STATUSES:
		return {"ok": False, "error": _("This agreement is no longer available to sign."), "redo": []}

	contract.add_comment(
		"Comment",
		_("Signed in person in the Contract Builder, presented by {0}; the signed copy goes to {1}.").format(
			escape_html(frappe.utils.get_fullname(frappe.session.user)), escape_html(recipient)
		),
	)
	lifecycle.execute_contract(request, evidence)
	return {"ok": True, "request": request.name}


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
		message=email_style.render(
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
			title=_("Please sign your agreement"),
			eyebrow=_contract_label(contract),
			preheader=_("Your agreement is ready to review and sign."),
			tagline=True,
			pillar="build",
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
