# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted RPC entry points for the Stripe Payments integration.

The public surface called by the browser (Settings form, dashboard, Sales Invoice
button), by the customer portal, and by Stripe (the webhook). These are thin
``@frappe.whitelist`` wrappers that enforce the permission boundary, then delegate
to ``checkout`` / ``client`` / ``webhooks``.

Access control: payment creation and the dashboard are restricted to accounting
operators via ``_require_stripe_operator``. The customer portal uses a separate
endpoint (``portal_create_payment``) that instead checks the logged-in user owns
the invoice. ``portal_invoice_pdf`` (the "View invoice (PDF)" link on ``/pay`` and
``/pay-card``) applies the same ownership rule, submitted invoices only, and answers
every refusal alike. The webhook is the only ``allow_guest`` endpoint and is gated by
Stripe signature verification. Mirrors the QuickBooks module's role-gating.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt

from erpnext_enhancements import email_style
from erpnext_enhancements.stripe_payments.core.checkout import create_payment
from erpnext_enhancements.stripe_payments.core.utils import get_secret, get_settings
from erpnext_enhancements.stripe_payments.core.webhooks import handle_webhook

STRIPE_OPERATOR_ROLES = ("System Manager", "Accounts Manager")


def _require_stripe_operator():
	"""Throw ``frappe.PermissionError`` unless the user is an accounting operator."""
	frappe.only_for(STRIPE_OPERATOR_ROLES)


@frappe.whitelist()
def create_invoice_payment(sales_invoice, method=None):
	"""RPC (desk): start a Stripe Checkout for a Sales Invoice. Returns checkout url.

	``method`` ("card"/"ach") locks the session and applies that method's fee when
	surcharging is on; omit it to offer all enabled methods with no fee.
	"""
	_require_stripe_operator()
	return create_payment(sales_invoice=sales_invoice, channel="Desk", method=method)


@frappe.whitelist()
def create_adhoc_payment(customer, amount, description=None, method=None):
	"""RPC (desk): start a Stripe Checkout for an ad-hoc amount (no invoice). Refused while the
	customer has a Stripe payment settling or a link open: it is applied to no invoice, so it
	would not settle that bill (``card_element._refuse_ad_hoc_beside_open_payments``)."""
	_require_stripe_operator()
	return create_payment(
		customer=customer, amount=flt(amount), description=description, channel="Desk", method=method
	)


@frappe.whitelist()
def send_payment_link(stripe_payment, via="email", to=None):
	"""RPC (desk): email or text the hosted-checkout link for a Stripe Payment.

	``via`` is "email" or "sms"; ``to`` overrides the auto-resolved recipient.
	SMS reuses the existing Triton sender (``api.telephony.send_system_sms``).
	"""
	_require_stripe_operator()
	sp = frappe.get_doc("Stripe Payment", stripe_payment)
	if not sp.checkout_url:
		frappe.throw("This payment has no checkout link to send.")
	if sp.status not in ("Link Sent", "Processing"):
		frappe.throw(f"Cannot send a link for a payment that is {sp.status}.")
	if sp.status == "Link Sent" and sp.sales_invoice:
		# A link outlives its invoice: canceled, amended, credited or paid another way since
		# it was made, paying it now would be a second payment or an overpayment.
		from erpnext_enhancements.stripe_payments.core.card_element import link_is_stale

		if link_is_stale(sp.sales_invoice, sp.amount):
			frappe.throw(
				f"Sales Invoice {sp.sales_invoice} has changed since this link was made (canceled, "
				"amended, or paid in part or in full), so it must not be sent. Start a new Stripe "
				"payment from the invoice if anything is still owed."
			)

	label = sp.description or "your payment"
	message = f"Sapphire Fountains — pay {label}: {sp.checkout_url}"

	if via == "sms":
		number = to or _customer_mobile(sp.customer)
		if not number:
			frappe.throw("No mobile number found for this customer.")
		from erpnext_enhancements.api.telephony import send_system_sms

		send_system_sms(number, message)
		return {"sent": True, "via": "sms", "to": number}

	recipient = to or _customer_email(sp.customer)
	if not recipient:
		frappe.throw("No email address found for this customer.")
	frappe.sendmail(
		recipients=[recipient],
		subject=f"Payment link — {label}",
		message=email_style.wrap(
			email_style.p("Hello,")
			+ email_style.p(f"You can pay {label} securely online here:")
			+ email_style.button(sp.checkout_url, "Pay now")
			+ email_style.button_fallback(sp.checkout_url)
			+ email_style.p("Thank you, Sapphire Fountains"),
			title=f"Payment link — {label}",
			eyebrow="Billing",
			tagline=True,
		),
	)
	return {"sent": True, "via": "email", "to": recipient}


@frappe.whitelist()
def test_connection():
	"""RPC (desk): verify the Stripe credentials and report config readiness."""
	_require_stripe_operator()
	from erpnext_enhancements.stripe_payments.core.client import retrieve_account
	from erpnext_enhancements.stripe_payments.core.utils import update_settings_status

	settings = get_settings()
	try:
		account = retrieve_account(settings)
	except Exception as exc:
		from erpnext_enhancements.stripe_payments.core.utils import error_snippet

		update_settings_status("Error", error_snippet(str(exc), 300))
		frappe.throw(f"Stripe connection failed: {error_snippet(str(exc), 200)}")

	update_settings_status("Connected", f"Connected to Stripe account {account.get('id')}.")
	return {
		"account_id": account.get("id"),
		"environment": settings.environment,
		"deposit_account_set": bool(settings.deposit_account),
		"card_mode_set": bool(settings.card_mode_of_payment),
		"ach_mode_set": bool(settings.ach_mode_of_payment),
	}


@frappe.whitelist()
def get_dashboard_status():
	"""RPC (desk): connection state, config readiness, counts and recent payments."""
	_require_stripe_operator()
	settings = get_settings()
	counts = {
		status: frappe.db.count("Stripe Payment", {"status": status})
		for status in ("Link Sent", "Processing", "Paid", "Failed", "Expired", "Refunded")
	}
	recent = frappe.get_all(
		"Stripe Payment",
		fields=[
			"name",
			"customer",
			"sales_invoice",
			"amount",
			"currency",
			"status",
			"payment_method_type",
			"channel",
			"payment_entry",
			"modified",
		],
		order_by="modified desc",
		limit_page_length=15,
	)
	return {
		"settings": {
			"environment": settings.environment,
			"enabled": settings.enabled,
			"company": settings.company,
			"status": settings.status,
			"status_message": settings.status_message,
			"last_webhook_at": settings.last_webhook_at,
			"deposit_account": settings.deposit_account,
			"card_mode_of_payment": settings.card_mode_of_payment,
			"ach_mode_of_payment": settings.ach_mode_of_payment,
			"enable_card": settings.enable_card,
			"enable_ach": settings.enable_ach,
			"webhook_url": settings.webhook_url,
			"has_secret_key": bool(get_secret(settings, "secret_key")),
			"has_webhook_secret": bool(get_secret(settings, "webhook_signing_secret")),
		},
		"counts": counts,
		"recent": recent,
	}


@frappe.whitelist()
def payment_config():
	"""RPC: lightweight config for the payment UI. Login required.

	Safe for any signed-in user (desk staff or portal customer) — exposes no secrets.
	Deliberately returns no surcharge rates: a fee depends on the card's funding type,
	which is not knowable until the payer's card is in hand, so no UI can quote one up
	front. Quoting a rate here is what let a debit customer be shown a fee they must
	never pay.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(frappe._("Please log in."), frappe.PermissionError)
	settings = get_settings()
	return {
		"enable_card": bool(settings.enable_card),
		"enable_ach": bool(settings.enable_ach),
		"currency": frappe.db.get_value("Company", settings.company, "default_currency") or "USD",
	}


@frappe.whitelist()
def portal_create_payment(sales_invoice, method=None):
	"""RPC (portal): a logged-in customer pays one of *their own* invoices.

	Not operator-gated; instead verifies the session user's Customer owns the
	invoice before creating the Checkout Session.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(frappe._("Please log in to pay."), frappe.PermissionError)

	customer = frappe.db.get_value("Sales Invoice", sales_invoice, "customer")
	if not customer or customer not in get_portal_customers():
		frappe.throw(frappe._("You can only pay your own invoices."), frappe.PermissionError)

	return create_payment(sales_invoice=sales_invoice, channel="Portal", method=method)


def _own_invoice_or_throw(sales_invoice):
	"""Portal guard: the logged-in user's Customer must own this invoice."""
	if frappe.session.user == "Guest":
		frappe.throw(frappe._("Please log in to pay."), frappe.PermissionError)
	customer = frappe.db.get_value("Sales Invoice", sales_invoice, "customer")
	if not customer or customer not in get_portal_customers():
		frappe.throw(frappe._("You can only pay your own invoices."), frappe.PermissionError)
	return customer


@frappe.whitelist()
def portal_price_card_payment(sales_invoice, confirmation_token):
	"""RPC (portal): quote the true total for the card the payer just entered.

	Step one of the two-step card flow. Reads the card's funding type from the
	ConfirmationToken and prices the surcharge — credit only. **No money moves**; the
	payer sees the breakdown and confirms (or goes back and uses a different card)
	before anything is charged.
	"""
	from erpnext_enhancements.stripe_payments.core.card_element import price_card_payment

	_own_invoice_or_throw(sales_invoice)
	return price_card_payment(
		confirmation_token=confirmation_token, sales_invoice=sales_invoice, channel="Portal"
	)


@frappe.whitelist()
def portal_confirm_card_payment(stripe_payment, confirmation_token):
	"""RPC (portal): charge the total quoted by ``portal_price_card_payment``.

	Re-checks ownership against the ledger row itself rather than trusting the row id
	from the client, and ``card_element.confirm_card_payment`` additionally requires
	the same ConfirmationToken the quote was made against.
	"""
	from erpnext_enhancements.stripe_payments.core.card_element import confirm_card_payment

	if frappe.session.user == "Guest":
		frappe.throw(frappe._("Please log in to pay."), frappe.PermissionError)
	customer = frappe.db.get_value("Stripe Payment", stripe_payment, "customer")
	if not customer or customer not in get_portal_customers():
		frappe.throw(frappe._("You can only pay your own invoices."), frappe.PermissionError)

	return confirm_card_payment(
		stripe_payment=stripe_payment, confirmation_token=confirmation_token
	)


def _own_submitted_invoice(invoice):
	"""Portal guard for reading an invoice: its canonical name when the logged-in user's
	Customer owns it and it is submitted, else None.

	Never raises, and every refusal is the same None — a name that is not a string, a missing
	invoice, another customer's, a draft, a canceled one — so the caller can give them one
	answer. The Guest check is a backstop: the endpoint is not ``allow_guest``, so a signed-out
	tab gets Frappe's own 403 "Not Permitted" page before this runs. The ownership rule is ``/pay``'s and ``/pay-card``'s (the invoice's Customer is
	one of ``get_portal_customers()``); submitted only because a draft is not yet the customer's
	bill and a canceled one no longer is. A credit note that is theirs is theirs to read.

	The same answer is not enough if it takes a different time, so the work done does not depend
	on the invoice either. The user's customers are looked up first, whatever was asked for, and
	then the invoice is one query that carries all three conditions. Looking the invoice up first
	would have run the customer lookup (Contact, Dynamic Link, sometimes Contact Email) only for
	a submitted invoice that exists, and the response time would have said which ones do.
	"""
	if frappe.session.user == "Guest" or not invoice or not isinstance(invoice, str):
		return None
	customers = get_portal_customers()
	if not customers:
		return None
	# The stored name, not the one typed: MariaDB matches a name case-insensitively.
	return (
		frappe.db.get_value(
			"Sales Invoice",
			{"name": invoice, "customer": ["in", customers], "docstatus": 1},
			"name",
		)
		or None
	)


@frappe.whitelist(methods=["GET"])
def portal_invoice_pdf(invoice=None):
	"""RPC (portal, GET): one of the logged-in customer's own invoices, as a PDF shown inline.

	The "View invoice (PDF)" link on ``/pay`` and ``/pay-card``, which opens in a new tab. Reads
	only, which is why it is GET: a link, no CSRF token, and Frappe rolls a GET's transaction
	back. Ownership is checked here (``_own_submitted_invoice``); anything it refuses gets the
	same "not available" page, so the answer is no oracle for which invoices exist. Rendering —
	the customer-facing print format and no other, the Desk's PDF generator, print permission
	waived for this render only, never ``set_user``, a site-wide and a per-user limit — and the
	response are ``core.invoice_pdf``'s. Not gated on the Stripe switch: reading your own
	invoice is not a payment.
	"""
	from erpnext_enhancements.stripe_payments.core import invoice_pdf

	name = _own_submitted_invoice(invoice)
	if not name:
		return invoice_pdf.respond_not_available()
	return invoice_pdf.respond_with_pdf(name)


@frappe.whitelist()
def enroll_autopay(customer):
	"""RPC (desk): start a setup-mode Checkout to save a customer's method for autopay."""
	_require_stripe_operator()
	from erpnext_enhancements.stripe_payments.core.saved_methods import create_setup_session

	return create_setup_session(customer, channel="Desk")


@frappe.whitelist()
def portal_enroll_autopay():
	"""RPC (portal): a logged-in customer saves their own method for autopay."""
	if frappe.session.user == "Guest":
		frappe.throw(frappe._("Please log in."), frappe.PermissionError)
	customers = get_portal_customers()
	if not customers:
		frappe.throw(frappe._("No customer is linked to your account."), frappe.PermissionError)
	from erpnext_enhancements.stripe_payments.core.saved_methods import create_setup_session

	return create_setup_session(customers[0], channel="Portal")


@frappe.whitelist()
def charge_saved_method(customer, amount=None, sales_invoice=None, description=None):
	"""RPC (desk): charge a customer's saved method off-session (manual trigger).

	With ``sales_invoice`` (the Customer form's prompt offers the customer's outstanding
	invoices) it is the guarded invoice path, charged for the invoice's outstanding amount.
	Without one it is an ad hoc charge allocated to no invoice, refused while the customer
	has a Stripe payment in flight or a link open (``saved_methods.charge_saved_method``).
	"""
	_require_stripe_operator()
	from erpnext_enhancements.stripe_payments.core.saved_methods import (
		charge_saved_method as _charge_saved_method,
	)

	return _charge_saved_method(
		customer=customer,
		amount=flt(amount) if amount else None,
		sales_invoice=sales_invoice,
		description=description,
		channel="Desk",
	)


@frappe.whitelist()
def revoke_autopay(customer):
	"""RPC (desk): cancel a customer's autopay — detach the method, clear flags, revoke consent."""
	_require_stripe_operator()
	from erpnext_enhancements.stripe_payments.core.saved_methods import revoke_autopay as _revoke

	return _revoke(customer)


@frappe.whitelist()
def portal_revoke_autopay():
	"""RPC (portal): a logged-in customer cancels their own autopay."""
	if frappe.session.user == "Guest":
		frappe.throw(frappe._("Please log in."), frappe.PermissionError)
	customers = get_portal_customers()
	if not customers:
		frappe.throw(frappe._("No customer is linked to your account."), frappe.PermissionError)
	from erpnext_enhancements.stripe_payments.core.saved_methods import revoke_autopay as _revoke

	return _revoke(customers[0])


@frappe.whitelist()
def refund_payment(stripe_payment, amount=None):
	"""RPC (desk): refund a Stripe Payment in Stripe (full unless ``amount`` given).

	The ``charge.refunded`` webhook records the refunded amount + status back on the
	Stripe Payment. Booking the GL reversal is a manual step for now.
	"""
	_require_stripe_operator()
	from erpnext_enhancements.stripe_payments.core.client import create_refund
	from erpnext_enhancements.stripe_payments.core.utils import to_minor_units

	sp = frappe.get_doc("Stripe Payment", stripe_payment)
	if not sp.stripe_payment_intent:
		frappe.throw("This payment has no PaymentIntent to refund.")
	if sp.status not in ("Paid", "Processing", "Refunded"):
		frappe.throw(f"Cannot refund a payment that is {sp.status}.")

	minor = to_minor_units(flt(amount), sp.currency) if amount else None
	refund = create_refund(sp.stripe_payment_intent, amount_minor=minor)
	return {"refund": refund.get("id"), "status": refund.get("status")}


@frappe.whitelist(allow_guest=True)
def stripe_webhook():
	"""RPC (guest): inbound Stripe webhook endpoint; signature-verified in handler."""
	return handle_webhook()


# --- shared helpers ---------------------------------------------------------


def get_portal_customers(user: str | None = None) -> list[str]:
	"""Customers linked to a portal user (via their Contact's dynamic links)."""
	user = user or frappe.session.user
	if not user or user == "Guest":
		return []
	contacts = frappe.get_all("Contact", filters={"user": user}, pluck="name")
	if not contacts:
		contacts = frappe.get_all("Contact Email", filters={"email_id": user}, pluck="parent")
	if not contacts:
		return []
	return frappe.get_all(
		"Dynamic Link",
		filters={
			"parenttype": "Contact",
			"parent": ["in", contacts],
			"link_doctype": "Customer",
		},
		pluck="link_name",
	)


def _customer_email(customer: str) -> str | None:
	contact = frappe.db.get_value("Customer", customer, "customer_primary_contact")
	if contact:
		return frappe.db.get_value("Contact", contact, "email_id")
	return None


def _customer_mobile(customer: str) -> str | None:
	contact = frappe.db.get_value("Customer", customer, "customer_primary_contact")
	if contact:
		return frappe.db.get_value("Contact", contact, "mobile_no") or frappe.db.get_value(
			"Contact", contact, "phone"
		)
	return None
