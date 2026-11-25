# WI-035: Tie-out of the posted ledger to QuickBooks' December close, freeze and sign-off
**Phase:** 1   **Type:** DATA   **Size:** S
**Blocked by:** Wave 3 of the 2026 posting (TASK-2026-01236, Jan 13–14); WI-003 December close locked (Jan 13); WI-034   **Blocks:** WI-045 (the Disconnect is gated on this sign-off); Stripe autopay enrollment (WI-039)
**Re-scoped 2026-11-25** (Nikolas's cutover decision 2). WI-032 and WI-033 are superseded, so this item no longer reconciles opening entries. It proves that the **posted ledger at 2026-12-31 equals QuickBooks' December close**, books any residual as one JE John approves, and freezes 2026. Due **2027-01-22**; Lisa runs it, John signs. Procedure: WI-051 runbook §8.2. ERPNext Task: TASK-2026-00928.

## Why
One item owns the proof that ERPNext's books equal QuickBooks' closed books: the CPA-facing artifact that lets QuickBooks go read-only and be disconnected with confidence.

## Native-first check
Native Trial Balance, Accounts Receivable, Accounts Payable, General Ledger and Balance Sheet reports are the statutory numbers (rule 6). Supplemented by the app's `reconcile.compare_account_balances(as_of_date, tolerance)` / 'QuickBooks Balance Comparison' report for an automated per-account QuickBooks-vs-ERPNext diff while QuickBooks is still connected. Verdict: native reports + existing tool; nothing built.

## Preconditions
- All three posting waves done (WI-051 runbook §3): no QBO-linked draft dated 2026 left, except rows Lisa held with a written reason.
- QuickBooks still connected (the Disconnect, WI-045, comes after this item).
- The open decision on the 2026 stock postings that QuickBooks never saw (Purchase Receipts, Stock Entries and Stock Reconciliations already in ERPNext's 2026 GL) taken by John and Lisa: book them in QuickBooks' December close, reverse them in ERPNext, or accept a written variance.

## Scope
- Run `compare_account_balances(as_of_date='2026-12-31')`: every account in the matched bucket within $0.01; investigate any mismatched / qb_only / erp_only row.
- Native tie-outs at 2026-12-31 against QuickBooks' December close package: Trial Balance; AR aging; AP aging; every bank account.
- Settle the stock postings per the decision above.
- Book any remaining residual as **one** Journal Entry dated 2026-12-31, approved by John.
- Confirm the unapplied January receipts for December invoices are reconciled (WI-051 runbook §7.5).
- On sign-off: set `Company.accounts_frozen_till_date = 2026-12-31` and `role_allowed_for_frozen_entries = 'Accounts Manager'` (native Company fields, the same mechanism Month-End Close automates) so nothing can post into 2026.
- Archive John's written sign-off. The Disconnect (WI-045) follows.

## Acceptance criteria
- `compare_account_balances('2026-12-31')`: 0 accounts in mismatched / qb_only / erp_only, or each residual has a written accepted-variance note.
- At most one residual Journal Entry dated 2026-12-31, with John's approval on its timeline.
- ``SELECT SUM(debit)-SUM(credit) FROM `tabGL Entry` WHERE is_cancelled=0 AND account IN (SELECT name FROM tabAccount WHERE account_type='Temporary')`` → 0, or NULL.
- ``SELECT COUNT(*) FROM `tabPayment Entry` WHERE docstatus=1 AND payment_type='Receive' AND posting_date BETWEEN '2027-01-01' AND '2027-01-14' AND unallocated_amount>0`` = 0, or each row is a listed prepayment.
- `SELECT accounts_frozen_till_date FROM tabCompany WHERE name='Sapphire Fountains'` = '2026-12-31'.
- Sign-off document archived on TASK-2026-00928.

## Rollback
If the tie-out fails: correct per document (cancel and amend) or through the one residual JE, then re-run. A QuickBooks-side error loops back to a WI-003 re-close. The frozen date is reversible by an Accounts Manager.

## Explicitly NOT in this work item
Fixing QuickBooks-side errors (WI-003); the Disconnect itself (WI-045); posting the waves (TASK-2026-01236).
