# WI-035: Opening reconciliation, tie-out, and go-live sign-off gate
**Phase:** 1   **Type:** DATA   **Size:** S
**Blocked by:** WI-032, WI-033, WI-034   **Blocks:** go-live declaration (external); WI-045 (Disconnect + QBO read-only flip are gated on this sign-off)

## Why
One item owns the proof that ERPNext's opening position equals QBO's closed books — the CPA-facing artifact that lets QBO go read-only with confidence.

## Native-first check
Native Trial Balance, Accounts Receivable, Accounts Payable, Balance Sheet reports (all verified present and enabled — prod_finance_native) are the statutory numbers (rule 6). Supplemented by the app's existing `reconcile.compare_account_balances(as_of_date, tolerance)` / 'QuickBooks Balance Comparison' report (repo_qbo_sync) for automated per-account QBO-vs-ERPNext diffing while QBO is still reachable. Verdict: native reports + existing tool; nothing built.

## Preconditions
- WI-032/WI-033/WI-034 complete. QBO connection still alive OR the WI-003 close package used for manual comparison.

## Scope
- Run `compare_account_balances(as_of_date='2026-12-31')`: assert matched-bucket covers all accounts within $0.01; investigate any mismatched/qb_only/erp_only rows.
- Native tie-outs as of 2026-12-31/2027-01-01: Trial Balance total; AR report total == AR control account balance == QBO AR aging; AP mirror; bank account GL balances == reconciled bank statements; Temporary Opening == 0.
- On sign-off: set `Company.accounts_frozen_till_date = 2026-12-31` and `role_allowed_for_frozen_entries='Accounts Manager'` (native Company fields — same mechanism Month-End Close automates, verified in month_end_close.py) so nothing can post into the opening period.
- Written sign-off from the close owner + external accountant archived; QBO flipped to read-only per rule 4 (executed by the sync workstream in WI-045, gated on this sign-off).

## Acceptance criteria
- compare_account_balances result: 0 accounts in mismatched/qb_only/erp_only buckets (or each residual item has a written accepted-variance note).
- `SELECT SUM(debit)-SUM(credit) FROM \`tabGL Entry\` WHERE account LIKE '%Temporary%'` = 0 (exact account per WI-004).
- `SELECT accounts_frozen_till_date FROM tabCompany WHERE name='Sapphire Fountains'` = '2026-12-31'.
- Sign-off document archived.

## Rollback
If tie-out fails: cancel offending opening docs (WI-032/WI-033 rollbacks), correct, re-run. The frozen date is reversible by Accounts Manager.

## Explicitly NOT in this work item
Fixing QBO-side errors (loops back to WI-003 re-close); the Disconnect itself (WI-045).
