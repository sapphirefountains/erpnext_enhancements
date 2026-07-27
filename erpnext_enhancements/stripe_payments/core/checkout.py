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

# Stripe ``card.funding`` values that may never carry a surcharge. Debit and prepaid
# are banned outright by the card networks (no cost-of-acceptance exception, unlike
# credit, which is merely capped), and "unknown" means Stripe could not classify the
# card — we fail toward not charging. Used by the reconciler to void a fee that was
# collected from a card that turned out not to be credit.
NON_SURCHARGEABLE_FUNDING = frozenset({"debit", "prepaid", "unknown"})


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
	``method`` ("card"/"ach") locks the session to one method; when omitted, all
	enabled methods are offered. **No session created here ever carries a surcharge**
	— see the note at the ``_compute_surcharge`` call below. Returns
	``{"stripe_payment", "checkout_url", "session_id"}``. The caller is responsible
	for permission checks (operator role for desk; ownership for portal).
	"""
	settings = get_settings()
	if not is_enabled(settings):
		frappe.throw("Stripe Payments is not enabled. Turn it on in Stripe Payments Settings.")

	customer, amount, currency, description = _resolve_target(
		sales_invoice, customer, amount, description, settings
	)

	payment_method_types = _methods_for(settings, method)
	# Always 0 on this path, by construction: hosted Checkout fixes its line items
	# when the Session is created, which is before the payer's card — and therefore
	# its funding type — exists. Only credit cards may be surcharged, so funding is
	# unknowable here and ``_compute_surcharge`` fails toward not charging. Priced
	# through the same gate anyway so this path can never drift from the policy.
	surcharge = _compute_surcharge(settings, pm_type=_method_hint(method), funding=None, base=amount)
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
	"""Lock the session to a single method when one is chosen; otherwise offer
	everything enabled."""
	if method == "card":
		return ["card"]
	if method == "ach":
		return ["us_bank_account"]
	return _payment_method_types(settings)


def _compute_surcharge(settings, *, pm_type=None, funding=None, base=0) -> float:
	"""The fee to add for a payment. Zero unless every gate passes.

	This is the single chokepoint every path prices through, and it returns a fee
	for exactly one case — a **credit card**:

	* ``pm_type`` must be Stripe's ``"card"``. ``us_bank_account`` (ACH) can never
	  carry a fee: there is no ACH branch and no ACH fee setting to misconfigure.
	* ``funding`` must be exactly ``"credit"``. Surcharging debit or prepaid is a
	  flat card-network violation with no cost-of-acceptance exception, and
	  ``"unknown"`` (Stripe could not classify the card) or ``None`` (funding not
	  determined yet, or a lookup that failed) fail toward not charging.

	``funding`` is only knowable once the payer's card is in hand. Callers that
	price *before* that — hosted Checkout, which fixes its line items when the
	Session is created — pass ``None`` and therefore always get 0. The funding-aware
	callers are the off-session charge (which reads the saved PaymentMethod first)
	and the Payment Element flow. See docs/stripe_surcharging_compliance.md.
	"""
	if not settings.surcharge_enabled:
		return 0.0
	if pm_type != "card" or funding != "credit":
		return 0.0
	pct, flat = flt(settings.card_surcharge_percent), flt(settings.card_surcharge_flat)
	return flt(flt(base) * pct / 100.0 + flat, 2)


def surcharge_cap_error(percent, flat, cost_percent, cost_flat) -> str | None:
	"""The reason a surcharge configuration is not allowed, or None if it is (pure).

	US network rules cap a surcharge at **3% or your cost of acceptance, whichever is
	lower**. Comparing a percent-plus-flat surcharge against a percent-plus-flat cost
	is amount-dependent, so this enforces it componentwise: percent ≤ cost percent
	AND flat ≤ cost flat. That is provably within cost at *every* invoice total —
	``pct·X + flat ≤ cost_pct·X + cost_flat`` holds for all ``X ≥ 0`` — without
	needing to know the amount in advance.

	A flat 3% ceiling is not sufficient on its own: against a cost of 2.9% + $0.30, a
	3% surcharge exceeds cost on any invoice over ~$300. Split out from the Settings
	controller so the policy is unit-tested without a bench, like ``_surcharge_je_legs``.
	"""
	percent, flat = flt(percent), flt(flat)
	cost_percent, cost_flat = flt(cost_percent), flt(cost_flat)

	if percent > 3:
		return (
			"Card Surcharge % cannot exceed the US network cap of 3%. "
			"Some states require less — see docs/stripe_surcharging_compliance.md."
		)
	if not cost_percent and not cost_flat:
		return (
			"Set your Cost of Acceptance before enabling surcharging — a surcharge must "
			"never exceed it, and it cannot be validated while it is blank."
		)
	if percent > cost_percent:
		return (
			f"Card Surcharge % ({percent:g}%) exceeds your cost of acceptance "
			f"({cost_percent:g}%). Network rules cap the surcharge at whichever is lower."
		)
	if flat > cost_flat:
		return (
			f"Card Surcharge (flat) of {flat:g} exceeds the flat component of your cost of "
			f"acceptance ({cost_flat:g}). Network rules cap the surcharge at whichever is lower."
		)
	return None


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
