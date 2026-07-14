# WI-023: Disposition of the 638 draft QBO-Estimate Quotations
**Phase:** 1   **Type:** DATA   **Size:** M
**Blocked by:** WI-007   **Blocks:** WI-028, WI-051

## Why
All 638 Quotations on prod are docstatus=0 drafts imported from QBO Estimates (prod_customers_items). At cutover, sales must know which quotes are live commercial offers versus dead history; live ones must become submitted ERPNext Quotations that can drive Sales Orders, and dead ones must not pollute open-quote lists. This item is the SOLE owner of the 638-quotation triage and completes BEFORE WI-028's bulk deletion (review correction C8): the shared live-quote keep-list it produces defines exactly which drafts WI-028 may delete (everything this item marks historical) and which submitted quotes must survive.

## Native-first check
Native Quotation docstatus lifecycle and native `valid_till` field — SUFFICIENT. No custom archival doctype; drafts are inert (no GL impact) so "leave as draft with expired validity" is the native archival mechanism (interim state — the marked-historical drafts are subsequently deleted by WI-028).

## Preconditions
- WI-007 SOP agreed (what a live quote must contain: items, tax template, project/opportunity link).
- Sales team produces a keep-list: Quotations whose linked Opportunity is not in status Lost/Closed (Opportunity statuses verified: Closed Won 280, Lost 304, Closed 144, etc. — prod_projects_opps). This is the ONE shared live-quote keep-list, consumed by WI-028 (review correction C8).
- Baseline SQL: `SELECT COUNT(*) FROM tabQuotation WHERE docstatus=0` = 638.

## Scope
- Partition the 638 drafts: (a) carry-forward (live offers) — sales re-validates price/tax and submits each individually in the Desk; (b) historical — set `valid_till` to a past date via `frappe.db.set_value` bulk script (db.set_value bypasses doc_events, avoiding the wildcard `'*'` after_save → `utils.triton_sync.global_triton_sync` hook that fires on every full doc save — repo_app_inventory).
- Side-effect-storm control: NO bulk `doc.save()` loops; no Customer/Opportunity writes in this run (avoids the Customer after_insert Drive-folder hook and the Opportunity on_update closed-won prompt — repo_ops). Run in batches ≤200 with commit between batches.
- Population is exactly `tabQuotation WHERE docstatus=0` (638 rows).
- Hand-off to WI-028 (review correction C8): publish the keep-list (submitted carry-forward quotes) and the marked-historical set; WI-028's Quotation delete population = exactly the marked-historical drafts.

## Acceptance criteria
- `SELECT COUNT(*) FROM tabQuotation WHERE docstatus=1 AND valid_till >= '2027-01-01'` = size of the approved keep-list.
- `SELECT COUNT(*) FROM tabQuotation WHERE docstatus=0 AND (valid_till IS NULL OR valid_till >= '2026-12-31')` = 0 (every remaining draft is visibly expired). NOTE (review correction C8): this "every remaining draft visibly expired" state is interim only — it is superseded by the subsequent WI-028 deletion of the marked-historical drafts; after WI-028 runs, the enduring check is docstatus=0 count = 0 with the submitted keep-list intact.
- Zero new rows in `tabError Log` referencing triton_sync during the run window.

## Rollback
`valid_till` back-dates are reversible via the same db.set_value script from a pre-run CSV snapshot of (name, valid_till). Submitted carry-forward quotes can be individually cancelled. (Rollback window closes once WI-028 deletes the marked-historical drafts.)

## Explicitly NOT in this work item
Deleting any Quotation (WI-028 owns the deletion, using this item's partition); touching the 1,563 draft Sales Invoices (finance/opening-balance workstream); Opportunity remediation.
