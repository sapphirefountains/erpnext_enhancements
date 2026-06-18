"""Web-page controller for the Stripe Checkout return/landing page at
``/stripe-return``.

Stripe redirects the payer here via ``success_url``/``cancel_url`` after the hosted
Checkout. Intentionally public: a customer who paid via an emailed/texted link is
not logged in. It only shows friendly status messaging and never posts the payment —
the signed webhook is the single source of truth for recording the Payment Entry.
"""

import frappe

no_cache = 1


def get_context(context):
	context.no_cache = 1
	context.outcome = frappe.form_dict.get("status") or "success"

	# Best-effort, non-sensitive: surface the ledger row's current status if present.
	context.payment_status = None
	sp = frappe.form_dict.get("sp")
	if sp and frappe.db.exists("Stripe Payment", sp):
		context.payment_status = frappe.db.get_value("Stripe Payment", sp, "status")

	return context
