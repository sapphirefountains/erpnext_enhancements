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
"""

import frappe
from frappe.utils import flt, fmt_money

from erpnext_enhancements.stripe_payments.core.api import get_portal_customers
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
		["name", "customer", "outstanding_amount", "currency", "docstatus"],
		as_dict=True,
	)
	# Same ownership rule the RPCs enforce; failing closed here just avoids rendering
	# a form that the server would reject anyway.
	if invoice.customer not in get_portal_customers() or invoice.docstatus != 1:
		return context
	if flt(invoice.outstanding_amount) <= 0:
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
