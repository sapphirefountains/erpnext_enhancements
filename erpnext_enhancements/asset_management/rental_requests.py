# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The public rental request form, ``/rent-a-fountain`` (v1.565.0).

Nik's call, 2026-09-29: **a request form only** — the event, its dates, the venue and who to call
back become an Events **Lead**, and nothing about the fleet's availability is shown publicly (the
schedule is a competitor's map of our calendar). Sales then books it on the Rental Planner.

Off until ``Rental Settings.public_request_form`` is ticked; while off the page 404s and the
endpoint answers ``rejected``. The marketing site is WordPress on another host, so this page lives
on the ERP domain and the website links to it.

Spam controls, the same family as ``/fountain-move``: Cloudflare Turnstile (the site's existing
widget key, with its own action ``rental-request`` so a token minted for one form is refused by
the other), a honeypot field that returns a fake success, a per-IP rate limit, and a field
allowlist — the payload is never splatted onto a document. A Turnstile outage accepts the request
but says so on the Lead, so an outage costs scrutiny, not a lost customer.

The Lead goes through the same triage as a website enquiry (``lead_triage.prepare_inbound_lead`` /
``assign_inbound_lead``): an owner, and the speed-to-lead clock when that is on.
"""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, getdate, validate_email_address

TURNSTILE_ACTION = "rental-request"
TURNSTILE_SITE_KEY_FIELD = "fountain_move_turnstile_site_key"
TURNSTILE_SECRET_FIELD = "fountain_move_turnstile_secret_key"
HONEYPOT_FIELD = "company_website"
SERVICE_INTEREST = "Events"
MAX_LENGTH = 140
NOTES_MAX_LENGTH = 2000

#: payload key -> Lead field. Nothing else from the request reaches the Lead's own fields.
LEAD_FIELDS = {
	"first_name": "first_name",
	"last_name": "last_name",
	"email": "email_id",
	"phone": "mobile_no",
	"organization": "company_name",
}

#: Words that go into the comment for the salesperson, not onto fields.
DETAIL_FIELDS = (
	("event_name", _("Event")),
	("guests", _("Guests")),
	("venue", _("Venue")),
	("interest", _("Fountains of interest")),
	("notes", _("Notes")),
)


def enabled():
	try:
		return bool(cint(frappe.get_cached_doc("Rental Settings").get("public_request_form")))
	except Exception:
		return False


def turnstile_site_key():
	return frappe.get_cached_doc("ERPNext Enhancements Settings").get(TURNSTILE_SITE_KEY_FIELD) or ""


def _text(payload, key, limit=MAX_LENGTH):
	value = payload.get(key)
	return value.strip()[:limit] if isinstance(value, str) else ""


def _date(payload, key):
	value = _text(payload, key, 20)
	if not value:
		return None
	try:
		return getdate(value)
	except Exception:
		return None


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=10, seconds=3600, methods=["POST"])
def submit_request(**payload):
	"""Create an Events Lead from the public form. Answers only accepted / rejected, on purpose."""
	if not enabled():
		return {"status": "rejected"}
	if _text(payload, HONEYPOT_FIELD):
		# A bot that believes it succeeded does not adapt.
		return {"status": "accepted"}

	from erpnext_enhancements.crm_enhancements.fountain_move.intake import _verify_turnstile

	verdict, _detail = _verify_turnstile(
		_text(payload, "turnstile_token", 4096), action=TURNSTILE_ACTION, secret_field=TURNSTILE_SECRET_FIELD
	)
	if verdict in ("Failed", "Not Checked"):
		frappe.local.response["http_status_code"] = 400
		return {"status": "rejected", "reason": "captcha"}

	fields = {target: _text(payload, key) for key, target in LEAD_FIELDS.items() if _text(payload, key)}
	email = fields.get("email_id")
	if email and not validate_email_address(email):
		fields.pop("email_id")
	if not (fields.get("email_id") or fields.get("mobile_no")):
		frappe.local.response["http_status_code"] = 400
		return {"status": "rejected", "reason": "no_contact_method"}
	start, end = _date(payload, "event_date"), _date(payload, "end_date")
	if not start:
		frappe.local.response["http_status_code"] = 400
		return {"status": "rejected", "reason": "no_date"}
	end = max(end or start, start)

	fields["lead_name"] = (
		" ".join(x for x in (fields.get("first_name"), fields.get("last_name")) if x)
		or fields.get("company_name")
		or fields.get("email_id")
		or _("Rental request")
	)
	fields["status"] = "Lead"
	lead = frappe.get_doc({"doctype": "Lead", **fields})
	meta = frappe.get_meta("Lead")
	for fieldname, value in (
		("custom_service_interest", SERVICE_INTEREST),
		("custom_rental_start_date", start),
		("custom_rental_end_date", end),
		("custom_venue_zip_code", _text(payload, "zip", 20)),
	):
		if value and meta.has_field(fieldname):
			lead.set(fieldname, value)

	from erpnext_enhancements.crm_enhancements import lead_triage

	lead_triage.prepare_inbound_lead(lead)
	try:
		lead.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Rental request: Lead insert failed", message=frappe.get_traceback())
		frappe.local.response["http_status_code"] = 500
		return {"status": "rejected"}

	_record_request(lead, payload, start, end, verdict)
	lead_triage.assign_inbound_lead(lead)
	return {"status": "accepted"}


def _record_request(lead, payload, start, end, verdict):
	"""The customer's own words, for the salesperson. Logged and swallowed on failure."""
	try:
		lines = [_("Rental request from the website form, {0} to {1}.").format(
			frappe.utils.formatdate(start), frappe.utils.formatdate(end)
		)]
		for key, label in DETAIL_FIELDS:
			value = _text(payload, key, NOTES_MAX_LENGTH if key == "notes" else MAX_LENGTH)
			if value:
				lines.append(f"{label}: {frappe.utils.escape_html(value)}")
		if verdict != "Passed":
			lines.append(_("Note: the spam check could not be completed ({0}). Check this one by hand.").format(verdict))
		lead.add_comment("Comment", "<br>".join(lines))
	except Exception:
		frappe.log_error(title="Rental request: comment failed", message=frappe.get_traceback())
