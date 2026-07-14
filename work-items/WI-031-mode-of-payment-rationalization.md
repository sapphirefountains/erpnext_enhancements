# WI-031: Mode of Payment rationalization, default accounts, and the ACH/check run process
**Phase:** 1   **Type:** CONFIG   **Size:** M
**Blocked by:** WI-029, WI-042   **Blocks:** WI-035 (payment plumbing must exist before live receipts)

## Why
Prod has 21 Modes of Payment, ALL enabled, including QBO-era artifacts ('QuickBooks Payments-Bank', 'QuickBooks Payments-Credit Card', 'Gregg K', 'square', 'PayStation') — and only 2 ('Cash', 'Rewards Account') have a company default account, both pointing at the soon-to-be-replaced '1110 - Cash - SF'; the other 19 lack them, which blocks Payment Entry account-defaulting (prod_finance_native, verbatim finding). Junk modes ('Gregg K', 'square', 'PayStation', 'QuickBooks Payments-Bank', 'QuickBooks Payments-Credit Card', duplicate gift-card entries) invite mis-keying. Vendor payments (ACH/check runs) need clean modes with correct default accounts, and staff need a defined way to record a run.

## Native-first check
Mode of Payment + Mode of Payment Account child (native) for defaults (`Mode of Payment Account.company`, `.default_account` verified); Payment Entry (native) for recording; Accounts Payable report (native, present — prod_finance_native) drives the run selection. Note the Stripe/ACH modes are code-ensured by `after_migrate create_stripe_modes_of_payment` (repo_payments) so those two are already code-managed; the rest are ordinary master data — CONFIG is correct, not FIXTURE. Verdict: native, config only.

## Preconditions
- New chart live (WI-029) with Undeposited Funds / Stripe Clearing / bank accounts — the default-account targets do not exist until the CoA rebuild completes.
- Bank masters and the operating-bank GL mapping from WI-042 available.
- Controller sign-off on the keep-list. Proposed keep: Cash, Check, ACH, Wire Transfer, Credit Card, Stripe. Proposed disable (enabled=0, never delete — QBO-imported PEs may reference them): American Express, Bank Draft, Debit Card, Discover, E-Check, Gift Card, Gift Certificate/Card, Gregg K, MasterCard, PayStation, QuickBooks Payments-Bank, QuickBooks Payments-Credit Card, Rewards Account, square, Visa (all names verified — prod_finance_native; card-brand modes collapse into 'Credit Card'/'Stripe').
- Nuance to resolve with the controller: 'ACH' is dual-use — it is Stripe's `ach_mode_of_payment` (customer receipts → default 'Stripe Clearing - SF' per WI-005) AND the natural label for outbound vendor ACH (wants the operating bank). A Mode of Payment has one default per company; branch A (recommended): keep 'ACH' for the Stripe rail, add a new 'Vendor ACH' mode defaulting to the operating bank GL; branch B: keep one 'ACH' and train AP to override `paid_from` per entry.

## Scope
- Set `enabled`=0 on the disable-list modes.
- Add `Mode of Payment Account` rows (company 'Sapphire Fountains') for each kept mode: Check/Wire Transfer/Vendor ACH → operating bank GL (from the WI-042 mapping); Cash → per the rebuilt chart (the WI-004 CoA design routes Check/Cash → Undeposited Funds once the rebuilt chart is live — controller confirms the final per-mode target against the WI-004 mapping workbook; pre-rebuild, Cash pointed at '1110 - Cash - SF'); Stripe/ACH → 'Stripe Clearing - SF' per WI-005.
- **Do NOT rename or disable 'Stripe' and 'ACH'**: `Stripe Payments Settings.card_mode_of_payment='Stripe'` and `ach_mode_of_payment='ACH'` reference them (prod_finance_native), and `after_migrate` `create_stripe_modes_of_payment` re-seeds them (repo_payments) — renames would fork duplicates on next deploy.
- AP run process (runbook): weekly, from the native Accounts Payable report select due Purchase Invoices → Payment Entry type 'Pay', mode Check or Vendor ACH, `reference_no` = check number or ACH trace id, `reference_date` = issue date; PEs route through the WI-044 approval workflow (payment_type='Pay' scope).

## Acceptance criteria
- SQL: every `tabMode of Payment` row with enabled=1 has a matching `tabMode of Payment Account` row for company='Sapphire Fountains' with non-null default_account (count parity): `SELECT COUNT(*) FROM \`tabMode of Payment\` mp WHERE mp.enabled=1 AND NOT EXISTS (SELECT 1 FROM \`tabMode of Payment Account\` a WHERE a.parent=mp.name AND IFNULL(a.default_account,'')<>'')` = 0.
- `SELECT COUNT(*) FROM \`tabMode of Payment\` WHERE enabled=1` equals the signed-off keep-list size; disabled-mode count matches the ratified list.
- 'Stripe' and 'ACH' remain enabled with default_account = 'Stripe Clearing - SF'.
- A test vendor PE in each kept mode prefills the correct paid_from/paid_to.

## Rollback
Re-enable modes / remove added child rows; nothing destructive occurs (no transactional impact while unused).

## Explicitly NOT in this work item
Positive pay / bank file (NACHA) generation — out of scope, payments are initiated at the bank portal and recorded in ERPNext; check Print Format design (none ships for payment docs — repo_ops — raise with the Documents workstream (WI-020) if check printing is wanted); Stripe Payments Settings configuration and payout/fee accounting design (WI-039/WI-040); POS Profile (unused, 0 rows).
