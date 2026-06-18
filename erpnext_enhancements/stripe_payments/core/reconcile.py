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

import json

import frappe
from frappe.utils import flt, now_datetime, today

from erpnext_enhancements.stripe_payments.core.checkout import _stamp_invoice
from erpnext_enhancements.stripe_payments.core.utils import (
	error_snippet,
	from_minor_units,
	get_settings,
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
}


def process_event(event_name: str):
	"""Dispatch one stored ``Stripe Event`` by type; record the outcome on the doc.

	Idempotent: re-running for an already-processed event is a no-op, and the
	terminal ``finalize_payment`` dedupes against existing Payment Entries.
	"""
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
		err = (pi.get("last_payment_error") or {}).get("message") or "Payment failed."
		sp.db_set("status", "Failed")
		sp.db_set("error_message", error_snippet(err, 200))
		frappe.db.commit()
	return sp


def _on_charge_refunded(charge):
	"""Record a refund against the Stripe Payment (no Payment Entry reversal yet)."""
	sp = _find_payment(charge)
	if not sp:
		return None
	refunded = from_minor_units(charge.get("amount_refunded"))
	sp.db_set("amount_refunded", refunded)
	if charge.get("refunded") or refunded >= flt(sp.amount):
		sp.db_set("status", "Refunded")
	frappe.db.commit()
	return sp


# --- finalization -----------------------------------------------------------


def finalize_payment(sp, source_obj):
	"""Create and submit the Payment Entry for a successful Stripe Payment.

	Idempotent: returns early if this Stripe Payment is already Paid with a linked
	entry, or if a Payment Entry already references the same PaymentIntent (defends
	against event redelivery and the session/PI double-signal).
	"""
	if sp.status == "Paid" and sp.payment_entry:
		return sp.payment_entry

	pi_id = sp.stripe_payment_intent or _extract_payment_intent(source_obj)
	charge_id, method_type = _enrich(source_obj, pi_id)

	if pi_id:
		existing = frappe.db.get_value(
			"Payment Entry",
			{"custom_stripe_payment_intent": pi_id, "docstatus": ["<", 2]},
			"name",
		)
		if existing:
			_mark_paid(sp, existing, pi_id, charge_id, method_type)
			return existing

	pe_name = _create_payment_entry(sp, pi_id, charge_id, method_type)
	_mark_paid(sp, pe_name, pi_id, charge_id, method_type)
	return pe_name


def _create_payment_entry(sp, pi_id, charge_id, method_type) -> str:
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

	_apply_surcharge(pe, sp)
	pe.mode_of_payment = mode_of_payment
	pe.reference_no = reference_no
	pe.reference_date = today()
	pe.custom_stripe_payment_intent = pi_id
	pe.custom_stripe_charge_id = charge_id
	pe.remarks = f"Stripe payment {sp.name} ({reference_no})"
	pe.flags.ignore_permissions = True
	pe.insert()
	pe.submit()
	return pe.name


def _apply_surcharge(pe, sp):
	"""Add the collected surcharge/fee to the Payment Entry as income.

	The bank/clearing account receives invoice + surcharge; the surcharge is credited
	to the configured income account via a negative deduction row, so the invoice is
	never over-allocated. Single-currency; the deduction sign is verified on the dev
	site during end-to-end testing.
	"""
	surcharge = flt(getattr(sp, "surcharge_amount", 0))
	if surcharge <= 0:
		return
	settings = get_settings()
	if not settings.surcharge_income_account:
		frappe.throw("Stripe surcharge income account is not set, but a surcharge was collected.")
	pe.received_amount = flt(pe.received_amount) + surcharge
	pe.append(
		"deductions",
		{
			"account": settings.surcharge_income_account,
			"cost_center": frappe.get_cached_value("Company", pe.company, "cost_center"),
			"amount": -surcharge,
		},
	)


def _mark_paid(sp, pe_name, pi_id, charge_id, method_type):
	"""Stamp the Stripe Payment + Sales Invoice as Paid and link the Payment Entry."""
	sp.db_set(
		{
			"status": "Paid",
			"payment_entry": pe_name,
			"stripe_payment_intent": pi_id,
			"stripe_charge_id": charge_id,
			"payment_method_type": method_type,
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
	"""Best-effort (charge_id, method_type) from the object, enriched via API.

	Never required: if the API call fails (offline / test), we return whatever the
	event object already carried so a Payment Entry can still post.
	"""
	charge_id = None
	method_type = None

	if obj.get("object") == "charge":
		charge_id = obj.get("id")
		method_type = (obj.get("payment_method_details") or {}).get("type")
	elif obj.get("object") == "payment_intent":
		latest = obj.get("latest_charge")
		charge_id = latest.get("id") if isinstance(latest, dict) else latest
	elif obj.get("object") == "checkout.session":
		types = obj.get("payment_method_types") or []
		method_type = types[0] if types else None

	if pi_id and (not charge_id or not method_type):
		try:
			from erpnext_enhancements.stripe_payments.core.client import retrieve_payment_intent

			pi = retrieve_payment_intent(pi_id)
			latest = pi.get("latest_charge")
			if isinstance(latest, dict):
				charge_id = charge_id or latest.get("id")
				method_type = method_type or (latest.get("payment_method_details") or {}).get("type")
			else:
				charge_id = charge_id or latest
		except Exception:
			pass

	return charge_id, method_type


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
	frappe.db.commit()
	return None


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
