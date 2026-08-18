# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Declined-card dunning for Stripe auto-charges.

The Stripe integration auto-charges a customer's saved card when a Sales Invoice
is submitted (``saved_methods.auto_charge_on_invoice_submit``). Nothing retried a
*declined* card: the failure only stamped the invoice ``custom_stripe_payment_status
= "Failed"`` and alerted Accounts once. This module adds the recovery loop.

:func:`run_dunning_cycle` — a daily job (gated by "Declined-Card Dunning" in
ERPNext Enhancements Settings + the Stripe master switch) that:

1. **Enrolls** invoices whose auto-charge failed and are still outstanding into a
   dunning cycle (state tracked in ``custom_dunning_*`` fields on the Sales
   Invoice), emailing the customer that the first charge failed.
2. **Retries** the saved card on a configurable schedule (``dunning_retry_days``,
   default ``2,4,7`` days after the first decline — 4 attempts over ~a week),
   re-invoking ``charge_saved_method`` under a distinct ``Dunning`` channel so the
   per-failure Accounts alert stays suppressed and this engine owns the customer
   emails. Each failed attempt emails the customer.
3. On **recovery** (the card clears, or the invoice was paid manually) marks the
   invoice ``Recovered``; on **exhaustion** (all retries used, or the card was
   removed) alerts Accounts, turns off the customer's autopay, and applies a
   **service hold** (``Customer.custom_service_hold``) that pauses maintenance
   visit generation until staff resolve payment.

State lives on the Sales Invoice (``custom_dunning_state/attempts/next_retry/
opened_on/last_attempt/last_error``) — co-located with what is being collected,
mirroring the existing ``custom_stripe_*`` fields. All stamps use
``update_modified=False`` (background bookkeeping, like ``checkout._stamp_invoice``)
and date math is site-local (``today()``/``add_days``).
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, cint, flt, getdate, now_datetime, nowdate

from erpnext_enhancements import email_style
from erpnext_enhancements.stripe_payments.core.utils import error_snippet, is_enabled

DEFAULT_RETRY_DAYS = "2,4,7"
DUNNING_BATCH = 50
# How far back enrollment looks for a fresh auto-charge failure. A new, still-
# unenrolled outstanding failure is recent by definition (the sweep runs daily);
# this also bounds the discovery query so it never scans the full, never-pruned
# history of Failed Stripe Payment rows. Once enrolled, a case is driven by the
# Active-state query, so its original failure ageing past this window is fine.
ENROLL_LOOKBACK_DAYS = 30


def _dunning_enabled() -> bool:
    return bool(cint(frappe.db.get_single_value("ERPNext Enhancements Settings", "dunning_enabled")))


def _retry_schedule() -> list[int]:
    """Sorted, de-duplicated positive day-offsets from the first decline to each
    retry (e.g. ``[2, 4, 7]``). Total attempts = 1 initial + ``len(schedule)``.
    Parsed from Settings ``dunning_retry_days``; blank/garbage falls back to the
    default so the loop always has a valid, bounded schedule."""
    raw = frappe.db.get_single_value("ERPNext Enhancements Settings", "dunning_retry_days")
    days = sorted({int(t) for t in str(raw or "").split(",") if t.strip().isdigit() and int(t) > 0})
    return days or [int(t) for t in DEFAULT_RETRY_DAYS.split(",")]


def run_dunning_cycle(today=None):
    """Daily: enroll newly-declined auto-charges and process due retries.

    Gated by ``dunning_enabled`` + the Stripe master switch. Each invoice is
    handled inside its own savepoint so one failure never starves the batch.
    """
    if not _dunning_enabled() or not is_enabled():
        return
    today = getdate(today or nowdate())
    schedule = _retry_schedule()

    # Each case commits on success. On failure roll back only the current case's
    # uncommitted stamps — a full rollback (not a savepoint) because the retry
    # path calls charge_saved_method, which commits internally (releasing any
    # savepoint); the committed Stripe Payment attempt row survives, as intended.
    for inv in _discover_new_failures(today):
        try:
            _enroll(inv, today, schedule)
        except Exception:
            frappe.db.rollback()
            frappe.log_error(frappe.get_traceback(), f"Dunning enroll failed: {inv}")

    for inv in _discover_due_retries(today):
        try:
            _process_case(inv, today, schedule)
        except Exception:
            frappe.db.rollback()
            frappe.log_error(frappe.get_traceback(), f"Dunning retry failed: {inv}")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _discover_new_failures(today) -> list[str]:
    """Submitted, still-outstanding invoices whose *auto-charge* failed recently
    and that are not yet in a dunning cycle.

    Scoped to invoices that actually have a failed Auto/Dunning Stripe Payment —
    NOT the invoice-level ``custom_stripe_payment_status = "Failed"`` flag, which
    is shared (``reconcile._on_session_async_failed`` sets it for one-off ACH
    link bounces too, with no channel guard). Keying on that flag alone would
    wrongly enrol — and immediately service-hold — a customer who was never on
    autopay. The ``ENROLL_LOOKBACK_DAYS`` recency bound keeps this query off the
    full never-pruned Failed-payment history."""
    auto_failed = frappe.get_all(
        "Stripe Payment",
        filters={
            "status": "Failed",
            "channel": ["in", ["Auto", "Dunning"]],
            "sales_invoice": ["is", "set"],
            "creation": [">=", add_days(today, -ENROLL_LOOKBACK_DAYS)],
        },
        pluck="sales_invoice",
        distinct=True,
    )
    if not auto_failed:
        return []
    return frappe.get_all(
        "Sales Invoice",
        filters={
            "name": ["in", list(set(auto_failed))],
            "docstatus": 1,
            "outstanding_amount": [">", 0],
            "custom_dunning_state": ["is", "not set"],
        },
        pluck="name",
        limit=DUNNING_BATCH,
    )


def _discover_due_retries(today) -> list[str]:
    """Active dunning cases whose next retry is due."""
    return frappe.get_all(
        "Sales Invoice",
        filters={
            "docstatus": 1,
            "custom_dunning_state": "Active",
            "custom_dunning_next_retry": ["<=", today],
        },
        pluck="name",
        limit=DUNNING_BATCH,
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def _enroll(inv, today, schedule):
    """Open a dunning cycle for an invoice whose initial auto-charge failed.

    The initial charge is attempt 1 (already failed); the first retry is scheduled
    for ``opened_on + schedule[0]``. If the card is already gone (autopay revoked /
    no saved method) there is nothing to retry — exhaust immediately.
    """
    invoice = frappe.get_doc("Sales Invoice", inv)
    if flt(invoice.outstanding_amount) <= 0:
        return  # paid between discovery and now

    # Discovery already scoped this to an auto-charge failure; carry the decline
    # reason from the most-recent failed attempt onto the case for visibility.
    original_error = frappe.db.get_value(
        "Stripe Payment",
        {"sales_invoice": inv, "status": "Failed", "channel": ["in", ["Auto", "Dunning"]]},
        "error_message",
        order_by="creation desc",
    )
    _stamp(inv, {
        "custom_dunning_state": "Active",
        "custom_dunning_attempts": 1,
        "custom_dunning_opened_on": today,
        "custom_dunning_last_attempt": now_datetime(),
        "custom_dunning_last_error": error_snippet(original_error or "Card declined.", 500),
    })

    if not _can_autocharge(invoice.customer):
        _exhaust(inv, invoice, "No saved card on file (autopay was removed).")
        return

    _stamp(inv, {"custom_dunning_next_retry": add_days(today, schedule[0])})
    _email_customer(inv, invoice, attempt=1, final=False)
    frappe.db.commit()


def _process_case(inv, today, schedule):
    """Retry one due dunning case, then recover / reschedule / exhaust."""
    invoice = frappe.get_doc("Sales Invoice", inv)

    if flt(invoice.outstanding_amount) <= 0:
        _stamp(inv, {"custom_dunning_state": "Recovered", "custom_dunning_next_retry": None})
        frappe.db.commit()
        return

    # An earlier attempt's charge is still settling (ACH) or already cleared —
    # don't stack a second in-flight PaymentIntent.
    inflight = frappe.db.exists(
        "Stripe Payment", {"sales_invoice": inv, "status": ["in", ["Processing", "Paid"]]}
    )
    if inflight:
        _stamp(inv, {"custom_dunning_next_retry": add_days(today, 2)})
        frappe.db.commit()
        return

    # Idempotency for the declined path (whose only other guard is the
    # post-charge stamp): if a Dunning charge was already attempted for this
    # invoice today — a same-day scheduler catch-up, or a re-run after a crash
    # dropped the bookkeeping stamp — do not charge again; just push the retry to
    # a future day so the schedule keeps advancing without a duplicate charge.
    if frappe.db.exists(
        "Stripe Payment", {"sales_invoice": inv, "channel": "Dunning", "creation": [">=", today]}
    ):
        _stamp(inv, {"custom_dunning_next_retry": add_days(today, 1)})
        frappe.db.commit()
        return

    if not _can_autocharge(invoice.customer):
        _exhaust(inv, invoice, "No saved card on file (autopay was removed).")
        return

    status, err = _attempt_charge(invoice.customer, inv)

    # Re-read outstanding: finalize_payment (on success) posts the Payment Entry.
    outstanding = flt(frappe.db.get_value("Sales Invoice", inv, "outstanding_amount"))
    if status == "Paid" or outstanding <= 0:
        _stamp(inv, {
            "custom_dunning_state": "Recovered",
            "custom_dunning_next_retry": None,
            "custom_dunning_last_attempt": now_datetime(),
        })
        frappe.db.commit()
        return
    if status == "Processing":
        # ACH pending — wait for the webhook to settle, re-check in a couple of days.
        _stamp(inv, {"custom_dunning_next_retry": add_days(today, 2), "custom_dunning_last_attempt": now_datetime()})
        frappe.db.commit()
        return

    # Declined.
    attempts = cint(invoice.custom_dunning_attempts) + 1
    _stamp(inv, {
        "custom_dunning_attempts": attempts,
        "custom_dunning_last_attempt": now_datetime(),
        "custom_dunning_last_error": error_snippet(err or "Card declined.", 500),
    })
    retries_done = attempts - 1  # the initial charge was attempt 1, not a scheduled retry
    if retries_done >= len(schedule):
        _exhaust(inv, invoice, err or "Card declined.")
        return
    # Anchor the schedule to opened_on, but never stamp a past date: after a
    # scheduler gap opened_on + offset can be <= today, which would re-select the
    # case every run and collapse the spacing — clamp to at least tomorrow.
    opened_on = getdate(invoice.custom_dunning_opened_on or today)
    next_retry = max(getdate(add_days(opened_on, schedule[retries_done])), add_days(today, 1))
    _stamp(inv, {"custom_dunning_next_retry": next_retry})
    _email_customer(inv, invoice, attempt=attempts, final=False)
    frappe.db.commit()


def _exhaust(inv, invoice, reason):
    """Terminal: give up retrying — alert Accounts, disable autopay, apply a
    service hold, and send the customer a final notice."""
    _stamp(inv, {"custom_dunning_state": "Exhausted", "custom_dunning_next_retry": None})
    frappe.db.set_value(
        "Customer",
        invoice.customer,
        {"custom_stripe_autopay_enabled": 0, "custom_service_hold": 1},
        update_modified=False,
    )
    _alert_accounts_exhausted(inv, invoice, reason)
    _email_customer(inv, invoice, attempt=cint(invoice.custom_dunning_attempts), final=True)
    frappe.db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stamp(inv, fields):
    """Background bookkeeping write — never bump the invoice's modified timestamp
    (mirrors checkout._stamp_invoice), so dunning never trips an editor's lock."""
    frappe.db.set_value("Sales Invoice", inv, fields, update_modified=False)


def _can_autocharge(customer) -> bool:
    row = frappe.db.get_value(
        "Customer",
        customer,
        ["custom_stripe_autopay_enabled", "custom_stripe_default_payment_method"],
        as_dict=True,
    )
    return bool(row and row.custom_stripe_autopay_enabled and row.custom_stripe_default_payment_method)


def _attempt_charge(customer, sales_invoice):
    """Re-charge the saved card for a retry. Returns ``(status, error)`` — status
    is the resulting Stripe Payment status ('Paid'/'Processing'/'Failed');
    ``charge_saved_method`` raises on a synchronous decline, caught here. Uses the
    ``Dunning`` channel so the built-in per-failure Accounts alert stays quiet."""
    from erpnext_enhancements.stripe_payments.core.saved_methods import charge_saved_method

    try:
        result = charge_saved_method(customer=customer, sales_invoice=sales_invoice, channel="Dunning")
        return result.get("status"), None
    except Exception as exc:
        return "Failed", str(exc)


def _customer_email(customer) -> str | None:
    """Best-effort billing email: the customer's primary contact, else any linked
    contact that has an email."""
    contact = frappe.db.get_value("Customer", customer, "customer_primary_contact")
    if contact:
        email = frappe.db.get_value("Contact", contact, "email_id")
        if email:
            return email
    linked = frappe.get_all(
        "Dynamic Link",
        filters={"link_doctype": "Customer", "link_name": customer, "parenttype": "Contact"},
        pluck="parent",
    )
    for name in linked:
        email = frappe.db.get_value("Contact", name, "email_id")
        if email:
            return email
    return None


def _email_customer(inv, invoice, attempt, final):
    """Email the customer that a payment attempt failed. Best-effort — a missing
    email or a mail failure never breaks the dunning run."""
    try:
        email = _customer_email(invoice.customer)
        if not email:
            frappe.log_error(f"Dunning: no email for {invoice.customer} (invoice {inv})", "Dunning customer email")
            return
        amount = frappe.utils.fmt_money(flt(invoice.outstanding_amount), currency=invoice.currency)
        company = invoice.company or "our team"
        if final:
            subject = f"Payment unsuccessful — autopay paused for invoice {inv}"
            body = (
                f"<p>We were unable to process payment of {amount} for invoice "
                f"<b>{frappe.utils.escape_html(inv)}</b> after several attempts on the card we have on file.</p>"
                f"<p>Automatic payments have been paused and upcoming maintenance service is on hold until "
                f"payment is resolved. Please contact {frappe.utils.escape_html(company)} to update your "
                f"payment method and restore service.</p>"
            )
        else:
            subject = f"Payment could not be processed — invoice {inv}"
            body = (
                f"<p>The card we have on file was declined for payment of {amount} on invoice "
                f"<b>{frappe.utils.escape_html(inv)}</b>.</p>"
                f"<p>We'll automatically try again over the next few days. To avoid an interruption, please "
                f"contact {frappe.utils.escape_html(company)} if your card details have changed.</p>"
            )
        frappe.sendmail(
            recipients=[email],
            subject=subject,
            message=email_style.wrap(body, title=subject, eyebrow="Billing", tagline=True),
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Dunning: customer email failed")


def _alert_accounts_exhausted(inv, invoice, reason):
    """Notification Log to Accounts Managers when a dunning cycle is exhausted."""
    from erpnext_enhancements.stripe_payments.core.payouts import _accounts_managers

    # Read the counter fresh — the in-memory invoice doc predates the stamp that
    # _enroll/_process_case commit before calling _exhaust.
    attempts = cint(frappe.db.get_value("Sales Invoice", inv, "custom_dunning_attempts"))
    amount = frappe.utils.fmt_money(flt(invoice.outstanding_amount), currency=invoice.currency)
    subject = f"Dunning exhausted: {invoice.customer} — autopay off + service hold"
    content = (
        f"Automatic payment for invoice {inv} ({amount}) failed after {attempts} attempt(s).<br><br>"
        f"Reason: {error_snippet(reason or 'card declined', 200)}<br><br>"
        f"Autopay has been disabled and a <b>service hold</b> applied to {invoice.customer} "
        f"(maintenance visit generation is paused). Collect payment and re-enrol autopay, then clear "
        f"<b>Service Hold</b> on the Customer to resume service."
    )
    recipients = _accounts_managers()
    for user in recipients:
        try:
            frappe.get_doc({
                "doctype": "Notification Log",
                "subject": subject,
                "email_content": content,
                "document_type": "Sales Invoice",
                "document_name": inv,
                "for_user": user,
                "type": "Alert",
            }).insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Dunning: exhaustion alert failed")
    if not recipients:
        frappe.log_error(f"{subject}: {content}", "Dunning: exhausted (no Accounts Manager to notify)")
