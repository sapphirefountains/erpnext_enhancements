"""Web-page controller for the card payment page at ``/pay-card``.

The card half of the customer portal. Unlike ``/pay``'s bank option — which hands off
to Stripe's hosted Checkout — this page collects the card itself, in a Stripe Payment
Element, so the card's **funding type** can be read before any amount is committed.
That is the only way to surcharge credit cards without ever touching a debit or
prepaid card (see ``stripe_payments.core.card_element``).

Authenticated, like ``/pay``: guests bounce to login, and the invoice must belong to
the logged-in user's Customer. This controller only renders the form — pricing and
charging live behind ``core.api.portal_price_card_payment`` /
``portal_confirm_card_payment``, which re-check ownership themselves. No card data
touches this server; the Element posts it straight to Stripe.

**A paid invoice, or one with a payment still settling, gets no form.** It gets
``settled`` ("paid" / "received" / "processing") and a way back to the invoice list
instead; "received" is a Stripe payment on the ledger for an invoice that still shows a
balance, which is blocked but must not be called paid. The
common way here is the phone's Back from ``/stripe-return`` straight after paying:
the page is ``no-store``, so Back downloads it again, and a card payment finished
through 3-D Secure is still "Processing" — outstanding unchanged — until the webhook
posts it. It used to show a working card form for that invoice. The verdict is
``card_element.invoice_payment_block``, the same one both RPCs enforce; an abandoned
3-D Secure attempt does not count, so such an invoice can still be paid.
"""

import frappe
from frappe.utils import flt, fmt_money

from erpnext_enhancements.stripe_payments.core.api import get_portal_customers
from erpnext_enhancements.stripe_payments.core.card_element import invoice_payment_block
from erpnext_enhancements.stripe_payments.core.utils import get_settings, is_enabled

no_cache = 1


def get_context(context):
	# Auth gate: bounce guests to login and return here afterwards (mirrors www/pay.py).
	if frappe.session.user == "Guest":
		invoice = frappe.form_dict.get("invoice") or ""
		frappe.local.flags.redirect_location = f"/login?redirect-to=/pay-card?invoice={invoice}"
		raise frappe.Redirect

	context.no_cache = 1
	context.csrf_token = frappe.sessions.get_csrf_token()

	settings = get_settings()
	context.enabled = bool(is_enabled(settings)) and bool(settings.enable_card)
	context.publishable_key = settings.publishable_key
	context.enable_ach = bool(settings.enable_ach)
	context.invoice = None
	context.settled = None

	if not context.enabled or not settings.publishable_key:
		# Without a publishable key the Element cannot mount at all; say so rather
		# than rendering a form that silently does nothing.
		context.enabled = False
		return context

	name = frappe.form_dict.get("invoice")
	if not name or not frappe.db.exists("Sales Invoice", name):
		return context

	invoice = frappe.db.get_value(
		"Sales Invoice",
		name,
		["name", "customer", "outstanding_amount", "currency", "docstatus", "is_return"],
		as_dict=True,
	)
	# Same ownership rule the RPCs enforce; failing closed here just avoids rendering
	# a form that the server would reject anyway. A return (credit note) is never the
	# customer's to pay, and its negative outstanding is not "paid" either.
	if invoice.customer not in get_portal_customers() or invoice.docstatus != 1 or invoice.is_return:
		return context
	# Only after the ownership check: whether an invoice is paid is its owner's business.
	if flt(invoice.outstanding_amount) <= 0:
		context.settled = "paid"
	else:
		block = invoice_payment_block(invoice.name)
		if block == "Paid":
			# A Stripe payment for it was received, yet it still shows a balance (its Payment
			# Entry was canceled, most often). Another card payment could be a second one, so
			# it stays blocked, as dunning and autopay block it; but "paid" would be untrue.
			context.settled = "received"
		elif block:
			context.settled = "processing"
	if context.settled:
		context.settled_invoice = invoice.name
		return context

	context.invoice = invoice
	context.amount = flt(invoice.outstanding_amount, 2)
	context.currency = invoice.currency or "USD"
	context.amount_display = fmt_money(context.amount, currency=context.currency)
	# Minor units for the Element's deferred-intent options. This figure only sizes
	# the wallet UIs; the amount actually charged is set server-side after the
	# surcharge is priced from the real card.
	context.amount_minor = int(round(context.amount * 100))

	return context
