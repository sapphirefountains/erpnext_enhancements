# WI-008: Sales Invoice → Project association: adopt the native chain, define the ad-hoc-invoice rule
**Phase:** 0   **Type:** CONFIG   **Size:** S
**Blocked by:** WI-007   **Blocks:** WI-022

## Why
NO code links Sales Invoices to Projects today (repo_ops: grep confirms zero SI auto-association in the handoff), yet job profitability requires every revenue dollar tagged to a project. The decision "native chain vs small APP_CODE" must be made explicitly.

## Native-first check
Native `Sales Invoice.project` header field (already populated on 1,280 of 1,563 imported drafts — prod_customers_items) and native SO→SI mapping which carries `project` — SUFFICIENT for the two main paths: (1) SO-driven invoices inherit project; (2) maintenance invoices get project set by the custom module (`api/maintenance_workflow.py:408`). VERDICT: no new APP_CODE day one. The only gap is a hand-keyed ad-hoc SI where the user forgets project — closed by process + a list-view check, not code.

## Preconditions
- WI-007 flow configured on test.
- Confirmed: native `Profitability Analysis` and `Gross Profit` reports present and enabled in this build (prod_finance_native) — these consume SI.project, so no custom profitability report is needed.

## Scope
- Property Setter (delivered inside WI-019's fixture batch): `Sales Invoice.project` → `in_list_view=1` and moved into the top section, so a missing project is visible at a glance.
- A saved List View filter "Invoices without Project" (Sales Invoice list, filter project = empty, docstatus != 2) documented in the accountant SOP.
- Written rule: ad-hoc SIs for job work must select the Project; true non-job revenue may use the 13 'Internal' projects pattern or stay blank (decision recorded).
- Phase-2 branch documented (NOT built): a small APP_CODE validation "warn/block SI submit when customer has ≥1 Active project and project is empty" — only if UAT shows persistent misses.

## Acceptance criteria
- During UAT (WI-022): `SELECT COUNT(*) FROM `tabSales Invoice` WHERE docstatus=1 AND posting_date >= '2027-01-01' AND (project IS NULL OR project='')` reviewed weekly and = 0 for job-type invoices in the parallel-run sample.
- Native Profitability Analysis report, filtered by Project, returns revenue rows for the UAT test project.

## Rollback
Remove the saved filter and the SOP paragraph; Property Setter rollback is WI-019's.

## Explicitly NOT in this work item
Any server-side validation code (Phase 2 branch only); backfilling project on the 283 legacy draft SIs without project (finance workstream decides with opening balances — WI-033).
