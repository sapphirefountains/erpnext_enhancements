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
	_request,
	create_payment_intent,
	ensure_stripe_customer,
	retrieve_confirmation_token,
	retrieve_payment_intent,
)
from erpnext_enhancements.stripe_payments.core.utils import (
	error_snippet,
	get_settings,
	is_enabled,
	to_minor_units,
)

#: PaymentIntent states in which the card has not been charged, and cannot be without a
#: fresh confirmation from the page that started it. A Payment Element attempt left in one
#: of them was abandoned — most often 3-D Secure the payer never finished, because they
#: left the page. ``tasks.poll_pending`` settles only ``succeeded`` and ``canceled``, so
#: such a row would stay "Processing" for good and, read as in flight, block the invoice
#: for good. :func:`invoice_payment_block` lets the payer start again instead, and cancels
#: the abandoned PaymentIntent at Stripe first, so the two can never both charge.
ABANDONED_PI_STATES = ("requires_action", "requires_payment_method", "requires_confirmation")

MSG_ALREADY_PAID = "This invoice has already been paid."
MSG_IN_FLIGHT = "A payment for this invoice is already being processed. It will show as paid once it clears."
#: A ``Paid`` Stripe Payment on an invoice that still shows a balance — most often because
#: Accounts canceled its Payment Entry. Blocked, as dunning and autopay block it, but not
#: "paid": the ledger says the money came in, the invoice says it is owed, and a person
#: has to settle which.
MSG_ALREADY_RECEIVED = (
	"A payment for this invoice was already received, so it cannot be paid again here. "
	"Please contact us about the balance."
)
#: The same verdict for Accounts, who start hosted Checkout links from the Sales Invoice and
#: are the "us" the customer's message sends them to.
MSG_ALREADY_RECEIVED_DESK = (
	"A Stripe payment for this invoice is already Paid, yet the invoice still shows a balance "
	"(most often because its Payment Entry was canceled). Settle that before taking another payment."
)
MSG_AMOUNT_CHANGED = "The amount due on this invoice has changed. Continue again to see the new total."


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
	# Before the card is even read: an invoice already paid, or with a payment still
	# settling, must never get a second quote. Reached by the phone's Back from
	# /stripe-return, or a second tab (www/pay_card.py shows the same verdict up front).
	_refuse_if_in_flight(sales_invoice)

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
	if sp.get("sales_invoice"):
		# The quote was priced against the invoice as it stood then. Re-checked here, where
		# the money actually moves: another tab, a bank payment or Accounts may have paid it
		# since, and each of those is a second payment for one invoice. Paid in part counts
		# too (a cheque, a credit note): an invoice quote is always the whole outstanding
		# (_resolve_target), so charging it now would leave the difference as an overpayment.
		outstanding = flt(frappe.db.get_value("Sales Invoice", sp.sales_invoice, "outstanding_amount"), 2)
		if outstanding <= 0:
			frappe.throw(MSG_ALREADY_PAID)
		if outstanding != flt(sp.amount, 2):
			frappe.throw(MSG_AMOUNT_CHANGED)
		_refuse_if_in_flight(sp.sales_invoice, exclude=sp.name)

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
		# The card is charged. Say so durably before posting: if the Payment Entry fails
		# (no deposit account, a closed period), the row must not stay Draft with the PI
		# rolled back off it, invisible to every in-flight guard while the page offers Pay
		# again. As "Processing" it blocks a second charge, and poll_pending retries the post.
		sp.db_set("status", "Processing")
		if sp.sales_invoice:
			_stamp_invoice(sp.sales_invoice, "Processing")
		frappe.db.commit()
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


def invoice_payment_block(sales_invoice: str, *, exclude: str | None = None, release: bool = False):
	"""``"Paid"`` or ``"Processing"`` when a new payment for this invoice — card or bank —
	must not start, or ``None`` when it may.

	The invoice-level guard the portal lacked: ``_resolve_target`` checks only what is
	outstanding, and ``confirm_card_payment`` only its own row, so a card payment still
	settling after 3-D Secure — the invoice unposted until the webhook lands — offered a
	working card form again to anyone who pressed Back from /stripe-return, and a /pay tab
	opened before it could still start a bank Checkout. It reads the
	ledger the way ``dunning`` and ``saved_methods`` do (a ``Processing`` or ``Paid``
	Stripe Payment for the invoice) with one difference. A Payment Element attempt — the
	only kind that carries a ConfirmationToken, and the only kind whose 3-D Secure runs in
	our page, where a Back can abandon it — is asked of Stripe. When its PaymentIntent is
	in :data:`ABANDONED_PI_STATES` (or already canceled) it does not block. Without that,
	an invoice whose 3-D Secure was abandoned could never be paid by card again. Anything
	that cannot be settled that way blocks: hosted Checkout, ACH and off-session charges
	settle on their own, and a Stripe lookup that fails is not proof that nothing charged.
	Those rows are never looked up and never canceled — an ACH debit waiting on microdeposit
	verification sits in ``requires_action`` for days, and is a payment, not an abandoned one.

	Every path that starts a payment for an invoice runs it once, through
	:func:`_refuse_if_in_flight` with ``release``: ``price_card_payment``,
	``confirm_card_payment`` and ``checkout.create_payment`` (the Bank button on /pay and the
	desk's "Pay with Stripe"). ``www/pay_card.py`` reads it, and ``www/pay.py`` its list form
	:func:`invoice_payment_blocks`, without — a page render starts nothing.

	``exclude`` is the row being confirmed. ``release`` (the POSTs, never the page
	render) cancels each abandoned PaymentIntent at Stripe and fails its row before saying
	``None``: a PaymentIntent that succeeded in the meantime cannot be canceled, so the
	old attempt and the new one can never both charge.
	"""
	if not sales_invoice:
		return None
	rows = [
		row
		for row in frappe.get_all(
			"Stripe Payment",
			filters={"sales_invoice": sales_invoice, "status": ["in", ["Processing", "Paid"]]},
			fields=["name", "status", "stripe_payment_intent", "confirmation_token"],
		)
		if row.name != exclude
	]
	return _block_from_rows(sales_invoice, rows, release)


def invoice_payment_blocks(sales_invoices) -> dict:
	""":func:`invoice_payment_block` for every invoice a page lists: ``{invoice: verdict}``,
	holding only the blocked ones.

	For ``www/pay.py``, which decides from it which invoices get Card and Bank buttons — so
	the list offers exactly what the endpoints behind those buttons will accept, rather than
	trusting the invoice's ``custom_stripe_payment_status`` stamp, which nothing clears when
	a 3-D Secure attempt is abandoned or fails. One ledger query for the lot, and Stripe is
	asked only about the rows the single form would ask about. Never releases.
	"""
	names = [name for name in sales_invoices or () if name]
	if not names:
		return {}
	rows_by_invoice = {}
	for row in frappe.get_all(
		"Stripe Payment",
		filters={"sales_invoice": ["in", names], "status": ["in", ["Processing", "Paid"]]},
		fields=["name", "sales_invoice", "status", "stripe_payment_intent", "confirmation_token"],
	):
		rows_by_invoice.setdefault(row.sales_invoice, []).append(row)
	verdicts = {}
	for sales_invoice, rows in rows_by_invoice.items():
		block = _block_from_rows(sales_invoice, rows, release=False)
		if block:
			verdicts[sales_invoice] = block
	return verdicts


def _block_from_rows(sales_invoice, rows, release):
	"""The verdict for one invoice's ``Processing`` / ``Paid`` Stripe Payment rows."""
	if any(row.status == "Paid" for row in rows):
		return "Paid"
	# Anything but a Payment Element attempt settles on its own and is waited on — never
	# looked up, never canceled. Checked before any card attempt is asked about, so a
	# payment in flight costs no call to Stripe and no card attempt is released beside it.
	if any(not (row.confirmation_token and row.stripe_payment_intent) for row in rows):
		return "Processing"

	abandoned = []
	for row in rows:
		pi_status = _payment_intent_status(row.stripe_payment_intent)
		if pi_status != "canceled" and pi_status not in ABANDONED_PI_STATES:
			# succeeded (the webhook has not posted it yet), processing, or unknown.
			return "Processing"
		abandoned.append((row, pi_status))

	if release:
		for row, pi_status in abandoned:
			if not _release_abandoned(row, pi_status):
				return "Processing"
		if abandoned:
			# The abandoned attempt stamped the invoice "Processing", which hides its pay
			# buttons on /pay. That is no longer true of anything.
			_stamp_invoice(sales_invoice, "Unpaid")
			frappe.db.commit()
	return None


def _refuse_if_in_flight(sales_invoice, exclude=None, desk=False):
	# Every caller has already refused an invoice with nothing outstanding (_resolve_target,
	# or confirm_card_payment's own re-read), so a "Paid" verdict here is a received payment
	# on an invoice that still shows a balance. ``desk`` words that one for Accounts.
	block = invoice_payment_block(sales_invoice, exclude=exclude, release=True)
	if block == "Paid":
		frappe.throw(MSG_ALREADY_RECEIVED_DESK if desk else MSG_ALREADY_RECEIVED)
	if block:
		frappe.throw(MSG_IN_FLIGHT)


def _payment_intent_status(payment_intent_id):
	"""The PaymentIntent's status at Stripe, or ``None`` when it cannot be read (which
	every caller treats as "still in flight")."""
	try:
		return (retrieve_payment_intent(payment_intent_id) or {}).get("status")
	except Exception:
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: card attempt status lookup failed")
		return None


def _cancel_payment_intent(payment_intent_id):
	"""Cancel a PaymentIntent at Stripe (``POST /v1/payment_intents/:id/cancel``).

	Through the client's own request helper: this module still never touches HTTP, and
	there is still no Stripe SDK. No idempotency key, on purpose: Stripe refuses to cancel
	a PaymentIntent twice, so a key would add no safety, and it would replay a transient
	error for 24 hours. Unkeyed, the next attempt reads the PaymentIntent first and skips
	the cancel once it shows ``canceled``.
	"""
	return _request(
		"POST",
		f"/payment_intents/{payment_intent_id}/cancel",
		data={"cancellation_reason": "abandoned"},
	)


def _release_abandoned(row, pi_status) -> bool:
	"""Make an abandoned card attempt provably dead: cancel its PaymentIntent and fail its
	row. ``False`` — so the caller keeps blocking — whenever that is not certain."""
	if pi_status != "canceled":
		try:
			canceled = _cancel_payment_intent(row.stripe_payment_intent) or {}
		except Exception:
			# Most likely the payer finished 3-D Secure in another tab a moment ago, and a
			# succeeded PaymentIntent cannot be canceled. Either way: not provably dead.
			frappe.log_error(
				error_snippet(frappe.get_traceback()), "Stripe: abandoned card attempt not canceled"
			)
			return False
		if canceled.get("status") != "canceled":
			return False
	frappe.db.set_value(
		"Stripe Payment",
		row.name,
		{
			"status": "Failed",
			"error_message": "Abandoned before it was completed (3-D Secure never finished). "
			"Canceled at Stripe when a new card payment for the invoice was started.",
		},
	)
	frappe.db.commit()
	return True


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
