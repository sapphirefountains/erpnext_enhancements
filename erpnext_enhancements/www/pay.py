"""Web-page controller for the customer self-service payment portal at ``/pay``.

Authenticated (Customer-role users only; guests bounce to login). Lists the logged-
in customer's outstanding Sales Invoices and lets them start a hosted Stripe
Checkout for any one of them. The actual session creation + ownership check live in
``stripe_payments.core.api.portal_create_payment``; this controller only builds the
list and the CSRF token the page's fetch needs. Mirrors the auth gate in
``www/kiosk.py``.
"""

import frappe
from frappe.utils import flt, fmt_money, formatdate

from erpnext_enhancements.stripe_payments.core.api import get_portal_customers
from erpnext_enhancements.stripe_payments.core.utils import get_settings, is_enabled

no_cache = 1


def get_context(context):
	# Auth gate: bounce guests to login and return here afterwards.
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/pay"
		raise frappe.Redirect

	context.no_cache = 1
	context.csrf_token = frappe.sessions.get_csrf_token()

	settings = get_settings()
	context.enabled = bool(is_enabled(settings))

	context.invoices = []
	customers = get_portal_customers()
	if context.enabled and customers:
		invoices = frappe.get_all(
			"Sales Invoice",
			filters={"customer": ["in", customers], "docstatus": 1, "outstanding_amount": [">", 0]},
			fields=[
				"name",
				"posting_date",
				"due_date",
				"outstanding_amount",
				"currency",
				"custom_stripe_payment_status",
			],
			order_by="due_date asc",
			limit_page_length=200,
		)
		for inv in invoices:
			inv["amount_display"] = fmt_money(inv["outstanding_amount"], currency=inv["currency"])
			inv["due_display"] = formatdate(inv["due_date"]) if inv["due_date"] else "—"
		context.invoices = invoices

	# Surcharge prompts (method-first) for the portal buttons.
	context.surcharge_enabled = bool(settings.surcharge_enabled)
	context.enable_card = bool(settings.enable_card)
	context.enable_ach = bool(settings.enable_ach)
	context.card_fee_label = _fee_label(settings, "card")
	context.ach_fee_label = _fee_label(settings, "ach")

	# Autopay / saved-method (Phase 2): consent text + current enrollment state.
	context.autopay_consent = settings.autopay_consent
	context.autopay_enrolled = False
	context.autopay_label = None
	for cust in customers:
		if frappe.db.get_value("Customer", cust, "custom_stripe_autopay_enabled"):
			context.autopay_enrolled = True
			context.autopay_label = frappe.db.get_value("Customer", cust, "custom_stripe_payment_method_label")
			break

	return context


def _fee_label(settings, method):
	"""Human label like ' (+3% fee)' for a method, or '' when no surcharge applies."""
	if not settings.surcharge_enabled:
		return ""
	pct = settings.card_surcharge_percent if method == "card" else settings.ach_fee_percent
	flat = settings.card_surcharge_flat if method == "card" else settings.ach_fee_flat
	currency = frappe.db.get_value("Company", settings.company, "default_currency") or "USD"
	parts = []
	if flt(pct):
		parts.append(f"{flt(pct):g}%")
	if flt(flat):
		parts.append(fmt_money(flat, currency=currency))
	return f" (+{' + '.join(parts)} fee)" if parts else ""
