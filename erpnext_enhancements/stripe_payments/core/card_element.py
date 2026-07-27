# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Card payments collected on our own page, priced from the real card's funding type.

Hosted Checkout (``checkout.create_payment``) fixes its line items when the Session
is created — before the payer's card, and therefore its funding type, exists. Since
only **credit** cards may be surcharged, that path can never price a fee: it would be
guessing, and guessing wrong on a debit card is a flat card-network violation.

This module is the way around that, using Stripe's ConfirmationToken two-step
confirmation (all GA API — no preview version, no third-party surcharge app):

1. The page mounts a Payment Element in *deferred intent* mode. No PaymentIntent
   exists yet, so no amount is committed.
2. The payer enters their card and submits. Stripe.js returns a **ConfirmationToken**
   whose ``payment_method_preview`` we retrieve server-side — it carries the method
   ``type`` and, for cards, ``card.funding``.
3. :func:`price_card_payment` reads that funding type, prices the surcharge through
   the one shared gate (``checkout._compute_surcharge``), and records the quote.
4. The page shows the payer the **true total** and what, if anything, the fee is.
   They confirm or go back and use a different card — which is exactly the disclosure
   and opt-out the card networks require.
5. :func:`confirm_card_payment` creates and confirms the PaymentIntent for that total.

Reconciliation is unchanged: the PaymentIntent carries the usual metadata, and the
``payment_intent.succeeded`` webhook posts the Payment Entry and the companion
surcharge Journal Entry exactly as it does for every other path.

**ACH is deliberately not handled here.** A bank debit has no funding type to detect
and never carries a fee, so the Element buys it nothing — while costing Financial
Connections, microdeposit fallback and Nacha mandate collection that hosted Checkout
already does correctly. Bank payments stay on ``checkout.create_payment``.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, fmt_money, get_url

from erpnext_enhancements.stripe_payments.core.checkout import (
	_compute_surcharge,
	_resolve_target,
	_stamp_invoice,
)
from erpnext_enhancements.stripe_payments.core.client import (
	create_payment_intent,
	ensure_stripe_customer,
	retrieve_confirmation_token,
)
from erpnext_enhancements.stripe_payments.core.utils import (
	error_snippet,
	get_settings,
	is_enabled,
	to_minor_units,
)


def price_card_payment(
	*,
	confirmation_token: str,
	sales_invoice: str | None = None,
	customer: str | None = None,
	amount=None,
	description: str | None = None,
	channel: str = "Portal",
) -> dict:
	"""Quote the true total for a card we can now actually see. No money moves here.

	Reads the card's funding type off the ConfirmationToken, prices the surcharge, and
	records a ``Stripe Payment`` holding both the quote and the token it was quoted
	against. Returns the breakdown for the page's confirmation step.

	Binding the token to the row is the security crux: :func:`confirm_card_payment`
	accepts only this exact token, so a client cannot get a quote with a credit card
	and then pay with a debit one (or the reverse — quoting on debit to get a zero fee
	and charging a credit card). One token, one price, one charge.

	The caller is responsible for permission checks.
	"""
	settings = get_settings()
	if not is_enabled(settings):
		frappe.throw("Stripe Payments is not enabled.")
	if not settings.enable_card:
		frappe.throw("Card payments are not enabled.")

	customer, amount, currency, description = _resolve_target(
		sales_invoice, customer, amount, description, settings
	)

	pm_type, funding = _confirmation_token_method(confirmation_token)
	if pm_type != "card":
		# This endpoint prices a card surcharge; anything else belongs on the hosted
		# path, where it is fee-free. Refuse rather than silently charge zero here.
		frappe.throw("This page accepts card payments only. Use the bank (ACH) option instead.")

	surcharge = _compute_surcharge(settings, pm_type=pm_type, funding=funding, base=amount)
	stripe_customer_id = ensure_stripe_customer(customer, settings)

	# One row per quote. If the payer goes back and tries a different card they get a
	# fresh row bound to the new token, and this one is left in Draft — harmless,
	# since a Draft never posts anything and is excluded from every in-flight guard.
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
			"payment_method_type": pm_type,
			"card_funding": funding,
			"confirmation_token": confirmation_token,
			"status": "Draft",
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()

	total = flt(flt(amount) + surcharge, 2)
	return {
		"stripe_payment": sp.name,
		"currency": currency,
		"amount": flt(amount, 2),
		"surcharge": surcharge,
		"total": total,
		"card_funding": funding,
		# Formatted server-side with the same fmt_money the rest of the portal uses,
		# so the figures the payer approves are rendered by one code path.
		"amount_display": fmt_money(amount, currency=currency),
		"surcharge_display": fmt_money(surcharge, currency=currency),
		"total_display": fmt_money(total, currency=currency),
		# Present only when a fee actually applies — a debit payer must never be told
		# that "a processing fee applies".
		"surcharge_label": (settings.surcharge_label or "Credit card processing fee") if surcharge else None,
		"surcharge_disclosure": (settings.surcharge_disclosure or None) if surcharge else None,
	}


def confirm_card_payment(*, stripe_payment: str, confirmation_token: str) -> dict:
	"""Charge the quoted total for a priced ``Stripe Payment``.

	Accepts only the ConfirmationToken the quote was made against (see
	:func:`price_card_payment`), so the card that was priced is the card that pays.
	Returns ``{"status", "client_secret", "requires_action"}``; on ``requires_action``
	the page runs 3-D Secure with ``stripe.handleNextAction`` and the webhook finalises.

	The caller is responsible for permission checks.
	"""
	settings = get_settings()
	if not is_enabled(settings):
		frappe.throw("Stripe Payments is not enabled.")

	sp = frappe.get_doc("Stripe Payment", stripe_payment)
	if sp.status not in ("Draft", "Failed"):
		# Already charged (or settling) — never start a second PaymentIntent for it.
		frappe.throw(f"This payment is already {sp.status}.")
	if not sp.confirmation_token or sp.confirmation_token != confirmation_token:
		frappe.throw("This payment was quoted for a different card. Start the payment again.")

	total = flt(flt(sp.amount) + flt(sp.surcharge_amount), 2)
	metadata = {
		"erpnext_customer": sp.customer,
		"stripe_payment": sp.name,
		"source": sp.channel or "Portal",
	}
	if sp.sales_invoice:
		metadata["erpnext_invoice"] = sp.sales_invoice

	params = {
		"amount": to_minor_units(total, sp.currency),
		"currency": (sp.currency or "USD").lower(),
		"customer": sp.stripe_customer_id,
		"confirmation_token": confirmation_token,
		"confirm": True,
		# The method is carried by the ConfirmationToken itself. We deliberately do
		# not also pin payment_method_types: the token is already proven to be a card
		# (price_card_payment rejects anything else) and is bound to this row, so
		# pinning would add no guarantee while risking a parameter conflict that would
		# fail every card payment.
		"return_url": f"{get_url(settings.success_route or '/stripe-return')}"
		f"?status=success&sp={sp.name}",
		"description": (sp.description or "Payment")[:250],
		"metadata": metadata,
	}
	if settings.statement_descriptor:
		# Safe to set unconditionally here, unlike the hosted path: this PaymentIntent
		# is always a card, and the suffix is only problematic when mixed with ACH.
		params["statement_descriptor_suffix"] = settings.statement_descriptor[:22]

	try:
		# Keyed on the ledger row, so a double-click or a retried request can never
		# create a second PaymentIntent for the same quote.
		pi = create_payment_intent(params, idempotency_key=f"ee-element-{sp.name}")
	except Exception as exc:
		sp.db_set("status", "Failed")
		sp.db_set("error_message", error_snippet(str(exc)))
		frappe.db.commit()
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: card element confirm failed")
		frappe.throw(f"The payment could not be processed: {error_snippet(str(exc), 200)}")

	sp.db_set("stripe_payment_intent", pi.get("id"))
	status = pi.get("status")

	if status == "succeeded":
		# Post the Payment Entry now; the webhook is a dedupe-protected backstop.
		from erpnext_enhancements.stripe_payments.core import reconcile

		reconcile.finalize_payment(sp, pi)
	elif status in ("processing", "requires_action", "requires_confirmation"):
		sp.db_set("status", "Processing")
		if sp.sales_invoice:
			_stamp_invoice(sp.sales_invoice, "Processing")
	else:
		sp.db_set("status", "Failed")
		sp.db_set("error_message", f"PaymentIntent status: {status}")

	frappe.db.commit()
	sp.reload()
	return {
		"stripe_payment": sp.name,
		"status": sp.status,
		"payment_intent": pi.get("id"),
		"requires_action": status == "requires_action",
		# Only needed to complete 3-D Secure in the browser; it authorises nothing
		# beyond this PaymentIntent.
		"client_secret": pi.get("client_secret") if status == "requires_action" else None,
	}


def _confirmation_token_method(confirmation_token: str):
	"""``(type, card funding)`` from a ConfirmationToken's ``payment_method_preview``.

	Unlike the off-session lookup, a failure here is fatal rather than best-effort: if
	we cannot read the funding type we cannot price the payment at all, and silently
	falling back to "no fee" would let a bad token dictate the price.
	"""
	if not confirmation_token:
		frappe.throw("No payment details were submitted. Please try again.")
	try:
		token = retrieve_confirmation_token(confirmation_token)
	except Exception as exc:
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: confirmation token lookup failed")
		frappe.throw(f"Could not read the payment details: {error_snippet(str(exc), 200)}")

	preview = token.get("payment_method_preview") or {}
	return preview.get("type"), (preview.get("card") or {}).get("funding")
