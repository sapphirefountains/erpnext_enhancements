# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Sending the intake link to a customer, and attributing what comes back.

Mirrors ``stripe_payments/core/api.py::send_payment_link``: role gate first,
validate the recipient, compose, send, return ``{"sent", "via", "to"}`` for the
client toast. SMS goes through the same Triton sender, with the same mandatory
lazy import (``api.telephony`` imports twilio at module top).

**The token is attribution, not authorisation.** ``/fountain-move`` works bare —
it is meant to be printed on a card at the till and scanned. ``?ref=<token>``
only records which invite a submission came from, which is what makes "whoever
sent the link owns the resulting Opportunity" work. Consequences, all deliberate:

* :func:`resolve_invite` returns ``None`` for absent, unknown, malformed, expired
  and revoked tokens alike, and never raises. A broken invite layer must not be
  able to take the public form down.
* A token pre-fills the store location and nothing else. Invites get forwarded,
  and a forwarded link must not disclose the original recipient's details.
"""

import re

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, cint, get_datetime, get_url, now_datetime, validate_email_address

from erpnext_enhancements.crm_enhancements.fountain_move import get_contact_phone
from erpnext_enhancements.feature_flags import throw_if_fountain_move_disabled
from erpnext_enhancements.utils.phone import is_nanp, normalize_phone

#: Who may send an intake link.
INTAKE_ROLES = ("System Manager", "Sales Manager", "Sales User")

#: Who may also text it. Narrower: an SMS costs money, reaches a personal device,
#: and carries our number's reputation.
SMS_ROLES = ("System Manager", "Sales Manager")

#: Cap a single sender, on top of the endpoint rate limit.
MAX_INVITES_PER_HOUR = 40

TEMPLATE = "erpnext_enhancements/templates/emails/crm_enhancements/fountain_intake_invite.html"


@frappe.whitelist()
@rate_limit(limit=MAX_INVITES_PER_HOUR, seconds=3600, methods=["POST"])
def send_intake_link(
	recipient_email,
	recipient_name=None,
	recipient_phone=None,
	also_text=0,
	ct_location=None,
	message=None,
):
	"""Create an invite and email (optionally text) the intake link.

	``@frappe.whitelist()`` must stay OUTERMOST: it registers the function object
	by identity, so a decorator applied above it would whitelist the inner
	function and leave the exposed one unroutable.
	"""
	throw_if_fountain_move_disabled()
	frappe.only_for(INTAKE_ROLES)

	recipient_email = _valid_single_email(recipient_email)
	message = _valid_message(message)
	recipient_name = (recipient_name or "").strip()[:140]

	invite = frappe.get_doc(
		{
			"doctype": "Fountain Move Invite",
			"recipient_email": recipient_email,
			"recipient_name": recipient_name,
			"recipient_phone": (recipient_phone or "").strip()[:30],
			"ct_location": ct_location,
			"message": message,
			"sent_by": frappe.session.user,
			"sent_on": now_datetime(),
			"status": "Sent",
		}
	)
	invite.insert(ignore_permissions=True)

	url = build_intake_url(invite.token)
	_email_invite(invite, url)

	result = {"sent": True, "via": "email", "to": recipient_email, "invite": invite.name, "url": url}

	if cint(also_text):
		result["sms"] = _text_invite(invite, url)

	frappe.db.commit()
	return result


@frappe.whitelist()
def resend_intake_link(invite_name):
	"""Send the same link again, extending its life. Keeps the original token
	so a customer who kept the first email is not left with a dead link."""
	throw_if_fountain_move_disabled()
	frappe.only_for(INTAKE_ROLES)

	invite = frappe.get_doc("Fountain Move Invite", invite_name)
	if invite.status in ("Submitted", "Revoked"):
		frappe.throw(_("This invite can no longer be resent."))

	invite.db_set("resend_count", cint(invite.resend_count) + 1)
	invite.db_set("sent_on", now_datetime())
	invite.db_set("status", "Sent")
	invite.db_set("expires_on", add_days(now_datetime(), invite.default_expiry_days()))

	url = build_intake_url(invite.token)
	_email_invite(invite, url)
	frappe.db.commit()
	return {"sent": True, "via": "email", "to": invite.recipient_email}


@frappe.whitelist()
def revoke_intake_link(invite_name):
	"""Stop attributing submissions to this invite."""
	frappe.only_for(INTAKE_ROLES)
	frappe.get_doc("Fountain Move Invite", invite_name).revoke()
	frappe.db.commit()
	return {"revoked": True}


@frappe.whitelist()
def get_public_form_url():
	"""The bare, shareable URL — for the desk's "Copy Public Link" action."""
	frappe.only_for(INTAKE_ROLES)
	return {"url": build_intake_url(), "live": _public_form_live()}


def build_intake_url(token=None):
	"""``get_url`` always; never hand-build a site URL."""
	url = get_url("/fountain-move")
	if token:
		from urllib.parse import quote

		url = f"{url}?ref={quote(str(token), safe='')}"
	return url


# ---------------------------------------------------------------------------
# attribution
# ---------------------------------------------------------------------------


def resolve_invite(token):
	"""Return ``{"name", "token", "ct_location"}`` for a live invite, else None.

	Never raises, for any input. The public form calls this on its render path,
	so an exception here would mean a customer sees an error page because
	somebody forwarded a stale link.
	"""
	try:
		if not token or not re.match(r"^[A-Za-z0-9]{8,64}$", str(token)):
			return None
		invite = frappe.db.get_value(
			"Fountain Move Invite",
			{"token": token},
			["name", "token", "status", "expires_on", "ct_location"],
			as_dict=True,
		)
		if not invite or invite.status in ("Revoked", "Expired"):
			return None
		if invite.expires_on and get_datetime(invite.expires_on) < now_datetime():
			return None
		return {
			"name": invite.name,
			"token": invite.token,
			"ct_location": invite.ct_location,
		}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Fountain Move: invite lookup", defer_insert=True)
		return None


def mark_invite_opened(invite_name):
	"""Background job: stamp the first open.

	Runs as a job rather than inline in ``get_context`` because a GET does not
	commit — frappe only commits for unsafe HTTP methods, so an inline write
	would be rolled back and the invite would sit at "Sent" forever.
	"""
	try:
		status, opened = frappe.db.get_value(
			"Fountain Move Invite", invite_name, ["status", "opened_on"]
		)
		if opened or status in ("Submitted", "Revoked"):
			return
		frappe.db.set_value(
			"Fountain Move Invite",
			invite_name,
			{"status": "Opened", "opened_on": now_datetime()},
			update_modified=False,
		)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Fountain Move: invite open", defer_insert=True)


def mark_invite_submitted(invite_name, request_name):
	try:
		frappe.db.set_value(
			"Fountain Move Invite",
			invite_name,
			{
				"status": "Submitted",
				"submitted_on": now_datetime(),
				"fountain_move_request": request_name,
			},
			update_modified=False,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Fountain Move: invite submit", defer_insert=True)


def expire_stale_invites():
	"""Daily: flip past-expiry invites so the list view tells the truth."""
	stale = frappe.get_all(
		"Fountain Move Invite",
		filters={
			"status": ["in", ("Sent", "Opened")],
			"expires_on": ["<", now_datetime()],
		},
		pluck="name",
		limit=500,
	)
	for name in stale:
		frappe.db.set_value("Fountain Move Invite", name, "status", "Expired", update_modified=False)
	if stale:
		frappe.db.commit()
	return len(stale)


# ---------------------------------------------------------------------------
# delivery
# ---------------------------------------------------------------------------


def _email_invite(invite, url):
	sender_name = frappe.db.get_value("User", invite.sent_by, "full_name") or "Sapphire Fountains"
	message = frappe.render_template(
		TEMPLATE,
		{
			"invite": invite,
			"url": url,
			"sender_name": sender_name,
			"note": invite.message,
			"recipient_name": invite.recipient_name,
			"contact_phone": get_contact_phone(),
		},
	)
	frappe.sendmail(
		recipients=[invite.recipient_email],
		subject="Your fountain installation — Sapphire Fountains",
		message=message,
		reference_doctype="Fountain Move Invite",
		reference_name=invite.name,
	)


def _text_invite(invite, url):
	"""Text the link. Returns a short status string; never raises.

	No ``send_system_sms`` fallback on purpose. That function skips the Employee
	check and the Communication log that ``send_sms`` performs, so falling back to
	it would quietly bypass both controls — and, because the gateway POST happens
	before the log is written, a retry can send the customer two texts.
	"""
	if not frappe.has_permission(ptype="write", doctype="Fountain Move Invite") or not any(
		role in frappe.get_roles() for role in SMS_ROLES
	):
		return "not_permitted"

	number = normalize_phone(invite.recipient_phone)
	if not is_nanp(number):
		return "no_number"

	if not frappe.db.exists("Employee", {"user_id": frappe.session.user, "status": "Active"}):
		# send_sms resolves the sender's Employee for the signature and the
		# Communication record; without one it throws mid-send.
		return "no_employee"

	try:
		# Lazy: api.telephony imports twilio at module top.
		from erpnext_enhancements.api.telephony import send_sms

		send_sms(
			target_number=invite.recipient_phone,
			message=f"Sapphire Fountains — tell us about your fountain installation: {url}",
			reference_doctype="Fountain Move Invite",
			# NOTE: reference_docname, not reference_name — send_sms differs from
			# frappe.sendmail here (api/telephony.py:1352).
			reference_docname=invite.name,
		)
		invite.db_set("sms_sent", 1, update_modified=False)
		return "sent"
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Fountain Move: invite SMS", defer_insert=True)
		# The email already went out, so the customer has the link. Report the
		# failure rather than retrying — a retry here is how people get texted twice.
		return "failed"


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _valid_single_email(value):
	"""Exactly one address.

	``validate_email_address`` is an extractor, not a predicate: it comma-splits
	and returns the valid parts joined, so "a@b.com, evil@x.com" would sail
	through a truthiness check and become two recipients.
	"""
	email = (value or "").strip()
	if not email or re.search(r"[,;\s]", email):
		frappe.throw(_("Enter a single email address."))
	if validate_email_address(email) != email:
		frappe.throw(_("Enter a valid email address."))
	return email.lower()


def _valid_message(value):
	"""Plain text only — the template escapes it, and this keeps it simple."""
	message = (value or "").strip()[:500]
	if "<" in message:
		frappe.throw(_("The personal note cannot contain HTML."))
	return message


def _public_form_live():
	from erpnext_enhancements.feature_flags import fountain_move_public_form_enabled

	return bool(fountain_move_public_form_enabled())
