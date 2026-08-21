# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Saved payment methods + off-session charging (Phase 2).

Two flows, both mirroring ``checkout.py`` conventions and reusing the reconciler to
post Payment Entries:

* :func:`create_setup_session` — a Checkout Session in ``setup`` mode that saves a
  customer's card/bank **with consent** and **no charge**; the webhook
  (``reconcile._handle_setup_completed``) then stores the payment method on the
  Customer.
* :func:`charge_saved_method` — charge a customer's saved method **off-session**
  (e.g. for a maintenance invoice), confirming a PaymentIntent immediately and
  finalizing the Payment Entry on success.
"""

from __future__ import annotations

import hashlib

import frappe
from frappe.utils import cint, flt, get_url, now_datetime

from erpnext_enhancements.stripe_payments.core.checkout import (
	_compute_surcharge,
	_payment_method_types,
	_resolve_target,
)
from erpnext_enhancements.stripe_payments.core.client import (
	create_checkout_session,
	create_payment_intent,
	ensure_stripe_customer,
)
from erpnext_enhancements.stripe_payments.core.utils import (
	error_snippet,
	get_settings,
	is_enabled,
	to_minor_units,
)


def autopay_consent_text(settings=None) -> str:
	"""The authorization text to show — and store as proof — at autopay enrolment.

	Appends a surcharge disclosure **only while surcharging is actually on**, and
	derives the rate from settings so the two can never drift apart. Autopay is where
	a credit-card fee is charged off-session, with the customer not present, so
	enrolment is the moment the card-network obligation to disclose the fee before
	payment — and to offer a way to avoid it — has to be met.

	The wording is enrolment-specific rather than reusing ``surcharge_disclosure``,
	which is written for a checkout page ("go back and choose another method"). Both
	must stay true for a debit customer: the fee applies to credit cards only.
	"""
	settings = settings or get_settings()
	text = (settings.autopay_consent or "").strip()
	percent = flt(settings.card_surcharge_percent)
	if cint(settings.surcharge_enabled) and percent:
		text = (
			f"{text}\n\nA {percent:g}% processing fee is added to payments made with a credit "
			"card. Debit cards, prepaid cards and bank (ACH) accounts are never charged this "
			"fee — save one of those instead to avoid it."
		).strip()
	return text


def create_setup_session(customer: str, channel: str = "Desk") -> dict:
	"""Start a Checkout Session (setup mode) to save a payment method with consent."""
	settings = get_settings()
	if not is_enabled(settings):
		frappe.throw("Stripe Payments is not enabled.")

	stripe_customer_id = ensure_stripe_customer(customer, settings)
	success_url = f"{get_url(settings.success_route or '/stripe-return')}?status=setup&customer={customer}"
	cancel_url = f"{get_url(settings.cancel_route or '/stripe-return')}?status=cancel"

	params = {
		"mode": "setup",
		"customer": stripe_customer_id,
		"payment_method_types": _payment_method_types(settings),
		"metadata": {"erpnext_customer": customer, "purpose": "autopay_setup", "source": channel},
		"success_url": success_url,
		"cancel_url": cancel_url,
	}
	consent_text = autopay_consent_text(settings)
	if consent_text:
		# Required consent for charging the saved method off-session in future, plus
		# the surcharge disclosure when one applies.
		params["custom_text"] = {"submit": {"message": consent_text[:1200]}}

	try:
		session = create_checkout_session(
			params, idempotency_key=f"ee-setup-{customer}-{frappe.generate_hash(length=8)}"
		)
	except Exception as exc:
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: create_setup_session failed")
		frappe.throw(f"Could not start autopay setup: {error_snippet(str(exc), 200)}")

	_record_consent(customer, channel, settings, session["id"])
	return {"checkout_url": session["url"], "session_id": session["id"], "customer": customer}


def charge_saved_method(
	*,
	customer: str,
	amount=None,
	sales_invoice: str | None = None,
	description: str | None = None,
	channel: str = "Auto",
) -> dict:
	"""Charge a customer's saved method off-session; post a Payment Entry on success.

	This is the one path that can surcharge correctly without any timing problem:
	the saved PaymentMethod is read first, so the card's funding type is known
	before the PaymentIntent is priced. Credit cards may carry a fee; debit,
	prepaid and bank accounts never do.

	Raises if the customer has no saved method. Returns
	``{"stripe_payment", "status", "payment_intent"}``.
	"""
	settings = get_settings()
	if not is_enabled(settings):
		frappe.throw("Stripe Payments is not enabled.")

	customer, amount, currency, description = _resolve_target(
		sales_invoice, customer, amount, description, settings
	)

	stripe_customer_id = frappe.db.get_value("Customer", customer, "custom_stripe_customer_id")
	payment_method = frappe.db.get_value("Customer", customer, "custom_stripe_default_payment_method")
	if not stripe_customer_id or not payment_method:
		frappe.throw(f"{customer} has no saved Stripe payment method. Enroll them in autopay first.")

	# Unlike hosted Checkout, the exact instrument is known *before* we charge, so
	# the surcharge can be priced correctly up front: credit cards only, never debit,
	# prepaid or ACH. No refund-after-the-fact is ever needed on this path.
	pm_type, funding = _payment_method_funding(payment_method)
	surcharge = _compute_surcharge(settings, pm_type=pm_type, funding=funding, base=amount)

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
			"status": "Draft",
		}
	).insert(ignore_permissions=True)

	metadata = {"erpnext_customer": customer, "stripe_payment": sp.name, "source": channel}
	if sales_invoice:
		metadata["erpnext_invoice"] = sales_invoice

	params = {
		# The invoice settles at face value; the surcharge rides on top, exactly as
		# it does on the hosted path, and is booked to income by the companion
		# Journal Entry in reconcile._book_surcharge.
		"amount": to_minor_units(flt(amount) + surcharge, currency),
		"currency": (currency or "USD").lower(),
		"customer": stripe_customer_id,
		"payment_method": payment_method,
		"off_session": True,
		"confirm": True,
		"description": (description or "Payment")[:250],
		"metadata": metadata,
	}

	try:
		pi = create_payment_intent(params, idempotency_key=f"ee-offsession-{sp.name}")
	except Exception as exc:
		sp.db_set("status", "Failed")
		sp.db_set("error_message", error_snippet(str(exc)))
		frappe.db.commit()
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: off-session charge failed")
		if channel == "Auto":
			_alert_failed_autocharge(sp, str(exc))
		frappe.throw(f"Off-session charge failed: {error_snippet(str(exc), 200)}")

	sp.db_set("stripe_payment_intent", pi.get("id"))
	status = pi.get("status")
	if status == "succeeded":
		# Post the Payment Entry now; the webhook is a dedupe-protected backstop.
		from erpnext_enhancements.stripe_payments.core import reconcile

		reconcile.finalize_payment(sp, pi)
	elif status == "processing":
		sp.db_set("status", "Processing")
	else:
		sp.db_set("status", "Failed")
		sp.db_set("error_message", f"PaymentIntent status: {status}")
		if channel == "Auto":
			_alert_failed_autocharge(sp, f"PaymentIntent status: {status}")
	frappe.db.commit()
	sp.reload()
	return {"stripe_payment": sp.name, "status": sp.status, "payment_intent": pi.get("id")}


def _payment_method_funding(payment_method_id):
	"""``(type, card funding)`` for a saved PaymentMethod — the surcharge gate's inputs.

	Returns Stripe's own vocabulary: ``type`` is ``"card"``/``"us_bank_account"`` and
	``funding`` is ``"credit"``/``"debit"``/``"prepaid"``/``"unknown"`` (absent for a
	bank account). Best-effort: a failed lookup returns ``(None, None)``, which
	``_compute_surcharge`` treats as non-surchargeable — the safe direction, since
	the cost of guessing wrong is a card-network violation rather than a lost fee.
	"""
	try:
		from erpnext_enhancements.stripe_payments.core.client import retrieve_payment_method

		pm = retrieve_payment_method(payment_method_id)
		return pm.get("type"), (pm.get("card") or {}).get("funding")
	except Exception:
		frappe.log_error(
			error_snippet(frappe.get_traceback()),
			"Stripe: payment method lookup failed (charging without surcharge)",
		)
		return None, None


def _alert_failed_autocharge(sp, reason):
	"""Alert Accounts and stamp the invoice when an automatic charge fails.

	Mirrors the payout-failure alert (payouts._notify_review). Without this a
	declined card on a maintenance auto-charge only left an Error Log line while
	the Sales Invoice silently aged in AR. Best-effort — never raises into the
	charge flow; commits so the alert survives the caller's frappe.throw.
	"""
	try:
		from erpnext_enhancements.stripe_payments.core.checkout import _stamp_invoice
		from erpnext_enhancements.stripe_payments.core.payouts import _accounts_managers

		if sp.get("sales_invoice"):
			_stamp_invoice(sp.sales_invoice, "Failed")

		amount = f"{sp.amount} {(sp.currency or 'USD')}"
		inv = f" for invoice {sp.sales_invoice}" if sp.get("sales_invoice") else ""
		subject = f"Auto-charge FAILED: {sp.customer} ({amount})"
		content = (
			f"The saved card on file for {sp.customer} was declined on an automatic charge{inv} "
			f"({amount}).<br><br>Reason: {error_snippet(reason or 'no reason given', 200)}<br><br>"
			f"Stripe Payment {sp.name}. The invoice remains outstanding — follow up on the card on file."
		)
		recipients = _accounts_managers()
		for user in recipients:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"subject": subject,
					"email_content": content,
					"document_type": "Sales Invoice" if sp.get("sales_invoice") else "Stripe Payment",
					"document_name": sp.sales_invoice or sp.name,
					"for_user": user,
					"type": "Alert",
				}
			).insert(ignore_permissions=True)
		if not recipients:
			frappe.log_error(f"{subject}: {content}", "Stripe: auto-charge failure (no Accounts Manager)")
		frappe.db.commit()
	except Exception:
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: auto-charge failure alert failed")


def auto_charge_on_invoice_submit(doc, method=None):
	"""Sales Invoice ``on_submit``: off-session charge the customer's saved method.

	Covers both the "auto on submit" and "scheduled by maintenance contract" triggers,
	since maintenance billing generates Sales Invoices. Best-effort and enqueued, so it
	never blocks or fails the invoice submission. No-ops unless the customer is
	autopay-enrolled and the invoice is outstanding and not already being charged.
	"""
	if not is_enabled():
		return
	enrolled = frappe.db.get_value(
		"Customer",
		doc.customer,
		["custom_stripe_autopay_enabled", "custom_stripe_default_payment_method"],
		as_dict=True,
	)
	if not enrolled or not enrolled.custom_stripe_autopay_enabled or not enrolled.custom_stripe_default_payment_method:
		return
	if flt(doc.outstanding_amount) <= 0:
		return
	# Don't double-charge if an active Stripe Payment already covers this invoice.
	if frappe.db.exists(
		"Stripe Payment", {"sales_invoice": doc.name, "status": ["in", ["Processing", "Paid"]]}
	):
		return
	frappe.enqueue(
		"erpnext_enhancements.stripe_payments.core.saved_methods.charge_saved_method",
		queue="short",
		# enqueue only once the invoice's own transaction has committed. Without this the
		# job can be picked up mid-submit — charge_saved_method reads the invoice in a fresh
		# session, sees docstatus 0 and throws "Only a submitted Sales Invoice can be paid",
		# dying with no Stripe Payment row and no retry (poll_pending/dunning only touch
		# existing rows). enqueue_after_commit also means a rolled-back submit never charges.
		enqueue_after_commit=True,
		customer=doc.customer,
		sales_invoice=doc.name,
		channel="Auto",
	)


def _record_consent(customer, channel, settings, setup_session):
	"""Record a proof-of-authorization (Stripe Autopay Consent) at enrollment.

	Captures the exact consent text shown, a fingerprint of it, who initiated, their
	IP/user-agent, the channel and the setup session — the Nacha/card-network record
	of authorization. Activated when the setup-mode Checkout completes. Best-effort:
	never blocks enrollment (the consent text is also shown on the Stripe page).
	"""
	try:
		# The exact text the payer was shown, surcharge disclosure included — the
		# stored proof has to match the page, not just the raw settings field.
		text = autopay_consent_text(settings)
		version = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else None
		user_agent = None
		if getattr(frappe, "request", None):
			user_agent = frappe.request.headers.get("User-Agent")
		frappe.get_doc(
			{
				"doctype": "Stripe Autopay Consent",
				"customer": customer,
				"status": "Pending",
				"channel": channel,
				"methods": ",".join(_payment_method_types(settings)),
				"accepted_by": frappe.session.user,
				"ip_address": getattr(frappe.local, "request_ip", None),
				"user_agent": user_agent,
				"setup_session": setup_session,
				"consent_version": version,
				"consent_text": text,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: consent record failed")


def revoke_autopay(customer):
	"""Cancel autopay for a customer: detach the saved method, clear the flags, and
	mark the active consent Revoked (the record is retained for the required period).
	Provides the customer-facing revocation path the authorization promises.
	"""
	pm = frappe.db.get_value("Customer", customer, "custom_stripe_default_payment_method")
	if pm:
		try:
			from erpnext_enhancements.stripe_payments.core.client import detach_payment_method

			detach_payment_method(pm)
		except Exception:
			frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: detach payment method failed")

	frappe.db.set_value(
		"Customer",
		customer,
		{
			"custom_stripe_autopay_enabled": 0,
			"custom_stripe_default_payment_method": None,
			"custom_stripe_payment_method_label": None,
		},
	)
	for name in frappe.get_all(
		"Stripe Autopay Consent", filters={"customer": customer, "status": "Active"}, pluck="name"
	):
		frappe.db.set_value("Stripe Autopay Consent", name, {"status": "Revoked", "revoked_on": now_datetime()})
	frappe.db.commit()
	return {"customer": customer, "revoked": True}
