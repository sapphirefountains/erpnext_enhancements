# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Build hosted Stripe Checkout Sessions and the ``Stripe Payment`` rows behind them.

``create_payment`` is the single entry point used by both initiation channels (the
Sales Invoice desk button and the customer portal). It resolves the customer and
amount (from a Sales Invoice or an ad-hoc figure), records a ``Stripe Payment``
ledger row, then creates a Stripe-hosted Checkout Session whose metadata carries
the reconciliation keys the webhook uses to post a Payment Entry. No card data ever
touches this server.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, get_url

from erpnext_enhancements.stripe_payments.core.client import (
	create_checkout_session,
	ensure_stripe_customer,
)
from erpnext_enhancements.stripe_payments.core.utils import (
	error_snippet,
	get_settings,
	is_enabled,
	to_minor_units,
)


def create_payment(
	*,
	sales_invoice: str | None = None,
	amount=None,
	description: str | None = None,
	customer: str | None = None,
	channel: str = "Desk",
	method: str | None = None,
):
	"""Create a Checkout Session for an invoice or ad-hoc amount; return its URL.

	Exactly one of ``sales_invoice`` or (``customer`` + ``amount``) must be given.
	``method`` ("card"/"ach") locks the session to one method and applies that
	method's surcharge/fee; when omitted, all enabled methods are offered with no
	fee. Returns ``{"stripe_payment", "checkout_url", "session_id"}``. The caller is
	responsible for permission checks (operator role for desk; ownership for portal).
	"""
	settings = get_settings()
	if not is_enabled(settings):
		frappe.throw("Stripe Payments is not enabled. Turn it on in Stripe Payments Settings.")

	customer, amount, currency, description = _resolve_target(
		sales_invoice, customer, amount, description, settings
	)

	payment_method_types = _methods_for(settings, method)
	surcharge = _compute_surcharge(settings, method, amount)
	stripe_customer_id = ensure_stripe_customer(customer, settings)

	# Ledger row first, so we have a stable name to use as the idempotency key and
	# metadata back-reference before we call Stripe.
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
			"surcharge_amount": surcharge,
			"payment_method_type": _method_hint(method),
			"status": "Draft",
		}
	).insert(ignore_permissions=True)

	metadata = {
		"erpnext_customer": customer,
		"stripe_payment": sp.name,
		"source": channel,
	}
	if sales_invoice:
		metadata["erpnext_invoice"] = sales_invoice

	cur = (currency or "USD").lower()
	line_items = [
		{
			"quantity": 1,
			"price_data": {
				"currency": cur,
				"unit_amount": to_minor_units(amount, currency),
				"product_data": {"name": (description or "Payment")[:250]},
			},
		}
	]
	if surcharge > 0:
		line_items.append(
			{
				"quantity": 1,
				"price_data": {
					"currency": cur,
					"unit_amount": to_minor_units(surcharge, currency),
					"product_data": {"name": (settings.surcharge_label or "Processing fee")[:250]},
				},
			}
		)

	success_url = (
		f"{get_url(settings.success_route or '/stripe-return')}"
		f"?status=success&sp={sp.name}&session_id={{CHECKOUT_SESSION_ID}}"
	)
	cancel_url = f"{get_url(settings.cancel_route or '/stripe-return')}?status=cancel&sp={sp.name}"

	params = {
		"mode": "payment",
		"customer": stripe_customer_id,
		"client_reference_id": sp.name,
		"payment_method_types": payment_method_types,
		"line_items": line_items,
		"metadata": metadata,
		# Stripe does NOT copy metadata from the Session to its PaymentIntent, so we
		# set it on both — checkout.session.* events read the session, payment_intent.*
		# events (off-session, later) read the PI.
		"payment_intent_data": {"metadata": metadata, "description": (description or "Payment")[:250]},
		"success_url": success_url,
		"cancel_url": cancel_url,
	}

	if settings.statement_descriptor and not settings.enable_ach:
		# Suffix appears on the cardholder's statement. Card-only: ACH ignores it
		# and mixing it across methods can trip Checkout validation.
		params["payment_intent_data"]["statement_descriptor_suffix"] = settings.statement_descriptor[:22]

	if surcharge > 0 and settings.surcharge_disclosure:
		# Conspicuous pre-payment disclosure of the fee (card-network requirement).
		params["custom_text"] = {"submit": {"message": settings.surcharge_disclosure[:1200]}}

	try:
		session = create_checkout_session(params, idempotency_key=f"ee-checkout-{sp.name}")
	except Exception as exc:
		sp.db_set("status", "Failed")
		sp.db_set("error_message", error_snippet(str(exc)))
		frappe.db.commit()
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: create_checkout_session failed")
		frappe.throw(f"Could not start the Stripe payment: {error_snippet(str(exc), 200)}")

	sp.db_set("stripe_checkout_session", session["id"])
	sp.db_set("checkout_url", session["url"])
	sp.db_set("status", "Link Sent")

	if sales_invoice:
		_stamp_invoice(sales_invoice, "Link Sent", session["url"])

	frappe.db.commit()
	return {"stripe_payment": sp.name, "checkout_url": session["url"], "session_id": session["id"]}


def _resolve_target(sales_invoice, customer, amount, description, settings):
	"""Validate inputs and derive (customer, amount, currency, description)."""
	if sales_invoice:
		si = frappe.db.get_value(
			"Sales Invoice",
			sales_invoice,
			["customer", "outstanding_amount", "currency", "docstatus", "status"],
			as_dict=True,
		)
		if not si:
			frappe.throw(f"Sales Invoice {sales_invoice} not found.")
		if si.docstatus != 1:
			frappe.throw("Only a submitted Sales Invoice can be paid.")
		if flt(si.outstanding_amount) <= 0:
			frappe.throw(f"Sales Invoice {sales_invoice} has nothing outstanding to pay.")
		return si.customer, flt(si.outstanding_amount), si.currency or "USD", f"Invoice {sales_invoice}"

	# Ad-hoc path
	if not customer:
		frappe.throw("A customer is required for an ad-hoc payment.")
	if flt(amount) <= 0:
		frappe.throw("A positive amount is required.")
	currency = frappe.db.get_value("Company", settings.company, "default_currency") or "USD"
	return customer, flt(amount), currency, description or "Payment"


def _payment_method_types(settings) -> list[str]:
	"""Methods to offer in Checkout, per the settings toggles (cards by default)."""
	methods = []
	if settings.enable_card:
		methods.append("card")
	if settings.enable_ach:
		methods.append("us_bank_account")
	return methods or ["card"]


def _methods_for(settings, method) -> list[str]:
	"""Lock the session to a single method when one is chosen (so the fee matches the
	method); otherwise offer everything enabled."""
	if method == "card":
		return ["card"]
	if method == "ach":
		return ["us_bank_account"]
	return _payment_method_types(settings)


def _compute_surcharge(settings, method, base) -> float:
	"""Fee to add for the chosen method, per settings.

	Zero unless surcharge is enabled AND a specific method was chosen — we never
	surcharge an unknown method (hosted Checkout can't tell debit from credit, so a
	method must be picked first for the fee to be fair and disclosed).
	"""
	if not settings.surcharge_enabled or not method:
		return 0.0
	if method == "card":
		pct, flat = flt(settings.card_surcharge_percent), flt(settings.card_surcharge_flat)
	elif method == "ach":
		pct, flat = flt(settings.ach_fee_percent), flt(settings.ach_fee_flat)
	else:
		return 0.0
	return flt(flt(base) * pct / 100.0 + flat, 2)


def _method_hint(method) -> str | None:
	"""Map our method choice to the Stripe payment_method_type we expect."""
	return {"card": "card", "ach": "us_bank_account"}.get(method)


def _stamp_invoice(sales_invoice: str, status: str, link: str | None = None):
	"""Update the Stripe status/link back-reference fields on a Sales Invoice."""
	values = {"custom_stripe_payment_status": status}
	if link is not None:
		values["custom_stripe_payment_link"] = link
	try:
		frappe.db.set_value("Sales Invoice", sales_invoice, values, update_modified=False)
	except Exception:
		# Custom fields may not exist yet on a site that hasn't migrated; non-fatal.
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: invoice stamp failed")
