# WI-039: Stripe production go-live — live keys, webhook endpoint, /pay portal, autopay consent
**Phase:** 1   **Type:** CONFIG   **Size:** M
**Blocked by:** WI-005; enrollment gated on WI-033   **Blocks:** WI-041 (live validation), WI-055

## Why
The full checkout→webhook→Payment Entry pipeline is built, merged to main, and validated on TEST (enabled=1, enable_card=1, enable_ach=1 — test_vs_prod), but prod has no keys, `enabled` defaults 0, and zero Stripe rows (prod_finance_native). Going live is configuration + Stripe-dashboard work, not code.

## Native-first check
Evaluated native `payments`-app Stripe gateway (`Stripe Settings`, 0 rows) and native Payment Request: rejected — the custom module is the tested path and Payment Request is unused by design (brief; repo_payments confirms zero Payment Request references). Verdict: configure the custom module; the artifacts it produces (Payment Entry) are native.

## Preconditions
- WI-005 done on prod.
- `Stripe Payments Settings.deposit_account` resolves to an existing Account (review correction C11) — WI-029's CoA rebuild step 3b re-points it to the rebuilt numbered account per coa_mapping.csv; Single Link fields are not covered reliably by Frappe link-checks, so a dangling value would fail silently. Verify before enabling.
- Live Stripe account with card + ACH (us_bank_account) capabilities activated.
- Web Page fixtures `payment-terms` and `refund-policy` published (shipped as fixtures — repo_app_inventory).
- TIMING DECISION (recommendation, not an OD): go live AT cutover, not before. Pre-cutover go-live would post real Payment Entries in ERPNext while QBO is still the book of record, forcing manual re-keying or enabling the gated `qbo_writeback_enabled` Payment Entry→QBO Payment push (repo_payments/repo_qbo_sync). Branch A (recommended): enable 2027-01-01. Note (review correction C12): the opening AR is NOT in place on Jan 1 — WI-033 cannot post before the December close completes (~Jan 10–15). Interim procedure for Jan 1–~15 (WI-033 scope + WI-051 runbook): incoming payments against pre-cutover invoices are recorded as unallocated/on-account Payment Entries (party set, no invoice reference), then reconciled to the opening Sales Invoices via native Payment Reconciliation once WI-033 posts. Branch B (early go-live): additionally enable `Accounting Intake Settings.qbo_writeback_enabled` and staff the manual 'Push to QuickBooks' step per payment until cutover.

## Scope
All fields on Single `Stripe Payments Settings` (verified in stripe_payments_settings.json and repo_payments): `environment`='Live' (Select Test/Live — verified line 56-61), `company`='Sapphire Fountains', `publishable_key`, `secret_key` (Password), `webhook_signing_secret` (Password), `enabled`=1, `enable_card`=1, `enable_ach`=1, `surcharge_enabled`=0 (STAYS OFF — see WI-055), `statement_descriptor`, `autopay_consent` (legal text approved by counsel — Nacha/card-network proof-of-authorization basis for `Stripe Autopay Consent` records, repo_payments).

Stripe dashboard: create a live webhook endpoint at the URL shown in the read-only `webhook_url` field — `/api/method/erpnext_enhancements.stripe_payments.api.stripe_webhook` (verified in `stripe_payments_settings.py:69-73`) — subscribed to the HANDLED set: `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, `checkout.session.expired`, `payment_intent.succeeded`, `payment_intent.payment_failed`, `charge.refunded` (repo_payments), plus the events added by WI-040/WI-041 once deployed.

Portal: `/pay` ships via `portal_menu_items` (role Customer — repo_app_inventory); verify a portal Customer user can open it and `portal_create_payment` is ownership-checked (repo_payments).

Autopay sequencing hazard: `auto_charge_on_invoice_submit` is wired to Sales Invoice `on_submit` (repo_payments). The cutover open-AR import (WI-033) bulk-submits Sales Invoices; therefore NO customer may be autopay-enrolled before that import completes. Safe today (prod: Stripe Autopay Consent = 0 rows — prod_finance_native); make it an explicit gate: enrollment (create_setup_session links) begins only after WI-033's open-invoice import is accepted. Stripe autopay enrollment stays gated on WI-033 (review correction C12).

Validation: one live-mode card payment (small real amount) end-to-end → refund it via `api.refund_payment` (GL reversal manual until WI-041 lands — book the reversing entry by hand for this one).

## Acceptance criteria
- tabSingles: `enabled`=1, `environment`='Live', `enable_card`=1, `enable_ach`=1, `surcharge_enabled`=0 on prod.
- `SELECT COUNT(*) FROM \`tabStripe Payment\` WHERE status='Paid'` ≥ 1 on prod; the linked Payment Entry is docstatus=1 with `paid_to`='Stripe Clearing - SF' and `custom_stripe_payment_intent` set.
- `SELECT COUNT(*) FROM \`tabStripe Event\` WHERE process_status='Failed'`... = 0 for the validation window; signature-rejected posts return HTTP 400 (test with a bogus signature).
- `SELECT COUNT(*) FROM \`tabStripe Autopay Consent\`` = 0 until the open-AR import (WI-033) acceptance date.

## Rollback
Set `enabled`=0 (master switch); disable the webhook endpoint in the Stripe dashboard. Existing Payment Entries stand.

## Explicitly NOT in this work item
Payout ingestion (WI-040); automated refund GL (WI-041); surcharging (WI-055 — OFF); customer-facing enrollment campaign (business/AR activity after cutover).
