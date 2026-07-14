# WI-054: Revenue-by-segment (Commercial/Residential) reporting (OD-4 resolved: branch a)
**Phase:** 2   **Type:** CONFIG   **Size:** M
**Blocked by:** WI-026, WI-027   **Blocks:** nothing

## Why
Leadership wants revenue split Commercial vs Residential. Reality check (prod_customers_items): `Customer.customer_type` ALREADY holds Commercial=1040 / Residential=172 (plus Company 365, Individual 13, Partnership 12), while the brief says segment was decided as a *project* attribute and the Products stream needs *customer-level* segment — so any report must union two sources (OD-4's open placement question).

## Native-first check
Evaluated three natives: (1) **Sales Analytics** (present, enabled) groups by Customer Group — covers the customer-level branch outright once WI-026 lands; (2) **Profitability Analysis** (present) covers project/cost-center views; (3) **Accounting Dimensions** (doctype present, 0 rows) — a 'Segment' dimension stamped on invoices makes the native P&L/financial statements filterable by segment, which is the only native way to get ONE unified number across both branches. Verdict: **native combination sufficient**: recommend creating one Accounting Dimension for segment; project-linked invoices inherit segment from the project attribute, Products-stream invoices from the customer — captured at entry (dimension field on the invoice) rather than by a custom union report. A custom report is a defect unless the dimension approach is rejected by OD-4's resolution; the sole APP_CODE fallback (a small query report unioning customer_type and the project attribute) is enumerated but NOT proposed for build.

## Preconditions
- **OD-4 RESOLVED (2026-07-14): branch (a)** — segment is a project attribute with customer fallback for Products, and the Accounting-Dimension approach is confirmed. WI-026 groups populated. **This item's scope now includes shipping the Project segment Custom Field as a FIXTURE** (Select: Commercial/Residential; the dimension sourcing rule reads it first, falling back to the customer segment for Products-stream invoices).

## Scope
- Create Accounting Dimension 'Segment' (Desk config) referencing the ratified master; enable on Sales Invoice/Journal Entry.
- Document the sourcing rule (project attr first, customer fallback) in the invoicing SOP; add dimension-completeness to the month-end checklist.
- Saved views: P&L filtered by each segment value; Sales Analytics by Customer Group for the customer-level cut.

## Acceptance criteria
- `SELECT COUNT(*) FROM \`tabAccounting Dimension\`` = 1 with the ratified name.
- For a test month, sum of segment-filtered P&L income across all segment values equals unfiltered P&L income (completeness check).
- Saved report views exist and are linked in the Finance workspace.

## Rollback
Disable the Accounting Dimension (dimension columns are additive; no GL impact).

## Explicitly NOT in this work item
Backfilling segment onto opening/historical documents; building the union query report (fallback only, requires a new decision); customer_type field cleanup.
