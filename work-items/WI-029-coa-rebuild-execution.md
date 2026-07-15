# WI-029: Execute the Chart of Accounts rebuild on prod
**Phase:** 1   **Type:** DATA   **Size:** L
**Blocked by:** WI-004, WI-028 (OD-1 RESOLVED 2026-07-14: "No" — single-company scope; skip the JDH import step)   **Blocks:** WI-030, WI-031, WI-032, WI-034, WI-036

## Why
The new numbered chart must exist on prod before any opening balance posts. Prod currently has 359 QBO-imported accounts and 4 stray GL Entries (posting_date 2025-11-04..2025-12-12 — prod_finance_native) that must be cleared for a clean import.

## Native-first check
Native **Chart of Accounts Importer**. Constraint honored: the importer requires a company with no ledger postings, hence the GL-clearing prep below. Fallback if link-checks defeat full deletion: native Account **rename/merge** (Account tree supports rename with merge) applied per the coa_mapping.csv instead of delete+import — same end state, more steps. Verdict: native sufficient; no custom import code.

## Preconditions
- WI-028 complete (drafts gone — they are the largest source of Account references).
- Identify and cancel/delete the source vouchers of the 4 GL Entries (`SELECT voucher_type, voucher_no FROM \`tabGL Entry\``), then the legacy submitted purchasing docs that reference old accounts: 65 submitted + 30 draft Purchase Orders, 1 draft Purchase Invoice, 7 Purchase Receipts, 17 Material Requests (counts per prod_customers_items). Genuinely-open POs are captured first in the WI-034 re-key list.
- Dry run of the full sequence completed on TEST (test's 281-account chart makes it the rehearsal site — test_vs_prod).

## Scope
1. Delete all 359 `tabAccount` rows for company 'Sapphire Fountains' (tree-safe order, leaves first). If any Account survives link-checks, switch that account to the rename/merge fallback per coa_mapping.csv.
2. Import `docs/migration/` CSV via Chart of Accounts Importer.
3. Re-point Company defaults to new accounts: `default_receivable_account`, `default_payable_account`, `default_bank_account`, `default_cash_account` (repo_qbo_sync), plus `Company.chart_of_accounts` display value.
3b. Re-point `Stripe Payments Settings.deposit_account` (and, once WI-040 adds them, `fee_expense_account`/`payout_bank_account`) to the rebuilt numbered accounts per coa_mapping.csv (review correction C11). Single Link fields are not covered reliably by Frappe link-checks — the setting would dangle silently — so this carries its own settings-value acceptance check below.
4. Update the QBO account mapping ledger so the opening tools resolve to the NEW chart: `UPDATE \`tabQuickBooks Sync Mapping\` SET erpnext_name=<new account> WHERE erpnext_doctype='Account'` driven by coa_mapping.csv (fields `qbo_entity_type IN ('Account','TaxCode')`, `erpnext_name`, `deleted` verified in opening_balances.py `_account_index`). RETIRE-mapped rows get `deleted=1`.
5. Review the 18 Cost Centers (prod_finance_native) — no rebuild, but confirm none references a deleted account.
6. OD-1 branch (a) only: create the JDH Company and run the same CSV import for it.

## Acceptance criteria
- `SELECT COUNT(*) FROM tabAccount WHERE company='Sapphire Fountains' AND account_name LIKE '%(deleted)%'` = 0.
- `SELECT COUNT(*) FROM tabAccount WHERE company='Sapphire Fountains' AND is_group=0 AND (account_number IS NULL OR account_number='')` = 0.
- Total account count equals the designed chart row count exactly.
- `SELECT COUNT(*) FROM \`tabGL Entry\`` = 0.
- Exactly 1 account with account_type='Temporary'; Trial Balance report renders without error for FY2026.
- All four Company default account fields non-empty and pointing at accounts in the new chart.
- `Stripe Payments Settings.deposit_account` resolves to an existing Account in the new chart (settings-value check, review correction C11 — Frappe link-checks do not reliably cover Single Link fields).

## Rollback
Restore pre-run backup (this item runs inside the same freeze window as WI-028); or, before any opening entry posts, delete the imported chart and re-import a corrected CSV (repeatable while GL Entry count = 0).

## Explicitly NOT in this work item
Tax templates (WI-036), Mode of Payment accounts (WI-031), initial Stripe Payments Settings configuration (WI-005 / payments workstream — only the step-3b re-point of existing account fields to the new chart is in scope here), any posting.
