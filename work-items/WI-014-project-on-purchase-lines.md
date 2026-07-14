# WI-014: Project on purchasing lines (PO + PI items) with the 'Internal' overhead pattern
**Phase:** 0   **Type:** FIXTURE   **Size:** S
**Blocked by:** nothing   **Blocks:** WI-022

## Why
Material AND subcontract cost must land on the job for profitability. `Purchase Order Item.project` is native but optional and buried; with 77% of suppliers being 'Staffing' labor vendors (892/1,156 — prod_customers_items), subcontract cost arriving via Purchase Invoices matters as much as materials. 13 'Internal' projects already exist as the overhead-booking pattern (prod_projects_opps: Internal 13), which makes a mandatory field workable — every line has a legitimate target.

## Native-first check
Native `Purchase Order Item.project` and `Purchase Invoice Item.project` fields + native **Property Setter** to surface/require them — SUFFICIENT. Native Accounting Dimension (verified present, 0 rows — prod_finance_native) evaluated as an alternative cost-tagging axis and REJECTED for this purpose: Project is already a first-class dimension on purchase lines and on GL; adding a parallel dimension would duplicate it.

## Preconditions
- Accountant consulted on the mandatory-vs-visible trade-off (her standing demand is minimal UI; one extra required field on lines is the cost).
- 'Internal' projects list confirmed current (rename/curate the 13 so pickers are obvious, e.g. 'Internal - Shop Overhead').

## Scope
- Property Setters (created on test, exported via `bench export-fixtures` into `erpnext_enhancements/fixtures/property_setter.json`): `Purchase Order Item.project` → `in_list_view=1`; `Purchase Invoice Item.project` → `in_list_view=1`.
- DECISION BRANCH (accountant call, enumerate both): Branch A (recommended): also `reqd=1` on `Purchase Order Item.project` only (POs are PM-authored; PIs stay non-mandatory so the accountant's rapid bill entry isn't blocked, with the WI-008-style empty-project saved filter as the catch net). Branch B: visibility only, no reqd, rely on filters + UAT metrics.

## Acceptance criteria
- Fixture file contains the Property Setter entries (`grep 'Purchase Order Item-project' fixtures/property_setter.json`).
- TEST: PO line grid shows Project column; if Branch A, saving a PO line without project raises a mandatory error; 'Internal - *' project selectable.
- `SELECT COUNT(*) FROM `tabPurchase Order Item` poi JOIN `tabPurchase Order` po ON poi.parent=po.name WHERE po.docstatus=1 AND po.transaction_date>='2027-01-01' AND (poi.project IS NULL OR poi.project='')` = 0 during UAT (Branch A) or trending to 0 (Branch B).

## Rollback
Remove the Property Setter fixture entries and redeploy; existing documents unaffected.

## Explicitly NOT in this work item
Budget rows per project (Phase 2, needs estimated_costing discipline); Cost Center restructuring; making PI lines mandatory (explicitly branch-gated).
