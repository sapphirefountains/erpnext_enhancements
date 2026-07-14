# WI-056: Plaid transactions sync → native Bank Transaction upserts
**Phase:** 2   **Type:** APP_CODE   **Size:** L
**Blocked by:** WI-042; WI-043 (manual process proven stable for ≥1 month)   **Blocks:** nothing

## Why
Manual CSV import works but is operator toil and lags days. The existing `plaid_banking` module already holds credentials, link flow, throttling and error-pause plumbing (`plaid_auth_blocked` auto-pause — repo_payments) but only calls /accounts/balance/get. Extending it to /transactions/sync auto-feeds the native Bank Reconciliation Tool. Honest phase placement: Phase 2 — day-one manual import (WI-043) fully covers the need; this is an efficiency enhancement and must not compete with cutover-critical work.

## Native-first check
This build ships no native Plaid/bank-feed connector (Plaid module is the custom app's — repo_payments; native tables are empty — prod_finance_native). The native artifact (Bank Transaction) is exactly what the code will create, feeding the native Reconciliation Tool. Verdict: APP_CODE producing native records; no native feature reimplemented.

## Preconditions
- Plaid PRODUCTION credentials (prod is sandbox with no keys — prod_finance_native) and a fresh item link (`plaid_access_token`, `plaid_item_id`).
- WI-043 runbook stable for ≥1 month (the fallback when Plaid pauses on `plaid_auth_blocked`).
- Settings-field naming ambiguity resolved: live on/off currently reads from tabSingles field 'enabled' although the doctype schema also carries 'plaid_enabled' (test_vs_prod observation) — reconcile in this change.

## Scope
In `erpnext_enhancements/plaid_banking/` (repo_payments): `core/constants.py` add `/transactions/sync`; `core/client.py` call; new `core/transactions.py` — cursor-based sync (persist cursor on `Plaid Settings`), map added/modified/removed to Bank Transaction insert/update/cancel, dedup keyed on the Plaid `transaction_id` stored on the Bank Transaction; map Plaid account_id → the `Bank Account` masters from WI-042; extend `core/tasks.py` `scheduled_balance_refresh` cadence pattern with a transactions poll honoring `refresh_poll_minutes` and `plaid_auth_blocked`. Bulk note: Bank Transaction saves will each fire the wildcard `global_triton_sync` enqueue (module not excluded); initial backfill should be windowed (e.g., 30 days) and run off-hours.

## Acceptance criteria
- `SELECT COUNT(*) FROM \`tabBank Transaction\`` grows without operator action after a known bank movement; re-running sync produces zero duplicates (COUNT by transaction_id has no >1 groups).
- A removed/pending-reversed Plaid transaction does not survive as a matchable row.
- Reconciliation for a Plaid-fed month completes without a CSV import.

## Rollback
Disable the transactions poll (settings flag added with the feature); fall back to WI-043 manual import; imported rows remain valid Bank Transactions.

## Explicitly NOT in this work item
Auto-matching/auto-reconciliation logic beyond what the native tool does; balances widget changes; credit-card-specific categorization.
