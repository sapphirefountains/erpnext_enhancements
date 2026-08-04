# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Machine-to-machine ingress for website contact-form submissions.

The public marketing site is **WordPress on WP Engine behind Cloudflare**
(``www.sapphirefountains.com``); ERPNext is a different host entirely
(``erp.sapphirefountains.com``). So a website enquiry cannot become a Lead by
being typed into a frappe Web Form -- something on the WordPress side has to
forward it. This module is the ERPNext half of that contract, and it is
deliberately the *only* half that lives in this repo.

**Contract summary** (the full version, including the capture snippet the
WordPress side needs, is in ``docs/attribution-runbook.md``)::

    POST https://erp.sapphirefountains.com/api/method/
         erpnext_enhancements.crm_enhancements.web_lead.submit_web_lead
    Authorization: Bearer <web_lead_shared_secret>
    Content-Type: application/json

    {
      "first_name": "Jane", "last_name": "Doe",
      "email_id": "jane@example.com", "mobile_no": "801-555-0100",
      "company_name": "Doe Landscapes", "notes": "Interested in a courtyard fountain",
      "utm_source": "google", "utm_medium": "cpc", "utm_campaign": "summer-2026",
      "utm_content": "hero-cta", "utm_term": "fountain installer",
      "gclid": "Cj0KCQ...", "landing_page": "/fountains/commercial",
      "first_referrer": "https://www.google.com/",
      "form_name": "contact-us", "hp_company_url": ""
    }

    -> 200 {"status": "accepted", "lead": "CRM-LEAD-2026-00123"}

## Why a shared secret and not Turnstile

Turnstile answers "is this a browser driven by a human", which is the right
question for ``fountain_move/intake.py`` -- the app's one genuinely
unauthenticated write path, where the caller is a member of the public with no
credential. It is the wrong question here. The caller is a **server**: the
WordPress site, posting after it has already run its own form's spam controls.
A server can hold a secret, so it should, and this endpoint is gated the same way
every other machine-to-machine webhook in this app is
(``mdm_integration/utils.verify_webhook_bearer``, ``api/telephony.py``).

That choice has a consequence worth being explicit about: **this endpoint is only
as trustworthy as WordPress's own spam filtering.** It does not attempt to
re-adjudicate spam. It records ``form_name`` and the claimed remote address so a
flood is attributable after the fact, honours a honeypot if the sending form
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
* **``X-Forwarded-For`` is advisory.** ``auth.py`` takes the first entry
  unconditionally, so behind an *appending* proxy it is attacker-controlled. We
  store it for forensics and never make it the sole basis of a decision. Confirm
  the Cloudflare -> GCLB -> bench chain overwrites rather than appends before
  trusting it for anything.
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

from erpnext_enhancements.crm_enhancements import attribution

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

	source = attribution.resolve_lead_source(values, explicit=payload.get("lead_source"))
	if source and hasattr(lead, "custom_lead_source"):
		lead.custom_lead_source = source

	# _fill_blanks rather than direct assignment, so the "first touch wins" rule
	# has exactly one implementation even on a brand-new document.
	attribution._fill_blanks(lead, values)
	attribution.stamp_capture_time(lead)

	owner = _default_owner()
	if owner:
		lead.lead_owner = owner

	try:
		lead.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Web Lead ingress: insert failed")
		frappe.local.response["http_status_code"] = 500
		return {"status": "rejected"}

	_record_submission_context(lead, payload)

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
	"""Constant-time Bearer check against the configured shared secret.

	Fails closed: an unset secret means nothing is authorised, so a half-configured
	site cannot be written to by anyone who guesses the URL.
	"""
	settings = _settings()
	try:
		secret = settings.get_password("web_lead_shared_secret", raise_exception=False)
	except Exception:
		secret = None
	if not secret:
		return False

	header = frappe.get_request_header("Authorization") or ""
	prefix = "Bearer "
	if not header.startswith(prefix):
		return False
	provided = header[len(prefix) :].strip()
	if not provided:
		return False

	# compare_digest rejects non-ASCII str operands; encode both sides.
	return hmac.compare_digest(provided.encode("utf-8"), secret.encode("utf-8"))


def _honeypot_tripped():
	raw = _raw_body_keys()
	for key in HONEYPOT_KEYS:
		if key in raw and (raw.get(key) or "").strip():
			return True
	return False


def _raw_body_keys():
	"""The request body as a flat dict, read before frappe's sanitisation where
	possible. Falls back to form_dict, which is always populated."""
	try:
		data = frappe.request.get_data(as_text=True) if frappe.request else ""
		if data:
			parsed = json.loads(data)
			if isinstance(parsed, dict):
				return {k: (v if isinstance(v, str) else "") for k, v in parsed.items()}
	except Exception:
		pass
	return {k: (v if isinstance(v, str) else "") for k, v in (frappe.form_dict or {}).items()}


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


def _default_owner():
	"""Configured triage owner, or None.

	Deliberately not guessed: routing real enquiries to the wrong person is worse
	than leaving them unassigned, where the missing-source report will surface
	them. Same reasoning as ``fmr_default_owner`` in the fountain-move flow.
	"""
	owner = (_settings().get("web_lead_default_owner") or "").strip()
	if owner and frappe.db.exists("User", owner):
		return owner
	return None


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

		context = {
			"form_name": (payload.get("form_name") or "")[:140],
			"claimed_ip": (frappe.get_request_header("X-Forwarded-For") or "")[:200],
			"user_agent": (frappe.get_request_header("User-Agent") or "")[:200],
		}
		lead.add_comment(
			"Comment",
			_("Submitted via website ingress: {0}").format(
				", ".join(f"{k}={v}" for k, v in context.items() if v) or _("no context")
			),
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Web Lead ingress: context comment failed")
