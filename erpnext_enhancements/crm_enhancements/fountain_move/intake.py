# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Guest endpoints behind the public fountain-move form at ``/fountain-move``.

**This is the only unauthenticated write path in the app.** Every other
``allow_guest`` endpoint here is a machine-to-machine webhook gated by a shared
secret or an HMAC (see ``api/telephony.py``, ``mdm_integration/webhooks.py``).
This one cannot be: the caller is a member of the public with a browser and no
credential of any kind. So the guarantees have to come from somewhere else, and
they are worth stating explicitly.

Threat model and the controls that answer it:

* **Automated spam** — Cloudflare Turnstile, verified server-side, fail-closed.
  A submission is only auto-converted when the verdict is ``Passed``.
* **Naive bots** — a honeypot field plus a minimum time-on-form. The honeypot
  trips on the *presence of the key in the raw request body*, not on a non-empty
  value: frappe runs ``sanitize_html`` over a guest's ``form_dict`` before the
  endpoint sees it, which can blank the value we were hoping to catch.
* **Resource exhaustion** — ``frappe.rate_limiter.rate_limit`` per endpoint, plus
  in-body counters keyed on the session and on a hash of the email. The
  decorator's IP dimension is honest but weak: ``auth.py:62-70`` takes the FIRST
  ``X-Forwarded-For`` entry unconditionally, so it is attacker-controlled unless
  the edge proxy overwrites the header. We record both the claimed and the peer
  address and never make an IP the sole basis of a decision.
* **Malicious uploads** — magic-byte sniffing (not extension trust), a total-pixel
  cap read from the image header before decode, a byte cap, and a server-generated
  filename. Files are always private.
* **Mass assignment** — the document is built from ``INTAKE_FIELD_MAP`` and
  nothing else. ``read_only`` in a DocType JSON is a UI hint; under
  ``ignore_permissions=True`` (which a Guest insert requires) frappe's
  higher-permlevel check returns early, so a splatted payload could set
  ``status`` or the ``created_*`` links directly.
* **Information disclosure** — errors are generic. Duplicate matching happens in
  a background job, never in the response, so the endpoint cannot be used as an
  oracle for "is this email a customer of yours?".

**CSRF: not a control here, but the header must still be sent when one exists.**
``auth.py:86`` short-circuits ``validate_csrf_token`` when the session carries no
saved token — exactly the state of a fresh guest, since ``Session.update`` never
persists one for Guest. So for the anonymous customer this page is built for,
CSRF validates nothing and the controls above are the real protection.

That is only half the picture, and getting it wrong shipped a bug. **This page is
equally reachable by logged-in staff**, whose session *does* carry a token — so
``validate_csrf_token`` does not short-circuit for them, and a POST without the
``X-Frappe-CSRF-Token`` header throws ``CSRFTokenError``, which is **HTTP 400**.
Every endpoint below returned 400 for anyone signed in, which broke the form
outright rather than degrading. Same-origin does not help: ``is_allowed_referrer``
only passes hosts explicitly listed in site config's ``allowed_referrers``, empty
by default.

``www/fountain_move.py`` therefore emits the session's **existing** token (or
``""``), and the client sends the header only when it is non-empty. Note it must
NOT use ``frappe.sessions.get_csrf_token()``, which mints a token when absent —
a guest would then send one the server has never stored.
"""

import hashlib
import io
import json
import re
import time

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, flt, now_datetime

from erpnext_enhancements.crm_enhancements.fountain_move import (
	CHECKBOX_FIELDS,
	FIELD_MAX_LENGTHS,
	HONEYPOT_FIELD_NAME,
	INTAKE_FIELD_MAP,
	INTAKE_FILE_FOLDER,
	PROPERTY_TYPES,
	TURNSTILE_ACTION,
	get_store_address,
	is_valid_store_location,
)
from erpnext_enhancements.feature_flags import (
	fountain_move_auto_convert_enabled,
	fountain_move_public_form_enabled,
)

#: Redis key prefixes.
SESSION_PREFIX = "fmi:sess:"
UPLOAD_COUNT_PREFIX = "fmi:cnt:"
SUBMIT_COUNT_PREFIX = "fmi:submit:"
EMAIL_COUNT_PREFIX = "fmi:email:"

#: How long a started intake stays alive, in seconds.
SESSION_TTL = 3600

#: Minimum seconds between opening the form and submitting it. A human reading
#: the consent line and typing an address cannot beat this; a bot posting the
#: form directly always will.
MIN_FILL_SECONDS = 4

#: Hard ceiling per photo regardless of site settings.
PHOTO_BYTE_CAP = 10 * 1024 * 1024

#: Total pixels (w*h) accepted. 40 MP is far beyond any phone camera and well
#: below what a decompression bomb needs to exhaust memory.
MAX_TOTAL_PIXELS = 40_000_000

#: Accepted image types, keyed by magic-byte signature. SVG is excluded on
#: purpose (it is XML, and an XML parser is a liability); HEIC too, because
#: Pillow cannot decode it without pillow-heif and the client converts to JPEG
#: during downscale anyway.
MAGIC_SIGNATURES = (
	(b"\xff\xd8\xff", "jpg", "image/jpeg"),
	(b"\x89PNG\r\n\x1a\n", "png", "image/png"),
)

#: Turnstile siteverify.
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TURNSTILE_TIMEOUT = 10
TURNSTILE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._\-]{1,2048}$")
TURNSTILE_MAX_AGE = 300

#: Characters rejected outright in any text field: C0/C1 controls, and the
#: bidirectional override characters (U+202A..U+202E, U+2066..U+2069) that let rendered
#: text disagree with what is actually stored — the trick behind names that look
#: harmless in a list view and are something else entirely in the database.
#: Written as escapes on purpose: these are invisible, so a literal in the source
#: is a character nobody can review and any editor may silently mangle.
CONTROL_CHARS_RE = re.compile("[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")


# ---------------------------------------------------------------------------
# 1. begin — verify the human, open a session
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=3600, methods=["POST"])
def begin_intake(turnstile_token=None, sid=None, ref=None):
	"""Verify the Turnstile token and open (or refresh) an intake session.

	Returns ``{"sid", "verdict"}``. A ``Failed`` verdict is returned rather than
	thrown so the page can show the captcha again instead of a dead end.

	An existing ``sid`` is refreshed **in place**, preserving ``created`` and any
	photos already uploaded. Turnstile tokens are single-use and short-lived, so a
	customer who takes a while over the form will re-solve mid-way; minting a new
	session there would silently orphan their uploads and reset the timing floor
	into a false spam positive.

	``ref`` is the invite token from the URL. It is resolved to an invite NAME and
	stored server-side, so the eventual submission is attributed from session
	state rather than from whatever the client posts back at submit time.
	"""
	_require_public_form()

	verdict, detail = _verify_turnstile(turnstile_token)
	invite = _resolve_invite_name(ref)

	session = _get_session(sid) if sid else None
	if session:
		session["turnstile"] = detail
		session["verdict"] = verdict
		# Keep the original attribution: a re-solve must not be able to move a
		# submission from one salesperson to another.
		session.setdefault("invite", invite)
	else:
		sid = frappe.generate_hash(length=32)
		session = {
			"created": int(time.time()),
			"verdict": verdict,
			"turnstile": detail,
			"invite": invite,
			"files": {},
		}

	_put_session(sid, session)
	return {"sid": sid, "verdict": verdict}


def _resolve_invite_name(ref):
	"""Invite docname for a token, or None. Never raises."""
	if not ref:
		return None
	from erpnext_enhancements.crm_enhancements.fountain_move.invites import resolve_invite

	invite = resolve_invite(ref)
	return invite["name"] if invite else None


def _verify_turnstile(token):
	"""Call siteverify. Returns ``(verdict, detail dict)``.

	Verdicts:
	  ``Passed``      — success, and the action/hostname/age assertions all hold.
	  ``Failed``      — Cloudflare said no, or the token is not ours to accept.
	  ``Unavailable`` — we could not reach Cloudflare. NOT treated as a pass: the
	                    submission is kept but parked for a human rather than
	                    auto-converted, so an outage costs a delay, not a flood.
	  ``Not Checked`` — no keys configured (the public form cannot be enabled in
	                    that state; this only arises for desk-created rows).
	"""
	secret = _turnstile_secret()
	if not secret:
		return "Not Checked", {"errors": "Turnstile is not configured."}

	if not token or not TURNSTILE_TOKEN_RE.match(token):
		# Malformed: reject without spending an outbound request on it.
		return "Failed", {"errors": "Malformed captcha token."}

	try:
		import requests

		response = requests.post(
			TURNSTILE_VERIFY_URL,
			data={
				"secret": secret,
				"response": token,
				"remoteip": frappe.local.request_ip or "",
			},
			timeout=TURNSTILE_TIMEOUT,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Fountain Move: Turnstile unreachable", defer_insert=True)
		return "Unavailable", {"errors": "Could not reach the captcha service."}

	if response.status_code >= 500:
		return "Unavailable", {"errors": f"Captcha service returned {response.status_code}."}
	if response.status_code >= 400:
		return "Failed", {"errors": f"Captcha service rejected the request ({response.status_code})."}

	try:
		payload = response.json()
	except ValueError:
		return "Unavailable", {"errors": "Captcha service returned an unreadable response."}

	detail = {
		"hostname": payload.get("hostname"),
		"action": payload.get("action"),
		"challenge_ts": payload.get("challenge_ts"),
		"errors": ", ".join(payload.get("error-codes") or []) or None,
	}

	if not payload.get("success"):
		codes = payload.get("error-codes") or []
		# An internal error on Cloudflare's side is an outage, not a verdict.
		if "internal-error" in codes:
			return "Unavailable", detail
		return "Failed", detail

	# A valid token proves a human solved *a* widget. These assertions prove they
	# solved OUR widget, on OUR site, just now — without them a token minted
	# elsewhere could be replayed here.
	if detail["action"] and detail["action"] != TURNSTILE_ACTION:
		detail["errors"] = f"Unexpected action {detail['action']!r}."
		return "Failed", detail
	if not _hostname_allowed(detail["hostname"]):
		detail["errors"] = f"Unexpected hostname {detail['hostname']!r}."
		return "Failed", detail
	if not _challenge_fresh(detail["challenge_ts"]):
		detail["errors"] = "Captcha challenge was too old."
		return "Failed", detail

	return "Passed", detail


def _hostname_allowed(hostname):
	if not hostname:
		return True  # Cloudflare omits it for some widget types; not our call to fail on
	from urllib.parse import urlparse

	site_host = (urlparse(frappe.utils.get_url()).netloc or "").split(":")[0].lower()
	return hostname.lower() in {site_host, f"www.{site_host}"} or not site_host


def _challenge_fresh(challenge_ts):
	if not challenge_ts:
		return True
	try:
		from frappe.utils import get_datetime

		solved = get_datetime(challenge_ts.replace("Z", "").replace("T", " ").split(".")[0])
		age = (now_datetime() - solved).total_seconds()
		return abs(age) <= TURNSTILE_MAX_AGE
	except Exception:
		return True  # unparseable timestamp is Cloudflare's format changing, not an attack


def _turnstile_secret():
	try:
		settings = frappe.get_cached_doc("ERPNext Enhancements Settings")
		return settings.get_password("fountain_move_turnstile_secret_key", raise_exception=False)
	except Exception:
		return None


# ---------------------------------------------------------------------------
# 2. upload — one photo at a time
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=3600, methods=["POST"])
def upload_intake_photo(sid=None, kind=None):
	"""Accept one photo and return an opaque handle.

	Never returns a ``file_url``: the caller is anonymous and the file is private,
	so handing back a URL would give a stranger a link to someone's property. The
	handle is only meaningful inside this session, and ``submit_intake`` reads the
	photos from the session rather than trusting anything the client sends back.
	"""
	_require_public_form()

	session = _get_session(sid)
	if not session:
		frappe.throw(_("Your session has expired. Please reload the page."))
	if session.get("verdict") != "Passed":
		frappe.throw(_("Please complete the verification check first."))
	if kind not in ("fountain", "path"):
		frappe.throw(_("Unknown photo type."))

	# Atomic per-kind counter — a read-modify-write on the session blob would
	# race two parallel uploads and let both through. Allows a couple of retries
	# (a flaky mobile connection is normal) but not unbounded re-uploads.
	if _bump_counter(f"{UPLOAD_COUNT_PREFIX}{sid}:{kind}", SESSION_TTL) > 3:
		frappe.throw(_("Too many uploads for this photo. Please reload the page."))

	files = getattr(frappe.request, "files", None)
	upload = files.get("file") if files else None
	if not upload:
		frappe.throw(_("No photo was received."))

	cap = _photo_byte_cap()
	# Read one byte past the cap so an oversized file is detected without ever
	# holding the whole thing in memory.
	content = upload.stream.read(cap + 1)
	if len(content) > cap:
		frappe.throw(
			_("That photo is larger than {0} MB. Please choose a smaller one.").format(
				cap // (1024 * 1024)
			)
		)
	if not content:
		frappe.throw(_("That photo appeared to be empty."))

	extension, _mime = _sniff_image(content)
	_assert_sane_dimensions(content)

	# The client-supplied filename is discarded entirely — it is attacker-chosen
	# text that would otherwise reach the filesystem and the File docname.
	filename = f"fmi-{sid[:8]}-{kind}.{extension}"

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"content": content,
			"is_private": 1,
			"folder": _intake_folder(),
			# Deliberately unattached: the request row does not exist yet. The
			# hourly GC sweeps anything that never gets claimed.
			"attached_to_doctype": None,
			"attached_to_name": None,
		}
	)
	file_doc.insert(ignore_permissions=True)

	session.setdefault("files", {})[kind] = file_doc.name
	_put_session(sid, session)
	frappe.db.commit()

	return {"handle": kind, "ok": True}


def _photo_byte_cap():
	"""Our cap, or the site's, whichever is lower — read at request time."""
	from frappe.core.api.file import get_max_file_size

	configured = cint(
		frappe.db.get_single_value("ERPNext Enhancements Settings", "fountain_move_max_photo_mb")
	)
	ours = (configured * 1024 * 1024) if configured > 0 else PHOTO_BYTE_CAP
	try:
		return min(ours, get_max_file_size())
	except Exception:
		return ours


def _sniff_image(content):
	"""Identify the image by its magic bytes. Extensions are not evidence."""
	for signature, extension, mime in MAGIC_SIGNATURES:
		if content.startswith(signature):
			return extension, mime
	# WebP is RIFF....WEBP — a prefix check alone would accept any RIFF container.
	if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
		return "webp", "image/webp"
	frappe.throw(_("Please upload a JPEG, PNG or WebP photo."))


def _assert_sane_dimensions(content):
	"""Reject decompression bombs using the header only — never decode the pixels.

	``MAX_IMAGE_PIXELS`` is deliberately not assigned: it is process-global, so
	changing it here would silently alter every other image operation in the
	worker. Pillow's own bomb error is caught and treated as a rejection.
	"""
	try:
		from PIL import Image
	except ImportError:
		return  # Pillow absent; the byte cap is still enforced

	try:
		with Image.open(io.BytesIO(content)) as image:
			width, height = image.size
	except Exception as exc:
		if type(exc).__name__ == "DecompressionBombError":
			frappe.throw(_("That photo's dimensions are too large."))
		frappe.throw(_("That photo could not be read. Please try another."))

	if width * height > MAX_TOTAL_PIXELS:
		frappe.throw(_("That photo's dimensions are too large. Please upload a smaller photo."))


def _intake_folder():
	"""The intake folder, created on first use."""
	if frappe.db.exists("File", INTAKE_FILE_FOLDER):
		return INTAKE_FILE_FOLDER
	try:
		folder = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "Fountain Move Intake",
				"is_folder": 1,
				"folder": "Home",
			}
		)
		folder.insert(ignore_permissions=True)
		return folder.name
	except Exception:
		return "Home/Attachments"


# ---------------------------------------------------------------------------
# 3. submit
# ---------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=15, seconds=3600, methods=["POST"])
def submit_intake(**payload):
	"""Accept the completed form and create a Fountain Move Request.

	Returns ``{"ok": True, "reference": <name>}``. The response is deliberately
	identical for a genuine submission and a spam one — telling a bot it was
	caught only teaches it what to change.
	"""
	_require_public_form()

	sid = payload.get("sid")
	session = _get_session(sid)
	if not session:
		frappe.throw(_("Your session has expired. Please reload the page and try again."))

	raw_body = _raw_body()
	honeypot_tripped = HONEYPOT_FIELD_NAME in raw_body
	too_fast = (int(time.time()) - int(session.get("created") or 0)) < MIN_FILL_SECONDS
	verdict = session.get("verdict") or "Not Checked"

	spam_reason = None
	if honeypot_tripped:
		spam_reason = "Honeypot field was present in the submission."
	elif too_fast:
		spam_reason = "Form was submitted faster than a person can fill it in."
	elif verdict == "Failed":
		spam_reason = "Captcha verification failed."

	if not spam_reason:
		_enforce_submission_caps(sid, payload.get("email"))

	fields = _validate_payload(payload)

	# Collapse an accidental double-submit (double click, retried request) rather
	# than creating two identical leads.
	fingerprint = _fingerprint(fields)
	duplicate = _recent_duplicate(fingerprint)
	if duplicate:
		return {"ok": True, "reference": duplicate}

	request = frappe.new_doc("Fountain Move Request")
	# Explicit assignment from the allowlist. NEVER doc.update(payload).
	for payload_key, fieldname in INTAKE_FIELD_MAP.items():
		if payload_key in fields:
			request.set(fieldname, fields[payload_key])

	request.status = "Spam" if spam_reason else "New"
	request.source_channel = "Emailed Link" if session.get("invite") else "Public Form"
	request.invite = session.get("invite")
	request.submitted_on = now_datetime()
	request.terms_accepted_on = now_datetime()
	request.terms_version = _terms_version()
	request.purchase_location_address = get_store_address(fields.get("purchase_location"))
	request.turnstile_verdict = verdict
	request.turnstile_hostname = (session.get("turnstile") or {}).get("hostname")
	request.turnstile_action = (session.get("turnstile") or {}).get("action")
	request.turnstile_challenge_ts = (session.get("turnstile") or {}).get("challenge_ts")
	request.turnstile_errors = (session.get("turnstile") or {}).get("errors")
	request.honeypot_tripped = 1 if honeypot_tripped else 0
	request.spam_reason = spam_reason
	request.submission_fingerprint = fingerprint
	request.submitter_ip_claimed = frappe.local.request_ip
	request.submitter_ip_peer = getattr(frappe.request, "remote_addr", None)
	request.user_agent = (frappe.get_request_header("User-Agent") or "")[:512]
	request.raw_payload = _safe_payload(fields)
	request.referral_partner = _referral_partner()

	# Spam gets a metadata-only row: no photos attached, so a bot cannot use the
	# form as free private file storage, and nothing downstream is touched.
	request.insert(ignore_permissions=True)

	if not spam_reason:
		_attach_session_photos(request, session)

	if session.get("invite"):
		from erpnext_enhancements.crm_enhancements.fountain_move.invites import (
			mark_invite_submitted,
		)

		mark_invite_submitted(session["invite"], request.name)

	frappe.db.commit()
	_clear_session(sid)

	if not spam_reason and verdict == "Passed" and fountain_move_auto_convert_enabled():
		request.db_set("status", "Queued", update_modified=False)
		frappe.enqueue(
			"erpnext_enhancements.crm_enhancements.fountain_move.conversion.run_conversion",
			queue="long",
			enqueue_after_commit=True,
			job_id=f"fmr-convert-{request.name}",
			deduplicate=True,
			docname=request.name,
		)

	return {"ok": True, "reference": request.name}


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _validate_payload(payload):
	"""Coerce and bound every allowlisted field. Throws on anything unusable."""
	fields = {}

	for key in INTAKE_FIELD_MAP:
		value = payload.get(key)

		if key in CHECKBOX_FIELDS:
			fields[key] = 1 if str(value).strip().lower() in ("1", "true", "on", "yes") else 0
			continue

		if key in ("latitude", "longitude"):
			fields[key] = flt(value) if value not in (None, "") else None
			continue

		if key == "fountain_weight_lbs":
			fields[key] = flt(value)
			continue

		text = "" if value is None else str(value).strip()
		if CONTROL_CHARS_RE.search(text):
			frappe.throw(_("Please remove any unusual characters from your answers."))
		limit = FIELD_MAX_LENGTHS.get(key)
		if limit and len(text) > limit:
			frappe.throw(_("One of your answers is too long. Please shorten it and try again."))
		fields[key] = text

	_require(fields, "first_name", _("Please enter your first name."))
	_require(fields, "last_name", _("Please enter your last name."))
	_require(fields, "address_line1", _("Please enter the street address."))
	_require(fields, "city", _("Please enter the city."))
	_require(fields, "state", _("Please enter the state."))
	_require(fields, "pincode", _("Please enter the postal code."))

	fields["email"] = _valid_email(fields.get("email"))
	fields["phone"] = _valid_phone(fields.get("phone"))

	if fields.get("property_type") not in PROPERTY_TYPES:
		frappe.throw(_("Please choose whether this is a residential or commercial property."))

	# The dropdown is client-side markup; an anonymous caller can post anything.
	if not is_valid_store_location(fields.get("purchase_location")):
		frappe.throw(_("Please choose which store the fountain was purchased at."))

	if flt(fields.get("fountain_weight_lbs")) <= 0:
		frappe.throw(_("Please enter the weight of the fountain in pounds."))
	if flt(fields.get("fountain_weight_lbs")) > 20000:
		frappe.throw(_("Please check the fountain weight and enter it in pounds."))

	if not fields.get("terms_accepted"):
		frappe.throw(_("Please accept the Terms of Use and Privacy Policy to continue."))

	return fields


def _require(fields, key, message):
	if not fields.get(key):
		frappe.throw(message)


def _valid_email(value):
	from frappe.utils import validate_email_address

	email = (value or "").strip().lower()
	# validate_email_address is a comma-splitting extractor, not a predicate: it
	# returns the valid parts joined. Reject separators first, then require that
	# it hands back exactly what we gave it.
	if not email or re.search(r"[,;\s]", email):
		frappe.throw(_("Please enter a single valid email address."))
	if validate_email_address(email) != email:
		frappe.throw(_("Please enter a valid email address."))
	return email


def _valid_phone(value):
	from erpnext_enhancements.utils.phone import normalize_phone

	phone = (value or "").strip()
	if len(normalize_phone(phone)) < 10:
		frappe.throw(_("Please enter a full 10-digit phone number."))
	return phone


# ---------------------------------------------------------------------------
# abuse counters, sessions, helpers
# ---------------------------------------------------------------------------


def _bump_counter(key, ttl):
	"""Atomically increment a namespaced counter and return its new value.

	``incrby`` comes from ``redis.Redis`` rather than frappe's wrapper, so it does
	NOT get the wrapper's per-site key prefix for free — on a shared bench two
	sites would otherwise increment the same counter and rate-limit each other.
	``make_key`` applies the same ``db_name|`` prefix the rest of the cache uses.

	Atomicity is the point: a read-modify-write on the session blob would let two
	concurrent requests both observe the old value and both pass the check.
	"""
	namespaced = frappe.cache.make_key(key)
	count = frappe.cache.incrby(namespaced, 1)
	if count == 1:
		# Only the creator sets the TTL, so a burst cannot keep pushing expiry out.
		frappe.cache.expire(namespaced, ttl)
	return count


def _enforce_submission_caps(sid, email):
	"""Per-session and per-email ceilings, on top of the IP-keyed decorator.

	These exist precisely because the decorator's IP dimension is spoofable
	(see the module docstring). A caller who forges X-Forwarded-For still cannot
	forge their way past a counter keyed on the session they must hold and the
	email address they claim.
	"""
	if _bump_counter(f"{SUBMIT_COUNT_PREFIX}{sid}", SESSION_TTL) > 3:
		frappe.throw(_("This form has already been submitted. Please reload the page."))

	if email:
		digest = hashlib.sha256(str(email).strip().lower().encode()).hexdigest()[:32]
		if _bump_counter(f"{EMAIL_COUNT_PREFIX}{digest}", 86400) > 5:
			frappe.throw(
				_("We've already received several requests from this email address today. "
				  "Please call us instead so we can help properly.")
			)


def _fingerprint(fields):
	basis = "|".join(
		str(fields.get(key) or "").strip().lower()
		for key in ("email", "phone", "address_line1", "pincode", "fountain_weight_lbs", "purchase_location")
	)
	return hashlib.sha256(basis.encode()).hexdigest()[:40]


def _recent_duplicate(fingerprint):
	"""An identical submission within 10 minutes is a double-click, not a new job."""
	from frappe.utils import add_to_date

	return frappe.db.get_value(
		"Fountain Move Request",
		{
			"submission_fingerprint": fingerprint,
			"creation": [">", add_to_date(None, minutes=-10)],
			"status": ["not in", ("Spam", "Rejected")],
		},
		"name",
	)


def _attach_session_photos(request, session):
	"""Claim the uploads recorded in the session onto the new request.

	Read from the session, never from the payload — otherwise an anonymous caller
	could name any File on the site and have it attached to (and then copied out
	of) their own request.
	"""
	mapping = {"fountain": "fountain_photo", "path": "path_photo"}
	for kind, file_name in (session.get("files") or {}).items():
		fieldname = mapping.get(kind)
		if not fieldname or not file_name or not frappe.db.exists("File", file_name):
			continue
		try:
			file_doc = frappe.get_doc("File", file_name)
			file_doc.db_set(
				{
					"attached_to_doctype": "Fountain Move Request",
					"attached_to_name": request.name,
					"attached_to_field": fieldname,
				},
				update_modified=False,
			)
			request.db_set(fieldname, file_doc.file_url, update_modified=False)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(), "Fountain Move: photo attach", defer_insert=True
			)


def _safe_payload(fields):
	"""The submission as received, for audit — bounded, and without image bytes."""
	try:
		return frappe.as_json(fields)[:64000]
	except Exception:
		return None


def _terms_version():
	"""Which revision of the terms the customer agreed to.

	Uses the Web Page's own modified stamp so the record survives the copy being
	rewritten later — "they accepted the version in force on this date".
	"""
	modified = frappe.db.get_value("Web Page", "terms-of-use", "modified")
	return f"terms-of-use@{str(modified)[:10]}" if modified else "terms-of-use@unversioned"


def _referral_partner():
	from erpnext_enhancements.crm_enhancements.fountain_move import DEFAULT_LEAD_SOURCE

	return DEFAULT_LEAD_SOURCE if frappe.db.exists("Lead Source", DEFAULT_LEAD_SOURCE) else None


def _raw_body():
	"""The request body as text, before frappe sanitised form_dict.

	The honeypot check needs to know whether the KEY was present, which a
	sanitised, blanked value no longer tells us.
	"""
	try:
		if getattr(frappe.request, "form", None):
			return " ".join(frappe.request.form.keys())
		return frappe.request.get_data(as_text=True) or ""
	except Exception:
		return ""


def _require_public_form():
	"""404 rather than explain ourselves to an anonymous caller.

	A disabled feature is not an invitation to probe. ``frappe.throw`` with a
	404 keeps the response indistinguishable from a route that does not exist.
	"""
	if not fountain_move_public_form_enabled():
		raise frappe.DoesNotExistError


def _session_key(sid):
	return f"{SESSION_PREFIX}{sid}"


def _get_session(sid):
	if not sid or not re.match(r"^[A-Za-z0-9]{8,64}$", str(sid)):
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


# ---------------------------------------------------------------------------
# housekeeping
# ---------------------------------------------------------------------------


def gc_orphan_intake_files():
	"""Delete photos uploaded by someone who never submitted the form.

	Hourly. Without this, an anonymous caller could use the upload endpoint as
	free private storage, and abandoned sessions would accumulate files nothing
	references. Only touches unattached files in our own folder, older than a day.
	"""
	from frappe.utils import add_to_date

	orphans = frappe.get_all(
		"File",
		filters={
			"folder": INTAKE_FILE_FOLDER,
			"attached_to_name": ["in", (None, "")],
			"creation": ["<", add_to_date(None, hours=-24)],
			"is_folder": 0,
		},
		pluck="name",
		limit=200,
	)
	for name in orphans:
		try:
			frappe.delete_doc("File", name, ignore_permissions=True, force=True, delete_permanently=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Fountain Move: orphan file GC", defer_insert=True)
	if orphans:
		frappe.db.commit()
	return len(orphans)
