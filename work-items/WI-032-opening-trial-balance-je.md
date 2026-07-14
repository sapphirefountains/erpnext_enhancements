# WI-032: Opening Trial Balance Journal Entry at 2026-12-31 (balance-sheet accounts only)
**Phase:** 1   **Type:** DATA   **Size:** M
**Blocked by:** WI-003, WI-029, WI-030   **Blocks:** WI-033, WI-035

## Why
Replicates the validated test pilot (test has exactly one submitted Journal Entry, voucher_type='Opening Entry', total_debit $909,722.12, posting_date 2025-12-31, producing 157 GL entries — test_vs_prod) on prod, re-dated to 2026-12-31. This entry seeds every balance-sheet account — bank accounts, Undeposited Funds, the Stripe Clearing balance (card payments in transit at midnight), fixed assets, liabilities, equity — EXCEPT AR/AP, which load as individual open invoices in WI-033 so aging and payment matching work day one.

## Native-first check
Native Journal Entry with voucher_type='Opening Entry' / `is_opening='Yes'` is THE ERPNext opening mechanism. The app additionally ships a purpose-built loader — `erpnext_enhancements/quickbooks_online/core/opening_balances.py::sync_opening_balances(as_of_date, auto_submit=0)` (verified: pulls the QBO Trial Balance via `reconcile._fetch_trial_balance`, maps accounts through QuickBooks Sync Mapping, skips Stock accounts, plugs residue to the account_type='Temporary' account, creates the JE as a DRAFT, logs a 'Opening Balances' QuickBooks Sync Log) — reuse it rather than hand-keying ~150 lines. Verdict: native doc + existing app tool; no new code.

## Preconditions
- WI-003 December close complete (frozen QBO TB exists).
- WI-029 step 4 done (Sync Mapping re-pointed to the new chart — otherwise the tool reports every account 'unmapped').
- `QuickBooks Online Settings.realm_id` set, i.e. QBO reconnected by the sync workstream (WI-002) (currently NULL — prod_qbo_state; the tool throws 'Connect QuickBooks Online before importing opening balances' without it). **Fallback if reconnect never lands:** key the same JE via Data Import from the QBO TB CSV exported in WI-003 — identical acceptance criteria, +1–2 days.
- `Stripe Payments Settings` not yet enabled on prod (hazard H4 — Stripe `auto_charge_on_invoice_submit` on Sales Invoice on_submit — irrelevant for a JE, but keeps the cutover window clean).

## Scope
- Run `sync_opening_balances(as_of_date='2026-12-31', auto_submit=0)`.
- On the resulting DRAFT JE: delete every row with `party_type` set (the tool appends per-Customer/per-Vendor party lines because Company `default_receivable_account`/`default_payable_account` are set — verified in `_append_party_lines`; AR/AP must NOT load here because WI-033 loads them as invoices), then re-square the entry by adjusting the account_type='Temporary' row so total debit = total credit. The Temporary balance left standing equals (AR total − AP total) and is extinguished by WI-033.
- Resolve the tool's returned `unmapped` and `skipped_stock` lists to zero/accepted before submit (skipped_stock expected empty — no Stock-type accounts carry value; Bank/Bank Account tables are empty on prod, prod_finance_native, so bank balances arrive purely as JE rows here).
- Review vs the WI-003 close package; submit.

## Acceptance criteria
- `SELECT COUNT(*) FROM \`tabJournal Entry\` WHERE voucher_type='Opening Entry' AND docstatus=1 AND posting_date='2026-12-31'` = 1, and its total_debit equals the QBO TB total to the cent.
- `SELECT COUNT(*) FROM \`tabJournal Entry Account\` WHERE parent=<that JE> AND IFNULL(party_type,'')<>''` = 0.
- Native Trial Balance report as of 2026-12-31 shows every balance-sheet account (excl. AR/AP) equal to the QBO close package.
- The 'Opening Balances' QuickBooks Sync Log row (sync_type='Opening Balances') has status='Completed' (tool path only).

## Rollback
Cancel the Journal Entry (docstatus 2) — GL reverses natively; re-run after correction.

## Explicitly NOT in this work item
AR/AP (WI-033); open POs (WI-034); an `include_parties=0` parameter for opening_balances.py (a possible 10-line APP_CODE nicety — deliberately excluded; the draft-edit procedure suffices); stock opening (no stock value to migrate).
