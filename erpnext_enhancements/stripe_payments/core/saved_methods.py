# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Saved payment methods + off-session charging (Phase 2).

Two flows, both mirroring ``checkout.py`` conventions and reusing the reconciler to
post Payment Entries:

* :func:`create_setup_session` — a Checkout Session in ``setup`` mode that saves a
  customer's card/bank **with consent** and **no charge**; the webhook
  (``reconcile._handle_setup_completed``) then stores the payment method on the
  Customer.
* :func:`charge_saved_method` — charge a customer's saved method **off-session**
  (e.g. for a maintenance invoice), confirming a PaymentIntent immediately and
  finalizing the Payment Entry on success.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, get_url

from erpnext_enhancements.stripe_payments.core.checkout import _payment_method_types, _resolve_target
from erpnext_enhancements.stripe_payments.core.client import (
	create_checkout_session,
	create_payment_intent,
	ensure_stripe_customer,
)
from erpnext_enhancements.stripe_payments.core.utils import (
	error_snippet,
	get_settings,
	is_enabled,
	to_minor_units,
)


def create_setup_session(customer: str, channel: str = "Desk") -> dict:
	"""Start a Checkout Session (setup mode) to save a payment method with consent."""
	settings = get_settings()
	if not is_enabled(settings):
		frappe.throw("Stripe Payments is not enabled.")

	stripe_customer_id = ensure_stripe_customer(customer, settings)
	success_url = f"{get_url(settings.success_route or '/stripe-return')}?status=setup&customer={customer}"
	cancel_url = f"{get_url(settings.cancel_route or '/stripe-return')}?status=cancel"

	params = {
		"mode": "setup",
		"customer": stripe_customer_id,
		"payment_method_types": _payment_method_types(settings),
		"metadata": {"erpnext_customer": customer, "purpose": "autopay_setup", "source": channel},
		"success_url": success_url,
		"cancel_url": cancel_url,
	}
	if settings.autopay_consent:
		# Required consent for charging the saved method off-session in future.
		params["custom_text"] = {"submit": {"message": settings.autopay_consent[:1200]}}

	try:
		session = create_checkout_session(
			params, idempotency_key=f"ee-setup-{customer}-{frappe.generate_hash(length=8)}"
		)
	except Exception as exc:
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: create_setup_session failed")
		frappe.throw(f"Could not start autopay setup: {error_snippet(str(exc), 200)}")

	return {"checkout_url": session["url"], "session_id": session["id"], "customer": customer}


def charge_saved_method(
	*,
	customer: str,
	amount=None,
	sales_invoice: str | None = None,
	description: str | None = None,
	channel: str = "Auto",
) -> dict:
	"""Charge a customer's saved method off-session; post a Payment Entry on success.

	Raises if the customer has no saved method. Returns
	``{"stripe_payment", "status", "payment_intent"}``.
	"""
	settings = get_settings()
	if not is_enabled(settings):
		frappe.throw("Stripe Payments is not enabled.")

	customer, amount, currency, description = _resolve_target(
		sales_invoice, customer, amount, description, settings
	)

	stripe_customer_id = frappe.db.get_value("Customer", customer, "custom_stripe_customer_id")
	payment_method = frappe.db.get_value("Customer", customer, "custom_stripe_default_payment_method")
	if not stripe_customer_id or not payment_method:
		frappe.throw(f"{customer} has no saved Stripe payment method. Enroll them in autopay first.")

	sp = frappe.get_doc(
		{
			"doctype": "Stripe Payment",
			"customer": customer,
			"sales_invoice": sales_invoice,
			"amount": amount,
			"currency": currency,
			"description": description,
			"channel": channel,
			"initiated_by": frappe.session.user,
			"stripe_customer_id": stripe_customer_id,
			"status": "Draft",
		}
	).insert(ignore_permissions=True)

	metadata = {"erpnext_customer": customer, "stripe_payment": sp.name, "source": channel}
	if sales_invoice:
		metadata["erpnext_invoice"] = sales_invoice

	params = {
		"amount": to_minor_units(amount, currency),
		"currency": (currency or "USD").lower(),
		"customer": stripe_customer_id,
		"payment_method": payment_method,
		"off_session": True,
		"confirm": True,
		"description": (description or "Payment")[:250],
		"metadata": metadata,
	}

	try:
		pi = create_payment_intent(params, idempotency_key=f"ee-offsession-{sp.name}")
	except Exception as exc:
		sp.db_set("status", "Failed")
		sp.db_set("error_message", error_snippet(str(exc)))
		frappe.db.commit()
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: off-session charge failed")
		frappe.throw(f"Off-session charge failed: {error_snippet(str(exc), 200)}")

	sp.db_set("stripe_payment_intent", pi.get("id"))
	status = pi.get("status")
	if status == "succeeded":
		# Post the Payment Entry now; the webhook is a dedupe-protected backstop.
		from erpnext_enhancements.stripe_payments.core import reconcile

		reconcile.finalize_payment(sp, pi)
	elif status == "processing":
		sp.db_set("status", "Processing")
	else:
		sp.db_set("status", "Failed")
		sp.db_set("error_message", f"PaymentIntent status: {status}")
	frappe.db.commit()
	sp.reload()
	return {"stripe_payment": sp.name, "status": sp.status, "payment_intent": pi.get("id")}


def auto_charge_on_invoice_submit(doc, method=None):
	"""Sales Invoice ``on_submit``: off-session charge the customer's saved method.

	Covers both the "auto on submit" and "scheduled by maintenance contract" triggers,
	since maintenance billing generates Sales Invoices. Best-effort and enqueued, so it
	never blocks or fails the invoice submission. No-ops unless the customer is
	autopay-enrolled and the invoice is outstanding and not already being charged.
	"""
	if not is_enabled():
		return
	enrolled = frappe.db.get_value(
		"Customer",
		doc.customer,
		["custom_stripe_autopay_enabled", "custom_stripe_default_payment_method"],
		as_dict=True,
	)
	if not enrolled or not enrolled.custom_stripe_autopay_enabled or not enrolled.custom_stripe_default_payment_method:
		return
	if flt(doc.outstanding_amount) <= 0:
		return
	# Don't double-charge if an active Stripe Payment already covers this invoice.
	if frappe.db.exists(
		"Stripe Payment", {"sales_invoice": doc.name, "status": ["in", ["Processing", "Paid"]]}
	):
		return
	frappe.enqueue(
		"erpnext_enhancements.stripe_payments.core.saved_methods.charge_saved_method",
		queue="short",
		customer=doc.customer,
		sales_invoice=doc.name,
		channel="Auto",
	)
