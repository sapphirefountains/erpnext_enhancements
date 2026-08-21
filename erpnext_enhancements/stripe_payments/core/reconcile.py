# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Turn verified Stripe webhook events into ERPNext accounting records.

``process_event`` runs in the background (enqueued by ``webhooks.handle_webhook``).
It loads the stored ``Stripe Event``, dispatches on the event type, and advances the
matching ``Stripe Payment`` through its lifecycle. The terminal success path,
``finalize_payment``, builds and submits a Payment Entry exactly like the QuickBooks
module's ``_map_payment_entry`` (Receive / Customer / deposit account / invoice
allocation), guarded so a redelivered or duplicated event never double-posts.

ACH (us_bank_account) is a delayed-notification method: ``checkout.session.completed``
arrives first with ``payment_status != "paid"`` (mark Processing), then
``checkout.session.async_payment_succeeded`` clears it (finalize). Cards finalize
straight from ``checkout.session.completed``.
"""

from __future__ import annotations

import contextlib
import json

import frappe
from frappe.utils import cint, flt, now_datetime, today

from erpnext_enhancements.stripe_payments.core.checkout import (
	NON_SURCHARGEABLE_FUNDING,
	_stamp_invoice,
)
from erpnext_enhancements.stripe_payments.core.utils import (
	error_snippet,
	from_minor_units,
	get_settings,
	to_minor_units,
)

# Events we act on; anything else is recorded and marked Ignored.
HANDLED = {
	"checkout.session.completed",
	"checkout.session.async_payment_succeeded",
	"checkout.session.async_payment_failed",
	"checkout.session.expired",
	"payment_intent.succeeded",
	"payment_intent.payment_failed",
	"charge.refunded",
	"payout.paid",
	"payout.failed",
}


@contextlib.contextmanager
def _reconcile_as_system_user():
	"""Run the reconciliation body as a system user.

	The Stripe webhook route is ``allow_guest``, so ``process_event`` is enqueued from
	a Guest session and the background job inherits it. ``get_payment_entry`` does a
	permission-checked read of the Sales Invoice, which raises ``PermissionError`` for
	Guest — silently failing every unattended charge. Elevate a Guest session to
	Administrator for the duration and always restore the caller's user; non-guest
	callers (the scheduled retry, a manual reprocess) are left untouched.
	"""
	original_user = frappe.session.user
	switched = original_user == "Guest"
	if switched:
		frappe.set_user("Administrator")
	try:
		yield
	finally:
		if switched:
			frappe.set_user(original_user)


def process_event(event_name: str):
	"""Dispatch one stored ``Stripe Event`` by type; record the outcome on the doc.

	Runs as a system user (see :func:`_reconcile_as_system_user`) so the guest webhook
	context cannot trip the Sales Invoice permission check. Idempotent: re-running for
	an already-processed event is a no-op, and the terminal ``finalize_payment``
	dedupes against existing Payment Entries.
	"""
	with _reconcile_as_system_user():
		_dispatch_event(event_name)


def _dispatch_event(event_name: str):
	event = frappe.get_doc("Stripe Event", event_name)
	if event.processed:
		return
	event_type = event.event_type
	obj = (json.loads(event.payload or "{}").get("data") or {}).get("object") or {}

	try:
		if event_type not in HANDLED:
			event.db_set({"processed": 1, "process_status": "Ignored"})
			frappe.db.commit()
			return

		handler = {
			"checkout.session.completed": _on_session_completed,
			"checkout.session.async_payment_succeeded": _on_session_async_succeeded,
			"checkout.session.async_payment_failed": _on_session_async_failed,
			"checkout.session.expired": _on_session_expired,
			"payment_intent.succeeded": _on_payment_intent_succeeded,
			"payment_intent.payment_failed": _on_payment_intent_failed,
			"charge.refunded": _on_charge_refunded,
			"payout.paid": _on_payout_paid,
			"payout.failed": _on_payout_failed,
		}[event_type]
		sp = handler(obj)

		event.db_set(
			{
				"processed": 1,
				"process_status": "Processed",
				"stripe_payment": sp.name if sp else None,
				"error": None,
			}
		)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		tb = error_snippet(frappe.get_traceback())
		event.db_set({"processed": 0, "process_status": "Error", "error": tb})
		frappe.db.commit()
		frappe.log_error(tb, f"Stripe: failed to process {event_type} ({event_name})")
		raise


# --- event handlers ---------------------------------------------------------


def _on_session_completed(session):
	"""Checkout submitted. Setup: save the method. Cards: finalize. ACH: mark Processing."""
	if session.get("mode") == "setup":
		return _handle_setup_completed(session)
	sp = _find_payment(session)
	if not sp:
		return None
	_record_session_refs(sp, session)
	if session.get("payment_status") == "paid":
		finalize_payment(sp, session)
	else:
		# Delayed method (ACH) authorized but not yet settled.
		sp.db_set("status", "Processing")
		if sp.sales_invoice:
			_stamp_invoice(sp.sales_invoice, "Processing")
		frappe.db.commit()
	return sp


def _on_session_async_succeeded(session):
	"""Delayed (ACH) payment finally cleared -> finalize."""
	sp = _find_payment(session)
	if not sp:
		return None
	_record_session_refs(sp, session)
	finalize_payment(sp, session)
	return sp


def _on_session_async_failed(session):
	"""Delayed (ACH) payment failed -> mark Failed."""
	sp = _find_payment(session)
	if not sp:
		return None
	if sp.status != "Paid":
		sp.db_set("status", "Failed")
		sp.db_set("error_message", "ACH payment failed at the bank.")
		if sp.sales_invoice:
			_stamp_invoice(sp.sales_invoice, "Failed")
		frappe.db.commit()
	return sp


def _on_session_expired(session):
	"""Checkout Session expired (24h) before payment -> mark Expired if still open."""
	sp = _find_payment(session)
	if not sp:
		return None
	if sp.status in ("Draft", "Link Sent"):
		sp.db_set("status", "Expired")
		if sp.sales_invoice:
			_stamp_invoice(sp.sales_invoice, "Unpaid")
		frappe.db.commit()
	return sp


def _on_payment_intent_succeeded(pi):
	"""Backstop finalizer (also covers off-session charges in a later phase)."""
	sp = _find_payment(pi)
	if not sp or sp.status == "Paid":
		return sp
	finalize_payment(sp, pi)
	return sp


def _on_payment_intent_failed(pi):
	sp = _find_payment(pi)
	if not sp:
		return None
	if sp.status != "Paid":
		was_failed = sp.status == "Failed"
		err = (pi.get("last_payment_error") or {}).get("message") or "Payment failed."
		sp.db_set("status", "Failed")
		sp.db_set("error_message", error_snippet(err, 200))
		frappe.db.commit()
		# Async (e.g. ACH) failure of an automatic charge -> parity with the
		# synchronous card-decline path (alert Accounts + stamp the invoice).
		# Guard on the prior status so a redelivered webhook, or the sync path
		# having already alerted, never re-fires.
		if not was_failed and sp.get("channel") == "Auto":
			from erpnext_enhancements.stripe_payments.core.saved_methods import _alert_failed_autocharge

			_alert_failed_autocharge(sp, err)
	return sp


def _on_payout_paid(payout):
	"""A Stripe payout settled to the bank -> post the clearing-sweep Journal Entry.

	Returns None (a payout maps to a Journal Entry, not a Stripe Payment).
	"""
	from erpnext_enhancements.stripe_payments.core import payouts

	payouts.process_payout(payout)
	return None


def _on_payout_failed(payout):
	"""A payout failed/was reversed at the bank -> alert; no GL entry is posted."""
	from erpnext_enhancements.stripe_payments.core import payouts

	payouts._notify_review(
		None,
		payout.get("id"),
		[
			f"Stripe payout {payout.get('id')} for {from_minor_units(payout.get('amount'))} "
			f"{(payout.get('currency') or 'usd').upper()} FAILED at the bank "
			f"({payout.get('failure_message') or payout.get('failure_code') or 'no reason given'}). "
			"No Journal Entry was posted; investigate in the Stripe dashboard."
		],
	)
	frappe.db.commit()
	return None


def _on_charge_refunded(charge):
	"""Record a refund against the Stripe Payment (no Payment Entry reversal yet)."""
	sp = _find_payment(charge)
	if not sp:
		return None
	refunded = from_minor_units(charge.get("amount_refunded"))
	# A voided surcharge is our own compliance correction, not a customer refund —
	# netting it out keeps amount_refunded meaning "money given back on the sale",
	# and stops a fully-collected payment from reading as partially refunded.
	if cint(sp.get("surcharge_voided")):
		refunded = max(refunded - flt(sp.get("surcharge_amount")), 0)
	sp.db_set("amount_refunded", refunded)
	# Stripe's own "fully refunded" flag still decides, except when the only thing
	# refunded was the voided surcharge — that leaves the sale fully collected.
	fully_refunded = bool(charge.get("refunded")) and not cint(sp.get("surcharge_voided"))
	if fully_refunded or refunded >= flt(sp.amount) > 0:
		sp.db_set("status", "Refunded")
	frappe.db.commit()
	return sp


# --- finalization -----------------------------------------------------------


def finalize_payment(sp, source_obj):
	"""Create and submit the Payment Entry for a successful Stripe Payment.

	Idempotent, and now race-safe. Stripe delivers TWO signals for one card payment —
	``checkout.session.completed`` and ``payment_intent.succeeded`` — as separate enqueued
	jobs, and the hourly ``poll_pending`` is a third caller. The dedup here is check-then-act,
	and a concurrent worker's uncommitted Payment Entry is invisible under REPEATABLE READ, so
	without serialization two submitted Receive Payment Entries could allocate against the same
	Sales Invoice. A ``filelock`` (keyed on the Stripe Payment, mirroring ``process_payout``)
	serializes the callers; the ``for_update`` reads are current reads that see the prior
	holder's committed work despite the snapshot; and the commit inside the lock makes that work
	durable before the lock is released.
	"""
	from frappe.utils.synchronization import filelock

	with filelock(f"stripe_finalize_{sp.name}", timeout=30):
		current = (
			frappe.db.get_value(
				"Stripe Payment", sp.name, ["status", "payment_entry"], as_dict=True, for_update=True
			)
			or frappe._dict()
		)
		if current.get("status") == "Paid" and current.get("payment_entry"):
			return current.payment_entry

		pi_id = sp.stripe_payment_intent or _extract_payment_intent(source_obj)
		charge_id, method_type, funding = _enrich(source_obj, pi_id)

		if pi_id:
			existing = frappe.db.get_value(
				"Payment Entry",
				{"custom_stripe_payment_intent": pi_id, "docstatus": ["<", 2]},
				"name",
				for_update=True,
			)
			if existing:
				_mark_paid(sp, existing, pi_id, charge_id, method_type, funding)
				frappe.db.commit()
				return existing

		pe_name = _create_payment_entry(sp, pi_id, charge_id, method_type, funding)
		_mark_paid(sp, pe_name, pi_id, charge_id, method_type, funding)
		frappe.db.commit()
		return pe_name


def _create_payment_entry(sp, pi_id, charge_id, method_type, funding=None) -> str:
	"""Build + submit a Receive Payment Entry; allocate to the invoice if linked."""
	settings = get_settings()
	if not settings.deposit_account:
		frappe.throw("Stripe Deposit / Clearing Account is not set in Stripe Payments Settings.")

	mode_of_payment = (
		settings.ach_mode_of_payment
		if method_type == "us_bank_account"
		else settings.card_mode_of_payment
	)
	reference_no = pi_id or sp.stripe_checkout_session or sp.name

	if sp.sales_invoice:
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

		pe = get_payment_entry(
			"Sales Invoice",
			sp.sales_invoice,
			party_amount=flt(sp.amount),
			bank_account=settings.deposit_account,
		)
		# Ensure the deposit account wins regardless of get_payment_entry's default.
		pe.paid_to = settings.deposit_account
	else:
		company = settings.company or frappe.defaults.get_user_default("Company")
		pe = frappe.new_doc("Payment Entry")
		pe.payment_type = "Receive"
		pe.company = company
		pe.party_type = "Customer"
		pe.party = sp.customer
		pe.paid_from = frappe.get_cached_value("Company", company, "default_receivable_account")
		pe.paid_to = settings.deposit_account
		pe.paid_amount = flt(sp.amount)
		pe.received_amount = flt(sp.amount)
		pe.source_exchange_rate = 1
		pe.target_exchange_rate = 1

	pe.mode_of_payment = mode_of_payment
	pe.reference_no = reference_no
	pe.reference_date = today()
	pe.custom_stripe_payment_intent = pi_id
	pe.custom_stripe_charge_id = charge_id
	pe.remarks = f"Stripe payment {sp.name} ({reference_no})"
	pe.flags.ignore_permissions = True
	pe.insert()
	pe.submit()
	# A collected surcharge is booked as income by a companion Journal Entry, not on
	# this Payment Entry: erpnext forces received == paid on a same-currency Receive,
	# so a surcharge cannot ride on the invoice PE as a deduction (see _book_surcharge).
	_void_surcharge_if_not_credit(sp, funding)
	_book_surcharge(sp, pe, charge_id, pi_id)
	return pe.name


def _surcharge_je_legs(deposit_account, income_account, surcharge, cost_center):
	"""The two balanced Journal Entry legs for a collected surcharge (pure function).

	Dr deposit/clearing, Cr surcharge income — equal amounts, so the entry always
	balances. Split out so the leg construction is unit-tested without a bench.
	"""
	amount = flt(surcharge, 2)
	return [
		{
			"account": deposit_account,
			"cost_center": cost_center,
			"debit_in_account_currency": amount,
			"credit_in_account_currency": 0,
		},
		{
			"account": income_account,
			"cost_center": cost_center,
			"debit_in_account_currency": 0,
			"credit_in_account_currency": amount,
		},
	]


def _void_surcharge_if_not_credit(sp, funding):
	"""Backstop: never keep a surcharge collected from a card that isn't credit.

	Surcharges are priced only where the funding type is known *before* charging
	(``saved_methods.charge_saved_method`` reads the saved PaymentMethod first;
	hosted Checkout never surcharges at all), so this should not fire. It exists for
	the case where the instrument that actually charged differs from the one we
	priced — the fee has already been collected by then, and the only remedy is to
	give it back.

	Fires only on a **positively known** non-surchargeable funding type. ``None``
	means we could not determine funding — usually a failed API lookup — and is
	deliberately *not* treated as a violation: refunding on missing data would claw
	back legitimate credit surcharges on every Stripe blip. That case alerts instead
	and leaves the booking alone.

	Sets the flag inside the reconciliation transaction (so it commits or rolls back
	with the Payment Entry) and enqueues the refund separately after commit, keeping
	the Payment Entry atomic and making the refund independently retryable.
	``_book_surcharge`` no-ops once the flag is set, so the fee is never booked to
	income as well as refunded.
	"""
	surcharge = flt(getattr(sp, "surcharge_amount", 0))
	if surcharge <= 0 or cint(getattr(sp, "surcharge_voided", 0)):
		return
	if funding is None:
		frappe.log_error(
			f"Stripe Payment {sp.name} collected a {surcharge} surcharge but the card's funding "
			"type could not be determined. Verify in the Stripe dashboard that it was a credit "
			"card; if it was debit or prepaid, refund the surcharge manually.",
			"Stripe: surcharge booked with unknown funding type",
		)
		return
	if funding not in NON_SURCHARGEABLE_FUNDING:
		return

	sp.db_set("surcharge_voided", 1)
	frappe.enqueue(
		"erpnext_enhancements.stripe_payments.core.reconcile.refund_voided_surcharge",
		queue="short",
		enqueue_after_commit=True,
		stripe_payment=sp.name,
	)


def refund_voided_surcharge(stripe_payment: str):
	"""Refund a surcharge collected from a non-credit card (background job).

	Idempotent three ways, in order of authority: it no-ops unless ``surcharge_voided``
	is set, it no-ops once ``surcharge_refund_id`` is recorded, and ``create_refund``
	sends a deterministic Idempotency-Key. The local flags are what actually protect
	a redelivered webhook — Stripe's idempotency keys expire after 24 hours, so a
	late redelivery would otherwise issue a *second* refund.
	"""
	sp = frappe.get_doc("Stripe Payment", stripe_payment)
	if not cint(sp.get("surcharge_voided")) or sp.get("surcharge_refund_id"):
		return
	surcharge = flt(sp.get("surcharge_amount"))
	if surcharge <= 0 or not sp.stripe_payment_intent:
		return

	from erpnext_enhancements.stripe_payments.core.client import create_refund

	refund = create_refund(
		sp.stripe_payment_intent, amount_minor=to_minor_units(surcharge, sp.currency)
	)
	sp.db_set("surcharge_refund_id", refund.get("id"))
	frappe.db.commit()
	_alert_surcharge_voided(sp, surcharge)


def _alert_surcharge_voided(sp, surcharge):
	"""Tell Accounts a surcharge was refunded because the card wasn't credit.

	Best-effort — the refund has already happened and is recorded on the row; this
	is the human-visible trail. Reaching this state means a payment was priced as
	credit and charged as something else, which is worth investigating.
	"""
	try:
		from erpnext_enhancements.stripe_payments.core.payouts import _accounts_managers

		amount = f"{surcharge} {(sp.currency or 'USD')}"
		subject = f"Stripe surcharge refunded: {sp.customer} ({amount})"
		content = (
			f"Stripe Payment {sp.name} collected a {amount} surcharge, but the card that actually "
			f"charged was <b>{frappe.utils.escape_html(sp.get('card_funding') or 'not credit')}</b>. "
			"Card-network rules prohibit surcharging debit and prepaid cards, so the fee was "
			"automatically refunded and was <b>not</b> booked to surcharge income.<br><br>"
			"The invoice payment itself was unaffected. Investigate why the payment was priced as "
			"credit — the saved payment method may have been replaced since it was stored."
		)
		for user in _accounts_managers():
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": subject,
					"email_content": content,
					"document_type": "Stripe Payment",
					"document_name": sp.name,
					"for_user": user,
					"type": "Alert",
				}
			).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: surcharge-void alert failed")


def _book_surcharge(sp, pe, charge_id, pi_id):
	"""Book a collected surcharge as income via a companion Journal Entry.

	erpnext's Payment Entry cannot model ``received_amount > paid_amount`` on a
	same-currency Receive — ``set_amounts`` forces them equal, so a surcharge cannot
	ride on the invoice Payment Entry as a deduction (submit fails with "Difference
	Amount must be zero"). Instead the Payment Entry settles the invoice at face value
	and this JE moves the extra the customer paid into surcharge income::

	    Dr  Deposit / Clearing   surcharge
	        Cr  Surcharge Income      surcharge

	The deposit account ends at charge + surcharge — matching the real Stripe deposit
	that the payout sweep (WI-040) later moves to the bank. Idempotent on the Stripe
	charge/PaymentIntent id (JE ``cheque_no``) so a redelivered event never double-books.
	Posted inside the same transaction as the Payment Entry, so any failure rolls the
	whole reconciliation back together. Skipped entirely when the fee was voided by
	:func:`_void_surcharge_if_not_credit` — a refunded surcharge is not income.
	"""
	surcharge = flt(getattr(sp, "surcharge_amount", 0))
	if surcharge <= 0 or cint(getattr(sp, "surcharge_voided", 0)):
		return None
	settings = get_settings()
	if not settings.surcharge_income_account:
		frappe.throw("Stripe surcharge income account is not set, but a surcharge was collected.")

	key = f"stripe-surcharge:{charge_id or pi_id or sp.name}"
	existing = frappe.db.get_value("Journal Entry", {"cheque_no": key, "docstatus": ["<", 2]}, "name")
	if existing:
		return existing

	cost_center = frappe.get_cached_value("Company", pe.company, "cost_center")
	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = pe.company
	je.posting_date = pe.posting_date or today()
	je.cheque_no = key
	je.cheque_date = je.posting_date
	je.user_remark = (
		f"Stripe surcharge for {sp.name} ({charge_id or pi_id}); "
		f"companion to Payment Entry {pe.name}."
	)
	for leg in _surcharge_je_legs(
		settings.deposit_account, settings.surcharge_income_account, surcharge, cost_center
	):
		je.append("accounts", leg)
	je.flags.ignore_permissions = True
	je.insert()
	je.submit()
	return je.name


def _mark_paid(sp, pe_name, pi_id, charge_id, method_type, funding=None):
	"""Stamp the Stripe Payment + Sales Invoice as Paid and link the Payment Entry.

	Records ``card_funding`` so the surcharge decision stays auditable after the
	fact: whether a fee was charged is only defensible against the funding type the
	card actually turned out to be. Never overwrites a known value with ``None``
	(the off-session path already stamped it before charging).
	"""
	sp.db_set(
		{
			"status": "Paid",
			"payment_entry": pe_name,
			"stripe_payment_intent": pi_id,
			"stripe_charge_id": charge_id,
			"payment_method_type": method_type,
			"card_funding": funding or sp.get("card_funding"),
			"error_message": None,
		}
	)
	if sp.sales_invoice:
		_stamp_invoice(sp.sales_invoice, "Paid")
	frappe.db.commit()


# --- helpers ----------------------------------------------------------------


def _find_payment(obj):
	"""Locate the Stripe Payment for an event object via metadata, then ids."""
	metadata = obj.get("metadata") or {}
	name = metadata.get("stripe_payment") or obj.get("client_reference_id")
	if name and frappe.db.exists("Stripe Payment", name):
		return frappe.get_doc("Stripe Payment", name)

	# Fall back to matching on the Stripe ids we recorded.
	for field, value in (
		("stripe_checkout_session", obj.get("id") if obj.get("object") == "checkout.session" else None),
		("stripe_payment_intent", _extract_payment_intent(obj)),
	):
		if not value:
			continue
		found = frappe.db.get_value("Stripe Payment", {field: value}, "name")
		if found:
			return frappe.get_doc("Stripe Payment", found)
	return None


def _record_session_refs(sp, session):
	"""Persist the PaymentIntent id + method type from a Checkout Session."""
	updates = {}
	pi_id = _extract_payment_intent(session)
	if pi_id and not sp.stripe_payment_intent:
		updates["stripe_payment_intent"] = pi_id
	method_types = session.get("payment_method_types") or []
	if method_types and not sp.payment_method_type:
		updates["payment_method_type"] = method_types[0]
	if updates:
		sp.db_set(updates)
		frappe.db.commit()


def _extract_payment_intent(obj) -> str | None:
	"""Pull a PaymentIntent id out of a session / PI / charge object."""
	if not obj:
		return None
	if obj.get("object") == "payment_intent":
		return obj.get("id")
	pi = obj.get("payment_intent")
	# May be an id string or an expanded object.
	if isinstance(pi, dict):
		return pi.get("id")
	return pi


def _enrich(obj, pi_id):
	"""Best-effort ``(charge_id, method_type, card_funding)``, enriched via API.

	``card_funding`` ("credit"/"debit"/"prepaid"/"unknown") is what makes a surcharge
	defensible or not, so it is worth one lookup when a card charge arrives without
	it — and it costs no extra round-trip, since it rides on the same expanded
	``latest_charge`` we already fetch for the charge id.

	Never required: if the API call fails (offline / test), we return whatever the
	event object already carried so a Payment Entry can still post. Funding then
	stays ``None``, which every downstream consumer treats as "not known to be
	credit" rather than as a violation.
	"""
	charge_id = None
	method_type = None
	funding = None

	def _from_charge(charge):
		details = charge.get("payment_method_details") or {}
		return details.get("type"), (details.get("card") or {}).get("funding")

	if obj.get("object") == "charge":
		charge_id = obj.get("id")
		method_type, funding = _from_charge(obj)
	elif obj.get("object") == "payment_intent":
		latest = obj.get("latest_charge")
		if isinstance(latest, dict):
			charge_id = latest.get("id")
			method_type, funding = _from_charge(latest)
		else:
			charge_id = latest
	elif obj.get("object") == "checkout.session":
		types = obj.get("payment_method_types") or []
		method_type = types[0] if types else None

	# Fetch when anything is still missing. A card charge with no funding yet counts
	# as missing; a bank debit has no funding to find, so it doesn't.
	needs_funding = funding is None and method_type in (None, "card")
	if pi_id and (not charge_id or not method_type or needs_funding):
		try:
			from erpnext_enhancements.stripe_payments.core.client import retrieve_payment_intent

			pi = retrieve_payment_intent(pi_id)
			latest = pi.get("latest_charge")
			if isinstance(latest, dict):
				charge_type, charge_funding = _from_charge(latest)
				charge_id = charge_id or latest.get("id")
				method_type = method_type or charge_type
				funding = funding or charge_funding
			else:
				charge_id = charge_id or latest
		except Exception:
			pass

	return charge_id, method_type, funding


def _handle_setup_completed(session):
	"""Store the saved payment method on the Customer after a setup-mode Checkout."""
	customer = (session.get("metadata") or {}).get("erpnext_customer")
	setup_intent = session.get("setup_intent")
	if not customer or not setup_intent or not frappe.db.exists("Customer", customer):
		return None

	from erpnext_enhancements.stripe_payments.core.client import retrieve_setup_intent

	si = retrieve_setup_intent(setup_intent if isinstance(setup_intent, str) else setup_intent.get("id"))
	pm = si.get("payment_method")
	pm_obj = pm if isinstance(pm, dict) else None
	pm_id = pm_obj.get("id") if pm_obj else pm
	if not pm_id:
		return None

	frappe.db.set_value(
		"Customer",
		customer,
		{
			"custom_stripe_default_payment_method": pm_id,
			"custom_stripe_payment_method_label": _pm_label(pm_obj),
			"custom_stripe_autopay_enabled": 1,
		},
	)
	# Activate the proof-of-authorization recorded at enrollment.
	consent = frappe.db.get_value(
		"Stripe Autopay Consent", {"setup_session": session.get("id"), "status": "Pending"}, "name"
	) or frappe.db.get_value(
		"Stripe Autopay Consent",
		{"customer": customer, "status": "Pending"},
		"name",
		order_by="creation desc",
	)
	if consent:
		frappe.db.set_value(
			"Stripe Autopay Consent",
			consent,
			{"status": "Active", "payment_method": pm_id, "activated_on": now_datetime()},
		)
	_close_esign_autopay(session.get("id"))
	frappe.db.commit()
	return None


def _close_esign_autopay(setup_session):
	"""Mark a contract-signing autopay enrolment complete.

	Enrolment offered at signing is recorded on the Contract Signature Request as
	"Started"; only Stripe can tell us it finished. Best-effort — this is a
	reporting stamp, and the authoritative record of the saved card is the
	Customer's own fields plus the consent row above.
	"""
	if not setup_session:
		return
	try:
		name = frappe.db.get_value(
			"Contract Signature Request", {"autopay_setup_session": setup_session}, "name"
		)
		if name:
			frappe.db.set_value(
				"Contract Signature Request", name, "autopay_outcome", "Enrolled", update_modified=False
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Contract e-sign: autopay completion stamp failed")


def _pm_label(pm):
	"""Human label for a saved payment method, e.g. 'Visa •••• 4242'."""
	if not pm:
		return None
	kind = pm.get("type")
	if kind == "card":
		card = pm.get("card") or {}
		return f"{(card.get('brand') or 'Card').title()} •••• {card.get('last4', '')}".strip()
	if kind == "us_bank_account":
		bank = pm.get("us_bank_account") or {}
		return f"{bank.get('bank_name', 'Bank')} •••• {bank.get('last4', '')}".strip()
	return kind
