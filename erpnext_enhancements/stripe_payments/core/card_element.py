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

**This module is also where "one invoice is never paid twice" lives**, for every path
that can take money for an invoice — the card page, hosted Checkout (the Bank button
and the desk's "Pay with Stripe"), and the off-session charge (desk, autopay,
dunning). The rules, each enforced by a function below:

* one **invoice lock** (:func:`invoice_lock`) is held from the guard until the new
  attempt is committed, so two tabs holding quotes cannot both pass the guard;
* the **guard** (:func:`invoice_payment_block`) refuses while any Stripe payment for
  the invoice — or for an invoice it was amended from — is paid, settling, or might
  still be charging;
* the **Sales Invoice row lock** is taken just before the new attempt is committed
  (:func:`_recheck_invoice` with ``for_update``), so an invoice being canceled right
  then is seen canceled, and the cancel (:func:`before_invoice_cancel`) sees the attempt;
* a charge is **written ahead**: its row is committed ``Processing`` *before* Stripe is
  called, so a timeout, a 5xx or a killed worker always leaves a row that blocks;
* a Stripe answer is **definite** only when it proves nothing was charged
  (``client.is_definite_failure``). Anything else is retried once with the same
  idempotency key, then left ``Processing`` for ``tasks.poll_pending`` to find at
  Stripe (:func:`resolve_unknown_outcome`) — never treated as a decline;
* an emailed Checkout link still open for the invoice is **expired at Stripe** before a
  payment the customer or Accounts start, and a completed one refuses it
  (:func:`_close_open_checkouts`); autopay and dunning leave the link alone and wait;
* 3-D Secure the payer may still be finishing is never canceled inside
  :data:`ABANDON_AFTER_MINUTES`;
* an invoice is never **canceled** while a Stripe payment for it is in flight
  (:func:`before_invoice_cancel`), since its amended copy would carry a new name.
"""

from __future__ import annotations

import calendar
import contextlib
import datetime
import hashlib
import html
import math
import re
import types

import frappe
from frappe.utils import cint, flt, fmt_money, get_datetime, get_url, now_datetime

from erpnext_enhancements.stripe_payments.core.checkout import (
	_compute_surcharge,
	_resolve_target,
	_stamp_invoice,
)
from erpnext_enhancements.stripe_payments.core.client import (
	StripeError,
	_request,
	create_payment_intent,
	ensure_stripe_customer,
	expire_checkout_session,
	is_definite_failure,
	is_missing,
	list_payment_intents,
	retrieve_checkout_session,
	retrieve_confirmation_token,
	retrieve_payment_intent,
)
from erpnext_enhancements.stripe_payments.core.utils import (
	error_snippet,
	get_settings,
	is_enabled,
	to_minor_units,
)

#: How long 3-D Secure may sit unfinished before the attempt counts as abandoned. The
#: payer may be approving in their bank's app, waiting on an SMS code, or on a second
#: device; inside this window the attempt is never canceled and the invoice cannot be paid
#: again (owner's decision, 2026-09-24). The customer is told the payment is waiting for
#: their bank's approval and when it can be paid again (:data:`MSG_AWAITING`; ``/pay``
#: shows "Waiting for bank approval…"); the desk's invoice stamp reads Processing. Measured
#: from the row's last change, which for a card attempt is the moment it went
#: ``requires_action``.
ABANDON_AFTER_MINUTES = 30
#: The PaymentIntent state of 3-D Secure in progress.
AWAITING_PAYER_PI_STATE = "requires_action"
#: PaymentIntent states that can never charge without a fresh confirmation from the page
#: that started them, which ours never sends: authentication failed or the card was
#: declined after it (``requires_payment_method``), never confirmed
#: (``requires_confirmation``), or already canceled. Released at once.
DEAD_PI_STATES = ("requires_payment_method", "requires_confirmation", "canceled")
#: What a status lookup reports when Stripe answers 404 — no such PaymentIntent for the
#: configured key (made in the other mode, or another account). It can never charge through
#: this account, and asking again gets the same answer for ever.
MISSING_PI_STATE = "missing"
#: PaymentIntent states in which the money is taken or on its way: an attempt read back in
#: one of these after a refused cancel is settling, and will show as paid once it clears.
CHARGING_PI_STATES = ("succeeded", "processing")

#: The guard's verdicts. "Paid" and "Processing" are what dunning and autopay always read;
#: the other two say *why* an in-flight payment blocks, so nobody is promised something
#: untrue about it. Every one of them blocks.
PAID = "Paid"
#: Settling: it will show as paid once it clears (a card charge the webhook has not posted,
#: an ACH debit, a hosted Checkout payment, an off-session charge).
PROCESSING = "Processing"
#: 3-D Secure the payer may still be finishing; the verdict's ``release_at`` says until when.
AWAITING = "Awaiting"
#: Nobody knows yet: a charge Stripe never answered, or a Stripe lookup that failed.
UNCONFIRMED = "Unconfirmed"
#: A card attempt last seen unable to charge, whose cancel Stripe would not confirm just now
#: (the cancel was refused or timed out, and the read-back failed or still shows a state that
#: cannot charge). It blocks until it is released, but it is not settling and will never
#: "clear", so nothing is promised about it: try again shortly. Reached two ways: a card-page
#: attempt whose release a POST (or the cancel hook) could not prove — a page render never
#: releases, so it never sees that one, and the refusal is :class:`AttemptUnreleased` — and an
#: off-session card challenge ``charge_saved_method`` could not prove canceled
#: (:data:`NOTE_CANCEL_UNPROVEN`), which every read sees, and which poll_pending cancels.
UNRELEASED = "Unreleased"
#: An off-session bank debit waiting on the customer to verify their bank account (microdeposits,
#: :data:`NOTE_ACH_VERIFYING`). A payment, never canceled, that goes ahead only once they verify
#: — so it is not promised to "clear" either.
VERIFYING = "Verifying"

#: Seconds a page render (/pay, /pay-card) waits on a Stripe lookup. A lookup that does
#: not answer in time counts as "still in flight" — the safe verdict — and poll_pending
#: settles the attempt later; a slow Stripe never holds a web worker for 30 s per invoice.
READ_TIMEOUT = 4
#: Seconds for the lookups, cancels and expiries a payment request makes while it holds
#: the invoice lock, so the lock is never held for the client's full 30 s per call.
CONTROL_TIMEOUT = 10
#: Seconds a second request for the same invoice waits for the lock before it is refused
#: as "already being processed" — which is what another request holding it means.
INVOICE_LOCK_TIMEOUT = 15
#: Pages of a customer's PaymentIntents searched for a charge whose outcome was unknown.
PAYMENT_INTENT_LIST_PAGES = 5
#: How far up an ``amended_from`` chain the guard looks. No real invoice is amended this
#: many times over; the bound only stops a corrupt cycle.
AMENDMENT_DEPTH = 10

MSG_ALREADY_PAID = "This invoice has already been paid."
#: An invoice brought to zero by a credit note (status "Credit Note Issued") was credited,
#: not paid, and must not be called paid.
MSG_NOTHING_TO_PAY = "There is nothing left to pay on this invoice."
#: For a payment known to be settling only (:data:`PROCESSING`): the promise holds.
MSG_IN_FLIGHT = "A payment for this invoice is already being processed. It will show as paid once it clears."
#: Another request holds the invoice lock: a payment is being *started*, and may yet be
#: declined, so nothing is promised about it. (The card page reloads and renders the truth.)
MSG_BUSY = (
	"A payment for this invoice is being started right now, so another cannot start until it has "
	"an answer. Nothing was charged; please check again in a moment."
)
#: 3-D Secure inside its window (:data:`AWAITING`). An abandoned attempt never "clears", so
#: this one promises nothing it cannot keep: it says when the invoice can be paid again.
MSG_AWAITING = (
	"A card payment for this invoice is waiting for your bank's approval. If you did not finish "
	"approving it, you can pay again in about {minutes}."
)
MSG_AWAITING_DESK = (
	"A card payment for this invoice is waiting for the customer's bank approval (3-D Secure). "
	"If they do not finish it, it is released in about {minutes}."
)
#: A charge Stripe never answered, or a lookup that failed (:data:`UNCONFIRMED`).
MSG_UNCONFIRMED = (
	"We could not yet confirm whether a payment for this invoice went through, so it cannot be "
	"paid again right now. Please do not pay again; check back later."
)
MSG_UNCONFIRMED_DESK = (
	"A Stripe charge for this invoice has no confirmed outcome yet (Stripe did not answer it, or "
	"could not be reached just now). poll_pending settles it within the hour; try again later."
)
#: A release that could not be proven (:data:`UNRELEASED`). Neutral on purpose: the attempt
#: was last seen unable to charge, but Stripe did not confirm it canceled, so neither "it will
#: show as paid" nor "nothing was charged" is known to be true of it.
MSG_UNRELEASED = (
	"An earlier card payment attempt for this invoice could not be released just now, so a new "
	"payment cannot start yet, and this one was not started. Please try again later: it can take up "
	"to an hour to clear."
)
MSG_UNRELEASED_DESK = (
	"An earlier card payment attempt for this invoice was last seen unable to charge, but Stripe did "
	"not confirm its cancellation just now, so a new payment cannot start until it does. It can take "
	"up to an hour: poll_pending retries the cancellation hourly."
)
#: :data:`VERIFYING`. Only the customer's verification moves it, so neither "it will show as
#: paid once it clears" nor "try again shortly" is true of it.
MSG_VERIFYING = (
	"A bank payment for this invoice is waiting for your bank account to be verified. It goes "
	"ahead once the account is verified, so the invoice cannot be paid again meanwhile."
)
MSG_VERIFYING_DESK = (
	"A bank (ACH) debit for this invoice is waiting on the customer to verify their bank account "
	"(microdeposits). It goes ahead once they do and is never canceled automatically, so the "
	"invoice cannot be paid again meanwhile. If it should not go ahead, cancel it in Stripe."
)
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
	"A Stripe payment for this invoice (or for the invoice it was amended from) is already Paid, "
	"yet the invoice still shows a balance (most often because its Payment Entry was canceled, or "
	"the invoice was canceled and amended). Settle that before taking another payment."
)
MSG_AMOUNT_CHANGED = "The amount due on this invoice has changed. Continue again to see the new total."
MSG_AMOUNT_CHANGED_DESK = (
	"The amount due on this invoice changed while the payment was being started. Nothing was "
	"charged; start it again."
)
#: Canceled, amended or turned into a return since the quote. Neutral on purpose: which of
#: those it was is Accounts' business, and the customer's next step is the same.
MSG_INVOICE_CHANGED = "This invoice has changed. Please start again from your invoice list."
MSG_INVOICE_CHANGED_DESK = (
	"This invoice was canceled, amended or changed while the payment was being started. Nothing "
	"was charged; reload the invoice."
)
#: An emailed Checkout link that Stripe would neither expire nor show as finished (it did
#: not answer). Nothing was charged; the next attempt tries again.
MSG_LINK_STILL_OPEN = (
	"A payment link for this invoice is still open and could not be closed just now. "
	"Nothing was charged. Please try again in a few minutes."
)
MSG_LINK_STILL_OPEN_DESK = (
	"A Stripe payment link for this invoice is still open, and Stripe could not be reached to "
	"close it. Try again in a few minutes."
)
#: Autopay and dunning never expire the link the customer was sent (owner's decision 3 is
#: about a payment someone starts; a retry of a declined card is not one).
MSG_LINK_OPEN_OFF_SESSION = (
	"A payment link emailed for this invoice is still open, so the saved method is not charged "
	"while the customer may pay it. Autopay and dunning try again once it is paid or has expired."
)
#: The desk's "Charge Saved Method" with no invoice, and its ad hoc Checkout link
#: (``api.create_adhoc_payment``), are applied to no invoice, so nothing can guard them by invoice.
MSG_AD_HOC_BESIDE_OPEN = (
	"This customer has a Stripe payment in flight or a payment link open ({payments}). A payment "
	"with no invoice is applied to no invoice, so it would not settle that bill: choose the "
	"invoice instead, or wait until that payment has settled (or that link is paid or expires)."
)
#: before_invoice_cancel. ``{detail}`` is one of the ``CANCEL_DETAIL_*`` below.
MSG_CANCEL_IN_FLIGHT = (
	"A Stripe payment for this invoice is still in progress ({payments}), so the invoice cannot be "
	"canceled yet: its money would arrive for an invoice that no longer exists, and an amended copy "
	"could be paid a second time. {detail}"
)
CANCEL_DETAIL_PROCESSING = "Wait for it to settle (or refund it in Stripe), then cancel."
CANCEL_DETAIL_AWAITING = (
	"The customer is approving a card payment with their bank; if they do not finish, it is "
	"released in about {minutes}."
)
CANCEL_DETAIL_UNCONFIRMED = (
	"Stripe has not confirmed its outcome yet; poll_pending checks it within the hour. Try again then."
)
CANCEL_DETAIL_VERIFYING = (
	"It is a bank (ACH) debit waiting on the customer to verify their bank account, and it goes ahead "
	"once they do. If it should not, cancel it in Stripe; poll_pending records that on its next run, "
	"and the invoice can then be canceled."
)
#: before_invoice_cancel on :data:`UNRELEASED`: its own message, since the attempt is not "in
#: progress" and no money is on its way for it.
MSG_CANCEL_UNRELEASED = (
	"A Stripe card payment attempt for this invoice ({payments}) could not be released just now: it "
	"was last seen unable to charge, but Stripe did not confirm its cancellation. The invoice cannot "
	"be canceled until it is. It can take up to an hour: poll_pending retries the cancellation hourly."
)
MSG_CANCEL_LINK_USED = (
	"A Stripe payment link for this invoice has just been used to pay it, so the invoice cannot be "
	"canceled. The payment will post shortly; refund it in Stripe if the invoice is wrong."
)
MSG_NOT_CHARGED = (
	"The payment did not go through, and nothing was charged. Please try again, or use another card."
)
#: Recorded on a row written ahead as Processing whose charge got no answer from Stripe.
NOTE_OUTCOME_UNKNOWN = (
	"Outcome unknown: Stripe did not answer the charge (timeout, connection error or server "
	"error), and a retry with the same idempotency key did not answer either. Held as Processing, "
	"which blocks every new payment for the invoice, until poll_pending finds the PaymentIntent at "
	"Stripe (by this row's name in its metadata) or proves there is none."
)
#: resolve_unknown_outcome, found at Stripe. Also the marker :func:`held_unknown` reads: the
#: card page's payer was told "we will email you if it did not go through", so when this row
#: is later released as unable to charge, they are emailed (:func:`notify_released_unknown`).
NOTE_OUTCOME_FOUND = (
	"Outcome was unknown (Stripe did not answer the charge); poll_pending has since found its "
	"PaymentIntent at Stripe. If it turns out unable to charge, it is released and whoever was "
	"waiting on it is told nothing was charged."
)
#: resolve_unknown_outcome, proven absent. ``Expired`` rather than ``Failed``: dunning
#: enrols Failed autopay rows as declined cards, and this one never reached a card.
NOTE_NEVER_REACHED = (
	"No PaymentIntent for this attempt exists at Stripe, so nothing was charged. (Its charge had "
	"got no answer; poll_pending read the customer's PaymentIntents.) Marked Expired, not Failed: "
	"it never reached the card, so it is not a decline."
)
NOTE_MISSING_PI = (
	"Stripe has no such PaymentIntent for the configured key (404): it was made in the other mode "
	"(Test/Live) or another Stripe account, and cannot charge through this one. Marked Expired so "
	"the invoice is not blocked for good; Accounts were alerted."
)
NOTE_MISSING_SESSION = (
	"Stripe has no such Checkout Session for the configured key (404): it was made in the other "
	"mode (Test/Live) or another Stripe account, so it can be neither expired nor paid through this "
	"one. Marked Expired; Accounts were alerted."
)
NOTE_LINK_EXPIRED = "Expired at Stripe before another payment for the invoice was started."
NOTE_LINK_EXPIRED_CANCEL = "Expired at Stripe before the invoice was canceled."
NOTE_LINK_STALE = (
	"Expired at Stripe: the invoice was canceled, amended or settled another way, so this link "
	"no longer matched what is owed."
)
#: An off-session bank debit waiting on microdeposit verification: a payment in progress. The
#: guard reads this marker (:data:`VERIFYING`).
NOTE_ACH_VERIFYING = (
	"The bank account is waiting on microdeposit verification (requires_action). Held as "
	"Processing, and never canceled: the debit goes ahead once the customer verifies."
)
#: An off-session card challenge (requires_action, nobody present to authenticate) whose cancel
#: ``saved_methods.charge_saved_method`` could not prove. The guard reads this marker
#: (:data:`UNRELEASED`): it will never clear, and poll_pending cancels it.
NOTE_CANCEL_UNPROVEN = (
	"The PaymentIntent is in requires_action and could not be proven canceled at Stripe, so it is "
	"held as Processing (it blocks new payments) until poll_pending settles it."
)


class PaymentBlocked(frappe.ValidationError):
	"""A new payment for an invoice was refused because another one is paid, settling, or
	holding the invoice right now, or the invoice is paid or no longer payable. Nothing was
	charged. Dunning reschedules on it rather than counting it as a declined card, and the card
	page reloads on it, so ``www/pay_card.py`` renders the reason instead of a card form."""


class LinkStillOpen(PaymentBlocked):
	"""An emailed link Stripe could not be asked to close. Nothing was charged, and there is no
	state for the card page to render, so it shows this message and keeps its form."""


class AttemptUnreleased(PaymentBlocked):
	"""An :data:`UNRELEASED` refusal: an earlier attempt that cannot charge, whose cancel Stripe
	would not confirm just now. Nothing was charged. The card page shows this message and keeps
	its form, as for :class:`LinkStillOpen`, rather than reloading: a render never releases, so
	for a card-page attempt it reads the dead attempt as not blocking and would bring back the
	card form with no word of why — each tap looping until Stripe answered the cancel."""


class Verdict(str):
	"""A blocking verdict, compared as the plain string it is. ``release_at`` is set on
	:data:`AWAITING`: when the 3-D Secure attempt holding the invoice counts as abandoned."""

	release_at = None


def _awaiting(release_at) -> Verdict:
	verdict = Verdict(AWAITING)
	verdict.release_at = release_at
	return verdict


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

	with invoice_lock(sales_invoice):
		if sales_invoice:
			# Paid, credited, canceled or a return since the page rendered: refused as
			# PaymentBlocked, so the page reloads and says which instead of a bare modal.
			_recheck_invoice(sales_invoice, None, changed_exc=PaymentBlocked)
		customer, amount, currency, description = _resolve_target(
			sales_invoice, customer, amount, description, settings
		)
		# Before the card is even read: an invoice already paid, or with a payment still
		# settling, must never get a second quote. Reached by the phone's Back from
		# /stripe-return, or a second tab (www/pay_card.py shows the same verdict up front).
		# A quote moves no money, so an emailed Checkout link is left open here; the charge
		# (confirm_card_payment) closes it.
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
	:func:`price_card_payment`), and only a quote not yet sent: one row, one charge.
	Returns ``{"status", "client_secret", "requires_action", "outcome_unknown"}``; on
	``requires_action`` the page runs 3-D Secure with ``stripe.handleNextAction`` and the
	webhook finalises. Throws only when nothing was charged; every other outcome —
	including one Stripe never answered — returns ``status`` "Processing" or "Paid", and
	the page goes to "being processed" or "received", never back to a card form.

	Under the invoice lock, in order: the quote and the invoice are re-read, the guard
	runs (releasing a dead card attempt), an emailed Checkout link is expired, the Sales
	Invoice row is locked and read once more, the row is committed ``Processing`` (written
	ahead), and only then is Stripe asked to charge. The Payment Entry is posted after the
	lock is released; a failure to post it is logged and left to poll_pending, never shown
	to a customer whose card was charged.

	The caller is responsible for permission checks.
	"""
	settings = get_settings()
	if not is_enabled(settings):
		frappe.throw("Stripe Payments is not enabled.")

	sp = frappe.get_doc("Stripe Payment", stripe_payment)
	_check_quote(sp, confirmation_token)

	failure = pi = None
	with invoice_lock(sp.get("sales_invoice")):
		# The lock began a fresh read: another request may have confirmed this very row,
		# or paid the invoice, while this one waited for it.
		sp.reload()
		_check_quote(sp, confirmation_token)
		if sp.get("sales_invoice"):
			_recheck_invoice(sp.sales_invoice, sp.amount)
			_refuse_if_in_flight(sp.sales_invoice, exclude=sp.name)
			_close_open_checkouts(sp.sales_invoice)
			# The Sales Invoice row lock, held until the Processing commit below — nothing
			# commits in between. A cancel holds the same lock from its first write to its
			# commit, so whichever comes second sees the other: this read waits for a cancel
			# under way and then sees it canceled, and a cancel that waits on this one finds
			# the Processing row (before_invoice_cancel).
			_recheck_invoice(sp.sales_invoice, sp.amount, for_update=True)

		params = _intent_params(sp, confirmation_token, settings)
		# Written ahead: durable before Stripe is asked. From here, a timeout, a 5xx or a
		# worker killed mid-request (a deploy restart) leaves a row the guard reads as in
		# flight — never a Draft, which every guard ignores while the card may be charged.
		sp.db_set({"status": "Processing", "error_message": None})
		if sp.sales_invoice:
			_stamp_invoice(sp.sales_invoice, "Processing")
		frappe.db.commit()

		# Keyed on the ledger row, so a retry can never create a second PaymentIntent for
		# the same quote: Stripe answers a repeated key with the first request's result.
		pi, failure = create_intent_settled(create_payment_intent, params, f"ee-element-{sp.name}")
		# Each branch writes the invoice before the row: the order a cancel takes its locks
		# in (the invoice row, then the ledger's), so the two can never wait on each other.
		if failure is not None:
			# A decline or a refused request: proven not charged.
			if sp.sales_invoice:
				_stamp_invoice(sp.sales_invoice, "Unpaid")
			sp.db_set({"status": "Failed", "error_message": error_snippet(str(failure))})
		elif pi is None:
			# No answer, twice. Stays Processing (it may have charged) until poll_pending
			# finds the PaymentIntent by this row's name, or proves Stripe has none.
			sp.db_set("error_message", NOTE_OUTCOME_UNKNOWN)
		else:
			values = {"stripe_payment_intent": pi.get("id")}
			if pi.get("status") in DEAD_PI_STATES:
				values.update(status="Failed", error_message=f"PaymentIntent status: {pi.get('status')}")
				if sp.sales_invoice:
					_stamp_invoice(sp.sales_invoice, "Unpaid")
			sp.db_set(values)
		frappe.db.commit()

	# Thrown here, outside every except block, so no traceback chains back into the
	# request that carried the secret key.
	if failure is not None:
		frappe.throw(_not_charged_message(failure))
	if pi is not None and pi.get("status") in DEAD_PI_STATES:
		frappe.throw(MSG_NOT_CHARGED)

	status = pi.get("status") if pi else None
	if status == "succeeded":
		# Post the Payment Entry now; the webhook is a dedupe-protected backstop.
		post_charged_payment(sp, pi)

	sp.reload()
	return {
		"stripe_payment": sp.name,
		"status": sp.status,
		"payment_intent": pi.get("id") if pi else None,
		"requires_action": status == "requires_action",
		# Only needed to complete 3-D Secure in the browser; it authorises nothing
		# beyond this PaymentIntent.
		"client_secret": pi.get("client_secret") if status == "requires_action" else None,
		"outcome_unknown": pi is None,
	}


def _check_quote(sp, confirmation_token):
	"""Only a quote that has never been sent, for the card it was priced against."""
	if sp.status == "Failed":
		frappe.throw(
			"This attempt did not go through, so it cannot be sent again. Continue again for a new total."
		)
	if sp.status != "Draft":
		# Already charged (or settling) — never start a second PaymentIntent for it. The page
		# reloads on PaymentBlocked, and the render says which.
		frappe.throw(f"This payment is already {sp.status}.", PaymentBlocked)
	if not sp.confirmation_token or sp.confirmation_token != confirmation_token:
		frappe.throw("This payment was quoted for a different card. Start the payment again.")


def _recheck_invoice(sales_invoice, amount, *, for_update=False, desk=False, changed_exc=None):
	"""The invoice as it is now, re-read where the money moves. One read, so the checks agree.

	Canceled (ERPNext leaves a canceled invoice's outstanding_amount untouched, so the
	amount check alone would still charge it), amended, or a return: neutral refusal.
	Nothing left to pay: "paid" only when ERPNext says Paid — a credit note brings the
	balance to zero without anyone paying it — refused as :class:`PaymentBlocked`, which the
	card page reloads on to show it. Paid in part (a cheque, a credit note), or a balance
	that grew: refused too, since an invoice payment is always the whole outstanding
	(``_resolve_target``) and charging it now would leave the difference as an overpayment.
	``amount`` ``None`` skips that last check (the quote, which prices the outstanding itself).

	``for_update`` takes the Sales Invoice row lock (``SELECT … FOR UPDATE``) and holds it to
	the caller's next commit: the last read before a payment is committed in flight, and the
	one a concurrent cancel serializes against (see :func:`before_invoice_cancel`).
	``changed_exc`` is what a changed invoice raises — plain ``ValidationError`` on the card
	page (its quote is spent; back to the card step), :class:`PaymentBlocked` on the paths
	dunning drives, where anything else would count as a declined card.
	"""
	si = frappe.db.get_value(
		"Sales Invoice",
		sales_invoice,
		["docstatus", "is_return", "outstanding_amount", "status"],
		as_dict=True,
		for_update=for_update,
	)
	changed_exc = changed_exc or frappe.ValidationError
	if not si or cint(si.docstatus) != 1 or cint(si.is_return):
		frappe.throw(MSG_INVOICE_CHANGED_DESK if desk else MSG_INVOICE_CHANGED, changed_exc)
	outstanding = flt(si.outstanding_amount, 2)
	if outstanding <= 0:
		frappe.throw(MSG_ALREADY_PAID if si.status == "Paid" else MSG_NOTHING_TO_PAY, PaymentBlocked)
	if amount is not None and outstanding != flt(amount, 2):
		frappe.throw(MSG_AMOUNT_CHANGED_DESK if desk else MSG_AMOUNT_CHANGED, changed_exc)


def _intent_params(sp, confirmation_token, settings) -> dict:
	total = flt(flt(sp.amount) + flt(sp.surcharge_amount), 2)
	metadata = {
		"erpnext_customer": sp.customer,
		# How poll_pending finds this PaymentIntent again when Stripe never answered.
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
		# Must match the Payment Element's own `paymentMethodTypes: ["card"]` in
		# www/pay-card.html. A ConfirmationToken collected by an Element configured with
		# payment_method_types can only confirm an intent that names them too: left out,
		# the intent defaults to automatic payment methods and Stripe refuses every charge
		# with a 400 ("Payment details were collected through Stripe Elements using
		# payment_method_types and cannot be confirmed through the API configured with
		# automatic payment methods"). That is how the first live portal payment failed
		# on 2026-09-25, under an earlier comment that argued pinning was unnecessary.
		# Nothing was charged: a 400 is a definite refusal.
		"payment_method_types": ["card"],
		"return_url": f"{get_url(settings.success_route or '/stripe-return')}"
		f"?status=success&sp={sp.name}",
		"description": (sp.description or "Payment")[:250],
		"metadata": metadata,
	}
	# No statement_descriptor_suffix: Stripe builds a card's descriptor as the account's
	# prefix + "* " + suffix, and the whole must be 22 characters or fewer. The setting holds
	# a full descriptor ("Sapphire Fountains LLC", 22 characters on prod), not a suffix, so
	# sending it here overflows on every charge, and Stripe's docs do not promise to
	# truncate rather than refuse. The account's own descriptor applies instead. (The hosted
	# path sends it only when ACH is off, which is not the case on prod.)
	return params


def _not_charged_message(exc) -> str:
	"""What a customer is told after a definite failure. Stripe's own message for a card
	error is written for the cardholder; nothing else from the response (a JSON body, an
	internal code) is shown."""
	detail = getattr(exc, "stripe_message", None) if getattr(exc, "status_code", None) == 402 else None
	if detail:
		return f"The payment did not go through: {error_snippet(detail, 200)} Nothing was charged."
	return MSG_NOT_CHARGED


# --- charging: definite answers and unknown ones ------------------------------


def create_intent_settled(create, params, idempotency_key):
	"""Create (and confirm) a PaymentIntent, and say what is actually known of the outcome.

	Returns ``(payment_intent, None)`` when Stripe answered; ``(None, exc)`` when it
	answered that nothing was charged (``client.is_definite_failure`` — a decline, an
	invalid request); and ``(None, None)`` when the outcome is unknown: a timeout, a
	dropped connection, a 5xx, a 409 (the same key still executing), a response that would
	not parse. An unknown outcome may well have charged the card, so it is never a decline.

	One retry, with the **same** idempotency key, which Stripe answers with the first
	request's stored result if it ran, and runs once if it never arrived. After an unknown
	first attempt only a decline (402) on the retry is trusted as proof: any other refusal
	could be about the first attempt having run. ``create`` is the caller's own
	``create_payment_intent``, so tests patch the module they exercise.
	"""
	try:
		return create(params, idempotency_key=idempotency_key), None
	except Exception as exc:
		if is_definite_failure(exc):
			return None, exc
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: charge got no answer; retrying once")
	try:
		return create(params, idempotency_key=idempotency_key), None
	except Exception as exc:
		if isinstance(exc, StripeError) and exc.status_code == 402:
			return None, exc
		frappe.log_error(
			error_snippet(frappe.get_traceback()), "Stripe: charge outcome unknown; held as Processing"
		)
	return None, None


def post_charged_payment(sp, source_obj):
	"""Post the Payment Entry for a charge that succeeded, whose row is already committed
	``Processing`` with its PaymentIntent.

	A failure here (no deposit account, a closed period, the finalize lock held by the
	webhook) must never reach the customer as "failed": their card *was* charged. So the
	half-made Payment Entry is rolled back to the committed Processing state, the error is
	logged (the traceback text only — no frame locals, so no secret), and the caller
	answers "Processing". The webhook and poll_pending both post it later; finalize is
	idempotent on the PaymentIntent.
	"""
	from erpnext_enhancements.stripe_payments.core import reconcile

	try:
		reconcile.finalize_payment(sp, source_obj)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(
			error_snippet(frappe.get_traceback()),
			f"Stripe: {sp.name} charged, Payment Entry not posted yet (poll_pending retries)",
		)


# --- the invoice lock -----------------------------------------------------------


@contextlib.contextmanager
def invoice_lock(sales_invoice):
	"""Serialize everything that can start a payment for one invoice.

	Held from the guard through the commit that makes the new attempt visible to the next
	guard — ``Processing`` for a charge, ``Link Sent`` for a Checkout Session — so two
	requests (two tabs holding quotes, two portal contacts of one customer, a desk link
	beside a card payment) can never both pass the guard. The ``filelock`` pattern
	``reconcile.finalize_payment`` already uses; it holds across the commits this path
	makes, where a ``SELECT ... FOR UPDATE`` would be released by the first one.

	Acquiring it commits, which ends the request's read snapshot. Under REPEATABLE READ a
	request that read anything before waiting here would otherwise keep reading the
	ledger as it stood *before* the previous holder committed its Processing row — and
	pass the guard it was waiting on. A second request that cannot get the lock within
	:data:`INVOICE_LOCK_TIMEOUT` is refused (:data:`MSG_BUSY`, as ``PaymentBlocked``): another
	request holding it means a payment is being started right now. Not re-entrant: only the functions that start a
	payment take it, never their callers. A no-op without an invoice (an ad hoc payment).

	A cancel does not take it (it must not commit halfway through the cancel's own
	transaction); the Sales Invoice row lock serializes a cancel instead — see
	:func:`before_invoice_cancel`.
	"""
	if not sales_invoice:
		yield
		return
	from frappe.utils import synchronization

	lock_timeout = getattr(synchronization, "LockTimeoutError", None) or ()
	acquired = busy = False
	try:
		with synchronization.filelock(_invoice_lock_name(sales_invoice), timeout=INVOICE_LOCK_TIMEOUT):
			acquired = True
			frappe.db.commit()
			yield
	except lock_timeout:
		if acquired:
			raise
		busy = True
	if busy:
		frappe.throw(MSG_BUSY, PaymentBlocked)


def _invoice_lock_name(sales_invoice) -> str:
	# The name becomes a file name under the site's locks directory.
	safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(sales_invoice))
	digest = hashlib.sha1(str(sales_invoice).encode("utf-8")).hexdigest()[:10]
	return f"stripe_invoice_{safe}_{digest}"


# --- an invoice and the invoices it was amended from ------------------------------


def _invoice_family(sales_invoice) -> list:
	"""The invoice and every invoice it was amended from, nearest first.

	Canceling an invoice and amending it gives the copy a new name, and every rule here keys
	on the name — so a payment still in flight on the original (an ACH debit, a charge whose
	Payment Entry could not post) or a link still open for it would be invisible to the
	copy's guard, and autopay would charge the copy on submit: two payments for one bill.
	:func:`before_invoice_cancel` refuses such a cancel in the first place; this is the
	defence in depth for whatever got past it (a cancel while the integration was switched
	off, one with ``ignore_validate``, a race it lost). A ``Paid`` row on the original
	blocks the copy too ("received — contact us"): Accounts allocate that money, not a
	second charge.
	"""
	family = [sales_invoice]
	current = sales_invoice
	for _ in range(AMENDMENT_DEPTH):
		parent = frappe.db.get_value("Sales Invoice", current, "amended_from")
		if not parent or parent in family:
			break
		family.append(parent)
		current = parent
	return family


def _invoice_families(sales_invoices) -> dict:
	"""``{invoice: its family}`` for a page's list. One query finds which listed invoices are
	amendments at all — almost none — and only those walk up."""
	families = {name: [name] for name in sales_invoices}
	for row in frappe.get_all(
		"Sales Invoice",
		filters={"name": ["in", list(sales_invoices)], "amended_from": ["is", "set"]},
		fields=["name", "amended_from"],
	):
		families[row.name] = _invoice_family(row.name)
	return families


#: What the guard reads of each ledger row.
_GUARD_FIELDS = [
	"name",
	"sales_invoice",
	"status",
	"stripe_payment_intent",
	"stripe_checkout_session",
	"confirmation_token",
	"payment_method_type",
	"modified",
	# held_unknown: a released attempt whose payer was promised an email.
	"error_message",
]


def _ledger_rows(invoices, status, fields, *, for_update=False):
	"""Stripe Payment rows for these invoices in ``status`` (a value or an ``["in", …]``).

	``for_update`` is a locking read (``SELECT … FOR UPDATE``, on the ``sales_invoice``
	index): the latest *committed* rows, whatever the transaction's snapshot, locked until
	its commit. Only :func:`before_invoice_cancel` needs it — its transaction began long
	before, and may not commit halfway."""
	filters = {
		"sales_invoice": invoices[0] if len(invoices) == 1 else ["in", list(invoices)],
		"status": status,
	}
	if for_update:
		return frappe.db.get_values(
			"Stripe Payment", filters, fields, as_dict=True, for_update=True, order_by=None
		)
	return frappe.get_all("Stripe Payment", filters=filters, fields=fields)


# --- the guard -------------------------------------------------------------------


def invoice_payment_block(
	sales_invoice: str, *, exclude: str | None = None, release: bool = False, commit: bool = True
):
	"""The verdict on starting a new payment for this invoice — card or bank: ``None`` when
	it may, else :data:`PAID`, :data:`PROCESSING`, :data:`AWAITING` (with ``release_at``),
	:data:`UNCONFIRMED`, :data:`VERIFYING` or :data:`UNRELEASED`, all of which block.

	It reads the ledger the way ``dunning`` and ``saved_methods`` did (a ``Processing`` or
	``Paid`` Stripe Payment for the invoice blocks) — for the invoice *and every invoice it
	was amended from* (:func:`_invoice_family`) — with one difference. A Payment Element
	attempt — the only kind that carries a ConfirmationToken, and the only kind whose 3-D
	Secure runs in our page, where a Back can abandon it — is asked of Stripe, and does not
	block once it provably cannot charge: its PaymentIntent is in :data:`DEAD_PI_STATES`,
	has sat in ``requires_action`` for :data:`ABANDON_AFTER_MINUTES` without a change, or
	does not exist for this key (404). Inside that window it blocks as :data:`AWAITING`: the
	payer may still be approving in their bank's app. Anything that cannot be settled that
	way blocks: hosted Checkout, ACH and off-session charges settle on their own
	(:data:`PROCESSING`) — except an off-session bank debit waiting on the customer to verify
	their account (:data:`VERIFYING`) and an off-session card challenge whose cancel could not
	be proven (:data:`UNRELEASED`), neither of which will clear by itself; a card attempt whose
	PaymentIntent we never learned (the charge got no answer) may have charged, and a Stripe
	lookup that fails is not proof that nothing charged (:data:`UNCONFIRMED`). Those rows are
	never looked up and never canceled here — an ACH debit waiting on microdeposit
	verification sits in ``requires_action`` for days, and is a payment, not an abandoned one.

	Every path that starts a payment for an invoice runs it once, through
	:func:`_refuse_if_in_flight` with ``release``, inside :func:`invoice_lock`:
	``price_card_payment``, ``confirm_card_payment``, ``checkout.create_payment`` (the
	Bank button on /pay and the desk's "Pay with Stripe") and
	``saved_methods.charge_saved_method`` for an invoice (autopay, dunning, and the desk's
	"Charge Saved Method" **when an invoice is chosen** — without one it is an ad hoc
	charge, which no invoice guard can cover; it is refused instead while the customer has
	anything in flight). ``www/pay_card.py`` and ``dunning`` read it, and ``www/pay.py``
	its list form :func:`invoice_payment_blocks`, without — they start nothing.

	``exclude`` is the row being confirmed. ``release`` (the POSTs, never a render)
	cancels each dead PaymentIntent at Stripe and fails its row before saying ``None``: a
	PaymentIntent that succeeded in the meantime cannot be canceled, so the old attempt
	and the new one can never both charge. A release that cannot be proven blocks as
	:data:`PROCESSING` when Stripe's read-back shows the attempt charged after all
	(``succeeded`` / ``processing``), and otherwise as :data:`UNRELEASED` — "could not be
	released just now", never "will show as paid" — refused as :class:`AttemptUnreleased`,
	which the card page shows beside its form. A render waits :data:`READ_TIMEOUT` on each
	lookup and logs nothing; a POST waits :data:`CONTROL_TIMEOUT` and logs a failure.
	``commit`` ``False`` leaves those writes to the caller's transaction (the cancel hook).
	"""
	if not sales_invoice:
		return None
	rows = [
		row
		for row in _ledger_rows(_invoice_family(sales_invoice), ["in", [PROCESSING, PAID]], _GUARD_FIELDS)
		if row.name != exclude
	]
	read = _status_reader(CONTROL_TIMEOUT if release else READ_TIMEOUT, quiet=not release)
	return _block_from_rows(sales_invoice, rows, release, read, commit=commit)


def invoice_payment_blocks(sales_invoices) -> dict:
	""":func:`invoice_payment_block` for every invoice a page lists: ``{invoice: verdict}``,
	holding only the blocked ones.

	For ``www/pay.py``, which decides from it which invoices get Card and Bank buttons — so
	the list offers exactly what the endpoints behind those buttons will accept, rather than
	trusting the invoice's ``custom_stripe_payment_status`` stamp, which nothing clears when
	a 3-D Secure attempt is abandoned or fails. One ledger query for the lot (amended-from
	invoices included), and Stripe is asked only about the rows the single form would ask
	about, each once, with the render's short timeout. After one lookup fails the rest are
	not attempted — they would most likely fail the same way, a timeout each — and read as
	:data:`UNCONFIRMED`. Never releases.
	"""
	names = [name for name in sales_invoices or () if name]
	if not names:
		return {}
	families = _invoice_families(names)
	wanted = list(dict.fromkeys(member for family in families.values() for member in family))
	rows_by_invoice = {}
	for row in _ledger_rows(wanted, ["in", [PROCESSING, PAID]], _GUARD_FIELDS):
		rows_by_invoice.setdefault(row.sales_invoice, []).append(row)
	read = _status_reader(READ_TIMEOUT, quiet=True)
	verdicts = {}
	for sales_invoice, family in families.items():
		rows = [row for member in family for row in rows_by_invoice.get(member, ())]
		block = _block_from_rows(sales_invoice, rows, False, read) if rows else None
		if block:
			verdicts[sales_invoice] = block
	return verdicts


def _block_from_rows(sales_invoice, rows, release, read, commit=True, canceling=False):
	"""The verdict for one invoice's ``Processing`` / ``Paid`` Stripe Payment rows.
	``canceling``: the cancel hook, whose release notices word it for a canceled invoice."""
	if any(row.status == PAID for row in rows):
		return PAID
	# Anything but a Payment Element attempt with a known PaymentIntent is waited on —
	# never looked up, never canceled. Checked before any card attempt is asked about, so a
	# payment in flight costs no call to Stripe and no card attempt is released beside it.
	waited_on = [row for row in rows if not (row.confirmation_token and row.stripe_payment_intent)]
	if waited_on:
		return _waited_on_verdict(waited_on)

	dead, awaiting, unknown = [], [], False
	for row in rows:
		pi_status = read(row.stripe_payment_intent)
		if _cannot_charge(row, pi_status):
			dead.append((row, pi_status))
		elif pi_status == AWAITING_PAYER_PI_STATE:
			awaiting.append(row)  # 3-D Secure the payer may still finish
		elif pi_status is None:
			unknown = True  # a lookup that failed proves nothing
		else:
			return PROCESSING  # succeeded (the webhook has not posted it yet), or processing
	if unknown:
		return UNCONFIRMED
	if awaiting:
		return _awaiting(max(_release_at(row) for row in awaiting))

	if release:
		for row, pi_status in dead:
			released, state = _release_attempt(row, pi_status, commit=commit, canceling=canceling)
			if not released:
				# The payer finished 3-D Secure a moment ago and it charged after all: settling,
				# and it will clear. Anything else — the cancel refused or timed out, and the
				# read-back failed or still shows a state that cannot charge — will never clear,
				# so nothing is promised about it: it could not be released just now.
				return PROCESSING if state in CHARGING_PI_STATES else UNRELEASED
		if dead:
			# The dead attempt stamped the invoice "Processing". That is no longer true of
			# anything (the caller re-stamps it if it starts something).
			_stamp_invoice(sales_invoice, "Unpaid")
			if commit:
				frappe.db.commit()
	return None


def _outcome_unknown_row(row) -> bool:
	"""A ledger row held Processing with no PaymentIntent and no Checkout Session: a charge
	whose outcome Stripe never told us (see :func:`outcome_unknown`)."""
	return not getattr(row, "stripe_payment_intent", None) and not getattr(
		row, "stripe_checkout_session", None
	)


#: Which verdict several waited-on rows give: the one that says most about the invoice. A
#: payment settling first — it will clear, whatever else is held — and "try again shortly"
#: last, since that is true only when nothing else is holding the invoice.
_WAITED_ON_ORDER = (PROCESSING, VERIFYING, UNCONFIRMED, UNRELEASED)


def _waited_on_verdict(rows):
	"""The verdict for rows the guard waits on — never looks up, never cancels — from what each
	is known to be (:func:`_waited_on_kind`)."""
	kinds = {_waited_on_kind(row) for row in rows}
	return next(verdict for verdict in _WAITED_ON_ORDER if verdict in kinds)


def _waited_on_kind(row):
	"""What one waited-on row is, from what is recorded on it:

	* a charge Stripe never answered — :data:`UNCONFIRMED`;
	* an off-session bank debit waiting on microdeposit verification
	  (:data:`NOTE_ACH_VERIFYING`) — :data:`VERIFYING`: it goes ahead only once the customer
	  verifies, so it is never promised to clear;
	* an off-session card challenge whose cancel could not be proven
	  (:data:`NOTE_CANCEL_UNPROVEN`) — :data:`UNRELEASED`: nobody can finish it, it will never
	  clear, and poll_pending cancels it. It was answered "It will show as paid once it clears".
	  Only a card: poll_pending cancels nothing else in ``requires_action``
	  (:func:`_cannot_charge`), so "try again shortly" would be untrue of any other kind;
	* anything else — a hosted Checkout payment or ACH debit, an off-session charge Stripe
	  answered charging — is settling: :data:`PROCESSING`.
	"""
	if _outcome_unknown_row(row):
		return UNCONFIRMED
	note = getattr(row, "error_message", None)
	if note == NOTE_ACH_VERIFYING:
		return VERIFYING
	if note == NOTE_CANCEL_UNPROVEN and getattr(row, "payment_method_type", None) == "card":
		return UNRELEASED
	return PROCESSING


def _cannot_charge(row, pi_status) -> bool:
	"""Whether an attempt's PaymentIntent can no longer charge without our page confirming it
	again: dead, missing at Stripe (404), or ``requires_action`` nobody will finish.

	For a Payment Element attempt (it carries a ConfirmationToken) that is 3-D Secure
	untouched for :data:`ABANDON_AFTER_MINUTES`. An off-session attempt (poll_pending only —
	the guard never looks one up) has nobody present to authenticate: a card's challenge can
	never be finished, so it is dead at once; a bank debit's ``requires_action`` is
	microdeposit verification — a payment in progress, never released (nor is one whose kind
	is unknown)."""
	if pi_status in DEAD_PI_STATES or pi_status == MISSING_PI_STATE:
		return True
	if pi_status != AWAITING_PAYER_PI_STATE:
		return False
	if getattr(row, "confirmation_token", None):
		return _minutes_untouched(row) >= ABANDON_AFTER_MINUTES
	return getattr(row, "payment_method_type", None) == "card"


def _minutes_untouched(row) -> float:
	"""Minutes since the row last changed (site-local ``modified`` against site-local now —
	never a UTC stamp). An age that cannot be read counts as zero: a payer is never cut
	short on missing data."""
	stamp = getattr(row, "modified", None) or getattr(row, "creation", None)
	if not stamp:
		return 0.0
	try:
		return (now_datetime() - get_datetime(stamp)).total_seconds() / 60.0
	except Exception:
		return 0.0


def _release_at(row):
	"""When a card attempt in ``requires_action`` counts as abandoned. An age that cannot be
	read gives it a whole window from now, as :func:`_minutes_untouched` does."""
	stamp = getattr(row, "modified", None) or getattr(row, "creation", None)
	try:
		return get_datetime(stamp) + datetime.timedelta(minutes=ABANDON_AFTER_MINUTES)
	except Exception:
		return now_datetime() + datetime.timedelta(minutes=ABANDON_AFTER_MINUTES)


def awaiting_minutes(verdict) -> int:
	"""Whole minutes (at least 1) until an :data:`AWAITING` verdict's attempt is released."""
	release_at = getattr(verdict, "release_at", None)
	try:
		seconds = (release_at - now_datetime()).total_seconds()
	except Exception:
		return ABANDON_AFTER_MINUTES
	return max(1, math.ceil(seconds / 60.0))


def _minutes_phrase(verdict) -> str:
	minutes = awaiting_minutes(verdict)
	return "1 minute" if minutes == 1 else f"{minutes} minutes"


def _status_reader(timeout, quiet):
	"""A PaymentIntent-status lookup for one request: each PaymentIntent asked once, and
	once a lookup has failed the rest are not attempted (``None``, "still in flight"),
	so a Stripe that is down costs one timeout, not one per invoice. Stripe's 404 is an
	answer, not a failure: :data:`MISSING_PI_STATE`."""
	cache = {}
	down = []

	def read(payment_intent_id):
		if payment_intent_id in cache:
			return cache[payment_intent_id]
		status = None
		if not down:
			try:
				status = (retrieve_payment_intent(payment_intent_id, timeout=timeout) or {}).get("status")
			except Exception as exc:
				if is_missing(exc):
					status = MISSING_PI_STATE
				else:
					down.append(payment_intent_id)
					if not quiet:
						frappe.log_error(
							error_snippet(frappe.get_traceback()), "Stripe: card attempt status lookup failed"
						)
		cache[payment_intent_id] = status
		return status

	return read


def block_message(verdict, desk=False) -> str:
	"""What a refusal on ``verdict`` says — to the customer, or (``desk``) to Accounts. Only
	a payment known to be settling is promised to "show as paid once it clears"."""
	if verdict == PAID:
		return MSG_ALREADY_RECEIVED_DESK if desk else MSG_ALREADY_RECEIVED
	if verdict == AWAITING:
		return (MSG_AWAITING_DESK if desk else MSG_AWAITING).format(minutes=_minutes_phrase(verdict))
	if verdict == UNCONFIRMED:
		return MSG_UNCONFIRMED_DESK if desk else MSG_UNCONFIRMED
	if verdict == UNRELEASED:
		return MSG_UNRELEASED_DESK if desk else MSG_UNRELEASED
	if verdict == VERIFYING:
		return MSG_VERIFYING_DESK if desk else MSG_VERIFYING
	return MSG_IN_FLIGHT


def _refuse_if_in_flight(sales_invoice, exclude=None, desk=False):
	# Every caller has already refused an invoice with nothing outstanding (_resolve_target,
	# or confirm_card_payment's own re-read), so a "Paid" verdict here is a received payment
	# on an invoice that still shows a balance. ``desk`` words each verdict for Accounts.
	# UNRELEASED is AttemptUnreleased: the card page keeps its form and shows the message.
	block = invoice_payment_block(sales_invoice, exclude=exclude, release=True)
	if block:
		refusal = AttemptUnreleased if block == UNRELEASED else PaymentBlocked
		frappe.throw(block_message(block, desk), refusal)


def _refuse_ad_hoc_beside_open_payments(customer):
	"""A payment with no invoice — the desk's "Charge Saved Method" with none chosen
	(``saved_methods.charge_saved_method``), or its ad hoc Checkout link for an amount
	(``api.create_adhoc_payment`` → ``checkout.create_payment``) — is posted as a Payment Entry
	allocated to nothing, so every invoice stays due and no invoice guard can see it. Refused
	while the customer has a Stripe payment settling or a payment link open — the moment
	Accounts reach for it to "pay" a bill that is already being paid."""
	rows = frappe.get_all(
		"Stripe Payment",
		filters={"customer": customer, "status": ["in", [PROCESSING, "Link Sent"]]},
		fields=["name", "sales_invoice", "status"],
	)
	if rows:
		payments = ", ".join(
			f"{row.name}: {row.status}" + (f" for {row.sales_invoice}" if row.sales_invoice else "")
			for row in rows[:3]
		)
		frappe.throw(MSG_AD_HOC_BESIDE_OPEN.format(payments=payments), PaymentBlocked)


def _cancel_payment_intent(payment_intent_id):
	"""Cancel a PaymentIntent at Stripe (``POST /v1/payment_intents/:id/cancel``).

	Through the client's own request helper: this module still never touches HTTP, and
	there is still no Stripe SDK. No idempotency key, on purpose: Stripe refuses to cancel
	a PaymentIntent twice, so a key would add no safety, and it would replay a transient
	error for 24 hours. A cancel that raises is settled by reading the PaymentIntent back.
	"""
	return _request(
		"POST",
		f"/payment_intents/{payment_intent_id}/cancel",
		data={"cancellation_reason": "abandoned"},
		timeout=CONTROL_TIMEOUT,
	)


def cancel_attempt(payment_intent_id) -> bool:
	"""Cancel a PaymentIntent and say whether it is now *provably* canceled
	(:func:`_cancel_and_read`)."""
	return _cancel_and_read(payment_intent_id) == "canceled"


def _cancel_and_read(payment_intent_id):
	"""Cancel a PaymentIntent and return the state Stripe now reports for it — ``"canceled"``
	only when that is proven, ``None`` when it could not be read back.

	A cancel that raises or times out proves nothing either way, so Stripe's record
	decides: the PaymentIntent is read back, and only ``canceled`` counts. That covers a
	second request that canceled it a moment earlier (Stripe refuses a second cancel), and
	refuses the one that matters — the payer finished 3-D Secure a moment ago, and a
	succeeded PaymentIntent cannot be canceled. The caller words its refusal from the state
	returned: only one that is charging (:data:`CHARGING_PI_STATES`) will ever clear."""
	try:
		now = (_cancel_payment_intent(payment_intent_id) or {}).get("status")
	except Exception:
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: card attempt cancel refused")
		now = None
	if now != "canceled":
		now = _status_reader(CONTROL_TIMEOUT, quiet=False)(payment_intent_id)
	return now


def _release_attempt(row, pi_status, commit=True, canceling=False):
	"""Make an attempt that cannot charge provably dead: cancel its PaymentIntent and fail its
	row. Returns ``(released, state)``. Not released — so the caller keeps blocking — whenever
	that is not certain; ``state`` is then what Stripe last reported for the PaymentIntent
	(``None`` when it could not be read), which the caller words its refusal from. A
	PaymentIntent Stripe does not have (404) cannot be canceled and cannot charge here: its row
	is marked ``Expired`` and Accounts are told.

	A released row whose charge had been held with an unknown outcome (:func:`held_unknown`)
	has a payer who was promised an email if it did not go through: they get it now
	(:func:`notify_released_unknown`) — worded for a canceled invoice when the release is the
	cancel hook's (``canceling``)."""
	if pi_status == MISSING_PI_STATE:
		frappe.db.set_value(
			"Stripe Payment", row.name, {"status": "Expired", "error_message": NOTE_MISSING_PI}
		)
		_alert_missing(row.name, "PaymentIntent", row.stripe_payment_intent)
		if commit:
			frappe.db.commit()
		return True, MISSING_PI_STATE
	if pi_status != "canceled":
		state = _cancel_and_read(row.stripe_payment_intent)
		if state != "canceled":
			return False, state
	# Read before the write below replaces the marker with the release note.
	was_held_unknown = held_unknown(row)
	frappe.db.set_value(
		"Stripe Payment",
		row.name,
		{"status": "Failed", "error_message": _release_note(pi_status)},
	)
	if commit:
		frappe.db.commit()
	if was_held_unknown:
		# After the release is durable, and made durable itself at once: a later row's refusal
		# rolls the request back, and must not take this notice with it. (In the cancel hook,
		# ``commit`` False, both stand or fall with the cancel.)
		notify_released_unknown(row.name, pi_status, invoice_canceled=canceling)
		if commit:
			frappe.db.commit()
	return True, "canceled"


def _release_note(pi_status) -> str:
	if pi_status == AWAITING_PAYER_PI_STATE:
		return (
			f"Abandoned: 3-D Secure was not completed within {ABANDON_AFTER_MINUTES} minutes. "
			"Canceled at Stripe; nothing was charged."
		)
	return f"Did not complete (PaymentIntent {pi_status}). Canceled at Stripe; nothing was charged."


def _alert_accounts(subject, content, doctype=None, docname=None):
	"""Best-effort Notification Log to every Accounts Manager (``reconcile._accounts_notify``)."""
	try:
		from erpnext_enhancements.stripe_payments.core import reconcile

		reconcile._accounts_notify(subject, content, doctype, docname)
	except Exception:
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: accounts alert failed")


def _alert_missing(stripe_payment, kind, stripe_id):
	_alert_accounts(
		f"Stripe {kind} not found: {stripe_payment}",
		f"Stripe answered 404 for {kind} {html.escape(stripe_id or '')} (Stripe Payment "
		f"{stripe_payment}): it does not exist for the configured key. It was most likely made while "
		"Stripe Payments Settings pointed at the other mode (Test/Live) or another Stripe account. The "
		"row was marked Expired so the invoice is not blocked for good. If it was made in another "
		"<b>live</b> account, check that account's dashboard: it may still be payable there.",
		"Stripe Payment",
		stripe_payment,
	)


# --- emailed Checkout links (owner's decision 3) --------------------------------


def _close_open_checkouts(sales_invoice, *, mode="payment", desk=False):
	"""Close every hosted Checkout link still open for the invoice before a new payment for
	it starts — or, in ``"cancel"`` mode, before the invoice is canceled.

	A ``Link Sent`` Checkout Session stays payable for 24 hours, and the link is often in
	the customer's inbox, so a card payment (or a second link, or a desk charge) started
	beside it could be followed by the customer completing the link: two payments.

	``mode``:

	* ``"payment"`` (the card page, the Bank button, the desk's link and desk charge): each
	  open session on the invoice or an invoice it was amended from is expired
	  (``client.expire_checkout_session``) and its row marked ``Expired``.
	* ``"off_session"`` (autopay, dunning): nothing is expired. The customer was sent that
	  link — often *because* the card declined — and a retry of a declined card is the
	  likeliest thing to fail again, so expiring it would leave the invoice with no way to be
	  paid at all. An open link refuses the charge instead (:data:`MSG_LINK_OPEN_OFF_SESSION`,
	  as ``PaymentBlocked``, which dunning reschedules on), as ``tasks.sweep_missed_autopay``
	  already treats an open link as the invoice being handled.
	* ``"cancel"`` (:func:`before_invoice_cancel`): the invoice's own links, read with a
	  locking read, inside the cancel's transaction — nothing is committed here.

	Stripe refuses to expire a session that is already complete — paid, or an ACH debit
	submitted — and then the new payment (or the cancel) is refused, because the invoice is
	being paid; the row is marked ``Processing``, as the webhook would, so the next guard
	blocks without asking Stripe. Each write is conditional on the row still being ``Link
	Sent`` (:func:`_settle_link`), so a webhook that marked it Paid meanwhile is never
	overwritten. A session Stripe does not have (404) can be neither expired nor paid through
	this account: its row is marked ``Expired``, Accounts are told, and the payment goes on.
	When Stripe answers none of those ways, the new payment is refused too
	(:class:`LinkStillOpen`); nothing was charged, and the next attempt tries again.
	Holders of the invoice lock (or, for a cancel, the Sales Invoice row lock) only.
	"""
	if not sales_invoice:
		return
	cancel = mode == "cancel"
	commit = not cancel
	invoices = [sales_invoice] if cancel else _invoice_family(sales_invoice)
	rows = _ledger_rows(
		invoices, "Link Sent", ["name", "sales_invoice", "stripe_checkout_session"], for_update=cancel
	)
	desk = desk or cancel
	in_flight = MSG_CANCEL_LINK_USED if cancel else MSG_IN_FLIGHT
	for row in rows:
		session_id = getattr(row, "stripe_checkout_session", None)
		if not session_id:
			continue  # no session, no link: nothing anyone can pay
		state = _session_state(session_id, expire=mode != "off_session")
		if state == "missing":
			changed, current = _settle_link(row.name, "Expired", NOTE_MISSING_SESSION, commit)
			if changed:
				_alert_missing(row.name, "Checkout Session", session_id)
		elif state == "expired":
			changed, current = _settle_link(
				row.name, "Expired", NOTE_LINK_EXPIRED_CANCEL if cancel else NOTE_LINK_EXPIRED, commit
			)
		elif state == "complete":
			changed, current = _settle_link(row.name, "Processing", None, commit)
			if changed:
				_stamp_invoice(row.sales_invoice or sales_invoice, "Processing")
				if commit:
					frappe.db.commit()
			frappe.throw(in_flight, PaymentBlocked)
		elif state == "open" and mode == "off_session":
			frappe.throw(MSG_LINK_OPEN_OFF_SESSION, PaymentBlocked)
		else:
			frappe.throw(MSG_LINK_STILL_OPEN_DESK if desk else MSG_LINK_STILL_OPEN, LinkStillOpen)
		if not changed and current in (PROCESSING, PAID):
			# The webhook got there first: the link was paid, and the invoice is being paid.
			frappe.throw(in_flight, PaymentBlocked)


def _settle_link(stripe_payment, to_status, note, commit):
	"""Move a ``Link Sent`` row on — only if it still is ``Link Sent``, read under its row
	lock (``FOR UPDATE``), so a ``checkout.session.completed`` webhook that marked it Paid
	(or the ACH Processing) in the meantime is never overwritten; that webhook's own locking
	read waits for this one. Returns ``(changed, status it was found in)``."""
	current = frappe.db.get_value("Stripe Payment", stripe_payment, "status", for_update=True)
	if current != "Link Sent":
		return False, current
	values = {"status": to_status}
	if note:
		values["error_message"] = note
	frappe.db.set_value("Stripe Payment", stripe_payment, values)
	if commit:
		frappe.db.commit()
	return True, current


def _session_state(session_id, expire=True):
	"""``"expired"``, ``"complete"``, ``"open"``, ``"missing"`` (Stripe answered 404: no such
	session for this key) or ``None`` (Stripe did not answer): the session's state after
	asking Stripe to expire it (``expire``), or just reading it."""
	missing = False
	if expire:
		try:
			session = expire_checkout_session(session_id, timeout=CONTROL_TIMEOUT) or {}
		except Exception as exc:
			# Already complete, already expired, gone, or no answer: read it back to know which.
			missing = is_missing(exc)
			if not missing:
				frappe.log_error(
					error_snippet(frappe.get_traceback()), "Stripe: Checkout Session expire refused"
				)
			session = {}
		if session.get("status") == "expired":
			return "expired"
	try:
		session = retrieve_checkout_session(session_id, timeout=CONTROL_TIMEOUT) or {}
	except Exception as exc:
		if missing or is_missing(exc):
			return "missing"
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: Checkout Session lookup failed")
		return None
	return session.get("status")


def link_is_stale(sales_invoice, amount) -> bool:
	"""Whether an emailed link for ``amount`` no longer matches what the invoice owes:
	canceled or amended, or settled in part or in full some other way (a cheque, a credit
	note, the QuickBooks sync). Paying it then would be a second payment or an overpayment."""
	si = frappe.db.get_value(
		"Sales Invoice", sales_invoice, ["docstatus", "outstanding_amount"], as_dict=True
	)
	return not si or cint(si.docstatus) != 1 or flt(si.outstanding_amount, 2) != flt(amount, 2)


def expire_stale_link(sp) -> bool:
	"""poll_pending's backstop for an emailed link that outlived its invoice
	(:func:`link_is_stale`). Nothing else notices those: a cancel expires the invoice's links
	first (:func:`before_invoice_cancel`), but a cheque or a credit note posted while a link
	is open does not, and the link would stay payable for the rest of its 24 hours.

	Expired at Stripe under the invoice lock, on a fresh read. A session that turns out to be
	complete was paid for an invoice that no longer owes it: marked ``Processing`` for the
	webhook, and Accounts are told, since that money needs a refund or reallocating.
	``True`` when the row was settled."""
	invoice = sp.get("sales_invoice")
	session_id = sp.get("stripe_checkout_session")
	if sp.status != "Link Sent" or not invoice or not session_id or not link_is_stale(invoice, sp.amount):
		return False
	try:
		with invoice_lock(invoice):
			if frappe.db.get_value("Stripe Payment", sp.name, "status") != "Link Sent":
				return False
			if not link_is_stale(invoice, sp.amount):
				return False
			state = _session_state(session_id)
			if state == "expired":
				return _settle_link(sp.name, "Expired", NOTE_LINK_STALE, True)[0]
			if state == "missing":
				changed = _settle_link(sp.name, "Expired", NOTE_MISSING_SESSION, True)[0]
				if changed:
					_alert_missing(sp.name, "Checkout Session", session_id)
				return changed
			if state == "complete" and _settle_link(sp.name, "Processing", None, True)[0]:
				_alert_accounts(
					f"Stripe link paid for a settled invoice: {sp.name}",
					f"The customer completed Stripe payment link {sp.name} for {invoice} after the "
					"invoice was canceled, amended or settled another way. The payment will reach "
					"the Stripe ledger; refund it in Stripe or allocate it by hand.",
					"Stripe Payment",
					sp.name,
				)
				return True
			return False  # open (Stripe would not expire it) or no answer: the next run tries again
	except PaymentBlocked:
		return False  # a payment for the invoice is starting right now; the next run looks again


# --- canceling an invoice ---------------------------------------------------------


def before_invoice_cancel(doc, method=None):
	"""Sales Invoice ``before_cancel``: never cancel an invoice a Stripe payment is still in
	flight for, and close its emailed links first.

	Frappe blocks a cancel only for *submitted* linked documents, and a Stripe Payment is
	never submitted, so Accounts could cancel an invoice while an ACH debit for it settled
	for days, a card charge's Payment Entry could not post, or 3-D Secure was under way —
	and the amended copy has a new name that no guard keyed on. Autopay then charged the
	copy on submit, the original payment landed on a canceled invoice, and the customer paid
	twice. So the cancel is refused while any ``Processing`` Stripe payment for the invoice
	remains after the guard's own release (a dead card attempt is canceled at Stripe and
	failed; 3-D Secure inside its window is not), with a reason for each verdict; and every
	``Link Sent`` Checkout Session for it is expired — a completed one refuses the cancel.
	A ``Paid`` row does not: ERPNext's own linked-Payment-Entry check governs that, and the
	amended copy's guard reads the original's rows (:func:`_invoice_family`).

	No invoice lock, and **no commit**: this runs inside the cancel's transaction, after
	ERPNext's own ``before_cancel`` has written (the Timesheet unlinking), and committing
	here would make those writes durable even if the cancel then failed. Serialization comes
	from the Sales Invoice row lock instead. The cancel took it (``check_if_latest``'s
	``SELECT … FOR UPDATE``) before this runs, and every payment path takes it just before
	committing its attempt in flight (:func:`_recheck_invoice` with ``for_update``). A
	payment that got it first has committed by the time the cancel gets here, and the
	ledger is read with a locking read — the latest committed rows, not this old
	transaction's snapshot — so its row is seen; a payment that comes second waits for the
	cancel's commit and then reads the invoice canceled. Stripe-side actions taken here (a
	PaymentIntent canceled, a session expired) survive a cancel that fails later; the rows
	then read ``Processing`` / ``Link Sent`` again until poll_pending or the webhook
	brings them up to date, which is harmless.

	Defensive, as every doc_event here: a no-op while the integration is off or not yet
	installed (ERPNext's own test bootstrap cancels invoices before this app's tables exist).
	"""
	try:
		if not is_enabled():
			return
	except Exception:
		return
	name = doc.name
	rows = _ledger_rows([name], PROCESSING, _GUARD_FIELDS, for_update=True)
	if rows:
		read = _status_reader(CONTROL_TIMEOUT, quiet=False)
		block = _block_from_rows(name, rows, True, read, commit=False, canceling=True)
		if block:
			frappe.throw(_cancel_message(block, rows), PaymentBlocked)
	_close_open_checkouts(name, mode="cancel")


def _cancel_message(block, rows) -> str:
	payments = ", ".join(sorted(row.name for row in rows)[:3])
	if block == UNRELEASED:
		# Not "in progress", and no money is on its way: never "wait for it to settle".
		return MSG_CANCEL_UNRELEASED.format(payments=f"Stripe Payment {payments}")
	if block == AWAITING:
		detail = CANCEL_DETAIL_AWAITING.format(minutes=_minutes_phrase(block))
	elif block == UNCONFIRMED:
		detail = CANCEL_DETAIL_UNCONFIRMED
	elif block == VERIFYING:
		detail = CANCEL_DETAIL_VERIFYING
	else:
		detail = CANCEL_DETAIL_PROCESSING
	return MSG_CANCEL_IN_FLIGHT.format(payments=f"Stripe Payment {payments}", detail=detail)


# --- settling what poll_pending finds -------------------------------------------


def outcome_unknown(sp) -> bool:
	"""A charge row committed ``Processing`` ahead of its Stripe call whose PaymentIntent we
	never learned: the call got no answer, or the worker died mid-request. Hosted Checkout
	rows always carry their session and are never this."""
	return (
		sp.status == "Processing"
		and not sp.get("stripe_payment_intent")
		and not sp.get("stripe_checkout_session")
	)


def held_unknown(row) -> bool:
	"""Whether this row's charge was held with an unknown outcome — the charge whose payer
	``/stripe-return`` told "please do not pay again for now: if it did not go through, we will
	email you". Either its PaymentIntent is still unknown (:func:`outcome_unknown`), or
	poll_pending has since found it and left :data:`NOTE_OUTCOME_FOUND` on the row. Takes a
	Stripe Payment doc or a ledger row (the guard's, settle_dead_attempt's, or the one
	``www/stripe_return.py`` reads, which words the page from it)."""
	value = row.get if hasattr(row, "get") else (lambda key: getattr(row, key, None))
	if value("error_message") == NOTE_OUTCOME_FOUND:
		return True
	return (
		value("status") == PROCESSING
		and not value("stripe_payment_intent")
		and not value("stripe_checkout_session")
	)


def find_payment_intent(sp):
	"""``(payment_intent, conclusive)`` for a row whose charge outcome was unknown.

	Pages through the Stripe Customer's PaymentIntents (the list endpoint is strongly
	consistent; search is not) for the one whose ``metadata.stripe_payment`` is this row —
	both charge paths set it. The window starts a day before the row was created: the row's
	stamp is site-local and Stripe's is UTC, and a day covers any offset. ``conclusive`` is
	``False`` when the list could not be finished (no Stripe Customer on the row, or more
	pages than :data:`PAYMENT_INTENT_LIST_PAGES`): absence then proves nothing. A Stripe
	Customer that does not exist for this key (404) has no PaymentIntents under it either:
	conclusive.
	"""
	customer = sp.get("stripe_customer_id")
	if not customer:
		return None, False
	created_gte = _epoch_day_before(sp.get("creation"))
	starting_after = None
	for _ in range(PAYMENT_INTENT_LIST_PAGES):
		try:
			page = (
				list_payment_intents(
					customer, created_gte=created_gte, starting_after=starting_after, timeout=CONTROL_TIMEOUT
				)
				or {}
			)
		except Exception as exc:
			if is_missing(exc):
				return None, True
			raise
		data = page.get("data") or []
		for pi in data:
			if (pi.get("metadata") or {}).get("stripe_payment") == sp.name:
				return pi, True
		if not page.get("has_more") or not data:
			return None, True
		starting_after = data[-1].get("id")
	return None, False


def _epoch_day_before(stamp):
	if not stamp:
		return None
	try:
		return calendar.timegm(get_datetime(stamp).timetuple()) - 86400
	except Exception:
		return None


def resolve_unknown_outcome(sp):
	"""Settle a row whose charge outcome was unknown (poll_pending, 15+ minutes after it
	last changed — long past any request Stripe could still be running).

	Found at Stripe: the PaymentIntent id is recorded and returned, and the caller settles
	it like any other (succeeded posts; dead is released; 3-D Secure waits out its window).
	The row keeps a marker (:data:`NOTE_OUTCOME_FOUND`), so that if it is released as unable
	to charge — now, or after the 3-D Secure window — whoever was waiting is told it did not
	go through (:func:`notify_released_unknown`), as the card page's payer was promised.
	Proven absent: nothing reached Stripe, so nothing was charged — the row is marked
	``Expired`` (never ``Failed``: dunning enrols a Failed autopay row as a declined card, and
	emails the customer so) and the invoice is payable again; autopay's sweep charges again,
	and whoever was told "being processed" is told it did not go through
	(:func:`_notify_never_charged`). Not provable either way: left blocking, for the next run.
	"""
	pi, conclusive = find_payment_intent(sp)
	if pi:
		sp.db_set({"stripe_payment_intent": pi.get("id"), "error_message": NOTE_OUTCOME_FOUND})
		frappe.db.commit()
		return pi
	if conclusive:
		sp.db_set({"status": "Expired", "error_message": NOTE_NEVER_REACHED})
		_unstamp_if_clear(sp.get("sales_invoice"))
		frappe.db.commit()
		_notify_never_charged(sp)
	return None


def _notify_never_charged(sp):
	"""Tell whoever is waiting that a charge whose outcome was unknown provably never
	happened (no PaymentIntent exists for it). Best-effort: nothing here may undo the
	settlement."""
	_notify_not_charged(
		sp,
		f"Stripe charge never reached Stripe: {sp.name}",
		"poll_pending has now read the customer's PaymentIntents and there is none for it, so nothing "
		"was charged. The row is marked Expired",
	)


def notify_released_unknown(stripe_payment, pi_status=None, invoice_canceled=False):
	"""Tell whoever is waiting that a charge whose outcome was unknown turned out unable to
	charge: its PaymentIntent, found at Stripe by poll_pending, was declined, never confirmed,
	or in 3-D Secure nobody finished (the payer never saw the challenge: the answer carrying it
	was lost), and was released — by poll_pending, by the next payment someone started for
	the invoice, or by canceling the invoice (``invoice_canceled``); or the
	``payment_intent.payment_failed`` webhook reported it declined first. Without this the
	payer, told "we will email you if it did not go through", heard nothing while the invoice
	quietly became payable again. Best-effort: nothing here may undo the release."""
	try:
		sp = frappe.get_doc("Stripe Payment", stripe_payment)
		if pi_status == AWAITING_PAYER_PI_STATE:
			why = "3-D Secure was never completed; canceled at Stripe"
		elif pi_status:
			why = f"PaymentIntent {pi_status}"
		else:
			why = "it could not charge"
		_notify_not_charged(
			sp,
			f"Stripe charge did not go through: {sp.name}",
			f"Stripe has since reported its PaymentIntent, and it could not charge ({why}), so "
			"nothing was charged. The row is marked Failed",
			invoice_canceled=invoice_canceled,
		)
	except Exception:
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: not-charged notice failed")


def _notify_not_charged(sp, subject, finding, invoice_canceled=False):
	"""Accounts hear of every charge whose outcome was unknown and then proved not charged.
	The card page's payer was told they would be emailed if it did not go through, so a
	Portal payer is emailed — unless they are the one whose request settled it (starting a
	new payment for the invoice is what released the old attempt): they are paying again as it
	lands, and a "your payment did not go through" email for the same invoice and amount could
	be taken for the new payment. (They saw the invoice ready to pay again, which
	``/stripe-return`` tells them is the other way they may learn of it; the owner confirmed
	this, 2026-09-24.) Autopay and dunning need no word to the customer: nothing was taken, and
	the next run charges again.

	The amount quoted is the total the payer approved — the invoice amount plus any surcharge.
	When the release came from canceling the invoice (``invoice_canceled``) nobody is told the
	invoice can be paid again: it is being canceled."""
	invoice = sp.get("sales_invoice")
	total = flt(sp.get("amount")) + flt(sp.get("surcharge_amount"))
	amount = fmt_money(total, currency=sp.get("currency") or "USD")
	what = f"invoice {invoice}" if invoice else (sp.get("description") or "a payment")
	if invoice_canceled:
		after = ". It was released as the invoice was canceled."
	else:
		after = " and the invoice can be paid again."
	_alert_accounts(
		subject,
		f"Stripe Payment {sp.name} ({amount} for {html.escape(what)}, channel "
		f"{sp.get('channel') or '—'}) had no answer from Stripe when it was charged. {html.escape(finding)}"
		f"{after}",
		"Stripe Payment",
		sp.name,
	)
	payer = sp.get("initiated_by")
	if sp.get("channel") == "Portal" and payer != getattr(frappe.session, "user", None):
		_email_payer_not_charged(sp, amount, what, invoice_canceled=invoice_canceled)


def _email_payer_not_charged(sp, amount, what, invoice_canceled=False):
	try:
		user = sp.get("initiated_by")
		if not user or user in ("Guest", "Administrator"):
			return
		email = frappe.db.get_value("User", user, "email") or (user if "@" in user else None)
		if not email:
			return
		from erpnext_enhancements import email_style

		subject = f"Your payment for {what} did not go through"
		link = get_url("/pay")
		if invoice_canceled:
			# Never "pay it again": the invoice is being canceled. A corrected one, if any, is
			# a new invoice, and shows in the list the button opens.
			next_step = (
				"That invoice has since been canceled, so no payment is due on it. If it is replaced "
				"by a corrected invoice, you will find that one with your invoices."
			)
		else:
			next_step = "You can pay it from your invoices whenever you are ready."
		body = (
			email_style.p("Hello,")
			+ email_style.p(
				f"We could not complete your card payment of {amount} for {what}. Nothing was charged "
				"to your card."
			)
			+ email_style.p(next_step)
			+ email_style.button(link, "View invoices")
			+ email_style.button_fallback(link)
			+ email_style.p("Thank you, Sapphire Fountains")
		)
		frappe.sendmail(
			recipients=[email],
			subject=subject,
			message=email_style.wrap(body, title=subject, eyebrow="Billing", tagline=True),
		)
	except Exception:
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: not-charged email failed")


def settle_dead_attempt(sp, pi_status) -> bool:
	"""poll_pending's release of a card or off-session attempt that can no longer charge
	(:func:`_cannot_charge`: dead, missing at Stripe, 3-D Secure untouched for
	:data:`ABANDON_AFTER_MINUTES`, or an off-session card challenge nobody is there to
	finish), so an abandoned attempt does not leave its invoice "being processed" for good.
	The same cancel-then-fail as the guard's release, under the invoice lock and on a fresh
	read of the row. Never a hosted Checkout row, and never an off-session bank debit in
	``requires_action``: that is microdeposit verification, and a payment. ``True`` when
	released. A row whose charge had been held with an unknown outcome has its payer emailed
	that it did not go through (:func:`_release_attempt`).
	"""
	if sp.status != "Processing" or sp.get("stripe_checkout_session") or not sp.get("stripe_payment_intent"):
		return False
	row = types.SimpleNamespace(
		name=sp.name,
		status=sp.status,
		stripe_payment_intent=sp.stripe_payment_intent,
		stripe_checkout_session=None,
		confirmation_token=sp.get("confirmation_token"),
		payment_method_type=sp.get("payment_method_type"),
		modified=sp.get("modified"),
		error_message=sp.get("error_message"),
	)
	if not _cannot_charge(row, pi_status):
		return False
	try:
		with invoice_lock(sp.get("sales_invoice")):
			current = frappe.db.get_value(
				"Stripe Payment", sp.name, ["status", "modified", "error_message"], as_dict=True
			)
			if not current or current.status != "Processing":
				return False
			row.modified = current.modified
			row.error_message = current.error_message
			if not _cannot_charge(row, pi_status) or not _release_attempt(row, pi_status)[0]:
				return False
			_unstamp_if_clear(sp.get("sales_invoice"))
			frappe.db.commit()
			return True
	except PaymentBlocked:
		return False  # a payment for the invoice is starting right now; the next run looks again


def settle_missing(sp, kind) -> bool:
	"""poll_pending: Stripe answered 404 for this row's PaymentIntent (``kind``
	"PaymentIntent") or Checkout Session ("Checkout Session") — it does not exist for the
	configured key (the site was switched between Test and Live, or to another account, or a
	database restored onto a differently keyed site). Asking again every hour gets the same
	answer and keeps the invoice blocked, and unpayable, for ever; so the row is marked
	``Expired`` under the invoice lock and Accounts are told. ``True`` when settled."""
	note = NOTE_MISSING_PI if kind == "PaymentIntent" else NOTE_MISSING_SESSION
	stripe_id = (
		sp.get("stripe_payment_intent") if kind == "PaymentIntent" else sp.get("stripe_checkout_session")
	)
	try:
		with invoice_lock(sp.get("sales_invoice")):
			if frappe.db.get_value("Stripe Payment", sp.name, "status") not in (PROCESSING, "Link Sent"):
				return False
			frappe.db.set_value("Stripe Payment", sp.name, {"status": "Expired", "error_message": note})
			_unstamp_if_clear(sp.get("sales_invoice"))
			frappe.db.commit()
	except PaymentBlocked:
		return False
	_alert_missing(sp.name, kind, stripe_id)
	return True


def _unstamp_if_clear(sales_invoice):
	"""Re-stamp the invoice "Unpaid" once nothing else is paying it."""
	if sales_invoice and not frappe.db.exists(
		"Stripe Payment",
		{"sales_invoice": sales_invoice, "status": ["in", ["Processing", "Paid", "Link Sent"]]},
	):
		_stamp_invoice(sales_invoice, "Unpaid")


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
