# WI-040: Stripe payout ingestion — payout.paid → clearing-sweep Journal Entry with fee expense
**Phase:** 1   **Type:** APP_CODE   **Size:** L
**Blocked by:** WI-005   **Blocks:** WI-043 (payout-matching step)

## Why
Verified gap (repo_payments): no `payout.*` events are handled, no balance_transaction ingestion exists, no JE ever moves clearing→bank, and Stripe's processing fee is never journaled — PEs post gross. Without this, 'Stripe Clearing' grows forever, the bank GL never matches the statement, and merchant fees vanish from the P&L. Target: on `payout.paid`, ingest the payout and its balance transactions and post one JE per payout: Dr Bank (net), Dr Merchant Fees (sum of fees), Cr Stripe Clearing (gross), with refunds/adjustments inside the payout body handled as additional lines so the clearing account nets to zero per payout.

## Native-first check
Evaluated: Bank Reconciliation Tool + Bank Transaction (can MATCH a deposit but cannot AUTHOR a fee-splitting JE from Stripe API data); native payments-app gateway (no payout accounting at all); Journal Entry (native — and is exactly the artifact this code creates). Verdict: thin APP_CODE that fetches Stripe data and creates native Journal Entries; no native report or tool is reimplemented.

## Preconditions
- WI-005 accounts exist ('Stripe Clearing - SF', 'Merchant Fees - SF').
- `Stripe Payments Settings.deposit_account` resolves to an existing Account (review correction C11) — WI-029's CoA rebuild step 3b re-points it (and, once this item adds them, `fee_expense_account`/`payout_bank_account`) to the rebuilt numbered accounts per coa_mapping.csv; Single Link fields are not covered reliably by Frappe link-checks, so verify explicitly.
- Stripe webhook endpoint (test mode first) can be updated to add `payout.paid` / `payout.failed`.
- A designated bank GL account for payout landing (the operating account mapped in WI-042).

## Scope
All in `erpnext_enhancements/stripe_payments/` (module verified — repo_payments):
- `doctype/stripe_payments_settings/stripe_payments_settings.json`: add two Link→Account fields, e.g. `fee_expense_account` and `payout_bank_account` (new fields; the existing verified fields `deposit_account`, `surcharge_income_account` establish the pattern).
- `core/reconcile.py` `process_event()`: extend the HANDLED set with `payout.paid` (post JE) and `payout.failed` (Notification alert only).
- New `core/payouts.py` (or extend reconcile): fetch the payout's balance transactions via `core/client.py` (REST via requests — repo_payments), aggregate by reporting_category (charge gross/fee, refund, adjustment/dispute), build+submit a Journal Entry: Dr `payout_bank_account` (net), Dr `fee_expense_account` (total fees), Cr `deposit_account` (charge gross), Dr `deposit_account` (refund gross) — lines sum to zero and the clearing balance for that payout's items nets to zero. Stamp the Stripe payout id on the JE (`cheque_no`/remark) for bank-rec matching. Idempotent via the existing `Stripe Event` id-keyed store (repo_payments: doc name = event id, redelivery-safe).
- Fee EXPECTATION validation only: card 2.9%+$0.30, ACH 0.8% capped $5 are used solely to sanity-check and alert on variance; the JOURNALED fee amounts always come from balance_transaction data (per brief; the settings' `ach_fee_percent`/`card_surcharge_percent` fields are customer-surcharge knobs, NOT Stripe's cost — repo_payments).
- Backstop: extend `core/tasks.py` hourly `poll_pending` pattern with a payout poll so a missed webhook still posts the JE.

## Acceptance criteria
- On TEST (Stripe test mode): trigger a payout containing ≥1 charge and ≥1 refund → exactly one submitted Journal Entry exists referencing the payout id; JE total_debit = gross + refund-side legs; GL balance of 'Stripe Clearing - SF' attributable to that payout's items = 0.
- `SELECT COUNT(*) FROM \`tabJournal Entry\` WHERE docstatus=1 AND cheque_no LIKE 'po_%'` ≥ 1 (or the chosen stamp field).
- Redelivering the same `payout.paid` event creates no second JE (Stripe Event dedup).
- Fee-variance alert fires when a synthetic fee deviates >20% from expectation.
- Unit tests added to the bench-free CI suite (repo_app_inventory: CI runs bench-free tests only).

## Rollback
Feature-guard the payout handler on the new settings fields being set (unset → event recorded as Ignored, matching existing dispatcher behavior); cancel any bad JEs (native cancel); revert release.

## Explicitly NOT in this work item
Bank Transaction creation/matching (WI-043 uses the JE); refund PE creation (WI-041); dispute workflow (WI-041); any surcharge logic.
