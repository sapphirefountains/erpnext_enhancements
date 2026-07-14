# WI-016: Labor cost to projects via native Activity Type + Activity Cost (hour-costing, no salary data)
**Phase:** 0   **Type:** CONFIG   **Size:** M
**Blocked by:** burdened rates from payroll firm (precondition)   **Blocks:** WI-022; Phase-2 labor budgets (WI-057)

## Why
Timesheets only produce job profitability if hours carry a cost rate. Per hard rule 3, ERPNext holds NO payroll/salary detail — so the design question "does Timesheet need salary detail?" is answered: NO; a fully-burdened hourly costing_rate per employee is sufficient for project profitability, and actual pay stays at the payroll firm.

## Native-first check
Native **Activity Type** + **Activity Cost** (per employee + activity: `costing_rate`, `billing_rate`) feeding native `Timesheet Detail.costing_rate/costing_amount` and Project costing totals — SUFFICIENT. Native **Profitability Analysis** / **Gross Profit** reports (verified present — prod_finance_native) then consume it. Any custom labor-cost doctype would be a defect.

## Preconditions
- Payroll firm supplies a burdened hourly cost per employee (wage + employer taxes + workers comp load) — a number, not payroll detail.
- Activity taxonomy agreed (proposal: Installation, Service Visit, Design, Shop/Fabrication, Travel, Admin — final list with ops).
- Note: `time_category` on Job Interval links Activity Type (prod_customers_items), so kiosk categories and costing categories are the SAME list — one taxonomy.

## Scope
- Create Activity Type rows (Desk) for the agreed taxonomy.
- Create Activity Cost rows: one per (employee × activity) or per employee default — ~14 active employees × small set; entered on test, re-entered on prod at cutover (values change with pay reviews; deliberately NOT fixture-ized — it is business data, and rates are sensitive).
- Permissions: restrict Activity Cost read/write to Accounts Manager + HR roles via Role Permission Manager (rates are confidential).
- Sensitivity note: `Timesheet.total_costing_amount` exposes cost — restrict Timesheet costing visibility per role (Property Setter `permlevel` on costing fields is a WI-019 branch if needed).

## Acceptance criteria
- `SELECT COUNT(*) FROM `tabActivity Cost`` ≥ number of active field employees on test and prod.
- TEST: a kiosk-generated Timesheet, once submitted, shows `costing_amount` > 0 on each row and the linked Project's costing total increments.
- A non-Accounts test user cannot read Activity Cost (PermissionError).

## Rollback
Delete Activity Cost rows (Timesheets already submitted keep their stamped rates).

## Explicitly NOT in this work item
Billing rates for T&M invoicing (Phase 2); salary structures/Salary Slip (prohibited — rule 3); backfilled labor history.
