# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Guest endpoints for the contract signing page.

**This is the app's second unauthenticated write path, and the first that mutates
a submitted, legally operative document.** ``crm_enhancements.fountain_move.intake``
is the reference threat model and its docstring is worth reading first; what
follows is what differs here.

*The token is authorisation, not attribution.* The intake invite's docstring says
the opposite about its own token, and every design choice that follows from it
inverts here: 256 bits instead of 88, stored hashed instead of plaintext,
consumable once instead of reusable, and a resolver that must not treat an
expired link as simply absent.

*What actually gates a signature*, in order:

1. possession of the token (emailed to an address we chose),
2. a one-time code the server emails to the address frozen on the request at send
   time, which the signer types back before they can sign or decline
   (:func:`send_signing_code`, :func:`verify_signing_code`; the code's own rules are
   in ``esign.email_code``). The session records when it was verified and for which
   link, and that time goes onto the evidence record,
3. a Turnstile check, which is *tertiary* here (see ``esign.turnstile`` for why an
   ``Unavailable`` verdict lets the signature through rather than blocking it).
   Whether it applies is decided by the server from its own configuration: the
   verdict comes from ``turnstile.verify`` alone, never from a value the page sends.
   A request that carries no bot-check token goes on to the emailed code, which is the
   identity check, under the same limits; a token that is sent and fails is refused,
4. the contract still being submitted, unsigned and not void — re-read under a row
   lock inside the transaction, which is what makes two simultaneous signers safe.

*Uniform failure copy.* A wrong, expired, already used or exhausted code all return
the **same** string. The address the code went to is only ever shown masked (first
character and domain), and the agreement shown before the code is entered masks it
too (``www/contract_sign.py``).

*Never a parameter named ``sid``.* ``Session.__init__`` pops it during auth, from
the query string, the form body and JSON alike; that cost a full outage on the
intake form. Ours is ``esign_sid``.

*Never ``doc.update(payload)``.* Under ``ignore_permissions`` (which a Guest write
requires) ``read_only`` is only a UI hint, so a splatted payload could set
``status`` directly. Every field is assigned explicitly.
"""

import base64
import binascii
import io
import json
import re

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from erpnext_enhancements import email_style
from erpnext_enhancements.feature_flags import contract_esign_public_page_enabled
from erpnext_enhancements.project_enhancements.esign import email_code, lifecycle, turnstile
from erpnext_enhancements.project_enhancements.esign.lifecycle import SignatureEvidence

SESSION_PREFIX = "csign:sess:"
SESSION_TTL = 3600
SID_RE = re.compile(r"^[A-Za-z0-9]{8,64}$")

# C0/C1 control characters plus the bidi overrides. Built from codepoint
# ranges rather than literals: these characters are invisible, so a literal
# in the source is a character nobody can review (or diff).
_CONTROL_RANGES = ((0x00, 0x1F), (0x7F, 0x9F), (0x202A, 0x202E), (0x2066, 0x2069))
CONTROL_CHARS_RE = re.compile(
	"[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _CONTROL_RANGES) + "]"
)

NAME_MAX = 140
SIGNATURE_DATA_URI_RE = re.compile(r"^data:image/png;base64,[A-Za-z0-9+/=\s]{32,}$")
SIGNATURE_MAX_BYTES = 200 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_SIGNATURE_PIXELS = 4_000_000

# Project Contract payment methods that agreed to no automatic charge (§4.3(c) of the
# maintenance agreement): no "save a card" offer after signing.
NO_AUTOPAY_METHODS = ("Invoice (Check or Bank Transfer)", "Other")

# Code entries per request, on top of each code's own limit (email_code.MAX_TRIES): generous
# for typos, useless for guessing. The lifetime count is persisted in `email_attempts`.
CODE_ENTRIES_PER_HOUR = 5
CODE_ENTRIES_LIFETIME = 15

CODE_TEMPLATE = "erpnext_enhancements/templates/emails/project_enhancements/contract_signing_code.html"

# One message for a wrong, expired, already used or exhausted code.
CODE_FAILURE = (
	"That code didn't work. Check the most recent email from us and try again, or send a new code."
)
CODE_REQUIRED = "Please confirm it's you with the code we email you first."


# ---------------------------------------------------------------------------
# 1. begin — verify the human, open a session
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=3600, methods=["POST"])
def begin_signing(ref=None, turnstile_token=None, esign_sid=None):
	"""Verify the bot check and open (or refresh) a signing session.

	The request is resolved from ``ref`` **once, here**, and its name is kept in
	the server-side session. :func:`sign_contract` reads it from there and never
	from the client — in the intake flow that was attribution hygiene, here it is
	load-bearing.

	The verdict is the server's own: ``turnstile.verify`` returns "Not Checked" when
	the site has no bot-check secret configured (accepted, as before), "Unavailable"
	(accepted, with a detail saying no token was sent) when a secret is configured and the
	request carries no token, and checks the token otherwise; a token that is sent and
	fails is "Failed" and refused. No parameter lets the page state the outcome.
	"""
	_require_public_page()

	request = lifecycle.resolve_request(ref)
	if not request:
		return {"ok": False, "state": "invalid"}
	# Resolved only via previous_token_hash: the caller is holding a link a resend
	# has replaced. It may not open a signing session.
	if request.flags.get("opened_with_superseded_link"):
		return {"ok": False, "state": "replaced"}

	verdict, detail = turnstile.verify(turnstile_token)

	sid = esign_sid if (esign_sid and SID_RE.match(str(esign_sid))) else frappe.generate_hash(length=32)
	session = _get_session(sid) or {"opened_at": frappe.utils.now_datetime().isoformat()}

	# The session id is caller-supplied, so a caller can point an EXISTING session
	# at a different request. Post-signature markers describe the request that was
	# signed, not whatever this session now points at — carrying them across would
	# let someone who legitimately signed one agreement act on another (see
	# _signed_session).
	if session.get("request") != request.name:
		for stale in ("signed", "signed_request", "contract"):
			session.pop(stale, None)
	# A verified code belongs to one request and one link: repointed at another request,
	# or opened on a link a resend has since replaced, the session must verify again.
	if session.get("request") != request.name or session.get("token_hash") != request.token_hash:
		for stale in ("code_verified_at", "code_token_hash"):
			session.pop(stale, None)

	session.update(
		{
			"request": request.name,
			# Pinned so a resend (which mints a new token on the SAME row) cannot be
			# executed by a tab still holding the superseded link. Without this the
			# old session would sign against text the customer never saw, and the
			# evidence would certify no drift because the resend refreshed the hash.
			"token_hash": request.token_hash,
			"verdict": verdict,
			"turnstile": detail,
		}
	)
	_put_session(sid, session)
	return {
		"ok": True,
		"esign_sid": sid,
		"verdict": verdict,
		"verified": 1 if _code_verified(session, request) else 0,
	}


# ---------------------------------------------------------------------------
# 1b. the emailed code — confirms the signer can read the address on file
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=3600, methods=["POST"])
def send_signing_code(esign_sid=None):
	"""Email a one-time code to the address frozen on the request (``esign.email_code``).

	Never to a caller-supplied address. Requires a signing session on the live link and
	an accepted bot-check verdict, and sends at most ``email_code.MAX_SENDS`` codes per
	request in ``email_code.SEND_WINDOW``. The answer names the address only masked.
	"""
	_require_public_page()

	session = _get_session(esign_sid)
	if not session:
		# Sessions live in the cache, which every deploy clears: the page opens a new one.
		return {"ok": False, "error": _("Please try again."), "restart": True}
	request = frappe.get_doc("Contract Signature Request", session["request"])
	problem = _code_step_problem(session, request)
	if problem:
		return problem

	if not email_code.send_allowed(request.name):
		return {
			"ok": False,
			"error": _(
				"We've sent several codes in the last few minutes. Please wait a little, then ask again."
			),
		}
	code = email_code.issue(request.name, request.token_hash)
	_email_code(request, code)
	return {
		"ok": True,
		"sent_to": email_code.mask_email(request.signer_email),
		"minutes": email_code.CODE_TTL // 60,
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=3600, methods=["POST"])
def verify_signing_code(esign_sid=None, code=None):
	"""Check the emailed code; on success the session may sign or decline.

	Returns the agreement exactly as it was sent (the page shows a copy with the client's
	address masked until now). Every failed check of the code itself answers with
	:data:`CODE_FAILURE`. Entries count against the request's own limits as well as the
	code's: ``CODE_ENTRIES_PER_HOUR``, and ``CODE_ENTRIES_LIFETIME`` persisted on the
	request, which pauses the link when it is spent. A blank entry costs nothing.
	"""
	_require_public_page()

	session = _get_session(esign_sid)
	if not session:
		# Cleared with the cache by a deploy, like the code itself: start over with a new code.
		return {"ok": False, "error": _(CODE_FAILURE), "restart": True}
	request = frappe.get_doc("Contract Signature Request", session["request"])
	problem = _code_step_problem(session, request)
	if problem:
		return problem

	if _code_verified(session, request):
		return {"ok": True, "already": True, "agreement_html": _agreement_for_page(request)}

	typed = email_code.clean(code)
	if not typed:
		return {"ok": False, "error": _("Enter the 6-digit code from the email.")}
	if not _code_entry_allowed(request):
		return {"ok": False, "error": _(CODE_FAILURE)}
	if not email_code.check(request.name, request.token_hash, typed):
		return {"ok": False, "error": _(CODE_FAILURE)}

	session["code_verified_at"] = frappe.utils.now()
	session["code_token_hash"] = request.token_hash
	_put_session(esign_sid, session)
	return {"ok": True, "agreement_html": _agreement_for_page(request)}


def _code_step_problem(session, request):
	"""The refusal for sending or checking a code on this session, or None when it may proceed."""
	if not _session_token_current(session, request):
		return {
			"ok": False,
			"error": _("Please open the most recent email we sent you and sign from there."),
		}
	if not lifecycle.is_signable(request):
		return {"ok": False, "error": _("This agreement is no longer available to sign.")}
	if not turnstile.accepts(session.get("verdict")):
		return {
			"ok": False,
			"error": _("We couldn't complete the security check. Please reload the page and try again."),
			"retry_captcha": True,
		}
	return None


def _code_verified(session, request):
	"""True when this session entered the right code for the request's current link."""
	return bool(
		session.get("code_verified_at")
		and request.token_hash
		and session.get("code_token_hash") == request.token_hash
	)


def _email_code(request, code):
	"""Send the code. Through the email shell; sent straight after the commit, and the body
	is replaced in the Email Queue once sent, so the code is not kept there."""
	label = _contract_label(request)
	frappe.sendmail(
		recipients=[request.signer_email],
		subject=_("Your code to sign {0}").format(label),
		message=email_style.render(
			CODE_TEMPLATE,
			{
				"code": code,
				"contract_label": label,
				"minutes": email_code.CODE_TTL // 60,
				"company": frappe.defaults.get_global_default("company") or "Sapphire Fountains",
			},
			title=_("Your signing code"),
			eyebrow=label,
			preheader=_("Enter this code on the signing page to confirm it's you."),
			tagline=True,
			pillar="build",
		),
		reference_doctype="Contract Signature Request",
		reference_name=request.name,
		now=True,
		redact_message_after_send=True,
	)


def _contract_label(request):
	"""The agreement's name as the customer knows it (as ``www/contract_sign._label`` gives it)."""
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


def _agreement_for_page(request):
	"""The stored snapshot in the letterhead and footer, as ``www/contract_sign.py`` renders it."""
	from erpnext_enhancements.project_enhancements import contract_style

	template_key = frappe.db.get_value("Project Contract", request.project_contract, "template_key")
	ref = frappe._dict({"name": request.project_contract, "template_key": template_key})
	return contract_style.wrap(request.document_snapshot or "", ref)


# ---------------------------------------------------------------------------
# 2. sign
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=3600, methods=["POST"])
def sign_contract(
	esign_sid=None,
	signature_mode=None,
	signed_name=None,
	signature_image=None,
	signed_title=None,
	agree=None,
	initials=None,
):
	"""Execute the contract. The end of the customer's journey.

	``initials`` is a JSON object of ``{clause key: drawn PNG data URI}``, one entry per
	initials box in the agreement the signer was sent (see :func:`_validated_initials`).
	"""
	_require_public_page()

	session = _get_session(esign_sid)
	if not session:
		return {"ok": False, "error": _("Your session expired. Please reload the page and try again.")}

	# Already signed: idempotent success. A double-click, a retried POST or a
	# refresh must never tell someone their executed contract failed. The session
	# is left holding a marker rather than deleted so this branch stays reachable.
	if session.get("signed"):
		return {"ok": True, "already": True, "contract": session.get("contract"), "request": session["request"]}

	request = frappe.get_doc("Contract Signature Request", session["request"])
	if request.status == "Signed":
		return {"ok": True, "already": True, "contract": request.project_contract, "request": request.name}

	if not _session_token_current(session, request):
		return {
			"ok": False,
			"error": _(
				"This agreement was updated after you opened it. Please open the most recent "
				"email we sent you and sign from there."
			),
		}

	if not lifecycle.is_signable(request):
		return {"ok": False, "error": _("This agreement is no longer available to sign.")}

	if not turnstile.accepts(session.get("verdict")):
		return {
			"ok": False,
			"error": _("We couldn't complete the security check. Please reload the page and try again."),
			"retry_captcha": True,
		}

	if not _code_verified(session, request):
		return {"ok": False, "error": _(CODE_REQUIRED), "code_required": True}

	if not frappe.utils.cint(agree):
		return {"ok": False, "error": _("Please tick the box to confirm you intend to sign.")}

	mode = signature_mode if signature_mode in ("Drawn", "Typed") else None
	if not mode:
		return {"ok": False, "error": _("Choose whether to type or draw your signature.")}

	name = _clean_text(signed_name)
	if not name:
		return {"ok": False, "error": _("Enter your full name as your signature.")}
	title = _clean_text(signed_title)

	image = ""
	if mode == "Drawn":
		image = _validated_signature_image(signature_image)
		if not image:
			return {"ok": False, "error": _("Draw your signature in the box, or switch to typing it.")}

	captured_initials, initials_error = _validated_initials(initials, request.document_snapshot)
	if initials_error:
		return {"ok": False, "error": initials_error, "initials": True}

	settings = frappe.get_cached_doc("ERPNext Enhancements Settings")
	evidence = SignatureEvidence(
		mode=mode,
		signed_name=name,
		signed_title=title,
		signature_image=image,
		email_confirmed=1,
		email_code_verified_on=session.get("code_verified_at"),
		ip_claimed=frappe.local.request_ip,
		ip_peer=getattr(getattr(frappe, "request", None), "remote_addr", None),
		user_agent=frappe.get_request_header("User-Agent"),
		turnstile=dict(session.get("turnstile") or {}, verdict=session.get("verdict")),
		consent_text=settings.get("contract_esign_consent_text") or "",
		disclosure_text=settings.get("contract_esign_disclosure_text") or "",
		review_seconds=_review_seconds(session),
		scrolled_to_end=0,
		provider_status="Signed",
		initials=captured_initials,
	)

	# The authoritative anti-double-sign guard: re-read the contract under a row
	# lock, inside the transaction, so two simultaneous signers cannot both pass.
	locked = frappe.db.get_value(
		"Project Contract", request.project_contract, ["docstatus", "status"], as_dict=True, for_update=True
	)
	if not locked or locked.docstatus != 1 or locked.status not in lifecycle.SIGNABLE_CONTRACT_STATUSES:
		return {"ok": False, "error": _("This agreement is no longer available to sign.")}

	lifecycle.execute_contract(request, evidence)

	# Leave a marker instead of clearing: a retried POST must land on the
	# idempotent branch above, not on "your session expired".
	# `signed_request` binds the marker to the request it describes, so it cannot
	# be reused against a different one if the session is later repointed.
	_put_session(
		esign_sid,
		{
			"request": request.name,
			"signed_request": request.name,
			"signed": 1,
			"contract": request.project_contract,
		},
	)

	return {
		"ok": True,
		"contract": request.project_contract,
		"request": request.name,
		"autopay": _autopay_offer(request),
	}


# ---------------------------------------------------------------------------
# 3. decline
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=3600, methods=["POST"])
def decline_contract(esign_sid=None, reason=None):
	"""Record that the customer declined.

	Deliberately does **not** touch the contract's status: voiding an agreement is
	a human decision and ``on_cancel`` already owns it. This just stops the link
	working and tells someone.
	"""
	_require_public_page()

	session = _get_session(esign_sid)
	if not session:
		return {"ok": False, "error": _("Your session expired. Please reload the page and try again.")}

	request = frappe.get_doc("Contract Signature Request", session["request"])
	if request.status == "Declined":
		return {"ok": True, "already": True}
	if not _session_token_current(session, request):
		return {"ok": False, "error": _("Please open the most recent email we sent you.")}
	if not lifecycle.is_signable(request):
		return {"ok": False, "error": _("This agreement is no longer available.")}
	# Declining is identity-bearing too: it needs the same verified code as signing.
	if not _code_verified(session, request):
		return {"ok": False, "error": _(CODE_REQUIRED), "code_required": True}

	request.status = "Declined"
	request.declined_on = frappe.utils.now_datetime()
	request.decline_reason = _clean_text(reason, limit=500)
	request.token_hash = None
	request.save(ignore_permissions=True)
	frappe.db.commit()

	frappe.enqueue(
		"erpnext_enhancements.project_enhancements.esign.portal.notify_declined",
		queue="short",
		enqueue_after_commit=True,
		request_name=request.name,
	)
	return {"ok": True}


def notify_declined(request_name):
	"""Tell the contract owner the customer declined. Best-effort."""
	try:
		request = frappe.get_doc("Contract Signature Request", request_name)
		owner = frappe.db.get_value("Project Contract", request.project_contract, "owner")
		if owner:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": _("Contract declined: {0}").format(request.project_contract),
					"email_content": _("{0} declined to sign. Reason given: {1}").format(
						frappe.utils.escape_html(request.signer_email or ""),
						frappe.utils.escape_html(request.decline_reason or _("none")),
					),
					"document_type": "Project Contract",
					"document_name": request.project_contract,
					"for_user": owner,
					"type": "Alert",
				}
			).insert(ignore_permissions=True)
		frappe.get_doc("Project Contract", request.project_contract).add_comment(
			"Comment", _("Customer declined to sign electronically.")
		)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Contract e-sign: decline notification failed")


# ---------------------------------------------------------------------------
# 4. autopay — offered AFTER execution, never a condition of it
# ---------------------------------------------------------------------------


def _autopay_offer(request):
	"""Whether to offer the just-signed customer a card-on-file, and the copy.

	Exhibit B of the maintenance agreement already promises exactly this ("Client
	will receive a secure payment link … to store a card on file"), so the offer
	is the agreement keeping its own word rather than an upsell bolted on.

	Never raises: this is computed on the response to a request that has already
	executed a contract, and nothing about a payment convenience may turn that
	into a failure.
	"""
	try:
		if not frappe.utils.cint(
			frappe.db.get_single_value("ERPNext Enhancements Settings", "contract_esign_offer_autopay")
		):
			return {"offer": False}

		from erpnext_enhancements.stripe_payments.core.utils import is_enabled

		if not is_enabled():
			return {"offer": False}

		party_type, party = frappe.db.get_value(
			"Project Contract", request.project_contract, ["party_type", "party"]
		)
		if party_type != "Customer" or not party:
			return {"offer": False}

		# A client who chose to pay by invoice (§4.3(c)) agreed to no automatic charge, so
		# asking them to save a card straight after signing would contradict the page they
		# just signed. Card, bank account (ACH) or no choice recorded keep the offer.
		if frappe.db.get_value("Project Contract", request.project_contract, "payment_method") in NO_AUTOPAY_METHODS:
			return {"offer": False}

		enrolled = frappe.db.get_value(
			"Customer", party, ["custom_stripe_autopay_enabled", "custom_stripe_default_payment_method"], as_dict=True
		)
		if enrolled and enrolled.custom_stripe_autopay_enabled and enrolled.custom_stripe_default_payment_method:
			return {"offer": False}  # already on file — do not ask twice

		frappe.db.set_value(
			"Contract Signature Request", request.name, "autopay_offered", 1, update_modified=False
		)
		return {"offer": True}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Contract e-sign: autopay offer check failed")
		return {"offer": False}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=3600, methods=["POST"])
def start_autopay(esign_sid=None):
	"""Begin card enrolment for the customer who just signed.

	Only reachable from a session that has already executed its contract, so this
	is structurally incapable of affecting the signature: by the time it exists,
	the contract is Signed and committed. A Stripe failure returns a soft outcome
	the page shows as "we'll follow up" — never an error on a page that has just
	told someone their agreement is executed.

	The enrolment is attributed to the **contract's** customer, resolved
	server-side from the session's request; nothing about the target comes from
	the caller.
	"""
	_require_public_page()

	session = _signed_session(esign_sid)
	if not session:
		return {"ok": False}
	try:
		request = frappe.get_doc("Contract Signature Request", session["request"])
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Contract e-sign: autopay setup failed")
		return {"ok": False}
	return begin_autopay(request)


def begin_autopay(request):
	"""Open a Stripe card-enrolment session for a signed request's customer: ``{"ok", "checkout_url"}``.

	Shared by the signing page (:func:`start_autopay`, reached only from the session that
	signed) and the Contract Builder (``contract_builder.start_card_on_file``, a staff member
	at the table with the client who just signed). Every precondition is re-derived from
	the request, never from the caller. Never raises.
	"""
	try:
		if request.status != "Signed":
			return {"ok": False}
		# Re-derive the offer's own preconditions rather than trusting that the
		# client only shows the button when _autopay_offer said yes: this endpoint
		# is reachable by a direct POST.
		if not request.autopay_offered:
			return {"ok": False}
		if request.autopay_outcome in ("Started", "Enrolled"):
			# Idempotent: a double-click must not open a second Stripe session.
			return {"ok": True, "checkout_url": None, "already": True}

		party_type, party = frappe.db.get_value(
			"Project Contract", request.project_contract, ["party_type", "party"]
		)
		if party_type != "Customer" or not party:
			return {"ok": False}

		# Never silently replace a card already on file — that would orphan the
		# previous payment method in Stripe and change how an existing customer
		# gets billed.
		enrolled = frappe.db.get_value(
			"Customer",
			party,
			["custom_stripe_autopay_enabled", "custom_stripe_default_payment_method"],
			as_dict=True,
		)
		if enrolled and enrolled.custom_stripe_autopay_enabled and enrolled.custom_stripe_default_payment_method:
			return {"ok": False}

		from erpnext_enhancements.stripe_payments.core.saved_methods import create_setup_session

		result = create_setup_session(customer=party, channel="Contract Signing")
		frappe.db.set_value(
			"Contract Signature Request",
			request.name,
			{"autopay_outcome": "Started", "autopay_setup_session": result.get("session_id")},
			update_modified=False,
		)
		frappe.db.commit()
		return {"ok": True, "checkout_url": result.get("checkout_url")}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Contract e-sign: autopay setup failed")
		return {"ok": False}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=3600, methods=["POST"])
def decline_autopay(esign_sid=None):
	"""Record that the customer chose not to save a card. Purely informational."""
	_require_public_page()

	session = _signed_session(esign_sid)
	if not session:
		return {"ok": False}
	try:
		request = frappe.get_doc("Contract Signature Request", session["request"])
		# Never overwrite a terminal outcome — "Declined" must not erase the record
		# that a card was actually enrolled.
		if request.status != "Signed" or request.autopay_outcome in ("Started", "Enrolled"):
			return {"ok": False}
		frappe.db.set_value(
			"Contract Signature Request",
			request.name,
			"autopay_outcome",
			"Declined",
			update_modified=False,
		)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Contract e-sign: autopay decline failed")
	return {"ok": True}


# ---------------------------------------------------------------------------
# 5. self-service fresh link — the escape hatch that stops a dead end
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=3, seconds=3600, methods=["POST"])
def request_fresh_link(ref=None):
	"""Re-send the signing link **to the address already on the request**.

	Never to a caller-supplied address — that would turn this into an open relay
	pointed at our own contracts. This is what rescues the dead end an expired
	link would otherwise be.
	"""
	_require_public_page()

	request = lifecycle.resolve_request(ref)
	# "Locked" is in the list deliberately: a link paused for too many failed
	# confirmations must not be revivable by the person who exhausted it. Staff
	# re-send it, which also clears the counter.
	if not request or request.status in ("Signed", "Void", "Declined", "Locked"):
		# Uniform response: never confirm whether a token exists.
		return {"ok": True}

	frappe.enqueue(
		"erpnext_enhancements.project_enhancements.esign.portal.send_fresh_link",
		queue="short",
		enqueue_after_commit=True,
		request_name=request.name,
	)
	return {"ok": True}


def send_fresh_link(request_name):
	"""Background half of :func:`request_fresh_link` — runs as the system user."""
	try:
		from erpnext_enhancements.project_enhancements.esign.api import resend_for_signature

		frappe.set_user("Administrator")
		# reset_attempts=False: a caller who is guessing at the code must not be
		# able to refresh their own entry budget by asking for a new link.
		resend_for_signature(request_name, reset_attempts=False)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Contract e-sign: self-service resend failed")


# ---------------------------------------------------------------------------
# Guards and helpers
# ---------------------------------------------------------------------------


def _require_public_page():
	"""404 rather than explain ourselves to an anonymous caller."""
	if not contract_esign_public_page_enabled():
		raise frappe.DoesNotExistError


def _signed_session(esign_sid):
	"""The session, only if it signed the very request it currently points at.

	The session id is caller-supplied and ``begin_signing`` will repoint an
	existing session at any request whose token resolves. Gating the post-signature
	actions on a bare "signed" marker would therefore let someone who legitimately
	signed one agreement — and who also holds a link to a second, already-signed
	one — act on that second request: in practice, open a Stripe card-enrolment
	session against **another customer**. Binding the marker to its own request
	closes that, and ``begin_signing`` additionally drops stale markers when it
	repoints.
	"""
	session = _get_session(esign_sid)
	if not session or not session.get("signed"):
		return None
	if not session.get("signed_request") or session.get("signed_request") != session.get("request"):
		return None
	return session


def _session_token_current(session, request):
	"""True when the session was opened against the link that is still live.

	A session opened before a resend holds the superseded token; letting it sign
	would execute the contract against text the customer never saw. Sessions
	predating this check have no pinned hash and are allowed through — they
	expire within the hour.
	"""
	pinned = session.get("token_hash")
	if not pinned:
		return True
	return pinned == request.token_hash


def _code_entry_allowed(request):
	"""Count one code entry against the request's limits; False once they are spent.

	Capped per request, not per IP: changing networks is cheaper than obtaining a second
	link. The caller only counts a well-formed entry, so a blank one costs nothing.

	The lifetime counter is persisted on the request (``email_attempts``), not just cached:
	the deploy pipeline flushes Redis on every release, which would reset a cache-only
	limit, and the per-code tries go with it. Spending it pauses the link
	(:func:`_lock_request`) until staff re-send it.
	"""
	if _bump_counter(f"csign:email:{request.name}:h", 3600) > CODE_ENTRIES_PER_HOUR:
		return False

	attempts = (frappe.db.get_value("Contract Signature Request", request.name, "email_attempts") or 0) + 1
	frappe.db.set_value(
		"Contract Signature Request", request.name, "email_attempts", attempts, update_modified=False
	)
	frappe.db.commit()
	if attempts > CODE_ENTRIES_LIFETIME:
		_lock_request(request)
		return False
	return True


def _lock_request(request):
	"""Too many wrong guesses: retire the link and tell someone. No countdown is
	ever shown to the caller — that would confirm they are on the right track."""
	try:
		frappe.db.set_value(
			"Contract Signature Request",
			request.name,
			{"status": "Locked", "token_hash": None},
			update_modified=False,
		)
		owner = frappe.db.get_value("Project Contract", request.project_contract, "owner")
		if owner:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": _("Signing link paused: {0}").format(request.project_contract),
					"email_content": _(
						"Too many signing codes were entered on the signing page, so the link has been "
						"paused. Re-send it if the customer needs another try."
					),
					"document_type": "Project Contract",
					"document_name": request.project_contract,
					"for_user": owner,
					"type": "Alert",
				}
			).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Contract e-sign: lock failed")


def _clean_text(value, limit=NAME_MAX):
	text = CONTROL_CHARS_RE.sub("", str(value or "")).strip()
	return text[:limit]


def _validated_signature_image(value):
	"""Accept only a real PNG drawn on our canvas.

	Shape, size, magic bytes and dimensions are all checked, and the value is
	re-encoded from the decoded bytes so no whitespace or parameter smuggling
	survives into a string that gets rendered through wkhtmltopdf. SVG is
	rejected by construction — it is XML, and this ends up inside a document.
	"""
	raw = str(value or "").strip()
	if not raw or len(raw) > SIGNATURE_MAX_BYTES or not SIGNATURE_DATA_URI_RE.match(raw):
		return ""
	try:
		payload = raw.split(",", 1)[1]
		content = base64.b64decode(payload, validate=False)
	except (IndexError, binascii.Error, ValueError):
		return ""
	if not content.startswith(PNG_MAGIC):
		return ""
	if not _dimensions_sane(content):
		return ""
	return "data:image/png;base64," + base64.b64encode(content).decode("ascii")


def _validated_initials(raw, snapshot):
	"""``(initials, error)`` for the boxes in the signer's snapshot; see :mod:`.initials`.

	Each set goes through the same checks as a drawn signature
	(:func:`_validated_signature_image`), and the boxes required come from the request's
	stored snapshot, never from the caller.
	"""
	from erpnext_enhancements.project_enhancements.esign.initials import validate_initials

	return validate_initials(
		raw,
		snapshot,
		_validated_signature_image,
		clean_text=lambda text, limit: _clean_text(text, limit=limit),
	)


def _dimensions_sane(content):
	"""Header-only check, mirroring ``intake._assert_sane_dimensions``."""
	try:
		from PIL import Image
	except ImportError:
		return True  # Pillow absent; the byte cap still applies
	try:
		with Image.open(io.BytesIO(content)) as image:
			width, height = image.size
	except Exception:
		return False
	return width * height <= MAX_SIGNATURE_PIXELS


def _review_seconds(session):
	try:
		opened = frappe.utils.get_datetime(session.get("opened_at"))
		return max(0, int((frappe.utils.now_datetime() - opened).total_seconds()))
	except Exception:
		return 0


def _bump_counter(key, ttl):
	"""Atomically increment a namespaced counter and return its new value.

	``make_key`` applies the per-site prefix (two sites on a shared bench would
	otherwise share the counter), and only the creator sets the TTL so a burst
	cannot keep pushing expiry out. Atomic because a read-modify-write on the
	session blob would let two concurrent requests both observe the old value.
	"""
	namespaced = frappe.cache.make_key(key)
	count = frappe.cache.incrby(namespaced, 1)
	if count == 1:
		frappe.cache.expire(namespaced, ttl)
	return count


def _session_key(sid):
	return f"{SESSION_PREFIX}{sid}"


def _get_session(sid):
	if not sid or not SID_RE.match(str(sid)):
		return None
	try:
		raw = frappe.cache.get_value(_session_key(sid))
		return json.loads(raw) if isinstance(raw, str) else raw
	except Exception:
		return None


def _put_session(sid, session):
	frappe.cache.set_value(_session_key(sid), json.dumps(session), expires_in_sec=SESSION_TTL)


def _clear_session(sid):
	try:
		frappe.cache.delete_value(_session_key(sid))
	except Exception:
		pass
