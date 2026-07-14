# WI-017: Payroll-firm hours export — report fixture + integration contract
**Phase:** 0   **Type:** FIXTURE   **Size:** M
**Blocked by:** WI-021 (real data to validate; the Time Kiosk → Timesheet pipeline live)   **Blocks:** WI-047

## Why
Per hard rule 3 the payroll pattern is: capture hours in ERPNext → export to the external payroll firm → summary JE back (WI-047). The export artifact is the middle link: per-employee hours for the pay period, in a shape the firm can import. Nothing exists today (repo_ops: no payroll/hours export code; verified again by grep — no payroll/hours-export code in the app). Hours already land in native Timesheets via `api/time_kiosk.py::sync_interval_to_timesheet` (Stop/Switch actions append Timesheet Detail rows — repo_ops). Both sides (firm and ERPNext) need one unambiguous contract or every pay period becomes a negotiation.

## Native-first check
Native stock **Timesheet reports** in this build evaluated FIRST: run `SELECT name, report_type FROM tabReport WHERE ref_doctype='Timesheet' AND disabled=0` on test (the 16 verified reports in prod_finance_native are finance reports; the projects-module Timesheet reports such as 'Daily Timesheet Summary' must be checked live). Branch A: a stock report's columns satisfy the firm's template → pure CONFIG, close this item with the SOP. Branch B (expected): the firm needs employee-period totals with regular/OT split by day — ship ONE **Query Report** 'Payroll Hours Export' (ref_doctype Timesheet: employee, employee_name, date, SUM(hours) grouped, submitted Timesheets only) as a FIXTURE. This is a new export shape, not a reimplementation of any stock report (rule 1 compliant). Native HR/Payroll (Salary Slip etc.) evaluated: prohibited by rule 3, and no payroll code exists in the app. Verdict: a FIXTURE query report over native Timesheet data — nothing more.

## Preconditions
- Payroll firm's import template obtained (columns, file format, OT rules — OT calculation stays THEIR job; we export raw daily hours).
- Pay-period calendar confirmed (weekly/biweekly vs semi-monthly — OPEN business input, precondition not decided here).
- The Time workstream's Timesheet flow produces submittable Timesheets (prod Timesheet count today: 2 drafts — prod_customers_items — so validation is contingent on WI-021).

## Scope
- Branch B artifacts: Report doctype record 'Payroll Hours Export' (report_type 'Query Report', ref_doctype 'Timesheet'); hooks.py fixtures list gains `{"dt": "Report", "filters": [["name", "in", ["Payroll Hours Export"]]]}` (new fixture doctype for this app — follows the existing name-in allowlist convention, repo_app_inventory); `bench export-fixtures` → commit.
- The integration CONTRACT (documented in the app's Process Documentation alongside the report definition — one build, not two):
  - Period boundary: [pay-period start 00:00, end 23:59] site timezone (America/Denver — repo_app_inventory cron note).
  - Approval cutoff: all Timesheets for the period docstatus=1 by <day+time> after period end; the export refuses (or flags) periods containing draft Timesheets.
  - Export columns (from Timesheet parent + `time_logs` child rows — repo_ops): employee (Employee ID + employee_name), project, day (date of the detail row), hours, activity (activity_type). One row per employee/project/day/activity.
  - Delivery: operator downloads CSV from the report and uploads to the firm's portal (manual; no API integration in scope).
- SOP: Friday of period end — supervisors submit Timesheets, accountant runs report filtered to the period, exports CSV, uploads to payroll firm.

## Acceptance criteria
- Report exists and is code-managed: repo `fixtures/report.json` contains 'Payroll Hours Export'; `SELECT COUNT(*) FROM tabReport WHERE name='Payroll Hours Export'` = 1 on both sites post-deploy (Branch B); applied on migrate; returns exactly the five agreed columns.
- Report total for a seeded TEST period equals `SELECT SUM(td.hours) FROM `tabTimesheet Detail` td JOIN tabTimesheet t ON td.parent=t.name WHERE t.docstatus=1 AND td.from_time BETWEEN <period>` exactly (SQL cross-check); draft Timesheets in-period cause a visible flag/refusal.
- One real pilot export accepted by the payroll firm during the December parallel run (sign-off artifact: firm confirms the sample file imports cleanly).

## Rollback
Remove the Report fixture entry + JSON, redeploy; fall back to manual list-view export of Timesheet Details. Superseded contract versions replaced via fixture deploy.

## Explicitly NOT in this work item
Any withholding/FICA/941/W-2/direct-deposit computation or pay/wage-rate/overtime math (prohibited — rule 3); auto-transmission to the firm (manual upload day one; automated SFTP/API delivery is a Phase-2 candidate at most); OT policy logic; building the Timesheet pipeline itself (WI-021).
