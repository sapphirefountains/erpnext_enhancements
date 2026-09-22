# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Machine-to-machine ingress for website contact-form submissions.

The public marketing site is **WordPress on WP Engine behind Cloudflare**
(``www.sapphirefountains.com``); ERPNext is a different host entirely
(``erp.sapphirefountains.com``). So a website enquiry cannot become a Lead by
being typed into a frappe Web Form -- something on the WordPress side has to
forward it. This module is the ERPNext half of that contract, and it is
deliberately the *only* half that lives in this repo.

**Contract summary** (the full version is in ``docs/attribution-runbook.md``;
the WordPress half is in ``docs/website-capture/``)::

    POST https://erp.sapphirefountains.com/api/method/
         erpnext_enhancements.crm_enhancements.web_lead.submit_web_lead
    X-Web-Lead-Secret: <web_lead_shared_secret>
    Content-Type: application/json

    {
      "first_name": "Jane", "last_name": "Doe",
      "email_id": "jane@example.com", "mobile_no": "801-555-0100",
      "company_name": "Doe Landscapes", "notes": "Interested in a courtyard fountain",
      "utm_source": "google", "utm_medium": "cpc", "utm_campaign": "summer-2026",
      "utm_id": "21456789012", "utm_content": "hero-cta", "utm_term": "fountain installer",
      "gclid": "Cj0KCQ...", "landing_page": "/fountains/commercial",
      "first_referrer": "https://www.google.com/",
      "form_name": "contact-us", "hp_company_url": ""
    }

    -> 200 {"status": "accepted", "lead": "CRM-LEAD-2026-00123"}

## Why a custom header and not ``Authorization: Bearer``

This endpoint shipped (v1.241.0) expecting ``Authorization: Bearer <secret>``,
and on Frappe v16 that request **never reaches this function**.
``frappe.auth.validate_auth`` runs first, treats any two-part ``Authorization``
header as a credential, tries it as an OAuth bearer token and then an API key,
and raises ``AuthenticationError`` (HTTP 401, ``{"exc_type":
"AuthenticationError"}``) when neither yields a user. Verified on production
2026-09-22: the same POST returned our own ``{"status": "rejected"}`` without the
header and Frappe's 401 with ``Authorization: Bearer <anything>``. Nothing had
ever called the endpoint, so nobody saw it -- and a real submission would have
failed in a way indistinguishable from a wrong secret.

So the secret travels in ``X-Web-Lead-Secret``, which Frappe does not interpret.
Never put it in ``Authorization``.

## Why a shared secret and not Turnstile

Turnstile answers "is this a browser driven by a human", which is the right
question for ``fountain_move/intake.py`` -- the app's one genuinely
unauthenticated write path, where the caller is a member of the public with no
credential. It is the wrong question here. The caller is a **server**: the
WordPress site, posting after it has already run its own form's spam controls.
A server can hold a secret, so it should: a shared secret in a request header,
compared in constant time, failing closed.

That choice has a consequence worth being explicit about: **this endpoint is only
as trustworthy as WordPress's own spam filtering.** It does not attempt to
re-adjudicate spam. It records ``form_name`` and the caller's address (WordPress's
egress, not the visitor's) so a flood is attributable after the fact, honours a
honeypot if the sending form
supplies one, and rate-limits -- but a compromised or misconfigured WordPress
install can create Leads. Accepted: the alternative is duplicating Turnstile on a
form we do not control.

## Security notes

* **Mass assignment.** The Lead is built from ``LEAD_FIELD_MAP`` and the
  attribution allowlist in ``attribution.normalize_payload``, never from a
  splatted payload. Guest inserts require ``ignore_permissions=True``, under
  which frappe's permlevel check returns early -- so ``read_only`` in a DocType
  JSON stops nothing and the allowlist is the actual control.
* **Never POST a key named ``sid``.** frappe pops it during auth to resume a
  *login* session, before the handler binds arguments. Anything named ``sid``
  here is silently swallowed and the request downgrades to Guest.
* **The client address is a rate-limit key, never a credential.** ``auth.py``
  takes the first ``X-Forwarded-For`` entry unconditionally. On this host the
  chain is GCLB -> nginx -> bench (no Cloudflare in front of erp), and nginx's
  realip file rewrites that header to the real caller, spoof-proof -- verified
  2026-09-22. That makes the rate limit per caller, but only while the file is
  on the VM (``utils/client_ip.py`` checks daily). So the address is recorded
  for forensics and keys the limit; the shared secret is what decides. Every
  genuine submission comes from WP Engine's egress address, so the 120/hour is
  the WordPress site's own budget, and a stranger without the secret spends
  their own bucket rather than the site's.
* **Errors are generic.** A duplicate-email check would turn this into an oracle
  for "is this person a customer of yours?", so there isn't one; de-duplication
  is a downstream review problem, not a response-code problem.

## Turning it off

``web_lead_ingress_enabled`` in ERPNext Enhancements Settings. Off by default --
the app's staged-rollout convention -- and while off the endpoint returns a
generic refusal without touching the database.
"""

import hmac
import json

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint

from erpnext_enhancements.crm_enhancements import attribution, lead_triage
from erpnext_enhancements.utils.error_throttle import log_error_throttled

#: Inbound key -> Lead fieldname. The complete set of non-attribution fields this
#: endpoint will write. Anything not listed is dropped.
LEAD_FIELD_MAP = {
	"first_name": "first_name",
	"last_name": "last_name",
	"lead_name": "lead_name",
	"email_id": "email_id",
	"mobile_no": "mobile_no",
	"phone": "phone",
	"company_name": "company_name",
	"website": "website",
	"city": "city",
	"state": "state",
	"country": "country",
}

#: Per-field truncation for the mapped Lead fields. frappe's own varchar(140)
#: would raise on overflow, which would reject an otherwise good enquiry.
LEAD_FIELD_MAX_LENGTH = 140

#: Free-text goes to a Comment rather than a field: Lead has no notes field we
#: control, and a Comment keeps the customer's own words verbatim and timestamped.
NOTES_KEYS = ("notes", "message", "comments", "enquiry")

#: Honeypot. Present-and-non-empty means a bot filled a field a human cannot see.
#: Checked on the raw body, not the parsed value -- frappe runs sanitize_html over
#: a guest's form_dict and can blank the very value we wanted to catch.
HONEYPOT_KEYS = ("hp_company_url", "hp_website")

#: Cap on the stored raw payload, so a hostile body cannot bloat the Lead's
#: comment thread.
RAW_PAYLOAD_CAP = 4000

#: The request header carrying the shared secret. NOT ``Authorization`` -- see the
#: module docstring: Frappe v16 rejects that header before this code runs.
SECRET_HEADER = "X-Web-Lead-Secret"

#: A secret shorter than this is treated as unset. It is the ingress's only
#: credential and the rate limit allows 120 guesses an hour per address, so a
#: guessable one is a world-writable Lead table. 32 is what
#: ``python3 -c "import secrets; print(secrets.token_urlsafe(32))"`` comfortably
#: exceeds (43 characters).
MIN_SECRET_LENGTH = 32

#: Paid-click IDs with no Lead field of their own (see
#: ``attribution.PAID_CLICK_ID_KEYS``). Kept in the submission comment so the
#: evidence is not thrown away; gclid is omitted because it has a field.
UNSTORED_CLICK_ID_KEYS = ("gbraid", "wbraid", "msclkid")


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=120, seconds=3600, methods=["POST"])
def submit_web_lead(**payload):
	"""Create a Lead from a website contact-form submission.

	Returns ``{"status": "accepted", "lead": "<name>"}`` on success. Every failure
	mode that is not a configuration error returns ``{"status": "rejected"}`` with
	no detail, on purpose.
	"""
	if not _ingress_enabled():
		# Not a throw: a disabled ingress should look inert to the caller, not
		# like a broken endpoint worth retrying.
		return {"status": "rejected"}

	if not _authorized():
		frappe.local.response["http_status_code"] = 401
		return {"status": "unauthorized"}

	if _honeypot_tripped():
		# Return success. A bot that learns it was caught adapts; one that thinks
		# it succeeded does not.
		return {"status": "accepted", "lead": None}

	values = attribution.normalize_payload(payload)
	lead_fields = _mapped_lead_fields(payload)

	if not (lead_fields.get("email_id") or lead_fields.get("mobile_no") or lead_fields.get("phone")):
		# No way to contact them: not a lead, and inserting it would create a row
		# nobody can action.
		frappe.local.response["http_status_code"] = 400
		return {"status": "rejected", "reason": "no_contact_method"}

	lead = frappe.get_doc({"doctype": "Lead", **lead_fields})

	source = attribution.resolve_lead_source(
		values,
		explicit=payload.get("lead_source"),
		paid_click=attribution.has_paid_click_id(payload),
	)
	if source and hasattr(lead, "custom_lead_source"):
		lead.custom_lead_source = source

	# _fill_blanks rather than direct assignment, so the "first touch wins" rule
	# has exactly one implementation even on a brand-new document.
	attribution._fill_blanks(lead, values)
	attribution.stamp_capture_time(lead)

	# An owner (the named triage owner, else the Sales Team rotation) and, when the
	# SLA is on, a first-response deadline. See lead_triage.py; never raises.
	lead_triage.prepare_inbound_lead(lead)

	try:
		lead.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Web Lead ingress: insert failed")
		frappe.local.response["http_status_code"] = 500
		return {"status": "rejected"}

	_record_submission_context(lead, payload)
	lead_triage.assign_inbound_lead(lead)

	return {"status": "accepted", "lead": lead.name}


# ------------------------------------------------------------------- gating


def _settings():
	return frappe.get_cached_doc("ERPNext Enhancements Settings")


def _ingress_enabled():
	settings = _settings()
	return bool(cint(settings.get("lead_attribution_enabled") or 0)) and bool(
		cint(settings.get("web_lead_ingress_enabled") or 0)
	)


def _authorized():
	"""Constant-time check of ``X-Web-Lead-Secret`` against the shared secret.

	Fails closed: an unset secret -- or one too short to be a secret, see
	``MIN_SECRET_LENGTH`` -- authorises nothing, so a half-configured site cannot
	be written to by anyone who guesses the URL. The short-secret case is logged
	(at most daily): it looks exactly like a broken integration from outside, and
	the fix is on this side.

	Deliberately never reads ``Authorization``: see the module docstring.
	"""
	settings = _settings()
	try:
		secret = settings.get_password("web_lead_shared_secret", raise_exception=False)
	except Exception:
		secret = None
	if not secret:
		return False
	if len(secret) < MIN_SECRET_LENGTH:
		log_error_throttled(
			f"web_lead_shared_secret is shorter than {MIN_SECRET_LENGTH} characters, so the website "
			"ingress refuses every submission. Generate one with "
			'`python3 -c "import secrets; print(secrets.token_urlsafe(32))"` and set it here and in '
			f"the Fluent Forms webhook's {SECRET_HEADER} header together.",
			"Web Lead ingress: secret too short",
			window=86400,
			limit=1,
		)
		return False

	provided = (frappe.get_request_header(SECRET_HEADER) or "").strip()
	if not provided:
		return False

	# compare_digest rejects non-ASCII str operands; encode both sides.
	return hmac.compare_digest(provided.encode("utf-8"), secret.encode("utf-8"))


def _honeypot_tripped():
	"""True when a honeypot key arrived carrying anything at all.

	A non-string counts: Fluent Forms only ever sends strings, so a number or a
	list in a field a human cannot see is a hand-rolled bot, not a quirk.
	"""
	raw = _raw_body()
	for key in HONEYPOT_KEYS:
		if key not in raw:
			continue
		value = raw.get(key)
		if isinstance(value, str):
			if value.strip():
				return True
		elif value is not None:
			return True
	return False


def _raw_body():
	"""The request body as a flat dict, read before frappe's sanitisation where
	possible. Falls back to form_dict, which is always populated. Values are
	left as sent; callers decide what a non-string means."""
	try:
		data = frappe.request.get_data(as_text=True) if frappe.request else ""
		if data:
			parsed = json.loads(data)
			if isinstance(parsed, dict):
				return parsed
	except Exception:
		pass
	return dict(frappe.form_dict or {})


def _text(value, limit):
	"""A payload value as a trimmed, capped string; anything else as ""."""
	return value.strip()[:limit] if isinstance(value, str) else ""


# ---------------------------------------------------------------- field mapping


def _mapped_lead_fields(payload):
	"""Build the Lead's own fields from the allowlist. Never splats the payload."""
	fields = {}
	for key, fieldname in LEAD_FIELD_MAP.items():
		value = (payload.get(key) or "").strip() if isinstance(payload.get(key), str) else ""
		if value:
			fields[fieldname] = value[:LEAD_FIELD_MAX_LENGTH]

	# Lead requires a display name. erpnext derives it from first/last name, but a
	# form that only collected a company name would otherwise fail validation.
	if not fields.get("lead_name"):
		derived = " ".join(x for x in (fields.get("first_name"), fields.get("last_name")) if x)
		fields["lead_name"] = derived or fields.get("company_name") or fields.get("email_id") or _("Website Enquiry")

	fields["status"] = "Lead"
	return fields


def _record_submission_context(lead, payload):
	"""Attach the customer's own words and the forensic context as Comments.

	Two separate comments rather than one: the enquiry text is what a salesperson
	needs to read, and burying it under a wall of headers guarantees nobody does.
	Failures here are logged and swallowed -- the Lead already exists and is the
	thing that mattered.
	"""
	try:
		notes = ""
		for key in NOTES_KEYS:
			value = payload.get(key)
			if isinstance(value, str) and value.strip():
				notes = value.strip()[:RAW_PAYLOAD_CAP]
				break
		if notes:
			lead.add_comment("Comment", _("Website enquiry:\n\n{0}").format(notes))

		# caller_ip is the address that POSTed -- WordPress's server, not the visitor.
		# request_ip rather than the raw header: since 2026-08-03 nginx rewrites it to
		# the real caller, and utils/client_ip.py watches that it stays so.
		context = {
			"form_name": _text(payload.get("form_name"), 140),
			"caller_ip": (getattr(frappe.local, "request_ip", None) or "")[:64],
			"user_agent": (frappe.get_request_header("User-Agent") or "")[:200],
		}
		for key in UNSTORED_CLICK_ID_KEYS:
			context[key] = _text(payload.get(key), 255)
		lead.add_comment(
			"Comment",
			_("Submitted via website ingress: {0}").format(
				", ".join(f"{k}={v}" for k, v in context.items() if v) or _("no context")
			),
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Web Lead ingress: context comment failed")
