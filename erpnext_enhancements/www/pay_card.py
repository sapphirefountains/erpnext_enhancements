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
``settled`` ("paid" / "nothing" / "received" / "processing" / "awaiting" /
"unconfirmed" / "verifying" / "unreleased") and a way back to the invoice list instead.
"paid" only when ERPNext's own status says Paid: an invoice brought to zero by a credit
note was credited, not paid, and gets "nothing left to pay". "received" is a Stripe payment on the ledger for an invoice
that still shows a balance, which is blocked but must not be called paid. Only
"processing" — a payment known to be settling — promises to show as paid once it clears:
"awaiting" is a card payment waiting on the payer's bank approval (3-D Secure), which may
never be finished, so it says instead when the invoice can be paid again
(``awaiting_minutes``); "unconfirmed" is a charge nobody has an answer for yet;
"verifying" is a bank debit waiting on the customer to verify their bank account, which goes
ahead only once they do; and "unreleased" is an off-session card challenge Stripe would not
confirm canceled, which will never clear (poll_pending cancels it). The
common way here is the phone's Back from ``/stripe-return`` straight after paying:
the page is ``no-store``, so Back downloads it again, and a card payment finished
through 3-D Secure is still "Processing" — outstanding unchanged — until the webhook
posts it. It used to show a working card form for that invoice. The verdict is
``card_element.invoice_payment_block``, the same one both RPCs enforce; a card attempt
that can no longer charge does not count, so such an invoice can still be paid.

The render never waits long on Stripe: each lookup gets ``card_element.READ_TIMEOUT``
seconds, and one that does not answer reads as "unconfirmed" — blocked, the safe answer —
while ``tasks.poll_pending`` settles the attempt in the background.
"""

import frappe
from frappe.utils import flt, fmt_money

from erpnext_enhancements.stripe_payments.core.api import get_portal_customers
from erpnext_enhancements.stripe_payments.core.card_element import (
	AWAITING,
	PAID,
	UNCONFIRMED,
	UNRELEASED,
	VERIFYING,
	awaiting_minutes,
	invoice_payment_block,
)
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
		["name", "customer", "outstanding_amount", "currency", "docstatus", "is_return", "status"],
		as_dict=True,
	)
	# Same ownership rule the RPCs enforce; failing closed here just avoids rendering
	# a form that the server would reject anyway. A return (credit note) is never the
	# customer's to pay, and its negative outstanding is not "paid" either.
	if invoice.customer not in get_portal_customers() or invoice.docstatus != 1 or invoice.is_return:
		return context
	# Only after the ownership check: whether an invoice is paid is its owner's business.
	if flt(invoice.outstanding_amount) <= 0:
		# "Paid" is ERPNext's word, not ours: a zero balance from a credit note ("Credit
		# Note Issued") was never paid, and saying so would be untrue.
		context.settled = "paid" if invoice.status == "Paid" else "nothing"
	else:
		block = invoice_payment_block(invoice.name)
		if block == PAID:
			# A Stripe payment for it was received, yet it still shows a balance (its Payment
			# Entry was canceled, most often). Another card payment could be a second one, so
			# it stays blocked, as dunning and autopay block it; but "paid" would be untrue.
			context.settled = "received"
		elif block == AWAITING:
			context.settled = "awaiting"
			context.awaiting_minutes = awaiting_minutes(block)
		elif block == UNCONFIRMED:
			context.settled = "unconfirmed"
		elif block == VERIFYING:
			context.settled = "verifying"
		elif block == UNRELEASED:
			# Only an off-session card challenge reaches a render this way: a card-page attempt
			# the POSTs could not release reads as not blocking here (a render never releases),
			# and the POST refusing it says so beside the form instead (AttemptUnreleased).
			context.settled = "unreleased"
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
