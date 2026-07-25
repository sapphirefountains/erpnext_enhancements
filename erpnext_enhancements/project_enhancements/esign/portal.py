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
2. knowing that address — the signer re-types it, compared constant-time against
   the copy frozen on the request at send time,
3. a Turnstile check, which is *tertiary* here (see ``esign.turnstile`` for why an
   ``Unavailable`` verdict lets the signature through rather than blocking it),
4. the contract still being submitted, unsigned and not void — re-read under a row
   lock inside the transaction, which is what makes two simultaneous signers safe.

*Uniform failure copy.* Wrong email and failed bot check return the **same**
string, and the expected address is never echoed — not even masked. The whole
identity control is "re-type the address"; a masked hint would hand it to whoever
the link was forwarded to.

*Never a parameter named ``sid``.* ``Session.__init__`` pops it during auth, from
the query string, the form body and JSON alike; that cost a full outage on the
intake form. Ours is ``esign_sid``.

*Never ``doc.update(payload)``.* Under ``ignore_permissions`` (which a Guest write
requires) ``read_only`` is only a UI hint, so a splatted payload could set
``status`` directly. Every field is assigned explicitly.
"""

import base64
import binascii
import hmac
import io
import json
import re
import unicodedata

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from erpnext_enhancements.feature_flags import contract_esign_public_page_enabled
from erpnext_enhancements.project_enhancements.esign import lifecycle, turnstile
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

# Quote characters a pasted address can arrive wrapped in (straight and
# typographic). Built from codepoints for the same reason as _CONTROL_RANGES:
# smart quotes are visually ambiguous in source.
_QUOTE_CHARS = "\"'" + "".join(chr(c) for c in (0x2018, 0x2019, 0x201C, 0x201D))

NAME_MAX = 140
SIGNATURE_DATA_URI_RE = re.compile(r"^data:image/png;base64,[A-Za-z0-9+/=\s]{32,}$")
SIGNATURE_MAX_BYTES = 200 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_SIGNATURE_PIXELS = 4_000_000

# Email attempts per request: generous for typos, useless for fishing.
EMAIL_ATTEMPTS_PER_HOUR = 5
EMAIL_ATTEMPTS_LIFETIME = 15

GENERIC_FAILURE = (
	"We couldn't verify that. Please check the email address this agreement was "
	"sent to and try again."
)


# ---------------------------------------------------------------------------
# 1. begin — verify the human, open a session
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=3600, methods=["POST"])
def begin_signing(ref=None, turnstile_token=None, esign_sid=None, widget_unavailable=None):
	"""Verify the bot check and open (or refresh) a signing session.

	The request is resolved from ``ref`` **once, here**, and its name is kept in
	the server-side session. :func:`sign_contract` reads it from there and never
	from the client — in the intake flow that was attribution hygiene, here it is
	load-bearing.
	"""
	_require_public_page()

	request = lifecycle.resolve_request(ref)
	if not request:
		return {"ok": False, "state": "invalid"}
	# Resolved only via previous_token_hash: the caller is holding a link a resend
	# has replaced. It may not open a signing session.
	if request.flags.get("opened_with_superseded_link"):
		return {"ok": False, "state": "replaced"}

	if frappe.utils.cint(widget_unavailable):
		# The widget never loaded or errored in the browser. Recorded verbatim on
		# the evidence row and in the staff alert — never laundered into a pass —
		# but it does not block: the signer already holds a single-use link sent to
		# a known address, and the resulting maintenance contract is a draft a
		# human activates.
		verdict, detail = "Unavailable", {"errors": "Challenge widget unavailable in the browser."}
	else:
		verdict, detail = turnstile.verify(turnstile_token)

	sid = esign_sid if (esign_sid and SID_RE.match(str(esign_sid))) else frappe.generate_hash(length=32)
	session = _get_session(sid) or {"opened_at": frappe.utils.now_datetime().isoformat()}
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
	return {"ok": True, "esign_sid": sid, "verdict": verdict}


# ---------------------------------------------------------------------------
# 2. sign
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=3600, methods=["POST"])
def sign_contract(
	esign_sid=None,
	confirm_email=None,
	signature_mode=None,
	signed_name=None,
	signature_image=None,
	signed_title=None,
	agree=None,
):
	"""Execute the contract. The end of the customer's journey."""
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

	if not str(confirm_email or "").strip():
		return {"ok": False, "error": _("Type the email address this agreement was sent to.")}

	if not _email_matches(request, confirm_email):
		return {"ok": False, "error": _(GENERIC_FAILURE)}

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

	settings = frappe.get_cached_doc("ERPNext Enhancements Settings")
	evidence = SignatureEvidence(
		mode=mode,
		signed_name=name,
		signed_title=title,
		signature_image=image,
		email_confirmed=1,
		ip_claimed=frappe.local.request_ip,
		ip_peer=getattr(getattr(frappe, "request", None), "remote_addr", None),
		user_agent=frappe.get_request_header("User-Agent"),
		turnstile=dict(session.get("turnstile") or {}, verdict=session.get("verdict")),
		consent_text=settings.get("contract_esign_consent_text") or "",
		disclosure_text=settings.get("contract_esign_disclosure_text") or "",
		review_seconds=_review_seconds(session),
		scrolled_to_end=0,
		provider_status="Signed",
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
	_put_session(
		esign_sid,
		{"request": request.name, "signed": 1, "contract": request.project_contract},
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
def decline_contract(esign_sid=None, confirm_email=None, reason=None):
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
	# Declining is identity-bearing too — otherwise anyone holding a forwarded
	# link could kill a live agreement.
	if not str(confirm_email or "").strip():
		return {"ok": False, "error": _("Type the email address this agreement was sent to.")}
	if not _email_matches(request, confirm_email):
		return {"ok": False, "error": _(GENERIC_FAILURE)}

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

	session = _get_session(esign_sid)
	if not session or not session.get("signed"):
		return {"ok": False}

	try:
		request = frappe.get_doc("Contract Signature Request", session["request"])
		if request.status != "Signed":
			return {"ok": False}
		if request.autopay_outcome in ("Started", "Enrolled"):
			# Idempotent: a double-click must not open a second Stripe session.
			return {"ok": True, "checkout_url": None, "already": True}

		party_type, party = frappe.db.get_value(
			"Project Contract", request.project_contract, ["party_type", "party"]
		)
		if party_type != "Customer" or not party:
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

	session = _get_session(esign_sid)
	if not session or not session.get("signed"):
		return {"ok": False}
	try:
		frappe.db.set_value(
			"Contract Signature Request",
			session["request"],
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
	pointed at our own contracts. This is what rescues the two dead ends the
	identity check would otherwise create: a link that expired, and an assistant
	who genuinely does not know which address it went to.
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
		# reset_attempts=False: a caller who is guessing at the address must not be
		# able to refresh their own guess budget by asking for a new link.
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


def _normalise_email(value):
	"""Fold the differences that are typing noise, and nothing more.

	Angle brackets and smart quotes are stripped because people paste
	``Jane <jane@x.com>`` — or a curly-quoted address out of a document —
	constantly. Gmail dot/plus tricks are deliberately NOT folded: that would
	quietly widen who can execute a contract for marginal convenience.
	"""
	email = unicodedata.normalize("NFKC", str(value or "")).strip()
	email = email.strip(_QUOTE_CHARS)
	if email.startswith("<") and email.endswith(">"):
		email = email[1:-1]
	if "<" in email and email.endswith(">"):
		email = email[email.index("<") + 1 : -1]
	return email.strip().casefold()


def _email_matches(request, typed):
	"""Constant-time compare against the address frozen at send time.

	Attempts are capped per request, not per IP: the threat is a link-holder
	fishing for the address, and a forged X-Forwarded-For would sail past an
	IP-keyed counter.

	A blank entry costs nothing. It is not a guess, and charging for it let the
	decline flow — whose dialog never asks for an address — silently spend the
	signer's whole hourly budget and lock them out of signing.

	The lifetime counter is persisted on the request, not just cached: the deploy
	pipeline flushes Redis on every release, which would quietly reset the only
	durable limit on address guessing and mean the lockout never fires.
	"""
	if not str(typed or "").strip():
		return False

	if _bump_counter(f"csign:email:{request.name}:h", 3600) > EMAIL_ATTEMPTS_PER_HOUR:
		return False

	attempts = (frappe.db.get_value("Contract Signature Request", request.name, "email_attempts") or 0) + 1
	frappe.db.set_value(
		"Contract Signature Request", request.name, "email_attempts", attempts, update_modified=False
	)
	frappe.db.commit()
	if attempts > EMAIL_ATTEMPTS_LIFETIME:
		_lock_request(request)
		return False

	# Compare bytes: compare_digest only accepts str operands that are pure
	# ASCII, and a pasted address with a curly quote or an accent would raise
	# rather than return the uniform failure message.
	return hmac.compare_digest(
		_normalise_email(typed).encode("utf-8"),
		_normalise_email(request.signer_email).encode("utf-8"),
	)


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
						"Too many incorrect email confirmations were entered on the signing page, so "
						"the link has been paused. Re-send it if the customer needs another try."
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
