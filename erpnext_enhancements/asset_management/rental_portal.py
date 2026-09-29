# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The customer side of an event rental: the ``/rentals`` portal and its two actions (v1.565.0).

A customer signed in (by email link, ``portal_login``) sees every rental of the customers their
Contact is linked to — the same resolution ``/pay`` uses (``stripe_payments.core.api.
get_portal_customers``) — and on each one can fill in the **site prep** the delivery crew needs
and **ask for a change**. Paying goes to the existing ``/pay`` page, which already carries every
Stripe safety rule; nothing here takes money.

Every read and write checks that the booking belongs to one of the user's customers. Writes set
an explicit allowlist of fields with ``db.set_value`` rather than saving the booking, so a
customer can never move its dates, lines, status or money — those stay with staff and the
availability checks.
"""

from urllib.parse import quote

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, now_datetime

#: What a customer may write on their own booking.
SITE_PREP_FIELDS = (
	"site_contact_name",
	"site_contact_phone",
	"surface",
	"site_access",
	"power_source",
	"water_source",
	"site_prep_notes",
)
FIELD_MAX_LENGTH = 1000
SURFACES = ("", "Grass", "Concrete", "Pavers", "Deck", "Indoor Floor", "Other")

#: Bookings a customer sees. Canceled and Expired ones are not theirs to act on.
VISIBLE_STATUSES = ("Tentative", "Confirmed", "Out", "Returned", "Closed")
#: Bookings whose site prep can still change: before the fountains come back.
EDITABLE_STATUSES = ("Tentative", "Confirmed", "Out")

#: What the customer reads for each status.
CUSTOMER_STATUS = {
	"Tentative": _("On hold"),
	"Confirmed": _("Confirmed"),
	"Out": _("Delivered"),
	"Returned": _("Picked up"),
	"Closed": _("Complete"),
}


def portal_customers(user=None):
	from erpnext_enhancements.stripe_payments.core.api import get_portal_customers

	return get_portal_customers(user)


def own_booking(name, user=None):
	"""The booking if it is visible to this portal user, else PermissionError."""
	customers = portal_customers(user)
	row = frappe.db.get_value("Rental Booking", name, ["name", "customer", "status"], as_dict=True) if name else None
	if not row or row.customer not in customers or row.status not in VISIBLE_STATUSES:
		# Same answer for "not yours" and "does not exist": the name reveals nothing.
		frappe.throw(_("Rental not found."), frappe.PermissionError)
	return row


def list_bookings(user=None):
	customers = portal_customers(user)
	if not customers:
		return []
	return frappe.get_all(
		"Rental Booking",
		filters={"customer": ["in", customers], "status": ["in", VISIBLE_STATUSES]},
		fields=["name", "status", "event_name", "delivery_datetime", "takedown_datetime", "customer_name"],
		order_by="delivery_datetime desc",
		limit_page_length=100,
	)


def booking_detail(name, user=None):
	"""Everything the portal shows for one rental (already ownership-checked)."""
	own_booking(name, user)
	doc = frappe.get_doc("Rental Booking", name)
	pools = {
		row.pool: frappe.db.get_value("Rental Accessory Pool", row.pool, "pool_name") or row.pool
		for row in doc.accessories or []
	}
	invoices = frappe.get_all(
		"Sales Invoice",
		filters={"custom_rental_booking": name, "docstatus": 1},
		fields=["name", "custom_rental_invoice_kind", "grand_total", "outstanding_amount", "due_date", "currency"],
		order_by="posting_date asc",
	)
	agreement = None
	if doc.rental_agreement:
		agreement = frappe.db.get_value(
			"Project Contract", doc.rental_agreement, ["status", "signed_on"], as_dict=True
		)
	return frappe._dict(
		name=doc.name,
		status=doc.status,
		status_label=CUSTOMER_STATUS.get(doc.status, doc.status),
		event_name=doc.event_name,
		venue=frappe.db.get_value("Address", doc.venue_address, "address_line1") if doc.venue_address else "",
		delivery_datetime=doc.delivery_datetime,
		event_start_datetime=doc.event_start_datetime,
		takedown_datetime=doc.takedown_datetime,
		hold_expires_on=doc.hold_expires_on if doc.status == "Tentative" else None,
		fountains=[row.asset_name or row.asset for row in doc.fountains or []],
		accessories=[f"{cint(row.qty)} × {pools[row.pool]}" for row in doc.accessories or []],
		invoices=invoices,
		agreement=agreement,
		editable=doc.status in EDITABLE_STATUSES,
		site_prep={field: doc.get(field) or "" for field in SITE_PREP_FIELDS},
		site_prep_updated_on=doc.site_prep_updated_on,
	)


def _require_login():
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=3600)
def save_site_prep(booking, **values):
	"""The portal's site-prep form. Writes only :data:`SITE_PREP_FIELDS`."""
	_require_login()
	row = own_booking(booking)
	if row.status not in EDITABLE_STATUSES:
		frappe.throw(_("This rental is finished, so its site details can no longer change."))
	clean = {}
	for field in SITE_PREP_FIELDS:
		if field in values:
			value = str(values.get(field) or "").strip()[:FIELD_MAX_LENGTH]
			if field == "surface" and value not in SURFACES:
				frappe.throw(_("Pick a surface from the list."))
			clean[field] = value
	if not clean:
		return {"saved": False}
	clean["site_prep_updated_on"] = now_datetime()
	frappe.db.set_value("Rental Booking", booking, clean, update_modified=True)
	frappe.get_doc("Rental Booking", booking).add_comment(
		"Comment", _("{0} updated the site details on the portal.").format(frappe.session.user)
	)
	return {"saved": True}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=3600)
def request_change(booking, message):
	"""The portal's "ask for a change" box: a comment plus a to-do for whoever owns the booking."""
	_require_login()
	own_booking(booking)
	message = str(message or "").strip()[:FIELD_MAX_LENGTH]
	if not message:
		frappe.throw(_("Tell us what you would like to change."))
	doc = frappe.get_doc("Rental Booking", booking)
	doc.add_comment(
		"Comment",
		_("Change requested by {0} on the portal:").format(frappe.session.user)
		+ "<br>"
		+ frappe.utils.escape_html(message),
	)
	owner = doc.owner if frappe.db.get_value("User", doc.owner, "user_type") == "System User" else None
	if owner:
		# A ToDo inserted directly: assign_to.add checks the CALLER's permission on the booking,
		# and a portal customer has none.
		frappe.get_doc(
			{
				"doctype": "ToDo",
				"allocated_to": owner,
				"reference_type": "Rental Booking",
				"reference_name": booking,
				"description": _("Customer change request on {0}: {1}").format(booking, frappe.utils.escape_html(message[:200])),
				"assigned_by": frappe.session.user,
			}
		).insert(ignore_permissions=True)
	return {"sent": True}



# ---------------------------------------------------------------- accounts and invitations


def open_portal_for_signer(contract_name, booking_name):
	"""After a Rental Agreement is signed: a portal account for the signer, and an email saying where.

	Called from ``rental_sales.on_rental_agreement_signed`` inside its own savepoint; it may raise.
	The signer's address is the one the e-sign flow confirmed (``Contract Signature Request``), so
	the account belongs to someone who just proved they read that inbox.
	"""
	from erpnext_enhancements.portal_login import ensure_portal_user

	request = frappe.get_all(
		"Contract Signature Request",
		filters={"project_contract": contract_name, "status": "Signed"},
		fields=["signer_email", "signed_name", "signer_name"],
		order_by="signed_on desc",
		limit_page_length=1,
	)
	if not request or not request[0].signer_email:
		return None
	booking = frappe.get_doc("Rental Booking", booking_name)
	user = ensure_portal_user(booking.customer, request[0].signer_email, request[0].signed_name or request[0].signer_name)
	if user:
		send_portal_invite(booking, user)
	return user


@frappe.whitelist(methods=["POST"])
def invite_to_portal(booking, contact):
	"""Staff: give one of the customer's contacts a portal account and email them the way in."""
	from erpnext_enhancements.portal_login import ensure_portal_user

	doc = frappe.get_doc("Rental Booking", booking)
	doc.check_permission("write")
	linked = frappe.db.exists(
		"Dynamic Link",
		{"parenttype": "Contact", "parent": contact, "link_doctype": "Customer", "link_name": doc.customer},
	)
	if not linked:
		frappe.throw(_("{0} is not a contact of {1}.").format(contact, doc.customer_name or doc.customer))
	email = frappe.db.get_value("Contact", contact, "email_id")
	if not email:
		frappe.throw(_("{0} has no email address.").format(contact))
	user = ensure_portal_user(doc.customer, email, frappe.db.get_value("Contact", contact, "full_name"))
	if not user:
		frappe.throw(_("{0} belongs to a staff account, which signs in with Google.").format(email))
	send_portal_invite(doc, user)
	doc.add_comment("Comment", _("Portal invitation sent to {0}.").format(email))
	return user


def send_portal_invite(booking, email):
	"""Where the rental lives and how to sign in. In the email design system; queued, never raises out."""
	from erpnext_enhancements import email_style

	url = frappe.utils.get_url(f"/rentals?booking={quote(booking.name)}")
	event = booking.event_name or _("your fountain rental")
	subject = _("View {0} online").format(event)
	body = (
		email_style.p(_("Hello,"))
		+ email_style.p(
			_(
				"You can see {0} any time: the schedule, what is booked, invoices, and a form for the details "
				"our crew needs on site."
			).format(event)
		)
		+ email_style.button(url, _("View my rental"))
		+ email_style.button_fallback(url)
		+ email_style.p(
			_(
				'To sign in, enter this email address and choose "Login with Email Link". We will email you a '
				"one-time link. There is no password to remember."
			)
		)
		+ email_style.p(_("Thank you, Sapphire Fountains"))
	)
	frappe.sendmail(
		recipients=[email],
		subject=subject,
		message=email_style.wrap(body, title=subject, eyebrow=_("Rentals"), tagline=True, pillar="rent"),
		reference_doctype="Rental Booking",
		reference_name=booking.name,
	)
