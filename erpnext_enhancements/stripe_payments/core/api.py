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
the invoice. The webhook is the only ``allow_guest`` endpoint and is gated by Stripe
signature verification. Mirrors the QuickBooks module's role-gating.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt

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
	"""RPC (desk): start a Stripe Checkout for an ad-hoc amount (no invoice)."""
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
		message=(
			f"<p>Hello,</p><p>You can pay {frappe.utils.escape_html(label)} securely online here:</p>"
			f'<p><a href="{sp.checkout_url}">Pay now</a></p>'
			f"<p>Thank you,<br>Sapphire Fountains</p>"
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
	"""RPC: lightweight config for the payment UI (surcharge prompts). Login required.

	Safe for any signed-in user (desk staff or portal customer) — exposes no secrets.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(frappe._("Please log in."), frappe.PermissionError)
	settings = get_settings()
	return {
		"surcharge_enabled": bool(settings.surcharge_enabled),
		"card_surcharge_percent": settings.card_surcharge_percent,
		"card_surcharge_flat": settings.card_surcharge_flat,
		"ach_fee_percent": settings.ach_fee_percent,
		"ach_fee_flat": settings.ach_fee_flat,
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
	"""RPC (desk): charge a customer's saved method off-session (manual trigger)."""
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
