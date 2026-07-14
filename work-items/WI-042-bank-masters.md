# WI-042: Bank and Bank Account masters
**Phase:** 1   **Type:** CONFIG   **Size:** S
**Blocked by:** authoritative account list from finance (OD-1 RESOLVED "No" — single-company masters, no JDH scoping needed)   **Blocks:** WI-043, WI-056

## Why
Bank, Bank Account, and Bank Transaction tables are all 0 rows on prod (prod_finance_native), so neither statement import nor the Reconciliation Tool can run. Masters are pure native config and prerequisite to any day-one reconciliation.

## Native-first check
Native Bank + Bank Account doctypes — fully sufficient, nothing custom needed. Verdict: native, CONFIG.

## Preconditions
- Finance provides the authoritative list of real bank/credit-card accounts (institution, last-4, purpose) — not derivable from the reports.
- Corresponding GL leaf accounts identified in the 359-account CoA ('1110 - Cash - SF' is the one verified bank-ish GL name — prod_finance_native; the QBO-imported CoA cleanup is WI-004/WI-029's; coordinate naming).

## Scope
- One `Bank` record per institution.
- One `Bank Account` per real account: `is_company_account`=1 (field verified — prod_finance_native), company='Sapphire Fountains', linked GL account, masked account number.
- Do NOT map 'Stripe Clearing - SF' as a Bank Account — it is a GL clearing account, not a statement-bearing bank account.

## Acceptance criteria
- `SELECT COUNT(*) FROM \`tabBank Account\` WHERE is_company_account=1` = number on finance's list (≥1).
- Every such row has a non-null GL `account` link; zero rows link to 'Stripe Clearing - SF'.
- Bank Reconciliation Tool opens for each account without error.

## Rollback
Delete the master rows (no transactions reference them yet).

## Explicitly NOT in this work item
Statement import/rec process (WI-043); Plaid (WI-056); CoA renumbering (WI-029).
