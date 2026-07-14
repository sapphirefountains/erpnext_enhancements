# WI-028: Draft-mirror quarantine — bulk delete the QBO draft transaction mirror
**Phase:** 1   **Type:** DATA   **Size:** M
**Blocked by:** OD-6, WI-023, final QBO sync completed + sync retirement initiated (webhook subscription removed — WI-045's early step), full site backup   **Blocks:** WI-029

## Why
Prod contains 12,341 draft Journal Entries (sum total_debit $13,339,246.15), 1,563 draft Sales Invoices (~$9,175,219 grand_total), 1,405 draft Payment Entries, and 638 draft Quotations — 100% docstatus=0, imported from QBO (test_vs_prod, prod_customers_items). They carry zero GL value but three live risks: (1) accidental submit posts phantom history into the new ledger; (2) they hard-block the CoA rebuild — Frappe's link check refuses to delete an Account referenced by any document, draft or not; (3) list-view noise and naming-series pollution. **Recommendation: bulk delete**, because reference value is preserved three ways without them — QBO itself stays accessible read-only after cutover, every imported record's JSON is archived in `QuickBooks Raw Payload` (repo_qbo_sync), and a pre-delete CSV export is taken. Alternatives rejected: *mark+filter* leaves the accidental-submit risk and still blocks Account deletion; *a separate 'historical' flag doctype* costs APP_CODE for data with no GL meaning.

## Native-first check
Native bulk delete (`frappe.delete_doc` batched / Desk bulk actions) — no custom tooling. Native docstatus semantics already keep these out of the GL (only 4 GL Entries exist). Verdict: native deletion sufficient; nothing to build.

## Preconditions
- **OD-6 ratified by the business** (bulk deletion of 15,947 documents is destructive; the recommendation is recorded in OD-6 but the business signs it). (review correction C4)
- The QBO sync's final master-data reconciliation run is done and the sync workstream has set `QuickBooks Online Settings.sync_enabled=0` AND removed the Intuit webhook subscription (hazard H5: `webhooks.handle_webhook` ignores sync_enabled — repo_qbo_sync — so a live webhook could re-import deleted transactions).
- **Cutover-window ordering is binding (review correction C5):** final CDC/Import → `sync_enabled=0` + Intuit webhook subscription DELETED (partial kill; OAuth tokens stay ALIVE for the opening tools) → this item's delete → WI-029 CoA rebuild → WI-030/WI-031 → WI-032 opening TB (uses the live QBO API) → WI-033/WI-034 → WI-035 → THEN WI-045 full Disconnect. The webhook-deletion step of WI-045 executes EARLY as this item's precondition; the Disconnect step executes LAST.
- Frappe Cloud full backup taken and download verified.
- Pre-delete archival export: Data Export (CSV) of all four doctypes' drafts, plus the Sales Invoice project-linkage extract (1,280 of 1,563 drafts carry `project` — prod_customers_items) archived for historical project-revenue reference.
- WI-023 complete (review correction C8): WI-023 is the SOLE owner of the 638-quotation triage and finishes before this item; it produces the ONE shared live-quote keep-list. This item's Quotation delete population = exactly the drafts WI-023 marked historical. Provenance test remains available: `QuickBooks Sync Mapping` rows with `qbo_entity_type='Estimate'` identify QBO-sourced quotations (repo_qbo_sync).

## Scope
Populations (SQL-checkable, all on prod):
- `SELECT COUNT(*) FROM \`tabJournal Entry\` WHERE docstatus=0` → 12,341 (all voucher_type='Journal Entry')
- `SELECT COUNT(*) FROM \`tabSales Invoice\` WHERE docstatus=0` → 1,563
- `SELECT COUNT(*) FROM \`tabPayment Entry\` WHERE docstatus=0` → 1,405
- `SELECT COUNT(*) FROM tabQuotation WHERE docstatus=0` → 638 (delete population = exactly the drafts WI-023 marked historical, per the shared keep-list — review correction C8)
Execution: batched `frappe.delete_doc(..., ignore_permissions=True)` on the `long` queue, commit every 100 (mirrors the sync's own QBO_COMMIT_EVERY=100 pattern). Hook exposure is low by inspection (repo_app_inventory hooks digest): none of the four doctypes has an on_trash hook, and the wildcard Triton hook is `after_save` (not fired on delete). Keep `QuickBooks Sync Mapping` rows but set `deleted=1` on mappings whose `erpnext_name` was removed (field verified in opening_balances.py's filter `deleted: 0`), so any accidental future resync cannot silently relink. `QuickBooks Raw Payload` rows are retained untouched (they are the archive).

## Acceptance criteria
- The four counts above return 0 (Quotation: docstatus=0 count = 0 AND the WI-023 submitted keep-list intact — review correction C8).
- `SELECT COUNT(*) FROM \`tabGL Entry\`` unchanged by this item (still 4 — drafts never touched GL).
- `SELECT COUNT(*) FROM \`tabQuickBooks Raw Payload\`` unchanged.
- Archival CSVs exist in the agreed storage location.

## Rollback
Restore the pre-delete Frappe Cloud backup (site-wide, so this item runs in a window with no other writes), or re-import individual docs from the archival CSVs.

## Explicitly NOT in this work item
Deleting the 65 submitted Purchase Orders / 6 submitted Purchase Receipts / 9 submitted Material Requests (handled inside WI-029 prep); deleting QuickBooks Sync Log/Mapping/Raw Payload doctypes; the sync Disconnect itself (WI-045); the quotation triage itself (WI-023 owns it); any deletion on test.
