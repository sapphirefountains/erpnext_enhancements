# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Emailed client sign-off for a maintenance visit (v1.614.0).

Nik, 2026-10-13: "will this signature requirement allow the tech to send it via email to the
client to sign off? the clients aren't always available to sign in person". Until now the only
client sign-off was the pad on the Visit Wizard's Wrap-up, so a visit done while nobody was on
site printed "Not signed on site" for good. Now the technician (at Finish) or anyone who may send
it from the visit form (:func:`can_send`) emails the client a link; the client opens
``/visit-sign-off``, reads the service report, types their name, signs and confirms the service
was performed; and the signature, name and time land on the visit, which may be approved and
submitted by then (``db_set`` on the four ``allow_on_submit`` fields, never a re-run of the submit
pipeline). **The client's sign-off never blocks anything**: review, approval and billing go on
exactly as before, signed or not.

The record of each link is a **Visit Sign-Off Request**. The threat model is the contract
e-signature's (``project_enhancements/esign/tokens.py``, ``portal.py``, ``lifecycle.py``), scaled
to what is at stake: a sign-off acknowledges a visit, it executes nothing and moves no money, so
there is no Turnstile and no re-typed address. What carries over unchanged:

* **The token is a bearer credential, so it is 256 bits** (``secrets.token_urlsafe``) **and only
  its SHA-256 is stored.** It is in the email and nowhere else; nothing here logs it. A malformed
  token is refused by shape before any database read, so an unknown token and a malformed one are
  indistinguishable from outside.
* **One live link per visit.** A resend mints a new token on a new request and retires the old
  one (status Superseded); its hash is kept, so the old email's link can say "we sent a newer
  one" instead of being a dead end. Links expire after :data:`LINK_DAYS` days and sign once.
* **Uniform failure copy** (:data:`UNAVAILABLE`): unknown, expired, superseded and canceled all
  get the same words from the endpoint.
* **Never a request parameter named ``sid``**: ``Session.__init__`` pops it during auth. The
  link's parameter is ``ref``.
* **Never ``doc.update(payload)`` from a guest.** Under ``ignore_permissions`` ``read_only`` is a
  UI hint only, so a splatted payload could set ``status`` itself. Every field is assigned by
  name (``tests/test_visit_sign_off.py`` walks this module's AST for it).
* Guest endpoints are ``POST``-only, rate limited, and take the token and nothing that names a
  record: the request and its visit are resolved from the token, server side.
"""

import base64
import binascii
import hashlib
import io
import re
import secrets

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_to_date, cint, escape_html, get_datetime, get_url, getdate, now_datetime, nowdate

VISIT = "Sapphire Maintenance Record"
REQUEST = "Visit Sign-Off Request"
#: The public page. Its template is ``www/visit-sign-off.html`` and its controller
#: ``www/visit_sign_off.py``: underscores, or frappe never imports it.
ROUTE = "/visit-sign-off"
TEMPLATE = "erpnext_enhancements/templates/emails/sapphire_maintenance/visit_sign_off_request.html"

#: How long a link signs for. Long enough for a client who reads email weekly; short enough that
#: a forgotten link in an old inbox stops working.
LINK_DAYS = 14
LIVE_STATUSES = ("Sent", "Viewed")
#: ``client_sign_off_method`` on the visit.
ON_SITE = "On site"
BY_EMAIL = "By email"
#: Who may send a link for any visit they can read. Everyone else must be the visit's technician
#: or crew, or hold write permission on it (:func:`can_send`).
SEND_ROLES = frozenset({"Maintenance Reviewer", "Maintenance Supervisor", "Projects Manager", "System Manager"})

# base64url alphabet: token_urlsafe(32) is 43 characters. ``-`` and ``_`` must be allowed or the
# shape guard refuses most real tokens (esign/tokens.py says why that is the easy bug to copy).
TOKEN_BYTES = 32
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")

NAME_MAX = 140
SIGNATURE_DATA_URI_RE = re.compile(r"^data:image/png;base64,[A-Za-z0-9+/=\s]{32,}$")
SIGNATURE_MAX_BYTES = 200 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_SIGNATURE_PIXELS = 4_000_000

# C0/C1 controls and the bidi overrides, built from codepoints: the characters are invisible, so
# a literal in the source could not be reviewed (esign/portal.py, same reason).
_CONTROL_RANGES = ((0x00, 0x1F), (0x7F, 0x9F), (0x202A, 0x202E), (0x2066, 0x2069))
CONTROL_CHARS_RE = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _CONTROL_RANGES) + "]")

#: Every reason a link cannot sign, in one sentence: never says which.
UNAVAILABLE = (
	"This sign-off link isn't available any more. It may have expired or been replaced by a newer "
	"one, so please check for our most recent email."
)


# ---------------------------------------------------------------------------
# Tokens


def mint_token():
	"""``(plaintext, sha256_hex)``. The plaintext goes into the email and is never persisted."""
	token = secrets.token_urlsafe(TOKEN_BYTES)
	return token, hash_token(token)


def hash_token(token):
	"""SHA-256 hex of a token, or None when it is not even the right shape (no database read)."""
	if not token or not TOKEN_RE.match(str(token)):
		return None
	return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def sign_off_url(token):
	return get_url(f"{ROUTE}?ref={token}")


def resolve(token):
	"""The Visit Sign-Off Request a link's token names, or None. Never raises: the public page
	calls it on every render, and a mangled link must show a page, not a traceback."""
	try:
		token_hash = hash_token(token)
		if not token_hash:
			return None
		name = frappe.db.get_value(REQUEST, {"token_hash": token_hash}, "name")
		return frappe.get_doc(REQUEST, name) if name else None
	except Exception:
		frappe.log_error(title="Visit sign-off: resolve failed", message=frappe.get_traceback())
		return None


# ---------------------------------------------------------------------------
# State


def is_expired(request, now=None):
	expires = request.get("expires_on")
	return bool(expires) and get_datetime(expires) <= (now or now_datetime())


def effective_status(request, now=None):
	"""The status, with a live link past its expiry read as Expired before the daily sweep
	(:func:`expire_stale`) has written it."""
	status = request.get("status") or ""
	if status in LIVE_STATUSES and is_expired(request, now):
		return "Expired"
	return status


def is_signable(request, now=None):
	return bool(request) and request.get("status") in LIVE_STATUSES and not is_expired(request, now)


def latest_request(visit_name):
	"""The visit's newest request as a dict, or None."""
	rows = frappe.get_all(
		REQUEST,
		filters={"visit": visit_name},
		fields=["name", "status", "email", "sent_on", "sent_by", "expires_on", "first_viewed_on", "signed_on", "signed_name"],
		order_by="creation desc",
		limit=1,
	)
	return rows[0] if rows else None


def describe(visit, request=None, fmt=None):
	"""The client sign-off in a few words, for the review digest and the form.

	``fmt`` formats a datetime (``frappe.utils.format_datetime`` by default). Signed on site with
	no time recorded is a visit signed before v1.614.0 stamped one: just "Signed".
	"""
	fmt = fmt or _format_datetime
	if visit.get("client_sign_off"):
		when = visit.get("client_signed_on")
		if visit.get("client_sign_off_method") == BY_EMAIL:
			text = _("Signed by email")
			if visit.get("client_signed_by"):
				text += " " + _("by {0}").format(visit.get("client_signed_by"))
			if when:
				text += " " + _("on {0}").format(fmt(when))
			return text
		if when:
			return _("Signed on site {0}").format(fmt(when))
		return _("Signed")
	if request:
		status = effective_status(request)
		sent = fmt(request.get("sent_on")) if request.get("sent_on") else ""
		if status == "Viewed":
			return _("Emailed to {0} on {1}; opened, not signed yet").format(request.get("email"), sent)
		if status == "Sent":
			return _("Emailed to {0} on {1}, not signed yet").format(request.get("email"), sent)
		if status == "Expired":
			return _("Emailed to {0} on {1}; the link expired unsigned").format(request.get("email"), sent)
	return _("Not signed")


def _format_datetime(value):
	from frappe.utils import format_datetime

	return format_datetime(value)


# ---------------------------------------------------------------------------
# Who may send, and to whom


def can_send(visit, user=None):
	"""Whether ``user`` may email this visit's client a sign-off link.

	The technician on it or one of its crew, anyone holding :data:`SEND_ROLES` who can read it, or
	anyone with write permission on it. Never for a canceled visit or one the client has signed.
	"""
	user = user or frappe.session.user
	if visit.get("docstatus") == 2 or visit.get("client_sign_off"):
		return False
	if user == "Guest" or not frappe.has_permission(VISIT, "read", doc=visit, user=user):
		return False
	if user == "Administrator" or SEND_ROLES & set(frappe.get_roles(user)):
		return True
	if user == visit.get("technician") or user in {row.get("user") for row in visit.get("crew") or []}:
		return True
	return bool(frappe.has_permission(VISIT, "write", doc=visit, user=user))


def default_email(visit):
	"""The address to prefill: the last one this client was sent a link at, else the maintenance
	agreement's contact, else the account's email or primary contact. Never raises; "" for none."""
	try:
		customer = visit.get("customer")
		if customer:
			last = frappe.get_all(
				REQUEST, filters={"customer": customer}, fields=["email"], order_by="creation desc", limit=1
			)
			if last and clean_email(last[0].get("email")):
				return clean_email(last[0].get("email"))
		contract = visit.get("maintenance_contract")
		if contract:
			project_contract = frappe.db.get_value("Sapphire Maintenance Contract", contract, "project_contract")
			if project_contract:
				email = clean_email(frappe.db.get_value("Project Contract", project_contract, "contact_email"))
				if email:
					return email
		if customer:
			email = clean_email(frappe.db.get_value("Customer", customer, "email_id"))
			if email:
				return email
			contact = frappe.db.get_value("Customer", customer, "customer_primary_contact")
			if contact:
				return clean_email(frappe.db.get_value("Contact", contact, "email_id"))
	except Exception:
		frappe.log_error(title="Visit sign-off: default address failed", message=frappe.get_traceback())
	return ""


def clean_email(value):
	"""Exactly one valid address, lower-cased, or "".

	``validate_email_address`` extracts rather than checks: it comma-splits and returns the valid
	parts joined, so "a@b.com, c@d.com" would pass a truthiness test as two recipients.
	"""
	from frappe.utils import validate_email_address

	email = str(value or "").strip()
	if not email or re.search(r"[,;\s<>]", email):
		return ""
	if validate_email_address(email) != email:
		return ""
	return email.lower()


# ---------------------------------------------------------------------------
# Sending


def send_request(visit, email):
	"""Mint a link, retire any live one, and email it. The caller has checked :func:`can_send`.

	Raises on a bad address or a failed send: a desk user who pressed the button must not be
	told it went. :func:`send_after_finish` is the never-raising form for Finish.
	"""
	recipient = clean_email(email)
	if not recipient:
		frappe.throw(_("Enter one valid email address for the client."), title=_("Email needed"))
	now = now_datetime()
	retire_live(visit.name)
	token, token_hash = mint_token()
	request = frappe.new_doc(REQUEST)
	request.visit = visit.name
	request.status = "Sent"
	request.email = recipient
	request.customer = visit.get("customer")
	request.sent_by = frappe.session.user
	request.sent_on = now
	request.expires_on = add_to_date(now, days=LINK_DAYS)
	request.token_hash = token_hash
	request.insert(ignore_permissions=True)
	_email_request(visit, request, sign_off_url(token))
	visit.add_comment(
		"Comment",
		_("Sign-off link emailed to {0} (expires {1}).").format(
			escape_html(recipient), _format_datetime(request.expires_on)
		),
	)
	return public_status(request)


def send_after_finish(visit, email):
	"""Finish's sign-off email: ``{"sign_off": status}`` or ``{"sign_off_error": message}``.

	Never raises, and never undoes the Finish: the send runs in a savepoint and a failure rolls back
	only itself. The visit has gone for review either way, which is the point: the client's
	sign-off never blocks review, approval or billing.
	"""
	recipient = clean_email(email)
	if not recipient:
		return {"sign_off_error": _("The visit was finished, but {0} is not an email address. Send the sign-off link from the visit form.").format(email)}
	if visit.get("client_sign_off"):
		return {}
	if not can_send(visit):
		return {"sign_off_error": _("The visit was finished, but you may not email its client. Ask the office to send the sign-off link.")}
	frappe.db.savepoint("visit_sign_off")
	try:
		return {"sign_off": send_request(visit, recipient)}
	except Exception:
		frappe.db.rollback(save_point="visit_sign_off")
		frappe.log_error(title="Visit sign-off: email after Finish failed", message=frappe.get_traceback())
		return {"sign_off_error": _("The visit was finished, but the sign-off email could not be sent. Send it from the visit form.")}


def retire_live(visit_name, keep=None):
	"""Supersede every live link for the visit (but ``keep``). The hash stays, so an old link
	resolves and is told it was replaced rather than that it never existed."""
	for name in frappe.get_all(REQUEST, filters={"visit": visit_name, "status": ["in", LIVE_STATUSES]}, pluck="name"):
		if name != keep:
			frappe.db.set_value(REQUEST, name, "status", "Superseded", update_modified=False)


def public_status(request):
	"""What the form and the wizard show about a request: never its token or hash."""
	return {
		"request": request.get("name"),
		"status": effective_status(request),
		"email": request.get("email"),
		"sent_on": str(request.get("sent_on") or "") or None,
		"expires_on": str(request.get("expires_on") or "") or None,
		"first_viewed_on": str(request.get("first_viewed_on") or "") or None,
		"signed_on": str(request.get("signed_on") or "") or None,
	}


def _email_request(visit, request, url):
	"""The email: a short note and a button, in the app's shell (``email_style``)."""
	from erpnext_enhancements import email_style

	site = site_label(visit)
	visit_day = getdate(visit.get("visit_date") or nowdate())
	if visit_day == getdate(nowdate()):
		subject = _("Please sign off today's service visit — {0}").format(site)
	else:
		subject = _("Please sign off your service visit on {0} — {1}").format(_format_date(visit_day), site)
	company = frappe.defaults.get_global_default("company") or "Sapphire Fountains"
	frappe.sendmail(
		recipients=[request.email],
		subject=subject,
		message=email_style.render(
			TEMPLATE,
			{
				"site": site,
				"visit_date": _format_date(visit_day),
				"technician": _full_name(visit.get("technician")),
				"sign_off_url": url,
				"expires_on": _format_datetime(request.expires_on),
				"company": company,
			},
			title=_("Please sign off your service visit"),
			eyebrow=site,
			preheader=_("Your technician has finished. Please look over the service report and sign it off."),
			tagline=True,
			pillar="service",
		),
		reference_doctype=VISIT,
		reference_name=visit.name,
		reply_to=_office_email(company),
	)


def _office_email(company):
	"""Replies go to the office, not to whoever's account sent it. None when no company email."""
	try:
		return frappe.db.get_value("Company", company, "email") or None
	except Exception:
		return None


def site_label(visit):
	"""What the client calls the place: the project's name, else the account's."""
	try:
		if visit.get("project"):
			name = frappe.db.get_value("Project", visit.project, "project_name")
			if name:
				return name
		if visit.get("customer"):
			return frappe.db.get_value("Customer", visit.customer, "customer_name") or visit.customer
	except Exception:
		pass
	return _("your fountain")


def _full_name(user):
	if not user:
		return ""
	try:
		return frappe.db.get_value("User", user, "full_name") or user
	except Exception:
		return user


def _format_date(value):
	from frappe.utils import formatdate

	return formatdate(value)


# ---------------------------------------------------------------------------
# Desk endpoints


def _visit(record):
	from erpnext_enhancements.sapphire_maintenance.visit_names import current_visit_name

	name = current_visit_name(record)
	if not name:
		frappe.throw(_("Visit {0} not found.").format(record), frappe.DoesNotExistError)
	visit = frappe.get_doc(VISIT, name)
	visit.check_permission("read")
	return visit


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=3600, methods=["POST"])
def send_sign_off_request(record, email=None):
	"""Email (or re-send) the visit's client a sign-off link, from the visit form.

	Needs :func:`can_send`. A re-send supersedes the link before it.
	"""
	visit = _visit(record)
	if visit.docstatus == 2:
		frappe.throw(_("This visit is canceled."), title=_("Cannot send"))
	if visit.get("client_sign_off"):
		frappe.throw(_("The client has already signed this visit off."), title=_("Already signed"))
	if not can_send(visit):
		frappe.throw(
			_("Only the visit's technician or a maintenance reviewer can email the client."),
			frappe.PermissionError,
		)
	return send_request(visit, email or default_email(visit))


@frappe.whitelist()
def get_sign_off_status(record):
	"""The visit form's sign-off line: the latest link, the address to prefill, and whether this
	user may send one."""
	visit = _visit(record)
	request = latest_request(visit.name)
	return {
		"request": public_status(request) if request else None,
		"summary": describe(visit, request),
		"method": visit.get("client_sign_off_method"),
		"default_email": default_email(visit) if not visit.get("client_sign_off") else "",
		"can_send": can_send(visit),
	}


def wizard_context(visit):
	"""For the Visit Wizard's bootstrap: the address to prefill and the latest link. Never raises."""
	try:
		request = latest_request(visit.name)
		return {
			"default_email": default_email(visit),
			"request": public_status(request) if request else None,
		}
	except Exception:
		frappe.log_error(title="Visit sign-off: wizard context failed", message=frappe.get_traceback())
		return {"default_email": "", "request": None}


# ---------------------------------------------------------------------------
# Guest endpoint: the public page's Sign


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=3600, methods=["POST"])
def sign_visit(ref=None, signed_name=None, signature_image=None, confirmed=None):
	"""Sign the visit off. The token is the only authority; the visit comes from it.

	Signing twice (a double tap, a retried POST) answers ``already`` rather than an error: a
	client who has signed must never be told it failed.
	"""
	request = resolve(ref)
	if request is None:
		return {"ok": False, "error": _(UNAVAILABLE)}
	if request.status == "Signed":
		return {"ok": True, "already": True}
	if not is_signable(request):
		return {"ok": False, "error": _(UNAVAILABLE)}

	if not cint(confirmed):
		return {"ok": False, "error": _("Please tick the box to confirm the service was performed.")}
	name = clean_text(signed_name)
	if not name:
		return {"ok": False, "error": _("Please type your full name.")}
	image = validated_signature_image(signature_image)
	if not image:
		return {"ok": False, "error": _("Please sign in the box.")}

	# Re-read both rows under a lock, inside the transaction: two simultaneous signers (or a
	# signature on site at the same moment) cannot both land.
	status = frappe.db.get_value(REQUEST, request.name, "status", for_update=True)
	if status == "Signed":
		return {"ok": True, "already": True}
	if status not in LIVE_STATUSES:
		return {"ok": False, "error": _(UNAVAILABLE)}
	visit = frappe.db.get_value(VISIT, request.visit, ["docstatus", "client_sign_off"], as_dict=True, for_update=True)
	if not visit or visit.docstatus == 2:
		return {"ok": False, "error": _(UNAVAILABLE)}
	if visit.client_sign_off:
		# Signed on site in the meantime. That signature stands; this link is done.
		frappe.db.set_value(REQUEST, request.name, "status", "Superseded", update_modified=False)
		return {"ok": True, "already": True}

	record_signature(request, name, image)
	return {"ok": True}


def record_signature(request, signed_name, image):
	"""Stamp the evidence on the request, then the signature on the visit.

	Explicit assignment, field by field. The visit gets ``db_set``: it may be submitted by now,
	and these four fields are ``allow_on_submit``; saving it would re-run validation and hooks a
	client's signature has no business triggering. ``db_set`` also moves ``modified``, so a visit
	form open since before the client signed refuses to save over it instead of blanking it.
	"""
	signed_at = now_datetime()
	request.status = "Signed"
	request.signed_name = signed_name
	request.signature_image = image
	request.signed_on = signed_at
	request.confirmed = 1
	request.signer_ip = str(getattr(frappe.local, "request_ip", None) or "")[:140]
	request.user_agent = str(frappe.get_request_header("User-Agent") or "")[:512]
	request.save(ignore_permissions=True)

	visit = frappe.get_doc(VISIT, request.visit)
	visit.db_set(
		{
			"client_sign_off": image,
			"client_signed_by": signed_name,
			"client_signed_on": signed_at,
			"client_sign_off_method": BY_EMAIL,
		}
	)
	visit.add_comment("Comment", _("Signed off by email by {0}.").format(escape_html(signed_name)))
	retire_live(visit.name, keep=request.name)


def mark_viewed(request_name):
	"""Count an open of the page. Enqueued by the page's controller: a GET is not committed, so
	an inline write would be rolled back on the way out."""
	try:
		row = frappe.db.get_value(REQUEST, request_name, ["status", "first_viewed_on", "view_count"], as_dict=True)
		if not row:
			return
		updates = {"view_count": cint(row.view_count) + 1}
		if not row.first_viewed_on:
			updates["first_viewed_on"] = now_datetime()
		if row.status == "Sent":
			updates["status"] = "Viewed"
		frappe.db.set_value(REQUEST, request_name, updates, update_modified=False)
	except Exception:
		frappe.log_error(title="Visit sign-off: view tracking failed", message=frappe.get_traceback())


def expire_stale():
	"""Daily: write Expired on live links past their expiry, so the list says what the page does."""
	for name in frappe.get_all(
		REQUEST, filters={"status": ["in", LIVE_STATUSES], "expires_on": ["<", now_datetime()]}, pluck="name"
	):
		frappe.db.set_value(REQUEST, name, "status", "Expired", update_modified=False)


# ---------------------------------------------------------------------------
# Input hygiene (the same checks as esign/portal.py)


def clean_text(value, limit=NAME_MAX):
	return CONTROL_CHARS_RE.sub("", str(value or "")).strip()[:limit]


def validated_signature_image(value):
	"""A PNG drawn on the page's canvas, re-encoded from its bytes, or "".

	Shape, size, magic bytes and dimensions are checked, and the value is rebuilt from the decoded
	bytes so nothing smuggled survives: it is printed unescaped into an ``<img src>`` on the
	customer's report, and the visit's own check (``CLIENT_SIGNATURE``) must accept it.
	"""
	raw = str(value or "").strip()
	if not raw or len(raw) > SIGNATURE_MAX_BYTES or not SIGNATURE_DATA_URI_RE.match(raw):
		return ""
	try:
		content = base64.b64decode(raw.split(",", 1)[1], validate=False)
	except (IndexError, binascii.Error, ValueError):
		return ""
	if not content.startswith(PNG_MAGIC) or not _dimensions_sane(content):
		return ""
	return "data:image/png;base64," + base64.b64encode(content).decode("ascii")


def _dimensions_sane(content):
	try:
		from PIL import Image
	except ImportError:
		return True  # Pillow absent: the byte cap still applies
	try:
		with Image.open(io.BytesIO(content)) as image:
			width, height = image.size
	except Exception:
		return False
	return width * height <= MAX_SIGNATURE_PIXELS
