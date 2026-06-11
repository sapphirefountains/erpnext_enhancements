# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Travel emails (code-driven, ``frappe.sendmail``) — booked / traveler added
/ expense claims generated / trip closed, plus the itinerary email behind the
"Send Itinerary" button and the scheduler reminders.

Architecture follows ``status_alerts.py``: a doc_events dispatcher
(:func:`on_trip_update`, hooks.py) guards transitions via
``get_doc_before_save()``, delivery runs in background jobs
(``enqueue_after_commit``) so a slow SMTP can never block or roll back a
save, per-recipient failures are logged and skipped, and every emailed user
also gets a Notification Log entry for the in-app audit trail.

Why not Notification fixtures: every travel event needs the *computed*
traveler recipient list (and per-traveler ICS attachments) — the Notification
doctype only does role/field recipients.

The whole surface is gated by **Travel Settings → Send Travel Notifications**
(off by default so the module can ship dormant); the explicit "Send
Itinerary" button bypasses the gate via ``force=True``.
"""

import frappe
from frappe import _
from frappe.utils import cint, get_url, get_url_to_form

from erpnext_enhancements.travel_management.ics import trip_ics_attachment

TEMPLATE_DIR = "erpnext_enhancements/templates/emails/travel"


def _notifications_enabled():
	return bool(cint(frappe.db.get_single_value("Travel Settings", "notifications_enabled")))


def _in_maintenance_context():
	"""True while migrating/installing/patching/importing — never email then."""
	flags = frappe.flags
	return bool(flags.in_migrate or flags.in_install or flags.in_patch or flags.in_import)


def _traveler_recipients(doc, employees=None):
	"""Resolve traveler rows to ``{row, employee, employee_name, email, user_id}``
	dicts (rows without any email address are skipped by delivery)."""
	recipients = []
	for row in doc.travelers:
		if employees is not None and row.employee not in employees:
			continue
		info = frappe.db.get_value(
			"Employee",
			row.employee,
			["employee_name", "user_id", "prefered_email", "company_email", "personal_email"],
			as_dict=True,
		)
		if not info:
			continue
		email = info.prefered_email or info.user_id or info.company_email or info.personal_email
		recipients.append(
			frappe._dict(
				row=row,
				employee=row.employee,
				employee_name=info.employee_name or row.employee,
				email=email,
				user_id=info.user_id,
			)
		)
	return recipients


def _base_context(doc):
	return {
		"trip": doc,
		"trip_url": get_url_to_form("Travel Trip", doc.name),
		"itinerary_url": get_url("/itinerary"),
		"guidelines_url": get_url("/travel_guidelines"),
	}


def _send(recipient, subject, template, context, doc, attachments=None):
	"""One email + Notification Log, failure logged and swallowed."""
	if not recipient.email:
		return False
	try:
		message = frappe.render_template(
			f"{TEMPLATE_DIR}/{template}", dict(context, recipient=recipient)
		)
		frappe.sendmail(
			recipients=[recipient.email],
			subject=subject,
			message=message,
			reference_doctype="Travel Trip",
			reference_name=doc.name,
			attachments=attachments or [],
		)
		if recipient.user_id:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": subject,
					"email_content": message,
					"for_user": recipient.user_id,
					"type": "Alert",
					"document_type": "Travel Trip",
					"document_name": doc.name,
				}
			).insert(ignore_permissions=True)
		return True
	except Exception:
		frappe.log_error(
			title="Travel notification failed",
			message=f"{doc.name} -> {recipient.email} ({template})\n{frappe.get_traceback()}",
		)
		return False


# ------------------------------------------------------------- dispatcher


def on_trip_update(doc, method=None):
	"""Travel Trip ``on_update`` (hooks.py): queue transition emails."""
	if _in_maintenance_context() or not _notifications_enabled():
		return

	before = doc.get_doc_before_save()

	if doc.status == "Booked" and (not before or before.status != "Booked"):
		frappe.enqueue(
			"erpnext_enhancements.travel_management.notifications.deliver_trip_booked",
			trip=doc.name,
			queue="short",
			enqueue_after_commit=True,
		)

	if before and doc.status != "Planning":
		old = {t.employee for t in before.travelers}
		added = [t.employee for t in doc.travelers if t.employee not in old]
		if added:
			frappe.enqueue(
				"erpnext_enhancements.travel_management.notifications.deliver_traveler_added",
				trip=doc.name,
				employees=added,
				queue="short",
				enqueue_after_commit=True,
			)

	if doc.status == "Closed" and (not before or before.status != "Closed"):
		frappe.enqueue(
			"erpnext_enhancements.travel_management.notifications.deliver_trip_closed",
			trip=doc.name,
			queue="short",
			enqueue_after_commit=True,
		)


# -------------------------------------------------------- background jobs


def deliver_trip_booked(trip):
	"""All travelers + the owner get the booked notice; travelers get their
	personal ICS calendar attached."""
	doc = frappe.get_doc("Travel Trip", trip)
	context = _base_context(doc)
	subject = _("Trip booked: {0} ({1} – {2})").format(doc.purpose, doc.start_date, doc.end_date)

	for recipient in _traveler_recipients(doc):
		_send(
			recipient,
			subject,
			"trip_booked.html",
			context,
			doc,
			attachments=[trip_ics_attachment(doc, recipient.row)],
		)

	owner_email = frappe.db.get_value("User", doc.owner, "email")
	traveler_users = {r.user_id for r in _traveler_recipients(doc)}
	if owner_email and doc.owner not in traveler_users and doc.owner != "Administrator":
		_send(
			frappe._dict(email=owner_email, user_id=doc.owner, employee_name=doc.owner),
			subject,
			"trip_booked.html",
			context,
			doc,
		)


def deliver_traveler_added(trip, employees):
	doc = frappe.get_doc("Travel Trip", trip)
	context = _base_context(doc)
	subject = _("You were added to a trip: {0} ({1} – {2})").format(
		doc.purpose, doc.start_date, doc.end_date
	)
	for recipient in _traveler_recipients(doc, employees=set(employees)):
		_send(
			recipient,
			subject,
			"traveler_added.html",
			context,
			doc,
			attachments=[trip_ics_attachment(doc, recipient.row)],
		)


def deliver_trip_closed(trip):
	"""Closed notice — only travelers whose Expense Claim is missing or still
	a draft get it (it is a nudge, not a broadcast)."""
	doc = frappe.get_doc("Travel Trip", trip)
	context = _base_context(doc)
	subject = _("Trip closed: {0} — check your expenses").format(doc.purpose)

	for recipient in _traveler_recipients(doc):
		claim_status = recipient.row.expense_claim_status
		if recipient.row.expense_claim and claim_status not in (None, "", "Draft"):
			continue
		_send(recipient, subject, "trip_closed.html", context, doc)


def notify_expense_claims_generated(doc, claims):
	"""Called by ``travel_management.api`` right after claim creation.
	``claims`` is ``{employee: claim_name}``; each traveler gets only their
	own claim link."""
	if _in_maintenance_context() or not _notifications_enabled() or not claims:
		return
	frappe.enqueue(
		"erpnext_enhancements.travel_management.notifications.deliver_expense_claims_generated",
		trip=doc.name,
		claims=claims,
		queue="short",
		enqueue_after_commit=True,
	)


def deliver_expense_claims_generated(trip, claims):
	doc = frappe.get_doc("Travel Trip", trip)
	base = _base_context(doc)
	for recipient in _traveler_recipients(doc, employees=set(claims)):
		claim = claims[recipient.employee]
		_send(
			recipient,
			_("Expense Claim {0} drafted for trip {1}").format(claim, doc.purpose),
			"expense_claim_generated.html",
			dict(base, claim=claim, claim_url=get_url_to_form("Expense Claim", claim)),
			doc,
		)


# ------------------------------------------------------------- itinerary


def send_itinerary_emails(doc, employee=None, force=False):
	"""Itinerary summary + ICS to one traveler (or all). Used by the "Send
	Itinerary" form button (force=True bypasses the master switch) and the
	pre-travel reminder job. Returns the list of employees actually emailed."""
	if not force and (_in_maintenance_context() or not _notifications_enabled()):
		return []

	from erpnext_enhancements.api.travel import shape_itinerary

	base = _base_context(doc)
	subject = _("Your itinerary: {0} ({1} – {2})").format(
		doc.purpose, doc.start_date, doc.end_date
	)

	sent = []
	employees = {employee} if employee else None
	for recipient in _traveler_recipients(doc, employees=employees):
		itinerary = shape_itinerary(doc, viewing_employee=recipient.employee)
		if _send(
			recipient,
			subject,
			"pre_travel_reminder.html",
			dict(base, itinerary=itinerary),
			doc,
			attachments=[trip_ics_attachment(doc, recipient.row)],
		):
			sent.append(recipient.employee)
	return sent
