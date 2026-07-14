# WI-043: Day-one bank reconciliation — manual statement import runbook + Stripe payout matching
**Phase:** 1   **Type:** CONFIG   **Size:** M
**Blocked by:** WI-042; WI-040 (for automated payout-JE matching; degraded manual mode works without it)   **Blocks:** WI-056

## Why
The Plaid module is balances-only — it calls only /accounts/balance/get and creates zero Bank Transaction rows (repo_payments, verified), so it cannot feed reconciliation. Day one must run on the native import path or the first month-end close has no bank tie-out. Honest posture: manual CSV import is fine at this company's volume; Plaid transactions are a convenience, not a dependency.

## Native-first check
Native Bank Statement Import (CSV/XLSX) → Bank Transaction → Bank Reconciliation Tool → Bank Reconciliation Statement report (present and enabled — prod_finance_native lists it among the 16 native Script Reports). Verdict: native features are sufficient; this item is process/config only. Reimplementing any of these would be a defect.

## Preconditions
- WI-042 masters exist; bank portal access for CSV export per account.
- Month-End Close usage decision from the Finance workstream (WI-049) — the doctype exists with a seeded 9-task checklist including bank/CC reconciliation (repo_ops) but is not wired to any hook/scheduler (repo_app_inventory).

## Scope
Runbook (Process Documentation module is the natural home — repo_app_inventory) covering, per account, weekly cadence:
1. Export statement CSV → Bank Statement Import (map columns once per bank; the import tool persists the mapping).
2. Bank Reconciliation Tool: match Bank Transactions against submitted Payment Entries / Journal Entries.
3. Stripe payout matching: each bank credit from Stripe matches the WI-040 payout JE's bank leg (JE stamped with the `po_...` payout id; match on amount+date+reference). Until WI-040 ships: match the net deposit manually against a hand-built clearing-sweep JE (same three-leg shape, keyed off the Stripe dashboard payout report).
4. Month-end: run Bank Reconciliation Statement; attach to the Month-End Close task for the period.

Named operators: the Finance process map assigns Lisa Symanski / John Juntunen to close tasks (repo_ops) — confirm with the controller.

## Acceptance criteria
- One full statement month imported and reconciled on TEST: `SELECT COUNT(*) FROM \`tabBank Transaction\`` > 0 and unallocated amount = 0 for the period; Bank Reconciliation Statement shows zero unexplained difference.
- Runbook document exists and names owners and cadence.
- ≥1 Stripe payout line matched to its JE on TEST.

## Rollback
N/A (process); imported Bank Transactions can be cancelled/deleted if a bad file is loaded — document the bad-import recovery step in the runbook.

## Explicitly NOT in this work item
Plaid automation (WI-056); rebuilding any native report; credit-card feed handling beyond the same CSV path.
