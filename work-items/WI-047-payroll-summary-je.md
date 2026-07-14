# WI-047: Payroll summary Journal Entry return process (native JE Template + register mapping)
**Phase:** 1   **Type:** CONFIG   **Size:** S
**Blocked by:** WI-017, WI-029 (CoA account mapping sign-off — account names confirmation precondition)   **Blocks:** WI-051

## Why
After the payroll firm runs payroll, total labor cost must land in the ERPNext GL as ONE summary Journal Entry per pay run (gross wages + employer taxes to expense, net pay + liabilities to cash/liability accounts). Rule 3's third leg: the payroll firm computes everything; ERPNext only books the period summary so the P&L and balance sheet are complete. Without a templated, repeatable entry the accountant free-keys a 6-10 line JE every two weeks — slow and error-prone — and wage/tax accounts drift between periods.

## Native-first check
Native **Journal Entry Template** doctype — SUFFICIENT (pre-populates accounts; accountant fills amounts from the firm's register). Native **Auto Repeat** evaluated and REJECTED: JE amounts differ every run, and Auto Repeat clones amounts; a template with blank amounts is the correct native tool. Native Payroll module (Salary Slip etc.) evaluated: prohibited (rule 3). Precondition check: `SELECT COUNT(*) FROM tabDocType WHERE name='Journal Entry Template'` = 1 (native in v16 lineage; verify on test). Verdict: native JE, process-configured — zero build.

## Preconditions
- Accountant confirms the payroll GL accounts in the QBO-imported CoA (359 accounts — prod_finance_native; exact wages/payroll-liability account names NOT yet verified — enumerate them from `SELECT name FROM tabAccount WHERE account_name LIKE '%payroll%' OR account_name LIKE '%wage%'`).
- Controller maps the payroll register lines to CoA leaf accounts: gross wages expense(s) (by department/cost center if desired — 18 Cost Centers exist, 16 leaf — prod_finance_native), employer tax expense, withholding/tax liability accounts, net-pay credit to the operating bank GL (exact account names live in the rebuilt CoA per WI-029; the mapping is the deliverable, names not invented here).
- Opening Entry posted on prod (finance workstream — WI-032) so JEs post into a live ledger.
- Timing: first period booked = first 2027 payday (post-cutover).

## Scope
- One Journal Entry Template 'Payroll Summary - <frequency>' with the account rows (no amounts); created on test, re-created on prod at cutover (documented in runbook WI-051; Journal Entry Template is not fixture-mandated).
- A one-page mapping table (register line → account → Dr/Cr) stored in Process Documentation.
- Per-period SOP: receive register → New JE from template (or duplicate prior period's JE) → fill amounts from payroll register → attach register PDF → post dated payday; the bank debit then matches in WI-043 reconciliation. Route through WI-044's Payment Entry workflow only if paid from ERPNext (the JE itself posts expense/liability; the funding transfer follows bank rec).
- Optional dimension: cost_center per wage line if the controller wants departmental P&L (native Accounting Dimensions/Cost Center — no custom work).

## Acceptance criteria
- `SELECT COUNT(*) FROM `tabJournal Entry Template` WHERE name LIKE 'Payroll Summary%'` = 1 on prod at cutover.
- One pilot payroll JE posted on TEST during parallel run: docstatus=1, total_debit = register gross + employer taxes, lines exactly per the mapping table, balances (native validation; debits = credits by construction); Trial Balance (native report) reflects it.
- Native P&L for the period shows wages in the mapped expense accounts; liability accounts carry the withholding balance until remittance.
- `SELECT COUNT(*) FROM `tabSalary Slip`` = 0 and no Payroll Entry docs exist — permanent guard (rule 3).

## Rollback
Delete the template; cancel the JE (native); process reverts to free-form JE.

## Explicitly NOT in this work item
Per-employee GL detail (prohibited granularity per rule 3); any withholding/FICA/941/W-2/direct-deposit computation; importing per-employee pay detail; historical payroll backfill; labor-cost-to-project allocation via JE (project labor costing is Timesheet-based — WI-016; the payroll JE is company-level).
