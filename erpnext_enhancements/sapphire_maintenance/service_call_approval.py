# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The client's approval of a Service Call, by emailed or texted link (v1.778.0).

Nikolas, 2026-11-03: a one-off client approves on a link that shows a plain-language summary,
the service type, the rate and short terms, with one **Approve** button and a typed name; the
name, the time, the IP address and the browser are recorded. No drawn signature or initials.
Approve moves the Service Call to Approved and its Opportunity to **Closed Won**; Decline marks
the Opportunity Lost with the reason, and an expired link does the same.

Built the way ``sign_off.py`` builds the visit sign-off, and what carries over unchanged:

* **The token is a bearer credential, so it is 256 bits** (``secrets.token_urlsafe``) **and only
  its SHA-256 is stored** on the **Service Call Approval Request**. It is in the email or text
  and nowhere else; nothing here logs it. A malformed token is refused by shape before any
  database read, so an unknown and a malformed one are indistinguishable from outside.
* **One live link per call.** A resend mints a new token on a new request and retires the old
  one (Superseded); its hash is kept, so the old link can say "we sent a newer one". Links expire
  after :data:`LINK_DAYS` days and answer once.
* **Uniform failure copy** (:data:`UNAVAILABLE`) for unknown, expired, superseded and canceled.
* **Never a request parameter named ``sid``** (``Session.__init__`` pops it); the link's is ``ref``.
* **Never ``doc.update(payload)`` from a guest**: every field is assigned by name.
* Guest endpoints are POST-only, rate limited, and take the token and nothing that names a record.

**Sending.** Email always, through the app's email shell. A text goes through the Triton gateway
(``api/telephony.send_sms``) when the sender asks for one and the gateway is configured; it is
the app's only SMS path, and a text that fails never undoes the email.
"""

import hashlib
import re
import secrets

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_to_date, cint, escape_html, get_datetime, get_url, now_datetime

from erpnext_enhancements.sapphire_maintenance import service_calls

CALL = service_calls.DOCTYPE
REQUEST = "Service Call Approval Request"
#: The public page: ``www/service-call-approval.html`` with controller
#: ``www/service_call_approval.py`` (underscores, or frappe never imports it).
ROUTE = "/service-call-approval"
TEMPLATE = "erpnext_enhancements/templates/emails/sapphire_maintenance/service_call_approval.html"

LINK_DAYS = 14
LIVE_STATUSES = ("Sent", "Viewed")
TOKEN_BYTES = 32
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
NAME_MAX = 140
REASON_MAX = 500
_CONTROL_RANGES = ((0x00, 0x1F), (0x7F, 0x9F), (0x202A, 0x202E), (0x2066, 0x2069))
CONTROL_CHARS_RE = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _CONTROL_RANGES) + "]")
ANGLE_BRACKETS_RE = re.compile(r"[<>]")
EXPIRED_REASON = "The approval link expired without an answer."
SMS_MAX = 320

UNAVAILABLE = (
	"This approval link isn't available any more. It may have expired or been replaced by a newer "
	"one, so please check for our most recent message."
)


# ---------------------------------------------------------------------------- tokens


def mint_token():
	token = secrets.token_urlsafe(TOKEN_BYTES)
	return token, hash_token(token)


def hash_token(token):
	if not token or not TOKEN_RE.match(str(token)):
		return None
	return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def approval_url(token):
	return get_url(f"{ROUTE}?ref={token}")


def resolve(token):
	"""The request a link's token names, or None. Never raises: the page calls it on every render."""
	try:
		token_hash = hash_token(token)
		if not token_hash:
			return None
		name = frappe.db.get_value(REQUEST, {"token_hash": token_hash}, "name")
		return frappe.get_doc(REQUEST, name) if name else None
	except Exception:
		frappe.log_error(title="Service call approval: resolve failed", message=frappe.get_traceback())
		return None


# ---------------------------------------------------------------------------- state


def is_expired(request, now=None):
	expires = request.get("expires_on")
	return bool(expires) and get_datetime(expires) <= (now or now_datetime())


def effective_status(request, now=None):
	status = request.get("status") or ""
	if status in LIVE_STATUSES and is_expired(request, now):
		return "Expired"
	return status


def is_live(request, now=None):
	return bool(request) and request.get("status") in LIVE_STATUSES and not is_expired(request, now)


def latest_request(service_call):
	rows = frappe.get_all(
		REQUEST,
		filters={"service_call": service_call},
		fields=["name", "status", "channel", "email", "phone", "sent_on", "sent_by", "expires_on", "first_viewed_on", "approved_on", "approved_name", "declined_on", "decline_reason"],
		order_by="creation desc",
		limit=1,
	)
	return rows[0] if rows else None


def public_status(request):
	"""What the form shows about a request: never its token or hash."""
	return {
		"request": request.get("name"),
		"status": effective_status(request),
		"channel": request.get("channel"),
		"email": request.get("email"),
		"phone": request.get("phone"),
		"sent_on": str(request.get("sent_on") or "") or None,
		"expires_on": str(request.get("expires_on") or "") or None,
		"first_viewed_on": str(request.get("first_viewed_on") or "") or None,
		"approved_on": str(request.get("approved_on") or "") or None,
		"approved_name": request.get("approved_name"),
		"declined_on": str(request.get("declined_on") or "") or None,
		"decline_reason": request.get("decline_reason"),
	}


def describe(call, request=None, fmt=None):
	"""The approval in a few words, for the form."""
	fmt = fmt or _format_datetime
	if call.get("approved_on"):
		return _("Approved by {0} on {1}").format(call.get("approved_name") or "", fmt(call.get("approved_on")))
	if call.get("declined_on"):
		return _("Declined on {0}: {1}").format(fmt(call.get("declined_on")), call.get("decline_reason") or "")
	if request:
		status = effective_status(request)
		to = request.get("email") or request.get("phone") or ""
		sent = fmt(request.get("sent_on")) if request.get("sent_on") else ""
		if status == "Viewed":
			return _("Sent to {0} on {1}; opened, not answered yet").format(to, sent)
		if status == "Sent":
			return _("Sent to {0} on {1}, not answered yet").format(to, sent)
		if status == "Expired":
			return _("Sent to {0} on {1}; the link expired unanswered").format(to, sent)
	return _("Not sent")


def _format_datetime(value):
	from frappe.utils import format_datetime

	return format_datetime(value)


# ---------------------------------------------------------------------------- sending


def sms_available():
	"""Whether the Triton gateway is configured, so a text can go. Never raises."""
	try:
		if not frappe.db.exists("DocType", "Triton Settings"):
			return False
		return bool(frappe.db.get_single_value("Triton Settings", "gateway_url"))
	except Exception:
		return False


def clean_email(value):
	from frappe.utils import validate_email_address

	email = str(value or "").strip()
	if not email or re.search(r"[,;\s<>]", email):
		return ""
	if validate_email_address(email) != email:
		return ""
	return email.lower()


def clean_phone(value):
	digits = re.sub(r"\D", "", str(value or ""))
	if len(digits) < 10:
		return ""
	return str(value or "").strip()[:30]


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=3600, methods=["POST"])
def send_approval_request(service_call, email=None, phone=None, via_sms=0):
	"""Send (or re-send) the client the approval link, from the Service Call form.

	Needs write on the call, and a call that may be sent (:data:`service_calls.SENDABLE`). A
	resend supersedes the link before it; a resend after a decline puts the Opportunity back at
	its opening status, since the client is being asked again.
	"""
	call = frappe.get_doc(CALL, service_call)
	call.check_permission("write")
	if call.status not in service_calls.SENDABLE:
		frappe.throw(_("A {0} service call cannot be sent for approval.").format(_(call.status)), title=_("Cannot send"))
	_require_rate(call)
	recipient = clean_email(email if email is not None else call.get("contact_email"))
	number = clean_phone(phone if phone is not None else call.get("contact_phone")) if cint(via_sms) else ""
	if not recipient:
		frappe.throw(_("Enter one valid email address for the client."), title=_("Email needed"))
	if cint(via_sms) and not number:
		frappe.throw(_("Enter a phone number with at least ten digits, or untick Text."), title=_("Phone needed"))
	if cint(via_sms) and not sms_available():
		frappe.throw(_("Texting is not set up on this site (Triton Settings has no gateway). The email can still go."), title=_("No SMS"))

	now = now_datetime()
	retire_live(call.name)
	token, token_hash = mint_token()
	request = frappe.new_doc(REQUEST)
	request.service_call = call.name
	request.status = "Sent"
	request.channel = "Email and SMS" if number else "Email"
	request.email = recipient
	request.phone = number or None
	request.customer = call.get("customer")
	request.sent_by = frappe.session.user
	request.sent_on = now
	request.expires_on = add_to_date(now, days=LINK_DAYS)
	request.token_hash = token_hash
	request.insert(ignore_permissions=True)

	url = approval_url(token)
	_email_request(call, request, url)
	sms_error = None
	if number:
		sms_error = _text_request(call, request, url)

	was_declined = call.status == "Declined"
	call.db_set(
		{
			"status": "Sent for Approval",
			"approval_email": recipient,
			"approval_phone": number or None,
			"approval_sent_on": now,
			"expires_on": request.expires_on,
			"declined_on": None,
			"decline_reason": None,
		}
	)
	call.add_comment(
		"Comment",
		_("Approval link sent to {0}{1} (expires {2}).").format(
			escape_html(recipient), (" " + _("and texted to {0}").format(escape_html(number))) if number and not sms_error else "", _format_datetime(request.expires_on)
		),
	)
	if was_declined:
		service_calls.set_opportunity_status(call.get("opportunity"), service_calls.opening_status())
	result = public_status(request)
	if sms_error:
		result["sms_error"] = sms_error
	return result


def _require_rate(call):
	"""A link quotes a rate, so a call with none cannot be sent. A rate entered in the table after
	the call was made is copied now (the call was created before its row existed); a call that
	still has none is refused with the row to fill in."""
	from frappe.utils import flt

	field = service_calls.rate_field(call.get("service_type"))
	if flt(call.get(field)) > 0:
		return
	service_calls.copy_rates(call)
	if flt(call.get(field)) > 0:
		call.db_set(field, call.get(field))
		return
	frappe.throw(
		_(
			"This call has no rate to quote. Enter the {0} ({1}) rate in Service Call Rate, then send again."
		).format(call.get("service_type"), call.get("rate_kind") or service_calls.default_rate_kind(call.get("service_type"))),
		title=_("No rate on file"),
	)


def retire_live(service_call, keep=None):
	for name in frappe.get_all(REQUEST, filters={"service_call": service_call, "status": ["in", LIVE_STATUSES]}, pluck="name"):
		if name != keep:
			frappe.db.set_value(REQUEST, name, "status", "Superseded", update_modified=False)


def _email_request(call, request, url):
	from erpnext_enhancements import email_style

	site = service_calls.site_label(call)
	label = service_calls.service_label(call.get("service_type"))
	company = frappe.defaults.get_global_default("company") or "Sapphire Fountains"
	frappe.sendmail(
		recipients=[request.email],
		subject=_("Please approve your {0} — {1}").format(label.lower(), site),
		message=email_style.render(
			TEMPLATE,
			{
				"site": site,
				"service": label,
				"rate": service_calls.describe_rate(call, _money),
				"scheduled": _format_date(call.get("scheduled_date")) if call.get("scheduled_date") else "",
				"approval_url": url,
				"expires_on": _format_datetime(request.expires_on),
				"company": company,
			},
			title=_("Please approve your service call"),
			eyebrow=site,
			preheader=_("One tap to approve: the summary, the rate and our terms are on the page."),
			tagline=True,
			pillar="service",
		),
		reference_doctype=CALL,
		reference_name=call.name,
		reply_to=_office_email(company),
	)


def sms_text(site, service, url, expires):
	"""The text message, kept short: carriers split past 160 characters and the link must survive."""
	text = f"Sapphire Fountains: please approve your {service.lower()} for {site} here: {url} (link expires {expires})."
	return text[:SMS_MAX]


def _text_request(call, request, url):
	"""Send the text in a savepoint; a failure is logged and reported, never raised, and never
	undoes the email or the request."""
	from erpnext_enhancements.api import telephony

	frappe.db.savepoint("service_call_sms")
	try:
		telephony.send_sms(
			request.phone,
			sms_text(service_calls.site_label(call), service_calls.service_label(call.get("service_type")), url, _format_date(request.expires_on)),
			reference_doctype=CALL,
			reference_docname=call.name,
		)
		return None
	except Exception:
		frappe.db.rollback(save_point="service_call_sms")
		frappe.log_error(title="Service call approval: SMS failed", message=frappe.get_traceback())
		return _("The email went, but the text could not be sent. Check the Error Log.")


def _office_email(company):
	try:
		return frappe.db.get_value("Company", company, "email") or None
	except Exception:
		return None


def _money(value):
	from frappe.utils import fmt_money

	try:
		currency = frappe.get_cached_value("Company", frappe.defaults.get_global_default("company"), "default_currency")
	except Exception:
		currency = None
	return fmt_money(value, currency=currency or "USD")


def _format_date(value):
	from frappe.utils import formatdate

	return formatdate(value)


# ---------------------------------------------------------------------------- desk


@frappe.whitelist()
def get_approval_status(service_call):
	"""The form's approval line: the latest link, what to prefill, whether a text can go."""
	call = frappe.get_doc(CALL, service_call)
	call.check_permission("read")
	request = latest_request(call.name)
	return {
		"request": public_status(request) if request else None,
		"summary": describe(call, request),
		"default_email": call.get("approval_email") or call.get("contact_email") or _customer_email(call.get("customer")),
		"default_phone": call.get("approval_phone") or call.get("contact_phone") or "",
		"sms_available": sms_available(),
		"can_send": call.status in service_calls.SENDABLE and frappe.has_permission(CALL, "write", doc=call),
	}


def _customer_email(customer):
	if not customer:
		return ""
	try:
		email = clean_email(frappe.db.get_value("Customer", customer, "email_id"))
		if email:
			return email
		contact = frappe.db.get_value("Customer", customer, "customer_primary_contact")
		if contact:
			return clean_email(frappe.db.get_value("Contact", contact, "email_id"))
	except Exception:
		pass
	return ""


# ---------------------------------------------------------------------------- the page


def hourly_rates(fmt=None):
	"""The three Small Repair hourly rates on the site, for the page's terms: ``[(kind, text)]``.
	Only kinds with a rate filled in. Never raises."""
	fmt = fmt or _money
	rows = []
	try:
		for kind in service_calls.HOURLY_KINDS:
			rate = service_calls.rate_for(service_calls.SMALL_REPAIR, kind)
			if rate:
				rows.append((kind, f"{fmt(rate)} per hour"))
	except Exception:
		return []
	return rows


def page_summary(call):
	"""The approval page's facts as plain values; the template escapes every one."""
	from frappe.utils import sanitize_html

	service_type = call.get("service_type")
	return {
		"number": call.get("name"),
		"site": service_calls.site_label(call),
		"account": _customer_name(call.get("customer")),
		"address": call.get("address_display") or "",
		"service": service_calls.service_label(service_type),
		"rate": service_calls.describe_rate(call, _money),
		"hourly": service_calls.is_hourly(service_type),
		"hourly_rates": hourly_rates() if service_calls.is_hourly(service_type) else [],
		"scheduled": _format_date(call.get("scheduled_date")) if call.get("scheduled_date") else "",
		"scope_html": sanitize_html(call.get("scope") or ""),
		"terms": service_calls.terms_for(service_type),
	}


def _customer_name(customer):
	if not customer:
		return ""
	try:
		return frappe.db.get_value("Customer", customer, "customer_name") or customer
	except Exception:
		return customer


def mark_viewed(request_name):
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
		frappe.log_error(title="Service call approval: view tracking failed", message=frappe.get_traceback())


# ---------------------------------------------------------------------------- guest endpoints


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=3600, methods=["POST"])
def approve_service_call(ref=None, approved_name=None):
	"""Approve. The token is the only authority; the call comes from it. Approving twice answers
	``already`` rather than an error."""
	request = resolve(ref)
	if request is None:
		return {"ok": False, "error": _(UNAVAILABLE)}
	if request.status == "Approved":
		return {"ok": True, "already": True}
	if not is_live(request):
		return {"ok": False, "error": _(UNAVAILABLE)}
	name = clean_text(approved_name)
	if not name:
		return {"ok": False, "error": _("Please type your full name.")}

	status = frappe.db.get_value(REQUEST, request.name, "status", for_update=True)
	if status == "Approved":
		return {"ok": True, "already": True}
	if status not in LIVE_STATUSES:
		return {"ok": False, "error": _(UNAVAILABLE)}
	call = frappe.db.get_value(CALL, request.service_call, ["status"], as_dict=True, for_update=True)
	if not call or call.status not in ("Sent for Approval", "Requested"):
		return {"ok": False, "error": _(UNAVAILABLE)}

	record_approval(request, name)
	return {"ok": True}


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=20, seconds=3600, methods=["POST"])
def decline_service_call(ref=None, reason=None):
	"""Decline, with a reason in the client's words."""
	request = resolve(ref)
	if request is None:
		return {"ok": False, "error": _(UNAVAILABLE)}
	if request.status == "Declined":
		return {"ok": True, "already": True}
	if not is_live(request):
		return {"ok": False, "error": _(UNAVAILABLE)}
	text = clean_text(reason, REASON_MAX)
	status = frappe.db.get_value(REQUEST, request.name, "status", for_update=True)
	if status == "Declined":
		return {"ok": True, "already": True}
	if status not in LIVE_STATUSES:
		return {"ok": False, "error": _(UNAVAILABLE)}
	call = frappe.db.get_value(CALL, request.service_call, ["status"], as_dict=True, for_update=True)
	if not call or call.status not in ("Sent for Approval", "Requested"):
		return {"ok": False, "error": _(UNAVAILABLE)}
	record_decline(request, text or _("No reason given"))
	return {"ok": True}


def record_approval(request, approved_name):
	"""Stamp the evidence on the request, then on the call; the Opportunity goes to Closed Won.

	Explicit assignment, field by field. The call gets ``db_set``: its status is read-only on the
	form, and nothing a client types may run the call's validation.
	"""
	when = now_datetime()
	request.status = "Approved"
	request.approved_name = approved_name
	request.approved_on = when
	request.approver_ip = str(getattr(frappe.local, "request_ip", None) or "")[:140]
	request.user_agent = str(frappe.get_request_header("User-Agent") or "")[:512]
	request.save(ignore_permissions=True)

	call = frappe.get_doc(CALL, request.service_call)
	call.db_set(
		{
			"status": "Approved",
			"approved_name": approved_name,
			"approved_on": when,
			"approver_ip": request.approver_ip,
			"approver_user_agent": request.user_agent,
			"declined_on": None,
			"decline_reason": None,
		}
	)
	call.add_comment("Comment", _("Approved on the link by {0}.").format(escape_html(approved_name)))
	retire_live(call.name, keep=request.name)
	service_calls.set_opportunity_status(call.get("opportunity"), "Closed Won")
	_draft_visit_after_approval(call)


def _draft_visit_after_approval(call):
	"""A call with a date on it gets its visit drafted at once, so it is on the Planner. In a
	savepoint: a visit that cannot be drafted never undoes the approval."""
	if not call.get("scheduled_date"):
		return
	frappe.db.savepoint("service_call_visit")
	try:
		call.reload()
		service_calls.draft_visit(call, call.scheduled_date, call.get("technician"))
	except Exception:
		frappe.db.rollback(save_point="service_call_visit")
		frappe.log_error(title="Service call approval: visit not drafted", message=frappe.get_traceback())


def record_decline(request, reason):
	when = now_datetime()
	request.status = "Declined"
	request.declined_on = when
	request.decline_reason = reason
	request.approver_ip = str(getattr(frappe.local, "request_ip", None) or "")[:140]
	request.user_agent = str(frappe.get_request_header("User-Agent") or "")[:512]
	request.save(ignore_permissions=True)

	call = frappe.get_doc(CALL, request.service_call)
	call.db_set({"status": "Declined", "declined_on": when, "decline_reason": reason})
	call.add_comment("Comment", _("Declined on the link: {0}").format(escape_html(reason)))
	retire_live(call.name, keep=request.name)
	service_calls.set_opportunity_status(
		call.get("opportunity"), "Lost", reason, lost_reason=service_calls.LOST_DECLINED, service_call=call.name
	)


def expire_stale():
	"""Daily: a live link past its expiry is Expired, its call Declined with the reason, and the
	Opportunity Lost (decision 2026-11-03: "expired marks it Lost")."""
	for name in frappe.get_all(REQUEST, filters={"status": ["in", LIVE_STATUSES], "expires_on": ["<", now_datetime()]}, pluck="name"):
		try:
			frappe.db.set_value(REQUEST, name, "status", "Expired", update_modified=False)
			service_call = frappe.db.get_value(REQUEST, name, "service_call")
			if not service_call:
				continue
			row = frappe.db.get_value(CALL, service_call, ["status", "opportunity"], as_dict=True)
			if row and row.status == "Sent for Approval":
				frappe.db.set_value(CALL, service_call, {"status": "Declined", "declined_on": now_datetime(), "decline_reason": EXPIRED_REASON})
				service_calls.set_opportunity_status(
					row.opportunity, "Lost", EXPIRED_REASON, lost_reason=service_calls.LOST_EXPIRED, service_call=service_call
				)
		except Exception:
			frappe.log_error(title="Service call approval: expiry failed", message=frappe.get_traceback())


def clean_text(value, limit=NAME_MAX):
	"""A guest's typed text, safe to store: no control or bidi characters, no ``<`` or ``>``."""
	text = CONTROL_CHARS_RE.sub("", str(value or ""))
	return ANGLE_BRACKETS_RE.sub("", text).strip()[:limit]
