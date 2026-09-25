"""Web-page controller for the Stripe Checkout return/landing page at
``/stripe-return``.

Stripe redirects the payer here via ``success_url``/``cancel_url`` after the hosted
Checkout. Intentionally public: a customer who paid via an emailed/texted link is
not logged in. It only shows friendly status messaging and never posts the payment —
the signed webhook is the single source of truth for recording the Payment Entry.

What it says comes from the ledger row as it is **now**, not from the ``status`` in the
address, so a revisit tells the truth: received, being processed, not yet confirmed (the
card page's charge that Stripe never answered — the payer is told not to pay again for now,
and that if it did not go through they will be emailed or will find the invoice ready to pay
again), or did not go through. Only the row's status and whether its outcome is known are
read; nothing else about it is shown.

"Not yet confirmed" is ``card_element.held_unknown`` while the row is still Processing: a
charge whose PaymentIntent is still unknown, **or** one poll_pending has since found at
Stripe but not seen complete (``NOTE_OUTCOME_FOUND`` — 3-D Secure the payer never saw, say,
which is released half an hour later with an email that nothing was charged). The page's own
test of "no PaymentIntent and no Session" turned that second kind into "Thank you! Your
payment is being processed" the moment poll_pending recorded the PaymentIntent.

The promise is worded for both ways the payer can learn it did not go through. They are
emailed — except when it is their own new payment for the invoice that releases the old
attempt (``card_element._notify_not_charged``: an email saying "did not go through" as they
pay again could be taken for the new payment). They can only have started that payment from
the invoice shown ready to pay again, so the page says that is the other way they may find
out.
"""

import frappe

from erpnext_enhancements.stripe_payments.core.card_element import held_unknown

no_cache = 1


def get_context(context):
	context.no_cache = 1
	context.outcome = frappe.form_dict.get("status") or "success"

	# Best-effort, non-sensitive: surface the ledger row's current status if present.
	context.payment_status = None
	context.unconfirmed = False
	sp = frappe.form_dict.get("sp")
	if sp and frappe.db.exists("Stripe Payment", sp):
		row = frappe.db.get_value(
			"Stripe Payment",
			sp,
			["status", "stripe_payment_intent", "stripe_checkout_session", "error_message"],
			as_dict=True,
		)
		if row:
			context.payment_status = row.status
			# Written ahead as Processing and Stripe never said whether it charged, or found at
			# Stripe but not yet seen to complete. Not "being processed" — that promises what
			# nobody knows yet.
			context.unconfirmed = bool(row.status == "Processing" and held_unknown(row))

	return context
