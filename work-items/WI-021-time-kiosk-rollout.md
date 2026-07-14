# WI-021: Time Kiosk pilot & rollout (zero usage today → field standard)
**Phase:** 1   **Type:** CONFIG   **Size:** L
**Blocked by:** WI-011 (Employee.user_id links), WI-016 (Activity Types define the kiosk taxonomy)   **Blocks:** WI-017, WI-046, WI-022; Phase-2 %-escalation chain (WI-057/WI-058)

## Why
The Time Kiosk is built end-to-end — Job Interval doctype with `project` reqd, PWA at /kiosk, `api/time_kiosk.py::log_time` (Start/Pause/Resume/Switch/Stop), `sync_interval_to_timesheet` creating draft Timesheets on Stop/Switch — yet `tabJob Interval` has ZERO rows ever (prod_customers_items; repo_ops §1). Hours are the company's largest cost and currently live in QuickBooks Time. Without kiosk adoption there are no project-tagged Timesheets, no labor actuals, no labor budgets, and therefore no Phase-2 percentage escalation — this item is the root of that §8 dependency chain, and every link must be stated: kiosk adoption → Timesheets with project → labor actuals on jobs → credible labor budgets → percentage-of-budget PO rule.

## Native-first check
Native **Timesheet** is the system of record — the kiosk already feeds it (sync_interval_to_timesheet), so the custom capture layer + native Timesheet is the design; no new code. Native HR attendance/check-in evaluated: HRMS app is NOT installed (installed apps list — prod_finance_native), so ERPNext-native Timesheet via the existing kiosk is the only in-scope path. The standalone `sync_time_kiosk.py` REST script (repo root, unwired — repo_ops §1) is NOT adopted; the wired in-app sync path is sufficient.

## Preconditions
- Every field employee has Employee.user_id set + can log in on a mobile device (WI-011).
- Activity Types exist for the field taxonomy (WI-016 defines them; kiosk `time_category` links Activity Type — prod_customers_items Job Interval schema).
- GPS policy decided (privacy conversation): `Time Kiosk Settings.enable_tracking` on/off.

## Scope
- Configure `Time Kiosk Settings` (Single, module Workforce; verified fieldnames): `enable_tracking`, `keep_wake_lock`, `distance_filter_m`, `heartbeat_seconds`, `high_accuracy`, `min_accuracy_m`, `max_batch_size`, `retention_days`.
- December pilot on TEST then prod: 3-5 field employees week 1, all field staff by week 3; daily standup check of open/orphaned intervals.
- SOP: Start on arrival, Switch between projects, Stop at end; supervisors review draft Timesheets weekly and submit them (submitted Timesheets are the payroll export source — WI-017).
- Adoption metric owned by ops lead.

## Acceptance criteria
- By end of parallel run: `SELECT COUNT(DISTINCT employee) FROM `tabJob Interval` WHERE DATE(start_time) >= '2026-12-07'` ≥ 80% of active field employees.
- `SELECT COUNT(*) FROM `tabJob Interval` WHERE sync_status='Failed'` = 0 (or triaged).
- Every kiosk-fed Timesheet row carries project: `SELECT COUNT(*) FROM `tabTimesheet Detail` WHERE creation>='2026-12-01' AND (project IS NULL OR project='')` = 0.

## Rollback
Stop using the PWA; QuickBooks Time remains alive until WI-046 executes (sequenced AFTER adoption is proven), so reverting = staying on QB Time for another cycle.

## Explicitly NOT in this work item
Payroll export (WI-017); costing rates (WI-016); decommissioning QB Time (WI-046); geofencing/enforcement features.
