# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Provider-agnostic contract-signature lifecycle.

Everything that happens *after* a signature is collected lives here, so it
happens identically however it was collected: our own signing page today, a
DocuSign webhook tomorrow. A provider normalises its result into
:class:`SignatureEvidence` and calls :func:`execute_contract`; this module knows
nothing about tokens, Turnstile or envelopes.

**The one rule that must never be optimised away** — :func:`execute_contract`
flips the contract with a real ``doc.save()``. ``hooks.py`` binds
``on_update_after_submit`` on Project Contract to
``autocreate_maintenance_contract_on_signed``; a ``frappe.db.set_value`` would
update the field and silently skip the entire downstream chain, so no operational
Maintenance Contract would ever be drafted and nothing would ever be scheduled or
billed. There is a test that asserts the draft appears.

**Transaction shape** — guards, then evidence, then the contract save, then ONE
commit, then best-effort delivery is enqueued:

    resolve + guards -> stamp the request Signed -> contract.save() -> commit
                     -> enqueue(deliver_signed_contract, after_commit)

This deliberately differs from ``_record_consent``, which commits its own row
inside a try/except. That is right for a best-effort side record; it is wrong
here. If the contract save fails, a committed signature would leave an
executed-but-not-Signed contract — strictly worse than rolling the whole thing
back and letting the customer press the button again.

Delivery (PDF, email, alerts) is enqueued because wkhtmltopdf is the slowest and
most failure-prone step in the stack. **A PDF failure must never void an executed
signature**: the stored ``agreement_html`` and its hash are the record, and
``tasks.retry_undelivered`` sweeps anything that did not land.
"""

from dataclasses import dataclass, field

import frappe
from frappe.utils import escape_html, get_datetime, getdate, now_datetime

from erpnext_enhancements import email_style
from erpnext_enhancements.project_enhancements.esign.tokens import hash_token, text_fingerprint

SIGNABLE_CONTRACT_STATUSES = ("Out for Signature",)


@dataclass
class SignatureEvidence:
	"""What a provider must hand back to execute a contract.

	Everything here is either the signer's deliberate act (mode, name, title) or
	server-observed (addresses, agent, verdicts). Nothing a client could assert
	about *itself* is trusted — see the ``scrolled_to_end`` field description on
	the doctype.
	"""

	mode: str = "Typed"
	signed_name: str = ""
	signed_title: str = ""
	signature_image: str = ""
	email_confirmed: int = 0
	ip_claimed: str = ""
	ip_peer: str = ""
	user_agent: str = ""
	turnstile: dict = field(default_factory=dict)
	consent_text: str = ""
	disclosure_text: str = ""
	review_seconds: int = 0
	scrolled_to_end: int = 0
	provider_reference: str = ""
	provider_status: str = ""
	provider_payload: str = ""


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_request(token):
	"""The Contract Signature Request for a signing token, or None.

	Never raises — this runs on the public render path, so a forwarded, stale or
	mangled link must show a page, not a traceback. The token is shape-checked
	before any database hit (``hash_token`` returns None for anything malformed),
	so an unknown token and a malformed one are indistinguishable from outside.
	"""
	try:
		token_hash = hash_token(token)
		if not token_hash:
			return None
		name = frappe.db.get_value("Contract Signature Request", {"token_hash": token_hash}, "name")
		if name:
			return frappe.get_doc("Contract Signature Request", name)
		# A link that was superseded by a resend: resolve it anyway so the page can
		# say "we sent you a newer one" instead of a dead end. The caller checks
		# status, and a superseded row is never signable.
		name = frappe.db.get_value(
			"Contract Signature Request", {"previous_token_hash": token_hash}, "name"
		)
		if not name:
			return None
		doc = frappe.get_doc("Contract Signature Request", name)
		doc.flags.opened_with_superseded_link = True
		return doc
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Contract e-sign: resolve failed")
		return None


def mark_viewed(request_name):
	"""Stamp that the signer opened the agreement.

	Enqueued from the page controller, never run inline: a GET is not an unsafe
	method, so frappe does not commit the request transaction and an inline write
	would be rolled back on the way out (the lesson ``mark_invite_opened`` records).
	"""
	try:
		row = frappe.db.get_value(
			"Contract Signature Request", request_name, ["status", "first_viewed_on", "view_count"], as_dict=True
		)
		if not row:
			return
		updates = {
			"last_viewed_on": now_datetime(),
			"view_count": (row.view_count or 0) + 1,
		}
		if not row.first_viewed_on:
			updates["first_viewed_on"] = now_datetime()
		if row.status == "Sent":
			updates["status"] = "Viewed"
		frappe.db.set_value("Contract Signature Request", request_name, updates, update_modified=False)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Contract e-sign: view tracking failed")


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def execute_contract(request, evidence):
	"""Record the signature and flip the contract to Signed.

	Caller is responsible for authorisation (token, identity, bot check) and for
	having verified the contract is signable. Everything here is provider-agnostic.

	Returns the saved Contract Signature Request.
	"""
	contract = frappe.get_doc("Project Contract", request.project_contract)
	contract.flags.ignore_permissions = True  # the caller may be Guest

	# 1. Drift: re-render the body while it is still unsigned (sig() resolves
	#    nothing yet) and compare to what the signer was shown. A mismatch means
	#    the template or a contract field changed while the link was out. We do
	#    NOT block — refusing here would cost the customer their signature over
	#    our own edit — but we flag it loudly for a human.
	drifted = 0
	try:
		if request.document_hash:
			drifted = 1 if text_fingerprint(contract.render_body()) != request.document_hash else 0
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Contract e-sign: drift check failed")

	signed_at = now_datetime()
	turnstile = evidence.turnstile or {}

	# 2. Stamp the evidence. Done BEFORE the re-render below so that sig()
	#    resolves this row and the signature lands in the executed instrument.
	request.status = "Signed"
	request.signature_mode = evidence.mode
	request.signed_name = evidence.signed_name
	request.signed_title = evidence.signed_title
	request.signature_image = evidence.signature_image or None
	request.signed_on = signed_at
	request.signed_at_utc = _utc_stamp()
	request.signed_tz = _site_timezone()
	request.email_confirmed = 1 if evidence.email_confirmed else 0
	request.signer_ip_claimed = evidence.ip_claimed
	request.signer_ip_peer = evidence.ip_peer
	request.user_agent = (evidence.user_agent or "")[:512]
	request.turnstile_verdict = turnstile.get("verdict") or "Not Checked"
	request.turnstile_hostname = turnstile.get("hostname")
	request.turnstile_action = turnstile.get("action")
	request.turnstile_challenge_ts = turnstile.get("challenge_ts")
	request.turnstile_errors = turnstile.get("errors")
	request.review_seconds = evidence.review_seconds or 0
	request.scrolled_to_end = 1 if evidence.scrolled_to_end else 0
	request.consent_text = evidence.consent_text
	request.consent_version = text_fingerprint(evidence.consent_text)
	request.esign_disclosure_text = evidence.disclosure_text
	request.esign_disclosure_version = text_fingerprint(evidence.disclosure_text)
	request.document_drifted = drifted
	request.provider_reference = evidence.provider_reference or request.provider_reference
	request.provider_status = evidence.provider_status or "Signed"
	if evidence.provider_payload:
		request.provider_payload = evidence.provider_payload[:65535]
	_apply_countersignature(request)
	request.save(ignore_permissions=True)

	# 3. Stamp the contract's own signature fields BEFORE rendering the executed
	#    instrument. The SIGNATURES block reads `fill(doc.signed_by)` and
	#    `dt(doc.signed_on)` straight off the contract, so rendering first would
	#    freeze a blank Print Name and Date into the very document the customer
	#    receives — permanently, because everything downstream prefers the stored
	#    copy. The save() itself stays below, so the automation chain is unaffected.
	contract.status = "Signed"
	contract.signed_by = evidence.signed_name
	contract.signed_on = getdate(signed_at)

	# 4. The executed instrument: the agreement re-rendered now that sig() resolves
	#    and the contract carries its signature fields, plus a completion
	#    certificate. Stored so the customer's copy, the attached copy and the desk
	#    print are one identical document that a later template edit cannot change.
	try:
		body = contract.render_body()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Contract e-sign: executed render failed")
		# A caught frappe.throw leaves its message queued; it must not surface in
		# an anonymous signer's response.
		frappe.clear_messages()
		body = request.document_snapshot or ""
		# The stored instrument is now the UNSIGNED text. The signature still
		# stands, but a human needs to look — reuse the drift flag, which the
		# staff alert already reports on.
		drifted = 1
	agreement_html = body + completion_certificate(request, contract)
	request.db_set("agreement_html", agreement_html, update_modified=False)
	request.db_set("agreement_hash", text_fingerprint(agreement_html), update_modified=False)
	request.db_set("document_drifted", drifted, update_modified=False)

	# 5. Flip the contract. A REAL save — see the module docstring. This fires
	#    on_update_after_submit -> autocreate_maintenance_contract_on_signed.
	contract.save()

	# Registered BEFORE the commit it rides on: enqueue_after_commit attaches the
	# job to the next commit, so committing first would hand delivery to whatever
	# commit happened to come later — and lose it entirely to an intervening
	# rollback.
	frappe.enqueue(
		"erpnext_enhancements.project_enhancements.esign.lifecycle.deliver_signed_contract",
		queue="short",
		enqueue_after_commit=True,
		request_name=request.name,
	)
	frappe.db.commit()
	return request


def _apply_countersignature(request):
	"""Record the Sapphire signer, if one is configured. Never gates anything."""
	name = frappe.db.get_single_value("ERPNext Enhancements Settings", "contract_esign_countersigner_name")
	if not name:
		return
	request.countersigned_by = name
	request.countersigned_title = frappe.db.get_single_value(
		"ERPNext Enhancements Settings", "contract_esign_countersigner_title"
	)
	request.countersigned_on = getdate(now_datetime())


def completion_certificate(request, contract=None):
	"""The signature evidence, rendered for the executed document.

	Everything interpolated is escaped: the signer's typed name reaches this from
	an anonymous request, and the output is read in the desk and rendered through
	wkhtmltopdf.
	"""
	rows = [
		(frappe._("Signed by"), request.signed_name),
		(frappe._("Title"), request.signed_title),
		(frappe._("Email"), request.signer_email),
		(frappe._("Method"), frappe._("Drawn signature") if request.signature_mode == "Drawn" else frappe._("Typed signature")),
		(frappe._("Signed at"), f"{request.signed_on} ({request.signed_tz or ''})"),
		(frappe._("IP address"), request.signer_ip_peer),
		(frappe._("Reference"), request.name),
	]
	cells = "".join(
		f"<tr><td><b>{escape_html(str(label))}</b></td><td>{escape_html(str(value or ''))}</td></tr>"
		for label, value in rows
	)
	return (
		'<div class="ct-certificate">'
		f"<h1>{escape_html(frappe._('CERTIFICATE OF ELECTRONIC SIGNATURE'))}</h1>"
		f"<p>{escape_html(frappe._('This agreement was executed electronically in accordance with its Electronic Signatures clause. The record below was captured at the moment of signing.'))}</p>"
		f'<table class="ct-table">{cells}</table>'
		"</div>"
	)


def _utc_stamp():
	from datetime import datetime, timezone

	return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _site_timezone():
	try:
		return frappe.utils.get_system_timezone()
	except Exception:
		return None


# ---------------------------------------------------------------------------
# Delivery (best-effort, retried — never able to void a signature)
# ---------------------------------------------------------------------------


def deliver_signed_contract(request_name):
	"""Build the signed PDF, attach it, email the customer, alert staff.

	Idempotent: ``delivered_on`` is stamped once the customer has their copy, so
	``tasks.retry_undelivered`` can call this freely without double-sending.
	Every sub-step is individually guarded — none can undo the execution.

	The customer email is deliberately **not** conditional on the PDF. The page
	has already told them a copy is on its way, and wkhtmltopdf is the most
	failure-prone thing in the stack; when it fails they get the executed
	agreement inline instead of silence.
	"""
	request = frappe.get_doc("Contract Signature Request", request_name)
	if request.status != "Signed":
		return
	if request.get("delivered_on"):
		return

	pdf_url = request.signed_document or _build_signed_pdf(request)

	if _email_signed_copy(request, pdf_url):
		request.db_set("delivered_on", now_datetime(), update_modified=False)

	# Staff notification and the timeline note get their OWN one-shot marker.
	# Without it the daily retry sweep — which only exits when the customer email
	# finally succeeds — would re-alert the team and append another
	# "Signed electronically by…" comment to a legal document every single day
	# for as long as the mail problem lasted.
	if not request.get("staff_notified_on"):
		_alert_staff_signed(request)
		_comment_evidence(request)
		request.db_set("staff_notified_on", now_datetime(), update_modified=False)

	frappe.db.commit()


def _build_signed_pdf(request):
	"""Render ``agreement_html`` to a private PDF attached to the contract.

	Built from the stored executed instrument, **not** ``frappe.attach_print`` —
	attach_print re-renders the live template and would silently defeat the whole
	snapshot, handing the customer a document that could differ from the one they
	signed.
	"""
	try:
		from frappe.utils.pdf import get_pdf

		html = request.agreement_html or request.document_snapshot
		if not html:
			return None
		content = get_pdf(_print_wrapper(html, request.project_contract))
		safe_contract = frappe.utils.cstr(request.project_contract).replace("/", "-")
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{safe_contract}-signed.pdf",
				"content": content,
				"is_private": 1,
				"attached_to_doctype": "Project Contract",
				"attached_to_name": request.project_contract,
			}
		).insert(ignore_permissions=True)
		request.db_set("signed_document", file_doc.file_url, update_modified=False)
		frappe.db.commit()
		return file_doc.file_url
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Contract e-sign: PDF build failed ({request.name})")
		return None


def _print_wrapper(html, contract_name=None):
	"""Wrap the executed agreement in the contract print styling.

	Both the styling and the branded chrome come from the same places the desk
	print format uses — the "Project Contract Print" record's CSS and
	``contract_style`` — rather than from a copy kept here. This function used to
	carry its own hand-written stylesheet, and it had drifted: Times New Roman
	11pt against the print format's Georgia 10.5pt, different border greys. The
	customer's emailed copy of their own signed agreement was the one rendering
	nobody ever compared, and it did not match the document they had signed.
	"""
	from erpnext_enhancements.project_enhancements import contract_style
	from erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract import (
		_contract_css,
	)

	doc = frappe._dict({"name": contract_name}) if contract_name else None
	body = contract_style.wrap(html, doc)
	return (
		"<html><head><meta charset='utf-8'>"
		f"<style>{_contract_css()}</style></head>"
		f'<body><div class="contract-doc">{body}</div></body></html>'
	)


def _email_signed_copy(request, pdf_url):
	"""Send the customer their executed copy. Returns True when it went out.

	Attaches the PDF when there is one; when the PDF failed, the executed
	agreement rides inline instead. The signer has already been told a copy is
	coming — sending nothing is the one outcome to avoid.
	"""
	try:
		if not request.signer_email:
			return False

		attachments = []
		if pdf_url:
			try:
				from frappe.utils.file_manager import get_file

				_, content = get_file(pdf_url)
				attachments = [{"fname": f"{request.project_contract}-signed.pdf", "fcontent": content}]
			except Exception:
				frappe.log_error(
					frappe.get_traceback(), f"Contract e-sign: signed PDF unreadable ({request.name})"
				)

		message = frappe.render_template(
			"erpnext_enhancements/templates/emails/project_enhancements/contract_signed_customer.html",
			{
				"signer_name": request.signed_name,
				"contract": request.project_contract,
				"signed_on": request.signed_on,
				"attached": bool(attachments),
			},
		)
		if not attachments:
			message += (
				"<hr><p><i>"
				+ frappe.utils.escape_html(
					frappe._("A copy of your executed agreement follows. Reply to this email for a PDF.")
				)
				+ "</i></p>"
				+ (request.agreement_html or "")
			)

		frappe.sendmail(
			recipients=[request.signer_email],
			subject=frappe._("Your signed agreement — {0}").format(request.project_contract),
			# The PDF-failure fallback above appends the frozen agreement_html to
			# `message`, so the wrap happens here rather than at render time —
			# the contract text belongs inside the chrome, not after it.
			message=email_style.wrap(
				message,
				title=frappe._("Your signed agreement"),
				eyebrow=request.project_contract,
				tagline=True,
			),
			attachments=attachments,
			reference_doctype="Contract Signature Request",
			reference_name=request.name,
		)
		return True
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Contract e-sign: customer email failed ({request.name})")
		return False


def _alert_staff_signed(request):
	"""Notification Log to the contract owner (plus any configured addresses)."""
	try:
		owner = frappe.db.get_value("Project Contract", request.project_contract, "owner")
		subject = frappe._("Contract signed: {0}").format(request.project_contract)
		drift = (
			frappe._("<br><b>Note:</b> the agreement text changed between sending and signing — review the stored copy.")
			if request.document_drifted
			else ""
		)
		turnstile = (
			frappe._("<br><b>Note:</b> the bot check was unavailable at signing; the signature was accepted and flagged.")
			if request.turnstile_verdict == "Unavailable"
			else ""
		)
		content = (
			frappe._("{0} signed {1} on {2}.").format(
				escape_html(request.signed_name or ""),
				escape_html(request.project_contract),
				request.signed_on,
			)
			+ f"{drift}{turnstile}"
		)
		if owner:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": subject,
					"email_content": content,
					"document_type": "Project Contract",
					"document_name": request.project_contract,
					"for_user": owner,
					"type": "Alert",
				}
			).insert(ignore_permissions=True)
		extra = [
			row.email
			for row in (
				frappe.get_single("ERPNext Enhancements Settings").get("contract_esign_notify_emails") or []
			)
			if row.get("email")
		]
		if extra:
			frappe.sendmail(
				recipients=extra,
				subject=subject,
				# Wrapped for the inbox only; the Notification Log row above keeps
				# the unwrapped fragment for the desk bell panel.
				message=email_style.wrap(content, title=subject, eyebrow="Contracts"),
				reference_doctype="Contract Signature Request",
				reference_name=request.name,
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Contract e-sign: staff alert failed ({request.name})")


def _comment_evidence(request):
	"""Timeline note on the contract so the history is legible in the desk."""
	try:
		contract = frappe.get_doc("Project Contract", request.project_contract)
		method = frappe._("drew") if request.signature_mode == "Drawn" else frappe._("typed")
		contract.add_comment(
			"Comment",
			frappe._("Signed electronically by {0} ({1}) — {2} their signature. Bot check: {3}. Evidence: {4}.").format(
				escape_html(request.signed_name or ""),
				escape_html(_mask_email(request.signer_email)),
				method,
				escape_html(request.turnstile_verdict or "Not Checked"),
				request.name,
			),
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Contract e-sign: timeline comment failed ({request.name})")


def _mask_email(email):
	"""``jane@acme.com`` -> ``j***@acme.com`` for display in staff-visible text."""
	email = (email or "").strip()
	if "@" not in email:
		return email
	local, _, domain = email.partition("@")
	return f"{local[:1]}***@{domain}" if local else f"***@{domain}"


def is_signable(request, contract=None):
	"""True when this request could still execute its contract.

	One predicate, used by both the page render and the signing endpoint, so the
	page never offers a signature the endpoint would refuse.
	"""
	if not request or not request.is_live:
		return False
	if request.provider != "In-House":
		return False
	contract = contract or frappe.db.get_value(
		"Project Contract", request.project_contract, ["docstatus", "status"], as_dict=True
	)
	if not contract:
		return False
	if contract.docstatus != 1:
		return False
	return contract.status in SIGNABLE_CONTRACT_STATUSES


def request_expired(request):
	return bool(
		request.expires_on and now_datetime() > get_datetime(request.expires_on)
	)
