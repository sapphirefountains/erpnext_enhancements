# WI-005: Create Stripe Clearing + Merchant Fees accounts and point Stripe routing at them
**Phase:** 0   **Type:** CONFIG   **Size:** S
**Blocked by:** nothing (do on TEST first, promote to prod)   **Blocks:** WI-039, WI-040, WI-031

## Why
Today the webhook-created Payment Entry posts the GROSS charge to the single `Stripe Payments Settings.deposit_account` and Stripe's own processing fee is never journaled (repo_payments: grep confirmed no fee/payout/balance_transaction handling). If `deposit_account` points at a real bank account, the bank GL will never tie to the statement (deposits arrive net, in payout batches). Correct target accounting: PEs post gross to a dedicated 'Stripe Clearing' asset account; the payout JE (WI-040) sweeps net to bank, fees to expense, zeroing the clearing account per payout.

## Native-first check
Native Account doctype + Mode of Payment Account child table are exactly sufficient — this is pure chart/config. Evaluated and rejected: native `payments`-app Stripe gateway + Payment Gateway Account (both present and empty on prod — prod_finance_native) because the operating integration is the custom `stripe_payments` module, which already supports ACH, surcharge and autopay that the native gateway lacks (repo_payments); Payment Request is unused by design. Verdict: native accounts config feeding the custom module's settings.

## Preconditions
- CoA loaded on the target site (prod has 359 accounts under 'Sapphire Fountains' — prod_finance_native).
- Modes of Payment 'Stripe' and 'ACH' exist (prod_finance_native lists both among the 21; hooks `after_migrate` runs `create_stripe_modes_of_payment` so they are code-ensured — repo_payments).

## Scope
1. Create Account 'Stripe Clearing' — company 'Sapphire Fountains', root_type Asset, `account_type`='Bank' (required so Payment Entry accepts it as `paid_to`), placed under the company's current-assets bank group alongside '1110 - Cash - SF' (prod_finance_native; do NOT reuse the QBO-legacy 'QuickBooks Payments' accounts).
2. Create Account 'Merchant Fees' — root_type Expense, under the existing expense tree (operator picks the parent group with the controller; 177 expense accounts exist — prod_finance_native).
3. Set `Stripe Payments Settings.deposit_account` = 'Stripe Clearing - SF' (field verified: Link→Account, label 'Deposit / Clearing Account' — stripe_payments_settings.json line 142, repo_payments).
4. Add `Mode of Payment Account` child rows (company='Sapphire Fountains', `default_account`='Stripe Clearing - SF') for modes 'Stripe' (`card_mode_of_payment`) and 'ACH' (`ach_mode_of_payment`) — prod_finance_native confirms 19 of 21 modes currently lack default accounts.
5. Apply on TEST first (TEST already has Stripe enabled with card+ACH — test_vs_prod), validate a test-mode payment posts `paid_to`='Stripe Clearing - SF', then repeat on prod.

## Acceptance criteria
- `SELECT COUNT(*) FROM tabAccount WHERE account_name IN ('Stripe Clearing','Merchant Fees') AND company='Sapphire Fountains'` = 2; Stripe Clearing has `account_type`='Bank', root_type='Asset'; Merchant Fees root_type='Expense'.
- tabSingles `Stripe Payments Settings.deposit_account` = 'Stripe Clearing - SF'.
- `SELECT parent, default_account FROM \`tabMode of Payment Account\` WHERE parent IN ('Stripe','ACH')` returns both rows → 'Stripe Clearing - SF'.
- On TEST: newest Stripe-created Payment Entry has `paid_to`='Stripe Clearing - SF'.

## Rollback
Repoint `deposit_account` to the prior value; remove the two Mode of Payment Account rows; disable (don't delete) the two accounts if unused.

## Explicitly NOT in this work item
Live keys/enablement (WI-039); payout JE logic (WI-040); default accounts for the other 17 modes (WI-031); `surcharge_income_account` (WI-055 — surcharge stays OFF).
