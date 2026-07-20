# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Alerts for the fountain-move intake pipeline.

Three things are worth telling someone about:

* a **new request converted** — there is a real lead to work;
* a **conversion failed** — a real customer is sitting in limbo and nobody would
  otherwise notice, because the visible symptom is an absence;
* a **duplicate review** — we deliberately created nothing and need a human.

Delivery is belt-and-braces: a desk assignment (which survives a bounced email and
shows up in the owner's inbox) plus an email. Every send is wrapped so a mail
failure can never roll back or mask the conversion itself.

**We never email the customer about an internal failure.** They submitted a form
and got a confirmation; our plumbing is not their problem.
"""

import frappe
from frappe.utils import add_to_date, cint, get_url_to_form

from erpnext_enhancements.crm_enhancements.fountain_move import MAX_CONVERSION_ATTEMPTS

TEMPLATE_DIR = "erpnext_enhancements/templates/emails/crm_enhancements"


def notify_converted(req, owner):
	"""Tell the owner there is a new lead."""
	url = get_url_to_form("Opportunity", req.created_opportunity)
	subject = f"New fountain move request — {req.first_name} {req.last_name} ({req.city})"
	_assign(req, owner, f"New fountain move request from {req.first_name} {req.last_name}")
	_send(
		recipients=_recipients(owner),
		subject=subject,
		template="fountain_move_new.html",
		context={
			"req": req,
			"opportunity_url": url,
			"request_url": get_url_to_form("Fountain Move Request", req.name),
		},
		req=req,
	)


def notify_conversion_failure(req):
	"""Tell someone a submission could not be converted."""
	attempts = cint(req.conversion_attempts)
	if attempts != 1 and attempts < MAX_CONVERSION_ATTEMPTS:
		return  # already shouted on attempt 1; shout again only when giving up

	owner = _fallback_owner()
	final = attempts >= MAX_CONVERSION_ATTEMPTS
	subject = (
		f"Fountain move request {req.name} could not be converted"
		f"{' (giving up)' if final else ''}"
	)
	_assign(req, owner, f"Fountain move request {req.name} failed to convert")
	_send(
		recipients=_recipients(owner),
		subject=subject,
		template="fountain_move_failed.html",
		context={
			"req": req,
			"final": final,
			"attempts": attempts,
			"max_attempts": MAX_CONVERSION_ATTEMPTS,
			"error_summary": _summarise(req.error),
			"request_url": get_url_to_form("Fountain Move Request", req.name),
		},
		req=req,
	)


def notify_duplicate_review(req, match):
	"""Tell someone we stopped rather than guess which account this belongs to."""
	owner = _fallback_owner()
	_assign(req, owner, f"Fountain move request {req.name} needs a duplicate decision")
	_send(
		recipients=_recipients(owner),
		subject=f"Fountain move request {req.name} needs a duplicate decision",
		template="fountain_move_duplicate.html",
		context={
			"req": req,
			"reason": getattr(match, "reason", "") or "",
			"candidates": getattr(match, "candidates", []) or [],
			"request_url": get_url_to_form("Fountain Move Request", req.name),
		},
		req=req,
	)


# ---------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------


def _recipients(owner):
	"""Owner plus the configured extra addresses, deduped."""
	addresses = []
	if owner:
		email = frappe.db.get_value("User", owner, "email")
		if email:
			addresses.append(email)

	try:
		settings = frappe.get_cached_doc("ERPNext Enhancements Settings")
		for row in settings.get("fountain_move_notify_emails") or []:
			if row.get("email"):
				addresses.append(row.email)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Fountain Move: recipient lookup", defer_insert=True)

	seen = set()
	return [a for a in addresses if a and not (a.lower() in seen or seen.add(a.lower()))]


def _fallback_owner():
	try:
		return frappe.db.get_single_value("ERPNext Enhancements Settings", "fmr_default_owner")
	except Exception:
		return None


def _send(recipients, subject, template, context, req):
	"""One email. Failures are logged and swallowed — never roll back a conversion."""
	if not recipients:
		return False
	try:
		message = frappe.render_template(f"{TEMPLATE_DIR}/{template}", context)
		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=message,
			reference_doctype="Fountain Move Request",
			reference_name=req.name,
		)
		return True
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Fountain Move: notification failed", defer_insert=True)
		return False


def _assign(req, owner, description):
	"""Add a ToDo so the work lands in someone's inbox even if mail is broken."""
	if not owner:
		return
	try:
		from frappe.desk.form.assign_to import add

		add(
			{
				"assign_to": [owner],
				"doctype": "Fountain Move Request",
				"name": req.name,
				"description": description,
			}
		)
	except Exception:
		# Already assigned, or the assignment API objected. Neither is worth
		# failing a conversion over.
		frappe.log_error(frappe.get_traceback(), "Fountain Move: assignment failed", defer_insert=True)


def _summarise(traceback):
	"""Last meaningful line of a traceback — the bit that says what went wrong."""
	if not traceback:
		return "No error detail was recorded."
	lines = [line.strip() for line in str(traceback).strip().splitlines() if line.strip()]
	return lines[-1][:500] if lines else "No error detail was recorded."


def digest_stuck_requests():
	"""Daily backstop: one email listing everything that needs a human.

	Individual alerts can be missed, filtered or sent while someone is on leave.
	This is the safety net that stops a real customer's request rotting silently
	in a status nobody watches.
	"""
	buckets = {
		"Failed": _stuck("Failed"),
		"Duplicate Review": _stuck("Duplicate Review"),
		"Converting (stalled over an hour)": _stalled_converting(),
		"New (unconverted over a day)": _stale_new(),
	}
	if not any(buckets.values()):
		return

	owner = _fallback_owner()
	recipients = _recipients(owner)
	if not recipients:
		return

	try:
		message = frappe.render_template(
			f"{TEMPLATE_DIR}/fountain_move_digest.html",
			{"buckets": buckets, "base_url": get_url_to_form("Fountain Move Request", "")},
		)
		frappe.sendmail(
			recipients=recipients,
			subject="Fountain move requests needing attention",
			message=message,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Fountain Move: digest failed", defer_insert=True)


def _stuck(status):
	return frappe.get_all(
		"Fountain Move Request",
		filters={"status": status},
		fields=["name", "first_name", "last_name", "city", "email", "modified"],
		order_by="modified asc",
		limit=50,
	)


def _stalled_converting():
	"""Rows claimed by a worker that never finished — a killed job, usually."""
	return frappe.get_all(
		"Fountain Move Request",
		filters={"status": "Converting", "modified": ["<", add_to_date(None, hours=-1)]},
		fields=["name", "first_name", "last_name", "city", "email", "modified"],
		order_by="modified asc",
		limit=50,
	)


def _stale_new():
	"""Never enqueued, or enqueued and lost. Includes Turnstile-unavailable rows."""
	return frappe.get_all(
		"Fountain Move Request",
		filters={"status": "New", "creation": ["<", add_to_date(None, days=-1)]},
		fields=["name", "first_name", "last_name", "city", "email", "modified"],
		order_by="creation asc",
		limit=50,
	)
