# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Scheduled maintenance for the Stripe Payments integration (hourly).

``poll_pending`` is a safety net for missed webhooks: it reconciles payments stuck
in Link Sent / Processing by re-reading their state from Stripe. ``retry_failed``
re-runs events whose processing previously errored (e.g. the deposit account wasn't
configured yet). Both no-op when the integration is disabled. Mirrors the
QuickBooks module's hourly tasks.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, now_datetime

from erpnext_enhancements.stripe_payments.core.utils import error_snippet, get_settings, is_enabled

# Don't chase a payment the instant the link is created — give the customer time.
PENDING_GRACE_MINUTES = 15
PENDING_BATCH = 50
RETRY_BATCH = 25


def poll_pending():
	"""Reconcile Stripe Payments stuck in Link Sent / Processing against Stripe."""
	settings = get_settings()
	if not is_enabled(settings):
		return

	from erpnext_enhancements.stripe_payments.core import reconcile
	from erpnext_enhancements.stripe_payments.core.client import (
		retrieve_checkout_session,
		retrieve_payment_intent,
	)

	cutoff = add_to_date(now_datetime(), minutes=-PENDING_GRACE_MINUTES)
	rows = frappe.get_all(
		"Stripe Payment",
		filters={"status": ["in", ["Link Sent", "Processing"]], "modified": ["<", cutoff]},
		pluck="name",
		limit=PENDING_BATCH,
	)
	for name in rows:
		try:
			sp = frappe.get_doc("Stripe Payment", name)
			if sp.stripe_payment_intent:
				pi = retrieve_payment_intent(sp.stripe_payment_intent)
				if pi.get("status") == "succeeded":
					reconcile.finalize_payment(sp, pi)
				elif pi.get("status") in ("canceled",):
					sp.db_set("status", "Failed")
					frappe.db.commit()
			elif sp.stripe_checkout_session:
				session = retrieve_checkout_session(sp.stripe_checkout_session)
				if session.get("payment_status") == "paid":
					reconcile.finalize_payment(sp, session)
				elif session.get("status") == "expired":
					sp.db_set("status", "Expired")
					frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(error_snippet(frappe.get_traceback()), f"Stripe: poll_pending {name} failed")


def poll_payouts():
	"""Backstop for a missed ``payout.paid`` webhook: post JEs for recent payouts.

	Lists the most recent payouts from Stripe and reconciles any that are ``paid``
	but not yet journalled. ``process_payout`` is idempotent (keyed on the payout id
	stamped in the Journal Entry's cheque_no), so re-running is safe. No-ops unless
	the integration is enabled and the payout accounts are configured.
	"""
	settings = get_settings()
	if not is_enabled(settings):
		return
	if not (settings.deposit_account and settings.fee_expense_account and settings.payout_bank_account):
		return

	from erpnext_enhancements.stripe_payments.core import payouts
	from erpnext_enhancements.stripe_payments.core.client import list_recent_payouts

	try:
		recent = list_recent_payouts(limit=20, settings=settings)
	except Exception:
		frappe.log_error(error_snippet(frappe.get_traceback()), "Stripe: poll_payouts list failed")
		return

	for payout in recent:
		if payout.get("status") != "paid":
			continue
		try:
			payouts.process_payout(payout)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				error_snippet(frappe.get_traceback()), f"Stripe: poll_payouts {payout.get('id')} failed"
			)


def retry_failed():
	"""Re-run events whose processing errored, or whose enqueued job never ran.

	The ``Error`` events are ordinary retries. The ``Pending`` sweep is the deploy
	durability story: ``handle_webhook`` records the event ``Pending`` and enqueues
	``process_event``, but a prod deploy FLUSHDBs the queue redis and destroys that job —
	after which nothing re-drives it. ``poll_pending`` backstops the payment-status money
	paths, but not ``charge.refunded`` (the refund would never post), setup-mode
	``checkout.session.completed`` (the saved card never stored, autopay never activates),
	or ``payout.failed`` alerts. A grace window avoids racing an in-flight enqueue;
	``process_event`` is idempotent, so re-running a Pending event that did run is safe.
	"""
	if not is_enabled():
		return

	from erpnext_enhancements.stripe_payments.core import reconcile

	cutoff = add_to_date(now_datetime(), minutes=-PENDING_GRACE_MINUTES)
	rows = frappe.get_all(
		"Stripe Event",
		filters={"process_status": "Error", "processed": 0},
		pluck="name",
		limit=RETRY_BATCH,
	)
	stuck_pending = frappe.get_all(
		"Stripe Event",
		filters={"process_status": "Pending", "processed": 0, "modified": ["<", cutoff]},
		pluck="name",
		limit=RETRY_BATCH,
	)
	for name in [*rows, *stuck_pending]:
		try:
			reconcile.process_event(name)
		except Exception:
			# process_event already logged + recorded the error on the event.
			frappe.db.rollback()


def sweep_missed_autopay():
	"""Re-drive autopay charges whose enqueued job never ran.

	``auto_charge_on_invoice_submit`` now enqueues after commit, but a deploy still
	FLUSHDBs the queue redis between commit and execution. Neither ``poll_pending`` (polls
	existing Stripe Payment rows) nor the dunning cycle (retries Failed payments) covers an
	invoice that never produced a Stripe Payment at all. Find autopay-enrolled customers'
	submitted, outstanding invoices with no active/handled Stripe Payment and re-charge
	them. ``charge_saved_method`` is guarded and the on-submit path already skips a
	Processing/Paid payment, so a double run is safe.
	"""
	settings = get_settings()
	if not is_enabled(settings):
		return

	cutoff = add_to_date(now_datetime(), minutes=-30)
	invoices = frappe.db.sql(
		"""
		select si.name as invoice, si.customer
		from `tabSales Invoice` si
		join `tabCustomer` c on c.name = si.customer
		where si.docstatus = 1 and si.outstanding_amount > 0 and si.modified < %(cutoff)s
		  and c.custom_stripe_autopay_enabled = 1
		  and ifnull(c.custom_stripe_default_payment_method, '') != ''
		  and not exists (
		      select 1 from `tabStripe Payment` sp
		      where sp.sales_invoice = si.name
		        and sp.status in ('Processing', 'Paid', 'Failed', 'Link Sent')
		  )
		limit %(batch)s
		""",
		{"cutoff": cutoff, "batch": RETRY_BATCH},
		as_dict=True,
	)
	for row in invoices:
		try:
			frappe.enqueue(
				"erpnext_enhancements.stripe_payments.core.saved_methods.charge_saved_method",
				queue="short",
				enqueue_after_commit=True,
				customer=row.customer,
				sales_invoice=row.invoice,
				channel="Auto",
			)
		except Exception:
			frappe.log_error(
				error_snippet(frappe.get_traceback()), f"Stripe: sweep_missed_autopay {row.invoice} failed"
			)
