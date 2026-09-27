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

Rendering and delivery are separate steps (:func:`_render`, :func:`_deliver`;
:func:`_send` runs both), so Plan a Trip can show one person's itinerary email
and calendar invite exactly as they will get them without sending anything
(:func:`render_itinerary_preview`).
"""

import frappe
from frappe import _
from frappe.utils import cint, get_url, get_url_to_form

from erpnext_enhancements import email_style
from erpnext_enhancements.travel_management.ics import trip_events_for_traveler, trip_ics_attachment
from erpnext_enhancements.travel_management.itinerary_text import booking_lines, day_lines
from erpnext_enhancements.travel_management.views import itinerary_path

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
		# This trip, not bare /itinerary: bare opens whichever trip is current, and an
		# owner who is not on the crew landed on "No upcoming or recent trips".
		"itinerary_url": get_url(itinerary_path(doc.name)),
		"guidelines_url": get_url("/travel_guidelines"),
	}


def _render(recipient, subject, template, context):
	"""``(fragment, html)`` for one email: the rendered template, and the same wrapped in
	the email chrome exactly as it is sent. Renders only — no mail, no log, no job — which
	is what lets :func:`render_itinerary_preview` show an email with no way to send it."""
	message = frappe.render_template(f"{TEMPLATE_DIR}/{template}", dict(context, recipient=recipient))
	return message, email_style.wrap(message, title=subject, eyebrow=_("Travel"))


def _deliver(recipient, subject, message, html, doc, attachments=None):
	"""Send one rendered email and write its Notification Log. Raises on failure."""
	frappe.sendmail(
		recipients=[recipient.email],
		subject=subject,
		# Chrome for the email only. The Notification Log row below keeps the
		# UNWRAPPED fragment on purpose: that content is rendered in the desk
		# bell panel, which supplies its own frame, and a letterhead inside a
		# dropdown notification is noise.
		message=html,
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


def _send(recipient, subject, template, context, doc, attachments=None):
	"""One email + Notification Log, failure logged and swallowed.

	The name and signature are load-bearing: ``reminders.py`` imports and calls it."""
	if not recipient.email:
		return False
	try:
		message, html = _render(recipient, subject, template, context)
		_deliver(recipient, subject, message, html, doc, attachments)
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


def _bookings_context(doc, employee=None):
	"""The recipient's own bookings with their numbers — or, for the owner who is not on
	the trip, every booking with who is on it — for the booked / added emails."""
	from erpnext_enhancements.api.travel import shape_itinerary

	return {
		"bookings": booking_lines(shape_itinerary(doc, viewing_employee=employee)),
		"bookings_title": _("Your bookings") if employee else _("Bookings"),
	}


def deliver_trip_booked(trip):
	"""All travelers + the owner get the booked notice, each with their own bookings and
	confirmation numbers listed; travelers get their personal ICS calendar attached."""
	doc = frappe.get_doc("Travel Trip", trip)
	context = _base_context(doc)
	subject = _("Trip booked: {0} ({1} – {2})").format(doc.purpose, doc.start_date, doc.end_date)

	for recipient in _traveler_recipients(doc):
		_send(
			recipient,
			subject,
			"trip_booked.html",
			dict(context, **_bookings_context(doc, recipient.employee)),
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
			dict(context, **_bookings_context(doc)),
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
			dict(context, **_bookings_context(doc, recipient.employee)),
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


def _itinerary_email(doc, recipient, base=None, poi_cache=None):
	"""What the itinerary email carries for one traveler: ``{subject, template, context,
	attachments}``. The one definition both :func:`send_itinerary_emails` and
	:func:`render_itinerary_preview` build from, so a preview cannot drift from the send."""
	from erpnext_enhancements.api.travel import shape_itinerary

	itinerary = shape_itinerary(doc, viewing_employee=recipient.employee, poi_cache=poi_cache)
	return {
		"subject": _("Your itinerary: {0} ({1} – {2})").format(doc.purpose, doc.start_date, doc.end_date),
		"template": "pre_travel_reminder.html",
		"context": dict(
			base if base is not None else _base_context(doc),
			itinerary=itinerary,
			itinerary_days=day_lines(itinerary),
		),
		"attachments": [trip_ics_attachment(doc, recipient.row)],
	}


def send_itinerary_emails(doc, employee=None, force=False):
	"""Itinerary summary + ICS to one traveler (or all). Used by the "Send
	Itinerary" form button (force=True bypasses the master switch) and the
	pre-travel reminder job. Returns the list of employees actually emailed."""
	if not force and (_in_maintenance_context() or not _notifications_enabled()):
		return []

	base = _base_context(doc)
	poi_cache = {}

	sent = []
	employees = {employee} if employee else None
	for recipient in _traveler_recipients(doc, employees=employees):
		email = _itinerary_email(doc, recipient, base, poi_cache)
		if _send(
			recipient,
			email["subject"],
			email["template"],
			email["context"],
			doc,
			attachments=email["attachments"],
		):
			sent.append(recipient.employee)
	return sent


def _full_html(subject, html):
	"""The message as the inbox receives it: frappe wraps every queued email in its own
	``standard.html`` and inlines the CSS (``get_formatted_html``, v16 email_body.py). Falls
	back to our wrapped body where that is not importable or fails, so the preview then
	lacks only frappe's outer frame, never our content."""
	try:
		from frappe.email.email_body import get_formatted_html
	except ImportError:
		return html
	try:
		return get_formatted_html(subject, html)
	except Exception:
		return html


def render_itinerary_preview(doc, employee):
	"""Exactly what :func:`send_itinerary_emails` would send ``employee`` (the subject, the
	email as delivered, the .ics and its events), rendered and NOT sent: no
	``frappe.sendmail``, no Notification Log, no job. The caller checks that ``employee``
	is on the crew.

	A person with no email address still gets a preview, with ``no_email`` set: the page
	shows what they would miss rather than an error."""
	row = next((t for t in doc.travelers if t.employee == employee), None)
	if row is None:
		frappe.throw(_("That person is not on this trip."))
	found = _traveler_recipients(doc, employees={employee})
	if found:
		recipient = found[0]
	else:
		# No Employee record behind the row: nothing to send to, but still a preview.
		recipient = frappe._dict(
			row=row,
			employee=employee,
			employee_name=row.employee_name or employee,
			email=None,
			user_id=None,
		)

	email = _itinerary_email(doc, recipient)
	_fragment, html = _render(recipient, email["subject"], email["template"], email["context"])
	ics = email["attachments"][0]

	return {
		"subject": email["subject"],
		"to_name": recipient.employee_name,
		"to_email": recipient.email or None,
		"html": _full_html(email["subject"], html),
		"ics": ics["fcontent"],
		"ics_filename": ics["fname"],
		"events": [
			{
				"summary": event.get("summary"),
				"start": str(event["start"]) if event.get("start") else None,
				"end": str(event["end"]) if event.get("end") else None,
				"all_day": bool(event.get("all_day")),
				"location": event.get("location") or None,
				"description": event.get("description") or None,
			}
			for event in trip_events_for_traveler(doc, recipient.row)
		],
		"no_email": not recipient.email,
	}
